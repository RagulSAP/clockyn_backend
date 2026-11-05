"""Closing the open endpoints (WP12 K2), in the style of tests/test_idor_matrix.py: the REAL routers (profile photo,
company, auth) mounted behind the real global guard, with REQUIRE_AUTH on and off. Handlers' storage / database calls
are replaced by stubs. Run: python -m pytest tests/test_open_endpoints.py -q
"""
import ast
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from core_functions import auth_policy as P  # noqa: E402
from test_route_table import mounted_routes  # noqa: E402

USERS = {
    "adm": {"role": 1, "org": "org-A"},
    "mgr": {"role": 2, "org": "org-A"},
    "stf": {"role": 3, "org": "org-A"},
    "stf2": {"role": 3, "org": "org-A"},
    "adm-b": {"role": 1, "org": "org-B"},
    "stf-b": {"role": 3, "org": "org-B"},
}
CALLERS = {
    "admin": auth("adm", "org-A", 1),
    "manager": auth("mgr", "org-A", 2),
    "staff": auth("stf", "org-A", 3),
    "admin-of-B": auth("adm-b", "org-B", 1),
}
OK, NO, UNAUTH = 200, 403, 401
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(scope="module")
def client():
    import router.auth as ra
    import router.company as co
    import router.profile_photo as pp
    mp = pytest.MonkeyPatch()
    mp.setenv("AUTH_TOKEN_SECRET", KEY)
    mp.setenv("REQUIRE_AUTH", "1")
    mp.setenv("RATE_LIMIT_ENABLED", "0")
    FakeDirectory(USERS, orgs_of_org_ids=("org-A", "org-B")).install()

    async def ok(user_id):
        return {"success": True, "deleted": user_id}
    mp.setattr(co, "delete_user_account", ok)
    mp.setattr(co, "request_account_deletion", ok)
    mp.setattr(pp, "save_employee_profile", lambda uid, data: "https://s/" + uid)
    mp.setattr(pp, "get_employee_profile_url", lambda uid: "https://s/" + uid)
    mp.setattr(ra, "save_employee_profile", lambda uid, data: "https://s/" + uid)
    mp.setattr(ra, "save_org_logo", lambda org, data: "https://s/" + org)

    class Cur:
        def execute(self, *a):
            pass

        def fetchone(self):
            return {"mobile_number": "9876543210"}

        def close(self):
            pass

    class Conn:
        def cursor(self, cursor_factory=None):
            return Cur()
    mp.setattr(ra, "get_db_connection", lambda: Conn())
    mp.setattr(ra, "return_connection", lambda c: None)
    app = make_app()
    for module in (pp, co, ra):
        app.include_router(module.router)
    yield TestClient(app)
    mp.undo()
    ad.reset_state()


def photo(c, user, headers=None):
    return c.post("/profile-photo/" + user, files={"photo": ("p.png", PNG, "image/png")}, headers=headers or {})


# ---------------------------------------------------------------------------------------------------- profile photo
# who may WRITE the photo of whom: the owner, or an administrator / manager of that user's organisation
PHOTO_TABLE = {
    "admin":      {"adm": OK, "stf": OK, "stf2": OK, "mgr": OK, "stf-b": NO, "adm-b": NO},
    "manager":    {"mgr": OK, "stf": OK, "stf2": OK, "adm": OK, "stf-b": NO, "adm-b": NO},
    "staff":      {"stf": OK, "stf2": NO, "mgr": NO, "adm": NO, "stf-b": NO},
    "admin-of-B": {"adm-b": OK, "stf-b": OK, "stf": NO, "adm": NO},
}


def photo_cells():
    for caller, row in PHOTO_TABLE.items():
        for target, expected in row.items():
            yield pytest.param(caller, target, expected, id=f"{caller}-{target}")


@pytest.mark.parametrize("caller, target, expected", list(photo_cells()))
def test_profile_photo_upload_matrix(client, caller, target, expected):
    r = photo(client, target, CALLERS[caller])
    assert r.status_code == expected, r.text
    if expected == OK:
        assert r.json() == {"success": True, "photo_url": "https://s/" + target}       # response shape unchanged
    else:
        assert r.json() == {"detail": "Forbidden"}


