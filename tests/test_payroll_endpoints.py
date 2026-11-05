"""The payroll endpoints with a routed fake cursor (no database): the shared salary engine is fed the attendance rows,
the responses keep the contract shapes of `e2e/mock-server/routes/payroll.js` / `reports.js` (additive fields only),
and the batch uses a constant number of statements. Run: python -m pytest tests/test_payroll_endpoints.py -q
"""
import asyncio
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from payroll_fakes import RoutedCursor, patch_db
from payroll_helpers import NOW, WORK_DAYS, at

from core_functions import gen_dashboard_functions as gdash
from core_functions import gen_plans_functions as gp
from core_functions import overtime as ot
from core_functions import payroll_engine as eng
from core_functions import salary_engine as se
from core_functions import salary_payment_functions as spf
from core_functions import salary_structure as ss

ORG = "org-1"
Y, M = 2026, 10


async def _ok(*a, **k):
    return None


def run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------------------- a fake organisation
def user(uid, name, branch="HQ", dept="Sales", shift=(time(9, 0), time(18, 0)), role="Employee"):
    return {"user_id": uid, "name": name, "emp_id": "E-" + uid, "role_name": role, "branch_name": branch,
            "dept_name": dept, "start_time": shift[0] if shift else None, "end_time": shift[1] if shift else None}


def att_row(uid, day, in_hm="09:00", hours=9.0, auto_closed=False, open_=False):
    pin = at(day, in_hm)
    return {"user_id": uid, "attendance_date": date(Y, M, day), "punch_in": pin,
            "punch_out": None if open_ else pin + timedelta(hours=hours),
            "work_hours": None if open_ else Decimal(str(hours)), "auto_closed": auto_closed}


PAYSLIP_ROW = {"pay_slip_id": "ps-1", "pay_slip_name": "Grade A", "basic_pay": Decimal("30000.00"),
               "hra": Decimal("10000.00"), "epf": Decimal("1800.00"), "professional_tax": Decimal("200.00"),
               "health_insurance": Decimal("500.00")}


def org_routes(users, attendance=(), payslips=None, wages=(), incentives=(), details=None, rule=None,
               holidays=(), leaves=(), odometer=(), org_rate=None, expenses=(), advances=(), paid=(),
               custom_earn=(), custom_ded=(), off_days=None):
    """Routes (needle -> rows) of every statement of `salary_engine.compute_with_cursor`."""
    details = details or {}
    return [
        ("FROM users u LEFT JOIN role_info",
         lambda params: [u for u in users if len(params) < 2 or u["user_id"] in params[1]]),
        ("FROM attendance a WHERE", lambda params: [r for r in attendance if r["user_id"] in params[0]]),
        ("FROM attendance_rule", [rule] if rule else []),
        ("FROM leave_dates ld", list(leaves)),
        ("FROM org_holidays h", list(holidays)),
        ("FROM org_off_days", [{"off_days": off_days}] if off_days is not None else []),
        ("SELECT DISTINCT ON (pr.user_id)", [dict(v, user_id=k) for k, v in (payslips or {}).items()]),
        ("FROM payslip_add_earnings", list(custom_earn)),
        ("FROM payslip_add_deduction", list(custom_ded)),
        ("FROM assigned_wages aw", list(wages)),
        ("FROM assigned_incentives ai", list(incentives)),
        ("FROM incentive_count_details", details.get("COUNT", [])),
        ("FROM incentive_turnover_details", details.get("TURNOVER", [])),
        ("FROM incentive_bonus_details", details.get("BONUS", [])),
        ("FROM incentive_overtime_details", details.get("OVERTIME", [])),
        ("FROM odometer_readings", list(odometer)),
        ("FROM org_allowance_rate", [{"rate": org_rate}] if org_rate is not None else []),
        ("FROM travel_bill_expenses", list(expenses)),
        ("FROM advance_details", list(advances)),
        ("FROM salary_payments", list(paid)),
    ]


@pytest.fixture(autouse=True)
def _clock_and_gates(monkeypatch):
    monkeypatch.setattr(se, "get_utc_now", lambda: NOW)
    monkeypatch.setattr(gp, "check_plan_feature", _ok)


def engine_cursor(monkeypatch, routes):
    cur = RoutedCursor(routes)
    conn = patch_db(monkeypatch, se, cur)
    return cur, conn


