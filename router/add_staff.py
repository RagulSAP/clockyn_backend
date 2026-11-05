from fastapi import APIRouter, Query, HTTPException, Path
from core_functions.add_staff_function import (
    add_staff, get_branches, get_departments, get_managers, set_staff_status,
    get_shift_options, get_staff_detail, update_staff, delete_staff, permanently_delete_staff,
    get_branch_info, get_department_info, get_manager, get_staff_by_role,
    AddStaffRequest, BranchResponse, DepartmentResponse, ManagerResponse,
    StaffDetailResponse, BranchInfoResponse, DepartmentInfoResponse, ManagerInfoResponse,get_next_employee_id,validate_employee_id,validate_user_id
)
from typing import Optional, List

router = APIRouter(prefix="/addstaff", tags=["addstaff"])

@router.post("/")
async def add_staff_route(data: AddStaffRequest):
    """Add new staff member"""
    return await add_staff(data)

@router.get("/next-emp-id")
async def get_next_employee_id_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID")
):
    """Get next available employee ID"""
    return await get_next_employee_id(user_id, role_id)

@router.get("/validate-emp-id")
async def validate_employee_id_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID"),
    emp_id: str = Query(..., description="Employee ID to validate")
):
    """Validate if employee ID is available"""
    return await validate_employee_id(user_id, role_id, emp_id)

@router.get("/branches", response_model=List[BranchResponse])
@router.get("/branches/", response_model=List[BranchResponse])
async def get_branches_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID")
):
    """Get all branches"""
    return await get_branches(user_id, role_id)

@router.get("/departments", response_model=List[DepartmentResponse])
@router.get("/departments/", response_model=List[DepartmentResponse])
async def get_departments_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID")
):
    """Get all departments"""
    return await get_departments(user_id, role_id)

@router.get("/managers/", response_model=List[ManagerResponse])
async def get_managers_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID"),
    dept_id: Optional[str] = Query(None, description="Department ID for filtering")
):
    """Get potential managers"""
    return await get_managers(user_id, role_id, dept_id)

@router.get("/shifts/")
async def get_shifts_route(
    user_id: str = Query(..., description="User UUID"),
    role_id: int = Query(..., description="User Role ID")
):
    """Get available shift options"""
    return await get_shift_options(user_id, role_id)

@router.get("/staff-by-role")
async def get_staff_by_role_route(
    user_id: str = Query(..., description="Requesting User UUID"),
    target_role_id: Optional[int] = Query(None, description="Target role ID to filter")
):
    """Get staff based on role hierarchy"""
    return await get_staff_by_role(user_id, target_role_id)

@router.get("/count/{org_id}")
async def get_staff_count_route(org_id: str = Path(..., description="Organization ID")):
    """Get active staff count for an organization"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM users WHERE parent_id = %s AND role_id IN (2,3) AND status = 1",
            (org_id,)
        )
        row = cursor.fetchone()
        return {'success': True, 'count': int(row['cnt'] or 0)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


@router.get("/all-staff/{org_id}")
async def get_all_staff_route(
    org_id: str = Path(..., description="Organization ID"),
    branch_id: Optional[str] = Query(None, description="Branch ID filter"),
    dept_id: Optional[str] = Query(None, description="Department ID filter")
):
    """Get all staff for an organization with optional filters"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT DISTINCT u.user_id, u.name, u.emp_id, u.branch_id, u.dept_id,
                   b.branch_name, d.dept_name, u.created_at
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            WHERE u.parent_id = %s AND u.status = 1
        """
        params = [org_id, org_id, org_id]
        
        if branch_id:
            query += " AND u.branch_id = %s"
            params.append(int(branch_id))
        
        if dept_id:
            query += " AND u.dept_id = %s"
            params.append(dept_id)
        
        query += " ORDER BY u.created_at ASC"
        
        cursor.execute(query, tuple(params))
        staff = cursor.fetchall()
        
        return {"success": True, "data": staff}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

@router.get("/branch-info/{user_id}", response_model=BranchInfoResponse)
async def get_branch_info_route(
    user_id: str = Path(..., description="User UUID"),
    role_id: int = Query(..., description="Role ID")
):
    """Get branch info for a user"""
    validate_user_id(user_id)
    return await get_branch_info(user_id, role_id)

@router.get("/department-info/{user_id}", response_model=DepartmentInfoResponse)
async def get_department_info_route(
    user_id: str = Path(..., description="User UUID"),
    role_id: int = Query(..., description="Role ID")
):
    """Get department info for a user"""
    validate_user_id(user_id)
    return await get_department_info(user_id, role_id)

@router.get("/{user_id}", response_model=StaffDetailResponse)
async def get_staff_detail_route(user_id: str = Path(..., description="User UUID")):
    """Get detailed information of a specific staff member"""
    validate_user_id(user_id)
    return await get_staff_detail(user_id)

@router.put("/{user_id}")
async def update_staff_route(
    user_id: str = Path(..., description="User UUID"),
    data: AddStaffRequest = ...
):
    """Update existing staff member"""
    validate_user_id(user_id)
    return await update_staff(user_id, data)

@router.delete("/{user_id}")
async def delete_staff_route(user_id: str = Path(..., description="User UUID")):
    """Delete staff member (soft delete)"""
    validate_user_id(user_id)
    return await delete_staff(user_id)


@router.delete("/{user_id}/permanent")
async def permanently_delete_staff_route(
    user_id: str = Path(..., description="User UUID"),
    requested_by_role_id: int = Query(..., description="role_id of the account performing this action — must be 1 (admin)"),
):
    """PERMANENTLY delete a staff member and all their data from the database and S3. Irreversible."""
    validate_user_id(user_id)
    return await permanently_delete_staff(user_id, requested_by_role_id)


@router.put("/{user_id}/status")
async def toggle_staff_status(user_id: str = Path(..., description="User UUID"), status: int = Query(..., description="1 for active, 0 for inactive")):
    """Activate or deactivate a staff member (activating enforces the plan's staff limit like add_staff)"""
    return await set_staff_status(user_id, status)