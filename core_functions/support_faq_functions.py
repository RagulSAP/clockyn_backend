"""FAQ Hub: the FAQs support maintains in the web console and the Support > FAQ screen of the app.

App: only ACTIVE FAQs (`GET /support/faqs`). Operators (web console): list every FAQ, create, edit, delete.
`answer` is plain text (never HTML): the clients render it as text with line breaks.
Resilience: a deployment that runs before the table exists must not create a stream of 5xx (the app opens the FAQ screen,
the console polls its list), so reads answer `[]` and writes answer a clear 503 while `support_faq` is missing.
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)

MAX_BUCKET = 60
DEFAULT_BUCKET = "General"           # the top-level group a FAQ falls into when none is given
MAX_CATEGORY = 60
MIN_QUESTION = 5
MAX_QUESTION = 300
MAX_ANSWER = 8000
MAX_SORT_ORDER = 1_000_000
SORT_STEP = 10                       # a new FAQ goes after the current last one

_UNDEFINED_TABLE = "42P01"           # PostgreSQL: relation does not exist
_UNIQUE_VIOLATION = "23505"
_missing_table_logged = False

_COLUMNS = "id, faq_bucket, category, question, answer, sort_order, status, created_at, updated_at"


class FaqCreateRequest(BaseModel):
    faq_bucket: Optional[str] = None
    category: str
    question: str
    answer: str
    sort_order: Optional[int] = None
    status: Optional[int] = None


class FaqUpdateRequest(BaseModel):
    faq_bucket: Optional[str] = None
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    sort_order: Optional[int] = None
    status: Optional[int] = None


# ---------------------------------------------------------------------------------------------- pure helpers
def _text(value: Optional[str], field: str, low: int, high: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) < low:
        raise HTTPException(status_code=400, detail=f"{field} is required" if low <= 1 else f"{field} must be at least {low} characters")
    if len(cleaned) > high:
        raise HTTPException(status_code=400, detail=f"{field} must be at most {high} characters")
    return cleaned


def clean_bucket(value: Optional[str]) -> str:
    return _text(value, "Bucket", 1, MAX_BUCKET)


def clean_category(value: Optional[str]) -> str:
    return _text(value, "Category", 1, MAX_CATEGORY)


def clean_question(value: Optional[str]) -> str:
    return _text(value, "Question", MIN_QUESTION, MAX_QUESTION)


def clean_answer(value: Optional[str]) -> str:
    return _text(value, "Answer", 1, MAX_ANSWER)


def clean_sort_order(value: Optional[int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_SORT_ORDER:
        raise HTTPException(status_code=400, detail=f"sort_order must be a whole number from 0 to {MAX_SORT_ORDER}")
    return value


def clean_status(value: Optional[int]) -> int:
    if isinstance(value, bool) or value not in (0, 1):
        raise HTTPException(status_code=400, detail="status must be 0 or 1")
    return int(value)


def _tables_missing(exc: Exception) -> bool:
    return getattr(exc, "pgcode", None) == _UNDEFINED_TABLE


def _log_table_missing(what: str) -> None:
    """Say it loudly, once per process (not on every poll)."""
    global _missing_table_logged
    if not _missing_table_logged:
        _missing_table_logged = True
        logger.error("faq: the table support_faq does not exist in this schema (first seen in %s). "
                     "Run the 'CREATE: FAQ' block of clockyn_ddl_latest.sql.", what)


def _unexpected(what: str, exc: Exception) -> HTTPException:
    if _tables_missing(exc):
        _log_table_missing(what)
        return HTTPException(status_code=503, detail="FAQ is not set up yet")
    logger.exception("faq: %s failed", what)
    return HTTPException(status_code=500, detail="Internal server error")


def _degrade_if_missing(conn, what: str, exc: Exception) -> bool:
    if not _tables_missing(exc):
        return False
    try:
        conn.rollback()
    except Exception:
        pass
    _log_table_missing(what)
    return True


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"], "faq_bucket": row["faq_bucket"], "category": row["category"], "question": row["question"], "answer": row["answer"],
        "sort_order": row.get("sort_order"), "status": row.get("status"),
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
    }


# ---------------------------------------------------------------------------------------------- app
async def list_active_faqs() -> List[Dict[str, Any]]:
    """The FAQs the app shows: active only, in display order."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, faq_bucket, category, question, answer FROM support_faq WHERE status = 1 ORDER BY sort_order, id")
            rows = cur.fetchall() or []
        return [{k: r[k] for k in ("id", "faq_bucket", "category", "question", "answer")} for r in rows]
    except Exception as exc:
        if _degrade_if_missing(conn, "app list", exc):
            return []
        raise _unexpected("app list", exc)
    finally:
        return_connection(conn)


