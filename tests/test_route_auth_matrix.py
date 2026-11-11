"""Route-table test for authentication (WP12): every mounted route resolves to exactly one class
(public / operator / authenticated), the PUBLIC and OPERATOR lists are explicit and never stale, and every WebSocket
handler checks its handshake.

Static: reuses the `ast` route parser of tests/test_route_table.py (nothing is imported, no heavy library needed).
When a new route appears it is `authenticated` by default (any signed-in user of the same organisation); to make it
public or operator-only add it to PUBLIC_RULES / OPERATOR_RULES in core_functions/auth_policy.py, to narrow it by role
add a RoleRule - this test then keeps the lists honest.
Run: python -m pytest tests/test_route_auth_matrix.py -q
"""
import ast
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_functions import auth_policy as P  # noqa: E402
from test_route_table import mounted_routes  # noqa: E402

ROUTES = mounted_routes()          # [(file, METHOD, normalised path with {} for parameters)]

# Paths that are public on purpose but are not in the router table: FastAPI's own docs routes, the root route that
# app.py defines directly, and static-file prefixes the app does not mount today (kept in the list for when it does).
UNMOUNTED_PUBLIC = {"/", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
                    "/static/*", "/files/*", "/uploads/*"}


def kind_of(method, path):
    return P.classify("GET" if method == "WEBSOCKET" else method, path)


def test_the_route_table_is_read():
    assert len(ROUTES) > 250
    assert ("auth.py", "POST", "/auth/validate-otp") in ROUTES


def test_every_mounted_route_resolves_to_exactly_one_known_class():
    seen = {}
    for file, method, path in ROUTES:
        kind = kind_of(method, path)
        assert kind in (P.PUBLIC, P.OPERATOR, P.AUTHENTICATED), (method, path, kind)
        pub = P._first(P.PUBLIC_RULES, "GET" if method == "WEBSOCKET" else method, path) is not None
        op = P._first(P.OPERATOR_RULES, "GET" if method == "WEBSOCKET" else method, path) is not None
        assert not (pub and op), f"{method} {path} is both public and operator"
        seen[(method, path)] = kind
    counts = Counter(seen.values())
    assert counts[P.AUTHENTICATED] > counts[P.PUBLIC] > 10 and counts[P.OPERATOR] >= 20


def test_every_public_rule_matches_a_mounted_route_or_is_a_documented_extra():
    stale = []
    for methods, pattern in P.PUBLIC_RULES:
        hit = any(P._method_ok(methods, "GET" if m == "WEBSOCKET" else m) and P.pattern_matches(pattern, p)
                  for _f, m, p in ROUTES)
        if not hit and pattern not in UNMOUNTED_PUBLIC:
            stale.append((methods, pattern))
    assert not stale, f"PUBLIC_RULES entries that match no mounted route (remove them or list them as extras): {stale}"


def test_every_operator_rule_matches_a_mounted_route():
    stale = [(m, p) for m, p in P.OPERATOR_RULES
             if not any(P._method_ok(m, mm) and P.pattern_matches(p, path) for _f, mm, path in ROUTES)]
    assert not stale, f"OPERATOR_RULES entries that match no mounted route: {stale}"


def test_every_role_rule_matches_a_mounted_route():
    stale = [(r.methods, r.pattern) for r in P.ROLE_RULES
             if not any(P._method_ok(r.methods, m) and P.pattern_matches(r.pattern, p) for _f, m, p in ROUTES)]
    assert not stale, f"ROLE_RULES entries that match no mounted route: {stale}"


def test_the_public_list_is_exactly_the_documented_one():
    public = sorted({(m, p) for _f, m, p in ROUTES if kind_of(m, p) == P.PUBLIC})
    paths = {p for _m, p in public}
    assert paths == {
        "/health", "/ready", "/version/check",
        "/auth/config", "/auth/generate-otp", "/auth/validate-otp", "/auth/verify-device-token", "/auth/login",
        "/auth/employee-login", "/auth/register", "/auth/register-company", "/auth/logout",
        "/auth/admin-login", "/auth/admin-login/verify-otp",
        "/auth/check-mobile/{}", "/auth/check-email/{}", "/auth/org-logo/{}", "/auth/session-events/{}",
        "/profile-photo/{}", "/requests/device-change/submit",
        "/referral/validate",           # Refer & Earn: the signup screen checks a typed code (rate limited per IP)
        "/webhook/razorpay", "/webhook/msg91/whatsapp",
        "/invoice/download",            # WP12 K2: signed link, otherwise the handler demands the admin's token itself
    }, "a route became public (or stopped being public): update the contract with the app first"
    # profile photo: only the READ is public, the upload needs an identity (WP12 K2)
    assert {(m, p) for m, p in public if p == "/profile-photo/{}"} == {("GET", "/profile-photo/{}")}
    assert {(m, p) for m, p in public if p == "/invoice/download"} == {("GET", "/invoice/download")}
    assert {(m, p) for m, p in public if p == "/referral/validate"} == {("GET", "/referral/validate")}


