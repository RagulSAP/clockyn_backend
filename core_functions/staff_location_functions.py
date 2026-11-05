from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from datetime import datetime, date, timedelta
from utils.timezone import get_utc_now, IST
from core_functions import attendance_rules as rules
from pydantic import BaseModel
from typing import Optional, List
import uuid
import logging

logger = logging.getLogger(__name__)

class LocationLogRequest(BaseModel):
    user_id: str
    org_id: str
    latitude: float
    longitude: float
    timestamp: Optional[str] = None

class StaffLocationResponse(BaseModel):
    user_id: str
    name: str
    emp_id: str
    branch_name: str
    dept_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_updated_at: Optional[str] = None
    punch_in: Optional[str] = None
    punch_out: Optional[str] = None

async def log_staff_location(data: LocationLogRequest):
    """Log staff location - only for field staff (role_id 2 or 3) who are currently punched in"""
    # 0 is a valid coordinate: only a missing value (None) or an empty id is rejected
    if not data.user_id or not data.org_id or data.latitude is None or data.longitude is None:
        raise HTTPException(status_code=400, detail="User ID, Org ID, latitude, and longitude are required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Check if user is field staff with role_id 2 or 3
        cursor.execute(
            """SELECT u.user_id, u.is_field_staff, u.role_id 
               FROM users u 
               WHERE u.user_id = %s AND u.status = 1 AND u.role_id IN (2, 3)""",
            (data.user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=403, detail="Location tracking only available for staff members (role_id 2 or 3)")
        
        if not user['is_field_staff']:
            raise HTTPException(status_code=403, detail="Location tracking only available for field staff")
        
        # Only log location while a session is open. "Today" is the IST business date; a day can hold several
        # sessions (use the open one) and an overnight session that started on an earlier date and is still open
        # (younger than the 18 h stale limit) counts as well.
        now = get_utc_now()
        today = rules.ist_today(now)
        cursor.execute(
            """SELECT punch_in, punch_out FROM attendance
               WHERE user_id = %s
                 AND (attendance_date = %s
                      OR (punch_in IS NOT NULL AND punch_out IS NULL AND punch_in >= %s))
               ORDER BY punch_in DESC NULLS LAST""",
            (data.user_id, today, rules.stale_cutoff(now))
        )
        sessions = [r for r in (cursor.fetchall() or []) if r['punch_in']]
        if not sessions:
            raise HTTPException(status_code=403, detail="Location tracking only active after punch in")
        if not any(r['punch_out'] is None for r in sessions):
            raise HTTPException(status_code=403, detail="Location tracking stopped after punch out")
        
        # Insert location log
        cursor.execute(
            """INSERT INTO staff_location_logs (user_id, org_id, location, date_time) 
               VALUES (%s, %s, POINT(%s, %s), %s)""",
            (data.user_id, data.org_id, data.longitude, data.latitude, 
             datetime.fromisoformat(data.timestamp) if data.timestamp else get_utc_now())
        )
        
        conn.commit()
        
        return {
            "success": True,
            "message": "Location logged successfully",
            "is_field_staff": True
        }
        
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to log location: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to log location: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_locations(org_id: str, target_date: Optional[str] = None):
    """Get field staff locations for a specific date - only shows staff marked as field staff"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Use today's date (IST) if not provided
        if not target_date:
            target_date = rules.ist_today(get_utc_now()).strftime('%Y-%m-%d')
        
        # Get field staff with their latest location from staff_location_logs and attendance info
        query = """
            WITH latest_locations AS (
                SELECT DISTINCT ON (sll.user_id) 
                    sll.user_id,
                    sll.location[0] as longitude,
                    sll.location[1] as latitude,
                    sll.date_time as location_updated_at
                FROM staff_location_logs sll
                WHERE sll.org_id = %s
                    AND DATE((sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY sll.user_id, sll.date_time DESC
            )
            SELECT
                u.user_id,
                u.name,
                u.emp_id,
                COALESCE(b.branch_name, 'No Branch') as branch_name,
                COALESCE(d.dept_name, 'No Department') as dept_name,
                ll.latitude,
                ll.longitude,
                ll.location_updated_at,
                TO_CHAR(a.first_in AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as punch_in,
                TO_CHAR(a.last_out AT TIME ZONE 'Asia/Kolkata', 'HH12:MI AM') as punch_out
            FROM users u
            LEFT JOIN latest_locations ll ON u.user_id = ll.user_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = %s
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = %s
            LEFT JOIN (
                SELECT user_id,
                       MIN(punch_in) AS first_in,
                       (array_agg(punch_out ORDER BY punch_in DESC))[1] AS last_out
                FROM attendance
                WHERE attendance_date = %s
                GROUP BY user_id
            ) a ON u.user_id = a.user_id
            WHERE (u.parent_id = %s OR u.org_id = %s)
                AND u.status = 1
                AND u.role_id IN (2, 3)
                AND u.is_field_staff = 1
            ORDER BY u.name
        """
        
        cursor.execute(query, (org_id, target_date, org_id, org_id, target_date, org_id, org_id))
        staff_locations = cursor.fetchall()
        
        # Convert to list of dictionaries
        result = []
        for staff in staff_locations:
            result.append({
                "user_id": staff['user_id'],
                "name": staff['name'],
                "emp_id": staff['emp_id'],
                "branch_name": staff['branch_name'],
                "dept_name": staff['dept_name'],
                "latitude": float(staff['latitude']) if staff['latitude'] else None,
                "longitude": float(staff['longitude']) if staff['longitude'] else None,
                "location_updated_at": staff['location_updated_at'].isoformat() if staff['location_updated_at'] else None,
                "punch_in": staff['punch_in'],
                "punch_out": staff['punch_out']
            })
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to get staff locations: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get staff locations: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_user_location_history(user_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Get location history for a specific user"""
    if not user_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Default to last 7 days if dates not provided
        if not start_date:
            start_date = (rules.ist_today(get_utc_now()) - timedelta(days=7)).strftime('%Y-%m-%d')
        if not end_date:
            end_date = rules.ist_today(get_utc_now()).strftime('%Y-%m-%d')
        
        query = """
            SELECT 
                sll.location[0] as longitude,
                sll.location[1] as latitude,
                sll.date_time
            FROM staff_location_logs sll
            WHERE sll.user_id = %s
                AND DATE((sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') BETWEEN %s AND %s
            ORDER BY sll.date_time ASC
        """
        
        cursor.execute(query, (user_id, start_date, end_date))
        locations = cursor.fetchall()
        
        result = []
        for location in locations:
            result.append({
                "latitude": float(location['latitude']),
                "longitude": float(location['longitude']),
                "timestamp": location['date_time'].isoformat()
            })
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Failed to get location history: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get location history: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def delete_old_location_logs(days_to_keep: int = 30):
    """Delete location logs older than specified days (for maintenance)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cutoff_date = get_utc_now().date() - timedelta(days=days_to_keep)
        
        cursor.execute(
            "DELETE FROM staff_location_logs WHERE DATE(date_time) < %s",
            (cutoff_date,)
        )
        
        deleted_count = cursor.rowcount
        conn.commit()
        
        return {
            "success": True,
            "message": f"Deleted {deleted_count} old location logs",
            "deleted_count": deleted_count
        }
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to delete old location logs: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to delete old location logs: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)

async def get_staff_location_stats(org_id: str, target_date: Optional[str] = None):
    """Get location statistics for staff"""
    if not org_id:
        raise HTTPException(status_code=400, detail="Organization ID is required")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if not target_date:
            target_date = rules.ist_today(get_utc_now()).strftime('%Y-%m-%d')

        # Get stats for locations updated within 15 minutes and before 15 minutes
        query = """
            WITH latest_locations AS (
                SELECT DISTINCT ON (sll.user_id) 
                    sll.user_id,
                    sll.date_time as location_updated_at
                FROM staff_location_logs sll
                WHERE sll.org_id = %s
                    AND DATE((sll.date_time AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata') = %s
                ORDER BY sll.user_id, sll.date_time DESC
            ),
            staff_with_locations AS (
                SELECT 
                    u.user_id,
                    ll.location_updated_at,
                    CASE 
                        WHEN ll.location_updated_at IS NULL THEN 'no_location'
                        WHEN ll.location_updated_at >= NOW() - INTERVAL '15 minutes' THEN 'recent'
                        ELSE 'outdated'
                    END as location_status
                FROM users u
                LEFT JOIN latest_locations ll ON u.user_id = ll.user_id
                WHERE (u.parent_id = %s OR u.org_id = %s)
                    AND u.status = 1
                    AND u.role_id IN (2, 3)
                    AND u.is_field_staff = 1
            )
            SELECT 
                location_status,
                COUNT(*) as count
            FROM staff_with_locations
            GROUP BY location_status
        """
        
        cursor.execute(query, (org_id, target_date, org_id, org_id))
        stats = cursor.fetchall()
        
        result = {
            "recent": 0,
            "outdated": 0,
            "no_location": 0,
            "total": 0
        }
        
        for stat in stats:
            result[stat['location_status']] = stat['count']
            result['total'] += stat['count']
        
        return {
            "success": True,
            "data": result,
            "date": target_date
        }
        
    except Exception as e:
        logger.error(f"Failed to get location stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get location stats: {str(e)}")
    finally:
        cursor.close()
        return_connection(conn)