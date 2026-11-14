from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from core_functions import pricing
from core_functions.gen_orders_functions import load_current_subscription
from core_functions import referral_functions as referral_fn
from psycopg2.extras import RealDictCursor
from utils.razorpay import get_razorpay_client
from utils.email import send_invoice_email, format_full_address
from utils import branding
from utils.timezone import get_utc_now, ist_today
import uuid
import logging

logger = logging.getLogger(__name__)

# Result states of activate_paid_order
ACTIVATED = "activated"
ALREADY = "already"
NOT_FOUND = "not_found"
BLOCKED = "blocked"        # a payment of a different amount was recorded for this order: never activated
MISMATCH = "mismatch"      # this call found a different amount / currency and flagged the order


def subscription_id_for_order(razorpay_order_id: str) -> str:
    """Deterministic subscription id: the unique index on it is a second guard that an order activates once."""
    return f"sub-{razorpay_order_id}"[:255]


def _lock_order(cursor, razorpay_order_id: str, user_id: str = None):
    sql = """
        SELECT id, user_id, org_id, plan_id, razorpay_order_id, amount, currency, status, staff_limit,
               coupon_code, purchase_mode, term_months, base_amount, gst_amount, payment_flag, activated_at,
               discount_amount, referral_reward_id
        FROM gen_orders
        WHERE razorpay_order_id = %s
    """
    params = [razorpay_order_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    sql += " FOR UPDATE"
    cursor.execute(sql, tuple(params))
    return cursor.fetchone()


def _existing_activation(cursor, razorpay_order_id: str):
    cursor.execute(
        "SELECT plan_id, end_date, staff_limit FROM subscription WHERE subscription_id = %s",
        (subscription_id_for_order(razorpay_order_id),),
    )
    return cursor.fetchone()


INVOICE_NUMBER_RETRIES = 3     # extra attempts with a fresh sequence value after a duplicate invoice_number
_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) == _UNIQUE_VIOLATION


# gen_orders.payment_flag values that stop an order from being activated (a payment we cannot trust). REFERRAL_ALREADY_USED is
# NOT one of them: the payment is honoured, the flag is only a note for the operator.
def _blocks_activation(order) -> bool:
    flag = order.get("payment_flag")
    return bool(flag) and flag != referral_fn.FLAG_REFERRAL_ALREADY_USED


def _insert_invoice(cursor, order, razorpay_payment_id, plan_name, staff_count, end_date, customer_state, today,
                    referral_reward_id=None):
    """One invoice per payment (unique index + existence check); runs in a savepoint so a failure never undoes the activation.

    A unique violation of invoice_number (sequence behind the highest issued number) rolls back to the savepoint and
    retries up to INVOICE_NUMBER_RETRIES times with a fresh ``nextval``; if every attempt fails an ERROR with the order
    id is logged (the plan stays activated: invoices are best effort but never dropped silently).

    Returns (invoice_number, amounts, payment_date) of the NEW invoice, or None when one already existed or it could
    not be written.
    """
    order_id = order.get("razorpay_order_id")
    cursor.execute("SAVEPOINT invoice_sp")
    attempt = 0
    while True:
        attempt += 1
        try:
            if attempt == 1:
                cursor.execute("SELECT invoice_number FROM gen_invoices WHERE razorpay_payment_id = %s", (razorpay_payment_id,))
                if cursor.fetchone():
                    cursor.execute("RELEASE SAVEPOINT invoice_sp")
                    return None

            if order.get("base_amount") is not None and order.get("gst_amount") is not None:
                base, gst = pricing.money(order["base_amount"]), pricing.money(order["gst_amount"])
            else:                                   # order created before the price snapshot existed
                legacy = pricing.legacy_split_from_total(order["amount"])
                base, gst = legacy["base"], legacy["gst"]
            amounts = pricing.invoice_amounts(base, gst, customer_state)

            cursor.execute("SELECT nextval('gen_invoice_seq') AS n")
            number = pricing.invoice_number(cursor.fetchone()["n"], today)
            payment_date = get_utc_now()
            cursor.execute("""
                INSERT INTO gen_invoices (
                    invoice_id, invoice_number, user_id, org_id,
                    razorpay_payment_id, razorpay_order_id,
                    plan_name, staff_count,
                    base_amount, cgst, sgst, igst, total_amount,
                    payment_date, valid_till, coupon_code, referral_reward_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (razorpay_payment_id) DO NOTHING
                RETURNING invoice_number
            """, (
                str(uuid.uuid4()), number, order["user_id"], order["org_id"],
                razorpay_payment_id, order["razorpay_order_id"],
                plan_name, staff_count,
                amounts["base_amount"], amounts["cgst"], amounts["sgst"], amounts["igst"], amounts["total_amount"],
                payment_date, end_date, order.get("coupon_code"), referral_reward_id,
            ))
            row = cursor.fetchone()
            cursor.execute("RELEASE SAVEPOINT invoice_sp")
            if not row:
                return None
            return row["invoice_number"], amounts, payment_date
        except Exception as exc:
            try:
                cursor.execute("ROLLBACK TO SAVEPOINT invoice_sp")
            except Exception:
                logger.error("Invoice NOT written for order %s (payment %s): cannot roll back to the savepoint",
                             order_id, razorpay_payment_id)
                return None
            if _is_unique_violation(exc):
                if attempt <= INVOICE_NUMBER_RETRIES:
                    logger.warning("Invoice number already used (order %s, attempt %s): retrying with the next sequence value",
                                   order_id, attempt)
                    continue
                logger.error("Invoice NOT written for order %s (payment %s): invoice_number is still taken after %s attempts; "
                             "the plan is active, issue the invoice manually (check gen_invoice_seq against max(invoice_number))",
                             order_id, razorpay_payment_id, attempt)
                return None
            logger.exception("Failed to insert invoice record for order %s (payment %s)", order_id, razorpay_payment_id)
            return None