# ----------------------------------------------------------------------------- the engine is fed the attendance rows
def test_engine_input_carries_sessions_shift_rule_holidays_leaves_and_off_days(monkeypatch):
    seen = {}
    real = eng.compute_salary

    def spy(inp):
        seen[inp["user_id"]] = inp
        return real(inp)
    monkeypatch.setattr(se.engine, "compute_salary", spy)
    users = [user("u1", "Asha"), user("u2", "Ben", branch="Branch 2", dept="Ops", shift=None)]
    rows = [att_row("u1", 5, "14:00", 4), att_row("u1", 5, "09:00", 4), att_row("u1", 6), att_row("u2", 5)]
    holidays = [
        {"holiday_date": date(Y, M, 20), "holiday_name": "Everyone", "branch_name": None, "dept_name": None},
        {"holiday_date": date(Y, M, 21), "holiday_name": "HQ only", "branch_name": "HQ", "dept_name": None},
        {"holiday_date": date(Y, M, 22), "holiday_name": "Ops only", "branch_name": None, "dept_name": "Ops"},
        {"holiday_date": date(Y, M, 23), "holiday_name": "Elsewhere", "branch_name": "Branch 9", "dept_name": None},
    ]
    cur, _ = engine_cursor(monkeypatch, org_routes(
        users, rows, rule={"late_punch_in_count": 3, "consider_as": "Leave", "grace_minutes": 10},
        holidays=holidays, leaves=[{"user_id": "u1", "leave_date": date(Y, M, 7)}], off_days=[5, 6]))
    out = se.compute_org_salaries(ORG, Y, M)
    assert [r["user_id"] for r in out] == ["u1", "u2"] and not any(r["error"] for r in out)

    a = seen["u1"]
    day5 = a["sessions_by_date"][date(Y, M, 5)]
    assert [s["punch_in"].hour for s in day5] == [9, 14]                       # both sessions, ordered by punch-in
    assert [s["work_hours"] for s in day5] == [4.0, 4.0]
    assert set(a["sessions_by_date"]) == {date(Y, M, 5), date(Y, M, 6)}
    assert a["shift"] == {"start_time": time(9, 0), "end_time": time(18, 0)}
    assert a["grace_minutes"] == 10
    assert a["rule"] == {"late_punch_in_count": 3, "consider_as": "Leave"}
    assert a["leave_dates"] == [date(Y, M, 7)]
    assert a["off_weekdays"] == [5, 6]
    assert sorted(a["holidays"]) == [date(Y, M, 20), date(Y, M, 21)]            # org-wide + its branch, not Ops / Branch 9
    b = seen["u2"]
    assert b["shift"] is None and b["leave_dates"] == []
    assert sorted(b["holidays"]) == [date(Y, M, 20), date(Y, M, 22)]            # org-wide + its department
    # the two-session day is ONE present day with both sessions' hours
    d = out[0]["data"]
    assert d["present_days"] == 2 and d["total_hours"] == 17.0


def test_default_off_day_is_sunday_and_no_rule_means_no_fine(monkeypatch):
    seen = {}
    real = eng.compute_salary
    monkeypatch.setattr(se.engine, "compute_salary", lambda inp: seen.setdefault("inp", inp) and real(inp))
    engine_cursor(monkeypatch, org_routes([user("u1", "Asha")], [att_row("u1", 5, "09:45")],
                                          payslips={"u1": PAYSLIP_ROW}))
    out = se.compute_org_salaries(ORG, Y, M)
    assert seen["inp"]["off_weekdays"] == [6] and seen["inp"]["rule"] is None and seen["inp"]["grace_minutes"] == 0
    assert out[0]["data"]["late_days"] == 1 and out[0]["data"]["deductions"]["fine"] == 0.0


def test_every_statement_is_organisation_or_user_scoped(monkeypatch):
    cur, _ = engine_cursor(monkeypatch, org_routes([user("u1", "Asha")], [att_row("u1", 5)],
                                                   incentives=[{"user_id": "u1", "incentive_id": "i1", "type": "COUNT"}],
                                                   details={"COUNT": [{"incentive_id": "i1", "user_id": None,
                                                                       "each_incentive": 100}]}))
    se.compute_org_salaries(ORG, Y, M)
    stmts = dict(cur.non_savepoint())
    first, nxt = date(Y, M, 1), date(Y, M + 1, 1)

    def params_of(needle):
        return [p for s, p in cur.non_savepoint() if needle in s][0]
    assert params_of("FROM users u LEFT JOIN role_info") == (ORG,)
    assert "u.role_id IN (2, 3) AND u.status = 1" in [s for s, _ in cur.non_savepoint()][0]
    assert "b.org_id = u.parent_id" in cur.sql_text() and "d.org_id = u.parent_id" in cur.sql_text()
    assert "s.org_id = u.parent_id" in cur.sql_text()                            # shift ids are per company
    assert params_of("FROM attendance a WHERE") == (["u1"], first, nxt)
    assert params_of("FROM assigned_incentives ai") == (ORG, ["u1"])
    assert params_of("FROM odometer_readings") == (ORG, ["u1"], first, nxt)
    assert params_of("FROM travel_bill_expenses") == (ORG, ["u1"], first, nxt)
    assert params_of("FROM leave_dates ld") == (ORG, ["u1"], first, nxt)
    assert params_of("FROM org_holidays h") == (ORG, first, nxt)
    assert params_of("FROM salary_payments")[1:] == (ORG, Y, M, ["u1"])
    assert "reading_date >= %s AND reading_date < %s" in cur.sql_text()


