from fastapi import HTTPException, Depends, Header
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from typing import Optional
import functools

async def check_manager_permission_middleware(user_id: str, setting_name: str) -> bool:
    """Check if manager has specific permission"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get user role and org_id
        cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            return False
        
        # If user is admin (role_id = 1), allow all permissions
        if user['role_id'] == 1:
            return True
        
        # If user is not a manager (role_id = 2), deny access
        if user['role_id'] != 2:
            return False
        
        org_id = user['parent_id']
        
        # Check if manager has the specific permission
        cursor.execute(
            """SELECT mp.status FROM manager_permissions mp
               JOIN settings ps ON mp.setting_id = ps.setting_id
               WHERE mp.user_id = %s AND mp.org_id = %s AND ps.setting_name = %s AND mp.status = 1""",
            (user_id, org_id, setting_name)
        )
        permission = cursor.fetchone()
        return permission is not None
    except Exception:
        return False
    finally:
        cursor.close()
        return_connection(conn)

def require_permission(setting_name: str):
    """Decorator to require specific manager permission"""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user_id from kwargs or request
            user_id = kwargs.get('user_id')
            if not user_id:
                # Try to get from query parameters or path parameters
                for arg in args:
                    if hasattr(arg, 'user_id'):
                        user_id = arg.user_id
                        break
            
            if not user_id:
                raise HTTPException(status_code=400, detail="User ID required")
            
            has_permission = await check_manager_permission_middleware(user_id, setting_name)
            if not has_permission:
                raise HTTPException(
                    status_code=403, 
                    detail=f"Access denied. Required permission: {setting_name}"
                )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

class PermissionChecker:
    """Class to check permissions for different settings"""
    
    @staticmethod
    async def has_staff_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Staff Management")
    
    @staticmethod
    async def has_branch_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Branch Management")
    
    @staticmethod
    async def has_department_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Department Management")
    
    @staticmethod
    async def has_attendance_reports(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Attendance Reports")
    
    @staticmethod
    async def has_payroll_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Payroll Management")
    
    @staticmethod
    async def has_holiday_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Holiday Management")
    
    @staticmethod
    async def has_shift_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Shift Management")
    
    @staticmethod
    async def has_leads_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Leads Management")
    
    @staticmethod
    async def has_incentive_management(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Incentive Management")
    
    @staticmethod
    async def has_company_settings(user_id: str) -> bool:
        return await check_manager_permission_middleware(user_id, "Company Settings")

async def get_user_permissions(user_id: str):
    """Get all permissions for a user"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get user role and org_id
        cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            return []
        
        # If user is admin (role_id = 1), return all permissions
        if user['role_id'] == 1:
            cursor.execute("SELECT setting_name FROM settings ORDER BY setting_id")
            all_settings = cursor.fetchall()
            return [setting['setting_name'] for setting in all_settings]
        
        # If user is not a manager (role_id = 2), return empty list
        if user['role_id'] != 2:
            return []
        
        org_id = user['parent_id']
        
        # Get manager's permissions
        cursor.execute(
            """SELECT ps.setting_name FROM manager_permissions mp
               JOIN settings ps ON mp.setting_id = ps.setting_id
               WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1
               ORDER BY ps.setting_id""",
            (user_id, org_id)
        )
        permissions = cursor.fetchall()
        return [p['setting_name'] for p in permissions]
    except Exception:
        return []
    finally:
        cursor.close()
        return_connection(conn)
