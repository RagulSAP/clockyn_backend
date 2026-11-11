"""Refer & Earn, APP side (see core_functions/referral_functions.py and migrations/037_referral.sql).

  GET /referral/validate?code=ABC123   no login (the signup screen); rate limited per IP; answers only the referrer's name
  GET /referral/{org_id}               the organisation's code, share text, referrals and rewards (organisation admin only)

Operators use /gen-dashboard/referrals (router/gen_dashboard.py). `GET /referral/validate` is declared FIRST so that it is
never read as an organisation id.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core_functions import auth_policy as policy
from core_functions import referral_functions as referral
from core_functions.auth_dependency import assert_same_org, current_identity
from utils import rate_limit

router = APIRouter(prefix="/referral", tags=["referral"])


@router.get("/validate", dependencies=[Depends(rate_limit.by_ip("REFERRAL_VALIDATE"))])
async def validate_referral_code_route(code: str = Query(..., max_length=32)):
    return await referral.validate_code_public(code)


@router.get("/{org_id}")
async def get_org_referral_route(org_id: str, request: Request, user_id: Optional[str] = Query(None)):
    # With a token: the organisation must be one of the caller's and the caller must be an administrator (the global guard
    # also applies the same ROLE_RULES / org-id check; repeated here so the route never depends on that alone).
    assert_same_org(request, org_id)
    identity = current_identity(request)
    if identity is not None and identity.role_id != policy.ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="Forbidden")
    # Without a token (REQUIRE_AUTH off) the optional `user_id` is checked like the billing routes: it must own the organisation.
    return await referral.get_org_referral(org_id, user_id)
