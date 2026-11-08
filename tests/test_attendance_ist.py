"""DB-free tests for WP5 attendance integrity: IST business date, two sessions per day, lateness / half day,
forgotten punch-out auto-close (D-22), day types of `detail=v2`, and the SQL of every changed statement.

Pure rules are table driven; the DB layer is a scripted fake cursor (no PostgreSQL, no ML models).
Run: python -m pytest tests/test_attendance_ist.py -q
"""
import asyncio
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import attendance_rules as R  # noqa: E402
from utils.timezone import IST, UTC  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def utc(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def ist(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=IST)


# =============================================================================== pure rules
@pytest.mark.parametrize("instant,expected", [
    (utc(2026, 11, 2, 18, 29, 59), date(2026, 11, 2)),   # 23:59:59 IST
    (utc(2026, 11, 2, 18, 30, 0), date(2026, 11, 3)),    # 00:00:00 IST of the next day
    (utc(2026, 11, 2, 0, 0, 0), date(2026, 11, 2)),      # 05:30 IST
    (utc(2026, 12, 31, 18, 30), date(2027, 1, 1)),       # year boundary
    (datetime(2026, 11, 2, 18, 30), date(2026, 11, 3)),  # naive values are UTC
])
def test_ist_business_date_boundary(instant, expected):
    assert R.ist_date_of(instant) == expected
    assert R.ist_today(instant) == expected


def test_ist_time_of():
    assert R.ist_time_of(utc(2026, 11, 2, 3, 45)) == time(9, 15)


def test_stale_open_boundary_is_strictly_older_than_18_hours():
    pin = utc(2026, 11, 2, 1, 0)
    assert R.is_stale_open(pin, pin + timedelta(hours=17, minutes=59, seconds=59)) is False
    assert R.is_stale_open(pin, pin + timedelta(hours=18)) is False          # exactly 18 h: not yet
    assert R.is_stale_open(pin, pin + timedelta(hours=18, seconds=1)) is True
    assert R.stale_cutoff(pin + timedelta(hours=18)) == pin


@pytest.mark.parametrize("pin_ist,start,end,expected_ist", [
    (ist(2026, 11, 2, 9, 0), time(9, 0), time(18, 0), ist(2026, 11, 2, 18, 0)),    # day shift: shift end
    (ist(2026, 11, 2, 7, 45), time(9, 0), time(18, 0), ist(2026, 11, 2, 18, 0)),   # early arrival, same shift end
    (ist(2026, 11, 2, 21, 0), time(21, 0), time(6, 0), ist(2026, 11, 3, 6, 0)),    # night shift: next morning
    (ist(2026, 11, 2, 9, 0), None, None, ist(2026, 11, 2, 18, 0)),                 # no shift: +9 h
    (ist(2026, 11, 2, 20, 0), time(9, 0), time(18, 0), ist(2026, 11, 3, 5, 0)),    # shift end 22 h away: +9 h fallback
    (ist(2026, 11, 2, 18, 0), time(9, 0), time(18, 0), ist(2026, 11, 3, 3, 0)),    # punch exactly at shift end: next end is 24 h away
])
def test_auto_close_time(pin_ist, start, end, expected_ist):
    assert R.auto_close_time(pin_ist.astimezone(UTC), start, end) == expected_ist.astimezone(UTC)


def test_session_hours_closed_open_and_stale():
    now = utc(2026, 11, 2, 10, 0)
    # closed: stored value wins, else out - in
    assert R.session_hours(utc(2026, 11, 2, 3, 0), utc(2026, 11, 2, 7, 0), 3.5, now) == 3.5
    assert R.session_hours(utc(2026, 11, 2, 3, 0), utc(2026, 11, 2, 7, 30), None, now) == 4.5
    # open: live hours until now
    assert R.session_hours(utc(2026, 11, 2, 8, 0), None, None, now) == 2.0
    # stale open (> 18 h): 0 until the auto-close
    assert R.session_hours(utc(2026, 11, 1, 10, 0) - timedelta(hours=1), None, None, now) == 0.0
    # never negative, no punch-in -> 0
    assert R.session_hours(utc(2026, 11, 2, 12, 0), utc(2026, 11, 2, 11, 0), None, now) == 0.0
    assert R.session_hours(None, utc(2026, 11, 2, 11, 0), 2.0, now) == 0.0


def test_two_sessions_total_hours_and_status():
    now = utc(2026, 11, 2, 12, 0)
    sessions = [
        {"punch_in": utc(2026, 11, 2, 3, 30), "punch_out": utc(2026, 11, 2, 7, 30), "work_hours": 4.0},
        {"punch_in": utc(2026, 11, 2, 8, 30), "punch_out": None, "work_hours": None},   # open for 3.5 h
    ]
    assert R.total_hours(sessions, now) == 7.5
    assert R.session_status(None) == "open"
    assert R.session_status(now) == "closed"
    assert R.session_status(now, True) == "auto_closed"


def test_overnight_session_is_todays_open_session_until_stale():
    # started 22:00 IST on 1 Nov (16:30 UTC), still open at 02:00 IST on 2 Nov
    overnight = {"attendance_date": date(2026, 11, 1), "punch_in": utc(2026, 11, 1, 16, 30), "punch_out": None}
    old_closed = {"attendance_date": date(2026, 11, 1), "punch_in": utc(2026, 11, 1, 4, 0), "punch_out": utc(2026, 11, 1, 8, 0)}
    now = ist(2026, 11, 2, 2, 0).astimezone(UTC)
    today = R.ist_today(now)
    assert today == date(2026, 11, 2)
    picked = R.select_today_sessions([old_closed, overnight], today, now)
    assert picked == [overnight]                                   # open overnight session yes, closed yesterday no
    # once closed it belongs to yesterday only
    closed = dict(overnight, punch_out=utc(2026, 11, 1, 22, 0))
    assert R.select_today_sessions([closed], today, now) == []
    # stale (older than 18 h): not reported as today's session
    later = utc(2026, 11, 2, 10, 31)
    assert R.select_today_sessions([overnight], R.ist_today(later), later) == []
    # today's own rows are kept and ordered by punch-in
    a = {"attendance_date": today, "punch_in": utc(2026, 11, 2, 5, 0), "punch_out": utc(2026, 11, 2, 6, 0)}
    b = {"attendance_date": today, "punch_in": utc(2026, 11, 2, 7, 0), "punch_out": None}
    assert R.select_today_sessions([b, a], today, utc(2026, 11, 2, 8, 0)) == [a, b]


def test_right_after_midnight_ist_uses_the_new_date():
    now = utc(2026, 11, 2, 18, 31)           # 00:01 IST on 3 Nov
    assert R.ist_today(now) == date(2026, 11, 3)
    yesterday_row = {"attendance_date": date(2026, 11, 2), "punch_in": utc(2026, 11, 2, 4, 0), "punch_out": utc(2026, 11, 2, 12, 0)}
    assert R.select_today_sessions([yesterday_row], R.ist_today(now), now) == []


@pytest.mark.parametrize("punch,start,end,grace,late,by", [
    (time(8, 59), time(9, 0), time(18, 0), 0, False, 0),
    (time(9, 0), time(9, 0), time(18, 0), 0, False, 0),
    (time(9, 1), time(9, 0), time(18, 0), 0, True, 1),
    (time(9, 42), time(9, 0), time(18, 0), 0, True, 42),
    (time(9, 15), time(9, 0), time(18, 0), 15, False, 0),      # inside grace
    (time(9, 16), time(9, 0), time(18, 0), 15, True, 16),      # late counted from the shift start, not the grace end
    (time(9, 0, 30), time(9, 0), time(18, 0), 0, True, 1),     # seconds count, at least 1 minute reported
    (time(20, 0), time(21, 0), time(6, 0), 0, False, 0),       # night shift: early arrival
    (time(21, 10), time(21, 0), time(6, 0), 0, True, 10),
    (time(0, 30), time(21, 0), time(6, 0), 0, True, 210),      # after midnight, still within 12 h of start
    (time(12, 0), time(21, 0), time(6, 0), 0, False, 0),       # far from the night shift start: next evening's arrival
])
def test_late_rule(punch, start, end, grace, late, by):
    assert R.is_late(punch, start, end, grace) is late
    assert R.late_by_minutes(punch, start, end, grace) == by


def test_late_rule_matches_attendance_rule_functions():
    from core_functions import attendance_rule_functions as arf
    for p in (time(8, 0), time(9, 0), time(9, 1), time(23, 59), time(0, 1), time(3, 0), time(15, 0)):
        for start, end in ((time(9, 0), time(18, 0)), (time(21, 0), time(6, 0)), (time(0, 0), time(8, 0))):
            for g in (0, 15):
                assert R.is_late(p, start, end, g) == arf.is_late(p, start, end, g)


def test_late_by_minutes_without_shift_or_punch():
    assert R.late_by_minutes(time(10, 0), None, None) == 0
    assert R.late_by_minutes(None, time(9, 0), time(18, 0)) == 0


@pytest.mark.parametrize("worked_min,start,end,half", [
    (269, time(9, 0), time(18, 0), True),      # under half of 9 h
    (270, time(9, 0), time(18, 0), False),     # exactly half is not a half day
    (0, time(9, 0), time(18, 0), False),       # nothing worked: absent/open, not half
    (0.5, time(9, 0), time(18, 0), False),     # under one minute is ignored
    (1, time(9, 0), time(18, 0), True),
    (269, None, None, True),                   # no shift: 9 h default
    (270, None, None, False),
    (269, time(0, 0), time(0, 0), True),       # unusable span: default
    (269, time(21, 0), time(6, 0), True),      # night shift span 9 h
    (270, time(21, 0), time(6, 0), False),
    (239, time(9, 0), time(17, 0), True),      # 8 h shift: half = 240
    (240, time(9, 0), time(17, 0), False),
])
def test_half_day(worked_min, start, end, half):
    assert R.is_half_day(worked_min, start, end) is half


@pytest.mark.parametrize("start,end,span", [
    (time(9, 0), time(18, 0), 540), (time(21, 0), time(6, 0), 540), (time(0, 0), time(0, 0), 0),
    (time(9, 0), time(9, 0), 1440), (None, time(9, 0), None), (time(22, 0), time(0, 0), 120),
])
def test_shift_span(start, end, span):
    assert R.shift_span_minutes(start, end) == span


# ------------------------------------------------------------------ day types (mirror of domain.js / attendanceCalendar.ts)
TODAY = date(2026, 11, 10)
NOW = ist(2026, 11, 10, 15, 0).astimezone(UTC)
S9, E18 = time(9, 0), time(18, 0)


def sess(in_ist, out_ist=None, hours=None):
    return {"punch_in": in_ist.astimezone(UTC), "punch_out": out_ist.astimezone(UTC) if out_ist else None, "work_hours": hours}


@pytest.mark.parametrize("name,day,sessions,kw,exp_type,exp_status", [
    ("full day on time", date(2026, 11, 4), [sess(ist(2026, 11, 4, 8, 55), ist(2026, 11, 4, 18, 5), 9.17)], {}, "present", "present"),
    ("late full day", date(2026, 11, 4), [sess(ist(2026, 11, 4, 9, 42), ist(2026, 11, 4, 18, 5), 8.38)], {}, "late", "late"),
    ("half day by hours", date(2026, 11, 4), [sess(ist(2026, 11, 4, 9, 0), ist(2026, 11, 4, 13, 0), 4.0)], {}, "half_day", "half_day"),
    ("half day beats late", date(2026, 11, 4), [sess(ist(2026, 11, 4, 10, 0), ist(2026, 11, 4, 13, 0), 3.0)], {}, "half_day", "half_day"),
    ("two sessions add up to a full day", date(2026, 11, 4), [sess(ist(2026, 11, 4, 9, 0), ist(2026, 11, 4, 12, 0), 3.0), sess(ist(2026, 11, 4, 13, 0), ist(2026, 11, 4, 18, 0), 5.0)], {}, "present", "present"),
    ("absent elapsed working day", date(2026, 11, 4), [], {}, "absent", "absent"),
    ("approved leave", date(2026, 11, 4), [], {"is_leave": True}, "leave", "leave"),
    ("leave with a punch counts as present", date(2026, 11, 4), [sess(ist(2026, 11, 4, 9, 0), ist(2026, 11, 4, 18, 0), 9.0)], {"is_leave": True}, "present", "present"),
    ("holiday", date(2026, 11, 4), [], {"holiday_name": "Diwali"}, "holiday", "absent"),
    ("leave beats holiday", date(2026, 11, 4), [], {"is_leave": True, "holiday_name": "Diwali"}, "leave", "leave"),
    ("weekly off", date(2026, 11, 8), [], {"is_weekly_off": True}, "weekly_off", "absent"),
    ("holiday beats weekly off", date(2026, 11, 8), [], {"holiday_name": "Diwali", "is_weekly_off": True}, "holiday", "absent"),
    ("punches on a weekly off stay present", date(2026, 11, 8), [sess(ist(2026, 11, 8, 9, 0), ist(2026, 11, 8, 18, 0), 9.0)], {"is_weekly_off": True}, "present", "present"),
    ("today without punches is upcoming, not absent", TODAY, [], {}, "upcoming", "absent"),
    ("future day", date(2026, 11, 20), [], {}, "upcoming", "absent"),
    ("future weekly off", date(2026, 11, 15), [], {"is_weekly_off": True}, "weekly_off", "absent"),
    ("open session today is present (no half day while working)", TODAY, [sess(ist(2026, 11, 10, 14, 0))], {}, "late", "late"),
    ("open stale session of a past day is present", date(2026, 11, 4), [sess(ist(2026, 11, 4, 9, 0))], {}, "present", "present"),
])
def test_day_types_table(name, day, sessions, kw, exp_type, exp_status):
    info = R.classify_day(day, TODAY, sessions, S9, E18, NOW, 0, **kw)
    assert (info["type"], info["status"]) == (exp_type, exp_status), name
    assert info["type"] in R.DAY_TYPES


def test_day_types_without_a_shift_and_night_shift():
    # no shift: never late, half-day against the 9 h default
    info = R.classify_day(date(2026, 11, 4), TODAY, [sess(ist(2026, 11, 4, 14, 0), ist(2026, 11, 4, 20, 0), 6.0)], None, None, NOW)
    assert (info["type"], info["late_by_minutes"]) == ("present", 0)
    info = R.classify_day(date(2026, 11, 4), TODAY, [sess(ist(2026, 11, 4, 14, 0), ist(2026, 11, 4, 18, 0), 4.0)], None, None, NOW)
    assert info["type"] == "half_day"
    # night shift 21:00-06:00: punch 21:20 -> late by 20; worked across midnight 8 h -> present
    pin, pout = ist(2026, 11, 4, 21, 20), ist(2026, 11, 5, 5, 20)
    info = R.classify_day(date(2026, 11, 4), TODAY, [sess(pin, pout, 8.0)], time(21, 0), time(6, 0), NOW)
    assert (info["type"], info["late_by_minutes"], info["total_hours"]) == ("late", 20, 8.0)
    # night shift, 3 h worked -> half
    info = R.classify_day(date(2026, 11, 4), TODAY, [sess(ist(2026, 11, 4, 21, 0), ist(2026, 11, 5, 0, 0), 3.0)], time(21, 0), time(6, 0), NOW)
    assert info["type"] == "half_day"


def test_lateness_uses_the_first_session_only():
    sessions = [sess(ist(2026, 11, 4, 14, 0), ist(2026, 11, 4, 18, 0), 4.0), sess(ist(2026, 11, 4, 8, 50), ist(2026, 11, 4, 12, 0), 3.0)]
    info = R.classify_day(date(2026, 11, 4), TODAY, sessions, S9, E18, NOW)
    assert info["late_by_minutes"] == 0 and info["session_count"] == 2


def test_lateness_is_ist_not_utc():
    # 03:45 UTC = 09:15 IST: late by 15 against 09:00; 03:30 UTC = 09:00 IST: on time
    late = R.classify_day(date(2026, 11, 4), TODAY, [{"punch_in": utc(2026, 11, 4, 3, 45), "punch_out": utc(2026, 11, 4, 12, 45), "work_hours": 9.0}], S9, E18, NOW)
    ok = R.classify_day(date(2026, 11, 4), TODAY, [{"punch_in": utc(2026, 11, 4, 3, 30), "punch_out": utc(2026, 11, 4, 12, 30), "work_hours": 9.0}], S9, E18, NOW)
    assert (late["status"], late["late_by_minutes"]) == ("late", 15)
    assert (ok["status"], ok["late_by_minutes"]) == ("present", 0)


def test_build_month_and_summary_counts():
    sessions_by_date = {
        date(2026, 11, 2): [sess(ist(2026, 11, 2, 9, 0), ist(2026, 11, 2, 18, 0), 9.0)],       # present
        date(2026, 11, 3): [sess(ist(2026, 11, 3, 9, 30), ist(2026, 11, 3, 18, 0), 8.5)],      # late
        date(2026, 11, 4): [sess(ist(2026, 11, 4, 9, 0), ist(2026, 11, 4, 12, 0), 3.0)],       # half
        date(2026, 11, 9): [sess(ist(2026, 11, 9, 9, 0), ist(2026, 11, 9, 18, 0), 9.0)],       # present
    }
    days = R.build_month(2026, 11, TODAY, NOW, sessions_by_date, lambda d: {"start_time": S9, "end_time": E18}, 0,
                         leave_dates=[date(2026, 11, 5)], holidays={date(2026, 11, 6): "Diwali"}, off_weekdays=[6])
    assert len(days) == 30
    by = {d["date"]: d for d in days}
    assert by[date(2026, 11, 1)]["type"] == "weekly_off"          # Sunday
    assert by[date(2026, 11, 5)]["type"] == "leave"
    assert by[date(2026, 11, 6)]["type"] == "holiday"
    assert by[date(2026, 11, 7)]["type"] == "absent"              # Saturday, elapsed, no punch
    assert by[date(2026, 11, 10)]["type"] == "upcoming"           # today, nothing yet
    assert by[date(2026, 11, 25)]["type"] == "upcoming"
    summary = R.summarize_days(days, 30)
    assert summary["present_days"] == 4 and summary["late_days"] == 1 and summary["half_days"] == 1
    assert summary["leave_days"] == 1 and summary["holiday_days"] == 1 and summary["weekly_off_days"] == 5
    assert summary["working_days"] == 24
    # elapsed working days without punches or leave: only Saturday 7 Nov (2, 3, 4, 9 punched; 5 leave; 6 holiday)
    assert summary["absent_days"] == 1


def test_build_month_asks_for_the_shift_per_date():
    asked = []

    def shift_of(d):
        asked.append(d)
        return None
    R.build_month(2026, 2, date(2026, 2, 15), NOW, {}, shift_of)
    assert asked == R.month_dates(2026, 2) and len(asked) == 28


# =============================================================================== fake cursor
class Cur:
    """Scripted cursor: `script` maps an SQL substring to {"one": row | callable, "all": rows}. First match wins."""

    def __init__(self, script=None, fail=None):
        self.script = script or []
        self.calls = []
        self.fail = fail or []
        self._sql = ""

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        self.calls.append((norm, params))
        self._sql = norm
        for needle in self.fail:
            if needle in norm:
                raise RuntimeError("scripted failure")

    def _hit(self):
        for needle, res in self.script:
            if needle in self._sql:
                return res
        return {}

    def fetchone(self):
        v = self._hit().get("one")
        return v() if callable(v) else v

    def fetchall(self):
        v = self._hit().get("all", [])
        return v() if callable(v) else v

    def close(self):
        pass

    def find(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


class Conn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def patch_db(monkeypatch, module, cur):
    conn = Conn(cur)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


# =============================================================================== punch flow
import test_face_tier_a as tfa  # noqa: E402  (installs the ML stubs and brings the upload fake)
import numpy as np  # noqa: E402

faf = tfa.faf


@pytest.fixture
def flow(monkeypatch):
    st = {
        "open": None, "cnt": 0, "record": None, "rule": None, "shift": {"start_time": time(9, 0), "end_time": time(18, 0)},
        "user": {"is_field_staff": True, "branch_id": 1, "role_id": 1, "org_id": "o1", "parent_id": "o1",
                 "branch_lng": None, "branch_lat": None, "punch_radius": 100},
    }
    script = [
        ("FROM users u", {"one": lambda: st["user"]}),
        ("SELECT id, punch_in FROM attendance", {"one": lambda: st["open"]}),
        ("FROM users_additional_info", {"one": lambda: st["shift"]}),
        ("FROM attendance_rule", {"one": lambda: st["rule"]}),       # company settings: max_sessions_per_day
        ("COUNT(*)", {"one": lambda: {"cnt": st["cnt"]}}),
        ("RETURNING id", {"one": {"id": 77}}),
        ("SELECT id, punch_in, attendance_date", {"one": lambda: st["record"]}),
        ("SUM(work_hours)", {"one": {"prev_hours": 0}}),
    ]
    cur = Cur(script)
    st["cur"] = cur
    st["conn"] = patch_db(monkeypatch, faf, cur)
    st["now"] = utc(2026, 11, 2, 19, 0)         # 00:30 IST on 3 Nov
    monkeypatch.setattr(faf, "get_utc_now", lambda: st["now"])
    monkeypatch.setattr(faf, "save_punch_image", lambda *a, **k: None)
    monkeypatch.setenv("FACE_ATTEMPT_LOG", "0")
    tfa.fs.set_backend(tfa.FakeFaceBackend(registered=True))
    monkeypatch.setattr(faf, "prepare_face", lambda b, for_enroll=False: tfa.prepared_ok())
    monkeypatch.setattr(faf, "verify_face", lambda prepared, u: {"match": True, "score": 0.9})
    import core_functions.holiday_functions as hol
    monkeypatch.setattr(hol, "check_holiday_block", lambda o, u: None)
    yield st


def punch(action="punch_in", lat=None, lng=None):
    return run(faf.punch_with_face("u1", "o1", tfa.FakeUpload(b"img"), action, lat, lng, False))


def all_sql(cur):
    return " ".join(s for s, _ in cur.calls)


def test_punch_in_uses_the_ist_business_date_not_current_date(flow):
    r = punch()
    assert r["success"] and "auto_closed_previous" not in r
    cur = flow["cur"]
    assert "CURRENT_DATE" not in all_sql(cur)
    count_sql, count_params = cur.find("COUNT(*)")[0]
    assert count_params == ("u1", date(2026, 11, 3))               # 00:30 IST: the NEW IST date, not the UTC date
    insert_sql, insert_params = cur.find("INSERT INTO attendance")[0]
    assert insert_params[2] == date(2026, 11, 3) and insert_params[3] == flow["now"]
    assert flow["conn"].commits == 1


def test_punch_in_1829_utc_is_still_the_same_ist_day(flow):
    flow["now"] = utc(2026, 11, 2, 18, 29, 59)
    assert punch()["success"]
    assert flow["cur"].find("INSERT INTO attendance")[0][1][2] == date(2026, 11, 2)


def test_advisory_lock_is_per_user(flow):
    punch()
    sql, params = flow["cur"].find("pg_advisory_xact_lock")[0]
    assert params == ("u1",) and "hashtext(%s)" in sql


def test_open_session_blocks_punch_in(flow):
    flow["open"] = {"id": 5, "punch_in": flow["now"] - timedelta(hours=17, minutes=59)}
    r = punch()
    assert (r["success"], r["code"]) == (False, "already_punched_in")
    assert not flow["cur"].find("UPDATE attendance") and not flow["cur"].find("INSERT INTO attendance")


def test_exactly_18_hours_is_not_stale(flow):
    flow["open"] = {"id": 5, "punch_in": flow["now"] - timedelta(hours=18)}
    assert punch()["code"] == "already_punched_in"


def test_stale_session_is_auto_closed_at_shift_end_then_punch_in_proceeds(flow):
    # forgot to punch out: in at 09:00 IST on 2 Nov, now 00:30 IST on 3 Nov (15.5 h) is NOT stale ...
    flow["open"] = {"id": 5, "punch_in": ist(2026, 11, 2, 9, 0).astimezone(UTC)}
    assert punch()["code"] == "already_punched_in"
    # ... at 03:01 IST it is (18 h 1 min)
    flow["now"] = ist(2026, 11, 3, 3, 1).astimezone(UTC)
    r = punch()
    assert r["success"] is True and r["auto_closed_previous"] is True
    cur = flow["cur"]
    ups = cur.find("UPDATE attendance")
    assert len(ups) == 1
    sql, params = ups[0]
    assert "auto_closed = TRUE" in sql and "punch_out IS NULL" in sql
    close_at, hours, updated, att_id = params
    assert close_at == ist(2026, 11, 2, 18, 0).astimezone(UTC)       # shift end of the day it was opened
    assert hours == 9.0 and att_id == 5 and updated == flow["now"]
    # sequence: lock, open lookup, shift lookup, UPDATE, COUNT, INSERT; one commit
    order = [s for s, _ in cur.calls]
    idx = lambda needle: next(i for i, s in enumerate(order) if needle in s)  # noqa: E731
    assert idx("pg_advisory_xact_lock") < idx("SELECT id, punch_in FROM attendance") < idx("FROM users_additional_info") \
        < idx("UPDATE attendance") < idx("COUNT(*)") < idx("INSERT INTO attendance")
    shift_params = cur.find("FROM users_additional_info")[0][1]
    assert shift_params == ("o1", "u1")                                # org scoped shift lookup
    assert flow["conn"].commits == 1


def test_stale_session_without_shift_closes_at_punch_in_plus_9h(flow):
    flow["shift"] = None
    flow["open"] = {"id": 6, "punch_in": ist(2026, 11, 2, 8, 0).astimezone(UTC)}
    flow["now"] = ist(2026, 11, 3, 3, 0).astimezone(UTC)
    r = punch()
    assert r["auto_closed_previous"] is True
    close_at, hours, _, att_id = flow["cur"].find("UPDATE attendance")[0][1]
    assert close_at == ist(2026, 11, 2, 17, 0).astimezone(UTC) and hours == 9.0 and att_id == 6


def test_session_over_the_default_limit_is_refused_but_auto_close_is_kept(flow):
    flow["open"] = {"id": 5, "punch_in": ist(2026, 11, 2, 1, 0).astimezone(UTC)}
    flow["now"] = ist(2026, 11, 2, 23, 0).astimezone(UTC)
    flow["cnt"] = 3                                     # no rule row: the default limit of 3 applies
    r = punch()
    assert r["code"] == "max_sessions" and r["message"] == "Maximum 3 punch-in session(s) allowed per day."
    assert flow["conn"].commits == 1 and not flow["cur"].find("INSERT INTO attendance")
    flow["open"] = None
    flow["conn"].commits = 0
    assert punch()["code"] == "max_sessions" and flow["conn"].commits == 0


def test_the_organisation_limit_from_company_settings_is_used(flow):
    flow["rule"] = {"max_sessions_per_day": 2}
    flow["cnt"] = 2
    r = punch()
    assert r["code"] == "max_sessions" and r["message"] == "Maximum 2 punch-in session(s) allowed per day."
    flow["rule"] = {"max_sessions_per_day": 5}          # raised in company settings: a 3rd and 4th session are fine
    flow["cnt"] = 3
    assert punch()["success"] is True
    flow["cnt"] = 5
    assert punch()["code"] == "max_sessions"


def test_second_session_same_day_is_allowed(flow):
    flow["cnt"] = 1
    assert punch()["success"] is True
    assert flow["cur"].find("INSERT INTO attendance")


def test_punch_out_of_a_stale_session_is_refused(flow):
    flow["record"] = {"id": 5, "punch_in": flow["now"] - timedelta(hours=19), "attendance_date": date(2026, 11, 2)}
    r = punch("punch_out")
    assert (r["success"], r["code"]) == (False, "no_active_punch")
    assert not flow["cur"].find("UPDATE attendance")


def test_punch_out_overnight_session_closes_it(flow):
    flow["record"] = {"id": 5, "punch_in": flow["now"] - timedelta(hours=3), "attendance_date": date(2026, 11, 2)}
    r = punch("punch_out")
    assert r["success"] and r["work_hours"] == 3.0
    sql, params = flow["cur"].find("UPDATE attendance")[0]
    assert params[-1] == 5
    assert flow["cur"].find("SUM(work_hours)")[0][1] == ("u1", date(2026, 11, 2))   # total of the day it started


def test_location_log_runs_in_a_savepoint_and_failure_does_not_lose_the_punch(flow):
    flow["cur"].fail = ["INSERT INTO staff_location_logs"]
    r = punch(lat=12.0, lng=77.0)
    assert r["success"] is True
    sqls = [s for s, _ in flow["cur"].calls]
    assert "SAVEPOINT sp_punch_location" in sqls and "ROLLBACK TO SAVEPOINT sp_punch_location" in sqls
    assert "RELEASE SAVEPOINT sp_punch_location" not in sqls
    assert flow["cur"].find("INSERT INTO attendance")      # the attendance row is still written
    assert flow["conn"].commits == 1


def test_location_log_success_releases_the_savepoint(flow):
    assert punch(lat=12.0, lng=77.0)["success"]
    sqls = [s for s, _ in flow["cur"].calls]
    assert sqls.index("SAVEPOINT sp_punch_location") < sqls.index("RELEASE SAVEPOINT sp_punch_location")


# =============================================================================== today / sessions
def row(att_date, pin, pout=None, hours=None, aid=1, **extra):
    return dict({"id": aid, "attendance_date": att_date, "punch_in": pin, "punch_out": pout, "work_hours": hours,
                 "punchinface": None, "punchoutface": None, "auto_closed": False}, **extra)


def test_today_right_after_midnight_ist(monkeypatch):
    now = utc(2026, 11, 2, 18, 31)                     # 00:01 IST on 3 Nov
    monkeypatch.setattr(faf, "get_utc_now", lambda: now)
    cur = Cur([("FROM attendance", {"all": []})])
    patch_db(monkeypatch, faf, cur)
    res = run(faf.get_today_attendance("u1"))
    sql, params = cur.find("FROM attendance")[0]
    assert "CURRENT_DATE" not in sql
    assert params[0] == "u1" and params[1] == date(2026, 11, 3) and params[2] == now - timedelta(hours=18)
    assert res["date"] == "2026-11-03" and res["sessions"] == [] and res["is_punched_in"] is False and res["punch_count"] == 0


def test_today_reports_overnight_open_session_and_two_sessions(monkeypatch):
    now = ist(2026, 11, 3, 2, 0).astimezone(UTC)       # 02:00 IST on 3 Nov
    monkeypatch.setattr(faf, "get_utc_now", lambda: now)
    overnight = row(date(2026, 11, 2), ist(2026, 11, 2, 22, 0).astimezone(UTC), None, None, aid=1)
    cur = Cur([("FROM attendance", {"all": [overnight]})])
    patch_db(monkeypatch, faf, cur)
    res = run(faf.get_today_attendance("u1"))
    assert res["is_punched_in"] is True and res["punch_in"] == "10:00:00 PM" and res["punch_out"] is None
    assert res["punch_count"] == 1 and res["sessions"][0]["punch_in"] == "10:00:00 PM"
    # a day with two sessions: the first closed, the second open
    now2 = ist(2026, 11, 3, 15, 0).astimezone(UTC)
    monkeypatch.setattr(faf, "get_utc_now", lambda: now2)
    a = row(date(2026, 11, 3), ist(2026, 11, 3, 9, 0).astimezone(UTC), ist(2026, 11, 3, 12, 0).astimezone(UTC), 3.0, aid=2)
    b = row(date(2026, 11, 3), ist(2026, 11, 3, 13, 0).astimezone(UTC), None, None, aid=3)
    cur2 = Cur([("FROM attendance", {"all": [b, a]})])
    patch_db(monkeypatch, faf, cur2)
    res = run(faf.get_today_attendance("u1"))
    assert [s["punch_in"] for s in res["sessions"]] == ["09:00:00 AM", "01:00:00 PM"]
    assert res["is_punched_in"] is True and res["punch_out"] == "12:00:00 PM" and res["total_work_hours"] == 3.0


def test_today_error_is_a_generic_500(monkeypatch):
    cur = Cur(fail=["FROM attendance"])
    patch_db(monkeypatch, faf, cur)
    with pytest.raises(HTTPException) as e:
        run(faf.get_today_attendance("u1"))
    assert e.value.status_code == 500 and "scripted" not in e.value.detail


def test_sessions_endpoint_ist_work_hours_and_status(monkeypatch):
    import ml.s3 as s3
    monkeypatch.setattr(s3, "refresh_punch_image_url", lambda u: u)
    now = utc(2026, 11, 2, 12, 0)
    monkeypatch.setattr(faf, "get_utc_now", lambda: now)
    rows = [
        {"punch_in": utc(2026, 11, 2, 3, 45), "punch_out": utc(2026, 11, 2, 7, 30), "work_hours": 3.75,
         "punchinface": "in.png", "punchoutface": None, "auto_closed": False},
        {"punch_in": utc(2026, 11, 2, 8, 0), "punch_out": None, "work_hours": None,
         "punchinface": None, "punchoutface": None, "auto_closed": False},
        {"punch_in": utc(2026, 11, 1, 3, 0), "punch_out": utc(2026, 11, 1, 12, 0), "work_hours": 9.0,
         "punchinface": None, "punchoutface": None, "auto_closed": True},
    ]
    cur = Cur([("FROM attendance", {"all": rows})])
    patch_db(monkeypatch, faf, cur)
    res = run(faf.get_attendance_sessions("u1", "2026-11-02"))
    assert cur.find("FROM attendance")[0][1] == ("u1", date(2026, 11, 2))
    s = res["sessions"]
    assert res["success"] is True and res["date"] == "2026-11-02"
    assert (s[0]["punch_in"], s[0]["punch_out"], s[0]["work_hours"], s[0]["status"]) == ("09:15:00 AM", "01:00:00 PM", 3.75, "closed")
    assert (s[1]["punch_out"], s[1]["work_hours"], s[1]["status"]) == (None, 4.0, "open")      # live 08:00Z -> 12:00Z
    assert s[2]["status"] == "auto_closed" and s[2]["auto_closed"] is True
    assert s[0]["punch_in_photo"] == "in.png"


def test_sessions_endpoint_errors_use_real_status_codes(monkeypatch):
    with pytest.raises(HTTPException) as e:
        run(faf.get_attendance_sessions("u1", "02-11-2026"))
    assert e.value.status_code == 400
    cur = Cur(fail=["FROM attendance"])
    patch_db(monkeypatch, faf, cur)
    with pytest.raises(HTTPException) as e:
        run(faf.get_attendance_sessions("u1", "2026-11-02"))
    assert e.value.status_code == 500 and "scripted" not in e.value.detail


# =============================================================================== monthly attendance
from core_functions import attendance_functions as af  # noqa: E402


def month_cur(rows, *, shift=(S9, E18), grace=0, leaves=(), holidays=(), off_days=None, branch="HQ", dept="Ops", user=True):
    return Cur([
        ("FROM users u", {"one": {"org_id": "o1", "branch_name": branch, "dept_name": dept} if user else None}),
        ("FROM users_additional_info", {"one": {"start_time": shift[0], "end_time": shift[1]} if shift else None}),
        ("FROM attendance_rule", {"one": {"grace_minutes": grace}}),
        ("FROM attendance a", {"all": rows}),
        ("FROM leave_dates", {"all": [{"leave_date": d} for d in leaves]}),
        ("FROM org_holidays", {"all": [{"holiday_date": d, "holiday_name": n} for d, n in holidays]}),
        ("FROM org_off_days", {"one": {"off_days": off_days} if off_days is not None else None}),
    ])


def att_row(att_date, pin_utc, pout_utc=None, hours=None, lat=None, lng=None, aid=1):
    return {"attendance_date": att_date, "punch_in": pin_utc, "punch_out": pout_utc, "work_hours": hours, "lng": lng, "lat": lat,
            "punchinface": None, "punchoutface": None, "auto_closed": False}


@pytest.fixture
def monthly(monkeypatch):
    import ml.s3 as s3
    monkeypatch.setattr(s3, "refresh_punch_image_url", lambda u: u)
    monkeypatch.setattr(af, "get_utc_now", lambda: utc(2026, 11, 10, 9, 30))      # 15:00 IST on 10 Nov
    return monkeypatch


def test_monthly_v1_shape_ist_lateness_and_additive_fields(monthly):
    rows = [
        att_row(date(2026, 11, 3), utc(2026, 11, 3, 3, 45), utc(2026, 11, 3, 12, 30), 8.75, lat=12.97, lng=77.59),   # 09:15 IST: late
        att_row(date(2026, 11, 4), utc(2026, 11, 4, 3, 30), utc(2026, 11, 4, 12, 30), 9.0),                          # 09:00 IST: on time
        att_row(date(2026, 11, 5), utc(2026, 11, 5, 3, 30), utc(2026, 11, 5, 6, 30), 3.0),                           # first session
        att_row(date(2026, 11, 5), utc(2026, 11, 5, 7, 30), utc(2026, 11, 5, 12, 30), 5.0),                          # second session
    ]
    cur = month_cur(rows)
    patch_db(monthly, af, cur)
    res = run(af.get_monthly_attendance("u1", "o1", 11, 2026))
    data = res.model_dump()
    assert set(data) == {"month", "year", "total_days", "present_days", "absent_days", "late_days", "attendance_data"}
    assert (res.total_days, res.present_days, res.late_days, res.absent_days) == (30, 3, 1, 27)
    d3, d4, d5 = res.attendance_data
    assert (d3.date, d3.status, d3.punch_in, d3.punch_out, d3.total_hours) == ("2026-11-03", "late", "09:15 AM", "06:00 PM", "8.75")
    assert (d3.shift_start_time, d3.shift_end_time, d3.late_by_minutes, d3.session_count, d3.location) == ("09:00", "18:00", 15, 1, "12.97,77.59")
    assert (d4.status, d4.late_by_minutes) == ("present", 0)
    assert (d5.punch_in, d5.punch_out, d5.total_hours, d5.session_count) == ("09:00 AM", "06:00 PM", "8.00", 2)
    # SQL: explicit date range, org scoped shift lookup, no CURRENT_DATE, no UTC time cast
    sql, params = cur.find("FROM attendance a")[0]
    assert "attendance_date >= %s AND a.attendance_date < %s" in sql and "CURRENT_DATE" not in sql
    assert params == ("u1", date(2026, 11, 1), date(2026, 12, 1))
    assert cur.find("FROM users_additional_info")[0][1] == ("o1", "u1")


def test_monthly_v1_open_second_session_has_no_punch_out(monthly):
    rows = [att_row(date(2026, 11, 10), utc(2026, 11, 10, 3, 30), utc(2026, 11, 10, 6, 30), 3.0),
            att_row(date(2026, 11, 10), utc(2026, 11, 10, 7, 30))]
    patch_db(monthly, af, month_cur(rows))
    d = run(af.get_monthly_attendance("u1", "o1", 11, 2026)).attendance_data[0]
    assert d.punch_out is None and d.total_hours == "5.00"            # 3.0 + live 2.0 h (07:30Z -> 09:30Z)


def test_monthly_v1_grace_comes_from_the_attendance_rule(monthly):
    rows = [att_row(date(2026, 11, 3), utc(2026, 11, 3, 3, 45), utc(2026, 11, 3, 12, 30), 8.75)]   # 09:15 IST
    patch_db(monthly, af, month_cur(rows, grace=15))
    res = run(af.get_monthly_attendance("u1", "o1", 11, 2026))
    assert res.late_days == 0 and res.attendance_data[0].status == "present"


def test_monthly_without_shift_is_never_late(monthly):
    rows = [att_row(date(2026, 11, 3), utc(2026, 11, 3, 8, 0), utc(2026, 11, 3, 12, 30), 4.5)]
    patch_db(monthly, af, month_cur(rows, shift=None))
    d = run(af.get_monthly_attendance("u1", "o1", 11, 2026)).attendance_data[0]
    assert d.status == "present" and d.shift_start_time is None and d.late_by_minutes == 0


def test_monthly_v2_every_day_with_day_types(monthly):
    rows = [
        att_row(date(2026, 11, 2), utc(2026, 11, 2, 3, 30), utc(2026, 11, 2, 12, 30), 9.0),     # present
        att_row(date(2026, 11, 3), utc(2026, 11, 3, 4, 12), utc(2026, 11, 3, 12, 30), 8.3),     # 09:42 late
        att_row(date(2026, 11, 4), utc(2026, 11, 4, 3, 30), utc(2026, 11, 4, 6, 30), 3.0),      # half
        att_row(date(2026, 11, 9), utc(2026, 11, 9, 3, 30), utc(2026, 11, 9, 6, 30), 3.0),      # first session
        att_row(date(2026, 11, 9), utc(2026, 11, 9, 7, 30), utc(2026, 11, 9, 12, 30), 5.0),     # second: full day together
    ]
    cur = month_cur(rows, leaves=[date(2026, 11, 5)], holidays=[(date(2026, 11, 6), "Diwali")], off_days=[6])
    patch_db(monthly, af, cur)
    res = run(af.get_monthly_attendance("u1", "o1", 11, 2026, "v2"))
    assert isinstance(res, dict) and res["success"] is True and res["as_of"] == "2026-11-10"
    days = {d["date"]: d for d in res["attendance_data"]}
    assert len(days) == 30
    assert days["2026-11-01"]["type"] == "weekly_off" and days["2026-11-01"]["is_weekly_off"] is True
    assert days["2026-11-02"]["type"] == "present" and days["2026-11-02"]["sessions"][0]["status"] == "closed"
    d3 = days["2026-11-03"]
    assert (d3["type"], d3["status"], d3["late_by_minutes"], d3["shift_start_time"], d3["shift_end_time"]) == ("late", "late", 42, "09:00", "18:00")
    assert days["2026-11-04"]["type"] == "half_day" and days["2026-11-04"]["status"] == "half_day"
    assert days["2026-11-05"]["type"] == "leave" and days["2026-11-05"]["status"] == "leave"
    assert days["2026-11-06"]["type"] == "holiday" and days["2026-11-06"]["holiday_name"] == "Diwali" and days["2026-11-06"]["status"] == "absent"
    assert days["2026-11-07"]["type"] == "absent"
    d9 = days["2026-11-09"]
    assert d9["type"] == "present" and d9["session_count"] == 2 and d9["total_hours"] == "8.00" and len(d9["sessions"]) == 2
    assert days["2026-11-10"]["type"] == "upcoming" and days["2026-11-20"]["type"] == "upcoming"
    assert "holiday_name" not in days["2026-11-07"]
    assert (res["total_days"], res["present_days"], res["late_days"], res["half_days"], res["leave_days"],
            res["holiday_days"], res["weekly_off_days"], res["working_days"]) == (30, 4, 1, 1, 1, 1, 5, 24)
    assert res["absent_days"] == 1                                       # only Saturday 7 Nov
    # holiday lookup scoped to org + the staff member's branch / department; leave must be approved
    hsql, hparams = cur.find("FROM org_holidays")[0]
    assert hparams == ("o1", date(2026, 11, 1), date(2026, 12, 1), "HQ", "Ops")
    assert "h.branch_name IS NULL OR h.branch_name = %s" in hsql and "h.dept_name IS NULL OR h.dept_name = %s" in hsql
    lsql, lparams = cur.find("FROM leave_dates")[0]
    assert "= 'approved'" in lsql and "r.request_type = 'leave'" in lsql and lparams[:2] == ("u1", "o1")


def test_monthly_v2_default_weekly_off_is_sunday_and_future_month_is_all_upcoming(monthly):
    patch_db(monthly, af, month_cur([]))
    res = run(af.get_monthly_attendance("u1", "o1", 12, 2026, "v2"))
    types = {d["date"]: d["type"] for d in res["attendance_data"]}
    assert types["2026-12-06"] == "weekly_off" and types["2026-12-07"] == "upcoming"
    assert res["absent_days"] == 0 and res["present_days"] == 0


def test_monthly_v2_missing_optional_tables_fall_back_without_aborting(monthly):
    cur = month_cur([])
    cur.fail = ["FROM org_off_days", "FROM attendance_rule"]
    patch_db(monthly, af, cur)
    res = run(af.get_monthly_attendance("u1", "o1", 11, 2026, "v2"))
    assert res["weekly_off_days"] == 5                                    # default [6]
    sqls = [s for s, _ in cur.calls]
    assert "ROLLBACK TO SAVEPOINT sp_att_offdays" in sqls and "ROLLBACK TO SAVEPOINT sp_att_grace" in sqls


def test_monthly_errors_are_real_status_codes(monthly):
    patch_db(monthly, af, month_cur([], user=False))
    with pytest.raises(HTTPException) as e:
        run(af.get_monthly_attendance("nobody", "o1", 11, 2026))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        run(af.get_monthly_attendance("u1", "o1", 13, 2026))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        run(af.get_monthly_attendance("u1", "o1", 11, 2026, "v3"))
    assert e.value.status_code == 422
    cur = month_cur([])
    cur.fail = ["FROM attendance a"]
    patch_db(monthly, af, cur)
    with pytest.raises(HTTPException) as e:
        run(af.get_monthly_attendance("u1", "o1", 11, 2026))
    assert e.value.status_code == 500 and "scripted" not in e.value.detail


def test_monthly_route_detail_param(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from router import home as home_router
    seen = []

    async def fake(staff_id, org_id, month, year, detail=None):
        seen.append(detail)
        return {"success": True, "attendance_data": []} if detail == "v2" else af.MonthlyAttendance(
            month=month, year=year, total_days=30, present_days=0, absent_days=30, late_days=0, attendance_data=[])

    monkeypatch.setattr(home_router, "get_monthly_attendance", fake)
    app = FastAPI()
    app.include_router(home_router.router)
    c = TestClient(app)
    q = "/home/monthly-attendance?staff_id=u1&org_id=o1&month=11&year=2026"
    legacy = c.get(q).json()
    assert set(legacy) == {"month", "year", "total_days", "present_days", "absent_days", "late_days", "attendance_data"}
    assert c.get(q + "&detail=v2").json() == {"success": True, "attendance_data": []}
    assert seen == [None, "v2"]


# =============================================================================== daily report
def test_daily_report_aggregates_two_sessions(monkeypatch):
    from core_functions import manager_functions as mf

    async def scope(org):
        return org, None, None
    monkeypatch.setattr(mf, "get_manager_scope", scope)
    base = {"user_id": "u1", "name": "A", "emp_id": "E1", "mobile_number": "1", "email": "a@x", "dept_name": "Ops",
            "branch_name": "HQ", "latitude": None, "longitude": None}
    working = dict(base, punch_in=utc(2026, 11, 2, 3, 30), punch_out=utc(2026, 11, 2, 6, 30), has_open=True, work_hours=3.0)
    done = dict(base, user_id="u2", punch_in=utc(2026, 11, 2, 3, 30), punch_out=utc(2026, 11, 2, 12, 30), has_open=False, work_hours=9.0)
    cur = Cur([("FROM users u", {"all": [working, done]})])
    patch_db(monkeypatch, af, cur)
    res = run(af.get_daily_attendance("o1", "2026-11-02"))
    assert (res[0].status, res[0].punch_out, res[0].total_hours) == ("Working", None, None)
    assert (res[1].status, res[1].punch_out, res[1].total_hours) == ("Punched Out", "06:00 PM", "9.0")
    sql, params = cur.find("FROM users u")[0]
    assert "bool_or(punch_in IS NOT NULL AND punch_out IS NULL)" in sql and params[0] == date(2026, 11, 2)
    with pytest.raises(HTTPException) as e:
        run(af.get_daily_attendance("o1", "02/11/2026"))
    assert e.value.status_code == 400                                     # was swallowed into a 500 before


# =============================================================================== admin edit
def test_edit_attendance_has_no_on_conflict_and_edits_the_chosen_session(monkeypatch):
    two = [{"id": 11, "punch_in": utc(2026, 11, 2, 3, 30), "punch_out": utc(2026, 11, 2, 6, 30)},
           {"id": 12, "punch_in": utc(2026, 11, 2, 7, 30), "punch_out": None}]
    cur = Cur([("FROM attendance", {"all": two})])
    conn = patch_db(monkeypatch, af, cur)
    req = af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_out="18:00", session=2)
    res = run(af.edit_attendance(req))
    assert res["success"] and res["message"] == "Attendance updated successfully" and res["work_hours"] == 5.0
    assert "ON CONFLICT" not in all_sql(cur)
    sql, params = cur.find("UPDATE attendance")[0]
    assert params[-1] == 12 and params[0] == utc(2026, 11, 2, 7, 30) and params[1] == utc(2026, 11, 2, 12, 30)
    assert "auto_closed = CASE WHEN %s THEN FALSE ELSE auto_closed END" in sql and params[4] is True
    assert conn.commits == 1
    # session defaults to 1 and the other session is untouched
    cur2 = Cur([("FROM attendance", {"all": two})])
    patch_db(monkeypatch, af, cur2)
    run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_in="09:30")))
    assert cur2.find("UPDATE attendance")[0][1][-1] == 11