@pytest.mark.parametrize("n", [1, 5, 40])
def test_statement_count_does_not_depend_on_the_number_of_staff(monkeypatch, n):
    users = [user(f"u{i}", f"S{i:02d}") for i in range(n)]
    rows = [att_row(u["user_id"], d) for u in users for d in WORK_DAYS[:3]]
    incentives = [{"user_id": u["user_id"], "incentive_id": "i1", "type": "COUNT"} for u in users]
    cur, _ = engine_cursor(monkeypatch, org_routes(
        users, rows, payslips={u["user_id"]: PAYSLIP_ROW for u in users}, incentives=incentives,
        details={"COUNT": [{"incentive_id": "i1", "user_id": None, "each_incentive": 100}]},
        wages=[{"user_id": u["user_id"], "temp_type": "D", "temp_amount": Decimal("400")} for u in users]))
    out = se.compute_org_salaries(ORG, Y, M)
    assert len(out) == n and all(r["data"]["earnings"]["incentive_count"] == 100.0 for r in out)
    # one statement per component, whatever the staff count (the extra 8 are SAVEPOINT guards of optional lookups)
    assert len(cur.non_savepoint()) == 17
    assert len(cur.executed) == 25


def test_no_staff_means_one_statement_and_no_rows(monkeypatch):
    cur, _ = engine_cursor(monkeypatch, org_routes([]))
    assert se.compute_org_salaries(ORG, Y, M) == []
    assert len(cur.non_savepoint()) == 1


def test_one_broken_employee_is_an_error_row_not_zeros(monkeypatch):
    broken = dict(PAYSLIP_ROW, basic_pay="not-a-number")
    cur, _ = engine_cursor(monkeypatch, org_routes([user("u1", "Asha"), user("u2", "Ben")],
                                                   payslips={"u1": broken, "u2": PAYSLIP_ROW}))
    out = se.compute_org_salaries(ORG, Y, M)
    assert out[0]["error"] is True and out[0]["data"] is None
    assert out[1]["error"] is False and out[1]["data"]["net_salary"] == 37500.0


def test_paid_rows_overlay_the_stored_figures(monkeypatch):
    stored = eng.compute_salary({**__import__("payroll_helpers").engine_input(payslip=dict(PAYSLIP_ROW, basic_pay=20000)),
                                 "user_id": "u1"})
    paid = [{"user_id": "u1", "net_amount": Decimal(str(stored["net_salary"])), "paid_date": date(2026, 10, 31),
             "snapshot": eng.snapshot_of(stored)}]
    engine_cursor(monkeypatch, org_routes([user("u1", "Asha"), user("u2", "Ben")],
                                          payslips={"u1": PAYSLIP_ROW, "u2": PAYSLIP_ROW}, paid=paid))
    out = {r["user_id"]: r["data"] for r in se.compute_org_salaries(ORG, Y, M)}
    assert out["u1"]["paid"] is True and out["u1"]["paid_on"] == "2026-10-31"
    assert out["u1"]["earnings"]["basic"] == 20000.0 and out["u1"]["net_salary"] == stored["net_salary"]   # frozen
    assert out["u2"]["paid"] is False and out["u2"]["earnings"]["basic"] == 30000.0                          # live


def test_missing_snapshot_column_or_tables_fall_back_gracefully(monkeypatch):
    class Failing(RoutedCursor):
        def execute(self, sql, params=None):
            norm = " ".join(sql.split())
            if "snapshot" in norm and "FROM salary_payments" in norm and "NULL::jsonb" not in norm:
                self.executed.append((norm, params))
                raise RuntimeError("column snapshot does not exist")
            if "FROM org_allowance_rate" in norm:
                self.executed.append((norm, params))
                raise RuntimeError("relation org_allowance_rate does not exist")
            return super().execute(sql, params)
    routes = org_routes([user("u1", "Asha")], payslips={"u1": PAYSLIP_ROW},
                        paid=[{"user_id": "u1", "net_amount": Decimal("37500"), "paid_date": date(2026, 10, 31),
                               "snapshot": None}],
                        odometer=[{"user_id": "u1", "distance_km": Decimal("10"), "allowance_rate": None}])
    routes.insert(0, ("FROM odometer_readings WHERE org_id = %s AND allowance_rate", [{"allowance_rate": Decimal("4")}]))
    cur = Failing(routes)
    patch_db(monkeypatch, se, cur)
    out = se.compute_org_salaries(ORG, Y, M)[0]["data"]
    assert out["paid"] is True and out["net_salary"] == 37500.0
    assert out["earnings"]["odometer"] == 40.0                     # legacy rate lookup used when migration 032 is not applied


def test_compute_user_salary_unknown_user_and_failure(monkeypatch):
    cur, _ = engine_cursor(monkeypatch, [("SELECT parent_id FROM users", [])])
    with pytest.raises(HTTPException) as e:
        se.compute_user_salary("ghost", Y, M)
    assert e.value.status_code == 404
    routes = [("SELECT parent_id FROM users", [{"parent_id": ORG}])] + org_routes(
        [user("u1", "Asha")], payslips={"u1": dict(PAYSLIP_ROW, basic_pay="bad")})
    engine_cursor(monkeypatch, routes)
    with pytest.raises(HTTPException) as e:
        se.compute_user_salary("u1", Y, M)
    assert e.value.status_code == 500 and e.value.detail == "Failed to fetch salary"


# ----------------------------------------------------------------------------- GET /api/salary/staff-breakdown
@pytest.fixture
def client(monkeypatch):
    from router import report, salary, salary_payments
    monkeypatch.setattr(salary, "check_plan_feature", _ok)
    monkeypatch.setattr(salary, "get_employee_profile_url", lambda uid: f"https://img/{uid}")
    app = FastAPI()
    for r in (salary.router, salary_payments.router, report.router):
        app.include_router(r)
    return TestClient(app)


