"""DB-free tests of Live Chat (organisation admin <-> Clockyn support console).

A small stateful fake database (users, org_info, gen_admin_login, live_chat_thread, live_chat_message) understands exactly
the statements core_functions/live_chat_functions.py issues and fails loudly on anything else. Realtime, push and S3 are
recorded / faked; the WebSocket tests use the real socket registry.
Run: python -m pytest tests/test_live_chat.py -q
"""
import asyncio
import concurrent.futures
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_policy as P  # noqa: E402
from core_functions import live_chat_functions as live  # noqa: E402
from core_functions import operator_auth  # noqa: E402
from router import gen_live_chat, live_chat  # noqa: E402
from services import realtime  # noqa: E402
from socket_manager import manager  # noqa: E402
from utils import rate_limit  # noqa: E402

ORIG_PUBLISH_OPERATORS = realtime.publish_to_operators
ORIG_PUBLISH_USERS = realtime.publish_to_users
ADMIN, ADMIN2, MANAGER, STAFF, OTHER_ADMIN = "adm-1", "adm-1b", "mgr-1", "stf-1", "adm-2"
ORG, ORG2, ORG3 = "org-1", "org-2", "org-3"
AGENT = "agent@clockyn.com"
S3_PREFIX = "documents/"            # what the fake upload stores; it must never appear in a response
OP = {"x-admin-id": AGENT}
MESSAGE_KEYS = {"message_id", "timestamp", "message_type", "message", "file_url", "file_name", "content_type",
                "sender_type", "sender_id", "sender_name", "is_read", "is_delivered"}


# ───────────────────────────────────────────────────────────────────────────── fake database
class FakeDB:
    def __init__(self):
        def user(id_, uid, org, name, role, email=None, status=1):
            return {"id": id_, "user_id": uid, "org_id": org, "name": name, "role_id": role, "status": status,
                    "email": email, "mobile_number": f"98000000{id_:02d}"}
        self.users = [
            user(1, ADMIN, ORG, "Anita Desai", 1, "anita@acme.in"),
            user(2, ADMIN2, ORG, "Ravi Kumar", 1, "ravi@acme.in"),
            user(3, MANAGER, None, "Meera", 2, "meera@acme.in"),
            user(4, STAFF, None, "Sam", 3, "sam@acme.in"),
            user(5, OTHER_ADMIN, ORG2, "Bela Shah", 1, "bela@bharat.in"),
            user(6, "adm-3", ORG3, "Chandan Rao", 1, "chandan@zenith.in"),
            user(7, "adm-gone", ORG3, "Inactive", 1, "gone@zenith.in", status=0),
        ]
        self.orgs = {ORG: "Acme Traders", ORG2: "Bharat Logistics", ORG3: "Zenith Pharma"}
        self.operators = [{"email": AGENT, "phone_number": "9000000009", "name": "Priya"}]
        self.threads, self.messages, self.log = [], [], []
        self.commits = self.rollbacks = 0

    def thread(self, org_id):
        return next((t for t in self.threads if t["org_id"] == org_id), None)

    def unread(self, org_id, sender_type):
        return sum(1 for m in self.messages if m["org_id"] == org_id and m["sender_type"] == sender_type and m["read_at"] is None)


