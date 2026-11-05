# Daily wages & hourly charges — backend context

Domain: daily-wage and hourly-charge templates (`daily_hourly_wages`, `temp_type` `'D'`/`'H'`) and their staff assignment (`assigned_wages`). **The logic exists but no route exposes it**: the frontend calls `/api/daily-wages/*`, `/api/hourly-charges/*` and `/api/wages/*`, none of which is mounted. Sister docs: `salary_and_payslips.md`, `incentives.md`.

Verified against source on 2026-10-19. Backend root `C:\POC\pa_backend\Clockyn`. "DDL dump" = repo-root `*_ddl_latest.sql` (legacy-named).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `core_functions/daily_hourly_functions.py` | 219 | 9 functions: `get_templates_by_org` (`:8-33`), `create_template` (`:35-67`), `update_template` (`:69-84`), `delete_template` (`:86-97`), `get_template_by_id` (`:99-114`), `get_assigned_staff` (`:117-139`), `get_unassigned_staff` (`:142-164`), `assign_staff_to_wage` (`:167-204`), `unassign_staff_from_wage` (`:207-219`). **No importer anywhere** (`grep -rn daily_hourly_functions --include=*.py` finds none) = dead code |
| `router/daily_hourly.py` | 60 | **Misleadingly named: it is a copy of the holiday router** (`APIRouter(prefix="/api/holidays")` `:8`, imports `holiday_functions` `:4-6`). It does not expose wages. Mounted at `app.py:48` (import `app.py:3`) before the real holiday router (`app.py:65`), so it shadows it (P1-5). Out of this domain's scope except that it must be replaced |
| `core_functions/report_functions.py` | `:578-602` | The only consumer of wages: salary calculation |
| `core_functions/add_staff_function.py` | `:759-760` | Staff delete removes `assigned_wages` + `daily_hourly_wages` rows of the user |
| `core_functions/company_functions.py` | `:453` | Org delete removes `daily_hourly_wages` of the org (not `assigned_wages`) |

Other money-bearing mounts: `GET /api/reports/staff-salary/{user_id}` (`router/report.py:49`) and `GET /api/salary/staff-breakdown` (`router/salary.py:30`) display wage results (`daily_wages`, `hourly_calc`).

Frontend callers: `src/api/payroll.ts:344-392` (`WAGE_BASE` `:348`; `listWageTemplates`, `saveWageTemplate`, `deleteWageTemplate`, `listWageStaff`, `assignWages`, `unassignWages`). Screens: Daily Wages (`/admin/payroll/daily-wages`) and Hourly Charges (`/admin/payroll/hourly`), assign dialogs (B6). Reference mock: `e2e/mock-server/routes/payroll.js:111-163`.

## 2. Data model touched

| Table (DDL line) | Columns | Constraints | Notes |
|---|---|---|---|
| `daily_hourly_wages` (`:248`) | `id int identity`, `org_id varchar(36)` NULL, `temp_id varchar(36)` NULL, `temp_name varchar(100)` NOT NULL, `temp_type varchar(1)` NOT NULL, `temp_amount numeric(10,2)` NULL, `att_bandwidth int4` NULL, `created_at`, `updated_at` (naive `timestamp`), `user_id varchar(36)` NULL | PK `id`; indexes on `org_id` (`:261`) and `temp_id` (`:262`); **`temp_id` not unique; no CHECK on amount or `temp_type`; amount nullable** | Row kinds: **template** (`user_id IS NULL`) and **per-staff copy** (`user_id = X`, **new random `temp_id`**, same name/type/amount; see §4.3). `att_bandwidth` is always `NULL` on insert (`daily_hourly_functions.py:62`) and never read; meaning UNVERIFIED |
| `assigned_wages` (`:120`) | `id`, `org_id`, `user_id` (NOT NULL), `temp_id` (NOT NULL), `created_at`, `updated_at` | PK `id` only: **no unique (user,type), no FK to the template** | `temp_id` = the **template's** `temp_id` (insert at `:183-186`), never the copy's |
| `users` (`:991`), `branch_info` (`:160`), `dept_info` (`:267`) | staff list columns | | active role 2/3 of the org (`parent_id`) |
| `attendance` (`:133`) | `work_hours`, `attendance_date` | | present days and hours for the calculation |