def test_edit_attendance_creates_a_missing_day_or_second_session_without_upsert(monkeypatch):
    cur = Cur([("FROM attendance", {"all": []})])
    patch_db(monkeypatch, af, cur)
    res = run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_in="09:00", punch_out="18:00")))
    assert res["message"] == "Attendance record created successfully"
    sql, params = cur.find("INSERT INTO attendance")[0]
    assert "ON CONFLICT" not in sql and params[2] == date(2026, 11, 2)
    one = [{"id": 11, "punch_in": utc(2026, 11, 2, 3, 30), "punch_out": utc(2026, 11, 2, 6, 30)}]
    cur2 = Cur([("FROM attendance", {"all": one})])
    patch_db(monkeypatch, af, cur2)
    run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_in="13:00", punch_out="18:00", session=2)))
    assert cur2.find("INSERT INTO attendance") and not cur2.find("UPDATE attendance")


def test_edit_attendance_follows_the_organisation_limit(monkeypatch):
    two = [{"id": 11, "punch_in": utc(2026, 11, 2, 3, 30), "punch_out": utc(2026, 11, 2, 6, 30)},
           {"id": 12, "punch_in": utc(2026, 11, 2, 7, 30), "punch_out": utc(2026, 11, 2, 9, 30)}]
    req = lambda **kw: af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_in="13:00", punch_out="18:00", **kw)  # noqa: E731
    patch_db(monkeypatch, af, Cur([("FROM attendance_rule", {"one": {"max_sessions_per_day": 2}}), ("FROM attendance", {"all": two})]))
    with pytest.raises(HTTPException) as e:
        run(af.edit_attendance(req(session=3)))
    assert e.value.status_code == 422 and "between 1 and 2" in e.value.detail
    cur = Cur([("FROM attendance_rule", {"one": {"max_sessions_per_day": 5}}), ("FROM attendance", {"all": two})])
    patch_db(monkeypatch, af, cur)
    assert run(af.edit_attendance(req(session=3)))["message"] == "Attendance record created successfully"
    assert cur.find("INSERT INTO attendance")


