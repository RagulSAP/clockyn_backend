"""Pure payroll engine: the per-employee month computation as functions of plain dicts / lists (no cursor, no
request, no clock): `compute_salary(inp)` is the single place where a salary figure is calculated; the database
layer (`salary_engine.py`) only loads `inp` and the HTTP layer only shapes the output.

What it computes (legacy arithmetic and rounding kept, see tests/test_payroll_snapshot.py for the pinned numbers):
  * attendance counters of the month from `attendance_rules` (day types: present / late / half day / leave /
    holiday / weekly off / absent), distinct punched dates = present days (a two-session day counts once),
    worked hours = hours of CLOSED sessions;
  * earnings: basic, HRA, daily wage (rate x present days), hourly wage (rate x hours), incentives (COUNT flat amount,
    TURNOVER basic x percent, BONUS basic x percent in its month), overtime (`overtime.overtime_for_month`, the
    single shared helper), odometer allowance (distance x stamped rate), approved travel / bill expenses, custom lines;
  * deductions: EPF, professional tax, health insurance, advance, custom lines and `fine`;
  * `fine` (decision D-8): drawn from the organisation's attendance rule only (late count threshold and 'Half Day'
    vs 'Leave'): `penalty_days = floor(late_days / count) x (0.5 | 1.0)`, `fine = penalty_days x basic / working_days`
    for staff on a payslip. NO proration of basic for absences; leave only changes the day type (D-7).

Input dict (`compute_salary`), every key optional unless marked:
  user_id*, year*, month*, now* (aware datetime), today* (IST date),
  payslip {basic_pay, hra, epf, professional_tax, health_insurance} | None,
  custom_earnings / custom_deductions [{name, amount}],
  sessions_by_date {date: [{punch_in, punch_out, work_hours, auto_closed}]},
  shift {start_time, end_time} | None, grace_minutes, leave_dates [date], holidays {date: name},
  off_weekdays [int] (Monday = 0), rule {late_punch_in_count, consider_as} | None,
  daily_rate, hourly_rate (float | None: None = no such wage assigned),
  incentives [{type: COUNT|TURNOVER|BONUS|OVERTIME, incentive_id, details: [row]}] (rows carry `user_id` or None),
  odometer [{distance_km, allowance_rate}], org_allowance_rate, default_odometer_rate,
  travel_amount, others_amount, advance_amount.
"""
import calendar
import os
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from core_functions import attendance_rules as rules
from core_functions import overtime as overtime_mod
from core_functions.attendance_rule_functions import penalty_days_for

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

DEFAULT_ODOMETER_RATE_FALLBACK = "3"     # rupees per km shown by the app (odometerMath.ts DEFAULT_RATE_PER_KM)
_PAID_KEYS = ("paid", "paid_on", "paid_date", "recomputed_net_salary")


def default_odometer_rate() -> Decimal:
    """Rupees per km used when neither the reading nor the organisation carries a rate.
    `DEFAULT_ODOMETER_RATE` (env, default 3); set it to 1 to restore the legacy fallback."""
    raw = os.getenv("DEFAULT_ODOMETER_RATE", DEFAULT_ODOMETER_RATE_FALLBACK)
    try:
        value = Decimal(str(raw).strip())
        if value.is_finite() and value > 0:
            return value
    except Exception:
        pass
    return Decimal(DEFAULT_ODOMETER_RATE_FALLBACK)


def _f(value) -> float:
    return float(value) if value is not None else 0.0


# --------------------------------------------------------------------------- small pure helpers
def derive_salary_type(has_payslip: bool, has_daily: bool, has_hourly: bool) -> str:
    """Enum key of what is assigned: payslip -> MONTHLY_FIXED, daily -> DAILY_WAGES, hourly -> HOURLY."""
    if has_payslip:
        return 'MONTHLY_FIXED'
    if has_daily:
        return 'DAILY_WAGES'
    if has_hourly:
        return 'HOURLY'
    return 'MONTHLY_FIXED'


def salary_types_of(has_payslip: bool, has_daily: bool, has_hourly: bool) -> List[str]:
    """Every salary type that applies (additive `salary_types`)."""
    out = []
    if has_payslip:
        out.append('MONTHLY_FIXED')
    if has_daily:
        out.append('DAILY_WAGES')
    if has_hourly:
        out.append('HOURLY')
    return out


def pick_detail(rows: List[dict], user_id: str) -> Optional[dict]:
    """The row of a template detail table for a user: the user's own row, else the template row (user_id NULL)."""
    template = None
    for r in rows or []:
        if r.get("user_id") == user_id:
            return r
        if r.get("user_id") is None and template is None:
            template = r
    return template


def odometer_amount(readings: List[dict], org_rate, default_rate) -> Decimal:
    """Sum of distance x rate where the rate is the reading's own stamp, else the organisation rate, else the default.
    Exact (Decimal), the way the database summed it."""
    default = Decimal(str(default_rate)) if default_rate is not None else default_odometer_rate()
    total = Decimal(0)
    for r in readings or []:
        if r.get("distance_km") is None:
            continue
        rate = r.get("allowance_rate")
        if rate is None:
            rate = org_rate if org_rate is not None else default
        total += Decimal(str(r["distance_km"])) * Decimal(str(rate))
    return total


