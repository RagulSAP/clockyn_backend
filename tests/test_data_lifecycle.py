"""DB-free tests for the data lifecycle (WP6, planner D-14): the retention purge of soft-deleted staff, the PII
anonymisation, the account deletion in one transaction and the operator route. The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_data_lifecycle.py -q
"""
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import data_lifecycle as dl  # noqa: E402

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


class Events(list):
    """One shared, ordered log of statements, commits, rollbacks and S3 calls."""


class FakeCursor:
    """Statements are logged to `events`. `routes` = [(substring, rows)]: the FIRST route whose text is in the statement
    supplies fetchone / fetchall (rows is a list, or a callable(params) -> list). `fail_on` = substring that raises."""

    def __init__(self, events, routes=(), fail_on=None, connection=None):
        self.events = events
        self.routes = list(routes)
        self.fail_on = fail_on
        self.last = []
        self.connection = connection
        self.sql = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        self.events.append(("sql", s, params))
        if self.fail_on and self.fail_on in s:
            raise RuntimeError("boom")
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


class FakeConn:
    def __init__(self, events):
        self.events = events

    def commit(self):
        self.events.append(("commit",))

    def rollback(self):
        self.events.append(("rollback",))


def make(events, routes=(), fail_on=None):
    conn = FakeConn(events)
    return FakeCursor(events, routes, fail_on, conn), conn


@pytest.fixture
def s3(monkeypatch):
    calls = []
    events = []

    def fake_assets(owner, urls=None):
        calls.append((owner, list(urls or [])))
        events.append(("s3", owner))
        return {}

    monkeypatch.setattr(dl, "delete_s3_assets", fake_assets)
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: {"ok": True, "removed": 0, "errors": []})
    monkeypatch.setattr(dl, "delete_org_logo", lambda org: calls.append(("logo", org)))
    return calls


def deletes(cursor):
    return [s for s, _p in cursor.sql if s.startswith("DELETE FROM")]


def candidate_routes(*targets, history=(), subordinates=0, urls=()):
    """routes for purge: candidate list, per-user history flag, manager subordinate count, harvested urls."""
    return [
        ("FROM users WHERE status = 2", [dict(t) for t in targets]),
        ("SELECT COUNT(*) AS cnt FROM users WHERE parent_id", [{"cnt": subordinates}]),
        ("EXISTS (SELECT 1 FROM salary_payments", lambda p: [{"has_history": p[0] in history}]),
        ("SELECT punchinface", [{"punchinface": u, "punchoutface": None} for u in urls]),
    ]


# ------------------------------------------------------------------------------------------------ pure helpers
def test_placeholders_are_unique_and_fit_the_columns():
    uid = "123e4567-e89b-12d3-a456-426614174000"
    assert dl.anonymous_mobile(uid) == "deleted-" + uid and dl.anonymous_mobile("a") != dl.anonymous_mobile("b")
    mail = dl.anonymous_email(uid)
    assert mail.endswith("@x.invalid") and len(mail) <= 50 and uid in mail
    assert len(dl.anonymous_email("x" * 64)) <= 50          # users.email is varchar(50)
    assert dl.anonymous_email("a") != dl.anonymous_email("b")


@pytest.mark.parametrize("days", [0, -1, 3651, "abc", None])
def test_bad_retention_days_are_rejected(days):
    cur, _ = make(Events())
    with pytest.raises(ValueError):
        dl.purge_deleted_staff(cur, days=days)
    assert cur.sql == []


def test_every_step_has_one_placeholder_per_user_id_and_known_kind():
    for kind, sql in dl.STAFF_STEPS:
        assert kind in (dl.PRIVATE, dl.HISTORY) and sql.startswith("DELETE FROM") and sql.count("%s") in (1, 2)
    # payroll history is never a private step; chat and tokens always are
    kinds = {sql: kind for kind, sql in dl.STAFF_STEPS}
    assert kinds["DELETE FROM payroll WHERE user_id = %s"] == dl.HISTORY
    assert kinds["DELETE FROM attendance WHERE user_id = %s"] == dl.HISTORY
    assert kinds["DELETE FROM chat WHERE sender_id = %s OR receiver_id = %s"] == dl.PRIVATE
    assert kinds["DELETE FROM user_push_tokens WHERE user_id = %s"] == dl.PRIVATE


