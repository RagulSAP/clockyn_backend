"""DB-free tests for the manager scope helper (WP6, planner D-10): core_functions/scope.py and where it is applied.
Pure logic is tested for every caller role x target combination; the DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_scope.py -q
"""
import asyncio
import itertools
import sys
from datetime import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import scope as S  # noqa: E402


class FakeCursor:
    def __init__(self, one=None, many=None):
        self.sql = []
        self.one = list(one or [])
        self.many = list(many or [])

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.one.pop(0) if self.one else None

    def fetchall(self):
        return self.many.pop(0) if self.many else []

    def close(self):
        pass

    def find(self, text):
        return [(s, p) for s, p in self.sql if text in s]


class ExplodingCursor:
    def execute(self, *a, **k):
        raise AssertionError("the database must not be touched while ENFORCE_MANAGER_SCOPE is off")


class FakeConn:
    def __init__(self, cursor):
        self.cur = cursor

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch):
    monkeypatch.delenv("ENFORCE_MANAGER_SCOPE", raising=False)


# ------------------------------------------------------------------------------------------------ the flag
@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("off", False), ("no", False),
    ("1", True), ("true", True), ("YES", True), ("On", True), (" 1 ", True),
])
def test_flag_default_off_and_parsing(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", value)
    assert S.enforced() is expected


def test_flag_off_touches_no_database_and_adds_nothing():
    assert S.current(ExplodingCursor(), "mgr-1") is None
    assert S.clause(None, "u") == ("", [])


def test_flag_on_without_caller_id_adds_nothing(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    assert S.current(ExplodingCursor(), "") is None
    assert S.current(ExplodingCursor(), None) is None


# ------------------------------------------------------------------------------------------------ building scopes
def test_make_scope_normalises_values():
    sc = S.make_scope("m1", 2, "org1", ["3", 1, None, "x", 3], ["d2", "d1", None, "d1"])
    assert sc.kind == S.KIND_MANAGER and sc.branch_ids == (1, 3) and sc.dept_ids == ("d1", "d2")
    assert S.make_scope("org1", None).kind == S.KIND_ORG and S.make_scope("org1", None).org_id == "org1"
    assert S.make_scope("a1", 1).kind == S.KIND_ORG and S.make_scope("a1", 1).org_id == "a1"
    assert S.make_scope("s1", 3, "org1").kind == S.KIND_STAFF
    assert S.make_scope("x", 9, "org1").kind == S.KIND_UNKNOWN


def test_load_scope_manager_reads_assignments():
    cur = FakeCursor(one=[{"role_id": 2, "parent_id": "org1"}], many=[[{"branch_id": 4, "dept_id": "d1"}, {"branch_id": 5, "dept_id": "d1"}]])
    sc = S.load_scope(cur, "m1")
    assert (sc.kind, sc.org_id, sc.branch_ids, sc.dept_ids) == (S.KIND_MANAGER, "org1", (4, 5), ("d1",))
    assert cur.sql[0] == ("SELECT role_id, parent_id FROM users WHERE user_id = %s", ("m1",))
    assert cur.sql[1] == ("SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s", ("m1",))


def test_load_scope_other_roles_and_unknown_id():
    assert S.load_scope(FakeCursor(one=[{"role_id": 1, "parent_id": None}]), "a1").kind == S.KIND_ORG
    cur = FakeCursor(one=[{"role_id": 3, "parent_id": "org1"}])
    assert S.load_scope(cur, "s1").kind == S.KIND_STAFF and len(cur.sql) == 1     # no assignment lookup for staff
    org_style = S.load_scope(FakeCursor(one=[None]), "org1")                        # not a users row: an organisation id
    assert org_style.kind == S.KIND_ORG and org_style.org_id == "org1"


def test_current_loads_the_scope_when_the_flag_is_on(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"role_id": 2, "parent_id": "org1"}], many=[[]])
    assert S.current(cur, "m1").kind == S.KIND_MANAGER


# ------------------------------------------------------------------------------------------------ predicate / clause
def test_predicate_admin_and_org_style():
    sc = S.make_scope("a1", 1)
    assert S.scope_predicate(sc, "u") == ("u.parent_id = %s", ["a1"])
    assert S.scope_predicate(sc, "u", include_org=False) == ("TRUE", [])
    assert S.clause(sc, "u") == ("", [])


