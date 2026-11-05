# Platform admin (super-admin) API, support, suggestions, deletion requests & version control — backend context

Companion docs: `plans_subscriptions_billing.md`, `orders_payments_webhooks_invoices.md`. `sub_tbl` = the subscription table (name carries the legacy product prefix, first used at `core_functions/gen_dashboard_functions.py:162`). "Platform admin" = the vendor-side operators listed in `gen_admin_login`, as opposed to an organisation's admin (`users.role_id=1`).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/gen_dashboard.py` | 137 | 21 routes under `/gen-dashboard` |
| `core_functions/gen_dashboard_functions.py` | 951 | all dashboard/org-management/analytics queries |
| `router/auth.py:46-267,304-328` | – | platform-admin OTP login, `gen-admin-users` CRUD, `check-mobile`/`check-email`, header guards |
| `router/support.py` + `core_functions/support_functions.py` | 101 + 217 | tickets (`/support/*`) |
| `router/company.py:53-109` + `core_functions/suggest_feature_functions.py` | 109 + 225 | feature suggestions (`/company/suggest-feature*`) |
| `router/company.py:43-50` + `core_functions/company_functions.py:336-401` | – | account-deletion requests (list/submit) |
| `router/version.py` | 50 | `GET /version/check` |
| `core_functions/session_registry.py` | – | in-memory SSE registry used for force-logout |
| `app.py:10-33` | – | CORS (allows `x-admin-id`, `x-role-id` headers) |

Frontend callers: the new Clockyn app has **no** platform-admin screens and never calls `/gen-dashboard/*`, `/auth/admin-login*` or `/auth/gen-admin-users*` (grep of `src/` finds none; `admin-login` there is the organisation-admin login route). It does call `/support/subjects`, `/support/requests/{org}`, `/support/submit`, `/support/{id}/reply` (`src/api/support.ts`), `POST /company/suggest-feature`, `POST /company/request-deletion/{user_id}` and `GET /version/check` (`src/api/system.ts:90-118`). All other routes here are consumed by the legacy vendor admin web panel (separate repository, not in the scanned sources).

## 2. Data model touched

| Table | Columns used | Notes |
|---|---|---|
| `gen_admin_login` | `phone_number`, `role_id` (1 = "Admin", 2 = "Staff", anything else labelled Admin, `gen_dashboard_functions.py:525-531`), `name` | **No primary key / unique constraint** in the DDL dump -> duplicate phones possible. Rows are inserted manually or via `/auth/gen-admin-users`. |
| `moblie_otp_verification` (sic) | `mobile_number`, `otp_code`, `is_verified`, `attempts`, `created_at`, `expires_at` | shared with end-user OTP; `attempts`/`is_verified` unused by admin flow (`auth.py:76-80,110-118`) |
| `org_info` | `org_id`, `org_name`, `org_address`, `org_gst`, `state`, `district`, `pincode`, `parent_id` (owner user_id), `status` (1 active, 0 inactive; "delete" is stored as 0), `created_at`, `updated_at` | |
| `users` | `user_id`, `parent_id` (org for role 2/3), `role_id`, `status` (1 active, 0 inactive, 2 deleted), `mobile_number`, `email`, `name`, `emp_id`, `dept_id`, `branch_id`, `created_at` | |
| `sub_tbl`, `free_trial`, `gen_plans`, `gen_payments`, `gen_orders`, `gen_invoices` | read-only for dashboards | |
| `attendance` | `attendance_date`, `punch_in/out`, `punchinface`, `punchoutface`, `work_hours`, `org_id`, `user_id` | |
| `requests` | `status='pending'` count | |
| `activity_tracking` | code inserts `(org_id, changed_by, status)` (`gen_dashboard_functions.py:127-131`, schema from env `DB_SCHEMA`) but the DDL dump has `(org_id, user_id, activity NOT NULL, created_at, updated_at)` -> **mismatch (UNVERIFIED which is live)**; if the dump is right every org activate/deactivate/delete fails and rolls back | |
| `admin_support` | `id`, `sub_id`, `sub_name`, `status` (1 = selectable) | ticket subjects |
| `admin_subject_request` | `id`, `support_id`, `sub_id`, `org_id`, `client_msg`, `status` ('created' default), `reply`, `photo_url` (S3 key), timestamps; code also writes `user_id` (not in DDL dump; UNVERIFIED) | no PK in the dump |
| `ticket_replies` | `ticket_id`, `reply_text`, `author_name`, `is_admin`, `created_at` | **not in the DDL dump**; code falls back when missing (`support_functions.py:152-165`, `support.py:83-84` returns 503 mentioning a migration file) |
| `email_log` | `id`, `org_id`, `date`, `subject`, `text`, `created_at` | stores feature suggestions |
| `suggestion_replies` | `email_log_id`, `reply_text`, `admin_name`, `created_at` | |
| `active_mail` | `email`, `status` | recipients of suggestion e-mails (no PK) |
| `account_deletion_requests` | `request_id` uuid, `user_id`, `org_name`, `mobile_number`, `email`, `status` ('pending' / 'deleted'), `requested_at`, `actioned_at` | |
| `app_version_control` | `platform`, `latest_version`, `min_supported`, `force_update`, `message`, `store_url` (inferred from `version.py:15`) | **not in the DDL dump; real columns UNVERIFIED**; no admin endpoint edits it |

