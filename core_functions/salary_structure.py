from fastapi import HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import logging
from utils.timezone import get_utc_now, IST
import uuid
from core_functions import attendance_rules as rules
from core_functions import payroll_engine, payroll_validation, salary_engine
from core_functions.attendance_functions import _guarded
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


# Pydantic Models
class EarningItem(BaseModel):
    name: str
    amount: float

class DeductionItem(BaseModel):
    name: str
    amount: float

class SalaryTemplateCreate(BaseModel):
    payslip_name: str
    org_id: str
    basic_pay: float
    hra: float
    epf: float
    professional_tax: float
    health_insurance: float
    count_basis: Optional[float] = None
    upto: Optional[float] = None
    next: Optional[float] = None
    custom_earnings: Optional[List[EarningItem]] = []
    custom_deductions: Optional[List[DeductionItem]] = []

class SalaryTemplateResponse(BaseModel):
    pay_slip_id: str
    payslip_name: str
    org_id: str
    basic_pay: float
    hra: float
    epf: float
    professional_tax: float
    health_insurance: float
    count_basis: Optional[float]
    upto: Optional[float]
    next: Optional[float]
    total_earnings: float
    total_deductions: float
    net_salary: float
    custom_earnings: List[EarningItem]
    custom_deductions: List[DeductionItem]
    created_at: datetime

