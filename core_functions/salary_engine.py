"""Database layer of the payroll engine: loads the inputs of `payroll_engine.compute_salary` with ONE set-based
statement per component for a whole organisation (the number of statements does not depend on the number of staff),
runs the pure engine per employee and overlays the stored paid state.

Public API
  compute_org_salaries(org_id, year, month, user_ids=None) -> [row]     # breakdown, monthly list, mark-paid
  compute_user_salary(user_id, year, month) -> row                       # get_staff_salary wrapper
  month_attendance_figures(org_id, user_id, year, month, cursor=None)    # counters + late penalty (payslip preview, PDF)
  row = {user_id, name, emp_id, role_name, branch_name, dept_name, data, error}
        `data` = the `payroll_engine.compute_salary` dict with the paid state applied (None when `error`).

A failure while computing one employee is logged and returned as that employee's row with `error: true` and
`data: None`; it is never turned into a row of zeros.
"""
import logging
from datetime import date
from typing import Dict, List, Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from core_functions import attendance_rules as rules
from core_functions import payroll_engine as engine
from core_functions.attendance_functions import _guarded
from core_functions.db_function import get_db_connection, return_connection
from core_functions.overtime import ATTENDANCE_ROWS_SQL, group_attendance_rows, month_bounds
from utils.timezone import get_utc_now

logger = logging.getLogger(__name__)

IST_TZ = 'Asia/Kolkata'

USERS_SQL = """
SELECT u.user_id, u.name, u.emp_id, COALESCE(r.role_name, '') AS role_name,
       b.branch_name, d.dept_name, s.start_time, s.end_time
FROM users u
LEFT JOIN role_info r ON r.role_id = u.role_id
LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = u.parent_id
LEFT JOIN users_additional_info uai ON uai.user_id = u.user_id AND uai.shift_id > 0
LEFT JOIN shift s ON s.shift_id = uai.shift_id AND s.org_id = u.parent_id AND s.status = 1
WHERE u.parent_id = %s
"""
USERS_ACTIVE_STAFF = " AND u.role_id IN (2, 3) AND u.status = 1"
USERS_BY_ID = " AND u.user_id = ANY(%s)"
USERS_ORDER = " ORDER BY u.name, u.user_id"