## 3. Endpoints

**Auth model (all rows): none, except where stated.** Handlers are `async def` with blocking DB calls. Unless noted, DB errors -> `500 {"detail": "<raw exception text>"}`; list endpoints return **bare JSON arrays** (no `success` envelope) unless shown with `{success,data}`.

### 3.1 `/gen-dashboard` (`router/gen_dashboard.py`)

| METHOD path | Handler (router:line -> function:line) | Request | Success response | Quirks |
|---|---|---|---|---|
| GET `/gen-dashboard/me` | `:30` -> `get_admin_profile` `:520` | query `phone_number` | `{name (or phone), mobile, role ('Admin'\|'Staff'), role_id}` | 404 `User not found`; identity = phone typed by client |
| PUT `/gen-dashboard/me` | `:40` -> `update_admin_profile` `:551` | body `{phone_number, name}` | `{success:true}` | 404; anyone can rename any admin |
| GET `/gen-dashboard/stats` | `:45` -> `get_super_admin_stats` `:152` | – | `{total_orgs,total_users(status=1),active_subscriptions,free_trial_orgs,revenue_this_month,pending_payments,failed_payments,today_attendance,open_requests}` | semantics in §4.2 |
| GET `/gen-dashboard/users` | `:55` -> `get_all_users` `:219` | – | array `{user_id,name,emp_id,email,mobile,status('Active'\|'Inactive'),role_id,role_name,org_name,dept_name,branch_name,joined('DD Mon YYYY')}`, missing values `'—'` | **every user of the platform, no pagination/auth**; an admin owning several orgs appears once per org |
| GET `/gen-dashboard/users/{user_id}/latest-attendance` | `:50` -> `get_user_latest_attendance` `:573` | path | `{punch_in_time,punch_out_time (IST '%d %b %Y, %I:%M %p'),punch_in_photo,punch_out_photo}` or `{}` | "latest" = **today only** (`CURRENT_DATE`); photo URLs re-presigned (7 days); faces exposed without auth |
| GET `/gen-dashboard/users/{user_id}/attendance-range` | `:120` -> `get_user_attendance_range` `:761` | query `from_date`,`to_date` (strings) | `{success,data:[{date,punch_in_time,punch_out_time,work_hours,punch_in_photo,punch_out_photo}],total_days}` | bad date -> 500; one S3 `head_object` per photo |
| GET `/gen-dashboard/organizations` | `:60` -> `get_all_organizations` `:279` | – | array `{org_id,org_name,org_address,gst_number,state,district,pincode,email,mobile,emp_count,attendance_activity_count,sub_status,plan_name,joined,start_date,end_date,free_trial_end_date,free_trial_end,payment_status('paid'\|'unpaid'),org_status}` | unpaginated; §4.2 |
| PUT `/gen-dashboard/organizations/{org_id}` | `:65` -> `update_org_details` `:27` | body `OrgUpdateRequest {org_name, org_address, gst_number?, mobile?, email?, state?, district?, pincode?}` | `{success:true}` | 404 `Organization not found`; omitted optional fields **overwrite with empty/NULL** (:46); owner's `mobile`/`email` updated in `users` (:52-66) without uniqueness/OTP check |
| POST `/gen-dashboard/organizations/{org_id}/activate` | `:95` -> `update_org_status(...,1,changed_by)` `:81` | body `{changed_by}` (phone) | `{success:true}` | §4.3 |
| POST `/gen-dashboard/organizations/{org_id}/deactivate` | `:100` -> status 0 | same | same | |
| DELETE `/gen-dashboard/organizations/{org_id}` | `:105` -> status 2 | body `{changed_by}` (DELETE with body) | same | soft delete only |
| GET `/gen-dashboard/recent-orgs` | `:70` -> `get_recent_orgs` `:379` | – | array `{org_id,org_name,user_count,plan_name,sub_status,end_date,joined}` | despite the name returns **all** orgs, no LIMIT |
| GET `/gen-dashboard/recent-payments` | `:75` -> `get_recent_payments` `:425` | – | array `{payment_id,org_name,plan_name,amount,status,date}` | all payments, no LIMIT |
| GET `/gen-dashboard/plan-breakdown` | `:80` -> `get_plan_breakdown` `:489` | – | array `{plan_id,plan_name,plan_amount,plan_month,active_count}` | counts `status='ACTIVE'` rows incl. date-expired |
| GET `/gen-dashboard/monthly-revenue` | `:85` -> `get_monthly_revenue` `:462` | – | array `{month:'Mon',revenue}` | label has no year; window `>= NOW()-12 months` includes a partial same-named month -> duplicate labels |
| GET `/gen-dashboard/org-analytics/{org_id}` | `:90` -> `get_org_analytics` `:632` | query `days:int=7` (unbounded) | `{total_employees,daily_attendance:[{date,present,absent}],attendance_map:[{date,present}] (30 d)}` | `absent = total_employees - present` can go negative (counts inactive punchers) |
| GET `/gen-dashboard/org/{org_id}/info` | `:110` -> `get_org_info` `:693` | – | `{success,data:{org fields,admin_name,admin_mobile,admin_email,plan_name,sub_status,start_date,end_date,staff_limit}}` | 404; joins ACTIVE sub (may duplicate; `LIMIT 1`) |
| GET `/gen-dashboard/org/{org_id}/employees` | `:115` -> `get_org_employees` `:727` | – | `{success,data:[{user_id,name,emp_id,email,mobile_number,status,role_id,created_at,role_name,dept_name,branch_name,punch_in_today,punch_out_today ('%I:%M %p' IST)}]}` | role 2/3 only (all statuses) |
| GET `/gen-dashboard/active-orgs` | `:125` -> `get_active_orgs_today` `:806` | – | `{success,data:[{org_id,org_name,active_count,last_punch_time}]}` | `org_info.status=1`, `LIMIT 50` |
| GET `/gen-dashboard/org-subscriptions` | `:130` -> `get_org_subscriptions_list` `:907` | – | `{success,data:[{org_id,org_name,admin_mobile,admin_email,plan_name,staff_limit,start_date,end_date,status,invoice_number,total_amount,base_amount,cgst,sgst,razorpay_payment_id,payment_date,valid_till,coupon_code,invoice_user_id}]}` | one row per `ACTIVE` sub ordered by `end_date` ASC, with the latest invoice (LATERAL); amounts `null` when `0` (falsy test :941-944) |
| GET `/gen-dashboard/org/{org_id}/attendance/{date}` | `:135` -> `get_org_attendance_by_date` `:841` | path `date` (string) | `{success,data:[{user_id,name,emp_id,role_name,dept_name,branch_name,punch_in_time,punch_out_time,work_hours,punch_in_photo,punch_out_photo,is_present}],date}` | active role 2/3; face photos |