def test_profile_photo_upload_without_a_token_is_401_but_reading_stays_public(client):
    r = photo(client, "stf")
    assert r.status_code == UNAUTH and r.headers["www-authenticate"] == "Bearer"
    r = client.get("/profile-photo/stf")
    assert r.status_code == 200 and r.json() == {"success": True, "photo_url": "https://s/stf"}


def test_profile_photo_upload_with_the_flag_off_is_unchanged(monkeypatch, client):
    monkeypatch.delenv("REQUIRE_AUTH")
    assert photo(client, "stf").status_code == 200                      # no token, no enforcement: as before
    assert photo(client, "stf2", CALLERS["staff"]).status_code == 200   # ids are not checked while the flag is off


def test_other_upload_routes_are_owner_or_admin(client):
    f = {"photo": ("p.png", PNG, "image/png")}
    assert client.post("/auth/upload-employee-profile/stf", files=f, headers=CALLERS["staff"]).status_code == OK
    assert client.post("/auth/upload-employee-profile/stf2", files=f, headers=CALLERS["staff"]).status_code == NO
    assert client.post("/auth/upload-employee-profile/stf", files=f, headers=CALLERS["admin-of-B"]).status_code == NO
    assert client.post("/auth/upload-employee-profile/stf").status_code == UNAUTH
    logo = {"logo": ("l.png", PNG, "image/png")}
    assert client.post("/auth/upload-org-logo/org-A", files=logo, headers=CALLERS["admin"]).status_code == OK
    assert client.post("/auth/upload-org-logo/org-A", files=logo, headers=CALLERS["manager"]).status_code == NO   # admin only
    assert client.post("/auth/upload-org-logo/org-A", files=logo, headers=CALLERS["staff"]).status_code == NO
    assert client.post("/auth/upload-org-logo/org-A", files=logo, headers=CALLERS["admin-of-B"]).status_code == NO
    assert client.post("/auth/upload-org-logo/org-A", files=logo).status_code == UNAUTH


# ---------------------------------------------------------------------------------------------------- owner-only routes
OWNER_ROUTES = [("GET", "/auth/user-mobile/{}"), ("DELETE", "/company/delete-account/{}"),
                ("POST", "/company/request-deletion/{}")]
# caller -> {target user id: expected}: only the caller's own id (not even an administrator for a colleague)
OWNER_TABLE = {
    "admin":      {"adm": OK, "stf": NO, "mgr": NO, "stf-b": NO, "adm-b": NO},
    "manager":    {"mgr": OK, "adm": NO, "stf": NO},
    "staff":      {"stf": OK, "stf2": NO, "adm": NO, "mgr": NO},
    "admin-of-B": {"adm-b": OK, "stf-b": NO, "adm": NO},
}


def owner_cells():
    for (method, pattern) in OWNER_ROUTES:
        for caller, row in OWNER_TABLE.items():
            for target, expected in row.items():
                yield pytest.param(method, pattern, caller, target, expected, id=f"{method}{pattern}-{caller}-{target}")


@pytest.mark.parametrize("method, pattern, caller, target, expected", list(owner_cells()))
def test_owner_only_routes(client, method, pattern, caller, target, expected):
    r = client.request(method, pattern.format(target), headers=CALLERS[caller])
    assert r.status_code == expected, r.text
    if expected == NO:
        assert r.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("method, pattern", OWNER_ROUTES)
def test_owner_only_routes_need_a_token_when_the_flag_is_on(client, method, pattern):
    assert client.request(method, pattern.format("stf")).status_code == UNAUTH


def test_user_mobile_keeps_its_response_shape_for_the_owner(client):
    r = client.get("/auth/user-mobile/adm", headers=CALLERS["admin"])
    assert r.status_code == 200 and r.json() == {"success": True, "mobile_number": "9876543210"}


def test_owner_only_routes_with_the_flag_off(monkeypatch, client):
    monkeypatch.delenv("REQUIRE_AUTH")
    for method, pattern in OWNER_ROUTES:
        assert client.request(method, pattern.format("stf")).status_code == 200          # no token: unchanged behaviour
    # a token that identifies somebody else is still refused (the identity is known even though nothing is enforced)
    assert client.get("/auth/user-mobile/stf", headers=CALLERS["admin"]).status_code == NO


