"""DB-free tests: announcement explicit recipients are limited to the organisation's users, and the payslip template
lookup by id is organisation-scoped. Run: python -m pytest tests/test_announce_template_scope.py -q
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from payroll_fakes import RoutedCursor, patch_db

from core_functions import announcement_functions as af
from core_functions import gen_plans_functions as gp
from core_functions import salary_structure as ss


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- announcements
class CtxCursor(RoutedCursor):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _announce_cursor(member_ids):
    """Routes of send_announcement: the admin check, the member filter, the insert, the delivery query."""
    return CtxCursor([
        ("SELECT role_id FROM users", [{"role_id": 1}]),
        ("INSERT INTO announcement (", [{"announcement_id": "a-1", "title": "T", "message": "M", "org_id": "org-1",
                                         "target": "selected", "created_at": datetime(2026, 1, 1)}]),
        ("(org_id = %s OR parent_id = %s)", lambda p: [{"user_id": u} for u in p[0] if u in member_ids]),
        ("AND parent_id = %s AND status = 1", lambda p: [{"user_id": u} for u in p[0] if u in member_ids]),
    ])


@pytest.fixture
def delivered(monkeypatch):
    from services import realtime
    calls = []

    async def fake_deliver(recipient_ids, *a, **k):
        calls.append(list(recipient_ids))
    monkeypatch.setattr(realtime, "deliver_announcement", fake_deliver)
    return calls


def _req(ids, target="selected"):
    return af.SendAnnouncementRequest(org_id="org-1", sent_by_id="admin-1", title="T", message="M",
                                      target=target, selected_user_ids=ids)


def test_foreign_recipient_is_not_stored_and_count_is_the_stored_number(monkeypatch, delivered):
    cur = _announce_cursor({"u1", "u2"})
    patch_db(monkeypatch, af, cur)
    out = run(af.send_announcement(_req(["u1", "foreign", "u2", "u1"])))
    assert out["success"] is True and out["data"]["recipient_count"] == 2
    stored = [p[1] for s, p in cur.find("INSERT INTO announcement_recipients")]
    assert stored == ["u1", "u2"]
    sql, params = cur.find("(org_id = %s OR parent_id = %s)")[0]
    assert "status = 1" in sql and "user_id = ANY(%s)" in sql
    assert params == (["u1", "foreign", "u2"], "org-1", "org-1")
    assert sql.count("%s") == len(params)


def test_all_foreign_list_is_400_and_nothing_is_written(monkeypatch, delivered):
    cur = _announce_cursor(set())
    conn = patch_db(monkeypatch, af, cur)
    with pytest.raises(HTTPException) as e:
        run(af.send_announcement(_req(["x", "y"])))
    assert (e.value.status_code, e.value.detail) == (400, "No valid recipients selected")
    assert cur.find("INSERT INTO announcement") == []
    assert conn.commits == 0 and delivered == []


def test_other_targets_do_not_use_the_member_filter(monkeypatch, delivered):
    cur = _announce_cursor(set())
    cur.routes.append(("WHERE parent_id = %s AND role_id IN (2, 3)", [{"user_id": "s1"}]))
    patch_db(monkeypatch, af, cur)
    out = run(af.send_announcement(_req(None, target="all")))
    assert out["data"]["recipient_count"] == 1
    assert cur.find("(org_id = %s OR parent_id = %s)") == []


def test_send_route_keeps_the_400(monkeypatch, delivered):
    from router import announcement
    patch_db(monkeypatch, af, _announce_cursor(set()))
    app = FastAPI()
    app.include_router(announcement.router)
    r = TestClient(app).post("/api/announcements", json=dict(
        org_id="org-1", sent_by_id="admin-1", title="T", message="M", target="selected", selected_user_ids=["z"]))
    assert r.status_code == 400 and r.json() == {"detail": "No valid recipients selected"}


# ----------------------------------------------------------------------------- payslip template by id
TEMPLATE = {"pay_slip_id": "ps-1", "pay_slip_name": "Grade A", "org_id": "org-1", "basic_pay": 100, "hra": 10,
            "epf": 5, "professional_tax": 1, "health_insurance": 2, "created_at": datetime(2026, 1, 1)}


def _template_cursor():
    return RoutedCursor([
        ("FROM pay_slip WHERE", lambda p: [TEMPLATE] if p[0] == "ps-1" and (len(p) < 2 or p[1] == "org-1") else []),
    ])


def test_template_without_scope_is_unchanged(monkeypatch):
    cur = _template_cursor()
    patch_db(monkeypatch, ss, cur)
    out = run(ss.get_salary_template("ps-1"))
    assert out["success"] is True and out["data"]["org_id"] == "org-1" and out["data"]["net_salary"] == 102.0
    assert cur.find("FROM pay_slip WHERE")[0] == ("SELECT * FROM pay_slip WHERE pay_slip_id = %s", ("ps-1",))


def test_template_org_id_param_is_part_of_the_predicate(monkeypatch):
    cur = _template_cursor()
    patch_db(monkeypatch, ss, cur)
    assert run(ss.get_salary_template("ps-1", org_id="org-1"))["success"] is True
    assert cur.find("FROM pay_slip WHERE")[0][1] == ("ps-1", "org-1")
    with pytest.raises(HTTPException) as e:
        run(ss.get_salary_template("ps-1", org_id="org-2"))
    assert (e.value.status_code, e.value.detail) == (404, "Salary template not found")


def test_template_of_another_caller_org_is_404(monkeypatch):
    patch_db(monkeypatch, ss, _template_cursor())
    with pytest.raises(HTTPException) as e:
        run(ss.get_salary_template("ps-1", orgs=frozenset({"org-2"})))
    assert (e.value.status_code, e.value.detail) == (404, "Salary template not found")
    assert run(ss.get_salary_template("ps-1", orgs=frozenset({"org-1", "org-2"})))["success"] is True


def _client(monkeypatch, orgs):
    from router import salary
    monkeypatch.setattr(salary, "check_plan_feature", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(gp, "check_plan_feature", lambda *a, **k: asyncio.sleep(0))
    app = FastAPI()

    @app.middleware("http")
    async def identify(request, call_next):
        if orgs is not None:
            request.state.identity = SimpleNamespace(org_id=next(iter(orgs)), role_id=1, user_id="admin-1")
            request.state.allowed_orgs = frozenset(orgs)
        return await call_next(request)

    app.include_router(salary.router)
    return TestClient(app)


def test_template_route_scoping(monkeypatch):
    patch_db(monkeypatch, ss, _template_cursor())
    # no token, no org_id: old behaviour
    r = _client(monkeypatch, None).get("/api/salary/template/ps-1")
    assert r.status_code == 200 and r.json()["data"]["pay_slip_id"] == "ps-1"
    # own org
    r = _client(monkeypatch, {"org-1"}).get("/api/salary/template/ps-1?org_id=org-1")
    assert r.status_code == 200
    # another org's token: not found
    r = _client(monkeypatch, {"org-2"}).get("/api/salary/template/ps-1")
    assert r.status_code == 404 and r.json() == {"detail": "Salary template not found"}
    # org_id param naming another org: not found
    r = _client(monkeypatch, None).get("/api/salary/template/ps-1?org_id=org-2")
    assert r.status_code == 404
