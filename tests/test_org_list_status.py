"""The console's organisation list must report a lapsed paid plan as EXPIRED even when nobody from that org has opened
the app since it lapsed (the stored status only flips to EXPIRED lazily, on a per-org read)."""
import asyncio
import re
from datetime import date

import pytest

from core_functions import gen_dashboard_functions as gdf

TODAY = date(2026, 11, 11)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **_kw):
        return self.cur


@pytest.fixture
def run(monkeypatch):
    def _run(fn, rows):
        cur = FakeCursor(rows)
        monkeypatch.setattr(gdf, "get_db_connection", lambda: FakeConn(cur))
        monkeypatch.setattr(gdf, "return_connection", lambda _c: None)
        monkeypatch.setattr(gdf, "ist_today", lambda: TODAY)
        return asyncio.run(fn()), cur
    return _run


def _org_row(**over):
    row = {
        "org_id": "o1", "org_name": "Acme", "org_address": None, "org_gst": None, "state": None, "district": None,
        "pincode": None, "created_at": None, "joined_at": None, "email": None, "mobile_number": None, "emp_count": 3,
        "attendance_activity_count": 0, "sub_status": "EXPIRED", "plan_name": "Pro", "start_date": None,
        "end_date": date(2026, 11, 1), "free_trial_end_date": None, "payment_status": "unpaid", "org_status": 1,
    }
    row.update(over)
    return row


def test_all_organizations_sql_expires_a_lapsed_active_plan_and_binds_both_dates(run):
    result, cur = run(gdf.get_all_organizations, [_org_row()])
    sql, params = cur.calls[0]
    # the status shown is derived from the end date, not just the stored value
    assert re.search(r"CASE WHEN status = 'ACTIVE' AND end_date < %s THEN 'EXPIRED' ELSE status END AS status", sql)
    # one placeholder for the expiry date, one for the attendance date, both IST today
    assert sql.count("%s") == 2 and params == (TODAY, TODAY)
    # payment_status keys off the derived status, so a lapsed plan reads unpaid
    assert "WHEN sub.status = 'ACTIVE' THEN 'paid'" in sql
    assert result[0]["sub_status"] == "EXPIRED"


def test_recent_orgs_sql_expires_a_lapsed_active_plan(run):
    result, cur = run(gdf.get_recent_orgs, [{
        "org_id": "o1", "org_name": "Acme", "created_at": None, "user_count": 1, "sub_status": "EXPIRED",
        "plan_name": "Pro", "end_date": date(2026, 11, 1)}])
    sql, params = cur.calls[0]
    assert re.search(r"CASE WHEN status = 'ACTIVE' AND end_date < %s THEN 'EXPIRED' ELSE status END AS status", sql)
    assert sql.count("%s") == 1 and params == (TODAY,)
    assert result[0]["sub_status"] == "EXPIRED"
