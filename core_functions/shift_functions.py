from fastapi import HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
import uuid
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor


class ShiftCreate(BaseModel):
    org_id: str
    shift_name: str
    start_time: str
    end_time: str


def _parse_shift_time(time_str: str, field_name: str) -> str:
    """
    Parse a shift time string in any of the formats the two frontends send
    (24h "18:00", 12h zero-padded no-space "06:00PM", 12h with space "6:00 PM")
    and normalize it to an unambiguous 24-hour "HH:MM:SS" string before it ever
    reaches Postgres — instead of trusting a raw, differently-formatted string
    from each caller to `::time` cast correctly on its own.

    This mirrors the same multi-format parsing already used (and proven safe)
    for attendance edits in attendance_functions.py::_parse_time.
    """
    raw = (time_str or '').strip()
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time().strftime("%H:%M:%S")
        except ValueError:
            continue
    raise HTTPException(
        status_code=422,
        detail=f"Invalid {field_name}: '{time_str}'. Use HH:MM (24h) or HH:MM AM/PM."
    )


MAX_PLAUSIBLE_SHIFT_HOURS = 18


def _validate_shift_duration(start_time: str, end_time: str) -> None:
    """
    Reject shifts whose computed duration is implausibly long — the usual
    cause is an AM/PM or digit typo on start_time/end_time (e.g. a "Day
    shift" that works out to 21 hours). This only guards new creates/updates;
    it never re-checks or touches shifts already saved.
    """
    st = datetime.strptime(start_time, "%H:%M:%S").time()
    et = datetime.strptime(end_time, "%H:%M:%S").time()
    td = (timedelta(hours=et.hour, minutes=et.minute, seconds=et.second) -
          timedelta(hours=st.hour, minutes=st.minute, seconds=st.second))
    if td.total_seconds() < 0:
        td += timedelta(days=1)
    hours = td.total_seconds() / 3600
    if hours > MAX_PLAUSIBLE_SHIFT_HOURS:
        raise HTTPException(
            status_code=422,
            detail=(f"This shift works out to {hours:.1f} hours "
                    f"({start_time[:5]} to {end_time[:5]}). Please double-check "
                    f"the start/end time — especially AM/PM.")
        )


