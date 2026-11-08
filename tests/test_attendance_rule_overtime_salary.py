"""Attendance rule, overtime report and salary paid state, against a real PostgreSQL database.

Runs inside a temporary schema `clockyn_dev_test_<hex>` of an EXISTING database (no database is created or dropped and
no other schema is touched: see tests/pg_scratch.py). The shared DDL file is loaded into that schema, the two new tables
are created if the DDL does not have them yet, and the schema is dropped at the end.
"""
import asyncio
import itertools
import os
import sys
import time as _time
import types
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest
import pg_scratch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IST = timezone(timedelta(hours=5, minutes=30))
MONTH, YEAR = 10, 2026

NEW_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS attendance_rule (
  org_id               varchar(36) PRIMARY KEY,
  late_punch_in_count  int         NOT NULL CHECK (late_punch_in_count BETWEEN 1 AND 10),
  consider_as          varchar(10) NOT NULL CHECK (consider_as IN ('Half Day','Leave')),
  grace_minutes        int         NOT NULL DEFAULT 0 CHECK (grace_minutes BETWEEN 0 AND 120),
  updated_by           varchar(36),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS salary_payments (
  id         int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     varchar(36)  NOT NULL,
  user_id    varchar(36)  NOT NULL,
  pay_month  smallint     NOT NULL CHECK (pay_month BETWEEN 1 AND 12),
  pay_year   smallint     NOT NULL CHECK (pay_year BETWEEN 2000 AND 2100),
  net_amount numeric(12,2) NOT NULL,
  paid_at    timestamptz  NOT NULL DEFAULT now(),
  paid_by    varchar(36),
  UNIQUE (org_id, user_id, pay_month, pay_year));
CREATE INDEX IF NOT EXISTS idx_salary_payments_org_period ON salary_payments (org_id, pay_year, pay_month);
"""


@pytest.fixture(scope="module")
def db():
    conn = pg_scratch.connect()
    schema = pg_scratch.create_scratch_schema(conn)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(pg_scratch.rewrite_ddl((ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8"), schema))
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(NEW_TABLES_DDL)
        # Two attendance sessions on one date need the one-row-per-day unique index gone.
        cur.execute("DROP INDEX IF EXISTS attendance_user_id_attendance_date_key")

        os.environ.update(pg_scratch.app_env(schema))
        from core_functions import db_function
        db_function._connection_pool = None
        yield conn
    finally:
        from core_functions import db_function
        if db_function._connection_pool is not None:
            db_function._connection_pool.closeall()
            db_function._connection_pool = None
        pg_scratch.drop_scratch_schema(conn, schema)
        conn.close()


def _ensure_timezone_module():
    """The app's timezone module needs OS tz data; fall back to a pytz-backed stand-in when absent."""
    try:
        import utils.timezone  # noqa: F401
    except Exception:
        import pytz
        stub = types.ModuleType("utils.timezone")
        stub.UTC = pytz.UTC
        stub.IST = pytz.timezone("Asia/Kolkata")
        stub.get_utc_now = lambda: datetime.now(pytz.UTC)
        stub.convert_utc_to_ist = lambda dt: dt.astimezone(stub.IST) if dt else None
        sys.modules["utils.timezone"] = stub
        import utils
        utils.timezone = stub


@pytest.fixture(scope="module")
def client(db):
    _ensure_timezone_module()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from router import attendance_rule, incentive, salary_payments
    from core_functions import gen_plans_functions as gp

    async def _no_gate(*args, **kwargs):          # plan gating is covered by tests/test_plan_gating.py
        return None
    gp.check_plan_feature = _no_gate
    app = FastAPI()
    for r in (attendance_rule.router, incentive.router, salary_payments.router):
        app.include_router(r)
    return TestClient(app)


# ---------------------------------------------------------------- fixtures data helpers

_shift_ids = itertools.count(90000)


class Org:
    def __init__(self, db):
        self.db = db
        self.cur = db.cursor()
        self.id = "org-" + uuid.uuid4().hex[:8]
        self._n = 0
        self.cur.execute(
            "INSERT INTO org_info (org_id, org_name, org_address, org_latitude, org_longitude) VALUES (%s,%s,'addr',0,0)",
            (self.id, "Org " + self.id))

    def staff(self, name="Staff", role=2, status=1, shift=None):
        self._n += 1
        uid = f"{self.id}-u{self._n}"
        self.cur.execute(
            """INSERT INTO users (user_id, name, emp_id, role_id, parent_id, mobile_number, email, status)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uid, name, f"E{self._n}", role, self.id, "9" + str(self._n).zfill(9), f"{uid}@example.com", status))
        self.cur.execute(
            """INSERT INTO users_additional_info (user_id, acc_no, ifsc_code, bank_name, bank_branch, gender, address,
                   emergency_contact_no, shift_id) VALUES (%s,'1','1','b','b','M','a','1',%s)""",
            (uid, shift))
        return uid

    def shift(self, start, end):
        sid = next(_shift_ids)
        self.cur.execute(
            "INSERT INTO shift (shift_id, shift_name, start_time, end_time, org_id) VALUES (%s,'S',%s,%s,%s)",
            (sid, start, end, self.id))
        return sid

    def attend(self, uid, day, punch_in_ist, hours, punch_out=True):
        """One session; `punch_in_ist` is 'HH:MM' on `day` in IST, stored as UTC-aware timestamptz."""
        h, m = map(int, punch_in_ist.split(":"))
        pin = datetime.combine(day, time(h, m), tzinfo=IST)
        pout = pin + timedelta(hours=hours) if punch_out else None
        self.cur.execute(
            """INSERT INTO attendance (user_id, org_id, attendance_date, punch_in, punch_out, work_hours)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (uid, self.id, day, pin, pout, hours if punch_out else None))

    def overtime_template(self, uids, duration=30, amount=50):
        iid = "ot-" + uuid.uuid4().hex[:10]
        self.cur.execute(
            "INSERT INTO incentive_templates (org_id, type, template_name, incentive_id) VALUES (%s,'OVERTIME','OT',%s)",
            (self.id, iid))
        self.cur.execute(
            "INSERT INTO incentive_overtime_details (incentive_id, duration_minutes, amount) VALUES (%s,%s,%s)",
            (iid, duration, amount))
        for u in uids:
            self.cur.execute("INSERT INTO assigned_incentives (org_id, user_id, incentive_id) VALUES (%s,%s,%s)",
                             (self.id, u, iid))
        return iid

    def payslip(self, uid, basic=30000, hra=1000, epf=500):
        psid = "ps-" + uuid.uuid4().hex[:10]
        self.cur.execute(
            """INSERT INTO pay_slip (pay_slip_id, pay_slip_name, org_id, basic_pay, hra, epf, professional_tax, health_insurance)
               VALUES (%s,'T',%s,%s,%s,%s,0,0)""", (psid, self.id, basic, hra, epf))
        self.cur.execute("INSERT INTO payroll (org_id, user_id, pay_slip_id) VALUES (%s,%s,%s)", (self.id, uid, psid))
        return psid

    def wage(self, uid, kind, amount):
        tid = "w-" + uuid.uuid4().hex[:10]
        self.cur.execute(
            """INSERT INTO daily_hourly_wages (org_id, temp_id, temp_name, temp_type, temp_amount)
               VALUES (%s,%s,'W',%s,%s)""", (self.id, tid, kind, amount))
        self.cur.execute("INSERT INTO assigned_wages (org_id, user_id, temp_id) VALUES (%s,%s,%s)",
                         (self.id, uid, tid))


@pytest.fixture
def org(db):
    return Org(db)


def d(day):
    return date(2026, 10, day)


# ---------------------------------------------------------------- attendance rule: pure helpers

def test_is_late_boundaries_day_shift():
    from core_functions.attendance_rule_functions import is_late
    start, end = time(9, 0), time(18, 0)
    assert is_late(time(8, 59), start, end) is False
    assert is_late(time(9, 0), start, end) is False
    assert is_late(time(9, 1), start, end) is True
    assert is_late(time(9, 10), start, end, grace_minutes=10) is False
    assert is_late(time(9, 11), start, end, grace_minutes=10) is True


def test_is_late_night_shift():
    from core_functions.attendance_rule_functions import is_late
    start, end = time(21, 0), time(6, 0)
    assert is_late(time(20, 30), start, end) is False
    assert is_late(time(21, 0), start, end) is False
    assert is_late(time(21, 30), start, end) is True
    assert is_late(time(0, 30), start, end) is True      # arrived after midnight


@pytest.mark.parametrize("late,count,consider,expected", [
    (0, 3, "Half Day", 0.0), (1, 1, "Half Day", 0.5), (1, 1, "Leave", 1.0),
    (2, 3, "Half Day", 0.0), (3, 3, "Half Day", 0.5), (7, 3, "Leave", 2.0),
    (9, 10, "Leave", 0.0), (10, 10, "Leave", 1.0), (25, 10, "Half Day", 1.0),
])
def test_penalty_days_for(late, count, consider, expected):
    from core_functions.attendance_rule_functions import penalty_days_for
    assert penalty_days_for(late, count, consider) == expected


# ---------------------------------------------------------------- attendance rule: DB helpers

def test_late_days_for_user_month_utc_storage(db, org):
    from core_functions.attendance_rule_functions import late_days_for_user_month
    sid = org.shift("09:00", "18:00")
    u = org.staff(shift=sid)
    org.attend(u, d(1), "08:59", 9)     # on time
    org.attend(u, d(2), "09:00", 9)     # on time (not later)
    org.attend(u, d(3), "09:01", 9)     # late
    org.attend(u, d(6), "10:30", 8)     # late
    org.attend(u, date(2026, 9, 30), "11:00", 8)   # previous month, ignored
    assert late_days_for_user_month(u, YEAR, MONTH) == 2
    assert late_days_for_user_month(u, YEAR, MONTH, grace_minutes=2) == 1


def test_late_days_uses_first_punch_of_the_day(db, org):
    from core_functions.attendance_rule_functions import late_days_for_user_month
    sid = org.shift("09:00", "18:00")
    u = org.staff(shift=sid)
    org.attend(u, d(1), "08:55", 3)
    org.attend(u, d(1), "13:00", 5)     # second session later; first one was on time
    assert late_days_for_user_month(u, YEAR, MONTH) == 0


def test_late_days_night_shift_and_no_shift(db, org):
    from core_functions.attendance_rule_functions import late_days_for_user_month
    night = org.staff(shift=org.shift("21:00", "06:00"))
    org.attend(night, d(1), "20:45", 9)
    org.attend(night, d(2), "21:20", 9)
    assert late_days_for_user_month(night, YEAR, MONTH) == 1
    noshift = org.staff()
    org.attend(noshift, d(1), "13:00", 4)
    assert late_days_for_user_month(noshift, YEAR, MONTH) == 0
    zero = org.staff(shift=0)
    org.attend(zero, d(1), "13:00", 4)
    assert late_days_for_user_month(zero, YEAR, MONTH) == 0


def test_penalty_for_user_month_configured_and_not(db, org):
    from core_functions.attendance_rule_functions import penalty_for_user_month, save_rule
    u = org.staff(shift=org.shift("09:00", "18:00"))
    for day in (1, 2, 3, 6):
        org.attend(u, d(day), "09:30", 8)
    assert penalty_for_user_month(org.id, u, YEAR, MONTH) == {
        "configured": False, "late_days": 0, "penalty_units": 0, "penalty_days": 0.0}
    save_rule(org.id, 2, "Leave")
    assert penalty_for_user_month(org.id, u, YEAR, MONTH) == {
        "configured": True, "late_days": 4, "penalty_units": 2, "penalty_days": 2.0}


# ---------------------------------------------------------------- attendance rule: API

def test_rule_get_default_contract(client, org):
    r = client.get(f"/api/attendance-rule/{org.id}")
    assert r.status_code == 200
    assert r.json() == {"success": True, "late_punch_in_count": 3, "consider_as": "Half Day", "configured": False}


def test_rule_post_then_get_and_idempotent(client, org, db):
    url = f"/api/attendance-rule/{org.id}"
    r = client.post(url, json={"late_punch_in_count": 2, "consider_as": "Leave"})
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Attendance rule saved",
                        "late_punch_in_count": 2, "consider_as": "Leave"}
    assert client.get(url).json() == {"success": True, "late_punch_in_count": 2, "consider_as": "Leave",
                                      "configured": True}
    again = client.post(url, json={"late_punch_in_count": 2, "consider_as": "Leave"})
    assert again.status_code == 200
    client.post(url, json={"late_punch_in_count": 5, "consider_as": "Half Day"})
    assert client.get(url).json()["late_punch_in_count"] == 5
    org.cur.execute("SELECT count(*) FROM attendance_rule WHERE org_id=%s", (org.id,))
    assert org.cur.fetchone()[0] == 1


