# Incentives — backend context

Domain: incentive templates of four types (COUNT, TURNOVER, OVERTIME, BONUS), their assignment to staff, and how they feed salary. Sister docs: `salary_and_payslips.md` (salary formula, plan gate), `wages_daily_hourly.md` (same template/assignment design for wages).

Verified against source on 2026-10-19. Backend root `C:\POC\pa_backend\Clockyn`. "DDL dump" = repo-root `*_ddl_latest.sql` (legacy-named; only schema source, `migrations/` holds only a README).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/incentive.py` | 348 | `APIRouter()` with **no prefix** (`:22`); 25 routes with full `/api/incentive/...` paths; Pydantic bodies `:24-71`; every handler wraps in `try/except Exception -> HTTPException(500, str(e))` |
| `core_functions/incentive_functions.py` | 459 | All SQL: list/details (`:8-89`), 4 creates (`:91-196`), 4 updates (`:198-292`), delete (`:294-314`), assigned/unassigned (`:317-356`), assign (`:359-442`), unassign (`:445-459`) |
| `core_functions/report_functions.py` | `:604-678` | The only place incentives affect pay (`get_staff_salary`) |
| `app.py` | `:49` | `app.include_router(incentive.router)` |

Frontend callers: `src/api/payroll.ts:394-607` (families, assignment). Overtime report: `src/api/reports.ts:88-93` (route missing, see §8.1). Screens: Count/Turnover/Overtime/Bonus templates + assign dialogs (B6), Overtime report (B7).
Permission helper `has_incentive_management` exists (`core_functions/permission_checker.py:109-110`) but is never called by this router (grep: only `manager.py:92` and `report.py:8` import from that module).

## 2. Data model touched

| Table (DDL line) | Columns | Constraints | Notes |
|---|---|---|---|
| `incentive_templates` (`:555`) | `incentive_id varchar(36)` (unique index `:569`), `org_id`, `user_id` (never written by this code; legacy), `type incentive_type` NOT NULL, `template_name varchar(150)` NOT NULL, `created_at`, `updated_at` (naive `timestamp`) | PK `id`; indexes on incentive_id, org_id, user_id | One row per template. **The enum `incentive_type` has no `CREATE TYPE` in the dump (referenced at `:559`)**; the code uses labels `COUNT`, `TURNOVER`, `OVERTIME`, `BONUS` (`incentive_functions.py:102,128,156,182`) (UNVERIFIED: complete label list on the deployed DB) |
| `incentive_count_details` (`:518`) | `incentive_id` (FK -> templates), `count_upto_basic_salary int4`, `next_count int4`, `each_incentive numeric(12,2)`, `user_id` | PK `id`, index on incentive_id; FK `:532` | `user_id IS NULL` = the template row; `user_id = X` = per-staff copy keeping the **same `incentive_id`** |
| `incentive_turnover_details` (`:574`) | `turnover_upto_basic_salary numeric(14,2)`, `after_target_incentive_percent numeric(5,2)`, `user_id` | FK `:587` | percent max 999.99 |
| `incentive_overtime_details` (`:537`) | `duration_minutes int4`, `amount numeric(12,2)`, `user_id` | FK `:550` | |
| `incentive_bonus_details` (`:499`) | `bonus_percent numeric(5,2)` NOT NULL, `month varchar(15)`, `year int4`, `user_id` | FK `:513` | month stored as the English month name (`'January'`...) |
| `assigned_incentives` (`:108`) | `org_id`, `user_id`, `incentive_id`, `created_at`, `updated_at` | PK `id` only: **no unique (org,user,incentive), no FK** | Assignment rows |
| `users` (`:991`), `branch_info` (`:160`), `dept_info` (`:267`) | staff list columns | | `parent_id` = org; active role 2/3 |
| `attendance` (`:133`), `shift` (`:934`), `users_additional_info` (`:1032`) | work_hours, shift times, `shift_id` | | overtime computation |

Per-staff copy design: on assign, every detail row of the template is copied for the user (`incentive_functions.py:395-434`). Details tables carry `user_id`; the details FK is to `incentive_templates.incentive_id`, so a template cannot be deleted while detail rows exist (hence the ordered deletes at `:299-309`). Cleanup on other deletes: staff delete removes per-user detail rows, `assigned_incentives` and `incentive_templates WHERE user_id` (`add_staff_function.py:758,766-770`); org delete removes details + templates (`company_functions.py:444-448`) but **not `assigned_incentives`** (no statement in `company_functions.py`; grep).
Money/percent types: DB `numeric(12,2)` / `numeric(14,2)` / `numeric(5,2)` / `int4`; boundary types: Pydantic `float` and `int` (`router/incentive.py:24-66`); reads return `Decimal` that FastAPI serialises as JSON numbers.

## 3. Endpoints

> **Current contract:** the rows below record the pre-WP4b behaviour (line numbers refer to it); the **Changes (WP4b ...)** block after the tables is authoritative for what the endpoints do now.

Identity/auth: none (no caller id, org from path/body, P3-1). Plan gate: `plan_gate('salary_structure')` on the router (D-12; the original code had none). Response envelope `{success:true, data|message}`; errors are HTTP 500 with `detail=str(e)` (a nested 404 raised inside `try` is only preserved where `except HTTPException: raise` is present: update/delete/unassign handlers); `ValueError`/DB errors surface as 500 with raw text.

`{T}` below is one of `count`, `turnover`, `overtime`, `bonus`.

| # | METHOD path | Handler (router:line -> function) | Request | Success response | Notes / errors |
|---|---|---|---|---|---|
| 1-3 | GET `/api/incentive/count/{org_id}` `.../turnover/{org_id}` `.../overtime/{org_id}` | `:74`, `:128`, `:182` -> `get_incentives_by_org(org, TYPE)` `incentive_functions.py:8-66` | path | `{success, data:[row...]}` rows below | 500 on error |
| 4 | GET `/api/incentive/bonus/{org_id}` | `:245` -> same | path | rows + `user_ids:[assigned user ids as text]` (`incentive_functions.py:44-46`) | |
| 5-8 | GET `/api/incentive/{T}/details/{incentive_id}` | `:83`, `:137`, `:191`, `:236` -> `get_incentive_details` `:68-89` | path | `{success, data:[ALL detail rows for this incentive_id]}` (`SELECT *`, no ORDER BY, includes per-staff copies) | unknown id -> `data: []`, no 404; wrong type for the id -> `[]` |
| 9 | POST `/api/incentive/count` | `:92` -> `create_count_incentive` `:91-113` | `{org_id, template_name, count_upto:int, next_count:int, each_incentive:float}` all required | `{success, message:"Incentive created successfully", data:{incentive_id, template_name}}` | 500 on DB error (no rollback call; pool rolls back on return) |
| 10 | POST `/api/incentive/turnover` | `:146` -> `:115-143` | `{org_id, template_name, turnover_upto:float, incentive_percent:float}` | same | `print()` debug |
| 11 | POST `/api/incentive/overtime` | `:200` -> `:145-167` | `{org_id, template_name, duration_minutes:int, amount:float}` | same | |
| 12 | POST `/api/incentive/bonus` | `:254` -> `:169-196` | `{org_id, template_name, bonus_percent:float, month:str, year:int}` | same | falsy month or year -> `ValueError` -> 500 (`:171-172`) |
| 13-16 | PUT `/api/incentive/{T}/{incentive_id}` | `:101`, `:155`, `:209`, `:263` -> `update_*` `:198-292` | count `{count_upto,next_count,each_incentive}`; turnover `{turnover_upto,incentive_percent}`; overtime `{duration_minutes,amount}`; bonus `{bonus_percent, month:str='' , year:int=0}` (`:63-66`) | `{success, message:"Incentive updated successfully"}` | `template_name` is **not updatable**; 404 `Incentive not found` when the `incentive_templates` UPDATE hits 0 rows (`rowcount` is of the last statement) |
| 17-20 | DELETE `/api/incentive/{T}/{incentive_id}` | `:114`, `:168`, `:222`, `:276` -> `delete_incentive(id, TYPE)` `:294-314` | path | `{success, message}` | 404 if no template row; wrong `{T}` for the id -> FK violation 500 (details of the real type remain, §7 D6) |
| 21 | GET `/api/incentive/unassigned-all/{org_id}` | `:290-314` (inline SQL) | path | `{success, data:[{user_id,name,emp_id,branch_name,dept_name}]}` | docstring says "not assigned to any incentive" but the query has **no exclusion**: returns ALL active staff (role 2,3) of the org |
| 22 | GET `/api/incentive/assigned/{org_id}/{incentive_id}` | `:316` -> `get_assigned_incentive_staff` `:317-335` | path | same row shape | includes inactive users (no `status` filter); `JOIN users` ignores org of the user |
| 23 | GET `/api/incentive/unassigned/{org_id}/{incentive_id}` | `:323` -> `:338-356` | path | same row shape | active role-2/3 staff of the org **not on this template**: staff already on other templates of the same type are included |
| 24 | POST `/api/incentive/assign` | `:330` -> `assign_incentive_staff` `:359-442` | `{org_id, user_ids:[str], incentive_id}` | `{success, message:"<n> staff assigned successfully"}` (n = `len(user_ids)`, even 0 or unknown template) | rollback on error (`:437-439`); bonus template with null month/year -> `ValueError` 500 |
| 25 | DELETE `/api/incentive/unassign/{org_id}/{user_id}/{incentive_id}` | `:338` -> `unassign_incentive_staff` `:445-459` | path | `{success, message}` | 404 `Assignment not found` if no `assigned_incentives` row (the per-user detail rows are still deleted and committed) |

List row shapes (`incentive_functions.py:15-51`): all rows carry `incentive_id, template_name, type, created_at, assigned_count` (count of `assigned_incentives` rows for the org). COUNT adds `count_upto_basic_salary, next_count, each_incentive`; TURNOVER adds `turnover_upto_basic_salary, after_target_incentive_percent`; OVERTIME adds `duration_minutes, amount`; BONUS adds `bonus_percent, month, year, user_ids`. Details join only `user_id IS NULL` rows (template row), ordered `created_at ASC`.

Added (step 1): `GET /api/incentive/overtime-report/{org_id}?month=&year=` (`router/incentive.py` -> `core_functions/overtime.py:compute_overtime`) returns `{success, data:[{user_id, ot_days, ot_minutes, ot_amount}]}`, one row per active role 2/3 staff member of the org with an OVERTIME template assigned; distinct attendance dates, wrapped night-shift duration, monthly aggregate `max(0, worked - shift x present_days)`, `floor(ot/duration) x amount`; `month` 1..12 and `year` 2000..2100 else 422, both default to the current IST month. The salary engine (`report_functions.get_staff_salary`) is not yet switched to this helper.

Route-order note: all static segments differ (`count|turnover|overtime|bonus|assigned|unassigned|unassigned-all|assign|unassign`), so no shadowing today; the missing `GET /api/incentive/overtime-report/{org}` will not clash with `/overtime/{org_id}` (different first segment).

### Changes (WP4b, decisions D-9, D-15, D-21)

- **Validation (D-15)**, in `incentive_functions.py` via `payroll_validation.py`; failures are real HTTP errors with the app's wording:
  `template_name` required (400 `Template name is required`, <= 150 chars); COUNT `count_upto` / `next_count` whole numbers >= 1 and
  `each_incentive` `0 < x <= 9,999,999.99` with 2 decimals; TURNOVER `turnover_upto` amount and `incentive_percent` `0 < x <= 100`;
  OVERTIME `duration_minutes` whole 1..1440 and `amount` > 0; BONUS `bonus_percent` `0..100` (0 allowed), `month` an English month name
  (stored canonical, e.g. `October`) and `year` 2000..2100 (create: required; update: empty month / year 0 keep the stored value).
  Messages: `Must be a valid number`, `Enter a number between 0-100`, `Amount must be greater than 0`, `Amount must not exceed 9999999.99`,
  `Amount can have at most 2 decimal places`, `Month is required`.
- **Uniqueness per organisation and type (409)**, checked under `pg_advisory_xact_lock(hashtext('incentive:<org>:<TYPE>'))` against the template rows
  (`user_id IS NULL`), excluding the template itself on update: COUNT (each of `count_upto`, `next_count`, `each_incentive`):
  `This value already exists in Count <name>`; TURNOVER `turnover_upto`: `This value already exists in Turnover <name>`; OVERTIME duration:
  `This duration already exists in Over Time <name>`; BONUS percent: `This percentage already exists in Bonus <name>`.
- Update of an unknown template is still 404; updates read the template's organisation from `incentive_templates.org_id`.
- Router: `HTTPException` is re-raised before the generic handler and 500 responses no longer carry `str(e)` (`Failed to process the request`).
- **Overtime** (D-9): `GET /api/incentive/overtime-report/{org}` and the salary engine call `overtime.overtime_for_month`; formula unchanged (monthly
  aggregate), with the two-session and night-shift fixes and auto-closed days skipped (see `salary_and_payslips.md` Changes).
- **Formulas kept (D-21)**: a COUNT template pays its flat `each_incentive` to every assigned staff member, TURNOVER pays `basic x percent`, BONUS pays
  `basic x percent` only in its month / year; `count_upto`, `next_count` and `turnover_upto` are stored but unused because the database holds no per-staff count
  or turnover figure (inputs are a separate feature).

## 4. Business logic

### 4.1 Create
Generate `uuid4` (`incentive_functions.py:96`), `now = get_utc_now()` (tz-aware UTC written into naive `timestamp`, so stored wall time is UTC), INSERT `incentive_templates` with `%s::incentive_type` cast (`:100-102`), INSERT the type's details row with `user_id` NULL, commit, return `{incentive_id, template_name}`. No uniqueness (name or values), no validation of ranges, `org_id` not checked to exist. `template_name` is required and free text (the app auto-numbers `"1"`, `"2"`, ... client side: `payroll.ts:240-246`). Over-long name (>150) or `count_upto` beyond int4 -> DB error -> 500 with driver text.

### 4.2 Update
Updates **every** detail row with that `incentive_id` (no `user_id IS NULL` filter): `incentive_functions.py:203-207, 224-228, 245-249, 275-279`. Therefore a template edit **does propagate** to all per-staff copies (contrast wages, `wages_daily_hourly.md` §4.3). Then touches `incentive_templates.updated_at` and uses that statement's `rowcount` for the 404 test. Consequences: type is not verified (a PUT `/count/{bonus id}` updates nothing but returns success because the template row exists); no org check; `template_name` cannot be changed.
Bonus update (`:261-292`): reads the existing `(month, year)`; `final_month = month if month not in (None,'') else existing`; `final_year = year if year not in (None,0) else existing` (`:272-273`); so the endpoint **accepts month/year** and an empty string / 0 means "keep"; the frontend sends them only when edited (`payroll.ts:539-547`). Month is not validated to be a real month name.

### 4.3 Delete
`delete_incentive` (`:294-314`): delete the type's detail rows (all rows of the incentive, copies included) per the `{T}` in the URL, then `DELETE FROM assigned_incentives WHERE incentive_id`, then `DELETE FROM incentive_templates` and return the **last** statement's `rowcount>0`; commit before the test, so a 404 still means "committed deletions of children". No try/except/rollback; relies on the pool rolling back an aborted transaction at `putconn`.

### 4.4 Assignment
`assign_incentive_staff` (`:359-442`) in one transaction: read the template row of each detail table (`user_id IS NULL`, `:365-387`, `fetchone`), then for each `user_id`: delete any existing `(org,user,incentive)` assignment, INSERT a fresh `assigned_incentives` row (`:390-394`), and for each detail table that has a template row delete the user's copy and insert a new copy with the template values and `user_id` (`:395-434`). Semantics:
- Re-assigning the same pair is idempotent (delete + insert).
- **A staff member may be on many templates of the same type** (nothing removes them from others). Effect in `get_staff_salary`: COUNT amounts **sum** across templates, TURNOVER sums, BONUS sums (only matching month/year), but OVERTIME uses `fetchone()` of an unordered join, so with several overtime templates the applied template is **nondeterministic** (`report_functions.py:651-656`). (The frontend DD-76 says "overtime allows several templates per staff; bonus warns but does not prevent".)
- `user_ids` not validated against org/status/role; unknown `incentive_id` inserts dangling `assigned_incentives` rows (no FK) and reports success.
- `unassigned/{org}/{incentive}` lists everyone not on *this* template (staff on other templates of the same type included, no marker of which). `unassigned-all` lists everyone (see #21).
- Unassign (`:445-459`): delete assignment, then delete the user's copies in all four detail tables (committed regardless), returns `deleted` boolean for the 404.
- Assigned list returns `user_id` (+ `name`, `emp_id`, `branch_name`, `dept_name`); the payslip staff picker uses `staff_id`/`staff_name`/`department_name` (see salary doc §8.4).

### 4.5 How incentives become pay (`report_functions.py:604-678`)
Per assigned template of type COUNT/TURNOVER/BONUS (`:612-617`), pick the user's detail row first, else the template row (`ORDER BY (user_id IS NOT NULL) DESC LIMIT 1`):
- COUNT: `+= each_incentive` once per template. `count_upto_basic_salary` and `next_count` are **never used**; no count of orders/leads is read anywhere (`:619-627`).
- TURNOVER: `+= basic * after_target_incentive_percent / 100` where `basic` = payslip basic (0 without a payslip); `turnover_upto_basic_salary` unused (`:629-637`).
- BONUS: `+= basic * bonus_percent / 100` only if `year == requested year` and `month == month_names[requested month]` exact string (`:639-647`).
- OVERTIME (`:649-678`): described in `salary_and_payslips.md` §4.2 step 5: `ot_slots = floor(max(0, total_hours*60 - shift_minutes*present_days) / duration_minutes)`, `ot_amt = ot_slots * amount`; no shift => all worked minutes count; overnight shift => negative `shift_minutes`.
So count/turnover "targets" in the UI are informational only today.

## 5. Side effects & integrations
None (no push/e-mail/S3/WebSocket). Debug `print()` in `create_turnover_incentive` (`:123,138`). No audit trail.

## 6. Config & env vars
`DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_SCHEMA` only.

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | all routes | No authentication/authorisation and no org scoping on update/delete/details/assign/unassign (id alone is enough): cross-tenant edits and deletes |
| D2 | High | `report_functions.py:619-647` | COUNT and TURNOVER targets/`count_upto`/`turnover_upto` are ignored: a staff member on a count template always earns the flat `each_incentive`; paying/overpaying depends on assignment only |
| D3 | Med | `router/incentive.py:290-314` | `unassigned-all` returns every active staff member (contradicts name/docstring); client must filter itself |
| D4 | Med | `incentive_functions.py:338-356` | `unassigned/{org}/{id}` includes staff already on sibling templates of the type; assigning stacks incentives (sums) and, for overtime, picks an arbitrary template |
| D5 | Med | `incentive_functions.py:171-172`, router catch-alls | Validation errors (`ValueError`) and DB errors return 500 with raw driver text; no 4xx |
| D6 | Med | `incentive_functions.py:294-314` | Wrong `{T}` in DELETE (e.g. `/count/{bonus id}`) deletes nothing of the real type, then the template DELETE fails on the details FK -> 500 (the uncommitted work is rolled back only because the pool rolls back on `putconn`); PUT with the wrong type returns success without changing details |
| D7 | Med | `incentive_functions.py:359-442` | No validation of staff/template/org; unknown template returns success and inserts dangling assignments; no unique index so repeated concurrent assigns can double rows |
| D8 | Med | `report_functions.py:651-656` | Overtime template selection nondeterministic when several are assigned; negative shift minutes for overnight shifts (`:668`); no-shift staff treated as all-OT |
| D9 | Low | `incentive_functions.py:68-89` | Details endpoint returns all rows (copies included, unordered); the app takes `[0]` (`payroll.ts:407`); safe only because updates propagate to copies |
| D10 | Low | `incentive_functions.py:445-459` | Unassign of an unassigned pair returns 404 but still deletes/commits copies; assign message counts requested ids not changed rows |
| D11 | Low | `company_functions.py:444-448` | Org delete leaves `assigned_incentives` rows (and `assigned_wages`) behind |
| D12 | Low | DDL `:555-590` | `assigned_incentives` has no unique/FK; `incentive_type` enum DDL absent from the dump; legacy `incentive_templates.user_id` unused |
| D13 | Low | bonus `month` free text, `year` any int | `get_staff_salary` matches `month_names[month]` exactly: `"Jan"`, lowercase, or localized names silently never pay; bonus_percent not capped at 100 (DD-74) |
| D14 | Low | router | Missing `except HTTPException` in list/details/create/assign handlers: any HTTPException raised inside would be rewrapped as 500 text |

## 8. Clockyn requirements

References: `backend_changes.md` P0-5 (overtime report), P1-9 (delete), P2-1, P2-6, P2-7, P3-1; `DEFERRED_DECISIONS.md` DD-73 (upper bounds), DD-74 (turnover percent/uniqueness), DD-76 (assignment rules), DD-77 (delete rule), DD-65 (inactive staff in reports).

### 8.1 `GET /api/incentive/overtime-report/{org_id}?month&year` (P0-5)
Current: not mounted (grep `overtime-report` in `*.py`: none).
Contract (`src/api/reports.ts:88-93`, mock `e2e/mock-server/routes/reports.js:65-70`): `{success:true, data:[{user_id, ot_days:int, ot_minutes:int, ot_amount:number}]}`. The screen merges by `user_id` onto the staff list; missing users show zeros (`src/features/reports/utils/overtime.ts:23-44`), so returning rows only for staff with an OVERTIME assignment is acceptable (returning all active staff with zeros is also fine).
Outline:
1. `router/incentive.py`: add `@router.get("/api/incentive/overtime-report/{org_id}")` with `month:int, year:int` (validate 1-12 / 2000-2100 -> 422), call `get_overtime_report(org_id, month, year)`; wrap with `except HTTPException: raise`. Place it anywhere (no clash with `/overtime/{org_id}`).
2. `core_functions/incentive_functions.py`: new `get_overtime_report`. **Extract the overtime maths from `report_functions.py:649-678` into one shared helper** `compute_overtime(cur, user_id, year, month) -> (ot_days, ot_minutes, ot_amount)` and call it from both the report and `get_staff_salary`, so the report and the payslip can never disagree. Tables: `assigned_incentives` + `incentive_templates` (type `OVERTIME`) + `incentive_overtime_details` (user row else template row), `attendance` (`attendance_date`, `work_hours`), `users_additional_info.shift_id` -> `shift.start_time/end_time`.
3. Formula that matches today's payroll (recommended default so figures equal the salary sheet): `shift_minutes = (end - start)` with overnight fix `+1440` when `end < start`; `ot_minutes = max(0, SUM(work_hours)*60 - shift_minutes*present_days)`; `ot_amount = floor(ot_minutes / duration_minutes) * amount`; `ot_days = COUNT(days where work_hours*60 > shift_minutes)`; integers for minutes/days, amount as number with 2 dp. Alternative (per-day, stricter): per-day `ot = max(0, work_hours*60 - shift_minutes)`, slot flooring per day. Today's payroll uses the aggregate form (short days offset long days), so choosing per-day changes pay: **needs product sign-off** (record as new DD; until then use the aggregate form).
4. Staff with an OVERTIME template but no shift: today treated as all-hours OT (`report_functions.py:666,674`); decide (recommend 0 OT when no shift is assigned, applied to both report and salary).
5. Query sketch (one pass, set-based):
```sql
WITH staff AS (
  SELECT u.user_id FROM users u
  WHERE u.parent_id = %(org)s AND u.role_id IN (2,3) AND u.status = 1),