def _lock_templates(cursor, org_id: str):
    """Serialise writers of one organisation's payslip templates (name uniqueness check + insert)."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"payslip:{org_id}",))


def _template_name_taken(cursor, org_id: str, name: str, exclude_id: str = None) -> bool:
    cursor.execute(
        """SELECT 1 FROM pay_slip
           WHERE org_id = %s AND lower(pay_slip_name) = lower(%s)
             AND (%s::text IS NULL OR pay_slip_id <> %s::text)
           LIMIT 1""",
        (org_id, name, exclude_id, exclude_id),
    )
    return cursor.fetchone() is not None


def _insert_custom_lines(cursor, pay_slip_id: str, earnings, deductions):
    for name, amount in earnings:
        cursor.execute("""
            INSERT INTO payslip_add_earnings (pay_slip_id, add_earnings_name, add_earnings_amount)
            VALUES (%s, %s, %s)
        """, (pay_slip_id, name, amount))
    for name, amount in deductions:
        cursor.execute("""
            INSERT INTO payslip_add_deduction (pay_slip_id, add_deduction_name, add_deduction_amount)
            VALUES (%s, %s, %s)
        """, (pay_slip_id, name, amount))


async def create_salary_template(template_data: SalaryTemplateCreate):
    """Create a new salary template (validated, D-15: amounts 0..9,999,999.99 with 2 decimals, unique name per org)"""
    v = payroll_validation.validate_salary_template(template_data)
    org_id = template_data.org_id
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        _lock_templates(cursor, org_id)
        if _template_name_taken(cursor, org_id, v["name"]):
            raise HTTPException(status_code=409, detail=payroll_validation.MSG_TEMPLATE_NAME_EXISTS)

        pay_slip_id = str(uuid.uuid4())
        amounts = v["amounts"]
        cursor.execute(
            """
            INSERT INTO pay_slip (pay_slip_id, pay_slip_name, org_id, basic_pay, hra, epf, professional_tax, health_insurance, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (pay_slip_id, v["name"], org_id, amounts["basic_pay"], amounts["hra"], amounts["epf"],
             amounts["professional_tax"], amounts["health_insurance"]),
        )
        _insert_custom_lines(cursor, pay_slip_id, v["custom_earnings"], v["custom_deductions"])
        conn.commit()

        updated_templates = await get_salary_templates(org_id)

        return {
            "success": True,
            "message": "Salary template created successfully",
            "pay_slip_id": pay_slip_id,
            "updated_templates": updated_templates.get("data", [])
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("create_salary_template failed")
        raise HTTPException(status_code=500, detail="Failed to create salary template")
    finally:
        cursor.close()
        return_connection(conn)

async def get_salary_templates(org_id: str):
    """Get all salary templates for an organization"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Fetch templates + all related earnings, deductions, and assigned counts in 3 queries (was 1+3N)
        cursor.execute("""
            SELECT * FROM pay_slip WHERE org_id = %s ORDER BY created_at ASC
        """, (org_id,))
        templates = cursor.fetchall()

        if not templates:
            return {"success": True, "data": []}

        template_ids = [t['pay_slip_id'] for t in templates]
        placeholders = ','.join(['%s'] * len(template_ids))

        cursor.execute(f"""
            SELECT pay_slip_id, add_earnings_name as name, add_earnings_amount as amount
            FROM payslip_add_earnings WHERE pay_slip_id IN ({placeholders})
        """, template_ids)
        earnings_by_id: dict = {}
        for row in cursor.fetchall():
            earnings_by_id.setdefault(row['pay_slip_id'], []).append({'name': row['name'], 'amount': row['amount']})

        cursor.execute(f"""
            SELECT pay_slip_id, add_deduction_name as name, add_deduction_amount as amount
            FROM payslip_add_deduction WHERE pay_slip_id IN ({placeholders})
        """, template_ids)
        deductions_by_id: dict = {}
        for row in cursor.fetchall():
            deductions_by_id.setdefault(row['pay_slip_id'], []).append({'name': row['name'], 'amount': row['amount']})

        cursor.execute(f"""
            SELECT pay_slip_id, COUNT(*) as count FROM payroll
            WHERE pay_slip_id IN ({placeholders}) GROUP BY pay_slip_id
        """, template_ids)
        assigned_by_id = {row['pay_slip_id']: row['count'] for row in cursor.fetchall()}

        result = []
        for template in templates:
            pid = template['pay_slip_id']
            earnings = earnings_by_id.get(pid, [])
            deductions = deductions_by_id.get(pid, [])
            assigned_count = assigned_by_id.get(pid, 0)

            # Calculate totals
            total_earnings = float(template['basic_pay']) + float(template['hra']) + sum(float(e['amount']) for e in earnings)
            total_deductions = float(template['epf']) + float(template['professional_tax']) + float(template['health_insurance']) + sum(float(d['amount']) for d in deductions)
            net_salary = total_earnings - total_deductions

            result.append({
                "pay_slip_id": pid,
                "payslip_name": template['pay_slip_name'],
                "org_id": template['org_id'],
                "basic_pay": float(template['basic_pay']),
                "hra": float(template['hra']),
                "epf": float(template['epf']),
                "professional_tax": float(template['professional_tax']),
                "health_insurance": float(template['health_insurance']),
                "count_basis": None,
                "upto": None,
                "next": None,
                "total_earnings": total_earnings,
                "total_deductions": total_deductions,
                "net_salary": net_salary,
                "custom_earnings": earnings,
                "custom_deductions": deductions,
                "assigned_count": assigned_count,
                "created_at": template['created_at']
            })
        
        return {"success": True, "data": result}
        
    except Exception as e:
        logger.exception("Failed to get salary templates failed")
        raise HTTPException(status_code=500, detail="Failed to get salary templates")
    finally:
        cursor.close()
        return_connection(conn)

async def get_salary_template(pay_slip_id: str, org_id: Optional[str] = None, orgs=None):
    """Get a specific salary template. A template of another organisation answers 404 exactly like a missing one:
    `org_id` (optional query parameter) must match the template's organisation when given, and `orgs` (the caller's
    organisations from the token, None without a token) must contain it. Neither given: unchanged behaviour."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if org_id is not None:
            cursor.execute("SELECT * FROM pay_slip WHERE pay_slip_id = %s AND org_id = %s", (pay_slip_id, org_id))
        else:
            cursor.execute("SELECT * FROM pay_slip WHERE pay_slip_id = %s", (pay_slip_id,))
        template = cursor.fetchone()

        if not template or (orgs is not None and template['org_id'] not in orgs):
            raise HTTPException(status_code=404, detail="Salary template not found")
        
        # Get custom earnings
        cursor.execute("""
            SELECT add_earnings_name as name, add_earnings_amount as amount
            FROM payslip_add_earnings WHERE pay_slip_id = %s
        """, (pay_slip_id,))
        earnings = cursor.fetchall()
        
        # Get custom deductions
        cursor.execute("""
            SELECT add_deduction_name as name, add_deduction_amount as amount
            FROM payslip_add_deduction WHERE pay_slip_id = %s
        """, (pay_slip_id,))
        deductions = cursor.fetchall()
        
        # Calculate totals
        total_earnings = float(template['basic_pay']) + float(template['hra']) + sum(float(e['amount']) for e in earnings)
        total_deductions = float(template['epf']) + float(template['professional_tax']) + float(template['health_insurance']) + sum(float(d['amount']) for d in deductions)
        net_salary = total_earnings - total_deductions
        
        return {
            "success": True,
            "data": {
                "pay_slip_id": template['pay_slip_id'],
                "payslip_name": template['pay_slip_name'],
                "org_id": template['org_id'],
                "basic_pay": float(template['basic_pay']),
                "hra": float(template['hra']),
                "epf": float(template['epf']),
                "professional_tax": float(template['professional_tax']),
                "health_insurance": float(template['health_insurance']),
                "count_basis": None,
                "upto": None,
                "next": None,
                "total_earnings": total_earnings,
                "total_deductions": total_deductions,
                "net_salary": net_salary,
                "custom_earnings": earnings,
                "custom_deductions": deductions,
                "created_at": template['created_at']
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get salary template failed")
        raise HTTPException(status_code=500, detail="Failed to get salary template")
    finally:
        cursor.close()
        return_connection(conn)

async def update_salary_template(pay_slip_id: str, template_data: SalaryTemplateCreate):
    """Update an existing salary template (same validation as create; 404 when it is not this organisation's)"""
    v = payroll_validation.validate_salary_template(template_data)
    org_id = template_data.org_id
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        _lock_templates(cursor, org_id)
        if _template_name_taken(cursor, org_id, v["name"], exclude_id=pay_slip_id):
            raise HTTPException(status_code=409, detail=payroll_validation.MSG_TEMPLATE_NAME_EXISTS)

        amounts = v["amounts"]
        cursor.execute(
            """
            UPDATE pay_slip
            SET pay_slip_name = %s, basic_pay = %s, hra = %s, epf = %s, professional_tax = %s, health_insurance = %s, updated_at = CURRENT_TIMESTAMP
            WHERE pay_slip_id = %s AND org_id = %s
            """,
            (v["name"], amounts["basic_pay"], amounts["hra"], amounts["epf"], amounts["professional_tax"],
             amounts["health_insurance"], pay_slip_id, org_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Salary template not found")

        # Replace the custom lines
        cursor.execute("DELETE FROM payslip_add_earnings WHERE pay_slip_id = %s", (pay_slip_id,))
        cursor.execute("DELETE FROM payslip_add_deduction WHERE pay_slip_id = %s", (pay_slip_id,))
        _insert_custom_lines(cursor, pay_slip_id, v["custom_earnings"], v["custom_deductions"])
        conn.commit()

        updated_templates = await get_salary_templates(org_id)

        return {
            "success": True,
            "message": "Salary template updated successfully",
            "updated_templates": updated_templates.get("data", [])
        }

    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("update_salary_template failed")
        raise HTTPException(status_code=500, detail="Failed to update salary template")
    finally:
        cursor.close()
        return_connection(conn)

async def delete_salary_template(pay_slip_id: str):
    """Delete a salary template"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("DELETE FROM pay_slip WHERE pay_slip_id = %s", (pay_slip_id,))
        conn.commit()
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Salary template not found")
        
        return {"success": True, "message": "Salary template deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("Failed to delete salary template failed")
        raise HTTPException(status_code=500, detail="Failed to delete salary template")
    finally:
        cursor.close()
        return_connection(conn)

async def get_branches_by_org(org_id: str):
    """Get all branches for an organization"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT branch_id, branch_name FROM branch_info 
            WHERE org_id = %s AND status = 1
            ORDER BY branch_name
        """, (org_id,))
        branches = cursor.fetchall()
        
        return {"success": True, "data": branches}
        
    except Exception as e:
        logger.exception("Failed to get branches failed")
        raise HTTPException(status_code=500, detail="Failed to get branches")
    finally:
        cursor.close()
        return_connection(conn)

async def get_departments_by_org(org_id: str):
    """Get all departments for an organization"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("""
            SELECT dept_id as department_id, dept_name as department_name FROM dept_info 
            WHERE org_id = %s AND status = 1
            ORDER BY dept_name
        """, (org_id,))
        departments = cursor.fetchall()
        
        return {"success": True, "data": departments}
        
    except Exception as e:
        logger.exception("Failed to get departments failed")
        raise HTTPException(status_code=500, detail="Failed to get departments")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_by_branch_department(org_id: str, branch_id: str = None, department_id: str = None, pay_slip_id: str = None):
    """Get staff filtered by branch and department"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT DISTINCT u.user_id as staff_id, u.name as staff_name, u.emp_id,
                   COALESCE(b.branch_name, 'N/A') as branch_name, 
                   COALESCE(d.dept_name, 'N/A') as department_name,
                   CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END as is_assigned
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN payroll p ON u.user_id = p.user_id AND p.pay_slip_id = %s AND p.org_id = %s
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
        """
        params = [org_id, org_id, pay_slip_id, org_id, org_id]
        
        if branch_id:
            query += " AND u.branch_id = %s"
            params.append(int(branch_id))
        
        if department_id:
            query += " AND u.dept_id = %s"
            params.append(department_id)
        
        query += " ORDER BY u.name"
        
        cursor.execute(query, tuple(params))
        staff = cursor.fetchall()
        
        return {"success": True, "data": staff}
        
    except Exception as e:
        logger.exception("Failed to get staff failed")
        raise HTTPException(status_code=500, detail="Failed to get staff")
    finally:
        cursor.close()
        return_connection(conn)

async def assign_payslip_to_staff(org_id: str, staff_id: str, pay_slip_id: str):
    """Assign a payslip to a staff member"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if staff is already assigned to any payslip
        cursor.execute("""
            SELECT p.pay_slip_name 
            FROM payroll pr
            JOIN pay_slip p ON pr.pay_slip_id = p.pay_slip_id
            WHERE pr.org_id = %s AND pr.user_id = %s
        """, (org_id, staff_id))
        
        existing = cursor.fetchone()
        if existing:
            return {
                "success": False, 
                "message": f"Already assigned to '{existing['pay_slip_name']}'",
                "payslip_name": existing['pay_slip_name']
            }
        
        # Insert new assignment
        cursor.execute("""
            INSERT INTO payroll (org_id, user_id, pay_slip_id)
            VALUES (%s, %s, %s)
        """, (org_id, staff_id, pay_slip_id))
        
        conn.commit()
        
        return {"success": True, "message": "Payslip assigned successfully"}
        
    except Exception as e:
        conn.rollback()
        import traceback
        error_details = traceback.format_exc()
        print(f"Full error traceback:\n{error_details}")
        print(f"Values - org_id: {org_id}, staff_id: {staff_id}, pay_slip_id: {pay_slip_id}")
        logger.exception("Database error failed")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        cursor.close()
        return_connection(conn)

async def get_employee_assigned_payslip(org_id: str, staff_id: str):
    """Get the payslip assigned to a specific employee"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT pr.pay_slip_id
            FROM payroll pr
            WHERE pr.org_id = %s AND pr.user_id = %s
            LIMIT 1
        """, (org_id, staff_id))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "message": "No payslip assigned"}
        return {"success": True, "pay_slip_id": row['pay_slip_id']}
    except Exception as e:
        logger.exception("Failed to get employee payslip failed")
        raise HTTPException(status_code=500, detail="Failed to get employee payslip")
    finally:
        cursor.close()
        return_connection(conn)

async def get_payslip_preview(org_id: str, staff_id: str, pay_slip_id: str, month: Optional[int] = None,
                              year: Optional[int] = None):
    """Payslip preview of a staff member for a month (default: the current IST month).

    The earnings / deductions / net pay are the template's figures (the app labels it a preview). Additive fields:
    `month_number`, `fine` (the attendance-rule fine of that month for this template's basic pay, the same figure the
    salary breakdown uses), `net_pay_after_fine`, `late_days`, and the paid state of the month (`paid`, `paid_on`,
    `paid_date`, `paid_net`)."""
    today = rules.ist_today()
    month = today.month if month is None else payroll_validation.parse_month_number(month)
    if year is None:
        year = today.year
    elif not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year must be between 2000 and 2100")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Get org name
        cursor.execute("SELECT org_name FROM org_info WHERE org_id = %s", (org_id,))
        org = cursor.fetchone()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        # Get staff details
        cursor.execute("""
            SELECT u.name, u.emp_id,
                   COALESCE(b.branch_name, 'N/A') as branch_name,
                   COALESCE(d.dept_name, 'N/A') as department_name,
                   ua.acc_no, ua.ifsc_code
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN users_additional_info ua ON u.user_id = ua.user_id
            WHERE u.user_id = %s AND u.parent_id = %s
        """, (org_id, org_id, staff_id, org_id))
        staff = cursor.fetchone()
        if not staff:
            raise HTTPException(status_code=404, detail="Staff not found")

        # Get payslip details
        cursor.execute("""
            SELECT pay_slip_name, basic_pay, hra, epf, professional_tax, health_insurance
            FROM pay_slip WHERE pay_slip_id = %s AND org_id = %s
        """, (pay_slip_id, org_id))
        payslip = cursor.fetchone()
        if not payslip:
            raise HTTPException(status_code=404, detail="Payslip not found")

        # Get custom earnings
        cursor.execute("""
            SELECT add_earnings_name as name, add_earnings_amount as amount
            FROM payslip_add_earnings WHERE pay_slip_id = %s
        """, (pay_slip_id,))
        earnings = cursor.fetchall()

        # Get custom deductions
        cursor.execute("""
            SELECT add_deduction_name as name, add_deduction_amount as amount
            FROM payslip_add_deduction WHERE pay_slip_id = %s
        """, (pay_slip_id,))
        deductions = cursor.fetchall()

        # Calculate totals
        total_earnings = float(payslip['basic_pay']) + float(payslip['hra']) + sum(float(e['amount']) for e in earnings)
        total_deductions = float(payslip['epf']) + float(payslip['professional_tax']) + float(payslip['health_insurance']) + sum(float(d['amount']) for d in deductions)
        net_pay = total_earnings - total_deductions

        # Attendance-rule fine of the month (shared with the salary engine) and the paid state of the month.
        figures = _guarded(cursor, "sp_prev_figures",
                           lambda: salary_engine.month_attendance_figures(org_id, staff_id, year, month, cursor=cursor),
                           None)
        fine = 0.0
        late_days = 0
        if figures:
            late_days = figures['late_days']
            fine = payroll_engine.fine_amount(float(payslip['basic_pay']), figures['penalty']['penalty_days'],
                                              figures['working_days'])

        def _read_paid():
            cursor.execute(
                """SELECT net_amount, (paid_at AT TIME ZONE %s)::date AS paid_date
                   FROM salary_payments
                   WHERE org_id = %s AND user_id = %s AND pay_month = %s AND pay_year = %s""",
                (salary_engine.IST_TZ, org_id, staff_id, month, year))
            return cursor.fetchone()
        paid = _guarded(cursor, "sp_prev_paid", _read_paid, None)
        paid_on = paid['paid_date'].isoformat() if paid and paid['paid_date'] else None

        return {
            "success": True,
            "data": {
                "org_name": org['org_name'],
                "payslip_name": payslip['pay_slip_name'],
                "month": MONTH_NAMES[month],
                "month_number": month,
                "year": year,
                "staff_name": staff['name'],
                "emp_id": staff['emp_id'],
                "department": staff['department_name'],
                "branch": staff['branch_name'],
                "account_number": staff['acc_no'] or 'N/A',
                "ifsc_code": staff['ifsc_code'] or 'N/A',
                "earnings": [
                    {"name": "Basic Pay", "amount": float(payslip['basic_pay'])},
                    {"name": "HRA", "amount": float(payslip['hra'])},
                    *[{"name": e['name'], "amount": float(e['amount'])} for e in earnings]
                ],
                "deductions": [
                    {"name": "EPF", "amount": float(payslip['epf'])},
                    {"name": "Professional Tax", "amount": float(payslip['professional_tax'])},
                    {"name": "Health Insurance", "amount": float(payslip['health_insurance'])},
                    *[{"name": d['name'], "amount": float(d['amount'])} for d in deductions]
                ],
                "total_earnings": total_earnings,
                "total_deductions": total_deductions,
                "net_pay": net_pay,
                "fine": fine,
                "net_pay_after_fine": round(net_pay - fine, 2),
                "late_days": late_days,
                "paid": paid is not None,
                "paid_on": paid_on,
                "paid_date": paid_on,
                "paid_net": float(paid['net_amount']) if paid else None,
            }
        }

    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to get preview")
        raise HTTPException(status_code=500, detail="Failed to get preview")
    finally:
        cursor.close()
        return_connection(conn)
