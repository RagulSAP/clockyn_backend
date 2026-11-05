# Employee profile, profile photo, version check, attribution & platform utilities — backend context

Companion file: `identity_auth_onboarding.md` (auth, OTP, device tokens, registration, DB pool, permission checker).
Conventions: `<LP>_` = legacy product prefix on a table name (`<LP>_subscription`). No secret values are recorded. Line numbers refer to the snapshot in `C:\POC\pa_backend\Clockyn`.

## 1. Scope & files

| File | Lines | Role | Frontend caller |
|---|---|---|---|
| `router/employee.py` | 41 | `/employee/personal-details/{user_id}` GET/PUT, `/employee/today-birthdays/{org_id}` | `src/api/employee.ts:78-105` (`PersonalDetailsScreen`, `EmployeeHomeScreen`) |
| `core_functions/emp_pers_detail_functions.py` | 210 | personal-details read/update + Pydantic models | same |
| `router/profile_photo.py` | 19 | `/profile-photo/{user_id}` GET/POST (duplicate of `/auth/*employee-profile*`) | `src/api/auth.ts:142` (`getProfilePhotoUrl`) |
| `router/version.py` | 50 | `GET /version/check` | `src/api/system.ts:91`, `src/lib/platform/appUpdate.ts:23-37`, `appUpdateLogic.ts:47-64` |
| `router/attribution.py` | 93 | `POST /attribution/meta`, `GET /attribution/reports/meta-attribution` | `src/api/attribution.ts` (`useRegistration.ts:136`) |
| `services/meta_events.py` | 100 | Meta Conversions API helpers (not imported anywhere) | none |
| `core_functions/whatsapp_service.py` | 110 | WhatsApp onboarding message to new staff (MSG91) | triggered by `add_staff_function.py:427-440` (`POST /addstaff`) |
| `utils/email.py` | 1,201 | Invoice PDF/e-mail generation + `/invoice/*` router (4 endpoints); imported by `core_functions/gen_payment_functions.py:5` and mounted at `app.py:63` | `src/api` invoices screen (B5) |
| `utils/timezone.py` | 96 | UTC/IST helpers (`get_utc_now`, `convert_utc_to_ist`, `convert_ist_to_utc`, `format_ist_datetime`, `format_ist_time`, `IST`, `UTC`) | none (server-wide helper) |
| `ml/s3.py` (lines 154-187) | | employee profile put/get | |

## 2. Data model touched

| Table | Columns used | Notes |
|---|---|---|
| `users` | `user_id, name, emp_id, mobile_number, email, branch_id, dept_id, role_id, parent_id, status, push_token, updated_at`, attribution columns `meta_campaign_id, meta_adset_id, meta_ad_id, install_source, attribution_platform` (all nullable varchar) | personal-details read requires `role_id IN (2,3) AND status = 1`; update of name/mobile/email writes `users` directly |
| `users_additional_info` | `user_id, dob date, gender, blood_group varchar(5), address, emergency_contact_no varchar(15), acc_no, ifsc_code, bank_name, bank_branch, shift_id, father_name, marital_status, aadhaar_no varchar(12), pan_no varchar(10), uan_no, pf_no, esi_no, doj date, created_at, updated_at` | DDL: `acc_no, ifsc_code, bank_name, bank_branch, gender, address, emergency_contact_no` are **NOT NULL**; no unique constraint on `user_id` (only idx). `updated_at` is not maintained by the update (no trigger known). |
| `branch_info`, `dept_info`, `role_info` | `branch_name`, `dept_name`, `role_name` joined for display | joins filtered by `org_id = users.parent_id` |
| `app_version_control` | `platform, latest_version, min_supported, force_update, message, store_url` | **Not in the schema dump** (only referenced by `router/version.py:15`); keys/uniqueness unknown (UNVERIFIED). Created outside migrations. |
| `meta_attribution_log` | `id serial, user_id varchar(255), campaign_id, adset_id, ad_id, source default 'facebook', created_at timestamptz default now()` | indexes on campaign_id, created_at, user_id; no FK to `users` |
| `gen_invoices` | `invoice_id, invoice_number, user_id, org_id, plan_name, staff_count, base_amount, cgst, sgst, total_amount, razorpay_payment_id, razorpay_order_id, payment_date, valid_till, coupon_code, created_at` | read by `/invoice/*` |
| `gen_plans`, `<LP>_subscription`, `org_info`, `company_additional_info` | `plan_name, plan_month`; `plan_id, status, start_date, end_date`; org address/state/district/pincode/org_gst; `pan_number, gst_number` | joins in the invoice endpoints |

