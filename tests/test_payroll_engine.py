"""Pure, table-driven tests of `core_functions/payroll_engine.py` and the shared overtime helper
(`core_functions/overtime.py`): no database, no network. Run: python -m pytest tests/test_payroll_engine.py -q
"""
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from payroll_helpers import (  # noqa: F401
    DAY_SHIFT, NIGHT_SHIFT, NOW, PAYSLIP, TODAY, WORK_DAYS, add_session, at, engine_input, month_sessions,
    overtime_template, session,
)

from core_functions import attendance_rules as rules
from core_functions import overtime as ot
from core_functions import payroll_engine as eng


# ----------------------------------------------------------------------------- fine_amount
@pytest.mark.parametrize("basic, penalty_days, working_days, expected", [
    (30000, 1.0, 27, 1111.11),
    (30000, 0.5, 27, 555.56),          # 555.555... rounds half up
    (1, 0.5, 4, 0.13),                 # 0.125 -> 0.13 (half up, not banker's 0.12)
    (10000, 0.5, 26, 192.31),
    (30000, 2.0, 26, 2307.69),
    (30000, 0.0, 27, 0.0),             # no penalty
    (0, 1.0, 27, 0.0),                 # no basic pay
    (30000, 1.0, 0, 0.0),              # no working days: never divide by zero
    (30000, 1.0, -3, 0.0),
    (None, 1.0, 27, 0.0),
])
def test_fine_amount(basic, penalty_days, working_days, expected):
    assert eng.fine_amount(basic, penalty_days, working_days) == expected


# ----------------------------------------------------------------------------- late_penalty (the attendance rule)
@pytest.mark.parametrize("rule, late_days, units, days", [
    (None, 9, 0, 0.0),                                                    # no rule saved: nothing
    ({"late_punch_in_count": 3, "consider_as": "Half Day"}, 0, 0, 0.0),
    ({"late_punch_in_count": 3, "consider_as": "Half Day"}, 2, 0, 0.0),   # threshold not reached
    ({"late_punch_in_count": 3, "consider_as": "Half Day"}, 3, 1, 0.5),   # exactly the threshold
    ({"late_punch_in_count": 3, "consider_as": "Half Day"}, 8, 2, 1.0),
    ({"late_punch_in_count": 3, "consider_as": "Leave"}, 3, 1, 1.0),
    ({"late_punch_in_count": 3, "consider_as": "Leave"}, 9, 3, 3.0),
    ({"late_punch_in_count": 1, "consider_as": "Half Day"}, 4, 4, 2.0),
    ({"late_punch_in_count": 10, "consider_as": "Leave"}, 25, 2, 2.0),
])
def test_late_penalty(rule, late_days, units, days):
    p = eng.late_penalty(rule, late_days)
    assert (p["penalty_units"], p["penalty_days"], p["configured"]) == (units, days, rule is not None)


# ----------------------------------------------------------------------------- odometer
@pytest.mark.parametrize("readings, org_rate, default, expected", [
    ([], None, 3, "0"),
    ([{"distance_km": 10, "allowance_rate": 3}], 4, 3, "30"),                       # the stamp wins
    ([{"distance_km": 10, "allowance_rate": None}], 4, 3, "40"),                    # no stamp: organisation rate
    ([{"distance_km": 10, "allowance_rate": None}], None, 3, "30"),                 # neither: the default
    ([{"distance_km": 10, "allowance_rate": None}], None, 1, "10"),                 # legacy default via env
    ([{"distance_km": Decimal("12.5"), "allowance_rate": Decimal("3.50")}], None, 3, "43.750"),   # decimals are exact
    ([{"distance_km": None, "allowance_rate": 3}, {"distance_km": 2, "allowance_rate": 3}], None, 3, "6"),
    ([{"distance_km": 4, "allowance_rate": 3}, {"distance_km": 6, "allowance_rate": 5},
      {"distance_km": 1, "allowance_rate": None}], 2, 3, "44"),                     # a rate change does not rewrite stamps
])
def test_odometer_amount(readings, org_rate, default, expected):
    assert eng.odometer_amount(readings, org_rate, default) == Decimal(expected)


