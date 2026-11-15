"""B2C referral: promoter (YouTube / Instagram / Facebook ... channel) codes, the customers they bring in, and their commission.

Tables (migration 040): ``b2c_partner`` (one row per code), ``b2c_payout`` (what has been paid to a promoter), and the shared
``referral`` / ``referral_reward`` rows of the customers that used a code (``referral.partner_id`` is set, no referrer organisation).

Rules
* The code is typed by a super admin: exactly 6 letters / digits (A-Z, 0-9). It is unique among promoters AND never equal to a
  customer's C2C code (the C2C generator avoids promoter codes the same way). It cannot be changed afterwards; a code is switched
  off with ``status = inactive`` (new signups are refused, rewards already issued stay valid).
* A customer that registers with the code gets ``discount_percent`` off Prime / Elite once (the C2C reward rules), valid for
  ``reward_valid_days`` days after signup (NULL = never expires). See referral_functions._attach_partner_referral.
* Commission is earned ONCE per customer, when its first paid plan is activated: ``percent`` = that share of the plan amount
  excluding GST, ``fixed`` = a fixed amount per paid customer, ``none`` = nothing. The amount is booked then on
  ``referral.commission_amount`` (a later rate change never changes it). ``due = earned - paid``; a payout can never exceed it.
* Reading is open to every operator, creating / editing / paying is super admin only (the routes are guarded in the router).
"""
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from core_functions import referral_functions as ref
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

PLATFORMS = ("youtube", "instagram", "facebook", "x", "linkedin", "whatsapp", "telegram", "blog", "other")
COMMISSION_TYPES = ("none", "percent", "fixed")
STATUSES = {"active": 1, "inactive": 0}
MAX_PARTNER_ROWS = 2000
MAX_CUSTOMER_ROWS = 2000
MAX_FIXED_COMMISSION = Decimal("1000000")
MAX_REWARD_DAYS = 3650

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CENT = Decimal("0.01")
GST_DIVISOR = Decimal("1.18")           # an order without a stored base amount (before migration 031): amount / 1.18

MSG_CODE_FORMAT = "Code must be exactly 6 letters or digits (A-Z, 0-9)"
MSG_CODE_TAKEN = "This code is already in use"
MSG_CODE_FIXED = "The code cannot be changed. Create a new code and switch this one off"


# ──────────────────────────────────────────────────────────────────────────── small helpers
def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _f(value: Any) -> float:
    return float(_money(value))


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def _text(data: Dict[str, Any], key: str, label: str, limit: int, required: bool = False) -> Optional[str]:
    value = data.get(key)
    value = None if value is None else str(value).strip()
    if not value:
        if required:
            raise _bad(f"{label} is required")
        return None
    if len(value) > limit:
        raise _bad(f"{label} can be at most {limit} characters")
    return value


