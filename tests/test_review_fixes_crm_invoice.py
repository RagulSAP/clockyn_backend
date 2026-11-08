"""DB-free tests for two review fixes (fake cursor):
1. CRM org scoping: update_work_status / update_lead_status / delete_category only touch rows of the caller's
   organisation; another organisation's id behaves as not found; work status never fans out to every assignment.
2. Invoice insert: a duplicate invoice_number retries with a fresh sequence value (3 retries), an ERROR with the order
   id is logged when every attempt fails, and the migration / DDL move the sequence past the highest existing number.
Run: python -m pytest tests/test_review_fixes_crm_invoice.py -q
"""
import asyncio
import logging
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import leads_functions as lf  # noqa: E402
from core_functions import gen_payment_functions as pf  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ───────────────────────── CRM ─────────────────────────

class FakeCursor:
    """Rows live in a tiny model; statements are recorded and matched by their table and predicate."""

    def __init__(self, leads=None, assignments=None, categories=None):
        self.leads = leads if leads is not None else [{"leads_id": "l1", "org_id": "org1", "status": "new"}]
        self.assignments = assignments if assignments is not None else [
            {"leads_id": "l1", "org_id": "org1", "user_id": "u1", "status": "pending"},
            {"leads_id": "l1", "org_id": "org1", "user_id": "u2", "status": "pending"},
        ]
        self.categories = categories if categories is not None else [{"category_id": "c1", "org_id": "org1"}]
        self.sql = []
        self.rowcount = 0
        self._one = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = tuple(params or ())
        assert s.count("%s") == len(p), (s, p)
        self.sql.append((s, p))
        self.rowcount = 0
        self._one = None
        if s.startswith("SELECT org_id FROM leads_info WHERE leads_id = %s"):
            rows = [r for r in self.leads if r["leads_id"] == p[0] and (len(p) == 1 or r["org_id"] == p[1])]
            self._one = {"org_id": rows[0]["org_id"]} if rows else None
        elif s.startswith("SELECT org_id FROM leads_category_info WHERE category_id = %s"):
            rows = [r for r in self.categories if r["category_id"] == p[0] and (len(p) == 1 or r["org_id"] == p[1])]
            self._one = {"org_id": rows[0]["org_id"]} if rows else None
        elif s.startswith("UPDATE leads_assignment_info SET status = %s"):
            assert "AND org_id = %s" in s and "AND user_id = %s" in s, s
            status, lid, uid, org = p
            for r in self.assignments:
                if (r["leads_id"], r["user_id"], r["org_id"]) == (lid, uid, org):
                    r["status"] = status
                    self.rowcount += 1
        elif s.startswith("UPDATE leads_info SET status = %s"):
            assert s.endswith("WHERE leads_id = %s AND org_id = %s"), s
            status, lid, org = p
            for r in self.leads:
                if (r["leads_id"], r["org_id"]) == (lid, org):
                    r["status"] = status
                    self.rowcount += 1
        elif s.startswith("DELETE FROM leads_category_info"):
            assert s.endswith("WHERE category_id = %s AND org_id = %s"), s
            before = len(self.categories)
            self.categories[:] = [r for r in self.categories if (r["category_id"], r["org_id"]) != p]
            self.rowcount = before - len(self.categories)
        else:
            raise AssertionError(f"unexpected SQL: {s}")

    def fetchone(self):
        return self._one

    def close(self):
        pass


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


@pytest.fixture
def db(monkeypatch):
    def install(cursor):
        conn = FakeConn(cursor)
        monkeypatch.setattr(lf, "get_db_connection", lambda: conn)
        monkeypatch.setattr(lf, "return_connection", lambda c: None)
        return conn

    return install


def test_work_status_changes_only_the_acting_users_assignment(db):
    cur = FakeCursor()
    conn = db(cur)
    out = run(lf.update_work_status("l1", "working", "u1"))
    assert out == {"success": True, "message": "Work status updated to working"}
    assert [(a["user_id"], a["status"]) for a in cur.assignments] == [("u1", "working"), ("u2", "pending")]
    upd = [(s, p) for s, p in cur.sql if s.startswith("UPDATE")]
    assert upd == [("UPDATE leads_assignment_info SET status = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE leads_id = %s AND user_id = %s AND org_id = %s", ("working", "l1", "u1", "org1"))]
    assert conn.commits == 1


