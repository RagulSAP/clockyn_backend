import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from utils.timezone import get_utc_now
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor

# temp_type: 'D' = Daily wage, 'H' = Hourly charge.
# Template rows in daily_hourly_wages have user_id IS NULL. Assignments live only in
# assigned_wages (the old per-user copy rows in daily_hourly_wages are no longer written).

MAX_AMOUNT = Decimal("9999999.99")  # column is numeric(10,2)
_TWO_PLACES = Decimal("0.01")


@contextmanager
def _transaction():
    """Yield a RealDictCursor; commit on success, roll back on any error, always release the connection."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)


def _lock_org_type(cursor, org_id: str, temp_type: str):
    """Serialise writers of one org's templates of one type (numbering, duplicate check, assignment)."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"wage:{org_id}:{temp_type}",))


def parse_amount(value) -> Decimal:
    """Validate a wage amount: number, > 0, <= 9999999.99, at most 2 decimals. Raises 400 otherwise."""
    bad = HTTPException(status_code=400, detail="Amount must be greater than 0")
    if value is None or isinstance(value, (bool, list, dict)):
        raise bad
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise bad
    if not amount.is_finite() or amount <= 0:
        raise bad
    if amount > MAX_AMOUNT:
        raise HTTPException(status_code=400, detail="Amount must not exceed 9999999.99")
    if amount != amount.quantize(_TWO_PLACES):
        raise HTTPException(status_code=400, detail="Amount can have at most 2 decimal places")
    return amount.quantize(_TWO_PLACES)


def _require_org(org_id):
    if not org_id or not isinstance(org_id, str) or not org_id.strip():
        raise HTTPException(status_code=400, detail="org_id is required")
    return org_id.strip()


def _find_template(cursor, temp_id: str, org_id: str = None, temp_type: str = None, orgs=None):
    """Return the template row, or raise 404. org_id / temp_type are checked when given; `orgs` (the caller's
    organisations, from the token) must contain the template's org when given (the app's DELETE sends no org_id)."""
    cursor.execute(
        """SELECT temp_id, org_id, temp_name, temp_type, temp_amount
           FROM daily_hourly_wages WHERE temp_id = %s AND user_id IS NULL""",
        (temp_id,),
    )
    for row in cursor.fetchall():
        if org_id is not None and row["org_id"] != org_id:
            continue
        if orgs is not None and row["org_id"] not in orgs:
            continue
        if temp_type is not None and row["temp_type"] != temp_type:
            continue
        return row
    raise HTTPException(status_code=404, detail="Template not found")


def _amount_taken(cursor, org_id: str, temp_type: str, amount: Decimal, exclude_temp_id: str = None) -> bool:
    cursor.execute(
        """SELECT 1 FROM daily_hourly_wages
           WHERE org_id = %s AND temp_type = %s AND user_id IS NULL AND temp_amount = %s
             AND (%s::text IS NULL OR temp_id <> %s::text)
           LIMIT 1""",
        (org_id, temp_type, amount, exclude_temp_id, exclude_temp_id),
    )
    return cursor.fetchone() is not None


