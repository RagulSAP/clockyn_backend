"""Late punch-in attendance rule: the stored setting plus the pure penalty maths.

The rule drives `fine` (decision D-8) for organisations that saved one: `penalty_days_for` is the single definition of
"N late days become half a day / a day" and is used by `payroll_engine.late_penalty`, therefore by the salary
breakdown, the monthly list, the payslip preview and the attendance PDF. `fine = penalty_days x basic / working_days`
lives in `payroll_engine.fine_amount`. An organisation that never saved a rule has no penalty (`configured: false`).

`late_days_for_user_month` / `penalty_for_user_month` are single-employee database helpers kept for reports and tests;
they apply the same lateness rule as `attendance_rules.is_late` (the first punch-in of the day in IST against the shift
plus the grace of the rule).
"""
from datetime import time, datetime
from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from core_functions.db_function import get_db_connection, return_connection

DEFAULT_LATE_PUNCH_IN_COUNT = 3
DEFAULT_CONSIDER_AS = 'Half Day'
CONSIDER_AS_VALUES = ('Half Day', 'Leave')
IST_TZ = 'Asia/Kolkata'


def _org_exists(cur, org_id: str) -> bool:
    cur.execute("SELECT 1 FROM org_info WHERE org_id = %s LIMIT 1", (org_id,))
    return cur.fetchone() is not None


def get_rule(org_id: str) -> dict:
    """Return the saved rule, or the defaults with configured=False."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if not _org_exists(cur, org_id):
            raise HTTPException(status_code=404, detail="Organization not found")
        cur.execute(
            "SELECT late_punch_in_count, consider_as FROM attendance_rule WHERE org_id = %s",
            (org_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {
                "late_punch_in_count": DEFAULT_LATE_PUNCH_IN_COUNT,
                "consider_as": DEFAULT_CONSIDER_AS,
                "configured": False,
            }
        return {
            "late_punch_in_count": int(row['late_punch_in_count']),
            "consider_as": row['consider_as'],
            "configured": True,
        }
    finally:
        cur.close()
        return_connection(conn)


def save_rule(org_id: str, late_punch_in_count: int, consider_as: str, updated_by: str = None) -> dict:
    """Idempotent upsert of the org's rule. Raises 422 on invalid values, 404 on unknown org."""
    if (isinstance(late_punch_in_count, bool) or not isinstance(late_punch_in_count, int)
            or not 1 <= late_punch_in_count <= 10):
        raise HTTPException(status_code=422, detail="late_punch_in_count must be an integer between 1 and 10")
    if consider_as not in CONSIDER_AS_VALUES:
        raise HTTPException(status_code=422, detail="consider_as must be 'Half Day' or 'Leave'")
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if not _org_exists(cur, org_id):
            raise HTTPException(status_code=404, detail="Organization not found")
        cur.execute(
            """
            INSERT INTO attendance_rule (org_id, late_punch_in_count, consider_as, updated_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (org_id) DO UPDATE SET
                late_punch_in_count = EXCLUDED.late_punch_in_count,
                consider_as = EXCLUDED.consider_as,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
            """,
            (org_id, late_punch_in_count, consider_as, updated_by),
        )
        conn.commit()
        return {"late_punch_in_count": late_punch_in_count, "consider_as": consider_as}
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        return_connection(conn)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def is_late(punch_time: time, shift_start: time, shift_end: time, grace_minutes: int = 0) -> bool:
    """True when `punch_time` (IST time of day) is later than shift start + grace.

    Day shift (end >= start): plain time-of-day comparison. Night shift
    (end < start): the punch is late when it falls within 12 hours after
    start + grace (measured modulo 24h), so an early arrival at 20:00 for a
    21:00 shift is not late.
    """
    limit = shift_start.hour * 3600 + shift_start.minute * 60 + shift_start.second + grace_minutes * 60
    p = punch_time.hour * 3600 + punch_time.minute * 60 + punch_time.second
    if shift_end >= shift_start:
        return p > limit
    return 0 < (p - limit) % (24 * 3600) <= 12 * 3600


def penalty_days_for(late_days: int, late_punch_in_count: int, consider_as: str) -> float:
    """penalty_units = floor(late_days / count); days = units x (0.5 Half Day | 1.0 Leave)."""
    if late_punch_in_count < 1 or late_days <= 0:
        return 0.0
    units = late_days // late_punch_in_count
    return units * (0.5 if consider_as == 'Half Day' else 1.0)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def late_days_for_user_month(user_id: str, year: int, month: int, grace_minutes: int = 0) -> int:
    """Number of days whose first punch-in (IST) is later than the shift start + grace.

    Staff without a shift (no shift_id, or shift_id 0) are never late.
    """
    start = datetime(year, month, 1).date()
    end = (datetime(year + (month == 12), (month % 12) + 1, 1)).date()
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT s.start_time, s.end_time,
                   (MIN(a.punch_in) AT TIME ZONE %s)::time AS first_in
            FROM attendance a
            JOIN LATERAL (
                SELECT uai.shift_id FROM users_additional_info uai
                WHERE uai.user_id = a.user_id AND uai.shift_id > 0 LIMIT 1) u ON TRUE
            JOIN shift s ON s.shift_id = u.shift_id
            WHERE a.user_id = %s AND a.attendance_date >= %s AND a.attendance_date < %s
              AND a.punch_in IS NOT NULL
            GROUP BY a.attendance_date, s.start_time, s.end_time
            """,
            (IST_TZ, user_id, start, end),
        )
        return sum(1 for r in cur.fetchall()
                   if is_late(r['first_in'], r['start_time'], r['end_time'], grace_minutes))
    finally:
        cur.close()
        return_connection(conn)


def penalty_for_user_month(org_id: str, user_id: str, year: int, month: int) -> dict:
    """Late days and penalty for a staff member; all zeros when the org has no saved rule."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT late_punch_in_count, consider_as, grace_minutes FROM attendance_rule WHERE org_id = %s",
            (org_id,),
        )
        rule = cur.fetchone()
    finally:
        cur.close()
        return_connection(conn)
    if rule is None:
        return {"configured": False, "late_days": 0, "penalty_units": 0, "penalty_days": 0.0}
    late = late_days_for_user_month(user_id, year, month, int(rule['grace_minutes']))
    count = int(rule['late_punch_in_count'])
    return {
        "configured": True,
        "late_days": late,
        "penalty_units": late // count,
        "penalty_days": penalty_days_for(late, count, rule['consider_as']),
    }