class FakeCursor:
    def __init__(self, db):
        self.db, self.rows, self.rowcount = db, [], 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def _set(self, rows, rowcount=None):
        self.rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def execute(self, sql, params=None):
        s, p, db = " ".join(sql.split()), tuple(params or ()), self.db
        db.log.append((s, p))
        self._set([], 0)

        if s.startswith("SELECT user_id, name FROM users WHERE user_id = %s AND org_id = %s AND role_id = 1 AND status = 1"):
            u = next((u for u in db.users if (u["user_id"], u["org_id"]) == (p[0], p[1]) and u["role_id"] == 1 and u["status"] == 1), None)
            return self._set([{"user_id": u["user_id"], "name": u["name"]}] if u else [])
        if s.startswith("SELECT user_id FROM users WHERE org_id = %s AND role_id = 1 AND status = 1"):
            return self._set([{"user_id": u["user_id"]} for u in db.users if u["org_id"] == p[0] and u["role_id"] == 1 and u["status"] == 1])
        if s.startswith("SELECT org_name FROM org_info WHERE org_id = %s"):
            return self._set([{"org_name": db.orgs[p[0]]}] if p[0] in db.orgs else [])
        if s.startswith("SELECT DISTINCT ON (org_id) org_id, name, email, mobile_number FROM users WHERE org_id = ANY(%s) AND role_id = 1 ORDER BY org_id, id"):
            out = {}
            for u in sorted(db.users, key=lambda u: u["id"]):
                if u["org_id"] in p[0] and u["role_id"] == 1 and u["org_id"] not in out:
                    out[u["org_id"]] = {"org_id": u["org_id"], "name": u["name"], "email": u["email"], "mobile_number": u["mobile_number"]}
            return self._set(list(out.values()))
        if s.startswith("SELECT name FROM gen_admin_login WHERE email = %s"):
            return self._set([{"name": o["name"]} for o in db.operators if o["email"] == p[0]])
        if s.startswith("SELECT name FROM gen_admin_login WHERE phone_number = %s"):
            return self._set([{"name": o["name"]} for o in db.operators if o["phone_number"] == p[0]])

        if s.startswith("SELECT thread_id, status FROM live_chat_thread WHERE org_id = %s"):
            t = db.thread(p[0])
            return self._set([{"thread_id": t["thread_id"], "status": t["status"]}] if t else [])
        if s.startswith("INSERT INTO live_chat_thread"):
            assert "ON CONFLICT (org_id) DO UPDATE" in s and "RETURNING thread_id, status" in s     # race-safe lazy creation
            # the fake applies the upsert itself, so the real clause is asserted here: an org message REOPENS the thread
            assert "DO UPDATE SET status = 'open', last_message = EXCLUDED.last_message" in s and "last_sender_type = 'org'" in s
            thread_id, org_id, preview, mtype, at = p[0], p[1], p[2], p[3], p[4]
            t = db.thread(org_id)
            if t is None:
                t = {"thread_id": thread_id, "org_id": org_id, "created_at": at}
                db.threads.append(t)
            t.update(status="open", last_message=preview, last_message_type=mtype, last_sender_type="org",
                     last_message_at=at, updated_at=p[6])
            return self._set([{"thread_id": t["thread_id"], "status": t["status"]}])
        if s.startswith("UPDATE live_chat_thread SET last_message = %s"):
            message, mtype, at, _upd, org_id = p
            t = db.thread(org_id)
            t.update(last_message=message, last_message_type=mtype, last_sender_type="support", last_message_at=at)
            return self._set([], 1)
        if s.startswith("UPDATE live_chat_thread SET status = %s"):
            t = db.thread(p[2])
            if t:
                t["status"] = p[0]
            return self._set([{"thread_id": t["thread_id"]}] if t else [])

        if s.startswith("INSERT INTO live_chat_message"):
            keys = ["message_id", "thread_id", "org_id", "sender_type", "sender_id", "sender_name", "message_type", "message",
                    "file_key", "file_name", "content_type", "created_at"]
            assert s.count("%s") == len(keys) == len(p)
            row = dict(zip(keys, p), id=len(db.messages) + 1, read_at=None)
            db.messages.append(row)
            return self._set([], 1)
        if s.startswith("SELECT message_id, created_at, message_type, message, file_key, file_name, content_type, sender_type, sender_id, sender_name, read_at FROM live_chat_message WHERE thread_id = %s ORDER BY id DESC LIMIT %s"):
            rows = sorted((m for m in db.messages if m["thread_id"] == p[0]), key=lambda m: -m["id"])[: p[1]]
            return self._set(rows)
        if s.startswith("SELECT COUNT(*) AS cnt FROM live_chat_message WHERE org_id = %s AND sender_type = %s AND read_at IS NULL"):
            return self._set([{"cnt": db.unread(p[0], p[1])}])
        if s.startswith("UPDATE live_chat_message SET read_at = %s WHERE org_id = %s AND sender_type = %s AND read_at IS NULL"):
            hit = [m for m in db.messages if m["org_id"] == p[1] and m["sender_type"] == p[2] and m["read_at"] is None]
            for m in hit:
                m["read_at"] = p[0]
            return self._set([], len(hit))
        if s.startswith("SELECT COUNT(*) AS unread_messages, COUNT(DISTINCT org_id) AS unread_threads FROM live_chat_message WHERE sender_type = 'org' AND read_at IS NULL"):
            un = [m for m in db.messages if m["sender_type"] == "org" and m["read_at"] is None]
            return self._set([{"unread_messages": len(un), "unread_threads": len({m["org_id"] for m in un})}])

        if s.startswith("SELECT t.thread_id, t.org_id, t.status"):
            args, status, term = list(p), None, None
            if "AND t.status = %s" in s:
                status = args.pop(0)
            if "ILIKE" in s:
                like = args[0]
                term = like[1:-1].replace("\\%", "%").replace("\\_", "_").replace("\\\\", "\\").lower()
            out = []
            for t in db.threads:
                if t.get("last_message_at") is None or (status and t["status"] != status):
                    continue
                name = db.orgs.get(t["org_id"], "")
                admins = [u for u in db.users if u["org_id"] == t["org_id"] and u["role_id"] == 1]
                if term and not (term in name.lower() or any(term in (u["name"] or "").lower() or term in (u["email"] or "").lower() for u in admins)):
                    continue
                out.append({"thread_id": t["thread_id"], "org_id": t["org_id"], "status": t["status"], "last_message": t["last_message"],
                            "last_message_type": t["last_message_type"], "last_sender_type": t["last_sender_type"],
                            "last_message_at": t["last_message_at"], "org_name": name, "unread_count": db.unread(t["org_id"], "org")})
            return self._set(sorted(out, key=lambda r: r["last_message_at"], reverse=True))
        raise AssertionError(f"unexpected SQL: {s}")


class FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self, cursor_factory=None):
        return FakeCursor(self.db)

    def commit(self):
        self.db.commits += 1

    def rollback(self):
        self.db.rollbacks += 1


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


