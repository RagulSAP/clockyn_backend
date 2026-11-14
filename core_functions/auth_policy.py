"""Authentication policy as DATA: which routes are public, which belong to the platform operator, which role (and which
manager permission) may call the rest, and which request keys carry ids that must belong to the caller.

Pure module (no FastAPI, no database): `core_functions/auth_dependency.py` applies it, the tests read it.

Path language: a pattern is `/seg/seg/...`; `{}` matches exactly one path segment; a trailing `/*` matches the prefix
itself and everything below it. Paths are normalised first (query string removed, repeated and trailing slashes dropped).
Methods are "*" or a tuple of upper-case HTTP methods. Rules are tried in order, the first match wins, so specific
rules come before general ones.

Classes (see `classify`):
  public         no token needed (login, registration, health, version, static, signed webhook, ...).
  operator       platform-operator routes: NEVER satisfied by an app token; they need the operator mechanism
                 (`_require_gen_admin` in router/auth.py), which is hardened separately.
  authenticated  everything else: a valid token of a user of the same organisation, then ROLE_RULES narrow it down.

The default for an unlisted path is "any authenticated user of the same organisation" (conservative: a wrong guess in
ROLE_RULES would lock a real user out of the app, a missing entry only leaves today's behaviour in place).
"""
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROLE_ADMIN, ROLE_MANAGER, ROLE_STAFF = 1, 2, 3
ALL_ROLES = (ROLE_ADMIN, ROLE_MANAGER, ROLE_STAFF)
NON_GET = ("POST", "PUT", "PATCH", "DELETE")

PUBLIC, OPERATOR, AUTHENTICATED = "public", "operator", "authenticated"


@dataclass(frozen=True)
class Identity:
    """The caller, as proven by a valid token and the current database row of the user."""
    user_id: str
    org_id: str            # users.org_id (admin) / users.parent_id (manager, staff); '' when unknown
    role_id: int
    epoch: int = 0
    token_exp: int = 0


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    status: int = 403      # HTTP status to answer when not allowed
    needs: Tuple[str, ...] = ()   # manager permissions of which one is required (any-of), for the message


ALLOW = Decision(True)


# ======================================================================================== public / operator data
# (methods, pattern). Everything here is explicit on purpose: tests/test_route_auth_matrix.py asserts that every entry
# still matches a mounted route, so the lists cannot silently go stale.
PUBLIC_RULES: Tuple[Tuple[object, str], ...] = (
    ("*", "/"),
    ("*", "/health"),
    ("*", "/ready"),
    ("*", "/docs"),
    ("*", "/docs/oauth2-redirect"),
    ("*", "/redoc"),
    ("*", "/openapi.json"),
    ("*", "/version/check"),
    # sign-in / registration (the token is issued by these)
    ("*", "/auth/config"),
    ("*", "/auth/generate-otp"),
    ("*", "/auth/validate-otp"),
    ("*", "/auth/verify-device-token"),
    ("*", "/auth/login"),
    ("*", "/auth/employee-login"),
    ("*", "/auth/register"),
    ("*", "/auth/register-company"),
    ("*", "/auth/logout"),
    # platform operator sign-in (phone + SMS code; not an app user)
    ("*", "/auth/admin-login"),
    ("*", "/auth/admin-login/verify-otp"),
    ("*", "/auth/check-mobile/*"),
    ("*", "/auth/check-email/*"),
    ("*", "/auth/org-logo/*"),
    # profile photo: reading the URL is public; WRITING it (POST) needs the owner / an organisation admin (WP12 K2)
    (("GET",), "/profile-photo/{}"),
    # invoice PDF: a signed, expiring link needs no token; without a signature the handler itself demands the admin's
    # token when REQUIRE_AUTH=1 (utils/signed_urls.py, utils/email.py::download_invoice)
    (("GET",), "/invoice/download"),
    # device_token (query) based SSE for the signed-in device
    ("*", "/auth/session-events/*"),
    # the staff submits this BEFORE login (user_id + org_id); the existing 1-pending-and-3-per-24h rule limits it
    (("POST",), "/requests/device-change/submit"),
    # Refer & Earn: the signup screen checks a typed referral code before the account exists (rate limited per IP,
    # answers only the referrer's organisation name); every other /referral/* route is an organisation-admin route
    (("GET",), "/referral/validate"),
    # payment / messaging webhooks carry their own HMAC signature. (No /static, /files or /uploads rule: the app mounts no
    # static route and serves files from S3 presigned URLs; a public rule for a route that does not exist would make the
    # first route someone adds under that prefix public. tests/test_auth_route_sweep.py keeps every rule tied to a route.)
    ("*", "/webhook/*"),
)

