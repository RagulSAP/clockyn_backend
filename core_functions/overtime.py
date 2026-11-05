"""Overtime computation shared by the overtime report, the salary breakdown / monthly list and the payslip
(decision D-9: the monthly aggregate formula is kept; only the two-session and night-shift defects are fixed).

Definition (per user and month):
  present_days   = distinct attendance dates with a punch (a two-session day counts once)
  worked_minutes = sum of the work hours of CLOSED sessions on those dates x 60
  shift_minutes  = assigned shift duration with midnight wrap (09:00-18:00 = 540, 21:00-06:00 = 540), 0 when no shift
  ot_minutes     = max(0, worked_minutes - shift_minutes x present_days)
  ot_amount      = floor(ot_minutes / duration_minutes) x amount
  ot_days        = dates whose own worked minutes exceed shift_minutes (informational)

A day with an auto-closed session (forgotten punch-out closed by the server, D-22) is skipped entirely until an admin
reviews it: its hours are a guess, and counting only the day's shift would drag the monthly aggregate down.
A night-shift session belongs to the date it started on (attendance_date), so it is neither split nor counted twice.

`overtime_for_month` is the one pure helper (used by the report, by `payroll_engine.compute_salary`, therefore by every
salary figure); `compute_overtime` is its database wrapper for the report.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Iterable, List, Optional

from psycopg2.extras import RealDictCursor

from core_functions import attendance_rules as rules
from core_functions.db_function import get_db_connection, return_connection
from utils.timezone import get_utc_now

_OT_STAFF_SQL = """
SELECT DISTINCT ON (ai.user_id)
       ai.user_id, d.duration_minutes, d.amount, u.name, s.start_time, s.end_time
FROM assigned_incentives ai
JOIN incentive_templates t ON t.incentive_id = ai.incentive_id AND t.type = 'OVERTIME'
JOIN incentive_overtime_details d
     ON d.incentive_id = t.incentive_id AND (d.user_id = ai.user_id OR d.user_id IS NULL)
JOIN users u ON u.user_id = ai.user_id AND u.parent_id = %(org)s
            AND u.status = 1 AND u.role_id IN (2, 3)
LEFT JOIN users_additional_info uai ON uai.user_id = u.user_id AND uai.shift_id > 0
LEFT JOIN shift s ON s.shift_id = uai.shift_id AND s.org_id = u.parent_id AND s.status = 1
WHERE ai.org_id = %(org)s
  AND (%(ids)s::text[] IS NULL OR ai.user_id = ANY(%(ids)s::text[]))
ORDER BY ai.user_id, ai.id, (d.user_id IS NOT NULL) DESC
"""

ATTENDANCE_ROWS_SQL = """
SELECT a.user_id, a.attendance_date, a.punch_in, a.punch_out, a.work_hours, a.auto_closed
FROM attendance a
WHERE a.user_id = ANY(%s) AND a.attendance_date >= %s AND a.attendance_date < %s
  AND a.punch_in IS NOT NULL
