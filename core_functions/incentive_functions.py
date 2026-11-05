import uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException

from core_functions import payroll_validation
from utils.timezone import get_utc_now, IST
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor


def get_incentives_by_org(org_id: str, incentive_type: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        assign_join = "LEFT JOIN (SELECT incentive_id, COUNT(*) as assigned_count FROM assigned_incentives WHERE org_id = %s GROUP BY incentive_id) ai ON t.incentive_id = ai.incentive_id"
        assign_col = ", COALESCE(ai.assigned_count, 0) as assigned_count"
        
        if incentive_type == 'COUNT':
            query = f"""
                SELECT t.incentive_id, t.template_name, t.type, t.created_at, d.count_upto_basic_salary, d.next_count, d.each_incentive{assign_col}
                FROM incentive_templates t
                LEFT JOIN incentive_count_details d ON t.incentive_id = d.incentive_id AND d.user_id IS NULL
                {assign_join}
                WHERE t.org_id = %s AND t.type = %s
                ORDER BY t.created_at ASC
            """
        elif incentive_type == 'TURNOVER':
            query = f"""
                SELECT t.incentive_id, t.template_name, t.type, t.created_at, d.turnover_upto_basic_salary, d.after_target_incentive_percent{assign_col}
                FROM incentive_templates t
                LEFT JOIN incentive_turnover_details d ON t.incentive_id = d.incentive_id AND d.user_id IS NULL
                {assign_join}
                WHERE t.org_id = %s AND t.type = %s
                ORDER BY t.created_at ASC
            """
        elif incentive_type == 'OVERTIME':
            query = f"""
                SELECT t.incentive_id, t.template_name, t.type, t.created_at, d.duration_minutes, d.amount{assign_col}
                FROM incentive_templates t
                LEFT JOIN incentive_overtime_details d ON t.incentive_id = d.incentive_id AND d.user_id IS NULL
                {assign_join}
                WHERE t.org_id = %s AND t.type = %s
                ORDER BY t.created_at ASC
            """
        elif incentive_type == 'BONUS':
            query = f"""
                SELECT t.incentive_id, t.template_name, t.type, t.created_at, d.bonus_percent, d.month, d.year,
                       COALESCE((SELECT array_agg(ai2.user_id::text) FROM assigned_incentives ai2 WHERE ai2.incentive_id = t.incentive_id AND ai2.org_id = t.org_id), ARRAY[]::text[]) as user_ids{assign_col}
                FROM incentive_templates t
                LEFT JOIN incentive_bonus_details d ON t.incentive_id = d.incentive_id AND d.user_id IS NULL
                {assign_join}
                WHERE t.org_id = %s AND t.type = %s
                ORDER BY t.created_at ASC
            """
        else:
            query = """
                SELECT incentive_id, template_name, type, created_at
                FROM incentive_templates
                WHERE org_id = %s AND type = %s
                ORDER BY created_at ASC
            """
            cursor.execute(query, (org_id, incentive_type))
            return [dict(row) for row in cursor.fetchall()]
        
        cursor.execute(query, (org_id, org_id, incentive_type))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        return_connection(conn)

def get_incentive_details(incentive_id: str, incentive_type: str, orgs=None):
    """Get details for a specific incentive template. With `orgs` (the caller's organisations) a template of another
    organisation answers 404 like a missing one."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if orgs is not None and not _in_scope(_template_org(cursor, incentive_id), orgs):
            raise HTTPException(status_code=404, detail="Incentive not found")
        if incentive_type == 'COUNT':
            query = "SELECT * FROM incentive_count_details WHERE incentive_id = %s"
        elif incentive_type == 'TURNOVER':
            query = "SELECT * FROM incentive_turnover_details WHERE incentive_id = %s"
        elif incentive_type == 'OVERTIME':
            query = "SELECT * FROM incentive_overtime_details WHERE incentive_id = %s"
        elif incentive_type == 'BONUS':
            query = "SELECT * FROM incentive_bonus_details WHERE incentive_id = %s"
        else:
            return None
        
        cursor.execute(query, (incentive_id,))
        details = cursor.fetchall()
        return [dict(row) for row in details]
    finally:
        cursor.close()
        return_connection(conn)

# --------------------------------------------------------------------------- validation (D-15) and uniqueness
# One statement per unique field: the existing template of the organisation (user_id IS NULL = the template row) that
# already uses the value; the message names that template the way the app words it.
_DUP_SQL = {
    'count_upto': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_count_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'COUNT' AND d.user_id IS NULL AND d.count_upto_basic_salary = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
    'next_count': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_count_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'COUNT' AND d.user_id IS NULL AND d.next_count = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
    'each_incentive': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_count_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'COUNT' AND d.user_id IS NULL AND d.each_incentive = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
    'turnover_upto': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_turnover_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'TURNOVER' AND d.user_id IS NULL AND d.turnover_upto_basic_salary = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
    'duration': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_overtime_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'OVERTIME' AND d.user_id IS NULL AND d.duration_minutes = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
    'bonus_percent': """SELECT t.template_name FROM incentive_templates t
        JOIN incentive_bonus_details d ON d.incentive_id = t.incentive_id
        WHERE t.org_id = %s AND t.type = 'BONUS' AND d.user_id IS NULL AND d.bonus_percent = %s
          AND (%s::text IS NULL OR t.incentive_id <> %s::text) LIMIT 1""",
}
_DUP_MESSAGE = {
    'count_upto': "This value already exists in Count {name}",
    'next_count': "This value already exists in Count {name}",
    'each_incentive': "This value already exists in Count {name}",
    'turnover_upto': "This value already exists in Turnover {name}",
    'duration': "This duration already exists in Over Time {name}",
    'bonus_percent': "This percentage already exists in Bonus {name}",
}


def _lock(cursor, org_id: str, kind: str):
    """Serialise the writers of one organisation's templates of one type (duplicate check + write)."""
    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"incentive:{org_id}:{kind}",))