async def create_shift(shift_data: ShiftCreate):
    """Create a new shift"""
    start_time = _parse_shift_time(shift_data.start_time, "start_time")
    end_time = _parse_shift_time(shift_data.end_time, "end_time")
    _validate_shift_duration(start_time, end_time)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Get next shift_id globally (not per org)
        cursor.execute("""
            SELECT COALESCE(MAX(shift_id), 0) + 1 as next_id
            FROM shift
        """)
        shift_id = cursor.fetchone()['next_id']

        cursor.execute("""
            INSERT INTO shift (shift_id, org_id, shift_name, start_time, end_time, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s::time, %s::time, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (shift_id, shift_data.org_id, shift_data.shift_name, start_time, end_time))

        conn.commit()

        return {"success": True, "message": "Shift created successfully", "shift_id": shift_id}

    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to create shift: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_shift(shift_id: int):
    """Get a single shift by ID"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT shift_id, shift_name,
                   TO_CHAR(start_time, 'HH12:MIAM') as start_time,
                   TO_CHAR(end_time, 'HH12:MIAM') as end_time,
                   status
            FROM shift WHERE shift_id = %s
        """, (shift_id,))
        shift = cursor.fetchone()
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        return {"success": True, "data": shift}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get shift: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_shifts(org_id: str):
    """Get all shifts for an organization"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT shift_id, shift_name, 
                   TO_CHAR(start_time, 'HH12:MIAM') as start_time, 
                   TO_CHAR(end_time, 'HH12:MIAM') as end_time, 
                   status
            FROM shift
            WHERE org_id = %s AND status = 1
            ORDER BY shift_id
        """, (org_id,))
        shifts = cursor.fetchall()
        
        return {"success": True, "data": shifts}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get shifts: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def update_shift(shift_id: int, shift_data: ShiftCreate):
    """Update an existing shift"""
    start_time = _parse_shift_time(shift_data.start_time, "start_time")
    end_time = _parse_shift_time(shift_data.end_time, "end_time")
    _validate_shift_duration(start_time, end_time)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            UPDATE shift
            SET shift_name = %s, start_time = %s::time, end_time = %s::time, updated_at = CURRENT_TIMESTAMP
            WHERE shift_id = %s AND org_id = %s
        """, (shift_data.shift_name, start_time, end_time, shift_id, shift_data.org_id))

        conn.commit()
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Shift not found")
        
        return {"success": True, "message": "Shift updated successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update shift: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def delete_shift(shift_id: int, org_id: str, force: bool = False):
    """Delete a shift and optionally unassign users"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if shift exists
        cursor.execute(
            "SELECT shift_id FROM shift WHERE shift_id = %s AND org_id = %s",
            (shift_id, org_id)
        )
        shift = cursor.fetchone()
        
        if not shift:
            raise HTTPException(status_code=404, detail="Shift not found")
        
        # Check if shift is being used by any users
        cursor.execute(
            "SELECT COUNT(*) as count FROM users_additional_info WHERE shift_id = %s",
            (shift_id,)
        )
        result = cursor.fetchone()
        
        if result['count'] > 0:
            # Unassign users from this shift (set shift_id to NULL)
            cursor.execute(
                "UPDATE users_additional_info SET shift_id = NULL WHERE shift_id = %s",
                (shift_id,)
            )
        
        # Delete the shift
        cursor.execute(
            "DELETE FROM shift WHERE shift_id = %s AND org_id = %s",
            (shift_id, org_id)
        )
        
        conn.commit()
        
        message = "Shift deleted successfully"
        if result['count'] > 0:
            message = f"Shift deleted successfully. {result['count']} user(s) unassigned from this shift."
        
        return {"success": True, "message": message}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to delete shift: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


# ---------------------------------------------------------------------------
# Shift <-> staff assignment (users_additional_info.shift_id)
# ---------------------------------------------------------------------------

_STAFF_ROW_SQL = """
    SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id,
           (u.status = 1) AS is_active,
           COALESCE(d.dept_name, '') AS dept_name,
           COALESCE(b.branch_name, '') AS branch_name
    FROM users u
    {join} JOIN users_additional_info uai ON uai.user_id = u.user_id
    LEFT JOIN dept_info d   ON d.dept_id = u.dept_id     AND d.org_id = u.parent_id
    LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
    WHERE u.parent_id = %(org)s AND u.role_id IN (2, 3) AND u.status = 1
      AND {cond}
    ORDER BY u.name
"""


def _profile_photo_url(user_id: str):
    """Profile photo URL, or None when there is none or storage is unreachable."""
    try:
        from ml.s3 import get_employee_profile_url
        return get_employee_profile_url(user_id)
    except Exception:
        return None


def _require_shift(cursor, org_id: str, shift_id, active_only: bool = True):
    if org_id is None or not str(org_id).strip():
        raise HTTPException(status_code=400, detail="org_id is required")
    if shift_id is None or isinstance(shift_id, bool):
        raise HTTPException(status_code=400, detail="shift_id is required")
    cursor.execute(
        "SELECT shift_id FROM shift WHERE shift_id = %s AND org_id = %s" + (" AND status = 1" if active_only else ""),
        (shift_id, org_id),
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Shift not found")


def _staff_rows(org_id: str, shift_id: int, assigned: bool):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _require_shift(cursor, org_id, shift_id)
        if assigned:
            sql = _STAFF_ROW_SQL.format(join="INNER", cond="uai.shift_id = %(shift)s")
        else:
            sql = _STAFF_ROW_SQL.format(join="LEFT", cond="uai.shift_id IS DISTINCT FROM %(shift)s")
        cursor.execute(sql, {"org": org_id, "shift": shift_id})
        rows = []
        for r in cursor.fetchall():
            r = dict(r)
            r["profile_photo_url"] = _profile_photo_url(r["user_id"])
            rows.append(r)
        return rows
    finally:
        cursor.close()
        return_connection(conn)


def get_assigned_staff(org_id: str, shift_id: int):
    """Active role 2/3 staff currently on the shift."""
    return {"success": True, "data": _staff_rows(org_id, shift_id, True)}


def get_unassigned_staff(org_id: str, shift_id: int):
    """Active role 2/3 staff whose shift is NULL, 0 or another shift."""
    return {"success": True, "data": _staff_rows(org_id, shift_id, False)}


def assign_staff(org_id: str, shift_id: int, user_ids: list):
    """Put staff on a shift (idempotent). Staff without an additional-info row get one."""
    if not isinstance(user_ids, list) or not user_ids or not all(isinstance(u, str) and u for u in user_ids):
        raise HTTPException(status_code=400, detail="Select at least one employee")
    user_ids = list(dict.fromkeys(user_ids))
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _require_shift(cursor, org_id, shift_id)
        cursor.execute(
            "SELECT user_id FROM users WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1 AND user_id = ANY(%s)",
            (org_id, user_ids),
        )
        valid = {r["user_id"] for r in cursor.fetchall()}
        for uid in user_ids:
            if uid not in valid:
                raise HTTPException(status_code=404, detail=f"Staff not found: {uid}")
        cursor.execute(
            "UPDATE users_additional_info SET shift_id = %s, updated_at = now() WHERE user_id = ANY(%s)",
            (shift_id, user_ids),
        )
        cursor.execute("SELECT DISTINCT user_id FROM users_additional_info WHERE user_id = ANY(%s)", (user_ids,))
        have_row = {r["user_id"] for r in cursor.fetchall()}
        for uid in user_ids:
            if uid not in have_row:
                cursor.execute(
                    """INSERT INTO users_additional_info
                       (user_id, acc_no, ifsc_code, bank_name, bank_branch, gender, address, emergency_contact_no, shift_id)
                       VALUES (%s, '', '', '', '', '', '', '', %s)""",
                    (uid, shift_id),
                )
        conn.commit()
        return {"success": True, "message": "Staff assigned successfully", "assigned": len(user_ids)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to assign staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


def unassign_staff(org_id: str, user_id: str, shift_id: int):
    """Take a staff member off a shift. A no-op when they are on a different shift."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _require_shift(cursor, org_id, shift_id, active_only=False)
        cursor.execute("SELECT 1 FROM users WHERE user_id = %s AND parent_id = %s", (user_id, org_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Staff not found")
        cursor.execute(
            "UPDATE users_additional_info SET shift_id = NULL, updated_at = now() WHERE user_id = %s AND shift_id = %s",
            (user_id, shift_id),
        )
        conn.commit()
        return {"success": True, "message": "Staff unassigned successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to unassign staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)
