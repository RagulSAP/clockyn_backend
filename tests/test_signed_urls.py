"""Signed download links (WP12 K2): utils/signed_urls.py and the invoice endpoints of utils/email.py.
DB-free: the invoice query is answered by a fake connection and the PDF renderer is stubbed.
Run: python -m pytest tests/test_signed_urls.py -q
"""
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auth_fakes import FakeDirectory, KEY, auth, make_app  # noqa: E402
from core_functions import auth_dependency as ad  # noqa: E402
from utils import signed_urls as S  # noqa: E402

SECRET = "s" * 40
PATH = "/invoice/download"
PARAMS = {"user_id": "u1", "org_id": "org-A", "invoice_number": "INV/2026/0001"}


def query_of(url):
    return dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("AUTH_TOKEN_SECRET", "REQUIRE_AUTH", "SIGNED_URL_TTL_SECONDS", "OPERATOR_API_KEY", "OPERATOR_ENFORCE"):
        monkeypatch.delenv(name, raising=False)
    yield
    ad.reset_state()


# ---------------------------------------------------------------------------------------------------- the primitive
def test_sign_then_verify_round_trip():
    url = S.sign_url(PATH, PARAMS, now=1000, key=SECRET)
    assert url.startswith(PATH + "?")
    q = query_of(url)
    assert q["user_id"] == "u1" and q["invoice_number"] == "INV/2026/0001" and q["exp"] == str(1000 + 900)
    assert len(q["sig"]) == 64
    assert S.verify(PATH, q, now=1000, key=SECRET) == "ok"
    assert S.verify(PATH, q, now=1900, key=SECRET) == "ok"           # the last second
    assert S.verify(PATH, q, now=1901, key=SECRET) == "expired"


def test_default_lifetime_is_fifteen_minutes_and_is_configurable(monkeypatch):
    assert S.ttl_seconds() == 900
    monkeypatch.setenv("SIGNED_URL_TTL_SECONDS", "120")
    assert S.ttl_seconds() == 120
    for bad in ("5", "999999", "abc"):
        monkeypatch.setenv("SIGNED_URL_TTL_SECONDS", bad)
        assert S.ttl_seconds() == 900
    assert query_of(S.sign_url(PATH, PARAMS, now=0, ttl=60, key=SECRET))["exp"] == "60"


@pytest.mark.parametrize("field, value", [("user_id", "u2"), ("org_id", "org-B"), ("invoice_number", "INV/2026/0002"),
                                          ("exp", "9999999999"), ("sig", "0" * 64)])
def test_changing_any_signed_value_invalidates_the_link(field, value):
    q = query_of(S.sign_url(PATH, PARAMS, now=1000, key=SECRET))
    q[field] = value
    assert S.verify(PATH, q, now=1000, key=SECRET) == "invalid"


def test_an_extra_parameter_or_a_different_path_invalidates_the_link():
    q = query_of(S.sign_url(PATH, PARAMS, now=1000, key=SECRET))
    assert S.verify(PATH, {**q, "extra": "1"}, now=1000, key=SECRET) == "invalid"
    assert S.verify("/invoice/bulk-download", q, now=1000, key=SECRET) == "invalid"
    missing = {k: v for k, v in q.items() if k != "org_id"}
    assert S.verify(PATH, missing, now=1000, key=SECRET) == "invalid"


def test_parameter_order_does_not_matter():
    q = query_of(S.sign_url(PATH, PARAMS, now=1000, key=SECRET))
    assert S.verify(PATH, dict(reversed(list(q.items()))), now=1000, key=SECRET) == "ok"


def test_a_different_key_does_not_verify():
    q = query_of(S.sign_url(PATH, PARAMS, now=1000, key=SECRET))
    assert S.verify(PATH, q, now=1000, key="x" * 40) == "invalid"


def test_without_a_usable_secret_nothing_is_signed_and_nothing_verifies():
    assert S.sign_url(PATH, PARAMS, key="short") is None
    assert S.sign_url(PATH, PARAMS) is None                            # AUTH_TOKEN_SECRET unset
    q = query_of(S.sign_url(PATH, PARAMS, now=1000, key=SECRET))
    assert S.verify(PATH, q, now=1000) == "invalid"
    assert S.enabled() is False


