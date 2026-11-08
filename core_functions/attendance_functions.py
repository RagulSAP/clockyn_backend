from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from datetime import datetime, date, timedelta
from utils.timezone import get_utc_now, convert_utc_to_ist, format_ist_time, UTC, IST
from pydantic import BaseModel
from typing import Optional, List
from psycopg2.extras import RealDictCursor
import logging
import psycopg2.errors

from core_functions import attendance_rules as rules

logger = logging.getLogger(__name__)


class EditAttendanceRequest(BaseModel):
    staff_id: str
    org_id: Optional[str] = None
    date: str                        # "YYYY-MM-DD"
    punch_in: Optional[str] = None   # "HH:MM" 24-hour format
    punch_out: Optional[str] = None  # "HH:MM" 24-hour format
    session: Optional[int] = None    # 1 or 2 (default 1): which session of the day to edit or create

class AttendanceDay(BaseModel):
    date: str
    status: str  # 'present', 'absent', 'late', 'half_day', 'leave'
    punch_in: Optional[str] = None
    punch_out: Optional[str] = None
    total_hours: Optional[str] = None
    location: Optional[str] = None
    distance: Optional[str] = None
    punch_in_photo: Optional[str] = None
    punch_out_photo: Optional[str] = None
    # additive (P1-3): shift that applied, lateness in IST, number of sessions
    shift_start_time: Optional[str] = None
    shift_end_time: Optional[str] = None
    late_by_minutes: int = 0
    session_count: int = 0

class MonthlyAttendance(BaseModel):
    month: int
    year: int
    total_days: int
    present_days: int
    absent_days: int
    late_days: int
    attendance_data: List[AttendanceDay]

class DailyAttendanceResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: Optional[str]
    branch_name: Optional[str]
    punch_in: Optional[str] = None
    punch_out: Optional[str] = None
    total_hours: Optional[str] = None
    status: str
    location: Optional[str] = None


# --------------------------------------------------------------------------- shared DB helpers
def shift_for(cursor, user_id: str, org_id: str, on_date=None) -> Optional[dict]:
    """Shift that applies to `user_id` on `on_date`: `{start_time, end_time}` (time objects) or None.

    An approved one-day shift change for `on_date` wins over the assigned shift (core_functions/shift_resolution.py);
    the shift is matched on the organisation as well (shift ids are per company)."""
    from core_functions import shift_resolution

    row = shift_resolution.shift_for_cursor(cursor, user_id, org_id, on_date)
    if not row:
        return None
    return {"start_time": row["start_time"], "end_time": row["end_time"]}


def _guarded(cursor, name: str, fn, default):
    """Run `fn()` inside a SAVEPOINT: a failure (for example a table that is missing on an older database) is
    logged and `default` is returned instead of aborting the surrounding transaction."""
    cursor.execute("SAVEPOINT " + name)
    try:
        result = fn()
    except Exception as e:
        logger.warning("optional lookup %s failed: %s", name, e)
        cursor.execute("ROLLBACK TO SAVEPOINT " + name)
        return default
    cursor.execute("RELEASE SAVEPOINT " + name)
    return result


def org_grace_minutes(cursor, org_id: str) -> int:
    """Late-arrival grace of the organisation (attendance rule), 0 when no rule is saved."""
    def _read():
        cursor.execute("SELECT grace_minutes FROM attendance_rule WHERE org_id = %s", (org_id,))
        row = cursor.fetchone()
        return int(row["grace_minutes"]) if row and row.get("grace_minutes") is not None else 0
    return _guarded(cursor, "sp_att_grace", _read, 0)