## 3. Endpoints

No endpoint here verifies a caller. Plan gate: none.

| # | METHOD path | Handler (file:line) | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|---|
| 1 | GET `/employee/personal-details/{user_id}` | `get_employee_personal_details_endpoint` router/employee.py:10 -> emp_pers_detail_functions.py:59 | path `user_id` (no auth) | - | `EmployeePersonalDetails` (bare object): `user_id,name,emp_id,mobile_number,email,branch_name,dept_name,role_name,dob 'YYYY-MM-DD',gender,blood_group,address,emergency_contact_no,acc_no,ifsc_code,bank_name,bank_branch,shift_id,father_name,marital_status,aadhaar_no,pan_no,uan_no,pf_no,esi_no,doj,profile_photo_url` | 400 empty id; 404 "Employee not found or not authorized" (role 1 or inactive); other failures 500 `"Failed to get employee details: <exception text>"` (leaks internals). Government IDs/bank data unmasked. |
| 2 | PUT `/employee/personal-details/{user_id}` | `update_employee_personal_details_endpoint` :15 -> :105 | path `user_id` (no auth) | `UpdateEmployeePersonalDetails` (all optional strings): `name, mobile_number, email, dob, gender, blood_group, address, emergency_contact_no, acc_no, ifsc_code, bank_name, bank_branch, father_name, marital_status, aadhaar_no, pan_no, uan_no, pf_no, esi_no, doj` | `{success:true,message:"Personal details updated successfully"}` | 404 "Employee not found"; 500 `"Failed to update personal details: <text>"`; empty/null values are ignored (cannot clear a field); unparseable dates silently ignored but still `success:true` |
| 3 | GET `/employee/today-birthdays/{org_id}?exclude_user_id=` | `get_today_birthdays` :20 | path `org_id`, required query `exclude_user_id` | - | `{birthdays:[name,...]}` | 422 if query missing; no try/except around DB errors (raw 500) |
| 4 | GET `/profile-photo/{user_id}` | `get_profile_photo` router/profile_photo.py:7 | none | - | `{success:true,photo_url\|null}` | `print()` log; S3 errors raise raw 500 (the helper itself swallows into `null`) |
| 5 | POST `/profile-photo/{user_id}` | `upload_profile_photo` :14 | none (anyone can overwrite) | multipart `photo` | `{success:true,photo_url}` | stored as `employee_profile/{user_id}.jpg`, JPEG type forced; no size/type check; no try/except |
| 6 | GET `/version/check?platform=&version=` | `check_version` router/version.py:8 | none | both query params required strings | `{success:true,data:{update_required:bool,latest_version,force_update:bool,message,store_url}}`; unknown platform -> `{success:true,data:{update_required:false}}` | invalid version string -> 500 with raw exception text (`str(e)`, :46-47); `force_update` returned even when `update_required` is false |
| 7 | POST `/attribution/meta` | `save_meta_attribution` router/attribution.py:21 | `user_id` in body | `{user_id, campaign_id?, adset_id?, ad_id?, source="facebook"}` | `{success:true,message:"Attribution saved"}` | **always HTTP 200**; failure -> `{success:false,message:<exception text>}` (:55-58) |
| 8 | GET `/attribution/reports/meta-attribution` | `meta_attribution_report` :64 | none | - | `{success:true,data:[{campaign,installs,registrations,active_users}]}` | currently always `{success:false,data:[],message:<db error>}` (D-5) |
| 9 | GET `/invoice/details?user_id=&org_id=` | `get_invoice_details` utils/email.py:768 | `user_id` (no auth) | `org_id` optional | `{success:true,data:{client_name,phone,email,invoice_number,payment_id,payment_date,subscription_type,plan_duration,plan_start_date,plan_end_date,plan_amount,cgst,sgst,amount_paid,order_id,valid_till}}` | none found -> HTTP 200 `{success:false,message:"No invoice found"}`; other 500 with exception text |
| 10 | GET `/invoice/history?user_id=&org_id=` | `get_invoice_history` :863 | both required | - | `{success:true,count,data:[{invoice_id,invoice_number,user_id,org_id,customer_name,org_name,plan_name,staff_count,base_amount,cgst,sgst,total_amount,payment_id,order_id,payment_date,valid_till,created_at,download_url}]}` | 400 if missing; `download_url` is a relative path to #11 |
| 11 | GET `/invoice/download?user_id=&org_id=&invoice_number=` | `download_invoice` :921 | `user_id` | `org_id`, `invoice_number` optional | `application/pdf` attachment `<invoice_number>.pdf` | 404 "No invoice found"; NULL amounts/`staff_count` -> `TypeError` -> 500 |
| 12 | GET `/invoice/bulk-download?from_date=&to_date=` | `bulk_download_invoices` :1096 | **none** | two date strings (`payment_date::date BETWEEN`), unvalidated | `application/zip`: one PDF per invoice + `<legacy-prefix>_invoices_summary_<from>_to_<to>.xlsx` | 404 if empty; dumps every customer's name, e-mail, mobile, GST for all orgs |