def test_edit_attendance_validation(monkeypatch):
    patch_db(monkeypatch, af, Cur([("FROM attendance", {"all": []})]))
    for kwargs, status in (
        ({"session": 4, "punch_in": "09:00"}, 422),            # over the default daily limit of 3
        ({"session": 0, "punch_in": "09:00"}, 422),
        ({"session": 3, "punch_in": "09:00"}, 400),            # allowed number, but sessions 1 and 2 must exist first
        ({"session": 2, "punch_in": "09:00"}, 400),            # session 1 must exist first
        ({}, 400),                                             # nothing to change
    ):
        with pytest.raises(HTTPException) as e:
            run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", **kwargs)))
        assert e.value.status_code == status
    with pytest.raises(HTTPException) as e:
        run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="bad", punch_in="09:00")))
    assert e.value.status_code == 422


def test_edit_attendance_second_open_session_conflict_is_409(monkeypatch):
    import psycopg2.errors
    cur = Cur([("FROM attendance", {"all": []})])
    orig = cur.execute

    def execute(sql, params=None):
        orig(sql, params)
        if "INSERT INTO attendance" in sql:
            raise psycopg2.errors.UniqueViolation()
    cur.execute = execute
    conn = patch_db(monkeypatch, af, cur)
    with pytest.raises(HTTPException) as e:
        run(af.edit_attendance(af.EditAttendanceRequest(staff_id="u1", org_id="o1", date="2026-11-02", punch_in="09:00")))
    assert e.value.status_code == 409 and conn.rollbacks >= 1