# ───────────────────────────────────────────────────────────────────────────── fixtures
@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    ops, users, pushes, uploads = [], [], [], []
    clock = {"t": datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc)}

    def now():
        clock["t"] += timedelta(seconds=1)
        return clock["t"]

    async def pub_ops(frame):
        ops.append(frame)
        return 1

    async def pub_users(ids, frame):
        users.append((list(ids), frame))
        return len(list(ids))

    def upload(data, filename, content_type):
        uploads.append({"bytes": len(data), "filename": filename, "content_type": content_type})
        return f"{S3_PREFIX}abc_{filename}", f"https://s3.test/signed/{filename}?sig=1"

    monkeypatch.setattr(live, "get_db_connection", lambda: FakeConn(db))
    monkeypatch.setattr(live, "return_connection", lambda c: None)
    monkeypatch.setattr(live, "get_utc_now", now)
    monkeypatch.setattr(live.realtime, "publish_to_operators", pub_ops)
    monkeypatch.setattr(live.realtime, "publish_to_users", pub_users)
    monkeypatch.setattr(live, "_fire_push", lambda ids, body, data, image_url=None: pushes.append({"ids": list(ids), "body": body, "data": data, **({"image": image_url} if image_url else {})}))
    monkeypatch.setattr("ml.s3.upload_chat_file", upload)
    monkeypatch.setattr("ml.s3.get_chat_file_presigned_url", lambda key, *a, **k: f"https://s3.test/signed/{key.split('_', 1)[1]}?sig=2")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "0")
    monkeypatch.delenv("OPERATOR_ENFORCE", raising=False)
    app = FastAPI()
    app.include_router(live_chat.router)
    app.include_router(gen_live_chat.router)
    client = TestClient(app)
    yield type("Env", (), {"db": db, "ops": ops, "users": users, "pushes": pushes, "uploads": uploads, "client": client})()
    manager.active_connections.clear()
    operator_auth.set_lookup(None)
    rate_limit.reset()


def recv(ws, kind="json", timeout=5.0):
    """A socket read that FAILS instead of hanging forever when the frame never arrives."""
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(ws.receive_json if kind == "json" else ws.receive_text).result(timeout)
    except concurrent.futures.TimeoutError:
        pytest.fail(f"no websocket {kind} frame within {timeout} s")
    finally:
        pool.shutdown(wait=False)


def org_send(env, text="Hello support", user=ADMIN, org=ORG):
    return env.client.post("/live-chat/send", json={"org_id": org, "user_id": user, "message": text})


def reply(env, text="Hi, how can we help?", org=ORG, headers=OP):
    return env.client.post(f"/gen-dashboard/live-chat/threads/{org}/messages", json={"message": text}, headers=headers)


def history(env, user=ADMIN, org=ORG):
    return env.client.get(f"/live-chat/messages/{org}", params={"user_id": user})


# ───────────────────────────────────────────────────────────────────────────── org (app) side
class TestOrgSends:
    def test_first_message_creates_the_thread_lazily_and_notifies(self, env):
        assert history(env).json() == {"success": True, "status": None, "unread": 0, "messages": []}
        assert env.db.threads == []                                    # reading never creates a thread
        r = org_send(env, "  Hello support  ")
        assert r.status_code == 200
        m = r.json()
        assert set(m) == MESSAGE_KEYS
        assert (m["message"], m["message_type"], m["sender_type"], m["sender_id"], m["sender_name"]) == ("Hello support", "text", "org", ADMIN, "Anita Desai")
        assert (m["is_read"], m["is_delivered"], m["file_url"]) == (False, True, None) and m["timestamp"].endswith("Z")
        t = env.db.thread(ORG)
        assert len(env.db.threads) == 1 and t["status"] == "open" and t["last_message"] == "Hello support" and t["last_sender_type"] == "org"
        assert env.db.commits >= 1
        # operators: the full message + org name + thread status; org admins: the message with org_id (both admins of the org)
        assert [f["type"] for f in env.ops] == ["live_chat_message"]
        assert env.ops[0]["data"] == {**m, "org_id": ORG, "org_name": "Acme Traders", "thread_status": "open"}
        ids, frame = env.users[0]
        assert sorted(ids) == sorted([ADMIN, ADMIN2]) and frame == {"type": "live_chat_message", "data": {**m, "org_id": ORG}}
        assert env.pushes == []                                        # no FCM when the org writes

    def test_second_message_reuses_the_thread(self, env):
        org_send(env, "one")
        org_send(env, "two")
        assert len(env.db.threads) == 1 and env.db.thread(ORG)["last_message"] == "two"
        body = history(env).json()
        assert [x["message"] for x in body["messages"]] == ["one", "two"]          # ascending
        assert env.client.get("/gen-dashboard/live-chat/unread-count").json() == {"success": True, "unread_threads": 1, "unread_messages": 2}

    def test_text_validation(self, env):
        for bad in ("", "   ", "\n\t"):
            assert org_send(env, bad).status_code == 400
        assert org_send(env, "x" * 4001).status_code == 400
        assert org_send(env, "x" * 4000).status_code == 200
        assert len(env.db.messages) == 1 and env.db.thread(ORG) is not None

    def test_a_closed_thread_is_reopened_by_the_next_org_message(self, env):
        org_send(env)
        r = env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "closed"})
        assert r.json() == {"success": True, "status": "closed"} and env.db.thread(ORG)["status"] == "closed"
        assert history(env).json()["status"] == "closed"
        org_send(env, "still there?")
        assert env.db.thread(ORG)["status"] == "open" and history(env).json()["status"] == "open"
        assert env.ops[-1]["data"]["thread_status"] == "open"

    def test_history_returns_the_last_500_oldest_first(self, env):
        org_send(env, "first")
        thread_id = env.db.thread(ORG)["thread_id"]
        for i in range(2, 4):
            org_send(env, f"m{i}")
        history(env)
        sql, params = [x for x in env.db.log if "ORDER BY id DESC LIMIT" in x[0]][-1]
        assert params == (thread_id, 500)
        assert [m["message"] for m in history(env).json()["messages"]] == ["first", "m2", "m3"]


