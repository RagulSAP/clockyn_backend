"""API tests for daily wages / hourly charges / wage assignment and shift assignment.

Runs against a real PostgreSQL database, inside a temporary schema `clockyn_dev_test_<hex>` of an EXISTING database
(no database is created or dropped, and no other schema is touched: see tests/pg_scratch.py). The tables are created from
the definitions in `punchapp_ddl_latest.sql` (only the tables these routes touch) and the schema is dropped afterwards.
"""
import os
import re
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest
import pg_scratch
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows without the tzdata package cannot build ZoneInfo objects; stub the helper then
    from zoneinfo import ZoneInfo
    ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - environment dependent
    import types
    from datetime import datetime, timezone

    _tz = types.ModuleType("utils.timezone")
    _tz.get_utc_now = lambda: datetime.now(timezone.utc)
    _tz.IST = _tz.UTC = timezone.utc
    sys.modules["utils.timezone"] = _tz

SCHEMA = "punchapp_prod"
TABLES = ["users", "branch_info", "dept_info", "daily_hourly_wages", "assigned_wages", "shift", "users_additional_info"]

_STATE = {}  # schema name of the scratch schema of this module

ORG_A = "org-a"
ORG_B = "org-b"


def _ddl_statements():
    """CREATE TABLE / CREATE INDEX statements of TABLES, taken from the real DDL file."""
    text = (ROOT / "punchapp_ddl_latest.sql").read_text(encoding="utf-8")
    out = []
    for chunk in re.split(r";[ \t]*\r?\n", text):
        stmt = "\n".join(ln for ln in chunk.splitlines() if not ln.startswith("--")).strip()
        for t in TABLES:
            if re.match(rf"CREATE TABLE {SCHEMA}\.{t} \(", stmt) or re.match(
                rf"CREATE (UNIQUE )?INDEX \S+ ON {SCHEMA}\.{t} ", stmt
            ):
                out.append(stmt)
                break
    return out


@pytest.fixture(scope="module")
def dbname():
    conn = pg_scratch.connect()
    schema = pg_scratch.create_scratch_schema(conn)
    _STATE["schema"] = schema
    try:
        cur = conn.cursor()
        for stmt in _ddl_statements():
            cur.execute(pg_scratch.rewrite_ddl(stmt, schema))
        yield pg_scratch.settings()["dbname"]
    finally:
        # drop pooled connections of this process first
        from core_functions import db_function
        pool = db_function._connection_pool
        if pool is not None:
            pool.closeall()
            db_function._connection_pool = None
        pg_scratch.drop_scratch_schema(conn, schema)
        conn.close()