def _first(row, key):
    return row[key] if isinstance(row, dict) else row[0]


def _check_unique(cursor, org_id: str, field: str, value, exclude_id: Optional[str] = None):
    """409 when another template of the organisation (same type) already stores `value` in `field`."""
    cursor.execute(_DUP_SQL[field], (org_id, value, exclude_id, exclude_id))
    row = cursor.fetchone()
    if row:
        raise HTTPException(status_code=409, detail=_DUP_MESSAGE[field].format(name=_first(row, 'template_name')))


def _in_scope(template_org: Optional[str], orgs) -> bool:
    """True when no caller scope is known (`orgs` None: no token, as before) or the template belongs to one of the
    caller's organisations. A foreign template is treated exactly like a missing one by the callers (404)."""
    return orgs is None or (template_org is not None and template_org in orgs)


def _template_org(cursor, incentive_id: str) -> Optional[str]:
    cursor.execute("SELECT org_id FROM incentive_templates WHERE incentive_id = %s", (incentive_id,))
    row = cursor.fetchone()
    return _first(row, 'org_id') if row else None


def _template_name(value) -> str:
    return payroll_validation.parse_name(value, payroll_validation.MSG_TEMPLATE_NAME_REQUIRED,
                                         payroll_validation.MAX_INCENTIVE_NAME_LENGTH)


def _count_values(count_upto, next_count, each_incentive):
    return (payroll_validation.parse_positive_int(count_upto, "Count upto"),
            payroll_validation.parse_positive_int(next_count, "Next count"),
            payroll_validation.parse_amount(each_incentive, "Each incentive"))


def _turnover_values(turnover_upto, incentive_percent):
    pct = payroll_validation.parse_percent(incentive_percent, "Incentive percent")
    if pct == 0:
        raise payroll_validation.bad_request(payroll_validation.MSG_VALID_NUMBER)
    return payroll_validation.parse_amount(turnover_upto, "Turnover upto"), pct


def _overtime_values(duration_minutes, amount):
    return (payroll_validation.parse_duration_minutes(duration_minutes),
            payroll_validation.parse_amount(amount, "Amount"))