class TestRefusals:
    @pytest.mark.parametrize("user, org", [(STAFF, ORG), (MANAGER, ORG), (OTHER_ADMIN, ORG), ("ghost", ORG), ("adm-gone", ORG3)])
    def test_only_an_active_admin_of_the_org_may_use_the_app_routes(self, env, user, org):
        org_send(env)                                                  # a real thread exists
        before = len(env.db.messages)
        assert history(env, user=user, org=org).status_code == 403
        assert org_send(env, "hack", user=user, org=org).status_code == 403
        assert env.client.put("/live-chat/mark-read", json={"org_id": org, "user_id": user}).status_code == 403
        assert env.client.get(f"/live-chat/unread/{org}", params={"user_id": user}).status_code == 403
        r = env.client.post("/live-chat/send-file", data={"org_id": org, "user_id": user}, files={"file": ("a.png", b"123", "image/png")})
        assert r.status_code == 403 and env.uploads == []              # refused before the bytes are read or stored
        assert len(env.db.messages) == before

    def test_the_role_rule_keeps_managers_and_staff_out_when_auth_is_required(self):
        def ident(role):
            return P.Identity(user_id="u", org_id="o", role_id=role)
        for method, path in (("GET", "/live-chat/messages/o1"), ("POST", "/live-chat/send"), ("POST", "/live-chat/send-file"),
                             ("PUT", "/live-chat/mark-read"), ("GET", "/live-chat/unread/o1")):
            assert P.evaluate(method, path, ident(1)).allowed, path
            assert not P.evaluate(method, path, ident(2)).allowed and not P.evaluate(method, path, ident(3)).allowed, path
            assert P.classify(method, path) == P.AUTHENTICATED         # no plan gate, bearer token routes

    def test_operator_routes_404_without_a_thread_and_never_create_one(self, env):
        o = "/gen-dashboard/live-chat/threads/org-9"
        assert env.client.get(o + "/messages").status_code == 404
        assert reply(env, org="org-9").status_code == 404
        assert env.client.put(o + "/read").status_code == 404
        assert env.client.put(o + "/status", json={"status": "closed"}).status_code == 404
        r = env.client.post(o + "/messages/file", files={"file": ("a.pdf", b"%PDF-1", "application/pdf")}, headers=OP)
        assert r.status_code == 404 and env.uploads == []
        assert env.db.threads == [] and env.db.messages == []
        assert reply(env, "", org=ORG).status_code == 400              # validation comes first, nothing is created either way

    def test_bad_operator_inputs(self, env):
        org_send(env)
        assert reply(env, "   ").status_code == 400
        assert reply(env, "x" * 4001).status_code == 400
        assert env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "archived"}).status_code == 400
        assert env.client.get("/gen-dashboard/live-chat/threads", params={"status": "weird"}).status_code == 400


# ───────────────────────────────────────────────────────────────────────────── operator (console) side
class TestOperatorReplies:
    def test_reply_notifies_the_org_admins_pushes_and_counts_unread(self, env):
        org_send(env, "Hello")
        env.ops.clear(), env.users.clear()
        r = reply(env, "We are on it")
        assert r.status_code == 200
        m = r.json()
        assert set(m) == MESSAGE_KEYS
        assert (m["sender_type"], m["sender_id"], m["sender_name"], m["message"], m["is_read"]) == ("support", AGENT, "Priya", "We are on it", False)
        assert env.ops[0]["type"] == "live_chat_message" and env.ops[0]["data"]["org_name"] == "Acme Traders"      # other consoles update
        ids, frame = env.users[0]
        assert sorted(ids) == sorted([ADMIN, ADMIN2]) and frame == {"type": "live_chat_message", "data": {**m, "org_id": ORG}}
        assert env.pushes == [{"ids": [ADMIN, ADMIN2], "body": "We are on it",
                               "data": {"type": "live_chat_message", "org_id": ORG, "message_id": m["message_id"]}}]
        body = history(env).json()
        assert body["unread"] == 1 and [x["sender_type"] for x in body["messages"]] == ["org", "support"]
        assert env.client.get(f"/live-chat/unread/{ORG}", params={"user_id": ADMIN}).json() == {"success": True, "unread": 1}
        t = env.db.thread(ORG)
        assert t["last_sender_type"] == "support" and t["last_message"] == "We are on it" and t["status"] == "open"

    def test_operator_identity_comes_from_x_admin_id(self, env):
        org_send(env)
        assert reply(env, "a", headers={"x-admin-id": "9000000009"}).json()["sender_name"] == "Priya"      # by phone
        assert reply(env, "b", headers={"x-admin-id": "Newbie@Clockyn.com"}).json()["sender_name"] == "Newbie"   # unknown: e-mail local part
        anon = reply(env, "c", headers={}).json()
        assert (anon["sender_id"], anon["sender_name"]) == ("support", "Clockyn Support")                   # operator key only
        assert reply(env, "d", headers={"x-admin-id": "AGENT@clockyn.com"}).json()["sender_id"] == AGENT       # e-mail is normalised

    def test_a_reply_to_a_closed_thread_does_not_reopen_it(self, env):
        org_send(env)
        env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "closed"})
        assert reply(env).status_code == 200 and env.db.thread(ORG)["status"] == "closed"