BREAKDOWN_KEYS = {"user_id", "name", "emp_id", "role_name", "branch_name", "salary_type", "basic_salary", "incentive",
                  "incentive_count", "incentive_turnover", "overtime", "bonus", "fine", "travel_allowance", "odometer",
                  "travel_ticket", "advance", "other_bills", "daily_wages", "hourly_calc", "net_salary",
                  "profile_photo_url"}


def _org(monkeypatch, **kw):
    users = [user("u1", "Asha"), user("u2", "Ben"), user("u3", "Cara")]
    rows = [att_row("u1", d) for d in WORK_DAYS[:22]] + [att_row("u2", d) for d in WORK_DAYS[:4]]
    base = dict(attendance=rows, payslips={"u1": PAYSLIP_ROW},
                wages=[{"user_id": "u2", "temp_type": "D", "temp_amount": Decimal("500")}])
    base.update(kw)
    return engine_cursor(monkeypatch, org_routes(users, **base))


def test_breakdown_keeps_the_contract_and_adds_fields(monkeypatch, client):
    _org(monkeypatch)
    r = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y})
    assert r.status_code == 200
    rows = r.json()
    assert [x["user_id"] for x in rows] == ["u1", "u2", "u3"]
    assert BREAKDOWN_KEYS <= set(rows[0])                                       # every key the app reads
    a, b, c = rows
    assert (a["name"], a["emp_id"], a["role_name"], a["branch_name"]) == ("Asha", "E-u1", "Employee", "HQ")
    assert a["salary_type"] == "MONTHLY_FIXED" and a["basic_salary"] == 30000.0 and a["net_salary"] == 37500.0
    assert a["fine"] == 0.0 and a["profile_photo_url"] == "https://img/u1"
    assert b["salary_type"] == "DAILY_WAGES" and b["daily_wages"] == 2000.0 and b["net_salary"] == 2000.0
    assert c["salary_type"] == "MONTHLY_FIXED" and c["net_salary"] == 0.0        # nothing assigned: not an error
    # additive fields
    assert a["present_days"] == 22 and a["working_days"] == 27 and a["paid"] is False and a["paid_on"] is None
    assert a["salary_types"] == ["MONTHLY_FIXED"] and "error" not in a


def test_breakdown_fine_comes_from_the_attendance_rule(monkeypatch, client):
    rows = [att_row("u1", d, "09:30") for d in WORK_DAYS[:7]] + [att_row("u1", d) for d in WORK_DAYS[7:22]]
    _org(monkeypatch, attendance=rows, rule={"late_punch_in_count": 3, "consider_as": "Half Day", "grace_minutes": 0})
    a = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()[0]
    assert a["fine"] == 1111.11 and a["net_salary"] == 36388.89 and a["late_days"] == 7
    _org(monkeypatch, attendance=rows)                                           # no rule saved: no fine
    a = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()[0]
    assert a["fine"] == 0.0 and a["net_salary"] == 37500.0


def test_breakdown_returns_stored_values_for_a_paid_month(monkeypatch, client):
    stored = eng.compute_salary({**__import__("payroll_helpers").engine_input(payslip=dict(PAYSLIP_ROW, basic_pay=20000)),
                                 "user_id": "u1"})
    _org(monkeypatch, paid=[{"user_id": "u1", "net_amount": Decimal(str(stored["net_salary"])),
                             "paid_date": date(2026, 10, 31), "snapshot": eng.snapshot_of(stored)}])
    a = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()[0]
    assert a["paid"] is True and a["paid_on"] == "2026-10-31" and a["paid_date"] == "2026-10-31"
    assert a["basic_salary"] == 20000.0 and a["net_salary"] == stored["net_salary"]


def test_breakdown_error_row_and_empty_org_and_validation(monkeypatch, client):
    _org(monkeypatch, payslips={"u1": dict(PAYSLIP_ROW, basic_pay="bad"), "u2": PAYSLIP_ROW})
    rows = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()
    assert rows[0]["error"] is True and "net_salary" not in rows[0] and rows[0]["name"] == "Asha"
    assert rows[1]["net_salary"] == 39500.0                       # payslip 37500 + daily wage 500 x 4 days
    engine_cursor(monkeypatch, org_routes([]))
    assert client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json() == []
    for bad in ({"month": 13, "year": Y}, {"month": 0, "year": Y}, {"month": M, "year": 1999}, {"month": "x", "year": Y}):
        assert client.get("/api/salary/staff-breakdown", params={"org_id": ORG, **bad}).status_code == 422


# ----------------------------------------------------------------------------- /salary/monthly-list and /salary/mark-paid
def test_monthly_list_contract_shape(monkeypatch, client):
    _org(monkeypatch)
    r = client.get("/salary/monthly-list", params={"org_id": ORG, "month": M, "year": Y})
    assert r.status_code == 200 and r.json()["success"] is True
    first = r.json()["data"][0]
    assert first == {"user_id": "u1", "name": "Asha", "emp_id": "E-u1", "dept_name": "Sales", "branch_name": "HQ",
                     "salary_type": "MONTHLY_FIXED", "basic_salary": 30000.0, "net_salary": 37500.0,
                     "paid": False, "paid_date": None}
    assert [x["salary_type"] for x in r.json()["data"]] == ["MONTHLY_FIXED", "DAILY_WAGES", "MONTHLY_FIXED"]


