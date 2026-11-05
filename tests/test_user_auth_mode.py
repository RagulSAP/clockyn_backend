"""DB-free tests for the USER_AUTH switch (``otp`` = mobile + SMS code, ``email`` = email + emailed code).

The DB layer is replaced by a scripted fake cursor, SMS / SES senders are monkeypatched.
Run: python -m pytest tests/test_user_auth_mode.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_function as af  # noqa: E402
from utils import auth_mode  # noqa: E402


class FakeCursor:
    """Records every statement; `fetch` is a list of rows handed out by successive fetchone() calls."""

    def __init__(self, fetch=None):
        self.sql = []
        self.fetch = list(fetch or [])

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetch.pop(0) if self.fetch else None

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def use_db(monkeypatch, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(af, "get_db_connection", lambda: conn)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    return conn


def no_db(monkeypatch):
    def boom():
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(af, "get_db_connection", boom)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("USER_AUTH", raising=False)
    monkeypatch.delenv("user_auth", raising=False)
    monkeypatch.delenv("OTP_BYPASS", raising=False)


# ---------------------------------------------------------------------------- the flag
def test_default_is_otp():
    assert auth_mode.get_user_auth_mode() == "otp"
    assert auth_mode.is_email_auth() is False


@pytest.mark.parametrize("value,expected", [("email", "email"), (" EMAIL ", "email"), ("otp", "otp"), ("Otp", "otp"), ("", "otp"), ("sms", "otp")])
def test_flag_values(monkeypatch, value, expected):
    monkeypatch.setenv("USER_AUTH", value)
    assert auth_mode.get_user_auth_mode() == expected


def test_lowercase_variable_name_is_accepted(monkeypatch):
    monkeypatch.setenv("user_auth", "email")
    assert auth_mode.get_user_auth_mode() == "email"


def test_config_route_reports_the_mode(monkeypatch):
    from router import auth as auth_router

    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)
    assert client.get("/auth/config").json() == {"success": True, "user_auth": "otp"}
    monkeypatch.setenv("USER_AUTH", "email")
    assert client.get("/auth/config").json() == {"success": True, "user_auth": "email"}


# ---------------------------------------------------------------------------- email mode: phone is refused, nothing sent
def test_generate_otp_rejects_a_phone_in_email_mode(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    no_db(monkeypatch)
    sms = []
    monkeypatch.setattr(af, "_send_msg91_sms", lambda m, o: sms.append(m) or True)
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="9000000003", purpose="login", role_id=3)))
    assert e.value.status_code == 400 and "email" in e.value.detail.lower()
    assert sms == []


def test_review_bypass_numbers_do_not_work_in_email_mode(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    monkeypatch.setenv("OTP_BYPASS", "1")  # even when enabled, the e-mail mode keeps them inert
    no_db(monkeypatch)
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="7904559310", purpose="login", role_id=1)))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        run(af.validate_otp(af.ValidateOTPRequest(contact="7904559310", otp_code="123456", purpose="login")))
    assert e.value.status_code == 400


def test_validate_otp_rejects_a_phone_in_email_mode(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    no_db(monkeypatch)
    with pytest.raises(HTTPException) as e:
        run(af.validate_otp(af.ValidateOTPRequest(contact="9000000003", otp_code="123456", purpose="login")))
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------- email mode: the happy path
def test_generate_otp_sends_an_email_and_stores_the_code(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    cur = FakeCursor(fetch=[{"n": 1}, {"user_id": "u1", "role_id": 3, "status": 1}, None])
    use_db(monkeypatch, cur)
    sent = []

    async def fake_send(email, otp, purpose):
        sent.append((email, otp, purpose))
        return True

    monkeypatch.setattr(af, "_send_email_otp_async", fake_send)
    monkeypatch.setattr(af, "_send_msg91_sms", lambda *a: pytest.fail("no SMS in email mode"))
    res = run(af.generate_otp(af.GenerateOTPRequest(contact="  Staff@Example.COM ", purpose="login", role_id=3)))
    assert res["success"] is True and "email" in res["message"].lower()
    assert sent and sent[0][0] == "staff@example.com" and sent[0][2] == "login"
    assert any("INSERT INTO email_otp_verification" in s for s, _ in cur.sql)
    assert not any("moblie_otp_verification" in s for s, _ in cur.sql)
    # the user is looked up by e-mail, not by mobile number
    lookup = [s for s, _ in cur.sql if "FROM users u" in s][0]
    assert "LOWER(u.email) = LOWER(%s)" in lookup and "mobile_number" not in lookup


def test_generate_otp_reports_a_failed_email_and_drops_the_code(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    cur = FakeCursor(fetch=[{"n": 1}, {"user_id": "u1", "role_id": 3, "status": 1}, None])
    use_db(monkeypatch, cur)

    async def failing_send(*a):
        return False

    monkeypatch.setattr(af, "_send_email_otp_async", failing_send)
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="staff@example.com", purpose="login", role_id=3)))
    assert e.value.status_code == 503
    assert sum("DELETE FROM email_otp_verification" in s for s, _ in cur.sql) == 2  # old code cleared + the unsent one


def test_unknown_employee_email_is_a_404(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    use_db(monkeypatch, FakeCursor(fetch=[{"n": 0}, None]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="nobody@example.com", purpose="login", role_id=3)))
    assert e.value.status_code == 404 and "email" in e.value.detail


def test_an_email_shared_by_two_accounts_is_refused(monkeypatch):
    monkeypatch.setenv("USER_AUTH", "email")
    use_db(monkeypatch, FakeCursor(fetch=[{"n": 2}]))
    with pytest.raises(HTTPException) as e:
        run(af.generate_otp(af.GenerateOTPRequest(contact="shared@example.com", purpose="login", role_id=3)))
    assert e.value.status_code == 409


def test_employee_and_admin_login_look_up_by_email(monkeypatch):
    cur = FakeCursor(fetch=[{"n": 1}, None, None])
    use_db(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.employee_login(af.EmployeeLoginRequest(login_id="Staff@Example.com")))
    assert e.value.status_code == 401
    assert all("mobile_number" not in s for s, _ in cur.sql)
    assert all(p == ("staff@example.com",) for s, p in cur.sql if "FROM users" in s and "COUNT" not in s)

    cur = FakeCursor(fetch=[{"n": 1}, None, None])
    use_db(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.admin_login(af.AdminLoginRequest(login_id="boss@example.com")))
    assert e.value.status_code == 401
    assert all("mobile_number" not in s for s, _ in cur.sql)


# ---------------------------------------------------------------------------- otp mode is unchanged
def test_otp_mode_still_sends_sms_for_a_phone(monkeypatch):
    cur = FakeCursor(fetch=[{"user_id": "u1", "role_id": 3, "status": 1}, None])
    use_db(monkeypatch, cur)
    sms = []
    monkeypatch.setattr(af, "_send_msg91_sms", lambda m, o: sms.append(m) or True)
    res = run(af.generate_otp(af.GenerateOTPRequest(contact="9000000003", purpose="login", role_id=3)))
    assert res["success"] is True and sms == ["9000000003"]
    assert any("INSERT INTO moblie_otp_verification" in s for s, _ in cur.sql)
    lookup = [s for s, _ in cur.sql if "FROM users u" in s][0]
    assert "u.mobile_number = %s" in lookup


def test_otp_mode_bypass_number_still_works(monkeypatch):
    monkeypatch.setenv("OTP_BYPASS", "1")
    no_db(monkeypatch)
    res = run(af.generate_otp(af.GenerateOTPRequest(contact="7904559310", purpose="login", role_id=1)))
    assert res["success"] is True
    # validate: the static code is accepted and the user is looked up (still no real DB)
    cur = FakeCursor(fetch=[{"user_id": "u1", "role_id": 1}])
    use_db(monkeypatch, cur)
    res = run(af.validate_otp(af.ValidateOTPRequest(contact="7904559310", otp_code="123456", purpose="login")))
    assert res["success"] is True and res["user_exists"] is True and res["device_token"]


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes "])
def test_bypass_flag_truthy_values(monkeypatch, value):
    monkeypatch.setenv("OTP_BYPASS", value)
    assert af._otp_bypass_enabled() is True


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "2"])
def test_bypass_flag_off_by_default_and_for_other_values(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("OTP_BYPASS", value)
    assert af._otp_bypass_enabled() is False


def test_bypass_numbers_are_refused_when_the_flag_is_off(monkeypatch):
    # default OFF: the number goes through the normal flow (real code, real SMS) ...
    cur = FakeCursor(fetch=[{"user_id": "u1", "role_id": 1, "status": 1}, None])
    use_db(monkeypatch, cur)
    sms = []
    monkeypatch.setattr(af, "_send_msg91_sms", lambda m, o: sms.append((m, o)) or True)
    res = run(af.generate_otp(af.GenerateOTPRequest(contact="7904559310", purpose="login", role_id=1)))
    assert res["success"] is True and sms and sms[0][0] == "7904559310"
    assert any("INSERT INTO moblie_otp_verification" in s for s, _ in cur.sql)
    # ... and the static code 123456 is just a wrong code (no stored OTP -> 400, device token untouched)
    cur = FakeCursor(fetch=[None])
    use_db(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.validate_otp(af.ValidateOTPRequest(contact="7904559310", otp_code="123456", purpose="login", push_token="x")))
    assert e.value.status_code == 400
    assert not any("UPDATE users" in s for s, _ in cur.sql)


def test_otp_mode_email_registration_still_fire_and_forget(monkeypatch):
    cur = FakeCursor(fetch=[None, None])
    use_db(monkeypatch, cur)
    called = []

    async def fake_send(email, otp, purpose):
        called.append(email)
        return False  # a failure is still swallowed in otp mode (legacy behaviour)

    monkeypatch.setattr(af, "_send_email_otp_async", fake_send)

    async def go():
        res = await af.generate_otp(af.GenerateOTPRequest(contact="new@example.com", purpose="registration"))
        await asyncio.sleep(0)  # let the background task run
        return res

    assert run(go())["success"] is True
    assert called == ["new@example.com"]
