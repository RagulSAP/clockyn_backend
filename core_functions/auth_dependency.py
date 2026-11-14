"""Bearer-token authentication for the HTTP API and the WebSocket handshakes.

ONE global mechanism instead of an edit on every route: `install(app)` (called from app.py before the routers are
included) appends `auth_guard` to the application-level dependencies, so FastAPI runs it for every route after
routing (path parameter names are known) and before the handler. It

  1. classifies the request with `core_functions/auth_policy.py` (public / operator / authenticated),
  2. parses `Authorization: Bearer <token>` (HS256, `utils/auth_token.py`) and checks it against the user's current
     database state (auth epoch, active status) through a small in-process cache,
  3. stores the caller on `request.state.identity` (None when there is no valid token),
  4. with REQUIRE_AUTH=1 answers 401 (no/invalid/expired/revoked token), 403 (role, manager permission, or an id of
     another organisation / another user), otherwise lets the request through exactly as before.

The guard never reads a request body it does not need: JSON bodies are inspected only when the framework already read
them (declared body) or when `Content-Length` is small; Starlette caches the bytes, so the handler still reads them.
WebSocket routes skip the guard (the handler calls `authenticate_websocket`, see router/chat.py and router/ws_badge.py).

Revocation: users.auth_epoch (migration 033). A token is valid only while its `ep` equals the user's current epoch and
the user is active (status 1). The epoch is bumped when a device takeover / device-change approval signs a single-device
user (roles 2, 3) out and when such a user logs out; admins are multi-device and are never revoked by a login or logout.
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Optional, Tuple

from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request

from core_functions import auth_policy as policy
from core_functions.auth_policy import Identity
from utils import auth_token as tokens

logger = logging.getLogger("auth")

DEFAULT_CACHE_SECONDS = 30.0
ORG_REFRESH_SECONDS = 5.0          # an admin's organisation list is re-read at most this often when an id is not in it
_clock: Callable[[], float] = time.monotonic   # tests replace it


def cache_seconds() -> float:
    raw = (os.getenv("AUTH_EPOCH_CACHE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_CACHE_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_CACHE_SECONDS


# ============================================================================================ cache
class _TTLCache:
    """key -> (value, loaded_at, expires_at); thread-safe; a missing or expired entry is a miss (None stored = a value)."""

    def __init__(self):
        self._data: Dict[object, Tuple[object, float, float]] = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry[2] <= _clock():
                self._data.pop(key, None)
                return None
            return entry

    def put(self, key, value):
        ttl = cache_seconds()
        if ttl <= 0:
            return
        now = _clock()
        with self._lock:
            if len(self._data) > 20000:       # bound the memory (ids come from request input): start over
                self._data.clear()
            self._data[key] = (value, now, now + ttl)

    def drop(self, key):
        with self._lock:
            self._data.pop(key, None)

    def drop_where(self, predicate):
        with self._lock:
            for key in [k for k in self._data if predicate(k)]:
                self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()


_user_cache = _TTLCache()         # user_id -> UserState | None
_entity_cache = _TTLCache()       # id -> organisation id | None
_perm_cache = _TTLCache()         # (user_id, org_id) -> frozenset of permission names


@dataclass(frozen=True)
class UserState:
    epoch: int
    status: Optional[int]
    role_id: Optional[int]
    org_id: str
    orgs: FrozenSet[str]          # organisations the user may use (admin: every org_info row it owns + its own org)


# ============================================================================================ database loaders
# Column / table names checked against clockyn_ddl_latest.sql: users(user_id, org_id, parent_id, role_id, status,
# auth_epoch), org_info(id, org_id, parent_id), manager_permissions(user_id, org_id, setting_id, status),
# settings(setting_id, setting_name).
_USER_STATE_SQL = (
    "SELECT COALESCE(u.auth_epoch, 0) AS auth_epoch, u.status AS status, u.role_id AS role_id, "
    "CASE WHEN u.role_id = 1 THEN COALESCE(u.org_id, "
    "(SELECT o.org_id FROM org_info o WHERE o.parent_id = u.user_id ORDER BY o.id LIMIT 1)) "
    "WHEN u.manager_id IS NOT NULL AND u.parent_id = u.manager_id THEN "
    "(SELECT COALESCE(m.parent_id, m.org_id) FROM users m WHERE m.user_id = u.manager_id) "
    "ELSE u.parent_id END AS org_id "
    "FROM users u WHERE u.user_id = %s"
)
_OWNED_ORGS_SQL = "SELECT org_id FROM org_info WHERE parent_id = %s"
_ENTITY_USER_SQL = (
    "SELECT CASE WHEN u.role_id = 1 THEN COALESCE(u.org_id, "
    "(SELECT o.org_id FROM org_info o WHERE o.parent_id = u.user_id ORDER BY o.id LIMIT 1)) "
    "WHEN u.manager_id IS NOT NULL AND u.parent_id = u.manager_id THEN "
    "(SELECT COALESCE(m.parent_id, m.org_id) FROM users m WHERE m.user_id = u.manager_id) "
    "ELSE u.parent_id END AS org_id "
    "FROM users u WHERE u.user_id = %s"
)
_ENTITY_ORG_SQL = "SELECT org_id FROM org_info WHERE org_id = %s"
_PERMISSIONS_SQL = (
    "SELECT ps.setting_name AS setting_name FROM manager_permissions mp "
    "JOIN settings ps ON mp.setting_id = ps.setting_id "
    "WHERE mp.user_id = %s AND mp.org_id = %s AND mp.status = 1"
)


def _value(row, key: str, index: int):
    """Column of a fetched row for both RealDictCursor (by key) and the plain cursor (by position)."""
    if row is None:
        return None
    if isinstance(row, dict) or hasattr(row, "keys"):
        return row[key]
    return row[index]


def _db_query(fn):
    """Run `fn(cursor)` on a pooled connection and give the connection back."""
    from psycopg2.extras import RealDictCursor
    from core_functions.db_function import get_db_connection, return_connection
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            return fn(cursor)
        finally:
            cursor.close()
    finally:
        return_connection(conn)


def load_user_state(user_id: str) -> Optional[UserState]:
    """Current epoch / status / role / organisations of a user (None: no such user)."""
    def run(cursor):
        cursor.execute(_USER_STATE_SQL, (user_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        role = row["role_id"]
        org = row["org_id"] or ""
        orgs = {org} if org else set()
        if role == policy.ROLE_ADMIN:
            cursor.execute(_OWNED_ORGS_SQL, (user_id,))
            orgs.update(r["org_id"] for r in cursor.fetchall() if r["org_id"])
        return UserState(int(row["auth_epoch"] or 0), row["status"], role, org, frozenset(orgs))
    return _db_query(run)


def load_entity_org(entity_id: str):
    """Organisation that owns an id used as user_id / staff_id / employee_id / receiver_id: the user's organisation (a
    frozenset of every organisation for an admin who owns several), or the id itself when it is an organisation id.
    None when the id exists nowhere."""
    def run(cursor):
        cursor.execute(_ENTITY_USER_SQL, (entity_id,))
        row = cursor.fetchone()
        if row is not None:
            org = row["org_id"] or ""
            cursor.execute(_OWNED_ORGS_SQL, (entity_id,))
            owned = {r["org_id"] for r in cursor.fetchall() if r["org_id"]}
            return frozenset(owned | {org}) if owned else org
        cursor.execute(_ENTITY_ORG_SQL, (entity_id,))
        row = cursor.fetchone()
        return row["org_id"] if row is not None else None
    return _db_query(run)


def load_manager_permissions(user_id: str, org_id: str) -> FrozenSet[str]:
    def run(cursor):
        cursor.execute(_PERMISSIONS_SQL, (user_id, org_id))
        return frozenset(r["setting_name"] for r in cursor.fetchall())
    return _db_query(run)


_loaders = {"user_state": load_user_state, "entity_org": load_entity_org, "permissions": load_manager_permissions}


def set_loaders(**loaders) -> None:
    """Replace database loaders (tests inject fakes): user_state(user_id), entity_org(id), permissions(user_id, org_id)."""
    for name, fn in loaders.items():
        if name not in _loaders:
            raise KeyError(name)
        _loaders[name] = fn


def reset_state() -> None:
    """Default loaders and empty caches (tests)."""
    _loaders.update(user_state=load_user_state, entity_org=load_entity_org, permissions=load_manager_permissions)
    _user_cache.clear()
    _entity_cache.clear()
    _perm_cache.clear()


class AuthBackendError(Exception):
    """A lookup failed (database unreachable): never treated as 'allowed'."""


async def _load(name: str, *args):
    try:
        return await run_in_threadpool(_loaders[name], *args)
    except Exception as exc:
        logger.warning("auth lookup %s failed: %s", name, exc.__class__.__name__)
        raise AuthBackendError(name) from exc


async def get_user_state(user_id: str, *, force: bool = False) -> Optional[UserState]:
    if not force:
        entry = _user_cache.get(user_id)
        if entry is not None:
            return entry[0]
    state = await _load("user_state", user_id)
    _user_cache.put(user_id, state)
    return state


async def admin_orgs(identity: Identity, wanted: FrozenSet[str]) -> FrozenSet[str]:
    """Organisations the caller may use. An admin may own several; when an id is not in the cached list the list is
    re-read (at most every ORG_REFRESH_SECONDS), so a company the admin has just added works without waiting."""
    base = frozenset({identity.org_id}) if identity.org_id else frozenset()
    if identity.role_id != policy.ROLE_ADMIN:
        return base
    entry = _user_cache.get(identity.user_id)
    state = entry[0] if entry else None
    if state is None:
        state = await get_user_state(identity.user_id)
        entry = _user_cache.get(identity.user_id)
    orgs = (state.orgs if state else frozenset()) | base
    if wanted - orgs and entry is not None and _clock() - entry[1] >= ORG_REFRESH_SECONDS:
        state = await get_user_state(identity.user_id, force=True)
        orgs = (state.orgs if state else frozenset()) | base
    return orgs


async def get_entity_org(entity_id: str) -> Optional[str]:
    entry = _entity_cache.get(entity_id)
    if entry is not None:
        return entry[0]
    owner = await _load("entity_org", entity_id)
    _entity_cache.put(entity_id, owner)
    return owner


async def get_manager_permissions(user_id: str, org_id: str) -> FrozenSet[str]:
    key = (user_id, org_id)
    entry = _perm_cache.get(key)
    if entry is not None:
        return entry[0]
    names = await _load("permissions", user_id, org_id)
    _perm_cache.put(key, names)
    return names


def forget_user(user_id: str) -> None:
    """Drop every cached fact about a user (call right after bumping or changing the user in this process)."""
    _user_cache.drop(user_id)
    _perm_cache.drop_where(lambda key: key[0] == user_id)


# ============================================================================================ epoch (write side)
_BUMP_SQL = "UPDATE users SET auth_epoch = auth_epoch + 1 WHERE user_id = %s AND role_id IN (2, 3)"
_TAKEOVER_SQL = ("UPDATE users SET auth_epoch = auth_epoch + 1 WHERE user_id = %s AND role_id IN (2, 3) "
                 "AND device_token IS NOT NULL AND device_token IS DISTINCT FROM %s")


def _guarded_update(cursor, savepoint: str, sql: str, params: tuple) -> bool:
    """Run an UPDATE of users.auth_epoch inside a savepoint, so a database without the column (migration 033 not applied)
    never breaks the surrounding transaction. True when at least one row was updated."""
    try:
        cursor.execute("SAVEPOINT " + savepoint)
    except Exception:
        return False
    try:
        cursor.execute(sql, params)
        updated = (cursor.rowcount or 0) > 0
        cursor.execute("RELEASE SAVEPOINT " + savepoint)
        return updated
    except Exception as exc:
        logger.warning("auth_epoch update skipped: %s", exc.__class__.__name__)
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT " + savepoint)
        except Exception:
            pass
        return False


def bump_epoch(cursor, user_id: str) -> bool:
    """Revoke every token of a single-device user (role 2 or 3; an admin is never touched). Runs in the caller's
    transaction (no commit here); the local cache entry is dropped now and the caller should call `forget_user`
    again after its commit. No-op (False) while tokens are not configured: nothing can be issued then."""
    if not tokens.tokens_enabled() or not user_id:
        return False
    updated = _guarded_update(cursor, "auth_epoch_bump", _BUMP_SQL, (user_id,))
    forget_user(user_id)
    return updated


def bump_epoch_on_takeover(cursor, user_id: str, new_device_token: str) -> bool:
    """Same as bump_epoch, only when the user holds a DIFFERENT device token (a login on another device takes over)."""
    if not tokens.tokens_enabled() or not user_id:
        return False
    updated = _guarded_update(cursor, "auth_epoch_takeover", _TAKEOVER_SQL, (user_id, new_device_token))
    forget_user(user_id)
    return updated


def issue_for_user(cursor, user_id: str) -> dict:
    """{"access_token": ..., "expires_in": seconds} for the user's current epoch, or {} when tokens are not configured
    or the user cannot be read. Never raises. Call it AFTER any epoch bump of the same login."""
    if not tokens.tokens_enabled() or not user_id:
        return {}
    try:
        cursor.execute("SAVEPOINT auth_token_issue")
    except Exception:
        return {}
    try:
        cursor.execute(_USER_STATE_SQL, (user_id,))
        row = cursor.fetchone()
        cursor.execute("RELEASE SAVEPOINT auth_token_issue")
        if row is None:
            return {}
        token, expires_in = tokens.issue_token(user_id, _value(row, "org_id", 3) or "", int(_value(row, "role_id", 2) or 0),
                                               int(_value(row, "auth_epoch", 0) or 0))
        return {"access_token": token, "expires_in": expires_in}
    except Exception as exc:
        logger.warning("access token not issued: %s", exc.__class__.__name__)
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT auth_token_issue")
        except Exception:
            pass
        return {}


# ============================================================================================ identity from a token
async def identify(token: str) -> Tuple[Optional[Identity], Optional[str]]:
    """(identity, None) for a good token, else (None, reason) with reason "expired", "invalid" or "revoked".
    Raises AuthBackendError when the user lookup itself fails."""
    try:
        claims = tokens.verify_token(token)
    except tokens.TokenError as exc:
        logger.debug("token rejected: %s", exc.reason)
        return None, exc.code
    state = await get_user_state(claims["sub"])
    if state is None or state.status != 1 or state.epoch != claims["ep"]:
        logger.debug("token revoked or user inactive: sub=%s", claims["sub"])
        return None, "revoked"
    role = state.role_id if state.role_id in policy.ALL_ROLES else claims["role"]
    return Identity(claims["sub"], state.org_id or claims["org"], int(role), state.epoch, claims["exp"]), None


# ============================================================================================ request helpers
def _request_path(request: HTTPConnection) -> str:
    path = request.scope.get("path", "/")
    root = request.scope.get("root_path", "") or ""
    if root and path.startswith(root + "/"):
        path = path[len(root):]
    return path


async def _peek_json(request: Request):
    """Top-level JSON body, only when it costs nothing: already read by the framework, or small by Content-Length.
    The bytes stay cached on the request, so the handler reads them again unchanged."""
    ctype = (request.headers.get("content-type") or "").lower()
    # No Content-Type at all: FastAPI still parses a declared body as JSON, so the guard must look at it as well.
    if ctype and "json" not in ctype:
        return None
    cached = getattr(request, "_body", None)
    if cached is None:
        try:
            length = int(request.headers.get("content-length", ""))
        except ValueError:
            return None
        if length > policy.MAX_PEEK_BODY_BYTES:
            return None
        cached = await request.body()
    try:
        return json.loads(cached) if cached else None
    except ValueError:
        return None


def _http_error(status: int, detail: str) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return HTTPException(status_code=status, detail=detail, headers=headers)


def _log_denied(identity: Identity, method: str, path: str, why: str, key: str = "") -> None:
    logger.warning("auth denied: actor=%s role=%s %s %s reason=%s%s", identity.user_id, identity.role_id, method, path, why,
                   (" key=" + key) if key else "")


async def auth_guard(conn: HTTPConnection) -> None:
    """Application-level dependency: see the module docstring."""
    if conn.scope.get("type") != "http":
        return                                                   # WebSockets: authenticate_websocket in the handler
    request = conn
    method, path = request.method.upper(), _request_path(request)
    required = tokens.require_auth_enabled()
    kind = policy.classify(method, path)
    request.state.identity = None
    request.state.allowed_orgs = frozenset()

    identity, failure = None, None
    token = tokens.bearer_from_header(request.headers.get("authorization"))
    if token is not None:
        if not tokens.tokens_enabled():
            logger.debug("bearer token ignored: AUTH_TOKEN_SECRET is not configured")
        else:
            try:
                identity, failure = await identify(token)
            except AuthBackendError:
                if required and kind == policy.AUTHENTICATED:
                    raise _http_error(503, "Authentication temporarily unavailable")
                identity, failure = None, "invalid"
    if identity is not None:
        request.state.identity = identity
        request.state.allowed_orgs = await _safe_orgs(identity)
    elif token is not None and not required:
        logger.debug("invalid bearer token ignored (REQUIRE_AUTH is off): %s", failure)

    if kind == policy.PUBLIC or not required:
        return
    if kind == policy.OPERATOR:
        if identity is not None:
            _log_denied(identity, method, path, "operator route")
            raise _http_error(403, "Forbidden")
        return                                                   # the operator mechanism of the handler decides
    # ---- REQUIRE_AUTH=1, route needs an app user
    if identity is None:
        raise _http_error(401, "Token expired" if failure == "expired" else "Not authenticated")

    needs = policy.needs_manager_permission(method, path) if identity.role_id == policy.ROLE_MANAGER else ()
    permissions: FrozenSet[str] = frozenset()
    if needs:
        try:
            permissions = await get_manager_permissions(identity.user_id, identity.org_id)
        except AuthBackendError:
            raise _http_error(503, "Authentication temporarily unavailable")
    decision = policy.evaluate(method, path, identity, lambda name: name in permissions)
    if not decision.allowed:
        _log_denied(identity, method, path, decision.reason)
        if decision.needs:
            raise _http_error(403, "Access denied. Required permission: " + " or ".join(decision.needs))
        raise _http_error(decision.status, "Forbidden")

    form = getattr(request, "_form", None)
    body = await _peek_json(request) if method in policy.NON_GET else None
    ids = policy.collect_ids(request.path_params, request.query_params, body, form)
    if not ids:
        return
    wanted = frozenset(value for _s, _k, value in ids)
    try:
        orgs = await admin_orgs(identity, wanted)
        entity_orgs = {value: await get_entity_org(value)
                       for value in policy.ids_to_resolve(identity, ids, orgs, method, path)}
    except AuthBackendError:
        raise _http_error(503, "Authentication temporarily unavailable")
    request.state.allowed_orgs = orgs
    bad = policy.ownership_violation(identity, ids, orgs, entity_orgs, method, path)
    if bad is not None:
        _log_denied(identity, method, path, "id of another organisation or user", "%s:%s" % bad)
        raise _http_error(403, "Forbidden")


async def _safe_orgs(identity: Identity) -> FrozenSet[str]:
    try:
        return await admin_orgs(identity, frozenset())
    except AuthBackendError:
        return frozenset({identity.org_id}) if identity.org_id else frozenset()


def install(app) -> None:
    """Make `auth_guard` run for every route that is added AFTER this call (call it before include_router)."""
    app.router.dependencies.append(Depends(auth_guard))


# ============================================================================================ handler helpers
def current_identity(request: HTTPConnection) -> Optional[Identity]:
    """FastAPI dependency (optional): the caller when a valid token was sent, else None."""
    return getattr(request.state, "identity", None)


def require_identity(request: HTTPConnection) -> Identity:
    """FastAPI dependency (mandatory): 401 without a valid token, even while REQUIRE_AUTH is off."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        raise _http_error(401, "Not authenticated")
    return identity


