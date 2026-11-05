"""DB-free tests for WP11: identity, branding and configuration.

1. Guard: no legacy product name appears in code, SQL, templates or the README outside a small allow-list.
2. utils/branding.py: defaults and env overrides.
3. E-mail / invoice / WhatsApp / push-free text builders use APP_NAME and SELLER_* values from the environment.
4. CORS origins from env (helper and the middleware class that app.py mounts).

Run: python -m pytest tests/test_branding.py -q
"""
import ast
import asyncio
import email as email_lib
import io
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import branding  # noqa: E402
# Imported here (not inside the tests): these modules call load_dotenv() on import, which would otherwise
# re-populate variables that the autouse fixture below has just removed.
from utils import email as invoice_mail  # noqa: E402,F401
from core_functions import auth_function, suggest_feature_functions, whatsapp_service  # noqa: E402,F401

# ---------------------------------------------------------------------------
# 1. Guard test. The legacy tokens are assembled from pieces so this file never spells them.
# ---------------------------------------------------------------------------
_P = "punch" + "app"          # legacy product name (identifier form)
_V = "gen" + "sharp"          # legacy vendor name

# Identifiers that contain a legacy token and MUST stay until a database migration plan is executed
# (see project_complete_context/rename_inventory.md sections 3 and 8). Everything else is a failure.
ALLOWED_IDENTIFIERS = {
    _P + "_prod",                       # schema qualifier used by the root DDL file and by tests
    _P + "_dev",                        # dev schema name quoted in the runbook-style comments
    _P + "_qa",
    _P + "_subscription",               # existing tables (renaming = migration + same-release code change)
    _P + "_settings",
    _P + "_manager_permissions",
    _P + "_subscription_pkey",          # constraint / index names derived from the tables
    _P + "_subscription_subscription_id_key",
    _P + "_settings_pkey",
    _P + "_manager_permissions_pkey",
    _P + "_ddl_latest",                 # file name of the root DDL file (not read by code, cited by tests)
    _P + "_ddl_postgresql_new",         # old design-DDL file name quoted in a comment of the root DDL file
    _P + "_prod_x",                     # deliberately-similar schema name in tests/test_migrate.py
}

SCAN_SUFFIXES = {".py", ".sql", ".html", ".htm", ".jinja", ".jinja2", ".j2", ".txt"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "venv", ".venv", "node_modules",
             "project_complete_context"}   # the docs folder holds the rename inventory (the one allowed place)

_IDENT = re.compile(r"[A-Za-z0-9_]*(?:" + _P + "|" + _V + r")[A-Za-z0-9_]*", re.IGNORECASE)
_SPACED = re.compile(r"\bpunch[ _-]app\b", re.IGNORECASE)


def _scan_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if set(rel.parts) & SKIP_DIRS:
            continue
        if path.suffix.lower() in SCAN_SUFFIXES or rel.as_posix() == "README.md":
            yield path, rel


def _violations():
    found = []
    for path, rel in _scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            for m in _IDENT.finditer(line):
                if m.group(0).lower() not in ALLOWED_IDENTIFIERS:
                    found.append(f"{rel}:{lineno}: {m.group(0)}")
            for m in _SPACED.finditer(line):
                found.append(f"{rel}:{lineno}: {m.group(0)}")
    return found


def test_no_legacy_product_name_outside_allow_list():
    bad = _violations()
    assert not bad, "legacy product/vendor name found (use utils/branding.py or a neutral name):\n" + "\n".join(bad)


def test_guard_detects_a_planted_token():
    """The scanner itself works: the assembled tokens match, the allow-list entries are the only exemptions."""
    assert _IDENT.search("x = '" + _V.capitalize() + "_logo.png'")
    assert _SPACED.search("Welcome to " + "Punch" + " App")
    assert _IDENT.search("FROM " + _P + "_newtable").group(0).lower() not in ALLOWED_IDENTIFIERS
    assert _IDENT.search("FROM " + _P + "_subscription").group(0).lower() in ALLOWED_IDENTIFIERS


def test_scanner_covers_the_main_code_folders():
    rels = {rel.as_posix() for _, rel in _scan_files()}
    for expected in ("app.py", "utils/email.py", "core_functions/whatsapp_service.py", "README.md",
                     "utils/branding.py"):
        assert expected in rels


