from datetime import date, datetime
from zoneinfo import ZoneInfo
import calendar
import psycopg2
import psycopg2.errors
from core_functions.db_function import get_db_connection, return_connection

_IST = ZoneInfo("Asia/Kolkata")

_DUPLICATE = {"success": False, "message": "A holiday already exists for this date and scope"}


def get_off_days(org_id: str) -> dict:
    """Return the configured weekly off days for the org (0=Mon ... 6=Sun, Python weekday).
    Defaults to [6] (Sunday) if not configured."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT off_days FROM org_off_days WHERE org_id = %s
        """, (org_id,))
        row = cursor.fetchone()
        return {"success": True, "off_days": row[0] if row else [6]}
    finally:
        cursor.close()
        return_connection(conn)


def normalise_off_days(off_days) -> list:
    """Validate weekly off days (ints 0=Mon ... 6=Sun), de-duplicate and sort. Raises ValueError otherwise."""
    if off_days is None:
        raise ValueError("off_days is required")
    out = set()
    for d in off_days:
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6:
            raise ValueError("off_days must contain integers from 0 (Monday) to 6 (Sunday)")
        out.add(d)
    return sorted(out)


def set_off_days(org_id: str, off_days: list) -> dict:
    """Upsert the weekly off days for the org (validated, de-duplicated, sorted)."""
    days = normalise_off_days(off_days)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO org_off_days (org_id, off_days)
            VALUES (%s, %s)
            ON CONFLICT (org_id) DO UPDATE SET off_days = EXCLUDED.off_days
        """, (org_id, days))
        conn.commit()
        return {"success": True, "message": "Off days updated", "off_days": days}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def _scope(value) -> str | None:
    """A blank branch / department means 'whole organisation'."""
    value = (value or "").strip()
    return value or None


def _clean_name(name) -> str:
    name = (name or "").strip()
    if not name:
        raise ValueError("Holiday name is required")
    return name


def _clean_date(date_str) -> str:
    try:
        return date.fromisoformat(str(date_str).strip()).isoformat()
    except (TypeError, ValueError):
        raise ValueError("Holiday date must be a valid date in YYYY-MM-DD format")


def get_holidays(org_id: str, year: int, month: int) -> dict:
    if not 1 <= month <= 12 or not 1 <= year <= 9999:
        raise ValueError("year and month must be a valid calendar month")
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT id, name, date, branch_name, dept_name, block_attendance
            FROM org_holidays
            WHERE org_id = %s
              AND EXTRACT(YEAR FROM date) = %s
              AND EXTRACT(MONTH FROM date) = %s
            ORDER BY date, id
        """, (org_id, year, month))
        rows = cursor.fetchall()
        data = [
            {"id": r[0], "name": r[1], "date": str(r[2]),
             "branch_name": r[3], "dept_name": r[4],
             "block_attendance": True if r[5] is None else bool(r[5])}
            for r in rows
        ]
        return {"success": True, "data": data}
    finally:
        cursor.close()
        return_connection(conn)


