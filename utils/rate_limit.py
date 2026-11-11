"""In-memory sliding-window rate limiter (WP12).

PER INSTANCE: the counters live in this process only. With N application instances (or a restart) the effective limit
is N times higher (or reset). That is acceptable for the goal here (stop scripted enumeration / spam from one client
against ONE instance); a shared limiter (Redis, an API gateway, a WAF rule) is the place for a hard global limit.

Rules (name -> default `count/window_seconds`), each overridable with the env variable `RATE_LIMIT_<NAME>` written as
`<count>/<window_seconds>` (for example `RATE_LIMIT_AUTH_LOGIN=60/60`) or `0` / `off` to switch that one rule off:

  SUPPORT_CREATE       10/3600   per user    POST /support/submit
  SUGGESTION_CREATE    10/3600   per user    POST /company/suggest-feature
  VIDEO_VIEW           120/3600  per user    POST /support/videos/{id}/view
  LIVE_CHAT_SEND       30/60     per user    POST /live-chat/send, /live-chat/send-file
  ANALYTICS_EVENTS     30/60     per user    POST /analytics/events (clickstream batches)
  AUTH_CHECK           30/60     per IP      GET /auth/check-mobile/*, /auth/check-email/*  (anti-enumeration)
  AUTH_LOGIN           30/60     per IP      POST /auth/login, /auth/employee-login          (anti-enumeration)
  OTP_GENERATE         20/3600   per IP      POST /auth/generate-otp (the per-contact throttle stays in place)
  DEVICE_CHANGE_SUBMIT 10/3600   per IP      POST /requests/device-change/submit
  REFERRAL_VALIDATE    20/60     per IP      GET /referral/validate (signup screen; anti-enumeration of referral codes)
  OPERATOR_FAIL        20/60     per IP      failed operator-credential attempts (see core_functions/operator_auth.py)

Global switches (read on every call): `RATE_LIMIT_ENABLED` (default 1; 0/false/off disables every rule),
`TRUST_PROXY` (1 = the client IP is the entry of `X-Forwarded-For` that the nearest proxy appended, i.e. the LAST one, or the
`TRUSTED_PROXY_HOPS`-th from the right; leave it off unless the application sits behind
a proxy that overwrites that header, otherwise a client could pick its own IP and dodge the limit).

A request over the limit gets `429 {"detail": "Too many requests. Please try again later."}` with `Retry-After`
(seconds). The limiter never raises anything else: an internal error lets the request through.
"""
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Optional, Tuple

from fastapi import HTTPException, Request

logger = logging.getLogger("rate_limit")

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")
MAX_KEYS = 50_000          # memory bound: expired keys are dropped first, then everything when still above the bound
MAX_KEY_LENGTH = 200       # request-supplied values (user ids) are clipped before they become dictionary keys

# name -> (count, window seconds)
DEFAULT_RULES: Dict[str, Tuple[int, int]] = {
    "SUPPORT_CREATE": (10, 3600),
    "SUGGESTION_CREATE": (10, 3600),
    "VIDEO_VIEW": (120, 3600),
    "LIVE_CHAT_SEND": (30, 60),
    "ANALYTICS_EVENTS": (30, 60),
    "AUTH_CHECK": (30, 60),
    "AUTH_LOGIN": (30, 60),
    "OTP_GENERATE": (20, 3600),
    "DEVICE_CHANGE_SUBMIT": (10, 3600),
    "REFERRAL_VALIDATE": (20, 60),
    "OPERATOR_FAIL": (20, 60),
}


@dataclass(frozen=True)
class Rule:
    name: str
    count: int
    window: int


def enabled() -> bool:
    return (os.getenv("RATE_LIMIT_ENABLED") or "1").strip().lower() not in _FALSE


def trust_proxy() -> bool:
    return (os.getenv("TRUST_PROXY") or "").strip().lower() in _TRUE


def proxy_hops() -> int:
    try:
        return max(1, int((os.getenv("TRUSTED_PROXY_HOPS") or "1").strip()))
    except ValueError:
        return 1


def rule(name: str) -> Optional[Rule]:
    """The effective rule (env override applied), None when the rule is switched off or unknown."""
    key = name.upper()
    default = DEFAULT_RULES.get(key)
    raw = (os.getenv("RATE_LIMIT_" + key) or "").strip().lower()
    if raw in ("0", "off", "false", "no"):
        return None
    if raw:
        try:
            count_text, _, window_text = raw.partition("/")
            count, window = int(count_text), int(window_text)
            if count < 1 or window < 1:
                raise ValueError(raw)
            return Rule(key, count, window)
        except ValueError:
            logger.warning("RATE_LIMIT_%s=%r is not '<count>/<window_seconds>': using the default", key, raw)
    if default is None:
        return None
    return Rule(key, default[0], default[1])