def get_templates_by_org(org_id: str, temp_type: str):
    """All daily/hourly wage templates of an organization, oldest first."""
    org_id = _require_org(org_id)
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            SELECT d.temp_id AS id, d.temp_name, d.temp_name AS template_name,
                   d.temp_amount AS amount, d.att_bandwidth, d.created_at,
                   (SELECT COUNT(*) FROM assigned_wages aw
                      JOIN users u ON u.user_id = aw.user_id AND u.parent_id = aw.org_id AND u.status = 1
                     WHERE aw.org_id = d.org_id AND aw.temp_id = d.temp_id) AS assigned_count
            FROM daily_hourly_wages d
            WHERE d.org_id = %s AND d.temp_type = %s AND d.user_id IS NULL
            ORDER BY d.created_at ASC, d.id ASC
            """,
            (org_id, temp_type),
        )
        rows = []
        for r in cursor.fetchall():
            r = dict(r)
            r["amount"] = float(r["amount"]) if r["amount"] is not None else None
            r["assigned_count"] = int(r["assigned_count"])
            r["created_at"] = r["created_at"].isoformat() if r["created_at"] else None
            rows.append(r)
        return rows
    finally:
        cursor.close()
        return_connection(conn)


def create_template(org_id: str, amount, temp_type: str, template_name: str = None):
    """Create a wage template. The name is auto-numbered ("1", "2", ...) unless given."""
    org_id = _require_org(org_id)
    amount = parse_amount(amount)
    name = template_name.strip() if isinstance(template_name, str) and template_name.strip() else None
    if name and len(name) > 100:
        raise HTTPException(status_code=400, detail="Template name is too long")
    with _transaction() as cursor:
        _lock_org_type(cursor, org_id, temp_type)
        if _amount_taken(cursor, org_id, temp_type, amount):
            raise HTTPException(status_code=409, detail="A template with this amount already exists")
        if not name:
            cursor.execute(
                """SELECT COALESCE(MAX(CASE WHEN temp_name ~ '^[0-9]{1,9}$' THEN CAST(temp_name AS INTEGER) ELSE 0 END), 0) AS max_num
                   FROM daily_hourly_wages WHERE org_id = %s AND temp_type = %s AND user_id IS NULL""",
                (org_id, temp_type),
            )
            name = str(cursor.fetchone()["max_num"] + 1)
        temp_id = str(uuid.uuid4())
        now = get_utc_now()
        cursor.execute(
            """INSERT INTO daily_hourly_wages
               (temp_id, org_id, temp_name, temp_type, temp_amount, att_bandwidth, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (temp_id, org_id, name, temp_type, amount, None, now, now),
        )
    return {"id": temp_id, "template_name": name, "temp_name": name}


def update_template(temp_id: str, amount, temp_type: str, org_id: str = None, orgs=None):
    """Change a template's amount (org and type scoped). Raises 404 / 409 / 400."""
    amount = parse_amount(amount)
    with _transaction() as cursor:
        tpl = _find_template(cursor, temp_id, org_id, temp_type, orgs)
        _lock_org_type(cursor, tpl["org_id"], temp_type)
        if _amount_taken(cursor, tpl["org_id"], temp_type, amount, exclude_temp_id=temp_id):
            raise HTTPException(status_code=409, detail="A template with this amount already exists")
        cursor.execute(
            "UPDATE daily_hourly_wages SET temp_amount = %s, updated_at = %s WHERE temp_id = %s AND user_id IS NULL",
            (amount, get_utc_now(), temp_id),
        )
    return True


def delete_template(temp_id: str, temp_type: str, org_id: str = None, orgs=None):
    """Delete a template, its assignments and any legacy per-user copy rows in one transaction.
    Returns the number of staff that were unassigned."""
    with _transaction() as cursor:
        tpl = _find_template(cursor, temp_id, org_id, temp_type, orgs)
        org = tpl["org_id"]
        cursor.execute("DELETE FROM assigned_wages WHERE org_id = %s AND temp_id = %s", (org, temp_id))
        unassigned = cursor.rowcount
        cursor.execute(
            """DELETE FROM daily_hourly_wages c USING daily_hourly_wages t
               WHERE t.temp_id = %s AND t.org_id = %s AND t.temp_type = %s AND t.user_id IS NULL
                 AND c.org_id = t.org_id AND c.temp_type = t.temp_type AND c.temp_name = t.temp_name
                 AND c.user_id IS NOT NULL""",
            (temp_id, org, temp_type),
        )
        cursor.execute(
            "DELETE FROM daily_hourly_wages WHERE temp_id = %s AND org_id = %s AND temp_type = %s AND user_id IS NULL",
            (temp_id, org, temp_type),
        )
    return unassigned