### 3.2 Platform-admin identity (`router/auth.py`)

| METHOD path | Handler | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| POST `/auth/admin-login` | `gen_admin_login_route` `:53-95` | none | `{phone}` | `{success:true,message:'OTP sent successfully'}` | 401 `Phone number not found` (enumerates admins); 500 `Internal server error` |
| POST `/auth/admin-login/verify-otp` | `gen_admin_verify_otp_route` `:97-136` | none | `{phone, otp}` | `{success:true, phone_number, role_id}` (**no token**) | 400 `Invalid OTP format` / `Invalid or expired OTP` |
| GET `/auth/gen-admin-users` | `:186` | header `x-admin-id` must be a role_id=1 admin (`_require_role1` `:168-184`) | – | `{success,users:[{phone_number,name,role_id}]}` | 403 `Only role_id=1 admins can perform this action` |
| POST `/auth/gen-admin-users` | `:199` | `_require_role1` | `{phone_number,name='',role_id=2}` | `{success,user}` | 400 `Phone number already exists`; 500 |
| PUT `/auth/gen-admin-users/{phone_number}` | `:224` | `_require_role1` | `{phone_number,name,role_id}` (phone may be changed) | `{success,user}` | 404 `User not found` |
| DELETE `/auth/gen-admin-users/{phone_number}` | `:248` | `_require_role1` | – | `{success:true}` | 404 |
| GET `/auth/check-mobile/{mobile}` | `check_mobile_exists` `:304` | `_require_gen_admin` (any row in `gen_admin_login`, `:148-165`) | – | `{exists: bool}` (org admins, role 1) | 403 `Authentication required`; the organisation app calls this without the header and always gets 403 (backend_changes P3-5) |
| GET `/auth/check-email/{email}` | `:317` | `_require_gen_admin` | – | `{exists}` | same |

