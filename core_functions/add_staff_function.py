from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from utils.timezone import get_utc_now, IST, ist_today
from core_functions.pricing import TRIAL_STAFF_LIMIT
from pydantic import BaseModel, Field
from typing import Optional, List
import re
import uuid
import logging
from utils.auth_mode import is_email_auth
from core_functions import data_lifecycle

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_user_id(user_id) -> str:
    """Ids from the app / mock look like `org-0001`, real ones are UUIDs: accept 1-64 letters, digits, `-` and `_`.

    Only the shape is checked here; the org-ownership checks of each function stay in place. Raises HTTP 400.
    """
    if not isinstance(user_id, str) or not _ID_RE.match(user_id):
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    return user_id


def _check_staff_email(cursor, data, exclude_user_id=None):
    """USER_AUTH=email: staff sign in with their email, so every staff record needs a valid one that no other live account uses.

    The email is stored trimmed and lower-case. In `otp` mode (mobile sign-in) nothing changes: the email stays optional.
    """
    if not is_email_auth():
        return
    email = (data.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="A valid email address is required: staff sign in with their email.")
    sql = "SELECT user_id FROM users WHERE LOWER(email) = LOWER(%s) AND status IN (0, 1)"
    params = [email]
    if exclude_user_id:
        sql += " AND user_id <> %s"
        params.append(exclude_user_id)
    cursor.execute(sql + " LIMIT 1", tuple(params))
    if cursor.fetchone():
        raise HTTPException(status_code=400, detail="This email address is already used by another account.")
    data.email = email

def assert_staff_capacity(cursor, org_id: str, lock: bool = False) -> None:
    """The plan's staff limit, enforced the same way for a new staff member and for a reactivation (status 0 -> 1).

    An active plan sets the limit; free trial / no active plan use the trial limit (D-13: 10). The count is the active
    managers and staff of the organisation, including those created under a manager by the old code path (their
    parent_id is the manager's id; migration 034 gives them a manager_id). Raises 403 with the message the app shows.
    `lock=True` first locks the organisation row so two concurrent reactivations cannot both take the last seat."""
    if lock:
        cursor.execute("SELECT org_id FROM org_info WHERE org_id = %s FOR UPDATE", (org_id,))
    cursor.execute("""
        SELECT staff_limit FROM subscription
        WHERE org_id = %s AND status = 'ACTIVE' AND end_date >= %s
        ORDER BY created_at DESC LIMIT 1
    """, (org_id, ist_today()))
    sub = cursor.fetchone()
    staff_limit = int(sub['staff_limit'] or TRIAL_STAFF_LIMIT) if sub else TRIAL_STAFF_LIMIT
    cursor.execute(
        """SELECT COUNT(*) AS cnt FROM users
           WHERE role_id IN (2,3) AND status = 1
           AND (parent_id = %s OR manager_id IN (SELECT m.user_id FROM users m WHERE m.parent_id = %s AND m.role_id = 2))""",
        (org_id, org_id)
    )
    current_count = int(cursor.fetchone()['cnt'] or 0)
    if current_count >= staff_limit:
        raise HTTPException(
            status_code=403,
            detail=f'Staff limit reached ({current_count}/{staff_limit}). Please upgrade your plan to add more staff.'
        )


def resolve_manager_id(cursor, candidate: Optional[str], org_id: str) -> Optional[str]:
    """`AddStaffRequest.parent_id` is the REPORTING MANAGER chosen in the form (the organisation id when none): returns
    the manager's user_id to store in users.manager_id, or None for "no manager". The manager must be an active
    manager (role 2) of the same organisation, otherwise 400. users.parent_id always stays the organisation."""
    candidate = (candidate or "").strip()
    if not candidate or candidate == org_id:
        return None
    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = %s AND role_id = 2 AND status = 1 AND parent_id = %s",
        (candidate, org_id)
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=400, detail="The selected manager was not found in this organisation.")
    return candidate


