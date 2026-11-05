"""Daily wages, hourly charges and wage assignment routes.

Three routers: /api/daily-wages (type D), /api/hourly-charges (type H), /api/wages (assignment).
Business errors use real HTTP status codes (400 / 404 / 409) with {"detail": ...}.
"""
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from core_functions.auth_dependency import caller_orgs
from core_functions.gen_plans_functions import plan_gate

from core_functions.daily_hourly_functions import (
    get_templates_by_org,
    create_template,
    update_template,
    delete_template,
    get_assigned_staff,
    get_unassigned_staff,
    assign_staff_to_wage,
    unassign_staff_from_wage,
)


class TemplateCreate(BaseModel):
    org_id: Optional[str] = None
    amount: Any = None  # validated in parse_amount so bad values give 400, not 422
    template_name: Optional[str] = None


class TemplateUpdate(BaseModel):
    org_id: Optional[str] = None
    amount: Any = None


class AssignStaffRequest(BaseModel):
    org_id: Optional[str] = None
    user_ids: Optional[List[str]] = None
    temp_id: Optional[str] = None


def _template_router(prefix: str, temp_type: str) -> APIRouter:
    r = APIRouter(prefix=prefix, dependencies=[Depends(plan_gate('salary_structure'))])

    @r.get("/templates/{org_id}")
    def list_templates(org_id: str):
        return {"success": True, "data": get_templates_by_org(org_id, temp_type)}

    @r.post("/template")
    def create(data: TemplateCreate):
        result = create_template(data.org_id, data.amount, temp_type, template_name=data.template_name)
        return {
            "success": True,
            "message": "Template saved successfully",
            "id": result["id"],
            "temp_name": result["temp_name"],
            "template_name": result["template_name"],
        }

    @r.put("/template/{temp_id}")
    def update(temp_id: str, data: TemplateUpdate, request: Request):
        update_template(temp_id, data.amount, temp_type, org_id=data.org_id, orgs=caller_orgs(request))
        return {"success": True, "message": "Template updated successfully"}

    @r.delete("/template/{temp_id}")
    def delete(temp_id: str, request: Request, org_id: Optional[str] = Query(None)):
        unassigned = delete_template(temp_id, temp_type, org_id=org_id, orgs=caller_orgs(request))
        return {"success": True, "message": "Template deleted successfully", "unassigned": unassigned}

    return r


daily_router = _template_router("/api/daily-wages", "D")
hourly_router = _template_router("/api/hourly-charges", "H")

wages_router = APIRouter(prefix="/api/wages", dependencies=[Depends(plan_gate('salary_structure'))])


@wages_router.get("/assigned/{org_id}/{temp_id}")
def assigned(org_id: str, temp_id: str):
    return {"success": True, "data": get_assigned_staff(org_id, temp_id)}


@wages_router.get("/unassigned/{org_id}/{temp_id}")
def unassigned(org_id: str, temp_id: str):
    return {"success": True, "data": get_unassigned_staff(org_id, temp_id)}


@wages_router.post("/assign")
def assign(data: AssignStaffRequest):
    if not data.temp_id:
        raise HTTPException(status_code=400, detail="temp_id is required")
    result = assign_staff_to_wage(data.org_id, data.user_ids, data.temp_id)
    return {
        "success": True,
        "message": "Staff assigned successfully",
        "assigned": result["assigned"],
        "moved_from": result["moved_from"],
    }


@wages_router.delete("/unassign/{org_id}/{user_id}/{temp_id}")
def unassign(org_id: str, user_id: str, temp_id: str):
    if not unassign_staff_from_wage(org_id, user_id, temp_id):
        raise HTTPException(status_code=404, detail="Assignment not found")
    return {"success": True, "message": "Staff unassigned successfully"}


routers = [daily_router, hourly_router, wages_router]
