from fastapi import HTTPException, Form, File, UploadFile
from core_functions.db_function import get_db_connection, return_connection
from datetime import datetime, timedelta
from pydantic import BaseModel
from typing import List, Optional
import uuid
import random
import os
import time
import httpx
import logging
import math
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import asyncio
from psycopg2.extras import RealDictCursor
from ml.s3 import save_org_logo
 
logger = logging.getLogger(__name__)
from utils.timezone import get_utc_now, convert_utc_to_ist, IST
from utils.auth_mode import is_email_auth
from utils import branding
from html import escape as html_escape
from core_functions import request_rules as _request_rules  # ENFORCE_SINGLE_DEVICE policy (pure helpers)
from core_functions import auth_dependency as _auth  # bearer access tokens: issue at login, revoke on sign-out (WP12)

# Whitelist for OTP table/column identifiers — values are hardcoded here and must never be derived from user input
_OTP_TABLE_MAP: dict = {
    True:  ("email_otp_verification", "email"),
    False: ("moblie_otp_verification", "mobile_number"),
}

# Test/review accounts that bypass real SMS OTP — accepted OTP is always 123456
_BYPASS_NUMBERS: dict = {
    "7904559310": "123456",   # admin
    "9842329027": "123456",   # admin
    "8778783720": "123456",   # employee
    "9876543210": "123456",   # employee
}


def _otp_bypass_enabled() -> bool:
    """The static-code bypass for the numbers above is OFF unless env OTP_BYPASS is 1/true/yes (read on every call)."""
    return os.getenv("OTP_BYPASS", "").strip().lower() in ("1", "true", "yes")


def _is_bypass_contact(contact: str) -> bool:
    return _otp_bypass_enabled() and contact in _BYPASS_NUMBERS


# ---------------------------------------------------------------------------- OTP send throttle
_OTP_MAX_PER_HOUR_DEFAULT = 5
_OTP_MIN_GAP_SECONDS_DEFAULT = 30


def _env_int(name: str, default: int, minimum: int) -> int:
    """Integer from the environment; unset, non-numeric or below `minimum` -> `default`."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not a whole number - using %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r is below %s - using %s", name, raw, minimum, default)
        return default
    return value


def _otp_throttle_limits() -> tuple:
    """(max sends per rolling hour, minimum seconds between two sends) - OTP_MAX_PER_HOUR / OTP_MIN_GAP_SECONDS."""
    return (
        _env_int("OTP_MAX_PER_HOUR", _OTP_MAX_PER_HOUR_DEFAULT, 1),
        _env_int("OTP_MIN_GAP_SECONDS", _OTP_MIN_GAP_SECONDS_DEFAULT, 0),
    )


def _check_otp_send_throttle(cursor, contact: str) -> None:
    """429 when `contact` asked for a code too soon after the previous one or too often within the last hour.

    Runs before a new code is stored or sent, so a rejected request costs no SMS/e-mail and the previous code stays valid.
    """
    max_per_hour, min_gap = _otp_throttle_limits()
    cursor.execute(
        """
        SELECT COUNT(*) AS n,
               EXTRACT(EPOCH FROM (now() - MAX(sent_at))) AS since_last,
               EXTRACT(EPOCH FROM (now() - MIN(sent_at))) AS since_first
        FROM otp_send_log
        WHERE contact = %s AND sent_at > now() - interval '1 hour'
        """,
        (contact,),
    )
    row = cursor.fetchone() or {}
    count = int(row.get("n") or 0)
    if count <= 0:
        return
    since_last = float(row.get("since_last") or 0)
    since_first = float(row.get("since_first") or 0)
    if count >= max_per_hour:
        retry = max(1, math.ceil(3600 - since_first))
        raise HTTPException(
            status_code=429,
            detail="Too many codes requested. Try again later.",
            headers={"Retry-After": str(retry)},
        )
    if since_last < min_gap:
        wait = max(1, math.ceil(min_gap - since_last))
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {wait} seconds before requesting another code.",
            headers={"Retry-After": str(wait)},
        )


def _log_otp_send(cursor, contact: str) -> None:
    """Record a send (same transaction as the stored code) and prune entries older than 24 h."""
    cursor.execute("DELETE FROM otp_send_log WHERE sent_at < now() - interval '24 hours'")
    cursor.execute("INSERT INTO otp_send_log (contact) VALUES (%s)", (contact,))


def _unlog_otp_send(cursor, contact: str) -> None:
    """Take the newest entry of `contact` back (the code was dropped because the e-mail could not be sent)."""
    cursor.execute(
        "DELETE FROM otp_send_log WHERE id = (SELECT MAX(id) FROM otp_send_log WHERE contact = %s)",
        (contact,),
    )

def _normalize_contact(contact: str) -> str:
    """Trim the contact; e-mail addresses are compared in lower case."""
    contact = (contact or "").strip()
    return contact.lower() if "@" in contact else contact


def _contact_clause(contact: str, alias: str = "u") -> str:
    """SQL predicate (one %s) matching a user by e-mail (case-insensitive) when the contact holds '@', else by mobile."""
    if "@" in contact:
        return f"LOWER({alias}.email) = LOWER(%s)"
    return f"{alias}.mobile_number = %s"


def _reject_phone_in_email_mode(contact: str) -> None:
    """USER_AUTH=email: no SMS is ever sent, so the OTP routes only accept e-mail addresses."""
    if is_email_auth() and "@" not in contact:
        raise HTTPException(
            status_code=400,
            detail="Sign-in uses your email address. Enter your email address instead of a mobile number.",
        )


def _ensure_single_email_account(cursor, contact: str, role_ids: tuple) -> None:
    """An e-mail can only identify one active account of the given roles (staff e-mails are unique per company only)."""
    if "@" not in contact:
        return
    cursor.execute(
        "SELECT COUNT(*) AS n FROM users WHERE LOWER(email) = LOWER(%s) AND status = 1 AND role_id IN %s",
        (contact, role_ids),
    )
    row = cursor.fetchone()
    if row and row["n"] > 1:
        raise HTTPException(
            status_code=409,
            detail="This email address is linked to more than one account. Please contact your admin.",
        )


# SMS credentials - configure these in your environment
sms_cred = {
    'MSG91_AUTH_KEY': os.getenv('MSG91_AUTH_KEY', ''),
    'MSG91_TEMPLATE_ID': os.getenv('MSG91_TEMPLATE_ID', '')
}

# Email config
email_config = {
    'EMAIL_USER': os.getenv('EMAIL_USER', ''),
    'AWS_SES_REGION': os.getenv('AWS_SES_REGION', 'ap-south-1')
}
# 
# Push (FCM v1): the sender, token storage and realtime fan-out live in services/push_service.py and
# services/realtime.py. The names below stay importable from here for existing callers.
from services import push_service as _push_service
from services import realtime as _realtime


def get_fcm_access_token():
    """(access_token, project_id) or (None, None); see services.push_service.get_fcm_access_token."""
    return _push_service.get_fcm_access_token()


class UpdatePushTokenRequest(BaseModel):
    user_id: str
    push_token: str
    device_token: Optional[str] = None  # optional — validate session if provided
    platform: Optional[str] = None  # optional: 'android' | 'ios' | 'web' (stored in user_push_tokens)

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    def model_post_init(self, __context):
        if not self.push_token or not self.push_token.strip():
            raise ValueError('push_token must not be null or empty')
        if not self.user_id or not self.user_id.strip():
            raise ValueError('user_id must not be null or empty')

async def update_push_token(data: UpdatePushTokenRequest):
    """
    Update push token by user_id — always succeeds if user exists.
    device_token is ignored for push_token updates (no session gate).
    """
    if not data.push_token or not data.push_token.strip():
        logger.warning(f"[PushToken] Rejected null/empty push_token for user {data.user_id}")
        raise HTTPException(status_code=400, detail={'success': False, 'message': 'push_token must not be null or empty'})
    logger.info(f"[PushToken] update_push_token called for user: {data.user_id}")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        current_time = get_utc_now()
        cursor.execute(
            'UPDATE users SET push_token = %s, updated_at = %s WHERE user_id = %s',
            (data.push_token, current_time, data.user_id)
        )
        if cursor.rowcount == 0:
            logger.warning(f"[PushToken] No user found with user_id={data.user_id}")
            raise HTTPException(status_code=404, detail={'success': False, 'message': 'User not found'})
        # Multi-device: also register the token in user_push_tokens (device_token doubles as the device id)
        _push_service.upsert_user_token(cursor, data.user_id, data.push_token, data.platform, data.device_token)
        conn.commit()
        logger.info(f"[PushToken] Successfully saved push_token for user {data.user_id}")
        return {'success': True, 'message': 'Push token updated successfully'}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        logger.error(f"[PushToken] update_push_token error for user {data.user_id}: {e}")
        raise HTTPException(status_code=500, detail={'success': False, 'message': 'Internal server error'})
    finally:
        cursor.close()
        return_connection(conn)
 
async def _send_push_to_token(token: str, title: str, body: str, data: dict, image_url: str = None):
    """Send a push notification via FCM V1 API using a raw FCM device token (delegates to services.push_service)."""
    await _push_service.send_push(token, title, body, data or {}, image_url)


async def send_push_notification(push_token: str, title: str, body: str, data: dict = None, image_url: str = None):
    """Generic push notification sender for any token."""
    if not push_token:
        return
    await _push_service.send_push(push_token, title, body, data or {}, image_url)


async def send_punch_in_out_notification(user_id: str, org_id: str, punch_type: str, punch_time: str, image_url: str = None):
    """Punch in/out notification to the org's admin and managers: socket `badge_update` + push (services.realtime)."""
    logger.info(f"[Notify] send_punch_in_out_notification user={user_id} org={org_id} type={punch_type}")
    await _realtime.notify_punch_in_out(user_id, org_id, punch_type, punch_time, image_url)


