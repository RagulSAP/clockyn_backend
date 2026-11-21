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
  nothing it does can raise into, slow down or change the Home response. Only the user id and the result are logged, never the e-mail body
  or the recipient's address.
* Every decision is logged at INFO with the prefix ``welcome-email:`` (``grep welcome-email`` in the server log): switch off, already looked
  at, queued, why an admin was skipped (not an admin / already sent / attempts used up / tried less than an hour ago / bad address /
  no such user), each step of a send and the reason a send failed.
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


def _ensure_log_output() -> None:
    """Make this module's INFO lines visible however the server was started. ``python app.py`` configures logging only in the parent
    process; the auto-reload worker (and a plain ``uvicorn app:app``) has no root handler, so INFO lines would be silently dropped
    (uvicorn's own access log keeps working because uvicorn sets up its own handlers). Only when nothing else would print them a
    handler is added to THIS logger, so there are never duplicate lines."""
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s:%(message)s"))
        logger.addHandler(handler)


_ensure_log_output()

MAX_ATTEMPTS = 3
TRIAL_MONTHS = 1                 # registration gives a 1-month trial (core_functions/auth_function.py: relativedelta(months=1))
SKIP_TTL_SECONDS = 600           # an admin already looked at in the last 10 minutes is not looked at again by this process
_SKIP_CACHE_LIMIT = 5000
_TRUTHY = {"1", "true", "yes", "on"}

# ONE atomic statement. It only succeeds for an ADMIN who has not been welcomed, has tried fewer than MAX_ATTEMPTS times, did not try in the
# last hour and has a plausible e-mail address (so a blank or broken address never burns an attempt). Two parallel callers: the second one
# finds the first one's new last_attempt_at (row lock + READ COMMITTED re-check) and gets no row.
# The id the Home screen sends is the ORGANISATION id for an admin (GET /home/dashboard-status?user_id=<org id>: the backend treats the value as
# the organisation for an admin), and an admin's own user id is a different uuid. So the id is matched as the admin's user id OR as the
# organisation whose owner (org_info.parent_id) is that admin; role_id = 1 still keeps managers and staff out.
CLAIM_SQL = """
    UPDATE users u
    SET welcome_email_attempts = u.welcome_email_attempts + 1,
        welcome_email_last_attempt_at = NOW()
    WHERE (u.user_id = %s OR u.user_id = (SELECT o.parent_id FROM org_info o WHERE o.org_id = %s))
      AND u.role_id = 1
      AND u.welcome_email_sent_at IS NULL
      AND u.welcome_email_attempts < %s
      AND (u.welcome_email_last_attempt_at IS NULL OR u.welcome_email_last_attempt_at < NOW() - INTERVAL '1 hour')
      AND u.email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$'
    RETURNING u.user_id, u.name, u.email, u.org_id
"""
MARK_SENT_SQL = "UPDATE users SET welcome_email_sent_at = NOW() WHERE user_id = %s AND welcome_email_sent_at IS NULL"

_recent: Dict[str, float] = {}
_warned_off = False            # the 'switch is off' line is written once per process, not on every Home load

# Why CLAIM_SQL returned no row: the same conditions, read back so the log can name the one that failed (diagnostics only).
DIAGNOSE_SQL = """
    SELECT u.user_id, u.role_id, u.welcome_email_sent_at, u.welcome_email_attempts,
           (u.welcome_email_last_attempt_at IS NOT NULL AND u.welcome_email_last_attempt_at >= NOW() - INTERVAL '1 hour') AS tried_recently,
           (u.email ~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$') AS email_ok
    FROM users u
    WHERE u.user_id = %s OR u.user_id = (SELECT o.parent_id FROM org_info o WHERE o.org_id = %s)
    ORDER BY (u.role_id = 1) DESC
    LIMIT 1
"""


def log_startup_state() -> None:
    """One line when the server starts: is the feature on and is the sender configured (values are never printed)."""
    if enabled():
        logger.info("welcome-email: ON at startup (sender EMAIL_USER configured: %s, AWS_SES_REGION=%s)",
                    bool((os.getenv("EMAIL_USER") or "").strip()), os.getenv("AWS_SES_REGION", "ap-south-1"))
    else:
        logger.info("welcome-email: OFF at startup (WELCOME_EMAIL_ENABLED=%r). Set WELCOME_EMAIL_ENABLED=1 in the backend's environment "
                    "and restart to send welcome e-mails.", os.getenv("WELCOME_EMAIL_ENABLED"))


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
def first_failure(row: Optional[Dict[str, Any]]) -> Optional[str]:
    """The first condition of CLAIM_SQL that a user row (from DIAGNOSE_SQL) fails, as a short sentence; None when the row passes them all."""
    if not row:
        return "no user with this id, and no organisation with this id has an owner"
    if row.get("role_id") != 1:
        return f"the user is not a company admin (role_id={row.get('role_id')})"
    if row.get("welcome_email_sent_at") is not None:
        return f"already sent at {row['welcome_email_sent_at']}"
    if int(row.get("welcome_email_attempts") or 0) >= MAX_ATTEMPTS:
        return f"all {MAX_ATTEMPTS} attempts are used up"
    if row.get("tried_recently"):
        return "an attempt was made less than an hour ago (retries wait one hour)"
    if not row.get("email_ok"):
        return "the admin's e-mail address is blank or not valid"
    return None


