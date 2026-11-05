from fastapi import APIRouter, Depends, Query, HTTPException, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from typing import Optional, List, Dict
import asyncio
import logging
from datetime import datetime, timezone
from core_functions.gen_plans_functions import check_plan_feature, plan_gate
from services import push_service, realtime
from utils.uploads import read_upload, chat_upload_max_mb
from core_functions.auth_dependency import authenticate_websocket
from core_functions.chat_functions import (
    # Individual
    get_chat_list, get_chat_messages, send_chat_message, mark_as_read, delete_chat,
    search_users_for_chat,
    SendMessageRequest, MarkReadRequest,
    # Group
    get_group_chat_list, create_group, get_group_messages, send_group_message, get_group_members,
    mark_group_as_read, add_group_members, check_chat_peers,
    CreateGroupRequest, SendGroupMessageRequest, MarkGroupReadRequest, AddGroupMembersRequest,
    # Announcement
    send_announcement_all, get_announcements_all,
    send_announcement_branch_dept, get_announcements_branch_dept,
    get_announcements_for_employee,
    SendAnnouncementRequest,
)

router = APIRouter(tags=["chat"])
# D-12: every chat / group-chat route answers a real 403 when the organisation's plan has lapsed (the app already understands it).
_chat_gate = [Depends(plan_gate('chat', is_admin=False))]
logger = logging.getLogger(__name__)


async def _send_fcm_push(token: str, title: str, body: str, data: dict = None):
    """Legacy name: one raw FCM token. The single sender lives in services.push_service."""
    await push_service.send_push(token, title, body, data or {})


def _attachment_or_text(message_type: str, message: str) -> str:
    return message if message_type == 'text' else '📎 Sent an attachment'