def test_monthly_list_equals_breakdown_row_by_row(monkeypatch, client):
    _org(monkeypatch)
    lst = client.get("/salary/monthly-list", params={"org_id": ORG, "month": M, "year": Y}).json()["data"]
    _org(monkeypatch)
    brk = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()
    cur, _ = _org(monkeypatch)
    cur.routes.insert(0, ("SELECT parent_id FROM users", [{"parent_id": ORG}]))
    for l, b in zip(lst, brk):
        assert (l["user_id"], l["net_salary"], l["basic_salary"], l["salary_type"]) == (
            b["user_id"], b["net_salary"], b["basic_salary"], b["salary_type"])
        single = client.get(f"/api/reports/staff-salary/{l['user_id']}", params={"year": Y, "month": M})
        assert single.status_code == 200 and single.json()["data"]["net_salary"] == l["net_salary"]


def test_monthly_list_marks_paid_rows(monkeypatch, client):
    _org(monkeypatch, paid=[{"user_id": "u1", "net_amount": Decimal("37500.00"), "paid_date": date(2026, 10, 31),
                             "snapshot": None}])
    data = client.get("/salary/monthly-list", params={"org_id": ORG, "month": M, "year": Y}).json()["data"]
    assert data[0]["paid"] is True and data[0]["paid_date"] == "2026-10-31" and data[0]["net_salary"] == 37500.0
    assert data[1]["paid"] is False and data[1]["paid_date"] is None


def _mark_paid_cursor(monkeypatch, valid, **kw):
    routes = [("SELECT user_id FROM users WHERE parent_id", [{"user_id": u} for u in valid]),
              ("INSERT INTO salary_payments", [])] + org_routes(
        [user("u1", "Asha"), user("u2", "Ben")], payslips={"u1": PAYSLIP_ROW}, **kw)
    cur = RoutedCursor(routes)
    patch_db(monkeypatch, se, cur)
    conn = patch_db(monkeypatch, spf, cur)
    return cur, conn


def test_mark_paid_stores_net_and_snapshot_and_is_idempotent(monkeypatch, client):
    cur, conn = _mark_paid_cursor(monkeypatch, ["u1", "u2"])
    cur.rowcounts["INSERT INTO salary_payments"] = 2
    r = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1", "u2", "u1"], "month": M, "year": Y})
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "Salary marked as paid for 2 employee(s)", "marked": 2,
                        "already_paid": 0}
    sql, params = cur.find("INSERT INTO salary_payments")[0]
    assert "ON CONFLICT (org_id, user_id, pay_month, pay_year) DO NOTHING" in sql        # a stored payment is never overwritten
    org, month, year, paid_by, ids, nets, snaps = params
    assert (org, month, year, paid_by, ids) == (ORG, M, Y, None, ["u1", "u2"])
    assert nets == ["37500.00", "0.00"]
    snap = json.loads(snaps[0])
    assert snap["net_salary"] == 37500.0 and snap["earnings"]["basic"] == 30000.0 and "paid" not in snap
    assert conn.commits == 1
    cur.rowcounts["INSERT INTO salary_payments"] = 0
    again = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1", "u2"], "month": M, "year": Y}).json()
    assert again["marked"] == 0 and again["already_paid"] == 2


def test_mark_paid_does_not_recompute_an_already_paid_row(monkeypatch, client):
    stored = eng.compute_salary({**__import__("payroll_helpers").engine_input(payslip=dict(PAYSLIP_ROW, basic_pay=20000)),
                                 "user_id": "u1"})
    cur, _ = _mark_paid_cursor(monkeypatch, ["u1"], paid=[{
        "user_id": "u1", "net_amount": Decimal(str(stored["net_salary"])), "paid_date": date(2026, 10, 31),
        "snapshot": eng.snapshot_of(stored)}])
    cur.rowcounts["INSERT INTO salary_payments"] = 0
    r = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1"], "month": M, "year": Y})
    assert r.json()["marked"] == 0 and r.json()["already_paid"] == 1
    params = cur.find("INSERT INTO salary_payments")[0][1]
    assert params[5] == [str(Decimal(str(stored["net_salary"])).quantize(Decimal("0.01")))]     # the stored snapshot, not live
    assert json.loads(params[6][0])["earnings"]["basic"] == 20000.0


def test_mark_paid_failures_write_nothing(monkeypatch, client):
    cur, conn = _mark_paid_cursor(monkeypatch, ["u1"])
    r = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1", "u9"], "month": M, "year": Y})
    assert r.status_code == 404 and "u9" in r.json()["detail"] and cur.find("INSERT INTO salary_payments") == []
    cur, conn = _mark_paid_cursor(monkeypatch, ["u1"], )
    cur.routes.insert(0, ("SELECT DISTINCT ON (pr.user_id)", [dict(PAYSLIP_ROW, user_id="u1", basic_pay="bad")]))
    r = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1"], "month": M, "year": Y})
    assert r.status_code == 500 and "bad" not in r.text and cur.find("INSERT INTO salary_payments") == []
    r = client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": [], "month": M, "year": Y})
    assert r.status_code == 400 and r.json()["detail"] == "Select at least one employee"
    assert client.post("/salary/mark-paid", json={"org_id": ORG, "user_ids": ["u1"], "month": 13, "year": Y}).status_code == 422