# ---------------------------------------------------------------------------
# 2. utils/branding.py
# ---------------------------------------------------------------------------
BRAND_VARS = ["APP_NAME", "SUPPORT_EMAIL", "SUPPORT_URL", "WEBSITE_URL", "SELLER_NAME", "SELLER_ADDRESS",
              "SELLER_GSTIN", "SELLER_CIN", "SELLER_EMAIL", "SELLER_PHONE", "SELLER_SAC",
              "SELLER_PLACE_OF_SUPPLY", "ANDROID_STORE_URL", "IOS_STORE_URL", "APP_PLAY_STORE_LINK",
              "APP_APP_STORE_LINK", "BRAND_LOGO_FILE", "BRAND_SIGNATURE_FILE", "MSG91_WHATSAPP_TEMPLATE",
              "CORS_ALLOWED_ORIGINS"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in BRAND_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_read_the_product_name_and_invent_nothing():
    assert branding.app_name() == "Clockyn"
    assert branding.seller_name() == "Clockyn"
    assert branding.support_email() == "" and branding.support_url() == "" and branding.website_url() == ""
    assert branding.seller_address_lines() == []
    assert branding.seller_gstin() == "" and branding.seller_cin() == ""
    assert branding.seller_sac() == "998314"
    assert branding.seller_place_of_supply() == ""
    assert branding.whatsapp_template() == ""
    assert branding.app_download_link() == ""
    assert branding.invoice_item_name("Core") == "Core - Clockyn Subscription"
    assert branding.invoice_pdf_filename() == "Clockyn_Invoice.pdf"
    assert branding.invoice_zip_filename("2026-01-01", "2026-01-31") == "clockyn_invoices_2026-01-01_to_2026-01-31.zip"
    assert branding.invoice_summary_filename("a", "b") == "clockyn_invoices_summary_a_to_b.xlsx"
    assert branding.initials() == "C"


def test_env_overrides_and_blank_values_fall_back(monkeypatch):
    monkeypatch.setenv("APP_NAME", "  Acme Time ")
    monkeypatch.setenv("SELLER_NAME", "   ")
    assert branding.app_name() == "Acme Time"
    assert branding.seller_name() == "Acme Time"          # blank SELLER_NAME falls back to APP_NAME
    assert branding.file_slug() == "AcmeTime"
    assert branding.initials() == "AT"
    monkeypatch.setenv("SELLER_ADDRESS", "1 Main Road;Pune - 411001\\nMaharashtra")
    assert branding.seller_address_lines() == ["1 Main Road", "Pune - 411001", "Maharashtra"]


def test_store_links_prefer_new_names_then_legacy_names_then_website(monkeypatch):
    monkeypatch.setenv("WEBSITE_URL", "https://example.test")
    assert branding.app_download_link() == "https://example.test"
    monkeypatch.setenv("APP_APP_STORE_LINK", "https://apps.example/ios-old")
    assert branding.app_download_link() == "https://apps.example/ios-old"
    monkeypatch.setenv("APP_PLAY_STORE_LINK", "https://play.example/old")
    assert branding.app_download_link() == "https://play.example/old"
    monkeypatch.setenv("ANDROID_STORE_URL", "https://play.example/new")
    assert branding.app_download_link() == "https://play.example/new"
    assert branding.ios_store_url() == "https://apps.example/ios-old"


def test_logo_file_names_stay_inside_static(monkeypatch):
    assert Path(branding.logo_path()).parent == ROOT / "static"
    monkeypatch.setenv("BRAND_LOGO_FILE", "../../etc/evil.png")
    assert Path(branding.logo_path()) == ROOT / "static" / "evil.png"
    monkeypatch.setenv("BRAND_SIGNATURE_FILE", "sig.png")
    assert Path(branding.signature_path()) == ROOT / "static" / "sig.png"


def test_legacy_assets_are_no_longer_referenced_by_code():
    for rel in ("utils/email.py", "utils/branding.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "sign.png" not in src
        assert _V not in src.lower()


# ---------------------------------------------------------------------------
# 3. Text builders
# ---------------------------------------------------------------------------
@pytest.fixture
def seller_env(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Zeta Clock")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@zeta.example")
    monkeypatch.setenv("WEBSITE_URL", "https://www.zeta.example")
    monkeypatch.setenv("SELLER_NAME", "Zeta Labs Pvt Ltd")
    monkeypatch.setenv("SELLER_ADDRESS", "7 Park Street;Kolkata - 700016")
    monkeypatch.setenv("SELLER_GSTIN", "19ABCDE1234F1Z5")
    monkeypatch.setenv("SELLER_CIN", "U00000WB2020PTC000000")
    monkeypatch.setenv("SELLER_PLACE_OF_SUPPLY", "West Bengal (19)")
    monkeypatch.setenv("SELLER_SAC", "123456")


INVOICE = {
    "customer_name": "Cust", "org_name": "Org", "bill_address": "Org\nCity", "ship_address": "Org\nCity",
    "invoice_number": "INV-2026-0001", "payment_date": "01/01/2026", "due_date": "01/01/2026",
    "plan_name": "Core", "staff_count": 3, "base_amount": 100.0, "cgst": 9.0, "sgst": 9.0, "total_amount": 118.0,
    "items": [{"name": "Core - sub", "description": "", "sac": "123456", "qty": 3, "unit": "Staff",
               "rate": 33.33, "amount": 100.0}],
}


def _story_text(story):
    """All paragraph markup inside a reportlab story (recursing through tables)."""
    from reportlab.platypus import Paragraph, Table
    out = []

    def walk(node):
        if isinstance(node, Paragraph):
            out.append(node.text)
        elif isinstance(node, Table):
            for row in node._cellvalues:
                for cell in row:
                    walk(cell)
        elif isinstance(node, (list, tuple)):
            for n in node:
                walk(n)
    walk(story)
    return "\n".join(out)


def _render_pdf_text(monkeypatch, data):
    from utils import email as em
    captured = {}

    class FakeDoc:
        def __init__(self, *a, **k):
            pass

        def build(self, story):
            captured["story"] = story

    monkeypatch.setattr(em, "SimpleDocTemplate", FakeDoc)
    em._generate_invoice_pdf(data)
    return _story_text(captured["story"]), captured["story"]


def test_invoice_pdf_header_uses_seller_env(monkeypatch, seller_env):
    text, _ = _render_pdf_text(monkeypatch, dict(INVOICE))
    assert "Zeta Labs Pvt Ltd" in text
    assert "CIN: U00000WB2020PTC000000" in text
    assert "7 Park Street" in text and "Kolkata - 700016" in text
    assert "GSTIN: 19ABCDE1234F1Z5" in text
    assert "West Bengal (19)" in text
    assert _P not in text.lower() and _V not in text.lower()


def test_invoice_pdf_defaults_leave_unset_seller_lines_out(monkeypatch):
    text, _ = _render_pdf_text(monkeypatch, dict(INVOICE))
    assert "Clockyn" in text
    assert "CIN:" not in text and "GSTIN:" not in text
    assert ": N/A" in text                                      # place of supply placeholder when unset


def test_invoice_pdf_text_mark_replaces_missing_logo(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Zeta Clock")
    text, _ = _render_pdf_text(monkeypatch, dict(INVOICE))
    assert "<b>ZC</b>" in text            # no brand_logo.png in static/: initials are printed instead


def test_invoice_pdf_prints_customer_gstin_under_bill_to_not_under_seller(monkeypatch, seller_env):
    data = dict(INVOICE, company_gstin="29CUSTX1234F1Z5")
    text, _ = _render_pdf_text(monkeypatch, data)
    assert text.count("GSTIN:") == 2
    assert text.index("GSTIN: 19ABCDE1234F1Z5") < text.index("GSTIN: 29CUSTX1234F1Z5")


def test_invoice_pdf_escapes_markup_in_env_values(monkeypatch):
    monkeypatch.setenv("SELLER_NAME", "A & B <Co>")
    text, _ = _render_pdf_text(monkeypatch, dict(INVOICE))
    assert "A &amp; B &lt;Co&gt;" in text


class _FakeSes:
    def __init__(self):
        self.calls = []

    def send_raw_email(self, **kw):
        self.calls.append(kw)

    def send_email(self, **kw):
        self.calls.append(kw)


def test_invoice_email_uses_brand_env(monkeypatch, seller_env):
    from utils import email as em
    ses = _FakeSes()
    monkeypatch.setenv("EMAIL_USER", "sender@zeta.example")
    monkeypatch.setattr(em.boto3, "client", lambda *a, **k: ses)
    monkeypatch.setattr(em, "_generate_invoice_pdf", lambda data: b"%PDF-fake")
    em._send_email_async("cust@example.test", dict(INVOICE))
    assert len(ses.calls) == 1
    msg = email_lib.message_from_string(ses.calls[0]["RawMessage"]["Data"])
    html = ""
    pdf_name = None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True).decode("utf-8")
        if part.get_content_type() == "application/pdf":
            pdf_name = part.get_filename()
    assert "<title>Invoice – Zeta Clock</title>" in html
    assert "Zeta Labs Pvt Ltd" in html
    assert "CIN: U00000WB2020PTC000000" in html and "7 Park Street" in html
    assert 'href="mailto:help@zeta.example"' in html
    assert 'href="https://www.zeta.example"' in html and ">www.zeta.example<" in html
    assert "West Bengal (19)" in html
    assert pdf_name == "ZetaClock_Invoice.pdf"
    assert _P not in html.lower() and _V not in html.lower()


def test_invoice_email_footer_without_contacts_names_the_product(monkeypatch):
    from utils import email as em
    ses = _FakeSes()
    monkeypatch.setenv("EMAIL_USER", "sender@x.example")
    monkeypatch.setattr(em.boto3, "client", lambda *a, **k: ses)
    monkeypatch.setattr(em, "_generate_invoice_pdf", lambda data: b"%PDF-fake")
    em._send_email_async("cust@example.test", dict(INVOICE))
    msg = email_lib.message_from_string(ses.calls[0]["RawMessage"]["Data"])
    html = next(p.get_payload(decode=True).decode("utf-8") for p in msg.walk()
                if p.get_content_type() == "text/html")
    assert "mailto:" not in html
    assert "Clockyn" in html


class _BulkCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _BulkConn:
    def __init__(self, rows):
        self.cur = _BulkCursor(rows)

    def cursor(self, cursor_factory=None):
        return self.cur


def test_bulk_download_names_and_item_names_follow_app_name(monkeypatch):
    from utils import email as em
    monkeypatch.setenv("APP_NAME", "Zeta Clock")
    row = {"invoice_number": "INV-1", "plan_name": "Core", "staff_count": 2, "base_amount": 100, "cgst": 9,
           "sgst": 9, "total_amount": 118, "payment_id": "pay_1", "order_id": "order_1",
           "payment_date": datetime(2026, 1, 5), "valid_till": datetime(2026, 2, 5), "coupon_code": None,
           "customer_name": "C", "customer_email": "c@x.test", "customer_mobile": "9", "org_name": "O",
           "org_gst": "", "org_address": "A", "state": "S", "district": "D", "pincode": "1"}
    seen = []
    monkeypatch.setattr(em, "get_db_connection", lambda: _BulkConn([row]))
    monkeypatch.setattr(em, "return_connection", lambda c: None)
    monkeypatch.setattr(em, "_generate_invoice_pdf", lambda data: seen.append(data) or b"%PDF")
    resp = asyncio.run(em.bulk_download_invoices("2026-01-01", "2026-01-31"))
    assert resp.headers["content-disposition"] == 'attachment; filename="zetaclock_invoices_2026-01-01_to_2026-01-31.zip"'
    names = zipfile.ZipFile(io.BytesIO(resp.body)).namelist()
    assert "INV-1.pdf" in names
    assert "zetaclock_invoices_summary_2026-01-01_to_2026-01-31.xlsx" in names
    assert seen[0]["items"][0]["name"] == "Core - Zeta Clock Subscription"


def test_otp_email_subject_and_body_use_brand_env(monkeypatch, seller_env):
    from core_functions import auth_function as af
    ses = _FakeSes()
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: ses)
    monkeypatch.setitem(af.email_config, "EMAIL_USER", "sender@zeta.example")
    monkeypatch.setitem(af.email_config, "AWS_SES_REGION", "ap-south-1")
    assert asyncio.run(af._send_email_otp_async("a@b.test", "123456", "login")) is True
    msg = ses.calls[0]["Message"]
    assert msg["Subject"]["Data"] == "Your Login OTP - Zeta Clock"
    body = msg["Body"]["Html"]["Data"]
    assert "Zeta Clock OTP is: <strong>123456</strong>" in body
    assert "help@zeta.example" in body


def test_otp_email_without_support_address_has_no_help_line(monkeypatch):
    from core_functions import auth_function as af
    ses = _FakeSes()
    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: ses)
    monkeypatch.setitem(af.email_config, "EMAIL_USER", "sender@x.example")
    monkeypatch.setitem(af.email_config, "AWS_SES_REGION", "ap-south-1")
    asyncio.run(af._send_email_otp_async("a@b.test", "123456", "registration"))
    body = ses.calls[0]["Message"]["Body"]["Html"]["Data"]
    assert "Need help" not in body
    assert ses.calls[0]["Message"]["Subject"]["Data"] == "Your Registration OTP - Clockyn"


