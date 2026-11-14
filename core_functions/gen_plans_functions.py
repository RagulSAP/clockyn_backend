import logging
import pytz
from datetime import date
from typing import Optional
from fastapi import HTTPException, Request
from core_functions.db_function import get_db_connection, return_connection
from core_functions.pricing import TRIAL_STAFF_LIMIT, plan_family_id
from psycopg2.extras import RealDictCursor
from utils.timezone import get_utc_now, IST, ist_today

ist = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)

# The plan catalogue, as the app and the operator console read it. plan_amount is the list price per staff per MONTH (the
# same on every duration of a plan); free_months of plan_month are not charged; offer_text is optional marketing wording.
# Cheapest first, and within one plan the shorter duration first.
_PLAN_COLUMNS = "plan_id, plan_name, plan_amount, plan_month, free_months AS plan_free_months, offer_text AS plan_offer_text"
_PLAN_LIST_SQL = f"SELECT {_PLAN_COLUMNS} FROM gen_plans ORDER BY plan_amount ASC, plan_month ASC"


def expire_due_subscriptions(today_ist: date, org_id: Optional[str] = None, cursor=None) -> int:
    """Write status 'EXPIRED' on every ACTIVE subscription whose end date is before ``today_ist`` (IST, end date inclusive).

    Called opportunistically when a subscription / plan status is read, so no scheduler is needed. ``org_id`` limits it
    to one organisation (the read paths); ``None`` sweeps all (for a future scheduler). With a ``cursor`` only the
    UPDATE runs and the caller commits; without one a connection is taken and committed. Returns the rows changed.
    """
    sql = "UPDATE subscription SET status = 'EXPIRED', updated_at = NOW() WHERE status = 'ACTIVE' AND end_date < %s"
    params = [today_ist]
    if org_id is not None:
        sql += " AND org_id = %s"
        params.append(org_id)
    if cursor is not None:
        cursor.execute(sql, tuple(params))
        return cursor.rowcount or 0
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        changed = cur.rowcount or 0
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        return_connection(conn)


