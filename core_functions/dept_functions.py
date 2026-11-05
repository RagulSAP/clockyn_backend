from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from pydantic import BaseModel
from psycopg2.extras import RealDictCursor
import uuid


class DeptRequest(BaseModel):
    dept_name: str
    org_id: str


async def get_departments(org_id: str):
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT d.dept_id, d.dept_name,
                   COUNT(u.user_id) AS staff_count
            FROM dept_info d
            LEFT JOIN users u ON u.dept_id = d.dept_id
                              AND (u.parent_id = d.org_id OR u.org_id = d.org_id)
                              AND u.status = 1
                              AND u.role_id != 1
            WHERE d.org_id = %s
            GROUP BY d.dept_id, d.dept_name
            ORDER BY d.dept_name
            """,
            (org_id,)
        )
        return cursor.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get departments: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def add_department(data: DeptRequest):
    if not data.dept_name.strip():
        raise HTTPException(status_code=400, detail="Department name is required")
    if not data.org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Check duplicate
        cursor.execute(
            "SELECT dept_id FROM dept_info WHERE LOWER(dept_name) = LOWER(%s) AND org_id = %s",
            (data.dept_name.strip(), data.org_id)
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Department name already exists")

        dept_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO dept_info (dept_id, dept_name, org_id, created_at) VALUES (%s, %s, %s, CURRENT_TIMESTAMP)",
            (dept_id, data.dept_name.strip(), data.org_id)
        )
        conn.commit()
        return {"success": True, "message": "Department added successfully", "dept_id": dept_id, "dept_name": data.dept_name.strip()}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to add department: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def update_department(dept_id: str, data: DeptRequest):
    if not data.dept_name.strip():
        raise HTTPException(status_code=400, detail="Department name is required")
    if not data.org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Check exists
        cursor.execute(
            "SELECT dept_id FROM dept_info WHERE dept_id = %s AND org_id = %s",
            (dept_id, data.org_id)
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Department not found")

        # Check duplicate name (excluding self)
        cursor.execute(
            "SELECT dept_id FROM dept_info WHERE LOWER(dept_name) = LOWER(%s) AND org_id = %s AND dept_id != %s",
            (data.dept_name.strip(), data.org_id, dept_id)
        )
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Department name already exists")

        cursor.execute(
            "UPDATE dept_info SET dept_name = %s WHERE dept_id = %s AND org_id = %s",
            (data.dept_name.strip(), dept_id, data.org_id)
        )
        conn.commit()
        return {"success": True, "message": "Department updated successfully", "dept_id": dept_id, "dept_name": data.dept_name.strip()}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update department: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def delete_department(dept_id: str, org_id: str):
    if not dept_id or not org_id:
        raise HTTPException(status_code=400, detail="Department ID and Organization ID are required")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT dept_id, dept_name FROM dept_info WHERE dept_id = %s AND org_id = %s",
            (dept_id, org_id)
        )
        dept = cursor.fetchone()
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found")

        cursor.execute(
            "UPDATE users SET dept_id = NULL WHERE dept_id = %s AND (parent_id = %s OR org_id = %s)",
            (dept_id, org_id, org_id)
        )
        cursor.execute("DELETE FROM dept_info WHERE dept_id = %s AND org_id = %s", (dept_id, org_id))
        conn.commit()
        return {"success": True, "message": "Department deleted successfully", "dept_id": dept_id, "dept_name": dept['dept_name']}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete department: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)