Type codes: `'D'` = daily wage (amount per present day), `'H'` = hourly charge (amount per worked hour) (`daily_hourly_functions.py:9-10,36-37`). Money type: DB `numeric(10,2)`; Python `float` (`create_template(amount: float)`, `report_functions.py:589,602` casts `float()`).
Org-delete leaves orphan `assigned_wages`; staff delete cleans both tables (see §1).

## 3. Endpoints

### 3.1 Mounted endpoints (`router/wages.py`, mounted in `app.py` via `wages.routers`)

| Method path | Function (`daily_hourly_functions.py`) | Success | Errors |
|---|---|---|---|
| GET `/api/daily-wages/templates/{org}` and `/api/hourly-charges/templates/{org}` | `get_templates_by_org(org, 'D'/'H')` | `{success,data:[{id,temp_name,template_name,amount(float),att_bandwidth,assigned_count(active staff),created_at}]}` | |
| POST `/api/{daily-wages,hourly-charges}/template` `{org_id,amount[,template_name]}` | `create_template` | `{success,message:'Template saved successfully',id,temp_name,template_name}` (auto-numbered) | 400 amount not number / <=0 / >9999999.99 / >2 decimals, 400 `org_id is required`, 409 duplicate amount in org+type |
| PUT `/api/{daily-wages,hourly-charges}/template/{id}` `{org_id?,amount}` | `update_template` | `{success,message}` | 400, 404 `Template not found` (unknown, other org, other type), 409 |
| DELETE `/api/{daily-wages,hourly-charges}/template/{id}?org_id=` (optional) | `delete_template` (one transaction: assignments, legacy copy rows, template) | `{success,message,unassigned:n}` | 404 |
| GET `/api/wages/assigned/{org}/{temp}` | `get_assigned_staff` | `{success,data:[{user_id,name,emp_id,branch_name,dept_name}]}` active staff only | 404 template not in org |
| GET `/api/wages/unassigned/{org}/{temp}` | `get_unassigned_staff` | same shape; active role 2/3 staff not on THIS template | 404 |
| POST `/api/wages/assign` `{org_id,user_ids,temp_id}` | `assign_staff_to_wage` | `{success,message,assigned,moved_from:[{user_id,temp_id,temp_name}]}`; one template per type per staff, idempotent, no per-user copy rows written | 400 `Select at least one employee`, 404 template / user (foreign, inactive, non role 2/3; names the id) |
| DELETE `/api/wages/unassign/{org}/{user}/{temp}` | `unassign_staff_from_wage` | `{success,message}` | 404 `Assignment not found` |

Business errors use real HTTP statuses with `{detail}`. The table in 3.2 below is the original pre-implementation analysis, kept for the legacy-behaviour notes.

### 3.2 Endpoints the frontend requires (NOT MOUNTED; contract from `src/api/payroll.ts` and the mock, handler = existing function to wrap)

Identity/auth: none exist; ids trusted (P3-1). Plan gate: none in the legacy code (decision in §8.6).

| # | METHOD path | Target handler -> existing function | Request | Success response expected by the app | Notes |
|---|---|---|---|---|---|
| 1 | GET `/api/daily-wages/templates/{org_id}` | `get_templates_by_org(org, 'D')` `:8-33` | path | `{success:true, data:[{id, template_name|temp_name, amount, assigned_count, ...}]}` (`mapWageTemplate` reads `id ?? temp_id`, `template_name ?? temp_name`, `amount ?? temp_amount`, `payroll.ts:157-166`) | function returns rows `{id, temp_name, amount, att_bandwidth, created_at, assigned_count}`; no `template_name` alias; ordered `created_at ASC` |
| 2 | POST `/api/daily-wages/template` | `create_template(org, amount, 'D', template_name=None)` `:35-67` | JSON `{org_id, amount}` (app sends no name, `payroll.ts:360`) | `{success:true, message, id}` | function returns `{id, template_name, temp_name}`; name auto-numbered when omitted |
| 3 | PUT `/api/daily-wages/template/{id}` | `update_template(temp_id, amount)` `:69-84` | JSON `{org_id, amount}` | `{success:true, message}` | function ignores `template_name`; returns bool (false -> 404) |
| 4 | DELETE `/api/daily-wages/template/{id}` | `delete_template(temp_id)` `:86-97` | path | `{success:true, message}` | returns bool |
| 5-8 | same four for `/api/hourly-charges/...` | same functions with `'H'` | same | same | the app derives the base from `WageKind` (`payroll.ts:348`) |
| 9 | GET `/api/wages/assigned/{org_id}/{temp_id}` | `get_assigned_staff` `:117-139` | path | `{success:true, data:[{user_id,name,emp_id,branch_name,dept_name}]}` | JOIN `users` without status filter (includes inactive) |
| 10 | GET `/api/wages/unassigned/{org_id}/{temp_id}` | `get_unassigned_staff` `:142-164` | path | same row shape | active staff **not on this template**, i.e. includes staff on the other template of the same type |
| 11 | POST `/api/wages/assign` | `assign_staff_to_wage(org, user_ids, temp_id)` `:167-204` | JSON `{org_id, user_ids:[str], temp_id}` (`payroll.ts:382`) | `{success:true, message}` | raises `TypeError` when `temp_id` unknown (see §7 D4) |
| 12 | DELETE `/api/wages/unassign/{org_id}/{user_id}/{temp_id}` | `unassign_staff_from_wage` `:207-219` | path | `{success:true, message}` | returns bool (false -> 404 like incentives) |