@pytest.mark.parametrize("body", [
    {"late_punch_in_count": 0, "consider_as": "Leave"},
    {"late_punch_in_count": 11, "consider_as": "Leave"},
    {"late_punch_in_count": 3, "consider_as": "Absent"},
    {"late_punch_in_count": "3", "consider_as": "Leave"},
    {"late_punch_in_count": 2.5, "consider_as": "Leave"},
    {"late_punch_in_count": True, "consider_as": "Leave"},
    {"late_punch_in_count": 3},
    {},
])
def test_rule_validation_422_and_nothing_stored(client, org, body):
    r = client.post(f"/api/attendance-rule/{org.id}", json=body)
    assert r.status_code == 422
    assert client.get(f"/api/attendance-rule/{org.id}").json()["configured"] is False


def test_rule_unknown_org_404(client):
    assert client.get("/api/attendance-rule/org-nope").status_code == 404
    r = client.post("/api/attendance-rule/org-nope", json={"late_punch_in_count": 2, "consider_as": "Leave"})
    assert r.status_code == 404


def test_rule_isolated_between_orgs(client, db):
    a, b = Org(db), Org(db)
    client.post(f"/api/attendance-rule/{a.id}", json={"late_punch_in_count": 7, "consider_as": "Leave"})
    assert client.get(f"/api/attendance-rule/{b.id}").json()["configured"] is False


