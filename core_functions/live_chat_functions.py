"""Live Chat: the organisation's admin (role 1, Clockyn app) <-> the Clockyn support team (web console operators).

One thread per organisation (`live_chat_thread`), messages in `live_chat_message` (`sender_type` 'org' | 'support').
Text and image / file attachments, read ticks (`read_at` = the OTHER side has read it), unread counts, realtime frames
(`services/realtime.py`: org admins' app sockets and the operators' `support-ops` sockets) and an FCM push to the org
admins when support writes. Not plan-gated: a lapsed customer must still be able to reach support.

App side (`app_*`): the caller must be a role-1 user of the organisation, otherwise 403 (also when REQUIRE_AUTH is off).
Operator side (`op_*`): the routers sit behind `operator_auth.require_operator`; support never starts a conversation
(replying to an organisation without a thread is a 404). Names of senders always come from the database, never from the
client. Attachments are stored in S3 (`ml.s3.upload_chat_file`) and only presigned URLs leave the server, never the key.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor

from core_functions.db_function import get_db_connection, return_connection
from services import push_service, realtime
from utils.timezone import get_utc_now
from utils.uploads import chat_upload_max_mb, read_upload

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 4000
MAX_MESSAGES = 500          # the history route returns the LAST this many, oldest first
MAX_THREADS = 500
SUPPORT_NAME = "Clockyn Support"
SUPPORT_SENDER_ID = "support"           # sender id of a reply made with the operator key only (no x-admin-id)
PUSH_TITLE = "Clockyn Support"
ATTACHMENT_PUSH_TEXT = "\U0001f4ce Sent an attachment"
THREAD_STATUSES = ("open", "closed")


# ═══════════════════ MODELS ═══════════════════
class AppSendRequest(BaseModel):
    org_id: str
    user_id: str
    message: str


class AppReadRequest(BaseModel):
    org_id: str
    user_id: str


class OperatorSendRequest(BaseModel):
    message: str


class OperatorStatusRequest(BaseModel):
    status: str


# ═══════════════════ helpers ═══════════════════
def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _presign(file_key: Optional[str]) -> Optional[str]:
    if not file_key:
        return None
    from ml.s3 import get_chat_file_presigned_url
    return get_chat_file_presigned_url(file_key)


def message_payload(row: Dict[str, Any], file_url: Optional[str] = None) -> Dict[str, Any]:
    """The wire `Message` of a `live_chat_message` row. Never contains the S3 key."""
    return {
        "message_id": str(row["message_id"]),
        "timestamp": _iso(row["created_at"]),
        "message_type": row.get("message_type") or "text",
        "message": row.get("message"),
        "file_url": file_url if file_url is not None else _presign(row.get("file_key")),
        "file_name": row.get("file_name"),
        "content_type": row.get("content_type"),
        "sender_type": row["sender_type"],
        "sender_id": row["sender_id"],
        "sender_name": row.get("sender_name"),
        "is_read": row.get("read_at") is not None,
        "is_delivered": True,
    }


def _clean_text(raw: Optional[str]) -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(text) > MAX_TEXT_LENGTH:
        raise HTTPException(status_code=400, detail=f"Message is too long (max {MAX_TEXT_LENGTH} characters)")
    return text


def _attachment_kind(content_type: Optional[str]) -> str:
    return "image" if (content_type or "").lower().startswith("image/") else "file"


def _like(q: str) -> str:
    """`%q%` with the LIKE wildcards of the user's text escaped."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fire_push(user_ids: List[str], body: str, data: Dict[str, str], image_url: Optional[str] = None) -> None:
    """Fire-and-forget FCM push to every device of the org admins (the request never waits for FCM).
    `image_url`: a photo shown in the notification (collapsed thumbnail, expanded full picture)."""
    if user_ids:
        if image_url:
            asyncio.create_task(push_service.send_push_to_users(user_ids, PUSH_TITLE, body, data, image_url=image_url))
        else:
            asyncio.create_task(push_service.send_push_to_users(user_ids, PUSH_TITLE, body, data))


_UNDEFINED_TABLE = "42P01"          # PostgreSQL: relation does not exist
_missing_tables_logged = False


def _tables_missing(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) == _UNDEFINED_TABLE


def _log_tables_missing(what: str) -> None:
    """The deployment ran before the two tables were created: say so loudly, once per process (not on every poll)."""
    global _missing_tables_logged
    if not _missing_tables_logged:
        _missing_tables_logged = True
        logger.error("live chat: the tables live_chat_thread / live_chat_message do not exist in this schema (first seen in "
                     "%s). Run the 'CREATE: Live Chat' block of clockyn_ddl_latest.sql.", what)