_SINGLE_DEVICE_ROLES = (2, 3)  # managers and staff are signed in on one device; the admin (role 1) may use several


async def _snapshot_previous_device(cursor, user: dict, new_device_token: str, new_push_token: str = None) -> dict:
    """Read the user's current device/push token BEFORE a login overwrites them.
    Returns {"takeover": bool, "old_tokens": [...]}: takeover = a single-device role (2/3) that had another device;
    old_tokens = that device's push tokens (never the token being registered now)."""
    cursor.execute("SELECT device_token, push_token FROM users WHERE user_id = %s", (user['user_id'],))
    row = cursor.fetchone() or {}
    previous_device = row.get('device_token')
    takeover = bool(previous_device) and previous_device != new_device_token and user.get('role_id') in _SINGLE_DEVICE_ROLES
    old_tokens = []
    if takeover:
        candidates = [row.get('push_token')]
        try:
            loop = asyncio.get_running_loop()
            candidates += await loop.run_in_executor(None, _push_service.get_user_tokens, user['user_id'])
        except Exception as e:
            logger.warning(f"[PushToken] could not list the previous device tokens: {e}")
        old_tokens = [t for t in dict.fromkeys(candidates) if t and t != new_push_token]
    # ENFORCE_SINGLE_DEVICE: an admin signing in on a second device does not end the first one (no legacy SSE signal)
    skip_sse = user.get('role_id') == 1 and _request_rules.single_device_enforced()
    return {"takeover": takeover, "old_tokens": old_tokens, "skip_sse": skip_sse}


def _file_device_access_request(user_id: str, org_id: str):
    """File the device-change request of a login from a device that is not the account's active one.
    One pending request per user (a repeat returns the existing one) and at most 3 per 24 hours (429, swallowed
    here). Returns the request result dict, or None when none could be filed. Never raises."""
    try:
        from core_functions.requests_functions import submit_device_change_request
        return submit_device_change_request(user_id, org_id, "Device change requested")
    except Exception as e:
        logger.warning(f"[Device] could not file a device-change request for {user_id}: {e}")
        return None


async def _signal_previous_device(user_id: str, previous: dict) -> None:
    """After a login saved the new device: legacy SSE force_logout (always, as before) and, on a single-device
    takeover, the `force_logout` socket frame + a silent FCM data message to the previous device's tokens."""
    if previous.get("skip_sse"):
        return
    try:
        from core_functions.session_registry import force_logout_sse
        await force_logout_sse(user_id)
        if previous.get("takeover"):
            await _realtime.force_logout(user_id, push_tokens=previous.get("old_tokens") or [], sse=False)
    except Exception as e:
        logger.warning(f"[Notify] force_logout signalling failed for {user_id}: {e}")



