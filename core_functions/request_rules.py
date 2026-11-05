"""Pure validation rules for leave / permission / shift-change / device-change requests (no database, no clock:
callers pass `today`). Messages and status codes follow the app's executable contract
(`e2e/mock-server/routes/requests.js`) and the app's own rules (`requestRules.ts`); every failure is an
`HTTPException` with a plain-text `detail` the app already shows.
"""
import os
import re
from datetime import date, timedelta
from typing import Any, Iterable, List, Optional, Tuple

from fastapi import HTTPException

MAX_PERMISSION_MINUTES = 360          # permission is at most 6 hours (decision D-17)
MAX_LEAVE_DATES = 366                 # one request cannot span more than a year of dates
MAX_REASON_LENGTH = 500
DEVICE_CHANGE_MAX_PER_DAY = 3         # device-change requests per user in a rolling 24 hours
MAX_CALENDAR_DAYS = 400               # widest /requests/calendar range

DEFAULT_DEVICE_REASON = "Device change requested"

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?$")

OVERLAP_MESSAGE = "A leave request already exists for one of the selected dates"
SAME_SHIFT_MESSAGE = "You are already on this shift"
DEVICE_RATE_LIMIT_MESSAGE = "Too many device change requests. Please try again later."


def _bad(status_code: int, detail: str):
    return HTTPException(status_code=status_code, detail=detail)


def single_device_enforced() -> bool:
    """`ENFORCE_SINGLE_DEVICE` 1/true/yes/on (read on every call; default off = today's behaviour).
    On: managers and staff (roles 2/3) cannot take a session over by OTP from a new device, they must request
    access (a device-change request the admin approves); admins (role 1) stay multi-device."""
    return os.getenv("ENFORCE_SINGLE_DEVICE", "").strip().lower() in ("1", "true", "yes", "on")


def single_device_blocked(role_id: Any, stored_token: Any, stored_expires_at: Any, supplied_token: Any,
                          now: Any) -> bool:
    """True when a login (OTP) from `supplied_token` must be refused under ENFORCE_SINGLE_DEVICE: a single-device
    role (2/3) whose account holds a device token that is set, not expired and different from the supplied one.
    A cleared token (device change approved), an expired one, or the same device never blocks."""
    if role_id not in (2, 3) or not stored_token:
        return False
    if stored_token == supplied_token:
        return False
    if stored_expires_at is not None and stored_expires_at < now:
        return False
    return True


# ---------------------------------------------------------------------------- dates and times
def parse_iso_date(value: Any, label: str) -> date:
    """'YYYY-MM-DD' -> date. Anything else is a 400 `Invalid <label>`."""
    text = str(value).strip() if value is not None else ""
    if not _ISO_DATE.match(text):
        raise _bad(400, f"Invalid {label}")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise _bad(400, f"Invalid {label}")


