from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from datetime import datetime
from utils.timezone import get_utc_now, IST
from pydantic import BaseModel
from typing import List, Optional
import os
import uuid
from psycopg2.extras import RealDictCursor
from core_functions import data_lifecycle


class AddCompanyRequest(BaseModel):
    user_id: str
    mobile_number: Optional[str] = None
    email: Optional[str] = None
    org_name: str
    org_address: str
    org_gst: str = ''
    gst_number: Optional[str] = None
    departments: List[str] = []
    branches: List[str]
    pan_number: Optional[str] = None
    company_website: Optional[str] = None
    cin_number: Optional[str] = None
    tan_number: Optional[str] = None
    msme_number: Optional[str] = None
    business_category: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    pincode: Optional[int] = None

async def add_company(data: AddCompanyRequest):
    if not all([data.user_id, data.org_name, data.branches]):
        raise HTTPException(status_code=400, detail="User ID, company name, and branches are required")
        
    if not data.branches or len(data.branches) == 0:
        raise HTTPException(status_code=400, detail="At least one branch is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if user exists
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (data.user_id,))
        existing_user = cursor.fetchone()
        if not existing_user:
            raise HTTPException(status_code=400, detail="User not found")
        
        # Generate separate org_id for the new company
        org_id = str(uuid.uuid4())
        
        # Insert organization with separate org_id and parent_id = user_id
        cursor.execute(
            "INSERT INTO org_info (org_id, org_name, org_address, org_latitude, org_longitude, org_gst, parent_id, state, district, pincode, created_at) VALUES (%s, %s, %s, 0, 0, %s, %s, %s, %s, %s, %s)",
            (org_id, data.org_name, data.org_address, data.org_gst, data.user_id, data.state, data.district, data.pincode, get_utc_now())
        )
        
        # Insert branches
        branch_ids = []
        for branch_name in data.branches:
            if branch_name.strip():
                # Get next branch_id (auto-increment)
                cursor.execute("SELECT COALESCE(MAX(branch_id), 0) + 1 as next_id FROM branch_info")
                branch_id = cursor.fetchone()['next_id']
                cursor.execute(
                    "INSERT INTO branch_info (branch_id, org_id, branch_name, branch_address, gps_location, status, created_at) VALUES (%s, %s, %s, %s, POINT(0, 0), %s, %s)",
                    (branch_id, org_id, branch_name.strip(), '', 1, get_utc_now())
                )
                branch_ids.append(branch_id)
        
        # Insert departments (if provided and not empty)
        dept_ids = []
        departments_to_insert = [d.strip() for d in data.departments if d.strip()]
        if departments_to_insert:
            for dept_name in departments_to_insert:
                dept_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO dept_info (org_id, dept_id, dept_name, created_at) VALUES (%s, %s, %s, %s)",
                    (org_id, dept_id, dept_name, get_utc_now())
                )
                dept_ids.append(dept_id)
        
        # Insert company additional info if provided
        if any([data.pan_number, data.company_website, data.cin_number, data.tan_number, data.msme_number, data.business_category, data.gst_number]):
            cursor.execute(
                "INSERT INTO company_additional_info (user_id, org_id, pan_number, company_website, cin_number, tan_number, msme_number, business_category, gst_number, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (data.user_id, org_id, data.pan_number or '', data.company_website or '', data.cin_number or '', data.tan_number or '', data.msme_number or '', data.business_category or '', data.gst_number or '', get_utc_now())
            )
        
        conn.commit()
        
        return {
            "success": True, 
            "message": "Company added successfully!", 
            "user_id": data.user_id,
            "org_id": org_id,
            "org_name": data.org_name,
            "role_id": 1
        }
        
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to add company")
    finally:
        cursor.close()
        return_connection(conn)

