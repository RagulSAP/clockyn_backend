import uuid
import logging
from datetime import datetime
from utils.timezone import get_utc_now, IST
from typing import Optional, List
from fastapi import HTTPException
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)


# ═══════════════════ MODELS ═══════════════════

class SendAnnouncementRequest(BaseModel):
    org_id: str
    sent_by_id: str
    title: str
    message: str
    announcement_type: str = "text"
    # target: 'all' | 'selected' | 'branch' | 'dept'
    target: str = "all"
    selected_user_ids: Optional[List[str]] = None
    branch_id: Optional[int] = None
    dept_id: Optional[str] = None


# ═══════════════════ SEND ANNOUNCEMENT ═══════════════════

async def send_announcement(req: SendAnnouncementRequest):
    conn = get_db_connection()
    try:
        # Validate admin
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT role_id FROM users WHERE user_id = %s", (req.sent_by_id,))
            user = cur.fetchone()
            if not user or user['role_id'] != 1:
                raise Exception("Only admin can send announcements")

        # Explicit recipients: only users of this organisation (staff by parent_id, the owner/admin rows by org_id) are
        # stored; checked before anything is written, so a list of foreign ids creates no announcement.
        valid_ids: List[str] = []
        if req.target == 'selected' and req.selected_user_ids:
            valid_ids = _org_member_ids(conn, req.org_id, req.selected_user_ids)
            if not valid_ids:
                raise HTTPException(status_code=400, detail="No valid recipients selected")

        announcement_id = str(uuid.uuid4())
        now = get_utc_now()

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO announcement
                    (announcement_id, announcement_type, title, message, org_id,
                     branch_id, dept_id, target, sent_by_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING announcement_id, title, message, org_id, target, created_at
            """, (
                announcement_id, req.announcement_type, req.title, req.message,
                req.org_id,
                req.branch_id if req.target in ('branch', 'dept') else None,
                req.dept_id if req.target in ('branch', 'dept') else None,
                req.target, req.sent_by_id, now, now
            ))
            conn.commit()
            result = dict(cur.fetchone())

            # Insert recipients for 'selected' target
            if valid_ids:
                for uid in valid_ids:
                    cur.execute("""
                        INSERT INTO announcement_recipients (announcement_id, user_id, created_at)
                        VALUES (%s, %s, %s)
                    """, (announcement_id, uid, now))
                conn.commit()
                result['recipient_count'] = len(valid_ids)
            else:
                result['recipient_count'] = 0

            # Resolve the recipients once (active users of this org only); the socket frame and the push go to them
            # role 2 & 3 have parent_id = org_id
            has_query = True
            if req.target == 'all':
                cur.execute("""
                    SELECT user_id FROM users
                    WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1
                """, (req.org_id,))
            elif valid_ids:
                cur.execute("""
                    SELECT user_id FROM users
                    WHERE user_id = ANY(%s) AND parent_id = %s AND status = 1
                """, (valid_ids, req.org_id))
            elif req.target == 'branch' and req.branch_id:
                cur.execute("""
                    SELECT user_id FROM users
                    WHERE parent_id = %s AND branch_id = %s AND role_id IN (2, 3) AND status = 1
                """, (req.org_id, req.branch_id))
            elif req.target == 'dept' and req.dept_id:
                cur.execute("""
                    SELECT user_id FROM users
                    WHERE parent_id = %s AND dept_id = %s AND role_id IN (2, 3) AND status = 1
                """, (req.org_id, req.dept_id))
            else:
                has_query = False
            recipient_ids = [r['user_id'] for r in cur.fetchall()] if has_query else []
            result['recipient_count'] = len(recipient_ids)

        import asyncio
        from services import realtime
        # Socket frame (`announcement`) + push to every device of each recipient; fire and forget.
        asyncio.ensure_future(realtime.deliver_announcement(
            recipient_ids, announcement_id, req.title, req.message[:100], req.target, req.org_id,
            push_title=req.title,
            push_data={
                'type': 'announcement',
                'announcement_id': announcement_id,
                'org_id': req.org_id,
                'target': req.target,
            }
        ))

        return {"success": True, "data": result}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Send announcement error: {e}")
        raise
    finally:
        return_connection(conn)


def _org_member_ids(conn, org_id: str, user_ids) -> List[str]:
    """The ids (de-duplicated, request order kept) that are active users of the organisation: staff (`parent_id`) and
    the owner/admin row (`org_id`). One query."""
    wanted = list(dict.fromkeys(u for u in user_ids if u))
    if not wanted:
        return []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT user_id FROM users
            WHERE user_id = ANY(%s) AND (org_id = %s OR parent_id = %s) AND status = 1
        """, (wanted, org_id, org_id))
        found = {r['user_id'] for r in cur.fetchall()}
    return [u for u in wanted if u in found]


