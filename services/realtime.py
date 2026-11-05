"""Realtime delivery: every live event the app consumes goes through here.

The app opens exactly one socket, `/ws/chat/{user_id}` (registry: `socket_manager.manager`). All events are published on
it. The legacy `/ws/badge/{user_id}` socket (registry: `router.ws_badge`) and the SSE session channel
(`core_functions.session_registry`) keep working: `badge_update` and `request_submitted` are mirrored to `/ws/badge`,
and `force_logout` still goes to the SSE queue. Nothing here raises: a failed delivery is logged and skipped.

Frames (the shapes the app's `interpretSocketFrame` / `useChatSocket` read):
  badge_update       {"type":"badge_update","notification":{id,title,body,type,image_url}}   (notification optional)
  request_submitted  {"type":"request_submitted","data":{request_id,request_type}}
  announcement       {"type":"announcement","target":T,"data":{announcement_id,title,target,org_id}}
  read_update        {"type":"read_update","data":{reader_id,group_id,message_id,read_count,total_members}}
  group_created      {"type":"group_created","data":{group_id,group_name,created_by}}
  group_updated      {"type":"group_updated","data":{group_id,member_count}}
  force_logout       {"type":"force_logout","data":{user_id}}
"""
import asyncio
import logging
import time
from typing import Any, Dict, Iterable, List, Optional

from core_functions.db_function import get_db_connection, return_connection
from services import push_service
from socket_manager import manager

logger = logging.getLogger(__name__)

# Frame types also delivered to the legacy /ws/badge socket (what it carried before the app moved to /ws/chat).
LEGACY_BADGE_TYPES = frozenset({"badge_update", "request_submitted"})
# More than this many per-message read frames in one call collapse into one refetch hint per sender.
MAX_READ_FRAMES_PER_CALL = 50


