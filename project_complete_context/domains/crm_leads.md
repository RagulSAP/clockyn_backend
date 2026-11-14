# CRM / leads — backend context

Domain owner: BD4. Line numbers verified against the files as read.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/leads.py` | 110 | 16 endpoints, **no prefix** (full paths `/api/leads/...`), mounted at `app.py:44`; no try/except wrappers (HTTPExceptions pass through with their real status) |
| `core_functions/leads_functions.py` | 466 | all logic + `LeadCreate` model (`:10-17`) |
| `core_functions/gen_plans_functions.py:121-196` | n/a | `get_org_plan_status`, `PLAN_BLOCKED_FEATURES`, `check_plan_feature` (the `crm` gate) |
| `core_functions/company_functions.py:440-442` | n/a | company-account deletion removes `leads_assignment_info`, `leads_info`, `leads_category_info` of the org (not `carry_forward_requests`) |

Route-order rule: static and prefixed routes are declared **before** the wildcard `/{org_id}` / `/{leads_id}` routes (`router/leads.py:13`, `:80`); keep this order when editing.

Frontend consumer: `src/api/leads.ts` (also sends the `crm` 403 `detail` to the UI). There is a dead v1 call `POST /api/leads/{id}/request` that the backend does not have and Clockyn does not port.

## 2. Data model touched

Schema dump: `leads_info` (dump line 632), `leads_assignment_info` (591), `leads_category_info` (616), `carry_forward_requests` (180). Joins: `users` (`name`, `parent_id`).

### `leads_info`
| Column | Type | Meaning |
|---|---|---|
| `id` | int identity PK | internal |
| `leads_id` | varchar(36) NULL; non-unique index | uuid4 external key |
| `org_id` | varchar(36) | owner org |
| `client_name` varchar(100), `mobile_number` varchar(15), `client_address` varchar(255) NOT NULL, `remarks` varchar(255) | | lead details; length overflow -> DB error -> 500 |
| `category_id` | varchar(36) NOT NULL | -> `leads_category_info.category_id` (no FK) |
| `created_at`, `updated_at` | timestamp | `CURRENT_TIMESTAMP` (DB clock) |
| `status` | **varchar(10)** default `'pending'` | pipeline status. Allowed by the API: `new, contacted, interested, converted, not_interested` (`leads_functions.py:448`); the default `pending` is not in that set and `not_interested` (14 chars) **does not fit varchar(10)** |
| `created_date` | date NULL | **customer's required date**, not the creation date. Written from `required_date` on create and update (`:34`, `:153`) and **moved by carry-forward approval** (`:421-424`). NULL means "undated" |

### `leads_assignment_info` (one lead can have many rows)
| Column | Type | Meaning |
|---|---|---|
| `leads_id`, `assignment_id` (uuid4), `org_id`, `branch_id` int4, `dept_id` varchar(50), `user_id` NOT NULL | | the assignee |
| `remarks` varchar(255) | | admin note |
| `status` | varchar(20) default `'pending'` | **work status**: `pending, working, completed, carry_forward` (API validation `:238`); `carry_forward` is only ever set by an explicit `work-status` call, never by the carry-forward request flow |
| `description` text, `request_date` date | | last carry-forward request text/date copied from the request (`:304-307`) or the `request_date` passed to assign |
| `created_at`, `updated_at` | timestamp | |
Indexes: `leads_id`, `org_id`, `(org_id,status)`, `status`, `user_id`. No unique key on (`leads_id`,`user_id`).

### `leads_category_info`
`category_id` (unique index), `category_name` varchar(100) NOT NULL, `org_id`, timestamps. No unique key on (`org_id`,`category_name`).

### `carry_forward_requests` (CFR)
| Column | Type | Meaning |
|---|---|---|
| `request_id` | varchar(36) UNIQUE NOT NULL | uuid4 |
| `leads_id`, `org_id`, `user_id` | varchar(36) NOT NULL | lead, org, requesting employee |
| `description` | text | reason |
| `request_date` | date NOT NULL | the date the employee wants the lead moved **to** (default today) |
| `status` | varchar(10) default `'pending'` in dump (code's create says varchar(20)) | `pending / approved / rejected` |
| `actioned_by`, `actioned_at` | varchar(36), timestamp | admin decision |
| `created_at` | timestamp | submit time |
The table is also created at runtime: `CREATE TABLE IF NOT EXISTS carry_forward_requests ...` runs inside `request_carry_forward` (`leads_functions.py:273-287`), `get_my_carry_forward_requests` (`:324-337`) and `get_carry_forward_requests` (`:366-379`), plus `ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS description TEXT` and `ALTER COLUMN status TYPE VARCHAR(20)` on **every** carry-forward request (`:288-289`).

No soft delete anywhere: lead delete is a hard delete of `leads_info` only.

## 3. Endpoints

Common: no authentication. Plan gate `crm` (`check_plan_feature(org, 'crm', is_admin)`) only where shown. A blocked org gets **HTTP 403** `detail` = `Your plan has expired. Please renew your plan to continue using this feature.` (admin endpoints) or `Your plan has expired. Please contact your admin.` (employee endpoint), also for plan-blocked (not expired) orgs, whose wording is therefore misleading. Gate semantics: free trial -> allowed; expired -> blocked; paid plan Core (1) and Prime (2) -> blocked for `crm`; Elite (3) -> allowed (`gen_plans_functions.py:159-196`). Gate input is the client-supplied `org_id`.

| METHOD path | Handler (file:line) | Plan gate | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| POST `/api/leads/create` | `create_lead_endpoint` (`router/leads.py:15`) -> `create_lead` (`leads_functions.py:20`) | **yes** (admin msg) | JSON `LeadCreate {org_id, name, mobile, address, category:str, remarks?:'' , required_date?:str\|null}` | `{success:true, message:"Lead created successfully", leads_id}` | 403 plan; 500 `Failed to create lead: <db error>` (length overflow, bad date) |
| POST `/api/leads/assign` | `:20` -> `assign_lead` (`:102`) | **yes** | **query only**: `leads_id`, `org_id`, `user_id` (req), `branch_id:int?`, `dept_id:str?`, `remarks=''`, `request_date?` | `{success:true, message:"Lead assigned successfully"}` | **WP9:** 404 `Lead not found` / `Employee not found` (org scoped); 400 bad `request_date`; assigning to a staff who already holds the lead -> 200 `{success:false, message:"Lead is already assigned"}` and no new row; `request_date` also sets the lead's due date |
| GET `/api/leads/employee/{org_id}/{user_id}` | `:33` -> `get_employee_leads` (`:73`) | **yes** (employee msg) | path | `{success:true, data:[{leads_id, client_name, mobile_number, client_address, category_name, remarks, created_at, created_date, required_date, lead_status, assigned_user_id, assigned_user_name, work_status, assigned_at, has_pending_cfr}]}` | **WP9:** leads assigned to the user whose `created_date = IST today OR NULL`; one row per lead |
| GET `/api/leads/categories/{org_id}` | `:38` -> `get_categories` (`:169`) | no | path | `{success:true, data:[{category_id, category_name}]}` ordered by name | |
| POST `/api/leads/categories` | `:42` -> `create_category` (`:183`) | no | query `org_id`, `category_name` | `{success:true, message, category_id}` | duplicates allowed |
| DELETE `/api/leads/categories/{category_id}` | `:49` -> `delete_category` (`:199`) | no | path | `{success:true, message}` | 404 `Category not found`; leads keep the dangling `category_id` |
| GET `/api/leads/carry-forward/my/{org_id}/{user_id}` | `:53` -> `get_my_carry_forward_requests` (`:319`) | no | path | `{success:true, data:[{request_id, leads_id, description, request_date, status, actioned_at, client_name, forwarded_date, mobile_number, client_address, category_name, work_status}]}` newest first | runs `CREATE TABLE IF NOT EXISTS` |
| POST `/api/leads/carry-forward/request` | `:58` -> `request_carry_forward` (`:268`) | no | query `leads_id`, `org_id`, `user_id`, `description` (all req), `forward_date?` | `{success:true, message:"Carry forward request submitted", request_id}` | duplicate pending -> **200** `{success:false, message:"A pending carry forward request already exists"}`; bad `forward_date` -> 500 `Failed to submit request: Invalid isoformat string` |
| GET `/api/leads/carry-forward/requests/{org_id}` | `:68` -> `get_carry_forward_requests` (`:361`) | no | path | `{success:true, data:[{request_id, leads_id, user_id, description, request_date, status, created_at, client_name, lead_date, employee_name, crf_request_date}]}` newest first, all statuses | `crf_request_date` duplicates `request_date` |
| PUT `/api/leads/carry-forward/{request_id}/action` | `:72` -> `action_carry_forward` (`:401`) | no | path; query `action:'approved'\|'rejected'`, `admin_id` (req) | `{success:true, message:"Request <action>"}` | 400 `action must be 'approved' or 'rejected'`; 404 `Request not found` |
| GET `/api/leads/{org_id}` | `:82` -> `get_leads` (`:48`) | **yes** (admin msg) | path | `{success:true, data:[{leads_id, client_name, mobile_number, client_address, category_name, remarks, created_at, created_date, required_date, lead_status, assigned_user_id, assigned_user_name, work_status, assigned_at, assignees:[{user_id,name,work_status}], has_pending_cfr}]}` | **WP9:** exactly one row per lead; the flat assignee fields describe the most recently updated assignment |
| DELETE `/api/leads/{leads_id}` | `:87` -> `delete_lead` (`:121`) | no | path | `{success:true, message:"Lead deleted successfully"}` | 404 `Lead not found`; assignments/CFRs left behind |
| PUT `/api/leads/{leads_id}/work-status` | `:91` -> `update_work_status` (`:236`) | no | path; query `work_status` (req), `user_id?` | `{success:true, message:"Work status updated to <s>"}` | 400 invalid (`pending/working/completed/carry_forward`); 404 `Lead not found` when 0 rows; without `user_id` updates **all** assignments of the lead |
| PUT `/api/leads/{leads_id}/status` | `:95` -> `update_lead_status` (`:446`) | no | path; query `status` | `{success:true, message:"Lead status updated to <s>"}` | 400 invalid; 404; `not_interested` fails at DB (varchar(10)) -> 500 |
| PUT `/api/leads/assignment/{leads_id}` | `:99` -> `update_lead_assignment` (`:218`) | no | path; query `user_id` (req), `branch_id?`, `dept_id?` | `{success:true, message:"Lead assignment updated successfully"}` | updates **all** assignment rows of the lead; success even when 0 rows |
| PUT `/api/leads/{leads_id}` | `:108` -> `update_lead` (`:140`) | no | JSON `LeadCreate` | `{success:true, message:"Lead updated successfully"}` | 404 `Lead not found`; overwrites `created_date` with `required_date or NULL` (omitting it clears the date) |

Endpoint count: 16.

## 4. Business logic

### 4.1 Create / update lead (`:20-45`, `:140-166`)
1. Category by **exact name** within the org: `SELECT category_id FROM leads_category_info WHERE category_name = %s AND org_id = %s`; if missing INSERT a new category (uuid4) — so typing a new name silently creates a category (case-sensitive; `General` vs `general` are different).
2. INSERT `leads_info(leads_id uuid4, org_id, client_name, mobile_number, client_address, category_id, remarks, created_date, created_at, updated_at)` with `created_date = required_date or None`. **`created_date` is the customer's required date**, `created_at` is the creation timestamp (BC P1-11). Status takes the DB default `'pending'`.
3. Update: same category logic, `UPDATE ... SET client_name, mobile_number, client_address, category_id, remarks, created_date, updated_at WHERE leads_id` (no `org_id` filter; commit happens before the `rowcount` 404 check, `:155-157`). The body's `org_id` is used for the category lookup only.

### 4.2 Lists
* **Admin** `get_leads` (`:48-70`): `SELECT DISTINCT ... FROM leads_info l LEFT JOIN leads_category_info c ON ... AND c.org_id LEFT JOIN leads_assignment_info la ON l.leads_id = la.leads_id AND la.org_id LEFT JOIN users u ON la.user_id = u.user_id AND u.parent_id = org WHERE l.org_id ORDER BY l.created_at DESC`. A lead with N assignments appears N times (DISTINCT cannot collapse rows that differ in `assigned_user_id`), unassigned leads appear once with null assignee. `assigned_at` = assignment `updated_at`. No pagination, no `lead_status`.
* **Employee** `get_employee_leads` (`:73-99`): `today = date.today()` (**server local date, not IST**); INNER JOIN `leads_assignment_info la ON la.user_id = %s AND la.org_id = %s`; filter `l.created_date = today OR l.created_date IS NULL`; order `created_date ASC NULLS LAST, created_at DESC`. Hence undated leads stay visible forever (DD-95), future-dated carried-forward leads reappear on their date, completed leads still appear while dated today/undated. Returns both `lead_status` (pipeline) and `work_status` (assignment).

### 4.3 Assign / reassign
* `assign_lead` (`:102-118`): INSERT a new `leads_assignment_info` row (`assignment_id` uuid4, status `'pending'`, `request_date or NULL`). It does **not** check that the lead exists, belongs to the org, that the user belongs to the org, or that the lead is already assigned: **assigning twice creates two rows** (two assignees, duplicate rows in the admin list). No notification is sent to the assignee.
* `update_lead_assignment` (`:218-233`): `UPDATE leads_assignment_info SET user_id, branch_id, dept_id, updated_at WHERE leads_id` — all rows of the lead are pointed to the new user (collapsing multi-assignee data into duplicates of one user); status and description are left unchanged; no effect and no error if the lead has no assignment.

### 4.4 Work status vs pipeline status
Two independent lifecycles: `leads_assignment_info.status` (work: `pending, working, completed, carry_forward`; `update_work_status`, `:236-265`, scoped by `user_id` when given) and `leads_info.status` (pipeline: `new, contacted, interested, converted, not_interested`; `update_lead_status`, `:446-466`). The pipeline column is varchar(10) so `not_interested` always fails (E-3).

### 4.5 Carry-forward lifecycle
1. **Request** (`:268-316`): runs the DDL statements (create table / add column / alter type) and commits; `request_date = date.fromisoformat(forward_date)` or today; refuses if a **pending** CFR exists for the same (`leads_id`,`user_id`) with HTTP 200 `success:false`; INSERT CFR `pending`; `UPDATE leads_assignment_info SET description, request_date, updated_at WHERE leads_id AND user_id`; commit. **The assignment `status` is never changed** to `carry_forward` and the lead is not flagged (BC P1-11, DD-94); no check that the employee is actually assigned, no notification to admins.
2. **Admin list** (`:361-398`): every CFR of the org, all statuses, joined to `leads_info` (inner: CFRs of deleted leads vanish) and `users` for `employee_name`; `lead_date` = the lead's **current** `created_date`.
3. **Employee list** (`:319-358`): the user's CFRs joined to `leads_info`, `leads_category_info`, and `leads_assignment_info la ON cf.leads_id = la.leads_id AND cf.user_id = la.user_id`. `forwarded_date` is `l.created_date` (current required date, so after approval it equals the approved `request_date`) and `work_status` is `la.status`, which is **NULL after approval** because the assignment row is deleted; multiple assignment rows for the same pair would duplicate the CFR row (CFR join).
4. **Action** (`:401-443`): 400 for an action other than `approved/rejected`; load CFR (404); `UPDATE carry_forward_requests SET status, actioned_by, actioned_at`; if **approved**: `UPDATE leads_info SET created_date = <CFR.request_date>` (moves the lead to that date) and `DELETE FROM leads_assignment_info WHERE leads_id AND user_id` (lead becomes unassigned; the admin must reassign); if **rejected**: nothing else (the assignment keeps the `description/request_date` written at request time; no restore). Not guarded against re-actioning (flipping rejected -> approved or repeating approve) and `admin_id` is not validated as an admin of the org. No notification to the employee. Approved CFRs remain in the lists forever.

### 4.6 Plan gating on four endpoints
`create`, `assign`, `GET /api/leads/{org_id}` and `GET /api/leads/employee/...` call `check_plan_feature(org_id, 'crm', is_admin=...)` before any DB work (`router/leads.py:17`, `:30`, `:35`, `:84`). Every other endpoint (categories, carry-forward, work-status, status, assignment, update, delete) is ungated, so a Core/Prime/expired org can still read/modify existing CRM data through them. `check_plan_feature` order: free trial active -> allow; no active trial: look up the latest ACTIVE subscription (none -> `is_expired`), `end_date < today` -> expired; blocked set per plan id (`PLAN_BLOCKED_FEATURES`, `gen_plans_functions.py:167-171`). It uses the server `date.today()` (not IST). Its docstring (`:175-182`) is stale versus the matrix (it claims chat/salary/camera are blocked for Core).

## 5. Side effects & integrations

None: no push, WebSocket, e-mail, WhatsApp or S3 in this domain. DDL executed at runtime (see §2). Company deletion hard-deletes leads/assignments/categories of the org (`company_functions.py:440-442`) but not CFRs.

## 6. Config & env vars

DB connection and `DB_SCHEMA` only (plan status reads the free-trial and subscription tables).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| L-1 | High | all routes | No authentication/ownership: any caller can read, create, reassign, delete leads and approve carry-forward requests for any org id; `admin_id`, `user_id`, `org_id` are trusted. |
| L-2 | High | `leads_functions.py:421-430` | Approving a CFR deletes the requesting user's assignment and rewrites `created_date`; re-approving or approving a rejected CFR repeats this; no state check, no actor validation. |
| L-3 | High | `:446-466` + dump `leads_info.status varchar(10)` | `not_interested` cannot be stored (14 chars > 10) -> 500; default `pending` is not an allowed value, so `lead_status` is not round-trippable. |
| L-4 | High | `:102-118` | No duplicate-assign guard: assigning an already assigned lead (or double-tapping) inserts another row -> duplicate rows in `GET /api/leads/{org}` and duplicate employee entries; lead/org/user existence unchecked. |
| L-5 | Medium | `:218-225` | Reassign updates **all** assignment rows of the lead and succeeds with zero rows; loses per-assignee state. |
| L-6 | Medium | `:268-316` | Carry-forward request never persists a `carry_forward` status on the assignment/lead; admin "Carry Fwd" counts about 0; approved/rejected CFRs stay listed forever; reject does not restore description/date. |
| L-7 | Medium | `:268-316`, `:319-398` | DDL (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN`, `ALTER COLUMN TYPE`) runs inside request handlers on every call: needs DDL rights, takes locks, hides schema drift (code says varchar(20) for CFR status, dump says varchar(10)). |
| L-8 | Medium | `:34`, `:153` | `created_date` is the **required date**, not the creation date; update without `required_date` clears it (`required_date or None`); employee list and CFR joins depend on it. |
| L-9 | Medium | `:78-92` | `date.today()` server-local (likely UTC) decides "today" for employee leads; between 00:00 and 05:30 IST staff see yesterday's list. |
| L-10 | Medium | `:52-63` | Admin list is `SELECT DISTINCT` over lead x assignment: duplicates per assignee, no pagination; org scoping of the user join relies on `users.parent_id`. |
| L-11 | Medium | `:319-358` | CFR join: `work_status` NULL after approval; duplicates if several assignment rows exist; inner join drops CFRs for deleted leads. |
| L-12 | Medium | `:121-137`, `:199-215` | Lead delete leaves assignments and CFRs; category delete leaves leads with dangling `category_id` (NOT NULL column) and a null `category_name` in lists; deletes are not org scoped. |
| L-13 | Medium | `router/leads.py:17-84` | Plan gate on only 4 of 16 endpoints; gate uses the client-supplied org id; denial text always says "expired" (even for plan-blocked Core/Prime orgs). |
| L-14 | Low | `:140-166`, `:236-265` | Commit before the `rowcount` 404 check (harmless), `update_lead` ignores org of the lead, `work-status` without `user_id` updates every assignee. |
| L-15 | Low | `:25-31`, `:144-150` | Category auto-create by exact name, case-sensitive, no unique key -> duplicate categories; `create_category` does not check duplicates. |
| L-16 | Low | `leads_functions.py:25-37` | Length limits unchecked: `mobile_number varchar(15)`, `client_name varchar(100)`, `client_address/remarks varchar(255)`; overflow -> 500 with the raw DB message (`Failed to create lead: ...`). |
| L-17 | Low | `router/leads.py:64`, `:100-104` | `branch_id` is typed `int`; the v1 client sent `""` (422). `dept_id` is a string. |
| L-18 | Info | `:395` | `crf_request_date` is a redundant alias of `request_date`. |

