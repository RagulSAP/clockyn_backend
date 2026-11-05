"""The ONE guard of the platform-operator routes (WP12).

Operator routes are the vendor-side routes that no organisation user may call: `/gen-dashboard/*`,
`/auth/gen-admin-users*`, plan and coupon administration, support / suggestion administration lists, invoice bulk
download, attribution report, deletion requests (see `OPERATOR_RULES` in core_functions/auth_policy.py). An app access
token (the bearer token of `utils/auth_token.py`) NEVER satisfies this guard: the `Authorization` header is not read here.

Credentials accepted, in this order:
  1. `X-Operator-Key: <OPERATOR_API_KEY>`: a shared secret from the environment, compared in constant time. Header sent
     but `OPERATOR_API_KEY` unset (or shorter than 24 characters): 503 (the routes fail closed). Wrong key: 403.
  2. LEGACY platform-admin login: header `x-admin-id: <phone>` where the phone is a row of `gen_admin_login`. This is how
     the existing operator web dashboard authenticates today: the operator signs in with `POST /auth/admin-login` (SMS code)
     + `/auth/admin-login/verify-otp`, which answers `{phone_number, role_id}` and NO token; the web panel then sends the
     phone as `x-admin-id`. It proves nothing beyond knowing an operator's phone number, which is why the key above exists.
     Accepted unless `OPERATOR_LEGACY_ADMIN_ID=0` (key-only mode).

When the guard is ENFORCED, a request with neither credential gets 403 (503 when neither mechanism is configured).
Enforcement is on when `REQUIRE_AUTH` is on, or `OPERATOR_ENFORCE=1`; `OPERATOR_ENFORCE=0` forces it off. While it is off
(the default, so today's behaviour is unchanged) routes that were open before stay open, and only the routes that always
demanded `x-admin-id` (`/auth/gen-admin-users*` role 1; `/auth/check-mobile` and `/auth/check-email` are PUBLIC since QW-13) keep demanding a
credential, now also accepting the key.

Failed attempts are counted per client IP (rule OPERATOR_FAIL, default 20/60 s, see utils/rate_limit.py): beyond it the
answer is 429 for everybody from that IP, including a correct key, so the key cannot be guessed online.

Env (read on every call): OPERATOR_API_KEY, OPERATOR_ENFORCE, OPERATOR_LEGACY_ADMIN_ID.
"""
import hashlib
import hmac
import logging
import os
from typing import Optional

from fastapi import HTTPException, Request

from utils import auth_token, rate_limit

logger = logging.getLogger("operator_auth")

KEY_HEADER = "x-operator-key"
LEGACY_HEADER = "x-admin-id"
MIN_KEY_LENGTH = 24
SUPER_ROLE = 1
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")

KEY = "key"                # authorize() results
LEGACY = "legacy"
OPEN = "open"


def configured_key() -> str:
    """OPERATOR_API_KEY, or '' when unset / too short to be a secret."""
    value = (os.getenv("OPERATOR_API_KEY") or "").strip()
    if value and len(value) < MIN_KEY_LENGTH:
        logger.warning("OPERATOR_API_KEY is shorter than %d characters: treated as not configured", MIN_KEY_LENGTH)
        return ""
    return value


def enforced() -> bool:
    raw = (os.getenv("OPERATOR_ENFORCE") or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return auth_token.require_auth_enabled()


def legacy_enabled() -> bool:
    return (os.getenv("OPERATOR_LEGACY_ADMIN_ID") or "1").strip().lower() not in _FALSE


def keys_match(presented: str, expected: str) -> bool:
    """Constant-time comparison (digests first, so the lengths never leak)."""
    return hmac.compare_digest(hashlib.sha256((presented or "").encode("utf-8")).digest(),
                               hashlib.sha256((expected or "").encode("utf-8")).digest())


def lookup_gen_admin_role(identifier: str) -> Optional[int]:
    """role_id of the `gen_admin_login` row for this identifier (email or legacy phone). None: no such operator."""
    from psycopg2.extras import RealDictCursor
    from core_functions.db_function import get_db_connection, return_connection
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            if '@' in identifier:
                cursor.execute("SELECT role_id FROM gen_admin_login WHERE email = %s", (identifier,))
            else:
                cursor.execute("SELECT role_id FROM gen_admin_login WHERE phone_number = %s", (identifier,))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return None if row is None else row["role_id"]
    finally:
        return_connection(conn)


_lookup = lookup_gen_admin_role      # tests replace it with set_lookup()


def set_lookup(fn) -> None:
    global _lookup
    _lookup = fn or lookup_gen_admin_role


def _fail(request, status: int, detail: str) -> HTTPException:
    rate_limit.record("OPERATOR_FAIL", "ip:" + rate_limit.client_ip(request))
    logger.warning("operator access denied: %s %s from %s", request.method, request.url.path, rate_limit.client_ip(request))
    return HTTPException(status_code=status, detail=detail)


def authorize(request: Request, *, super_only: bool = False, always_check: bool = False,
              denied_detail: str = "Authentication required") -> str:
    """Raise unless the request carries an operator credential (see the module docstring). Returns "key", "legacy" or
    "open" (not enforced and the route was open before). `super_only`: the legacy login must be a role_id=1 operator
    (the key is always the full operator). `always_check`: demand a credential even while enforcement is off (routes
    that already demanded `x-admin-id` before this guard existed)."""
    rate_limit.ensure_open("OPERATOR_FAIL", "ip:" + rate_limit.client_ip(request))
    presented = request.headers.get(KEY_HEADER)
    if presented is not None:
        expected = configured_key()
        if not expected:
            logger.error("X-Operator-Key sent but OPERATOR_API_KEY is not configured: refusing (fail closed)")
            raise HTTPException(status_code=503, detail="Operator access is not configured")
        if keys_match(presented, expected):
            return KEY
        raise _fail(request, 403, denied_detail)

    must_check = always_check or enforced()
    if not must_check:
        return OPEN
    if not legacy_enabled():
        if not configured_key():
            raise HTTPException(status_code=503, detail="Operator access is not configured")
        raise _fail(request, 403, denied_detail)

    phone = (request.headers.get(LEGACY_HEADER) or "").strip()
    if not phone:
        raise _fail(request, 403, denied_detail)
    try:
        role = _lookup(phone)
    except HTTPException:
        raise
    except Exception:
        logger.exception("operator lookup failed")
        raise HTTPException(status_code=503, detail="Operator authentication temporarily unavailable")
    if role is None or (super_only and role != SUPER_ROLE):
        raise _fail(request, 403, denied_detail)
    return LEGACY


# ---------------------------------------------------------------------------------------------- FastAPI dependencies
def require_operator(request: Request) -> str:
    """Dependency for the operator routes that were open before (any operator; role 2 may read and write as before)."""
    return authorize(request)


def require_operator_login(request: Request) -> str:
    """Dependency for routes that always demanded a platform-admin login (any `gen_admin_login` row, or the key)."""
    return authorize(request, always_check=True)


def require_operator_super(request: Request) -> str:
    """Dependency for operator administration (role_id=1 operators, or the key)."""
    return authorize(request, super_only=True, always_check=True,
                     denied_detail="Only role_id=1 admins can perform this action")