# ---------------------------------------------------------------------------------------------- operator
async def list_all_faqs() -> List[Dict[str, Any]]:
    """Every FAQ, any status, in display order (web console)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM support_faq ORDER BY sort_order, id")
            rows = cur.fetchall() or []
        return [_public(r) for r in rows]
    except Exception as exc:
        if _degrade_if_missing(conn, "operator list", exc):
            return []
        raise _unexpected("operator list", exc)
    finally:
        return_connection(conn)


def _duplicate(cur, question: str, exclude_id: Optional[int] = None) -> bool:
    if exclude_id is None:
        cur.execute("SELECT id FROM support_faq WHERE lower(question) = lower(%s)", (question,))
    else:
        cur.execute("SELECT id FROM support_faq WHERE lower(question) = lower(%s) AND id <> %s", (question, exclude_id))
    return cur.fetchone() is not None


def _conflict() -> HTTPException:
    return HTTPException(status_code=409, detail="A FAQ with this question already exists")


async def create_faq(data: FaqCreateRequest) -> Dict[str, Any]:
    bucket = DEFAULT_BUCKET if data.faq_bucket is None else clean_bucket(data.faq_bucket)
    category = clean_category(data.category)
    question = clean_question(data.question)
    answer = clean_answer(data.answer)
    sort_order = None if data.sort_order is None else clean_sort_order(data.sort_order)
    status = 1 if data.status is None else clean_status(data.status)

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if _duplicate(cur, question):
                raise _conflict()
            if sort_order is None:
                cur.execute("SELECT COALESCE(MAX(sort_order), 0) + %s AS next_order FROM support_faq", (SORT_STEP,))
                sort_order = min(int(cur.fetchone()["next_order"]), MAX_SORT_ORDER)
            cur.execute(
                f"INSERT INTO support_faq (faq_bucket, category, question, answer, sort_order, status) VALUES (%s, %s, %s, %s, %s, %s) "
                f"RETURNING {_COLUMNS}",
                (bucket, category, question, answer, sort_order, status),
            )
            row = dict(cur.fetchone())
        conn.commit()
        return {"success": True, "faq": _public(row)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == _UNIQUE_VIOLATION:      # lost a race against the unique index
            raise _conflict()
        raise _unexpected("create", exc)
    finally:
        return_connection(conn)


async def update_faq(faq_id: int, data: FaqUpdateRequest) -> Dict[str, Any]:
    sets: List[str] = []
    params: List[Any] = []
    question = None
    if data.faq_bucket is not None:
        sets.append("faq_bucket = %s")
        params.append(clean_bucket(data.faq_bucket))
    if data.category is not None:
        sets.append("category = %s")
        params.append(clean_category(data.category))
    if data.question is not None:
        question = clean_question(data.question)
        sets.append("question = %s")
        params.append(question)
    if data.answer is not None:
        sets.append("answer = %s")
        params.append(clean_answer(data.answer))
    if data.sort_order is not None:
        sets.append("sort_order = %s")
        params.append(clean_sort_order(data.sort_order))
    if data.status is not None:
        sets.append("status = %s")
        params.append(clean_status(data.status))
    if not sets:
        raise HTTPException(status_code=400, detail="Nothing to update")
    sets.append("updated_at = NOW()")

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if question is not None and _duplicate(cur, question, exclude_id=faq_id):
                raise _conflict()
            cur.execute(f"UPDATE support_faq SET {', '.join(sets)} WHERE id = %s RETURNING {_COLUMNS}", (*params, faq_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="FAQ not found")
            row = dict(row)
        conn.commit()
        return {"success": True, "faq": _public(row)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        if getattr(exc, "pgcode", None) == _UNIQUE_VIOLATION:
            raise _conflict()
        raise _unexpected("update", exc)
    finally:
        return_connection(conn)


async def delete_faq(faq_id: int) -> Dict[str, Any]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM support_faq WHERE id = %s", (faq_id,))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="FAQ not found")
        conn.commit()
        return {"success": True}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise _unexpected("delete", exc)
    finally:
        return_connection(conn)
