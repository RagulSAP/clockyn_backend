"""Payroll input validation (decision D-15) without a database: bounds, two decimals, percentage ranges, uniqueness per
organisation and type, with the messages of the app / mock server (`e2e/mock-server/routes/payroll.js`).

Run: python -m pytest tests/test_payroll_validation.py -q
"""
import asyncio
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from payroll_fakes import RoutedCursor, forbid_db, patch_db

from core_functions import advance_functions as adv
from core_functions import daily_hourly_functions as dh
from core_functions import emp_expenses_functions as exp
from core_functions import gen_plans_functions as gp
from core_functions import incentive_functions as inc
from core_functions import payroll_validation as pv
from core_functions import salary_structure as ss


def run(coro):
    return asyncio.run(coro)


def status_and_detail(fn, *a, **kw):
    with pytest.raises(HTTPException) as e:
        fn(*a, **kw)
    return e.value.status_code, e.value.detail


# ----------------------------------------------------------------------------- parse_amount
@pytest.mark.parametrize("value, expected", [
    (0.01, "0.01"), (1, "1.00"), ("12.5", "12.50"), (100, "100.00"), (9999999.99, "9999999.99"),
    ("9999999.99", "9999999.99"), (Decimal("5.10"), "5.10"), (" 7 ", "7.00"), ("1e3", "1000.00"),
])
def test_parse_amount_accepts(value, expected):
    assert pv.parse_amount(value) == Decimal(expected)


@pytest.mark.parametrize("value, detail", [
    (0, "Amount must be greater than 0"), (-1, "Amount must be greater than 0"), (-0.01, "Amount must be greater than 0"),
    (None, "Amount must be greater than 0"), ("", "Amount must be greater than 0"), ("abc", "Amount must be greater than 0"),
    (True, "Amount must be greater than 0"), ([1], "Amount must be greater than 0"),
    (float("nan"), "Amount must be greater than 0"), (float("inf"), "Amount must be greater than 0"),
    (10000000, "Amount must not exceed 9999999.99"), (9999999.995, "Amount must not exceed 9999999.99"),
    (9999999.981, "Amount can have at most 2 decimal places"),
    (10000000.00, "Amount must not exceed 9999999.99"),
    (1.005, "Amount can have at most 2 decimal places"), ("0.001", "Amount can have at most 2 decimal places"),
])
def test_parse_amount_rejects(value, detail):
    assert status_and_detail(pv.parse_amount, value) == (400, detail)


def test_parse_amount_allow_zero_for_template_fields():
    assert pv.parse_amount(0, "HRA", allow_zero=True) == Decimal("0.00")
    assert status_and_detail(pv.parse_amount, -1, "HRA", True) == (400, "HRA must be a valid amount (0 or more)")
    assert status_and_detail(pv.parse_amount, "x", "HRA", True) == (400, "HRA must be a valid amount (0 or more)")
    assert status_and_detail(pv.parse_amount, 10000000, "HRA", True) == (400, "HRA must not exceed 9999999.99")


def test_parse_amount_label_in_message():
    assert status_and_detail(pv.parse_amount, 0, "Advance amount") == (400, "Advance amount must be greater than 0")


# ----------------------------------------------------------------------------- percentages, ints, names
@pytest.mark.parametrize("value, expected", [(0, "0.00"), (100, "100.00"), (12.5, "12.50"), ("99.99", "99.99"), (0.01, "0.01")])
def test_parse_percent_accepts(value, expected):
    assert pv.parse_percent(value) == Decimal(expected)


@pytest.mark.parametrize("value", [-0.01, 100.01, 101, 1000, "abc", None, True, "", float("nan")])
def test_parse_percent_rejects_with_the_client_message(value):
    assert status_and_detail(pv.parse_percent, value) == (400, "Enter a number between 0-100")


def test_parse_percent_two_decimals():
    assert status_and_detail(pv.parse_percent, 12.345) == (400, "Percentage can have at most 2 decimal places")


@pytest.mark.parametrize("value, expected", [(1, 1), ("7", 7), (7.0, 7), (2147483647, 2147483647)])
def test_parse_positive_int_accepts(value, expected):
    assert pv.parse_positive_int(value) == expected


