"""Characterisation (golden) tests of the payroll engine: hand-computed numbers of a fixed month (October 2026).

These pin the legacy arithmetic of `report_functions.get_staff_salary` (same formulas, same rounding) after it was
extracted into `core_functions/payroll_engine.py`. A golden number may change ONLY for an intended fix, and every such
case carries a `# intentional change` comment that names the legacy value:

  (a) two sessions on one day are one present day and their hours add up,
  (b) a night-shift session belongs to the date it started on and the shift length wraps midnight (overtime),
  (c) `fine` comes from the organisation's attendance rule (it was a constant 0).

Reference month: 31 days, Thursday 1 October 2026, Sundays 4/11/18/25 off, so 27 working days; 22 worked days
(`WORK_DAYS`) of 09:00-18:00 = 9 h each. Run: python -m pytest tests/test_payroll_snapshot.py -q
"""
from datetime import date

import pytest

from payroll_helpers import (  # noqa: F401  (also puts the backend root on sys.path)
    DAY_SHIFT, NIGHT_SHIFT, PAYSLIP, WORK_DAYS, add_session, bonus_template, count_template, engine_input,
    month_sessions, overtime_template, session, turnover_template,
)

from core_functions import payroll_engine as eng


def compute(**over):
    return eng.compute_salary(engine_input(**over))


# ----------------------------------------------------------------------------- normal month
def test_normal_month_payslip_with_custom_lines():
    d = compute(payslip=PAYSLIP, custom_earnings=[{"name": "Conveyance", "amount": 1500}],
                custom_deductions=[{"name": "Canteen", "amount": 300}], sessions_by_date=month_sessions())
    # attendance counters: 22 punched days, 27 working days (4 Sundays off), 5 absent working days
    assert (d["present_days"], d["total_hours"]) == (22, 198.0)
    assert (d["working_days"], d["weekly_off_days"], d["holiday_days"], d["absent_days"]) == (27, 4, 0, 5)
    assert (d["half_days"], d["leave_days"], d["late_days"]) == (0, 0, 0)
    e, ded = d["earnings"], d["deductions"]
    assert (e["basic"], e["hra"]) == (30000.0, 10000.0)
    assert e["custom"] == [{"name": "Conveyance", "amount": 1500.0}]
    assert (ded["epf"], ded["professional_tax"], ded["health_insurance"]) == (1800.0, 200.0, 500.0)
    assert ded["custom"] == [{"name": "Canteen", "amount": 300.0}]
    assert d["total_earnings"] == 41500.0          # 30000 + 10000 + 1500
    assert d["total_deductions"] == 2800.0         # 1800 + 200 + 500 + 300
    assert d["net_salary"] == 38700.0
    assert (ded["fine"], ded["advance"]) == (0.0, 0.0)
    assert d["salary_type"] == "MONTHLY_FIXED" and d["salary_types"] == ["MONTHLY_FIXED"]
    assert (d["paid"], d["paid_on"]) == (False, None)


def test_complete_employee_every_component():
    d = compute(
        payslip=PAYSLIP, custom_earnings=[{"name": "Conveyance", "amount": 1500}],
        custom_deductions=[{"name": "Canteen", "amount": 300}], sessions_by_date=month_sessions(),
        incentives=[count_template(250), turnover_template(2.5), bonus_template(10, "October", 2026),
                    overtime_template(30, 50)],
        odometer=[{"distance_km": 10, "allowance_rate": 3}, {"distance_km": 5, "allowance_rate": None}],
        org_allowance_rate=4, travel_amount=400, others_amount=150, advance_amount=2000)
    e, ded = d["earnings"], d["deductions"]
    assert e["incentive_count"] == 250.0
    assert e["incentive_turnover"] == 750.0        # 30000 x 2.5 / 100
    assert e["bonus"] == 3000.0                    # 30000 x 10 / 100, October 2026
    assert e["overtime"] == 0.0                    # 9 h days never exceed the 9 h shift
    assert e["odometer"] == 50.0                   # 10 km x stamp 3 + 5 km x org rate 4
    assert (e["travel"], e["others"]) == (400.0, 150.0)
    assert ded["advance"] == 2000.0
    # 30000 + 10000 + 250 + 750 + 3000 + 0 + 50 + 400 + 150 + 1500
    assert d["total_earnings"] == 46100.0
    assert d["total_deductions"] == 4800.0         # 1800 + 200 + 500 + 2000 + 300
    assert d["net_salary"] == 41300.0