class LogoutRequest(BaseModel):
    user_id: str
    device_token: Optional[str] = None
    push_token: Optional[str] = None


def logout_user(data: LogoutRequest, caller_id: Optional[str] = None) -> dict:
    """POST /auth/logout: the device signs the user out. Clears the user's `device_token` and removes the given push
    token (and only that one: the user's other devices keep theirs).

    A `device_token` that is no longer the stored one (another device has taken over since) is NOT cleared: a stale
    device must not sign the active one out. Without a `device_token` the stored one is cleared.
    Idempotent and forgiving: unknown user, repeated call or a storage error all answer {"success": True}.
    Bearer tokens: a manager / staff logout (a call that really cleared the device token) increments users.auth_epoch,
    which revokes every token of that user; an admin logout does not (admins are multi-device)."""
    user_id = (data.user_id or "").strip()
    device_token = (data.device_token or "").strip() or None
    push_token = (data.push_token or "").strip() or None
    if not user_id:
        return {"success": True}
    if not device_token and _auth.tokens.require_auth_enabled() and caller_id != user_id:
        # REQUIRE_AUTH: this route is public (the access token may already be dead), so without the secret device token a
        # bare user_id must not sign somebody else out: only the caller's own valid token may do that.
        return {"success": True}
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if device_token:
                cursor.execute(
                    "UPDATE users SET device_token = NULL, device_token_expires_at = NULL "
                    "WHERE user_id = %s AND device_token = %s",
                    (user_id, device_token)
                )
            else:
                cursor.execute(
                    "UPDATE users SET device_token = NULL, device_token_expires_at = NULL WHERE user_id = %s",
                    (user_id,)
                )
            if cursor.rowcount > 0:
                # The device really signed out: revoke the bearer tokens of a single-device user (role 2/3). A stale
                # device (its device_token no longer matches) updated no row and revokes nothing. Admins are multi-device.
                _auth.bump_epoch(cursor, user_id)
            if push_token:
                cursor.execute(
                    "UPDATE users SET push_token = NULL WHERE user_id = %s AND push_token = %s",
                    (user_id, push_token)
                )
                cursor.execute("SAVEPOINT logout_push_token")
                try:
                    cursor.execute("DELETE FROM user_push_tokens WHERE user_id = %s AND token = %s",
                                   (user_id, push_token))
                    cursor.execute("RELEASE SAVEPOINT logout_push_token")
                except Exception as e:  # table not migrated yet: the legacy column was already cleared
                    logger.warning(f"[Logout] user_push_tokens not cleared: {e}")
                    cursor.execute("ROLLBACK TO SAVEPOINT logout_push_token")
            conn.commit()
            _auth.forget_user(user_id)
        finally:
            cursor.close()
    except Exception as e:
        logger.warning(f"[Logout] could not clear the session of {user_id}: {e}")
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            return_connection(conn)
    return {"success": True}


def _clear_admin_push_token(org_id: str):
    """Clear invalid push_token for the admin of an org."""
    try:
        conn2 = get_db_connection()
        try:
            with conn2.cursor() as c:
                c.execute("""
                    UPDATE users u SET push_token = NULL
                    FROM org_info o
                    WHERE o.parent_id = u.user_id AND o.org_id = %s AND u.role_id = 1
                """, (org_id,))
            conn2.commit()
            logger.warning(f"[Notify] Cleared invalid push_token for admin of org {org_id}")
        finally:
            return_connection(conn2)
    except Exception as e:
        logger.error(f"[Notify] Failed to clear push_token: {e}")
 
async def _send_email_otp_async(email, otp, purpose):
    """Send OTP via AWS SES (non-blocking)"""
    logger.info(f"Sending email OTP to: ...{email[-4:]}")
    try:
        import boto3
        from botocore.exceptions import ClientError
 
        ses = boto3.client('ses', region_name=email_config['AWS_SES_REGION'])
 
        app_name = branding.app_name()
        support = branding.support_email()
        support_line = (f"<p>Need help? Contact {html_escape(support)}.</p>" if support else "")
        body = f"""
        <html><body>
            <h2>Your OTP for {purpose.replace('_', ' ').title()}</h2>
            <p>Your {html_escape(app_name)} OTP is: <strong>{otp}</strong></p>
            <p>This OTP will expire in 5 minutes.</p>
            <p>If you didn't request this, please ignore this email.</p>
            {support_line}
        </body></html>
        """
 
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: ses.send_email(
            Source=email_config['EMAIL_USER'],
            Destination={'ToAddresses': [email]},
            Message={
                'Subject': {'Data': f"Your {purpose.replace('_', ' ').title()} OTP - {app_name}"},
                'Body': {'Html': {'Data': body}}
            }
        ))
 
        logger.info(f"Email OTP sent successfully to ...{email[-4:]}")
        return True
 
    except Exception as e:
        logger.error(f"Email sending error: {str(e)}")
        return False
 
class AdminRegisterRequest(BaseModel):
    name: str
    emp_id: str
    mobile_number: str
    email: str
    org_name: str
    org_address: str
    org_gst: str = ''
    gst_number: str = ''
    departments: List[str] = []
    branches: List[str]
    pan_number: str = None
    company_website: str = None
    cin_number: str = None
    tan_number: str = None
    msme_number: str = None
    business_category: str = None
    state: Optional[str] = None
    district: Optional[str] = None
    pincode: Optional[int] = None
    referral_code: Optional[str] = None     # Refer & Earn: another organisation's code (optional, only at registration)
 
class DeviceTokenVerifyRequest(BaseModel):
    contact: str
    device_token: str
 