def _insert_template(cursor, incentive_id: str, org_id: str, kind: str, name: str):
    now = get_utc_now()
    cursor.execute("""
        INSERT INTO incentive_templates (incentive_id, org_id, type, template_name, created_at, updated_at)
        VALUES (%s, %s, %s::incentive_type, %s, %s, %s)
    """, (incentive_id, org_id, kind, name, now, now))


def _touch_template(cursor, incentive_id: str):
    cursor.execute("""
        UPDATE incentive_templates SET updated_at = %s WHERE incentive_id = %s
    """, (get_utc_now(), incentive_id))


def create_count_incentive(org_id: str, template_name: str, count_upto: int, next_count: int, each_incentive: float):
    """Create COUNT type incentive (whole numbers > 0, amount 0 < x <= 9,999,999.99, each value unique per organisation)"""
    name = _template_name(template_name)
    count_upto, next_count, each_incentive = _count_values(count_upto, next_count, each_incentive)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _lock(cursor, org_id, 'COUNT')
        _check_unique(cursor, org_id, 'count_upto', count_upto)
        _check_unique(cursor, org_id, 'next_count', next_count)
        _check_unique(cursor, org_id, 'each_incentive', each_incentive)

        incentive_id = str(uuid.uuid4())
        _insert_template(cursor, incentive_id, org_id, 'COUNT', name)
        cursor.execute("""
            INSERT INTO incentive_count_details (incentive_id, count_upto_basic_salary, next_count, each_incentive)
            VALUES (%s, %s, %s, %s)
        """, (incentive_id, count_upto, next_count, each_incentive))

        conn.commit()
        return {"incentive_id": incentive_id, "template_name": name}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def create_turnover_incentive(org_id: str, template_name: str, turnover_upto: float, incentive_percent: float):
    """Create TURNOVER type incentive (amount > 0 unique per organisation, percent 0 < x <= 100)"""
    name = _template_name(template_name)
    turnover_upto, incentive_percent = _turnover_values(turnover_upto, incentive_percent)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _lock(cursor, org_id, 'TURNOVER')
        _check_unique(cursor, org_id, 'turnover_upto', turnover_upto)

        incentive_id = str(uuid.uuid4())
        _insert_template(cursor, incentive_id, org_id, 'TURNOVER', name)
        cursor.execute("""
            INSERT INTO incentive_turnover_details (incentive_id, turnover_upto_basic_salary, after_target_incentive_percent)
            VALUES (%s, %s, %s)
        """, (incentive_id, turnover_upto, incentive_percent))

        conn.commit()
        return {"incentive_id": incentive_id, "template_name": name}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def create_overtime_incentive(org_id: str, template_name: str, duration_minutes: int, amount: float):
    """Create OVERTIME type incentive (duration 1..1440 minutes unique per organisation, amount > 0)"""
    name = _template_name(template_name)
    duration_minutes, amount = _overtime_values(duration_minutes, amount)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _lock(cursor, org_id, 'OVERTIME')
        _check_unique(cursor, org_id, 'duration', duration_minutes)

        incentive_id = str(uuid.uuid4())
        _insert_template(cursor, incentive_id, org_id, 'OVERTIME', name)
        cursor.execute("""
            INSERT INTO incentive_overtime_details (incentive_id, duration_minutes, amount)
            VALUES (%s, %s, %s)
        """, (incentive_id, duration_minutes, amount))

        conn.commit()
        return {"incentive_id": incentive_id, "template_name": name}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def create_bonus_incentive(org_id: str, template_name: str, bonus_percent: float, month: str, year: int):
    """Create BONUS type incentive (percent 0..100 unique per organisation, month = English month name)"""
    name = _template_name(template_name)
    bonus_percent = payroll_validation.parse_percent(bonus_percent, "Percentage")
    month = payroll_validation.parse_month_name(month)
    year = payroll_validation.parse_year(year)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        _lock(cursor, org_id, 'BONUS')
        _check_unique(cursor, org_id, 'bonus_percent', bonus_percent)

        incentive_id = str(uuid.uuid4())
        _insert_template(cursor, incentive_id, org_id, 'BONUS', name)
        cursor.execute("""
            INSERT INTO incentive_bonus_details (incentive_id, bonus_percent, month, year)
            VALUES (%s, %s, %s, %s)
        """, (incentive_id, bonus_percent, month, year))

        conn.commit()
        return {"incentive_id": incentive_id, "template_name": name}
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def update_count_incentive(incentive_id: str, count_upto: int, next_count: int, each_incentive: float, orgs=None):
    """Update COUNT type incentive"""
    count_upto, next_count, each_incentive = _count_values(count_upto, next_count, each_incentive)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        org_id = _template_org(cursor, incentive_id)
        if org_id is None or not _in_scope(org_id, orgs):
            return False
        _lock(cursor, org_id, 'COUNT')
        _check_unique(cursor, org_id, 'count_upto', count_upto, incentive_id)
        _check_unique(cursor, org_id, 'next_count', next_count, incentive_id)
        _check_unique(cursor, org_id, 'each_incentive', each_incentive, incentive_id)

        cursor.execute("""
            UPDATE incentive_count_details
            SET count_upto_basic_salary = %s, next_count = %s, each_incentive = %s
            WHERE incentive_id = %s
        """, (count_upto, next_count, each_incentive, incentive_id))
        updated = cursor.rowcount > 0
        _touch_template(cursor, incentive_id)

        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def update_turnover_incentive(incentive_id: str, turnover_upto: float, incentive_percent: float, orgs=None):
    """Update TURNOVER type incentive"""
    turnover_upto, incentive_percent = _turnover_values(turnover_upto, incentive_percent)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        org_id = _template_org(cursor, incentive_id)
        if org_id is None or not _in_scope(org_id, orgs):
            return False
        _lock(cursor, org_id, 'TURNOVER')
        _check_unique(cursor, org_id, 'turnover_upto', turnover_upto, incentive_id)

        cursor.execute("""
            UPDATE incentive_turnover_details
            SET turnover_upto_basic_salary = %s, after_target_incentive_percent = %s
            WHERE incentive_id = %s
        """, (turnover_upto, incentive_percent, incentive_id))
        updated = cursor.rowcount > 0
        _touch_template(cursor, incentive_id)

        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def update_overtime_incentive(incentive_id: str, duration_minutes: int, amount: float, orgs=None):
    """Update OVERTIME type incentive"""
    duration_minutes, amount = _overtime_values(duration_minutes, amount)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        org_id = _template_org(cursor, incentive_id)
        if org_id is None or not _in_scope(org_id, orgs):
            return False
        _lock(cursor, org_id, 'OVERTIME')
        _check_unique(cursor, org_id, 'duration', duration_minutes, incentive_id)

        cursor.execute("""
            UPDATE incentive_overtime_details
            SET duration_minutes = %s, amount = %s
            WHERE incentive_id = %s
        """, (duration_minutes, amount, incentive_id))
        updated = cursor.rowcount > 0
        _touch_template(cursor, incentive_id)

        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def update_bonus_incentive(incentive_id: str, bonus_percent: float, month: str, year: int, orgs=None):
    """Update BONUS type incentive (an empty month / year 0 keeps the stored value)"""
    bonus_percent = payroll_validation.parse_percent(bonus_percent, "Percentage")
    new_month = payroll_validation.parse_month_name(month) if month not in (None, '') else None
    new_year = payroll_validation.parse_year(year) if year not in (None, 0) else None
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        org_id = _template_org(cursor, incentive_id)
        if org_id is None or not _in_scope(org_id, orgs):
            return False
        _lock(cursor, org_id, 'BONUS')
        _check_unique(cursor, org_id, 'bonus_percent', bonus_percent, incentive_id)

        # Fetch existing values as fallback
        cursor.execute(
            "SELECT month, year FROM incentive_bonus_details WHERE incentive_id = %s LIMIT 1",
            (incentive_id,)
        )
        existing = cursor.fetchone()
        final_month = new_month if new_month is not None else (existing['month'] if existing else '')
        final_year  = new_year  if new_year  is not None else (existing['year']  if existing else None)

        cursor.execute("""
            UPDATE incentive_bonus_details
            SET bonus_percent = %s, month = %s, year = %s
            WHERE incentive_id = %s
        """, (bonus_percent, final_month, final_year, incentive_id))
        updated = cursor.rowcount > 0
        _touch_template(cursor, incentive_id)

        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)

