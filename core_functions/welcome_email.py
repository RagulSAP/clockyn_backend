"""One-time welcome e-mail to a company admin, sent the first time that admin's Home screen loads after registering.

Rules (approved by the owner, see email_drafts/welcome_email_proposal.md):

* Nothing happens unless ``WELCOME_EMAIL_ENABLED`` is truthy (1 / true / yes / on). The variable is read on every call and, when it is off,
  not even a database query is made.
* Only a company ADMIN (``users.role_id = 1``) with a plausible e-mail address is ever welcomed. Managers and staff never are.
* Sent exactly once: ``claim_send`` is ONE atomic ``UPDATE ... RETURNING`` on the admin's row (migration 042), so two phones or two parallel
  Home loads can never both win. A failed send leaves the attempt counted; it is retried on a later Home load at most 3 times in total,
  at least 1 hour apart (``MAX_ATTEMPTS`` / the interval in ``CLAIM_SQL``).
* Users who existed when migration 042 ran were marked as welcomed by the migration, so existing customers get nothing.
* Never in the request path: ``schedule_welcome_email`` returns at once and does the claim, the rendering and the SES call in a worker thread;
  nothing it does can raise into, slow down or change the Home response. Only the user id and the result are logged, never the e-mail body.
* The page itself is built by ``utils/welcome_email_template.py``; links, support address and company details come from ``utils/branding``.
  Sent through Amazon SES like the OTP and invoice e-mails (``EMAIL_USER`` sender, ``AWS_SES_REGION``) as a raw MIME message so the
  logo can travel inline (Content-ID) instead of being fetched from a web address.
"""
import asyncio
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

from core_functions import pricing
from core_functions.db_function import get_db_connection, return_connection
from utils import branding

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
TRIAL_MONTHS = 1                 # registration gives a 1-month trial (core_functions/auth_function.py: relativedelta(months=1))
SKIP_TTL_SECONDS = 600           # an admin already looked at in the last 10 minutes is not looked at again by this process
_SKIP_CACHE_LIMIT = 5000
_TRUTHY = {"1", "true", "yes", "on"}

# ONE atomic statement. It only succeeds for an ADMIN who has not been welcomed, has tried fewer than MAX_ATTEMPTS times, did not try in the
# last hour and has a plausible e-mail address (so a blank or broken address never burns an attempt). Two parallel callers: the second one
# finds the first one's new last_attempt_at (row lock + READ COMMITTED re-check) and gets no row.
CLAIM_SQL = """
    UPDATE users u
    SET welcome_email_attempts = u.welcome_email_attempts + 1,
        welcome_email_last_attempt_at = NOW()
    WHERE u.user_id = %s
      AND u.role_id = 1
      AND u.welcome_email_sent_at IS NULL
      AND u.welcome_email_attempts < %s
      AND (u.welcome_email_last_attempt_at IS NULL OR u.welcome_email_last_attempt_at < NOW() - INTERVAL '1 hour')
      AND u.email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
    RETURNING u.user_id, u.name, u.email, u.org_id
"""
MARK_SENT_SQL = "UPDATE users SET welcome_email_sent_at = NOW() WHERE user_id = %s AND welcome_email_sent_at IS NULL"

_recent: Dict[str, float] = {}


def enabled() -> bool:
    return os.getenv("WELCOME_EMAIL_ENABLED", "").strip().lower() in _TRUTHY


# ── pure helpers ────────────────────────────────────────────────────────────────────────────────────────────────────
def first_name(full_name: Optional[str]) -> str:
    """First word of the admin's name; 'there' when there is none ("Welcome to Clockyn, there!")."""
    words = str(full_name or "").split()
    return words[0] if words else "there"


def trial_months(start: Optional[date], end: Optional[date]) -> int:
    """Whole months between the trial's start and end date (the registration sets start + 1 month); TRIAL_MONTHS when unknown."""
    try:
        months = (end.year - start.year) * 12 + (end.month - start.month)
    except AttributeError:
        return TRIAL_MONTHS
    return months if months >= 1 else TRIAL_MONTHS