def test_work_status_without_any_user_changes_nothing(db):
    cur = FakeCursor()
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.update_work_status("l1", "working"))
    assert e.value.status_code == 400
    assert cur.sql == [] and conn.commits == 0
    assert all(a["status"] == "pending" for a in cur.assignments)


def test_work_status_of_another_org_is_not_found(db):
    cur = FakeCursor()
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.update_work_status("l1", "working", "u1", org_id="org2"))
    assert e.value.status_code == 404 and e.value.detail == "Lead not found"
    assert not [s for s, _ in cur.sql if s.startswith("UPDATE")]
    assert conn.commits == 0 and conn.rollbacks >= 1
    assert all(a["status"] == "pending" for a in cur.assignments)


def test_work_status_org_is_derived_from_the_lead_and_matches_when_given(db):
    cur = FakeCursor()
    db(cur)
    run(lf.update_work_status("l1", "completed", "u2", org_id="org1"))
    assert cur.sql[0][1] == ("l1", "org1")                  # lookup by lead id + supplied org
    assert cur.sql[-1][1] == ("completed", "l1", "u2", "org1")
    cur = FakeCursor()
    db(cur)
    run(lf.update_work_status("l1", "completed", "u2"))
    assert cur.sql[0][1] == ("l1",)                         # derived from the lead row when absent
    assert cur.sql[-1][1][-1] == "org1"


def test_work_status_user_without_assignment_is_not_found(db):
    cur = FakeCursor()
    db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.update_work_status("l1", "working", "stranger"))
    assert e.value.status_code == 404


def test_lead_status_is_org_scoped(db):
    cur = FakeCursor()
    conn = db(cur)
    assert run(lf.update_lead_status("l1", "converted"))["success"] is True
    assert cur.sql[-1] == ("UPDATE leads_info SET status = %s, updated_at = CURRENT_TIMESTAMP "
                           "WHERE leads_id = %s AND org_id = %s", ("converted", "l1", "org1"))
    assert conn.commits == 1
    cur = FakeCursor()
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.update_lead_status("l1", "converted", org_id="org2"))
    assert e.value.status_code == 404 and cur.leads[0]["status"] == "new" and conn.commits == 0


def test_delete_category_is_org_scoped(db):
    cur = FakeCursor()
    conn = db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.delete_category("c1", org_id="org2"))
    assert e.value.status_code == 404 and e.value.detail == "Category not found"
    assert len(cur.categories) == 1 and conn.commits == 0
    assert not [s for s, _ in cur.sql if s.startswith("DELETE")]


def test_delete_category_in_own_org_and_derived_org(db):
    cur = FakeCursor()
    conn = db(cur)
    assert run(lf.delete_category("c1", org_id="org1")) == {"success": True, "message": "Category deleted successfully"}
    assert cur.categories == [] and conn.commits == 1
    assert cur.sql[-1][1] == ("c1", "org1")
    cur = FakeCursor()
    db(cur)
    run(lf.delete_category("c1"))
    assert cur.sql[0][1] == ("c1",) and cur.sql[-1][1] == ("c1", "org1") and cur.categories == []
    cur = FakeCursor(categories=[])
    db(cur)
    with pytest.raises(HTTPException) as e:
        run(lf.delete_category("missing"))
    assert e.value.status_code == 404


