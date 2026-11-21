"""Find out why a welcome e-mail did not arrive. Run it where the backend runs (same environment / .env), from the backend folder:

    python scripts/check_welcome_email.py <id>
        READ-ONLY. <id> is what the app sends to /home/dashboard-status (the organisation id) or the admin's user id. Checks the settings,
        the database columns, the admin's row (and says which rule would stop the e-mail) and what Amazon SES says about the sender,
        the recipient and the account (sandbox or not). Nothing is sent and nothing is changed.
    python scripts/check_welcome_email.py <id> --send-test-to you@example.com
        also sends the real welcome e-mail (the admin's first name and company) to THAT address through SES. No database change.
    python scripts/check_welcome_email.py <id> --run
        runs exactly what the Home screen triggers (claim, send to the admin, mark as sent) in this process, with every log line on screen.
        It counts as an attempt; on success the admin is marked as welcomed.
    python scripts/check_welcome_email.py <id> --reset
        clears the three welcome_email_* columns of that ONE admin so the e-mail can be sent again.

Exit code 0 when no check failed. The output is for you: it shows the recipient's address masked (a***@domain) and never prints secrets.
"""
import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COLUMNS = ("welcome_email_sent_at", "welcome_email_attempts", "welcome_email_last_attempt_at")
ADMIN_SQL = """
    SELECT u.user_id, u.name, u.email, u.org_id, u.role_id, u.welcome_email_sent_at, u.welcome_email_attempts,
           (u.welcome_email_last_attempt_at IS NOT NULL AND u.welcome_email_last_attempt_at >= NOW() - INTERVAL '1 hour') AS tried_recently,
           u.welcome_email_last_attempt_at,
           (u.email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$') AS email_ok
    FROM users u
    WHERE u.user_id = %s OR u.user_id = (SELECT o.parent_id FROM org_info o WHERE o.org_id = %s)
    ORDER BY (u.role_id = 1) DESC
    LIMIT 1
"""


def mask(address) -> str:
    local, _, domain = str(address or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "(blank)"


class Report:
    def __init__(self, out):
        self.out, self.failures = out, 0

    def _say(self, tag, message):
        print(f"{tag} {message}", file=self.out)

    def ok(self, message):
        self._say("[ ok ]", message)

    def note(self, message):
        self._say("[note]", message)

    def bad(self, message):
        self.failures += 1
        self._say("[FAIL]", message)


# ── 1. settings ─────────────────────────────────────────────────────────────────────────────────────────────────────
def check_settings(r: Report, we) -> None:
    raw = os.getenv("WELCOME_EMAIL_ENABLED")
    if we.enabled():
        r.ok(f"WELCOME_EMAIL_ENABLED={raw!r}: the feature is on in THIS environment")
    else:
        r.bad(f"WELCOME_EMAIL_ENABLED={raw!r}: the feature is OFF (use 1 / true / yes / on). Set it where the backend runs and restart it")
    sender = (os.getenv("EMAIL_USER") or "").strip()
    (r.ok if sender else r.bad)(f"EMAIL_USER {'is set (' + mask(sender) + ')' if sender else 'is NOT set: there is no sender address for SES'}")
    r.note(f"AWS_SES_REGION={os.getenv('AWS_SES_REGION', 'ap-south-1')!r} (default ap-south-1); DB_SCHEMA={os.getenv('DB_SCHEMA')!r}")
    try:
        import boto3
        creds = boto3.Session().get_credentials()
        (r.ok if creds else r.bad)("AWS credentials were found" if creds else
                                   "no AWS credentials found (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, a profile or an instance role): SES cannot be called")
    except ImportError:
        r.bad("boto3 is not installed in this Python environment (pip install -r requirements.txt)")


# ── 2. database ─────────────────────────────────────────────────────────────────────────────────────────────────────
def check_database(r: Report, we, given_id: str):
    """Returns (connection, cursor, admin row or None). The caller closes the connection."""
    from psycopg2.extras import RealDictCursor
    try:
        conn = we.get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT current_schema() AS s")
        r.ok(f"connected to the database (schema {(cur.fetchone() or {}).get('s')})")
    except Exception as exc:
        r.bad(f"cannot connect to the database: {type(exc).__name__}: {exc}")
        return None, None, None
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'users' "
                "AND column_name = ANY(%s)", (list(COLUMNS),))
    have = {row["column_name"] for row in cur.fetchall()}
    missing = [c for c in COLUMNS if c not in have]
    if missing:
        r.bad(f"users is missing the column(s): {', '.join(missing)} (run migration 042 / the welcome-email block of clockyn_ddl_latest.sql)")
        return conn, cur, None
    r.ok("users has the three welcome_email_* columns")
    cur.execute(ADMIN_SQL, (given_id, given_id))
    row = cur.fetchone()
    if not row:
        r.bad(f"no user has id {given_id} and no organisation with that id has an owner: check the id (it must come from your database)")
        return conn, cur, None
    row = dict(row)
    r.ok(f"found the account: admin user {row['user_id']} (role_id {row['role_id']}, e-mail {mask(row['email'])}, organisation {row['org_id']})")
    r.note(f"welcome_email_sent_at={row['welcome_email_sent_at']}  attempts={row['welcome_email_attempts']}  "
           f"last_attempt_at={row['welcome_email_last_attempt_at']}")
    reason = we.first_failure(row)
    if reason:
        r.bad(f"the e-mail will NOT be sent for this admin: {reason}")
    else:
        r.ok("this admin is eligible: the next Home screen load (after the server's 10-minute memory) sends the e-mail")
    return conn, cur, row


