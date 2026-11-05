"""DB-free tests for WP9 (CRM leads): no runtime DDL, one row per lead, duplicate-assign guard, my-leads filter,
persisted carry-forward, migration 029 text. The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_crm_leads.py -q
"""
import asyncio
import inspect
import re
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import leads_functions as lf  # noqa: E402

TODAY = date(2026, 11, 2)
MIGRATION = ROOT / "migrations" / "029_crm_leads_hardening.sql"


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


@pytest.fixture
def db(monkeypatch):
    """db(cursor) installs the fake connection into leads_functions and freezes 'today' (IST)."""
    holder = {}

    def install(cursor):
        conn = FakeConn(cursor)
        holder["conn"] = conn
        monkeypatch.setattr(lf, "get_db_connection", lambda: conn)
        monkeypatch.setattr(lf, "return_connection", lambda c: None)
        monkeypatch.setattr(lf, "_today_ist", lambda: TODAY)
        return conn

    return install


def assert_no_ddl(cursor):
    for sql, _ in cursor.sql:
        assert not re.match(r"\s*(CREATE|ALTER|DROP)\b", sql, re.I), sql


def check_placeholders(cursor):
    """psycopg2 contract: %s count == tuple length; %(name)s keys all present in the dict."""
    for sql, params in cursor.sql:
        if isinstance(params, dict):
            for key in re.findall(r"%\((\w+)\)s", sql):
                assert key in params, (key, sql)
        else:
            assert sql.count("%s") == len(params or ()), (sql, params)


LEAD = {"org_id": "org1"}
EMP = {"ok": 1}


# ───────── 1. no DDL anywhere ─────────

def test_source_has_no_ddl_strings():
    src = inspect.getsource(lf)
    for needle in ("CREATE TABLE", "ALTER TABLE", "CREATE INDEX", "DROP TABLE"):
        assert needle not in src.upper()


def test_no_handler_executes_ddl(db):
    """Run every handler through its main path and assert no statement is DDL."""
    cur = FakeCursor(
        one=[LEAD, EMP, None, None, None, None] * 8,
        many=[[{"id": 1, "user_id": "u1"}]] * 8,
    )
    db(cur)
    body = lf.LeadCreate(org_id="org1", name="n", mobile="9", address="a", category="General")
    calls = [
        lf.create_lead(body),
        lf.get_leads("org1"),
        lf.get_employee_leads("org1", "u1"),
        lf.assign_lead("l1", "org1", "u1"),
        lf.update_lead("l1", body),
        lf.get_categories("org1"),
        lf.create_category("org1", "Retail"),
        lf.update_lead_assignment("l1", "u1"),
        lf.update_work_status("l1", "working", "u1"),
        lf.update_lead_status("l1", "converted"),
        lf.get_my_carry_forward_requests("org1", "u1"),
        lf.get_carry_forward_requests("org1"),
    ]
    for c in calls:
        try:
            run(c)
        except HTTPException:
            pass
    assert cur.sql
    assert_no_ddl(cur)


def test_request_and_action_carry_forward_run_no_ddl(db):
    cur = FakeCursor(one=[LEAD, {"id": 1}, None])
    db(cur)
    run(lf.request_carry_forward("l1", "org1", "u1", "call back", "2026-11-03"))
    assert_no_ddl(cur)
    cur2 = FakeCursor(one=[{"request_id": "r", "leads_id": "l1", "user_id": "u1", "org_id": "org1", "request_date": date(2026, 11, 3), "status": "pending"}])
    db(cur2)
    run(lf.action_carry_forward("r", "approved", "adm"))
    assert_no_ddl(cur2)


# ───────── 2. one row per lead ─────────

def test_admin_list_sql_has_no_row_multiplying_join():
    sql = " ".join(lf.LIST_LEADS_SQL.split())
    assert "DISTINCT" not in sql.upper()
    # the assignment table is reached only through a LATERAL subquery limited to one row
    assert not re.search(r"JOIN leads_assignment_info", sql)
    assert "JOIN LATERAL ( SELECT x.user_id, x.status, x.updated_at FROM leads_assignment_info x" in sql
    assert "ORDER BY x.updated_at DESC NULLS LAST, x.id DESC LIMIT 1" in sql
    # users are looked up with scalar subqueries, never joined
    assert not re.search(r"JOIN users", sql)
    assert "l.status AS lead_status" in sql
    assert "has_pending_cfr" in sql and "assignees" in sql
    assert "l.created_date AS required_date" in sql
    assert "WHERE l.org_id = %(org)s" in sql