class TestReadReceipts:
    def test_operator_mark_read_ticks_the_org_messages(self, env):
        org_send(env, "1"), org_send(env, "2")
        env.users.clear()
        r = env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/read")
        assert r.json() == {"success": True, "marked": 2}
        assert all(m["is_read"] for m in env.client.get(f"/gen-dashboard/live-chat/threads/{ORG}/messages").json()["messages"])
        assert env.users == [([ADMIN, ADMIN2], {"type": "live_chat_read", "data": {"org_id": ORG, "reader": "support"}})]
        assert env.client.get("/gen-dashboard/live-chat/unread-count").json()["unread_messages"] == 0
        env.users.clear()
        assert env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/read").json()["marked"] == 0
        assert env.users == []                                         # nothing changed: no frame

    def test_org_mark_read_ticks_the_support_messages_and_tells_the_consoles(self, env):
        org_send(env)
        reply(env, "r1"), reply(env, "r2")
        env.ops.clear()
        r = env.client.put("/live-chat/mark-read", json={"org_id": ORG, "user_id": ADMIN})
        assert r.json() == {"success": True, "marked": 2}
        assert env.ops == [{"type": "live_chat_read", "data": {"org_id": ORG, "reader": "org"}}]
        body = history(env).json()
        assert body["unread"] == 0 and [m["is_read"] for m in body["messages"]] == [False, True, True]   # only support's messages
        env.ops.clear()
        assert env.client.put("/live-chat/mark-read", json={"org_id": ORG, "user_id": ADMIN}).json()["marked"] == 0
        assert env.ops == []

    def test_each_side_only_reads_the_other_sides_messages(self, env):
        org_send(env, "mine")
        env.client.put("/live-chat/mark-read", json={"org_id": ORG, "user_id": ADMIN})
        assert env.db.unread(ORG, "org") == 1                          # org marking read did not touch its own message


class TestStatusAndList:
    def test_status_change_notifies_the_consoles(self, env):
        org_send(env)
        env.ops.clear()
        assert env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "closed"}).status_code == 200
        assert env.ops == [{"type": "live_chat_status", "data": {"org_id": ORG, "status": "closed"}}]
        assert env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "OPEN"}).json()["status"] == "open"

    def _three_threads(self, env):
        org_send(env, "from acme")
        org_send(env, "from bharat", user=OTHER_ADMIN, org=ORG2)
        org_send(env, "from zenith", user="adm-3", org=ORG3)
        reply(env, "to bharat", org=ORG2)
        env.client.put(f"/gen-dashboard/live-chat/threads/{ORG3}/status", json={"status": "closed"})

    def test_list_is_newest_first_with_admin_contact_and_unread(self, env):
        self._three_threads(env)
        rows = env.client.get("/gen-dashboard/live-chat/threads").json()
        assert [r["org_id"] for r in rows] == [ORG2, ORG3, ORG]        # newest activity first (the reply to org-2 is the latest)
        by = {r["org_id"]: r for r in rows}
        assert set(by[ORG]) == {"thread_id", "org_id", "org_name", "admin_name", "admin_email", "admin_mobile", "status", "last_message",
                                "last_message_type", "last_sender_type", "last_message_at", "unread_count"}
        assert (by[ORG]["org_name"], by[ORG]["admin_name"], by[ORG]["admin_email"]) == ("Acme Traders", "Anita Desai", "anita@acme.in")
        assert by[ORG]["unread_count"] == 1 and by[ORG2]["unread_count"] == 1                 # org2: its own message is still unread
        assert by[ORG2]["last_sender_type"] == "support" and by[ORG2]["last_message"] == "to bharat"
        assert by[ORG3]["status"] == "closed"

    def test_status_filter_and_search(self, env):
        self._three_threads(env)
        ids = lambda **q: sorted(r["org_id"] for r in env.client.get("/gen-dashboard/live-chat/threads", params=q).json())   # noqa: E731
        assert ids(status="open") == [ORG, ORG2] and ids(status="closed") == [ORG3] and ids(status="all") == [ORG, ORG2, ORG3]
        assert ids(q="bharat") == [ORG2]                      # org name, case-insensitive
        assert ids(q="CHANDAN") == [ORG3]                     # admin name
        assert ids(q="ravi@acme") == [ORG]                    # admin e-mail (the org's second admin)
        assert ids(q="nobody") == [] and ids(q="100%_") == []
        assert ids(q="zenith", status="open") == []

    def test_search_text_is_parameterised_and_wildcards_are_escaped(self, env):
        self._three_threads(env)
        env.client.get("/gen-dashboard/live-chat/threads", params={"q": "x'; DROP TABLE users;--"})
        sql, params = [x for x in env.db.log if x[0].startswith("SELECT t.thread_id")][-1]
        assert "DROP" not in sql and "ILIKE %s" in sql and params[0].startswith("%x'; DROP")
        env.client.get("/gen-dashboard/live-chat/threads", params={"q": "50%_off"})
        assert [x for x in env.db.log if x[0].startswith("SELECT t.thread_id")][-1][1][0] == "%50\\%\\_off%"

    def test_unread_count_is_one_aggregate(self, env):
        self._three_threads(env)
        n = len(env.db.log)
        assert env.client.get("/gen-dashboard/live-chat/unread-count").json() == {"success": True, "unread_threads": 3, "unread_messages": 3}
        assert len(env.db.log) - n == 1