# ----------------------------------------------------------------------------- GET /api/reports/staff-salary/{id}
def test_staff_salary_endpoint_shape_and_errors(monkeypatch, client):
    routes = [("SELECT parent_id FROM users", [{"parent_id": ORG}])] + org_routes(
        [user("u1", "Asha")], [att_row("u1", d) for d in WORK_DAYS[:22]], payslips={"u1": PAYSLIP_ROW})
    engine_cursor(monkeypatch, routes)
    r = client.get("/api/reports/staff-salary/u1", params={"year": Y, "month": M})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True and set(body) == {"success", "data"}
    d = body["data"]
    assert {"user_id", "month", "year", "present_days", "total_hours", "earnings", "deductions", "total_earnings",
            "total_deductions", "net_salary"} <= set(d)
    assert (d["user_id"], d["month"], d["year"], d["present_days"], d["net_salary"]) == ("u1", M, Y, 22, 37500.0)
    assert d["paid"] is False
    assert client.get("/api/reports/staff-salary/u1", params={"year": Y, "month": 13}).status_code == 422
    engine_cursor(monkeypatch, [("SELECT parent_id FROM users", [])])
    assert client.get("/api/reports/staff-salary/ghost", params={"year": Y, "month": M}).status_code == 404


# ----------------------------------------------------------------------------- overtime report = breakdown overtime
def test_overtime_report_matches_the_breakdown_overtime(monkeypatch, client):
    from router import incentive
    app = FastAPI()
    app.include_router(incentive.router)
    ot_client = TestClient(app)
    users = [user("u1", "Asha"), user("u2", "Ben", shift=None), user("u3", "Cara", shift=(time(21, 0), time(6, 0)))]
    rows = ([att_row("u1", d, hours=10.5) for d in WORK_DAYS[:5]] + [att_row("u1", d) for d in WORK_DAYS[5:22]]
            + [att_row("u1", 1, "15:00", 1.0)]                                      # a second session on the 1st
            + [att_row("u2", 1, hours=2.5)]
            + [att_row("u3", d, "21:00", 10.5) for d in WORK_DAYS[:4]] + [att_row("u3", d, "21:00") for d in WORK_DAYS[4:20]])
    incentives = [{"user_id": u, "incentive_id": "ot1", "type": "OVERTIME"} for u in ("u1", "u2", "u3")]
    details = {"OVERTIME": [{"incentive_id": "ot1", "user_id": None, "duration_minutes": 30, "amount": Decimal("50")}]}
    # overtime report statements
    ot_cur = RoutedCursor([
        ("FROM assigned_incentives ai JOIN incentive_templates", [
            {"user_id": u["user_id"], "duration_minutes": 30, "amount": Decimal("50"), "name": u["name"],
             "start_time": u["start_time"], "end_time": u["end_time"]} for u in users]),
        ("FROM attendance a WHERE", rows)])
    patch_db(monkeypatch, ot, ot_cur)
    monkeypatch.setattr(ot, "get_utc_now", lambda: NOW)
    r = ot_client.get(f"/api/incentive/overtime-report/{ORG}", params={"month": M, "year": Y})
    assert r.status_code == 200 and r.json()["success"] is True
    report = {x["user_id"]: x for x in r.json()["data"]}
    assert all(set(x) == {"user_id", "ot_days", "ot_minutes", "ot_amount"} for x in report.values())
    # the report reads the attendance rows of exactly these users and this IST month
    assert ot_cur.find("FROM attendance a")[0][1][0] == ["u1", "u2", "u3"]
    # salary breakdown of the same data
    engine_cursor(monkeypatch, org_routes(users, rows, incentives=incentives, details=details))
    brk = {x["user_id"]: x for x in client.get("/api/salary/staff-breakdown",
                                               params={"org_id": ORG, "month": M, "year": Y}).json()}
    for uid in ("u1", "u2", "u3"):
        assert report[uid]["ot_amount"] == brk[uid]["overtime"], uid
    # u1: 5 x 630 + 17 x 540 + 60 (second session on the 1st) = 12390 min, ONE shift per date (22 x 540 = 11880) -> 510 -> 17 slots
    assert report["u1"]["ot_minutes"] == 510 and report["u1"]["ot_amount"] == 850.0
    assert report["u2"]["ot_minutes"] == 150                                         # no shift: all worked minutes
    # u3 (night shift 540 min wrapped): 4 x 630 + 16 x 540 = 11160 - 20 x 540 = 360 -> 12 slots
    assert report["u3"]["ot_minutes"] == 360 and report["u3"]["ot_amount"] == 600.0


