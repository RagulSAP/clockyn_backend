"""Who may see / act on whom: the ONE staff-scope helper (WP6, planner D-10).

Rules (deny by default):
  * administrator (role 1) or an organisation-style id: every user of the organisation;
  * manager (role 2): themselves, the staff assigned to them (`users.manager_id = manager`), and, when the manager has
    rows in `manager_assignments` (the branch / department assignment table), the staff of those branches AND
    departments (a list that is absent adds no restriction of its own, exactly like the existing list queries);
    a manager without assignments and without assigned staff therefore sees only themselves;
  * staff (role 3): only themselves;
  * a caller id that is not a `users` row is an organisation id (the app's convention for the administrator: the home
    routes receive the organisation id) and gets organisation scope; the org filter every query already carries applies.

Today's behaviour is different: a manager without assignments is UNRESTRICTED (`get_manager_scope` returns `None`
lists, `core_functions/manager_functions.py`). The new rules apply ONLY while the environment variable
ENFORCE_MANAGER_SCOPE is on (default OFF: every query is byte-for-byte what it was). With the flag off `current()`
answers None without touching the database and `clause(None)` adds nothing.

Usage in a list query that already filters by organisation:
    sc = scope.current(cursor, caller_id)          # None unless ENFORCE_MANAGER_SCOPE=1
    sql, params = scope.clause(sc, "u")            # (" AND (...)", [...]) or ("", [])
    query += sql; query_params.extend(params)

Pure pieces (no database): `scope_predicate(scope, alias)` and `can_manage(scope, target_row)`.
"""
import os
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Tuple

ROLE_ADMIN, ROLE_MANAGER, ROLE_STAFF = 1, 2, 3
ENV_FLAG = "ENFORCE_MANAGER_SCOPE"
_TRUE = ("1", "true", "yes", "on")

KIND_ORG = "org"            # administrator, or an organisation id
KIND_MANAGER = "manager"
KIND_STAFF = "staff"
KIND_UNKNOWN = "unknown"    # a users row with an unexpected role: sees nothing


def enforced() -> bool:
    """ENFORCE_MANAGER_SCOPE (read on every call). Default OFF = today's behaviour."""
    return (os.getenv(ENV_FLAG) or "").strip().lower() in _TRUE


@dataclass(frozen=True)
class Scope:
    user_id: str
    kind: str
    org_id: str = ""
    branch_ids: Tuple[int, ...] = ()
    dept_ids: Tuple[str, ...] = ()

    @property
    def role_id(self) -> Optional[int]:
        return {KIND_ORG: ROLE_ADMIN, KIND_MANAGER: ROLE_MANAGER, KIND_STAFF: ROLE_STAFF}.get(self.kind)


def make_scope(user_id: str, role_id: Optional[int], org_id: str = "",
               branch_ids: Iterable = (), dept_ids: Iterable = ()) -> Scope:
    """Build a Scope from plain values (what `load_scope` reads from the database). `role_id` None = organisation id."""
    branches = []
    for b in branch_ids or ():
        try:
            branches.append(int(b))
        except (TypeError, ValueError):
            continue
    depts = [str(d) for d in (dept_ids or ()) if d is not None]
    if role_id is None or role_id == ROLE_ADMIN:
        return Scope(user_id, KIND_ORG, org_id or user_id)
    if role_id == ROLE_MANAGER:
        return Scope(user_id, KIND_MANAGER, org_id, tuple(sorted(set(branches))), tuple(sorted(set(depts))))
    if role_id == ROLE_STAFF:
        return Scope(user_id, KIND_STAFF, org_id)
    return Scope(user_id, KIND_UNKNOWN, org_id)