def _format_ws_timestamp(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(value)


def _normalize_ws_message(result: dict) -> dict:
    payload = dict(result)
    if payload.get("message_id") is not None:
        payload["message_id"] = str(payload["message_id"])
    payload["timestamp"] = _format_ws_timestamp(payload.get("timestamp"))
    message_type = str(payload.get("message_type") or "text").strip().lower()
    if message_type not in {"text", "image", "file"}:
        message_type = "text"
    payload["message_type"] = message_type
    return payload


def _build_ws_event(event_type: str, result: dict) -> dict:
    return {
        "type": event_type,
        "data": _normalize_ws_message(result),
    }


def _caller_id(request: Request) -> Optional[str]:
    """User id proven by the bearer token (None when the request carries no valid token)."""
    identity = getattr(request.state, "identity", None)
    return identity.user_id if identity else None


def _must_be_caller(request: Request, user_id: str) -> None:
    """403 when a token is present and `user_id` is somebody else (an admin may not read a member's chat as that member)."""
    caller = _caller_id(request)
    if caller is not None and caller != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")


async def _get_group_member_ids(group_id: str) -> list[str]:
    members = await get_group_members(group_id)
    return [member["user_id"] for member in members]


def _push_chat_to_user(user_id: str, title: str, body: str, data: dict):
    """Fire-and-forget push to every device of one user (the sender never waits for FCM)."""
    asyncio.create_task(push_service.send_push_to_user(user_id, title, body, data))


def _push_group_to_offline_members(member_ids: list, sender_id: str, title: str, body: str, data: dict):
    """Push to the group members that have no open socket (online members got the socket frame)."""
    offline = realtime.offline_user_ids(member_ids, exclude=sender_id)
    if offline:
        asyncio.create_task(push_service.send_push_to_users(offline, title, body, data))

# Import centralized manager
from socket_manager import manager as shared_manager

@router.websocket("/ws/chat/{user_id}")
async def chat_websocket(websocket: WebSocket, user_id: str):
    if not await authenticate_websocket(websocket, user_id):  # REQUIRE_AUTH=1: ?token= of this user, else close 4401
        return
    await shared_manager.connect(user_id, websocket)
    await _keep_alive(user_id, websocket)


@router.websocket("/ws/group-chat/{group_id}/{user_id}")
async def group_chat_websocket(websocket: WebSocket, group_id: str, user_id: str):
    if not await authenticate_websocket(websocket, user_id):
        return
    # For simplicity and cross-socket delivery, group members should also be
    # on the main shared_manager.
    await shared_manager.connect(user_id, websocket)
    await _keep_alive(user_id, websocket)


async def _keep_alive(user_id: str, websocket: WebSocket):
    """Read client frames until the socket closes: the app's text `ping` is answered with `pong` (the client
    ignores non-JSON frames); any end of the connection removes exactly this socket from the registry."""
    try:
        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[WS] chat socket error for user {user_id}: {e}")
    finally:
        shared_manager.disconnect(user_id, websocket)


# ═══════════════════ INDIVIDUAL CHAT ═══════════════════

@router.get("/chat/search/{user_id}", dependencies=_chat_gate)
async def chat_search_route(user_id: str, q: str = Query(...)):
    try:
        return await search_users_for_chat(user_id, q)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/chat/list/{user_id}")
async def chat_list_route(user_id: str, org_id: str = Query(...)):
    await check_plan_feature(org_id, 'chat', is_admin=False)  # a plan block answers a real 403
    try:
        return await get_chat_list(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/chat/messages/{user_id}/{other_user_id}", dependencies=_chat_gate)
async def chat_messages_route(user_id: str, other_user_id: str):
    try:
        return await get_chat_messages(user_id, other_user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/chat/send", dependencies=_chat_gate)
async def chat_send_route(req: SendMessageRequest):
    try:
        result = await send_chat_message(req)
        await shared_manager.broadcast_chat_message(req.sender_id, req.receiver_id, result)

        # Always send push notification (every device of the receiver) so background/killed app gets it
        _push_chat_to_user(req.receiver_id, result.get('sender_name') or 'Someone',
                           _attachment_or_text(req.message_type, req.message),
                           {'type': 'new_message', 'sender_id': req.sender_id, 'receiver_id': req.receiver_id,
                            'message_id': result.get('message_id')})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/chat/mark-read", dependencies=_chat_gate)
async def chat_mark_read_route(req: MarkReadRequest):
    try:
        result = await mark_as_read(req)
        # Notify sender that their messages have been read (blue ticks)
        await realtime.publish_to_user(req.sender_id, {
            "type": "read_update",
            "data": {"reader_id": req.receiver_id, "sender_id": req.sender_id}
        })
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.delete("/chat/delete/{user_id}/{other_user_id}", dependencies=_chat_gate)
async def chat_delete_route(user_id: str, other_user_id: str):
    try:
        return await delete_chat(user_id, other_user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ═══════════════════ GROUP CHAT ═══════════════════

@router.get("/group-chat/list/{user_id}")
async def group_list_route(user_id: str, org_id: str = Query(...)):
    await check_plan_feature(org_id, 'chat', is_admin=False)  # a plan block answers a real 403
    try:
        return await get_group_chat_list(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/group-chat/create", dependencies=_chat_gate)
async def group_create_route(req: CreateGroupRequest, request: Request):
    try:
        _must_be_caller(request, req.created_by)
        result = await create_group(req)
        for member_id in req.member_ids:
            if member_id == req.created_by:
                continue
            await realtime.publish_to_user(member_id, {"type": "group_created", "data": result})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/group-chat/messages/{group_id}", dependencies=_chat_gate)
async def group_messages_route(group_id: str, request: Request, user_id: str = Query(...)):
    try:
        _must_be_caller(request, user_id)
        return await get_group_messages(group_id, user_id)       # 404 unless user_id is a participant of the group
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/group-chat/send", dependencies=_chat_gate)
async def group_send_route(req: SendGroupMessageRequest):
    try:
        from core_functions.db_function import get_db_connection, return_connection
        from psycopg2.extras import RealDictCursor
        conn = get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT 1 FROM group_members WHERE group_id = %s AND member_id = %s AND status = 1",
                    (req.group_id, req.sender_id)
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=403, detail="User is not a member of this group")
        finally:
            return_connection(conn)

        result = await send_group_message(req)
        member_ids = await _get_group_member_ids(req.group_id)
        # Use standardized broadcaster which handles type-safe lookups
        await shared_manager.broadcast_group_message(req.sender_id, member_ids, result)

        # Push notify offline group members (every device of each)
        _push_group_to_offline_members(
            member_ids, req.sender_id, result.get('sender_name', 'Someone'),
            _attachment_or_text(req.message_type, req.message),
            {'type': 'new_group_message', 'group_id': req.group_id, 'sender_id': req.sender_id,
             'message_id': result.get('message_id')})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/group-chat/members/{group_id}", dependencies=_chat_gate)
async def group_members_route(group_id: str, request: Request):
    try:
        return await get_group_members(group_id, _caller_id(request))   # with a token: 404 unless a participant
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.put("/group-chat/mark-read", dependencies=_chat_gate)
async def group_mark_read_route(req: MarkGroupReadRequest, request: Request):
    try:
        _must_be_caller(request, req.user_id)
        result = await mark_group_as_read(req)
        # One read_update per newly read message, sent to that message's sender with the fresh counts. The reader
        # never receives an update about their own read (their own messages are never marked by them).
        await realtime.publish_group_read_updates(req.user_id, req.group_id, result.pop("read_updates", []))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/group-chat/add-members", dependencies=_chat_gate)
async def group_add_members_route(req: AddGroupMembersRequest, request: Request):
    try:
        # with a token the caller must be a member / the creator of the group; every added user must be of its organisation
        result = await add_group_members(req, _caller_id(request))
        # Realtime only (not part of the HTTP body): new members get `group_created`, the others `group_updated`.
        change = result.pop("membership_change", None)
        if change:
            await realtime.publish_group_members_added(
                req.group_id, change["group_name"], change["created_by"], change["new_member_ids"],
                change["existing_member_ids"], change["member_count"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ═══════════════════ ANNOUNCEMENT TO ALL ═══════════════════

@router.post("/announcement/send-all")
async def announcement_send_all_route(req: SendAnnouncementRequest):
    try:
        return await send_announcement_all(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/announcement/all/{org_id}")
async def announcement_all_route(org_id: str):
    try:
        return await get_announcements_all(org_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ═══════════════════ ANNOUNCEMENT BY BRANCH & DEPT ═══════════════════

@router.post("/announcement/send-branch-dept")
async def announcement_send_branch_dept_route(req: SendAnnouncementRequest):
    try:
        return await send_announcement_branch_dept(req)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/announcement/branch-dept/{org_id}")
async def announcement_branch_dept_route(
    org_id: str,
    branch_id: Optional[int] = Query(None),
    dept_id: Optional[str] = Query(None)
):
    try:
        return await get_announcements_branch_dept(org_id, branch_id, dept_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ═══════════════════ CHAT FILE UPLOAD ═══════════════════

@router.post("/chat/send-file", dependencies=_chat_gate)
async def chat_send_file_route(
    sender_id: str = Form(...),
    receiver_id: str = Form(...),
    file: UploadFile = File(...),
    sender_name: Optional[str] = Form(None),
):
    """Upload attachment and send as individual chat message (pin button)."""
    try:
        from ml.s3 import upload_chat_file
        from core_functions.db_function import get_db_connection, return_connection
        import uuid
        from psycopg2.extras import RealDictCursor
        from utils.timezone import get_utc_now

        check_chat_peers(sender_id, receiver_id)       # 404 unless the receiver is a user of the sender's organisation
        file_bytes = await read_upload(file, chat_upload_max_mb(), None)
        s3_key, presigned_url = upload_chat_file(file_bytes, file.filename, file.content_type or "application/octet-stream")
        message_kind = "image" if (file.content_type or "").startswith("image/") else "file"
        display_name = file.filename or s3_key.split('/')[-1]

        chat_id = str(uuid.uuid4())
        now = get_utc_now()
        conn = get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO chat (chat_id, sender_id, receiver_id, message, message_type, file_url, date_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING chat_id AS message_id, sender_id, receiver_id, message, message_type, file_url, date_time AS timestamp
                """, (chat_id, sender_id, receiver_id, display_name, message_kind, s3_key, now))
                conn.commit()
                result = _normalize_ws_message(dict(cur.fetchone()))
                result['file_url'] = presigned_url  # return presigned URL to frontend
                result['is_read'] = False
                result['is_delivered'] = False
                result['filename'] = file.filename
                result['content_type'] = file.content_type
                cur.execute('SELECT name FROM users WHERE user_id = %s', (sender_id,))
                sender_row = cur.fetchone()
                result['sender_name'] = sender_row['name'] if sender_row else 'Someone'
        finally:
            return_connection(conn)

        await shared_manager.broadcast_chat_message(sender_id, receiver_id, result)
        _push_chat_to_user(receiver_id, sender_name or result.get('sender_name') or 'Someone',
                           '\U0001f4ce Sent an attachment',
                           {'type': 'new_message', 'sender_id': sender_id, 'receiver_id': receiver_id,
                            'message_id': result.get('message_id')})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/group-chat/send-file", dependencies=_chat_gate)
async def group_send_file_route(
    sender_id: str = Form(...),
    group_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Upload attachment and send as group chat message (pin button)."""
    try:
        from ml.s3 import upload_chat_file
        from core_functions.db_function import get_db_connection, return_connection
        import uuid
        from psycopg2.extras import RealDictCursor
        from utils.timezone import get_utc_now

        file_bytes = await read_upload(file, chat_upload_max_mb(), None)
        s3_key, presigned_url = upload_chat_file(file_bytes, file.filename, file.content_type or "application/octet-stream")
        message_kind = "image" if (file.content_type or "").startswith("image/") else "file"
        display_name = file.filename or s3_key.split('/')[-1]

        chat_id = str(uuid.uuid4())
        now = get_utc_now()
        conn = get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT 1 FROM group_members WHERE group_id = %s AND member_id = %s AND status = 1
                """, (group_id, sender_id))
                if not cur.fetchone():
                    raise HTTPException(status_code=403, detail="User is not a member of this group")

                cur.execute("""
                    INSERT INTO chat (chat_id, sender_id, group_id, message, message_type, file_url, date_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING chat_id AS message_id, group_id, sender_id, message, message_type, file_url, date_time AS timestamp
                """, (chat_id, sender_id, group_id, display_name, message_kind, s3_key, now))
                conn.commit()
                result = _normalize_ws_message(dict(cur.fetchone()))
                result['file_url'] = presigned_url  # return presigned URL to frontend

                cur.execute("SELECT name FROM users WHERE user_id = %s", (sender_id,))
                sender = cur.fetchone()
                result['sender_name'] = sender['name'] if sender else ''
                result['is_read'] = False
                result['read_count'] = 0
                result['filename'] = file.filename
                result['content_type'] = file.content_type
        finally:
            return_connection(conn)

        member_ids = await _get_group_member_ids(group_id)
        await shared_manager.broadcast_group_message(sender_id, member_ids, result)
        _push_group_to_offline_members(
            member_ids, sender_id, result.get('sender_name', 'Someone'), '\U0001f4ce Sent an attachment',
            {'type': 'new_group_message', 'group_id': group_id, 'sender_id': sender_id,
             'message_id': result.get('message_id')})
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

# ═══════════════════ EMPLOYEE: GET MY ANNOUNCEMENTS ═══════════════════

@router.get("/announcement/employee/{user_id}")
async def announcement_employee_route(user_id: str):
    """Get all announcements visible to this employee (org-wide + their branch/dept)"""
    try:
        return await get_announcements_for_employee(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[chat] request failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
