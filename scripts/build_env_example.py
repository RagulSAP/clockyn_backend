"""Write `.env.example`: every environment variable the backend reads, grouped by how much you need it, with the README description above each.

The names come from the same code scan as tests/test_env_documented.py (so a variable can not be forgotten), the descriptions from the README
"Environment Variables" table, the example values from the table below (only values the README states as defaults, plus placeholders).

    python scripts/build_env_example.py            rewrite .env.example
    python scripts/build_env_example.py --check    exit 1 when .env.example is out of date (used by the tests)

Copy the result to `.env`, replace every <placeholder>, and keep `.env` out of git (it is git-ignored). `.env.example` itself is safe to commit:
it holds no secret.
"""
import argparse
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_env_documented as T  # noqa: E402  (the same scanner and README parser the tests use)

OUT = ROOT / ".env.example"

# (section title, note, [(NAME, example value, active)]). active = written as a real line (a value you must set); otherwise commented out
# and shown with its default / a typical value. Values that look like a secret are always placeholders.
SECTIONS = [
    ("1. REQUIRED TO START", "The service cannot work without these.", [
        ("DB_HOST", "<database host>", True), ("DB_PORT", "5432", True), ("DB_NAME", "<database name>", True),
        ("DB_USER", "<database user>", True), ("DB_PASSWORD", "<database password>", True), ("DB_SCHEMA", "clockyn_dev", True),
        ("AWS_ACCESS_KEY_ID", "<aws access key id>", True), ("AWS_SECRET_ACCESS_KEY", "<aws secret access key>", True),
        ("AWS_S3_REGION", "ap-south-1", True), ("S3_BUCKET_NAME", "<s3 bucket for photos, videos and chat files>", True),
    ]),
    ("2. SIGN-IN AND E-MAIL (REQUIRED for OTP, invoices and the welcome e-mail)", "", [
        ("USER_AUTH", "otp", False), ("AWS_SES_REGION", "ap-south-1", True), ("EMAIL_USER", "<ses-verified sender address>", True),
        ("MSG91_AUTH_KEY", "<msg91 auth key>", False), ("MSG91_TEMPLATE_ID", "<msg91 sms template id>", False),
        ("MSG91_SENDER_ID", "<msg91 sender id>", False),
    ]),
    ("3. SECURITY (REQUIRED IN PRODUCTION)", "Recommended production values are written out; leave REQUIRE_AUTH off only on a local machine.", [
        ("AUTH_TOKEN_SECRET", "<at least 32 random characters>", True), ("REQUIRE_AUTH", "1", True),
        ("OPERATOR_ENFORCE", "1", True), ("OPERATOR_LEGACY_ADMIN_ID", "0", True),
        ("OPERATOR_API_KEY", "<at least 24 random characters, for scripts and break-glass access>", False),
        ("CORS_ALLOWED_ORIGINS", "https://<console host>", True), ("TRUST_PROXY", "1", False),
        ("RAZORPAY_WEBHOOK_SECRET", "<razorpay webhook secret>", True),
    ]),
    ("4. BRAND, INVOICES AND E-MAIL CONTENT", "Unset values are simply left out of invoices and e-mails; nothing is invented.", [
        ("APP_NAME", "Clockyn", False), ("SUPPORT_EMAIL", "<support address>", False), ("SUPPORT_URL", "<support page url>", False),
        ("WEBSITE_URL", "<website url>", False), ("ANDROID_STORE_URL", "<play store listing url>", False),
        ("IOS_STORE_URL", "<app store listing url>", False),
        ("SELLER_NAME", "<legal entity name>", False), ("SELLER_ADDRESS", "<line 1>;<line 2>", False),
        ("SELLER_GSTIN", "<gstin>", False), ("SELLER_CIN", "<cin>", False), ("SELLER_EMAIL", "<billing e-mail>", False),
        ("SELLER_PHONE", "<phone>", False), ("SELLER_SAC", "998314", False), ("SELLER_PLACE_OF_SUPPLY", "<state (code)>", False),
        ("BRAND_LOGO_FILE", "brand_logo.png", False), ("EMAIL_LOGO_FILE", "email_logo.png", False),
        ("EMAIL_LOGO_URL", "", False), ("WELCOME_EMAIL_ENABLED", "0", False),
    ]),
    ("5. PUSH, WHATSAPP, FACE RECOGNITION AND ADS", "", [
        ("FCM_PROJECT_ID", "<firebase project id>", False), ("FCM_CLIENT_EMAIL", "<service account e-mail>", False),
        ("FCM_PRIVATE_KEY", "<private key, keep the \\n escapes>", False), ("FCM_PRIVATE_KEY_ID", "<key id>", False),
        ("FCM_CLIENT_ID", "<client id>", False), ("FCM_ANDROID_CHANNEL_ID", "attendance_notifications", False),
        ("MSG91_WHATSAPP_AUTH_KEY", "<msg91 whatsapp auth key>", False), ("MSG91_WHATSAPP_NUMBER", "<whatsapp number>", False),
        ("MSG91_WHATSAPP_TEMPLATE", "<approved template name>", False),
        ("FACE_BACKEND", "rekognition", False), ("REKOGNITION_REGION", "ap-south-1", False),
        ("REKOGNITION_COLLECTION_ID", "clockyn-faces", False), ("REKOGNITION_THRESHOLD", "90.0", False),
        ("META_APP_ID", "<meta app id>", False), ("META_CLIENT_TOKEN", "<meta client token>", False),
        ("META_ACCESS_TOKEN", "<meta access token>", False), ("META_PIXEL_ID", "<meta pixel id>", False),
    ]),
]
# README defaults for the long tail (all optional). Anything not listed here is written as an empty commented line.
DEFAULTS = {
    "RUN_MIGRATIONS": "0", "OTP_BYPASS": "0", "OTP_MAX_PER_HOUR": "5", "OTP_MIN_GAP_SECONDS": "30",
    "AUTH_TOKEN_TTL_DAYS": "90", "AUTH_EPOCH_CACHE_SECONDS": "30", "OPERATOR_TOKEN_TTL_HOURS": "12",
    "UPLOAD_MAX_MB": "10", "CHAT_UPLOAD_MAX_MB": "25", "SIGNED_URL_TTL_SECONDS": "900",
    "RATE_LIMIT_ENABLED": "1", "TRUSTED_PROXY_HOPS": "1", "DEFAULT_ODOMETER_RATE": "3", "BILLING_ENFORCE_ORG_OWNER": "1",
    "CRM_CARRY_FORWARD_UNASSIGN": "0", "ENFORCE_SINGLE_DEVICE": "0", "REQUESTS_ENFORCE_ACTOR": "0", "ENFORCE_MANAGER_SCOPE": "0",
    "ACCOUNT_DELETE_ANONYMISE": "0", "APP_ENV": "production", "FACE_MOCK_ALWAYS_MATCH": "0", "REKOGNITION_AUTO_CREATE": "0",
    "REKOGNITION_MAX_RETRIES": "3", "FACE_MAX_WIDTH": "640", "FACE_DARK_THRESHOLD": "45", "FACE_BLUR_THRESHOLD": "40",
    "FACE_MIN_SIDE": "200", "FACE_MAX_CONCURRENCY": "8", "FACE_ATTEMPT_LOG": "1", "FACE_ANTISPOOF": "0",
}
SECRETISH = re.compile(r"SECRET|PASSWORD|PRIVATE_KEY|AUTH_KEY|API_KEY|ACCESS_KEY|TOKEN")