def _expire_on_read(conn, cursor, org_id: str, today_ist: date) -> None:
    """Best-effort expiry write before a status read; a failure here never breaks the read."""
    try:
        if expire_due_subscriptions(today_ist, org_id=org_id, cursor=cursor):
            conn.commit()
    except Exception:
        logger.warning("subscription expiry sweep failed for org %s", org_id, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass


async def get_plans():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(_PLAN_LIST_SQL)
        plans = cursor.fetchall()
        return {'success': True, 'data': [dict(p) for p in plans]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def get_org_subscription(org_id: str):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        today = ist_today()
        _expire_on_read(conn, cursor, org_id, today)
        cursor.execute("""
            SELECT
                ps.plan_id,
                ps.status,
                ps.start_date,
                ps.end_date,
                COALESCE(ps.staff_limit, 10) AS staff_limit,
                gp.plan_name,
                gp.plan_amount,
                gp.plan_month,
                gp.free_months AS plan_free_months
            FROM subscription ps
            JOIN gen_plans gp ON ps.plan_id = gp.plan_id
            WHERE ps.org_id = %s AND ps.status IN ('ACTIVE', 'EXPIRED')
            ORDER BY ps.created_at DESC
            LIMIT 1
        """, (org_id,))
        sub = cursor.fetchone()

        # Free trial check
        cursor.execute("""
            SELECT end_date, is_active FROM free_trial WHERE org_id = %s
        """, (org_id,))
        trial = cursor.fetchone()
        is_free_trial = bool(
            trial and trial['is_active'] and trial['end_date'] >= today
        )
        free_trial_end = str(trial['end_date']) if trial else None

        if not sub:
            return {
                'success': True,
                'data': {
                    'is_free_trial': is_free_trial,
                    'free_trial_end': free_trial_end,
                    'billing_date': None,
                    'plan_name': 'Free Trial' if is_free_trial else None,
                    'plan_amount': None,
                    'start_date': None,
                    'end_date': free_trial_end,
                    'staff_limit': TRIAL_STAFF_LIMIT,
                    'status': 'FREE_TRIAL' if is_free_trial else 'INACTIVE',
                    # additive, schema-stable placeholder (the app derives the same state itself)
                    'plan_id': None,
                    'plan_month': None,
                    'plan_free_months': None,
                    'billing_state': 'trial' if is_free_trial else 'none',
                    'trial_staff_limit': TRIAL_STAFF_LIMIT,
                }
            }
        result = dict(sub)
        result['is_expired'] = bool(sub['status'] == 'EXPIRED' or (sub['end_date'] and sub['end_date'] < today))
        result['is_free_trial'] = is_free_trial
        result['free_trial_end'] = free_trial_end
        result['billing_date'] = str(sub['start_date']) if sub.get('start_date') else None
        result['billing_state'] = 'paid_expired' if result['is_expired'] else 'paid_active'
        result['trial_staff_limit'] = TRIAL_STAFF_LIMIT
        return {'success': True, 'data': result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def check_org_access(org_id: str):
    """Raises 403 if both free trial and active subscription are expired/inactive."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        today = ist_today()
        cursor.execute(
            "SELECT end_date, is_active FROM free_trial WHERE org_id = %s", (org_id,)
        )
        trial = cursor.fetchone()
        if bool(trial and trial['is_active'] and trial['end_date'] >= today):
            return
        cursor.execute("""
            SELECT 1 FROM subscription
            WHERE org_id = %s AND status = 'ACTIVE' AND end_date >= %s
            LIMIT 1
        """, (org_id, today))
        if cursor.fetchone() is None:
            raise HTTPException(
                status_code=403,
                detail='Access denied: your free trial and plan have expired. Please subscribe to continue.'
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def get_org_plan_status(org_id: str) -> dict:
    """Returns plan_id, is_free_trial, is_expired for the org."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        today = ist_today()          # IST calendar day; the end date itself is still valid
        _expire_on_read(conn, cursor, org_id, today)

        # Always check free trial first — it takes priority
        cursor.execute(
            "SELECT end_date, is_active FROM free_trial WHERE org_id = %s", (org_id,)
        )
        trial = cursor.fetchone()
        if trial and trial['is_active'] and trial['end_date'] >= today:
            return {'plan_id': None, 'is_free_trial': True, 'is_expired': False}

        # Free trial not active — check paid subscription (EXPIRED rows are written by expire_due_subscriptions)
        cursor.execute("""
            SELECT ps.plan_id, gp.plan_name, ps.end_date, ps.status
            FROM subscription ps
            LEFT JOIN gen_plans gp ON gp.plan_id = ps.plan_id
            WHERE ps.org_id = %s AND ps.status IN ('ACTIVE', 'EXPIRED')
            ORDER BY ps.created_at DESC LIMIT 1
        """, (org_id,))
        sub = cursor.fetchone()

        if sub:
            # Every duration of a plan (Core 1 / 6 / 12 months ...) reports its TIER id (1 Core, 2 Prime, 3 Elite): the
            # feature matrix here and in the app is keyed on the tier, not on the duration row.
            tier_id = plan_family_id(sub.get('plan_name'), sub['plan_id'])
            if sub['status'] == 'ACTIVE' and sub['end_date'] >= today:
                return {'plan_id': tier_id, 'is_free_trial': False, 'is_expired': False}
            return {'plan_id': tier_id, 'is_free_trial': False, 'is_expired': True}

        return {'plan_id': None, 'is_free_trial': False, 'is_expired': True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


CORE_PLAN_ID  = 1
PRIME_PLAN_ID = 2
ELITE_PLAN_ID = 3

# Features blocked per ACTIVE plan (not expired)
# Core  (1): staff_location and crm blocked only
# Prime (2): crm blocked only
# Elite (3): nothing blocked
PLAN_BLOCKED_FEATURES: dict = {
    CORE_PLAN_ID:  {'staff_location', 'crm'},
    PRIME_PLAN_ID: {'crm'},
    ELITE_PLAN_ID: set(),
}


async def check_plan_feature(org_id: str, feature: str, is_admin: bool = True):
    """
    Raises 403 if the feature is not allowed for the org's current plan (the matrix is PLAN_BLOCKED_FEATURES).
    Free trial  → all features allowed.
    Expired (or no plan after the trial) → every gated feature is blocked.
    Core  (1)   → staff_location and crm blocked.
    Prime (2)   → crm blocked.
    Elite (3)   → nothing blocked.
    chat, salary_structure and camera_punch never block on an active paid plan (only when expired).
    """
    status = await get_org_plan_status(org_id)

    if status['is_free_trial']:
        return

    admin_msg = 'Your plan has expired. Please renew your plan to continue using this feature.'
    emp_msg   = 'Your plan has expired. Please contact your admin.'

    if status['is_expired']:
        raise HTTPException(status_code=403, detail=admin_msg if is_admin else emp_msg)

    blocked = PLAN_BLOCKED_FEATURES.get(status['plan_id'], set())
    if feature in blocked:
        raise HTTPException(status_code=403, detail=admin_msg if is_admin else emp_msg)


_ORG_KEYS = ('org_id',)
_USER_KEYS = ('user_id', 'sender_id', 'created_by')


def _lookup_org_for_user(user_id: str) -> Optional[str]:
    """An admin carries org_id; managers and staff carry their organisation as parent_id."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("SELECT org_id, parent_id FROM users WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        return_connection(conn)
    if not row:
        return None
    return row.get('org_id') or row.get('parent_id')


def _lookup_org_for_group(group_id: str) -> Optional[str]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute('SELECT org_id FROM "group" WHERE group_id = %s', (group_id,))
            row = cursor.fetchone()
        finally:
            cursor.close()
    finally:
        return_connection(conn)
    return row.get('org_id') if row else None


async def _request_fields(request: Request) -> dict:
    """Top-level string fields of a JSON / form body ({} when there is none or it cannot be read)."""
    content_type = (request.headers.get('content-type') or '').lower()
    try:
        if 'application/json' in content_type:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        if 'multipart/form-data' in content_type or 'application/x-www-form-urlencoded' in content_type:
            form = await request.form()
            return {k: v for k, v in form.items() if isinstance(v, str)}
    except Exception:
        return {}
    return {}


async def org_id_from_request(request: Request) -> Optional[str]:
    """The organisation a request is about: org_id (path / query / body), else via user_id / sender_id / created_by, else via group_id."""
    body = await _request_fields(request)
    sources = (request.path_params, request.query_params, body)
    for key in _ORG_KEYS:
        for src in sources:
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in _USER_KEYS:
        for src in sources:
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                return _lookup_org_for_user(value.strip())
    for src in sources:
        value = src.get('group_id')
        if isinstance(value, str) and value.strip():
            return _lookup_org_for_group(value.strip())
    return None


def plan_gate(feature: str, is_admin: bool = True):
    """FastAPI dependency: the same 403 as check_plan_feature, for a whole router or one route.

        router = APIRouter(dependencies=[Depends(plan_gate('salary_structure'))])

    A request whose organisation cannot be determined is not gated here (its handler decides).
    """
    async def _gate(request: Request):
        org_id = await org_id_from_request(request)
        if org_id:
            await check_plan_feature(org_id, feature, is_admin=is_admin)
    return _gate


async def get_first_coupon():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        today = ist_today()
        cursor.execute(
            "SELECT coupon_id, coupon_name, discount FROM gen_coupons WHERE status = 1 "
            "AND (valid_from IS NULL OR valid_from <= %s) AND (valid_until IS NULL OR valid_until >= %s) "
            "ORDER BY coupon_id ASC LIMIT 1",
            (today, today)
        )
        coupon = cursor.fetchone()
        if not coupon:
            return {'success': False, 'message': 'No active coupons'}
        return {'success': True, 'data': dict(coupon)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def validate_coupon(coupon_name: str):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        today = ist_today()
        cursor.execute(
            "SELECT coupon_id, coupon_name, discount FROM gen_coupons WHERE UPPER(coupon_name) = UPPER(%s) AND status = 1 "
            "AND (valid_from IS NULL OR valid_from <= %s) AND (valid_until IS NULL OR valid_until >= %s)",
            (coupon_name, today, today)
        )
        coupon = cursor.fetchone()
        if not coupon:
            return {'success': False, 'message': 'Invalid or expired coupon'}
        return {'success': True, 'data': dict(coupon)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def list_coupons():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT coupon_id, coupon_name, discount, status FROM gen_coupons ORDER BY coupon_id ASC")
        coupons = cursor.fetchall()
        return {'success': True, 'data': [dict(c) for c in coupons]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def create_coupon(coupon_name: str, discount: float):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            INSERT INTO gen_coupons (coupon_id, coupon_name, discount, status)
            VALUES ((SELECT COALESCE(MAX(coupon_id), 0) + 1 FROM gen_coupons), %s, %s, 1)
            RETURNING coupon_id, coupon_name, discount, status
            """,
            (coupon_name.upper(), discount)
        )
        conn.commit()
        coupon = cursor.fetchone()
        return {'success': True, 'data': dict(coupon)}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def delete_coupon(coupon_id: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM gen_coupons WHERE coupon_id = %s", (coupon_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail='Coupon not found')
        conn.commit()
        return {'success': True, 'message': 'Coupon deleted'}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def update_coupon(coupon_id: int, coupon_name: str, discount: float, status: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "UPDATE gen_coupons SET coupon_name=%s, discount=%s, status=%s, updated_at=NOW() WHERE coupon_id=%s RETURNING coupon_id, coupon_name, discount, status",
            (coupon_name.upper(), discount, status, coupon_id)
        )
        conn.commit()
        coupon = cursor.fetchone()
        if not coupon:
            raise HTTPException(status_code=404, detail='Coupon not found')
        return {'success': True, 'data': dict(coupon)}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def list_plans():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(_PLAN_LIST_SQL)
        plans = cursor.fetchall()
        return {'success': True, 'data': [dict(p) for p in plans]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


def _check_plan_terms(plan_month: int, free_months: int, offer_text: Optional[str]) -> Optional[str]:
    """Normalised offer text (None when blank); raises 400 for a duration or free-month count that cannot be priced."""
    if plan_month < 1:
        raise HTTPException(status_code=400, detail='plan_month must be at least 1')
    if free_months < 0 or free_months >= plan_month:
        raise HTTPException(status_code=400, detail='free_months must be from 0 to one less than plan_month')
    text = (offer_text or '').strip()
    if len(text) > 80:
        raise HTTPException(status_code=400, detail='offer_text can be at most 80 characters')
    return text or None


async def create_plan(plan_name: str, plan_amount: float, plan_month: int, free_months: int = 0, offer_text: Optional[str] = None):
    offer_text = _check_plan_terms(plan_month, free_months, offer_text)
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            INSERT INTO gen_plans (plan_id, plan_name, plan_amount, plan_month, free_months, offer_text)
            VALUES ((SELECT COALESCE(MAX(plan_id), 0) + 1 FROM gen_plans), %s, %s, %s, %s, %s)
            RETURNING plan_id, plan_name, plan_amount, plan_month, free_months AS plan_free_months, offer_text AS plan_offer_text
            """,
            (plan_name, plan_amount, plan_month, free_months, offer_text)
        )
        conn.commit()
        plan = cursor.fetchone()
        return {'success': True, 'data': dict(plan)}
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def update_plan(plan_id: int, plan_name: str, plan_amount: float, plan_month: int,
                      free_months: Optional[int] = None, offer_text: Optional[str] = None):
    """``free_months`` / ``offer_text`` left out (None) keep the stored value, so an older console that only knows name, amount
    and months cannot wipe a free-month offer; an empty ``offer_text`` clears the wording."""
    if free_months is not None:
        _check_plan_terms(plan_month, free_months, None)
    elif plan_month < 1:
        raise HTTPException(status_code=400, detail='plan_month must be at least 1')
    if offer_text is not None:
        offer_text = _check_plan_terms(plan_month, free_months or 0, offer_text) or ''
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "UPDATE gen_plans SET plan_name=%s, plan_amount=%s, plan_month=%s, free_months=COALESCE(%s, free_months), "
            "offer_text=CASE WHEN %s IS NULL THEN offer_text ELSE NULLIF(%s, '') END, updated_at=NOW() "
            "WHERE plan_id=%s RETURNING plan_id, plan_name, plan_amount, plan_month, free_months AS plan_free_months, offer_text AS plan_offer_text",
            (plan_name, plan_amount, plan_month, free_months, offer_text, offer_text, plan_id)
        )
        conn.commit()
        plan = cursor.fetchone()
        if not plan:
            raise HTTPException(status_code=404, detail='Plan not found')
        return {'success': True, 'data': dict(plan)}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)


async def delete_plan(plan_id: int):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        # Check if any subscriptions or orders reference this plan
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM subscription WHERE plan_id = %s", (plan_id,)
        )
        sub_count = cursor.fetchone()['cnt']
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM gen_orders WHERE plan_id = %s", (plan_id,)
        )
        order_count = cursor.fetchone()['cnt']
        if sub_count > 0 or order_count > 0:
            raise HTTPException(
                status_code=400,
                detail=f'Cannot delete: this plan has {sub_count} subscription(s) and {order_count} order(s) linked to it.'
            )
        cursor.execute("DELETE FROM gen_plans WHERE plan_id = %s", (plan_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail='Plan not found')
        conn.commit()
        return {'success': True, 'message': 'Plan deleted'}
    except HTTPException:
        raise
    except Exception as e:
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=f'Server error: {str(e)}')
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)
