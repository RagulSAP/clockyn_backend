"""DB-free tests for GET /version/check (planner P0-8). The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_version_check.py -q
"""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import version_function as vf  # noqa: E402
from router import version as version_router  # noqa: E402


class FakeCursor:
    def __init__(self, row=None, fail=False):
        self.row = row
        self.fail = fail
        self.sql = []
        self.closed = False

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("secret db detail")
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self, cursor_factory=None):
        return self.cur


def row(latest="1.0.0", minimum="1.0.0", force=False, message=None, url=None):
    return {"latest_version": latest, "min_supported": minimum, "force_update": force, "message": message, "store_url": url}


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.delenv("ANDROID_STORE_URL", raising=False)
    monkeypatch.delenv("IOS_STORE_URL", raising=False)

    def build(cursor):
        conn = FakeConn(cursor)
        returned = []
        monkeypatch.setattr(version_router, "get_db_connection", lambda: conn)
        monkeypatch.setattr(version_router, "return_connection", lambda c: returned.append(c))
        app = FastAPI()
        app.include_router(version_router.router)
        return TestClient(app), returned

    return build


def call(client, **params):
    return client.get("/version/check", params=params)


# ---- pure version logic ----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.0.0", (1, 0, 0)),
    ("1.0.10", (1, 0, 10)),
    ("2.1", (2, 1)),
    ("3", (3,)),
    ("v1.2.3", (1, 2, 3)),
    (" 1.0.0 ", (1, 0, 0)),
    ("1.0.0-beta.1", (1, 0, 0)),
    ("1.0.0+build5", (1, 0, 0)),
    ("garbage", None),
    ("", None),
    (".1", None),
    (None, None),
])
def test_parse_version(text, expected):
    assert vf.parse_version(text) == expected


@pytest.mark.parametrize("a,b,expected", [
    ("1.0.10", "1.0.9", 1),
    ("1.0.9", "1.0.10", -1),
    ("1.0.0", "1.0.0", 0),
    ("1.0", "1.0.0", 0),
    ("2.0", "1.0.9", 1),
    ("1.0", "1.0.1", -1),
    ("1.0.0", "1.0.34", -1),
    ("10.0", "9.9.9", 1),
])
def test_compare_versions(a, b, expected):
    assert vf.compare_versions(vf.parse_version(a), vf.parse_version(b)) == expected


def test_resolve_store_url(monkeypatch):
    monkeypatch.setenv("ANDROID_STORE_URL", " https://play.example/app ")
    monkeypatch.delenv("IOS_STORE_URL", raising=False)
    assert vf.resolve_store_url("https://row.example", "android") == "https://row.example"
    assert vf.resolve_store_url(None, "android") == "https://play.example/app"
    assert vf.resolve_store_url("   ", "android") == "https://play.example/app"
    assert vf.resolve_store_url(None, "ios") is None
    assert vf.resolve_store_url(None, "web") is None


# ---- route ------------------------------------------------------------------

def test_up_to_date_shape_matches_contract(make_client):
    cur = FakeCursor(row("1.0.0", "1.0.0"))
    client, returned = make_client(cur)
    r = call(client, platform="android", version="1.0.0", app_id="com.clockyn.app")
    assert r.status_code == 200
    assert r.json() == {"success": True, "data": {
        "update_required": False, "latest_version": "1.0.0", "force_update": False, "message": None, "store_url": None}}
    assert cur.sql[0][1] == ("com.clockyn.app", "android")
    assert "FROM app_version_control WHERE app_id = %s AND platform = %s" in cur.sql[0][0]
    assert cur.closed and len(returned) == 1


def test_soft_update(make_client):
    client, _ = make_client(FakeCursor(row("1.0.10", "1.0.0", message="New version")))
    d = call(client, platform="ios", version="1.0.9", app_id="com.clockyn.app").json()["data"]
    assert d["update_required"] is True and d["force_update"] is False and d["message"] == "New version"


def test_double_digit_patch_is_newer(make_client):
    client, _ = make_client(FakeCursor(row("1.0.9", "1.0.0")))
    d = call(client, platform="ios", version="1.0.10", app_id="x").json()["data"]
    assert d["update_required"] is False


def test_force_below_min_supported(make_client):
    client, _ = make_client(FakeCursor(row("2.0", "1.5")))
    d = call(client, platform="android", version="1.4.9", app_id="x").json()["data"]
    assert d["update_required"] is True and d["force_update"] is True


