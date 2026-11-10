"""The in-app chat pushes carry the photo when the message is a picture (shown in the notification: thumbnail, expandable).

DB-free: the push service and the realtime registry are replaced by recorders.
Run: python -m pytest tests/test_chat_push_image.py -q
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from router import chat  # noqa: E402

URL = "https://s3.test/signed/photo.jpg?sig=1"


def recorder(monkeypatch):
    calls = []

    async def one(user_id, title, body, data, image_url=None, silent=False):
        calls.append(("one", user_id, title, body, data, image_url))
        return 1

    async def many(user_ids, title, body, data, image_url=None, silent=False):
        calls.append(("many", list(user_ids), title, body, data, image_url))
        return 1

    monkeypatch.setattr(chat.push_service, "send_push_to_user", one)
    monkeypatch.setattr(chat.push_service, "send_push_to_users", many)
    return calls


def test_direct_chat_push_carries_the_photo(monkeypatch):
    calls = recorder(monkeypatch)

    async def go():
        chat._push_chat_to_user("u2", "Priya", chat.push_service.PHOTO_PUSH_TEXT, {"type": "new_message"}, image_url=URL)
        chat._push_chat_to_user("u2", "Priya", "hello", {"type": "new_message"})
        await asyncio.sleep(0)

    asyncio.run(go())
    assert calls[0] == ("one", "u2", "Priya", "\U0001f4f7 Sent a photo", {"type": "new_message"}, URL)
    assert calls[1][5] is None and calls[1][3] == "hello"                      # a text message has no picture


def test_group_chat_push_carries_the_photo_to_offline_members_only(monkeypatch):
    calls = recorder(monkeypatch)
    monkeypatch.setattr(chat.realtime, "offline_user_ids", lambda ids, exclude=None: [i for i in ids if i not in ("online", exclude)])

    async def go():
        chat._push_group_to_offline_members(["a", "online", "me"], "me", "Priya", chat.push_service.PHOTO_PUSH_TEXT, {"type": "new_group_message"}, image_url=URL)
        chat._push_group_to_offline_members(["online"], "me", "Priya", "x", {})        # everybody is online: nothing is sent
        await asyncio.sleep(0)

    asyncio.run(go())
    assert calls == [("many", ["a"], "Priya", "\U0001f4f7 Sent a photo", {"type": "new_group_message"}, URL)]
