"""Operator guard (WP12 K2): core_functions/operator_auth.py and its use on every operator route.
DB-free: the `gen_admin_login` lookup is replaced through `operator_auth.set_lookup`.
Run: python -m pytest tests/test_operator_auth.py -q
"""
import ast
import sys
from pathlib import Path

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from core_functions import auth_policy as P  # noqa: E402
from core_functions import operator_auth as oa  # noqa: E402
from test_route_table import _decorator_route, _is_api_router, _norm, _prefix_of, mounted_routes  # noqa: E402
from utils import rate_limit  # noqa: E402

OPERATOR_KEY = "k" * 32
GUARD_NAMES = {"require_operator", "require_operator_login", "require_operator_super", "authorize", "operator_auth",
               "_require_gen_admin", "_require_role1"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("OPERATOR_API_KEY", "OPERATOR_ENFORCE", "OPERATOR_LEGACY_ADMIN_ID", "REQUIRE_AUTH", "AUTH_TOKEN_SECRET",
                 "RATE_LIMIT_ENABLED", "RATE_LIMIT_OPERATOR_FAIL", "TRUST_PROXY"):
        monkeypatch.delenv(name, raising=False)
    rate_limit.set_clock(None)
    oa.set_lookup({"111": 1, "222": 2}.get)
    yield
    oa.set_lookup(None)
    rate_limit.set_clock(None)
    ad.reset_state()


def operator_app():
    app = FastAPI()
    r = APIRouter()

    @r.get("/open", dependencies=[Depends(oa.require_operator)])
    def open_route():
        return {"ok": True}

    @r.get("/login", dependencies=[Depends(oa.require_operator_login)])
    def login_route():
        return {"ok": True}

    @r.get("/super", dependencies=[Depends(oa.require_operator_super)])
    def super_route():
        return {"ok": True}
    app.include_router(r)
    return TestClient(app)


# ------------------------------------------------------------------------------------------------------ the guard itself
def test_default_mode_keeps_previously_open_routes_open_and_previously_guarded_routes_guarded():
    c = operator_app()
    assert c.get("/open").status_code == 200                                   # as before: nobody was asked
    assert c.get("/login").status_code == 403                                  # as before: x-admin-id was demanded
    assert c.get("/login", headers={"x-admin-id": "222"}).status_code == 200
    assert c.get("/login", headers={"x-admin-id": "999"}).status_code == 403
    assert c.get("/super", headers={"x-admin-id": "222"}).json() == {"detail": "Only role_id=1 admins can perform this action"}
    assert c.get("/super", headers={"x-admin-id": "111"}).status_code == 200


def test_enforced_mode_demands_a_credential_everywhere(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    c = operator_app()
    assert c.get("/open").status_code == 403
    assert c.get("/open", headers={"x-admin-id": "222"}).status_code == 200   # legacy operator login still works
    assert c.get("/open", headers={"x-admin-id": "999"}).status_code == 403


def test_require_auth_turns_enforcement_on_and_operator_enforce_zero_turns_it_off(monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    assert oa.enforced() is True
    monkeypatch.setenv("OPERATOR_ENFORCE", "0")
    assert oa.enforced() is False
    assert operator_app().get("/open").status_code == 200


def test_key_header_with_the_env_unset_fails_closed_with_503():
    c = operator_app()
    r = c.get("/open", headers={"X-Operator-Key": OPERATOR_KEY})
    assert r.status_code == 503 and r.json() == {"detail": "Operator access is not configured"}


def test_a_short_key_counts_as_not_configured(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", "short")
    assert operator_app().get("/open", headers={"X-Operator-Key": "short"}).status_code == 503


def test_correct_key_passes_wrong_key_is_403_even_with_a_valid_legacy_login(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    c = operator_app()
    for path in ("/open", "/login", "/super"):
        assert c.get(path, headers={"X-Operator-Key": OPERATOR_KEY}).status_code == 200      # the key is the full operator
        assert c.get(path, headers={"X-Operator-Key": OPERATOR_KEY + "x"}).status_code == 403
        assert c.get(path, headers={"X-Operator-Key": "", "x-admin-id": "111"}).status_code == 403   # a wrong key never falls back


def test_key_compare_is_constant_time_by_construction():
    src = (ROOT / "core_functions" / "operator_auth.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src
    assert oa.keys_match("a" * 40, "a" * 40) and not oa.keys_match("a" * 40, "a" * 39) and not oa.keys_match("", "x")


def test_key_only_mode_refuses_the_legacy_header(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setenv("OPERATOR_LEGACY_ADMIN_ID", "0")
    c = operator_app()
    assert c.get("/open", headers={"x-admin-id": "111"}).status_code == 503        # nothing configured: fail closed
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    assert c.get("/open", headers={"x-admin-id": "111"}).status_code == 403
    assert c.get("/open", headers={"X-Operator-Key": OPERATOR_KEY}).status_code == 200


def test_an_app_token_never_satisfies_the_guard(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    FakeDirectory({"adm": {"role": 1, "org": "o"}}).install()
    c = operator_app()
    # a Bearer token that is not an operator session is "not authenticated" (401; the console redirects to login)
    assert c.get("/open", headers=auth("adm", "o", 1)).status_code == 401
    assert c.get("/login", headers=auth("adm", "o", 1)).status_code == 401
    # not even when the token value is sent as the key
    token = auth("adm", "o", 1)["Authorization"].split()[1]
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    assert c.get("/open", headers={"X-Operator-Key": token}).status_code == 403


def test_a_lookup_failure_is_503_not_open(monkeypatch):
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")

    def boom(phone):
        raise RuntimeError("database down")
    oa.set_lookup(boom)
    assert operator_app().get("/open", headers={"x-admin-id": "111"}).status_code == 503


def test_failed_attempts_lock_the_ip_even_for_a_correct_key(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("RATE_LIMIT_OPERATOR_FAIL", "3/60")
    clock = [0.0]
    rate_limit.set_clock(lambda: clock[0])
    c = operator_app()
    for _ in range(3):
        assert c.get("/open", headers={"X-Operator-Key": "nope"}).status_code == 403
    r = c.get("/open", headers={"X-Operator-Key": OPERATOR_KEY})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    clock[0] = 61.0
    assert c.get("/open", headers={"X-Operator-Key": OPERATOR_KEY}).status_code == 200


def test_successful_calls_do_not_count_as_failures(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("RATE_LIMIT_OPERATOR_FAIL", "2/60")
    c = operator_app()
    for _ in range(10):
        assert c.get("/open", headers={"X-Operator-Key": OPERATOR_KEY}).status_code == 200


def test_router_auth_helpers_delegate_to_the_one_guard(monkeypatch):
    """`_require_gen_admin` / `_require_role1` of router/auth.py are thin wrappers of operator_auth.authorize."""
    import router.auth as ra
    seen = []
    monkeypatch.setattr(ra.operator_auth, "authorize", lambda request, **kw: seen.append(kw) or "legacy")
    ra._require_gen_admin(object())
    ra._require_role1(object())
    assert seen[0] == {"always_check": True}
    assert seen[1]["super_only"] is True and seen[1]["always_check"] is True
    assert "Only role_id=1" in seen[1]["denied_detail"]


# ------------------------------------------------------------------------------------------------------ every operator route
def _modules():
    files = {f: (ROOT / "router" / f) for f in {f for f, _m, _p in mounted_routes() if f != "email.py"}}
    files["email.py"] = ROOT / "utils" / "email.py"
    return files


def _guard_refs(node) -> set:
    refs = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            refs.add(n.id)
        elif isinstance(n, ast.Attribute):
            refs.add(n.attr)
    return refs & GUARD_NAMES


def _dependencies_of(call: ast.Call) -> set:
    for kw in call.keywords:
        if kw.arg == "dependencies":
            return _guard_refs(kw.value)
    return set()


def guarded_routes() -> dict:
    """{(file, METHOD, normalised path): evidence} for every route whose router, decorator or body references the guard."""
    found = {}
    for file, path in _modules().items():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        prefixes, router_guards = {}, {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                    and _is_api_router(node.value)):
                prefixes[node.targets[0].id] = _prefix_of(node.value) or ""
                router_guards[node.targets[0].id] = _dependencies_of(node.value)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                route = _decorator_route(dec)
                if not route or route[0] not in prefixes:
                    continue
                evidence = router_guards[route[0]] | _dependencies_of(dec) | _guard_refs(ast.Module(body=node.body, type_ignores=[]))
                if evidence:
                    found[(file, route[1], _norm(prefixes[route[0]] + route[2]))] = evidence
    return found


def test_every_operator_route_of_the_policy_references_the_one_guard():
    guarded = guarded_routes()
    missing = []
    checked = 0
    for file, method, path in mounted_routes():
        if method == "WEBSOCKET" or P.classify(method, path) != P.OPERATOR:
            continue
        checked += 1
        if (file, method, _norm(path)) not in guarded:
            missing.append((file, method, path))
    assert checked >= 30, "the operator route list shrank or the route table was not read"
    assert not missing, f"operator routes that do not reference core_functions.operator_auth: {missing}"


def test_the_gen_admin_user_routes_that_always_demanded_a_login_still_do():
    guarded = guarded_routes()
    for needle in (("auth.py", "GET", "/auth/gen-admin-users"), ("auth.py", "DELETE", "/auth/gen-admin-users/{}")):
        assert needle in guarded, needle


def test_the_registration_duplicate_checks_are_public_since_qw13():
    """check-mobile / check-email no longer demand an operator credential (the app calls them before it has a session)."""
    guarded = guarded_routes()
    for needle in (("auth.py", "GET", "/auth/check-mobile/{}"), ("auth.py", "GET", "/auth/check-email/{}")):
        assert needle not in guarded, needle
    for path in ("/auth/check-mobile/9876543210", "/auth/check-email/a@b.co"):
        assert P.classify("GET", path) == P.PUBLIC


def test_the_guard_is_the_only_operator_mechanism():
    """No operator route may still carry its own x-admin-id parsing: the header is read in operator_auth.py only."""
    offenders = []
    for file, path in _modules().items():
        if "x-admin-id" in path.read_text(encoding="utf-8-sig").lower():
            offenders.append(file)
    assert offenders == []


def test_real_routers_refuse_without_a_credential_when_enforced(monkeypatch):
    """The real gen_dashboard / plans / support / company routers, mounted as they are: 403 before any handler runs."""
    import router.company as company
    import router.gen_dashboard as gd
    import router.gen_plans as plans
    import router.support as support
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    app = FastAPI()
    for module in (gd, plans, support, company):
        app.include_router(module.router)
    c = TestClient(app)
    for method, path, kw in (("GET", "/gen-dashboard/stats", {}), ("GET", "/gen-dashboard/users", {}),
                             ("DELETE", "/gen-dashboard/organizations/o1", {"json": {"changed_by": "x"}}),
                             ("GET", "/plans/all", {}), ("POST", "/plans/coupons", {"json": {"coupon_name": "A", "discount": 5}}),
                             ("PUT", "/plans/9", {"json": {"plan_name": "p", "plan_amount": 1, "plan_month": 1}}),
                             ("DELETE", "/plans/9", {}), ("GET", "/support/requests/all", {}),
                             ("PUT", "/support/update/3", {"json": {"status": "closed"}}),
                             ("GET", "/company/deletion-requests", {}), ("GET", "/company/suggest-feature", {}),
                             ("GET", "/company/suggest-feature/with-replies", {}),
                             ("POST", "/company/suggest-feature/4/reply", {"json": {"reply_text": "x"}})):
        r = c.request(method, path, **kw)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    # the app-facing neighbours stay open
    monkeypatch.setattr(plans, "get_plans", _async({"success": True, "plans": []}))
    assert c.get("/plans/get_plans").status_code == 200


def test_real_routers_serve_the_operator_with_the_key(monkeypatch):
    import router.gen_dashboard as gd
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    monkeypatch.setattr(gd, "get_super_admin_stats", _async({"success": True, "stats": {}}))
    app = FastAPI()
    app.include_router(gd.router)
    c = TestClient(app)
    assert c.get("/gen-dashboard/stats").status_code == 403
    r = c.get("/gen-dashboard/stats", headers={"X-Operator-Key": OPERATOR_KEY})
    assert r.status_code == 200 and r.json() == {"success": True, "stats": {}}


def test_with_require_auth_the_global_guard_refuses_an_app_token_before_the_operator_guard(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.setenv("OPERATOR_API_KEY", OPERATOR_KEY)
    FakeDirectory({"adm": {"role": 1, "org": "org-A"}}).install()
    app = make_app()                                   # real global guard; /gen-dashboard/stats is a dummy route there
    c = TestClient(app)
    assert c.get("/gen-dashboard/stats", headers=auth("adm", "org-A", 1)).status_code == 403
    assert c.get("/gen-dashboard/stats").status_code == 200        # no token: left to the operator mechanism of the route


def _async(value):
    async def fn(*a, **k):
        return value
    return fn
