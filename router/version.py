import logging
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection
from core_functions.version_function import check_app_version

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/version", tags=["Version"])


@router.get("/check")
async def check_version(
    platform: str = Query(...),
    version: str = Query(...),
    app_id: Optional[str] = Query(None),
):
    """Check if app version needs update. `app_id` (bundle id) is optional: absent means the legacy app."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        data = check_app_version(cursor, platform, version, app_id)
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception:
        logger.exception("version check failed")
        raise HTTPException(status_code=500, detail="Failed to check app version")
    finally:
        cursor.close()
        return_connection(conn)
