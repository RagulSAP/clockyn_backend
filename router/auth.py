import asyncio
import hmac
import os
import secrets
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, Request
import logging
from fastapi.responses import StreamingResponse
from core_functions.auth_function import (
    admin_login, employee_login, admin_register_json,
    generate_otp, validate_otp, verify_device_token,
    update_push_token,
    DeviceTokenVerifyRequest, UpdatePushTokenRequest,
    AdminLoginRequest, EmployeeLoginRequest,
    GenerateOTPRequest, ValidateOTPRequest, DeviceTokenRequest
)
from core_functions.auth_function import logout_user, LogoutRequest
from core_functions.db_function import get_db_connection, return_connection
from utils.auth_mode import get_user_auth_mode
from core_functions.session_registry import register_session, unregister_session
from pydantic import BaseModel
from typing import List, Optional
from psycopg2.extras import RealDictCursor
from ml.s3 import save_org_logo, get_org_logo_url, save_employee_profile, get_employee_profile_url
from core_functions import operator_auth
from core_functions.owner_guard import require_owner
from utils import operator_token, rate_limit
from utils.uploads import read_upload

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

class AdminRegisterRequest(BaseModel):
    name: str
    emp_id: str = ""
    mobile_number: str
    email: str
    org_name: str
    org_address: str = ""
    org_gst: str = ""
    gst_number: str
    departments: List[str]
    branches: List[str]
    pan_number: Optional[str] = None
    company_website: Optional[str] = None
    cin_number: Optional[str] = None
    tan_number: Optional[str] = None
    msme_number: Optional[str] = None
    business_category: Optional[str] = None
    device_token: Optional[str] = None
    state: Optional[str] = None
    district: Optional[str] = None
    pincode: Optional[int] = None
    referral_code: Optional[str] = None      # Refer & Earn: another organisation's code (optional)

class GenAdminLoginRequest(BaseModel):
    email: str

class GenAdminOTPVerifyRequest(BaseModel):
    email: str
    otp: str

OPERATOR_OTP_MAX_ATTEMPTS = 5        # wrong codes allowed per issued code (same as the app OTP, core_functions/auth_function.py)


def _otp_digits(value) -> bytes:
    return ("%06d" % int(value)).encode("ascii")


@router.post("/admin-login", dependencies=[Depends(rate_limit.by_ip("OTP_GENERATE"))])
async def gen_admin_login_route(data: GenAdminLoginRequest):
    from core_functions.auth_function import _send_email_otp_async
    from datetime import timedelta
    from utils.timezone import get_utc_now

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        email = data.email.lower().strip()
        cursor.execute(
            "SELECT email, role_id FROM gen_admin_login WHERE email = %s",
            (email,)
        )
        admin = cursor.fetchone()
        if not admin:
            raise HTTPException(status_code=401, detail="Email not found")

        # A locked code (5 wrong attempts) cannot be replaced by asking for a new one before it expires: otherwise the
        # attempt counter could be reset at will.
        cursor.execute("SELECT attempts, expires_at FROM email_otp_verification WHERE email = %s", (email,))
        existing_otp = cursor.fetchone()
        if (existing_otp and (existing_otp["attempts"] or 0) >= OPERATOR_OTP_MAX_ATTEMPTS
                and existing_otp["expires_at"] > get_utc_now()):
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect OTP attempts. Please wait for the current OTP to expire before requesting a new one."
            )

        otp_code = secrets.randbelow(900000) + 100000
        expires_at = get_utc_now() + timedelta(minutes=5)

        cursor.execute("DELETE FROM email_otp_verification WHERE email = %s", (email,))
        cursor.execute(
            "INSERT INTO email_otp_verification (email, otp_code, is_verified, attempts, created_at, expires_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (email, otp_code, False, 0, get_utc_now(), expires_at)
        )
        conn.commit()

        sent = await _send_email_otp_async(email, otp_code, "login")
        if not sent:
            raise HTTPException(status_code=503, detail="Could not send OTP email. Please try again.")

        logger.info("[GenAdmin] OTP emailed to ...%s", email[-8:])
        return {"success": True, "message": "OTP sent to your email"}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)