OPERATOR_RULES: Tuple[Tuple[object, str], ...] = (
    ("*", "/gen-dashboard/*"),
    ("*", "/auth/gen-admin-users/*"),
    # plan and coupon administration (the app only reads /plans/get_plans, /plans/subscription, /plans/plan-status,
    # /plans/coupon/first and /plans/coupon/validate)
    ("*", "/plans/coupons/*"),
    (("GET",), "/plans/all"),
    (("POST",), "/plans"),
    (("PUT", "DELETE"), "/plans/{}"),
    # support / suggestion / attribution / invoice administration lists
    (("GET",), "/support/requests/all"),
    (("PUT",), "/support/update/{}"),
    (("GET",), "/company/deletion-requests"),
    (("GET",), "/company/suggest-feature"),
    (("GET",), "/company/suggest-feature/with-replies"),
    (("POST",), "/company/suggest-feature/{}/reply"),
    (("GET",), "/invoice/bulk-download"),
    (("GET",), "/invoice/bulk-download-link"),     # mints the signed link of the bulk download (WP12 K2)
    (("GET",), "/attribution/reports/meta-attribution"),
    (("POST",), "/admin/lifecycle/purge-deleted"),    # retention purge of soft-deleted staff (WP6, D-14)
)


# ======================================================================================== role rules
@dataclass(frozen=True)
class RoleRule:
    methods: object                      # "*" or tuple of methods
    pattern: str
    roles: Tuple[int, ...]               # roles that may call it
    perms: Tuple[str, ...] = ()          # for role 2 (manager): needs ANY of these permissions (empty = none needed)
    note: str = ""


# Manager permission names are the exact strings of settings (see managerShortcuts.ts in the app).
P_STAFF, P_BRANCH, P_REPORTS = "Staff Management", "Branch Management", "Attendance Reports"
P_SALARY, P_HOLIDAY, P_SHIFT = "Salary Management", "Holiday Management", "Shift Management"
P_LEADS, P_INCENTIVE, P_COMPANY = "Leads Management", "Incentive Management", "Company Settings"
P_REQUESTS = "Requests Management"

ADMIN_ONLY = (ROLE_ADMIN,)
ADMIN_MANAGER = (ROLE_ADMIN, ROLE_MANAGER)