def delete_incentive(incentive_id: str, incentive_type: str, orgs=None):
    """Delete an incentive template and its details. With `orgs` (the caller's organisations) a template of another
    organisation is left untouched and False is returned (the route answers 404)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if orgs is not None and not _in_scope(_template_org(cursor, incentive_id), orgs):
            return False
        if incentive_type == 'COUNT':
            cursor.execute("DELETE FROM incentive_count_details WHERE incentive_id = %s", (incentive_id,))
        elif incentive_type == 'TURNOVER':
            cursor.execute("DELETE FROM incentive_turnover_details WHERE incentive_id = %s", (incentive_id,))
        elif incentive_type == 'OVERTIME':
            cursor.execute("DELETE FROM incentive_overtime_details WHERE incentive_id = %s", (incentive_id,))
        elif incentive_type == 'BONUS':
            cursor.execute("DELETE FROM incentive_bonus_details WHERE incentive_id = %s", (incentive_id,))
        
        cursor.execute("DELETE FROM assigned_incentives WHERE incentive_id = %s", (incentive_id,))
        cursor.execute("DELETE FROM incentive_templates WHERE incentive_id = %s", (incentive_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        cursor.close()
        return_connection(conn)


def get_assigned_incentive_staff(org_id: str, incentive_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT ai.user_id, u.name, u.emp_id,
                   COALESCE(b.branch_name, '') as branch_name,
                   COALESCE(d.dept_name, '') as dept_name
            FROM assigned_incentives ai
            JOIN users u ON ai.user_id = u.user_id
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            WHERE ai.org_id = %s AND ai.incentive_id = %s
            ORDER BY u.name
        """, (org_id, incentive_id))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        return_connection(conn)


