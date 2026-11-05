import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from core_functions.auth_dependency import caller_orgs
from core_functions.gen_plans_functions import plan_gate
from pydantic import BaseModel
from typing import List, Optional
from core_functions.overtime import compute_overtime
from core_functions.db_function import ist as IST
from core_functions.incentive_functions import (
    get_incentives_by_org,
    get_incentive_details,
    create_count_incentive,
    create_turnover_incentive,
    create_overtime_incentive,
    create_bonus_incentive,
    update_count_incentive,
    update_turnover_incentive,
    update_overtime_incentive,
    update_bonus_incentive,
    delete_incentive,
    get_assigned_incentive_staff,
    get_unassigned_incentive_staff,
    assign_incentive_staff,
    unassign_incentive_staff
)

logger = logging.getLogger(__name__)

# D-12: payroll-related, plan-gated like the salary structure (a lapsed plan answers a real 403)
router = APIRouter(dependencies=[Depends(plan_gate('salary_structure'))])

class CountIncentiveCreate(BaseModel):
    org_id: str
    template_name: str
    count_upto: int
    next_count: int
    each_incentive: float

class CountIncentiveUpdate(BaseModel):
    count_upto: int
    next_count: int
    each_incentive: float

class TurnoverIncentiveCreate(BaseModel):
    org_id: str
    template_name: str
    turnover_upto: float
    incentive_percent: float

class TurnoverIncentiveUpdate(BaseModel):
    turnover_upto: float
    incentive_percent: float

class OvertimeIncentiveCreate(BaseModel):
    org_id: str
    template_name: str
    duration_minutes: int
    amount: float

class OvertimeIncentiveUpdate(BaseModel):
    duration_minutes: int
    amount: float

class BonusIncentiveCreate(BaseModel):
    org_id: str
    template_name: str
    bonus_percent: float
    month: str
    year: int

class BonusIncentiveUpdate(BaseModel):
    bonus_percent: float
    month: str = ''
    year: int = 0

class AssignIncentiveStaffRequest(BaseModel):
    org_id: str
    user_ids: List[str]
    incentive_id: str

# COUNT Incentive Endpoints
@router.get("/api/incentive/count/{org_id}")
async def get_count_incentives(org_id: str):
    """Get all COUNT incentive templates for an organization"""
    try:
        templates = get_incentives_by_org(org_id, 'COUNT')
        return {"success": True, "data": templates}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/count/details/{incentive_id}")
async def get_count_details(incentive_id: str, request: Request):
    """Get details for a COUNT incentive"""
    try:
        details = get_incentive_details(incentive_id, 'COUNT', caller_orgs(request))
        return {"success": True, "data": details}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.post("/api/incentive/count")