def late_penalty(rule: Optional[dict], late_days: int) -> dict:
    """Penalty of the attendance rule for a month's late days (D-8): zero unless the organisation saved a rule.
    `penalty_units = floor(late_days / count)`, `penalty_days = units x (0.5 Half Day | 1.0 Leave)`."""
    if not rule:
        return {"configured": False, "penalty_units": 0, "penalty_days": 0.0}
    count = int(rule["late_punch_in_count"])
    consider = rule["consider_as"]
    return {
        "configured": True,
        "penalty_units": late_days // count if count >= 1 and late_days > 0 else 0,
        "penalty_days": penalty_days_for(late_days, count, consider),
    }


def fine_amount(basic, penalty_days, working_days) -> float:
    """`fine = penalty_days x basic / working_days`, two decimals (half up). 0 without a basic, penalty or working days."""
    if not basic or not penalty_days or not working_days or working_days <= 0:
        return 0.0
    value = Decimal(str(penalty_days)) * Decimal(str(basic)) / Decimal(int(working_days))
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def closed_session_hours(sessions_by_date: Dict[date, List[dict]], now) -> float:
    """Hours of every CLOSED session of the month (stored work hours, else out - in), two decimals."""
    total = 0.0
    for sessions in sessions_by_date.values():
        for s in sessions:
            if s.get("punch_in") is None or s.get("punch_out") is None:
                continue
            total += rules.session_hours(s["punch_in"], s["punch_out"], s.get("work_hours"), now)
    return round(total, 2)


def attendance_summary(year: int, month: int, sessions_by_date: Dict[date, List[dict]], shift: Optional[dict],
                       grace_minutes: int, leave_dates, holidays: Optional[Dict[date, str]], off_weekdays,
                       today: date, now) -> dict:
    """Day-type counters of the month plus the worked hours, from the shared attendance rules (the numbers the
    attendance calendar shows): working_days, present_days (distinct punched dates), late_days, half_days,
    leave_days, holiday_days, weekly_off_days, absent_days, total_days, total_hours."""
    in_month = {d: s for d, s in (sessions_by_date or {}).items() if d.year == year and d.month == month}
    days = rules.build_month(year, month, today, now, in_month, lambda d: shift, grace_minutes or 0,
                             leave_dates or (), holidays or {}, off_weekdays if off_weekdays is not None else (6,))
    counters = rules.summarize_days(days, calendar.monthrange(year, month)[1])
    counters["total_hours"] = closed_session_hours(in_month, now)
    return counters


