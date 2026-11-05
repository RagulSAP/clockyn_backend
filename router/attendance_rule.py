import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictInt, Field

from core_functions.attendance_rule_functions import get_rule, save_rule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attendance-rule", tags=["Attendance Rule"])


class AttendanceRuleSave(BaseModel):
    late_punch_in_count: StrictInt = Field(ge=1, le=10)
    consider_as: Literal['Half Day', 'Leave']


@router.get("/{org_id}")
def get_attendance_rule(org_id: str):
    """Late punch-in rule of an organisation (defaults 3 / Half Day when none is saved)."""
    try:
        return {"success": True, **get_rule(org_id)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("attendance rule read failed for org %s", org_id)
        raise HTTPException(status_code=500, detail="Failed to load the attendance rule")


@router.post("/{org_id}")
def save_attendance_rule(org_id: str, data: AttendanceRuleSave):
    """Create or replace the late punch-in rule of an organisation (idempotent)."""
    try:
        saved = save_rule(org_id, data.late_punch_in_count, data.consider_as)
        return {"success": True, "message": "Attendance rule saved", **saved}
    except HTTPException:
        raise
    except Exception:
        logger.exception("attendance rule save failed for org %s", org_id)
        raise HTTPException(status_code=500, detail="Failed to save the attendance rule")
