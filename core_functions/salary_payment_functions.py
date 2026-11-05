"""Monthly salary paid state: list with paid flag, and bulk mark-paid.

Salary figures come from the shared payroll engine (`salary_engine.compute_org_salaries`, one set-based batch for the
organisation), imported lazily so this module stays importable without the PDF/report dependencies.

Paid state: `mark_paid` stores the net amount AND a snapshot of the computed salary (`salary_payments.snapshot`,
migration 032). A paid row is never recomputed silently: the list, the breakdown and the payslip return the stored
figures with `paid: true` (the engine overlays them, `payroll_engine.apply_paid_state`); marking again is a no-op
(`ON CONFLICT DO NOTHING`), so a stored payment cannot be overwritten.
"""
import json
from decimal import Decimal
from typing import List, Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor

from core_functions.db_function import get_db_connection, return_connection
from core_functions.payroll_engine import derive_salary_type, snapshot_of  # noqa: F401  (derive_salary_type re-exported)

MAX_USER_IDS = 500
IST_TZ = 'Asia/Kolkata'


def _engine_rows(org_id: str, user_ids: Optional[List[str]], year: int, month: int) -> List[dict]:
    """Salary rows of the shared engine (a seam the tests replace)."""
    from core_functions.salary_engine import compute_org_salaries
    return compute_org_salaries(org_id, year, month, user_ids)


def _check_period(month: int, year: int):
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")
    if not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year must be between 2000 and 2100")


async def get_monthly_list(org_id: str, month: int, year: int) -> List[dict]:
    """Active staff with salary type, basic / net salary and paid state. A paid row carries the stored figures."""
    _check_period(month, year)
    out = []
    for r in _engine_rows(org_id, None, year, month):
        base = {"user_id": r['user_id'], "name": r['name'], "emp_id": r['emp_id'],
                "dept_name": r['dept_name'], "branch_name": r['branch_name']}
        if r['error']:
            out.append({**base, "error": True})
            continue
        d = r['data']
        out.append({
            **base,
            "salary_type": d['salary_type'],
            "basic_salary": float(d['earnings']['basic']),
            "net_salary": float(d['net_salary']),
            "paid": d['paid'],
            "paid_date": d['paid_date'],
        })
    return out


async def mark_paid(org_id: str, user_ids: List[str], month: int, year: int, paid_by: str = None) -> dict:
    _check_period(month, year)
    ids = list(dict.fromkeys(user_ids or []))  # dedupe, keep order
    if not ids:
        raise HTTPException(status_code=400, detail="Select at least one employee")
    if len(ids) > MAX_USER_IDS:
        raise HTTPException(status_code=400, detail=f"Select at most {MAX_USER_IDS} employees")

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """SELECT user_id FROM users
               WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1 AND user_id = ANY(%s)""",
            (org_id, ids),
        )
        valid = {r['user_id'] for r in cur.fetchall()}
    finally:
        cur.close()
        return_connection(conn)
    for uid in ids:
        if uid not in valid:
            raise HTTPException(status_code=404, detail=f"Staff not found: {uid}")

    # Amounts and snapshots come from the salary engine, computed before the write transaction. One failing
    # employee aborts the whole request: nothing is written.
    by_user = {r['user_id']: r for r in _engine_rows(org_id, ids, year, month)}
    nets, snaps = [], []
    for uid in ids:
        row = by_user.get(uid)
        if row is None or row['error'] or row['data'] is None:
            raise HTTPException(status_code=500, detail="Could not compute the salary of one or more employees")
        data = row['data']
        nets.append(str(Decimal(str(data['net_salary'])).quantize(Decimal('0.01'))))
        snaps.append(json.dumps(snapshot_of(data)))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO salary_payments (org_id, user_id, pay_month, pay_year, net_amount, paid_by, snapshot)
            SELECT %s, x.user_id, %s, %s, x.net, %s, x.snap::jsonb
            FROM unnest(%s::text[], %s::numeric[], %s::text[]) AS x(user_id, net, snap)
            ON CONFLICT (org_id, user_id, pay_month, pay_year) DO NOTHING
            """,
            (org_id, month, year, paid_by, ids, nets, snaps),
        )
        marked = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        return_connection(conn)
    return {
        "message": f"Salary marked as paid for {marked} employee(s)",
        "marked": marked,
        "already_paid": len(ids) - marked,
    }