def test_default_odometer_rate_env(monkeypatch):
    monkeypatch.delenv("DEFAULT_ODOMETER_RATE", raising=False)
    assert eng.default_odometer_rate() == Decimal("3")
    for raw, want in (("1", "1"), ("2.5", "2.5"), ("0", "3"), ("-1", "3"), ("abc", "3"), ("", "3")):
        monkeypatch.setenv("DEFAULT_ODOMETER_RATE", raw)
        assert eng.default_odometer_rate() == Decimal(want), raw


# ----------------------------------------------------------------------------- small helpers
@pytest.mark.parametrize("payslip, daily, hourly, main, types", [
    (True, True, True, "MONTHLY_FIXED", ["MONTHLY_FIXED", "DAILY_WAGES", "HOURLY"]),
    (False, True, True, "DAILY_WAGES", ["DAILY_WAGES", "HOURLY"]),
    (False, False, True, "HOURLY", ["HOURLY"]),
    (False, False, False, "MONTHLY_FIXED", []),
    (True, False, False, "MONTHLY_FIXED", ["MONTHLY_FIXED"]),
])
def test_salary_type(payslip, daily, hourly, main, types):
    assert eng.derive_salary_type(payslip, daily, hourly) == main
    assert eng.salary_types_of(payslip, daily, hourly) == types


def test_pick_detail_prefers_the_user_row():
    rows = [{"user_id": None, "v": "template"}, {"user_id": "u2", "v": "other"}, {"user_id": "u1", "v": "mine"}]
    assert eng.pick_detail(rows, "u1")["v"] == "mine"
    assert eng.pick_detail(rows, "u3")["v"] == "template"
    assert eng.pick_detail([{"user_id": "u2"}], "u1") is None
    assert eng.pick_detail([], "u1") is None and eng.pick_detail(None, "u1") is None


# ----------------------------------------------------------------------------- attendance_summary
def summary(by_date, **kw):
    args = dict(year=2026, month=10, sessions_by_date=by_date, shift=DAY_SHIFT, grace_minutes=0, leave_dates=[],
                holidays={}, off_weekdays=[6], today=TODAY, now=NOW)
    args.update(kw)
    return eng.attendance_summary(**args)


def test_summary_counts_distinct_dates_not_rows():
    by_date = month_sessions([1, 2])
    add_session(by_date, 1, in_hm="14:00", hours=1)         # a second session on the 1st
    s = summary(by_date)
    assert s["present_days"] == 2
    assert s["total_hours"] == 19.0


def test_summary_ignores_rows_outside_the_month():
    by_date = month_sessions([1])
    by_date[date(2026, 9, 30)] = [session(30, "09:00", 9)]
    by_date[date(2026, 11, 1)] = [session(1, "09:00", 9)]
    s = summary(by_date)
    assert s["present_days"] == 1 and s["total_hours"] == 9.0


def test_summary_today_and_future_days_are_not_absent():
    s = summary({}, today=date(2026, 10, 10))
    # 1..9 October elapsed: weekdays 1,2,3,5,6,7,8,9 -> 8 absent working days (4 is a Sunday); the 10th and later upcoming
    assert s["absent_days"] == 8


@pytest.mark.parametrize("hours, half", [(4.49, 1), (4.5, 0), (0.02, 1), (9.0, 0)])
def test_summary_half_day_boundary(hours, half):
    # 09:00-18:00 shift = 540 min: half = worked >= 1 min and < 270 min
    assert summary(month_sessions([1], hours=hours))["half_days"] == half


def test_summary_open_session_is_never_a_half_day():
    by_date = {date(2026, 10, 1): [session(1, "09:00", open_=True)]}
    s = summary(by_date)
    assert (s["present_days"], s["half_days"]) == (1, 0)


