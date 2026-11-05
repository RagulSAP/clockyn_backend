from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from datetime import date
from core_functions import payroll_validation


def create_advance(org_id: str, user_id: str, created_by: str, advance_amt: float, advance_date: date):
    """
    Store an advance record.
    created_by: role_id 1 or 2 (admin/manager)
    user_id:    role_id 3 (staff)
    advance_id is auto-set to the generated id after insert.
    advance_amt: 0 < x <= 9,999,999.99 with at most 2 decimals (D-15), else 400 "Advance amount must be greater than 0" etc.
    """
    advance_amt = payroll_validation.parse_amount(advance_amt, "Advance amount")
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Validate created_by is role 1 (admin)
        cur.execute(
            "SELECT role_id FROM users WHERE user_id = %s",
            (created_by,)
        )
        creator = cur.fetchone()
        if not creator or creator['role_id'] != 1:
            raise HTTPException(status_code=403, detail="Only admin (role_id = 1) can assign advances")

        # Ensure the admin belongs to the target org (prevent cross-tenant advances)
        cur.execute(
            "SELECT org_id FROM org_info WHERE parent_id = %s AND org_id = %s",
            (created_by, org_id)
        )
        if not cur.fetchone():
            raise HTTPException(status_code=403, detail="Admin does not have access to this organization")

        # Validate user_id is role 2 or 3
        cur.execute(
            "SELECT role_id FROM users WHERE user_id = %s",
            (user_id,)
        )
        employee = cur.fetchone()
        if not employee or employee['role_id'] not in (2, 3):
            raise HTTPException(status_code=400, detail="Advance can only be assigned to manager (role_id = 2) or staff (role_id = 3)")

        # Insert and get the generated id
        cur.execute(
            """
            INSERT INTO advance_details
                (advance_amt, date, user_id, created_by, org_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (advance_amt, advance_date, user_id, created_by, org_id)
        )
        new_id = cur.fetchone()['id']

        # Set advance_id = id
        cur.execute(
            "UPDATE advance_details SET advance_id = %s WHERE id = %s",
            (new_id, new_id)
        )
        conn.commit()

        # Return the full row
        cur.execute(
            "SELECT * FROM advance_details WHERE id = %s",
            (new_id,)
        )
        return dict(cur.fetchone())
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to create advance")
    finally:
        cur.close()
        return_connection(conn)


def get_advances_by_org(org_id: str):
    """Get all advance records for an org with employee details."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT ad.id, ad.advance_id, ad.advance_amt, ad.date,
                   ad.user_id, u.name as employee_name, u.emp_id,
                   ad.created_by, cb.name as created_by_name,
                   ad.org_id
            FROM advance_details ad
            JOIN users u  ON ad.user_id   = u.user_id
            JOIN users cb ON ad.created_by = cb.user_id
            WHERE ad.org_id = %s
            ORDER BY ad.date DESC, ad.id DESC
            """,
            (org_id,)
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch advances")
    finally:
        cur.close()
        return_connection(conn)


def get_advances_by_user(user_id: str, org_id: str):
    """Get all advance records for a specific employee."""
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT ad.id, ad.advance_id, ad.advance_amt, ad.date,
                   ad.user_id, u.name as employee_name, u.emp_id,
                   ad.created_by, cb.name as created_by_name,
                   ad.org_id
            FROM advance_details ad
            JOIN users u  ON ad.user_id   = u.user_id
            JOIN users cb ON ad.created_by = cb.user_id
            WHERE ad.user_id = %s AND ad.org_id = %s
            ORDER BY ad.date DESC, ad.id DESC
            """,
            (user_id, org_id)
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch advances")
    finally:
        cur.close()
        return_connection(conn)


def delete_advance(advance_id: int, org_id: str):
    """Delete an advance record."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM advance_details WHERE id = %s AND org_id = %s",
            (advance_id, org_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Advance record not found")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to delete advance")
    finally:
        cur.close()
        return_connection(conn)

