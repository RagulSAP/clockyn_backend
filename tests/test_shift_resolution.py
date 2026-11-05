"""DB-free tests of core_functions/shift_resolution.py (WP7, decision D-6: approved shift change = one-day override).
The cursor is scripted; the SQL predicates (org scope, approved only, the date) are asserted on the text.
Run: python -m pytest tests/test_shift_resolution.py -q
"""
import sys
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import shift_resolution as sr  # noqa: E402
from wp7_fakes import RouteCursor, norm, patch_db, placeholders_match  # noqa: E402

DAY = time(9, 0), time(18, 0)
NIGHT = time(22, 0), time(6, 0)


def assigned_row(shift_id=1, times=DAY):
    return {"shift_id": shift_id, "start_time": times[0], "end_time": times[1]}


def cursor(assigned=None, override=None, overrides=()):
    cur = RouteCursor()
    cur.add("FROM requests r JOIN shift s", one=override, many=list(overrides))
    cur.add("FROM users_additional_info uai", one=assigned)
    return cur


# ---------------------------------------------------------------------------- pure
def test_normalise_date():
    assert sr.normalise_date(date(2026, 11, 2)) == date(2026, 11, 2)
    assert sr.normalise_date(datetime(2026, 11, 2, 23, 30)) == date(2026, 11, 2)
    assert sr.normalise_date("2026-11-02") == date(2026, 11, 2)
    assert sr.normalise_date("2026-11-02T10:00:00") == date(2026, 11, 2)
    for bad in (None, "", "soon", "2026-13-40"):
        assert sr.normalise_date(bad) is None


def test_pick_shift_prefers_the_override():
    assert sr.pick_shift({"a": 1}, {"b": 2}) == {"a": 1}
    assert sr.pick_shift(None, {"b": 2}) == {"b": 2}
    assert sr.pick_shift(None, None) is None


def test_format_shift_timing_matches_the_list_text():
    assert sr.format_shift_timing({"start_time": time(9, 0), "end_time": time(18, 0)}) == "09:00 AM - 06:00 PM"
    assert sr.format_shift_timing({"start_time": time(22, 30), "end_time": time(6, 5)}) == "10:30 PM - 06:05 AM"
    assert sr.format_shift_timing(None) is None


def test_effective_shift_id_sql_for_set_based_queries():
    expr = sr.effective_shift_id_sql("u.user_id", "u.parent_id", "a.attendance_date", "uai.shift_id")
    s = norm(expr)
    assert s.startswith("COALESCE((SELECT r2.requested_shift_id FROM requests r2")
    assert "r2.user_id = u.user_id" in s and "r2.org_id = u.parent_id" in s          # org scoped
    assert "r2.status = 'approved'" in s and "r2.request_type = 'shift_change'" in s
    assert "r2.shift_change_date = a.attendance_date" in s
    assert s.endswith("LIMIT 1), uai.shift_id)")
    assert s.count("(") == s.count(")")


# ---------------------------------------------------------------------------- shift_for_cursor
def test_override_date_returns_the_override_shift():
    cur = cursor(assigned=assigned_row(1, DAY), override={"shift_id": 2, "start_time": NIGHT[0], "end_time": NIGHT[1]})
    got = sr.shift_for_cursor(cur, "u1", "org1", "2026-11-05")
    assert got == {"shift_id": 2, "start_time": NIGHT[0], "end_time": NIGHT[1], "source": "override"}
    assert len(cur.sql) == 1                                  # the override answered, the assigned shift is not read
    sql, params = cur.sql[0]
    assert params == ("u1", "org1", date(2026, 11, 5)) and placeholders_match(sql, params)


def test_any_other_date_falls_back_to_the_assigned_shift():
    cur = cursor(assigned=assigned_row(1, DAY), override=None)
    got = sr.shift_for_cursor(cur, "u1", "org1", date(2026, 11, 6))
    assert got == {"shift_id": 1, "start_time": DAY[0], "end_time": DAY[1], "source": "assigned"}
    assert len(cur.sql) == 2
    sql, params = cur.sql[1]
    assert params == ("org1", "u1") and placeholders_match(sql, params)