def test_save_rule_function_validates_directly(db, org):
    from fastapi import HTTPException
    from core_functions.attendance_rule_functions import save_rule
    for bad in ((0, "Leave"), (11, "Leave"), (3, "Absent"), (True, "Leave")):
        with pytest.raises(HTTPException) as e:
            save_rule(org.id, *bad)
        assert e.value.status_code == 422


# ---------------------------------------------------------------- overtime

def test_ot_figures_30_minute_slots():
    from core_functions.overtime import ot_figures
    ot_min, amount = ot_figures(worked_minutes=1000, shift_minutes=480, present_days=1, duration_minutes=30, amount=50)
    assert ot_min == 520 and amount == 17 * 50
    ot_min, amount = ot_figures(575, 480, 1, 30, 50)       # 95 OT minutes -> 3 slots
    assert ot_min == 95 and amount == 150
    ot_min, amount = ot_figures(300, 480, 1, 30, 50)       # short day -> nothing
    assert ot_min == 0 and amount == 0


def _ot(client, org, month=MONTH, year=YEAR):
    r = client.get(f"/api/incentive/overtime-report/{org.id}", params={"month": month, "year": year})
    assert r.status_code == 200, r.text
    return {row["user_id"]: row for row in r.json()["data"]}


def test_overtime_single_session_over_and_under_shift(client, org):
    sid = org.shift("09:00", "17:00")      # 480
    over, under = org.staff("A"), org.staff("B")
    org.overtime_template([over, under], duration=30, amount=50)
    org.attend(over, d(1), "09:00", 9.5)   # 570 -> 90 ot -> 3 slots
    org.cur.execute("UPDATE users_additional_info SET shift_id=%s WHERE user_id IN (%s,%s)", (sid, over, under))
    org.attend(under, d(1), "09:00", 7)
    rows = _ot(client, org)
    assert rows[over] == {"user_id": over, "ot_days": 1, "ot_minutes": 90, "ot_amount": 150.0}
    assert rows[under] == {"user_id": under, "ot_days": 0, "ot_minutes": 0, "ot_amount": 0.0}


