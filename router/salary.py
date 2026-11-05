import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from core_functions.auth_dependency import caller_orgs
from core_functions.gen_plans_functions import check_plan_feature, plan_gate
from core_functions.salary_structure import (
    create_salary_template,
    get_salary_templates,
    get_salary_template,
    update_salary_template,
    delete_salary_template,
    get_branches_by_org,
    get_departments_by_org,
    get_staff_by_branch_department,
    assign_payslip_to_staff,
    get_payslip_preview,
    get_employee_assigned_payslip,
    SalaryTemplateCreate
)
from core_functions.salary_engine import compute_org_salaries
from ml.s3 import get_employee_profile_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/salary", tags=["Salary Structure"], dependencies=[Depends(plan_gate('salary_structure'))])

class PayslipAssignment(BaseModel):
    org_id: str
    staff_id: str
    pay_slip_id: str

def _breakdown_row(row: dict) -> dict:
    """One `/staff-breakdown` row from an engine row. A failed computation is an `error: true` row (identity fields
    only), never a row of zeros."""
    base = {
        'user_id':            row['user_id'],
        'name':               row['name'],
        'emp_id':             row['emp_id'],
        'role_name':          row['role_name'] or '',
        'branch_name':        row['branch_name'],
    }
    photo = get_employee_profile_url(row['user_id'])
    if row['error'] or row['data'] is None:
        return {**base, 'error': True, 'profile_photo_url': photo}
    d = row['data']
    e = d['earnings']
    ded = d['deductions']
    return {
        **base,
        'salary_type':        d['salary_type'],
        'basic_salary':       e['basic'],
        'incentive':          e['incentive_count'] + e['incentive_turnover'],
        'incentive_count':    e['incentive_count'],
        'incentive_turnover': e['incentive_turnover'],
        'overtime':           e['overtime'],
        'bonus':              e['bonus'],
        'fine':               ded.get('fine', 0),
        'travel_allowance':   e['travel'],
        'odometer':           e['odometer'],
        'travel_ticket':      e['travel'],
        'advance':            ded['advance'],
        'other_bills':        e['others'],
        'daily_wages':        e['daily_wages'],
        'hourly_calc':        e['hourly_calc'],
        'net_salary':         d['net_salary'],
        'profile_photo_url':  photo,
        # additive
        'salary_types':       d.get('salary_types', []),
        'present_days':       d['present_days'],
        'working_days':       d.get('working_days'),
        'half_days':          d.get('half_days'),
        'leave_days':         d.get('leave_days'),
        'absent_days':        d.get('absent_days'),
        'late_days':          d.get('late_days'),
        'paid':               d.get('paid', False),
        'paid_on':            d.get('paid_on'),
        'paid_date':          d.get('paid_date'),
    }


@router.get("/staff-breakdown")
async def staff_salary_breakdown(org_id: str, month: int = Query(ge=1, le=12), year: int = Query(ge=2000, le=2100)):
    """Salary components of every active staff member for a month (shared salary engine, one batch per request).
    A paid month returns the figures stored when it was paid with `paid: true`."""
    await check_plan_feature(org_id, 'salary_structure', is_admin=True)
    try:
        rows = compute_org_salaries(org_id, year, month)
    except HTTPException:
        raise
    except Exception:
        logger.exception("salary breakdown failed for org %s (%s-%s)", org_id, year, month)
        raise HTTPException(status_code=500, detail="Failed to load salary breakdown")
    return [_breakdown_row(r) for r in rows]


@router.post("/template")
async def create_template(template_data: SalaryTemplateCreate):
    await check_plan_feature(template_data.org_id, 'salary_structure', is_admin=True)
    return await create_salary_template(template_data)

@router.get("/templates/{org_id}")
async def get_templates(org_id: str):
    await check_plan_feature(org_id, 'salary_structure', is_admin=True)
    return await get_salary_templates(org_id)

@router.get("/template/{pay_slip_id}")
async def get_template(pay_slip_id: str, request: Request, org_id: Optional[str] = None):
    """Get a specific salary template (another organisation's id answers 404 like a missing one)"""
    return await get_salary_template(pay_slip_id, org_id=org_id, orgs=caller_orgs(request))

@router.put("/template/{pay_slip_id}")
async def update_template(pay_slip_id: str, template_data: SalaryTemplateCreate):
    """Update an existing salary template"""
    return await update_salary_template(pay_slip_id, template_data)

@router.delete("/template/{pay_slip_id}")
async def delete_template(pay_slip_id: str):
    """Delete a salary template"""
    return await delete_salary_template(pay_slip_id)

@router.get("/branches/{org_id}")
async def get_branches(org_id: str):
    """Get all branches for an organization"""
    return await get_branches_by_org(org_id)

@router.get("/departments/{org_id}")
async def get_departments(org_id: str):
    """Get all departments for an organization"""
    return await get_departments_by_org(org_id)

@router.get("/staff/{org_id}")
async def get_staff(org_id: str, branch_id: str = None, department_id: str = None, pay_slip_id: str = None):
    """Get staff by branch and department"""
    return await get_staff_by_branch_department(org_id, branch_id, department_id, pay_slip_id)

@router.post("/assign")
async def assign_payslip(assignment: PayslipAssignment):
    """Assign a payslip to a staff member"""
    return await assign_payslip_to_staff(assignment.org_id, assignment.staff_id, assignment.pay_slip_id)

@router.get("/employee/{org_id}/{staff_id}")
async def get_employee_payslip(org_id: str, staff_id: str):
    """Get the assigned payslip for an employee"""
    return await get_employee_assigned_payslip(org_id, staff_id)

@router.get("/preview/{org_id}/{staff_id}/{pay_slip_id}")
async def preview_payslip(
    org_id: str,
    staff_id: str,
    pay_slip_id: str,
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None, ge=2000, le=2100),
):
    """Get payslip preview for a staff member (optional `month` / `year`, default the current IST month)"""
    return await get_payslip_preview(org_id, staff_id, pay_slip_id, month, year)