# --------------------------------------------------------------------------- daily report
async def get_daily_attendance(org_id: str, target_date: str, branch_id: Optional[str] = None, dept_id: Optional[str] = None):
    """Get all staff attendance for a specific date"""
    if not org_id or not target_date:
        raise HTTPException(status_code=400, detail="Organization ID and target date are required")

    from core_functions.manager_functions import get_manager_scope
    resolved_org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(org_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        try:
            parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

        # One row per staff member: both sessions of the day are aggregated. punch_out / has_open come from the
        # LAST session (an open second session means the staff member is still working).
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email,
                   d.dept_name, b.branch_name,
                   agg.first_punch_in as punch_in,
                   agg.last_punch_out as punch_out,
                   agg.has_open as has_open,
                   agg.total_work_hours as work_hours,
                   agg.first_lng as longitude, agg.first_lat as latitude
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN (
                SELECT user_id,
                       MIN(punch_in) as first_punch_in,
                       (array_agg(punch_out ORDER BY punch_in DESC))[1] as last_punch_out,
                       bool_or(punch_in IS NOT NULL AND punch_out IS NULL) as has_open,
                       SUM(work_hours) as total_work_hours,
                       (array_agg(punch_in_location[0] ORDER BY punch_in ASC))[1] as first_lng,
                       (array_agg(punch_in_location[1] ORDER BY punch_in ASC))[1] as first_lat
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) agg ON u.user_id = agg.user_id
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
        """
        params = [parsed_date, resolved_org_id]

        if branch_id and branch_id != "0":
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)

        if dept_id and dept_id != "0":
            query += " AND u.dept_id = %s"
            params.append(dept_id)
        elif mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)

        query += " ORDER BY u.name"

        cursor.execute(query, tuple(params))
        staff_data = cursor.fetchall()

        attendance_list = []
        for staff in staff_data:
            if staff['punch_in'] and (staff.get('has_open') or not staff['punch_out']):
                status = "Working"
                total_hours = None
                punch_out_value = None
            elif staff['punch_in'] and staff['punch_out']:
                status = "Punched Out"
                total_hours = str(staff['work_hours']) if staff['work_hours'] else None
                punch_out_value = staff['punch_out']
            else:
                status = "Absent"
                total_hours = None
                punch_out_value = None

            punch_in = format_ist_time(staff['punch_in']) if staff['punch_in'] else None
            punch_out = format_ist_time(punch_out_value) if punch_out_value else None

            location = None
            if staff.get('latitude') and staff.get('longitude'):
                location = f"{staff['latitude']},{staff['longitude']}"

            attendance_list.append(DailyAttendanceResponse(
                user_id=staff['user_id'],
                name=staff['name'],
                emp_id=staff['emp_id'],
                mobile_number=staff['mobile_number'],
                email=staff['email'],
                dept_name=staff['dept_name'],
                branch_name=staff['branch_name'],
                punch_in=punch_in,
                punch_out=punch_out,
                total_hours=total_hours,
                status=status,
                location=location
            ))

        return attendance_list

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("daily attendance failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get daily attendance")
    finally:
        cursor.close()
        return_connection(conn)


# --------------------------------------------------------------------------- monthly report (v1 + detail=v2)
def _fmt_ist(dt, fmt="%I:%M %p"):
    return format_ist_time(dt, fmt) if dt else None


def _month_bounds(year: int, month: int):
    first = date(year, month, 1)
    nxt = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    return first, nxt


def _session_rows_by_date(records):
    """attendance rows (already ordered by date, punch_in) -> {date: [session dict]} with float work_hours."""
    out = {}
    for r in records:
        out.setdefault(r["attendance_date"], []).append({
            "punch_in": r["punch_in"],
            "punch_out": r["punch_out"],
            "work_hours": float(r["work_hours"]) if r.get("work_hours") is not None else None,
            "lng": r.get("lng"),
            "lat": r.get("lat"),
            "punch_in_photo": r.get("punchinface"),
            "punch_out_photo": r.get("punchoutface"),
            "auto_closed": bool(r.get("auto_closed")),
        })
    return out


def _day_figures(sessions, now):
    """First punch-in, last session's punch-out (None while it is open), live total, location, photos of a day."""
    punched = [s for s in sessions if s["punch_in"] is not None]
    first = punched[0] if punched else None
    last = punched[-1] if punched else None
    outs = [s["punch_out"] for s in sessions if s["punch_out"] is not None]
    last_out = last["punch_out"] if last else (max(outs) if outs else None)
    out_photo_session = max((s for s in sessions if s["punch_out"] is not None), key=lambda s: s["punch_out"], default=None)
    location = None
    if first is not None and first.get("lat") is not None and first.get("lng") is not None:
        location = f"{first['lat']},{first['lng']}"
    total = rules.total_hours(punched, now)
    return {
        "first_in": first["punch_in"] if first else None,
        "last_out": last_out,
        "total": total,
        "location": location,
        "in_photo": first["punch_in_photo"] if first else None,
        "out_photo": out_photo_session["punch_out_photo"] if out_photo_session else None,
    }


async def get_monthly_attendance(staff_id: str, org_id: str, month: int, year: int, detail: Optional[str] = None):
    """Monthly attendance of one staff member.

    Default (v1) shape: only dates that have an attendance row, status present / late (lateness in IST against the
    shift, grace from the attendance rule), plus additive per-day shift times / late_by_minutes / session_count.
    `detail=v2` (D-5) returns a plain dict with a row for EVERY day of the month and the server day types."""
    version = (detail or "v1").strip().lower()
    if version not in ("v1", "v2"):
        raise HTTPException(status_code=422, detail="detail must be 'v1' or 'v2'")
    if not 1 <= month <= 12 or not 1 <= year <= 9999:
        raise HTTPException(status_code=400, detail="month must be 1-12 and year a valid year")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT COALESCE(u.parent_id, u.org_id) AS org_id, b.branch_name, d.dept_name
            FROM users u
            LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = COALESCE(u.parent_id, u.org_id)
            LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = COALESCE(u.parent_id, u.org_id)
            WHERE u.user_id = %s
            """,
            (staff_id,),
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Staff not found")
        staff_org = user["org_id"]

        first_day, next_month = _month_bounds(year, month)
        now = get_utc_now()
        today = rules.ist_today(now)

        # The shift of the day: one lookup per month today; WP7 swaps `shift_for` for a per-date lookup.
        month_shift = shift_for(cursor, staff_id, staff_org, first_day)
        shift_of = lambda d: month_shift  # noqa: E731
        grace = org_grace_minutes(cursor, staff_org)

        cursor.execute(
            """
            SELECT a.attendance_date, a.punch_in, a.punch_out, a.work_hours,
                   a.punch_in_location[0] AS lng, a.punch_in_location[1] AS lat,
                   a.punchinface, a.punchoutface, a.auto_closed
            FROM attendance a
            WHERE a.user_id = %s AND a.attendance_date >= %s AND a.attendance_date < %s
            ORDER BY a.attendance_date ASC, a.punch_in ASC NULLS LAST, a.id ASC
            """,
            (staff_id, first_day, next_month),
        )
        records = cursor.fetchall()
        sessions_by_date = _session_rows_by_date(records)

        from ml.s3 import refresh_punch_image_url

        if version == "v1":
            return _monthly_v1(year, month, today, now, sessions_by_date, shift_of, grace, refresh_punch_image_url)

        cursor.execute(
            """
            SELECT ld.leave_date
            FROM leave_dates ld
            JOIN requests r ON r.request_id = ld.request_id
            WHERE r.user_id = %s AND r.org_id = %s AND r.request_type = 'leave'
              AND ld.leave_date >= %s AND ld.leave_date < %s
              AND COALESCE(ld.status, r.status, 'pending') = 'approved'
            """,
            (staff_id, staff_org, first_day, next_month),
        )
        leave_dates = [r["leave_date"] for r in cursor.fetchall()]

        cursor.execute(
            """
            SELECT h.date AS holiday_date, h.name AS holiday_name
            FROM org_holidays h
            WHERE h.org_id = %s AND h.date >= %s AND h.date < %s
              AND (h.branch_name IS NULL OR h.branch_name = %s)
              AND (h.dept_name IS NULL OR h.dept_name = %s)
            ORDER BY h.date ASC, h.id ASC
            """,
            (staff_org, first_day, next_month, user["branch_name"], user["dept_name"]),
        )
        holidays = {}
        for r in cursor.fetchall():
            holidays.setdefault(r["holiday_date"], r["holiday_name"])

        def _read_off_days():
            cursor.execute("SELECT off_days FROM org_off_days WHERE org_id = %s", (staff_org,))
            row = cursor.fetchone()
            return list(row["off_days"]) if row and row.get("off_days") is not None else [6]
        off_weekdays = _guarded(cursor, "sp_att_offdays", _read_off_days, [6])

        return _monthly_v2(year, month, today, now, sessions_by_date, shift_of, grace, leave_dates, holidays,
                           off_weekdays, refresh_punch_image_url)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("monthly attendance failed for %s: %s", staff_id, e)
        raise HTTPException(status_code=500, detail="Failed to get monthly attendance")
    finally:
        cursor.close()
        return_connection(conn)


def _monthly_v1(year, month, today, now, sessions_by_date, shift_of, grace, refresh_url):
    """Legacy shape: rows only for dates with attendance rows, present / late (IST), absent = days - rows."""
    import calendar
    attendance_data = []
    present_count = 0
    late_count = 0
    for d in sorted(sessions_by_date):
        sessions = sessions_by_date[d]
        fig = _day_figures(sessions, now)
        shift = shift_of(d)
        start = shift["start_time"] if shift else None
        end = shift["end_time"] if shift else None
        late_by = 0
        if fig["first_in"]:
            late_by = rules.late_by_minutes(rules.ist_time_of(fig["first_in"]), start, end, grace)
            status = 'late' if late_by > 0 else 'present'
            present_count += 1
            if late_by > 0:
                late_count += 1
        else:
            status = 'absent'
        attendance_data.append(AttendanceDay(
            date=str(d),
            status=status,
            punch_in=_fmt_ist(fig["first_in"]),
            punch_out=_fmt_ist(fig["last_out"]),
            total_hours=f"{fig['total']:.2f}" if fig["total"] > 0 else None,
            location=fig["location"],
            distance=None,
            punch_in_photo=refresh_url(fig["in_photo"]),
            punch_out_photo=refresh_url(fig["out_photo"]),
            shift_start_time=rules.hhmm(start),
            shift_end_time=rules.hhmm(end),
            late_by_minutes=late_by,
            session_count=sum(1 for s in sessions if s["punch_in"] is not None),
        ))
    total_days_in_month = calendar.monthrange(year, month)[1]
    return MonthlyAttendance(
        month=month,
        year=year,
        total_days=total_days_in_month,
        present_days=present_count,
        absent_days=total_days_in_month - len(sessions_by_date),
        late_days=late_count,
        attendance_data=attendance_data,
    )


def _monthly_v2(year, month, today, now, sessions_by_date, shift_of, grace, leave_dates, holidays, off_weekdays,
                refresh_url):
    """`detail=v2`: a row for every day with the day type, shift times, sessions and hours (D-5)."""
    days = rules.build_month(year, month, today, now, sessions_by_date, shift_of, grace,
                             leave_dates=leave_dates, holidays=holidays, off_weekdays=off_weekdays)
    rows = []
    for st in days:
        fig = _day_figures(st["sessions"], now)
        row = {
            "date": str(st["date"]),
            "type": st["type"],
            "status": st["status"],
            "is_holiday": st["is_holiday"],
            "is_weekly_off": st["is_weekly_off"],
            "punch_in": _fmt_ist(fig["first_in"]),
            "punch_out": _fmt_ist(fig["last_out"]),
            "total_hours": f"{fig['total']:.2f}" if st["session_count"] else None,
            "location": fig["location"],
            "punch_in_photo": refresh_url(fig["in_photo"]),
            "punch_out_photo": refresh_url(fig["out_photo"]),
            "shift_start_time": rules.hhmm(st["shift_start"]),
            "shift_end_time": rules.hhmm(st["shift_end"]),
            "late_by_minutes": st["late_by_minutes"],
            "session_count": st["session_count"],
            "sessions": [
                {
                    "punch_in": _fmt_ist(s["punch_in"], "%I:%M:%S %p"),
                    "punch_out": _fmt_ist(s["punch_out"], "%I:%M:%S %p"),
                    "work_hours": rules.session_hours(s["punch_in"], s["punch_out"], s["work_hours"], now),
                    "status": rules.session_status(s["punch_out"], s["auto_closed"]),
                    "auto_closed": s["auto_closed"],
                    "punch_in_photo": refresh_url(s["punch_in_photo"]),
                    "punch_out_photo": refresh_url(s["punch_out_photo"]),
                }
                for s in st["sessions"] if s["punch_in"] is not None
            ],
        }
        if st["is_holiday"]:
            row["holiday_name"] = st["holiday_name"]
        rows.append(row)
    summary = rules.summarize_days(days, len(days))
    return {
        "success": True,
        "month": month,
        "year": year,
        "as_of": str(today),
        **summary,
        "attendance_data": rows,
    }


# --------------------------------------------------------------------------- admin edit
def _parse_time(time_str: str, base_date: date):
    """Parse HH:MM (24h) or HH:MM AM/PM (12h) into a timezone-aware UTC datetime."""
    from utils.timezone import IST, convert_ist_to_utc
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
        try:
            t = datetime.strptime(time_str.strip(), fmt).time()
            # Input is IST (admin enters local time) — combine with base_date in IST, store as UTC
            ist_dt = datetime.combine(base_date, t).replace(tzinfo=IST)
            return convert_ist_to_utc(ist_dt)
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail=f"Invalid time format: '{time_str}'. Use HH:MM (24h) or HH:MM AM/PM")

def _org_session_limit(cursor, org_id) -> int:
    """Daily punch-session limit of an organisation (attendance_rule.max_sessions_per_day, editable in company settings);
    rules.MAX_SESSIONS_PER_DAY when the organisation has no rule row."""
    cursor.execute("SELECT max_sessions_per_day FROM attendance_rule WHERE org_id = %s", (org_id,))
    row = cursor.fetchone()
    value = row.get("max_sessions_per_day") if hasattr(row, "get") else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return rules.MAX_SESSIONS_PER_DAY


async def edit_attendance(data: EditAttendanceRequest):
    """Create or edit punch_in / punch_out of ONE session of a day (admin use).
    `session` 1 (default) = the earliest session of the day, 2 = the second, ... up to the organisation's daily limit.
    A day without a row gets one created (this is the main "regularise a missed day" path). Sessions are never merged:
    a day with several sessions keeps all of them."""
    session_no = 1 if data.session is None else data.session
    if isinstance(session_no, bool) or not isinstance(session_no, int) or session_no < 1:
        raise HTTPException(status_code=422, detail="session must be a whole number from 1")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        try:
            parsed_date = datetime.strptime(data.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD")

        org_id = data.org_id
        if not org_id:
            cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (data.staff_id,))
            user_row = cursor.fetchone()
            org_id = user_row['parent_id'] if user_row else None
        if not org_id:
            raise HTTPException(status_code=400, detail="Unable to resolve organization for this staff member")
        limit = _org_session_limit(cursor, org_id)
        if session_no > limit:
            raise HTTPException(status_code=422, detail=f"session must be between 1 and {limit} (this organization's daily limit)")

        cursor.execute(
            """SELECT id, punch_in, punch_out FROM attendance
               WHERE user_id = %s AND attendance_date = %s
               ORDER BY punch_in ASC NULLS LAST, id ASC""",
            (data.staff_id, parsed_date)
        )
        day_rows = cursor.fetchall() or []
        if session_no > len(day_rows) + 1:
            raise HTTPException(status_code=400, detail=f"Session {len(day_rows) + 1} of this day must exist before session {session_no} can be created")
        record = day_rows[session_no - 1] if session_no <= len(day_rows) else None

        if not record and not data.punch_in and not data.punch_out:
            raise HTTPException(status_code=400, detail="Please provide a punch in or punch out time")

        new_punch_in  = _parse_time(data.punch_in, parsed_date)  if data.punch_in  else (record['punch_in']  if record else None)
        new_punch_out = _parse_time(data.punch_out, parsed_date) if data.punch_out else (record['punch_out'] if record else None)

        # Overnight shift: the form only takes one date, so a punch_out time that
        # isn't after punch_in on that date must actually belong to the next
        # calendar day (e.g. an 8PM-8AM shift regularised as punch_in=20:00,
        # punch_out=08:00 on the same "date" would otherwise yield negative hours).
        if new_punch_in and new_punch_out and new_punch_out < new_punch_in:
            new_punch_out = new_punch_out + timedelta(days=1)

        work_hours = None
        if new_punch_in and new_punch_out:
            work_hours = round((new_punch_out - new_punch_in).total_seconds() / 3600, 2)

        now = get_utc_now()
        try:
            if record:
                # An admin supplying the punch-out of an auto-closed session is the review: clear the flag.
                cursor.execute(
                    """UPDATE attendance
                       SET punch_in = %s, punch_out = %s, work_hours = %s, updated_at = %s,
                           auto_closed = CASE WHEN %s THEN FALSE ELSE auto_closed END
                       WHERE id = %s""",
                    (new_punch_in, new_punch_out, work_hours, now, bool(data.punch_out), record['id'])
                )
            else:
                cursor.execute(
                    """INSERT INTO attendance (user_id, org_id, attendance_date, punch_in, punch_out, work_hours, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (data.staff_id, org_id, parsed_date, new_punch_in, new_punch_out, work_hours, now, now)
                )
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="This staff member already has another open session; close it first")
        conn.commit()
        return {
            "success": True,
            "message": "Attendance updated successfully" if record else "Attendance record created successfully",
            "work_hours": work_hours,
            "punch_in": format_ist_time(new_punch_in) if new_punch_in else None,
            "punch_out": format_ist_time(new_punch_out) if new_punch_out else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("attendance edit failed for %s: %s", data.staff_id, e)
        raise HTTPException(status_code=500, detail="Failed to update attendance")
    finally:
        cursor.close()
        return_connection(conn)
