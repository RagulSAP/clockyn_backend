"""Push notifications: the single FCM v1 sender used by every feature.

* Credentials come ONLY from the environment (read on every call): FCM_PROJECT_ID, FCM_PRIVATE_KEY_ID,
  FCM_PRIVATE_KEY (literal "\\n" sequences are unescaped), FCM_CLIENT_EMAIL, FCM_CLIENT_ID; for local development
  FCM_SERVICE_ACCOUNT_PATH (a service-account JSON file) is the fallback. FCM_ANDROID_CHANNEL_ID names the Android
  notification channel (default "attendance_notifications", the channel the app creates).
* The OAuth access token is cached for 55 minutes.
* Never raises: an unconfigured sender logs once and returns False; every failure is logged and returns False.
* A token that FCM reports as dead (UNREGISTERED / NOT_FOUND / INVALID_ARGUMENT about the token) is removed from
  `user_push_tokens` and from `users.push_token`.
* A user can have several devices: `user_push_tokens` holds every token, `users.push_token` stays as the legacy single
  token (used when the table has no row for the user).

Tests monkeypatch `_mint_access_token` (credentials -> token) and `_post_fcm` (the HTTP POST).
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOKEN_TTL_SECONDS = 3300  # Google tokens live 60 minutes; refresh after 55
DEFAULT_ANDROID_CHANNEL = "attendance_notifications"
DEFAULT_SERVICE_ACCOUNT_FILE = "firebase-service-account.json"
MAX_PARALLEL_SENDS = 10
HTTP_TIMEOUT_SECONDS = 15.0

# OAuth token cache: {"token", "project_id", "expires_at" (time.monotonic), "fingerprint"}
_token_cache: Dict[str, Any] = {"token": None, "project_id": None, "expires_at": 0.0, "fingerprint": None}
_unconfigured_logged = False


# ---------------------------------------------------------------------------- credentials
def _unescape_key(raw: str) -> str:
    key = (raw or "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
        key = key[1:-1]
    return key.replace("\\n", "\n")


def get_fcm_credentials() -> Optional[Dict[str, Any]]:
    """Service-account description from the environment, or None when FCM is not configured.

    Returns {"project_id", "source": "env"|"file", "info": dict | None, "path": str | None}.
    """
    project_id = os.getenv("FCM_PROJECT_ID", "").strip()
    private_key = _unescape_key(os.getenv("FCM_PRIVATE_KEY", ""))
    client_email = os.getenv("FCM_CLIENT_EMAIL", "").strip()
    if project_id and private_key and client_email:
        info = {
            "type": "service_account",
            "project_id": project_id,
            "private_key_id": os.getenv("FCM_PRIVATE_KEY_ID", "").strip(),
            "private_key": private_key,
            "client_email": client_email,
            "client_id": os.getenv("FCM_CLIENT_ID", "").strip(),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/" + client_email.replace("@", "%40"),
            "universe_domain": "googleapis.com",
        }
        return {"project_id": project_id, "source": "env", "info": info, "path": None}

    path = os.getenv("FCM_SERVICE_ACCOUNT_PATH", "").strip() or DEFAULT_SERVICE_ACCOUNT_FILE
    if os.path.isfile(path):
        file_project = project_id
        if not file_project:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    file_project = str(json.load(fh).get("project_id") or "").strip()
            except Exception:
                file_project = ""
        if file_project:
            return {"project_id": file_project, "source": "file", "info": None, "path": path}
    return None


def is_configured() -> bool:
    return get_fcm_credentials() is not None


def _log_unconfigured_once() -> None:
    global _unconfigured_logged
    if not _unconfigured_logged:
        _unconfigured_logged = True
        logger.warning("[FCM] not configured (set FCM_PROJECT_ID, FCM_PRIVATE_KEY, FCM_CLIENT_EMAIL, ...): push is disabled")


def _fingerprint(creds: Dict[str, Any]) -> str:
    info = creds.get("info") or {}
    return "|".join([creds["project_id"], creds["source"], str(info.get("private_key_id", "")), str(creds.get("path") or "")])


def _mint_access_token(creds: Dict[str, Any]) -> str:
    """Blocking: exchange the service account for an OAuth access token (monkeypatched in tests)."""
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GoogleRequest

    if creds["source"] == "env":
        credentials = service_account.Credentials.from_service_account_info(creds["info"], scopes=[FCM_SCOPE])
    else:
        credentials = service_account.Credentials.from_service_account_file(creds["path"], scopes=[FCM_SCOPE])
    credentials.refresh(GoogleRequest())
    return credentials.token


def invalidate_access_token() -> None:
    _token_cache.update({"token": None, "project_id": None, "expires_at": 0.0, "fingerprint": None})


def get_fcm_access_token() -> Tuple[Optional[str], Optional[str]]:
    """(access_token, project_id) or (None, None). Cached for 55 minutes; never raises."""
    creds = get_fcm_credentials()
    if creds is None:
        _log_unconfigured_once()
        return None, None
    fingerprint = _fingerprint(creds)
    now = time.monotonic()
    if _token_cache["token"] and _token_cache["fingerprint"] == fingerprint and now < _token_cache["expires_at"]:
        return _token_cache["token"], _token_cache["project_id"]
    try:
        token = _mint_access_token(creds)
    except Exception as e:
        logger.error("[FCM] could not obtain an access token: %s", e)
        return None, None
    if not token:
        return None, None
    _token_cache.update({
        "token": token, "project_id": creds["project_id"],
        "expires_at": now + TOKEN_TTL_SECONDS, "fingerprint": fingerprint,
    })
    logger.info("[FCM] access token refreshed and cached")
    return token, creds["project_id"]


# ---------------------------------------------------------------------------- message building / error classification
def is_expo_token(token: str) -> bool:
    return str(token or "").startswith(("ExponentPushToken[", "ExpoPushToken["))


def stringify_data(data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """FCM data values must be strings (None becomes '')."""
    return {str(k): ("" if v is None else str(v)) for k, v in (data or {}).items()}


# Formats the Android notification shade can draw as the expanded picture (HEIC / PDF / other files cannot be shown).
PUSH_IMAGE_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif")
PHOTO_PUSH_TEXT = "\U0001f4f7 Sent a photo"


def push_image_for(content_type: Optional[str], url: Optional[str]) -> Optional[str]:
    """The link to show as the notification picture (collapsed: thumbnail, expanded: the full photo), or None when the
    attachment is not a picture the phone can draw. The link is a presigned S3 URL (valid 7 days), so a device that is
    offline for a while can still load it when it comes back."""
    if not url or not isinstance(url, str) or not url.startswith("https://"):
        return None
    kind = (content_type or "").split(";")[0].strip().lower()
    return url if kind in PUSH_IMAGE_TYPES else None


def build_fcm_message(token: str, title: str, body: str, data: Optional[Dict[str, Any]] = None,
                      image_url: Optional[str] = None, silent: bool = False) -> Dict[str, Any]:
    """FCM v1 `message` object. `silent` = data-only message (no visible notification)."""
    str_data = stringify_data(data)
    if silent:
        return {
            "token": token,
            "data": str_data,
            "android": {"priority": "high"},
            "apns": {
                "headers": {"apns-push-type": "background", "apns-priority": "5"},
                "payload": {"aps": {"content-available": 1}},
            },
        }
    channel = os.getenv("FCM_ANDROID_CHANNEL_ID", "").strip() or DEFAULT_ANDROID_CHANNEL
    message: Dict[str, Any] = {
        "token": token,
        "notification": {"title": title, "body": body},
        "data": str_data,
        "android": {"notification": {"sound": "default", "channel_id": channel}, "priority": "high"},
        "apns": {"payload": {"aps": {"sound": "default"}}},
    }
    if image_url:
        message["notification"]["image"] = image_url
        message["android"]["notification"]["image"] = image_url
    return message


def parse_fcm_error(body: Any) -> Dict[str, Any]:
    """{"status": str, "codes": set[str], "message": str, "fields": list[str]} from an FCM error body ('' when unparsable)."""
    out: Dict[str, Any] = {"status": "", "codes": set(), "message": "", "fields": []}
    try:
        parsed = json.loads(body) if isinstance(body, (str, bytes)) else body
        err = (parsed or {}).get("error") or {}
    except Exception:
        return out
    if not isinstance(err, dict):
        return out
    out["status"] = str(err.get("status") or "")
    out["message"] = str(err.get("message") or "")
    if out["status"]:
        out["codes"].add(out["status"])
    for detail in err.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if detail.get("errorCode"):
            out["codes"].add(str(detail["errorCode"]))
        for violation in detail.get("fieldViolations") or []:
            if isinstance(violation, dict) and violation.get("field"):
                out["fields"].append(str(violation["field"]))
    return out


def is_stale_token_error(http_status: int, body: Any) -> bool:
    """True when FCM says the *token* is dead and should be pruned.

    UNREGISTERED and NOT_FOUND always mean that. INVALID_ARGUMENT also covers malformed payloads, so it only counts
    when the error is about the token (message or field name mentions it): a payload bug must never wipe tokens.
    """
    if http_status == 200:
        return False
    err = parse_fcm_error(body)
    codes = err["codes"]
    if "UNREGISTERED" in codes or "NOT_FOUND" in codes:
        return True
    if "INVALID_ARGUMENT" in codes:
        text = err["message"].lower()
        return "token" in text or any("token" in f.lower() for f in err["fields"])
    return False


async def _post_fcm(project_id: str, access_token: str, message: Dict[str, Any]) -> Tuple[int, str]:
    """The only HTTP call to FCM (monkeypatched in tests). Returns (status_code, body_text)."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"message": message},
        )
        return resp.status_code, resp.text


