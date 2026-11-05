"""Which shift applies to a user on a calendar date (WP7, planner decision D-6).

An approved shift-change request is a ONE-DAY override: for `requests.shift_change_date` the user works the
requested shift, on every other date the shift assigned in `users_additional_info.shift_id` applies. Nothing is
ever written to `users_additional_info`; the override is resolved at read time.

Shift ids are per company, so every shift lookup also matches the organisation.

Layers
  * pure:      `pick_shift`, `normalise_date`, `effective_shift_id_sql`
  * cursor:    `shift_for_cursor(cursor, user_id, org_id, on_date)`   <- the integration point for other modules
               `shift_overrides_in_range(cursor, user_id, org_id, start, end)`
  * wrapper:   `shift_for(user_id, org_id, on_date)` opens its own connection

INTEGRATION POINT (lateness, overtime, reports): `core_functions.attendance_functions.shift_for(cursor, user_id,
org_id, on_date)` is a local stand-in with the same signature and the same `{start_time, end_time}` result keys;
its body should become `return shift_resolution.shift_for_cursor(cursor, user_id, org_id, on_date)`. Set-based SQL
(the dashboard lateness queries, `attendance_rule_functions` late counts, `overtime`) can embed
`effective_shift_id_sql(...)` in place of `uai.shift_id`.
"""
import logging
from datetime import date, datetime
from typing import Any, Dict, Optional

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

SOURCE_OVERRIDE = "override"
SOURCE_ASSIGNED = "assigned"

# Shift assigned to the user (org matched, active shift only). Params: (org_id, user_id)
ASSIGNED_SHIFT_SQL = """
    SELECT s.shift_id, s.start_time, s.end_time
    FROM users_additional_info uai
    JOIN shift s ON s.shift_id = uai.shift_id AND s.org_id = %s AND s.status = 1
    WHERE uai.user_id = %s AND uai.shift_id > 0
    LIMIT 1
"""

# Shift of the approved one-day override for one date (latest decision wins). Params: (user_id, org_id, on_date)
OVERRIDE_SHIFT_SQL = """
    SELECT s.shift_id, s.start_time, s.end_time
    FROM requests r
    JOIN shift s ON s.shift_id = r.requested_shift_id AND s.org_id = r.org_id AND s.status = 1
    WHERE r.user_id = %s AND r.org_id = %s AND r.request_type = 'shift_change'
      AND r.status = 'approved' AND r.shift_change_date = %s
    ORDER BY r.action_at DESC NULLS LAST, r.id DESC
    LIMIT 1
"""

# Every approved override of a user in a date range. Params: (user_id, org_id, start, end)
OVERRIDES_IN_RANGE_SQL = """
    SELECT r.shift_change_date, s.shift_id, s.start_time, s.end_time
    FROM requests r
    JOIN shift s ON s.shift_id = r.requested_shift_id AND s.org_id = r.org_id AND s.status = 1
    WHERE r.user_id = %s AND r.org_id = %s AND r.request_type = 'shift_change'
      AND r.status = 'approved' AND r.shift_change_date BETWEEN %s AND %s
    ORDER BY r.shift_change_date, r.action_at ASC NULLS FIRST, r.id ASC
"""


# ---------------------------------------------------------------------------- pure helpers
def normalise_date(value: Any) -> Optional[date]:
    """date / datetime / 'YYYY-MM-DD' (or longer ISO text) -> date; None or unparseable -> None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _field(row: Any, key: str, index: int) -> Any:
    """Column of a RealDictCursor row (mapping) or a plain tuple row."""
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def _shift_dict(row: Any, source: str, offset: int = 0) -> Dict[str, Any]:
    return {
        "shift_id": _field(row, "shift_id", offset),
        "start_time": _field(row, "start_time", offset + 1),
        "end_time": _field(row, "end_time", offset + 2),
        "source": source,
    }


def pick_shift(override: Optional[Dict[str, Any]], assigned: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The one-day override wins over the assigned shift; None when neither exists (staff without a shift)."""
    return override or assigned


def effective_shift_id_sql(user_id_expr: str = "u.user_id", org_expr: str = "u.parent_id",
                           date_expr: str = "a.attendance_date", assigned_expr: str = "uai.shift_id") -> str:
    """SQL expression (a scalar subquery inside COALESCE) giving the shift id that applies to a user on a date,
    for set-based queries. Every argument is a SQL fragment written by the caller (a column or a constant such as
    a bound-parameter cast), never user input. Usage: `JOIN shift s ON s.shift_id = <expr> AND s.org_id = ...`."""
    return (
        "COALESCE((SELECT r2.requested_shift_id FROM requests r2 "
        f"WHERE r2.user_id = {user_id_expr} AND r2.org_id = {org_expr} "
        "AND r2.request_type = 'shift_change' AND r2.status = 'approved' "
        f"AND r2.shift_change_date = {date_expr} "
        f"ORDER BY r2.action_at DESC NULLS LAST, r2.id DESC LIMIT 1), {assigned_expr})"
    )


# ---------------------------------------------------------------------------- cursor level
def shift_for_cursor(cursor, user_id: str, org_id: str, on_date: Any = None) -> Optional[Dict[str, Any]]:
    """Shift that applies to `user_id` (of company `org_id`) on `on_date`.

    Returns `{shift_id, start_time, end_time, source}` (time objects; source is 'override' or 'assigned') or None
    when the user has no active shift. `on_date=None` (or an unreadable value) skips the override lookup and
    returns the assigned shift, which is how the lookup behaved before overrides existed."""
    day = normalise_date(on_date)
    override = None
    if day is not None:
        cursor.execute(OVERRIDE_SHIFT_SQL, (user_id, org_id, day))
        row = cursor.fetchone()
        if row:
            override = _shift_dict(row, SOURCE_OVERRIDE)
    if override:
        return override
    cursor.execute(ASSIGNED_SHIFT_SQL, (org_id, user_id))
    row = cursor.fetchone()
    assigned = _shift_dict(row, SOURCE_ASSIGNED) if row else None
    return pick_shift(None, assigned)


def shift_overrides_in_range(cursor, user_id: str, org_id: str, start: Any, end: Any) -> Dict[str, Dict[str, Any]]:
    """{'YYYY-MM-DD': shift} of the approved one-day overrides of a user between two dates (inclusive)."""
    first, last = normalise_date(start), normalise_date(end)
    if first is None or last is None:
        return {}
    cursor.execute(OVERRIDES_IN_RANGE_SQL, (user_id, org_id, first, last))
    out: Dict[str, Dict[str, Any]] = {}
    for row in cursor.fetchall():
        day = normalise_date(_field(row, "shift_change_date", 0))
        if day is not None:
            out[day.isoformat()] = _shift_dict(row, SOURCE_OVERRIDE, offset=1)  # later rows (newer decisions) win
    return out


def format_shift_timing(shift: Optional[Dict[str, Any]]) -> Optional[str]:
    """`09:00 AM - 06:00 PM` (the text the request lists show), None without a shift."""
    if not shift or shift.get("start_time") is None or shift.get("end_time") is None:
        return None
    return f"{shift['start_time'].strftime('%I:%M %p')} - {shift['end_time'].strftime('%I:%M %p')}"


# ---------------------------------------------------------------------------- own connection
def shift_for(user_id: str, org_id: str, on_date: Any = None) -> Optional[Dict[str, Any]]:
    """`shift_for_cursor` on its own pooled connection (use the cursor form inside an open transaction)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        try:
            return shift_for_cursor(cursor, user_id, org_id, on_date)
        finally:
            cursor.close()
    finally:
        return_connection(conn)
