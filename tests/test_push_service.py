"""DB-free tests for services/push_service.py (FCM v1 sender, token cache, error handling, multi-device fan-out).
The HTTP POST (`_post_fcm`), the OAuth exchange (`_mint_access_token`) and the DB connection are faked.
Run: python -m pytest tests/test_push_service.py -q
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services import push_service as ps  # noqa: E402

FCM_ENV = ("FCM_PROJECT_ID", "FCM_PRIVATE_KEY_ID", "FCM_PRIVATE_KEY", "FCM_CLIENT_EMAIL", "FCM_CLIENT_ID",
           "FCM_SERVICE_ACCOUNT_PATH", "FCM_ANDROID_CHANNEL_ID")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in FCM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_PATH", str(ROOT / "no-such-service-account.json"))
    ps.invalidate_access_token()
    monkeypatch.setattr(ps, "_unconfigured_logged", False)
    yield
    ps.invalidate_access_token()


def configure(monkeypatch, project="proj-1", key_id="kid-1"):
    monkeypatch.setenv("FCM_PROJECT_ID", project)
    monkeypatch.setenv("FCM_PRIVATE_KEY_ID", key_id)
    monkeypatch.setenv("FCM_PRIVATE_KEY", '"-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n"')
    monkeypatch.setenv("FCM_CLIENT_EMAIL", "sa@proj-1.iam.gserviceaccount.com")
    monkeypatch.setenv("FCM_CLIENT_ID", "123")


class Fcm:
    """Fake FCM endpoint: scripted (status, body) answers, every message recorded."""

    def __init__(self, monkeypatch, answers=None, mint=None):
        mint_fn = mint
        self.answers = list(answers or [])
        self.posts = []
        self.mints = 0
        self.pruned = []

        async def post(project_id, access_token, message):
            self.posts.append((project_id, access_token, message))
            if self.answers:
                a = self.answers.pop(0)
                if callable(a):
                    return a(message)
                return a
            return 200, "{}"

        def fake_mint(creds):
            self.mints += 1
            return mint_fn(self.mints) if mint_fn else f"access-{self.mints}"

        monkeypatch.setattr(ps, "_post_fcm", post)
        monkeypatch.setattr(ps, "_mint_access_token", fake_mint)
        monkeypatch.setattr(ps, "prune_token", lambda tok: self.pruned.append(tok))


def err(status, code=None, message="boom", fields=None):
    detail = []
    if code:
        detail.append({"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code})
    if fields:
        detail.append({"@type": "type.googleapis.com/google.rpc.BadRequest",
                       "fieldViolations": [{"field": f, "description": "x"} for f in fields]})
    http = {"UNREGISTERED": 404, "NOT_FOUND": 404, "INVALID_ARGUMENT": 400, "UNAUTHENTICATED": 401}.get(status, 500)
    return http, json.dumps({"error": {"code": http, "message": message, "status": status, "details": detail}})


# ---------------------------------------------------------------------------- credentials
def test_credentials_from_env_unescape_the_key_and_strip_quotes(monkeypatch):
    configure(monkeypatch)
    creds = ps.get_fcm_credentials()
    assert creds["source"] == "env" and creds["project_id"] == "proj-1"
    key = creds["info"]["private_key"]
    assert key.startswith("-----BEGIN PRIVATE KEY-----\nABC\n") and "\\n" not in key and not key.startswith('"')
    assert creds["info"]["client_email"] == "sa@proj-1.iam.gserviceaccount.com"
    assert creds["info"]["private_key_id"] == "kid-1" and creds["info"]["client_id"] == "123"


def test_credentials_missing_or_partial_means_not_configured(monkeypatch):
    assert ps.get_fcm_credentials() is None and ps.is_configured() is False
    monkeypatch.setenv("FCM_PROJECT_ID", "p")          # key and e-mail missing
    assert ps.get_fcm_credentials() is None


def test_credentials_fall_back_to_the_service_account_file(monkeypatch, tmp_path):
    sa = tmp_path / "sa.json"
    sa.write_text(json.dumps({"project_id": "from-file"}), encoding="utf-8")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_PATH", str(sa))
    creds = ps.get_fcm_credentials()
    assert creds["source"] == "file" and creds["project_id"] == "from-file" and creds["path"] == str(sa)


# ---------------------------------------------------------------------------- OAuth token cache
def test_access_token_is_cached_for_55_minutes(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ps.time, "monotonic", lambda: clock["t"])

    assert ps.get_fcm_access_token() == ("access-1", "proj-1")
    clock["t"] += 54 * 60
    assert ps.get_fcm_access_token() == ("access-1", "proj-1")
    assert fcm.mints == 1
    clock["t"] += 2 * 60                       # 56 minutes after the first mint
    assert ps.get_fcm_access_token() == ("access-2", "proj-1")
    assert fcm.mints == 2


def test_changed_credentials_drop_the_cached_token(monkeypatch):
    configure(monkeypatch, project="proj-1", key_id="kid-1")
    fcm = Fcm(monkeypatch)
    assert ps.get_fcm_access_token()[1] == "proj-1"
    configure(monkeypatch, project="proj-2", key_id="kid-2")
    assert ps.get_fcm_access_token() == ("access-2", "proj-2")
    assert fcm.mints == 2


def test_mint_failure_returns_none_and_never_raises(monkeypatch):
    configure(monkeypatch)

    def boom(n):
        raise RuntimeError("google down")

    Fcm(monkeypatch, mint=boom)
    assert ps.get_fcm_access_token() == (None, None)


# ---------------------------------------------------------------------------- unconfigured
def test_unconfigured_send_returns_false_logs_once_and_posts_nothing(monkeypatch, caplog):
    fcm = Fcm(monkeypatch)
    with caplog.at_level(logging.WARNING, logger=ps.logger.name):
        assert run(ps.send_push("tok", "t", "b", {"type": "x"})) is False
        assert run(ps.send_push("tok", "t", "b", {"type": "x"})) is False
    assert fcm.posts == [] and fcm.mints == 0
    assert sum("not configured" in r.getMessage() for r in caplog.records) == 1


def test_unconfigured_fan_out_does_not_touch_the_database(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("the database must not be queried when FCM is not configured")

    monkeypatch.setattr(ps, "get_users_tokens", no_db)
    assert run(ps.send_push_to_users(["u1", "u2"], "t", "b")) == 0
    assert run(ps.send_push_to_user("u1", "t", "b")) == 0


def test_empty_and_expo_tokens_are_skipped_without_a_post(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    assert run(ps.send_push("", "t", "b")) is False
    assert run(ps.send_push(None, "t", "b")) is False
    assert run(ps.send_push("ExponentPushToken[abc]", "t", "b")) is False
    assert fcm.posts == [] and fcm.pruned == []        # an Expo token is not an FCM token: skipped, not pruned


# ---------------------------------------------------------------------------- message shape
def test_visible_message_keeps_the_app_payload_and_stringifies_data(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    ok = run(ps.send_push("tok-1", "Asha", "hi", {"type": "new_message", "message_id": 7, "x": None, "flag": True}))
    assert ok is True
    project, access, msg = fcm.posts[0]
    assert (project, access) == ("proj-1", "access-1")
    assert msg["token"] == "tok-1"
    assert msg["notification"] == {"title": "Asha", "body": "hi"}
    assert msg["data"] == {"type": "new_message", "message_id": "7", "x": "", "flag": "True"}
    assert all(isinstance(v, str) for v in msg["data"].values())
    assert msg["android"]["notification"]["channel_id"] == "attendance_notifications"
    assert msg["android"]["priority"] == "high" and msg["apns"]["payload"]["aps"]["sound"] == "default"


def test_channel_id_and_image_come_from_env_and_arguments(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("FCM_ANDROID_CHANNEL_ID", "alerts")
    fcm = Fcm(monkeypatch)
    run(ps.send_push("tok-1", "t", "b", {}, image_url="https://img/x.png"))
    msg = fcm.posts[0][2]
    assert msg["android"]["notification"]["channel_id"] == "alerts"
    assert msg["notification"]["image"] == "https://img/x.png" and msg["android"]["notification"]["image"] == "https://img/x.png"


def test_silent_message_is_data_only(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    run(ps.send_push("tok-1", "", "", {"type": "force_logout", "user_id": "u1"}, silent=True))
    msg = fcm.posts[0][2]
    assert "notification" not in msg
    assert msg["data"] == {"type": "force_logout", "user_id": "u1"}
    assert msg["apns"]["headers"]["apns-push-type"] == "background"
    assert msg["apns"]["payload"]["aps"] == {"content-available": 1}


# ---------------------------------------------------------------------------- error classification
@pytest.mark.parametrize("status, body, stale", [
    (200, "{}", False),
    (*err("UNREGISTERED", "UNREGISTERED"), True),
    (*err("NOT_FOUND"), True),
    (*err("INVALID_ARGUMENT", "INVALID_ARGUMENT", "The registration token is not a valid FCM registration token"), True),
    (*err("INVALID_ARGUMENT", "INVALID_ARGUMENT", "Invalid JSON payload", fields=["message.token"]), True),
    (*err("INVALID_ARGUMENT", "INVALID_ARGUMENT", "Invalid value at 'message.data[0].value'"), False),   # payload bug
    (*err("UNAUTHENTICATED"), False),
    (500, "<html>oops</html>", False),
    (404, "<html>not found</html>", False),                                                               # unparsable 404
    (503, json.dumps({"error": {"status": "UNAVAILABLE", "message": "try later"}}), False),
])
def test_stale_token_classification(status, body, stale):
    assert ps.is_stale_token_error(status, body) is stale


# ---------------------------------------------------------------------------- stale-token pruning on send
@pytest.mark.parametrize("answer", [err("UNREGISTERED", "UNREGISTERED"), err("NOT_FOUND"),
                                    err("INVALID_ARGUMENT", "INVALID_ARGUMENT", "not a valid FCM registration token")])
def test_dead_token_is_pruned(monkeypatch, answer):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch, answers=[answer])
    assert run(ps.send_push("dead-tok", "t", "b")) is False
    assert fcm.pruned == ["dead-tok"]


def test_other_errors_do_not_prune(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch, answers=[err("INVALID_ARGUMENT", "INVALID_ARGUMENT", "bad payload"), (500, "boom")])
    assert run(ps.send_push("tok", "t", "b")) is False
    assert run(ps.send_push("tok", "t", "b")) is False
    assert fcm.pruned == []


def test_expired_access_token_is_refreshed_and_retried_once(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch, answers=[err("UNAUTHENTICATED"), (200, "{}")])
    assert run(ps.send_push("tok", "t", "b")) is True
    assert [p[1] for p in fcm.posts] == ["access-1", "access-2"] and fcm.pruned == []


def test_second_unauthorized_answer_gives_up(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch, answers=[err("UNAUTHENTICATED"), err("UNAUTHENTICATED")])
    assert run(ps.send_push("tok", "t", "b")) is False
    assert len(fcm.posts) == 2


def test_transport_exception_never_raises(monkeypatch):
    configure(monkeypatch)

    async def boom(*a, **k):
        raise ConnectionError("network down")

    Fcm(monkeypatch)
    monkeypatch.setattr(ps, "_post_fcm", boom)
    assert run(ps.send_push("tok", "t", "b")) is False


# ---------------------------------------------------------------------------- fan-out
def test_fan_out_sends_to_every_token_of_every_user_once(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    monkeypatch.setattr(ps, "get_users_tokens", lambda ids: {"u1": ["a", "b"], "u2": ["c", "a"]})   # 'a' shared: sent once
    n = run(ps.send_push_to_users(["u1", "u2"], "t", "b", {"type": "x"}))
    assert n == 3
    assert sorted(m[2]["token"] for m in fcm.posts) == ["a", "b", "c"]


def test_one_dead_device_does_not_stop_the_others(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch, answers=[lambda m: err("UNREGISTERED", "UNREGISTERED") if m["token"] == "old" else (200, "{}")] * 2)
    monkeypatch.setattr(ps, "get_users_tokens", lambda ids: {"u1": ["old", "new"]})
    assert run(ps.send_push_to_user("u1", "t", "b")) == 1
    assert fcm.pruned == ["old"] and len(fcm.posts) == 2


def test_fan_out_to_explicit_tokens_deduplicates(monkeypatch):
    configure(monkeypatch)
    fcm = Fcm(monkeypatch)
    assert run(ps.send_push_to_tokens(["x", "x", "", None, "y"], "", "", {"type": "force_logout"}, silent=True)) == 2
    assert sorted(m[2]["token"] for m in fcm.posts) == ["x", "y"]


# ---------------------------------------------------------------------------- DB layer (fake connection)
class Cur:
    def __init__(self, rows=None, fail_on=None):
        self.rows = list(rows or [])           # fetchall() results in order
        self.sql = []
        self.fail_on = fail_on

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.sql.append((text, params))
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("relation does not exist")

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def patch_db(monkeypatch, cur):
    conn = Conn(cur)
    monkeypatch.setattr(ps, "get_db_connection", lambda: conn)
    monkeypatch.setattr(ps, "return_connection", lambda c: None)
    return conn


def test_get_users_tokens_prefers_the_table_and_falls_back_to_the_legacy_column(monkeypatch):
    cur = Cur(rows=[
        [("u1", "t1"), ("u1", "t2"), ("u1", "t1")],      # user_push_tokens
        [("u2", "legacy-2")],                            # users.push_token for the users without a row
    ])
    patch_db(monkeypatch, cur)
    out = ps.get_users_tokens(["u1", "u2", "u3", "u1"])
    assert out == {"u1": ["t1", "t2"], "u2": ["legacy-2"]}
    (sql1, p1), (sql2, p2) = cur.sql
    assert "FROM user_push_tokens WHERE user_id IN %s" in sql1 and p1 == (("u1", "u2", "u3"),)
    assert "FROM users WHERE user_id IN %s" in sql2 and "push_token <> ''" in sql2 and p2 == (("u2", "u3"),)


def test_get_users_tokens_survives_a_missing_table(monkeypatch):
    cur = Cur(rows=[[("u1", "legacy-1")]], fail_on="user_push_tokens")
    conn = patch_db(monkeypatch, cur)
    assert ps.get_users_tokens(["u1"]) == {"u1": ["legacy-1"]}
    assert conn.rollbacks == 1


def test_get_user_tokens_and_empty_input(monkeypatch):
    patch_db(monkeypatch, Cur(rows=[[("u1", "a")]]))
    assert ps.get_user_tokens("u1") == ["a"]
    assert ps.get_users_tokens([]) == {} and ps.get_users_tokens([None, ""]) == {}


def test_prune_token_removes_it_from_both_places(monkeypatch):
    cur = Cur()
    conn = patch_db(monkeypatch, cur)
    ps.prune_token("dead")
    assert [s for s, _p in cur.sql] == [
        "DELETE FROM user_push_tokens WHERE token = %s",
        "UPDATE users SET push_token = NULL WHERE push_token = %s",
    ]
    assert all(p == ("dead",) for _s, p in cur.sql) and conn.commits == 1


def test_prune_token_without_the_table_still_clears_the_legacy_column(monkeypatch):
    cur = Cur(fail_on="user_push_tokens")
    conn = patch_db(monkeypatch, cur)
    ps.prune_token("dead")
    assert cur.sql[-1][0] == "UPDATE users SET push_token = NULL WHERE push_token = %s" and conn.commits == 1


def test_clear_user_tokens(monkeypatch):
    cur = Cur()
    conn = patch_db(monkeypatch, cur)
    ps.clear_user_tokens("u1")
    assert cur.sql == [("DELETE FROM user_push_tokens WHERE user_id = %s", ("u1",))] and conn.commits == 1


def test_upsert_user_token_statements(monkeypatch):
    cur = Cur()
    assert ps.upsert_user_token(cur, "u1", " tok-1 ", "android", "dev-1") is True
    sqls = [s for s, _p in cur.sql]
    assert sqls[0] == "SAVEPOINT user_push_tokens_upsert" and sqls[-1] == "RELEASE SAVEPOINT user_push_tokens_upsert"
    insert, params = cur.sql[1]
    assert insert.startswith("INSERT INTO user_push_tokens (user_id, token, platform, device_id, updated_at)")
    assert "ON CONFLICT (user_id, token) DO UPDATE" in insert and params == ("u1", "tok-1", "android", "dev-1")
    assert insert.count("%s") == len(params)
    assert cur.sql[2] == ("DELETE FROM user_push_tokens WHERE token = %s AND user_id <> %s", ("tok-1", "u1"))
    assert cur.sql[3] == ("DELETE FROM user_push_tokens WHERE user_id = %s AND device_id = %s AND token <> %s",
                          ("u1", "dev-1", "tok-1"))
    assert len(cur.sql) == 5                  # no "replace others" statement unless asked


def test_upsert_user_token_replace_others_and_no_device_id(monkeypatch):
    cur = Cur()
    assert ps.upsert_user_token(cur, "u1", "tok-2", replace_others=True) is True
    sqls = [s for s, _p in cur.sql]
    assert not any("device_id = %s AND token" in s for s in sqls)
    assert "DELETE FROM user_push_tokens WHERE user_id = %s AND token <> %s" in sqls
    assert cur.sql[1][1] == ("u1", "tok-2", None, None)


def test_upsert_user_token_expo_platform_and_truncation():
    cur = Cur()
    ps.upsert_user_token(cur, "u1", "ExponentPushToken[abc]", device_id="d" * 100)
    assert cur.sql[1][1] == ("u1", "ExponentPushToken[abc]", "expo", "d" * 64)


def test_upsert_failure_rolls_back_to_the_savepoint_and_reports_false():
    cur = Cur(fail_on="INSERT INTO user_push_tokens")
    assert ps.upsert_user_token(cur, "u1", "tok") is False
    assert cur.sql[-1][0] == "ROLLBACK TO SAVEPOINT user_push_tokens_upsert"


def test_upsert_ignores_blank_input():
    cur = Cur()
    assert ps.upsert_user_token(cur, "u1", "  ") is False and ps.upsert_user_token(cur, "", "t") is False
    assert cur.sql == []


# ---------------------------------------------------------------------------- the save sites in auth_function
class AuthCur(Cur):
    rowcount = 1

    def fetchone(self):
        return None


def _patch_auth_db(monkeypatch, cur):
    from core_functions import auth_function as af
    conn = Conn(cur)
    monkeypatch.setattr(af, "get_db_connection", lambda: conn)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    return af, conn


def test_update_push_token_also_registers_the_device_in_the_table(monkeypatch):
    cur = AuthCur()
    cur.close = lambda: None
    af, conn = _patch_auth_db(monkeypatch, cur)
    res = run(af.update_push_token(af.UpdatePushTokenRequest(user_id="u1", push_token="tok-9", device_token="dev-9", platform="ios")))
    assert res["success"] is True and conn.commits == 1
    sqls = [s for s, _p in cur.sql]
    assert sqls[0] == "UPDATE users SET push_token = %s, updated_at = %s WHERE user_id = %s"        # legacy column kept
    insert = [c for c in cur.sql if c[0].startswith("INSERT INTO user_push_tokens")][0]
    assert insert[1] == ("u1", "tok-9", "ios", "dev-9")


def test_update_push_token_for_an_unknown_user_is_404_and_stores_nothing(monkeypatch):
    from fastapi import HTTPException
    cur = AuthCur()
    cur.rowcount = 0
    cur.close = lambda: None
    af, conn = _patch_auth_db(monkeypatch, cur)
    with pytest.raises(HTTPException) as exc:
        run(af.update_push_token(af.UpdatePushTokenRequest(user_id="ghost", push_token="t")))
    assert exc.value.status_code == 404 and not any("user_push_tokens" in s for s, _p in cur.sql)


def test_a_missing_token_table_does_not_break_the_legacy_save(monkeypatch):
    cur = AuthCur(fail_on="INSERT INTO user_push_tokens")
    cur.close = lambda: None
    af, conn = _patch_auth_db(monkeypatch, cur)
    res = run(af.update_push_token(af.UpdatePushTokenRequest(user_id="u1", push_token="tok-1")))
    assert res["success"] is True and conn.commits == 1 and conn.rollbacks == 0
    assert cur.sql[-1][0] == "ROLLBACK TO SAVEPOINT user_push_tokens_upsert"