tmpl AS (  -- deterministic template per staff: most recent OVERTIME assignment
  SELECT DISTINCT ON (ai.user_id) ai.user_id, ai.incentive_id
  FROM assigned_incentives ai
  JOIN incentive_templates it ON it.incentive_id = ai.incentive_id AND it.type = 'OVERTIME'
  WHERE ai.org_id = %(org)s ORDER BY ai.user_id, ai.created_at DESC, ai.id DESC),
rate AS (
  SELECT t.user_id, d.duration_minutes, d.amount FROM tmpl t
  JOIN LATERAL (SELECT duration_minutes, amount FROM incentive_overtime_details d
                WHERE d.incentive_id = t.incentive_id AND (d.user_id = t.user_id OR d.user_id IS NULL)
                ORDER BY (d.user_id IS NOT NULL) DESC LIMIT 1) d ON true),
att AS (
  SELECT a.user_id, COUNT(*) AS present_days, COALESCE(SUM(a.work_hours),0)*60 AS worked_min
  FROM attendance a JOIN staff s USING (user_id)
  WHERE a.attendance_date >= make_date(%(y)s,%(m)s,1)
    AND a.attendance_date <  make_date(%(y)s,%(m)s,1) + INTERVAL '1 month'
  GROUP BY a.user_id)