def test_overtime_two_sessions_same_day_subtract_shift_once(client, org):
    sid = org.shift("09:00", "18:00")      # 540
    u = org.staff(shift=sid)
    org.overtime_template([u], duration=30, amount=50)
    org.attend(u, d(1), "09:00", 5)
    org.attend(u, d(1), "15:00", 5)        # 600 worked on one date -> 60 ot, not 0
    assert _ot(client, org)[u] == {"user_id": u, "ot_days": 1, "ot_minutes": 60, "ot_amount": 100.0}


def test_overtime_night_shift_wrapped_duration(client, org):
    sid = org.shift("21:00", "06:00")      # 540 with wrap
    u = org.staff(shift=sid)
    org.overtime_template([u], duration=30, amount=50)
    org.attend(u, d(1), "21:00", 10)       # 600 -> 60 ot
    assert _ot(client, org)[u]["ot_minutes"] == 60
    assert _ot(client, org)[u]["ot_amount"] == 100.0


def test_overtime_monthly_aggregate_short_day_offsets_long_day(client, org):
    sid = org.shift("09:00", "17:00")
    u = org.staff(shift=sid)
    org.overtime_template([u], duration=30, amount=50)
    org.attend(u, d(1), "09:00", 10)       # +120
    org.attend(u, d(2), "09:00", 7)        # -60
    row = _ot(client, org)[u]
    assert row["ot_minutes"] == 60 and row["ot_days"] == 1 and row["ot_amount"] == 100.0


