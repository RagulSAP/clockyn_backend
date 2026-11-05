import uuid
import logging
from datetime import datetime, timezone
from utils.timezone import get_utc_now
from typing import Optional, List
from fastapi import HTTPException
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

# ═══════════════════ MODELS ═══════════════════

class SendMessageRequest(BaseModel):
    sender_id: str
    receiver_id: str
    message: str
    message_type: str = "text"

class SendGroupMessageRequest(BaseModel):
    group_id: str
    sender_id: str
    message: str
    message_type: str = "text"

class CreateGroupRequest(BaseModel):
    group_name: str
    created_by: str
    member_ids: List[str]
    org_id: str

class SendAnnouncementRequest(BaseModel):
    sender_id: str
    org_id: str
    message: str
    announcement_type: str = "text"
    branch_id: Optional[int] = None
    dept_id: Optional[str] = None

class MarkReadRequest(BaseModel):
    sender_id: str
    receiver_id: str

class MarkGroupReadRequest(BaseModel):
    group_id: str
    user_id: str


class AddGroupMembersRequest(BaseModel):
    group_id: str
    member_ids: List[str]


_ALLOWED_MESSAGE_TYPES = {"text", "image", "file"}


def _normalize_message_type(message_type: Optional[str]) -> str:
    value = str(message_type or "text").strip().lower()
    return value if value in _ALLOWED_MESSAGE_TYPES else "text"


# ═══════════════════ RESOURCE SCOPING (organisation / membership) ═══════════════════
# The central auth guard only compares user_id style keys; the ids below name ONE resource (a group, a chat partner,
# the members of a group), so the handlers check them. A foreign id answers the plain 404 of a missing resource.

def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def _orgs_of_users(cur, user_ids) -> dict:
    """user_id -> set of organisation ids the user belongs to (admin: users.org_id plus every org_info row it owns;
    manager / staff: users.parent_id). Unknown users are absent from the result."""
    ids = [i for i in dict.fromkeys(user_ids) if i]
    if not ids:
        return {}
    cur.execute("SELECT user_id, role_id, org_id, parent_id FROM users WHERE user_id = ANY(%s)", (ids,))
    rows = cur.fetchall() or []
    out, admins = {}, []
    for row in rows:
        if row['role_id'] == 1:
            out[row['user_id']] = {row['org_id']} if row['org_id'] else set()
            admins.append(row['user_id'])
        else:
            out[row['user_id']] = {row['parent_id']} if row['parent_id'] else set()
    if admins:
        cur.execute("SELECT parent_id, org_id FROM org_info WHERE parent_id = ANY(%s)", (admins,))
        for row in cur.fetchall() or []:
            if row['org_id']:
                out[row['parent_id']].add(row['org_id'])
    return out


def assert_same_org_users(cur, first_id: str, second_id: str) -> None:
    """404 unless both users exist and share an organisation (1:1 chat: the receiver must be a colleague)."""
    orgs = _orgs_of_users(cur, [first_id, second_id])
    if first_id not in orgs or second_id not in orgs or not (orgs[first_id] & orgs[second_id]):
        raise _not_found("User not found")


def check_chat_peers(sender_id: str, receiver_id: str) -> None:
    """Same check for callers that have no cursor open yet (the multipart send-file route, before the upload)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_same_org_users(cur, sender_id, receiver_id)
    finally:
        return_connection(conn)


def users_in_org(cur, user_ids, org_id: Optional[str]) -> set:
    """The ids of `user_ids` that are users of organisation `org_id` (its admin, or a manager / staff of it)."""
    ids = [i for i in dict.fromkeys(user_ids) if i]
    if not ids or not org_id:
        return set()
    cur.execute("""
        SELECT user_id FROM users
        WHERE user_id = ANY(%s)
          AND (org_id = %s OR parent_id = %s
               OR user_id IN (SELECT parent_id FROM org_info WHERE org_id = %s))
    """, (ids, org_id, org_id, org_id))
    return {r['user_id'] for r in (cur.fetchall() or [])}


def is_group_participant(cur, group_id: str, user_id: str) -> bool:
    """True when `user_id` is an active member of the group or the user who created it."""
    cur.execute("""
        SELECT 1 FROM "group" g
        WHERE g.group_id = %s
          AND (g.user_id = %s
               OR EXISTS (SELECT 1 FROM group_members gm
                          WHERE gm.group_id = g.group_id AND gm.member_id = %s AND gm.status = 1))
        LIMIT 1
    """, (group_id, user_id, user_id))
    return cur.fetchone() is not None


def assert_group_participant(cur, group_id: str, user_id: str) -> None:
    if not is_group_participant(cur, group_id, user_id):
        raise _not_found("Group not found")


def assert_group_access(group_id: str, user_id: str) -> None:
    """`assert_group_participant` on its own connection (routes that have no cursor)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_group_participant(cur, group_id, user_id)
    finally:
        return_connection(conn)


