from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from datetime import datetime, date, timedelta
from utils.timezone import get_utc_now, IST, format_ist_time
from pydantic import BaseModel
from typing import Optional, List
from psycopg2.extras import RealDictCursor
from ml.s3 import get_employee_profile_url
from core_functions import attendance_rules as rules
from core_functions.attendance_functions import org_grace_minutes
from core_functions import scope as staff_scope   # ENFORCE_MANAGER_SCOPE: adds nothing while the flag is off


def _count_late(rows, grace_minutes: int, key=None):
    """Count (or group) late staff from rows of `{first_in (IST time of the first punch-in of the day),
    start_time, end_time}`. Lateness is evaluated in Python with the shared rule (IST, grace, night shifts) instead of
    comparing `punch_in::time` in the database session time zone. With `key` the result is `{key value: count}`."""
    late = []
    seen = set()
    for r in rows:
        if r['first_in'] is None or r['start_time'] is None or r['end_time'] is None:
            continue
        if r['user_id'] in seen or not rules.is_late(r['first_in'], r['start_time'], r['end_time'], grace_minutes):
            continue
        seen.add(r['user_id'])
        late.append(r)
    if key is None:
        return len(late)
    out = {}
    for r in late:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


class DateResponse(BaseModel):
    current_date: str
    formatted_date: str
    day_name: str

class DashboardStatusResponse(BaseModel):
    total_staff: int
    punched_in: int
    working: int
    punched_out: int
    absent: int
    late_punched_in: int = 0

class BranchResponse(BaseModel):
    branch_id: str
    branch_name: str
    branch_address: str
    org_id: str
    branch_latitude: float = 0.0
    branch_longitude: float = 0.0
    punch_radius: int = 100

class DepartmentResponse(BaseModel):
    dept_id: str
    dept_name: str
    org_id: str

class StaffResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: Optional[str]
    branch_name: Optional[str]
    status: str  # "Working", "Absent", "Punched In", "Punched Out"
    profile_photo_url: Optional[str] = None
    is_active: Optional[bool] = True
    role_id: Optional[int] = None

class StaffDetailResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: Optional[str]
    branch_name: Optional[str]
    branch_address: Optional[str]
    status: str
    profile_photo_url: Optional[str] = None
    branch_id: Optional[str] = None
    dept_id: Optional[str] = None
    org_id: Optional[str] = None

class StaffProfileResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: Optional[str] = None
    branch_name: Optional[str] = None
    role_name: Optional[str] = None
    branch_id: Optional[str] = None
    dept_id: Optional[str] = None
    org_id: Optional[str] = None
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
    father_name: Optional[str] = None
    marital_status: Optional[str] = None
    aadhaar_no: Optional[str] = None
    pan_no: Optional[str] = None
    uan_no: Optional[str] = None
    pf_no: Optional[str] = None
    esi_no: Optional[str] = None
    doj: Optional[str] = None

class DepartmentStatusResponse(BaseModel):
    dept_id: str
    dept_name: str
    total_staff: int
    punched_in: int
    working: int
    punched_out: int
    absent: int
    late_punched_in: int = 0

class DepartmentSummaryResponse(BaseModel):
    departments: List[DepartmentStatusResponse]
    overall_Status: DashboardStatusResponse

class StaffAttendanceResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    punch_in: Optional[str] = None
    punch_out: Optional[str] = None
    work_hours: Optional[float] = None
    status: str

