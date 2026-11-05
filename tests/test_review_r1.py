"""Regression tests found by the independent security/realtime review (R1). DB-free."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402

USERS = {
    "stf": {"role": 3, "org": "org-A"},
    "adm": {"role": 1, "org": "org-A", "orgs": {"org-A"}},
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory(USERS, {}, orgs_of_org_ids=("org-A", "org-B")).install()
    yield
    ad.reset_state()


def test_json_body_without_content_type_cannot_dodge_the_org_check():
    # FastAPI 0.128 still parses a body with NO Content-Type header as JSON, so the guard must look at it too.
    c = TestClient(make_app())
    r = c.post("/api/leads/create", headers=auth("stf", "org-A", 3), content=b'{"org_id": "org-B"}')
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------------------------- logout under REQUIRE_AUTH
def test_logout_by_bare_user_id_cannot_sign_somebody_else_out_when_auth_is_required():
    from core_functions import auth_function as af
    from wp7_fakes import RouteCursor
    import wp7_fakes

    class MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    cur = RouteCursor()
    conn = wp7_fakes.patch_db(MP(), af, cur)
    # no device token, no matching identity: nothing is cleared, still the forgiving 200
    assert af.logout_user(af.LogoutRequest(user_id="stf")) == {"success": True}
    assert not cur.has("UPDATE users SET device_token = NULL")
    # the caller's own valid token may sign the user out
    af.logout_user(af.LogoutRequest(user_id="stf"), caller_id="stf")
    assert cur.has("UPDATE users SET device_token = NULL")
    # a device token (the secret) is enough on its own
    cur2 = RouteCursor()
    wp7_fakes.patch_db(MP(), af, cur2)
    af.logout_user(af.LogoutRequest(user_id="stf", device_token="dev-A"))
    assert cur2.has("UPDATE users SET device_token = NULL")


# ---------------------------------------------------------------------------------------------- manager_id is an id key
def test_manager_id_of_another_organisation_is_refused():
    from core_functions import auth_policy as P
    me = P.Identity("mgr", "org-A", 2)
    ids = P.collect_ids({"manager_id": "mgr-of-B"}, None, None)
    assert ids == [("path", "manager_id", "mgr-of-B")]
    assert P.ids_to_resolve(me, ids, frozenset({"org-A"})) == ["mgr-of-B"]
    assert P.ownership_violation(me, ids, frozenset({"org-A"}), {"mgr-of-B": "org-B"}) == ("path", "manager_id")
    assert P.ownership_violation(me, ids, frozenset({"org-A"}), {"mgr-of-B": "org-A"}) is None
    own = P.collect_ids({"manager_id": "mgr"}, None, None)
    assert P.ownership_violation(me, own, frozenset({"org-A"}), {}) is None


# ---------------------------------------------------------------------------------------------- request actor check
def test_request_actor_check_follows_require_auth_unless_set_explicitly(monkeypatch):
    from core_functions import requests_functions as rf
    monkeypatch.delenv("REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("REQUESTS_ENFORCE_ACTOR", raising=False)
    assert rf.enforce_request_actor() is False            # default configuration: log only
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    assert rf.enforce_request_actor() is True              # auth required: another company's request cannot be decided
    monkeypatch.setenv("REQUESTS_ENFORCE_ACTOR", "0")
    assert rf.enforce_request_actor() is False             # an explicit setting wins
    monkeypatch.setenv("REQUIRE_AUTH", "")
    monkeypatch.setenv("REQUESTS_ENFORCE_ACTOR", "1")
    assert rf.enforce_request_actor() is True