def test_advance_deduction():
    d = compute(payslip=PAYSLIP, sessions_by_date=month_sessions(), advance_amount=2000)
    assert d["deductions"]["advance"] == 2000.0
    assert d["total_deductions"] == 4500.0         # 2500 + 2000
    assert d["net_salary"] == 35500.0              # 40000 - 4500


def test_no_assignment_is_all_zero():
    d = compute(sessions_by_date=month_sessions())
    assert (d["total_earnings"], d["total_deductions"], d["net_salary"]) == (0.0, 0.0, 0.0)
    assert d["salary_type"] == "MONTHLY_FIXED" and d["salary_types"] == []
    assert d["present_days"] == 22


# ----------------------------------------------------------------------------- half days, leave, wages
def _half_leave_month():
    by_date = month_sessions(WORK_DAYS[:15])                      # 15 full days
    for day in WORK_DAYS[15:18]:                                  # 3 half days: 4 h of a 9 h shift
        add_session(by_date, day, in_hm="09:00", hours=4)
    leaves = [date(2026, 10, d) for d in WORK_DAYS[18:20]]        # 2 approved leave days
    return by_date, leaves


def test_half_days_and_leave_do_not_prorate_basic():
    by_date, leaves = _half_leave_month()
    d = compute(payslip=PAYSLIP, sessions_by_date=by_date, leave_dates=leaves)
    assert (d["present_days"], d["half_days"], d["leave_days"]) == (18, 3, 2)
    assert d["absent_days"] == 7                                  # 27 - 18 punched - 2 leave
    assert d["total_hours"] == 147.0                              # 15 x 9 + 3 x 4
    # decision D-8 / D-7: no proration of basic pay, leave only changes the day type
    assert d["earnings"]["basic"] == 30000.0
    assert d["net_salary"] == 37500.0                             # 40000 - 2500


def test_daily_wage_pays_every_punched_day_half_days_included():
    by_date, leaves = _half_leave_month()
    d = compute(daily_rate=500, sessions_by_date=by_date, leave_dates=leaves)
    assert d["earnings"]["daily_wages"] == 9000.0                 # 500 x 18 punched days (legacy parity)
    assert d["net_salary"] == 9000.0
    assert d["salary_type"] == "DAILY_WAGES"


def test_hourly_wage_pays_closed_session_hours():
    by_date, leaves = _half_leave_month()
    d = compute(hourly_rate=100, sessions_by_date=by_date, leave_dates=leaves)
    assert d["earnings"]["hourly_calc"] == 14700.0                # 100 x 147 h
    assert d["salary_type"] == "HOURLY"


def test_open_session_pays_no_hours_but_is_a_present_day():
    by_date = month_sessions(WORK_DAYS[:2])
    add_session(by_date, WORK_DAYS[2], open_=True)
    d = compute(hourly_rate=100, daily_rate=500, sessions_by_date=by_date)
    assert d["present_days"] == 3 and d["total_hours"] == 18.0
    assert d["earnings"]["hourly_calc"] == 1800.0 and d["earnings"]["daily_wages"] == 1500.0


# ----------------------------------------------------------------------------- late fines
def _late_month(minutes_late="09:30"):
    by_date = month_sessions(WORK_DAYS[:7], in_hm=minutes_late)   # 7 late first punches
    by_date.update(month_sessions(WORK_DAYS[7:22]))               # 15 on time
    return by_date


@pytest.mark.parametrize("rule, fine, penalty_days, net", [
    # `# intentional change` (c): fine was a constant 0, now the rule drives it for organisations that saved one
    ({"late_punch_in_count": 3, "consider_as": "Half Day"}, 1111.11, 1.0, 36388.89),   # 2 units x 0.5 -> 1 day / 27
    ({"late_punch_in_count": 3, "consider_as": "Leave"}, 2222.22, 2.0, 35277.78),      # 2 units x 1.0 -> 2 days / 27
    ({"late_punch_in_count": 7, "consider_as": "Half Day"}, 555.56, 0.5, 36944.44),    # 1 unit: 0.5 day, 555.5555 up
    ({"late_punch_in_count": 8, "consider_as": "Half Day"}, 0.0, 0.0, 37500.0),        # 7 lates < 8: nothing
    (None, 0.0, 0.0, 37500.0),                                                          # no rule saved: no fine (legacy)
])
def test_late_fine_follows_the_attendance_rule(rule, fine, penalty_days, net):
    d = compute(payslip=PAYSLIP, sessions_by_date=_late_month(), rule=rule)
    assert d["late_days"] == 7                    # the lateness itself never depends on the rule
    assert d["penalty_days"] == penalty_days
    assert d["deductions"]["fine"] == fine
    assert d["total_deductions"] == round(2500 + fine, 2)
    assert d["net_salary"] == net
    assert d["fine_rule_configured"] is (rule is not None)


