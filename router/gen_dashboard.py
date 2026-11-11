from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from core_functions.operator_auth import require_operator, require_operator_super
from core_functions.support_video_functions import (
    VideoCreateRequest,
    VideoUpdateRequest,
    VideoUploadUrlRequest,
    create_support_video,
    create_video_upload_url,
    delete_support_video,
    list_all_support_videos,
    list_support_video_views,
    update_support_video,
)
from core_functions.support_faq_functions import (
    FaqCreateRequest,
    FaqUpdateRequest,
    create_faq,
    delete_faq,
    list_all_faqs,
    update_faq,
)
from core_functions.gen_dashboard_functions import (
    get_super_admin_stats,
    get_all_users,
    get_all_organizations,
    get_recent_orgs,
    get_recent_payments,
    get_plan_breakdown,
    get_monthly_revenue,
    get_org_analytics,
    get_admin_profile,
    update_admin_profile,
    update_org_details,
    update_org_status,
    get_user_latest_attendance,
    get_org_info,
    get_org_employees,
    get_user_attendance_range,
    get_active_orgs_today,
    get_org_subscriptions_list,
    get_org_attendance_by_date,
    OrgUpdateRequest,
    OrgStatusRequest,
)
from core_functions.analytics_functions import clickstream_events, clickstream_summary, purge_clickstream
from core_functions.referral_functions import operator_referrals, operator_void_reward

# Platform-operator routes: every route of this router goes through the ONE operator guard (core_functions/operator_auth.py).
router = APIRouter(prefix="/gen-dashboard", tags=["gen-dashboard"], dependencies=[Depends(require_operator)])


@router.get("/me")
async def admin_profile(email: str = Query(...)):
    return await get_admin_profile(email)


class UpdateProfileRequest(BaseModel):
    email: str
    name: str


@router.put("/me")
async def update_profile(data: UpdateProfileRequest):
    return await update_admin_profile(data.email, data.name)


@router.get("/stats")
async def dashboard_stats():
    return await get_super_admin_stats()


@router.get("/users/{user_id}/latest-attendance")
async def user_latest_attendance(user_id: str):
    return await get_user_latest_attendance(user_id)


@router.get("/users")
async def all_users():
    return await get_all_users()


@router.get("/organizations")
async def all_organizations():
    return await get_all_organizations()


@router.put("/organizations/{org_id}")
async def edit_organization(org_id: str, data: OrgUpdateRequest):
    return await update_org_details(org_id, data)


@router.get("/recent-orgs")
async def recent_orgs():
    return await get_recent_orgs()


@router.get("/recent-payments")
async def recent_payments():
    return await get_recent_payments()


@router.get("/plan-breakdown")
async def plan_breakdown():
    return await get_plan_breakdown()


@router.get("/monthly-revenue")
async def monthly_revenue():
    return await get_monthly_revenue()


@router.get("/org-analytics/{org_id}")
async def org_analytics(org_id: str, days: int = Query(default=7)):
    return await get_org_analytics(org_id, days)


@router.post("/organizations/{org_id}/activate")
async def activate_org(org_id: str, data: OrgStatusRequest):
    return await update_org_status(org_id, 1, data.changed_by)


@router.post("/organizations/{org_id}/deactivate")
async def deactivate_org(org_id: str, data: OrgStatusRequest):
    return await update_org_status(org_id, 0, data.changed_by)


@router.delete("/organizations/{org_id}")
async def delete_org(org_id: str, data: OrgStatusRequest):
    return await update_org_status(org_id, 2, data.changed_by)


@router.get("/org/{org_id}/info")
async def get_org_info_route(org_id: str):
    return await get_org_info(org_id)


@router.get("/org/{org_id}/employees")
async def get_org_employees_route(org_id: str):
    return await get_org_employees(org_id)


@router.get("/users/{user_id}/attendance-range")
async def get_user_attendance_range_route(user_id: str, from_date: str = Query(...), to_date: str = Query(...)):
    return await get_user_attendance_range(user_id, from_date, to_date)


@router.get("/active-orgs")
async def get_active_orgs_today_route():
    return await get_active_orgs_today()


@router.get("/org-subscriptions")
async def get_org_subscriptions_list_route():
    return await get_org_subscriptions_list()


# ── Refer & Earn: read = any operator, voiding a reward = super admin (role_id 1) ──────────────────────────────────────
@router.get("/referrals")
async def referrals_route(status: str = Query("all"), q: str = Query("", max_length=100)):
    return await operator_referrals(status, q)


@router.post("/referrals/rewards/{reward_id}/void", dependencies=[Depends(require_operator_super)])
async def referral_reward_void_route(reward_id: int):
    return await operator_void_reward(reward_id)


@router.get("/org/{org_id}/attendance/{date}")
async def get_org_attendance_by_date_route(org_id: str, date: str):
    return await get_org_attendance_by_date(org_id, date)


# ── Video Hub (guide videos of the app's Support tab): read = any operator, write = super admin (role_id 1) ──────────
@router.get("/support-videos")
async def support_videos_list_route():
    return await list_all_support_videos()


@router.post("/support-videos/upload-url", dependencies=[Depends(require_operator_super)])
async def support_video_upload_url_route(data: VideoUploadUrlRequest):
    return await create_video_upload_url(data)


@router.post("/support-videos", dependencies=[Depends(require_operator_super)])
async def support_video_create_route(data: VideoCreateRequest):
    return await create_support_video(data)


@router.put("/support-videos/{video_id}", dependencies=[Depends(require_operator_super)])
async def support_video_update_route(video_id: int, data: VideoUpdateRequest):
    return await update_support_video(video_id, data)


@router.delete("/support-videos/{video_id}", dependencies=[Depends(require_operator_super)])
async def support_video_delete_route(video_id: int):
    return await delete_support_video(video_id)


@router.get("/support-video-views")
async def support_video_views_route(video_id: Optional[int] = Query(None), limit: int = Query(1000)):
    return await list_support_video_views(video_id, limit)


# ── Clickstream (screen views and button taps of the app + product insights): read = any operator, purge = super admin ──
@router.get("/clickstream/summary")
async def clickstream_summary_route(days: int = Query(30, ge=1, le=180), platform: Optional[str] = Query(None), role: Optional[int] = Query(None)):
    return await clickstream_summary(days, platform, role)


@router.get("/clickstream/events")
async def clickstream_events_route(
    limit: int = Query(200, ge=1, le=1000),
    org_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    return await clickstream_events(limit, org_id, user_id, event_type, q)


@router.post("/clickstream/purge", dependencies=[Depends(require_operator_super)])
async def clickstream_purge_route(days: int = Query(180, ge=30)):
    return await purge_clickstream(days)


# ── FAQ Hub (FAQs of the app's Support tab): read = any operator, write = super admin (role_id 1) ───────────────────
@router.get("/faqs")
async def support_faqs_list_route():
    return await list_all_faqs()


@router.post("/faqs", dependencies=[Depends(require_operator_super)])
async def support_faq_create_route(data: FaqCreateRequest):
    return await create_faq(data)


@router.put("/faqs/{faq_id}", dependencies=[Depends(require_operator_super)])
async def support_faq_update_route(faq_id: int, data: FaqUpdateRequest):
    return await update_faq(faq_id, data)


@router.delete("/faqs/{faq_id}", dependencies=[Depends(require_operator_super)])
async def support_faq_delete_route(faq_id: int):
    return await delete_faq(faq_id)
