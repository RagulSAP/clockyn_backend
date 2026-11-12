"""Operator session tokens: short-lived Bearer tokens of the platform-operator console (WP12, finding S-02).

Same compact layout as the app access token of `utils/auth_token.py` (HS256, `head.claims.signature`, so any JWT tool can
read it) but it can never be confused with it, in either direction:
  * the signing key is DERIVED from AUTH_TOKEN_SECRET (HMAC-SHA256 of the secret with a fixed label), so an app token has a
    signature that does not verify here and an operator token has one that does not verify as an app token;
  * the claims carry `aud = "clockyn-operator"` and `typ = "operator"`, which are checked on verification (an app token has
    neither), and an app token's `ep`/`org` claims are absent.
  claims  sub (operator e-mail, lower-case), role (gen_admin_login.role_id at login time, informational: the guard re-reads
          the role from the database on every request), iat, exp (epoch seconds), aud, typ.

Environment (read on every call):
  AUTH_TOKEN_SECRET          the same secret as the app tokens (>= 32 characters); unset/short: no operator token is issued
                             and none is accepted.
  OPERATOR_TOKEN_TTL_HOURS   lifetime in hours, default 12, allowed 1..72 (anything else falls back to the default).

Pure functions: no database and no framework import here. The guard that uses them is core_functions/operator_auth.py.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional, Tuple

from utils import auth_token
from utils.auth_token import ALGORITHM, CLOCK_SKEW_SECONDS, MAX_TOKEN_LENGTH, MIN_SECRET_LENGTH, TokenError

logger = logging.getLogger("auth.operator_token")

AUDIENCE = "clockyn-operator"
TOKEN_TYPE = "operator"
KEY_LABEL = b"clockyn-operator-session-v1"
DEFAULT_TTL_HOURS = 12
MIN_TTL_HOURS = 1
MAX_TTL_HOURS = 72


def signing_key(secret: Optional[str] = None) -> str:
    """The operator signing key derived from AUTH_TOKEN_SECRET ('' when there is no usable secret)."""
    base = secret if secret is not None else auth_token.secret()
    if len(base or "") < MIN_SECRET_LENGTH:
        return ""
    return hmac.new(base.encode("utf-8"), KEY_LABEL, hashlib.sha256).hexdigest()


def tokens_enabled() -> bool:
    return bool(signing_key())


def ttl_hours() -> int:
    raw = (os.getenv("OPERATOR_TOKEN_TTL_HOURS") or "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        hours = int(raw)
    except ValueError:
        logger.warning("OPERATOR_TOKEN_TTL_HOURS=%r is not an integer: using %d", raw, DEFAULT_TTL_HOURS)
        return DEFAULT_TTL_HOURS
    if hours < MIN_TTL_HOURS or hours > MAX_TTL_HOURS:
        logger.warning("OPERATOR_TOKEN_TTL_HOURS=%d is outside %d..%d: using %d",
                       hours, MIN_TTL_HOURS, MAX_TTL_HOURS, DEFAULT_TTL_HOURS)
        return DEFAULT_TTL_HOURS
    return hours


def issue_operator_token(email: str, role_id: int, *, now: Optional[float] = None, key: Optional[str] = None,
                         ttl_seconds: Optional[int] = None) -> Tuple[str, int]:
    """(token, expires_in seconds). `key` is the raw AUTH_TOKEN_SECRET (tests); the signing key is derived from it.
    Raises TokenError("invalid") when there is no usable secret."""
    sign_key = signing_key(key)
    if not sign_key:
        raise TokenError("invalid", "no usable AUTH_TOKEN_SECRET")
    subject = (email or "").strip().lower()
    if not subject:
        raise TokenError("invalid", "empty operator")
    ttl = int(ttl_seconds) if ttl_seconds is not None else ttl_hours() * 3600
    issued = int(now if now is not None else time.time())
    header = {"alg": ALGORITHM, "typ": "JWT"}
    claims = {"sub": subject, "role": int(role_id), "iat": issued, "exp": issued + ttl,
              "aud": AUDIENCE, "typ": TOKEN_TYPE}
    head = auth_token._b64e(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    body = auth_token._b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{auth_token._b64e(auth_token._sign(signing_input, sign_key))}", ttl


def verify_operator_token(token: str, *, now: Optional[float] = None, key: Optional[str] = None) -> dict:
    """The claims of a valid operator token. Raises TokenError: code "expired" (signature fine, past exp + 60 s) or
    "invalid" (anything else: shape, algorithm, signature, audience / type, claim types, issued in the future)."""
    sign_key = signing_key(key)
    if not sign_key:
        raise TokenError("invalid", "no usable AUTH_TOKEN_SECRET")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise TokenError("invalid", "empty or oversized token")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("invalid", "not three segments")
    head_text, body_text, sig_text = parts
    header = auth_token._json(auth_token._b64d(head_text))
    if not isinstance(header, dict) or header.get("alg") != ALGORITHM:      # "none" / RS256 refused before the signature
        raise TokenError("invalid", "unsupported algorithm")
    expected = auth_token._sign(f"{head_text}.{body_text}".encode("ascii"), sign_key)
    if not hmac.compare_digest(expected, auth_token._b64d(sig_text)):
        raise TokenError("invalid", "bad signature")
    claims = auth_token._json(auth_token._b64d(body_text))
    if not isinstance(claims, dict):
        raise TokenError("invalid", "claims are not an object")
    if claims.get("aud") != AUDIENCE or claims.get("typ") != TOKEN_TYPE:
        raise TokenError("invalid", "not an operator token")
    sub, role, iat, exp = claims.get("sub"), claims.get("role"), claims.get("iat"), claims.get("exp")
    if not isinstance(sub, str) or not sub:
        raise TokenError("invalid", "bad sub")
    for name, value in (("role", role), ("iat", iat), ("exp", exp)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TokenError("invalid", f"bad {name}")
    current = now if now is not None else time.time()
    if iat > current + CLOCK_SKEW_SECONDS:
        raise TokenError("invalid", "issued in the future")
    if current > exp + CLOCK_SKEW_SECONDS:
        raise TokenError("expired", "expired")
    return {"sub": sub, "role": role, "iat": iat, "exp": exp}