def test_grace_minutes_remove_the_lateness():
    rule = {"late_punch_in_count": 3, "consider_as": "Half Day"}
    on_time = compute(payslip=PAYSLIP, sessions_by_date=_late_month(), rule=rule, grace_minutes=30)
    assert on_time["late_days"] == 0 and on_time["deductions"]["fine"] == 0.0    # 09:30 is not after 09:00 + 30
    late = compute(payslip=PAYSLIP, sessions_by_date=_late_month("09:31"), rule=rule, grace_minutes=30)
    assert late["late_days"] == 7 and late["deductions"]["fine"] == 1111.11


def test_fine_divides_by_the_working_days_of_the_month():
    rule = {"late_punch_in_count": 3, "consider_as": "Half Day"}
    by_date = month_sessions(WORK_DAYS[:3], in_hm="09:30")        # 3 lates -> 0.5 day
    base = compute(payslip=PAYSLIP, sessions_by_date=by_date, rule=rule)
    assert base["working_days"] == 27 and base["deductions"]["fine"] == 555.56
    holidays = {date(2026, 10, 8): "Local holiday", date(2026, 10, 20): "Other holiday"}
    d = compute(payslip=PAYSLIP, sessions_by_date=by_date, rule=rule, holidays=holidays)
    assert d["working_days"] == 25 and d["deductions"]["fine"] == 600.0         # 0.5 x 30000 / 25


def test_fine_only_for_staff_on_a_payslip():
    rule = {"late_punch_in_count": 3, "consider_as": "Half Day"}
    d = compute(daily_rate=500, sessions_by_date=_late_month(), rule=rule)
    assert d["late_days"] == 7 and d["penalty_days"] == 1.0 and d["deductions"]["fine"] == 0.0
    assert d["net_salary"] == 11000.0                              # 500 x 22 days


def test_staff_without_shift_is_never_late():
    rule = {"late_punch_in_count": 1, "consider_as": "Leave"}
    d = compute(payslip=PAYSLIP, sessions_by_date=_late_month(), rule=rule, shift=None)
    assert d["late_days"] == 0 and d["deductions"]["fine"] == 0.0


# ----------------------------------------------------------------------------- overtime above shift hours
def _ot_month(long_days=5, long_hours=10.5, short_day=None):
    by_date = month_sessions()
    for day in WORK_DAYS[:long_days]:
        by_date[date(2026, 10, day)] = [session(day, "09:00", long_hours)]
    if short_day is not None:
        by_date[date(2026, 10, WORK_DAYS[short_day])] = [session(WORK_DAYS[short_day], "09:00", 6)]
    return by_date


def test_overtime_above_shift_hours_monthly_aggregate():
    d = compute(sessions_by_date=_ot_month(), incentives=[overtime_template(30, 50)])
    # worked 5 x 630 + 17 x 540 = 12330 min; shift 22 x 540 = 11880 -> 450 OT min -> 15 slots of 30 min x 50
    assert d["overtime_minutes"] == 450
    assert d["earnings"]["overtime"] == 750.0
    assert d["net_salary"] == 750.0


def test_overtime_short_day_offsets_a_long_day_decision_d9():
    d = compute(sessions_by_date=_ot_month(short_day=21), incentives=[overtime_template(30, 50)])
    # one 9 h day became 6 h: 12330 - 180 = 12150 -> 270 OT min -> 9 slots (the monthly aggregate is kept)
    assert d["overtime_minutes"] == 270 and d["earnings"]["overtime"] == 450.0