def _unexpected(what: str, exc: Exception) -> HTTPException:
    if _tables_missing(exc):
        _log_tables_missing(what)
        return HTTPException(status_code=503, detail="Live chat is not set up yet")
    logger.exception("live chat: %s failed", what)
    return HTTPException(status_code=500, detail="Internal server error")


def _degrade_if_tables_missing(conn, what: str, exc: Exception) -> bool:
    """READ endpoints answer an empty result while the tables are missing instead of a 5xx: the web console polls
    unread-count on every page and the app seeds its badge at start-up, so an error here would be a constant stream of
    5xx that also trips the deployment's error-rate rollback alarm."""
    if not _tables_missing(exc):
        return False
    try:
        conn.rollback()
    except Exception:
        pass
    _log_tables_missing(what)
    return True


# ═══════════════════ SQL building blocks (all take an open cursor) ═══════════════════
_MESSAGE_COLUMNS = ("message_id, created_at, message_type, message, file_key, file_name, content_type, sender_type, "
                    "sender_id, sender_name, read_at")


def _assert_org_admin(cur, user_id: str, org_id: str) -> Dict[str, Any]:
    """403 unless `user_id` is an active role-1 user of `org_id`."""
    cur.execute(
        "SELECT user_id, name FROM users WHERE user_id = %s AND org_id = %s AND role_id = 1 AND status = 1",
        (user_id, org_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Forbidden")
    return row


def _org_admin_ids(cur, org_id: str) -> List[str]:
    cur.execute("SELECT user_id FROM users WHERE org_id = %s AND role_id = 1 AND status = 1", (org_id,))
    return [r["user_id"] for r in (cur.fetchall() or [])]


def _org_name(cur, org_id: str) -> Optional[str]:
    cur.execute("SELECT org_name FROM org_info WHERE org_id = %s", (org_id,))
    row = cur.fetchone()
    return row["org_name"] if row else None


def _org_admin_info(cur, org_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """{org_id: {name, email, mobile}} of the first admin (role 1) of each organisation."""
    ids = [i for i in dict.fromkeys(org_ids) if i]
    if not ids:
        return {}
    cur.execute(
        "SELECT DISTINCT ON (org_id) org_id, name, email, mobile_number FROM users "
        "WHERE org_id = ANY(%s) AND role_id = 1 ORDER BY org_id, id",
        (ids,),
    )
    return {r["org_id"]: {"name": r.get("name"), "email": r.get("email"), "mobile": r.get("mobile_number")}
            for r in (cur.fetchall() or [])}


def _thread_row(cur, org_id: str) -> Optional[Dict[str, Any]]:
    cur.execute("SELECT thread_id, status FROM live_chat_thread WHERE org_id = %s", (org_id,))
    return cur.fetchone()


def _upsert_org_thread(cur, org_id: str, preview: str, message_type: str, now: datetime) -> Dict[str, Any]:
    """Race-safe: creates the thread with the first org message, otherwise updates the preview and REOPENS it."""
    cur.execute(
        """
        INSERT INTO live_chat_thread (thread_id, org_id, status, last_message, last_message_type, last_sender_type,
                                      last_message_at, created_at, updated_at)
        VALUES (%s, %s, 'open', %s, %s, 'org', %s, %s, %s)
        ON CONFLICT (org_id) DO UPDATE SET
            status = 'open',
            last_message = EXCLUDED.last_message,
            last_message_type = EXCLUDED.last_message_type,
            last_sender_type = 'org',
            last_message_at = EXCLUDED.last_message_at,
            updated_at = EXCLUDED.updated_at
        RETURNING thread_id, status
        """,
        (str(uuid.uuid4()), org_id, preview, message_type, now, now, now),
    )
    return cur.fetchone()


def _insert_message(cur, *, thread_id: str, org_id: str, sender_type: str, sender_id: str, sender_name: Optional[str],
                    message_type: str, message: str, file_key: Optional[str], file_name: Optional[str],
                    content_type: Optional[str], now: datetime) -> Dict[str, Any]:
    message_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO live_chat_message (message_id, thread_id, org_id, sender_type, sender_id, sender_name,
                                       message_type, message, file_key, file_name, content_type, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (message_id, thread_id, org_id, sender_type, sender_id, sender_name, message_type, message, file_key,
         file_name, content_type, now),
    )
    return {
        "message_id": message_id, "created_at": now, "message_type": message_type, "message": message,
        "file_key": file_key, "file_name": file_name, "content_type": content_type, "sender_type": sender_type,
        "sender_id": sender_id, "sender_name": sender_name, "read_at": None,
    }


def _unread(cur, org_id: str, sender_type: str) -> int:
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM live_chat_message WHERE org_id = %s AND sender_type = %s AND read_at IS NULL",
        (org_id, sender_type),
    )
    row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def _mark_read(cur, org_id: str, sender_type: str, now: datetime) -> int:
    """Marks the messages SENT BY `sender_type` as read (by the other side). Returns how many changed."""
    cur.execute(
        "UPDATE live_chat_message SET read_at = %s WHERE org_id = %s AND sender_type = %s AND read_at IS NULL",
        (now, org_id, sender_type),
    )
    return cur.rowcount or 0


def _history(cur, thread_id: str) -> List[Dict[str, Any]]:
    cur.execute(
        f"SELECT {_MESSAGE_COLUMNS} FROM live_chat_message WHERE thread_id = %s ORDER BY id DESC LIMIT %s",
        (thread_id, MAX_MESSAGES),
    )
    return list(reversed(cur.fetchall() or []))


def _operator_identity(cur, admin_id: Optional[str]):
    """(sender_id, display name) of the operator who replies, from the `x-admin-id` header (e-mail, else phone)."""
    raw = (admin_id or "").strip()
    if not raw:
        return SUPPORT_SENDER_ID, SUPPORT_NAME
    cur.execute("SELECT name FROM gen_admin_login WHERE email = %s", (raw.lower(),))
    row = cur.fetchone()
    if not row:
        cur.execute("SELECT name FROM gen_admin_login WHERE phone_number = %s", (raw,))
        row = cur.fetchone()
    name = ((row or {}).get("name") or "").strip()
    if not name and "@" in raw:
        name = raw.split("@", 1)[0]
    return (raw.lower() if "@" in raw else raw), (name or SUPPORT_NAME)


# ═══════════════════ realtime ═══════════════════
async def _publish_message(payload: Dict[str, Any], org_id: str, org_name: Optional[str], thread_status: str,
                           admin_ids: List[str]) -> None:
    """The new message to every open console and to every device of the org admins. Never raises."""
    try:
        await realtime.publish_to_operators({"type": "live_chat_message", "data": {
            **payload, "org_id": org_id, "org_name": org_name, "thread_status": thread_status}})
        await realtime.publish_to_users(admin_ids, {"type": "live_chat_message", "data": {**payload, "org_id": org_id}})
    except Exception as exc:                                    # a socket problem must never fail the send
        logger.warning("live chat realtime publish failed: %s", exc)


# ═══════════════════ APP side ═══════════════════
async def app_get_messages(org_id: str, user_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _assert_org_admin(cur, user_id, org_id)
            thread = _thread_row(cur, org_id)
            if not thread:
                return {"success": True, "status": None, "unread": 0, "messages": []}
            rows = _history(cur, thread["thread_id"])
            unread = _unread(cur, org_id, "support")
        return {"success": True, "status": thread["status"], "unread": unread,
                "messages": [message_payload(r) for r in rows]}
    except HTTPException:
        raise
    except Exception as exc:
        if _degrade_if_tables_missing(conn, "history", exc):
            return {"success": True, "status": None, "unread": 0, "messages": [], "setup_required": True}
        raise _unexpected("history", exc)
    finally:
        return_connection(conn)


async def app_unread(org_id: str, user_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _assert_org_admin(cur, user_id, org_id)
            return {"success": True, "unread": _unread(cur, org_id, "support")}
    except HTTPException:
        raise
    except Exception as exc:
        if _degrade_if_tables_missing(conn, "unread", exc):
            return {"success": True, "unread": 0, "setup_required": True}
        raise _unexpected("unread", exc)
    finally:
        return_connection(conn)


def app_check_admin(org_id: str, user_id: str) -> None:
    """403 before an upload is read (multipart route)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _assert_org_admin(cur, user_id, org_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise _unexpected("owner check", exc)
    finally:
        return_connection(conn)


async def _app_store(org_id: str, user_id: str, message_type: str, message: str, *, file_key: Optional[str] = None,
                     file_name: Optional[str] = None, content_type: Optional[str] = None,
                     file_url: Optional[str] = None) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            user = _assert_org_admin(cur, user_id, org_id)
            now = get_utc_now()
            thread = _upsert_org_thread(cur, org_id, message, message_type, now)
            row = _insert_message(cur, thread_id=thread["thread_id"], org_id=org_id, sender_type="org",
                                  sender_id=user_id, sender_name=user.get("name"), message_type=message_type,
                                  message=message, file_key=file_key, file_name=file_name, content_type=content_type,
                                  now=now)
            org_name = _org_name(cur, org_id)
            admin_ids = _org_admin_ids(cur, org_id)
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("send", exc)
    finally:
        return_connection(conn)
    payload = message_payload(row, file_url=file_url)
    await _publish_message(payload, org_id, org_name, thread["status"], admin_ids)
    return payload


async def app_send_text(req: AppSendRequest) -> Dict[str, Any]:
    return await _app_store(req.org_id, req.user_id, "text", _clean_text(req.message))


async def app_send_file(org_id: str, user_id: str, upload) -> Dict[str, Any]:
    app_check_admin(org_id, user_id)                           # 403 before the bytes are read / stored
    data = await read_upload(upload, chat_upload_max_mb(), None)
    content_type = upload.content_type or "application/octet-stream"
    from ml.s3 import upload_chat_file
    try:
        file_key, file_url = upload_chat_file(data, upload.filename, content_type)
    except Exception as exc:
        raise _unexpected("attachment upload", exc)
    display = (upload.filename or file_key.split("/")[-1])[:255]
    return await _app_store(org_id, user_id, _attachment_kind(content_type), display, file_key=file_key,
                            file_name=display, content_type=content_type[:100], file_url=file_url)


async def app_mark_read(req: AppReadRequest) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            _assert_org_admin(cur, req.user_id, req.org_id)
            marked = _mark_read(cur, req.org_id, "support", get_utc_now())
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("mark read", exc)
    finally:
        return_connection(conn)
    if marked:
        try:
            await realtime.publish_to_operators({"type": "live_chat_read", "data": {"org_id": req.org_id, "reader": "org"}})
        except Exception as exc:
            logger.warning("live chat read frame failed: %s", exc)
    return {"success": True, "marked": marked}


# ═══════════════════ OPERATOR side ═══════════════════
def _thread_header(cur, org_id: str, thread: Dict[str, Any]) -> Dict[str, Any]:
    admin = _org_admin_info(cur, [org_id]).get(org_id, {})
    return {
        "thread_id": thread["thread_id"], "org_id": org_id, "org_name": _org_name(cur, org_id),
        "admin_name": admin.get("name"), "admin_email": admin.get("email"), "admin_mobile": admin.get("mobile"),
        "status": thread["status"],
    }


async def op_list_threads(status: Optional[str] = None, q: Optional[str] = None) -> List[Dict[str, Any]]:
    status = (status or "all").strip().lower()
    if status not in THREAD_STATUSES + ("all",):
        raise HTTPException(status_code=400, detail="status must be open, closed or all")
    where, params = ["t.last_message_at IS NOT NULL"], []
    if status != "all":
        where.append("t.status = %s")
        params.append(status)
    term = (q or "").strip()
    if term:
        like = _like(term)
        where.append(
            "(o.org_name ILIKE %s OR t.org_id IN (SELECT org_id FROM users WHERE role_id = 1 AND "
            "(name ILIKE %s OR email ILIKE %s)))"
        )
        params += [like, like, like]
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT t.thread_id, t.org_id, t.status, t.last_message, t.last_message_type, t.last_sender_type,
                       t.last_message_at, o.org_name, COALESCE(c.cnt, 0) AS unread_count
                FROM live_chat_thread t
                LEFT JOIN org_info o ON o.org_id = t.org_id
                LEFT JOIN (SELECT org_id, COUNT(*) AS cnt FROM live_chat_message
                           WHERE sender_type = 'org' AND read_at IS NULL GROUP BY org_id) c ON c.org_id = t.org_id
                WHERE {' AND '.join(where)}
                ORDER BY t.last_message_at DESC
                LIMIT {MAX_THREADS}
                """,
                tuple(params),
            )
            rows = cur.fetchall() or []
            admins = _org_admin_info(cur, [r["org_id"] for r in rows])
        out = []
        for r in rows:
            admin = admins.get(r["org_id"], {})
            out.append({
                "thread_id": r["thread_id"], "org_id": r["org_id"], "org_name": r.get("org_name"),
                "admin_name": admin.get("name"), "admin_email": admin.get("email"), "admin_mobile": admin.get("mobile"),
                "status": r["status"], "last_message": r.get("last_message"),
                "last_message_type": r.get("last_message_type"), "last_sender_type": r.get("last_sender_type"),
                "last_message_at": _iso(r.get("last_message_at")), "unread_count": int(r.get("unread_count") or 0),
            })
        return out
    except HTTPException:
        raise
    except Exception as exc:
        if _degrade_if_tables_missing(conn, "thread list", exc):
            return []
        raise _unexpected("thread list", exc)
    finally:
        return_connection(conn)


async def op_get_messages(org_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            thread = _thread_row(cur, org_id)
            if not thread:
                raise HTTPException(status_code=404, detail="No conversation for this organization")
            header = _thread_header(cur, org_id, thread)
            rows = _history(cur, thread["thread_id"])
        return {"success": True, "thread": header, "messages": [message_payload(r) for r in rows]}
    except HTTPException:
        raise
    except Exception as exc:
        if _degrade_if_tables_missing(conn, "operator history", exc):
            raise HTTPException(status_code=404, detail="No conversation for this organization")
        raise _unexpected("operator history", exc)
    finally:
        return_connection(conn)


async def _op_store(org_id: str, admin_id: Optional[str], message_type: str, message: str, *,
                    file_key: Optional[str] = None, file_name: Optional[str] = None, content_type: Optional[str] = None,
                    file_url: Optional[str] = None) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            thread = _thread_row(cur, org_id)
            if not thread:
                raise HTTPException(status_code=404, detail="No conversation for this organization")
            sender_id, sender_name = _operator_identity(cur, admin_id)
            now = get_utc_now()
            row = _insert_message(cur, thread_id=thread["thread_id"], org_id=org_id, sender_type="support",
                                  sender_id=sender_id, sender_name=sender_name, message_type=message_type,
                                  message=message, file_key=file_key, file_name=file_name, content_type=content_type,
                                  now=now)
            cur.execute(
                "UPDATE live_chat_thread SET last_message = %s, last_message_type = %s, last_sender_type = 'support', "
                "last_message_at = %s, updated_at = %s WHERE org_id = %s",
                (message, message_type, now, now, org_id),
            )
            org_name = _org_name(cur, org_id)
            admin_ids = _org_admin_ids(cur, org_id)
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("operator send", exc)
    finally:
        return_connection(conn)
    payload = message_payload(row, file_url=file_url)
    await _publish_message(payload, org_id, org_name, thread["status"], admin_ids)
    push_data = {"type": "live_chat_message", "org_id": org_id, "message_id": payload["message_id"]}
    push_image = push_service.push_image_for(content_type, file_url) if message_type != "text" else None
    if push_image:
        _fire_push(admin_ids, push_service.PHOTO_PUSH_TEXT, push_data, image_url=push_image)
    else:
        _fire_push(admin_ids, message if message_type == "text" else ATTACHMENT_PUSH_TEXT, push_data)
    return payload


async def op_send_text(org_id: str, text: str, admin_id: Optional[str]) -> Dict[str, Any]:
    return await _op_store(org_id, admin_id, "text", _clean_text(text))


def op_check_thread(org_id: str) -> None:
    """404 before an upload is read (multipart route): support never starts a conversation."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not _thread_row(cur, org_id):
                raise HTTPException(status_code=404, detail="No conversation for this organization")
    except HTTPException:
        raise
    except Exception as exc:
        raise _unexpected("thread check", exc)
    finally:
        return_connection(conn)


async def op_send_file(org_id: str, upload, admin_id: Optional[str]) -> Dict[str, Any]:
    op_check_thread(org_id)
    data = await read_upload(upload, chat_upload_max_mb(), None)
    content_type = upload.content_type or "application/octet-stream"
    from ml.s3 import upload_chat_file
    try:
        file_key, file_url = upload_chat_file(data, upload.filename, content_type)
    except Exception as exc:
        raise _unexpected("attachment upload", exc)
    display = (upload.filename or file_key.split("/")[-1])[:255]
    return await _op_store(org_id, admin_id, _attachment_kind(content_type), display, file_key=file_key,
                           file_name=display, content_type=content_type[:100], file_url=file_url)


async def _publish_message_deleted(data: Dict[str, Any], admin_ids: List[str]) -> None:
    """Tells every open console and every device of the org admins that a message is gone. Never raises."""
    frame = {"type": "live_chat_message_deleted", "data": data}
    try:
        await realtime.publish_to_operators(frame)
        await realtime.publish_to_users(admin_ids, frame)
    except Exception as exc:                                    # a socket problem must never fail the delete
        logger.warning("live chat delete frame failed: %s", exc)


def _delete_s3_object_quietly(file_key: str) -> None:
    """Best-effort removal of an attachment's S3 object: the row is already gone, so a storage problem only leaves an
    orphan object behind and must never fail the request."""
    try:
        from ml.s3 import delete_s3_object
        delete_s3_object(file_key)
    except Exception as exc:
        logger.warning("live chat: could not delete attachment %s from storage: %s", file_key, exc)


async def op_delete_message(org_id: str, message_id: str) -> Dict[str, Any]:
    """Hard-deletes ONE message of the organisation's thread (404 when it does not exist or belongs to another
    organisation), refreshes the thread's preview (last_message*) from the newest remaining message (cleared when none is
    left; status and the other thread fields are untouched) and tells the consoles and the org admins' devices."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            thread = _thread_row(cur, org_id)
            if not thread:
                raise HTTPException(status_code=404, detail="No conversation for this organization")
            cur.execute(
                "DELETE FROM live_chat_message WHERE message_id = %s AND thread_id = %s AND org_id = %s "
                "RETURNING message_id, file_key",
                (message_id, thread["thread_id"], org_id),
            )
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail="Message not found")
            cur.execute(
                "SELECT message, message_type, sender_type, created_at FROM live_chat_message "
                "WHERE thread_id = %s ORDER BY id DESC LIMIT 1",
                (thread["thread_id"],),
            )
            newest = cur.fetchone() or {}
            cur.execute(
                "UPDATE live_chat_thread SET last_message = %s, last_message_type = %s, last_sender_type = %s, "
                "last_message_at = %s, updated_at = %s WHERE org_id = %s",
                (newest.get("message"), newest.get("message_type"), newest.get("sender_type"),
                 newest.get("created_at"), get_utc_now(), org_id),
            )
            admin_ids = _org_admin_ids(cur, org_id)
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("operator delete message", exc)
    finally:
        return_connection(conn)
    if deleted.get("file_key"):
        await asyncio.to_thread(_delete_s3_object_quietly, deleted["file_key"])
    data = {"org_id": org_id, "message_id": str(message_id), "last_message": newest.get("message"),
            "last_message_at": _iso(newest.get("created_at"))}
    await _publish_message_deleted(data, admin_ids)
    return data


async def op_mark_read(org_id: str) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if not _thread_row(cur, org_id):
                raise HTTPException(status_code=404, detail="No conversation for this organization")
            marked = _mark_read(cur, org_id, "org", get_utc_now())
            admin_ids = _org_admin_ids(cur, org_id) if marked else []
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("operator mark read", exc)
    finally:
        return_connection(conn)
    if marked:
        try:
            await realtime.publish_to_users(admin_ids, {"type": "live_chat_read",
                                                        "data": {"org_id": org_id, "reader": "support"}})
        except Exception as exc:
            logger.warning("live chat read frame failed: %s", exc)
    return {"success": True, "marked": marked}


async def op_set_status(org_id: str, status: str) -> Dict[str, Any]:
    status = (status or "").strip().lower()
    if status not in THREAD_STATUSES:
        raise HTTPException(status_code=400, detail="status must be open or closed")
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE live_chat_thread SET status = %s, updated_at = %s WHERE org_id = %s RETURNING thread_id",
                (status, get_utc_now(), org_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="No conversation for this organization")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("status change", exc)
    finally:
        return_connection(conn)
    try:
        await realtime.publish_to_operators({"type": "live_chat_status", "data": {"org_id": org_id, "status": status}})
    except Exception as exc:
        logger.warning("live chat status frame failed: %s", exc)
    return {"success": True, "status": status}


async def op_unread_count() -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS unread_messages, COUNT(DISTINCT org_id) AS unread_threads "
                "FROM live_chat_message WHERE sender_type = 'org' AND read_at IS NULL"
            )
            row = cur.fetchone() or {}
        return {"success": True, "unread_threads": int(row.get("unread_threads") or 0),
                "unread_messages": int(row.get("unread_messages") or 0)}
    except HTTPException:
        raise
    except Exception as exc:
        if _degrade_if_tables_missing(conn, "unread count", exc):
            return {"success": True, "unread_threads": 0, "unread_messages": 0, "setup_required": True}
        raise _unexpected("unread count", exc)
    finally:
        return_connection(conn)
