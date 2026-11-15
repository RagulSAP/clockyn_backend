"""The dashboard's "expiring subscriptions" numbers (GET /gen-dashboard/stats -> expiring).
They must follow the same rule as the Organizations page filter: the LATEST subscription of a non-deleted organisation is ACTIVE and
ends today / within 7 / within 30 days (IST, today included). DB-free: the cursor is a script.
Run: python -m pytest tests/test_dashboard_expiring.py -q
"""
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import gen_dashboard_functions as dash  # noqa: E402

TODAY = date(2026, 11, 15)


class Cur:
    def __init__(self, expiring=None):
        self.expiring, self.log, self.row = expiring, [], {"cnt": 0, "rev": 0}

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.log.append((s, params))
        self.row = ({"today": 2, "within_7_days": 5, "within_30_days": 9} if self.expiring is None else self.expiring) \
            if "AS within_30_days" in s else {"cnt": 0, "rev": 0}

    def fetchone(self):
        return self.row

    def close(self):
        pass


class Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, cursor_factory=None):
        return self.cur


def stats(monkeypatch, cur):
    monkeypatch.setattr(dash, "get_db_connection", lambda: Conn(cur))
    monkeypatch.setattr(dash, "return_connection", lambda c: None)
    monkeypatch.setattr(dash, "ist_today", lambda: TODAY)
    return asyncio.run(dash.get_super_admin_stats())


def test_the_stats_carry_the_three_expiry_counts_and_the_date_they_are_for(monkeypatch):
    out = stats(monkeypatch, Cur())
    assert out["expiring"] == {"today": 2, "within_7_days": 5, "within_30_days": 9, "as_of": "2026-11-15"}


def test_the_windows_are_ist_today_plus_seven_and_thirty_days(monkeypatch):
    cur = Cur()
    stats(monkeypatch, cur)
    sql, params = next(x for x in cur.log if "AS within_30_days" in x[0])
    assert params == (TODAY, TODAY + timedelta(days=7), TODAY, TODAY + timedelta(days=30))


def test_the_rule_matches_the_organizations_filter(monkeypatch):
    cur = Cur()
    stats(monkeypatch, cur)
    sql = next(x[0] for x in cur.log if "AS within_30_days" in x[0])
    assert "ORDER BY created_at DESC LIMIT 1" in sql                    # the LATEST subscription, like the organisations list
    assert "sub.status = 'ACTIVE'" in sql                               # an ACTIVE plan that has not run out
    assert "COALESCE(o.status, 1) <> 2" in sql                          # deleted organisations are not customers to chase
    assert "sub.end_date BETWEEN %s AND %s" in sql                      # today included, nothing in the past


def test_a_missing_row_gives_zeros(monkeypatch):
    out = stats(monkeypatch, Cur(expiring={}))
    assert out["expiring"]["today"] == 0 and out["expiring"]["within_30_days"] == 0