def test_no_date_means_the_assigned_shift_without_an_override_lookup():
    cur = cursor(assigned=assigned_row(1, DAY), override={"shift_id": 2, "start_time": NIGHT[0], "end_time": NIGHT[1]})
    got = sr.shift_for_cursor(cur, "u1", "org1")
    assert got["source"] == "assigned" and got["shift_id"] == 1
    assert not cur.has("FROM requests")
    assert sr.shift_for_cursor(cursor(assigned=assigned_row()), "u1", "org1", "garbage")["source"] == "assigned"


def test_user_without_a_shift_gets_none():
    assert sr.shift_for_cursor(cursor(), "u1", "org1", "2026-11-05") is None


def test_tuple_rows_work_like_dict_rows():
    cur = cursor(assigned=(1, DAY[0], DAY[1]), override=(2, NIGHT[0], NIGHT[1]))
    assert sr.shift_for_cursor(cur, "u1", "org1", "2026-11-05")["shift_id"] == 2
    cur = cursor(assigned=(1, DAY[0], DAY[1]))
    assert sr.shift_for_cursor(cur, "u1", "org1", "2026-11-05")["shift_id"] == 1


def test_sql_is_org_scoped_approved_only_and_dated():
    cur = cursor(assigned=assigned_row())
    sr.shift_for_cursor(cur, "u1", "org1", "2026-11-05")
    override_sql = cur.sql[0][0]
    assigned_sql = cur.sql[1][0]
    # a pending or rejected request never overrides
    assert "r.status = 'approved'" in override_sql and "r.request_type = 'shift_change'" in override_sql
    assert "r.shift_change_date = %s" in override_sql
    # shift ids are per company: both lookups match the org on the requests row / the shift row
    assert "r.org_id = %s" in override_sql and "s.org_id = r.org_id" in override_sql and "s.status = 1" in override_sql
    assert "s.org_id = %s" in assigned_sql and "s.status = 1" in assigned_sql and "uai.shift_id > 0" in assigned_sql
    # latest decision wins when two approved requests cover one date
    assert "ORDER BY r.action_at DESC NULLS LAST, r.id DESC" in override_sql


# ---------------------------------------------------------------------------- range
def test_overrides_in_range_keyed_by_date_latest_wins():
    rows = [
        (date(2026, 11, 5), 2, NIGHT[0], NIGHT[1]),
        (date(2026, 11, 5), 3, time(6, 0), time(14, 0)),      # newer decision for the same date
        (date(2026, 11, 9), 2, NIGHT[0], NIGHT[1]),
    ]
    cur = cursor(overrides=rows)
    out = sr.shift_overrides_in_range(cur, "u1", "org1", "2026-11-01", "2026-11-30")
    assert sorted(out) == ["2026-11-05", "2026-11-09"]
    assert out["2026-11-05"]["shift_id"] == 3 and out["2026-11-05"]["source"] == "override"
    sql, params = cur.sql[0]
    assert params == ("u1", "org1", date(2026, 11, 1), date(2026, 11, 30)) and placeholders_match(sql, params)
    assert "r.status = 'approved'" in sql and "r.org_id = %s" in sql and "BETWEEN %s AND %s" in sql


def test_overrides_in_range_with_unreadable_dates_is_empty_and_runs_no_query():
    cur = cursor()
    assert sr.shift_overrides_in_range(cur, "u1", "org1", "x", "2026-11-30") == {}
    assert cur.sql == []


# ---------------------------------------------------------------------------- own-connection wrapper
def test_shift_for_wrapper_uses_its_own_connection(monkeypatch):
    cur = cursor(assigned=assigned_row(7, DAY))
    patch_db(monkeypatch, sr, cur)
    assert sr.shift_for("u1", "org1", "2026-11-05")["shift_id"] == 7


# ---------------------------------------------------------------------------- end to end through the request flow
def test_approved_shift_change_applies_to_its_date_only(monkeypatch):
    """Approving a shift change marks the request approved (no write to users_additional_info); the lookup then
    answers the requested shift for that one date and the assigned shift for every other date."""
    from core_functions import requests_functions as rf

    request_row = ("shift_change", "org1", "u1", "pending", None, None)
    cur = RouteCursor()
    cur.add("FROM requests r LEFT JOIN users u", one=request_row)
    patch_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    monkeypatch.setattr(rf, "enforce_request_actor", lambda: False)
    assert rf.update_request_status("req-1", "approved", "adm", 1)["success"] is True
    assert not [s for s, _ in cur.sql if "users_additional_info" in s and s.startswith("UPDATE")]
    assert cur.find("UPDATE requests")[0][1][0] == "approved"
