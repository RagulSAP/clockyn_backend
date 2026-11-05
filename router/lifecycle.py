import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg2.extras import RealDictCursor

from core_functions import data_lifecycle
from core_functions.db_function import get_db_connection, return_connection
from core_functions.operator_auth import require_operator_super

router = APIRouter(prefix="/admin/lifecycle", tags=["lifecycle"])
logger = logging.getLogger(__name__)


@router.post("/purge-deleted", dependencies=[Depends(require_operator_super)])
async def purge_deleted_route(
    days: int = Query(data_lifecycle.DEFAULT_RETENTION_DAYS, description="retention in days (1-3650)"),
    dry_run: bool = Query(True, description="true (default): only count; false: really purge"),
):
    """OPERATOR route (X-Operator-Key or a role-1 platform login; always enforced). Permanently removes (or anonymises
    where payroll history must stay) staff soft-deleted more than `days` days ago. Default `dry_run=true` returns the counts only."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        try:
            result = data_lifecycle.purge_deleted_staff(cursor, days=days, dry_run=dry_run, conn=conn)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if dry_run:
            conn.rollback()      # a dry run writes nothing; end the read transaction
        return {"success": True, **result}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("purge-deleted failed")
        raise HTTPException(status_code=500, detail="Failed to run the purge")
    finally:
        cursor.close()
        return_connection(conn)