### 3.3 Support tickets (`router/support.py`)

| METHOD path | Handler -> function | Request | Response | Errors |
|---|---|---|---|---|
| GET `/support/subjects` | `:24` -> `get_support_subjects` `support_functions.py:9` | – | array `{sub_id,sub_name}` where `status=1` | 500 |
| POST `/support/submit` | `:32` -> `submit_support_request` `:19-40` | multipart `user_id, org_id, subject (a sub_name), message, photo?` | `{success:true,data:{id,support_id,sub_id,org_id,user_id,client_msg,status:'created',photo_url(key),created_at}}` | 400 `Invalid subject: ...`; photo upload failure is swallowed (`support.py:45-51`); `user_id`/`org_id` not validated; **no notification to anyone** |
| PUT `/support/update/{request_id}` | `:61` -> `update_support_request` `:43-63` | `{status:str (free text), reply?:str}` | `{success,data:{id,status,reply,updated_at}}` | 404 `Request not found`; overwrites the single legacy `reply` column; unauthenticated admin action |
| POST `/support/{request_id}/reply` | `:72` -> `reply_to_ticket` `:66-106` | `{reply_text, author_name='Admin', is_admin=true}` | `{success,data:{id,ticket_id,reply_text,author_name,is_admin,created_at,status}}` | 400 empty text; 404 `Ticket not found`; 503 if `ticket_replies` missing; **`is_admin` is client-supplied**; status moves `created -> in_progress` on admin reply, `resolved -> in_progress` on user reply |
| GET `/support/requests/all` | `:88` -> `get_all_support_requests` `:123` | – | array of tickets with `sub_name, org_name, user_name, user_mobile, replies[]` and presigned `photo_url` | all tickets of all orgs, unauthenticated; fallback query without replies if the table is missing |
| GET `/support/requests/{org_id}` | `:96` -> `get_support_requests` `:171` | path | same, filtered | any org id readable |

### 3.4 Suggestions (`router/company.py`)

| METHOD path | Handler | Request | Response | Quirks |
|---|---|---|---|---|
| GET `/company/suggest-feature` | `:61` -> `get_suggestions` `suggest_feature_functions.py:60` | – | array `{log_id,org_id,date,subject,text,created_at,company_name,content}` | all orgs; unauthenticated |
| POST `/company/suggest-feature` | `:69` -> `submit_suggest_feature` `:89-115` | `{org_id='',subject,content,company_name='',user_email=''}` | `{success:true,message:'Suggestion submitted successfully'}` | inserts `email_log`; a daemon thread e-mails every `active_mail.status=1` recipient via SES (`:12-57`); user text embedded **unescaped** in the HTML body (`:35-43`) |
| GET `/company/suggest-feature/mine` | `:84` -> `get_my_suggestions` `:141` | query `org_id` | array `{log_id,org_id,subject,content,created_at,replies[{reply_text,admin_name,created_at}]}` | |
| GET `/company/suggest-feature/with-replies` | `:89` -> `get_suggestions_with_reply_count` `:181` | – | admin list + `reply_count,last_reply_at,replies` | on error silently falls back to the plain list (`:93-95`) |
| POST `/company/suggest-feature/{log_id}/reply` | `:98` -> `reply_to_suggestion` `:118` | body dict `{reply_text, admin_name='Admin'}` | `{success:true,message:'Reply sent successfully'}` | 400 empty; 503 if table missing; reply is stored only (no notification to the org) |

### 3.5 Account-deletion requests

| METHOD path | Handler | Request | Response | Quirks |
|---|---|---|---|---|
| POST `/company/request-deletion/{user_id}` | `router/company.py:43` -> `request_account_deletion` `company_functions.py:336-373` | path | `{success:true,message}`; repeat while pending -> same success with "already submitted" text | 404 `User not found`; any role (employees too) can file; org name via `org_info.parent_id` (blank for employees) |
| GET `/company/deletion-requests` | `:48` -> `get_deletion_requests` `:376-401` | – | array `{request_id,user_id,org_name,mobile,email,status,requested_at('DD Mon YYYY HH:MM')}` pending only | unauthenticated; PII |
| (action) DELETE `/company/delete-account/{user_id}` | `company.py:38` -> `delete_user_account` (company domain doc) | path | hard-deletes the owner's orgs/staff/data and marks the request `deleted` (`company_functions.py:468-472`) | unauthenticated; **leaves** `gen_orders`, `gen_payments`, `gen_invoices`, `sub_tbl`, `free_trial`, tickets, suggestions |

