"""
Meta Conversions API service.
Reads META_APP_ID, META_CLIENT_TOKEN, and optionally META_ACCESS_TOKEN / META_PIXEL_ID
from environment variables.

Required env vars (already in .env):
    META_APP_ID=1529133025464669
    META_CLIENT_TOKEN=<value>

Optional env vars (add when Conversions API is needed):
    META_ACCESS_TOKEN=<YOUR_SYSTEM_USER_TOKEN>
    META_PIXEL_ID=<YOUR_PIXEL_ID>
"""

import os
import time
import hashlib
import logging
import httpx

logger = logging.getLogger(__name__)

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PIXEL_ID     = os.getenv("META_PIXEL_ID", "")
CAPI_BASE         = "https://graph.facebook.com/v23.0"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _build_event(event_name: str, user_data: dict, custom_data: dict | None = None) -> dict:
    return {
        "event_name":    event_name,
        "event_time":    int(time.time()),
        "user_data":     user_data,
        "custom_data":   custom_data or {},
        "action_source": "app",
    }


async def _send_capi_event(event: dict) -> None:
    """Send a single event to the Meta Conversions API (optional, requires pixel credentials)."""
    if not META_ACCESS_TOKEN or not META_PIXEL_ID:
        logger.debug("[Meta CAPI] Skipped — META_ACCESS_TOKEN or META_PIXEL_ID not set")
        return
    url = f"{CAPI_BASE}/{META_PIXEL_ID}/events"
    payload = {
        "data":         [event],
        "access_token": META_ACCESS_TOKEN,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning(f"[Meta CAPI] {event['event_name']} → HTTP {resp.status_code}: {resp.text}")
            else:
                logger.info(f"[Meta CAPI] {event['event_name']} sent OK")
    except Exception as exc:
        logger.warning(f"[Meta CAPI] {event['event_name']} failed: {exc}")


async def send_registration_event(mobile: str | None = None, email: str | None = None) -> None:
    user_data: dict = {}
    if mobile:
        user_data["ph"] = _sha256(mobile)
    if email:
        user_data["em"] = _sha256(email)
    await _send_capi_event(_build_event("CompleteRegistration", user_data))


async def send_subscription_event(
    mobile: str | None = None,
    email: str | None = None,
    value: float | None = None,
    currency: str = "INR",
) -> None:
    user_data: dict = {}
    if mobile:
        user_data["ph"] = _sha256(mobile)
    if email:
        user_data["em"] = _sha256(email)
    custom: dict = {"currency": currency}
    if value is not None:
        custom["value"] = value
    await _send_capi_event(_build_event("Subscribe", user_data, custom))


async def send_attendance_event(mobile: str | None = None, action: str = "checkin") -> None:
    user_data: dict = {}
    if mobile:
        user_data["ph"] = _sha256(mobile)
    await _send_capi_event(_build_event("AttendanceMarked", user_data, {"action": action}))


async def send_leave_request_event(mobile: str | None = None) -> None:
    user_data: dict = {}
    if mobile:
        user_data["ph"] = _sha256(mobile)
    await _send_capi_event(_build_event("LeaveRequest", user_data))