## 8. Clockyn requirements

References: `backend_changes.md` (BC), `docs/DEFERRED_DECISIONS.md` (DD).

### C-1 Lead `created_date` = customer required date (BC P1-11, DD-95)
* **Current:** column `created_date` holds the required date; the real creation time is `created_at`; `required_date` -> `created_date` (`:34`, `:153`). The Clockyn client exposes it as `requiredDate` and always resends it on update (`src/api/leads.ts` `buildLeadBody`, `leadToInput`), and derives `effectiveDate = requiredDate ?? createdOn`.
* **Required change:** keep the column (rename is optional); return an explicit `required_date` alias in every lead payload next to `created_date`, and make update **not clear** the date when `required_date` is omitted (use "field present" semantics).
* **Outline:** add `l.created_date AS required_date` to the SELECTs (`:53-55`, `:80-84`); in `update_lead` build the SET list dynamically or use `COALESCE`. Optional migration `ALTER TABLE leads_info RENAME COLUMN created_date TO required_date` with a compatibility view or both names selected.
* **Compat:** additive. **Acceptance:** edit a lead's remarks without `required_date` and the date is preserved; list rows contain `required_date` equal to `created_date`.

### C-2 One row per lead; update/assign one assignment; duplicate-assign guard (BC P1-11)
* **Current:** L-4, L-5, L-10; the client merges duplicate rows.
* **Required change:** (a) `assign` is an **upsert** per (`leads_id`,`user_id`) or rejects an already active assignment with 409; (b) reassign targets one assignment (by `assignment_id` or by `leads_id` + current `user_id`) and removes the others if only single assignment is intended; (c) admin list returns one row per lead with `assignees:[...]` while keeping the legacy flat fields for the primary assignee; (d) validate lead/org/user membership.
* **Outline:** add `UNIQUE (leads_id, user_id)` on `leads_assignment_info` after de-duplication (`DELETE ... USING` keeping the newest); change `INSERT` to `ON CONFLICT (leads_id, user_id) DO UPDATE SET branch_id, dept_id, remarks, updated_at`; rewrite `get_leads` with `LEFT JOIN LATERAL` (latest assignment) or aggregate `json_agg`; extend `update_lead_assignment` with optional `from_user_id`.
* **Compat:** keep flat `assigned_user_id`, `assigned_user_name`, `work_status` for the primary assignee so the current client keeps working; additive `assignees`.
* **Acceptance:** assigning the same lead to the same user twice results in one row and `success:true` (or 409 by decision); `GET /api/leads/{org}` returns each `leads_id` once; reassign changes exactly one assignment.

