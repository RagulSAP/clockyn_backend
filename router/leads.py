from fastapi import APIRouter, Query, Request
from core_functions.auth_dependency import caller_orgs
from core_functions.gen_plans_functions import check_plan_feature
from core_functions.leads_functions import (
    create_lead, get_leads, get_employee_leads, assign_lead, delete_lead, update_lead,
    update_lead_assignment, get_categories, create_category, delete_category,
    update_work_status, update_lead_status, LeadCreate,
    request_carry_forward, get_carry_forward_requests, action_carry_forward,
    get_my_carry_forward_requests
)

router = APIRouter()

# ── Static / prefix routes MUST come before wildcard /{leads_id} routes ──

@router.post("/api/leads/create")
async def create_lead_endpoint(lead_data: LeadCreate):
    await check_plan_feature(lead_data.org_id, 'crm', is_admin=True)
    return await create_lead(lead_data)

@router.post("/api/leads/assign")
async def assign_lead_endpoint(
    leads_id: str = Query(...),
    org_id: str = Query(...),
    user_id: str = Query(...),
    branch_id: int = Query(None),
    dept_id: str = Query(None),
    remarks: str = Query(''),
    request_date: str = Query(None)
):
    await check_plan_feature(org_id, 'crm', is_admin=True)
    return await assign_lead(leads_id, org_id, user_id, branch_id, dept_id, remarks, request_date)

@router.get("/api/leads/employee/{org_id}/{user_id}")
async def get_employee_leads_endpoint(org_id: str, user_id: str):
    await check_plan_feature(org_id, 'crm', is_admin=False)
    return await get_employee_leads(org_id, user_id)

@router.get("/api/leads/categories/{org_id}")
async def get_categories_endpoint(org_id: str):
    return await get_categories(org_id)

@router.post("/api/leads/categories")
async def create_category_endpoint(
    org_id: str = Query(...),
    category_name: str = Query(...)
):
    return await create_category(org_id, category_name)

@router.delete("/api/leads/categories/{category_id}")
async def delete_category_endpoint(request: Request, category_id: str, org_id: str = Query(None)):
    return await delete_category(category_id, org_id, caller_orgs(request))

@router.get("/api/leads/carry-forward/my/{org_id}/{user_id}")
async def get_my_carry_forward_requests_endpoint(org_id: str, user_id: str):
    return await get_my_carry_forward_requests(org_id, user_id)


@router.post("/api/leads/carry-forward/request")
async def request_carry_forward_endpoint(
    leads_id: str = Query(...),
    org_id: str = Query(...),
    user_id: str = Query(...),
    description: str = Query(...),
    forward_date: str = Query(None)
):
    return await request_carry_forward(leads_id, org_id, user_id, description, forward_date)

@router.get("/api/leads/carry-forward/requests/{org_id}")
async def get_carry_forward_requests_endpoint(org_id: str):
    return await get_carry_forward_requests(org_id)

@router.put("/api/leads/carry-forward/{request_id}/action")
async def action_carry_forward_endpoint(
    request: Request,
    request_id: str,
    action: str = Query(...),
    admin_id: str = Query(...)
):
    return await action_carry_forward(request_id, action, admin_id, caller_orgs(request))

# ── Wildcard /{leads_id} routes LAST ──

@router.get("/api/leads/{org_id}")
async def get_leads_endpoint(org_id: str):
    await check_plan_feature(org_id, 'crm', is_admin=True)
    return await get_leads(org_id)

@router.delete("/api/leads/{leads_id}")
async def delete_lead_endpoint(request: Request, leads_id: str, org_id: str = Query(None)):
    return await delete_lead(leads_id, org_id, caller_orgs(request))

@router.put("/api/leads/{leads_id}/work-status")
async def update_work_status_endpoint(request: Request, leads_id: str, work_status: str = Query(...), user_id: str = Query(None), org_id: str = Query(None)):
    if not user_id:
        # no explicit user: only the caller's own assignment may change (the token's user, when there is one)
        identity = getattr(request.state, "identity", None)
        user_id = getattr(identity, "user_id", None) or None
    return await update_work_status(leads_id, work_status, user_id, org_id, caller_orgs(request))

@router.put("/api/leads/{leads_id}/status")
async def update_lead_status_endpoint(request: Request, leads_id: str, status: str = Query(...), org_id: str = Query(None)):
    return await update_lead_status(leads_id, status, org_id, caller_orgs(request))

@router.put("/api/leads/assignment/{leads_id}")
async def update_lead_assignment_endpoint(
    request: Request,
    leads_id: str,
    user_id: str = Query(...),
    branch_id: int = Query(None),
    dept_id: str = Query(None),
    org_id: str = Query(None)
):
    return await update_lead_assignment(leads_id, user_id, branch_id, dept_id, org_id, caller_orgs(request))

@router.put("/api/leads/{leads_id}")
async def update_lead_endpoint(request: Request, leads_id: str, lead_data: LeadCreate):
    return await update_lead(leads_id, lead_data, caller_orgs(request))
