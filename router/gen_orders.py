import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel
from core_functions.gen_orders_functions import create_new_order, quote_order
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor

router = APIRouter(prefix="/orders", tags=["orders"])
logger = logging.getLogger(__name__)


class CreateOrderRequest(BaseModel):
    user_id: str
    org_id: str
    plan_id: int
    # Display value from the app. The server recomputes the price and ignores this (a difference is only logged).
    amount: Optional[float] = None
    # Buy / change plan: staff count. Staff-only upgrade: the EXTRA staff.
    staff_limit: int = 10
    coupon_code: Optional[str] = None
    # new | renew | upgrade. Older app builds omit it; the server then infers it (see core_functions/pricing.pick_mode).
    mode: Optional[str] = None
    # Refer & Earn: use one of the organisation's AVAILABLE referral rewards (the server picks it; never sent by the app).
    apply_referral: bool = False


class QuoteOrderRequest(BaseModel):
    user_id: str
    org_id: str
    # Required for mode=new; renew / upgrade always use the organisation's CURRENT plan.
    plan_id: Optional[int] = None
    staff_limit: int = 10
    coupon_code: Optional[str] = None
    mode: Optional[str] = None
    apply_referral: bool = False


@router.post("/quote")
async def quote(data: QuoteOrderRequest):
    """Price breakdown for the checkout screen, computed from the database (same code as /orders/create)."""
    return await quote_order(
        data.user_id,
        data.org_id,
        data.plan_id,
        data.staff_limit,
        data.coupon_code,
        data.mode,
        data.apply_referral,
    )


@router.post("/create")
async def create_order(data: CreateOrderRequest):
    return await create_new_order(
        data.user_id,
        data.org_id,
        data.plan_id,
        data.amount,
        data.staff_limit,
        data.coupon_code,
        data.mode,
        data.apply_referral,
    )

@router.get("/check_first_payment")
async def check_first_payment(user_id: str = Query(...)):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM gen_orders WHERE user_id = %s AND status = 'PAID'",
            (user_id,)
        )
        row = cursor.fetchone()
        return {"is_first_payment": row['cnt'] == 0}
    except Exception as e:
        return {"is_first_payment": False}
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)

@router.get("/history/{org_id}")
async def get_order_history(org_id: str):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT
                invoice_id, invoice_number, razorpay_payment_id, razorpay_order_id,
                plan_name, staff_count, base_amount, cgst, sgst, igst, total_amount,
                payment_date, valid_till, created_at, coupon_code
            FROM gen_invoices
            WHERE org_id = %s
            ORDER BY payment_date DESC
            LIMIT 50
            """,
            (org_id,)
        )
        rows = cursor.fetchall()
        return {"success": True, "data": [dict(r) for r in rows]}
    except Exception:
        logger.exception("Order history failed")
        return {"success": False, "data": [], "error": "Could not load the order history"}
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)
