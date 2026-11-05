"""DB-free tests of the global authentication guard (core_functions/auth_dependency.py) on a small FastAPI app:
REQUIRE_AUTH off passthrough, on (401 / 403), revocation epoch + cache, organisation / user ownership, manager permissions,
body re-reading, the public / operator lists, WebSocket handshakes and the sign-in / sign-out hooks.
Run: python -m pytest tests/test_auth_guard.py -q
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app, token_for  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from utils import auth_token as T  # noqa: E402
from wp7_fakes import RouteCursor  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("AUTH_EPOCH_CACHE_SECONDS", raising=False)
    yield
    ad.reset_state()


USERS = {
    "adm": {"role": 1, "org": "org-A", "orgs": {"org-A"}},
    "mgr": {"role": 2, "org": "org-A"},
    "stf": {"role": 3, "org": "org-A"},
    "stf2": {"role": 3, "org": "org-A"},
    "other-adm": {"role": 1, "org": "org-B"},
    "other-stf": {"role": 3, "org": "org-B"},
}


@pytest.fixture
def directory():
    return FakeDirectory(USERS, {"mgr": {"Staff Management"}}, orgs_of_org_ids=("org-A", "org-B")).install()


@pytest.fixture
def client(directory):
    return TestClient(make_app())


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")


ADM = auth("adm", "org-A", 1)
MGR = auth("mgr", "org-A", 2)
STF = auth("stf", "org-A", 3)


# ============================================================================ REQUIRE_AUTH off: behaviour as today
def test_off_request_without_a_token_passes_everywhere(client):
    for path in ("/me", "/home/dashboard-status?org_id=org-B", "/addstaff/other-stf", "/gen-dashboard/stats"):
        assert client.get(path).status_code == 200
    assert client.post("/orders/create").status_code == 200
    assert client.post("/branch/add", json={"org_id": "org-B"}).status_code == 200
    assert client.get("/me").json() == {"identity": None}


@pytest.mark.parametrize("header", ["Bearer garbage", "Bearer a.b.c", "Basic abc", "Bearer ", "Token xyz"])
def test_off_invalid_present_token_is_ignored(client, header):
    r = client.get("/me", headers={"Authorization": header})
    assert r.status_code == 200 and r.json() == {"identity": None}


def test_off_expired_and_revoked_tokens_are_ignored(client, directory):
    expired = token_for("stf", "org-A", 3, now=1_000_000)
    assert client.get("/me", headers={"Authorization": "Bearer " + expired}).json() == {"identity": None}
    directory.users["stf"]["epoch"] = 5
    ad.reset_state(); directory.install()
    assert client.get("/me", headers=STF).json() == {"identity": None}


def test_off_valid_token_exposes_the_identity_but_enforces_nothing(client):
    assert client.get("/me", headers=STF).json() == {"identity": {"user": "stf", "org": "org-A", "role": 3}}
    # a staff token reading another organisation's data: still served while the flag is off
    assert client.get("/addstaff/other-stf", headers=STF).status_code == 200
    assert client.post("/orders/create", headers=STF).status_code == 200


def test_off_token_is_not_parsed_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    r = client.get("/me", headers=STF)
    assert r.status_code == 200 and r.json() == {"identity": None}


def test_off_lookup_failure_is_ignored(client, directory):
    directory.fail = True
    assert client.get("/me", headers=STF).json() == {"identity": None}


# ============================================================================ REQUIRE_AUTH on: 401
def test_on_missing_token_is_401_not_authenticated(client, enforced):
    for path in ("/me", "/home/dashboard-status?org_id=org-A", "/addstaff/stf"):
        r = client.get(path)
        assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}
        assert r.headers["www-authenticate"] == "Bearer"
    assert client.post("/orders/create").status_code == 401


@pytest.mark.parametrize("header", ["Bearer garbage", "Bearer a.b.c", "Basic abc", "Bearer "])
def test_on_invalid_token_is_401(client, enforced, header):
    r = client.get("/me", headers={"Authorization": header})
    assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}


def test_on_expired_token_is_401_token_expired(client, enforced):
    expired = token_for("stf", "org-A", 3, now=1_000_000)
    r = client.get("/me", headers={"Authorization": "Bearer " + expired})
    assert r.status_code == 401 and r.json() == {"detail": "Token expired"}


def test_on_wrong_secret_and_alg_none_are_401(client, enforced):
    forged = T.issue_token("adm", "org-A", 1, 0, key="x" * 48)[0]
    assert client.get("/me", headers={"Authorization": "Bearer " + forged}).status_code == 401
    head = "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"
    body = token_for("adm", "org-A", 1).split(".")[1]
    assert client.get("/me", headers={"Authorization": f"Bearer {head}.{body}."}).status_code == 401


def test_on_revoked_epoch_and_inactive_or_missing_user_are_401(client, enforced, directory):
    old = auth("stf", "org-A", 3, epoch=0)
    assert client.get("/me", headers=old).status_code == 200
    directory.users["stf"]["epoch"] = 1
    ad.forget_user("stf")                                   # the bump happened in this process: cache dropped at once
    assert client.get("/me", headers=old).status_code == 401
    assert client.get("/me", headers=auth("stf", "org-A", 3, epoch=1)).status_code == 200
    directory.users["stf"]["status"] = 2                    # deleted
    ad.forget_user("stf")
    assert client.get("/me", headers=auth("stf", "org-A", 3, epoch=1)).status_code == 401
    assert client.get("/me", headers=auth("ghost", "org-A", 3)).status_code == 401


def test_on_lookup_failure_fails_closed_with_503(client, enforced, directory):
    directory.fail = True
    r = client.get("/me", headers=STF)
    assert r.status_code == 503 and r.json() == {"detail": "Authentication temporarily unavailable"}


def test_on_database_role_wins_over_the_role_in_the_token(client, enforced, directory):
    # demoted manager: the token still says 2, the database says 3 -> treated as staff (no manager rights)
    directory.users["mgr"]["role"] = 3
    r = client.get("/me", headers=MGR)
    assert r.json()["identity"]["role"] == 3
    assert client.post("/requests/update-status", headers=MGR, json={"request_id": "r", "action_by": "mgr"}).status_code == 403


# ============================================================================ public and operator lists
def test_on_public_paths_need_no_token(client, enforced):
    assert client.get("/health").status_code == 200
    assert client.get("/auth/config").status_code == 200
    assert client.get("/docs").status_code in (200, 404)    # not mounted on this small app, but never 401 from the guard
    assert client.post("/requests/device-change/submit", json={"user_id": "stf", "org_id": "org-A"}).status_code == 200


def test_on_public_path_still_exposes_a_valid_identity_and_ignores_a_bad_one(client, enforced):
    assert client.get("/health", headers=STF).json()["identity"]["user"] == "stf"
    r = client.get("/health", headers={"Authorization": "Bearer junk"})
    assert r.status_code == 200 and r.json()["identity"] is None


def test_on_operator_route_is_never_satisfied_by_an_app_token(client, enforced):
    assert client.get("/gen-dashboard/stats").status_code == 200            # no app token: the operator mechanism decides
    for headers in (ADM, MGR, STF):
        r = client.get("/gen-dashboard/stats", headers=headers)
        assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}


# ============================================================================ 403: roles, permissions
def test_on_role_policy_denies_staff_admin_routes(client, enforced):
    assert client.post("/orders/create", headers=ADM).status_code == 200
    for headers in (MGR, STF):
        r = client.post("/orders/create", headers=headers)
        assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}


def test_on_manager_permission_is_required_for_managed_areas(client, enforced, directory):
    body = {"org_id": "org-A", "note": "x"}
    assert client.post("/branch/add", headers=ADM, json=body).status_code == 200
    r = client.post("/branch/add", headers=MGR, json=body)                  # mgr only holds Staff Management
    assert r.status_code == 403 and "Branch Management" in r.json()["detail"]
    directory.permissions["mgr"] = {"Branch Management"}
    ad.forget_user("mgr")
    assert client.post("/branch/add", headers=MGR, json=body).status_code == 200
    assert client.post("/branch/add", headers=STF, json=body).status_code == 403


def test_on_permission_lookup_happens_only_for_managers_and_only_when_needed(client, enforced, directory):
    client.get("/me", headers=MGR)
    client.post("/branch/add", headers=ADM, json={"org_id": "org-A"})
    client.post("/branch/add", headers=STF, json={"org_id": "org-A"})
    assert directory.calls["permissions"] == 0
    client.post("/branch/add", headers=MGR, json={"org_id": "org-A"})
    client.post("/branch/add", headers=MGR, json={"org_id": "org-A"})
    assert directory.calls["permissions"] == 1                              # cached for the second request


# ============================================================================ 403: ownership of ids
def test_on_org_mismatch_in_query_path_body_and_form_is_403(client, enforced):
    assert client.get("/home/dashboard-status?org_id=org-A", headers=ADM).status_code == 200
    assert client.get("/home/dashboard-status?org_id=org-B", headers=ADM).status_code == 403
    assert client.get("/api/leads/employee/org-B/stf", headers=STF).status_code == 403
    assert client.get("/api/leads/employee/org-A/stf", headers=STF).status_code == 200
    assert client.post("/api/leads/create", headers=STF, json={"org_id": "org-B", "user_id": "stf"}).status_code == 403
    assert client.post("/api/leads/create", headers=STF, json={"org_id": "org-A", "user_id": "stf"}).status_code == 200
    ok = client.post("/support/submit", headers=STF, data={"user_id": "stf", "org_id": "org-A", "message": "m"})
    bad = client.post("/support/submit", headers=STF, data={"user_id": "stf", "org_id": "org-B", "message": "m"})
    assert ok.status_code == 200 and bad.status_code == 403 and bad.json() == {"detail": "Forbidden"}


def test_on_staff_may_only_use_their_own_user_id(client, enforced):
    assert client.get("/addstaff/stf", headers=STF).status_code == 200
    assert client.get("/addstaff/stf2", headers=STF).status_code == 403               # same org, other user
    assert client.get("/addstaff/other-stf", headers=STF).status_code == 403


def test_on_admin_and_manager_may_use_users_of_their_own_org_only(client, enforced, directory):
    for headers in (ADM, MGR):
        assert client.get("/addstaff/stf2", headers=headers).status_code == 200
        assert client.get("/addstaff/other-stf", headers=headers).status_code == 403
        assert client.get("/addstaff/other-adm", headers=headers).status_code == 403
        assert client.get("/addstaff/org-B", headers=headers).status_code == 403     # another org's id under user_id
        assert client.get("/addstaff/org-A", headers=headers).status_code == 200      # own org id under user_id
        assert client.get("/addstaff/no-such-user", headers=headers).status_code == 200  # unknown: the handler answers 404


def test_on_manager_may_send_their_own_user_id_as_org_id(client, enforced):
    assert client.get("/home/dashboard-status?org_id=mgr", headers=MGR).status_code == 200      # app convention
    assert client.get("/home/dashboard-status?org_id=adm", headers=MGR).status_code == 403
    assert client.get("/home/dashboard-status?org_id=stf", headers=STF).status_code == 403      # staff: no such convention


def test_on_sender_and_actor_keys_must_be_the_caller(client, enforced):
    ok = client.post("/requests/update-status", headers=ADM, json={"request_id": "r", "action_by": "adm"})
    assert ok.status_code == 200
    spoof = client.post("/requests/update-status", headers=ADM, json={"request_id": "r", "action_by": "other-adm"})
    assert spoof.status_code == 403


def test_on_multi_company_admin_may_use_every_company_they_own(client, enforced, directory):
    directory.users["adm"]["orgs"] = {"org-A", "org-A2"}
    ad.reset_state(); directory.install()
    assert client.get("/home/dashboard-status?org_id=org-A2", headers=ADM).status_code == 200
    assert client.get("/home/dashboard-status?org_id=org-B", headers=ADM).status_code == 403


def test_on_a_company_added_after_the_cache_was_filled_is_found_by_the_throttled_refresh(client, enforced, directory, monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(ad, "_clock", lambda: clock["t"])
    assert client.get("/home/dashboard-status?org_id=org-A", headers=ADM).status_code == 200
    directory.users["adm"]["orgs"] = {"org-A", "org-NEW"}                   # created through /company/add meanwhile
    before = directory.calls["user_state"]
    assert client.get("/home/dashboard-status?org_id=org-NEW", headers=ADM).status_code == 403   # refresh throttled (5 s)
    assert directory.calls["user_state"] == before
    clock["t"] += 6
    assert client.get("/home/dashboard-status?org_id=org-NEW", headers=ADM).status_code == 200
    assert directory.calls["user_state"] == before + 1


def test_on_exempt_key_lets_staff_read_a_colleagues_photo_url(client, enforced):
    from core_functions import auth_policy as P
    assert P.ownership_violation(P.Identity("stf", "org-A", 3), [("path", "user_id", "stf2")], frozenset({"org-A"}),
                                 method="GET", path="/auth/employee-profile/stf2") is None
    assert P.ownership_violation(P.Identity("stf", "org-A", 3), [("path", "user_id", "stf2")], frozenset({"org-A"}),
                                 method="GET", path="/addstaff/stf2") == ("path", "user_id")


# ============================================================================ bodies stay readable
def test_body_is_readable_by_the_handler_after_the_guard_peeked_at_it(client, enforced):
    payload = {"org_id": "org-A", "user_id": "stf", "note": "hello"}
    r = client.post("/api/leads/create", headers=STF, json=payload)
    assert r.status_code == 200 and r.json()["body"]["note"] == "hello"
    raw = client.post("/raw/json", headers=STF, json=payload)               # handler calls request.json() itself
    assert raw.status_code == 200 and raw.json()["json"] == payload
    assert raw.json()["bytes"] == len(json.dumps(payload, separators=(",", ":")))


def test_raw_body_handler_with_foreign_org_is_blocked_after_peek(client, enforced):
    r = client.post("/raw/json", headers=STF, json={"org_id": "org-B"})
    assert r.status_code == 403


def test_large_undeclared_body_is_not_buffered_by_the_guard(client, enforced):
    big = b"x" * (200 * 1024)
    r = client.post("/raw/stream", headers={**STF, "Content-Type": "application/json"}, content=big)
    assert r.status_code == 200 and r.json() == {"bytes": len(big)}         # streamed through untouched


def test_non_json_body_is_not_parsed(client, enforced):
    r = client.post("/raw/stream", headers={**STF, "Content-Type": "application/octet-stream"}, content=b"org_id=org-B")
    assert r.status_code == 200


def test_invalid_json_body_is_left_to_the_handler(directory, enforced):
    lenient = TestClient(make_app(), raise_server_exceptions=False)
    r = lenient.post("/raw/json", headers={**STF, "Content-Type": "application/json"}, content=b"{not json")
    assert r.status_code == 500                                              # the handler's own json() error, not a guard 401/403
    ok = lenient.post("/api/leads/create", headers={**STF, "Content-Type": "application/json"}, content=b"{not json")
    assert ok.status_code == 422                                             # FastAPI's validation answer is unchanged


# ============================================================================ cache behaviour
def test_epoch_lookup_is_cached_between_requests(client, enforced, directory):
    for _ in range(5):
        assert client.get("/me", headers=STF).status_code == 200
    assert directory.calls["user_state"] == 1


def test_epoch_cache_expires_after_the_configured_time(client, enforced, directory, monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(ad, "_clock", lambda: clock["t"])
    client.get("/me", headers=STF)
    clock["t"] += 29
    client.get("/me", headers=STF)
    assert directory.calls["user_state"] == 1
    clock["t"] += 2                                                           # 31 s: past the default 30 s
    client.get("/me", headers=STF)
    assert directory.calls["user_state"] == 2


def test_epoch_cache_seconds_env_and_zero_disables_caching(client, enforced, directory, monkeypatch):
    monkeypatch.setenv("AUTH_EPOCH_CACHE_SECONDS", "0")
    for _ in range(3):
        client.get("/me", headers=STF)
    assert directory.calls["user_state"] == 3
    monkeypatch.setenv("AUTH_EPOCH_CACHE_SECONDS", "junk")
    assert ad.cache_seconds() == 30.0
    monkeypatch.setenv("AUTH_EPOCH_CACHE_SECONDS", "12.5")
    assert ad.cache_seconds() == 12.5


def test_a_stale_revoked_token_is_cut_off_within_the_cache_window_in_other_processes(client, enforced, directory, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(ad, "_clock", lambda: clock["t"])
    old = auth("stf", "org-A", 3)
    assert client.get("/me", headers=old).status_code == 200
    directory.users["stf"]["epoch"] = 1                                       # bumped by ANOTHER process: no local drop
    assert client.get("/me", headers=old).status_code == 200                  # still cached
    clock["t"] += 31
    assert client.get("/me", headers=old).status_code == 401                  # picked up after at most 30 s


def test_unknown_ids_are_cached_too_and_lookups_are_per_target(client, enforced, directory):
    for _ in range(3):
        client.get("/addstaff/stf2", headers=ADM)
    assert directory.calls["entity_org"] == 1


# ============================================================================ WebSockets
def test_ws_off_connects_without_a_token(client):
    with client.websocket_connect("/ws/chat/stf") as ws:
        assert ws.receive_text() == "hello stf"


def test_ws_on_requires_a_token_of_the_path_user(client, enforced):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat/stf"):
            pass
    assert exc.value.code == 4401
    good = token_for("stf", "org-A", 3)
    with client.websocket_connect(f"/ws/chat/stf?token={good}") as ws:
        assert ws.receive_text() == "hello stf"
    with pytest.raises(WebSocketDisconnect) as exc:                          # a valid token of ANOTHER user
        with client.websocket_connect(f"/ws/chat/stf2?token={good}"):
            pass
    assert exc.value.code == 4401


@pytest.mark.parametrize("query", ["?token=garbage", "?token=", "?tok=abc"])
def test_ws_on_bad_tokens_are_refused(client, enforced, query):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/chat/stf" + query):
            pass
    assert exc.value.code == 4401


def test_ws_on_expired_or_revoked_tokens_are_refused(client, enforced, directory):
    expired = token_for("stf", "org-A", 3, now=1_000_000)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/chat/stf?token={expired}"):
            pass
    directory.users["stf"]["epoch"] = 3
    ad.forget_user("stf")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/chat/stf?token=" + token_for("stf", "org-A", 3, epoch=0)):
            pass


def test_ws_routes_are_not_blocked_by_the_http_guard(client, enforced):
    # the global dependency skips websocket scopes: only the handler decides (otherwise the handshake would 403 first)
    good = token_for("adm", "org-A", 1)
    with client.websocket_connect(f"/ws/chat/adm?token={good}") as ws:
        assert ws.receive_text() == "hello adm"


# ============================================================================ handler helpers
def test_helpers_org_of_and_assert_same_org(directory):
    from fastapi import FastAPI
    from starlette.requests import Request
    app = FastAPI()
    ad.install(app)

    @app.get("/who")
    def who(request: Request):
        ad.assert_same_org(request, request.query_params.get("org"))
        return {"org": ad.org_of(request), "id": getattr(ad.current_identity(request), "user_id", None)}

    @app.get("/must")
    def must(request: Request):
        return {"id": ad.require_identity(request).user_id}

    c = TestClient(app)
    assert c.get("/who?org=org-A", headers=STF).json() == {"org": "org-A", "id": "stf"}
    assert c.get("/who?org=org-B", headers=STF).status_code == 403
    assert c.get("/who?org=org-B").json() == {"org": None, "id": None}       # no token: the helper does not guess
    assert c.get("/must").status_code == 401                                  # require_identity works with the flag off
    assert c.get("/must", headers=STF).json() == {"id": "stf"}


# ============================================================================ issuing and revoking (write side)
def test_issue_for_user_builds_a_token_from_the_current_row():
    cur = RouteCursor().add("FROM users u WHERE u.user_id", one={"auth_epoch": 4, "status": 1, "role_id": 3, "org_id": "org-A"})
    out = ad.issue_for_user(cur, "stf")
    assert set(out) == {"access_token", "expires_in"} and out["expires_in"] == 90 * 86400
    claims = T.verify_token(out["access_token"], key=KEY)
    assert (claims["sub"], claims["org"], claims["role"], claims["ep"]) == ("stf", "org-A", 3, 4)
    sql, params = cur.find("FROM users u WHERE u.user_id")[0]
    assert params == ("stf",) and sql.count("%s") == 1 and "auth_epoch" in sql
    assert cur.has("SAVEPOINT auth_token_issue") and cur.has("RELEASE SAVEPOINT auth_token_issue")


def test_issue_for_user_also_reads_plain_tuple_rows():
    cur = RouteCursor().add("FROM users u WHERE u.user_id", one=(2, 1, 2, "org-A"))
    claims = T.verify_token(ad.issue_for_user(cur, "mgr")["access_token"], key=KEY)
    assert (claims["role"], claims["ep"], claims["org"]) == (2, 2, "org-A")


def test_issue_for_user_is_empty_without_secret_unknown_user_or_missing_column(monkeypatch):
    cur = RouteCursor().add("FROM users u WHERE u.user_id", one=None)
    assert ad.issue_for_user(cur, "ghost") == {}
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    cur2 = RouteCursor()
    assert ad.issue_for_user(cur2, "stf") == {} and cur2.sql == []           # nothing is even queried
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)

    class Broken(RouteCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "FROM users u" in " ".join(str(sql).split()):
                raise RuntimeError('column "auth_epoch" does not exist')

    broken = Broken()
    assert ad.issue_for_user(broken, "stf") == {}
    assert broken.has("ROLLBACK TO SAVEPOINT auth_token_issue")              # the surrounding transaction survives


def test_bump_epoch_sql_only_touches_single_device_roles_and_drops_the_cache(directory):
    cur = RouteCursor()
    client = TestClient(make_app())
    # warm the cache, then bump: the very next request re-reads the user
    client.get("/me", headers=STF)
    warm = directory.calls["user_state"]
    assert ad.bump_epoch(cur, "stf") is True
    client.get("/me", headers=STF)
    assert directory.calls["user_state"] == warm + 1
    sql, params = cur.find("UPDATE users SET auth_epoch")[0]
    assert "auth_epoch = auth_epoch + 1" in sql and "role_id IN (2, 3)" in sql and params == ("stf",) and sql.count("%s") == 1
    assert cur.has("SAVEPOINT auth_epoch_bump") and cur.has("RELEASE SAVEPOINT auth_epoch_bump")


def test_takeover_bump_needs_a_different_non_null_device_token():
    cur = RouteCursor()
    assert ad.bump_epoch_on_takeover(cur, "stf", "dev-NEW") is True
    sql, params = cur.find("UPDATE users SET auth_epoch")[0]
    assert "device_token IS NOT NULL" in sql and "device_token IS DISTINCT FROM %s" in sql
    assert params == ("stf", "dev-NEW") and sql.count("%s") == 2


def test_bump_is_a_noop_without_a_configured_secret(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    cur = RouteCursor()
    assert ad.bump_epoch(cur, "stf") is False and ad.bump_epoch_on_takeover(cur, "stf", "d") is False
    assert cur.sql == []


def test_bump_survives_a_missing_column():
    class Broken(RouteCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "UPDATE users SET auth_epoch" in " ".join(str(sql).split()):
                raise RuntimeError('column "auth_epoch" does not exist')

    cur = Broken()
    assert ad.bump_epoch(cur, "stf") is False
    assert cur.has("ROLLBACK TO SAVEPOINT auth_epoch_bump")


# ============================================================================ sign-in / sign-out hooks
def test_logout_of_staff_bumps_the_epoch_and_admin_logout_is_covered_by_the_role_filter(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    cur = RouteCursor()
    conn = patch_db(monkeypatch, af, cur)
    assert af.logout_user(af.LogoutRequest(user_id="stf", device_token="dev-A")) == {"success": True}
    assert cur.has("UPDATE users SET auth_epoch = auth_epoch + 1")
    assert "role_id IN (2, 3)" in cur.find("UPDATE users SET auth_epoch")[0][0]      # an admin row is never matched
    assert conn.commits == 1


def test_logout_from_a_stale_device_revokes_nothing(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    cur = RouteCursor(rowcount=0)                                              # device_token no longer matches: 0 rows
    patch_db(monkeypatch, af, cur)
    af.logout_user(af.LogoutRequest(user_id="stf", device_token="dev-OLD"))
    assert not cur.has("UPDATE users SET auth_epoch")


def test_logout_without_a_secret_runs_no_epoch_statement(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    cur = RouteCursor()
    patch_db(monkeypatch, af, cur)
    af.logout_user(af.LogoutRequest(user_id="stf", device_token="dev-A"))
    assert not cur.has("auth_epoch") and not cur.has("SAVEPOINT auth_epoch")


def _validate(monkeypatch, role, stored, device, purpose="login"):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    monkeypatch.setenv("USER_AUTH", "otp")
    monkeypatch.delenv("ENFORCE_SINGLE_DEVICE", raising=False)
    cur = RouteCursor()
    cur.add("FROM moblie_otp_verification", one={"attempts": 0, "otp_code": 123456})
    cur.add("FROM users WHERE users.mobile_number", one={"user_id": "stf", "role_id": role})
    cur.add("SELECT device_token, push_token FROM users", one={"device_token": stored, "push_token": None})
    cur.add("FROM users u WHERE u.user_id", one={"auth_epoch": 1, "status": 1, "role_id": role, "org_id": "org-A"})
    patch_db(monkeypatch, af, cur)

    async def noop(*a, **k):
        return None

    monkeypatch.setattr(af, "_signal_previous_device", noop)
    req = af.ValidateOTPRequest(contact="9000000003", otp_code="123456", purpose=purpose, device_token=device)
    return asyncio.run(af.validate_otp(req)), cur


def test_validate_otp_returns_a_token_and_bumps_on_a_staff_takeover(monkeypatch):
    res, cur = _validate(monkeypatch, 3, "dev-OLD", "dev-NEW")
    assert res["success"] is True and res["user_exists"] is True and res["device_token"] == "dev-NEW"
    assert set(res) == {"success", "message", "device_token", "user_exists", "role_id", "access_token", "expires_in"}
    assert T.verify_token(res["access_token"], key=KEY)["sub"] == "stf" and res["expires_in"] == 90 * 86400
    statements = [s for s, _ in cur.sql]
    bump = next(i for i, s in enumerate(statements) if s.startswith("UPDATE users SET auth_epoch"))
    save = next(i for i, s in enumerate(statements) if s.startswith("UPDATE users SET device_token"))
    issue = next(i for i, s in enumerate(statements) if "FROM users u WHERE u.user_id" in s)
    assert bump < save < issue                                               # the token carries the NEW epoch


def test_validate_otp_same_device_relogin_does_not_bump(monkeypatch):
    res, cur = _validate(monkeypatch, 3, "dev-SAME", "dev-SAME")
    assert "access_token" in res and not cur.has("UPDATE users SET auth_epoch")


def test_validate_otp_admin_second_device_is_not_a_takeover(monkeypatch):
    res, cur = _validate(monkeypatch, 1, "dev-OLD", "dev-NEW")
    assert "access_token" in res and not cur.has("UPDATE users SET auth_epoch")


def test_validate_otp_non_login_purposes_do_not_bump(monkeypatch):
    res, cur = _validate(monkeypatch, 3, "dev-OLD", "dev-NEW", purpose="update_email")
    assert "access_token" in res and not cur.has("UPDATE users SET auth_epoch")


def test_validate_otp_without_a_secret_answers_exactly_as_before(monkeypatch):
    monkeypatch.delenv("AUTH_TOKEN_SECRET")
    res, cur = _validate(monkeypatch, 3, "dev-OLD", "dev-NEW")
    assert set(res) == {"success", "message", "device_token", "user_exists", "role_id"}
    assert not cur.has("auth_epoch") and not cur.has("SAVEPOINT")


def test_validate_otp_for_a_new_user_issues_no_token(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    monkeypatch.setenv("USER_AUTH", "otp")
    cur = RouteCursor()
    cur.add("FROM moblie_otp_verification", one={"attempts": 0, "otp_code": 123456})
    cur.add("FROM users WHERE users.mobile_number", one=None)
    patch_db(monkeypatch, af, cur)
    res = asyncio.run(af.validate_otp(af.ValidateOTPRequest(contact="9000000009", otp_code="123456", purpose="registration")))
    assert res["user_exists"] is False and "access_token" not in res


def test_verify_device_token_success_returns_a_token(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    cur = RouteCursor()
    cur.add("FROM users u LEFT JOIN org_info o", one={
        "user_id": "adm", "role_id": 1, "device_token": "dev-A", "device_token_expires_at": None, "name": "A",
        "email": "a@x.in", "org_id": "org-A", "status": 1, "org_name": "Org"})
    cur.add("FROM users u WHERE u.user_id", one={"auth_epoch": 0, "status": 1, "role_id": 1, "org_id": "org-A"})
    patch_db(monkeypatch, af, cur)
    monkeypatch.setenv("USER_AUTH", "otp")

    import ml.s3 as s3  # the function imports it lazily: stub the S3 call
    monkeypatch.setattr(s3, "get_org_logo_url", lambda org: None, raising=False)
    res = asyncio.run(af.verify_device_token(af.DeviceTokenVerifyRequest(contact="9000000001", device_token="dev-A", role_id=1)))
    assert res["success"] is True and res["skip_otp"] is True
    assert T.verify_token(res["access_token"], key=KEY)["sub"] == "adm" and res["expires_in"] == 90 * 86400
    # nothing else of the body changed
    assert {"message", "user_id", "email", "role_id", "org_id", "org_name", "org_logo"} <= set(res)


def test_failed_device_verification_carries_no_token(monkeypatch):
    from core_functions import auth_function as af
    from wp7_fakes import patch_db
    cur = RouteCursor()
    cur.add("FROM users u LEFT JOIN org_info o", one={
        "user_id": "adm", "role_id": 1, "device_token": "dev-OTHER", "device_token_expires_at": None, "name": "A",
        "email": "a@x.in", "org_id": "org-A", "status": 1, "org_name": "Org"})
    patch_db(monkeypatch, af, cur)
    monkeypatch.setenv("USER_AUTH", "otp")
    monkeypatch.setattr(af, "_file_device_access_request", lambda *a: None)
    res = asyncio.run(af.verify_device_token(af.DeviceTokenVerifyRequest(contact="9000000001", device_token="dev-A", role_id=1)))
    assert res["success"] is False and "access_token" not in res


def test_device_change_approval_bumps_the_requesters_epoch(monkeypatch):
    from core_functions import requests_functions as rf
    from wp7_fakes import patch_db
    cur = RouteCursor()
    cur.add("SELECT r.request_type, r.org_id", one=("device_change", "org-A", "stf", "pending", 1, "d1"))
    patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    import services.push_service as ps
    monkeypatch.setattr(ps, "clear_user_tokens", lambda uid: None)
    assert rf.update_request_status("req-1", "approved", "adm", 1)["success"] is True
    sql, params = cur.find("UPDATE users SET auth_epoch")[0]
    assert params == ("stf",) and "role_id IN (2, 3)" in sql


def test_device_change_rejection_and_repeat_approval_do_not_bump(monkeypatch):
    from core_functions import requests_functions as rf
    from wp7_fakes import patch_db
    for status, previous in (("rejected", "pending"), ("approved", "approved")):
        cur = RouteCursor()
        cur.add("SELECT r.request_type, r.org_id", one=("device_change", "org-A", "stf", previous, 1, "d1"))
        patch_db(monkeypatch, rf, cur)
        monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
        assert rf.update_request_status("req-1", status, "adm", 1)["success"] is True
        assert not cur.has("UPDATE users SET auth_epoch")


# ============================================================================ wiring on a real router include
def test_guard_covers_included_routers_websockets_openapi_and_routes_added_after_install(directory, enforced):
    from fastapi import APIRouter, Depends, FastAPI, WebSocket

    app = FastAPI()
    ad.install(app)
    router = APIRouter(prefix="/orders", dependencies=[Depends(lambda: None)])

    @router.get("/history/{user_id}")
    def history(user_id: str):
        return {"ok": True}

    @router.websocket("/ws/{user_id}")
    async def sock(websocket: WebSocket, user_id: str):
        if not await ad.authenticate_websocket(websocket, user_id):
            return
        await websocket.accept()
        await websocket.close()

    app.include_router(router)

    @app.get("/late")
    def late():
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/orders/history/adm").status_code == 401                        # router routes are guarded
    assert c.get("/late").status_code == 401                                      # so are routes defined after install
    assert c.get("/orders/history/adm", headers=ADM).status_code == 200
    assert c.get("/orders/history/adm", headers=STF).status_code == 403           # /orders/* is admin only
    assert c.get("/openapi.json").status_code == 200                              # the guard does not break the schema
    with pytest.raises(WebSocketDisconnect) as exc:
        with c.websocket_connect("/orders/ws/adm"):
            pass
    assert exc.value.code == 4401
    with c.websocket_connect("/orders/ws/adm?token=" + token_for("adm", "org-A", 1)):
        pass