# ── 3. Amazon SES ───────────────────────────────────────────────────────────────────────────────────────────────────
def check_ses(r: Report, recipient) -> None:
    sender = (os.getenv("EMAIL_USER") or "").strip()
    region = os.getenv("AWS_SES_REGION", "ap-south-1")
    try:
        import boto3
        ses = boto3.client("ses", region_name=region)
    except Exception as exc:
        r.bad(f"cannot create the SES client: {type(exc).__name__}: {exc}")
        return
    try:
        quota = ses.get_send_quota()
        limit = float(quota.get("Max24HourSend", 0))
        r.note(f"SES {region}: may send {limit:.0f} e-mails per 24 hours, {float(quota.get('SentLast24Hours', 0)):.0f} sent in the last 24 hours")
        sandbox = limit <= 200
        if sandbox:
            r.bad("this looks like the SES SANDBOX (200 per day): SES only delivers to recipient addresses that are verified in SES. "
                  "Verify the recipient or ask AWS for production access")
        else:
            r.ok("SES is out of the sandbox (the daily limit is above 200)")
    except Exception as exc:
        sandbox = None
        r.note(f"could not read the SES quota ({type(exc).__name__}); the IAM user may lack ses:GetSendQuota, which does not stop sending")
    identities = [x for x in (sender, sender.partition("@")[2], str(recipient or "")) if x]
    try:
        attrs = ses.get_identity_verification_attributes(Identities=identities).get("VerificationAttributes", {})
        status = {i: attrs.get(i, {}).get("VerificationStatus", "NotVerified") for i in identities}
        sender_ok = status.get(sender) == "Success" or status.get(sender.partition("@")[2]) == "Success"
        (r.ok if sender_ok else r.bad)(f"sender {mask(sender)} (or its domain) is {'verified' if sender_ok else 'NOT verified'} in SES region {region}: "
                                         f"{ {mask(i) if '@' in i else i: v for i, v in status.items()} }")
        if recipient:
            rec_ok = status.get(str(recipient)) == "Success" or status.get(str(recipient).partition("@")[2]) == "Success"
            if rec_ok:
                r.ok(f"recipient {mask(recipient)} is verified in SES")
            elif sandbox:
                r.bad(f"recipient {mask(recipient)} is NOT verified in SES and the account is in the sandbox: SES rejects it")
            else:
                r.note(f"recipient {mask(recipient)} is not a verified identity (fine once the account is out of the sandbox)")
    except Exception as exc:
        r.note(f"could not read the SES identities ({type(exc).__name__}); the IAM user may lack ses:GetIdentityVerificationAttributes")


