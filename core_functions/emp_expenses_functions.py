from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from decimal import Decimal
from utils.timezone import get_utc_now
from core_functions import payroll_engine, payroll_validation
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
from ml.s3 import s3, S3_BUCKET, AWS_REGION, put_image_variant
from utils.uploads import HEIC_EXT


class OdometerStartRequest(BaseModel):
    user_id: str
    reading_date: str
    start_km: float


class OdometerStopRequest(BaseModel):
    user_id: str
    reading_date: str
    stop_km: float


class TravelBillExpenseRequest(BaseModel):
    user_id: str
    expense_date: str
    expense_type: str  # TRAVEL or BILL
    amount: float


def save_odometer_photo(org_id: str, user_id: str, reading_date: str, photo_type: str, photo_bytes: bytes) -> str:
    """Save odometer photo to S3"""
    # HEIC bytes (older iPhone builds) are stored as received under .heic; everything else keeps the .png key
    return put_image_variant(f"odo_meter_photos/{org_id}/{user_id}/{reading_date}_{photo_type}", photo_bytes, "png", "image/png")


def _first_existing_url(key_stem: str) -> str:
    """Presigned URL of `<key_stem>.png`, else of `<key_stem>.heic`, else None."""
    for ext in ("png", HEIC_EXT):
        key = f"{key_stem}.{ext}"
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=key)
            return s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': key},
                ExpiresIn=604800
            )
        except Exception:
            continue
    return None


def get_odometer_photo_url(org_id: str, user_id: str, reading_date: str, photo_type: str) -> str:
    """Get presigned odometer photo URL from S3"""
    return _first_existing_url(f"odo_meter_photos/{org_id}/{user_id}/{reading_date}_{photo_type}")


