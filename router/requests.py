import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from utils import rate_limit
from typing import List, Optional
from core_functions.requests_functions import (
    submit_leave_request,
    submit_permission_request,
    submit_shift_change_request,
    submit_device_change_request,
    get_employee_requests,
    get_admin_requests,
    update_request_status,
    get_employee_calendar_data,
    check_device_session
)

router = APIRouter(prefix="/requests", tags=["Requests"])
logger = logging.getLogger(__name__)

_SERVER_ERROR = "Internal server error"


def _server_error(where: str) -> HTTPException:
    """500 without the raw error text (it is logged with its traceback instead)."""
    logger.exception("requests.%s failed", where)
    return HTTPException(status_code=500, detail=_SERVER_ERROR)


class LeaveRequestModel(BaseModel):
    user_id: str
    org_id: str
    leave_dates: List[str]
    reason: str


class PermissionRequestModel(BaseModel):
    user_id: str
    org_id: str
    permission_date: str
    start_time: str
    end_time: str
    reason: str


class DeviceChangeRequestModel(BaseModel):
    user_id: str
    org_id: str
    reason: str


class ShiftChangeRequestModel(BaseModel):
    user_id: str
    org_id: str
    reason: str
    requested_shift_id: int
    shift_change_date: str


class UpdateRequestStatusModel(BaseModel):
    request_id: str
    status: str
    action_by: str
    role_id: int
    leave_date: Optional[str] = None


@router.post("/device-change/submit", dependencies=[Depends(rate_limit.by_ip("DEVICE_CHANGE_SUBMIT"))])
async def submit_device_change(request: DeviceChangeRequestModel):
    try:
        result = submit_device_change_request(request.user_id, request.org_id, request.reason)
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("device-change/submit")


@router.post("/shift-change/submit")
async def submit_shift_change(request: ShiftChangeRequestModel):
    try:
        result = submit_shift_change_request(
            request.user_id, request.org_id, request.reason,
            request.requested_shift_id, request.shift_change_date
        )
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("shift-change/submit")


@router.post("/leave/submit")
async def submit_leave(request: LeaveRequestModel):
    try:
        result = submit_leave_request(
            request.user_id,
            request.org_id,
            request.leave_dates,
            request.reason
        )
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("leave/submit")


@router.post("/permission/submit")
async def submit_permission(request: PermissionRequestModel):
    try:
        result = submit_permission_request(
            request.user_id,
            request.org_id,
            request.permission_date,
            request.start_time,
            request.end_time,
            request.reason
        )
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("permission/submit")


@router.get("/employee/{user_id}/{org_id}")
async def get_employee_requests_api(user_id: str, org_id: str):
    try:
        result = get_employee_requests(user_id, org_id)
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("employee")


@router.get("/admin/{org_id}")
async def get_admin_requests_api(org_id: str, status: Optional[str] = None):
    try:
        result = get_admin_requests(org_id, status)
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("admin")


@router.post("/update-status")
async def update_status(request: UpdateRequestStatusModel):
    try:
        result = update_request_status(
            request.request_id,
            request.status,
            request.action_by,
            request.role_id,
            request.leave_date
        )
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("update-status")


@router.get("/calendar/{user_id}/{org_id}")
async def get_calendar_data(user_id: str, org_id: str, start_date: str, end_date: str,
                            include_off_days: bool = False):
    """Leave / permission dates of the employee in the range (`data`, unchanged) plus the additive `holidays`,
    `weekly_offs`, `off_days` and `shift_overrides`. `include_off_days=true` also appends the holidays and weekly
    offs to `data` as `type: "holiday" | "weekly_off"` rows (for a client that reads `type`)."""
    try:
        result = get_employee_calendar_data(user_id, org_id, start_date, end_date, include_off_days)
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("calendar")


@router.get("/check-session/{user_id}")
async def check_session(user_id: str, device_token: str):
    """Poll this endpoint to detect if the current device has been logged out by another device login.
    Frontend should call this periodically (e.g. every 30s) and force logout if force_logout=True.
    """
    try:
        result = check_device_session(user_id, device_token)
        return result
    except HTTPException:
        raise
    except Exception:
        raise _server_error("check-session")