async def create_count(data: CountIncentiveCreate):
    """Create a new COUNT incentive"""
    try:
        result = create_count_incentive(data.org_id, data.template_name, data.count_upto, data.next_count, data.each_incentive)
        return {"success": True, "message": "Incentive created successfully", "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.put("/api/incentive/count/{incentive_id}")
async def update_count(incentive_id: str, data: CountIncentiveUpdate, request: Request):
    """Update a COUNT incentive"""
    try:
        success = update_count_incentive(incentive_id, data.count_upto, data.next_count, data.each_incentive, caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive updated successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.delete("/api/incentive/count/{incentive_id}")
async def delete_count(incentive_id: str, request: Request):
    """Delete a COUNT incentive"""
    try:
        success = delete_incentive(incentive_id, 'COUNT', caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive deleted successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

# TURNOVER Incentive Endpoints
@router.get("/api/incentive/turnover/{org_id}")
async def get_turnover_incentives(org_id: str):
    """Get all TURNOVER incentive templates for an organization"""
    try:
        templates = get_incentives_by_org(org_id, 'TURNOVER')
        return {"success": True, "data": templates}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/turnover/details/{incentive_id}")
async def get_turnover_details(incentive_id: str, request: Request):
    """Get details for a TURNOVER incentive"""
    try:
        details = get_incentive_details(incentive_id, 'TURNOVER', caller_orgs(request))
        return {"success": True, "data": details}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.post("/api/incentive/turnover")
async def create_turnover(data: TurnoverIncentiveCreate):
    """Create a new TURNOVER incentive"""
    try:
        result = create_turnover_incentive(data.org_id, data.template_name, data.turnover_upto, data.incentive_percent)
        return {"success": True, "message": "Incentive created successfully", "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.put("/api/incentive/turnover/{incentive_id}")
async def update_turnover(incentive_id: str, data: TurnoverIncentiveUpdate, request: Request):
    """Update a TURNOVER incentive"""
    try:
        success = update_turnover_incentive(incentive_id, data.turnover_upto, data.incentive_percent, caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive updated successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.delete("/api/incentive/turnover/{incentive_id}")
async def delete_turnover(incentive_id: str, request: Request):
    """Delete a TURNOVER incentive"""
    try:
        success = delete_incentive(incentive_id, 'TURNOVER', caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive deleted successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

# OVERTIME Incentive Endpoints
@router.get("/api/incentive/overtime/{org_id}")
async def get_overtime_incentives(org_id: str):
    """Get all OVERTIME incentive templates for an organization"""
    try:
        templates = get_incentives_by_org(org_id, 'OVERTIME')
        return {"success": True, "data": templates}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/overtime/details/{incentive_id}")
async def get_overtime_details(incentive_id: str, request: Request):
    """Get details for an OVERTIME incentive"""
    try:
        details = get_incentive_details(incentive_id, 'OVERTIME', caller_orgs(request))
        return {"success": True, "data": details}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.post("/api/incentive/overtime")
async def create_overtime(data: OvertimeIncentiveCreate):
    """Create a new OVERTIME incentive"""
    try:
        result = create_overtime_incentive(data.org_id, data.template_name, data.duration_minutes, data.amount)
        return {"success": True, "message": "Incentive created successfully", "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.put("/api/incentive/overtime/{incentive_id}")
async def update_overtime(incentive_id: str, data: OvertimeIncentiveUpdate, request: Request):
    """Update an OVERTIME incentive"""
    try:
        success = update_overtime_incentive(incentive_id, data.duration_minutes, data.amount, caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive updated successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.delete("/api/incentive/overtime/{incentive_id}")
async def delete_overtime(incentive_id: str, request: Request):
    """Delete an OVERTIME incentive"""
    try:
        success = delete_incentive(incentive_id, 'OVERTIME', caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive deleted successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/overtime-report/{org_id}")
async def get_overtime_report(
    org_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
):
    """Overtime days / minutes / amount per staff member with an overtime template (defaults to the current IST month)"""
    try:
        now_ist = datetime.now(IST)
        data = compute_overtime(None, org_id, year or now_ist.year, month or now_ist.month)
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

# BONUS Incentive Endpoints
@router.get("/api/incentive/bonus/details/{incentive_id}")
async def get_bonus_details(incentive_id: str, request: Request):
    """Get details for a BONUS incentive"""
    try:
        details = get_incentive_details(incentive_id, 'BONUS', caller_orgs(request))
        return {"success": True, "data": details}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/bonus/{org_id}")
async def get_bonus_incentives(org_id: str):
    """Get all BONUS incentive templates for an organization"""
    try:
        templates = get_incentives_by_org(org_id, 'BONUS')
        return {"success": True, "data": templates}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.post("/api/incentive/bonus")
async def create_bonus(data: BonusIncentiveCreate):
    """Create a new BONUS incentive"""
    try:
        result = create_bonus_incentive(data.org_id, data.template_name, data.bonus_percent, data.month, data.year)
        return {"success": True, "message": "Incentive created successfully", "data": result}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.put("/api/incentive/bonus/{incentive_id}")
async def update_bonus(incentive_id: str, data: BonusIncentiveUpdate, request: Request):
    """Update a BONUS incentive"""
    try:
        success = update_bonus_incentive(incentive_id, data.bonus_percent, data.month, data.year, caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive updated successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.delete("/api/incentive/bonus/{incentive_id}")
async def delete_bonus(incentive_id: str, request: Request):
    """Delete a BONUS incentive"""
    try:
        success = delete_incentive(incentive_id, 'BONUS', caller_orgs(request))
        if success:
            return {"success": True, "message": "Incentive deleted successfully"}
        raise HTTPException(status_code=404, detail="Incentive not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

# Assigned Incentives Endpoints
@router.get("/api/incentive/unassigned-all/{org_id}")
async def get_unassigned_all(org_id: str):
    """Get all staff not assigned to any incentive in the org"""
    try:
        from core_functions.db_function import get_db_connection, return_connection
        from psycopg2.extras import RealDictCursor
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("""
                SELECT u.user_id, u.name, u.emp_id,
                       COALESCE(b.branch_name, '') as branch_name,
                       COALESCE(d.dept_name, '') as dept_name
                FROM users u
                LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
                LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
                WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
                ORDER BY u.name
            """, (org_id,))
            return {"success": True, "data": [dict(r) for r in cursor.fetchall()]}
        finally:
            cursor.close()
            return_connection(conn)
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/assigned/{org_id}/{incentive_id}")
async def get_assigned(org_id: str, incentive_id: str):
    try:
        return {"success": True, "data": get_assigned_incentive_staff(org_id, incentive_id)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.get("/api/incentive/unassigned/{org_id}/{incentive_id}")
async def get_unassigned(org_id: str, incentive_id: str):
    try:
        return {"success": True, "data": get_unassigned_incentive_staff(org_id, incentive_id)}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.post("/api/incentive/assign")
async def assign_staff(data: AssignIncentiveStaffRequest):
    try:
        assign_incentive_staff(data.org_id, data.user_ids, data.incentive_id)
        return {"success": True, "message": f"{len(data.user_ids)} staff assigned successfully"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")

@router.delete("/api/incentive/unassign/{org_id}/{user_id}/{incentive_id}")
async def unassign_staff(org_id: str, user_id: str, incentive_id: str):
    try:
        success = unassign_incentive_staff(org_id, user_id, incentive_id)
        if success:
            return {"success": True, "message": "Staff unassigned successfully"}
        raise HTTPException(status_code=404, detail="Assignment not found")
    except HTTPException:
        raise
    except Exception:
        logger.exception("incentive request failed")
        raise HTTPException(status_code=500, detail="Failed to process the request")
