"""Shared builders for the payroll tests (not a test module). October 2026 is the reference month:
31 days, Thursday 1 October, Sundays on the 4th / 11th / 18th / 25th (27 working days with the default Sunday off).
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.timezone import IST, UTC  # noqa: E402

YEAR, MONTH = 2026, 10
# A date after the month: every day of October is "elapsed" (absent days are counted).
NOW = datetime(2026, 11, 15, 6, 0, tzinfo=UTC)
TODAY = date(2026, 11, 15)
SUNDAYS = (4, 11, 18, 25)
DAY_SHIFT = {"start_time": datetime(2000, 1, 1, 9, 0).time(), "end_time": datetime(2000, 1, 1, 18, 0).time()}
NIGHT_SHIFT = {"start_time": datetime(2000, 1, 1, 21, 0).time(), "end_time": datetime(2000, 1, 1, 6, 0).time()}
# 22 working days (no Sunday): 1,2,3, 5-10, 12-17, 19-24, 26
WORK_DAYS = [d for d in range(1, 32) if d not in SUNDAYS][:22]

PAYSLIP = {"basic_pay": 30000, "hra": 10000, "epf": 1800, "professional_tax": 200, "health_insurance": 500}


def at(day, hm, month=MONTH, year=YEAR):
    """IST wall-clock `HH:MM` of `day` as an aware datetime."""
    h, m = map(int, hm.split(":"))
    return datetime(year, month, day, h, m, tzinfo=IST)


def session(day, in_hm="09:00", hours=9.0, auto_closed=False, open_=False):
    """One session starting at `in_hm` IST on `day` lasting `hours` (closed unless `open_`)."""
    pin = at(day, in_hm)
    if open_:
        return {"punch_in": pin, "punch_out": None, "work_hours": None, "auto_closed": auto_closed}
    return {"punch_in": pin, "punch_out": pin + timedelta(hours=hours), "work_hours": hours,
            "auto_closed": auto_closed}


def month_sessions(days=WORK_DAYS, in_hm="09:00", hours=9.0):
    """{date: [session]} for `days` of October 2026."""
    return {date(YEAR, MONTH, d): [session(d, in_hm, hours)] for d in days}


def add_session(by_date, day, **kw):
    by_date.setdefault(date(YEAR, MONTH, day), []).append(session(day, **kw))
    return by_date


def engine_input(**over):
    """Input of `payroll_engine.compute_salary`: no payslip, no wages, no attendance, day shift."""
    inp = {
        "user_id": "u1", "year": YEAR, "month": MONTH, "now": NOW, "today": TODAY,
        "payslip": None, "custom_earnings": [], "custom_deductions": [],
        "sessions_by_date": {}, "shift": DAY_SHIFT, "grace_minutes": 0, "leave_dates": [], "holidays": {},
        "off_weekdays": [6], "rule": None,
        "daily_rate": None, "hourly_rate": None, "incentives": [],
        "odometer": [], "org_allowance_rate": None, "default_odometer_rate": 3,
        "travel_amount": 0.0, "others_amount": 0.0, "advance_amount": 0.0,
    }
    inp.update(over)
    return inp


def overtime_template(duration=30, amount=50, user_id=None):
    return {"type": "OVERTIME", "incentive_id": "ot1",
            "details": [{"incentive_id": "ot1", "user_id": user_id, "duration_minutes": duration, "amount": amount}]}


def count_template(each=250, user_id=None, incentive_id="c1"):
    return {"type": "COUNT", "incentive_id": incentive_id,
            "details": [{"incentive_id": incentive_id, "user_id": user_id, "each_incentive": each}]}


def turnover_template(percent=2.5, user_id=None, incentive_id="t1"):
    return {"type": "TURNOVER", "incentive_id": incentive_id,
            "details": [{"incentive_id": incentive_id, "user_id": user_id, "after_target_incentive_percent": percent}]}


def bonus_template(percent=10, month="October", year=2026, user_id=None, incentive_id="b1"):
    return {"type": "BONUS", "incentive_id": incentive_id,
            "details": [{"incentive_id": incentive_id, "user_id": user_id, "bonus_percent": percent,
                         "month": month, "year": year}]}