@pytest.fixture(scope="module")
def client(dbname):
    os.environ.update(pg_scratch.app_env(_STATE["schema"]))
    from core_functions import db_function
    db_function._connection_pool = None
    from router import wages, shift
    app = FastAPI()
    for r in wages.routers:
        app.include_router(r)
    app.include_router(shift.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_s3(monkeypatch):
    from core_functions import shift_functions
    monkeypatch.setattr(shift_functions, "_profile_photo_url", lambda user_id: None)


@pytest.fixture(autouse=True)
def _plan_gate_open(monkeypatch):
    """The wages routers are behind plan_gate('salary_structure'), which reads free_trial / punchapp_subscription
    (tables this mini database does not create). The gate itself is covered by test_plan_gating.py; open it here."""
    from core_functions import gen_plans_functions

    async def _allowed(org_id, feature, is_admin=True):
        return None

    monkeypatch.setattr(gen_plans_functions, "check_plan_feature", _allowed)


@pytest.fixture
def db(dbname):
    conn = psycopg2.connect(options=f"-c search_path={_STATE['schema']}", **pg_scratch.settings())
    conn.autocommit = True
    yield conn
    conn.close()


def q(db, sql, args=None):
    cur = db.cursor()
    cur.execute(sql, args)
    return cur.fetchall() if cur.description else None


def add_user(db, uid, org, name, role=3, status=1, branch=1, dept="d1", with_info=True, shift_id=None):
    q(db, """INSERT INTO users (user_id, parent_id, name, emp_id, mobile_number, email, role_id, status, branch_id, dept_id)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (uid, org, name, "E-" + uid, "9" + uid[-9:].rjust(9, "0"), f"{uid}@x.test", role, status, branch, dept))
    if with_info:
        q(db, """INSERT INTO users_additional_info (user_id, acc_no, ifsc_code, bank_name, bank_branch, gender, address,
                 emergency_contact_no, shift_id) VALUES (%s,'','','','','','','',%s)""", (uid, shift_id))


@pytest.fixture
def seed(client, db):
    q(db, "TRUNCATE users, branch_info, dept_info, daily_hourly_wages, assigned_wages, shift, users_additional_info RESTART IDENTITY")
    for org in (ORG_A, ORG_B):
        q(db, "INSERT INTO branch_info (org_id, branch_id, branch_name, branch_address, gps_location) VALUES (%s,1,'Head Office','x','(0,0)')", (org,))
        q(db, "INSERT INTO dept_info (org_id, dept_id, dept_name) VALUES (%s,'d1','Sales')", (org,))
    q(db, "INSERT INTO users (user_id, org_id, name, emp_id, mobile_number, email, role_id) VALUES ('admin-a', %s, 'Admin A', 'A0', '9000000000', 'a@x.test', 1)", (ORG_A,))
    add_user(db, "stf-1", ORG_A, "Aarav", role=3)
    add_user(db, "stf-2", ORG_A, "Bela", role=2)
    add_user(db, "stf-3", ORG_A, "Chitra", role=3)
    add_user(db, "stf-4", ORG_A, "Dev", role=3, status=0)          # inactive
    add_user(db, "stf-5", ORG_A, "Esha", role=3, with_info=False)  # no additional-info row (older data)
    add_user(db, "stf-b", ORG_B, "Bhanu", role=3)
    for sid, org, name in ((1, ORG_A, "Day"), (2, ORG_A, "Night"), (3, ORG_B, "Other org")):
        q(db, "INSERT INTO shift (shift_id, org_id, shift_name, start_time, end_time, status) VALUES (%s,%s,%s,'09:00','17:00',1)", (sid, org, name))
    return client


def ids(resp_json):
    return [r["user_id"] for r in resp_json["data"]]


# ----------------------------------------------------------------------------------------------------
# Wage templates (daily and hourly share one implementation; D / H is fixed by the router)
# ----------------------------------------------------------------------------------------------------

KINDS = [("/api/daily-wages", "D"), ("/api/hourly-charges", "H")]
kinds = pytest.mark.parametrize("base,ttype", KINDS)


def create(c, base, amount, org=ORG_A, **extra):
    return c.post(f"{base}/template", json={"org_id": org, "amount": amount, **extra})


@kinds
def test_create_and_list_contract(seed, db, base, ttype):
    c = seed
    r = create(c, base, 700)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"success", "message", "id", "temp_name", "template_name"}
    assert body["success"] is True and body["message"] == "Template saved successfully"
    assert body["temp_name"] == body["template_name"] == "1"
    r2 = create(c, base, 650.5)
    assert r2.json()["temp_name"] == "2"

    lst = c.get(f"{base}/templates/{ORG_A}").json()
    assert lst["success"] is True
    assert [t["amount"] for t in lst["data"]] == [700.0, 650.5]
    first = lst["data"][0]
    assert {"id", "temp_name", "template_name", "amount", "assigned_count", "created_at"} <= set(first)
    assert first["id"] == body["id"] and first["assigned_count"] == 0
    assert isinstance(first["amount"], float)
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", first["created_at"])
    # other org sees nothing; the other type sees nothing
    assert c.get(f"{base}/templates/{ORG_B}").json() == {"success": True, "data": []}
    other = "/api/hourly-charges" if ttype == "D" else "/api/daily-wages"
    assert c.get(f"{other}/templates/{ORG_A}").json()["data"] == []


@kinds
def test_auto_numbering_skips_non_numeric_names(seed, base, ttype):
    c = seed
    create(c, base, 100, template_name="Premium")
    assert create(c, base, 200).json()["temp_name"] == "1"
    create(c, base, 300, template_name="7")
    assert create(c, base, 400).json()["temp_name"] == "8"
    # numbering is per org
    assert create(c, base, 100, org=ORG_B).json()["temp_name"] == "1"


@kinds
@pytest.mark.parametrize("bad", [0, -1, "abc", 1e9, 12.345, None, True, "", "NaN"])
def test_create_rejects_bad_amount(seed, db, base, ttype, bad):
    r = create(seed, base, bad)
    assert r.status_code == 400, r.text
    assert "detail" in r.json()
    assert q(db, "SELECT count(*) FROM daily_hourly_wages")[0][0] == 0


def test_create_amount_message_and_org_required(seed):
    r = create(seed, "/api/daily-wages", 0)
    assert r.json() == {"detail": "Amount must be greater than 0"}
    r = seed.post("/api/daily-wages/template", json={"amount": 5})
    assert r.status_code == 400 and r.json()["detail"] == "org_id is required"
    assert create(seed, "/api/daily-wages", 9999999.99).status_code == 200


@kinds
def test_duplicate_amount_409_scoped_to_org_and_type(seed, base, ttype):
    c = seed
    assert create(c, base, 700).status_code == 200
    r = create(c, base, 700)
    assert r.status_code == 409 and r.json() == {"detail": "A template with this amount already exists"}
    assert create(c, base, 700, org=ORG_B).status_code == 200
    other = "/api/hourly-charges" if ttype == "D" else "/api/daily-wages"
    assert create(c, other, 700).status_code == 200


@kinds
def test_update_template(seed, db, base, ttype):
    c = seed
    a = create(c, base, 700).json()["id"]
    b = create(c, base, 800).json()["id"]
    r = c.put(f"{base}/template/{a}", json={"org_id": ORG_A, "amount": 720})
    assert r.status_code == 200 and r.json() == {"success": True, "message": "Template updated successfully"}
    assert q(db, "SELECT temp_amount FROM daily_hourly_wages WHERE temp_id=%s", (a,))[0][0] == 720
    # same amount on itself is fine, another template's amount is a conflict
    assert c.put(f"{base}/template/{a}", json={"org_id": ORG_A, "amount": 720}).status_code == 200
    assert c.put(f"{base}/template/{a}", json={"org_id": ORG_A, "amount": 800}).status_code == 409
    # validation
    for bad in (0, -5, "abc", 1e9):
        assert c.put(f"{base}/template/{b}", json={"org_id": ORG_A, "amount": bad}).status_code == 400
    # cross-org, wrong type, unknown id -> 404 and nothing changed
    assert c.put(f"{base}/template/{a}", json={"org_id": ORG_B, "amount": 900}).status_code == 404
    other = "/api/hourly-charges" if ttype == "D" else "/api/daily-wages"
    assert c.put(f"{other}/template/{a}", json={"org_id": ORG_A, "amount": 900}).status_code == 404
    r = c.put(f"{base}/template/nope", json={"org_id": ORG_A, "amount": 900})
    assert r.status_code == 404 and r.json() == {"detail": "Template not found"}
    assert q(db, "SELECT temp_amount FROM daily_hourly_wages WHERE temp_id=%s", (a,))[0][0] == 720


@kinds
def test_assign_contract_idempotent_and_no_copy_rows(seed, db, base, ttype):
    c = seed
    t1 = create(c, base, 650).json()["id"]
    body = {"org_id": ORG_A, "user_ids": ["stf-1", "stf-2"], "temp_id": t1}
    r = c.post("/api/wages/assign", json=body)
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Staff assigned successfully", "assigned": 2, "moved_from": []}
    # idempotent
    r = c.post("/api/wages/assign", json=body)
    assert r.json()["moved_from"] == [] and r.json()["assigned"] == 2
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE temp_id=%s", (t1,))[0][0] == 2
    # no dead per-user copy rows are written
    assert q(db, "SELECT count(*) FROM daily_hourly_wages WHERE user_id IS NOT NULL")[0][0] == 0
    lst = c.get(f"{base}/templates/{ORG_A}").json()["data"]
    assert lst[0]["assigned_count"] == 2


@kinds
def test_assign_moves_staff_and_reports_moved_from(seed, db, base, ttype):
    c = seed
    t1 = create(c, base, 650).json()
    t2 = create(c, base, 700).json()
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1"], "temp_id": t1["id"]})
    r = c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1", "stf-3"], "temp_id": t2["id"]})
    assert r.status_code == 200
    assert r.json()["moved_from"] == [{"user_id": "stf-1", "temp_id": t1["id"], "temp_name": t1["temp_name"]}]
    assert r.json()["assigned"] == 2
    rows = q(db, "SELECT user_id, temp_id FROM assigned_wages ORDER BY user_id")
    assert rows == [("stf-1", t2["id"]), ("stf-3", t2["id"])]
    # a template of the OTHER type does not move the staff member
    other = "/api/hourly-charges" if ttype == "D" else "/api/daily-wages"
    o = create(c, other, 50).json()["id"]
    r = c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1"], "temp_id": o})
    assert r.json()["moved_from"] == []
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE user_id='stf-1'")[0][0] == 2


def test_assign_validation(seed, db):
    c = seed
    t = create(c, "/api/daily-wages", 650).json()["id"]
    tb = create(c, "/api/daily-wages", 650, org=ORG_B).json()["id"]
    post = lambda **kw: c.post("/api/wages/assign", json=kw)
    assert post(org_id=ORG_A, user_ids=[], temp_id=t).status_code == 400
    assert post(org_id=ORG_A, user_ids=[], temp_id=t).json() == {"detail": "Select at least one employee"}
    assert post(org_id=ORG_A, temp_id=t).status_code == 400
    assert post(user_ids=["stf-1"], temp_id=t).status_code == 400
    assert post(org_id=ORG_A, user_ids=["stf-1"]).status_code == 400
    # unknown / other-org / inactive / non-staff users -> 404 naming the id, nothing written
    for bad in ("ghost", "stf-b", "stf-4", "admin-a"):
        r = post(org_id=ORG_A, user_ids=["stf-1", bad], temp_id=t)
        assert r.status_code == 404 and bad in r.json()["detail"]
    assert q(db, "SELECT count(*) FROM assigned_wages")[0][0] == 0
    # template of another org / unknown template
    assert post(org_id=ORG_A, user_ids=["stf-1"], temp_id=tb).status_code == 404
    assert post(org_id=ORG_A, user_ids=["stf-1"], temp_id="nope").json() == {"detail": "Template not found"}


def test_assigned_unassigned_partition_and_cross_org(seed):
    c = seed
    t = create(c, "/api/daily-wages", 650).json()["id"]
    tb = create(c, "/api/daily-wages", 650, org=ORG_B).json()["id"]
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1"], "temp_id": t})
    a = c.get(f"/api/wages/assigned/{ORG_A}/{t}")
    assert a.status_code == 200
    assert a.json() == {"success": True, "data": [
        {"user_id": "stf-1", "name": "Aarav", "emp_id": "E-stf-1", "branch_name": "Head Office", "dept_name": "Sales"}]}
    u = c.get(f"/api/wages/unassigned/{ORG_A}/{t}").json()
    # active role 2/3 staff of the org not on this template: not stf-1, not inactive stf-4, not other org
    assert ids(u) == ["stf-2", "stf-3", "stf-5"]
    assert set(u["data"][0]) == {"user_id", "name", "emp_id", "branch_name", "dept_name"}
    # staff on ANOTHER template of the same type still appear as unassigned for this one
    t2 = create(c, "/api/daily-wages", 700).json()["id"]
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-2"], "temp_id": t2})
    assert "stf-2" in ids(c.get(f"/api/wages/unassigned/{ORG_A}/{t}").json())
    # cross-org / unknown template -> 404
    for side in ("assigned", "unassigned"):
        assert c.get(f"/api/wages/{side}/{ORG_A}/{tb}").status_code == 404
        assert c.get(f"/api/wages/{side}/{ORG_B}/{t}").status_code == 404
        assert c.get(f"/api/wages/{side}/{ORG_A}/nope").status_code == 404


def test_unassign(seed, db):
    c = seed
    t = create(c, "/api/daily-wages", 650).json()["id"]
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1", "stf-2"], "temp_id": t})
    r = c.delete(f"/api/wages/unassign/{ORG_A}/stf-1/{t}")
    assert r.status_code == 200 and r.json() == {"success": True, "message": "Staff unassigned successfully"}
    assert ids(c.get(f"/api/wages/assigned/{ORG_A}/{t}").json()) == ["stf-2"]
    # not assigned any more -> 404; wrong org -> 404 and the assignment survives
    assert c.delete(f"/api/wages/unassign/{ORG_A}/stf-1/{t}").status_code == 404
    r = c.delete(f"/api/wages/unassign/{ORG_B}/stf-2/{t}")
    assert r.status_code == 404 and r.json() == {"detail": "Assignment not found"}
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE user_id='stf-2'")[0][0] == 1


@kinds
def test_delete_cascades_in_one_go_and_is_scoped(seed, db, base, ttype):
    c = seed
    t1 = create(c, base, 650).json()
    t2 = create(c, base, 700).json()
    tb = create(c, base, 650, org=ORG_B).json()   # other org, same amount and name
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1", "stf-2"], "temp_id": t1["id"]})
    c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-3"], "temp_id": t2["id"]})
    c.post("/api/wages/assign", json={"org_id": ORG_B, "user_ids": ["stf-b"], "temp_id": tb["id"]})
    # legacy per-user copy rows (written by the old assign) for t1 and for the other org's template
    for org, name, uid in ((ORG_A, t1["temp_name"], "stf-1"), (ORG_B, tb["temp_name"], "stf-b")):
        q(db, """INSERT INTO daily_hourly_wages (org_id, temp_id, temp_name, temp_type, temp_amount, user_id)
                 VALUES (%s, %s, %s, %s, 650, %s)""", (org, str(uuid.uuid4()), name, ttype, uid))

    other = "/api/hourly-charges" if ttype == "D" else "/api/daily-wages"
    # wrong type / wrong org / unknown -> 404, nothing deleted
    assert c.delete(f"{other}/template/{t1['id']}").status_code == 404
    assert c.delete(f"{base}/template/{t1['id']}", params={"org_id": ORG_B}).status_code == 404
    assert c.delete(f"{base}/template/nope").status_code == 404
    assert q(db, "SELECT count(*) FROM assigned_wages")[0][0] == 4

    r = c.delete(f"{base}/template/{t1['id']}", params={"org_id": ORG_A})
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Template deleted successfully", "unassigned": 2}
    assert q(db, "SELECT count(*) FROM daily_hourly_wages WHERE temp_id=%s", (t1["id"],))[0][0] == 0
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE temp_id=%s", (t1["id"],))[0][0] == 0
    # org A copy row gone; other org's template, its assignment and its copy row untouched
    assert q(db, "SELECT count(*) FROM daily_hourly_wages WHERE org_id=%s AND user_id IS NOT NULL", (ORG_A,))[0][0] == 0
    assert q(db, "SELECT count(*) FROM daily_hourly_wages WHERE org_id=%s", (ORG_B,))[0][0] == 2
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE org_id=%s", (ORG_B,))[0][0] == 1
    assert q(db, "SELECT count(*) FROM assigned_wages WHERE temp_id=%s", (t2["id"],))[0][0] == 1
    # org_id query parameter is optional
    r = c.delete(f"{base}/template/{t2['id']}")
    assert r.status_code == 200 and r.json()["unassigned"] == 1
    assert c.delete(f"{base}/template/{t2['id']}").status_code == 404


def test_delete_template_other_type_assignments_survive(seed, db):
    c = seed
    d = create(c, "/api/daily-wages", 650).json()["id"]
    h = create(c, "/api/hourly-charges", 50).json()["id"]
    for t in (d, h):
        c.post("/api/wages/assign", json={"org_id": ORG_A, "user_ids": ["stf-1"], "temp_id": t})
    c.delete(f"/api/daily-wages/template/{d}")
    assert q(db, "SELECT temp_id FROM assigned_wages WHERE user_id='stf-1'") == [(h,)]


# ----------------------------------------------------------------------------------------------------
# Shift assignment
# ----------------------------------------------------------------------------------------------------

def test_shift_staff_contract_and_partition(seed, db):
    c = seed
    q(db, "UPDATE users_additional_info SET shift_id=1 WHERE user_id IN ('stf-1','stf-2')")
    q(db, "UPDATE users_additional_info SET shift_id=2 WHERE user_id='stf-3'")
    a = c.get(f"/shift/assigned_staff/{ORG_A}/1")
    assert a.status_code == 200
    body = a.json()
    assert body["success"] is True
    assert body["data"][0] == {
        "user_id": "stf-1", "name": "Aarav", "emp_id": "E-stf-1", "mobile_number": body["data"][0]["mobile_number"],
        "email": "stf-1@x.test", "dept_name": "Sales", "branch_name": "Head Office",
        "role_id": 3, "is_active": True, "profile_photo_url": None}
    assert ids(body) == ["stf-1", "stf-2"]
    u = c.get(f"/shift/unassigned_staff/{ORG_A}/1").json()
    # stf-3 on another shift, stf-5 has no additional-info row; inactive stf-4, admin and other org excluded
    assert ids(u) == ["stf-3", "stf-5"]
    assert set(u["data"][0]) == set(body["data"][0])
    # partition: disjoint, union == all active role 2/3 staff of the org
    all_active = {r[0] for r in q(db, "SELECT user_id FROM users WHERE parent_id=%s AND role_id IN (2,3) AND status=1", (ORG_A,))}
    assert set(ids(body)) | set(ids(u)) == all_active
    assert set(ids(body)) & set(ids(u)) == set()
    # shift_id 0 counts as "none"
    q(db, "UPDATE users_additional_info SET shift_id=0 WHERE user_id='stf-1'")
    assert "stf-1" in ids(c.get(f"/shift/unassigned_staff/{ORG_A}/1").json())


def test_shift_staff_cross_org_and_unknown_shift(seed, db):
    c = seed
    for side in ("assigned_staff", "unassigned_staff"):
        r = c.get(f"/shift/{side}/{ORG_A}/3")      # shift 3 belongs to org B
        assert r.status_code == 404 and r.json() == {"detail": "Shift not found"}
        assert c.get(f"/shift/{side}/{ORG_A}/99").status_code == 404
        assert c.get(f"/shift/{side}/{ORG_B}/1").status_code == 404
    q(db, "UPDATE shift SET status=0 WHERE shift_id=2")
    assert c.get(f"/shift/assigned_staff/{ORG_A}/2").status_code == 404


def test_assign_staff_updates_shift_id_and_is_idempotent(seed, db):
    c = seed
    body = {"org_id": ORG_A, "user_ids": ["stf-1", "stf-5"], "shift_id": 1}
    r = c.post("/shift/assign_staff", json=body)
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Staff assigned successfully", "assigned": 2}
    shift_of = lambda uid: q(db, "SELECT shift_id FROM users_additional_info WHERE user_id=%s", (uid,))
    assert shift_of("stf-1") == [(1,)]
    assert shift_of("stf-5") == [(1,)]       # additional-info row created for older data
    assert c.post("/shift/assign_staff", json=body).status_code == 200
    assert q(db, "SELECT count(*) FROM users_additional_info WHERE user_id='stf-5'")[0][0] == 1
    assert ids(c.get(f"/shift/assigned_staff/{ORG_A}/1").json()) == ["stf-1", "stf-5"]
    # moving to another shift replaces the assignment
    c.post("/shift/assign_staff", json={"org_id": ORG_A, "user_ids": ["stf-1"], "shift_id": 2})
    assert shift_of("stf-1") == [(2,)]
    assert ids(c.get(f"/shift/assigned_staff/{ORG_A}/1").json()) == ["stf-5"]


def test_assign_staff_validation(seed, db):
    c = seed
    post = lambda **kw: c.post("/shift/assign_staff", json=kw)
    assert post(org_id=ORG_A, user_ids=[], shift_id=1).status_code == 400
    assert post(org_id=ORG_A, shift_id=1).status_code == 400
    assert post(org_id=ORG_A, user_ids=["stf-1"]).status_code == 400
    assert post(user_ids=["stf-1"], shift_id=1).status_code == 400
    assert post(org_id=ORG_A, user_ids=["stf-1"], shift_id=3).status_code == 404     # shift of another org
    assert post(org_id=ORG_A, user_ids=["stf-1"], shift_id=99).json() == {"detail": "Shift not found"}
    for bad in ("ghost", "stf-b", "stf-4", "admin-a"):
        r = post(org_id=ORG_A, user_ids=["stf-1", bad], shift_id=1)
        assert r.status_code == 404 and bad in r.json()["detail"]
    # all-or-nothing: stf-1 was not assigned by the rejected requests
    assert q(db, "SELECT shift_id FROM users_additional_info WHERE user_id='stf-1'") == [(None,)]


def test_unassign_staff(seed, db):
    c = seed
    q(db, "UPDATE users_additional_info SET shift_id=1 WHERE user_id IN ('stf-1','stf-2')")
    r = c.delete(f"/shift/unassign_staff/{ORG_A}/stf-1/1")
    assert r.status_code == 200 and r.json() == {"success": True, "message": "Staff unassigned successfully"}
    assert q(db, "SELECT shift_id FROM users_additional_info WHERE user_id='stf-1'") == [(None,)]
    assert q(db, "SELECT shift_id FROM users_additional_info WHERE user_id='stf-2'") == [(1,)]
    # unassign from a shift the staff member is not on is a no-op
    r = c.delete(f"/shift/unassign_staff/{ORG_A}/stf-2/2")
    assert r.status_code == 200
    assert q(db, "SELECT shift_id FROM users_additional_info WHERE user_id='stf-2'") == [(1,)]
    # unknown staff / other org's staff / other org's shift -> 404
    assert c.delete(f"/shift/unassign_staff/{ORG_A}/ghost/1").status_code == 404
    assert c.delete(f"/shift/unassign_staff/{ORG_A}/stf-b/1").status_code == 404
    assert c.delete(f"/shift/unassign_staff/{ORG_A}/stf-2/3").status_code == 404
    assert q(db, "SELECT shift_id FROM users_additional_info WHERE user_id='stf-2'") == [(1,)]


def test_deleting_shift_empties_assigned_list(seed, db):
    c = seed
    c.post("/shift/assign_staff", json={"org_id": ORG_A, "user_ids": ["stf-1", "stf-2"], "shift_id": 2})
    assert len(c.get(f"/shift/assigned_staff/{ORG_A}/2").json()["data"]) == 2
    assert c.delete("/shift/delete/2", params={"org_id": ORG_A}).status_code == 200
    assert q(db, "SELECT count(*) FROM users_additional_info WHERE shift_id=2")[0][0] == 0
    assert c.get(f"/shift/assigned_staff/{ORG_A}/2").status_code == 404
