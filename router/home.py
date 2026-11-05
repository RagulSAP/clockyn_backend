from fastapi import APIRouter, Query, HTTPException, Path
from core_functions.home_functions import (
    get_dashboard_status, get_branches, get_departments, get_staff_list, get_current_date, format_date,
    get_department_wise_Status, get_dashboard_stats, get_department_stats, get_all_staff, get_absent_staff,
    get_late_punched_in_staff, get_punched_in_staff, get_working_staff, get_punched_out_staff, get_staff_detail,
    get_staff_profile, get_staff_locations, get_staff_punch_locations, DashboardStatusResponse, BranchResponse, DepartmentResponse, StaffResponse, StaffDetailResponse,
    StaffProfileResponse, DateResponse, DepartmentStatusResponse
)
from core_functions.attendance_functions import get_daily_attendance, get_monthly_attendance, edit_attendance, DailyAttendanceResponse, MonthlyAttendance, EditAttendanceRequest
from typing import Optional, List
 
router = APIRouter(prefix="/home", tags=["home"])
 
@router.get("/dashboard-status", response_model=DashboardStatusResponse)
async def dashboard_status_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    return await get_dashboard_status(user_id, branch_id, target_date)
 
@router.get("/department-status", response_model=List[DepartmentStatusResponse])
async def department_Status_route(
    org_id: Optional[str] = Query(None, description="Organization ID"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering (0 for ALL)"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get department-wise statistics for home screen"""
    if not org_id or org_id.strip() == "":
        return []
    result = await get_department_wise_Status(org_id, branch_id, target_date)
    return result.departments
 
@router.get("/branches", response_model=List[BranchResponse])
async def branches_route(org_id: str = Query(..., description="Organization ID")):
    """Get all branches for an organization"""
    return await get_branches(org_id)
 
@router.get("/departments", response_model=List[DepartmentResponse])
async def departments_route(org_id: str = Query(..., description="Organization ID")):
    """Get all departments for an organization"""
    return await get_departments(org_id)
 
@router.get("/staff-list", response_model=List[StaffResponse])
async def staff_list_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    dept_id: Optional[str] = Query(None, description="Department ID for filtering")
):
    """Get staff list with attendance status"""
    return await get_staff_list(user_id, branch_id, dept_id)
 
@router.get("/department-stats", response_model=List[DepartmentStatusResponse])
async def department_stats_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get department-wise statistics"""
    return await get_department_stats(user_id, branch_id, target_date)

@router.get("/all-staff", response_model=List[StaffResponse])
async def all_staff_route(user_id: str = Query(..., description="User ID (admin)")):
    """Get all staff for organization"""
    return await get_all_staff(user_id)

@router.get("/absent-staff", response_model=List[StaffResponse])
async def absent_staff_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get absent staff for a specific date"""
    return await get_absent_staff(user_id, branch_id, target_date)

@router.get("/late-punched-in-staff", response_model=List[StaffResponse])
async def late_punched_in_staff_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get late punched-in staff for a specific date"""
    return await get_late_punched_in_staff(user_id, branch_id, target_date)

@router.get("/punched-in-staff", response_model=List[StaffResponse])
async def punched_in_staff_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get punched-in staff for a specific date"""
    return await get_punched_in_staff(user_id, branch_id, target_date)

@router.get("/working-staff", response_model=List[StaffResponse])
async def working_staff_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get working staff for a specific date"""
    return await get_working_staff(user_id, branch_id, target_date)

@router.get("/punched-out-staff", response_model=List[StaffResponse])
async def punched_out_staff_route(
    user_id: str = Query(..., description="User ID (admin)"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get punched-out staff for a specific date"""
    return await get_punched_out_staff(user_id, branch_id, target_date)

@router.get("/current-date", response_model=DateResponse)
async def current_date_route():
    """Get current date with formatting"""
    return await get_current_date()
 
@router.post("/format-date", response_model=DateResponse)
async def format_date_route(date_string: str):
    """Format date from various formats to standard format"""
    return await format_date(date_string)

@router.get("/staff-detail", response_model=StaffDetailResponse)
async def staff_detail_route(
    staff_id: str = Query(..., description="Staff user ID"),
    org_id: str = Query(..., description="Organization ID")
):
    """Get detailed information for a specific staff member"""
    return await get_staff_detail(staff_id, org_id)

@router.get("/staff-profile", response_model=StaffProfileResponse)
async def staff_profile_route(
    staff_id: str = Query(..., description="Staff user ID"),
    org_id: str = Query(..., description="Organization ID")
):
    """Get complete staff profile including admin-added and employee personal details"""
    return await get_staff_profile(staff_id, org_id)

@router.get("/daily-attendance", response_model=List[DailyAttendanceResponse])
async def daily_attendance_route(
    org_id: str = Query(..., description="Organization ID"),
    target_date: str = Query(..., description="Date in YYYY-MM-DD format"),
    branch_id: Optional[str] = Query(None, description="Branch ID for filtering"),
    dept_id: Optional[str] = Query(None, description="Department ID for filtering")
):
    """Get all staff attendance for a specific date"""
    return await get_daily_attendance(org_id, target_date, branch_id, dept_id)

@router.get("/monthly-attendance", response_model=None)
async def monthly_attendance_route(
    staff_id: str = Query(..., description="Staff user ID"),
    org_id: str = Query(..., description="Organization ID"),
    month: int = Query(..., description="Month (1-12)"),
    year: int = Query(..., description="Year"),
    detail: Optional[str] = Query(None, description="'v2' returns a row for every day with server day types; default keeps the legacy shape")
):
    """Get monthly attendance data for a specific staff member (`MonthlyAttendance` shape; `detail=v2` a dict)"""
    return await get_monthly_attendance(staff_id, org_id, month, year, detail)

@router.get("/staff-locations")
async def staff_locations_route(
    org_id: str = Query(..., description="Organization ID"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get FIELD STAFF locations from location logs for Staff Location page (background tracking only)"""
    return await get_staff_locations(org_id, target_date)

@router.get("/staff-punch-locations")
async def staff_punch_locations_route(
    org_id: str = Query(..., description="Organization ID"),
    target_date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format")
):
    """Get ALL STAFF punch-in locations from attendance table for Track Location tab in staff details"""
    return await get_staff_punch_locations(org_id, target_date)

@router.get("/org-admin/{user_id}")
async def org_admin_route(user_id: str):
    """Get the admin (role_id=1) of the org that this employee belongs to"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    from ml.s3 import get_employee_profile_url
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u2.user_id, u2.name, u2.emp_id, u2.mobile_number, u2.email, u2.role_id
            FROM users u1
            JOIN users u2 ON u2.org_id = u1.parent_id AND u2.role_id = 1
            WHERE u1.user_id = %s AND u1.status = 1
            LIMIT 1
        """, (user_id,))
        admin = cursor.fetchone()
        if not admin:
            return None
        return {
            "user_id": admin["user_id"],
            "name": admin["name"],
            "emp_id": admin["emp_id"],
            "mobile_number": admin["mobile_number"],
            "email": admin["email"],
            "role_id": admin["role_id"],
            "role_name": "Admin",
            "dept_name": None,
            "branch_name": None,
            "profile_photo_url": get_employee_profile_url(admin["user_id"]),
            "is_active": True,
        }
    finally:
        cursor.close()
        return_connection(conn)

@router.put("/update-attendance-punch")
async def update_attendance_punch_route(data: EditAttendanceRequest):
    """Edit punch_in / punch_out for a staff member on a specific date (admin)"""
    return await edit_attendance(data)