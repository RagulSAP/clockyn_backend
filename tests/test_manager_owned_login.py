"""Login lookups for staff created under a manager (WP6, defect D3, migration 034).

The real `employee_login` and `verify_device_token` run against an in-memory SQLite database (the statements use only
portable SQL: joins, a scalar sub-select, LOWER, LIMIT), so the organisation join is executed for real, not just
inspected. The migration's backfill statement is executed the same way.
Run: python -m pytest tests/test_manager_owned_login.py -q
"""
import asyncio
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_function as af  # noqa: E402


class SqliteCursor:
    """psycopg2-style cursor over sqlite: `%s` placeholders, rows as dicts (duplicate column names: the last one wins,
    as with RealDictCursor)."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self.sql = []

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.sql.append(text)
        self.cur.execute(text.replace("%s", "?"), tuple(params or ()))

    def _row(self, row):
        names = [d[0] for d in self.cur.description]
        out = {}
        for name, value in zip(names, row):
            out[name] = value
        return out

    def fetchone(self):
        row = self.cur.fetchone()
        return None if row is None else self._row(row)

    def fetchall(self):
        return [self._row(r) for r in self.cur.fetchall()]

    def close(self):
        pass


class Conn:
    def __init__(self, db):
        self.db = db
        self.cur = SqliteCursor(db)

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()


SCHEMA = """
CREATE TABLE users (user_id TEXT, org_id TEXT, name TEXT, role_id INT, parent_id TEXT, manager_id TEXT,
    mobile_number TEXT, email TEXT, status INT, device_token TEXT, device_token_expires_at TEXT);
