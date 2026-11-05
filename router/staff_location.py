from fastapi import APIRouter, Query, HTTPException
from core_functions.gen_plans_functions import check_plan_feature
from core_functions.staff_location_functions import (
    log_staff_location, get_staff_locations, get_user_location_history,
    delete_old_location_logs, get_staff_location_stats,
    LocationLogRequest, StaffLocationResponse
)
from typing import Optional, List

router = APIRouter(prefix="/staff-location", tags=["staff-location"])

@router.post("/log")
async def log_location_route(data: LocationLogRequest):
    return await log_staff_location(data)

@router.get("/locations", response_model=List[StaffLocationResponse])
async def get_staff_locations_route(
    org_id: str = Query(..., description="Organization ID"),
    target_date: Optional[str] = Query(None, description="Target date (YYYY-MM-DD)")
):
    await check_plan_feature(org_id, 'staff_location', is_admin=True)
    return await get_staff_locations(org_id, target_date)

@router.get("/history/{user_id}")
async def get_location_history_route(
    user_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)")
):
    return await get_user_location_history(user_id, start_date, end_date)

@router.get("/stats")
async def get_location_stats_route(
    org_id: str = Query(..., description="Organization ID"),
    target_date: Optional[str] = Query(None, description="Target date (YYYY-MM-DD)")
):
    await check_plan_feature(org_id, 'staff_location', is_admin=True)
    return await get_staff_location_stats(org_id, target_date)

@router.delete("/cleanup")
async def cleanup_old_logs_route(
    days_to_keep: int = Query(30, description="Number of days to keep logs")
):
    return await delete_old_location_logs(days_to_keep)
