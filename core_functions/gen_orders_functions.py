from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from core_functions import pricing
from core_functions import referral_functions as referral_fn
from psycopg2.extras import RealDictCursor, Json
from utils.razorpay import get_razorpay_client
from utils.timezone import ist_today
import logging
import os
import uuid

logger = logging.getLogger(__name__)

_FALSE = {"0", "false", "no", "off"}


def _owner_check_enabled() -> bool:
    """BILLING_ENFORCE_ORG_OWNER (default on): only the admin that owns the organisation may buy / quote for it."""
    return os.getenv("BILLING_ENFORCE_ORG_OWNER", "1").strip().lower() not in _FALSE


def _fail(exc: pricing.PricingError):
    # `detail` stays the human message (what the app shows); the machine code of a referral error rides in a header.
    headers = {"X-Error-Code": exc.code} if str(exc.code).startswith("referral_") else None
    return HTTPException(status_code=exc.status, detail=exc.message, headers=headers)


# ── data the price depends on (all read from the database, never from the request) ──────────────
def _load_user(cursor, user_id: str):
    cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="User not found")


def _check_org_owner(cursor, user_id: str, org_id: str):
    """The organisation must exist and belong to this admin (org_info.parent_id, or the admin's own users.org_id)."""
    cursor.execute(
        """
        SELECT 1 AS ok FROM org_info o
        WHERE o.org_id = %s
          AND (o.parent_id = %s
               OR EXISTS (SELECT 1 FROM users u WHERE u.user_id = %s AND u.org_id = o.org_id))
        LIMIT 1
        """,
        (org_id, user_id, user_id),
    )
    if cursor.fetchone():
        return
    cursor.execute("SELECT 1 AS ok FROM org_info WHERE org_id = %s LIMIT 1", (org_id,))
    if cursor.fetchone():
        raise HTTPException(status_code=403, detail="You cannot purchase a plan for this organisation")
    raise HTTPException(status_code=404, detail="Organisation not found")