# =============================================================================== staff location
from core_functions import staff_location_functions as sl  # noqa: E402


def loc_req():
    return sl.LocationLogRequest(user_id="u1", org_id="o1", latitude=0.0, longitude=0.0)


def loc_cur(sessions):
    return Cur([("FROM users u", {"one": {"user_id": "u1", "is_field_staff": 1, "role_id": 3}}),
                ("FROM attendance", {"all": sessions})])


def test_location_log_uses_ist_date_and_open_overnight_session(monkeypatch):
    now = ist(2026, 11, 3, 2, 0).astimezone(UTC)
    monkeypatch.setattr(sl, "get_utc_now", lambda: now)
    overnight = {"punch_in": ist(2026, 11, 2, 22, 0).astimezone(UTC), "punch_out": None}
    cur = loc_cur([overnight])
    conn = patch_db(monkeypatch, sl, cur)
    assert run(sl.log_staff_location(loc_req()))["success"] is True      # (0, 0) is accepted
    sql, params = cur.find("FROM attendance")[0]
    assert "CURRENT_DATE" not in sql and params == ("u1", date(2026, 11, 3), now - timedelta(hours=18))
    assert conn.commits == 1


def test_location_log_403_messages(monkeypatch):
    now = ist(2026, 11, 3, 15, 0).astimezone(UTC)
    monkeypatch.setattr(sl, "get_utc_now", lambda: now)
    closed = {"punch_in": ist(2026, 11, 3, 9, 0).astimezone(UTC), "punch_out": ist(2026, 11, 3, 12, 0).astimezone(UTC)}
    openish = {"punch_in": ist(2026, 11, 3, 13, 0).astimezone(UTC), "punch_out": None}
    patch_db(monkeypatch, sl, loc_cur([]))
    with pytest.raises(HTTPException) as e:
        run(sl.log_staff_location(loc_req()))
    assert (e.value.status_code, e.value.detail) == (403, "Location tracking only active after punch in")
    patch_db(monkeypatch, sl, loc_cur([closed]))
    with pytest.raises(HTTPException) as e:
        run(sl.log_staff_location(loc_req()))
    assert (e.value.status_code, e.value.detail) == (403, "Location tracking stopped after punch out")
    patch_db(monkeypatch, sl, loc_cur([openish, closed]))                 # session 1 closed, session 2 open: accepted
    assert run(sl.log_staff_location(loc_req()))["success"] is True


