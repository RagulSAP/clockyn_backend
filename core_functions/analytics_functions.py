"""Clickstream: the Clockyn app reports screen views and button taps; the operator console reads the summary.

APP side   POST /analytics/events  (bearer token, any role)  -> `record_events`
CONSOLE    GET  /gen-dashboard/clickstream/summary | /events, POST /gen-dashboard/clickstream/purge (operator routes, see router/gen_dashboard.py)

Privacy: only screen route patterns and button labels are stored, never typed text. Every text is cleaned by `clean_text`
(control characters removed, length capped, anything that looks like an e-mail or a long number replaced by "[redacted]").
The user id / organisation id come from the verified token when there is one; the ids in the body are used only when the
deployment runs without token enforcement (same as the other app routes).
All dates of the report are Indian Standard Time days. The table is `app_event` (migration 036_app_event.sql).
"""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor, execute_values
from pydantic import BaseModel

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

EVENT_TYPES = ("session", "screen", "click")
PLATFORMS = ("android", "ios", "web")
MAX_BATCH = 50
MAX_PAST = timedelta(hours=24)       # an event older than this on arrival is stored with the arrival time
MAX_FUTURE = timedelta(minutes=5)    # a device clock that runs ahead is clamped to the arrival time
MAX_SCREEN, MAX_TARGET = 120, 80
MIN_PURGE_DAYS = 30
MAX_REPORT_DAYS = 180
IST = "Asia/Kolkata"

_SESSION_RE = re.compile(r"^[A-Za-z0-9-]{8,36}$")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+\-]{1,20}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_SPACE_RE = re.compile(r"\s+")
_LONG_NUMBER_RE = re.compile(r"\d{6,}")
REDACTED = "[redacted]"


# ------------------------------------------------------------------------------------------------ intake
class AppEvent(BaseModel):
    type: str
    name: Optional[str] = None      # screen: the route pattern; click: the label
    screen: Optional[str] = None    # click: the screen it was tapped on
    t: Optional[float] = None       # device time, epoch milliseconds


class EventBatch(BaseModel):
    session_id: str
    platform: Optional[str] = None
    app_version: Optional[str] = None
    # Used ONLY when the request carries no verified identity (token enforcement off).
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    role_id: Optional[int] = None
    events: List[AppEvent]


def clean_text(raw: Optional[str], limit: int) -> Optional[str]:
    """A label / route made safe to store: no control characters, one-space gaps, capped, and never an e-mail or a long number."""
    if raw is None:
        return None
    text = _SPACE_RE.sub(" ", _CONTROL_RE.sub(" ", str(raw))).strip()
    if not text:
        return None
    if "@" in text or _LONG_NUMBER_RE.search(text):
        return REDACTED
    return text[:limit]