PAYSLIP_SQL = """
SELECT DISTINCT ON (pr.user_id)
       pr.user_id, ps.pay_slip_id, ps.pay_slip_name, ps.basic_pay, ps.hra, ps.epf, ps.professional_tax,
       ps.health_insurance
FROM payroll pr
JOIN pay_slip ps ON ps.pay_slip_id = pr.pay_slip_id
WHERE pr.user_id = ANY(%s)
ORDER BY pr.user_id, pr.id
"""
CUSTOM_EARNINGS_SQL = """
SELECT pay_slip_id, add_earnings_name AS name, add_earnings_amount AS amount
FROM payslip_add_earnings WHERE pay_slip_id = ANY(%s) ORDER BY id
"""
CUSTOM_DEDUCTIONS_SQL = """
SELECT pay_slip_id, add_deduction_name AS name, add_deduction_amount AS amount
FROM payslip_add_deduction WHERE pay_slip_id = ANY(%s) ORDER BY id
"""
WAGES_SQL = """
SELECT aw.user_id, dw.temp_type, dw.temp_amount
FROM assigned_wages aw
JOIN daily_hourly_wages dw ON dw.temp_id = aw.temp_id
WHERE aw.user_id = ANY(%s) AND dw.temp_type IN ('D', 'H')
  AND (dw.user_id IS NULL OR dw.user_id = aw.user_id)
ORDER BY aw.user_id, dw.temp_type, (dw.user_id IS NOT NULL) DESC, aw.id
"""
INCENTIVES_SQL = """
SELECT ai.user_id, ai.incentive_id, it.type::text AS type
FROM assigned_incentives ai
JOIN incentive_templates it ON it.incentive_id = ai.incentive_id
WHERE ai.org_id = %s AND ai.user_id = ANY(%s)
  AND it.type IN ('COUNT', 'TURNOVER', 'BONUS', 'OVERTIME')
ORDER BY ai.user_id, ai.id
"""
INCENTIVE_DETAIL_SQL = {
    'COUNT': "SELECT incentive_id, user_id, each_incentive FROM incentive_count_details "
             "WHERE incentive_id = ANY(%s) ORDER BY id",
    'TURNOVER': "SELECT incentive_id, user_id, after_target_incentive_percent FROM incentive_turnover_details "
                "WHERE incentive_id = ANY(%s) ORDER BY id",
    'BONUS': "SELECT incentive_id, user_id, bonus_percent, month, year FROM incentive_bonus_details "
             "WHERE incentive_id = ANY(%s) ORDER BY id",
    'OVERTIME': "SELECT incentive_id, user_id, duration_minutes, amount FROM incentive_overtime_details "
                "WHERE incentive_id = ANY(%s) ORDER BY id",
}
ODOMETER_SQL = """
SELECT user_id, distance_km, allowance_rate
FROM odometer_readings
WHERE org_id = %s AND user_id = ANY(%s) AND status = 'approved'
  AND reading_date >= %s AND reading_date < %s
"""
ORG_RATE_SQL = "SELECT rate FROM org_allowance_rate WHERE org_id = %s"
LEGACY_ORG_RATE_SQL = """
SELECT allowance_rate FROM odometer_readings
WHERE org_id = %s AND allowance_rate IS NOT NULL
ORDER BY updated_at DESC LIMIT 1
"""
EXPENSES_SQL = """
SELECT user_id, expense_type, COALESCE(SUM(amount), 0) AS total
FROM travel_bill_expenses
WHERE org_id = %s AND user_id = ANY(%s) AND status = 'approved' AND expense_type IN ('TRAVEL', 'BILL')
  AND expense_date >= %s AND expense_date < %s
GROUP BY user_id, expense_type
"""
ADVANCE_SQL = """
SELECT user_id, COALESCE(SUM(advance_amt), 0) AS total
FROM advance_details
WHERE user_id = ANY(%s) AND "date" >= %s AND "date" < %s
GROUP BY user_id
"""
RULE_SQL = "SELECT late_punch_in_count, consider_as, grace_minutes FROM attendance_rule WHERE org_id = %s"
LEAVE_SQL = """
SELECT r.user_id, ld.leave_date
FROM leave_dates ld
JOIN requests r ON r.request_id = ld.request_id
WHERE r.org_id = %s AND r.user_id = ANY(%s) AND r.request_type = 'leave'
  AND ld.leave_date >= %s AND ld.leave_date < %s
  AND COALESCE(ld.status, r.status, 'pending') = 'approved'
"""
HOLIDAY_SQL = """
SELECT h.date AS holiday_date, h.name AS holiday_name, h.branch_name, h.dept_name
FROM org_holidays h
WHERE h.org_id = %s AND h.date >= %s AND h.date < %s
ORDER BY h.date ASC, h.id ASC
"""
OFF_DAYS_SQL = "SELECT off_days FROM org_off_days WHERE org_id = %s"
PAID_SQL = """
SELECT user_id, net_amount, (paid_at AT TIME ZONE %s)::date AS paid_date, snapshot
FROM salary_payments
WHERE org_id = %s AND pay_year = %s AND pay_month = %s AND user_id = ANY(%s)
"""
PAID_NO_SNAPSHOT_SQL = """
SELECT user_id, net_amount, (paid_at AT TIME ZONE %s)::date AS paid_date, NULL::jsonb AS snapshot
FROM salary_payments
WHERE org_id = %s AND pay_year = %s AND pay_month = %s AND user_id = ANY(%s)
"""