async def get_dashboard_status(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get dashboard statistics for home screen"""
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        # ENFORCE_MANAGER_SCOPE (default off): deny-by-default manager scope, resolved once for the three queries
        dash_scope = staff_scope.current(cursor, user_id)

        # Count staff under this admin/manager
        staff_query = "SELECT COUNT(*) as count FROM users WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1"
        staff_params = [org_id]
        
        if branch_id and branch_id != "0":
            staff_query += " AND branch_id = %s"
            staff_params.append(branch_id)
        elif mgr_branch_ids:
            staff_query += " AND branch_id = ANY(%s)"
            staff_params.append(mgr_branch_ids)

        if mgr_dept_ids:
            staff_query += " AND dept_id = ANY(%s)"
            staff_params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(dash_scope, None)
        staff_query += scope_sql
        staff_params.extend(scope_params)

        cursor.execute(staff_query, tuple(staff_params))
        total_staff = cursor.fetchone()['count']
        
        # One row per staff member who has attendance on the date, so a staff member with two sessions is
        # counted once: working while any session is open, punched out only when every session is closed.
        attendance_query = """
            SELECT
                COUNT(*) FILTER (WHERE t.has_open) as punched_in,
                COUNT(*) FILTER (WHERE t.has_closed AND NOT t.has_open) as punched_out,
                COUNT(*) as total_attendance
            FROM (
                SELECT a.user_id,
                       bool_or(a.punch_in IS NOT NULL AND a.punch_out IS NULL) as has_open,
                       bool_or(a.punch_in IS NOT NULL AND a.punch_out IS NOT NULL) as has_closed
                FROM attendance a
                JOIN users u ON a.user_id = u.user_id
                WHERE u.parent_id = %s AND a.attendance_date = %s AND u.status = 1
        """
        attendance_params = [org_id, parsed_date]

        if branch_id and branch_id != "0":
            attendance_query += " AND u.branch_id = %s"
            attendance_params.append(branch_id)
        elif mgr_branch_ids:
            attendance_query += " AND u.branch_id = ANY(%s)"
            attendance_params.append(mgr_branch_ids)

        if mgr_dept_ids:
            attendance_query += " AND u.dept_id = ANY(%s)"
            attendance_params.append(mgr_dept_ids)

        scope_sql, scope_params = staff_scope.clause(dash_scope, "u")
        attendance_query += scope_sql
        attendance_params.extend(scope_params)

        attendance_query += " GROUP BY a.user_id) t"

        cursor.execute(attendance_query, tuple(attendance_params))
        attendance_data = cursor.fetchone()

        # Late punched-in (only staff with shifts): FIRST punch-in of the day, converted to IST, against the shift
        # start (+ the organisation's grace); evaluated by the shared rule in Python.
        late_query = """
            SELECT u.user_id, s.start_time, s.end_time,
                   (MIN(a.punch_in) AT TIME ZONE 'Asia/Kolkata')::time AS first_in
            FROM attendance a
            JOIN users u ON a.user_id = u.user_id
            JOIN users_additional_info uai ON u.user_id = uai.user_id
            JOIN shift s ON uai.shift_id = s.shift_id AND s.org_id = u.parent_id AND s.status = 1
            WHERE u.parent_id = %s AND a.attendance_date = %s AND u.status = 1
            AND a.punch_in IS NOT NULL
            AND uai.shift_id IS NOT NULL AND uai.shift_id > 0
        """
        late_params = [org_id, parsed_date]

        if branch_id and branch_id != "0":
            late_query += " AND u.branch_id = %s"
            late_params.append(branch_id)
        elif mgr_branch_ids:
            late_query += " AND u.branch_id = ANY(%s)"
            late_params.append(mgr_branch_ids)

        if mgr_dept_ids:
            late_query += " AND u.dept_id = ANY(%s)"
            late_params.append(mgr_dept_ids)

        scope_sql, scope_params = staff_scope.clause(dash_scope, "u")
        late_query += scope_sql
        late_params.extend(scope_params)

        late_query += " GROUP BY u.user_id, s.start_time, s.end_time"

        cursor.execute(late_query, tuple(late_params))
        late_rows = cursor.fetchall()

        punched_in = attendance_data['punched_in'] or 0
        punched_out = attendance_data['punched_out'] or 0
        total_attendance = attendance_data['total_attendance'] or 0
        late_punched_in = _count_late(late_rows, org_grace_minutes(cursor, org_id))
        
        absent = total_staff - total_attendance
        working = punched_in
        
        return DashboardStatusResponse(
            total_staff=total_staff,
            punched_in=punched_in,
            working=working,
            punched_out=punched_out,
            absent=absent,
            late_punched_in=late_punched_in
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get dashboard Status: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_branches(org_id: str):
    """Get all branches for an organization, scoped to manager's assigned branches if role_id=2"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")

    from core_functions.manager_functions import get_manager_scope
    resolved_org_id, mgr_branch_ids, _ = await get_manager_scope(org_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if mgr_branch_ids:
            cursor.execute(
                """SELECT CAST(branch_id AS VARCHAR) as branch_id, branch_name,
                   COALESCE(branch_address, '') as branch_address, org_id,
                   COALESCE(gps_location[0], 0) as branch_longitude,
                   COALESCE(gps_location[1], 0) as branch_latitude,
                   COALESCE(punch_radius, 100) as punch_radius
                   FROM branch_info WHERE org_id = %s AND branch_id = ANY(%s) AND status = 1""",
                (resolved_org_id, mgr_branch_ids)
            )
        else:
            cursor.execute(
                """SELECT CAST(branch_id AS VARCHAR) as branch_id, branch_name,
                   COALESCE(branch_address, '') as branch_address, org_id,
                   COALESCE(gps_location[0], 0) as branch_longitude,
                   COALESCE(gps_location[1], 0) as branch_latitude,
                   COALESCE(punch_radius, 100) as punch_radius
                   FROM branch_info WHERE org_id = %s AND status = 1""",
                (resolved_org_id,)
            )
        return cursor.fetchall()
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to get branches: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_departments(org_id: str):
    """Get all departments for an organization"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            "SELECT dept_id, dept_name, org_id FROM dept_info WHERE org_id = %s AND status = 1",
            (org_id,)
        )
        departments = cursor.fetchall()
        
        return [DepartmentResponse(**dept) for dept in departments]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get departments: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_list(org_id: str, branch_id: Optional[str] = None, dept_id: Optional[str] = None):
    """Get staff list with attendance status for home page"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    resolved_org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(org_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Find staff under this admin (not the admin themselves)
        query = """
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.status as user_status, u.role_id,
                   d.dept_name, b.branch_name, r.role_name,
                   a.punch_in, a.punch_out, a.attendance_date as attendance_date
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN role_info r ON u.role_id = r.role_id
            LEFT JOIN LATERAL (
                SELECT punch_in, punch_out, attendance_date
                FROM attendance
                WHERE user_id = u.user_id
                  AND (attendance_date = %s
                       OR (punch_in IS NOT NULL AND punch_out IS NULL AND punch_in >= %s))
                ORDER BY punch_in DESC NULLS LAST
                LIMIT 1
            ) a ON true
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status IN (0, 1)
        """
        now = get_utc_now()
        params = [rules.ist_today(now), rules.stale_cutoff(now), resolved_org_id]
        
        if branch_id:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)
        
        if dept_id:
            query += " AND u.dept_id = %s"
            params.append(dept_id)
        elif mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, org_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.user_id, u.created_at ASC"

        cursor.execute(query, tuple(params))
        staff_data = cursor.fetchall()

        # Process staff data to determine status
        staff_list = []
        for staff in staff_data:
            is_active = staff['user_status'] == 1
            # Determine attendance status
            if not is_active:
                status = "Inactive"
            elif staff['attendance_date']:
                if staff['punch_in'] and staff['punch_out']:
                    status = "Punched Out"
                elif staff['punch_in']:
                    status = "Working"
                else:
                    status = "Punched In"
            else:
                status = "Absent"
            
            staff_response = StaffResponse(
                user_id=staff['user_id'],
                name=staff['name'],
                emp_id=staff['emp_id'],
                mobile_number=staff['mobile_number'],
                email=staff['email'],
                dept_name=staff['dept_name'],
                branch_name=staff['branch_name'],
                status=status,
                profile_photo_url=get_employee_profile_url(staff['user_id']),
                is_active=is_active,
                role_id=staff['role_id']
            )
            staff_list.append(staff_response)
        
        return staff_list
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff list: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_dashboard_stats(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get dashboard statistics - alias for get_dashboard_status"""
    return await get_dashboard_status(user_id, branch_id, target_date)

async def get_department_stats(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get department-wise statistics with correct staff categorization"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if not target_date:
            target_date = datetime.now(IST).date().strftime('%Y-%m-%d')
        
        # Get all departments for the organization, even if they have no staff
        query = """
            SELECT 
                d.dept_id,
                d.dept_name,
                COALESCE(COUNT(DISTINCT u.user_id), 0) as total_staff,
                COALESCE(COUNT(DISTINCT CASE WHEN u.user_id IS NOT NULL AND a.user_id IS NULL THEN u.user_id END), 0) as absent,
                COALESCE(COUNT(DISTINCT CASE WHEN a.has_open THEN a.user_id END), 0) as punched_in,
                COALESCE(COUNT(DISTINCT CASE WHEN a.has_open THEN a.user_id END), 0) as working,
                COALESCE(COUNT(DISTINCT CASE WHEN a.has_closed AND NOT a.has_open THEN a.user_id END), 0) as punched_out
            FROM dept_info d
            LEFT JOIN users u ON d.dept_id = u.dept_id AND u.parent_id = %s AND u.status = 1 AND u.role_id IN (2, 3)
            LEFT JOIN (
                SELECT user_id,
                       bool_or(punch_in IS NOT NULL AND punch_out IS NULL) as has_open,
                       bool_or(punch_in IS NOT NULL AND punch_out IS NOT NULL) as has_closed
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            WHERE d.org_id = %s AND d.status = 1
        """
        
        params = [user_id, target_date, user_id]
        
        if branch_id:
            query += " AND (u.branch_id = %s OR u.branch_id IS NULL)"
            params.append(branch_id)
        
        query += " GROUP BY d.dept_id, d.dept_name ORDER BY d.dept_name"
        
        cursor.execute(query, tuple(params))
        results = cursor.fetchall()
        
        # Get late punch-in counts separately for each department
        # (first punch-in of the day in IST against the shift start; evaluated by the shared rule)
        late_query = """
            SELECT d.dept_id, u.user_id, s.start_time, s.end_time,
                   (MIN(a.punch_in) AT TIME ZONE 'Asia/Kolkata')::time AS first_in
            FROM dept_info d
            JOIN users u ON d.dept_id = u.dept_id AND u.parent_id = %s AND u.status = 1 AND u.role_id IN (2, 3)
            JOIN attendance a ON u.user_id = a.user_id AND a.attendance_date = %s
            JOIN users_additional_info uai ON u.user_id = uai.user_id
            JOIN shift s ON uai.shift_id = s.shift_id AND s.org_id = d.org_id AND s.status = 1
            WHERE d.org_id = %s AND d.status = 1
            AND a.punch_in IS NOT NULL
            AND uai.shift_id IS NOT NULL AND uai.shift_id > 0
        """
        late_params = [user_id, target_date, user_id]

        if branch_id:
            late_query += " AND u.branch_id = %s"
            late_params.append(branch_id)

        late_query += " GROUP BY d.dept_id, u.user_id, s.start_time, s.end_time"

        cursor.execute(late_query, tuple(late_params))
        late_results = cursor.fetchall()

        # Create a dict for quick lookup
        late_dict = _count_late(late_results, org_grace_minutes(cursor, user_id), key='dept_id')

        return [DepartmentStatusResponse(
            dept_id=row['dept_id'],
            dept_name=row['dept_name'],
            total_staff=row['total_staff'],
            punched_in=row['punched_in'],
            working=row['working'],
            punched_out=row['punched_out'],
            absent=row['absent'],
            late_punched_in=late_dict.get(row['dept_id'], 0)
        ) for row in results]
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to get department stats: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_all_staff(user_id: str):
    """Get all staff for organization, scoped to manager's branch/dept if role_id=2"""
    if not user_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    resolved_org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.status as user_status, u.role_id,
                   d.dept_name, b.branch_name, r.role_name,
                   a.punch_in, a.punch_out, a.attendance_date as attendance_date
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN role_info r ON u.role_id = r.role_id
            LEFT JOIN LATERAL (
                SELECT punch_in, punch_out, attendance_date
                FROM attendance
                WHERE user_id = u.user_id
                  AND (attendance_date = %s
                       OR (punch_in IS NOT NULL AND punch_out IS NULL AND punch_in >= %s))
                ORDER BY punch_in DESC NULLS LAST
                LIMIT 1
            ) a ON true
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status IN (0, 1)
        """
        now = get_utc_now()
        params: list = [rules.ist_today(now), rules.stale_cutoff(now), resolved_org_id]

        if mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)
        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)

        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, user_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.user_id, u.created_at ASC"
        cursor.execute(query, tuple(params))
        staff_data = cursor.fetchall()

        staff_list = []
        for staff in staff_data:
            is_active = staff['user_status'] == 1
            if not is_active:
                status = "Inactive"
            elif staff['attendance_date']:
                if staff['punch_in'] and staff['punch_out']:
                    status = "Punched Out"
                elif staff['punch_in']:
                    status = "Working"
                else:
                    status = "Punched In"
            else:
                status = "Absent"
            
            staff_response = StaffResponse(
                user_id=staff['user_id'],
                name=staff['name'],
                emp_id=staff['emp_id'],
                mobile_number=staff['mobile_number'],
                email=staff['email'],
                dept_name=staff['dept_name'],
                branch_name=staff['branch_name'],
                status=status,
                profile_photo_url=get_employee_profile_url(staff['user_id']),
                is_active=is_active,
                role_id=staff['role_id']
            )
            staff_list.append(staff_response)
        
        return staff_list
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get all staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_absent_staff(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get list of absent staff for a specific date"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        # Get staff who don't have attendance record for the date
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id, u.status AS user_status,
                   d.dept_name, b.branch_name
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN attendance a ON u.user_id = a.user_id AND a.attendance_date = %s
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
            AND a.user_id IS NULL
        """
        params = [parsed_date, org_id]
        
        if branch_id and branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)

        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, user_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.created_at ASC"

        cursor.execute(query, tuple(params))
        absent_staff = cursor.fetchall()
        
        return [StaffResponse(
            user_id=staff['user_id'],
            name=staff['name'],
            emp_id=staff['emp_id'],
            mobile_number=staff['mobile_number'],
            email=staff['email'],
            dept_name=staff['dept_name'],
            branch_name=staff['branch_name'],
            status="Absent",
            profile_photo_url=get_employee_profile_url(staff['user_id']),
            is_active=(staff['user_status'] == 1),
            role_id=staff['role_id']
        ) for staff in absent_staff]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get absent staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_late_punched_in_staff(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get list of staff who punched in late based on their shift"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        # Get staff who punched in late based on their assigned shift (only staff with shifts)
        query = """
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id, u.status AS user_status,
                   d.dept_name, b.branch_name, a.punch_in,
                   (a.punch_in AT TIME ZONE 'Asia/Kolkata')::time AS first_in, s.start_time, s.end_time
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            JOIN users_additional_info uai ON u.user_id = uai.user_id
            JOIN shift s ON uai.shift_id = s.shift_id AND s.org_id = u.parent_id AND s.status = 1
            JOIN LATERAL (
                SELECT punch_in
                FROM attendance
                WHERE user_id = u.user_id AND attendance_date = %s
                  AND punch_in IS NOT NULL
                ORDER BY punch_in ASC
                LIMIT 1
            ) a ON true
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
            AND uai.shift_id IS NOT NULL AND uai.shift_id > 0
        """
        params = [parsed_date, org_id]
        
        if branch_id and branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)

        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, user_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.user_id, u.created_at ASC"

        cursor.execute(query, tuple(params))
        # read the rows BEFORE the grace lookup: it runs its own statements (savepoint + select) on the same cursor
        candidate_rows = cursor.fetchall()
        grace = org_grace_minutes(cursor, org_id)
        # first punch-in of the day (IST) against the shift of the staff member, by the shared lateness rule
        late_staff = [r for r in candidate_rows
                      if rules.is_late(r['first_in'], r['start_time'], r['end_time'], grace)]

        return [StaffResponse(
            user_id=staff['user_id'],
            name=staff['name'],
            emp_id=staff['emp_id'],
            mobile_number=staff['mobile_number'],
            email=staff['email'],
            dept_name=staff['dept_name'],
            branch_name=staff['branch_name'],
            status="Late Punched-In",
            profile_photo_url=get_employee_profile_url(staff['user_id']),
            is_active=(staff['user_status'] == 1),
            role_id=staff['role_id']
        ) for staff in late_staff]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get late punched-in staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_punched_in_staff(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get list of staff who have punched in but not out"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        query = """
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id, u.status AS user_status,
                   d.dept_name, b.branch_name, a.punch_in
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            JOIN LATERAL (
                SELECT punch_in
                FROM attendance
                WHERE user_id = u.user_id AND attendance_date = %s
                  AND punch_in IS NOT NULL AND punch_out IS NULL
                ORDER BY punch_in DESC NULLS LAST
                LIMIT 1
            ) a ON true
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
        """
        params = [parsed_date, org_id]
        
        if branch_id and branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)

        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, user_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.user_id, u.created_at ASC"

        cursor.execute(query, tuple(params))
        punched_in_staff = cursor.fetchall()
        
        return [StaffResponse(
            user_id=staff['user_id'],
            name=staff['name'],
            emp_id=staff['emp_id'],
            mobile_number=staff['mobile_number'],
            email=staff['email'],
            dept_name=staff['dept_name'],
            branch_name=staff['branch_name'],
            status="Punched In",
            profile_photo_url=get_employee_profile_url(staff['user_id']),
            is_active=(staff['user_status'] == 1),
            role_id=staff['role_id']
        ) for staff in punched_in_staff]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get punched-in staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_working_staff(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get list of staff who are currently working (punched in but not out)"""
    # This is the same as punched_in_staff for now
    return await get_punched_in_staff(user_id, branch_id, target_date)

async def get_punched_out_staff(user_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get list of staff who have punched out"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    from core_functions.manager_functions import get_staff_query_scope as get_manager_scope
    org_id, mgr_branch_ids, mgr_dept_ids = await get_manager_scope(user_id)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        query = """
            SELECT DISTINCT ON (u.user_id)
                   u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id, u.status AS user_status,
                   d.dept_name, b.branch_name, a.punch_in, a.punch_out
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            JOIN LATERAL (
                SELECT punch_in, punch_out
                FROM attendance
                WHERE user_id = u.user_id AND attendance_date = %s
                  AND punch_in IS NOT NULL AND punch_out IS NOT NULL
                ORDER BY punch_in DESC NULLS LAST
                LIMIT 1
            ) a ON true
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
            AND NOT EXISTS (
                SELECT 1 FROM attendance o
                WHERE o.user_id = u.user_id AND o.attendance_date = %s
                  AND o.punch_in IS NOT NULL AND o.punch_out IS NULL
            )
        """
        params = [parsed_date, org_id, parsed_date]

        if branch_id and branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        elif mgr_branch_ids:
            query += " AND u.branch_id = ANY(%s)"
            params.append(mgr_branch_ids)

        if mgr_dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(mgr_dept_ids)
        
        scope_sql, scope_params = staff_scope.clause(staff_scope.current(cursor, user_id), "u")
        query += scope_sql
        params.extend(scope_params)

        query += " ORDER BY u.user_id, u.created_at ASC"

        cursor.execute(query, tuple(params))
        punched_out_staff = cursor.fetchall()
        
        return [StaffResponse(
            user_id=staff['user_id'],
            name=staff['name'],
            emp_id=staff['emp_id'],
            mobile_number=staff['mobile_number'],
            email=staff['email'],
            dept_name=staff['dept_name'],
            branch_name=staff['branch_name'],
            status="Punched Out",
            profile_photo_url=get_employee_profile_url(staff['user_id']),
            is_active=(staff['user_status'] == 1),
            role_id=staff['role_id']
        ) for staff in punched_out_staff]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get punched-out staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_current_date():
    """Get current date with formatting for frontend"""
    try:
        current = datetime.now(IST).date()
        return DateResponse(
            current_date=current.strftime("%Y-%m-%d"),
            formatted_date=current.strftime("%d-%m-%Y"),
            day_name=current.strftime("%A")
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get current date: {str(e)}")

async def format_date(input_date: str):
    """Format date from various formats to DD-MM-YYYY"""
    try:
        formats = ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y"]
        
        for fmt in formats:
            try:
                parsed = datetime.strptime(input_date, fmt).date()
                return DateResponse(
                    current_date=parsed.strftime("%Y-%m-%d"),
                    formatted_date=parsed.strftime("%d-%m-%Y"),
                    day_name=parsed.strftime("%A")
                )
            except ValueError:
                continue
        
        current = datetime.now(IST).date()
        return DateResponse(
            current_date=current.strftime("%Y-%m-%d"),
            formatted_date=current.strftime("%d-%m-%Y"),
            day_name=current.strftime("%A")
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to format date: {str(e)}")

async def get_department_wise_Status(org_id: str, branch_id: Optional[str] = None, target_date: Optional[str] = None):
    """Get department-wise staff statistics for dashboard"""
    if not org_id or org_id.strip() == "":
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        dept_query = """
            SELECT 
                d.dept_id,
                d.dept_name,
                COUNT(DISTINCT u.user_id) as total_staff,
                COUNT(DISTINCT CASE WHEN a.has_open THEN a.user_id END) as punched_in,
                COUNT(DISTINCT CASE WHEN a.has_closed AND NOT a.has_open THEN a.user_id END) as punched_out,
                COUNT(DISTINCT a.user_id) as total_attendance
            FROM dept_info d
            LEFT JOIN users u ON d.dept_id = u.dept_id AND u.parent_id = d.org_id AND u.status = 1
            LEFT JOIN (
                SELECT user_id,
                       bool_or(punch_in IS NOT NULL AND punch_out IS NULL) as has_open,
                       bool_or(punch_in IS NOT NULL AND punch_out IS NOT NULL) as has_closed
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            WHERE d.org_id = %s AND d.status = 1
        """
        dept_params = [parsed_date, org_id]
        
        if branch_id and branch_id != "0":
            dept_query += " AND (u.branch_id = %s OR u.branch_id IS NULL)"
            dept_params.append(branch_id)
        
        dept_query += " GROUP BY d.dept_id, d.dept_name ORDER BY d.dept_name"
        
        cursor.execute(dept_query, tuple(dept_params))
        dept_results = cursor.fetchall()
        
        # Get late punch-in counts separately using INNER JOINs
        late_query = """
            SELECT d.dept_id, u.user_id, s.start_time, s.end_time,
                   (MIN(a.punch_in) AT TIME ZONE 'Asia/Kolkata')::time AS first_in
            FROM dept_info d
            JOIN users u ON d.dept_id = u.dept_id AND u.parent_id = d.org_id AND u.status = 1
            JOIN attendance a ON u.user_id = a.user_id AND a.attendance_date = %s
            JOIN users_additional_info uai ON u.user_id = uai.user_id
            JOIN shift s ON uai.shift_id = s.shift_id AND s.org_id = d.org_id AND s.status = 1
            WHERE d.org_id = %s AND d.status = 1
            AND a.punch_in IS NOT NULL
            AND uai.shift_id IS NOT NULL AND uai.shift_id > 0
        """
        late_params = [parsed_date, org_id]

        if branch_id and branch_id != "0":
            late_query += " AND u.branch_id = %s"
            late_params.append(branch_id)

        late_query += " GROUP BY d.dept_id, u.user_id, s.start_time, s.end_time"

        cursor.execute(late_query, tuple(late_params))
        late_results = cursor.fetchall()

        late_dict = _count_late(late_results, org_grace_minutes(cursor, org_id), key='dept_id')

        department_status = []
        overall_total = 0
        overall_punched_in = 0
        overall_punched_out = 0
        overall_attendance = 0
        
        for dept in dept_results:
            total_staff = dept['total_staff'] or 0
            punched_in = dept['punched_in'] or 0
            punched_out = dept['punched_out'] or 0
            total_attendance = dept['total_attendance'] or 0
            
            working = punched_in
            absent = total_staff - total_attendance
            
            late_punched_in = late_dict.get(dept['dept_id'], 0)
            
            department_status.append(DepartmentStatusResponse(
                dept_id=dept['dept_id'],
                dept_name=dept['dept_name'],
                total_staff=total_staff,
                punched_in=punched_in,
                working=working,
                punched_out=punched_out,
                absent=absent,
                late_punched_in=late_punched_in
            ))
            
            overall_total += total_staff
            overall_punched_in += punched_in
            overall_punched_out += punched_out
            overall_attendance += total_attendance
        
        overall_working = overall_punched_in
        overall_absent = overall_total - overall_attendance
        
        overall_status = DashboardStatusResponse(
            total_staff=overall_total,
            punched_in=overall_punched_in,
            working=overall_working,
            punched_out=overall_punched_out,
            absent=overall_absent
        )
        
        return DepartmentSummaryResponse(
            departments=department_status,
            overall_status=overall_status
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get department-wise status: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_department_staff_attendance(org_id: str, dept_id: str, target_date: Optional[str] = None, branch_id: Optional[str] = None, status_filter: Optional[str] = None):
    """Get staff list for a department with attendance details for selected date"""
    if not org_id or not dept_id:
        raise HTTPException(status_code=400, detail="Organization ID and Department ID are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        query = """
            SELECT 
                u.user_id,
                u.name,
                u.emp_id,
                u.mobile_number,
                u.email,
                a.punch_in,
                a.punch_out,
                a.work_hours
            FROM users u
            LEFT JOIN (
                SELECT user_id,
                       MIN(punch_in) AS punch_in,
                       (array_agg(punch_out ORDER BY punch_in DESC))[1] AS punch_out,
                       SUM(work_hours) AS work_hours
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            WHERE u.org_id = %s AND u.dept_id = %s AND u.status = 1
        """
        params = [parsed_date, org_id, dept_id]
        
        if branch_id and branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        
        query += " ORDER BY u.name"
        
        cursor.execute(query, tuple(params))
        staff_data = cursor.fetchall()
        
        staff_list = []
        for staff in staff_data:
            if staff['punch_in'] and staff['punch_out']:
                status = "Completed"
            elif staff['punch_in'] and not staff['punch_out']:
                status = "Working"
            else:
                status = "Absent"
            
            if status_filter:
                if status_filter == "punched_in" and not (staff['punch_in'] and not staff['punch_out']):
                    continue
                elif status_filter == "punched_out" and not (staff['punch_in'] and staff['punch_out']):
                    continue
                elif status_filter == "working" and status != "Working":
                    continue
                elif status_filter == "absent" and status != "Absent":
                    continue
            
            punch_in = format_ist_time(staff['punch_in'], "%H:%M") if staff['punch_in'] else None
            punch_out = format_ist_time(staff['punch_out'], "%H:%M") if staff['punch_out'] else None

            staff_list.append(StaffAttendanceResponse(
                user_id=staff['user_id'],
                name=staff['name'],
                emp_id=staff['emp_id'],
                mobile_number=staff['mobile_number'],
                email=staff['email'],
                punch_in=punch_in,
                punch_out=punch_out,
                work_hours=float(staff['work_hours']) if staff['work_hours'] else None,
                status=status
            ))
        
        return staff_list
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get department staff attendance: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_detail(staff_id: str, org_id: str):
    """Get detailed information for a specific staff member"""
    if not staff_id or not org_id:
        raise HTTPException(status_code=400, detail="Staff ID and Organization ID are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email,
                   CAST(u.branch_id AS VARCHAR) AS branch_id, u.dept_id, u.parent_id AS org_id,
                   d.dept_name, b.branch_name, b.branch_address,
                   a.punch_in, a.punch_out, a.attendance_date as attendance_date
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN LATERAL (
                SELECT punch_in, punch_out, attendance_date
                FROM attendance
                WHERE user_id = u.user_id
                  AND (attendance_date = %s
                       OR (punch_in IS NOT NULL AND punch_out IS NULL AND punch_in >= %s))
                ORDER BY punch_in DESC NULLS LAST
                LIMIT 1
            ) a ON true
            WHERE u.user_id = %s AND u.parent_id = %s AND u.status IN (0, 1)
        """
        
        now = get_utc_now()
        cursor.execute(query, (org_id, org_id, rules.ist_today(now), rules.stale_cutoff(now), staff_id, org_id))
        staff_data = cursor.fetchone()
        
        if not staff_data:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Determine attendance status
        if staff_data['attendance_date']:
            if staff_data['punch_in'] and staff_data['punch_out']:
                status = "Punched Out"
            elif staff_data['punch_in']:
                status = "Working"
            else:
                status = "Punched In"
        else:
            status = "Absent"
        
        return StaffDetailResponse(
            user_id=staff_data['user_id'],
            name=staff_data['name'],
            emp_id=staff_data['emp_id'],
            mobile_number=staff_data['mobile_number'],
            email=staff_data['email'],
            dept_name=staff_data['dept_name'],
            branch_name=staff_data['branch_name'],
            branch_address=staff_data['branch_address'],
            status=status,
            profile_photo_url=get_employee_profile_url(staff_data['user_id']),
            branch_id=staff_data['branch_id'],
            dept_id=staff_data['dept_id'],
            org_id=staff_data['org_id']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff detail: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_profile(staff_id: str, org_id: str):
    """Get complete staff profile including admin-added and employee personal details"""
    if not staff_id or not org_id:
        raise HTTPException(status_code=400, detail="Staff ID and Organization ID are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id,
                   CAST(u.branch_id AS VARCHAR) AS branch_id, u.dept_id, u.parent_id AS org_id,
                   d.dept_name, b.branch_name,
                   ua.dob, ua.gender, ua.blood_group, ua.address,
                   ua.emergency_contact_no, ua.acc_no, ua.ifsc_code,
                   ua.bank_name, ua.bank_branch, ua.shift_id,
                   ua.father_name, ua.marital_status, ua.aadhaar_no,
                   ua.pan_no, ua.uan_no, ua.pf_no, ua.esi_no, ua.doj
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN users_additional_info ua ON u.user_id = ua.user_id
            WHERE u.user_id = %s AND u.parent_id = %s AND u.status IN (0, 1)
        """
        
        cursor.execute(query, (staff_id, org_id))
        staff_data = cursor.fetchone()
        
        if not staff_data:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Map role_id to role_name
        role_map = {1: 'Admin', 2: 'Manager', 3: 'Staff'}
        role_name = role_map.get(staff_data['role_id'], None)
        
        # Format dates for frontend
        dob_value = None
        if staff_data['dob']:
            dob_value = staff_data['dob'].strftime('%Y-%m-%d')
        
        doj_value = None
        if staff_data['doj']:
            doj_value = staff_data['doj'].strftime('%Y-%m-%d')
        
        return StaffProfileResponse(
            user_id=staff_data['user_id'],
            name=staff_data['name'],
            emp_id=staff_data['emp_id'],
            mobile_number=staff_data['mobile_number'],
            email=staff_data['email'],
            dept_name=staff_data['dept_name'],
            branch_name=staff_data['branch_name'],
            role_name=role_name,
            branch_id=staff_data['branch_id'],
            dept_id=staff_data['dept_id'],
            org_id=staff_data['org_id'],
            dob=dob_value,
            gender=staff_data['gender'],
            blood_group=staff_data['blood_group'],
            address=staff_data['address'],
            emergency_contact_no=staff_data['emergency_contact_no'],
            acc_no=staff_data['acc_no'],
            ifsc_code=staff_data['ifsc_code'],
            bank_name=staff_data['bank_name'],
            bank_branch=staff_data['bank_branch'],
            shift_id=staff_data['shift_id'],
            profile_photo_url=get_employee_profile_url(staff_data['user_id']),
            father_name=staff_data['father_name'],
            marital_status=staff_data['marital_status'],
            aadhaar_no=staff_data['aadhaar_no'],
            pan_no=staff_data['pan_no'],
            uan_no=staff_data['uan_no'],
            pf_no=staff_data['pf_no'],
            esi_no=staff_data['esi_no'],
            doj=doj_value
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff profile: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_locations(org_id: str, target_date: Optional[str] = None):
    """Get FIELD STAFF locations from location logs for Staff Location page (background tracking only)"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        # Get ONLY FIELD STAFF with their latest location from location logs and attendance info
        query = """
            WITH latest_locations AS (
                SELECT DISTINCT ON (sll.user_id) 
                    sll.user_id,
                    sll.location[0] as longitude,
                    sll.location[1] as latitude,
                    sll.date_time as location_updated_at
                FROM staff_location_logs sll
                WHERE sll.org_id = %s
                    AND DATE((sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY sll.user_id, sll.date_time DESC
            )
            SELECT
                u.user_id,
                u.name,
                u.emp_id,
                COALESCE(b.branch_name, 'No Branch') as branch_name,
                COALESCE(d.dept_name, 'No Department') as dept_name,
                ll.latitude,
                ll.longitude,
                ll.location_updated_at,
                TO_CHAR(a.first_in AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') as punch_in,
                TO_CHAR(a.last_out AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') as punch_out
            FROM users u
            INNER JOIN latest_locations ll ON u.user_id = ll.user_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN (
                SELECT user_id,
                       MIN(punch_in) AS first_in,
                       (array_agg(punch_out ORDER BY punch_in DESC))[1] AS last_out
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            WHERE (u.parent_id = %s OR u.org_id = %s)
                AND u.status = 1
                AND u.role_id IN (2, 3)
                AND u.is_field_staff = 1
            ORDER BY u.name
        """
        
        cursor.execute(query, (org_id, parsed_date, org_id, org_id, parsed_date, org_id, org_id))
        locations = cursor.fetchall()
        
        return [{
            "user_id": loc['user_id'],
            "name": loc['name'],
            "emp_id": loc['emp_id'],
            "dept_name": loc['dept_name'],
            "branch_name": loc['branch_name'],
            "punch_in": loc['punch_in'],
            "punch_out": loc['punch_out'],
            "latitude": float(loc['latitude']) if loc['latitude'] else None,
            "longitude": float(loc['longitude']) if loc['longitude'] else None,
            "location_updated_at": loc['location_updated_at'].isoformat() if loc['location_updated_at'] else None
        } for loc in locations]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff locations: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_punch_locations(org_id: str, target_date: Optional[str] = None):
    """Get ALL STAFF punch-in locations from attendance table for Track Location tab in staff details"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if target_date:
            try:
                parsed_date = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = datetime.now(IST).date()
        else:
            parsed_date = datetime.now(IST).date()
        
        # Get ALL STAFF with their punch-in and punch-out locations from attendance table
        query = """
            SELECT 
                u.user_id,
                u.name,
                u.emp_id,
                COALESCE(b.branch_name, 'No Branch') as branch_name,
                COALESCE(d.dept_name, 'No Department') as dept_name,
                NULLIF(SPLIT_PART(REPLACE(REPLACE(a.punch_in_location::text, '(', ''), ')', ''), ',', 2), '')::float as punch_in_lat,
                NULLIF(SPLIT_PART(REPLACE(REPLACE(a.punch_in_location::text, '(', ''), ')', ''), ',', 1), '')::float as punch_in_lng,
                NULLIF(SPLIT_PART(REPLACE(REPLACE(a.punch_out_location::text, '(', ''), ')', ''), ',', 2), '')::float as punch_out_lat,
                NULLIF(SPLIT_PART(REPLACE(REPLACE(a.punch_out_location::text, '(', ''), ')', ''), ',', 1), '')::float as punch_out_lng,
                TO_CHAR(a.punch_in AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as punch_in,
                TO_CHAR(a.punch_out AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as punch_out
            FROM users u
            INNER JOIN (
                SELECT user_id,
                       MIN(punch_in) AS punch_in,
                       (array_agg(punch_out ORDER BY punch_in DESC))[1] AS punch_out,
                       (array_agg(punch_in_location ORDER BY punch_in ASC))[1] AS punch_in_location,
                       (array_agg(punch_out_location ORDER BY punch_in DESC))[1] AS punch_out_location
                FROM attendance
                WHERE attendance_date = %s AND punch_in IS NOT NULL
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            WHERE (u.parent_id = %s OR u.org_id = %s)
                AND u.status = 1
                AND u.role_id IN (2, 3)
                AND a.punch_in IS NOT NULL
            ORDER BY u.name
        """
        
        cursor.execute(query, (parsed_date, org_id, org_id, org_id, org_id))
        locations = cursor.fetchall()
        
        return [{
            "user_id": loc['user_id'],
            "name": loc['name'],
            "emp_id": loc['emp_id'],
            "dept_name": loc['dept_name'],
            "branch_name": loc['branch_name'],
            "punch_in": loc['punch_in'],
            "punch_out": loc['punch_out'],
            "punch_in_lat": float(loc['punch_in_lat']) if loc['punch_in_lat'] else None,
            "punch_in_lng": float(loc['punch_in_lng']) if loc['punch_in_lng'] else None,
            "punch_out_lat": float(loc['punch_out_lat']) if loc['punch_out_lat'] else None,
            "punch_out_lng": float(loc['punch_out_lng']) if loc['punch_out_lng'] else None,
        } for loc in locations]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff punch locations: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)