def test_location_queries_use_ist_day_and_one_row_per_staff(monkeypatch):
    monkeypatch.setattr(sl, "get_utc_now", lambda: utc(2026, 11, 2, 19, 0))     # 00:30 IST on 3 Nov
    cur = Cur([])
    patch_db(monkeypatch, sl, cur)
    run(sl.get_staff_locations("o1"))
    sql, params = cur.find("latest_locations")[0]
    assert "DATE((sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = %s" in sql
    assert "LEFT JOIN attendance a" not in sql and "MIN(punch_in) AS first_in" in sql
    assert "array_agg(punch_out ORDER BY punch_in DESC))[1] AS last_out" in sql
    assert "AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM'" in sql
    assert params[1] == "2026-11-03" and params[4] == "2026-11-03"          # both date params are the IST date
    cur2 = Cur([])
    patch_db(monkeypatch, sl, cur2)
    run(sl.get_staff_location_stats("o1"))
    sql2, params2 = cur2.find("latest_locations")[0]
    assert "(sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata'" in sql2 and params2[1] == "2026-11-03"
    cur3 = Cur([])
    patch_db(monkeypatch, sl, cur3)
    run(sl.get_user_location_history("u1"))
    sql3, params3 = cur3.find("staff_location_logs")[0]
    assert "(sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata'" in sql3 and params3[2] == "2026-11-03" and params3[1] == "2026-10-27"