# --------------------------------------------------------------------------- loaders
def _load_users(cur, org_id: str, user_ids: Optional[List[str]]) -> List[dict]:
    if user_ids is None:
        cur.execute(USERS_SQL + USERS_ACTIVE_STAFF + USERS_ORDER, (org_id,))
    else:
        cur.execute(USERS_SQL + USERS_BY_ID + USERS_ORDER, (org_id, list(user_ids)))
    seen, users = set(), []
    for r in cur.fetchall():           # users_additional_info could repeat a user: first row wins
        if r["user_id"] in seen:
            continue
        seen.add(r["user_id"])
        users.append(r)
    return users


def _load_attendance_context(cur, org_id: str, users: List[dict], year: int, month: int) -> dict:
    """Everything `payroll_engine.attendance_summary` needs, for every user: sessions, rule, leave dates, holidays
    (branch / department scoped), off weekdays."""
    ids = [u["user_id"] for u in users]
    first, nxt = month_bounds(year, month)
    cur.execute(ATTENDANCE_ROWS_SQL, (ids, first, nxt))
    sessions = group_attendance_rows(cur.fetchall())

    def _read_rule():
        cur.execute(RULE_SQL, (org_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    rule = _guarded(cur, "sp_pay_rule", _read_rule, None)

    cur.execute(LEAVE_SQL, (org_id, ids, first, nxt))
    leaves: Dict[str, List[date]] = {}
    for r in cur.fetchall():
        leaves.setdefault(r["user_id"], []).append(r["leave_date"])

    cur.execute(HOLIDAY_SQL, (org_id, first, nxt))
    holiday_rows = cur.fetchall()

    def _read_off_days():
        cur.execute(OFF_DAYS_SQL, (org_id,))
        row = cur.fetchone()
        return list(row["off_days"]) if row and row.get("off_days") is not None else [6]
    off_weekdays = _guarded(cur, "sp_pay_offdays", _read_off_days, [6])

    holidays: Dict[str, Dict[date, str]] = {}
    for u in users:
        mine: Dict[date, str] = {}
        for h in holiday_rows:
            if h["branch_name"] is not None and h["branch_name"] != u["branch_name"]:
                continue
            if h["dept_name"] is not None and h["dept_name"] != u["dept_name"]:
                continue
            mine.setdefault(h["holiday_date"], h["holiday_name"])
        holidays[u["user_id"]] = mine
    return {"sessions": sessions, "rule": rule, "leaves": leaves, "holidays": holidays, "off_weekdays": off_weekdays}


def _attendance_inputs(u: dict, actx: dict) -> dict:
    shift = {"start_time": u["start_time"], "end_time": u["end_time"]} if u["start_time"] and u["end_time"] else None
    rule = actx["rule"]
    return {
        "sessions_by_date": actx["sessions"].get(u["user_id"], {}),
        "shift": shift,
        "grace_minutes": int(rule["grace_minutes"]) if rule and rule.get("grace_minutes") is not None else 0,
        "leave_dates": actx["leaves"].get(u["user_id"], []),
        "holidays": actx["holidays"].get(u["user_id"], {}),
        "off_weekdays": actx["off_weekdays"],
        "rule": ({"late_punch_in_count": rule["late_punch_in_count"], "consider_as": rule["consider_as"]}
                 if rule else None),
    }


def _load_paid(cur, org_id: str, ids: List[str], year: int, month: int) -> Dict[str, dict]:
    """Stored payments of the month: {user_id: {net_amount, paid_date (ISO), snapshot}}. Falls back to the statement
    without the snapshot column on a database where migration 032 is not applied yet."""
    def _read(sql):
        def run():
            cur.execute(sql, (IST_TZ, org_id, year, month, ids))
            return cur.fetchall()
        return run
    rows = _guarded(cur, "sp_pay_paid", _read(PAID_SQL), None)
    if rows is None:
        rows = _guarded(cur, "sp_pay_paid2", _read(PAID_NO_SNAPSHOT_SQL), [])
    out = {}
    for r in rows:
        pd = r["paid_date"]
        out[r["user_id"]] = {
            "net_amount": r["net_amount"],
            "paid_date": pd.isoformat() if pd is not None else None,
            "snapshot": r.get("snapshot"),
        }
    return out


def _org_allowance_rate(cur, org_id: str):
    """The organisation's rupees-per-km, None when never set (the engine then uses the default)."""
    def _read():
        cur.execute(ORG_RATE_SQL, (org_id,))
        row = cur.fetchone()
        return ("table", row["rate"] if row else None)
    found = _guarded(cur, "sp_pay_orgrate", _read, None)
    if found is not None:
        return found[1]

    def _legacy():                       # database without migration 032: the latest reading's rate
        cur.execute(LEGACY_ORG_RATE_SQL, (org_id,))
        row = cur.fetchone()
        return row["allowance_rate"] if row else None
    return _guarded(cur, "sp_pay_orgrate2", _legacy, None)


def _group(rows, key="user_id") -> Dict[str, list]:
    out: Dict[str, list] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


# --------------------------------------------------------------------------- the batch
def compute_with_cursor(cur, org_id: str, year: int, month: int, user_ids: Optional[List[str]] = None,
                        now=None) -> List[dict]:
    """The whole batch on an open cursor: about 17 statements whatever the number of staff."""
    now = now or get_utc_now()
    today = rules.ist_today(now)
    first, nxt = month_bounds(year, month)

    users = _load_users(cur, org_id, user_ids)
    if not users:
        return []
    ids = [u["user_id"] for u in users]

    actx = _load_attendance_context(cur, org_id, users, year, month)

    cur.execute(PAYSLIP_SQL, (ids,))
    payslips = {r["user_id"]: r for r in cur.fetchall()}
    slip_ids = [p["pay_slip_id"] for p in payslips.values()]
    earn_by_slip: Dict[str, list] = {}
    ded_by_slip: Dict[str, list] = {}
    if slip_ids:
        cur.execute(CUSTOM_EARNINGS_SQL, (slip_ids,))
        earn_by_slip = _group(cur.fetchall(), "pay_slip_id")
        cur.execute(CUSTOM_DEDUCTIONS_SQL, (slip_ids,))
        ded_by_slip = _group(cur.fetchall(), "pay_slip_id")

    cur.execute(WAGES_SQL, (ids,))
    wage_rate: Dict[tuple, float] = {}
    for r in cur.fetchall():                         # first row per (user, type): the user's own copy wins
        wage_rate.setdefault((r["user_id"], r["temp_type"]),
                             float(r["temp_amount"]) if r["temp_amount"] is not None else None)

    cur.execute(INCENTIVES_SQL, (org_id, ids))
    assigned = cur.fetchall()
    details: Dict[str, List[dict]] = {}
    for kind, sql in INCENTIVE_DETAIL_SQL.items():
        inc_ids = sorted({a["incentive_id"] for a in assigned if a["type"] == kind})
        if inc_ids:
            cur.execute(sql, (inc_ids,))
            for d in cur.fetchall():
                details.setdefault(d["incentive_id"], []).append(d)
    incentives_by_user: Dict[str, List[dict]] = {}
    for a in assigned:
        incentives_by_user.setdefault(a["user_id"], []).append(
            {"type": a["type"], "incentive_id": a["incentive_id"], "details": details.get(a["incentive_id"], [])})

    cur.execute(ODOMETER_SQL, (org_id, ids, first, nxt))
    odometer = _group(cur.fetchall())
    org_rate = _org_allowance_rate(cur, org_id)

    cur.execute(EXPENSES_SQL, (org_id, ids, first, nxt))
    expenses = {(r["user_id"], r["expense_type"]): float(r["total"]) for r in cur.fetchall()}
    cur.execute(ADVANCE_SQL, (ids, first, nxt))
    advances = {r["user_id"]: float(r["total"]) for r in cur.fetchall()}

    paid = _load_paid(cur, org_id, ids, year, month)
    default_rate = engine.default_odometer_rate()

    rows = []
    for u in users:
        uid = u["user_id"]
        row = {"user_id": uid, "name": u["name"], "emp_id": u["emp_id"], "role_name": u["role_name"],
               "branch_name": u["branch_name"] or "", "dept_name": u["dept_name"] or "", "data": None, "error": False}
        try:
            slip = payslips.get(uid)
            inp = {
                "user_id": uid, "year": year, "month": month, "now": now, "today": today,
                "payslip": slip,
                "custom_earnings": earn_by_slip.get(slip["pay_slip_id"], []) if slip else [],
                "custom_deductions": ded_by_slip.get(slip["pay_slip_id"], []) if slip else [],
                "daily_rate": wage_rate.get((uid, 'D')),
                "hourly_rate": wage_rate.get((uid, 'H')),
                "incentives": incentives_by_user.get(uid, []),
                "odometer": odometer.get(uid, []),
                "org_allowance_rate": org_rate,
                "default_odometer_rate": default_rate,
                "travel_amount": expenses.get((uid, 'TRAVEL'), 0.0),
                "others_amount": expenses.get((uid, 'BILL'), 0.0),
                "advance_amount": advances.get(uid, 0.0),
            }
            inp.update(_attendance_inputs(u, actx))
            data = engine.compute_salary(inp)
            row["data"] = engine.apply_paid_state(data, paid.get(uid))
        except Exception:
            logger.exception("salary computation failed for user %s (%s-%s)", uid, year, month)
            row["error"] = True
        rows.append(row)
    return rows


def compute_org_salaries(org_id: str, year: int, month: int, user_ids: Optional[List[str]] = None) -> List[dict]:
    """Salary rows of an organisation's active staff (role 2/3), or of exactly `user_ids` (any role / status of the
    organisation). Constant number of statements."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        return compute_with_cursor(cur, org_id, year, month, user_ids)
    finally:
        cur.close()
        return_connection(conn)


def compute_user_salary(user_id: str, year: int, month: int) -> dict:
    """The row of ONE employee (404 for an unknown user, 500 when the computation itself fails)."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT parent_id FROM users WHERE user_id = %s", (user_id,))
        u = cur.fetchone()
        if not u or not u["parent_id"]:
            raise HTTPException(status_code=404, detail="Staff not found")
        rows = compute_with_cursor(cur, u["parent_id"], year, month, [user_id])
    finally:
        cur.close()
        return_connection(conn)
    if not rows:
        raise HTTPException(status_code=404, detail="Staff not found")
    if rows[0]["error"]:
        raise HTTPException(status_code=500, detail="Failed to fetch salary")
    return rows[0]


def month_attendance_figures(org_id: str, user_id: str, year: int, month: int, cursor=None, now=None) -> dict:
    """Attendance counters of one employee for a month plus the attendance-rule penalty (payslip preview and the
    attendance PDF use it so `fine` is the one the salary computation uses). Keys: the `attendance_summary` counters,
    `penalty` {configured, penalty_units, penalty_days}, `rule`."""
    own = cursor is None
    conn = None
    if own:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now = now or get_utc_now()
        users = _load_users(cursor, org_id, [user_id])
        if not users:
            raise HTTPException(status_code=404, detail="Staff not found")
        u = users[0]
        actx = _load_attendance_context(cursor, org_id, users, year, month)
        inp = _attendance_inputs(u, actx)
        summary = engine.attendance_summary(year, month, inp["sessions_by_date"], inp["shift"], inp["grace_minutes"],
                                            inp["leave_dates"], inp["holidays"], inp["off_weekdays"],
                                            rules.ist_today(now), now)
        summary["penalty"] = engine.late_penalty(inp["rule"], summary["late_days"])
        summary["rule"] = inp["rule"]
        return summary
    finally:
        if own:
            cursor.close()
            return_connection(conn)
