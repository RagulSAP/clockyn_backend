# Salary & payslips — backend context

Domain: payslip (salary-structure) templates, per-staff payslip assignment, payslip preview, and the org-wide monthly salary breakdown. Sister docs: `incentives.md` (incentive templates, overtime), `wages_daily_hourly.md` (daily/hourly wage templates). The salary computation itself lives in `core_functions/report_functions.py::get_staff_salary` and is documented here (§4.2) because the breakdown endpoint and the employee salary view depend on it.

Verified against source on 2026-10-19. All `file:LINE` references are real. Backend root = `C:\POC\pa_backend\Clockyn`. "DDL dump" = the schema file `*_ddl_latest.sql` in the repo root (legacy-named; it lists tables of one schema; `migrations/` contains only a README, so the dump is the only schema source).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/salary.py` | 158 | `APIRouter(prefix="/api/salary")` (`:23`); 12 routes; also holds `staff-breakdown` which runs raw SQL in the router (`:36-45`) |
| `core_functions/salary_structure.py` | 581 | Pydantic models (`:12-51`), template CRUD (`:53-346`), branch/department/staff pickers (`:348-428`), assign (`:430-471`), employee lookup (`:473-492`), preview (`:494-581`) |
| `core_functions/report_functions.py` (`get_staff_salary`) | `:532-780` | The only salary calculator (11 queries per staff member). Also served raw at `GET /api/reports/staff-salary/{user_id}` (`router/report.py:13,49-51`, ungated, owned by the reports domain) |
| `core_functions/gen_plans_functions.py` (`check_plan_feature`) | `:174-196` | Plan gate used by 3 of the 12 routes |
| `ml/s3.py::get_employee_profile_url` | `:170-183` | Pre-signed photo URL (7 days) used by staff-breakdown |
| `app.py` | `:43` | `app.include_router(salary.router)` |

Frontend callers: `src/api/payroll.ts` (templates, staff picker, assign, employee, preview: `:259-342`), `src/api/salaryReports.ts` (`staff-breakdown` `:100-105`; `monthly-list` / `mark-paid` `:107-124`, not mounted).
Frontend screens: payslip templates and assignment (B6, `/admin/payroll/*`), Salary sheet and Monthly salary (B7), employee payslip sheet (B11).

Not exposed anywhere in this repo (confirmed by `grep -rn "mark-paid\|monthly-list\|mark_paid\|salary_payments"` over `*.py`/`*.sql`: no hit): `GET /salary/monthly-list`, `POST /salary/mark-paid` (see §8.1).

## 2. Data model touched

All ids are `varchar(36)` UUID strings generated in Python (`uuid.uuid4()`), plus an internal `id int4 identity` PK. DDL dump line numbers below.

| Table (DDL) | Columns used | Keys / indexes / constraints | Notes |
|---|---|---|---|
| `pay_slip` (`:780`) | `pay_slip_id`, `pay_slip_name varchar(255)`, `org_id`, `basic_pay`, `hra`, `epf`, `professional_tax`, `health_insurance` (all `numeric(10,2)` NULL-able), `created_at`, `updated_at` | PK `id`; plain indexes on `org_id` and `pay_slip_id`. **`pay_slip_id` is not unique** and nothing references it by FK | Template. No `status` column: hard delete only. `created_at` written with `CURRENT_TIMESTAMP` (`salary_structure.py:66`), `updated_at` on update only (`:272`) |
| `payslip_add_earnings` (`:830`) | `pay_slip_id`, `add_earnings_name varchar(255)`, `add_earnings_amount numeric(10,2)` | PK `id` only: **no FK, no index on `pay_slip_id`** | Custom earning lines; `created_at/updated_at` never written |
| `payslip_add_deduction` (`:817`) | `pay_slip_id`, `add_deduction_name`, `add_deduction_amount numeric(10,2)` | same as above | Custom deduction lines |
| `payroll` (`:800`) | `org_id`, `user_id`, `pay_slip_id`, `created_at`, `updated_at` | PK `id`; **UNIQUE index `payroll_user_id_key` on `user_id` alone (`:812`)**, indexes on org, pay_slip_id, user_id; no FK | Assignment of one payslip to one staff member (globally unique per user). `created_at`/`updated_at` never written by the insert (`salary_structure.py:453-456`) so they are NULL |
| `users` (`:991`) | `user_id`, `name`, `emp_id`, `branch_id int`, `dept_id varchar`, `parent_id` (= org id for staff), `role_id`, `status` | | Staff pickers use `parent_id = org_id AND role_id IN (2,3) AND status = 1` (role 1 = admin is excluded) |
| `users_additional_info` (`:1032`) | `acc_no`, `ifsc_code` (NOT NULL), `shift_id` | | Bank details in preview (`salary_structure.py:509,513`); shift for overtime (`report_functions.py:667-672`) |
| `branch_info` (`:160`), `dept_info` (`:267`), `org_info` (`:755`), `role_info` (`:921`) | names | | Lookups. `branch_info.status = 1` / `dept_info.status = 1` filters at `salary_structure.py:356,377` |
| Read by `get_staff_salary` (documented elsewhere): `attendance` (`:133`), `assigned_wages`/`daily_hourly_wages` (`:120`,`:248`), `assigned_incentives`/`incentive_*` (`:108`,`:499-590`), `odometer_readings` (`:717`), `travel_bill_expenses` (`:970`), `advance_details` (`:67`), `shift` (`:934`) | | | See §4.2 |

Soft delete: none for payslips; `delete_salary_template` hard-deletes the `pay_slip` row only (`salary_structure.py:331`). Staff delete removes the user's `payroll` row (`core_functions/add_staff_function.py:754`); organisation delete removes `pay_slip` + lines (`core_functions/company_functions.py:455-457`) but its `payroll` deletion is per staff user only (`:428`).

Money types: DB `numeric(10,2)` (max 99,999,999.99); Python boundary = `float` (Pydantic `float` fields, `salary_structure.py:12-32`; `float()` casts at `:168-169,176-180`). No `Decimal` anywhere in this domain.

## 3. Endpoints

> **Current contract:** the rows below record the pre-WP4b behaviour (line numbers refer to it); the **Changes (WP4b ...)** block after the tables is authoritative for what the endpoints do now.

Identity/auth: **none** on any route (no token, no caller id; `org_id` and ids are trusted from path/query/body; P3-1 in `backend_changes.md`). Plan gate = `check_plan_feature(org_id, 'salary_structure', is_admin=True)`; semantics in §4.8.

Common error behaviour: business failures return HTTP 200 `{success:false, message}` (assign, employee) or raise `HTTPException(500, "Failed to ...: <db error text>")` (db error text leaks to client). `HTTPException` raised inside `try` are re-raised only where `except HTTPException: raise` exists (`get_salary_template` `:255`, `delete_salary_template` `:339`); elsewhere the generic `except Exception` turns them into 500.

| # | METHOD path | Handler (file:line) | Plan gate | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|---|
| 1 | GET `/api/salary/staff-breakdown` | `staff_salary_breakdown` `router/salary.py:30-102` | `salary_structure` (`:32`) | query `org_id: str`, `month: int`, `year: int` (all required, unvalidated) | **bare JSON array** (or `[]` if no staff, `:51-52`) of `{user_id,name,emp_id,role_name,branch_name,salary_type:'MONTHLY_FIXED',basic_salary,incentive,incentive_count,incentive_turnover,overtime,bonus,fine:0,travel_allowance,odometer,travel_ticket,advance,other_bills,daily_wages,hourly_calc,net_salary,profile_photo_url}` | 403 plan gate; 422 missing/non-int params; any per-staff failure silently becomes an all-zero row (`:87-101`); no 500 path for calc errors |
| 2 | POST `/api/salary/template` | `create_template` `router/salary.py:105-108` -> `create_salary_template` `salary_structure.py:53-118` | gated on body `org_id` (`:107`) | JSON `SalaryTemplateCreate`: `payslip_name:str, org_id:str, basic_pay:float, hra:float, epf:float, professional_tax:float, health_insurance:float` (all required), `count_basis?, upto?, next?` (accepted, ignored), `custom_earnings?:[{name,amount}]`, `custom_deductions?:[{name,amount}]` | `{success:true, message, pay_slip_id, updated_templates:[...]}` | 403; 422; 500 `Failed to create salary template: ...` (e.g. numeric overflow >99,999,999.99) |
| 3 | GET `/api/salary/templates/{org_id}` | `get_templates` `router/salary.py:110-113` -> `get_salary_templates` `salary_structure.py:120-199` | gated (`:112`) | path `org_id` | `{success:true, data:[template...]}` each with `pay_slip_id,payslip_name,org_id,basic_pay,hra,epf,professional_tax,health_insurance,count_basis:null,upto:null,next:null,total_earnings,total_deductions,net_salary,custom_earnings:[{name,amount}],custom_deductions,assigned_count,created_at`. **Empty org returns bare `[]`** (`salary_structure.py:132-133`) | 403; 500 |
| 4 | GET `/api/salary/template/{pay_slip_id}` | `get_template` `router/salary.py:115-118` -> `salary_structure.py:201-261` | none | path | `{success:true, data:{...same fields, no assigned_count}}` | 404 `Salary template not found` (`:211`); not org-scoped |
| 5 | PUT `/api/salary/template/{pay_slip_id}` | `update_template` `router/salary.py:120-123` -> `salary_structure.py:263-323` | none | same body as #2 (`org_id` required in body but not applied to the row) | `{success:true, message, updated_templates:[...for body org_id]}` (no `pay_slip_id` key) | **No 404** when the id does not exist (UPDATE of 0 rows, `:270-283`, still returns success); 500 when body `org_id` has no templates (see §7 D5) |
| 6 | DELETE `/api/salary/template/{pay_slip_id}` | `delete_template` `router/salary.py:125-128` -> `salary_structure.py:325-346` | none | path | `{success:true, message}` | 404 (`:335`, evaluated after `commit()`); leaves `payroll` + lines (see §4.5) |
| 7 | GET `/api/salary/branches/{org_id}` | `router/salary.py:130-133` -> `:348-367` | none | path | `{success:true, data:[{branch_id,branch_name}]}` active branches, by name | 500 |
| 8 | GET `/api/salary/departments/{org_id}` | `router/salary.py:135-138` -> `:369-388` | none | path | `{success:true, data:[{department_id,department_name}]}` (aliased from `dept_id`,`dept_name`) | 500 |
| 9 | GET `/api/salary/staff/{org_id}` | `router/salary.py:140-143` -> `:390-428` | none | path `org_id`; query `branch_id?` (cast `int()`), `department_id?`, `pay_slip_id?` | `{success:true, data:[{staff_id,staff_name,emp_id,branch_name('N/A' default),department_name('N/A'),is_assigned:0\|1}]}` | non-numeric `branch_id` -> `int()` ValueError -> 500 (`:411`); `is_assigned` is relative only to the passed `pay_slip_id` (`:404`) |
| 10 | POST `/api/salary/assign` | `assign_payslip` `router/salary.py:145-148` -> `salary_structure.py:430-471` | none | JSON `PayslipAssignment {org_id, staff_id, pay_slip_id}` (`router/salary.py:25-28`) | `{success:true, message:"Payslip assigned successfully"}` | **HTTP 200 `{success:false, message:"Already assigned to '<name>'", payslip_name}`** if the user already has a payslip (even the same one, `:437-450`); 500 `Database error: ...` (e.g. unique violation after template delete, §7 D2) |
| 11 | GET `/api/salary/employee/{org_id}/{staff_id}` | `router/salary.py:150-153` -> `salary_structure.py:473-492` | none | path | `{success:true, pay_slip_id}` | HTTP 200 `{success:false, message:"No payslip assigned"}` (`:486`); does not verify the template still exists |
| 12 | GET `/api/salary/preview/{org_id}/{staff_id}/{pay_slip_id}` | `router/salary.py:155-158` -> `salary_structure.py:494-581` | none | path | `{success:true, data:{org_name,payslip_name,month:'<EnglishMonthName>',year,staff_name,emp_id,department,branch,account_number|'N/A',ifsc_code|'N/A',earnings:[{name,amount}],deductions:[...],total_earnings,total_deductions,net_pay}}` | unknown staff/org/payslip -> `TypeError` -> 500 `Failed to get preview: 'NoneType' ...` (not 404); month/year are **UTC** now (`:545,552-553`) |

Added (step 1, `router/salary_payments.py` + `core_functions/salary_payment_functions.py`, table `salary_payments`):

| # | METHOD path | Request | Success response | Errors |
|---|---|---|---|---|
| 13 | GET `/salary/monthly-list` | query `org_id, month(1..12), year(2000..2100)` | `{success, data:[{user_id,name,emp_id,dept_name,branch_name,salary_type(MONTHLY_FIXED\|DAILY_WAGES\|HOURLY),basic_salary,net_salary,paid,paid_date}]}`; unpaid rows use the live `get_staff_salary` net, paid rows the net snapshot stored at payment; `paid_date` = IST date | 422 bad month/year |
| 14 | POST `/salary/mark-paid` | `{org_id, user_ids[1..500], month, year}` | `{success, message, marked, already_paid}`; idempotent (`ON CONFLICT DO NOTHING`) | 400 empty/over 500 ids; 404 `Staff not found: <id>` for an id that is not an active role 2/3 user of the org (nothing written); 422 bad month/year |

Related but owned elsewhere: `GET /api/reports/staff-salary/{user_id}?year&month` (`router/report.py:49-51`) returns the full `get_staff_salary` object (`{success, data:{user_id,month,year,present_days,total_hours,earnings:{...},deductions:{...},total_earnings,total_deductions,net_salary}}`), no gate, no org check.

### Changes (WP4b payroll engine, migration 032; decisions D-7, D-8, D-9, D-15, D-21)

Code: `core_functions/payroll_engine.py` (pure engine: `compute_salary(inp)`, `attendance_summary`, `late_penalty`,
`fine_amount`, `odometer_amount`, `apply_paid_state`; no cursor), `core_functions/salary_engine.py` (database layer: one
set-based statement per component for the whole organisation, 17 statements whatever the number of staff;
`compute_org_salaries`, `compute_user_salary`, `month_attendance_figures`), `core_functions/overtime.py`
(`overtime_for_month`, the ONE overtime helper used by the report and by the engine), `core_functions/payroll_validation.py`
(D-15 validators), `salary_payment_functions.py`, `salary_structure.py`, `report_functions.py` (`get_staff_salary` is a
one-employee wrapper; the PDF uses the engine counters), `incentive_functions.py`, `advance_functions.py`,
`emp_expenses_functions.py` (allowance-rate endpoints only), `gen_dashboard_functions.py` (IST date), routers
`salary.py`, `salary_payments.py`, `incentive.py`, `report.py`, `attendance_rule.py`, `emp_expenses.py` (rate type).
Tests: `tests/test_payroll_snapshot.py` (golden hand-computed numbers), `test_payroll_engine.py`, `test_payroll_validation.py`,
`test_payroll_endpoints.py` (fake cursor, contract shapes, statement count); DB-only `tests/test_attendance_rule_overtime_salary.py`
(extended, not run). Schema: `migrations/032_payroll_engine.sql` + `clockyn_ddl_latest.sql`: `salary_payments.snapshot jsonb`,
table `org_allowance_rate`, `odometer_readings.allowance_rate numeric(8,2)`.

**Engine rules.** Attendance comes from `attendance_rules` (IST business date, one row per session): present days = distinct
punched dates (a half day and a late day are present days); worked hours = hours of CLOSED sessions (both sessions of a day
add up, an open session adds none); day types / working days from `build_month` (leave, holiday scoped by branch / department,
weekly off from `org_off_days`, default Sunday); lateness = first punch-in of the day against the shift plus the rule's
`grace_minutes` (night shifts wrap midnight; staff without a shift are never late). Pay lines keep the legacy formulas: daily wage
= rate x present days, hourly = rate x hours, COUNT incentive = flat `each_incentive` per assigned template, TURNOVER = basic x percent,
BONUS = basic x percent in its month / year, odometer = distance x (reading stamp, else organisation rate, else default),
overtime = monthly aggregate (D-9). **No proration of basic for absences, half days or leave (D-8, D-7)**: the formulas of
`domains/incentives.md` are kept (D-21). `fine` = `penalty_days x basic / working_days` for staff on a payslip, with
`penalty_days = floor(late_days / late_punch_in_count) x (0.5 Half Day | 1.0 Leave)`; zero for an organisation without a saved
rule; it is deducted in `total_deductions` / `net_salary` and reported in `deductions.fine`.

**Endpoint changes (additive unless stated).**

| Endpoint | Change |
|---|---|
| GET `/api/salary/staff-breakdown` | one batch for the organisation instead of one connection per employee; `month` 1..12 / `year` 2000..2100 else 422; `salary_type` is the real enum (`MONTHLY_FIXED` payslip, else `DAILY_WAGES`, else `HOURLY`, else `MONTHLY_FIXED`); `fine` from the attendance rule; a failing employee is `{user_id,name,emp_id,role_name,branch_name,error:true,profile_photo_url}` (never zeros); additive `salary_types, present_days, working_days, half_days, leave_days, absent_days, late_days, paid, paid_on, paid_date`. Paid months return the stored figures |
| GET `/salary/monthly-list` | same batch; unchanged keys (`paid`, `paid_date`); paid rows return the stored net AND basic; a failing employee is an `error:true` row |
| POST `/salary/mark-paid` | one batch computation; stores `net_amount` and the full computed `snapshot`; an already paid row is never overwritten or recomputed (`ON CONFLICT DO NOTHING`, counted in `already_paid`); one failing employee aborts with 500 and nothing is written |
| GET `/api/reports/staff-salary/{user_id}` | wrapper of the engine, same shape; additive `deductions.fine` and the fields above; 422 bad month/year; 404 unknown user (was a row of zeros) |
| GET `/api/salary/preview/{org}/{staff}/{payslip}` | optional `month` (1..12) / `year` (default current IST month; 422 outside the range); `month` is the English name of the requested month plus `month_number`; staff and payslip must belong to the organisation (404, was 500); additive `fine`, `net_pay_after_fine`, `late_days`, `paid`, `paid_on`, `paid_date`, `paid_net`. `total_earnings` / `net_pay` stay template-based |
| POST / PUT `/api/salary/template` | D-15: name required, amounts `0..9,999,999.99` with 2 decimals (0 allowed, blank lines dropped), unique name per organisation (409 `A template with this name already exists`); PUT is organisation scoped (404 `Salary template not found`, no longer a silent success); an empty template list is `{success:true,data:[]}` |
| GET `/api/reports/monthly-attendance/...` (PDF) | punch times in IST (they were UTC), Present / Absent / Half Days / Leaves / Holidays from the engine counters, the payslip block gains a `Late Fine` deduction row when the rule produces a fine |
| GET `/api/incentive/overtime-report/{org}` | same shape; computed by the shared helper: it equals `earnings.overtime` of the breakdown row by row |
| POST `/expenses/admin/allowance-rate`, GET `/expenses/admin/allowance-rate/{org}`, PUT readings status | rate lives in `org_allowance_rate` (decimal, `0 < rate <= 9999.99`); saving stamps PENDING readings only; approving stamps the organisation rate (default `DEFAULT_ODOMETER_RATE`, 3) once, on the transition to approved; GET returns `{success, rate, is_default}` |
| Incentives / advances / wages | D-15 validation and uniqueness per organisation and type, see `incentives.md` / `reports_and_advance.md` / `wages_daily_hourly.md`; payroll routes no longer put the database error text in `detail` |
| Platform admin dashboard | the seven `attendance_date = CURRENT_DATE` filters take the IST date (`utils.timezone.ist_today()`) |

**Intentional behaviour changes (release notes).** Everything else is byte-identical to the legacy engine (golden tests).
1. **Two sessions on one day count once**: daily wage = rate x distinct present dates (legacy: x attendance rows), and the shift is subtracted once per date in overtime.
2. **Night-shift overtime** uses the wrapped shift length (21:00-06:00 = 540 min; legacy `end - start` was negative and inflated overtime enormously); a session that crosses midnight belongs to its start date, never split or doubled.
3. **`fine`** is no longer a constant 0 for organisations that saved an attendance rule: `net_salary` drops by the fine (payslip preview shows `fine` / `net_pay_after_fine`; the PDF shows a `Late Fine` row). Organisations without a rule see no change.
4. **`salary_type`** in the breakdown is the real enum (was always `MONTHLY_FIXED`).
5. **Overtime**: a day with an auto-closed (forgotten punch-out) session is skipped until reviewed; the staff member's shift must belong to the organisation and be active (as for lateness). The overtime report and the salary now use one helper, so they agree.
6. **Odometer rate**: a reading with neither its own stamp nor an organisation rate pays the default (3, env `DEFAULT_ODOMETER_RATE`; the legacy fallback was 1 per km); saving the rate no longer rewrites approved / rejected readings; re-approval does not restamp; decimals accepted.
7. **Paid months are frozen**: the list, breakdown, staff-salary and preview return the figures stored at payment with `paid: true` (rows paid before migration 032 keep the stored net and show live components).
8. **Payslip preview** honours `month` / `year` (it always showed the current month); unknown staff / payslip is 404.
9. **Validation (D-15)**: negative, zero-where-positive-required, over-limit or 3-decimal amounts, percentages outside 0..100, bad months and duplicate template names / amounts / thresholds per organisation are now refused by the server (400 / 409) with the app's messages.
10. **Errors**: a failing employee is an `error: true` row (not zeros); payroll 500 responses no longer expose database error text; month / year outside the range are 422; `staff-salary` for an unknown user is 404.
11. **PDF**: IST punch times; counters and fine from the engine (Absent no longer counts weekly offs, holidays or approved leave).

## 4. Business logic

### 4.1 Template CRUD
Create (`salary_structure.py:53-118`): generate UUID (`:59`) -> INSERT `pay_slip` (`:64-77`) -> for each custom earning/deduction **skip when `name` empty or `amount` falsy** (`:82`, `:91`: zero amounts silently dropped, negative accepted) -> INSERT lines -> single commit (`:97`) -> re-query `get_salary_templates(body.org_id)` for `updated_templates` (`:101`). On any exception: rollback + 500 (`:110-115`). No duplicate-name check, no org existence check, no length/range validation beyond Pydantic types.
Update (`:263-323`): UPDATE row (name + 5 amounts + `updated_at`) -> DELETE all lines -> re-INSERT lines (same skip rule) -> commit -> `get_salary_templates(template_data.org_id)`. `count_basis/upto/next` accepted in the model (`:28-30`) but no column exists: always returned `null` (`:181-183,243-245`).
Totals (identical in list/get/preview, `:168-170,228-230,540-542`): `total_earnings = basic_pay + hra + Σ custom_earnings`; `total_deductions = epf + professional_tax + health_insurance + Σ custom_deductions`; `net_salary = total_earnings - total_deductions` (float arithmetic, not rounded; the DB `NULL` columns would raise `TypeError` in `float(None)` -> 500).
Delete (`:325-346`): one `DELETE FROM pay_slip WHERE pay_slip_id = %s` (`:331`) then `commit()` then `rowcount` check (`:334`). **Nothing else is deleted** (see §4.5).

### 4.2 Salary calculation used by staff-breakdown (`report_functions.py:532-780`)
Inputs `user_id, year, month`. All queries use the user's *current* assignments (no historical versioning: changing a template changes past months when re-queried).
1. Payslip: `payroll JOIN pay_slip` by `user_id` (`:537-544`); no row => basic, hra, epf, prof_tax, health = 0 (`:546-551`). Custom lines read by `pay_slip_id` (`:553-564`).
2. Attendance: `present_days = COUNT(*)` of `attendance` rows for the month and `total_hours = SUM(work_hours)` (`:567-576`). A row counts as a present day even with no punch-out, leave/holiday/absence do not reduce `basic`.
3. Daily wages (type `D`): `rate * present_days`; rate from `assigned_wages JOIN daily_hourly_wages` on the assigned `temp_id`, preferring a per-user row (`ORDER BY (dw.user_id IS NOT NULL) DESC LIMIT 1`, `:579-589`). Hourly (`H`): `rate * total_hours` (`:592-602`). See `wages_daily_hourly.md` §4.4 (per-user copies are never matched by this join).
4. Incentives (`:604-647`): for every assigned COUNT template add `each_incentive` once (flat, ignores `count_upto`/`next_count`); TURNOVER adds `basic * after_target_incentive_percent / 100` (ignores `turnover_upto`); BONUS adds `basic * bonus_percent / 100` only when `year == requested year` and `month == English month name` (case-sensitive, `month_names[month]`, `:609-610,646`). Per-user detail row preferred over template row.
5. Overtime (`:649-678`): first assigned OVERTIME template (`fetchone` without ORDER BY, `:651-656`); `shift_minutes = EXTRACT(EPOCH FROM end_time - start_time)/60` (negative for overnight shifts), `0` when no shift; `ot_minutes = max(0, total_hours*60 - shift_minutes*present_days)`; `ot_amt = floor(ot_minutes / duration_minutes) * amount`.
6. Odometer (`:680-709`): sum of approved readings in the month x (row `allowance_rate` else org's most recently updated rate else 1).
7. Travel (`:711-717`) = approved `travel_bill_expenses` with `expense_type='TRAVEL'`; Others (`:720-725`) = approved `BILL` rows; Advance (`:728-733`) = all `advance_details` dated in the month.
8. Totals (`:735-740`):
   `total_earnings = basic + hra + daily + hourly + (count + turnover + bonus) + overtime + odometer + travel + others + Σ custom_earnings`
   `total_deductions = epf + professional_tax + health_insurance + advance + Σ custom_deductions`
   `net_salary = total_earnings - total_deductions` (rounded to 2 dp in the response, `:771-773`).
Month filters use `EXTRACT(... FROM attendance_date)` on a date that is the **UTC** punch date (P2-5), so 00:00-05:30 IST punches land in the previous day/month. Handler is `async` but uses blocking `psycopg2` calls (event-loop blocking, §7 D9).

### 4.3 staff-breakdown mapping (`router/salary.py:30-102`)
1. Gate (`:32`). 2. Staff query: active (`status=1`) users with `role_id IN (2,3)` under `parent_id = org_id`, left-joining role name and branch (`:36-45`), `ORDER BY u.name`. 3. For each staff, sequentially `await get_staff_salary(...)` (11 queries + a new pooled connection per staff) and map:

| Response field | Source | Notes |
|---|---|---|
| `salary_type` | constant `'MONTHLY_FIXED'` (`:69`) | never `DAILY_WAGES/HOURLY/WEEKLY` even if wages are assigned |
| `basic_salary` | `earnings.basic` | full template amount, no proration |
| `incentive` | `incentive_count + incentive_turnover` (`:71`) | **excludes bonus** (bonus is its own field) |
| `incentive_count` | rupee amount (sum of `each_incentive`), not a count | frontend column title says "Count (#)" |
| `incentive_turnover`, `overtime`, `bonus`, `odometer`, `daily_wages`, `hourly_calc` | same-named earnings | |
| `fine` | literal `0` (`:76`, `:95`) | no fine data exists anywhere |
| `travel_allowance` | `earnings.travel` (`:77`) | |
| `travel_ticket` | `earnings.travel` (`:79`) | **identical value to `travel_allowance`** (double-labelled) |
| `other_bills` | `earnings.others` (`:78`) | approved BILL expenses; are **added** to earnings |
| `advance` | `deductions.advance` | |
| `net_salary` | `get_staff_salary.net_salary` | includes HRA, custom earnings, EPF, professional tax, health insurance, custom deductions, which have **no column** in this response |
| `profile_photo_url` | `get_employee_profile_url(user_id)` (`:85`) | up to 2 S3 `head_object` calls per staff (blocking), may be `null`, 7-day pre-signed URL |

Visible columns do **not** sum to `net_salary`: missing from the response are `hra`, custom earnings (+), `epf`, `professional_tax`, `health_insurance`, custom deductions (-), and `travel_ticket` double-counts travel visually (but is added only once inside `net_salary`). A client re-summing the columns will be wrong by `hra + Σcustom_earn - epf - pt - health - Σcustom_ded + travel` etc.
If anything in `get_staff_salary` throws (it wraps all errors into `HTTPException(500)`, `report_functions.py:776-777`), the router catches `Exception` (`:87`) and emits a zero row with `net_salary: 0` — indistinguishable from a genuine zero.

### 4.4 Staff picker, assign, employee lookup
- Picker (`:390-428`): `SELECT DISTINCT` active staff roles 2,3 for the org, LEFT JOIN branch/department (`'N/A'` fallbacks) and LEFT JOIN `payroll p ON p.user_id = u.user_id AND p.pay_slip_id = <param> AND p.org_id = <org>` to compute `is_assigned`. Staff assigned to a *different* template show `is_assigned = 0`.
- Assign (`:430-471`): look up any existing `payroll` row for `(org_id, user_id)` joined to `pay_slip` for the name (`:437-442`); if found return 200 `success:false` (no move/replace); else `INSERT INTO payroll (org_id, user_id, pay_slip_id)` (`:453-456`) and commit. No validation that the staff belongs to the org, is active, or that the template exists/belongs to the org (no FK). There is **no unassign and no re-assign endpoint** anywhere (DD-78).
- Employee lookup (`:473-492`): first `payroll` row for `(org_id, user_id)`; returns only `pay_slip_id`.

### 4.5 Template delete: cascade behaviour (actual)
Deleting a template removes only the `pay_slip` row. Left behind: `payslip_add_earnings` rows, `payslip_add_deduction` rows, and **all `payroll` rows pointing at it** (no FK, no cascade). Consequences:
1. Assigned staff keep a dangling `payroll` row; `get_staff_salary` JOIN finds no payslip => basic/hra/etc = 0 silently (`report_functions.py:537-551`).
2. `assigned_count` for that id disappears with the template, but the staff are **stuck**: the assign pre-check joins `pay_slip` (`:437-442`) so it finds nothing, the INSERT then hits `payroll_user_id_key` (unique on `user_id`) and fails with 500 `Database error: duplicate key value violates unique constraint ...` (`:462-468`). Only a staff delete (`add_staff_function.py:754`) clears the row.
3. Orphan lines accumulate (no index on `pay_slip_id`).

### 4.6 Preview (`:494-581`)
Six queries: org name, staff (+ branch/dept/bank LEFT JOINs **without org scoping**, `:505-516`), payslip row, custom earnings, custom deductions. Builds `earnings = [Basic Pay, HRA, ...custom]` and `deductions = [EPF, Professional Tax, Health Insurance, ...custom]` from the **template only** (not the staff's real month figures: no attendance, wages, incentives, advances). Month label and year come from `get_utc_now()` (`:545,552-553`): wrong during the last/first 5.5 h of an IST month. No `month/year` parameters (P1-16).

### 4.7 Time zone / ID generation / idempotency
`pay_slip_id` = `uuid4` string. `pay_slip.created_at` = DB `CURRENT_TIMESTAMP` (naive `timestamp`). Template create is not idempotent (double submit creates two templates; no unique name). Assign is idempotent-ish (second call -> `success:false`).

### 4.8 Plan gate behaviour (`gen_plans_functions.py:121-196`)
`check_plan_feature(org, feature, is_admin)`:
1. `get_org_plan_status`: active free trial (`free_trial.is_active` and `end_date >= today`) => all features allowed; else latest `ACTIVE` subscription: `end_date >= today` => `{plan_id}` active, else `is_expired=True`; no subscription => `is_expired=True` (`:121-157`). `date.today()` = server date (not IST).
2. Free trial => return. Expired => **403** with the admin message `Your plan has expired. Please renew your plan to continue using this feature.` (`:188-192`).
3. Else blocked set per plan `PLAN_BLOCKED_FEATURES` (`:167-171`): Core `{staff_location, crm}`, Prime `{crm}`, Elite `{}`. **`salary_structure` is in no set**, so for an unexpired plan it is never blocked; the docstring (`:179`) claiming Core blocks it is stale. Net effect: **only expiry blocks `salary_structure`**.
Where applied in this domain: staff-breakdown (`router/salary.py:32`), template create (`:107`), template list (`:112`). **Not applied** to get/update/delete template, branches, departments, staff, assign, employee, preview. An expired org can therefore still edit/delete/assign/preview payslips. The `HTTPException(403)` raised by the gate is outside any `try` in these routers, so it surfaces as a real 403 (unlike chat, P2-2).

## 5. Side effects & integrations
- S3: `get_employee_profile_url` per staff in staff-breakdown (`router/salary.py:85,100`): 1-2 `head_object` + one `generate_presigned_url` (7 days) each; failures swallowed (`ml/s3.py:181`).
- No push, e-mail, WhatsApp, WebSocket or payment calls in this domain.
- Console `print()` debug lines in create (`salary_structure.py:61,98,112-114`); no structured logging.

## 6. Config & env vars
`DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_SCHEMA` (`core_functions/db_function.py:25-37`; pool 5-20 `:46`), `AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_REGION, S3_BUCKET_NAME` (`ml/s3.py:10-14`). Plan ids hard-coded (`gen_plans_functions.py:159-161`). A secret exists at `ml/s3.py:10-11` (read from env; value not recorded).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | no auth anywhere (`router/salary.py` all) | Any caller can read/modify/delete any org's templates and assignments by id (IDOR); `pay_slip_id` routes are not even org-scoped (`salary_structure.py:207,331`) |
| D2 | High | `salary_structure.py:325-346` + DDL `:812` | Template delete orphans `payroll` rows; assigned staff then cannot be assigned again (unique `user_id`) -> 500; salary silently drops basic/hra/deductions to 0 |
| D3 | High | `router/salary.py:87-101` | Per-staff exceptions become zero rows with `net_salary: 0`; payroll totals can be silently wrong |
| D4 | Med | `router/salary.py:79` / `:77` | `travel_ticket == travel_allowance` (duplicate). `fine` hard-coded 0 (`:76`). Columns do not reconcile with `net_salary` (§4.3) |
| D5 | Med | `salary_structure.py:263-323` | Update never 404s; uses **body** `org_id` for the returned list (`:310`): when that org has no templates `get_salary_templates` returns a bare list (`:133`) and `.get("data")` (`:315`) raises `AttributeError` AFTER `commit()` => client sees 500 though the update was saved |
| D6 | Med | `salary_structure.py:437-450` | Re-assigning (change template) is impossible; no unassign endpoint. Success=false returned with HTTP 200 |
| D7 | Med | `salary_structure.py:82,91` | Zero-amount custom lines silently dropped; negative amounts, empty-string names with amount, NaN/Infinity accepted; no limits (>99,999,999.99 -> 500 numeric overflow with DB text in message) |
| D8 | Med | `report_functions.py:546,735` | Basic is never prorated: absent/leave days do not reduce pay; present-day = any attendance row (even without punch-out) |
| D9 | Med | `router/salary.py:55-86` | `async def` handlers run blocking psycopg2 + boto3 calls and do N x (11 queries + 2 S3 calls) sequentially: one slow org blocks the event loop; pool max 20 |
| D10 | Med | `salary_structure.py:545` | Preview month/year in UTC and current-month only; content is template-only |
| D11 | Low | `salary_structure.py:181-183`, `:28-30` | `count_basis/upto/next` accepted but never stored/returned (always null) |
| D12 | Low | `salary_structure.py:411` | `int(branch_id)` unguarded -> 500 on non-numeric; `dept_id` is a string in users but `branch_id` an int |
| D13 | Low | `salary_structure.py:133` | Empty list returns bare `[]` vs `{success,data}` otherwise (P2-6); frontend `listOf` tolerates both |
| D14 | Low | `gen_plans_functions.py:179` | Stale docstring says Core blocks salary_structure; gate only blocks on expiry. Gate missing from 9 of 12 routes |
| D15 | Low | DDL `:780-842` | No FK / unique on `pay_slip_id`; `payroll.created_at` NULL always; `updated_at` of lines never set |
| D16 | Low | `salary_structure.py:66` vs incentive code `get_utc_now()` | Mixed timestamp sources (`CURRENT_TIMESTAMP` vs app UTC) into naive `timestamp` columns |

## 8. Clockyn requirements

Cross-references: `backend_changes.md` P0-6 (paid state), P1-9 (delete cascade), P1-16 (preview month), P2-1 (500-wrapped errors), P2-6 (envelopes), P2-7 (validation), P3-1 (auth); `DEFERRED_DECISIONS.md` DD-10, DD-13 (gate placement), DD-61 (salary_type/fine placeholders), DD-71..73 (payslip validation), DD-78 (payslip assignment).
Migration DDL below is a sketch (unqualified table names; the connection sets `search_path` from `DB_SCHEMA`, `db_function.py:61`). Apply per the migrations README flow; **do not** create files from this doc.

### 8.1 Salary paid state: `GET /salary/monthly-list`, `POST /salary/mark-paid` (P0-6)
Current: not mounted; no table.
Required (frontend contract `src/api/salaryReports.ts:107-124`, mock `e2e/mock-server/routes/reports.js:65-100`):
- `GET /salary/monthly-list?org_id&month&year` -> `{success:true, data:[{user_id,name,emp_id,dept_name,branch_name,salary_type,basic_salary,net_salary,paid:boolean,paid_date:'YYYY-MM-DD'|null}]}`.
- `POST /salary/mark-paid` body `{org_id, user_ids:[str], month:int, year:int}` -> `{success:true, message}`. Idempotent. Note both paths have **no `/api` prefix**, while `router/salary.py:23` has prefix `/api/salary`.

Migration sketch:
```sql
CREATE TABLE salary_payments (
  id          int4 GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id      varchar(36) NOT NULL,
  user_id     varchar(36) NOT NULL,
  month       smallint    NOT NULL CHECK (month BETWEEN 1 AND 12),
  year        smallint    NOT NULL CHECK (year BETWEEN 2000 AND 2100),
  basic_salary numeric(12,2) NOT NULL DEFAULT 0,   -- snapshot at pay time
  net_salary   numeric(12,2) NOT NULL,             -- snapshot at pay time
  paid_at     timestamptz NOT NULL DEFAULT now(),
  paid_by     varchar(36) NULL,                    -- NULL until the API has an authenticated caller (P3-1)
  CONSTRAINT uq_salary_payments UNIQUE (user_id, month, year)
);
CREATE INDEX idx_salary_payments_org_period ON salary_payments (org_id, year, month);
```
Implementation outline:
1. `router/salary.py`: add `payments_router = APIRouter(prefix="/salary", tags=["Salary Payments"])` and `app.include_router(...)` next to `app.py:43` (a plain `@router.get("/monthly-list")` under the existing prefix would produce `/api/salary/monthly-list`, which the app does not call). Alternatively register absolute-path routes on a prefix-less router.
2. Refactor `get_staff_salary` so the SQL core takes an open cursor (`compute_salary(cur, user_id, year, month)`), and keep the async wrapper. Reuse it for staff-breakdown, monthly-list and mark-paid.
3. `monthly-list`: validate `1<=month<=12`; gate `check_plan_feature(org,'salary_structure')` (same as breakdown); same staff query as `router/salary.py:36-45` plus `LEFT JOIN dept_info`; `LEFT JOIN salary_payments sp ON sp.user_id=u.user_id AND sp.month=%s AND sp.year=%s`. For paid rows return the snapshot `net_salary/basic_salary`; for unpaid compute live. `paid_date = (sp.paid_at AT TIME ZONE 'Asia/Kolkata')::date` formatted `YYYY-MM-DD`. `salary_type` `'MONTHLY_FIXED'` (keep identical to breakdown).
4. `mark-paid`: validate non-empty `user_ids` (400), month/year range, dedupe ids; verify every id is an active role-2/3 user with `parent_id = org_id` (else 400 listing offenders); **compute `net_salary` server-side** per user (never accept client amounts); `INSERT ... ON CONFLICT (user_id, month, year) DO NOTHING`; one transaction; response `{success:true, message:"Salary marked as paid for N employee(s)", marked:N_new, already_paid:M}` (extra keys are additive; the app ignores them). Decision: reject future periods (month after the current IST month) — product to confirm.
5. Cleanup hooks: `DELETE FROM salary_payments WHERE user_id = %s` beside `add_staff_function.py:754` and org-wide beside `company_functions.py:455`.
6. Optional (gap): `DELETE /salary/mark-paid` `{org_id,user_ids,month,year}` to undo a mistaken mark; the app has no UI for it today.
Backward compat: purely additive; no existing client calls these paths.
Acceptance: (a) mark A,B paid for 3/2026 -> list shows `paid:true`, `paid_date` = IST date, both users; (b) repeat call -> 200, no duplicate row, `already_paid=2`; (c) change A's advance afterwards -> list still shows A's snapshot net, breakdown shows the new live net; (d) other month list shows unpaid; (e) user from another org -> 400; (f) expired plan -> 403.

### 8.2 Staff-breakdown contract (formula, `fine`, `travel_ticket`, columns, salary type)
Current behaviour: §4.3. The frontend salary sheet (`src/features/reports/utils/salarySheet.ts:8-140`) displays 13 amount columns and a local-only Type override; DD-61 records `fine`/`salary_type` as placeholders.
Required (all additive, no key removal):
1. Add fields so the UI can reconcile: `hra`, `custom_earnings_total`, `epf`, `professional_tax`, `health_insurance`, `custom_deductions_total`, `total_earnings`, `total_deductions`, `present_days`, `total_hours` (all already computed in `get_staff_salary`, `report_functions.py:742-775`). Invariant to test: `net_salary == total_earnings - total_deductions` and `total_earnings == basic + hra + daily_wages + hourly_calc + incentive + bonus + overtime + odometer + travel_allowance + other_bills + custom_earnings_total`.
2. Make `travel_ticket` real or drop: either source it from a separate expense type (needs product; `expense_type` currently TRAVEL/BILL only, `report_functions.py:714,722`) or keep as an alias and document it as a duplicate. Do not add it to net twice.
3. `fine`: stays `0` until a fines feature exists. If wanted: table `salary_fines(id, org_id, user_id, month, year, amount numeric(12,2), reason, created_at)` and subtract in `compute_salary`; product decision.
4. `salary_type`: constant `'MONTHLY_FIXED'`. Option: derive per staff (`DAILY_WAGES` when only a D template and no payslip, `HOURLY` likewise, else `MONTHLY_FIXED`), but earnings are additive (basic + daily + hourly), so a derived label would misdescribe mixed staff; keep constant unless product defines precedence.
5. Do not swallow errors: return the row with `error: "<message>"` and a null/omitted net (or fail the request) instead of an all-zero row (`router/salary.py:87-101`).
6. Performance (P2-9): single set-based query per component or at least `run_in_executor`; skip S3 photo lookup unless `?include_photos=true`.
7. Validate `1<=month<=12`, `2000<=year<=2100` (422).
Acceptance: totals reconcile for a staff with HRA, custom lines, wages and incentives; a forced DB error for one staff returns an error marker, not zeros.

### 8.3 Payslip template delete cascade (P1-9)
Required: delete removes lines and either removes or blocks on assignments, org-scoped.
Outline (`salary_structure.py:325-346`): accept `org_id` query param (or look it up) and `SELECT org_id FROM pay_slip WHERE pay_slip_id=%s` to verify; count `payroll` rows; **recommended: 409 `{success:false, message:"Template is assigned to N staff", assigned_count:N}` unless `?force=true`**, in which case, in one transaction: `DELETE FROM payroll WHERE pay_slip_id=%s`, `DELETE FROM payslip_add_earnings ...`, `DELETE FROM payslip_add_deduction ...`, `DELETE FROM pay_slip ...`, 404 if no `pay_slip` row (check `rowcount` before commit, rollback on failure). The current client confirms first and does not pass `force` (`payroll.ts:289-292`), so keep the default behaviour = cascade for compatibility, and make the 409 an opt-in `?strict=true`.
Migration sketch (data repair then integrity):
```sql
DELETE FROM payroll pr WHERE NOT EXISTS (SELECT 1 FROM pay_slip ps WHERE ps.pay_slip_id = pr.pay_slip_id);
DELETE FROM payslip_add_earnings e WHERE NOT EXISTS (SELECT 1 FROM pay_slip ps WHERE ps.pay_slip_id = e.pay_slip_id);
DELETE FROM payslip_add_deduction d WHERE NOT EXISTS (SELECT 1 FROM pay_slip ps WHERE ps.pay_slip_id = d.pay_slip_id);
ALTER TABLE pay_slip ADD CONSTRAINT uq_pay_slip_id UNIQUE (pay_slip_id);
ALTER TABLE payroll ADD CONSTRAINT fk_payroll_slip FOREIGN KEY (pay_slip_id) REFERENCES pay_slip(pay_slip_id) ON DELETE CASCADE;
ALTER TABLE payslip_add_earnings ADD CONSTRAINT fk_pae_slip FOREIGN KEY (pay_slip_id) REFERENCES pay_slip(pay_slip_id) ON DELETE CASCADE;
ALTER TABLE payslip_add_deduction ADD CONSTRAINT fk_pad_slip FOREIGN KEY (pay_slip_id) REFERENCES pay_slip(pay_slip_id) ON DELETE CASCADE;
CREATE INDEX idx_pae_slip ON payslip_add_earnings (pay_slip_id);
CREATE INDEX idx_pad_slip ON payslip_add_deduction (pay_slip_id);
```
(Run the repair statements only after confirming on the deployed data; UNVERIFIED: whether production already holds orphans.)
Acceptance: delete an assigned template -> `payroll` empty for it; the staff can be assigned another template; no orphan lines; second delete -> 404.

### 8.4 Assignment semantics (payslip) (DD-78)
Current: one payslip per user globally (unique `user_id`), assign refuses when one exists, no unassign (§4.4). Staff picker marks only the current template. The app (`payroll.ts:318-325`) assigns one staff at a time and surfaces `payslip_name` from the refusal.
Required: (a) `DELETE /api/salary/assign/{org_id}/{staff_id}` (unassign, 404 if none); (b) optional `replace: true` on `POST /api/salary/assign` -> `UPDATE payroll SET pay_slip_id=%s, updated_at=now()` (use `INSERT ... ON CONFLICT (user_id) DO UPDATE`); (c) also set `payroll.created_at/updated_at`; (d) validate the template exists and has the same `org_id`, the staff is active and in the org (`parent_id`); (e) make `/staff` return `assigned_payslip_id` + `assigned_payslip_name` per row so the picker can show "currently on X" (additive); (f) return HTTP 409 (not 200 `success:false`) for "already assigned" **only** behind a version flag, since the app depends on the current `success:false` + `payslip_name` shape.
`staff_id` vs `user_id`: the picker (`#9`) returns `staff_id`/`staff_name`/`department_name`; incentive/wage lists return `user_id`/`name`/`dept_name`. The frontend mapper accepts both (`payroll.ts:122-132,169-178`). Keep both keys (add `user_id` alias to `/staff` rather than renaming).
Acceptance: assign A->T1, assign A->T2 with `replace` moves; unassign then assign works; assign to a template of another org -> 400.

### 8.5 Preview month/year (P1-16)
Add optional query `month` (1-12) and `year`; default to the IST current month (`datetime.now(IST)`; `IST` is already imported in `salary_structure.py:5`) instead of `get_utc_now()` (`:545`); return `month` as English name (keep) plus numeric `month_number`. Optionally compute the staff's real figures by calling `compute_salary` (flag `?actual=true`). Return 404 (not 500) for unknown staff/payslip/org. Acceptance: `?month=2&year=2026` header shows February 2026; at 2026-03-31T20:00Z (IST April 1) default month is April.

### 8.6 Plan gate
Current: §4.8. Required: (a) keep behaviour (only expiry blocks `salary_structure`) but fix the stale docstring `gen_plans_functions.py:179`; (b) apply the gate uniformly to every `/api/salary/*` route via `APIRouter(dependencies=[...])` resolving the org from path/query/body, **except** employee-facing `GET /employee/...` and `GET /preview/...` which should call it with `is_admin=False` (employee message variant, `gen_plans_functions.py:189`) because the employee payslip sheet (B11) uses them; (c) keep the real 403 (do not wrap in a `try`). Decision needed (DD-10/DD-13): whether Core should block `salary_structure` (docstring intent) or not (current matrix).
Acceptance: expired org -> 403 on template update/delete/assign; active Core org -> 200 on all.

### 8.7 Amount / validation bounds, numeric types (DD-71..73, P2-7)
Enforce server-side (Pydantic v1/v2 `Field`/validators; return 422 or 400 with a clear `detail`), do not rely on the client:

| Field | Rule |
|---|---|
| `payslip_name` | trimmed, 1-255 chars, unique per org case-insensitively (mock enforces; no server check today) |
| `basic_pay`, `hra`, `epf`, `professional_tax`, `health_insurance` | `Decimal`, `0 <= x <= 99_999_999.99`, max 2 decimals (matches `numeric(10,2)`), reject NaN/Infinity; `basic_pay > 0` if product accepts DD-71(a) |
| custom lines | name trimmed 1-255; amount `> 0` and `<= 99_999_999.99`, 2 dp; **reject** zero/blank lines with 422 instead of silently dropping (`salary_structure.py:82,91`); cap count (e.g. 20 per side, product to confirm) |
| Totals | `total_deductions <= total_earnings` is NOT enforced today (negative net possible); product decision whether to reject |
| `month`/`year` params | 1-12 / 2000-2100 |
| `branch_id` | int; return 422 not 500 |

Numeric types: DB money is `numeric(10,2)`; the Python layer uses `float`. Move to `decimal.Decimal` end-to-end (Pydantic `condecimal(max_digits=10, decimal_places=2)`), compute totals in `Decimal`, serialise as JSON numbers (FastAPI encodes `Decimal` with fractional part as float, so the wire format stays numeric and compatible with `num()` in `payroll.ts`). Rounding rule: ROUND_HALF_UP to 2 dp when storing derived values. Acceptance: `0.1 + 0.2` style inputs total exactly `0.30`; `100000000` -> 422.

### 8.8 Envelope / error fixes (P2-1, P2-6)
Return `{success:true, data:[]}` for an empty org (`salary_structure.py:133`); add `except HTTPException: raise` before the generic handlers (create/update/list/preview); return real 404s (update, preview, employee template missing). Keep `{success:false,message}` shapes of #10/#11 for client compatibility.

## 9. Test notes
No automated tests exist in the repository (`find . -iname "test*"` returned nothing). Fixtures: one org (free trial active), one org with expired subscription, one Core-plan org, staff with roles 2 and 3 (+ an inactive one and an admin), 2 branches, 2 departments, shift assigned, attendance rows, an approved odometer reading, TRAVEL and BILL expenses, an advance, daily/hourly templates assigned, count/turnover/bonus/overtime templates assigned.
Critical cases: (1) template create with custom lines incl. `amount=0` (dropped) and verify totals; (2) update replaces lines; update unknown id -> currently success (document/fix); (3) delete assigned template -> orphan `payroll`, then re-assign 500 (regression for §8.3); (4) assign twice -> `success:false` + `payslip_name`; (5) staff-breakdown for a staff with no payslip -> zeros; with payslip and all components -> verify §4.2 formula incl. bonus only in matching month/year and overnight shift overtime; (6) force `get_staff_salary` failure -> zero row (defect D3); (7) gate: expired org 403 on 3 routes but 200 on others; Core plan -> 200; (8) preview month at UTC/IST month boundary; (9) `GET /templates/{org}` empty org returns bare `[]`; (10) numeric overflow `1e8` -> 500 today.

### Changes (payslip template lookup scoped to the organisation)

* `GET /api/salary/template/{pay_slip_id}` takes an optional `org_id` query parameter and uses `caller_orgs(request)`: with `org_id` the lookup is `WHERE pay_slip_id = %s AND org_id = %s`; with a token the template's org must be one of the caller's organisations. Another organisation's id answers **404** `Salary template not found` exactly like a missing one. No `org_id` and no token: the old behaviour (statement and response unchanged). Response shape unchanged.
* Tests: `tests/test_announce_template_scope.py`. PUT / DELETE of the template by id are unchanged here (PUT already filters by the body `org_id`).
