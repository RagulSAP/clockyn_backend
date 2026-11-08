"""CRM leads: leads, categories, assignments, work/pipeline status and carry-forward requests.

Schema lives in clockyn_ddl_latest.sql / migrations (029); nothing here executes DDL.
Rules (decision D-17, enforced on the server):
  * one row per lead in every list, carrying the lead's pipeline status (lead_status);
  * an assignment is unique per (lead, staff): assigning twice is refused as a no-op;
  * a carry-forward request is persisted: the assignment's work status becomes carry_forward (the previous
    status is kept in prev_status), reject restores it, approve moves the lead's due date and the assignment
    goes back to pending (set CRM_CARRY_FORWARD_UNASSIGN=1 for the older behaviour: assignment removed);
  * "my leads" = assigned to the staff and due today (IST) or undated.
"""
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from psycopg2.extras import RealDictCursor
from pydantic import BaseModel

from core_functions.db_function import get_db_connection, return_connection
from utils.timezone import IST

logger = logging.getLogger(__name__)

PIPELINE_STATUSES = ('new', 'contacted', 'interested', 'converted', 'not_interested')
WORK_STATUSES = ('pending', 'working', 'completed', 'carry_forward')
ALREADY_ASSIGNED = "Lead is already assigned"

# Latest assignment of a lead (one row at most), used by every listing instead of a multiplying join.
_LATEST_ASSIGNMENT_ORDER = "ORDER BY x.updated_at DESC NULLS LAST, x.id DESC LIMIT 1"


class LeadCreate(BaseModel):
    org_id: str
    name: str
    mobile: str
    address: str
    category: str
    remarks: Optional[str] = ''
    required_date: Optional[str] = None


# ───────────────────────── helpers ─────────────────────────

def _today_ist() -> date:
    return datetime.now(IST).date()


def _unassign_on_approve() -> bool:
    return os.getenv("CRM_CARRY_FORWARD_UNASSIGN", "0").strip().lower() in ("1", "true", "yes")


def _parse_date(value, field: str) -> Optional[date]:
    """None / '' -> None, an ISO date string -> date, anything else -> 400."""
    if value is None or value == '':
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a date in YYYY-MM-DD format")


def _fields_set(model) -> set:
    fs = getattr(model, 'model_fields_set', None)
    if fs is None:
        fs = getattr(model, '__fields_set__', set())
    return set(fs)


def _server_error(action: str, exc: Exception):
    logger.exception("CRM: failed to %s", action)
    raise HTTPException(status_code=500, detail=f"Failed to {action}")


def _get_lead(cursor, leads_id: str, org_id: Optional[str] = None, orgs=None):
    """The lead row (org_id) or a 404. With org_id the lead must belong to that organisation. `orgs` (the caller's
    organisations, from the token; None without a token) narrows it further: the lead's own organisation must be one of
    them, also when the request names no org_id (the app's lead delete / status calls do not)."""
    if org_id and orgs is not None and org_id not in orgs:
        raise HTTPException(status_code=404, detail="Lead not found")
    if org_id:
        cursor.execute("SELECT org_id FROM leads_info WHERE leads_id = %s AND org_id = %s LIMIT 1", (leads_id, org_id))
    elif orgs is not None:
        cursor.execute("SELECT org_id FROM leads_info WHERE leads_id = %s AND org_id = ANY(%s) LIMIT 1",
                       (leads_id, list(orgs)))
    else:
        cursor.execute("SELECT org_id FROM leads_info WHERE leads_id = %s LIMIT 1", (leads_id,))
    lead = cursor.fetchone()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _require_employee(cursor, user_id: str, org_id: str):
    cursor.execute(
        "SELECT 1 AS ok FROM users WHERE user_id = %s AND (parent_id = %s OR org_id = %s) LIMIT 1",
        (user_id, org_id, org_id),
    )
    if not cursor.fetchone():
        raise HTTPException(status_code=404, detail="Employee not found")


