from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from datetime import datetime
from utils.timezone import get_utc_now, IST
from pydantic import BaseModel
from typing import Optional, List, Dict
from core_functions import scope as staff_scope   # ENFORCE_MANAGER_SCOPE (default off)


class BranchDeptAssignment(BaseModel):
    branch_id: int
    dept_id: str  # required: at least one department must be selected

class AssignManagerRequest(BaseModel):
    user_id: str
    assignments: List[BranchDeptAssignment]

class ManagerAssignment(BaseModel):
    branch_id: int
    branch_name: str
    dept_id: str
    dept_name: str

class ManagerResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    role_id: int
    assignments: List[ManagerAssignment]

class StaffForManagerResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str

class ManagerPermissionsRequest(BaseModel):
    permissions: Dict[str, bool]

async def get_staff_under_manager(user_id: str):
    """Get staff whose branch_id AND dept_id match the manager's assignments."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s AND role_id = 2", (user_id,))
        manager = cursor.fetchone()
        if not manager:
            raise HTTPException(status_code=404, detail="Manager not found")
        org_id = manager['parent_id']

        # ENFORCE_MANAGER_SCOPE on: the staff assigned to this manager (users.manager_id) plus, when assignment rows
        # exist, the staff of the assigned branches AND departments (core_functions/scope.py). A manager with neither
        # sees nobody. Flag off (default): the unchanged logic below.
        sc = staff_scope.current(cursor, user_id)
        if sc is not None:
            scope_sql, scope_params = staff_scope.scope_predicate(sc, "u", include_org=False)
            cursor.execute(
                f"""SELECT u.user_id, u.name, u.emp_id,
                           COALESCE(b.branch_name, '') as branch_name,
                           COALESCE(d.dept_name, '') as dept_name
                    FROM users u
                    LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %s
                    LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = %s
                    WHERE u.parent_id IN (%s, %s) AND u.role_id = 3 AND u.status = 1
                    AND ({scope_sql})
                    ORDER BY u.name""",
                [org_id, org_id, org_id, user_id] + scope_params)
            return cursor.fetchall()

        cursor.execute("SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall()
        if not rows:
            return []

        branch_ids = list({int(r['branch_id']) for r in rows if r['branch_id'] is not None})
        dept_ids = list({r['dept_id'] for r in rows if r['dept_id'] is not None})

        if not branch_ids:
            return []

        query = f"""
            SELECT u.user_id, u.name, u.emp_id,
                   COALESCE(b.branch_name, '') as branch_name,
                   COALESCE(d.dept_name, '') as dept_name
            FROM users u
            LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = %s
            WHERE u.parent_id = %s AND u.role_id = 3 AND u.status = 1
            AND u.branch_id = ANY(%s)"""
        params = [org_id, org_id, org_id, branch_ids]

        if dept_ids:
            query += " AND u.dept_id = ANY(%s)"
            params.append(dept_ids)

        query += " ORDER BY u.name"
        cursor.execute(query, params)
        return cursor.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff under manager: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_for_manager(org_id: str, branch_id: int, dept_id: str):
    """Get staff (role_id=3) by branch and department, or all if 'ALL' is selected"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """SELECT DISTINCT u.user_id, u.name, u.emp_id 
                   FROM users u
                   LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
                   WHERE u.parent_id = %s AND u.role_id = 3 AND u.status = 1"""
        params = [org_id]
        
        if branch_id != 0:
            query += " AND u.branch_id = %s"
            params.append(branch_id)
        
        if dept_id.upper() != 'ALL':
            query += " AND (u.dept_id = %s OR d.dept_name = %s)"
            params.extend([dept_id, dept_id])
        
        query += " ORDER BY u.name"
        
        cursor.execute(query, tuple(params))
        staff = cursor.fetchall()
        return [StaffForManagerResponse(**s) for s in staff]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get staff: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def assign_manager(data: AssignManagerRequest):
    """Set role_id=2 and insert all branch+dept assignments into manager_assignments."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if not data.assignments:
            raise HTTPException(status_code=400, detail="At least one branch-department assignment is required")
        if any(not a.dept_id or not a.dept_id.strip() for a in data.assignments):
            raise HTTPException(status_code=400, detail="Department is required for each assignment")

        # Verify user exists and get org_id
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s AND status = 1", (data.user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Staff not found")
        org_id = user['parent_id']

        # Promote to manager
        cursor.execute(
            "UPDATE users SET role_id = 2, updated_at = %s WHERE user_id = %s",
            (get_utc_now(), data.user_id)
        )

        # Replace all assignments
        cursor.execute("DELETE FROM manager_assignments WHERE user_id = %s", (data.user_id,))
        for a in data.assignments:
            cursor.execute(
                """INSERT INTO manager_assignments (org_id, user_id, branch_id, dept_id, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (org_id, data.user_id, a.branch_id, a.dept_id, get_utc_now(), get_utc_now())
            )

        conn.commit()
        return {"success": True, "message": "Manager assigned successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to assign manager: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_managers(org_id: str):
    """Get all managers with their branch+dept assignments."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            """SELECT u.user_id, u.name, u.emp_id, u.role_id,
                      ma.branch_id, ma.dept_id,
                      COALESCE(b.branch_name, '') as branch_name,
                      COALESCE(d.dept_name, '') as dept_name
               FROM users u
               LEFT JOIN manager_assignments ma ON u.user_id = ma.user_id
               LEFT JOIN branch_info b ON ma.branch_id = b.branch_id AND b.org_id = %s
               LEFT JOIN dept_info d ON ma.dept_id = d.dept_id AND d.org_id = %s
               WHERE u.parent_id = %s AND u.role_id = 2 AND u.status = 1
               ORDER BY u.name""",
            (org_id, org_id, org_id)
        )
        rows = cursor.fetchall()

        # Group assignments by manager
        managers: Dict[str, ManagerResponse] = {}
        for row in rows:
            uid = row['user_id']
            if uid not in managers:
                managers[uid] = ManagerResponse(
                    user_id=uid, name=row['name'],
                    emp_id=row['emp_id'], role_id=row['role_id'],
                    assignments=[]
                )
            if row['branch_id']:
                managers[uid].assignments.append(ManagerAssignment(
                    branch_id=row['branch_id'], branch_name=row['branch_name'],
                    dept_id=row['dept_id'], dept_name=row['dept_name']
                ))

        return list(managers.values())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get managers: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def remove_manager(user_id: str):
    """Update manager role_id back to 3 (Staff) and remove all assignments."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute(
            "UPDATE users SET role_id = 3, updated_at = %s WHERE user_id = %s AND role_id = 2",
            (get_utc_now(), user_id)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Manager not found")

        cursor.execute("DELETE FROM manager_assignments WHERE user_id = %s", (user_id,))

        conn.commit()
        return {"success": True, "message": "Manager removed successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to remove manager: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_manager_permissions(user_id: str):
    """Get manager permissions"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id for the manager
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Manager not found")
        
        org_id = user['parent_id']
        
        # Get all settings
        cursor.execute("SELECT setting_id, setting_name FROM settings ORDER BY setting_id")
        settings = cursor.fetchall()
        
        # Get manager's permissions
        cursor.execute(
            "SELECT setting_id FROM manager_permissions WHERE user_id = %s AND org_id = %s AND status = 1",
            (user_id, org_id)
        )
        enabled_settings = {row['setting_id'] for row in cursor.fetchall()}
        
        # Build permissions dict
        permissions = {setting['setting_name']: setting['setting_id'] in enabled_settings for setting in settings}
        
        return {"success": True, "permissions": permissions}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get permissions: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def save_manager_permissions(user_id: str, data: ManagerPermissionsRequest):
    """Save manager permissions"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id for the manager
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s AND role_id = 2", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Manager not found")
        
        org_id = user['parent_id']
        
        # Get all settings with their IDs
        cursor.execute("SELECT setting_id, setting_name FROM settings")
        settings = {row['setting_name']: row['setting_id'] for row in cursor.fetchall()}
        
        # Delete existing permissions
        cursor.execute(
            "DELETE FROM manager_permissions WHERE user_id = %s AND org_id = %s",
            (user_id, org_id)
        )
        
        # Insert new permissions
        for setting_name, enabled in data.permissions.items():
            if setting_name in settings and enabled:
                cursor.execute(
                    """INSERT INTO manager_permissions (org_id, user_id, setting_id, status, created_at, updated_at)
                       VALUES (%s, %s, %s, 1, %s, %s)""",
                    (org_id, user_id, settings[setting_name], get_utc_now(), get_utc_now())
                )
        
        conn.commit()
        return {"success": True, "message": "Permissions saved successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save permissions: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def check_manager_permission(user_id: str, setting_name: str):
    """Check if manager has specific permission"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id for the manager
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s AND role_id = 2", (user_id,))
        user = cursor.fetchone()
        if not user:
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

async def get_manager_enabled_settings(user_id: str):
    """Get all enabled settings for a manager"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id for the manager
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s AND role_id = 2", (user_id,))
        user = cursor.fetchone()
        if not user:
            return []
        
        org_id = user['parent_id']
        
        # Get enabled settings for the manager
        cursor.execute(
            """SELECT ps.setting_name FROM manager_permissions mp
               JOIN settings ps ON mp.setting_id = ps.setting_id
               WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1
               ORDER BY ps.setting_id""",
            (user_id, org_id)
        )
        settings = cursor.fetchall()
        return [setting['setting_name'] for setting in settings]
    except Exception:
        return []
    finally:
        cursor.close()
        return_connection(conn)


async def get_staff_query_scope(user_id: str):
    """`get_manager_scope` for the STAFF-list queries of home_functions (they also append `scope.clause()`).

    Flag off (default): exactly `get_manager_scope`. ENFORCE_MANAGER_SCOPE on: a manager's branch / department lists are
    dropped here because the scope clause (core_functions/scope.py) already carries them, united with the staff assigned
    to the manager (`users.manager_id`); keeping both would hide assigned staff that sit outside the assigned branches."""
    org_id, branch_ids, dept_ids = await get_manager_scope(user_id)
    if staff_scope.enforced():
        return org_id, None, None
    return org_id, branch_ids, dept_ids


async def get_manager_scope(user_id: str):
    """
    Returns (org_id, branch_ids, dept_ids) for a manager (role_id=2),
    or (user_id, None, None) for an admin (role_id=1).
    branch_ids and dept_ids are lists; None means no restriction.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            return user_id, None, None
        if user['role_id'] == 1:
            return user_id, None, None
        if user['role_id'] == 2:
            org_id = user['parent_id']
            cursor.execute(
                "SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s",
                (user_id,)
            )
            rows = cursor.fetchall()
            branch_ids = list({int(r['branch_id']) for r in rows if r['branch_id'] is not None})
            dept_ids = list({r['dept_id'] for r in rows if r['dept_id'] is not None})
            return org_id, branch_ids or None, dept_ids or None
        # role_id=3 or other: return as-is
        return user_id, None, None
    except Exception:
        return user_id, None, None
    finally:
        cursor.close()
        return_connection(conn)