ROLE_RULES: Tuple[RoleRule, ...] = (
    # --- explicit staff-allowed exceptions that sit under an otherwise restricted prefix (own payslip, own advances)
    RoleRule(("GET",), "/api/salary/employee/{}/{}", ALL_ROLES, note="own payslip assignment"),
    RoleRule(("GET",), "/api/salary/preview/{}/{}/{}", ALL_ROLES, note="own payslip preview"),
    RoleRule(("GET",), "/api/advance/user/{}/{}", ALL_ROLES, note="own advances"),
    RoleRule(("GET",), "/api/announcements/employee/{}", ALL_ROLES, note="announcements for me"),
    RoleRule(("GET",), "/api/incentive/overtime-report/{}", ADMIN_MANAGER, (P_REPORTS, P_INCENTIVE)),

    # --- administrator only: billing, manager administration, destructive company/staff operations
    RoleRule("*", "/orders/*", ADMIN_ONLY, note="plan purchase"),
    RoleRule("*", "/payments/*", ADMIN_ONLY, note="payment verification"),
    RoleRule("*", "/referral/*", ADMIN_ONLY, note="Refer & Earn page of the organisation (GET /referral/validate is public)"),
    RoleRule("*", "/live-chat/*", ADMIN_ONLY, note="live chat with Clockyn support (organisation admin only, not plan-gated)"),
    RoleRule("*", "/invoice/*", ADMIN_ONLY, note="invoices (bulk-download is operator)"),
    RoleRule(("POST",), "/managers/assign", ADMIN_ONLY, note="promote staff to manager"),
    RoleRule(("DELETE",), "/managers/{}", ADMIN_ONLY, note="demote manager"),
    RoleRule(("POST",), "/managers/{}/permissions", ADMIN_ONLY, note="grant manager permissions"),
    RoleRule(("POST",), "/api/attendance-rule/{}", ADMIN_ONLY, note="attendance rule write"),
    RoleRule(("DELETE",), "/company/delete/{}/{}", ADMIN_ONLY, note="delete a company"),
    RoleRule(("DELETE",), "/addstaff/{}/permanent", ADMIN_ONLY, note="irreversible staff delete"),
    RoleRule(("DELETE",), "/staff-location/cleanup", ADMIN_ONLY, note="location history purge"),
    RoleRule(("POST",), "/auth/upload-org-logo/{}", ADMIN_ONLY, note="company logo"),
    RoleRule(NON_GET, "/department/*", ADMIN_ONLY, note="departments have no manager permission"),

    # --- administrator, or manager with the permission of the area
    RoleRule(("POST", "PUT"), "/company/add", ADMIN_MANAGER, (P_COMPANY,)),
    RoleRule(("PUT",), "/company/update/{}", ADMIN_MANAGER, (P_COMPANY,)),
    RoleRule(("POST",), "/company/update-logo/{}", ADMIN_MANAGER, (P_COMPANY,)),
    RoleRule(NON_GET, "/addstaff/*", ADMIN_MANAGER, (P_STAFF,)),
    RoleRule(NON_GET, "/branch/*", ADMIN_MANAGER, (P_BRANCH,)),
    RoleRule(NON_GET, "/shift/*", ADMIN_MANAGER, (P_SHIFT,)),
    RoleRule(NON_GET, "/api/holidays/*", ADMIN_MANAGER, (P_HOLIDAY,)),
    RoleRule("*", "/api/salary/*", ADMIN_MANAGER, (P_SALARY,), note="salary templates and sheets"),
    RoleRule("*", "/api/wages/*", ADMIN_MANAGER, (P_SALARY,)),
    RoleRule("*", "/api/daily-wages/*", ADMIN_MANAGER, (P_SALARY,)),
    RoleRule("*", "/api/hourly-charges/*", ADMIN_MANAGER, (P_SALARY,)),
    RoleRule("*", "/api/advance/*", ADMIN_MANAGER, (P_SALARY,), note="advance write / org list"),
    RoleRule("*", "/salary/*", ADMIN_MANAGER, (P_SALARY,), note="salary payments"),
    RoleRule("*", "/api/incentive/*", ADMIN_MANAGER, (P_INCENTIVE,)),
    RoleRule("*", "/api/reports/*", ADMIN_MANAGER, (P_REPORTS,)),
    RoleRule(("POST",), "/api/leads/assign", ADMIN_MANAGER, (P_LEADS,)),
    # POST /api/leads/categories is deliberately NOT here: the staff "add lead" sheet (LeadForm -> CategoryPicker) lets an
    # employee create a category, so it is open to every signed-in role (only deleting a category is a manager action)
    RoleRule(("DELETE",), "/api/leads/categories/{}", ADMIN_MANAGER, (P_LEADS,)),
    RoleRule(("PUT",), "/api/leads/carry-forward/{}/action", ADMIN_MANAGER, (P_LEADS,)),
    RoleRule(("GET",), "/api/leads/carry-forward/requests/{}", ADMIN_MANAGER, (P_LEADS,)),
    RoleRule(("PUT",), "/api/leads/assignment/{}", ADMIN_MANAGER, (P_LEADS,)),
    RoleRule(("POST",), "/requests/update-status", ADMIN_MANAGER, (P_REQUESTS,), note="approve / reject requests"),

    # --- administrator or manager (not staff); no finer permission is known for these screens
    RoleRule(("GET",), "/requests/admin/{}", ADMIN_MANAGER, note="request inbox (also feeds the manager badge)"),
    RoleRule("*", "/expenses/admin/*", ADMIN_MANAGER, note="expense approvals"),
    RoleRule("*", "/api/announcements/*", ADMIN_MANAGER, note="create / list / delete announcements"),
    RoleRule(("POST",), "/announcement/send-all", ADMIN_MANAGER),
    RoleRule(("POST",), "/announcement/send-branch-dept", ADMIN_MANAGER),
    RoleRule(("GET",), "/announcement/all/{}", ADMIN_MANAGER),
    RoleRule(("GET",), "/announcement/branch-dept/{}", ADMIN_MANAGER),
    RoleRule(("GET",), "/staff-location/locations", ADMIN_MANAGER, note="live map"),
    RoleRule(("GET",), "/staff-location/history/{}", ADMIN_MANAGER),
    RoleRule(("GET",), "/staff-location/stats", ADMIN_MANAGER),
    RoleRule(("GET",), "/home/staff-locations", ADMIN_MANAGER),
    RoleRule(("GET",), "/home/staff-punch-locations", ADMIN_MANAGER),
    RoleRule(("PUT",), "/home/update-attendance-punch", ADMIN_MANAGER, note="edit a day's punches"),
)