async def admin_register_json(data: AdminRegisterRequest, device_token: str = None):
    if not all([data.org_name, data.mobile_number, data.email, data.branches]):
        raise HTTPException(status_code=400, detail="Company name, mobile, email, and branches are required")
       
    if not data.branches or len(data.branches) == 0:
        raise HTTPException(status_code=400, detail="At least one branch is required")
   
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
   
    try:
        # Check if user already exists
        cursor.execute("SELECT * FROM users WHERE mobile_number = %s OR email = %s", (data.mobile_number, data.email))
        existing_user = cursor.fetchone()
        if existing_user:
            raise HTTPException(status_code=400, detail="User already registered")

        # Refer & Earn: refuse an unknown / mistyped code (HTTP 400) BEFORE anything is created
        referral_code = (getattr(data, "referral_code", None) or "").strip()
        if referral_code:
            from core_functions import referral_functions as referral_fn
            referral_fn.resolve_code_for_registration(cursor, referral_code)
       
        # Generate IDs
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
       
        # Insert organization
        cursor.execute(
            "INSERT INTO org_info (org_id, org_name, org_address, org_latitude, org_longitude, org_gst, parent_id, state, district, pincode, created_at) VALUES (%s, %s, %s, 0, 0, %s, %s, %s, %s, %s, %s)",
            (org_id, data.org_name, data.org_address or '', data.org_gst or '', user_id, data.state, data.district, data.pincode, get_utc_now())
        )
       
        # Insert branches
        branch_ids = []
        for idx, branch_name in enumerate(data.branches, start=1):
            if branch_name.strip():
                cursor.execute(
                    "INSERT INTO branch_info (org_id, branch_id, branch_name, branch_address, gps_location, created_at) VALUES (%s, %s, %s, %s, POINT(0, 0), %s)",
                    (org_id, idx, branch_name.strip(), '', get_utc_now())
                )
                branch_ids.append(idx)
       
        # Insert departments (if provided)
        dept_ids = []
        if data.departments and len(data.departments) > 0:
            for dept_name in data.departments:
                if dept_name.strip():
                    dept_id = str(uuid.uuid4())
                    cursor.execute(
                        "INSERT INTO dept_info (org_id, dept_id, dept_name, created_at) VALUES (%s, %s, %s, %s)",
                        (org_id, dept_id, dept_name.strip(), get_utc_now())
                    )
                    dept_ids.append(dept_id)
        else:
            # Create default "General" department - check if it already exists first
            cursor.execute(
                "SELECT dept_id FROM dept_info WHERE org_id = %s AND dept_name = 'General'",
                (org_id,)
            )
            existing_dept = cursor.fetchone()
           
            if existing_dept:
                dept_id = existing_dept['dept_id']
            else:
                dept_id = str(uuid.uuid4())
                cursor.execute(
                    "INSERT INTO dept_info (org_id, dept_id, dept_name, created_at) VALUES (%s, %s, %s, %s)",
                    (org_id, dept_id, 'General', get_utc_now())
                )
            dept_ids.append(dept_id)
       
        # Use first branch for admin user assignment
        primary_branch_id = branch_ids[0] if branch_ids else None
       
        # Insert user (admin) — push_token set to empty string as placeholder; updated via update_push_token after login
        cursor.execute(
            "INSERT INTO users (user_id, org_id, name, emp_id, branch_id, dept_id, mobile_number, email, role_id, device_token, push_token, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, org_id, data.name, data.emp_id or '', primary_branch_id, None, data.mobile_number, data.email, 1, device_token, '', get_utc_now())
        )

        # Refer & Earn: the referral and both rewards are written in THIS transaction (committed with the organisation below)
        if referral_code:
            referral_fn.attach_referral_at_registration(cursor, org_id, referral_code)
       
        # Insert company additional info if provided
        if any([data.pan_number, data.company_website, data.cin_number, data.tan_number, data.msme_number, data.business_category, data.gst_number]):
            cursor.execute(
                "INSERT INTO company_additional_info (user_id, org_id, pan_number, company_website, cin_number, tan_number, msme_number, business_category, gst_number, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (user_id, org_id, data.pan_number or '', data.company_website or '', data.cin_number or '', data.tan_number or '', data.msme_number or '', data.business_category or '', data.gst_number or '', get_utc_now())
            )
       
        conn.commit()
       
        # Insert 1-month free trial
        from datetime import date
        from dateutil.relativedelta import relativedelta
        trial_start = date.today()
        trial_end   = trial_start + relativedelta(months=1)
        cursor.execute("""
            INSERT INTO free_trial (org_id, user_id, start_date, end_date, is_active)
            VALUES (%s, %s, %s, %s, TRUE)
            ON CONFLICT (org_id) DO NOTHING
        """, (org_id, user_id, trial_start, trial_end))
        conn.commit()

        # Refer & Earn: give the new organisation its own code now (best effort: it is also created on first view)
        try:
            from core_functions import referral_functions as referral_fn
            referral_fn.get_or_create_code(cursor, org_id)
            conn.commit()
        except Exception:
            conn.rollback()
            logger.warning("Referral code not created at registration for org %s (it is created on first view)", org_id, exc_info=True)
 
        # Get org logo URL if exists
        from ml.s3 import get_org_logo_url
        logo_url = get_org_logo_url(org_id)
       
        response = {
            "success": True,
            "message": "Registration successful!",
            "user_id": user_id,
            "org_id": org_id,
            "org_name": data.org_name,
            "role_id": 1,
            "org_logo": logo_url
        }
        # Bearer access token (additive fields access_token / expires_in); absent while AUTH_TOKEN_SECRET is not set
        response.update(_auth.issue_for_user(cursor, user_id))
        return response
       
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Registration failed")
    finally:
        cursor.close()
        return_connection(conn)
 
class AdminLoginRequest(BaseModel):
    login_id: str  # Can be mobile_number or email
 
class GenerateOTPRequest(BaseModel):
    contact: str
    purpose: str
    role_id: Optional[int] = None
 
class ValidateOTPRequest(BaseModel):
    contact: str
    otp_code: str
    purpose: str
    device_token: str = None
    push_token: str = None
 
class DeviceTokenRequest(BaseModel):
    contact: str
    device_token: str
 
class DeviceTokenVerifyRequest(BaseModel):
    contact: str
    device_token: str
    role_id: Optional[int] = None
    push_token: str = None
 
