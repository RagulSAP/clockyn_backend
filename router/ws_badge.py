from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Any, Dict
import asyncio
import logging
from core_functions.auth_dependency import authenticate_websocket

router = APIRouter(tags=["WebSocket"])
logger = logging.getLogger(__name__)

# LEGACY socket: the Clockyn app does not open it (it uses /ws/chat/{user_id}). Kept for older clients.
# user_id -> active WebSocket connection (one per user, last connection wins)
_connections: Dict[str, WebSocket] = {}

SEND_TIMEOUT_SECONDS = 5.0


async def send_legacy_frame(user_id: str, frame: Dict[str, Any]) -> bool:
    """Deliver one frame to the user's legacy badge socket, if any. Used by services.realtime."""
    ws = _connections.get(user_id)
    if not ws:
        return False
    try:
        await asyncio.wait_for(ws.send_json(frame), timeout=SEND_TIMEOUT_SECONDS)
        return True
    except Exception:
        if _connections.get(user_id) is ws:
            _connections.pop(user_id, None)
        return False


async def notify_badge_update(user_id: str, event_type: str = "badge_update"):
    """Push a badge-type event to a user. Kept for old callers: it now goes through services.realtime, which
    delivers on the chat socket the app opens AND on this legacy socket."""
    from services import realtime
    await realtime.notify_badge_update(user_id, event_type)


async def notify_org_badge_update(org_id: str, exclude_user_id: str = None):
    """badge_update to the admin and managers of an org (resolved from the database; the old version ignored org_id)."""
    from services import realtime
    loop = asyncio.get_running_loop()
    user_ids = await loop.run_in_executor(None, realtime.org_admin_manager_user_ids, org_id, exclude_user_id)
    await realtime.publish_to_users(user_ids, realtime.badge_frame())


@router.websocket("/ws/badge/{user_id}")
async def badge_websocket(websocket: WebSocket, user_id: str):
    if not await authenticate_websocket(websocket, user_id):  # REQUIRE_AUTH=1: ?token= of this user, else close 4401
        return
    await websocket.accept()
    _connections[user_id] = websocket
    logger.info(f"[WS] Badge connection opened for user {user_id}")
    try:
        while True:
            # Keep connection alive — client sends ping, we send pong
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info(f"[WS] Badge connection closed for user {user_id}")
    except Exception as e:
        logger.warning(f"[WS] Badge connection error for user {user_id}: {e}")
    finally:
        # Only remove our own entry: a newer socket of the same user may already have replaced it.
        if _connections.get(user_id) is websocket:
            _connections.pop(user_id, None)