def test_employee_list_sql_has_no_row_multiplying_join():
    sql = " ".join(lf.EMPLOYEE_LEADS_SQL.split())
    assert not re.search(r"JOIN leads_assignment_info", sql)
    assert "JOIN LATERAL" in sql and "LIMIT 1" in sql
    assert "x.user_id = %(user)s" in sql
    assert "l.status AS lead_status" in sql
    assert "(l.created_date = %(today)s OR l.created_date IS NULL)" in sql


def test_carry_forward_list_sqls_do_not_multiply_rows():
    my = " ".join(lf.MY_CARRY_FORWARD_SQL.split())
    assert not re.search(r"JOIN leads_assignment_info", my)
    assert "LEFT JOIN LATERAL" in my and "LIMIT 1" in my
    assert "cf.created_at" in my  # additive submitted timestamp
    org = " ".join(lf.ORG_CARRY_FORWARD_SQL.split())
    assert not re.search(r"JOIN users", org)
    assert "AS employee_name" in org and "cf.actioned_at" in org


def test_get_leads_passes_rows_through_once_each(db):
    rows = [
        {"leads_id": "a", "lead_status": "new", "assigned_user_id": "u1", "assignees": [{"user_id": "u1"}, {"user_id": "u2"}]},
        {"leads_id": "b", "lead_status": "contacted", "assigned_user_id": None, "assignees": []},
    ]
    cur = FakeCursor(many=[rows])
    db(cur)
    out = run(lf.get_leads("org1"))
    assert out["success"] is True
    assert [r["leads_id"] for r in out["data"]] == ["a", "b"]
    assert out["data"][0]["lead_status"] == "new"
    assert cur.sql[0][1] == {"org": "org1"}
    check_placeholders(cur)


# ───────── 3. my leads: due today (IST) or undated ─────────

def test_my_leads_params_use_ist_today(db):
    cur = FakeCursor(many=[[{"leads_id": "a"}]])
    db(cur)
    out = run(lf.get_employee_leads("org1", "u1"))
    assert out["data"] == [{"leads_id": "a"}]
    sql, params = cur.sql[0]
    assert params == {"org": "org1", "user": "u1", "today": TODAY}
    check_placeholders(cur)


def test_today_ist_is_the_ist_calendar_day(monkeypatch):
    from datetime import datetime, timezone
    import core_functions.leads_functions as mod

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            # 2026-11-01 20:00 UTC is already 2026-11-02 01:30 in IST
            return datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(mod, "datetime", FakeDT)
    assert mod._today_ist() == date(2026, 11, 2)


# ───────── 4. duplicate assign guard ─────────

def test_assign_new_inserts_once_with_conflict_guard(db):
    cur = FakeCursor(one=[LEAD, EMP, None])
    conn = db(cur)
    out = run(lf.assign_lead("l1", "org1", "u1", 5, "d1", "go", None))
    assert out == {"success": True, "message": "Lead assigned successfully"}
    ins = cur.find("INSERT INTO leads_assignment_info")
    assert len(ins) == 1 and "ON CONFLICT DO NOTHING" in ins[0][0]
    assert ins[0][1][2] == "org1" and ins[0][1][5] == "u1" and ins[0][1][3] == 5
    assert conn.commits == 1
    assert not cur.find("UPDATE leads_info")  # no request_date given
    check_placeholders(cur)


def test_assign_same_staff_twice_is_a_noop(db):
    cur = FakeCursor(one=[LEAD, EMP, {"ok": 1}])
    conn = db(cur)
    out = run(lf.assign_lead("l1", "org1", "u1"))
    assert out == {"success": False, "message": "Lead is already assigned"}
    assert not cur.find("INSERT INTO leads_assignment_info")
    assert conn.commits == 0 and conn.rollbacks == 1
    # the existence check is scoped to lead + staff + org
    sel = cur.find("FROM leads_assignment_info WHERE leads_id = %s AND user_id = %s AND org_id = %s")
    assert sel and sel[0][1] == ("l1", "u1", "org1")