@router.post("/admin-login/verify-otp", dependencies=[Depends(rate_limit.by_ip("AUTH_LOGIN"))])
async def gen_admin_verify_otp_route(data: GenAdminOTPVerifyRequest):
    """Operator sign-in, step 2. Answers `{success, email, role_id}` and, when AUTH_TOKEN_SECRET is configured, the operator
    session token `access_token` / `token_type` ("Bearer") / `expires_in` (seconds) that the console sends as
    `Authorization: Bearer` (core_functions/operator_auth.py). Five wrong codes lock the issued code until it expires."""
    from utils.timezone import get_utc_now

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        try:
            otp_int = int(data.otp)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid OTP format")

        email = data.email.lower().strip()
        cursor.execute(
            "SELECT * FROM email_otp_verification WHERE email = %s AND expires_at > %s",
            (email, get_utc_now())
        )
        otp_record = cursor.fetchone()
        if not otp_record:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        if (otp_record["attempts"] or 0) >= OPERATOR_OTP_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect OTP attempts. Please request a new OTP."
            )

        if not hmac.compare_digest(_otp_digits(otp_record["otp_code"]), _otp_digits(otp_int)):
            cursor.execute(
                "UPDATE email_otp_verification SET attempts = attempts + 1 WHERE email = %s",
                (email,)
            )
            conn.commit()
            raise HTTPException(status_code=400, detail="Invalid or expired OTP")

        cursor.execute("DELETE FROM email_otp_verification WHERE email = %s", (email,))

        cursor.execute(
            "SELECT email, role_id FROM gen_admin_login WHERE email = %s",
            (email,)
        )
        admin = cursor.fetchone()
        conn.commit()
        if not admin:
            raise HTTPException(status_code=401, detail="Email not found")

        response = {"success": True, "email": admin["email"], "role_id": admin["role_id"]}
        if admin["role_id"] is not None and operator_token.tokens_enabled():
            try:
                token, expires_in = operator_token.issue_operator_token(admin["email"], admin["role_id"])
                response.update({"access_token": token, "token_type": "Bearer", "expires_in": expires_in})
            except Exception:
                logger.exception("operator session token not issued")
        return response
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)

class GenAdminUserRequest(BaseModel):
    email: str
    name: str = ""
    role_id: int = 2

class GenAdminUserUpdateRequest(BaseModel):
    email: str
    name: str = ""
    role_id: int = 2

def _require_gen_admin(request):
    """Any platform operator: `X-Operator-Key` (OPERATOR_API_KEY) or the legacy admin-phone header login (a `gen_admin_login`
    row). Both live in core_functions/operator_auth.py, the ONE guard of the operator routes."""
    operator_auth.authorize(request, always_check=True)


def _require_role1(request):
    """Operator administration: `X-Operator-Key`, or a legacy admin-phone header that is a role_id=1 `gen_admin_login` row."""
    operator_auth.authorize(request, super_only=True, always_check=True,
                            denied_detail="Only role_id=1 admins can perform this action")

@router.get("/gen-admin-users")
async def list_gen_admin_users(request: Request):
    _require_role1(request)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT email, name, role_id FROM gen_admin_login ORDER BY email")
        users = cursor.fetchall()
        return {"success": True, "users": [dict(u) for u in users]}
    finally:
        cursor.close()
        return_connection(conn)

@router.post("/gen-admin-users")
async def add_gen_admin_user(data: GenAdminUserRequest, request: Request):
    _require_role1(request)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        email = data.email.lower().strip()
        cursor.execute("SELECT email FROM gen_admin_login WHERE email = %s", (email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="Email already exists")
        cursor.execute(
            "INSERT INTO gen_admin_login (email, name, role_id) VALUES (%s, %s, %s)",
            (email, data.name, data.role_id)
        )
        conn.commit()
        return {"success": True, "user": {"email": email, "name": data.name, "role_id": data.role_id}}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)

@router.put("/gen-admin-users/{email}")
async def update_gen_admin_user(email: str, data: GenAdminUserUpdateRequest, request: Request):
    _require_role1(request)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        new_email = data.email.lower().strip()
        cursor.execute(
            "UPDATE gen_admin_login SET email = %s, name = %s, role_id = %s WHERE email = %s",
            (new_email, data.name, data.role_id, email.lower().strip())
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return {"success": True, "user": {"email": new_email, "name": data.name, "role_id": data.role_id}}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)

@router.delete("/gen-admin-users/{email}")
async def delete_gen_admin_user(email: str, request: Request):
    _require_role1(request)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("DELETE FROM gen_admin_login WHERE email = %s", (email.lower().strip(),))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="User not found")
        conn.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        cursor.close()
        return_connection(conn)

@router.post("/register")
async def register_route(data: AdminRegisterRequest):
    return await admin_register_json(data, data.device_token)

@router.post("/register-company")
async def register_company_route(data: AdminRegisterRequest):
    return await admin_register_json(data, data.device_token)

@router.post("/update-push-token",
         summary="Update Push Token",
         description="Update user push token for notifications.")
async def update_push_token_route(request: UpdatePushTokenRequest):
    logger.info(f"Auth endpoint: update-push-token called for user {request.user_id}")
    return await update_push_token(request)

@router.post("/login", dependencies=[Depends(rate_limit.by_ip("AUTH_LOGIN"))])
async def login_route(data: AdminLoginRequest):
    return await admin_login(data)