# ======================================================================================== id keys (data)
ORG_KEYS: Tuple[str, ...] = ("org_id",)
USER_KEYS: Tuple[str, ...] = ("user_id", "staff_id", "employee_id", "manager_id",     # manager_id: /api/reports/manager/{manager_id}/..., /manager/staff?manager_id=
                              "receiver_id", "other_user_id")                          # the chat partner (PEER_KEYS below)
SELF_KEYS: Tuple[str, ...] = ("sender_id", "action_by")   # the actor of the call: always the caller
# Peer keys name the OTHER party of a conversation (/chat/send receiver_id, /chat/messages/{me}/{other_user_id}). Same
# rule as the user keys (a user of an allowed organisation, an unknown id passes to the handler's 404) EXCEPT that staff
# (role 3) may name a colleague too: their organisation is looked up and must be an allowed one (chat between staff).
PEER_KEYS: Tuple[str, ...] = ("receiver_id", "other_user_id")

# (methods, pattern, {key: kind}): routes where a key does not play its usual part. kind: self | peer | user | org.
# /chat/mark-read: the viewer is `receiver_id` (the caller), `sender_id` is the conversation partner.
KEY_KIND_OVERRIDES: Tuple[Tuple[object, str, Mapping[str, str]], ...] = (
    (("PUT",), "/chat/mark-read", {"receiver_id": "self", "sender_id": "peer"}),
)

# (methods, pattern, keys): requests where the user-key check is skipped for these keys (read-only, harmless lookups)
ID_CHECK_EXEMPT: Tuple[Tuple[object, str, Tuple[str, ...]], ...] = (
    (("GET",), "/auth/employee-profile/{}", ("user_id",)),   # a colleague's profile photo URL (chat lists)
)

# (methods, pattern, key): routes whose path parameter `key` must be the CALLER's own user id. Stricter than the
# organisation check above (which lets an admin or manager name any user of the organisation): these read a phone number
# or destroy an account, so not even an administrator may act for somebody else. Enforced by the route dependency
# `core_functions/owner_guard.py::require_owner` whenever a token identifies the caller (WP12 K2).
OWNER_ONLY_RULES: Tuple[Tuple[object, str, str], ...] = (
    (("GET",), "/auth/user-mobile/{}", "user_id"),
    (("DELETE",), "/company/delete-account/{}", "user_id"),
    (("POST",), "/company/request-deletion/{}", "user_id"),
)

# Request bodies are only peeked when small; larger declared bodies are inspected when the framework already read them.
MAX_PEEK_BODY_BYTES = 64 * 1024


# ======================================================================================== matching
def normalise_path(path: str) -> str:
    path = (path or "/").split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in path.split("/") if p != ""]
    return "/" + "/".join(parts)


def _split(pattern: str) -> Tuple[str, ...]:
    return tuple(p for p in pattern.split("/") if p != "")


def pattern_matches(pattern: str, path: str) -> bool:
    pat, got = _split(pattern), _split(normalise_path(path))
    if pat and pat[-1] == "*":
        head = pat[:-1]
        return len(got) >= len(head) and all(h == "{}" or h == g for h, g in zip(head, got))
    return len(pat) == len(got) and all(h == "{}" or h == g for h, g in zip(pat, got))