@pytest.mark.parametrize("hours, slots", [(9.49, 0), (9.5, 1), (9.99, 1), (10.0, 2), (10.51, 3)])
def test_overtime_slot_boundaries(hours, slots):
    d = compute(sessions_by_date=month_sessions(WORK_DAYS[:1], hours=hours), incentives=[overtime_template(30, 50)])
    assert d["earnings"]["overtime"] == slots * 50.0


def test_overtime_without_shift_counts_all_worked_minutes():
    d = compute(sessions_by_date=month_sessions(WORK_DAYS[:1], hours=2.5), shift=None,
                incentives=[overtime_template(60, 100)])
    assert d["overtime_minutes"] == 150 and d["earnings"]["overtime"] == 200.0


# ----------------------------------------------------------------------------- night shift
def _night_month():
    by_date = month_sessions(WORK_DAYS[:20], in_hm="21:00", hours=9)
    for day in WORK_DAYS[:4]:                                      # 4 long nights: 21:00 -> 07:30 next day
        by_date[date(2026, 10, day)] = [session(day, "21:00", 10.5)]
    return by_date


def test_night_shift_overtime_wraps_the_shift_length():
    d = compute(sessions_by_date=_night_month(), shift=NIGHT_SHIFT, incentives=[overtime_template(30, 50)])
    assert d["present_days"] == 20 and d["total_hours"] == 186.0
    # intentional change (b): the shift 21:00-06:00 is 540 min. Legacy computed end - start = -900 min (no wrap):
    # ot = 11160 - (-900 x 20) = 29160 min -> 972 slots -> 48600.0. Now: 11160 - 540 x 20 = 360 -> 12 slots.
    assert d["overtime_minutes"] == 360
    assert d["earnings"]["overtime"] == 600.0


def test_night_shift_sessions_belong_to_the_start_date():
    by_date = month_sessions([31], in_hm="21:00", hours=9)         # starts 31 October, ends 1 November 06:00
    next_month = {date(2026, 11, 1): [session(1, "21:00", 9)]}      # a November night must not leak in
    by_date.update(next_month)
    d = compute(hourly_rate=100, sessions_by_date=by_date, shift=NIGHT_SHIFT)
    # intentional change (b): one night = one present day / 9 h on its start date, never split or doubled
    assert d["present_days"] == 1 and d["total_hours"] == 9.0
    assert d["earnings"]["hourly_calc"] == 900.0


def test_night_shift_lateness_uses_the_wrapped_window():
    by_date = month_sessions(WORK_DAYS[:2], in_hm="21:20", hours=9)    # two late nights
    by_date.update(month_sessions(WORK_DAYS[2:5], in_hm="20:45", hours=9))   # early arrival is not late
    d = compute(payslip=PAYSLIP, sessions_by_date=by_date, shift=NIGHT_SHIFT,
                rule={"late_punch_in_count": 2, "consider_as": "Leave"})
    assert d["late_days"] == 2 and d["penalty_days"] == 1.0
    assert d["deductions"]["fine"] == 1111.11                      # 1 day x 30000 / 27


# ----------------------------------------------------------------------------- two sessions on one day
def _two_session_day(first=(9, 4), second=(14, 5)):
    by_date = {}
    add_session(by_date, 1, in_hm=f"{first[0]:02d}:00", hours=first[1])
    add_session(by_date, 1, in_hm=f"{second[0]:02d}:00", hours=second[1])
    return by_date


def test_two_sessions_one_day_is_one_daily_wage():
    d = compute(daily_rate=500, hourly_rate=100, sessions_by_date=_two_session_day())
    # intentional change (a): legacy counted attendance ROWS: present_days 2 -> daily wage 1000.0
    assert d["present_days"] == 1
    assert d["earnings"]["daily_wages"] == 500.0
    assert d["total_hours"] == 9.0 and d["earnings"]["hourly_calc"] == 900.0   # both sessions add up (as before)


def test_two_sessions_one_day_subtract_the_shift_once():
    d = compute(sessions_by_date=_two_session_day(first=(9, 5), second=(15, 5.5)),
                incentives=[overtime_template(30, 50)])
    # intentional change (a): 630 min worked on one date, shift 540 subtracted ONCE -> 90 min -> 3 slots = 150.0.
    # Legacy: present_days = COUNT(*) = 2 -> 630 - 2 x 540 < 0 -> overtime 0.0
    assert d["overtime_minutes"] == 90 and d["earnings"]["overtime"] == 150.0