def test_predicate_staff_is_self_only():
    sc = S.make_scope("s1", 3, "org1")
    assert S.scope_predicate(sc, "u") == ("u.user_id = %s", ["s1"])
    assert S.clause(sc, "u") == (" AND (u.user_id = %s)", ["s1"])


def test_predicate_manager_without_assignments_is_self_and_assigned_staff_only():
    sc = S.make_scope("m1", 2, "org1")
    sql, params = S.scope_predicate(sc, "u", include_org=False)
    assert sql == "(u.user_id = %s OR u.manager_id = %s)" and params == ["m1", "m1"]
    assert "branch_id" not in sql and "dept_id" not in sql
    assert sql.count("%s") == len(params)


def test_predicate_manager_with_assignments_unites_owned_and_area():
    sc = S.make_scope("m1", 2, "org1", [4, 5], ["d1"])
    sql, params = S.scope_predicate(sc, "u", include_org=False)
    assert sql == "(u.user_id = %s OR u.manager_id = %s OR (u.branch_id = ANY(%s) AND u.dept_id = ANY(%s)))"
    assert params == ["m1", "m1", [4, 5], ["d1"]]
    full_sql, full_params = S.scope_predicate(sc, "u")
    assert full_sql.startswith("u.parent_id = %s AND (") and full_params == ["org1"] + params
    assert full_sql.count("%s") == len(full_params)


def test_predicate_manager_branch_only_and_dept_only():
    sql, params = S.scope_predicate(S.make_scope("m1", 2, "org1", [4], []), "x", include_org=False)
    assert sql == "(x.user_id = %s OR x.manager_id = %s OR (x.branch_id = ANY(%s)))" and params == ["m1", "m1", [4]]
    sql, params = S.scope_predicate(S.make_scope("m1", 2, "org1", [], ["d9"]), "x", include_org=False)
    assert sql == "(x.user_id = %s OR x.manager_id = %s OR (x.dept_id = ANY(%s)))" and params == ["m1", "m1", ["d9"]]


def test_predicate_without_alias_uses_bare_columns():
    sql, params = S.scope_predicate(S.make_scope("m1", 2, "org1", [4], ["d1"]), None, include_org=False)
    assert "u." not in sql and "branch_id = ANY(%s)" in sql and sql.startswith("(user_id = %s")
    assert S.clause(S.make_scope("m1", 2, "org1"), None)[0].startswith(" AND (user_id = %s OR manager_id = %s")


def test_unknown_role_sees_nobody():
    sc = S.make_scope("x", 9, "org1")
    assert S.scope_predicate(sc, "u") == ("FALSE", [])
    assert S.clause(sc, "u") == (" AND (FALSE)", [])


# ------------------------------------------------------------------------------------------------ can_manage matrix
ORG, OTHER_ORG = "org1", "org2"
ADMIN = S.make_scope("admin1", 1)                                       # org id convention: org_id == own id... see below
ADMIN = S.Scope("admin1", S.KIND_ORG, ORG)
MGR_AREA = S.make_scope("m1", 2, ORG, [4], ["d1"])                      # assigned branch 4 + dept d1
MGR_BARE = S.make_scope("m2", 2, ORG)                                   # no assignments
STAFF = S.make_scope("s1", 3, ORG)
UNKNOWN = S.make_scope("x1", 9, ORG)


def row(user_id, role=3, parent=ORG, branch=4, dept="d1", manager=None):
    return {"user_id": user_id, "role_id": role, "parent_id": parent, "branch_id": branch, "dept_id": dept, "manager_id": manager}


TARGETS = {
    "self_staff": row("s1"),
    "colleague_in_area": row("s2"),
    "colleague_other_branch": row("s3", branch=9),
    "colleague_other_dept": row("s4", dept="d2"),
    "colleague_no_branch": row("s5", branch=None),
    "assigned_to_m1_outside_area": row("s6", branch=9, dept="d2", manager="m1"),
    "assigned_to_m2": row("s7", branch=9, dept="d2", manager="m2"),
    "assigned_to_other_manager": row("s8", branch=9, dept="d2", manager="mX"),
    "other_org_staff": row("s9", parent=OTHER_ORG),
    "other_org_assigned_to_m1": row("s10", parent=OTHER_ORG, manager="m1"),
    "a_manager": row("m3", role=2),
    "manager_m1_itself": row("m1", role=2, branch=None, dept=None),
    "the_admin": row("admin1", role=1, parent=None, branch=None, dept=None),
    "another_admin": row("admin2", role=1, parent=None, branch=None, dept=None),
    "legacy_manager_owned": row("s11", parent="m1", branch=9, dept="d2", manager="m1"),
}