Endpoint count in this file: 12 (total across both domain files: 35; `utils/email.py` router prefix `/invoice`).

## 4. Business logic

### 4.1 Personal details read (emp_pers_detail_functions.py:59-103)
Single query `users u LEFT JOIN branch_info b (branch_id and b.org_id = u.parent_id) LEFT JOIN dept_info d (...) LEFT JOIN role_info r LEFT JOIN users_additional_info ua` with `u.role_id IN (2,3) AND u.status = 1`, dates formatted with `TO_CHAR`. Then `get_employee_profile_url(user_id)` (S3 HEAD `.jpg` then `.png`, bare `except:`) and Pydantic response model validation.

### 4.2 Personal details update (:105-210)
1. 404 unless an active employee/manager row exists.
2. If any of `name, mobile_number, email` truthy: dynamic `UPDATE users SET ...` (**no uniqueness check, no OTP, no format check**; the mobile number is the login identifier).
3. Dates `dob`/`doj` parsed with formats `%Y-%m-%d, %d/%m/%Y, %d%m%Y, %m%d%Y` in that order (ambiguous for `ddmmyyyy` vs `mmddyyyy`); failures are swallowed with bare `except` (:144-158).
4. Build `non_null_fields` = values that are not `None`/`''`. If a `users_additional_info` row exists -> `UPDATE ... SET <non-null fields>` only; if none -> `INSERT (user_id, <non-null fields>)` which violates the NOT NULL columns unless all of them are supplied (500). `updated_at` not touched.
5. `commit`; generic 500 with exception text; **PII `print()`s** of the whole request model at :159-160, :182-184.
6. Not performed: validation of Aadhaar (12), PAN (10), emergency number, IFSC; authorisation (path id trusted); audit log; cache invalidation of other tables that cache name/mobile.

### 4.3 Today's birthdays (router/employee.py:20-41)
`today = datetime.now(IST).date()`; employees/managers with `parent_id = org_id`, `status = 1`, `role_id IN (2,3)`, `dob` month and day equal today, excluding `exclude_user_id`. Returns names only. 29 Feb birthdays never match in non-leap years; admins are not listed (admin has no `parent_id`).

