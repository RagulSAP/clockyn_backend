"""DB-free tests of the clickstream: what is accepted, what is cleaned, how funnels are counted, how the routes are classified.

The database layer is replaced by a scripted fake. Run: python -m pytest tests/test_analytics.py -q
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import analytics_functions as af  # noqa: E402
from core_functions import auth_policy as policy  # noqa: E402

NOW = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)
SESSION = "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"


def batch(events, **kw):
    return af.EventBatch(session_id=kw.pop("session_id", SESSION), events=[af.AppEvent(**e) for e in events], **kw)


# ---------------------------------------------------------------------------------------------- cleaning
def test_clean_text_trims_collapses_and_caps():
    assert af.clean_text("  Add \n\t Staff  ", 80) == "Add Staff"
    assert af.clean_text("x" * 500, 80) == "x" * 80
    assert af.clean_text("   ", 80) is None
    assert af.clean_text(None, 80) is None


@pytest.mark.parametrize("text", ["Call priya@acme.in", "Send to 9876543210", "Employee 123456 saved"])
def test_clean_text_redacts_personal_looking_text(text):
    assert af.clean_text(text, 80) == af.REDACTED


def test_clean_text_keeps_short_numbers():
    assert af.clean_text("Step 3 of 5", 80) == "Step 3 of 5"


# ---------------------------------------------------------------------------------------------- event time
def test_clamp_event_time():
    ok = int((NOW - timedelta(minutes=2)).timestamp() * 1000)
    assert af.clamp_event_time(ok, NOW) == NOW - timedelta(minutes=2)
    assert af.clamp_event_time(None, NOW) == NOW
    assert af.clamp_event_time(int((NOW - timedelta(days=3)).timestamp() * 1000), NOW) == NOW   # too old
    assert af.clamp_event_time(int((NOW + timedelta(hours=1)).timestamp() * 1000), NOW) == NOW  # clock ahead
    assert af.clamp_event_time(float("nan"), NOW) == NOW
    assert af.clamp_event_time(1e30, NOW) == NOW


# ---------------------------------------------------------------------------------------------- rows
def test_build_rows_keeps_valid_events_only():
    b = batch(
        [
            {"type": "session", "name": "start"},
            {"type": "screen", "name": "/admin/staff/edit"},
            {"type": "click", "name": "Add Staff", "screen": "/admin/staff"},
            {"type": "screen", "name": "   "},          # empty
            {"type": "click"},                           # no label
            {"type": "scroll", "name": "x"},             # unknown type
        ],
        platform="android", app_version="1.0.0",
    )
    rows = af.build_rows(b, "u1", "o1", 1, NOW)
    assert [(r[7], r[8], r[9]) for r in rows] == [
        ("session", None, None),
        ("screen", "/admin/staff/edit", None),
        ("click", "/admin/staff", "Add Staff"),
    ]
    assert all(r[1] == SESSION and r[2] == "u1" and r[3] == "o1" and r[4] == 1 and r[5] == "android" and r[6] == "1.0.0" for r in rows)


def test_build_rows_drops_bad_platform_version_and_role():
    rows = af.build_rows(batch([{"type": "screen", "name": "/x"}], platform="symbian", app_version="1 0;DROP"), "u", "o", 9, NOW)
    assert rows[0][4] is None and rows[0][5] is None and rows[0][6] is None


def test_build_rows_caps_the_batch():
    events = [{"type": "screen", "name": f"/s{i}"} for i in range(af.MAX_BATCH + 25)]
    assert len(af.build_rows(batch(events), "u", "o", 3, NOW)) == af.MAX_BATCH


def test_build_rows_redacts_click_labels():
    rows = af.build_rows(batch([{"type": "click", "name": "Call 9876543210", "screen": "/admin/staff"}]), "u", "o", 1, NOW)
    assert rows[0][9] == af.REDACTED


# ---------------------------------------------------------------------------------------------- intake
class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self):
        self.committed = False
        self.rolled_back = False

    def cursor(self, **kw):
        return FakeCursor()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def fake_db(monkeypatch):
    conn = FakeConn()
    inserted = []
    monkeypatch.setattr(af, "get_db_connection", lambda: conn)
    monkeypatch.setattr(af, "return_connection", lambda c: None)
    monkeypatch.setattr(af, "execute_values", lambda cur, sql, rows: inserted.extend(rows))
    return conn, inserted


def run(coro):
    return asyncio.run(coro)


def test_record_events_uses_the_verified_identity_not_the_body(fake_db):
    conn, inserted = fake_db
    identity = SimpleNamespace(user_id="real-user", org_id="real-org", role_id=2)
    out = run(af.record_events(batch([{"type": "screen", "name": "/admin/home"}], user_id="spoof", org_id="spoof", role_id=1), identity))
    assert out == {"success": True, "stored": 1}
    assert conn.committed
    assert inserted[0][2:5] == ("real-user", "real-org", 2)


def test_record_events_falls_back_to_body_ids_without_identity(fake_db):
    _, inserted = fake_db
    run(af.record_events(batch([{"type": "screen", "name": "/staff/home"}], user_id="u9", org_id="o9", role_id=3), None))
    assert inserted[0][2:5] == ("u9", "o9", 3)


def test_record_events_needs_a_user_and_a_valid_session(fake_db):
    with pytest.raises(HTTPException) as e:
        run(af.record_events(batch([{"type": "screen", "name": "/x"}]), None))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        run(af.record_events(batch([{"type": "screen", "name": "/x"}], session_id="bad id!"), SimpleNamespace(user_id="u", org_id="o", role_id=1)))
    assert e.value.status_code == 400


def test_record_events_with_nothing_valid_stores_nothing(fake_db):
    conn, inserted = fake_db
    out = run(af.record_events(batch([{"type": "nope", "name": "x"}]), SimpleNamespace(user_id="u", org_id="o", role_id=1)))
    assert out["stored"] == 0 and inserted == [] and not conn.committed


def test_record_events_swallows_a_missing_table(fake_db, monkeypatch):
    class Missing(Exception):
        pgcode = "42P01"

    def boom(cur, sql, rows):
        raise Missing('relation "app_event" does not exist')

    monkeypatch.setattr(af, "execute_values", boom)
    out = run(af.record_events(batch([{"type": "screen", "name": "/x"}]), SimpleNamespace(user_id="u", org_id="o", role_id=1)))
    assert out == {"success": True, "stored": 0}


# ---------------------------------------------------------------------------------------------- funnels
def t(minutes):
    return NOW + timedelta(minutes=minutes)


def test_funnel_counts_in_order():
    rows = [
        (t(0), t(1), t(2)),      # reaches all three
        (t(0), t(1), None),      # stops at step 2
        (t(0), None, t(5)),      # skipped step 2: counts only step 1
        (None, t(1), t(2)),      # never saw step 1: not counted anywhere
        (t(5), t(1), t(9)),      # step 2 happened BEFORE step 1: stops after step 1
    ]
    assert af.funnel_counts(rows, 3) == [4, 2, 1]


def test_funnel_counts_empty():
    assert af.funnel_counts([], 3) == [0, 0, 0]


def test_funnel_definitions_are_sane():
    keys = [f["key"] for f in af.FUNNELS]
    assert len(keys) == len(set(keys))
    for fn in af.FUNNELS:
        assert len(fn["steps"]) >= 2
        for label, screen in fn["steps"]:
            assert label and screen.startswith("/")


def test_step_condition_uses_like_only_for_prefixes():
    assert af._step_condition("/admin/staff") == ("screen = %s", "/admin/staff")
    assert af._step_condition("/admin/help/%") == ("screen LIKE %s", "/admin/help/%")


def test_filters_ignore_unknown_values():
    assert af._filters("android", 1) == (" AND platform = %s AND role_id = %s", ["android", 1])
    assert af._filters("toaster", 7) == ("", [])


def test_purge_refuses_short_retention():
    with pytest.raises(HTTPException) as e:
        run(af.purge_clickstream(7))
    assert e.value.status_code == 400


# ---------------------------------------------------------------------------------------------- routes
def test_route_classes():
    assert policy.classify("POST", "/analytics/events") == policy.AUTHENTICATED
    for path in ("/gen-dashboard/clickstream/summary", "/gen-dashboard/clickstream/events", "/gen-dashboard/clickstream/purge"):
        assert policy.classify("GET", path) == policy.OPERATOR


def test_analytics_route_is_open_to_every_role():
    # No ROLE_RULES entry narrows /analytics/events: admins, managers and staff all report their usage.
    assert not any(r.pattern.startswith("/analytics") for r in policy.ROLE_RULES)