def test_missing_and_malformed_signature_fields():
    assert S.verify(PATH, {"user_id": "u"}, key=SECRET) == "missing"
    assert S.verify(PATH, {"sig": "abc"}, key=SECRET) == "invalid"
    assert S.verify(PATH, {"exp": "1"}, key=SECRET) == "invalid"
    assert S.verify(PATH, {"exp": "soon", "sig": "abc"}, key=SECRET) == "invalid"
    assert S.verify(PATH, {"exp": "1", "sig": "é" * 5}, key=SECRET) == "invalid"          # non-ASCII signature: no exception


def test_constant_time_compare_is_used():
    assert "hmac.compare_digest" in (ROOT / "utils" / "signed_urls.py").read_text(encoding="utf-8")


def test_none_params_sign_like_the_empty_string():
    q = query_of(S.sign_url(PATH, {"user_id": "u", "org_id": None}, now=5, key=SECRET))
    assert q["org_id"] == "" and S.verify(PATH, q, now=5, key=SECRET) == "ok"


# ---------------------------------------------------------------------------------------------------- /invoice/history
def test_history_rows_carry_a_signed_download_url_only_when_tokens_are_configured(monkeypatch):
    import utils.email as em
    row = {"invoice_id": 1, "invoice_number": "INV/1", "user_id": "u1", "org_id": "org-A", "base_amount": 1, "cgst": 0,
           "sgst": 0, "total_amount": 1}
    plain = em._invoice_row_to_dict(row, "u1", "org-A")["download_url"]
    assert plain == "/invoice/download?user_id=u1&org_id=org-A&invoice_number=INV/1"       # exactly as before
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    signed = em._invoice_row_to_dict(row, "u1", "org-A")["download_url"]
    q = query_of(signed)
    assert signed.startswith("/invoice/download?user_id=u1&org_id=org-A&invoice_number=INV%2F1&exp=")
    assert S.verify(PATH, q) == "ok"
    out = em._invoice_row_to_dict(row, "u1", "org-A")
    assert set(out) >= {"invoice_id", "invoice_number", "payment_id", "order_id", "download_url", "created_at"}  # shape kept


# ---------------------------------------------------------------------------------------------------- /invoice/download
class FakeCursor:
    def __init__(self, invoice_org="org-A", found=True):
        self.invoice_org, self.found, self.sql = invoice_org, found, []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        if "FROM gen_invoices" in self.sql[-1]:
            if not self.found:
                return None
            return {"customer_name": "C", "org_name": "O", "org_address": "a", "state": "s", "district": "d", "pincode": 1,
                    "org_id": self.invoice_org, "invoice_number": "INV1", "plan_name": "P", "staff_count": 2,
                    "base_amount": 100, "cgst": 9, "sgst": 9, "total_amount": 118, "payment_id": "p", "order_id": "o",
                    "payment_date": None, "valid_till": None}
        return None

    def close(self):
        pass


class FakeConn:
    def __init__(self, cursor):
        self._c = cursor

    def cursor(self, cursor_factory=None):
        return self._c


@pytest.fixture
def invoice_app(monkeypatch):
    import utils.email as em
    state = {"cursor": FakeCursor(), "rendered": 0}
    monkeypatch.setattr(em, "get_db_connection", lambda: FakeConn(state["cursor"]))
    monkeypatch.setattr(em, "return_connection", lambda c: None)

    def render(data):
        state["rendered"] += 1
        return b"%PDF-fake"
    monkeypatch.setattr(em, "_generate_invoice_pdf", render)
    app = make_app()                                  # the real global guard (identity attached, REQUIRE_AUTH decides)
    app.include_router(em.router)
    state["client"] = TestClient(app)
    return state


Q = {"user_id": "u1", "org_id": "org-A", "invoice_number": "INV1"}


def test_flag_off_the_unsigned_legacy_call_still_works(invoice_app):
    r = invoice_app["client"].get(PATH, params=Q)
    assert r.status_code == 200 and r.content == b"%PDF-fake" and r.headers["content-type"] == "application/pdf"
    assert 'filename="INV1.pdf"' in r.headers["content-disposition"]