### C-3 Carry-forward status persistence, CFR join, approval semantics (BC P1-11, DD-94)
* **Current:** L-6, L-11; request does not set `carry_forward`; approved CFRs listed forever; employee `work_status` NULL after approval.
* **Required change (needs product answer first, DD-94):** (a) on request set `leads_assignment_info.status='carry_forward'` (remember the previous status in a new column `status_before_cf` or on the CFR); (b) on **reject** restore the previous status and clear `description/request_date`; (c) on approve move the lead date (as today) and either delete the assignment (as today) or mark it `carried`; (d) add `has_pending_cfr` (boolean) and `cf_status` to lead rows; (e) filter parameters on the CFR lists (`status`, `from`, `to`) so the approved history does not grow unbounded; (f) guard state transitions (only `pending` can be actioned, 409 otherwise) and verify the admin; (g) push the employee on decision.
* **Outline:** `request_carry_forward` (`:268`) add the UPDATE of `status`; `action_carry_forward` (`:401`) add `AND status='pending'` to the load and restore logic; move DDL to `migrations/` (create table, `ALTER TABLE leads_assignment_info ADD COLUMN description text, request_date date`, `ALTER COLUMN status TYPE varchar(20)`, widen `carry_forward_requests.status` to varchar(20)) and delete the runtime DDL at `:273-290`, `:324-338`, `:366-380`.
* **Compat:** keep existing fields; the current client joins `/carry-forward/my` itself and keeps working; new fields are additive.
* **Acceptance:** after a request the admin list shows the lead with `work_status='carry_forward'` and `has_pending_cfr=true`; reject restores `pending`/`working`; approve results in the new `created_date` and no assignment; approving twice returns 409; requests for a lead not assigned to the user return 400/404.