# ---------------------------------------------------------------------------- token storage
def upsert_user_token(cursor, user_id: str, token: str, platform: Optional[str] = None,
                      device_id: Optional[str] = None, replace_others: bool = False) -> bool:
    """Register `token` for the user in `user_push_tokens`, on the caller's cursor / transaction.

    Runs inside a SAVEPOINT: if the table is missing (migration 027 not applied) or any statement fails, the
    savepoint is rolled back and the caller's transaction (login, token update) is untouched. Returns success.
      * the same token under any other user is removed (a token belongs to the device's current user);
      * same user + same device_id with another token = a rotated token: the old row is removed;
      * replace_others=True (single-device roles after a takeover login) removes every other token of the user.
    """
    token = (token or "").strip()
    if not user_id or not token:
        return False
    if platform is None and is_expo_token(token):
        platform = "expo"
    platform = platform[:16] if platform else None
    device_id = device_id[:64] if device_id else None
    try:
        cursor.execute("SAVEPOINT user_push_tokens_upsert")
        cursor.execute(
            """
            INSERT INTO user_push_tokens (user_id, token, platform, device_id, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (user_id, token) DO UPDATE
            SET platform = COALESCE(EXCLUDED.platform, user_push_tokens.platform),
                device_id = COALESCE(EXCLUDED.device_id, user_push_tokens.device_id),
                updated_at = now()
            """,
            (user_id, token, platform, device_id),
        )
        cursor.execute("DELETE FROM user_push_tokens WHERE token = %s AND user_id <> %s", (token, user_id))
        if device_id:
            cursor.execute(
                "DELETE FROM user_push_tokens WHERE user_id = %s AND device_id = %s AND token <> %s",
                (user_id, device_id, token),
            )
        if replace_others:
            cursor.execute("DELETE FROM user_push_tokens WHERE user_id = %s AND token <> %s", (user_id, token))
        cursor.execute("RELEASE SAVEPOINT user_push_tokens_upsert")
        return True
    except Exception as e:
        logger.warning("[PushToken] could not store the device token for user %s: %s", user_id, e)
        try:
            cursor.execute("ROLLBACK TO SAVEPOINT user_push_tokens_upsert")
        except Exception:
            pass
        return False