def test_two_short_sessions_make_one_half_day():
    d = compute(sessions_by_date=_two_session_day(first=(9, 2), second=(14, 2)), daily_rate=500)
    # 2 h + 2 h = 4 h of a 9 h shift: ONE half day (each session alone would be two), one punched day
    assert (d["half_days"], d["present_days"]) == (1, 1)
    assert d["earnings"]["daily_wages"] == 500.0


# ----------------------------------------------------------------------------- weekly offs and holidays
@pytest.mark.parametrize("off_weekdays, holidays, working, weekly_off, holiday", [
    ([6], {}, 27, 4, 0),                                                   # Sundays off
    ([6], {date(2026, 10, 2): "A", date(2026, 10, 20): "B"}, 25, 4, 2),    # two weekday holidays
    ([6], {date(2026, 10, 11): "Sunday holiday"}, 27, 3, 1),               # a holiday on an off day counts once
    ([5, 6], {}, 22, 9, 0),                                                # Saturday and Sunday off
    ([], {}, 31, 0, 0),                                                    # no weekly off at all
])
def test_weekly_offs_and_holidays_are_not_working_days(off_weekdays, holidays, working, weekly_off, holiday):
    # ten Monday-Thursday punches that are neither holidays nor Saturdays (punches on an off day / holiday count)
    punched = [5, 6, 7, 8, 12, 13, 14, 15, 19, 21]
    d = compute(payslip=PAYSLIP, sessions_by_date=month_sessions(punched), off_weekdays=off_weekdays,
                holidays=holidays)
    assert (d["working_days"], d["weekly_off_days"], d["holiday_days"]) == (working, weekly_off, holiday)
    # absent = elapsed working days nobody punched: off days and holidays are never absences
    assert d["absent_days"] == working - 10


def test_punches_on_an_off_day_count_as_a_working_day():
    by_date = month_sessions([4, 5])                                       # Sunday 4 October + Monday
    d = compute(daily_rate=500, sessions_by_date=by_date)
    assert d["present_days"] == 2 and d["weekly_off_days"] == 3 and d["working_days"] == 28
    assert d["earnings"]["daily_wages"] == 1000.0


# ----------------------------------------------------------------------------- incentives (formulas kept, D-21)
def test_incentive_formulas_are_the_legacy_flat_ones():
    inc = [count_template(250, incentive_id="c1"), count_template(100, incentive_id="c2"),
           turnover_template(2.5), bonus_template(10, "October", 2026)]
    d = compute(payslip=PAYSLIP, sessions_by_date=month_sessions(), incentives=inc)
    assert d["earnings"]["incentive_count"] == 350.0               # every assigned COUNT template pays its flat amount
    assert d["earnings"]["incentive_turnover"] == 750.0            # basic x percent
    assert d["earnings"]["bonus"] == 3000.0


@pytest.mark.parametrize("month, year, bonus", [("October", 2026, 3000.0), ("September", 2026, 0.0),
                                                ("October", 2025, 0.0)])
def test_bonus_pays_only_in_its_month_and_year(month, year, bonus):
    d = compute(payslip=PAYSLIP, incentives=[bonus_template(10, month, year)])
    assert d["earnings"]["bonus"] == bonus


def test_user_specific_detail_beats_the_template_row():
    inc = [{"type": "COUNT", "incentive_id": "c1", "details": [
        {"user_id": None, "each_incentive": 100}, {"user_id": "u1", "each_incentive": 400},
        {"user_id": "other", "each_incentive": 999}]}]
    assert compute(incentives=inc)["earnings"]["incentive_count"] == 400.0
    assert compute(user_id="u2", incentives=inc)["earnings"]["incentive_count"] == 100.0


def test_unrounded_float_arithmetic_is_kept_then_rounded_once():
    # legacy rounding: components rounded for display, totals computed from the unrounded floats
    d = compute(payslip={"basic_pay": 100.10, "hra": 0, "epf": 0, "professional_tax": 0, "health_insurance": 0},
                incentives=[turnover_template(33.33)])
    assert d["earnings"]["incentive_turnover"] == round(100.10 * 33.33 / 100, 2)
    assert d["net_salary"] == round(100.10 + 100.10 * 33.33 / 100, 2)