ORDER BY a.user_id, a.attendance_date, a.punch_in, a.id
"""


def month_bounds(year: int, month: int):
    """First day of the month and first day of the next month."""
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start, end


def group_attendance_rows(rows: Iterable[dict]) -> Dict[str, Dict[date, List[dict]]]:
    """attendance rows (any order) -> {user_id: {attendance_date: [session dicts sorted by punch-in]}}."""
    out: Dict[str, Dict[date, List[dict]]] = {}
    for r in rows:
        if r.get("punch_in") is None:
            continue
        wh = r.get("work_hours")
        out.setdefault(r["user_id"], {}).setdefault(r["attendance_date"], []).append({
            "punch_in": r["punch_in"],
            "punch_out": r.get("punch_out"),
            "work_hours": float(wh) if wh is not None else None,
            "auto_closed": bool(r.get("auto_closed")),
        })
    for by_date in out.values():
        for sessions in by_date.values():
            sessions.sort(key=lambda s: rules.to_utc(s["punch_in"]))
    return out


def shift_minutes_of(start, end) -> int:
    """Shift length in minutes with midnight wrap; 0 when there is no (usable) shift."""
    span = rules.shift_span_minutes(start, end)
    return span if span else 0


def ot_figures(worked_minutes, shift_minutes, present_days: int, duration_minutes: int, amount) -> tuple:
    """Pure maths: returns (ot_minutes, ot_amount) as (Decimal, Decimal)."""
    worked = Decimal(str(worked_minutes))
    ot_minutes = max(Decimal(0), worked - Decimal(str(shift_minutes)) * present_days)
    slots = int(ot_minutes // duration_minutes) if duration_minutes and duration_minutes > 0 else 0
    return ot_minutes, Decimal(slots) * Decimal(str(amount))


def day_worked_minutes(sessions: List[dict], now: datetime) -> Decimal:
    """Minutes of the CLOSED sessions of one day (an open session adds nothing), exact (Decimal)."""
    hours = Decimal(0)
    for s in sessions:
        if s.get("punch_out") is None:
            continue
        h = rules.session_hours(s.get("punch_in"), s.get("punch_out"), s.get("work_hours"), now)
        hours += Decimal(str(round(h, 2)))
    return hours * 60


def overtime_for_month(sessions_by_date: Dict[date, List[dict]], shift_minutes, duration_minutes, amount,
                       now: Optional[datetime] = None) -> dict:
    """The single overtime helper. `sessions_by_date` = {attendance_date: [{punch_in, punch_out, work_hours,
    auto_closed}]} of ONE user and ONE month. Returns `{present_days, worked_minutes (Decimal), ot_days,
    ot_minutes (Decimal), ot_amount (Decimal)}`."""
    now = now or get_utc_now()
    shift = Decimal(str(shift_minutes or 0))
    present_days = 0
    ot_days = 0
    worked = Decimal(0)
    for day in sorted(sessions_by_date):
        sessions = [s for s in sessions_by_date[day] if s.get("punch_in") is not None]
        if not sessions or any(s.get("auto_closed") for s in sessions):
            continue
        present_days += 1
        minutes = day_worked_minutes(sessions, now)
        worked += minutes
        if minutes > shift:
            ot_days += 1
    ot_minutes, ot_amount = ot_figures(worked, shift, present_days, duration_minutes, amount)
    return {"present_days": present_days, "worked_minutes": worked, "ot_days": ot_days,
            "ot_minutes": ot_minutes, "ot_amount": ot_amount}


def compute_overtime(user_ids: Optional[Iterable[str]], org_id: str, year: int, month: int) -> List[dict]:
    """Overtime per active role 2/3 staff member of `org_id` that has an overtime template.

    `user_ids=None` means every such staff member. Users without a template are
    not returned. Rows: {user_id, ot_days, ot_minutes, ot_amount}.
    """
    start, end = month_bounds(year, month)
    ids = None if user_ids is None else list(user_ids)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(_OT_STAFF_SQL, {"org": org_id, "ids": ids})
        staff = sorted(cur.fetchall(), key=lambda r: (r["name"] or "", r["user_id"]))
        by_user: Dict[str, Dict[date, List[dict]]] = {}
        if staff:
            cur.execute(ATTENDANCE_ROWS_SQL, ([r["user_id"] for r in staff], start, end))
            by_user = group_attendance_rows(cur.fetchall())
    finally:
        cur.close()
        return_connection(conn)
    now = get_utc_now()
    result = []
    for r in staff:
        fig = overtime_for_month(by_user.get(r["user_id"], {}), shift_minutes_of(r["start_time"], r["end_time"]),
                                 int(r["duration_minutes"]), r["amount"], now)
        result.append({
            "user_id": r["user_id"],
            "ot_days": fig["ot_days"],
            "ot_minutes": int(fig["ot_minutes"].to_integral_value()),
            "ot_amount": float(round(fig["ot_amount"], 2)),
        })
    return result