SELECT ... -- join shift minutes, compute GREATEST(0, worked_min - shift_min*present_days), FLOOR(ot_min/duration)*amount
```
(Per-day `ot_days` needs a second CTE over attendance rows.)
6. Gate: none today on incentives; keep none (parity) or add `salary_structure` (decision DD-13).
Backward compat: purely additive. Acceptance: staff with OT template, shift 09:00-18:00, work 10 h on 3 days, template 60 min => 60 min x 3 = 180 min, `ot_days = 3`, `ot_amount = 3 * amount`; staff with no assignment => absent or zeros; same staff in `staff-breakdown` `overtime` equals `ot_amount`; overnight shift 22:00-06:00 handled (+1440); month boundary uses IST dates (note attendance_date is UTC date, P2-5).

### 8.2 Assignment semantics (DD-76)
Current: §4.4. Required (all additive unless flagged):
1. `GET /unassigned/{org}/{incentive}` rows gain `current_incentive_id` and `current_template_name` of any template of the **same type** the staff is already on (single value for COUNT/TURNOVER/BONUS if exactly one; array `current_templates` when several) so the app can warn/confirm (the bonus screen already warns client-side from `user_ids`).
2. `GET /unassigned-all/{org}`: make it do what it says, i.e. add `AND NOT EXISTS (SELECT 1 FROM assigned_incentives ai WHERE ai.org_id = u.parent_id AND ai.user_id = u.user_id)` (`router/incentive.py:306`). Check the app's use first (`payroll.ts:562-567`: label says "not assigned to any incentive"; UNVERIFIED which screen consumes it) because changing it narrows the list.
3. Decide exclusivity per type: if one template per type per staff is wanted (the mock enforces it, `e2e/mock-server/routes/payroll.js:240-247`), implement in `assign_incentive_staff` by deleting the user's assignments + detail copies for other templates of the same type inside the same transaction and return `moved:[{user_id, from_incentive_id}]` (additive). Without exclusivity at least make OVERTIME deterministic (order by `ai.created_at DESC`) as in §8.1.
4. Validate: template exists and belongs to `org_id` (404), users are active staff of the org (400 listing offenders), `user_ids` non-empty; message uses the number actually assigned.
5. Unique index (after de-dup):
```sql
DELETE FROM assigned_incentives a USING assigned_incentives b
 WHERE a.id > b.id AND a.org_id=b.org_id AND a.user_id=b.user_id AND a.incentive_id=b.incentive_id;
