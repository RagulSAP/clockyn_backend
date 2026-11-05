"""DB-free tests for the quick-win fixes (planner section 4). The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_quick_wins.py -q
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeCursor:
    """fetchone() / fetchall() hand out the scripted values in order; every statement is recorded."""

    def __init__(self, one=None, many=None):
        self.sql = []
        self.one = list(one or [])
        self.many = list(many or [])

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.many.pop(0) if self.many else []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        self.rollbacks += 1


def run(coro):
    return asyncio.run(coro)


def patch_db(monkeypatch, module, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


# ---------------------------------------------------------------------------- (2) message_id in chat pushes
def test_chat_pushes_carry_the_message_id(monkeypatch):
    from router import chat

    pushes = []

    async def fake_push(user_id, title, body, data=None, image_url=None, silent=False):
        pushes.append((user_id, data))
        return 1

    async def fake_send(req):
        return {"message_id": "m-1", "sender_name": "Asha"}

    async def noop(*a, **k):
        return None

    # the push now fans out per user (every device): the seam is push_service.send_push_to_user
    monkeypatch.setattr(chat.push_service, "send_push_to_user", fake_push)
    monkeypatch.setattr(chat, "send_chat_message", fake_send)
    monkeypatch.setattr(chat.shared_manager, "broadcast_chat_message", noop)

    async def go():
        req = chat.SendMessageRequest(sender_id="s1", receiver_id="r1", message="hi", message_type="text")
        res = await chat.chat_send_route(req)
        await asyncio.sleep(0)  # let the push task run
        return res

    res = run(go())
    assert res["message_id"] == "m-1"
    assert pushes == [("r1", {"type": "new_message", "sender_id": "s1", "receiver_id": "r1", "message_id": "m-1"})]


def test_group_chat_pushes_carry_the_message_id(monkeypatch):
    from router import chat
    from core_functions import db_function

    pushes = []

    async def fake_push(user_ids, title, body, data=None, image_url=None, silent=False):
        pushes.append(data)
        return 1

    async def fake_send(req):
        return {"message_id": "g-9", "sender_name": "Asha"}

    async def noop(*a, **k):
        return None

    async def member_ids(group_id):
        return ["s1", "m2"]

    patch_db(monkeypatch, db_function, FakeCursor(one=[{"?column?": 1}]))
    monkeypatch.setattr(chat.push_service, "send_push_to_users", fake_push)
    monkeypatch.setattr(chat, "send_group_message", fake_send)
    monkeypatch.setattr(chat, "_get_group_member_ids", member_ids)
    monkeypatch.setattr(chat.shared_manager, "broadcast_group_message", noop)

    async def go():
        req = chat.SendGroupMessageRequest(group_id="grp", sender_id="s1", message="yo", message_type="text")
        await chat.group_send_route(req)
        await asyncio.sleep(0)

    run(go())
    assert pushes == [{"type": "new_group_message", "group_id": "grp", "sender_id": "s1", "message_id": "g-9"}]


# ---------------------------------------------------------------------------- (11) plan block = real 403
@pytest.mark.parametrize("route_name", ["chat_list_route", "group_list_route"])
def test_chat_list_routes_answer_403_when_the_plan_blocks_chat(monkeypatch, route_name):
    from router import chat

    async def blocked(org_id, feature, is_admin=True):
        raise HTTPException(status_code=403, detail="Your plan has expired. Please contact your admin.")

    monkeypatch.setattr(chat, "check_plan_feature", blocked)
    with pytest.raises(HTTPException) as e:
        run(getattr(chat, route_name)("u1", org_id="o1"))
    assert e.value.status_code == 403 and e.value.detail.startswith("Your plan has expired")


def test_chat_list_routes_still_return_the_list_when_allowed(monkeypatch):
    from router import chat

    async def allowed(org_id, feature, is_admin=True):
        return None

    async def chats(user_id):
        return {"success": True, "chats": []}

    monkeypatch.setattr(chat, "check_plan_feature", allowed)
    monkeypatch.setattr(chat, "get_chat_list", chats)
    monkeypatch.setattr(chat, "get_group_chat_list", chats)
    assert run(chat.chat_list_route("u1", org_id="o1")) == {"success": True, "chats": []}
    assert run(chat.group_list_route("u1", org_id="o1")) == {"success": True, "chats": []}


# ---------------------------------------------------------------------------- (3) role_id / is_active on the live-status lists
LIVE_ROW = {
    "user_id": "u1", "name": "Asha", "emp_id": "E1", "mobile_number": "9000000003", "email": "a@x.io",
    "dept_name": "Ops", "branch_name": "HQ", "role_id": 2, "user_status": 1,
}


@pytest.mark.parametrize("fn,status", [
    ("get_absent_staff", "Absent"),
    ("get_late_punched_in_staff", "Late Punched-In"),
    ("get_punched_in_staff", "Punched In"),
    ("get_working_staff", "Punched In"),
    ("get_punched_out_staff", "Punched Out"),
])
def test_live_status_lists_return_role_id_and_is_active(monkeypatch, fn, status):
    from core_functions import home_functions as hf
    from core_functions import manager_functions as mf

    async def scope(user_id):
        return "org1", None, None

    row = dict(LIVE_ROW)
    if fn == "get_late_punched_in_staff":
        # lateness is now decided in Python from the IST first punch-in and the shift (WP5): 10:00 vs 09:00-18:00
        from datetime import time
        row.update(first_in=time(10, 0), start_time=time(9, 0), end_time=time(18, 0))
    cur = FakeCursor(many=[[row]])
    patch_db(monkeypatch, hf, cur)
    monkeypatch.setattr(mf, "get_manager_scope", scope)
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    rows = run(getattr(hf, fn)("admin1"))
    assert len(rows) == 1 and rows[0].status == status
    assert rows[0].role_id == 2 and rows[0].is_active is True
    select = cur.sql[0][0]
    assert "u.role_id" in select and "u.status AS user_status" in select and "u.status = 1" in select


# ---------------------------------------------------------------------------- (4)+(5) staff detail / profile
DETAIL_ROW = {
    "user_id": "u1", "name": "Asha", "emp_id": "E1", "mobile_number": "9000000003", "email": "a@x.io",
    "branch_id": "3", "dept_id": "d-1", "org_id": "org1", "dept_name": "Ops", "branch_name": "HQ", "branch_address": "Road 1",
    "punch_in": None, "punch_out": None, "attendance_date": None,
}


def test_staff_detail_unknown_user_is_a_404_not_a_500(monkeypatch):
    from core_functions import home_functions as hf

    cur = FakeCursor(one=[None])
    patch_db(monkeypatch, hf, cur)
    with pytest.raises(HTTPException) as e:
        run(hf.get_staff_detail("nope", "org1"))
    assert e.value.status_code == 404
    assert "u.status IN (0, 1)" in cur.sql[0][0]  # QW-4b: inactive staff (0) are found so they can be reactivated; deleted (2) are not


def test_staff_profile_unknown_user_is_a_404_not_a_500(monkeypatch):
    from core_functions import home_functions as hf

    patch_db(monkeypatch, hf, FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(hf.get_staff_profile("nope", "org1"))
    assert e.value.status_code == 404


def test_staff_detail_returns_branch_dept_and_org_ids(monkeypatch):
    from core_functions import home_functions as hf

    patch_db(monkeypatch, hf, FakeCursor(one=[dict(DETAIL_ROW)]))
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    res = run(hf.get_staff_detail("u1", "org1"))
    assert (res.branch_id, res.dept_id, res.org_id) == ("3", "d-1", "org1")
    assert res.status == "Absent" and res.branch_address == "Road 1"


def test_staff_profile_returns_branch_dept_and_org_ids(monkeypatch):
    from core_functions import home_functions as hf

    row = {k: None for k in (
        "dob", "gender", "blood_group", "address", "emergency_contact_no", "acc_no", "ifsc_code", "bank_name", "bank_branch",
        "shift_id", "father_name", "marital_status", "aadhaar_no", "pan_no", "uan_no", "pf_no", "esi_no", "doj")}
    row.update({k: DETAIL_ROW[k] for k in ("user_id", "name", "emp_id", "mobile_number", "email", "branch_id", "dept_id", "org_id", "dept_name", "branch_name")})
    row["role_id"] = 3
    cur = FakeCursor(one=[row])
    patch_db(monkeypatch, hf, cur)
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    res = run(hf.get_staff_profile("u1", "org1"))
    assert (res.branch_id, res.dept_id, res.org_id, res.role_name) == ("3", "d-1", "org1", "Staff")
    assert "u.status IN (0, 1)" in cur.sql[0][0]    # QW-4b: inactive staff (0) open too, deleted (2) stay 404


def test_addstaff_detail_returns_branch_dept_and_org_ids(monkeypatch):
    from core_functions import add_staff_function as asf
    import ml.s3

    row = {k: None for k in (
        "dob", "gender", "blood_group", "address", "emergency_contact_no", "acc_no", "ifsc_code", "bank_name", "bank_branch", "shift_id", "role_name")}
    row.update({"user_id": "u1", "name": "Asha", "emp_id": "E1", "mobile_number": "9000000003", "email": "a@x.io", "status": 1,
                "is_field_staff": 0, "branch_id": "3", "dept_id": "d-1", "org_id": "org1", "dept_name": "Ops", "branch_name": "HQ"})
    patch_db(monkeypatch, asf, FakeCursor(one=[row]))
    monkeypatch.setattr(ml.s3, "get_employee_profile_url", lambda uid: None)
    res = run(asf.get_staff_detail("u1"))
    assert (res.branch_id, res.dept_id, res.org_id) == ("3", "d-1", "org1")
    patch_db(monkeypatch, asf, FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(asf.get_staff_detail("nope"))
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------- (12) registration keeps its HTTP status
def test_registration_reraises_http_exceptions(monkeypatch):
    from core_functions import auth_function as af

    cur = FakeCursor(one=[{"user_id": "existing"}])
    conn = patch_db(monkeypatch, af, cur)
    req = af.AdminRegisterRequest(
        name="A", emp_id="E", mobile_number="9000000001", email="a@x.io", org_name="Org", org_address="Road", branches=["HQ"])
    with pytest.raises(HTTPException) as e:
        run(af.admin_register_json(req))
    assert e.value.status_code == 400 and e.value.detail == "User already registered"
    assert conn.rollbacks == 1


def test_registration_still_maps_unexpected_errors_to_500(monkeypatch):
    from core_functions import auth_function as af

    class Boom(FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("db down")

    patch_db(monkeypatch, af, Boom())
    req = af.AdminRegisterRequest(
        name="A", emp_id="E", mobile_number="9000000001", email="a@x.io", org_name="Org", org_address="Road", branches=["HQ"])
    with pytest.raises(HTTPException) as e:
        run(af.admin_register_json(req))
    assert e.value.status_code == 500


# ---------------------------------------------------------------------------- (17) staff-location log
def _location_request(lat, lon):
    from core_functions.staff_location_functions import LocationLogRequest

    return LocationLogRequest(user_id="u1", org_id="org1", latitude=lat, longitude=lon)


FIELD_STAFF = {"user_id": "u1", "is_field_staff": 1, "role_id": 3}
T1, T2 = datetime(2026, 10, 30, 3, 0), datetime(2026, 10, 30, 9, 0)


def test_location_log_accepts_zero_coordinates(monkeypatch):
    from core_functions import staff_location_functions as sl

    cur = FakeCursor(one=[FIELD_STAFF], many=[[{"punch_in": T1, "punch_out": None}]])
    patch_db(monkeypatch, sl, cur)
    res = run(sl.log_staff_location(_location_request(0.0, 0.0)))
    assert res["success"] is True
    insert = [p for s, p in cur.sql if s.startswith("INSERT INTO staff_location_logs")][0]
    assert insert[2] == 0.0 and insert[3] == 0.0


def test_location_log_still_rejects_missing_values(monkeypatch):
    from core_functions import staff_location_functions as sl

    for bad in (
        sl.LocationLogRequest.model_construct(user_id="u1", org_id="org1", latitude=None, longitude=77.0),
        sl.LocationLogRequest.model_construct(user_id="u1", org_id="org1", latitude=12.0, longitude=None),
        sl.LocationLogRequest.model_construct(user_id="", org_id="org1", latitude=12.0, longitude=77.0),
    ):
        with pytest.raises(HTTPException) as e:
            run(sl.log_staff_location(bad))
        assert e.value.status_code == 400


def test_location_log_uses_the_open_session_of_a_two_session_day(monkeypatch):
    from core_functions import staff_location_functions as sl

    # newest session is open, an earlier one is closed: the open one counts
    cur = FakeCursor(one=[FIELD_STAFF], many=[[{"punch_in": T2, "punch_out": None}, {"punch_in": T1, "punch_out": T1}]])
    patch_db(monkeypatch, sl, cur)
    assert run(sl.log_staff_location(_location_request(12.9, 77.6)))["success"] is True


def test_location_log_stops_after_punch_out_and_waits_for_punch_in(monkeypatch):
    from core_functions import staff_location_functions as sl

    patch_db(monkeypatch, sl, FakeCursor(one=[FIELD_STAFF], many=[[{"punch_in": T1, "punch_out": T2}]]))
    with pytest.raises(HTTPException) as e:
        run(sl.log_staff_location(_location_request(12.9, 77.6)))
    assert e.value.status_code == 403 and "stopped after punch out" in e.value.detail

    patch_db(monkeypatch, sl, FakeCursor(one=[FIELD_STAFF], many=[[]]))
    with pytest.raises(HTTPException) as e:
        run(sl.log_staff_location(_location_request(12.9, 77.6)))
    assert e.value.status_code == 403 and "after punch in" in e.value.detail


# ---------------------------------------------------------------------------- (20) employee-login returns email
def test_employee_login_returns_the_email(monkeypatch):
    from core_functions import auth_function as af
    import ml.s3

    user = {"user_id": "u1", "role_id": 3, "org_name": "Org", "org_id": "org1", "email": "staff@x.io"}
    cur = FakeCursor(one=[{"user_id": "u1", "role_id": 3, "status": 1}, user])
    patch_db(monkeypatch, af, cur)
    monkeypatch.setattr(ml.s3, "get_org_logo_url", lambda org_id: None)
    res = run(af.employee_login(af.EmployeeLoginRequest(login_id="9000000003")))
    assert res["success"] is True and res["email"] == "staff@x.io" and res["role_id"] == 3
