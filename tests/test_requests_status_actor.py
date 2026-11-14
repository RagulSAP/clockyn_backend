"""DB-free tests of POST /requests/update-status (WP7): the actor check (log-only unless REQUESTS_ENFORCE_ACTOR),
org scoping, per-date decisions round trip and the aggregate status, plus the request lists built from them.
Run: python -m pytest tests/test_requests_status_actor.py -q
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import requests_functions as rf  # noqa: E402
from core_functions import request_rules as rr  # noqa: E402
from wp7_fakes import RouteCursor, norm, patch_db, placeholders_match  # noqa: E402

ORG = "org-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("REQUESTS_ENFORCE_ACTOR", raising=False)
    monkeypatch.delenv("ENFORCE_SINGLE_DEVICE", raising=False)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)


class DecisionCursor(RouteCursor):
    """RouteCursor plus a tiny stateful `leave_dates` table: {date: status}."""

    def __init__(self, dates, request_type="leave", org=ORG, requester="stf-1", branch=1, dept="d1",
                 actor=None, permitted=True, assignments=(), owner_of=(ORG,), request_status="pending"):
        super().__init__()
        self.dates = dict(dates)
        self.add("FROM requests r LEFT JOIN users u", one=(request_type, org, requester, request_status, branch, dept))
        if actor is not None:
            self.add("SELECT user_id, role_id, status, parent_id FROM users WHERE user_id", one=actor)
        self.add("FROM org_info WHERE org_id = %s AND parent_id = %s",
                 one=lambda p: (1,) if p[0] in owner_of else None)
        self.add("FROM manager_permissions", one=(1,) if permitted else None)
        self.add("FROM manager_assignments", many=list(assignments))

    def execute(self, sql, params=None):
        s = norm(sql)
        if s.startswith("UPDATE leave_dates"):
            self.sql.append((s, params))
            status, action_by, _at, _upd, request_id = params[:5]
            if len(params) == 6:                                   # one date
                day = params[5]
                if day in self.dates:
                    self.dates[day] = status
                    self.rowcount = 1
                else:
                    self.rowcount = 0
            else:
                for day in self.dates:
                    self.dates[day] = status
                self.rowcount = len(self.dates)
            return
        if s.startswith("SELECT COALESCE(status, 'pending') FROM leave_dates"):
            self.sql.append((s, params))
            self._many = [(v,) for v in self.dates.values()]
            self._one = None
            return
        super().execute(sql, params)


def decide(monkeypatch, cur, status="approved", by="adm-1", leave_date=None):
    conn = patch_db(monkeypatch, rf, cur)
    return rf.update_request_status("req-1", status, by, 1, leave_date), conn


ADMIN = ("adm-1", 1, 1, None)
MANAGER = ("mgr-1", 2, 1, ORG)
STAFF = ("stf-9", 3, 1, ORG)


# ---------------------------------------------------------------------------- actor check: log only (default)
def test_flag_off_logs_actor_check_failed_and_proceeds(monkeypatch, caplog):
    cur = DecisionCursor({date(2026, 11, 3): "pending"}, actor=None)           # unknown actor
    with caplog.at_level(logging.WARNING, logger=rf.logger.name):
        res, conn = decide(monkeypatch, cur, by="ghost")
    assert res["success"] is True and conn.commits == 1
    assert cur.has("UPDATE leave_dates") and cur.has("UPDATE requests")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "actor_check_failed" in r.getMessage()]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "request_id=req-1" in msg and "action_by=ghost" in msg and rr.FAIL_NOT_FOUND in msg and "enforced=False" in msg


def test_flag_off_allowed_actor_logs_nothing(monkeypatch, caplog):
    cur = DecisionCursor({date(2026, 11, 3): "pending"}, actor=ADMIN)
    with caplog.at_level(logging.WARNING, logger=rf.logger.name):
        res, _ = decide(monkeypatch, cur)
    assert res["success"] is True
    assert not [r for r in caplog.records if "actor_check_failed" in r.getMessage()]


# ---------------------------------------------------------------------------- actor check: enforced
@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("REQUESTS_ENFORCE_ACTOR", "1")


@pytest.mark.parametrize("kind, actor, kw, outcome", [
    ("admin of the org", ADMIN, {}, "ok"),
    ("admin of another org", ("adm-2", 1, 1, None), {"owner_of": ("org-2",)}, 404),
    ("manager with permission", MANAGER, {"permitted": True}, "ok"),
    ("manager without permission", MANAGER, {"permitted": False}, 403),
    ("manager of another org", ("mgr-2", 2, 1, "org-2"), {}, 404),
    ("manager outside the assigned branch", MANAGER, {"assignments": [(2, None)], "branch": 1}, 403),
    ("manager inside the assigned branch", MANAGER, {"assignments": [(1, None)], "branch": 1}, "ok"),
    ("manager outside the assigned department", MANAGER, {"assignments": [(None, "d2")], "dept": "d1"}, 403),
    ("staff member", STAFF, {}, 403),
    ("inactive admin", ("adm-1", 1, 0, None), {}, 403),
    ("unknown user", None, {}, 403),
])
def test_actor_matrix_enforced(monkeypatch, enforced, kind, actor, kw, outcome):
    cur = DecisionCursor({date(2026, 11, 3): "pending"}, actor=actor, **kw)
    conn = patch_db(monkeypatch, rf, cur)
    if outcome == "ok":
        assert rf.update_request_status("req-1", "approved", "who", 1)["success"] is True
        assert cur.has("UPDATE leave_dates") and conn.commits == 1
        return
    with pytest.raises(HTTPException) as exc:
        rf.update_request_status("req-1", "approved", "who", 1)
    assert exc.value.status_code == outcome, kind
    assert exc.value.detail == ("Request not found" if outcome == 404 else "Not allowed to decide this request")
    assert not cur.has("UPDATE leave_dates") and not cur.has("UPDATE requests") and conn.commits == 0


def test_request_lookup_and_permission_sql_are_scoped(monkeypatch, enforced):
    cur = DecisionCursor({date(2026, 11, 3): "pending"}, actor=MANAGER)
    decide(monkeypatch, cur, by="mgr-1")
    sql, params = cur.find("FROM requests r LEFT JOIN users u")[0]
    assert "r.request_id = %s" in sql and params == ("req-1",)
    sql, params = cur.find("FROM manager_permissions")[0]
    assert params == ("mgr-1", ORG, "Requests Management") and placeholders_match(sql, params)
    assert "mp.status = 1" in sql and "ps.setting_name = %s" in sql and "mp.org_id = %s" in sql
    sql, params = cur.find("FROM manager_assignments")[0]
    assert params == ("mgr-1",)


def test_unknown_request_is_a_plain_not_found_in_both_modes(monkeypatch, enforced):
    cur = RouteCursor().add("FROM requests r LEFT JOIN users u", one=None)
    patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("nope", "approved", "adm-1", 1) == {"success": False, "message": "Request not found"}
    assert not cur.has("UPDATE")


def test_invalid_status_is_a_400_before_any_database_work(monkeypatch):
    def boom():
        raise AssertionError("no connection expected")
    monkeypatch.setattr(rf, "get_db_connection", boom)
    with pytest.raises(HTTPException) as exc:
        rf.update_request_status("req-1", "maybe", "adm-1", 1)
    assert exc.value.status_code == 400 and exc.value.detail == "Invalid status"


# ---------------------------------------------------------------------------- per-date round trip
D1, D2, D3 = date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)


def test_approve_and_reject_selected_dates_store_each_status_and_the_aggregate(monkeypatch):
    cur = DecisionCursor({D1: "pending", D2: "pending", D3: "pending"}, actor=ADMIN)
    patch_db(monkeypatch, rf, cur)
    aggregates = []
    for status, day in (("approved", "2026-11-03"), ("rejected", "2026-11-04"), ("approved", "2026-11-05")):
        assert rf.update_request_status("req-1", status, "adm-1", 1, day)["success"] is True
        aggregates.append(cur.find("UPDATE requests")[-1][1][0])
    assert cur.dates == {D1: "approved", D2: "rejected", D3: "approved"}
    assert aggregates == ["pending", "pending", "approved"]          # pending while any date is pending
    sql, params = cur.find("UPDATE leave_dates")[0]
    assert "leave_date = %s" in sql and params[0] == "approved" and params[1] == "adm-1" and params[4:] == ("req-1", D1)
    assert placeholders_match(sql, params)


def test_all_dates_rejected_aggregates_to_rejected_and_a_revert_goes_back_to_pending(monkeypatch):
    cur = DecisionCursor({D1: "pending", D2: "pending"}, actor=ADMIN)
    patch_db(monkeypatch, rf, cur)
    rf.update_request_status("req-1", "rejected", "adm-1", 1)                           # no leave_date: every date
    assert cur.dates == {D1: "rejected", D2: "rejected"} and cur.find("UPDATE requests")[-1][1][0] == "rejected"
    rf.update_request_status("req-1", "pending", "adm-1", 1, "2026-11-04")
    assert cur.dates == {D1: "rejected", D2: "pending"} and cur.find("UPDATE requests")[-1][1][0] == "pending"


def test_bulk_decision_without_leave_date_decides_all_dates(monkeypatch):
    cur = DecisionCursor({D1: "pending", D2: "pending", D3: "pending"}, actor=ADMIN)
    patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("req-1", "approved", "adm-1", 1)["success"] is True
    assert cur.dates == {D1: "approved", D2: "approved", D3: "approved"}
    sql, params = cur.find("UPDATE leave_dates")[0]
    assert "leave_date = %s" not in sql and len(params) == 5


def test_permission_decision_uses_the_same_rule(monkeypatch):
    cur = DecisionCursor({D1: "pending"}, request_type="permission", actor=ADMIN)
    patch_db(monkeypatch, rf, cur)
    rf.update_request_status("req-1", "approved", "adm-1", 1)
    assert cur.find("UPDATE requests")[-1][1][0] == "approved"


def test_unknown_date_is_reported_and_nothing_is_kept(monkeypatch):
    cur = DecisionCursor({D1: "pending"}, actor=ADMIN)
    res, conn = decide(monkeypatch, cur, leave_date="2026-12-25")
    assert res == {"success": False, "message": "Date not part of this request"}
    assert conn.rollbacks == 1 and conn.commits == 0 and not cur.has("UPDATE requests")


def test_date_for_a_request_without_dates_is_reported(monkeypatch):
    cur = DecisionCursor({}, request_type="shift_change", actor=ADMIN)
    res, conn = decide(monkeypatch, cur, leave_date="2026-11-03")
    assert res == {"success": False, "message": "Date not part of this request"} and conn.commits == 0


def test_leave_without_date_rows_is_a_404(monkeypatch):
    cur = DecisionCursor({}, actor=ADMIN)
    conn = patch_db(monkeypatch, rf, cur)
    with pytest.raises(HTTPException) as exc:
        rf.update_request_status("req-1", "approved", "adm-1", 1)
    assert exc.value.status_code == 404 and exc.value.detail == "Request has no dates"
    assert conn.commits == 0 and conn.rollbacks >= 1


def test_malformed_leave_date_is_a_400(monkeypatch):
    patch_db(monkeypatch, rf, DecisionCursor({D1: "pending"}, actor=ADMIN))
    with pytest.raises(HTTPException) as exc:
        rf.update_request_status("req-1", "approved", "adm-1", 1, "03/11/2026")
    assert exc.value.status_code == 400 and exc.value.detail == "Invalid date"


def test_shift_change_decision_updates_the_request_and_notifies(monkeypatch):
    cur = DecisionCursor({}, request_type="shift_change", actor=ADMIN)
    sent = []
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: "tok")
    monkeypatch.setattr(rf, "_fire_user_notification", lambda uid, title, body, data=None: sent.append((uid, title, data)))
    patch_db(monkeypatch, rf, cur)
    assert rf.update_request_status("req-1", "approved", "adm-1", 1)["success"] is True
    assert cur.find("UPDATE requests")[0][1][0] == "approved" and not cur.has("UPDATE users")
    assert sent == [("stf-1", "Shift Change Approved",
                     {"type": "request_updated", "request_type": "shift_change", "request_id": "req-1", "status": "approved"})]


# ---------------------------------------------------------------------------- lists carry the decisions
def test_employee_list_derives_the_leave_status_from_its_dates_and_adds_the_effective_shift(monkeypatch):
    leave = ("rq-1", "leave", "trip", None, None, None, None, None, datetime(2026, 9, 30, 9), "",
             None, None, None, None, None)                                   # legacy row: status NULL
    shift = ("rq-2", "shift_change", "night", None, None, "approved", "adm", datetime(2026, 10, 2, 9),
             datetime(2026, 10, 1, 9), "Boss", date(2026, 10, 5), "10:00 PM - 06:00 AM", 4, 2, "09:00 AM - 06:00 PM")
    from datetime import time
    cur = RouteCursor()
    cur.add("FROM requests r LEFT JOIN users u", many=[leave, shift])
    cur.add("FROM leave_dates WHERE request_id = ANY", many=[
        ("rq-1", D1, "approved", "adm", datetime(2026, 10, 1, 9)), ("rq-1", D2, "rejected", "adm", datetime(2026, 10, 1, 9))])
    cur.add("FROM requests r JOIN shift s", one={"shift_id": 4, "start_time": time(22, 0), "end_time": time(6, 0)})
    patch_db(monkeypatch, rf, cur)
    out = rf.get_employee_requests("stf-1", ORG)["data"]
    assert out[0]["status"] == "approved" and out[0]["dates"] == ["2026-11-03", "2026-11-04"]
    assert out[0]["date_statuses"]["2026-11-04"]["status"] == "rejected"
    assert out[1]["effective_shift_id"] == 4 and out[1]["effective_shift_timing"] == "10:00 PM - 06:00 AM"
    assert "effective_shift_id" not in out[0]
    sql, params = cur.find("FROM leave_dates WHERE request_id = ANY")[0]
    assert params == (["rq-1", "rq-2"],)                                     # one query for every request
    list_sql = cur.sql[0][0]
    assert "rs.org_id = r.org_id" in list_sql and "cs.org_id = r.org_id" in list_sql


def test_admin_list_adds_emp_id_and_profile_photo(monkeypatch):
    row = ("rq-1", "leave", "trip", None, None, "pending", None, None, datetime(2026, 9, 30, 9), "stf-1",
           "Asha", "", None, None, None, None, None, "E-17")
    cur = RouteCursor()
    cur.add("SELECT role_id, parent_id FROM users", one=(1, None))
    cur.add("FROM requests r JOIN users u", many=[row])
    cur.add("FROM leave_dates WHERE request_id = ANY", many=[("rq-1", D1, "pending", None, None)])
    patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_profile_photos", lambda ids: {i: f"https://photo/{i}" for i in ids})
    out = rf.get_admin_requests(ORG)["data"]
    assert out[0]["emp_id"] == "E-17" and out[0]["profile_photo"] == "https://photo/stf-1"
    assert out[0]["staff_name"] == "Asha" and out[0]["date_statuses"]["2026-11-03"]["status"] == "pending"
    sql, params = cur.find("FROM requests r JOIN users u")[0]
    assert "WHERE r.org_id = %s" in sql and params == [ORG] and placeholders_match(sql, params)