CREATE UNIQUE INDEX uq_assigned_incentives ON assigned_incentives (org_id, user_id, incentive_id);
-- one template row and one copy per user per template, for each details table:
CREATE UNIQUE INDEX uq_icd_template ON incentive_count_details (incentive_id) WHERE user_id IS NULL;
CREATE UNIQUE INDEX uq_icd_user ON incentive_count_details (incentive_id, user_id) WHERE user_id IS NOT NULL;
-- same pair for incentive_turnover_details, incentive_overtime_details, incentive_bonus_details
```
Acceptance: assign A to count#1 then count#2: either summed (documented) or moved (if exclusivity chosen) consistently in list, salary and overtime report; assign with unknown template -> 404.

### 8.3 Template edits vs per-staff copies, details endpoint
Current: edits propagate (§4.2) because the UPDATE has no `user_id` filter; details returns copies too. Required: keep propagation (matches DD-78 "editing a template changes pay immediately"), but make `get_incentive_details` filter `user_id IS NULL` and `ORDER BY id` (or add `?user_id=` to read a copy), and add `incentive_id` + `type` to the response so the app does not rely on `[0]`. Optionally drop per-user copies altogether (salary already falls back to the template row, `report_functions.py:620-647`): then `assign` only inserts `assigned_incentives`, removing 8 statements per user; data fix `DELETE FROM incentive_*_details WHERE user_id IS NOT NULL`. Product decision (copies currently allow future per-user overrides but nothing writes them).

### 8.4 Bonus update accepts month/year
Current: accepted, `''`/`0` mean keep (§4.2). Required: keep, but validate `month` is one of the 12 English names (case-insensitive, normalise to Title Case; the pay logic compares exact strings, `report_functions.py:646`) and `year` in 2000-2100; return 422 otherwise; also normalise on create (`incentive_functions.py:171-187`). Optionally prevent changing month/year of a bonus already paid (needs the paid-state table from `salary_and_payslips.md` §8.1). Acceptance: PUT `{bonus_percent:5}` keeps month/year; `{month:"Janu"}` -> 422; created `"march"` stored as `March`.

### 8.5 Validation bounds & numeric types (DD-73, DD-74, P2-7)

| Field | Rule |
|---|---|
| `template_name` | trimmed 1-150; unique per org+type (client-only today) |
| `count_upto`, `next_count` | int `>= 1` (`<= 1_000_000`, product to confirm); `next_count` meaning unclear (UNVERIFIED) |
| `each_incentive`, overtime `amount` | `Decimal`, `> 0`, `<= 9_999_999_999.99` (numeric(12,2)); 2 dp |
| `turnover_upto` | `Decimal > 0`, `<= 999_999_999_999.99` (numeric(14,2)) |
| `incentive_percent`, `bonus_percent` | `0 <= x <= 100` (DD-74 option a; DB allows 999.99), 2 dp |
| `duration_minutes` | int `1-1440` (zero would divide by zero guard `dur > 0` in `report_functions.py:677` yields 0 slots) |
| `year` | 2000-2100; `month` name enum |

Use `Decimal` (Pydantic `condecimal`) for money and percent, `int` with `Field(ge=, le=)` for counts; return 422 with field names. Error mapping: add `except HTTPException: raise` and map `ValueError` to 400 (`incentive_functions.py:172,430`).

### 8.6 Delete behaviour (P1-9, DD-77)
Required: `DELETE /{T}/{id}` should verify type (`SELECT type FROM incentive_templates WHERE incentive_id=%s`, 404/409 on mismatch), scope by org (optional `org_id` query for scoping), delete all four details tables (not only the URL's type), `assigned_incentives`, then the template, in one transaction with explicit rollback; and return 404 only when nothing existed. Add FK `assigned_incentives.incentive_id -> incentive_templates.incentive_id ON DELETE CASCADE` (needs the unique index that already exists at DDL `:569`) and `ON DELETE CASCADE` on the four details FKs after data repair:
```sql
DELETE FROM assigned_incentives ai WHERE NOT EXISTS (SELECT 1 FROM incentive_templates t WHERE t.incentive_id = ai.incentive_id);
ALTER TABLE assigned_incentives ADD CONSTRAINT fk_ai_template FOREIGN KEY (incentive_id) REFERENCES incentive_templates(incentive_id) ON DELETE CASCADE;
```
Also add `DELETE FROM assigned_incentives WHERE org_id = %s` to the org delete (`company_functions.py:444-448`). The "newest-only" delete rule stays client-side (DD-77) unless product wants it enforced.

### 8.7 Plan gate / permission
No gate today; managers' "Incentive Management" permission exists but is unused (`permission_checker.py:109`). Required only when P3-1 (server auth) lands: apply `has_incentive_management` for role 2. Optionally gate with `check_plan_feature(org,'salary_structure')` for parity with payslips (decision DD-13).

### 8.8 Resource scoping (G4-A Changes)
`GET .../details/{incentive_id}`, `PUT` and `DELETE /api/incentive/{type}/{incentive_id}` carry no org id: with a token the template's `incentive_templates.org_id` must be one of the caller's organisations (`caller_orgs`), else 404 and nothing is written; without a token nothing changes. `POST /api/incentive/assign` needs a template of `org_id` and only users of `org_id` (404, nothing written); `DELETE /api/incentive/unassign/...` no longer touches the per-user detail rows when no assignment of that organisation was deleted.

## 9. Test notes
No automated tests in the repo. Fixtures: org with active trial, staff (roles 2/3, one inactive, one admin), shifts (day and overnight), attendance rows with `work_hours`, one template per type, two templates of the same type, a payslip with `basic_pay`.
Cases: (1) create/list/details for each type (list rows include `assigned_count`; bonus includes `user_ids`); (2) edit propagates to a per-user copy (row with `user_id`) and not to other templates; (3) assign -> copies inserted; unassign -> copies removed; unassign twice -> 404; (4) `unassigned-all` currently returns everyone; `unassigned` includes staff on sibling templates; (5) bonus update with/without month/year; bonus applies only in the matching month/year in `staff-breakdown`; (6) overtime: slots floor, no-shift, overnight shift, two overtime templates (nondeterminism); (7) wrong-type PUT/DELETE; (8) 500-wrapped errors (bonus create with `month=""`); (9) overflow inputs (`count_upto=3e9`) -> 500; (10) overtime-report vs `staff-breakdown.overtime` equality once §8.1 ships.
