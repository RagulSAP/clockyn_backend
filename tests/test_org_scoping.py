"""DB-free tests for org scoping of branch / department statements.
branch_id is a per-organisation counter, so every statement touching branch_info / dept_info
must also match the organisation. The DB layer is a scripted fake cursor.
Run: python -m pytest tests/test_org_scoping.py -q
"""
import asyncio
import re
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class FakeCursor:
    def __init__(self, one=None, many=None, rowcount=1):
        self.sql = []
        self.one = list(one or [])
        self.many = list(many or [])
        self.rowcount = rowcount

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


def run(coro):
    return asyncio.run(coro)


def patch_db(monkeypatch, module, cursor):
    conn = FakeConn(cursor)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


def run_ignoring_errors(coro):
    """Run a function against an empty fake DB; only the recorded SQL matters here."""
    try:
        run(coro)
    except Exception:
        pass


# ------------------------------------------------------------------ static guard over all SQL
_JOIN = re.compile(
    r"JOIN\s+(?:branch_info|dept_info)\s+\w+\s+ON(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\"\"\"|\"\s*,|$)",
    re.S,
)


def test_every_branch_dept_join_matches_org():
    offenders = []
    for folder in ("core_functions", "router"):
        for path in sorted((ROOT / folder).glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for m in _JOIN.finditer(text):
                if "org_id" not in m.group(1):
                    offenders.append(f"{path.name}: {' '.join(m.group(0).split())[:90]}")
    assert not offenders, offenders


# ------------------------------------------------------------------ branch delete / reassign
def test_delete_branch_foreign_id_is_not_found(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[None])
    conn = patch_db(monkeypatch, bf, cur)
    with pytest.raises(HTTPException) as e:
        run(bf.delete_branch("1", "orgB"))
    assert e.value.status_code == 404
    assert not cur.find("DELETE")
    assert conn.commits == 0
    sel = cur.find("FROM branch_info")[0]
    assert "org_id = %s" in sel[0] and sel[1] == ("1", "orgB")


def test_delete_branch_scopes_delete_by_org(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[{"branch_id": 1, "branch_name": "HQ"}, {"staff_count": 0}])
    conn = patch_db(monkeypatch, bf, cur)
    out = run(bf.delete_branch("1", "orgA"))
    assert out["message"] == "Branch deleted successfully" and out["branch_name"] == "HQ"
    sql, params = cur.find("DELETE FROM branch_info")[0]
    assert "org_id = %s" in sql and params == ("1", "orgA")
    assert conn.commits == 1


def test_reassign_foreign_delete_branch_is_not_found(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[{"branch_id": 2}, None])  # target ok, branch to delete foreign
    conn = patch_db(monkeypatch, bf, cur)
    req = bf.ReassignStaffRequest(staff_ids=["u1"], new_branch_id="2", org_id="orgB", delete_branch_id="1")
    with pytest.raises(HTTPException) as e:
        run(bf.reassign_staff(req))
    assert e.value.status_code == 404
    assert not cur.find("UPDATE users") and not cur.find("DELETE")
    assert conn.commits == 0


def test_reassign_scopes_count_and_delete(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[{"branch_id": 2}, {"branch_id": 1}, {"staff_count": 0}])
    patch_db(monkeypatch, bf, cur)
    req = bf.ReassignStaffRequest(staff_ids=["u1"], new_branch_id="2", org_id="orgA", delete_branch_id="1")
    out = run(bf.reassign_staff(req))
    assert out["branch_deleted"] is True
    upd = cur.find("UPDATE users")[0]
    assert "parent_id = %s" in upd[0] and upd[1][-1] == "orgA"
    cnt = cur.find("COUNT(*)")[0]
    assert "parent_id = %s" in cnt[0] and cnt[1] == ("1", "orgA")
    dele = cur.find("DELETE FROM branch_info")[0]
    assert "org_id = %s" in dele[0] and dele[1] == ("1", "orgA")


def test_get_branch_by_id_org_scoped(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[None])
    patch_db(monkeypatch, bf, cur)
    with pytest.raises(HTTPException) as e:
        run(bf.get_branch_by_id("1", "orgB"))
    assert e.value.status_code == 404
    sql, params = cur.find("FROM branch_info")[0]
    assert "AND org_id = %s" in sql and params == ("1", "orgB")


def test_get_branch_by_id_without_org_keeps_old_query(monkeypatch):
    from core_functions import branch_functions as bf
    cur = FakeCursor(one=[None])
    patch_db(monkeypatch, bf, cur)
    with pytest.raises(HTTPException):
        run(bf.get_branch_by_id("1"))
    sql, params = cur.find("FROM branch_info")[0]
    assert "org_id = %s" not in sql and params == ("1",)


# ------------------------------------------------------------------ department delete
def test_delete_department_foreign_id_is_not_found(monkeypatch):
    from core_functions import dept_functions as df
    cur = FakeCursor(one=[None])
    conn = patch_db(monkeypatch, df, cur)
    with pytest.raises(HTTPException) as e:
        run(df.delete_department("d1", "orgB"))
    assert e.value.status_code == 404
    assert not cur.find("UPDATE users") and not cur.find("DELETE")
    assert conn.commits == 0


def test_delete_department_only_touches_own_org(monkeypatch):
    from core_functions import dept_functions as df
    cur = FakeCursor(one=[{"dept_id": "d1", "dept_name": "Ops"}])
    conn = patch_db(monkeypatch, df, cur)
    out = run(df.delete_department("d1", "orgA"))
    assert out["success"] is True and out["dept_name"] == "Ops"
    usql, uparams = cur.find("UPDATE users SET dept_id = NULL")[0]
    assert "parent_id = %s" in usql and "org_id = %s" in usql
    assert uparams == ("d1", "orgA", "orgA")
    dsql, dparams = cur.find("DELETE FROM dept_info")[0]
    assert "org_id = %s" in dsql and dparams == ("d1", "orgA")
    assert conn.commits == 1


def test_get_departments_staff_count_scoped(monkeypatch):
    from core_functions import dept_functions as df
    cur = FakeCursor()
    patch_db(monkeypatch, df, cur)
    run(df.get_departments("orgA"))
    sql, params = cur.sql[0]
    assert "u.parent_id = d.org_id" in sql and params == ("orgA",)


# ------------------------------------------------------------------ the scoped joins
def test_add_staff_manager_lookups_scoped(monkeypatch):
    from core_functions import add_staff_function as af
    cur = FakeCursor()
    patch_db(monkeypatch, af, cur)
    run_ignoring_errors(af.get_branches("m1", 2))
    run_ignoring_errors(af.get_departments("m1", 2))
    run_ignoring_errors(af.get_branch_info("u1", 3))
    run_ignoring_errors(af.get_department_info("u1", 3))
    b1 = cur.find("JOIN branch_info b ON ma.branch_id")[0][0]
    d1 = cur.find("JOIN dept_info d ON ma.dept_id")[0][0]
    b2 = cur.find("JOIN branch_info b ON u.branch_id")[0][0]
    d2 = cur.find("JOIN dept_info d ON u.dept_id")[0][0]
    for sql in (b1, b2):
        assert "b.org_id = COALESCE(" in sql
    for sql in (d1, d2):
        assert "d.org_id = COALESCE(" in sql


def test_daily_attendance_joins_scoped(monkeypatch):
    from core_functions import attendance_functions as at
    from core_functions import manager_functions as mf

    async def scope(org):
        return org, None, None

    monkeypatch.setattr(mf, "get_manager_scope", scope)
    cur = FakeCursor()
    patch_db(monkeypatch, at, cur)
    run_ignoring_errors(at.get_daily_attendance("orgA", "2026-01-05"))
    sql, params = cur.find("LEFT JOIN branch_info")[0]
    assert "b.org_id = u.parent_id" in sql and "d.org_id = u.parent_id" in sql
    assert "orgA" in params


def test_punch_location_branch_join_scoped(monkeypatch):
    from core_functions import face_attendance_functions as fa
    cur = FakeCursor()
    patch_db(monkeypatch, fa, cur)
    run_ignoring_errors(fa.check_punch_location("u1", 1.0, 1.0))
    sql, params = cur.find("LEFT JOIN branch_info")[0]
    assert "b.org_id = COALESCE(u.parent_id, u.org_id)" in sql and params == ("u1",)


def test_holiday_block_lookup_scoped(monkeypatch):
    from core_functions import holiday_functions as hf
    cur = FakeCursor()
    patch_db(monkeypatch, hf, cur)
    run_ignoring_errors(hf.check_holiday_block("orgA", "u1"))
    sql, params = cur.find("LEFT JOIN branch_info")[0]
    assert "b.org_id = COALESCE(u.parent_id, u.org_id)" in sql
    assert "d.org_id = COALESCE(u.parent_id, u.org_id)" in sql


def test_org_employee_and_attendance_listings_pass_org_to_both_joins(monkeypatch):
    from core_functions import gen_dashboard_functions as gd
    cur = FakeCursor()
    patch_db(monkeypatch, gd, cur)
    run_ignoring_errors(gd.get_org_employees("orgA"))
    run_ignoring_errors(gd.get_org_attendance_by_date("orgA", "2026-01-05"))
    # the platform-admin dashboard now passes the IST date as a parameter instead of CURRENT_DATE (WP4b)
    emp_sql, emp_params = cur.find("LEFT JOIN attendance a ON")[0]
    assert "d.org_id = %s" in emp_sql and "b.org_id = %s" in emp_sql
    assert emp_params == ("orgA", "orgA", gd.ist_today(), "orgA")
    att_sql, att_params = cur.find("a.punchinface")[0]
    assert "d.org_id = %s" in att_sql and "b.org_id = %s" in att_sql
    assert att_params == ("orgA", "orgA", "2026-01-05", "orgA")


def test_payslip_preview_staff_lookup_scoped(monkeypatch):
    from core_functions import salary_structure as ss
    cur = FakeCursor(one=[{"org_name": "X"}])
    patch_db(monkeypatch, ss, cur)
    run_ignoring_errors(ss.get_payslip_preview("orgA", "u1", "p1"))
    sql, params = cur.find("LEFT JOIN branch_info")[0]
    assert "b.org_id = %s" in sql and "d.org_id = %s" in sql
    # the staff lookup is also limited to the organisation's own staff (u.parent_id = %s, WP4b)
    assert params == ("orgA", "orgA", "u1", "orgA")


def test_manager_staff_and_report_dept_joins_scoped(monkeypatch):
    from core_functions import manager_functions as mf
    from core_functions import report_functions as rf
    cur = FakeCursor()
    patch_db(monkeypatch, mf, cur)
    run_ignoring_errors(mf.get_staff_for_manager("orgA", 1, "Ops"))
    sql, params = cur.find("LEFT JOIN dept_info")[0]
    assert "d.org_id = u.parent_id" in sql and params[0] == "orgA"
    cur2 = FakeCursor()
    patch_db(monkeypatch, rf, cur2)
    run_ignoring_errors(rf.generate_monthly_attendance_pdf("u1", "2026-01"))
    sql2, _ = cur2.find("LEFT JOIN dept_info")[0]
    assert "d.org_id = COALESCE(u.parent_id, u.org_id)" in sql2