class _SugCursor:
    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return [("sales@x.test",)]

    def close(self):
        pass


class _SugConn:
    def cursor(self):
        return _SugCursor()


def test_suggestion_email_heading_uses_app_name(monkeypatch):
    from core_functions import suggest_feature_functions as sf
    ses = _FakeSes()
    monkeypatch.setenv("APP_NAME", "Zeta Clock")
    monkeypatch.setenv("EMAIL_USER", "sender@x.example")
    monkeypatch.setattr(sf, "get_db_connection", lambda: _SugConn())
    monkeypatch.setattr(sf, "return_connection", lambda c: None)
    monkeypatch.setattr(sf.boto3, "client", lambda *a, **k: ses)
    sf._send_suggestion_emails("Idea", "text", "org1")
    html = ses.calls[0]["Message"]["Body"]["Html"]["Data"]
    assert "New Feature Suggestion - Zeta Clock" in html


def _wa_payload(mobile="9876543210"):
    from core_functions import whatsapp_service as wa
    return wa, wa._build_employee_onboard_payload(mobile, "Asha", "Org")


def test_whatsapp_requires_template_env_and_skips_without_it(monkeypatch):
    from core_functions import whatsapp_service as wa
    monkeypatch.setenv("MSG91_WHATSAPP_AUTH_KEY", "key")
    monkeypatch.setenv("MSG91_WHATSAPP_NUMBER", "919000000000")
    monkeypatch.setenv("ANDROID_STORE_URL", "https://play.example/app")
    posts = []
    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: posts.append((a, k)))
    assert wa.send_employee_onboard_whatsapp("9876543210", "Asha", "Org") is False
    assert posts == []                                        # nothing sent, no default template name
    _, payload = _wa_payload()
    assert payload["payload"]["template"]["name"] == ""


