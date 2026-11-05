from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional
from core_functions.report_functions import generate_monthly_attendance_pdf, get_staff_salary
from core_functions.db_function import get_db_connection, return_connection
from core_functions.permission_checker import check_manager_permission_middleware
from psycopg2.extras import RealDictCursor
import io
from pypdf import PdfWriter, PdfReader

router = APIRouter(prefix="/api", tags=["Reports"])


@router.get("/reports/staff-salary/{user_id}")
async def staff_salary(user_id: str, year: int, month: int):
    return await get_staff_salary(user_id, year, month)


@router.get("/reports/monthly-attendance/{user_id}/{month}")
async def monthly_attendance_pdf(user_id: str, month: str):
    pdf_bytes = await generate_monthly_attendance_pdf(user_id, month)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=attendance_{month}.pdf"}
    )


@router.get("/reports/monthly-attendance-all/{org_id}/{month}")
async def monthly_attendance_all_pdf(org_id: str, month: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Resolve the actual org owner user_id:
        # If the caller is role 1, their user_id = org_id (stored in org_info)
        # If the caller is role 2, org_id is null — use parent_id to find the org owner
        cursor.execute("""
            SELECT COALESCE(u.org_id, u.parent_id) AS resolved_org_id
            FROM users u
            WHERE u.user_id = %s
        """, (org_id,))
        row = cursor.fetchone()
        resolved_org_id = row['resolved_org_id'] if row else org_id

        cursor.execute("""
            SELECT u.user_id, u.emp_id
            FROM users u
            WHERE u.parent_id = %s
              AND u.role_id != 1
              AND u.status = 1
            ORDER BY u.name
        """, (resolved_org_id,))
        staff_list = cursor.fetchall()
    finally:
        cursor.close()
        return_connection(conn)

    if not staff_list:
        return Response(status_code=404, content="No staff found")

    writer = PdfWriter()
    for staff in staff_list:
        try:
            pdf_bytes = await generate_monthly_attendance_pdf(staff['user_id'], month)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue

    merged_buf = io.BytesIO()
    writer.write(merged_buf)

    return Response(
        content=merged_buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=attendance_all_{month}.pdf"}
    )


@router.get("/reports/manager/{manager_id}/monthly-attendance/{user_id}/{month}")
async def manager_view_staff_attendance(manager_id: str, user_id: str, month: str):
    if not await check_manager_permission_middleware(manager_id, "Attendance Reports"):
        raise HTTPException(status_code=403, detail="Access denied. Attendance Reports permission required.")

    # Ensure the staff is under this manager
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.user_id FROM users u
            JOIN manager_assignments ma ON ma.user_id = %s AND ma.branch_id = u.branch_id
            WHERE u.user_id = %s AND u.role_id = 3 AND u.status = 1
        """, (manager_id, user_id))
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Staff not under your management.")
    finally:
        cursor.close()
        return_connection(conn)

    pdf_bytes = await generate_monthly_attendance_pdf(user_id, month)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=attendance_{month}.pdf"}
    )


@router.get("/reports/manager/{manager_id}/monthly-attendance-all/{month}")
async def manager_view_all_attendance(manager_id: str, month: str):
    if not await check_manager_permission_middleware(manager_id, "Attendance Reports"):
        raise HTTPException(status_code=403, detail="Access denied. Attendance Reports permission required.")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Resolve org: manager's parent_id = admin's user_id = org owner
        cursor.execute("""
            SELECT COALESCE(org_id, parent_id) AS resolved_org_id
            FROM users WHERE user_id = %s
        """, (manager_id,))
        row = cursor.fetchone()
        resolved_org_id = row['resolved_org_id'] if row else manager_id

        cursor.execute("""
            SELECT DISTINCT u.user_id, u.emp_id
            FROM users u
            WHERE (u.parent_id = %s OR u.user_id = %s)
              AND u.role_id IN (2, 3) AND u.status = 1
            ORDER BY u.emp_id
        """, (resolved_org_id, manager_id))
        staff_list = cursor.fetchall()
    finally:
        cursor.close()
        return_connection(conn)

    if not staff_list:
        return Response(status_code=404, content="No staff found under this manager")

    writer = PdfWriter()
    for staff in staff_list:
        try:
            pdf_bytes = await generate_monthly_attendance_pdf(staff['user_id'], month)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue

    merged_buf = io.BytesIO()
    writer.write(merged_buf)

    return Response(
        content=merged_buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=attendance_all_{month}.pdf"}
    )