class SlidingWindowLimiter:
    """`hit(bucket, key, count, window)` -> (allowed, retry_after_seconds). Thread-safe; the clock is injectable."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: Dict[Tuple[str, str], Deque[float]] = {}
        self._windows: Dict[Tuple[str, str], int] = {}

    def hit(self, bucket: str, key: str, count: int, window: int) -> Tuple[bool, int]:
        now = self._clock()
        ident = (bucket, key[:MAX_KEY_LENGTH])
        with self._lock:
            if len(self._hits) >= MAX_KEYS and ident not in self._hits:
                self._purge(now)
            stamps = self._hits.get(ident)
            if stamps is None:
                stamps = self._hits[ident] = deque()
            self._windows[ident] = window
            cutoff = now - window
            while stamps and stamps[0] <= cutoff:
                stamps.popleft()
            if len(stamps) >= count:
                retry = int(math.ceil(stamps[0] + window - now))
                return False, max(1, retry)
            stamps.append(now)
            return True, 0

    def retry_after(self, bucket: str, key: str, count: int, window: int) -> int:
        """0 while `key` is under the limit, else the seconds until it is again. Does not count a hit."""
        now = self._clock()
        with self._lock:
            stamps = self._hits.get((bucket, key[:MAX_KEY_LENGTH]))
            if not stamps:
                return 0
            live = [s for s in stamps if s > now - window]
            if len(live) < count:
                return 0
            return max(1, int(math.ceil(live[0] + window - now)))

    def _purge(self, now: float) -> None:
        for ident in [i for i, stamps in self._hits.items()
                      if not stamps or stamps[-1] <= now - self._windows.get(i, 0)]:
            self._hits.pop(ident, None)
            self._windows.pop(ident, None)
        if len(self._hits) >= MAX_KEYS:
            self._hits.clear()
            self._windows.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._windows.clear()


_limiter = SlidingWindowLimiter()


def set_clock(clock: Optional[Callable[[], float]]) -> None:
    """Tests: replace the clock (None = monotonic) and forget every counter."""
    global _limiter
    _limiter = SlidingWindowLimiter(clock or time.monotonic)


def reset() -> None:
    _limiter.reset()


# ---------------------------------------------------------------------------------------------- identifying the caller
def client_ip(request) -> str:
    """The caller's IP. `X-Forwarded-For` only when TRUST_PROXY=1, else the socket peer. A proxy APPENDS the address it
    saw to the header, so the entries to the left of it are client-controlled: the entry TRUSTED_PROXY_HOPS from the
    right (default 1 = the address the nearest proxy saw) is the one a client cannot forge."""
    if trust_proxy():
        entries = [e.strip() for e in (request.headers.get("x-forwarded-for") or "").split(",") if e.strip()]
        if entries:
            return entries[-min(proxy_hops(), len(entries))]
    client = getattr(request, "client", None)
    return (client.host if client and client.host else "unknown")


def user_key(request, hint: Optional[str] = None) -> str:
    """The user the limit counts: the token identity when there is one, else the id the request names, else the IP."""
    identity = getattr(getattr(request, "state", None), "identity", None)
    if identity is not None and getattr(identity, "user_id", None):
        return "u:" + str(identity.user_id)
    hint = (hint or "").strip()
    if hint:
        return "u:" + hint
    return "ip:" + client_ip(request)


# ---------------------------------------------------------------------------------------------- enforcement
def check(name: str, key: str) -> None:
    """Count one request of `key` against the rule; raises HTTP 429 (+ Retry-After) when it is over the limit."""
    try:
        if not enabled():
            return
        current = rule(name)
        if current is None:
            return
        allowed, retry_after = _limiter.hit(current.name, key, current.count, current.window)
    except Exception:                          # a limiter bug must never take an endpoint down
        logger.exception("rate limiter failed for rule %s: request allowed", name)
        return
    if not allowed:
        logger.warning("rate limit %s exceeded (key %s...)", current.name, key[:12])
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.",
                            headers={"Retry-After": str(retry_after)})


def record(name: str, key: str) -> None:
    """Count one event of `key` without ever raising (used to count FAILED attempts)."""
    try:
        current = rule(name)
        if enabled() and current is not None:
            _limiter.hit(current.name, key, current.count, current.window)
    except Exception:
        logger.exception("rate limiter failed for rule %s", name)


def ensure_open(name: str, key: str) -> None:
    """429 (+ Retry-After) when `key` is already at the limit of the rule; counts nothing (pair it with `record`)."""
    try:
        current = rule(name)
        if not enabled() or current is None:
            return
        retry_after = _limiter.retry_after(current.name, key, current.count, current.window)
    except Exception:
        logger.exception("rate limiter failed for rule %s: request allowed", name)
        return
    if retry_after:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.",
                            headers={"Retry-After": str(retry_after)})


def check_ip(name: str, request) -> None:
    check(name, "ip:" + client_ip(request))


def check_user(name: str, request, hint: Optional[str] = None) -> None:
    check(name, user_key(request, hint))


def by_ip(name: str):
    """FastAPI dependency factory: `dependencies=[Depends(rate_limit.by_ip("AUTH_LOGIN"))]`."""
    def dependency(request: Request) -> None:
        check_ip(name, request)
    dependency.__name__ = "rate_limit_ip_" + name.lower()
    return dependency
