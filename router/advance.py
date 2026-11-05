from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import date
from core_functions.advance_functions import (
    create_advance,
    get_advances_by_org,
    get_advances_by_user,
    delete_advance,
)

router = APIRouter(prefix="/api/advance", tags=["Advance"])


class AdvanceCreate(BaseModel):
    org_id: str
    user_id: str
    created_by: str
    advance_amt: float
    date: date


@router.post("")
async def add_advance(data: AdvanceCreate):
    result = create_advance(
        org_id=data.org_id,
        user_id=data.user_id,
        created_by=data.created_by,
        advance_amt=data.advance_amt,
        advance_date=data.date,
    )
    return {"success": True, "message": "Advance recorded successfully", "data": result}


@router.get("/org/{org_id}")
async def list_advances_by_org(org_id: str):
    data = get_advances_by_org(org_id)
    return {"success": True, "data": data}


@router.get("/user/{user_id}/{org_id}")
async def list_advances_by_user(user_id: str, org_id: str):
    data = get_advances_by_user(user_id, org_id)
    return {"success": True, "data": data}


@router.delete("/{advance_id}/{org_id}")
async def remove_advance(advance_id: int, org_id: str):
    delete_advance(advance_id, org_id)
    return {"success": True, "message": "Advance deleted successfully"}
