from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from core_functions.manager_functions import (
    get_staff_for_manager, assign_manager, get_managers, remove_manager,
    get_manager_permissions, save_manager_permissions, check_manager_permission,
    get_manager_enabled_settings, get_staff_under_manager,
    AssignManagerRequest, ManagerResponse, StaffForManagerResponse,
    ManagerPermissionsRequest, BranchDeptAssignment
)
from typing import List

router = APIRouter(prefix="/managers", tags=["managers"])

@router.get("/staff", response_model=List[StaffForManagerResponse])
async def get_staff_route(
    org_id: str = Query(..., description="Organization ID"),
    branch_id: int = Query(..., description="Branch ID"),
    dept_id: str = Query(..., description="Department ID")
):
    """Get staff by branch and department for manager assignment"""
    return await get_staff_for_manager(org_id, branch_id, dept_id)

@router.post("/assign")
async def assign_manager_route(data: AssignManagerRequest):
    """Assign staff as manager (update role_id to 2)"""
    return await assign_manager(data)

@router.get("/", response_model=List[ManagerResponse])
async def get_managers_route(
    org_id: str = Query(..., description="Organization ID")
):
    """Get all managers with branch and department info"""
    return await get_managers(org_id)

@router.delete("/{user_id}")
async def remove_manager_route(user_id: str):
    """Remove manager (update role_id back to 3)"""
    return await remove_manager(user_id)

@router.get("/{user_id}/staff")
async def get_staff_under_manager_route(user_id: str):
    """Get staff under manager by assigned branch_id"""
    return await get_staff_under_manager(user_id)

@router.get("/get-staff-by-manager")
async def get_staff_by_manager_route(manager_id: str = Query(..., description="Manager user_id")):
    """Get staff filtered by manager's assigned branches"""
    return await get_staff_under_manager(manager_id)

@router.get("/{user_id}/permissions")
async def get_permissions_route(user_id: str):
    """Get manager permissions"""
    return await get_manager_permissions(user_id)

@router.post("/{user_id}/permissions")
async def save_permissions_route(user_id: str, data: ManagerPermissionsRequest):
    """Save manager permissions"""
    return await save_manager_permissions(user_id, data)

@router.get("/{user_id}/check-permission/{setting_name}")
async def check_permission_route(user_id: str, setting_name: str):
    """Check if manager has specific permission"""
    has_permission = await check_manager_permission(user_id, setting_name)
    return {"has_permission": has_permission}

@router.get("/{user_id}/enabled-settings")
async def get_enabled_settings_route(user_id: str):
    """Get all enabled settings for a manager"""
    settings = await get_manager_enabled_settings(user_id)
    return {"enabled_settings": settings}

@router.get("/settings/all")
async def get_all_settings_route():
    """Get all available settings"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute("SELECT setting_id, setting_name FROM settings ORDER BY setting_id")
        settings = cursor.fetchall()
        return {"settings": [dict(setting) for setting in settings]}
    finally:
        cursor.close()
        return_connection(conn)

@router.get("/permissions/{user_id}")
async def get_user_permissions_route(user_id: str):
    """Get user permissions for frontend"""
    from core_functions.permission_checker import get_user_permissions
    permissions = await get_user_permissions(user_id)
    return {"permissions": permissions}