def test_assign_race_insert_without_row_is_reported_as_already_assigned(db):
    cur = FakeCursor(one=[LEAD, EMP, None])
    conn = db(cur)
    orig = cur.execute

    def execute(sql, params=None):
        orig(sql, params)
        if sql.lstrip().startswith("INSERT INTO leads_assignment_info"):
            cur.rowcount = 0

    cur.execute = execute
    out = run(lf.assign_lead("l1", "org1", "u1"))
    assert out["success"] is False and out["message"] == "Lead is already assigned"
    assert conn.commits == 0


def test_assign_unknown_lead_or_employee_is_404(db):
    cur = FakeCursor(one=[None])
    db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.assign_lead("nope", "org1", "u1"))
    assert e.value.status_code == 404 and e.value.detail == "Lead not found"
    assert cur.sql[0][1] == ("nope", "org1")  # org scoped lead lookup
    cur = FakeCursor(one=[LEAD, None])
    db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.assign_lead("l1", "org1", "ghost"))
    assert e.value.status_code == 404 and e.value.detail == "Employee not found"
    assert cur.sql[1][1] == ("ghost", "org1", "org1")


def test_assign_request_date_moves_the_due_date_and_bad_date_is_400(db):
    cur = FakeCursor(one=[LEAD, EMP, None])
    db(cur)
    run(lf.assign_lead("l1", "org1", "u1", request_date="2026-11-05"))
    upd = cur.find("UPDATE leads_info SET created_date")
    assert upd and upd[0][1] == (date(2026, 11, 5), "l1", "org1")
    with pytest.raises(HTTPException) as e:
        run(lf.assign_lead("l1", "org1", "u1", request_date="05/11/2026"))
    assert e.value.status_code == 400


def test_reassign_replaces_all_assignments_with_one(db):
    cur = FakeCursor(one=[LEAD, EMP], many=[[{"id": 9, "user_id": "u2"}, {"id": 8, "user_id": "u3"}]])
    conn = db(cur)
    run(lf.update_lead_assignment("l1", "u1", 5, "d1", "org1"))
    assert cur.find("DELETE FROM leads_assignment_info WHERE leads_id = %s AND org_id = %s")[0][1] == ("l1", "org1")
    ins = cur.find("INSERT INTO leads_assignment_info")
    assert len(ins) == 1 and ins[0][1][5] == "u1" and ins[0][1][2] == "org1"
    assert conn.commits == 1
    check_placeholders(cur)


def test_reassign_to_current_holder_keeps_row_and_removes_the_rest(db):
    cur = FakeCursor(one=[LEAD, EMP], many=[[{"id": 9, "user_id": "u1"}, {"id": 8, "user_id": "u3"}]])
    db(cur)
    run(lf.update_lead_assignment("l1", "u1"))
    dele = cur.find("DELETE FROM leads_assignment_info")
    assert len(dele) == 1 and dele[0][1] == ("l1", "org1", 9)
    assert not cur.find("INSERT INTO leads_assignment_info")
    assert cur.find("UPDATE leads_assignment_info SET branch_id")[0][1][-1] == 9