def test_whatsapp_skips_when_no_link_is_configured(monkeypatch):
    from core_functions import whatsapp_service as wa
    monkeypatch.setenv("MSG91_WHATSAPP_AUTH_KEY", "key")
    monkeypatch.setenv("MSG91_WHATSAPP_NUMBER", "919000000000")
    monkeypatch.setenv("MSG91_WHATSAPP_TEMPLATE", "tmpl_1")
    posts = []
    monkeypatch.setattr(wa.requests, "post", lambda *a, **k: posts.append((a, k)))
    assert wa.send_employee_onboard_whatsapp("9876543210", "Asha", "Org") is False
    assert posts == []


def test_whatsapp_sends_with_env_template_and_link(monkeypatch):
    from core_functions import whatsapp_service as wa

    class Resp:
        ok = True
        status_code = 200
        text = "{}"

        def json(self):
            return {"ok": True}

    monkeypatch.setenv("MSG91_WHATSAPP_AUTH_KEY", "key")
    monkeypatch.setenv("MSG91_WHATSAPP_NUMBER", "+91 90000 00000")
    monkeypatch.setenv("MSG91_WHATSAPP_TEMPLATE", "tmpl_1")
    monkeypatch.setenv("ANDROID_STORE_URL", "https://play.example/app")
    posts = []
    monkeypatch.setattr(wa.requests, "post", lambda url, json=None, headers=None, timeout=None:
                        posts.append(json) or Resp())
    assert wa.send_employee_onboard_whatsapp("9876543210", "Asha", "Org") is True
    tpl = posts[0]["payload"]["template"]
    assert tpl["name"] == "tmpl_1"
    comps = tpl["to_and_components"][0]["components"]
    assert comps["body_var_3"]["value"] == "https://play.example/app"
    assert comps["body_var_1"]["value"] == "Asha" and comps["body_var_2"]["value"] == "Org"