def _consume_referral_reward(cursor, order, plan_name):
    """Mark the order's reward USED (same transaction as the subscription). Returns the reward id when THIS call consumed it.
    0 rows = another order consumed it first: the payment stays honoured, the order is flagged and a warning is logged.
    A database error inside the referral step rolls back to a savepoint, so it can never undo the activation."""
    reward_id = order["referral_reward_id"]
    cursor.execute("SAVEPOINT referral_use_sp")
    try:
        consumed = referral_fn.consume_reward_on_activation(
            cursor, reward_id=reward_id, org_id=order["org_id"], order_id=order["id"], plan_name=plan_name,
            discount_amount=order.get("discount_amount"),
        )
        if not consumed:
            cursor.execute(
                "UPDATE gen_orders SET payment_flag = 'REFERRAL_ALREADY_USED', updated_at = NOW() WHERE id = %s",
                (order["id"],),
            )
            logger.warning(
                "Referral reward %s was already used when order %s (org %s) was activated: payment honoured, order flagged %s",
                reward_id, order.get("razorpay_order_id"), order["org_id"], referral_fn.FLAG_REFERRAL_ALREADY_USED,
            )
        cursor.execute("RELEASE SAVEPOINT referral_use_sp")
        return reward_id if consumed else None
    except Exception:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT referral_use_sp")
        except Exception:
            pass
        logger.exception("Referral reward %s could not be consumed for order %s: the plan stays activated; fix by hand",
                         reward_id, order.get("razorpay_order_id"))
        return None


def _record_reward_invoice(cursor, reward_id, order, invoice_number):
    cursor.execute("SAVEPOINT referral_inv_sp")
    try:
        referral_fn.set_reward_invoice_number(cursor, reward_id, order["id"], invoice_number)
        cursor.execute("RELEASE SAVEPOINT referral_inv_sp")
    except Exception:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT referral_inv_sp")
        except Exception:
            pass
        logger.exception("Invoice number of referral reward %s not recorded (order %s)", reward_id, order.get("razorpay_order_id"))


def _qualify_referral(cursor, order):
    """First paid activation of a referred organisation: stamp referral.qualified_at and release the referrer's reward. Runs only
    for the call that created the subscription and is a no-op when already qualified, so a re-delivered webhook adds nothing."""
    cursor.execute("SAVEPOINT referral_qual_sp")
    try:
        referral_id = referral_fn.qualify_referral_on_first_payment(cursor, order["org_id"])
        cursor.execute("RELEASE SAVEPOINT referral_qual_sp")
        if referral_id is not None:
            logger.info("Referral %s qualified by the first paid plan of org %s", referral_id, order["org_id"])
    except Exception:
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT referral_qual_sp")
        except Exception:
            pass
        logger.exception("Referral qualification failed for org %s (order %s): the plan stays activated",
                         order["org_id"], order.get("razorpay_order_id"))