def test_reassign_unknown_lead_is_404(db):
    db(FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(lf.update_lead_assignment("nope", "u1", org_id="org1"))
    assert e.value.status_code == 404


# ───────── 5. carry forward persisted ─────────

def test_request_carry_forward_persists_status_and_defaults_to_tomorrow(db):
    cur = FakeCursor(one=[LEAD, {"id": 1}, None])
    conn = db(cur)
    out = run(lf.request_carry_forward("l1", "org1", "u1", "  call tomorrow  "))
    assert out["success"] is True and out["request_id"]
    ins = cur.find("INSERT INTO carry_forward_requests")[0][1]
    assert ins[3] == "u1" and ins[4] == "call tomorrow" and ins[5] == date(2026, 11, 3)
    upd = cur.find("UPDATE leads_assignment_info SET prev_status")
    assert upd and "status = 'carry_forward'" in upd[0][0]
    assert upd[0][1] == ("call tomorrow", date(2026, 11, 3), "l1", "u1", "org1")
    assert conn.commits == 1
    check_placeholders(cur)


def test_request_carry_forward_validation(db):
    for kwargs, code in [
        ({"description": "  "}, 400),
        ({"description": "x", "forward_date": "tomorrow"}, 400),
        ({"description": "x", "forward_date": "2026-11-01"}, 400),
    ]:
        db(FakeCursor())
        with pytest.raises(HTTPException) as e:
            run(lf.request_carry_forward("l1", "org1", "u1", **kwargs))
        assert e.value.status_code == code
    # not assigned to this employee
    cur = FakeCursor(one=[LEAD, None])
    db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.request_carry_forward("l1", "org1", "u1", "x"))
    assert e.value.status_code == 400
    # unknown lead
    db(FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(lf.request_carry_forward("l1", "org1", "u1", "x"))
    assert e.value.status_code == 404


def test_request_carry_forward_duplicate_pending_is_a_noop(db):
    cur = FakeCursor(one=[LEAD, {"id": 1}, {"request_id": "r0"}])
    conn = db(cur)
    out = run(lf.request_carry_forward("l1", "org1", "u1", "x"))
    assert out == {"success": False, "message": "A pending carry forward request already exists"}
    assert not cur.find("INSERT INTO carry_forward_requests") and conn.commits == 0


REQ = {"request_id": "r1", "leads_id": "l1", "user_id": "u1", "org_id": "org1", "request_date": date(2026, 11, 3), "status": "pending"}


def test_approve_moves_due_date_and_returns_assignment_to_pending(db, monkeypatch):
    monkeypatch.delenv("CRM_CARRY_FORWARD_UNASSIGN", raising=False)
    cur = FakeCursor(one=[dict(REQ)])
    conn = db(cur)
    out = run(lf.action_carry_forward("r1", "approved", "adm"))
    assert out == {"success": True, "message": "Request approved"}
    assert cur.find("UPDATE leads_info SET created_date")[0][1] == (date(2026, 11, 3), "l1", "org1")
    assert not cur.find("DELETE FROM leads_assignment_info")
    upd = cur.find("UPDATE leads_assignment_info SET status = 'pending'")
    assert upd and upd[0][1] == ("l1", "u1", "org1")
    assert conn.commits == 1
    check_placeholders(cur)


def test_approve_with_unassign_flag_removes_the_assignment(db, monkeypatch):
    monkeypatch.setenv("CRM_CARRY_FORWARD_UNASSIGN", "1")
    cur = FakeCursor(one=[dict(REQ)])
    db(cur)
    run(lf.action_carry_forward("r1", "approved", "adm"))
    assert cur.find("DELETE FROM leads_assignment_info WHERE leads_id = %s AND user_id = %s AND org_id = %s")
    assert not cur.find("UPDATE leads_assignment_info SET status = 'pending'")


def test_reject_restores_previous_status(db):
    cur = FakeCursor(one=[dict(REQ)])
    db(cur)
    run(lf.action_carry_forward("r1", "rejected", "adm"))
    upd = cur.find("UPDATE leads_assignment_info SET status = CASE")
    assert upd and "prev_status" in upd[0][0] and upd[0][1] == ("l1", "u1", "org1")
    assert not cur.find("UPDATE leads_info")


def test_action_guards(db):
    with pytest.raises(HTTPException) as e:
        run(lf.action_carry_forward("r1", "maybe", "adm"))
    assert e.value.status_code == 400
    db(FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(lf.action_carry_forward("r1", "approved", "adm"))
    assert e.value.status_code == 404
    done = dict(REQ, status="approved")
    cur = FakeCursor(one=[done])
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.action_carry_forward("r1", "approved", "adm"))
    assert e.value.status_code == 409 and not cur.find("UPDATE") and conn.commits == 0
    # lost the race: guarded UPDATE touched no row
    cur = FakeCursor(one=[dict(REQ)], rowcount=0)
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.action_carry_forward("r1", "approved", "adm"))
    assert e.value.status_code == 409 and conn.commits == 0


# ───────── other handlers ─────────

def test_update_lead_keeps_date_when_required_date_not_sent(db):
    cur = FakeCursor(one=[LEAD, {"category_id": "c1"}])
    db(cur)
    body = lf.LeadCreate(org_id="org1", name="n", mobile="9", address="a", category="Retail")
    run(lf.update_lead("l1", body))
    upd = cur.find("UPDATE leads_info SET client_name")
    assert upd and "created_date" not in upd[0][0] and upd[0][1][-2:] == ("l1", "org1")
    cur = FakeCursor(one=[LEAD, {"category_id": "c1"}])
    db(cur)
    body = lf.LeadCreate(org_id="org1", name="n", mobile="9", address="a", category="Retail", required_date=None)
    run(lf.update_lead("l1", body))
    upd = cur.find("UPDATE leads_info SET client_name")
    assert "created_date = %s" in upd[0][0] and upd[0][1][5] is None  # explicit null clears the date
    check_placeholders(cur)