@pytest.mark.parametrize("value", [0, -1, 1.5, "1.5", "abc", None, True, [], ""])
def test_parse_positive_int_rejects(value):
    assert status_and_detail(pv.parse_positive_int, value) == (400, "Must be a valid number")


def test_parse_positive_int_upper_bounds():
    assert status_and_detail(pv.parse_positive_int, 2147483648) == (400, "Value must not exceed 2147483647")
    assert pv.parse_duration_minutes(1440) == 1440
    assert status_and_detail(pv.parse_duration_minutes, 1441)[0] == 400
    assert status_and_detail(pv.parse_duration_minutes, 0) == (400, "Must be a valid number")


def test_parse_name():
    assert pv.parse_name("  Grade A ", "required") == "Grade A"
    for bad in (None, "", "   ", 5):
        assert status_and_detail(pv.parse_name, bad, "Template name is required") == (400, "Template name is required")
    assert status_and_detail(pv.parse_name, "x" * 256, "r", 255)[0] == 400
    assert pv.parse_name("x" * 255, "r", 255) == "x" * 255


def test_month_and_year():
    assert pv.parse_month_name("october") == "October" and pv.parse_month_name(" December ") == "December"
    for bad in (None, "", "Octob", 10, "13"):
        assert status_and_detail(pv.parse_month_name, bad) == (400, "Month is required")
    assert pv.parse_year(2026) == 2026 and pv.parse_year("2000") == 2000 and pv.parse_year(2100) == 2100
    for bad in (1999, 2101, "x", None, True, 2026.5):
        assert status_and_detail(pv.parse_year, bad)[0] == 400
    assert pv.parse_month_number(1) == 1 and pv.parse_month_number("12") == 12
    for bad in (0, 13, "x", None, True, 1.5):
        assert status_and_detail(pv.parse_month_number, bad) == (422, "month must be between 1 and 12")


# ----------------------------------------------------------------------------- payslip template validation
class Line:
    def __init__(self, name, amount):
        self.name, self.amount = name, amount


class Tpl:
    def __init__(self, **kw):
        base = dict(payslip_name="Grade A", org_id="org-1", basic_pay=30000, hra=10000, epf=1800,
                    professional_tax=200, health_insurance=500, custom_earnings=[], custom_deductions=[])
        base.update(kw)
        self.__dict__.update(base)


def test_template_valid_normalises():
    v = pv.validate_salary_template(Tpl(payslip_name="  Grade A  ", hra=0, custom_earnings=[Line("Bonus", 100.5)],
                                        custom_deductions=[Line(" Canteen ", "300")]))
    assert v["name"] == "Grade A"
    assert v["amounts"] == {"basic_pay": Decimal("30000.00"), "hra": Decimal("0.00"), "epf": Decimal("1800.00"),
                            "professional_tax": Decimal("200.00"), "health_insurance": Decimal("500.00")}
    assert v["custom_earnings"] == [("Bonus", Decimal("100.50"))]
    assert v["custom_deductions"] == [("Canteen", Decimal("300.00"))]


def test_template_drops_unnamed_and_zero_custom_lines_like_the_app():
    v = pv.validate_salary_template(Tpl(custom_earnings=[Line("", 50), Line("  ", 50), Line("Zero", 0),
                                                         Line(None, 5), Line("Kept", 1)]))
    assert v["custom_earnings"] == [("Kept", Decimal("1.00"))]


@pytest.mark.parametrize("field, value, detail", [
    ("basic_pay", -1, "Basic pay must be a valid amount (0 or more)"),
    ("hra", 10000000, "HRA must not exceed 9999999.99"),
    ("epf", 10.123, "EPF can have at most 2 decimal places"),
    ("professional_tax", "x", "Professional tax must be a valid amount (0 or more)"),
    ("health_insurance", None, "Health insurance must be a valid amount (0 or more)"),
    ("payslip_name", "   ", "Template name is required"),
    ("payslip_name", None, "Template name is required"),
])
def test_template_rejects(field, value, detail):
    assert status_and_detail(pv.validate_salary_template, Tpl(**{field: value})) == (400, detail)


