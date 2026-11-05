"""Signed, expiring download links (WP12): HMAC-SHA256 over `path`, the query string and the expiry.

A link looks like `/invoice/download?user_id=u&org_id=o&invoice_number=I1&exp=1735689600&sig=<hex>`; the signature covers
the path, EVERY other query parameter (sorted, so parameter order does not matter) and `exp`, so changing the invoice
number, the user or the expiry invalidates it. The key is `AUTH_TOKEN_SECRET` (same >= 32 character rule as the access
tokens; without it nothing is signed and nothing verifies). The default lifetime is 15 minutes (`SIGNED_URL_TTL_SECONDS`,
60..86400). Signatures are compared in constant time.

Pure module: no database, no framework import.
"""
import hashlib
import hmac
import os
import time
from typing import Mapping, Optional
from urllib.parse import urlencode

from utils import auth_token

DEFAULT_TTL_SECONDS = 15 * 60
MIN_TTL_SECONDS, MAX_TTL_SECONDS = 60, 86400
EXP_PARAM, SIG_PARAM = "exp", "sig"


def ttl_seconds() -> int:
    raw = (os.getenv("SIGNED_URL_TTL_SECONDS") or "").strip()
    try:
        value = int(raw) if raw else DEFAULT_TTL_SECONDS
    except ValueError:
        return DEFAULT_TTL_SECONDS
    return value if MIN_TTL_SECONDS <= value <= MAX_TTL_SECONDS else DEFAULT_TTL_SECONDS


def enabled() -> bool:
    return auth_token.tokens_enabled()


def _canonical(path: str, params: Mapping[str, object], exp: int) -> bytes:
    pairs = sorted((str(k), "" if v is None else str(v)) for k, v in params.items()
                   if k not in (EXP_PARAM, SIG_PARAM))
    return ("%s\n%s\n%d" % (path, urlencode(pairs), exp)).encode("utf-8")


def _signature(path: str, params: Mapping[str, object], exp: int, key: str) -> str:
    return hmac.new(key.encode("utf-8"), _canonical(path, params, exp), hashlib.sha256).hexdigest()


def sign_url(path: str, params: Mapping[str, object], *, now: Optional[float] = None, ttl: Optional[int] = None,
             key: Optional[str] = None) -> Optional[str]:
    """`path?params&exp=..&sig=..`, or None when no usable AUTH_TOKEN_SECRET is configured (callers then keep the plain
    link). `params` values are strings (None becomes the empty string, as in the query string actually sent)."""
    key = key if key is not None else auth_token.secret()
    if len(key or "") < auth_token.MIN_SECRET_LENGTH:
        return None
    issued = int(now if now is not None else time.time())
    exp = issued + int(ttl if ttl is not None else ttl_seconds())
    clean = {str(k): ("" if v is None else str(v)) for k, v in params.items()}
    query = urlencode(list(clean.items()) + [(EXP_PARAM, str(exp)), (SIG_PARAM, _signature(path, clean, exp, key))])
    return "%s?%s" % (path, query)


def verify(path: str, params: Mapping[str, object], *, now: Optional[float] = None, key: Optional[str] = None) -> str:
    """"ok", "missing" (no sig/exp in the request), "expired" or "invalid". `params` = the request's query parameters."""
    sig, exp_text = params.get(SIG_PARAM), params.get(EXP_PARAM)
    if not sig and not exp_text:
        return "missing"
    key = key if key is not None else auth_token.secret()
    if len(key or "") < auth_token.MIN_SECRET_LENGTH or not sig or not exp_text:
        return "invalid"
    try:
        exp = int(str(exp_text))
    except ValueError:
        return "invalid"
    expected = _signature(path, params, exp, key)
    if not hmac.compare_digest(expected.encode("ascii"), str(sig).encode("utf-8", "replace")):
        return "invalid"
    current = now if now is not None else time.time()
    return "ok" if current <= exp else "expired"