def test_crm_routes_forward_org_and_fall_back_to_the_token_user(monkeypatch):
    import router.leads as rl

    seen = {}

    def stub(name):
        async def f(*a, **k):
            seen[name] = a
            return {"success": True}
        return f

    for name in ("update_work_status", "update_lead_status", "delete_category"):
        monkeypatch.setattr(rl, name, stub(name))
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        uid = request.headers.get("x-test-user")
        request.state.identity = type("I", (), {"user_id": uid})() if uid else None
        return await call_next(request)

    app.include_router(rl.router)
    c = TestClient(app)
    assert c.put("/api/leads/l1/work-status", params={"work_status": "working", "user_id": "u1", "org_id": "o1"}).json() == {"success": True}
    assert seen["update_work_status"] == ("l1", "working", "u1", "o1", None)    # last: the caller's orgs (no token: None)
    c.put("/api/leads/l1/work-status", params={"work_status": "working"}, headers={"x-test-user": "tok-user"})
    assert seen["update_work_status"] == ("l1", "working", "tok-user", None, frozenset())   # stub identity has no org
    c.put("/api/leads/l1/work-status", params={"work_status": "working"})
    assert seen["update_work_status"] == ("l1", "working", None, None, None)
    c.put("/api/leads/l1/status", params={"status": "new", "org_id": "o1"})
    assert seen["update_lead_status"] == ("l1", "new", "o1", None)
    c.put("/api/leads/l1/status", params={"status": "new"})
    assert seen["update_lead_status"] == ("l1", "new", None, None)
    c.delete("/api/leads/categories/c1", params={"org_id": "o1"})
    assert seen["delete_category"] == ("c1", "o1", None)
    c.delete("/api/leads/categories/c1")
    assert seen["delete_category"] == ("c1", None, None)


# ───────────────────────── invoice insert ─────────────────────────

TODAY = date(2026, 11, 2)
ORDER = {"user_id": "u1", "org_id": "org1", "razorpay_order_id": "order_X1", "amount": 8496,
         "base_amount": Decimal("7200.00"), "gst_amount": Decimal("1296.00"), "coupon_code": None}


class UniqueViolationLike(Exception):
    pgcode = "23505"


class InvoiceCursor:
    """invoice_number values in `taken` raise a unique violation (aborting until ROLLBACK TO SAVEPOINT)."""

    def __init__(self, taken=(), start=0, fail_with=None, existing_payment=False, rollback_fails=False):
        self.taken = set(taken)
        self.seq = start
        self.fail_with = fail_with
        self.existing_payment = existing_payment
        self.rollback_fails = rollback_fails
        self.sql = []
        self.inserted = []
        self._row = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = tuple(params or ())
        self.sql.append((s, p))
        if s.startswith("ROLLBACK TO SAVEPOINT") and self.rollback_fails:
            raise RuntimeError("connection lost")
        if s.startswith("SELECT invoice_number FROM gen_invoices"):
            self._row = {"invoice_number": "INV-old"} if self.existing_payment else None
        elif s.startswith("SELECT nextval"):
            self.seq += 1
            self._row = {"n": self.seq}
        elif s.startswith("INSERT INTO gen_invoices"):
            if self.fail_with:
                raise self.fail_with
            if p[1] in self.taken:
                raise UniqueViolationLike("duplicate key value violates unique constraint uq_gen_invoices_invoice_number")
            self.inserted.append(p[1])
            self._row = {"invoice_number": p[1]}

    def fetchone(self):
        return self._row


def call(cursor):
    return pf._insert_invoice(cursor, ORDER, "pay_1", "Pro", 15, date(2027, 11, 2), "Tamil Nadu (33)", TODAY)


def test_invoice_happy_path_uses_one_nextval():
    cur = InvoiceCursor()
    number, amounts, _ = call(cur)
    assert number == "INV-2026-0001" and amounts["total_amount"] == Decimal("8496.00")
    assert len([s for s, _ in cur.sql if s.startswith("SELECT nextval")]) == 1
    assert cur.sql[0][0] == "SAVEPOINT invoice_sp" and cur.sql[-1][0] == "RELEASE SAVEPOINT invoice_sp"
    assert not [s for s, _ in cur.sql if s.startswith("ROLLBACK")]


