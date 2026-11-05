import uuid
import asyncio
import logging
import os
from datetime import datetime
from typing import List, Optional
from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from core_functions import request_rules as rules
from core_functions import shift_resolution
from utils.timezone import get_utc_now, ist_today

logger = logging.getLogger(__name__)


def enforce_request_actor() -> bool:
    """`REQUESTS_ENFORCE_ACTOR` 1/true/yes/on: POST /requests/update-status answers 403 (or 404 for another
    organisation's request) when the acting user may not decide the request. Unset: follows REQUIRE_AUTH (off by default:
    the failure is only logged as `actor_check_failed` at WARNING and the decision goes through; read on every call)."""
    raw = os.getenv("REQUESTS_ENFORCE_ACTOR", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    # Not set: follow REQUIRE_AUTH. The guard cannot see which organisation a request_id belongs to, so with
    # authentication required this check is what stops an admin of one company deciding another company's request.
    from utils.auth_token import require_auth_enabled
    return require_auth_enabled()


def _fire_notification(push_token: str, title: str, body: str, data: dict = None):
    """Fire-and-forget push notification from sync context."""
    async def _send():
        try:
            from core_functions.auth_function import send_push_notification
            await send_push_notification(push_token, title, body, data or {})
        except Exception:
            pass
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # FastAPI async context — schedule as a task
            asyncio.ensure_future(_send())
        else:
            loop.run_until_complete(_send())
    except RuntimeError:
        # No event loop in this thread — create one
        asyncio.run(_send())
    except Exception:
        pass


def _fire_user_notification(user_id: str, title: str, body: str, data: dict = None):
    """Fire-and-forget push to every device of one user from sync context."""
    async def _send():
        try:
            from services import push_service
            await push_service.send_push_to_user(user_id, title, body, data or {})
        except Exception:
            pass
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_send())
        else:
            loop.run_until_complete(_send())
    except RuntimeError:
        asyncio.run(_send())
    except Exception:
        pass


def _get_org_admin_manager_tokens(org_id: str, exclude_user_id: str = None) -> list:
    """Push tokens (every device) of the admin (role_id=1) and managers (role_id=2) of an org."""
    from services import push_service
    user_ids = _get_org_admin_manager_user_ids(org_id, exclude_user_id)
    tokens = []
    for toks in push_service.get_users_tokens(user_ids).values():
        tokens.extend(t for t in toks if t not in tokens)
    return tokens


def _get_org_admin_manager_user_ids(org_id: str, exclude_user_id: str = None) -> list:
    """Get user_ids for admin and managers of an org — used for WebSocket badge push."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        query = """
            SELECT u.user_id FROM users u
            WHERE (
                (u.role_id = 1 AND EXISTS (
                    SELECT 1 FROM org_info o WHERE o.org_id = %s AND o.parent_id = u.user_id
                ))
                OR
                (u.role_id = 2 AND u.parent_id = %s)
              )
        """
        params = [org_id, org_id]
        if exclude_user_id:
            query += " AND u.user_id != %s"
            params.append(exclude_user_id)
        cursor.execute(query, params)
        return [r[0] for r in cursor.fetchall()]
    finally:
        cursor.close()
        return_connection(conn)


def _ws_notify_request(org_id: str, exclude_user_id: str = None, request_id: str = None, request_type: str = None):
    """Push request_submitted WebSocket event to all admin/managers of the org (chat socket + legacy badge socket)."""
    try:
        from services import realtime
        user_ids = _get_org_admin_manager_user_ids(org_id, exclude_user_id)
        async def _push():
            await realtime.publish_request_submitted(user_ids, request_id, request_type)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_push())
            else:
                loop.run_until_complete(_push())
        except RuntimeError:
            asyncio.run(_push())
    except Exception:
        pass


def _get_user_push_token(user_id: str) -> str:
    """One push token of a user (None = the user has no device registered); sends fan out to every device."""
    from services import push_service
    tokens = push_service.get_user_tokens(user_id)
    return tokens[0] if tokens else None


def _get_user_name(user_id: str) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT name FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 'Employee'
    finally:
        cursor.close()
        return_connection(conn)


# ---------------------------------------------------------------------------- shared submit helpers
_SUBMIT_LABELS = {
    'device_change': ('Device Change Request', 'device change'),
    'shift_change': ('Shift Change Request', 'shift change'),
    'leave': ('Leave Request', 'leave'),
    'permission': ('Permission Request', 'permission'),
}


def _notify_request_submitted(user_id: str, org_id: str, request_id: str, request_type: str) -> None:
    """After the commit (and after the WebSocket badge event the caller sends): one push per admin/manager device.
    Best effort: a failure here is logged and never turns an already saved request into an error."""
    title, label = _SUBMIT_LABELS[request_type]
    try:
        emp_name = _get_user_name(user_id)
        for token in _get_org_admin_manager_tokens(org_id, exclude_user_id=user_id):
            _fire_notification(token, title, f'{emp_name} submitted a {label} request.',
                               {'type': 'request_submitted', 'request_type': request_type, 'request_id': request_id})
    except Exception as e:
        logger.warning("request notification failed for %s: %s", request_id, e)


def _lock_user_requests(cursor, user_id: str) -> None:
    """Serialise concurrent submits of one user (duplicate / overlap checks) until the transaction ends."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("requests:" + str(user_id),))