def clamp_event_time(t_ms: Optional[float], now: datetime) -> datetime:
    """The device time of an event when it is plausible, else the arrival time."""
    if t_ms is None:
        return now
    try:
        when = datetime.fromtimestamp(float(t_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    if when < now - MAX_PAST or when > now + MAX_FUTURE:
        return now
    return when


def build_rows(batch: EventBatch, user_id: str, org_id: str, role_id: Optional[int], now: datetime) -> List[Tuple]:
    """The insert tuples of the valid events of a batch (invalid ones are skipped, never an error)."""
    platform = batch.platform if batch.platform in PLATFORMS else None
    version = batch.app_version if batch.app_version and _VERSION_RE.match(batch.app_version) else None
    role = role_id if role_id in (1, 2, 3) else None
    rows: List[Tuple] = []
    for ev in batch.events[:MAX_BATCH]:
        if ev.type not in EVENT_TYPES:
            continue
        screen: Optional[str] = None
        target: Optional[str] = None
        if ev.type == "screen":
            screen = clean_text(ev.name, MAX_SCREEN)
            if not screen:
                continue
        elif ev.type == "click":
            target = clean_text(ev.name, MAX_TARGET)
            if not target:
                continue
            screen = clean_text(ev.screen, MAX_SCREEN)
        rows.append((clamp_event_time(ev.t, now), batch.session_id, user_id, org_id, role, platform, version, ev.type, screen, target))
    return rows


async def record_events(batch: EventBatch, identity: Any = None) -> Dict[str, Any]:
    """Stores one batch. `identity` is `request.state.identity` (None when there is no verified token)."""
    if not _SESSION_RE.match(batch.session_id or ""):
        raise HTTPException(status_code=400, detail="Invalid session id")
    if identity is not None:
        user_id, org_id, role_id = identity.user_id, identity.org_id, identity.role_id
    else:
        user_id, org_id, role_id = (batch.user_id or "").strip(), (batch.org_id or "").strip(), batch.role_id
    if not user_id or not org_id or len(user_id) > 36 or len(org_id) > 36:
        raise HTTPException(status_code=400, detail="User and organisation are required")
    rows = build_rows(batch, user_id, org_id, role_id, datetime.now(timezone.utc))
    if not rows:
        return {"success": True, "stored": 0}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            execute_values(
                cur,
                "INSERT INTO app_event (event_time, session_id, user_id, org_id, role_id, platform, app_version, event_type, screen, target) VALUES %s",
                rows,
            )
        conn.commit()
        return {"success": True, "stored": len(rows)}
    except Exception as exc:
        conn.rollback()
        if _table_missing(exc):
            # Analytics must never break the app: before migration 036 is applied the events are simply dropped.
            logger.warning("app_event table is missing: apply migrations/036_app_event.sql")
            return {"success": True, "stored": 0}
        logger.exception("Could not store clickstream events")
        raise HTTPException(status_code=500, detail="Could not store the events")
    finally:
        return_connection(conn)


def _table_missing(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) == "42P01" or "app_event" in str(exc) and "does not exist" in str(exc)


# ------------------------------------------------------------------------------------------------ funnels (pure)
# A funnel is an ordered list of (label, screen). A trailing % matches a prefix. Users are counted at a step when they reached
# it AFTER the previous step (first visit of each step inside the report window).
FUNNELS: Tuple[Dict[str, Any], ...] = (
    {
        "key": "staff_setup", "title": "Add your team",
        "description": "Admins getting from the home screen to the add-staff form",
        "steps": (("Admin home", "/admin/home"), ("Staff list", "/admin/staff"), ("Add / edit staff", "/admin/staff/edit")),
    },
    {
        "key": "upgrade", "title": "Upgrade to a paid plan",
        "description": "From the billing page to the payment screen",
        "steps": (("Billing", "/admin/billing"), ("Choose a plan", "/admin/billing/upgrade"), ("Checkout", "/admin/billing/checkout")),
    },
    {
        "key": "payroll", "title": "Payroll adoption",
        "description": "Salary setup through to payslips",
        "steps": (("Monthly salary", "/admin/payroll/monthly-salary"), ("Salary sheet", "/admin/payroll/salary-sheet"), ("Payslips", "/admin/payroll/payslips")),
    },
    {
        "key": "help", "title": "Getting help",
        "description": "Support tab to a video, FAQ or live chat",
        "steps": (("Help tab", "/admin/help"), ("Videos, FAQ or chat", "/admin/help/%")),
    },
)


def funnel_counts(rows: Sequence[Sequence[Optional[datetime]]], steps: int) -> List[int]:
    """`rows`: one tuple per user with the FIRST time each step was seen (None = never). Returns users per step, in order."""
    counts = [0] * steps
    for row in rows:
        previous: Optional[datetime] = None
        for i in range(steps):
            when = row[i] if i < len(row) else None
            if when is None or (previous is not None and when < previous):
                break
            counts[i] += 1
            previous = when
    return counts


def _step_condition(screen: str) -> Tuple[str, str]:
    return ("screen LIKE %s", screen) if "%" in screen else ("screen = %s", screen)


# ------------------------------------------------------------------------------------------------ report
def _filters(platform: Optional[str], role: Optional[int]) -> Tuple[str, List[Any]]:
    sql, params = "", []
    if platform in PLATFORMS:
        sql += " AND platform = %s"
        params.append(platform)
    if role in (1, 2, 3):
        sql += " AND role_id = %s"
        params.append(role)
    return sql, params


def _f(v: Any) -> float:
    return float(v) if v is not None else 0.0


def _i(v: Any) -> int:
    return int(v) if v is not None else 0


async def clickstream_summary(days: int = 30, platform: Optional[str] = None, role: Optional[int] = None) -> Dict[str, Any]:
    """Everything the Clickstream page shows, in one response."""
    days = max(1, min(int(days or 30), MAX_REPORT_DAYS))
    flt, fp = _filters(platform, role)
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                return _summary(cur, days, flt, fp)
            except Exception as exc:
                conn.rollback()
                if _table_missing(exc):
                    return {"ready": False, "message": "The clickstream table does not exist yet. Apply migrations/036_app_event.sql."}
                raise
    except HTTPException:
        raise
    except Exception:
        logger.exception("Clickstream summary failed")
        raise HTTPException(status_code=500, detail="Could not build the clickstream report")
    finally:
        return_connection(conn)


def _summary(cur, days: int, flt: str, fp: List[Any]) -> Dict[str, Any]:
    # IST midnight of the first day of the window, the same for the previous window, and now.
    cur.execute(
        """
        SELECT (((now() AT TIME ZONE %s)::date - (%s - 1))::timestamp AT TIME ZONE %s) AS start_at,
               (((now() AT TIME ZONE %s)::date - (%s * 2 - 1))::timestamp AT TIME ZONE %s) AS prev_start_at,
               now() AS now_at
        """,
        (IST, days, IST, IST, days, IST),
    )
    w = cur.fetchone()
    start, prev_start, now_at = w["start_at"], w["prev_start_at"], w["now_at"]

    def kpis(a: datetime, b: datetime) -> Dict[str, Any]:
        cur.execute(
            f"""
            SELECT COUNT(*) AS events,
                   COUNT(*) FILTER (WHERE event_type = 'screen') AS screen_views,
                   COUNT(*) FILTER (WHERE event_type = 'click') AS clicks,
                   COUNT(DISTINCT session_id) AS sessions,
                   COUNT(DISTINCT user_id) AS users,
                   COUNT(DISTINCT org_id) AS orgs
            FROM app_event WHERE event_time >= %s AND event_time < %s{flt}
            """,
            (a, b, *fp),
        )
        k = dict(cur.fetchone())
        cur.execute(
            f"""
            SELECT AVG(secs) AS avg_secs FROM (
                SELECT EXTRACT(EPOCH FROM MAX(event_time) - MIN(event_time)) AS secs
                FROM app_event WHERE event_time >= %s AND event_time < %s{flt}
                GROUP BY session_id HAVING COUNT(*) >= 2
            ) s
            """,
            (a, b, *fp),
        )
        k["avg_session_secs"] = _f(cur.fetchone()["avg_secs"])
        k["screens_per_session"] = round(k["screen_views"] / k["sessions"], 1) if k["sessions"] else 0
        return {key: (float(v) if isinstance(v, float) else _i(v)) for key, v in k.items()}

    current = kpis(start, now_at)
    previous = kpis(prev_start, start)

    # Reach right now (rolling windows, not the report window) and stickiness = average daily users / monthly users.
    cur.execute(
        f"""
        SELECT COUNT(DISTINCT user_id) FILTER (WHERE event_time >= now() - interval '1 day') AS dau,
               COUNT(DISTINCT user_id) FILTER (WHERE event_time >= now() - interval '7 days') AS wau,
               COUNT(DISTINCT user_id) AS mau
        FROM app_event WHERE event_time >= now() - interval '30 days'{flt}
        """,
        tuple(fp),
    )
    reach = {k: _i(v) for k, v in cur.fetchone().items()}
    cur.execute(
        f"""
        SELECT COALESCE(SUM(u), 0) AS total FROM (
            SELECT COUNT(DISTINCT user_id) AS u FROM app_event
            WHERE event_time >= now() - interval '30 days'{flt}
            GROUP BY (event_time AT TIME ZONE %s)::date
        ) d
        """,
        (*fp, IST),
    )
    avg_dau = _f(cur.fetchone()["total"]) / 30.0
    reach["stickiness"] = round(100.0 * avg_dau / reach["mau"], 1) if reach["mau"] else 0.0

    cur.execute(
        f"""
        SELECT COUNT(*) FILTER (WHERE first_seen >= %s) AS new_users,
               COUNT(*) FILTER (WHERE first_seen < %s) AS returning_users
        FROM (SELECT user_id, MIN(event_time) AS first_seen FROM app_event WHERE true{flt}
              GROUP BY user_id HAVING MAX(event_time) >= %s) t
        """,
        (start, start, *fp, start),
    )
    nr = cur.fetchone()
    reach["new_users"], reach["returning_users"] = _i(nr["new_users"]), _i(nr["returning_users"])

    cur.execute(
        f"""
        SELECT d.day::date AS day, COALESCE(a.users, 0) AS users, COALESCE(a.sessions, 0) AS sessions,
               COALESCE(a.events, 0) AS events, COALESCE(a.clicks, 0) AS clicks
        FROM generate_series((now() AT TIME ZONE %s)::date - (%s - 1), (now() AT TIME ZONE %s)::date, interval '1 day') AS d(day)
        LEFT JOIN (
            SELECT (event_time AT TIME ZONE %s)::date AS day, COUNT(DISTINCT user_id) AS users, COUNT(DISTINCT session_id) AS sessions,
                   COUNT(*) AS events, COUNT(*) FILTER (WHERE event_type = 'click') AS clicks
            FROM app_event WHERE event_time >= %s{flt} GROUP BY 1
        ) a ON a.day = d.day::date
        ORDER BY d.day
        """,
        (IST, days, IST, IST, start, *fp),
    )
    daily = [{"day": r["day"].isoformat(), "users": _i(r["users"]), "sessions": _i(r["sessions"]), "events": _i(r["events"]), "clicks": _i(r["clicks"])} for r in cur.fetchall()]

    cur.execute(
        f"""
        WITH s AS (
            SELECT event_type, screen, user_id, org_id, event_time,
                   LEAD(event_time) OVER (PARTITION BY session_id ORDER BY event_time, id) AS nxt
            FROM app_event WHERE event_time >= %s{flt}
        )
        SELECT screen, COUNT(*) AS views, COUNT(DISTINCT user_id) AS users, COUNT(DISTINCT org_id) AS orgs,
               AVG(LEAST(EXTRACT(EPOCH FROM nxt - event_time), 600)) FILTER (WHERE nxt IS NOT NULL AND nxt - event_time < interval '30 minutes') AS avg_secs
        FROM s WHERE event_type = 'screen' AND screen IS NOT NULL
        GROUP BY screen ORDER BY views DESC, screen LIMIT 30
        """,
        (start, *fp),
    )
    screens = [{"screen": r["screen"], "views": _i(r["views"]), "users": _i(r["users"]), "orgs": _i(r["orgs"]), "avg_secs": round(_f(r["avg_secs"]), 1)} for r in cur.fetchall()]

    cur.execute(
        f"""
        SELECT target, screen, COUNT(*) AS clicks, COUNT(DISTINCT user_id) AS users
        FROM app_event WHERE event_type = 'click' AND target IS NOT NULL AND target NOT LIKE 'event:%%' AND event_time >= %s{flt}
        GROUP BY target, screen ORDER BY clicks DESC, target LIMIT 30
        """,
        (start, *fp),
    )
    clicks = [{"target": r["target"], "screen": r["screen"], "clicks": _i(r["clicks"]), "users": _i(r["users"])} for r in cur.fetchall()]

    # Key actions reported by the app itself (login, registration, punch, subscription): `event:<Name>[:<action>]`.
    cur.execute(
        f"""
        SELECT substr(target, 7) AS action, COUNT(*) AS n, COUNT(DISTINCT user_id) AS users, COUNT(DISTINCT org_id) AS orgs
        FROM app_event WHERE event_type = 'click' AND target LIKE 'event:%%' AND event_time >= %s{flt}
        GROUP BY 1 ORDER BY n DESC, 1 LIMIT 12
        """,
        (start, *fp),
    )
    key_actions = [{"action": r["action"], "count": _i(r["n"]), "users": _i(r["users"]), "orgs": _i(r["orgs"])} for r in cur.fetchall()]

    def split(column: str, limit: int) -> List[Dict[str, Any]]:
        cur.execute(
            f"""
            SELECT COALESCE({column}::text, 'unknown') AS k, COUNT(DISTINCT user_id) AS users, COUNT(*) AS events
            FROM app_event WHERE event_time >= %s{flt} GROUP BY 1 ORDER BY users DESC, events DESC LIMIT {limit}
            """,
            (start, *fp),
        )
        return [{"key": r["k"], "users": _i(r["users"]), "events": _i(r["events"])} for r in cur.fetchall()]

    splits = {"platform": split("platform", 5), "role": split("role_id", 4), "version": split("app_version", 8)}

    cur.execute(
        f"""
        SELECT EXTRACT(DOW FROM event_time AT TIME ZONE %s)::int AS dow, EXTRACT(HOUR FROM event_time AT TIME ZONE %s)::int AS hr,
               COUNT(DISTINCT session_id) AS sessions
        FROM app_event WHERE event_time >= %s{flt} GROUP BY 1, 2
        """,
        (IST, IST, start, *fp),
    )
    heatmap = [{"dow": r["dow"], "hour": r["hr"], "sessions": _i(r["sessions"])} for r in cur.fetchall()]

    cur.execute(
        f"""
        WITH s AS (
            SELECT screen, LEAD(screen) OVER (PARTITION BY session_id ORDER BY event_time, id) AS nxt
            FROM app_event WHERE event_type = 'screen' AND event_time >= %s{flt}
        )
        SELECT screen AS src, nxt AS dst, COUNT(*) AS n FROM s
        WHERE nxt IS NOT NULL AND nxt <> screen GROUP BY 1, 2 ORDER BY n DESC, 1, 2 LIMIT 12
        """,
        (start, *fp),
    )
    flows = [{"from": r["src"], "to": r["dst"], "count": _i(r["n"])} for r in cur.fetchall()]

    funnels = []
    for fn in FUNNELS:
        conds = [_step_condition(s) for _, s in fn["steps"]]
        cols = ", ".join(f"MIN(event_time) FILTER (WHERE {c}) AS s{i}" for i, (c, _) in enumerate(conds))
        params = [p for _, p in conds]
        cur.execute(
            f"SELECT user_id, {cols} FROM app_event WHERE event_type = 'screen' AND event_time >= %s{flt} GROUP BY user_id",
            (*params, start, *fp),
        )
        data = [tuple(r[f"s{i}"] for i in range(len(conds))) for r in cur.fetchall()]
        counts = funnel_counts(data, len(conds))
        funnels.append({
            "key": fn["key"], "title": fn["title"], "description": fn["description"],
            "steps": [{"label": label, "screen": screen, "users": counts[i]} for i, (label, screen) in enumerate(fn["steps"])],
        })

    cur.execute(
        f"""
        SELECT e.org_id, COALESCE(o.org_name, 'Unknown organization') AS org_name, COUNT(DISTINCT e.user_id) AS users,
               COUNT(DISTINCT e.session_id) AS sessions, COUNT(*) AS events, MAX(e.event_time) AS last_seen
        FROM app_event e LEFT JOIN org_info o ON o.org_id = e.org_id
        WHERE e.event_time >= %s{flt.replace('platform', 'e.platform').replace('role_id', 'e.role_id')}
        GROUP BY e.org_id, o.org_name ORDER BY events DESC LIMIT 10
        """,
        (start, *fp),
    )
    top_orgs = [{"org_id": r["org_id"], "org_name": r["org_name"], "users": _i(r["users"]), "sessions": _i(r["sessions"]), "events": _i(r["events"]), "last_seen": r["last_seen"].isoformat()} for r in cur.fetchall()]

    # Organisations that were active in the 30 days before the last week and have been silent for the last 7 days.
    cur.execute(
        """
        SELECT e.org_id, COALESCE(o.org_name, 'Unknown organization') AS org_name, COUNT(DISTINCT e.user_id) AS users,
               COUNT(*) AS events, MAX(e.event_time) AS last_seen
        FROM app_event e LEFT JOIN org_info o ON o.org_id = e.org_id
        WHERE e.event_time >= now() - interval '37 days'
        GROUP BY e.org_id, o.org_name HAVING MAX(e.event_time) < now() - interval '7 days'
        ORDER BY events DESC LIMIT 10
        """
    )
    at_risk = [{"org_id": r["org_id"], "org_name": r["org_name"], "users": _i(r["users"]), "events": _i(r["events"]), "last_seen": r["last_seen"].isoformat()} for r in cur.fetchall()]

    cur.execute("SELECT MIN(event_time) AS first_event, COUNT(*) AS total FROM app_event")
    all_time = cur.fetchone()
    business = _business(cur, days, start, prev_start)

    return {
        "ready": True,
        "days": days,
        "since": start.isoformat(),
        "generated_at": now_at.isoformat(),
        "first_event": all_time["first_event"].isoformat() if all_time["first_event"] else None,
        "total_events_ever": _i(all_time["total"]),
        "kpis": current,
        "previous": previous,
        "reach": reach,
        "daily": daily,
        "screens": screens,
        "clicks": clicks,
        "key_actions": key_actions,
        "splits": splits,
        "heatmap": heatmap,
        "flows": flows,
        "funnels": funnels,
        "top_orgs": top_orgs,
        "at_risk_orgs": at_risk,
        "business": business,
    }


# ------------------------------------------------------------------------------------------------ business insights
# The rest of the picture comes from the tables the product already fills: sign-ups, trials, plans and payments, how much each
# feature is really used, and the support channels. Every block is read on its own and fails soft (an empty answer), so a
# difference between databases can never take the whole page down.
# (key, label, table, organisation column, time column)
FEATURE_TABLES: Tuple[Tuple[str, str, str, str, str], ...] = (
    ("attendance", "Attendance punches", "attendance", "org_id", "created_at"),
    ("requests", "Leave, permission and shift requests", "requests", "org_id", "created_at"),
    ("leads", "CRM leads added", "leads_info", "org_id", "created_at"),
    ("expenses", "Travel and bill expenses", "travel_bill_expenses", "org_id", "created_at"),
    ("odometer", "Odometer readings", "odometer_readings", "org_id", "created_at"),
    ("announcements", "Announcements sent", "announcement", "org_id", "created_at"),
    ("salary", "Salary payments recorded", "salary_payments", "org_id", "paid_at"),
)


def _safe(cur, sql: str, params: Sequence[Any] = (), many: bool = True):
    """Runs a read query; on any error rolls back and answers empty (None / [])."""
    try:
        cur.execute(sql, tuple(params))
        return cur.fetchall() if many else cur.fetchone()
    except Exception:
        cur.connection.rollback()
        logger.warning("Clickstream business query failed: %s", " ".join(sql.split())[:90], exc_info=True)
        return [] if many else None


def _business(cur, days: int, start: datetime, prev_start: datetime) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    row = _safe(cur, "SELECT COUNT(*) AS total FROM org_info", many=False)
    out["orgs_total"] = _i(row["total"]) if row else 0

    def count_between(table: str, col: str, a: datetime, b: datetime) -> int:
        r = _safe(cur, f"SELECT COUNT(*) AS n FROM {table} WHERE {col} >= %s AND {col} < %s", (a, b), many=False)
        return _i(r["n"]) if r else 0

    # Sign-ups (organisations) and the people added.
    now_plus = start + timedelta(days=days + 1)
    out["new_orgs"] = count_between("org_info", "created_at", start, now_plus)
    out["new_orgs_previous"] = count_between("org_info", "created_at", prev_start, start)
    out["signup_daily"] = [
        {"day": r["day"].isoformat(), "count": _i(r["n"])}
        for r in _safe(cur, "SELECT (created_at AT TIME ZONE %s)::date AS day, COUNT(*) AS n FROM org_info WHERE created_at >= %s GROUP BY 1 ORDER BY 1", (IST, start))
    ]
    row = _safe(cur, "SELECT COUNT(*) AS n FROM users WHERE created_at >= %s AND role_id = 3", (start,), many=False)
    out["staff_added"] = _i(row["n"]) if row else 0
    out["install_sources"] = [
        {"key": r["k"], "count": _i(r["n"])}
        for r in _safe(cur, "SELECT COALESCE(NULLIF(install_source, ''), 'organic') AS k, COUNT(*) AS n FROM users WHERE role_id = 1 AND created_at >= %s GROUP BY 1 ORDER BY n DESC LIMIT 6", (start,))
    ]

    # Trials, paying customers and revenue.
    row = _safe(cur, "SELECT COUNT(DISTINCT org_id) AS n FROM free_trial WHERE is_active = true AND end_date >= now()", many=False)
    out["trials_active"] = _i(row["n"]) if row else 0
    row = _safe(cur, "SELECT COUNT(DISTINCT org_id) AS n FROM subscription WHERE status = 'ACTIVE'", many=False)
    out["paid_active"] = _i(row["n"]) if row else 0
    row = _safe(
        cur,
        """
        SELECT COUNT(*) AS created, COUNT(*) FILTER (WHERE status = 'PAID') AS paid,
               COALESCE(SUM(amount) FILTER (WHERE status = 'PAID'), 0) AS revenue
        FROM gen_orders WHERE created_at >= %s AND created_at < %s
        """,
        (start, now_plus),
        many=False,
    )
    prev = _safe(
        cur,
        "SELECT COALESCE(SUM(amount) FILTER (WHERE status = 'PAID'), 0) AS revenue FROM gen_orders WHERE created_at >= %s AND created_at < %s",
        (prev_start, start),
        many=False,
    )
    out["orders"] = {
        "created": _i(row["created"]) if row else 0,
        "paid": _i(row["paid"]) if row else 0,
        "revenue": _f(row["revenue"]) if row else 0.0,
        "revenue_previous": _f(prev["revenue"]) if prev else 0.0,
    }
    out["plan_mix"] = [
        {"plan": r["plan_name"] or "Plan", "orgs": _i(r["n"])}
        for r in _safe(
            cur,
            """
            SELECT p.plan_name, COUNT(DISTINCT s.org_id) AS n
            FROM subscription s LEFT JOIN gen_plans p ON p.plan_id = s.plan_id
            WHERE s.status = 'ACTIVE' GROUP BY p.plan_name ORDER BY n DESC LIMIT 8
            """,
        )
    ]

    # Feature usage: organisations that used it in the window and how many records they created.
    features = []
    for key, label, table, org_col, time_col in FEATURE_TABLES:
        r = _safe(cur, f"SELECT COUNT(DISTINCT {org_col}) AS orgs, COUNT(*) AS records FROM {table} WHERE {time_col} >= %s", (start,), many=False)
        features.append({"key": key, "label": label, "orgs": _i(r["orgs"]) if r else 0, "records": _i(r["records"]) if r else 0})
    out["features"] = features

    # Support channels.
    row = _safe(
        cur,
        "SELECT COUNT(*) AS views, COUNT(DISTINCT user_id) AS users, COUNT(DISTINCT org_id) AS orgs FROM support_video_view WHERE viewed_at >= %s",
        (start,),
        many=False,
    )
    out["video"] = {
        "views": _i(row["views"]) if row else 0,
        "users": _i(row["users"]) if row else 0,
        "orgs": _i(row["orgs"]) if row else 0,
        "top": [
            {"name": r["video_name"], "views": _i(r["views"]), "users": _i(r["users"])}
            for r in _safe(
                cur,
                """
                SELECT video_name, COUNT(*) AS views, COUNT(DISTINCT user_id) AS users
                FROM support_video_view WHERE viewed_at >= %s GROUP BY video_name ORDER BY views DESC, video_name LIMIT 8
                """,
                (start,),
            )
        ],
    }
    row = _safe(
        cur,
        """
        SELECT COUNT(DISTINCT org_id) AS orgs, COUNT(*) AS messages
        FROM live_chat_message WHERE sender_type = 'org' AND created_at >= %s
        """,
        (start,),
        many=False,
    )
    out["live_chat"] = {"orgs": _i(row["orgs"]) if row else 0, "messages": _i(row["messages"]) if row else 0}
    return out


async def clickstream_events(limit: int = 200, org_id: Optional[str] = None, user_id: Optional[str] = None,
                             event_type: Optional[str] = None, q: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest first: the raw stream with the organisation and user names joined at read time."""
    limit = max(1, min(int(limit or 200), 1000))
    where, params = ["true"], []  # type: List[str], List[Any]
    if org_id:
        where.append("e.org_id = %s")
        params.append(org_id)
    if user_id:
        where.append("e.user_id = %s")
        params.append(user_id)
    if event_type in EVENT_TYPES:
        where.append("e.event_type = %s")
        params.append(event_type)
    if q and q.strip():
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(e.screen ILIKE %s OR e.target ILIKE %s OR o.org_name ILIKE %s OR u.name ILIKE %s)")
        params.extend([like, like, like, like])
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    f"""
                    SELECT e.id, e.event_time, e.session_id, e.user_id, e.org_id, e.role_id, e.platform, e.app_version,
                           e.event_type, e.screen, e.target, u.name AS user_name, o.org_name
                    FROM app_event e
                    LEFT JOIN users u ON u.user_id = e.user_id
                    LEFT JOIN org_info o ON o.org_id = e.org_id
                    WHERE {' AND '.join(where)}
                    ORDER BY e.event_time DESC, e.id DESC LIMIT %s
                    """,
                    (*params, limit),
                )
                return [dict(r) for r in cur.fetchall()]
            except Exception as exc:
                conn.rollback()
                if _table_missing(exc):
                    return []
                raise
    except Exception:
        logger.exception("Clickstream events failed")
        raise HTTPException(status_code=500, detail="Could not load the events")
    finally:
        return_connection(conn)


async def purge_clickstream(days: int) -> Dict[str, Any]:
    """Deletes events older than `days` (at least 30)."""
    days = int(days)
    if days < MIN_PURGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Keep at least {MIN_PURGE_DAYS} days")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_event WHERE event_time < now() - make_interval(days => %s)", (days,))
            deleted = cur.rowcount
        conn.commit()
        return {"success": True, "deleted": deleted}
    except Exception:
        conn.rollback()
        logger.exception("Clickstream purge failed")
        raise HTTPException(status_code=500, detail="Could not purge the events")
    finally:
        return_connection(conn)