`get_template_by_id` (`:99-114`) has no caller in the frontend contract (useful for 404/validation). Daily and hourly share the `/api/wages/*` assignment endpoints; the type is derived from the template.
Mock note: the e2e mock's `unassigned` excludes staff already on any template of the same kind and `assign` removes them from the siblings (`payroll.js:146-158`); the real functions list all staff not on *this* template and move silently inside `assign_staff_to_wage` (§4.3). Keep the function semantics and add the "current template" fields (§8.3).

### Changes (WP4b)

Validation of wage templates was already server-side (`parse_amount`: `0 < x <= 9,999,999.99`, two decimals, 400; duplicate amount per organisation and type 409, under an advisory lock);
the shared D-15 validator `payroll_validation.parse_amount` uses the same bounds and wording (a test keeps them identical). The engine pays daily wage = rate x DISTINCT present dates
(a two-session day counts once) and hourly wage = rate x hours of closed sessions; see `salary_and_payslips.md` Changes.

## 4. Business logic

### 4.1 Template list (`:8-33`)
`daily_hourly_wages d LEFT JOIN (assigned_wages grouped by temp_id, filtered by org) aw` filtered `d.org_id = %s AND d.temp_type = %s AND d.user_id IS NULL`, `ORDER BY created_at ASC`. `assigned_count` counts `assigned_wages` rows for the template `temp_id` (inactive staff and orphans included).

### 4.2 Create / update / delete
- Create (`:35-67`): UUID `temp_id` (`:42`); if no `template_name`, auto name = `MAX(CAST(temp_name AS INTEGER) over names matching '^[0-9]+$')+1` computed over **all rows of the org and type, including per-staff copies** (`:46-50`), no lock => concurrent creates can get the same number; INSERT with `att_bandwidth NULL`, `created_at = updated_at = get_utc_now()` (`:56-62`). No amount validation: `0`, negative, and `NULL` are possible at DB level; the app enforces `> 0` client-side (`payroll.ts:359`); duplicates of amount allowed (DD-74: client only).
- Update (`:69-84`): `UPDATE daily_hourly_wages SET temp_amount, updated_at WHERE temp_id = %s` (not constrained by type, org, or `user_id IS NULL`). It only touches rows whose `temp_id` equals the id, i.e. the template; **per-staff copies (different `temp_id`) are not updated**. Name cannot change.
- Delete (`:86-97`): `DELETE FROM daily_hourly_wages WHERE temp_id = %s` (template row only). **`assigned_wages` rows and per-staff copies remain** (no cascade); also not type-/org-scoped. Because copies keep the old name, auto numbering skips the deleted number whenever copies exist (DD-77 "numbering skips deleted numbers").

