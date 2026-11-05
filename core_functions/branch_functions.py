from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from pydantic import BaseModel
from typing import Optional
import uuid
from psycopg2.extras import RealDictCursor



class AddBranchRequest(BaseModel):
    branch_name: str
    branch_address: str
    branch_latitude: float = 0.0
    branch_longitude: float = 0.0
    punch_radius: int = 100
    org_id: str  # Admin's user_id

class UpdateBranchRequest(BaseModel):
    branch_name: str
    branch_address: str
    branch_latitude: float = 0.0
    branch_longitude: float = 0.0
    punch_radius: int = 100
    org_id: str  # Admin's user_id

class DeleteBranchRequest(BaseModel):
    branch_id: str
    org_id: str  # Admin's user_id for verification

class BranchResponse(BaseModel):
    branch_id: str
    branch_name: str
    branch_address: str
    branch_latitude: float
    branch_longitude: float
    punch_radius: int
    org_id: str  # This is admin's user_id
    status: int

class BranchStaffResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    mobile_number: str
    email: str
    dept_name: str
    current_branch_id: str
    current_branch_name: str

class ReassignStaffRequest(BaseModel):
    staff_ids: list[str]
    new_branch_id: str
    org_id: str
    delete_branch_id: Optional[str] = None  # Branch to delete after reassignment