def test_overtime_no_shift_all_worked_minutes_are_overtime(client, org):
    u = org.staff()
    org.overtime_template([u], duration=60, amount=100)
    org.attend(u, d(1), "09:00", 2.5)
    assert _ot(client, org)[u]["ot_minutes"] == 150
    assert _ot(client, org)[u]["ot_amount"] == 200.0


def test_overtime_membership_and_empty_user(client, org):
    sid = org.shift("09:00", "17:00")
    with_t, no_t, idle, inactive, admin_role = (org.staff("A"), org.staff("B"), org.staff("C"),
                                                org.staff("D", status=0), org.staff("E", role=1))
    org.overtime_template([with_t, idle, inactive, admin_role], duration=30, amount=50)
    org.attend(with_t, d(1), "09:00", 9)
    org.attend(no_t, d(1), "09:00", 12)
    rows = _ot(client, org)
    assert set(rows) == {with_t, idle}
    assert rows[idle] == {"user_id": idle, "ot_days": 0, "ot_minutes": 0, "ot_amount": 0.0}


def test_overtime_other_month_and_other_org_ignored(client, org, db):
    u = org.staff()
    org.overtime_template([u], duration=30, amount=50)
    org.attend(u, date(2026, 9, 30), "09:00", 5)
    org.attend(u, date(2026, 11, 1), "09:00", 5)
    assert _ot(client, org)[u]["ot_minutes"] == 0
    other = Org(db)
    assert _ot(client, other) == {}


def test_overtime_open_session_counts_as_present_without_minutes(client, org):
    sid = org.shift("09:00", "17:00")
    u = org.staff(shift=sid)
    org.overtime_template([u], duration=30, amount=50)
    org.attend(u, d(1), "09:00", 10)               # +120
    org.attend(u, d(2), "09:00", 0, punch_out=False)  # present, no closed minutes: -480
    assert _ot(client, org)[u]["ot_minutes"] == 0