# --------------------------------------------------------------------------- the engine
def compute_salary(inp: dict) -> dict:
    """Salary of one employee for one month. Returns the legacy `data` dict of `get_staff_salary` plus additive fields."""
    user_id, year, month = inp["user_id"], inp["year"], inp["month"]
    now = inp["now"]
    payslip = inp.get("payslip")

    basic = _f(payslip["basic_pay"]) if payslip else 0.0
    hra = _f(payslip["hra"]) if payslip else 0.0
    epf = _f(payslip["epf"]) if payslip else 0.0
    prof_tax = _f(payslip["professional_tax"]) if payslip else 0.0
    health_ins = _f(payslip["health_insurance"]) if payslip else 0.0
    custom_earnings = [{"name": r["name"], "amount": _f(r["amount"])} for r in inp.get("custom_earnings") or []]
    custom_deductions = [{"name": r["name"], "amount": _f(r["amount"])} for r in inp.get("custom_deductions") or []]

    # only the sessions that started in the month count (a night session belongs to its start date)
    sessions_by_date = {d: s for d, s in (inp.get("sessions_by_date") or {}).items()
                        if d.year == year and d.month == month}
    shift = inp.get("shift")
    att = attendance_summary(year, month, sessions_by_date, shift, inp.get("grace_minutes") or 0,
                             inp.get("leave_dates"), inp.get("holidays"), inp.get("off_weekdays"),
                             inp["today"], now)
    present_days = att["present_days"]
    total_hours = att["total_hours"]

    daily_rate, hourly_rate = inp.get("daily_rate"), inp.get("hourly_rate")
    daily_wages = _f(daily_rate) * present_days if daily_rate is not None else 0.0
    hourly_calc = _f(hourly_rate) * total_hours if hourly_rate is not None else 0.0

    incentive_count_amt = 0.0
    incentive_turnover_amt = 0.0
    bonus_amt = 0.0
    overtime_template = None
    for inc in inp.get("incentives") or []:
        kind = inc["type"]
        if kind == "OVERTIME":
            if overtime_template is None:
                overtime_template = inc
            continue
        d = pick_detail(inc.get("details"), user_id)
        if not d:
            continue
        if kind == "COUNT":
            incentive_count_amt += float(d["each_incentive"])
        elif kind == "TURNOVER":
            incentive_turnover_amt += basic * float(d["after_target_incentive_percent"]) / 100
        elif kind == "BONUS":
            if d.get("year") == year and d.get("month") == MONTH_NAMES[month]:
                bonus_amt += basic * float(d["bonus_percent"]) / 100

    ot_amt = 0.0
    ot_minutes = 0
    if overtime_template is not None:
        d = pick_detail(overtime_template.get("details"), user_id)
        if d:
            shift_minutes = overtime_mod.shift_minutes_of(shift["start_time"], shift["end_time"]) if shift else 0
            fig = overtime_mod.overtime_for_month(sessions_by_date, shift_minutes, int(d["duration_minutes"]),
                                                  d["amount"], now)
            ot_amt = float(fig["ot_amount"])
            ot_minutes = int(fig["ot_minutes"].to_integral_value())

    odometer_amt = float(odometer_amount(inp.get("odometer"), inp.get("org_allowance_rate"),
                                         inp.get("default_odometer_rate")))
    travel_amt = _f(inp.get("travel_amount"))
    others_amt = _f(inp.get("others_amount"))
    advance_amt = _f(inp.get("advance_amount"))

    penalty = late_penalty(inp.get("rule"), att["late_days"])
    fine = fine_amount(basic, penalty["penalty_days"], att["working_days"]) if payslip else 0.0

    total_incentive = incentive_count_amt + incentive_turnover_amt + bonus_amt
    total_earnings = (basic + hra + daily_wages + hourly_calc +
                      total_incentive + ot_amt + odometer_amt + travel_amt + others_amt +
                      sum(e["amount"] for e in custom_earnings))
    total_deductions = (epf + prof_tax + health_ins + advance_amt + sum(d["amount"] for d in custom_deductions)
                        + fine)
    net_salary = total_earnings - total_deductions

    has_payslip = payslip is not None
    has_daily = daily_rate is not None
    has_hourly = hourly_rate is not None
    return {
        "user_id": user_id,
        "month": month,
        "year": year,
        "present_days": present_days,
        "total_hours": round(total_hours, 2),
        "earnings": {
            "basic":               basic,
            "hra":                 hra,
            "daily_wages":         round(daily_wages, 2),
            "hourly_calc":         round(hourly_calc, 2),
            "incentive_count":     round(incentive_count_amt, 2),
            "incentive_turnover":  round(incentive_turnover_amt, 2),
            "bonus":               round(bonus_amt, 2),
            "overtime":            round(ot_amt, 2),
            "odometer":            round(odometer_amt, 2),
            "travel":              round(travel_amt, 2),
            "others":              round(others_amt, 2),
            "custom":              custom_earnings,
        },
        "deductions": {
            "epf":              epf,
            "professional_tax": prof_tax,
            "health_insurance": health_ins,
            "advance":          round(advance_amt, 2),
            "fine":             round(fine, 2),
            "custom":           custom_deductions,
        },
        "total_earnings":   round(total_earnings, 2),
        "total_deductions": round(total_deductions, 2),
        "net_salary":       round(net_salary, 2),
        # additive
        "salary_type":      derive_salary_type(has_payslip, has_daily, has_hourly),
        "salary_types":     salary_types_of(has_payslip, has_daily, has_hourly),
        "working_days":     att["working_days"],
        "half_days":        att["half_days"],
        "leave_days":       att["leave_days"],
        "holiday_days":     att["holiday_days"],
        "weekly_off_days":  att["weekly_off_days"],
        "absent_days":      att["absent_days"],
        "late_days":        att["late_days"],
        "penalty_days":     penalty["penalty_days"],
        "fine_rule_configured": penalty["configured"],
        "overtime_minutes": ot_minutes,
        "paid":             False,
        "paid_on":          None,
        "paid_date":        None,
    }


# --------------------------------------------------------------------------- paid state
def snapshot_of(data: dict) -> dict:
    """The part of a computed salary that is stored when it is paid (everything except the paid flags)."""
    return {k: v for k, v in data.items() if k not in _PAID_KEYS}


def apply_paid_state(data: dict, paid: Optional[dict]) -> dict:
    """Overlay the stored payment on a freshly computed salary (decision: a paid month is not recomputed silently).

    `paid` = None (unpaid: live figures, `paid: false`) or `{net_amount, paid_date (ISO string), snapshot (dict|None)}`.
    With a snapshot every stored figure is returned as it was when paid; without one (paid before snapshots existed)
    the components stay live and `net_salary` is the stored amount (`recomputed_net_salary` shows the live figure when
    it differs)."""
    if not paid:
        out = dict(data)
        out.update({"paid": False, "paid_on": None, "paid_date": None})
        return out
    snap = paid.get("snapshot")
    if isinstance(snap, dict) and snap.get("earnings") is not None:
        out = dict(snap)
    else:
        out = dict(data)
        stored = round(float(paid["net_amount"]), 2)
        if stored != out["net_salary"]:
            out["recomputed_net_salary"] = out["net_salary"]
            out["net_salary"] = stored
    out["paid"] = True
    out["paid_on"] = paid.get("paid_date")
    out["paid_date"] = paid.get("paid_date")
    return out
