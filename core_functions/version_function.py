"""App version policy: which app (app_id) on which platform must update, and whether the update is forced.

Table `app_version_control` holds one row per (app_id, platform). A request without app_id belongs to the
legacy app (app_id 'legacy'), so its behaviour does not change; an unknown app_id never falls back to
the legacy row.
"""
import logging
import re

from utils import branding

logger = logging.getLogger(__name__)

LEGACY_APP_ID = "legacy"

# Leading numeric dotted part of a version: "1.0.10", "v2.1", "1.0.0-beta.1" -> (1, 0, 0). Anything after it is ignored.
_VERSION_RE = re.compile(r"^\s*[vV]?(\d+(?:\.\d+)*)")

_STORE_URL_ENV = {"android": branding.android_store_url, "ios": branding.ios_store_url}


def parse_version(value):
    """Return the numeric tuple of a version string, or None when it cannot be read (never raises)."""
    if not isinstance(value, str):
        return None
    m = _VERSION_RE.match(value)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def compare_versions(a, b):
    """-1 / 0 / 1 comparing two parsed versions; missing trailing parts count as 0 ('1.0' == '1.0.0')."""
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def resolve_store_url(row_url, platform):
    """The row's store url, else the platform's env value (ANDROID_STORE_URL / IOS_STORE_URL via utils.branding) read now, else None."""
    if isinstance(row_url, str) and row_url.strip():
        return row_url.strip()
    from_env = _STORE_URL_ENV.get(platform)
    return (from_env() or "").strip() or None if from_env else None


def _no_update(latest=None, message=None, store_url=None):
    return {
        "update_required": False,
        "latest_version": latest,
        "force_update": False,
        "message": message,
        "store_url": store_url,
    }


def check_app_version(cursor, platform, version, app_id=None):
    """Version policy for one app/platform. Missing row or unreadable version means 'no update required'."""
    platform = (platform or "").strip().lower()
    app_id = (app_id or "").strip() or LEGACY_APP_ID

    cursor.execute(
        "SELECT latest_version, min_supported, force_update, message, store_url "
        "FROM app_version_control WHERE app_id = %s AND platform = %s",
        (app_id, platform),
    )
    row = cursor.fetchone()
    if not row:
        logger.info("version check: no policy row for app_id=%s platform=%s", app_id, platform)
        return _no_update()

    latest_text = row["latest_version"]
    message = row["message"]
    store_url = resolve_store_url(row["store_url"], platform)

    current = parse_version(version)
    latest = parse_version(latest_text)
    minimum = parse_version(row["min_supported"])
    if current is None:
        logger.warning("version check: unreadable app version %r (app_id=%s platform=%s)", version, app_id, platform)
        return _no_update(latest_text, message, store_url)

    update_required = latest is not None and compare_versions(current, latest) < 0
    force_required = minimum is not None and compare_versions(current, minimum) < 0
    return {
        "update_required": update_required,
        "latest_version": latest_text,
        # The row flag forces only a client that is actually behind; being below min_supported always forces.
        "force_update": bool(force_required or (row["force_update"] and update_required)),
        "message": message,
        "store_url": store_url,
    }