def test_overtime_contract_default_month_and_validation(client, org):
    u = org.staff()
    org.overtime_template([u], duration=30, amount=50)
    r = client.get(f"/api/incentive/overtime-report/{org.id}")      # current IST month default
    assert r.status_code == 200 and r.json()["success"] is True
    body = client.get(f"/api/incentive/overtime-report/{org.id}", params={"month": 10, "year": 2026}).json()
    assert body["success"] is True and isinstance(body["data"], list)
    assert set(body["data"][0]) == {"user_id", "ot_days", "ot_minutes", "ot_amount"}
    for bad in ({"month": 13}, {"month": 0}, {"month": "x"}, {"month": 10, "year": 1999}):
        assert client.get(f"/api/incentive/overtime-report/{org.id}", params=bad).status_code == 422


def test_overtime_compute_overtime_user_filter(db, org):
    from core_functions.overtime import compute_overtime
    a, b = org.staff("A"), org.staff("B")
    org.overtime_template([a, b], duration=30, amount=50)
    assert [r["user_id"] for r in compute_overtime([a], org.id, YEAR, MONTH)] == [a]
    assert compute_overtime([], org.id, YEAR, MONTH) == []
    assert {r["user_id"] for r in compute_overtime(None, org.id, YEAR, MONTH)} == {a, b}


# ---------------------------------------------------------------- salary paid state

def _engine(uid):
    _ensure_timezone_module()
    from core_functions.report_functions import get_staff_salary
    return asyncio.run(get_staff_salary(uid, YEAR, MONTH))["data"]


def _list(client, org):
    r = client.get("/salary/monthly-list", params={"org_id": org.id, "month": MONTH, "year": YEAR})
    assert r.status_code == 200, r.text
    return {row["user_id"]: row for row in r.json()["data"]}


def _pay(client, org, ids, month=MONTH, year=YEAR):
    return client.post("/salary/mark-paid", json={"org_id": org.id, "user_ids": ids, "month": month, "year": year})


def _salary_rows(org, extra=""):
    org.cur.execute("SELECT count(*) FROM salary_payments WHERE org_id=%s " + extra, (org.id,))
    return org.cur.fetchone()[0]


def test_monthly_list_contract_and_values(client, org):
    a = org.staff("Aarav")
    org.payslip(a, basic=30000, hra=1000, epf=500)
    r = client.get("/salary/monthly-list", params={"org_id": org.id, "month": MONTH, "year": YEAR})
    assert r.json() == {"success": True, "data": [{
        "user_id": a, "name": "Aarav", "emp_id": "E1", "dept_name": "", "branch_name": "",
        "salary_type": "MONTHLY_FIXED", "basic_salary": 30000.0, "net_salary": 30500.0,
        "paid": False, "paid_date": None}]}


def test_monthly_list_net_equals_engine_for_unpaid(client, org):
    a, b = org.staff("A"), org.staff("B")
    org.payslip(a)
    org.wage(b, "D", 400)
    org.attend(b, d(1), "09:00", 8)
    org.attend(b, d(2), "09:00", 8)
    rows = _list(client, org)
    for uid in (a, b):
        assert rows[uid]["net_salary"] == _engine(uid)["net_salary"]
    assert rows[b]["net_salary"] == 800.0 and rows[b]["basic_salary"] == 0.0


def test_salary_type_enum_keys(client, org):
    m, dw, h, none = org.staff("M"), org.staff("D"), org.staff("H"), org.staff("N")
    org.payslip(m)
    org.wage(dw, "D", 100)
    org.wage(h, "H", 50)
    rows = _list(client, org)
    assert rows[m]["salary_type"] == "MONTHLY_FIXED"
    assert rows[dw]["salary_type"] == "DAILY_WAGES"
    assert rows[h]["salary_type"] == "HOURLY"
    assert rows[none]["salary_type"] == "MONTHLY_FIXED"