def get_unassigned_incentive_staff(org_id: str, incentive_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.user_id, u.name, u.emp_id,
                   COALESCE(b.branch_name, '') as branch_name,
                   COALESCE(d.dept_name, '') as dept_name
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = u.parent_id
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = u.parent_id
            WHERE u.parent_id = %s AND u.role_id IN (2, 3) AND u.status = 1
            AND u.user_id NOT IN (SELECT ai.user_id FROM assigned_incentives ai WHERE ai.org_id = %s AND ai.incentive_id = %s)
            ORDER BY u.name
        """, (org_id, org_id, incentive_id))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        return_connection(conn)


def assign_incentive_staff(org_id: str, user_ids: list, incentive_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        now = get_utc_now()
        user_ids = list(dict.fromkeys(user_ids))

        # the template and every staff member must belong to this organisation (nothing is written otherwise)
        cursor.execute("SELECT 1 FROM incentive_templates WHERE incentive_id = %s AND org_id = %s LIMIT 1",
                       (incentive_id, org_id))
        if cursor.fetchone() is None:
            raise HTTPException(status_code=404, detail="Incentive not found")
        if user_ids:
            cursor.execute("SELECT user_id FROM users WHERE user_id = ANY(%s) AND (org_id = %s OR parent_id = %s)",
                           (user_ids, org_id, org_id))
            if {_first(r, 'user_id') for r in cursor.fetchall()} != set(user_ids):
                raise HTTPException(status_code=404, detail="Staff member not found")

        cursor.execute(
            "SELECT count_upto_basic_salary, next_count, each_incentive FROM incentive_count_details WHERE incentive_id = %s AND user_id IS NULL",
            (incentive_id,)
        )
        count_template = cursor.fetchone()

        cursor.execute(
            "SELECT turnover_upto_basic_salary, after_target_incentive_percent FROM incentive_turnover_details WHERE incentive_id = %s AND user_id IS NULL",
            (incentive_id,)
        )
        turnover_template = cursor.fetchone()

        cursor.execute(
            "SELECT duration_minutes, amount FROM incentive_overtime_details WHERE incentive_id = %s AND user_id IS NULL",
            (incentive_id,)
        )
        overtime_template = cursor.fetchone()

        cursor.execute(
            "SELECT bonus_percent, month, year FROM incentive_bonus_details WHERE incentive_id = %s AND user_id IS NULL",
            (incentive_id,)
        )
        bonus_template = cursor.fetchone()

        for user_id in user_ids:
            cursor.execute("DELETE FROM assigned_incentives WHERE org_id = %s AND user_id = %s AND incentive_id = %s", (org_id, user_id, incentive_id))
            cursor.execute(
                "INSERT INTO assigned_incentives (org_id, user_id, incentive_id, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
                (org_id, user_id, incentive_id, now, now)
            )
            if count_template:
                cursor.execute(
                    "DELETE FROM incentive_count_details WHERE incentive_id = %s AND user_id = %s",
                    (incentive_id, user_id)
                )
                cursor.execute(
                    "INSERT INTO incentive_count_details (incentive_id, count_upto_basic_salary, next_count, each_incentive, user_id) VALUES (%s, %s, %s, %s, %s)",
                    (incentive_id, count_template[0], count_template[1], count_template[2], user_id)
                )
            if turnover_template:
                cursor.execute(
                    "DELETE FROM incentive_turnover_details WHERE incentive_id = %s AND user_id = %s",
                    (incentive_id, user_id)
                )
                cursor.execute(
                    "INSERT INTO incentive_turnover_details (incentive_id, turnover_upto_basic_salary, after_target_incentive_percent, user_id) VALUES (%s, %s, %s, %s)",
                    (incentive_id, turnover_template[0], turnover_template[1], user_id)
                )
            if overtime_template:
                cursor.execute(
                    "DELETE FROM incentive_overtime_details WHERE incentive_id = %s AND user_id = %s",
                    (incentive_id, user_id)
                )
                cursor.execute(
                    "INSERT INTO incentive_overtime_details (incentive_id, duration_minutes, amount, user_id) VALUES (%s, %s, %s, %s)",
                    (incentive_id, overtime_template[0], overtime_template[1], user_id)
                )
            if bonus_template:
                cursor.execute(
                    "DELETE FROM incentive_bonus_details WHERE incentive_id = %s AND user_id = %s",
                    (incentive_id, user_id)
                )
                b_month = bonus_template[1]
                b_year  = bonus_template[2]
                if not b_month or not b_year:
                    raise ValueError("Cannot assign bonus: template has null month or year")
                cursor.execute(
                    "INSERT INTO incentive_bonus_details (incentive_id, bonus_percent, month, year, user_id) VALUES (%s, %s, %s, %s, %s)",
                    (incentive_id, bonus_template[0], b_month, b_year, user_id)
                )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        return_connection(conn)


def unassign_incentive_staff(org_id: str, user_id: str, incentive_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM assigned_incentives WHERE org_id = %s AND user_id = %s AND incentive_id = %s", (org_id, user_id, incentive_id))
        deleted = cursor.rowcount > 0
        if not deleted:
            # nothing assigned in THIS organisation: the per-user detail rows (which carry no org column) are not touched
            conn.rollback()
            return False
        cursor.execute("DELETE FROM incentive_count_details WHERE incentive_id = %s AND user_id = %s", (incentive_id, user_id))
        cursor.execute("DELETE FROM incentive_turnover_details WHERE incentive_id = %s AND user_id = %s", (incentive_id, user_id))
        cursor.execute("DELETE FROM incentive_overtime_details WHERE incentive_id = %s AND user_id = %s", (incentive_id, user_id))
        cursor.execute("DELETE FROM incentive_bonus_details WHERE incentive_id = %s AND user_id = %s", (incentive_id, user_id))
        conn.commit()
        return deleted
    finally:
        cursor.close()
        return_connection(conn)