async def generate_otp(data: GenerateOTPRequest):
    if not data.contact or not data.purpose:
        raise HTTPException(status_code=400, detail="Contact and purpose are required")

    data.purpose = data.purpose.lower()
    if data.purpose not in ["registration", "login", "delete_account", "update_email"]:
        raise HTTPException(status_code=400, detail="Purpose must be 'registration', 'login', 'delete_account', or 'update_email'")
    data.contact = _normalize_contact(data.contact)
    _reject_phone_in_email_mode(data.contact)

    # Bypass OTP for test/review accounts — skip SMS and all DB checks
    if _is_bypass_contact(data.contact):
        return {
            "success": True,
            "message": f"{data.purpose.title()} mobile OTP sent successfully!"
        }

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
   
    try:
        # Check if user already exists for registration purpose
        if data.purpose == "registration":
            is_email = "@" in data.contact
            if is_email:
                cursor.execute("""
                    SELECT user_id FROM users
                    WHERE email = %s AND role_id = 1
                """, (data.contact,))
            else:
                cursor.execute("""
                    SELECT user_id FROM users
                    WHERE mobile_number = %s AND role_id = 1
                """, (data.contact,))
            existing_user = cursor.fetchone()
           
            if existing_user:
                if is_email:
                    raise HTTPException(status_code=400, detail="Email already registered")
                else:
                    raise HTTPException(status_code=400, detail="Mobile number already registered")
       
        # For login purpose, verify user exists and check role compatibility
        if data.purpose == "login":
            _ensure_single_email_account(
                cursor, data.contact,
                (1,) if data.role_id == 1 else (2, 3) if data.role_id in [2, 3] else (1, 2, 3),
            )
            # First check if user exists at all (including inactive/deleted)
            cursor.execute(f"""
                SELECT u.user_id, u.role_id, u.status
                FROM users u
                WHERE {_contact_clause(data.contact)}
                LIMIT 1
            """, (data.contact,))
            user = cursor.fetchone()

            # For employee login, require the phone number / email to exist
            if data.role_id in [2, 3] and not user:
                raise HTTPException(
                    status_code=404,
                    detail="There is no user with this email address" if "@" in data.contact else "There is no user with this phone number",
                )
           
            if user:
                # Check if deleted before anything else
                if user['status'] == 2:
                    raise HTTPException(status_code=403, detail="Your account has been deleted. Please create a new account. For further details, contact admin.")
                # Check if inactive
                if user['status'] == 0:
                    raise HTTPException(status_code=403, detail="Your account is inactive. Please contact your admin.")
               
                # Check role compatibility if role_id is provided
                if data.role_id is not None:
                    if data.role_id == 1 and user['role_id'] in [2, 3]:
                        raise HTTPException(status_code=403, detail="This is an employee account. Please use employee login portal.")
                    elif data.role_id in [2, 3] and user['role_id'] == 1:
                        raise HTTPException(status_code=403, detail="This is an admin account. Please use admin login portal.")
       
        otp_code = random.randint(100000, 999999)
        expires_at = get_utc_now() + timedelta(minutes=5)
       
        # Determine table based on contact type (from whitelist — never user-derived)
        is_email = "@" in data.contact
        table_name, contact_field = _OTP_TABLE_MAP[is_email]

        # Block OTP generation if previous OTP is locked (attempts >= 5) and not yet expired
        cursor.execute(
            f"SELECT attempts, expires_at FROM {table_name} WHERE {contact_field} = %s",
            (data.contact,)
        )
        existing_otp = cursor.fetchone()
        if existing_otp and existing_otp['attempts'] >= 5 and existing_otp['expires_at'] > get_utc_now():
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect OTP attempts. Please wait for the current OTP to expire before requesting a new one."
            )

        # Send throttle (OTP_MAX_PER_HOUR / OTP_MIN_GAP_SECONDS): refuse before the old code is replaced or anything is sent
        _check_otp_send_throttle(cursor, data.contact)

        # Delete existing OTP
        cursor.execute(f"DELETE FROM {table_name} WHERE {contact_field} = %s", (data.contact,))

        # Insert new OTP
        cursor.execute(
            f"INSERT INTO {table_name} ({contact_field}, otp_code, is_verified, attempts, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (data.contact, otp_code, False, 0, get_utc_now(), expires_at)
        )
        _log_otp_send(cursor, data.contact)

        conn.commit()
       
        contact_type = "Email" if is_email else "Mobile"
       
        # Send OTP via appropriate channel
        if is_email:
            if is_email_auth():
                # E-mail is the only channel in this mode: report a failed send instead of a code that never arrives.
                if not await _send_email_otp_async(data.contact, otp_code, data.purpose):
                    cursor.execute(f"DELETE FROM {table_name} WHERE {contact_field} = %s", (data.contact,))
                    _unlog_otp_send(cursor, data.contact)  # a failed send must not burn the contact's quota
                    conn.commit()
                    raise HTTPException(status_code=503, detail="We could not send the email right now. Please try again in a moment.")
            else:
                asyncio.create_task(_send_email_otp_async(data.contact, otp_code, data.purpose))
        else:
            sms_success = _send_msg91_sms(data.contact, str(otp_code))
            if not sms_success:
                logger.warning(f"SMS sending failed for {data.contact[-4:]}, but OTP stored in DB")
       
        return {
            "success": True,
            "message": f"{data.purpose.title()} {contact_type.lower()} OTP sent successfully!"
        }
       
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to generate OTP")
    finally:
        cursor.close()
        return_connection(conn)
 