def get_template_by_id(temp_id: str):
    """Get a specific template by ID"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        query = """
            SELECT temp_id as id, temp_name, temp_amount as amount, att_bandwidth, temp_type, org_id
            FROM daily_hourly_wages
            WHERE temp_id = %s
        """
        cursor.execute(query, (temp_id,))
        template = cursor.fetchone()
        return dict(template) if template else None
    finally:
        cursor.close()
        return_connection(conn)


def get_assigned_staff(org_id: str, temp_id: str):
    """Active staff assigned to a wage template."""
    org_id = _require_org(org_id)
    with _transaction() as cursor:
        _find_template(cursor, temp_id, org_id)
        cursor.execute(
            """
            SELECT aw.user_id, u.name, u.emp_id,
                   COALESCE(b.branch_name, '') as branch_name,
                   COALESCE(d.dept_name, '') as dept_name
            FROM assigned_wages aw
            JOIN users u ON aw.user_id = u.user_id AND u.parent_id = aw.org_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            WHERE aw.org_id = %s AND aw.temp_id = %s AND u.status = 1
            ORDER BY u.name
            """,
            (org_id, temp_id),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_unassigned_staff(org_id: str, temp_id: str):
    """Active role 2/3 staff NOT assigned to this wage template."""
    org_id = _require_org(org_id)
    with _transaction() as cursor:
        _find_template(cursor, temp_id, org_id)
        cursor.execute(
            """
            SELECT u.user_id, u.name, u.emp_id,
                   COALESCE(b.branch_name, '') as branch_name,
                   COALESCE(d.dept_name, '') as dept_name
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
              AND NOT EXISTS (SELECT 1 FROM assigned_wages aw
                               WHERE aw.org_id = u.parent_id AND aw.user_id = u.user_id AND aw.temp_id = %s)
            ORDER BY u.name
            """,
            (org_id, temp_id),
        )
        return [dict(row) for row in cursor.fetchall()]


def assign_staff_to_wage(org_id: str, user_ids: list, temp_id: str):
    """Put staff on a wage template. A staff member has at most one template per type, so any other
    template of the same type is dropped; those moves are returned as `moved_from`. Idempotent."""
    org_id = _require_org(org_id)
    if not isinstance(user_ids, list) or not user_ids or not all(isinstance(u, str) and u for u in user_ids):
        raise HTTPException(status_code=400, detail="Select at least one employee")
    user_ids = list(dict.fromkeys(user_ids))
    moved_from = []
    with _transaction() as cursor:
        tpl = _find_template(cursor, temp_id, org_id)
        temp_type = tpl["temp_type"]
        _lock_org_type(cursor, org_id, temp_type)
        cursor.execute(
            "SELECT user_id FROM users WHERE parent_id = %s AND role_id IN (2, 3) AND status = 1 AND user_id = ANY(%s)",
            (org_id, user_ids),
        )
        valid = {r["user_id"] for r in cursor.fetchall()}
        for uid in user_ids:
            if uid not in valid:
                raise HTTPException(status_code=404, detail=f"Employee not found: {uid}")
        now = get_utc_now()
        for uid in user_ids:
            cursor.execute(
                """SELECT aw.id, aw.temp_id, t.temp_name
                   FROM assigned_wages aw
                   JOIN daily_hourly_wages t ON t.temp_id = aw.temp_id AND t.user_id IS NULL
                                            AND t.org_id = aw.org_id AND t.temp_type = %s
                   WHERE aw.org_id = %s AND aw.user_id = %s""",
                (temp_type, org_id, uid),
            )
            existing = cursor.fetchall()
            if len(existing) == 1 and existing[0]["temp_id"] == temp_id:
                continue  # already on this template, nothing to change
            for row in existing:
                if row["temp_id"] != temp_id:
                    moved_from.append({"user_id": uid, "temp_id": row["temp_id"], "temp_name": row["temp_name"]})
            if existing:
                cursor.execute("DELETE FROM assigned_wages WHERE id = ANY(%s)", ([r["id"] for r in existing],))
            cursor.execute(
                "INSERT INTO assigned_wages (org_id, user_id, temp_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                (org_id, uid, temp_id, now, now),
            )
    return {"assigned": len(user_ids), "moved_from": moved_from}


def unassign_staff_from_wage(org_id: str, user_id: str, temp_id: str):
    """Remove one staff member from a wage template. False when there was no such assignment."""
    org_id = _require_org(org_id)
    with _transaction() as cursor:
        cursor.execute(
            "DELETE FROM assigned_wages WHERE org_id = %s AND user_id = %s AND temp_id = %s",
            (org_id, user_id, temp_id),
        )
        return cursor.rowcount > 0