def test_home_location_queries_aggregate_sessions_in_ist(monkeypatch):
    from core_functions import home_functions as hf
    cur = Cur([])
    patch_db(monkeypatch, hf, cur)
    run(hf.get_staff_locations("o1", "2026-11-02"))
    sql, _ = cur.find("latest_locations")[0]
    assert "(sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata'" in sql and "LEFT JOIN attendance a" not in sql and "GROUP BY user_id" in sql
    cur2 = Cur([])
    patch_db(monkeypatch, hf, cur2)
    run(hf.get_staff_punch_locations("o1", "2026-11-02"))
    sql2, _ = cur2.find("punch_in_lat")[0]
    assert "INNER JOIN attendance a" not in sql2 and "GROUP BY user_id" in sql2
    assert "(array_agg(punch_in_location ORDER BY punch_in ASC))[1]" in sql2 and "AT TIME ZONE 'Asia/Kolkata'" in sql2


# =============================================================================== dashboard lateness
def test_dashboard_lateness_is_ist_first_punch_and_counts_a_user_once(monkeypatch):
    from core_functions import home_functions as hf
    from core_functions import manager_functions as mf

    async def scope(uid):
        return "o1", None, None
    monkeypatch.setattr(mf, "get_manager_scope", scope)
    late_rows = [
        {"user_id": "a", "first_in": time(9, 15), "start_time": S9, "end_time": E18},        # late
        {"user_id": "b", "first_in": time(8, 55), "start_time": S9, "end_time": E18},        # on time
        {"user_id": "c", "first_in": time(21, 30), "start_time": time(21, 0), "end_time": time(6, 0)},   # night shift late
        {"user_id": "c", "first_in": time(21, 30), "start_time": time(21, 0), "end_time": time(6, 0)},   # duplicate shift row
    ]
    cur = Cur([
        ("COUNT(*) FILTER", {"one": {"punched_in": 2, "punched_out": 1, "total_attendance": 3}}),
        ("SELECT COUNT(*) as count FROM users", {"one": {"count": 5}}),
        ("(MIN(a.punch_in) AT TIME ZONE 'Asia/Kolkata')::time", {"all": late_rows}),
    ])
    patch_db(monkeypatch, hf, cur)
    res = run(hf.get_dashboard_status("admin1", None, "2026-11-02"))
    assert (res.total_staff, res.punched_in, res.working, res.punched_out, res.absent, res.late_punched_in) == (5, 2, 2, 1, 2, 2)
    sqls = all_sql(cur)
    assert "::time >" not in sqls and "CURRENT_DATE" not in sqls
    assert "bool_or(a.punch_in IS NOT NULL AND a.punch_out IS NULL)" in sqls


