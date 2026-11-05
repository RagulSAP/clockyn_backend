from typing import List, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel
from core_functions.shift_functions import (
    create_shift, get_shift, get_shifts, update_shift, delete_shift, ShiftCreate,
    get_assigned_staff, get_unassigned_staff, assign_staff, unassign_staff
)

router = APIRouter()


@router.post("/shift/create")
async def create_shift_endpoint(shift_data: ShiftCreate):
    """Create a new shift"""
    return await create_shift(shift_data)


@router.get("/shift/get_shift/{shift_id}")
async def get_shift_endpoint(shift_id: int):
    """Get a single shift by ID"""
    return await get_shift(shift_id)


@router.get("/shift/get_shifts/{org_id}")
async def get_shifts_endpoint(org_id: str):
    """Get all shifts for an organization"""
    return await get_shifts(org_id)


@router.put("/shift/update/{shift_id}")
async def update_shift_endpoint(shift_id: int, shift_data: ShiftCreate):
    """Update an existing shift"""
    return await update_shift(shift_id, shift_data)


@router.delete("/shift/delete/{shift_id}")
async def delete_shift_endpoint(shift_id: int, org_id: str = Query(...)):
    """Delete a shift"""
    return await delete_shift(shift_id, org_id)


class AssignShiftStaff(BaseModel):
    org_id: Optional[str] = None
    user_ids: Optional[List[str]] = None
    shift_id: Optional[int] = None


@router.get("/shift/assigned_staff/{org_id}/{shift_id}")
def assigned_staff_endpoint(org_id: str, shift_id: int):
    """Staff currently on a shift"""
    return get_assigned_staff(org_id, shift_id)


@router.get("/shift/unassigned_staff/{org_id}/{shift_id}")
def unassigned_staff_endpoint(org_id: str, shift_id: int):
    """Active staff not on this shift"""
    return get_unassigned_staff(org_id, shift_id)


@router.post("/shift/assign_staff")
def assign_staff_endpoint(data: AssignShiftStaff):
    """Assign staff to a shift"""
    return assign_staff(data.org_id, data.shift_id, data.user_ids)


@router.delete("/shift/unassign_staff/{org_id}/{user_id}/{shift_id}")
def unassign_staff_endpoint(org_id: str, user_id: str, shift_id: int):
    """Remove one staff member from a shift"""
    return unassign_staff(org_id, user_id, shift_id)