def get_user_tokens(user_id: str) -> List[str]:
    """Every push token of one user: the `user_push_tokens` rows, else the legacy `users.push_token`. Blocking."""
    return get_users_tokens([user_id]).get(str(user_id), [])


def get_users_tokens(user_ids: Iterable[str]) -> Dict[str, List[str]]:
    """{user_id: [tokens]} for users that have at least one token. Blocking (own connection)."""
    ids = tuple(dict.fromkeys(str(u) for u in user_ids if u))
    if not ids:
        return {}
    result: Dict[str, List[str]] = {}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "SELECT user_id, token FROM user_push_tokens WHERE user_id IN %s ORDER BY updated_at DESC",
                    (ids,),
                )
                for uid, tok in cur.fetchall():
                    if tok and tok not in result.setdefault(str(uid), []):
                        result[str(uid)].append(tok)
            except Exception as e:  # table not migrated yet: fall back to the legacy column only
                logger.warning("[PushToken] user_push_tokens unavailable (%s); using users.push_token", e)
                conn.rollback()
                result = {}
            missing = tuple(u for u in ids if u not in result)
            if missing:
                cur.execute(
                    "SELECT user_id, push_token FROM users WHERE user_id IN %s AND push_token IS NOT NULL AND push_token <> ''",
                    (missing,),
                )
                for uid, tok in cur.fetchall():
                    result.setdefault(str(uid), []).append(tok)
    finally:
        return_connection(conn)
    return result