def normalise_time(value: Any) -> Optional[str]:
    """Any wire time ('09:05 AM', '9:05am', '14:05', '14:05:30') -> 'HH:MM' (24 h), None when unreadable.
    Same reading as the mock's `toHHmm`, plus range checks (hour 0-23, or 1-12 with AM/PM; minute 0-59)."""
    text = str(value).strip() if value is not None else ""
    m = _TIME.match(text)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = (hour % 12) + (12 if meridiem.upper() == "PM" else 0)
    elif hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def minutes_of(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def clean_reason(reason: Any, required: bool) -> str:
    """Trimmed reason. Required -> 400 `Reason is required` when blank. Over 500 characters -> 400."""
    text = str(reason).strip() if reason is not None else ""
    if required and not text:
        raise _bad(400, "Reason is required")
    if len(text) > MAX_REASON_LENGTH:
        raise _bad(400, f"Reason is too long (maximum {MAX_REASON_LENGTH} characters)")
    return text


# ---------------------------------------------------------------------------- leave
def validate_leave(leave_dates: Any, reason: Any, today: date) -> Tuple[List[date], str]:
    """Check a leave request body. Returns (sorted, de-duplicated dates, trimmed reason).
    Order follows the mock: no dates, blank reason, then date validity and the past-date rule."""
    raw = list(leave_dates) if isinstance(leave_dates, (list, tuple)) else []
    if not raw:
        raise _bad(400, "Select at least one leave date")
    text = clean_reason(reason, required=True)
    days = sorted({parse_iso_date(d, "leave date") for d in raw})
    if len(days) > MAX_LEAVE_DATES:
        raise _bad(400, f"A leave request can cover at most {MAX_LEAVE_DATES} dates")
    if days[0] < today:
        raise _bad(400, "Leave dates cannot be in the past")
    return days, text


def overlaps_existing_leave(requested: Iterable[date], existing_active: Iterable[date]) -> bool:
    """True when any requested date is already covered by another pending / approved leave of the user."""
    return bool(set(requested) & set(existing_active))


# ---------------------------------------------------------------------------- permission
def validate_permission(permission_date: Any, start_time: Any, end_time: Any, reason: Any,
                        today: date) -> Tuple[date, str, str, str]:
    """Check a permission request body. Returns (date, 'HH:MM' start, 'HH:MM' end, trimmed reason).
    One calendar day only (both times are clock times of the same date, so the end must be later than the
    start), at most 6 hours (360 minutes passes, 361 fails)."""
    start = normalise_time(start_time)
    end = normalise_time(end_time)
    if not permission_date or not start or not end:
        raise _bad(400, "permission_date, start_time and end_time are required")
    day = parse_iso_date(permission_date, "permission date")
    span = minutes_of(end) - minutes_of(start)
    if span <= 0:
        raise _bad(400, "End time must be after start time")
    if span > MAX_PERMISSION_MINUTES:
        raise _bad(400, "End time cannot be more than 6 hours after start time")
    text = clean_reason(reason, required=True)
    if day < today:
        raise _bad(400, "Permission date cannot be in the past")
    return day, start, end, text


# ---------------------------------------------------------------------------- shift change
def validate_shift_change(reason: Any, requested_shift_id: Any, shift_change_date: Any,
                          today: date) -> Tuple[int, date, str]:
    """Check the shape of a shift-change body. Returns (requested shift id, date, trimmed reason).
    Existence of the shift and 'already on this shift' need the database (see requests_functions)."""
    text = clean_reason(reason, required=True)
    if requested_shift_id is None or isinstance(requested_shift_id, bool):
        raise _bad(400, "Requested shift not found")
    try:
        shift_id = int(requested_shift_id)
    except (TypeError, ValueError):
        raise _bad(400, "Requested shift not found")
    day = parse_iso_date(shift_change_date, "shift change date")
    if day < today:
        raise _bad(400, "Shift change date cannot be in the past")
    return shift_id, day, text


# ---------------------------------------------------------------------------- device change
def device_change_rate_limited(recent_count: int, limit: int = DEVICE_CHANGE_MAX_PER_DAY) -> bool:
    """True when the user already filed `limit` device-change requests in the last 24 hours."""
    return int(recent_count or 0) >= limit


# ---------------------------------------------------------------------------- calendar
def validate_calendar_range(start_date: Any, end_date: Any) -> Tuple[date, date]:
    first = parse_iso_date(start_date, "start_date")
    last = parse_iso_date(end_date, "end_date")
    if last < first:
        raise _bad(400, "end_date must not be before start_date")
    if (last - first).days >= MAX_CALENDAR_DAYS:
        raise _bad(400, f"The date range can cover at most {MAX_CALENDAR_DAYS} days")
    return first, last


def weekly_off_dates(first: date, last: date, off_days: Iterable[int]) -> List[str]:
    """ISO dates between first and last (inclusive) whose weekday (0 = Monday ... 6 = Sunday) is a weekly off."""
    offs = {int(d) for d in off_days}
    out: List[str] = []
    day = first
    while day <= last:
        if day.weekday() in offs:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------- update-status actor
FAIL_NOT_FOUND = "actor_not_found"
FAIL_INACTIVE = "actor_inactive"
FAIL_OTHER_ORG = "request_in_other_org"
FAIL_ROLE = "actor_not_admin_or_manager"
FAIL_PERMISSION = "manager_missing_requests_permission"
FAIL_SCOPE = "manager_out_of_scope"


def evaluate_actor(actor: Optional[dict], requester_branch_id: Any, requester_dept_id: Any) -> Optional[str]:
    """Why `actor` may not decide a request, or None when allowed.

    `actor` is None for an unknown user, else a dict:
      role_id, status, in_request_org (admin owns the request's org / manager belongs to it),
      has_permission (manager holds 'Requests Management'),
      branch_ids / dept_ids (manager assignments; an empty collection means no restriction on that axis,
      the same rule the admin list applies).
    The requester's branch and department are matched against the manager's assignments (AND semantics)."""
    if not actor:
        return FAIL_NOT_FOUND
    if actor.get("status") not in (1, None):
        return FAIL_INACTIVE
    role = actor.get("role_id")
    if role not in (1, 2):
        return FAIL_ROLE
    if not actor.get("in_request_org"):
        return FAIL_OTHER_ORG
    if role == 1:
        return None
    if not actor.get("has_permission"):
        return FAIL_PERMISSION
    branches = {int(b) for b in (actor.get("branch_ids") or ())}
    depts = {str(d) for d in (actor.get("dept_ids") or ())}
    if branches and (requester_branch_id is None or int(requester_branch_id) not in branches):
        return FAIL_SCOPE
    if depts and (requester_dept_id is None or str(requester_dept_id) not in depts):
        return FAIL_SCOPE
    return None