# ---------------------------------------------------------------------------------------------------- policy data
def test_the_owner_only_list_is_explicit_and_not_stale():
    routes = mounted_routes()
    for methods, pattern, key in P.OWNER_ONLY_RULES:
        assert key == "user_id"
        assert any(P._method_ok(methods, m) and P.pattern_matches(pattern, p) for _f, m, p in routes), (methods, pattern)
    assert {(m[0], p) for m, p, _k in P.OWNER_ONLY_RULES} == {("GET", "/auth/user-mobile/{}"),
                                                              ("DELETE", "/company/delete-account/{}"),
                                                              ("POST", "/company/request-deletion/{}")}


def test_every_owner_only_route_has_the_owner_dependency():
    """AST: the mounted handler of each OWNER_ONLY_RULES entry lists `Depends(require_owner)`."""
    found = set()
    for file in ("auth.py", "company.py"):
        tree = ast.parse((ROOT / "router" / file).read_text(encoding="utf-8-sig"))
        prefix = {"auth.py": "/auth", "company.py": "/company"}[file]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.args \
                        and isinstance(dec.args[0], ast.Constant):
                    deps = [kw for kw in dec.keywords if kw.arg == "dependencies"]
                    if deps and "require_owner" in ast.dump(deps[0].value):
                        found.add((dec.func.attr.upper(), P.normalise_path(prefix + dec.args[0].value)
                                   .replace("{user_id}", "{}")))
    for methods, pattern, _key in P.OWNER_ONLY_RULES:
        assert (methods[0], pattern) in found, (methods, pattern)


@pytest.mark.parametrize("method, path, expected", [
    ("GET", "/profile-photo/u1", P.PUBLIC), ("POST", "/profile-photo/u1", P.AUTHENTICATED),
    ("PUT", "/profile-photo/u1", P.AUTHENTICATED), ("DELETE", "/profile-photo/u1", P.AUTHENTICATED),
    ("GET", "/invoice/download", P.PUBLIC), ("POST", "/invoice/download", P.AUTHENTICATED),
    ("GET", "/invoice/history", P.AUTHENTICATED), ("GET", "/invoice/bulk-download-link", P.OPERATOR),
    ("POST", "/auth/upload-employee-profile/u1", P.AUTHENTICATED), ("POST", "/auth/upload-org-logo/o", P.AUTHENTICATED),
    ("GET", "/auth/user-mobile/u1", P.AUTHENTICATED), ("DELETE", "/company/delete-account/u1", P.AUTHENTICATED),
    ("POST", "/face/register", P.AUTHENTICATED), ("POST", "/face/punch", P.AUTHENTICATED),
    ("DELETE", "/staff-location/cleanup", P.AUTHENTICATED),
])
def test_classification_of_the_closed_endpoints(method, path, expected):
    assert P.classify(method, path) == expected


def test_destructive_and_maintenance_routes_are_restricted():
    """Every mounted DELETE route, and every route whose path says delete / cleanup / reset / purge / debug / wipe /
    truncate / drop / test / clear, is operator, owner-only or restricted by a role rule, except the two reviewed below.
    There is no `/face/cleanup*`, `/face/delete*` or debug / reset route in this code base."""
    words = ("delete", "cleanup", "reset", "purge", "debug", "wipe", "truncate", "drop", "test", "clear")
    open_ones = []
    for _f, method, path in mounted_routes():
        if method == "WEBSOCKET" or not (method == "DELETE" or any(w in path.lower() for w in words)):
            continue
        if P.classify(method, path) != P.AUTHENTICATED or P.role_rule_for(method, path) or P.owner_only_key(method, path):
            continue
        open_ones.append((method, path))
    assert sorted(open_ones) == [
        ("DELETE", "/api/leads/{}"),          # staff delete a lead of their own organisation (CRM scoping is WP10)
        ("DELETE", "/chat/delete/{}/{}"),     # a user deletes their own message: the first id is the caller (SELF/USER keys)
    ], "a new destructive route is open to every signed-in user: add a RoleRule / OWNER_ONLY_RULES entry or review it here"
    assert not [p for _f, _m, p in mounted_routes() if p.startswith("/face/") and
                any(w in p for w in ("cleanup", "delete", "reset", "purge"))]
