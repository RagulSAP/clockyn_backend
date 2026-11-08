"""Pure attendance rules (no database, no FastAPI): IST business date, sessions, lateness, half day,
forgotten punch-out policy and the per-day day types served by `GET /home/monthly-attendance?detail=v2`.

Everything here is a plain function over plain values so it can be unit tested without a database
(tests/test_attendance_ist.py) and reused by payroll / report code later.

Rules (mirrors the executable contract `e2e/mock-server/domain.js` and the client derivation
`src/features/employee/utils/attendanceCalendar.ts`, plus the owner decisions D-2, D-5, D-22):

* Business date (D-2): the calendar date in IST (Asia/Kolkata) of the instant of the punch. Timestamps are stored
  in UTC (timestamptz); `attendance.attendance_date` of a new row is `ist_today(now)`.
* A day holds at most the organisation's `attendance_rule.max_sessions_per_day` sessions (default `MAX_SESSIONS_PER_DAY`, 3). An overnight session belongs to the business date it
  started on; while it is open (and younger than `STALE_OPEN_HOURS`) it is also reported as today's open session.
* Lateness: first punch-in of the day, converted to IST, compared with the shift start (+ grace). Night shifts
  (end < start) are late when the punch falls within 12 h after start + grace.
* Half day: worked time is positive but under half of the shift span (9 h default when there is no shift) and no
  session of the day is still open.
* Forgotten punch-out (D-22): an open session older than 18 h is closed at the next punch-in at the shift end
  (or punch-in + 9 h when there is no shift / the shift end is implausible) and flagged `auto_closed`.
* Day types (D-5), precedence for a date: punches (present / late / half_day) > approved leave > holiday >
  weekly off > upcoming (today or later) > absent.
"""
import calendar
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, Iterable, List, Optional

from utils.timezone import IST, UTC, get_utc_now

IST_TZ_NAME = "Asia/Kolkata"
MAX_SESSIONS_PER_DAY = 3
STALE_OPEN_HOURS = 18            # D-22: an open session older than this is "forgotten"
FALLBACK_SHIFT_HOURS = 9         # D-22: auto-close length when there is no usable shift
DEFAULT_SHIFT_MINUTES = 540      # attendanceCalendar.ts DEFAULT_SHIFT_MINUTES (9 h)

DAY_TYPES = ("present", "late", "half_day", "absent", "leave", "holiday", "weekly_off", "upcoming")
PUNCHED_TYPES = ("present", "late", "half_day")


# --------------------------------------------------------------------------- time helpers
def to_utc(dt: datetime) -> datetime:
    """Aware UTC datetime (naive values are taken as UTC, as the rest of the backend does)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def ist_date_of(dt: datetime) -> date:
    """IST calendar date of an instant: the business date of a punch."""
    return to_utc(dt).astimezone(IST).date()


def ist_today(now: Optional[datetime] = None) -> date:
    """Today's business date in IST (`now` defaults to the current UTC instant)."""
    return ist_date_of(now if now is not None else get_utc_now())


def ist_time_of(dt: datetime) -> time:
    """IST wall-clock time of an instant."""
    return to_utc(dt).astimezone(IST).time()


def stale_cutoff(now: datetime, hours: int = STALE_OPEN_HOURS) -> datetime:
    """Open sessions that started before this instant are stale (forgotten punch-out)."""
    return to_utc(now) - timedelta(hours=hours)


def is_stale_open(punch_in: datetime, now: datetime, hours: int = STALE_OPEN_HOURS) -> bool:
    """True when an open session started MORE than `hours` ago (exactly 18 h is not yet stale)."""
    return (to_utc(now) - to_utc(punch_in)) > timedelta(hours=hours)