def _category_id_for(cursor, org_id: str, category: str) -> str:
    """Existing category of the org (case-insensitive) or a newly inserted one."""
    name = (category or '').strip() or 'General'
    cursor.execute(
        "SELECT category_id FROM leads_category_info WHERE lower(category_name) = lower(%s) AND org_id = %s LIMIT 1",
        (name, org_id),
    )
    row = cursor.fetchone()
    if row:
        return row['category_id']
    category_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO leads_category_info (category_id, category_name, org_id) VALUES (%s, %s, %s)",
        (category_id, name, org_id),
    )
    return category_id


# ───────────────────────── leads ─────────────────────────

async def create_lead(lead_data: LeadCreate):
    required = _parse_date(lead_data.required_date, 'required_date')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        leads_id = str(uuid.uuid4())
        category_id = _category_id_for(cursor, lead_data.org_id, lead_data.category)
        cursor.execute(
            "INSERT INTO leads_info (leads_id, org_id, client_name, mobile_number, client_address, category_id, remarks, status, created_date, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'new', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (leads_id, lead_data.org_id, lead_data.name, lead_data.mobile, lead_data.address, category_id, lead_data.remarks or '', required),
        )
        conn.commit()
        return {"success": True, "message": "Lead created successfully", "leads_id": leads_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("create lead", e)
    finally:
        cursor.close()
        return_connection(conn)


LIST_LEADS_SQL = """
    SELECT l.leads_id, l.client_name, l.mobile_number, l.client_address,
           c.category_name, l.remarks, l.created_at, l.created_date,
           l.created_date AS required_date,
           l.status AS lead_status,
           la.user_id AS assigned_user_id,
           (SELECT u.name FROM users u WHERE u.user_id = la.user_id AND u.parent_id = %(org)s LIMIT 1) AS assigned_user_name,
           la.status AS work_status, la.updated_at AS assigned_at,
           COALESCE((SELECT json_agg(json_build_object(
                          'user_id', a2.user_id,
                          'name', (SELECT u2.name FROM users u2 WHERE u2.user_id = a2.user_id LIMIT 1),
                          'work_status', a2.status)
                      ORDER BY a2.updated_at DESC NULLS LAST, a2.id DESC)
                     FROM leads_assignment_info a2
                     WHERE a2.leads_id = l.leads_id AND a2.org_id = l.org_id), '[]'::json) AS assignees,
           EXISTS (SELECT 1 FROM carry_forward_requests cf
                   WHERE cf.leads_id = l.leads_id AND cf.org_id = l.org_id AND cf.status = 'pending') AS has_pending_cfr
    FROM leads_info l
    LEFT JOIN leads_category_info c ON c.category_id = l.category_id AND c.org_id = l.org_id
    LEFT JOIN LATERAL (
        SELECT x.user_id, x.status, x.updated_at
        FROM leads_assignment_info x
        WHERE x.leads_id = l.leads_id AND x.org_id = l.org_id
        """ + _LATEST_ASSIGNMENT_ORDER + """
    ) la ON true
    WHERE l.org_id = %(org)s
    ORDER BY l.created_at DESC, l.id DESC
"""


async def get_leads(org_id: str):
    """Admin list: exactly one row per lead (primary assignment = most recently updated)."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(LIST_LEADS_SQL, {"org": org_id})
        return {"success": True, "data": cursor.fetchall()}
    except Exception as e:
        _server_error("get leads", e)
    finally:
        cursor.close()
        return_connection(conn)


EMPLOYEE_LEADS_SQL = """
    SELECT l.leads_id, l.client_name, l.mobile_number, l.client_address,
           c.category_name, l.remarks, l.created_at, l.created_date,
           l.created_date AS required_date,
           l.status AS lead_status,
           la.user_id AS assigned_user_id,
           (SELECT u.name FROM users u WHERE u.user_id = la.user_id AND u.parent_id = %(org)s LIMIT 1) AS assigned_user_name,
           la.status AS work_status, la.updated_at AS assigned_at,
           EXISTS (SELECT 1 FROM carry_forward_requests cf
                   WHERE cf.leads_id = l.leads_id AND cf.org_id = l.org_id
                     AND cf.user_id = la.user_id AND cf.status = 'pending') AS has_pending_cfr
    FROM leads_info l
    LEFT JOIN leads_category_info c ON c.category_id = l.category_id AND c.org_id = l.org_id
    JOIN LATERAL (
        SELECT x.user_id, x.status, x.updated_at
        FROM leads_assignment_info x
        WHERE x.leads_id = l.leads_id AND x.org_id = l.org_id AND x.user_id = %(user)s
        """ + _LATEST_ASSIGNMENT_ORDER + """
    ) la ON true
    WHERE l.org_id = %(org)s
      AND (l.created_date = %(today)s OR l.created_date IS NULL)
    ORDER BY l.created_date ASC NULLS LAST, l.created_at DESC, l.id DESC
"""


async def get_employee_leads(org_id: str, user_id: str):
    """My leads: assigned to the staff and due today (IST) or undated; one row per lead."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(EMPLOYEE_LEADS_SQL, {"org": org_id, "user": user_id, "today": _today_ist()})
        return {"success": True, "data": cursor.fetchall()}
    except Exception as e:
        _server_error("get employee leads", e)
    finally:
        cursor.close()
        return_connection(conn)


async def assign_lead(leads_id: str, org_id: str, user_id: str, branch_id: Optional[int] = None, dept_id: Optional[str] = None, remarks: Optional[str] = '', request_date: Optional[str] = None):
    due = _parse_date(request_date, 'request_date')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _get_lead(cursor, leads_id, org_id)
        _require_employee(cursor, user_id, org_id)
        cursor.execute(
            "SELECT 1 AS ok FROM leads_assignment_info WHERE leads_id = %s AND user_id = %s AND org_id = %s LIMIT 1",
            (leads_id, user_id, org_id),
        )
        if cursor.fetchone():
            conn.rollback()
            return {"success": False, "message": ALREADY_ASSIGNED}
        # ON CONFLICT DO NOTHING (no target) also closes the double-tap race once ux_leads_assignment_lead_user exists.
        cursor.execute(
            "INSERT INTO leads_assignment_info (leads_id, assignment_id, org_id, branch_id, dept_id, user_id, remarks, status, request_date, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING",
            (leads_id, str(uuid.uuid4()), org_id, branch_id, dept_id, user_id, remarks or '', due),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            return {"success": False, "message": ALREADY_ASSIGNED}
        if due:
            cursor.execute(
                "UPDATE leads_info SET created_date = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND org_id = %s",
                (due, leads_id, org_id),
            )
        conn.commit()
        return {"success": True, "message": "Lead assigned successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("assign lead", e)
    finally:
        cursor.close()
        return_connection(conn)


async def delete_lead(leads_id: str, org_id: Optional[str] = None, orgs=None):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        lead = _get_lead(cursor, leads_id, org_id, orgs)
        lead_org = lead['org_id']
        cursor.execute("DELETE FROM carry_forward_requests WHERE leads_id = %s AND org_id = %s", (leads_id, lead_org))
        cursor.execute("DELETE FROM leads_assignment_info WHERE leads_id = %s AND org_id = %s", (leads_id, lead_org))
        cursor.execute("DELETE FROM leads_info WHERE leads_id = %s AND org_id = %s", (leads_id, lead_org))
        conn.commit()
        return {"success": True, "message": "Lead deleted successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("delete lead", e)
    finally:
        cursor.close()
        return_connection(conn)


async def update_lead(leads_id: str, lead_data: LeadCreate, orgs=None):
    """Update a lead of lead_data.org_id. The due date changes only when required_date is part of the body."""
    set_date = 'required_date' in _fields_set(lead_data)
    required = _parse_date(lead_data.required_date, 'required_date') if set_date else None
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _get_lead(cursor, leads_id, lead_data.org_id, orgs)
        category_id = _category_id_for(cursor, lead_data.org_id, lead_data.category)
        if set_date:
            cursor.execute(
                "UPDATE leads_info SET client_name = %s, mobile_number = %s, client_address = %s, category_id = %s, remarks = %s, created_date = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND org_id = %s",
                (lead_data.name, lead_data.mobile, lead_data.address, category_id, lead_data.remarks or '', required, leads_id, lead_data.org_id),
            )
        else:
            cursor.execute(
                "UPDATE leads_info SET client_name = %s, mobile_number = %s, client_address = %s, category_id = %s, remarks = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND org_id = %s",
                (lead_data.name, lead_data.mobile, lead_data.address, category_id, lead_data.remarks or '', leads_id, lead_data.org_id),
            )
        conn.commit()
        return {"success": True, "message": "Lead updated successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("update lead", e)
    finally:
        cursor.close()
        return_connection(conn)


# ───────────────────────── categories ─────────────────────────

async def get_categories(org_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT category_id, category_name FROM leads_category_info WHERE org_id = %s ORDER BY category_name", (org_id,))
        return {"success": True, "data": cursor.fetchall()}
    except Exception as e:
        _server_error("get categories", e)
    finally:
        cursor.close()
        return_connection(conn)


async def create_category(org_id: str, category_name: str):
    name = (category_name or '').strip()
    if not name:
        return {"success": False, "message": "Category name is required"}
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT category_id FROM leads_category_info WHERE lower(category_name) = lower(%s) AND org_id = %s LIMIT 1",
            (name, org_id),
        )
        if cursor.fetchone():
            conn.rollback()
            return {"success": False, "message": "Category already exists"}
        category_id = str(uuid.uuid4())
        cursor.execute("INSERT INTO leads_category_info (category_id, category_name, org_id) VALUES (%s, %s, %s)", (category_id, name, org_id))
        conn.commit()
        return {"success": True, "message": "Category created successfully", "category_id": category_id}
    except Exception as e:
        conn.rollback()
        _server_error("create category", e)
    finally:
        cursor.close()
        return_connection(conn)


async def delete_category(category_id: str, org_id: Optional[str] = None, orgs=None):
    """Delete a category of the caller's organisation. org_id is derived from the category row when absent; a category
    of another organisation behaves as not found."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        if org_id and orgs is not None and org_id not in orgs:
            conn.rollback()                                    # a foreign org named: the same answer as not found
            raise HTTPException(status_code=404, detail="Category not found")
        elif org_id:
            cursor.execute("SELECT org_id FROM leads_category_info WHERE category_id = %s AND org_id = %s LIMIT 1", (category_id, org_id))
        elif orgs is not None:
            cursor.execute("SELECT org_id FROM leads_category_info WHERE category_id = %s AND org_id = ANY(%s) LIMIT 1", (category_id, list(orgs)))
        else:
            cursor.execute("SELECT org_id FROM leads_category_info WHERE category_id = %s LIMIT 1", (category_id,))
        category = cursor.fetchone()
        if not category:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Category not found")
        cursor.execute("DELETE FROM leads_category_info WHERE category_id = %s AND org_id = %s", (category_id, category['org_id']))
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Category not found")
        conn.commit()
        return {"success": True, "message": "Category deleted successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("delete category", e)
    finally:
        cursor.close()
        return_connection(conn)


# ───────────────────────── assignment / status ─────────────────────────

async def update_lead_assignment(leads_id: str, user_id: str, branch_id: Optional[int] = None, dept_id: Optional[str] = None, org_id: Optional[str] = None, orgs=None):
    """Reassign: afterwards the lead has exactly one assignment, for user_id (a row the staff already holds keeps its status)."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        lead = _get_lead(cursor, leads_id, org_id, orgs)
        lead_org = lead['org_id']
        _require_employee(cursor, user_id, lead_org)
        cursor.execute(
            "SELECT id, user_id FROM leads_assignment_info WHERE leads_id = %s AND org_id = %s ORDER BY updated_at DESC NULLS LAST, id DESC",
            (leads_id, lead_org),
        )
        rows = cursor.fetchall() or []
        keep = next((r for r in rows if r['user_id'] == user_id), None)
        if keep:
            if len(rows) > 1:
                cursor.execute(
                    "DELETE FROM leads_assignment_info WHERE leads_id = %s AND org_id = %s AND id <> %s",
                    (leads_id, lead_org, keep['id']),
                )
            cursor.execute(
                "UPDATE leads_assignment_info SET branch_id = %s, dept_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (branch_id, dept_id, keep['id']),
            )
        else:
            if rows:
                cursor.execute("DELETE FROM leads_assignment_info WHERE leads_id = %s AND org_id = %s", (leads_id, lead_org))
            cursor.execute(
                "INSERT INTO leads_assignment_info (leads_id, assignment_id, org_id, branch_id, dept_id, user_id, remarks, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, '', 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (leads_id, str(uuid.uuid4()), lead_org, branch_id, dept_id, user_id),
            )
        conn.commit()
        return {"success": True, "message": "Lead assignment updated successfully"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("update assignment", e)
    finally:
        cursor.close()
        return_connection(conn)


async def update_work_status(leads_id: str, work_status: str, user_id: Optional[str] = None, org_id: Optional[str] = None, orgs=None):
    """Work status lives on the assignment row of the acting user (user_id) in the lead's organisation. The route fills
    user_id from the token when the request omits it; without any acting user nothing is changed (400)."""
    status = str(work_status or '').strip().lower().replace(' ', '_').replace('-', '_')
    if status not in WORK_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(WORK_STATUSES)}")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        lead = _get_lead(cursor, leads_id, org_id, orgs)
        cursor.execute(
            "UPDATE leads_assignment_info SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND user_id = %s AND org_id = %s",
            (status, leads_id, user_id, lead['org_id']),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Lead not found")
        conn.commit()
        return {"success": True, "message": f"Work status updated to {status}"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("update work status", e)
    finally:
        cursor.close()
        return_connection(conn)


async def update_lead_status(leads_id: str, status: str, org_id: Optional[str] = None, orgs=None):
    """Pipeline status on the lead (new/contacted/interested/converted/not_interested) of its organisation."""
    if status not in PIPELINE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(PIPELINE_STATUSES)}")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        lead = _get_lead(cursor, leads_id, org_id, orgs)
        cursor.execute(
            "UPDATE leads_info SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND org_id = %s",
            (status, leads_id, lead['org_id']),
        )
        if cursor.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Lead not found")
        conn.commit()
        return {"success": True, "message": f"Lead status updated to {status}"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("update lead status", e)
    finally:
        cursor.close()
        return_connection(conn)


# ───────────────────────── carry forward ─────────────────────────

async def request_carry_forward(leads_id: str, org_id: str, user_id: str, description: str, forward_date: Optional[str] = None):
    text = (description or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail="Description is required")
    today = _today_ist()
    target = _parse_date(forward_date, 'forward_date') or (today + timedelta(days=1))
    if target < today:
        raise HTTPException(status_code=400, detail="forward_date cannot be in the past")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        _get_lead(cursor, leads_id, org_id)
        cursor.execute(
            "SELECT id FROM leads_assignment_info WHERE leads_id = %s AND user_id = %s AND org_id = %s LIMIT 1",
            (leads_id, user_id, org_id),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Lead is not assigned to this employee")
        cursor.execute(
            "SELECT request_id FROM carry_forward_requests WHERE leads_id = %s AND user_id = %s AND org_id = %s AND status = 'pending' LIMIT 1",
            (leads_id, user_id, org_id),
        )
        if cursor.fetchone():
            conn.rollback()
            return {"success": False, "message": "A pending carry forward request already exists"}
        request_id = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO carry_forward_requests (request_id, leads_id, org_id, user_id, description, request_date, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending', CURRENT_TIMESTAMP)",
            (request_id, leads_id, org_id, user_id, text, target),
        )
        # Persist the carry-forward state on the assignment and remember the status to restore on reject.
        cursor.execute(
            "UPDATE leads_assignment_info SET prev_status = CASE WHEN status = 'carry_forward' THEN prev_status ELSE status END, "
            "status = 'carry_forward', description = %s, request_date = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE leads_id = %s AND user_id = %s AND org_id = %s",
            (text, target, leads_id, user_id, org_id),
        )
        conn.commit()
        return {"success": True, "message": "Carry forward request submitted", "request_id": request_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("submit carry forward request", e)
    finally:
        cursor.close()
        return_connection(conn)


MY_CARRY_FORWARD_SQL = """
    SELECT cf.request_id, cf.leads_id, cf.description,
           cf.request_date, cf.status, cf.actioned_at, cf.created_at,
           l.client_name, l.created_date AS forwarded_date,
           l.mobile_number, l.client_address, c.category_name,
           la.status AS work_status
    FROM carry_forward_requests cf
    JOIN leads_info l ON l.leads_id = cf.leads_id AND l.org_id = cf.org_id
    LEFT JOIN leads_category_info c ON c.category_id = l.category_id AND c.org_id = l.org_id
    LEFT JOIN LATERAL (
        SELECT x.status
        FROM leads_assignment_info x
        WHERE x.leads_id = cf.leads_id AND x.user_id = cf.user_id AND x.org_id = cf.org_id
        """ + _LATEST_ASSIGNMENT_ORDER + """
    ) la ON true
    WHERE cf.org_id = %s AND cf.user_id = %s
    ORDER BY cf.created_at DESC, cf.id DESC
"""


async def get_my_carry_forward_requests(org_id: str, user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(MY_CARRY_FORWARD_SQL, (org_id, user_id))
        return {"success": True, "data": cursor.fetchall()}
    except Exception as e:
        _server_error("fetch carry forward requests", e)
    finally:
        cursor.close()
        return_connection(conn)


ORG_CARRY_FORWARD_SQL = """
    SELECT cf.request_id, cf.leads_id, cf.user_id, cf.description,
           cf.request_date, cf.status, cf.created_at, cf.actioned_at,
           l.client_name, l.created_date AS lead_date,
           (SELECT u.name FROM users u WHERE u.user_id = cf.user_id LIMIT 1) AS employee_name,
           cf.request_date AS crf_request_date
    FROM carry_forward_requests cf
    JOIN leads_info l ON l.leads_id = cf.leads_id AND l.org_id = cf.org_id
    WHERE cf.org_id = %s
    ORDER BY cf.created_at DESC, cf.id DESC
"""


async def get_carry_forward_requests(org_id: str):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(ORG_CARRY_FORWARD_SQL, (org_id,))
        return {"success": True, "data": cursor.fetchall()}
    except Exception as e:
        _server_error("fetch carry forward requests", e)
    finally:
        cursor.close()
        return_connection(conn)


async def action_carry_forward(request_id: str, action: str, admin_id: str, orgs=None):
    if action not in ('approved', 'rejected'):
        raise HTTPException(status_code=400, detail="action must be 'approved' or 'rejected'")
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT request_id, leads_id, user_id, org_id, request_date, status FROM carry_forward_requests WHERE request_id = %s",
            (request_id,),
        )
        req = cursor.fetchone()
        if not req or (orgs is not None and req['org_id'] not in orgs):
            raise HTTPException(status_code=404, detail="Request not found")     # a foreign request is a missing one
        if req['status'] != 'pending':
            raise HTTPException(status_code=409, detail="Request has already been actioned")
        cursor.execute(
            "UPDATE carry_forward_requests SET status = %s, actioned_by = %s, actioned_at = CURRENT_TIMESTAMP WHERE request_id = %s AND status = 'pending'",
            (action, admin_id, request_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=409, detail="Request has already been actioned")
        if action == 'approved':
            target = _parse_date(req['request_date'], 'request_date')
            cursor.execute(
                "UPDATE leads_info SET created_date = %s, updated_at = CURRENT_TIMESTAMP WHERE leads_id = %s AND org_id = %s",
                (target, req['leads_id'], req['org_id']),
            )
            if _unassign_on_approve():
                cursor.execute(
                    "DELETE FROM leads_assignment_info WHERE leads_id = %s AND user_id = %s AND org_id = %s",
                    (req['leads_id'], req['user_id'], req['org_id']),
                )
            else:
                cursor.execute(
                    "UPDATE leads_assignment_info SET status = 'pending', prev_status = NULL, updated_at = CURRENT_TIMESTAMP "
                    "WHERE leads_id = %s AND user_id = %s AND org_id = %s AND status = 'carry_forward'",
                    (req['leads_id'], req['user_id'], req['org_id']),
                )
        else:
            cursor.execute(
                "UPDATE leads_assignment_info SET "
                "status = CASE WHEN prev_status IS NULL OR prev_status = 'carry_forward' THEN 'pending' ELSE prev_status END, "
                "prev_status = NULL, description = NULL, request_date = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE leads_id = %s AND user_id = %s AND org_id = %s AND status = 'carry_forward'",
                (req['leads_id'], req['user_id'], req['org_id']),
            )
        conn.commit()
        return {"success": True, "message": f"Request {action}"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        _server_error("action carry forward request", e)
    finally:
        cursor.close()
        return_connection(conn)