def test_update_lead_other_org_is_404(db):
    cur = FakeCursor(one=[None])
    db(cur)
    body = lf.LeadCreate(org_id="org2", name="n", mobile="9", address="a", category="Retail")
    with pytest.raises(HTTPException) as e:
        run(lf.update_lead("l1", body))
    assert e.value.status_code == 404 and cur.sql[0][1] == ("l1", "org2")


def test_create_lead_writes_new_status_and_parses_date(db):
    cur = FakeCursor(one=[{"category_id": "c1"}])
    db(cur)
    body = lf.LeadCreate(org_id="org1", name="n", mobile="9", address="a", category="Retail", required_date="2026-11-09")
    out = run(lf.create_lead(body))
    assert out["success"] is True and out["leads_id"]
    ins = cur.find("INSERT INTO leads_info")[0]
    assert "'new'" in ins[0] and ins[1][-1] == date(2026, 11, 9)
    check_placeholders(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.create_lead(lf.LeadCreate(org_id="org1", name="n", mobile="9", address="a", category="R", required_date="nope")))
    assert e.value.status_code == 400


def test_delete_lead_removes_children_in_org(db):
    cur = FakeCursor(one=[LEAD])
    conn = db(cur)
    run(lf.delete_lead("l1", "org1"))
    stmts = [s for s, _ in cur.sql if s.startswith("DELETE")]
    assert [s.split()[2] for s in stmts] == ["carry_forward_requests", "leads_assignment_info", "leads_info"]
    assert all(p == ("l1", "org1") for s, p in cur.sql if s.startswith("DELETE"))
    assert conn.commits == 1
    db(FakeCursor(one=[None]))
    with pytest.raises(HTTPException) as e:
        run(lf.delete_lead("l1", "org1"))
    assert e.value.status_code == 404


def test_status_validation_and_widths(db):
    for fn, arg in ((lf.update_lead_status, "pending"), (lf.update_work_status, "done")):
        with pytest.raises(HTTPException) as e:
            run(fn("l1", arg))
        assert e.value.status_code == 400
    cur = FakeCursor(one=[LEAD])
    db(cur)
    run(lf.update_lead_status("l1", "not_interested"))
    assert cur.sql[-1][1] == ("not_interested", "l1", "org1")      # org-scoped since the review fix
    assert max(len(s) for s in lf.PIPELINE_STATUSES + lf.WORK_STATUSES) <= 20
    cur = FakeCursor(one=[LEAD])
    db(cur)
    run(lf.update_work_status("l1", "Carry Forward", "u1"))
    assert cur.sql[-1][1] == ("carry_forward", "l1", "u1", "org1")
    db(FakeCursor(one=[LEAD], rowcount=0))
    with pytest.raises(HTTPException) as e:
        run(lf.update_lead_status("l1", "new"))
    assert e.value.status_code == 404


def test_create_category_rejects_duplicates_case_insensitively(db):
    cur = FakeCursor(one=[{"category_id": "c1"}])
    conn = db(cur)
    out = run(lf.create_category("org1", " retail "))
    assert out == {"success": False, "message": "Category already exists"}
    assert cur.sql[0][1] == ("retail", "org1") and conn.commits == 0
    cur = FakeCursor(one=[None])
    db(cur)
    assert run(lf.create_category("org1", "Retail"))["success"] is True
    assert run(lf.create_category("org1", "  "))["success"] is False


def test_unexpected_db_error_is_500_without_internals(db):
    class Boom(FakeCursor):
        def execute(self, sql, params=None):
            raise RuntimeError("secret table detail")

    db(Boom())
    with pytest.raises(HTTPException) as e:
        run(lf.get_leads("org1"))
    assert e.value.status_code == 500 and "secret" not in e.value.detail


# ───────── HTTP routes ─────────

@pytest.fixture
def client(monkeypatch):
    import router.leads as rl

    async def ok_gate(*a, **k):
        return None

    monkeypatch.setattr(rl, "check_plan_feature", ok_gate)
    seen = {}

    def stub(name, result):
        async def f(*a, **k):
            seen[name] = a
            return result
        return f

    monkeypatch.setattr(rl, "assign_lead", stub("assign", {"success": False, "message": "Lead is already assigned"}))
    monkeypatch.setattr(rl, "get_employee_leads", stub("employee", {"success": True, "data": []}))
    monkeypatch.setattr(rl, "update_lead_assignment", stub("reassign", {"success": True, "message": "ok"}))
    monkeypatch.setattr(rl, "delete_lead", stub("delete", {"success": True, "message": "ok"}))
    app = FastAPI()
    app.include_router(rl.router)
    return TestClient(app), seen