def test_mark_paid_flow_and_idempotency(client, org):
    a, b = org.staff("A"), org.staff("B")
    org.payslip(a, basic=30000, hra=1000, epf=500)
    org.payslip(b, basic=20000, hra=0, epf=0)
    assert not any(r["paid"] for r in _list(client, org).values())

    r = _pay(client, org, [a, b])
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Salary marked as paid for 2 employee(s)",
                        "marked": 2, "already_paid": 0}
    today_ist = datetime.now(IST).date().isoformat()
    rows = _list(client, org)
    assert rows[a]["paid"] is True and rows[a]["paid_date"] == today_ist and rows[a]["net_salary"] == 30500.0
    assert rows[b]["paid"] is True and rows[b]["net_salary"] == 20000.0

    again = _pay(client, org, [a, b]).json()
    assert again["marked"] == 0 and again["already_paid"] == 2
    partial = _pay(client, org, [a, a])      # duplicate ids collapse
    assert partial.json()["already_paid"] == 1
    assert _salary_rows(org) == 2


def test_mark_paid_partial_overlap(client, org):
    a, b = org.staff("A"), org.staff("B")
    org.payslip(a)
    org.payslip(b)
    _pay(client, org, [a])
    r = _pay(client, org, [a, b]).json()
    assert r["marked"] == 1 and r["already_paid"] == 1


def test_paid_snapshot_survives_payslip_edit(client, org):
    a, b = org.staff("A"), org.staff("B")
    psa = org.payslip(a, basic=30000, hra=0, epf=0)
    org.payslip(b, basic=30000, hra=0, epf=0)
    _pay(client, org, [a])
    org.cur.execute("UPDATE pay_slip SET basic_pay = 45000 WHERE org_id=%s", (org.id,))
    rows = _list(client, org)
    assert rows[a]["paid"] is True and rows[a]["net_salary"] == 30000.0      # snapshot
    assert rows[b]["paid"] is False and rows[b]["net_salary"] == 45000.0     # live
    # intentional change (WP4b): a paid month is returned as it was paid, components included. Before the snapshot
    # column existed only the net amount was stored and basic_salary followed the edited template (45000.0).
    assert rows[a]["basic_salary"] == 30000.0


def test_mark_paid_foreign_user_404_and_nothing_written(client, org, db):
    other = Org(db)
    mine = org.staff("A")
    org.payslip(mine)
    foreign = other.staff("X")
    inactive = org.staff("I", status=0)
    admin_role = org.staff("R", role=1)
    for bad in (foreign, inactive, admin_role, "ghost-user"):
        r = _pay(client, org, [mine, bad])
        assert r.status_code == 404 and bad in r.json()["detail"]
    assert _salary_rows(org) == 0 and _salary_rows(other) == 0


def test_mark_paid_validation(client, org):
    a = org.staff("A")
    org.payslip(a)
    assert _pay(client, org, [a], month=13).status_code == 422
    assert _pay(client, org, [a], month=0).status_code == 422
    assert _pay(client, org, [a], year=1999).status_code == 422
    r = _pay(client, org, [])
    assert r.status_code == 400 and r.json()["detail"] == "Select at least one employee"
    r = _pay(client, org, [f"u{i}" for i in range(501)])
    assert r.status_code == 400
    assert client.post("/salary/mark-paid", json={"org_id": org.id, "user_ids": [a], "month": "x",
                                                  "year": YEAR}).status_code == 422
    assert _salary_rows(org) == 0
    assert client.get("/salary/monthly-list", params={"org_id": org.id, "month": 13, "year": YEAR}).status_code == 422


def test_paid_state_is_per_month_and_per_org(client, org, db):
    other = Org(db)
    a = org.staff("A")
    org.payslip(a)
    o = other.staff("O")
    other.payslip(o)
    _pay(client, org, [a])
    assert _list(client, other)[o]["paid"] is False
    r = client.get("/salary/monthly-list", params={"org_id": org.id, "month": 11, "year": YEAR}).json()
    assert r["data"][0]["paid"] is False