### 3.6 App version

| METHOD path | Handler | Request | Response | Quirks |
|---|---|---|---|---|
| GET `/version/check` | `check_version` `router/version.py:8-47` | query `platform`, `version` (both required) | `{success:true,data:{update_required,latest_version,force_update,message,store_url}}`; unknown platform -> `{update_required:false}` | 500 on unparsable version string; `update_required = current < latest`; `force_update = current < min_supported OR row.force_update` |

### 3.7 Health and readiness probes (`router/health.py`)

| METHOD path | Handler | Response | Notes |
|---|---|---|---|
| GET `/health` | `health` | always 200 `{"status":"ok"}` | liveness; never touches the database; no auth |
| GET `/ready` | `ready` | 200 `{"status":"ready"}` when a pooled connection runs `SELECT 1`; otherwise 503 `{"status":"unavailable"}` | readiness for the load balancer; the failure reason is only logged |

Changes (WP0/WP1): every response carries `X-Request-ID` (an incoming safe value is reused); an unhandled exception answers 500 `{"detail":"Internal server error"}` and is logged with traceback and request id (`utils/errors.py`); `HTTPException` and validation errors keep their shapes. `RUN_MIGRATIONS=1` makes start-up run `migrate.py` (never fatal).

Endpoint count in this doc: 21 + 8 + 6 + 5 + 2 + 1 = **43** (+1 cross-reference).

## 4. Business logic

### 4.1 Platform-admin login (`auth.py:53-136`)
`admin-login`: row must exist in `gen_admin_login`; OTP = `random.randint(100000, 999999)` (non-cryptographic RNG); old OTP rows for the phone deleted, new row with 5-minute expiry; SMS via the same MSG91 helper as end users (`_send_msg91_sms`); the OTP is **printed to stdout** (`:84`). `verify-otp`: match on phone + code + `expires_at > now`; row deleted; returns `{phone_number, role_id}`; there is **no attempt counter** (6-digit code, 5 minutes, unlimited tries) and no token/cookie is issued. Afterwards the admin web app sends whatever it likes: `/gen-dashboard/*` check nothing; only `/auth/gen-admin-users*`, `/auth/check-mobile`, `/auth/check-email` read the `x-admin-id` header (a bare phone number, spoofable by anyone who knows an admin number) and look it up in `gen_admin_login`. The `x-role-id` header allowed by CORS (`app.py:25,31`) is **never read** server-side.

### 4.2 Dashboard semantics (`gen_dashboard_functions.py`)
* `total_orgs` counts every `org_info` row (including deactivated/"deleted"); `total_users` = `users.status=1` (includes org admins, managers, staff).
* `active_subscriptions` = `sub_tbl.status='ACTIVE'` rows, **including date-expired plans** (nothing writes `EXPIRED`); `free_trial_orgs` = orgs with no ACTIVE sub (so expired trials and orgs without any trial count as "free trial").
* `revenue_this_month` = `SUM(gen_payments.amount)` with `status='PAID'` in the DB-timezone month: **GST-inclusive gross**, includes top-ups, no refunds modelled. `pending_payments` = all `gen_orders` still `CREATED` since forever (abandoned checkouts); `failed_payments` = `gen_payments.status='FAILED'`, which nothing ever writes (the webhook sets `gen_orders.status`, not `gen_payments`) -> always 0.
* `today_attendance` = distinct users with `punch_in` on `CURRENT_DATE` (DB date, attendance stored with UTC date); `open_requests` = `requests.status='pending'` platform-wide.
* `get_all_organizations`: latest `sub_tbl` row of any status (LATERAL), `sub_status` defaults to `FREE_TRIAL`, `plan_name` to `Free Trial`, `payment_status='paid'` iff that row is `ACTIVE` (so expired = still "paid"), `emp_count` = active role 2/3 users, `attendance_activity_count` = punch-ins + punch-outs today, `org_status = COALESCE(o.status,1)`.

### 4.3 Org activate / deactivate / delete (`update_org_status` `:81-149`)
1. status must be 0/1/2 (always true from the router). 404 if org missing.
2. For 0 and 2: collect role 2/3 users of the org (to force-logout).
3. Update `users.status` (= requested value; 2 for delete) for all users with `parent_id=org` **and** the owner (`role_id=1 AND user_id IN (org.parent_id)`). Activate sets **everyone** to 1, including users that had been individually deactivated or deleted earlier.
4. `org_info.status = 0 if status==2 else status` (a deleted org is indistinguishable from an inactive one in `org_info`; `users.status=2` marks deletion).
5. Audit: `INSERT INTO <DB_SCHEMA>.activity_tracking (org_id, changed_by[:20], status)`; `changed_by` is whatever phone the client sent.
6. Commit, then `force_logout_user(user_id)` for each affected role 2/3 user: pushes `force_logout` to an **in-process** dict of SSE queues (`session_registry.py`), so it only works for connections on the same worker; the owner (role 1) is not logged out. Billing is untouched (subscription stays ACTIVE; no refund/proration).
No cascade deletion of data; `DELETE` is a soft delete that `activate` can reverse.