@router.post("/employee-login", dependencies=[Depends(rate_limit.by_ip("AUTH_LOGIN"))])
async def employee_login_route(data: EmployeeLoginRequest):
    return await employee_login(data)

@router.get("/config")
async def auth_config_route():
    """Public: how this deployment signs users in (`USER_AUTH`): "otp" = mobile + SMS code, "email" = email + emailed code."""
    return {"success": True, "user_auth": get_user_auth_mode()}

@router.post("/generate-otp", dependencies=[Depends(rate_limit.by_ip("OTP_GENERATE"))])
async def generate_otp_route(data: GenerateOTPRequest):
    return await generate_otp(data)

@router.post("/validate-otp")
async def validate_otp_route(data: ValidateOTPRequest):
    return await validate_otp(data)

@router.post("/verify-device-token")
async def verify_device_token_route(data: DeviceTokenVerifyRequest):
    return await verify_device_token(data)

@router.post("/logout")
async def logout_route(data: LogoutRequest, request: Request):
    """The device signs out: clears the user's device token and the given push token. Always 200 {success: true}."""
    identity = getattr(request.state, "identity", None)
    return logout_user(data, identity.user_id if identity is not None else None)

# PUBLIC duplicate pre-check of the registration form (QW-13): the app calls it BEFORE it has any session. It answers
# `{"exists": bool}` and nothing else. User enumeration is accepted for the registration UX and bounded by the per-IP
# rate limit AUTH_CHECK (30/min); any account counts (register-company refuses a duplicate of any role).
def _contact_exists(sql: str, value: str) -> dict:
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(sql, (value,))
        return {"exists": cursor.fetchone() is not None}
    finally:
        cursor.close()
        return_connection(conn)


@router.get("/check-mobile/{mobile}", dependencies=[Depends(rate_limit.by_ip("AUTH_CHECK"))])
async def check_mobile_exists(mobile: str):
    return _contact_exists("SELECT 1 AS ok FROM users WHERE mobile_number = %s LIMIT 1", mobile.strip())


@router.get("/check-email/{email}", dependencies=[Depends(rate_limit.by_ip("AUTH_CHECK"))])
async def check_email_exists(email: str):
    return _contact_exists("SELECT 1 AS ok FROM users WHERE LOWER(email) = LOWER(%s) LIMIT 1", email.strip())

@router.post("/upload-org-logo/{org_id}")
async def upload_org_logo(org_id: str, logo: UploadFile = File(...)):
    try:
        logo_bytes = await read_upload(logo, allow_heic=True)
        logo_url = save_org_logo(org_id, logo_bytes)
        return {"success": True, "logo_url": logo_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/org-logo/{org_id}")
async def get_org_logo(org_id: str):
    try:
        logo_url = get_org_logo_url(org_id)
        if logo_url:
            return {"success": True, "logo_url": logo_url}
        else:
            return {"success": False, "logo_url": None}
    except Exception as e:
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.post("/upload-employee-profile/{user_id}")
async def upload_employee_profile(user_id: str, photo: UploadFile = File(...)):
    try:
        photo_bytes = await read_upload(photo, allow_heic=True)
        photo_url = save_employee_profile(user_id, photo_bytes)
        return {"success": True, "photo_url": photo_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/employee-profile/{user_id}")
async def get_employee_profile(user_id: str):
    try:
        photo_url = get_employee_profile_url(user_id)
        return {"success": True, "photo_url": photo_url}
    except Exception as e:
        logger.exception("Unexpected server error")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/user-mobile/{user_id}", dependencies=[Depends(require_owner)])
async def get_user_mobile(user_id: str):
    """Get user's mobile number for OTP verification. Owner only (auth_policy.OWNER_ONLY_RULES): with a token the id
    must be the caller's own; an administrator cannot read a colleague's number through it."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT mobile_number FROM users WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {"success": True, "mobile_number": user['mobile_number']}
    finally:
        cursor.close()
        return_connection(conn)

@router.get("/session-events/{user_id}")
async def session_events(user_id: str, device_token: str):
    """SSE endpoint. Old device connects here after login.
    When a new device logs in and overwrites device_token,
    this connection immediately receives a force_logout event.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT device_token FROM users WHERE user_id = %s",
            (user_id,)
        )
        row = cursor.fetchone()
        if not row or row["device_token"] != device_token:
            raise HTTPException(status_code=403, detail="Invalid user_id or device_token")
    finally:
        cursor.close()
        return_connection(conn)

    queue: asyncio.Queue = asyncio.Queue()
    register_session(user_id, queue)

    async def event_stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if event == "force_logout":
                        yield f"data: force_logout\n\n"
                        return
                except asyncio.TimeoutError:
                    # Send a heartbeat to keep the connection alive
                    yield f": heartbeat\n\n"
        finally:
            unregister_session(user_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
