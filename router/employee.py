from fastapi import APIRouter, HTTPException, Query
from core_functions.emp_pers_detail_functions import get_employee_personal_details, update_employee_personal_details, EmployeePersonalDetails, UpdateEmployeePersonalDetails
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from utils.timezone import IST
from datetime import datetime

router = APIRouter(prefix="/employee", tags=["Employee"])

@router.get("/personal-details/{user_id}", response_model=EmployeePersonalDetails)
async def get_employee_personal_details_endpoint(user_id: str):
    """Get personal details for employee (role_id 2 or 3)"""
    return await get_employee_personal_details(user_id)

@router.put("/personal-details/{user_id}")
async def update_employee_personal_details_endpoint(user_id: str, data: UpdateEmployeePersonalDetails):
    """Update personal details for employee (role_id 2 or 3)"""
    return await update_employee_personal_details(user_id, data)

@router.get("/today-birthdays/{org_id}")
async def get_today_birthdays(org_id: str, exclude_user_id: str = Query(...)):
    """Get employees whose birthday (month+day) matches today, excluding the viewer themselves"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        today = datetime.now(IST).date()
        cursor.execute("""
            SELECT u.name
            FROM users u
            JOIN users_additional_info ua ON u.user_id = ua.user_id
            WHERE u.parent_id = %s AND u.status = 1 AND u.role_id IN (2, 3)
              AND u.user_id != %s
              AND ua.dob IS NOT NULL
              AND EXTRACT(MONTH FROM ua.dob) = %s
              AND EXTRACT(DAY FROM ua.dob) = %s
        """, (org_id, exclude_user_id, today.month, today.day))
        rows = cursor.fetchall()
        return {"birthdays": [r["name"] for r in rows]}
    finally:
        cursor.close()
        return_connection(conn)