def _load_tier(cursor, plan_id):
    if plan_id is None:
        return None
    cursor.execute(
        "SELECT plan_id, plan_name, plan_amount, plan_month FROM gen_plans WHERE plan_id = %s",
        (plan_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return pricing.PlanTier(
        plan_id=int(row["plan_id"]),
        name=(row["plan_name"] or "").strip(),
        price=pricing.to_decimal(row["plan_amount"]),
        months=int(row["plan_month"] or 0),
    )


def load_current_subscription(cursor, org_id: str):
    """Latest paid subscription row (ACTIVE or EXPIRED) with its tier; None for a trial / never-paid organisation."""
    cursor.execute(
        """
        SELECT ps.plan_id, ps.status, ps.start_date, ps.end_date,
               COALESCE(ps.staff_limit, 10) AS staff_limit,
               gp.plan_name, gp.plan_amount, gp.plan_month
        FROM punchapp_subscription ps
        JOIN gen_plans gp ON gp.plan_id = ps.plan_id
        WHERE ps.org_id = %s AND ps.status IN ('ACTIVE', 'EXPIRED')
        ORDER BY ps.created_at DESC, ps.id DESC
        LIMIT 1
        """,
        (org_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return pricing.CurrentSubscription(
        plan=pricing.PlanTier(
            plan_id=int(row["plan_id"]),
            name=(row["plan_name"] or "").strip(),
            price=pricing.to_decimal(row["plan_amount"]),
            months=int(row["plan_month"] or 0),
        ),
        status=row["status"],
        start_date=row.get("start_date"),
        end_date=row["end_date"],
        staff_limit=int(row["staff_limit"] or pricing.TRIAL_STAFF_LIMIT),
    )


def _load_coupon(cursor, code):
    if not code:
        return None
    cursor.execute(
        """
        SELECT coupon_id, coupon_name, discount, status, discount_type, flat_amount,
               max_discount, min_amount, valid_from, valid_until, plan_ids
        FROM gen_coupons
        WHERE UPPER(coupon_name) = UPPER(%s) AND status = 1
        ORDER BY coupon_id ASC
        LIMIT 1
        """,
        (code,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    def dec(value):
        return None if value is None else pricing.to_decimal(value)

    return pricing.Coupon(
        name=(row["coupon_name"] or "").strip().upper(),
        discount=pricing.to_decimal(row["discount"], pricing.ZERO),
        discount_type=(row.get("discount_type") or "percent"),
        flat_amount=dec(row.get("flat_amount")),
        max_discount=dec(row.get("max_discount")),
        min_amount=dec(row.get("min_amount")),
        valid_from=row.get("valid_from"),
        valid_until=row.get("valid_until"),
        plan_ids=tuple(int(p) for p in (row.get("plan_ids") or ())),
        status=int(row["status"] or 0),
        coupon_id=row.get("coupon_id"),
    )


def compute_server_quote(
    cursor,
    *,
    org_id: str,
    plan_id,
    staff_limit,
    coupon_code,
    mode=None,
    client_amount=None,
    strict_coupon=True,
    today=None,
    apply_referral=False,
):
    """Price a purchase entirely from database data. Returns the pricing.Quote. Raises HTTPException (404 / 400)."""
    today = today or ist_today()
    code = (coupon_code or "").strip().upper() or None
    mode = (mode or "").strip().lower() or None
    if mode is not None and mode not in pricing.MODES:
        raise HTTPException(status_code=400, detail="mode must be one of new, upgrade, renew")

    current = load_current_subscription(cursor, org_id)
    target = _load_tier(cursor, plan_id)
    if plan_id is None and (mode == "new" or (mode is None and current is None)):
        raise HTTPException(status_code=400, detail="plan_id is required")
    if mode == "new" and target is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if mode is None and plan_id is not None and target is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    coupon = _load_coupon(cursor, code)
    # The reward is always looked up (the quote reports the available count even when it is not applied); the id is the
    # server's pick (oldest AVAILABLE of THIS organisation), never something the client sent.
    offer = referral_fn.load_available_reward(cursor, org_id)

    try:
        if mode is None:
            mode = pricing.pick_mode(
                target=target, staff=staff_limit, current=current, today=today,
                coupon=coupon, coupon_code=code, client_amount=client_amount,
                referral=offer, apply_referral=apply_referral,
            )
        return pricing.build_quote(
            mode=mode, target=target, staff=staff_limit, current=current, today=today,
            coupon=coupon, coupon_code=code, strict_coupon=strict_coupon,
            referral=offer, apply_referral=apply_referral, strict_referral=strict_coupon,
        )
    except pricing.PricingError as exc:
        raise _fail(exc)


async def quote_order(
    user_id: str,
    org_id: str,
    plan_id,
    staff_limit: int = 10,
    coupon_code: str = None,
    mode: str = None,
    apply_referral: bool = False,
):
    """POST /orders/quote: the price breakdown the checkout screen shows. Read-only; nothing is stored."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _load_user(cursor, user_id)
        if _owner_check_enabled():
            _check_org_owner(cursor, user_id, org_id)
        # A coupon that cannot be applied is reported inside the quote (the screen shows the message);
        # the order itself refuses it.
        quote = compute_server_quote(
            cursor, org_id=org_id, plan_id=plan_id, staff_limit=staff_limit,
            coupon_code=coupon_code, mode=mode, strict_coupon=False, apply_referral=bool(apply_referral),
        )
        return {"success": True, "data": quote.to_dict()}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Order quote failed")
        raise HTTPException(status_code=500, detail="Could not calculate the price")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)


async def create_new_order(
    user_id: str,
    org_id: str,
    plan_id: int,
    amount: float = None,
    staff_limit: int = 10,
    coupon_code: str = None,
    mode: str = None,
    apply_referral: bool = False,
):
    """
    Creates the payment order. The amount charged is ALWAYS recomputed here from the database (plan price, coupon,
    current subscription) with core_functions.pricing; ``amount`` from the app is display-only: a different value is
    ignored and logged. The response keeps the shape the app reads and adds the full ``quote``.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Validate user
        _load_user(cursor, user_id)
        if _owner_check_enabled():
            _check_org_owner(cursor, user_id, org_id)

        quote = compute_server_quote(
            cursor, org_id=org_id, plan_id=plan_id, staff_limit=staff_limit,
            coupon_code=coupon_code, mode=mode, client_amount=amount, strict_coupon=True,
            apply_referral=bool(apply_referral),
        )
        if not quote.payable:
            raise HTTPException(status_code=400, detail="Amount must be greater than 0")

        client_amount = None
        if amount is not None:
            try:
                client_amount = pricing.money(amount)
            except pricing.PricingError:
                client_amount = None
            if client_amount is None or client_amount != quote.grand_total:
                logger.warning(
                    "Order amount from the client ignored: client=%s server=%s mode=%s org=%s user=%s",
                    amount, quote.grand_total, quote.mode, org_id, user_id,
                )
        total = quote.grand_total

        try:
            razorpay_client, razorpay_key_id = get_razorpay_client()
        except Exception:
            logger.exception("Razorpay configuration error")
            raise HTTPException(status_code=500, detail="Payment gateway is not configured")

        cursor.execute("""
            SELECT
                u.name AS customer_name,
                u.mobile_number AS phone_number,
                u.email,
                o.org_name,
                gp.plan_name
            FROM users u
            LEFT JOIN org_info o ON o.org_id = %s
            LEFT JOIN gen_plans gp ON gp.plan_id = %s
            WHERE u.user_id = %s
        """, (org_id, quote.plan_id, user_id))
        row = cursor.fetchone() or {}

        customer_name = (row.get("customer_name") or "").strip()
        org_name = (row.get("org_name") or "").strip()
        plan_name = (row.get("plan_name") or "").strip()
        phone_number = (row.get("phone_number") or "").strip()
        email = (row.get("email") or "").strip()
        order_name = f"{plan_name} - {org_name}".strip(" -") or f"Order-{quote.plan_id}"

        order_notes = {
            "name": customer_name,
            "customer_name": customer_name,
            "org_name": org_name,
            "plan_name": plan_name,
            "phone_number": phone_number,
            "email": email,
            "order_name": order_name,
            "staff_limit": str(quote.charged_staff),
            "plan_id": str(quote.plan_id),
            "mode": quote.mode,
            "org_id": org_id,
            "user_id": user_id,
        }

        # Check if this is the user's first-ever paid subscription
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM gen_orders
            WHERE user_id = %s AND status = 'PAID'
        """, (user_id,))
        paid_count = cursor.fetchone()['cnt']
        is_first_payment = (paid_count == 0)

        receipt = f"ord-{uuid.uuid4().hex[:16]}"

        # Create Razorpay order with the server-computed grand total and customer metadata
        try:
            razorpay_order = razorpay_client.create_order(
                total,
                receipt=receipt,
                notes=order_notes,
            )
        except Exception:
            logger.exception("Razorpay order creation failed")
            raise HTTPException(status_code=502, detail="Could not start the payment. Please try again.")

        # Store order with the price snapshot used at activation time
        cursor.execute("""
            INSERT INTO gen_orders (
                user_id, org_id, plan_id, razorpay_order_id, amount, currency, status, staff_limit, coupon_code,
                purchase_mode, term_months, base_amount, gst_amount, discount_amount, credit_amount,
                client_amount, pricing, referral_reward_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            org_id,
            quote.plan_id,
            razorpay_order['id'],
            total,
            razorpay_order['currency'],
            'CREATED',
            quote.charged_staff,
            quote.coupon_code if quote.coupon_applied else None,
            quote.mode,
            quote.term_months,
            quote.subtotal,
            quote.gst,
            quote.discount_amount,
            quote.credit,
            client_amount,
            Json(quote.to_dict()),
            quote.referral_reward_id if quote.referral_applied else None,
        ))

        conn.commit()

        amount_out = float(total)
        return {
            "success": True,
            "is_first_payment": is_first_payment,
            "order_id": razorpay_order["id"],
            "key_id": razorpay_key_id,
            "amount": amount_out,
            "currency": razorpay_order["currency"],
            "order_name": order_name,
            "quote": quote.to_dict(),
            "customer": {
                "name": customer_name,
                "email": email,
                "contact": phone_number,
            },
            "checkout": {
                "order_id": razorpay_order["id"],
                "key_id": razorpay_key_id,
                "amount": amount_out,
                "currency": razorpay_order["currency"],
                "prefill": {
                    "name": customer_name,
                    "email": email,
                    "contact": phone_number,
                },
                "notes": order_notes,
            }
        }

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Order creation error")
        raise HTTPException(status_code=500, detail="Order creation error")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)
