"""Tests of the authentication policy data (core_functions/auth_policy.py): path matching, public / operator / role rules,
manager permissions, the pure ownership function, and - the safety net against locking real users out - the list of
endpoints the Clockyn app calls from its STAFF screens, which must be allowed for a staff member (role 3).
Run: python -m pytest tests/test_auth_policy.py -q
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_policy as P  # noqa: E402
from test_route_table import mounted_routes  # noqa: E402

ADMIN = P.Identity("adm", "org-A", 1)
MANAGER = P.Identity("mgr", "org-A", 2)
STAFF = P.Identity("stf", "org-A", 3)

# ============================================================================================================== staff calls
# Endpoints called from the app's staff (role 3) screens and the staff-side parts used by managers (role 2) as employees.
# HOW IT WAS DERIVED (the test must not need the frontend repo, so the result is copied here):
#   1. `src/config/routes.ts`: /staff/* = role 2 + 3 areas; `src/features/{employee,requests/staff,expenses/staff,chat,crm,
#      announcements,settings,auth}` are the staff-side features (admin-only features: billing, org-structure, payroll,
#      reports, team-rules, staff-directory, dashboard, location, company edit).
#   2. `grep -rhoE "\b[a-zA-Z]+Api\.[a-zA-Z]+" src/features/<those dirs>` lists the API functions the staff screens call
#      (faceApi, employeeApi, attendanceApi, requestsApi, expensesApi, chatApi, groupChatApi, leadsApi, shiftsApi,
#      companyApi.deleteAccount, payrollApi.getPayslipPreview/getAssignedPayslipId, salaryReportsApi.listUserAdvances,
#      homeApi.getMonthlyAttendance/getOrgAdmin/getAllStaff, staffApi.getStaff, systemApi, supportApi, plansApi ...).
#   3. each function's path was read from `src/api/<module>.ts` (`api.get/post/put/delete/upload('<path>')`).
# Re-derive when the app gains a staff screen. A path written here that no mounted route serves fails the test below.
STAFF_CALLS = [
    ("POST", "/auth/update-push-token"), ("GET", "/auth/user-mobile/{}"), ("GET", "/auth/employee-profile/{}"),
    ("POST", "/auth/upload-employee-profile/{}"), ("POST", "/auth/logout"), ("GET", "/version/check"),
    ("GET", "/face/check/{}"), ("GET", "/face/warmup/{}"), ("POST", "/face/check-location"), ("POST", "/face/register"),
    ("POST", "/face/punch"), ("POST", "/profile-photo/{}"), ("GET", "/profile-photo/{}"),
    ("GET", "/attendance/today/{}"), ("GET", "/attendance/sessions/{}"),
    ("GET", "/employee/personal-details/{}"), ("PUT", "/employee/personal-details/{}"), ("GET", "/employee/today-birthdays/{}"),
    ("GET", "/addstaff/{}"),
    ("GET", "/home/monthly-attendance"), ("GET", "/home/org-admin/{}"), ("GET", "/home/all-staff"), ("GET", "/home/current-date"),
    ("POST", "/requests/leave/submit"), ("POST", "/requests/permission/submit"), ("POST", "/requests/shift-change/submit"),
    ("POST", "/requests/device-change/submit"), ("GET", "/requests/employee/{}/{}"), ("GET", "/requests/calendar/{}/{}"),
    ("GET", "/requests/check-session/{}"),
    ("GET", "/shift/get_shifts/{}"), ("GET", "/shift/get_shift/{}"),
    ("GET", "/api/salary/employee/{}/{}"), ("GET", "/api/salary/preview/{}/{}/{}"), ("GET", "/api/advance/user/{}/{}"),
    ("GET", "/api/holidays/{}"), ("GET", "/api/holidays/working-days/{}"), ("GET", "/api/holidays/off-days/{}"),
    ("GET", "/api/attendance-rule/{}"),
    ("POST", "/expenses/odometer/start"), ("POST", "/expenses/odometer/stop"), ("GET", "/expenses/odometer/readings"),
    ("POST", "/expenses/odometer/upload-photo"), ("POST", "/expenses/travel-bill/submit"),
    ("GET", "/expenses/travel-bill/expenses"), ("POST", "/expenses/travel-bill/upload-photo"),
    ("GET", "/chat/list/{}"), ("GET", "/chat/search/{}"), ("GET", "/chat/messages/{}/{}"), ("POST", "/chat/send"),
    ("PUT", "/chat/mark-read"), ("DELETE", "/chat/delete/{}/{}"), ("POST", "/chat/send-file"),
    ("GET", "/group-chat/list/{}"), ("POST", "/group-chat/create"), ("GET", "/group-chat/messages/{}"),
    ("POST", "/group-chat/send"), ("GET", "/group-chat/members/{}"), ("PUT", "/group-chat/mark-read"),
    ("POST", "/group-chat/add-members"), ("POST", "/group-chat/send-file"),
    ("GET", "/api/announcements/employee/{}"), ("GET", "/announcement/employee/{}"),
    ("GET", "/api/leads/employee/{}/{}"), ("PUT", "/api/leads/{}/work-status"), ("PUT", "/api/leads/{}/status"),
    ("POST", "/api/leads/create"), ("PUT", "/api/leads/{}"), ("DELETE", "/api/leads/{}"),
    ("GET", "/api/leads/categories/{}"), ("POST", "/api/leads/categories"), ("POST", "/api/leads/carry-forward/request"),
    ("GET", "/api/leads/carry-forward/my/{}/{}"),
    ("POST", "/staff-location/log"),
    ("DELETE", "/company/delete-account/{}"), ("POST", "/company/request-deletion/{}"), ("GET", "/company/details/{}"),
    ("POST", "/company/suggest-feature"), ("GET", "/company/suggest-feature/mine"),
    ("GET", "/plans/plan-status/{}"), ("GET", "/plans/subscription/{}"), ("GET", "/plans/get_plans"),
    ("GET", "/support/subjects"), ("POST", "/support/submit"), ("GET", "/support/requests/{}"), ("POST", "/support/{}/reply"),
]

ROUTES = {(m, p) for _f, m, p in mounted_routes()}


def _route_exists(method, path):
    return any(m == method and P.pattern_matches(path, p) and P.pattern_matches(p, path) for m, p in ROUTES) or \
        (method, P.normalise_path(path)) in {(m, P.normalise_path(p)) for m, p in ROUTES}


@pytest.mark.parametrize("method, path", STAFF_CALLS)
def test_staff_screen_calls_exist_and_are_allowed_for_a_staff_member(method, path):
    assert _route_exists(method, path), f"{method} {path} is not served by any mounted route: fix the list"
    decision = P.evaluate(method, path, STAFF)
    assert decision.allowed, f"{method} {path} would lock staff out: {decision.reason}"


@pytest.mark.parametrize("method, path", STAFF_CALLS)
def test_staff_screen_calls_are_allowed_for_a_manager_without_any_permission(method, path):
    # a manager uses the staff screens as an employee: no permission may be needed for them
    assert P.evaluate(method, path, MANAGER, has_permission=lambda name: False).allowed


def test_the_staff_list_covers_the_important_screens():
    assert len(STAFF_CALLS) >= 80
    assert ("POST", "/face/punch") in STAFF_CALLS and ("GET", "/chat/list/{}") in STAFF_CALLS


# ============================================================================================================== matching
@pytest.mark.parametrize("pattern, path, expected", [
    ("/a/b", "/a/b", True), ("/a/b", "/a/b/", True), ("/a/b", "/a//b", True), ("/a/b", "/a/b/c", False), ("/a/b", "/a", False),
    ("/a/{}", "/a/x", True), ("/a/{}", "/a", False), ("/a/{}", "/a/x/y", False), ("/a/{}/c", "/a/x/c", True),
    ("/a/*", "/a", True), ("/a/*", "/a/x", True), ("/a/*", "/a/x/y/z", True), ("/a/*", "/ab", False),
    ("/", "/", True), ("/", "/x", False), ("/a/b", "/a/b?x=1", True),
])
def test_pattern_matching(pattern, path, expected):
    assert P.pattern_matches(pattern, path) is expected


def test_normalise_path():
    assert P.normalise_path("") == "/" and P.normalise_path("/a//b/") == "/a/b" and P.normalise_path("/a?b=1") == "/a"


# ============================================================================================================== public/operator
@pytest.mark.parametrize("method, path", [
    ("GET", "/"), ("GET", "/health"), ("GET", "/ready"), ("GET", "/openapi.json"), ("GET", "/docs"), ("GET", "/version/check"),
    ("GET", "/auth/config"), ("POST", "/auth/generate-otp"), ("POST", "/auth/validate-otp"),
    ("POST", "/auth/verify-device-token"), ("POST", "/auth/login"), ("POST", "/auth/employee-login"),
    ("POST", "/auth/register"), ("POST", "/auth/logout"), ("GET", "/auth/check-mobile/9876"), ("GET", "/auth/check-email/a@b.c"),
    ("GET", "/auth/org-logo/o1"), ("GET", "/profile-photo/u1"), ("GET", "/auth/session-events/u1"),
    ("POST", "/webhook/razorpay"), ("POST", "/webhook/msg91/whatsapp"), ("GET", "/static/a.png"),
    ("POST", "/requests/device-change/submit"), ("POST", "/auth/admin-login"),
])
def test_public_paths(method, path):
    assert P.classify(method, path) == P.PUBLIC
    assert P.evaluate(method, path, None).allowed


def test_device_change_submit_is_public_for_post_only():
    assert P.classify("GET", "/requests/device-change/submit") == P.AUTHENTICATED
    assert P.classify("POST", "/requests/shift-change/submit") == P.AUTHENTICATED     # only the pre-login endpoint is public


@pytest.mark.parametrize("method, path", [
    ("GET", "/gen-dashboard/stats"), ("DELETE", "/gen-dashboard/organizations/x"), ("GET", "/auth/gen-admin-users"),
    ("PUT", "/auth/gen-admin-users/9"), ("POST", "/plans/coupons"), ("DELETE", "/plans/coupons/3"), ("POST", "/plans"),
    ("POST", "/plans/"), ("PUT", "/plans/7"), ("DELETE", "/plans/7"), ("GET", "/plans/all"),
    ("GET", "/support/requests/all"), ("PUT", "/support/update/4"), ("GET", "/invoice/bulk-download"),
])
def test_operator_paths_are_never_satisfied_by_an_app_token(method, path):
    assert P.classify(method, path) == P.OPERATOR
    for identity in (ADMIN, MANAGER, STAFF):
        d = P.evaluate(method, path, identity, has_permission=lambda n: True)
        assert d.allowed is False and d.status == 403
    assert P.evaluate(method, path, None).allowed is False


def test_the_app_reads_of_plans_are_not_operator():
    for path in ("/plans/get_plans", "/plans/subscription/o", "/plans/plan-status/o", "/plans/coupon/first", "/plans/coupon/validate"):
        assert P.classify("GET", path) == P.AUTHENTICATED


# ============================================================================================================== role rules
def allowed(method, path, identity, perms=()):
    return P.evaluate(method, path, identity, has_permission=lambda name: name in perms).allowed


@pytest.mark.parametrize("method, path", [
    ("POST", "/orders/create"), ("POST", "/orders/quote"), ("GET", "/orders/history/o"), ("POST", "/payments/verify"),
    # GET /invoice/download is public in the policy since WP12 K2 (signed link); the handler demands the admin's token
    # itself: tests/test_signed_urls.py
    ("GET", "/invoice/history"), ("POST", "/managers/assign"), ("DELETE", "/managers/u1"),
    ("POST", "/managers/u1/permissions"), ("POST", "/api/attendance-rule/o"), ("DELETE", "/company/delete/o/u"),
    ("DELETE", "/addstaff/u1/permanent"), ("DELETE", "/staff-location/cleanup"), ("POST", "/auth/upload-org-logo/o"),
    ("POST", "/department/add"), ("PUT", "/department/update/d"), ("POST", "/department/delete/d"),
])
def test_admin_only_routes(method, path):
    assert allowed(method, path, ADMIN)
    assert not allowed(method, path, MANAGER, perms=("Staff Management", "Company Settings", "Salary Management",
                                                     "Branch Management", "Requests Management"))
    assert not allowed(method, path, STAFF)


@pytest.mark.parametrize("method, path, permission", [
    ("POST", "/addstaff/", "Staff Management"), ("PUT", "/addstaff/u1", "Staff Management"),
    ("DELETE", "/addstaff/u1", "Staff Management"), ("PUT", "/addstaff/u1/status", "Staff Management"),
    ("POST", "/branch/add", "Branch Management"), ("PUT", "/branch/update/1", "Branch Management"),
    ("POST", "/branch/delete/1", "Branch Management"), ("POST", "/branch/reassign-staff", "Branch Management"),
    ("POST", "/shift/create", "Shift Management"), ("DELETE", "/shift/delete/s", "Shift Management"),
    ("POST", "/shift/assign_staff", "Shift Management"),
    ("POST", "/api/holidays/o", "Holiday Management"), ("DELETE", "/api/holidays/o/h", "Holiday Management"),
    ("POST", "/api/salary/template", "Salary Management"), ("GET", "/api/salary/templates/o", "Salary Management"),
    ("POST", "/api/salary/assign", "Salary Management"), ("GET", "/api/salary/staff-breakdown", "Salary Management"),
    ("POST", "/api/wages/assign", "Salary Management"), ("POST", "/api/daily-wages/template", "Salary Management"),
    ("POST", "/api/hourly-charges/template", "Salary Management"), ("POST", "/api/advance", "Salary Management"),
    ("GET", "/api/advance/org/o", "Salary Management"), ("DELETE", "/api/advance/o/a", "Salary Management"),
    ("GET", "/salary/monthly-list", "Salary Management"), ("POST", "/salary/mark-paid", "Salary Management"),
    ("GET", "/api/incentive/count/o", "Incentive Management"), ("POST", "/api/incentive/bonus", "Incentive Management"),
    ("GET", "/api/reports/monthly-attendance-all/o/2025-01", "Attendance Reports"),
    ("GET", "/api/reports/manager/m/monthly-attendance-all/2025-01", "Attendance Reports"),
    ("POST", "/api/leads/assign", "Leads Management"),
    ("DELETE", "/api/leads/categories/c", "Leads Management"), ("PUT", "/api/leads/carry-forward/r/action", "Leads Management"),
    ("PUT", "/api/leads/assignment/l", "Leads Management"),
    ("POST", "/company/add", "Company Settings"), ("PUT", "/company/update/o", "Company Settings"),
    ("POST", "/company/update-logo/o", "Company Settings"),
    ("POST", "/requests/update-status", "Requests Management"),
])
def test_manager_needs_the_permission_staff_never_passes_admin_always_does(method, path, permission):
    assert allowed(method, path, ADMIN)
    assert allowed(method, path, MANAGER, perms=(permission,))
    assert not allowed(method, path, MANAGER, perms=())
    assert not allowed(method, path, MANAGER, perms=("Some Other Permission",))
    assert not allowed(method, path, STAFF, perms=(permission,))
    d = P.evaluate(method, path, MANAGER, has_permission=lambda n: False)
    assert d.status == 403 and permission in d.needs


def test_overtime_report_accepts_either_reporting_or_incentive_permission():
    path = "/api/incentive/overtime-report/o"
    assert allowed("GET", path, MANAGER, perms=("Attendance Reports",))
    assert allowed("GET", path, MANAGER, perms=("Incentive Management",))
    assert not allowed("GET", path, MANAGER, perms=("Staff Management",)) and not allowed("GET", path, STAFF)


@pytest.mark.parametrize("method, path", [
    ("GET", "/requests/admin/o"), ("GET", "/expenses/admin/odometer-readings/o"), ("PUT", "/expenses/admin/travel-bill/1/status"),
    ("POST", "/api/announcements"), ("DELETE", "/api/announcements/1/o"), ("GET", "/api/announcements/o"),
    ("POST", "/announcement/send-all"), ("POST", "/announcement/send-branch-dept"), ("GET", "/announcement/all/o"),
    ("GET", "/staff-location/locations"), ("GET", "/staff-location/history/u"), ("GET", "/staff-location/stats"),
    ("GET", "/home/staff-locations"), ("GET", "/home/staff-punch-locations"), ("PUT", "/home/update-attendance-punch"),
])
def test_admin_or_manager_routes_exclude_staff_but_need_no_permission(method, path):
    assert allowed(method, path, ADMIN) and allowed(method, path, MANAGER) and not allowed(method, path, STAFF)


def test_staff_exceptions_under_restricted_prefixes():
    for method, path in (("GET", "/api/salary/employee/o/u"), ("GET", "/api/salary/preview/o/u/p"),
                         ("GET", "/api/advance/user/o/u"), ("GET", "/api/announcements/employee/u")):
        assert allowed(method, path, STAFF)
    for method, path in (("GET", "/api/salary/templates/o"), ("GET", "/api/advance/org/o"), ("GET", "/api/announcements/o")):
        assert not allowed(method, path, STAFF)


def test_unlisted_paths_default_to_any_authenticated_user():
    for path in ("/home/dashboard-status", "/branch/o", "/anything/new"):
        for identity in (ADMIN, MANAGER, STAFF):
            assert allowed("GET", path, identity)
    assert P.evaluate("GET", "/anything/new", None).status == 401
    assert not P.evaluate("GET", "/home/dashboard-status", P.Identity("x", "o", 9)).allowed      # unknown role


def test_a_manager_is_denied_when_no_permission_callable_is_supplied():
    assert P.evaluate("POST", "/branch/add", MANAGER).allowed is False
    assert P.needs_manager_permission("POST", "/branch/add") == ("Branch Management",)
    assert P.needs_manager_permission("GET", "/home/dashboard-status") == ()


def test_permission_names_are_the_ten_exact_setting_names_of_the_ddl():
    ddl = (ROOT / "punchapp_ddl_latest.sql").read_text(encoding="utf-8")
    used = {n for r in P.ROLE_RULES for n in r.perms}
    assert used and all(f"'{name}'" in ddl for name in used), used


def test_every_manager_permission_of_the_app_is_used_by_some_rule():
    # the ten names the admin can grant (managerShortcuts.ts): none may be silently unmapped
    used = {n for r in P.ROLE_RULES for n in r.perms}
    assert used == {"Staff Management", "Branch Management", "Attendance Reports", "Salary Management", "Holiday Management",
                    "Shift Management", "Leads Management", "Incentive Management", "Company Settings", "Requests Management"}


# ============================================================================================================== ownership (pure)
ORGS = frozenset({"org-A"})


def ids(*triples):
    return [("path", k, v) for k, v in triples]


@pytest.mark.parametrize("identity, given, entity, expected", [
    (ADMIN, [("org_id", "org-A")], {}, None),
    (ADMIN, [("org_id", "org-B")], {}, ("path", "org_id")),
    (STAFF, [("user_id", "stf")], {}, None),
    (STAFF, [("user_id", "org-A")], {}, None),                                  # the app sends the org id as user_id
    (STAFF, [("user_id", "org-B")], {}, ("path", "user_id")),
    (STAFF, [("user_id", "stf2")], {}, ("path", "user_id")),
    (STAFF, [("staff_id", "stf2")], {"stf2": "org-A"}, ("path", "staff_id")),
    (ADMIN, [("employee_id", "u9")], {"u9": "org-A"}, None),
    (ADMIN, [("employee_id", "u9")], {"u9": "org-B"}, ("path", "employee_id")),
    (ADMIN, [("employee_id", "u9")], {"u9": None}, None),                       # unknown id: left to the handler (404)
    (ADMIN, [("user_id", "org-A")], {}, None),                                  # the org id carried as user_id
    (MANAGER, [("org_id", "mgr")], {}, None),                                   # manager convention
    (ADMIN, [("org_id", "adm")], {}, ("path", "org_id")),
    (ADMIN, [("sender_id", "adm")], {}, None),
    (ADMIN, [("sender_id", "u9")], {}, ("path", "sender_id")),
    (ADMIN, [("action_by", "u9")], {}, ("path", "action_by")),
    (ADMIN, [("org_id", "org-A"), ("user_id", "u9")], {"u9": "org-B"}, ("path", "user_id")),
])
def test_ownership_violation(identity, given, entity, expected):
    assert P.ownership_violation(identity, ids(*given), ORGS, entity) == expected


def test_ids_to_resolve_skips_staff_own_ids_and_org_ids():
    given = ids(("user_id", "adm"), ("user_id", "u9"), ("org_id", "org-A"), ("staff_id", "org-A"), ("user_id", "u9"))
    assert P.ids_to_resolve(ADMIN, given, ORGS) == ["u9"]
    assert P.ids_to_resolve(STAFF, given, ORGS) == []                           # staff never need a lookup


def test_collect_ids_reads_every_source_and_only_the_checked_keys():
    from starlette.datastructures import FormData, QueryParams
    found = P.collect_ids({"org_id": "o", "other": "x"}, QueryParams("user_id=a&user_id=b&q=1"),
                          {"staff_id": "s", "nested": {"org_id": "z"}, "org_id": None, "user_id": True},
                          FormData([("employee_id", "e"), ("note", "n")]))
    assert sorted(found) == [("body", "staff_id", "s"), ("form", "employee_id", "e"), ("path", "org_id", "o"),
                             ("query", "user_id", "a"), ("query", "user_id", "b")]
    assert P.collect_ids(None, None, ["x"], None) == []


def test_key_lists_are_data():
    assert P.ORG_KEYS == ("org_id",)
    # receiver_id / other_user_id (the chat partner) are user keys with the peer rule (staff may name a colleague)
    assert set(P.USER_KEYS) == {"user_id", "staff_id", "employee_id", "manager_id", "receiver_id", "other_user_id"}
    assert set(P.PEER_KEYS) == {"receiver_id", "other_user_id"}


# ============================================================================================================== staff id usage
# What the staff screens put into the checked keys (own id, or the org id the app also sends under `user_id`):
# every combination must pass the ownership check for a staff member, so REQUIRE_AUTH=1 cannot break these screens.
@pytest.mark.parametrize("given", [
    [("query", "user_id", "org-A")],                                           # GET /home/all-staff?user_id=<org>
    [("path", "user_id", "org-A")],                                            # GET /home/org-admin/{org}
    [("path", "user_id", "stf"), ("query", "org_id", "org-A")],                # GET /chat/list/{me}?org_id=
    [("path", "org_id", "org-A"), ("path", "user_id", "stf")],                 # GET /api/leads/employee/{org}/{me}
    [("path", "staff_id", "stf"), ("path", "org_id", "org-A")],                # GET /api/salary/employee/{org}/{me}
    [("form", "user_id", "stf"), ("form", "org_id", "org-A")],                 # POST /face/punch, /support/submit
    [("query", "user_id", "stf"), ("query", "org_id", "org-A")],               # /expenses/*
    [("body", "user_id", "stf"), ("body", "org_id", "org-A")],                 # POST /requests/leave/submit
    [("body", "sender_id", "stf")],                                            # POST /chat/send
    [("body", "action_by", "stf")],
])
def test_staff_screens_pass_the_ownership_check(given):
    assert P.ownership_violation(STAFF, given, frozenset({"org-A"}), {}) is None
    assert P.ids_to_resolve(STAFF, given, frozenset({"org-A"})) == []
