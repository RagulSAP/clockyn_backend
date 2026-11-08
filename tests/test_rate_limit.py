"""Sliding-window rate limiter (WP12 K2): utils/rate_limit.py with a fake clock, and its use on the guarded routes.
Run: python -m pytest tests/test_rate_limit.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import rate_limit as RL  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    for name in ("RATE_LIMIT_ENABLED", "TRUST_PROXY") + tuple("RATE_LIMIT_" + n for n in RL.DEFAULT_RULES):
        monkeypatch.delenv(name, raising=False)
    c = Clock()
    RL.set_clock(c)
    yield c
    RL.set_clock(None)


# ---------------------------------------------------------------------------------------------------- the window
def test_sliding_window_allows_count_then_blocks_then_recovers(clock):
    lim = RL.SlidingWindowLimiter(clock)
    for i in range(3):
        assert lim.hit("b", "k", 3, 60) == (True, 0)
        clock.now += 10                                       # hits at t=1000, 1010, 1020
    allowed, retry = lim.hit("b", "k", 3, 60)                 # t=1030: oldest (1000) still inside the window
    assert allowed is False and retry == 30
    clock.now = 1059.9
    assert lim.hit("b", "k", 3, 60)[0] is False
    clock.now = 1060.1                                        # the first hit left the window: exactly one slot is free
    assert lim.hit("b", "k", 3, 60) == (True, 0)
    assert lim.hit("b", "k", 3, 60)[0] is False


def test_a_blocked_attempt_does_not_extend_the_block(clock):
    lim = RL.SlidingWindowLimiter(clock)
    assert lim.hit("b", "k", 1, 60)[0]
    for _ in range(5):
        clock.now += 5
        assert lim.hit("b", "k", 1, 60)[0] is False
    clock.now = 1060.1
    assert lim.hit("b", "k", 1, 60)[0] is True


def test_keys_and_buckets_are_independent(clock):
    lim = RL.SlidingWindowLimiter(clock)
    assert lim.hit("a", "k1", 1, 60)[0] and lim.hit("a", "k2", 1, 60)[0] and lim.hit("b", "k1", 1, 60)[0]
    assert lim.hit("a", "k1", 1, 60)[0] is False


def test_retry_after_peek_does_not_count(clock):
    lim = RL.SlidingWindowLimiter(clock)
    assert lim.retry_after("b", "k", 2, 60) == 0
    lim.hit("b", "k", 2, 60)
    assert lim.retry_after("b", "k", 2, 60) == 0
    lim.hit("b", "k", 2, 60)
    assert lim.retry_after("b", "k", 2, 60) == 60
    assert lim.retry_after("b", "k", 2, 60) == 60             # still the same: peeking changed nothing


def test_memory_is_bounded(clock, monkeypatch):
    monkeypatch.setattr(RL, "MAX_KEYS", 50)
    lim = RL.SlidingWindowLimiter(clock)
    for i in range(50):
        lim.hit("b", "k%d" % i, 1, 10)
    clock.now += 20                                           # all expired: purged when the table is full
    lim.hit("b", "fresh", 1, 10)
    assert len(lim._hits) == 1
    for i in range(200):
        lim.hit("b", "live%d" % i, 1, 1000)                   # nothing expires: the table is reset instead of growing
    assert len(lim._hits) <= 51


def test_very_long_keys_are_clipped(clock):
    lim = RL.SlidingWindowLimiter(clock)
    lim.hit("b", "x" * 5000, 1, 60)
    assert max(len(k[1]) for k in lim._hits) == RL.MAX_KEY_LENGTH


# ---------------------------------------------------------------------------------------------------- rules and env
def test_default_rules_match_the_documentation():
    assert {n: (r[0], r[1]) for n, r in RL.DEFAULT_RULES.items()} == {
        "SUPPORT_CREATE": (10, 3600), "SUGGESTION_CREATE": (10, 3600), "VIDEO_VIEW": (120, 3600), "LIVE_CHAT_SEND": (30, 60),
        "AUTH_CHECK": (30, 60), "AUTH_LOGIN": (30, 60), "OTP_GENERATE": (20, 3600), "DEVICE_CHANGE_SUBMIT": (10, 3600), "OPERATOR_FAIL": (20, 60)}


def test_env_override_off_and_garbage(clock, monkeypatch):
    assert RL.rule("auth_login") == RL.Rule("AUTH_LOGIN", 30, 60)
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", "5/10")
    assert RL.rule("AUTH_LOGIN") == RL.Rule("AUTH_LOGIN", 5, 10)
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", "off")
    assert RL.rule("AUTH_LOGIN") is None
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", "0")
    assert RL.rule("AUTH_LOGIN") is None
    for bad in ("banana", "5", "5/0", "-1/60", "5/x", "0/60"):
        monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", bad)
        assert RL.rule("AUTH_LOGIN") == RL.Rule("AUTH_LOGIN", 30, 60), bad
    assert RL.rule("NO_SUCH_RULE") is None


def test_the_global_switch(clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", "1/60")
    RL.check("AUTH_LOGIN", "ip:1")
    with pytest.raises(HTTPException):
        RL.check("AUTH_LOGIN", "ip:1")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    for _ in range(5):
        RL.check("AUTH_LOGIN", "ip:1")                        # switched off: never raises
    for off in ("false", "off", "no"):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", off)
        assert RL.enabled() is False
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    assert RL.enabled() is True


def test_429_carries_retry_after(clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_CHECK", "2/60")
    RL.check("AUTH_CHECK", "ip:9")
    clock.now += 10
    RL.check("AUTH_CHECK", "ip:9")
    with pytest.raises(HTTPException) as e:
        RL.check("AUTH_CHECK", "ip:9")
    assert e.value.status_code == 429 and e.value.headers["Retry-After"] == "50"
    assert e.value.detail == "Too many requests. Please try again later."


def test_a_limiter_failure_lets_the_request_through(clock, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(RL._limiter, "hit", broken)
    RL.check("AUTH_LOGIN", "ip:1")                           # no exception
    RL.record("AUTH_LOGIN", "ip:1")


# ---------------------------------------------------------------------------------------------------- who is the client
class Req:
    def __init__(self, host="1.2.3.4", xff=None, identity=None):
        self.client = type("C", (), {"host": host})() if host else None
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.state = type("S", (), {"identity": identity})()


def test_client_ip_ignores_x_forwarded_for_unless_the_proxy_is_trusted(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    assert RL.client_ip(Req(xff="9.9.9.9, 8.8.8.8")) == "1.2.3.4"
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert RL.client_ip(Req(xff="9.9.9.9, 8.8.8.8")) == "8.8.8.8"          # the proxy-appended entry, not the forgeable first
    assert RL.client_ip(Req(xff="6.6.6.6, 5.5.5.5, 8.8.8.8")) == "8.8.8.8"  # a client-chosen prefix buys nothing
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert RL.client_ip(Req(xff="6.6.6.6, 5.5.5.5, 8.8.8.8")) == "5.5.5.5"
    monkeypatch.delenv("TRUSTED_PROXY_HOPS")
    assert RL.client_ip(Req(xff="  7.7.7.7  ")) == "7.7.7.7"
    assert RL.client_ip(Req(xff="")) == "1.2.3.4"                          # empty header: the peer
    assert RL.client_ip(Req(host=None)) == "unknown"


def test_user_key_prefers_the_token_identity_then_the_named_user_then_the_ip():
    ident = type("I", (), {"user_id": "u-token"})()
    assert RL.user_key(Req(identity=ident), "u-claimed") == "u:u-token"
    assert RL.user_key(Req(), "  u-claimed ") == "u:u-claimed"
    assert RL.user_key(Req(), "") == "ip:1.2.3.4"
    assert RL.user_key(Req(), None) == "ip:1.2.3.4"


# ---------------------------------------------------------------------------------------------------- on the routes
def test_ip_dependency_limits_per_ip_and_trusts_the_proxy_header_only_when_told(clock, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_LOGIN", "3/60")
    app = FastAPI()

    @app.post("/auth/login", dependencies=[Depends(RL.by_ip("AUTH_LOGIN"))])
    def login():
        return {"ok": True}
    c = TestClient(app)
    for _ in range(3):
        assert c.post("/auth/login").status_code == 200
    r = c.post("/auth/login")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 60
    # a spoofed X-Forwarded-For does NOT buy a fresh allowance while TRUST_PROXY is off
    assert c.post("/auth/login", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 429
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert c.post("/auth/login", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 200
    clock.now += 61
    monkeypatch.delenv("TRUST_PROXY")
    assert c.post("/auth/login").status_code == 200


@pytest.mark.parametrize("module, method, path, rule", [
    ("router.auth", "post", "/auth/login", "AUTH_LOGIN"),
    ("router.auth", "post", "/auth/employee-login", "AUTH_LOGIN"),
    ("router.auth", "post", "/auth/generate-otp", "OTP_GENERATE"),
    ("router.auth", "get", "/auth/check-mobile/9876543210", "AUTH_CHECK"),
    ("router.auth", "get", "/auth/check-email/a@b.co", "AUTH_CHECK"),
    ("router.auth", "post", "/auth/admin-login", "OTP_GENERATE"),
])
def test_the_real_auth_routes_are_limited(clock, monkeypatch, module, method, path, rule):
    """The real routers: the limit trips (429) BEFORE the handler runs, so no database or SMS is touched."""
    import importlib
    mod = importlib.import_module(module)
    monkeypatch.setenv("RATE_LIMIT_" + rule, "1/60")
    for name in ("generate_otp", "admin_login", "employee_login"):
        monkeypatch.setattr(mod, name, _stub, raising=False)
    monkeypatch.setattr(mod, "get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")), raising=False)
    app = FastAPI()
    app.include_router(mod.router)
    c = TestClient(app, raise_server_exceptions=False)
    first = getattr(c, method)(path, **({"json": {"login_id": "x", "contact": "x", "phone": "1", "purpose": "login", "role_id": 1}}
                                        if method == "post" else {}))
    assert first.status_code != 429
    second = getattr(c, method)(path, **({"json": {"login_id": "x", "contact": "x", "phone": "1", "purpose": "login", "role_id": 1}}
                                         if method == "post" else {}))
    assert second.status_code == 429 and "Retry-After" in second.headers


async def _stub(*a, **k):
    return {"success": True}


def test_support_tickets_are_limited_per_user_not_per_ip(clock, monkeypatch):
    import router.support as sp

    async def submit(*a):
        return {"id": 1}
    monkeypatch.setattr(sp, "submit_support_request", submit)
    monkeypatch.setenv("RATE_LIMIT_SUPPORT_CREATE", "2/3600")
    app = FastAPI()
    app.include_router(sp.router)
    c = TestClient(app)

    def post(user):
        return c.post("/support/submit", data={"user_id": user, "org_id": "o", "subject": "s", "message": "m"})
    assert post("u1").status_code == 200 and post("u1").status_code == 200
    assert post("u1").status_code == 429
    assert post("u2").status_code == 200                      # another user from the same IP is not affected
    clock.now += 3601
    assert post("u1").status_code == 200


def test_suggestions_are_limited(clock, monkeypatch):
    import router.company as co
    monkeypatch.setattr(co, "submit_suggest_feature", lambda **k: {"success": True})
    monkeypatch.setenv("RATE_LIMIT_SUGGESTION_CREATE", "2/3600")
    app = FastAPI()
    app.include_router(co.router)
    c = TestClient(app)
    body = {"org_id": "o1", "subject": "s", "content": "c"}
    assert c.post("/company/suggest-feature", json=body).status_code == 200
    assert c.post("/company/suggest-feature", json=body).status_code == 200
    assert c.post("/company/suggest-feature", json=body).status_code == 429
    assert c.post("/company/suggest-feature", json={**body, "org_id": "o2"}).status_code == 200


def test_device_change_submit_is_limited_per_ip():
    src = (ROOT / "router" / "requests.py").read_text(encoding="utf-8-sig")
    assert 'rate_limit.by_ip("DEVICE_CHANGE_SUBMIT")' in src


def test_a_token_identity_beats_the_claimed_user(clock, monkeypatch):
    """With a valid token the limit counts the TOKEN's user: sending another user_id in the form does not dodge it."""
    from types import SimpleNamespace
    ident = SimpleNamespace(user_id="real", role_id=3, org_id="o")
    app = FastAPI()
    monkeypatch.setenv("RATE_LIMIT_SUPPORT_CREATE", "1/60")

    @app.middleware("http")
    async def attach(request: Request, call_next):
        request.state.identity = ident
        return await call_next(request)

    @app.post("/t")
    def t(request: Request, user_id: str = ""):
        RL.check_user("SUPPORT_CREATE", request, user_id)
        return {"ok": True}
    c = TestClient(app)
    assert c.post("/t?user_id=a").status_code == 200
    assert c.post("/t?user_id=b").status_code == 429