def test_department_late_counts_group_by_department(monkeypatch):
    from core_functions import home_functions as hf
    rows = [{"dept_id": "d1", "user_id": "a", "first_in": time(10, 0), "start_time": S9, "end_time": E18},
            {"dept_id": "d1", "user_id": "b", "first_in": time(9, 30), "start_time": S9, "end_time": E18},
            {"dept_id": "d2", "user_id": "c", "first_in": time(8, 0), "start_time": S9, "end_time": E18}]
    assert hf._count_late(rows, 0, key="dept_id") == {"d1": 2}
    assert hf._count_late(rows, 45, key="dept_id") == {"d1": 1}      # grace 45 min: only 10:00 is late
    assert hf._count_late(rows, 0) == 2


def test_staff_list_queries_pass_an_explicit_ist_date(monkeypatch):
    from core_functions import home_functions as hf
    from core_functions import manager_functions as mf
    monkeypatch.setattr(hf, "get_utc_now", lambda: utc(2026, 11, 2, 19, 0))       # 00:30 IST on 3 Nov

    async def scope(uid):
        return "o1", None, None
    monkeypatch.setattr(mf, "get_manager_scope", scope)
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda u: None)
    for fn, args in ((hf.get_staff_list, ("o1",)), (hf.get_all_staff, ("o1",))):
        cur = Cur([])
        patch_db(monkeypatch, hf, cur)
        run(fn(*args))
        sql, params = cur.find("LEFT JOIN LATERAL")[0]
        assert "CURRENT_DATE" not in sql
        assert params[0] == date(2026, 11, 3) and params[2] == "o1"
    cur = Cur([("FROM users u", {"one": {"user_id": "u1", "name": "A", "emp_id": "E", "mobile_number": "1", "email": "a@x",
                                         "branch_id": "1", "dept_id": "d", "org_id": "o1", "dept_name": "Ops", "branch_name": "HQ",
                                         "branch_address": "x", "punch_in": None, "punch_out": None, "attendance_date": None}})])
    patch_db(monkeypatch, hf, cur)
    run(hf.get_staff_detail("u1", "o1"))
    sql, params = cur.find("FROM users u")[0]
    assert "CURRENT_DATE" not in sql and params[:2] == ("o1", "o1") and params[2] == date(2026, 11, 3) and params[4:] == ("u1", "o1")


