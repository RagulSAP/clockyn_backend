"""DB-free tests for the platform hygiene helpers: global error handler + request id, IST time helpers,
add-staff id check, /health and /ready, UTC session time zone.
Run: python -m pytest tests/test_platform_hygiene.py -q
"""
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import errors, timezone as tz  # noqa: E402


# ---------------------------------------------------------------------------------------------------------------
# Global error handler + X-Request-ID
# ---------------------------------------------------------------------------------------------------------------
class _FakeCors(BaseHTTPMiddleware):
    """Stands in for app.py's custom CORS middleware (a BaseHTTPMiddleware that edits response headers)."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response


def make_app():
    app = FastAPI()
    app.add_middleware(_FakeCors)
    errors.install(app)

    @app.get("/ok")
    def ok():
        return {"a": 1}

    @app.get("/boom")
    def boom():
        raise RuntimeError("password=hunter2 connection refused 10.0.0.5")

    @app.get("/boom-async")
    async def boom_async():
        raise KeyError("internal_column")

    @app.get("/http")
    def http():
        raise HTTPException(status_code=404, detail="Branch not found")

    @app.get("/http-headers")
    def http_headers():
        raise HTTPException(status_code=429, detail="slow down", headers={"Retry-After": "5"})

    @app.get("/valid")
    def valid(n: int):
        return {"n": n}

    @app.get("/rid")
    def rid():
        return {"rid": errors.request_id_var.get()}

    return app


@pytest.fixture
def client():
    return TestClient(make_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/boom", "/boom-async"])
def test_unhandled_exception_is_a_clean_500(client, path):
    r = client.get(path)
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}
    assert "hunter2" not in r.text and "internal_column" not in r.text
    assert r.headers["x-request-id"]


def test_unhandled_exception_is_logged_with_traceback_and_request_id(client, caplog):
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        r = client.get("/boom", headers={"X-Request-ID": "req-123"})
    assert r.headers["x-request-id"] == "req-123"
    recs = [rec for rec in caplog.records if rec.name == "app.errors"]
    assert len(recs) == 1
    rec = recs[0]
    assert "req-123" in rec.getMessage() and "GET /boom" in rec.getMessage()
    assert rec.exc_info and rec.exc_info[0] is RuntimeError
    assert "hunter2" in caplog.text                    # the traceback is in the log, never in the response


def test_http_exception_and_validation_shapes_are_unchanged(client):
    r = client.get("/http")
    assert r.status_code == 404 and r.json() == {"detail": "Branch not found"}
    assert r.headers["x-request-id"]
    r = client.get("/http-headers")
    assert r.status_code == 429 and r.headers["retry-after"] == "5" and r.json() == {"detail": "slow down"}
    r = client.get("/valid", params={"n": "abc"})
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"] == ["query", "n"]
    assert client.get("/valid", params={"n": 3}).json() == {"n": 3}
    assert client.get("/missing").status_code == 404


def test_request_id_generated_reused_and_sanitised(client):
    r1 = client.get("/ok")
    r2 = client.get("/ok")
    assert len(r1.headers["x-request-id"]) == 32 and r1.headers["x-request-id"] != r2.headers["x-request-id"]
    assert client.get("/ok", headers={"X-Request-ID": "abc.DEF_1-2"}).headers["x-request-id"] == "abc.DEF_1-2"
    for bad in ("has space", "x" * 65, "semi;colon", "<script>"):
        got = client.get("/ok", headers={"X-Request-ID": bad}).headers["x-request-id"]
        assert got != bad and len(got) == 32
    # visible to handlers through the ContextVar, and the other middleware still ran
    r = client.get("/rid", headers={"X-Request-ID": "ctx-1"})
    assert r.json() == {"rid": "ctx-1"} and r.headers["access-control-allow-origin"] == "*"


def test_request_id_var_is_reset_after_the_request(client):
    client.get("/ok", headers={"X-Request-ID": "gone"})
    assert errors.request_id_var.get() == "-"


def test_clean_request_id_accepts_bytes():
    assert errors.clean_request_id(b"abc-1") == "abc-1"
    assert len(errors.clean_request_id(None)) == 32


# ---------------------------------------------------------------------------------------------------------------
# IST helpers
# ---------------------------------------------------------------------------------------------------------------
UTC = timezone.utc


@pytest.mark.parametrize("utc_dt,expected", [
    (datetime(2026, 5, 1, 18, 29, 59, tzinfo=UTC), date(2026, 5, 1)),     # 23:59:59 IST
    (datetime(2026, 5, 1, 18, 30, 0, tzinfo=UTC), date(2026, 5, 2)),      # 00:00:00 IST next day
    (datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC), date(2026, 5, 1)),
    (datetime(2026, 12, 31, 18, 30, tzinfo=UTC), date(2027, 1, 1)),       # year boundary
    (datetime(2026, 2, 28, 18, 30, tzinfo=UTC), date(2026, 3, 1)),        # month boundary
    (datetime(2026, 5, 1, 18, 30), date(2026, 5, 2)),                     # naive = UTC (how the DB returns it)
])
def test_ist_date_of(utc_dt, expected):
    assert tz.ist_date_of(utc_dt) == expected


def test_ist_date_of_none_and_non_utc_input():
    assert tz.ist_date_of(None) is None
    ist_input = datetime(2026, 5, 2, 0, 0, tzinfo=tz.IST)                  # same instant as 18:30 UTC the day before
    assert tz.ist_date_of(ist_input) == date(2026, 5, 2)


def test_to_ist():
    out = tz.to_ist(datetime(2026, 5, 1, 18, 29, tzinfo=UTC))
    assert (out.hour, out.minute, out.utcoffset().total_seconds()) == (23, 59, 19800)
    assert tz.to_ist(datetime(2026, 5, 1, 6, 0)).hour == 11               # naive taken as UTC
    assert tz.to_ist(None) is None


@pytest.mark.parametrize("now_utc,today", [
    (datetime(2026, 5, 1, 18, 29, 59, tzinfo=UTC), date(2026, 5, 1)),
    (datetime(2026, 5, 1, 18, 30, 0, tzinfo=UTC), date(2026, 5, 2)),
])
def test_ist_now_and_today_follow_the_clock(monkeypatch, now_utc, today):
    monkeypatch.setattr(tz, "get_utc_now", lambda: now_utc)
    assert tz.ist_today() == today
    n = tz.ist_now()
    assert n.tzinfo is not None and n.utcoffset().total_seconds() == 19800 and n.date() == today


def test_ist_now_is_aware_and_real():
    n = tz.ist_now()
    assert n.utcoffset().total_seconds() == 19800
    assert abs((n - datetime.now(UTC)).total_seconds()) < 5
    assert isinstance(tz.ist_today(), date)


def test_existing_helpers_keep_working():
    assert tz.convert_utc_to_ist(datetime(2026, 5, 1, 18, 30, tzinfo=UTC)).day == 2
    assert tz.convert_ist_to_utc(datetime(2026, 5, 2, 0, 0)).hour == 18
    assert tz.format_ist_time(datetime(2026, 5, 1, 6, 0, tzinfo=UTC)) == "11:30 AM"
    assert tz.get_utc_now().tzinfo is not None


# ---------------------------------------------------------------------------------------------------------------
# Add-staff id check
# ---------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("good", ["org-0001", "user_12", "A", "0b9f3c3e-6d5b-4f60-9c64-0a5d2f1e7a11", "x" * 64])
def test_validate_user_id_accepts(good):
    from core_functions.add_staff_function import validate_user_id
    assert validate_user_id(good) == good


@pytest.mark.parametrize("bad", ["", " ", "a b", "x" * 65, "a/b", "a;drop", "a'b", "a%20b", "ünï", "a.b", None, 5])
def test_validate_user_id_rejects(bad):
    from core_functions.add_staff_function import validate_user_id
    with pytest.raises(HTTPException) as e:
        validate_user_id(bad)
    assert e.value.status_code == 400 and e.value.detail == "Invalid user ID format"


def test_add_staff_routes_take_non_uuid_ids(monkeypatch):
    from router import add_staff as r
    seen = []

    async def fake_delete(user_id):
        seen.append(user_id)
        return {"ok": True}

    async def fake_branch_info(user_id, role_id):
        seen.append((user_id, role_id))
        return {"branch_id": "b1", "branch_name": "HQ"}

    monkeypatch.setattr(r, "delete_staff", fake_delete)
    monkeypatch.setattr(r, "get_branch_info", fake_branch_info)
    app = FastAPI()
    app.include_router(r.router)
    c = TestClient(app)
    assert c.delete("/addstaff/org-0001").status_code == 200
    assert c.delete("/addstaff/0b9f3c3e-6d5b-4f60-9c64-0a5d2f1e7a11").status_code == 200
    bad = c.delete("/addstaff/bad%20id")
    assert bad.status_code == 400 and bad.json() == {"detail": "Invalid user ID format"}
    assert c.delete("/addstaff/" + "x" * 65).status_code == 400
    assert c.get("/addstaff/branch-info/org-0001", params={"role_id": 1}).status_code == 200
    assert seen == ["org-0001", "0b9f3c3e-6d5b-4f60-9c64-0a5d2f1e7a11", ("org-0001", 1)]


# ---------------------------------------------------------------------------------------------------------------
# /health and /ready
# ---------------------------------------------------------------------------------------------------------------
class _Cur:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.sql.append(sql)
        if self.conn.fail_select:
            raise RuntimeError("server closed the connection unexpectedly")

    def fetchone(self):
        return (1,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, fail_select=False):
        self.fail_select = fail_select
        self.sql = []
        self.rollbacks = 0

    def cursor(self):
        return _Cur(self)

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def health_client(monkeypatch):
    from router import health
    state = {"returned": [], "conn": _Conn(), "get_error": None, "gets": 0}

    def fake_get():
        state["gets"] += 1
        if state["get_error"]:
            raise state["get_error"]
        return state["conn"]

    monkeypatch.setattr(health, "get_db_connection", fake_get)
    monkeypatch.setattr(health, "return_connection", lambda c: state["returned"].append(c))
    app = FastAPI()
    app.include_router(health.router)
    return TestClient(app), state


def test_health_is_always_ok_and_never_touches_the_db(health_client):
    c, state = health_client
    state["get_error"] = HTTPException(status_code=500, detail="Database connection failed")
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"} and state["gets"] == 0


def test_ready_ok_runs_select_1_and_returns_the_connection(health_client):
    c, state = health_client
    r = c.get("/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
    assert state["conn"].sql == ["SELECT 1"] and state["returned"] == [state["conn"]]


def test_ready_503_when_no_connection(health_client):
    c, state = health_client
    state["get_error"] = HTTPException(status_code=500, detail="Database connection failed")
    r = c.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"} and state["returned"] == []


def test_ready_503_when_select_fails_and_connection_is_still_returned(health_client):
    c, state = health_client
    state["conn"] = _Conn(fail_select=True)
    r = c.get("/ready")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert state["returned"] == [state["conn"]]


def test_health_routes_are_mounted_in_app_py():
    src = (ROOT / "app.py").read_text(encoding="utf-8-sig")
    assert "app.include_router(health.router)" in src and "_install_error_handling(app)" in src
    assert "run_startup_migrations" in src


# ---------------------------------------------------------------------------------------------------------------
# DB session time zone
# ---------------------------------------------------------------------------------------------------------------
def test_db_config_pins_utc_and_search_path(monkeypatch):
    from core_functions import db_function as dbf
    monkeypatch.setenv("DB_SCHEMA", "clockyn_dev")
    opts = dbf.get_db_config()["options"]
    assert "-c timezone=UTC" in opts and "-c search_path=clockyn_dev" in opts