### 4.4 Photos
Logo/photo helpers in `ml/s3.py:116-187`: put to a fixed key per id (overwrite semantics, no versioning), return a presigned GET URL valid 7 days; `get_*_url` does HEAD then presign; returns `None` on any exception. There is no delete (P3-8). `/profile-photo/*` and `/auth/*employee-profile*` are two routes to the same key.

### 4.5 Version check (router/version.py:8-50)
1. `SELECT latest_version, min_supported, force_update, message, store_url FROM app_version_control WHERE platform = %s` (exact, case-sensitive match; no `ORDER BY`, no uniqueness known -> arbitrary row if several).
2. No row -> `{success:true,data:{update_required:false}}`.
3. Parse the three strings with `packaging.version.parse` (`packaging` is an unpinned requirement, requirements.txt:50). `update_required = current < latest`; `force_required = current < min_supported`; response `force_update = force_required OR row.force_update` (the stored flag is returned even for a client already on the latest version; the app honours it only when `update_required` is true, `appUpdateLogic.ts:56`).
4. Any exception (including `InvalidVersion`) -> 500 with `str(e)`.
Keyed by `platform` only. The frontend sends `platform` = `ios`/`android` and `version` = `Constants.expoConfig.version` (`appUpdate.ts:23-37`); web skips the check.

### 4.6 Attribution (router/attribution.py)
* POST: `UPDATE users SET meta_campaign_id=COALESCE(new, old), meta_adset_id=..., meta_ad_id=..., install_source=COALESCE(source, old), attribution_platform='meta' WHERE user_id=%s` (rowcount ignored - an unknown `user_id` is accepted) then `INSERT INTO meta_attribution_log` (append-only, one row per call), commit. Any exception -> rollback, HTTP 200 `{success:false,message}` (exposes DB errors). The new app always sends `source:'organic'` and no campaign ids, but the endpoint still stamps `attribution_platform='meta'`.
* Report: groups `users` where `attribution_platform='meta' OR install_source='facebook'` by `meta_campaign_id` (`COALESCE(...,'organic')`), counts distinct users (as "installs"), role-1 users ("registrations") and `u.status = 'active'` ("active_users") - `users.status` is an integer, so the comparison with the string `'active'` raises a Postgres type error and the report endpoint can never succeed (D-5).

### 4.7 Meta Conversions API (services/meta_events.py)
Not imported by any module (dead code). Defines `send_registration_event / send_subscription_event / send_attendance_event / send_leave_request_event`, each building one event (`event_name` CompleteRegistration / Subscribe / AttendanceMarked / LeaveRequest, `event_time` epoch, `user_data` with SHA-256 of lower-cased/trimmed phone and e-mail, `custom_data`, `action_source:"app"`) and POSTing `{data:[event], access_token}` to `https://graph.facebook.com/v23.0/<pixel>/events`. Skipped silently when `META_ACCESS_TOKEN` or `META_PIXEL_ID` is empty (read at import). Phone numbers are hashed without E.164 normalisation. A public application identifier literal is present in the docstring (:7).

