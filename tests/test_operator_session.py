"""Operator session token (finding S-02): utils/operator_token.py, its use in core_functions/operator_auth.py, the
/ws/support-ops handshake and the hardened operator OTP flow of router/auth.py. DB-free: the `gen_admin_login` lookup is
replaced through `operator_auth.set_lookup`, the OTP table by a small stateful fake cursor.
Run: python -m pytest tests/test_operator_session.py -q
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from core_functions import operator_auth as oa  # noqa: E402
from utils import auth_token, operator_token as ot, rate_limit  # noqa: E402

SECRET = KEY                       # 48 characters
OPERATORS = {"boss@clockyn.com": 1, "agent@clockyn.com": 2}
LEGACY = {"111": 1, "222": 2}


def bearer(token):
    return {"Authorization": "Bearer " + token}


def session(email="boss@clockyn.com", role=1, **kw):
    return bearer(ot.issue_operator_token(email, role, key=SECRET, **kw)[0])


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("OPERATOR_API_KEY", "OPERATOR_ENFORCE", "OPERATOR_LEGACY_ADMIN_ID", "OPERATOR_TOKEN_TTL_HOURS", "REQUIRE_AUTH",
                 "RATE_LIMIT_ENABLED", "RATE_LIMIT_OPERATOR_FAIL", "TRUST_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    rate_limit.set_clock(None)
    rate_limit.reset()
    state = dict(OPERATORS, **LEGACY)
    oa.set_lookup(state.get)
    yield state
    oa.set_lookup(None)
    rate_limit.set_clock(None)
    rate_limit.reset()
    ad.reset_state()


def operator_app():
    app = FastAPI()
    r = APIRouter()

    @r.get("/open", dependencies=[Depends(oa.require_operator)])
    def open_route(request: Request):
        return {"ok": True, "who": getattr(request.state, "operator_id", None)}

    @r.get("/login", dependencies=[Depends(oa.require_operator_login)])
    def login_route():
        return {"ok": True}

    @r.get("/super", dependencies=[Depends(oa.require_operator_super)])
    def super_route():
        return {"ok": True}
    app.include_router(r)
    return TestClient(app)


# ----------------------------------------------------------------------------------------------------- the token itself
def test_round_trip_and_claims():
    token, expires_in = ot.issue_operator_token("Boss@Clockyn.com", 1, now=1_000_000, key=SECRET)
    assert expires_in == 12 * 3600
    claims = ot.verify_operator_token(token, now=1_000_100, key=SECRET)
    assert claims == {"sub": "boss@clockyn.com", "role": 1, "iat": 1_000_000, "exp": 1_000_000 + 12 * 3600}
    assert token.count(".") == 2 and len(token) < 400


def test_ttl_hours_default_range_and_invalid(monkeypatch):
    assert ot.ttl_hours() == 12
    for raw, expected in (("1", 1), ("72", 72), ("24", 24), ("0", 12), ("73", 12), ("-3", 12), ("abc", 12), ("", 12), (" 5 ", 5)):
        monkeypatch.setenv("OPERATOR_TOKEN_TTL_HOURS", raw)
        assert ot.ttl_hours() == expected, raw
    monkeypatch.setenv("OPERATOR_TOKEN_TTL_HOURS", "2")
    assert ot.issue_operator_token("a@b.co", 1, key=SECRET)[1] == 7200


def test_expiry_with_sixty_seconds_of_clock_skew():
    token, _ = ot.issue_operator_token("a@b.co", 1, now=1_000_000, key=SECRET, ttl_seconds=100)
    assert ot.verify_operator_token(token, now=1_000_100 + 60, key=SECRET)["sub"] == "a@b.co"
    with pytest.raises(auth_token.TokenError) as e:
        ot.verify_operator_token(token, now=1_000_100 + 61, key=SECRET)
    assert e.value.code == "expired"
    with pytest.raises(auth_token.TokenError) as e:                          # issued in the future beyond the skew
        ot.verify_operator_token(token, now=1_000_000 - 61, key=SECRET)
    assert e.value.code == "invalid"


def test_tampering_is_refused():
    token, _ = ot.issue_operator_token("a@b.co", 2, now=int(time.time()), key=SECRET)
    head, body, sig = token.split(".")
    forged_body = auth_token._b64e(b'{"sub":"boss@clockyn.com"}')
    for bad in (f"{head}.{forged_body}.{sig}", f"{head}.{body}.{sig[:-2]}AA", f"{head}.{body}", f"{head}.{body}.{sig}.x",
                "", "a.b.c", token + "A", "x" * 5000):
        with pytest.raises(auth_token.TokenError) as e:
            ot.verify_operator_token(bad, key=SECRET)
        assert e.value.code == "invalid", bad[:30]
    # a role flipped inside an otherwise untouched token
    import base64
    import json
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["role"] = 1
    flipped = auth_token._b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    with pytest.raises(auth_token.TokenError):
        ot.verify_operator_token(f"{head}.{flipped}.{sig}", key=SECRET)


def test_alg_none_and_other_secrets_are_refused():
    import json
    head = auth_token._b64e(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = auth_token._b64e(json.dumps({"sub": "a@b.co", "role": 1, "iat": 1, "exp": 9999999999,
                                        "aud": ot.AUDIENCE, "typ": ot.TOKEN_TYPE}).encode())
    with pytest.raises(auth_token.TokenError):
        ot.verify_operator_token(f"{head}.{body}.", key=SECRET)
    token, _ = ot.issue_operator_token("a@b.co", 1, key="z" * 48)
    with pytest.raises(auth_token.TokenError):
        ot.verify_operator_token(token, key=SECRET)


def test_wrong_audience_or_type_is_refused_even_with_the_right_signature():
    """Signed with the real operator key but not claiming to be an operator token: refused."""
    import json
    key = ot.signing_key(SECRET)
    now = int(time.time())

    def forge(**override):
        claims = {"sub": "boss@clockyn.com", "role": 1, "iat": now, "exp": now + 600, "aud": ot.AUDIENCE, "typ": ot.TOKEN_TYPE}
        claims.update(override)
        head = auth_token._b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, sort_keys=True).encode())
        body = auth_token._b64e(json.dumps(claims, sort_keys=True).encode())
        return f"{head}.{body}.{auth_token._b64e(auth_token._sign(f'{head}.{body}'.encode(), key))}"

    assert ot.verify_operator_token(forge(), key=SECRET)["sub"] == "boss@clockyn.com"            # the forge helper is right
    for override in ({"aud": "clockyn-app"}, {"aud": None}, {"typ": "access"}, {"typ": None}):
        with pytest.raises(auth_token.TokenError):
            ot.verify_operator_token(forge(**override), key=SECRET)
    for override in ({"role": "1"}, {"role": True}, {"sub": ""}, {"exp": "x"}):
        with pytest.raises(auth_token.TokenError):
            ot.verify_operator_token(forge(**override), key=SECRET)


def test_app_and_operator_tokens_never_verify_as_each_other():
    app_token = auth_token.issue_token("u1", "org", 1, key=SECRET)[0]
    op_token = ot.issue_operator_token("boss@clockyn.com", 1, key=SECRET)[0]
    with pytest.raises(auth_token.TokenError):
        ot.verify_operator_token(app_token, key=SECRET)
    with pytest.raises(auth_token.TokenError):
        auth_token.verify_token(op_token, key=SECRET)
    assert ot.signing_key(SECRET) != SECRET and len(ot.signing_key(SECRET)) == 64


def test_no_usable_secret_issues_and_accepts_nothing(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "short")
    assert ot.tokens_enabled() is False
    with pytest.raises(auth_token.TokenError):
        ot.issue_operator_token("a@b.co", 1)
    with pytest.raises(auth_token.TokenError):
        ot.verify_operator_token("a.b.c")


# ----------------------------------------------------------------------------------------------------- the guard
def test_session_token_is_accepted_and_returns_session():
    c = operator_app()
    r = c.get("/open", headers=session())
    assert r.status_code == 200 and r.json()["who"] == "boss@clockyn.com"
    assert oa.authorize.__doc__ and oa.SESSION == "session"
    for path in ("/login", "/super"):
        assert c.get(path, headers=session()).status_code == 200


def test_session_works_when_enforced_and_legacy_is_off(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    c = operator_app()
    assert c.get("/open", headers=session()).status_code == 200
    assert c.get("/open", headers={"x-admin-id": "boss@clockyn.com"}).status_code == 403     # legacy refused, no key set up
    assert c.get("/open").status_code == 403


def test_authorize_returns_session(monkeypatch):
    seen = []
    app = FastAPI()

    @app.get("/x")
    def x(request: Request):
        seen.append(oa.authorize(request))
        return {}
    TestClient(app).get("/x", headers=session())
    assert seen == ["session"]


def test_expired_token_is_401_token_expired():
    c = operator_app()
    old = ot.issue_operator_token("boss@clockyn.com", 1, now=time.time() - 7200, key=SECRET, ttl_seconds=60)[0]
    r = c.get("/login", headers=bearer(old))
    assert r.status_code == 401 and r.json() == {"detail": "Token expired"} and r.headers["www-authenticate"] == "Bearer"


def test_invalid_token_is_401_not_authenticated(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    c = operator_app()
    good = ot.issue_operator_token("boss@clockyn.com", 1, key=SECRET)[0]
    for token in (good[:-3] + "xyz", "garbage", good + "." + "a", "x" * 3000):
        r = c.get("/open", headers=bearer(token))
        assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}, token[:20]


def test_an_app_token_is_rejected_by_the_operator_guard(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    c = operator_app()
    r = c.get("/open", headers=auth("adm", "o", 1))
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
    assert c.get("/login", headers={**auth("adm", "o", 1), "x-admin-id": "111"}).status_code == 401   # no fall-through


def test_without_a_secret_a_bearer_token_cannot_be_valid(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    token = ot.issue_operator_token("boss@clockyn.com", 1, key=SECRET)[0]
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    assert operator_app().get("/open", headers=bearer(token)).status_code == 401


def test_deleted_operator_is_rejected_immediately(clean):
    c = operator_app()
    headers = session("agent@clockyn.com", 2)
    assert c.get("/login", headers=headers).status_code == 200
    del clean["agent@clockyn.com"]
    r = c.get("/login", headers=headers)
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}


def test_role_change_takes_effect_immediately_and_the_database_role_wins(clean):
    c = operator_app()
    claims_say_super = session("agent@clockyn.com", 1)          # the claim says role 1 ...
    r = c.get("/super", headers=claims_say_super)               # ... the database says 2
    assert r.status_code == 403 and r.json() == {"detail": "Only role_id=1 admins can perform this action"}
    assert c.get("/login", headers=claims_say_super).status_code == 200
    clean["agent@clockyn.com"] = 1                              # promoted: same token now passes
    assert c.get("/super", headers=claims_say_super).status_code == 200
    clean["agent@clockyn.com"] = 2                              # demoted again
    assert c.get("/super", headers=claims_say_super).status_code == 403


def test_super_only_with_role_2_session_is_403_not_401():
    r = operator_app().get("/super", headers=session("agent@clockyn.com", 2))
    assert r.status_code == 403


def test_an_invalid_bearer_never_falls_through_to_the_legacy_header(monkeypatch):
    c = operator_app()
    for enforce in ("0", "1"):
        monkeypatch.setenv("OPERATOR_ENFORCE", enforce)
        r = c.get("/login", headers={"Authorization": "Bearer nonsense", "x-admin-id": "111"})      # /login always checks
        assert r.status_code == 401, enforce
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    expired = ot.issue_operator_token("boss@clockyn.com", 1, now=time.time() - 9999, key=SECRET, ttl_seconds=10)[0]
    assert c.get("/open", headers={**bearer(expired), "x-admin-id": "111"}).status_code == 401
    # without a bearer the legacy header still works (legacy enabled by default)
    assert c.get("/open", headers={"x-admin-id": "111"}).status_code == 200


def test_not_enforced_open_routes_ignore_a_bearer_that_is_not_a_valid_session():
    """Behaviour of a route that was open before: unchanged, an app token sent by mistake does not break it."""
    c = operator_app()
    assert c.get("/open", headers=auth("adm", "o", 1)).status_code == 200
    assert c.get("/open", headers=bearer("garbage")).status_code == 200


def test_the_key_still_comes_first(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", "k" * 32)
    c = operator_app()
    assert c.get("/login", headers={"X-Operator-Key": "k" * 32, "Authorization": "Bearer nonsense"}).status_code == 200
    assert c.get("/login", headers={"X-Operator-Key": "wrong", **session()}).status_code == 403


def test_failed_session_attempts_count_in_the_rate_limit(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setenv("RATE_LIMIT_OPERATOR_FAIL", "3/60")
    clock = [0.0]
    rate_limit.set_clock(lambda: clock[0])
    c = operator_app()
    for _ in range(3):
        assert c.get("/open", headers=bearer("garbage")).status_code == 401
    r = c.get("/open", headers=session())
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    clock[0] = 61.0
    assert c.get("/open", headers=session()).status_code == 200


def test_a_valid_session_and_a_role_refusal_are_not_counted_as_failures(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_OPERATOR_FAIL", "2/60")
    c = operator_app()
    for _ in range(6):
        assert c.get("/super", headers=session("agent@clockyn.com", 2)).status_code == 403
        assert c.get("/open", headers=session()).status_code == 200


def test_a_lookup_failure_is_503_when_enforced(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")

    def boom(who):
        raise RuntimeError("database down")
    oa.set_lookup(boom)
    r = operator_app().get("/open", headers=session())
    assert r.status_code == 503


def test_operator_id_attribution(monkeypatch):
    c = operator_app()
    assert c.get("/open", headers=session("agent@clockyn.com", 2)).json()["who"] == "agent@clockyn.com"

    class Req:
        state = type("S", (), {})()
        headers = {"x-admin-id": "legacy@x.com"}
    assert oa.operator_id(Req) == "legacy@x.com"
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    assert oa.operator_id(Req) is None


# ----------------------------------------------------------------------------------------------------- the app guard
def test_an_operator_token_is_rejected_by_the_app_guard(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({"boss@clockyn.com": {"role": 1, "org": "org-A"}}).install()
    c = TestClient(make_app())
    op = session()
    r = c.get("/me", headers=op)
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
    assert c.get("/home/dashboard-status", params={"org_id": "org-A"}, headers=op).status_code == 401
    assert c.get("/me", headers=auth("boss@clockyn.com", "org-A", 1)).status_code == 200     # an app token still works there


def test_an_operator_token_never_identifies_a_caller_even_with_require_auth_off():
    FakeDirectory({"boss@clockyn.com": {"role": 1, "org": "org-A"}}).install()
    c = TestClient(make_app())
    assert c.get("/me", headers=session()).json() == {"identity": None}
    assert c.get("/me", headers=auth("boss@clockyn.com", "org-A", 1)).json()["identity"]["user"] == "boss@clockyn.com"


def test_with_require_auth_an_operator_token_reaches_the_operator_guard(monkeypatch):
    """The global guard leaves operator routes to the operator mechanism: it must not answer 403 for an operator token."""
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({}).install()
    c = TestClient(make_app())
    assert c.get("/gen-dashboard/stats", headers=session()).status_code == 200


# ----------------------------------------------------------------------------------------------------- WebSocket
def ws_client():
    from router import gen_live_chat
    app = FastAPI()
    app.include_router(gen_live_chat.router)
    return TestClient(app)


def refused(client, url):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(url):
            pass
    return e.value.code


def pong(client, url):
    with client.websocket_connect(url) as ws:
        ws.send_text("ping")
        return ws.receive_text()


def test_websocket_accepts_a_session_token_and_refuses_everything_else_when_enforced(monkeypatch, clean):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    c = ws_client()
    token = ot.issue_operator_token("boss@clockyn.com", 1, key=SECRET)[0]
    assert pong(c, f"/ws/support-ops?token={token}") == "pong"
    assert refused(c, "/ws/support-ops") == 4403
    assert refused(c, "/ws/support-ops?admin_id=boss@clockyn.com") == 4403       # legacy is off
    assert refused(c, f"/ws/support-ops?token={token}x") == 4403
    assert refused(c, "/ws/support-ops?token=garbage") == 4403
    assert refused(c, "/ws/support-ops?token=" + auth_token.issue_token("u", "o", 1, key=SECRET)[0]) == 4403   # app token
    expired = ot.issue_operator_token("boss@clockyn.com", 1, now=time.time() - 9999, key=SECRET, ttl_seconds=10)[0]
    assert refused(c, f"/ws/support-ops?token={expired}") == 4403
    del clean["boss@clockyn.com"]                                              # operator deleted: the same token is refused
    assert refused(c, f"/ws/support-ops?token={token}") == 4403


def test_websocket_legacy_admin_id_only_while_legacy_is_enabled(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    c = ws_client()
    assert pong(c, "/ws/support-ops?admin_id=boss@clockyn.com") == "pong"
    assert refused(c, "/ws/support-ops?admin_id=intruder@evil.com") == 4403
    # a token that is present but invalid does not fall back to a valid admin_id
    assert refused(c, "/ws/support-ops?token=garbage&admin_id=boss@clockyn.com") == 4403
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    assert refused(c, "/ws/support-ops?admin_id=boss@clockyn.com") == 4403


def test_websocket_is_open_when_not_enforced():
    assert pong(ws_client(), "/ws/support-ops") == "pong"


# ----------------------------------------------------------------------------------------------------- operator OTP
class OtpDb:
    """Stateful fake of the three tables used by /auth/admin-login and /auth/admin-login/verify-otp."""

    def __init__(self, operators=None):
        self.operators = operators if operators is not None else {"boss@clockyn.com": 1}
        self.otp = None            # dict(email, otp_code, attempts, expires_at)
        self.row = None
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return self

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.row = None
        if s.startswith("SELECT email, role_id FROM gen_admin_login"):
            role = self.operators.get(params[0])
            self.row = None if params[0] not in self.operators else {"email": params[0], "role_id": role}
        elif s.startswith("SELECT attempts, expires_at FROM email_otp_verification"):
            if self.otp and self.otp["email"] == params[0]:
                self.row = {"attempts": self.otp["attempts"], "expires_at": self.otp["expires_at"]}
        elif s.startswith("SELECT * FROM email_otp_verification"):
            if self.otp and self.otp["email"] == params[0] and self.otp["expires_at"] > params[1]:
                self.row = dict(self.otp)
        elif s.startswith("DELETE FROM email_otp_verification"):
            self.otp = None
        elif s.startswith("INSERT INTO email_otp_verification"):
            email, code, _v, attempts, _c, expires = params
            self.otp = {"email": email, "otp_code": code, "attempts": attempts, "expires_at": expires}
        elif s.startswith("UPDATE email_otp_verification SET attempts = attempts + 1"):
            self.otp["attempts"] += 1
        else:
            raise AssertionError("unexpected SQL: " + s)

    def fetchone(self):
        return self.row


@pytest.fixture
def otp(monkeypatch):
    import router.auth as ra
    from core_functions import auth_function as af
    db = OtpDb()
    sent = []

    async def fake_send(email, code, purpose):
        sent.append((email, code, purpose))
        return True
    monkeypatch.setattr(ra, "get_db_connection", lambda: db)
    monkeypatch.setattr(ra, "return_connection", lambda c: None)
    monkeypatch.setattr(af, "_send_email_otp_async", fake_send)
    db.sent = sent
    db.ra = ra
    return db


def send(db, email="boss@clockyn.com"):
    return asyncio.run(db.ra.gen_admin_login_route(db.ra.GenAdminLoginRequest(email=email)))


def verify(db, code, email="boss@clockyn.com"):
    return asyncio.run(db.ra.gen_admin_verify_otp_route(db.ra.GenAdminOTPVerifyRequest(email=email, otp=str(code))))


def status_of(fn, *a):
    with pytest.raises(HTTPException) as e:
        fn(*a)
    return e.value.status_code, e.value.detail


def test_the_code_is_six_digits_from_the_secrets_module(otp, monkeypatch):
    import secrets
    assert "random.randint" not in (ROOT / "router" / "auth.py").read_text(encoding="utf-8")
    calls = []
    real = secrets.randbelow
    monkeypatch.setattr(secrets, "randbelow", lambda n: calls.append(n) or real(n))
    for _ in range(40):
        send(otp)
        code = otp.sent[-1][1]
        assert 100000 <= code <= 999999 and otp.otp["otp_code"] == code and otp.otp["attempts"] == 0
    assert set(calls) == {900000}


def test_unknown_email_is_still_401_email_not_found(otp):
    assert status_of(send, otp, "nobody@clockyn.com") == (401, "Email not found")
    assert otp.sent == []


def test_verify_answers_the_old_shape_plus_the_token_when_a_secret_is_set(otp):
    send(otp)
    result = verify(otp, otp.sent[-1][1])
    assert set(result) == {"success", "email", "role_id", "access_token", "token_type", "expires_in"}
    assert result["success"] is True and result["email"] == "boss@clockyn.com" and result["role_id"] == 1
    assert result["token_type"] == "Bearer" and result["expires_in"] == 12 * 3600
    claims = ot.verify_operator_token(result["access_token"])
    assert claims["sub"] == "boss@clockyn.com" and claims["role"] == 1 and claims["exp"] - claims["iat"] == 12 * 3600
    # and the token really opens the operator routes
    assert operator_app().get("/super", headers=bearer(result["access_token"])).status_code == 200


def test_verify_omits_the_token_without_a_usable_secret(otp, monkeypatch):
    for value in (None, "short"):
        if value is None:
            monkeypatch.delenv("AUTH_TOKEN_SECRET")
        else:
            monkeypatch.setenv("AUTH_TOKEN_SECRET", value)
        send(otp)
        assert verify(otp, otp.sent[-1][1]) == {"success": True, "email": "boss@clockyn.com", "role_id": 1}


def test_ttl_env_changes_expires_in(otp, monkeypatch):
    monkeypatch.setenv("OPERATOR_TOKEN_TTL_HOURS", "3")
    send(otp)
    assert verify(otp, otp.sent[-1][1])["expires_in"] == 3 * 3600


def test_the_code_is_single_use(otp):
    send(otp)
    code = otp.sent[-1][1]
    assert verify(otp, code)["success"] is True
    assert otp.otp is None
    assert status_of(verify, otp, code) == (400, "Invalid or expired OTP")


def test_five_wrong_attempts_lock_the_code_even_for_the_right_one(otp):
    send(otp)
    code = otp.sent[-1][1]
    wrong = 100000 if code != 100000 else 100001
    for i in range(5):
        assert status_of(verify, otp, wrong) == (400, "Invalid or expired OTP")
        assert otp.otp["attempts"] == i + 1
    assert status_of(verify, otp, wrong) == (429, "Too many incorrect OTP attempts. Please request a new OTP.")
    assert status_of(verify, otp, code)[0] == 429                          # the right code no longer works
    assert otp.otp["attempts"] == 5                                         # locked attempts are not counted further
    # a new code cannot be requested while the locked one is alive (the counter cannot be reset at will)
    assert status_of(send, otp) == (429, "Too many incorrect OTP attempts. Please wait for the current OTP to expire before requesting a new one.")
    assert len(otp.sent) == 1
    # ... but once it expired a new one is issued
    otp.otp["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    send(otp)
    assert len(otp.sent) == 2 and otp.otp["attempts"] == 0
    assert verify(otp, otp.sent[-1][1])["success"] is True


def test_four_wrong_then_the_right_code_still_works(otp):
    send(otp)
    code = otp.sent[-1][1]
    wrong = 100000 if code != 100000 else 100001
    for _ in range(4):
        status_of(verify, otp, wrong)
    assert verify(otp, code)["success"] is True


def test_bad_format_and_expired_codes(otp):
    send(otp)
    assert status_of(verify, otp, "abc") == (400, "Invalid OTP format")
    otp.otp["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert status_of(verify, otp, otp.otp["otp_code"]) == (400, "Invalid or expired OTP")


def test_the_comparison_is_constant_time_and_leading_zero_safe():
    src = (ROOT / "router" / "auth.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src
    import router.auth as ra
    assert ra._otp_digits(123456) == b"123456" and ra._otp_digits(5) == b"000005"


def test_operator_deleted_between_send_and_verify_is_401_not_500(otp):
    send(otp)
    code = otp.sent[-1][1]
    otp.operators.clear()
    assert status_of(verify, otp, code) == (401, "Email not found")


# ----------------------------------------------------------------------------------------------------- start-up warnings
def test_startup_warnings(monkeypatch):
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    msgs = oa.startup_security_warnings()
    assert len(msgs) == 2 and all(m.startswith("SECURITY:") for m in msgs)
    assert any("REQUIRE_AUTH is off" in m for m in msgs) and any("operator routes are not enforced" in m for m in msgs)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    msgs = oa.startup_security_warnings()
    assert len(msgs) == 1 and "legacy x-admin-id" in msgs[0]
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    assert oa.startup_security_warnings() == []
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    msgs = oa.startup_security_warnings()
    assert len(msgs) == 1 and "AUTH_TOKEN_SECRET" in msgs[0]


def test_the_startup_hook_exists_and_never_raises(monkeypatch):
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _warn_insecure_auth_config" in src and "startup_security_warnings" in src
    monkeypatch.setattr(oa.auth_token, "require_auth_enabled", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert oa.startup_security_warnings() == []


# ------------------------------------------------------------------ invoice download from the console (REQUIRE_AUTH on)
def _invoice_client():
    from utils import email as em
    app = FastAPI()
    ad.install(app)

    @app.get("/invoice/download")
    def download(request: Request):
        return {"access": em._authorize_invoice_download(request)}
    return TestClient(app)


def test_the_console_can_download_an_invoice_with_its_operator_session(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({"adm": {"role": 1, "org": "o"}, "stf": {"role": 3, "org": "o"}}).install()
    c = _invoice_client()
    assert c.get("/invoice/download", headers=session()).json() == {"access": "operator"}
    assert c.get("/invoice/download", headers=session("agent@clockyn.com", 2)).json() == {"access": "operator"}   # any operator
    assert c.get("/invoice/download", headers=auth("adm", "o", 1)).json() == {"access": "token"}                  # unchanged
    assert c.get("/invoice/download", headers=auth("stf", "o", 3)).status_code == 403                             # unchanged
    assert c.get("/invoice/download").status_code == 401                                                          # unchanged


def test_the_invoice_download_refuses_a_bad_or_deleted_operator_session(monkeypatch, clean):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory().install()
    c = _invoice_client()
    assert c.get("/invoice/download", headers=bearer("not-a-token")).status_code == 401
    expired = bearer(ot.issue_operator_token("boss@clockyn.com", 1, key=SECRET, now=time.time() - 10 * 3600 * 24)[0])
    assert c.get("/invoice/download", headers=expired).status_code == 401
    clean.pop("boss@clockyn.com")                                    # the operator was deleted after signing in
    assert c.get("/invoice/download", headers=session()).status_code == 401


def test_the_invoice_download_stays_open_when_require_auth_is_off():
    c = _invoice_client()
    assert c.get("/invoice/download").json() == {"access": "open"}
