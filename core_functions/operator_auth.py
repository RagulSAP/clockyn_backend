"""The ONE guard of the platform-operator routes (WP12).

Operator routes are the vendor-side routes that no organisation user may call: `/gen-dashboard/*`,
`/auth/gen-admin-users*`, plan and coupon administration, support / suggestion administration lists, invoice bulk
download, attribution report, deletion requests (see `OPERATOR_RULES` in core_functions/auth_policy.py). An APP access
token (the bearer token of `utils/auth_token.py`) NEVER satisfies this guard: only an OPERATOR session token
(`utils/operator_token.py`, other signing key and audience) is read from the `Authorization` header here.

Credentials accepted, in this order:
  1. `X-Operator-Key: <OPERATOR_API_KEY>`: a shared secret from the environment, compared in constant time. Header sent
     but `OPERATOR_API_KEY` unset (or shorter than 24 characters): 503 (the routes fail closed). Wrong key: 403.
  2. OPERATOR SESSION: `Authorization: Bearer <operator token>`. The operator proves who they are with the e-mailed code
     (`POST /auth/admin-login` + `/auth/admin-login/verify-otp`), which answers `access_token` (HS256, signed with a key
     derived from `AUTH_TOKEN_SECRET`, claims sub = e-mail, role, iat, exp, aud; lifetime `OPERATOR_TOKEN_TTL_HOURS`, default
     12). The signature and expiry are verified, then the operator is RE-LOOKED-UP in `gen_admin_login` on EVERY request, so
     deleting an operator or changing the role takes effect at once (the role of the database is used, never the claim;
     `super_only` needs role_id 1). Expired token: 401 `Token expired`. Invalid token (bad signature, an app token, wrong
     audience) or an operator that no longer exists: 401 `Not authenticated`. A role that is not allowed: 403. A Bearer token
     that is present but invalid NEVER falls through to the legacy header below. Result "session".
  3. LEGACY platform-admin login: header `x-admin-id: <e-mail or phone>` where the value is a row of `gen_admin_login`. It
     proves nothing beyond knowing an operator's e-mail address or phone number (there is no secret in it), so it exists only
     for the roll-out of the session token. Accepted unless `OPERATOR_LEGACY_ADMIN_ID=0`.

Recommended production roll-out: deploy the backend, deploy the console (it sends the session token), then set
`OPERATOR_ENFORCE=1` and `OPERATOR_LEGACY_ADMIN_ID=0` (operator sessions and the key only). Start-up logs a SECURITY
warning while either is not set (`startup_security_warnings`).

When the guard is ENFORCED, a request with no credential gets 403 (503 when no mechanism is configured: legacy off, no key).
Enforcement is on when `REQUIRE_AUTH` is on, or `OPERATOR_ENFORCE=1`; `OPERATOR_ENFORCE=0` forces it off. While it is off
(the default, so today's behaviour is unchanged) routes that were open before stay open (a Bearer token that is not a valid
operator session is then ignored), and only the routes that always demanded a login (`/auth/gen-admin-users*` role 1;
`/auth/check-mobile` and `/auth/check-email` are PUBLIC since QW-13) keep demanding a credential.

Failed attempts (wrong key, invalid / expired session token, unknown or malformed legacy header) are counted per client IP
(rule OPERATOR_FAIL, default 20/60 s, see utils/rate_limit.py): beyond it the answer is 429 for everybody from that IP,
including a correct credential, so nothing can be guessed online.

Env (read on every call): OPERATOR_API_KEY, OPERATOR_ENFORCE, OPERATOR_LEGACY_ADMIN_ID, OPERATOR_TOKEN_TTL_HOURS (token
lifetime), AUTH_TOKEN_SECRET (signing).
"""
import hashlib
import hmac
import logging
import os
from typing import List, Optional, Tuple

from fastapi import HTTPException, Request

from utils import auth_token, operator_token, rate_limit

logger = logging.getLogger("operator_auth")

KEY_HEADER = "x-operator-key"
LEGACY_HEADER = "x-admin-id"
MIN_KEY_LENGTH = 24
SUPER_ROLE = 1
_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")

KEY = "key"                # authorize() results
SESSION = "session"
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