def prune_token(token: str) -> None:
    """Remove a dead token everywhere (blocking; own connection; never raises)."""
    if not token:
        return
    try:
        conn = get_db_connection()
    except Exception as e:
        logger.error("[Notify] could not prune a stale token: %s", e)
        return
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("DELETE FROM user_push_tokens WHERE token = %s", (token,))
            except Exception as e:
                logger.warning("[PushToken] user_push_tokens not pruned: %s", e)
                conn.rollback()
            cur.execute("UPDATE users SET push_token = NULL WHERE push_token = %s", (token,))
        conn.commit()
        logger.warning("[Notify] removed a stale push token (...%s)", token[-6:])
    except Exception as e:
        logger.error("[Notify] failed to prune a stale token: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        return_connection(conn)


def clear_user_tokens(user_id: str) -> None:
    """Forget every device of a user (device-change approval). Blocking; never raises."""
    if not user_id:
        return
    try:
        conn = get_db_connection()
    except Exception as e:
        logger.error("[PushToken] could not clear tokens: %s", e)
        return
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_push_tokens WHERE user_id = %s", (user_id,))
        conn.commit()
    except Exception as e:
        logger.warning("[PushToken] user_push_tokens not cleared: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------- senders
async def send_push(token: str, title: str, body: str, data: Optional[Dict[str, Any]] = None,
                    image_url: Optional[str] = None, silent: bool = False) -> bool:
    """Send one push to one raw FCM token. True when FCM accepted it. Never raises."""
    token = (token or "").strip() if isinstance(token, str) else ""
    if not token:
        logger.warning("[Notify] push token is empty - skipping")
        return False
    if is_expo_token(token):
        logger.info("[Notify] Expo push token skipped (FCM sender only)")
        return False
    try:
        loop = asyncio.get_running_loop()
        message = build_fcm_message(token, title, body, data, image_url, silent)
        for attempt in (1, 2):
            access_token, project_id = await loop.run_in_executor(None, get_fcm_access_token)
            if not access_token or not project_id:
                return False
            status, text = await _post_fcm(project_id, access_token, message)
            logger.info("[Notify][FCM] status=%s token=...%s", status, token[-4:])
            if status == 200:
                return True
            if status == 401 and attempt == 1:
                invalidate_access_token()  # expired / revoked access token: mint a new one and retry once
                continue
            if is_stale_token_error(status, text):
                await loop.run_in_executor(None, prune_token, token)
            else:
                logger.error("[Notify][FCM] failed: %s %s", status, text)
            return False
    except Exception as e:
        logger.error("[Notify] push send error: %s", e)
    return False


async def _send_many(tokens: List[str], title: str, body: str, data, image_url, silent) -> int:
    if not tokens:
        return 0
    gate = asyncio.Semaphore(MAX_PARALLEL_SENDS)

    async def one(tok: str) -> bool:
        async with gate:
            return await send_push(tok, title, body, data, image_url, silent)

    results = await asyncio.gather(*(one(t) for t in tokens), return_exceptions=True)
    return sum(1 for r in results if r is True)


async def send_push_to_user(user_id: str, title: str, body: str, data: Optional[Dict[str, Any]] = None,
                            image_url: Optional[str] = None, silent: bool = False) -> int:
    """Fan out to every device of one user. Returns how many tokens FCM accepted. Never raises."""
    return await send_push_to_users([user_id], title, body, data, image_url, silent)


async def send_push_to_users(user_ids: Iterable[str], title: str, body: str, data: Optional[Dict[str, Any]] = None,
                             image_url: Optional[str] = None, silent: bool = False) -> int:
    """Fan out to every device of every listed user (tokens de-duplicated). Never raises."""
    try:
        if not is_configured():
            _log_unconfigured_once()
            return 0
        loop = asyncio.get_running_loop()
        by_user = await loop.run_in_executor(None, get_users_tokens, list(user_ids))
        tokens: List[str] = []
        for toks in by_user.values():
            for tok in toks:
                if tok not in tokens:
                    tokens.append(tok)
        if not tokens:
            # The usual reason a push "does not arrive": the app never registered a token (permission denied, a build without
            # Firebase, or the user has not opened the app since signing in). Say so instead of failing silently.
            logger.warning("[Notify] no push token registered for %d user(s) - nothing sent", len(by_user) or len(list(user_ids)))
        return await _send_many(tokens, title, body, data, image_url, silent)
    except Exception as e:
        logger.error("[Notify] fan-out failed: %s", e)
        return 0


async def send_push_to_tokens(tokens: Iterable[str], title: str, body: str, data: Optional[Dict[str, Any]] = None,
                              image_url: Optional[str] = None, silent: bool = False) -> int:
    """Send to an explicit token list (e.g. the previous device on a takeover login). Never raises."""
    try:
        unique = [t for t in dict.fromkeys(t for t in tokens if t)]
        return await _send_many(unique, title, body, data, image_url, silent)
    except Exception as e:
        logger.error("[Notify] token fan-out failed: %s", e)
        return 0