def org_of(request: HTTPConnection) -> Optional[str]:
    """Organisation of the caller (None without a valid token)."""
    identity = getattr(request.state, "identity", None)
    return identity.org_id if identity and identity.org_id else None


def caller_orgs(request: HTTPConnection) -> Optional[FrozenSet[str]]:
    """Organisations the signed-in caller may use, or None when no valid token identifies the caller (REQUIRE_AUTH off and
    no token: nothing to compare with). For handlers that load a resource by its own id (no org id in the request) and
    must answer 404 when that resource belongs to another organisation."""
    identity = getattr(request.state, "identity", None)
    if identity is None:
        return None
    own = getattr(identity, "org_id", None)
    allowed = getattr(request.state, "allowed_orgs", None) or frozenset({own} if own else ())
    return frozenset(allowed)


def assert_same_org(request: HTTPConnection, org_id: Optional[str]) -> None:
    """403 when a token is present and `org_id` is not one of the caller's organisations. No token: no check (the
    global guard already answered 401 when REQUIRE_AUTH is on)."""
    identity = getattr(request.state, "identity", None)
    if identity is None or org_id is None:
        return
    allowed = getattr(request.state, "allowed_orgs", None) or frozenset({identity.org_id})
    if org_id not in allowed and not (identity.role_id == policy.ROLE_MANAGER and org_id == identity.user_id):
        _log_denied(identity, request.method, _request_path(request), "org mismatch", "org_id")
        raise _http_error(403, "Forbidden")