def test_summary_leave_does_not_override_punches():
    s = summary(month_sessions([1]), leave_dates=[date(2026, 10, 1), date(2026, 10, 2)])
    assert (s["present_days"], s["leave_days"]) == (1, 1)


def test_closed_session_hours_ignores_open_sessions_and_uses_out_minus_in_when_unstored():
    by_date = {date(2026, 10, 1): [session(1, "09:00", 4), session(1, "14:00", open_=True)],
               date(2026, 10, 2): [{"punch_in": at(2, "09:00"), "punch_out": at(2, "12:30"), "work_hours": None,
                                    "auto_closed": False}]}
    assert eng.closed_session_hours(by_date, NOW) == 7.5


# ----------------------------------------------------------------------------- overtime helper (D-9)
def test_shift_minutes_wrap():
    assert ot.shift_minutes_of(time(9, 0), time(18, 0)) == 540
    assert ot.shift_minutes_of(time(21, 0), time(6, 0)) == 540
    assert ot.shift_minutes_of(time(0, 0), time(0, 0)) == 0           # unusable shift
    assert ot.shift_minutes_of(None, None) == 0


@pytest.mark.parametrize("worked, shift, present, duration, amount, minutes, money", [
    (1000, 480, 1, 30, 50, 520, 850),        # 17 slots
    (575, 480, 1, 30, 50, 95, 150),          # 95 min -> 3 slots
    (300, 480, 1, 30, 50, 0, 0),             # short day: nothing
    (960, 480, 2, 30, 50, 0, 0),             # shift subtracted per present day
    (1200, 480, 2, 60, 100, 240, 400),
    (600, 540, 1, 0, 50, 60, 0),             # a zero duration pays nothing (no division by zero)
])
def test_ot_figures(worked, shift, present, duration, amount, minutes, money):
    m, a = ot.ot_figures(worked, shift, present, duration, amount)
    assert (m, a) == (Decimal(minutes), Decimal(money))


def _sessions(spec):
    """{day: [(in 'HH:MM', hours | None=open, auto_closed)]} -> overtime_for_month input."""
    out = {}
    for day, items in spec.items():
        for in_hm, hours, *rest in items:
            out.setdefault(date(2026, 10, day), []).append(
                session(day, in_hm, hours or 0, auto_closed=bool(rest and rest[0]), open_=hours is None))
    return out


@pytest.mark.parametrize("spec, shift, expect", [
    # single day, over the shift
    ({1: [("09:00", 9.5)]}, 480, dict(present_days=1, ot_days=1, ot_minutes=90, ot_amount=150)),
    # two sessions of one date: one present day, shift subtracted once
    ({1: [("09:00", 5), ("15:00", 5)]}, 540, dict(present_days=1, ot_days=1, ot_minutes=60, ot_amount=100)),
    # a short day offsets a long day (monthly aggregate)
    ({1: [("09:00", 10)], 2: [("09:00", 7)]}, 480, dict(present_days=2, ot_days=1, ot_minutes=60, ot_amount=100)),
    # an open session is present but adds no minutes
    ({1: [("09:00", 10)], 2: [("09:00", None)]}, 480, dict(present_days=2, ot_days=1, ot_minutes=0, ot_amount=0)),
    # no shift: every worked minute is overtime
    ({1: [("09:00", 2.5)]}, 0, dict(present_days=1, ot_days=1, ot_minutes=150, ot_amount=250)),
    # nothing worked
    ({}, 480, dict(present_days=0, ot_days=0, ot_minutes=0, ot_amount=0)),
    # an auto-closed (forgotten punch-out) day is skipped entirely until reviewed
    ({1: [("09:00", 10)], 2: [("09:00", 9, True)]}, 480, dict(present_days=1, ot_days=1, ot_minutes=120, ot_amount=200)),
    # a day that has an auto-closed session next to a normal one is skipped as a whole
    ({1: [("09:00", 4), ("15:00", 12, True)]}, 480, dict(present_days=0, ot_days=0, ot_minutes=0, ot_amount=0)),
])
def test_overtime_for_month(spec, shift, expect):
    r = ot.overtime_for_month(_sessions(spec), shift, 30, 50, NOW)          # 30-minute slots of 50
    got = {k: (int(v) if isinstance(v, Decimal) else v) for k, v in r.items()
           if k in ("present_days", "ot_days", "ot_minutes", "ot_amount")}
    assert got == expect


