"""DB-free tests of plan gating, subscription expiry and the trial staff limit (D-12, D-13).
Run: python -m pytest tests/test_plan_gating.py -q
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI, Form, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import add_staff_function as asf  # noqa: E402
from core_functions import gen_plans_functions as gp  # noqa: E402

TODAY = date(2026, 11, 2)
ADMIN_MSG = "Your plan has expired. Please renew your plan to continue using this feature."
STAFF_MSG = "Your plan has expired. Please contact your admin."


def run(coro):
    return asyncio.run(coro)


class Cur:
    """Scripted cursor: fetchone() answers by SQL fragment; every statement is recorded."""

    def __init__(self, answers=None, rowcount=0):
        self.answers = answers or {}
        self.sql = []
        self.rowcount = rowcount
        self._last = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        self._last = None
        for frag, value in self.answers.items():
            if frag in s:
                self._last = value
                break

    def fetchone(self):
        return self._last

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


@pytest.fixture
def db(monkeypatch):
    def install(answers=None, rowcount=0):
        cur = Cur(answers, rowcount)
        conn = Conn(cur)
        monkeypatch.setattr(gp, "get_db_connection", lambda: conn)
        monkeypatch.setattr(gp, "return_connection", lambda c: None)
        monkeypatch.setattr(gp, "ist_today", lambda: TODAY)
        return cur, conn
    return install


# ── expiry writer ───────────────────────────────────────────────────────────
class TestExpireDueSubscriptions:
    def test_org_scoped_update_with_a_cursor_leaves_the_commit_to_the_caller(self):
        cur = Cur(rowcount=2)
        assert gp.expire_due_subscriptions(TODAY, org_id="org-1", cursor=cur) == 2
        sql, params = cur.sql[0]
        assert sql == ("UPDATE subscription SET status = 'EXPIRED', updated_at = NOW() "
                       "WHERE status = 'ACTIVE' AND end_date < %s AND org_id = %s")
        assert params == (TODAY, "org-1")           # '<': the end date itself is still a valid day

    def test_global_sweep_has_no_org_predicate(self):
        cur = Cur(rowcount=0)
        gp.expire_due_subscriptions(TODAY, cursor=cur)
        sql, params = cur.sql[0]
        assert "org_id" not in sql and params == (TODAY,)

    def test_without_a_cursor_it_owns_its_connection_and_commits(self, db):
        cur, conn = db(rowcount=3)
        assert gp.expire_due_subscriptions(TODAY, org_id="org-1") == 3 and conn.commits == 1


# ── plan status ─────────────────────────────────────────────────────────────
class TestPlanStatus:
    def status(self, db, trial=None, sub=None, rowcount=0):
        cur, conn = db({"FROM free_trial": trial, "FROM subscription ps": sub}, rowcount)
        return run(gp.get_org_plan_status("org-1")), cur, conn

    def test_expiry_is_written_before_the_read_and_committed(self, db):
        st, cur, conn = self.status(db, rowcount=1)
        assert cur.sql[0][0].startswith("UPDATE subscription SET status = 'EXPIRED'") and cur.sql[0][1] == (TODAY, "org-1")
        assert conn.commits == 1

    def test_no_commit_when_nothing_expired(self, db):
        _, _, conn = self.status(db, rowcount=0)
        assert conn.commits == 0

    def test_sweep_failure_never_breaks_the_read(self, db, monkeypatch):
        cur, conn = db({"FROM free_trial": None, "FROM subscription ps": None})
        monkeypatch.setattr(gp, "expire_due_subscriptions", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        assert run(gp.get_org_plan_status("org-1")) == {"plan_id": None, "is_free_trial": False, "is_expired": True}
        assert conn.rollbacks == 1

    def test_active_trial_wins(self, db):
        st, *_ = self.status(db, trial={"end_date": TODAY, "is_active": True})
        assert st == {"plan_id": None, "is_free_trial": True, "is_expired": False}      # end date inclusive

    def test_trial_ended_yesterday(self, db):
        st, *_ = self.status(db, trial={"end_date": date(2026, 11, 1), "is_active": True})
        assert st["is_free_trial"] is False and st["is_expired"] is True

    @pytest.mark.parametrize("end,status,expired", [
        (TODAY, "ACTIVE", False),                  # the last day is still valid
        (date(2026, 11, 3), "ACTIVE", False),
        (date(2026, 11, 1), "ACTIVE", True),
        (date(2027, 1, 1), "EXPIRED", True),       # the written status wins
        (date(2026, 11, 1), "EXPIRED", True),
    ])
    def test_paid_plan_boundaries(self, db, end, status, expired):
        st, cur, _ = self.status(db, sub={"plan_id": 2, "end_date": end, "status": status})
        assert st == {"plan_id": 2, "is_free_trial": False, "is_expired": expired}
        sql, params = cur.find("FROM subscription ps")[0]
        assert "status IN ('ACTIVE', 'EXPIRED')" in sql and params == ("org-1",)

    def test_expired_plan_keeps_its_plan_id_in_the_response(self, db):
        st, *_ = self.status(db, sub={"plan_id": 3, "end_date": date(2026, 1, 1), "status": "EXPIRED"})
        assert st["plan_id"] == 3 and st["is_expired"] is True

    def test_never_subscribed(self, db):
        st, *_ = self.status(db)
        assert st == {"plan_id": None, "is_free_trial": False, "is_expired": True}

    @pytest.mark.parametrize("row_id,name,tier_id", [
        (1, "Core", 1), (4, "Core", 1), (7, "core ", 1),      # every duration of a plan reports its TIER id
        (2, "Prime", 2), (5, "Prime", 2), (8, "PRIME", 2),
        (3, "Elite", 3), (6, "Elite", 3), (9, "Elite", 3),
        (20, "Platinum", 20),                                   # a name that is not one of the three keeps its own id
        (6, None, 6),                                           # no gen_plans row to read the name from
    ])
    def test_plan_id_is_the_tier_for_every_duration_row(self, db, row_id, name, tier_id):
        st, *_ = self.status(db, sub={"plan_id": row_id, "plan_name": name, "end_date": date(2027, 1, 1), "status": "ACTIVE"})
        assert st == {"plan_id": tier_id, "is_free_trial": False, "is_expired": False}

    def test_the_plan_name_is_read_with_a_left_join(self, db):
        _, cur, _ = self.status(db, sub={"plan_id": 5, "plan_name": "Prime", "end_date": date(2027, 1, 1), "status": "ACTIVE"})
        assert "LEFT JOIN gen_plans gp ON gp.plan_id = ps.plan_id" in cur.find("FROM subscription ps")[0][0]

    @pytest.mark.parametrize("row_id,name,blocked", [
        (4, "Core", {"staff_location", "crm"}), (7, "Core", {"staff_location", "crm"}),     # Core 6 / 12 months
        (5, "Prime", {"crm"}), (8, "Prime", {"crm"}),                                       # Prime 6 / 12 months
        (6, "Elite", set()), (9, "Elite", set()),                                           # Elite 6 / 12 months
    ])
    def test_features_of_a_longer_duration_are_those_of_its_tier(self, db, row_id, name, blocked):
        db({"FROM free_trial": None,
            "FROM subscription ps": {"plan_id": row_id, "plan_name": name, "end_date": date(2027, 1, 1), "status": "ACTIVE"}})
        for feature in ("staff_location", "crm"):
            if feature in blocked:
                with pytest.raises(HTTPException) as exc:
                    run(gp.check_plan_feature("org-1", feature))
                assert exc.value.status_code == 403
            else:
                run(gp.check_plan_feature("org-1", feature))      # allowed: no exception


class TestSubscriptionRead:
    def sub(self, db, trial=None, row=None, rowcount=0):
        cur, conn = db({"FROM subscription ps": row, "FROM free_trial": trial}, rowcount)
        return run(gp.get_org_subscription("org-1")), cur

    def test_expiry_sweep_runs_first(self, db):
        _, cur = self.sub(db)
        assert cur.sql[0][0].startswith("UPDATE subscription SET status = 'EXPIRED'")

    def test_placeholder_is_schema_stable_and_keeps_the_old_keys(self, db):
        out, _ = self.sub(db, trial={"end_date": date(2026, 12, 1), "is_active": True})
        d = out["data"]
        for key in ("is_free_trial", "free_trial_end", "billing_date", "plan_name", "plan_amount", "start_date", "end_date",
                    "staff_limit", "status"):
            assert key in d
        assert d["status"] == "FREE_TRIAL" and d["plan_name"] == "Free Trial" and d["staff_limit"] == 10
        assert d["plan_id"] is None and d["billing_state"] == "trial" and d["trial_staff_limit"] == 10

    def test_no_plan_after_the_trial(self, db):
        out, _ = self.sub(db, trial={"end_date": date(2026, 10, 1), "is_active": True})
        assert out["data"]["status"] == "INACTIVE" and out["data"]["billing_state"] == "none" and out["data"]["plan_name"] is None

    def test_paid_row_flags(self, db):
        row = {"plan_id": 2, "status": "ACTIVE", "start_date": date(2026, 3, 1), "end_date": TODAY, "staff_limit": 20,
               "plan_name": "Prime", "plan_amount": 40, "plan_month": 12}
        d = self.sub(db, row=row)[0]["data"]
        assert d["is_expired"] is False and d["billing_state"] == "paid_active" and d["billing_date"] == "2026-03-01"
        d = self.sub(db, row=dict(row, end_date=date(2026, 11, 1)))[0]["data"]
        assert d["is_expired"] is True and d["billing_state"] == "paid_expired"
        d = self.sub(db, row=dict(row, status="EXPIRED", end_date=date(2027, 1, 1)))[0]["data"]
        assert d["is_expired"] is True


# ── the plan matrix ─────────────────────────────────────────────────────────
class TestMatrix:
    @pytest.fixture
    def status(self, monkeypatch):
        holder = {}

        async def fake(org_id):
            return holder["s"]
        monkeypatch.setattr(gp, "get_org_plan_status", fake)
        return holder

    def blocked(self, status, plan_id, feature, trial=False, expired=False, is_admin=True):
        status["s"] = {"plan_id": plan_id, "is_free_trial": trial, "is_expired": expired}
        try:
            run(gp.check_plan_feature("org-1", feature, is_admin=is_admin))
            return None
        except HTTPException as e:
            assert e.status_code == 403
            return e.detail

    @pytest.mark.parametrize("plan,blocked_features", [
        (1, {"staff_location", "crm"}), (2, {"crm"}), (3, set()),
    ])
    def test_matrix_per_plan(self, status, plan, blocked_features):
        for feature in ("chat", "salary_structure", "camera_punch", "staff_location", "crm"):
            detail = self.blocked(status, plan, feature)
            assert (detail is not None) == (feature in blocked_features), (plan, feature)

    def test_trial_never_blocks(self, status):
        for feature in ("chat", "salary_structure", "camera_punch", "staff_location", "crm"):
            assert self.blocked(status, None, feature, trial=True) is None

    def test_expired_blocks_everything_with_the_app_texts(self, status):
        for feature in ("chat", "salary_structure", "camera_punch", "staff_location", "crm"):
            assert self.blocked(status, 3, feature, expired=True, is_admin=True) == ADMIN_MSG
            assert self.blocked(status, 3, feature, expired=True, is_admin=False) == STAFF_MSG

    def test_no_plan_after_trial_blocks(self, status):
        assert self.blocked(status, None, "chat", expired=True) == ADMIN_MSG


# ── the dependency used on routers ──────────────────────────────────────────
@pytest.fixture
def gate_app(monkeypatch):
    calls = []
    state = {"expired": True}

    async def fake_check(org_id, feature, is_admin=True):
        calls.append((org_id, feature, is_admin))
        if state["expired"]:
            raise HTTPException(status_code=403, detail=ADMIN_MSG if is_admin else STAFF_MSG)

    monkeypatch.setattr(gp, "check_plan_feature", fake_check)
    monkeypatch.setattr(gp, "_lookup_org_for_user", lambda uid: {"u1": "org-of-u1", "ghost": None}.get(uid))
    monkeypatch.setattr(gp, "_lookup_org_for_group", lambda gid: {"g1": "org-of-g1"}.get(gid))

    app = FastAPI()
    r = APIRouter(dependencies=[Depends(gp.plan_gate("salary_structure"))])

    @r.get("/by-path/{org_id}")
    async def by_path(org_id: str):
        return {"ok": True}

    @r.get("/by-query")
    async def by_query(org_id: str):
        return {"ok": True}

    @r.post("/by-body")
    async def by_body(payload: dict):
        return {"ok": True, "echo": payload}

    @r.get("/by-user/{user_id}")
    async def by_user(user_id: str):
        return {"ok": True}

    @r.get("/by-group/{group_id}")
    async def by_group(group_id: str):
        return {"ok": True}

    @r.post("/by-sender")
    async def by_sender(payload: dict):
        return {"ok": True}

    @r.get("/nothing")
    async def nothing():
        return {"ok": True}

    chat_like = APIRouter()

    @chat_like.post("/form", dependencies=[Depends(gp.plan_gate("chat", is_admin=False))])
    async def form(sender_id: str = Form(...)):
        return {"ok": True}

    app.include_router(r)
    app.include_router(chat_like)
    return TestClient(app), calls, state


class TestPlanGateDependency:
    def test_blocked_org_gets_a_real_403_with_the_app_text(self, gate_app):
        client, calls, _ = gate_app
        r = client.get("/by-path/org-1")
        assert r.status_code == 403 and r.json() == {"detail": ADMIN_MSG}
        assert calls == [("org-1", "salary_structure", True)]

    def test_valid_plan_is_unchanged(self, gate_app):
        client, calls, state = gate_app
        state["expired"] = False
        assert client.get("/by-path/org-1").json() == {"ok": True}
        assert client.get("/by-query?org_id=org-2").status_code == 200
        r = client.post("/by-body", json={"org_id": "org-3", "x": 1})
        assert r.status_code == 200 and r.json()["echo"] == {"org_id": "org-3", "x": 1}       # body still reaches the handler
        assert [c[0] for c in calls] == ["org-1", "org-2", "org-3"]

    def test_org_from_query_and_body(self, gate_app):
        client, calls, _ = gate_app
        assert client.get("/by-query?org_id=org-2").status_code == 403
        assert client.post("/by-body", json={"org_id": "org-3"}).status_code == 403
        assert [c[0] for c in calls] == ["org-2", "org-3"]

    def test_org_resolved_from_the_user(self, gate_app):
        client, calls, _ = gate_app
        assert client.get("/by-user/u1").status_code == 403
        assert client.post("/by-sender", json={"sender_id": "u1"}).status_code == 403
        assert client.post("/by-sender", json={"created_by": "u1"}).status_code == 403
        assert [c[0] for c in calls] == ["org-of-u1"] * 3

    def test_org_resolved_from_the_group(self, gate_app):
        client, calls, _ = gate_app
        assert client.get("/by-group/g1").status_code == 403
        assert calls == [("org-of-g1", "salary_structure", True)]

    def test_org_id_wins_over_user_id(self, gate_app):
        client, calls, _ = gate_app
        client.post("/by-body", json={"org_id": "org-3", "user_id": "u1"})
        assert calls[0][0] == "org-3"

    def test_unresolvable_request_is_not_gated(self, gate_app):
        client, calls, _ = gate_app
        assert client.get("/nothing").status_code == 200
        assert client.get("/by-user/ghost").status_code == 200
        assert client.get("/by-group/unknown").status_code == 200
        assert calls == []

    def test_form_uploads_are_resolved_and_use_the_staff_text(self, gate_app):
        client, calls, _ = gate_app
        r = client.post("/form", data={"sender_id": "u1"})
        assert r.status_code == 403 and r.json() == {"detail": STAFF_MSG} and calls == [("org-of-u1", "chat", False)]

    def test_malformed_json_does_not_crash_the_gate(self, gate_app):
        client, _, _ = gate_app
        r = client.post("/by-body", content=b"{bad", headers={"content-type": "application/json"})
        assert r.status_code == 422


# ── the real routers carry the gate ─────────────────────────────────────────
def gated(route):
    return any(getattr(d.dependency, "__qualname__", "").startswith("plan_gate.") for d in getattr(route, "dependencies", []))


def test_every_chat_route_is_gated_but_not_announcements_or_websockets():
    from router import chat
    paths = {}
    for route in chat.router.routes:
        if hasattr(route, "methods"):
            paths[route.path] = paths.get(route.path, False) or gated(route)
    chat_paths = [p for p in paths if p.startswith(("/chat/", "/group-chat/"))]
    assert len(chat_paths) >= 13
    for path in chat_paths:
        if path in ("/chat/list/{user_id}", "/group-chat/list/{user_id}"):
            continue                                    # these two call check_plan_feature(org_id) themselves
        assert paths[path], f"{path} is not plan-gated"
    for path in paths:
        if path.startswith("/announcement"):
            assert not paths[path], f"{path} should stay ungated (not a plan feature)"


def test_payroll_routers_are_gated():
    from router import incentive, salary, salary_payments, wages
    routers = [salary.router, salary_payments.router, incentive.router, *wages.routers]
    for r in routers:
        assert r.dependencies, r.prefix
    for r in (salary.router, salary_payments.router, incentive.router, wages.daily_router, wages.hourly_router, wages.wages_router):
        for route in r.routes:
            assert gated(route), route.path


def test_chat_route_answers_403_before_doing_any_work(monkeypatch):
    from router import chat

    async def fake_check(org_id, feature, is_admin=True):
        raise HTTPException(status_code=403, detail=STAFF_MSG)

    monkeypatch.setattr(gp, "check_plan_feature", fake_check)
    monkeypatch.setattr(gp, "_lookup_org_for_user", lambda uid: "org-1")

    async def must_not_run(*a, **k):
        raise AssertionError("handler ran")
    monkeypatch.setattr(chat, "search_users_for_chat", must_not_run)
    app = FastAPI()
    app.include_router(chat.router)
    r = TestClient(app).get("/chat/search/u1", params={"q": "ab"})
    assert r.status_code == 403 and r.json() == {"detail": STAFF_MSG}


def test_salary_payment_route_blocked_when_expired(monkeypatch):
    from router import salary_payments

    async def fake_check(org_id, feature, is_admin=True):
        assert (org_id, feature) == ("org-1", "salary_structure")
        raise HTTPException(status_code=403, detail=ADMIN_MSG)

    monkeypatch.setattr(gp, "check_plan_feature", fake_check)
    app = FastAPI()
    app.include_router(salary_payments.router)
    c = TestClient(app)
    assert c.get("/salary/monthly-list", params={"org_id": "org-1", "month": 5, "year": 2026}).status_code == 403
    assert c.post("/salary/mark-paid", json={"org_id": "org-1", "user_ids": ["u"], "month": 5, "year": 2026}).json() == {"detail": ADMIN_MSG}


# ── trial staff limit ───────────────────────────────────────────────────────
class StaffCur:
    def __init__(self, sub, count):
        self.sub, self.count = sub, count
        self.sql = []
        self._last = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        if "FROM subscription" in s:
            self._last = self.sub
        elif "COUNT(*)" in s:
            self._last = {"cnt": self.count}
        else:
            self._last = {"user_id": "dup"}          # the next check ("already exists") stops the call with a 400

    def fetchone(self):
        return self._last

    def close(self):
        pass


class TestTrialStaffLimit:
    def add(self, monkeypatch, sub, count):
        cur = StaffCur(sub, count)
        conn = Conn(cur)
        monkeypatch.setattr(asf, "get_db_connection", lambda: conn)
        monkeypatch.setattr(asf, "return_connection", lambda c: None)
        monkeypatch.setattr(asf, "ist_today", lambda: TODAY)
        data = asf.AddStaffRequest(name="A", emp_id="E1", mobile_number="9000000001", email="a@example.com", user_id="org-1")
        with pytest.raises(HTTPException) as e:
            run(asf.add_staff(data))
        return e.value, cur

    def test_trial_org_is_limited_to_ten(self, monkeypatch):
        err, cur = self.add(monkeypatch, sub=None, count=10)
        assert err.status_code == 403 and err.detail.startswith("Staff limit reached (10/10)")

    def test_trial_org_can_add_the_tenth(self, monkeypatch):
        err, _ = self.add(monkeypatch, sub=None, count=9)
        assert err.status_code == 400                       # passed the limit, stopped by the duplicate check

    def test_plan_limit_applies_when_there_is_an_active_plan(self, monkeypatch):
        assert self.add(monkeypatch, sub={"staff_limit": 25}, count=24)[0].status_code == 400
        err, _ = self.add(monkeypatch, sub={"staff_limit": 25}, count=25)
        assert err.status_code == 403 and "(25/25)" in err.detail

    def test_active_plan_lookup_uses_the_ist_date(self, monkeypatch):
        _, cur = self.add(monkeypatch, sub=None, count=0)
        sql, params = cur.sql[0]
        assert "end_date >= %s" in sql and params == ("org-1", TODAY)