### 4.3 Assignment (`:167-204`) and the per-staff copy
For each `user_id` in the request, in one transaction:
1. Read the template row `WHERE temp_id = %s AND user_id IS NULL` (`:173-177`); `template` is `None` for an unknown id or for a copy's id.
2. `DELETE FROM assigned_wages WHERE org_id AND user_id AND temp_id IN (templates of the same temp_type with user_id IS NULL)` (`:179-182`) **= silently removes the staff member from every other template of the same type** (and from this one). Dereferences `template['temp_type']` (`:181`) before the `if template:` guard at `:187`, so an unknown template raises `TypeError` -> uncaught as 500 (rollback in `except`, `:199-201`) when `user_ids` is non-empty.
3. `INSERT INTO assigned_wages (org_id, user_id, temp_id, ...)` using the **template** id (`:183-186`).
4. If the template exists: delete `daily_hourly_wages WHERE temp_id = <template id> AND user_id = <user>` (`:188-191`) and insert a **copy** with a **new** `temp_id = uuid4()` and the same name/type/amount/`att_bandwidth` plus `user_id` (`:192-196`). The delete at `:189` can never match (copies have fresh ids), so **every assignment and re-assignment adds another copy** that is never removed except by staff/org delete.
Unassign (`:207-219`): delete the `assigned_wages` row, then `DELETE ... WHERE temp_id = <template id> AND user_id = <user>` (`:214`) which again matches nothing; returns whether an assignment row existed.
Consequences:
- Per-staff copies are **dead data**: salary only joins `assigned_wages.temp_id` to the template (§4.4). Therefore editing a template **does** change the pay of assigned staff immediately (DD-78), even though copies keep the old amount ("template edits not propagating to per-user copies" is true but irrelevant to pay).
- Copies accumulate: N assigns of one staff member = N copies; list queries hide them (`user_id IS NULL`), the auto-name query does not.
- No unique `(user, type)` on `assigned_wages`; the delete-before-insert in step 2 is the only guard (racy under concurrency).

### 4.4 How wages become pay (`report_functions.py:578-602`)
- Daily: `rate = temp_amount` of the first row from `assigned_wages aw JOIN daily_hourly_wages dw ON aw.temp_id = dw.temp_id WHERE aw.user_id = %s AND dw.temp_type = 'D' AND (dw.user_id IS NULL OR dw.user_id = %s) ORDER BY (dw.user_id IS NOT NULL) DESC LIMIT 1` (`:579-587`); `daily_wages = rate * present_days` where `present_days = COUNT(attendance rows in the month)` (`:567-576`, any row counts, even without punch-out, UTC dates).
- Hourly: same query for `'H'` (`:592-600`); `hourly_calc = rate * SUM(attendance.work_hours)`; rows without `work_hours` contribute 0.
- The result is **added on top of** the payslip basic (no exclusivity with a payslip) into `total_earnings` (`:736-738`) and exposed as `daily_wages` / `hourly_calc` in staff-breakdown (`router/salary.py:81-82`) while `salary_type` stays `'MONTHLY_FIXED'` (`:69`).
- Orphaned assignment (template deleted) => join finds nothing => 0, silently.
- `LIMIT 1`: a staff member assigned to two templates of one type (possible only through orphan/legacy data) gets one arbitrary rate.

### 4.5 Time zone / IDs / idempotency
`created_at/updated_at = get_utc_now()` (aware UTC) into naive `timestamp`. IDs `uuid4` strings. Assign is idempotent for the assignment row; template create is not.

## 5. Side effects & integrations
None (no push/e-mail/S3/WebSocket). Debug `print()` in create (`:51,54`).