class TestAttachments:
    def test_org_image_and_file_uploads(self, env):
        r = env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN},
                            files={"file": ("shot.png", b"\x89PNG....", "image/png")})
        assert r.status_code == 200
        m = r.json()
        assert set(m) == MESSAGE_KEYS and (m["message_type"], m["message"], m["file_name"], m["content_type"]) == ("image", "shot.png", "shot.png", "image/png")
        assert m["file_url"] == "https://s3.test/signed/shot.png?sig=1" and m["sender_type"] == "org"
        assert env.db.messages[0]["file_key"] == f"{S3_PREFIX}abc_shot.png"              # only the key is stored
        assert env.db.thread(ORG)["last_message_type"] == "image" and env.db.thread(ORG)["last_message"] == "shot.png"
        assert env.pushes == []
        r = env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN}, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
        assert r.json()["message_type"] == "file"

    def test_operator_file_reply_pushes_an_attachment_text(self, env):
        org_send(env)
        r = env.client.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages/file", headers=OP,
                            files={"file": ("guide.pdf", b"%PDF-1.4 x", "application/pdf")})
        assert r.status_code == 200 and r.json()["message_type"] == "file" and r.json()["sender_name"] == "Priya"
        assert env.pushes[-1]["body"] == "\U0001f4ce Sent an attachment" and env.pushes[-1]["data"]["type"] == "live_chat_message"
        assert env.users[-1][1]["data"]["file_url"] == "https://s3.test/signed/guide.pdf?sig=1"

    def test_operator_photo_reply_pushes_the_picture(self, env):
        org_send(env)
        r = env.client.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages/file", headers=OP,
                            files={"file": ("screen.png", b"123", "image/png")})
        assert r.status_code == 200 and r.json()["message_type"] == "image"
        assert env.pushes[-1].get("image") == "https://s3.test/signed/screen.png?sig=1"
        assert env.pushes[-1]["body"] == "\U0001f4f7 Sent a photo" and env.pushes[-1]["data"]["type"] == "live_chat_message"

    def test_operator_non_picture_reply_has_no_notification_picture(self, env):
        org_send(env)
        env.client.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages/file", headers=OP, files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
        env.client.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages/file", headers=OP, files={"file": ("p.heic", b"123", "image/heic")})
        assert [p.get("image") for p in env.pushes[-2:]] == [None, None]          # PDFs and HEIC cannot be drawn by the notification shade
        assert env.pushes[-1]["body"] == "\U0001f4ce Sent an attachment"

    def test_the_s3_key_never_leaves_the_server(self, env):
        env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN}, files={"file": ("a.png", b"123", "image/png")})
        env.client.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages/file", headers=OP, files={"file": ("b.pdf", b"%PDF-", "application/pdf")})
        bodies = [history(env).text, env.client.get(f"/gen-dashboard/live-chat/threads/{ORG}/messages").text,
                  env.client.get("/gen-dashboard/live-chat/threads").text, json.dumps(env.ops), json.dumps(env.users)]
        for text in bodies:
            assert S3_PREFIX not in text and "file_key" not in text
        urls = [m["file_url"] for m in history(env).json()["messages"]]
        assert urls == ["https://s3.test/signed/a.png?sig=2", "https://s3.test/signed/b.pdf?sig=2"]      # history presigns from the key

    def test_oversize_and_empty_uploads_are_refused(self, env, monkeypatch):
        org_send(env)
        monkeypatch.setenv("CHAT_UPLOAD_MAX_MB", "0.001")
        r = env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN}, files={"file": ("big.png", b"x" * 4096, "image/png")})
        assert r.status_code == 413
        monkeypatch.delenv("CHAT_UPLOAD_MAX_MB")
        r = env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN}, files={"file": ("e.png", b"", "image/png")})
        assert r.status_code == 400 and env.uploads == [] and len(env.db.messages) == 1

    def test_a_failed_storage_upload_is_a_generic_500_and_stores_nothing(self, env, monkeypatch):
        def boom(*a):
            raise RuntimeError("S3 secret detail")
        monkeypatch.setattr("ml.s3.upload_chat_file", boom)
        r = env.client.post("/live-chat/send-file", data={"org_id": ORG, "user_id": ADMIN}, files={"file": ("a.png", b"123", "image/png")})
        assert r.status_code == 500 and "S3 secret" not in r.text and env.db.messages == [] and env.db.threads == []


class TestRateLimit:
    def test_sending_is_limited_per_user(self, env, monkeypatch):
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        monkeypatch.setenv("RATE_LIMIT_LIVE_CHAT_SEND", "2/60")
        rate_limit.reset()
        assert org_send(env, "1").status_code == 200 and org_send(env, "2").status_code == 200
        assert org_send(env, "3").status_code == 429
        assert org_send(env, "other admin", user=OTHER_ADMIN, org=ORG2).status_code == 200      # per user, not global
        assert history(env).status_code == 200                                                    # reading is not limited