### C-4 Pipeline status column too small (new)
* **Current:** `leads_info.status varchar(10)` vs `not_interested` (L-3). The Clockyn client sends `not_interested` (`updateLeadPipelineStatus`).
* **Required change:** `ALTER TABLE leads_info ALTER COLUMN status TYPE varchar(20)`; make the default `'new'` (a member of the allowed set) and back-fill `pending` -> `new`; expose `lead_status` in the admin list too.
* **Acceptance:** setting each of the five statuses succeeds; admin list returns `lead_status`.

### C-5 Plan gating completeness (BC P2-2)
* **Current:** only four endpoints gated; message says "expired" for plan-blocked orgs.
* **Required change:** gate every CRM endpoint (or none of the data-changing ones) consistently; use distinct codes/messages (`PLAN_EXPIRED` vs `FEATURE_NOT_IN_PLAN`); derive `org_id` from the caller once authentication exists (BC P3-1). Keep returning real 403 (the leads router has no catch-all, so the 403 already reaches the client unwrapped).
* **Outline:** add `await check_plan_feature(...)` to categories, carry-forward, work-status, status, assignment, update, delete handlers (`router/leads.py:38-110`) or a router dependency; fix the docstring at `gen_plans_functions.py:175-182`.
* **Acceptance:** a Core org gets 403 on every CRM route; Elite and trial orgs succeed.

