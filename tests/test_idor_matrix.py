"""IDOR matrix (WP12): role x id key x where the id travels x whose id it is, run through the real global guard with
REQUIRE_AUTH=1 on a small app. The expected status of every cell is written out by hand below (not computed by the
code under test). Run: python -m pytest tests/test_idor_matrix.py -q
"""
import itertools
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402

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

OK, NO = 200, 403

# --- org_id -----------------------------------------------------------------------------------------------------------
# caller -> {value: expected}. A manager may send their OWN user id as org_id (the app's convention); nobody else may.
ORG_TABLE = {
    "admin":      {"org-A": OK, "org-B": NO, "mgr": NO, "adm": NO, "stf": NO},
    "manager":    {"org-A": OK, "org-B": NO, "mgr": OK, "adm": NO, "stf": NO},
    "staff":      {"org-A": OK, "org-B": NO, "stf": NO, "mgr": NO},
    "admin-of-B": {"org-B": OK, "org-A": NO, "adm-b": NO},
}

# --- user_id / staff_id / employee_id -----------------------------------------------------------------------------------
# everyone may send their own id and their own org id (the app sends the org id under `user_id` on /home/*, /addstaff/*,
# /home/org-admin/{user_id}). Admin / manager: also users of their org; other orgs' users and org ids are refused; an id
# that exists nowhere passes (the handler answers 404). Staff: nothing else (no colleague, no manager, no admin).
ADMIN_USER_ROW = {"adm": OK, "stf": OK, "stf2": OK, "mgr": OK, "org-A": OK, "ghost": OK,
                  "stf-b": NO, "adm-b": NO, "org-B": NO}
USER_TABLE = {
    "admin":      ADMIN_USER_ROW,
    "manager":    {**ADMIN_USER_ROW, "adm": OK},
    "staff":      {"stf": OK, "stf2": NO, "mgr": NO, "adm": NO, "org-A": OK, "ghost": NO, "stf-b": NO, "org-B": NO},
    "admin-of-B": {"adm-b": OK, "stf-b": OK, "org-B": OK, "stf": NO, "adm": NO, "org-A": NO, "ghost": OK},
}

PATH_ROUTE = {"org_id": "/k/org/{v}", "user_id": "/k/user/{v}", "staff_id": "/k/staff/{v}", "employee_id": "/k/employee/{v}"}


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setenv("AUTH_TOKEN_SECRET", KEY)
    mp.setenv("REQUIRE_AUTH", "1")
    FakeDirectory(USERS, orgs_of_org_ids=("org-A", "org-B")).install()
    yield TestClient(make_app())
    mp.undo()
    ad.reset_state()


def cells():
    for caller, key in itertools.product(CALLERS, ("org_id", "user_id", "staff_id", "employee_id")):
        table = ORG_TABLE if key == "org_id" else USER_TABLE
        for value, expected in table[caller].items():
            yield pytest.param(caller, key, value, expected, id=f"{caller}-{key}-{value}")


@pytest.mark.parametrize("caller, key, value, expected", list(cells()))
def test_id_in_the_path(client, caller, key, value, expected):
    r = client.get(PATH_ROUTE[key].format(v=value), headers=CALLERS[caller])
    assert r.status_code == expected, r.text
    if expected == NO:
        assert r.json() == {"detail": "Forbidden"}


@pytest.mark.parametrize("caller, key, value, expected", list(cells()))
def test_id_in_the_query_string(client, caller, key, value, expected):
    r = client.get("/me", params={key: value}, headers=CALLERS[caller])
    assert r.status_code == expected, r.text


@pytest.mark.parametrize("caller, key, value, expected", list(cells()))
def test_id_in_the_json_body(client, caller, key, value, expected):
    r = client.post("/raw/json", json={key: value, "note": "x"}, headers=CALLERS[caller])
    assert r.status_code == expected, r.text


@pytest.mark.parametrize("caller, key, value, expected",
                         [c for c in cells() if c.values[1] in ("org_id", "user_id")])
def test_id_in_a_multipart_form(client, caller, key, value, expected):
    own = {"admin": ("adm", "org-A"), "manager": ("mgr", "org-A"), "staff": ("stf", "org-A"),
           "admin-of-B": ("adm-b", "org-B")}[caller]
    form = {"user_id": own[0], "org_id": own[1], "message": "m"}
    form[key] = value
    r = client.post("/support/submit", data=form, headers=CALLERS[caller])
    assert r.status_code == expected, r.text


@pytest.mark.parametrize("caller", list(CALLERS))
def test_a_request_with_no_id_in_it_is_only_subject_to_role_policy(client, caller):
    # /me carries no id: there is nothing to compare, every signed-in user is served
    assert client.get("/me", headers=CALLERS[caller]).status_code == 200


def test_second_id_in_the_same_request_is_still_checked(client):
    # own org_id in the path, someone else's user_id in the query: refused
    r = client.get("/api/leads/employee/org-A/stf2", headers=CALLERS["staff"])
    assert r.status_code == NO
    r = client.get("/api/leads/employee/org-A/stf", headers=CALLERS["staff"])
    assert r.status_code == OK
    r = client.get("/api/leads/employee/org-A/stf", params={"user_id": "stf2"}, headers=CALLERS["staff"])
    assert r.status_code == NO                                               # repeated key: every value must be the caller's


def test_staff_chat_routes_use_own_id_first_and_may_name_the_other_party(client):
    # other_user_id is the conversation partner (not an ownership key): staff may open a chat with a colleague
    assert client.get("/chat/messages/stf/stf2", headers=CALLERS["staff"]).status_code == OK
    assert client.get("/chat/messages/stf2/stf", headers=CALLERS["staff"]).status_code == NO


def test_numeric_and_nested_values_are_not_a_bypass(client):
    h = CALLERS["staff"]
    assert client.post("/raw/json", json={"org_id": 12345}, headers=h).status_code == NO
    assert client.post("/raw/json", json={"user_id": ["stf2"]}, headers=h).status_code == OK   # not a scalar: handler validation
    assert client.post("/raw/json", json=[{"org_id": "org-B"}], headers=h).status_code == OK   # top-level list: no keys
    assert client.post("/raw/json", json={"nested": {"org_id": "org-B"}}, headers=h).status_code == OK  # top-level keys only
