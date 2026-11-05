import asyncio
from typing import Dict

# Maps user_id -> asyncio.Queue for pushing logout events to the old device's SSE connection
_sessions: Dict[str, asyncio.Queue] = {}


def register_session(user_id: str, queue: asyncio.Queue):
    _sessions[user_id] = queue


def unregister_session(user_id: str):
    _sessions.pop(user_id, None)


async def force_logout_sse(user_id: str):
    """Legacy channel only: push force_logout to the device's SSE connection if it has one."""
    queue = _sessions.get(user_id)
    if queue:
        await queue.put("force_logout")


async def force_logout_user(user_id: str):
    """Sign a user out everywhere: SSE queue, `force_logout` frame on the chat socket and a silent FCM data message
    to every device of the user (services.realtime.force_logout). Used by the organisation deactivation flow."""
    from services import realtime
    await realtime.force_logout(user_id)
