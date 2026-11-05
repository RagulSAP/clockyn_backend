"""DB-free tests for services/realtime.py and the socket plumbing around it.
Real `ConnectionManager` with fake sockets; the legacy `/ws/badge` registry and the DB/push layers are faked.
Two-socket integration tests mount the chat router on a small FastAPI app (TestClient websockets).
Run: python -m pytest tests/test_realtime.py -q
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import socket_manager  # noqa: E402
from services import realtime, push_service  # noqa: E402
from router import ws_badge  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class FakeSocket:
    def __init__(self, fail=False, hang=False):
        self.frames = []
        self.fail = fail
        self.hang = hang
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail:
            raise RuntimeError("socket closed")
        self.frames.append(payload)


@pytest.fixture
def mgr(monkeypatch):
    """A fresh chat-socket registry, an empty legacy badge registry, and recorders for the push layer."""
    m = socket_manager.ConnectionManager()
    monkeypatch.setattr(realtime, "manager", m)
    monkeypatch.setattr(socket_manager, "manager", m)
    monkeypatch.setattr(ws_badge, "_connections", {})
    return m


class Pushes:
    def __init__(self, monkeypatch):
        self.users = []     # (user_ids, title, body, data, silent)
        self.tokens = []    # (tokens, data, silent)

        async def to_users(user_ids, title, body, data=None, image_url=None, silent=False):
            self.users.append((list(user_ids), title, body, data, silent))
            return len(list(user_ids))

        async def to_user(user_id, title, body, data=None, image_url=None, silent=False):
            self.users.append(([user_id], title, body, data, silent))
            return 1

        async def to_tokens(tokens, title, body, data=None, image_url=None, silent=False):
            self.tokens.append((list(tokens), data, silent))
            return len(list(tokens))

        monkeypatch.setattr(push_service, "send_push_to_users", to_users)
        monkeypatch.setattr(push_service, "send_push_to_user", to_user)
        monkeypatch.setattr(push_service, "send_push_to_tokens", to_tokens)


# ---------------------------------------------------------------------------- delivery basics
def test_frame_reaches_every_socket_of_that_user_only(mgr):
    async def go():
        a1, a2, b = FakeSocket(), FakeSocket(), FakeSocket()
        await mgr.connect("admin", a1)
        await mgr.connect("admin", a2)          # second device / tab
        await mgr.connect("other", b)
        n = await realtime.publish_to_user("admin", {"type": "announcement", "data": {}})
        return n, a1, a2, b

    n, a1, a2, b = run(go())
    assert n == 2 and len(a1.frames) == 1 and len(a2.frames) == 1 and b.frames == []


def test_offline_user_is_not_an_error(mgr):
    assert run(realtime.publish_to_user("nobody", {"type": "badge_update"})) == 0


def test_badge_type_frames_are_mirrored_to_the_legacy_socket_others_are_not(mgr):
    async def go():
        chat, legacy = FakeSocket(), FakeSocket()
        await mgr.connect("admin", chat)
        ws_badge._connections["admin"] = legacy
        await realtime.publish_to_user("admin", realtime.badge_frame())
        await realtime.publish_to_user("admin", realtime.request_submitted_frame("r1", "leave"))
        await realtime.publish_to_user("admin", realtime.announcement_frame("a1", "T", "all", "o1"))
        return chat, legacy

    chat, legacy = run(go())
    assert [f["type"] for f in chat.frames] == ["badge_update", "request_submitted", "announcement"]
    assert [f["type"] for f in legacy.frames] == ["badge_update", "request_submitted"]


def test_legacy_only_listener_still_receives_badge_frames(mgr):
    async def go():
        legacy = FakeSocket()
        ws_badge._connections["admin"] = legacy
        n = await realtime.publish_to_user("admin", realtime.badge_frame({"id": "p"}))
        return n, legacy

    n, legacy = run(go())
    assert n == 0 and legacy.frames == [{"type": "badge_update", "notification": {"id": "p"}}]


def test_legacy_notify_badge_update_goes_through_realtime(mgr):
    async def go():
        chat, legacy = FakeSocket(), FakeSocket()
        await mgr.connect("u1", chat)
        ws_badge._connections["u1"] = legacy
        await ws_badge.notify_badge_update("u1", "request_submitted")
        return chat, legacy

    chat, legacy = run(go())
    assert chat.frames == [{"type": "request_submitted"}] and legacy.frames == [{"type": "request_submitted"}]


def test_dead_and_hung_sockets_are_dropped_and_do_not_block_the_rest(mgr, monkeypatch):
    monkeypatch.setattr(socket_manager, "SEND_TIMEOUT_SECONDS", 0.05)

    async def go():
        good, dead, hung = FakeSocket(), FakeSocket(fail=True), FakeSocket(hang=True)
        for ws in (dead, hung, good):
            await mgr.connect("u1", ws)
        n = await realtime.publish_to_user("u1", {"type": "badge_update"})
        return n, good, mgr.active_connections["u1"], good

    n, good, remaining, _ = run(go())
    assert n == 1 and good.frames == [{"type": "badge_update"}] and remaining == [good]


def test_a_failing_legacy_socket_never_breaks_publishing(mgr):
    async def go():
        chat = FakeSocket()
        await mgr.connect("u1", chat)
        ws_badge._connections["u1"] = FakeSocket(fail=True)
        n = await realtime.publish_to_user("u1", realtime.badge_frame())
        return n, chat

    n, chat = run(go())
    assert n == 1 and len(chat.frames) == 1 and "u1" not in ws_badge._connections


def test_publish_to_users_dedupes_recipients(mgr):
    async def go():
        a, b = FakeSocket(), FakeSocket()
        await mgr.connect("a", a)
        await mgr.connect("b", b)
        n = await realtime.publish_to_users(["a", "b", "a", None, "c"], {"type": "announcement"})
        return n, a, b

    n, a, b = run(go())
    assert n == 2 and len(a.frames) == 1 and len(b.frames) == 1


def test_offline_user_ids(mgr):
    run(mgr.connect("online", FakeSocket()))
    assert realtime.offline_user_ids(["sender", "online", "off1", "off1", "off2"], exclude="sender") == ["off1", "off2"]
    assert realtime.is_online("online") and not realtime.is_online("off1")


# ---------------------------------------------------------------------------- frame shapes (the mock's contract)
def test_frame_shapes():
    assert realtime.request_submitted_frame("req-301", "device_change") == {
        "type": "request_submitted", "data": {"request_id": "req-301", "request_type": "device_change"}}
    assert realtime.announcement_frame("ann-5", "Hi", "branch", "org-1") == {
        "type": "announcement", "target": "branch",
        "data": {"announcement_id": "ann-5", "title": "Hi", "target": "branch", "org_id": "org-1"}}
    assert realtime.badge_frame() == {"type": "badge_update"}
    note = realtime.punch_notification("Aarav Sharma", "punch_in", "09:02 AM", now_ms=1760000000)
    assert note == {"id": "punch-1760000000", "title": "Punched In", "body": "Aarav Sharma punched in at 09:02 AM",
                    "type": "punch_in", "image_url": None}
    out = realtime.punch_notification("Aarav Sharma", "punch_out", "06:00 PM", "https://img", now_ms=1)
    assert out["title"] == "Punched Out" and out["body"].endswith("punched out at 06:00 PM") and out["image_url"] == "https://img"
    assert realtime.force_logout_frame("u1") == {"type": "force_logout", "data": {"user_id": "u1"}}


# ---------------------------------------------------------------------------- punch in/out
def test_punch_notifies_admin_and_managers_on_the_chat_socket_and_by_push(mgr, monkeypatch):
    pushes = Pushes(monkeypatch)
    asked = {}

    def recipients(org_id, exclude):
        asked["args"] = (org_id, exclude)
        return ["admin", "mgr"]

    monkeypatch.setattr(realtime, "_employee_name", lambda uid: "Aarav Sharma")
    monkeypatch.setattr(realtime, "org_admin_manager_user_ids", recipients)

    async def go():
        admin, mgr_ws, staff = FakeSocket(), FakeSocket(), FakeSocket()
        await mgr.connect("admin", admin)
        await mgr.connect("mgr", mgr_ws)
        await mgr.connect("staff1", staff)
        await realtime.notify_punch_in_out("staff1", "org-1", "punch_in", "09:02 AM")
        return admin, mgr_ws, staff

    admin, mgr_ws, staff = run(go())
    assert asked["args"] == ("org-1", "staff1")                      # the punching user is excluded
    for ws in (admin, mgr_ws):
        frame = ws.frames[0]
        assert frame["type"] == "badge_update"
        n = frame["notification"]
        assert n["title"] == "Punched In" and n["body"] == "Aarav Sharma punched in at 09:02 AM"
        assert n["type"] == "punch_in" and n["id"].startswith("punch-") and n["image_url"] is None
    assert staff.frames == []
    assert pushes.users == [(["admin", "mgr"], "Punch In", "Aarav Sharma punch in at 09:02 AM",
                             {"type": "punch_in", "user_id": "staff1", "image_url": ""}, False)]


def test_punch_for_an_unknown_employee_or_without_recipients_sends_nothing(mgr, monkeypatch):
    pushes = Pushes(monkeypatch)
    monkeypatch.setattr(realtime, "_employee_name", lambda uid: None)
    run(realtime.notify_punch_in_out("ghost", "org-1", "punch_in", "09:00 AM"))
    monkeypatch.setattr(realtime, "_employee_name", lambda uid: "Asha")
    monkeypatch.setattr(realtime, "org_admin_manager_user_ids", lambda o, e: [])
    run(realtime.notify_punch_in_out("u1", "org-1", "punch_out", "09:00 AM"))
    assert pushes.users == []


def test_punch_notification_failure_never_raises(mgr, monkeypatch):
    def boom(uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(realtime, "_employee_name", boom)
    run(realtime.notify_punch_in_out("u1", "org-1", "punch_in", "09:00 AM"))


def test_auth_function_wrappers_delegate_to_the_new_modules(monkeypatch):
    from core_functions import auth_function as af

    seen = {}

    async def fake_punch(*a):
        seen["punch"] = a

    async def fake_send(token, title, body, data=None, image_url=None, silent=False):
        seen["send"] = (token, title, body, data, image_url)
        return True

    monkeypatch.setattr(af._realtime, "notify_punch_in_out", fake_punch)
    monkeypatch.setattr(af._push_service, "send_push", fake_send)
    run(af.send_punch_in_out_notification("u1", "o1", "punch_in", "09:00 AM", image_url="x"))
    run(af.send_push_notification("tok", "t", "b", {"k": 1}))
    run(af._send_push_to_token("tok2", "t2", "b2", {"k": 2}, "img"))
    run(af.send_push_notification("", "t", "b"))                       # empty token: nothing
    assert seen["punch"] == ("u1", "o1", "punch_in", "09:00 AM", "x")
    assert seen["send"] == ("tok2", "t2", "b2", {"k": 2}, "img")
    assert callable(af.get_fcm_access_token) and callable(af._clear_admin_push_token)


# ---------------------------------------------------------------------------- request submitted
def test_request_submission_reaches_admin_and_managers_with_the_contract_frame(mgr, monkeypatch):
    from core_functions import requests_functions as rf

    monkeypatch.setattr(rf, "_get_org_admin_manager_user_ids", lambda org, exclude: ["admin", "mgr"])

    async def go():
        admin, mgr_ws, other = FakeSocket(), FakeSocket(), FakeSocket()
        legacy = FakeSocket()
        await mgr.connect("admin", admin)
        await mgr.connect("mgr", mgr_ws)
        await mgr.connect("someone-else", other)
        ws_badge._connections["admin"] = legacy
        rf._ws_notify_request("org-1", exclude_user_id="staff1", request_id="req-301", request_type="device_change")
        await asyncio.sleep(0.05)
        return admin, mgr_ws, other, legacy

    admin, mgr_ws, other, legacy = run(go())
    expected = {"type": "request_submitted", "data": {"request_id": "req-301", "request_type": "device_change"}}
    assert admin.frames == [expected] and mgr_ws.frames == [expected] and other.frames == []
    assert legacy.frames == [expected]


def test_every_submit_function_passes_request_id_and_type():
    import inspect
    from core_functions import requests_functions as rf

    src = inspect.getsource(rf)
    for rtype in ("device_change", "shift_change", "leave", "permission"):
        assert f"request_type='{rtype}')" in src
    assert src.count("_ws_notify_request(org_id, exclude_user_id=user_id, request_id=request_id") == 4


def test_org_admin_manager_lookup_is_org_scoped_and_excludes_the_requester(monkeypatch):
    class Cur:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=None):
            self.calls.append((" ".join(sql.split()), params))

        def fetchall(self):
            return [("a",), ("m",)]

        def __enter__(self):
            return self

        def __exit__(self, *x):
            return False

    class Conn:
        def __init__(self, c):
            self.c = c

        def cursor(self):
            return self.c

    cur = Cur()
    monkeypatch.setattr(realtime, "get_db_connection", lambda: Conn(cur))
    monkeypatch.setattr(realtime, "return_connection", lambda c: None)
    assert realtime.org_admin_manager_user_ids("org-1", "staff1") == ["a", "m"]
    sql, params = cur.calls[0]
    assert "o.org_id = %s AND o.parent_id = u.user_id" in sql and "u.role_id = 2 AND u.parent_id = %s" in sql
    assert sql.endswith("AND u.user_id <> %s") and params == ("org-1", "org-1", "staff1")
    assert sql.count("%s") == len(params)
    assert realtime.org_admin_manager_user_ids("") == []


# ---------------------------------------------------------------------------- announcements
def test_announcement_reaches_only_its_recipients_by_socket_and_push(mgr, monkeypatch):
    pushes = Pushes(monkeypatch)

    async def go():
        branch_a, branch_b = FakeSocket(), FakeSocket()
        await mgr.connect("a1", branch_a)
        await mgr.connect("b1", branch_b)
        await realtime.deliver_announcement(["a1", "a2"], "ann-5", "Holiday", "Office closed tomorrow", "branch", "org-1",
                                            push_title="Holiday")
        return branch_a, branch_b

    a, b = run(go())
    assert a.frames == [{"type": "announcement", "target": "branch",
                         "data": {"announcement_id": "ann-5", "title": "Holiday", "target": "branch", "org_id": "org-1"}}]
    assert b.frames == []
    assert pushes.users == [(["a1", "a2"], "Holiday", "Office closed tomorrow",
                             {"type": "announcement", "announcement_id": "ann-5", "org_id": "org-1", "target": "branch"}, False)]


def test_announcement_without_recipients_does_nothing(mgr, monkeypatch):
    pushes = Pushes(monkeypatch)
    run(realtime.deliver_announcement([], "a", "T", "m", "all", "o"))
    assert pushes.users == []


def test_send_announcement_resolves_recipients_in_the_org_and_counts_them(mgr, monkeypatch):
    from core_functions import announcement_functions as af

    delivered = {}

    async def fake_deliver(ids, announcement_id, title, message, target, org_id, push_title=None, push_data=None):
        delivered.update(ids=list(ids), title=title, target=target, org=org_id, push_data=push_data)

    class Cur:
        def __init__(self):
            self.sql = []
            self.fetch = [{"role_id": 1}, None, [{"user_id": "u1"}, {"user_id": "u2"}]]

        def execute(self, sql, params=None):
            self.sql.append((" ".join(sql.split()), params))

        def fetchone(self):
            return {"role_id": 1} if len(self.sql) == 1 else {"announcement_id": "x", "title": "T", "message": "m",
                                                               "org_id": "org-1", "target": "branch", "created_at": None}

        def fetchall(self):
            return [{"user_id": "u1"}, {"user_id": "u2"}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def __init__(self, c):
            self.c = c

        def cursor(self, cursor_factory=None):
            return self.c

        def commit(self):
            pass

        def rollback(self):
            pass

    cur = Cur()
    monkeypatch.setattr(af, "get_db_connection", lambda: Conn(cur))
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    monkeypatch.setattr(realtime, "deliver_announcement", fake_deliver)

    async def go():
        res = await af.send_announcement(af.SendAnnouncementRequest(
            org_id="org-1", sent_by_id="adm", title="T", message="m", target="branch", branch_id=4))
        await asyncio.sleep(0.01)
        return res

    res = run(go())
    recipient_sql, params = [c for c in cur.sql if c[0].startswith("SELECT user_id FROM users")][0]
    assert "parent_id = %s AND branch_id = %s" in recipient_sql and "status = 1" in recipient_sql
    assert params == ("org-1", 4) and recipient_sql.count("%s") == len(params)
    assert res["data"]["recipient_count"] == 2
    assert delivered["ids"] == ["u1", "u2"] and delivered["target"] == "branch" and delivered["org"] == "org-1"
    assert delivered["push_data"]["type"] == "announcement" and delivered["push_data"]["target"] == "branch"


# ---------------------------------------------------------------------------- group read updates / membership
def test_group_read_updates_go_to_senders_only_never_to_the_reader(mgr):
    async def go():
        s1, s2, reader = FakeSocket(), FakeSocket(), FakeSocket()
        await mgr.connect("s1", s1)
        await mgr.connect("s2", s2)
        await mgr.connect("reader", reader)
        await realtime.publish_group_read_updates("reader", "g", [
            {"message_id": "m1", "sender_id": "s1", "read_count": 1, "total_members": 2},
            {"message_id": "m2", "sender_id": "reader", "read_count": 1, "total_members": 2},
            {"message_id": "m3", "sender_id": "s2", "read_count": 2, "total_members": 2},
        ])
        return s1, s2, reader

    s1, s2, reader = run(go())
    assert s1.frames == [{"type": "read_update", "data": {"reader_id": "reader", "group_id": "g", "message_id": "m1",
                                                          "read_count": 1, "total_members": 2}}]
    assert s2.frames[0]["data"]["message_id"] == "m3" and reader.frames == []


def test_more_than_50_read_updates_collapse_to_one_refetch_frame_per_sender(mgr):
    updates = [{"message_id": f"m{i}", "sender_id": "s1" if i % 2 else "s2", "read_count": 1, "total_members": 3}
               for i in range(60)]

    async def go():
        s1, s2 = FakeSocket(), FakeSocket()
        await mgr.connect("s1", s1)
        await mgr.connect("s2", s2)
        await realtime.publish_group_read_updates("reader", "g", updates)
        return s1, s2

    s1, s2 = run(go())
    assert s1.frames == [{"type": "read_update", "data": {"reader_id": "reader", "group_id": "g"}}]
    assert s2.frames == [{"type": "read_update", "data": {"reader_id": "reader", "group_id": "g"}}]


def test_exactly_50_read_updates_keep_the_detailed_frames(mgr):
    updates = [{"message_id": f"m{i}", "sender_id": "s1", "read_count": 1, "total_members": 3} for i in range(50)]

    async def go():
        s1 = FakeSocket()
        await mgr.connect("s1", s1)
        await realtime.publish_group_read_updates("reader", "g", updates)
        return s1

    assert len(run(go()).frames) == 50


def test_members_added_new_get_group_created_existing_get_group_updated(mgr):
    async def go():
        new, old, outsider = FakeSocket(), FakeSocket(), FakeSocket()
        await mgr.connect("new", new)
        await mgr.connect("old", old)
        await mgr.connect("outsider", outsider)
        await realtime.publish_group_members_added("g1", "Sales", "adm", ["new"], ["old"], 2)
        return new, old, outsider

    new, old, outsider = run(go())
    assert new.frames == [{"type": "group_created", "data": {"group_id": "g1", "group_name": "Sales", "created_by": "adm"}}]
    assert old.frames == [{"type": "group_updated", "data": {"group_id": "g1", "member_count": 2}}]
    assert outsider.frames == []


# ---------------------------------------------------------------------------- force logout
def test_force_logout_reaches_sse_socket_and_silent_push(mgr, monkeypatch):
    from core_functions import session_registry

    pushes = Pushes(monkeypatch)

    async def go():
        queue = asyncio.Queue()
        session_registry.register_session("u1", queue)
        ws = FakeSocket()
        await mgr.connect("u1", ws)
        await realtime.force_logout("u1")
        session_registry.unregister_session("u1")
        return queue.get_nowait(), ws

    event, ws = run(go())
    assert event == "force_logout"
    assert ws.frames == [{"type": "force_logout", "data": {"user_id": "u1"}}]
    assert pushes.users == [(["u1"], "", "", {"type": "force_logout", "user_id": "u1"}, True)]


def test_force_logout_with_explicit_tokens_pushes_only_to_them(mgr, monkeypatch):
    pushes = Pushes(monkeypatch)
    run(realtime.force_logout("u1", push_tokens=["old-1", "old-2"], sse=False))
    assert pushes.tokens == [(["old-1", "old-2"], {"type": "force_logout", "user_id": "u1"}, True)] and pushes.users == []
    run(realtime.force_logout("u1", push_tokens=[], sse=False))        # nothing to notify
    assert len(pushes.tokens) == 1


def test_session_registry_force_logout_user_is_the_full_signal(mgr, monkeypatch):
    from core_functions import session_registry

    pushes = Pushes(monkeypatch)
    run(session_registry.force_logout_user("u9"))
    assert pushes.users and pushes.users[0][3] == {"type": "force_logout", "user_id": "u9"} and pushes.users[0][4] is True


class AuthCur:
    def __init__(self, row):
        self.row = row
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row


def test_takeover_login_notifies_exactly_the_previous_device(mgr, monkeypatch):
    from core_functions import auth_function as af

    pushes = Pushes(monkeypatch)
    monkeypatch.setattr(af._push_service, "get_user_tokens", lambda uid: ["old-a", "new-tok"])
    cur = AuthCur({"device_token": "dev-OLD", "push_token": "old-b"})
    user = {"user_id": "u1", "role_id": 3}

    async def go():
        snap = await af._snapshot_previous_device(cur, user, "dev-NEW", "new-tok")
        await af._signal_previous_device("u1", snap)
        return snap

    snap = run(go())
    assert snap["takeover"] is True and snap["old_tokens"] == ["old-b", "old-a"]      # never the new device's token
    assert cur.sql == [("SELECT device_token, push_token FROM users WHERE user_id = %s", ("u1",))]
    assert pushes.tokens == [(["old-b", "old-a"], {"type": "force_logout", "user_id": "u1"}, True)]


@pytest.mark.parametrize("row, role, new_device", [
    ({"device_token": "dev-1", "push_token": "t"}, 3, "dev-1"),         # same device logging in again
    ({"device_token": None, "push_token": None}, 3, "dev-2"),           # first login / cleared by device change
    ({"device_token": "dev-1", "push_token": "t"}, 1, "dev-2"),         # admin: several devices are allowed
    (None, 2, "dev-2"),
])
def test_no_force_logout_push_without_a_single_device_takeover(mgr, monkeypatch, row, role, new_device):
    from core_functions import auth_function as af

    pushes = Pushes(monkeypatch)
    cur = AuthCur(row)

    async def go():
        snap = await af._snapshot_previous_device(cur, {"user_id": "u1", "role_id": role}, new_device, "tok")
        await af._signal_previous_device("u1", snap)
        return snap

    snap = run(go())
    assert snap["takeover"] is False and snap["old_tokens"] == []
    assert pushes.tokens == [] and pushes.users == []


# ---------------------------------------------------------------------------- chat router: pushes and two-socket integration
@pytest.fixture
def chat_app(mgr, monkeypatch):
    from router import chat
    from core_functions import gen_plans_functions

    async def no_org(request):          # plan gating (WP10) needs the database; these tests are about realtime
        return None

    monkeypatch.setattr(gen_plans_functions, "org_id_from_request", no_org)
    monkeypatch.setattr(chat, "shared_manager", mgr)
    app = FastAPI()
    app.include_router(chat.router)
    return chat, app


def test_group_message_pushes_only_to_offline_members_and_never_to_the_sender(chat_app, monkeypatch):
    chat, app = chat_app
    pushes = Pushes(monkeypatch)
    from core_functions import db_function

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return {"?column?": 1}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self, cursor_factory=None):
            return Cur()

    async def fake_send(req):
        return {"message_id": "g-1", "sender_name": "Asha", "group_id": req.group_id}

    async def member_ids(group_id):
        return ["sender", "online", "offline1", "offline2"]

    monkeypatch.setattr(db_function, "get_db_connection", lambda: Conn())
    monkeypatch.setattr(db_function, "return_connection", lambda c: None)
    monkeypatch.setattr(chat, "send_group_message", fake_send)
    monkeypatch.setattr(chat, "_get_group_member_ids", member_ids)

    async def go():
        online = FakeSocket()
        await chat.shared_manager.connect("online", online)
        await chat.group_send_route(chat.SendGroupMessageRequest(group_id="g", sender_id="sender", message="yo", message_type="text"))
        await asyncio.sleep(0.01)
        return online

    online = run(go())
    assert [f["type"] for f in online.frames] == ["new_group_message"]
    assert pushes.users == [(["offline1", "offline2"], "Asha", "yo",
                             {"type": "new_group_message", "group_id": "g", "sender_id": "sender", "message_id": "g-1"}, False)]


def test_chat_socket_answers_ping_with_pong(chat_app):
    _chat, app = chat_app
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/u1") as ws:
            ws.send_text("ping")
            assert ws.receive_text() == "pong"


def test_two_sockets_group_read_update_reaches_the_sender_only(chat_app, monkeypatch):
    chat, app = chat_app

    async def fake_mark(req):
        return {"success": True, "marked_count": 1, "read_updates": [
            {"message_id": "m1", "sender_id": "sender", "read_count": 1, "total_members": 2}]}

    monkeypatch.setattr(chat, "mark_group_as_read", fake_mark)

    async def fake_mark_direct(req):
        return {"success": True}

    monkeypatch.setattr(chat, "mark_as_read", fake_mark_direct)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/sender") as sender, client.websocket_connect("/ws/chat/reader") as reader:
            r = client.put("/group-chat/mark-read", json={"group_id": "g", "user_id": "reader"})
            assert r.status_code == 200 and r.json() == {"success": True, "marked_count": 1}
            assert sender.receive_json() == {"type": "read_update", "data": {
                "reader_id": "reader", "group_id": "g", "message_id": "m1", "read_count": 1, "total_members": 2}}
            # the reader got nothing: the first frame it sees is the one we trigger now
            client.put("/chat/mark-read", json={"sender_id": "reader", "receiver_id": "x"})
            assert reader.receive_json()["data"] == {"reader_id": "x", "sender_id": "reader"}


def test_two_sockets_add_members_new_member_gets_group_created(chat_app, monkeypatch):
    chat, app = chat_app

    async def fake_add(req, caller_id=None):
        return {"success": True, "added": 1, "membership_change": {
            "group_name": "Sales", "created_by": "adm", "new_member_ids": ["new"],
            "existing_member_ids": ["old"], "member_count": 2}}

    monkeypatch.setattr(chat, "add_group_members", fake_add)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/chat/new") as new, client.websocket_connect("/ws/chat/old") as old:
            r = client.post("/group-chat/add-members", json={"group_id": "g1", "member_ids": ["new"]})
            assert r.status_code == 200 and r.json() == {"success": True, "added": 1}      # body unchanged
            assert new.receive_json() == {"type": "group_created",
                                          "data": {"group_id": "g1", "group_name": "Sales", "created_by": "adm"}}
            assert old.receive_json() == {"type": "group_updated", "data": {"group_id": "g1", "member_count": 2}}


def test_group_send_for_a_non_member_is_a_real_403(chat_app, monkeypatch):
    chat, app = chat_app
    from core_functions import db_function

    class Cur:
        def execute(self, *a, **k):
            pass

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def cursor(self, cursor_factory=None):
            return Cur()

    monkeypatch.setattr(db_function, "get_db_connection", lambda: Conn())
    monkeypatch.setattr(db_function, "return_connection", lambda c: None)
    with TestClient(app) as client:
        r = client.post("/group-chat/send", json={"group_id": "g", "sender_id": "x", "message": "hi", "message_type": "text"})
        assert r.status_code == 403


def test_add_group_members_returns_the_membership_change_for_new_members_only(monkeypatch):
    from core_functions import chat_functions as cf

    class Cur:
        def __init__(self):
            self.sql = []
            # the group row (organisation check), then the three members, then the realtime facts
            self.ones = [{"org_id": "o1", "user_id": "adm"}, {"id": 1, "status": 1}, None, {"id": 2, "status": 0},
                         {"group_name": "Sales", "created_by": "adm"}]

        def execute(self, sql, params=None):
            self.sql.append((" ".join(sql.split()), params))

        def fetchone(self):
            return self.ones.pop(0)

        def fetchall(self):
            if "FROM users" in self.sql[-1][0]:                 # users_in_org: all three are users of the group's org
                return [{"user_id": "m1"}, {"user_id": "m2"}, {"user_id": "m3"}]
            return [{"member_id": "m0"}, {"member_id": "m1"}, {"member_id": "m2"}, {"member_id": "m3"}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Conn:
        def __init__(self, c):
            self.c = c

        def cursor(self, cursor_factory=None):
            return self.c

        def commit(self):
            pass

        def rollback(self):
            pass

    cur = Cur()
    monkeypatch.setattr(cf, "get_db_connection", lambda: Conn(cur))
    monkeypatch.setattr(cf, "return_connection", lambda c: None)
    res = run(cf.add_group_members(cf.AddGroupMembersRequest(group_id="g", member_ids=["m1", "m2", "m3"])))
    assert res["added"] == 2
    # m1 was already active (not new), m2 was re-activated, m3 was inserted
    assert res["membership_change"] == {"group_name": "Sales", "created_by": "adm", "new_member_ids": ["m2", "m3"],
                                        "existing_member_ids": ["m0", "m1"], "member_count": 4}
    assert any(s.startswith('SELECT group_name, user_id AS created_by FROM "group" WHERE group_id = %s') for s, _p in cur.sql)
