"""Render the welcome e-mail with sample data so you can look at it in a browser (nothing is sent).

    python scripts/preview_welcome_email.py              -> email_drafts/welcome_email_preview.html  and  .txt
    python scripts/preview_welcome_email.py --links-off  -> email_drafts/welcome_email_preview_nolinks.html  and  .txt
                                                            (what a deployment with no store links, website or support address sends)

Sample data: Asha, Sunrise Traders, trial ends 16 Dec 2026, 10 staff, 1 month. The sample links are set only for this render
(environment overrides inside this process); the real e-mail reads them from the deployment's settings (utils/branding.py).
email_drafts/welcome_email_draft.html is the approved design reference and is never touched by this script.
"""
import argparse
import base64
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE_ENV = {
    "ANDROID_STORE_URL": "https://example.com/android",
    "IOS_STORE_URL": "https://example.com/ios",
    "SUPPORT_EMAIL": "support@example.com",
    "SELLER_NAME": "Clockyn Technologies Pvt Ltd",
    "SELLER_ADDRESS": "12 Example Street, Chennai 600001",
}
BRANDING_VARS = ("APP_NAME", "ANDROID_STORE_URL", "APP_PLAY_STORE_LINK", "IOS_STORE_URL", "APP_APP_STORE_LINK", "WEBSITE_URL",
                 "SUPPORT_EMAIL", "SUPPORT_URL", "SELLER_NAME", "SELLER_ADDRESS", "SELLER_EMAIL")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--links-off", action="store_true", help="render with no store links, website or support address configured")
    ap.add_argument("--out", default=str(ROOT / "email_drafts"), help="folder for the preview files")
    args = ap.parse_args(argv)

    for name in BRANDING_VARS:
        os.environ.pop(name, None)
    if not args.links_off:
        os.environ.update(SAMPLE_ENV)

    from utils.welcome_email_template import render_welcome_email
    mail = render_welcome_email("Asha", "Sunrise Traders", date(2026, 12, 16), 10, 1)
    html = mail.html
    if mail.logo_bytes:
        # the real e-mail attaches the logo inline (cid:); a browser cannot resolve that, so the PREVIEW FILE ONLY embeds it as a data: URI
        html = html.replace(f"cid:{mail.logo_cid}", "data:image/png;base64," + base64.b64encode(mail.logo_bytes).decode("ascii"))
    suffix = "_nolinks" if args.links_off else ""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"welcome_email_preview{suffix}.html").write_text(html, encoding="utf-8", newline="\n")
    (out / f"welcome_email_preview{suffix}.txt").write_text(f"Subject: {mail.subject}\n\n{mail.text}", encoding="utf-8", newline="\n")
    print(f"subject: {mail.subject}")
    print(f"wrote {out / ('welcome_email_preview' + suffix + '.html')} ({len(mail.html.encode('utf-8')) / 1024:.1f} KB without the preview-only logo data) and .txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
