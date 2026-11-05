from fastapi import APIRouter, HTTPException, Query
from core_functions.branch_functions import (
    add_branch, get_branch_by_id, update_branch, delete_branch, get_branch_staff, reassign_staff,
    AddBranchRequest, UpdateBranchRequest, DeleteBranchRequest, BranchStaffResponse, ReassignStaffRequest
)
from typing import List

router = APIRouter(prefix="/branch", tags=["Branch"])

@router.post("/add")
async def add_branch_endpoint(branch_data: AddBranchRequest):
    """Add a new branch"""
    try:
        result = await add_branch(branch_data)
        return result
    except Exception as e:
        raise

@router.put("/update/{branch_id}")
async def update_branch_endpoint(branch_id: str, branch_data: UpdateBranchRequest):
    """Update an existing branch"""
    return await update_branch(branch_id, branch_data)

@router.post("/delete/{branch_id}")
async def delete_branch_endpoint(
    branch_id: int,
    org_id: str = Query(..., description="Organization ID")
):
    """Delete a branch"""
    print(f"DELETE endpoint called with branch_id={branch_id}, org_id={org_id}")
    return await delete_branch(str(branch_id), org_id)

@router.get("/staff/{branch_id}", response_model=List[BranchStaffResponse])
async def get_branch_staff_endpoint(
    branch_id: str,
    org_id: str = Query(..., description="Organization ID")
):
    """Get all staff assigned to a specific branch"""
    return await get_branch_staff(branch_id, org_id)

@router.get("/{branch_id}")
async def get_branch_endpoint(branch_id: str, org_id: str = Query(None, description="Organization ID (scopes the lookup)")):
    """Get branch by ID"""
    return await get_branch_by_id(branch_id, org_id)

@router.post("/reassign-staff")
async def reassign_staff_endpoint(reassign_data: ReassignStaffRequest):
    """Reassign staff to a different branch"""
    return await reassign_staff(reassign_data)