CREATE TABLE org_info (org_id TEXT, org_name TEXT, parent_id TEXT);
CREATE TABLE settings (setting_id INT, setting_name TEXT);
CREATE TABLE manager_permissions (user_id TEXT, org_id TEXT, setting_id INT, status INT);
"""


def build_db():
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO org_info VALUES (?,?,?)", [("orgA", "Alpha", "adminA"), ("orgB", "Beta", "adminB")])
    db.execute("INSERT INTO settings VALUES (1, 'Staff Management')")
    db.execute("INSERT INTO manager_permissions VALUES ('mgrA', 'orgA', 1, 1)")
    rows = [
        # user_id, org_id, name, role, parent_id, manager_id, mobile
        ("adminA", "orgA", "Admin A", 1, None, None, "9000000000"),
        ("mgrA", None, "Manager A", 2, "orgA", None, "9000000001"),
        ("stfNormal", None, "Normal", 3, "orgA", None, "9000000002"),
        ("stfNew", None, "New style", 3, "orgA", "mgrA", "9000000003"),            # parent_id = org, manager_id = manager
        ("stfLegacy", None, "Legacy migrated", 3, "mgrA", "mgrA", "9000000004"),    # parent_id = manager (old code), backfilled
        ("stfLegacyRaw", None, "Legacy raw", 3, "mgrA", None, "9000000005"),        # parent_id = manager, migration not run
        ("stfB", None, "Other org", 3, "orgB", None, "9000000006"),
        ("mgrB", None, "Manager B", 2, "orgB", None, "9000000007"),
        ("stfLegacyB", None, "Legacy of B", 3, "mgrB", "mgrB", "9000000008"),
    ]
    for user_id, org, name, role, parent, manager, mobile in rows:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,1,'tok-' || ?, NULL)",
                   (user_id, org, name, role, parent, manager, mobile, user_id + "@x.test", user_id))
    db.commit()
    return db


class Handle:
    """The sqlite database plus the psycopg2-style connection the code under test uses."""

    def __init__(self, database, conn):
        self.database, self.conn = database, conn

    def execute(self, sql, params=()):
        return self.database.execute(sql, params)

    def commit(self):
        self.database.commit()


@pytest.fixture
def db(monkeypatch):
    database = build_db()
    conn = Conn(database)
    monkeypatch.setattr(af, "get_db_connection", lambda: conn)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    import ml.s3 as s3
    monkeypatch.setattr(s3, "get_org_logo_url", lambda org: None)
    monkeypatch.delenv("USER_AUTH", raising=False)
    monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("ENFORCE_SINGLE_DEVICE", raising=False)
    return Handle(database, conn)


def login(mobile):
    return asyncio.run(af.employee_login(af.EmployeeLoginRequest(login_id=mobile)))


def verify(mobile, token):
    return asyncio.run(af.verify_device_token(af.DeviceTokenVerifyRequest(contact=mobile, device_token=token, role_id=3)))


# ------------------------------------------------------------------------------------------------ employee_login
@pytest.mark.parametrize("mobile, user_id, org_id, org_name", [
    ("9000000002", "stfNormal", "orgA", "Alpha"),        # unchanged behaviour: parent_id = org
    ("9000000003", "stfNew", "orgA", "Alpha"),           # manager-created, stored the new way
    ("9000000004", "stfLegacy", "orgA", "Alpha"),        # manager-created by the old code path (manager_id backfilled)
    ("9000000006", "stfB", "orgB", "Beta"),
    ("9000000008", "stfLegacyB", "orgB", "Beta"),        # resolves through ITS manager's organisation, not another one
])
def test_employee_login_resolves_the_organisation(db, mobile, user_id, org_id, org_name):
    out = login(mobile)
    assert out["success"] is True
    assert (out["user_id"], out["org_id"], out["org_name"], out["role_id"]) == (user_id, org_id, org_name, 3)


def test_manager_login_still_returns_the_permissions(db):
    out = login("9000000001")
    assert out["org_id"] == "orgA" and out["role_id"] == 2 and out["manager_permissions"] == ["Staff Management"]


def test_legacy_row_without_manager_id_is_not_found_until_the_migration_ran(db):
    # documents WHY migration 034 backfills manager_id: without it the row cannot reach its organisation
    with pytest.raises(HTTPException) as e:
        login("9000000005")
    assert e.value.status_code == 401


def test_the_join_does_not_duplicate_or_cross_organisations(db):
    # a new-style row matches on parent_id AND through its manager: still exactly one organisation
    out = login("9000000003")
    assert out["org_id"] == "orgA"
    conn = db.conn
    join = [s for s in conn.cur.sql if "JOIN org_info o" in s][0]
    assert "u.parent_id = o.org_id OR o.org_id = (SELECT m.parent_id FROM users m WHERE m.user_id = u.manager_id)" in join


# ------------------------------------------------------------------------------------------------ verify_device_token
@pytest.mark.parametrize("mobile, user_id, org_id", [
    ("9000000002", "stfNormal", "orgA"),
    ("9000000003", "stfNew", "orgA"),
    ("9000000004", "stfLegacy", "orgA"),
    ("9000000008", "stfLegacyB", "orgB"),
])
def test_verify_device_token_resolves_the_organisation(db, mobile, user_id, org_id):
    out = verify(mobile, "tok-" + user_id)
    assert out["success"] is True and out["skip_otp"] is True
    assert (out["user_id"], out["org_id"], out["org_name"]) == (user_id, org_id, "Alpha" if org_id == "orgA" else "Beta")


def test_verify_device_token_admin_join_is_unchanged(db):
    out = asyncio.run(af.verify_device_token(af.DeviceTokenVerifyRequest(contact="9000000000", device_token="tok-adminA", role_id=1)))
    assert out["success"] is True and out["org_id"] == "orgA" and out["role_id"] == 1


def test_verify_device_token_for_an_unmigrated_legacy_row_has_no_organisation(db):
    out = verify("9000000005", "tok-stfLegacyRaw")
    # the LEFT JOIN keeps the user, but without an organisation: the reason the migration backfill exists
    assert out["success"] is True and out["org_id"] is None


# ------------------------------------------------------------------------------------------------ migration 034 backfill
def _backfill_sql():
    text = (ROOT / "migrations" / "034_users_manager_id.sql").read_text(encoding="utf-8")
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    stmts = [s.strip() for s in body.split(";") if s.strip()]
    update = [s for s in stmts if s.upper().startswith("UPDATE USERS")]
    assert len(update) == 1
    text = " ".join(update[0].split())
    assert text.startswith("UPDATE users u SET")
    return text.replace("UPDATE users u SET", "UPDATE users AS u SET", 1)    # sqlite wants AS; PostgreSQL accepts both


def _legacy_db():
    db = build_db()
    db.execute("UPDATE users SET manager_id = NULL")               # a database as it was before the migration
    db.commit()
    return db


def test_migration_backfill_sets_manager_id_only_for_rows_created_under_a_manager():
    db = _legacy_db()
    db.execute(_backfill_sql())
    got = dict(db.execute("SELECT user_id, manager_id FROM users").fetchall())
    assert got["stfLegacy"] == "mgrA" and got["stfLegacyRaw"] == "mgrA" and got["stfLegacyB"] == "mgrB"
    for untouched in ("adminA", "mgrA", "stfNormal", "stfNew", "stfB", "mgrB"):
        assert got[untouched] is None, untouched                    # org-parented rows and admins are left alone
    # nothing else changed: parent_id is exactly what it was
    parents = dict(db.execute("SELECT user_id, parent_id FROM users").fetchall())
    assert parents["stfLegacy"] == "mgrA" and parents["stfNormal"] == "orgA"


def test_migration_backfill_is_idempotent_and_keeps_existing_manager_ids():
    db = _legacy_db()
    db.execute("UPDATE users SET manager_id = 'someone-else' WHERE user_id = 'stfLegacy'")
    db.execute(_backfill_sql())
    first = db.execute("SELECT user_id, manager_id FROM users ORDER BY user_id").fetchall()
    db.execute(_backfill_sql())
    assert db.execute("SELECT user_id, manager_id FROM users ORDER BY user_id").fetchall() == first
    assert dict(first)["stfLegacy"] == "someone-else"               # `manager_id IS NULL` guard


def test_after_the_backfill_every_legacy_row_can_log_in(db):
    db.execute(_backfill_sql())
    db.commit()
    assert login("9000000005")["org_id"] == "orgA"                  # the raw legacy row resolves now


def test_ddl_and_migration_declare_the_same_column_and_index():
    ddl = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
    mig = (ROOT / "migrations" / "034_users_manager_id.sql").read_text(encoding="utf-8")
    assert re.search(r"manager_id varchar\(36\) NULL", ddl)
    assert "idx_users_manager_id ON clockyn_dev.users USING btree (manager_id)" in ddl
    assert "ADD COLUMN IF NOT EXISTS manager_id varchar(36) NULL" in mig
    assert "CREATE INDEX IF NOT EXISTS idx_users_manager_id ON users USING btree (manager_id)" in mig
