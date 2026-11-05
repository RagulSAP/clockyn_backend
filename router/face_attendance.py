from fastapi import APIRouter, UploadFile, File, Form
from typing import List
from core_functions.gen_plans_functions import check_plan_feature
from core_functions.face_attendance_functions import (
    check_face_registered, check_punch_location, register_face, punch_with_face, get_today_attendance,
    warmup_user, get_attendance_sessions
)
from core_functions.attendance_functions import edit_attendance, EditAttendanceRequest
from utils.uploads import check_upload, IMAGES

router = APIRouter()


@router.get("/face/check/{user_id}")
async def check_face_registered_endpoint(user_id: str):
    """Check if user has registered face"""
    return await check_face_registered(user_id)


@router.get("/face/warmup/{user_id}")
async def face_warmup_endpoint(user_id: str):
    """Pre-load the user's embeddings into the in-process cache (call when the punch screen opens)"""
    return await warmup_user(user_id)


@router.post("/face/check-location")
async def check_punch_location_endpoint(
    user_id: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...)
):
    """Check if user is within branch radius before face capture"""
    return await check_punch_location(user_id, latitude, longitude)


@router.post("/face/register")
async def register_face_endpoint(
    user_id: str = Form(...),
    org_id: str = Form(...),
    faces: List[UploadFile] = File(...)
):
    await check_plan_feature(org_id, 'camera_punch', is_admin=False)
    for face_file in faces:                       # size (413) and image type (415) of every photo; the file is rewound
        await check_upload(face_file, allowed_types=IMAGES)
    return await register_face(user_id, org_id, faces)


@router.post("/face/punch")
async def punch_endpoint(
    user_id: str = Form(...),
    org_id: str = Form(...),
    action: str = Form(...),
    face: UploadFile = File(...),
    latitude: float = Form(None),
    longitude: float = Form(None),
    force_accept: bool = Form(False)
):
    await check_plan_feature(org_id, 'camera_punch', is_admin=False)
    await check_upload(face, allowed_types=IMAGES)
    return await punch_with_face(user_id, org_id, face, action, latitude, longitude, force_accept)


@router.get("/attendance/today/{user_id}")
async def get_today_attendance_endpoint(user_id: str):
    """Get today's attendance for user"""
    return await get_today_attendance(user_id)


@router.get("/attendance/sessions/{user_id}")
async def get_attendance_sessions_endpoint(user_id: str, date: str):
    """Get all punch sessions for a user on a specific date (IST business date)"""
    return await get_attendance_sessions(user_id, date)
