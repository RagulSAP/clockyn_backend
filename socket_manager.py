import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from fastapi import WebSocket

# A client that does not accept a frame within this many seconds is treated as dead (its socket is dropped) so one
# stuck connection can never hold up the HTTP request that produced the event.
SEND_TIMEOUT_SECONDS = 5.0


class ConnectionManager:
    def __init__(self):
        # Maps user_id -> list of active WebSocket sessions (multi-device support)
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        user_id = str(user_id)
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, user_id: str, websocket: WebSocket = None):
        user_id = str(user_id)
        if user_id in self.active_connections:
            if websocket and websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            else:
                # Fallback to popping user if no specific socket provided (old logic)
                self.active_connections.pop(user_id, None)
                return

            if not self.active_connections[user_id]:
                del self.active_connections[user_id]

    def is_online(self, user_id: str) -> bool:
        """True when the user has at least one open socket on this process."""
        return bool(self.active_connections.get(str(user_id)))

    async def _send_to_user_sessions(self, user_id: str, payload: Dict[str, Any]) -> int:
        """Requirement: Deliver to all active tabs/devices of a specific user. Returns the number of sockets reached."""
        uid_str = str(user_id)
        delivered = 0
        if uid_str in self.active_connections:
            for connection in list(self.active_connections[uid_str]):
                try:
                    await asyncio.wait_for(connection.send_json(payload), timeout=SEND_TIMEOUT_SECONDS)
                    delivered += 1
                except Exception:
                    self.disconnect(uid_str, connection)
        return delivered

    async def send_event(self, user_id: str, payload: Dict[str, Any]) -> int:
        """Public name for the per-user fan-out used by services.realtime (every socket of the user)."""
        return await self._send_to_user_sessions(user_id, payload)

    async def broadcast_chat_message(self, sender_id: str, receiver_id: str, msg: Any):
        """Standardized payload for 1-to-1 chats. Uses type='new_message' so all chat screens handle it."""
        payload = {
            "type": "new_message",
            "data": {
                "message_id": str(msg.get("message_id")),
                "timestamp": msg.get("timestamp"),
                "message_type": msg.get("message_type", "text"),
                "message": msg.get("message"),
                "sender_id": str(sender_id),
                "receiver_id": str(receiver_id),
                "sender_name": msg.get("sender_name", "Someone"),
                "file_url": msg.get("file_url"),
                "is_delivered": True,
                "is_read": False,
            }
        }
        await self._send_to_user_sessions(receiver_id, payload)
        await self._send_to_user_sessions(sender_id, payload)

    async def broadcast_group_message(self, sender_id: str, member_ids: List[str], msg: Any):
        """Send to every member's personal socket. Uses type='new_group_message'."""
        payload = {
            "type": "new_group_message",
            "data": {
                "message_id": str(msg.get("message_id")),
                "timestamp": msg.get("timestamp"),
                "message_type": msg.get("message_type", "text"),
                "message": msg.get("message"),
                "sender_id": str(sender_id),
                "group_id": str(msg.get("group_id")),
                "sender_name": msg.get("sender_name", "Someone"),
                "file_url": msg.get("file_url"),
                "is_read": False,
            }
        }
        for member_id in member_ids:
            await self._send_to_user_sessions(member_id, payload)

manager = ConnectionManager()