EXPECTED = {
    # caller:           {target: allowed}
    "admin": {"self_staff", "colleague_in_area", "colleague_other_branch", "colleague_other_dept", "colleague_no_branch",
              "assigned_to_m1_outside_area", "assigned_to_m2", "assigned_to_other_manager", "a_manager",
              "manager_m1_itself", "the_admin"},
    "mgr_area": {"self_staff", "colleague_in_area", "assigned_to_m1_outside_area", "manager_m1_itself", "legacy_manager_owned"},
    "mgr_bare": {"assigned_to_m2"},
    "staff": {"self_staff"},
    "unknown": set(),
}
CALLERS = {"admin": ADMIN, "mgr_area": MGR_AREA, "mgr_bare": MGR_BARE, "staff": STAFF, "unknown": UNKNOWN}


@pytest.mark.parametrize("caller, target", list(itertools.product(CALLERS, TARGETS)))
def test_can_manage_matrix(caller, target):
    assert S.can_manage(CALLERS[caller], TARGETS[target]) is (target in EXPECTED[caller]), (caller, target)


def test_can_manage_manager_itself_is_always_allowed_and_target_org_override():
    assert S.can_manage(MGR_BARE, row("m2", role=2, branch=None, dept=None)) is True
    legacy = row("s11", parent="m1", branch=9, dept="d2", manager="m1")
    assert S.can_manage(ADMIN, legacy) is False                          # org of a legacy row is unknown to the helper
    assert S.can_manage(ADMIN, legacy, target_org_id=ORG) is True        # ... unless the caller resolved it
    assert S.can_manage(ADMIN, legacy, target_org_id=OTHER_ORG) is False


def test_can_manage_rejects_empty_or_unknown_rows():
    for caller in CALLERS.values():
        assert S.can_manage(caller, None) is False
        assert S.can_manage(caller, {}) is False
    assert S.can_manage(ADMIN, {"user_id": "s1", "role_id": 3}) is False  # no organisation on the row: deny


def test_can_manage_manager_with_only_one_assignment_list():
    branch_only = S.make_scope("m4", 2, ORG, [4], [])
    assert S.can_manage(branch_only, row("a", branch=4, dept="anything")) is True
    assert S.can_manage(branch_only, row("a", branch=5)) is False
    dept_only = S.make_scope("m5", 2, ORG, [], ["d1"])
    assert S.can_manage(dept_only, row("a", branch=99, dept="d1")) is True
    assert S.can_manage(dept_only, row("a", dept="d2")) is False
    assert S.can_manage(dept_only, row("a", dept=None)) is False


def test_visible_user_ids_filters_a_list():
    rows = [TARGETS["colleague_in_area"], TARGETS["colleague_other_branch"], TARGETS["a_manager"]]
    assert S.visible_user_ids(MGR_AREA, rows) == ["s2"]
    assert S.visible_user_ids(ADMIN, rows) == ["s2", "s3", "m3"]


# ------------------------------------------------------------------------------------------------ applied: home lists
def _patch_home(monkeypatch, cursor, branch_ids=None, dept_ids=None):
    from core_functions import home_functions as hf
    import core_functions.manager_functions as mf

    async def scope(uid):
        return "org1", branch_ids, dept_ids

    monkeypatch.setattr(mf, "get_manager_scope", scope)
    monkeypatch.setattr(hf, "get_db_connection", lambda: FakeConn(cursor))
    monkeypatch.setattr(hf, "return_connection", lambda c: None)
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    return hf


STAFF_LISTS = ["get_staff_list", "get_all_staff", "get_absent_staff", "get_punched_in_staff", "get_punched_out_staff"]


@pytest.mark.parametrize("fn", STAFF_LISTS)
def test_flag_off_the_staff_lists_run_the_old_statement(monkeypatch, fn):
    cur = FakeCursor(many=[[]])
    hf = _patch_home(monkeypatch, cur, [4], ["d1"])
    run(getattr(hf, fn)("m1"))
    users_sql = [(s, p) for s, p in cur.sql if "FROM users u" in s]
    assert len(users_sql) == 1 and len(cur.sql) == 1                       # no role / assignment lookup
    sql, params = users_sql[0]
    assert "manager_id" not in sql
    assert "u.branch_id = ANY(%s)" in sql and "u.dept_id = ANY(%s)" in sql  # the old manager filters, unchanged
    assert params[-2:] == ([4], ["d1"])


