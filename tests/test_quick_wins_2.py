"""DB-free tests for the second batch of quick-win fixes (planner section 4: QW-6, 7, 8, 10, 18).
The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_quick_wins_2.py -q
"""
import asyncio
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeCursor:
    """fetchone() / fetchall() hand out the scripted values in order; every statement is recorded."""

    def __init__(self, one=None, many=None, rowcount=1):
        self.sql = []
        self.one = list(one or [])
        self.many = list(many or [])
        self.rowcount = rowcount

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

    def find(self, text):
        return [(s, p) for s, p in self.sql if text in s]


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def run(coro):
    return asyncio.run(coro)


def patch_db(monkeypatch, module, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


# ---------------------------------------------------------------------------- (6) PUT /addstaff
def _staff(**kw):
    from core_functions.add_staff_function import AddStaffRequest
    base = dict(name="Asha", emp_id="E1", mobile_number="9000000001", email="a@x.in", user_id="org1")
    base.update(kw)
    return AddStaffRequest(**base)


def test_update_staff_never_writes_role_and_keeps_branch_dept_shift_when_omitted(monkeypatch):
    from core_functions import add_staff_function as af

    cur = FakeCursor(one=[{"parent_id": "org1"}])
    conn = patch_db(monkeypatch, af, cur)
    res = run(af.update_staff("u1", _staff()))
    assert res["success"] is True and conn.commits == 1

    users_sql, users_params = cur.find("UPDATE users SET")[0]
    assert "role_id" not in users_sql
    assert "branch_id = COALESCE(%s, branch_id)" in users_sql and "dept_id = COALESCE(%s, dept_id)" in users_sql
    # branch / dept were omitted -> NULL is passed, COALESCE keeps the stored values
    assert users_params[2] is None and users_params[3] is None

    info_sql, info_params = cur.find("UPDATE users_additional_info")[0]
    assert "shift_id = COALESCE(%s, shift_id)" in info_sql
    assert info_params[9] is None            # shift_id omitted -> stored shift stays
    # no branch / department lookups or inserts happened
    assert not cur.find("branch_info") and not cur.find("dept_info")


def test_update_staff_applies_branch_dept_and_shift_when_given(monkeypatch):
    from core_functions import add_staff_function as af

    cur = FakeCursor(one=[{"parent_id": "org1"}, {"branch_id": 7}, {"dept_id": "d-9"}])
    patch_db(monkeypatch, af, cur)
    run(af.update_staff("u1", _staff(branch_name="Chennai", dept_name="Sales", shift_id=4)))
    _sql, params = cur.find("UPDATE users SET")[0]
    assert params[2] == 7 and params[3] == "d-9"
    assert cur.find("UPDATE users_additional_info")[0][1][9] == 4


def test_add_staff_request_shift_default_is_omitted_not_one():
    # the create path still falls back to shift 1 (`data.shift_id if ... else 1`); update must see "omitted" as None
    assert _staff().shift_id is None


# ---------------------------------------------------------------------------- (18) delete_staff = status 2
def test_delete_staff_marks_status_2(monkeypatch):
    from core_functions import add_staff_function as af

    cur = FakeCursor(one=[{"user_id": "u1"}])
    conn = patch_db(monkeypatch, af, cur)
    assert run(af.delete_staff("u1"))["success"] is True
    sql, params = cur.find("UPDATE users SET status")[0]
    assert "status = 2" in sql and params[1] == "u1"
    assert conn.commits == 1


def test_delete_staff_unknown_user_is_404(monkeypatch):
    from core_functions import add_staff_function as af

    patch_db(monkeypatch, af, FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(af.delete_staff("nope"))
    assert e.value.status_code == 404


def test_staff_directory_lists_hide_deleted_users(monkeypatch):
    from core_functions import home_functions as hf

    async def scope(org_id):
        return org_id, None, None

    import core_functions.manager_functions as mf
    monkeypatch.setattr(mf, "get_manager_scope", scope)
    for fn in (hf.get_staff_list, hf.get_all_staff):
        cur = FakeCursor(many=[[]])
        patch_db(monkeypatch, hf, cur)
        try:
            run(fn("org1"))
        except Exception:
            pass
        directory_sql = [s for s, _p in cur.sql if "FROM users u" in s]
        assert directory_sql, fn.__name__
        assert "u.status IN (0, 1)" in directory_sql[0], fn.__name__


# ---------------------------------------------------------------------------- (7) employee requests: date_statuses + shift ids
def test_employee_requests_carry_date_statuses_and_shift_ids(monkeypatch):
    from core_functions import requests_functions as rf

    leave = ("rq-1", "leave", "trip", None, None, None, None, datetime(2026, 10, 1, 9), datetime(2026, 9, 30, 9), "Boss",
             None, None, None, None, None)
    shift = ("rq-2", "shift_change", "night", None, None, "approved", "adm", datetime(2026, 10, 2, 9), datetime(2026, 10, 1, 9),
             "Boss", date(2026, 10, 5), "09:00 AM - 06:00 PM", 4, 2, "10:00 AM - 07:00 PM")
    # WP7: the per-date decisions of every listed request come from ONE query (request_id leads each row)
    cur = FakeCursor(many=[
        [leave, shift],
        [("rq-1", date(2026, 10, 3), "approved", "adm", datetime(2026, 10, 1, 9)), ("rq-1", date(2026, 10, 4), None, None, None)],
    ])
    patch_db(monkeypatch, rf, cur)
    out = rf.get_employee_requests("u1", "org1")["data"]

    assert out[0]["dates"] == ["2026-10-03", "2026-10-04"]
    assert out[0]["date_statuses"] == {
        "2026-10-03": {"status": "approved", "action_by": "adm", "action_at": "2026-10-01T09:00:00"},
        "2026-10-04": {"status": "pending", "action_by": None, "action_at": None},
    }
    assert out[1]["requested_shift_id"] == 4 and out[1]["current_shift_id"] == 2
    assert out[1]["requested_shift_timing"] == "09:00 AM - 06:00 PM" and out[1]["current_shift_timing"] == "10:00 AM - 07:00 PM"
    assert out[1]["date_statuses"] == {} and out[1]["shift_change_date"] == "2026-10-05"
    # existing keys are untouched
    assert {"request_id", "request_type", "reason", "status", "action_by", "action_by_name", "action_at",
            "created_at", "permission_start_time", "permission_end_time"} <= set(out[0])


# ---------------------------------------------------------------------------- (8) aggregate status from the dates
@pytest.mark.parametrize("dates, expected", [
    (["approved", "approved"], "approved"),
    (["rejected", "rejected"], "rejected"),
    (["approved", "rejected"], "approved"),       # no "partial" value exists: same rule as the app's deriveStatus
    (["approved", "pending"], "pending"),
    ([None, "rejected"], "pending"),
])
def test_aggregate_date_status(dates, expected):
    from core_functions.requests_functions import aggregate_date_status
    assert aggregate_date_status(dates) == expected


def _decide(monkeypatch, request_type, date_rows, status="approved", leave_date="2026-10-03"):
    from core_functions import requests_functions as rf

    # WP7: the request row is (type, org_id, requester, status, requester branch, requester dept); the actor lookup that
    # follows finds no such user (None), which is only logged while REQUESTS_ENFORCE_ACTOR is off
    cur = FakeCursor(one=[(request_type, "org1", "u1", "pending", None, None)], many=[[(s,) for s in date_rows]])
    conn = patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    res = rf.update_request_status("rq-1", status, "adm", 1, leave_date)
    return res, cur, conn


def test_leave_decision_recomputes_the_request_status(monkeypatch):
    res, cur, conn = _decide(monkeypatch, "leave", ["approved", "rejected"])
    assert res["success"] is True and conn.commits == 1
    sql, params = cur.find("UPDATE requests")[0]
    assert params[0] == "approved" and params[1] == "adm" and params[4] == "rq-1"


def test_leave_stays_pending_while_a_date_is_pending(monkeypatch):
    _res, cur, _conn = _decide(monkeypatch, "leave", ["approved", "pending"])
    assert cur.find("UPDATE requests")[0][1][0] == "pending"


def test_leave_all_rejected_is_rejected(monkeypatch):
    _res, cur, _conn = _decide(monkeypatch, "leave", ["rejected", "rejected"], status="rejected")
    assert cur.find("UPDATE requests")[0][1][0] == "rejected"


def test_permission_decision_uses_the_same_aggregate(monkeypatch):
    _res, cur, _conn = _decide(monkeypatch, "permission", ["approved"])
    assert cur.find("UPDATE requests")[0][1][0] == "approved"


def test_shift_change_still_updates_the_request_directly(monkeypatch):
    from core_functions import requests_functions as rf

    cur = FakeCursor(one=[("shift_change", "org1", "u1", "pending", None, None)])
    patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    rf.update_request_status("rq-9", "approved", "adm", 1)
    assert not cur.find("FROM leave_dates")
    assert cur.find("UPDATE requests")[0][1][0] == "approved"


# ---------------------------------------------------------------------------- (10) group read_update payload
def test_mark_group_as_read_returns_counts_per_message(monkeypatch):
    from core_functions import chat_functions as cf

    cur = FakeCursor(
        many=[
            [{"chat_id": "m1", "group_id": "g", "sender_id": "s1"}, {"chat_id": "m2", "group_id": "g", "sender_id": "s2"}],
            [{"chat_id": "m1", "read_count": 2}],            # m2 has no count row -> 0
        ],
        one=[{"?column?": 1}, {"total": 4}],            # first: the reader is a participant of the group
    )
    patch_db(monkeypatch, cf, cur)
    res = run(cf.mark_group_as_read(cf.MarkGroupReadRequest(group_id="g", user_id="reader")))
    assert res["success"] is True and res["marked_count"] == 2
    assert res["read_updates"] == [
        {"message_id": "m1", "sender_id": "s1", "read_count": 2, "total_members": 3},
        {"message_id": "m2", "sender_id": "s2", "read_count": 0, "total_members": 3},
    ]
    assert "c.sender_id" in cur.sql[1][0]            # sql[0] is the participant check


def test_mark_group_as_read_with_nothing_unread_has_no_updates(monkeypatch):
    from core_functions import chat_functions as cf

    patch_db(monkeypatch, cf, FakeCursor(one=[{"?column?": 1}], many=[[]]))
    res = run(cf.mark_group_as_read(cf.MarkGroupReadRequest(group_id="g", user_id="reader")))
    assert res == {"success": True, "marked_count": 0, "read_updates": []}


def test_group_read_update_event_carries_counts_and_skips_the_reader(monkeypatch):
    from router import chat

    sent = []

    async def fake_mark(req):
        return {"success": True, "marked_count": 3, "read_updates": [
            {"message_id": "m1", "sender_id": "s1", "read_count": 2, "total_members": 3},
            {"message_id": "m2", "sender_id": "reader", "read_count": 1, "total_members": 3},   # defensive: never to the reader
            {"message_id": "m3", "sender_id": "s2", "read_count": 3, "total_members": 3},
        ]}

    async def fake_send(user_id, payload):
        sent.append((user_id, payload))

    monkeypatch.setattr(chat, "mark_group_as_read", fake_mark)
    monkeypatch.setattr(chat.shared_manager, "_send_to_user_sessions", fake_send)

    from types import SimpleNamespace
    anon = SimpleNamespace(state=SimpleNamespace(identity=None))      # no token: the route only forwards
    res = run(chat.group_mark_read_route(chat.MarkGroupReadRequest(group_id="g", user_id="reader"), anon))
    assert res == {"success": True, "marked_count": 3}          # HTTP body unchanged: no internal field leaks
    assert [u for u, _p in sent] == ["s1", "s2"]
    assert "reader" not in [u for u, _p in sent]
    assert sent[0][1] == {"type": "read_update", "data": {
        "reader_id": "reader", "group_id": "g", "message_id": "m1", "read_count": 2, "total_members": 3}}
