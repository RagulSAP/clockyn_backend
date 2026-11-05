"""Liveness and readiness probes for the load balancer / container platform (no auth, no body parameters)."""
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health")
def health():
    """Liveness: the process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/ready")
def ready():
    """Readiness: 200 only when a connection can be taken from the pool and `SELECT 1` succeeds, else 503."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.rollback()
        return {"status": "ready"}
    except Exception as e:
        logger.warning("readiness check failed: %s", e)
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    finally:
        if conn is not None:
            return_connection(conn)