# ═══════════════════ GET ANNOUNCEMENTS (ADMIN) ═══════════════════

async def get_announcements(org_id: str):
    """Admin: get all announcements for the org with sender name and recipient count."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    a.announcement_id AS id,
                    a.title,
                    a.message,
                    a.target,
                    a.branch_id,
                    a.dept_id,
                    a.created_at,
                    u.name AS sent_by,
                    b.branch_name,
                    d.dept_name,
                    CASE
                        WHEN a.target = 'selected' THEN (
                            SELECT COUNT(*) FROM announcement_recipients ar
                            WHERE ar.announcement_id = a.announcement_id
                        )
                        ELSE 0
                    END AS recipient_count
                FROM announcement a
                LEFT JOIN users u ON u.user_id = a.sent_by_id
                LEFT JOIN branch_info b ON b.branch_id = a.branch_id AND b.org_id = a.org_id
                LEFT JOIN dept_info d ON d.dept_id = a.dept_id AND d.org_id = a.org_id
                WHERE a.org_id = %s
                ORDER BY a.created_at DESC
            """, (org_id,))
            rows = cur.fetchall()
            return {"success": True, "data": rows}
    finally:
        return_connection(conn)


# ═══════════════════ DELETE ANNOUNCEMENT ═══════════════════

async def delete_announcement(announcement_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Delete recipients first
            cur.execute(
                "DELETE FROM announcement_recipients WHERE announcement_id = %s",
                (announcement_id,)
            )
            cur.execute(
                "DELETE FROM announcement WHERE announcement_id = %s RETURNING announcement_id",
                (announcement_id,)
            )
            deleted = cur.fetchone()
            conn.commit()
            if not deleted:
                return {"success": False, "message": "Announcement not found"}
            return {"success": True}
    except Exception as e:
        conn.rollback()
        logger.error(f"Delete announcement error: {e}")
        raise
    finally:
        return_connection(conn)


# ═══════════════════ GET ANNOUNCEMENTS FOR EMPLOYEE ═══════════════════

async def get_announcements_for_employee(user_id: str):
    """
    Returns all announcements visible to this employee:
    - target='all'  → org-wide (no branch/dept filter)
    - target='selected' → employee is in announcement_recipients
    - target='branch' → employee's branch_id matches
    - target='dept'  → employee's dept_id matches
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT parent_id, branch_id, dept_id FROM users WHERE user_id = %s",
                (user_id,)
            )
            user = cur.fetchone()
            if not user:
                return {"success": True, "data": []}

            org_id = user['parent_id']
            branch_id = user['branch_id']
            dept_id = user['dept_id']

            cur.execute("""
                SELECT
                    a.announcement_id AS id,
                    a.title,
                    a.message,
                    a.target,
                    a.created_at,
                    u.name AS sent_by,
                    b.branch_name,
                    d.dept_name
                FROM announcement a
                LEFT JOIN users u ON u.user_id = a.sent_by_id
                LEFT JOIN branch_info b ON b.branch_id = a.branch_id AND b.org_id = a.org_id
                LEFT JOIN dept_info d ON d.dept_id = a.dept_id AND d.org_id = a.org_id
                WHERE a.org_id = %s
                  AND (
                      a.target = 'all'
                      OR (a.target = 'branch' AND a.branch_id = %s)
                      OR (a.target = 'dept'   AND a.dept_id = %s)
                      OR (a.target = 'selected' AND EXISTS (
                          SELECT 1 FROM announcement_recipients ar
                          WHERE ar.announcement_id = a.announcement_id
                            AND ar.user_id = %s
                      ))
                  )
                ORDER BY a.created_at DESC
            """, (org_id, branch_id, dept_id, user_id))
            rows = cur.fetchall()
            return {"success": True, "data": rows}
    finally:
        return_connection(conn)
