# Reports (attendance PDF, staff salary) & Advance — backend context

Domain owner: BD3. Frontend: `C:\POC\pa_frontend\Clockyn\src\api\reports.ts` (PDF downloads, lines 41-64), `src\api\salaryReports.ts` (advance calls, lines 126-160). Decisions: DD-50, DD-64, DD-66.

## 1. Scope & files
| File | Lines | Role |
|---|---|---|
| `router/report.py` | 192 | `/api` router (mounted `app.py:56`): holiday copy (shadowed), staff salary, 4 attendance-PDF endpoints |
| `core_functions/report_functions.py` | 780 | `generate_monthly_attendance_pdf` (reportlab canvas, 96-529), `get_staff_salary` (532-780) |
| `router/advance.py` | 50 | 4 routes under `/api/advance` (mounted `app.py:62`) |
| `core_functions/advance_functions.py` | 154 | create/list/delete advance (sync functions) |
| `core_functions/manager_functions.py:376-407`, `core_functions/permission_checker.py:7-40` | - | scope + permission helpers for manager PDF routes |
| `core_functions/holiday_functions.py` | - | logic behind the shadowed holiday routes |

Libraries used here: **reportlab** (canvas) and **pypdf** (merge, `router/report.py:11,96-107`) only. `weasyprint`, `xhtml2pdf`, `openpyxl` are used only in `utils/email.py` (invoices). No server-side Excel/CSV export exists; the frontend builds xlsx/CSV client-side.

## 2. Data model touched
| Table | Columns | Use |
|---|---|---|
| `users` | `user_id,name,emp_id,mobile_number,dept_id,branch_id,role_id,status,parent_id,org_id,email` | identity, org resolution, scope |
| `dept_info` | `dept_id,dept_name` | PDF staff box (join not org-scoped) |
| `users_additional_info` | `user_id,shift_id` | shift |
| `shift` | `shift_id,start_time,end_time,status,org_id` | shift label/minutes (join on `shift_id`+`status=1`, no org filter) |
| `org_info`, `company_additional_info` | `org_name,org_address,parent_id`; `company_website` | company box |
| `payroll`, `pay_slip`, `payslip_add_earnings`, `payslip_add_deduction` | payslip assignment and amounts | payslip block / salary |
| `attendance` | `attendance_date,punch_in,punch_out,work_hours` | grid / salary |
| `assigned_wages`, `daily_hourly_wages`, `assigned_incentives`, `incentive_templates`, `incentive_count_details`, `incentive_turnover_details`, `incentive_bonus_details`, `incentive_overtime_details` | | wages/incentives in salary |
| `odometer_readings`, `travel_bill_expenses` | | expense lines in salary |
| `advance_details` (dump:67-75) | `id identity (no PK/index in dump), advance_id (copy of id), advance_amt numeric(10,2), "date" date, user_id, created_by, org_id` | advances |
| manager-permission tables (legacy-prefixed, `permission_checker.py:31-34`), `manager_assignments` | | "Attendance Reports" permission, branch scope |
| S3 | `org_logo/{org_id}.png` | PDF logo (`ml/s3.py:134-151`) |

## 3. Endpoints