def test_template_rejects_bad_custom_line_amounts():
    code, detail = status_and_detail(pv.validate_salary_template, Tpl(custom_earnings=[Line("Bonus", -5)]))
    assert code == 400 and "Bonus" in detail
    code, detail = status_and_detail(pv.validate_salary_template, Tpl(custom_deductions=[Line("Fine", 1.234)]))
    assert code == 400 and "2 decimal" in detail


# ----------------------------------------------------------------------------- salary templates (DB layer, fake cursor)
def _template_routes(taken=False, rowcount_update=1):
    return [("lower(pay_slip_name)", [{"?column?": 1}] if taken else [])], {"UPDATE pay_slip": rowcount_update}


@pytest.fixture
def no_listing(monkeypatch):
    async def fake_list(org_id):
        return {"success": True, "data": [{"pay_slip_id": "x"}]}
    monkeypatch.setattr(ss, "get_salary_templates", fake_list)


def test_create_template_validates_before_touching_the_database(monkeypatch):
    forbid_db(monkeypatch, ss)
    for bad in (Tpl(basic_pay=-5), Tpl(payslip_name=""), Tpl(hra=1.005), Tpl(epf=10 ** 8)):
        with pytest.raises(HTTPException) as e:
            run(ss.create_salary_template(bad))
        assert e.value.status_code == 400


def test_create_template_duplicate_name_is_409_and_nothing_is_written(monkeypatch, no_listing):
    routes, counts = _template_routes(taken=True)
    cur = RoutedCursor(routes, counts)
    conn = patch_db(monkeypatch, ss, cur)
    with pytest.raises(HTTPException) as e:
        run(ss.create_salary_template(Tpl(payslip_name="Grade A")))
    assert (e.value.status_code, e.value.detail) == (409, "A template with this name already exists")
    assert cur.find("INSERT INTO pay_slip") == []
    assert conn.commits == 0 and conn.rollbacks == 1
    assert cur.find("pg_advisory_xact_lock")[0][1] == ("payslip:org-1",)
    dup = cur.find("lower(pay_slip_name)")[0]
    assert "org_id = %s" in dup[0] and dup[1] == ("org-1", "Grade A", None, None)    # per organisation, case-insensitive


def test_create_template_inserts_validated_decimals(monkeypatch, no_listing):
    routes, counts = _template_routes()
    cur = RoutedCursor(routes, counts)
    conn = patch_db(monkeypatch, ss, cur)
    res = run(ss.create_salary_template(Tpl(payslip_name=" Grade A ", custom_earnings=[Line("Conv", 1500), Line("", 9)],
                                            custom_deductions=[Line("Canteen", 0)])))
    assert res["success"] is True and res["updated_templates"] == [{"pay_slip_id": "x"}] and res["pay_slip_id"]
    sql, params = cur.find("INSERT INTO pay_slip ")[0]
    assert params[1:3] == ("Grade A", "org-1")
    assert params[3:] == (Decimal("30000.00"), Decimal("10000.00"), Decimal("1800.00"), Decimal("200.00"), Decimal("500.00"))
    assert len(cur.find("INSERT INTO payslip_add_earnings")) == 1
    assert cur.find("INSERT INTO payslip_add_deduction") == []
    assert conn.commits == 1


def test_update_template_duplicate_excludes_itself_and_is_org_scoped(monkeypatch, no_listing):
    routes, counts = _template_routes()
    cur = RoutedCursor(routes, counts)
    conn = patch_db(monkeypatch, ss, cur)
    run(ss.update_salary_template("ps-1", Tpl()))
    assert cur.find("lower(pay_slip_name)")[0][1] == ("org-1", "Grade A", "ps-1", "ps-1")
    sql, params = cur.find("UPDATE pay_slip")[0]
    assert "pay_slip_id = %s AND org_id = %s" in sql and params[-2:] == ("ps-1", "org-1")
    assert conn.commits == 1