def _format_utc_iso(dt: datetime) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _normalize_message_row(row: dict) -> dict:
    data = dict(row)
    if data.get("message_id") is not None:
        data["message_id"] = str(data["message_id"])
    if data.get("timestamp") is not None:
        data["timestamp"] = _format_utc_iso(data["timestamp"])
    data["message_type"] = _normalize_message_type(data.get("message_type"))
    return data


# ═══════════════════ 1. INDIVIDUAL CHAT ═══════════════════

async def get_chat_list(user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get caller's role and resolve their org_id
            cur.execute("SELECT role_id, org_id, parent_id FROM users WHERE user_id = %s", (user_id,))
            caller = cur.fetchone()
            if not caller:
                return []
            # role_id=1: org_id is their own org_id column (set on registration)
            # role_id=2,3: org_id column is NULL, their org is parent_id
            if caller['role_id'] == 1:
                org_id = caller['org_id']
            else:
                org_id = caller['parent_id']

            cur.execute("""
                WITH msg_counts AS (
                    SELECT
                        CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END AS other_user_id,
                        COUNT(*) AS msg_count,
                        MAX(date_time) AS last_message_time
                    FROM chat
                    WHERE (sender_id = %s OR receiver_id = %s)
                      AND group_id IS NULL
                    GROUP BY CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END
                ),
                last_msgs AS (
                    SELECT DISTINCT ON (
                        CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END
                    )
                        CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END AS other_user_id,
                        message AS last_message,
                        message_type AS last_message_type
                    FROM chat
                    WHERE (sender_id = %s OR receiver_id = %s)
                      AND group_id IS NULL
                    ORDER BY
                        CASE WHEN sender_id = %s THEN receiver_id ELSE sender_id END,
                        date_time DESC
                ),
                unread AS (
                    SELECT sender_id AS other_user_id, COUNT(*) AS unread_count
                    FROM chat c
                    WHERE c.receiver_id = %s AND c.group_id IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM chat_activity ca
                          WHERE ca.chat_id = c.chat_id
                            AND ca.receiver_id = %s
                            AND ca.seen_at IS NOT NULL
                      )
                    GROUP BY sender_id
                )
                SELECT
                    u.user_id, u.name,
                    r.role_name, d.dept_name, b.branch_name,
                    lm.last_message,
                    lm.last_message_type,
                    mc.last_message_time,
                    COALESCE(mc.msg_count, 0) AS msg_count,
                    COALESCE(ur.unread_count, 0) AS unread_count,
                    false AS is_online
                FROM users u
                LEFT JOIN role_info r ON r.role_id = u.role_id
                LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = u.parent_id
                LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
                LEFT JOIN msg_counts mc ON mc.other_user_id = u.user_id
                LEFT JOIN last_msgs lm ON lm.other_user_id = u.user_id
                LEFT JOIN unread ur ON ur.other_user_id = u.user_id
                WHERE u.user_id != %s
                  AND u.status = 1
                  AND (
                      (u.role_id = 1 AND EXISTS (
                          SELECT 1 FROM org_info o WHERE o.org_id = %s AND o.parent_id = u.user_id
                      ))
                      OR
                      (u.role_id IN (2, 3) AND u.parent_id = %s)
                  )
                ORDER BY mc.last_message_time DESC NULLS LAST, u.name ASC
            """, (
                user_id, user_id, user_id, user_id,
                user_id, user_id, user_id, user_id, user_id,
                user_id, user_id,
                user_id, org_id, org_id
            ))
            rows = cur.fetchall()
            from ml.s3 import get_employee_profile_url
            result = []
            for row in rows:
                r = dict(row)
                r['profile_photo_url'] = get_employee_profile_url(r['user_id'])
                result.append(r)
            return result
    finally:
        return_connection(conn)


async def search_users_for_chat(user_id: str, query: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT role_id, org_id, parent_id FROM users WHERE user_id = %s", (user_id,))
            caller = cur.fetchone()
            if not caller:
                return []
            org_id = caller['org_id'] if caller['role_id'] == 1 else caller['parent_id']
            cur.execute("""
                SELECT u.user_id, u.name, r.role_name, d.dept_name, b.branch_name
                FROM users u
                LEFT JOIN role_info r ON r.role_id = u.role_id
                LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = u.parent_id
                LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
                WHERE u.user_id != %s
                  AND u.status = 1
                  AND u.name ILIKE %s
                  AND (
                      (u.role_id = 1 AND EXISTS (
                          SELECT 1 FROM org_info o WHERE o.org_id = %s AND o.parent_id = u.user_id
                      ))
                      OR
                      (u.role_id IN (2, 3) AND u.parent_id = %s)
                  )
                ORDER BY u.name ASC
            """, (user_id, f"%{query}%", org_id, org_id))
            rows = cur.fetchall()
            from ml.s3 import get_employee_profile_url
            result = []
            for row in rows:
                r = dict(row)
                r['profile_photo_url'] = get_employee_profile_url(r['user_id'])
                result.append(r)
            return result
    finally:
        return_connection(conn)


async def get_chat_messages(user_id: str, other_user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT c.chat_id AS message_id, c.sender_id, c.receiver_id,
                       c.message, c.message_type, c.file_url, c.date_time AS timestamp,
                       true AS is_delivered,
                       CASE
                           WHEN c.sender_id = %s THEN
                               EXISTS (
                                   SELECT 1 FROM chat_activity ca
                                   WHERE ca.chat_id = c.chat_id
                                     AND ca.receiver_id = %s
                                     AND ca.seen_at IS NOT NULL
                               )
                           ELSE true
                       END AS is_read
                FROM chat c
                WHERE c.group_id IS NULL
                  AND ((c.sender_id = %s AND c.receiver_id = %s)
                    OR (c.sender_id = %s AND c.receiver_id = %s))
                ORDER BY c.date_time ASC
            """, (user_id, other_user_id, user_id, other_user_id, other_user_id, user_id))
            rows = cur.fetchall()
            from ml.s3 import get_chat_file_presigned_url
            result = []
            for row in rows:
                r = dict(row)
                if r.get('file_url') and r['message_type'] in ('file', 'image'):
                    fresh_url = get_chat_file_presigned_url(r['file_url'])
                    r['file_url'] = fresh_url or r.get('message') or r['file_url']
                    # Detect type from the S3 key/path — strip query string first
                    resolved = r['file_url'] or ''
                    ext = (resolved.split('?')[0].rstrip('/').split('.')[-1] or '').lower()
                    image_exts = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'heic'}
                    r['message_type'] = 'image' if ext in image_exts else 'file'
                else:
                    r['message_type'] = _normalize_message_type(r.get('message_type'))
                r['timestamp'] = _format_utc_iso(r.get('timestamp'))
                result.append(r)
            return result
    finally:
        return_connection(conn)


async def send_chat_message(req: SendMessageRequest):
    conn = get_db_connection()
    try:
        chat_id = str(uuid.uuid4())
        now = get_utc_now()
        message_type = _normalize_message_type(req.message_type)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_same_org_users(cur, req.sender_id, req.receiver_id)
            cur.execute("""
                INSERT INTO chat (chat_id, sender_id, receiver_id, message, message_type, date_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING chat_id AS message_id, sender_id, receiver_id, message, message_type, file_url, date_time AS timestamp
            """, (chat_id, req.sender_id, req.receiver_id, req.message, message_type, now))
            conn.commit()
            result = _normalize_message_row(cur.fetchone())
            cur.execute('SELECT name FROM users WHERE user_id = %s', (req.sender_id,))
            sender = cur.fetchone()
            result['sender_name'] = sender['name'] if sender else 'Someone'
            result['is_read'] = False
            result['is_delivered'] = False
            return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Send message error: {e}")
        raise
    finally:
        return_connection(conn)


async def mark_as_read(req: MarkReadRequest):
    """
    Individual chat: when receiver opens chat, mark all messages FROM sender as read.
    Inserts into chat_activity with chat_id + receiver_id (the person reading).
    """
    conn = get_db_connection()
    try:
        now = get_utc_now()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find all unread messages sent BY sender TO receiver
            cur.execute("""
                SELECT c.chat_id FROM chat c
                WHERE c.sender_id = %s
                  AND c.receiver_id = %s
                  AND c.group_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM chat_activity ca
                      WHERE ca.chat_id = c.chat_id
                        AND ca.receiver_id = %s
                        AND ca.seen_at IS NOT NULL
                  )
            """, (req.sender_id, req.receiver_id, req.receiver_id))
            rows = cur.fetchall()

            for row in rows:
                cur.execute("""
                    INSERT INTO chat_activity (chat_id, receiver_id, seen_at)
                    VALUES (%s, %s, %s)
                """, (row['chat_id'], req.receiver_id, now))

            conn.commit()
        return {"success": True, "marked_count": len(rows)}
    except Exception as e:
        conn.rollback()
        logger.error(f"Mark read error: {e}")
        raise
    finally:
        return_connection(conn)


# ═══════════════════ 2. GROUP CHAT ═══════════════════

async def get_group_chat_list(user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                WITH user_groups AS (
                    SELECT g.group_id, g.group_name, g.user_id AS created_by
                    FROM "group" g
                    JOIN group_members gm ON gm.group_id = g.group_id
                    WHERE gm.member_id = %s AND g.status = 1 AND gm.status = 1
                ),
                member_counts AS (
                    SELECT group_id, COUNT(*) AS member_count
                    FROM group_members WHERE status = 1
                    GROUP BY group_id
                ),
                last_msgs AS (
                    SELECT DISTINCT ON (group_id)
                        group_id, message AS last_message, sender_id, date_time AS last_message_time
                    FROM chat
                    WHERE group_id IS NOT NULL
                    ORDER BY group_id, date_time DESC
                ),
                unread AS (
                    SELECT c.group_id, COUNT(*) AS unread_count
                    FROM chat c
                    WHERE c.group_id IS NOT NULL
                      AND c.sender_id != %s
                      AND NOT EXISTS (
                          SELECT 1 FROM chat_activity ca
                          WHERE ca.chat_id = c.chat_id
                            AND ca.group_id = c.group_id
                            AND ca.receiver_id = %s
                            AND ca.seen_at IS NOT NULL
                      )
                    GROUP BY c.group_id
                )
                SELECT
                    ug.group_id, ug.group_name, ug.created_by,
                    COALESCE(mc.member_count, 0) AS member_count,
                    lm.last_message,
                    u.name AS last_message_sender,
                    lm.last_message_time,
                    COALESCE(ur.unread_count, 0) AS unread_count
                FROM user_groups ug
                LEFT JOIN member_counts mc ON mc.group_id = ug.group_id
                LEFT JOIN last_msgs lm ON lm.group_id = ug.group_id
                LEFT JOIN users u ON u.user_id = lm.sender_id
                LEFT JOIN unread ur ON ur.group_id = ug.group_id
                ORDER BY lm.last_message_time DESC NULLS LAST
            """, (user_id, user_id, user_id))
            return cur.fetchall()
    finally:
        return_connection(conn)


async def create_group(req: CreateGroupRequest):
    conn = get_db_connection()
    try:
        group_id = str(uuid.uuid4())
        now = get_utc_now()
        member_ids = list(dict.fromkeys(req.member_ids))          # a repeated id would violate the unique member index
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # the creator and every member must be users of the group's organisation
            wanted = set(member_ids) | {req.created_by}
            if users_in_org(cur, wanted, req.org_id) != wanted:
                raise _not_found("User not found")
            cur.execute("""
                INSERT INTO "group" (group_id, user_id, org_id, group_name, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 1, %s, %s)
            """, (group_id, req.created_by, req.org_id, req.group_name, now, now))

            for mid in member_ids:
                cur.execute("""
                    INSERT INTO group_members (group_id, member_id, status, created_at, updated_at)
                    VALUES (%s, %s, 1, %s, %s)
                """, (group_id, mid, now, now))

            conn.commit()
            return {
                "group_id": group_id,
                "group_name": req.group_name,
                "created_by": req.created_by,
                "member_count": len(member_ids),
                "unread_count": 0
            }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Create group error: {e}")
        raise
    finally:
        return_connection(conn)


async def get_group_messages(group_id: str, user_id: str):
    """Get group messages with read count for sender's messages"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_group_participant(cur, group_id, user_id)      # only the group's members read its messages
            # Get total members (excluding sender for read count)
            cur.execute("""
                SELECT COUNT(*) AS total FROM group_members
                WHERE group_id = %s AND status = 1
            """, (group_id,))
            total_members = cur.fetchone()['total']

            cur.execute("""
                SELECT c.chat_id AS message_id, c.group_id, c.sender_id,
                       u.name AS sender_name, c.message, c.message_type, c.file_url, c.date_time AS timestamp,
                       CASE
                           WHEN c.sender_id = %s THEN (
                               SELECT COUNT(*) FROM chat_activity ca
                               WHERE ca.chat_id = c.chat_id
                                 AND ca.group_id = c.group_id
                                 AND ca.seen_at IS NOT NULL
                           )
                           ELSE 0
                       END AS read_count,
                       CASE
                           WHEN c.sender_id != %s THEN
                               EXISTS (
                                   SELECT 1 FROM chat_activity ca
                                   WHERE ca.chat_id = c.chat_id
                                     AND ca.group_id = c.group_id
                                     AND ca.receiver_id = %s
                                     AND ca.seen_at IS NOT NULL
                               )
                           ELSE true
                       END AS is_read
                FROM chat c
                JOIN users u ON u.user_id = c.sender_id
                WHERE c.group_id = %s
                ORDER BY c.date_time ASC
            """, (user_id, user_id, user_id, group_id))
            messages = cur.fetchall()

            from ml.s3 import get_chat_file_presigned_url
            result = []
            for msg in messages:
                m = dict(msg)
                m['total_members'] = total_members - 1
                if m.get('file_url') and m.get('message_type') in ('file', 'image'):
                    m['file_url'] = get_chat_file_presigned_url(m['file_url'])
                m['message_type'] = _normalize_message_type(m.get('message_type'))
                m['timestamp'] = _format_utc_iso(m.get('timestamp'))
                result.append(m)
            return result
    finally:
        return_connection(conn)


async def send_group_message(req: SendGroupMessageRequest):
    conn = get_db_connection()
    try:
        chat_id = str(uuid.uuid4())
        now = get_utc_now()
        message_type = _normalize_message_type(req.message_type)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT 1 FROM group_members
                WHERE group_id = %s AND member_id = %s AND status = 1
            """, (req.group_id, req.sender_id))
            if not cur.fetchone():
                raise Exception("User is not a member of this group")

            cur.execute("SELECT role_id, name FROM users WHERE user_id = %s", (req.sender_id,))
            user_row = cur.fetchone()
            role_id = user_row['role_id'] if user_row else 3

            cur.execute("""
                INSERT INTO chat (chat_id, sender_id, group_id, message, message_type, date_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING chat_id AS message_id, group_id, sender_id, message, message_type, file_url, date_time AS timestamp
            """, (chat_id, req.sender_id, req.group_id, req.message, message_type, now))
            conn.commit()
            result = _normalize_message_row(cur.fetchone())

            cur.execute("SELECT name FROM users WHERE user_id = %s", (req.sender_id,))
            sender = cur.fetchone()
            result['sender_name'] = sender['name'] if sender else ''
            result['is_read'] = False
            result['read_count'] = 0
            return result
    except Exception as e:
        conn.rollback()
        logger.error(f"Send group message error: {e}")
        raise
    finally:
        return_connection(conn)


async def mark_group_as_read(req: MarkGroupReadRequest):
    """
    Group chat: when a member opens the group, mark all messages (not sent by them) as read.
    Inserts into chat_activity with chat_id + group_id + receiver_id (the person reading).
    """
    conn = get_db_connection()
    try:
        now = get_utc_now()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            assert_group_participant(cur, req.group_id, req.user_id)
            # Find all unread group messages not sent by this user
            cur.execute("""
                SELECT c.chat_id, c.group_id, c.sender_id FROM chat c
                WHERE c.group_id = %s
                  AND c.sender_id != %s
                  AND NOT EXISTS (
                      SELECT 1 FROM chat_activity ca
                      WHERE ca.chat_id = c.chat_id
                        AND ca.group_id = c.group_id
                        AND ca.receiver_id = %s
                        AND ca.seen_at IS NOT NULL
                  )
            """, (req.group_id, req.user_id, req.user_id))
            rows = cur.fetchall()

            for row in rows:
                cur.execute("""
                    INSERT INTO chat_activity (chat_id, group_id, receiver_id, seen_at)
                    VALUES (%s, %s, %s, %s)
                """, (row['chat_id'], row['group_id'], req.user_id, now))

            # Counts for the realtime `read_update` events (one per newly read message, for its sender):
            # read_count = members who have seen it, total_members = members other than the sender.
            read_updates = []
            if rows:
                cur.execute("""
                    SELECT COUNT(*) AS total FROM group_members
                    WHERE group_id = %s AND status = 1
                """, (req.group_id,))
                total_members = max((cur.fetchone() or {}).get('total', 0) - 1, 0)
                cur.execute("""
                    SELECT chat_id, COUNT(*) AS read_count FROM chat_activity
                    WHERE group_id = %s AND chat_id = ANY(%s) AND seen_at IS NOT NULL
                    GROUP BY chat_id
                """, (req.group_id, [r['chat_id'] for r in rows]))
                counts = {r['chat_id']: r['read_count'] for r in cur.fetchall()}
                read_updates = [
                    {"message_id": r['chat_id'], "sender_id": r['sender_id'],
                     "read_count": counts.get(r['chat_id'], 0), "total_members": total_members}
                    for r in rows
                ]

            conn.commit()
        return {"success": True, "marked_count": len(rows), "read_updates": read_updates}
    except Exception as e:
        conn.rollback()
        logger.error(f"Mark group read error: {e}")
        raise
    finally:
        return_connection(conn)


async def get_group_members(group_id: str, caller_id: Optional[str] = None):
    """Active members of a group. With `caller_id` (the signed-in caller) the caller must be a participant of the group."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if caller_id:
                assert_group_participant(cur, group_id, caller_id)
            cur.execute("""
                SELECT u.user_id, u.name, u.emp_id,
                       r.role_name, d.dept_name, b.branch_name,
                       CASE WHEN g.user_id = u.user_id THEN true ELSE false END AS is_admin
                FROM group_members gm
                JOIN users u ON u.user_id = gm.member_id
                JOIN "group" g ON g.group_id = gm.group_id
                LEFT JOIN role_info r ON r.role_id = u.role_id
                LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = u.parent_id
                LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
                WHERE gm.group_id = %s AND gm.status = 1
                ORDER BY is_admin DESC, u.name ASC
            """, (group_id,))
            return cur.fetchall()
    finally:
        return_connection(conn)


async def add_group_members(req: AddGroupMembersRequest, caller_id: Optional[str] = None):
    """Add users to a group. The group must exist; with `caller_id` (the signed-in caller, from the token) the caller
    must be a member or the creator of the group; every added user must belong to the group's organisation. A foreign
    group or user answers 404 and nothing is written."""
    conn = get_db_connection()
    try:
        now = get_utc_now()
        added = 0
        new_member_ids = []
        membership_change = None
        member_ids = list(dict.fromkeys(req.member_ids))
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute('SELECT org_id, user_id FROM "group" WHERE group_id = %s', (req.group_id,))
            group = cur.fetchone()
            if not group:
                raise _not_found("Group not found")
            if caller_id:
                assert_group_participant(cur, req.group_id, caller_id)
            group_org = group['org_id']
            if not group_org:                                     # legacy row without organisation: the creator's one
                creator_orgs = _orgs_of_users(cur, [group['user_id']]).get(group['user_id']) or set()
                group_org = next(iter(creator_orgs), None)
            if users_in_org(cur, member_ids, group_org) != set(member_ids):
                raise _not_found("User not found")
            for member_id in member_ids:
                # Check if already a member
                cur.execute(
                    "SELECT id, status FROM group_members WHERE group_id = %s AND member_id = %s",
                    (req.group_id, member_id)
                )
                existing = cur.fetchone()
                if existing:
                    if existing['status'] != 1:
                        cur.execute(
                            "UPDATE group_members SET status = 1, updated_at = %s WHERE id = %s",
                            (now, existing['id'])
                        )
                        added += 1
                        new_member_ids.append(member_id)
                else:
                    cur.execute(
                        "INSERT INTO group_members (group_id, member_id, status, created_at, updated_at) VALUES (%s, %s, 1, %s, %s)",
                        (req.group_id, member_id, now, now)
                    )
                    added += 1
                    new_member_ids.append(member_id)
            conn.commit()

            if new_member_ids:
                # Facts for the realtime events (the route sends them, they are not part of the HTTP body)
                cur.execute('SELECT group_name, user_id AS created_by FROM "group" WHERE group_id = %s', (req.group_id,))
                group_row = cur.fetchone() or {}
                cur.execute("SELECT member_id FROM group_members WHERE group_id = %s AND status = 1", (req.group_id,))
                active_ids = [r['member_id'] for r in cur.fetchall()]
                membership_change = {
                    "group_name": group_row.get('group_name'),
                    "created_by": group_row.get('created_by'),
                    "new_member_ids": list(new_member_ids),
                    "existing_member_ids": [m for m in active_ids if m not in new_member_ids],
                    "member_count": len(active_ids),
                }
        result = {"success": True, "added": added}
        if membership_change:
            result["membership_change"] = membership_change
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Add group members error: {e}")
        raise
    finally:
        return_connection(conn)


# ═══════════════════ 3. ANNOUNCEMENT TO ALL ═══════════════════

async def send_announcement_all(req: SendAnnouncementRequest):
    conn = get_db_connection()
    try:
        announcement_id = str(uuid.uuid4())
        now = get_utc_now()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT role_id, org_id, name FROM users WHERE user_id = %s", (req.sender_id,))
            user = cur.fetchone()
            if not user or user['role_id'] != 1:
                raise Exception("Only admin can send announcements")

            cur.execute("""
                INSERT INTO announcement (announcement_id, announcement_type, message, org_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING announcement_id, message, org_id, created_at
            """, (announcement_id, req.announcement_type, req.message, req.org_id, now, now))
            conn.commit()
            result = cur.fetchone()

            # Recipients: every active employee (role 2 & 3) of the org (they carry parent_id = org_id)
            cur.execute("""
                SELECT user_id FROM users
                WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1
            """, (req.org_id,))
            recipient_ids = [r['user_id'] for r in cur.fetchall()]

        sender_name = user['name']
        import asyncio
        from services import realtime
        asyncio.ensure_future(realtime.deliver_announcement(
            recipient_ids, announcement_id, None, req.message, 'all', req.org_id,
            push_title=f'Announcement from {sender_name}',
            push_data={'type': 'announcement', 'announcement_id': announcement_id, 'org_id': req.org_id}
        ))
        return result
    except Exception as e:
        conn.rollback()
        logger.error(f"Send announcement error: {e}")
        raise
    finally:
        return_connection(conn)


async def get_announcements_all(org_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT a.announcement_id, a.announcement_type, a.message,
                       a.org_id, a.created_at, a.branch_id, a.dept_id
                FROM announcement a
                WHERE a.org_id = %s AND a.branch_id IS NULL AND a.dept_id IS NULL
                ORDER BY a.created_at DESC
            """, (org_id,))
            return cur.fetchall()
    finally:
        return_connection(conn)


# ═══════════════════ 4. ANNOUNCEMENT BY BRANCH & DEPT ═══════════════════

async def send_announcement_branch_dept(req: SendAnnouncementRequest):
    conn = get_db_connection()
    try:
        announcement_id = str(uuid.uuid4())
        now = get_utc_now()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT role_id, name FROM users WHERE user_id = %s", (req.sender_id,))
            user = cur.fetchone()
            if not user or user['role_id'] != 1:
                raise Exception("Only admin can send announcements")

            if not req.branch_id and not req.dept_id:
                raise Exception("branch_id or dept_id is required for targeted announcement")

            cur.execute("""
                INSERT INTO announcement (announcement_id, announcement_type, message, org_id, branch_id, dept_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING announcement_id, message, org_id, branch_id, dept_id, created_at
            """, (announcement_id, req.announcement_type, req.message, req.org_id, req.branch_id, req.dept_id, now, now))
            conn.commit()
            result = cur.fetchone()

            # Recipients: active employees of the org, narrowed by branch and/or department
            query = """
                SELECT user_id FROM users
                WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1
            """
            params = [req.org_id]
            if req.branch_id:
                query += " AND branch_id = %s"
                params.append(req.branch_id)
            if req.dept_id:
                query += " AND dept_id = %s"
                params.append(req.dept_id)
            cur.execute(query, params)
            recipient_ids = [r['user_id'] for r in cur.fetchall()]

        sender_name = user['name']
        import asyncio
        from services import realtime
        asyncio.ensure_future(realtime.deliver_announcement(
            recipient_ids, announcement_id, None, req.message, 'branch' if req.branch_id else 'dept', req.org_id,
            push_title=f'Announcement from {sender_name}',
            push_data={
                'type': 'announcement',
                'announcement_id': announcement_id,
                'org_id': req.org_id,
                'branch_id': str(req.branch_id) if req.branch_id else '',
                'dept_id': req.dept_id or ''
            }
        ))
        return result
    except Exception as e:
        conn.rollback()
        logger.error(f"Send branch/dept announcement error: {e}")
        raise
    finally:
        return_connection(conn)


async def get_announcements_branch_dept(org_id: str, branch_id: Optional[int] = None, dept_id: Optional[str] = None):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = """
                SELECT a.announcement_id, a.announcement_type, a.message,
                       a.org_id, a.branch_id, a.dept_id, a.created_at,
                       b.branch_name, d.dept_name
                FROM announcement a
                LEFT JOIN branch_info b ON b.branch_id = a.branch_id AND b.org_id = a.org_id
                LEFT JOIN dept_info d ON d.dept_id = a.dept_id AND d.org_id = a.org_id
                WHERE a.org_id = %s AND (a.branch_id IS NOT NULL OR a.dept_id IS NOT NULL)
            """
            params = [org_id]
            if branch_id:
                query += " AND a.branch_id = %s"
                params.append(branch_id)
            if dept_id:
                query += " AND a.dept_id = %s"
                params.append(dept_id)
            query += " ORDER BY a.created_at DESC"
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        return_connection(conn)


async def delete_chat(user_id: str, other_user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Delete chat_activity records first (FK constraint)
            cur.execute("""
                DELETE FROM chat_activity
                WHERE chat_id IN (
                    SELECT chat_id FROM chat
                    WHERE group_id IS NULL
                      AND ((sender_id = %s AND receiver_id = %s)
                        OR (sender_id = %s AND receiver_id = %s))
                )
            """, (user_id, other_user_id, other_user_id, user_id))
            # Delete all messages between the two users
            cur.execute("""
                DELETE FROM chat
                WHERE group_id IS NULL
                  AND ((sender_id = %s AND receiver_id = %s)
                    OR (sender_id = %s AND receiver_id = %s))
            """, (user_id, other_user_id, other_user_id, user_id))
            conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        logger.error(f"Delete chat error: {e}")
        raise
    finally:
        return_connection(conn)


async def get_announcements_for_employee(user_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT org_id, branch_id, dept_id FROM users WHERE user_id = %s", (user_id,))
            user = cur.fetchone()
            if not user:
                return []

            cur.execute("""
                SELECT a.announcement_id, a.announcement_type, a.message,
                       a.org_id, a.branch_id, a.dept_id, a.created_at,
                       b.branch_name, d.dept_name
                FROM announcement a
                LEFT JOIN branch_info b ON b.branch_id = a.branch_id AND b.org_id = a.org_id
                LEFT JOIN dept_info d ON d.dept_id = a.dept_id AND d.org_id = a.org_id
                WHERE a.org_id = %s
                  AND (
                      (a.branch_id IS NULL AND a.dept_id IS NULL)
                      OR a.branch_id = %s
                      OR a.dept_id = %s
                  )
                ORDER BY a.created_at DESC
            """, (user['org_id'], user['branch_id'], user['dept_id']))
            return cur.fetchall()
    finally:
        return_connection(conn)
