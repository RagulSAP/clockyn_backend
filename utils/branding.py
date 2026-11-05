"""Single source for every brand, contact and seller-identity string the backend emits.

Everything is read from the environment on each call (so a changed value or a test monkeypatch is picked up
immediately). Defaults read the product name only; contact and legal-entity values default to empty and the
templates simply leave an empty line out, so nothing is invented. Set the variables in the deployment
environment (see the Environment Variables table in README.md).

    APP_NAME                 product name                                   (default Clockyn)
    SUPPORT_EMAIL            support address shown in e-mail footers        (default empty)
    SUPPORT_URL              support page link                              (default empty)
    WEBSITE_URL              marketing site link                            (default empty)
    SELLER_NAME              legal entity on invoices                       (default = APP_NAME)
    SELLER_ADDRESS           registered office, '\\n' or ';' separate lines  (default empty)
    SELLER_GSTIN / SELLER_CIN / SELLER_EMAIL / SELLER_PHONE                 (default empty)
    SELLER_SAC               SAC code on invoice items                      (default 998314)
    SELLER_PLACE_OF_SUPPLY   text shown as Place of Supply                  (default empty)
    ANDROID_STORE_URL / IOS_STORE_URL   store listings (also used by the version check)
    BRAND_LOGO_FILE          logo file name inside static/                  (default brand_logo.png)
    BRAND_SIGNATURE_FILE     signature image file name inside static/       (default signature.png)
    MSG91_WHATSAPP_TEMPLATE  approved WhatsApp template name (no default)
    CORS_ALLOWED_ORIGINS     comma list of allowed web origins              (default * = any origin)
"""
import os
import re
from typing import List, Optional

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

DEFAULT_APP_NAME = "Clockyn"
DEFAULT_SAC = "998314"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


# ── identity ────────────────────────────────────────────────────────────────
def app_name() -> str:
    return _env("APP_NAME", DEFAULT_APP_NAME)


def file_slug() -> str:
    """Product name reduced to letters and digits, for download file names."""
    slug = re.sub(r"[^A-Za-z0-9]+", "", app_name())
    return slug or DEFAULT_APP_NAME


def initials() -> str:
    """Text mark used in place of a missing logo file."""
    letters = [w[0] for w in re.findall(r"[A-Za-z0-9]+", app_name())]
    return ("".join(letters[:2]) or app_name()[:1] or "?").upper()


def support_email() -> str:
    return _env("SUPPORT_EMAIL")


def support_url() -> str:
    return _env("SUPPORT_URL")


def website_url() -> str:
    return _env("WEBSITE_URL")


def android_store_url() -> str:
    # APP_PLAY_STORE_LINK is the name earlier deployments used; kept as a fallback.
    return _env("ANDROID_STORE_URL") or _env("APP_PLAY_STORE_LINK")


def ios_store_url() -> str:
    return _env("IOS_STORE_URL") or _env("APP_APP_STORE_LINK")


def app_download_link() -> str:
    """Link placed in the staff invitation: Android listing, else iOS listing, else the website."""
    return android_store_url() or ios_store_url() or website_url()


def whatsapp_template() -> str:
    """Approved MSG91 WhatsApp template name. Empty means invitations are not sent."""
    return _env("MSG91_WHATSAPP_TEMPLATE")


# ── seller (legal entity printed on invoices) ───────────────────────────────
def seller_name() -> str:
    return _env("SELLER_NAME") or app_name()


def seller_address_lines() -> List[str]:
    raw = _env("SELLER_ADDRESS")
    if not raw:
        return []
    raw = raw.replace("\\n", "\n").replace(";", "\n")
    return [part.strip() for part in raw.split("\n") if part.strip()]


def seller_gstin() -> str:
    return _env("SELLER_GSTIN")


def seller_cin() -> str:
    return _env("SELLER_CIN")


def seller_email() -> str:
    return _env("SELLER_EMAIL")


def seller_phone() -> str:
    return _env("SELLER_PHONE")


def seller_sac() -> str:
    return _env("SELLER_SAC", DEFAULT_SAC)


def seller_place_of_supply() -> str:
    return _env("SELLER_PLACE_OF_SUPPLY")


def seller_contact_email() -> str:
    """Address in invoice footers: SUPPORT_EMAIL, else SELLER_EMAIL."""
    return support_email() or seller_email()


# ── invoice wording and file names ──────────────────────────────────────────
def invoice_item_name(plan_name: str) -> str:
    return f"{plan_name} - {app_name()} Subscription"


def invoice_pdf_filename() -> str:
    return f"{file_slug()}_Invoice.pdf"


def invoice_zip_filename(from_date, to_date) -> str:
    return f"{file_slug().lower()}_invoices_{from_date}_to_{to_date}.zip"


def invoice_summary_filename(from_date, to_date) -> str:
    return f"{file_slug().lower()}_invoices_summary_{from_date}_to_{to_date}.xlsx"


# ── static artwork ──────────────────────────────────────────────────────────
def _static_file(env_name: str, default_file: str) -> str:
    name = os.path.basename(_env(env_name, default_file))  # never leave static/
    return os.path.join(_STATIC_DIR, name)


def logo_path() -> str:
    return _static_file("BRAND_LOGO_FILE", "brand_logo.png")


def signature_path() -> str:
    return _static_file("BRAND_SIGNATURE_FILE", "signature.png")


# ── CORS ────────────────────────────────────────────────────────────────────
def cors_allowed_origins() -> Optional[List[str]]:
    """None = any origin (the default, today's behaviour). Otherwise the configured origins."""
    raw = _env("CORS_ALLOWED_ORIGINS")
    if not raw:
        return None
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if not origins or "*" in origins:
        return None
    return origins


def cors_allow_origin(origin: Optional[str]) -> Optional[str]:
    """Value for Access-Control-Allow-Origin, or None when the origin must not be answered.

    Wildcard config answers '*'. With a list, a listed origin is echoed back, and the file:// 'null'
    origin keeps being answered as before; any other origin gets no CORS header (the browser blocks it).
    Requests without an Origin header (the mobile app) are unaffected by CORS either way.
    """
    allowed = cors_allowed_origins()
    if allowed is None:
        return "*"
    if not origin:
        return None
    if origin == "null":
        return "null"
    return origin if origin.rstrip("/") in allowed else None