# ----------------------------------------------------------------------------- GET /api/salary/preview (month param, fine, paid)
def _preview_cursor(monkeypatch, staff=True, payslip=True, paid=None, rule=None, rows=()):
    routes = [
        ("SELECT org_name FROM org_info", [{"org_name": "Acme"}]),
        ("FROM users u LEFT JOIN branch_info b", [{"name": "Asha", "emp_id": "E-u1", "branch_name": "HQ",
                                                    "department_name": "Sales", "acc_no": "123", "ifsc_code": "IFSC"}]
         if staff else []),
        ("SELECT pay_slip_name, basic_pay", [dict(PAYSLIP_ROW)] if payslip else []),
        ("FROM payslip_add_earnings WHERE pay_slip_id = %s", [{"name": "Conv", "amount": Decimal("1500")}]),
        ("FROM payslip_add_deduction WHERE pay_slip_id = %s", []),
        ("SELECT net_amount, (paid_at AT TIME ZONE", [paid] if paid else []),
        # the shared attendance figures
        ("FROM users u LEFT JOIN role_info", [user("u1", "Asha")]),
        ("FROM attendance a WHERE", list(rows)),
        ("FROM attendance_rule", [rule] if rule else []),
    ]
    cur = RoutedCursor(routes)
    patch_db(monkeypatch, ss, cur)
    return cur


def test_preview_honours_the_month_parameter(monkeypatch, client):
    cur = _preview_cursor(monkeypatch)
    r = client.get(f"/api/salary/preview/{ORG}/u1/ps-1", params={"month": 2, "year": 2026})
    assert r.status_code == 200
    d = r.json()["data"]
    assert (d["month"], d["month_number"], d["year"]) == ("February", 2, 2026)
    first, nxt = date(2026, 2, 1), date(2026, 3, 1)
    assert cur.find("FROM attendance a WHERE")[0][1] == (["u1"], first, nxt)           # the requested month, not "now"
    # contract keys of the mock preview
    assert {"org_name", "payslip_name", "month", "year", "staff_name", "emp_id", "department", "branch",
            "account_number", "ifsc_code", "earnings", "total_earnings", "deductions", "total_deductions",
            "net_pay"} <= set(d)
    assert d["total_earnings"] == 41500.0 and d["total_deductions"] == 2500.0 and d["net_pay"] == 39000.0
    assert d["earnings"][0] == {"name": "Basic Pay", "amount": 30000.0}
    assert d["paid"] is False and d["paid_on"] is None and d["fine"] == 0.0


def test_preview_defaults_to_the_current_ist_month(monkeypatch, client):
    monkeypatch.setattr(ss.rules, "ist_today", lambda now=None: date(2026, 3, 5))
    _preview_cursor(monkeypatch)
    d = client.get(f"/api/salary/preview/{ORG}/u1/ps-1").json()["data"]
    assert (d["month"], d["month_number"], d["year"]) == ("March", 3, 2026)


@pytest.mark.parametrize("params", [{"month": 13}, {"month": 0}, {"month": "x"}, {"month": 2, "year": 1999}])
def test_preview_rejects_bad_month_or_year(monkeypatch, client, params):
    _preview_cursor(monkeypatch)
    assert client.get(f"/api/salary/preview/{ORG}/u1/ps-1", params=params).status_code == 422


def test_preview_fine_matches_the_salary_engine(monkeypatch, client):
    rows = [att_row("u1", d, "09:30") for d in WORK_DAYS[:7]] + [att_row("u1", d) for d in WORK_DAYS[7:22]]
    rule = {"late_punch_in_count": 3, "consider_as": "Half Day", "grace_minutes": 0}
    _preview_cursor(monkeypatch, rule=rule, rows=rows)
    d = client.get(f"/api/salary/preview/{ORG}/u1/ps-1", params={"month": M, "year": Y}).json()["data"]
    assert d["fine"] == 1111.11 and d["late_days"] == 7
    assert d["net_pay"] == 39000.0 and d["net_pay_after_fine"] == 37888.89          # template net stays template-based
    _org(monkeypatch, attendance=rows, rule=rule, payslips={"u1": PAYSLIP_ROW})
    brk = client.get("/api/salary/staff-breakdown", params={"org_id": ORG, "month": M, "year": Y}).json()[0]
    assert brk["fine"] == d["fine"]


def test_preview_shows_the_paid_state(monkeypatch, client):
    _preview_cursor(monkeypatch, paid={"net_amount": Decimal("37500.00"), "paid_date": date(2026, 10, 31)})
    d = client.get(f"/api/salary/preview/{ORG}/u1/ps-1", params={"month": M, "year": Y}).json()["data"]
    assert (d["paid"], d["paid_on"], d["paid_date"], d["paid_net"]) == (True, "2026-10-31", "2026-10-31", 37500.0)


def test_preview_is_organisation_scoped(monkeypatch, client):
    cur = _preview_cursor(monkeypatch, staff=False)
    r = client.get(f"/api/salary/preview/{ORG}/u-of-another-org/ps-1")
    assert r.status_code == 404 and r.json()["detail"] == "Staff not found"
    sql, params = cur.find("FROM users u LEFT JOIN branch_info b")[0]
    assert "u.parent_id = %s" in sql and params == (ORG, ORG, "u-of-another-org", ORG)
    cur = _preview_cursor(monkeypatch, payslip=False)
    r = client.get(f"/api/salary/preview/{ORG}/u1/ps-of-another-org")
    assert r.status_code == 404
    assert "org_id = %s" in cur.find("SELECT pay_slip_name, basic_pay")[0][0]