class AddStaffRequest(BaseModel):
    name: str
    emp_id: str
    mobile_number: str
    email: str
    user_id: str = Field(..., description="Organization ID (same as user_id for org owner)")
    branch_name: Optional[str] = None
    dept_name: Optional[str] = None
    parent_id: Optional[str] = None
    gender: Optional[str] = None
    shift_id: Optional[int] = None  # omitted: add falls back to shift 1, update keeps the stored shift
    is_field_staff: Optional[int] = 0
    
    # Additional info fields
    dob: Optional[str] = None  # Frontend sends as string
    address: Optional[str] = None
    emergency_contact_no: Optional[str] = None
    blood_group: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    acc_no: Optional[str] = None
    ifsc_code: Optional[str] = None

class BranchInfoResponse(BaseModel):
    branch_id: str
    branch_name: str

class DepartmentInfoResponse(BaseModel):
    dept_id: str
    dept_name: str

class ManagerInfoResponse(BaseModel):
    user_id: str
    name: str

class BranchResponse(BaseModel):
    branch_id: str
    branch_name: str
    branch_address: str
    status: int

class DepartmentResponse(BaseModel):
    dept_id: str
    dept_name: str
    status: int

class ManagerResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    dept_id: Optional[str] = None

class RoleResponse(BaseModel):
    role_id: int
    role_name: str
    description: str

class StaffDetailResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: Optional[str] = None
    branch_name: Optional[str] = None
    role_name: Optional[str] = None
    status: int
    is_field_staff: Optional[int] = 0
    branch_id: Optional[str] = None
    dept_id: Optional[str] = None
    org_id: Optional[str] = None
    
    # Additional info
    dob: Optional[str] = None
    gender: Optional[str] = None
    blood_group: Optional[str] = None
    address: Optional[str] = None
    emergency_contact_no: Optional[str] = None
    acc_no: Optional[str] = None
    ifsc_code: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    shift_id: Optional[int] = None
    profile_photo_url: Optional[str] = None

