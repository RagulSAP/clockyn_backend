"""Face attempt measurement + bounded inference offload.

- `run_inference(fn, *args)`: run blocking model code in a bounded thread pool
  (env FACE_MAX_CONCURRENCY, default 8: the work is now network bound
  (Rekognition calls) plus light image decoding) so the event loop is never blocked.
- `FaceAttempt`: collects one attempt's numbers; `emit()` writes one JSON line to
  the dedicated `face.metrics` logger and (best effort, env FACE_ATTEMPT_LOG, default 1)
  inserts a row into `face_attempt_log` through its OWN short connection.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("face.metrics")
# This logger has its own level and handler (it does not depend on the root logger configuration).
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)


def _max_concurrency() -> int:
    try:
        return max(1, min(16, int(os.getenv("FACE_MAX_CONCURRENCY", 8))))
    except (TypeError, ValueError):
        return 8


_executor = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_max_concurrency(), thread_name_prefix="face-infer")
    return _executor


async def run_inference(fn, *args, **kwargs):
    """Await blocking `fn(*args)` on the bounded face thread pool (extra calls queue)."""
    loop = asyncio.get_running_loop()
    if kwargs:
        import functools
        fn = functools.partial(fn, **kwargs)
    return await loop.run_in_executor(_get_executor(), fn, *args)


def short_hash(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]


def _attempt_log_enabled() -> bool:
    return os.getenv("FACE_ATTEMPT_LOG", "1").strip().lower() not in ("0", "false", "no", "off", "")


class FaceAttempt:
    def __init__(self, action, user_id, org_id, forced=False, img_bytes=0):
        self.action = action
        self.user_id = user_id
        self.org_id = org_id
        self.forced = bool(forced)
        self.img_bytes = img_bytes
        self.score = None
        self.quality = {}
        self.detect_ms = None
        self.match_ms = None
        self.outcome = None
        self._t0 = time.perf_counter()

    def set_quality(self, quality):
        if quality:
            self.quality = dict(quality)

    def line(self) -> dict:
        q = self.quality or {}
        return {
            "event": "face_attempt",
            "action": self.action,
            "user": short_hash(self.user_id),
            "outcome": self.outcome,
            "score": None if self.score is None else round(float(self.score), 4),
            "forced": self.forced,
            "img_w": q.get("w"),
            "img_h": q.get("h"),
            "img_bytes": self.img_bytes,
            "brightness": q.get("brightness"),
            "blur": q.get("blur"),
            "detect_ms": self.detect_ms,
            "match_ms": self.match_ms,
            "total_ms": int((time.perf_counter() - self._t0) * 1000),
        }

    def emit(self, outcome=None):
        """Log the JSON line and schedule the DB insert. Never raises."""
        try:
            if outcome is not None:
                self.outcome = outcome
            row = self.line()
            logger.info(json.dumps(row, separators=(",", ":")))
            if _attempt_log_enabled():
                try:
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, persist_attempt, self.org_id, self.user_id, row)
                except RuntimeError:
                    persist_attempt(self.org_id, self.user_id, row)
        except Exception as e:  # pragma: no cover - defensive
            try:
                logger.warning("face metrics emit failed: %s", e)
            except Exception:
                pass


def persist_attempt(org_id, user_id, row) -> bool:
    """Insert one row into face_attempt_log on a dedicated connection.

    Uses its own short-lived connection (not the pool) so a failure here can never
    touch the punch transaction. All errors are swallowed and logged.
    """
    conn = None
    try:
        import psycopg2
        from core_functions.db_function import get_db_config
        conn = psycopg2.connect(connect_timeout=3, **get_db_config())
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO face_attempt_log (org_id, user_id, action, outcome, score, forced, "
                "brightness, blur, img_w, img_h, ms_total) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    None if org_id is None else str(org_id)[:36],
                    None if user_id is None else str(user_id)[:36],
                    row.get("action"),
                    row.get("outcome"),
                    row.get("score"),
                    bool(row.get("forced")),
                    row.get("brightness"),
                    row.get("blur"),
                    row.get("img_w"),
                    row.get("img_h"),
                    row.get("total_ms"),
                ),
            )
        conn.commit()
        return True
    except Exception as e:
        try:
            logger.warning("face_attempt_log insert skipped: %s", e)
        except Exception:
            pass
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
