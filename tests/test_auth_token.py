"""DB-free tests of utils/auth_token.py: token round trip, tampering, expiry, clock skew, alg confusion, settings and the
start-up validation. Run: python -m pytest tests/test_auth_token.py -q
"""
import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import auth_token as T  # noqa: E402

KEY = "k" * 40
NOW = 1_800_000_000


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge(header: dict, claims: dict, key: str = KEY, sign: bool = True) -> str:
    head = b64(json.dumps(header, separators=(",", ":")).encode())
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = b64(hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()) if sign else ""
    return f"{head}.{body}.{sig}"


GOOD_CLAIMS = {"sub": "u1", "org": "o1", "role": 3, "iat": NOW, "exp": NOW + 1000, "ep": 0}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AUTH_TOKEN_SECRET", "AUTH_TOKEN_TTL_DAYS", "REQUIRE_AUTH"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------- round trip
def test_round_trip_carries_every_claim():
    token, expires_in = T.issue_token("u1", "org-1", 2, 7, now=NOW, key=KEY)
    claims = T.verify_token(token, now=NOW + 5, key=KEY)
    assert claims == {"sub": "u1", "org": "org-1", "role": 2, "iat": NOW, "exp": NOW + expires_in, "ep": 7}
    assert expires_in == 90 * 86400                                          # default TTL = device-token TTL


def test_token_is_a_standard_three_segment_jwt_with_hs256_header():
    token, _ = T.issue_token("u1", "o1", 1, 0, now=NOW, key=KEY)
    head, body, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(head))) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(pad(body)))["sub"] == "u1"
    assert "=" not in token and "+" not in token and "/" not in token


def test_missing_org_is_an_empty_string_claim():
    token, _ = T.issue_token("u1", None, 1, 0, now=NOW, key=KEY)
    assert T.verify_token(token, now=NOW, key=KEY)["org"] == ""


def test_ttl_comes_from_env_in_days(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("AUTH_TOKEN_TTL_DAYS", "7")
    token, expires_in = T.issue_token("u1", "o", 3, 0, now=NOW)
    assert expires_in == 7 * 86400 and T.verify_token(token, now=NOW)["exp"] == NOW + 7 * 86400


@pytest.mark.parametrize("raw, days", [("", 90), ("abc", 90), ("0", 90), ("-3", 90), ("99999", 90), ("30", 30), (" 365 ", 365)])
def test_ttl_days_parsing(monkeypatch, raw, days):
    monkeypatch.setenv("AUTH_TOKEN_TTL_DAYS", raw)
    assert T.ttl_days() == days


def test_issue_needs_a_usable_secret(monkeypatch):
    with pytest.raises(T.TokenError):
        T.issue_token("u1", "o", 3, 0)                                       # no secret configured
    with pytest.raises(T.TokenError):
        T.issue_token("u1", "o", 3, 0, key="short")


# ---------------------------------------------------------------------------- tampering
def test_tampered_claims_fail_the_signature():
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY)
    head, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["role"] = 1
    evil = b64(json.dumps(claims, separators=(",", ":")).encode())
    with pytest.raises(T.TokenError) as exc:
        T.verify_token(f"{head}.{evil}.{sig}", now=NOW, key=KEY)
    assert exc.value.code == "invalid"


def test_tampered_signature_and_wrong_secret_fail():
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY)
    head, body, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    for bad in (f"{head}.{body}.{flipped}", f"{head}.{body}.", f"{head}.{body}"):
        with pytest.raises(T.TokenError):
            T.verify_token(bad, now=NOW, key=KEY)
    with pytest.raises(T.TokenError):
        T.verify_token(token, now=NOW, key="z" * 40)


@pytest.mark.parametrize("header", [
    {"alg": "none", "typ": "JWT"},
    {"alg": "None"},
    {"alg": "NONE"},
    {"alg": "HS384"},
    {"alg": "RS256"},
    {"typ": "JWT"},
    {"alg": ["HS256"]},
])
def test_alg_confusion_is_refused_even_with_a_valid_hs256_signature_of_the_same_bytes(header):
    # signed with the right key (the strongest forgery): the algorithm in the header alone makes it invalid
    with pytest.raises(T.TokenError) as exc:
        T.verify_token(forge(header, GOOD_CLAIMS), now=NOW, key=KEY)
    assert exc.value.code == "invalid"
    with pytest.raises(T.TokenError):
        T.verify_token(forge(header, GOOD_CLAIMS, sign=False), now=NOW, key=KEY)


@pytest.mark.parametrize("token", [
    "", "a", "a.b", "a.b.c", "a.b.c.d", "....", "!!.!!.!!", "e30.e30.e30", "e30.a.e30", None, 123, "x" * 3000,
])
def test_malformed_tokens_are_invalid_not_crashes(token):
    with pytest.raises(T.TokenError) as exc:
        T.verify_token(token, now=NOW, key=KEY)
    assert exc.value.code == "invalid"