# ── 4. the e-mail itself ────────────────────────────────────────────────────────────────────────────────────────────
def render(r: Report, we, cur, row):
    from utils import welcome_email_template as tpl
    claimed = {"name": (row or {}).get("name"), "org_id": (row or {}).get("org_id")}
    data = we.gather(cur, claimed) if cur is not None and row else {
        "first_name": "there", "org_name": "", "trial_end": None, "staff_limit": we.pricing.TRIAL_STAFF_LIMIT, "trial_months": we.TRIAL_MONTHS}
    try:
        mail = tpl.render_welcome_email(first_name=data["first_name"], org_name=data["org_name"], trial_end=data["trial_end"],
                                        staff_limit=data["staff_limit"], trial_months=data["trial_months"])
        r.ok(f"the e-mail renders: subject {mail.subject!r}, {len(mail.html.encode('utf-8')) / 1024:.0f} KB, logo inline: {bool(mail.logo_bytes)}")
        return mail
    except Exception as exc:
        r.bad(f"the e-mail template failed: {type(exc).__name__}: {exc}")
        return None


def send_test(r: Report, we, mail, address: str) -> None:
    try:
        we.send_email(address, mail.subject, mail.html, mail.text, logo_cid=getattr(mail, "logo_cid", None),
                      logo_bytes=getattr(mail, "logo_bytes", None))
        r.ok(f"SES accepted the test e-mail for {mask(address)}. If it is not in the inbox in a few minutes, look in spam")
    except Exception as exc:
        r.bad(f"SES refused the test e-mail: {type(exc).__name__}: {exc}")


def main(argv=None, out=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("id", help="the id the app sends to /home/dashboard-status (organisation id) or the admin's user id")
    ap.add_argument("--send-test-to", metavar="ADDRESS", help="also send the real welcome e-mail to this address (no database change)")
    ap.add_argument("--run", action="store_true", help="run the real flow for this id now (claims, sends to the admin, marks as sent)")
    ap.add_argument("--reset", action="store_true", help="clear the welcome_email_* columns of this one admin so it can be sent again")
    args = ap.parse_args(argv)
    out = out or sys.stdout
    for prefix in ("id=", "org_id=", "user_id="):                       # `id=<uuid>` pasted from a URL or a log is accepted too
        if args.id.lower().startswith(prefix):
            args.id = args.id[len(prefix):]
    args.id = args.id.strip().strip("'\"")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s", stream=out)   # before the import: no duplicate handler
    from core_functions import welcome_email as we
    r = Report(out)

    print("1. settings", file=out)
    check_settings(r, we)
    print("2. database", file=out)
    conn, cur, row = check_database(r, we, args.id)
    try:
        print("3. Amazon SES", file=out)
        check_ses(r, (row or {}).get("email"))
        print("4. the e-mail", file=out)
        mail = render(r, we, cur, row)
        if args.send_test_to:
            if mail is None:
                r.bad("not sending the test: the e-mail could not be built")
            else:
                send_test(r, we, mail, args.send_test_to)
        if args.reset and row:
            cur.execute("UPDATE users SET welcome_email_sent_at = NULL, welcome_email_attempts = 0, welcome_email_last_attempt_at = NULL "
                        "WHERE user_id = %s", (row["user_id"],))
            conn.commit()
            r.ok(f"cleared the welcome_email_* columns of admin {row['user_id']}")
    finally:
        if conn:
            try:
                we.return_connection(conn)
            except Exception:
                pass
    if args.run:
        print("5. running the real flow (the log lines below are what the server writes)", file=out)
        result = we.process(args.id)
        (r.ok if result == "sent" else r.bad)(f"result: {result}")
    print(f"\n{'ALL CHECKS PASSED' if not r.failures else str(r.failures) + ' CHECK(S) FAILED: fix the [FAIL] lines above, top to bottom'}", file=out)
    return 0 if not r.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