async def validate_otp(data: ValidateOTPRequest):
    if not data.contact or not data.otp_code or not data.purpose:
        raise HTTPException(status_code=400, detail="Contact, OTP code, and purpose are required")

    data.purpose = data.purpose.lower()
    if data.purpose not in ["registration", "login", "delete_account", "update_email"]:
        raise HTTPException(status_code=400, detail="Purpose must be 'registration', 'login', 'delete_account', or 'update_email'")
    data.contact = _normalize_contact(data.contact)
    _reject_phone_in_email_mode(data.contact)

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Bypass for test/review accounts — accept static OTP, still update device token
        if _is_bypass_contact(data.contact) and data.otp_code == _BYPASS_NUMBERS[data.contact]:
            device_token = data.device_token or str(uuid.uuid4())
            token_expires_at = get_utc_now() + timedelta(days=90)
            cursor.execute(
                "SELECT user_id, role_id FROM users WHERE mobile_number = %s AND status = 1",
                (data.contact,)
            )
            user = cursor.fetchone()
            if user:
                _auth.bump_epoch_on_takeover(cursor, user['user_id'], device_token)  # single-device roles only
                if data.push_token:
                    cursor.execute(
                        "UPDATE users SET device_token = %s, device_token_expires_at = %s, push_token = %s WHERE user_id = %s",
                        (device_token, token_expires_at, data.push_token, user['user_id'])
                    )
                    _push_service.upsert_user_token(cursor, user['user_id'], data.push_token, None, device_token)
                else:
                    cursor.execute(
                        "UPDATE users SET device_token = %s, device_token_expires_at = %s WHERE user_id = %s",
                        (device_token, token_expires_at, user['user_id'])
                    )
                conn.commit()
                _auth.forget_user(user['user_id'])
            bypass_response = {
                "success": True,
                "message": f"{data.purpose.title()} mobile OTP validated successfully!",
                "device_token": device_token,
                "user_exists": bool(user),
                "role_id": user['role_id'] if user else None,
            }
            if user:
                bypass_response.update(_auth.issue_for_user(cursor, user['user_id']))
            return bypass_response

        # Determine table based on contact type (from whitelist — never user-derived)
        is_email = "@" in data.contact
        table_name, contact_field = _OTP_TABLE_MAP[is_email]

        # Convert OTP to integer for comparison
        try:
            otp_int = int(data.otp_code)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid OTP format")
       
        # Fetch record first to check attempts and expiry
        cursor.execute(
            f"SELECT * FROM {table_name} WHERE {contact_field} = %s AND expires_at > %s",
            (data.contact, get_utc_now())
        )
        otp_record = cursor.fetchone()

        if not otp_record:
            contact_type = "email" if is_email else "mobile"
            raise HTTPException(
                status_code=400,
                detail=f"Invalid or expired {data.purpose} {contact_type} OTP"
            )

        if otp_record['attempts'] >= 5:
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect OTP attempts. Please request a new OTP."
            )

        if otp_record['otp_code'] != otp_int:
            cursor.execute(
                f"UPDATE {table_name} SET attempts = attempts + 1 WHERE {contact_field} = %s",
                (data.contact,)
            )
            conn.commit()
            remaining = 4 - otp_record['attempts']
            contact_type = "email" if is_email else "mobile"
            raise HTTPException(
                status_code=400,
                detail=f"Invalid or expired {data.purpose} {contact_type} OTP"
            )

        if otp_record:
            cursor.execute(f"DELETE FROM {table_name} WHERE {contact_field} = %s", (data.contact,))
           
            # Generate device token if not provided
            device_token = data.device_token or str(uuid.uuid4())
           
            # Check if user exists and update device token
            cursor.execute(
                f"SELECT user_id, role_id FROM users WHERE {_contact_clause(data.contact, 'users')} AND status = 1",
                (data.contact,)
            )
            user = cursor.fetchone()
           
            token_expires_at = get_utc_now() + timedelta(days=90)
            user_exists = False
            if user:
                if data.purpose == "login" and _request_rules.single_device_enforced():
                    # ENFORCE_SINGLE_DEVICE: managers and staff keep one device. A login from another device is
                    # refused (409 device_mismatch) and an access request is filed for the admin instead.
                    cursor.execute(
                        "SELECT device_token, device_token_expires_at, parent_id FROM users WHERE user_id = %s",
                        (user['user_id'],)
                    )
                    held = cursor.fetchone() or {}
                    if _request_rules.single_device_blocked(user.get('role_id'), held.get('device_token'),
                                                            held.get('device_token_expires_at'), device_token,
                                                            get_utc_now()):
                        conn.commit()  # the OTP was consumed: a code is single use
                        _file_device_access_request(user['user_id'], held.get('parent_id'))
                        raise HTTPException(status_code=409, detail="device_mismatch")
                # Previous device (single-device roles): read before the UPDATE overwrites it
                previous_device = await _snapshot_previous_device(cursor, user, device_token, data.push_token)
                if previous_device['takeover'] and data.purpose == "login":
                    # a manager / staff login on another device ends the previous device's bearer tokens too
                    _auth.bump_epoch(cursor, user['user_id'])
                # Always save device_token; save push_token if provided
                if data.push_token:
                    cursor.execute(
                        "UPDATE users SET device_token = %s, device_token_expires_at = %s, push_token = %s WHERE user_id = %s",
                        (device_token, token_expires_at, data.push_token, user['user_id'])
                    )
                    _push_service.upsert_user_token(cursor, user['user_id'], data.push_token, None, device_token,
                                                    replace_others=previous_device['takeover'])
                    logger.info(f"[PushToken] Saved push_token via validate_otp for user {user['user_id']}")
                else:
                    cursor.execute(
                        "UPDATE users SET device_token = %s, device_token_expires_at = %s WHERE user_id = %s",
                        (device_token, token_expires_at, user['user_id'])
                    )
                conn.commit()
                _auth.forget_user(user['user_id'])
                # SSE force_logout (legacy) + chat-socket frame and silent push to the previous device on a takeover
                await _signal_previous_device(user['user_id'], previous_device)
                user_exists = True
            else:
                # For new users (registration flow), device token will be set during registration
                pass
           
            conn.commit()
           
            contact_type = "email" if is_email else "mobile"
            otp_response = {
                "success": True,
                "message": f"{data.purpose.title()} {contact_type} OTP validated successfully!",
                "device_token": device_token,
                "user_exists": user_exists,
                "role_id": user['role_id'] if user else None
            }
            if user_exists:
                # Bearer access token (additive fields access_token / expires_in); absent while AUTH_TOKEN_SECRET is not set
                otp_response.update(_auth.issue_for_user(cursor, user['user_id']))
            return otp_response
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail="Failed to validate OTP")
    finally:
        cursor.close()
        return_connection(conn)
 
