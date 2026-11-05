from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from core_functions import gen_payment_functions as payments
from psycopg2.extras import RealDictCursor
from utils.razorpay import verify_webhook_signature
import json
import os
import logging

logger = logging.getLogger(__name__)

# Events that mean "the customer paid"; both activate the plan (idempotent with the client verify call).
_PAID_EVENTS = {"payment.captured", "order.paid"}


def _paise_or_invalid(value):
    """Payment amount in paise as an int; None when absent; -1 (never equal to an order amount) when unreadable."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _webhook_secret() -> str:
    # Read on every call so a rotated secret / a test patch is picked up without a restart.
    return os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()


async def handle_razorpay_webhook(raw_body: bytes, signature: str, header_event_id: str = None):
    """Razorpay webhook. ``raw_body`` is the request body bytes exactly as received (the signature covers them).

    Signature missing / wrong -> 400; secret not configured -> 503 (fail closed). Duplicate events and unknown
    orders answer 200 without side effects. A paid event activates the plan once (same function as the client
    verify call); a payment of a different amount is flagged and never activated.
    """
    secret = _webhook_secret()
    if not secret:
        logger.error("RAZORPAY_WEBHOOK_SECRET is not set: webhook rejected")
        raise HTTPException(status_code=503, detail="Webhook is not configured")
    if not verify_webhook_signature(raw_body, signature, secret):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    conn = None
    cursor = None
    activation = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        event_type = payload.get("event")
        # Razorpay sends the unique event id in the X-Razorpay-Event-Id header.
        event_id = (header_event_id or payload.get("id") or None)

        cursor.execute("""
            INSERT INTO gen_webhook_events (event_id, event_type, payload)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
        """, (event_id, event_type, json.dumps(payload)))
        if cursor.fetchone() is None:
            conn.rollback()
            logger.info("Duplicate Razorpay webhook event %s ignored", event_id)
            return {"success": True, "duplicate": True}

        body = payload.get("payload") or {}
        payment = (body.get("payment") or {}).get("entity") or {}
        order_entity = (body.get("order") or {}).get("entity") or {}
        razorpay_order_id = payment.get("order_id") or order_entity.get("id")
        razorpay_payment_id = payment.get("id")

        if razorpay_order_id and event_type in _PAID_EVENTS:
            if not razorpay_payment_id:
                logger.warning("Paid webhook for order %s without a payment id ignored", razorpay_order_id)
                conn.commit()
            else:
                activation = payments.activate_paid_order(
                    conn, cursor, razorpay_order_id, razorpay_payment_id,
                    paid_amount_paise=_paise_or_invalid(payment.get("amount")),
                    paid_currency=payment.get("currency"),
                )
                if activation["state"] == payments.NOT_FOUND:
                    logger.warning("Paid webhook for unknown order %s", razorpay_order_id)
                # activate_paid_order committed (or rolled back a no-op); make sure the event row is kept
                conn.commit()

        elif razorpay_order_id and event_type == "payment.failed":
            # A late failed attempt never overwrites an order that is already paid.
            cursor.execute("""
                UPDATE gen_orders SET status = 'FAILED', updated_at = NOW()
                WHERE razorpay_order_id = %s AND status IS DISTINCT FROM 'PAID' AND activated_at IS NULL
            """, (razorpay_order_id,))
            conn.commit()
        else:
            conn.commit()

    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Razorpay webhook processing error")
        raise HTTPException(status_code=500, detail="Webhook processing error")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)

    if activation and activation.get("state") == payments.ACTIVATED:
        payments.send_invoice_for_result(activation)
    return {"success": True}


def _extract_msg91_webhook_event(payload: dict):
    request_id = (
        payload.get("requestId")
        or payload.get("request_id")
        or payload.get("campaignRequestId")
        or payload.get("oneApiRequestId")
        or payload.get("crqid")
    )
    event_type = (
        payload.get("eventName")
        or payload.get("status")
        or payload.get("event")
        or "unknown"
    )
    customer_number = (
        payload.get("customerNumber")
        or payload.get("customer_number")
        or payload.get("to")
        or payload.get("mobile")
    )
    return request_id, event_type, customer_number


async def handle_msg91_whatsapp_webhook(payload: dict):
    request_id, event_type, customer_number = _extract_msg91_webhook_event(payload)

    logger.info(
        "MSG91 WhatsApp webhook received | request_id=%s | event_type=%s | customer=%s",
        request_id,
        event_type,
        customer_number,
    )
    logger.info("MSG91 WhatsApp webhook payload: %s", json.dumps(payload, ensure_ascii=False))

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            INSERT INTO gen_webhook_events (event_id, event_type, payload)
            VALUES (%s, %s, %s)
            """,
            (
                request_id or f"msg91-{event_type}",
                f"msg91.whatsapp.{event_type}",
                json.dumps(payload),
            ),
        )

        conn.commit()
        return {"success": True}

    except Exception as e:
        if conn:
            conn.rollback()
        logger.exception("MSG91 webhook processing error")
        raise HTTPException(status_code=500, detail="Webhook processing error")
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)