def assert_same_user(request: HTTPConnection, user_id: Optional[str]) -> None:
    """403 when a staff member (role 3) names a user other than themselves."""
    identity = getattr(request.state, "identity", None)
    if identity is None or user_id is None:
        return
    if identity.role_id == policy.ROLE_STAFF and user_id != identity.user_id:
        _log_denied(identity, request.method, _request_path(request), "user mismatch", "user_id")
        raise _http_error(403, "Forbidden")


# ============================================================================================ WebSocket handshake
WS_CLOSE_UNAUTHORIZED = 4401


async def authenticate_websocket(websocket, path_user_id: str) -> bool:
    """Handshake check for /ws/chat/{user_id}, /ws/group-chat/{group_id}/{user_id} and /ws/badge/{user_id}.

    REQUIRE_AUTH off: always True (as before). REQUIRE_AUTH on: the query parameter `token` must be a valid token of
    the user named in the path; otherwise the handshake is refused with close code 4401 and False is returned (the
    handler must return without accepting)."""
    if not tokens.require_auth_enabled():
        return True
    token = websocket.query_params.get("token")
    identity, reason = None, "missing"
    if token and tokens.tokens_enabled():
        try:
            identity, reason = await identify(token)
        except AuthBackendError:
            identity, reason = None, "unavailable"
    if identity is None or identity.user_id != str(path_user_id):
        logger.warning("websocket refused: %s", reason if identity is None else "token user differs from path user")
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return False
    websocket.state.identity = identity
    return True