@pytest.mark.parametrize("fn", STAFF_LISTS)
def test_flag_on_manager_scope_replaces_the_old_lists_and_sits_before_order_by(monkeypatch, fn):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"role_id": 2, "parent_id": "org1"}], many=[[{"branch_id": 4, "dept_id": "d1"}], []])
    hf = _patch_home(monkeypatch, cur, [4], ["d1"])
    run(getattr(hf, fn)("m1"))
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "AND (u.user_id = %s OR u.manager_id = %s OR (u.branch_id = ANY(%s) AND u.dept_id = ANY(%s)))" in sql
    assert sql.count("branch_id = ANY(%s)") == 1                           # the old list filter is not applied twice
    assert sql.index("manager_id") < sql.rindex("ORDER BY")        # the final ORDER BY (the lateral sub-select has its own)
    assert params[-4:] == ("m1", "m1", [4], ["d1"]) or list(params[-4:]) == ["m1", "m1", [4], ["d1"]]
    assert sql.count("%s") == len(params)


def test_flag_on_manager_without_assignments_sees_self_and_assigned_staff_only(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"role_id": 2, "parent_id": "org1"}], many=[[], []])
    hf = _patch_home(monkeypatch, cur, None, None)
    run(hf.get_all_staff("m2"))
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "AND (u.user_id = %s OR u.manager_id = %s)" in sql and list(params[-2:]) == ["m2", "m2"]


def test_flag_on_admin_gets_the_unchanged_statement(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"role_id": 1, "parent_id": None}], many=[[]])
    hf = _patch_home(monkeypatch, cur, None, None)
    run(hf.get_all_staff("org1"))
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "manager_id" not in sql and "FALSE" not in sql


def test_flag_on_staff_caller_sees_only_themselves(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"role_id": 3, "parent_id": "org1"}], many=[[]])
    hf = _patch_home(monkeypatch, cur, None, None)
    run(hf.get_all_staff("s1"))
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "AND (u.user_id = %s)" in sql and list(params)[-1] == "s1"