async def verify_device_token(data: DeviceTokenVerifyRequest):
    if not data.contact or not data.device_token:
        raise HTTPException(status_code=400, detail="Contact and device token are required")
   
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Build query with optional role_id filter
        data.contact = _normalize_contact(data.contact)
        _ensure_single_email_account(
            cursor, data.contact,
            (1,) if data.role_id == 1 else (2, 3) if data.role_id in [2, 3] else (1, 2, 3),
        )
        query = f"""
            SELECT u.user_id, u.role_id, u.device_token, u.device_token_expires_at, u.name, u.email, o.org_id, u.status, o.org_name
            FROM users u
            LEFT JOIN org_info o ON (u.role_id = 1 AND o.parent_id = u.user_id) OR (u.role_id IN (2,3) AND (u.parent_id = o.org_id OR o.org_id = (SELECT m.parent_id FROM users m WHERE m.user_id = u.manager_id)))
            WHERE {_contact_clause(data.contact)}
        """
        params = [data.contact]
       
        # Add role_id filter if provided
        if data.role_id is not None:
            if data.role_id == 1:
                query += " AND u.role_id = 1"
            elif data.role_id in [2, 3]:
                query += " AND u.role_id IN (2, 3)"
       
        cursor.execute(query, params)
        user = cursor.fetchone()
       
        if user:
            # Check if deleted
            if user['status'] == 2:
                return {
                    "success": False,
                    "message": "Your account has been deleted. Please create a new account. For further details, contact admin.",
                    "skip_otp": False,
                    "deleted": True
                }
            # Check if user is inactive
            if user['status'] == 0:
                return {
                    "success": False,
                    "message": "Your account is inactive. Please contact your admin.",
                    "skip_otp": False,
                    "inactive": True
                }
       
        logger.info(f"Device verification for ...{data.contact[-4:]}: user found={bool(user)}")
        if user:
            logger.info(f"Device token match: {user['device_token'] == data.device_token}")

        if user and user['device_token'] is None:
            # device_token was cleared by admin (device change approved) — force re-login via OTP
            return {
                "success": False,
                "message": "Your device change request was approved. Please log in again with OTP on your new device.",
                "skip_otp": False,
                "device_change_approved": True,
                "user_id": user['user_id'],
                "org_id": user['org_id']
            }
        elif user and user['device_token'] == data.device_token and user.get('device_token_expires_at') and user['device_token_expires_at'] < get_utc_now():
            # Token matched but has expired — force re-login
            return {
                "success": False,
                "message": "Your session has expired. Please log in again with OTP.",
                "skip_otp": False,
                "session_expired": True,
                "user_id": user['user_id'],
                "org_id": user['org_id']
            }
        elif user and user['device_token'] == data.device_token:
            # Update push_token if provided (device may have a new Expo token)
            if data.push_token:
                cursor.execute(
                    "UPDATE users SET push_token = %s WHERE user_id = %s",
                    (data.push_token, user['user_id'])
                )
                _push_service.upsert_user_token(cursor, user['user_id'], data.push_token, None, data.device_token)
                conn.commit()
                logger.info(f"[PushToken] Saved push_token via verify_device_token for user {user['user_id']}")
            # Get fresh org logo URL (non-blocking S3 call)
            from ml.s3 import get_org_logo_url
            logo_url = await asyncio.get_running_loop().run_in_executor(None, get_org_logo_url, user['org_id'])

            response = {
                "success": True,
                "message": "Device verified successfully!",
                "skip_otp": True,
                "user_id": user['user_id'],
                "email": user['email'],
                "role_id": user['role_id'],
                "org_id": user['org_id'],
                "org_name": user['org_name'],
                "org_logo": logo_url
            }
           
            # Bearer access token (additive fields access_token / expires_in); absent while AUTH_TOKEN_SECRET is not set
            response.update(_auth.issue_for_user(cursor, user['user_id']))

            # Add manager permissions if role is manager
            if user['role_id'] == 2:
                cursor.execute(
                    """SELECT ps.setting_name FROM punchapp_manager_permissions mp
                       JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id
                       WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1
                       ORDER BY ps.setting_id""",
                    (user['user_id'], user['org_id'])
                )
                permissions = cursor.fetchall()
                response['manager_permissions'] = [p['setting_name'] for p in permissions]
           
            return response
        elif user and user['device_token'] != data.device_token:
            # Token mismatch means this device is not the active session
            enforced = _request_rules.single_device_enforced()
            if enforced and user['role_id'] == 1:
                # ENFORCE_SINGLE_DEVICE: admins stay multi-device: no access request, a normal OTP login follows
                return {
                    "success": False,
                    "message": "Please log in again with OTP on this device.",
                    "skip_otp": False,
                    "device_mismatch": True,
                    "request_pending": False,
                    "user_id": user['user_id'],
                    "org_id": user['org_id']
                }
            # Auto-submit a device change request for the admin to approve: one pending request per user (a repeat
            # returns the existing one), at most 3 per 24 hours
            filed = _file_device_access_request(user['user_id'], user['org_id'])
            return {
                "success": False,
                "message": ("This account is active on another device. Request access from your admin." if enforced
                            else "This account is already logged in on another device. Please log in again with OTP to take over the session."),
                "skip_otp": False,
                "device_mismatch": True,
                "request_pending": bool(filed and filed.get("success")),
                "user_id": user['user_id'],
                "org_id": user['org_id']
            }
        else:
            return {
                "success": False,
                "message": "Device not recognized",
                "skip_otp": False,
                "device_mismatch": False
            }
    finally:
        cursor.close()
        return_connection(conn)
 
class EmployeeLoginRequest(BaseModel):
    login_id: str
 
def _send_msg91_sms(mobile_number, otp):
    """Send OTP via MSG91 API (synchronous version)"""
    logger.info(f"Sending SMS OTP to mobile: {mobile_number[-4:]}")
    try:
        # Check if credentials are available
        if not sms_cred['MSG91_AUTH_KEY'] or not sms_cred['MSG91_TEMPLATE_ID']:
            logger.warning("MSG91 credentials not configured — SMS not sent")
            return False
       
        # Ensure mobile number has country code
        if not mobile_number.startswith('91'):
            mobile_number = '91' + mobile_number
       
        payload = {
            "template_id": sms_cred['MSG91_TEMPLATE_ID'],
            "short_url": "0",
            "realTimeResponse": "1",
            "recipients": [
                {
                    "mobiles": mobile_number,
                    "numeric": otp
                }
            ]
        }
       
        headers = {
            'accept': "application/json",
            'authkey': sms_cred['MSG91_AUTH_KEY'],
            'content-type': "application/json"
        }
       
        # Use synchronous httpx client
        with httpx.Client(timeout=10.0) as client:
            response = client.post("https://control.msg91.com/api/v5/flow", json=payload, headers=headers)
            result = response.json()
            logger.info(f"MSG91 response status: {response.status_code}")
       
        return result.get('type') == 'success'
       
    except Exception as e:
        logger.error(f"SMS sending error: {str(e)}")
        return False
   
 
 