def _load_requester(cursor, user_id: str, org_id: str) -> dict:
    """The requesting user, checked against the organisation. 404 `User not found`; 400 when the user does not
    belong to `org_id`. Returns {user_id, role_id, status, parent_id, org_id (users.org_id), branch_id, dept_id}."""
    cursor.execute(
        "SELECT user_id, role_id, status, parent_id, org_id FROM users WHERE user_id = %s", (user_id,)
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    user = {"user_id": row[0], "role_id": row[1], "status": row[2], "parent_id": row[3], "org_id": row[4]}
    member = org_id is not None and org_id in (user["parent_id"], user["org_id"])
    if not member and user["role_id"] == 1 and org_id:
        cursor.execute("SELECT 1 FROM org_info WHERE org_id = %s AND parent_id = %s LIMIT 1", (org_id, user_id))
        member = cursor.fetchone() is not None
    if not member:
        raise HTTPException(status_code=400, detail="User does not belong to this organization")
    return user


def _optional(cursor, name: str, fn, default):
    """Run `fn()` in a SAVEPOINT: an optional lookup that fails (older database) is logged and `default` is
    returned instead of aborting the surrounding transaction."""
    cursor.execute("SAVEPOINT " + name)
    try:
        result = fn()
    except Exception as e:
        logger.warning("optional lookup %s failed: %s", name, e)
        cursor.execute("ROLLBACK TO SAVEPOINT " + name)
        return default
    cursor.execute("RELEASE SAVEPOINT " + name)
    return result


# ---------------------------------------------------------------------------- submit
def submit_device_change_request(user_id: str, org_id: str, reason: str) -> dict:
    """Submit a device change request. One pending request per user: a second attempt returns the existing one
    (`already_pending: true`, no new row, no notification). At most 3 requests per user per rolling 24 hours:
    HTTP 429 beyond that."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        text = rules.clean_reason(reason, required=False) or rules.DEFAULT_DEVICE_REASON

        # If org_id not provided, look it up (managers/staff: their organisation; admin: the organisation they own)
        if not org_id:
            cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="User not found")
            if row[0] == 1:
                cursor.execute("SELECT org_id FROM org_info WHERE parent_id = %s LIMIT 1", (user_id,))
                org_row = cursor.fetchone()
                org_id = org_row[0] if org_row else None
            elif row[0] in (2, 3):
                org_id = row[1]
        if not org_id:
            return {"success": False, "message": "Could not determine organization"}

        _load_requester(cursor, user_id, org_id)
        _lock_user_requests(cursor, user_id)

        cursor.execute("""
            SELECT request_id FROM requests
            WHERE user_id = %s AND org_id = %s AND request_type = 'device_change' AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, org_id))
        pending = cursor.fetchone()
        if pending:
            conn.commit()
            return {"success": True, "request_id": pending[0], "already_pending": True,
                    "message": "A device change request is already pending"}

        cursor.execute("""
            SELECT COUNT(*) FROM requests
            WHERE user_id = %s AND request_type = 'device_change'
              AND created_at > now() - interval '24 hours'
        """, (user_id,))
        recent = cursor.fetchone()
        if rules.device_change_rate_limited(recent[0] if recent else 0):
            raise HTTPException(status_code=429, detail=rules.DEVICE_RATE_LIMIT_MESSAGE)

        request_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO requests (user_id, org_id, request_id, request_type, reason, status)
            VALUES (%s, %s, %s, 'device_change', %s, 'pending')
        """, (user_id, org_id, request_id, text))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)
    # Notify admin/managers about the new device change request
    _ws_notify_request(org_id, exclude_user_id=user_id, request_id=request_id, request_type='device_change')
    _notify_request_submitted(user_id, org_id, request_id, 'device_change')
    return {"success": True, "request_id": request_id, "already_pending": False,
            "message": "Device change request submitted successfully"}


def has_pending_device_change(user_id: str) -> bool:
    """True when the user has a pending device-change request (the `request_pending` flag of verify-device-token)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT 1 FROM requests
            WHERE user_id = %s AND request_type = 'device_change' AND status = 'pending' LIMIT 1
        """, (user_id,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()
        return_connection(conn)


def submit_shift_change_request(user_id: str, org_id: str, reason: str, requested_shift_id: int, shift_change_date: str) -> dict:
    """Submit a shift change request (a one-day override of the user's shift once approved)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _load_requester(cursor, user_id, org_id)
        shift_id, day, text = rules.validate_shift_change(reason, requested_shift_id, shift_change_date, ist_today())

        # The requested shift must be an active shift of this organisation
        cursor.execute("SELECT 1 FROM shift WHERE shift_id = %s AND org_id = %s AND status = 1", (shift_id, org_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Requested shift not found")

        # Fetch employee's current shift_id
        cursor.execute("""
            SELECT ua.shift_id FROM users_additional_info ua WHERE ua.user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        current_shift_id = row[0] if row else None
        if current_shift_id is not None and int(current_shift_id) == shift_id:
            raise HTTPException(status_code=409, detail=rules.SAME_SHIFT_MESSAGE)

        _lock_user_requests(cursor, user_id)
        cursor.execute("""
            SELECT 1 FROM requests
            WHERE user_id = %s AND org_id = %s AND request_type = 'shift_change'
              AND shift_change_date = %s AND status = 'pending' LIMIT 1
        """, (user_id, org_id, day))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail="A shift change request already exists for this date")

        request_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO requests (user_id, org_id, request_id, request_type, reason,
                                  requested_shift_id, current_shift_id, shift_change_date, status)
            VALUES (%s, %s, %s, 'shift_change', %s, %s, %s, %s, 'pending')
        """, (user_id, org_id, request_id, text, shift_id, current_shift_id, day))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)
    _ws_notify_request(org_id, exclude_user_id=user_id, request_id=request_id, request_type='shift_change')
    _notify_request_submitted(user_id, org_id, request_id, 'shift_change')
    return {"success": True, "request_id": request_id, "message": "Shift change request submitted successfully"}


def submit_leave_request(user_id: str, org_id: str, leave_dates: List[str], reason: str) -> dict:
    """Submit a leave request with multiple dates. Rules (D-17): at least one valid date, none in the past, a
    reason, and no date already covered by another pending / approved leave of the same user (409)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _load_requester(cursor, user_id, org_id)
        days, text = rules.validate_leave(leave_dates, reason, ist_today())

        _lock_user_requests(cursor, user_id)
        cursor.execute("""
            SELECT 1 FROM leave_dates ld
            JOIN requests r ON r.request_id = ld.request_id
            WHERE r.user_id = %s AND r.org_id = %s AND r.request_type = 'leave'
              AND ld.leave_date = ANY(%s) AND COALESCE(ld.status, 'pending') <> 'rejected'
            LIMIT 1
        """, (user_id, org_id, days))
        if cursor.fetchone():
            raise HTTPException(status_code=409, detail=rules.OVERLAP_MESSAGE)

        request_id = str(uuid.uuid4())
        # The request-level status follows the per-date decisions (aggregate_date_status): pending until decided
        cursor.execute("""
            INSERT INTO requests (user_id, org_id, request_id, request_type, reason, status)
            VALUES (%s, %s, %s, 'leave', %s, 'pending')
        """, (user_id, org_id, request_id, text))

        # Insert leave dates with pending status
        for leave_date in days:
            cursor.execute("""
                INSERT INTO leave_dates (request_id, leave_date, status)
                VALUES (%s, %s, 'pending')
            """, (request_id, leave_date))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)
    _ws_notify_request(org_id, exclude_user_id=user_id, request_id=request_id, request_type='leave')
    _notify_request_submitted(user_id, org_id, request_id, 'leave')
    return {"success": True, "request_id": request_id, "message": "Leave request submitted successfully"}


def submit_permission_request(user_id: str, org_id: str, permission_date: str, start_time: str, end_time: str, reason: str) -> dict:
    """Submit a permission request: one day, end later than start, at most 6 hours, not in the past."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _load_requester(cursor, user_id, org_id)
        day, start, end, text = rules.validate_permission(permission_date, start_time, end_time, reason, ist_today())

        request_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO requests (user_id, org_id, request_id, request_type, reason, permission_start_time, permission_end_time, status)
            VALUES (%s, %s, %s, 'permission', %s, %s, %s, 'pending')
        """, (user_id, org_id, request_id, text, start, end))

        # Store permission date in leave_dates table
        cursor.execute("""
            INSERT INTO leave_dates (request_id, leave_date, status)
            VALUES (%s, %s, 'pending')
        """, (request_id, day))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)
    _ws_notify_request(org_id, exclude_user_id=user_id, request_id=request_id, request_type='permission')
    _notify_request_submitted(user_id, org_id, request_id, 'permission')
    return {"success": True, "request_id": request_id, "message": "Permission request submitted successfully"}


# ---------------------------------------------------------------------------- lists
def aggregate_date_status(date_statuses) -> str:
    """Overall status of a leave / permission request from its per-date statuses. The vocabulary has no
    'partially approved' value: a request with any pending date is pending, one with at least one approved date
    (and none pending) is approved, otherwise rejected - identical to the app's deriveStatus()."""
    values = [(s or 'pending') for s in date_statuses]
    if any(v == 'pending' for v in values):
        return 'pending'
    if any(v == 'approved' for v in values):
        return 'approved'
    return 'rejected'


def _attach_decisions(cursor, rows: list) -> None:
    """Fill `dates` / `date_statuses` of every request row (one query for all of them) and derive the request-level
    status of leave / permission rows from their dates (a NULL status of an older leave row reads as its aggregate)."""
    ids = [r["request_id"] for r in rows if r.get("request_id")]
    by_request = {}
    if ids:
        cursor.execute("""
            SELECT request_id, leave_date, status, action_by, action_at
            FROM leave_dates WHERE request_id = ANY(%s) ORDER BY leave_date
        """, (ids,))
        for rid, leave_date, status, action_by, action_at in cursor.fetchall():
            by_request.setdefault(rid, []).append((leave_date, status, action_by, action_at))
    for row in rows:
        decisions = by_request.get(row["request_id"], [])
        for leave_date, status, action_by, action_at in decisions:
            date_str = leave_date.isoformat()
            row["dates"].append(date_str)
            row["date_statuses"][date_str] = {
                "status": status or 'pending',
                "action_by": action_by,
                "action_at": action_at.isoformat() if action_at else None
            }
        if row["request_type"] in ('leave', 'permission') and decisions:
            row["status"] = aggregate_date_status(s for _, s, _, _ in decisions)


def _attach_effective_shift(cursor, rows: list, org_id: str) -> None:
    """Shift-change rows: `effective_shift_id` / `effective_shift_timing` = the shift that applies on the request's
    date right now (the requested shift once approved, the assigned shift otherwise). Additive fields."""
    for row in rows:
        if row["request_type"] != 'shift_change' or not row.get("shift_change_date"):
            continue
        user_id = row.get("user_id")
        if not user_id:
            continue
        shift = _optional(
            cursor, "sp_effective_shift",
            lambda: shift_resolution.shift_for_cursor(cursor, user_id, org_id, row["shift_change_date"]),
            None,
        )
        row["effective_shift_id"] = shift["shift_id"] if shift else None
        row["effective_shift_timing"] = shift_resolution.format_shift_timing(shift)


def get_employee_requests(user_id: str, org_id: str) -> dict:
    """Get all requests for an employee with leave dates"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT r.request_id, r.request_type, r.reason, r.permission_start_time, r.permission_end_time,
                   r.status, r.action_by, r.action_at, r.created_at,
                   COALESCE(u.name, '') as action_by_name,
                   r.shift_change_date,
                   CASE WHEN rs.start_time IS NOT NULL THEN TO_CHAR(rs.start_time, 'HH:MI AM') || ' - ' || TO_CHAR(rs.end_time, 'HH:MI AM') ELSE NULL END as requested_shift_timing,
                   r.requested_shift_id, r.current_shift_id,
                   CASE WHEN cs.start_time IS NOT NULL THEN TO_CHAR(cs.start_time, 'HH:MI AM') || ' - ' || TO_CHAR(cs.end_time, 'HH:MI AM') ELSE NULL END as current_shift_timing
            FROM requests r
            LEFT JOIN users u ON r.action_by = u.user_id
            LEFT JOIN shift rs ON r.requested_shift_id = rs.shift_id AND rs.org_id = r.org_id
            LEFT JOIN shift cs ON r.current_shift_id = cs.shift_id AND cs.org_id = r.org_id
            WHERE r.user_id = %s AND r.org_id = %s
            ORDER BY r.created_at DESC
        """, (user_id, org_id))

        requests = []
        for row in cursor.fetchall():
            requests.append({
                "request_id": row[0],
                "request_type": row[1],
                "reason": row[2],
                "permission_start_time": str(row[3]) if row[3] else None,
                "permission_end_time": str(row[4]) if row[4] else None,
                "status": row[5],
                "action_by": row[6],
                "action_by_name": row[9],
                "action_at": row[7].isoformat() if row[7] else None,
                "created_at": row[8].isoformat() if row[8] else None,
                "shift_change_date": row[10].isoformat() if row[10] else None,
                "requested_shift_timing": row[11],
                "requested_shift_id": row[12],
                "current_shift_id": row[13],
                "current_shift_timing": row[14],
                "user_id": user_id,
                "dates": [],
                "date_statuses": {}
            })

        # Per-date decisions (same shape as the admin list) and the effective shift of shift-change dates
        _attach_decisions(cursor, requests)
        _attach_effective_shift(cursor, requests, org_id)
        return {"success": True, "data": requests}
    finally:
        cursor.close()
        return_connection(conn)


_PHOTO_LOOKUP_CAP = 50


def _profile_photos(user_ids: list) -> dict:
    """{user_id: presigned photo URL or None}, best effort, at most _PHOTO_LOOKUP_CAP storage lookups per call."""
    out = {}
    for uid in user_ids[:_PHOTO_LOOKUP_CAP]:
        try:
            from ml.s3 import get_employee_profile_url
            out[uid] = get_employee_profile_url(uid)
        except Exception:
            out[uid] = None
    return out


def get_admin_requests(org_id: str, status: Optional[str] = None) -> dict:
    """Get all requests for admin/manager to approve/reject with per-date status"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Resolve manager scope inline (sync)
        cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (org_id,))
        user_row = cursor.fetchone()
        if user_row and user_row[0] == 2:
            resolved_org_id = user_row[1]
            cursor.execute("SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s", (org_id,))
            asgn = cursor.fetchall()
            mgr_branch_ids = list({int(r[0]) for r in asgn if r[0] is not None}) or None
            mgr_dept_ids = list({r[1] for r in asgn if r[1] is not None}) or None
        else:
            resolved_org_id = org_id
            mgr_branch_ids = None
            mgr_dept_ids = None

        query = """
            SELECT r.request_id, r.request_type, r.reason, r.permission_start_time, r.permission_end_time,
                   r.status, r.action_by, r.action_at, r.created_at, r.user_id,
                   u.name as staff_name,
                   COALESCE(action_user.name, '') as action_by_name,
                   r.requested_shift_id, r.current_shift_id, r.shift_change_date,
                   CASE WHEN rs.start_time IS NOT NULL THEN TO_CHAR(rs.start_time, 'HH:MI AM') || ' - ' || TO_CHAR(rs.end_time, 'HH:MI AM') ELSE NULL END as requested_shift_timing,
                   CASE WHEN cs.start_time IS NOT NULL THEN TO_CHAR(cs.start_time, 'HH:MI AM') || ' - ' || TO_CHAR(cs.end_time, 'HH:MI AM') ELSE NULL END as current_shift_timing,
                   u.emp_id
            FROM requests r
            JOIN users u ON r.user_id = u.user_id
            LEFT JOIN users action_user ON r.action_by = action_user.user_id
            LEFT JOIN shift rs ON r.requested_shift_id = rs.shift_id AND rs.org_id = r.org_id
            LEFT JOIN shift cs ON r.current_shift_id = cs.shift_id AND cs.org_id = r.org_id
            WHERE r.org_id = %s
        """
        params = [resolved_org_id]

        if mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)
        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)

        if status:
            query += " AND r.status = %s"
            params.append(status)

        query += " ORDER BY r.created_at DESC"

        cursor.execute(query, params)

        requests = []
        for row in cursor.fetchall():
            requests.append({
                "request_id": row[0],
                "request_type": row[1],
                "reason": row[2],
                "permission_start_time": str(row[3]) if row[3] else None,
                "permission_end_time": str(row[4]) if row[4] else None,
                "status": row[5],
                "action_by": row[6],
                "action_at": row[7].isoformat() if row[7] else None,
                "created_at": row[8].isoformat() if row[8] else None,
                "user_id": row[9],
                "staff_name": row[10],
                "action_by_name": row[11],
                "requested_shift_id": row[12],
                "current_shift_id": row[13],
                "shift_change_date": row[14].isoformat() if row[14] else None,
                "requested_shift_timing": row[15],
                "current_shift_timing": row[16],
                "emp_id": row[17],
                "dates": [],
                "date_statuses": {}
            })

        # Per-date statuses, effective shift of shift-change dates, requester photos
        _attach_decisions(cursor, requests)
        _attach_effective_shift(cursor, requests, resolved_org_id)
        photos = _profile_photos(list(dict.fromkeys(r["user_id"] for r in requests)))
        for request_data in requests:
            request_data["profile_photo"] = photos.get(request_data["user_id"])

        return {"success": True, "data": requests}
    finally:
        cursor.close()
        return_connection(conn)


# ---------------------------------------------------------------------------- decisions
# Why: the one place that decides who may approve / reject (see rules.evaluate_actor for the matrix).
_ACTOR_PERMISSION = "Requests Management"


def _check_actor(cursor, action_by: str, request_org_id: str, requester_branch_id, requester_dept_id) -> Optional[str]:
    """None when `action_by` may decide a request of `request_org_id`, else the failure code (request_rules.FAIL_*)."""
    actor = None
    if action_by:
        cursor.execute("SELECT user_id, role_id, status, parent_id FROM users WHERE user_id = %s", (action_by,))
        row = cursor.fetchone()
        if row:
            actor_id, role_id, status, parent_id = row
            actor = {"role_id": role_id, "status": status, "in_request_org": False,
                     "has_permission": False, "branch_ids": [], "dept_ids": []}
            if role_id == 1:
                cursor.execute("SELECT 1 FROM org_info WHERE org_id = %s AND parent_id = %s LIMIT 1",
                               (request_org_id, actor_id))
                actor["in_request_org"] = cursor.fetchone() is not None
            elif role_id == 2 and parent_id == request_org_id:
                actor["in_request_org"] = True
                cursor.execute("""
                    SELECT 1 FROM punchapp_manager_permissions mp
                    JOIN punchapp_settings ps ON ps.setting_id = mp.setting_id
                    WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1 AND ps.setting_name = %s
                    LIMIT 1
                """, (actor_id, request_org_id, _ACTOR_PERMISSION))
                actor["has_permission"] = cursor.fetchone() is not None
                cursor.execute("SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s", (actor_id,))
                assignments = cursor.fetchall()
                actor["branch_ids"] = [a[0] for a in assignments if a[0] is not None]
                actor["dept_ids"] = [a[1] for a in assignments if a[1] is not None]
    return rules.evaluate_actor(actor, requester_branch_id, requester_dept_id)


def update_request_status(request_id: str, status: str, action_by: str, role_id: int, leave_date: str = None) -> dict:
    """Approve or reject a request or specific leave date.

    Leave / permission: `leave_date` decides that one date, no `leave_date` decides every date of the request;
    the request-level status is recomputed from the per-date decisions (aggregate_date_status).
    Shift change: approval is a one-day override of the user's shift (core_functions.shift_resolution).
    Device change: approval (first time only) clears the user's device token and push tokens.
    Who may decide: see _check_actor; log-only unless REQUESTS_ENFORCE_ACTOR is on."""
    if status not in ['approved', 'rejected', 'pending']:
        raise HTTPException(status_code=400, detail="Invalid status")
    day = rules.parse_iso_date(leave_date, "date") if leave_date else None

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Load the request (org, requester, requester's branch/department for the manager scope check)
        cursor.execute("""
            SELECT r.request_type, r.org_id, r.user_id, r.status, u.branch_id, u.dept_id
            FROM requests r
            LEFT JOIN users u ON u.user_id = r.user_id
            WHERE r.request_id = %s
        """, (request_id,))
        result = cursor.fetchone()
        if not result:
            return {"success": False, "message": "Request not found"}

        request_type, request_org_id, requester_id, previous_status = result[0], result[1], result[2], result[3]

        failure = _check_actor(cursor, action_by, request_org_id, result[4], result[5])
        if failure:
            # A request of ANOTHER organisation is never decided, whatever REQUESTS_ENFORCE_ACTOR says: the app never
            # sends one, and the answer is the plain "not found" of a missing request (nothing is disclosed).
            enforced = enforce_request_actor() or failure == rules.FAIL_OTHER_ORG
            logger.warning("actor_check_failed request_id=%s action_by=%s reason=%s enforced=%s",
                           request_id, action_by, failure, enforced)
            if enforced:
                conn.rollback()
                if failure == rules.FAIL_OTHER_ORG:
                    raise HTTPException(status_code=404, detail="Request not found")
                raise HTTPException(status_code=403, detail="Not allowed to decide this request")

        cleared_tokens = False
        has_dates = request_type in ('leave', 'permission')
        if day is not None and not has_dates:
            return {"success": False, "message": "Date not part of this request"}

        if has_dates:
            if day is not None:
                # Update specific date status in leave_dates table
                cursor.execute("""
                    UPDATE leave_dates
                    SET status = %s, action_by = %s, action_at = %s, updated_at = %s
                    WHERE request_id = %s AND leave_date = %s
                """, (status, action_by, get_utc_now(), get_utc_now(), request_id, day))
                if cursor.rowcount == 0:
                    conn.rollback()
                    return {"success": False, "message": "Date not part of this request"}
            else:
                # Update all dates for this request
                cursor.execute("""
                    UPDATE leave_dates
                    SET status = %s, action_by = %s, action_at = %s, updated_at = %s
                    WHERE request_id = %s
                """, (status, action_by, get_utc_now(), get_utc_now(), request_id))
                if cursor.rowcount == 0:
                    conn.rollback()
                    raise HTTPException(status_code=404, detail="Request has no dates")

            # Recompute the aggregate requests.status from the per-date decisions (same rule as the app: any
            # pending date -> pending, else any approved -> approved, else rejected).
            cursor.execute("""
                SELECT COALESCE(status, 'pending') FROM leave_dates WHERE request_id = %s
            """, (request_id,))
            date_statuses = [r[0] for r in cursor.fetchall()]
            aggregate = aggregate_date_status(date_statuses)
            cursor.execute("""
                UPDATE requests
                SET status = %s, action_by = %s, action_at = %s, updated_at = %s
                WHERE request_id = %s
            """, (aggregate, action_by, get_utc_now(), get_utc_now(), request_id))

        # For shift_change and device_change, directly update requests.status
        elif request_type in ('shift_change', 'device_change'):
            cursor.execute("""
                UPDATE requests
                SET status = %s, action_by = %s, action_at = %s, updated_at = %s
                WHERE request_id = %s
            """, (status, action_by, get_utc_now(), get_utc_now(), request_id))

            # First approval of a device_change: clear device_token and push_token so the old device session
            # expires and the user can sign in on the new device. A repeated approval must not clear the token
            # the user has just obtained on the new device. A rejection keeps the token.
            if request_type == 'device_change' and status == 'approved' and previous_status != 'approved':
                cursor.execute("""
                    UPDATE users SET device_token = NULL, device_token_expires_at = NULL, push_token = NULL
                    WHERE user_id = %s
                """, (requester_id,))
                cleared_tokens = True
                # the old device is signed out: revoke its bearer tokens too (single-device roles only; no-op otherwise)
                from core_functions import auth_dependency
                auth_dependency.bump_epoch(cursor, requester_id)
            else:
                cleared_tokens = False
        else:
            return {"success": False, "message": "Request not found"}

        conn.commit()
        # Notify the employee about the decision
        if requester_id:
            if request_type == 'device_change' and cleared_tokens:
                # The old device is signed out: forget every device token of the user (multi-device table)
                from services import push_service
                push_service.clear_user_tokens(requester_id)
                from core_functions import auth_dependency
                auth_dependency.forget_user(requester_id)  # the epoch bump is committed: drop the cached state now
            emp_token = _get_user_push_token(requester_id)
            if emp_token:
                status_label = status.capitalize()
                type_label = request_type.replace('_', ' ').title()
                _fire_user_notification(requester_id, f'{type_label} {status_label}',
                                        f'Your {type_label.lower()} has been {status}.',
                                        {'type': 'request_updated', 'request_type': request_type,
                                         'request_id': request_id, 'status': status})
        return {"success": True, "message": f"Request {status} successfully"}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)


def invalidate_other_device_sessions(user_id: str, new_device_token: str) -> None:
    """Called on new device login. Marks the old device as force-logged out
    by writing the new token to users table — old device's check_device_session
    will detect the mismatch and return force_logout=True immediately.
    This is a no-op if the token is already up to date.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET device_token = %s WHERE user_id = %s AND device_token IS DISTINCT FROM %s",
            (new_device_token, user_id, new_device_token)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def check_device_session(user_id: str, device_token: str) -> dict:
    """Check if the current device session is still valid.
    Returns force_logout=True if another device has logged in and taken over.
    The frontend should poll this periodically and log out immediately if force_logout is True.
    With ENFORCE_SINGLE_DEVICE on, admins (role 1) may be signed in on several devices: a different or
    missing device token never signs an admin out (an inactive or unknown account still does).
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT device_token, status, role_id FROM users WHERE user_id = %s",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row:
            return {"valid": False, "force_logout": True, "reason": "user_not_found"}

        stored_token, status, role_id = row[0], row[1], row[2]

        if status == 0:
            return {"valid": False, "force_logout": True, "reason": "account_inactive"}

        if role_id == 1 and rules.single_device_enforced():
            return {"valid": True, "force_logout": False}

        # device_token is NULL means admin cleared it (device change approved)
        if stored_token is None:
            return {"valid": False, "force_logout": True, "reason": "device_change_approved"}

        # Token mismatch means another device logged in
        if stored_token != device_token:
            return {"valid": False, "force_logout": True, "reason": "logged_in_on_another_device"}

        return {"valid": True, "force_logout": False}
    finally:
        cursor.close()
        return_connection(conn)


# ---------------------------------------------------------------------------- calendar
_HOLIDAYS_SQL = """
    SELECT date, name, block_attendance FROM org_holidays
    WHERE org_id = %s AND date BETWEEN %s AND %s
      AND (branch_name IS NULL OR branch_name = %s)
      AND (dept_name IS NULL OR dept_name = %s)
    ORDER BY date, name
"""


def get_employee_calendar_data(user_id: str, org_id: str, start_date: str, end_date: str,
                               include_off_days: bool = False) -> dict:
    """Calendar data for an employee: leave / permission dates (all statuses) in `data`, as before.

    Additive fields (D-17): `holidays` [{date, name, block_attendance}] that apply to the employee's branch and
    department, `weekly_offs` [date] and `off_days` [weekday numbers, 0 = Monday] of the organisation, and
    `shift_overrides` [{date, shift_id, shift_timing}] of approved one-day shift changes. They are separate keys
    because the app reads any other `type` inside `data` as a leave day; with `include_off_days=true` the same
    holidays and weekly offs are also appended to `data` as {date, status: 'approved', type: 'holiday' |
    'weekly_off', name?}."""
    first, last = rules.validate_calendar_range(start_date, end_date)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT ld.leave_date,
                   COALESCE(ld.status, r.status, 'pending') as status,
                   r.request_type
            FROM leave_dates ld
            JOIN requests r ON ld.request_id = r.request_id
            WHERE r.user_id = %s AND r.org_id = %s
              AND ld.leave_date BETWEEN %s AND %s
            ORDER BY ld.leave_date
        """, (user_id, org_id, first, last))
        calendar_data = [
            {"date": row[0].isoformat(), "status": row[1], "type": row[2]}
            for row in cursor.fetchall()
        ]

        def _holidays():
            cursor.execute("""
                SELECT b.branch_name, d.dept_name
                FROM users u
                LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %s
                LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = %s
                WHERE u.user_id = %s
            """, (org_id, org_id, user_id))
            names = cursor.fetchone()
            branch_name, dept_name = (names[0], names[1]) if names else (None, None)
            cursor.execute(_HOLIDAYS_SQL, (org_id, first, last, branch_name, dept_name))
            return [{"date": h[0].isoformat(), "name": h[1], "block_attendance": bool(h[2])}
                    for h in cursor.fetchall()]

        def _off_days():
            cursor.execute("SELECT off_days FROM org_off_days WHERE org_id = %s", (org_id,))
            row = cursor.fetchone()
            return sorted(int(d) for d in row[0]) if row and row[0] is not None else [6]

        holidays = _optional(cursor, "sp_cal_holidays", _holidays, [])
        off_days = _optional(cursor, "sp_cal_off_days", _off_days, [6])
        weekly_offs = rules.weekly_off_dates(first, last, off_days)
        overrides = _optional(
            cursor, "sp_cal_shift_overrides",
            lambda: shift_resolution.shift_overrides_in_range(cursor, user_id, org_id, first, last), {})
        shift_overrides = [
            {"date": day, "shift_id": s["shift_id"], "shift_timing": shift_resolution.format_shift_timing(s)}
            for day, s in sorted(overrides.items())
        ]

        if include_off_days:
            calendar_data = calendar_data + \
                [{"date": h["date"], "status": "approved", "type": "holiday", "name": h["name"]} for h in holidays] + \
                [{"date": d, "status": "approved", "type": "weekly_off"} for d in weekly_offs]
            calendar_data.sort(key=lambda item: item["date"])

        return {"success": True, "data": calendar_data, "holidays": holidays, "weekly_offs": weekly_offs,
                "off_days": off_days, "shift_overrides": shift_overrides}
    finally:
        cursor.close()
        return_connection(conn)
