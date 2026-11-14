# Requests & approvals (leave / permission / shift change / device change) — backend context

Domain owner: BD4. Source snapshot: `C:\POC\pa_backend\Clockyn` (line numbers verified against the files as read for this document; they differ by a few lines from the numbers quoted in the frontend `backend_changes.md`, which was written against an earlier snapshot).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/requests.py` | 159 | 9 endpoints under prefix `/requests` (`router/requests.py:16`); mounted at `app.py:52` |
| `core_functions/requests_functions.py` | 598 | all logic: submit x4, employee list, admin list, update-status, calendar, device-session check, push/WS helpers |
| `core_functions/auth_function.py:878-884` | n/a | caller of `submit_device_change_request` (auto-submit on device mismatch at login) |
| `core_functions/gen_plans_functions.py` | n/a | not used by this domain (no plan gate on any request endpoint) |
| `router/ws_badge.py:10-18` | n/a | `notify_badge_update` used for the `request_submitted` WebSocket event |
| `core_functions/auth_function.py:164-208` | n/a | `send_push_notification` (FCM v1) used for every push |

Frontend consumer: `src/api/requests.ts` (+ `src/api/mappers/requests.ts`, rules in `src/features/requests/staff/utils/requestRules.ts`). Contract note: `C:\POC\pa_frontend\project_complete_context\shared\api\requests.md`. The mock server `e2e/mock-server/routes/requests.js` is the executable shape reference.

Callers of `update_request_status` side effects elsewhere: `delete_user_account` / permanent staff delete remove a user's `requests` + `leave_dates` rows (`core_functions/add_staff_function.py:739-742`, `core_functions/company_functions.py:433-434`). Nothing else in the backend reads `requests` / `leave_dates` (attendance, salary and reports do **not** consult approved leave: grep of `core_functions/` finds no other reader). Approved leave therefore has no effect on attendance status or payroll.

## 2. Data model touched

Schema dump references: `requests` (dump line 891), `leave_dates` (653), `shift` (934), `users` (≈990), `org_info`, `manager_assignments` (≈670). The schema dump is older than the code (see §7 D-18).

### `requests`
| Column | Type | Meaning / rule |
|---|---|---|
| `id` | int identity PK | internal |
| `user_id`, `org_id` | varchar(36) NOT NULL | requester and org (client supplied, never validated) |
| `request_id` | varchar(36) NULL, **non-unique** index | uuid4 string; the external key. No UNIQUE constraint |
| `request_type` | `request_type_enum` NOT NULL | values written: `leave`, `permission`, `shift_change`, `device_change`. Enum definition is not in the dump (UNVERIFIED: full value list) |
| `reason` | text | free text, unbounded |
| `permission_start_time`, `permission_end_time` | time | only permission rows |
| `current_shift_id`, `requested_shift_id` | int | only shift_change rows; current is snapshotted from `users_additional_info.shift_id` at submit (`requests_functions.py:176-181`) |
| `shift_change_date` | date | only shift_change |
| `status` | varchar(20) default `'pending'` | **NULL for leave** (explicit NULL insert, `requests_functions.py:214`); `pending/approved/rejected` for the other three |
| `action_by`, `action_at` | varchar(36), timestamp | last decider (UTC naive) |
| `created_at`, `updated_at` | timestamp default `CURRENT_TIMESTAMP` | DB clock, not `get_utc_now()` (mixed sources) |
| `new_device_token` | varchar(255) | exists in dump, never read or written by this code |
Indexes: `user_id`, `org_id`, `request_id`, `status`, `request_type`. No soft delete; rows are hard-deleted only by user/company deletion.

### `leave_dates`
| Column | Type | Meaning |
|---|---|---|
| `request_id` | varchar(36) | link to `requests.request_id` (no FK) |
| `leave_date` | date NOT NULL | one row per leave date, **and** the single date of a permission request (`requests_functions.py:255-258`) |
| `status` | varchar(20) default `'pending'` | per-date decision for leave and permission |
| `action_by`, `action_at` | varchar(36), timestamp | per-date decider |
| `created_at`, `updated_at` | timestamp | defaults |
No UNIQUE (`request_id`,`leave_date`): duplicate dates within or across requests are accepted by the DB. Indexes on `leave_date`, `request_id`.

### Other tables read
| Table | Columns used | Where |
|---|---|---|
| `users` | `user_id, name, role_id, parent_id, push_token, device_token, status, branch_id, dept_id` | push targeting, admin scope, device session |
| `org_info` | `org_id, parent_id` (owner user id) | admin recipients and device-change org lookup |
| `manager_assignments` | `user_id, branch_id, dept_id` | manager scoping in admin list (`requests_functions.py:340`) |
| `shift` | `shift_id, start_time, end_time` | `requested_shift_timing` / `current_shift_timing` text |
| `users_additional_info` | `shift_id` | `current_shift_id` snapshot |

Status vocabulary: request/date `pending | approved | rejected`; `update-status` also accepts `pending` (reverting a decision). Device-session `reason`: `user_not_found`, `account_inactive`, `device_change_approved`, `logged_in_on_another_device`.

## 3. Endpoints

All handlers are `async def` that call **synchronous** DB code (blocks the event loop) and wrap everything in `except Exception -> HTTPException(500, str(e))` (`router/requests.py:62-63` and the same pattern in every handler). Pydantic validation failures stay HTTP 422; unexpected DB errors become HTTP 500 with the raw message; business failures (unknown request, invalid status, ...) return **HTTP 200 `{success:false,message}`**. No authentication, no plan gate. Identity is the id in the body/path.

| METHOD path | Handler | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| POST `/requests/device-change/submit` | `submit_device_change` (`router/requests.py:57`) -> `submit_device_change_request` (`requests_functions.py:129`) | body `user_id`, `org_id` (unverified) | JSON `{user_id:str, org_id:str, reason:str}` (all required) | `{success:true, request_id, message:"Device change request submitted successfully"}` | `org_id==""` triggers lookup; unresolved -> 200 `{success:false,message:"Could not determine organization"}` (`:147-148`). **Unauthenticated, no dedupe, no rate limit** |
| POST `/requests/shift-change/submit` | `router/requests.py:66` -> `requests_functions.py:170` | body | `{user_id, org_id, reason, requested_shift_id:int, shift_change_date:str(YYYY-MM-DD)}` | `{success:true, request_id, message:"Shift change request submitted successfully"}` | bad date -> DB error -> 500 with raw message |
| POST `/requests/leave/submit` | `router/requests.py:78` -> `requests_functions.py:204` | body | `{user_id, org_id, leave_dates:str[], reason}` | `{success:true, request_id, message:"Leave request submitted successfully"}` | empty `leave_dates` accepted (request with zero dates); bad date -> 500 |
| POST `/requests/permission/submit` | `router/requests.py:92` -> `requests_functions.py:241` | body | `{user_id, org_id, permission_date, start_time, end_time, reason}` (strings) | `{success:true, request_id, message:"Permission request submitted successfully"}` | times cast by Postgres (`'09:30'` ok); malformed -> 500 |
| GET `/requests/employee/{user_id}/{org_id}` | `router/requests.py:108` -> `get_employee_requests` (`:276`) | path | none | `{success:true, data:[{request_id, request_type, reason, permission_start_time, permission_end_time, status, action_by, action_by_name, action_at, created_at, shift_change_date, requested_shift_timing, dates:[ISO]}]}` newest first | **No `date_statuses`, no `requested_shift_id`/`current_shift_id`/`current_shift_timing`**; leave `status` is JSON `null`. Anyone can read anyone's requests |
| GET `/requests/admin/{org_id}` | `router/requests.py:117` -> `get_admin_requests` (`:329`) | path id is a **user id first** (role 2 -> manager scope) else org id | optional `?status=` | `{success:true, data:[{...employee fields..., user_id, staff_name, requested_shift_id, current_shift_id, requested_shift_timing, current_shift_timing, dates:[ISO], date_statuses:{ISO:{status,action_by,action_at}}}]}` | `status` filter matches `requests.status` only, so leave (NULL) never matches. No `emp_id`, `profile_photo`. No pagination. N+1 query per request |
| POST `/requests/update-status` | `router/requests.py:126` -> `update_request_status` (`:428`) | body `action_by`, `role_id` (**ignored**) | `{request_id:str, status:str, action_by:str, role_id:int, leave_date?:str}` | `{success:true, message:"Request <status> successfully"}` | invalid status / unknown request / unknown date -> 200 `{success:false,...}` |
| GET `/requests/calendar/{user_id}/{org_id}` | `router/requests.py:141` -> `get_employee_calendar_data` (`:574`) | path | query `start_date`, `end_date` (required strings) | `{success:true, data:[{date, status, type}]}` ordered by date | only leave+permission dates; **no holidays / weekly offs**; rejected dates included; end date inclusive |
| GET `/requests/check-session/{user_id}` | `router/requests.py:150` -> `check_device_session` (`:537`) | path | query `device_token` (required) | `{valid:true, force_logout:false}` or `{valid:false, force_logout:true, reason}` | **bare object, no `success`** |

Endpoint count: 9. There is **no pending-counts endpoint**; counts are computed client-side from the admin list (`src/api/requests.ts` `getPendingCounts`).

### 3a. Behaviour after WP7 (supersedes the "Errors / quirks" column above where they differ)

Errors are real HTTP 4xx with a plain `{detail}` string (the app already shows it); unexpected failures are `500 {"detail":"Internal server error"}` (the raw error is only logged). Messages are the ones of the contract mock `e2e/mock-server/routes/requests.js`. All dates are compared with the **IST** date.

| Endpoint | Rules and responses |
|---|---|
| POST `/requests/leave/submit` | 404 `User not found`; 400 `User does not belong to this organization`; 400 `Select at least one leave date`; 400 `Reason is required` (max 500 chars); 400 `Invalid leave date`; 400 `Leave dates cannot be in the past` (today is allowed); 400 beyond 366 dates; **409 `A leave request already exists for one of the selected dates`** when a date is already on another **pending or approved** leave of the same user (rejected dates may be requested again). Dates are de-duplicated and sorted; the request starts as `status='pending'` (no longer NULL). Per-user advisory lock serialises concurrent submits. |
| POST `/requests/permission/submit` | 400 `permission_date, start_time and end_time are required` (also for an unreadable time; `HH:mm`, `HH:mm:ss`, `9:05 AM` all read); 400 `End time must be after start time`; 400 `End time cannot be more than 6 hours after start time` (360 min passes, 361 fails); 400 `Reason is required`; 400 `Permission date cannot be in the past`. One day only (both times belong to the date). |
| POST `/requests/shift-change/submit` | 400 `Requested shift not found` (not an **active shift of this organisation**); 409 `You are already on this shift`; 409 `A shift change request already exists for this date` (pending one); 400 `Reason is required`, `Shift change date cannot be in the past`, `Invalid shift change date`. |
| POST `/requests/device-change/submit` | One pending request per user: a repeat returns `{success:true, request_id:<existing>, already_pending:true, message:"A device change request is already pending"}` (no row, no push). **429 `Too many device change requests. Please try again later.`** after 3 requests in 24 hours (any status). 404 unknown user, 400 user not in `org_id`; empty `org_id` is resolved from the user. New response key `already_pending` (additive). |
| GET `/requests/employee/{user_id}/{org_id}` | Adds `user_id`, and for shift-change rows `effective_shift_id` / `effective_shift_timing` (the shift that applies on `shift_change_date` now, via `shift_for`). Leave/permission `status` is derived from the per-date decisions. `date_statuses`, shift ids and timings as before. One query for all dates (no N+1). Shift joins are org scoped. |
| GET `/requests/admin/{org_id}` | Same rows plus `emp_id`, `profile_photo` (best effort, at most 50 storage lookups per call), `effective_shift_*`. `?status=` now matches leave rows (their status is stored). |
| POST `/requests/update-status` | 400 `Invalid status`; 200 `{success:false, message:"Request not found"}`; 400 `Invalid date`; 200 `{success:false, message:"Date not part of this request"}` (date not on the request, or a date sent for a shift/device request); **404 `Request has no dates`** for a leave/permission without date rows. With `leave_date` that date is decided, without it every date; the request status is recomputed (`pending` / `approved` / `rejected`, same rule as the app). **Actor check:** `action_by` must be an active admin of the request's organisation, or a manager of it who holds `Requests Management` and whose assignments cover the requester's branch/department. `REQUESTS_ENFORCE_ACTOR` off (default): a failure is logged as `actor_check_failed request_id=.. action_by=.. reason=.. enforced=False` at WARNING and the decision proceeds. On: **403 `Not allowed to decide this request`**, or **404 `Request not found`** for another organisation's request (nothing is written). Approving a device change the first time clears `device_token`, `device_token_expires_at`, `push_token` and the multi-device push tokens; a repeated approval and a rejection leave the tokens. Approving a shift change is a one-day override (see below). |
| GET `/requests/calendar/{user_id}/{org_id}` | `data` is unchanged (leave/permission dates, all statuses). Additive top-level keys: `holidays` `[{date,name,block_attendance}]` (org holidays of the employee's branch/department, same scope rule as `check_holiday_block`), `weekly_offs` `[date]` and `off_days` `[0..6]` (org weekly offs, default `[6]`), `shift_overrides` `[{date,shift_id,shift_timing}]` (approved one-day shift changes). Optional `?include_off_days=true` also appends `{date,status:"approved",type:"holiday"|"weekly_off",name?}` rows to `data` (the app maps any unknown `type` to leave, so the default stays off). 400 for an invalid or reversed range or one wider than 400 days. |
| GET `/requests/check-session/{user_id}` | Unchanged; with `ENFORCE_SINGLE_DEVICE` on, an admin (role 1) is never signed out by a token mismatch. |

**Shift change = one-day override (D-6).** `core_functions/shift_resolution.py`: `shift_for(user_id, org_id, on_date)` / `shift_for_cursor(cursor, user_id, org_id, on_date)` return the shift that applies on that date: the latest **approved** `shift_change` request with `shift_change_date = on_date` (its `requested_shift_id`, matched on the organisation, active shifts only), else the assigned shift (`users_additional_info.shift_id`, org matched). Nothing is written to `users_additional_info`. Integration point for lateness/overtime/reports: replace the body of `attendance_functions.shift_for(cursor, user_id, org_id, on_date)` with `return shift_resolution.shift_for_cursor(cursor, user_id, org_id, on_date)` (same result keys `start_time`, `end_time`; plus `shift_id`, `source`); set-based SQL can use `shift_resolution.effective_shift_id_sql(user_id_expr, org_expr, date_expr, assigned_expr)` in place of `uai.shift_id`.

**Leave approval (D-7)** only changes the attendance day type (approved dates are read by the monthly attendance v2); no salary or balance effect.

## 4. Business logic

### Common helpers
* `_get_org_admin_manager_tokens` (`:31-55`): push tokens of (a) role-1 users that own the org (`org_info.parent_id = u.user_id`) and (b) **all** role-2 users with `u.parent_id = org_id`; excludes the requester. Manager branch/dept scope is **not** applied to recipients.
* `_get_org_admin_manager_user_ids` (`:58-81`): same set without the token filter, for the WebSocket badge.
* `_fire_notification` (`:9-28`) and `_ws_notify_request` (`:84-101`): fire-and-forget (`asyncio.ensure_future`); failures are swallowed.
* ID generation: `str(uuid.uuid4())` for `request_id`. Timestamps for decisions: `get_utc_now()` (UTC aware, stored in `timestamp` columns as UTC wall time).

### 4.1 Submit (all four)
No validation beyond Pydantic types: no user/org membership check, no date parsing, no duplicate check, no length limits. Order of work per endpoint:
1. **device_change** (`:129-167`): `request_id`; if `org_id` empty, resolve via `users JOIN org_info ON u.parent_id=o.org_id WHERE role_id IN (2,3)` (`:138-145`); INSERT `requests(... 'device_change', reason, 'pending')`; commit; notify.
2. **shift_change** (`:170-201`): read `users_additional_info.shift_id` as `current_shift_id` (NULL if no row); INSERT with `requested_shift_id`, `current_shift_id`, `shift_change_date`, status `'pending'`; commit; notify. Does not check the requested shift exists, belongs to the org, differs from the current shift, or that the date is in the future.
3. **leave** (`:204-238`): INSERT `requests` with `status` **NULL**; then one `leave_dates` INSERT per date with status `'pending'`; commit (single transaction). Does not reject past dates, Sundays, holidays, duplicates, already-requested or already-rejected dates, or empty list.
4. **permission** (`:241-273`): INSERT `requests` (`permission_start_time`, `permission_end_time`, status `'pending'`); INSERT `leave_dates(request_id, leave_date)` (status takes the column default `'pending'`); commit. Does not check end > start, the 6-hour maximum, midnight crossing, or past/today start time.
5. After commit: `_get_user_name`; `_ws_notify_request` (WebSocket `{"type":"request_submitted"}` to each admin/manager user id that has an open `/ws/badge/{user_id}` socket, `router/ws_badge.py:10-18`); one FCM push per admin/manager token (see §5).
6. On any exception: `rollback`, re-raise -> router turns it into 500 `detail=str(e)`.

### 4.2 Employee list (`:276-326`)
Single query on `requests r` LEFT JOIN `users` (decider name) and `shift rs` (requested shift) `WHERE r.user_id AND r.org_id ORDER BY created_at DESC`. `requested_shift_timing` = `TO_CHAR(start,'HH:MI AM') || ' - ' || TO_CHAR(end,'HH:MI AM')` (12-hour, zero padded, e.g. `09:00 AM - 06:00 PM`; no timezone conversion because `shift.start_time` is a local wall time). For each row a second query reads `leave_dates.leave_date` ascending into `dates` (ISO `YYYY-MM-DD`). Time columns are `str(time)` (`HH:MM:SS`). Timestamps are `isoformat()` of naive datetimes (no `Z`; clients treat as UTC).

### 4.3 Admin list (`:329-425`)
1. Scope resolution (`:336-347`): look up the path id in `users`. If it is a role-2 user: `resolved_org_id = users.parent_id`; read `manager_assignments(branch_id, dept_id)` into distinct lists. Otherwise the path id is used as the org id as is (so an **admin user id as path yields no rows**; the app passes `current_org_id || user_id`).
2. Query on `requests r JOIN users u` (inner: requests of deleted users disappear; inactive users included) with `LEFT JOIN` for decider name and both shifts, `WHERE r.org_id = resolved`. Manager scope appends `AND u.branch_id = ANY(%s)` and/or `AND u.dept_id = ANY(%s)` (both when both lists exist: **AND semantics**). Optional `status` appends `AND r.status = %s`.
3. For each request a second query returns `leave_dates` rows `(leave_date, status, action_by, action_at)`; `dates` is the ordered date list; `date_statuses[date] = {status: COALESCE(status,'pending'), action_by, action_at}`. A shift or device request has `dates: []`, `date_statuses: {}`.

### 4.4 Update status (`:428-512`)
Validation order and effects:
1. `status in {approved, rejected, pending}` else 200 `{success:false,"Invalid status"}`.
2. Load `request_type` by `request_id`; missing -> 200 `{success:false,"Request not found"}`.
3. **With `leave_date`**: `UPDATE leave_dates SET status, action_by, action_at WHERE request_id AND leave_date`; `rowcount==0` -> 200 `{success:false,"Date not found"}` (transaction left open, then returns without rollback; connection returned to the pool with an open transaction, see D-14).
4. **Without `leave_date` (bulk form)**: the same UPDATE for **all** `leave_dates` rows of the request (no `leave_date` filter). For a request that has no date rows (shift/device, or a leave submitted with zero dates) it updates nothing and still proceeds.
5. Type-specific:
   * `permission`: `COUNT(DISTINCT COALESCE(status,'pending'))` over its dates; if 1, `UPDATE requests SET status, action_by, action_at, updated_at` (a permission has exactly one date, so this always runs).
   * `shift_change`, `device_change`: `UPDATE requests` directly. For an **approved device_change**: `UPDATE users SET device_token = NULL, push_token = NULL WHERE user_id = (requester)` (`:487-492`).
   * `leave`: **request-level `requests.status` is never written** (stays NULL). Per-date rows are the only record; `action_by/action_at` on `requests` stay NULL for leave.
   * **Approving a shift_change does not change `users_additional_info.shift_id`** or anything else; the approval is purely a status flag (grep shows no consumer of `shift_change_date`/`requested_shift_id` outside this domain).
6. `commit`. Then reload the requester's push token (`_get_user_push_token`) and, if present, one push per call (see §5). For an approved device change the token was just cleared, so **no push is ever delivered** for that decision.
7. `role_id` and `action_by` are accepted but never validated (any caller can approve any request; `action_by` is stored as given). No check of current status (approved -> rejected -> pending flips are allowed), none that the request belongs to the caller's org/scope, and no notification when a per-date call is one of several (one push per call).

### 4.5 Calendar (`:574-598`)
`SELECT ld.leave_date, COALESCE(ld.status, r.status, 'pending'), r.request_type FROM leave_dates ld JOIN requests r ON ld.request_id = r.request_id WHERE r.user_id AND r.org_id AND ld.leave_date BETWEEN start AND end ORDER BY leave_date`. Returns leave and permission dates of all statuses (rejected too, which is what lets the client grey them out; the server itself would still accept a re-request). Holidays, weekly offs and shift-change dates are not included.

### 4.6 Device session (`:537-571`, and `:515-534` unused)
`check_device_session` reads `users.device_token, status`: no row -> `user_not_found`; `status == 0` -> `account_inactive`; stored token NULL -> `device_change_approved` (also true for any account whose token was never set or was cleared); stored != supplied -> `logged_in_on_another_device`; else valid. All "invalid" answers carry `force_logout:true`. `invalidate_other_device_sessions` (`:515-534`) has no caller in the repo (grep) (UNVERIFIED that no deployed code imports it).

Login-side producer: `auth_function.py:878-884` calls `submit_device_change_request(user_id, org_id, "Device change requested")` on **every** device-token mismatch, swallowing errors, which creates a new pending request (and push + WebSocket to all admins/managers) per attempt.

## 5. Side effects & integrations

| Trigger | Channel | Recipients | Payload |
|---|---|---|---|
| any submit | WebSocket `/ws/badge/{user_id}` | role-1 owner + all role-2 of the org, minus requester, if connected | `{"type":"request_submitted"}` (no ids) |
| device_change submit | FCM push | same set with token | title `Device Change Request`, body `<employee name> submitted a device change request.`, data `{type:"request_submitted", request_type:"device_change", request_id}` |
| shift_change submit | FCM | same | title `Shift Change Request`, body `<name> submitted a shift change request.`, data `{type:"request_submitted", request_type:"shift_change", request_id}` |
| leave submit | FCM | same | title `Leave Request`, body `<name> submitted a leave request.`, data `{..., request_type:"leave", ...}` |
| permission submit | FCM | same | title `Permission Request`, body `<name> submitted a permission request.`, data `{..., request_type:"permission", ...}` |
| update-status | FCM | the requester, if `users.push_token` non-empty | title `<Type Label> <Status>` (e.g. `Leave Approved`, `Shift Change Rejected`), body `Your <type label lower> has been <status>.`, data `{type:"request_updated", request_type, request_id, status}` |

FCM v1 sender: `core_functions/auth_function.py:164-201` (data values are stringified; Android channel `attendance_notifications`; failures logged, never raised). No e-mail or WhatsApp. No WebSocket event goes to the employee on a decision. Neither push nor WS is sent on an admin's behalf to other admins when one admin decides.

## 6. Config & env vars

None specific. Indirect: `DB_SCHEMA`, DB connection variables (`core_functions/db_function.py`), FCM credentials `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`, `FCM_PRIVATE_KEY_ID`, `FCM_CLIENT_ID` (a secret exists at `core_functions/auth_function.py:61-100`, values not recorded here).

## 7. Known defects & risks

| # | Severity | Where | Defect / impact |
|---|---|---|---|
| D-1 | High | `requests_functions.py:129-167`, `router/requests.py:57` | Unauthenticated device-change submit: any `user_id`/`org_id` pair, no membership validation, no dedupe, no rate limit; every call pushes + WS-notifies all admins/managers. Combined with D-2 an admin who approves a spam/forged request logs the real user out (tokens cleared). |
| D-2 | High | `:487-492` | Approving a device change clears both `device_token` and `push_token` of the requester; no check that the request came from the user's own device/login attempt. |
| D-3 | High | `:428-512`, `router/requests.py:126` | `role_id`/`action_by` ignored: no authorization or org scoping on approve/reject; any caller can decide any request id, flip decisions, or revert to `pending`. |
| D-4 | High | `:204-273` | No server-side rule enforcement on leave/permission (see §8 R-5). Past dates, Sundays, holidays, duplicates, rejected-date re-request, >6 h permission, end<=start, empty date list, zero-length reason all accepted. |
| D-5 | Medium | `:214` + `:455-476` | Leave `requests.status` is NULL forever (JSON `null`); admin `?status=` filter never matches leave; `action_by/action_at` never set on the request; clients must derive status from dates. |
| D-6 | Medium | `:282-293` | Employee list omits `date_statuses`, shift ids, `current_shift_timing`. |
| D-7 | Medium | `:428-512` | Approving a shift change has no effect on the employee's shift; the feature is decorative unless an admin separately edits the shift. |
| D-8 | Medium | `:455-461` | Bulk (no `leave_date`) call silently succeeds with zero rows for requests lacking date rows; per-date and bulk both send one push per call (N pushes for N dates). |
| D-9 | Medium | `:487-506` | Device-change approval push is never delivered (token cleared before the push-token lookup at `:498`). |
| D-10 | Medium | `:31-55` | Push/WS recipients ignore manager scope: managers outside the requester's branch/dept are notified of requests they cannot see in `/requests/admin`. |
| D-11 | Medium | `auth_function.py:878-884` | Every device-mismatch login attempt inserts a new pending device-change request (no dedupe): inbox and push spam. |
| D-12 | Low | `:336-347` | Admin list path semantics: path id is a *user id first*; passing an admin's own user id returns no rows, passing a manager id returns that manager's scoped org. Branch AND dept filters combine with AND. |
| D-13 | Low | `:349-364` | Inner join to `users`: requests of deleted staff vanish from the admin list; inactive staff remain. |
| D-14 | Low | `:447-454` | "Date not found" returns without rollback/commit; pooled connection goes back with an open transaction (psycopg2 pool is not reset in `return_connection`; UNVERIFIED impact). |
| D-15 | Low | `router/requests.py` (all) | Handlers are `async def` calling blocking psycopg2 code: the event loop is blocked per request; N+1 queries in both list endpoints. |
| D-16 | Low | `:539-560` | `check-session`: NULL `device_token` forces logout with reason `device_change_approved`, even for never-set or logged-out sessions; returns no `success` key. |
| D-17 | Low | `:170-187` | `shift_change_date`/`requested_shift_id` unvalidated; bad input produces 500 with the raw DB error text (information leak). |
| D-18 | Low | schema dump | `request_type_enum` definition is not in the dump; `requests.request_id` has no UNIQUE; `leave_dates` has no UNIQUE(request_id, leave_date) and no FK to `requests`. |
| D-19 | Low | `:136-148` | Organisation lookup for an empty `org_id` only considers roles 2 and 3 (admins cannot submit a device change without an org id). |
| D-20 | Info | mixed clocks | `created_at/updated_at` use DB `CURRENT_TIMESTAMP`, decisions use Python UTC; both are zone-less in JSON. |

## 8. Clockyn requirements

Cross-references are to `C:\POC\pa_frontend\Clockyn\backend_changes.md` (BC) and `docs/DEFERRED_DECISIONS.md` (DD).

### R-1 Employee requests need `date_statuses` and shift ids (BC P1-1)
* **Current:** `get_employee_requests` returns `dates` only, no `date_statuses`; shift rows carry only `requested_shift_timing`; leave `status` is `null`. The app falls back to request status (`src/api/mappers/requests.ts` `deriveStatus`) and to `/requests/calendar` for leave badges. The frontend mapper reads `requested_shift_id`, `current_shift_id`, `requested_shift_timing`, `current_shift_timing` (`mapRequest`); it does not read `new_shift_id` anywhere in `src/`, so `new_shift_id` is only a v1 name.
* **Required change:** add `date_statuses`, `requested_shift_id`, `current_shift_id`, `current_shift_timing` (and optionally an alias `new_shift_id` = `requested_shift_id` for v1 clients) to each employee row; give leave a derived request-level `status` (see R-4).
* **Outline:** in `requests_functions.py` `get_employee_requests` (`:282-317`): select `r.requested_shift_id, r.current_shift_id` and LEFT JOIN `shift cs ON r.current_shift_id = cs.shift_id`; replace the second query with the admin variant (`SELECT leave_date, status, action_by, action_at`), build `dates` and `date_statuses` exactly as `:405-416`. Extract one shared `_build_request_row(row, cursor)` used by both list functions to prevent drift. Fix the N+1 by a single `WHERE request_id = ANY(%s)` query.
* **Backward compat:** purely additive fields; keep `dates` unchanged.
* **Acceptance:** (1) employee with a 3-date leave where one date is approved, one rejected, one pending gets `date_statuses` with three entries and the exact `status/action_by/action_at`; (2) shift-change row contains both shift ids and both timing strings; (3) a device-change row has `dates: []`, `date_statuses: {}`; (4) response keys are a superset of the previous response.

### R-2 Holiday and weekly-off dates in `/requests/calendar` (DD-97)
* **Current:** only leave and permission dates (`requests_functions.py:579-593`); the client cannot grey out company holidays or non-Sunday weekly offs.
* **Required change:** return off-day entries so employees cannot select them.
* **Outline:** extend `get_employee_calendar_data` to append (a) org holidays for the range from `org_holidays` filtered like `check_holiday_block` does (`core_functions/holiday_functions.py:150-164`): `org_id = %s AND date BETWEEN %s AND %s AND (branch_name IS NULL OR branch_name = <user's branch name>) AND (dept_name IS NULL OR dept_name = <user's dept name>)`, and (b) weekly offs from `org_off_days.off_days` (Python weekday, 0=Mon..6=Sun, default `[6]`; `holiday_functions.py:6-19`) expanded over the range. Emit `{date, status:"approved", type:"holiday"|"weekly_off", name?}`.
* **Backward compat (important):** the current mapper (`mapCalendarDay`) maps any `type` other than `permission` to `leave` and any known status verbatim, so an unmodified client would show holidays as **approved leave**. Gate the new rows behind an opt-in query parameter (e.g. `include_off_days=true`) until the new client reads `type`; old clients keep the old shape.
* **Acceptance:** with a holiday on the 15th scoped to branch A, a branch-A employee gets the entry and a branch-B employee does not; with `off_days=[5,6]` all Saturdays and Sundays of the range are listed; without the flag the response is byte-identical to today; range with no data returns `{success:true,data:[]}`.

### R-3 Leave `update-status`: per-date semantics and the bulk form (BC P1-15, DD-91)
* **Current:** with `leave_date` -> that date only; without it -> **all** dates of the request (a working bulk, `:455-461`); for a request without dates it updates nothing and still reports success; leave `requests.status` untouched; one push per call; no role/org check; a status of `pending` is allowed; "Date not found" is HTTP 200.
* **Required change:** document and keep both forms, make them safe: reject an unknown request with 404 and a request with zero matching date rows (for leave/permission) with 404/409; return the updated per-date map (`date_statuses`) and the derived request status so the client does not refetch; send **one** push per call summarising the decision (e.g. `Leave approved for 3 dates`); validate caller (BC P3-1).
* **Outline:** in `update_request_status` check `cursor.rowcount` for the bulk branch; after the update recompute `COUNT(DISTINCT COALESCE(status,'pending'))` for leave as for permission (`:464-476`) and write the derived status (see R-4); accept `leave_dates: [str]` as an optional list for a multi-date decision in one call (keeps `leave_date` working).
* **Backward compat:** current clients call per date (DD-91 says the new client currently applies one decision to all dates by calling per date); keep `leave_date` and the all-dates default.
* **Acceptance:** (1) bulk approve of a 3-date leave sets 3 rows and returns `success:true`; (2) per-date reject of one date leaves the other two pending and the derived request status `pending`; (3) bulk call on a request id that has no date rows -> 404 (not success); (4) unknown date -> 404 with a message; (5) exactly one push per call.

### R-4 Meaningful request-level status for leave (BC P1-1 / P1-15, D-5)
* **Current:** leave `requests.status` is NULL.
* **Required change:** derive and persist `pending` (any date pending) / `approved` (no pending and at least one approved) / `rejected` (all rejected), the same rule as the client `deriveStatus` (`src/api/mappers/requests.ts`).
* **Outline:** insert leave with `'pending'` instead of NULL (`requests_functions.py:213-216`), recompute in `update_request_status`, and one-off back-fill: `UPDATE requests r SET status = ... FROM (SELECT request_id, ...)` for existing NULL rows. Also set `requests.action_by/action_at`.
* **Compat:** the admin `?status=` filter then matches leave; clients that treat NULL as pending are unaffected.
* **Acceptance:** `GET /requests/admin/{org}?status=pending` returns pending leave; after deciding all dates the request status is stable.

### R-5 Server-side enforcement of leave / permission rules (DD-90, BC P2-7)
Rules the Clockyn client enforces today (`requestRules.ts`) and what the server does:

| Rule | Client (file) | Server today |
|---|---|---|
| Past dates not allowed, today allowed | `dateBlockReason` (`requestRules.ts`) | **not enforced** |
| Sundays not selectable | `dateBlockReason` / `sundaysOfMonth` | **not enforced** (configurable weekly offs exist server-side in `org_off_days`, default Sunday) |
| A date that already has a request of any status, **rejected included**, cannot be re-requested | `dateBlockReason` (`'requested'`) from `/requests/calendar` | **not enforced** (no unique key, no check) |
| Permission end later than start, at most 6 h (`MAX_PERMISSION_MINUTES = 360`), no midnight crossing | `endSlots`, `validatePermission` | **not enforced** |
| Permission today must start after current IST minute | `validatePermission` | **not enforced** |
| At least one date and non-empty reason | `validateLeave` | **not enforced** (empty list and blank reason accepted) |
| Shift change: reason, shift and date required | `validateShiftChange` | not enforced; requested shift existence/org/difference/duplicate not checked |
| Company holidays not selectable | not enforced client-side either (DD-97) | not enforced |

* **Required change:** implement these as server rules with 400/409 responses (suggest `{detail}` with a machine `code` such as `DATE_IN_PAST`, `OFF_DAY`, `ALREADY_REQUESTED`, `PERMISSION_TOO_LONG`), computed in **IST** (`utils/timezone.py` `IST`) since the client sends IST dates (BC P2-5). Product decision pending on holiday blocking and leave balance (DD-90).
* **Outline:** add a `_validate_leave_dates(cursor, user_id, org_id, dates)` helper in `requests_functions.py` called before the INSERT in `submit_leave_request` (past/off-day via `org_off_days`, duplicates inside the payload, existing `leave_dates` joined to `requests` for the user in any status, optional holiday check as in R-2); a `_validate_permission(...)` for the time rules; for shift change verify `shift_id` belongs to the org and differs from the user's current shift and that no pending shift change exists for that date. Add `UNIQUE (request_id, leave_date)` after de-duplication of existing rows. Validate that `user_id` belongs to `org_id` (`users.parent_id`).
* **Backward compat:** previously accepted payloads that violate the rules become 400; the client already blocks them, so only direct API callers are affected. Provide a feature flag during rollout.
* **Acceptance:** each rule above has a negative test (400/409, no rows written, no notification sent) and a positive test; rejected-date re-request returns 409; permission of 360 minutes passes, 361 fails; a Sunday fails unless `org_off_days` says otherwise; dates compared against the IST date between 00:00 and 05:30 IST.

### R-6 Device-change submit abuse (BC P3-4, P1-14)
* **Current:** see D-1, D-11. The Clockyn login screen calls `POST /requests/device-change/submit` before login (works pre-auth by design: `src/api/requests.ts` `submitDeviceChange`), and the server also auto-creates a request on mismatch.
* **Required change:** keep it callable before login but (a) validate that `user_id` exists, is active, and belongs to `org_id`; (b) reject when the user already has a `pending` device-change request (return the existing `request_id` with `success:true` and a flag `already_pending:true`); (c) rate-limit per user and per IP (e.g. 5/hour); (d) require a recent failed device check or a one-time proof (e.g. the OTP-verified token from login) so a third party cannot create requests; (e) notify admins only for the first pending request; (f) cap `reason` length.
* **Outline:** in `submit_device_change_request` add `SELECT request_id FROM requests WHERE user_id=%s AND request_type='device_change' AND status='pending'` before the INSERT and reuse it from `auth_function.py:882`; add a small in-memory/Redis limiter (no limiter exists in the repo) or a DB count over `created_at`. Also add `request_pending` to the `verify-device-token` response (BC P1-14).
* **Compat:** response keys unchanged; `already_pending` is additive.
* **Acceptance:** two submits for the same user create one row and one notification; unknown user -> 404; mismatching org -> 400; the 6th call within an hour -> 429.

### R-7 Employee push for device-change approval (new; D-9)
Send the decision push **before** clearing `push_token`, or skip the push and rely on `check-session` (`device_change_approved`). Acceptance: after approval the old device receives either a push or a forced logout within one poll (30 s).

### R-8 Pending counts endpoint (BC P2-8)
Add `GET /requests/admin/{org_id}/counts?` returning `{success:true, data:{leave, permission, shift_change, device_change, total}}` computed in SQL with the manager scope of `get_admin_requests` and the "any date pending" rule (leave/permission) or `status='pending'` (others); add `status`, `limit`, `offset` to the list endpoint. Acceptance: counts equal the client's `computePendingCounts` over the full list for admin and manager ids.

### R-9 Apply an approved shift change (new, product decision)
Decide whether approval should update `users_additional_info.shift_id` on `shift_change_date` (D-7). Current Clockyn UI promises only a status. Implementation if accepted: a scheduled job or a read-time override in the attendance shift lookup keyed by `shift_change_date`.

## 9. Test notes

* **Fixtures:** an org with one admin (role 1, `org_info.parent_id` = admin user), two managers (role 2, `parent_id` = org, rows in `manager_assignments`), employees in different branches/depts, two shifts, `users_additional_info.shift_id` set, push tokens set on admin/manager/employee, an open `/ws/badge/{id}` socket for the admin. Mock FCM by patching `core_functions.auth_function._send_push_to_token`.
* **Critical cases:** submit each type and assert rows (`requests`, `leave_dates`), notifications and WS payload; leave submit leaves `requests.status` NULL (current) / `pending` (after R-4); per-date and bulk update-status; permission status derivation; approve device change clears both tokens and `check-session` then returns `device_change_approved`; manager scope (branch AND dept) in admin list; admin list with `?status=pending` excludes leave (current behaviour); calendar range inclusive on both ends; unauthenticated device-change spam produces N rows (current).
* **Existing tests:** none found in the backend repository for this domain (no `tests/` directory at repo root). The frontend has unit tests for rules/mappers (`src/features/requests/**/__tests__`, `src/api/__tests__/staffRequestsExpensesContract.test.ts`) and a mock server selfcheck (`e2e/mock-server/selfcheck.js`).

## WP7 Changes (requests and sessions)

* **Code:** `core_functions/request_rules.py` (new, pure rules and messages), `core_functions/shift_resolution.py` (new, `shift_for`), `core_functions/requests_functions.py` (validation, dedupe, rate limit, actor check, per-date decisions, calendar), `router/requests.py` (honest 500s, `include_off_days`), `core_functions/auth_function.py` + `router/auth.py` (device flow, `POST /auth/logout`, see identity_auth_onboarding.md).
* **Migration 030** (`migrations/030_requests_sessions.sql`, also in `clockyn_ddl_latest.sql`): index `idx_requests_user_type_created (user_id, request_type, created_at)` and a one-off, idempotent back-fill of the request-level status of older leave rows (NULL -> aggregate of their dates).
* **Env:** `REQUESTS_ENFORCE_ACTOR` (default off = log `actor_check_failed` only; roll out: watch the log for one release, then turn on), `ENFORCE_SINGLE_DEVICE` (default off = today's OTP take-over).
* **Decisions applied:** D-6 (one-day override), D-7 (leave approval = day type only), D-11 (single device), D-17 (server-side overlap / 6 h / per-date status / calendar off days).
* **Not done here:** WebSocket `request_update` frame to the employee (WP8), pending-counts endpoint (R-8), holiday blocking and Sunday blocking of leave dates (product decision DD-90/DD-97: the calendar now returns the entries, the app greys them out, the server does not reject them), unique constraints on `leave_dates` / pending device-change (existing duplicate data would make a migration fail).
* **Tests (DB-free):** `tests/test_requests_rules.py`, `tests/test_shift_resolution.py`, `tests/test_device_flow.py`, `tests/test_requests_status_actor.py`, `tests/test_requests_routes.py` (+ helper `tests/wp7_fakes.py`). Two fixtures in `tests/test_quick_wins_2.py` were adapted to the new query shapes (the per-date decisions are read with one query; the request lookup returns the org/requester columns).
