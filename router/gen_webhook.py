from fastapi import APIRouter, Request, Header
from core_functions.gen_webhook_functions import handle_razorpay_webhook, handle_msg91_whatsapp_webhook

router = APIRouter(prefix="/webhook", tags=["webhook"])

@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str = Header(None, alias='X-Razorpay-Signature'),
    x_razorpay_event_id: str = Header(None, alias='X-Razorpay-Event-Id'),
):
    # The signature covers the exact bytes Razorpay sent: verify on the raw body, never on re-serialised JSON.
    raw_body = await request.body()
    return await handle_razorpay_webhook(raw_body, x_razorpay_signature, x_razorpay_event_id)


@router.post("/msg91/whatsapp")
async def msg91_whatsapp_webhook(request: Request):
    payload = await request.json()
    return await handle_msg91_whatsapp_webhook(payload)
