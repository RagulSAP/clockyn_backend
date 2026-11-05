from fastapi import APIRouter, Query, File, UploadFile, HTTPException
from core_functions.emp_expenses_functions import (
    OdometerStartRequest,
    OdometerStopRequest,
    submit_odometer_start,
    submit_odometer_stop,
    get_odometer_readings,
    get_admin_odometer_readings,
    get_admin_travel_bill_expenses,
    save_odometer_photo,
    get_odometer_photo_url,
    TravelBillExpenseRequest,
    submit_travel_bill_expense,
    get_travel_bill_expenses,
    save_travel_bill_photo,
    get_travel_bill_photo_url,
    update_odometer_status,
    save_allowance_rate,
    get_allowance_rate,
    update_travel_bill_status
)
from typing import Optional
from pydantic import BaseModel
from utils.uploads import read_upload, IMAGES_AND_PDF

router = APIRouter(prefix="/expenses", tags=["Employee Expenses"])


class StatusUpdateRequest(BaseModel):
    status: str
    org_id: str


class AllowanceRateRequest(BaseModel):
    org_id: str
    rate: float   # decimals allowed (3.5); validated and bounded in save_allowance_rate


@router.post("/odometer/start")
async def submit_start_reading(data: OdometerStartRequest):
    """Submit odometer start reading"""
    return await submit_odometer_start(data)


@router.post("/odometer/stop")
async def submit_stop_reading(data: OdometerStopRequest):
    """Submit odometer stop reading"""
    return await submit_odometer_stop(data)


@router.get("/odometer/readings")
async def get_readings(
    user_id: str = Query(..., description="User ID"),
    reading_date: Optional[str] = Query(None, description="Reading date (YYYY-MM-DD)")
):
    """Get odometer readings"""
    return await get_odometer_readings(user_id, reading_date)


@router.get("/admin/odometer-readings/{org_id}")
async def get_admin_readings(org_id: str):
    """Get all odometer readings for admin view"""
    return await get_admin_odometer_readings(org_id)


@router.put("/admin/odometer-readings/{readings_id}/status")
async def update_reading_status(readings_id: str, data: StatusUpdateRequest):
    """Update odometer reading status (approved/rejected)"""
    return await update_odometer_status(readings_id, data.status, data.org_id)


@router.post("/admin/allowance-rate")
async def save_rate(data: AllowanceRateRequest):
    """Save allowance rate for an org"""
    return await save_allowance_rate(data.org_id, data.rate)


@router.get("/admin/allowance-rate/{org_id}")
async def get_rate(org_id: str):
    """Get allowance rate for an org"""
    return await get_allowance_rate(org_id)


@router.post("/odometer/upload-photo")
async def upload_odometer_photo(
    user_id: str = Query(..., description="User ID"),
    reading_date: str = Query(..., description="Reading date"),
    photo_type: str = Query(..., description="Photo type (start/stop)"),
    file: UploadFile = File(...)
):
    """Upload odometer photo to S3"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    
    try:
        # Get org_id from user's parent_id
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        cursor.close()
        return_connection(conn)
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        org_id = user['parent_id']
        photo_bytes = await read_upload(file, allow_heic=True)       # odometer photo: an image
        url = save_odometer_photo(org_id, user_id, reading_date, photo_type, photo_bytes)
        return {"success": True, "url": url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/travel-bill/{org_id}")
async def get_admin_travel_bill(org_id: str, expense_type: Optional[str] = Query(None)):
    """Get all travel/bill expenses for admin view"""
    return await get_admin_travel_bill_expenses(org_id, expense_type)


@router.put("/admin/travel-bill/{expense_id}/status")
async def update_expense_status(expense_id: str, data: StatusUpdateRequest):
    """Update travel/bill expense status (approved/rejected)"""
    return await update_travel_bill_status(expense_id, data.status, data.org_id)


@router.post("/travel-bill/submit")
async def submit_expense(data: TravelBillExpenseRequest):
    """Submit travel or bill expense"""
    return await submit_travel_bill_expense(data)


@router.get("/travel-bill/expenses")
async def get_expenses(
    user_id: str = Query(..., description="User ID"),
    expense_type: Optional[str] = Query(None, description="Expense type (TRAVEL/BILL)"),
    expense_date: Optional[str] = Query(None, description="Expense date (YYYY-MM-DD)")
):
    """Get travel/bill expenses"""
    return await get_travel_bill_expenses(user_id, expense_type, expense_date)


@router.post("/travel-bill/upload-photo")
async def upload_travel_bill_photo(
    user_id: str = Query(..., description="User ID"),
    expense_id: str = Query(..., description="Expense ID"),
    file: UploadFile = File(...)
):
    """Upload travel/bill photo to S3"""
    from core_functions.db_function import get_db_connection, return_connection
    from psycopg2.extras import RealDictCursor
    
    try:
        # Get org_id from user's parent_id
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("SELECT parent_id FROM users WHERE user_id = %s", (user_id,))
            user = cursor.fetchone()
            expense = None
            if user:
                # the expense must be this user's own, in this user's organisation (the id also names the S3 key)
                cursor.execute(
                    "SELECT 1 FROM travel_bill_expenses WHERE expense_id = %s AND user_id = %s AND org_id = %s",
                    (expense_id, user_id, user['parent_id']))
                expense = cursor.fetchone()
        finally:
            cursor.close()
            return_connection(conn)

        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        if not expense:
            raise HTTPException(status_code=404, detail="Expense not found")

        org_id = user['parent_id']
        photo_bytes = await read_upload(file, allowed_types=IMAGES_AND_PDF, allow_heic=True)
        url = save_travel_bill_photo(org_id, user_id, expense_id, photo_bytes)
        return {"success": True, "url": url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