> **Current contract:** the rows below record the pre-WP4b behaviour (line numbers refer to it); the **Changes (WP4b ...)** block after the tables is authoritative for what the endpoints do now.
All: no authentication (P3-1), no plan gate. RT=`router/report.py`, RF=`report_functions.py`.
| # | METHOD path | Handler | Scope | Request | Success | Errors |
|---|---|---|---|---|---|---|
| 1 | GET `/api/reports/monthly-attendance/{user_id}/{month}` | RT:54 -> RF:96 | any user_id, no check | `month`=YYYY-MM | PDF, inline, `attendance_{month}.pdf`, one landscape A4 page | 400 invalid month; 404 `Employee not found`/`Company not found`; others unhandled 500 |
| 2 | GET `/api/reports/monthly-attendance-all/{org_id}/{month}` | RT:64-113 | path `org_id` is really a caller user id, resolved via `COALESCE(users.org_id, users.parent_id)` | month | merged PDF of all active non-admin staff ordered by name | 404 plain-text `No staff found`; per-staff errors silently skipped |
| 3 | GET `/api/reports/manager/{manager_id}/monthly-attendance/{user_id}/{month}` | RT:116-141 | permission "Attendance Reports"; staff role 3, active, on a manager-assigned branch | | PDF | 403 permission; 403 `Staff not under your management.` |
| 4 | GET `/api/reports/manager/{manager_id}/monthly-attendance-all/{month}` | RT:144-192 | permission; **org-wide** | | merged PDF | 403; 404 plain text |
| 5 | GET `/api/reports/staff-salary/{user_id}?year&month` | RT:49 -> RF:532 | any user_id | both ints required | `{success,data:{user_id,month,year,present_days,total_hours,earnings{basic,hra,daily_wages,hourly_calc,incentive_count,incentive_turnover,bonus,overtime,odometer,travel,others,custom[]},deductions{epf,professional_tax,health_insurance,advance,custom[]},total_earnings,total_deductions,net_salary}}` | 500 `Failed to fetch salary: <exc>` |
| 6-9 | GET/POST `/api/holidays/{org_id}`, DELETE `/api/holidays/{org_id}/{holiday_id}`, GET `/api/holidays/working-days/{org_id}` | RT:25,32,37,42 | **shadowed** by `router/daily_hourly.py` (mounted first, `app.py:48`) | | | dead code (holiday domain) |
| 10 | POST `/api/advance` | advance.py:23 -> create_advance | `created_by` must be role 1 owning the org | JSON `{org_id,user_id,created_by,advance_amt,date}` | `{success,message,data:<row>}` | 403 not admin; 403 no org access; 400 employee not role 2/3; 500 |
| 11 | GET `/api/advance/org/{org_id}` | advance.py:35 | any | | `{success,data:[{id,advance_id,advance_amt,date,user_id,employee_name,emp_id,created_by,created_by_name,org_id}]}` | 500 |
| 12 | GET `/api/advance/user/{user_id}/{org_id}` | advance.py:41 | any | | same, **all months** | 500 |
| 13 | DELETE `/api/advance/{advance_id}/{org_id}` | advance.py:47 | no caller identity | | `{success,message}` | 404; 500 |

Frontend: admin all -> #2, admin one -> #1, manager all -> #4, manager team member -> #3; advances -> #10-#13. `staff-salary` is not called by the new frontend (it uses `/api/salary/staff-breakdown`, whose backend `router/salary.py:57` calls `get_staff_salary`).

### Changes (WP4b, decisions D-8, D-15)

- `GET /api/reports/staff-salary/{user_id}` (#5) is a wrapper of `salary_engine.compute_user_salary` (same `{success,data}` shape; additive fields and `deductions.fine`;
  422 for month / year outside 1..12 / 2000..2100; 404 for an unknown user). Details in `salary_and_payslips.md` Changes.
