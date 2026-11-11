from fastapi import FastAPI
import uvicorn
from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, incentive, face_attendance, emp_expenses, requests, staff_location, chat, announcement, report, department, gen_orders, gen_payments, gen_plans, gen_webhook, advance, profile_photo, holiday, gen_dashboard, version, ws_badge, support, attribution, live_chat, gen_live_chat, analytics, referral
from utils import email
from utils import branding
from router import wages
from router import attendance_rule, salary_payments, lifecycle



app = FastAPI(
    title=branding.app_name(),
    description=f"FastAPI backend for {branding.app_name()}",
    version="1.0.0"
)

# Custom CORS middleware — handles file:// (Origin: null) and all other origins
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

class PermissiveCORSMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        # CORS_ALLOWED_ORIGINS unset or '*' answers '*' exactly as before; a list echoes only listed origins
        # (plus the file:// 'null' origin) and leaves every other origin without CORS headers.
        allow_origin = branding.cors_allow_origin(request.headers.get("origin"))
        if request.method == "OPTIONS":
            response = StarletteResponse(status_code=204)
            if allow_origin:
                response.headers["Access-Control-Allow-Origin"] = allow_origin
                response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
                response.headers["Access-Control-Allow-Headers"] = "Content-Type, x-admin-id, x-role-id, x-operator-key, Authorization"
                response.headers["Access-Control-Max-Age"] = "86400"
                if allow_origin != "*":
                    response.headers["Vary"] = "Origin"
            return response
        response = await call_next(request)
        if not allow_origin:
            return response
        if allow_origin != "*":
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, x-admin-id, x-role-id, x-operator-key, Authorization"
        return response

app.add_middleware(PermissiveCORSMiddleware)

# Global 500 handler (no internal text in the response, traceback + request id in the log) and X-Request-ID.
from utils.errors import install as _install_error_handling
_install_error_handling(app)

# Bearer-token authentication (WP12): ONE app-level dependency, installed BEFORE the routers below so every route gets
# it. REQUIRE_AUTH is off by default (requests without a token pass exactly as before); see core_functions/auth_policy.py.
from core_functions.auth_dependency import install as _install_auth
_install_auth(app)


# Include routers
from router import health
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(home.router)
app.include_router(add_staff.router)
app.include_router(branch.router)
app.include_router(company.router)
app.include_router(salary.router)
app.include_router(leads.router)
app.include_router(manager.router)
app.include_router(shift.router)
app.include_router(employee.router)
for _wages_router in wages.routers:
    app.include_router(_wages_router)
app.include_router(incentive.router)
app.include_router(face_attendance.router)
app.include_router(emp_expenses.router)
app.include_router(requests.router)
app.include_router(staff_location.router)
app.include_router(chat.router)
app.include_router(announcement.router)
app.include_router(report.router)
app.include_router(department.router)
app.include_router(gen_orders.router)
app.include_router(gen_payments.router)
app.include_router(gen_plans.router)
app.include_router(referral.router)
app.include_router(gen_webhook.router)
app.include_router(advance.router)
app.include_router(email.router)
app.include_router(profile_photo.router)
app.include_router(holiday.router)
app.include_router(gen_dashboard.router)
app.include_router(version.router)
app.include_router(ws_badge.router)
app.include_router(support.router)
app.include_router(live_chat.router)
app.include_router(gen_live_chat.router)
app.include_router(attribution.router)
app.include_router(analytics.router)
app.include_router(attendance_rule.router)
app.include_router(salary_payments.router)
app.include_router(lifecycle.router)


@app.on_event("startup")
def _validate_auth_config():
    """REQUIRE_AUTH=1 without a usable AUTH_TOKEN_SECRET (>= 32 characters) refuses to start (raises on purpose)."""
    from utils.auth_token import validate_auth_config
    validate_auth_config()


@app.on_event("startup")
def _run_migrations_if_enabled():
    """RUN_MIGRATIONS=1 applies pending migrations (migrate.py). Never fatal: an error is logged and startup goes on."""
    try:
        from migrate import run_startup_migrations
        run_startup_migrations()
    except Exception:
        import logging
        logging.getLogger("migrate").exception("migration start-up hook failed")


@app.on_event("startup")
def _warm_face_model():
    """Warm the face model in a background thread (first inference is slow). Never fatal."""
    import threading
    def _run():
        try:
            from ml.face import warmup_model
            warmup_model()
        except Exception as e:
            import logging
            logging.getLogger("face.metrics").warning("face warm-up skipped: %s", e)
    threading.Thread(target=_run, name="face-warmup", daemon=True).start()


@app.on_event("startup")
def _warn_missing_payment_webhook_secret():
    """Without RAZORPAY_WEBHOOK_SECRET every Razorpay webhook is answered 503 and gen_webhook_events stays empty."""
    from core_functions.gen_webhook_functions import webhook_secret_configured
    if not webhook_secret_configured():
        import logging
        logging.getLogger("payments").warning(
            "RAZORPAY_WEBHOOK_SECRET is not set: Razorpay webhooks will be rejected with 503 and nothing is stored in "
            "gen_webhook_events. Payments verified by the app still work, but a payment made while the app is closed is "
            "not activated. Set it to the secret entered in the Razorpay dashboard (Settings, Webhooks)."
        )


@app.on_event("startup")
def _log_user_auth_mode():
    """Log the sign-in mode and warn when e-mail sign-in has no sender configured."""
    import logging
    import os
    from utils.auth_mode import get_user_auth_mode
    log = logging.getLogger("auth")
    mode = get_user_auth_mode()
    log.info("USER_AUTH=%s", mode)
    if mode == "email" and not os.getenv("EMAIL_USER"):
        log.warning("USER_AUTH=email but EMAIL_USER (the SES-verified sender address) is not set - login e-mails will fail")
    from core_functions.auth_function import _otp_bypass_enabled
    if _otp_bypass_enabled():
        log.warning("OTP_BYPASS is enabled: the hard-coded test numbers accept the static code 123456 (OTP mode only). Do not enable this in production.")


@app.get("/", tags=["Root"])
def home():
    return {"message": f"Welcome to {branding.app_name()} API"}


if __name__ == "__main__":
    import os
    import logging
    import sys
    
    # Configure logging to show INFO level
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s:     %(name)s:%(message)s'
    )
    
    # Suppress only watchfiles warnings
    logging.getLogger("watchfiles.main").setLevel(logging.ERROR)
    logging.getLogger("watchfiles").setLevel(logging.ERROR)
    
    # Set environment to ignore .venv
    os.environ["WATCHFILES_IGNORE_PATHS"] = ".venv;venv;env"
    
    try:
        uvicorn.run(
            "app:app",
            host="0.0.0.0",
            port=8080,
            reload=True,
            log_level="info"
        )
    except KeyboardInterrupt:
        sys.exit(0)

 