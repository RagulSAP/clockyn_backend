# Managers, shifts, holidays, weekly offs, attendance rule, wage templates — backend context

Conventions: `file:LINE` under `C:\POC\pa_backend\Clockyn\`; "DDL:N" = line in the schema dump at the repo root (legacy-named .sql); `<legacy>_` = table prefix that carries the legacy product name (exact names in the DDL). FE = `C:\POC\pa_frontend\Clockyn`.

## 1. Scope & files

| File | Lines | Role | FE caller |
|---|---|---|---|
| `router/manager.py` | 94 | `/managers/*` (12 handlers) | `src/api/managers.ts` |
| `core_functions/manager_functions.py` | 407 | assign/remove manager, permissions, `get_manager_scope` (used by 9 call sites in `home_functions.py` and `attendance_functions.py:56`) | same |
| `core_functions/permission_checker.py` | — | `check_manager_permission_middleware`, `get_user_permissions` (used by `/managers/permissions/{id}` and `router/report.py:118,146`) | login payload |
| `router/shift.py` | 36 | `/shift/*` (5 handlers) | `src/api/shifts.ts`, `src/api/staff.ts:getShiftOptions` |
| `core_functions/shift_functions.py` | 240 | shift CRUD, time parsing, 18 h guard | same |
| `router/holiday.py` | 76 | holiday router, **mostly shadowed**; only `off-days` routes are live | `src/api/system.ts` |
| `router/daily_hourly.py` | 60 | **despite its name, a holiday router** (`prefix="/api/holidays"`, `daily_hourly.py:8`); this is the live holiday CRUD because it is mounted first | `src/api/system.ts` |
| `router/report.py:16-47` | — | third copy of get/add/delete/working-days under `/api/holidays/...` (prefix `/api`) — shadowed | — |
| `core_functions/holiday_functions.py` | 220 | holiday + weekly-off persistence, `check_holiday_block`, `get_working_days` | — |
| `core_functions/daily_hourly_functions.py` | 219 | daily/hourly wage template + assignment functions — **not imported by any router or other module** (repo-wide grep: only self-references; `report_functions.py:581-595` only reads the same tables) | `src/api/payroll.ts:348-392` (routes absent) |

Mount order (`app.py`): `manager` :45, `shift` :46, `daily_hourly` :48, `report` :56, `holiday` :65. Starlette serves the first route whose path **and method** match, so for `/api/holidays/...` the order is `daily_hourly` → `report` → `holiday`.

Weekly-off / attendance-rule search: `grep -rn "attendance.rule|attendance_rule|weekly"` over all `.py`/`.sql` finds only the docstring at `holiday_functions.py:7` (and `org_off_days` usage, `:13,:28`). **There is no `/api/attendance-rule` route, function or table anywhere**; `org_off_days` is also absent from the schema dump and `migrations/` contains only a README.

## 2. Data model touched

| Table (DDL) | Columns | Notes |
|---|---|---|
| `users` (991) | `role_id` (2 = manager), `parent_id` (org id for managers), `status` | promotion sets `role_id=2` only |
| `manager_assignments` (670) | `id`, `org_id` NOT NULL, `user_id` NOT NULL, `branch_id` int NOT NULL, `dept_id` varchar NULL, timestamps; **unique index on `(user_id, branch_id, COALESCE(dept_id,''))`** (DDL:680) | one row per (branch, department) pair; `dept_id NULL` is allowed by the schema but rejected by the API |
| `<legacy>_settings` (860) | `setting_id`, `setting_name` | catalogue of manager permissions; rows are not in the repo (UNVERIFIED which names exist) |
| `<legacy>_manager_permissions` (843) | `org_id, user_id, setting_id, status(1)` | enabled permissions; not cleaned on demotion |
| `shift` (934) | `id` identity, `shift_id` int, `shift_name`, `start_time time`, `end_time time`, `status`, `org_id`; **unique `shift_id` globally** (`unique_shift_id`) and `(shift_id, org_id)` | soft status exists but delete is a hard delete |
| `users_additional_info` (1032) | `shift_id` (NULL/0 = no shift; readers use `> 0`, `home_functions.py:186`) | **the shift ↔ staff assignment lives here** |
| `org_holidays` (741) | `id serial`, `org_id`, `name`, `date`, `branch_name`, `dept_name` (scope is by **name**), `created_at`; **no `block_attendance` column and no primary key in the dump** though the code reads/writes `block_attendance` (`holiday_functions.py:47,73,118-120,161`) | UNVERIFIED whether the deployed DB has the column |
| `org_off_days` | `org_id` (unique/PK needed for `ON CONFLICT (org_id)`, `:30`), `off_days` (array of ints; psycopg2 adapts a Python list) | **table not in the dump**; created out-of-band (UNVERIFIED) |
| `firm_holidays` (312) | legacy table, only deleted by account delete (`company_functions.py:452`) | not read anywhere |
| `daily_hourly_wages` (248) | `org_id,temp_id,temp_name,temp_type('D'/'H'),temp_amount,att_bandwidth,user_id` | template rows have `user_id NULL`; per-user copies have `user_id` set |
| `assigned_wages` (120) | `org_id,user_id,temp_id` | assignment of a user to a template |
| `branch_info`, `dept_info` | read to label assignments | see org-structure doc |
| (missing) attendance rule | — | see R4 |

## 3. Endpoints

All unauthenticated (identity = ids supplied by the client; `backend_changes.md` P3-1).

### 3.1 Managers (`/managers`, `manager.py`)

| Method path | Handler → function | Request | Success | Errors |
|---|---|---|---|---|
| GET `/managers/staff?org_id&branch_id&dept_id` | :14 → `get_staff_for_manager` mf:87 | all required; `branch_id=0` = all branches, `dept_id='ALL'` = all (a dept **name** also matches, mf:104-105) | bare `[{user_id,name,emp_id}]` active role 3 with `parent_id = org` | 500 |
| POST `/managers/assign` | :23 → `assign_manager` mf:118 | JSON `{user_id, assignments:[{branch_id:int, dept_id:str (required)}]}` (models mf:10-16) | `{success,message:"Manager assigned successfully"}` | 422 (Pydantic) when `dept_id` missing; 400 empty list (mf:124-125) / blank `dept_id` (mf:126-127); 404 "Staff not found" (needs `status=1`, mf:130-133); 500 `"Failed to assign manager: ..."` (e.g. duplicate pair in request → unique-index violation) |
| GET `/managers/?org_id=` | :28 → `get_managers` mf:163 | | `[{user_id,name,emp_id,role_id,assignments:[{branch_id,branch_name,dept_id,dept_name}]}]` — role 2, active, **`parent_id = org`** | 500 |
| DELETE `/managers/{user_id}` | :35 → `remove_manager` mf:207 | | `{success,message:"Manager removed successfully"}` | 404 "Manager not found" (rowcount 0) |
| GET `/managers/{user_id}/staff` and GET `/managers/get-staff-by-manager?manager_id=` | :40, :45 → `get_staff_under_manager` mf:39 | | `[{user_id,name,emp_id,branch_name,dept_name}]` | 404 "Manager not found" |
| GET `/managers/{user_id}/permissions` | :50 → mf:234 | | `{success,permissions:{<setting_name>:bool}}` for **every** setting row | 404 only if user absent (role not checked) |
| POST `/managers/{user_id}/permissions` | :55 → mf:271 | `{permissions:{name:bool}}` | `{success,message}` | 404 unless role 2 |
| GET `/managers/{user_id}/check-permission/{setting_name}` | :60 → mf:316 | | `{has_permission}` | never errors |
| GET `/managers/{user_id}/enabled-settings` | :66 → mf:345 | | `{enabled_settings:[names]}` | never errors |
| GET `/managers/settings/all` | :72 | | `{settings:[{setting_id,setting_name}]}` | — |
| GET `/managers/permissions/{user_id}` | :89 → `permission_checker.get_user_permissions` | | `{permissions: ...}` | — |

**Changes (WP6, decision D-10, migration 034): server-side manager scope, flag `ENFORCE_MANAGER_SCOPE` (default OFF).**
`core_functions/scope.py` answers "which staff may this caller see / act on". Rules (deny by default): administrator (or an organisation id, the app's admin convention) = the whole organisation; manager = themselves, the staff assigned to them (`users.manager_id = manager`) and, when `manager_assignments` rows exist, the staff of those branches AND departments (a list that is absent adds no restriction of its own); staff = only themselves; an unknown role sees nothing. A manager with no assignments and no assigned staff therefore sees only themselves (today such a manager is unrestricted: `get_manager_scope` returns `None` lists). API: `scope_predicate(scope, alias, include_org)` -> `(sql, params)` (pure), `can_manage(scope, target_row, target_org_id=None)` (pure), `current(cursor, caller_id)` (None without any database access while the flag is off) and `clause(scope, alias)` (` AND (...)` to append to a query that already filters by organisation). With the flag ON it is applied in `home_functions.py` (`get_dashboard_status` three queries, `get_staff_list`, `get_all_staff`, `get_absent_staff`, `get_late_punched_in_staff`, `get_punched_in_staff`, `get_punched_out_staff`; these call `manager_functions.get_staff_query_scope`, which drops the manager's branch/department lists because the clause carries them united with `manager_id`) and in `GET /managers/{id}/staff` / `get-staff-by-manager` (`get_staff_under_manager`). With the flag OFF every query is exactly the old one. Not scoped (no caller id in the request, or not a staff list): `GET /managers/staff`, `get_branches`, `get_department_stats`, `attendance_functions.get_daily_attendance`, `GET /addstaff/{id}` and the staff detail/profile reads; `can_manage` is the helper for those once the caller identity is available (WP12 token). Also fixed: `get_late_punched_in_staff` read its rows after the grace lookup had reused the cursor.
`manager_id` also fixes the manager-created staff problem (defect D3, see `staff_management.md` "Changes (WP6 remainder)"); manager edit never demotes (QW-6, verified).

### 3.2 Shifts (`shift.py`, no prefix)

| Method path | Handler → function | Request | Success | Errors |
|---|---|---|---|---|
| POST `/shift/create` | :9 → `create_shift` sf:65 | JSON `ShiftCreate{org_id,shift_name,start_time,end_time}` (sf:9-13) | `{success,message,shift_id}` | 422 bad time format (sf:33-36) or duration > 18 h (sf:57-62); 500 |
| GET `/shift/get_shift/{shift_id}` | :15 → sf:103 | int | `{success,data:{shift_id,shift_name,start_time,end_time ("HH12:MIAM", e.g. `09:00AM`),status}}` (any org/status) | 404; 500 |
| GET `/shift/get_shifts/{org_id}` | :21 → sf:128 | | `{success,data:[...same fields]}` `status=1` only, by `shift_id` | 500 |
| PUT `/shift/update/{shift_id}` | :27 → sf:154 | same body as create | `{success,message}` | 422 as create; 404 (raised after commit, sf:170-173); 500 |
| DELETE `/shift/delete/{shift_id}?org_id=` | :33 → sf:187 | | `{success,message}` (adds "N user(s) unassigned..." when used) | 404; 500 |
| GET `/shift/assigned_staff/{org}/{shift}` | `router/shift.py` -> `get_assigned_staff` (sf) | path (shift must be active and belong to org) | `{success,data:[{user_id,name,emp_id,mobile_number,email,dept_name,branch_name,role_id,is_active,profile_photo_url}]}` active role 2/3 staff whose `users_additional_info.shift_id` = shift | 404 `Shift not found` |
| GET `/shift/unassigned_staff/{org}/{shift}` | -> `get_unassigned_staff` | path | same row shape; active role 2/3 staff with shift_id NULL, 0, another shift, or no additional-info row | 404 |
| POST `/shift/assign_staff` | -> `assign_staff` | JSON `{org_id,user_ids:[str],shift_id:int}` | `{success,message,assigned}`; idempotent; inserts an additional-info row for staff lacking one | 400 empty `user_ids` / missing fields; 404 shift or user (foreign, inactive, non role 2/3) - all-or-nothing |
| DELETE `/shift/unassign_staff/{org}/{user}/{shift}` | -> `unassign_staff` | path | `{success,message}`; sets `shift_id` NULL only when it equals `{shift}` (otherwise no-op) | 404 shift (any status) or user not in org |

### 3.3 Holidays / weekly offs - effective routes (single owner: `router/holiday.py`, prefix `/api/holidays`)

The duplicate holiday copies in `router/daily_hourly.py` (file deleted, no longer mounted) and `router/report.py` were removed; `tests/test_route_table.py` has an empty `ALLOWED_DUPLICATES`. Business failures answer HTTP 200 `{success:false,message}`; bad input answers 422 (`ValueError` -> 422 in `_call`); other errors 500.

| Method path | Request | Success | Errors |
|---|---|---|---|
| GET `/api/holidays/{org_id}?year&month` (both optional; default = current IST month) | | `{success,data:[{id,name,date:"YYYY-MM-DD",branch_name,dept_name,block_attendance:bool}]}` ordered by date, id (NULL flag reads as `true`) | 422 month not 1-12 |
| POST `/api/holidays/{org_id}` | `{name,date,branch_name?,dept_name?,block_attendance=true}`; blank branch/dept = whole org; name is trimmed | `{success,id,message:"Holiday added successfully"}` | 422 blank name / date not `YYYY-MM-DD`; 200 `{success:false,message:"A holiday already exists for this date and scope"}` for the same org + date + branch + dept |
| PUT `/api/holidays/{org_id}/{holiday_id}` | `{name?,date?,block_attendance?}` (all optional; only given fields change) | `{success:true,message:"Holiday updated"}` | 200 `{success:false,message:"Holiday not found"}` when the id does not exist **in this org** (cross-org id refused); duplicate scope on a date change as above; 422 blank name / bad date |
| DELETE `/api/holidays/{org_id}/{holiday_id}` | | `{success,message:"Holiday deleted"}` | 200 `{success:false,message:"Holiday not found"}` (unknown or other org's id) |
| GET `/api/holidays/working-days/{org_id}?year&month&branch_name&dept_name` | | `{success,total_days,off_days,holidays,working_days}` | 422 invalid month |
| GET `/api/holidays/off-days/{org_id}` | | `{success,off_days:[0..6]}`, default `[6]` | |
| POST `/api/holidays/off-days/{org_id}` | `{off_days:[int]}` 0 = Monday ... 6 = Sunday | `{success,message:"Off days updated",off_days:[sorted, de-duplicated]}` | 422 value outside 0..6 |

Notes: a unique index `uq_org_holidays_org_date_scope (org_id, date, COALESCE(branch_name,''), COALESCE(dept_name,''))` backs the duplicate check (DDL + migration 025; de-duplicate existing rows first, see the migration comment). `check_holiday_block` (face punch flow) now compares against today's IST date. No bulk-add endpoint: the app issues one POST per date (`system.ts addHolidayDates`), so none was added. `get_working_days` still counts every holiday regardless of `block_attendance` (R3 item 6 needs a product decision).

### 3.4 Wage templates

Mounted by `router/wages.py` (routers `/api/daily-wages`, `/api/hourly-charges`, `/api/wages`); routes and behaviour are listed in `wages_daily_hourly.md` §3. See R6.

## 4. Business logic

**assign_manager** (mf:118-161), one transaction: validate list non-empty and each `dept_id` non-blank → read `parent_id` of `users` row (`status=1`) as `org_id` → `UPDATE users SET role_id = 2` → `DELETE manager_assignments WHERE user_id` → INSERT one row per `(branch_id, dept_id)`. No check that the user is role 3 (any active user with a non-null `parent_id` incl. an existing manager can be re-assigned; an admin has `parent_id NULL` → INSERT of NULL `org_id` fails → 500/rollback), that branches/departments belong to the org, or that pairs are unique (duplicates → unique-index violation → 500). The FE builds one entry per branch × department and omits `dept_id` when none chosen (`mappers/managers.ts:60-69`) → Pydantic 422 (not the 400 described in DD-54).

**remove_manager** (mf:207-232): `UPDATE users SET role_id=3 WHERE user_id AND role_id=2` (404 if 0 rows) then delete assignments. Permission rows stay (re-promotion silently restores them); staff whose `parent_id` is that manager are left pointing at a non-manager.

**get_staff_under_manager** (mf:39-85): manager lookup requires `role_id=2`; reads assignments; builds **independent** `branch_ids` and `dept_ids` sets and filters `u.branch_id = ANY(branches) AND u.dept_id = ANY(depts)` (mf:55-74): the pair semantics are lost (assignments (A,X),(B,Y) also expose A/Y and B/X staff); staff with `parent_id = org` only; if no assignments → `[]`. `get_manager_scope` (mf:376-407) returns the same two sets and `org_id = manager.parent_id` and is used to narrow `/home/*` and attendance lists; any exception returns `(user_id, None, None)` = unrestricted.

**Permissions** (mf:234-373): permission names come from the settings table; save = delete all rows of `(user, org)` then insert enabled known names; `check_*` helpers swallow errors and return False/[]. `permission_checker.py` hard-codes names (`Staff Management`, `Branch Management`, `Department Management`, `Attendance Reports`, `Payroll Management`, `Holiday Management`, `Shift Management`, `Leads Management`, `Incentive Management`, `Company Settings`); the FE screen uses a slightly different list (`Salary Management`, `Requests Management`) — UNVERIFIED which rows exist in the deployed settings table.

**Shifts** (`shift_functions.py`):
- Time parsing `_parse_shift_time` (sf:16-36): accepts `HH:MM`, `h:mm AM`, `hh:mmAM`, `HH:MM:SS`; normalises to `HH:MM:SS`; else 422. The FE sends `hh:mmAM/PM` without space (`mappers/shifts.ts:27-34`).
- **>18 h rule** (`_validate_shift_duration`, sf:39-62; `MAX_PLAUSIBLE_SHIFT_HOURS = 18`): `duration = end − start`, negative → `+24 h` (overnight shifts allowed, e.g. 22:00→06:00 = 8 h); `> 18` → 422 `"This shift works out to {h:.1f} hours (HH:MM to HH:MM). Please double-check the start/end time — especially AM/PM."`. `start == end` gives 0 h and is accepted. Only create and update call it; shifts stored earlier are not re-checked.
- `create_shift` (sf:65-100): `shift_id = MAX(shift_id)+1` over the whole table (sf:76-80; unique index is global, so concurrent creates race → 500 "Failed to create shift"), INSERT `status 1`, `CURRENT_TIMESTAMP`. No duplicate-name check, no org existence check.
- `update_shift` (sf:154-184): `WHERE shift_id AND org_id`; commits before checking `rowcount` (sf:170-173).
- `delete_shift` (sf:187-240): requires `(shift_id, org_id)`; counts `users_additional_info.shift_id = X`; if any → `UPDATE ... SET shift_id = NULL` (sf:211-215); hard `DELETE FROM shift`. The `force` argument is unused.
- Assignment: only through `users_additional_info.shift_id` set by `POST/PUT /addstaff` (default `1`, FE sends `0` for none; see staff doc D8) and the shift-change request flow (`requests_functions.py`, other domain).

**Holidays** (`holiday_functions.py`):
- `get_holidays` (:42-63): month filter via `EXTRACT`; `block_attendance` returned as `bool(col)` so a NULL column reads as **false** while the FE treats null as blocking (`system.ts:50-60`).
- `add_holiday` (:66-85): `RETURNING id`; scope stored as names (`branch_name`/`dept_name` NULL = org-wide); no duplicate check; `date_str` passed straight to SQL.
- `update_holiday` (:106-137): dynamic SET of `name`, optional `date`, optional `block_attendance`; rollback + `success:false` when no row.
- `check_holiday_block(org_id, user_id)` (:140-170): user's branch/dept names via **`branch_info b ON b.branch_id = u.branch_id` without org filter** (:149); holiday must satisfy `date = CURRENT_DATE` (DB session time zone, not IST), `block_attendance = TRUE`, `(branch_name IS NULL OR = user_branch) AND (dept_name IS NULL OR = user_dept)`. Called only from `face_attendance_functions.py:115-116`, only for `action == "punch_in"`: response `{success:false,message:"Today is a holiday (<name>). Attendance is not required.",is_holiday:true}` (HTTP 200).
- `get_working_days` (:173-220): `total − weekly-off weekdays in month − COUNT(DISTINCT holiday date not falling on an off weekday)`; holidays are counted whether or not they block attendance; optional `branch_name`/`dept_name` narrow with `(col IS NULL OR col = X)`.
- Weekly off: `get_off_days` default `[6]`; `set_off_days` UPSERT; weekly offs are **only** used by `get_working_days` — not by punch validation, monthly attendance or salary.

**Wage templates** (functions only): `create_template` auto-numbers `temp_name` when none given (`MAX(numeric names)+1`, :45-51); `update_template` updates amount only (name ignored, no org filter, :69-84); `delete_template` deletes the template row only by `temp_id` (:86-97; assignments and per-user copies stay); `assign_staff_to_wage` loads the template (`user_id IS NULL`) and for each user deletes their other assignments of the same `temp_type`, inserts the assignment and inserts a **per-user copy row with a new UUID** (:178-196) — the cleanup `DELETE ... WHERE temp_id = <template id> AND user_id = <user>` (:188-191, :214) never matches the copy (different `temp_id`), so copies accumulate; if the template id is unknown `template` is None and `template['temp_type']` raises `TypeError` (:180). Report salary joins `assigned_wages` to the **template** row (`report_functions.py:581-595`), so copies are dead data.

## 5. Side effects & integrations

| Flow | Effect |
|---|---|
| holiday block | affects `punch_with_face` punch-in only (response above) |
| manager role/permissions | consumed by login payload (`permissions`) and manager-scoped `/home/*`, `/requests/admin/*`, report routes (`report.py:118,146`) |
| none | none of these flows sends push/e-mail/WhatsApp/WebSocket events |

## 6. Config & env vars

`DB_SCHEMA` (search_path). WP6: `ENFORCE_MANAGER_SCOPE` (`1`/`true`/`yes`/`on` = deny-by-default manager scope, default off; section 3.1 Changes).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | `app.py:48,56,65`; `router/daily_hourly.py` | Three routers claim `/api/holidays`; the file named for wages is a holiday copy that wins. `block_attendance` works **only** because it is mounted first; swapping the order silently drops the flag on add/update (`holiday.py:12-22`). The real wage router does not exist (P0-1..3, P1-5). |
| D2 | High | DDL:741-750 vs `holiday_functions.py:47,73,118,161`; `:13,:28` | Code reads/writes `org_holidays.block_attendance` and table `org_off_days`, neither is in the schema dump; `org_holidays` has no primary key. Deployed state UNVERIFIED; a clean install from the dump fails every holiday read/write. |
| D3 | High | n/a | No `/api/attendance-rule` route/table/enforcement (P0-4). |
| D4 | High | `shift.py` | Four shift-assignment endpoints called by the FE are missing (P0-7). |
| D5 | Med | `holiday_functions.py:160` | Holiday blocking uses DB `CURRENT_DATE` (not IST) → 00:00-05:30 IST punches are checked against yesterday when the DB clock is UTC; branch name lookup ignores org (:149). |
| D6 | Med | `holiday_functions.py:57` | `bool(NULL)` → `false` for rows without the flag; FE interprets absent/null as blocking. Scope stored by name: renaming a branch/department silently orphans holiday scope. |
| D7 | Med | `daily_hourly.py:38-43` / `holiday_functions.py:127-129` | PUT unknown id returns HTTP 200 `success:false`; DELETE of unknown id returns success; duplicates allowed; `date` not validated (500 on bad format). |
| D8 | Med | `manager_functions.py:10-12,126`; unique idx DDL:680 | `dept_id` mandatory (422/400); duplicate pairs → 500; no org/role validation on assign (any active user, any branch/dept id). |
| D9 | Med | `manager_functions.py:55-74,398-400` | Pair semantics lost: branch and department sets are independent (cross-product). `get_manager_scope` fails open (returns unrestricted on any exception). |
| D10 | Med | `manager_functions.py:178`, `:68` | `get_managers`/`get_staff_under_manager` filter `parent_id = org`: a manager promoted from manager-owned staff (parent = another manager), or staff under a manager, are invisible (see staff doc D3). |
| D11 | Med | `manager_functions.py:213-222` | Demotion leaves permission rows and orphan `parent_id`s; `add_staff` update demotion (`add_staff_function.py:584`) leaves assignments (staff doc D1). |
| D12 | Med | `manager_functions.py:241-246` | `GET permissions` does not require role 2; permission-name set mismatch between FE, `permission_checker.py` and the settings table (UNVERIFIED). |
| D13 | Low | `shift_functions.py:76-80` | Global `MAX+1` allocation with a global unique index → race → 500. |
| D14 | Low | `shift_functions.py:170-173` | Update commits before the not-found check; `get_shift` is not org-scoped; start == end accepted (0 h shift). |
| D15 | Med | `daily_hourly_functions.py:180,188-196,214,69-97` | Wage functions are dead code with latent bugs (TypeError on missing template, copy-row leak, name ignored on update, no org scoping on update/delete, delete leaves assignments). |
| D16 | Low | `holiday_functions.py:213` | Working-days counts non-blocking (informational) holidays as non-working. |
| D18 | Med | `report_functions.py:668-675` | Overtime uses `end_time - start_time` as shift length; for overnight shifts (allowed by the 18 h rule, e.g. 22:00-06:00) the value is negative, so overtime minutes are overstated (UNVERIFIED: static reading). |
| D17 | Low | `holiday.py:76`, `holiday_functions.py:22-31` | `off_days` not validated (values outside 0..6, duplicates, empty list accepted). |

## 8. Clockyn requirements

Cross-refs: `backend_changes.md` P0-1..P0-4, P0-7, P1-5, P1-17, P3-9; DD-44, DD-50, DD-54, DD-60, DD-76..78.

### R1 Manager assignment requires a department (DD-54)
- **Current:** `BranchDeptAssignment.dept_id: str` required (mf:10-12), blank rejected 400 (mf:126-127), missing → 422. FE sends one `{branchId, deptId}` per branch × department and sends every department id explicitly; "all departments" would omit `dept_id` and be rejected (`mappers/managers.ts:64-66`).
- **Required (keeps current behaviour working, adds the option the FE decision DD-54(a) wants):**
  1. `dept_id: Optional[str] = None`; `None`/`"ALL"` ⇒ stored as `dept_id NULL` = all current **and future** departments of that branch (the DDL already allows NULL and the unique index uses `COALESCE(dept_id,'')`).
  2. Validate in `assign_manager`: user exists, `status=1`, `role_id IN (2,3)`, same org; each `branch_id` ∈ `branch_info (org_id, status=1)`; each non-null `dept_id` ∈ `dept_info (org_id)`; de-duplicate pairs before INSERT; reject mixing `NULL` and explicit departments for the same branch (400). Return 400 (not 500) with a clear `detail`.
  3. Replace the independent sets by per-pair semantics everywhere. `get_staff_under_manager` filter becomes
     `AND EXISTS (SELECT 1 FROM manager_assignments ma WHERE ma.user_id = %s AND ma.branch_id = u.branch_id AND (ma.dept_id IS NULL OR ma.dept_id = u.dept_id))`.
     `get_manager_scope` (mf:376-407) should additionally return the pair list `[(branch_id, dept_id|None)]`; add a helper `manager_scope_sql(alias='u')` returning that `EXISTS` predicate and use it in the 9 call sites (`home_functions.py:119-120,233-234,298-299,478-479,559-560,626-627,703-704,781-782`, `attendance_functions.py:56-57`); keep returning the legacy `branch_ids`/`dept_ids` lists for backward compatibility (dept list `None` when any NULL pair).
  4. Return `assignments[].dept_id = null`, `dept_name = "All departments"` in `GET /managers/` for NULL rows (FE mapper already tolerates `dept_id: string|null`).
- **Compat:** requests with explicit `dept_id` behave as today; `GET /managers/` shape unchanged.
- **Acceptance:** POST `[{branch_id:1,dept_id:D1},{branch_id:2,dept_id:D1}]` → two rows, role 2; POST `[{branch_id:1}]` → one NULL-dept row and the manager sees every department of branch 1 including departments created later; duplicate pair → 400; foreign branch → 400; assignments (A,X),(B,Y) do not expose staff in (A,Y); re-assign replaces rows.

### R1b Manager role handling on demotion / staff edit
- `remove_manager`: also `DELETE FROM <legacy>_manager_permissions WHERE user_id=%s` (same transaction) and optionally reset `parent_id` of its direct reports (staff doc R4). `PUT /addstaff/{id}` must not touch `role_id` (staff doc R3, P1-4/DD-53). Manager scope for staff/branch/department/report endpoints should be derived from the caller server-side (P1-17) — outline: add a FastAPI dependency `resolve_scope(x_admin_id, x_role_id)` once auth exists; until then keep the FE convention (manager's own id as `org_id`) by making `get_branch_staff`, `delete_branch`, `reassign_staff` accept a manager id and resolve `org_id = users.parent_id` when `users.role_id = 2` (`SELECT parent_id FROM users WHERE user_id=%s AND role_id=2`).

### R2 Shift assignment endpoints (P0-7) — missing, implement in `router/shift.py` + `shift_functions.py`
FE contract (`src/api/shifts.ts:55-85`): lists accept `{success,data:[...]}` or a bare array; rows need `user_id,name,emp_id` (+ `mobile_number,email,branch_name,dept_name` used by the staff mapper); writes return `{success:true,message}` (FE uses `message`).

```python
@router.get("/shift/assigned_staff/{org_id}/{shift_id}")
async def assigned_staff_endpoint(org_id: str, shift_id: int): return await get_shift_staff(org_id, shift_id, assigned=True)
@router.get("/shift/unassigned_staff/{org_id}/{shift_id}")
async def unassigned_staff_endpoint(org_id: str, shift_id: int): return await get_shift_staff(org_id, shift_id, assigned=False)
class AssignShiftRequest(BaseModel): org_id: str; shift_id: int; user_ids: List[str]
@router.post("/shift/assign_staff")      # body AssignShiftRequest
@router.delete("/shift/unassign_staff/{org_id}/{user_id}/{shift_id}")
```
Route names do not collide with `/shift/get_shifts|get_shift|create|update|delete`.

`get_shift_staff` SQL (after verifying `SELECT 1 FROM shift WHERE shift_id=%s AND org_id=%s` else 404 "Shift not found"):
```sql
SELECT u.user_id,u.name,u.emp_id,u.mobile_number,u.email,
       COALESCE(b.branch_name,'') AS branch_name, COALESCE(d.dept_name,'') AS dept_name
FROM users u
LEFT JOIN users_additional_info uai ON uai.user_id = u.user_id
LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = %(org)s
LEFT JOIN dept_info  d ON d.dept_id  = u.dept_id  AND d.org_id = %(org)s
WHERE u.parent_id = %(org)s AND u.role_id IN (2,3) AND u.status = 1
  AND COALESCE(uai.shift_id,0) {= | <>} %(shift)s
ORDER BY u.name
```
(`= ` for assigned, `<>` for unassigned — matches the FE mock: unassigned = everyone not on this shift, including staff on other shifts.) Until the `manager_id` migration, include manager-owned staff with `OR u.parent_id IN (SELECT user_id FROM users WHERE parent_id=%(org)s AND role_id=2)` and join branch/dept on `b.org_id = %(org)s`.

`assign_staff(org_id, shift_id, user_ids)`: 400 if `user_ids` empty; load the shift `(shift_id, org_id, status=1)` → 404; **re-apply the 18 h rule** using the stored times: `_validate_shift_duration(start.strftime('%H:%M:%S'), end.strftime('%H:%M:%S'))` (422 for legacy bad shifts); one transaction:
```sql
UPDATE users_additional_info SET shift_id=%s, updated_at=%s
WHERE user_id = ANY(%s) AND user_id IN (SELECT user_id FROM users WHERE parent_id=%s AND role_id IN (2,3) AND status=1)
```
then, for scoped users without an additional-info row (rowcount shortfall), INSERT a row with `''` for the NOT NULL text columns (as `add_staff` does) and the shift id. Return `{"success":true,"message":"Staff assigned successfully","assigned_count":n}`; users outside the org are ignored (report them in `skipped`).
`unassign_staff(org,user,shift)`: `UPDATE users_additional_info SET shift_id = NULL WHERE user_id=%s AND shift_id=%s AND user_id IN (SELECT user_id FROM users WHERE parent_id=%s ...)`; `rowcount == 0` → 404 "Staff is not assigned to this shift"; return `{"success":true,"message":"Staff unassigned successfully"}`. Use NULL (same as `delete_shift`, sf:211-215; readers treat NULL and 0 as none, `home_functions.py:186`).
`GET /addstaff/{id}` already reads `ua.shift_id` live, so the staff-detail `shift_id` refreshes automatically.
- **Acceptance:** assign 3 users to S1 → `assigned_staff` lists them and `unassigned_staff` does not; assign to a shift of another org → 404; unassign → user in `unassigned_staff`, `shift_id IS NULL`; legacy 20 h shift → 422 on assign; deleting the shift still nulls assignments.

### R2b Shift create/update hardening
Keep the 18 h rule (sf:39-62); additionally reject `start == end` (422), duplicate `(org_id, lower(shift_name))` (400), allocate `shift_id` from a sequence (`CREATE SEQUENCE shift_id_seq`, `setval` to `MAX(shift_id)`), do the `rowcount` check before commit, scope `get_shift` by `org_id` (optional query param, FE passes it for fallback only).

### R3 Holiday `block_attendance` and router mount order (P1-5, DD-44)
- **Current:** D1/D2.
- **Required:** (1) one holiday router: move the model fields of `daily_hourly.py:11-47` into `router/holiday.py` (prefix `/api/holidays`, plus the existing `off-days` routes), delete the holiday copy at `router/daily_hourly.py` (the filename is reused for R6) and the copies at `report.py:16-47`; do **both** in the same release because `daily_hourly` is mounted first. (2) Migration:
```sql
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS block_attendance boolean NOT NULL DEFAULT true;
ALTER TABLE org_holidays ADD PRIMARY KEY (id);            -- dump has none
CREATE TABLE IF NOT EXISTS org_off_days (org_id varchar(36) PRIMARY KEY, off_days integer[] NOT NULL DEFAULT '{6}', updated_at timestamp DEFAULT now());
```
(3) `check_holiday_block`: compute the date in IST (`datetime.now(IST).date()` passed as a parameter instead of `CURRENT_DATE`), join branch/dept with the user's org (`b.org_id = u.parent_id`, or via `manager_id` migration), keep name-based scope for compatibility but also add nullable `branch_id int`/`dept_id varchar` columns populated from names for new rows (DD-44 wants id-based scope; additive). (4) `get_holidays` returns `block_attendance` as stored (NOT NULL now). (5) 404 (not HTTP 200 `success:false`) for unknown holiday on PUT/DELETE, 422 on invalid date, reject duplicates `(org_id, date, name, coalesce(branch_name,''), coalesce(dept_name,''))`, optional bulk `POST /api/holidays/{org}/bulk {name,dates[],branch_name?,dept_name?,block_attendance}` (FE currently issues one POST per date, `system.ts addHolidayDates`). (6) `get_working_days`: count only holidays with `block_attendance = TRUE` (confirm with product).
- **Compat:** response keys unchanged; FE already sends the flag on every write and treats a missing flag as blocking.
- **Acceptance:** `POST {block_attendance:false}` then `GET` returns `false`; punch-in on that date succeeds; with `true` the punch-in returns `is_holiday:true`; mount-order independence (only one router left); holiday dated "today IST" blocks at 00:30 IST.

### R4 `/api/attendance-rule/{org}` (P0-4, DD-60) — missing, implement
- **Table:** 
```sql
CREATE TABLE org_attendance_rule (
  org_id varchar(36) PRIMARY KEY,
  late_punch_in_count int NOT NULL DEFAULT 3 CHECK (late_punch_in_count BETWEEN 1 AND 10),
  consider_as varchar(10) NOT NULL DEFAULT 'Half Day' CHECK (consider_as IN ('Half Day','Leave')),
  created_at timestamp DEFAULT now(), updated_at timestamp DEFAULT now());
```
- **Router** `router/attendance_rule.py`, `APIRouter(prefix="/api/attendance-rule")`, include in `app.py` (any position; paths are unique): `GET /{org_id}` → `{success:true,data:{late_punch_in_count,consider_as}}` returning the defaults (3, `Half Day`) when no row; `POST /{org_id}` body `{late_punch_in_count:int 1..10, consider_as:'Half Day'|'Leave'}` via a Pydantic model with `Literal` and `Field(ge=1, le=10)` (422 otherwise) → UPSERT `ON CONFLICT (org_id) DO UPDATE` → `{success:true,message:"Attendance rule saved"}`. FE reads `success`, `late_punch_in_count`, `consider_as` (`system.ts:80-87,189-205`).
- **Enforcement (otherwise cosmetic):** add `get_attendance_rule(cursor, org_id)` in a new `core_functions/attendance_rule_functions.py`; in the monthly attendance computation (`attendance_functions.py:154-247`, late detection at :195-201) compute `late_count`, then `penalty_units = late_count // late_punch_in_count`; expose `late_penalty_units` and `late_penalty_as` in the response, and in salary computation (`report_functions.py` per-day/absent logic around :660-675 — UNVERIFIED exact location, plus `salary_structure.py`) deduct `penalty_units × (0.5 day if 'Half Day' else 1 day)`. Semantics (grace minutes, per-month reset, interaction with approved leave) need product sign-off (DD-60); implement behind the table row existing so orgs without a saved rule keep current behaviour.
- **Acceptance:** GET without row → defaults; POST 5/Leave → GET returns 5/Leave; POST 0 or 11 or `consider_as:'x'` → 422; with rule 3/Half Day and 6 late days in a month the monthly response shows 2 penalty units and salary deducts 1 day.

### R5 Weekly offs
- Keep `GET/POST /api/holidays/off-days/{org}`; validate `off_days` ⊆ 0..6, de-duplicate and sort; document `[6]` default. Enforcement outside `get_working_days` (monthly attendance day types, leave calendar — P1-3, DD-07/97) is a separate change: expose `weekly_off`/`holiday` day types from `get_off_days` + `get_holidays` in `/home/monthly-attendance` and `/requests/employee/{user}/{org}`.

### R6 Wage template routers (P0-1..P0-3; out of the FE files listed but same root cause)
- Re-create `router/daily_hourly.py` as a wage router (after R3 removed the holiday copy): `APIRouter(prefix="/api")` with `GET /daily-wages/templates/{org_id}` → `get_templates_by_org(org,'D')` wrapped as `{success:true,data:[{id,temp_id?,template_name,temp_name,amount,assigned_count}]}` (map `id`→`temp_id`, add `template_name`), `POST /daily-wages/template {org_id,amount}`, `PUT /daily-wages/template/{id} {org_id,amount}`, `DELETE /daily-wages/template/{id}`; same four under `/hourly-charges` with `'H'`; `GET /wages/assigned/{org}/{template}`, `GET /wages/unassigned/{org}/{template}`, `POST /wages/assign {org_id,user_ids,temp_id}`, `DELETE /wages/unassign/{org}/{user}/{template}` over `get_assigned_staff`, `get_unassigned_staff`, `assign_staff_to_wage`, `unassign_staff_from_wage`.
- Fix before exposing: scope `update_template`/`delete_template` by `org_id` and `temp_type`; delete cascade (`assigned_wages` + per-user copies by `(org_id, user_id, temp_name, temp_type)`); stop creating per-user copies or key them to the template (copies are unused by `report_functions.py:581-595`); guard `template is None` (404) in `assign_staff_to_wage`; update name; return the template the staff was moved off (DD-76). Done as `404` JSON `detail` errors.

### R7 Routing / hygiene
- Remove duplicate holiday routes (R3), add `tests` asserting no two routers register the same `(method, path)` (iterate `app.routes`).

## 9. Test notes

- Fixtures: org with 2 branches × 2 departments, 3 role-3 staff, 1 manager; shifts S1 (09:00-18:00), S2 (22:00-06:00), S3 invalid (06:00AM→11:59PM = 17.98 h passes; 05:00AM→11:59PM = 18.98 h → 422); a second org with `branch_id` 1 and `shift_id` of its own.
- Critical cases: assign manager (explicit pairs / NULL dept / duplicates / foreign ids), pair-scope exposure, demotion cleanup; shift create/update time formats (`09:00AM`, `9:00 AM`, `21:00`), overnight, >18 h, assign/unassign/list; holiday add/update/delete with `block_attendance` both ways, punch-in on holiday (IST date boundary), working-days with off-days `[5,6]`; attendance-rule GET/POST/validation and enforcement; route-uniqueness test.
- No automated tests in the repo; use a scratch schema built from the DDL plus the R3/R4 migrations.