def test_report_and_salary_use_the_same_overtime_helper():
    """The overtime report (`compute_overtime`) and the salary engine both call `overtime_for_month`: same sessions,
    same shift, same template -> the same amount."""
    by_date = month_sessions()
    for day in WORK_DAYS[:5]:
        by_date[date(2026, 10, day)] = [session(day, "09:00", 10.5)]
    fig = ot.overtime_for_month(by_date, ot.shift_minutes_of(DAY_SHIFT["start_time"], DAY_SHIFT["end_time"]), 30, 50, NOW)
    d = eng.compute_salary(engine_input(sessions_by_date=by_date, incentives=[overtime_template(30, 50)]))
    assert d["earnings"]["overtime"] == float(fig["ot_amount"]) == 750.0
    assert d["overtime_minutes"] == int(fig["ot_minutes"]) == 450


def test_group_attendance_rows_sorts_sessions_and_skips_rows_without_punch_in():
    rows = [
        {"user_id": "a", "attendance_date": date(2026, 10, 1), "punch_in": at(1, "14:00"), "punch_out": at(1, "18:00"),
         "work_hours": Decimal("4.00"), "auto_closed": False},
        {"user_id": "a", "attendance_date": date(2026, 10, 1), "punch_in": at(1, "09:00"), "punch_out": at(1, "13:00"),
         "work_hours": Decimal("4.00"), "auto_closed": None},
        {"user_id": "a", "attendance_date": date(2026, 10, 2), "punch_in": None, "punch_out": None, "work_hours": None},
        {"user_id": "b", "attendance_date": date(2026, 10, 1), "punch_in": at(1, "10:00"), "punch_out": None,
         "work_hours": None, "auto_closed": False},
    ]
    g = ot.group_attendance_rows(rows)
    day = g["a"][date(2026, 10, 1)]
    assert [s["punch_in"].hour for s in day] == [9, 14]
    assert all(isinstance(s["work_hours"], float) for s in day)
    assert day[0]["auto_closed"] is False
    assert date(2026, 10, 2) not in g["a"]
    assert g["b"][date(2026, 10, 1)][0]["work_hours"] is None


# ----------------------------------------------------------------------------- compute_salary details
def test_compute_salary_is_a_pure_function_of_its_input():
    inp = engine_input(payslip=PAYSLIP, sessions_by_date=month_sessions())
    assert eng.compute_salary(inp) == eng.compute_salary(inp)


def test_compute_salary_handles_null_numeric_columns():
    d = eng.compute_salary(engine_input(
        payslip={"basic_pay": None, "hra": None, "epf": None, "professional_tax": None, "health_insurance": None},
        custom_earnings=[{"name": "x", "amount": None}], daily_rate=None))
    assert d["net_salary"] == 0.0 and d["earnings"]["custom"] == [{"name": "x", "amount": 0.0}]


def test_compute_salary_response_keys_keep_the_contract():
    d = eng.compute_salary(engine_input(payslip=PAYSLIP))
    assert {"user_id", "month", "year", "present_days", "total_hours", "earnings", "deductions", "total_earnings",
            "total_deductions", "net_salary"} <= set(d)
    assert set(d["earnings"]) == {"basic", "hra", "daily_wages", "hourly_calc", "incentive_count",
                                  "incentive_turnover", "bonus", "overtime", "odometer", "travel", "others", "custom"}
    assert set(d["deductions"]) == {"epf", "professional_tax", "health_insurance", "advance", "fine", "custom"}