### 4.4 Support/suggestion flows
Ticket lifecycle: `created -> in_progress (first admin reply) -> resolved` (set only via `PUT /support/update` with free-text status) `-> in_progress (user reply on resolved)`. Photos: best-effort upload to S3 `client_support/<uuid>_<filename>` (`ml/s3.py:286-302`), stored as the S3 **key**, re-presigned for 7 days on read (`:305-319`, returns `null` if the object is missing). Suggestions: stored in `email_log` then mailed to `active_mail` recipients; replies stored in `suggestion_replies`; nothing notifies the submitting org of a reply (the app polls `/mine`).

### 4.5 Version check
Single SQL read per `platform`; versions parsed with `packaging.version.parse`; see §3.6.

## 5. Side effects & integrations

| Item | Payload / detail |
|---|---|
| MSG91 SMS | admin OTP via `_send_msg91_sms` |
| AWS SES | suggestion e-mail `Source=EMAIL_USER`, `ToAddresses=[recipient]`, subject `[Feature Suggestion] <subject>`, HTML body with org id, subject and raw text (`suggest_feature_functions.py:35-54`) |
| S3 | support photos (`client_support/` prefix), attendance photos presigned on every dashboard read (one `head_object` + one presign per photo) |
| SSE | `force_logout` event on org deactivate/delete (single-process registry) |
| FCM / WebSocket / e-mail on new ticket | **none** |