def skip_reason(row: Optional[Dict[str, Any]]) -> str:
    """first_failure for the log. A claim that returned nothing although the row passes every condition means another request claimed it."""
    return first_failure(row) or "not eligible for a reason this check does not know (another request may have claimed it at the same moment)"


def diagnose(cursor, user_id: str) -> str:
    """skip_reason for ``user_id`` after a claim returned nothing. Never raises (a diagnostic must not hide the real outcome)."""
    try:
        cursor.execute(DIAGNOSE_SQL, (user_id, user_id))
        row = cursor.fetchone()
        return skip_reason(dict(row) if row else None)
    except Exception as exc:
        return f"reason unknown (the diagnostic query failed: {type(exc).__name__})"


def claim_send(cursor, user_id: str) -> Optional[Dict[str, Any]]:
    """The admin's {user_id, name, email, org_id} when THIS call won the right to send the welcome e-mail, else None. The caller commits."""
    cursor.execute(CLAIM_SQL, (user_id, user_id, MAX_ATTEMPTS))
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


def _safe_message(exc: Exception, claimed: Optional[Dict[str, Any]]) -> str:
    """The error text for the log, on one line, with the recipient's address (SES repeats it in its errors) replaced."""
    text = " ".join(str(exc).split())[:300]
    address = str((claimed or {}).get("email") or "")
    return text.replace(address, "<recipient>") if address else text


def process(user_id: str) -> str:
    """Claim, render, send and record, synchronously. Returns 'skipped' (not eligible / nothing to do), 'sent' or 'failed'. Never raises."""
    conn = None
    stage = "connecting to the database"
    try:
        logger.info("welcome-email: checking user %s", user_id)
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        stage = "claiming the send"
        claimed = claim_send(cur, user_id)
        conn.commit()                                   # the claim is visible to every other request before anything slow happens
        if not claimed:
            reason = diagnose(cur, user_id)
            conn.commit()
            logger.info("welcome-email: skipped user %s: %s", user_id, reason)
            return "skipped"
        logger.info("welcome-email: claimed user %s (admin %s); preparing the e-mail", user_id, claimed.get("user_id"))
        stage = "reading the company details"
        data = gather(cur, claimed)
        conn.commit()
        stage = "building the e-mail"
        from utils import welcome_email_template as tpl
        mail = tpl.render_welcome_email(
            first_name=data["first_name"], org_name=data["org_name"], trial_end=data["trial_end"],
            staff_limit=data["staff_limit"], trial_months=data["trial_months"],
        )
        subject = getattr(mail, "subject", None) or subject_for(claimed.get("name"), data["trial_months"])
        stage = "sending through Amazon SES"
        logger.info("welcome-email: sending to admin %s via SES (sender configured: %s, region %s, logo inline: %s)", claimed.get("user_id"),
                    bool((os.getenv("EMAIL_USER") or "").strip()), os.getenv("AWS_SES_REGION", "ap-south-1"),
                    bool(getattr(mail, "logo_bytes", None)))
        send_email(claimed["email"], subject, mail.html, mail.text,
                   logo_cid=getattr(mail, "logo_cid", None), logo_bytes=getattr(mail, "logo_bytes", None))
        stage = "recording the send"
        cur.execute(MARK_SENT_SQL, (claimed["user_id"],))      # the ADMIN's user id (the id we were called with may be the organisation's)
        conn.commit()
        logger.info("welcome-email: SENT for user %s (admin %s)", user_id, claimed["user_id"])
        return "sent"
    except Exception as exc:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        logger.error("welcome-email: FAILED for user %s while %s: %s: %s (the attempt is counted; retried after an hour, %d attempts in total)",
                     user_id, stage, type(exc).__name__, _safe_message(exc, claimed if "claimed" in locals() else None), MAX_ATTEMPTS)
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
        logger.exception("welcome-email: worker for user %s crashed", user_id)


async def schedule_welcome_email(user_id: Optional[str]) -> None:
    """Called by the Home screen's first request. Returns immediately and never raises: the work runs in a worker thread.
    Off (the default): returns before any query. The same admin is looked at once per SKIP_TTL_SECONDS per process."""
    try:
        global _warned_off
        if not user_id:
            return
        if not enabled():
            if not _warned_off:
                _warned_off = True
                logger.info("welcome-email: OFF (WELCOME_EMAIL_ENABLED=%r; set it to 1 in the backend's environment and restart). "
                            "No welcome e-mail is sent while it is off. This line is written once per process.", os.getenv("WELCOME_EMAIL_ENABLED"))
            return
        if _remember(str(user_id)):
            logger.info("welcome-email: user %s was looked at less than %d minutes ago by this process; not checking again yet",
                        user_id, SKIP_TTL_SECONDS // 60)
            return
        loop = asyncio.get_running_loop()
        logger.info("welcome-email: home screen loaded for user %s; queued the check", user_id)
        loop.run_in_executor(None, _run_safely, str(user_id))
    except Exception:
        logger.exception("welcome-email: could not schedule the check for user %s", user_id)
