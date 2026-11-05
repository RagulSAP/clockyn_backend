from fastapi import APIRouter, File, UploadFile
from ml.s3 import save_employee_profile, get_employee_profile_url
from utils.uploads import read_upload

router = APIRouter(prefix="/profile-photo", tags=["profile-photo"])


@router.get("/{user_id}")
async def get_profile_photo(user_id: str):
    url = get_employee_profile_url(user_id)
    print(f"[profile-photo] GET {user_id} -> {url}")
    return {"success": True, "photo_url": url}


@router.post("/{user_id}")
async def upload_profile_photo(user_id: str, photo: UploadFile = File(...)):
    # POST is NOT public any more (auth_policy.PUBLIC_RULES lists only GET): with REQUIRE_AUTH=1 the caller must be
    # `user_id` themselves or an administrator / manager of that user's organisation (id ownership of the global guard).
    photo_bytes = await read_upload(photo, allow_heic=True)
    url = save_employee_profile(user_id, photo_bytes)
    print(f"[profile-photo] POST {user_id} -> saved, url={url[:80] if url else None}")
    return {"success": True, "photo_url": url}
