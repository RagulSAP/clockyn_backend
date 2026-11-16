import logging
from fastapi import APIRouter, HTTPException, Request
from utils import rate_limit
from core_functions.support_video_functions import VideoViewRequest, get_active_support_videos, record_video_view
from core_functions.support_faq_functions import list_active_faqs

# Support area of the app: the Video Hub of guide videos and the FAQ. (The old ticket screen and its /support/submit,
# /support/requests, /support/update and /support/{id}/reply routes were removed on 2026-11-16: Live Chat replaces them.)
router = APIRouter(tags=["support"])


@router.get("/support/videos")
async def support_videos_route():
    """Video Hub: the active (status = 1) guide videos with presigned playback URLs."""
    try:
        return await get_active_support_videos()
    except Exception:
        logging.getLogger(__name__).exception("Could not load guide videos")
        raise HTTPException(status_code=500, detail="Could not load videos")


@router.get("/support/faqs")
async def support_faqs_route():
    """FAQ Hub: the active (status = 1) FAQs in display order (the app's Support > FAQ screen)."""
    return await list_active_faqs()


@router.post("/support/videos/{video_id}/view")
async def support_video_view_route(video_id: int, data: VideoViewRequest, request: Request):
    """The app opened a guide video: lets the operators see which organisations use which topics."""
    rate_limit.check_user("VIDEO_VIEW", request, data.user_id)
    return await record_video_view(video_id, data.user_id, data.org_id)