- Attendance PDF (#1-#4): punch times are printed in IST (the database session is UTC); Present / Absent / Half Days / Leaves / Holidays come from the shared
  engine counters (`salary_engine.month_attendance_figures`, optional: "-" if it fails) and the payslip block shows a `Late Fine` deduction row (attendance-rule fine, D-8)
  when the organisation's rule produces one.
- `POST /api/advance` (#10): `advance_amt` is validated before any database access: `0 < x <= 9,999,999.99`, at most 2 decimals, else 400
  `Advance amount must be greater than 0` / `Advance amount must not exceed 9999999.99` / `Advance amount can have at most 2 decimal places`.
  Advances of the month are deducted by the engine (`deductions.advance`, `advance_details.date` in the month).
- `GET /api/reports/monthly-attendance-all/...` and the manager routes keep their per-staff PDF generation (errors of one staff member are still skipped).

## 4. Business logic
**PDF (RF:96-529).**
1. Month is parsed by slicing and `int()` (`:98-100`); month 13 passes and crashes later.
2. Employee join `users + dept_info + users_additional_info + shift(status=1)`; 404 if missing; `org_id = parent_id`.
3. Company `org_info LEFT JOIN users(owner) LEFT JOIN company_additional_info`; phone/e-mail are the owner user's.
4. Payslip is the user's current `payroll -> pay_slip` (not month-specific) plus custom lines.
5. Attendance is grouped by UTC `attendance_date`: `array_agg(punch_in/out ORDER BY punch_in)` and `SUM(work_hours)`.
6. The logo is fetched via S3 presign plus `urlopen` with a 5 s timeout for every PDF.
7. Shift minutes = `end - start`, plus 24 h if negative.
8. Layout: landscape A4, title bar, company box, staff box, a per-day grid, summary and payslip blocks, and a footer "Generated on <UTC>" (unlabelled, `:198`).
9. Grid rows: Punch In/Out 1-3 (4th+ session not drawn), Total Hrs (`HH:MM`), and Difference = total - shift minutes. Sunday is hard-coded as the highlighted weekly off (`:341`). Punch times use `strftime` on DB-zone datetimes (not IST).
10. Summary: Present = days with a punch; Absent = total - present (counts future days and offs); Half Days, Leaves and Holidays are always "-" (`:415-422`).
11. The payslip block shows template amounts only (`Rs. {n:,.0f}`), so the PDF "Net Pay" differs from the salary endpoint.
12. Generation is synchronous inside `async def`.

**Merged PDFs.**
- Admin all: staff = `parent_id=org AND role_id!=1 AND status=1 ORDER BY name`. Each staff PDF is generated sequentially inside `try/except: continue` and merged with `PdfWriter`; errors are swallowed.
- Manager all: `check_manager_permission_middleware(manager_id,"Attendance Reports")`: admin is always allowed, a non-manager is denied, a manager needs an active permission row. Staff = `(parent_id=org OR user_id=manager) AND role_id IN (2,3) AND status=1`. There is **no branch/department filter**.
- Manager single: `JOIN manager_assignments ON ma.branch_id=u.branch_id`, staff role 3 only; department is ignored.

**get_staff_salary (RF:532-780).**
- `present_days = COUNT(*)` of attendance rows, i.e. per session, and `total_hours = SUM(work_hours)` (`:567-576`).
- `daily_wages = rate_D * present_days`; `hourly_calc = rate_H * total_hours`.
- COUNT incentive = `each_incentive`; TURNOVER = `basic * pct/100`; BONUS = `basic * pct/100` when month name and year match.
- Overtime: `shift_minutes = EXTRACT(EPOCH FROM end - start)/60` (negative for overnight shifts); `ot_minutes = max(0, total_hours*60 - shift_minutes*present_days)`; slots = floor(ot_minutes / duration); amount = slots * rate.
- Odometer = approved `distance_km * COALESCE(row rate, org latest rate, 1)`; travel and bills = approved sums.
- Advance = month-filtered `SUM` (`:727-733`).
- `total_earnings` = basic + hra + daily + hourly + incentives + overtime + odometer + travel + others + custom earnings.
- `total_deductions` = epf + prof_tax + health_ins + advance + custom deductions.
- `net` = earnings - deductions. No proration and no attendance rule.

**Advance.**
- Create: `created_by` must be role 1 (403); `org_info(parent_id=created_by, org_id)` must exist (403); `user_id` must be role 2/3 (400).
  - It does not check that the employee is in that org, and has no amount > 0 or date validation.
  - Steps: `INSERT ... RETURNING id`, then `UPDATE advance_id=id`, one commit, return the row.
- Lists use INNER JOINs on `users` and the creator, so orphaned advances vanish. There is no month filter.
- Delete is `WHERE id AND org_id` with no role check.
- The functions are sync `def`.

## 5. Side effects & integrations
S3 logo (presign plus server-side HTTP fetch), reportlab/pypdf in memory. Writes are only advance create/delete. No push, WebSocket or e-mail.

## 6. Config & env vars
`DB_SCHEMA`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`; `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME`. Hard-coded: permission name "Attendance Reports" (`RT:118,146`), PDF layout constants (`RF:221-248`), logo timeout 5 s, Sunday highlight, 3 punch pairs.

## 7. Known defects & risks
| ID | Sev | Where | Defect |
|---|---|---|---|
| D-RA-01 | High | RT:54,64,49 | Admin PDF/salary endpoints have no auth or ownership check (P3-1, P3-13). |
| D-RA-02 | High | RT:144-170 | Manager "All Staff" PDF is org-wide (all active managers and staff plus self, ignoring `manager_assignments`); payslip amounts included (P1-17, DD-50). |
| D-RA-03 | High | RT:116-141 vs :54 | Manager single check is bypassable via admin single #1. |
| D-RA-04 | High | RF:31-38,198 | PDF punch times are not converted to IST (DB session zone); "Generated on" is UTC, unlabelled. |
| D-RA-05 | High | RF:567-568,589,602,675 | Salary counts sessions as present days (overpays daily wages and overtime); overnight `shift_minutes` is negative. |
| D-RA-06 | Med | RF:415-422,341 | Half/Leave/Holiday always "-"; Absent counts future days and offs; Sunday hard-coded despite configurable off days. |
| D-RA-07 | Med | RT:96-107,175-186 | Merged PDFs swallow per-staff errors (an empty or partial PDF with 200); sequential, logo fetched per page, blocking in `async def`. |
| D-RA-08 | Med | RF:96-100 | Weak month validation (`2026-13` -> 500); 4th+ session not drawn. |
| D-RA-09 | Med | RF:121-130 | Company phone/e-mail are the owner user's; payslip block differs from salary breakdown. |
| D-RA-10 | Med | advance_functions.py:35-41 | Advance create has no employee-org, amount or date validation (cross-tenant possible); `advance_details` has no PK or index in the dump. |
| D-RA-11 | Med | advance.py:41-44 | Advance lists are not month-filtered while the salary deduction is (DD-66); no paid state. |
| D-RA-12 | Med | app.py:48,56,65 | Holiday routes triplicated; `report.py:25-47` is shadowed dead code (P1-5). |
| D-RA-13 | Low | advance.py:47-50 | Delete has no identity check. |
| D-RA-14 | Low | RT:93-94,172-173 | Plain-text 404 bodies. |
| D-RA-15 | Low | RF:777, RT:56-61 | Raw `month` in the `Content-Disposition` header; exception text in 500s. |

## 8. Clockyn requirements
- **R-RA-1 Auth and manager scope (P3-13, P1-17, DD-50, DD-64).**
  - Current: D-RA-01/02/03.
  - Required: authenticated caller on all PDF routes and `staff-salary`; admin routes check org ownership; manager "All" limited to `manager_assignments` and permission; payslip block only for callers allowed to see payroll.
  - Outline: use `get_manager_scope` in `RT:144-170` (`AND u.branch_id=ANY(%s) AND u.dept_id=ANY(%s)`, drop `OR user_id=manager`); verify the target's `parent_id` equals the caller's org in #1/#2; add an `include_payslip` flag.
  - Back-compat: URLs unchanged, 403 instead of org-wide data.
  - Acceptance: a branch-A manager gets only branch-A pages; another org's staff -> 403; no credentials -> 401.
- **R-RA-2 IST rendering and day semantics (P2-5, DD-07, DD-60, DD-61).**
  - Required: IST punch times and a labelled generated-on; Half/Leave/Holiday from the shared server day-type logic (see `attendance_and_home.md` R-1); off days from `holiday_functions.get_off_days`; Absent excludes future days, holidays, offs and leave.
  - Outline: `convert_utc_to_ist` in `_fmt_time_parts` (`RF:31-38`); shared `compute_day_types`; replace `RF:415-422`.
  - Acceptance: 09:05 IST prints 09:05 AM; a holiday and 2 leaves are reflected in the summary; Saturday highlighted when it is an off day.
- **R-RA-3 Async, resilient generation (P2-9).**
  - Required: thread pool; logo fetched once per request; a failure header or non-200 when all fail; strict `YYYY-MM` (400); optional branch/dept/staff filters and a max count.
  - Outline: `run_in_executor` in `RT:96-107`; pass `logo_image` in; regex `^\d{4}-(0[1-9]|1[0-2])$`.
  - Acceptance: 100 staff < 15 s without blocking the loop; invalid month -> 400.
- **R-RA-4 Salary multi-session correctness (DD-61).**
  - Required: `present_days = COUNT(DISTINCT attendance_date)`; overnight-safe `shift_minutes` (CASE end > start ... + 1440).
  - Outline: `RF:567-568`, `RF:667-674`.
  - Acceptance: 2 sessions x 10 days gives `present_days` 10; a 20:00-08:00 shift gives 720.
- **R-RA-5 Advance (DD-66, P3-1; P0-6 related).**
  - Required (additive): optional `month`/`year` query params on both list endpoints (absent = all); server validation (amount > 0, employee in the org with `status=1`); `LEFT JOIN` for orphans; migration `ALTER TABLE advance_details ADD PRIMARY KEY (id)` plus indexes on `(org_id,date)` and `(user_id,date)`.
  - Back-compat: absent params = current behaviour.
  - Acceptance: `?month=10&year=2026` returns only October advances and the sum equals the breakdown `advance`; amount 0 or negative -> 400; foreign employee -> 403.
- **R-RA-6** Remove the shadowed holiday copy in `router/report.py:16-46` (P1-5).
- **R-RA-7** No server Excel is needed; an optional bulk JSON endpoint for late/attendance summaries removes the N requests (`attendance_and_home.md` R-9).

## 9. Test notes
No existing tests. Generate PDFs from a seeded DB and assert with pypdf `extract_text` (month label, name, IST times, counts, payslip lines; 1 page per staff in merged PDFs). Cases:
- valid and invalid months (`2026-02`, `2026-13`, `2026-1`, empty)
- no shift, payslip or logo
- 3 and 4 sessions in a day
- overnight sessions across midnight
- holidays and off days
- manager permission, scope and bypass
- an admin-all failure being surfaced
- salary fixtures per earning type, multi-session days and overnight shifts
- advance create/list/delete with month filters

Contract reference: `C:\POC\pa_frontend\Clockyn\e2e\mock-server\routes\reports.js:52-63`.