def activate_paid_order(
    conn,
    cursor,
    razorpay_order_id: str,
    razorpay_payment_id: str,
    *,
    user_id: str = None,
    paid_amount_paise: int = None,
    paid_currency: str = None,
    override_email: str = None,
) -> dict:
    """Activate the plan bought by a paid order, exactly once. Used by BOTH the client verify call and the webhook.

    Idempotent on the order: the order row is locked, the subscription id is derived from the order id and an
    activated order is a no-op (``state == 'already'``), so verify-then-webhook, webhook-then-verify, retries and
    double taps all activate once. ``paid_amount_paise`` (webhook only) must equal the stored order amount, else
    the order is flagged and nothing is activated. Commits on success; the caller rolls back on exceptions.
    """
    order = _lock_order(cursor, razorpay_order_id, user_id)
    if not order:
        return {"state": NOT_FOUND}
    if _blocks_activation(order):
        return {"state": BLOCKED}

    already = _existing_activation(cursor, razorpay_order_id)
    if str(order["status"]) == "PAID" and (order.get("activated_at") is not None or already):
        return {                             # nothing changed; the caller ends the transaction
            "state": ALREADY,
            "plan_id": order["plan_id"],
            "valid_till": already["end_date"] if already else None,
            "staff_limit": already["staff_limit"] if already else None,
            "mode": order.get("purchase_mode"),
        }

    if paid_amount_paise is not None:
        expected = pricing.to_paise(order["amount"])
        currency_ok = (paid_currency or order.get("currency") or "INR").upper() == (order.get("currency") or "INR").upper()
        if int(paid_amount_paise) != expected or not currency_ok:
            logger.error(
                "Payment amount mismatch for order %s: paid=%s %s expected=%s %s",
                razorpay_order_id, paid_amount_paise, paid_currency, expected, order.get("currency"),
            )
            cursor.execute(
                "UPDATE gen_orders SET payment_flag = 'AMOUNT_MISMATCH', updated_at = NOW() WHERE id = %s",
                (order["id"],),
            )
            conn.commit()
            return {"state": MISMATCH}

    today = ist_today()
    mode = (order.get("purchase_mode") or "new").lower()       # in-flight orders from before migration 031
    ordered_staff = max(1, int(order.get("staff_limit") or pricing.MIN_STAFF))
    existing = load_current_subscription(cursor, order["org_id"])
    cursor.execute("SELECT plan_name, plan_month FROM gen_plans WHERE plan_id = %s", (order["plan_id"],))
    plan_row = cursor.fetchone() or {}
    term = order.get("term_months")
    if term is None:
        term = int(plan_row.get("plan_month") or pricing.RENEW_MONTHS)
    start_date, end_date, new_staff_limit = pricing.activation_terms(
        mode=mode, term_months=term, ordered_staff=ordered_staff, existing=existing, today=today,
    )

    # Record the payment (unique on the payment id)
    cursor.execute("""
        INSERT INTO gen_payments (user_id, org_id, razorpay_payment_id, razorpay_order_id, amount, status)
        VALUES (%s, %s, %s, %s, %s, 'PAID')
        ON CONFLICT (razorpay_payment_id) DO NOTHING
    """, (order["user_id"], order["org_id"], razorpay_payment_id, razorpay_order_id, order["amount"]))

    # The new subscription row; a conflict on the order-derived id means another call already activated it
    sub_id = subscription_id_for_order(razorpay_order_id)
    cursor.execute("""
        INSERT INTO subscription (org_id, user_id, plan_id, subscription_id, status, start_date, end_date, staff_limit)
        VALUES (%s, %s, %s, %s, 'ACTIVE', %s, %s, %s)
        ON CONFLICT (subscription_id) DO NOTHING
    """, (order["org_id"], order["user_id"], order["plan_id"], sub_id, start_date, end_date, new_staff_limit))
    created = (cursor.rowcount or 0) > 0

    if created:
        # Deactivate previous subscriptions
        cursor.execute("""
            UPDATE subscription SET status = 'INACTIVE', updated_at = NOW()
            WHERE org_id = %s AND subscription_id != %s AND status <> 'INACTIVE'
        """, (order["org_id"], sub_id))
        # Deactivate free trial; if still active and not yet expired, stamp end_date to payment date
        cursor.execute(
            "UPDATE free_trial SET is_active = FALSE, end_date = LEAST(end_date, %s) WHERE org_id = %s AND is_active = TRUE",
            (start_date, order["org_id"]),
        )

    plan_name = (plan_row.get("plan_name") or "Subscription Plan")
    reward_used_id = None
    if created and order.get("referral_reward_id"):
        reward_used_id = _consume_referral_reward(cursor, order, plan_name)

    cursor.execute(
        "UPDATE gen_orders SET status = 'PAID', activated_at = COALESCE(activated_at, NOW()), updated_at = NOW() WHERE id = %s",
        (order["id"],),
    )

    invoice_info = None
    if created:
        cursor.execute("""
            SELECT u.name AS customer_name, u.email AS customer_email, u.mobile_number AS phone_number,
                   o.org_name, o.org_address, o.state, o.district, o.pincode
            FROM users u
            LEFT JOIN org_info o ON o.org_id = %s
            WHERE u.user_id = %s
        """, (order["org_id"], order["user_id"]))
        cust = cursor.fetchone() or {}
        invoice_info = _insert_invoice(
            cursor, order, razorpay_payment_id, plan_name, new_staff_limit, end_date, cust.get("state"), today,
            referral_reward_id=reward_used_id,
        )
        if reward_used_id is not None and invoice_info:
            _record_reward_invoice(cursor, reward_used_id, order, invoice_info[0])

    if created:
        _qualify_referral(cursor, order)

    conn.commit()

    result = {
        "state": ACTIVATED if created else ALREADY,
        "plan_id": order["plan_id"],
        "valid_till": end_date,
        "staff_limit": new_staff_limit,
        "mode": mode,
    }
    if invoice_info:
        number, amounts, payment_date = invoice_info
        result["invoice_email"] = (override_email.strip() if override_email and override_email.strip() else cust.get("customer_email"))
        result["invoice_data"] = _invoice_email_data(
            cust, order, plan_name, number, amounts, payment_date, end_date, ordered_staff, new_staff_limit,
            razorpay_payment_id, razorpay_order_id,
        )
    return result


