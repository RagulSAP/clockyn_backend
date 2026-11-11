"""Refer & Earn: referral codes, the two 10 % rewards a referral creates, using a reward at checkout and the operator views.

Business rules (fixed contract, see migrations/037_referral.sql for the tables):

* Every organisation has ONE permanent 6-character code (letters + digits, at least one of each, no ``0 O 1 I L``). It is created
  lazily (first view of the referral page, or right after registration); ``org_referral_code.code`` is UNIQUE and is the final
  guard, the generator only retries on a collision.
* A NEW organisation may enter a code while it registers. A bad / unknown code is refused with HTTP 400 BEFORE the organisation
  is created; the referral and its two rewards are written inside the registration transaction.
      referred organisation's reward: AVAILABLE at once;  referrer's reward: PENDING until the referred organisation's FIRST
      paid plan is activated (``qualify_referral_on_first_payment``), then AVAILABLE.
* A reward is usable once, on ``new`` / ``renew`` of Prime or Elite (``pricing.REFERRAL_ELIGIBLE_PLAN_IDS``), never with a coupon.
  The server picks the reward (oldest AVAILABLE first); the client only says ``apply_referral=true``. It is consumed (USED) in the
  same transaction that writes the subscription (``consume_reward_on_activation``).

Every function that writes takes the CALLER's cursor and never commits: the registration / payment transaction owns the commit.
Only the ``async`` route helpers at the bottom open their own connection.
"""
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from core_functions import pricing
from core_functions.db_function import get_db_connection, return_connection
from utils import branding

logger = logging.getLogger(__name__)

# Single source of truth lives in core_functions/pricing.py (a pure module); re-exported here for the callers of this module.
REFERRAL_PERCENT = pricing.REFERRAL_PERCENT
REFERRAL_ELIGIBLE_PLAN_IDS = pricing.REFERRAL_ELIGIBLE_PLAN_IDS
DEFAULT_ELIGIBLE_PLAN_NAMES = ("Prime", "Elite")      # only used when gen_plans cannot be read

CODE_LENGTH = 6
CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"               # no I, L, O
CODE_DIGITS = "23456789"                               # no 0, 1
CODE_ALPHABET = CODE_LETTERS + CODE_DIGITS
CODE_ATTEMPTS = 12                                     # fresh codes tried before giving up (a collision is ~1 in 10^8 at launch)
_CODE_RE = re.compile(r"^[A-Z0-9]{%d}$" % CODE_LENGTH)

STATUS_PENDING, STATUS_AVAILABLE, STATUS_USED, STATUS_VOID = "PENDING", "AVAILABLE", "USED", "VOID"
ROLE_REFERRER, ROLE_REFERRED = "referrer", "referred"

MSG_INVALID_CODE = "This referral code is not valid"
MSG_OWN_CODE = "You cannot use your own referral code"
MSG_ALREADY_REFERRED = "This organisation has already used a referral code"
FLAG_REFERRAL_ALREADY_USED = "REFERRAL_ALREADY_USED"   # gen_orders.payment_flag: payment honoured, the reward was already consumed

_SECURE = secrets.SystemRandom()


# ──────────────────────────────────────────────────────────────────────────── small helpers
def _iso_z(value) -> Optional[str]:
    """UTC ISO-8601 with a trailing Z (naive datetimes are taken as UTC, like the rest of the backend)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _num(value) -> Optional[float]:
    return None if value is None else float(value)


def _like(text: str) -> str:
    """A LIKE pattern that matches ``text`` anywhere, with the wildcards of ``text`` itself escaped."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ──────────────────────────────────────────────────────────────────────────── code generation
def normalise_code(code: Optional[str]) -> str:
    return (code or "").strip().upper()


def is_valid_code_format(code: Optional[str]) -> bool:
    """6 characters of the code alphabet with at least one letter and one digit (a typed code that cannot exist is refused early)."""
    text = normalise_code(code)
    if not _CODE_RE.match(text):
        return False
    return (any(c in CODE_LETTERS for c in text) and any(c in CODE_DIGITS for c in text)
            and all(c in CODE_ALPHABET for c in text))