### C-6 Employee "My Leads" visibility and IST date (DD-95)
Use the IST date (`utils/timezone.py` `IST`) instead of `date.today()` at `:78`, and decide the rule for completed/undated leads (e.g. hide `completed` after the day). Acceptance: at 01:00 IST the list uses the IST calendar day.

### C-7 Deletes (BC P1-9)
Make `delete_lead` also delete its assignments and CFRs (single transaction) with an `org_id` scope parameter; `delete_category` refuses (409) when leads use the category or re-points them to `General`. Acceptance: no orphan assignment/CFR rows after a lead delete; deleting an in-use category returns 409.

## 8b. Changes (WP9, migration 029, decision D-17)

Sections 2 to 7 above describe the code before WP9. After WP9:
* **No DDL in handlers.** `carry_forward_requests`, `leads_assignment_info.description/request_date/prev_status` and the index set are in `clockyn_ddl_latest.sql` and `migrations/029_crm_leads_hardening.sql` (idempotent, deletes nothing).
* **Status width.** `leads_info.status` and `leads_assignment_info.status` are `varchar(64)`; `leads_info.status` defaults to `new` and existing `pending`/NULL lead statuses are relabelled `new`. Create writes `new` explicitly.
* **One row per lead.** Admin list: lateral "latest assignment" subquery plus scalar subqueries (no multiplying join, no `DISTINCT`); adds `lead_status`, `required_date`, `assignees`, `has_pending_cfr`. Employee list uses the same lateral pattern and the IST date. `GET /carry-forward/my` adds `created_at`; the admin CFR list adds `actioned_at`; both lists no longer multiply rows.
* **Duplicate-assign guard.** Same staff twice returns 200 `{success:false, message:"Lead is already assigned"}` (the contract mock's text), nothing inserted. `ux_leads_assignment_lead_user` (unique `(leads_id, user_id)`) is created by 029 only when no duplicates exist (otherwise a NOTICE and the index is skipped); `scripts/dedupe_leads.sql` removes duplicates by hand (dry run first, ends in ROLLBACK, backs rows up, never run by `migrate.py`). Assigning a lead that is held by a different staff is still allowed (legacy); reassign (`PUT /assignment/{id}`, optional `org_id`) leaves exactly one assignment.
* **Carry-forward persisted.** Request: lead must exist in the org and be assigned to the employee (400 otherwise), description required, `forward_date` defaults to tomorrow (IST) and may not be in the past; the assignment gets `status='carry_forward'` with the old status in `prev_status`. Reject restores `prev_status`, clears description/date. Approve moves the lead's due date and sets the assignment back to `pending` (flag `CRM_CARRY_FORWARD_UNASSIGN=1` restores the old "delete the assignment"). Only `pending` requests can be actioned (409 otherwise).
* **Other.** Update lead is org scoped and keeps the due date when `required_date` is absent from the body; delete lead also removes its assignments and requests (optional `org_id`); category names are unique per org, case-insensitive (`success:false` "Category already exists"); 500 responses no longer carry raw DB messages; bad dates are 400.
* **Org scoping of status and category calls (independent review fix).** `PUT /api/leads/{id}/work-status`, `PUT /api/leads/{id}/status` and `DELETE /api/leads/categories/{id}` take an optional `org_id` query param. The org is looked up from the lead / category row by its own id (compared with `org_id` when supplied) and every UPDATE/DELETE carries `AND org_id = %s`; an id of another organisation answers the usual 404 (`Lead not found` / `Category not found`). Work status changes only the assignment of `user_id` (the app always sends it); when the request omits it the route falls back to the token's user (`request.state.identity.user_id`), and with no acting user at all nothing changes (400 `user_id is required`) instead of rewriting every assignment of the lead. Response shapes are unchanged. Tests: `tests/test_review_fixes_crm_invoice.py`.
* Not changed: no authentication (L-1), plan gating on four endpoints (L-13), `delete_category` still allows in-use categories (L-12), `GET /requests/calendar` belongs to the requests domain (WP7).

### 8c. Resource scoping (G4-A Changes)
Lead delete / update / status / work-status / reassign and category delete: the lead (category) is looked up by `leads_id` AND the caller's organisations (`org_id = ANY(...)` from the token) when the request names no `org_id`; a named foreign `org_id` is 404 before any query. `PUT /api/leads/carry-forward/{request_id}/action` answers 404 for a request of another organisation. Without a token the lookups are unchanged.

## 9. Test notes

* **Fixtures:** orgs on trial, expired, Core, Prime, Elite (to exercise `check_plan_feature`), staff users, categories, leads with 0/1/2 assignments, CFRs in each status, leads with NULL / today / past / future `created_date`.
* **Critical cases:** plan gate returns 403 with the admin vs employee message on the four endpoints and none elsewhere; double assign creates two rows (current); reassign hits all rows; update without `required_date` clears the date; `not_interested` fails with a DB error (current); CFR request twice -> 200 `success:false`; approve moves date and deletes assignment; employee list at the IST/UTC day boundary; CFR join NULL `work_status` after approval; lead delete leaves orphans; DDL idempotence on a fresh schema.
* **Existing tests:** none in the backend; frontend `src/api/__tests__/announcementsLeads.test.ts`, `features/crm/__tests__/*`.