async def get_branches(user_id: str, role_id: int):
    """Get all branches for organization"""
    if not user_id or user_id == 'undefined' or user_id == 'null':
        raise HTTPException(status_code=400, detail="Valid User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if role_id == 2:
            # Manager: only return assigned branches
            cursor.execute(
                """
                SELECT DISTINCT CAST(b.branch_id AS VARCHAR) as branch_id, b.branch_name, b.branch_address, b.status
                FROM manager_assignments ma
                JOIN users mu ON mu.user_id = ma.user_id
                JOIN branch_info b ON ma.branch_id = b.branch_id AND b.org_id = COALESCE(mu.parent_id, mu.org_id)
                WHERE ma.user_id = %s AND b.status = 1
                ORDER BY b.branch_name
                """,
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT CAST(branch_id AS VARCHAR) as branch_id, branch_name, branch_address, status FROM branch_info WHERE org_id = %s AND status = 1 ORDER BY branch_name",
                (user_id,)
            )
        branches = cursor.fetchall()
        return [BranchResponse(**branch) for branch in branches]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get branches")
    finally:
        cursor.close()
        return_connection(conn)

async def get_departments(user_id: str, role_id: int):
    """Get all departments for organization"""
    if not user_id or user_id == 'undefined' or user_id == 'null':
        raise HTTPException(status_code=400, detail="Valid User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if role_id == 2:
            # Manager: only return assigned departments
            cursor.execute(
                """
                SELECT DISTINCT d.dept_id, d.dept_name, d.status
                FROM manager_assignments ma
                JOIN users mu ON mu.user_id = ma.user_id
                JOIN dept_info d ON ma.dept_id = d.dept_id AND d.org_id = COALESCE(mu.parent_id, mu.org_id)
                WHERE ma.user_id = %s AND d.status = 1
                ORDER BY d.dept_name
                """,
                (user_id,)
            )
        else:
            cursor.execute(
                "SELECT dept_id, dept_name, status FROM dept_info WHERE org_id = %s AND status = 1 ORDER BY dept_name",
                (user_id,)
            )
        departments = cursor.fetchall()
        return [DepartmentResponse(**dept) for dept in departments]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get departments")
    finally:
        cursor.close()
        return_connection(conn)

async def get_managers(user_id: str, role_id: int, dept_id: Optional[str] = None):
    """Get potential managers (role_id=2 only)"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if dept_id:
            query = "SELECT DISTINCT user_id, name, emp_id, dept_id FROM users WHERE parent_id = %s AND dept_id = %s AND role_id = 2 AND status = 1 ORDER BY name"
            params = (user_id, dept_id)
        else:
            query = "SELECT DISTINCT user_id, name, emp_id, dept_id FROM users WHERE parent_id = %s AND role_id = 2 AND status = 1 ORDER BY name"
            params = (user_id,)
        
        cursor.execute(query, params)
        managers = cursor.fetchall()
        
        return [ManagerResponse(**manager) for manager in managers]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get managers")
    finally:
        cursor.close()
        return_connection(conn)

async def get_branch_info(user_id: str, role_id: int):
    """Get branch info for a user based on user_id and role_id"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT b.branch_id, b.branch_name
            FROM users u
            JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = COALESCE(u.parent_id, u.org_id)
            WHERE u.user_id = %s AND u.role_id = %s AND u.status = 1
        """
        cursor.execute(query, (user_id, role_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Branch info not found for user")
        
        return BranchInfoResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get branch info")
    finally:
        cursor.close()
        return_connection(conn)

async def get_department_info(user_id: str, role_id: int):
    """Get department info for a user based on user_id and role_id"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT d.dept_id, d.dept_name
            FROM users u
            JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = COALESCE(u.parent_id, u.org_id)
            WHERE u.user_id = %s AND u.role_id = %s AND u.status = 1
        """
        cursor.execute(query, (user_id, role_id))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Department info not found for user")
        
        return DepartmentInfoResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get department info")
    finally:
        cursor.close()
        return_connection(conn)

async def get_manager(user_id: str):
    """Get manager info - users who have this user_id as parent_id and role=2"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT DISTINCT u.user_id, u.name
            FROM users u
            WHERE u.parent_id = %s AND u.role_id = 2 AND u.status = 1
        """
        cursor.execute(query, (user_id,))
        results = cursor.fetchall()
        
        return [ManagerInfoResponse(**manager) for manager in results]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get manager info")
    finally:
        cursor.close()
        return_connection(conn)

async def add_staff(data: AddStaffRequest):
    """Add new staff member with role validation"""
    # USER_AUTH=email: staff sign in with their email, so the mobile number is optional (the email is validated below).
    data.mobile_number = (data.mobile_number or "").strip()
    if is_email_auth():
        if not all([data.name, data.emp_id, data.user_id]):
            raise HTTPException(status_code=400, detail="Name, Employee ID, and User ID are required")
    elif not all([data.name, data.emp_id, data.mobile_number, data.user_id]):
        raise HTTPException(status_code=400, detail="Name, Employee ID, Mobile Number, and User ID are required")

    # user_id from frontend is actually org_id
    org_id = data.user_id
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # ── Staff limit check (shared with the reactivation in set_staff_status) ──
        assert_staff_capacity(cursor, org_id)
        # ──────────────────────────────────────────────────────────────────
        # Check if employee ID or mobile_number already exists (email is optional)
        # A blank mobile (email mode) is never compared: several staff may have none.
        clauses, params = ["emp_id = %s"], [data.emp_id]
        if data.mobile_number:
            clauses.append("mobile_number = %s")
            params.append(data.mobile_number)
        if data.email:
            clauses.append("email = %s")
            params.append(data.email)
        cursor.execute(
            f"SELECT * FROM users WHERE ({' OR '.join(clauses)}) AND parent_id = %s",
            (*params, org_id)
        )
        existing_user = cursor.fetchone()
        if existing_user:
            raise HTTPException(status_code=400, detail="Employee ID, mobile number, or email already exists")
        _check_staff_email(cursor, data)
        
        branch_id = None
        dept_id = None
        
        # Handle branch - create if doesn't exist
        if data.branch_name and data.branch_name.strip():
            cursor.execute(
                "SELECT branch_id FROM branch_info WHERE branch_name = %s AND org_id = %s AND status = 1",
                (data.branch_name.strip(), org_id)
            )
            branch_result = cursor.fetchone()
            
            if branch_result:
                branch_id = branch_result['branch_id']
            else:
                # Create new branch
                cursor.execute(
                    "SELECT COALESCE(MAX(branch_id), 0) + 1 as next_id FROM branch_info WHERE org_id = %s",
                    (org_id,)
                )
                next_branch_id = cursor.fetchone()['next_id']
                
                cursor.execute(
                    """INSERT INTO branch_info (org_id, branch_id, branch_name, branch_address, status, created_at, updated_at) 
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (org_id, next_branch_id, data.branch_name.strip(), 
                     "Default Address", 1, get_utc_now(), get_utc_now())
                )
                branch_id = next_branch_id
        
        # Handle department - lookup existing or use General
        dept_id = None
        if data.dept_name and data.dept_name.strip():
            # User selected a specific department
            cursor.execute(
                "SELECT dept_id FROM dept_info WHERE dept_name = %s AND org_id = %s AND status = 1",
                (data.dept_name.strip(), org_id)
            )
            dept_result = cursor.fetchone()
            
            if dept_result:
                dept_id = dept_result['dept_id']
            else:
                # Create new department
                dept_id = str(uuid.uuid4())
                cursor.execute(
                    """INSERT INTO dept_info (org_id, dept_id, dept_name, status, created_at, updated_at) 
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (org_id, dept_id, data.dept_name.strip(), 1, get_utc_now(), get_utc_now())
                )
        
        # If no department selected or found, use General department
        if not dept_id:
            cursor.execute(
                "SELECT dept_id FROM dept_info WHERE dept_name = 'General' AND org_id = %s AND status = 1",
                (org_id,)
            )
            dept_result = cursor.fetchone()
            if dept_result:
                dept_id = dept_result['dept_id']
            else:
                # Should not happen as General is created during registration
                raise HTTPException(status_code=500, detail="General department not found. Please contact support.")
        
        # Generate user ID
        new_user_id = str(uuid.uuid4())
        
        # parent_id is ALWAYS the organisation. The reporting manager chosen in the form (data.parent_id) goes to
        # manager_id; the old code stored the manager in parent_id, which hid the staff member from every org query and
        # broke the employee login (org join on parent_id).
        parent_id = org_id
        manager_id = resolve_manager_id(cursor, data.parent_id, org_id)

        # Always set role_id to 3 (Staff)
        cursor.execute(
            """INSERT INTO users
               (user_id, name, emp_id, branch_id, role_id, dept_id, parent_id, manager_id, mobile_number, email, status, is_field_staff, push_token, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (new_user_id, data.name, data.emp_id, branch_id, 3, dept_id,
             parent_id, manager_id, data.mobile_number, data.email, 1, data.is_field_staff if data.is_field_staff is not None else 0, '', get_utc_now(), get_utc_now())
        )
        
        # Parse date if provided
        dob_date = None
        if data.dob and data.dob.strip():
            try:
                dob_date = datetime.strptime(data.dob.strip(), '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        # Insert additional info
        cursor.execute(
            """INSERT INTO users_additional_info 
               (user_id, acc_no, ifsc_code, bank_name, bank_branch, dob, gender, blood_group, 
                address, emergency_contact_no, shift_id, created_at, updated_at) 
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (new_user_id, data.acc_no or "", data.ifsc_code or "", data.bank_name or "", 
             data.bank_branch or "", dob_date, data.gender or "", 
             data.blood_group or "", data.address or "", data.emergency_contact_no or "", 
             data.shift_id if data.shift_id is not None else 1, get_utc_now(), get_utc_now())
        )
        
        conn.commit()

        # Send WhatsApp onboarding message (non-blocking, does not affect staff creation)
        try:
            if not data.mobile_number:
                return {"success": True, "message": "Staff added successfully!", "user_id": new_user_id}
            from core_functions.whatsapp_service import send_employee_onboard_whatsapp
            cursor.execute("SELECT name FROM users WHERE user_id = %s", (org_id,))
            org_row = cursor.fetchone()
            org_name = org_row['name'] if org_row else 'Your Organization'
            wa_sent = send_employee_onboard_whatsapp(
                mobile=data.mobile_number,
                name=data.name,
                org=org_name
            )
            logger.info(
                "WhatsApp onboarding send result for staff %s (%s): %s",
                data.name,
                data.mobile_number,
                wa_sent
            )
        except Exception:
            logger.exception("WhatsApp onboarding failed after staff creation")

        return {
            "success": True,
            "message": "Staff added successfully!",
            "user_id": new_user_id
        }
        
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to add staff")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_detail(user_id: str):
    """Get detailed information of a specific staff member"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.status, u.is_field_staff,
                   CAST(u.branch_id AS VARCHAR) AS branch_id, u.dept_id, u.parent_id AS org_id,
                   d.dept_name, b.branch_name, r.role_name,
                   ua.dob, ua.gender, ua.blood_group, ua.address, ua.emergency_contact_no,
                   ua.acc_no, ua.ifsc_code, ua.bank_name, ua.bank_branch, ua.shift_id
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN role_info r ON u.role_id = r.role_id
            LEFT JOIN users_additional_info ua ON u.user_id = ua.user_id
            WHERE u.user_id = %s AND COALESCE(u.status, 1) IN (0, 1)
        """
        
        cursor.execute(query, (user_id,))
        staff = cursor.fetchone()
        
        if not staff:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Format date for frontend
        if staff['dob']:
            staff['dob'] = staff['dob'].strftime('%Y-%m-%d')

        # Fetch profile photo URL from S3
        from ml.s3 import get_employee_profile_url
        staff_dict = dict(staff)
        staff_dict['profile_photo_url'] = get_employee_profile_url(user_id)

        return StaffDetailResponse(**staff_dict)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get staff details")
    finally:
        cursor.close()
        return_connection(conn)

async def update_staff(user_id: str, data: AddStaffRequest):
    """Update existing staff member"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if staff exists and get current parent_id
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (user_id,))
        existing_user = cursor.fetchone()
        if not existing_user:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # parent_id (the organisation) is never changed by an edit. A reporting manager in the request
        # (data.parent_id: the manager's id, or the organisation id for "none") updates manager_id; omitted keeps it.
        parent_id = existing_user['parent_id']

        branch_id = None
        dept_id = None
        
        # Handle branch - create if doesn't exist
        if data.branch_name and data.branch_name.strip():
            cursor.execute(
                "SELECT branch_id FROM branch_info WHERE branch_name = %s AND org_id = %s",
                (data.branch_name.strip(), data.user_id)
            )
            branch_result = cursor.fetchone()
            
            if branch_result:
                branch_id = branch_result['branch_id']
            else:
                # Create new branch
                cursor.execute(
                    "SELECT COALESCE(MAX(branch_id), 0) + 1 as next_id FROM branch_info WHERE org_id = %s",
                    (data.user_id,)
                )
                next_branch_id = cursor.fetchone()['next_id']
                
                cursor.execute(
                    """INSERT INTO branch_info (org_id, branch_id, branch_name, branch_address,
                       gps_location, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, POINT(%s, %s), %s, %s)""",
                    (data.user_id, next_branch_id, data.branch_name.strip(),
                     "Default Address", 0.0, 0.0, get_utc_now(), get_utc_now())
                )
                branch_id = next_branch_id
        
        # Handle department - create if doesn't exist
        if data.dept_name and data.dept_name.strip():
            cursor.execute(
                "SELECT dept_id FROM dept_info WHERE dept_name = %s AND org_id = %s",
                (data.dept_name.strip(), data.user_id)
            )
            dept_result = cursor.fetchone()
            
            if dept_result:
                dept_id = dept_result['dept_id']
            else:
                # Create new department
                dept_id = str(uuid.uuid4())
                cursor.execute(
                    """INSERT INTO dept_info (org_id, dept_id, dept_name, created_at, updated_at) 
                       VALUES (%s, %s, %s, %s, %s)""",
                    (data.user_id, dept_id, data.dept_name.strip(), get_utc_now(), get_utc_now())
                )
        
        _check_staff_email(cursor, data, exclude_user_id=user_id)

        # Reporting manager: given -> validated against the organisation (None = cleared); omitted -> unchanged.
        manager_given = bool((data.parent_id or "").strip())
        manager_id = resolve_manager_id(cursor, data.parent_id, data.user_id) if manager_given else None

        # Update user info. role_id is never written here (an edit must not change the role); branch / dept keep
        # their stored value when the request omits them (COALESCE with NULL), parent_id is preserved.
        cursor.execute(
            """UPDATE users SET
               name = %s, emp_id = %s, branch_id = COALESCE(%s, branch_id), dept_id = COALESCE(%s, dept_id),
               parent_id = %s, mobile_number = %s, email = %s, is_field_staff = %s,
               manager_id = CASE WHEN %s THEN %s ELSE manager_id END, updated_at = %s
               WHERE user_id = %s""",
            (data.name, data.emp_id, branch_id, dept_id,
             parent_id, data.mobile_number, data.email, data.is_field_staff if data.is_field_staff is not None else 0,
             manager_given, manager_id, get_utc_now(), user_id)
        )
        
        # Parse date if provided
        dob_date = None
        if data.dob and data.dob.strip():
            try:
                dob_date = datetime.strptime(data.dob.strip(), '%Y-%m-%d').date()
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
        
        # Update additional info
        cursor.execute(
            """UPDATE users_additional_info SET 
               acc_no = %s, ifsc_code = %s, bank_name = %s, bank_branch = %s, 
               dob = %s, gender = %s, blood_group = %s, address = %s, 
               emergency_contact_no = %s, shift_id = COALESCE(%s, shift_id), updated_at = %s
               WHERE user_id = %s""",
            (data.acc_no or "", data.ifsc_code or "", data.bank_name or "",
             data.bank_branch or "", dob_date, data.gender or "",
             data.blood_group or "", data.address or "", data.emergency_contact_no or "",
             data.shift_id, get_utc_now(), user_id)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Staff updated successfully!"
        }
        
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update staff")
    finally:
        cursor.close()
        return_connection(conn)

async def delete_staff(user_id: str):
    """Soft delete staff member (status 2 = deleted; 0 stays 'inactive', 1 'active')"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if staff exists
        cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        existing_user = cursor.fetchone()
        if not existing_user:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Soft delete: status 2 (login reports 2 as "account deleted"; directory lists only show 0 / 1)
        cursor.execute(
            "UPDATE users SET status = 2, updated_at = %s WHERE user_id = %s",
            (get_utc_now(), user_id)
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Staff deleted successfully!"
        }
        
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete staff")
    finally:
        cursor.close()
        return_connection(conn)

async def set_staff_status(user_id: str, status: int):
    """Activate (1) or deactivate (0) a staff member. A change TO active from any other state (inactive 0, or a
    soft-deleted 2 being restored) is a new seat: the plan's staff limit is enforced exactly as in add_staff
    (`assert_staff_capacity`: active plan limit, trial limit 10), with the organisation row locked so two reactivations
    cannot both take the last seat. Deactivating never checks the limit; an administrator row (role 1) is not counted."""
    if status not in (0, 1):
        raise HTTPException(status_code=400, detail="Status must be 0 or 1")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT user_id, role_id, parent_id, manager_id, status FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if status == 1 and row['status'] != 1 and row['role_id'] in (2, 3):
            org_id = row['parent_id']
            if row.get('manager_id') and org_id == row['manager_id']:
                # created under a manager by the old code path: the organisation is the manager's
                cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (org_id,))
                manager = cursor.fetchone()
                org_id = (manager or {}).get('parent_id') or org_id
            assert_staff_capacity(cursor, org_id, lock=True)
        cursor.execute(
            "UPDATE users SET status = %s, updated_at = %s WHERE user_id = %s RETURNING user_id",
            (status, get_utc_now(), user_id)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        label = "activated" if status == 1 else "deactivated"
        return {"success": True, "message": f"Staff {label} successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Failed to change staff status")
        raise HTTPException(status_code=500, detail="Failed to update staff status")
    finally:
        cursor.close()
        return_connection(conn)


async def permanently_delete_staff(user_id: str, requested_by_role_id: Optional[int] = None):
    """
    PERMANENTLY and irreversibly delete one employee (role_id 2 or 3) and every
    piece of data tied to them — across the database and S3. This is distinct
    from `delete_staff` above, which only soft-deletes (status = 2); soft-deleted staff are purged after the retention
    period by `core_functions/data_lifecycle.py::purge_deleted_staff`, which runs the same table list.

    Safety rules enforced here:
      - Only role_id 2 (Manager) or 3 (Employee) may be targeted. Role_id 1
        (org admin/owner) can never be deleted through this path.
      - Only a caller with role_id == 1 (org admin) may invoke this at all.
      - If the target is a Manager with staff still reporting to them
        (users.parent_id == target user_id), the deletion is blocked — those
        staff must be reassigned first, otherwise they'd be silently orphaned
        (their parent_id would point at a user that no longer exists, which
        would make them vanish from every org-level query that filters by
        parent_id = org_id).

    Deliberately OUT OF SCOPE (left untouched on purpose, not by oversight):
      - Support tickets (admin_subject_request / ticket_replies) submitted by
        this employee are preserved — they may hold org-relevant
        troubleshooting history, and their ownership semantics are murkier
        than the tables below.
      - Chat groups this employee CREATED are preserved as-is (only their own
        membership row is removed) — deleting a shared group would destroy
        other members' history over one person's departure.
      - `action_by`/`actioned_by` references (e.g. a leave request this
        person approved as a manager) are left pointing at the now-deleted
        user_id. This is a harmless historical/audit field on someone else's
        row, not a functional dependency.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if requested_by_role_id != 1:
            raise HTTPException(status_code=403, detail="Only an organization admin can permanently delete an employee.")

        cursor.execute("SELECT user_id, name, role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
        target = cursor.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="Staff member not found")
        if target['role_id'] not in (2, 3):
            raise HTTPException(status_code=403, detail="This action can only be used on Manager or Employee accounts.")

        if target['role_id'] == 2:
            cursor.execute("SELECT COUNT(*) AS cnt FROM users WHERE parent_id = %s", (user_id,))
            subordinate_count = cursor.fetchone()['cnt']
            if subordinate_count > 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"This manager still has {subordinate_count} staff reporting to them. "
                           f"Please reassign those staff to another manager before deleting this account."
                )

        # ── Harvest S3-relevant URLs BEFORE deleting the rows that reference them ──
        # (shared with the retention purge: core_functions/data_lifecycle.py holds the one list of tables)
        s3_urls: list = data_lifecycle.harvest_s3_urls(cursor, user_id)

        # ── Delete DB rows in dependency order, all scoped to this exact user_id ──
        data_lifecycle.delete_staff_rows(cursor, user_id)

        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to permanently delete staff {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete staff member. No data was removed.")
    finally:
        cursor.close()
        return_connection(conn)

    # ── DB deletion has committed successfully — now clean up S3 (best-effort) ──
    try:
        from ml.s3 import delete_employee_s3_assets
        delete_employee_s3_assets(user_id, extra_urls=s3_urls)
    except Exception as e:
        logger.error(f"S3 cleanup failed for deleted employee {user_id}: {e}")

    # The Rekognition faces and their user_face_index rows (best effort: a failure is logged inside, never blocks the delete)
    data_lifecycle.remove_face_data(user_id)

    return {
        "success": True,
        "message": f"{target['name']} has been permanently deleted."
    }


async def get_staff_by_role(requesting_user_id: str, target_role_id: Optional[int] = None):
    """Get staff based on role hierarchy"""
    if not requesting_user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get requesting user's role and parent_id (parent_id = org owner's user_id = org_id)
        cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (requesting_user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        requesting_role = user['role_id']
        # For role 1 (admin), their own user_id is the org_id
        # For role 2 & 3, parent_id points to the admin whose user_id = org_id
        org_id = requesting_user_id if requesting_role == 1 else user['parent_id']
        
        # Build query based on role hierarchy
        if requesting_role == 1:  # Admin - can see all
            if target_role_id:
                query = "SELECT user_id, name, emp_id, role_id FROM users WHERE parent_id = %s AND role_id = %s AND status = 1 ORDER BY name"
                params = (org_id, target_role_id)
            else:
                query = "SELECT user_id, name, emp_id, role_id FROM users WHERE parent_id = %s AND status = 1 ORDER BY name"
                params = (org_id,)
        elif requesting_role == 2:  # Manager - can see staff (role_id=3) in their department
            cursor.execute("SELECT dept_id FROM users WHERE user_id = %s", (requesting_user_id,))
            manager_dept = cursor.fetchone()
            if manager_dept and manager_dept['dept_id']:
                query = "SELECT user_id, name, emp_id, role_id FROM users WHERE parent_id = %s AND dept_id = %s AND role_id = 3 AND status = 1 ORDER BY name"
                params = (org_id, manager_dept['dept_id'])
            else:
                return []  # Manager not assigned to department
        else:  # Staff - can only see themselves
            query = "SELECT user_id, name, emp_id, role_id FROM users WHERE user_id = %s AND status = 1"
            params = (requesting_user_id,)
        
        cursor.execute(query, params)
        staff = cursor.fetchall()
        return staff
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get staff")
    finally:
        cursor.close()
        return_connection(conn)

async def get_shift_options(user_id: str, role_id: int):
    """Get available shift options from database"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            "SELECT shift_id as id, shift_name as name FROM shift WHERE org_id = %s AND status = 1 ORDER BY shift_id",
            (user_id,)
        )
        shifts = cursor.fetchall()
        return shifts if shifts else [{"id": 1, "name": "Default Shift"}]
    except Exception as e:
        return [{"id": 1, "name": "Default Shift"}]
    finally:
        cursor.close()
        return_connection(conn)

async def get_next_employee_id(user_id: str, role_id: int):
    """Get next available employee ID starting from 1001"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            """SELECT emp_id FROM users 
               WHERE parent_id = %s 
               AND emp_id ~ '^[0-9]+$' 
               ORDER BY CAST(emp_id AS INTEGER) DESC 
               LIMIT 1""",
            (user_id,)
        )
        result = cursor.fetchone()
        
        if result and result['emp_id']:
            next_id = int(result['emp_id']) + 1
        else:
            next_id = 1001
        
        return {"next_emp_id": str(next_id)}
        
    except Exception as e:
        print(f"Error in get_next_employee_id: {str(e)}")
        return {"next_emp_id": "1001"}
    finally:
        cursor.close()
        return_connection(conn)

async def validate_employee_id(user_id: str, role_id: int, emp_id: str):
    """Validate if employee ID is available"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # user_id is actually org_id from frontend, query by parent_id
        cursor.execute(
            "SELECT COUNT(*) as count FROM users WHERE emp_id = %s AND parent_id = %s",
            (emp_id, user_id)
        )
        result = cursor.fetchone()
        is_available = result['count'] == 0 if result else True
        
        return {"is_available": is_available}
        
    except Exception as e:
        print(f"Error in validate_employee_id: {str(e)}")
        return {"is_available": True}
    finally:
        cursor.close()
        return_connection(conn)


