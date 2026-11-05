from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel
from typing import Optional
import os
from datetime import timedelta
from utils.timezone import get_utc_now, ist_today

DB_SCHEMA = os.getenv('DB_SCHEMA')


class OrgStatusRequest(BaseModel):
    changed_by: str  # phone number of the logged-in gen_admin


class OrgUpdateRequest(BaseModel):
    org_name: str
    org_address: str
    gst_number: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    pincode: Optional[str] = None


async def update_org_details(org_id: str, data: OrgUpdateRequest):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now = get_utc_now()

        cursor.execute(
            """
            UPDATE org_info
            SET org_name = %s,
                org_address = %s,
                org_gst = %s,
                state = %s,
                district = %s,
                pincode = %s,
                updated_at = %s
            WHERE org_id = %s
            RETURNING parent_id
            """,
            (data.org_name, data.org_address, data.gst_number or "", data.state, data.district, data.pincode, now, org_id),
        )
        org = cursor.fetchone()
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        if org["parent_id"]:
            update_fields = []
            params = []
            if data.mobile is not None:
                update_fields.append("mobile_number = %s")
                params.append(data.mobile)
            if data.email is not None:
                update_fields.append("email = %s")
                params.append(data.email)
            if update_fields:
                params.append(org["parent_id"])
                cursor.execute(
                    f"UPDATE users SET {', '.join(update_fields)} WHERE user_id = %s",
                    params,
                )

        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def update_org_status(org_id: str, status: int, changed_by: str):
    """
    status: 1=active, 0=inactive, 2=delete
    When inactive or delete → set all users under org to status=0
    """
    if status not in (0, 1, 2):
        raise HTTPException(status_code=400, detail="Invalid status value")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT org_id FROM org_info WHERE org_id = %s", (org_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Organization not found")

        # Fetch role 2 & 3 user_ids before updating, so we can force-logout them
        affected_user_ids = []
        if status in (0, 2):
            cursor.execute(
                "SELECT user_id FROM users WHERE parent_id = %s AND role_id IN (2, 3)",
                (org_id,)
            )
            affected_user_ids = [r["user_id"] for r in cursor.fetchall()]

        if status == 2:
            cursor.execute(
                "UPDATE users SET status = 2 WHERE parent_id = %s OR (role_id = 1 AND user_id IN (SELECT parent_id FROM org_info WHERE org_id = %s))",
                (org_id, org_id)
            )
        elif status == 0:
            cursor.execute(
                "UPDATE users SET status = 0 WHERE parent_id = %s OR (role_id = 1 AND user_id IN (SELECT parent_id FROM org_info WHERE org_id = %s))",
                (org_id, org_id)
            )
        elif status == 1:
            cursor.execute(
                "UPDATE users SET status = 1 WHERE parent_id = %s OR (role_id = 1 AND user_id IN (SELECT parent_id FROM org_info WHERE org_id = %s))",
                (org_id, org_id)
            )

        new_status = 0 if status == 2 else status
        cursor.execute(
            "UPDATE org_info SET status = %s WHERE org_id = %s",
            (new_status, org_id)
        )

        cursor.execute(
            f"""INSERT INTO {DB_SCHEMA}.activity_tracking (org_id, changed_by, status)
               VALUES (%s, %s, %s)""",
            (org_id, changed_by[:20], status)
        )

        conn.commit()

        # Force-logout all role 2 & 3 users with active SSE sessions
        if affected_user_ids:
            from core_functions.session_registry import force_logout_user
            for user_id in affected_user_ids:
                await force_logout_user(user_id)

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_super_admin_stats():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT COUNT(*) AS cnt FROM org_info")
        total_orgs = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) AS cnt FROM users WHERE status = 1")
        total_users = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) AS cnt FROM punchapp_subscription WHERE status = 'ACTIVE'")
        active_subs = cursor.fetchone()["cnt"]

        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM org_info o
            WHERE NOT EXISTS (
                SELECT 1 FROM punchapp_subscription s
                WHERE s.org_id = o.org_id AND s.status = 'ACTIVE'
            )
        """)
        free_trial_orgs = cursor.fetchone()["cnt"]

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS rev
            FROM gen_payments
            WHERE status = 'PAID'
              AND DATE_TRUNC('month', created_at) = DATE_TRUNC('month', NOW())
        """)
        revenue = cursor.fetchone()["rev"]

        cursor.execute("SELECT COUNT(*) AS cnt FROM gen_orders WHERE status = 'CREATED'")
        pending_payments = cursor.fetchone()["cnt"]

        cursor.execute("SELECT COUNT(*) AS cnt FROM gen_payments WHERE status = 'FAILED'")
        failed_payments = cursor.fetchone()["cnt"]

        cursor.execute("""
            SELECT COUNT(DISTINCT user_id) AS cnt
            FROM attendance
            WHERE attendance_date = %s AND punch_in IS NOT NULL
        """, (ist_today(),))
        today_attendance = cursor.fetchone()["cnt"]

        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM requests
            WHERE status = 'pending'
        """)
        open_requests = cursor.fetchone()["cnt"]

        return {
            "total_orgs": total_orgs,
            "total_users": total_users,
            "active_subscriptions": active_subs,
            "free_trial_orgs": free_trial_orgs,
            "revenue_this_month": float(revenue),
            "pending_payments": pending_payments,
            "failed_payments": failed_payments,
            "today_attendance": today_attendance,
            "open_requests": open_requests,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch dashboard stats: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                u.user_id,
                u.name,
                u.emp_id,
                u.email,
                u.mobile_number,
                u.status,
                u.role_id,
                u.created_at,
                CASE u.role_id
                    WHEN 1 THEN 'Admin'
                    WHEN 2 THEN 'Manager'
                    WHEN 3 THEN 'Employee'
                    ELSE 'Unknown'
                END AS role_name,
                COALESCE(o_admin.org_name, o_emp.org_name) AS org_name,
                d.dept_name,
                b.branch_name
            FROM users u
            LEFT JOIN org_info o_admin
                ON u.role_id = 1 AND o_admin.parent_id = u.user_id
            LEFT JOIN org_info o_emp
                ON u.role_id IN (2,3) AND o_emp.org_id = u.parent_id
            LEFT JOIN dept_info d
                ON d.dept_id = u.dept_id
                AND d.org_id = COALESCE(o_admin.org_id, o_emp.org_id)
            LEFT JOIN branch_info b
                ON b.branch_id = u.branch_id
                AND b.org_id = COALESCE(o_admin.org_id, o_emp.org_id)
            ORDER BY u.created_at DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                "user_id":     r["user_id"],
                "name":        r["name"],
                "emp_id":      r["emp_id"] or "—",
                "email":       r["email"] or "—",
                "mobile":      r["mobile_number"] or "—",
                "status":      "Active" if r["status"] == 1 else "Inactive",
                "role_id":     r["role_id"],
                "role_name":   r["role_name"],
                "org_name":    r["org_name"] or "—",
                "dept_name":   r["dept_name"] or "—",
                "branch_name": r["branch_name"] or "—",
                "joined":      r["created_at"].strftime("%d %b %Y") if r["created_at"] else "—",
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch users: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_all_organizations():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                o.org_id,
                o.org_name,
                o.org_address,
                o.org_gst,
                o.state,
                o.district,
                o.pincode,
                o.created_at,
                COALESCE(o.updated_at, o.created_at) AS joined_at,
                u.email,
                u.mobile_number,
                COUNT(DISTINCT emp.user_id) AS emp_count,
                COALESCE(att.attendance_activity_count, 0) AS attendance_activity_count,
                COALESCE(sub.status, 'FREE_TRIAL') AS sub_status,
                COALESCE(gp.plan_name, 'Free Trial') AS plan_name,
                sub.start_date,
                sub.end_date,
                ft.end_date AS free_trial_end_date,
                CASE
                    WHEN sub.status = 'ACTIVE' THEN 'paid'
                    ELSE 'unpaid'
                END AS payment_status,
                COALESCE(o.status, 1) AS org_status
            FROM org_info o
            LEFT JOIN users u
                ON u.user_id = o.parent_id
            LEFT JOIN users emp
                ON emp.parent_id = o.org_id
                AND emp.status = 1
                AND emp.role_id IN (2, 3)
            LEFT JOIN LATERAL (
                SELECT plan_id, status, start_date, end_date
                FROM punchapp_subscription
                WHERE org_id = o.org_id
                ORDER BY created_at DESC
                LIMIT 1
            ) sub ON true
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(
                    CASE WHEN a.punch_in IS NOT NULL THEN 1 ELSE 0 END +
                    CASE WHEN a.punch_out IS NOT NULL THEN 1 ELSE 0 END
                ), 0) AS attendance_activity_count
                FROM attendance a
                JOIN users au
                    ON au.user_id = a.user_id
                    AND au.status = 1
                    AND au.role_id IN (2, 3)
                WHERE a.org_id = o.org_id
                  AND a.attendance_date = %s
            ) att ON true
            LEFT JOIN free_trial ft
                ON ft.org_id = o.org_id
            LEFT JOIN gen_plans gp ON gp.plan_id = sub.plan_id
            GROUP BY
                o.org_id, o.org_name, o.org_address, o.org_gst, o.state, o.district, o.pincode,
                o.created_at, o.updated_at, o.status,
                u.email, u.mobile_number,
                sub.status, gp.plan_name, sub.start_date, sub.end_date,
                ft.end_date, att.attendance_activity_count
            ORDER BY o.created_at DESC
        """, (ist_today(),))
        rows = cursor.fetchall()
        return [
            {
                "org_id":      r["org_id"],
                "org_name":    r["org_name"],
                "org_address": r["org_address"] or "",
                "gst_number":  r["org_gst"] or "",
                "state":       r["state"] or "—",
                "district":    r["district"] or "—",
                "pincode":     str(r["pincode"]) if r["pincode"] else "—",
                "email":       r["email"] or "—",
                "mobile":      r["mobile_number"] or "—",
                "emp_count":   r["emp_count"],
                "attendance_activity_count": int(r["attendance_activity_count"] or 0),
                "sub_status":  r["sub_status"],
                "plan_name":   r["plan_name"],
                "joined":      r["joined_at"].strftime("%d %b %Y") if r["joined_at"] else "—",
                "start_date":  str(r["start_date"]) if r["start_date"] else "—",
                "end_date":    str(r["end_date"]) if r["end_date"] else "—",
                "free_trial_end_date": str(r["free_trial_end_date"]) if r["free_trial_end_date"] else None,
                "free_trial_end": str(r["free_trial_end_date"]) if r["free_trial_end_date"] else None,
                "payment_status": r["payment_status"],
                "org_status":  int(r["org_status"]) if r["org_status"] is not None else 1,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch organizations: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_recent_orgs():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                o.org_id,
                o.org_name,
                o.created_at,
                COUNT(u.user_id) AS user_count,
                COALESCE(sub.status, 'FREE_TRIAL') AS sub_status,
                COALESCE(gp.plan_name, 'Free Trial') AS plan_name,
                sub.end_date
            FROM org_info o
            LEFT JOIN users u ON u.parent_id = o.org_id AND u.status = 1
            LEFT JOIN LATERAL (
                SELECT plan_id, status, end_date
                FROM punchapp_subscription
                WHERE org_id = o.org_id
                ORDER BY created_at DESC
                LIMIT 1
            ) sub ON true
            LEFT JOIN gen_plans gp ON gp.plan_id = sub.plan_id
            GROUP BY o.org_id, o.org_name, o.created_at, sub.status, gp.plan_name, sub.end_date
            ORDER BY o.created_at DESC
        """)
        rows = cursor.fetchall()
        return [
            {
                "org_id": r["org_id"],
                "org_name": r["org_name"],
                "user_count": r["user_count"],
                "plan_name": r["plan_name"],
                "sub_status": r["sub_status"],
                "end_date": str(r["end_date"]) if r["end_date"] else "—",
                "joined": r["created_at"].strftime("%d %b %Y") if r["created_at"] else "—",
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch recent orgs: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_recent_payments():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                gp.razorpay_payment_id,
                o.org_name,
                gpl.plan_name,
                gp.amount,
                gp.status,
                gp.created_at
            FROM gen_payments gp
            LEFT JOIN org_info o ON o.org_id = gp.org_id
            LEFT JOIN gen_orders go ON go.razorpay_order_id = gp.razorpay_order_id
            LEFT JOIN gen_plans gpl ON gpl.plan_id = go.plan_id
            ORDER BY gp.created_at DESC
        """)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "payment_id": r["razorpay_payment_id"],
                "org_name": r["org_name"] or "—",
                "plan_name": r["plan_name"] or "—",
                "amount": float(r["amount"]),
                "status": r["status"],
                "date": r["created_at"].strftime("%d %b %Y") if r["created_at"] else "—",
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch recent payments: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_monthly_revenue():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                TO_CHAR(DATE_TRUNC('month', created_at), 'Mon') AS month,
                DATE_TRUNC('month', created_at) AS month_date,
                COALESCE(SUM(amount), 0) AS revenue
            FROM gen_payments
            WHERE status = 'PAID'
              AND created_at >= NOW() - INTERVAL '12 months'
            GROUP BY DATE_TRUNC('month', created_at)
            ORDER BY month_date ASC
        """)
        rows = cursor.fetchall()
        return [
            {"month": r["month"], "revenue": float(r["revenue"])}
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch monthly revenue: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_plan_breakdown():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT gp.plan_id, gp.plan_name, gp.plan_amount, gp.plan_month,
                   COUNT(ps.subscription_id) AS active_count
            FROM gen_plans gp
            LEFT JOIN punchapp_subscription ps
                ON ps.plan_id = gp.plan_id AND ps.status = 'ACTIVE'
            GROUP BY gp.plan_id, gp.plan_name, gp.plan_amount, gp.plan_month
            ORDER BY gp.plan_amount ASC
        """)
        rows = cursor.fetchall()
        return [
            {
                "plan_id": r["plan_id"],
                "plan_name": r["plan_name"],
                "plan_amount": float(r["plan_amount"]),
                "plan_month": r["plan_month"],
                "active_count": r["active_count"],
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch plan breakdown: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_admin_profile(email: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT email, name, role_id,
                CASE role_id
                    WHEN 1 THEN 'Admin'
                    WHEN 2 THEN 'Staff'
                    ELSE 'Admin'
                END AS role_name
            FROM gen_admin_login WHERE email = %s
        """, (email,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "name":    row["name"] or row["email"],
            "email":   row["email"],
            "role":    row["role_name"],
            "role_id": row["role_id"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def update_admin_profile(email: str, name: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "UPDATE gen_admin_login SET name = %s WHERE email = %s",
            (name, email)
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_user_latest_attendance(user_id: str):
    """Get the latest attendance record (punch in + out times and photos) for a user."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT
                MIN(punch_in)  AS punch_in_time,
                MAX(punch_out) AS punch_out_time,
                (array_agg(punchinface  ORDER BY punch_in ASC))[1]              AS punch_in_photo,
                (array_agg(punchoutface ORDER BY punch_out DESC NULLS LAST))[1] AS punch_out_photo
            FROM attendance
            WHERE user_id = %s
              AND attendance_date = %s
        """, (user_id, ist_today()))
        row = cursor.fetchone()
        if not row or not row["punch_in_time"]:
            return {}

        from utils.timezone import convert_utc_to_ist
        from ml.s3 import S3_BUCKET, s3, PUNCH_PHOTO_PREFIX
        from urllib.parse import urlparse

        def fresh_url(stored_url):
            """Return a fresh 7-day presigned URL from a stored S3 URL."""
            if not stored_url:
                return None
            try:
                key = urlparse(stored_url).path.lstrip('/')
                # path may start with bucket-name/ when using path-style URLs
                if key.startswith(S3_BUCKET + '/'):
                    key = key[len(S3_BUCKET) + 1:]
                s3.head_object(Bucket=S3_BUCKET, Key=key)
                return s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': S3_BUCKET, 'Key': key},
                    ExpiresIn=604800
                )
            except Exception:
                return stored_url  # fallback: return as-is

        def fmt(dt):
            if not dt:
                return None
            return convert_utc_to_ist(dt).strftime("%d %b %Y, %I:%M %p")

        return {
            "punch_in_time":   fmt(row["punch_in_time"]),
            "punch_out_time":  fmt(row["punch_out_time"]),
            "punch_in_photo":  fresh_url(row["punch_in_photo"]),
            "punch_out_photo": fresh_url(row["punch_out_photo"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch attendance: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_org_analytics(org_id: str, days: int = 7):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM users
            WHERE parent_id = %s AND status = 1 AND role_id IN (2, 3)
        """, (org_id,))
        total_employees = cursor.fetchone()["cnt"]

        cursor.execute("""
            SELECT
                attendance_date,
                COUNT(DISTINCT user_id) AS present_count
            FROM attendance
            WHERE org_id = %s
              AND attendance_date >= %s
              AND punch_in IS NOT NULL
            GROUP BY attendance_date
            ORDER BY attendance_date ASC
        """, (org_id, ist_today() - timedelta(days=days)))
        daily_attendance = cursor.fetchall()

        cursor.execute("""
            SELECT
                attendance_date,
                COUNT(DISTINCT user_id) AS present_count
            FROM attendance
            WHERE org_id = %s
              AND attendance_date >= %s
              AND punch_in IS NOT NULL
            GROUP BY attendance_date
            ORDER BY attendance_date ASC
        """, (org_id, ist_today() - timedelta(days=30)))
        attendance_map = cursor.fetchall()

        return {
            "total_employees": total_employees,
            "daily_attendance": [
                {
                    "date": str(r["attendance_date"]),
                    "present": r["present_count"],
                    "absent": total_employees - r["present_count"]
                }
                for r in daily_attendance
            ],
            "attendance_map": [
                {
                    "date": str(r["attendance_date"]),
                    "present": r["present_count"]
                }
                for r in attendance_map
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch org analytics: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)


async def get_org_info(org_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT o.org_id, o.org_name, o.org_address, o.org_gst, o.state, o.district,
                   o.pincode, o.status, o.created_at,
                   u.name AS admin_name, u.mobile_number AS admin_mobile, u.email AS admin_email,
                   p.plan_name, ps.status AS sub_status, ps.start_date, ps.end_date, ps.staff_limit
            FROM org_info o
            LEFT JOIN users u ON u.user_id = o.parent_id
            LEFT JOIN punchapp_subscription ps ON ps.org_id = o.org_id AND ps.status = 'ACTIVE'
            LEFT JOIN gen_plans p ON p.plan_id = ps.plan_id
            WHERE o.org_id = %s
            LIMIT 1
        """, (org_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organization not found")
        result = dict(row)
        result['created_at'] = result['created_at'].isoformat() if result.get('created_at') else None
        result['start_date']  = str(result['start_date'])  if result.get('start_date')  else None
        result['end_date']    = str(result['end_date'])    if result.get('end_date')    else None
        result['pincode']     = str(result['pincode'])     if result.get('pincode')     else None
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_org_employees(org_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.user_id, u.name, u.emp_id, u.email, u.mobile_number,
                   u.status, u.role_id, u.created_at,
                   CASE u.role_id WHEN 2 THEN 'Manager' WHEN 3 THEN 'Employee' ELSE 'Other' END AS role_name,
                   d.dept_name, b.branch_name,
                   a.punch_in AS punch_in_today, a.punch_out AS punch_out_today
            FROM users u
            LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = %s
            LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %s
            LEFT JOIN attendance a ON a.user_id = u.user_id AND a.attendance_date = %s
            WHERE u.parent_id = %s AND u.role_id IN (2, 3)
            ORDER BY u.name ASC
        """, (org_id, org_id, ist_today(), org_id))
        rows = cursor.fetchall()
        from utils.timezone import convert_utc_to_ist
        result = []
        for r in rows:
            item = dict(r)
            item['created_at']     = item['created_at'].isoformat() if item.get('created_at') else None
            item['punch_in_today'] = convert_utc_to_ist(item['punch_in_today']).strftime("%I:%M %p") if item.get('punch_in_today') else None
            item['punch_out_today']= convert_utc_to_ist(item['punch_out_today']).strftime("%I:%M %p") if item.get('punch_out_today') else None
            result.append(item)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_user_attendance_range(user_id: str, from_date: str, to_date: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT attendance_date, punch_in, punch_out, work_hours, punchinface, punchoutface
            FROM attendance
            WHERE user_id = %s AND attendance_date BETWEEN %s AND %s
            ORDER BY attendance_date DESC
        """, (user_id, from_date, to_date))
        rows = cursor.fetchall()
        from utils.timezone import convert_utc_to_ist
        from ml.s3 import S3_BUCKET, s3
        from urllib.parse import urlparse
        def fresh_url(stored_url):
            if not stored_url: return None
            try:
                key = urlparse(stored_url).path.lstrip('/')
                if key.startswith(S3_BUCKET + '/'):
                    key = key[len(S3_BUCKET) + 1:]
                s3.head_object(Bucket=S3_BUCKET, Key=key)
                return s3.generate_presigned_url('get_object', Params={'Bucket': S3_BUCKET, 'Key': key}, ExpiresIn=604800)
            except Exception:
                return stored_url
        def fmt(dt):
            if not dt: return None
            return convert_utc_to_ist(dt).strftime("%d %b %Y, %I:%M %p")
        result = []
        for r in rows:
            result.append({
                "date": str(r["attendance_date"]),
                "punch_in_time":   fmt(r["punch_in"]),
                "punch_out_time":  fmt(r["punch_out"]),
                "work_hours":      float(r["work_hours"]) if r["work_hours"] else None,
                "punch_in_photo":  fresh_url(r["punchinface"]),
                "punch_out_photo": fresh_url(r["punchoutface"]),
            })
        return {"success": True, "data": result, "total_days": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_active_orgs_today():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT o.org_id, o.org_name,
                   COUNT(DISTINCT a.user_id) AS active_count,
                   MAX(a.punch_in) AS last_punch_time
            FROM org_info o
            INNER JOIN attendance a ON a.org_id = o.org_id
                AND a.attendance_date = %s
                AND a.punch_in IS NOT NULL
            WHERE o.status = 1
            GROUP BY o.org_id, o.org_name
            ORDER BY active_count DESC, o.org_name ASC
            LIMIT 50
        """, (ist_today(),))
        rows = cursor.fetchall()
        from utils.timezone import convert_utc_to_ist
        result = []
        for r in rows:
            result.append({
                "org_id": r["org_id"],
                "org_name": r["org_name"],
                "active_count": r["active_count"],
                "last_punch_time": convert_utc_to_ist(r["last_punch_time"]).strftime("%I:%M %p") if r.get("last_punch_time") else None,
            })
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_org_attendance_by_date(org_id: str, date: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.user_id, u.name, u.emp_id, u.role_id,
                   CASE u.role_id WHEN 2 THEN 'Manager' WHEN 3 THEN 'Employee' ELSE 'Other' END AS role_name,
                   d.dept_name, b.branch_name,
                   a.punch_in, a.punch_out, a.work_hours, a.punchinface, a.punchoutface
            FROM users u
            LEFT JOIN dept_info d ON d.dept_id = u.dept_id AND d.org_id = %s
            LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %s
            LEFT JOIN attendance a ON a.user_id = u.user_id AND a.attendance_date = %s
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
            ORDER BY a.punch_in ASC NULLS LAST, u.name ASC
        """, (org_id, org_id, date, org_id))
        rows = cursor.fetchall()
        from utils.timezone import convert_utc_to_ist
        from ml.s3 import S3_BUCKET, s3
        from urllib.parse import urlparse

        def fresh_url(stored_url):
            if not stored_url:
                return None
            try:
                key = urlparse(stored_url).path.lstrip('/')
                if key.startswith(S3_BUCKET + '/'):
                    key = key[len(S3_BUCKET) + 1:]
                s3.head_object(Bucket=S3_BUCKET, Key=key)
                return s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': S3_BUCKET, 'Key': key},
                    ExpiresIn=604800
                )
            except Exception:
                return stored_url

        def fmt(dt):
            if not dt:
                return None
            return convert_utc_to_ist(dt).strftime("%I:%M %p")

        result = []
        for r in rows:
            result.append({
                "user_id":        r["user_id"],
                "name":           r["name"],
                "emp_id":         r["emp_id"] or "—",
                "role_name":      r["role_name"],
                "dept_name":      r["dept_name"] or "—",
                "branch_name":    r["branch_name"] or "—",
                "punch_in_time":  fmt(r["punch_in"]),
                "punch_out_time": fmt(r["punch_out"]),
                "work_hours":     float(r["work_hours"]) if r["work_hours"] else None,
                "punch_in_photo": fresh_url(r["punchinface"]),
                "punch_out_photo":fresh_url(r["punchoutface"]),
                "is_present":     bool(r["punch_in"]),
            })
        return {"success": True, "data": result, "date": date}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_org_subscriptions_list():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT ps.org_id, o.org_name,
                   u.mobile_number AS admin_mobile, u.email AS admin_email,
                   p.plan_name, ps.staff_limit, ps.start_date, ps.end_date, ps.status,
                   gi.invoice_number, gi.total_amount, gi.base_amount, gi.cgst, gi.sgst,
                   gi.razorpay_payment_id, gi.payment_date, gi.valid_till, gi.coupon_code,
                   gi.invoice_user_id
            FROM punchapp_subscription ps
            LEFT JOIN org_info o ON o.org_id = ps.org_id
            LEFT JOIN users u ON u.user_id = o.parent_id
            LEFT JOIN gen_plans p ON p.plan_id = ps.plan_id
            LEFT JOIN LATERAL (
                SELECT invoice_number, total_amount, base_amount, cgst, sgst,
                       razorpay_payment_id, payment_date, valid_till, user_id AS invoice_user_id, coupon_code
                FROM gen_invoices gi2
                WHERE gi2.org_id = ps.org_id
                ORDER BY gi2.payment_date DESC
                LIMIT 1
            ) gi ON true
            WHERE ps.status = 'ACTIVE'
            ORDER BY ps.end_date ASC
        """)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item['start_date']    = str(item['start_date'])    if item.get('start_date')    else None
            item['end_date']      = str(item['end_date'])      if item.get('end_date')      else None
            item['valid_till']    = str(item['valid_till'])    if item.get('valid_till')    else None
            item['payment_date']  = item['payment_date'].isoformat() if item.get('payment_date') else None
            item['total_amount']  = float(item['total_amount'])  if item.get('total_amount')  else None
            item['base_amount']   = float(item['base_amount'])   if item.get('base_amount')   else None
            item['cgst']          = float(item['cgst'])          if item.get('cgst')          else None
            item['sgst']          = float(item['sgst'])          if item.get('sgst')          else None
            result.append(item)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)