async def get_company_details(org_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get organization details with user info
        cursor.execute(
            "SELECT o.*, u.mobile_number, u.email FROM org_info o LEFT JOIN users u ON o.parent_id = u.user_id WHERE o.org_id = %s",
            (org_id,)
        )
        company = cursor.fetchone()
        
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        
        # Get org logo URL from S3
        from ml.s3 import get_org_logo_url
        logo_url = get_org_logo_url(org_id)
        company['org_logo'] = logo_url
        company['logo_url'] = logo_url
        
        # Get additional company info if exists
        cursor.execute(
            "SELECT * FROM company_additional_info WHERE org_id = %s",
            (org_id,)
        )
        additional_info = cursor.fetchone()
        
        if additional_info:
            company.update(additional_info)
        
        return {
            "success": True,
            "data": company
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to get company details")
    finally:
        cursor.close()
        return_connection(conn)

async def get_user_companies(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            "SELECT org_id, org_name, org_address, org_gst, created_at FROM org_info WHERE parent_id = %s ORDER BY created_at ASC",
            (user_id,)
        )
        companies = cursor.fetchall()
        
        return {
            "success": True,
            "data": companies
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get user companies")
    finally:
        cursor.close()
        return_connection(conn)

async def delete_company(org_id: str, user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check total companies for user
        cursor.execute(
            "SELECT COUNT(*) as count FROM org_info WHERE parent_id = %s",
            (user_id,)
        )
        count = cursor.fetchone()['count']
        
        if count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the only company")
        
        # Delete all staff in this organization
        cursor.execute("DELETE FROM users WHERE parent_id = %s", (org_id,))
        
        # Delete company additional info
        cursor.execute("DELETE FROM company_additional_info WHERE org_id = %s", (org_id,))
        
        # Delete departments
        cursor.execute("DELETE FROM dept_info WHERE org_id = %s", (org_id,))
        
        # Delete branches
        cursor.execute("DELETE FROM branch_info WHERE org_id = %s", (org_id,))
        
        # Delete shifts
        cursor.execute("DELETE FROM shift WHERE org_id = %s", (org_id,))
        
        # Delete company
        cursor.execute("DELETE FROM org_info WHERE org_id = %s AND parent_id = %s", (org_id, user_id))
        
        conn.commit()
        
        # Get remaining companies for this user
        cursor.execute(
            "SELECT org_id, org_name, org_address, org_gst, created_at FROM org_info WHERE parent_id = %s ORDER BY created_at ASC",
            (user_id,)
        )
        remaining_companies = cursor.fetchall()
        
        return {
            "success": True, 
            "message": "Company deleted successfully",
            "remaining_companies": remaining_companies
        }
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete company")
    finally:
        cursor.close()
        return_connection(conn)

async def update_company(org_id: str, data: AddCompanyRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Update org_info
        cursor.execute(
            "UPDATE org_info SET org_name = %s, org_address = %s, org_gst = %s, state = %s, district = %s, pincode = %s, updated_at = %s WHERE org_id = %s",
            (data.org_name, data.org_address, data.org_gst, data.state, data.district, data.pincode, get_utc_now(), org_id)
        )
        
        # Get parent_id for user updates
        cursor.execute("SELECT parent_id FROM org_info WHERE org_id = %s", (org_id,))
        org = cursor.fetchone()
        
        if org and org['parent_id']:
            # Update mobile and email in users table if provided
            if data.mobile_number or data.email:
                if data.mobile_number and data.email:
                    cursor.execute(
                        "UPDATE users SET mobile_number = %s, email = %s WHERE user_id = %s",
                        (data.mobile_number, data.email, org['parent_id'])
                    )
                elif data.mobile_number:
                    cursor.execute(
                        "UPDATE users SET mobile_number = %s WHERE user_id = %s",
                        (data.mobile_number, org['parent_id'])
                    )
                else:
                    cursor.execute(
                        "UPDATE users SET email = %s WHERE user_id = %s",
                        (data.email, org['parent_id'])
                    )
            
            # Update or insert company additional info
            cursor.execute(
                "SELECT org_id FROM company_additional_info WHERE org_id = %s",
                (org_id,)
            )
            existing_info = cursor.fetchone()
            
            if existing_info:
                cursor.execute(
                    "UPDATE company_additional_info SET pan_number = %s, company_website = %s, cin_number = %s, tan_number = %s, msme_number = %s, business_category = %s, gst_number = %s, updated_at = %s WHERE org_id = %s",
                    (data.pan_number or '', data.company_website or '', data.cin_number or '', data.tan_number or '', data.msme_number or '', data.business_category or '', data.gst_number or '', get_utc_now(), org_id)
                )
            elif any([data.pan_number, data.company_website, data.cin_number, data.tan_number, data.msme_number, data.business_category, data.gst_number]):
                cursor.execute(
                    "INSERT INTO company_additional_info (user_id, org_id, pan_number, company_website, cin_number, tan_number, msme_number, business_category, gst_number, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (org['parent_id'], org_id, data.pan_number or '', data.company_website or '', data.cin_number or '', data.tan_number or '', data.msme_number or '', data.business_category or '', data.gst_number or '', get_utc_now())
                )
        
        # Update branches - delete existing and insert new ones (only if branches provided)
        if data.branches and len(data.branches) > 0:
            # Delete branches not in the new list
            cursor.execute("DELETE FROM branch_info WHERE org_id = %s", (org_id,))
            
            # Insert new branches
            for branch_name in data.branches:
                if branch_name.strip():
                    cursor.execute("SELECT COALESCE(MAX(branch_id), 0) + 1 as next_id FROM branch_info")
                    branch_id = cursor.fetchone()['next_id']
                    cursor.execute(
                        "INSERT INTO branch_info (branch_id, org_id, branch_name, branch_address, gps_location, status, created_at) VALUES (%s, %s, %s, %s, POINT(0, 0), %s, %s)",
                        (branch_id, org_id, branch_name.strip(), '', 1, get_utc_now())
                    )
        
        # Update departments - only if departments provided and not empty
        departments_to_insert = [d.strip() for d in data.departments if d.strip()]
        if departments_to_insert:
            # Check existing departments
            cursor.execute("SELECT dept_name FROM dept_info WHERE org_id = %s", (org_id,))
            existing_depts = [row['dept_name'] for row in cursor.fetchall()]
            
            # Only update if departments changed
            if set(departments_to_insert) != set(existing_depts):
                cursor.execute("DELETE FROM dept_info WHERE org_id = %s", (org_id,))
                
                for dept_name in departments_to_insert:
                    dept_id = str(uuid.uuid4())
                    cursor.execute(
                        "INSERT INTO dept_info (org_id, dept_id, dept_name, created_at) VALUES (%s, %s, %s, %s)",
                        (org_id, dept_id, dept_name, get_utc_now())
                    )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Company updated successfully!",
            "org_id": org_id,
            "org_name": data.org_name
        }
        
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to update company")
    finally:
        cursor.close()
        return_connection(conn)


async def request_account_deletion(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.user_id, u.mobile_number, u.email, o.org_name
            FROM users u
            LEFT JOIN org_info o ON o.parent_id = u.user_id
            WHERE u.user_id = %s
            LIMIT 1
        """, (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        cursor.execute(
            "SELECT request_id FROM account_deletion_requests WHERE user_id = %s AND status = 'pending'",
            (user_id,)
        )
        if cursor.fetchone():
            return {"success": True, "message": "Deletion request already submitted. Awaiting admin review."}

        request_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO account_deletion_requests (request_id, user_id, org_name, mobile_number, email, status, requested_at)
            VALUES (%s, %s, %s, %s, %s, 'pending', %s)
        """, (request_id, user_id, user["org_name"] or "", user["mobile_number"] or "", user["email"] or "", get_utc_now()))
        conn.commit()
        return {"success": True, "message": "Account deletion request submitted. An admin will review and process it."}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)


async def get_deletion_requests():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT request_id, user_id, org_name, mobile_number, email, status,
                   requested_at, actioned_at
            FROM account_deletion_requests
            WHERE status = 'pending'
            ORDER BY requested_at DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                "request_id":   r["request_id"],
                "user_id":      r["user_id"],
                "org_name":     r["org_name"] or "—",
                "mobile":       r["mobile_number"] or "—",
                "email":        r["email"] or "—",
                "status":       r["status"],
                "requested_at": r["requested_at"].strftime("%d %b %Y %H:%M") if r["requested_at"] else "—",
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)


def account_delete_anonymises() -> bool:
    """ACCOUNT_DELETE_ANONYMISE (default OFF = the hard delete below). ON: personal data is anonymised in place
    (core_functions/data_lifecycle.py::anonymise_account, planner D-14) and every row and reference stays."""
    return (os.getenv("ACCOUNT_DELETE_ANONYMISE") or "").strip().lower() in ("1", "true", "yes", "on")


async def delete_user_account(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if account_delete_anonymises():
            # ONE transaction: any failure rolls everything back, S3 objects are removed only after the commit
            cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="User not found")
            summary = data_lifecycle.anonymise_account(cursor, user_id)
            conn.commit()
            data_lifecycle.finish_account_cleanup(summary)
            return {"success": True, "message": "Account deleted and personal data anonymised successfully"}

        # Get all organizations owned by this user (org_info.parent_id = user_id)
        cursor.execute("SELECT org_id FROM org_info WHERE parent_id = %s", (user_id,))
        orgs = cursor.fetchall()
        face_user_ids = [user_id]      # whose Rekognition faces are deleted after the commit

        for org in orgs:
            org_id = org['org_id']

            # Get all staff/employees in this organization
            cursor.execute("SELECT user_id FROM users WHERE parent_id = %s", (org_id,))
            staff_users = cursor.fetchall()
            face_user_ids.extend(s['user_id'] for s in staff_users)

            # Delete staff-related data
            for staff in staff_users:
                staff_user_id = staff['user_id']
                cursor.execute("DELETE FROM users_additional_info WHERE user_id = %s", (staff_user_id,))
                cursor.execute("DELETE FROM attendance WHERE user_id = %s", (staff_user_id,))
                cursor.execute("DELETE FROM payroll WHERE user_id = %s", (staff_user_id,))
                cursor.execute("DELETE FROM odometer_readings WHERE user_id = %s", (staff_user_id,))
                cursor.execute("DELETE FROM travel_bill_expenses WHERE user_id = %s", (staff_user_id,))
                cursor.execute("DELETE FROM incentive_templates WHERE user_id = %s", (staff_user_id,))
                # Delete requests and leave_dates
                cursor.execute("DELETE FROM leave_dates WHERE request_id IN (SELECT request_id FROM requests WHERE user_id = %s)", (staff_user_id,))
                cursor.execute("DELETE FROM requests WHERE user_id = %s", (staff_user_id,))
            
            # Delete all staff in this organization
            cursor.execute("DELETE FROM users WHERE parent_id = %s", (org_id,))
            
            # Delete org-related data
            cursor.execute("DELETE FROM leads_assignment_info WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM leads_info WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM leads_category_info WHERE org_id = %s", (org_id,))
            # Delete incentive details first
            cursor.execute("DELETE FROM incentive_count_details WHERE incentive_id IN (SELECT incentive_id FROM incentive_templates WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM incentive_turnover_details WHERE incentive_id IN (SELECT incentive_id FROM incentive_templates WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM incentive_overtime_details WHERE incentive_id IN (SELECT incentive_id FROM incentive_templates WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM incentive_bonus_details WHERE incentive_id IN (SELECT incentive_id FROM incentive_templates WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM incentive_templates WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM shift WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM punchapp_manager_permissions WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM company_additional_info WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM firm_holidays WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM daily_hourly_wages WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM activity_tracking WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM payslip_add_earnings WHERE pay_slip_id IN (SELECT pay_slip_id FROM pay_slip WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM payslip_add_deduction WHERE pay_slip_id IN (SELECT pay_slip_id FROM pay_slip WHERE org_id = %s)", (org_id,))
            cursor.execute("DELETE FROM pay_slip WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM dept_info WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM branch_info WHERE org_id = %s", (org_id,))
            cursor.execute("DELETE FROM org_info WHERE org_id = %s", (org_id,))
        
        # Delete user's own data
        cursor.execute("DELETE FROM users_additional_info WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM company_additional_info WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))

        # Mark any pending deletion request as actioned: in the SAME transaction as the deletes (one commit)
        cursor.execute(
            "UPDATE account_deletion_requests SET status = 'deleted', actioned_at = %s WHERE user_id = %s AND status = 'pending'",
            (get_utc_now(), user_id)
        )
        conn.commit()
        for face_user_id in face_user_ids:         # best effort, never raises (failures are logged)
            data_lifecycle.remove_face_data(face_user_id)
        return {"success": True, "message": "Account and all related data deleted successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to delete account")
    finally:
        cursor.close()
        return_connection(conn)

