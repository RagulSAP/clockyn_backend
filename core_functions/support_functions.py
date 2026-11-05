import logging
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)


async def get_support_subjects():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT sub_id, sub_name FROM admin_support WHERE status = 1 ORDER BY id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        return_connection(conn)


async def submit_support_request(user_id: str, org_id: str, subject: str, message: str, photo_key: str = None):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, sub_id FROM admin_support WHERE sub_name = %s AND status = 1", (subject,))
            sub = cur.fetchone()
            if not sub:
                raise ValueError(f"Invalid subject: {subject}")

            cur.execute("""
                INSERT INTO admin_subject_request (support_id, sub_id, org_id, user_id, client_msg, status, photo_url)
                VALUES (%s, %s, %s, %s, %s, 'created', %s)
                RETURNING id, support_id, sub_id, org_id, user_id, client_msg, status, photo_url, created_at
            """, (sub['id'], sub['sub_id'], org_id, user_id, message, photo_key))
            conn.commit()
            return dict(cur.fetchone())
    except Exception as e:
        conn.rollback()
        logger.error(f"Submit support error: {e}")
        raise
    finally:
        return_connection(conn)


async def update_support_request(request_id: int, status: str, reply: str = None):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE admin_subject_request
                SET status = %s, reply = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id, status, reply, updated_at
            """, (status, reply, request_id))
            conn.commit()
            row = cur.fetchone()
            if not row:
                raise ValueError("Request not found")
            return dict(row)
    except Exception as e:
        conn.rollback()
        logger.error(f"Update support error: {e}")
        raise
    finally:
        return_connection(conn)


async def reply_to_ticket(ticket_id: int, reply_text: str, author_name: str, is_admin: bool):
    """Add a reply to a ticket's thread (admin or user), and keep status sensible:
    - Admin reply on a fresh ticket moves it to 'in_progress'.
    - User reply on a resolved ticket reopens it to 'in_progress'.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT status FROM admin_subject_request WHERE id = %s", (ticket_id,))
            ticket = cur.fetchone()
            if not ticket:
                raise ValueError("Ticket not found")

            cur.execute("""
                INSERT INTO ticket_replies (ticket_id, reply_text, author_name, is_admin)
                VALUES (%s, %s, %s, %s)
                RETURNING id, ticket_id, reply_text, author_name, is_admin, created_at
            """, (ticket_id, reply_text, author_name, is_admin))
            new_reply = dict(cur.fetchone())

            new_status = None
            if is_admin and ticket['status'] == 'created':
                new_status = 'in_progress'
            elif not is_admin and ticket['status'] == 'resolved':
                new_status = 'in_progress'

            if new_status:
                cur.execute(
                    "UPDATE admin_subject_request SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (new_status, ticket_id)
                )

            conn.commit()
            new_reply['status'] = new_status or ticket['status']
            return new_reply
    except Exception as e:
        conn.rollback()
        logger.error(f"Reply to ticket error: {e}")
        raise
    finally:
        return_connection(conn)


def _rows_with_replies_and_photos(rows: list) -> list:
    from ml.s3 import get_support_photo_url
    result = []
    for row in rows:
        item = dict(row)
        if item.get('photo_url'):
            item['photo_url'] = get_support_photo_url(item['photo_url'])
        for r in item.get('replies') or []:
            if r.get('created_at'):
                r['created_at'] = str(r['created_at'])
        result.append(item)
    return result


async def get_all_support_requests():
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name,
                           r.user_id, u.name AS user_name, u.mobile_number AS user_mobile,
                           r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at,
                           COALESCE(
                               json_agg(
                                   json_build_object(
                                       'reply_text', tr.reply_text,
                                       'author_name', tr.author_name,
                                       'is_admin', tr.is_admin,
                                       'created_at', tr.created_at
                                   ) ORDER BY tr.created_at ASC
                               ) FILTER (WHERE tr.id IS NOT NULL),
                               '[]'::json
                           ) AS replies
                    FROM admin_subject_request r
                    JOIN admin_support s ON s.sub_id = r.sub_id
                    LEFT JOIN org_info oi ON oi.org_id = r.org_id
                    LEFT JOIN users u ON u.user_id = r.user_id
                    LEFT JOIN ticket_replies tr ON tr.ticket_id = r.id
                    GROUP BY r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name, r.user_id, u.name, u.mobile_number,
                             r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at
                    ORDER BY r.created_at DESC
                """)
                rows = cur.fetchall()
            except Exception:
                conn.rollback()
                # ticket_replies table not yet migrated — fall back to base query
                cur.execute("""
                    SELECT r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name,
                           r.user_id, u.name AS user_name, u.mobile_number AS user_mobile,
                           r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at
                    FROM admin_subject_request r
                    JOIN admin_support s ON s.sub_id = r.sub_id
                    LEFT JOIN org_info oi ON oi.org_id = r.org_id
                    LEFT JOIN users u ON u.user_id = r.user_id
                    ORDER BY r.created_at DESC
                """)
                rows = cur.fetchall()
            return _rows_with_replies_and_photos(rows)
    finally:
        return_connection(conn)


async def get_support_requests(org_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute("""
                    SELECT r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name,
                           r.user_id, u.name AS user_name, u.mobile_number AS user_mobile,
                           r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at,
                           COALESCE(
                               json_agg(
                                   json_build_object(
                                       'reply_text', tr.reply_text,
                                       'author_name', tr.author_name,
                                       'is_admin', tr.is_admin,
                                       'created_at', tr.created_at
                                   ) ORDER BY tr.created_at ASC
                               ) FILTER (WHERE tr.id IS NOT NULL),
                               '[]'::json
                           ) AS replies
                    FROM admin_subject_request r
                    JOIN admin_support s ON s.sub_id = r.sub_id
                    LEFT JOIN org_info oi ON oi.org_id = r.org_id
                    LEFT JOIN users u ON u.user_id = r.user_id
                    LEFT JOIN ticket_replies tr ON tr.ticket_id = r.id
                    WHERE r.org_id = %s
                    GROUP BY r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name, r.user_id, u.name, u.mobile_number,
                             r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at
                    ORDER BY r.created_at DESC
                """, (org_id,))
                rows = cur.fetchall()
            except Exception:
                conn.rollback()
                cur.execute("""
                    SELECT r.id, r.sub_id, s.sub_name, r.org_id, oi.org_name,
                           r.user_id, u.name AS user_name, u.mobile_number AS user_mobile,
                           r.client_msg, r.status, r.reply, r.photo_url, r.created_at, r.updated_at
                    FROM admin_subject_request r
                    JOIN admin_support s ON s.sub_id = r.sub_id
                    LEFT JOIN org_info oi ON oi.org_id = r.org_id
                    LEFT JOIN users u ON u.user_id = r.user_id
                    WHERE r.org_id = %s
                    ORDER BY r.created_at DESC
                """, (org_id,))
                rows = cur.fetchall()
            return _rows_with_replies_and_photos(rows)
    finally:
        return_connection(conn)
