"""Auth policy vs the app (REQUIRE_AUTH=1 must not lock a real screen out).

Data: `tests/data/frontend_role_calls.json`, generated from the frontend repo by
`python scripts/derive_frontend_role_calls.py` (static import-graph analysis of src/app/staff, src/app/admin and the manager
Settings shortcuts). The analysis over-approximates (file-level: a hook file that holds an admin-only mutation is "used" by
a staff screen that imports only a sibling hook), so every denied call is either fixed or listed below with the reason.
Run: python -m pytest tests/test_frontend_role_access.py -q
"""
import json
import re
from pathlib import Path

from core_functions import auth_policy as P

DATA = json.loads((Path(__file__).resolve().parent / "data" / "frontend_role_calls.json").read_text(encoding="utf-8"))
ROLE_ADMIN, ROLE_MANAGER, ROLE_STAFF = 1, 2, 3


def concrete(path: str) -> str:
    return re.sub(r"\{param\}", "x", path)


def decide(role: int, call: dict, permission=None):
    ident = P.Identity("u1", "org1", role)
    return P.evaluate(call["method"], concrete(call["path"]), ident, (lambda n: n == permission) if permission else (lambda n: False))


# (method, path) the static analysis attributes to a screen although no screen of that role can trigger it.
STAFF_AREA_FALSE_POSITIVES = {
    ("POST", "/api/leads/assign"): "AssignLeadsScreen mutation lives in the same hook file (useLeadWrites) as the staff add-lead hook",
    ("PUT", "/api/leads/assignment/{param}"): "same hook file; admin screen only",
    ("DELETE", "/api/leads/categories/{param}"): "CategoryPicker shows delete only when allowDelete (admin lead form)",
}
MANAGER_FALSE_POSITIVES = {
    ("DELETE", "/addstaff/{param}/permanent"): "StaffDetailScreen shows the delete button only for role 1 (canPermanentlyDelete)",
    ("DELETE", "/company/delete/{param}/{param}"): "needs more than one company; a manager belongs to exactly one",
    ("POST", "/auth/upload-org-logo/{param}"): "add-company flow (admin multi-company); manager edits use /company/update-logo",
    ("GET", "/api/incentive/overtime-report/{param}"): "the salary screens share a loader file with the overtime report screen",
}
# Real but deliberate: a manager with only 'Staff Management' sees the Advance tab of the staff detail, and adding an advance
# needs 'Salary Management' (money). Owner decision; documented here so a change is noticed.
MANAGER_KNOWN_GAPS = {("POST", "/api/advance"): "Staff Management manager without Salary Management gets 403 on 'add advance'"}


def key(call):
    return call["method"], call["path"]


def test_every_path_the_app_sends_without_a_token_is_public_on_the_server():
    exact, prefixes = DATA["public_exact"], DATA["public_prefixes"]
    assert "/auth/validate-otp" in exact and "/version/check" in exact
    for path in exact:
        for method in ("GET", "POST"):
            assert P.classify(method, path) == P.PUBLIC, "app sends %s %s without a token but the server wants one" % (method, path)
    for prefix in prefixes:
        assert P.classify("GET", prefix + "x") == P.PUBLIC, "app sends %s* without a token but the server wants one" % prefix


def test_no_app_call_is_an_operator_only_route():
    for area in ("staff_area", "admin_area"):
        for call in DATA[area]:
            assert P.classify(call["method"], concrete(call["path"])) != P.OPERATOR, call


def test_staff_screens_are_allowed_for_staff_and_manager():
    denied = []
    for role in (ROLE_STAFF, ROLE_MANAGER):
        for call in DATA["staff_area"]:
            if not decide(role, call).allowed and key(call) not in STAFF_AREA_FALSE_POSITIVES:
                denied.append((role, key(call)))
    assert not denied, "staff-area calls the policy denies: %s" % denied


def test_admin_screens_are_allowed_for_admin():
    denied = [key(c) for c in DATA["admin_area"] if not decide(ROLE_ADMIN, c).allowed]
    assert not denied, "admin-area calls the policy denies the administrator: %s" % denied


def test_manager_shortcut_screens_are_allowed_with_the_shortcut_permission():
    denied = []
    for permission, calls in DATA["manager_screens"].items():
        for call in calls:
            if decide(ROLE_MANAGER, call, permission).allowed:
                continue
            if key(call) in MANAGER_FALSE_POSITIVES or key(call) in MANAGER_KNOWN_GAPS:
                continue
            denied.append((permission, key(call)))
    assert not denied, "manager shortcut screens call routes the policy denies: %s" % denied


def test_exception_lists_are_not_stale():
    staff_denied = {key(c) for c in DATA["staff_area"] if not decide(ROLE_STAFF, c).allowed}
    assert staff_denied <= set(STAFF_AREA_FALSE_POSITIVES), staff_denied - set(STAFF_AREA_FALSE_POSITIVES)
    manager_denied = {key(c) for perm, cs in DATA["manager_screens"].items() for c in cs if not decide(ROLE_MANAGER, c, perm).allowed}
    listed = set(MANAGER_FALSE_POSITIVES) | set(MANAGER_KNOWN_GAPS)
    assert listed <= manager_denied | {key(c) for c in DATA["staff_area"]}, "stale entries: %s" % (listed - manager_denied)


def test_staff_can_create_a_lead_category_but_not_delete_one():
    """Regression: the staff add-lead sheet creates categories (LeadForm -> CategoryPicker)."""
    staff = P.Identity("u1", "o1", ROLE_STAFF)
    assert P.evaluate("POST", "/api/leads/categories", staff).allowed
    assert P.evaluate("GET", "/api/leads/categories/o1", staff).allowed
    assert not P.evaluate("DELETE", "/api/leads/categories/c1", staff).allowed
    assert not P.evaluate("POST", "/api/leads/assign", staff).allowed