## 6. Config & env vars
`DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_SCHEMA` only.

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | `app.py:48`, `router/daily_hourly.py` | Wages router missing; the file with that name is a shadowing copy of the holiday router (P0-1..3, P1-5): the Daily Wages / Hourly Charges screens cannot work; the holiday copy changes which handlers answer `/api/holidays/*` |
| D2 | High | `daily_hourly_functions.py:86-97` | Template delete leaves `assigned_wages` and copies: staff silently lose the wage in salary; assignment counts/lists reference dead templates |
| D3 | High | `:179-182` | Assigning moves staff off other templates of the same type with no signal to the caller; the unassigned list does not reveal their current template (DD-76) |
| D4 | Med | `:173-182` | Unknown `temp_id` -> `TypeError` 500 instead of 404; no org/type/user validation; any org's template can be assigned to any user ids |
| D5 | Med | `:187-196,214` | Per-staff copies use a fresh `temp_id` and the cleanup deletes match nothing: unbounded growth, copies never read; template edits "don't propagate" to them |
| D6 | Med | `:69-97` | Update/delete not scoped by org, type or `user_id IS NULL`: a PUT on `/daily-wages/template/{hourly id}` would change an hourly template; cross-tenant edits (no auth) |
| D7 | Med | `:42-62` | No amount validation (0, negative, NULL, huge -> numeric overflow 500); auto-name race and copies inflate the counter; amount duplicates allowed |
| D8 | Med | `report_functions.py:567-576,589` | Daily wage pays for any attendance row (incomplete punches, UTC date), no weekly off/holiday/leave logic; hourly uses `work_hours` (nullable) |
| D9 | Low | DDL `:248-262` | `temp_id` not unique, no FK/unique/CHECK, `att_bandwidth` unused, `user_id` doubles as copy marker |
| D10 | Low | `company_functions.py:453` | Org delete removes templates but not `assigned_wages` |
| D11 | Low | `:117-139` | Assigned list includes inactive staff, unassigned only active |

## 8. Clockyn requirements

References: `backend_changes.md` P0-1 (daily wages), P0-2 (hourly charges), P0-3 (wage assignment), P1-5 (replace `router/daily_hourly.py`), P1-9 (delete cascade), P2-7 (validation); `DEFERRED_DECISIONS.md` DD-72 (decimals), DD-73 (bounds), DD-74 (uniqueness), DD-76 (assignment moves staff), DD-77 (delete rule), DD-78 (edit changes pay).

### 8.1 Mount the missing routes (P0-1, P0-2, P0-3)
Current: not mounted; frontend calls get HTTP 404 (`router/daily_hourly.py` serves holidays).
Outline:
1. **Replace `router/daily_hourly.py`** (delete the holiday copy and the `daily_hourly` import/mount at `app.py:3,48`; the real holiday router `router/holiday.py` stays; keep the holiday de-duplication work in the holiday domain, P1-5). Create `router/wages.py` (name is free; keep the file name `daily_hourly.py` if a rename of mounts is undesirable, but its content must be wages).
2. A factory `build_template_router(prefix, temp_type)` producing, for `('/api/daily-wages','D')` and `('/api/hourly-charges','H')`, routes #1-4; plus one router `/api/wages` with #9-12. Include in `app.py` next to `app.py:48-49`.
3. Handlers (async def is fine but the functions are blocking; prefer plain `def` so FastAPI runs them in the threadpool):
   - `GET /templates/{org_id}` -> `{"success": True, "data": get_templates_by_org(org_id, temp_type)}`; add `template_name` alias in the SELECT (`d.temp_name AS template_name`) and `temp_type`.
   - `POST /template` body `WageTemplateCreate{org_id:str, amount:Decimal, template_name:Optional[str]}` -> `create_template(...)` -> `{"success": True, "message": "Template saved successfully", "id": r["id"], "template_name": r["template_name"]}`.
   - `PUT /template/{id}` body `{org_id?, amount}` -> `update_template` (extend with `temp_type` and `org_id` predicates and `user_id IS NULL`); 404 if `False`.
   - `DELETE /template/{id}` -> cascade delete (§8.2); 404 if nothing deleted.
   - `/api/wages/assigned|unassigned/{org}/{temp}` wrap the functions into `{success, data}`; 404 if the template does not exist.
   - `POST /api/wages/assign` body `AssignWagesRequest{org_id, user_ids:List[str], temp_id}` -> validate (§8.3), call `assign_staff_to_wage`, return `{success, message:"Staff assigned successfully", moved:[...]}`.
   - `DELETE /api/wages/unassign/{org}/{user}/{temp}` -> `unassign_staff_from_wage`; 404 `Assignment not found` when `False`.
4. Response shape (compatible with `mapWageTemplate` and `ensureSuccess`): list `{success:true, data:[{id, temp_name, template_name, amount, assigned_count, created_at}]}`; money as JSON numbers; errors via `HTTPException` (real 4xx).
5. Raise real 4xx and re-raise `HTTPException` (P2-1).
Acceptance: create (`{org_id, amount:500}`) returns id and shows in list with `assigned_count 0` and an auto name `"1"`; update changes amount; second template gets `"2"`; assign two staff -> counts 2, staff listed in `assigned`, absent from `unassigned` of that template; hourly and daily lists are independent; unknown ids -> 404.