# ---------------------------------------------------------------------------- frame builders (pure)
def badge_frame(notification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    frame: Dict[str, Any] = {"type": "badge_update"}
    if notification:
        frame["notification"] = notification
    return frame


def punch_notification(user_name: str, punch_type: str, punch_time: str, image_url: Optional[str] = None,
                       now_ms: Optional[int] = None) -> Dict[str, Any]:
    punched_in = punch_type == "punch_in"
    return {
        "id": f"punch-{now_ms if now_ms is not None else int(time.time() * 1000)}",
        "title": "Punched In" if punched_in else "Punched Out",
        "body": f"{user_name} {'punched in' if punched_in else 'punched out'} at {punch_time}",
        "type": punch_type,
        "image_url": image_url or None,
    }


def request_submitted_frame(request_id: Optional[str], request_type: Optional[str]) -> Dict[str, Any]:
    return {"type": "request_submitted", "data": {"request_id": request_id, "request_type": request_type}}


def announcement_frame(announcement_id: str, title: Optional[str], target: str, org_id: Optional[str]) -> Dict[str, Any]:
    return {"type": "announcement", "target": target,
            "data": {"announcement_id": announcement_id, "title": title, "target": target, "org_id": org_id}}


def force_logout_frame(user_id: str) -> Dict[str, Any]:
    return {"type": "force_logout", "data": {"user_id": str(user_id)}}


# ---------------------------------------------------------------------------- publishing
def is_online(user_id: str) -> bool:
    return manager.is_online(user_id)


def offline_user_ids(user_ids: Iterable[str], exclude: Optional[str] = None) -> List[str]:
    """The users (minus `exclude`, de-duplicated, order kept) with no open chat socket: they need a push instead."""
    out: List[str] = []
    for uid in user_ids:
        uid = str(uid)
        if uid == str(exclude) or uid in out or manager.is_online(uid):
            continue
        out.append(uid)
    return out


async def _legacy_send(user_id: str, frame: Dict[str, Any]) -> bool:
    try:
        from router.ws_badge import send_legacy_frame
        return await send_legacy_frame(str(user_id), frame)
    except Exception as e:
        logger.warning("[Realtime] legacy badge socket send failed for %s: %s", user_id, e)
        return False


async def publish_to_user(user_id: str, frame: Dict[str, Any]) -> int:
    """Send one frame to every chat socket of the user (+ the legacy badge socket for badge-type frames).
    Returns the number of chat sockets reached."""
    reached = 0
    try:
        reached = await manager.send_event(str(user_id), frame)
    except Exception as e:
        logger.warning("[Realtime] chat socket send failed for %s: %s", user_id, e)
    if frame.get("type") in LEGACY_BADGE_TYPES:
        await _legacy_send(user_id, frame)
    return reached or 0


async def publish_to_users(user_ids: Iterable[str], frame: Dict[str, Any]) -> int:
    ids = list(dict.fromkeys(str(u) for u in user_ids if u))
    if not ids:
        return 0
    results = await asyncio.gather(*(publish_to_user(u, frame) for u in ids), return_exceptions=True)
    return sum(r for r in results if isinstance(r, int))


async def notify_badge_update(user_id: str, event_type: str = "badge_update", notification: Optional[Dict[str, Any]] = None,
                              data: Optional[Dict[str, Any]] = None) -> int:
    """Generic badge-type event (the legacy `ws_badge.notify_badge_update` signature plus optional payload)."""
    frame: Dict[str, Any] = {"type": event_type}
    if notification:
        frame["notification"] = notification
    if data:
        frame["data"] = data
    return await publish_to_user(user_id, frame)


# ---------------------------------------------------------------------------- recipients
def org_admin_manager_user_ids(org_id: str, exclude_user_id: Optional[str] = None) -> List[str]:
    """The organisation's admin (role 1, via org_info.parent_id) and its managers (role 2, parent_id = org). Blocking."""
    if not org_id:
        return []
    sql = """
        SELECT DISTINCT u.user_id FROM users u
        WHERE (
            (u.role_id = 1 AND EXISTS (SELECT 1 FROM org_info o WHERE o.org_id = %s AND o.parent_id = u.user_id))
            OR (u.role_id = 2 AND u.parent_id = %s)
        )
    """
    params: List[Any] = [org_id, org_id]
    if exclude_user_id:
        sql += " AND u.user_id <> %s"
        params.append(exclude_user_id)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return [r[0] for r in cur.fetchall()]
    finally:
        return_connection(conn)


def _employee_name(user_id: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------- feature events
async def notify_punch_in_out(user_id: str, org_id: str, punch_type: str, punch_time: str,
                              image_url: Optional[str] = None) -> None:
    """Punch in/out of a staff member: `badge_update` (with `notification`) on the sockets and a push, for the
    organisation's admin and managers (not the punching user). Never raises."""
    try:
        loop = asyncio.get_running_loop()
        name = await loop.run_in_executor(None, _employee_name, user_id)
        if name is None:
            logger.warning("[Notify] employee %s not found - punch notification skipped", user_id)
            return
        recipients = await loop.run_in_executor(None, org_admin_manager_user_ids, org_id, user_id)
        if not recipients:
            logger.warning("[Notify] no admin/manager recipients for org %s - punch notification skipped", org_id)
            return

        await publish_to_users(recipients, badge_frame(punch_notification(name, punch_type, punch_time, image_url)))

        title = "Punch In" if punch_type == "punch_in" else "Punch Out"
        body = f"{name} {punch_type.replace('_', ' ')} at {punch_time}"
        data = {"type": punch_type, "user_id": user_id, "image_url": image_url or ""}
        await push_service.send_push_to_users(recipients, title, body, data, image_url)
    except Exception as e:
        logger.error("[Notify] punch notification failed: %s", e)


async def publish_request_submitted(user_ids: Iterable[str], request_id: Optional[str], request_type: Optional[str]) -> int:
    return await publish_to_users(user_ids, request_submitted_frame(request_id, request_type))


async def deliver_announcement(user_ids: Iterable[str], announcement_id: str, title: Optional[str], message: str,
                               target: str, org_id: Optional[str], push_title: Optional[str] = None,
                               push_data: Optional[Dict[str, Any]] = None) -> None:
    """Socket frame + push to each recipient of an announcement. Never raises."""
    try:
        ids = list(dict.fromkeys(str(u) for u in user_ids if u))
        if not ids:
            return
        await publish_to_users(ids, announcement_frame(announcement_id, title, target, org_id))
        data = push_data if push_data is not None else {
            "type": "announcement", "announcement_id": announcement_id, "org_id": org_id, "target": target}
        await push_service.send_push_to_users(ids, push_title or title or "", (message or "")[:100], data)
    except Exception as e:
        logger.error("[Notify] announcement delivery failed: %s", e)


async def publish_group_read_updates(reader_id: str, group_id: str, updates: List[Dict[str, Any]]) -> int:
    """`read_update` frames to the sender of each newly read group message (never to the reader).
    Above MAX_READ_FRAMES_PER_CALL messages: one frame per sender without message_id/counts (the app refetches)."""
    updates = [u for u in updates if u.get("sender_id") != reader_id]
    sent = 0
    if len(updates) > MAX_READ_FRAMES_PER_CALL:
        for sender_id in dict.fromkeys(u["sender_id"] for u in updates):
            sent += await publish_to_user(sender_id, {
                "type": "read_update", "data": {"reader_id": reader_id, "group_id": group_id}})
        return sent
    for u in updates:
        sent += await publish_to_user(u["sender_id"], {
            "type": "read_update",
            "data": {"reader_id": reader_id, "group_id": group_id, "message_id": u["message_id"],
                     "read_count": u["read_count"], "total_members": u["total_members"]}})
    return sent


async def publish_group_members_added(group_id: str, group_name: Optional[str], created_by: Optional[str],
                                      new_member_ids: List[str], existing_member_ids: List[str], member_count: int) -> None:
    """New members get `group_created`; the members already in the group get `group_updated`."""
    await publish_to_users(new_member_ids, {"type": "group_created", "data": {
        "group_id": group_id, "group_name": group_name, "created_by": created_by}})
    await publish_to_users(existing_member_ids, {"type": "group_updated", "data": {
        "group_id": group_id, "member_count": member_count}})


async def force_logout(user_id: str, push_tokens: Optional[List[str]] = None, sse: bool = True) -> None:
    """Tell a user's device(s) to sign out: SSE queue (legacy), `force_logout` frame on the chat socket, and a silent
    FCM data message `{"type":"force_logout","user_id":...}` (the app's NEW-7 handler).
    `push_tokens` = explicit tokens to notify (the previous device on a takeover); None = every token of the user."""
    uid = str(user_id)
    try:
        if sse:
            from core_functions.session_registry import force_logout_sse
            await force_logout_sse(uid)
    except Exception as e:
        logger.warning("[Realtime] SSE force_logout failed for %s: %s", uid, e)
    try:
        await manager.send_event(uid, force_logout_frame(uid))
    except Exception as e:
        logger.warning("[Realtime] force_logout frame failed for %s: %s", uid, e)
    try:
        data = {"type": "force_logout", "user_id": uid}
        if push_tokens is None:
            await push_service.send_push_to_user(uid, "", "", data, silent=True)
        elif push_tokens:
            await push_service.send_push_to_tokens(push_tokens, "", "", data, silent=True)
    except Exception as e:
        logger.warning("[Realtime] force_logout push failed for %s: %s", uid, e)