def readme_descriptions() -> dict:
    """name -> cleaned description of the README row that documents it (first sentences, markdown removed)."""
    out = {}
    for line in T.readme_env_section().splitlines():
        m = re.match(r"^\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if not m or m.group(1) in ("Variable", "---"):
            continue
        names = re.findall(r"`([A-Z][A-Z0-9_]*)`", m.group(1))
        desc = re.sub(r"`|\*\*", "", m.group(2))
        desc = re.sub(r"\s+", " ", desc).strip()
        sentences = re.split(r"(?<=[.;])\s+", desc)
        short, total = [], 0
        for sentence in sentences:
            if total + len(sentence) > 330 and short:
                break
            short.append(sentence)
            total += len(sentence)
        for n in names:
            out[n] = " ".join(short)
    return out


def render() -> str:
    wanted, _ = T._wanted()
    desc = readme_descriptions()
    placed = {n for _, _, items in SECTIONS for n, _, _ in items}
    # a name the sections do not mention yet goes to the last section, so a newly added variable can never be missing
    rest = sorted(n for n in wanted if n not in placed)
    lines = [
        "# Clockyn backend environment: copy to `.env`, replace every <placeholder>, keep `.env` out of git.",
        "# Generated by scripts/build_env_example.py from the README table and a scan of the code (do not edit by hand:",
        "# edit the README row or the SECTIONS table in that script, then run it again; a test fails when this file is out of date).",
        "# A line starting with # is optional: the value shown is the default or a typical value. Razorpay keys are NOT here: they live in the",
        "# gen_razorpay_key database table. Plan prices and coupons are data too (gen_plans, gen_coupons).",
        "",
    ]
    sections = list(SECTIONS) + [("6. TUNING AND FEATURE FLAGS (ALL OPTIONAL)", "Defaults are the README values; change only with a reason.", [])]
    for title, note, items in sections:
        lines += ["# " + "=" * 108, "# " + title]
        if note:
            lines.append("# " + note)
        lines += ["# " + "=" * 108, ""]
        entries = list(items) + ([(n, DEFAULTS.get(n, ""), False) for n in rest] if title.startswith("6.") else [])
        last = None
        for name, value, active in entries:
            if name not in wanted:
                continue                                       # a variable the code no longer reads is not listed
            text = desc.get(name, "")
            if text != last:                                    # variables of one README row share its description
                if last is not None:
                    lines.append("")
                lines += ["# " + w for w in textwrap.wrap(text, 108)] if text else []
                last = text
            if SECRETISH.search(name) and value and "<" not in value:
                value = ""                                      # never a real-looking value for a secret
            lines.append(("" if active else "# ") + f"{name}={value}")
        lines.append("")
    lines += ["# Per-endpoint rate limits (count/window_seconds, or off); the rules and defaults are in the README (RATE_LIMIT_<RULE>).",
              "# RATE_LIMIT_AUTH_LOGIN=30/60", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="only verify that .env.example is up to date")
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8").replace("\r\n", "\n") if OUT.exists() else ""
        if current != text:
            print(".env.example is out of date: run  python scripts/build_env_example.py", file=sys.stderr)
            return 1
        print(".env.example is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT.name} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