class SessionError(Exception):
    """An operator session token that must not be accepted. `status` 401, `detail` "Token expired" or "Not authenticated"."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def verify_session(token: str) -> Tuple[str, int]:
    """(operator e-mail, role_id FROM THE DATABASE) of a valid operator session token. The signature / expiry are verified
    and the operator is looked up in `gen_admin_login` right now. Raises SessionError (expired / invalid token, operator
    deleted or without a role); a lookup failure propagates (the callers answer 503). Blocking (database): call it in a
    thread from async code."""
    try:
        claims = operator_token.verify_operator_token(token)
    except auth_token.TokenError as exc:
        logger.debug("operator token rejected: %s", exc.reason)
        raise SessionError(401, "Token expired" if exc.code == "expired" else "Not authenticated")
    email = claims["sub"].strip().lower()
    role = _lookup(email)
    if role is None:
        logger.debug("operator token of a deleted / unknown operator")
        raise SessionError(401, "Not authenticated")
    return email, role


def _fail(request, status: int, detail: str) -> HTTPException:
    rate_limit.record("OPERATOR_FAIL", "ip:" + rate_limit.client_ip(request))
    logger.warning("operator access denied: %s %s from %s", request.method, request.url.path, rate_limit.client_ip(request))
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return HTTPException(status_code=status, detail=detail, headers=headers)


def operator_id(request) -> Optional[str]:
    """Who the operator is, for attribution (live-chat sender): the e-mail of a verified session, else the legacy header
    value while the legacy login is enabled, else None (the key alone is not a person)."""
    who = getattr(getattr(request, "state", None), "operator_id", None)
    if who:
        return who
    if legacy_enabled():
        return request.headers.get(LEGACY_HEADER)
    return None


def authorize(request: Request, *, super_only: bool = False, always_check: bool = False,
              denied_detail: str = "Authentication required") -> str:
    """Raise unless the request carries an operator credential (see the module docstring). Returns "key", "session",
    "legacy" or "open" (not enforced and the route was open before). `super_only`: a session / legacy login must be a
    role_id=1 operator (the key is always the full operator). `always_check`: demand a credential even while enforcement is
    off (routes that already demanded a platform-admin login before this guard existed)."""
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
    bearer = auth_token.bearer_from_header(request.headers.get("authorization"))
    if bearer is not None:
        # A Bearer token that is present decides the request on its own: when it is not a valid operator session the
        # answer is 401 and the legacy header is NOT consulted. While nothing is demanded (open route, not enforced) an
        # invalid token (e.g. an app token) is ignored, exactly as the Authorization header was ignored before.
        try:
            email, role = verify_session(bearer)
        except SessionError as exc:
            if not must_check:
                return OPEN
            raise _fail(request, exc.status, exc.detail)
        except HTTPException:
            raise
        except Exception:
            if not must_check:
                return OPEN
            logger.exception("operator lookup failed")
            raise HTTPException(status_code=503, detail="Operator authentication temporarily unavailable")
        if super_only and role != SUPER_ROLE:
            logger.warning("operator %s refused: role %s on a role_id=1 route", request.url.path, role)
            raise HTTPException(status_code=403, detail=denied_detail)
        request.state.operator_id = email
        return SESSION

    if not must_check:
        return OPEN
    if not legacy_enabled():
        if not configured_key() and not operator_token.tokens_enabled():
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


def startup_security_warnings() -> List[str]:
    """Human-readable SECURITY warnings about the current settings (empty list = nothing to warn about). Never raises."""
    messages: List[str] = []
    try:
        if not auth_token.require_auth_enabled():
            messages.append("SECURITY: REQUIRE_AUTH is off: the app API does not demand an access token, any caller that "
                            "knows a user id can act as that user. Set REQUIRE_AUTH=1 (needs AUTH_TOKEN_SECRET) in production.")
        if not enforced():
            messages.append("SECURITY: operator routes are not enforced (OPERATOR_ENFORCE is off): the admin-console API is "
                            "open to anyone who can reach it. Set OPERATOR_ENFORCE=1 and OPERATOR_LEGACY_ADMIN_ID=0 in production.")
        elif legacy_enabled():
            messages.append("SECURITY: the operator API still accepts the legacy x-admin-id header (OPERATOR_LEGACY_ADMIN_ID is "
                            "not 0): knowing an operator e-mail or phone number is enough. After the console is deployed, set "
                            "OPERATOR_LEGACY_ADMIN_ID=0.")
        elif not operator_token.tokens_enabled() and not configured_key():
            messages.append("SECURITY: operator routes are enforced with the legacy header off, but neither AUTH_TOKEN_SECRET "
                            "(operator sessions) nor OPERATOR_API_KEY is configured: every operator request is refused (503).")
    except Exception:
        logger.exception("security configuration check failed")
    return messages


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
