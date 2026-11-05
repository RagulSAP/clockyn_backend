"""HTTP-level tests of the /requests router (WP7): the exact error strings and status codes the app's contract mock
(e2e/mock-server/routes/requests.js) produces, success shapes, org scoping, honest 500s and the calendar fields.
The DB layer is the scripted RouteCursor; notifications are stubbed.
Run: python -m pytest tests/test_requests_routes.py -q
"""
import sys
from datetime import date, time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import requests_functions as rf  # noqa: E402
from router import requests as requests_router  # noqa: E402
from wp7_fakes import RouteCursor, patch_db, placeholders_match  # noqa: E402

ORG = "org-1"
TODAY = date(2026, 11, 2)
STAFF = ("stf-1", 3, 1, ORG, None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(rf, "ist_today", lambda: TODAY)
    monkeypatch.setattr(rf, "_ws_notify_request", lambda *a, **k: None)
    monkeypatch.setattr(rf, "_notify_request_submitted", lambda *a: None)
    app = FastAPI()
    app.include_router(requests_router.router)
    return TestClient(app)


def base_cursor(user=STAFF):
    cur = RouteCursor()
    cur.add("SELECT user_id, role_id, status, parent_id, org_id FROM users", one=user)
    return cur


def post(client, path, **body):
    return client.post(path, json=body)


def detail(resp):
    return resp.json()["detail"]


# ---------------------------------------------------------------------------- leave
def leave(client, **over):
    body = dict(user_id="stf-1", org_id=ORG, leave_dates=["2026-11-04", "2026-11-05"], reason="Family function")
    body.update(over)
    return post(client, "/requests/leave/submit", **body)


def test_leave_success_shape_status_pending_and_one_row_per_date(client, monkeypatch):
    cur = base_cursor()
    conn = patch_db(monkeypatch, rf, cur)
    r = leave(client, leave_dates=["2026-11-05", "2026-11-04", "2026-11-05"])
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and body["message"] == "Leave request submitted successfully" and body["request_id"]
    sql, params = cur.find("INSERT INTO requests")[0]
    assert "'leave'" in sql and "'pending'" in sql and "NULL" not in sql        # no more NULL leave status
    assert params == ("stf-1", ORG, body["request_id"], "Family function") and placeholders_match(sql, params)
    rows = cur.find("INSERT INTO leave_dates")
    assert [p[1] for _s, p in rows] == [date(2026, 11, 4), date(2026, 11, 5)]    # sorted, de-duplicated
    assert conn.commits == 1


@pytest.mark.parametrize("over, status, message", [
    ({"leave_dates": []}, 400, "Select at least one leave date"),
    ({"reason": "   "}, 400, "Reason is required"),
    ({"leave_dates": ["2026-11-01"]}, 400, "Leave dates cannot be in the past"),
    ({"leave_dates": ["31-12-2026"]}, 400, "Invalid leave date"),
    ({"reason": "x" * 501}, 400, "Reason is too long (maximum 500 characters)"),
])
def test_leave_validation_errors_use_the_mock_strings(client, monkeypatch, over, status, message):
    cur = base_cursor()
    conn = patch_db(monkeypatch, rf, cur)
    r = leave(client, **over)
    assert r.status_code == status and detail(r) == message
    assert not cur.has("INSERT") and conn.commits == 0


def test_leave_user_not_found_and_foreign_org(client, monkeypatch):
    patch_db(monkeypatch, rf, base_cursor(user=None))
    r = leave(client)
    assert r.status_code == 404 and detail(r) == "User not found"
    patch_db(monkeypatch, rf, base_cursor())
    r = leave(client, org_id="org-OTHER")
    assert r.status_code == 400 and detail(r) == "User does not belong to this organization"


def test_leave_overlap_with_pending_or_approved_leave_is_a_409(client, monkeypatch):
    cur = base_cursor()
    cur.add("FROM leave_dates ld JOIN requests r", one=(1,))
    conn = patch_db(monkeypatch, rf, cur)
    r = leave(client)
    assert r.status_code == 409 and detail(r) == "A leave request already exists for one of the selected dates"
    assert not cur.has("INSERT") and conn.rollbacks == 1
    sql, params = cur.find("FROM leave_dates ld JOIN requests r")[0]
    # same user and org, leave only, rejected dates may be requested again, the requested dates are the filter
    assert "r.user_id = %s AND r.org_id = %s" in sql and "r.request_type = 'leave'" in sql
    assert "ld.leave_date = ANY(%s)" in sql and "COALESCE(ld.status, 'pending') <> 'rejected'" in sql
    assert params == ("stf-1", ORG, [date(2026, 11, 4), date(2026, 11, 5)]) and placeholders_match(sql, params)


def test_leave_without_overlap_goes_through(client, monkeypatch):
    cur = base_cursor()
    cur.add("FROM leave_dates ld JOIN requests r", one=None)
    patch_db(monkeypatch, rf, cur)
    assert leave(client).status_code == 200


# ---------------------------------------------------------------------------- permission
def permission(client, **over):
    body = dict(user_id="stf-1", org_id=ORG, permission_date="2026-11-05", start_time="09:00", end_time="12:00",
                reason="Bank work")
    body.update(over)
    return post(client, "/requests/permission/submit", **body)


def test_permission_success_stores_clock_times_and_the_date(client, monkeypatch):
    cur = base_cursor()
    patch_db(monkeypatch, rf, cur)
    r = permission(client, start_time="09:00 AM", end_time="03:00 PM")                 # exactly 6 hours
    assert r.status_code == 200 and r.json()["message"] == "Permission request submitted successfully"
    sql, params = cur.find("INSERT INTO requests")[0]
    assert params[3:] == ("Bank work", "09:00", "15:00") and placeholders_match(sql, params)
    sql, params = cur.find("INSERT INTO leave_dates")[0]
    assert params[1] == date(2026, 11, 5)


@pytest.mark.parametrize("over, status, message", [
    ({"end_time": "09:00"}, 400, "End time must be after start time"),
    ({"end_time": "08:00"}, 400, "End time must be after start time"),
    ({"end_time": "15:01"}, 400, "End time cannot be more than 6 hours after start time"),
    ({"permission_date": ""}, 400, "permission_date, start_time and end_time are required"),
    ({"start_time": "soon"}, 400, "permission_date, start_time and end_time are required"),
    ({"reason": ""}, 400, "Reason is required"),
    ({"permission_date": "2026-11-01"}, 400, "Permission date cannot be in the past"),
])
def test_permission_validation_errors_use_the_mock_strings(client, monkeypatch, over, status, message):
    cur = base_cursor()
    conn = patch_db(monkeypatch, rf, cur)
    r = permission(client, **over)
    assert r.status_code == status and detail(r) == message
    assert not cur.has("INSERT") and conn.commits == 0


# ---------------------------------------------------------------------------- shift change
def shift_change(client, **over):
    body = dict(user_id="stf-1", org_id=ORG, reason="Exam", requested_shift_id=2, shift_change_date="2026-11-10")
    body.update(over)
    return post(client, "/requests/shift-change/submit", **body)


def shift_cursor(shift_exists=True, current=1, duplicate=False):
    cur = base_cursor()
    cur.add("SELECT 1 FROM shift WHERE", one=(1,) if shift_exists else None)
    cur.add("SELECT ua.shift_id FROM users_additional_info", one=(current,) if current is not None else None)
    cur.add("request_type = 'shift_change' AND shift_change_date = %s", one=(1,) if duplicate else None)
    return cur


def test_shift_change_success_snapshots_the_current_shift(client, monkeypatch):
    cur = shift_cursor()
    patch_db(monkeypatch, rf, cur)
    r = shift_change(client)
    assert r.status_code == 200 and r.json()["message"] == "Shift change request submitted successfully"
    sql, params = cur.find("INSERT INTO requests")[0]
    assert params[3:] == ("Exam", 2, 1, date(2026, 11, 10)) and placeholders_match(sql, params)
    sql, params = cur.find("SELECT 1 FROM shift WHERE")[0]
    assert "org_id = %s" in sql and "status = 1" in sql and params == (2, ORG)         # the shift is org scoped


def test_shift_change_unknown_shift_is_a_400(client, monkeypatch):
    cur = shift_cursor(shift_exists=False)
    patch_db(monkeypatch, rf, cur)
    r = shift_change(client)
    assert r.status_code == 400 and detail(r) == "Requested shift not found" and not cur.has("INSERT")


def test_shift_change_to_the_current_shift_is_refused(client, monkeypatch):
    cur = shift_cursor(current=2)
    patch_db(monkeypatch, rf, cur)
    r = shift_change(client)
    assert r.status_code == 409 and detail(r) == "You are already on this shift" and not cur.has("INSERT")


def test_shift_change_duplicate_pending_request_for_the_date_is_refused(client, monkeypatch):
    cur = shift_cursor(duplicate=True)
    patch_db(monkeypatch, rf, cur)
    r = shift_change(client)
    assert r.status_code == 409 and detail(r) == "A shift change request already exists for this date"


@pytest.mark.parametrize("over, message", [
    ({"reason": ""}, "Reason is required"),
    ({"shift_change_date": "2026-11-01"}, "Shift change date cannot be in the past"),
    ({"shift_change_date": "tomorrow"}, "Invalid shift change date"),
])
def test_shift_change_shape_errors(client, monkeypatch, over, message):
    patch_db(monkeypatch, rf, shift_cursor())
    r = shift_change(client, **over)
    assert r.status_code == 400 and detail(r) == message


# ---------------------------------------------------------------------------- device change
def test_device_change_rate_limit_is_a_429_with_a_clear_message(client, monkeypatch):
    cur = base_cursor()
    cur.add("SELECT request_id FROM requests", one=None)
    cur.add("SELECT COUNT(*) FROM requests", one=(3,))
    patch_db(monkeypatch, rf, cur)
    r = post(client, "/requests/device-change/submit", user_id="stf-1", org_id=ORG, reason="New phone")
    assert r.status_code == 429 and detail(r) == "Too many device change requests. Please try again later."


def test_device_change_second_call_returns_the_existing_request(client, monkeypatch):
    cur = base_cursor()
    cur.add("SELECT request_id FROM requests", one=("req-301",))
    patch_db(monkeypatch, rf, cur)
    r = post(client, "/requests/device-change/submit", user_id="stf-1", org_id=ORG, reason="again")
    assert r.status_code == 200
    assert r.json() == {"success": True, "request_id": "req-301", "already_pending": True,
                        "message": "A device change request is already pending"}


# ---------------------------------------------------------------------------- update-status over HTTP
def test_update_status_invalid_status_and_unknown_request(client, monkeypatch):
    r = post(client, "/requests/update-status", request_id="r", status="maybe", action_by="adm", role_id=1)
    assert r.status_code == 400 and detail(r) == "Invalid status"
    patch_db(monkeypatch, rf, RouteCursor().add("FROM requests r LEFT JOIN users u", one=None))
    r = post(client, "/requests/update-status", request_id="r", status="approved", action_by="adm", role_id=1)
    assert r.status_code == 200 and r.json() == {"success": False, "message": "Request not found"}


def test_update_status_403_when_enforced(client, monkeypatch):
    monkeypatch.setenv("REQUESTS_ENFORCE_ACTOR", "1")
    cur = RouteCursor().add("FROM requests r LEFT JOIN users u", one=("leave", ORG, "stf-1", "pending", None, None))
    cur.add("SELECT user_id, role_id, status, parent_id FROM users WHERE user_id", one=("stf-9", 3, 1, ORG))
    patch_db(monkeypatch, rf, cur)
    r = post(client, "/requests/update-status", request_id="r", status="approved", action_by="stf-9", role_id=3)
    assert r.status_code == 403 and detail(r) == "Not allowed to decide this request"


# ---------------------------------------------------------------------------- honest 500s
def test_unexpected_errors_are_a_generic_500_without_the_raw_message(client, monkeypatch):
    def boom():
        raise RuntimeError("connection to secret-host failed")
    monkeypatch.setattr(rf, "get_db_connection", boom)
    for r in (leave(client), client.get("/requests/employee/stf-1/org-1"), client.get("/requests/admin/org-1")):
        assert r.status_code == 500 and detail(r) == "Internal server error"
        assert "secret-host" not in r.text


# ---------------------------------------------------------------------------- calendar
def calendar_cursor(holidays=(), off_days=(6,), overrides=()):
    cur = RouteCursor()
    cur.add("FROM leave_dates ld JOIN requests r", many=[(date(2026, 11, 4), "approved", "leave"),
                                                         (date(2026, 11, 12), "pending", "permission")])
    cur.add("FROM users u LEFT JOIN branch_info b", one=("Main", "Sales"))
    cur.add("FROM org_holidays", many=list(holidays))
    cur.add("SELECT off_days FROM org_off_days", one=(list(off_days),))
    cur.add("FROM requests r JOIN shift s", many=list(overrides))
    return cur


def test_calendar_keeps_data_and_adds_holidays_weekly_offs_and_overrides(client, monkeypatch):
    cur = calendar_cursor(holidays=[(date(2026, 11, 14), "Diwali", True)],
                          overrides=[(date(2026, 11, 9), 2, time(22, 0), time(6, 0))])
    patch_db(monkeypatch, rf, cur)
    r = client.get("/requests/calendar/stf-1/org-1", params={"start_date": "2026-11-01", "end_date": "2026-11-30"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"] == [{"date": "2026-11-04", "status": "approved", "type": "leave"},
                            {"date": "2026-11-12", "status": "pending", "type": "permission"}]    # unchanged contract
    assert body["holidays"] == [{"date": "2026-11-14", "name": "Diwali", "block_attendance": True}]
    assert body["weekly_offs"] == ["2026-11-01", "2026-11-08", "2026-11-15", "2026-11-22", "2026-11-29"]
    assert body["off_days"] == [6]
    assert body["shift_overrides"] == [{"date": "2026-11-09", "shift_id": 2, "shift_timing": "10:00 PM - 06:00 AM"}]
    sql, params = cur.find("FROM org_holidays")[0]
    assert "org_id = %s" in sql and "branch_name IS NULL OR branch_name = %s" in sql and "dept_name IS NULL OR dept_name = %s" in sql
    assert params == (ORG, date(2026, 11, 1), date(2026, 11, 30), "Main", "Sales") and placeholders_match(sql, params)
    sql, params = cur.find("FROM leave_dates ld JOIN requests r")[0]
    assert params == ("stf-1", ORG, date(2026, 11, 1), date(2026, 11, 30))
    assert "b.org_id = %s" in cur.find("FROM users u LEFT JOIN branch_info b")[0][0]           # branch lookup is org scoped


def test_calendar_include_off_days_appends_typed_rows_to_data(client, monkeypatch):
    cur = calendar_cursor(holidays=[(date(2026, 11, 14), "Diwali", True)], off_days=(5, 6))
    patch_db(monkeypatch, rf, cur)
    r = client.get("/requests/calendar/stf-1/org-1",
                   params={"start_date": "2026-11-01", "end_date": "2026-11-08", "include_off_days": "true"})
    rows = r.json()["data"]
    assert {"date": "2026-11-01", "status": "approved", "type": "weekly_off"} in rows
    assert {"date": "2026-11-07", "status": "approved", "type": "weekly_off"} in rows
    assert {"date": "2026-11-04", "status": "approved", "type": "leave"} in rows
    assert [x["date"] for x in rows] == sorted(x["date"] for x in rows)
    # without the flag (the default) the app never sees a holiday / weekly_off type
    patch_db(monkeypatch, rf, calendar_cursor(off_days=(5, 6)))
    r = client.get("/requests/calendar/stf-1/org-1", params={"start_date": "2026-11-01", "end_date": "2026-11-08"})
    assert {x["type"] for x in r.json()["data"]} <= {"leave", "permission"}


def test_calendar_defaults_to_sunday_when_no_off_days_are_configured(client, monkeypatch):
    cur = calendar_cursor()
    cur.routes = [(n, s) for n, s in cur.routes if n != "SELECT off_days FROM org_off_days"]
    cur.add("SELECT off_days FROM org_off_days", one=None)
    patch_db(monkeypatch, rf, cur)
    r = client.get("/requests/calendar/stf-1/org-1", params={"start_date": "2026-11-01", "end_date": "2026-11-08"})
    assert r.json()["off_days"] == [6] and r.json()["weekly_offs"] == ["2026-11-01", "2026-11-08"]


def test_calendar_bad_range_is_a_400(client, monkeypatch):
    patch_db(monkeypatch, rf, calendar_cursor())
    r = client.get("/requests/calendar/stf-1/org-1", params={"start_date": "2026-11-30", "end_date": "2026-11-01"})
    assert r.status_code == 400 and detail(r) == "end_date must not be before start_date"
    r = client.get("/requests/calendar/stf-1/org-1", params={"start_date": "nope", "end_date": "2026-11-01"})
    assert r.status_code == 400 and detail(r) == "Invalid start_date"
    assert client.get("/requests/calendar/stf-1/org-1").status_code == 422             # both dates are required
