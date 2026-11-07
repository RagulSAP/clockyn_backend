import logging
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from typing import Optional
from pydantic import BaseModel
from core_functions.operator_auth import require_operator
from utils import rate_limit
from utils.uploads import read_upload
from core_functions.support_functions import (
    get_support_subjects, submit_support_request, get_support_requests,
    update_support_request, get_all_support_requests, reply_to_ticket,
)
from core_functions.support_video_functions import get_active_support_videos

router = APIRouter(tags=["support"])


class UpdateSupportRequest(BaseModel):
    status: str
    reply: Optional[str] = None


class ReplyRequest(BaseModel):
    reply_text: str
    author_name: str = "Admin"
    is_admin: bool = True


@router.get("/support/subjects")
async def support_subjects_route():
    try:
        return await get_support_subjects()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/support/videos")
async def support_videos_route():
    """Video Hub: the active (status = 1) guide videos with presigned playback URLs."""
    try:
        return await get_active_support_videos()
    except Exception:
        logging.getLogger(__name__).exception("Could not load guide videos")
        raise HTTPException(status_code=500, detail="Could not load videos")


@router.post("/support/submit")
async def support_submit_route(
    request: Request,
    user_id: str = Form(...),
    org_id: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
    photo: Optional[UploadFile] = File(None),
):
    rate_limit.check_user("SUPPORT_CREATE", request, user_id)        # 10 tickets / hour / user (per instance)
    try:
        photo_key = None
        if photo:
            # Photo upload is best-effort — a storage/S3 hiccup should never
            # block the ticket itself from being submitted. A photo that is too large or not an image
            # (413 / 415) is refused up front.
            file_bytes = await read_upload(photo, allow_empty=True, allow_heic=True)
            try:
                from ml.s3 import upload_support_photo
                if file_bytes:
                    photo_key, _ = upload_support_photo(file_bytes, photo.filename, photo.content_type or "image/jpeg")
            except Exception as e:
                logging.getLogger(__name__).error(f"Support photo upload failed, submitting ticket without it: {e}")

        result = await submit_support_request(user_id, org_id, subject, message, photo_key)
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/support/update/{request_id}", dependencies=[Depends(require_operator)])
async def support_update_route(request_id: int, req: UpdateSupportRequest):
    try:
        result = await update_support_request(request_id, req.status, req.reply)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/support/{request_id}/reply")
async def support_reply_route(request_id: int, req: ReplyRequest):
    reply_text = req.reply_text.strip()
    if not reply_text:
        raise HTTPException(status_code=400, detail="reply_text is required")
    try:
        result = await reply_to_ticket(request_id, reply_text, req.author_name, req.is_admin)
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        if 'ticket_replies' in str(e):
            raise HTTPException(status_code=503, detail="Reply feature requires DB migration. Please run change_db.sql first.")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/support/requests/all", dependencies=[Depends(require_operator)])
async def support_all_requests_route():
    try:
        return await get_all_support_requests()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/support/requests/{org_id}")
async def support_requests_route(org_id: str):
    try:
        return await get_support_requests(org_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