def test_dashboard_counts_use_the_same_scope_in_all_three_queries(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")

    class Cur(FakeCursor):
        def fetchone(self):
            last = self.sql[-1][0]
            if "FROM users WHERE user_id" in last:
                return {"role_id": 2, "parent_id": "org1"}
            if "COUNT(*) as count" in last:
                return {"count": 3}
            if "punched_in" in last:
                return {"punched_in": 1, "punched_out": 1, "total_attendance": 2}
            return None

        def fetchall(self):
            return []

    cur = Cur()
    hf = _patch_home(monkeypatch, cur, None, None)
    run(hf.get_dashboard_status("m2"))
    scoped = [(s, p) for s, p in cur.sql if "manager_id" in s]
    assert len(scoped) == 3
    assert "AND (user_id = %s OR manager_id = %s)" in scoped[0][0]          # unaliased staff count
    assert all("AND (u.user_id = %s OR u.manager_id = %s)" in s for s, _p in scoped[1:])
    assert len([1 for s, _p in cur.sql if "FROM users WHERE user_id" in s]) == 1   # the scope is loaded once
    for s, p in scoped:
        assert s.count("%s") == len(p)


def test_flag_off_dashboard_has_no_scope_lookup(monkeypatch):
    class Cur(FakeCursor):
        def fetchone(self):
            last = self.sql[-1][0]
            if "COUNT(*) as count" in last:
                return {"count": 3}
            return {"punched_in": 0, "punched_out": 0, "total_attendance": 0, "grace_minutes": 0}

        def fetchall(self):
            return []

    cur = Cur()
    hf = _patch_home(monkeypatch, cur, None, None)
    run(hf.get_dashboard_status("org1"))
    assert not [1 for s, _p in cur.sql if "manager_id" in s or "FROM users WHERE user_id" in s]


def test_late_list_reads_its_rows_before_the_grace_lookup(monkeypatch):
    class Cur(FakeCursor):
        def __init__(self):
            super().__init__()
            self.pending = None

        def execute(self, sql, params=None):
            super().execute(sql, params)
            s = " ".join(sql.split())
            self.pending = ("rows" if "FROM users u" in s else "other")

        def fetchall(self):
            return [{"user_id": "s1", "name": "A", "emp_id": "1", "mobile_number": "9", "email": "e", "role_id": 3,
                     "user_status": 1, "dept_name": "D", "branch_name": "B", "punch_in": None,
                     "first_in": time(10, 0), "start_time": time(9, 0), "end_time": time(18, 0)}] if self.pending == "rows" else []

        def fetchone(self):
            return None

    cur = Cur()
    hf = _patch_home(monkeypatch, cur, None, None)
    # the grace lookup runs its own statement on the same cursor (as the real one does with SAVEPOINT + SELECT)
    monkeypatch.setattr(hf, "org_grace_minutes", lambda c, o: (c.execute("SELECT 1"), 0)[1])
    out = run(hf.get_late_punched_in_staff("org1"))
    assert [s.user_id for s in out] == ["s1"]      # 10:00 against a 09:00 shift: the row fetched BEFORE the lookup is kept


# ------------------------------------------------------------------------------------------------ applied: managers
def test_get_staff_query_scope_flag_off_is_get_manager_scope(monkeypatch):
    import core_functions.manager_functions as mf

    async def scope(uid):
        return "org1", [4], ["d1"]

    monkeypatch.setattr(mf, "get_manager_scope", scope)
    assert run(mf.get_staff_query_scope("m1")) == ("org1", [4], ["d1"])
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    assert run(mf.get_staff_query_scope("m1")) == ("org1", None, None)


def _patch_mf(monkeypatch, cursor):
    import core_functions.manager_functions as mf
    monkeypatch.setattr(mf, "get_db_connection", lambda: FakeConn(cursor))
    monkeypatch.setattr(mf, "return_connection", lambda c: None)
    return mf


def test_staff_under_manager_flag_off_keeps_the_old_statement(monkeypatch):
    cur = FakeCursor(one=[{"parent_id": "org1"}], many=[[{"branch_id": 4, "dept_id": "d1"}], []])
    mf = _patch_mf(monkeypatch, cur)
    run(mf.get_staff_under_manager("m1"))
    users_sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "manager_id" not in users_sql and "u.branch_id = ANY(%s)" in users_sql and "u.dept_id = ANY(%s)" in users_sql


def test_staff_under_manager_flag_off_without_assignments_is_empty_as_before(monkeypatch):
    cur = FakeCursor(one=[{"parent_id": "org1"}], many=[[]])
    mf = _patch_mf(monkeypatch, cur)
    assert run(mf.get_staff_under_manager("m2")) == []


def test_staff_under_manager_flag_on_returns_assigned_staff_even_without_assignments(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"parent_id": "org1"}, {"role_id": 2, "parent_id": "org1"}],
                     many=[[], [{"user_id": "s7", "name": "A", "emp_id": "7", "branch_name": "", "dept_name": ""}]])
    mf = _patch_mf(monkeypatch, cur)
    out = run(mf.get_staff_under_manager("m2"))
    assert [r["user_id"] for r in out] == ["s7"]
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "u.parent_id IN (%s, %s)" in sql and "u.role_id = 3" in sql and "(u.user_id = %s OR u.manager_id = %s)" in sql
    assert list(params) == ["org1", "org1", "org1", "m2", "m2", "m2"]
    assert sql.count("%s") == len(params)


def test_staff_under_manager_flag_on_with_assignments_unites_area_and_assigned(monkeypatch):
    monkeypatch.setenv("ENFORCE_MANAGER_SCOPE", "1")
    cur = FakeCursor(one=[{"parent_id": "org1"}, {"role_id": 2, "parent_id": "org1"}],
                     many=[[{"branch_id": 4, "dept_id": "d1"}], []])
    mf = _patch_mf(monkeypatch, cur)
    run(mf.get_staff_under_manager("m1"))
    sql, params = [(s, p) for s, p in cur.sql if "FROM users u" in s][0]
    assert "(u.user_id = %s OR u.manager_id = %s OR (u.branch_id = ANY(%s) AND u.dept_id = ANY(%s)))" in sql
    assert list(params)[-2:] == [[4], ["d1"]] and sql.count("%s") == len(params)