def test_flag_off_a_bad_or_expired_signature_is_simply_ignored(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    c = invoice_app["client"]
    assert c.get(PATH, params={**Q, "exp": "1", "sig": "0" * 64}).status_code == 200      # legacy behaviour kept


def test_flag_on_a_valid_signature_needs_no_token(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({}).install()
    c = invoice_app["client"]
    url = S.sign_url(PATH, Q)
    assert c.get(url).status_code == 200


def test_flag_on_unsigned_without_a_token_is_401(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({}).install()
    r = invoice_app["client"].get(PATH, params=Q)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert invoice_app["rendered"] == 0


def test_flag_on_tampered_or_expired_signature_is_refused(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory({}).install()
    c = invoice_app["client"]
    q = query_of(S.sign_url(PATH, Q))
    assert c.get(PATH, params={**q, "invoice_number": "INV2"}).status_code == 401           # signature no longer matches
    expired = query_of(S.sign_url(PATH, Q, now=1))
    r = c.get(PATH, params=expired)
    assert r.status_code == 401 and r.json() == {"detail": "Link expired"}


USERS = {"adm": {"role": 1, "org": "org-A"}, "stf": {"role": 3, "org": "org-A"}, "mgr": {"role": 2, "org": "org-A"},
         "adm-b": {"role": 1, "org": "org-B"}}


def test_flag_on_token_access_is_admin_of_the_org_that_owns_the_invoice(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory(USERS, orgs_of_org_ids=("org-A", "org-B")).install()
    c = invoice_app["client"]
    assert c.get(PATH, params=Q, headers=auth("adm", "org-A", 1)).status_code == 200
    assert c.get(PATH, params=Q, headers=auth("stf", "org-A", 3)).status_code == 403             # role: administrators only
    assert c.get(PATH, params=Q, headers=auth("mgr", "org-A", 2)).status_code == 403
    # an administrator of ANOTHER organisation, asking for a link that omits the org (the row still decides)
    other = invoice_app["client"].get(PATH, params={"user_id": "u1", "invoice_number": "INV1"}, headers=auth("adm-b", "org-B", 1))
    assert other.status_code == 403 and other.json() == {"detail": "Forbidden"}


def test_flag_on_token_of_another_org_is_refused_when_the_org_id_is_named(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory(USERS, orgs_of_org_ids=("org-A", "org-B")).install()
    r = invoice_app["client"].get(PATH, params=Q, headers=auth("adm-b", "org-B", 1))
    # the public route is not covered by the guard's id check, so the handler's own org check decides: the invoice row
    # belongs to org-A and the caller owns org-B only
    assert r.status_code == 403


def test_flag_on_a_missing_invoice_is_404_for_an_admin(invoice_app, monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN_SECRET", KEY)
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    FakeDirectory(USERS, orgs_of_org_ids=("org-A",)).install()
    invoice_app["cursor"] = FakeCursor(found=False)
    r = invoice_app["client"].get(PATH, params=Q, headers=auth("adm", "org-A", 1))
    assert r.status_code == 404


# ---------------------------------------------------------------------------------------------------- bulk download
def test_bulk_download_link_is_operator_only_and_the_link_replaces_the_header(monkeypatch):
    import utils.email as em
    monkeypatch.setenv("AUTH_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("OPERATOR_API_KEY", "k" * 32)
    monkeypatch.setenv("OPERATOR_ENFORCE", "1")
    monkeypatch.setattr(em, "get_db_connection", lambda: (_ for _ in ()).throw(RuntimeError("stop here: auth passed")))
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(em.router)
    c = TestClient(app, raise_server_exceptions=False)
    params = {"from_date": "2026-01-01", "to_date": "2026-01-31"}
    assert c.get("/invoice/bulk-download-link", params=params).status_code == 403
    assert c.get("/invoice/bulk-download", params=params).status_code == 403
    r = c.get("/invoice/bulk-download-link", params=params, headers={"X-Operator-Key": "k" * 32})
    assert r.status_code == 200 and r.json()["expires_in"] == 900
    url = r.json()["url"]
    assert url.startswith("/invoice/bulk-download?") and S.verify("/invoice/bulk-download", query_of(url)) == "ok"
    assert c.get(url).status_code == 500                                  # passed the gate (and then hit the fake DB failure)
    tampered = url.replace("2026-01-31", "2026-12-31")
    assert c.get(tampered).status_code == 403


def test_bulk_download_link_needs_a_secret(monkeypatch):
    import utils.email as em
    monkeypatch.setenv("OPERATOR_API_KEY", "k" * 32)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(em.router)
    r = TestClient(app).get("/invoice/bulk-download-link", params={"from_date": "a", "to_date": "b"},
                            headers={"X-Operator-Key": "k" * 32})
    assert r.status_code == 503
