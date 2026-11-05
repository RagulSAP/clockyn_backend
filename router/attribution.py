import os
import logging
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from core_functions.operator_auth import require_operator
from typing import Optional
from psycopg2.extras import RealDictCursor
from core_functions.db_function import get_db_connection, return_connection

router = APIRouter(prefix="/attribution", tags=["attribution"])
logger = logging.getLogger(__name__)


class MetaAttributionRequest(BaseModel):
    user_id:     str
    campaign_id: Optional[str] = None
    adset_id:    Optional[str] = None
    ad_id:       Optional[str] = None
    source:      Optional[str] = "facebook"


@router.post("/meta")
async def save_meta_attribution(data: MetaAttributionRequest):
    """
    Store Meta Ads attribution data for a user.
    Updates the users table and appends a row to meta_attribution_log.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Update users table attribution columns
        cursor.execute(
            """
            UPDATE users
            SET meta_campaign_id    = COALESCE(%s, meta_campaign_id),
                meta_adset_id       = COALESCE(%s, meta_adset_id),
                meta_ad_id          = COALESCE(%s, meta_ad_id),
                install_source      = COALESCE(%s, install_source),
                attribution_platform = 'meta'
            WHERE user_id = %s
            """,
            (data.campaign_id, data.adset_id, data.ad_id, data.source, data.user_id),
        )

        # Append to attribution log
        cursor.execute(
            """
            INSERT INTO meta_attribution_log (user_id, campaign_id, adset_id, ad_id, source)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (data.user_id, data.campaign_id, data.adset_id, data.ad_id, data.source),
        )

        conn.commit()
        return {"success": True, "message": "Attribution saved"}
    except Exception as exc:
        conn.rollback()
        logger.error(f"[Attribution] save failed: {exc}")
        return {"success": False, "message": str(exc)}
    finally:
        cursor.close()
        return_connection(conn)


@router.get("/reports/meta-attribution", dependencies=[Depends(require_operator)])
async def meta_attribution_report():
    """
    Aggregate report: installs, registrations, and active users grouped by campaign.
    """
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT
                COALESCE(u.meta_campaign_id, 'organic') AS campaign,
                COUNT(DISTINCT u.user_id)                AS installs,
                COUNT(DISTINCT CASE WHEN u.role_id = 1 THEN u.user_id END) AS registrations,
                COUNT(DISTINCT CASE WHEN u.status = 1 THEN u.user_id END) AS active_users
            FROM users u
            WHERE u.attribution_platform = 'meta'
               OR u.install_source = 'facebook'
            GROUP BY u.meta_campaign_id
            ORDER BY installs DESC
            """
        )
        rows = cursor.fetchall()
        return {"success": True, "data": [dict(r) for r in rows]}
    except Exception as exc:
        logger.error(f"[Attribution] report failed: {exc}")
        return {"success": False, "data": [], "message": str(exc)}
    finally:
        cursor.close()
        return_connection(conn)