def load_scope(cursor, user_id: str) -> Scope:
    """Read the caller's role and assignments. Columns: users(user_id, role_id, parent_id), manager_assignments(user_id,
    branch_id, dept_id). The cursor is a RealDictCursor (rows are read by key)."""
    cursor.execute("SELECT role_id, parent_id FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if not row:
        return make_scope(user_id, None)
    role = row["role_id"]
    org = row["parent_id"] or ""
    if role == ROLE_MANAGER:
        cursor.execute("SELECT branch_id, dept_id FROM manager_assignments WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall() or []
        return make_scope(user_id, role, org, [r["branch_id"] for r in rows if r["branch_id"] is not None],
                          [r["dept_id"] for r in rows if r["dept_id"] is not None])
    if role == ROLE_ADMIN:
        return make_scope(user_id, role, user_id)
    return make_scope(user_id, role, org)


def _col(alias: Optional[str], name: str) -> str:
    return f"{alias}.{name}" if alias else name


def scope_predicate(scope: Scope, alias: Optional[str] = "u", include_org: bool = True) -> Tuple[str, list]:
    """(sql, params): a boolean expression selecting the `users` rows (aliased `alias`, or unqualified when alias is
    falsy) this caller may see. Always the deny-by-default rules (the env flag is applied by `current()`).
    `include_org=False` leaves the organisation equality out for queries that already carry it."""
    uid = _col(alias, "user_id")
    if scope.kind == KIND_ORG:
        if include_org:
            return f"{_col(alias, 'parent_id')} = %s", [scope.org_id]
        return "TRUE", []
    if scope.kind == KIND_STAFF:
        return f"{uid} = %s", [scope.user_id]
    if scope.kind == KIND_MANAGER:
        owned = f"({uid} = %s OR {_col(alias, 'manager_id')} = %s"
        params: list = [scope.user_id, scope.user_id]
        area = []
        if scope.branch_ids:
            area.append(f"{_col(alias, 'branch_id')} = ANY(%s)")
            params.append(list(scope.branch_ids))
        if scope.dept_ids:
            area.append(f"{_col(alias, 'dept_id')} = ANY(%s)")
            params.append(list(scope.dept_ids))
        if area:
            owned += " OR (" + " AND ".join(area) + ")"
        owned += ")"
        if include_org:
            return f"{_col(alias, 'parent_id')} = %s AND {owned}", [scope.org_id] + params
        return owned, params
    return "FALSE", []


def current(cursor, caller_id: Optional[str]) -> Optional[Scope]:
    """The caller's Scope while ENFORCE_MANAGER_SCOPE is on, else None WITHOUT any database access."""
    if not enforced() or not caller_id:
        return None
    return load_scope(cursor, caller_id)


def clause(sc: Optional[Scope], alias: Optional[str] = "u") -> Tuple[str, list]:
    """` AND (<predicate>)` to append to a query that already filters by organisation; ("", []) when `sc` is None."""
    if sc is None:
        return "", []
    sql, params = scope_predicate(sc, alias, include_org=False)
    if sql == "TRUE":
        return "", []
    return (" AND " + (sql if sql.startswith("(") else f"({sql})")), params


def _to_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def can_manage(scope: Scope, target: Mapping, target_org_id: Optional[str] = None) -> bool:
    """May the caller see / act on this `users` row? `target` carries user_id, role_id, parent_id (the organisation),
    branch_id, dept_id, manager_id (missing keys count as unknown). `target_org_id` overrides the organisation read
    from `target["parent_id"]` (rows created by the old add-staff code carry the manager's id there).
    Pure rules, always deny by default:
      administrator / organisation id: own organisation, roles 2 and 3 and itself;
      manager: itself, and role-3 staff of the organisation who are assigned to the manager (manager_id) or inside the
               assigned branches AND departments; never another manager and never an administrator;
      staff: only itself."""
    if not target:
        return False
    target_uid = target.get("user_id")
    if scope.kind == KIND_UNKNOWN:
        return False
    if target_uid is not None and target_uid == scope.user_id:
        return True
    if scope.kind == KIND_STAFF:
        return False
    org = target_org_id if target_org_id is not None else target.get("parent_id")
    if scope.kind == KIND_ORG:
        return bool(org) and org == scope.org_id and target.get("role_id") in (ROLE_MANAGER, ROLE_STAFF)
    # manager
    if target.get("role_id") != ROLE_STAFF:
        return False
    if target.get("manager_id") == scope.user_id and (not org or org == scope.org_id or org == scope.user_id):
        return True
    if not org or org != scope.org_id:
        return False
    if not scope.branch_ids and not scope.dept_ids:
        return False
    if scope.branch_ids and _to_int(target.get("branch_id")) not in scope.branch_ids:
        return False
    if scope.dept_ids and (target.get("dept_id") is None or str(target.get("dept_id")) not in scope.dept_ids):
        return False
    return True


def visible_user_ids(scope: Scope, rows: Iterable[Mapping]) -> List[str]:
    """user_id of every row of `rows` the caller can manage (helper for in-memory lists)."""
    return [r["user_id"] for r in rows if can_manage(scope, r)]