def generate_code(rng=None) -> str:
    """One random code: CODE_LENGTH characters of CODE_ALPHABET with at least one letter and one digit (``secrets`` based)."""
    rng = rng or _SECURE
    chars = [rng.choice(CODE_LETTERS), rng.choice(CODE_DIGITS)]
    chars += [rng.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH - 2)]
    rng.shuffle(chars)
    return "".join(chars)


def get_or_create_code(cursor, org_id: str) -> str:
    """The organisation's code; created (and stored) when it has none yet. Safe under concurrency: the INSERT is
    ``ON CONFLICT DO NOTHING`` on both unique keys, so a taken code just means another attempt and a code the same
    organisation got meanwhile is returned. The caller commits."""
    cursor.execute("SELECT code FROM org_referral_code WHERE org_id = %s", (org_id,))
    row = cursor.fetchone()
    if row:
        return row["code"]
    for _ in range(CODE_ATTEMPTS):
        candidate = generate_code()
        cursor.execute(
            "INSERT INTO org_referral_code (org_id, code) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING code",
            (org_id, candidate),
        )
        row = cursor.fetchone()
        if row:
            return row["code"]
        cursor.execute("SELECT code FROM org_referral_code WHERE org_id = %s", (org_id,))
        row = cursor.fetchone()
        if row:                                   # the same organisation got a code concurrently
            return row["code"]
    logger.error("Could not generate a free referral code for org %s after %s attempts", org_id, CODE_ATTEMPTS)
    raise HTTPException(status_code=503, detail="Could not create your referral code right now. Please try again.")