def test_the_operator_list_covers_the_platform_routes():
    operator = {(m, p) for _f, m, p in ROUTES if kind_of(m, p) == P.OPERATOR}
    gen_files = {(f, m, p) for f, m, p in ROUTES if f == "gen_dashboard.py"}
    assert gen_files and all(kind_of(m, p) == P.OPERATOR for _f, m, p in gen_files)
    for needle in (("GET", "/auth/gen-admin-users"), ("POST", "/auth/gen-admin-users"),
                   ("PUT", "/auth/gen-admin-users/{}"), ("DELETE", "/auth/gen-admin-users/{}"),
                   ("GET", "/plans/coupons"), ("POST", "/plans/coupons"), ("PUT", "/plans/coupons/{}"),
                   ("DELETE", "/plans/coupons/{}"), ("GET", "/plans/all"), ("POST", "/plans/"), ("PUT", "/plans/{}"),
                   ("DELETE", "/plans/{}"), ("GET", "/support/requests/all"), ("PUT", "/support/update/{}"),
                   ("GET", "/invoice/bulk-download"), ("GET", "/company/deletion-requests"),
                   ("GET", "/attribution/reports/meta-attribution"),
                   ("GET", "/gen-dashboard/referrals"), ("POST", "/gen-dashboard/referrals/rewards/{}/void")):
        assert kind_of(*needle) == P.OPERATOR, needle
    assert len(operator) >= 20


@pytest.mark.parametrize("method, path", [
    # what the Clockyn app reads from /plans, /support, /company must stay available to a signed-in user
    ("GET", "/plans/get_plans"), ("GET", "/plans/subscription/{}"), ("GET", "/plans/plan-status/{}"),
    ("GET", "/plans/coupon/first"), ("GET", "/plans/coupon/validate"),
    ("GET", "/support/subjects"), ("POST", "/support/submit"), ("GET", "/support/requests/{}"), ("POST", "/support/{}/reply"),
    ("POST", "/company/suggest-feature"), ("GET", "/company/suggest-feature/mine"), ("POST", "/company/request-deletion/{}"),
    ("GET", "/invoice/history"), ("POST", "/attribution/meta"),
    ("GET", "/referral/{}"),     # the organisation's own Refer & Earn page: an app route (admin only), never operator
])
def test_app_facing_routes_are_not_operator_routes(method, path):
    assert P.classify(method, path) == P.AUTHENTICATED


def test_the_webhook_routes_are_public_because_they_verify_a_signature():
    hooks = [(m, p) for f, m, p in ROUTES if f == "gen_webhook.py"]
    assert hooks and all(kind_of(m, p) == P.PUBLIC for m, p in hooks)


def test_websocket_routes_are_authenticated_and_their_handlers_check_the_handshake():
    sockets = [(f, p) for f, m, p in ROUTES if m == "WEBSOCKET"]
    assert {p for _f, p in sockets} >= {"/ws/chat/{}", "/ws/badge/{}", "/ws/group-chat/{}/{}"}
    for _f, p in sockets:
        assert P.classify("GET", p) == P.AUTHENTICATED
    for file in ("chat.py", "ws_badge.py"):
        tree = ast.parse((ROOT / "router" / file).read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and any(
                    isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "websocket" for d in node.decorator_list):
                calls = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(node) if isinstance(c, ast.Call)}
                assert "authenticate_websocket" in calls, f"{file}:{node.name} accepts a socket without the handshake check"
                first_accept = min((c.lineno for c in ast.walk(node) if isinstance(c, ast.Call)
                                    and getattr(c.func, "attr", "") in ("accept", "connect")), default=10 ** 9)
                check = min(c.lineno for c in ast.walk(node) if isinstance(c, ast.Call)
                            and getattr(c.func, "id", "") == "authenticate_websocket")
                assert check < first_accept, f"{file}:{node.name} accepts before checking the token"


def test_no_router_mounts_a_route_with_its_own_global_bypass():
    """The guard is a dependency of the app router: a router created with `dependencies=[]` would not remove it, but a
    mount on a sub-application (app.mount) would bypass it, so none may exist."""
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8-sig"))
    mounts = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "mount"]
    assert not mounts, "app.mount() sub-applications are not covered by the authentication guard"


def test_print_the_classification_for_review(capsys):
    """Not an assertion: `pytest -s tests/test_route_auth_matrix.py` lists the class of every route."""
    lines = sorted(f"{kind_of(m, p):13s} {m:9s} {p}" for _f, m, p in ROUTES)
    print("\n".join(lines))
    assert lines