def test_update_template_of_another_org_is_404_and_changes_nothing(monkeypatch, no_listing):
    routes, counts = _template_routes(rowcount_update=0)
    cur = RoutedCursor(routes, counts)
    conn = patch_db(monkeypatch, ss, cur)
    with pytest.raises(HTTPException) as e:
        run(ss.update_salary_template("ps-other", Tpl()))
    assert e.value.status_code == 404
    assert cur.find("DELETE FROM payslip_add_earnings") == [] and conn.commits == 0 and conn.rollbacks == 1


def test_update_template_duplicate_name_is_409(monkeypatch, no_listing):
    routes, counts = _template_routes(taken=True)
    patch_db(monkeypatch, ss, RoutedCursor(routes, counts))
    with pytest.raises(HTTPException) as e:
        run(ss.update_salary_template("ps-1", Tpl()))
    assert e.value.status_code == 409


def test_template_routes_return_real_status_codes(monkeypatch, no_listing):
    from router import salary
    monkeypatch.setattr(salary, "check_plan_feature", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(gp, "check_plan_feature", lambda *a, **k: asyncio.sleep(0))
    app = FastAPI()
    app.include_router(salary.router)
    client = TestClient(app)
    body = dict(payslip_name="Grade A", org_id="org-1", basic_pay=-1, hra=0, epf=0, professional_tax=0,
                health_insurance=0)
    r = client.post("/api/salary/template", json=body)
    assert r.status_code == 400 and r.json() == {"detail": "Basic pay must be a valid amount (0 or more)"}
    routes, counts = _template_routes(taken=True)
    patch_db(monkeypatch, ss, RoutedCursor(routes, counts))
    r = client.post("/api/salary/template", json=dict(body, basic_pay=100))
    assert r.status_code == 409 and r.json() == {"detail": "A template with this name already exists"}


# ----------------------------------------------------------------------------- incentives
def _inc_cursor(dups=None, org="org-1"):
    """dups: {needle-column: template name already holding the value}."""
    routes = []
    for column, name in (dups or {}).items():
        routes.append((column, [{"template_name": name}]))
    routes.append(("SELECT org_id FROM incentive_templates", [{"org_id": org}] if org else []))
    return RoutedCursor(routes)


COUNT_ARGS = ("org-1", "1", 5, 10, 250.0)


def test_incentive_create_validates_before_the_database(monkeypatch):
    forbid_db(monkeypatch, inc)
    bad_calls = [
        (inc.create_count_incentive, ("org-1", "1", 0, 10, 250.0)),
        (inc.create_count_incentive, ("org-1", "1", 5, 1.5, 250.0)),
        (inc.create_count_incentive, ("org-1", "1", 5, 10, 0)),
        (inc.create_count_incentive, ("org-1", "1", 5, 10, 10000000)),
        (inc.create_count_incentive, ("org-1", "", 5, 10, 250.0)),
        (inc.create_turnover_incentive, ("org-1", "1", 0, 5)),
        (inc.create_turnover_incentive, ("org-1", "1", 1000, 0)),
        (inc.create_turnover_incentive, ("org-1", "1", 1000, 100.5)),
        (inc.create_overtime_incentive, ("org-1", "1", 0, 50)),
        (inc.create_overtime_incentive, ("org-1", "1", 1441, 50)),
        (inc.create_overtime_incentive, ("org-1", "1", 30, -5)),
        (inc.create_bonus_incentive, ("org-1", "1", 100.01, "October", 2026)),
        (inc.create_bonus_incentive, ("org-1", "1", -1, "October", 2026)),
        (inc.create_bonus_incentive, ("org-1", "1", 10, "", 2026)),
        (inc.create_bonus_incentive, ("org-1", "1", 10, "October", 0)),
        (inc.create_bonus_incentive, ("org-1", "1", 10, "Smarch", 2026)),
    ]
    for fn, args in bad_calls:
        with pytest.raises(HTTPException) as e:
            fn(*args)
        assert e.value.status_code == 400, (fn.__name__, args)


def test_incentive_boundaries_are_accepted(monkeypatch):
    for fn, args in (
        (inc.create_bonus_incentive, ("org-1", "1", 0, "October", 2026)),            # 0% allowed
        (inc.create_bonus_incentive, ("org-1", "2", 100, "October", 2026)),          # 100% allowed
        (inc.create_turnover_incentive, ("org-1", "1", 9999999.99, 100)),
        (inc.create_overtime_incentive, ("org-1", "1", 1440, 9999999.99)),
        (inc.create_count_incentive, ("org-1", "1", 1, 1, 0.01)),
    ):
        cur = _inc_cursor()
        conn = patch_db(monkeypatch, inc, cur)
        assert fn(*args)["incentive_id"]
        assert conn.commits == 1


@pytest.mark.parametrize("column, args, message", [
    ("d.count_upto_basic_salary = %s", COUNT_ARGS, "This value already exists in Count 3"),
    ("d.next_count = %s", COUNT_ARGS, "This value already exists in Count 3"),
    ("d.each_incentive = %s", COUNT_ARGS, "This value already exists in Count 3"),
])
def test_count_duplicates_are_409_per_field(monkeypatch, column, args, message):
    cur = _inc_cursor({column: "3"})
    conn = patch_db(monkeypatch, inc, cur)
    with pytest.raises(HTTPException) as e:
        inc.create_count_incentive(*args)
    assert (e.value.status_code, e.value.detail) == (409, message)
    assert cur.find("INSERT INTO incentive_templates") == [] and conn.commits == 0 and conn.rollbacks == 1


@pytest.mark.parametrize("fn, args, column, message", [
    (inc.create_turnover_incentive, ("org-1", "2", 50000, 2.5), "d.turnover_upto_basic_salary = %s",
     "This value already exists in Turnover 1"),
    (inc.create_overtime_incentive, ("org-1", "2", 30, 50), "d.duration_minutes = %s",
     "This duration already exists in Over Time 1"),
    (inc.create_bonus_incentive, ("org-1", "2", 10, "October", 2026), "d.bonus_percent = %s",
     "This percentage already exists in Bonus 1"),
])
def test_other_duplicates_are_409(monkeypatch, fn, args, column, message):
    cur = _inc_cursor({column: "1"})
    patch_db(monkeypatch, inc, cur)
    with pytest.raises(HTTPException) as e:
        fn(*args)
    assert (e.value.status_code, e.value.detail) == (409, message)


def test_incentive_uniqueness_is_checked_per_organisation_and_type_under_a_lock(monkeypatch):
    cur = _inc_cursor()
    patch_db(monkeypatch, inc, cur)
    inc.create_count_incentive("org-7", "1", 5, 10, 250.0)
    assert cur.find("pg_advisory_xact_lock")[0][1] == ("incentive:org-7:COUNT",)
    dups = [(s, p) for s, p in cur.executed if "SELECT t.template_name" in s]
    assert len(dups) == 3
    for sql, params in dups:
        assert "t.org_id = %s" in sql and "t.type = 'COUNT'" in sql and "d.user_id IS NULL" in sql
        assert params[0] == "org-7" and params[2:] == (None, None)
    assert [p[1] for _, p in dups] == [5, 10, Decimal("250.00")]
    order = [s.split()[0] for s, _ in cur.executed]
    assert order.index("SELECT") < order.index("INSERT")                      # lock + checks before the insert


def test_incentive_update_excludes_itself_and_scopes_to_the_template_org(monkeypatch):
    cur = _inc_cursor(org="org-9")
    conn = patch_db(monkeypatch, inc, cur)
    cur.rowcounts["UPDATE incentive_count_details"] = 1
    assert inc.update_count_incentive("inc-1", 5, 10, 250.0) is True
    for sql, params in [(s, p) for s, p in cur.executed if "SELECT t.template_name" in s]:
        assert params[0] == "org-9" and params[2:] == ("inc-1", "inc-1")
    assert conn.commits == 1


def test_incentive_update_unknown_template_returns_false_without_writes(monkeypatch):
    cur = _inc_cursor(org=None)
    conn = patch_db(monkeypatch, inc, cur)
    assert inc.update_turnover_incentive("nope", 50000, 2.5) is False
    assert cur.find("UPDATE") == [] and conn.commits == 0


def test_incentive_update_duplicate_and_validation(monkeypatch):
    forbid_db(monkeypatch, inc)
    assert status_and_detail(inc.update_overtime_incentive, "i", 0, 50)[0] == 400
    assert status_and_detail(inc.update_bonus_incentive, "i", 101, "October", 2026)[0] == 400
    assert status_and_detail(inc.update_bonus_incentive, "i", 10, "Smarch", 2026)[0] == 400
    cur = _inc_cursor({"d.duration_minutes = %s": "2"})
    patch_db(monkeypatch, inc, cur)
    assert status_and_detail(inc.update_overtime_incentive, "i", 30, 50) == (409, "This duration already exists in Over Time 2")


def test_bonus_update_keeps_month_and_year_when_omitted(monkeypatch):
    cur = RoutedCursor([("SELECT org_id FROM incentive_templates", [{"org_id": "org-1"}]),
                        ("SELECT month, year FROM incentive_bonus_details", [{"month": "March", "year": 2026}])],
                       {"UPDATE incentive_bonus_details": 1})
    patch_db(monkeypatch, inc, cur)
    assert inc.update_bonus_incentive("b1", 12.5, "", 0) is True
    assert cur.find("UPDATE incentive_bonus_details")[0][1] == (Decimal("12.50"), "March", 2026, "b1")


def test_incentive_routes_answer_400_and_409(monkeypatch):
    from router import incentive
    monkeypatch.setattr(gp, "check_plan_feature", lambda *a, **k: asyncio.sleep(0))
    app = FastAPI()
    app.include_router(incentive.router)
    client = TestClient(app)
    r = client.post("/api/incentive/overtime", json={"org_id": "org-1", "template_name": "1", "duration_minutes": 0,
                                                     "amount": 50})
    assert r.status_code == 400 and r.json() == {"detail": "Must be a valid number"}
    cur = _inc_cursor({"d.bonus_percent = %s": "1"})
    patch_db(monkeypatch, inc, cur)
    r = client.post("/api/incentive/bonus", json={"org_id": "org-1", "template_name": "2", "bonus_percent": 10,
                                                  "month": "October", "year": 2026})
    assert r.status_code == 409 and r.json() == {"detail": "This percentage already exists in Bonus 1"}
    r = client.post("/api/incentive/bonus", json={"org_id": "org-1", "template_name": "2", "bonus_percent": 101,
                                                  "month": "October", "year": 2026})
    assert r.status_code == 400 and r.json() == {"detail": "Enter a number between 0-100"}
    cur = _inc_cursor(org=None)
    patch_db(monkeypatch, inc, cur)
    r = client.put("/api/incentive/count/nope", json={"count_upto": 5, "next_count": 10, "each_incentive": 250})
    assert r.status_code == 404


# ----------------------------------------------------------------------------- advances
def _adv_cursor():
    def role(params):
        return [{"role_id": 1 if params[0] == "adm-1" else 3}]
    return RoutedCursor([
        ("SELECT role_id FROM users", role),
        ("SELECT org_id FROM org_info", [{"org_id": "org-1"}]),
        ("INSERT INTO advance_details", [{"id": 5}]),
        ("SELECT * FROM advance_details", [{"id": 5, "advance_amt": Decimal("1500.50")}]),
    ])


@pytest.mark.parametrize("amount, detail", [
    (0, "Advance amount must be greater than 0"), (-100, "Advance amount must be greater than 0"),
    (10000000, "Advance amount must not exceed 9999999.99"), (10.001, "Advance amount can have at most 2 decimal places"),
    (None, "Advance amount must be greater than 0"), ("abc", "Advance amount must be greater than 0"),
])
def test_advance_amount_validation_happens_before_the_database(monkeypatch, amount, detail):
    from datetime import date
    forbid_db(monkeypatch, adv)
    assert status_and_detail(adv.create_advance, "org-1", "emp-1", "adm-1", amount, date(2026, 10, 1)) == (400, detail)


def test_advance_valid_amount_is_stored_as_a_two_decimal_number(monkeypatch):
    from datetime import date
    cur = _adv_cursor()
    conn = patch_db(monkeypatch, adv, cur)
    row = adv.create_advance("org-1", "emp-1", "adm-1", 1500.5, date(2026, 10, 1))
    assert row["id"] == 5
    assert cur.find("INSERT INTO advance_details")[0][1] == (Decimal("1500.50"), date(2026, 10, 1), "emp-1", "adm-1", "org-1")
    assert conn.commits == 1
    # bounds: smallest and largest accepted amounts
    for amount in (0.01, 9999999.99):
        patch_db(monkeypatch, adv, _adv_cursor())
        adv.create_advance("org-1", "emp-1", "adm-1", amount, date(2026, 10, 1))


def test_advance_route_contract(monkeypatch):
    from router import advance
    app = FastAPI()
    app.include_router(advance.router)
    client = TestClient(app)
    forbid_db(monkeypatch, adv)
    body = {"org_id": "org-1", "user_id": "emp-1", "created_by": "adm-1", "advance_amt": 0, "date": "2026-10-01"}
    r = client.post("/api/advance", json=body)
    assert r.status_code == 400 and r.json() == {"detail": "Advance amount must be greater than 0"}
    patch_db(monkeypatch, adv, _adv_cursor())
    r = client.post("/api/advance", json=dict(body, advance_amt=1500.5))
    assert r.status_code == 200
    assert r.json()["success"] is True and r.json()["message"] == "Advance recorded successfully"
    assert r.json()["data"]["advance_amt"] == 1500.5


# ----------------------------------------------------------------------------- wages (validation already server-side)
@pytest.mark.parametrize("value, detail", [
    (0, "Amount must be greater than 0"), (-5, "Amount must be greater than 0"), ("x", "Amount must be greater than 0"),
    (10000000, "Amount must not exceed 9999999.99"), (1.234, "Amount can have at most 2 decimal places"),
])
def test_wage_amount_rules_match_the_shared_validator(value, detail):
    assert status_and_detail(dh.parse_amount, value) == (400, detail)
    assert status_and_detail(pv.parse_amount, value) == (400, detail)
    assert dh.parse_amount(0.01) == pv.parse_amount(0.01) == Decimal("0.01")
    assert dh.MAX_AMOUNT == pv.MAX_AMOUNT


def test_wage_duplicate_amount_per_org_and_type_is_409(monkeypatch):
    cur = RoutedCursor([("SELECT 1 FROM daily_hourly_wages", [{"?column?": 1}])])
    patch_db(monkeypatch, dh, cur)
    with pytest.raises(HTTPException) as e:
        dh.create_template("org-1", 400, "D")
    assert (e.value.status_code, e.value.detail) == (409, "A template with this amount already exists")
    sql, params = cur.find("SELECT 1 FROM daily_hourly_wages")[0]
    assert "org_id = %s AND temp_type = %s" in sql and params[:3] == ("org-1", "D", Decimal("400.00"))


# ----------------------------------------------------------------------------- allowance rate (P1-12)
@pytest.mark.parametrize("rate", [0, -1, "x", None, True, 10000, 3.456, 10 ** 7])
def test_allowance_rate_rejects_bad_values_before_the_database(monkeypatch, rate):
    forbid_db(monkeypatch, exp)
    with pytest.raises(HTTPException) as e:
        run(exp.save_allowance_rate("org-1", rate))
    assert e.value.status_code == 400


def test_allowance_rate_unknown_org_is_404(monkeypatch):
    cur = RoutedCursor([])
    conn = patch_db(monkeypatch, exp, cur)
    with pytest.raises(HTTPException) as e:
        run(exp.save_allowance_rate("org-x", 3.5))
    assert e.value.status_code == 404 and cur.find("INSERT INTO org_allowance_rate") == [] and conn.commits == 0


def test_save_allowance_rate_upserts_the_org_row_and_stamps_only_pending_readings(monkeypatch):
    cur = RoutedCursor([("SELECT 1 FROM org_info", [{"?column?": 1}])])
    conn = patch_db(monkeypatch, exp, cur)
    res = run(exp.save_allowance_rate("org-1", 3.5))
    assert res == {"success": True, "message": "Allowance rate saved successfully", "rate": 3.5}
    sql, params = cur.find("INSERT INTO org_allowance_rate")[0]
    assert "ON CONFLICT (org_id) DO UPDATE" in sql and params[:2] == ("org-1", Decimal("3.50"))
    sql, params = cur.find("UPDATE odometer_readings")[0]
    assert "status = 'pending'" in sql and "org_id = %s" in sql and params == (Decimal("3.50"), "org-1")
    assert conn.commits == 1
    # never "every reading of the organisation" (approved ones keep their stamp)
    assert "UPDATE odometer_readings SET allowance_rate = %s WHERE org_id = %s AND status = 'pending'" in cur.sql_text()


@pytest.mark.parametrize("value, rate", [(0.01, 0.01), (9999.99, 9999.99), (3, 3.0)])
def test_allowance_rate_bounds_accepted(monkeypatch, value, rate):
    patch_db(monkeypatch, exp, RoutedCursor([("SELECT 1 FROM org_info", [{"x": 1}])]))
    assert run(exp.save_allowance_rate("org-1", value))["rate"] == rate


def test_get_allowance_rate_default_and_stored(monkeypatch):
    monkeypatch.delenv("DEFAULT_ODOMETER_RATE", raising=False)
    cur = RoutedCursor([])
    patch_db(monkeypatch, exp, cur)
    assert run(exp.get_allowance_rate("org-1")) == {"success": True, "rate": 3.0, "is_default": True}
    assert cur.find("SELECT rate FROM org_allowance_rate")[0][1] == ("org-1",)
    patch_db(monkeypatch, exp, RoutedCursor([("SELECT rate FROM org_allowance_rate", [{"rate": Decimal("3.50")}])]))
    assert run(exp.get_allowance_rate("org-1")) == {"success": True, "rate": 3.5, "is_default": False}


def test_approving_stamps_the_org_rate_once(monkeypatch):
    cur = RoutedCursor([("SELECT rate FROM org_allowance_rate", [{"rate": Decimal("4.00")}]),
                        ("UPDATE odometer_readings", [{"id": 1}])])
    conn = patch_db(monkeypatch, exp, cur)
    run(exp.update_odometer_status("r-1", "approved", "org-1"))
    sql, params = cur.find("UPDATE odometer_readings")[0]
    assert "COALESCE(status, '') <> 'approved'" in sql        # stamps on the transition only, never on a re-approval
    assert params[0] is True and params[1] == Decimal("4.00") and params[2] == "approved"
    assert params[-2:] == ("r-1", "org-1") and conn.commits == 1


def test_approving_without_an_org_rate_uses_the_default(monkeypatch):
    monkeypatch.setenv("DEFAULT_ODOMETER_RATE", "2.5")
    cur = RoutedCursor([("UPDATE odometer_readings", [{"id": 1}])])
    patch_db(monkeypatch, exp, cur)
    run(exp.update_odometer_status("r-1", "approved", "org-1"))
    assert cur.find("UPDATE odometer_readings")[0][1][1] == Decimal("2.5")


def test_rejecting_never_stamps(monkeypatch):
    cur = RoutedCursor([("UPDATE odometer_readings", [{"id": 1}])])
    patch_db(monkeypatch, exp, cur)
    run(exp.update_odometer_status("r-1", "rejected", "org-1"))
    assert cur.find("SELECT rate FROM org_allowance_rate") == []
    params = cur.find("UPDATE odometer_readings")[0][1]
    assert params[0] is False and params[1] is None


def test_allowance_rate_route_accepts_decimals(monkeypatch):
    from router import emp_expenses
    cur = RoutedCursor([("SELECT 1 FROM org_info", [{"x": 1}])])
    patch_db(monkeypatch, exp, cur)
    app = FastAPI()
    app.include_router(emp_expenses.router)
    r = TestClient(app).post("/expenses/admin/allowance-rate", json={"org_id": "org-1", "rate": 3.5})
    assert r.status_code == 200 and r.json()["rate"] == 3.5
    r = TestClient(app).post("/expenses/admin/allowance-rate", json={"org_id": "org-1", "rate": 0})
    assert r.status_code == 400 and r.json() == {"detail": "Allowance rate must be greater than 0"}