# ------------------------------------------------------------------------------------------------ dry run
def test_dry_run_counts_and_writes_nothing(s3):
    events = Events()
    targets = [{"user_id": "u1", "role_id": 3}, {"user_id": "u2", "role_id": 3}, {"user_id": "m1", "role_id": 2}]
    routes = candidate_routes(*targets, history={"u2"})
    # m1 is a manager with active staff: skipped (the count route answers for every manager lookup)
    routes[1] = ("SELECT COUNT(*) AS cnt FROM users WHERE parent_id", lambda p: [{"cnt": 4 if p[0] == "m1" else 0}])
    cur, conn = make(events, routes)
    out = dl.purge_deleted_staff(cur, days=30, now=NOW)            # dry_run defaults to True

    assert out["dry_run"] is True and out["eligible"] == 3
    assert (out["delete"], out["anonymise"]) == (1, 1)
    assert out["skipped"] == [{"user_id": "m1", "reason": "manager_has_subordinates"}]
    assert out["purged"] == [] and out["failed"] == []
    assert not deletes(cur) and not cur.find("UPDATE ") and ("commit",) not in events and not s3
    select_sql, params = cur.sql[0]
    assert "status = 2" in select_sql and "role_id IN (2, 3)" in select_sql and "updated_at < %s" in select_sql
    assert params == (NOW - timedelta(days=30), dl.ANON_NAME, dl.DEFAULT_BATCH)
    assert out["cutoff"] == (NOW - timedelta(days=30)).isoformat()


def test_default_days_is_thirty_and_custom_days_move_the_cutoff(s3):
    cur, _ = make(Events(), candidate_routes())
    assert dl.purge_deleted_staff(cur, now=NOW)["days"] == 30
    cur2, _ = make(Events(), candidate_routes())
    dl.purge_deleted_staff(cur2, days=90, now=NOW)
    assert cur2.sql[0][1][0] == NOW - timedelta(days=90)


# ------------------------------------------------------------------------------------------------ real purge
def test_full_delete_runs_the_shared_step_list_in_order_then_unassigns_and_removes_the_row(s3):
    events = Events()
    cur, conn = make(events, candidate_routes({"user_id": "u1", "role_id": 3}, urls=["https://b/x.jpg"]))
    out = dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)

    assert out["purged"] == [{"user_id": "u1", "action": "delete"}] and out["delete"] == 1 and out["failed"] == []
    expected = [sql for _k, sql in dl.STAFF_STEPS] + [
        "UPDATE users SET manager_id = NULL WHERE manager_id = %s", "DELETE FROM users WHERE user_id = %s"]
    ran = [s for s, _p in cur.sql if s.startswith("DELETE FROM") or "SET manager_id = NULL" in s]
    assert ran == expected
    for s, p in cur.sql:
        if s in expected:
            assert p == ("u1",) * s.count("%s")           # every statement is scoped to exactly this user
    # users row is the last write; the attendance rows (and payroll) are deleted on a full delete
    assert ran[-1] == "DELETE FROM users WHERE user_id = %s"
    assert "DELETE FROM attendance WHERE user_id = %s" in ran and "DELETE FROM payroll WHERE user_id = %s" in ran


def test_permanent_delete_uses_the_same_statements_as_the_purge(monkeypatch, s3):
    from core_functions import add_staff_function as af

    ev1 = Events()
    cur1, _ = make(ev1, candidate_routes({"user_id": "u1", "role_id": 3}))
    dl.purge_deleted_staff(cur1, days=30, dry_run=False, now=NOW)
    purge_stmts = [e[1] for e in ev1 if e[0] == "sql" and (e[1].startswith("DELETE") or "SET manager_id" in e[1])]

    ev2 = Events()
    cur2 = FakeCursor(ev2, [("SELECT user_id, name, role_id, parent_id", [{"user_id": "u1", "name": "A", "role_id": 3, "parent_id": "o"}])],
                      connection=FakeConn(ev2))
    conn2 = cur2.connection
    conn2.cursor = lambda cursor_factory=None: cur2
    monkeypatch.setattr(af, "get_db_connection", lambda: conn2)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    res = asyncio.run(af.permanently_delete_staff("u1", 1))
    assert res["success"] is True
    perm_stmts = [e[1] for e in ev2 if e[0] == "sql" and (e[1].startswith("DELETE") or "SET manager_id" in e[1])]
    assert perm_stmts == purge_stmts                       # one list of tables, two callers
    assert ("commit",) in ev2