def test_routes_forward_contract_params(client):
    c, seen = client
    r = c.post("/api/leads/assign", params={"leads_id": "l1", "org_id": "org1", "user_id": "u1", "request_date": "2026-11-05"})
    assert r.status_code == 200 and r.json() == {"success": False, "message": "Lead is already assigned"}
    assert seen["assign"] == ("l1", "org1", "u1", None, None, "", "2026-11-05")
    assert c.get("/api/leads/employee/org1/u1").json() == {"success": True, "data": []}
    assert seen["employee"] == ("org1", "u1")
    c.put("/api/leads/assignment/l1", params={"user_id": "u2", "org_id": "org1"})
    assert seen["reassign"] == ("l1", "u2", None, None, "org1", None)   # last: the caller's orgs (no token: None)
    c.delete("/api/leads/l1", params={"org_id": "org1"})
    assert seen["delete"] == ("l1", "org1", None)
    c.delete("/api/leads/l1")
    assert seen["delete"] == ("l1", None, None)


# ───────── migration 029 text ─────────

def _migration():
    return MIGRATION.read_text(encoding="utf-8")


def test_migration_text_is_idempotent_style_and_widens_status():
    sql = _migration()
    assert "varchar(64)" in sql and "ALTER COLUMN status TYPE varchar(64)" in sql
    assert "ADD COLUMN IF NOT EXISTS prev_status" in sql
    assert "CREATE TABLE IF NOT EXISTS carry_forward_requests" in sql
    assert "punchapp_prod" not in sql  # unqualified: the schema comes from the search_path
    assert not re.search(r"(?im)^\s*(DELETE|TRUNCATE|DROP)\b", sql)
    # unique index only inside the duplicate check, with a NOTICE
    assert "RAISE NOTICE" in sql and "HAVING count(*) > 1" in sql
    idx = [m.start() for m in re.finditer(r"CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_assignment_lead_user", sql)]
    assert len(idx) == 1 and sql.rfind("DO $$", 0, idx[0]) > sql.rfind("RAISE NOTICE", 0, idx[0]) - 800


def test_migration_parses_with_the_postgres_parser():
    if not _try_pglast():
        pytest.skip("pglast not available")
    import pglast
    stmts = pglast.parse_sql(_migration())
    assert len(stmts) >= 10
    # the status ALTER and the unique index live inside DO blocks: parse their bodies as plpgsql
    do_bodies = [s for s in stmts if type(s.stmt).__name__ == "DoStmt"]
    assert len(do_bodies) == 2


def _try_pglast():
    try:
        import pglast  # noqa: F401
        return True
    except ImportError:
        pass
    for p in (r"C:\Users\RSITHU~1\AppData\Local\Temp\claude\c--POC-pa-frontend\90c115e7-e99e-4778-ad6c-7055cb1620cc\scratchpad\pylibs",):
        if Path(p).exists():
            sys.path.insert(0, p)
    try:
        import pglast  # noqa: F401
        return True
    except ImportError:
        return False


def test_dedupe_script_is_manual_and_transactional():
    text = (ROOT / "scripts" / "dedupe_leads.sql").read_text(encoding="utf-8")
    first_select = text.index("SELECT leads_id, user_id, count(*)")
    assert first_select < text.index("BEGIN;") < text.index("DELETE FROM leads_assignment_info")
    assert text.rstrip().endswith("ROLLBACK;")  # never commits by itself
    assert "migrate.py never runs" in text


def test_ddl_file_matches_what_the_code_needs():
    ddl = (ROOT / "punchapp_ddl_latest.sql").read_text(encoding="utf-8")
    assert "prev_status varchar(64)" in ddl
    assert "ux_leads_assignment_lead_user" in ddl
    li = ddl[ddl.index("CREATE TABLE punchapp_prod.leads_info"):]
    li = li[: li.index(");")]
    assert "status varchar(64) DEFAULT 'new'" in li
    assert "CREATE TABLE punchapp_prod.carry_forward_requests" in ddl
