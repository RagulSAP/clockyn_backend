"""DB-free tests for the OTP send throttle on POST /auth/generate-otp.

A scripted fake cursor simulates the `otp_send_log` table (rows are kept as "age in seconds"); every other query is
answered from a queue, exactly like tests/test_user_auth_mode.py. SMS / SES senders are monkeypatched.
Run: python -m pytest tests/test_otp_throttle.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_function as af  # noqa: E402

PHONE = "9000000003"
STAFF = {"user_id": "u1", "role_id": 3, "status": 1}


class ThrottleCursor:
    """`log_ages`: ages (seconds) of the existing otp_send_log rows of the contact. Other SELECTs read from `fetch`."""

    def __init__(self, fetch=None, log_ages=()):
        self.sql = []
        self.fetch = list(fetch or [])
        self.log = [float(a) for a in log_ages]
        self._next = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        if s.startswith("SELECT") and "FROM otp_send_log" in s:
            ages = [a for a in self.log if a < 3600]
            self._next = {
                "n": len(ages),
                "since_last": min(ages) if ages else None,
                "since_first": max(ages) if ages else None,
            }
        elif s.startswith("INSERT INTO otp_send_log"):
            self.log.append(0.0)
        elif s.startswith("DELETE FROM otp_send_log WHERE id"):
            self.log.remove(min(self.log))

    def fetchone(self):
        if self._next is not None:
            row, self._next = self._next, None
            return row
        return self.fetch.pop(0) if self.fetch else None

    def close(self):
        pass

    def count(self, text):
        return sum(text in s for s, _ in self.sql)


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


def use_db(monkeypatch, cursor):
    monkeypatch.setattr(af, "get_db_connection", lambda: FakeConn(cursor))
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    return cursor


def no_db(monkeypatch):
    def boom():
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(af, "get_db_connection", boom)


def run(coro):
    return asyncio.run(coro)


def phone_login(contact=PHONE):
    return af.GenerateOTPRequest(contact=contact, purpose="login", role_id=3)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("USER_AUTH", "user_auth", "OTP_BYPASS", "OTP_MAX_PER_HOUR", "OTP_MIN_GAP_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    sms = []
    monkeypatch.setattr(af, "_send_msg91_sms", lambda m, o: sms.append(m) or True)
    return sms


def sent_nothing(cur, sms):
    return (
        sms == []
        and cur.count("DELETE FROM moblie_otp_verification") == 0
        and cur.count("INSERT INTO moblie_otp_verification") == 0
        and cur.count("INSERT INTO otp_send_log") == 0
    )


# ---------------------------------------------------------------------------- limits
def test_defaults_and_env_overrides(monkeypatch):
    assert af._otp_throttle_limits() == (5, 30)
    monkeypatch.setenv("OTP_MAX_PER_HOUR", "8")
    monkeypatch.setenv("OTP_MIN_GAP_SECONDS", "0")
    assert af._otp_throttle_limits() == (8, 0)


@pytest.mark.parametrize("name,bad", [
    ("OTP_MAX_PER_HOUR", "abc"), ("OTP_MAX_PER_HOUR", "0"), ("OTP_MAX_PER_HOUR", "-3"), ("OTP_MAX_PER_HOUR", "2.5"), ("OTP_MAX_PER_HOUR", " "),
    ("OTP_MIN_GAP_SECONDS", "x"), ("OTP_MIN_GAP_SECONDS", "-1"), ("OTP_MIN_GAP_SECONDS", "1e3"),
])
def test_invalid_env_values_fall_back_to_the_defaults(monkeypatch, name, bad):
    monkeypatch.setenv(name, bad)
    assert af._otp_throttle_limits() == (5, 30)


# ---------------------------------------------------------------------------- the gap rule
def test_first_send_is_logged_and_old_rows_are_pruned(monkeypatch, clean):
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None]))
    res = run(af.generate_otp(phone_login()))
    assert res["success"] is True and clean == [PHONE]
    assert cur.count("INSERT INTO otp_send_log") == 1
    assert ("INSERT INTO otp_send_log (contact) VALUES (%s)", (PHONE,)) in cur.sql
    assert cur.count("DELETE FROM otp_send_log WHERE sent_at < now() - interval '24 hours'") == 1
    assert cur.log == [0.0]


def test_second_request_inside_the_gap_is_a_429_and_nothing_is_sent(monkeypatch, clean):
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[10]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 429
    assert e.value.detail == "Please wait 20 seconds before requesting another code."
    assert e.value.headers["Retry-After"] == "20"
    # the previous code stays valid: it was neither deleted nor replaced, and no SMS went out
    assert sent_nothing(cur, clean)
    assert cur.log == [10.0]


def test_request_after_the_gap_is_allowed(monkeypatch, clean):
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[30]))
    assert run(af.generate_otp(phone_login()))["success"] is True
    assert clean == [PHONE] and len(cur.log) == 2


def test_gap_is_configurable(monkeypatch, clean):
    monkeypatch.setenv("OTP_MIN_GAP_SECONDS", "120")
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[100]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 429 and "20 seconds" in e.value.detail
    assert sent_nothing(cur, clean)

    monkeypatch.setenv("OTP_MIN_GAP_SECONDS", "5")
    use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[10]))
    assert run(af.generate_otp(phone_login()))["success"] is True


# ---------------------------------------------------------------------------- the hourly cap
def test_sixth_request_within_the_hour_is_a_429(monkeypatch, clean):
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[3000, 2000, 1000, 500, 100]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 429 and e.value.detail == "Too many codes requested. Try again later."
    assert e.value.headers["Retry-After"] == "600"  # the oldest of the five leaves the window in 600 s
    assert sent_nothing(cur, clean)


def test_fifth_request_is_still_allowed_and_rows_older_than_an_hour_do_not_count(monkeypatch, clean):
    use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[3000, 2000, 1000, 100]))
    assert run(af.generate_otp(phone_login()))["success"] is True
    clean.clear()
    use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[7200, 5000, 4000, 3700, 3601, 100]))
    assert run(af.generate_otp(phone_login()))["success"] is True


def test_hourly_cap_is_configurable(monkeypatch, clean):
    monkeypatch.setenv("OTP_MAX_PER_HOUR", "2")
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[900, 400]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 429 and "Too many codes" in e.value.detail
    assert sent_nothing(cur, clean)
    monkeypatch.setenv("OTP_MAX_PER_HOUR", "3")
    use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, None], log_ages=[900, 400]))
    assert run(af.generate_otp(phone_login()))["success"] is True


# ---------------------------------------------------------------------------- e-mail channel, every purpose
def test_email_contact_is_stored_lower_cased_and_throttled(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    sent = []

    async def fake_send(email, otp, purpose):
        sent.append(email)
        return True

    monkeypatch.setattr(af, "_send_email_otp_async", fake_send)
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[{"n": 1}, STAFF, None]))
    run(af.generate_otp(af.GenerateOTPRequest(contact="  Staff@Example.COM ", purpose="login", role_id=3)))
    assert ("INSERT INTO otp_send_log (contact) VALUES (%s)", ("staff@example.com",)) in cur.sql
    assert ("staff@example.com",) in [p for s, p in cur.sql if s.startswith("SELECT COUNT(*) AS n, EXTRACT")]
    # a different spelling of the same address hits the same log entries
    use_db(monkeypatch, ThrottleCursor(fetch=[{"n": 1}, STAFF, None], log_ages=[5]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="STAFF@example.com", purpose="login", role_id=3)))
    assert e.value.status_code == 429
    assert sent == ["staff@example.com"]


@pytest.mark.parametrize("purpose", ["registration", "login", "delete_account", "update_email"])
def test_every_purpose_is_throttled(monkeypatch, clean, purpose):
    fetch = [None, None] if purpose == "registration" else [STAFF, None] if purpose == "login" else [None]
    cur = use_db(monkeypatch, ThrottleCursor(fetch=fetch, log_ages=[1]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact=PHONE, purpose=purpose, role_id=3 if purpose == "login" else None)))
    assert e.value.status_code == 429
    assert sent_nothing(cur, clean)


# ---------------------------------------------------------------------------- bypass numbers
def test_bypass_numbers_skip_the_throttle_and_the_log(monkeypatch):
    monkeypatch.setenv("OTP_BYPASS", "1")
    no_db(monkeypatch)  # no log lookup, no insert: the database is not touched at all
    for _ in range(10):
        assert run(af.generate_otp(af.GenerateOTPRequest(contact="7904559310", purpose="login", role_id=1)))["success"] is True


def test_a_bypass_number_is_throttled_like_any_other_when_the_bypass_is_off(monkeypatch, clean):
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[{"user_id": "u9", "role_id": 1, "status": 1}, None], log_ages=[3]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="7904559310", purpose="login", role_id=1)))
    assert e.value.status_code == 429
    assert sent_nothing(cur, clean)


# ---------------------------------------------------------------------------- failed sends and early rejections
def test_failed_email_send_does_not_burn_quota(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    monkeypatch.setenv("OTP_MAX_PER_HOUR", "1")
    ok = {"value": False}

    async def send(email, otp, purpose):
        return ok["value"]

    monkeypatch.setattr(af, "_send_email_otp_async", send)
    cur = ThrottleCursor(fetch=[{"n": 1}, STAFF, None, {"n": 1}, STAFF, None])
    use_db(monkeypatch, cur)
    req = lambda: af.GenerateOTPRequest(contact="staff@example.com", purpose="login", role_id=3)  # noqa: E731
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(req()))
    assert e.value.status_code == 503
    assert cur.count("INSERT INTO otp_send_log") == 1 and cur.count("DELETE FROM otp_send_log WHERE id") == 1
    assert cur.log == []  # the failed send was taken back
    # with a cap of ONE per hour the retry is still accepted
    ok["value"] = True
    assert run(af.generate_otp(req()))["success"] is True
    assert cur.log == [0.0]


def test_requests_rejected_earlier_are_not_logged(monkeypatch, clean):
    # validation
    no_db(monkeypatch)
    with pytest.raises(HTTPException):
        run(af.generate_otp(af.GenerateOTPRequest(contact=PHONE, purpose="nonsense")))
    # phone in e-mail mode
    monkeypatch.setenv("USER_AUTH", "email")
    with pytest.raises(HTTPException):
        run(af.generate_otp(phone_login()))
    monkeypatch.delenv("USER_AUTH")
    # role mismatch (admin account on the employee portal)
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[{"user_id": "u9", "role_id": 1, "status": 1}]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 403
    assert cur.count("otp_send_log") == 0 and clean == []
    # the existing "attempts >= 5" lock is unchanged and also answers before anything is logged
    from utils.timezone import get_utc_now
    from datetime import timedelta

    locked = {"attempts": 5, "expires_at": get_utc_now() + timedelta(minutes=3)}
    cur = use_db(monkeypatch, ThrottleCursor(fetch=[STAFF, locked]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(phone_login()))
    assert e.value.status_code == 429 and "incorrect OTP attempts" in e.value.detail
    assert cur.count("INSERT INTO otp_send_log") == 0 and cur.count("FROM otp_send_log") == 0