def test_s3_cleanup_runs_after_the_commit_with_the_harvested_urls(s3, monkeypatch):
    events = Events()
    cur, conn = make(events, candidate_routes({"user_id": "u1", "role_id": 3}, urls=["https://b/p1.jpg", "https://b/p2.jpg"]))
    monkeypatch.setattr(dl, "delete_s3_assets", lambda owner, urls=None: events.append(("s3", owner, list(urls))) or {})
    dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)
    kinds = [e[0] for e in events]
    assert kinds.index("commit") < kinds.index("s3") and kinds.count("s3") == 1
    assert ("s3", "u1", ["https://b/p1.jpg", "https://b/p2.jpg"]) in events


def test_staff_with_payroll_history_are_anonymised_not_deleted(s3):
    events = Events()
    cur, conn = make(events, candidate_routes({"user_id": "u2", "role_id": 3}, history={"u2"}, urls=["https://b/f.jpg"]))
    out = dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)

    assert out["purged"] == [{"user_id": "u2", "action": "anonymise"}] and out["anonymise"] == 1 and out["delete"] == 0
    ran = deletes(cur)
    assert ran == [sql for kind, sql in dl.STAFF_STEPS if kind == dl.PRIVATE]          # only the private steps
    assert "DELETE FROM users WHERE user_id = %s" not in ran
    assert not cur.find("DELETE FROM attendance") and not cur.find("DELETE FROM payroll")

    nulls = cur.find("UPDATE attendance SET punchinface = NULL, punchoutface = NULL")
    assert nulls and nulls[0][1] == ("u2",)
    blank = cur.find("UPDATE users_additional_info SET")[0]
    assert "acc_no = ''" in blank[0] and "aadhaar_no = NULL" in blank[0] and "dob = NULL" in blank[0]
    assert blank[1] == (NOW, "u2")
    anon_sql, anon_params = cur.find("UPDATE users SET name = %s")[0]
    assert anon_params == (dl.ANON_NAME, "deleted-u2", "del-u2@x.invalid", NOW, "u2")
    for needle in ("device_token = NULL", "device_token_expires_at = NULL", "push_token = ''", "status = 2",
                   "auth_epoch = COALESCE(auth_epoch, 0) + 1", "meta_campaign_id = NULL"):
        assert needle in anon_sql
    assert "role_id" not in anon_sql and "parent_id" not in anon_sql and "emp_id" not in anon_sql   # references stay
    assert ("commit",) in events and s3 == [("u2", ["https://b/f.jpg"])]


def test_failure_rolls_back_that_staff_member_and_the_purge_continues(s3):
    events = Events()
    routes = candidate_routes({"user_id": "bad", "role_id": 3}, {"user_id": "good", "role_id": 3})
    cur, conn = make(events, routes)
    real_execute = cur.execute

    def execute(sql, params=None):
        if params == ("bad",) and "DELETE FROM payroll" in " ".join(sql.split()):
            real_execute(sql, params)
            raise RuntimeError("boom")
        return real_execute(sql, params)

    cur.execute = execute
    out = dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)

    assert out["failed"] == ["bad"] and out["purged"] == [{"user_id": "good", "action": "delete"}]
    assert out["delete"] == 1                                   # the failed one is not counted as done
    kinds = [e[0] for e in events if e[0] in ("commit", "rollback", "s3")]
    assert kinds[0] == "rollback" and kinds.count("commit") == 1       # bad: rolled back; good: committed
    assert [c[0] for c in s3] == ["good"]                       # no S3 deletion for the one that rolled back
    bad_users_delete = [e for e in events if e[0] == "sql" and e[1] == "DELETE FROM users WHERE user_id = %s" and e[2] == ("bad",)]
    assert not bad_users_delete


def test_classification_failure_is_reported_not_raised(s3):
    events = Events()
    routes = candidate_routes({"user_id": "u1", "role_id": 3})
    cur, conn = make(events, routes, fail_on="EXISTS (SELECT 1 FROM salary_payments")
    out = dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)
    assert out["failed"] == ["u1"] and out["purged"] == [] and not deletes(cur)


def test_the_batch_limit_is_passed_to_the_selection(s3):
    cur, _ = make(Events(), candidate_routes())
    dl.purge_deleted_staff(cur, days=30, now=NOW, limit=7)
    assert cur.sql[0][1][-1] == 7


