"""
Centralized timezone handling for the backend.

Rules:
- All DB timestamps are stored in UTC
- All API responses convert UTC → IST for display
- Use get_utc_now() instead of datetime.now()
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# Timezone constants
UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")


def get_utc_now() -> datetime:
    """Get current UTC time (timezone-aware).
    
    Use this instead of datetime.now() everywhere.
    All DB timestamps should be stored in UTC.
    """
    return datetime.now(UTC)


def convert_utc_to_ist(dt: datetime) -> datetime:
    """Convert UTC datetime to IST for API responses.
    
    Args:
        dt: UTC datetime (timezone-aware or naive)
    
    Returns:
        IST datetime (timezone-aware)
    """
    if dt is None:
        return None
    
    # If naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    
    return dt.astimezone(IST)


def convert_ist_to_utc(dt: datetime) -> datetime:
    """Convert IST datetime to UTC for DB storage.
    
    Args:
        dt: IST datetime (timezone-aware or naive)
    
    Returns:
        UTC datetime (timezone-aware)
    """
    if dt is None:
        return None
    
    # If naive, assume IST
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    
    return dt.astimezone(UTC)


def format_ist_datetime(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format datetime as IST string for API responses.
    
    Args:
        dt: UTC datetime from DB
        fmt: strftime format string
    
    Returns:
        Formatted IST string
    """
    if dt is None:
        return None
    
    ist_dt = convert_utc_to_ist(dt)
    return ist_dt.strftime(fmt)


def format_ist_time(dt: datetime, fmt: str = "%I:%M %p") -> str:
    """Format time as IST string (12-hour format by default).
    
    Args:
        dt: UTC datetime from DB
        fmt: strftime format string
    
    Returns:
        Formatted IST time string
    """
    if dt is None:
        return None
    
    ist_dt = convert_utc_to_ist(dt)
    return ist_dt.strftime(fmt)

def ist_now() -> datetime:
    """Current time in IST (timezone-aware)."""
    return get_utc_now().astimezone(IST)


def ist_today() -> date:
    """Today's calendar date in IST (the business date: a day runs 00:00-24:00 IST = 18:30-18:30 UTC)."""
    return ist_now().date()


def to_ist(dt: datetime) -> datetime:
    """Any datetime as IST (timezone-aware). A naive value is taken to be UTC (how the DB returns timestamps). None stays None."""
    return convert_utc_to_ist(dt)


def ist_date_of(utc_dt: datetime) -> date:
    """The IST calendar date of a UTC instant, e.g. 2026-05-01 18:30 UTC -> 2026-05-02. None stays None."""
    if utc_dt is None:
        return None
    return convert_utc_to_ist(utc_dt).date()

