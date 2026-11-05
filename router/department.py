from fastapi import APIRouter
from core_functions.dept_functions import (
    get_departments, add_department, update_department,
    delete_department, DeptRequest
)

router = APIRouter(prefix="/department", tags=["Department"])


@router.get("/list/{org_id}")
async def list_departments(org_id: str):
    return await get_departments(org_id)


@router.post("/add")
async def create_department(data: DeptRequest):
    return await add_department(data)


@router.put("/update/{dept_id}")
async def edit_department(dept_id: str, data: DeptRequest):
    return await update_department(dept_id, data)


@router.post("/delete/{dept_id}")
async def remove_department(dept_id: str, org_id: str):
    return await delete_department(dept_id, org_id)