# ------------------------------------------------------------------------------------------------ account deletion
def account_routes():
    return [
        ("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}, {"org_id": "orgB"}]),
        ("FROM users WHERE parent_id = %s OR parent_id IN", lambda p: [{"user_id": "s-" + p[0]}, {"user_id": "owner"}]),
        ("SELECT punchinface", [{"punchinface": "https://b/a.jpg", "punchoutface": "https://b/b.jpg"}]),
    ]


def test_anonymise_account_statements(s3):
    events = Events()
    cur, _ = make(events, account_routes())
    summary = dl.anonymise_account(cur, "owner", NOW)

    assert summary["org_ids"] == ["orgA", "orgB"] and summary["user_ids"] == ["s-orgA", "s-orgB", "owner"]
    anon_users = [p[-1] for s, p in cur.sql if s.startswith("UPDATE users SET name = %s")]
    assert anon_users == ["s-orgA", "s-orgB", "owner"]                          # the owner is not processed twice
    assert all(p[0] == dl.ANON_NAME for s, p in cur.sql if s.startswith("UPDATE users SET name = %s"))
    mobiles = {p[1] for s, p in cur.sql if s.startswith("UPDATE users SET name = %s")}
    assert mobiles == {"deleted-s-orgA", "deleted-s-orgB", "deleted-owner"}      # non-colliding
    # nothing is hard-deleted in the users / billing / attendance / payroll tables
    assert not cur.find("DELETE FROM users ") and not cur.find("DELETE FROM attendance")
    assert not cur.find("DELETE FROM payroll") and not cur.find("gen_") and not cur.find("subscription")
    # org + leads + company rows are blanked in place
    org_update = cur.find("UPDATE org_info SET org_name")
    assert [p[-1] for _s, p in org_update] == ["orgA", "orgB"] and all(p[0] == dl.ANON_ORG_NAME for _s, p in org_update)
    assert "org_address = ''" in org_update[0][0] and "org_gst = NULL" in org_update[0][0]
    leads = cur.find("UPDATE leads_info SET")
    assert [p[-1] for _s, p in leads] == ["orgA", "orgB"] and "mobile_number = NULL" in leads[0][0]
    company = cur.find("UPDATE company_additional_info SET")
    assert len(company) == 3 and "WHERE user_id = %s" in company[-1][0]         # two orgs + the owner's own row
    req = cur.find("UPDATE account_deletion_requests SET")[0]
    assert "status = 'deleted'" in req[0] and "mobile_number = NULL" in req[0] and req[1][-1] == "owner"
    # S3 work is only reported for the caller to run after the commit
    assert [u for u, _urls in summary["s3"]] == ["s-orgA", "s-orgB", "owner"]
    assert summary["s3"][0][1] == ["https://b/a.jpg", "https://b/b.jpg"] and not s3


def test_finish_account_cleanup_calls_the_s3_helpers(s3):
    dl.finish_account_cleanup({"s3": [("u1", ["k1"]), ("u2", [])], "org_ids": ["orgA"]})
    assert s3 == [("u1", ["k1"]), ("u2", []), ("logo", "orgA")]


def _company(monkeypatch, cursor, conn):
    from core_functions import company_functions as cf
    conn.cursor = lambda cursor_factory=None: cursor
    monkeypatch.setattr(cf, "get_db_connection", lambda: conn)
    monkeypatch.setattr(cf, "return_connection", lambda c: None)
    monkeypatch.setattr(cf, "get_utc_now", lambda: NOW)
    return cf


def test_delete_account_flag_on_is_one_transaction_with_s3_after_the_commit(monkeypatch, s3):
    monkeypatch.setenv("ACCOUNT_DELETE_ANONYMISE", "1")
    events = Events()
    routes = account_routes() + [("SELECT user_id FROM users WHERE user_id = %s", [{"user_id": "owner"}])]
    routes = [routes[-1]] + routes[:-1]
    cur, conn = make(events, routes)
    cf = _company(monkeypatch, cur, conn)
    monkeypatch.setattr(dl, "delete_s3_assets", lambda owner, urls=None: events.append(("s3", owner)) or {})
    res = asyncio.run(cf.delete_user_account("owner"))

    assert res["success"] is True and "anonymised" in res["message"]
    kinds = [e[0] for e in events if e[0] in ("commit", "rollback", "s3")]
    assert kinds.count("commit") == 1 and "rollback" not in kinds
    assert kinds.index("commit") < kinds.index("s3")
    assert not cur.find("DELETE FROM users ") and cur.find("UPDATE users SET name = %s")


def test_delete_account_flag_on_failure_rolls_everything_back_and_skips_s3(monkeypatch, s3):
    monkeypatch.setenv("ACCOUNT_DELETE_ANONYMISE", "1")
    events = Events()
    routes = [("SELECT user_id FROM users WHERE user_id = %s", [{"user_id": "owner"}])] + account_routes()
    cur, conn = make(events, routes, fail_on="UPDATE org_info SET org_name")
    cf = _company(monkeypatch, cur, conn)
    with pytest.raises(HTTPException) as e:
        asyncio.run(cf.delete_user_account("owner"))
    assert e.value.status_code == 500 and "boom" not in str(e.value.detail)
    kinds = [x[0] for x in events if x[0] in ("commit", "rollback", "s3")]
    assert kinds == ["rollback"] and not s3


def test_delete_account_flag_on_unknown_user_is_404(monkeypatch, s3):
    monkeypatch.setenv("ACCOUNT_DELETE_ANONYMISE", "1")
    events = Events()
    cur, conn = make(events, [("SELECT user_id FROM users WHERE user_id = %s", [])])
    cf = _company(monkeypatch, cur, conn)
    with pytest.raises(HTTPException) as e:
        asyncio.run(cf.delete_user_account("nobody"))
    assert e.value.status_code == 404 and ("rollback",) in events and ("commit",) not in events


def test_delete_account_flag_off_is_the_hard_delete_in_one_commit(monkeypatch, s3):
    monkeypatch.delenv("ACCOUNT_DELETE_ANONYMISE", raising=False)
    events = Events()
    cur, conn = make(events, [("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}]),
                              ("SELECT user_id FROM users WHERE parent_id", [{"user_id": "s1"}])])
    cf = _company(monkeypatch, cur, conn)
    res = asyncio.run(cf.delete_user_account("owner"))
    assert res["message"] == "Account and all related data deleted successfully"
    assert [e[0] for e in events if e[0] in ("commit", "rollback")] == ["commit"]          # was two commits
    # the request-status update is inside the single transaction (before the commit)
    idx_update = max(i for i, e in enumerate(events) if e[0] == "sql" and "UPDATE account_deletion_requests" in e[1])
    assert idx_update < events.index(("commit",))
    assert cur.find("DELETE FROM users WHERE user_id = %s")                 # legacy behaviour untouched
    assert not cur.find("UPDATE users SET name = %s") and not s3


@pytest.mark.parametrize("value, expected", [(None, False), ("0", False), ("", False), ("1", True), ("true", True), ("ON", True)])
def test_account_delete_flag_parsing(monkeypatch, value, expected):
    from core_functions import company_functions as cf
    if value is None:
        monkeypatch.delenv("ACCOUNT_DELETE_ANONYMISE", raising=False)
    else:
        monkeypatch.setenv("ACCOUNT_DELETE_ANONYMISE", value)
    assert cf.account_delete_anonymises() is expected


# ------------------------------------------------------------------------------------------------ S3 helper wiring
def test_delete_s3_assets_passes_urls_and_the_heic_profile_key(monkeypatch):
    import ml.s3 as m
    seen = {}
    monkeypatch.setattr(m, "delete_employee_s3_assets", lambda uid, extra_urls=None: seen.update(uid=uid, extra=extra_urls) or {"ok": 1})
    out = dl.delete_s3_assets("u1", ["https://b/x.jpg"])
    assert out == {"ok": 1} and seen["uid"] == "u1"
    assert seen["extra"][0] == "https://b/x.jpg" and seen["extra"][-1].startswith("employee_profile/u1.")


def test_delete_s3_assets_never_raises(monkeypatch):
    import ml.s3 as m

    def boom(*a, **k):
        raise RuntimeError("s3 down")

    monkeypatch.setattr(m, "delete_employee_s3_assets", boom)
    assert "errors" in dl.delete_s3_assets("u1", [])
    assert "errors" in dl.delete_org_logo("orgA")


# ------------------------------------------------------------------------------------------------ operator route
KEY = "k" * 32


@pytest.fixture
def client(monkeypatch):
    from router import lifecycle
    from core_functions import operator_auth as oa
    from utils import rate_limit
    for name in ("OPERATOR_API_KEY", "OPERATOR_ENFORCE", "OPERATOR_LEGACY_ADMIN_ID", "REQUIRE_AUTH", "RATE_LIMIT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPERATOR_API_KEY", KEY)
    rate_limit.set_clock(None)
    oa.set_lookup({"111": 1, "222": 2}.get)
    calls = []
    cur = FakeCursor(Events())
    conn = FakeConn(cur.events)
    conn.cursor = lambda cursor_factory=None: cur
    monkeypatch.setattr(lifecycle, "get_db_connection", lambda: conn)
    monkeypatch.setattr(lifecycle, "return_connection", lambda c: None)

    def fake_purge(cursor, days=30, dry_run=True, conn=None, **kw):
        calls.append((days, dry_run))
        if days == 99:
            raise ValueError("days must be between 1 and 3650")
        if days == 98:
            raise RuntimeError("secret internals")
        return {"dry_run": dry_run, "days": days, "eligible": 2, "delete": 1, "anonymise": 1,
                "skipped": [], "purged": [], "failed": [], "cutoff": "x"}

    monkeypatch.setattr(lifecycle.data_lifecycle, "purge_deleted_staff", fake_purge)
    app = FastAPI()
    app.include_router(lifecycle.router)
    c = TestClient(app)
    c.calls, c.events = calls, cur.events
    yield c
    oa.set_lookup(None)
    rate_limit.set_clock(None)


def test_route_defaults_to_dry_run_and_30_days(client):
    r = client.post("/admin/lifecycle/purge-deleted", headers={"X-Operator-Key": KEY})
    assert r.status_code == 200
    assert r.json() == {"success": True, "dry_run": True, "days": 30, "eligible": 2, "delete": 1, "anonymise": 1,
                        "skipped": [], "purged": [], "failed": [], "cutoff": "x"}
    assert client.calls == [(30, True)] and ("rollback",) in client.events        # the read-only transaction is ended


def test_route_real_run_needs_dry_run_false(client):
    r = client.post("/admin/lifecycle/purge-deleted?days=45&dry_run=false", headers={"X-Operator-Key": KEY})
    assert r.status_code == 200 and r.json()["dry_run"] is False and client.calls == [(45, False)]


def test_route_requires_an_operator_even_while_auth_is_off(client):
    assert client.post("/admin/lifecycle/purge-deleted").status_code == 403
    assert client.post("/admin/lifecycle/purge-deleted", headers={"X-Operator-Key": "wrong" * 8}).status_code == 403
    assert client.post("/admin/lifecycle/purge-deleted", headers={"x-admin-id": "222"}).status_code == 403   # role 2 is not enough
    assert client.post("/admin/lifecycle/purge-deleted", headers={"x-admin-id": "111"}).status_code == 200   # role 1 operator
    assert client.calls == [(30, True)]


def test_route_bad_days_is_400_and_internal_errors_are_generic(client):
    r = client.post("/admin/lifecycle/purge-deleted?days=99", headers={"X-Operator-Key": KEY})
    assert r.status_code == 400 and "3650" in r.json()["detail"]
    r = client.post("/admin/lifecycle/purge-deleted?days=98&dry_run=false", headers={"X-Operator-Key": KEY})
    assert r.status_code == 500 and "secret" not in r.text
    assert client.post("/admin/lifecycle/purge-deleted?days=abc", headers={"X-Operator-Key": KEY}).status_code == 422


def test_route_is_an_operator_route_in_the_policy():
    from core_functions import auth_policy as P
    assert P.classify("POST", "/admin/lifecycle/purge-deleted") == P.OPERATOR


# ------------------------------------------------------------------------------------------------ Rekognition faces
def test_remove_face_data_reports_and_never_raises(monkeypatch):
    import ml.face_service as fs

    class Backend:
        def __init__(self, result=None, error=None):
            self.result, self.error, self.asked = result, error, []

        def remove(self, user_id):
            self.asked.append(user_id)
            if self.error:
                raise self.error
            return self.result

    ok = Backend(fs.RemoveResult(True, 4, []))
    fs.set_backend(ok)
    assert dl.remove_face_data("u1") == {"ok": True, "removed": 4, "errors": []} and ok.asked == ["u1"]
    fs.set_backend(Backend(fs.RemoveResult(False, 0, ["InternalServerError: x"])))
    assert dl.remove_face_data("u1") == {"ok": False, "removed": 0, "errors": ["InternalServerError: x"]}
    fs.set_backend(Backend(error=RuntimeError("aws down")))
    out = dl.remove_face_data("u1")
    assert out["ok"] is False and "aws down" in out["errors"][0]


def test_purge_removes_the_rekognition_faces_after_the_commit_and_reports_failures(s3, monkeypatch):
    events = Events()
    cur, conn = make(events, candidate_routes({"user_id": "u1", "role_id": 3}, {"user_id": "u2", "role_id": 3}))
    outcomes = {"u1": {"ok": True, "removed": 4, "errors": []}, "u2": {"ok": False, "removed": 0, "errors": ["x"]}}
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: events.append(("faces", uid)) or outcomes[uid])
    out = dl.purge_deleted_staff(cur, days=30, dry_run=False, now=NOW)
    assert [e[1] for e in events if e[0] == "faces"] == ["u1", "u2"]
    assert events.index(("commit",)) < events.index(("faces", "u1"))
    assert out["face_cleanup_failed"] == ["u2"]
    assert [p["user_id"] for p in out["purged"]] == ["u1", "u2"]               # the staff are purged either way


def test_purge_dry_run_touches_no_faces(s3, monkeypatch):
    events = Events()
    cur, _ = make(events, candidate_routes({"user_id": "u1", "role_id": 3}))
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: events.append(("faces", uid)) or {"ok": True})
    out = dl.purge_deleted_staff(cur, days=30, now=NOW)
    assert out["face_cleanup_failed"] == [] and not [e for e in events if e[0] == "faces"]