### 4.8 WhatsApp onboarding (whatsapp_service.py)
`send_employee_onboard_whatsapp(mobile, name, org)`: strips non-digits and prefixes `91` unless it already starts with `91` (same bug class as OTP SMS, :24-26); builds an MSG91 bulk outbound payload `{integrated_number, content_type:"template", payload:{messaging_product:"whatsapp", type:"template", template:{name, language:{code:"en",policy:"deterministic"}, to_and_components:[{to:[mobile], components:{body_var_1:{type:"text",value:name}, body_var_2:{...org}, body_var_3:{...app link}}}]}}}` and posts it with `requests.post(..., timeout=10)` to `https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/` (headers `authkey`, JSON). Returns True/False only; if `MSG91_WHATSAPP_AUTH_KEY` or `MSG91_WHATSAPP_NUMBER` is missing it logs a warning and returns False. The default template name and Play Store link are legacy-branded defaults (:29-33). The whole payload (name, phone) is logged at INFO (:93).
Caller defect: `add_staff_function.py:429` looks up `users.name WHERE user_id = <org_id>`; `org_id` is an organisation uuid (comment at :284), so the lookup returns no row and the message always says "Your Organization" (or, if the ids ever coincide, the admin's personal name instead of the organisation name). The call is synchronous (blocking `requests` up to 10 s) inside the async handler; failure never affects staff creation.

### 4.9 Invoice e-mail/PDF (utils/email.py)
* `send_invoice_email(to_email, invoice_data)` (:758): starts a daemon thread -> `_send_email_async` (:584): requires `EMAIL_USER`, renders the PDF (`_generate_invoice_pdf`, :137-555: ReportLab, fonts DejaVu or Helvetica, logo and signature images from `static/`), builds a multipart message (HTML body + `<legacy-prefix>_Invoice.pdf` attachment), `boto3` SES `send_raw_email`. Failures are only logged. Called from `gen_payment_functions.py` after a successful payment.
* The legal entity name, CIN, registered address, support e-mail/URL and default "Place of Supply" (Tamil Nadu (33)), terms "Net 0" are **hard-coded** in the template and PDF (email.py:205-216, 622-631, 726-730, 260); CGST/SGST labels fixed at 9% (:712-713).
* `/invoice/details`: joins `users -> org_info -> <LP>_subscription (ACTIVE) -> gen_plans -> gen_invoices` by `user_id` (not org), `ORDER BY gi.created_at DESC LIMIT 1`; derives `subscription_type` by substring of the plan name (`business|premium|standard|basic`); amounts: if base/cgst/sgst are NULL they are derived as `total/1.18`, `base*0.09` (:827-830).
* `/invoice/history`: invoices of (user, org), newest first.
* `/invoice/download`: invoice (optionally filtered by `org_id`, `invoice_number`), customer GSTIN/address from `company_additional_info` and `org_info`; the PDF header prints the **customer's** GST number under the issuer's address (`company_gstin`, :204-211, :973) - wrong issuer GSTIN on a tax invoice.
* `/invoice/bulk-download`: all invoices with `payment_date::date` in range -> PDFs (per-invoice failures skipped silently) + one Excel summary with a TOTAL row (:1030-1093); duplicate names disambiguated by payment id.
* No authorisation anywhere (IDOR; P3-12).

### 4.10 Time zone helper (utils/timezone.py)
Rule: DB timestamps stored in UTC; `get_utc_now()` returns aware UTC; `convert_utc_to_ist` treats naive input as UTC; `convert_ist_to_utc` treats naive as IST; `format_ist_*` for display. Many modules ignore it (`date.today()`, `CURRENT_DATE`, see BC P2-5). `datetime.now(IST)` is used directly in `router/employee.py:26`.

## 5. Side effects & integrations

| Integration | Where | Payload / behaviour |
|---|---|---|
| S3 | `ml/s3.py:154-187` | `employee_profile/{user_id}.jpg` put; presigned URL 604800 s |
| MSG91 WhatsApp | whatsapp_service.py:82-107 | payload in §4.8; env `MSG91_WHATSAPP_AUTH_KEY`, `MSG91_WHATSAPP_NUMBER`, `MSG91_WHATSAPP_TEMPLATE`, `APP_PLAY_STORE_LINK` |
| AWS SES | email.py:746-751 | `send_raw_email` with PDF attachment |
| Meta Graph API | meta_events.py:42-60 | unused; see §4.7 |
| Database writes | attribution (users + log), personal details (users + users_additional_info) | |
| Logging/PII | emp_pers_detail_functions.py:159-184 prints; whatsapp_service.py:93 logs payload; profile_photo.py:10,18 prints | |

## 6. Config & env vars

`MSG91_WHATSAPP_AUTH_KEY, MSG91_WHATSAPP_NUMBER, MSG91_WHATSAPP_TEMPLATE, APP_PLAY_STORE_LINK`; `EMAIL_USER, AWS_SES_REGION`; `META_ACCESS_TOKEN, META_PIXEL_ID` (and a documented `META_APP_ID`, `META_CLIENT_TOKEN`, not read by code); `S3_BUCKET_NAME, AWS_S3_REGION` (+ AWS credential pair at `ml/s3.py:10-11`, a secret exists there); DB variables as in the companion file. Static assets: `static/` logo and signature images referenced at `utils/email.py:172-174, 505-507` (legacy-branded file name).

## 7. Known defects & risks

| ID | Sev | Where | Finding / impact |
|---|---|---|---|
| D-1 | **High** | emp_pers_detail_functions.py:119-135 | `PUT /employee/personal-details/{id}` changes `mobile_number`/`email` without OTP, ownership check or duplicate check. Because the mobile number is the login key, anyone who knows a `user_id` can re-point an account to their own number and then log in by OTP (account takeover). Also allows duplicate mobiles across users. |
| D-2 | High | email.py:1096-1201, 768-1027 | `/invoice/*` are unauthenticated; `bulk-download` returns a ZIP/XLSX with every org's customer PII (name, e-mail, mobile, GST). |
| D-3 | High | router/employee.py:10, emp_pers_detail_functions.py:68-95 | Aadhaar, PAN, UAN, bank account data returned unmasked by id with no auth; PII also printed to logs (:159-184). |
| D-4 | Medium | router/version.py:15 | Version control is keyed by `platform` only and shared by every app/bundle on this backend; new app 1.0.0 would be compared with legacy numbers (BC P0-8). Table not in the schema dump; no uniqueness; arbitrary row if duplicated; `force_update` flag returned independent of version. |
| D-5 | Medium | router/attribution.py:78 | `u.status = 'active'` compares an integer column with text -> the report always fails (swallowed into `success:false`). Counts "installs" as user rows; `attribution_platform='meta'` stamped even for organic (`source:'organic'`) so organic users pollute the report. |
| D-6 | Medium | router/attribution.py:55-58 | HTTP 200 with DB error text on failure; unknown `user_id` accepted; endpoint is unauthenticated and unthrottled (append-only log can be flooded). |
| D-7 | Medium | emp_pers_detail_functions.py:179-196 | Cannot clear fields (None/'' skipped); invalid dates silently dropped with success; INSERT path violates NOT NULL columns -> 500; `updated_at` not updated; `except:` bare. The new app sends `null` for cleared dates (`src/api/employee.ts:52`) which therefore never clears. |
| D-8 | Medium | add_staff_function.py:429 | WhatsApp onboarding always reports "Your Organization" (lookup of a user by an org id); blocking HTTP call in the request path. |
| D-9 | Medium | whatsapp_service.py:24-26 | Same `91`-prefix bug as OTP SMS for 10-digit numbers starting with `91`. |
| D-10 | Medium | email.py:204-211, 973 | Tax invoice prints the customer's GSTIN in the issuer block; issuer legal details hard-coded; fixed 18% GST assumptions and fallback math (:827-830); Place of Supply default hard-coded. |
| D-11 | Low | email.py:937-1022 | NULL `base_amount/cgst/sgst/total_amount/staff_count` -> `TypeError` -> 500; `/invoice/details` uses `result.get('invoice_number','N/A')` which still returns `None` when the column is NULL. |
| D-12 | Low | router/profile_photo.py, router/auth.py:352-369 | Duplicate photo routes; no auth, size/type validation or delete; forced JPEG content type; raw 500 on S3 errors; `print()` logging. |
| D-13 | Low | router/employee.py:20-41 | Birthday query misses 29 Feb in non-leap years; no error handling. |
| D-14 | Low | router/version.py:46-47 | Exception text (incl. `InvalidVersion`) returned to the client. |
| D-15 | Low | services/meta_events.py | Dead code; non-normalised hashing; env documentation lists a client token. |
| D-16 | Low | email.py:604-737 | HTML/PDF built with unescaped f-strings from customer-provided names/addresses (HTML injection in the e-mail body). |

Legacy-brand strings in this domain (rename inventory pointers): `core_functions/whatsapp_service.py:29,32`; `utils/email.py:173,214,611,623,726-730,743,797,997,1155,1182,1185` (vendor legal name, support e-mail, domain, logo file name, attachment/zip names, plan item description, table name); `utils/timezone.py:2`; `static/` logo files.

## 8. Clockyn requirements

### R1 - Version control per app (BC P0-8)
* Current: §4.5. One row per `platform`, shared with the legacy app; the new app (1.0.0) would be told to update/force-update to the legacy listing.
* Required: accept an optional `app_id` (bundle id / package name) query parameter on `GET /version/check`; look up `WHERE platform = %s AND app_id = %s`; when `app_id` is absent keep today's behaviour (falls back to rows with `app_id IS NULL`) so the legacy app is not affected. Seed one row per (platform, app_id) for the new bundle with `latest_version`, `min_supported`, `store_url`. Make the version parsing tolerant (build metadata) and return `update_required:false` with a 200 + `success:false`-free envelope on unparsable input instead of 500; stop returning `force_update:true` when `update_required` is false; add a unique constraint on (`platform`, `app_id`).
* Migration (the table is not in the repo schema): `ALTER TABLE app_version_control ADD COLUMN IF NOT EXISTS app_id varchar(100); CREATE UNIQUE INDEX ... ON app_version_control(platform, COALESCE(app_id,''));` plus an INSERT for the new bundle; add the DDL to `migrations/` (the folder holds only a README).
* Frontend: send `app_id` once the parameter is named (`src/api/system.ts:91` builds the query; current query is `platform`, `version`).
* Acceptance: legacy request (no `app_id`) returns the legacy row; request with the new bundle id and version 1.0.0 against `latest 1.0.0` -> `update_required:false`; `1.0.0` against `min_supported 1.1.0` -> `force_update:true`; garbage version -> 200 `update_required:false` (or 400), never 500 with raw text; web is never asked.

**Changes (implemented, DB-free tested in `tests/test_version_check.py`).** `GET /version/check?platform&version[&app_id]`: logic moved to `core_functions/version_function.py`. Looks up `WHERE app_id = %s AND platform = %s` (platform lower-cased; absent or blank `app_id` = `'legacy'`; an unknown `app_id` never falls back to the legacy row). Always returns `update_required, latest_version, force_update, message, store_url` (nulls and `false` when there is no row). Versions are compared as numeric tuples (`1.0.10 > 1.0.9`, `1.0 == 1.0.0`, `v` prefix and `-beta`/`+build` suffix ignored); an unreadable version means no update, never 500; DB errors give a generic 500. `force_update` = below `min_supported`, or the row flag while `update_required` is true. `store_url` NULL/empty in the row is filled from env `ANDROID_STORE_URL` / `IOS_STORE_URL` at read time. DDL: table already has `app_id` and `UNIQUE (app_id, platform)`; seed rows for `com.clockyn.app` android/ios at `1.0.0`, `store_url` NULL. Open: the Clockyn client (`src/api/system.ts:92`) does not yet send `app_id` (FE-1), so until it does its request reads the `legacy` rows (none are seeded: no update prompt).

### R2 - Employee profile security (BC P3-6, P2-7; DD-42)
* Required: when real auth lands (identity doc R9) enforce `caller == user_id` or admin of the same org; **require OTP re-verification (purpose `update_email` / a new `update_mobile`) before changing `mobile_number` or `email`** and reject values already used by another user (409); validate Aadhaar (12 digits), PAN (10 alphanumeric), IFSC, emergency number length; return masked government IDs for list/profile exports (the app shows full values only on the owner's own screen); remove the PII `print()`s; stop returning exception text.
* Update semantics: support explicit clearing (accept `null` meaning "keep" and `""` meaning "clear", or use PATCH with `exclude_unset`); return 400 for unparseable dates; make the INSERT path safe by defaulting NOT NULL columns to `''`.
* Files: `emp_pers_detail_functions.py:105-210`, `router/employee.py`.
* Acceptance: changing a mobile to an existing number -> 409; sending `dob:null` clears dob; invalid PAN -> 422.

### R3 - Photos (BC P3-8)
* Required: `DELETE /profile-photo/{user_id}` and `DELETE /auth/org-logo/{org_id}` (also remove both `.jpg`/`.png` keys), size/type limits (e.g. 5 MB, JPEG/PNG sniffed), authorisation, correct content type per upload, one canonical route (keep `/auth/*` or `/profile-photo/*` plus alias). Files: `router/auth.py:330-369`, `router/profile_photo.py`, `ml/s3.py:116-187`.
* Acceptance: after DELETE, GET returns `photo_url:null`; a 10 MB or non-image upload -> 413/415.

### R4 - Attribution (DD-38 context; frontend `saveAttribution`)
* Required: validate that `user_id` exists and has role 1 (404 otherwise); set `attribution_platform='meta'` only when campaign ids are present or `source` is a Meta source; return real HTTP errors; fix the report (`u.status = 1`, count log rows for installs, group by the stored source); protect the report endpoint (operator auth). If the new app uses a different ad platform/bundle, add `app_id`/`platform` to the log.
* Files: `router/attribution.py`; migration: optional `app_id` column on `meta_attribution_log`.
* Acceptance: POST for a non-existent user -> 404; report returns rows; organic registrations do not appear as Meta.

### R5 - Invoices and WhatsApp/e-mail branding (BC P3-12, P2-10)
* Required: authenticate/sign `/invoice/*` (token or signed download URL), restrict `bulk-download` to operator role, fix issuer-vs-customer GSTIN, move issuer legal details, product name, support contacts, logo/signature file names, attachment/zip names and plan item text to configuration, escape user-supplied strings in the e-mail HTML, return stable error envelopes (404 `{detail}`). WhatsApp: make template name and app link configuration-only (new provider-approved template for the new brand - allow lead time), pass the **organisation name** (look up `org_info.org_name` by `org_id`), send asynchronously, drop payload logging.
* Files: `utils/email.py`, `core_functions/whatsapp_service.py`, `core_functions/add_staff_function.py:427-440`, `static/*`.
* Acceptance: unauthenticated `bulk-download` -> 401/403; invoice PDF shows the issuer GSTIN from config; WhatsApp variable 2 equals the organisation name.

### R6 - Time zone (BC P2-5)
* Required: use `utils/timezone.py` helpers (IST business date) for trial/plan dates and for any "today" logic in these files; keep `/employee/today-birthdays` on IST (already) and handle 29 Feb on 28 Feb of non-leap years if product wants it. Files: `router/employee.py:26`.

## 9. Test notes

* Fixtures: `users` (roles 2/3 active and inactive, an admin), `users_additional_info` (with and without row), `branch_info/dept_info/role_info`, `app_version_control` rows (android/ios plus duplicate-platform case), `meta_attribution_log`, `gen_invoices` + related joins, fake S3 (moto) and fake SES/MSG91/Meta HTTP endpoints.
* Critical cases: personal-details GET for role 1 (404), inactive (404), no additional-info row (NULLs), PUT creating the additional-info row (NOT NULL failure today), PUT with mobile owned by another user (R2), clearing fields, birthday query on 29 Feb and with `exclude_user_id`; `/version/check` matrix: no row, below min, below latest, equal, invalid string, stored `force_update` true with latest version (R1); attribution POST for missing user, COALESCE behaviour (null does not overwrite), report query on an empty and populated DB; invoice details/history/download with NULL amounts, org filter vs none, bulk-download range with zero rows; WhatsApp payload builder (`_build_employee_onboard_payload`) for numbers beginning `91`, missing env vars returning False; profile-photo round trip and overwrite.
* Frontend contract references: `src/api/__tests__/employee.test.ts`, `src/api/__tests__/supportSystem.test.ts` (version), `src/lib/platform/appUpdateLogic.ts` (pure logic for `interpretVersionCheck`).
* No existing backend tests found for any of these modules (UNVERIFIED beyond a tree search).