# ----------------------------------------------------------------------------- paid state
def test_snapshot_drops_only_the_paid_keys():
    d = eng.compute_salary(engine_input(payslip=PAYSLIP))
    d["paid"], d["paid_on"], d["paid_date"], d["recomputed_net_salary"] = True, "2026-10-31", "2026-10-31", 1.0
    snap = eng.snapshot_of(d)
    assert "paid" not in snap and "paid_on" not in snap and "recomputed_net_salary" not in snap
    assert snap["net_salary"] == d["net_salary"] and snap["earnings"] == d["earnings"]


def test_unpaid_row_is_live_with_paid_false():
    d = eng.compute_salary(engine_input(payslip=PAYSLIP))
    out = eng.apply_paid_state(d, None)
    assert (out["paid"], out["paid_on"], out["paid_date"]) == (False, None, None)
    assert out["net_salary"] == d["net_salary"]


def test_paid_row_with_snapshot_returns_the_stored_figures():
    stored = eng.compute_salary(engine_input(payslip=PAYSLIP, sessions_by_date=month_sessions()))
    live = eng.compute_salary(engine_input(payslip=dict(PAYSLIP, basic_pay=45000), sessions_by_date=month_sessions()))
    out = eng.apply_paid_state(live, {"net_amount": Decimal(str(stored["net_salary"])), "paid_date": "2026-10-31",
                                      "snapshot": eng.snapshot_of(stored)})
    assert out["paid"] is True and out["paid_on"] == "2026-10-31" and out["paid_date"] == "2026-10-31"
    assert out["net_salary"] == stored["net_salary"] != live["net_salary"]
    assert out["earnings"]["basic"] == 30000.0           # not recomputed after a template edit


def test_paid_row_without_snapshot_keeps_stored_net_and_shows_the_live_one():
    live = eng.compute_salary(engine_input(payslip=dict(PAYSLIP, basic_pay=45000)))
    out = eng.apply_paid_state(live, {"net_amount": Decimal("37500.00"), "paid_date": "2026-10-31", "snapshot": None})
    assert out["paid"] is True and out["net_salary"] == 37500.0
    assert out["recomputed_net_salary"] == live["net_salary"]
    same = eng.apply_paid_state(live, {"net_amount": Decimal(str(live["net_salary"])), "paid_date": "2026-10-31",
                                       "snapshot": None})
    assert "recomputed_net_salary" not in same


def test_apply_paid_state_does_not_mutate_its_input():
    d = eng.compute_salary(engine_input(payslip=PAYSLIP))
    before = dict(d)
    eng.apply_paid_state(d, {"net_amount": Decimal("1"), "paid_date": "2026-10-31", "snapshot": None})
    assert d == before


# ----------------------------------------------------------------------------- rules stay in sync
def test_attendance_rule_functions_is_late_matches_attendance_rules():
    from core_functions import attendance_rule_functions as arf
    for p in (time(8, 59), time(9, 0), time(9, 1), time(20, 30), time(21, 30), time(0, 30)):
        for start, end in ((time(9, 0), time(18, 0)), (time(21, 0), time(6, 0))):
            for grace in (0, 10):
                assert arf.is_late(p, start, end, grace) == rules.is_late(p, start, end, grace)


def test_sessions_of_other_months_are_ignored_by_overtime_too():
    by_date = month_sessions([1], hours=10.5)                       # 90 min over the 9 h shift
    by_date[date(2026, 9, 30)] = [session(30, "09:00", 12)]
    by_date[date(2026, 11, 1)] = [session(1, "09:00", 12)]
    d = eng.compute_salary(engine_input(sessions_by_date=by_date, incentives=[overtime_template(30, 50)]))
    assert d["present_days"] == 1 and d["overtime_minutes"] == 90 and d["earnings"]["overtime"] == 150.0