def test_finish_account_cleanup_removes_faces_of_every_user_and_lists_failures(s3, monkeypatch):
    seen = []
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: seen.append(uid) or {"ok": uid != "u2"})
    summary = {"s3": [("u1", []), ("u2", []), ("owner", [])], "org_ids": ["orgA"]}
    dl.finish_account_cleanup(summary)
    assert seen == ["u1", "u2", "owner"] and summary["face_cleanup_failed"] == ["u2"]


def test_permanent_delete_removes_the_faces_after_the_commit(monkeypatch, s3):
    from core_functions import add_staff_function as af
    events = Events()
    cur = FakeCursor(events, [("SELECT user_id, name, role_id, parent_id", [{"user_id": "u1", "name": "A", "role_id": 3, "parent_id": "o"}])],
                     connection=FakeConn(events))
    cur.connection.cursor = lambda cursor_factory=None: cur
    monkeypatch.setattr(af, "get_db_connection", lambda: cur.connection)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: events.append(("faces", uid)) or {"ok": True})
    res = asyncio.run(af.permanently_delete_staff("u1", 1))
    assert res["success"] is True and ("faces", "u1") in events
    assert events.index(("commit",)) < events.index(("faces", "u1"))


def test_hard_delete_account_removes_the_faces_of_the_owner_and_all_staff_after_the_commit(monkeypatch, s3):
    monkeypatch.delenv("ACCOUNT_DELETE_ANONYMISE", raising=False)
    events = Events()
    cur, conn = make(events, [("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}]),
                              ("SELECT user_id FROM users WHERE parent_id", [{"user_id": "s1"}, {"user_id": "s2"}])])
    cf = _company(monkeypatch, cur, conn)
    monkeypatch.setattr(dl, "remove_face_data", lambda uid: events.append(("faces", uid)) or {"ok": True})
    res = asyncio.run(cf.delete_user_account("owner"))
    assert res["success"] is True
    assert [e[1] for e in events if e[0] == "faces"] == ["owner", "s1", "s2"]
    assert events.index(("commit",)) < events.index(("faces", "owner"))
    # a failed delete (rolled back) removes no faces
    events2 = Events()
    cur2, conn2 = make(events2, [("SELECT org_id FROM org_info WHERE parent_id", [{"org_id": "orgA"}]),
                                 ("SELECT user_id FROM users WHERE parent_id", [{"user_id": "s1"}])], fail_on="DELETE FROM org_info")
    cf2 = _company(monkeypatch, cur2, conn2)
    with pytest.raises(HTTPException):
        asyncio.run(cf2.delete_user_account("owner"))
    assert not [e for e in events2 if e[0] == "faces"]