async def admin_login(data: AdminLoginRequest):
    if not data.login_id:
        raise HTTPException(status_code=400, detail="Mobile number is required")
 
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
   
    try:
        data.login_id = _normalize_contact(data.login_id)
        clause = _contact_clause(data.login_id)
        _ensure_single_email_account(cursor, data.login_id, (1,))
        # Check if user exists (including inactive)
        cursor.execute(f"""
            SELECT u.user_id, u.role_id, u.status
            FROM users u
            WHERE {clause}
            LIMIT 1
        """, (data.login_id,))
        user_check = cursor.fetchone()

        # If user exists but is deleted
        if user_check and user_check['status'] == 2:
            raise HTTPException(status_code=403, detail="Your account has been deleted. Please create a new account. For further details, contact admin.")

        # If user exists but is inactive
        if user_check and user_check['status'] == 0:
            raise HTTPException(status_code=403, detail="Your account is inactive. Please contact your admin.")

        # If user exists but is employee, throw specific error
        if user_check and user_check['role_id'] in [2, 3]:
            raise HTTPException(status_code=403, detail="This is an employee account. Please use employee login portal.")

        # Check if login_id matches in users table with role_id = 1
        cursor.execute(f"""
            SELECT u.*, o.org_name, o.org_id as current_org_id
            FROM users u
            JOIN org_info o ON o.parent_id = u.user_id
            WHERE u.role_id = 1 AND u.status = 1
            AND {clause}
            LIMIT 1
        """, (data.login_id,))
        user = cursor.fetchone()
 
        if user:
            from ml.s3 import get_org_logo_url
            logo_url = await asyncio.get_running_loop().run_in_executor(None, get_org_logo_url, user['current_org_id'])

            # Get active plan_id for this org
            cursor.execute("""
                SELECT plan_id FROM punchapp_subscription
                WHERE org_id = %s AND status = 'ACTIVE'
                ORDER BY created_at DESC LIMIT 1
            """, (user['current_org_id'],))
            sub = cursor.fetchone()
            plan_id = sub['plan_id'] if sub else None
 
            # Check free trial
            cursor.execute("""
                SELECT end_date, is_active FROM free_trial
                WHERE org_id = %s
            """, (user['current_org_id'],))
            trial = cursor.fetchone()
            from datetime import date
            is_free_trial = bool(
                trial and trial['is_active'] and trial['end_date'] >= date.today()
            )
            free_trial_end = str(trial['end_date']) if trial else None
 
            return {
                "success": True,
                "message": "Login successful!",
                "user_id": user['user_id'],
                "email": user['email'],
                "org_name": user['org_name'],
                "org_id": user['current_org_id'],
                "role_id": user['role_id'],
                "org_logo": logo_url,
                "plan_id": plan_id,
                "is_free_trial": is_free_trial,
                "free_trial_end": free_trial_end,
                "redirect": "home"
            }
        else:
            raise HTTPException(status_code=401, detail="User not found. Please register first.")
    finally:
        cursor.close()
        return_connection(conn)
 
async def employee_login(data: EmployeeLoginRequest):
    if not data.login_id:
        raise HTTPException(status_code=400, detail="Mobile number is required")
 
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
   
    try:
        data.login_id = _normalize_contact(data.login_id)
        clause = _contact_clause(data.login_id)
        _ensure_single_email_account(cursor, data.login_id, (2, 3))
        # Check if user exists (including inactive)
        cursor.execute(f"""
            SELECT u.user_id, u.role_id, u.status
            FROM users u
            WHERE {clause}
            LIMIT 1
        """, (data.login_id,))
        user_check = cursor.fetchone()

        # If user exists but is deleted
        if user_check and user_check['status'] == 2:
            raise HTTPException(status_code=403, detail="Your account has been deleted. Please create a new account. For further details, contact admin.")

        # If user exists but is inactive
        if user_check and user_check['status'] == 0:
            raise HTTPException(status_code=403, detail="Your account is inactive. Please contact your admin.")

        # If user exists but is admin, throw specific error
        if user_check and user_check['role_id'] == 1:
            raise HTTPException(status_code=403, detail="This is an admin account. Please use admin login portal.")
       
        # Employees (Manager role_id=2 and Staff role_id=3) have parent_id = org_id
        cursor.execute(f"""
            SELECT u.*, o.org_name, o.org_id
            FROM users u
            JOIN org_info o ON (u.parent_id = o.org_id OR o.org_id = (SELECT m.parent_id FROM users m WHERE m.user_id = u.manager_id))
            WHERE u.role_id IN (2, 3) AND u.status = 1
            AND {clause}
            LIMIT 1
        """, (data.login_id,))
        user = cursor.fetchone()
       
        if user:
            # Get fresh org logo URL (non-blocking S3 call)
            from ml.s3 import get_org_logo_url
            logo_url = await asyncio.get_running_loop().run_in_executor(None, get_org_logo_url, user['org_id'])

            response_data = {
                "success": True,
                "message": "Login successful!",
                "user_id": user['user_id'],
                "org_name": user['org_name'],
                "org_id": user['org_id'],
                "role_id": user['role_id'],
                "org_logo": logo_url,
                "email": user.get('email'),
                "redirect": "emp_home"  # Both manager and staff go to employee portal
            }
           
            # If user is a manager (role_id = 2), get their permissions
            if user['role_id'] == 2:
                cursor.execute(
                    """SELECT ps.setting_name FROM punchapp_manager_permissions mp
                       JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id
                       WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1
                       ORDER BY ps.setting_id""",
                    (user['user_id'], user['org_id'])
                )
                permissions = cursor.fetchall()
                response_data['manager_permissions'] = [p['setting_name'] for p in permissions]
           
            return response_data
        else:
            raise HTTPException(status_code=401, detail="Employee not found or not authorized")
    finally:
        cursor.close()
        return_connection(conn)
