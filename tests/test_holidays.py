"""DB-free tests for the holiday / weekly-off endpoints (router/holiday.py is the single owner of /api/holidays*).
The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_holidays.py -q
"""
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import holiday_functions as hf  # noqa: E402
from router import holiday as holiday_router  # noqa: E402


class FakeCursor:
    """fetchone()/fetchall()/rowcount hand out scripted values in order; every statement is recorded."""

    def __init__(self, one=None, many=None, rowcounts=None):
        self.sql = []
        self.one = list(one or [])
        self.many = list(many or [])
        self.rowcounts = list(rowcounts or [])
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        if sql.lstrip().upper().startswith(("UPDATE", "DELETE")):
            self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 0

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.many.pop(0) if self.many else []

    def close(self):
        pass

    def statements(self, prefix):
        return [(s, p) for s, p in self.sql if s.upper().startswith(prefix)]


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def use_db(monkeypatch, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(hf, "get_db_connection", lambda: conn)
    monkeypatch.setattr(hf, "return_connection", lambda c: None)
    return conn


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(holiday_router.router)
    return TestClient(app)


# ------------------------------------------------------------------------------------------------ list
def test_list_returns_the_contract_shape_with_block_attendance(monkeypatch, client):
    cur = FakeCursor(many=[[
        (1, "Diwali", date(2026, 11, 8), None, None, True),
        (2, "Founders day", date(2026, 11, 20), "Chennai", "Sales", False),
        (3, "Legacy row", date(2026, 11, 21), None, None, None),   # NULL flag counts as blocking
    ]])
    use_db(monkeypatch, cur)
    body = client.get("/api/holidays/org1", params={"year": 2026, "month": 11}).json()
    assert body["success"] is True
    assert body["data"] == [
        {"id": 1, "name": "Diwali", "date": "2026-11-08", "branch_name": None, "dept_name": None, "block_attendance": True},
        {"id": 2, "name": "Founders day", "date": "2026-11-20", "branch_name": "Chennai", "dept_name": "Sales", "block_attendance": False},
        {"id": 3, "name": "Legacy row", "date": "2026-11-21", "branch_name": None, "dept_name": None, "block_attendance": True},
    ]
    sql, params = cur.sql[0]
    assert "FROM org_holidays WHERE org_id = %s" in sql and params == ("org1", 2026, 11)


def test_list_without_year_month_defaults_to_the_current_month(monkeypatch, client):
    cur = FakeCursor(many=[[]])
    use_db(monkeypatch, cur)
    assert client.get("/api/holidays/org1").json() == {"success": True, "data": []}
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    assert cur.sql[0][1] == ("org1", today.year, today.month)


def test_list_rejects_an_invalid_month(monkeypatch, client):
    use_db(monkeypatch, FakeCursor())
    assert client.get("/api/holidays/org1", params={"year": 2026, "month": 13}).status_code == 422


# ------------------------------------------------------------------------------------------------ create
def test_create_defaults_to_blocking_and_returns_the_id(monkeypatch, client):
    cur = FakeCursor(one=[None, (41,)])          # no duplicate, then RETURNING id
    conn = use_db(monkeypatch, cur)
    res = client.post("/api/holidays/org1", json={"name": " Diwali ", "date": "2026-11-08"})
    assert res.status_code == 200
    assert res.json() == {"success": True, "id": 41, "message": "Holiday added successfully"}
    insert = cur.statements("INSERT")[0]
    assert insert[1] == ("org1", "Diwali", "2026-11-08", None, None, True)
    assert conn.commits == 1


def test_create_round_trips_block_attendance_false_and_scope(monkeypatch, client):
    cur = FakeCursor(one=[None, (42,)])
    use_db(monkeypatch, cur)
    res = client.post("/api/holidays/org1", json={
        "name": "Branch day", "date": "2026-11-09", "branch_name": "Chennai", "dept_name": "  ", "block_attendance": False})
    assert res.json()["id"] == 42
    assert cur.statements("INSERT")[0][1] == ("org1", "Branch day", "2026-11-09", "Chennai", None, False)


def test_create_refuses_a_duplicate_date_and_scope(monkeypatch, client):
    cur = FakeCursor(one=[(1,)])
    conn = use_db(monkeypatch, cur)
    res = client.post("/api/holidays/org1", json={"name": "Diwali", "date": "2026-11-08", "branch_name": "Chennai"}).json()
    assert res["success"] is False and "already exists" in res["message"]
    assert not cur.statements("INSERT") and conn.commits == 0
    # the scope is part of the duplicate probe: NULL scope compares as ''
    assert cur.sql[0][1] == ("org1", "2026-11-08", "Chennai", "")


@pytest.mark.parametrize("payload", [
    {"name": "   ", "date": "2026-11-08"},
    {"name": "Diwali", "date": "08-11-2026"},
    {"name": "Diwali", "date": "2026-02-30"},
    {"name": "Diwali", "date": ""},
])
def test_create_validates_name_and_date(monkeypatch, client, payload):
    cur = FakeCursor()
    use_db(monkeypatch, cur)
    res = client.post("/api/holidays/org1", json=payload)
    assert res.status_code == 422
    assert cur.sql == []


# ------------------------------------------------------------------------------------------------ update
def test_update_toggles_block_attendance_and_renames(monkeypatch, client):
    cur = FakeCursor(one=[(None, None)], rowcounts=[1])
    conn = use_db(monkeypatch, cur)
    res = client.put("/api/holidays/org1/7", json={"name": "Diwali Day", "block_attendance": False})
    assert res.json() == {"success": True, "message": "Holiday updated"}
    sql, params = cur.statements("UPDATE")[0]
    assert sql == "UPDATE org_holidays SET name = %s, block_attendance = %s WHERE id = %s AND org_id = %s"
    assert params == ["Diwali Day", False, 7, "org1"]
    assert conn.commits == 1


def test_update_without_block_attendance_leaves_the_flag_alone(monkeypatch, client):
    cur = FakeCursor(one=[(None, None)], rowcounts=[1])
    use_db(monkeypatch, cur)
    client.put("/api/holidays/org1/7", json={"name": "Renamed"})
    assert "block_attendance" not in cur.statements("UPDATE")[0][0]


def test_update_of_a_holiday_of_another_org_is_not_found(monkeypatch, client):
    cur = FakeCursor(one=[None])                  # SELECT ... WHERE id AND org_id finds nothing
    conn = use_db(monkeypatch, cur)
    res = client.put("/api/holidays/org-A/7", json={"name": "Hijack", "block_attendance": False}).json()
    assert res == {"success": False, "message": "Holiday not found"}
    assert not cur.statements("UPDATE") and conn.commits == 0
    assert cur.sql[0][1] == (7, "org-A")


def test_update_date_onto_an_existing_holiday_is_refused(monkeypatch, client):
    cur = FakeCursor(one=[("Chennai", None), (1,)])
    use_db(monkeypatch, cur)
    res = client.put("/api/holidays/org1/7", json={"name": "Diwali", "date": "2026-11-09"}).json()
    assert res["success"] is False and "already exists" in res["message"]
    assert not cur.statements("UPDATE")
    assert cur.sql[1][1] == ("org1", "2026-11-09", 7, "Chennai", "")


def test_update_validates_input(monkeypatch, client):
    cur = FakeCursor()
    use_db(monkeypatch, cur)
    assert client.put("/api/holidays/org1/7", json={"name": " "}).status_code == 422
    assert client.put("/api/holidays/org1/7", json={"name": "x", "date": "nope"}).status_code == 422
    assert cur.sql == []


# ------------------------------------------------------------------------------------------------ delete
def test_delete_is_scoped_to_the_org(monkeypatch, client):
    cur = FakeCursor(rowcounts=[1])
    conn = use_db(monkeypatch, cur)
    assert client.delete("/api/holidays/org1/7").json() == {"success": True, "message": "Holiday deleted"}
    assert cur.sql[0] == ("DELETE FROM org_holidays WHERE id = %s AND org_id = %s", (7, "org1"))
    assert conn.commits == 1


def test_delete_of_a_holiday_of_another_org_is_not_found(monkeypatch, client):
    cur = FakeCursor(rowcounts=[0])
    conn = use_db(monkeypatch, cur)
    assert client.delete("/api/holidays/org-A/7").json() == {"success": False, "message": "Holiday not found"}
    assert conn.commits == 0


# ------------------------------------------------------------------------------------------------ weekly offs
def test_off_days_default_to_sunday(monkeypatch, client):
    use_db(monkeypatch, FakeCursor(one=[None]))
    assert client.get("/api/holidays/off-days/org1").json() == {"success": True, "off_days": [6]}


def test_off_days_are_validated_deduplicated_and_sorted(monkeypatch, client):
    cur = FakeCursor()
    conn = use_db(monkeypatch, cur)
    res = client.post("/api/holidays/off-days/org1", json={"off_days": [6, 5, 6, 0]}).json()
    assert res["success"] is True and res["message"] == "Off days updated" and res["off_days"] == [0, 5, 6]
    assert cur.sql[0][1] == ("org1", [0, 5, 6]) and conn.commits == 1


def test_off_days_outside_0_to_6_are_refused(monkeypatch, client):
    cur = FakeCursor()
    use_db(monkeypatch, cur)
    assert client.post("/api/holidays/off-days/org1", json={"off_days": [7]}).status_code == 422
    assert client.post("/api/holidays/off-days/org1", json={"off_days": [-1]}).status_code == 422
    assert cur.sql == []


def test_working_days_still_served(monkeypatch, client):
    cur = FakeCursor(one=[([6],), (2,)])          # off_days = Sundays, then 2 holidays
    use_db(monkeypatch, cur)
    body = client.get("/api/holidays/working-days/org1", params={"year": 2026, "month": 11}).json()
    assert body["total_days"] == 30 and body["off_days"] == 5 and body["holidays"] == 2 and body["working_days"] == 23


# ------------------------------------------------------------------------------------------------ face punch flow
def test_check_holiday_block_uses_the_ist_date_and_branch_scope(monkeypatch):
    cur = FakeCursor(one=[("Chennai", "Sales"), ("Diwali",)])
    use_db(monkeypatch, cur)
    assert hf.check_holiday_block("org1", "u1") == "Diwali"
    sql, params = cur.sql[1]
    assert "block_attendance = TRUE" in sql and "CURRENT_DATE" not in sql
    assert params == ("org1", datetime.now(ZoneInfo("Asia/Kolkata")).date(), "Chennai", "Sales")


def test_check_holiday_block_returns_none_without_a_holiday(monkeypatch):
    use_db(monkeypatch, FakeCursor(one=[("Chennai", None), None]))
    assert hf.check_holiday_block("org1", "u1") is None


# ------------------------------------------------------------------------------------------------ one owner
def test_holiday_routes_have_one_owner():
    """The other routers no longer declare /api/holidays (static check; the route-table test covers duplicates)."""
    assert not (ROOT / "router" / "daily_hourly.py").exists()
    assert "holidays" not in (ROOT / "router" / "report.py").read_text(encoding="utf-8")
    assert "daily_hourly" not in (ROOT / "app.py").read_text(encoding="utf-8").split("include_router")[0]
