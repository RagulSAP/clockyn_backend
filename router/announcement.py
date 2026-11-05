from fastapi import APIRouter, HTTPException
from core_functions.announcement_functions import (
    send_announcement, get_announcements, delete_announcement,
    get_announcements_for_employee, SendAnnouncementRequest,
)

router = APIRouter(tags=["announcement"])


@router.post("/api/announcements")
async def send_announcement_route(req: SendAnnouncementRequest):
    try:
        return await send_announcement(req)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/announcements/{org_id}")
async def get_announcements_route(org_id: str):
    try:
        return await get_announcements(org_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/announcements/{announcement_id}")
async def delete_announcement_route(announcement_id: str):
    try:
        return await delete_announcement(announcement_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/announcements/employee/{user_id}")
async def get_employee_announcements_route(user_id: str):
    try:
        return await get_announcements_for_employee(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
