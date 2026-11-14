# Identity, auth & onboarding — backend context

Companion file: `employee_profile_version_attribution.md` (employee profile, profile photo, version check, attribution, Meta events, WhatsApp, invoice e-mail/PDF, time zone helper).
Conventions: `<LP>_` stands for the legacy product prefix on three table names (`<LP>_manager_permissions`, `<LP>_settings`, `<LP>_subscription`; cited by file:line). Secrets/values are never copied; "a secret exists at file:line" only. Line numbers refer to the snapshot in `C:\POC\pa_backend\Clockyn`.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/auth.py` | 446 | `/auth/*` router: 23 endpoints (login lookups, OTP, device trust, registration, push token, logos/photos, gen-admin console login + user admin, SSE session events) |
| `core_functions/auth_function.py` | 1,111 | All auth logic: OTP generate/validate, device-token verify, admin/employee login, registration, push-token update, FCM sender (`get_fcm_access_token`, `_send_push_to_token`, `send_push_notification`, `send_punch_in_out_notification`), MSG91 SMS, SES e-mail OTP |
| `core_functions/session_registry.py` | 20 | In-memory map `user_id -> asyncio.Queue` used by SSE force-logout |
| `core_functions/permission_checker.py` | 155 | Manager-permission lookups (`check_manager_permission_middleware`, `get_user_permissions`, unused decorator/class) |
| `core_functions/db_function.py` | 81 | psycopg2 pool, `get_db_connection`, `return_connection` (every module uses it) |
| `ml/s3.py` (only the logo/profile helpers, lines 116-187) | 422 | `save_org_logo`, `get_org_logo_url`, `save_employee_profile`, `get_employee_profile_url` |
| Related, owned elsewhere (interplay only) | | `core_functions/requests_functions.py:129-167` (`submit_device_change_request`), `:429-499` (approval clears tokens), `:515-566` (`invalidate_other_device_sessions`, `check_device_session`), `router/requests.py:57,151` (`/requests/device-change/submit`, `/requests/check-session/{user_id}`), `router/manager.py:90` (`GET /managers/permissions/{user_id}`), `router/report.py:117,146` (permission gate), `core_functions/gen_dashboard_functions.py:100-139` (org deactivate/delete -> users.status + SSE force-logout) |

Frontend callers: `src/api/auth.ts` (login, requestOtp, verifyOtp, verifyDeviceToken, registerCompany, checkMobileExists, checkEmailExists, updatePushToken, getUserMobile, getOrgLogoUrl, getEmployeePhotoUrl, getProfilePhotoUrl, uploadOrgLogo, uploadEmployeePhoto), `src/api/requests.ts:145-152` (`checkSession`), `src/api/mappers/auth.ts`, `src/store/auth.ts` (`sessionFromAdminLogin / sessionFromStaffLogin / sessionFromRegistration`), `src/features/auth/utils/loginService.ts`, `loginMachine.ts`, `deviceToken.ts`, `src/components/layout/bootstrap/pushToken.ts`. The frontend never calls: `/auth/admin-login*`, `/auth/gen-admin-users*`, `/auth/register-company`, `/auth/push-token-status`, `/auth/session-events` (SSE; frontend uses polling and a push `force_logout`).

## 2. Data model touched

Schema is chosen per process by `DB_SCHEMA` (`search_path`), dump in the root-level DDL file (legacy-named `*_ddl_latest.sql`; older than the code).

| Table | Columns used (type notes) | Keys / indexes | Notes |
|---|---|---|---|
| `users` | `user_id varchar(36)`, `org_id varchar(36)` (set for admins only), `name varchar(50)`, `emp_id`, `branch_id`, `dept_id`, `parent_id` (employee/manager -> org_id), `mobile_number varchar(255)`, `email varchar(50)`, `role_id` (1 admin, 2 manager, 3 staff), `status int` (1 active, 0 inactive, 2 deleted), `device_token varchar(36)`, `device_token_expires_at timestamptz`, `push_token text`, `created_at`, `updated_at`, `meta_*`, `install_source`, `attribution_platform` | PK `id`; UNIQUE `user_id`; UNIQUE index on `org_id` (`users_org_id_key`, NULLs distinct); non-unique indexes on `mobile_number`, `email` | No unique constraint on mobile or e-mail: duplicates only blocked in code (races possible). `status` 0/2 are also written by org deactivate/delete (`gen_dashboard_functions.py:107-117`). `device_token` is limited to 36 chars (a longer client value -> DB error -> 500). |
| `org_info` | `org_id`, `org_name`, `org_address`, `org_latitude/longitude` (0,0 on register), `org_gst`, `parent_id` (= admin `user_id`), `state`, `district`, `pincode numeric(6)`, `status`, `created_at` | UNIQUE `org_id`; index `parent_id` | An admin may own several rows (multi-company, `company_functions.py:53`). `status` is not read by any auth query. |
| `branch_info` | `org_id, branch_id (per-org counter), branch_name, branch_address, gps_location POINT, created_at` | | Registration creates branches 1..n |
| `dept_info` | `org_id, dept_id (uuid), dept_name, created_at` | | "General" created when no department sent |
| `company_additional_info` | `user_id, org_id, pan_number, company_website, cin_number, tan_number, msme_number, business_category, gst_number, created_at` | | Row inserted only if any value present |
| `free_trial` | `org_id, user_id, start_date, end_date, is_active` | UNIQUE `org_id` | 1 month, created after the main commit |
| `<LP>_subscription` | `org_id, plan_id, status ('ACTIVE'), created_at` | | admin login reads the newest ACTIVE row |
| `moblie_otp_verification` (sic) | `id identity, mobile_number varchar(255), otp_code int4, is_verified bool, attempts int4 NOT NULL, created_at timestamp, expires_at timestamp` | idx `mobile_number`, `expires_at` | One row per number (delete+insert). Timestamps are **without time zone** (see §7 D-18). |
| `email_otp_verification` | same, `email varchar(255)` | idx `email`, `expires_at` | |
| `gen_admin_login` | `phone_number varchar(20)`, `role_id int`, `name varchar(100)` | **no PK, no unique** | Operator (platform admin) directory; role 1 = may manage users. |
| `<LP>_manager_permissions` | `user_id, org_id, setting_id, status (1 = granted)` | idx user_id, org_id, setting_id | |
| `<LP>_settings` | `setting_id, setting_name` | | Feature names (`Staff Management`, `Branch Management`, `Department Management`, `Attendance Reports`, `Payroll Management`, `Holiday Management`, `Shift Management`, `Leads Management`, `Incentive Management`, `Company Settings` - strings in `permission_checker.py:77-114`) |
| `requests` | `request_type='device_change', status 'pending'/'approved'` (written by `submit_device_change_request`) | | Written as a side effect of `verify-device-token` mismatch |

Status/enum meanings: `users.status` 1/0/2 above; `role_id` 1/2/3; OTP `purpose` in `registration | login | delete_account | update_email`.

## 3. Endpoints

All handlers are `async def` (psycopg2 calls block the event loop). **Without `REQUIRE_AUTH` no endpoint requires a bearer token (WP12: see "Authentication tokens (WP12 Changes)" below)**; identity = ids/phone in URL, query, body or the `x-admin-id` header (P3-1). Plan gate: none in this domain.

| # | METHOD path | Handler (file:line) | Auth / identity param | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|---|
| 1 | POST `/auth/admin-login` | `gen_admin_login_route` router/auth.py:53 | none (phone in body) | `{phone}` | `{success:true,message:"OTP sent successfully"}` | 401 "Phone number not found"; 500 "Internal server error". OTP is also `print()`ed (auth.py:84). No attempt/rate limit. Shares `moblie_otp_verification` with user OTPs. |
| 2 | POST `/auth/admin-login/verify-otp` | `gen_admin_verify_otp_route` :97 | none | `{phone, otp}` | `{success:true, phone_number, role_id}` (no token) | 400 "Invalid OTP format" / "Invalid or expired OTP". **No attempts counter** (brute-forceable); the static bypass numbers are not honoured here. |
| 3 | GET `/auth/gen-admin-users` | `list_gen_admin_users` :186 | header `x-admin-id` = phone with role 1 | - | `{success, users:[{phone_number,name,role_id}]}` | 403 "Only role_id=1 admins can perform this action" |
| 4 | POST `/auth/gen-admin-users` | `add_gen_admin_user` :199 | same | `{phone_number, name="", role_id=2}` | `{success,user}` | 400 "Phone number already exists" |
| 5 | PUT `/auth/gen-admin-users/{phone_number}` | `update_gen_admin_user` :224 | same | `{phone_number,name,role_id}` | `{success,user}` | 404 "User not found"; may rename to an existing phone (duplicate) |
| 6 | DELETE `/auth/gen-admin-users/{phone_number}` | `delete_gen_admin_user` :248 | same | - | `{success:true}` | 404; can delete the last role-1 user |
| 7 | POST `/auth/register` | `register_route` :269 -> `admin_register_json` auth_function.py:364 | none | body `AdminRegisterRequest` (router model :24-44): `name, mobile_number, email, org_name, gst_number` (all required str), `departments: List[str]` (required, may be `[]`), `branches: List[str]` (required), optional `emp_id="", org_address="", org_gst="", pan_number, company_website, cin_number, tan_number, msme_number, business_category, device_token, state, district, pincode:int` | `{success:true,message:"Registration successful!",user_id,org_id,org_name,role_id:1,org_logo}` | 400 "Company name, mobile, email, and branches are required"; **any other failure incl. 400 "User already registered" -> 500 "Registration failed"** (:474-478); 422 on missing `gst_number`/`departments` |
| 8 | POST `/auth/register-company` | `register_company_route` :273 | none | identical to #7 | identical | duplicate alias of #7 (frontend uses `/auth/register`) |
| 9 | POST `/auth/update-push-token` | `update_push_token_route` :277 -> auth_function.py:131 | `user_id` in body | `{user_id, push_token, device_token?}` (`device_token` ignored) | `{success:true,message:"Push token updated successfully"}` | empty token -> 400 `{detail:{success:false,message}}` (dict detail); unknown user 404 (dict detail); 500 dict detail; the model's `model_post_init` also raises `ValueError` on empty values (:125-129) -> 422 expected (UNVERIFIED: pydantic 2.12 wrapping) |
| 10 | POST `/auth/login` | `login_route` :284 -> auth_function.py:953 | mobile only (`login_id`) | `{login_id}` | admin body, see §4.5 | 400 "Mobile number is required"; 403 deleted/inactive/employee-account; 401 "User not found. Please register first."; **does not require OTP or device token** |
| 11 | POST `/auth/employee-login` | `employee_login_route` :288 -> :1038 | mobile only | `{login_id}` | employee body, see §4.5 | 403 deleted/inactive/admin-account; 401 "Employee not found or not authorized" |
| 12 | POST `/auth/generate-otp` | `generate_otp_route` :292 -> :508 | contact in body | `{contact, purpose, role_id?}` | `{success:true,message:"<Purpose> <mobile\|email> OTP sent successfully!"}` | 400 missing/invalid purpose; 400 "Mobile number already registered"/"Email already registered" (registration); 403 deleted/inactive/portal mismatch (login); 404 "There is no user with this phone number" (login with role_id 2/3 only); 429 locked; 500 "Failed to generate OTP". Returns success even when SMS/e-mail delivery failed. |
| 13 | POST `/auth/validate-otp` | `validate_otp_route` :296 -> :632 | contact in body | `{contact, otp_code, purpose, device_token?, push_token?}` | `{success,message,device_token,user_exists,role_id}` | 400 "Invalid OTP format"; 400 "Invalid or expired <purpose> <mobile\|email> OTP" (wrong, expired or missing - indistinguishable); 429 after 5 wrong attempts; 500 "Failed to validate OTP" |
| 14 | POST `/auth/verify-device-token` | `verify_device_token_route` :300 -> :772 | contact in body | `{contact, device_token, role_id?, push_token?}` | flags, see §4.3 | 400 "Contact and device token are required"; business outcomes are HTTP 200 with `success:false` |
| 15 | GET `/auth/check-mobile/{mobile}` | `check_mobile_exists` | PUBLIC (QW-13; no credential) | path | `{exists:bool}` and nothing else; any account (any role, any status) counts, the same set `register-company` refuses | 429 from the per-IP limit AUTH_CHECK 30/min |
| 16 | GET `/auth/check-email/{email}` | `check_email_exists` | PUBLIC (QW-13) | path | `{exists:bool}`, matched case-insensitively (`LOWER(email) = LOWER(%s)`) | same |
| 17 | POST `/auth/upload-org-logo/{org_id}` | `upload_org_logo` :330 | none; REQUIRE_AUTH=1: admin of `org_id` only | multipart `logo` | `{success:true,logo_url}` | key `org_logo/{org_id}.png`, content-type forced `image/png`; K2: `read_upload` (UPLOAD_MAX_MB, default 10: 413; png/jpeg/webp/gif by magic bytes, plus HEIC/HEIF stored as `.heic` / `image/heic`: 415 otherwise); 500 generic |
| 18 | GET `/auth/org-logo/{org_id}` | `get_org_logo` :340 | none | path | `{success:true,logo_url}` or `{success:false,logo_url:null}` | presigned URL, 7 days (`ml/s3.py:134-150`) |
| 19 | POST `/auth/upload-employee-profile/{user_id}` | `upload_employee_profile` :352 | none (anyone can overwrite); REQUIRE_AUTH=1: the user or an admin / manager of the user's organisation | multipart `photo` | `{success:true,photo_url}` | key `employee_profile/{user_id}.jpg`, JPEG content-type forced; K2: same size / type check as the logo (HEIC accepted, stored as `employee_profile/{user_id}.heic`) |
| 20 | GET `/auth/employee-profile/{user_id}` | `get_employee_profile` :362 | none | path | `{success:true,photo_url\|null}` | checks `.jpg` then `.png` |
| 21 | GET `/auth/user-mobile/{user_id}` | `get_user_mobile` :371 | none; K2: with a token, OWNER ONLY (`auth_policy.OWNER_ONLY_RULES`, `core_functions/owner_guard.py`: 403 for any other id, administrators included) | path | `{success:true,mobile_number}` | 404 "User not found"; PII by id (used for the delete-account OTP) |
| 22 | GET `/auth/push-token-status/{user_id}` | `get_push_token_status` :386 | none | path | `{success,user_id,has_push_token,token_preview(first 30 chars),updated_at}` | debug endpoint in production |
| 23 | GET `/auth/session-events/{user_id}?device_token=` | `session_events` :409 | `user_id`+`device_token` validated against DB | query | SSE stream: `data: force_logout` then close; `: heartbeat` every 30 s | 403 "Invalid user_id or device_token". Per-process memory (see §5). |

Related endpoints owned elsewhere that the auth flow depends on: `GET /requests/check-session/{user_id}?device_token=` -> `{valid:bool, force_logout:bool, reason?}` (`requests_functions.py:537-566`; reasons `user_not_found`, `account_inactive`, `device_change_approved` (token NULL), `logged_in_on_another_device`); `POST /requests/device-change/submit`; `GET /managers/permissions/{user_id}` -> `{permissions:[names]}` (admin: all names; manager: granted; staff: `[]`).

| 24 | POST `/auth/logout` (WP7) | `logout_route` router/auth.py -> `logout_user` auth_function.py | `user_id` in body (no bearer token yet) | `{user_id, device_token?, push_token?}` | always `200 {success:true}` | Clears `users.device_token` / `device_token_expires_at` when `device_token` is omitted or equals the stored one (a stale device cannot sign the active one out) and removes the given push token (`users.push_token` and its `user_push_tokens` row) only. Idempotent: unknown user, repeat call or a storage error still answer `{success:true}` (errors are logged). Bearer-token revocation is WP12. |

Endpoint count in this file: 24 (23 plus `POST /auth/logout`).

**WP7 notes on rows 13 and 14** (device flow, see "WP7 Changes" at the end of this file): `verify-device-token` now also returns `request_pending` on a mismatch and files at most one pending device-change request per user (3 per 24 h); with `ENFORCE_SINGLE_DEVICE` on, `validate-otp` (`purpose=login`, roles 2/3) answers `409 {"detail":"device_mismatch"}` for a login from a device that is not the active one.

## 4. Business logic

### 4.1 OTP generation (`generate_otp`, auth_function.py:508-630)
1. 400 if `contact` or `purpose` empty; `purpose` lower-cased, must be one of 4 values (:512-514).
2. **Bypass:** if `contact` is in the hard-coded `_BYPASS_NUMBERS` dict (defined :29-34; 4 numbers, comments mark 2 admin + 2 employee) the function returns success immediately: no SMS, no DB, **for any purpose including registration** (:517-521). See D-1.
3. `registration`: looks up `users` with role_id=1 by e-mail (if contact contains `@`) else mobile; match -> 400 "...already registered" (:528-546). Employees' numbers are not checked (the later register call fails with a wrapped 500).
4. `login`: first user row by `mobile_number` (`LIMIT 1`, unordered; a number is looked up even when `contact` is an e-mail). `role_id in [2,3]` and no user -> 404. status 2 -> 403 deleted; status 0 -> 403 inactive; role mismatch -> 403 "employee account"/"admin account" (:549-576). An unknown number with `role_id=1` or none still gets an OTP (admin `/auth/login` later answers 401).
5. `delete_account` / `update_email`: no ownership check - any contact receives an OTP.
6. OTP = `random.randint(100000, 999999)` (non-cryptographic PRNG), expiry = now(UTC)+**5 minutes** (:578-579). Channel by `"@" in contact`: e-mail -> table `email_otp_verification`/column `email`; else `moblie_otp_verification`/`mobile_number` (whitelist map :23-26).
7. Lock check: existing row with `attempts >= 5` and `expires_at > now` -> **429** (:585-595). Otherwise `DELETE` old row, `INSERT` new (`attempts=0`, `is_verified=false`), `commit` (:597-606). There is **no time-based resend limit**: a client can request a new OTP unlimited times, each resetting `attempts` to 0.
8. Delivery: e-mail via `asyncio.create_task(_send_email_otp_async)` (fire-and-forget, SES `send_email`, subject/body contain the legacy brand at :327; failure only logged, :304-337); mobile via `_send_msg91_sms` (sync `httpx` POST to the MSG91 flow API, 10 s timeout, :908-949). If MSG91 credentials are missing it returns False silently (:913-915). Delivery failure is logged and the API still returns success (:614-616).
9. Mobile normalisation in `_send_msg91_sms`: prefixes `91` unless the string already starts with `91` (:918-919) -> a 10-digit number that itself begins with `91` is sent without country code (D-8).

### 4.2 OTP validation (`validate_otp`, :632-770)
1. Same required/purpose checks (:633-638).
2. **Bypass:** contact in `_BYPASS_NUMBERS` and `otp_code` equals the static value -> issues a device token (client's `device_token` or a new UUID), expiry now+90 days, updates `users` (status=1 only; also `push_token` if sent) and returns success **without touching the OTP tables or SSE** (:644-671). Wrong static value falls through to the normal path.
3. `int(otp_code)` else 400 "Invalid OTP format" (:678-681).
4. Select OTP row where `expires_at > now`; none -> 400 "Invalid or expired ..." (:684-695).
5. `attempts >= 5` -> 429 "Too many incorrect OTP attempts. Please request a new OTP." (checked **before** comparing, so even the correct code is refused after 5 failures) (:697-701).
6. Mismatch -> `attempts = attempts + 1`, commit, 400 same message as expiry (:703-714). So max 5 wrong attempts per OTP; with unlimited regeneration the per-contact guess rate is unbounded (D-3).
7. Success: delete OTP row; `device_token = request.device_token or uuid4()`; select active user (`status = 1`) by the contact column (`email` or `mobile_number`, `fetchone`, unordered); if found: `UPDATE users SET device_token, device_token_expires_at = now+90d [, push_token]` (:723-744), commit, then `force_logout_user(user_id)` (SSE push to the previous device, **also fires on the current device if it is connected**, and also for purposes `delete_account`/`update_email`) (:745-748).
8. Response `{success,message,device_token,user_exists,role_id}`; unknown user (registration) -> `user_exists:false, role_id:null`, device token not persisted (registration inserts it, :435).
9. OTP verification is **not linked** to later actions: `/auth/register`, `/company/delete-account`, e-mail update etc. never check that a verified OTP exists (`is_verified` is never set true anywhere in this domain).

### 4.3 Device-token lifecycle and `verify-device-token` (:772-903)
Lifecycle: issued at OTP success (90 days, fixed - **never extended** by later successful verifies); stored as the single value in `users.device_token` (single active device per user, all roles incl. admins); overwritten by any later OTP login (any purpose); set to NULL by device-change approval (`requests_functions.py:486-492`, which also NULLs `push_token`); registration stores the client-supplied token with **no expiry** (`device_token_expires_at` omitted, auth_function.py:435).

`verify_device_token` flow:
1. 400 if `contact` or `device_token` empty.
2. Query `users u LEFT JOIN org_info o ON (u.role_id=1 AND o.parent_id=u.user_id) OR (u.role_id IN (2,3) AND u.parent_id=o.org_id) WHERE u.mobile_number=%s` + optional role filter (`role_id==1` -> role 1; 2 or 3 -> roles 2,3) and `fetchone()` with no ORDER BY (:780-796). For a multi-company admin several rows match; the returned `org_id/org_name` is arbitrary (D-9).
3. Branch order (first match wins):

| Condition | HTTP | Body |
|---|---|---|
| status 2 | 200 | `{success:false,message,skip_otp:false,deleted:true}` (:800-806) |
| status 0 | 200 | `{success:false,message,skip_otp:false,inactive:true}` (:808-814) |
| stored token NULL | 200 | `{success:false,message,skip_otp:false,device_change_approved:true,user_id,org_id}` (:820-829) |
| token equal **and** `device_token_expires_at < now` | 200 | `{...,session_expired:true,user_id,org_id}` (:830-839) |
| token equal (NULL expiry never expires) | 200 | `{success:true,message,skip_otp:true,user_id,email,role_id,org_id,org_name,org_logo[,manager_permissions]}`; side effect: updates `push_token` if sent; S3 head+presign for the logo in an executor (:840-877) |
| token different | 200 | auto-calls `submit_device_change_request(user_id, org_id, "Device change requested")` (exceptions swallowed) then `{success:false,message,skip_otp:false,device_mismatch:true,user_id,org_id}` (:878-893) |
| no user | 200 | `{success:false,message:"Device not recognized",skip_otp:false,device_mismatch:false}` (:894-900) |

`request_pending` is **never emitted** (the frontend maps `r.request_pending` -> false, `mappers/auth.ts`). Each mismatch call inserts a **new** pending `requests` row and notifies admins/managers (no de-duplication, `requests_functions.py:150-160`). Admins hit this too: logging an admin in on a second phone overwrites the token, and the first phone's next verify creates a spurious device-change request for the admin (D-6). The expiry check only happens on equality; a mismatching expired token still goes to the mismatch branch.
The frontend ignores the success body's data and re-runs `/auth/login` or `/auth/employee-login` to build the session (`loginService.ts:41-62`).

### 4.4 Force-logout / check-session interplay
* `session_registry._sessions` is a per-process dict `user_id -> Queue` (one queue per user; a second SSE connection replaces the first). `force_logout_user` enqueues "force_logout" (`session_registry.py:16-20`). Callers: `validate_otp` (:745-748) and org deactivate/delete (`gen_dashboard_functions.py:137-139`). Multi-worker/multi-instance deployments lose events; there is no TTL; on stream end `unregister_session(user_id)` removes whichever queue is current.
* Authoritative check for the app is polling `GET /requests/check-session/{user_id}?device_token=`: returns `force_logout:true` when the row is missing, `status = 0` (note: status 2 is **not** treated as inactive - it is a token comparison), token NULL (approved change) or token mismatch. Frontend polls roles 2/3 only (DD-30); fail-open on network error.
* No FCM `force_logout` message exists on the backend (grep: the string appears only in SSE/`check_device_session`), although the new app also listens for a push `force_logout` (`Bootstrap.tsx:21`).
* Device approval clears `push_token` as well (:486-492), so pushes to the old device stop.

### 4.5 Login lookups
* **Admin** (`admin_login`, :953-1036): pre-check by `mobile_number LIMIT 1` -> 403 deleted (status 2) / inactive (0) / employee account (role 2,3). Then `users u JOIN org_info o ON o.parent_id=u.user_id WHERE role_id=1 AND status=1 AND mobile_number=%s LIMIT 1` (**no ORDER BY**, org arbitrary for multi-company admins; the app overrides with its remembered `lastOrg` via `GET /company/details`). Then logo (S3), active plan (`<LP>_subscription` newest ACTIVE), `free_trial` row. Response: `{success,message:"Login successful!",user_id,email,org_name,org_id (alias current_org_id),role_id,org_logo,plan_id|null,is_free_trial (active and end_date >= server date.today()),free_trial_end (string|null),redirect:"home"}`. Not found -> 401.
* **Employee/manager** (`employee_login`, :1038-1108): same pre-checks (admin number -> 403). `users u JOIN org_info o ON u.parent_id=o.org_id WHERE role_id IN (2,3) AND status=1 AND mobile_number=%s LIMIT 1`. Response: `{success,message,user_id,org_name,org_id,role_id,org_logo,redirect:"emp_home"}` + `manager_permissions:[setting_name…]` for role 2 (:1095-1104, ordered by `setting_id`). **No `email`, no `plan_id`/trial fields.**
* `login_id` is documented as "mobile or email" but only `mobile_number` is queried (:964-967).

Shapes the new frontend maps (`src/store/auth.ts:272-366`, `src/api/mappers/auth.ts`):

| Frontend | Reads | Server gap |
|---|---|---|
| `sessionFromAdminLogin(res)` | `user_id`, `role_id` (must be 1), `org_id`‖`current_org_id`‖user_id, `org_name`, `email`, `plan_id`, `is_free_trial`, `free_trial_end`, `org_logo` (separately) | OK. `org_name` is the arbitrary-org name unless overridden |
| `sessionFromStaffLogin(res,{deviceToken})` | `user_id`, `role_id` in {2,3}, `org_id`, `org_name`, `manager_permissions` (role 2), `email` optional; device token comes from the OTP step/stored token | OK; `manager_permissions` only at login (DD-36 stale) |
| `sessionFromRegistration` | `user_id`, `org_id`, `org_name`; email from the form | Registration returns no plan/trial fields; free trial dates only appear at next login |
| `mapDeviceVerifyResult` | `success,skip_otp,deleted,inactive,device_change_approved,session_expired,device_mismatch,request_pending,message,user_id,org_id` | `request_pending` never sent |
| `mapVerifyOtpResult` | `message,device_token,user_exists,role_id` | OK |

### 4.6 Registration (`admin_register_json`, :364-481)
1. Validate `org_name, mobile_number, email, branches` truthy (400) and at least one branch.
2. Duplicate check: `SELECT * FROM users WHERE mobile_number=%s OR email=%s` - **any role, any status** (includes deleted users) -> 400, but converted to **500 "Registration failed"** by the blanket `except Exception` (:376-379, :474-478).
3. Generate `user_id`, `org_id` (uuid4). Insert `org_info` (lat/long 0,0; `parent_id=user_id`; state/district/pincode), `branch_info` rows (`branch_id` = 1-based index of the list position; blank names skipped but the index still advances -> gaps; `gps_location POINT(0,0)`), `dept_info` rows (uuid ids) or default "General" (lookup is redundant: new org).
4. Insert admin `users` row: `role_id=1`, `branch_id=first non-blank branch`, `dept_id NULL`, `device_token` as sent (nullable), `push_token=''`, `org_id=org_id` (UNIQUE index -> one org per admin via this column). `created_at` UTC.
5. Insert `company_additional_info` if any extra field is set (note `gst_number` is mandatory in the router model but may be empty).
6. **commit #1.** Then insert `free_trial` (start = **server local** `date.today()`, end = +1 month via `relativedelta`, `ON CONFLICT (org_id) DO NOTHING`) and **commit #2** (:446-458). A failure after commit #1 leaves a registered admin and a 500 response.
7. Response includes a fresh logo URL (S3 head; normally `null`).
No OTP proof, no e-mail/mobile format validation, no length validation (name/e-mail `varchar(50)` overflow -> 500).

### 4.7 Push tokens
* Stored in `users.push_token` by: `validate_otp` (:733-738), `verify_device_token` (:842-848), `POST /auth/update-push-token` (:144-147; also bumps `updated_at`). Registration stores `''` placeholder (:436).
* Cleared by: device-change approval only (`requests_functions.py:486-492`). `update-push-token` rejects an empty token (400), so the client cannot revoke; **no logout endpoint exists** (P3-7). One token per user: the last device wins; a previous user on the same device keeps receiving pushes until overwritten.
* Sending: `get_fcm_access_token` (:61-115) builds a service-account credential from five `FCM_*` env vars (or a JSON file path), caches the OAuth token 55 min; `_send_push_to_token` (:164-201) posts to FCM v1 with `notification{title,body[,image]}`, `data` (all values stringified), Android channel `attendance_notifications`, priority high, APNs sound default. Non-200 only logged; an invalid token is never cleared (`_clear_admin_push_token`, :286-302, is dead code).
* Recipients for punch pushes: role 1 admin of the org (`org_info.parent_id = u.user_id`) and role 2 users with `parent_id = org_id`, excluding the actor (:227-253); plus a WebSocket `badge_update` to the same set.

### 4.8 Role / permission checks (`permission_checker.py`)
* `check_manager_permission_middleware(user_id, setting_name)` (:7-43): user missing -> False; role 1 -> True; role != 2 -> False; role 2 -> row in `<LP>_manager_permissions` joined to settings with `mp.user_id = user`, `mp.org_id = users.parent_id`, `setting_name`, `status = 1`. Any exception -> False (fails closed, errors swallowed).
* `get_user_permissions` (:116-155): role 1 -> all setting names; role 2 -> granted names ordered by `setting_id`; else `[]`.
* Only real consumers: `GET /managers/permissions/{user_id}` and the two manager report endpoints (`router/report.py:118,146`). `require_permission` (decorator, :45-71) and `PermissionChecker` (:73-114) are unused. Every other route trusts client-sent `role_id`/ids (P3-1, P3-14).
* Gen-admin console: `_require_gen_admin` (any phone found in `gen_admin_login`) and `_require_role1` (role_id 1), both from the `x-admin-id` request header (auth.py:148-184). The header is a bare phone number: anyone who knows an operator phone is authorised.

### 4.9 Database access (`db_function.py`)
* Pool: `psycopg2.pool.SimpleConnectionPool(5, 20)` created lazily at first use (:40-50); not closed at shutdown. `SimpleConnectionPool` is **not thread-safe**; handlers are `async def` so DB calls run on the event-loop thread, but `run_in_executor` helpers must not touch the DB.
* Config from env (`DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_SCHEMA`); `DB_SCHEMA` is mandatory and regex-validated (`^[A-Za-z_][A-Za-z0-9_]*$`); applied as connection option `-c search_path=` and again as `SET search_path TO <schema>` + `commit` on every checkout (:52-63).
* Transactions: autocommit is off. Callers `commit()` explicitly and `rollback()` in `except` blocks. `return_connection` does **not** roll back, so a read-only handler returns the connection with an open (idle-in-transaction) transaction until its next checkout runs the `SET`+`commit`. Several handlers never roll back after a failed statement.
* Exhaustion/failure: if `getconn` or the `SET` statement fails (e.g. connection left in an aborted transaction, or a dead connection after DB restart) the exception handler raises HTTP 500 "Database connection failed" **without returning the checked-out connection** (:58-66) -> pool leak (D-14). More than 20 simultaneous checkouts -> `PoolError` -> same 500.
* No statement timeout, no keepalive, no stale-connection ping.

### 4.10 Error behaviour (wrapped 500s)
* `generate_otp`/`validate_otp`/registration/`update_push_token` re-raise `HTTPException` correctly in some paths (`except HTTPException: raise`, :623-624, :763-764, :154-155) but **registration does not** (:474) so its 400 becomes 500.
* `router/auth.py` upload/logo handlers swallow everything into a bare 500 "Internal server error".
* Gen-admin routes re-raise `HTTPException` properly.
* Several auth endpoints use dict `detail` (`update-push-token`), others strings; business outcomes of `verify-device-token` are HTTP 200 `success:false`.
* The frontend unwraps `"<status>: <detail>"` strings (P2-1) and treats `success:false` as `ApiError` except for `verifyDeviceToken`.

## 5. Side effects & integrations

| Integration | Where | Detail |
|---|---|---|
| MSG91 SMS (OTP) | auth_function.py:908-949, auth.py:55,83 | POST `https://control.msg91.com/api/v5/flow`, headers `authkey`, body `{template_id, short_url:"0", realTimeResponse:"1", recipients:[{mobiles:"91XXXXXXXXXX", numeric:"<otp>"}]}`; success = `result.type == "success"`. Env `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID` (read once at import, :37-40). Template text lives at the provider and carries the legacy brand (P2-10). |
| AWS SES (e-mail OTP) | :304-337 | `ses.send_email(Source=EMAIL_USER, ToAddresses=[email], Subject "Your <Purpose> OTP - <legacy brand>", HTML body "Your OTP is ...; expires in 5 minutes")`; region `AWS_SES_REGION` default `ap-south-1`; `boto3` credentials from the environment. Not awaited by the request. |
| FCM v1 push | :61-201 | see §4.7 |
| S3 | `ml/s3.py` | `org_logo/{org_id}.png`, `employee_profile/{user_id}.jpg\|png`, presigned GET URLs valid 604800 s (7 days); each login does a HEAD (blocking S3 round-trip, run in an executor in auth functions). A secret credential pair is read from env at `ml/s3.py:10-11`. |
| SSE | auth.py:409-446 | `text/event-stream`; events `data: force_logout\n\n`, heartbeat `: heartbeat\n\n` every 30 s |
| WebSocket | `router.ws_badge.notify_badge_update` (punch notifications) | called from `send_punch_in_out_notification` (:259-265) |
| `requests` table + admin pushes | `submit_device_change_request` | triggered by device mismatch (§4.3) |
| Logging | auth_function.py:817-818 | **stored and supplied device tokens are logged at INFO**; auth.py:84 prints gen-admin OTP; punch notification `print()` calls (:213,221) |

## 6. Config & env vars

`DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT, DB_SCHEMA`; `MSG91_AUTH_KEY, MSG91_TEMPLATE_ID`; `EMAIL_USER, AWS_SES_REGION`; `FCM_PROJECT_ID, FCM_PRIVATE_KEY_ID, FCM_PRIVATE_KEY, FCM_CLIENT_EMAIL, FCM_CLIENT_ID, FCM_SERVICE_ACCOUNT_PATH`; `AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_REGION, S3_BUCKET_NAME` (via `ml/s3.py`); boto3 default credential chain for SES. Hard-coded: OTP TTL 5 min, max attempts 5, device-token TTL 90 days, FCM token cache 55 min, FCM channel id, SSE heartbeat 30 s, pool 5-20, bypass dict (auth_function.py:29-34 - **a secret-equivalent exists there**).

## 7. Known defects & risks

| ID | Sev | Where | Finding / impact |
|---|---|---|---|
| D-1 | **Critical** | auth_function.py:28-34, 517-521, 644-671 | Hard-coded **test/review OTP bypass**: four phone numbers (2 admin, 2 employee) accept one static code and skip SMS/DB. `generate_otp` returns success for them for any purpose; `validate_otp` then issues a 90-day device token and, when a user exists, makes the number's account loginable by anyone who knows the number and the static code. Values must be removed/replaced by a config-driven allow-list that is disabled in production (do not copy). |
| D-2 | High | router/auth.py:84 | Gen-admin OTP is written to stdout/logs in clear (`print`). |
| D-3 | High | auth_function.py:585-606, 703-714 | No resend cooldown/rate limit: unlimited OTP regeneration resets `attempts` -> unbounded brute force of a 6-digit code and SMS/e-mail bombing (also `delete_account`, `update_email` for any contact). 5 attempts/OTP only. |
| D-4 | High | router/auth.py:97-136 | Gen-admin verify has no attempt counter; returns no token, and every gen-admin route trusts a bare `x-admin-id` phone (:148-184) -> operator console effectively unauthenticated. |
| D-5 | High | auth_function.py:953-1111 | `/auth/login` and `/auth/employee-login` return `user_id`, `org_id`, role, plan, permissions to anyone who knows a mobile number (no OTP/device proof). `/auth/user-mobile/{id}`, `/auth/push-token-status/{id}` expose PII/tokens by id (P3-1, P3-6, P3-10). |
| D-6 | Medium | auth_function.py:878-893, requests_functions.py:129-167 | Every device-mismatch verify inserts a new pending device-change request and notifies admins (spam, no dedupe); also fires for **admins** because OTP login on a second phone overwrites their single token. |
| D-7 | Medium | auth_function.py:474-478 | Registration wraps its own 400 "User already registered" (and any later failure) into 500 "Registration failed" (P2-3). Duplicate check also matches deleted users (status 2) and employees although login says "create a new account". Free-trial insert is a second commit: partial success returns 500. |
| D-8 | Medium | auth_function.py:918-919 | Country-code logic skips the `91` prefix when a 10-digit mobile starts with `91` -> SMS not delivered to such numbers (login impossible). |
| D-9 | Medium | auth_function.py:783, 983-990 | Multi-company admin: `verify_device_token` and `admin_login` pick an arbitrary org (`LIMIT 1`/`fetchone`, no ORDER BY, and `verify_device_token` returns multiple joined rows). Result can change between calls. |
| D-10 | Medium | router/auth.py:304-328 | `check-mobile`/`check-email` demand a gen-admin `x-admin-id` header (P3-5) and only look at role-1 users, so even with the header they miss employees/deleted accounts that `register` rejects. Neither is rate limited (enumeration if opened). |
| D-11 | Medium | auth_function.py:817-818 | Device tokens (stored and supplied) logged at INFO. |
| D-12 | Medium | auth_function.py:645,660,733 | `validate_otp` overwrites the single device token and triggers SSE force-logout for **any** purpose (delete_account/update_email) and also for the requesting device if its SSE is open. |
| D-13 | Medium | auth_function.py:840-877, 435 | 90-day trust is fixed, never extended; registration-issued tokens never expire (NULL expiry); expiry only evaluated when tokens match. |
| D-14 | High | db_function.py:57-66 | Connection leak: when the post-checkout `SET search_path` fails, the connection is not returned, shrinking the 20-connection pool until the process restarts. `return_connection` also lacks rollback. |
| D-15 | Medium | session_registry.py | In-memory per-process registry: lost with multiple workers/instances/restarts; second SSE connection replaces the first. |
| D-16 | Low | router/auth.py:330-369, ml/s3.py:116-187 | Uploads: no auth, no size/type validation, content type forced; anyone can overwrite any org logo/employee photo; no delete endpoints (P3-8). |
| D-17 | Low | auth_function.py:129-135 | `model_post_init` + legacy `__get_validators__` mix pydantic v1/v2 styles (validator hook is dead in v2). |
| D-18 | Low | DDL: OTP `created_at/expires_at timestamp` | Aware UTC datetimes are compared with `timestamp without time zone`; correct only if the DB session time zone is UTC (UNVERIFIED on RDS). |
| D-19 | Low | auth_function.py:304-337, 614-616 | Delivery failures are invisible to the client (success is returned); no retry/queue. |
| D-20 | Low | permission_checker.py:39-40,151-152 | Exceptions are swallowed (denied/empty), hiding DB faults; unused `require_permission`/`PermissionChecker`. |
| D-21 | Low | auth_function.py:1013-1016, 449-451 | Trial dates use server `date.today()` instead of IST (P2-5). |
| D-22 | Info | `backend_changes.md` §7 | The note "`gst_number` defaults to `''`" refers to the unused model in `auth_function.py:347`; the model FastAPI actually validates is `router/auth.py:24-44`, where `gst_number` and `departments` are **required** (the app sends both, so no break). |

Legacy-brand strings in this domain (for the rename inventory): auth_function.py:327 (OTP e-mail subject), :909 (docstring), table names at :868-869, :999, :1097-1098 and `permission_checker.py:32-33,131,143-144`; `app.py:9-10,75`; `utils/timezone.py:2`.

## 8. Clockyn requirements

Cross-references: `backend_changes.md` (BC) and `docs/DEFERRED_DECISIONS.md` (DD).

### R1 - OTP policy and test accounts (BC P2-7, DD-37; security D-1..D-4)
* Current: see §4.1-4.2. 5 min TTL, 5 attempts, no cooldown, shared static bypass.
* Required: (a) delete `_BYPASS_NUMBERS`; if store-review accounts are needed, read them from env/DB, only when `APP_ENV != production`, and accept the static code only for those users; (b) per-contact **resend cooldown** (e.g. 30 s) and max N sends per hour per contact and per IP -> 429 with `Retry-After`; do **not** reset `attempts` when regenerating inside the TTL (reuse the lock row); (c) use `secrets.randbelow`, store a hash of the OTP; (d) return distinguishable codes: `otp_expired` vs `otp_invalid` plus `attempts_left` (keep the existing `detail` strings for compat); (e) gen-admin verify gets the same attempt counter and no `print` of OTPs; (f) for `delete_account`/`update_email` require that the contact belongs to the caller.
* Outline: `auth_function.py:508-770`, `router/auth.py:53-136`; migration: add `last_sent_at`, `send_count` columns (or a table keyed by contact) to both OTP tables; optionally hashed code column.
* Compat: existing client expects 429 text and 400 text; keep both. DD-37 resend button will call `generate-otp` again.
* Acceptance: 6th wrong attempt -> 429; second generate within cooldown -> 429; regenerate inside TTL does not reset attempts; static code rejected when bypass list empty; OTP never in logs.

### R2 - Device-token lifecycle, `request_pending`, sign-out revocation (BC P1-14, P3-4, P3-7, DD-31, DD-34, DD-30)
* Current: §4.3-4.4, §4.7.
* Required: (1) `verify-device-token` mismatch: before inserting, look for a pending `device_change` request for the same user; return `request_pending:true` (and `request_id`) when one exists, create only when none; keep `device_mismatch:true`; update the message (the legacy copy says "log in again with OTP to take over", while the app offers Request Access only - product to confirm DD-31). (2) Add `POST /auth/logout {user_id, device_token}` that, only if `device_token` matches the stored one, sets `push_token = NULL` and optionally `device_token = NULL`/expiry now; idempotent; return `{success:true}` even when already cleared. (3) Allow `update-push-token` with `push_token: null/""` as an explicit clear **or** keep it strict and rely on `/auth/logout`. (4) Extend `device_token_expires_at` on successful verify (sliding 90 days) or document fixed; set expiry on registration tokens. (5) Decide admin policy (DD-30): either stop overwriting admin tokens and skip mismatch handling for role 1, or keep single-device for admins; at minimum do not create a device-change request for role 1. (6) Optionally send an FCM data message `force_logout` when a token is overwritten (the app listens for it, BC P3-7 area).
* Files: `auth_function.py:772-903, 131-162`, `router/auth.py`, `requests_functions.py:129-167` (dedupe + validate pair), `router/requests.py:57`.
* Compat: new flag and endpoint are additive.
* Acceptance: two mismatching calls -> one request row, second response has `request_pending:true`; logout with wrong token -> no change; after logout no push reaches the device; admin mismatch creates no request.

### R3 - Login response shapes and multi-company `org_id` (BC P1-2 context, DD-33)
* Current: §4.5 table. Admin `org_id` from unordered `LIMIT 1`; `verify-device-token` also unordered and fan-out; employee login omits `email`.
* Required: deterministic selection: `ORDER BY o.created_at ASC, o.id ASC` (or an explicit `org_id` request field / `last_org_id`) in `admin_login` and `verify_device_token`; add optional `org_id` to `AdminLoginRequest` so the app can request its remembered org in one call (`orgOverride` today needs a second `/company/details` call); return `email` in employee login for symmetry; return `plan_id, is_free_trial, free_trial_end` in the registration response (zero extra queries needed: trial dates are known) and in `verify-device-token` for admins; keep `current_org_id` alias.
* Files: `auth_function.py:783-790, 983-1031, 464-472`.
* Acceptance: admin with 2 orgs gets the same org on repeated logins; passing `org_id` of an org the admin owns returns it; foreign org id -> 403.

### R4 - `check-mobile` / `check-email` without an operator header (BC P3-5, DD-38)
* Current: 403 for the app (needs `x-admin-id` of a gen-admin).
* Required: make both endpoints public but rate-limited, returning only `{exists}`, checking **all** users regardless of role/status the same way `admin_register_json` does (:376) (or drop the pre-check and return a typed 409 from `register`). Add a unique index or at least a locked check for mobile and e-mail to close races.
* Files: `router/auth.py:304-328`; DDL migration for unique partial indexes after de-duplicating data.
* Acceptance: unauthenticated call returns `{exists:true}` for an existing employee mobile; 429 after N calls/min; register of a duplicate returns 400/409 (see R5).

### R5 - Error behaviour: unwrap 500s (BC P2-1, P2-3)
* Required: `except HTTPException: raise` before the blanket handler in `admin_register_json` (:474), and apply the same pattern to the `router/auth.py` upload handlers; move free-trial insert inside the first transaction (single commit) so a failure rolls back the registration; return one error envelope with `detail` string. Keep the frontend's unwrap logic working (it tolerates both).
* Acceptance: duplicate registration -> HTTP 400 `"User already registered"`; forced failure of the trial insert leaves no user/org rows.

### R6 - Registration contract (DD-38, BC §7 note)
* Required: server-side proof that mobile and e-mail OTPs were verified for the registration (set `is_verified=true` instead of deleting the row, or return a signed short-lived `otp_proof` and require it in `/auth/register`); relax `gst_number` to optional with default `''` in the router model; validate formats/lengths (name/e-mail ≤ 50, mobile 10 digits, pincode 6 digits); branch ids contiguous; remove `/auth/register-company` alias or document it for the "add company" flow; response additions per R3.
* Acceptance: register without proof -> 400; 51-char name -> 422 not 500.

### R7 - Push credentials for the new bundle (BC P0-9, C-2)
* Current: single service account from five `FCM_*` vars (:49-55, 61-115). A token from the new Firebase project cannot be sent with the old credentials.
* Required: support the new project: either swap the five variables, or store `push_provider/project` with the token (new column `users.push_project`, set from a header/field on `update-push-token`/`validate-otp`) and cache one OAuth token per project; keep Android channel id `attendance_notifications` (:182); fix legacy-brand copy in notifications. Requires the iOS APNs key at the new project (owner config).
* Files: `auth_function.py:49-201`, `router/chat.py:28-72` (second sender duplicating the logic).
* Acceptance: a token issued under project B is delivered when B credentials are configured; invalid tokens (`UNREGISTERED`) clear `users.push_token`.

### R8 - Permissions (BC P1-17, P3-14, DD-36, DD-51)
* Current: §4.8; permission list only at login; `GET /managers/permissions/{user_id}` exists and can serve a refresh.
* Required: keep names exactly as seeded in `<LP>_settings` (frontend compares strings); expose permissions in `check-session` response or reuse `/managers/permissions/{id}` for a foreground refresh (DD-36); introduce a real authenticated caller (token) and an enforcement dependency replacing the unused `require_permission` before enabling server-side scoping; do not swallow DB errors into `False` (return 503).
* Acceptance: revoking a permission is visible to the manager within the refresh interval; a manager without `Attendance Reports` gets 403 on the manager report endpoints (already true).

### R9 - Authentication token (BC P3-1, DD-33)
* Required (cross-domain): issue a signed token (JWT/opaque) at `validate-otp` success and at `/auth/login`/`employee-login` after proof of device trust; verify on all routes; derive `user_id/org_id/role_id`. Until then, at minimum require device proof (`device_token`) on `/auth/login`, `/auth/employee-login`, `/auth/user-mobile`, uploads, `update-push-token`.
* Compat: the app has one header insertion point (`src/lib/api/client.ts`).

### R10 - Housekeeping tied to the rename (BC P2-10, P3-10, P2-15)
* Move the OTP e-mail subject/body brand, SMS/WhatsApp template names and app links to config; delete `/auth/push-token-status`, token logging, `print` calls; delete or protect `/auth/register-company`, `/auth/session-events` if the SSE path stays unused; fix the pool leak (D-14) by returning the connection in the `except` branch and rolling back in `return_connection`.

## 9. Test notes

* No automated tests exist in the repo for this domain (UNVERIFIED: none found under the backend tree). Needed fixtures: a test schema with `users` (roles 1/2/3, status 0/1/2), `org_info` (one admin with 2 orgs), OTP tables, `free_trial`, `<LP>_subscription`, permission tables, `gen_admin_login`; fake MSG91/SES/S3/FCM (mock `httpx`, `boto3`).
* Critical cases: OTP happy path, wrong code x5 then 429 (correct code afterwards still 429), expiry boundary (5:00), regenerate-while-locked 429, regeneration resets attempts (regression for R1), email vs mobile table selection, bypass accounts disabled (R1), registration with duplicate mobile/e-mail (status 400 expected after R5), registration partial failure rollback, verify-device-token every branch of §4.3 (deleted, inactive, NULL token, expired, match + push token update, mismatch creating exactly one request, unknown), admin vs staff role filter, multi-company admin determinism (R3), `check-session` reasons, `update-push-token` empty/unknown user, upload logo/photo round trip with mocked S3, `x-admin-id` gates (role 1 vs other roles), SSE force-logout delivery within one process, pool exhaustion (21 concurrent) and leak regression (D-14).
* Frontend contract tests: `src/features/auth/__tests__/loginMachine.test.ts`, `loginService.test.ts`, `src/store/__tests__/auth.test.ts` pin the shapes in §4.5; the mock server under `e2e/mock-server/` is the reference envelope.

## Sign-in mode switch (`USER_AUTH`)

Added with the Clockyn app. One environment variable chooses how users prove who they are; the code of the other mode stays in place.

| `USER_AUTH` | Identifier | Code delivery | Notes |
|---|---|---|---|
| `otp` (default, also when unset or invalid) | 10-digit mobile number | SMS through MSG91 (unchanged) | review/test bypass numbers still work |
| `email` | email address (compared case-insensitively) | email through SES (`EMAIL_USER` sender, `AWS_SES_REGION`) | no SMS is ever sent; `generate-otp` / `validate-otp` answer 400 for a phone number, so the bypass numbers are inert; a failed SES send answers 503 and the stored code is dropped (in `otp` mode the email send stays fire-and-forget) |

- `GET /auth/config` (public) returns `{"success": true, "user_auth": "otp" | "email"}`; the app reads it before drawing the login and registration screens and treats a missing route as `otp`.
- `core_functions/auth_function.py`: `generate_otp`, `validate_otp`, `verify_device_token`, `admin_login` and `employee_login` accept either identifier (`_contact_clause`: `LOWER(email) = LOWER(%s)` when the contact contains `@`, else `mobile_number = %s`). The behaviour for a mobile number is unchanged.
- An email can identify only one active account per role group: staff emails are only unique inside a company, so an email shared by two active accounts answers 409 ("linked to more than one account") instead of picking one.
- Registration in `email` mode verifies only the email (the app collects the mobile number as plain data); the existing `purpose` values are unchanged and the OTP tables are the existing `email_otp_verification` / `moblie_otp_verification`.
- Staff need an email on their record to sign in when `USER_AUTH=email`; the add-staff form already treats it as optional, so admins must fill it in.
- The platform-operator login (`/auth/admin-login`, `router/auth.py`) still sends its SMS through MSG91; it is not part of the Clockyn app.
- Tests: `tests/test_user_auth_mode.py` (DB-free). Not yet exercised against a real SES account or database.

### OTP safety switches (`OTP_BYPASS`, send throttle)

- **`OTP_BYPASS`** (env, default off, read on every call; `1`/`true`/`yes` turn it on). The hard-coded review numbers in `_BYPASS_NUMBERS` accept the static code `123456` only while it is on, and only with `USER_AUTH=otp`. With it off those numbers take the normal path (real code, real SMS, throttled). A startup warning is logged when it is on. **Store-review test accounts stop working in `otp` mode until the variable is set.**
- **Send throttle** on `POST /auth/generate-otp` for every non-bypass contact, both channels, every purpose: at most `OTP_MAX_PER_HOUR` (default 5) codes per contact per rolling hour and at least `OTP_MIN_GAP_SECONDS` (default 30) between two. Invalid env values fall back to the defaults. A violation answers HTTP 429 ("Please wait N seconds before requesting another code." / "Too many codes requested. Try again later.", with a `Retry-After` header) **before** a new code is stored or sent, so no SMS/SES cost is incurred and the previous code stays valid. The existing `attempts >= 5` lock is unchanged and is checked first.
- Counted in table `otp_send_log (id, contact, sent_at)` (index on `contact, sent_at`; rows older than 24 h are deleted on insert; e-mail contacts are stored lower-cased). A row is written only when the code is really stored (after validation, phone-in-email-mode and role-mismatch checks) and is removed again if the e-mail send fails in `email` mode. Apply `migrations/024_otp_send_log.sql` before deploying - without the table `generate-otp` answers 500.
- Hygiene done alongside: no OTP code, device token or push token is logged any more (only the last four characters of a contact or token), and the debug route `GET /auth/push-token-status/{user_id}` was removed (the app never called it).
- Tests: `tests/test_otp_throttle.py`, `tests/test_user_auth_mode.py` (DB-free).

## Branding and configuration (WP11 Changes)
- Every brand, contact and seller string is read through `utils/branding.py` (env on every call): `APP_NAME` (default `Clockyn`), `SUPPORT_EMAIL`, `SUPPORT_URL`, `WEBSITE_URL`, `SELLER_NAME/ADDRESS/GSTIN/CIN/EMAIL/PHONE/SAC/PLACE_OF_SUPPLY`, `ANDROID_STORE_URL`, `IOS_STORE_URL`, `BRAND_LOGO_FILE`, `BRAND_SIGNATURE_FILE`. Unset contact/legal values are left out of the text, nothing is invented.
- The OTP e-mail subject is `Your <Purpose> OTP - <APP_NAME>`, the body names the product and adds a support line when `SUPPORT_EMAIL` is set. The suggestion e-mail heading, the invoice e-mail/PDF/file names and `app.py` (OpenAPI title, `GET /` text) follow `APP_NAME`.
- WhatsApp staff invitation: no built-in template name. `MSG91_WHATSAPP_TEMPLATE` must be set, and the link is `ANDROID_STORE_URL`, else `IOS_STORE_URL`, else `WEBSITE_URL`; when either is missing the invitation is skipped and a `[WA SKIP]` warning is logged (the add-staff call is unaffected).
- `CORS_ALLOWED_ORIGINS` (comma list; unset or `*` = today's `*`). With a list only listed origins (plus the file:// `null` origin) receive CORS headers, with `Vary: Origin`; the mobile app sends no `Origin` and is unaffected.
- Tests: `tests/test_branding.py` (legacy-name guard, branding defaults/overrides, e-mail/PDF/WhatsApp text from env, CORS). Rename status per item: `rename_inventory.md` section 1a.

## Single device, device-change requests and sign-out (WP7 Changes)

Decision D-11: managers and staff (roles 2/3) keep **one** device; admins (role 1) stay multi-device. Behind env **`ENFORCE_SINGLE_DEVICE`** (`1`/`true`/`yes`/`on`, read on every call, default off).

| | `ENFORCE_SINGLE_DEVICE` off (default, as before) | on |
|---|---|---|
| `validate-otp`, `purpose=login`, role 2/3, stored `device_token` set, not expired, different from the supplied one | OTP take-over: the new device overwrites the token, the old one is signed out | **409 `{"detail":"device_mismatch"}`**, the OTP is consumed, the token is untouched, a device-change request is filed (deduped); the active device keeps its session |
| same, token NULL (an approved device change cleared it), expired, or the same device | normal login | normal login |
| `validate-otp`, role 1 (admin) | takes over as before (legacy SSE force-logout signal sent) | normal login, **no** legacy SSE signal to the other device |
| `verify-device-token` mismatch, role 2/3 | `device_mismatch:true`, message "This account is already logged in on another device. Please log in again with OTP to take over the session." | `device_mismatch:true`, message "This account is active on another device. Request access from your admin." |
| `verify-device-token` mismatch, role 1 | auto-files a request as before | `device_mismatch:true`, `request_pending:false`, message "Please log in again with OTP on this device.", **no request** |
| `GET /requests/check-session` for an admin with another/missing token | `force_logout:true` | `valid:true` (inactive or unknown accounts are still signed out) |

* **`request_pending`** (additive, both modes): `verify-device-token` mismatch responses carry `request_pending:true` when a pending device-change request exists for the user (the request was just filed or already existed); `false` when none could be filed (for example the rate limit). The app shows its "waiting for admin" state from it.
* **Dedupe and rate limit** (`submit_device_change_request`, used by `POST /requests/device-change/submit` and by the login flows): one pending request per user (a repeat returns the existing id with `already_pending:true`); at most 3 requests per user per rolling 24 hours (HTTP 429 "Too many device change requests. Please try again later."). The login flows swallow the 429 and report `request_pending:false`.
* **Approval** of a device change clears `users.device_token`, `device_token_expires_at`, `users.push_token` and all `user_push_tokens` rows of the user (once; a repeated approval does not); a **rejection** keeps them. After approval `check-session` answers `device_change_approved` and `verify-device-token` answers `device_change_approved:true` until the user signs in with an OTP on the new device.
* **`POST /auth/logout`**: see endpoint row 24.
* Tests: `tests/test_device_flow.py` (DB-free). Not exercised against a real database: the `SELECT device_token, device_token_expires_at, parent_id FROM users` read in `validate_otp`, the `UPDATE users SET device_token = NULL ...` in `logout_user`, and the `pg_advisory_xact_lock` / `now() - interval '24 hours'` statements in `submit_device_change_request`.


## Authentication tokens (WP12 Changes)

Decisions applied: D-18 (bearer token at login, verified on every route, rolled out behind `REQUIRE_AUTH`, default OFF), D-10 (manager scope: deny by default for areas without a permission), D-11 (admins multi-device, roles 2/3 single device). Files: `utils/auth_token.py` (token + settings, pure), `core_functions/auth_policy.py` (policy as data, pure), `core_functions/auth_dependency.py` (the guard, caches, issue / revoke helpers, WebSocket handshake), migration `033_user_auth_epoch.sql` (`users.auth_epoch int4 NOT NULL DEFAULT 0`).

### Token
* HS256 compact token (standard library `hmac`/`hashlib`/`base64`, no new dependency; readable by any JWT tool). Header `{"alg":"HS256","typ":"JWT"}`; claims `sub` (user_id), `org` (users.org_id for an admin, users.parent_id for manager/staff), `role`, `iat`, `exp`, `ep` (auth epoch).
* Verification: exactly three base64url segments (max 2048 characters), header `alg` must be `HS256` (so `none`, other HS/RS algorithms and key-confusion are refused before the signature is looked at), constant-time signature compare (`hmac.compare_digest`), claim types checked, 60 s clock skew both ways (`iat` in the future beyond 60 s is invalid; `exp + 60 s` passed is "expired").
* Env (read on every call): `AUTH_TOKEN_SECRET` (>= 32 characters; shorter counts as not configured), `AUTH_TOKEN_TTL_DAYS` (default 90 = device-token TTL; outside 1..3650 -> 90), `REQUIRE_AUTH`, `AUTH_EPOCH_CACHE_SECONDS` (default 30, 0 = off).
* `validate_auth_config()` runs as the first startup hook in `app.py`: `REQUIRE_AUTH=1` with a missing/short secret raises `AuthConfigError` (the app does not start). Off with a secret only logs a summary.

### Issuance (additive response fields)
`POST /auth/validate-otp` (valid OTP and the user exists; also the OTP_BYPASS test numbers), `POST /auth/verify-device-token` (success, `skip_otp:true`) and `POST /auth/register` add `access_token` (string) and `expires_in` (seconds, = TTL). Nothing else in those bodies changes; without a usable `AUTH_TOKEN_SECRET` (or a database without `auth_epoch`) the fields are simply absent. The token is built from the user's CURRENT row in the same connection (`issue_for_user`, inside a savepoint, never raises). `/auth/login` and `/auth/employee-login` stay public and token-less (the app calls them after the OTP step that already returned the token).

### Request handling (`auth_guard`, ONE app-level dependency)
Installed by `app.py` right after the error handlers and BEFORE the routers (`_install_auth(app)`), so FastAPI runs it for every HTTP route after routing (path parameter names known) and before the handler. WebSocket scopes are skipped by it (handlers call `authenticate_websocket`). No middleware: nothing buffers bodies or breaks streaming / multipart uploads.
1. Classify `(method, path)` with `auth_policy.classify`: `public`, `operator`, `authenticated` (default).
2. Parse `Authorization: Bearer <token>`; a valid token plus the current database row gives `request.state.identity` (`Identity(user_id, org_id, role_id, epoch)`; role and organisation come from the database row, so a demotion is effective within the cache window) and `request.state.allowed_orgs`.
3. `REQUIRE_AUTH` off: nothing is enforced, a missing/invalid/expired/revoked token is ignored (debug log), a valid one only identifies the caller. Behaviour is exactly as before.
4. `REQUIRE_AUTH` on, public route: pass (identity still attached when a good token was sent). Operator route: an app token is never accepted (403 `Forbidden`); without a token the request passes on to the operator mechanism of the handler (`_require_gen_admin`, hardened separately). Authenticated route: no/invalid/revoked token -> 401 `{"detail":"Not authenticated"}` (`"Token expired"` when only `exp` failed), `WWW-Authenticate: Bearer`; a lookup failure (database) -> 503 `Authentication temporarily unavailable` (fail closed); then role / manager permission (403), then ownership of ids (403 `{"detail":"Forbidden"}`; the log line has actor, path, source:key, never a token or the id value).

### Revocation (`users.auth_epoch`)
A token is valid only while its `ep` equals the user's current `auth_epoch` AND `users.status = 1` (deleted / deactivated users are cut off within the cache window; no epoch bump is needed for that). Epoch bumps (`UPDATE users SET auth_epoch = auth_epoch + 1 ... AND role_id IN (2, 3)`, in a savepoint so a missing column never breaks the transaction; only when `AUTH_TOKEN_SECRET` is configured):

| Event | Where | Bump |
|---|---|---|
| device-change request approved (first approval) | `requests_functions.update_request_status` | the requester (roles 2/3) |
| `validate-otp` `purpose=login` that takes over another device (role 2/3, `_snapshot_previous_device` says takeover) | `auth_function.validate_otp` (the OTP_BYPASS branch uses `device_token IS DISTINCT FROM`) | the user, BEFORE the token of the new login is issued (the new token carries the new epoch) |
| `POST /auth/logout` that really cleared the device token (role 2/3) | `auth_function.logout_user` (rowcount > 0; a stale device revokes nothing) | the user |
| admin logout / admin second-device login | - | never (admins are multi-device) |

The epoch/status/role/organisation lookup is cached in-process for `AUTH_EPOCH_CACHE_SECONDS` (30 s; the same cache holds the owner of ids and manager permissions); a bump made by this process drops the entry immediately (and again after the commit); another process notices within the cache time. Warm cache = no database round trip per request.

### Public list (explicit data, `auth_policy.PUBLIC_RULES`)
`/`, `/health`, `/ready`, docs/openapi, `/version/check`, `/auth/config`, `/auth/generate-otp`, `/auth/validate-otp`, `/auth/verify-device-token`, `/auth/login`, `/auth/employee-login`, `/auth/register`, `/auth/register-company`, `/auth/logout`, `/auth/admin-login`, `/auth/admin-login/verify-otp` (operator phone login), `/auth/check-mobile/*`, `/auth/check-email/*`, `/auth/org-logo/*`, `GET /profile-photo/{id}` (K2: only the READ is public; `POST /profile-photo/{id}` needs the owner or an administrator / manager of that user's organisation), `GET /invoice/download` (K2: signed link or the handler's own admin-token check), `/auth/session-events/*` (device_token query), `POST /requests/device-change/submit` (pre-login; the 1-pending + 3-per-24 h rule applies), `/webhook/*` (HMAC signature). There is no public static-file prefix (no such route is mounted; files come from S3 presigned URLs).

Operator list (`OPERATOR_RULES`, never satisfied by an app token): `/gen-dashboard/*`, `/auth/gen-admin-users*`, plan/coupon administration (`/plans/coupons*`, `GET /plans/all`, `POST /plans`, `PUT|DELETE /plans/{id}`), `GET /support/requests/all`, `PUT /support/update/{id}`, `GET /company/deletion-requests`, `GET /company/suggest-feature`, `GET /company/suggest-feature/with-replies`, `POST /company/suggest-feature/{id}/reply`, `GET /invoice/bulk-download`, `GET /attribution/reports/meta-attribution`.

### Role policy (`auth_policy.ROLE_RULES`, first match wins, unlisted = any authenticated user of the same organisation)
* Admin only: `/orders/*`, `/payments/*`, `/invoice/*`, `POST /managers/assign`, `DELETE /managers/{id}`, `POST /managers/{id}/permissions`, `POST /api/attendance-rule/{org}`, `DELETE /company/delete/{org}/{user}`, `DELETE /addstaff/{id}/permanent`, `DELETE /staff-location/cleanup`, `POST /auth/upload-org-logo/{org}`, department writes.
* Admin, or manager holding the permission (exact `settings` names, any-of): Staff Management (`/addstaff` writes), Branch Management (`/branch` writes), Shift Management (`/shift` writes), Holiday Management (`/api/holidays` writes), Salary Management (`/api/salary`, `/api/wages`, `/api/daily-wages`, `/api/hourly-charges`, `/api/advance`, `/salary`; own payslip / own advances stay open to every role), Incentive Management (`/api/incentive`), Attendance Reports (`/api/reports`; overtime report: either of the two), Leads Management (assign, categories, carry-forward actions, assignment edits), Company Settings (`POST /company/add`, `PUT /company/update`, `POST /company/update-logo`), Requests Management (`POST /requests/update-status`). A manager without it gets 403 `Access denied. Required permission: <name>` only when `REQUIRE_AUTH=1`.
* Admin or manager (no finer permission known): `GET /requests/admin/{org}`, `/expenses/admin/*`, announcement create/list/delete, staff-location reads, `/home/staff-locations`, `/home/staff-punch-locations`, `PUT /home/update-attendance-punch`.
* `tests/test_auth_policy.py::STAFF_CALLS` (about 85 calls copied from the app's staff screens, derivation in the file) asserts that no endpoint the staff / manager-as-employee screens call is restricted and that every one exists in the route table.

### Ownership of ids (with `REQUIRE_AUTH=1`, authenticated routes)
Keys are data (`ORG_KEYS`, `USER_KEYS`, `SELF_KEYS`); looked at in path parameters, query parameters, top-level JSON keys and form fields (multipart fields are read only when FastAPI already parsed them; JSON only when already read or `Content-Length` <= 64 KB; the bytes stay cached so the handler reads the body unchanged).
* `org_id` must be an organisation of the caller (an admin may own several: `org_info.parent_id`; the list is cached and re-read at most every 5 s when an id is not in it, so a freshly added company works). A manager may also send their OWN user id as `org_id` (the app's convention for manager-scoped dashboards).
* `user_id` / `staff_id` / `employee_id`: the caller's own id, or an organisation id of the caller (the app sends the org id under `user_id` on `/home/*`, `/addstaff/*`, `/home/org-admin/{user_id}`; an org id cannot select a user). Staff: nothing else. Admin / manager: also any user whose organisation is one of theirs (one cached lookup per target id); an id that exists nowhere is passed through (handler 404). `GET /auth/employee-profile/{user_id}` is exempt (colleague photo URLs in chat lists).
* `sender_id`, `action_by`: always the caller. Exception: on `PUT /chat/mark-read` the viewer is `receiver_id` (the caller) and `sender_id` is the conversation partner (`KEY_KIND_OVERRIDES`).
* `receiver_id`, `other_user_id` (the chat partner; user keys with the PEER rule, G4-A): like `user_id`, except that STAFF may also name a colleague: the target's organisation is looked up (for an admin who owns several organisations, all of them) and must be one of the caller's; an id that exists nowhere passes to the handler's 404. List keys (`member_ids`, `user_ids`, `staff_ids`) and single-resource ids (`group_id`, `incentive_id`, `leads_id`, `expense_id`, `temp_id`, `request_id`) are NOT guard keys: the handlers check them (see "Resource scoping (G4-A Changes)").
* `tests/test_idor_matrix.py`: role x key x path/query/body/form x own/other organisation.

### WebSockets
`/ws/chat/{user_id}`, `/ws/group-chat/{group_id}/{user_id}`, `/ws/badge/{user_id}`: `authenticate_websocket` runs before `accept`. Off: always allowed. On: `?token=` must be a valid, non-revoked token whose user equals the path user, else the handshake is refused with close code **4401**. `/auth/session-events/{user_id}?device_token=` keeps its own device_token check.

### Rollout
1. Deploy the backend, apply `033`, set `AUTH_TOKEN_SECRET` (tokens start being issued; `REQUIRE_AUTH` stays off). 2. Ship the app version that stores `access_token` and sends the header / `?token=`. 3. When the old app versions are gone (`/version/check` `min_supported`), set `REQUIRE_AUTH=1` (the app will not start without the secret). Roll back by unsetting `REQUIRE_AUTH`. Rotating `AUTH_TOKEN_SECRET` signs everybody out.

### Known gaps (for the owner / other work packages)
* `POST /auth/logout`, `/auth/login`, `/auth/employee-login`, `/auth/check-*` are public by the app contract: logout needs only a user_id (it can sign a staff member out: already true before, now it also revokes tokens). K2 closed `POST /profile-photo/{user_id}` in the policy (public for GET only); the app must therefore send its token on that POST before `REQUIRE_AUTH=1` is switched on: `PUBLIC_PREFIXES` in `src/lib/api/client.ts` still lists `/profile-photo/` for every method, so the app sends NO `Authorization` header there today (remove the entry or make it GET-only).
* Account delete / deactivate code paths do not bump the epoch themselves: the `status` check cuts the token off within 30 s (cache), but re-activating a user would make its old token valid again until it expires.
* Manager data scope (branch / department assignments, D-10 server scope) is WP6; this layer only enforces role, permission and organisation.
* The operator routes: hardened by K2 (see "Hardening of open endpoints" below). With `REQUIRE_AUTH=1` an app token on them is a 403.
* Not exercised against a real database: `_USER_STATE_SQL`, `_OWNED_ORGS_SQL`, `_ENTITY_USER_SQL`, `_ENTITY_ORG_SQL`, `_PERMISSIONS_SQL`, `_BUMP_SQL`, `_TAKEOVER_SQL` (`core_functions/auth_dependency.py`; parsed with pglast and checked against the DDL), the savepoint wrappers, and migration `033`.
* Tests: `tests/test_auth_token.py`, `test_auth_policy.py`, `test_auth_guard.py`, `test_idor_matrix.py`, `test_route_auth_matrix.py` (all DB-free).


## Hardening of open endpoints (WP12 K2 Changes)

Files: `core_functions/operator_auth.py`, `core_functions/owner_guard.py`, `utils/uploads.py`, `utils/signed_urls.py`, `utils/rate_limit.py`; additions in `core_functions/auth_policy.py`; wiring in `router/auth.py`, `gen_dashboard.py`, `gen_plans.py`, `support.py`, `company.py`, `attribution.py`, `profile_photo.py`, `face_attendance.py`, `emp_expenses.py`, `chat.py`, `requests.py`, `utils/email.py`. No migration. Every default keeps today's behaviour while `REQUIRE_AUTH` is off.

### Operator guard (section 3 rows 15-16, 24-27 and every `OPERATOR_RULES` route)
* How the operator signs in TODAY (unchanged): `POST /auth/admin-login` (phone of a `gen_admin_login` row -> SMS code) then `POST /auth/admin-login/verify-otp` -> `{success, phone_number, role_id}` and NO token. The web panel then sends the phone as header `x-admin-id`; `/auth/gen-admin-users*` (role_id 1), `/auth/check-mobile`, `/auth/check-email` looked it up, `/gen-dashboard/*` and the other operator routes checked nothing. The header proves only that the caller knows an operator's phone number.
* New: ONE guard for all of them, `operator_auth.authorize` (route dependencies `require_operator` / `require_operator_login` / `require_operator_super`; `_require_gen_admin` and `_require_role1` of router/auth.py are thin wrappers). Credentials: (1) `X-Operator-Key` = env `OPERATOR_API_KEY` (>= 24 characters, SHA-256 digests compared with `hmac.compare_digest`; header sent while the env is unset: 503, wrong key: 403, a wrong key never falls back to the legacy header); (2) the legacy `x-admin-id` `gen_admin_login` lookup (`OPERATOR_LEGACY_ADMIN_ID=0` disables it: key-only mode). An app access token is never read (`Authorization` is ignored here; with `REQUIRE_AUTH=1` the global guard answers 403 to an app token on these routes first).
* Enforcement: on when `REQUIRE_AUTH` is on or `OPERATOR_ENFORCE=1` (`OPERATOR_ENFORCE=0` forces it off). While it is off, routes that were open stay open (`/gen-dashboard/*`, plan/coupon admin, support / suggestion / deletion lists, bulk download, attribution report) and routes that always demanded the header (`/auth/gen-admin-users*` role 1, `check-mobile`, `check-email`) keep demanding it, now also accepting the key. When on and nothing valid is sent: 403 (503 if neither mechanism is configured). Role: role_id 1 for operator administration, any operator for the rest (role 2 may still write plans / coupons / organisations, as before: the dashboard's read-only role is not enforced yet).
* Brute force: failed credentials are counted per IP (rule `OPERATOR_FAIL`, 20 per minute); beyond it every request from that IP gets 429, even with the correct key.
* Recommended production setting: `OPERATOR_API_KEY` set, the web panel (or a small server-side proxy in front of it; do not ship the key inside a public single-page bundle) sending `X-Operator-Key`, then `OPERATOR_LEGACY_ADMIN_ID=0` and `OPERATOR_ENFORCE=1`.
* New route `GET /invoice/bulk-download-link?from_date&to_date` (operator): `{success, url, expires_in}` with a signed link of `/invoice/bulk-download`, which accepts either a valid signature or the operator credential.

### Closed endpoints
* `POST /profile-photo/{user_id}`: no longer public (policy: public for GET only); `REQUIRE_AUTH=1`: the owner, or an administrator / manager of the user's organisation (id ownership of the global guard). The app must send its token on that POST first (see Known gaps).
* Owner only (`auth_policy.OWNER_ONLY_RULES`, dependency `owner_guard.require_owner`; checked whenever a token identifies the caller): `GET /auth/user-mobile/{id}`, `DELETE /company/delete-account/{id}`, `POST /company/request-deletion/{id}`. Without a token nothing changes while the flag is off.
* Reviewed: there is no `/face/cleanup*`, `/face/delete*` or debug / reset / purge route. `DELETE /staff-location/cleanup`, company / staff / manager deletes are admin-only (role rules); the only other destructive routes open to every signed-in user are `DELETE /chat/delete/{user_id}/{message_id}` and `DELETE /api/leads/{id}` (asserted in `tests/test_open_endpoints.py`).
* Tests: `tests/test_operator_auth.py`, `test_open_endpoints.py` (matrix in the style of `test_idor_matrix.py`).

### Uploads (`utils/uploads.py`)
`read_upload(file, max_mb, allowed_types)` streams 64 KB chunks, 413 above the limit (the declared size is checked first, a wrong type is refused after the first chunk), 415 when the first bytes are not png / jpeg / webp / gif (/ pdf where allowed), 400 for an empty file; `Content-Type` and the file name are never trusted. `UPLOAD_MAX_MB` (default 10) for face photos, logos, profile photos, support photos, odometer and travel / bill receipts (images + pdf for bills); `CHAT_UPLOAD_MAX_MB` (default 25, any file type) for chat attachments. Face routes validate then rewind the file (`check_upload`), so `register_face` / `punch_with_face` are unchanged. Response shapes are unchanged; the new errors are 413 / 415 / 400. Tests: `tests/test_uploads.py`.

**HEIC / HEIF (older iPhone builds).** `sniff_type` also recognises an ISO base media `ftyp` box at offset 4 whose major brand is `heic`, `heix`, `hevc`, `hevx`, `mif1` or `msf1` (kind `heic`); other brands (`mp42`, `isom`, `qt  `, `avif` ...) stay 415. It is accepted ONLY where the endpoint passes `allow_heic=True` to `read_upload` / `check_upload`: org logo (`/auth/upload-org-logo`, `/company/update-logo`), employee / staff profile photo (`/auth/upload-employee-profile`, `/profile-photo`), odometer and travel / bill receipts, support photo. Chat attachments take any content already; a HEIC body is stored with content type `image/heic` and a `.heic` name. The face register / punch routes never set the flag (the ML decoder cannot read HEIC) and keep answering 415. No server-side conversion and no new dependency: the file is stored as received under a `.heic` key (`org_logo/{org}.heic`, `employee_profile/{user}.heic`, `odo_meter_photos/.../{date}_{type}.heic`, `travel_bill_photos/.../{expense}.heic`) with content type `image/heic`; `ml.s3.put_image_variant` removes the stale primary-extension object (needs `s3:DeleteObject` on those prefixes; failure is logged, and the old picture could then keep showing) and the getters look at the usual key first, then `.heic`. The size limits are unchanged. The Clockyn app converts every picked photo to JPEG before upload (`src/lib/platform/imageNormalize.ts`), so this path only serves older builds.

### Signed invoice links (`utils/signed_urls.py`)
HMAC-SHA256 (`AUTH_TOKEN_SECRET`) over path + sorted query + `exp` (default 15 minutes, `SIGNED_URL_TTL_SECONDS`), constant-time compare. `GET /invoice/history` rows: `download_url` is now a signed link (same path and parameters plus `exp`, `sig`; unchanged plain link while no secret is configured). `GET /invoice/download` is public in the policy and gates itself: a valid signature, or (flag off) the legacy unsigned call exactly as before (a bad / expired signature is ignored), or (flag on) a valid administrator token whose organisation owns the invoice (401 / 403 otherwise). Report PDFs are `/api/reports/*` calls that the app already makes with its bearer header, so they stay token + role protected, not signed. Tests: `tests/test_signed_urls.py`.

### Rate limits (`utils/rate_limit.py`)
In-memory sliding window, **per instance** (N instances = N times the limit, a restart resets it). Per user: support ticket and suggestion creation 10/hour. Per IP: `check-mobile` / `check-email` 30/min, `login` / `employee-login` / `admin-login/verify-otp` 30/min, `generate-otp` and `admin-login` 20/hour (on top of the per-contact throttle), `requests/device-change/submit` 10/hour. 429 `Too many requests. Please try again later.` + `Retry-After`. `RATE_LIMIT_ENABLED=0` disables all, `RATE_LIMIT_<RULE>=<count>/<seconds>|off` overrides one, `TRUST_PROXY=1` takes the first `X-Forwarded-For` hop as the client IP (off by default so a client cannot pick its own). Tests: `tests/test_rate_limit.py`.


## Resource scoping and public duplicate checks (G4-A Changes)
* **QW-13**: `GET /auth/check-mobile/{mobile}` and `GET /auth/check-email/{email}` are PUBLIC (they were already in the public policy list, but the handlers still demanded the operator credential, so the registration duplicate check of the app always failed). They answer only `{"exists": bool}`; email is compared case-insensitively; the per-IP rate limit `AUTH_CHECK` (30/min, 429) is kept. User enumeration is accepted for the registration UX and bounded by that limit. `OPERATOR_ENFORCE` no longer concerns these two routes.
* **Peer keys**: `receiver_id` and `other_user_id` are in `USER_KEYS` and in `PEER_KEYS` (staff may name a same-organisation colleague). `/chat/mark-read` swaps the roles of its two keys. `load_entity_org` returns a set for an admin who owns several organisations.
* **Handler-level checks** for ids that name one resource: requests (a request of another organisation is always 404 for `POST /requests/update-status`, whatever `REQUESTS_ENFORCE_ACTOR` says), chat (receiver, groups, members), incentives, wage templates, leads, expenses. Routes that carry no org id use `core_functions.auth_dependency.caller_orgs(request)` (None without a token = unchanged behaviour). See `tests/test_resource_scoping.py`.
