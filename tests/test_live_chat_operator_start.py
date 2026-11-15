"""DB-free tests: the Clockyn support operator can START a conversation with an organisation from the console.

Before: support could only answer (an organisation without a thread was a 404). Now:
  * GET  /gen-dashboard/live-chat/threads/{org}/messages for an organisation without a thread answers 200 `exists: false` and WRITES NOTHING;
  * the first POST (text or file) creates the thread together with the message in ONE transaction, publishes to the operators'
    sockets and the org admins' app sockets and pushes to the org admins;
  * the organisation must exist and not be deleted (404 "Organization not found") and have an active admin (409), checked before an
    upload is read; closing / marking read a thread that does not exist stays a 404.
Uses the stateful fake of tests/test_live_chat.py.
Run: python -m pytest tests/test_live_chat_operator_start.py -q
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import live_chat_functions as live  # noqa: E402
from test_live_chat import (  # noqa: E402,F401  (the fake, its fixture and helpers)
    ADMIN, MESSAGE_KEYS, OP, ORG, ORG2, OTHER_ADMIN, env, history, org_send, reply,
)

T = "/gen-dashboard/live-chat/threads"
NO_ADMIN = "This organization has no active admin to chat with"


def start_file(env, org=ORG2, name="guide.pdf", data=b"%PDF-1.4 x", ctype="application/pdf"):
    return env.client.post(f"{T}/{org}/messages/file", headers=OP, files={"file": (name, data, ctype)})


def writes(env):
    return [s for s, _ in env.db.log if s.startswith(("INSERT", "UPDATE", "DELETE"))]


def add_org_without_admin(env, org="org-4", name="Quiet Co"):
    env.db.orgs[org] = name
    env.db.users.append({"id": 90, "user_id": "adm-off", "org_id": org, "name": "Off", "role_id": 1, "status": 0,
                         "email": "off@quiet.in", "mobile_number": "9800000090"})
    return org


# ═════════════════════════════════════════════════════════════════════════ opening a chat writes nothing
class TestOpeningAnEmptyChat:
    def test_an_organisation_without_a_thread_opens_empty_and_nothing_is_written(self, env):
        r = env.client.get(f"{T}/{ORG2}/messages", headers=OP)
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True and body["messages"] == []
        t = body["thread"]
        assert (t["thread_id"], t["org_id"], t["org_name"], t["status"], t["exists"]) == (None, ORG2, "Bharat Logistics", "open", False)
        assert (t["unread_count"], t["last_message"], t["last_message_at"]) == (0, None, None)
        assert (t["admin_name"], t["admin_email"]) == ("Bela Shah", "bela@bharat.in")
        assert env.db.threads == [] and env.db.messages == [] and writes(env) == [] and env.db.commits == 0
        assert env.ops == [] and env.users == [] and env.pushes == []

    def test_an_existing_thread_says_exists_true(self, env):
        org_send(env)
        t = env.client.get(f"{T}/{ORG}/messages", headers=OP).json()["thread"]
        assert t["exists"] is True and t["thread_id"] and t["status"] == "open"
        assert {"org_id", "org_name", "admin_name", "admin_email", "admin_mobile"} <= set(t)

    def test_an_unknown_organisation_is_a_404(self, env):
        r = env.client.get(f"{T}/org-nope/messages", headers=OP)
        assert r.status_code == 404 and r.json()["detail"] == "Organization not found"

    def test_a_deleted_organisation_is_a_404_until_it_has_a_thread(self, env):
        env.db.org_status[ORG2] = 2
        r = env.client.get(f"{T}/{ORG2}/messages", headers=OP)
        assert r.status_code == 404 and r.json()["detail"] == "Organization not found"

    def test_the_history_of_an_existing_thread_of_a_deleted_organisation_is_still_readable(self, env):
        org_send(env)
        env.db.org_status[ORG] = 2
        assert env.client.get(f"{T}/{ORG}/messages", headers=OP).status_code == 200      # support can still read what was said


# ═════════════════════════════════════════════════════════════════════════ the first message
class TestFirstTextMessage:
    def test_it_creates_the_thread_and_the_message_in_one_transaction(self, env):
        r = reply(env, "  Hello from Clockyn  ", org=ORG2)
        assert r.status_code == 200
        m = r.json()
        assert set(m) == MESSAGE_KEYS
        assert (m["message"], m["message_type"], m["sender_type"], m["sender_name"], m["is_read"]) == (
            "Hello from Clockyn", "text", "support", "Priya", False)
        t = env.db.thread(ORG2)
        assert len(env.db.threads) == 1 and (t["status"], t["last_message"], t["last_sender_type"]) == ("open", "Hello from Clockyn", "support")
        assert len(env.db.messages) == 1 and env.db.messages[0]["thread_id"] == t["thread_id"] and env.db.messages[0]["org_id"] == ORG2
        assert env.db.commits == 1 and env.db.rollbacks == 0                      # ONE transaction, committed once
        order = [s for s in writes(env) if s.startswith("INSERT")]
        assert order[0].startswith("INSERT INTO live_chat_thread") and order[1].startswith("INSERT INTO live_chat_message")

    def test_it_publishes_to_the_consoles_and_the_org_admins_and_pushes(self, env):
        m = reply(env, "Hello from Clockyn", org=ORG2).json()
        assert [f["type"] for f in env.ops] == ["live_chat_message"]
        assert env.ops[0]["data"] == {**m, "org_id": ORG2, "org_name": "Bharat Logistics", "thread_status": "open"}
        ids, frame = env.users[0]
        assert ids == [OTHER_ADMIN] and frame == {"type": "live_chat_message", "data": {**m, "org_id": ORG2}}
        assert env.pushes == [{"ids": [OTHER_ADMIN], "body": "Hello from Clockyn",
                               "data": {"type": "live_chat_message", "org_id": ORG2, "message_id": m["message_id"]}}]

    def test_the_second_message_reuses_the_thread(self, env):
        reply(env, "one", org=ORG2)
        thread_id = env.db.thread(ORG2)["thread_id"]
        reply(env, "two", org=ORG2)
        assert len(env.db.threads) == 1 and env.db.thread(ORG2)["thread_id"] == thread_id
        assert [m["message"] for m in env.db.messages] == ["one", "two"] and env.db.thread(ORG2)["last_message"] == "two"
        assert sum(1 for s in writes(env) if s.startswith("INSERT INTO live_chat_thread")) == 1     # created once

    def test_text_is_validated_before_anything_is_created(self, env):
        for bad in ("", "   "):
            assert reply(env, bad, org=ORG2).status_code == 400
        assert reply(env, "x" * 4001, org=ORG2).status_code == 400
        assert env.db.threads == [] and env.db.messages == [] and writes(env) == []

    def test_an_unknown_or_deleted_organisation_is_a_404_and_nothing_is_written(self, env):
        r = reply(env, "hi", org="org-nope")
        assert r.status_code == 404 and r.json()["detail"] == "Organization not found"
        env.db.org_status[ORG2] = 2
        assert reply(env, "hi", org=ORG2).status_code == 404
        assert env.db.threads == [] and env.db.messages == [] and writes(env) == []
        assert env.ops == [] and env.users == [] and env.pushes == []

    def test_an_organisation_without_an_active_admin_is_a_409_and_nothing_is_written(self, env):
        org = add_org_without_admin(env)
        r = reply(env, "hi", org=org)
        assert r.status_code == 409 and r.json()["detail"] == NO_ADMIN
        assert env.db.threads == [] and env.db.messages == [] and writes(env) == [] and env.db.commits == 0
        assert env.ops == [] and env.users == [] and env.pushes == []

    def test_an_existing_closed_thread_is_not_reopened_or_recreated(self, env):
        org_send(env)
        env.client.put(f"{T}/{ORG}/status", json={"status": "closed"})
        thread_id = env.db.thread(ORG)["thread_id"]
        before = sum(1 for s in writes(env) if s.startswith("INSERT INTO live_chat_thread"))
        r = reply(env, "Anything else?", org=ORG)
        assert r.status_code == 200
        t = env.db.thread(ORG)
        assert (t["thread_id"], t["status"]) == (thread_id, "closed") and len(env.db.threads) == 1     # as before
        assert sum(1 for s in writes(env) if s.startswith("INSERT INTO live_chat_thread")) == before
        assert env.ops[-1]["data"]["thread_status"] == "closed"

    def test_two_operators_starting_at_once_end_up_in_one_thread(self, env, monkeypatch):
        # the organisation (or another operator) created the thread between this request's check and its insert
        org_send(env, org=ORG, user=ADMIN)
        env.client.put(f"{T}/{ORG}/status", json={"status": "closed"})
        real = live._thread_row
        calls = {"n": 0}

        def stale_first(cur, org_id):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(cur, org_id)

        monkeypatch.setattr(live, "_thread_row", stale_first)
        r = reply(env, "from the second operator", org=ORG)
        assert r.status_code == 200
        t = env.db.thread(ORG)
        assert len(env.db.threads) == 1 and t["status"] == "closed"                # the existing row came back untouched
        assert [m["message"] for m in env.db.messages] == ["Hello support", "from the second operator"]
        assert env.db.messages[1]["thread_id"] == t["thread_id"]


# ═════════════════════════════════════════════════════════════════════════ the first file message
class TestFirstFileMessage:
    def test_it_creates_the_thread_uploads_and_pushes_an_attachment_text(self, env):
        r = start_file(env)
        assert r.status_code == 200
        m = r.json()
        assert (m["message_type"], m["message"], m["sender_type"]) == ("file", "guide.pdf", "support")
        assert env.uploads == [{"bytes": 10, "filename": "guide.pdf", "content_type": "application/pdf"}]
        t = env.db.thread(ORG2)
        assert len(env.db.threads) == 1 and t["last_sender_type"] == "support" and t["last_message"] == "guide.pdf"
        assert env.db.commits == 1
        assert env.pushes[-1]["ids"] == [OTHER_ADMIN] and env.pushes[-1]["body"] == "\U0001f4ce Sent an attachment"
        assert env.users[-1][1]["data"]["file_url"] == "https://s3.test/signed/guide.pdf?sig=1"

    def test_a_photo_pushes_the_picture(self, env):
        r = start_file(env, name="screen.png", data=b"123", ctype="image/png")
        assert r.status_code == 200 and r.json()["message_type"] == "image"
        assert env.pushes[-1].get("image") == "https://s3.test/signed/screen.png?sig=1"

    def test_a_refused_start_never_reads_or_stores_the_upload(self, env):
        assert start_file(env, org="org-nope").status_code == 404
        env.db.org_status[ORG2] = 2
        assert start_file(env, org=ORG2).status_code == 404
        org = add_org_without_admin(env)
        r = start_file(env, org=org)
        assert r.status_code == 409 and r.json()["detail"] == NO_ADMIN
        assert env.uploads == [] and env.db.threads == [] and env.db.messages == [] and writes(env) == []


# ═════════════════════════════════════════════════════════════════════════ the rest of the system sees it
class TestEveryoneSeesTheStartedConversation:
    def test_the_org_admin_gets_the_message_and_one_unread(self, env):
        m = reply(env, "Welcome to Clockyn", org=ORG2).json()
        body = env.client.get(f"/live-chat/messages/{ORG2}", params={"user_id": OTHER_ADMIN}).json()
        assert body["status"] == "open" and body["unread"] == 1
        assert [(x["message_id"], x["sender_type"], x["message"]) for x in body["messages"]] == [(m["message_id"], "support", "Welcome to Clockyn")]
        assert env.client.get(f"/live-chat/unread/{ORG2}", params={"user_id": OTHER_ADMIN}).json()["unread"] == 1

    def test_another_organisations_admin_cannot_read_it(self, env):
        reply(env, "private", org=ORG2)
        assert env.client.get(f"/live-chat/messages/{ORG2}", params={"user_id": ADMIN}).status_code == 403

    def test_the_thread_is_in_the_operators_list_and_is_not_unread_for_them(self, env):
        reply(env, "Welcome to Clockyn", org=ORG2)
        rows = env.client.get("/gen-dashboard/live-chat/threads", headers=OP).json()
        row = next(r for r in rows if r["org_id"] == ORG2)
        assert (row["last_message"], row["last_sender_type"], row["unread_count"], row["status"]) == ("Welcome to Clockyn", "support", 0, "open")
        assert row["org_name"] == "Bharat Logistics" and row["last_message_at"].endswith("Z")
        assert env.client.get("/gen-dashboard/live-chat/unread-count", headers=OP).json() == {
            "success": True, "unread_threads": 0, "unread_messages": 0}              # support's own message is not work for support

    def test_a_reply_from_the_customer_then_counts_as_unread_for_support(self, env):
        reply(env, "Welcome", org=ORG2)
        org_send(env, "Thanks!", user=OTHER_ADMIN, org=ORG2)
        assert env.client.get("/gen-dashboard/live-chat/unread-count", headers=OP).json()["unread_messages"] == 1
        assert history(env, user=OTHER_ADMIN, org=ORG2).json()["unread"] == 1       # Welcome is still unread by the admin

    def test_a_thread_row_without_a_preview_is_listed_without_crashing(self, env):
        t = {"thread_id": "t-x", "org_id": ORG2, "status": "open", "last_message": None, "last_message_type": None,
             "last_sender_type": None, "last_message_at": datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)}
        env.db.threads.append(t)
        rows = env.client.get("/gen-dashboard/live-chat/threads", headers=OP).json()
        assert rows[0]["last_message"] is None and rows[0]["unread_count"] == 0


# ═════════════════════════════════════════════════════════════════════════ unchanged rules
class TestRulesThatDidNotChange:
    def test_closing_or_reading_a_thread_that_does_not_exist_is_still_a_404(self, env):
        r = env.client.put(f"{T}/{ORG2}/status", json={"status": "closed"})
        assert r.status_code == 404 and r.json()["detail"] == "No conversation for this organization"
        r = env.client.put(f"{T}/{ORG2}/read")
        assert r.status_code == 404 and r.json()["detail"] == "No conversation for this organization"
        assert env.db.threads == []

    def test_deleting_a_message_of_a_missing_thread_is_still_a_404(self, env):
        assert env.client.delete(f"{T}/{ORG2}/messages/nope").status_code == 404

    def test_the_operator_guards_are_unchanged(self, env, monkeypatch):
        from core_functions import operator_auth
        monkeypatch.setenv("OPERATOR_ENFORCE", "1")
        operator_auth.set_lookup(lambda phone: {"1": 1, "2": 2}.get(phone))
        assert env.client.get(f"{T}/{ORG2}/messages").status_code in (401, 403)
        assert env.client.post(f"{T}/{ORG2}/messages", json={"message": "hi"}).status_code in (401, 403)
        assert env.db.threads == []
        assert env.client.get(f"{T}/{ORG2}/messages", headers={"x-admin-id": "2"}).status_code == 200      # any operator may open it