# ----------------------------------------------------------------------------- attendance PDF uses the same fine / counters
def test_attendance_pdf_shows_the_late_fine_and_day_types(monkeypatch):
    pytest.importorskip("pypdf")
    from io import BytesIO
    from pypdf import PdfReader
    import ml.s3 as s3mod
    from core_functions import report_functions as rf
    monkeypatch.setattr(s3mod, "get_org_logo_url", lambda org: None, raising=False)
    rows = [att_row("u1", d, "09:30") for d in WORK_DAYS[:7]] + [att_row("u1", d) for d in WORK_DAYS[7:22]]
    pdf_att = [{"attendance_date": date(Y, M, d), "punch_ins": [at(d, "09:30" if i < 7 else "09:00")],
                "punch_outs": [at(d, "18:30" if i < 7 else "18:00")], "work_hours": Decimal("9.00")}
               for i, d in enumerate(WORK_DAYS[:22])]
    routes = [
        ("FROM users u LEFT JOIN dept_info d ON u.dept_id", [{"name": "Asha", "emp_id": "E-u1", "mobile_number": "9",
                                                             "dept_name": "Sales", "parent_id": ORG,
                                                             "start_time": time(9, 0), "end_time": time(18, 0)}]),
        ("FROM org_info o", [{"org_name": "Acme", "org_address": "Addr", "phone": "1", "email": "a@b.c", "website": ""}]),
        ("SELECT ps.pay_slip_name, ps.basic_pay, ps.hra", [dict(PAYSLIP_ROW, pay_slip_id="ps-1")]),
        ("FROM payslip_add_earnings WHERE pay_slip_id = %s", []),
        ("FROM payslip_add_deduction WHERE pay_slip_id = %s", []),
        ("array_agg(punch_in", pdf_att),
        ("FROM users u LEFT JOIN role_info", [user("u1", "Asha")]),
        ("FROM attendance a WHERE", rows),
        ("FROM attendance_rule", [{"late_punch_in_count": 3, "consider_as": "Half Day", "grace_minutes": 0}]),
    ]
    cur = RoutedCursor(routes)
    patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(se, "get_utc_now", lambda: NOW)
    pdf = run(rf.generate_monthly_attendance_pdf("u1", "2026-10"))
    assert pdf[:4] == b"%PDF"
    text = " ".join(" ".join(p.extract_text().split()) for p in PdfReader(BytesIO(pdf)).pages)
    assert "Late Fine" in text and "1,111" in text                                  # fine: 1 day x 30000 / 27
    assert "Net Pay Rs. 36,389" in text.replace("  ", " ") or "36,389" in text      # template net 37500 - fine
    assert "Present Days 22" in text and "Absent Days 5" in text
    # punch times are printed in IST (the database session is UTC)
    assert "09:30" in text


# ----------------------------------------------------------------------------- attendance rule router: honest errors
def test_attendance_rule_router_hides_internal_errors(monkeypatch):
    from router import attendance_rule

    def boom(org_id):
        raise RuntimeError("secret connection string")
    monkeypatch.setattr(attendance_rule, "get_rule", boom)
    app = FastAPI()
    app.include_router(attendance_rule.router)
    r = TestClient(app, raise_server_exceptions=False).get("/api/attendance-rule/org-1")
    assert r.status_code == 500 and "secret" not in r.text


# ----------------------------------------------------------------------------- platform-admin dashboard: IST date parameter
def test_dashboard_has_no_current_date_attendance_filters():
    import re
    from pathlib import Path
    text = Path(gdash.__file__).read_text(encoding="utf-8")
    assert not re.search(r"attendance_date\s*(=|>=)\s*CURRENT_DATE", text)
    assert text.count("ist_today()") >= 7


def test_active_orgs_today_uses_the_ist_date_parameter(monkeypatch):
    monkeypatch.setattr(gdash, "ist_today", lambda: date(2026, 10, 5))
    cur = RoutedCursor([("FROM org_info o", [])])
    patch_db(monkeypatch, gdash, cur)
    run(gdash.get_active_orgs_today())
    sql, params = cur.find("FROM org_info o")[0]
    assert "a.attendance_date = %s" in sql and "CURRENT_DATE" not in sql and params == (date(2026, 10, 5),)


def test_org_employees_and_analytics_use_the_ist_date_parameter(monkeypatch):
    monkeypatch.setattr(gdash, "ist_today", lambda: date(2026, 10, 5))
    cur = RoutedCursor([("SELECT COUNT(*) AS cnt FROM users", [{"cnt": 3}])])
    patch_db(monkeypatch, gdash, cur)
    run(gdash.get_org_employees("org-1"))
    sql, params = cur.find("LEFT JOIN attendance a")[0]
    assert "a.attendance_date = %s" in sql and params == ("org-1", "org-1", date(2026, 10, 5), "org-1")
    cur = RoutedCursor([("SELECT COUNT(*) AS cnt FROM users", [{"cnt": 3}])])
    patch_db(monkeypatch, gdash, cur)
    try:
        run(gdash.get_org_analytics("org-1", 7))
    except Exception:
        pass
    ranges = [p for s, p in cur.executed if "attendance_date >= %s" in s]
    assert ranges and ranges[0] == ("org-1", date(2026, 9, 28)) and ranges[1] == ("org-1", date(2026, 9, 5))
