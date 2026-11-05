"""Bearer access tokens (HS256 compact tokens, standard library only) and the authentication settings.

Token layout (JWT compact form, so any JWT tool can read it): `base64url(header).base64url(claims).base64url(signature)`
  header  {"alg": "HS256", "typ": "JWT"}
  claims  sub (user_id), org (organisation id: users.org_id for admins, users.parent_id for managers/staff),
          role (1 admin, 2 manager, 3 staff), iat, exp (epoch seconds), ep (auth epoch of the user, see auth_dependency).

Environment (all read on every call, so tests and operators can change them without a restart):
  AUTH_TOKEN_SECRET        signing key, at least 32 characters. Unset/short: no token is issued and none is accepted.
  AUTH_TOKEN_TTL_DAYS      token lifetime in days, default 90 (same as the device-token lifetime).
  REQUIRE_AUTH             1/true/yes = every non-public route needs a valid token (default off: behaviour as before).

Pure functions: no database and no framework import here.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger("auth.token")

ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32
CLOCK_SKEW_SECONDS = 60
DEFAULT_TTL_DAYS = 90
MAX_TTL_DAYS = 3650
MAX_TOKEN_LENGTH = 2048          # a real token is ~200 characters; anything larger is not parsed at all
_TRUE = ("1", "true", "yes", "on")


class TokenError(Exception):
    """A token that must not be accepted. `code` is "expired" or "invalid" (the reason is for logs, never for clients)."""

    def __init__(self, code: str, reason: str = ""):
        super().__init__(reason or code)
        self.code = code
        self.reason = reason or code


class AuthConfigError(RuntimeError):
    """REQUIRE_AUTH is on but the token settings cannot work: the application refuses to start."""


# --------------------------------------------------------------------------------------------- settings
def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def secret() -> str:
    """The signing key, or '' when it is missing or too short to be safe."""
    value = _clean(os.getenv("AUTH_TOKEN_SECRET"))
    return value if len(value) >= MIN_SECRET_LENGTH else ""


def tokens_enabled() -> bool:
    """True when a usable AUTH_TOKEN_SECRET is configured (tokens are issued and verified only then)."""
    return bool(secret())


def require_auth_enabled() -> bool:
    return _clean(os.getenv("REQUIRE_AUTH")).lower() in _TRUE


def ttl_days() -> int:
    raw = _clean(os.getenv("AUTH_TOKEN_TTL_DAYS"))
    if not raw:
        return DEFAULT_TTL_DAYS
    try:
        days = int(raw)
    except ValueError:
        logger.warning("AUTH_TOKEN_TTL_DAYS=%r is not an integer: using %d", raw, DEFAULT_TTL_DAYS)
        return DEFAULT_TTL_DAYS
    if days < 1 or days > MAX_TTL_DAYS:
        logger.warning("AUTH_TOKEN_TTL_DAYS=%d is outside 1..%d: using %d", days, MAX_TTL_DAYS, DEFAULT_TTL_DAYS)
        return DEFAULT_TTL_DAYS
    return days


def validate_auth_config() -> dict:
    """Start-up check (called from app.py). Raises AuthConfigError when REQUIRE_AUTH is on but AUTH_TOKEN_SECRET is
    missing or shorter than 32 characters; otherwise returns a small summary that is also logged (never the secret)."""
    required = require_auth_enabled()
    raw = _clean(os.getenv("AUTH_TOKEN_SECRET"))
    if required and not secret():
        problem = "is not set" if not raw else "is shorter than %d characters" % MIN_SECRET_LENGTH
        raise AuthConfigError(
            "REQUIRE_AUTH is enabled but AUTH_TOKEN_SECRET %s. Set AUTH_TOKEN_SECRET to a random string of at least "
            "%d characters (e.g. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) or turn REQUIRE_AUTH off."
            % (problem, MIN_SECRET_LENGTH)
        )
    if raw and not secret():
        logger.warning("AUTH_TOKEN_SECRET is set but shorter than %d characters: tokens are NOT issued", MIN_SECRET_LENGTH)
    summary = {"tokens_enabled": tokens_enabled(), "require_auth": required, "ttl_days": ttl_days()}
    logger.info("auth: tokens_enabled=%s require_auth=%s ttl_days=%s",
                summary["tokens_enabled"], summary["require_auth"], summary["ttl_days"])
    if not required and summary["tokens_enabled"]:
        logger.info("auth: REQUIRE_AUTH is off - requests without a token are still served")
    return summary


# --------------------------------------------------------------------------------------------- encoding
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    if not text or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in text):
        raise TokenError("invalid", "bad base64url segment")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError:                      # binascii.Error (a length of 4n+1 is impossible) is a ValueError
        raise TokenError("invalid", "bad base64url length")


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("invalid", "segment is not JSON")


def _sign(signing_input: bytes, key: str) -> bytes:
    return hmac.new(key.encode("utf-8"), signing_input, hashlib.sha256).digest()


# --------------------------------------------------------------------------------------------- issue / verify
def issue_token(user_id: str, org_id: Optional[str], role_id: int, epoch: int = 0, *,
                now: Optional[float] = None, key: Optional[str] = None,
                ttl_seconds: Optional[int] = None) -> Tuple[str, int]:
    """(token, expires_in seconds). Raises TokenError("invalid") when there is no usable secret."""
    key = key if key is not None else secret()
    if len(key or "") < MIN_SECRET_LENGTH:
        raise TokenError("invalid", "no usable AUTH_TOKEN_SECRET")
    ttl = int(ttl_seconds) if ttl_seconds is not None else ttl_days() * 86400
    issued = int(now if now is not None else time.time())
    header = {"alg": ALGORITHM, "typ": "JWT"}
    claims = {"sub": str(user_id), "org": str(org_id) if org_id else "", "role": int(role_id),
              "iat": issued, "exp": issued + ttl, "ep": int(epoch or 0)}
    head = _b64e(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    body = _b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{_b64e(_sign(signing_input, key))}", ttl


def verify_token(token: str, *, now: Optional[float] = None, key: Optional[str] = None) -> dict:
    """The claims of a valid token. Raises TokenError: code "expired" (signature fine, past exp + 60 s) or "invalid"
    (anything else: shape, algorithm, signature, claim types, issued in the future)."""
    key = key if key is not None else secret()
    if len(key or "") < MIN_SECRET_LENGTH:
        raise TokenError("invalid", "no usable AUTH_TOKEN_SECRET")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise TokenError("invalid", "empty or oversized token")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("invalid", "not three segments")
    head_text, body_text, sig_text = parts
    header = _json(_b64d(head_text))
    # Only HS256 is accepted: "none", RS256 (key confusion) and anything else is refused BEFORE the signature is looked at.
    if not isinstance(header, dict) or header.get("alg") != ALGORITHM:
        raise TokenError("invalid", "unsupported algorithm")
    expected = _sign(f"{head_text}.{body_text}".encode("ascii"), key)
    if not hmac.compare_digest(expected, _b64d(sig_text)):
        raise TokenError("invalid", "bad signature")
    claims = _json(_b64d(body_text))
    if not isinstance(claims, dict):
        raise TokenError("invalid", "claims are not an object")
    sub, org, role = claims.get("sub"), claims.get("org", ""), claims.get("role")
    iat, exp, ep = claims.get("iat"), claims.get("exp"), claims.get("ep", 0)
    if not isinstance(sub, str) or not sub or not isinstance(org, str):
        raise TokenError("invalid", "bad sub/org")
    for name, value in (("role", role), ("iat", iat), ("exp", exp), ("ep", ep)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TokenError("invalid", f"bad {name}")
    current = now if now is not None else time.time()
    if iat > current + CLOCK_SKEW_SECONDS:
        raise TokenError("invalid", "issued in the future")
    if current > exp + CLOCK_SKEW_SECONDS:
        raise TokenError("expired", "expired")
    return {"sub": sub, "org": org, "role": role, "iat": iat, "exp": exp, "ep": ep}


def bearer_from_header(value: Optional[str]) -> Optional[str]:
    """The token of an `Authorization: Bearer <token>` header value, else None."""
    if not value:
        return None
    scheme, _, rest = value.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = rest.strip()
    return token or None