### 8.2 Delete cascade (P1-9, DD-77)
Required: deleting a wage template removes its assignments and any per-staff rows in one transaction, scoped by org and type.
```sql
-- inside delete_template(temp_id, org_id, temp_type), one transaction:
DELETE FROM assigned_wages WHERE temp_id = %s AND org_id = %s;
DELETE FROM daily_hourly_wages WHERE temp_id = %s AND org_id = %s AND temp_type = %s AND user_id IS NULL;  -- rowcount 0 -> 404 + rollback
```
Product choice (recommended default = cascade, as the client confirms first): alternative 409 `{assigned_count}` when staff are assigned unless `?force=true`.
Server-side numbering (DD-77 mentions the client-side "newest or highest only" rule): keep it client-side; but compute auto names inside the same transaction with `pg_advisory_xact_lock(hashtext(org_id || temp_type))` to stop duplicates.

### 8.3 Assignment semantics (DD-76) and the per-staff copy
Current: §4.3. Required:
1. **Stop writing per-staff copies** (recommended; salary does not read them, §4.4): in `assign_staff_to_wage` drop `:187-196` and in unassign drop `:214`; one-off data fix below. This makes DD-78 explicit (template edit changes pay immediately) and ends unbounded growth. If product wants frozen per-staff rates instead, the salary join must use the copy (`assigned_wages.temp_id` should then point to the copy) and `update_template` must propagate; do **not** keep the hybrid.
2. Keep "one template per type per staff", but make it explicit and safe: add `temp_type` to `assigned_wages`, enforce with a unique index, and return the moves: response `{success, message, moved:[{user_id, from_temp_id, from_amount}]}` (additive; the dialog hint copy stays valid).
3. `GET /api/wages/unassigned/{org}/{temp}`: add `current_temp_id`, `current_temp_name`, `current_amount` (the same-type template the staff is on, else null) so the app can ask for confirmation (DD-76 option a). Keep listing them (the app expects to be able to pick them).
4. Validation in assign: template exists (`get_template_by_id`) and `org_id` matches, 404 otherwise (fixes `TypeError`); every `user_id` is an active role-2/3 user of that org (400 listing offenders); `user_ids` non-empty.
5. Unassign returns 404 `Assignment not found` when nothing deleted (parity with `incentive.py:344`).
6. `get_assigned_staff`: add `AND u.status = 1` (the mock does, `payroll.js:145`) or return `is_active` (DD-65).
Migration sketch:
```sql
-- 1. remove unused per-staff copies (assigned_wages never references them; verify on the deployed DB first)
DELETE FROM daily_hourly_wages WHERE user_id IS NOT NULL;
-- 2. remove orphan assignments
DELETE FROM assigned_wages aw WHERE NOT EXISTS
  (SELECT 1 FROM daily_hourly_wages d WHERE d.temp_id = aw.temp_id);
-- 3. integrity
ALTER TABLE daily_hourly_wages ADD CONSTRAINT uq_dhw_temp_id UNIQUE (temp_id);
ALTER TABLE daily_hourly_wages ADD CONSTRAINT ck_dhw_type CHECK (temp_type IN ('D','H'));
ALTER TABLE daily_hourly_wages ADD CONSTRAINT ck_dhw_amount CHECK (temp_amount IS NOT NULL AND temp_amount > 0);  -- after fixing violating rows
CREATE UNIQUE INDEX uq_dhw_org_type_name ON daily_hourly_wages (org_id, temp_type, temp_name);
ALTER TABLE assigned_wages ADD COLUMN temp_type varchar(1);
UPDATE assigned_wages aw SET temp_type = d.temp_type FROM daily_hourly_wages d WHERE d.temp_id = aw.temp_id;
ALTER TABLE assigned_wages ALTER COLUMN temp_type SET NOT NULL;
ALTER TABLE assigned_wages ADD CONSTRAINT fk_aw_temp FOREIGN KEY (temp_id) REFERENCES daily_hourly_wages(temp_id) ON DELETE CASCADE;
CREATE UNIQUE INDEX uq_aw_user_type ON assigned_wages (org_id, user_id, temp_type);
-- optional: stop duplicate amounts per org+type (DD-74 decision)
-- CREATE UNIQUE INDEX uq_dhw_org_type_amount ON daily_hourly_wages (org_id, temp_type, temp_amount);
```
(Statement 3 works only after step 1, because copies share names with templates. Keep the `user_id` column as legacy or drop it later; `add_staff_function.py:759-760` and `company_functions.py:453` deletes keep working. Add `DELETE FROM assigned_wages WHERE org_id=%s` to the org delete near `company_functions.py:453`, though the FK cascade makes it redundant once the templates go first.)
Acceptance: assign A to D1 then D2 -> A only on D2, response `moved` lists D1; unassigned(D1) now shows A with `current_temp_id = D2`; delete D2 -> A has no daily assignment and salary `daily_wages = 0`; table has no copy rows after repeated assigns.