async def submit_odometer_start(data: OdometerStartRequest):
    """Submit odometer start reading"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id from user's parent_id and validate
        cursor.execute(
            """SELECT u.parent_id FROM users u 
               WHERE u.user_id = %s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status = 1)""", 
            (data.user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User or organization not found")
        
        org_id = user['parent_id']
        
        # Check if reading already exists for this date
        cursor.execute(
            """
            SELECT id FROM odometer_readings 
            WHERE user_id = %s AND org_id = %s AND reading_date = %s
            """,
            (data.user_id, org_id, data.reading_date)
        )
        existing = cursor.fetchone()
        
        if existing:
            raise HTTPException(status_code=400, detail="Start reading already submitted for this date")
        
        # Generate readings_id
        import uuid
        readings_id = str(uuid.uuid4())
        
        # Insert start reading
        cursor.execute(
            """
            INSERT INTO odometer_readings 
            (user_id, org_id, readings_id, reading_date, start_km, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, readings_id
            """,
            (data.user_id, org_id, readings_id, data.reading_date, data.start_km, get_utc_now(), get_utc_now())
        )
        
        result = cursor.fetchone()
        conn.commit()
        
        return {"success": True, "message": "Start reading submitted successfully", "readings_id": result['readings_id']}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def submit_odometer_stop(data: OdometerStopRequest):
    """Submit odometer stop reading"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id from user's parent_id and validate
        cursor.execute(
            """SELECT u.parent_id FROM users u 
               WHERE u.user_id = %s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status = 1)""", 
            (data.user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User or organization not found")
        
        org_id = user['parent_id']
        
        # Check if start reading exists
        cursor.execute(
            """
            SELECT id, start_km FROM odometer_readings 
            WHERE user_id = %s AND org_id = %s AND reading_date = %s
            """,
            (data.user_id, org_id, data.reading_date)
        )
        reading = cursor.fetchone()
        
        if not reading:
            raise HTTPException(status_code=400, detail="Please submit start reading first")
        
        if not reading['start_km']:
            raise HTTPException(status_code=400, detail="Start reading not found")
        
        # Validate stop_km > start_km
        if data.stop_km <= float(reading['start_km']):
            raise HTTPException(status_code=400, detail="Stop reading must be greater than start reading")
        
        # Calculate distance
        distance = data.stop_km - float(reading['start_km'])
        
        # Update with stop reading
        cursor.execute(
            """
            UPDATE odometer_readings 
            SET stop_km = %s, distance_km = %s, updated_at = %s
            WHERE id = %s
            """,
            (data.stop_km, distance, get_utc_now(), reading['id'])
        )
        
        conn.commit()
        
        return {"success": True, "message": "Stop reading submitted successfully", "distance": distance}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_odometer_readings(user_id: str, reading_date: Optional[str] = None):
    """Get odometer readings for a user"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id from user's parent_id and validate
        cursor.execute(
            """SELECT u.parent_id FROM users u 
               WHERE u.user_id = %s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status = 1)""", 
            (user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User or organization not found")
        
        org_id = user['parent_id']
        
        if reading_date:
            cursor.execute(
                """
                SELECT * FROM odometer_readings 
                WHERE user_id = %s AND org_id = %s AND reading_date = %s
                ORDER BY created_at DESC
                """,
                (user_id, org_id, reading_date)
            )
        else:
            cursor.execute(
                """
                SELECT * FROM odometer_readings 
                WHERE user_id = %s AND org_id = %s
                ORDER BY reading_date DESC, created_at DESC
                LIMIT 30
                """,
                (user_id, org_id)
            )
        
        readings = cursor.fetchall()
        
        # Add presigned photo URLs
        for r in readings:
            rd = str(r.get('reading_date', ''))
            r['start_photo_url'] = get_odometer_photo_url(org_id, user_id, rd, 'start')
            r['stop_photo_url'] = get_odometer_photo_url(org_id, user_id, rd, 'stop')
        
        return {"success": True, "data": readings}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


def save_travel_bill_photo(org_id: str, user_id: str, expense_id: str, photo_bytes: bytes) -> str:
    """Save travel/bill photo to S3"""
    return put_image_variant(f"travel_bill_photos/{org_id}/{user_id}/{expense_id}", photo_bytes, "png", "image/png")


def get_travel_bill_photo_url(org_id: str, user_id: str, expense_id: str) -> str:
    """Get presigned travel/bill photo URL from S3"""
    return _first_existing_url(f"travel_bill_photos/{org_id}/{user_id}/{expense_id}")


async def submit_travel_bill_expense(data: TravelBillExpenseRequest):
    """Submit travel or bill expense"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Validate expense_type
        if data.expense_type.upper() not in ['TRAVEL', 'BILL']:
            raise HTTPException(status_code=400, detail="Expense type must be TRAVEL or BILL")
        
        # Get org_id from user's parent_id and validate
        cursor.execute(
            """SELECT u.parent_id FROM users u 
               WHERE u.user_id = %s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status = 1)""", 
            (data.user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User or organization not found")
        
        org_id = user['parent_id']
        
        # Generate expense_id
        import uuid
        expense_id = str(uuid.uuid4())
        
        # Insert expense
        cursor.execute(
            """
            INSERT INTO travel_bill_expenses 
            (user_id, org_id, expense_id, expense_date, expense_type, amount, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, expense_id
            """,
            (data.user_id, org_id, expense_id, data.expense_date, data.expense_type.upper(), data.amount, get_utc_now(), get_utc_now())
        )
        
        result = cursor.fetchone()
        conn.commit()
        
        return {"success": True, "message": "Expense submitted successfully", "expense_id": result['expense_id']}
        
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_travel_bill_expenses(user_id: str, expense_type: Optional[str] = None, expense_date: Optional[str] = None):
    """Get travel/bill expenses for a user"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Get org_id from user's parent_id and validate
        cursor.execute(
            """SELECT u.parent_id FROM users u 
               WHERE u.user_id = %s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status = 1)""", 
            (user_id,)
        )
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User or organization not found")
        
        org_id = user['parent_id']
        
        query = "SELECT * FROM travel_bill_expenses WHERE user_id = %s AND org_id = %s"
        params = [user_id, org_id]
        
        if expense_type:
            query += " AND expense_type = %s"
            params.append(expense_type.upper())
        
        if expense_date:
            query += " AND expense_date = %s"
            params.append(expense_date)
        
        query += " ORDER BY expense_date DESC, created_at DESC LIMIT 30"
        
        cursor.execute(query, params)
        expenses = cursor.fetchall()
        
        # Add presigned photo URLs
        for exp in expenses:
            exp['photo_url'] = get_travel_bill_photo_url(org_id, user_id, exp.get('expense_id', ''))
        
        return {"success": True, "data": expenses}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_admin_odometer_readings(org_id: str):
    """Get all odometer readings for admin view"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        cursor.execute(
            """
            SELECT 
                o.id,
                o.user_id,
                o.readings_id,
                o.reading_date,
                o.start_km,
                o.stop_km,
                o.distance_km,
                o.status,
                o.allowance_rate,
                o.created_at,
                o.updated_at,
                u.name as user_name
            FROM odometer_readings o
            LEFT JOIN users u ON o.user_id = u.user_id
            WHERE o.org_id = %s
            ORDER BY o.reading_date DESC, o.created_at DESC
            """,
            (org_id,)
        )
        
        readings = cursor.fetchall()
        
        # Calculate distance if not stored and add photo URLs
        for reading in readings:
            if reading['start_km'] and reading['stop_km'] and not reading['distance_km']:
                reading['distance_km'] = float(reading['stop_km']) - float(reading['start_km'])
            try:
                rd = str(reading.get('reading_date', ''))
                reading['start_photo_url'] = get_odometer_photo_url(org_id, reading['user_id'], rd, 'start')
                reading['stop_photo_url'] = get_odometer_photo_url(org_id, reading['user_id'], rd, 'stop')
            except Exception:
                reading['start_photo_url'] = None
                reading['stop_photo_url'] = None
        
        return {"success": True, "data": readings}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def update_odometer_status(readings_id: str, status: str, org_id: str):
    """Update odometer reading status (approved/rejected).

    Approving stamps the organisation's allowance rate onto the reading ONCE (on the transition to approved); a
    re-approval and later rate changes never rewrite the stamp (payroll pays distance x the stamped rate)."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if status not in ('approved', 'rejected'):
            raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")

        stamp = status == 'approved'
        rate_to_set = None
        if stamp:
            cursor.execute("SELECT rate FROM org_allowance_rate WHERE org_id = %s", (org_id,))
            rate_row = cursor.fetchone()
            rate_to_set = rate_row['rate'] if rate_row else payroll_engine.default_odometer_rate()

        cursor.execute(
            """
            UPDATE odometer_readings
            SET allowance_rate = CASE WHEN %s AND COALESCE(status, '') <> 'approved' THEN %s ELSE allowance_rate END,
                status = %s,
                updated_at = %s
            WHERE readings_id = %s AND org_id = %s
            RETURNING id
            """,
            (stamp, rate_to_set, status, get_utc_now(), readings_id, org_id)
        )

        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Reading not found")

        conn.commit()
        return {"success": True, "message": f"Reading {status} successfully"}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


MAX_ALLOWANCE_RATE = Decimal("9999.99")


async def save_allowance_rate(org_id: str, rate):
    """Save the organisation's allowance rate (rupees per km, decimals allowed, 0 < rate <= 9999.99).

    The rate lives in its own table (`org_allowance_rate`); only PENDING readings are stamped with it, approved and
    rejected readings keep the rate they were stamped with."""
    amount = payroll_validation.parse_amount(rate, "Allowance rate")
    if amount > MAX_ALLOWANCE_RATE:
        raise HTTPException(status_code=400, detail="Allowance rate must not exceed 9999.99")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute("SELECT 1 FROM org_info WHERE org_id = %s", (org_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Organization not found")

        cursor.execute(
            """
            INSERT INTO org_allowance_rate (org_id, rate, updated_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (org_id) DO UPDATE SET rate = EXCLUDED.rate, updated_at = EXCLUDED.updated_at
            """,
            (org_id, amount, get_utc_now())
        )
        cursor.execute(
            "UPDATE odometer_readings SET allowance_rate = %s WHERE org_id = %s AND status = 'pending'",
            (amount, org_id)
        )

        conn.commit()
        return {"success": True, "message": "Allowance rate saved successfully", "rate": float(amount)}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_allowance_rate(org_id: str):
    """The organisation's allowance rate; the default (env DEFAULT_ODOMETER_RATE, 3) with `is_default: true` when unset."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cursor.execute("SELECT rate FROM org_allowance_rate WHERE org_id = %s", (org_id,))
        result = cursor.fetchone()
        if result:
            return {"success": True, "rate": float(result['rate']), "is_default": False}
        return {"success": True, "rate": float(payroll_engine.default_odometer_rate()), "is_default": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def get_admin_travel_bill_expenses(org_id: str, expense_type: Optional[str] = None):
    """Get all travel/bill expenses for admin view"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        query = """
            SELECT 
                t.id, t.user_id, t.expense_id, t.expense_date,
                t.expense_type, t.amount, t.status, t.created_at,
                u.name as user_name
            FROM travel_bill_expenses t
            LEFT JOIN users u ON t.user_id = u.user_id
            WHERE t.org_id = %s
        """
        params = [org_id]
        
        if expense_type:
            query += " AND t.expense_type = %s"
            params.append(expense_type.upper())
        
        query += " ORDER BY t.expense_date DESC, t.created_at DESC"
        
        cursor.execute(query, params)
        expenses = cursor.fetchall()
        
        # Add presigned photo URLs
        for exp in expenses:
            try:
                exp['photo_url'] = get_travel_bill_photo_url(org_id, exp['user_id'], exp.get('expense_id', ''))
            except Exception:
                exp['photo_url'] = None
        
        return {"success": True, "data": expenses}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)


async def update_travel_bill_status(expense_id: str, status: str, org_id: str):
    """Update travel/bill expense status (approved/rejected)"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        if status not in ('approved', 'rejected'):
            raise HTTPException(status_code=400, detail="Status must be 'approved' or 'rejected'")
        
        cursor.execute(
            """
            UPDATE travel_bill_expenses 
            SET status = %s, updated_at = %s
            WHERE expense_id = %s AND org_id = %s
            RETURNING id
            """,
            (status, get_utc_now(), expense_id, org_id)
        )
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Expense not found")

        conn.commit()
        return {"success": True, "message": f"Expense {status} successfully"}

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        return_connection(conn)
