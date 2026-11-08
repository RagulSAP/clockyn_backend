"""DB-free tests of the FAQ Hub (core_functions/support_faq_functions.py): the app's `GET /support/faqs` and the operator CRUD
under `/gen-dashboard/faqs`.

A small stateful fake database understands exactly the statements the module issues and fails loudly on anything else.
Run: python -m pytest tests/test_support_faq.py -q
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_policy as P  # noqa: E402
from core_functions import operator_auth  # noqa: E402
from core_functions import support_faq_functions as faq  # noqa: E402
from router import gen_dashboard, support  # noqa: E402

SUPER, STAFF_OP = "super@clockyn.com", "agent@clockyn.com"
SUPER_H, STAFF_H = {"x-admin-id": SUPER}, {"x-admin-id": STAFF_OP}
T0 = datetime(2026, 11, 8, 9, 0, 0, tzinfo=timezone.utc)


class UndefinedTable(Exception):
    pgcode = "42P01"


class UniqueViolation(Exception):
    pgcode = "23505"


# ───────────────────────────────────────────────────────────────────────────── fake database
class FakeDB:
    def __init__(self):
        self.rows, self.log = [], []
        self.next_id, self.clock = 1, T0
        self.commits = self.rollbacks = 0
        self.missing = False                 # every support_faq statement raises 42P01
        self.fail_with = None                # next INSERT/UPDATE raises this exception once
        self.broken = False                  # every statement raises a plain error

    def tick(self):
        self.clock += timedelta(seconds=1)
        return self.clock

    def add(self, category, question, answer="A", sort_order=0, status=1, faq_bucket="General"):
        now = self.tick()
        row = {"id": self.next_id, "faq_bucket": faq_bucket, "category": category, "question": question, "answer": answer,
               "sort_order": sort_order, "status": status, "created_at": now, "updated_at": now}
        self.next_id += 1
        self.rows.append(row)
        return row

    def ordered(self):
        return sorted(self.rows, key=lambda r: (r["sort_order"], r["id"]))


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows, self.rowcount = db, [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def _set(self, rows, rowcount=None):
        self.rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def execute(self, sql, params=None):
        s, p, db = " ".join(sql.split()), tuple(params or ()), self.db
        db.log.append((s, p))
        self._set([], 0)
        if db.broken:
            raise RuntimeError("connection reset by peer: secret-host-name")
        if db.missing:
            raise UndefinedTable('relation "support_faq" does not exist')

        if s == "SELECT id, faq_bucket, category, question, answer FROM support_faq WHERE status = 1 ORDER BY sort_order, id":
            return self._set([{k: r[k] for k in ("id", "faq_bucket", "category", "question", "answer")} for r in db.ordered() if r["status"] == 1])
        if s == "SELECT id, faq_bucket, category, question, answer, sort_order, status, created_at, updated_at FROM support_faq ORDER BY sort_order, id":
            return self._set([dict(r) for r in db.ordered()])
        if s.startswith("SELECT id FROM support_faq WHERE lower(question) = lower(%s)"):
            excl = p[1] if "AND id <> %s" in s else None
            return self._set([{"id": r["id"]} for r in db.rows if r["question"].lower() == p[0].lower() and r["id"] != excl])
        if s == "SELECT COALESCE(MAX(sort_order), 0) + %s AS next_order FROM support_faq":
            return self._set([{"next_order": max([r["sort_order"] for r in db.rows], default=0) + p[0]}])
        if s.startswith("INSERT INTO support_faq (faq_bucket, category, question, answer, sort_order, status) VALUES (%s, %s, %s, %s, %s, %s) RETURNING"):
            if db.fail_with:
                exc, db.fail_with = db.fail_with, None
                raise exc
            bucket, category, question, answer, sort_order, status = p
            row = db.add(category, question, answer, sort_order, status, faq_bucket=bucket)
            return self._set([dict(row)], 1)
        if s.startswith("UPDATE support_faq SET "):
            if db.fail_with:
                exc, db.fail_with = db.fail_with, None
                raise exc
            assigns = s[len("UPDATE support_faq SET "):s.index(" WHERE id = %s")].split(", ")
            row = next((r for r in db.rows if r["id"] == p[-1]), None)
            if row is None:
                return self._set([], 0)
            values = iter(p[:-1])
            for a in assigns:
                col = a.split(" = ")[0]
                if a.endswith("NOW()"):
                    row[col] = db.tick()
                else:
                    assert a.endswith("%s"), a
                    row[col] = next(values)
            return self._set([dict(row)], 1)
        if s == "DELETE FROM support_faq WHERE id = %s":
            before = len(db.rows)
            db.rows = [r for r in db.rows if r["id"] != p[0]]
            return self._set([], before - len(db.rows))
        raise AssertionError(f"unexpected SQL: {s}")


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1


# ───────────────────────────────────────────────────────────────────────────── fixtures
@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(faq, "get_db_connection", lambda: FakeConn(db))
    monkeypatch.setattr(faq, "return_connection", lambda c: None)
    monkeypatch.setattr(faq, "_missing_table_logged", False)
    monkeypatch.delenv("OPERATOR_ENFORCE", raising=False)
    operator_auth.set_lookup({SUPER: 1, STAFF_OP: 2}.get)
    app = FastAPI()
    app.include_router(support.router)
    app.include_router(gen_dashboard.router)
    yield type("Env", (), {"db": db, "client": TestClient(app)})()
    operator_auth.set_lookup(None)


def post(env, body, headers=SUPER_H):
    return env.client.post("/gen-dashboard/faqs", json=body, headers=headers)


def valid(**kw):
    body = {"category": "Getting Started", "question": "How do I add a staff member?", "answer": "1. Open Staff.\n2. Tap Add."}
    body.update(kw)
    return body


# ───────────────────────────────────────────────────────────────────────────── app route
class TestAppRoute:
    def test_only_active_faqs_in_display_order_as_a_bare_list(self, env):
        env.db.add("Billing", "Third by order, first by id?", "c", sort_order=300)
        env.db.add("Attendance", "Second", "b", sort_order=200)
        env.db.add("Attendance", "Hidden one", "x", sort_order=250, status=0)
        env.db.add("Getting Started", "First", "a", sort_order=100)
        env.db.add("Attendance", "Same order, later id", "b2", sort_order=200)
        r = env.client.get("/support/faqs")
        assert r.status_code == 200
        body = r.json()
        assert [f["question"] for f in body] == ["First", "Second", "Same order, later id", "Third by order, first by id?"]
        assert all(set(f) == {"id", "faq_bucket", "category", "question", "answer"} for f in body)

    def test_the_bucket_is_part_of_every_app_entry_and_ordering_ignores_it(self, env):
        env.db.add("Shifts", "Second in order", "b", sort_order=20, faq_bucket="Attendance & Time")
        env.db.add("Getting Started", "First in order", "a", sort_order=10, faq_bucket="Set up & Account")
        env.db.add("Plain", "No bucket given", "c", sort_order=30)
        body = env.client.get("/support/faqs").json()
        assert [(f["faq_bucket"], f["question"]) for f in body] == [
            ("Set up & Account", "First in order"), ("Attendance & Time", "Second in order"), ("General", "No bucket given")]

    def test_the_answer_is_returned_as_plain_text_unchanged(self, env):
        text = "1. Open <b>Settings</b>\n2. Tap \"Save\" & go\n\n  indented line ₹500 \U0001f600"
        env.db.add("Plans & Billing", "Does it keep my text?", text, sort_order=10)
        assert env.client.get("/support/faqs").json()[0]["answer"] == text

    def test_an_empty_table_answers_an_empty_list(self, env):
        assert env.client.get("/support/faqs").json() == []

    def test_a_database_failure_is_a_generic_500(self, env):
        env.db.broken = True
        r = env.client.get("/support/faqs")
        assert r.status_code == 500 and r.json() == {"detail": "Internal server error"}      # no internal text


# ───────────────────────────────────────────────────────────────────────────── operator list
class TestOperatorList:
    def test_lists_every_faq_with_all_fields_active_and_hidden(self, env):
        env.db.add("A", "Visible question", "a", sort_order=20)
        env.db.add("B", "Hidden question", "b", sort_order=10, status=0)
        r = env.client.get("/gen-dashboard/faqs", headers=STAFF_H)
        assert r.status_code == 200
        rows = r.json()
        assert [x["question"] for x in rows] == ["Hidden question", "Visible question"]
        assert [x["status"] for x in rows] == [0, 1]
        assert set(rows[0]) == {"id", "faq_bucket", "category", "question", "answer", "sort_order", "status", "created_at", "updated_at"}
        assert rows[0]["created_at"].startswith("2026-11-08")

    def test_any_operator_may_read_without_a_credential_while_enforcement_is_off(self, env):
        assert env.client.get("/gen-dashboard/faqs").status_code == 200


# ───────────────────────────────────────────────────────────────────────────── create
class TestCreate:
    def test_defaults_sort_order_to_max_plus_ten_and_status_to_active(self, env):
        env.db.add("A", "Existing one", sort_order=70)
        env.db.add("A", "Existing two", sort_order=40)
        r = post(env, valid())
        assert r.status_code == 200 and r.json()["success"] is True
        faq_ = r.json()["faq"]
        assert faq_["sort_order"] == 80 and faq_["status"] == 1 and faq_["id"] == 3
        assert env.db.commits == 1

    def test_first_faq_of_an_empty_table_gets_ten(self, env):
        assert post(env, valid()).json()["faq"]["sort_order"] == 10

    def test_explicit_sort_order_and_status_are_kept(self, env):
        faq_ = post(env, valid(sort_order=0, status=0)).json()["faq"]
        assert faq_["sort_order"] == 0 and faq_["status"] == 0
        assert env.client.get("/support/faqs").json() == []                    # inactive: not in the app

    def test_values_are_trimmed_and_the_answer_text_is_not_otherwise_changed(self, env):
        r = post(env, {"category": "  Attendance & Face Punch ", "question": "  How do I punch in?  ", "answer": "\n 1. Tap.\n2. Smile \n"})
        faq_ = r.json()["faq"]
        assert faq_["category"] == "Attendance & Face Punch" and faq_["question"] == "How do I punch in?"
        assert faq_["answer"] == "1. Tap.\n2. Smile"

    @pytest.mark.parametrize("field,value", [
        ("faq_bucket", ""), ("faq_bucket", "   "), ("faq_bucket", "b" * 61),
        ("category", ""), ("category", "   "), ("category", "c" * 61),
        ("question", "four"), ("question", "    abc   "), ("question", "q" * 301),
        ("answer", ""), ("answer", "  \n "), ("answer", "a" * 8001),
        ("sort_order", -1), ("sort_order", 1_000_001),
        ("status", 2), ("status", -1),
    ])
    def test_validation_limits(self, env, field, value):
        r = post(env, valid(**{field: value}))
        assert r.status_code == 400 and r.json()["detail"]
        assert env.db.rows == []

    def test_the_bucket_defaults_to_general_and_is_trimmed_when_given(self, env):
        assert post(env, valid()).json()["faq"]["faq_bucket"] == "General"
        r = post(env, valid(question="Another question here", faq_bucket="  Attendance & Time "))
        assert r.json()["faq"]["faq_bucket"] == "Attendance & Time"
        assert [f["faq_bucket"] for f in env.client.get("/support/faqs").json()] == ["General", "Attendance & Time"]

    @pytest.mark.parametrize("field,value", [("faq_bucket", 60), ("category", 60), ("question", 300), ("answer", 8000)])
    def test_exact_limits_are_accepted(self, env, field, value):
        text = "x" * value
        if field == "question":
            text = "q" * value
        assert post(env, valid(**{field: text})).status_code == 200

    def test_a_question_of_exactly_five_characters_is_accepted(self, env):
        assert post(env, valid(question="Why?!")).status_code == 200

    def test_wrong_types_are_rejected_by_validation(self, env):
        assert post(env, valid(sort_order="soon")).status_code == 422
        assert env.client.post("/gen-dashboard/faqs", json={"category": "A"}, headers=SUPER_H).status_code == 422

    def test_duplicate_question_is_a_409_case_insensitive_and_trimmed(self, env):
        assert post(env, valid(question="How do I add staff?")).status_code == 200
        r = post(env, valid(question="  how DO i ADD staff?  ", category="Other"))
        assert r.status_code == 409 and "already exists" in r.json()["detail"]
        assert len(env.db.rows) == 1

    def test_losing_a_race_against_the_unique_index_is_also_a_409(self, env):
        env.db.fail_with = UniqueViolation("duplicate key value violates unique constraint")
        assert post(env, valid()).status_code == 409
        assert env.db.rollbacks >= 1

    def test_sort_order_never_exceeds_the_limit(self, env):
        env.db.add("A", "Last possible", sort_order=1_000_000)
        assert post(env, valid()).json()["faq"]["sort_order"] == 1_000_000


# ───────────────────────────────────────────────────────────────────────────── update / delete
class TestUpdate:
    def put(self, env, faq_id, body, headers=SUPER_H):
        return env.client.put(f"/gen-dashboard/faqs/{faq_id}", json=body, headers=headers)

    def test_partial_update_changes_only_the_given_fields(self, env):
        row = env.db.add("Billing", "Original question", "Original answer", sort_order=50)
        before = row["updated_at"]
        r = self.put(env, row["id"], {"status": 0})
        faq_ = r.json()["faq"]
        assert r.status_code == 200 and faq_["status"] == 0 and faq_["answer"] == "Original answer" and faq_["sort_order"] == 50
        assert env.db.rows[0]["updated_at"] > before
        r = self.put(env, row["id"], {"answer": "  New answer\nline 2 ", "category": " Plans "})
        faq_ = r.json()["faq"]
        assert faq_["answer"] == "New answer\nline 2" and faq_["category"] == "Plans" and faq_["status"] == 0

    def test_the_bucket_alone_can_be_changed_and_nothing_else_moves(self, env):
        row = env.db.add("Shifts", "Which bucket am I in?", "Original answer", sort_order=40, faq_bucket="General")
        r = self.put(env, row["id"], {"faq_bucket": "  Attendance & Time "})
        faq_ = r.json()["faq"]
        assert r.status_code == 200 and faq_["faq_bucket"] == "Attendance & Time"
        assert (faq_["category"], faq_["question"], faq_["answer"], faq_["sort_order"], faq_["status"]) == ("Shifts", "Which bucket am I in?", "Original answer", 40, 1)
        assert self.put(env, row["id"], {"faq_bucket": "  "}).status_code == 400
        assert self.put(env, row["id"], {"faq_bucket": "b" * 61}).status_code == 400
        assert env.db.rows[0]["faq_bucket"] == "Attendance & Time"

    def test_renaming_to_an_existing_question_is_a_409_but_keeping_its_own_is_fine(self, env):
        a = env.db.add("A", "First question here", sort_order=10)
        env.db.add("A", "Second question here", sort_order=20)
        assert self.put(env, a["id"], {"question": "SECOND question here"}).status_code == 409
        assert self.put(env, a["id"], {"question": "FIRST QUESTION HERE"}).status_code == 200          # itself (case change)
        assert env.db.rows[0]["question"] == "FIRST QUESTION HERE"

    def test_validation_and_empty_body(self, env):
        row = env.db.add("A", "A valid question", sort_order=10)
        for body in ({}, {"question": "no"}, {"answer": ""}, {"category": "c" * 61}, {"faq_bucket": ""}, {"sort_order": -5}, {"status": 3}):
            r = self.put(env, row["id"], body)
            assert r.status_code == 400, body
        assert env.db.rows[0]["question"] == "A valid question"

    def test_unknown_id_is_a_404(self, env):
        r = self.put(env, 999, {"status": 1})
        assert r.status_code == 404 and r.json()["detail"] == "FAQ not found"

    def test_reordering_changes_the_app_order(self, env):
        a = env.db.add("A", "Question number one", "a", sort_order=10)
        env.db.add("A", "Question number two", "b", sort_order=20)
        self.put(env, a["id"], {"sort_order": 30})
        assert [f["question"] for f in env.client.get("/support/faqs").json()] == ["Question number two", "Question number one"]


class TestDelete:
    def test_delete_removes_the_row_and_a_second_delete_is_404(self, env):
        row = env.db.add("A", "To be deleted soon", sort_order=10)
        r = env.client.delete(f"/gen-dashboard/faqs/{row['id']}", headers=SUPER_H)
        assert r.status_code == 200 and r.json() == {"success": True} and env.db.rows == []
        assert env.client.delete(f"/gen-dashboard/faqs/{row['id']}", headers=SUPER_H).status_code == 404

    def test_a_non_numeric_id_is_rejected_by_validation(self, env):
        assert env.client.delete("/gen-dashboard/faqs/abc", headers=SUPER_H).status_code == 422


# ───────────────────────────────────────────────────────────────────────────── who may write
class TestWritePermissions:
    def test_a_non_super_operator_cannot_write_but_can_read(self, env):
        row = env.db.add("A", "Protected question", sort_order=10)
        assert post(env, valid(), STAFF_H).status_code == 403
        assert env.client.put(f"/gen-dashboard/faqs/{row['id']}", json={"status": 0}, headers=STAFF_H).status_code == 403
        assert env.client.delete(f"/gen-dashboard/faqs/{row['id']}", headers=STAFF_H).status_code == 403
        assert env.client.get("/gen-dashboard/faqs", headers=STAFF_H).status_code == 200
        assert len(env.db.rows) == 1 and env.db.rows[0]["status"] == 1

    def test_unknown_operator_and_missing_credential_cannot_write(self, env):
        assert post(env, valid(), {"x-admin-id": "intruder@evil.com"}).status_code == 403
        assert post(env, valid(), {}).status_code == 403
        assert env.db.rows == []

    def test_a_super_operator_can_write(self, env):
        assert post(env, valid(), SUPER_H).status_code == 200

    def test_the_operator_key_is_enough_to_write(self, env, monkeypatch):
        monkeypatch.setenv("OPERATOR_API_KEY", "k" * 32)
        assert post(env, valid(), {"x-operator-key": "k" * 32}).status_code == 200


# ───────────────────────────────────────────────────────────────────────────── table not created yet
class TestTableNotCreatedYet:
    @pytest.fixture
    def no_table(self, env, caplog):
        env.db.missing = True
        caplog.set_level("ERROR")
        return env

    def test_reads_answer_empty_lists_not_errors(self, no_table):
        assert no_table.client.get("/support/faqs").json() == []
        r = no_table.client.get("/gen-dashboard/faqs", headers=SUPER_H)
        assert r.status_code == 200 and r.json() == []
        assert no_table.db.rollbacks >= 1

    def test_writes_answer_a_clear_503(self, no_table):
        r = post(no_table, valid())
        assert r.status_code == 503 and r.json()["detail"] == "FAQ is not set up yet"
        assert no_table.client.put("/gen-dashboard/faqs/1", json={"status": 0}, headers=SUPER_H).status_code == 503
        assert no_table.client.delete("/gen-dashboard/faqs/1", headers=SUPER_H).status_code == 503

    def test_the_cause_is_logged_once_not_on_every_request(self, no_table, caplog):
        for _ in range(5):
            no_table.client.get("/support/faqs")
            no_table.client.get("/gen-dashboard/faqs", headers=SUPER_H)
        hits = [r for r in caplog.records if "does not exist in this schema" in r.getMessage()]
        assert len(hits) == 1 and "CREATE: FAQ" in hits[0].getMessage()


# ───────────────────────────────────────────────────────────────────────────── injection
class TestInjection:
    NASTY = ["'; DROP TABLE support_faq; --", "Robert'); DELETE FROM support_faq;--", "\" OR 1=1 --", "%s %(x)s {0} ${HOME}"]

    def test_hostile_text_is_stored_literally_and_never_reaches_the_sql(self, env):
        for i, text in enumerate(self.NASTY):
            r = post(env, {"category": f"Cat {i}", "question": f"Q{i} {text}", "answer": text})
            assert r.status_code == 200, text
        env.client.put("/gen-dashboard/faqs/1", json={"answer": self.NASTY[0], "category": self.NASTY[1][:50]}, headers=SUPER_H)
        assert [r["answer"] for r in env.db.rows][:2] == [self.NASTY[0], self.NASTY[1]]
        assert env.client.get("/support/faqs").json()[1]["question"] == f"Q1 {self.NASTY[1]}"
        for sql, _params in env.db.log:                       # user text only ever travels as parameters
            assert "DROP" not in sql and "DELETE FROM support_faq;" not in sql and "OR 1=1" not in sql and "Robert" not in sql
        assert len(env.db.rows) == 4


# ───────────────────────────────────────────────────────────────────────────── routing
def test_the_faq_routes_are_exactly_these_and_classified_by_the_auth_policy(env):
    routes = {(m, r.path) for r in env.client.app.routes for m in getattr(r, "methods", ()) if "faq" in r.path}
    assert routes == {
        ("GET", "/support/faqs"),
        ("GET", "/gen-dashboard/faqs"),
        ("POST", "/gen-dashboard/faqs"),
        ("PUT", "/gen-dashboard/faqs/{faq_id}"),
        ("DELETE", "/gen-dashboard/faqs/{faq_id}"),
    }
    assert P.classify("GET", "/support/faqs") == P.AUTHENTICATED                  # any signed-in user (the tab itself is admin-only)
    for method, path in (("GET", "/gen-dashboard/faqs"), ("POST", "/gen-dashboard/faqs"), ("PUT", "/gen-dashboard/faqs/3"), ("DELETE", "/gen-dashboard/faqs/3")):
        assert P.classify(method, path) == P.OPERATOR, (method, path)


def test_the_ddl_has_the_table_the_code_expects():
    ddl = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS clockyn_dev\.support_faq \((.*?)\n\);", ddl, re.S)
    assert m, "support_faq table missing from the DDL"
    cols = {line.split()[0] for line in m.group(1).split("\n") if line.strip() and not line.strip().startswith(("CONSTRAINT", "--"))}
    assert "faq_bucket varchar(60) DEFAULT 'General' NOT NULL" in m.group(1)
    assert "ALTER TABLE clockyn_dev.support_faq ADD COLUMN IF NOT EXISTS faq_bucket varchar(60) DEFAULT 'General' NOT NULL;" in ddl
    assert cols == {"id", "faq_bucket", "category", "question", "answer", "sort_order", "status", "created_at", "updated_at"}
    assert "support_faq_status_check CHECK (status IN (0, 1))" in m.group(1)
    assert re.search(r"CREATE UNIQUE INDEX IF NOT EXISTS support_faq_question_key ON clockyn_dev\.support_faq .*lower\(question\)", ddl)
    assert "idx_support_faq_status_order" in ddl