### 8.4 Validation bounds & numeric types (DD-72, DD-73, P2-7)

| Field | Rule |
|---|---|
| `amount` | `Decimal`, `> 0`, `<= 99_999_999.99` (numeric(10,2)), max 2 dp, reject NaN/Infinity (currently only the client checks `> 0`, `payroll.ts:359`) |
| daily rate sanity | product maximum (e.g. <= 100,000 per day) and hourly (<= 10,000 per hour); UNVERIFIED limits (DD-73 open) |
| `template_name` | optional, trimmed 1-100 chars (`varchar(100)`), unique per org+type; auto name when omitted |
| `org_id`, `temp_id`, `user_ids` | non-empty strings; `user_ids` max length (e.g. 500) to bound the loop |

Numeric types: move from `float` to `Decimal` (Pydantic `condecimal(gt=0, max_digits=10, decimal_places=2)`), pass `Decimal` to psycopg2, and in `get_staff_salary` compute `rate * present_days` / `rate * total_hours` with `Decimal` (hours are `numeric(5,2)`), rounding half-up to 2 dp once at the end. Wire format stays JSON numbers.

### 8.5 Salary effects to keep consistent
- Document and test that `daily_wages = rate * present_days` and `hourly_calc = rate * total_hours` are additive to basic (`salary_and_payslips.md` §4.2); expose `rate`/`present_days`/`total_hours` in the breakdown for transparency (`salary_and_payslips.md` §8.2).
- Present-day definition (attendance rows, UTC date, incomplete punches) needs a product decision before wages are used for real pay (D8): recommend counting only rows with both punch-in and punch-out, IST date, excluding holidays/weekly offs per the attendance rule (P0-4).

### 8.6 Plan gate
Legacy had none for wages. Recommended: gate the three wage routers with `check_plan_feature(org_id, 'salary_structure')` (same as payslip create/list, only expiry blocks) for consistency with the Payroll area; DD-13 states the app gates only the Settings row, so the server gate is the real enforcement. Org id comes from the path (`templates/{org}`, `assigned|unassigned/{org}/...`, `unassign/{org}/...`) or the body (`POST /template`, `POST /assign`); for `PUT/DELETE /template/{id}` derive it from the template row. Decision needed.

## 9. Test notes
No automated tests in the repo; no existing tests exercise these functions (they are not imported). Fixtures: one org, five active staff + one inactive, an expired org, daily and hourly templates, attendance with `work_hours`.
Cases: (1) auto-numbering `"1","2","3"` and after deleting `"3"` (with and without existing copies) the next name; (2) update amount and verify `get_staff_salary` changes without re-assigning (DD-78); (3) assign A to D1, then D2 -> verify `assigned_wages` has one `D` row, copies count (currently 2, target 0), and `unassigned(D1)` contains A; (4) unassign twice -> second 404 (target); (5) delete assigned template -> orphan today (`assigned_wages` row stays, salary wage 0), target cascade; (6) unknown `temp_id` in assign -> `TypeError` 500 today, 404 target; (7) amounts `0`, `-5`, `1e9`, `NaN` -> rejected (target); (8) daily wage for a month with 3 attendance rows (one without punch-out) = `3 * rate` today; (9) hourly wage with NULL `work_hours` rows; (10) staff delete clears both tables (`add_staff_function.py:759-760`); (11) route-mount smoke test: the 12 paths return 2xx/404 (not "Not Found" from a missing router) and `/api/holidays/*` still answers exactly once.