@pytest.mark.parametrize("claims", [
    {**GOOD_CLAIMS, "sub": ""},
    {**GOOD_CLAIMS, "sub": 5},
    {**GOOD_CLAIMS, "org": 5},
    {**GOOD_CLAIMS, "role": "3"},
    {**GOOD_CLAIMS, "role": True},
    {**GOOD_CLAIMS, "exp": "soon"},
    {**GOOD_CLAIMS, "iat": None},
    {**GOOD_CLAIMS, "ep": "0"},
    [GOOD_CLAIMS],
])
def test_bad_claim_types_are_invalid(claims):
    with pytest.raises(T.TokenError) as exc:
        T.verify_token(forge({"alg": "HS256", "typ": "JWT"}, claims), now=NOW, key=KEY)
    assert exc.value.code == "invalid"


def test_missing_secret_rejects_every_token():
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY)
    with pytest.raises(T.TokenError):
        T.verify_token(token, now=NOW)                                       # no AUTH_TOKEN_SECRET in the environment


# ---------------------------------------------------------------------------- expiry and skew
def test_expired_token_is_reported_as_expired():
    token, ttl = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY, ttl_seconds=100)
    assert T.verify_token(token, now=NOW + 100, key=KEY)["sub"] == "u1"
    with pytest.raises(T.TokenError) as exc:
        T.verify_token(token, now=NOW + 100 + T.CLOCK_SKEW_SECONDS + 1, key=KEY)
    assert exc.value.code == "expired"


@pytest.mark.parametrize("offset, ok", [
    (100, True), (100 + 60, True), (100 + 61, False),                         # exp = iat + 100, skew 60 s
])
def test_clock_skew_tolerance_after_expiry(offset, ok):
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY, ttl_seconds=100)
    if ok:
        assert T.verify_token(token, now=NOW + offset, key=KEY)
    else:
        with pytest.raises(T.TokenError):
            T.verify_token(token, now=NOW + offset, key=KEY)


@pytest.mark.parametrize("verify_at, ok", [(-60, True), (-61, False), (0, True)])
def test_clock_skew_tolerance_before_issue(verify_at, ok):
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY)
    if ok:
        assert T.verify_token(token, now=NOW + verify_at, key=KEY)
    else:
        with pytest.raises(T.TokenError) as exc:
            T.verify_token(token, now=NOW + verify_at, key=KEY)
        assert exc.value.code == "invalid"


def test_signature_is_compared_in_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(T.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    token, _ = T.issue_token("u1", "o1", 3, 0, now=NOW, key=KEY)
    T.verify_token(token, now=NOW, key=KEY)
    assert calls


# ---------------------------------------------------------------------------- header parsing
@pytest.mark.parametrize("value, token", [
    ("Bearer abc.def.ghi", "abc.def.ghi"), ("bearer  abc", "abc"), ("BEARER abc", "abc"),
    ("Basic abc", None), ("Bearer", None), ("Bearer ", None), ("abc", None), ("", None), (None, None),
])
def test_bearer_header_parsing(value, token):
    assert T.bearer_from_header(value) == token


# ---------------------------------------------------------------------------- settings and start-up validation
def test_require_auth_flag_values(monkeypatch):
    assert T.require_auth_enabled() is False
    for raw, expected in (("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("", False), ("no", False)):
        monkeypatch.setenv("REQUIRE_AUTH", raw)
        assert T.require_auth_enabled() is expected


def test_secret_shorter_than_32_characters_is_not_usable(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "s" * 31)
    assert T.tokens_enabled() is False
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "s" * 32)
    assert T.tokens_enabled() is True


def test_startup_validation_default_off_does_not_need_a_secret():
    summary = T.validate_auth_config()
    assert summary == {"tokens_enabled": False, "require_auth": False, "ttl_days": 90}


def test_startup_validation_refuses_require_auth_without_secret(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    with pytest.raises(T.AuthConfigError) as exc:
        T.validate_auth_config()
    assert "AUTH_TOKEN_SECRET" in str(exc.value) and "is not set" in str(exc.value)


def test_startup_validation_refuses_require_auth_with_a_short_secret(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "true")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "too-short")
    with pytest.raises(T.AuthConfigError) as exc:
        T.validate_auth_config()
    assert "shorter than 32" in str(exc.value) and "too-short" not in str(exc.value)   # the secret is never echoed


def test_startup_validation_accepts_require_auth_with_a_good_secret(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    assert T.validate_auth_config() == {"tokens_enabled": True, "require_auth": True, "ttl_days": 90}


def test_startup_validation_tolerates_a_short_secret_while_require_auth_is_off(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "short")
    summary = T.validate_auth_config()
    assert summary["tokens_enabled"] is False                                # tokens are simply not issued


def test_app_py_runs_the_validation_as_a_startup_hook_and_installs_the_guard_before_the_routers():
    source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    assert "validate_auth_config()" in source and '@app.on_event("startup")\ndef _validate_auth_config' in source
    assert source.index("_install_auth(app)") < source.index("app.include_router(health.router)")
    # the hook is registered before the migrations hook: a refused configuration must not touch the database
    assert source.index("def _validate_auth_config") < source.index("def _run_migrations_if_enabled")
