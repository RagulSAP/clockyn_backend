"""Clickstream intake: the Clockyn app sends batches of screen views and button taps (see core_functions/analytics_functions.py).

Bearer-token route, any role. The user and organisation are taken from the verified token; the operator console reads the
results through the operator routes `/gen-dashboard/clickstream/*` (router/gen_dashboard.py).
"""
from fastapi import APIRouter, Request

from core_functions import analytics_functions as analytics
from utils import rate_limit

router = APIRouter(tags=["analytics"])


@router.post("/analytics/events")
async def analytics_events_route(batch: analytics.EventBatch, request: Request):
    identity = getattr(request.state, "identity", None)
    rate_limit.check_user("ANALYTICS_EVENTS", request, identity.user_id if identity is not None else batch.user_id)
    return await analytics.record_events(batch, identity)