def _method_ok(methods, method: str) -> bool:
    return methods == "*" or (method or "").upper() in methods


def _first(rules: Iterable, method: str, path: str):
    for rule in rules:
        methods, pattern = rule[0], rule[1]
        if _method_ok(methods, method) and pattern_matches(pattern, path):
            return rule
    return None


def classify(method: str, path: str) -> str:
    """"public", "operator" or "authenticated" (the default class; ROLE_RULES refine it)."""
    if _first(PUBLIC_RULES, method, path):
        return PUBLIC
    if _first(OPERATOR_RULES, method, path):
        return OPERATOR
    return AUTHENTICATED


def role_rule_for(method: str, path: str) -> Optional[RoleRule]:
    for rule in ROLE_RULES:
        if _method_ok(rule.methods, method) and pattern_matches(rule.pattern, path):
            return rule
    return None


def evaluate(method: str, path: str, identity: Optional[Identity],
             has_permission: Optional[Callable[[str], bool]] = None) -> Decision:
    """allow / deny(reason) for one request.

    public -> allow. operator -> deny always (an app token never satisfies the operator mechanism).
    authenticated: no identity -> 401 deny; otherwise the first matching ROLE_RULE decides (role set, then, for a
    manager, `has_permission(name)` for any of the rule's permissions; without a callable a manager is denied);
    no matching rule -> allow (any authenticated user; the same-organisation id checks run separately)."""
    kind = classify(method, path)
    if kind == PUBLIC:
        return ALLOW
    if kind == OPERATOR:
        return Decision(False, "operator route: an app token is not accepted", 403)
    if identity is None:
        return Decision(False, "not authenticated", 401)
    rule = role_rule_for(method, path)
    if rule is None:
        return ALLOW if identity.role_id in ALL_ROLES else Decision(False, "unknown role", 403)
    if identity.role_id not in rule.roles:
        return Decision(False, "role %s may not call this" % identity.role_id, 403)
    if identity.role_id == ROLE_MANAGER and rule.perms:
        if has_permission is None or not any(has_permission(name) for name in rule.perms):
            return Decision(False, "manager permission required", 403, rule.perms)
    return ALLOW


def needs_manager_permission(method: str, path: str) -> Tuple[str, ...]:
    """The permission names (any-of) a manager needs for this request, () when none (lets the caller skip the lookup)."""
    rule = role_rule_for(method, path)
    return rule.perms if rule else ()


# ======================================================================================== ownership of ids
IdRef = Tuple[str, str, str]   # (source: path|query|body|form, key, value)


def _scalar(value) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text or None
    return None


def collect_ids(path_params: Optional[Mapping], query_params, body, form=None) -> List[IdRef]:
    """Every id the request carries under a checked key name: path parameters, query parameters, top-level JSON keys
    and form fields. Repeated query keys are all returned."""
    keys = set(ORG_KEYS) | set(USER_KEYS) | set(SELF_KEYS)
    found: List[IdRef] = []
    for key, value in (path_params or {}).items():
        if key in keys and _scalar(value) is not None:
            found.append(("path", key, _scalar(value)))
    if query_params is not None:
        items = query_params.multi_items() if hasattr(query_params, "multi_items") else list(dict(query_params).items())
        for key, value in items:
            if key in keys and _scalar(value) is not None:
                found.append(("query", key, _scalar(value)))
    if isinstance(body, dict):
        for key, value in body.items():
            if key in keys and _scalar(value) is not None:
                found.append(("body", key, _scalar(value)))
    if form is not None and hasattr(form, "multi_items"):
        for key, value in form.multi_items():
            if key in keys and isinstance(value, str) and _scalar(value) is not None:
                found.append(("form", key, _scalar(value)))
    return found


def owner_only_key(method: str, path: str) -> Optional[str]:
    """The path-parameter name that must equal the caller's own user id on this route (OWNER_ONLY_RULES), else None."""
    for methods, pattern, key in OWNER_ONLY_RULES:
        if _method_ok(methods, method) and pattern_matches(pattern, path):
            return key
    return None


