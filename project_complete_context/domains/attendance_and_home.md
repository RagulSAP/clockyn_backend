# Attendance & Home dashboard — backend context

Domain owner: BD3. Source snapshot: `C:\POC\pa_backend\Clockyn` (untracked copy, no git history). Companion docs: `face_recognition_punch.md` (punch write path), `staff_location.md`, `reports_and_advance.md`. Frontend contract notes: `C:\POC\pa_frontend\Clockyn\backend_changes.md` (IDs P0-x/P1-x/P2-x/P3-x) and `docs\DEFERRED_DECISIONS.md` (DD-xx).

Conventions used below: "org id" = the value stored in `users.parent_id` of every staff member and in `org_info.org_id`; for a single-company admin it equals the admin's `users.user_id` (`core_functions/manager_functions.py:389-390` returns the admin's own id as the org id). Several routes name that parameter `user_id`; it may be an admin id, a manager id (resolved to the manager's `parent_id`) or a raw org id (unknown ids are returned unchanged, `manager_functions.py:386-387`). Role ids: 1 admin, 2 manager, 3 staff (`home_functions.py:1182`).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/home.py` | 213 | 22 routes under `/home` (mounted `app.py:39`) |
| `core_functions/home_functions.py` | 1369 | dashboard counts, staff lists by status, branches/departments, staff detail/profile, location helpers |
| `core_functions/attendance_functions.py` | 338 | `get_daily_attendance`, `get_monthly_attendance`, `edit_attendance`, Pydantic models |
| `router/face_attendance.py` (lines 52-91) | 91 | hosts `GET /attendance/today/{user_id}` and `GET /attendance/sessions/{user_id}` (mounted `app.py:50`, no prefix) |
| `core_functions/face_attendance_functions.py` (`get_today_attendance`, 388-449) | 449 | logic of `/attendance/today` |
| `core_functions/manager_functions.py:376-407` | - | `get_manager_scope` (used by most `/home/*` routes) |
| `ml/s3.py:170-183` | - | `get_employee_profile_url` (called once per staff row in every list) |
| `utils/timezone.py` | 96 | `get_utc_now`, `convert_utc_to_ist`, `format_ist_time`, `IST`/`UTC` |

Frontend callers (`C:\POC\pa_frontend\Clockyn\src\api\`): `home.ts` (dashboard-status, department-stats, current-date, status lists via `STATUS_ROUTES`, all-staff, staff-detail, staff-profile, daily-attendance, monthly-attendance, update-attendance-punch, org-admin, branches, departments, staff-locations helpers), `attendance.ts` (`/attendance/today`, `/attendance/sessions`), `branches.ts`/`departments.ts` (`/home/branches`, `/home/departments`), `location.ts:53` (`/home/staff-punch-locations`). Not called by the frontend: `/home/staff-list`, `/home/department-status`, `/home/format-date`, `/home/staff-locations`.

Dead code in scope: `get_dashboard_stats` (alias, `home_functions.py:385-387`), `get_department_staff_attendance` + `StaffAttendanceResponse` (`:106-115`, `:1011-1093`; never imported by a router), `AttendanceDay.status` values `half_day`/`leave` (never produced).

## 2. Data model touched

Source: schema dump (legacy-named `.sql` at repo root; cited as "dump:LINE"). The dump is older than the code (see §7 D-AH-02).

### `attendance` (dump:133-155)
| Column | Type | Meaning / rule |
|---|---|---|
| `id` | int4 identity PK | session row id; also used in S3 punch-photo key |
| `user_id` | varchar(36) | `users.user_id` |
| `org_id` | varchar(36) | **client-supplied** at punch time (`face_attendance_functions.py:227-230`), `users.parent_id` fallback in `edit_attendance` (`attendance_functions.py:302-306`) |
| `attendance_date` | date | **UTC date of punch-in** (`face_attendance_functions.py:180-181`) except rows created by `edit_attendance`, where it is the admin-entered (IST-semantics) date |
| `punch_in` / `punch_out` | timestamptz | stored UTC; psycopg2 returns them in the DB session time zone (never set by `db_function.py`, so server default, UNVERIFIED) |
| `work_hours` | numeric(5,2) | hours of ONE session (`round((now-punch_in)/3600,2)`), NULL while open |
| `punch_in_location`, `punch_out_location` | point | stored `(longitude,latitude)`; read as `col[0]=lng`, `col[1]=lat` |
| `punchinface`, `punchoutface` | text | **presigned S3 URL (7 days)** written by a background task; NULL until the upload finishes or if it fails |
| `face_mismatch` | bool default false | set when `force_accept=true`; **never read by any endpoint** (grep) |
| `created_at`, `updated_at` | timestamptz | |
Indexes: **UNIQUE `(user_id, attendance_date)`** (dump:150), plus non-unique `(user_id, attendance_date)`, `(org_id, attendance_date)`, `(attendance_date)`, `(org_id)`, `(user_id)`. The unique index contradicts the 2-sessions-per-day rule and is required by `edit_attendance`'s `ON CONFLICT` (see D-AH-02).
Status semantics (derived, not stored): open = `punch_in NOT NULL AND punch_out IS NULL`; closed = both set. No leave/half-day/holiday rows exist in this table.

### Other tables read
| Table | Columns used | Notes |
|---|---|---|
| `users` (dump:991-1027) | `user_id, org_id, name, emp_id, branch_id, role_id, dept_id, parent_id, mobile_number, email, status, is_field_staff, created_at` | `status` 1 active / 0 inactive; `users.org_id` has a UNIQUE index (only admins have it); staff have `org_id` NULL and `parent_id` = org id; `branch_id` int4 |
| `users_additional_info` | `shift_id`, profile fields (`dob, gender, blood_group, address, emergency_contact_no, acc_no, ifsc_code, bank_name, bank_branch, father_name, marital_status, aadhaar_no, pan_no, uan_no, pf_no, esi_no, doj`) | 1 row per user; `shift_id` NULL or 0 = no shift |
| `shift` (dump:934-949) | `shift_id, org_id, start_time (time), end_time (time), status` | unique `(shift_id, org_id)`; start/end are IST wall-clock `time` values |
| `branch_info` (dump:160-174) | `branch_id (int, per-org counter), branch_name, branch_address, gps_location (point lng,lat), punch_radius (default 100), status, org_id` | `branch_id` is NOT globally unique (see D-AH-16) |
| `dept_info` (dump:267-279) | `dept_id (uuid string), dept_name, org_id, status` | |
| `role_info` | `role_id, role_name` | joined in lists but role_name unused in responses |
| `manager_assignments` (dump:670-680) | `user_id, branch_id, dept_id, org_id` | manager scope; unique `(user_id, branch_id, COALESCE(dept_id,''))` |
| `org_info` | `org_id, parent_id` | used by `/home/org-admin` indirectly via `users` only |

Soft delete: staff are `users.status = 0` (inactive); lists differ on whether they include inactive rows (§3).

## 3. Endpoints

Auth: **none on any route in this file** (identity = ids in query/path/body; P3-1). Plan gate: none on any route here. Handler = function in `core_functions/home_functions.py` (HF) or `attendance_functions.py` (AF) unless stated. Response models are Pydantic (`home_functions.py:11-115`, `attendance_functions.py:10-49`).

| # | METHOD path | Router line / handler | Identity param | Request | Success response | Error quirks |
|---|---|---|---|---|---|---|
| 1 | GET `/home/dashboard-status` | `home.py:14` -> HF `get_dashboard_status` 117 | `user_id` (admin/manager/org id) | q: `user_id` req, `branch_id?` (`"0"` = all), `target_date?` YYYY-MM-DD | `{total_staff, punched_in, working, punched_out, absent, late_punched_in}` ints | bad `target_date` silently becomes today (IST) `:126-132`; any exception -> 500 `Failed to get dashboard Status: ...` |
| 2 | GET `/home/department-status` | `home.py:22` -> HF `get_department_wise_Status` 887 | `org_id?` | q: `org_id?`, `branch_id?`, `target_date?` | list of `DepartmentStatusResponse` | empty `org_id` -> `[]`; **always 500 for a real org** (D-AH-01) |
| 3 | GET `/home/branches` | `home.py:34` -> HF `get_branches` 228 | `org_id` (admin/manager/org id) | q: `org_id` req | list `{branch_id (string), branch_name, branch_address, org_id, branch_latitude, branch_longitude, punch_radius}` | manager id -> only assigned branches `:240-249`; active only (`status=1`); `gps_location[0]`=lng, `[1]`=lat `:244-245` |
| 4 | GET `/home/departments` | `home.py:39` -> HF `get_departments` 270 | `org_id` | q: `org_id` req | list `{dept_id, dept_name, org_id}` | never manager-scoped; active only |
| 5 | GET `/home/staff-list` | `home.py:44` -> HF `get_staff_list` 293 | `user_id` | q: `user_id` req, `branch_id?`, `dept_id?` | list `StaffResponse` incl. inactive staff | `branch_id="0"` is NOT treated as "all" (`:326`); status via `CURRENT_DATE` (DB date) |
| 6 | GET `/home/department-stats` | `home.py:53` -> HF `get_department_stats` 389 | `user_id` used directly as org id | q: `user_id` req, `branch_id?`, `target_date?` (IST default) | list `DepartmentStatusResponse` (all active depts, zero rows included) | no manager scope: a manager id returns zero-staff departments (org filter = manager id); `target_date` NOT validated (bad string -> SQL error -> 500) |
| 7 | GET `/home/all-staff` | `home.py:62` -> HF `get_all_staff` 473 | `user_id` | q: `user_id` req | list `StaffResponse` incl. inactive (`status:"Inactive"`, `is_active:false`) | scope by manager branches/depts; `CURRENT_DATE` basis |
| 8 | GET `/home/absent-staff` | `home.py:67` -> HF `get_absent_staff` 554 | `user_id` | q: `user_id`, `branch_id?`, `target_date?` | list `StaffResponse` status `"Absent"` | `branch_id="0"` compared as `"0" != 0` (always true, `:587`) -> filters branch 0 -> empty |
| 9 | GET `/home/late-punched-in-staff` | `home.py:76` -> HF `get_late_punched_in_staff` 621 | `user_id` | same | list status `"Late Punched-In"` | first punch of the day only (`:651-658`); same `branch_id="0"` bug (`:664`) |
| 10 | GET `/home/punched-in-staff` | `home.py:85` -> HF `get_punched_in_staff` 698 | `user_id` | same | list status `"Punched In"` | users with an OPEN session on the date (`:726-731`) |
| 11 | GET `/home/working-staff` | `home.py:94` -> HF `get_working_staff` 771 | `user_id` | same | **identical to #10** (status still `"Punched In"`) | never returns `"Working"` |
| 12 | GET `/home/punched-out-staff` | `home.py:103` -> HF `get_punched_out_staff` 776 | `user_id` | same | list status `"Punched Out"` | users with ANY closed session on the date (`:803-810`), even if a second session is open |
| 13 | GET `/home/current-date` | `home.py:112` -> HF `get_current_date` 849 | none | none | `{current_date "YYYY-MM-DD" (IST), formatted_date "DD-MM-YYYY", day_name}` | |
| 14 | POST `/home/format-date` | `home.py:117` -> HF `format_date` 861 | none | **query** `date_string` (bare str param) | `DateResponse` | tries `%Y-%m-%d,%d-%m-%Y,%m/%d/%Y,%d/%m/%Y`; unparseable -> returns TODAY (IST), no error |
| 15 | GET `/home/staff-detail` | `home.py:122` -> HF `get_staff_detail` 1095 | `staff_id`,`org_id` | q: both req | `{user_id,name,emp_id,mobile_number,email,dept_name,branch_name,branch_address,status,profile_photo_url}` | `u.status=1` and `u.parent_id=org_id` only (`:1112`); not found -> 404 re-wrapped as **500** `Failed to get staff detail: 404: ...` (`:1118,1145`); no `branch_id` |
| 16 | GET `/home/staff-profile` | `home.py:130` -> HF `get_staff_profile` 1151 | `staff_id`,`org_id` | q: both req | `StaffProfileResponse` (identity + HR + bank + gov-id fields, `role_name` mapped from `role_id`, `dob`/`doj` `YYYY-MM-DD`) | `u.status=1` filter (`:1172`) -> inactive staff unreadable; 404 wrapped as 500 (`:1224-1225`); no `branch_id`; PII without auth |
| 17 | GET `/home/daily-attendance` | `home.py:138` -> AF `get_daily_attendance` 51 | `org_id` (admin/manager/org id) | q: `org_id`, `target_date` req, `branch_id?` (`"0"`=all), `dept_id?` (`"0"`=all) | list `{user_id,name,emp_id,mobile_number,email,dept_name,branch_name,punch_in,punch_out,total_hours,status,location}` | bad date -> 500 (inner 400 swallowed by outer `except Exception`, `AF:63-66,148-149`); active role 2/3 only |
| 18 | GET `/home/monthly-attendance` | `home.py:148` -> AF `get_monthly_attendance` 154 | `staff_id` | q: `staff_id`,`org_id`,`month`,`year` req ints | `{month,year,total_days,present_days,absent_days,late_days,attendance_data:[AttendanceDay]}`; `AttendanceDay={date,status,punch_in,punch_out,total_hours,location,distance(null),punch_in_photo,punch_out_photo}` | **any exception -> 200 with an empty month** (`AF:241-250`); `org_id` unused |
| 19 | GET `/home/staff-locations` | `home.py:158` -> HF `get_staff_locations` 1230 | `org_id` | q: `org_id`, `target_date?` | list of dicts | see `staff_location.md` |
| 20 | GET `/home/staff-punch-locations` | `home.py:166` -> HF `get_staff_punch_locations` 1306 | `org_id` | q: `org_id`, `target_date?` | list of dicts | see `staff_location.md` |
| 21 | GET `/home/org-admin/{user_id}` | `home.py:174-208` (inline SQL) | path `user_id` | none | admin card `{user_id,name,emp_id,mobile_number,email,role_id:1,role_name:"Admin",dept_name:null,branch_name:null,profile_photo_url,is_active:true}` or JSON `null` | joins `users u1` (status=1) -> `users u2 ON u2.org_id = u1.parent_id AND u2.role_id=1`; no admin -> `null` (HTTP 200) |
| 22 | PUT `/home/update-attendance-punch` | `home.py:210` -> AF `edit_attendance` 270 | `staff_id` (no caller identity at all) | JSON `EditAttendanceRequest{staff_id, org_id?, date "YYYY-MM-DD", punch_in? "HH:MM"/"hh:mm AM", punch_out?}` | `{success:true, message, work_hours, punch_in, punch_out}` (IST `%I:%M %p`) | 400 neither time given for an absent day; 422 bad time (`AF:268`); 400 org unresolved; 500 `Failed to update attendance`; bad `date` -> 500 (not caught as 400) |
| 23 | GET `/attendance/today/{user_id}` | `face_attendance.py:52` -> `get_today_attendance` (FAF 388) | path `user_id` | none | `{success,punch_in,punch_out,is_punched_in,sessions:[{punch_in,punch_out,work_hours}],total_work_hours}` | empty day -> `is_punched_in:false`, `sessions:[]`, `total_work_hours:0`; DB error -> 500 |
| 24 | GET `/attendance/sessions/{user_id}` | `face_attendance.py:58-91` (inline) | path `user_id` | q: `date` req (string, unvalidated) | `{success:true,sessions:[{punch_in,punch_out,punch_in_photo,punch_out_photo}]}` | **any error -> HTTP 200 `{success:false,sessions:[],error}`** (`:87-88`) |

`StaffResponse` = `{user_id,name,emp_id,mobile_number,email,dept_name,branch_name,status,profile_photo_url,is_active=true,role_id=null}` (`HF:38-49`). Only #5/#7 fill `role_id`/`is_active` (`:362-374`, `:531-543`); #8-#12 never set `role_id` (always null) and leave `is_active` at its default (true, which is accurate because those queries are active-only). `profile_photo_url` is set in every list (S3 lookup, null when no object).

Added (step 1, `router/attendance_rule.py` + `core_functions/attendance_rule_functions.py`, table `attendance_rule`; stored setting only, not yet read by attendance or salary):

| # | METHOD path | Request | Success response | Errors |
|---|---|---|---|---|
| 22 | GET `/api/attendance-rule/{org_id}` | path | `{success, late_punch_in_count, consider_as, configured}`; defaults 3 / `Half Day` / `false` when no row | 404 unknown org |
| 23 | POST `/api/attendance-rule/{org_id}` | `{late_punch_in_count:int 1..10, consider_as:'Half Day'\|'Leave'}` | `{success, message:'Attendance rule saved', late_punch_in_count, consider_as}`; idempotent upsert | 422 invalid values; 404 unknown org |

Unwired helpers: `late_days_for_user_month`, `penalty_days_for`, `penalty_for_user_month`.

### Changes (WP5 attendance integrity, migration 028; decisions D-2, D-5, D-22, NEW-2)

Code: `core_functions/attendance_rules.py` (pure rules), `attendance_functions.py`, `face_attendance_functions.py`, `home_functions.py`, `staff_location_functions.py`, `router/home.py`, `router/face_attendance.py`. Tests: `tests/test_attendance_ist.py` (111 DB-free cases). Schema: `migrations/028_attendance_sessions.sql` + `clockyn_ddl_latest.sql`.

- **Unique index (NEW-2).** The dump's `UNIQUE INDEX attendance_user_id_attendance_date_key (user_id, attendance_date)` forbade the second session of a day. Migration 028 drops it (`DROP CONSTRAINT` first when the name is constraint-backed, then `DROP INDEX IF EXISTS`; it deletes no data) and creates `ux_attendance_one_open ON attendance (user_id) WHERE punch_in IS NOT NULL AND punch_out IS NULL` (at most one open session per user). The table keeps ONE ROW PER SESSION; there is no session-number column (a session is its row `id`, ordered by `punch_in`), so no `(user_id, attendance_date, session_no)` index was created. The 2-per-day limit stays in the punch code under `pg_advisory_xact_lock(hashtext(user_id))`. The migration pre-checks for users with more than one open session and stops (RAISE EXCEPTION, whole migration rolled back) with the query to resolve them. New column `attendance.auto_closed boolean NOT NULL DEFAULT false`.
- **IST business date (D-2).** `attendance_date` of a new row = IST date of the punch (`attendance_rules.ist_today(now)`); no attendance read/write uses `CURRENT_DATE`, UTC `date()` or the DB session zone any more (punch, today, sessions, staff-list/all-staff/staff-detail status, location log + location queries, dashboard late checks). Rows written before the release keep their UTC-based date (optional back-fill statement is commented at the end of 028; not run).
- **Overnight / stale sessions (D-22).** `GET /attendance/today/{user_id}` returns today's rows (IST date) plus an OPEN session that started earlier and is younger than 18 h (an overnight session is "today's open session" until closed). Additive fields: `date`, `punch_count`. An open session older than 18 h is stale: punch-out answers `no_active_punch`; the next punch-in closes it at the shift end of the day it was opened (first shift end after the punch-in, `punch_in + 9 h` without a shift or when that end is more than 18 h away), sets `auto_closed = TRUE`, then proceeds; the punch response gains `auto_closed_previous: true` (only then). Exactly 18 h is not yet stale.
- **Sessions.** `GET /attendance/sessions/{user_id}?date=` (moved to `get_attendance_sessions`): IST times (`"09:15:00 AM"`), per-session `work_hours` (live for an open one), `status` (`open | closed | auto_closed`), `auto_closed`, response `date`; invalid date -> 400, DB error -> 500 (no more HTTP 200 `success:false`).
- **Monthly attendance.** Default shape unchanged; per-day additive `shift_start_time`, `shift_end_time` (`HH:MM`), `late_by_minutes`, `session_count`; lateness = first punch-in in IST against the shift (+ `attendance_rule.grace_minutes`, 0 without a rule; night shifts via `is_late`); `punch_out` is the LAST session's (null while it is open); errors are 404 (unknown staff) / 400 / 422 (`detail` not v1/v2) / 500 instead of an empty month. **`?detail=v2`** returns a dict `{success, month, year, as_of, total_days, working_days, present_days, late_days, half_days, leave_days, holiday_days, weekly_off_days, absent_days, attendance_data}` with a row for EVERY day: `type` (`present | late | half_day | absent | leave | holiday | weekly_off | upcoming`), `status` (`present | late | half_day | absent | leave`; holiday / weekly off / upcoming report `absent`), `is_holiday`, `is_weekly_off`, `holiday_name` (holidays only), `punch_in`, `punch_out`, `total_hours`, `location`, photos, `shift_start_time`, `shift_end_time`, `late_by_minutes`, `session_count`, `sessions[] {punch_in, punch_out, work_hours, status, auto_closed, punch_in_photo, punch_out_photo}`. Precedence: punches > approved leave (`leave_dates` joined to `requests`, type `leave`, status approved) > holiday (org + branch/dept scope, any `block_attendance`) > weekly off (`org_off_days`, default Sunday) > upcoming (today and later) > absent. HALF = worked time >= 1 min and < half of the shift span (9 h without a shift) and no session still open; HALF supersedes late; `late_days` counts punched days with `late_by_minutes > 0`; `absent_days` = elapsed working days without punches or leave. Mirrors `e2e/mock-server/domain.js` and `attendanceCalendar.ts`; the mock's fixed 15 min grace is NOT used (grace comes from the org's attendance rule).
- **Daily report / dashboard / lists.** `daily-attendance`: `Working` while any session is open, `punch_out` of the last session; the invalid-date 400 is no longer wrapped into a 500. Dashboard counts: one row per staff member (`punched_in` = any open session; `punched_out` = has closed sessions and none open); late counts use the FIRST punch-in of the day in IST evaluated by the shared rule (replaces `punch_in::time > start_time` in `get_dashboard_status`, `get_department_stats`, `get_department_wise_Status`, `get_late_punched_in_staff`). `punched-out-staff` excludes staff whose second session is open. Location summaries (`/home/staff-locations`, `/home/staff-punch-locations`, `/staff-location/locations`) return one row per staff member: first punch-in, last session's punch-out, clock in IST; log/history/stats use the IST day of each log.
- **Admin edit (`PUT /home/update-attendance-punch`).** `ON CONFLICT (user_id, attendance_date)` is gone. Optional `session` (1 default, or 2): updates that session's row (ordered by `punch_in`) or inserts it when missing (session 2 needs session 1); never merges sessions; supplying `punch_out` clears `auto_closed`; a second open session -> 409; bad session -> 422; session 2 without session 1 -> 400.
- **Savepoints.** The punch location inserts into `staff_location_logs` run inside `SAVEPOINT sp_punch_location`, so a failed insert no longer aborts the attendance transaction.

**Helper API for payroll / report code** (`core_functions/attendance_rules.py`, pure, no DB; import `from core_functions import attendance_rules as rules`):
`ist_today(now=None)`, `ist_date_of(dt)`, `ist_time_of(dt)`, `to_utc(dt)` (IST business date and clock of an instant); `session_hours(punch_in, punch_out, stored_hours, now)`, `total_hours(sessions, now)` (day total, 2 decimals; live for an open non-stale session, 0 for a stale one); `is_late(punch_time, start, end, grace)`, `late_by_minutes(...)`; `shift_span_minutes(start, end)` (wraps midnight), `is_half_day(worked_minutes, start, end)`; `classify_day(day, today, sessions, start, end, now, grace, is_leave, holiday_name, is_weekly_off)` and `build_month(...)` + `summarize_days(days, total_days)` (the v2 day types and counters; `sessions` = `{punch_in, punch_out, work_hours}` dicts); `auto_close_time(...)`, `is_stale_open(...)`, `stale_cutoff(now)`, `select_today_sessions(rows, today, now)`, constants `MAX_SESSIONS_PER_DAY = 2`, `STALE_OPEN_HOURS = 18`. DB helpers in `attendance_functions.py`: `shift_for(cursor, user_id, org_id, on_date)` (SWAP POINT for WP7's one-day shift override: today it returns the user's current shift matched on org; every caller already passes the date) and `org_grace_minutes(cursor, org_id)`. Payroll should count a day's attendance as the rows of `attendance_date` (IST date), count distinct dates (not rows) for present days, sum `work_hours` of closed rows, and skip `auto_closed` rows from overtime until reviewed. SQL for the IST date of a timestamp: `(ts AT TIME ZONE 'Asia/Kolkata')::date`; IST clock: `(ts AT TIME ZONE 'Asia/Kolkata')::time`.

## 4. Business logic

### 4.1 Scope resolution (`get_manager_scope`, `manager_functions.py:376-407`)
1. Look up `users.role_id,parent_id` for the supplied id. Unknown id or any exception -> `(id, None, None)` (no restriction).
2. Role 1 -> `(id, None, None)`. Role 3/other -> `(id, None, None)` (the staff id becomes the "org id" and every query returns empty).
3. Role 2 -> org = `parent_id`; branch ids and dept ids = distinct values from `manager_assignments`; empty -> `None` (= unrestricted). Branch and dept filters are applied independently (`branch IN (...) AND dept IN (...)`), i.e. the cross product of all assignments, not per-assignment pairs.
Applied to #1, #3, #5, #7-#12, #17. NOT applied to #2, #4, #6, #15, #16, #18-#22 (P1-17).

### 4.2 Dashboard counts (#1, `HF:117-226`)
- Date: `target_date` parsed with `%Y-%m-%d`; invalid/absent -> `datetime.now(IST).date()` (`:126-132`). Compared to `attendance.attendance_date`.
- `total_staff = COUNT(users) WHERE parent_id=org AND role_id IN (2,3) AND status=1` + branch (`branch_id != "0"`) or manager branches + manager depts (`:135-149`).
- Attendance query over `attendance JOIN users` for the date: `punched_in = COUNT(DISTINCT user WHERE punch_in NOT NULL AND punch_out NULL)`, `punched_out = COUNT(DISTINCT user WHERE both NOT NULL)`, `total_attendance = COUNT(DISTINCT user)` (`:152-160`). A user with one closed and one open session is counted in BOTH `punched_in` and `punched_out`.
- `working = punched_in` (`:211`); `absent = total_staff - total_attendance` (`:210`; can go negative if an inactive-then-reactivated or out-of-scope user has rows).
- Late: `COUNT(DISTINCT user)` over ANY session of the date whose `punch_in::time > shift.start_time`, users with `shift_id>0` and an active shift of the same org (`:178-188`). Not restricted to the first punch (a post-lunch second punch-in after shift start counts as late), no grace, compares the DB-session-time-zone time-of-day with the IST shift time (D-AH-04).

### 4.3 Department stats (#2, #6)
- #6 (`:389-471`): org = `user_id` as given; LEFT JOIN `dept_info -> users(active, role 2/3, parent_id=org) -> attendance(date)`; `absent = users with no attendance row`; `punched_in = working = open sessions`; `punched_out = closed sessions` (same double-count as 4.2). Late counted in a second query keyed by dept (`:426-452`). A `branch_id` filter is appended as `(u.branch_id = %s OR u.branch_id IS NULL)` on the LEFT-JOINed user, which drops departments whose staff are all in other branches.
- #2 (`:887-1009`): `LEFT JOIN users u ON d.dept_id = u.dept_id AND u.status=1` (no `parent_id`/role filter, so inactive-excluded but role-1 admins with that dept would count). Fails at return: `DepartmentSummaryResponse(departments=..., overall_status=...)` while the model field is `overall_Status` (`:102-104` vs `:1000-1003`) -> Pydantic "Field required" -> caught -> 500. Verified with Pydantic 2.x in a scratch script.

### 4.4 Staff lists
- #5/#7 (`:293-383`, `:473-552`): one row per user (`DISTINCT ON (u.user_id)`), `LEFT JOIN LATERAL` to the user's latest-punch-in attendance row with `attendance_date = CURRENT_DATE` (DB date, not IST). Status: inactive -> `"Inactive"`; row exists and both punches -> `"Punched Out"`; punch_in only -> `"Working"`; row w/o punch_in -> `"Punched In"` (unreachable in practice); no row -> `"Absent"`. Only the LATEST session decides (a user with session 1 closed and session 2 open shows `"Working"`; after session 2 closes shows `"Punched Out"`).
- #8 absent (`:554-619`): `LEFT JOIN attendance ... a.user_id IS NULL`, active only, branch/scope filters.
- #9 late (`:621-696`): `JOIN LATERAL` first punch of the date (`ORDER BY punch_in ASC LIMIT 1`) with `punch_in::time > s.start_time`; shift must exist and be active in the org. Count in #1 and list here can disagree (4.2).
- #10 punched-in (`:698-769`): `LATERAL` on open session only. #12 punched-out (`:776-847`): `LATERAL` on a closed session (latest). Lists #10 and #12 overlap for users with 1 closed + 1 open session. #11 = #10.
- `profile_photo_url` for each row: 1-2 S3 `head_object` + presign per row (`ml/s3.py:170-183`), sequential, so list latency grows linearly with staff count.

### 4.5 `/home/staff-detail` and `/home/staff-profile`
- #15 (`:1095-1149`): staff joined to dept/branch (org-scoped joins) and to TODAY's attendance via `CURRENT_DATE` (multiple session rows would multiply the row; `fetchone` takes an arbitrary one). Status as 4.4. Filter `u.status=1 AND u.parent_id=org`.
- #16 (`:1151-1228`): `LEFT JOIN users_additional_info`; `role_name` from a hard-coded map `{1:Admin,2:Manager,3:Staff}`; dates formatted `YYYY-MM-DD`; same `status=1` filter. `shift_id` returned raw.

### 4.6 `/home/daily-attendance` (#17, `AF:51-152`)
1. Date must parse `%Y-%m-%d` (else inner 400, converted to 500 by the outer handler).
2. Per user aggregate of that `attendance_date` (subquery `agg`): `first_punch_in = MIN(punch_in)`, `last_punch_out = MAX(punch_out)`, `total_work_hours = SUM(work_hours)`, first-session punch-in lng/lat via `array_agg(... ORDER BY punch_in)[1]`.
3. Users: `parent_id = resolved org AND role_id IN (2,3) AND status=1`, + branch (`"0"` = all) / dept (`"0"` = all) or manager scope. Branch/dept joins are NOT org-scoped (`LEFT JOIN branch_info b ON u.branch_id=b.branch_id`).
4. Status: `punch_in AND punch_out` -> `"Punched Out"` with `total_hours = str(Decimal)` (e.g. `"8.50"`; `None` if SUM is 0/NULL); `punch_in` only -> `"Working"`; else `"Absent"`. Because `MAX(punch_out)` ignores open sessions, a user whose 2nd session is still open shows `"Punched Out"`.
5. Times via `format_ist_time` (`%I:%M %p`, e.g. `09:05 AM`). `location = "lat,lng"` of the first punch-in.

### 4.7 `/home/monthly-attendance` (#18, `AF:154-255`)
1. Shift: `users_additional_info JOIN shift (status=1)` for `staff_id` -> `shift_start` (`time`); not org-checked; `fetchone` (`:160-170`).
2. Per day (grouped by `attendance_date`, filtered by `EXTRACT(MONTH/YEAR FROM attendance_date)` so the **UTC** date): `MIN(punch_in)`, `MAX(punch_out)`, `SUM(work_hours)`, first-session location, first-session in-photo, latest out-photo (`:172-187`).
3. Status: no punch_in -> `absent` (cannot happen: rows always have punch_in except admin-edited punch-out-only rows); `punch_in.time() > shift_start` -> `late`, `late_days++`; else `present`; `present_days++` for both. `.time()` is taken on the tz-aware datetime in the DB session zone (not IST) (`:199`). No grace period, no overnight handling, no attendance rule (P0-4 absent). `half_day`/`leave` never emitted.
4. `total_hours = str(SUM(work_hours))` (Decimal string, 2 decimals, `None` when 0/NULL; open sessions contribute nothing).
5. Photos pass through `refresh_punch_image_url` (`ml/s3.py:401-423`): one S3 `head_object` + presign per photo per day (up to ~62 HEADs per request).
6. `absent_days = days_in_month - number_of_rows` (`:228-229`): counts future days, weekly offs and holidays as absent. `total_days = days in month`.
7. Whole function wrapped: any error (including `month` outside 1-12) returns an empty month with `total_days=0` and HTTP 200 (`:241-250`).
Missing fields the new frontend reads: `shift_start_time`, `shift_end_time` per day (`src/features/employee/utils/attendanceCalendar.ts:53-54`, `src/features/reports/utils/lateRule.ts:45-46`, `src/features/staff-directory/utils/attendanceDerive.ts:98`).

### 4.8 `PUT /home/update-attendance-punch` (#22, `AF:270-337`)
1. Parse `date` (`%Y-%m-%d`; failure -> generic 500).
2. `SELECT punch_in,punch_out FROM attendance WHERE user_id AND attendance_date` -> `record` (`fetchone`, arbitrary row if several sessions exist).
3. No row and neither time supplied -> 400.
4. `_parse_time` (`:257-268`): accepts `HH:MM`, `hh:mm AM/PM`; interprets as **IST** on the given date and converts to UTC; invalid -> 422.
5. Missing field keeps the existing value (a punch cannot be cleared).
6. Overnight fix: if `new_punch_out < new_punch_in` add 1 day (`:295-296`) — so a typo that puts out before in silently becomes a next-day punch.
7. `work_hours = round((out-in)/3600, 2)` when both present, else NULL.
8. `org_id` = body value or `users.parent_id` (`:302-308`); 400 if unresolved.
9. `INSERT ... ON CONFLICT (user_id, attendance_date) DO UPDATE SET punch_in,punch_out,work_hours,updated_at` (`:314-321`). Locations, photos and `face_mismatch` are untouched/NULL for new rows. Requires the unique index; with multiple sessions per day (index dropped) the statement raises "no unique or exclusion constraint matching the ON CONFLICT specification" -> 500.
10. Commit; response message "created" vs "updated" depends on step 2.
Not protected by any admin/role check or by the plan; no audit trail (who edited is not stored).

### 4.9 `GET /attendance/today/{user_id}` (#23, `FAF:388-449`)
1. `today = get_utc_now().date()` (UTC date).
2. Rows for `(user_id, attendance_date=today)` ordered by `punch_in`.
3. Empty -> `{success:true,punch_in:null,punch_out:null,is_punched_in:false,sessions:[],total_work_hours:0}`.
4. `sessions[i] = {punch_in "%I:%M:%S %p" IST, punch_out, work_hours float|null}` (`work_hours` of 0 becomes null); `is_punched_in` = any row with `punch_out IS NULL`; `punch_in` = first row's punch-in; `punch_out` = **last non-null** punch-out even when a later session is open; `total_work_hours = round(sum of closed work_hours, 2)` (open session excluded).
5. After UTC midnight (05:30 IST) an open overnight session (attendance_date = previous UTC date) is invisible: the day looks empty/not punched in although `punch_in` would answer "Already punched in" and `punch_out` would work (they look up open sessions without a date).

### 4.10 `GET /attendance/sessions/{user_id}` (#24)
Rows for `(user_id, attendance_date = ?date)` ordered by `punch_in`; times formatted with `strftime("%I:%M:%S %p")` directly on the DB datetimes (**no IST conversion**; an unused `pytz` Asia/Kolkata object is created at `:75`); photos refreshed via S3 HEAD. Result times are therefore in the DB session zone (UTC unless configured), unlike `/attendance/today`.

### 4.11 Time-zone / "which day" matrix
| Feature | Day basis | File:line |
|---|---|---|
| Punch-in `attendance_date` | UTC date | `face_attendance_functions.py:180-181` |
| 2-sessions-per-day count | UTC date | `:204-209` |
| `/attendance/today` | UTC date | `:394` |
| `/home/dashboard-status`, `department-*`, absent/late/punched-* lists, daily-attendance | caller date, default IST today | `home_functions.py:126-132` etc. |
| `/home/staff-list`, `/all-staff`, `/staff-detail` | DB `CURRENT_DATE` | `:318,497,1111` |
| `/home/staff-locations`, `/staff-punch-locations` | IST default; log date filter `DATE(date_time)` on UTC-naive timestamps | `:1239-1257,1315-1338` |
| `/staff-location/*` | UTC date | `staff_location_functions.py:56,106,184-186,262` |
| Holiday block on punch-in | DB `CURRENT_DATE` | `holiday_functions.py:160` |
| Late test | DB-session time-of-day vs IST shift time | `HF:187`, `AF:199` |
| `edit_attendance` | HH:MM read as IST; `attendance_date` as typed | `AF:263-265` |
Consequence: a punch between 00:00 and 05:30 IST is stored on the previous date; dashboards queried with the IST date will not see it (and see it under yesterday).

## 5. Side effects & integrations
- S3 (read): `get_employee_profile_url` (`employee_profile/{user_id}.jpg|png`, presigned 7 days) per list row; `refresh_punch_image_url` per punch photo (monthly, sessions). Requires `AWS_*`, `S3_BUCKET_NAME`.
- No push/e-mail/WebSocket/FCM from the read endpoints. `edit_attendance` sends no notification and writes no audit row.
- DB only otherwise; connections from a `SimpleConnectionPool(5,20)` (`db_function.py:40`) and every route is `async def` calling blocking psycopg2/boto3 (blocks the event loop under load).

## 6. Config & env vars
`DB_SCHEMA` (required, validated), `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT` (default 5432) — `core_functions/db_function.py:24-34`; `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME` — `ml/s3.py:10-14`. Hard-coded constants (no env): max 2 sessions/day (`face_attendance_functions.py:208`), default punch radius 100 m (SQL `COALESCE`, `HF:246`), presign TTL 604800 s, default time zone IST (`utils/timezone.py:14`). DB session `TimeZone` is not set anywhere (UNVERIFIED what the deployed server uses; it decides correctness of `::time`, `CURRENT_DATE`, `TO_CHAR`).

## 7. Known defects & risks

| ID | Sev | Where | Defect / impact |
|---|---|---|---|
| D-AH-01 | High | `HF:102-104,1000-1003` | `GET /home/department-status` always fails with 500 for a non-empty `org_id` (`overall_status=` vs field `overall_Status`). Frontend does not call it (uses `/home/department-stats`) but it is a mounted route. |
| D-AH-02 | High | dump:150 vs `FAF:203-209`, `AF:314-318` | Schema dump has a UNIQUE index on `(user_id, attendance_date)`. With it, the 2nd punch-in of a day fails (`Database error: duplicate key...` returned as `success:false`) so the "2 sessions/day" feature cannot work; without it, `edit_attendance`'s `ON CONFLICT` target does not exist and the admin edit fails (500). The code base assumes both. Which one is deployed is UNVERIFIED (dump is older than code; `migrations/` has only a README). |
| D-AH-03 | High | `FAF:264` | `img_url` NameError on staff/manager punch-in (see `face_recognition_punch.md` D-FP-01): the monthly/daily views will show the punch although the client was told it failed. |
| D-AH-04 | High | `HF:187,438,658,941`, `AF:199` | "Late" = DB-session time-of-day `>` IST shift start. `punch_in::time` (timestamptz cast) and `datetime.time()` use the DB session zone, not IST; the code never sets the zone. If the server zone is UTC a 09:00 IST shift is flagged late only after 14:30 IST. Also no grace period, no overnight-shift handling, and the dashboard count uses ANY session (2nd session after start = late) while the list uses the first. |
| D-AH-05 | High | `FAF:180-181,394`, `AF:154-187` | `attendance_date` is the UTC date: IST punches before 05:30 land on the previous day; `/attendance/today` is empty/previous day in that window and misses an open overnight session after 05:30 IST; dashboards (IST) and lists (`CURRENT_DATE`) disagree (P2-5). |
| D-AH-06 | Med | `HF:152-160,203-210,404-407` | `punched_in` and `punched_out` both include a user with one closed + one open session; lists #10 and #12 overlap; `absent` can be miscounted; `working == punched_in` always (DD-63). |
| D-AH-07 | Med | `AF:114-122` | daily-attendance status uses `MAX(punch_out)`: user in a 2nd open session is shown `"Punched Out"`. |
| D-AH-08 | Med | `AF:228-229` | `absent_days = days_in_month - rows` counts future days, weekly offs, holidays and leave as absent; `half_day`/`leave` statuses never produced; `present_days` includes late. |
| D-AH-09 | Med | `AF:241-250` | `monthly-attendance` swallows every error into an empty month (HTTP 200) and `month` out of 1-12 silently returns `total_days=0`. |
| D-AH-10 | Med | `HF:1112,1145,1172,1224` | staff-detail/profile find only `status=1` staff; 404 is re-raised as 500 `... 404: Staff member not found`. |
| D-AH-11 | Med | `HF:587,664,737,815,1044` | `branch_id != 0` compares a string to an int; `branch_id="0"` ("All") is not handled in absent/late/punched-in/punched-out (and `/home/staff-list`, `:326`) and returns an empty list; only dashboard-status, department-status and daily-attendance handle `"0"`. |
| D-AH-12 | Med | `HF:771-774` | `/home/working-staff` is an alias of punched-in (status text `"Punched In"`, never `"Working"`); while `/home/all-staff` and `/home/staff-list` label an open session `"Working"` and never `"Punched In"`: inconsistent vocab. |
| D-AH-13 | Med | `AF:63-66,148-149` | invalid `target_date` on daily-attendance returns 500 `Failed to get daily attendance` instead of 400. |
| D-AH-14 | Med | `router/face_attendance.py:75-88` | `/attendance/sessions` returns times un-converted (not IST) and answers HTTP 200 `{success:false}` for any error, including a malformed `date`. |
| D-AH-15 | Med | `HF:1111`, `AF:279-283` | status/detail/edit joins `attendance` by date only and take `fetchone`/multiplied rows: with 2 sessions the choice of session is arbitrary. |
| D-AH-16 | Med | `HF:313`, `AF:77`, `face_attendance_functions.py:157` | `branch_info` joined on `branch_id` only (no `org_id`); `branch_id` is a per-org counter in staff creation (`add_staff_function.py:342,545`) and global in branch creation (`branch_functions.py:83`), so a user can match another org's branch (wrong name, wrong geofence). |
| D-AH-17 | Med | `ml/s3.py:170-183` and every list | per-row S3 HEAD (up to 2) + presign: O(n) sequential network calls in `/all-staff`, `/staff-list`, all live-status lists, `/org-admin`. |
| D-AH-18 | Med | `HF:1161-1167` | `/home/staff-profile` returns government ids and bank details without auth (P3-6). |
| D-AH-19 | Med | `AF:288-296` | `edit_attendance` has no auth/role check, no audit, cannot clear a punch, silently converts out-before-in into next-day punch; admin edit leaves `punch_out` NULL for punch-in-only edits which then blocks the user's next punch-in until they punch out. |
| D-AH-20 | Low | `HF:393-396,416-417` | `/home/department-stats` ignores manager scope (manager id as org id returns empty/zero depts) and does not validate `target_date`. |
| D-AH-21 | Low | `home.py:174-208` | `/home/org-admin/{id}` returns a JSON `null` body with 200 when no admin is found; `status=1` on the caller only. |
| D-AH-22 | Low | `HF:1295-1297,1359-1362` | falsy test on coordinates: `0.0` latitude/longitude become `null`. |

## 8. Clockyn requirements

Cross-reference: `backend_changes.md` and `DEFERRED_DECISIONS.md`. Evidence tags: [V] verified here.

### R-1 `GET /home/monthly-attendance` shift fields, day types, errors — P1-3, P2-5, DD-07, DD-60, DD-61, DD-67 [V]
- **Current:** no `shift_start_time`/`shift_end_time`; `late` computed in DB-session zone (D-AH-04); `absent_days` includes future/off days; swallow-all error handler; `total_hours` Decimal string.
- **Required (additive, keep existing keys):** per day add `shift_start_time`, `shift_end_time` (24h `"HH:MM"`, IST wall time, `null` when no shift), `late_by_minutes` (int or null), `type` in `working|holiday|weekly_off|leave`; keep `status` in `present|late|absent|half_day|leave`; return `total_hours` as a JSON number with 2 decimals (frontend `optNum` accepts both string and number, `src/api/mappers/common.ts:143`); `absent_days` = past working days without a record; real HTTP status on error (400 invalid month/year, 404 staff not found).
- **Outline:** in `attendance_functions.py:get_monthly_attendance` (a) resolve the shift once with org scope (`JOIN shift s ON s.shift_id = uai.shift_id AND s.org_id = u.parent_id AND s.status=1`, handle `shift_id` NULL/0); (b) compute lateness in Python with `convert_utc_to_ist(first_punch_in).time()` vs `s.start_time` (never the DB session zone); (c) overnight shift (`end_time <= start_time`): measure minutes since `start_time` modulo 24h and only flag late when 0 < diff < 12h; (d) optional grace/rule from the future attendance-rule table (P0-4) — until it exists keep "strictly after start"; (e) day types from `org_holidays` (`holiday_functions.get_holidays`, off days via `get_off_days`) and approved leave (requests domain); (f) remove the blanket `except Exception` or re-raise `HTTPException`; validate `1<=month<=12`.
- **Back-compat:** all new keys optional; existing `status` values unchanged. Frontend currently infers missing data and will switch to server values when present.
- **Acceptance:** shift 09:00-18:00, punch-in 09:00 IST (03:30 UTC) -> `present`, `late_by_minutes` null; 09:10 IST -> `late`, 10; the same result under DB zones UTC and IST; no shift -> both shift fields null and never `late`; Feb 2026 returns `total_days` 28; a future day is not counted absent; month 13 -> 400; unknown staff -> 404.

### R-2 Live-status lists: `role_id`, `is_active`, photo, consistent labels — P1-10, DD-63, DD-62 [V!]
- **Current:** #8-#12 never fill `role_id` (null); `is_active` default true (accurate); photo is filled (S3 lookup, null if none) — the backend_changes `[L]` claim "no photo" is wrong for these routes; `working-staff` == `punched-in-staff`.
- **Required:** select and return `u.role_id`; set `is_active` explicitly; keep photo; define `working` = open session and `punched_in` = has any punch (open or closed) or return the same set with a documented alias; fix `branch_id="0"` = all in #5, #8-#12.
- **Outline:** `home_functions.py`: add `u.role_id` to the five SELECTs and pass `role_id=staff['role_id'], is_active=True`; replace `branch_id and branch_id != 0` by `branch_id and branch_id != "0"`; make the photo lookup batched (one S3 `list_objects_v2` on prefix `employee_profile/` or cache presigned URLs per user for the TTL).
- **Back-compat:** additive fields only.
- **Acceptance:** `/home/punched-in-staff?...` rows have non-null `role_id` (2 or 3); `branch_id=0` returns the same rows as omitting it; 200-staff org list responds without per-row S3 HEAD (assert call count).

### R-3 Staff detail/profile for inactive staff and `branch_id` — P1-2, P1-10 [V]
- **Current:** `status=1` only; 404 returned as 500; no `branch_id`.
- **Required:** add `branch_id`, `status`/`is_active`, optional `include_inactive=true` (default for admin screens true); real 404; mask or restrict PII fields per role (P3-6).
- **Outline:** `get_staff_detail`/`get_staff_profile`: select `u.branch_id, u.status`; drop `AND u.status = 1` or gate by `include_inactive`; add `except HTTPException: raise` before `except Exception` (`:1145,:1225`); add `branch_id`, `is_active` to `StaffDetailResponse`/`StaffProfileResponse`.
- **Back-compat:** new keys optional; default behaviour for active staff unchanged.
- **Acceptance:** inactive staff id returns 200 with `is_active:false` (or 404 JSON status 404, never 500); active staff includes `branch_id`.

### R-4 IST business dates — P2-5, DD-05 [V]
- **Current:** see 4.11 and D-AH-05.
- **Required:** one rule: `attendance_date` = IST date of the session's punch-in; every "today" = IST; all returned timestamps either IST clock strings or ISO with offset.
- **Outline:** `face_attendance_functions.py:180-181`: `today = convert_utc_to_ist(now).date()`; `get_today_attendance` (`:394`): `today_ist` and ALSO return any open session regardless of date (`WHERE user_id=%s AND (attendance_date=%s OR punch_out IS NULL)`); `/attendance/sessions` (`face_attendance.py:78-79`): use `format_ist_time`; replace `CURRENT_DATE` in `HF:318,497,1111` and `holiday_functions.py:160` by a bound IST date parameter; migration for history: `UPDATE attendance SET attendance_date = (punch_in AT TIME ZONE 'Asia/Kolkata')::date WHERE punch_in IS NOT NULL AND attendance_date <> (punch_in AT TIME ZONE 'Asia/Kolkata')::date;` (run a duplicate/session-count check first and take a table backup; admin-edited rows are already IST-dated and are unaffected).
- **Back-compat:** one-off data shift of dates for early-morning punches; deploy code and migration together; frontend already sends IST dates (`backend_changes.md` P2-5).
- **Acceptance:** punch-in at 02:00 IST on 19 Oct stores `attendance_date = 2026-10-19`; `/attendance/today` at 02:00 IST returns that session; the dashboard for 19 Oct counts the staff; overnight session opened 20:00 IST still returned as `is_punched_in:true` at 08:00 IST next day.

### R-5 `/attendance/today` contract and 2-session rule — DD-04, DD-05 [V]
- **Current:** server enforces max 2 sessions per UTC date (`FAF:204-209`, under advisory lock `:187-190`) — verified (DD-04 resolved); `punch_out` field returns the last non-null punch-out even while a session is open; `total_work_hours` excludes the open session; open overnight session lost after UTC midnight.
- **Required:** document and guarantee: `sessions[]` (ordered), `is_punched_in` (any open session, any date), `punch_in` = first, `punch_out` = **null while a session is open**, `total_work_hours` = closed sessions only (add `live_work_hours` optional).
- **Outline:** `get_today_attendance`: set `punch_out = None if active_session else last_punch_out`; add `active_session_started_at` (ISO with offset) for client timers; keep `work_hours` numeric.
- **Back-compat:** clients that read `punch_out` while open receive null (frontend already treats `is_punched_in` as authority, `stillWorking.ts`).
- **Acceptance:** session1 closed + session2 open -> `is_punched_in:true`, `punch_out:null`, `sessions.length==2`; two closed -> third punch-in answers `Maximum 2 punch-in sessions allowed per day.`

### R-6 Manager scoping on `/home/*` — P1-17, DD-50 [V]
- **Current:** applied to #1,#3,#5,#7-#12,#17 only; #4, #6, #15, #16, #18-#22 ignore it; "scope id" convention (manager id sent where org id expected).
- **Required:** apply `get_manager_scope` to every route in this file; ultimately derive identity from a token (P3-1).
- **Outline:** wrap #6 (`get_department_stats`), #15/#16 (`AND u.branch_id = ANY(%s) AND u.dept_id = ANY(%s)` when scope present), #18, #19, #20, #22 (verify the target staff is in scope before editing).
- **Acceptance:** manager with branch A only gets 404/403 for a staff member of branch B on detail/profile/edit; dashboard counts equal list lengths for the manager.

### R-7 Attendance edit — DD-67, P1-3 [V]
- **Current:** upsert on `(user_id, attendance_date)`; cannot clear a punch; no audit; no auth.
- **Required:** target a specific session (`attendance_id` optional; default first), allow `clear_punch_in/clear_punch_out` flags, store editor id and reason, keep overnight rule explicit (`punch_out_next_day` flag instead of silent +1 day), reject edits outside the caller's scope.
- **Outline:** replace `ON CONFLICT` by explicit `UPDATE ... WHERE id=%s` / `INSERT`; add `edited_by`, `edit_reason`, `edited_at` columns (migration); partial unique index `CREATE UNIQUE INDEX uq_attendance_open ON attendance(user_id) WHERE punch_out IS NULL`; drop the full unique index (D-AH-02).
- **Back-compat:** request body fields optional; response unchanged plus `attendance_id`.
- **Acceptance:** editing session 2 does not alter session 1; clearing punch-out reopens the session; two open sessions are impossible (DB error -> 409).

### R-8 Error semantics and envelopes — P2-1, P2-6 [V]
- **Current:** inner `HTTPException` wrapped as 500 in #15/#16/#17; `monthly` HTTP 200 empty; `/attendance/sessions` HTTP 200 `{success:false}`; lists are bare arrays.
- **Required:** `except HTTPException: raise` in every handler of this file; real statuses; keep bare-array lists (frontend mappers are tolerant).
- **Acceptance:** invalid `target_date` -> 400; unknown staff -> 404 with `{detail}`.

### R-9 Bulk/N+1 endpoints — P2-9 [V]
- **Current:** one monthly request per staff; per-row S3 HEAD in lists.
- **Required:** optional bulk endpoint `GET /home/monthly-attendance-bulk?org_id&month&year&branch_id&dept_id` returning per-staff summaries; photo URL batching/caching.
- **Acceptance:** 100-staff report in one call, p95 < 2 s with S3 mocked.

## 9. Test notes
- No automated tests exist in the backend (no `test_*.py`, no `conftest.py`). Use `pytest` + a throw-away Postgres created from the schema dump; run once WITH and once WITHOUT the unique index on `attendance` to expose D-AH-02. Set the Postgres session `TimeZone` to both `UTC` and `Asia/Kolkata` to expose D-AH-04/05.
- Fixtures: org admin (role 1) + manager (role 2) with `manager_assignments` (branch A, dept X) + staff (role 3) in branches A and B, one field staff, a shift 09:00-18:00 and an overnight shift 20:00-08:00, one inactive staff, branch rows with GPS and `punch_radius`, S3 mocked (`moto` or monkeypatch `ml.s3.s3`).
- Critical cases: (1) dashboard counts vs lists for staff with 0/1/2 sessions (closed+open); (2) late at 09:00/09:10 IST under both DB zones; (3) punch at 02:00 IST; (4) overnight session after UTC midnight; (5) `/home/department-status` (expect 200 after fix); (6) manager scoping on every route; (7) `branch_id=0`; (8) monthly with invalid month, future days, holidays; (9) `edit_attendance` create/update/overnight/multi-session; (10) inactive staff on detail/profile.
- The frontend mock server `C:\POC\pa_frontend\Clockyn\e2e\mock-server\routes\home.js` and `attendance.js` is the executable response-shape reference (`home.js:112-142` for monthly).
