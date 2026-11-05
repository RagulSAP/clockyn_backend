from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from core_functions.gen_payment_functions import verify_payment_process

router = APIRouter(prefix="/payments", tags=["payments"])

class VerifyPaymentRequest(BaseModel):
    user_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    email: Optional[str] = None
    wants_gst_invoice: Optional[bool] = False

@router.post("/verify")
async def verify_payment(data: VerifyPaymentRequest):
    return await verify_payment_process(
        data.user_id,
        data.razorpay_order_id,
        data.razorpay_payment_id,
        data.razorpay_signature,
        override_email=data.email,
    )