async def add_branch(branch_data: AddBranchRequest):
    """Add a new branch to the database"""
    
    if not branch_data.branch_name.strip():
        raise HTTPException(status_code=400, detail="Branch name is required")
    
    if not branch_data.branch_address.strip():
        raise HTTPException(status_code=400, detail="Branch address is required")
    
    if not branch_data.org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if branch name already exists for this organization
        cursor.execute(
            "SELECT branch_id FROM branch_info WHERE branch_name = %s AND org_id = %s",
            (branch_data.branch_name.strip(), branch_data.org_id)
        )
        existing_branch = cursor.fetchone()
        
        if existing_branch:
            raise HTTPException(status_code=400, detail="Branch name already exists")
        
        # Get next branch_id (auto-increment)
        cursor.execute("SELECT COALESCE(MAX(branch_id), 0) + 1 as next_id FROM branch_info")
        branch_id = cursor.fetchone()['next_id']
        
        # Insert new branch with GPS location and punch radius
        insert_query = """
            INSERT INTO branch_info (branch_id, branch_name, branch_address, gps_location, punch_radius, org_id, status, created_at, updated_at)
            VALUES (%s, %s, %s, POINT(%s, %s), %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
        
        cursor.execute(insert_query, (
            branch_id,
            branch_data.branch_name.strip(),
            branch_data.branch_address.strip(),
            branch_data.branch_longitude,
            branch_data.branch_latitude,
            branch_data.punch_radius,
            branch_data.org_id,
            1
        ))
        
        conn.commit()
        
        return {
            "message": "Branch added successfully",
            "branch_id": str(branch_id),
            "branch_name": branch_data.branch_name.strip()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to add branch")
    finally:
        cursor.close()
        return_connection(conn)

async def get_branch_by_id(branch_id: str, org_id: str = None):
    """Get branch details by branch ID (scoped to org_id when supplied)"""
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        sql = "SELECT branch_id, branch_name, branch_address, COALESCE(gps_location[0], 0) as branch_longitude, COALESCE(gps_location[1], 0) as branch_latitude, COALESCE(punch_radius, 100) as punch_radius, org_id, status FROM branch_info WHERE branch_id = %s AND status = 1"
        params = [branch_id]
        if org_id:
            sql += " AND org_id = %s"
            params.append(org_id)
        cursor.execute(sql, tuple(params))
        branch = cursor.fetchone()
        
        if not branch:
            raise HTTPException(status_code=404, detail="Branch not found")
        
        return BranchResponse(**branch)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to get branch")
    finally:
        cursor.close()
        return_connection(conn)

async def update_branch(branch_id: str, branch_data: UpdateBranchRequest):
    """Update an existing branch"""
    if not branch_data.branch_name.strip():
        raise HTTPException(status_code=400, detail="Branch name is required")
    
    if not branch_data.branch_address.strip():
        raise HTTPException(status_code=400, detail="Branch address is required")
    
    if not branch_data.org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if branch exists and belongs to the organization
        cursor.execute(
            "SELECT branch_id FROM branch_info WHERE branch_id = %s AND org_id = %s AND status = 1",
            (branch_id, branch_data.org_id)
        )
        existing_branch = cursor.fetchone()
        
        if not existing_branch:
            raise HTTPException(status_code=404, detail="Branch not found or access denied")
        
        # Update branch with GPS location and punch radius
        update_query = """
            UPDATE branch_info 
            SET branch_name = %s, branch_address = %s, gps_location = POINT(%s, %s), punch_radius = %s, updated_at = CURRENT_TIMESTAMP
            WHERE branch_id = %s AND org_id = %s
        """
        cursor.execute(update_query, (
            branch_data.branch_name.strip(),
            branch_data.branch_address.strip(),
            branch_data.branch_longitude,
            branch_data.branch_latitude,
            branch_data.punch_radius,
            branch_id,
            branch_data.org_id
        ))
        
        conn.commit()
        
        return {
            "message": "Branch updated successfully",
            "branch_id": branch_id,
            "branch_name": branch_data.branch_name.strip()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to update branch")
    finally:
        cursor.close()
        return_connection(conn)

async def delete_branch(branch_id: str, org_id: str):
    """Delete a branch permanently from database"""
    if not branch_id:
        raise HTTPException(status_code=400, detail="Branch ID is required")
    
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if branch exists and belongs to the organization
        cursor.execute(
            "SELECT branch_id, branch_name FROM branch_info WHERE branch_id = %s AND org_id = %s AND status = 1",
            (branch_id, org_id)
        )
        existing_branch = cursor.fetchone()
        
        if not existing_branch:
            raise HTTPException(status_code=404, detail="Branch not found or access denied")
        
        # Check if there are any staff assigned to this branch (scoped to the org)
        cursor.execute(
            "SELECT COUNT(*) as staff_count FROM users WHERE branch_id = %s AND parent_id = %s AND status = 1",
            (branch_id, org_id)
        )
        staff_count = cursor.fetchone()['staff_count']
        
        if staff_count > 0:
            raise HTTPException(status_code=400, detail=f"Cannot delete branch. {staff_count} staff members are assigned to this branch.")
        
        # Permanently delete the branch from database
        cursor.execute(
            "DELETE FROM branch_info WHERE branch_id = %s AND org_id = %s",
            (branch_id, org_id)
        )

        conn.commit()

        return {
            "message": "Branch deleted successfully",
            "branch_id": branch_id,
            "branch_name": existing_branch['branch_name']
        }
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete branch")
    finally:
        cursor.close()
        return_connection(conn)

async def get_branch_staff(branch_id: str, org_id: str):
    """Get all staff assigned to a specific branch"""
    if not branch_id or not org_id:
        raise HTTPException(status_code=400, detail="Branch ID and Organization ID are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        
        query = """
            SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email,
                   COALESCE(d.dept_name, '') as dept_name, 
                   CAST(u.branch_id AS VARCHAR) as current_branch_id, 
                   COALESCE(b.branch_name, '') as current_branch_name
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            WHERE u.branch_id = %s AND u.parent_id = %s AND u.role_id != 1 AND u.status = 1
            ORDER BY u.name
        """
        
        cursor.execute(query, (org_id, org_id, branch_id, org_id))
        staff_data = cursor.fetchall()
        
        return [BranchStaffResponse(**staff) for staff in staff_data]
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Failed to get branch staff")
    finally:
        cursor.close()
        return_connection(conn)

async def reassign_staff(reassign_data: ReassignStaffRequest):
    """Reassign staff to a different branch and optionally delete the source branch"""
    if not reassign_data.staff_ids or not reassign_data.new_branch_id or not reassign_data.org_id:
        raise HTTPException(status_code=400, detail="Staff IDs, new branch ID, and organization ID are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Verify new branch exists and belongs to organization
        cursor.execute(
            "SELECT branch_id FROM branch_info WHERE branch_id = %s AND org_id = %s AND status = 1",
            (reassign_data.new_branch_id, reassign_data.org_id)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Target branch not found")
        
        # The branch to be deleted must also belong to this organisation
        if reassign_data.delete_branch_id:
            cursor.execute(
                "SELECT branch_id FROM branch_info WHERE branch_id = %s AND org_id = %s",
                (reassign_data.delete_branch_id, reassign_data.org_id)
            )
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Branch not found or access denied")

        # Update staff branch assignments
        placeholders = ','.join(['%s'] * len(reassign_data.staff_ids))
        update_query = f"""
            UPDATE users 
            SET branch_id = %s 
            WHERE user_id IN ({placeholders}) AND parent_id = %s AND status = 1
        """
        
        params = [reassign_data.new_branch_id] + reassign_data.staff_ids + [reassign_data.org_id]
        cursor.execute(update_query, tuple(params))
        
        affected_rows = cursor.rowcount
        
        # If delete_branch_id is provided, delete the branch after reassignment
        if reassign_data.delete_branch_id:
            # Verify no staff remain in the branch to be deleted
            cursor.execute(
                "SELECT COUNT(*) as staff_count FROM users WHERE branch_id = %s AND parent_id = %s AND status = 1",
                (reassign_data.delete_branch_id, reassign_data.org_id)
            )
            remaining_staff = cursor.fetchone()['staff_count']
            
            if remaining_staff == 0:
                # Delete the branch
                cursor.execute(
                    "DELETE FROM branch_info WHERE branch_id = %s AND org_id = %s",
                    (reassign_data.delete_branch_id, reassign_data.org_id)
                )
                conn.commit()
                
                return {
                    "message": f"Successfully reassigned {affected_rows} staff members and deleted branch",
                    "reassigned_count": affected_rows,
                    "branch_deleted": True
                }
            else:
                conn.commit()
                return {
                    "message": f"Successfully reassigned {affected_rows} staff members. Branch still has {remaining_staff} staff members.",
                    "reassigned_count": affected_rows,
                    "branch_deleted": False
                }
        
        conn.commit()
        
        return {
            "message": f"Successfully reassigned {affected_rows} staff members",
            "reassigned_count": affected_rows,
            "branch_deleted": False
        }
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to reassign staff")
    finally:
        cursor.close()
        return_connection(conn)

