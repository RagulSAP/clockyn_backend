"""REQUIRE_AUTH=1 sweep over the REAL application (S-01): every route that app.py really mounts, no database.

  * every HTTP route that is not PUBLIC answers 401 without a token (the guard rejects before the handler runs, so
    nothing is stubbed except the pool, which fails the test if it is ever reached);
  * every operator route refuses a request without operator credentials (401 / 403, never a 2xx / 5xx);
  * every WebSocket route refuses the handshake without `?token=` (close code 4401);
  * every PUBLIC / OPERATOR / ROLE rule of core_functions/auth_policy.py matches at least one mounted route, so the lists
    cannot go stale, and the classification of the mounted routes is printed by `pytest -s` for review.

tests/test_route_auth_matrix.py does the same statically (AST); this one proves it on the running FastAPI app.
Run: python -m pytest tests/test_auth_route_sweep.py -q
"""
import re
import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import KEY  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from core_functions import auth_policy as P  # noqa: E402
from utils import rate_limit  # noqa: E402

SKIPPED_METHODS = {"HEAD", "OPTIONS"}


@pytest.fixture(scope="module")
def real_app():
    import app as app_module
    return app_module.app


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _walk(routes):
    """Every effective route with its full path. Newer FastAPI keeps `include_router` as a lazy tree
    (`_IncludedRouter` -> `effective_candidates()`), older versions copy the routes flat into `app.routes`."""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            for item in route.effective_candidates():
                if hasattr(item, "effective_candidates"):
                    yield from _walk([item])
                else:
                    yield getattr(item, "starlette_route", None) or item
        else:
            yield route


def _http_routes(application):
    out = []
    for route in _walk(application.routes):
        if isinstance(route, APIWebSocketRoute):
            continue
        for method in sorted((getattr(route, "methods", None) or set()) - SKIPPED_METHODS):   # includes /docs, /openapi.json, /
            out.append((method, route.path))
    return out


def _ws_routes(application):
    return [route.path for route in _walk(application.routes) if isinstance(route, APIWebSocketRoute)]


@pytest.fixture
def enforced(monkeypatch, real_app):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    monkeypatch.delenv("OPERATOR_ENFORCE", raising=False)
    ad.reset_state()
    rate_limit.reset()                                  # the operator guard counts anonymous failures per IP

    def no_database(*_a, **_k):
        raise AssertionError("a handler reached the database although no token was sent")
    import core_functions.db_function as dbf
    monkeypatch.setattr(dbf, "get_db_connection", no_database)

    def no_lookup(*_a, **_k):
        raise AssertionError("the guard looked a user up without a token")
    ad.set_loaders(user_state=no_lookup, entity_org=no_lookup, permissions=no_lookup)
    yield TestClient(real_app, raise_server_exceptions=False)
    ad.reset_state()
    rate_limit.reset()


def test_the_real_app_exposes_the_expected_surface(real_app):
    routes = _http_routes(real_app)
    assert len(routes) > 250
    assert ("POST", "/auth/validate-otp") in routes and ("GET", "/openapi.json") in routes
    assert {"/ws/chat/{user_id}", "/ws/badge/{user_id}"} <= set(_ws_routes(real_app))


def test_every_non_public_http_route_is_401_without_a_token(enforced, real_app):
    leaks, unclassified = [], []
    checked = 0
    for method, path in _http_routes(real_app):
        kind = P.classify(method, path)
        if kind not in (P.PUBLIC, P.OPERATOR, P.AUTHENTICATED):
            unclassified.append((method, path, kind))
            continue
        if kind != P.AUTHENTICATED:
            continue
        r = enforced.request(method, _concrete(path))
        checked += 1
        if r.status_code != 401:
            leaks.append((method, path, r.status_code))
    assert not unclassified, f"routes without a known class: {unclassified}"
    assert not leaks, f"authenticated routes that answer something else than 401 without a token: {leaks}"
    assert checked > 200


@pytest.mark.parametrize("header", ["Bearer garbage", "Bearer a.b.c", "Basic abc"])
def test_an_invalid_token_is_401_on_a_sample_of_every_router(enforced, real_app, header):
    seen_prefix, leaks = set(), []
    for method, path in _http_routes(real_app):
        if P.classify(method, path) != P.AUTHENTICATED:
            continue
        prefix = "/" + path.strip("/").split("/", 1)[0]
        if (prefix, method) in seen_prefix:
            continue
        seen_prefix.add((prefix, method))
        r = enforced.request(method, _concrete(path), headers={"Authorization": header})
        if r.status_code != 401:
            leaks.append((method, path, r.status_code))
    assert not leaks, leaks