def _invoice_email_data(cust, order, plan_name, number, amounts, payment_date, end_date, charged_staff, total_staff,
                        razorpay_payment_id, razorpay_order_id) -> dict:
    customer_name = cust.get("customer_name") or "Customer"
    org_name = cust.get("org_name") or customer_name
    full_address = format_full_address(
        org_name, cust.get("org_address"), cust.get("district"), cust.get("state"), cust.get("pincode"),
    )
    base = amounts["base_amount"]
    data = {
        'customer_name':   customer_name,
        'order_name':      f"{plan_name} - {org_name}",
        'org_name':        org_name,
        'phone_number':    cust.get("phone_number") or '',
        'customer_email':  cust.get("customer_email") or '',
        'bill_address':    full_address,
        'ship_address':    full_address,
        'place_of_supply': branding.seller_place_of_supply(),
        'invoice_number':  number,
        'payment_date':    payment_date.strftime('%d/%m/%Y'),
        'due_date':        payment_date.strftime('%d/%m/%Y'),
        'valid_till':      end_date.strftime('%d %b %Y'),
        'plan_name':       plan_name,
        'staff_count':     total_staff,
        'items': [{
            'name':        branding.invoice_item_name(plan_name),
            'description': f"Valid till {end_date.strftime('%d %b %Y')}",
            'sac':         branding.seller_sac(),
            'qty':         charged_staff,
            'unit':        'Staff',
            'rate':        float(pricing.money(base / max(charged_staff, 1))),
            'amount':      float(base),
        }],
        'base_amount':  float(base),
        'cgst':         float(amounts["cgst"]),
        'sgst':         float(amounts["sgst"]),
        'total_amount': float(amounts["total_amount"]),
        'payment_id':   razorpay_payment_id,
        'order_id':     razorpay_order_id,
    }
    if amounts["igst"]:
        data['igst'] = float(amounts["igst"])
        data['igst_pct'] = int(pricing.GST_PERCENT)
    return data


def send_invoice_for_result(result: dict) -> None:
    """E-mail the invoice of a freshly activated order. Runs after the commit; a failure is only logged."""
    email = result.get("invoice_email")
    data = result.get("invoice_data")
    if not (email and data):
        return
    try:
        send_invoice_email(email, data)
    except Exception:
        logger.exception("Failed to dispatch invoice email")


async def verify_payment_process(user_id: str, razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str, override_email: str = None):
    conn = None
    cursor = None
    try:
        try:
            razorpay_client, _ = get_razorpay_client()
        except Exception:
            logger.exception("Razorpay configuration error")
            raise HTTPException(status_code=500, detail="Payment gateway is not configured")

        is_valid = razorpay_client.verify_payment_signature(
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature
        )

        if not is_valid:
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Also verifies the calling user owns this order
        result = activate_paid_order(
            conn, cursor, razorpay_order_id, razorpay_payment_id,
            user_id=user_id, override_email=override_email,
        )
        state = result["state"]
        if state != ACTIVATED:
            conn.rollback()                  # nothing to persist; release the order row lock
        if state == NOT_FOUND:
            raise HTTPException(status_code=404, detail="Order not found")
        if state in (BLOCKED, MISMATCH):
            raise HTTPException(status_code=409, detail="This payment is under review. Please contact support.")

        send_invoice_for_result(result)

        valid_till = result.get("valid_till")
        body = {
            "success": True,
            "message": "Payment already verified" if state == ALREADY else "Payment verified successfully",
            "valid_till": str(valid_till) if valid_till else None,
            "plan_id": result.get("plan_id"),
        }
        if result.get("staff_limit") is not None:
            body["staff_limit"] = result["staff_limit"]
        if result.get("mode"):
            body["mode"] = result["mode"]
        return body

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Payment verification error")
        raise HTTPException(status_code=500, detail="Payment verification error")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)
