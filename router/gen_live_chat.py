"""Live Chat, OPERATOR side: the Clockyn support team in the web console (UI_ADMIN_APP).

HTTP routes live under /gen-dashboard/live-chat (an OPERATOR route class: `auth_policy.OPERATOR_RULES`), each guarded by
`operator_auth.require_operator` (the operator's e-mail in the legacy admin header, or the operator key). The console's realtime
channel is the WebSocket /ws/support-ops?admin_id=<operator e-mail>: every open console registers under ONE key in the
shared socket registry, so a single publish reaches all of them (frames: live_chat_message / live_chat_read /
live_chat_status / live_chat_message_deleted, see services/realtime.py). Browsers cannot set headers on a WebSocket, hence the query parameter; like
the other open operator routes, the operator is only checked when `operator_auth.enforced()`.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, WebSocket, WebSocketDisconnect

from core_functions import live_chat_functions as live
from core_functions import operator_auth
from core_functions.operator_auth import require_operator
from services.realtime import SUPPORT_OPS_KEY
from socket_manager import manager

# No router prefix: the WebSocket path is /ws/support-ops and a router-level operator dependency (HTTP `Request`) cannot
# be applied to a WebSocket route, so every HTTP route carries the guard itself.
router = APIRouter(tags=["live-chat-operator"])
logger = logging.getLogger(__name__)
WS_CLOSE_FORBIDDEN = 4403


def _admin_id(request: Request) -> Optional[str]:
    return request.headers.get(operator_auth.LEGACY_HEADER)


@router.get("/gen-dashboard/live-chat/threads", dependencies=[Depends(require_operator)])
async def live_chat_threads_route(status: str = Query("all"), q: Optional[str] = Query(None)):
    return await live.op_list_threads(status, q)


@router.get("/gen-dashboard/live-chat/unread-count", dependencies=[Depends(require_operator)])
async def live_chat_unread_count_route():
    return await live.op_unread_count()


@router.get("/gen-dashboard/live-chat/threads/{org_id}/messages", dependencies=[Depends(require_operator)])
async def live_chat_thread_messages_route(org_id: str):
    return await live.op_get_messages(org_id)


@router.post("/gen-dashboard/live-chat/threads/{org_id}/messages", dependencies=[Depends(require_operator)])
async def live_chat_reply_route(org_id: str, req: live.OperatorSendRequest, request: Request):
    return await live.op_send_text(org_id, req.message, _admin_id(request))


@router.post("/gen-dashboard/live-chat/threads/{org_id}/messages/file", dependencies=[Depends(require_operator)])
async def live_chat_reply_file_route(org_id: str, request: Request, file: UploadFile = File(...)):
    return await live.op_send_file(org_id, file, _admin_id(request))


@router.delete("/gen-dashboard/live-chat/threads/{org_id}/messages/{message_id}", dependencies=[Depends(require_operator)])
async def live_chat_delete_message_route(org_id: str, message_id: str):
    return await live.op_delete_message(org_id, message_id)


@router.put("/gen-dashboard/live-chat/threads/{org_id}/read", dependencies=[Depends(require_operator)])
async def live_chat_read_route(org_id: str):
    return await live.op_mark_read(org_id)


@router.put("/gen-dashboard/live-chat/threads/{org_id}/status", dependencies=[Depends(require_operator)])
async def live_chat_status_route(org_id: str, req: live.OperatorStatusRequest):
    return await live.op_set_status(org_id, req.status)


@router.websocket("/ws/support-ops")
async def support_ops_socket(websocket: WebSocket, admin_id: str = Query("")):
    admin_id = (admin_id or "").strip()
    if operator_auth.enforced():
        role = None
        if admin_id:
            try:
                role = await asyncio.to_thread(operator_auth._lookup, admin_id)
            except Exception:
                logger.exception("support-ops operator lookup failed")
        if role is None:
            logger.warning("support-ops socket refused: unknown operator")
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
    await manager.connect(SUPPORT_OPS_KEY, websocket)
    try:
        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("support-ops socket error: %s", exc)
    finally:
        manager.disconnect(SUPPORT_OPS_KEY, websocket)