# ───────────────────────────────────────────────────────────────────────────── realtime (real socket registry)
class TestRealtime:
    def test_org_admin_sockets_receive_support_replies(self, env, monkeypatch):
        monkeypatch.setattr(live.realtime, "publish_to_operators", ORIG_PUBLISH_OPERATORS)
        monkeypatch.setattr(live.realtime, "publish_to_users", ORIG_PUBLISH_USERS)
        s1, s2, other = FakeSocket(), FakeSocket(), FakeSocket()
        manager.active_connections[ADMIN] = [s1]
        manager.active_connections[ADMIN2] = [s2]
        manager.active_connections[OTHER_ADMIN] = [other]
        org_send(env)
        r = reply(env, "hello admins")
        assert [f["type"] for f in s1.sent] == ["live_chat_message", "live_chat_message"] and s1.sent[1]["data"]["message_id"] == r.json()["message_id"]
        assert s2.sent[1] == s1.sent[1] and s1.sent[1]["data"]["org_id"] == ORG
        assert other.sent == []                                        # another organisation hears nothing

    def test_operator_socket_accepts_pings_and_receives_org_messages(self, env, monkeypatch):
        monkeypatch.setattr(live.realtime, "publish_to_operators", ORIG_PUBLISH_OPERATORS)
        monkeypatch.setattr(live.realtime, "publish_to_users", ORIG_PUBLISH_USERS)
        with env.client.websocket_connect(f"/ws/support-ops?admin_id={AGENT}") as ws, \
                env.client.websocket_connect("/ws/support-ops") as ws_other:
            assert manager.is_online("support-ops")
            ws.send_text("ping")
            assert recv(ws, "text") == "pong"
            org_send(env, "live!")
            for sock in (ws, ws_other):                                # every open console gets it
                frame = recv(sock)
                assert frame["type"] == "live_chat_message" and frame["data"]["message"] == "live!"
                assert frame["data"]["org_name"] == "Acme Traders" and frame["data"]["thread_status"] == "open"
            env.client.put("/live-chat/mark-read", json={"org_id": ORG, "user_id": ADMIN})        # nothing unread: no frame
            env.client.put(f"/gen-dashboard/live-chat/threads/{ORG}/status", json={"status": "closed"})
            assert recv(ws) == {"type": "live_chat_status", "data": {"org_id": ORG, "status": "closed"}}
        assert not manager.is_online("support-ops")                    # closing removes exactly these sockets

    def test_operator_socket_is_open_like_other_operator_routes_when_not_enforced(self, env):
        with env.client.websocket_connect("/ws/support-ops?admin_id=whoever") as ws:
            ws.send_text("ping")
            assert recv(ws, "text") == "pong"

    def test_unknown_operators_are_refused_when_enforced(self, env, monkeypatch):
        monkeypatch.setenv("OPERATOR_ENFORCE", "1")
        operator_auth.set_lookup(lambda who: 1 if who == AGENT else None)
        for url in ("/ws/support-ops", "/ws/support-ops?admin_id=intruder@evil.com", "/ws/support-ops?admin_id="):
            with pytest.raises(WebSocketDisconnect) as e:
                with env.client.websocket_connect(url):
                    pass
            assert e.value.code == 4403
        assert not manager.is_online("support-ops")
        with env.client.websocket_connect(f"/ws/support-ops?admin_id={AGENT}") as ws:
            ws.send_text("ping")
            assert recv(ws, "text") == "pong"

    def test_a_dead_socket_never_fails_the_send(self, env, monkeypatch):
        class Dead:
            async def send_json(self, payload):
                raise RuntimeError("gone")
        monkeypatch.setattr(live.realtime, "publish_to_operators", ORIG_PUBLISH_OPERATORS)
        monkeypatch.setattr(live.realtime, "publish_to_users", ORIG_PUBLISH_USERS)
        manager.active_connections["support-ops"] = [Dead()]
        manager.active_connections[ADMIN] = [Dead()]
        assert org_send(env).status_code == 200 and not manager.is_online("support-ops")

    def test_a_realtime_error_never_fails_the_send(self, env, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("redis down")
        monkeypatch.setattr(live.realtime, "publish_to_operators", boom)
        assert org_send(env).status_code == 200 and len(env.db.messages) == 1


class _UndefinedTable(Exception):
    pgcode = "42P01"


class TestTablesNotCreatedYet:
    """A deployment that runs before the two tables exist must not produce a stream of 500s (the web console polls
    unread-count on every page, and the deployment rolls back on its error rate)."""

    @pytest.fixture
    def no_tables(self, env, monkeypatch, caplog):
        real = FakeCursor.execute

        def execute(self, sql, params=None):
            if "live_chat_" in sql:
                raise _UndefinedTable('relation "live_chat_thread" does not exist')
            return real(self, sql, params)

        monkeypatch.setattr(FakeCursor, "execute", execute)
        monkeypatch.setattr(live, "_missing_tables_logged", False)
        caplog.set_level("ERROR")
        return env

    def test_reads_answer_empty_200s(self, no_tables):
        c = no_tables.client
        r = c.get("/gen-dashboard/live-chat/unread-count", headers=OP)
        assert r.status_code == 200 and r.json() == {"success": True, "unread_threads": 0, "unread_messages": 0, "setup_required": True}
        assert c.get("/gen-dashboard/live-chat/threads", headers=OP).json() == []
        r = c.get(f"/live-chat/messages/{ORG}", params={"user_id": ADMIN})
        assert r.status_code == 200 and r.json()["messages"] == [] and r.json()["setup_required"] is True
        r = c.get(f"/live-chat/unread/{ORG}", params={"user_id": ADMIN})
        assert r.status_code == 200 and r.json()["unread"] == 0
        assert c.get(f"/gen-dashboard/live-chat/threads/{ORG}/messages", headers=OP).status_code == 404

    def test_writes_answer_a_clear_503_not_a_500(self, no_tables):
        c = no_tables.client
        r = c.post("/live-chat/send", json={"org_id": ORG, "user_id": ADMIN, "message": "hi"})
        assert r.status_code == 503 and r.json()["detail"] == "Live chat is not set up yet"
        assert c.post(f"/gen-dashboard/live-chat/threads/{ORG}/messages", json={"message": "x"}, headers=OP).status_code == 503

    def test_the_cause_is_logged_once_not_on_every_poll(self, no_tables, caplog):
        for _ in range(5):
            no_tables.client.get("/gen-dashboard/live-chat/unread-count", headers=OP)
        hits = [r for r in caplog.records if "do not exist in this schema" in r.getMessage()]
        assert len(hits) == 1 and "CREATE: Live Chat" in hits[0].getMessage()


def test_fire_push_goes_through_the_push_service(monkeypatch):
    calls = []

    async def fake(ids, title, body, data):
        calls.append((ids, title, body, data))
        return 1

    monkeypatch.setattr(live.push_service, "send_push_to_users", fake)

    async def run():
        live._fire_push(["a", "b"], "hi", {"type": "live_chat_message"})
        live._fire_push([], "nobody", {})                               # no admins: nothing is sent
        await asyncio.sleep(0)

    asyncio.run(run())
    assert calls == [(["a", "b"], "Clockyn Support", "hi", {"type": "live_chat_message"})]


# ───────────────────────────────────────────────────────────────────────────── route table / policy coverage
def test_the_live_chat_routes_are_exactly_these_and_are_covered_by_the_auth_policy():
    from test_route_table import mounted_routes
    routes = {(f, m, p) for f, m, p in mounted_routes() if f in ("live_chat.py", "gen_live_chat.py")}
    base = "/gen-dashboard/live-chat"
    assert routes == {
        ("live_chat.py", "GET", "/live-chat/messages/{}"), ("live_chat.py", "GET", "/live-chat/unread/{}"),
        ("live_chat.py", "POST", "/live-chat/send"), ("live_chat.py", "POST", "/live-chat/send-file"),
        ("live_chat.py", "PUT", "/live-chat/mark-read"),
        ("gen_live_chat.py", "GET", base + "/threads"), ("gen_live_chat.py", "GET", base + "/unread-count"),
        ("gen_live_chat.py", "GET", base + "/threads/{}/messages"), ("gen_live_chat.py", "POST", base + "/threads/{}/messages"),
        ("gen_live_chat.py", "POST", base + "/threads/{}/messages/file"), ("gen_live_chat.py", "PUT", base + "/threads/{}/read"),
        ("gen_live_chat.py", "PUT", base + "/threads/{}/status"), ("gen_live_chat.py", "WEBSOCKET", "/ws/support-ops"),
    }
    for f, m, p in routes:
        method = "GET" if m == "WEBSOCKET" else m
        expected = P.OPERATOR if f == "gen_live_chat.py" and m != "WEBSOCKET" else P.AUTHENTICATED
        assert P.classify(method, p) == expected, (m, p)
        if f == "live_chat.py":
            rule = P.role_rule_for(m, p)
            assert rule is not None and rule.roles == P.ADMIN_ONLY, (m, p)         # app routes: administrators only


def test_operator_routes_refuse_without_a_credential_when_enforced(env, monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    operator_auth.set_lookup(lambda who: 1 if who == AGENT else None)
    org_send(env)
    base = "/gen-dashboard/live-chat"
    calls = [("get", base + "/threads", {}), ("get", base + "/unread-count", {}), ("get", f"{base}/threads/{ORG}/messages", {}),
             ("post", f"{base}/threads/{ORG}/messages", {"json": {"message": "hi"}}), ("put", f"{base}/threads/{ORG}/read", {}),
             ("put", f"{base}/threads/{ORG}/status", {"json": {"status": "closed"}})]
    for method, url, kw in calls:
        assert getattr(env.client, method)(url, **kw).status_code == 403, url                       # no credential
        assert getattr(env.client, method)(url, headers={"x-admin-id": "intruder@evil.com"}, **kw).status_code == 403, url
    assert len(env.db.messages) == 1 and env.db.thread(ORG)["status"] == "open"                      # nothing was changed
    ok = env.client.get(base + "/threads", headers=OP)
    assert ok.status_code == 200 and len(ok.json()) == 1
    assert reply(env, "now allowed").status_code == 200
    assert history(env, user=ADMIN).status_code == 200                                               # app routes are not operator routes


def test_the_operator_socket_checks_the_operator_before_it_accepts():
    import ast
    tree = ast.parse((ROOT / "router" / "gen_live_chat.py").read_text(encoding="utf-8-sig"))
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "support_ops_socket")
    calls = [(c.lineno, getattr(c.func, "attr", getattr(c.func, "id", None))) for c in ast.walk(handler) if isinstance(c, ast.Call)]
    enforced = min(l for l, n in calls if n == "enforced")
    accept = min(l for l, n in calls if n in ("accept", "connect"))
    assert enforced < accept, "the socket is accepted before the operator is checked"