def _int_in(value: Any, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or (isinstance(value, float) and (not math.isfinite(value) or value != int(value))):
        raise _bad(f"{label} must be a whole number between {low} and {high}")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise _bad(f"{label} must be a whole number between {low} and {high}")
    if not low <= number <= high:
        raise _bad(f"{label} must be a whole number between {low} and {high}")
    return number


def normalise_partner_code(code: Any) -> str:
    text = ref.normalise_code(str(code) if code is not None else "")
    if not ref.is_valid_lookup_format(text):
        raise _bad(MSG_CODE_FORMAT)
    return text


def clean_partner(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the editable fields of a promoter (everything except the code and the status); HTTP 400 names the field."""
    platform = str(data.get("platform") or "").strip().lower()
    if platform not in PLATFORMS:
        raise _bad("Platform must be one of: " + ", ".join(PLATFORMS))
    out: Dict[str, Any] = {
        "platform": platform,
        "channel_name": _text(data, "channel_name", "Channel name", 150, required=True),
        "channel_url": _text(data, "channel_url", "Channel URL", 300),
        "contact_name": _text(data, "contact_name", "Contact name", 150),
        "phone": _text(data, "phone", "Phone", 20),
        "email": _text(data, "email", "Email", 255),
        "address": _text(data, "address", "Address", 1000),
        "notes": _text(data, "notes", "Notes", 2000),
    }
    if out["channel_url"] and not re.match(r"^https?://\S+$", out["channel_url"], re.I):
        raise _bad("Channel URL must start with http:// or https://")
    if out["phone"] and not 7 <= len(re.sub(r"\D", "", out["phone"])) <= 15:
        raise _bad("Phone must have 7 to 15 digits")
    if out["phone"] and not re.match(r"^[0-9+\-()\s]+$", out["phone"]):
        raise _bad("Phone can contain only digits, spaces and + - ( )")
    if out["email"] and not _EMAIL_RE.match(out["email"]):
        raise _bad("Email is not valid")
    out["discount_percent"] = ref.validate_percent(data.get("discount_percent"), "Discount")
    days = data.get("reward_valid_days")
    out["reward_valid_days"] = None if days is None or str(days).strip() == "" else _int_in(days, 1, MAX_REWARD_DAYS, "Reward valid days")
    ctype = str(data.get("commission_type") or "none").strip().lower()
    if ctype not in COMMISSION_TYPES:
        raise _bad("Commission type must be none, percent or fixed")
    try:
        cvalue = Decimal(str(data.get("commission_value") if data.get("commission_value") not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        raise _bad("Commission value must be a number")
    if not cvalue.is_finite():
        raise _bad("Commission value must be a number")
    if cvalue < 0:
        raise _bad("Commission value cannot be negative")
    if ctype == "percent" and cvalue > 100:
        raise _bad("A percent commission cannot be more than 100")
    if ctype == "fixed" and cvalue > MAX_FIXED_COMMISSION:
        raise _bad("A fixed commission cannot be more than 1,000,000")
    out["commission_type"] = ctype
    out["commission_value"] = Decimal("0.00") if ctype == "none" else cvalue.quantize(_CENT, rounding=ROUND_HALF_UP)
    return out


def _partner_view(row: Dict[str, Any]) -> Dict[str, Any]:
    earned, paid = _money(row.get("commission_earned")), _money(row.get("commission_paid"))
    return {
        "partner_id": row["partner_id"],
        "code": row["code"],
        "platform": row.get("platform"),
        "channel_name": row.get("channel_name") or "",
        "channel_url": row.get("channel_url"),
        "contact_name": row.get("contact_name"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "address": row.get("address"),
        "notes": row.get("notes"),
        "discount_percent": int(row.get("discount_percent") or 0),
        "reward_valid_days": row.get("reward_valid_days"),
        "commission_type": row.get("commission_type") or "none",
        "commission_value": _f(row.get("commission_value")),
        "status": "active" if int(row.get("status") or 0) == 1 else "inactive",
        "created_at": ref._iso_z(row.get("created_at")),
        "created_by": row.get("created_by"),
        "signups": int(row.get("signups") or 0),
        "converted": int(row.get("converted") or 0),
        "revenue": _f(row.get("revenue")),
        "commission_earned": float(earned),
        "commission_paid": float(paid),
        "commission_due": float(max(Decimal("0.00"), earned - paid)),
    }


# One query for the promoters with their counters. Revenue = the amount excluding GST of every PAID order of the customers that
# used the code (an order without a stored base amount is taken as amount / 1.18).
_PARTNERS_SQL = """
    SELECT p.partner_id, p.code, p.platform, p.channel_name, p.channel_url, p.contact_name, p.phone, p.email, p.address, p.notes,
           p.discount_percent, p.reward_valid_days, p.commission_type, p.commission_value, p.status, p.created_by, p.created_at,
           COALESCE(s.signups, 0) AS signups, COALESCE(s.converted, 0) AS converted,
           COALESCE(s.commission_earned, 0) AS commission_earned,
           COALESCE(v.revenue, 0) AS revenue, COALESCE(d.commission_paid, 0) AS commission_paid
    FROM b2c_partner p
    LEFT JOIN (SELECT partner_id, COUNT(*) AS signups, COUNT(qualified_at) AS converted,
                      COALESCE(SUM(commission_amount), 0) AS commission_earned
               FROM referral WHERE partner_id IS NOT NULL GROUP BY partner_id) s ON s.partner_id = p.partner_id
    LEFT JOIN (SELECT r.partner_id, COALESCE(SUM(COALESCE(o.base_amount, ROUND(o.amount / 1.18, 2))), 0) AS revenue
               FROM referral r JOIN gen_orders o ON o.org_id = r.referred_org_id AND o.status = 'PAID'
               WHERE r.partner_id IS NOT NULL GROUP BY r.partner_id) v ON v.partner_id = p.partner_id
    LEFT JOIN (SELECT partner_id, COALESCE(SUM(amount), 0) AS commission_paid FROM b2c_payout WHERE voided_at IS NULL
               GROUP BY partner_id) d
           ON d.partner_id = p.partner_id
"""


def _load_partner(cursor, partner_id: int) -> Dict[str, Any]:
    cursor.execute(_PARTNERS_SQL + " WHERE p.partner_id = %s", (partner_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Partner code not found")
    return _partner_view(row)


def _code_in_use(cursor, code: str) -> bool:
    cursor.execute("SELECT 1 AS hit FROM b2c_partner WHERE code = %s UNION ALL SELECT 1 FROM org_referral_code WHERE code = %s",
                   (code, code))
    return cursor.fetchone() is not None


# ──────────────────────────────────────────────────────────────────────────── operations (the caller's cursor, never commit)
def list_partners(cursor, q: str = "", status: str = "all") -> Dict[str, Any]:
    """The payload of GET /gen-dashboard/referrals/b2c. ``summary`` covers ALL promoters, ``partners`` is filtered."""
    status = (status or "all").strip().lower()
    if status not in ("all", "active", "inactive"):
        raise _bad("status must be one of all, active, inactive")
    q = (q or "").strip().lower()[:100]
    cursor.execute(_PARTNERS_SQL + " ORDER BY p.created_at DESC, p.partner_id DESC LIMIT %s", (MAX_PARTNER_ROWS,))
    everyone = [_partner_view(r) for r in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(SUM(rw.used_discount_amount) FILTER (WHERE rw.status = 'USED'), 0) AS discount_given
        FROM referral_reward rw JOIN referral r ON r.referral_id = rw.referral_id
        WHERE r.partner_id IS NOT NULL
        """
    )
    given = (cursor.fetchone() or {}).get("discount_given")
    summary = {
        "partners": len(everyone),
        "active": sum(1 for p in everyone if p["status"] == "active"),
        "signups": sum(p["signups"] for p in everyone),
        "converted": sum(p["converted"] for p in everyone),
        "revenue": round(sum(p["revenue"] for p in everyone), 2),
        "discount_given": _f(given),
        "commission_earned": round(sum(p["commission_earned"] for p in everyone), 2),
        "commission_paid": round(sum(p["commission_paid"] for p in everyone), 2),
        "commission_due": round(sum(p["commission_due"] for p in everyone), 2),
    }
    shown = everyone
    if status != "all":
        shown = [p for p in shown if p["status"] == status]
    if q:
        def hit(p):
            hay = " ".join(str(p.get(k) or "") for k in ("code", "platform", "channel_name", "contact_name", "phone", "email")).lower()
            return q in hay
        shown = [p for p in shown if hit(p)]
    return {"summary": summary, "partners": shown}


def create_partner(cursor, data: Dict[str, Any], admin: Optional[str] = None) -> Dict[str, Any]:
    code = normalise_partner_code(data.get("code"))
    fields = clean_partner(data)
    if _code_in_use(cursor, code):
        raise HTTPException(status_code=409, detail=MSG_CODE_TAKEN)
    cursor.execute(
        """
        INSERT INTO b2c_partner (code, platform, channel_name, channel_url, contact_name, phone, email, address, notes,
                                 discount_percent, reward_valid_days, commission_type, commission_value, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO NOTHING
        RETURNING partner_id
        """,
        (code, fields["platform"], fields["channel_name"], fields["channel_url"], fields["contact_name"], fields["phone"],
         fields["email"], fields["address"], fields["notes"], fields["discount_percent"], fields["reward_valid_days"],
         fields["commission_type"], fields["commission_value"], admin),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=409, detail=MSG_CODE_TAKEN)
    return {"partner": _load_partner(cursor, row["partner_id"])}


def update_partner(cursor, partner_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    cursor.execute("SELECT code, status FROM b2c_partner WHERE partner_id = %s FOR NO KEY UPDATE", (partner_id,))
    current = cursor.fetchone()
    if not current:
        raise HTTPException(status_code=404, detail="Partner code not found")
    if data.get("code") not in (None, "") and ref.normalise_code(str(data["code"])) != current["code"]:
        raise _bad(MSG_CODE_FIXED)
    fields = clean_partner(data)
    status = data.get("status")
    if status is None:
        status_value = int(current["status"])
    else:
        status_value = STATUSES.get(str(status).strip().lower())
        if status_value is None:
            raise _bad("Status must be active or inactive")
    cursor.execute(
        """
        UPDATE b2c_partner
        SET platform = %s, channel_name = %s, channel_url = %s, contact_name = %s, phone = %s, email = %s, address = %s, notes = %s,
            discount_percent = %s, reward_valid_days = %s, commission_type = %s, commission_value = %s, status = %s, updated_at = NOW()
        WHERE partner_id = %s
        """,
        (fields["platform"], fields["channel_name"], fields["channel_url"], fields["contact_name"], fields["phone"], fields["email"],
         fields["address"], fields["notes"], fields["discount_percent"], fields["reward_valid_days"], fields["commission_type"],
         fields["commission_value"], status_value, partner_id),
    )
    return {"partner": _load_partner(cursor, partner_id)}


def _month_keys(count: int = 6, today: Optional[date] = None) -> List[str]:
    today = today or datetime.now(timezone.utc).date()
    year, month = today.year, today.month
    keys = []
    for _ in range(count):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def partner_detail(cursor, partner_id: int, today: Optional[date] = None) -> Dict[str, Any]:
    """The payload of GET /gen-dashboard/referrals/b2c/{id}: the promoter, its insights, the customers and the payouts."""
    partner = _load_partner(cursor, partner_id)
    cursor.execute(
        """
        SELECT r.referral_id, r.referred_org_id AS org_id, o.org_name, r.created_at, r.qualified_at, r.commission_amount,
               rw.status AS rw_status, rw.expires_at AS rw_expires_at, rw.used_discount_amount,
               qo.base_amount AS first_base, qo.amount AS first_amount,
               (SELECT gp.plan_name FROM subscription s JOIN gen_plans gp ON gp.plan_id = s.plan_id
                WHERE s.org_id = r.referred_org_id AND s.status IN ('ACTIVE', 'EXPIRED')
                ORDER BY s.created_at DESC, s.id DESC LIMIT 1) AS plan_name
        FROM referral r
        LEFT JOIN org_info o ON o.org_id = r.referred_org_id
        LEFT JOIN referral_reward rw ON rw.referral_id = r.referral_id AND rw.role = 'referred'
        LEFT JOIN gen_orders qo ON qo.id = r.qualified_order_id
        WHERE r.partner_id = %s
        ORDER BY r.created_at DESC, r.referral_id DESC
        LIMIT %s
        """,
        (partner_id, MAX_CUSTOMER_ROWS),
    )
    customers, plans = [], {}
    used = expired = 0
    discount = Decimal("0.00")
    for r in cursor.fetchall():
        paid = r.get("qualified_at") is not None
        state = ref._reward_state(r.get("rw_status"), r.get("rw_expires_at")) if r.get("rw_status") else ""
        if state == "used":
            used += 1
            discount += _money(r.get("used_discount_amount"))
        elif state == "expired":
            expired += 1
        first = None
        if paid and (r.get("first_base") is not None or r.get("first_amount") is not None):
            first = _f(r["first_base"]) if r.get("first_base") is not None else float((_money(r["first_amount"]) / GST_DIVISOR).quantize(_CENT, rounding=ROUND_HALF_UP))
        if paid and r.get("plan_name"):
            plans[r["plan_name"]] = plans.get(r["plan_name"], 0) + 1
        customers.append({
            "referral_id": r["referral_id"],
            "org_id": r["org_id"],
            "org_name": r.get("org_name") or "",
            "signed_up_at": ref._iso_z(r.get("created_at")),
            "status": "paid" if paid else "signed_up",
            "qualified_at": ref._iso_z(r.get("qualified_at")),
            "plan_name": r.get("plan_name"),
            "first_payment": first,
            "discount_amount": _f(r["used_discount_amount"]) if state == "used" and r.get("used_discount_amount") is not None else None,
            "reward_status": state,
            "reward_expires_at": ref._iso_z(r.get("rw_expires_at")),
            "commission_amount": _f(r["commission_amount"]) if r.get("commission_amount") is not None else None,
        })

    months = _month_keys(6, today)
    trend = {k: {"month": k, "signups": 0, "converted": 0, "revenue": 0.0} for k in months}
    for c in customers:
        for field, stamp in (("signups", c["signed_up_at"]), ("converted", c["qualified_at"])):
            key = (stamp or "")[:7]
            if key in trend:
                trend[key][field] += 1
    cursor.execute(
        """
        SELECT to_char(COALESCE(o.activated_at, o.created_at), 'YYYY-MM') AS month,
               COALESCE(SUM(COALESCE(o.base_amount, ROUND(o.amount / 1.18, 2))), 0) AS revenue
        FROM referral r JOIN gen_orders o ON o.org_id = r.referred_org_id AND o.status = 'PAID'
        WHERE r.partner_id = %s
        GROUP BY 1
        """,
        (partner_id,),
    )
    for r in cursor.fetchall():
        if r["month"] in trend:
            trend[r["month"]]["revenue"] = _f(r["revenue"])

    cursor.execute(
        "SELECT payout_id, amount, paid_on, reference, note, created_by, created_at, voided_at, voided_by, void_reason FROM b2c_payout "
        "WHERE partner_id = %s ORDER BY paid_on DESC, payout_id DESC",
        (partner_id,),
    )
    payouts = [_payout_view(r) for r in cursor.fetchall()]

    signups, converted = partner["signups"], partner["converted"]
    insights = {
        "signups": signups,
        "converted": converted,
        "conversion_rate": round(100.0 * converted / signups, 1) if signups else 0.0,
        "revenue": partner["revenue"],
        "discount_given": float(discount),
        "commission_earned": partner["commission_earned"],
        "commission_paid": partner["commission_paid"],
        "commission_due": partner["commission_due"],
        "rewards_used": used,
        "rewards_expired": expired,
        "last_signup_at": customers[0]["signed_up_at"] if customers else None,
        "monthly": [trend[k] for k in months],
        "plans": [{"plan_name": n, "customers": c} for n, c in sorted(plans.items(), key=lambda kv: (-kv[1], kv[0]))],
    }
    return {"partner": partner, "insights": insights, "customers": customers, "payouts": payouts}


def _payout_view(row: Dict[str, Any]) -> Dict[str, Any]:
    paid_on = row.get("paid_on")
    return {
        "payout_id": row["payout_id"],
        "amount": _f(row.get("amount")),
        "paid_on": paid_on.isoformat() if isinstance(paid_on, (date, datetime)) else (str(paid_on) if paid_on else None),
        "reference": row.get("reference"),
        "note": row.get("note"),
        "created_by": row.get("created_by"),
        "created_at": ref._iso_z(row.get("created_at")),
        "status": "voided" if row.get("voided_at") is not None else "paid",
        "voided_at": ref._iso_z(row.get("voided_at")),
        "voided_by": row.get("voided_by"),
        "void_reason": row.get("void_reason"),
    }


def record_payout(cursor, partner_id: int, data: Dict[str, Any], admin: Optional[str] = None,
                  today: Optional[date] = None) -> Dict[str, Any]:
    """Record a payment to the promoter. The amount must be positive and at most the commission still due."""
    try:
        amount = Decimal(str(data.get("amount"))).quantize(_CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise _bad("Amount must be a number")
    if not amount.is_finite():                    # Decimal("NaN").quantize() does not raise; comparing it would
        raise _bad("Amount must be a number")
    if amount <= 0:
        raise _bad("Amount must be more than zero")
    raw_date = str(data.get("paid_on") or "").strip()
    try:
        paid_on = date.fromisoformat(raw_date)
    except ValueError:
        raise _bad("Paid on must be a date (YYYY-MM-DD)")
    if paid_on > (today or datetime.now(timezone.utc).date()) + timedelta(days=1):
        raise _bad("Paid on cannot be in the future")
    reference = _text(data, "reference", "Reference", 100)
    note = _text(data, "note", "Note", 1000)
    cursor.execute("SELECT partner_id FROM b2c_partner WHERE partner_id = %s FOR NO KEY UPDATE", (partner_id,))   # serialises two payouts, does not block a signup
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Partner code not found")
    due = Decimal(str(_load_partner(cursor, partner_id)["commission_due"]))
    if amount > due:
        raise _bad(f"Amount is more than the commission due (Rs {due:,.2f})")
    cursor.execute(
        """
        INSERT INTO b2c_payout (partner_id, amount, paid_on, reference, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING payout_id, amount, paid_on, reference, note, created_by, created_at, voided_at, voided_by, void_reason
        """,
        (partner_id, amount, paid_on, reference, note, admin),
    )
    payout = _payout_view(cursor.fetchone())
    return {"payout": payout, "partner": _load_partner(cursor, partner_id)}


def void_payout(cursor, partner_id: int, payout_id: int, data: Dict[str, Any], admin: Optional[str] = None) -> Dict[str, Any]:
    """Void a payout that was recorded by mistake (or whose transfer bounced). The row stays in the ledger with who / when / why,
    but it no longer counts as paid, so the commission due goes up by its amount. A payout can be voided once."""
    reason = _text(data or {}, "reason", "Reason", 300)
    cursor.execute("SELECT partner_id FROM b2c_partner WHERE partner_id = %s FOR NO KEY UPDATE", (partner_id,))   # same lock as a new payout
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Partner code not found")
    cursor.execute(
        """
        UPDATE b2c_payout SET voided_at = NOW(), voided_by = %s, void_reason = %s
        WHERE payout_id = %s AND partner_id = %s AND voided_at IS NULL
        RETURNING payout_id, amount, paid_on, reference, note, created_by, created_at, voided_at, voided_by, void_reason
        """,
        (admin, reason, payout_id, partner_id),
    )
    row = cursor.fetchone()
    if not row:
        cursor.execute("SELECT voided_at FROM b2c_payout WHERE payout_id = %s AND partner_id = %s", (payout_id, partner_id))
        existing = cursor.fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Payout not found")
        raise HTTPException(status_code=409, detail="This payout is already voided")
    return {"payout": _payout_view(row), "partner": _load_partner(cursor, partner_id)}


# ──────────────────────────────────────────────────────────────────────────── route helpers (own connection)
def _run(write: bool, what: str, fn):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        result = fn(cursor)
        if write:
            conn.commit()
        return result
    except HTTPException:
        if write:
            conn.rollback()
        raise
    except Exception:
        if write:
            conn.rollback()
        logger.exception("B2C referral: %s failed", what)
        raise HTTPException(status_code=500, detail=f"Could not {what}")
    finally:
        cursor.close()
        return_connection(conn)


async def operator_list_partners(q: str = "", status: str = "all"):
    return _run(False, "load the partner codes", lambda c: list_partners(c, q, status))


async def operator_create_partner(data: Dict[str, Any], admin: Optional[str] = None):
    return _run(True, "create the partner code", lambda c: create_partner(c, data, admin))


async def operator_update_partner(partner_id: int, data: Dict[str, Any]):
    return _run(True, "save the partner code", lambda c: update_partner(c, partner_id, data))


async def operator_partner_detail(partner_id: int):
    return _run(False, "load the partner", lambda c: partner_detail(c, partner_id))


async def operator_record_payout(partner_id: int, data: Dict[str, Any], admin: Optional[str] = None):
    return _run(True, "record the payout", lambda c: record_payout(c, partner_id, data, admin))


async def operator_void_payout(partner_id: int, payout_id: int, data: Dict[str, Any], admin: Optional[str] = None):
    return _run(True, "void the payout", lambda c: void_payout(c, partner_id, payout_id, data, admin))
