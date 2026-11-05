"""DB-free tests of the device flow (WP7): device-change dedupe + rate limit, approval/rejection, POST /auth/logout,
verify-device-token / validate-otp with ENFORCE_SINGLE_DEVICE off (today) and on, and the session poll.
Run: python -m pytest tests/test_device_flow.py -q
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_function as af  # noqa: E402
from core_functions import requests_functions as rf  # noqa: E402
from wp7_fakes import RouteCursor, patch_db, placeholders_match, seq  # noqa: E402


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ENFORCE_SINGLE_DEVICE", raising=False)
    monkeypatch.delenv("REQUESTS_ENFORCE_ACTOR", raising=False)
    monkeypatch.setenv("USER_AUTH", "otp")                    # the OTP routes take phone numbers in this mode


# ---------------------------------------------------------------------------- submit: dedupe + rate limit
STAFF_ROW = ("stf-1", 3, 1, "org-1", None)       # user_id, role_id, status, parent_id, users.org_id


def submit_cursor(pending=None, recent=0, user=STAFF_ROW):
    cur = RouteCursor()
    cur.add("SELECT user_id, role_id, status, parent_id, org_id FROM users", one=user)
    cur.add("SELECT role_id, parent_id FROM users", one=(3, "org-1"))
    cur.add("SELECT request_id FROM requests", one=pending)
    cur.add("SELECT COUNT(*) FROM requests", one=(recent,))
    return cur


@pytest.fixture
def notified(monkeypatch):
    calls = {"ws": [], "push": []}
    monkeypatch.setattr(rf, "_ws_notify_request", lambda *a, **k: calls["ws"].append((a, k)))
    monkeypatch.setattr(rf, "_notify_request_submitted", lambda *a: calls["push"].append(a))
    return calls


def test_first_device_change_request_is_created_and_notified(monkeypatch, notified):
    cur = submit_cursor()
    conn = patch_db(monkeypatch, rf, cur)
    res = rf.submit_device_change_request("stf-1", "org-1", "New phone")
    assert res["success"] is True and res["already_pending"] is False and res["request_id"]
    sql, params = cur.find("INSERT INTO requests")[0]
    assert "'device_change'" in sql and "'pending'" in sql
    assert params == ("stf-1", "org-1", res["request_id"], "New phone") and placeholders_match(sql, params)
    assert conn.commits == 1
    assert len(notified["ws"]) == 1 and notified["ws"][0][1]["request_type"] == "device_change"
    assert notified["push"] == [("stf-1", "org-1", res["request_id"], "device_change")]
    assert cur.has("pg_advisory_xact_lock")                                  # concurrent submits serialise


def test_second_attempt_returns_the_existing_request_without_a_new_row(monkeypatch, notified):
    cur = submit_cursor(pending=("req-existing",))
    patch_db(monkeypatch, rf, cur)
    res = rf.submit_device_change_request("stf-1", "org-1", "again")
    assert res == {"success": True, "request_id": "req-existing", "already_pending": True,
                   "message": "A device change request is already pending"}
    assert not cur.has("INSERT INTO requests")
    assert notified["ws"] == [] and notified["push"] == []                   # admins are notified once only
    sql, params = cur.find("SELECT request_id FROM requests")[0]
    assert "status = 'pending'" in sql and "request_type = 'device_change'" in sql
    assert params == ("stf-1", "org-1")


def test_three_mismatching_calls_create_one_row(monkeypatch, notified):
    """The login flow calls submit on every mismatching check: only the first creates a request."""
    state = {"pending": None}

    def pending(params):
        return state["pending"]

    cur = RouteCursor()
    cur.add("SELECT user_id, role_id, status, parent_id, org_id FROM users", one=STAFF_ROW)
    cur.add("SELECT request_id FROM requests", one=pending)
    cur.add("SELECT COUNT(*) FROM requests", one=(0,))
    patch_db(monkeypatch, rf, cur)
    ids = []
    for _ in range(3):
        res = rf.submit_device_change_request("stf-1", "org-1", "x")
        ids.append(res["request_id"])
        state["pending"] = (res["request_id"],)
    assert len(set(ids)) == 1 and len(cur.find("INSERT INTO requests")) == 1


@pytest.mark.parametrize("recent, limited", [(0, False), (2, False), (3, True), (7, True)])
def test_rate_limit_is_three_per_user_per_24_hours(monkeypatch, notified, recent, limited):
    cur = submit_cursor(recent=recent)
    conn = patch_db(monkeypatch, rf, cur)
    if limited:
        with pytest.raises(HTTPException) as exc:
            rf.submit_device_change_request("stf-1", "org-1", "x")
        assert exc.value.status_code == 429
        assert exc.value.detail == "Too many device change requests. Please try again later."
        assert not cur.has("INSERT INTO requests") and conn.rollbacks == 1
        assert notified["push"] == []
    else:
        assert rf.submit_device_change_request("stf-1", "org-1", "x")["success"] is True
    sql, params = cur.find("SELECT COUNT(*) FROM requests")[0]
    assert "created_at > now() - interval '24 hours'" in sql and "request_type = 'device_change'" in sql
    assert params == ("stf-1",) and placeholders_match(sql, params)


def test_unknown_user_is_404_and_foreign_org_is_400(monkeypatch, notified):
    patch_db(monkeypatch, rf, submit_cursor(user=None))
    with pytest.raises(HTTPException) as exc:
        rf.submit_device_change_request("ghost", "org-1", "x")
    assert exc.value.status_code == 404 and exc.value.detail == "User not found"

    patch_db(monkeypatch, rf, submit_cursor())
    with pytest.raises(HTTPException) as exc:
        rf.submit_device_change_request("stf-1", "org-OTHER", "x")
    assert exc.value.status_code == 400 and exc.value.detail == "User does not belong to this organization"


def test_empty_org_id_is_resolved_from_the_user(monkeypatch, notified):
    cur = submit_cursor()
    patch_db(monkeypatch, rf, cur)
    res = rf.submit_device_change_request("stf-1", "", "")
    assert res["success"] is True
    assert cur.find("INSERT INTO requests")[0][1][1] == "org-1"
    assert cur.find("INSERT INTO requests")[0][1][3] == "Device change requested"      # blank reason gets the default


# ---------------------------------------------------------------------------- approval / rejection
def decide_cursor(status="pending", request_type="device_change"):
    cur = RouteCursor()
    cur.add("FROM requests r LEFT JOIN users u", one=(request_type, "org-1", "stf-1", status, None, None))
    return cur


@pytest.fixture
def push_calls(monkeypatch):
    from services import push_service
    cleared = []
    monkeypatch.setattr(push_service, "clear_user_tokens", lambda uid: cleared.append(uid))
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    return cleared


def test_approval_clears_the_device_token_and_every_push_token(monkeypatch, push_calls):
    cur = decide_cursor()
    conn = patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("req-1", "approved", "adm", 1)["success"] is True
    sql, params = cur.find("UPDATE users SET")[0]
    assert "device_token = NULL" in sql and "push_token = NULL" in sql and "device_token_expires_at = NULL" in sql
    assert params == ("stf-1",) and placeholders_match(sql, params)
    assert push_calls == ["stf-1"]                                   # the multi-device table is cleared too (once)
    assert conn.commits == 1


def test_rejection_keeps_the_token(monkeypatch, push_calls):
    cur = decide_cursor()
    patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("req-1", "rejected", "adm", 1)["success"] is True
    assert not cur.has("UPDATE users SET") and push_calls == []
    assert cur.find("UPDATE requests")[0][1][0] == "rejected"


def test_a_repeated_approval_does_not_sign_the_new_device_out(monkeypatch, push_calls):
    cur = decide_cursor(status="approved")
    patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("req-1", "approved", "adm", 1)["success"] is True
    assert not cur.has("UPDATE users SET") and push_calls == []


# ---------------------------------------------------------------------------- session poll
def session_cursor(token, status=1, role=3):
    cur = RouteCursor()
    cur.add("SELECT device_token, status, role_id FROM users", one=(token, status, role))
    return cur


@pytest.mark.parametrize("flag, role, stored, supplied, expected", [
    (False, 3, "A", "A", {"valid": True, "force_logout": False}),
    (False, 3, "A", "B", {"valid": False, "force_logout": True, "reason": "logged_in_on_another_device"}),
    (False, 3, None, "A", {"valid": False, "force_logout": True, "reason": "device_change_approved"}),
    (False, 1, "A", "B", {"valid": False, "force_logout": True, "reason": "logged_in_on_another_device"}),  # today
    (True, 1, "A", "B", {"valid": True, "force_logout": False}),            # admins are multi-device when enforced
    (True, 1, None, "B", {"valid": True, "force_logout": False}),
    (True, 3, "A", "B", {"valid": False, "force_logout": True, "reason": "logged_in_on_another_device"}),
    (True, 3, None, "B", {"valid": False, "force_logout": True, "reason": "device_change_approved"}),
])
def test_check_device_session(monkeypatch, flag, role, stored, supplied, expected):
    if flag:
        monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    patch_db(monkeypatch, rf, session_cursor(stored, role=role))
    assert rf.check_device_session("u1", supplied) == expected


def test_inactive_or_unknown_accounts_are_signed_out_even_for_admins(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    patch_db(monkeypatch, rf, session_cursor("A", status=0, role=1))
    assert rf.check_device_session("u1", "A")["reason"] == "account_inactive"
    cur = RouteCursor().add("SELECT device_token, status, role_id FROM users", one=None)
    patch_db(monkeypatch, rf, cur)
    assert rf.check_device_session("u1", "A")["reason"] == "user_not_found"


# ---------------------------------------------------------------------------- logout
def logout(monkeypatch, body, fail=False):
    cur = RouteCursor()
    conn = patch_db(monkeypatch, af, cur)
    if fail:
        def boom(sql, params=None):
            raise RuntimeError("db down")
        cur.execute = boom
    return af.logout_user(af.LogoutRequest(**body)), cur, conn


def test_logout_clears_the_matching_device_and_the_given_push_token(monkeypatch):
    res, cur, conn = logout(monkeypatch, {"user_id": "u1", "device_token": "dev-A", "push_token": "tok-1"})
    assert res == {"success": True} and conn.commits == 1
    sql, params = cur.find("UPDATE users SET device_token = NULL")[0]
    assert "AND device_token = %s" in sql and params == ("u1", "dev-A") and placeholders_match(sql, params)
    assert "device_token_expires_at = NULL" in sql
    sql, params = cur.find("UPDATE users SET push_token = NULL")[0]
    assert "push_token = %s" in sql and params == ("u1", "tok-1")
    sql, params = cur.find("DELETE FROM user_push_tokens")[0]
    assert params == ("u1", "tok-1") and placeholders_match(sql, params)       # only this device's token goes


def test_logout_without_a_device_token_clears_the_stored_one(monkeypatch):
    res, cur, _ = logout(monkeypatch, {"user_id": "u1"})
    assert res == {"success": True}
    sql, params = cur.find("UPDATE users SET device_token = NULL")[0]
    assert "AND device_token" not in sql and params == ("u1",)
    assert not cur.has("push_token") and not cur.has("user_push_tokens")


def test_logout_is_idempotent_and_never_fails(monkeypatch):
    for _ in range(2):
        assert logout(monkeypatch, {"user_id": "ghost", "device_token": "x"})[0] == {"success": True}
    res, _cur, conn = logout(monkeypatch, {"user_id": "u1", "device_token": "x"}, fail=True)
    assert res == {"success": True} and conn.rollbacks == 1
    assert af.logout_user(af.LogoutRequest(user_id="  "))["success"] is True


def test_logout_route_always_answers_200(monkeypatch):
    from router import auth as auth_router
    patch_db(monkeypatch, af, RouteCursor())
    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)
    r = client.post("/auth/logout", json={"user_id": "u1", "device_token": "d", "push_token": "t"})
    assert r.status_code == 200 and r.json() == {"success": True}
    r = client.post("/auth/logout", json={"user_id": "u1"})
    assert r.status_code == 200 and r.json() == {"success": True}
    assert client.post("/auth/logout", json={}).status_code == 422             # user_id is required


# ---------------------------------------------------------------------------- verify-device-token
def verify_cursor(role, stored):
    cur = RouteCursor()
    cur.add("FROM users u LEFT JOIN org_info o", one={
        "user_id": "stf-1", "role_id": role, "device_token": stored, "device_token_expires_at": None,
        "name": "S", "email": "s@x.in", "org_id": "org-1", "status": 1, "org_name": "Org"})
    return cur


def verify(monkeypatch, role, stored, filed, device="dev-NEW"):
    patch_db(monkeypatch, af, verify_cursor(role, stored))
    calls = []
    monkeypatch.setattr(af, "_file_device_access_request", lambda uid, org: calls.append((uid, org)) or filed)
    req = af.DeviceTokenVerifyRequest(contact="9000000003", device_token=device, role_id=role)
    return run(af.verify_device_token(req)), calls


OLD_MESSAGE = "This account is already logged in on another device. Please log in again with OTP to take over the session."
NEW_MESSAGE = "This account is active on another device. Request access from your admin."


def test_mismatch_default_keeps_todays_message_and_flags_the_pending_request(monkeypatch):
    res, calls = verify(monkeypatch, 3, "dev-OLD", {"success": True, "request_id": "r1"})
    assert res["device_mismatch"] is True and res["request_pending"] is True
    assert res["message"] == OLD_MESSAGE and res["skip_otp"] is False
    assert res["user_id"] == "stf-1" and res["org_id"] == "org-1"
    assert calls == [("stf-1", "org-1")]


def test_mismatch_without_a_filed_request_is_not_pending(monkeypatch):
    res, _ = verify(monkeypatch, 3, "dev-OLD", None)                         # e.g. rate limited
    assert res["device_mismatch"] is True and res["request_pending"] is False


def test_mismatch_with_enforcement_uses_the_request_access_message(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    res, calls = verify(monkeypatch, 3, "dev-OLD", {"success": True, "request_id": "r1", "already_pending": True})
    assert res["message"] == NEW_MESSAGE and res["device_mismatch"] is True and res["request_pending"] is True
    assert calls == [("stf-1", "org-1")]


def test_admin_mismatch_default_still_files_a_request_as_today(monkeypatch):
    res, calls = verify(monkeypatch, 1, "dev-OLD", {"success": True})
    assert res["device_mismatch"] is True and res["message"] == OLD_MESSAGE
    assert calls == [("stf-1", "org-1")]


def test_admin_mismatch_with_enforcement_files_nothing(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    res, calls = verify(monkeypatch, 1, "dev-OLD", {"success": True})
    assert calls == [] and res["request_pending"] is False and res["skip_otp"] is False
    assert res["device_mismatch"] is True and "OTP" in res["message"]


def test_cleared_token_asks_for_an_otp_login(monkeypatch):
    res, calls = verify(monkeypatch, 3, None, None)
    assert res["device_change_approved"] is True and res["skip_otp"] is False and calls == []


# ---------------------------------------------------------------------------- validate-otp
OTP_ROW = {"attempts": 0, "otp_code": 123456}


def otp_cursor(role, stored, expires=None):
    cur = RouteCursor()
    cur.add("FROM moblie_otp_verification", one=OTP_ROW)
    cur.add("FROM users WHERE users.mobile_number", one={"user_id": "stf-1", "role_id": role})
    cur.add("SELECT device_token, device_token_expires_at, parent_id FROM users",
            one={"device_token": stored, "device_token_expires_at": expires, "parent_id": "org-1"})
    cur.add("SELECT device_token, push_token FROM users", one={"device_token": stored, "push_token": None})
    return cur


def validate(monkeypatch, role, stored, device="dev-NEW", expires=None, purpose="login"):
    cur = otp_cursor(role, stored, expires)
    conn = patch_db(monkeypatch, af, cur)
    filed = []
    signalled = []

    async def fake_signal(user_id, previous):
        signalled.append((user_id, previous))

    monkeypatch.setattr(af, "_signal_previous_device", fake_signal)
    monkeypatch.setattr(af, "_file_device_access_request", lambda uid, org: filed.append((uid, org)) or {"success": True})
    req = af.ValidateOTPRequest(contact="9000000003", otp_code="123456", purpose=purpose, device_token=device)
    return req, cur, conn, filed, signalled


def test_default_otp_login_takes_the_session_over_as_today(monkeypatch):
    req, cur, conn, filed, signalled = validate(monkeypatch, 3, "dev-OLD")
    res = run(af.validate_otp(req))
    assert res["success"] is True and res["device_token"] == "dev-NEW"
    assert cur.has("UPDATE users SET device_token") and filed == [] and len(signalled) == 1
    assert signalled[0][1]["takeover"] is True and signalled[0][1]["skip_sse"] is False


@pytest.mark.parametrize("role", [2, 3])
def test_enforced_login_from_a_new_device_is_refused_and_a_request_is_filed(monkeypatch, role):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    req, cur, conn, filed, signalled = validate(monkeypatch, role, "dev-OLD")
    with pytest.raises(HTTPException) as exc:
        run(af.validate_otp(req))
    assert exc.value.status_code == 409 and exc.value.detail == "device_mismatch"
    assert not cur.has("UPDATE users SET device_token")                       # the active device keeps its session
    assert filed == [("stf-1", "org-1")] and signalled == []
    assert conn.commits >= 1                                                  # the consumed OTP stays consumed


def test_enforced_login_from_the_same_device_works(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    req, cur, _conn, filed, _ = validate(monkeypatch, 3, "dev-SAME", device="dev-SAME")
    assert run(af.validate_otp(req))["success"] is True and filed == []


def test_enforced_login_after_an_approved_device_change_works(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    req, cur, _conn, filed, _ = validate(monkeypatch, 3, None)                # token cleared by the approval
    res = run(af.validate_otp(req))
    assert res["success"] is True and cur.has("UPDATE users SET device_token") and filed == []


def test_enforced_login_over_an_expired_session_works(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    req, cur, _conn, filed, _ = validate(monkeypatch, 3, "dev-OLD", expires=expired)
    assert run(af.validate_otp(req))["success"] is True and filed == []


def test_enforced_admin_login_from_a_second_device_is_allowed_and_ends_no_session(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    req, cur, _conn, filed, signalled = validate(monkeypatch, 1, "dev-OLD")
    assert run(af.validate_otp(req))["success"] is True and filed == []
    assert signalled[0][1]["takeover"] is False and signalled[0][1]["skip_sse"] is True


def test_enforcement_only_applies_to_the_login_purpose(monkeypatch):
    monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", "1")
    req, cur, _conn, filed, _ = validate(monkeypatch, 3, "dev-OLD", purpose="update_email")
    assert run(af.validate_otp(req))["success"] is True and filed == []


def test_signal_previous_device_is_skipped_for_multi_device_admins(monkeypatch):
    called = []
    import core_functions.session_registry as reg

    async def fake_sse(uid):
        called.append(uid)

    monkeypatch.setattr(reg, "force_logout_sse", fake_sse)
    run(af._signal_previous_device("u1", {"takeover": False, "old_tokens": [], "skip_sse": True}))
    assert called == []
    run(af._signal_previous_device("u1", {"takeover": False, "old_tokens": []}))
    assert called == ["u1"]                                                   # today's legacy signal is unchanged


# ---------------------------------------------------------------------------- the helper used by both flows
def test_file_device_access_request_never_raises(monkeypatch):
    monkeypatch.setattr(rf, "submit_device_change_request",
                        lambda *a: (_ for _ in ()).throw(HTTPException(status_code=429, detail="x")))
    assert af._file_device_access_request("u1", "org-1") is None
    monkeypatch.setattr(rf, "submit_device_change_request", lambda uid, org, reason: {"success": True, "reason": reason})
    assert af._file_device_access_request("u1", "org-1") == {"success": True, "reason": "Device change requested"}


def test_has_pending_device_change(monkeypatch):
    cur = RouteCursor().add("SELECT 1 FROM requests", one=(1,))
    patch_db(monkeypatch, rf, cur)
    assert rf.has_pending_device_change("u1") is True
    sql, params = cur.sql[0]
    assert "status = 'pending'" in sql and params == ("u1",)
    patch_db(monkeypatch, rf, RouteCursor())
    assert rf.has_pending_device_change("u1") is False