def owner_violation(identity: Optional[Identity], method: str, path: str, path_params: Optional[Mapping]) -> Optional[str]:
    """None when the route is not owner-only or its id is the caller's own (or nobody is identified: the global guard
    decides about a missing token); else the offending key name."""
    key = owner_only_key(method, path)
    if key is None or identity is None:
        return None
    value = _scalar((path_params or {}).get(key))
    if value is None or value == identity.user_id:
        return None
    return key


def _exempt(method: str, path: str, key: str) -> bool:
    for methods, pattern, keys in ID_CHECK_EXEMPT:
        if key in keys and _method_ok(methods, method) and pattern_matches(pattern, path):
            return True
    return False


def key_kind(key: str, method: str = "GET", path: str = "/") -> Optional[str]:
    """How a request key is checked: "org", "self", "peer", "user", or None (not a checked key)."""
    for methods, pattern, kinds in KEY_KIND_OVERRIDES:
        if key in kinds and _method_ok(methods, method) and pattern_matches(pattern, path):
            return kinds[key]
    if key in ORG_KEYS:
        return "org"
    if key in SELF_KEYS:
        return "self"
    if key in PEER_KEYS:
        return "peer"
    if key in USER_KEYS:
        return "user"
    return None


def _owner_allowed(owner, allowed_orgs: frozenset) -> bool:
    """`owner` is the organisation of a looked-up user (str), or - for an admin who owns several - a collection of them:
    allowed when any of them is one of the caller's organisations."""
    if isinstance(owner, (set, frozenset, list, tuple)):
        return any(item in allowed_orgs for item in owner)
    return owner in allowed_orgs


def ids_to_resolve(identity: Identity, ids: Sequence[IdRef], allowed_orgs: frozenset,
                   method: str = "GET", path: str = "/") -> List[str]:
    """Values of user-key ids for which the owning organisation must be looked up (roles 1 and 2: any user key; staff:
    only peer keys, a staff member may otherwise use nothing but their own id, which needs no lookup)."""
    out = []
    for _src, key, value in ids:
        kind = key_kind(key, method, path)
        if kind not in ("user", "peer") or (kind == "user" and identity.role_id == ROLE_STAFF):
            continue
        if not _exempt(method, path, key) and value != identity.user_id and value not in allowed_orgs \
                and value not in out:
            out.append(value)
    return out


def ownership_violation(identity: Identity, ids: Sequence[IdRef], allowed_orgs: frozenset,
                        entity_orgs: Optional[Mapping[str, Optional[str]]] = None,
                        method: str = "GET", path: str = "/") -> Optional[Tuple[str, str]]:
    """None when every id belongs to the caller, else (source, key) of the first offending id.

    org keys   : must be an organisation the caller belongs to (`allowed_orgs`; an admin may own several). A manager
                 may also send their OWN user id as `org_id` (the app's convention for manager-scoped dashboards).
    user keys  : everyone: their own id, or the id of an allowed organisation (the app sends the organisation id under
                 `user_id` on /home/*, /addstaff/* and /home/org-admin/{user_id}; an organisation id can never select
                 another user). Staff (role 3): nothing else. Admin / manager: also a user of an allowed organisation
                 (`entity_orgs[id]` = that user's organisation, resolved by the caller); an id that exists nowhere
                 is passed through (the handler answers 404, nothing is disclosed).
    peer keys  : like user keys, but staff may also name a user of an allowed organisation (a colleague).
    self keys  : always the caller's own id."""
    entity_orgs = entity_orgs or {}
    for source, key, value in ids:
        kind = key_kind(key, method, path)
        if kind == "org":
            if value in allowed_orgs or (identity.role_id == ROLE_MANAGER and value == identity.user_id):
                continue
            return source, key
        if kind == "self":
            if value != identity.user_id:
                return source, key
            continue
        if kind in ("user", "peer"):
            if _exempt(method, path, key) or value == identity.user_id or value in allowed_orgs:
                continue
            if identity.role_id == ROLE_STAFF and kind == "user":
                return source, key
            owner = entity_orgs.get(value)
            if owner is None or _owner_allowed(owner, allowed_orgs):
                continue
            return source, key
    return None
