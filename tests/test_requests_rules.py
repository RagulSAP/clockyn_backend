"""DB-free, table-driven tests of the pure request rules (core_functions/request_rules.py, WP7).
Messages and status codes follow the app's contract mock (e2e/mock-server/routes/requests.js) and requestRules.ts.
Run: python -m pytest tests/test_requests_rules.py -q
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import request_rules as rr  # noqa: E402

TODAY = date(2026, 11, 2)


def fails(fn, *args, status=400, detail=None):
    with pytest.raises(HTTPException) as exc:
        fn(*args)
    assert exc.value.status_code == status
    if detail is not None:
        assert exc.value.detail == detail
    return exc.value


# ---------------------------------------------------------------------------- dates and times
@pytest.mark.parametrize("value", ["2026-11-02", " 2026-12-31 ", "2028-02-29"])
def test_parse_iso_date_accepts_calendar_dates(value):
    assert rr.parse_iso_date(value, "x") == date.fromisoformat(value.strip())


@pytest.mark.parametrize("value", ["", None, "2026-13-01", "2026-02-30", "20261102", "02/11/2026", "2026-11-2", "abc"])
def test_parse_iso_date_rejects_everything_else(value):
    fails(rr.parse_iso_date, value, "leave date", detail="Invalid leave date")


@pytest.mark.parametrize("raw, expected", [
    ("09:05", "09:05"), ("9:05", "09:05"), ("14:05:30", "14:05"), ("09:05 AM", "09:05"), ("9:05am", "09:05"),
    ("12:00 AM", "00:00"), ("12:30 PM", "12:30"), ("01:15 PM", "13:15"), ("23:59", "23:59"), ("00:00", "00:00"),
    ("24:00", None), ("09:60", None), ("13:00 PM", None), ("0:30 AM", None), ("", None), (None, None), ("noon", None),
])
def test_normalise_time(raw, expected):
    assert rr.normalise_time(raw) == expected


# ---------------------------------------------------------------------------- reason
def test_reason_required_and_length_boundary():
    assert rr.clean_reason("  trip  ", True) == "trip"
    assert rr.clean_reason(None, False) == ""
    fails(rr.clean_reason, "   ", True, detail="Reason is required")
    assert len(rr.clean_reason("x" * 500, True)) == 500
    fails(rr.clean_reason, "x" * 501, True)


# ---------------------------------------------------------------------------- leave
def test_leave_valid_is_sorted_deduplicated_and_trimmed():
    days, reason = rr.validate_leave(["2026-11-05", "2026-11-03", "2026-11-05"], " Family function ", TODAY)
    assert days == [date(2026, 11, 3), date(2026, 11, 5)]
    assert reason == "Family function"


def test_leave_today_is_allowed_yesterday_is_not():
    assert rr.validate_leave(["2026-11-02"], "x", TODAY)[0] == [TODAY]
    fails(rr.validate_leave, ["2026-11-01"], "x", TODAY, detail="Leave dates cannot be in the past")
    # one past date among future ones still fails
    fails(rr.validate_leave, ["2026-11-10", "2026-10-31"], "x", TODAY, detail="Leave dates cannot be in the past")


@pytest.mark.parametrize("dates, reason, detail", [
    ([], "x", "Select at least one leave date"),
    (None, "x", "Select at least one leave date"),
    ("2026-11-05", "x", "Select at least one leave date"),            # not a list
    (["2026-11-05"], "", "Reason is required"),
    (["2026-11-05"], "   ", "Reason is required"),
    (["2026-11-05", "nope"], "x", "Invalid leave date"),
    ([], "", "Select at least one leave date"),                       # the mock checks the dates first
])
def test_leave_validation_table(dates, reason, detail):
    fails(rr.validate_leave, dates, reason, TODAY, detail=detail)


def test_leave_date_count_boundary():
    from datetime import timedelta
    many = [(TODAY + timedelta(days=i)).isoformat() for i in range(rr.MAX_LEAVE_DATES)]
    assert len(rr.validate_leave(many, "x", TODAY)[0]) == rr.MAX_LEAVE_DATES
    too_many = many + [(TODAY + timedelta(days=rr.MAX_LEAVE_DATES)).isoformat()]
    fails(rr.validate_leave, too_many, "x", TODAY)


@pytest.mark.parametrize("requested, existing, expected", [
    ([date(2026, 11, 3)], [date(2026, 11, 3)], True),
    ([date(2026, 11, 3), date(2026, 11, 4)], [date(2026, 11, 4)], True),
    ([date(2026, 11, 3)], [date(2026, 11, 4)], False),
    ([date(2026, 11, 3)], [], False),
])
def test_overlap_rule(requested, existing, expected):
    assert rr.overlaps_existing_leave(requested, existing) is expected


# ---------------------------------------------------------------------------- permission
@pytest.mark.parametrize("start, end, ok", [
    ("09:00", "09:01", True),          # one minute
    ("09:00", "15:00", True),          # exactly 6 hours = 360 minutes
    ("09:00", "15:01", False),         # 361 minutes
    ("09:00", "09:00", False),         # zero length
    ("10:00", "09:00", False),         # end before start
    ("00:00", "06:00", True),
    ("18:00", "23:59", True),          # ends within the day (359 minutes)
    ("17:30", "23:59", False),         # 389 minutes
    ("18:00", "00:00", False),         # would cross midnight: end clock time is earlier than the start
    ("09:00 AM", "03:00 PM", True),    # 12-hour wire format, 360 minutes
    ("09:00 AM", "03:01 PM", False),
])
def test_permission_duration_boundaries(start, end, ok):
    if ok:
        day, s, e, text = rr.validate_permission("2026-11-03", start, end, "doctor", TODAY)
        assert day == date(2026, 11, 3) and text == "doctor"
        assert rr.minutes_of(e) - rr.minutes_of(s) <= rr.MAX_PERMISSION_MINUTES
    else:
        with pytest.raises(HTTPException) as exc:
            rr.validate_permission("2026-11-03", start, end, "doctor", TODAY)
        assert exc.value.status_code == 400
        assert exc.value.detail in ("End time must be after start time",
                                    "End time cannot be more than 6 hours after start time")


def test_permission_exact_messages():
    fails(rr.validate_permission, "2026-11-03", "10:00", "09:00", "r", TODAY, detail="End time must be after start time")
    fails(rr.validate_permission, "2026-11-03", "09:00", "09:00", "r", TODAY, detail="End time must be after start time")
    fails(rr.validate_permission, "2026-11-03", "09:00", "16:00", "r", TODAY,
          detail="End time cannot be more than 6 hours after start time")
    fails(rr.validate_permission, "2026-11-03", "09:00", "10:00", " ", TODAY, detail="Reason is required")
    fails(rr.validate_permission, "2026-11-01", "09:00", "10:00", "r", TODAY, detail="Permission date cannot be in the past")
    fails(rr.validate_permission, "2026-13-01", "09:00", "10:00", "r", TODAY, detail="Invalid permission date")


@pytest.mark.parametrize("pdate, start, end", [
    ("", "09:00", "10:00"), (None, "09:00", "10:00"), ("2026-11-03", "", "10:00"), ("2026-11-03", "09:00", ""),
    ("2026-11-03", "bad", "10:00"), ("2026-11-03", "09:00", "25:00"),
])
def test_permission_required_fields(pdate, start, end):
    fails(rr.validate_permission, pdate, start, end, "r", TODAY,
          detail="permission_date, start_time and end_time are required")


def test_permission_today_is_allowed():
    assert rr.validate_permission("2026-11-02", "09:00", "10:00", "r", TODAY)[0] == TODAY


# ---------------------------------------------------------------------------- shift change
def test_shift_change_shape_rules():
    assert rr.validate_shift_change(" Exam ", 3, "2026-11-05", TODAY) == (3, date(2026, 11, 5), "Exam")
    assert rr.validate_shift_change("x", "4", "2026-11-02", TODAY)[0] == 4          # today is fine
    fails(rr.validate_shift_change, "", 3, "2026-11-05", TODAY, detail="Reason is required")
    fails(rr.validate_shift_change, "x", None, "2026-11-05", TODAY, detail="Requested shift not found")
    fails(rr.validate_shift_change, "x", True, "2026-11-05", TODAY, detail="Requested shift not found")
    fails(rr.validate_shift_change, "x", "abc", "2026-11-05", TODAY, detail="Requested shift not found")
    fails(rr.validate_shift_change, "x", 3, "2026-11-01", TODAY, detail="Shift change date cannot be in the past")
    fails(rr.validate_shift_change, "x", 3, "soon", TODAY, detail="Invalid shift change date")


# ---------------------------------------------------------------------------- device change rate limit
@pytest.mark.parametrize("recent, limited", [(0, False), (1, False), (2, False), (3, True), (4, True), (None, False)])
def test_device_change_rate_limit_boundary(recent, limited):
    assert rr.device_change_rate_limited(recent) is limited


# ---------------------------------------------------------------------------- calendar
def test_calendar_range_rules():
    first, last = rr.validate_calendar_range("2026-11-01", "2026-11-30")
    assert (first, last) == (date(2026, 11, 1), date(2026, 11, 30))
    assert rr.validate_calendar_range("2026-11-05", "2026-11-05") == (date(2026, 11, 5), date(2026, 11, 5))
    fails(rr.validate_calendar_range, "2026-11-30", "2026-11-01", detail="end_date must not be before start_date")
    fails(rr.validate_calendar_range, "x", "2026-11-01", detail="Invalid start_date")
    fails(rr.validate_calendar_range, "2026-11-01", "", detail="Invalid end_date")
    fails(rr.validate_calendar_range, "2025-01-01", "2026-11-01")           # wider than the cap


def test_weekly_off_dates_expand_the_range_inclusively():
    # November 2026: Sundays are the 1st, 8th, 15th, 22nd, 29th; Saturdays the 7th, 14th, 21st, 28th
    sundays = rr.weekly_off_dates(date(2026, 11, 1), date(2026, 11, 30), [6])
    assert sundays == ["2026-11-01", "2026-11-08", "2026-11-15", "2026-11-22", "2026-11-29"]
    both = rr.weekly_off_dates(date(2026, 11, 1), date(2026, 11, 8), [5, 6])
    assert both == ["2026-11-01", "2026-11-07", "2026-11-08"]
    assert rr.weekly_off_dates(date(2026, 11, 2), date(2026, 11, 3), [6]) == []
    assert rr.weekly_off_dates(date(2026, 11, 1), date(2026, 11, 1), [6]) == ["2026-11-01"]   # both ends inclusive


# ---------------------------------------------------------------------------- actor matrix
def actor(**kw):
    base = dict(role_id=1, status=1, in_request_org=True, has_permission=False, branch_ids=[], dept_ids=[])
    base.update(kw)
    return base


@pytest.mark.parametrize("who, branch, dept, expected", [
    (None, 1, "d1", rr.FAIL_NOT_FOUND),
    (actor(), 1, "d1", None),                                                        # admin of the org
    (actor(in_request_org=False), 1, "d1", rr.FAIL_OTHER_ORG),                       # admin of another org
    (actor(status=0), 1, "d1", rr.FAIL_INACTIVE),
    (actor(role_id=3), 1, "d1", rr.FAIL_ROLE),                                       # staff
    (actor(role_id=2, has_permission=True), 1, "d1", None),                          # manager, no assignment restriction
    (actor(role_id=2, has_permission=False), 1, "d1", rr.FAIL_PERMISSION),
    (actor(role_id=2, has_permission=True, in_request_org=False), 1, "d1", rr.FAIL_OTHER_ORG),
    (actor(role_id=2, has_permission=True, branch_ids=[1, 2]), 2, "d1", None),
    (actor(role_id=2, has_permission=True, branch_ids=[1, 2]), 3, "d1", rr.FAIL_SCOPE),
    (actor(role_id=2, has_permission=True, branch_ids=[1]), None, "d1", rr.FAIL_SCOPE),
    (actor(role_id=2, has_permission=True, dept_ids=["d1"]), 1, "d1", None),
    (actor(role_id=2, has_permission=True, dept_ids=["d1"]), 1, "d9", rr.FAIL_SCOPE),
    (actor(role_id=2, has_permission=True, branch_ids=[1], dept_ids=["d1"]), 1, "d9", rr.FAIL_SCOPE),   # AND semantics
    (actor(role_id=2, has_permission=True, branch_ids=[1], dept_ids=["d1"]), 1, "d1", None),
])
def test_actor_matrix(who, branch, dept, expected):
    assert rr.evaluate_actor(who, branch, dept) == expected


# ---------------------------------------------------------------------------- single device policy
@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("no", False),
    ("1", True), ("true", True), ("TRUE", True), (" yes ", True), ("on", True),
])
def test_single_device_flag_parsing(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("ENFORCE_SINGLE_DEVICE", raising=False)
    else:
        monkeypatch.setenv("ENFORCE_SINGLE_DEVICE", value)
    assert rr.single_device_enforced() is expected


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
FUTURE = datetime(2026, 12, 1, tzinfo=timezone.utc)
PAST = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("role, stored, expires, supplied, blocked", [
    (3, "dev-A", FUTURE, "dev-B", True),            # staff on a new device
    (2, "dev-A", None, "dev-B", True),              # manager, token without expiry
    (3, "dev-A", FUTURE, "dev-A", False),           # the same device
    (3, None, None, "dev-B", False),                # token cleared by an approved device change
    (3, "", None, "dev-B", False),
    (3, "dev-A", PAST, "dev-B", False),             # the old session already expired
    (1, "dev-A", FUTURE, "dev-B", False),           # admins are multi-device
    (None, "dev-A", FUTURE, "dev-B", False),
])
def test_single_device_blocked(role, stored, expires, supplied, blocked):
    assert rr.single_device_blocked(role, stored, expires, supplied, NOW) is blocked
