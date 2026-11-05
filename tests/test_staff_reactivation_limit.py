"""DB-free tests: the plan's staff limit on reactivation (status 0 -> 1, P2-11), the shared limit helper, and the
manager link (`users.manager_id`) written by add / edit staff (WP6, planner D-10, D-13). Scripted fake cursor.
Run: python -m pytest tests/test_staff_reactivation_limit.py -q
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import add_staff_function as af  # noqa: E402

TODAY = date(2026, 11, 2)


class RoutedCursor:
    """routes = [(substring, rows)] answered in order of the first matching substring; every statement is logged."""

    def __init__(self, routes=(), rowcount=1):
        self.routes = list(routes)
        self.sql = []
        self.last = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        self.last = []
        for text, rows in self.routes:
            if text in s:
                self.last = list(rows(params) if callable(rows) else rows)
                break

    def fetchone(self):
        return self.last.pop(0) if self.last else None

    def fetchall(self):
        out, self.last = self.last, []
        return out

    def close(self):
        pass

    def find(self, text):
        return [(s, p) for s, p in self.sql if text in s]


class Conn:
    def __init__(self, cur):
        self.cur = cur
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


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(af, "ist_today", lambda: TODAY)


def patch(monkeypatch, cur):
    conn = Conn(cur)
    monkeypatch.setattr(af, "get_db_connection", lambda: conn)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    return conn


def staff_row(status=0, role=3, parent="org1", manager=None, user_id="u1"):
    return {"user_id": user_id, "role_id": role, "parent_id": parent, "manager_id": manager, "status": status}


def routes(row, sub=None, count=0, update=True):
    return [
        ("SELECT user_id, role_id, parent_id, manager_id, status FROM users", [row] if row else []),
        ("FROM punchapp_subscription", [sub] if sub else []),
        ("SELECT COUNT(*) AS cnt FROM users", [{"cnt": count}]),
        ("UPDATE users SET status", [{"user_id": "u1"}] if update else []),
    ]


# ------------------------------------------------------------------------------------------------ the helper
def test_helper_trial_limit_is_ten():
    cur = RoutedCursor(routes(None, sub=None, count=10))
    with pytest.raises(HTTPException) as e:
        af.assert_staff_capacity(cur, "org1")
    assert e.value.status_code == 403 and e.value.detail.startswith("Staff limit reached (10/10)")
    af.assert_staff_capacity(RoutedCursor(routes(None, sub=None, count=9)), "org1")


def test_helper_active_plan_limit_and_ist_date():
    cur = RoutedCursor(routes(None, sub={"staff_limit": 25}, count=24))
    af.assert_staff_capacity(cur, "org1")
    sql, params = cur.sql[0]
    assert "end_date >= %s" in sql and params == ("org1", TODAY)
    with pytest.raises(HTTPException) as e:
        af.assert_staff_capacity(RoutedCursor(routes(None, sub={"staff_limit": 25}, count=25)), "org1")
    assert "(25/25)" in e.value.detail
    # a plan row with a NULL limit falls back to the trial limit
    with pytest.raises(HTTPException):
        af.assert_staff_capacity(RoutedCursor(routes(None, sub={"staff_limit": None}, count=10)), "org1")


def test_helper_count_statement_counts_active_staff_including_manager_owned():
    cur = RoutedCursor(routes(None, count=0))
    af.assert_staff_capacity(cur, "org1")
    sql, params = cur.find("SELECT COUNT(*) AS cnt FROM users")[0]
    assert "role_id IN (2,3)" in sql and "status = 1" in sql
    assert "parent_id = %s" in sql and "manager_id IN (SELECT m.user_id FROM users m WHERE m.parent_id = %s AND m.role_id = 2)" in sql
    assert params == ("org1", "org1") and sql.count("%s") == 2


def test_helper_lock_is_optional_and_comes_first():
    plain = RoutedCursor(routes(None))
    af.assert_staff_capacity(plain, "org1")
    assert not plain.find("FOR UPDATE")
    locked = RoutedCursor(routes(None))
    af.assert_staff_capacity(locked, "org1", lock=True)
    assert locked.sql[0] == ("SELECT org_id FROM org_info WHERE org_id = %s FOR UPDATE", ("org1",))


# ------------------------------------------------------------------------------------------------ reactivation
def test_reactivation_under_the_limit_succeeds(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=0), count=9))
    conn = patch(monkeypatch, cur)
    out = run(af.set_staff_status("u1", 1))
    assert out == {"success": True, "message": "Staff activated successfully"}
    assert conn.commits == 1 and conn.rollbacks == 0
    assert cur.find("FOR UPDATE")[0][1] == ("org1",)
    upd = cur.find("UPDATE users SET status")[0]
    assert upd[1][0] == 1 and upd[1][2] == "u1"
    # the limit was checked BEFORE the write
    assert [s for s, _ in cur.sql].index(upd[0]) > [s for s, _ in cur.sql].index(cur.find("SELECT COUNT(*)")[0][0])


def test_reactivation_at_the_trial_limit_is_403_and_writes_nothing(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=0), sub=None, count=10))
    conn = patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 1))
    assert e.value.status_code == 403 and e.value.detail == "Staff limit reached (10/10). Please upgrade your plan to add more staff."
    assert not cur.find("UPDATE users SET status") and conn.commits == 0 and conn.rollbacks == 1


def test_reactivation_uses_the_plan_limit(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=0), sub={"staff_limit": 50}, count=49))
    patch(monkeypatch, cur)
    assert run(af.set_staff_status("u1", 1))["success"] is True
    cur = RoutedCursor(routes(staff_row(status=0), sub={"staff_limit": 50}, count=50))
    patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 1))
    assert "(50/50)" in e.value.detail


def test_restoring_a_soft_deleted_user_is_also_a_new_seat(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=2), count=10))
    patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 1))
    assert e.value.status_code == 403


def test_setting_an_already_active_user_active_does_not_check_the_limit(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=1), count=99))
    patch(monkeypatch, cur)
    assert run(af.set_staff_status("u1", 1))["success"] is True
    assert not cur.find("COUNT(*)") and not cur.find("FOR UPDATE")


def test_deactivation_never_checks_the_limit(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=1), count=99))
    conn = patch(monkeypatch, cur)
    assert run(af.set_staff_status("u1", 0))["message"] == "Staff deactivated successfully"
    assert not cur.find("COUNT(*)") and conn.commits == 1


def test_an_administrator_row_is_not_limited(monkeypatch):
    cur = RoutedCursor(routes(staff_row(status=0, role=1, parent=None), count=99))
    patch(monkeypatch, cur)
    assert run(af.set_staff_status("u1", 1))["success"] is True
    assert not cur.find("COUNT(*)")


def test_manager_owned_legacy_row_checks_the_managers_organisation(monkeypatch):
    # parent_id holds the manager's user_id (old code path); the organisation is the manager's parent_id
    r = routes(staff_row(status=0, parent="m1", manager="m1"), count=10)
    r.insert(1, ("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org7"}]))
    cur = RoutedCursor(r)
    patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 1))
    assert e.value.status_code == 403
    assert cur.find("FOR UPDATE")[0][1] == ("org7",)
    assert cur.find("FROM punchapp_subscription")[0][1][0] == "org7"


def test_unknown_user_is_404_and_bad_status_is_400(monkeypatch):
    cur = RoutedCursor(routes(None))
    conn = patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("nope", 1))
    assert e.value.status_code == 404 and conn.rollbacks == 1
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 2))
    assert e.value.status_code == 400


def test_database_error_is_generic_500(monkeypatch):
    class Boom(RoutedCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("secret internals")

    conn = patch(monkeypatch, Boom())
    with pytest.raises(HTTPException) as e:
        run(af.set_staff_status("u1", 1))
    assert e.value.status_code == 500 and "secret" not in e.value.detail and conn.rollbacks == 1


def test_the_route_delegates_and_keeps_its_contract(monkeypatch):
    from router import add_staff as route

    async def fake(user_id, status):
        return {"success": True, "message": "Staff activated successfully", "seen": [user_id, status]}

    monkeypatch.setattr(route, "set_staff_status", fake)
    app = FastAPI()
    app.include_router(route.router)
    c = TestClient(app)
    r = c.put("/addstaff/u1/status", params={"status": 1})
    assert r.status_code == 200 and r.json()["seen"] == ["u1", 1]
    assert c.put("/addstaff/u1/status").status_code == 422          # status is still required


def test_the_route_surfaces_the_limit_as_403(monkeypatch):
    from router import add_staff as route

    cur = RoutedCursor(routes(staff_row(status=0), count=10))
    patch(monkeypatch, cur)
    app = FastAPI()
    app.include_router(route.router)
    r = TestClient(app).put("/addstaff/u1/status", params={"status": 1})
    assert r.status_code == 403 and r.json()["detail"].startswith("Staff limit reached (10/10)")


# ------------------------------------------------------------------------------------------------ add / edit: manager link
def _req(**kw):
    base = dict(name="Asha", emp_id="E1", mobile_number="9000000001", email="a@x.in", user_id="org1")
    base.update(kw)
    return af.AddStaffRequest(**base)


def add_routes(manager_ok=True):
    return [
        ("FROM punchapp_subscription", []),
        ("SELECT COUNT(*) AS cnt FROM users", [{"cnt": 0}]),
        ("SELECT * FROM users WHERE", []),
        ("SELECT dept_id FROM dept_info WHERE dept_name = 'General'", [{"dept_id": "d0"}]),
        ("SELECT user_id FROM users WHERE user_id = %s AND role_id = 2", [{"user_id": "m1"}] if manager_ok else []),
    ]


@pytest.fixture
def no_whatsapp(monkeypatch):
    from core_functions import whatsapp_service as wa
    monkeypatch.setattr(wa, "send_employee_onboard_whatsapp", lambda **k: True)


def _insert(cur):
    return cur.find("INSERT INTO users")[0]


def test_add_staff_without_manager_stores_org_as_parent_and_no_manager(monkeypatch, no_whatsapp):
    cur = RoutedCursor(add_routes())
    conn = patch(monkeypatch, cur)
    assert run(af.add_staff(_req()))["success"] is True
    sql, params = _insert(cur)
    assert "parent_id, manager_id," in sql and sql.count("%s") == len(params) == 15
    assert params[6] == "org1" and params[7] is None
    assert not cur.find("AND role_id = 2 AND status = 1") and conn.commits == 1


def test_add_staff_with_a_manager_keeps_the_org_as_parent_and_stores_manager_id(monkeypatch, no_whatsapp):
    cur = RoutedCursor(add_routes())
    patch(monkeypatch, cur)
    run(af.add_staff(_req(parent_id="m1")))
    sql, params = _insert(cur)
    assert params[6] == "org1" and params[7] == "m1"                    # parent_id is NOT the manager any more
    lookup = cur.find("SELECT user_id FROM users WHERE user_id = %s AND role_id = 2")[0]
    assert "status = 1" in lookup[0] and "parent_id = %s" in lookup[0] and lookup[1] == ("m1", "org1")


def test_add_staff_parent_id_equal_to_the_org_means_no_manager(monkeypatch, no_whatsapp):
    cur = RoutedCursor(add_routes())
    patch(monkeypatch, cur)
    run(af.add_staff(_req(parent_id="org1")))
    assert _insert(cur)[1][7] is None and not cur.find("AND role_id = 2 AND status = 1")


def test_add_staff_with_an_unknown_or_foreign_manager_is_400_and_rolled_back(monkeypatch, no_whatsapp):
    cur = RoutedCursor(add_routes(manager_ok=False))
    conn = patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.add_staff(_req(parent_id="someone-else")))
    assert e.value.status_code == 400 and "manager" in e.value.detail.lower()
    assert not cur.find("INSERT INTO users") and conn.rollbacks == 1 and conn.commits == 0


def test_update_staff_sets_the_manager_without_touching_parent_or_role(monkeypatch):
    cur = RoutedCursor([("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org1"}]),
                        ("SELECT user_id FROM users WHERE user_id = %s AND role_id = 2", [{"user_id": "m1"}])])
    patch(monkeypatch, cur)
    run(af.update_staff("u1", _req(parent_id="m1")))
    sql, params = cur.find("UPDATE users SET")[0]
    assert "role_id" not in sql
    assert "manager_id = CASE WHEN %s THEN %s ELSE manager_id END" in sql
    assert params[4] == "org1"                                          # parent_id: the stored organisation
    assert (params[-4], params[-3]) == (True, "m1")
    assert sql.count("%s") == len(params)


def test_update_staff_without_manager_keeps_the_stored_one(monkeypatch):
    cur = RoutedCursor([("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org1"}])])
    patch(monkeypatch, cur)
    run(af.update_staff("u1", _req()))
    sql, params = cur.find("UPDATE users SET")[0]
    assert (params[-4], params[-3]) == (False, None)                    # CASE WHEN false ... ELSE manager_id: unchanged
    assert not cur.find("AND role_id = 2 AND status = 1") and sql.count("%s") == len(params)


def test_update_staff_with_the_org_id_clears_the_manager(monkeypatch):
    cur = RoutedCursor([("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org1"}])])
    patch(monkeypatch, cur)
    run(af.update_staff("u1", _req(parent_id="org1")))
    sql, params = cur.find("UPDATE users SET")[0]
    assert (params[-4], params[-3]) == (True, None)


def test_update_staff_never_rewrites_parent_id_even_when_the_body_carries_one(monkeypatch):
    cur = RoutedCursor([("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org1"}]),
                        ("SELECT user_id FROM users WHERE user_id = %s AND role_id = 2", [{"user_id": "m1"}])])
    patch(monkeypatch, cur)
    run(af.update_staff("u1", _req(parent_id="m1")))
    assert cur.find("UPDATE users SET")[0][1][4] == "org1"


def test_update_staff_with_a_foreign_manager_is_400(monkeypatch):
    cur = RoutedCursor([("SELECT parent_id FROM users WHERE user_id = %s", [{"parent_id": "org1"}])])
    conn = patch(monkeypatch, cur)
    with pytest.raises(HTTPException) as e:
        run(af.update_staff("u1", _req(parent_id="zzz")))
    assert e.value.status_code == 400 and not cur.find("UPDATE users SET") and conn.rollbacks == 1
