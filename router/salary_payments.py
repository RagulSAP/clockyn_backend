import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core_functions.gen_plans_functions import plan_gate
from core_functions.salary_payment_functions import get_monthly_list, mark_paid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/salary", tags=["Salary Payments"], dependencies=[Depends(plan_gate('salary_structure'))])


class MarkPaidRequest(BaseModel):
    org_id: str
    user_ids: List[str]
    month: int = Field(ge=1, le=12)
    year: int = Field(ge=2000, le=2100)


@router.get("/monthly-list")
async def monthly_list(
    org_id: str,
    month: int = Query(ge=1, le=12),
    year: int = Query(ge=2000, le=2100),
):
    """Active staff with the salary type, basic/net salary and paid state for a month"""
    try:
        return {"success": True, "data": await get_monthly_list(org_id, month, year)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("monthly salary list failed for org %s (%s-%s)", org_id, year, month)
        raise HTTPException(status_code=500, detail="Failed to load the salary list")


@router.post("/mark-paid")
async def mark_salary_paid(data: MarkPaidRequest):
    """Mark the month's salary paid for the given staff (all-or-nothing, idempotent)"""
    try:
        result = await mark_paid(data.org_id, data.user_ids, data.month, data.year)
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("mark salary paid failed for org %s (%s-%s)", data.org_id, data.year, data.month)
        raise HTTPException(status_code=500, detail="Failed to mark the salary as paid")