# =============================================================================== schema artefacts
def test_ddl_and_migration_028_replace_the_stale_unique_index():
    ddl = (ROOT / "clockyn_ddl_latest.sql").read_text(encoding="utf-8")
    mig = (ROOT / "migrations" / "028_attendance_sessions.sql").read_text(encoding="utf-8")
    assert not re.search(r"^CREATE UNIQUE INDEX attendance_user_id_attendance_date_key", ddl, re.M)
    assert "ux_attendance_one_open ON clockyn_dev.attendance USING btree (user_id) WHERE (punch_in IS NOT NULL AND punch_out IS NULL)" in ddl
    assert "auto_closed bool DEFAULT false NOT NULL" in ddl
    assert "DROP INDEX IF EXISTS attendance_user_id_attendance_date_key;" in mig
    assert "DROP CONSTRAINT attendance_user_id_attendance_date_key" in mig
    assert "ADD COLUMN IF NOT EXISTS auto_closed boolean NOT NULL DEFAULT false" in mig
    assert "CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_one_open" in mig
    assert "RAISE EXCEPTION" in mig and "clockyn_dev" not in mig
    assert not re.search(r"^\s*DELETE\s+FROM|^\s*TRUNCATE|DROP TABLE", mig, re.M | re.I)
    assert mig.index("RAISE EXCEPTION") < mig.index("DROP INDEX IF EXISTS")      # pre-check runs before anything changes