def test_short_versions(make_client):
    client, _ = make_client(FakeCursor(row("1.1", "1.0")))
    d = call(client, platform="android", version="1.0", app_id="x").json()["data"]
    assert d["update_required"] is True and d["force_update"] is False
    client, _ = make_client(FakeCursor(row("1.0", "1.0")))
    d = call(client, platform="android", version="1.0.0", app_id="x").json()["data"]
    assert d["update_required"] is False


def test_force_flag_on_row_forces(make_client):
    client, _ = make_client(FakeCursor(row("1.0.5", "1.0.0", force=True)))
    d = call(client, platform="android", version="1.0.0", app_id="x").json()["data"]
    assert d["update_required"] is True and d["force_update"] is True


def test_force_flag_ignored_when_already_latest(make_client):
    client, _ = make_client(FakeCursor(row("1.0.5", "1.0.0", force=True)))
    d = call(client, platform="android", version="1.0.5", app_id="x").json()["data"]
    assert d["update_required"] is False and d["force_update"] is False


def test_legacy_request_without_app_id_uses_legacy_rows_and_lowercases_platform(make_client):
    cur = FakeCursor(row("1.0.34", "1.0.30"))
    client, _ = make_client(cur)
    d = call(client, platform="Android", version="1.0.0").json()["data"]
    assert cur.sql[0][1] == ("legacy", "android")
    assert d["update_required"] is True and d["force_update"] is True


def test_blank_app_id_means_legacy(make_client):
    cur = FakeCursor(None)
    client, _ = make_client(cur)
    call(client, platform="ios", version="1.0.0", app_id="  ")
    assert cur.sql[0][1] == ("legacy", "ios")


def test_missing_row_is_no_update_never_500(make_client):
    client, returned = make_client(FakeCursor(None))
    r = call(client, platform="android", version="1.0.0", app_id="unknown.app")
    assert r.status_code == 200
    assert r.json()["data"] == {
        "update_required": False, "latest_version": None, "force_update": False, "message": None, "store_url": None}
    assert len(returned) == 1


def test_unparsable_app_version_is_no_update(make_client):
    client, _ = make_client(FakeCursor(row("1.0.5", "1.0.0", force=True)))
    r = call(client, platform="android", version="not-a-version", app_id="x")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["update_required"] is False and d["force_update"] is False and d["latest_version"] == "1.0.5"


def test_unparsable_row_versions_do_not_raise(make_client):
    client, _ = make_client(FakeCursor(row("oops", "bad")))
    r = call(client, platform="android", version="1.0.0", app_id="x")
    assert r.status_code == 200
    assert r.json()["data"]["update_required"] is False and r.json()["data"]["force_update"] is False


def test_store_url_from_row_wins_then_env(make_client, monkeypatch):
    monkeypatch.setenv("ANDROID_STORE_URL", "https://play.example/clockyn")
    monkeypatch.setenv("IOS_STORE_URL", "https://apps.example/clockyn")
    client, _ = make_client(FakeCursor(row(url="https://row.example/a")))
    assert call(client, platform="android", version="1.0.0", app_id="x").json()["data"]["store_url"] == "https://row.example/a"
    client, _ = make_client(FakeCursor(row(url="")))
    assert call(client, platform="android", version="1.0.0", app_id="x").json()["data"]["store_url"] == "https://play.example/clockyn"
    client, _ = make_client(FakeCursor(row(url=None)))
    assert call(client, platform="ios", version="1.0.0", app_id="x").json()["data"]["store_url"] == "https://apps.example/clockyn"


def test_store_url_env_is_read_at_request_time(make_client, monkeypatch):
    client, _ = make_client(FakeCursor(row()))
    assert call(client, platform="android", version="1.0.0", app_id="x").json()["data"]["store_url"] is None
    monkeypatch.setenv("ANDROID_STORE_URL", "https://later.example")
    assert call(client, platform="android", version="1.0.0", app_id="x").json()["data"]["store_url"] == "https://later.example"


def test_missing_required_params_is_422(make_client):
    client, _ = make_client(FakeCursor(None))
    assert client.get("/version/check", params={"platform": "android"}).status_code == 422
    assert client.get("/version/check", params={"version": "1.0.0"}).status_code == 422


def test_db_error_is_generic_500_and_connection_returned(make_client):
    client, returned = make_client(FakeCursor(fail=True))
    r = call(client, platform="android", version="1.0.0", app_id="x")
    assert r.status_code == 500
    assert "secret" not in r.text
    assert len(returned) == 1
