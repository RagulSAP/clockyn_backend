from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Query, Request
from pydantic import BaseModel
from core_functions.operator_auth import require_operator
from core_functions.owner_guard import require_owner
from utils import rate_limit
from utils.uploads import read_upload
from core_functions.company_functions import add_company, get_company_details, get_user_companies, delete_company, update_company, delete_user_account, request_account_deletion, get_deletion_requests, AddCompanyRequest
from core_functions.suggest_feature_functions import submit_suggest_feature, get_suggestions, reply_to_suggestion, get_my_suggestions, get_suggestions_with_reply_count
from ml.s3 import save_org_logo

router = APIRouter(prefix="/company", tags=["company"])

@router.post("/add")
async def add_company_route(data: AddCompanyRequest):
    return await add_company(data)

@router.put("/update/{org_id}")
async def update_company_route(org_id: str, data: AddCompanyRequest):
    return await update_company(org_id, data)

@router.post("/update-logo/{org_id}")
async def update_company_logo_route(org_id: str, logo: UploadFile = File(...)):
    try:
        logo_bytes = await read_upload(logo, allow_heic=True)
        logo_url = save_org_logo(org_id, logo_bytes)
        return {"success": True, "logo_url": logo_url, "message": "Logo updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/details/{org_id}")
async def get_company_details_route(org_id: str):
    return await get_company_details(org_id)

@router.get("/list/{user_id}")
async def get_user_companies_route(user_id: str):
    return await get_user_companies(user_id)

@router.delete("/delete/{org_id}/{user_id}")
async def delete_company_route(org_id: str, user_id: str):
    return await delete_company(org_id, user_id)

@router.delete("/delete-account/{user_id}", dependencies=[Depends(require_owner)])
async def delete_user_account_route(user_id: str):
    # owner only (auth_policy.OWNER_ONLY_RULES): with a token, the id must be the caller's own account
    return await delete_user_account(user_id)


@router.post("/request-deletion/{user_id}", dependencies=[Depends(require_owner)])
async def request_deletion_route(user_id: str):
    return await request_account_deletion(user_id)


@router.get("/deletion-requests", dependencies=[Depends(require_operator)])
async def deletion_requests_route():
    return await get_deletion_requests()


class SuggestFeatureRequest(BaseModel):
    org_id: str = ''
    subject: str
    content: str
    company_name: str = ''
    user_email: str = ''


@router.get("/suggest-feature", dependencies=[Depends(require_operator)])
async def get_suggestions_route():
    try:
        return get_suggestions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/suggest-feature")
async def suggest_feature_route(data: SuggestFeatureRequest, request: Request):
    rate_limit.check_user("SUGGESTION_CREATE", request, data.org_id)     # 10 suggestions / hour / user (per instance)
    try:
        text = data.content
        if data.company_name:
            text = f"From: {data.company_name} ({data.user_email})\n\n{data.content}"
        return submit_suggest_feature(
            org_id=data.org_id,
            subject=data.subject,
            text=text,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggest-feature/mine")
async def get_my_suggestions_route(org_id: str = Query(...)):
    return get_my_suggestions(org_id)


@router.get("/suggest-feature/with-replies", dependencies=[Depends(require_operator)])
async def get_suggestions_with_replies_route():
    try:
        return get_suggestions_with_reply_count()
    except Exception:
        # suggestion_replies table not yet created — fall back to base list
        return get_suggestions()


@router.post("/suggest-feature/{log_id}/reply", dependencies=[Depends(require_operator)])
async def reply_to_suggestion_route(log_id: int, body: dict):
    reply_text = body.get("reply_text", "").strip()
    admin_name = body.get("admin_name", "Admin")
    if not reply_text:
        raise HTTPException(status_code=400, detail="reply_text is required")
    try:
        return reply_to_suggestion(log_id, reply_text, admin_name)
    except Exception as e:
        if 'suggestion_replies' in str(e):
            raise HTTPException(status_code=503, detail="Reply feature requires DB migration. Please run change_db.sql first.")
        raise HTTPException(status_code=500, detail=str(e))