def subject_for(name: str, months: int) -> str:
    """The approved subject: "Welcome to Clockyn, Asha! Your 1-month free trial has started"."""
    return f"Welcome to {branding.app_name()}, {first_name(name)}! Your {months}-month free trial has started"


def should_welcome(row: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """What CLAIM_SQL decides, as plain Python (for tests and documentation): an admin with a plausible e-mail that has not been
    welcomed, has tried fewer than MAX_ATTEMPTS times and (when it tried before) tried more than an hour ago."""
    if not row or row.get("role_id") != 1 or row.get("welcome_email_sent_at") is not None:
        return False
    if int(row.get("welcome_email_attempts") or 0) >= MAX_ATTEMPTS:
        return False
    last = row.get("welcome_email_last_attempt_at")
    if last is not None:
        now = now or datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < 3600:
            return False
    email = str(row.get("email") or "")
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not any(c.isspace() for c in email) and email.count("@") == 1


def _remember(user_id: str) -> bool:
    """True when this process looked at ``user_id`` within SKIP_TTL_SECONDS (so the Home call can skip the database); records the visit."""
    now = time.monotonic()
    if len(_recent) >= _SKIP_CACHE_LIMIT:
        for key in [k for k, t in _recent.items() if now - t > SKIP_TTL_SECONDS]:
            _recent.pop(key, None)
        if len(_recent) >= _SKIP_CACHE_LIMIT:
            _recent.clear()
    seen = _recent.get(user_id)
    if seen is not None and now - seen < SKIP_TTL_SECONDS:
        return True
    _recent[user_id] = now
    return False


# ── database ────────────────────────────────────────────────────────────────────────────────────────────────────────
def claim_send(cursor, user_id: str) -> Optional[Dict[str, Any]]:
    """The admin's {user_id, name, email, org_id} when THIS call won the right to send the welcome e-mail, else None. The caller commits."""
    cursor.execute(CLAIM_SQL, (user_id, MAX_ATTEMPTS))
    row = cursor.fetchone()
    return dict(row) if row else None


def gather(cursor, claimed: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the template needs: first name, organisation name, trial end date and length, and the trial staff limit."""
    org_name = ""
    start = end = None
    org_id = claimed.get("org_id")
    if org_id:
        cursor.execute("SELECT org_name FROM org_info WHERE org_id = %s", (org_id,))
        org = cursor.fetchone()
        org_name = (org or {}).get("org_name") or ""
        cursor.execute("SELECT start_date, end_date FROM free_trial WHERE org_id = %s", (org_id,))
        trial = cursor.fetchone()
        if trial:
            start, end = trial.get("start_date"), trial.get("end_date")
    return {
        "first_name": first_name(claimed.get("name")),
        "org_name": org_name,
        "trial_end": end,
        "staff_limit": pricing.TRIAL_STAFF_LIMIT,
        "trial_months": trial_months(start, end),
    }


# ── sending ─────────────────────────────────────────────────────────────────────────────────────────────────────────
_CID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
LOGO_FILENAME = "clockyn-logo.png"


def _header_value(value: str) -> str:
    """A header value on one line: CR / LF and other control characters become a space, so a name or setting can never add a header."""
    return _CONTROL_RE.sub(" ", str(value or "")).strip()


def build_mime(sender: str, to_email: str, subject: str, html: str, text: str, reply_to: Optional[str] = None,
               logo_cid: Optional[str] = None, logo_bytes: Optional[bytes] = None) -> MIMEMultipart:
    """The e-mail as MIME. One code path:
      * without a logo:  multipart/alternative ( text/plain, text/html )
      * with a logo:     multipart/related ( multipart/alternative ( text/plain, text/html ), image/png inline, Content-ID <logo_cid> )
    Text parts are UTF-8; the Subject is RFC 2047 encoded when it is not plain ASCII; every header value is forced onto one line."""
    if _CONTROL_RE.search(str(to_email or "")):
        raise ValueError("invalid recipient")
    if logo_bytes and not (logo_cid and _CID_RE.match(logo_cid)):
        raise ValueError("invalid Content-ID for the inline logo")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text, "plain", "utf-8"))
    alternative.attach(MIMEText(html, "html", "utf-8"))
    if logo_bytes:
        root = MIMEMultipart("related")
        root.attach(alternative)
        image = MIMEImage(logo_bytes, _subtype="png")
        image.add_header("Content-ID", f"<{logo_cid}>")
        image.add_header("Content-Disposition", "inline", filename=LOGO_FILENAME)
        root.attach(image)
    else:
        root = alternative
    root["Subject"] = Header(_header_value(subject), "utf-8")
    root["From"] = formataddr((_header_value(branding.app_name()), _header_value(sender)))
    root["To"] = _header_value(to_email)
    if reply_to:
        root["Reply-To"] = _header_value(reply_to)
    return root


def send_email(to_email: str, subject: str, html: str, text: str, logo_cid: Optional[str] = None,
               logo_bytes: Optional[bytes] = None) -> None:
    """Amazon SES (send_raw_email), like the invoice e-mail. From is "<app name> <EMAIL_USER>"; Reply-To is SUPPORT_EMAIL when it is set;
    the logo, when given, travels inside the message as an inline image. Raises on any problem (the caller counts a failed attempt)."""
    sender = (os.getenv("EMAIL_USER") or "").strip()
    if not sender:
        raise RuntimeError("EMAIL_USER is not configured")
    message = build_mime(sender, to_email, subject, html, text, reply_to=branding.support_email() or None,
                         logo_cid=logo_cid, logo_bytes=logo_bytes)
    import boto3
    ses = boto3.client("ses", region_name=os.getenv("AWS_SES_REGION", "ap-south-1"))
    ses.send_raw_email(Source=sender, Destinations=[to_email], RawMessage={"Data": message.as_bytes()})


def process(user_id: str) -> str:
    """Claim, render, send and record, synchronously. Returns 'skipped' (not eligible / nothing to do), 'sent' or 'failed'. Never raises."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        claimed = claim_send(cur, user_id)
        conn.commit()                                   # the claim is visible to every other request before anything slow happens
        if not claimed:
            return "skipped"
        data = gather(cur, claimed)
        conn.commit()
        from utils import welcome_email_template as tpl
        mail = tpl.render_welcome_email(
            first_name=data["first_name"], org_name=data["org_name"], trial_end=data["trial_end"],
            staff_limit=data["staff_limit"], trial_months=data["trial_months"],
        )
        subject = getattr(mail, "subject", None) or subject_for(claimed.get("name"), data["trial_months"])
        send_email(claimed["email"], subject, mail.html, mail.text,
                   logo_cid=getattr(mail, "logo_cid", None), logo_bytes=getattr(mail, "logo_bytes", None))
        cur.execute(MARK_SENT_SQL, (user_id,))
        conn.commit()
        logger.info("Welcome e-mail sent to user %s", user_id)
        return "sent"
    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        logger.error("Welcome e-mail for user %s failed (%s); the attempt is counted", user_id, type(exc).__name__)
        return "failed"
    finally:
        if conn:
            try:
                return_connection(conn)
            except Exception:
                pass


def _run_safely(user_id: str) -> None:
    try:
        process(user_id)
    except Exception:                                    # process() already catches everything; belt and braces for the worker thread
        logger.exception("Welcome e-mail worker for user %s crashed", user_id)


async def schedule_welcome_email(user_id: Optional[str]) -> None:
    """Called by the Home screen's first request. Returns immediately and never raises: the work runs in a worker thread.
    Off (the default): returns before any query. The same admin is looked at once per SKIP_TTL_SECONDS per process."""
    try:
        if not user_id or not enabled():
            return
        if _remember(str(user_id)):
            return
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run_safely, str(user_id))
    except Exception:
        logger.exception("Could not schedule the welcome e-mail for user %s", user_id)