def test_duplicate_number_retries_with_a_fresh_sequence_value():
    cur = InvoiceCursor(taken={"INV-2026-0001", "INV-2026-0002"})
    number, _, _ = call(cur)
    assert number == "INV-2026-0003"
    assert len([s for s, _ in cur.sql if s.startswith("SELECT nextval")]) == 3
    assert len([s for s, _ in cur.sql if s == "ROLLBACK TO SAVEPOINT invoice_sp"]) == 2
    assert cur.sql[-1][0] == "RELEASE SAVEPOINT invoice_sp"
    # the existence check ran once, not on every retry
    assert len([s for s, _ in cur.sql if s.startswith("SELECT invoice_number FROM gen_invoices")]) == 1


def test_three_retries_succeed_the_fourth_attempt(caplog):
    cur = InvoiceCursor(taken={f"INV-2026-{n:04d}" for n in (1, 2, 3)})
    with caplog.at_level(logging.ERROR):
        number, _, _ = call(cur)
    assert number == "INV-2026-0004"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_all_retries_fail_logs_an_error_with_the_order_id_and_returns_none(caplog):
    cur = InvoiceCursor(taken={f"INV-2026-{n:04d}" for n in range(1, 50)})
    with caplog.at_level(logging.ERROR):
        assert call(cur) is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and "order_X1" in errors[0].getMessage()
    assert len([s for s, _ in cur.sql if s.startswith("SELECT nextval")]) == 1 + pf.INVOICE_NUMBER_RETRIES
    assert cur.sql[-1][0] == "ROLLBACK TO SAVEPOINT invoice_sp"       # transaction usable: the activation still commits
    assert cur.inserted == []


def test_other_failures_are_not_retried_but_are_logged_as_errors(caplog):
    cur = InvoiceCursor(fail_with=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR):
        assert call(cur) is None
    assert len([s for s, _ in cur.sql if s.startswith("SELECT nextval")]) == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and "order_X1" in errors[0].getMessage()


def test_failed_savepoint_rollback_stops_and_logs(caplog):
    cur = InvoiceCursor(fail_with=UniqueViolationLike("dup"), rollback_fails=True)
    with caplog.at_level(logging.ERROR):
        assert call(cur) is None
    assert len([s for s, _ in cur.sql if s.startswith("SELECT nextval")]) == 1
    assert any("order_X1" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)


def test_existing_invoice_for_the_payment_is_a_quiet_noop(caplog):
    cur = InvoiceCursor(existing_payment=True)
    with caplog.at_level(logging.WARNING):
        assert call(cur) is None
    assert not [s for s, _ in cur.sql if s.startswith(("SELECT nextval", "INSERT"))]
    assert not caplog.records


# ───────── sequence catch-up in migration 031 and the DDL ─────────

SETVAL_BLOCK = re.compile(
    r"DO \$\$\s*BEGIN\s*IF EXISTS \(SELECT 1 FROM (?:clockyn_dev\.)?gen_invoices\) THEN\s*"
    r"PERFORM setval\('(?:clockyn_dev\.)?gen_invoice_seq', GREATEST\(\s*"
    r"COALESCE\(\(SELECT max\(substring\(invoice_number FROM '\(\[0-9\]\{1,15\}\)\$'\)::bigint\) "
    r"FROM (?:clockyn_dev\.)?gen_invoices\), 0\), 1\)\);\s*END IF;\s*END\s*\$\$;")


@pytest.mark.parametrize("path", [ROOT / "migrations" / "031_billing_server_pricing.sql", ROOT / "clockyn_ddl_latest.sql"])
def test_sequence_is_moved_past_existing_invoices(path):
    text = path.read_text(encoding="utf-8-sig")
    assert SETVAL_BLOCK.search(text), f"{path.name}: setval DO block missing or changed"
    assert text.index("gen_invoice_seq START 1") < SETVAL_BLOCK.search(text).start()


def test_setval_blocks_parse_with_the_postgres_parser():
    try:
        import pglast
    except ImportError:
        pytest.skip("pglast not installed")
    for path in (ROOT / "migrations" / "031_billing_server_pricing.sql", ROOT / "clockyn_ddl_latest.sql"):
        m = SETVAL_BLOCK.search(path.read_text(encoding="utf-8-sig"))
        assert pglast.parse_sql(m.group(0))