def test_every_operator_route_refuses_a_request_without_operator_credentials(enforced, real_app):
    """The guard leaves operator routes to the operator mechanism of the handler (owned by core_functions/operator_auth.py,
    tested in tests/test_operator_auth.py). Without any credential a 4xx must come back (401 / 403, 422 when the body is
    validated first, 429 once the anonymous-failure throttle is open); a 2xx or a 5xx (the handler reached the pool,
    which this fixture makes fail) means the route is open."""
    open_routes = []
    for method, path in _http_routes(real_app):
        if P.classify(method, path) != P.OPERATOR:
            continue
        r = enforced.request(method, _concrete(path))
        if not 400 <= r.status_code < 500:
            open_routes.append((method, path, r.status_code))
    assert not open_routes, f"operator routes that do not refuse an anonymous call: {open_routes}"


def test_an_app_token_is_never_accepted_on_an_operator_route(enforced, real_app, monkeypatch):
    from auth_fakes import FakeDirectory, auth
    FakeDirectory({"adm": {"role": 1, "org": "org-A", "orgs": {"org-A"}}}, orgs_of_org_ids=("org-A",)).install()
    headers = auth("adm", "org-A", 1)
    wrong = []
    for method, path in _http_routes(real_app):
        if P.classify(method, path) != P.OPERATOR:
            continue
        r = enforced.request(method, _concrete(path), headers=headers)
        if r.status_code != 403:
            wrong.append((method, path, r.status_code))
    assert not wrong, f"an administrator's app token must be refused with 403 on operator routes: {wrong}"


def test_every_websocket_route_refuses_the_handshake_without_a_token(enforced, real_app):
    user_sockets = {"/ws/chat/{user_id}", "/ws/group-chat/{group_id}/{user_id}", "/ws/badge/{user_id}"}
    paths = _ws_routes(real_app)
    assert user_sockets <= set(paths)
    for path in paths:
        for query in ("", "?token=not-a-token"):
            with pytest.raises(WebSocketDisconnect) as exc:
                with enforced.websocket_connect(_concrete(path) + query):
                    pass
            if path in user_sockets:
                assert exc.value.code == ad.WS_CLOSE_UNAUTHORIZED, path       # the code the app reacts to
            else:
                assert exc.value.code in (4401, 4403), (path, exc.value.code)  # e.g. /ws/support-ops: operator credential


def test_every_public_rule_matches_at_least_one_mounted_route(real_app):
    routes = _http_routes(real_app)
    ws = [("GET", p) for p in _ws_routes(real_app)]
    stale = [(methods, pattern) for methods, pattern in P.PUBLIC_RULES
             if not any(P._method_ok(methods, m) and P.pattern_matches(pattern, p) for m, p in routes + ws)]
    assert not stale, f"PUBLIC_RULES that match no mounted route (a dead public rule would make a future route public): {stale}"


def test_every_operator_and_role_rule_matches_at_least_one_mounted_route(real_app):
    routes = _http_routes(real_app)
    stale_operator = [(m, p) for m, p in P.OPERATOR_RULES
                      if not any(P._method_ok(m, mm) and P.pattern_matches(p, pp) for mm, pp in routes)]
    stale_role = [(r.methods, r.pattern) for r in P.ROLE_RULES
                  if not any(P._method_ok(r.methods, m) and P.pattern_matches(r.pattern, p) for m, p in routes)]
    assert not stale_operator and not stale_role, (stale_operator, stale_role)


def test_public_routes_are_only_the_documented_ones(real_app):
    public = {re.sub(r"\{[^}]+\}", "{}", path) for method, path in _http_routes(real_app) if P.classify(method, path) == P.PUBLIC}
    assert public == {
        "/", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json",
        "/health", "/ready", "/version/check",
        "/auth/config", "/auth/generate-otp", "/auth/validate-otp", "/auth/verify-device-token", "/auth/login",
        "/auth/employee-login", "/auth/register", "/auth/register-company", "/auth/logout",
        "/auth/admin-login", "/auth/admin-login/verify-otp",
        "/auth/check-mobile/{}", "/auth/check-email/{}", "/auth/org-logo/{}", "/auth/session-events/{}",
        "/profile-photo/{}", "/requests/device-change/submit", "/referral/validate",
        "/webhook/razorpay", "/webhook/msg91/whatsapp", "/invoice/download",
    }, sorted(public)


def test_print_the_routes_any_signed_in_role_may_call(real_app, capsys):
    """Not an assertion (review aid): `pytest -s tests/test_auth_route_sweep.py -k print` lists the WRITE routes that have
    no role rule, i.e. routes a staff member may call with a valid token."""
    rows = sorted(f"{m:7s} {p}" for m, p in _http_routes(real_app)
                  if m in P.NON_GET and P.classify(m, p) == P.AUTHENTICATED and P.role_rule_for(m, p) is None)
    print("\n".join(rows))
    assert rows
