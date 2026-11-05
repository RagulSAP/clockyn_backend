"""Request-id middleware and the global handler for unexpected exceptions.

* RequestIdMiddleware (pure ASGI): reuses an incoming X-Request-ID (when it is a safe token) or creates one, keeps it
  in `scope["state"]["request_id"]` and a ContextVar, and echoes it in the X-Request-ID response header.
* unhandled_exception_handler: any exception that no other handler claimed becomes
  HTTP 500 {"detail": "Internal server error"} (no internal text) and is logged with its traceback and the request id.
  HTTPException and request-validation errors keep FastAPI's own handlers (and shapes).
* install(app): adds both to a FastAPI app. app.py calls it once; tests call it on a small app.
"""
import logging
import re
import uuid
from contextvars import ContextVar

from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"
_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("latin-1")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")

request_id_var: ContextVar = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex


def clean_request_id(value):
    """The incoming id when it is 1-64 chars of letters, digits, `.`, `_`, `-`; otherwise a fresh id."""
    if isinstance(value, bytes):
        value = value.decode("latin-1", "replace")
    if isinstance(value, str) and _SAFE_ID_RE.match(value):
        return value
    return new_request_id()


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = None
        for name, value in scope.get("headers", []):
            if name == _HEADER_BYTES:
                incoming = value
                break
        rid = clean_request_id(incoming)
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != _HEADER_BYTES]
                headers.append((_HEADER_BYTES, rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def _request_id_of(request) -> str:
    return getattr(request.state, "request_id", None) or request_id_var.get()


async def unhandled_exception_handler(request, exc):
    rid = _request_id_of(request)
    logger.error("Unhandled exception request_id=%s %s %s", rid, request.method, request.url.path,
                 exc_info=(type(exc), exc, exc.__traceback__))
    # This response is built by Starlette's outermost layer, so the middleware cannot add the header: set it here.
    return JSONResponse(status_code=500, content={"detail": "Internal server error"}, headers={REQUEST_ID_HEADER: rid})


def install(app):
    """Register the catch-all handler and the request-id middleware (outermost) on `app`."""
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.add_middleware(RequestIdMiddleware)
    return app
