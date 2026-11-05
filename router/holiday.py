from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from core_functions.holiday_functions import (
    get_holidays, add_holiday, delete_holiday, update_holiday, get_working_days,
    get_off_days, set_off_days
)

# The single owner of /api/holidays*. Business failures (holiday not found / belongs to another org / duplicate
# date+scope) answer HTTP 200 {success: false, message}; bad input (blank name, invalid date, bad month, bad
# weekday) answers 422.
router = APIRouter(prefix="/api/holidays", tags=["holidays"])


class AddHolidayRequest(BaseModel):
    name: str
    date: str
    branch_name: Optional[str] = None
    dept_name: Optional[str] = None
    block_attendance: bool = True


class UpdateHolidayRequest(BaseModel):
    name: Optional[str] = None
    date: Optional[str] = None
    block_attendance: Optional[bool] = None


class SetOffDaysRequest(BaseModel):
    off_days: List[int]  # 0=Monday ... 6=Sunday (Python weekday)


def _call(fn, *args):
    """Run a core function: ValueError -> 422 (bad input), anything else -> 500."""
    try:
        return fn(*args)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{org_id}")
def get_holidays_route(org_id: str, year: int = 0, month: int = 0):
    # year / month default to the current IST month when omitted
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    return _call(get_holidays, org_id, year or today.year, month or today.month)


@router.post("/{org_id}")
def add_holiday_route(org_id: str, body: AddHolidayRequest):
    return _call(add_holiday, org_id, body.name, body.date, body.branch_name, body.dept_name, body.block_attendance)


@router.put("/{org_id}/{holiday_id}")
def update_holiday_route(org_id: str, holiday_id: int, body: UpdateHolidayRequest):
    return _call(update_holiday, org_id, holiday_id, body.name, body.date, body.block_attendance)


@router.delete("/{org_id}/{holiday_id}")
def delete_holiday_route(org_id: str, holiday_id: int):
    return _call(delete_holiday, org_id, holiday_id)


@router.get("/working-days/{org_id}")
def working_days_route(
    org_id: str, year: int, month: int,
    branch_name: Optional[str] = None,
    dept_name: Optional[str] = None
):
    return _call(get_working_days, org_id, year, month, branch_name, dept_name)


@router.get("/off-days/{org_id}")
def get_off_days_route(org_id: str):
    return _call(get_off_days, org_id)


@router.post("/off-days/{org_id}")
def set_off_days_route(org_id: str, body: SetOffDaysRequest):
    return _call(set_off_days, org_id, body.off_days)