# ──────────────────────────────────────────────────────────────────────────── registration
def find_referrer_by_code(cursor, code: Optional[str]) -> Optional[Dict[str, Any]]:
    """{'org_id', 'org_name'} of the organisation that owns ``code`` (an existing, not deleted one), else None."""
    text = normalise_code(code)
    if not is_valid_code_format(text):
        return None
    cursor.execute(
        """
        SELECT c.org_id, o.org_name
        FROM org_referral_code c
        JOIN org_info o ON o.org_id = c.org_id
        WHERE c.code = %s AND COALESCE(o.status, 1) <> 2
        LIMIT 1
        """,
        (text,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def resolve_code_for_registration(cursor, code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Call BEFORE the organisation is created. None when no code was entered; the referrer row when it is valid;
    HTTP 400 ("This referral code is not valid") otherwise, so a typo never creates an organisation."""
    if not (code or "").strip():
        return None
    referrer = find_referrer_by_code(cursor, code)
    if not referrer:
        raise HTTPException(status_code=400, detail=MSG_INVALID_CODE)
    return referrer


def attach_referral_at_registration(cursor, new_org_id: str, code: Optional[str]) -> Optional[int]:
    """Record that ``new_org_id`` was referred by the owner of ``code`` and create the two rewards (referred: AVAILABLE,
    referrer: PENDING). Runs inside the registration transaction (the caller commits or rolls back everything together).
    Returns the referral id, or None when no code was entered. HTTP 400 for an unknown code, the organisation's own code,
    or an organisation that was already referred (the code cannot be added later)."""
    referrer = resolve_code_for_registration(cursor, code)
    if referrer is None:
        return None
    referrer_org_id = referrer["org_id"]
    if referrer_org_id == new_org_id:
        raise HTTPException(status_code=400, detail=MSG_OWN_CODE)
    text = normalise_code(code)
    cursor.execute(
        """
        INSERT INTO referral (referrer_org_id, referred_org_id, code)
        VALUES (%s, %s, %s)
        ON CONFLICT (referred_org_id) DO NOTHING
        RETURNING referral_id
        """,
        (referrer_org_id, new_org_id, text),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=400, detail=MSG_ALREADY_REFERRED)
    referral_id = row["referral_id"]
    cursor.execute(
        """
        INSERT INTO referral_reward (referral_id, org_id, role, status, percent, available_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        """,
        (referral_id, new_org_id, ROLE_REFERRED, STATUS_AVAILABLE, REFERRAL_PERCENT),
    )
    cursor.execute(
        """
        INSERT INTO referral_reward (referral_id, org_id, role, status, percent, available_at)
        VALUES (%s, %s, %s, %s, %s, NULL)
        """,
        (referral_id, referrer_org_id, ROLE_REFERRER, STATUS_PENDING, REFERRAL_PERCENT),
    )
    return referral_id


# ──────────────────────────────────────────────────────────────────────────── checkout
def load_available_reward(cursor, org_id: str) -> pricing.ReferralOffer:
    """How many AVAILABLE rewards the organisation has and the one that would be used (the oldest). The id never comes from
    the client. One query: the count is a window function evaluated before LIMIT."""
    cursor.execute(
        """
        SELECT reward_id, percent, COUNT(*) OVER () AS available_count
        FROM referral_reward
        WHERE org_id = %s AND status = 'AVAILABLE'
        ORDER BY COALESCE(available_at, created_at) ASC, reward_id ASC
        LIMIT 1
        """,
        (org_id,),
    )
    row = cursor.fetchone()
    if not row:
        return pricing.ReferralOffer(available_count=0, reward_id=None, percent=pricing.D(REFERRAL_PERCENT))
    return pricing.ReferralOffer(
        available_count=int(row["available_count"]),
        reward_id=int(row["reward_id"]),
        percent=pricing.to_decimal(row.get("percent") or REFERRAL_PERCENT),
    )


def consume_reward_on_activation(cursor, *, reward_id: int, org_id: str, order_id: int, plan_name: Optional[str],
                                 discount_amount, invoice_number: Optional[str] = None) -> bool:
    """Mark the order's reward USED. ``UPDATE ... WHERE status = 'AVAILABLE'`` is the guard: True when this call consumed it,
    False when it was not available any more (consumed by a concurrent order, voided, or not this organisation's).
    Runs in the payment transaction; never commits."""
    cursor.execute(
        """
        UPDATE referral_reward
        SET status = 'USED', used_at = NOW(), used_order_id = %s, used_invoice_number = %s,
            used_plan_name = %s, used_discount_amount = %s
        WHERE reward_id = %s AND org_id = %s AND status = 'AVAILABLE'
        """,
        (order_id, invoice_number, plan_name, discount_amount, reward_id, org_id),
    )
    return (cursor.rowcount or 0) > 0


def set_reward_invoice_number(cursor, reward_id: int, order_id: int, invoice_number: str) -> None:
    """Fill in the invoice number once the invoice row exists (it is written after the reward was consumed)."""
    cursor.execute(
        "UPDATE referral_reward SET used_invoice_number = %s WHERE reward_id = %s AND used_order_id = %s AND status = 'USED'",
        (invoice_number, reward_id, order_id),
    )


def qualify_referral_on_first_payment(cursor, org_id: str) -> Optional[int]:
    """The referred organisation's first paid activation: stamp ``referral.qualified_at`` and make the REFERRER's PENDING reward
    AVAILABLE. Returns the referral id when this call qualified it, None otherwise (organisation not referred, or already
    qualified: a re-delivered webhook changes nothing). The conditional UPDATE locks the row, so two concurrent activations
    cannot both qualify. Runs in the payment transaction; never commits."""
    cursor.execute(
        "UPDATE referral SET qualified_at = NOW() WHERE referred_org_id = %s AND qualified_at IS NULL RETURNING referral_id",
        (org_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    referral_id = row["referral_id"]
    cursor.execute(
        """
        UPDATE referral_reward SET status = 'AVAILABLE', available_at = NOW()
        WHERE referral_id = %s AND role = 'referrer' AND status = 'PENDING'
        """,
        (referral_id,),
    )
    return referral_id


# ──────────────────────────────────────────────────────────────────────────── org-admin view
def _eligible_plan_names(cursor) -> List[str]:
    try:
        cursor.execute(
            "SELECT plan_name FROM gen_plans WHERE plan_id = ANY(%s) ORDER BY plan_id",
            (list(REFERRAL_ELIGIBLE_PLAN_IDS),),
        )
        names = [(r["plan_name"] or "").strip() for r in cursor.fetchall()]
        names = [n for n in names if n]
    except Exception:                              # never fail the page over a label
        logger.exception("Could not read the eligible plan names")
        names = []
    return names or list(DEFAULT_ELIGIBLE_PLAN_NAMES)


def share_message(code: str) -> str:
    return (f"Join me on {branding.app_name()} - the easy way to manage staff attendance and payroll. "
            f"Use my referral code {code} when you create your account and we both get {REFERRAL_PERCENT}% off our plan.")


def _reward_view(row, prefix: str = "") -> Optional[Dict[str, Any]]:
    rid = row.get(prefix + "reward_id")
    if rid is None:
        return None
    return {
        "reward_id": rid,
        "status": str(row.get(prefix + "status") or "").lower(),
        "percent": int(row.get(prefix + "percent") or REFERRAL_PERCENT),
        "used_at": _iso_z(row.get(prefix + "used_at")),
        "plan_name": row.get(prefix + "used_plan_name"),
        "discount_amount": _num(row.get(prefix + "used_discount_amount")),
    }


def get_referral_summary(cursor, org_id: str) -> Dict[str, Any]:
    """The payload of GET /referral/{org_id}. Creates the code when the organisation has none (the caller commits)."""
    code = get_or_create_code(cursor, org_id)

    cursor.execute(
        """
        SELECT r.referral_id, r.created_at, r.qualified_at, o.org_name,
               rw.reward_id, rw.status, rw.percent, rw.used_at, rw.used_plan_name, rw.used_discount_amount
        FROM referral r
        LEFT JOIN org_info o ON o.org_id = r.referred_org_id
        LEFT JOIN referral_reward rw ON rw.referral_id = r.referral_id AND rw.role = 'referrer'
        WHERE r.referrer_org_id = %s
        ORDER BY r.created_at DESC, r.referral_id DESC
        """,
        (org_id,),
    )
    referrals = []
    converted = 0
    for r in cursor.fetchall():
        paid = r.get("qualified_at") is not None
        converted += 1 if paid else 0
        referrals.append({
            "referral_id": r["referral_id"],
            "org_name": r.get("org_name") or "",
            "joined_at": _iso_z(r.get("created_at")),
            "status": "paid" if paid else "signed_up",
            "reward": _reward_view(r),
        })

    cursor.execute(
        "SELECT status, COUNT(*) AS cnt FROM referral_reward WHERE org_id = %s GROUP BY status",
        (org_id,),
    )
    counts = {str(r["status"]).upper(): int(r["cnt"]) for r in cursor.fetchall()}

    cursor.execute(
        """
        SELECT rw.reward_id, rw.status, rw.percent, rw.used_at, rw.used_plan_name, rw.used_discount_amount,
               ro.org_name AS referred_by_org_name
        FROM referral r
        JOIN referral_reward rw ON rw.referral_id = r.referral_id AND rw.role = 'referred' AND rw.org_id = r.referred_org_id
        LEFT JOIN org_info ro ON ro.org_id = r.referrer_org_id
        WHERE r.referred_org_id = %s
        LIMIT 1
        """,
        (org_id,),
    )
    mine = cursor.fetchone()
    my_reward = None
    if mine:
        my_reward = _reward_view(mine)
        my_reward["referred_by_org_name"] = mine.get("referred_by_org_name") or ""

    return {
        "code": code,
        "percent": REFERRAL_PERCENT,
        "eligible_plans": _eligible_plan_names(cursor),
        "share_message": share_message(code),
        "summary": {
            "referred_count": len(referrals),
            "converted_count": converted,
            "rewards_available": counts.get(STATUS_AVAILABLE, 0),
            "rewards_pending": counts.get(STATUS_PENDING, 0),
            "rewards_used": counts.get(STATUS_USED, 0),
        },
        "referrals": referrals,
        "my_reward": my_reward,
    }


# ──────────────────────────────────────────────────────────────────────────── operator views
STATUS_FILTERS = ("all", "signed_up", "paid")
MAX_OPERATOR_ROWS = 2000


def _operator_reward(row, prefix: str) -> Optional[Dict[str, Any]]:
    rid = row.get(prefix + "reward_id")
    if rid is None:
        return None
    return {
        "reward_id": rid,
        "status": str(row.get(prefix + "status") or "").lower(),
        "used_at": _iso_z(row.get(prefix + "used_at")),
        "plan_name": row.get(prefix + "plan_name"),
        "discount_amount": _num(row.get(prefix + "discount_amount")),
        "invoice_number": row.get(prefix + "invoice_number"),
    }


def operator_overview(cursor, status: str = "all", q: str = "") -> Dict[str, Any]:
    """The payload of GET /gen-dashboard/referrals. ``summary`` and ``top_referrers`` cover ALL referrals; ``referrals`` is
    filtered by ``status`` (all | signed_up | paid) and ``q`` (either organisation's name, either organisation's code, the code)."""
    status = (status or "all").strip().lower()
    if status not in STATUS_FILTERS:
        raise HTTPException(status_code=400, detail="status must be one of all, signed_up, paid")
    q = (q or "").strip()[:100]

    cursor.execute("SELECT COUNT(*) AS total_referrals, COUNT(qualified_at) AS converted FROM referral")
    ref = cursor.fetchone() or {}
    cursor.execute(
        """
        SELECT COUNT(*) AS issued,
               COUNT(*) FILTER (WHERE status = 'AVAILABLE') AS available,
               COUNT(*) FILTER (WHERE status = 'PENDING') AS pending,
               COUNT(*) FILTER (WHERE status = 'USED') AS used,
               COALESCE(SUM(used_discount_amount) FILTER (WHERE status = 'USED'), 0) AS discount_given
        FROM referral_reward
        """
    )
    rw = cursor.fetchone() or {}
    summary = {
        "total_referrals": int(ref.get("total_referrals") or 0),
        "converted": int(ref.get("converted") or 0),
        "rewards_issued": int(rw.get("issued") or 0),
        "rewards_available": int(rw.get("available") or 0),
        "rewards_pending": int(rw.get("pending") or 0),
        "rewards_used": int(rw.get("used") or 0),
        "discount_given": float(rw.get("discount_given") or 0),
    }

    cursor.execute(
        """
        SELECT r.referrer_org_id AS org_id, o.org_name, c.code,
               COUNT(*) AS referrals, COUNT(r.qualified_at) AS converted
        FROM referral r
        LEFT JOIN org_info o ON o.org_id = r.referrer_org_id
        LEFT JOIN org_referral_code c ON c.org_id = r.referrer_org_id
        GROUP BY r.referrer_org_id, o.org_name, c.code
        ORDER BY converted DESC, referrals DESC, o.org_name ASC
        LIMIT 10
        """
    )
    top = [{"org_id": r["org_id"], "org_name": r.get("org_name") or "", "code": r.get("code"),
            "referrals": int(r["referrals"]), "converted": int(r["converted"])} for r in cursor.fetchall()]

    where, params = [], []
    if status == "paid":
        where.append("r.qualified_at IS NOT NULL")
    elif status == "signed_up":
        where.append("r.qualified_at IS NULL")
    if q:
        pattern = _like(q)
        where.append("(ro.org_name ILIKE %s OR rd.org_name ILIKE %s OR r.code ILIKE %s OR cd.code ILIKE %s)")
        params += [pattern, pattern, pattern, pattern]
    sql = """
        SELECT r.referral_id, r.code, r.referrer_org_id, ro.org_name AS referrer_org_name,
               r.referred_org_id, rd.org_name AS referred_org_name, r.created_at, r.qualified_at,
               a.reward_id AS ra_reward_id, a.status AS ra_status, a.used_at AS ra_used_at, a.used_plan_name AS ra_plan_name,
               a.used_discount_amount AS ra_discount_amount, a.used_invoice_number AS ra_invoice_number,
               b.reward_id AS rb_reward_id, b.status AS rb_status, b.used_at AS rb_used_at, b.used_plan_name AS rb_plan_name,
               b.used_discount_amount AS rb_discount_amount, b.used_invoice_number AS rb_invoice_number
        FROM referral r
        LEFT JOIN org_info ro ON ro.org_id = r.referrer_org_id
        LEFT JOIN org_info rd ON rd.org_id = r.referred_org_id
        LEFT JOIN org_referral_code cd ON cd.org_id = r.referred_org_id
        LEFT JOIN referral_reward a ON a.referral_id = r.referral_id AND a.role = 'referrer'
        LEFT JOIN referral_reward b ON b.referral_id = r.referral_id AND b.role = 'referred'
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY r.created_at DESC, r.referral_id DESC LIMIT %s"
    params.append(MAX_OPERATOR_ROWS)
    cursor.execute(sql, tuple(params))
    rows = []
    for r in cursor.fetchall():
        rows.append({
            "referral_id": r["referral_id"],
            "code": r["code"],
            "referrer_org_id": r["referrer_org_id"],
            "referrer_org_name": r.get("referrer_org_name") or "",
            "referred_org_id": r["referred_org_id"],
            "referred_org_name": r.get("referred_org_name") or "",
            "signed_up_at": _iso_z(r.get("created_at")),
            "qualified_at": _iso_z(r.get("qualified_at")),
            "status": "paid" if r.get("qualified_at") is not None else "signed_up",
            "referrer_reward": _operator_reward(r, "ra_"),
            "referred_reward": _operator_reward(r, "rb_"),
        })
    return {"summary": summary, "top_referrers": top, "referrals": rows}


def void_reward(cursor, reward_id: int) -> Dict[str, Any]:
    """Withdraw an AVAILABLE or PENDING reward (the guard is in the UPDATE). 404 unknown id, 409 when it is USED / VOID."""
    cursor.execute(
        "UPDATE referral_reward SET status = 'VOID' WHERE reward_id = %s AND status IN ('AVAILABLE', 'PENDING') RETURNING reward_id",
        (reward_id,),
    )
    if cursor.fetchone():
        return {"reward_id": reward_id, "status": "void"}
    cursor.execute("SELECT status FROM referral_reward WHERE reward_id = %s", (reward_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reward not found")
    raise HTTPException(status_code=409, detail=f"Only an available or pending reward can be voided (this one is {str(row['status']).lower()})")


# ──────────────────────────────────────────────────────────────────────────── route helpers (own connection)
def _connect():
    conn = get_db_connection()
    return conn, conn.cursor(cursor_factory=RealDictCursor)


def _release(conn, cursor):
    if cursor:
        cursor.close()
    if conn:
        return_connection(conn)


async def validate_code_public(code: Optional[str]) -> Dict[str, Any]:
    """GET /referral/validate: the signup screen checks a typed code. Reveals ONLY the referrer's organisation name."""
    text = normalise_code(code)
    invalid = {"valid": False, "message": MSG_INVALID_CODE}
    if not is_valid_code_format(text):
        return invalid
    conn = cursor = None
    try:
        conn, cursor = _connect()
        referrer = find_referrer_by_code(cursor, text)
        if not referrer:
            return invalid
        return {"valid": True, "referrer_org_name": referrer.get("org_name") or ""}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Referral code validation failed")
        raise HTTPException(status_code=500, detail="Could not check the referral code")
    finally:
        _release(conn, cursor)


async def get_org_referral(org_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """GET /referral/{org_id}. ``user_id`` (optional, sent by the app) is checked like the billing routes: it must be the
    organisation's admin (BILLING_ENFORCE_ORG_OWNER). The token-based checks are made by the router."""
    from core_functions import gen_orders_functions as orders_fn

    conn = cursor = None
    try:
        conn, cursor = _connect()
        cursor.execute("SELECT 1 AS ok FROM org_info WHERE org_id = %s LIMIT 1", (org_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Organisation not found")
        if user_id and orders_fn._owner_check_enabled():
            orders_fn._check_org_owner(cursor, user_id, org_id)
        data = get_referral_summary(cursor, org_id)
        conn.commit()                               # the lazily created code
        return data
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Referral summary failed for org %s", org_id)
        raise HTTPException(status_code=500, detail="Could not load your referrals")
    finally:
        _release(conn, cursor)


async def operator_referrals(status: str = "all", q: str = "") -> Dict[str, Any]:
    conn = cursor = None
    try:
        conn, cursor = _connect()
        return operator_overview(cursor, status, q)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Operator referral list failed")
        raise HTTPException(status_code=500, detail="Could not load the referrals")
    finally:
        _release(conn, cursor)


async def operator_void_reward(reward_id: int) -> Dict[str, Any]:
    conn = cursor = None
    try:
        conn, cursor = _connect()
        result = void_reward(cursor, reward_id)
        conn.commit()
        logger.info("Referral reward %s voided by an operator", reward_id)
        return result
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception:
        if conn:
            conn.rollback()
        logger.exception("Voiding referral reward %s failed", reward_id)
        raise HTTPException(status_code=500, detail="Could not void the reward")
    finally:
        _release(conn, cursor)
