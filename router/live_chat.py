"""Live Chat, APP side: the organisation's admin writes to the Clockyn support team (see core_functions/live_chat_functions.py).

Bearer-token routes; the caller must be a role-1 user of `org_id` (403 otherwise; `auth_policy` also restricts `/live-chat/*`
to administrators). Not plan-gated on purpose: a lapsed customer must still reach support. Operators answer through
router/gen_live_chat.py.
"""
from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from core_functions import live_chat_functions as live
from utils import rate_limit

router = APIRouter(tags=["live-chat"])


@router.get("/live-chat/messages/{org_id}")
async def live_chat_messages_route(org_id: str, user_id: str = Query(...)):
    return await live.app_get_messages(org_id, user_id)


@router.get("/live-chat/unread/{org_id}")
async def live_chat_unread_route(org_id: str, user_id: str = Query(...)):
    return await live.app_unread(org_id, user_id)


@router.post("/live-chat/send")
async def live_chat_send_route(req: live.AppSendRequest, request: Request):
    rate_limit.check_user("LIVE_CHAT_SEND", request, req.user_id)
    return await live.app_send_text(req)


@router.post("/live-chat/send-file")
async def live_chat_send_file_route(
    request: Request,
    org_id: str = Form(...),
    user_id: str = Form(...),
    file: UploadFile = File(...),
):
    rate_limit.check_user("LIVE_CHAT_SEND", request, user_id)
    return await live.app_send_file(org_id, user_id, file)


@router.put("/live-chat/mark-read")
async def live_chat_mark_read_route(req: live.AppReadRequest):
    return await live.app_mark_read(req)