def _secs(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def shift_span_minutes(start: Optional[time], end: Optional[time]) -> Optional[int]:
    """Length of a shift in minutes, wrapping past midnight (09:00-18:00 = 540, 21:00-06:00 = 540).
    Mirrors shiftSpanMinutes in src/lib/datetime.ts: 00:00-00:00 is 0 (unusable), equal times otherwise wrap to 24 h."""
    if start is None or end is None:
        return None
    s = start.hour * 60 + start.minute
    e = end.hour * 60 + end.minute
    if s == 0 and e == 0:
        return 0
    return e + 1440 - s if e <= s else e - s


def hhmm(t: Optional[time]) -> Optional[str]:
    """`HH:MM` (24 h) of a time of day, None stays None."""
    return t.strftime("%H:%M") if t is not None else None


# --------------------------------------------------------------------------- sessions
def hours_between(start: datetime, end: datetime) -> float:
    """Hours between two instants, 2 decimals, never negative."""
    return round(max(0.0, (to_utc(end) - to_utc(start)).total_seconds() / 3600), 2)


def session_hours(punch_in: Optional[datetime], punch_out: Optional[datetime], stored_hours, now: datetime) -> float:
    """Hours of one session. Closed: the stored value, else out - in. Open: live hours until `now` while the session
    is not stale, 0 once it is stale (it is waiting for the auto-close). Rows without punch-in count 0."""
    if punch_in is None:
        return 0.0
    if punch_out is not None:
        if stored_hours is not None:
            return float(stored_hours)
        return hours_between(punch_in, punch_out)
    if is_stale_open(punch_in, now):
        return 0.0
    return hours_between(punch_in, now)


def total_hours(sessions: Iterable[Dict], now: datetime) -> float:
    """Sum of `session_hours` over `{punch_in, punch_out, work_hours}` dicts, 2 decimals."""
    return round(sum(session_hours(s.get("punch_in"), s.get("punch_out"), s.get("work_hours"), now) for s in sessions), 2)


def auto_close_time(punch_in: datetime, shift_start: Optional[time], shift_end: Optional[time]) -> datetime:
    """Instant at which a forgotten session is closed (D-22): the first shift end (IST) after the punch-in, or
    punch-in + 9 h when the shift is unknown or that shift end is further than `STALE_OPEN_HOURS` away."""
    pin = to_utc(punch_in)
    fallback = pin + timedelta(hours=FALLBACK_SHIFT_HOURS)
    if shift_start is None or shift_end is None:
        return fallback
    pin_ist = pin.astimezone(IST)
    candidate = datetime.combine(pin_ist.date(), shift_end, tzinfo=IST)
    if candidate <= pin_ist:
        candidate += timedelta(days=1)
    candidate = candidate.astimezone(UTC)
    if candidate - pin > timedelta(hours=STALE_OPEN_HOURS):
        return fallback
    return candidate


def session_status(punch_out: Optional[datetime], auto_closed: bool = False) -> str:
    """`open` (no punch-out yet) | `auto_closed` (closed by the server, D-22) | `closed`."""
    if punch_out is None:
        return "open"
    return "auto_closed" if auto_closed else "closed"


def select_today_sessions(rows: List[Dict], today: date, now: datetime) -> List[Dict]:
    """Rows reported for `today` (IST): every row whose business date is today, plus an open session that started
    earlier and is not stale (an overnight session). Sorted by punch-in."""
    out = []
    for r in rows:
        pin = r.get("punch_in")
        if r.get("attendance_date") == today:
            out.append(r)
        elif pin is not None and r.get("punch_out") is None and not is_stale_open(pin, now):
            out.append(r)
    return sorted(out, key=lambda r: (r.get("punch_in") is None, to_utc(r["punch_in"]) if r.get("punch_in") else 0))


# --------------------------------------------------------------------------- lateness and half day
def is_late(punch_time: time, shift_start: time, shift_end: time, grace_minutes: int = 0) -> bool:
    """True when `punch_time` (IST time of day) is later than shift start + grace.

    Day shift (end >= start): plain comparison. Night shift (end < start): late when the punch falls within 12 h
    after start + grace (modulo 24 h), so arriving at 20:00 for a 21:00 shift is not late. Same semantics as
    attendance_rule_functions.is_late (kept in sync by a test)."""
    limit = _secs(shift_start) + grace_minutes * 60
    p = _secs(punch_time)
    if shift_end >= shift_start:
        return p > limit
    return 0 < (p - limit) % 86400 <= 12 * 3600


def late_by_minutes(punch_time: Optional[time], shift_start: Optional[time], shift_end: Optional[time],
                    grace_minutes: int = 0) -> int:
    """Minutes after the shift start when the punch is late (at least 1), else 0. 0 without a shift."""
    if punch_time is None or shift_start is None or shift_end is None:
        return 0
    if not is_late(punch_time, shift_start, shift_end, grace_minutes):
        return 0
    diff = _secs(punch_time) - _secs(shift_start)
    if shift_end < shift_start:
        diff %= 86400
    return max(1, diff // 60)


def is_half_day(worked_minutes: float, shift_start: Optional[time], shift_end: Optional[time]) -> bool:
    """Client rule (attendanceCalendar.isHalfDay): worked time positive (>= 1 min) but under half of the shift span;
    the span is 9 h when there is no shift or the span is unusable."""
    span = shift_span_minutes(shift_start, shift_end)
    shift_mins = span if span is not None and span > 0 else DEFAULT_SHIFT_MINUTES
    return worked_minutes >= 1 and worked_minutes < shift_mins / 2


# --------------------------------------------------------------------------- day types (detail=v2)
def classify_day(day: date, today: date, sessions: List[Dict], shift_start: Optional[time], shift_end: Optional[time],
                 now: datetime, grace_minutes: int = 0, is_leave: bool = False,
                 holiday_name: Optional[str] = None, is_weekly_off: bool = False) -> Dict:
    """Day type and figures of one calendar day.

    `sessions` are `{punch_in, punch_out, work_hours}` dicts of the business date `day` (aware datetimes).
    Returns `type` (one of DAY_TYPES), `status` (present | late | half_day | absent | leave: the set the app maps;
    holiday / weekly_off / upcoming report `absent`), `late_by_minutes`, `total_hours`, `session_count`,
    `has_open_session`, `is_holiday`, `is_weekly_off`, `holiday_name`."""
    punched = [s for s in sessions if s.get("punch_in") is not None]
    is_holiday = holiday_name is not None
    late_by = 0
    hours = 0.0
    has_open = False
    if punched:
        first_in = min(punched, key=lambda s: to_utc(s["punch_in"]))["punch_in"]
        late_by = late_by_minutes(ist_time_of(first_in), shift_start, shift_end, grace_minutes)
        hours = total_hours(punched, now)
        has_open = any(s.get("punch_out") is None for s in punched)
        half = (not has_open) and is_half_day(hours * 60, shift_start, shift_end)
        status = "half_day" if half else ("late" if late_by > 0 else "present")
        day_type = status
    elif is_leave:
        day_type, status = "leave", "leave"
    elif is_holiday:
        day_type, status = "holiday", "absent"
    elif is_weekly_off:
        day_type, status = "weekly_off", "absent"
    elif day >= today:
        day_type, status = "upcoming", "absent"
    else:
        day_type, status = "absent", "absent"
    return {
        "type": day_type,
        "status": status,
        "late_by_minutes": late_by,
        "total_hours": hours,
        "session_count": len(punched),
        "has_open_session": has_open,
        "is_holiday": is_holiday,
        "is_weekly_off": is_weekly_off,
        "holiday_name": holiday_name,
    }


def summarize_days(days: List[Dict], total_days: int) -> Dict:
    """Month counters from classified days (the dicts returned by `classify_day`).

    present_days = punched days (present, late and half_day); late_days = punched days with late_by_minutes > 0;
    working_days = days of the month that are neither a holiday nor a weekly off (holiday / weekly-off days that
    carry punches count as working); absent_days = elapsed working days without punches or leave."""
    count = lambda t: sum(1 for d in days if d["type"] == t)  # noqa: E731
    holiday_days = count("holiday")
    weekly_off_days = count("weekly_off")
    return {
        "total_days": total_days,
        "working_days": total_days - holiday_days - weekly_off_days,
        "present_days": sum(1 for d in days if d["type"] in PUNCHED_TYPES),
        "late_days": sum(1 for d in days if d["type"] in PUNCHED_TYPES and d["late_by_minutes"] > 0),
        "half_days": count("half_day"),
        "leave_days": count("leave"),
        "holiday_days": holiday_days,
        "weekly_off_days": weekly_off_days,
        "absent_days": count("absent"),
    }


def month_dates(year: int, month: int) -> List[date]:
    """Every calendar date of a month."""
    return [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]


def build_month(year: int, month: int, today: date, now: datetime,
                sessions_by_date: Dict[date, List[Dict]],
                shift_of: Callable[[date], Optional[Dict]],
                grace_minutes: int = 0,
                leave_dates: Iterable[date] = (),
                holidays: Optional[Dict[date, str]] = None,
                off_weekdays: Iterable[int] = (6,)) -> List[Dict]:
    """Classify every day of a month. `shift_of(date)` returns `{start_time, end_time}` (time objects) or None: it is
    asked per date so a per-date shift lookup can replace the single current shift later. `off_weekdays` use Python
    weekdays (Mon = 0 ... Sun = 6). Returns one dict per date: `classify_day` fields plus `date`, `shift_start`,
    `shift_end` (time or None) and `sessions` (the input session dicts)."""
    leaves = set(leave_dates)
    holidays = holidays or {}
    offs = set(off_weekdays)
    out = []
    for d in month_dates(year, month):
        shift = shift_of(d)
        start = shift["start_time"] if shift else None
        end = shift["end_time"] if shift else None
        sessions = sessions_by_date.get(d, [])
        info = classify_day(d, today, sessions, start, end, now, grace_minutes,
                            is_leave=d in leaves, holiday_name=holidays.get(d), is_weekly_off=d.weekday() in offs)
        info.update({"date": d, "shift_start": start, "shift_end": end, "sessions": sessions})
        out.append(info)
    return out
