from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from core_functions.operator_auth import require_operator
from core_functions.gen_plans_functions import (
    get_plans, get_org_subscription, get_first_coupon, validate_coupon,
    list_coupons, create_coupon, update_coupon, delete_coupon,
    list_plans, create_plan, update_plan, delete_plan,
    check_org_access, check_plan_feature
)

router = APIRouter(prefix="/plans", tags=["plans"])


class CouponCreate(BaseModel):
    coupon_name: str
    discount: float


class CouponUpdate(BaseModel):
    coupon_name: str
    discount: float
    status: int


class PlanCreate(BaseModel):
    plan_name: str
    plan_amount: float          # list price per staff per MONTH
    plan_month: int             # duration of this plan in months (1, 6, 12 ...)
    free_months: int = 0        # months of plan_month that are not charged (6 months with 1 free charges 5)
    offer_text: Optional[str] = None   # optional wording for the app, e.g. "Pay 5 months, get 1 free"


class PlanUpdate(BaseModel):
    plan_name: str
    plan_amount: float
    plan_month: int
    free_months: Optional[int] = None   # left out = keep the stored value
    offer_text: Optional[str] = None    # left out = keep; "" = clear


@router.get("/get_plans")
async def get_plans_route():
    return await get_plans()

@router.get("/subscription/{org_id}")
async def get_org_subscription_route(org_id: str):
    return await get_org_subscription(org_id)

@router.get("/plan-status/{org_id}")
async def get_plan_status_route(org_id: str):
    from core_functions.gen_plans_functions import get_org_plan_status
    return await get_org_plan_status(org_id)

@router.get("/coupon/first")
async def get_first_coupon_route():
    return await get_first_coupon()

@router.get("/coupon/validate")
async def validate_coupon_route(coupon_name: str = Query(...)):
    return await validate_coupon(coupon_name)

@router.get("/coupons", dependencies=[Depends(require_operator)])
async def list_coupons_route():
    return await list_coupons()

@router.post("/coupons", dependencies=[Depends(require_operator)])
async def create_coupon_route(body: CouponCreate):
    return await create_coupon(body.coupon_name, body.discount)

@router.put("/coupons/{coupon_id}", dependencies=[Depends(require_operator)])
async def update_coupon_route(coupon_id: int, body: CouponUpdate):
    return await update_coupon(coupon_id, body.coupon_name, body.discount, body.status)

@router.delete("/coupons/{coupon_id}", dependencies=[Depends(require_operator)])
async def delete_coupon_route(coupon_id: int):
    return await delete_coupon(coupon_id)

@router.get("/all", dependencies=[Depends(require_operator)])
async def list_plans_route():
    return await list_plans()

@router.post("/", dependencies=[Depends(require_operator)])
async def create_plan_route(body: PlanCreate):
    return await create_plan(body.plan_name, body.plan_amount, body.plan_month, body.free_months, body.offer_text)

@router.put("/{plan_id}", dependencies=[Depends(require_operator)])
async def update_plan_route(plan_id: int, body: PlanUpdate):
    return await update_plan(plan_id, body.plan_name, body.plan_amount, body.plan_month, body.free_months, body.offer_text)

@router.delete("/{plan_id}", dependencies=[Depends(require_operator)])
async def delete_plan_route(plan_id: int):
    return await delete_plan(plan_id)