def add_holiday(org_id: str, name: str, date_str: str,
                branch_name: str | None, dept_name: str | None,
                block_attendance: bool = True) -> dict:
    """Add one holiday. Raises ValueError for a blank name / invalid date; an existing holiday with the same
    org + date + branch + department scope is reported as `success: False` (unique index backs this up)."""
    name = _clean_name(name)
    date_str = _clean_date(date_str)
    branch_name, dept_name = _scope(branch_name), _scope(dept_name)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 1 FROM org_holidays
            WHERE org_id = %s AND date = %s
              AND COALESCE(branch_name, '') = %s AND COALESCE(dept_name, '') = %s
            LIMIT 1
        """, (org_id, date_str, branch_name or "", dept_name or ""))
        if cursor.fetchone():
            conn.rollback()
            return dict(_DUPLICATE)
        cursor.execute("""
            INSERT INTO org_holidays (org_id, name, date, branch_name, dept_name, block_attendance)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (org_id, name, date_str, branch_name, dept_name, bool(block_attendance)))
        new_id = cursor.fetchone()[0]
        conn.commit()
        return {"success": True, "id": new_id, "message": "Holiday added successfully"}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return dict(_DUPLICATE)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def delete_holiday(org_id: str, holiday_id: int) -> dict:
    """Delete one holiday of this org; an id of another org (or one that no longer exists) is 'not found'."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM org_holidays WHERE id = %s AND org_id = %s",
            (holiday_id, org_id)
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return {"success": False, "message": "Holiday not found"}
        conn.commit()
        return {"success": True, "message": "Holiday deleted"}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def update_holiday(org_id: str, holiday_id: int, name: str | None,
                   date_str: str | None = None,
                   block_attendance: bool | None = None) -> dict:
    """Update the holiday name and optionally the date and/or attendance-block flag.
    The holiday must belong to `org_id`; otherwise it is reported as not found."""
    if name is not None:
        name = _clean_name(name)
    if date_str:
        date_str = _clean_date(date_str)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT branch_name, dept_name FROM org_holidays WHERE id = %s AND org_id = %s",
            (holiday_id, org_id)
        )
        current = cursor.fetchone()
        if not current:
            conn.rollback()
            return {"success": False, "message": "Holiday not found"}

        fields: list = []
        params: list = []
        if name is not None:
            fields.append("name = %s")
            params.append(name)
        if date_str:
            cursor.execute("""
                SELECT 1 FROM org_holidays
                WHERE org_id = %s AND date = %s AND id <> %s
                  AND COALESCE(branch_name, '') = %s AND COALESCE(dept_name, '') = %s
                LIMIT 1
            """, (org_id, date_str, holiday_id, current[0] or "", current[1] or ""))
            if cursor.fetchone():
                conn.rollback()
                return dict(_DUPLICATE)
            fields.append("date = %s")
            params.append(date_str)
        if block_attendance is not None:
            fields.append("block_attendance = %s")
            params.append(bool(block_attendance))
        if fields:
            params.extend([holiday_id, org_id])
            cursor.execute(
                f"UPDATE org_holidays SET {', '.join(fields)} WHERE id = %s AND org_id = %s",
                params
            )
            conn.commit()
        return {"success": True, "message": "Holiday updated"}
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return dict(_DUPLICATE)
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def check_holiday_block(org_id: str, user_id: str) -> str | None:
    """Return the holiday name if today (IST) is a blocking holiday for this user's
    branch/dept, else None. A holiday with no branch/dept set applies org-wide."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT b.branch_name, d.dept_name
            FROM users u
            LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = COALESCE(u.parent_id, u.org_id)
            LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = COALESCE(u.parent_id, u.org_id)
            WHERE u.user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        user_branch = row[0] if row else None
        user_dept   = row[1] if row else None

        cursor.execute("""
            SELECT name FROM org_holidays
            WHERE org_id = %s
              AND date = %s
              AND block_attendance = TRUE
              AND (branch_name IS NULL OR branch_name = %s)
              AND (dept_name IS NULL OR dept_name = %s)
            LIMIT 1
        """, (org_id, datetime.now(_IST).date(), user_branch, user_dept))
        holiday = cursor.fetchone()
        return holiday[0] if holiday else None
    finally:
        cursor.close()
        return_connection(conn)


def get_working_days(org_id: str, year: int, month: int,
                     branch_name: str | None = None,
                     dept_name: str | None = None) -> dict:
    total = calendar.monthrange(year, month)[1]

    # Use configured off days instead of hardcoded Sunday
    off_days_result = get_off_days(org_id)
    off_weekdays = set(off_days_result.get("off_days", [6]))  # 0=Mon ... 6=Sun

    off_day_count = sum(
        1 for d in range(1, total + 1)
        if date(year, month, d).weekday() in off_weekdays
    )

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT COUNT(DISTINCT date)
            FROM org_holidays
            WHERE org_id = %s
              AND EXTRACT(YEAR FROM date) = %s
              AND EXTRACT(MONTH FROM date) = %s
              AND EXTRACT(ISODOW FROM date) - 1 NOT IN %s
        """
        params: list = [org_id, year, month, tuple(off_weekdays) if off_weekdays else (-1,)]

        if branch_name:
            query += " AND (branch_name IS NULL OR branch_name = %s)"
            params.append(branch_name)
        if dept_name:
            query += " AND (dept_name IS NULL OR dept_name = %s)"
            params.append(dept_name)

        cursor.execute(query, params)
        holiday_count = cursor.fetchone()[0] or 0
    finally:
        cursor.close()
        return_connection(conn)

    working_days = total - off_day_count - holiday_count
    return {
        "success": True,
        "total_days": total,
        "off_days": off_day_count,
        "holidays": holiday_count,
        "working_days": working_days,
    }