## 6. Config & env vars
`DB_SCHEMA` (also interpolated into the `activity_tracking` insert, `gen_dashboard_functions.py:9,128`), DB connection vars, `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID` (OTP SMS), `EMAIL_USER`, `AWS_SES_REGION`, AWS credentials, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME` (`ml/s3.py:10-14`). Version rows, support subjects, `active_mail` recipients and platform admins are DB data.

## 7. Known defects & risks

| ID | Sev | Where | Defect / impact |
|---|---|---|---|
| D-1 | Critical | `router/gen_dashboard.py` (all routes) | Zero authentication: anyone can list all users/orgs/payments, read face photos, edit orgs, **deactivate or soft-delete any organisation and force-logout its staff**. |
| D-2 | Critical | `router/auth.py:97-136` | OTP login yields no session/token; admin identity is a client-held phone number. `x-admin-id` guard (`:148-184`) is spoofable by knowing a number; `/gen-admin-users` guarded only by that. |
| D-3 | High | `router/auth.py:84` | OTP logged in clear to stdout; `random` (not `secrets`); no attempt limit/lockout (6 digits, 5 min brute-forceable); `/admin-login` enumerates admin phones (401). |
| D-4 | High | `gen_dashboard_functions.py:127-131` vs DDL | `activity_tracking` insert columns do not exist in the DDL dump; if the dump is current every activate/deactivate/delete raises and rolls back (UNVERIFIED). Audit `changed_by` is unauthenticated and truncated to 20 chars. |
| D-5 | High | `router/support.py:13-21,61-85`, `company.py:98-109`, `company.py:48` | Admin-side ticket update/reply, suggestion list/reply, deletion-request list are unauthenticated; `is_admin` on replies is client-supplied (spoof admin replies). |
| D-6 | High | `gen_dashboard_functions.py:573-629,761-803,841-904` | Biometric face photos of any user readable by id; per-photo S3 `head_object` makes calls slow (N+1). |
| D-7 | Med | `:81-149` | `activate` resurrects all users (even individually deactivated); delete stored as 0 in `org_info`; billing unaffected; force-logout only in-process and not for the owner. |
| D-8 | Med | `:27-78` | PUT org overwrites omitted optional fields with empty/NULL and changes the owner's mobile/e-mail without uniqueness or OTP verification. |
| D-9 | Med | `:152-216` | Misleading metrics: `failed_payments` always 0, `free_trial_orgs` counts expired/never-trialled orgs, `active_subscriptions`/`payment_status` count date-expired ACTIVE rows, revenue is GST-inclusive, month boundary uses DB timezone. |
| D-10 | Med | `:219-276,379-459` | Unpaginated whole-platform lists (`users`, `organizations`, `recent-orgs`, `recent-payments`); duplicate user rows for multi-org admins. |
| D-11 | Low | `:462-486` | Monthly revenue label without year; 12-month window includes a partial duplicate month. |
| D-12 | Low | `:632-690` | `days` unbounded; `absent` may be negative. |
| D-13 | Med | `auth.py:224-246,248-267`; DDL | `gen_admin_login` has no PK/unique; PUT can rewrite the phone; an admin can delete the last role-1 admin (lock-out); no audit. |
| D-14 | Med | `suggest_feature_functions.py:35-43` | User text injected unescaped into the e-mail HTML (HTML injection into operator mailboxes); one SES call per recipient in a daemon thread. |
| D-15 | Med | `support_functions.py:19-40`, `support.py:32-58` | Ticket submit trusts `user_id`/`org_id`, sends no notification (tickets can go unseen); schema drift (`user_id`, `ticket_replies`, `app_version_control` not in DDL dump, UNVERIFIED). |
| D-16 | High | `router/version.py:14-18` | One row per platform shared by the legacy and new apps; if the row holds legacy numbers/`store_url`, the new app (version 1.0.0) is told to update and may be **force-updated to the legacy listing** (P0-8). `row.force_update=TRUE` forces every client regardless of version. Unparsable version -> 500. |
| D-17 | Med | `company_functions.py:404-472` | Deletion action leaves billing rows, tickets and suggestions; request list has no status filter or "rejected" state. |
| D-18 | Low | `auth.py:304-328` | `check-mobile`/`check-email` demand the operator header that the org app never sends, so the app's duplicate pre-check always receives 403. |

## 8. Clockyn requirements

Findings: backend_changes.md has **no** platform-admin item; the new frontend contains no super-admin UI. The Clockyn-relevant items in this domain are P0-8 (version), P3-1 (auth), P3-12 (invoice endpoints), P2-10 (brand), deletion/support/suggestion contracts used by the app, and C-7/C-9.

**R-1 Platform-admin authentication and authorisation (P3-1).** Current: see §4.1, D-1, D-2, D-3, D-5, D-13. Required: (a) `/auth/admin-login/verify-otp` issues a signed, short-lived token (JWT or opaque session row) bound to `gen_admin_login` identity; (b) a FastAPI dependency `require_platform_admin(min_role)` (reads `Authorization: Bearer`, **not** `x-admin-id`) applied to: all `/gen-dashboard/*`, `/auth/gen-admin-users*`, `/auth/check-mobile`, `/auth/check-email` (per P3-5: instead make them public, rate-limited and returning only `{exists}`), plan/coupon writes and `GET /plans/coupons` (`plans_subscriptions_billing.md` D-1), `/invoice/bulk-download`, `/orders/history/*`, `/support/requests/all`, `/support/update/*`, admin replies, `/company/suggest-feature` (list, with-replies, reply), `/company/deletion-requests`; (c) role model: `gen_admin_login.role_id` 1 = super admin (CRUD admins, deactivate/delete orgs, plan/coupon edits), 2 = read-only/support (reads, ticket replies); derive `changed_by` from the token; (d) OTP hardening: `secrets.randbelow`, attempt counter (use the existing `attempts` column), lockout, uniform error for unknown phone, remove the print; (e) add a primary key/unique constraint on `gen_admin_login.phone_number`, forbid deleting the last role-1 admin and self-delete; (f) restrict CORS (`app.py:20-34`) to the admin panel origin(s); (g) replace the activity audit with a real `admin_audit_log` table (admin, action, org, before/after, timestamp) and fix/migrate `activity_tracking` to match the code (or the other way round). Acceptance: every listed route returns 401/403 without a valid token; spoofed `x-admin-id` is ignored; five wrong OTPs lock the phone for N minutes.

**What a new Clockyn super-admin would need (if one is built; nothing in the repo requires it today).** Reuse the existing endpoints behind R-1 plus: (1) CRUD for `app_version_control` per app/platform (R-2; today edited by hand in SQL); (2) a deletion-request action endpoint (approve -> run `delete_user_account` and mark `deleted`; reject -> mark `rejected`; status filter), because the store data-safety form (C-7) requires a handled workflow; (3) ticket/suggestion notifications to operators (e-mail or push) and to the org on reply; (4) paginated/searchable org, user and payment lists with `limit/offset` and server-side filters; (5) plan/coupon management with audit and validation (discount 0-100, unique names, per-coupon expiry/usage); (6) a "billing state" column derived like the client (`paid_active|paid_expired|trial|none`) and a manual "extend subscription / grant trial" action (writes `sub_tbl` / `free_trial` and an audit row); (7) bootstrap script to create the first role-1 admin row; (8) config/DB rows to seed for the new brand: `gen_admin_login`, `admin_support` subjects, `active_mail` recipients, `gen_razorpay_key`, `gen_plans`, `gen_coupons`, `app_version_control`.

**R-2 `/version/check` must not force-update Clockyn to the legacy listing (P0-8; cross-ref BD1: `employee_profile_version_attribution.md` and `identity_auth_onboarding.md`).**
* Current: `SELECT ... FROM app_version_control WHERE platform=%s` (one row per platform, `router/version.py:14-18`); response `store_url` straight from the row; the Clockyn client sends only `platform` and `version` (`src/api/system.ts:90-95`, version `1.0.0`). With the legacy row (`latest_version` ~1.0.34, `min_supported` high) the new app computes `update_required=true` and `force_update=true` and shows the legacy `store_url`.
* Required: add an app identifier. Migration: `ALTER TABLE app_version_control ADD COLUMN app_id varchar(100) NOT NULL DEFAULT 'legacy'; ` unique `(app_id, platform)`; insert Clockyn rows for `android`/`ios` with the new bundle id (frontend default `com.clockyn.app`, `APP_ID`, C-1), `latest_version`, `min_supported`, `store_url` of the Clockyn listing (or NULL so the client falls back to `EXPO_PUBLIC_*_STORE_URL`, C-9). Endpoint: optional query `app_id`; when absent use `'legacy'` so v1 clients are unaffected; when present but unknown return `update_required:false` (never fall back to the legacy row). Keep the response shape. Fix: parse failure -> 400/`update_required:false` instead of 500; treat `force_update` flag as "force below `latest_version`" rather than unconditional (decision); log which app asked.
* Interim without code: keep Clockyn's version numbers above the stored `min_supported` (owner note in P0-8) and never put the legacy `store_url` in front of the new app.
* Acceptance: `app_id=com.clockyn.app&platform=android&version=1.0.0` with a Clockyn row `latest=1.0.0` -> `update_required:false`; same request with no `app_id` -> legacy row result unchanged; unknown app -> `update_required:false`; `version=abc` -> 4xx, not 500. The store-link rule: the response `store_url` must always belong to the requested `app_id`.

**R-3 Support, suggestions, deletion for the new app (contracts already in use).** Keep response shapes (`/support/*`, `/company/suggest-feature*`, `/company/request-deletion/*`); add token auth and make `is_admin` server-derived (customer replies from the app always `is_admin=false`); send an operator notification on `POST /support/submit` and on suggestions; escape HTML in the suggestion mail; make `request_account_deletion` verify the caller and block employees if policy requires; create the missing tables via migration (`ticket_replies`, `suggestion_replies` exists, `user_id` on `admin_subject_request`) and remove the 503 fallbacks. Brand strings in operator mails belong to the P2-10 rename sweep.

**R-4 Dashboard accuracy (only if the legacy admin panel is carried over).** Persist/derive an explicit `EXPIRED` state (see plans doc R-3) and make `active_subscriptions`, `payment_status`, `free_trial_orgs`, `failed_payments` use it; add pagination to `users`, `organizations`, `recent-orgs`, `recent-payments`; add year to monthly revenue and use IST month boundaries; make org delete a distinct state (`org_info.status=2`) and make activate restore only users deactivated by the org action.

**Backward compatibility.** The legacy admin panel and v1 apps share every route here; keep paths and response keys, add auth in a way the panel can adopt (e.g. enforce after the panel sends the new token; optionally feature-flag enforcement per route during migration).

## 9. Test notes
* No tests in the repo. Fixtures: `gen_admin_login` rows (role 1 and 2), orgs in each billing state (trial, ACTIVE future, ACTIVE past, none), users of each role/status, attendance with photos (mock S3), `admin_support` subjects, tickets with and without `ticket_replies`, `app_version_control` rows (legacy + Clockyn).
* Critical cases: each route unauthenticated vs authenticated (R-1); OTP expiry/brute force; deactivate -> users `status=0`, org `status=0`, `activity_tracking`/audit written, SSE `force_logout` delivered; activate after delete; PUT org with omitted optionals; stats values on a seeded DB (including date-expired ACTIVE sub, `FAILED` payments); ticket flow `created -> in_progress -> resolved -> in_progress` and spoofed `is_admin`; suggestion e-mail with HTML in text; `/version/check` matrix in R-2; deletion request submit twice (idempotent) and action via delete-account (billing rows remain).