def test_payment_invoice_text_comes_from_branding():
    """verify_payment_process builds its invoice item through branding (no DB here: check the source)."""
    src = (ROOT / "core_functions" / "gen_payment_functions.py").read_text(encoding="utf-8")
    assert "branding.invoice_item_name(plan_name)" in src
    assert "branding.seller_sac()" in src
    assert "branding.seller_place_of_supply()" in src


def test_app_py_title_and_root_message_use_branding():
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "title=branding.app_name()" in src
    assert "Welcome to {branding.app_name()} API" in src
    assert tree  # parses


# ---------------------------------------------------------------------------
# 4. CORS
# ---------------------------------------------------------------------------
def test_cors_default_is_wildcard(monkeypatch):
    assert branding.cors_allowed_origins() is None
    assert branding.cors_allow_origin("https://anything.test") == "*"
    assert branding.cors_allow_origin(None) == "*"
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://a.test, *")
    assert branding.cors_allowed_origins() is None            # a wildcard entry keeps today's behaviour


def test_cors_list_allows_only_listed_origins_and_null(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", " https://app.test/ ,https://admin.test,, ")
    assert branding.cors_allowed_origins() == ["https://app.test", "https://admin.test"]
    assert branding.cors_allow_origin("https://app.test") == "https://app.test"
    assert branding.cors_allow_origin("https://admin.test") == "https://admin.test"
    assert branding.cors_allow_origin("https://evil.test") is None
    assert branding.cors_allow_origin("null") == "null"       # file:// origin handling is unchanged
    assert branding.cors_allow_origin(None) is None           # no Origin header: nothing to answer


def _cors_client():
    """Mount the exact middleware class defined in app.py on a tiny app (app.py itself needs the ML stack)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PermissiveCORSMiddleware")
    ns = {"BaseHTTPMiddleware": BaseHTTPMiddleware, "StarletteRequest": StarletteRequest,
          "StarletteResponse": StarletteResponse, "branding": branding}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "app.py", "exec"), ns)
    app = FastAPI()
    app.add_middleware(ns["PermissiveCORSMiddleware"])

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_middleware_default_answers_star_like_before():
    c = _cors_client()
    r = c.get("/ping", headers={"Origin": "https://x.test"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    assert "vary" not in r.headers
    p = c.options("/ping", headers={"Origin": "https://x.test"})
    assert p.status_code == 204
    assert p.headers["access-control-allow-origin"] == "*"
    assert p.headers["access-control-max-age"] == "86400"
    assert "Authorization" in p.headers["access-control-allow-headers"]
    r2 = c.get("/ping")                                       # no Origin (mobile app): still '*', as before
    assert r2.headers["access-control-allow-origin"] == "*"


def test_middleware_with_list_echoes_listed_origin_only(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.test")
    c = _cors_client()
    ok = c.get("/ping", headers={"Origin": "https://app.test"})
    assert ok.headers["access-control-allow-origin"] == "https://app.test"
    assert ok.headers["vary"] == "Origin"
    bad = c.get("/ping", headers={"Origin": "https://evil.test"})
    assert bad.status_code == 200                              # request still served, browser blocks the read
    assert "access-control-allow-origin" not in bad.headers
    nul = c.get("/ping", headers={"Origin": "null"})
    assert nul.headers["access-control-allow-origin"] == "null"
    pre = c.options("/ping", headers={"Origin": "https://app.test"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "https://app.test"
    pre_bad = c.options("/ping", headers={"Origin": "https://evil.test"})
    assert pre_bad.status_code == 204 and "access-control-allow-origin" not in pre_bad.headers
    none = c.get("/ping")
    assert "access-control-allow-origin" not in none.headers
