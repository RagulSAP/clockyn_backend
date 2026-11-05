"""Resource scoping (G4-A): ids that name ONE resource (request_id, group_id, incentive_id, leads_id, expense_id,
holiday_id, temp_id, receiver_id, member lists) are checked in the handler against the caller's organisation (chat:
membership). Every test uses a fake cursor: the org / membership predicate must be in the SQL and a foreign id must
answer the existing not-found envelope WITHOUT any write being executed.
Run: python -m pytest tests/test_resource_scoping.py -q
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from core_functions import (chat_functions as cf, daily_hourly_functions as dh, emp_expenses_functions as ef,  # noqa: E402
                            holiday_functions as hol, incentive_functions as inc, leads_functions as lf,
                            requests_functions as rf)


def run(coro):
    return asyncio.run(coro)


class FakeCursor:
    """fetchone() / fetchall() hand out the scripted values in order (None / [] when exhausted); statements are recorded."""

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

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def writes(self):
        return [s for s, _p in self.sql if s.split(" ", 1)[0] in ("INSERT", "UPDATE", "DELETE")]

    def find(self, text):
        return [(s, p) for s, p in self.sql if text in s]


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def use_db(monkeypatch, module, cur):
    conn = FakeConn(cur)
    monkeypatch.setattr(module, "get_db_connection", lambda: conn)
    monkeypatch.setattr(module, "return_connection", lambda c: None)
    return conn


def not_found(exc_info, text=None):
    assert exc_info.value.status_code == 404
    if text:
        assert text in str(exc_info.value.detail)


# ======================================================================================================= requests
def _decide(monkeypatch, actor_rows, env=None):
    monkeypatch.delenv("REQUESTS_ENFORCE_ACTOR", raising=False)
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    cur = FakeCursor(one=[("shift_change", "org-A", "u1", "pending", None, None)] + actor_rows)
    conn = use_db(monkeypatch, rf, cur)
    monkeypatch.setattr(rf, "_get_user_push_token", lambda uid: None)
    return cur, conn


def test_request_of_another_org_is_not_found_even_while_the_actor_flag_is_off(monkeypatch):
    # admin of org-B (no org_info row for org-A) tries to decide a request of org-A; flag unset, REQUIRE_AUTH off
    cur, conn = _decide(monkeypatch, [("adm-b", 1, 1, None), None])
    with pytest.raises(HTTPException) as e:
        rf.update_request_status("r1", "approved", "adm-b", 1)
    not_found(e, "Request not found")
    assert cur.writes() == [] and conn.commits == 0
    assert cur.find("FROM org_info WHERE org_id = %s AND parent_id = %s")[0][1] == ("org-A", "adm-b")   # the org predicate


def test_request_of_another_org_is_not_found_for_a_manager_of_another_org(monkeypatch):
    cur, conn = _decide(monkeypatch, [("mgr-b", 2, 1, "org-B")])
    with pytest.raises(HTTPException) as e:
        rf.update_request_status("r1", "approved", "mgr-b", 2)
    not_found(e)
    assert cur.writes() == []


def test_request_of_the_same_org_is_still_decided(monkeypatch):
    cur, conn = _decide(monkeypatch, [("adm", 1, 1, None), (1,)])
    assert rf.update_request_status("r1", "approved", "adm", 1)["success"] is True
    assert cur.find("UPDATE requests") and conn.commits == 1


# ======================================================================================================= chat
def _user(uid, role, org=None, parent=None):
    return {"user_id": uid, "role_id": role, "org_id": org, "parent_id": parent}


def test_receiver_of_another_org_is_not_found_and_nothing_is_inserted(monkeypatch):
    cur = FakeCursor(many=[[_user("a", 3, parent="org-A"), _user("b", 3, parent="org-B")]])
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.send_chat_message(cf.SendMessageRequest(sender_id="a", receiver_id="b", message="hi")))
    not_found(e, "User not found")
    assert cur.writes() == [] and conn.commits == 0
    assert "user_id = ANY(%s)" in cur.sql[0][0] and cur.sql[0][1] == (["a", "b"],)


def test_unknown_receiver_is_not_found(monkeypatch):
    cur = FakeCursor(many=[[_user("a", 3, parent="org-A")]])
    use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.send_chat_message(cf.SendMessageRequest(sender_id="a", receiver_id="ghost", message="hi")))
    not_found(e)
    assert cur.writes() == []


def test_same_org_rule_for_staff_manager_and_a_multi_org_admin():
    cur = FakeCursor(many=[[_user("s", 3, parent="org-A"), _user("m", 2, parent="org-A")]])
    cf.assert_same_org_users(cur, "s", "m")                                          # staff <-> manager of one org
    # an admin owns org-A (primary) and org-B (org_info); a staff member of org-B may chat with that admin
    cur = FakeCursor(many=[[_user("adm", 1, org="org-A"), _user("s2", 3, parent="org-B")],
                           [{"parent_id": "adm", "org_id": "org-B"}]])
    cf.assert_same_org_users(cur, "adm", "s2")
    assert "FROM org_info WHERE parent_id = ANY(%s)" in cur.sql[1][0]
    cur = FakeCursor(many=[[_user("adm", 1, org="org-A"), _user("s3", 3, parent="org-C")], [{"parent_id": "adm", "org_id": "org-B"}]])
    with pytest.raises(HTTPException) as e:
        cf.assert_same_org_users(cur, "adm", "s3")
    not_found(e)


def test_send_file_route_checks_the_receiver_before_the_upload(monkeypatch):
    import router.chat as rc

    def refuse(sender, receiver):
        raise HTTPException(status_code=404, detail="User not found")

    async def no_upload(*a, **k):
        raise AssertionError("the file must not be read for a foreign receiver")

    async def no_org(request):          # plan gating needs the database; this test is about the receiver check
        return None

    from core_functions import gen_plans_functions
    monkeypatch.setattr(gen_plans_functions, "org_id_from_request", no_org)
    monkeypatch.setattr(rc, "check_chat_peers", refuse)
    monkeypatch.setattr(rc, "read_upload", no_upload)
    app = FastAPI()
    app.include_router(rc.router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/chat/send-file", data={"sender_id": "a", "receiver_id": "b"}, files={"file": ("x.png", b"123", "image/png")})
    assert r.status_code == 404 and r.json() == {"detail": "User not found"}


def test_group_messages_need_membership(monkeypatch):
    cur = FakeCursor(one=[None])
    use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.get_group_messages("g1", "outsider"))
    not_found(e, "Group not found")
    assert len(cur.sql) == 1                                   # no message query ran
    sql, params = cur.sql[0]
    assert 'FROM "group" g' in sql and "gm.member_id = %s" in sql and "gm.status = 1" in sql
    assert params == ("g1", "outsider", "outsider")


def test_group_mark_read_needs_membership(monkeypatch):
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.mark_group_as_read(cf.MarkGroupReadRequest(group_id="g1", user_id="outsider")))
    not_found(e)
    assert cur.writes() == [] and conn.commits == 0


def test_group_members_list_needs_membership_when_the_caller_is_known(monkeypatch):
    cur = FakeCursor(one=[None])
    use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.get_group_members("g1", "outsider"))
    not_found(e)
    assert len(cur.sql) == 1
    # without a caller (internal use, or no token) the list is read as before
    cur = FakeCursor(many=[[{"user_id": "u1"}]])
    use_db(monkeypatch, cf, cur)
    assert run(cf.get_group_members("g1")) == [{"user_id": "u1"}]


def test_add_members_to_an_unknown_group_is_not_found(monkeypatch):
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.add_group_members(cf.AddGroupMembersRequest(group_id="nope", member_ids=["m1"]), "adm"))
    not_found(e, "Group not found")
    assert cur.writes() == [] and conn.commits == 0


def test_add_members_by_somebody_who_is_not_in_the_group_is_not_found(monkeypatch):
    cur = FakeCursor(one=[{"org_id": "org-A", "user_id": "adm"}, None])         # group row, then no participant row
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.add_group_members(cf.AddGroupMembersRequest(group_id="g1", member_ids=["m1"]), "outsider"))
    not_found(e, "Group not found")
    assert cur.writes() == [] and conn.commits == 0
    assert cur.sql[1][1] == ("g1", "outsider", "outsider")


def test_add_members_of_another_org_is_rejected_and_nothing_is_written(monkeypatch):
    cur = FakeCursor(one=[{"org_id": "org-A", "user_id": "adm"}, {"?column?": 1}],
                     many=[[{"user_id": "m1"}]])                                # m2 is not a user of org-A
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.add_group_members(cf.AddGroupMembersRequest(group_id="g1", member_ids=["m1", "m2", "m1"]), "adm"))
    not_found(e, "User not found")
    assert cur.writes() == [] and conn.commits == 0
    sql, params = cur.find("FROM users")[0]
    assert "user_id = ANY(%s)" in sql and "org_id = %s OR parent_id = %s" in sql
    assert params == (["m1", "m2"], "org-A", "org-A", "org-A")                  # de-duplicated, group's org three times


def test_create_group_with_a_member_of_another_org_is_rejected(monkeypatch):
    cur = FakeCursor(many=[[{"user_id": "adm"}, {"user_id": "m1"}]])             # "x" belongs to another org
    conn = use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException) as e:
        run(cf.create_group(cf.CreateGroupRequest(group_name="G", created_by="adm", member_ids=["adm", "m1", "x"], org_id="org-A")))
    not_found(e, "User not found")
    assert cur.writes() == [] and conn.commits == 0


def test_create_group_checks_the_creator_too_and_keeps_the_happy_path(monkeypatch):
    cur = FakeCursor(many=[[{"user_id": "m1"}]])                                 # the creator "adm" is not of org-A
    use_db(monkeypatch, cf, cur)
    with pytest.raises(HTTPException):
        run(cf.create_group(cf.CreateGroupRequest(group_name="G", created_by="adm", member_ids=["m1"], org_id="org-A")))
    cur = FakeCursor(many=[[{"user_id": "adm"}, {"user_id": "m1"}]])
    conn = use_db(monkeypatch, cf, cur)
    out = run(cf.create_group(cf.CreateGroupRequest(group_name="G", created_by="adm", member_ids=["adm", "m1", "m1"], org_id="org-A")))
    assert out["member_count"] == 2 and conn.commits == 1
    assert len(cur.find("INSERT INTO group_members")) == 2                       # the repeated id is inserted once


def test_group_routes_bind_the_user_id_to_the_token():
    import router.chat as rc
    foreign = SimpleNamespace(state=SimpleNamespace(identity=SimpleNamespace(user_id="me")))
    with pytest.raises(HTTPException) as e:
        run(rc.group_messages_route("g1", foreign, user_id="somebody-else"))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        run(rc.group_mark_read_route(rc.MarkGroupReadRequest(group_id="g", user_id="somebody-else"), foreign))
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        run(rc.group_create_route(rc.CreateGroupRequest(group_name="G", created_by="somebody-else", member_ids=["x"], org_id="o"), foreign))
    assert e.value.status_code == 403


def test_add_members_route_passes_the_token_user_as_the_caller(monkeypatch):
    import router.chat as rc
    seen = {}

    async def fake_add(req, caller_id=None):
        seen["caller"] = caller_id
        return {"success": True, "added": 0}

    monkeypatch.setattr(rc, "add_group_members", fake_add)
    me = SimpleNamespace(state=SimpleNamespace(identity=SimpleNamespace(user_id="me")))
    run(rc.group_add_members_route(rc.AddGroupMembersRequest(group_id="g", member_ids=["m"]), me))
    assert seen["caller"] == "me"
    run(rc.group_add_members_route(rc.AddGroupMembersRequest(group_id="g", member_ids=["m"]), SimpleNamespace(state=SimpleNamespace())))
    assert seen["caller"] is None


# ======================================================================================================= expenses
def test_expense_decision_is_scoped_to_the_org_in_the_sql(monkeypatch):
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, ef, cur)
    with pytest.raises(HTTPException) as e:
        run(ef.update_travel_bill_status("exp-1", "approved", "org-B"))
    not_found(e, "Expense not found")
    sql, params = cur.sql[0]
    assert "WHERE expense_id = %s AND org_id = %s" in sql and params[2:] == ("exp-1", "org-B")
    assert conn.commits == 0 and conn.rollbacks == 1


def test_odometer_decision_is_scoped_to_the_org_in_the_sql(monkeypatch):
    cur = FakeCursor(one=[{"rate": 3}, None])
    conn = use_db(monkeypatch, ef, cur)
    with pytest.raises(HTTPException) as e:
        run(ef.update_odometer_status("rd-1", "approved", "org-B"))
    not_found(e, "Reading not found")
    sql, params = cur.find("UPDATE odometer_readings")[0]
    assert "WHERE readings_id = %s AND org_id = %s" in sql and params[-2:] == ("rd-1", "org-B")
    assert conn.commits == 0


def _expense_app(monkeypatch, cur):
    import router.emp_expenses as rx
    from core_functions import db_function
    conn = FakeConn(cur)
    monkeypatch.setattr(db_function, "get_db_connection", lambda: conn)
    monkeypatch.setattr(db_function, "return_connection", lambda c: None)
    saved = []
    monkeypatch.setattr(rx, "save_travel_bill_photo", lambda *a: saved.append(a) or "https://s3/x")
    app = FastAPI()
    app.include_router(rx.router)
    return TestClient(app, raise_server_exceptions=False), saved


def test_travel_bill_photo_upload_needs_the_users_own_expense(monkeypatch):
    cur = FakeCursor(one=[{"parent_id": "org-A"}, None])
    c, saved = _expense_app(monkeypatch, cur)
    r = c.post("/expenses/travel-bill/upload-photo", params={"user_id": "u1", "expense_id": "somebody-elses"},
               files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")})
    assert r.status_code == 404 and r.json() == {"detail": "Expense not found"}
    assert saved == []
    sql, params = cur.sql[1]
    assert "FROM travel_bill_expenses WHERE expense_id = %s AND user_id = %s AND org_id = %s" in sql
    assert params == ("somebody-elses", "u1", "org-A")


# ======================================================================================================= incentives
ORGS_A = frozenset({"org-A"})


def _tpl(org):
    return {"org_id": org}


def test_incentive_update_of_another_org_changes_nothing(monkeypatch):
    for fn, args in ((inc.update_count_incentive, (1000, 10, 5)), (inc.update_turnover_incentive, (1000, 5)),
                     (inc.update_overtime_incentive, (60, 100)), (inc.update_bonus_incentive, (5, "January", 2026))):
        cur = FakeCursor(one=[_tpl("org-B")])
        conn = use_db(monkeypatch, inc, cur)
        assert fn("i1", *args, orgs=ORGS_A) is False                         # the route answers 404 on False
        assert cur.writes() == [] and conn.commits == 0
        assert "FROM incentive_templates WHERE incentive_id = %s" in cur.sql[0][0]


def test_incentive_update_of_the_own_org_still_works(monkeypatch):
    cur = FakeCursor(one=[_tpl("org-A"), None])                              # template, then no duplicate amount
    conn = use_db(monkeypatch, inc, cur)
    assert inc.update_turnover_incentive("i1", 1000, 5, orgs=ORGS_A) is True
    assert cur.find("UPDATE incentive_turnover_details") and conn.commits == 1


def test_incentive_delete_of_another_org_deletes_nothing(monkeypatch):
    cur = FakeCursor(one=[_tpl("org-B")])
    conn = use_db(monkeypatch, inc, cur)
    assert inc.delete_incentive("i1", "COUNT", ORGS_A) is False
    assert cur.writes() == [] and conn.commits == 0
    cur = FakeCursor(one=[None])                                             # unknown template: same answer
    use_db(monkeypatch, inc, cur)
    assert inc.delete_incentive("i1", "COUNT", ORGS_A) is False and cur.writes() == []


def test_incentive_delete_of_the_own_org_deletes(monkeypatch):
    cur = FakeCursor(one=[_tpl("org-A")])
    conn = use_db(monkeypatch, inc, cur)
    inc.delete_incentive("i1", "COUNT", ORGS_A)
    assert len(cur.find("DELETE FROM incentive_templates")) == 1 and conn.commits == 1


def test_incentive_details_of_another_org_are_not_found(monkeypatch):
    cur = FakeCursor(one=[_tpl("org-B")])
    use_db(monkeypatch, inc, cur)
    with pytest.raises(HTTPException) as e:
        inc.get_incentive_details("i1", "COUNT", ORGS_A)
    not_found(e)
    assert len(cur.sql) == 1                                                 # the details were never read
    cur = FakeCursor(one=[_tpl("org-A")], many=[[{"incentive_id": "i1"}]])
    use_db(monkeypatch, inc, cur)
    assert inc.get_incentive_details("i1", "COUNT", ORGS_A) == [{"incentive_id": "i1"}]


def test_incentive_without_a_token_keeps_the_old_behaviour(monkeypatch):
    cur = FakeCursor(many=[[{"incentive_id": "i1"}]])
    use_db(monkeypatch, inc, cur)
    assert inc.get_incentive_details("i1", "COUNT") == [{"incentive_id": "i1"}]
    assert len(cur.sql) == 1                                                 # no template lookup without a scope


def test_assign_incentive_needs_a_template_of_the_org(monkeypatch):
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, inc, cur)
    with pytest.raises(HTTPException) as e:
        inc.assign_incentive_staff("org-A", ["u1"], "foreign-incentive")
    not_found(e, "Incentive not found")
    assert cur.writes() == [] and conn.commits == 0
    assert cur.sql[0][1] == ("foreign-incentive", "org-A") and "org_id = %s" in cur.sql[0][0]


def test_assign_incentive_rejects_staff_outside_the_org(monkeypatch):
    cur = FakeCursor(one=[(1,)], many=[[("u1",)]])                           # u2 is not a user of org-A
    conn = use_db(monkeypatch, inc, cur)
    with pytest.raises(HTTPException) as e:
        inc.assign_incentive_staff("org-A", ["u1", "u2", "u1"], "i1")
    not_found(e, "Staff member not found")
    assert cur.writes() == [] and conn.commits == 0
    sql, params = cur.sql[1]
    assert "user_id = ANY(%s) AND (org_id = %s OR parent_id = %s)" in sql
    assert params == (["u1", "u2"], "org-A", "org-A")


def test_unassign_touches_nothing_when_the_assignment_is_not_in_this_org(monkeypatch):
    cur = FakeCursor(rowcount=0)
    conn = use_db(monkeypatch, inc, cur)
    assert inc.unassign_incentive_staff("org-A", "u1", "foreign-incentive") is False
    assert len(cur.writes()) == 1 and "DELETE FROM assigned_incentives WHERE org_id = %s" in cur.sql[0][0]
    assert conn.commits == 0                                                 # the per-user detail rows stay untouched


# ======================================================================================================= holidays
def test_holiday_delete_and_update_are_scoped_to_the_org(monkeypatch):
    cur = FakeCursor(rowcount=0)
    conn = use_db(monkeypatch, hol, cur)
    assert hol.delete_holiday("org-A", 7)["success"] is False
    assert "WHERE id = %s AND org_id = %s" in cur.sql[0][0] and cur.sql[0][1] == (7, "org-A") and conn.commits == 0
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, hol, cur)
    assert hol.update_holiday("org-A", 7, "New name")["success"] is False
    assert "WHERE id = %s AND org_id = %s" in cur.sql[0][0] and cur.writes() == [] and conn.commits == 0


# ======================================================================================================= wage templates
def _template(org, temp_type="D"):
    return {"temp_id": "t1", "org_id": org, "temp_name": "Day 1", "temp_type": temp_type, "temp_amount": 500}


def test_wage_template_update_and_delete_of_another_org_are_404(monkeypatch):
    for call in (lambda: dh.update_template("t1", 600, "D", orgs=ORGS_A), lambda: dh.delete_template("t1", "D", orgs=ORGS_A),
                 lambda: dh.update_template("t1", 600, "D", org_id="org-A"), lambda: dh.delete_template("t1", "D", org_id="org-A")):
        cur = FakeCursor(many=[[_template("org-B")]])
        conn = use_db(monkeypatch, dh, cur)
        with pytest.raises(HTTPException) as e:
            call()
        not_found(e, "Template not found")
        assert cur.writes() == [] and conn.commits == 0 and conn.rollbacks == 1


def test_wage_template_of_the_own_org_is_still_changed(monkeypatch):
    cur = FakeCursor(many=[[_template("org-A")]])
    conn = use_db(monkeypatch, dh, cur)
    assert dh.update_template("t1", 600, "D", orgs=ORGS_A) is True
    assert cur.find("UPDATE daily_hourly_wages") and conn.commits == 1


def test_wage_assignment_rejects_staff_outside_the_org(monkeypatch):
    cur = FakeCursor(many=[[_template("org-A")], [{"user_id": "u1"}]])
    conn = use_db(monkeypatch, dh, cur)
    with pytest.raises(HTTPException) as e:
        dh.assign_staff_to_wage("org-A", ["u1", "u2"], "t1")
    not_found(e, "Employee not found")
    assert cur.writes() == [] and conn.commits == 0
    assert cur.find("FROM users WHERE parent_id = %s")[0][1] == ("org-A", ["u1", "u2"])


# ======================================================================================================= leads
def test_lead_lookup_without_org_id_uses_the_callers_orgs():
    cur = FakeCursor(one=[{"org_id": "org-A"}])
    assert lf._get_lead(cur, "l1", None, frozenset({"org-A"})) == {"org_id": "org-A"}
    sql, params = cur.sql[0]
    assert "org_id = ANY(%s)" in sql and params == ("l1", ["org-A"])
    cur = FakeCursor(one=[None])
    with pytest.raises(HTTPException) as e:
        lf._get_lead(cur, "l1", None, frozenset({"org-A"}))
    not_found(e, "Lead not found")


def test_lead_lookup_with_a_foreign_org_id_never_reaches_the_database():
    cur = FakeCursor(one=[{"org_id": "org-B"}])
    with pytest.raises(HTTPException) as e:
        lf._get_lead(cur, "l1", "org-B", frozenset({"org-A"}))
    not_found(e)
    assert cur.sql == []
    # no token: scope None -> the lookup stays exactly as before
    cur = FakeCursor(one=[{"org_id": "org-B"}])
    assert lf._get_lead(cur, "l1", None, None) == {"org_id": "org-B"}
    assert cur.sql[0][1] == ("l1",)


@pytest.mark.parametrize("name, call", [
    ("delete", lambda: lf.delete_lead("l1", None, ORGS_A)),
    ("status", lambda: lf.update_lead_status("l1", "new", None, ORGS_A)),
    ("work", lambda: lf.update_work_status("l1", "working", "u1", None, ORGS_A)),
    ("assignment", lambda: lf.update_lead_assignment("l1", "u1", None, None, None, ORGS_A)),
])
def test_lead_mutations_of_another_org_are_404_and_write_nothing(monkeypatch, name, call):
    cur = FakeCursor(one=[None])                                              # the org-narrowed lookup finds nothing
    conn = use_db(monkeypatch, lf, cur)
    with pytest.raises(HTTPException) as e:
        run(call())
    not_found(e, "Lead not found")
    assert cur.writes() == [] and conn.commits == 0
    assert "org_id = ANY(%s)" in cur.sql[0][0]


def test_lead_update_with_a_foreign_org_in_the_body_is_404(monkeypatch):
    cur = FakeCursor()
    use_db(monkeypatch, lf, cur)
    body = lf.LeadCreate(name="n", mobile="9000000001", address="a", category="c", org_id="org-B")
    with pytest.raises(HTTPException) as e:
        run(lf.update_lead("l1", body, ORGS_A))
    not_found(e)
    assert cur.sql == []


def test_carry_forward_action_on_a_foreign_request_is_404(monkeypatch):
    row = {"request_id": "r1", "leads_id": "l1", "user_id": "u1", "org_id": "org-B", "request_date": None, "status": "pending"}
    cur = FakeCursor(one=[row])
    conn = use_db(monkeypatch, lf, cur)
    with pytest.raises(HTTPException) as e:
        run(lf.action_carry_forward("r1", "approved", "adm", ORGS_A))
    not_found(e, "Request not found")
    assert cur.writes() == [] and conn.commits == 0
    # the own org's request is actioned (flag-free path: unassign or revert, then commit)
    cur = FakeCursor(one=[dict(row, org_id="org-A")])
    conn = use_db(monkeypatch, lf, cur)
    assert run(lf.action_carry_forward("r1", "rejected", "adm", ORGS_A))["success"] is True
    assert conn.commits == 1


def test_category_delete_is_scoped_by_the_callers_orgs(monkeypatch):
    cur = FakeCursor(one=[None])
    conn = use_db(monkeypatch, lf, cur)
    with pytest.raises(HTTPException) as e:
        run(lf.delete_category("c1", None, ORGS_A))
    not_found(e, "Category not found")
    assert "org_id = ANY(%s)" in cur.sql[0][0] and cur.writes() == []
    cur = FakeCursor()
    use_db(monkeypatch, lf, cur)
    with pytest.raises(HTTPException):
        run(lf.delete_category("c1", "org-B", ORGS_A))                        # a foreign org named explicitly
    assert cur.sql == []


def test_routes_hand_the_callers_orgs_to_the_functions(monkeypatch):
    import router.leads as rl
    seen = {}

    def stub(name):
        async def f(*a, **k):
            seen[name] = a
            return {"success": True}
        return f

    for name in ("delete_lead", "update_lead_status", "update_lead_assignment", "action_carry_forward", "delete_category"):
        monkeypatch.setattr(rl, name, stub(name))
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.identity = SimpleNamespace(user_id="adm", org_id="org-A")
        request.state.allowed_orgs = frozenset({"org-A", "org-A2"})
        return await call_next(request)

    app.include_router(rl.router)
    c = TestClient(app)
    c.delete("/api/leads/l1")
    assert seen["delete_lead"] == ("l1", None, frozenset({"org-A", "org-A2"}))
    c.put("/api/leads/carry-forward/r1/action", params={"action": "approved", "admin_id": "adm"})
    assert seen["action_carry_forward"] == ("r1", "approved", "adm", frozenset({"org-A", "org-A2"}))
    c.delete("/api/leads/categories/c1")
    assert seen["delete_category"] == ("c1", None, frozenset({"org-A", "org-A2"}))


# ======================================================================================================= guard keys
def test_chat_partner_keys_through_the_real_guard(monkeypatch):
    from auth_fakes import FakeDirectory, KEY, auth, make_app
    from core_functions import auth_dependency as ad

    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({
        "adm": {"role": 1, "org": "org-A"}, "mgr": {"role": 2, "org": "org-A"},
        "stf": {"role": 3, "org": "org-A"}, "stf2": {"role": 3, "org": "org-A"},
        "stf-b": {"role": 3, "org": "org-B"}}, orgs_of_org_ids=("org-A", "org-B")).install()
    try:
        c = TestClient(make_app(), raise_server_exceptions=False)
        staff, manager = auth("stf", "org-A", 3), auth("mgr", "org-A", 2)
        assert c.get("/chat/messages/stf/stf2", headers=staff).status_code == 200       # colleague
        assert c.get("/chat/messages/stf/mgr", headers=staff).status_code == 200        # manager of the same org
        assert c.get("/chat/messages/stf/adm", headers=staff).status_code == 200        # admin of the same org
        assert c.get("/chat/messages/stf/stf-b", headers=staff).status_code == 403      # a user of another org
        assert c.get("/chat/messages/stf/ghost", headers=staff).status_code == 200      # unknown id: the handler's 404
        assert c.get("/chat/messages/mgr/stf-b", headers=manager).status_code == 403
        assert c.get("/chat/messages/stf2/stf", headers=staff).status_code == 403       # not the caller's own id first
    finally:
        ad.reset_state()


def test_policy_peer_and_mark_read_rules():
    import core_functions.auth_policy as P
    staff = P.Identity("stf", "org-A", 3)
    orgs = frozenset({"org-A"})
    ids = [("body", "receiver_id", "x")]
    assert P.ids_to_resolve(staff, ids, orgs, "POST", "/chat/send") == ["x"]                      # staff peers are looked up
    assert P.ownership_violation(staff, ids, orgs, {"x": "org-A"}, "POST", "/chat/send") is None
    assert P.ownership_violation(staff, ids, orgs, {"x": "org-B"}, "POST", "/chat/send") == ("body", "receiver_id")
    assert P.ownership_violation(staff, ids, orgs, {"x": None}, "POST", "/chat/send") is None     # unknown: handler 404
    # an admin owning several orgs: the looked-up owner may be a set
    assert P.ownership_violation(staff, ids, orgs, {"x": frozenset({"org-B", "org-A"})}, "POST", "/chat/send") is None
    # mark-read: the viewer is receiver_id (must be the caller), sender_id is the partner (a colleague is fine)
    mark = [("body", "sender_id", "colleague"), ("body", "receiver_id", "stf")]
    assert P.ownership_violation(staff, mark, orgs, {"colleague": "org-A"}, "PUT", "/chat/mark-read") is None
    assert P.ownership_violation(staff, [("body", "receiver_id", "colleague")], orgs, {}, "PUT", "/chat/mark-read") \
        == ("body", "receiver_id")
    # sender_id stays the caller on every other route
    assert P.ownership_violation(staff, [("body", "sender_id", "colleague")], orgs, {}, "POST", "/chat/send") == ("body", "sender_id")


# ======================================================================================================= QW-13
def _auth_app(monkeypatch, cur):
    import router.auth as ra
    use_db(monkeypatch, ra, cur)
    app = FastAPI()
    app.include_router(ra.router)
    return TestClient(app, raise_server_exceptions=False)


def test_check_mobile_and_email_are_public_and_answer_only_exists(monkeypatch):
    cur = FakeCursor(one=[{"ok": 1}])
    c = _auth_app(monkeypatch, cur)
    r = c.get("/auth/check-mobile/9876543210")                         # no credential of any kind
    assert r.status_code == 200 and r.json() == {"exists": True}
    cur = FakeCursor(one=[None])
    c = _auth_app(monkeypatch, cur)
    r = c.get("/auth/check-email/New@Example.com")
    assert r.status_code == 200 and r.json() == {"exists": False}
    sql, params = cur.sql[0]
    assert "LOWER(email) = LOWER(%s)" in sql and params == ("New@Example.com",)       # case-insensitive match
    assert "SELECT 1" in sql and "role_id" not in sql                                  # any account counts, nothing else is read


def test_check_mobile_and_email_are_in_the_public_policy():
    import core_functions.auth_policy as P
    assert P.classify("GET", "/auth/check-mobile/9876543210") == P.PUBLIC
    assert P.classify("GET", "/auth/check-email/a@b.co") == P.PUBLIC


# ======================================================================================================= QW-4b
def test_staff_detail_and_profile_find_inactive_staff_but_not_deleted(monkeypatch):
    from core_functions import add_staff_function as asf, home_functions as hf
    import ml.s3

    monkeypatch.setattr(ml.s3, "get_employee_profile_url", lambda uid: None)
    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    # asf.get_staff_detail: SQL admits status 0 and 1 (NULL = active), never 2
    row = {k: None for k in ("dob", "gender", "blood_group", "address", "emergency_contact_no", "acc_no", "ifsc_code",
                             "bank_name", "bank_branch", "shift_id", "role_name")}
    row.update({"user_id": "u1", "name": "Asha", "emp_id": "E1", "mobile_number": "9000000003", "email": "a@x.io", "status": 0,
                "is_field_staff": 0, "branch_id": "3", "dept_id": "d-1", "org_id": "org1", "dept_name": "Ops", "branch_name": "HQ"})
    cur = FakeCursor(one=[row])
    use_db(monkeypatch, asf, cur)
    res = run(asf.get_staff_detail("u1"))
    assert res.status == 0                                                 # an inactive member opens (reactivation)
    assert "COALESCE(u.status, 1) IN (0, 1)" in cur.sql[0][0]
    cur = FakeCursor(one=[None])                                           # a deleted member: the filter hides the row
    use_db(monkeypatch, asf, cur)
    with pytest.raises(HTTPException) as e:
        run(asf.get_staff_detail("u1"))
    not_found(e)
    # home: staff-detail card and staff-profile
    for fn in (hf.get_staff_detail, hf.get_staff_profile):
        cur = FakeCursor(one=[None])
        use_db(monkeypatch, hf, cur)
        with pytest.raises(HTTPException) as e:
            run(fn("u1", "org1"))
        not_found(e)
        sql = cur.sql[0][0]
        assert "u.status IN (0, 1)" in sql and "u.status = 1" not in sql and "u.parent_id = %s" in sql


def test_inactive_staff_profile_is_returned(monkeypatch):
    from core_functions import home_functions as hf

    monkeypatch.setattr(hf, "get_employee_profile_url", lambda uid: None)
    row = {k: None for k in ("dob", "gender", "blood_group", "address", "emergency_contact_no", "acc_no", "ifsc_code",
                             "bank_name", "bank_branch", "shift_id", "father_name", "marital_status", "aadhaar_no",
                             "pan_no", "uan_no", "pf_no", "esi_no", "doj")}
    row.update({"user_id": "u1", "name": "Asha", "emp_id": "E1", "mobile_number": "9", "email": "a@x.io", "role_id": 3,
                "branch_id": "3", "dept_id": "d-1", "org_id": "org1", "dept_name": "Ops", "branch_name": "HQ"})
    use_db(monkeypatch, hf, FakeCursor(one=[row]))                         # the (faked) database returns the status-0 row
    assert run(hf.get_staff_profile("u1", "org1")).user_id == "u1"