def test_failing_engine_aborts_mark_paid_without_writes(client, org, monkeypatch):
    from core_functions import salary_payment_functions as spf
    a, b = org.staff("A"), org.staff("B")
    calls = []

    def fake(org_id, user_ids, year, month):
        calls.append(user_ids)
        ok = {"net_salary": 100.0, "earnings": {"basic": 100.0}}
        return [{"user_id": a, "error": False, "data": ok}, {"user_id": b, "error": True, "data": None}]

    monkeypatch.setattr(spf, "_engine_rows", fake)       # the batch engine reports one failing employee
    assert _pay(client, org, [a, b]).status_code == 500
    assert _salary_rows(org) == 0


# ---------------------------------------------------------------- WP4b: shared engine (two sessions, fine, rate table)

def test_two_sessions_one_day_count_once_for_the_daily_wage(db, org):
    u = org.staff()
    org.wage(u, "D", 500)
    org.attend(u, d(1), "09:00", 4)
    org.attend(u, d(1), "14:00", 5)
    data = _engine(u)
    # intentional change (WP4b): legacy counted attendance rows (2 days = 1000.0); now one date is one present day
    assert data["present_days"] == 1 and data["earnings"]["daily_wages"] == 500.0
    assert data["total_hours"] == 9.0


def test_fine_follows_the_attendance_rule_and_not_before_it_is_saved(db, org):
    from core_functions.attendance_rule_functions import save_rule
    u = org.staff(shift=org.shift("09:00", "18:00"))
    org.payslip(u, basic=30000, hra=0, epf=0)
    for day in (1, 2, 3):
        org.attend(u, d(day), "09:30", 9)                       # three late first punches
    assert _engine(u)["deductions"]["fine"] == 0.0               # no rule saved: nothing changes (legacy)
    save_rule(org.id, 3, "Half Day")
    data = _engine(u)
    # 3 late days / 3 = 1 unit x 0.5 day; October 2026 has 27 working days (Sundays off) -> 0.5 x 30000 / 27
    assert data["late_days"] == 3 and data["penalty_days"] == 0.5
    assert data["deductions"]["fine"] == 555.56 and data["net_salary"] == 29444.44


def test_allowance_rate_table_stamps_pending_rows_only(db, org):
    from core_functions import emp_expenses_functions as exp
    u = org.staff()

    def reading(rid, day, km):
        org.cur.execute(
            """INSERT INTO odometer_readings (user_id, org_id, reading_date, start_km, stop_km, distance_km, status,
                   readings_id) VALUES (%s,%s,%s,0,%s,%s,'pending',%s)""", (u, org.id, d(day), km, km, rid))

    def rate_of(rid):
        org.cur.execute("SELECT allowance_rate FROM odometer_readings WHERE readings_id=%s", (rid,))
        return org.cur.fetchone()[0]

    r1, r2 = "r-" + uuid.uuid4().hex[:8], "r-" + uuid.uuid4().hex[:8]
    reading(r1, 1, 10)
    reading(r2, 2, 20)
    assert asyncio.run(exp.get_allowance_rate(org.id)) == {"success": True, "rate": 3.0, "is_default": True}
    assert asyncio.run(exp.save_allowance_rate(org.id, 3.5))["rate"] == 3.5
    assert asyncio.run(exp.get_allowance_rate(org.id)) == {"success": True, "rate": 3.5, "is_default": False}
    asyncio.run(exp.update_odometer_status(r1, "approved", org.id))
    assert float(rate_of(r1)) == 3.5                              # stamped on approval
    asyncio.run(exp.save_allowance_rate(org.id, 5))
    assert float(rate_of(r1)) == 3.5                              # an approved reading keeps its stamp
    assert float(rate_of(r2)) == 5.0                              # a pending one follows the new rate
    asyncio.run(exp.update_odometer_status(r2, "approved", org.id))
    asyncio.run(exp.update_odometer_status(r1, "approved", org.id))      # re-approval does not restamp
    assert float(rate_of(r1)) == 3.5 and float(rate_of(r2)) == 5.0
    assert _engine(u)["earnings"]["odometer"] == 135.0            # 10 km x 3.5 + 20 km x 5
    org.cur.execute("SELECT count(*) FROM org_allowance_rate WHERE org_id=%s", (org.id,))
    assert org.cur.fetchone()[0] == 1
