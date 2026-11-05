# Clockyn backend gap analysis

Backend: `C:\POC\pa_backend\Clockyn` (copy of the legacy backend that becomes the Clockyn backend). Frontend: `C:\POC\pa_frontend\Clockyn`.
Written 2026-10-19 by BD9 (documentation only: no source, SQL or config was changed, nothing was run against a database or a network service).
Companion documents: `implementation_roadmap.md` (work packages, decisions, owner configuration) and `testing_strategy.md` (contract tests, local run recipe, Playwright against a real backend).

Naming rule: legacy product/vendor names are not typed here. Where a table, file or constant carries the legacy brand it is cited as "legacy-prefixed" with `file:line` (for example the subscription, settings and manager-permission tables, and the schema-dump file name).
No secrets are reproduced. A secret or credential-like constant is recorded only as "exists at file:line".

## 0. Method and evidence

| Source | What was done |
|---|---|
| Backend routes | AST parse of every `@router.<verb>` decorator in `router/*.py` and `utils/email.py` (the invoice router), with the mount order of `app.py:38-70`. 303 decorators: 301 in mounted routers plus 2 in the root `chat.py` that `app.py` never imports (`/send`, `/upload`), plus `GET /` in `app.py`. |
| Frontend calls | Every `api.get/post/put/delete/upload` call in `src/api/*.ts` (214 call sites), the report/invoice URL builders and the two WebSocket paths, with the templated paths expanded (`{count,turnover,overtime,bonus}`, daily/hourly wage bases, assigned/unassigned, the five live-status lists, the four PDF variants). Result: **239 concrete method+path rows**. |
| Shapes | Compared three ways: (1) what the app reads (`src/api/**/*.ts` mappers), (2) what the mock encodes (`e2e/mock-server/routes/*.js`, `domain.js`), (3) what the backend actually returns (the core function was read in full for every row that is not a plain CRUD echo). |
| Schema | `*_ddl_latest.sql` (legacy-prefixed name) read in full and cross-checked against every table/column the Python code touches. The dump is stale (Appendix A). |
| Other engineers' docs | The domain and database documents appeared while this was being written. Every finding here was derived from the code first; afterwards the key ones were cross-checked and are independently confirmed there: NEW-1 (`domains/org_structure_company_branch_dept.md` D1, `domains/face_recognition_punch.md` D-FP-12), NEW-2 (`domains/attendance_and_home.md` D-AH-02, `database/schema.md` risk 2, which reaches the same partial-unique-index remedy), NEW-6 (`domains/websockets_realtime.md` W-5), NEW-7 (`domains/push_notifications_fcm.md` P-6), NEW-16 (`domains/orders_payments_webhooks_invoices.md` D-3, D-4, D-7), P1-8 (D-FP-01). Items taken from them because the code confirmed them: NEW-17, the `leads_info.status` width, odometer re-stamp/stop-after-approval, the unique `payroll(user_id)` consequence, stale open sessions, savepoints, the pool leak and OTP throttling (roadmap). All line numbers were taken from the code as of this writing. |

Evidence tags used in prose: `[V]` verified in code; `[U]` UNVERIFIED (depends on the deployed database/server, reason given).

IDs: `P0-n`, `P1-n`, `P2-n`, `P3-n`, `C-n` are the frontend's own IDs from `backend_changes.md` (kept so the two documents cross-reference). `NEW-n` are findings added by this review (not in `backend_changes.md`).
Priority: P0 blocks a screen; P1 wrong data or behaviour; P2 consistency; P3 hardening. Effort: S under 1 day, M 1-3 days, L over 3 days (one engineer, including tests). "DB migration" says whether the change needs DDL (Yes), may need it (Check = verify the deployed schema first), or not (No).

## 1. Summary

| Backend route status | Rows |
|---|---|
| EXISTS-MATCHES | 186 |
| EXISTS-DIFFERENT-SHAPE | 26 |
| WRONG-MOUNT (works, but served from the wrong file or a shadowed duplicate) | 5 |
| MISSING | 22 (21 real dependencies + 1 dead export, `GET /auth/push-token/{id}`) |
| **Total** | **239** |

| Priority (highest item on the row) | Rows |
|---|---|
| P0 | 22 (21 missing routes in 7 groups + the version check) |
| P1 | 64 |
| P2 | 30 |
| P3 | 21 |
| none | 102 |

Item level: 9 P0 items (P0-1 to P0-9), 17 frontend P1 items (P1-1 to P1-17), 13 new P1 findings (NEW-1, 2, 3, 4, 5, 6, 8, 12, 13, 14, 15, 17 and NEW-16 billing integrity, Appendix E), 1 new P2 (NEW-7), plus the P2/P3 lists of `backend_changes.md` (not re-designed here, see the table rows).

Backend routes the app never calls: 86 of 303 (Appendix D): operator console (21 `/gen-dashboard/*`, 6 operator-login/admin-user routes), plan/coupon admin CRUD (8), legacy `/announcement/*` (5), duplicate holiday routers (10), suggestion/support admin (6), debug and SSE routes, the two unmounted root routes.

### 1.1 Five facts that change the plan

1. **The app opens only `/ws/chat/{userId}` but the backend sends `request_submitted` and punch `badge_update` frames only on `/ws/badge/{userId}`** (NEW-6). Admin badges and punch notifications will not arrive live. The mock hides this because it delivers every frame to every socket of a user (`e2e/mock-server/index.js:327-358`).
2. **The staff punch-in path returns `success:false` after committing** (P1-8, `core_functions/face_attendance_functions.py:264`, a `NameError` for `img_url`, caught at `:375-378`). It hits every role 2/3 punch-in unless the deployed server is already patched [U].
3. **`branch_id` is not unique across organisations** (NEW-1): registration and add-staff number branches per organisation from 1, add-branch uses a table-wide `MAX+1`, and about ten queries join, look up or delete by `branch_id` alone. With two organisations the geofence, the daily attendance report and branch lookups cross tenants.
4. **Schema and code disagree in several places** (NEW-14, NEW-2): the dump has a unique index on `(user_id, attendance_date)` while the code stores up to two rows per user per day and upserts with `ON CONFLICT (user_id, attendance_date)`; 8 objects used by code are absent from the dump (3 tables, 1 sequence, 4 columns; Appendix A). A baseline taken from the deployed database must precede every migration.
5. **Approved requests have no downstream effect** (NEW-8): an approved shift change never changes a shift, an approved leave never reaches attendance or salary, and monthly-fixed pay is never prorated by attendance. The attendance rule (P0-4) therefore has nothing to hook into until a salary-engine decision (DD-60/61) is made.

## 2. Verification deltas against `backend_changes.md`

| Item | `backend_changes.md` says | This review found |
|---|---|---|
| P2-13 chat delete | per-side or both unknown | **Resolved**: `core_functions/chat_functions.py:772-794` deletes the messages of both users, permanently. The app copy "This cannot be undone" is right but should say the other person loses it too. |
| P1-9 payslip template delete | deletes earnings, deductions and the template | `core_functions/salary_structure.py:325-340` deletes **only** the `pay_slip` row: custom earnings, custom deductions and `payroll` assignments are all orphaned. |
| P1-10 live lists | "lack `role_id`, `is_active`, photo" | Only the five live-status lists lack them (`home_functions.py:603,680,753,831`); `/home/all-staff` and the staff list already return them (`:362-372`, `:531-541`). |
| P2-5 UTC vs IST | punch times are UTC clock strings | `/home/daily-attendance`, `/home/monthly-attendance` and `/attendance/today` already convert with `format_ist_time`. The unconverted ones are `/attendance/sessions` (`router/face_attendance.py:78-79`, NEW-3), `/staff-location/*`, `/home/staff-punch-locations` (`TO_CHAR` in the connection time zone) and the attendance PDF (`report_functions.py:31-38`). The connection time zone is never set (`core_functions/db_function.py:34-40`), so all of these depend on the database default [U]. |
| P1-3 late test | "should be re-checked" | The same `punch_in::time > shift.start_time` comparison exists in four dashboard queries (`home_functions.py:187,438,658,941`) and in `attendance_functions.py:199`. If the database default is UTC, lateness is wrong by 5 h 30 m [U]. |
| P0-8 version shape | one row per platform | Also: the route answers `{success, data:{...}}` (`router/version.py:36-45`) and the mock answers the object flat; the app accepts both (`objectOf`). |
| P1-5 holidays | order-dependent | Confirmed. Additionally `org_off_days` (weekly offs, `holiday_functions.py:12-43`) is missing from the dump, like the `block_attendance` column. |
| P3-5 | header required | Confirmed: `_require_gen_admin` (`router/auth.py:148-163`). |
| P1-17 | scope applied only to some routes | `get_department_stats` (`home_functions.py:389-470`) has no scope call at all; `get_manager_scope` is used at `home_functions.py:119,233,298,478,559,626,703,781` and `attendance_functions.py:56`. |

## 3. Gap table (one row per frontend-called endpoint)

Columns: caller = `src/api/<file>` + exported function + line of the call; evidence = route decorator then the code that proves the gap; Pri/Effort/DB as defined in section 0. "None" in Required change means shape and behaviour are acceptable for the app (the global gaps P3-1 no authentication and P2-1 error wrapping apply to every row and are not repeated).

#### `src/api/announcements.ts` (4)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 1 | `announcements.ts` `createAnnouncement`:81 | `POST /api/announcements` | EXISTS-MATCHES | router/announcement.py:10; core_functions/announcement_functions.py:105-126 (push only, no WS publish); :68-70 recipient_count is 0 unless target=selected | P1-7: publish WS `announcement` frame to recipients; return the real recipient_count | P1 | S | No |
| 2 | `announcements.ts` `listAnnouncements`:127 | `GET /api/announcements/{id}` | EXISTS-MATCHES | router/announcement.py:18 | None | - | - | No |
| 3 | `announcements.ts` `deleteAnnouncement`:134 | `DELETE /api/announcements/{id}` | EXISTS-MATCHES | router/announcement.py:26; core_functions/announcement_functions.py:170-193 (no org or caller check) | P1-9: scope by org_id, require the sender/admin | P1 | S | No |
| 4 | `announcements.ts` `listEmployeeAnnouncements`:140 | `GET /api/announcements/employee/{id}` | EXISTS-MATCHES | router/announcement.py:34 | None | - | - | No |

#### `src/api/attendance.ts` (2)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 5 | `attendance.ts` `getToday`:11 | `GET /attendance/today/{id}` | EXISTS-MATCHES | router/face_attendance.py:52; core_functions/face_attendance_functions.py:394 (UTC date) | P2-5: IST business date; DD-05: document multi-session and midnight semantics | P2 | S | No |
| 6 | `attendance.ts` `getSessions`:18 | `GET /attendance/sessions/{id}` | EXISTS-DIFFERENT-SHAPE | router/face_attendance.py:58; router/face_attendance.py:78-79 (no IST conversion), :86-88 (HTTP 200 success:false on error); no work_hours | NEW-3: format with format_ist_time, add work_hours, return a real error status | P1 | S | No |

#### `src/api/attribution.ts` (1)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 7 | `attribution.ts` `saveAttribution`:9 | `POST /attribution/meta` | EXISTS-MATCHES | router/attribution.py:21 | None | - | - | No |

#### `src/api/auth.ts` (15)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 8 | `auth.ts` `login`:35 | `POST /auth/login` | EXISTS-MATCHES | router/auth.py:284; core_functions/auth_function.py:953-1036 | P3-1: issue an access token here (and on OTP/device verify) when auth is adopted | P3 | S | No |
| 9 | `auth.ts` `login`:35 | `POST /auth/employee-login` | EXISTS-MATCHES | router/auth.py:288; core_functions/auth_function.py:1038-1111 (no `email`) | Add `email` and `current_org_id` like admin login; P3-1 token | P2 | S | No |
| 10 | `auth.ts` `requestOtp`:41 | `POST /auth/generate-otp` | EXISTS-MATCHES | router/auth.py:292; core_functions/auth_function.py:508-630; SMS failure ignored at :614-616 | P2-7: per-contact/IP rate limit, surface provider failure; the SMS wording lives in the provider template (owner item) | P2 | S | No |
| 11 | `auth.ts` `verifyOtp`:61 | `POST /auth/validate-otp` | EXISTS-MATCHES | router/auth.py:296; core_functions/auth_function.py:29-34,517,645 (hard-coded review-account OTP bypass; numbers not reproduced here) | Gate the bypass behind an env flag that is off in production | P3 | S | No |
| 12 | `auth.ts` `verifyDeviceToken`:82 | `POST /auth/verify-device-token` | EXISTS-DIFFERENT-SHAPE | router/auth.py:300; core_functions/auth_function.py:772-905; mismatch branch :878-897 (auto-submits request, no request_pending); admin success lacks plan fields | P1-14: send request_pending, dedupe pending request; return plan_id/is_free_trial for admins | P1 | S | No |
| 13 | `auth.ts` `registerCompany`:93 | `POST /auth/register` | EXISTS-MATCHES | router/auth.py:269; core_functions/auth_function.py:473-478 (HTTP 400 re-wrapped as 500); :392-399 branches created with POINT(0,0) | P2-3: re-raise HTTPException; P2-10 branding; geofence is off until the branch is edited | P2 | S | No |
| 14 | `auth.ts` `checkMobileExists`:102 | `GET /auth/check-mobile/{id}` | EXISTS-DIFFERENT-SHAPE | router/auth.py:304; router/auth.py:304-316 requires operator header via _require_gen_admin (:148-163) | P3-5: allow unauthenticated {exists}, rate-limited | P3 | S | No |
| 15 | `auth.ts` `checkEmailExists`:108 | `GET /auth/check-email/{id}` | EXISTS-DIFFERENT-SHAPE | router/auth.py:317; router/auth.py:317-329 (same helper) | P3-5: as check-mobile | P3 | S | No |
| 16 | `auth.ts` `updatePushToken`:114 | `POST /auth/update-push-token` | EXISTS-MATCHES | router/auth.py:277; core_functions/auth_function.py:131-161; empty token rejected :136-138 | P3-7: allow clearing / add POST /auth/logout; P3-10 stop logging tokens; see P0-9 and NEW-7 | P3 | S | No |
| 17 | `auth.ts` `getUserMobile`:120 | `GET /auth/user-mobile/{id}` | EXISTS-MATCHES | router/auth.py:371; router/auth.py:371-383 (mobile by id, no auth) | P3-6: authorisation | P3 | S | No |
| 18 | `auth.ts` `getOrgLogoUrl`:129 | `GET /auth/org-logo/{id}` | EXISTS-MATCHES | router/auth.py:340 | None | - | - | No |
| 19 | `auth.ts` `getEmployeePhotoUrl`:136 | `GET /auth/employee-profile/{id}` | EXISTS-MATCHES | router/auth.py:362 | None | - | - | No |
| 20 | `auth.ts` `getProfilePhotoUrl`:143 | `GET /profile-photo/{id}` | EXISTS-MATCHES | router/profile_photo.py:7 | None | - | - | No |
| 21 | `auth.ts` `uploadOrgLogo`:150 | `POST /auth/upload-org-logo/{id}` | EXISTS-MATCHES | router/auth.py:330; router/auth.py:330-338 | P3-8: size/type limit, ownership check | P3 | S | No |
| 22 | `auth.ts` `uploadEmployeePhoto`:159 | `POST /auth/upload-employee-profile/{id}` | EXISTS-MATCHES | router/auth.py:352; router/auth.py:352-360 | P3-8: size/type limit, ownership check | P3 | S | No |

#### `src/api/billing.ts` (7)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 23 | `billing.ts` `checkFirstPayment`:79 | `GET /orders/check_first_payment` | EXISTS-MATCHES | router/gen_orders.py:28 | None | - | - | No |
| 24 | `billing.ts` `getFirstCoupon`:85 | `GET /plans/coupon/first` | EXISTS-MATCHES | router/gen_plans.py:49 | None | - | - | No |
| 25 | `billing.ts` `validateCoupon`:96 | `GET /plans/coupon/validate` | EXISTS-MATCHES | router/gen_plans.py:53 | None | - | - | No |
| 26 | `billing.ts` `createOrder`:104 | `POST /orders/create` | EXISTS-MATCHES | router/gen_orders.py:17; core_functions/gen_orders_functions.py:12-18 (client amount trusted), :97 uses gen_orders.coupon_code (not in schema dump) | P3-2/DD-15: recompute amount server-side from plan_id, staff_limit, coupon, credit, GST | P3 | M | Check |
| 27 | `billing.ts` `verifyPayment`:120 | `POST /payments/verify` | EXISTS-MATCHES | router/gen_payments.py:16; core_functions/gen_payment_functions.py:69-92 (same plan = capacity top-up, end_date kept); `wants_gst_invoice` unused; :171 place of supply constant | P1-13: explicit renew intent that extends end_date; honour wants_gst_invoice or drop it | P1 | M | Yes (column) |
| 28 | `billing.ts` `listInvoices`:138 | `GET /invoice/history` | EXISTS-MATCHES | utils/email.py:863; utils/email.py:863-918 (user_id/org_id only) | P3-12: token / signed URL | P3 | S | No |
| 233 | `billing.ts` `invoiceDownloadUrl`:145 | `GET /invoice/download` | EXISTS-MATCHES | utils/email.py:921; utils/email.py:921-1095 | P3-12: signed URL | P3 | S | No |

#### `src/api/branches.ts` (7)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 29 | `branches.ts` `listBranches`:16 | `GET /home/branches` | EXISTS-MATCHES | router/home.py:34 | None | - | - | No |
| 30 | `branches.ts` `getBranch`:22 | `GET /branch/{id}` | EXISTS-DIFFERENT-SHAPE | router/branch.py:41; core_functions/branch_functions.py:131-135 (WHERE branch_id only; ids repeat across orgs) | NEW-1: scope by org (new optional org_id query) or re-key branch ids | P1 | S | No |
| 31 | `branches.ts` `addBranch`:33 | `POST /branch/add` | EXISTS-MATCHES | router/branch.py:10; core_functions/branch_functions.py:83 (global MAX+1) vs per-org MAX+1 at add_staff_function.py:342,545 and auth_function.py:392-399 | NEW-1: one global branch id sequence | P1 | S | Yes (sequence) |
| 32 | `branches.ts` `updateBranch`:39 | `PUT /branch/update/{id}` | EXISTS-MATCHES | router/branch.py:19 | None | - | - | No |
| 33 | `branches.ts` `deleteBranch`:45 | `POST /branch/delete/{id}` | EXISTS-MATCHES | router/branch.py:24; core_functions/branch_functions.py:241-244 (DELETE without org_id); inactive staff keep dangling branch_id | P1-9: add org_id to DELETE, null out inactive staff | P1 | S | No |
| 34 | `branches.ts` `getBranchStaff`:51 | `GET /branch/staff/{id}` | EXISTS-MATCHES | router/branch.py:33 | None | - | - | No |
| 35 | `branches.ts` `reassignStaff`:72 | `POST /branch/reassign-staff` | EXISTS-MATCHES | router/branch.py:46; core_functions/branch_functions.py:331-335 (remaining-staff count not org-scoped) | P1-9/NEW-1: scope the count by parent_id | P1 | S | No |

#### `src/api/chat.ts` (9)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 36 | `chat.ts` `getChatList`:128 | `GET /chat/list/{id}` | EXISTS-DIFFERENT-SHAPE | router/chat.py:168; router/chat.py:168-174 (plan 403 re-wrapped as 500); core_functions/chat_functions.py:143 (`is_online` hard-coded false) | P2-2: check plan outside the try; derive is_online from socket_manager | P2 | S | No |
| 37 | `chat.ts` `searchChatStaff`:139 | `GET /chat/search/{id}` | EXISTS-MATCHES | router/chat.py:161 | None | - | - | No |
| 38 | `chat.ts` `getChatMessages`:145 | `GET /chat/messages/{id}/{id}` | EXISTS-MATCHES | router/chat.py:176 | None | - | - | No |
| 39 | `chat.ts` `getReceiverPushToken`:152 | `GET /auth/push-token/{id}` | MISSING | No route in the app.py mount table; dead export src/api/chat.ts:150-159 (no caller) | Remove the export; do NOT add the route | P3 | S | No |
| 40 | `chat.ts` `sendChatMessage`:162 | `POST /chat/send` | EXISTS-MATCHES | router/chat.py:183; router/chat.py:198 (push data has no message_id) | P1-7: add message_id to push data | P1 | S | No |
| 41 | `chat.ts` `sendChatFile`:174 | `POST /chat/send-file` | EXISTS-MATCHES | router/chat.py:359; router/chat.py:411 (push data without message_id) | P1-7: add message_id to push data | P1 | S | No |
| 42 | `chat.ts` `markChatRead`:198 | `PUT /chat/mark-read` | EXISTS-MATCHES | router/chat.py:204 | None | - | - | No |
| 43 | `chat.ts` `deleteConversation`:204 | `DELETE /chat/delete/{id}/{id}` | EXISTS-MATCHES | router/chat.py:217; core_functions/chat_functions.py:772-794 (deletes the messages of BOTH users, permanently) | P2-13 resolved: fix the app copy, or add per-side delete (chat.deleted_for) | P2 | S | Optional |
| 238 | `chat.ts` `chatSocketPath`:210 | `WS /ws/chat/{id}` | EXISTS-DIFFERENT-SHAPE | router/chat.py:137; router/chat.py:137-144; request_submitted/badge_update go only through router/ws_badge.py:12-20 (/ws/badge, which the app never opens); announcement never sent | NEW-6/P1-7: deliver badge/request/announcement frames on this socket; P3-1 authenticate; in-memory registry (socket_manager.py:9) | P1 | S | No |

#### `src/api/company.ts` (8)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 44 | `company.ts` `addCompany`:23 | `POST /company/add` | EXISTS-MATCHES | router/company.py:9 | None | - | - | No |
| 45 | `company.ts` `updateCompany`:28 | `PUT /company/update/{id}` | EXISTS-MATCHES | router/company.py:13; core_functions/company_functions.py:279-314 (deletes and re-creates ALL departments with new ids whenever the name set differs, and all branches when `branches` is non-empty: orphans users.dept_id/branch_id, manager_assignments, announcements); no ownership check (:232) | NEW-17: diff-based patch (add/remove by name, never re-key), refuse removing a department/branch that has staff, require user_id == org_info.parent_id | P1 | M | No |
| 46 | `company.ts` `getCompanyDetails`:33 | `GET /company/details/{id}` | EXISTS-MATCHES | router/company.py:26 | None | - | - | No |
| 47 | `company.ts` `listCompanies`:39 | `GET /company/list/{id}` | EXISTS-MATCHES | router/company.py:30 | None | - | - | No |
| 48 | `company.ts` `deleteCompany`:48 | `DELETE /company/delete/{id}/{id}` | EXISTS-MATCHES | router/company.py:34; core_functions/company_functions.py:175-231 (users, branches, depts, shifts only); the cascades at :191-203 run for ANY org id and ownership is checked only by the last statement :203 (a user owning two companies can wipe another organisation) | P1-9: verify ownership first, then cascade or soft-delete attendance, payroll, requests, leads, chat | P1 | M | No |
| 49 | `company.ts` `updateCompanyLogo`:59 | `POST /company/update-logo/{id}` | EXISTS-MATCHES | router/company.py:17 | None | - | - | No |
| 50 | `company.ts` `deleteAccount`:67 | `DELETE /company/delete-account/{id}` | EXISTS-MATCHES | router/company.py:38; core_functions/company_functions.py:408-483 | P1-9/P3-9: ownership check, confirmation token, retention rules (DD-40) | P1 | M | No |
| 51 | `company.ts` `requestAccountDeletion`:73 | `POST /company/request-deletion/{id}` | EXISTS-MATCHES | router/company.py:43 | None | - | - | No |

#### `src/api/departments.ts` (4)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 52 | `departments.ts` `listDepartments`:11 | `GET /home/departments` | EXISTS-MATCHES | router/home.py:39 | None | - | - | No |
| 53 | `departments.ts` `addDepartment`:23 | `POST /department/add` | EXISTS-MATCHES | router/department.py:15 | None | - | - | No |
| 54 | `departments.ts` `updateDepartment`:29 | `PUT /department/update/{id}` | EXISTS-MATCHES | router/department.py:20 | None | - | - | No |
| 55 | `departments.ts` `deleteDepartment`:35 | `POST /department/delete/{id}` | EXISTS-MATCHES | router/department.py:25; core_functions/dept_functions.py:132-133 (UPDATE/DELETE without org_id, staff un-assigned silently) | P1-9: scope by org, block or reassign | P1 | S | No |

#### `src/api/employee.ts` (3)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 56 | `employee.ts` `getPersonalDetails`:80 | `GET /employee/personal-details/{id}` | EXISTS-MATCHES | router/employee.py:10; router/employee.py:10-13 (PII by id, active only) | P3-6: authorisation | P3 | S | No |
| 57 | `employee.ts` `updatePersonalDetails`:86 | `PUT /employee/personal-details/{id}` | EXISTS-MATCHES | router/employee.py:15 | None | - | - | No |
| 58 | `employee.ts` `getTodayBirthdays`:105 | `GET /employee/today-birthdays/{id}` | EXISTS-MATCHES | router/employee.py:20 | None | - | - | No |

#### `src/api/expenses.ts` (13)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 59 | `expenses.ts` `getOdometerReadings`:29 | `GET /expenses/odometer/readings` | EXISTS-MATCHES | router/emp_expenses.py:50 | None | - | - | No |
| 60 | `expenses.ts` `startOdometer`:38 | `POST /expenses/odometer/start` | EXISTS-MATCHES | router/emp_expenses.py:38 | None | - | - | No |
| 61 | `expenses.ts` `stopOdometer`:51 | `POST /expenses/odometer/stop` | EXISTS-MATCHES | router/emp_expenses.py:44; core_functions/emp_expenses_functions.py:119-181 (no status check: an approved reading can be overwritten; reading chosen by fetchone on user/org/date) | P1-12: refuse unless pending; deterministic reading selection | P1 | S | No |
| 62 | `expenses.ts` `uploadOdometerPhoto`:63 | `POST /expenses/odometer/upload-photo` | EXISTS-MATCHES | router/emp_expenses.py:83 | None | - | - | No |
| 63 | `expenses.ts` `submitExpense`:81 | `POST /expenses/travel-bill/submit` | EXISTS-MATCHES | router/emp_expenses.py:126 | None | - | - | No |
| 64 | `expenses.ts` `uploadExpensePhoto`:94 | `POST /expenses/travel-bill/upload-photo` | EXISTS-MATCHES | router/emp_expenses.py:142 | None | - | - | No |
| 65 | `expenses.ts` `listMyExpenses`:106 | `GET /expenses/travel-bill/expenses` | EXISTS-MATCHES | router/emp_expenses.py:132 | None | - | - | No |
| 66 | `expenses.ts` `listOdometerReadings`:120 | `GET /expenses/admin/odometer-readings/{id}` | EXISTS-MATCHES | router/emp_expenses.py:59 | None | - | - | No |
| 67 | `expenses.ts` `reviewOdometerReading`:127 | `PUT /expenses/admin/odometer-readings/{id}/status` | EXISTS-MATCHES | router/emp_expenses.py:65; core_functions/emp_expenses_functions.py:429-446 (rate taken from the most recently updated reading) | P1-12: read the rate from its own table | P1 | S | No |
| 68 | `expenses.ts` `listExpenses`:136 | `GET /expenses/admin/travel-bill/{id}` | EXISTS-MATCHES | router/emp_expenses.py:114 | None | - | - | No |
| 69 | `expenses.ts` `reviewExpense`:145 | `PUT /expenses/admin/travel-bill/{id}/status` | EXISTS-MATCHES | router/emp_expenses.py:120 | None | - | - | No |
| 70 | `expenses.ts` `getAllowanceRate`:153 | `GET /expenses/admin/allowance-rate/{id}` | EXISTS-MATCHES | router/emp_expenses.py:77; core_functions/emp_expenses_functions.py:497-519 (rate of latest reading; null when none) | P1-12: org_allowance_rate table, default when unset | P1 | S | Yes (new table) |
| 71 | `expenses.ts` `setAllowanceRate`:165 | `POST /expenses/admin/allowance-rate` | EXISTS-MATCHES | router/emp_expenses.py:71; core_functions/emp_expenses_functions.py:477-483 (UPDATE of every reading incl. approved); router/emp_expenses.py:33-36 `rate:int` | P1-12/P2-4: persist in own table, stamp pending rows only, accept decimals | P1 | S | Yes (new table) |

#### `src/api/face.ts` (5)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 72 | `face.ts` `isFaceRegistered`:20 | `GET /face/check/{id}` | EXISTS-MATCHES | router/face_attendance.py:12 | None | - | - | No |
| 73 | `face.ts` `checkPunchLocation`:34 | `POST /face/check-location` | EXISTS-MATCHES | router/face_attendance.py:18; core_functions/face_attendance_functions.py:41 (branch join without org) | NEW-1: scope the join by parent_id | P1 | S | No |
| 74 | `face.ts` `punch`:47 | `POST /face/punch` | EXISTS-DIFFERENT-SHAPE | router/face_attendance.py:38; core_functions/face_attendance_functions.py:264 (`img_url` undefined) with except at :375-378; :157 unscoped branch join; :134-136 force_accept | P1-8: define img_url / move the notification; NEW-1; P3-3: restrict force_accept | P1 | S | No |
| 75 | `face.ts` `registerFace`:75 | `POST /face/register` | EXISTS-MATCHES | router/face_attendance.py:28 | None | - | - | No |
| 76 | `face.ts` `saveProfilePhoto`:90 | `POST /profile-photo/{id}` | EXISTS-MATCHES | router/profile_photo.py:14; router/profile_photo.py:14-19 | P3-8: ownership, limits, delete route | P3 | S | No |

#### `src/api/groupChat.ts` (9)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 77 | `groupChat.ts` `listGroups`:90 | `GET /group-chat/list/{id}` | EXISTS-MATCHES | router/chat.py:226; router/chat.py:226-232 (plan 403 re-wrapped as 500) | P2-2: check plan outside the try | P2 | S | No |
| 78 | `groupChat.ts` `createGroup`:105 | `POST /group-chat/create` | EXISTS-MATCHES | router/chat.py:234 | None | - | - | No |
| 79 | `groupChat.ts` `getGroupMessages`:117 | `GET /group-chat/messages/{id}` | EXISTS-MATCHES | router/chat.py:246 | None | - | - | No |
| 80 | `groupChat.ts` `sendGroupMessage`:123 | `POST /group-chat/send` | EXISTS-MATCHES | router/chat.py:253; router/chat.py:286 (push without message_id); core_functions/chat_functions.py:509-511 (no total_members) | P1-7: add message_id; add total_members to the response | P1 | S | No |
| 81 | `groupChat.ts` `getGroupMembers`:130 | `GET /group-chat/members/{id}` | EXISTS-MATCHES | router/chat.py:292 | None | - | - | No |
| 82 | `groupChat.ts` `addGroupMembers`:136 | `POST /group-chat/add-members` | EXISTS-MATCHES | router/chat.py:314; router/chat.py:314-319 (no WS event); core_functions/chat_functions.py:609 returns {success, added} | P1-6: emit group_updated to existing and new members | P1 | S | No |
| 83 | `groupChat.ts` `markGroupRead`:142 | `PUT /group-chat/mark-read` | EXISTS-MATCHES | router/chat.py:299; router/chat.py:299-311 (payload reader_id/group_id only, also sent to the reader) | P1-6: add message_id/read_count/total_members, skip the reader | P1 | S | No |
| 84 | `groupChat.ts` `sendGroupFile`:148 | `POST /group-chat/send-file` | EXISTS-MATCHES | router/chat.py:418; router/chat.py:478 (push without message_id) | P1-7: add message_id | P1 | S | No |
| 239 | `groupChat.ts` `groupSocketPath`:165 | `WS /ws/group-chat/{id}/{id}` | EXISTS-MATCHES | router/chat.py:147; router/chat.py:147-156 (registers the same user socket again) | Duplicate delivery per member (the app de-dupes by message_id); keep for compatibility | P2 | S | No |

#### `src/api/home.ts` (15)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 85 | `home.ts` `getDashboardStats`:33 | `GET /home/dashboard-status` | EXISTS-MATCHES | router/home.py:14; core_functions/home_functions.py:187 (`punch_in::time` in DB session tz vs local shift time); working == punched_in | P1-3/NEW-1: IST-correct lateness; DD-63 working semantics | P1 | S | No |
| 86 | `home.ts` `getDepartmentStats`:39 | `GET /home/department-stats` | EXISTS-MATCHES | router/home.py:53; core_functions/home_functions.py:389-470 (no get_manager_scope), :438 lateness as above | P1-17: manager scope; IST lateness | P1 | S | No |
| 87 | `home.ts` `getCurrentDate`:45 | `GET /home/current-date` | EXISTS-MATCHES | router/home.py:112; core_functions/home_functions.py:849-860 (formatted_date is DD-MM-YYYY; the mock sends "D Mon YYYY"); no screen calls it | None (keep; fix the mock string) | - | - | No |
| 88 | `home.ts` `getStaffByStatus`:62 | `GET /home/absent-staff` | EXISTS-DIFFERENT-SHAPE | router/home.py:67; core_functions/home_functions.py:603-611 (StaffResponse without role_id/is_active) | P1-10: add role_id, is_active, photo | P1 | S | No |
| 89 | `home.ts` `getStaffByStatus`:62 | `GET /home/late-punched-in-staff` | EXISTS-DIFFERENT-SHAPE | router/home.py:76; core_functions/home_functions.py:658,680-688 (UTC clock vs shift start; no role_id) | P1-10 + IST lateness | P1 | S | No |
| 90 | `home.ts` `getStaffByStatus`:62 | `GET /home/punched-in-staff` | EXISTS-DIFFERENT-SHAPE | router/home.py:85; core_functions/home_functions.py:753-761 | P1-10: add role_id, is_active, photo | P1 | S | No |
| 91 | `home.ts` `getStaffByStatus`:62 | `GET /home/working-staff` | EXISTS-DIFFERENT-SHAPE | router/home.py:94; core_functions/home_functions.py:771-773 (alias of punched-in) | P1-10; DD-63 | P1 | S | No |
| 92 | `home.ts` `getStaffByStatus`:62 | `GET /home/punched-out-staff` | EXISTS-DIFFERENT-SHAPE | router/home.py:103; core_functions/home_functions.py:831-839 | P1-10: add role_id, is_active, photo | P1 | S | No |
| 93 | `home.ts` `getAllStaff`:68 | `GET /home/all-staff` | EXISTS-MATCHES | router/home.py:62; core_functions/home_functions.py:497 (CURRENT_DATE in DB tz, no target_date) | P2-5: IST date | P2 | S | No |
| 94 | `home.ts` `getStaffDetail`:84 | `GET /home/staff-detail` | EXISTS-DIFFERENT-SHAPE | router/home.py:122; core_functions/home_functions.py:1095-1149 (no branch_id; status=1 only :1112; 404 wrapped as 500 :1146) | P1-2/P1-10: add branch_id, return inactive with a flag, re-raise 404 | P1 | S | No |
| 95 | `home.ts` `getStaffProfile`:90 | `GET /home/staff-profile` | EXISTS-DIFFERENT-SHAPE | router/home.py:130; core_functions/home_functions.py:1151-1228 (role_id selected but not returned; 404 wrapped :1225) | P1-2/P1-10: add branch_id, role_id, is_active | P1 | S | No |
| 96 | `home.ts` `getDailyAttendance`:103 | `GET /home/daily-attendance` | EXISTS-DIFFERENT-SHAPE | router/home.py:138; core_functions/attendance_functions.py:76-77 (branch/dept joins without org: duplicate staff rows when ids repeat); no role_id/is_active | NEW-1: scope joins; P1-10 fields | P1 | S | No |
| 97 | `home.ts` `getMonthlyAttendance`:117 | `GET /home/monthly-attendance` | EXISTS-DIFFERENT-SHAPE | router/home.py:148; core_functions/attendance_functions.py:154-250 (no shift times/day types; :199 UTC vs local; :229 absent = days - rows; :241-250 swallows errors) | P1-3: day types, shift times, late_by, IST lateness, real errors | P1 | M | No |
| 98 | `home.ts` `updateAttendancePunch`:130 | `PUT /home/update-attendance-punch` | EXISTS-MATCHES | router/home.py:210; core_functions/attendance_functions.py:314-320 (ON CONFLICT (user_id, attendance_date)) vs multi-row sessions | NEW-2: resolve unique index vs two sessions per day; edit by session | P1 | S | Check |
| 99 | `home.ts` `getOrgAdmin`:144 | `GET /home/org-admin/{id}` | EXISTS-MATCHES | router/home.py:174 | None | - | - | No |

#### `src/api/leads.ts` (16)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 100 | `leads.ts` `listLeads`:172 | `GET /api/leads/{id}` | EXISTS-DIFFERENT-SHAPE | router/leads.py:82; core_functions/leads_functions.py:52-63 (DISTINCT lead x assignment; no l.status so no lead_status) | P1-11/NEW-5: one row per lead, aggregate assignees, return lead_status | P1 | M | No |
| 101 | `leads.ts` `listEmployeeLeads`:179 | `GET /api/leads/employee/{id}/{id}` | EXISTS-MATCHES | router/leads.py:33; core_functions/leads_functions.py:78 (date.today()) | P2-5: IST date | P2 | S | No |
| 102 | `leads.ts` `createLead`:186 | `POST /api/leads/create` | EXISTS-MATCHES | router/leads.py:15 | None | - | - | No |
| 103 | `leads.ts` `updateLead`:193 | `PUT /api/leads/{id}` | EXISTS-MATCHES | router/leads.py:108; core_functions/leads_functions.py:151-153 (UPDATE by leads_id only) | P3-1: org scope | P3 | S | No |
| 104 | `leads.ts` `deleteLead`:199 | `DELETE /api/leads/{id}` | EXISTS-MATCHES | router/leads.py:87; core_functions/leads_functions.py:121-137 (no org check; assignments left) | P1-9: org scope, delete assignments and requests | P1 | S | No |
| 105 | `leads.ts` `assignLead`:214 | `POST /api/leads/assign` | EXISTS-MATCHES | router/leads.py:20; core_functions/leads_functions.py:102-118 (always INSERT) | P1-11: upsert / reject a duplicate active assignment | P1 | S | No |
| 106 | `leads.ts` `reassignLead`:221 | `PUT /api/leads/assignment/{id}` | EXISTS-MATCHES | router/leads.py:99; core_functions/leads_functions.py:218-233 (UPDATE all rows of the lead) | P1-11: update one assignment | P1 | S | No |
| 107 | `leads.ts` `updateLeadWorkStatus`:228 | `PUT /api/leads/{id}/work-status` | EXISTS-MATCHES | router/leads.py:91 | None | - | - | No |
| 108 | `leads.ts` `updateLeadPipelineStatus`:234 | `PUT /api/leads/{id}/status` | EXISTS-MATCHES | router/leads.py:95; core_functions/leads_functions.py:446-466 writes leads_info.status, `varchar(10)` in the schema dump: `not_interested` (14 characters) fails with 500; default `pending` is not a pipeline value | P1-11: widen to varchar(20), default new | P1 | S | Yes (column) |
| 109 | `leads.ts` `listLeadCategories`:240 | `GET /api/leads/categories/{id}` | EXISTS-MATCHES | router/leads.py:38 | None | - | - | No |
| 110 | `leads.ts` `createLeadCategory`:248 | `POST /api/leads/categories` | EXISTS-MATCHES | router/leads.py:42; core_functions/leads_functions.py:183-196 (no duplicate guard) | P2-7: reject duplicate name per org | P2 | S | No |
| 111 | `leads.ts` `deleteLeadCategory`:254 | `DELETE /api/leads/categories/{id}` | EXISTS-MATCHES | router/leads.py:49; core_functions/leads_functions.py:199-215 (no org scope; leads keep the dangling category_id) | P1-9 family: org scope, refuse while leads use the category | P2 | S | No |
| 112 | `leads.ts` `listCarryForwardRequests`:260 | `GET /api/leads/carry-forward/requests/{id}` | EXISTS-MATCHES | router/leads.py:68; core_functions/leads_functions.py:361-398 (approved requests stay listed) | P1-11/DD-94: lifecycle fields, has_pending_cfr | P1 | M | No |
| 113 | `leads.ts` `listMyCarryForwardRequests`:267 | `GET /api/leads/carry-forward/my/{id}/{id}` | EXISTS-DIFFERENT-SHAPE | router/leads.py:53; core_functions/leads_functions.py:339-352 (request_date is the forward-to date; no created_at) | Add created_at/submitted_at; keep request_date | P2 | S | No |
| 114 | `leads.ts` `requestCarryForward`:281 | `POST /api/leads/carry-forward/request` | EXISTS-MATCHES | router/leads.py:58; core_functions/leads_functions.py:273-289 (DDL in handler), :304-307 (status not persisted) | P1-11: persist carry_forward status, move DDL to a migration | P1 | M | Yes (migration) |
| 115 | `leads.ts` `actOnCarryForward`:288 | `PUT /api/leads/carry-forward/{id}/action` | EXISTS-MATCHES | router/leads.py:72; core_functions/leads_functions.py:415-429 (approve deletes the assignment; reject changes nothing) | P1-11/DD-94 | P1 | M | No |

#### `src/api/location.ts` (5)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 116 | `location.ts` `logLocation`:15 | `POST /staff-location/log` | EXISTS-MATCHES | router/staff_location.py:12; core_functions/staff_location_functions.py:56-69 (single fetchone on 1-2 rows, UTC date) | NEW-4: use the open session of the IST day | P1 | S | No |
| 117 | `location.ts` `listStaffLocations`:27 | `GET /staff-location/locations` | EXISTS-MATCHES | router/staff_location.py:16; core_functions/staff_location_functions.py:130-137 (LEFT JOIN attendance without aggregation: two sessions give two rows per staff); :106 UTC default date; TO_CHAR in DB tz | NEW-4: aggregate sessions; P2-5 IST date | P1 | S | No |
| 118 | `location.ts` `getLocationHistory`:34 | `GET /staff-location/history/{id}` | EXISTS-MATCHES | router/staff_location.py:24; core_functions/staff_location_functions.py:184-186 | P2-5: IST date range | P2 | S | No |
| 119 | `location.ts` `getLocationStats`:45 | `GET /staff-location/stats` | EXISTS-MATCHES | router/staff_location.py:32; core_functions/staff_location_functions.py:262; 15-minute window | P2-5: IST date; align the freshness window with the app | P2 | S | No |
| 120 | `location.ts` `listPunchPins`:53 | `GET /home/staff-punch-locations` | EXISTS-MATCHES | router/home.py:166; core_functions/home_functions.py:1306-1369 (every staff, no staff_id filter, DB-tz clock strings) | P1-10/P2-9: staff_id filter; P2-5 | P2 | S | No |

#### `src/api/managers.ts` (7)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 121 | `managers.ts` `listManagers`:24 | `GET /managers/` | EXISTS-MATCHES | router/manager.py:28 | None | - | - | No |
| 122 | `managers.ts` `getPromotableStaff`:33 | `GET /managers/staff` | EXISTS-MATCHES | router/manager.py:14 | None | - | - | No |
| 123 | `managers.ts` `assignManager`:43 | `POST /managers/assign` | EXISTS-MATCHES | router/manager.py:23; core_functions/manager_functions.py:12 (dept_id required) and :126-127 | DD-54: accept omitted dept as ALL; server overlap check | P2 | S | No |
| 124 | `managers.ts` `removeManager`:49 | `DELETE /managers/{id}` | EXISTS-MATCHES | router/manager.py:35 | None | - | - | No |
| 125 | `managers.ts` `getManagerPermissions`:55 | `GET /managers/{id}/permissions` | EXISTS-MATCHES | router/manager.py:50 | None | - | - | No |
| 126 | `managers.ts` `saveManagerPermissions`:61 | `POST /managers/{id}/permissions` | EXISTS-MATCHES | router/manager.py:55 | None | - | - | No |
| 127 | `managers.ts` `getStaffUnderManager`:67 | `GET /managers/{id}/staff` | EXISTS-MATCHES | router/manager.py:40 | None | - | - | No |

#### `src/api/payroll.ts` (48)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 128 | `payroll.ts` `listPayslipTemplates`:261 | `GET /api/salary/templates/{id}` | EXISTS-MATCHES | router/salary.py:110; core_functions/salary_structure.py:132-133 (bare [] when empty) | P2-6: always {success,data} | P2 | S | No |
| 129 | `payroll.ts` `getPayslipTemplate`:268 | `GET /api/salary/template/{id}` | EXISTS-MATCHES | router/salary.py:115 | None | - | - | No |
| 130 | `payroll.ts` `savePayslipTemplate`:278 | `PUT /api/salary/template/{id}` | EXISTS-MATCHES | router/salary.py:120 | None | - | - | No |
| 131 | `payroll.ts` `savePayslipTemplate`:279 | `POST /api/salary/template` | EXISTS-MATCHES | router/salary.py:105 | None | - | - | No |
| 132 | `payroll.ts` `deletePayslipTemplate`:290 | `DELETE /api/salary/template/{id}` | EXISTS-MATCHES | router/salary.py:125; core_functions/salary_structure.py:325-340 (deletes only the pay_slip row) | P1-9: delete earnings/deductions, refuse or cascade assignments (payroll) | P1 | S | No |
| 133 | `payroll.ts` `listSalaryBranches`:296 | `GET /api/salary/branches/{id}` | EXISTS-MATCHES | router/salary.py:130 | None | - | - | No |
| 134 | `payroll.ts` `listSalaryDepartments`:302 | `GET /api/salary/departments/{id}` | EXISTS-MATCHES | router/salary.py:135 | None | - | - | No |
| 135 | `payroll.ts` `listPayslipStaff`:313 | `GET /api/salary/staff/{id}` | EXISTS-MATCHES | router/salary.py:140 | None | - | - | No |
| 136 | `payroll.ts` `assignPayslip`:323 | `POST /api/salary/assign` | EXISTS-MATCHES | router/salary.py:145 | None | - | - | No |
| 137 | `payroll.ts` `getAssignedPayslipId`:329 | `GET /api/salary/employee/{id}/{id}` | EXISTS-MATCHES | router/salary.py:150 | None | - | - | No |
| 138 | `payroll.ts` `getPayslipPreview`:337 | `GET /api/salary/preview/{id}/{id}/{id}` | EXISTS-MATCHES | router/salary.py:155; core_functions/salary_structure.py:545-553 (month = UTC now); :511 unscoped branch join | P1-16: accept month/year; NEW-1 | P1 | S | No |
| 139 | `payroll.ts` `listWageTemplates`:352 | `GET /api/daily-wages/templates/{id}` | MISSING | Not mounted; router/daily_hourly.py:8 is a holiday copy; logic in core_functions/daily_hourly_functions.py:8 | P0-1/2: new wages router over get_templates_by_org(..., "D") | P0 | M | No |
| 140 | `payroll.ts` `listWageTemplates`:352 | `GET /api/hourly-charges/templates/{id}` | MISSING | Not mounted; router/daily_hourly.py:8 is a holiday copy; logic in core_functions/daily_hourly_functions.py:8 | P0-1/2: new wages router over get_templates_by_org(..., "H") | P0 | M | No |
| 141 | `payroll.ts` `saveWageTemplate`:362 | `PUT /api/daily-wages/template/{id}` | MISSING | Not mounted; update_template :69 (no org/type check) | P0-1/2 | P0 | S | No |
| 142 | `payroll.ts` `saveWageTemplate`:362 | `PUT /api/hourly-charges/template/{id}` | MISSING | Not mounted; update_template :69 (no org/type check) | P0-1/2 | P0 | S | No |
| 143 | `payroll.ts` `saveWageTemplate`:363 | `POST /api/daily-wages/template` | MISSING | Not mounted; create_template core_functions/daily_hourly_functions.py:35 | P0-1/2 | P0 | S | No |
| 144 | `payroll.ts` `saveWageTemplate`:363 | `POST /api/hourly-charges/template` | MISSING | Not mounted; create_template core_functions/daily_hourly_functions.py:35 | P0-1/2 | P0 | S | No |
| 145 | `payroll.ts` `deleteWageTemplate`:369 | `DELETE /api/daily-wages/template/{id}` | MISSING | Not mounted; delete_template :86 leaves assignments and copy rows | P0-1/2 + P1-9: cascade in one transaction | P0 | S | No |
| 146 | `payroll.ts` `deleteWageTemplate`:369 | `DELETE /api/hourly-charges/template/{id}` | MISSING | Not mounted; delete_template :86 leaves assignments and copy rows | P0-1/2 + P1-9: cascade in one transaction | P0 | S | No |
| 147 | `payroll.ts` `listWageStaff`:375 | `GET /api/wages/assigned/{id}/{id}` | MISSING | Not mounted; get_assigned_staff core_functions/daily_hourly_functions.py:117 | P0-3 | P0 | S | No |
| 148 | `payroll.ts` `listWageStaff`:375 | `GET /api/wages/unassigned/{id}/{id}` | MISSING | Not mounted; get_unassigned_staff :142 | P0-3 | P0 | S | No |
| 149 | `payroll.ts` `assignWages`:382 | `POST /api/wages/assign` | MISSING | Not mounted; assign_staff_to_wage :167 (moves staff off the same-type template; writes dead per-user copy rows) | P0-3: expose; return moved_from; stop writing copy rows | P0 | S | No |
| 150 | `payroll.ts` `unassignWages`:388 | `DELETE /api/wages/unassign/{id}/{id}/{id}` | MISSING | Not mounted; unassign_staff_from_wage :207 | P0-3 | P0 | S | No |
| 151 | `payroll.ts` `fetchIncentiveRows (listCount/Turnover/Overtime/BonusIncentives)`:399 | `GET /api/incentive/count/{id}` | EXISTS-MATCHES | router/incentive.py:74 | None | - | - | No |
| 152 | `payroll.ts` `fetchIncentiveRows (listCount/Turnover/Overtime/BonusIncentives)`:399 | `GET /api/incentive/turnover/{id}` | EXISTS-MATCHES | router/incentive.py:128 | None | - | - | No |
| 153 | `payroll.ts` `fetchIncentiveRows (listCount/Turnover/Overtime/BonusIncentives)`:399 | `GET /api/incentive/overtime/{id}` | EXISTS-MATCHES | router/incentive.py:182 | None | - | - | No |
| 154 | `payroll.ts` `fetchIncentiveRows (listCount/Turnover/Overtime/BonusIncentives)`:399 | `GET /api/incentive/bonus/{id}` | EXISTS-MATCHES | router/incentive.py:245 | None | - | - | No |
| 155 | `payroll.ts` `fetchIncentiveDetails (get*Incentive)`:405 | `GET /api/incentive/count/details/{id}` | EXISTS-MATCHES | router/incentive.py:83 | None | - | - | No |
| 156 | `payroll.ts` `fetchIncentiveDetails (get*Incentive)`:405 | `GET /api/incentive/turnover/details/{id}` | EXISTS-MATCHES | router/incentive.py:137 | None | - | - | No |
| 157 | `payroll.ts` `fetchIncentiveDetails (get*Incentive)`:405 | `GET /api/incentive/overtime/details/{id}` | EXISTS-MATCHES | router/incentive.py:191 | None | - | - | No |
| 158 | `payroll.ts` `fetchIncentiveDetails (get*Incentive)`:405 | `GET /api/incentive/bonus/details/{id}` | EXISTS-MATCHES | router/incentive.py:236 | None | - | - | No |
| 159 | `payroll.ts` `createCountIncentive`:461 | `POST /api/incentive/count` | EXISTS-MATCHES | router/incentive.py:92 | None | - | - | No |
| 160 | `payroll.ts` `updateCountIncentive`:473 | `PUT /api/incentive/count/{id}` | EXISTS-MATCHES | router/incentive.py:101 | None | - | - | No |
| 161 | `payroll.ts` `createTurnoverIncentive`:483 | `POST /api/incentive/turnover` | EXISTS-MATCHES | router/incentive.py:146 | None | - | - | No |
| 162 | `payroll.ts` `updateTurnoverIncentive`:494 | `PUT /api/incentive/turnover/{id}` | EXISTS-MATCHES | router/incentive.py:155 | None | - | - | No |
| 163 | `payroll.ts` `createOvertimeIncentive`:505 | `POST /api/incentive/overtime` | EXISTS-MATCHES | router/incentive.py:200 | None | - | - | No |
| 164 | `payroll.ts` `updateOvertimeIncentive`:516 | `PUT /api/incentive/overtime/{id}` | EXISTS-MATCHES | router/incentive.py:209 | None | - | - | No |
| 165 | `payroll.ts` `createBonusIncentive`:525 | `POST /api/incentive/bonus` | EXISTS-MATCHES | router/incentive.py:254 | None | - | - | No |
| 166 | `payroll.ts` `updateBonusIncentive`:545 | `PUT /api/incentive/bonus/{id}` | EXISTS-MATCHES | router/incentive.py:263 | None | - | - | No |
| 167 | `payroll.ts` `deleteIncentive`:551 | `DELETE /api/incentive/count/{id}` | EXISTS-MATCHES | router/incentive.py:114 | None | - | - | No |
| 168 | `payroll.ts` `deleteIncentive`:551 | `DELETE /api/incentive/turnover/{id}` | EXISTS-MATCHES | router/incentive.py:168 | None | - | - | No |
| 169 | `payroll.ts` `deleteIncentive`:551 | `DELETE /api/incentive/overtime/{id}` | EXISTS-MATCHES | router/incentive.py:222 | None | - | - | No |
| 170 | `payroll.ts` `deleteIncentive`:551 | `DELETE /api/incentive/bonus/{id}` | EXISTS-MATCHES | router/incentive.py:276 | None | - | - | No |
| 171 | `payroll.ts` `listIncentiveStaff`:557 | `GET /api/incentive/assigned/{id}/{id}` | EXISTS-MATCHES | router/incentive.py:316 | None | - | - | No |
| 172 | `payroll.ts` `listIncentiveStaff`:557 | `GET /api/incentive/unassigned/{id}/{id}` | EXISTS-MATCHES | router/incentive.py:323 | None | - | - | No |
| 173 | `payroll.ts` `listUnassignedIncentiveStaff`:564 | `GET /api/incentive/unassigned-all/{id}` | EXISTS-MATCHES | router/incentive.py:290 | None | - | - | No |
| 174 | `payroll.ts` `assignIncentive`:583 | `POST /api/incentive/assign` | EXISTS-MATCHES | router/incentive.py:330; core_functions/incentive_functions.py:359-443 (no one-template-per-kind rule; the mock moves staff off the other template); core_functions/report_functions.py:612-678 uses the first match | DD-76: define the rule, return the previous template; fix the mock to match | P2 | S | No |
| 175 | `payroll.ts` `unassignIncentive`:589 | `DELETE /api/incentive/unassign/{id}/{id}/{id}` | EXISTS-MATCHES | router/incentive.py:338 | None | - | - | No |

#### `src/api/plans.ts` (3)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 176 | `plans.ts` `getOrgPlanStatus`:59 | `GET /plans/plan-status/{id}` | EXISTS-MATCHES | router/gen_plans.py:44; core_functions/gen_plans_functions.py:121-151 (date.today()) | P2-5/DD-14: IST date, inclusive end | P2 | S | No |
| 177 | `plans.ts` `getSubscription`:66 | `GET /plans/subscription/{id}` | EXISTS-DIFFERENT-SHAPE | router/gen_plans.py:40; core_functions/gen_plans_functions.py:27-86 (no-subscription placeholder: staff_limit null, plan_name null/Free Trial; trial flags only inside data) | P1-13/P2-11: explicit `state` (trial, paid_active, paid_expired or none) and the trial staff limit | P1 | S | No |
| 178 | `plans.ts` `listPlans`:78 | `GET /plans/get_plans` | EXISTS-MATCHES | router/gen_plans.py:36 | None | - | - | No |

#### `src/api/reports.ts` (5)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 179 | `reports.ts` `getOvertimeReport`:89 | `GET /api/incentive/overtime-report/{id}` | MISSING | No route in router/incentive.py; OT maths in core_functions/report_functions.py:649-678 | P0-5: add the route over a shared overtime helper | P0 | M | No |
| 234 | `reports.ts` `resolveAttendanceReport`:199 | `GET /api/reports/monthly-attendance-all/{id}/{id}` | EXISTS-MATCHES | router/report.py:64; router/report.py:64-113 (synchronous merge of N PDFs, unauthenticated); report_functions.py:31-38 (session-tz clock) | P2-9/P3-13: async/limit, auth, IST times in the PDF | P2 | M | No |
| 235 | `reports.ts` `resolveAttendanceReport`:209 | `GET /api/reports/monthly-attendance/{id}/{id}` | EXISTS-MATCHES | router/report.py:54; router/report.py:54-61 | P3-13: auth/scope | P3 | S | No |
| 236 | `reports.ts` `resolveAttendanceReport`:201 | `GET /api/reports/manager/{id}/monthly-attendance-all/{id}` | EXISTS-MATCHES | router/report.py:144; router/report.py:144-192 (org-wide set incl. other branches) | P3-13: scope to manager assignments | P3 | S | No |
| 237 | `reports.ts` `resolveAttendanceReport`:208 | `GET /api/reports/manager/{id}/monthly-attendance/{id}/{id}` | EXISTS-MATCHES | router/report.py:116; router/report.py:116-141 (branch-only check, self not allowed) | P3-13: scope; allow self | P3 | S | No |

#### `src/api/requests.ts` (9)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 180 | `requests.ts` `submitLeave`:34 | `POST /requests/leave/submit` | EXISTS-MATCHES | router/requests.py:78; core_functions/requests_functions.py:204-238 (no overlap/past/holiday check; request status NULL :214) | P2-7/DD-90: validation | P2 | S | No |
| 181 | `requests.ts` `submitPermission`:48 | `POST /requests/permission/submit` | EXISTS-MATCHES | router/requests.py:92; core_functions/requests_functions.py:241-273 | P2-7: start<end, <=6h, date checks | P2 | S | No |
| 182 | `requests.ts` `submitShiftChange`:61 | `POST /requests/shift-change/submit` | EXISTS-MATCHES | router/requests.py:66; core_functions/requests_functions.py:170-201 | P2-7: same-shift/duplicate checks; NEW-8: approval never applies the shift | P2 | S | No |
| 183 | `requests.ts` `submitDeviceChange`:73 | `POST /requests/device-change/submit` | EXISTS-MATCHES | router/requests.py:57; core_functions/requests_functions.py:129-167 | P3-4: validate the pair, dedupe, rate-limit | P3 | S | No |
| 184 | `requests.ts` `listEmployeeRequests`:86 | `GET /requests/employee/{id}/{id}` | EXISTS-DIFFERENT-SHAPE | router/requests.py:108; core_functions/requests_functions.py:276-326 (no date_statuses, no shift ids; leave status NULL) | P1-1: add date_statuses, requested_shift_id, current_shift_id, derived status | P1 | S | No |
| 185 | `requests.ts` `listAdminRequests`:95 | `GET /requests/admin/{id}` | EXISTS-DIFFERENT-SHAPE | router/requests.py:117; core_functions/requests_functions.py:329-425 (no emp_id/profile_photo; status filter misses leave :373-375; whole inbox) | P2-8: counts endpoint, working filter, pagination; add emp_id/profile_photo | P2 | M | No |
| 186 | `requests.ts` `updateRequestStatus`:105 | `POST /requests/update-status` | EXISTS-MATCHES | router/requests.py:126; core_functions/requests_functions.py:428-512 (leave never updates requests.status; role_id/action_by unchecked; shift approval does not change the shift) | P1-15/NEW-8: validate caller, aggregate status, apply approved shift change, 404 for a date-less request | P1 | M | No |
| 187 | `requests.ts` `getRequestCalendar`:125 | `GET /requests/calendar/{id}/{id}` | EXISTS-MATCHES | router/requests.py:141; core_functions/requests_functions.py:574-598 (leave/permission dates only) | DD-97: add holiday and weekly-off entries | P2 | S | No |
| 188 | `requests.ts` `checkSession`:151 | `GET /requests/check-session/{id}` | EXISTS-MATCHES | router/requests.py:150; core_functions/requests_functions.py:537-571 | NEW-7: also send FCM force_logout to the old device on takeover | P2 | S | No |

#### `src/api/salaryReports.ts` (8)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 189 | `salaryReports.ts` `getStaffSalaryBreakdown`:102 | `GET /api/salary/staff-breakdown` | EXISTS-DIFFERENT-SHAPE | router/salary.py:30; router/salary.py:30-102 (salary_type constant :69, fine 0 :76, per-staff get_staff_salary :55-57, swallow-all fallback :87-101); core_functions/report_functions.py:546-552 (basic never prorated), :566-575 (present_days = COUNT(*) of rows) | NEW-12: batched engine, real salary_type enum, fine from the attendance rule, OT fix (NEW-13) | P1 | L | No |
| 190 | `salaryReports.ts` `listMonthlySalary`:109 | `GET /salary/monthly-list` | MISSING | No route in router/salary.py; no paid-state table in the schema dump | P0-6: salary_payments table + route over the shared salary engine | P0 | M | Yes (new table) |
| 191 | `salaryReports.ts` `markSalaryPaid`:117 | `POST /salary/mark-paid` | MISSING | No route; see monthly-list | P0-6: idempotent insert | P0 | S | Yes (new table) |
| 192 | `salaryReports.ts` `listUserAdvances`:128 | `GET /api/advance/user/{id}/{id}` | EXISTS-MATCHES | router/advance.py:41 | None | - | - | No |
| 193 | `salaryReports.ts` `listOrgAdvances`:135 | `GET /api/advance/org/{id}` | EXISTS-MATCHES | router/advance.py:35 | None | - | - | No |
| 194 | `salaryReports.ts` `createAdvance`:143 | `POST /api/advance` | EXISTS-DIFFERENT-SHAPE | router/advance.py:23; core_functions/advance_functions.py:63-69 (returns the raw row: no employee_name/created_by_name) | Return the enriched row like the list endpoints | P2 | S | No |
| 195 | `salaryReports.ts` `deleteAdvance`:156 | `DELETE /api/advance/{id}/{id}` | EXISTS-MATCHES | router/advance.py:47 | None | - | - | No |
| 196 | `salaryReports.ts` `getWorkingDays`:163 | `GET /api/holidays/working-days/{id}` | WRONG-MOUNT | router/daily_hourly.py:54; router/daily_hourly.py:54 is the live copy (also router/holiday.py:58, router/report.py:42); `org_off_days` is not in the schema dump (core_functions/holiday_functions.py:12-27) | P1-5: single holiday router; migration for org_off_days | P1 | S | Check |

#### `src/api/shifts.ts` (9)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 197 | `shifts.ts` `listShifts`:14 | `GET /shift/get_shifts/{id}` | EXISTS-MATCHES | router/shift.py:21 | None | - | - | No |
| 198 | `shifts.ts` `getShift`:25 | `GET /shift/get_shift/{id}` | EXISTS-MATCHES | router/shift.py:15 | None | - | - | No |
| 199 | `shifts.ts` `createShift`:38 | `POST /shift/create` | EXISTS-MATCHES | router/shift.py:9 | None | - | - | No |
| 200 | `shifts.ts` `updateShift`:44 | `PUT /shift/update/{id}` | EXISTS-MATCHES | router/shift.py:27 | None | - | - | No |
| 201 | `shifts.ts` `deleteShift`:50 | `DELETE /shift/delete/{id}` | EXISTS-MATCHES | router/shift.py:33 | None | - | - | No |
| 202 | `shifts.ts` `getAssignedStaff`:56 | `GET /shift/assigned_staff/{id}/{id}` | MISSING | No route in router/shift.py:9-35; assignment is users_additional_info.shift_id | P0-7 | P0 | S | No |
| 203 | `shifts.ts` `getUnassignedStaff`:62 | `GET /shift/unassigned_staff/{id}/{id}` | MISSING | No route in router/shift.py | P0-7 | P0 | S | No |
| 204 | `shifts.ts` `assignStaff`:69 | `POST /shift/assign_staff` | MISSING | No route | P0-7 | P0 | S | No |
| 205 | `shifts.ts` `unassignStaff`:78 | `DELETE /shift/unassign_staff/{id}/{id}/{id}` | MISSING | No route | P0-7 | P0 | S | No |

#### `src/api/staff.ts` (13)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 206 | `staff.ts` `addStaff`:43 | `POST /addstaff/` | EXISTS-MATCHES | router/add_staff.py:14; core_functions/add_staff_function.py:292-310 (limit only with an active subscription); :329-374 unknown branch/dept names create rows; :23 shift default 1 | P2-11: trial staff limit; reject unknown names; WhatsApp org name looked up with users.user_id = org_id (:429-431) | P2 | S | No |
| 207 | `staff.ts` `getStaff`:49 | `GET /addstaff/{id}` | EXISTS-DIFFERENT-SHAPE | router/add_staff.py:169; core_functions/add_staff_function.py:70-93,464-509 (no branch_id/dept_id/parent_id/doj; no status filter) | P1-2: add branch_id, dept_id, parent_id (+ doj, father_name, ...) | P1 | S | No |
| 208 | `staff.ts` `updateStaff`:55 | `PUT /addstaff/{id}` | EXISTS-MATCHES | router/add_staff.py:178; core_functions/add_staff_function.py:579-586 (role_id literal 3; branch_id/dept_id NULL when names omitted :529-530); :550-556 writes non-existent branch_latitude/branch_longitude | P1-4: keep role_id, keep branch/dept when omitted, fix the new-branch insert | P1 | S | No |
| 209 | `staff.ts` `deleteStaff`:61 | `DELETE /addstaff/{id}` | EXISTS-MATCHES | router/add_staff.py:190; core_functions/add_staff_function.py:641-645 (status = 0, identical to deactivate); duplicate check ignores status (:312-322); core_functions/home_functions.py:473-552 all-staff has no status filter (the mock hides deleted rows: status 2) | NEW-15: delete = status 2 with tokens cleared, exclude 2 from lists and from duplicate checks | P1 | S | No |
| 210 | `staff.ts` `permanentlyDeleteStaff`:71 | `DELETE /addstaff/{id}/permanent` | EXISTS-MATCHES | router/add_staff.py:200; core_functions/add_staff_function.py:664-700 (role from query) | P3-14: derive the role from the caller | P3 | S | No |
| 211 | `staff.ts` `setStaffActive`:80 | `PUT /addstaff/{id}/status` | EXISTS-MATCHES | router/add_staff.py:213; router/add_staff.py:213-243 (no role/org check; no staff-limit check on re-activation) | P3-14; enforce the staff limit on activation | P3 | S | No |
| 212 | `staff.ts` `getActiveStaffCount`:88 | `GET /addstaff/count/{id}` | EXISTS-MATCHES | router/add_staff.py:79 | None | - | - | No |
| 213 | `staff.ts` `listStaffForPicker`:95 | `GET /addstaff/all-staff/{id}` | EXISTS-MATCHES | router/add_staff.py:100 | None | - | - | No |
| 214 | `staff.ts` `getBranchOptions`:115 | `GET /addstaff/branches` | EXISTS-MATCHES | router/add_staff.py:36 | None | - | - | No |
| 215 | `staff.ts` `getDepartmentOptions`:128 | `GET /addstaff/departments` | EXISTS-MATCHES | router/add_staff.py:45 | None | - | - | No |
| 216 | `staff.ts` `getManagerOptions`:140 | `GET /addstaff/managers/` | EXISTS-MATCHES | router/add_staff.py:54 | None | - | - | No |
| 217 | `staff.ts` `getNextEmployeeId`:152 | `GET /addstaff/next-emp-id` | EXISTS-MATCHES | router/add_staff.py:19 | None | - | - | No |
| 218 | `staff.ts` `isEmployeeIdAvailable`:166 | `GET /addstaff/validate-emp-id` | EXISTS-MATCHES | router/add_staff.py:27 | None | - | - | No |

#### `src/api/support.ts` (4)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 219 | `support.ts` `listSupportSubjects`:64 | `GET /support/subjects` | EXISTS-MATCHES | router/support.py:24 | None | - | - | No |
| 220 | `support.ts` `listTickets`:70 | `GET /support/requests/{id}` | EXISTS-MATCHES | router/support.py:96 | None | - | - | No |
| 221 | `support.ts` `submitTicket`:83 | `POST /support/submit` | EXISTS-MATCHES | router/support.py:32; router/support.py:36-48 (photo failure swallowed); core_functions/support_functions.py:27-30 inserts user_id (column not in the schema dump) | P2-12: report photo failure; NEW-14: verify the column | P2 | S | Check |
| 222 | `support.ts` `replyToTicket`:100 | `POST /support/{id}/reply` | EXISTS-MATCHES | router/support.py:72 | None | - | - | No |

#### `src/api/system.ts` (10)

| # | App caller | Route | Backend status | Evidence (file:line) | Required change | Pri | Effort | DB migration |
|---|---|---|---|---|---|---|---|---|
| 223 | `system.ts` `checkAppVersion`:92 | `GET /version/check` | EXISTS-DIFFERENT-SHAPE | router/version.py:8; router/version.py:8-45 (one row per platform, shared by every app; table not in the schema dump) | P0-8: app_id (shared backend) or seed Clockyn values (own backend) | P0 | S | Yes (column or seed) |
| 224 | `system.ts` `submitFeatureSuggestion`:103 | `POST /company/suggest-feature` | EXISTS-MATCHES | router/company.py:69 | None | - | - | No |
| 225 | `system.ts` `listHolidays`:122 | `GET /api/holidays/{id}` | WRONG-MOUNT | router/daily_hourly.py:25; app.py:48 mounts router/daily_hourly.py (holiday copy) before router/holiday.py (app.py:65); `block_attendance` column missing in the schema dump | P1-5 | P1 | M | Yes (column) |
| 226 | `system.ts` `addHoliday`:133 | `POST /api/holidays/{id}` | WRONG-MOUNT | router/daily_hourly.py:30; router/daily_hourly.py:30-37 accepts block_attendance; router/holiday.py:34-40 model drops it | P1-5: one router, reject duplicate (date,name,scope) | P1 | M | Yes (column) |
| 227 | `system.ts` `updateHoliday`:160 | `PUT /api/holidays/{id}/{id}` | WRONG-MOUNT | router/daily_hourly.py:38; router/daily_hourly.py:38-45 vs router/holiday.py:42-48 | P1-5 | P1 | S | Yes (column) |
| 228 | `system.ts` `deleteHoliday`:170 | `DELETE /api/holidays/{id}/{id}` | WRONG-MOUNT | router/daily_hourly.py:46; router/daily_hourly.py:46-52 | P1-5 | P1 | S | No |
| 229 | `system.ts` `getWeeklyOffs`:176 | `GET /api/holidays/off-days/{id}` | EXISTS-MATCHES | router/holiday.py:66; router/holiday.py:66-69; core_functions/holiday_functions.py:12-27 reads org_off_days (not in the schema dump) | NEW-14: migration for org_off_days | P1 | S | Check |
| 230 | `system.ts` `setWeeklyOffs`:184 | `POST /api/holidays/off-days/{id}` | EXISTS-MATCHES | router/holiday.py:71; router/holiday.py:71-76; holiday_functions.py:30-43 | NEW-14: migration; validate 0..6 | P1 | S | Check |
| 231 | `system.ts` `getAttendanceRule`:190 | `GET /api/attendance-rule/{id}` | MISSING | No route and no table | P0-4 | P0 | M | Yes (new table) |
| 232 | `system.ts` `saveAttendanceRule`:199 | `POST /api/attendance-rule/{id}` | MISSING | No route and no table | P0-4 | P0 | M | Yes (new table) |

## 4. Designs for P0 items

Conventions for every design: new code goes in new modules where possible (the legacy app keeps its code paths); all new routes are additive; JSON examples use the mock-server fixture ids (`org-0001`, `stf-0001`, ...) so they can be diffed against `e2e/mock-server`; real ids are UUID strings (several routes parse the id as a UUID, for example `router/add_staff.py:200-210`). "Legacy compatibility" is stated per item. Tests: "Unit" = pure function or single SQL against a test database; "API" = FastAPI `TestClient` against a seeded Postgres (see `testing_strategy.md`).

Before building any P0 item: **check the deployed server for the route** (`backend_changes.md` section 0 warns that the deployed code may be newer than this snapshot). If it exists there with the same contract, port that code instead of writing a new one.

### P0-1 / P0-2 / P0-3 Daily wages, hourly charges, wage assignment (12 routes: rows 139-150 of section 3)

**Current state `[V]`.** `router/daily_hourly.py:8` declares `APIRouter(prefix="/api/holidays")` and holds a copy of the holiday routes; `app.py:48` mounts it. The functions that implement wages exist in `core_functions/daily_hourly_functions.py` (`get_templates_by_org :8`, `create_template :35`, `update_template :69`, `delete_template :86`, `get_template_by_id :99`, `get_assigned_staff :117`, `get_unassigned_staff :142`, `assign_staff_to_wage :167`, `unassign_staff_from_wage :207`) over `daily_hourly_wages` (template rows have `user_id IS NULL`, type `D`/`H`) and `assigned_wages`. The salary engine already reads them (`core_functions/report_functions.py:578-602`).

**Files.** New `router/wages.py` with three routers (`/api/daily-wages`, `/api/hourly-charges`, `/api/wages`); `app.py`: replace the `daily_hourly` import and `include_router` (lines 1, 48) with `wages`; delete `router/daily_hourly.py` only in the same release that makes `router/holiday.py` carry `block_attendance` (see P1-5, otherwise the live holiday handlers change). Changes in `core_functions/daily_hourly_functions.py`: add org/type scoping to `update_template` and `delete_template`, make delete cascade, make `assign_staff_to_wage` return the templates staff were moved off, stop writing the per-user copy rows (they get a new random `temp_id` at `:192`, so no query ever joins them: dead data, and the `DELETE ... WHERE temp_id = template AND user_id = x` at `:189` never matches, so repeated assigns accumulate rows).

**Contract (identical for `/api/daily-wages` and `/api/hourly-charges`; `D`/`H` is fixed by the router).**

```
GET  /api/daily-wages/templates/org-0001
200  {"success": true, "data": [
       {"id": "6f1c2d1e-...", "temp_name": "1", "template_name": "1", "amount": 650.0,
        "assigned_count": 3, "created_at": "2026-09-01T10:00:00"}]}
POST /api/daily-wages/template   {"org_id": "org-0001", "amount": 700}
200  {"success": true, "message": "Template saved successfully", "id": "9a3b...", "temp_name": "2", "template_name": "2"}
PUT  /api/daily-wages/template/9a3b...   {"org_id": "org-0001", "amount": 720}
200  {"success": true, "message": "Template updated successfully"}
DELETE /api/daily-wages/template/9a3b...?org_id=org-0001
200  {"success": true, "message": "Template deleted successfully", "unassigned": 3}
GET  /api/wages/assigned/org-0001/9a3b...      -> {"success": true, "data": [{"user_id": "stf-0001", "name": "Aarav Sharma", "emp_id": "E1001", "branch_name": "Head Office", "dept_name": "Sales"}]}
GET  /api/wages/unassigned/org-0001/9a3b...    -> same row shape (active role 2/3 staff not on THIS template)
POST /api/wages/assign   {"org_id": "org-0001", "user_ids": ["stf-0001"], "temp_id": "9a3b..."}
200  {"success": true, "message": "Staff assigned successfully", "assigned": 1,
      "moved_from": [{"user_id": "stf-0001", "temp_id": "6f1c...", "temp_name": "1"}]}
DELETE /api/wages/unassign/org-0001/stf-0001/9a3b...
200  {"success": true, "message": "Staff unassigned successfully"}
```
The app reads `id|temp_id`, `template_name|temp_name`, `amount|temp_amount`, `assigned_count` (`src/api/payroll.ts:157-166`), so both name aliases are emitted. The app sends `DELETE` without a body, so `org_id` is a query parameter and optional (the template row already carries its org; verify it matches when given). `moved_from` and `unassigned` are additive (DD-76).

**SQL / migration.** No new table. Recommended: `CREATE INDEX IF NOT EXISTS idx_assigned_wages_user_temp ON assigned_wages (user_id, temp_id);`. Optional after data clean-up (DD-74): `CREATE UNIQUE INDEX ux_wage_template_amount ON daily_hourly_wages (org_id, temp_type, temp_amount) WHERE user_id IS NULL;`. Dead-copy check before deleting copy rows: `SELECT count(*) FROM daily_hourly_wages c WHERE c.user_id IS NOT NULL AND EXISTS (SELECT 1 FROM assigned_wages a WHERE a.temp_id = c.temp_id AND a.user_id = c.user_id);` must be 0.

Cascade delete, one transaction, org and type scoped:
```sql
DELETE FROM assigned_wages WHERE org_id = %(org)s AND temp_id = %(id)s;
DELETE FROM daily_hourly_wages c USING daily_hourly_wages t
 WHERE t.temp_id = %(id)s AND t.org_id = %(org)s AND t.temp_type = %(type)s AND t.user_id IS NULL
   AND c.org_id = t.org_id AND c.temp_type = t.temp_type AND c.temp_name = t.temp_name AND c.user_id IS NOT NULL;
DELETE FROM daily_hourly_wages WHERE temp_id = %(id)s AND org_id = %(org)s AND temp_type = %(type)s AND user_id IS NULL;
```

**Validation and errors.** `amount`: number, `> 0`, `<= 9999999.99` (column `numeric(10,2)`), at most 2 decimals, else 400 `{"detail": "Amount must be greater than 0"}`; `org_id` required; template must exist, belong to `org_id` and to the router's type, else 404 `{"detail": "Template not found"}`; duplicate amount in the org and type: 409 `{"detail": "A template with this amount already exists"}` (the app already blocks it client-side); `user_ids` non-empty list (400 `"Select at least one employee"`), every id an active role 2/3 user of `org_id` (404 naming the id); assign is idempotent (re-assigning the same staff to the same template changes nothing). Business errors may alternatively use HTTP 200 `{"success": false, "message": ...}` like the mock, the app handles both (`assertSuccess`); choose one and keep it, the recommendation is real status codes.

**Legacy compatibility.** The old app uses the same paths and field names (v1 contract), so the aliases keep it working. Removing `router/daily_hourly.py` is safe for wages (nothing mounted them) and must not change holiday behaviour (see P1-5 ordering).

**Tests.** Unit: `create_template` auto-numbering (`"1"`, `"2"`, skips non-numeric names); assign moves staff off the other template of the same type and reports `moved_from`; delete removes assignments and copies and leaves other orgs untouched; `unassign` of a non-assigned user returns 404. API: contract JSON for each route above against the mock fixtures (`dw-1`, `dw-2`, `hc-1`); cross-org id returns 404; amount `0`, `-1`, `"abc"`, `1e9` rejected; salary breakdown reflects an assignment immediately (daily wage x present days).

### P0-4 Attendance rule (rows 231-232)

**Current state `[V]`.** No route, no table; nothing in attendance, dashboard or salary reads a rule. The salary engine pays monthly-fixed staff in full whatever the attendance (`core_functions/report_functions.py:546-552`, basic taken as is), and `fine` is a constant 0 (`router/salary.py:76`). The rule is therefore a stored setting plus a new computation.

**Files.** New `router/attendance_rule.py` (prefix `/api/attendance-rule`), new `core_functions/attendance_rule_functions.py` (`get_rule`, `save_rule`, `late_days_for_user_month`, `penalty_for_user_month`), hook in the shared salary engine (NEW-12) and in `get_monthly_attendance` (P1-3). `app.py`: include the router.

**Contract (matches `e2e/mock-server/routes/org.js:376-380`).**
```
GET  /api/attendance-rule/org-0001
200  {"success": true, "late_punch_in_count": 3, "consider_as": "Half Day", "configured": false}
POST /api/attendance-rule/org-0001   {"late_punch_in_count": 2, "consider_as": "Leave"}
200  {"success": true, "message": "Attendance rule saved", "late_punch_in_count": 2, "consider_as": "Leave"}
```
When no row exists the GET returns the v1 defaults (3, `Half Day`) with `configured:false`; the app reads defaults on `success:false` as well (`src/api/system.ts:190-196`). `configured` and the echoed fields are additive.

**SQL.**
```sql
CREATE TABLE IF NOT EXISTS attendance_rule (
  org_id               varchar(36) PRIMARY KEY,
  late_punch_in_count  int         NOT NULL CHECK (late_punch_in_count BETWEEN 1 AND 10),
  consider_as          varchar(10) NOT NULL CHECK (consider_as IN ('Half Day','Leave')),
  grace_minutes        int         NOT NULL DEFAULT 0 CHECK (grace_minutes BETWEEN 0 AND 120),
  updated_by           varchar(36),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now());
-- upsert
INSERT INTO attendance_rule (org_id, late_punch_in_count, consider_as, updated_by)
VALUES (%s,%s,%s,%s)
ON CONFLICT (org_id) DO UPDATE SET late_punch_in_count = EXCLUDED.late_punch_in_count,
  consider_as = EXCLUDED.consider_as, updated_by = EXCLUDED.updated_by, updated_at = now();
```
`grace_minutes` is not exposed by the app (DD-60 option b); keep it 0 until product decides.

**Enforcement (needs decision DD-60/61, recommended defaults).** For a month and a staff member with a shift: `late_days` = days whose first punch-in, converted to IST, is later than `shift_for(user, date).start_time + grace`. `penalty_units = floor(late_days / late_punch_in_count)`; `penalty_days = penalty_units x (0.5 if 'Half Day' else 1.0)`. Effects, only for organisations whose rule row exists (`configured = true`), so nobody's pay changes silently: (a) monthly-attendance v2 marks the 1st, 2nd, ... N-th late day as `half_day` or `leave` (P1-3); (b) the salary engine sets `fine = round(penalty_days x basic_pay / working_days, 2)` for payslip-assigned staff, where `working_days` is the figure `GET /api/holidays/working-days` returns for the month; the sum feeds `net_salary` and `/api/salary/staff-breakdown.fine` (`router/salary.py:76` stops being a constant). Staff without a shift are never penalised (same rule as the dashboard, `home_functions.py:181-187`).

**Validation and errors.** `late_punch_in_count` integer 1..10 and `consider_as` in the two literals, else 422; unknown `org_id` 404. Idempotent upsert.

**Legacy compatibility.** New routes; no effect on any org that never saves a rule.

**Tests.** Unit: `late_days_for_user_month` with punches at 08:59, 09:00, 09:01 IST (stored as UTC) against a 09:00 shift; a night shift; staff without shift; `penalty_days` for counts 1, 3, 10. API: GET default; POST then GET; 422 for `0`, `11`, `"Absent"`; salary breakdown `fine` is 0 for an unconfigured org and non-zero for a configured one with late days; a rule saved for org A does not affect org B.

### P0-5 Overtime report (row 179)

**Current state `[V]`.** `router/incentive.py` has CRUD and assignment only. The overtime maths exist inline in `core_functions/report_functions.py:649-678`: `ot_minutes = max(0, total_hours x 60 - shift_minutes x present_days)`, `slots = floor(ot_minutes / duration_minutes)`, `ot_amount = slots x amount` for the first overtime template assigned to the user. Two defects feed the report, both fixed here (see NEW-12/NEW-13): `present_days` is `COUNT(*)` of attendance rows (`:566-575`, so a two-session day counts twice and the shift is subtracted twice) and `shift_minutes` is `end_time - start_time` without wrap (`:665-675`, negative for a night shift).

**Files.** `core_functions/overtime.py` (new): `compute_overtime(user_ids, org_id, year, month)` set-based; `router/incentive.py`: add `GET /api/incentive/overtime-report/{org_id}`; `report_functions.get_staff_salary` calls the same helper so the report and the salary breakdown always agree.

**Contract (matches `e2e/mock-server/routes/reports.js:65-70`).**
```
GET /api/incentive/overtime-report/org-0001?month=10&year=2026
200 {"success": true, "data": [
     {"user_id": "stf-0001", "ot_days": 3, "ot_minutes": 195, "ot_amount": 450.0}]}
```
One row per active role 2/3 staff member of the organisation who has an overtime template assigned (the app shows "—" for staff not listed). `month` and `year` default to the current IST month; `month` outside 1..12 is 422.

**Definition (reconciles with salary).** Per user and month: `present_days` = distinct attendance dates; `worked_minutes` = sum of `work_hours x 60` of closed sessions; `shift_minutes` = shift duration with wrap (`CASE WHEN end_time >= start_time THEN end_time - start_time ELSE end_time - start_time + interval '24 hours' END`), 0 when no shift; `ot_minutes = max(0, worked_minutes - shift_minutes x present_days)`; `ot_amount = floor(ot_minutes / duration_minutes) x amount` (identical to today's salary formula so payroll numbers do not move); `ot_days` = number of dates whose own worked minutes exceed `shift_minutes` (informational). Decision DD-60-adjacent: per-day overtime is arguably fairer (a short day should not cancel a long day) but changes pay; keep the monthly aggregate until product decides.

```sql
WITH ot_staff AS (
  SELECT ai.user_id, d.duration_minutes, d.amount
  FROM assigned_incentives ai
  JOIN incentive_templates t ON t.incentive_id = ai.incentive_id AND t.type = 'OVERTIME'
  JOIN incentive_overtime_details d ON d.incentive_id = t.incentive_id AND (d.user_id = ai.user_id OR d.user_id IS NULL)
  JOIN users u ON u.user_id = ai.user_id AND u.parent_id = %(org)s AND u.status = 1 AND u.role_id IN (2,3)
  WHERE ai.org_id = %(org)s),
day_tot AS (
  SELECT a.user_id, a.attendance_date, SUM(a.work_hours) * 60 AS worked_min
  FROM attendance a JOIN ot_staff s USING (user_id)
  WHERE a.attendance_date BETWEEN %(from)s AND %(to)s AND a.punch_out IS NOT NULL
  GROUP BY 1,2)
SELECT ... -- join users_additional_info -> shift (wrapped duration) and aggregate per user
```

**Legacy compatibility.** New route; the salary formula is unchanged except for the two defect fixes, which only matter for two-session days and night shifts (announce in release notes).

**Tests.** Unit: single-session day under/over shift; two sessions the same day (present_days 1, shift subtracted once); night shift 21:00-06:00 (540 min); `duration_minutes` 30 with 95 OT minutes gives 3 slots; user without overtime template excluded; user with template but no attendance gets `0/0/0.0`. API: contract JSON; report `ot_amount` equals the `overtime` field of `/api/salary/staff-breakdown` for every row; invalid month 422.

### P0-6 Salary paid state (rows 190-191)

**Current state `[V]`.** No route; no table records a paid salary (`payroll` only links staff to a payslip template, schema dump lines for `payroll`).

**Files.** New `router/salary_payments.py` (prefix `/salary`), `core_functions/salary_payment_functions.py`; both routes use the shared salary engine (NEW-12) so `net_salary` equals the breakdown's.

**Contract (matches `e2e/mock-server/routes/reports.js:79-97`).**
```
GET /salary/monthly-list?org_id=org-0001&month=10&year=2026
200 {"success": true, "data": [
     {"user_id": "stf-0001", "name": "Aarav Sharma", "emp_id": "E1001", "dept_name": "Sales",
      "branch_name": "Head Office", "salary_type": "MONTHLY_FIXED", "basic_salary": 30000.0,
      "net_salary": 31250.0, "paid": true, "paid_date": "2026-10-31"}]}
POST /salary/mark-paid   {"org_id": "org-0001", "user_ids": ["stf-0001","stf-0002"], "month": 10, "year": 2026}
200 {"success": true, "message": "Salary marked as paid for 2 employee(s)", "marked": 2, "already_paid": 0}
```
`paid_date` is the IST calendar date of `paid_at` (`null` when unpaid). `salary_type` uses the enum `MONTHLY_FIXED | DAILY_WAGES | HOURLY | WEEKLY` the app expects (`src/features/reports/utils/salarySheet.ts:7-12`), not the mock's `Monthly/Daily/Hourly` strings (mock defect, Appendix C). `net_salary` of a paid row is the **snapshot stored at payment** so a later template edit cannot rewrite a paid salary; unpaid rows are live.

**SQL.**
```sql
CREATE TABLE IF NOT EXISTS salary_payments (
  id         int GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id     varchar(36)  NOT NULL,
  user_id    varchar(36)  NOT NULL,
  pay_month  smallint     NOT NULL CHECK (pay_month BETWEEN 1 AND 12),
  pay_year   smallint     NOT NULL CHECK (pay_year BETWEEN 2000 AND 2100),
  net_amount numeric(12,2) NOT NULL,
  paid_at    timestamptz  NOT NULL DEFAULT now(),
  paid_by    varchar(36),
  UNIQUE (org_id, user_id, pay_month, pay_year));
CREATE INDEX IF NOT EXISTS idx_salary_payments_org_period ON salary_payments (org_id, pay_year, pay_month);
-- mark-paid, one statement for all ids, net computed by the engine in the same request
INSERT INTO salary_payments (org_id, user_id, pay_month, pay_year, net_amount, paid_by)
SELECT %s, x.user_id, %s, %s, x.net, %s FROM unnest(%s::text[], %s::numeric[]) AS x(user_id, net)
ON CONFLICT (org_id, user_id, pay_month, pay_year) DO NOTHING;
```
`marked` = rows inserted, `already_paid` = ids minus inserted.

**Validation and errors.** `month` 1..12 and `year` 2000..2100 (422); `user_ids` non-empty (400 `"Select at least one employee"`), at most 500; every id must be an active role 2/3 user of `org_id` (404 naming the first offender, whole request rolled back); all-or-nothing in one transaction. Paying a month that has not ended is allowed (v1 allowed it); reject future months only if product asks. There is no un-pay route; add `POST /salary/mark-unpaid` only on request.

**Legacy compatibility.** New routes, no effect on existing ones.

**Tests.** Unit: engine net for a fixture staff member; snapshot survives a payslip edit. API: list shows `paid:false` then `true` after mark-paid; second mark-paid returns `already_paid`; unknown/foreign user id 404 and nothing written; month 13 422; a different org's list is unaffected; monthly-list `net_salary` equals breakdown `net_salary` for unpaid rows.

### P0-7 Shift assignment (rows 202-205)

**Current state `[V]`.** `router/shift.py:9-35` has create/get/update/delete only; assignment is `users_additional_info.shift_id`. `delete_shift` already nulls the column (`core_functions/shift_functions.py:213`).

**Files.** `core_functions/shift_functions.py`: `get_assigned_staff(org_id, shift_id)`, `get_unassigned_staff`, `assign_staff(org_id, shift_id, user_ids)`, `unassign_staff(org_id, user_id, shift_id)`; `router/shift.py`: four routes.

**Contract (matches `e2e/mock-server/routes/org.js:305-320`).** Rows use the staff-list shape the app maps (`mapStaffMember`):
```
GET /shift/assigned_staff/org-0001/1   200 {"success": true, "data": [
     {"user_id": "stf-0001", "name": "Aarav Sharma", "emp_id": "E1001", "mobile_number": "9000000003",
      "email": "aarav.sharma@clockyn.test", "dept_name": "Sales", "branch_name": "Head Office",
      "role_id": 3, "is_active": true, "profile_photo_url": null}]}
GET /shift/unassigned_staff/org-0001/1 200 same shape (active role 2/3 staff whose shift_id is NULL, 0 or another shift)
POST /shift/assign_staff   {"org_id": "org-0001", "user_ids": ["stf-0003"], "shift_id": 1}
200 {"success": true, "message": "Staff assigned successfully", "assigned": 1}
DELETE /shift/unassign_staff/org-0001/stf-0003/1
200 {"success": true, "message": "Staff unassigned successfully"}
```

**SQL.**
```sql
-- assigned
SELECT u.user_id, u.name, u.emp_id, u.mobile_number, u.email, u.role_id, (u.status = 1) AS is_active,
       COALESCE(d.dept_name,'') AS dept_name, COALESCE(b.branch_name,'') AS branch_name
FROM users u
JOIN users_additional_info uai ON uai.user_id = u.user_id
LEFT JOIN dept_info d   ON d.dept_id = u.dept_id     AND d.org_id = u.parent_id
LEFT JOIN branch_info b ON b.branch_id = u.branch_id AND b.org_id = u.parent_id
WHERE u.parent_id = %(org)s AND u.role_id IN (2,3) AND u.status = 1 AND uai.shift_id = %(shift)s
ORDER BY u.name;
-- unassigned: LEFT JOIN uai, WHERE uai.shift_id IS DISTINCT FROM %(shift)s
-- assign (after verifying the shift belongs to the org and every user is an active role 2/3 of the org)
UPDATE users_additional_info SET shift_id = %(shift)s, updated_at = now() WHERE user_id = ANY(%(ids)s);
-- staff without an additional-info row (older data): INSERT with '' for the NOT NULL text columns
-- (acc_no, ifsc_code, bank_name, bank_branch, gender, address, emergency_contact_no), as add_staff does
-- unassign
UPDATE users_additional_info SET shift_id = NULL WHERE user_id = %(uid)s AND shift_id = %(shift)s;
```
`shift_id` is globally unique (`unique_shift_id` index), but check `shift.org_id` anyway. Unassign writes `NULL` (consistent with `delete_shift`); `0` and `NULL` both mean "none" everywhere the code reads it (`uai.shift_id > 0` checks).

**Validation and errors.** shift must exist, be active and belong to `org_id` (404 `"Shift not found"`); `user_ids` non-empty (400); foreign or inactive user id 404; the 18-hour sanity rule is a shift-create rule (`shift_functions.py:42-62`), assignment inherits it, no extra check. Idempotent.

**Side effects to document.** Lateness, overtime and PDFs are evaluated against the staff member's current shift, so a mid-month assignment rewrites the interpretation of earlier days. Recommended follow-up (not P0): `shift_assignment_history (user_id, shift_id, effective_from)` and `shift_for(user, date)`; NEW-8 needs the same helper for approved one-day shift swaps.

**Legacy compatibility.** New routes; the staff form still sets `shift_id` on add/update (`add_staff_function.py:413-421, 597-607`).

**Tests.** Unit: assigned/unassigned partition equals all active staff; assign twice is idempotent; unassign of a different shift id is a no-op. API: contract JSON; foreign org 404; assigning updates `GET /addstaff/{id}` and `/home/staff-profile` `shift_id`; deleting the shift empties the assigned list.

### P0-8 Version check for the new app (row 223)

**Current state `[V]`.** `router/version.py:8-45` selects one row per `platform` from `app_version_control` (a table that is not in the dump, so its constraints are unknown [U]) and compares with `packaging.version`; a version string that is not PEP 440 raises and becomes HTTP 500. The legacy app and Clockyn would share the row.

**Decision first (roadmap D-1): own backend or shared backend.** If Clockyn runs on its own deployment and database (strongly recommended, because of the schema, branding and behaviour changes in this document), no code is needed: seed the Clockyn values (below) and ship. The design below is for a **shared** backend.

**Design (shared backend).** Optional query parameter `app_id` (the bundle id, for example `com.clockyn.app`); absent means the legacy app. Files: `router/version.py`; frontend dependency FE-1: `src/lib/platform/appUpdate.ts` / `src/api/system.ts:92` add `app_id` (read from the native application id; omit on web).
```sql
CREATE TABLE IF NOT EXISTS app_version_control (
  id serial PRIMARY KEY, app_id varchar(100) NOT NULL DEFAULT 'legacy', platform varchar(20) NOT NULL,
  latest_version varchar(20) NOT NULL, min_supported varchar(20) NOT NULL,
  force_update boolean NOT NULL DEFAULT false, message text, store_url text,
  updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (app_id, platform));
-- if the table exists: ADD COLUMN IF NOT EXISTS app_id ... DEFAULT 'legacy'; replace the platform-only unique
-- constraint (look up its name first) by UNIQUE (app_id, platform).
INSERT INTO app_version_control (app_id, platform, latest_version, min_supported, force_update, store_url)
VALUES ('com.clockyn.app','android','1.0.0','1.0.0',false,'<owner C-9>'),
       ('com.clockyn.app','ios','1.0.0','1.0.0',false,'<owner C-9>')
ON CONFLICT (app_id, platform) DO NOTHING;
```
Contract unchanged: `GET /version/check?platform=android&version=1.0.0&app_id=com.clockyn.app` returns `{"success": true, "data": {"update_required": false, "latest_version": "1.0.0", "force_update": false, "message": null, "store_url": "https://..."}}`. No matching row returns `{"update_required": false, ...nulls}` (today only `update_required`). An unparsable version returns `update_required:false` and logs, never 500. Platform is lower-cased.

**Legacy compatibility.** A request without `app_id` hits the `legacy` rows exactly as today.

**Tests.** Unit: version compare (`1.0.0` vs `1.0.34`, `1.0.0` vs `1.0.0`, `2.0` vs `1.0.9`, garbage). API: legacy request unchanged; Clockyn request with and without a row; `force_update` when below `min_supported`; platform case.

### P0-9 Push delivery for the new Firebase project

**Current state `[V]`.** One service account from `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY`, `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID` (or `FCM_SERVICE_ACCOUNT_PATH`) is used by `core_functions/auth_function.py:61-128` (`get_fcm_access_token`, cached 55 minutes) and there are two copies of the send code (`auth_function.py:164-201`, `router/chat.py:27-63`). The app registers raw FCM tokens (`src/lib/platform/notifications.native.ts:147`), which matches the backend's raw-token V1 call. Android channel id `attendance_notifications` is hard-coded in both copies (`auth_function.py:178`, `router/chat.py:53`).

**Design.** New `core_functions/push_service.py` exposing `async send_push(token, title, body, data, image_url=None, silent=False)` and `get_fcm_credentials(project_key)`. Both existing call sites delegate to it. Behaviour: primary credentials from the five `FCM_*` variables; optional fallback credentials from `FCM_LEGACY_PROJECT_ID`, `FCM_LEGACY_PRIVATE_KEY_ID`, `FCM_LEGACY_PRIVATE_KEY`, `FCM_LEGACY_CLIENT_EMAIL`, `FCM_LEGACY_CLIENT_ID` (names only here, values are secrets). Send with the primary project; on HTTP 403 with `SENDER_ID_MISMATCH` (token belongs to the other Firebase project) retry once with the fallback; on `UNREGISTERED`/404 clear the token (`UPDATE users SET push_token = NULL WHERE push_token = %s`, today only the admin token is cleared, `auth_function.py:286-300`). Channel id from `FCM_ANDROID_CHANNEL_ID` (default `attendance_notifications`, the app creates that channel). `silent=True` sends a data-only message (needed by NEW-7). Strings in `data` are stringified as today.

If Clockyn has its own backend (D-1), only the five `FCM_*` variables change and the fallback is not configured.

**Legacy compatibility.** With only the legacy credentials configured behaviour is today's. During a shared-backend migration, legacy devices keep working through the fallback.

**Tests.** Unit (HTTP mocked): primary success; 403 mismatch triggers exactly one fallback; 404 clears the token; no credentials returns without raising; data values are strings. API: chat send and punch-in notification go through `send_push` (spy). Manual: one real push per platform from each project.

## 5. Designs for P1 items

Same conventions as section 4. Items are ordered as in `backend_changes.md`; the new findings (NEW-n) follow in section 6.

### P1-1 Employee requests: per-date statuses and shift ids (row 184; also P2-8 admin fields)

**Current `[V]`.** `get_employee_requests` (`core_functions/requests_functions.py:276-326`) returns `dates` only; shift change rows lack `requested_shift_id`/`current_shift_id`/`current_shift_timing`; a leave request's `status` is `NULL` (inserted `NULL` at `:214`, never written by approval, see P1-15). The admin query (`:329-425`) already builds `date_statuses`.

**Change.** Reuse the admin query's join for the employee list (`WHERE r.user_id = %s AND r.org_id = %s`), add `date_statuses`, `requested_shift_id`, `current_shift_id`, `current_shift_timing`, and return a derived request-level `status` for leave and permission: any date `pending` gives `pending`, else any `approved` gives `approved`, else `rejected` (the same aggregate the app computes, `src/api/mappers/requests.ts:deriveStatus`, and the mock, `requests.js:aggregate`). Also add `emp_id` and `profile_photo` to the admin list (NEW-10: `mapRequest` reads them, `requests.ts:mapRequest`, the real query returns neither).
```
GET /requests/employee/stf-0001/org-0001
200 {"success": true, "data": [
  {"request_id": "req-001", "request_type": "leave", "reason": "Family function", "status": "pending",
   "dates": ["2026-10-22", "2026-10-23"],
   "date_statuses": {"2026-10-22": {"status": "approved", "action_by": "adm-0001", "action_at": "2026-10-19T08:00:00"},
                     "2026-10-23": {"status": "pending",  "action_by": null,       "action_at": null}},
   "created_at": "2026-10-18T10:00:00", "action_by": null, "action_by_name": "", "action_at": null},
  {"request_id": "req-201", "request_type": "shift_change", "status": "pending", "reason": "Exam",
   "shift_change_date": "2026-10-25", "requested_shift_id": 2, "current_shift_id": 1,
   "requested_shift_timing": "06:00 AM - 02:00 PM", "current_shift_timing": "09:00 AM - 06:00 PM", "dates": []}]}
```
Backfill for existing leave rows (run once after P1-15 ships, so new writes and backfill agree):
```sql
UPDATE requests r SET status = x.agg, updated_at = now()
FROM (SELECT request_id,
             CASE WHEN bool_or(COALESCE(status,'pending') = 'pending') THEN 'pending'
                  WHEN bool_or(status = 'approved') THEN 'approved' ELSE 'rejected' END AS agg
      FROM leave_dates GROUP BY request_id) x
WHERE r.request_id = x.request_id AND r.request_type = 'leave' AND r.status IS DISTINCT FROM x.agg;
```
**Legacy compatibility.** Additive fields; the old app ignores them. A leave `status` that was `NULL` becoming a string is the only visible change (the old app already treats missing as pending, `[U]` check once).
**Tests.** Unit: aggregate for [approved, pending], [approved, rejected], [rejected, rejected], []. API: employee list equals the admin row for the same request; another user's request is not returned; backfill idempotent.

### P1-2 `branch_id` and related ids on staff endpoints (rows 94, 95, 207)

**Current `[V]`.** `/home/staff-detail` (`core_functions/home_functions.py:1095-1149`), `/home/staff-profile` (`:1151-1228`, `role_id` is selected at `:1161` but not in `StaffProfileResponse :63`), `/addstaff/{id}` (`add_staff_function.py:70-93, 464-509`) return names only. The app matches by name today.
**Change.** Add `branch_id` (string, like `/home/branches`), `dept_id`, `org_id` to detail/profile and `branch_id`, `dept_id`, `parent_id` (manager), `doj`, `father_name`, `marital_status`, `aadhaar_no`, `pan_no`, `uan_no`, `pf_no`, `esi_no`, `is_active`, `role_id` to `/addstaff/{id}` (the model `StaffDetailResponse` lacks them although the form edits the personal part elsewhere). Join branch/dept with the org scope (NEW-1). `GET /addstaff/{id}` must stop returning status-2 (deleted) users: 404.
```
GET /addstaff/stf-0001  200 {"user_id":"stf-0001","name":"Aarav Sharma","emp_id":"E1001","mobile_number":"9000000003","email":"...",
  "branch_id":"101","branch_name":"Head Office","dept_id":"dept-101","dept_name":"Sales","parent_id":"mgr-0001",
  "role_id":3,"role_name":"Staff","status":1,"is_active":true,"is_field_staff":0,"shift_id":1,"dob":"1994-03-02","doj":"2024-04-01",
  "gender":"Male","blood_group":"B+","address":"...","emergency_contact_no":"...","acc_no":"...","ifsc_code":"...",
  "bank_name":"...","bank_branch":"...","profile_photo_url":null}
```
**Legacy compatibility.** Additive. **Tests.** API: ids present and equal to `/home/branches` ids; deleted user 404; a manager's `parent_id` equals the manager's user id.

### P1-3 `GET /home/monthly-attendance` (row 97)

**Current `[V]`.** `core_functions/attendance_functions.py:154-250`: status is `late` if `punch_in.time() > shift_start` (`:199`, wall clock in the connection time zone vs a local shift time), no `shift_start_time`/`shift_end_time`, no day types, days without a row are omitted, `absent_days = days_in_month - rows` (`:229`, counts future days and weekly offs), any exception returns an empty month with HTTP 200 (`:241-250`), the shift is the user's *current* one.

**Change (additive, opt-in for the structural parts).**
- Always (additive per-day fields): `shift_start_time`, `shift_end_time` (`HH:MM` 24 h, null without shift), `late_by_minutes` (>= 0, IST), `sessions` (count), `status` stays within the set the app maps (`present | late | absent | half_day | leave`, `src/api/mappers/attendance.ts:12-18`, any other value is mapped to `absent`).
- Lateness: compare in IST: `(a.punch_in AT TIME ZONE 'Asia/Kolkata')::time > shift.start_time + grace` where the first session of the day is used. Same fix in the four dashboard queries (`home_functions.py:187,438,658,941`).
- `detail=v2` (new query parameter, default off): synthesise rows for every day up to today (IST) with a `type` (`working | holiday | weekly_off | leave`) and `holiday_name`; compute `absent_days = working days elapsed - present - approved leave`; add `half_days`, `leave_days`, `holiday_days`, `weekly_off_days`, `working_days`; mark late penalty days from the attendance rule (P0-4). Precedence for a date: has punches (status from punches) > approved leave > holiday (branch/department scope as in `holiday_functions.py:131-167`) > weekly off (`org_off_days`, default Sunday) > absent. Failures return the real status (500/404), not an empty month.
```
GET /home/monthly-attendance?staff_id=stf-0001&org_id=org-0001&month=10&year=2026&detail=v2
200 {"success": true, "month": 10, "year": 2026, "total_days": 31, "working_days": 26, "present_days": 14, "late_days": 2,
     "half_days": 1, "leave_days": 1, "holiday_days": 1, "weekly_off_days": 4, "absent_days": 2,
     "attendance_data": [
       {"date": "2026-10-05", "type": "working", "status": "late", "punch_in": "09:42 AM", "punch_out": "06:05 PM", "total_hours": "8.38",
        "shift_start_time": "09:00", "shift_end_time": "18:00", "late_by_minutes": 42, "sessions": 1, "location": "12.971600,77.594600",
        "punch_in_photo": null, "punch_out_photo": null},
       {"date": "2026-10-02", "type": "holiday", "status": "absent", "holiday_name": "Gandhi Jayanti"},
       {"date": "2026-10-04", "type": "weekly_off", "status": "absent"},
       {"date": "2026-10-08", "type": "leave", "status": "leave"}]}
```
(`status:"absent"` on holiday/weekly-off rows is deliberate: it is what an unaware client maps unknown values to, and `type` is the discriminator the updated client reads; frontend change FE-2 in `src/features/employee/utils/attendanceCalendar.ts` and `src/features/staff-directory/...` drops its own inference once `detail=v2` is adopted.)
**Frontend dependency.** FE-2 (consume `type`, shift times, `late_by_minutes`; send `detail=v2`). Until then the legacy shape is unchanged, so the current app is unaffected.
**Legacy compatibility.** Without `detail` the response keeps `absent_days`, `present_days` semantics; only the additive per-day fields appear.
**Tests.** Unit: day-type precedence; lateness at 08:59/09:00/09:01 IST for punches stored as UTC; night shift; a holiday scoped to another branch is ignored; leave day with a punch counts as present. API: v1 shape snapshot unchanged for the legacy fields; v2 for a month with every day type; month in the future returns zero rows; DB error returns 500, not an empty 200.

### P1-4 `PUT /addstaff/{id}` keeps role, branch and department (row 208)

**Current `[V]`.** `core_functions/add_staff_function.py:579-586` writes `role_id = 3` literally (demotes managers), sets `branch_id`/`dept_id` to the looked-up value, which is `NULL` whenever the request omits `branch_name`/`dept_name` (`:529-530`), `shift_id` defaults to 1 (`:23, 606`), and when a branch name is unknown the insert at `:550-556` names columns `branch_latitude`/`branch_longitude` that do not exist in the dump (the table has `gps_location`), so the request fails with 500.
**Change.** Do not touch `role_id`; omitted `branch_name`/`dept_name` keep the stored ids; an unknown name returns 400 `"Branch 'X' not found"` (the mock does the same, `staff.js:26`) instead of creating a branch with "Default Address" (the same auto-create exists in POST `:329-374`; keep it for POST only if the legacy app needs it, otherwise reject there too); omitted `shift_id` keeps the stored shift; `parent_id` omitted keeps the stored value (already true, `:527`). The column is overloaded: it holds the org id for staff, or whatever the client sends (`add_staff_function.py:393`); a staff member whose `parent_id` is a manager's user id is invisible to every `parent_id = org` query, is not counted against the staff limit (`:296-308`) and cannot log in (employee login joins `parent_id` to the organisation, `auth_function.py:1067-1073`). The app does not send it (DD-42), so the server must ignore a `parent_id` that is not the org id until a separate `manager_id` column exists (migration, optional). Mobile/e-mail/emp_id uniqueness on update (currently unchecked) against the same org, excluding the user.
**Legacy compatibility.** The old staff form always sends names, so behaviour is the same for it; managers stop being demoted. **Tests.** API: edit a manager (role stays 2, assignments stay); omit branch (branch kept); unknown branch 400; duplicate mobile 400/409; edit does not reset shift.

### P1-5 One holiday router with `block_attendance`, weekly offs, schema (rows 196, 225-230)

**Current `[V]`.** Three routers share `/api/holidays`: `router/daily_hourly.py:8-60` (accepts `block_attendance`), `router/holiday.py:11-76` (model without it, plus `off-days`), `router/report.py:25-47`. `app.py:48` mounts the first, `app.py:65` the second, `app.py:56` the third; Starlette serves the first match, so add/update/get/delete/working-days run from `daily_hourly.py` and only `off-days` from `holiday.py`. The persistence code needs `org_holidays.block_attendance` and table `org_off_days`, neither in the dump (`holiday_functions.py:6-43` (off days), `:140-170` (block check)). `check_holiday_block` uses `CURRENT_DATE` (`:160`) and joins branch by id only (`:149`).

**Change.** Keep `router/holiday.py` as the single holiday router: add `block_attendance` (bool, default true) to both models, return it in every row, keep `off-days`; delete the copies in `router/daily_hourly.py` (the file becomes the wages router, P0-1) and `router/report.py:25-47` in the **same release** (otherwise the live handlers silently change). Add duplicate rejection (same org, date, name, branch scope, department scope: 409 `"Holiday already exists for this date"`) and an optional bulk route, because the app issues one request per date (`src/api/system.ts:145-157`):
`POST /api/holidays/{org_id}/bulk {"name": "Diwali", "dates": ["2026-11-08","2026-11-09"], "branch_name": null, "dept_name": null, "block_attendance": true}` returning `{"success": true, "added": 2, "skipped": [{"date": "...", "reason": "exists"}]}`. `check_holiday_block` uses the IST date and the org-scoped branch join. A startup test asserts that no `(method, path)` is registered twice (P2-15).
```sql
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS block_attendance boolean NOT NULL DEFAULT true;
CREATE TABLE IF NOT EXISTS org_off_days (org_id varchar(36) PRIMARY KEY, off_days int[] NOT NULL DEFAULT '{6}');
CREATE UNIQUE INDEX IF NOT EXISTS ux_org_holidays_scope
  ON org_holidays (org_id, date, name, COALESCE(branch_name,''), COALESCE(dept_name,''));
-- before the unique index: dedupe existing rows (keep min(id))
```
Weekly-off validation: list of ints 0..6 (422 otherwise), empty list allowed (no weekly off).
**Legacy compatibility.** `block_attendance` default true equals today's behaviour; response keeps `{success, data}` and `{success, id, message}`.
**Tests.** API: add with `block_attendance:false` persists and `GET` returns it; `PUT {name, block_attendance}`; duplicate 409; route-uniqueness test; punch-in on a blocking holiday returns `is_holiday:true`, on a non-blocking one succeeds; IST boundary (punch at 00:30 IST on the holiday date); weekly offs round-trip `[5,6]`.

### P1-6 Group `read_update` payload and add-members event (rows 82, 83)

**Current `[V]`.** `router/chat.py:299-311` sends `{"type":"read_update","data":{"reader_id","group_id"}}` to every member including the reader; `get_group_messages` already computes `read_count` and `total_members` per message (`chat_functions.py:423-475`, `total_members = active members - 1`). `add_group_members` (`:582-609`) emits nothing.
**Change.** `mark_group_as_read` also returns, per newly read message, `{message_id, sender_id}`. For each such message send **only to its sender** (not the reader, not other members) `{"type":"read_update","data":{"reader_id","group_id","message_id","read_count","total_members"}}` with the message's current `read_count`; cap at 50 frames per call, above that send one frame without `message_id`/counts so the app refetches (the app already handles both shapes, `src/features/chat/utils/frames.ts:75-76`, `useGroupConversation.ts:134-140`). After add-members, push `{"type":"group_created","data":{group_id, group_name, created_by}}` to each **new** member and `{"type":"group_updated","data":{group_id, member_count}}` to existing members. Add `total_members` to `/group-chat/send` responses (`chat_functions.py:509-511`).
**Legacy compatibility.** Additive fields; legacy ignores `group_updated`. **Tests.** Unit: counts after 1 and 2 readers; reader excluded; > 50 messages sends the fallback frame. API/WS (two sockets): sender receives the frame with `read_count 1, total_members 2`; reader receives nothing; new member receives `group_created`.

### P1-7 Push and WebSocket payloads (rows 1, 40, 41, 80, 84; WS rows 238-239; includes NEW-6 and NEW-11)

**Current `[V]`.** Chat/group pushes carry `type` and ids but no `message_id` (`router/chat.py:198, 286, 411, 478`); `send_announcement` never publishes a socket frame (`announcement_functions.py:105-126`) and reports `recipient_count` 0 for every target but `selected` (`:68-70`). **NEW-6:** the app has one global socket, `/ws/chat/{userId}` (`src/lib/realtime/socket.ts`), and expects `request_submitted`, `badge_update` (with `notification`) and `announcement` on it (`src/lib/realtime/events.ts:61-92`). The backend sends `request_submitted` and `badge_update` only through `router/ws_badge.py:12-20` (`_connections`, the `/ws/badge` socket that the app never opens; one connection per user, overwritten on reconnect). The mock hides this: its single registry delivers any frame to any socket of the user.
**Change.**
1. `core_functions/realtime.py` (new): `async publish_to_user(user_id, frame)` sending to both registries (`socket_manager.manager._send_to_user_sessions` and `ws_badge._connections`); `notify_badge_update(user_id, event_type, payload=None)` delegates to it. Callers: `requests_functions._ws_notify_request` (`:84-101`), `auth_function.send_punch_in_out_notification` (`:261-263`).
2. Frames (match the mock): `{"type":"request_submitted","data":{"request_id":"req-301","request_type":"device_change"}}`; punch: `{"type":"badge_update","notification":{"id":"punch-1760000000","title":"Punched In","body":"Aarav Sharma punched in at 09:02 AM","type":"punch_in","image_url":null}}`; announcement: `{"type":"announcement","target":"branch","data":{"announcement_id":"ann-5","title":"...","target":"branch","org_id":"org-0001"}}` to every recipient (resolve with the same rules as the employee list, `announcement_functions.py:221-247`, active users only).
3. Add `message_id` (the `chat_id`) to the four push data dicts; stringified by `_send_fcm_push`.
4. `recipient_count` = number of recipients for every target; the push loop skips inactive users and, for `selected`, users outside the org (`:82-87` has no org filter).
**Legacy compatibility.** Frames are additive and delivered on both sockets; a client that opens both would see badge events twice (neither app does). **Tests.** WS integration: a socket opened on `/ws/chat/{admin}` only receives `request_submitted` after a staff submits a leave; punch-in produces `badge_update` with a `notification` object; announcement to branch reaches only that branch's users; push payload contains `message_id` equal to the REST response's `message_id`.

### P1-8 Staff punch-in must not report failure after committing (row 74)

**Current `[V]`.** `core_functions/face_attendance_functions.py:259-265` calls `send_punch_in_out_notification(..., image_url=img_url)`; `img_url` is never assigned in `punch_with_face` (the photo URL is produced later in a background task, `:236-251`), so a `NameError` is raised after `conn.commit()` (`:233`); the surrounding `except Exception` (`:375-378`) returns `{"success": false, "message": "Database error: name 'img_url' is not defined"}` for every role 2/3 punch-in [U: unless patched on the deployed server]. The punch row, the location log and the background photo upload all happened.
**Change.** Replace `img_url` with `None` (the notification renders without an image today for punch-out too, `:347`) and wrap the post-commit notification scheduling in its own `try/except` so nothing after the commit can flip the response. Build the response before scheduling side effects. Optionally pass the photo URL later from the background task.
**Legacy compatibility.** Pure fix. **Tests.** Unit: role 3 punch-in with a mocked notifier returns `success:true`; a notifier that raises still returns `success:true`; regression test for every role (1, 2, 3) and both actions. API: punch-in then `/attendance/today` shows the session and `/home/dashboard-status` counts it.

### P1-9 Delete semantics (rows 3, 33, 35, 48, 50, 55, 104, 111, 132)

| Endpoint | Current `[V]` | Change |
|---|---|---|
| `POST /branch/delete/{id}` | active-staff check scoped, then `DELETE ... WHERE branch_id = %s` without org (`branch_functions.py:241-244`); inactive staff keep a dangling id | `DELETE FROM branch_info WHERE branch_id=%s AND org_id=%s`; `UPDATE users SET branch_id=NULL WHERE branch_id=%s AND parent_id=%s AND status<>1`; `DELETE FROM manager_assignments WHERE branch_id=%s AND org_id=%s`. Same org filter in `reassign_staff` count (`:331-335`). |
| `DELETE /api/salary/template/{id}` | deletes only the `pay_slip` row (`salary_structure.py:325-340`): custom earnings/deductions and `payroll` rows orphaned; because the salary engine inner-joins `payroll` to `pay_slip` (`report_functions.py:537-544`) assigned staff silently lose basic pay | one transaction: refuse with 409 `{"detail":"Template has N staff assigned"}` while `payroll` rows exist (today the orphaned `payroll` row also blocks re-assigning that staff member, because `payroll(user_id)` is unique, `payroll_user_id_key`, so the assign fails with 500); otherwise delete earnings, deductions, template. Because the app has no un-assign (DD-78), add `DELETE /api/salary/assign/{org_id}/{staff_id}` so an admin can clear assignments. |
| `POST /department/delete/{id}` | `UPDATE users SET dept_id = NULL` and `DELETE` without org (`dept_functions.py:132-133`) | scope both by org; also clear `manager_assignments.dept_id`; optional `reassign_to` query parameter for DD-43; keep silent un-assign as default. |
| `DELETE /company/delete/{org}/{user}` | users, branches, departments, shifts only; the cascades at `company_functions.py:191-203` run for **any** org id and ownership (`org_info.parent_id = user_id`) is checked only by the last statement (`:203`), so a user who owns two companies can wipe a third party's staff, branches, departments and shifts by passing a foreign org id (`:175-231`) | first verify ownership (`SELECT 1 FROM org_info WHERE org_id=%s AND parent_id=%s`, 403 otherwise) before any statement; then, recommended: **soft delete** (`org_info.status = 0`, users `status = 2`, tokens cleared) + a retention job that purges after N days; if hard delete is required, delete in dependency order: attendance, odometer_readings, travel_bill_expenses, advance_details, payroll, assigned_wages, daily_hourly_wages, assigned_incentives and incentive tables, requests/leave_dates, leads (info, assignment, category, carry_forward_requests), announcement(+recipients), chat/chat_activity/group/group_members of the org, holidays, off days, attendance_rule, salary_payments, shift, dept, branch, then users, S3 prefixes (`delete_employee_s3_assets`, `ml/s3.py:356`); keep `gen_orders`, `gen_payments`, `gen_invoices` (tax records). |
| `DELETE /company/delete-account/{user}` | removes that user's rows only, attendance/requests remain (`:408-483`) | DD-40: soft delete + PII anonymisation (name, mobile, e-mail, bank/Aadhaar/PAN fields), clear tokens, delete face embeddings and photos from S3, keep attendance for payroll history; admin account: block while the org is active unless confirmed. |
| `DELETE /api/announcements/{id}`, `DELETE /api/leads/{id}`, `DELETE /api/leads/categories/{id}` | no org/caller check (`announcement_functions.py:170-193`, `leads_functions.py:121-137, 199-215`) | without caller identity only the row's own org can be checked; the real fix needs P3-1. Interim: optional `org_id` query parameter (404 on mismatch); lead delete also deletes `leads_assignment_info` and `carry_forward_requests`; category delete refused while leads use it. |

**Legacy compatibility.** Branch/department/lead/announcement behaviour for valid callers is unchanged; payslip delete with assignments now refuses (the old app had no assign-then-delete flow that depended on the orphaning). **Tests.** For each: delete in org A never touches org B rows that share ids (create two orgs with colliding branch ids); delete with children; delete idempotence (second call 404); payslip with assignment 409, after un-assign 200 and no orphan rows (`SELECT count(*)` on the three child tables).

### P1-10 Staff detail, profile and live-status lists (rows 88-95, 120)

**Current `[V]`.** `get_staff_detail` and `get_staff_profile` select `status = 1` only (`core_functions/home_functions.py:1112` and `:1172`), so an inactive member answers 404, and their own 404 is re-wrapped as HTTP 500 (`:1146`, `:1225`, no `except HTTPException: raise`). The five live-status lists build `StaffResponse` without `role_id`/`is_active` (`:603, 680, 753, 831`; the model defaults `is_active` to true and `role_id` to null, `:38-48`); the list and all-staff builders do pass them (`:362-372`, `:531-541`). `/home/staff-punch-locations` (`:1306-1369`) returns every staff member and has no `staff_id` filter.
**Change.** One shared row builder used by all lists (`user_id, name, emp_id, mobile_number, email, dept_name, branch_name, status, is_active, role_id, role_name, profile_photo_url`); add `u.role_id, u.status` to the five SELECTs; detail/profile return inactive staff with `is_active:false` and `status:"Inactive"` (404 only when absent or in another org), re-raise `HTTPException` untouched; `GET /home/staff-punch-locations?org_id&target_date&staff_id` filters to one user.
```
GET /home/staff-detail?staff_id=stf-0005&org_id=org-0001
200 {"user_id":"stf-0005","name":"Kiran Das","emp_id":"E1005","mobile_number":"9000000005","email":"...","dept_name":"Sales",
     "branch_id":"101","branch_name":"Head Office","branch_address":"12 MG Road, Bengaluru","org_id":"org-0001",
     "role_id":3,"is_active":false,"status":"Inactive","profile_photo_url":null}
```
**Legacy compatibility.** Additive; inactive staff stop being a 404 (the old app shows a toast on 404, so it improves). **Tests.** API: each list row has `role_id` and `is_active`; inactive detail 200; unknown id 404 (not 500); `staff_id` filter returns one row; manager id as scope returns only the manager's branches.

### P1-11 Leads (rows 100, 104-106, 110-115; includes NEW-5)

**Current `[V]`.** `get_leads` is `SELECT DISTINCT` over lead x assignment (`core_functions/leads_functions.py:52-63`): one row per assignment (duplicates after re-assigning) and **no `lead_status`** (`l.status` is selected only for the employee list, `:82`), so the admin pipeline status is always missing (NEW-5). `assign_lead` always INSERTs (`:102-118`), `update_lead_assignment` updates every assignment row of the lead (`:218-233`), `request_carry_forward` creates tables inside the handler (`:273-289`) and writes `description`/`request_date` on the assignment but never the status (`:304-307`); reject changes nothing; approve deletes the assignment (`:415-429`).
**Change.**
- `GET /api/leads/{org}`: one row per lead; primary assignment = most recently updated; `lead_status`; `assignees` array; `has_pending_cfr` boolean.
```sql
SELECT l.leads_id, l.client_name, l.mobile_number, l.client_address, c.category_name, l.remarks, l.created_at, l.created_date,
       l.status AS lead_status, la.user_id AS assigned_user_id, u.name AS assigned_user_name, la.status AS work_status,
       la.updated_at AS assigned_at,
       COALESCE((SELECT json_agg(json_build_object('user_id', a2.user_id, 'name', u2.name, 'work_status', a2.status))
                 FROM leads_assignment_info a2 JOIN users u2 ON u2.user_id = a2.user_id
                 WHERE a2.leads_id = l.leads_id AND a2.org_id = %(org)s), '[]'::json) AS assignees,
       EXISTS (SELECT 1 FROM carry_forward_requests cf WHERE cf.leads_id = l.leads_id AND cf.status = 'pending') AS has_pending_cfr
FROM leads_info l
LEFT JOIN leads_category_info c ON c.category_id = l.category_id AND c.org_id = l.org_id
LEFT JOIN LATERAL (SELECT * FROM leads_assignment_info x WHERE x.leads_id = l.leads_id AND x.org_id = l.org_id
                   ORDER BY x.updated_at DESC NULLS LAST LIMIT 1) la ON true
LEFT JOIN users u ON u.user_id = la.user_id
WHERE l.org_id = %(org)s ORDER BY l.created_at DESC;
```
- Migration: `ALTER TABLE leads_info ALTER COLUMN status TYPE varchar(20), ALTER COLUMN status SET DEFAULT 'new';` (the dump has `varchar(10)`: `PUT /api/leads/{id}/status` with `not_interested`, 14 characters, fails with 500, `leads_functions.py:446-466`, and the default `pending` is not a pipeline value) [U: check the deployed column]; dedupe `leads_assignment_info` (keep the newest row per `(leads_id, user_id)`), then `CREATE UNIQUE INDEX ux_leads_assignment_lead_user ON leads_assignment_info (leads_id, user_id);` create `carry_forward_requests` in a migration (it exists in the dump, the handler DDL is redundant) and add `prev_status varchar(20)`; move all `CREATE TABLE/ALTER TABLE` out of request handlers.
- `POST /api/leads/assign`: `INSERT ... ON CONFLICT (leads_id, user_id) DO UPDATE SET status='pending', branch_id=..., dept_id=..., updated_at=now()`.
- `PUT /api/leads/assignment/{id}?user_id=`: replace the lead's assignments by one new assignment for `user_id` (one transaction), equal to the v1 result when there was a single assignee.
- Carry-forward (DD-94 recommended): on request, store `prev_status` and set the assignment's work status to `carry_forward`; reject restores `prev_status`; approve keeps today's behaviour (move date, remove assignment). The admin list keeps approved rows but adds `actioned_at` and `employee_name`.
- `GET /api/leads/carry-forward/my/...` additionally returns `created_at` (the submitted timestamp the app displays) next to `request_date` (the forward-to date, `leads_functions.py:340-342`).
```
GET /api/leads/org-0001  200 {"success": true, "data": [{"leads_id":"lead-004","client_name":"Rohit Stores","mobile_number":"9876543210",
  "client_address":"...","category_name":"Retail","remarks":"","created_at":"2026-10-10T09:00:00","created_date":"2026-10-20","lead_status":"contacted",
  "assigned_user_id":"stf-0001","assigned_user_name":"Aarav Sharma","work_status":"working","assigned_at":"2026-10-11T08:30:00",
  "assignees":[{"user_id":"stf-0001","name":"Aarav Sharma","work_status":"working"}],"has_pending_cfr":false}]}
```
**Legacy compatibility.** The row keys the old app reads are unchanged; it loses duplicate rows (it merged them client-side). **Tests.** Unit: assign twice to the same user leaves one row; reassign with two existing rows leaves one; reject restores the previous work status. API: list has no duplicate `leads_id`; `lead_status` present; approve moves the date and removes the assignment; employee list unchanged.

### P1-12 Odometer allowance rate (rows 67, 70, 71)

**Current `[V]`.** The org rate is "the `allowance_rate` of the most recently updated reading" (`core_functions/emp_expenses_functions.py:432-439, 503-513`); saving the rate runs `UPDATE odometer_readings SET allowance_rate = %s WHERE org_id = %s` for every reading including approved ones (`:477-483`), and persists nothing for an org without readings while answering success; `rate` is `int` (`router/emp_expenses.py:35`, column `allowance_rate int4`); the salary engine uses `COALESCE(row_rate, org_rate, 1)` (`report_functions.py:680-709`) so an unset rate pays 1 per km while the app shows 3 (`odometerMath.ts:DEFAULT_RATE_PER_KM`).
**Change.**
```sql
CREATE TABLE IF NOT EXISTS org_allowance_rate (org_id varchar(36) PRIMARY KEY, rate numeric(8,2) NOT NULL CHECK (rate > 0),
  updated_by varchar(36), updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE odometer_readings ALTER COLUMN allowance_rate TYPE numeric(8,2);
INSERT INTO org_allowance_rate (org_id, rate)
SELECT DISTINCT ON (org_id) org_id, allowance_rate FROM odometer_readings WHERE allowance_rate IS NOT NULL
ORDER BY org_id, updated_at DESC ON CONFLICT DO NOTHING;
```
`POST /expenses/admin/allowance-rate {org_id, rate}` upserts the org rate (decimal accepted, `> 0`, `<= 9999.99`) and updates only **pending** readings (`UPDATE ... SET allowance_rate = %s WHERE org_id = %s AND status = 'pending'`); approved and rejected rows keep their stamp. Approve (`:429-446`) stamps the org rate (falling back to `DEFAULT_ODOMETER_RATE`, env, default 3). `GET /expenses/admin/allowance-rate/{org}` returns `{"success": true, "rate": 3.5, "is_default": false}`; unset returns the default with `is_default: true` (the app shows its own default for `<= 0`). The engine uses the same default constant.
Two more rules from the same code: approve stamps only on the transition to `approved` (`SET allowance_rate = CASE WHEN status <> 'approved' THEN %s ELSE allowance_rate END`; today `COALESCE(%s, allowance_rate)` re-stamps a re-approved reading with the current rate, `emp_expenses_functions.py:441-451`), and `POST /expenses/odometer/stop` refuses unless the reading is `pending` and picks the reading deterministically (`ORDER BY id DESC LIMIT 1`; today an approved reading's distance can be rewritten by the employee and `fetchone` takes any row of the day, `:119-181`).
**Legacy compatibility.** `rate` stays a JSON number; the old int column readers still work (numeric prints as 4.00 in SQL only). **Tests.** Unit: save for an org with no readings persists; save does not change an approved row; approve stamps the current rate; engine uses the stamp. API: GET default then after POST; rate `0`, `-1`, `"x"` rejected; decimal 3.5 round-trips.

### P1-13 Renew semantics and subscription state (rows 27, 177)

**Current `[V]`.** `verify_payment_process` (`core_functions/gen_payment_functions.py:67-103`): an active subscription with the **same `plan_id`** is treated as a capacity top-up (`staff_limit` added, `end_date` kept) so "Renew" extends nothing; a different plan replaces limit and dates. `GET /plans/subscription` without a paid row returns `staff_limit: null`, `plan_name` "Free Trial" or null, trial flags only inside `data` (`gen_plans_functions.py:61-75`).
**Change.** Order intent: `POST /orders/create` accepts optional `intent` (`new | renew | add_staff | upgrade`), stored in `gen_orders.intent` (`ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS intent varchar(12)`); absent keeps today's behaviour (legacy). In `verify_payment_process`: `renew` sets `end_date = max(existing.end_date, today) + plan_month months` and `staff_limit = order.staff_limit` (the quoted total), `add_staff` adds capacity and keeps `end_date`, `upgrade`/`new` replace. Frontend dependency FE-3: the quote screen sends `intent` (`src/features/billing/utils/quote.ts`, `src/api/billing.ts:104`); until then renew keeps its legacy meaning (decision DD-16 must be taken first). Subscription payload gets an explicit `state` (`trial | paid_active | paid_expired | none`), the trial staff limit (`TRIAL_STAFF_LIMIT`, default 10) and, for paid rows, `plan_id`, `plan_name`, `plan_amount`, `plan_month`, `start_date`, `end_date`, `staff_limit`, `status`, `is_expired`.
```
GET /plans/subscription/org-0001
200 {"success": true, "data": {"state": "paid_active", "plan_id": 3, "plan_name": "Elite", "plan_amount": 999.0, "plan_month": 12,
     "start_date": "2026-01-10", "end_date": "2027-01-10", "staff_limit": 50, "status": "ACTIVE", "is_expired": false,
     "is_free_trial": false, "free_trial_end": null, "billing_date": "2026-01-10"}}
```
Also: honour or remove `wants_gst_invoice` (accepted by the model, unused, `router/gen_payments.py` to `verify_payment_process`), and replace the hard-coded place of supply and item description (`gen_payment_functions.py:171, 179`).
**Legacy compatibility.** No `intent` means no change; `state` is additive. **Tests.** Unit: renew on an active same-plan subscription extends `end_date` by `plan_month` months from the later of today and the old end; add_staff adds capacity only; expired subscription renew starts today. API: `state` for trial/paid/expired/none organisations; signature failure 400; duplicate verify idempotent (`"Payment already verified"`).

### P1-14 Device mismatch contract and enforcement (row 12)

**Current `[V]`.** `verify_device_token` (`core_functions/auth_function.py:772-905`) answers a mismatch with `device_mismatch:true` and auto-submits a device-change request on every call (`:882-883`), never sends `request_pending`, and its message invites a re-login "with OTP to take over". `validate_otp` then overwrites `device_token` and `push_token` for any valid OTP (`:733-744`), so the block is only as strong as the client.
**Change.** (1) Send `request_pending: true` when a pending `device_change` request exists for the user (`SELECT 1 FROM requests WHERE user_id=%s AND request_type='device_change' AND status='pending'`); create a request only when none is pending (also in `submit_device_change_request`, `requests_functions.py:129-167`, return the existing id like the mock: `{"success": true, "request_id": "req-301", "message": "A device change request is already pending"}`). (2) Message: "This account is active on another device. Request access from your admin." (3) **Server enforcement behind `ENFORCE_SINGLE_DEVICE` (env, default off):** `validate_otp` for `purpose=login`, roles 2/3 only (DD-30 keeps admins multi-device), returns 409 `{"detail": "device_mismatch"}` when the stored `device_token` is set, unexpired and different from the supplied one; a cleared token (approved request) logs in normally. Decision DD-31 (a) must be confirmed; (b) "OTP take-over" is today's behaviour.
```
POST /auth/verify-device-token {"contact":"9000000003","device_token":"other","role_id":3}
200 {"success": false, "message": "This account is active on another device. Request access from your admin.", "skip_otp": false,
     "device_mismatch": true, "request_pending": true, "user_id": "stf-0001", "org_id": "org-0001"}
```
**Legacy compatibility.** With the flag off only additive fields and the dedupe change; the old app still takes over by OTP. **Tests.** Unit: pending detection; no duplicate rows after three mismatching calls. API: flag on: OTP from a second device 409; after approval (token cleared) OTP login 200 and `check-session` valid; admin unaffected.

### P1-15 `POST /requests/update-status` and the effect of approvals (row 186; includes NEW-8)

**Current `[V]`.** `core_functions/requests_functions.py:428-512`: leave decisions update only `leave_dates` (never `requests.status`, so leave stays `NULL`); permission updates the request only when all dates agree (`:464-476`); `shift_change`/`device_change` update the request (`:479-484`); `role_id` and `action_by` are accepted and ignored (any caller can decide any request); a request without date rows returns success having changed nothing (`:456-461`); **nothing consumes an approval**: no code reads `shift_change_date`/`requested_shift_id` after approval, no attendance, report or salary code reads `leave_dates` (grep over `core_functions`, only `add_staff_function.py:739` and `company_functions.py:433` delete them).
**Change.**
1. Authorise: `action_by` must be an active user of the request's org with role 1, or role 2 whose `manager_assignments` cover the requester's branch/department and who holds the permission `Requests Management`; else 403 `{"detail": "Not allowed to decide this request"}` (identity is client-supplied until P3-1, but the check removes accidental cross-org decisions).
2. After updating dates, recompute and store the request-level status with the same aggregate as P1-1 for leave **and** permission; stamp `action_by`/`action_at`.
3. A leave/permission request without dates: 404 `{"detail": "Request has no dates"}`. Document that a call **without** `leave_date` decides every date (the all-dates call; DD-91 keeps per-date chips read-only).
4. **Effects (need decisions D-7/D-8):** approved shift change = a **one-day override** for `shift_change_date` (recommended; the request carries a single date): add `shift_for(user_id, date)` in `core_functions/shift_functions.py` returning the approved override else the base shift, and use it in lateness, overtime, the attendance PDF and monthly attendance; no mutation of `users_additional_info`. Approved leave dates appear as `type:"leave"` in monthly attendance v2 and are excluded from absent counts; salary effect only if product defines paid/unpaid leave (DD-90), default none.
5. Push the decision to the requester with `request_id`, `status` (exists, `:494-505`); add WS `request_update` frame (the mock sends it).
```
POST /requests/update-status {"request_id":"req-001","status":"approved","action_by":"adm-0001","role_id":1,"leave_date":"2026-10-22"}
200 {"success": true, "message": "Request approved successfully"}
403 {"detail": "Not allowed to decide this request"}    404 {"detail": "Request has no dates"}
```
**Legacy compatibility.** Success shape unchanged; the old app always sends a valid `action_by`. Leave `status` now non-null (see P1-1). **Tests.** Unit: aggregate after per-date decisions; shift override lookup (override date, other date, rejected request ignored); authorisation matrix (admin of org, admin of another org, manager without permission, staff). API: decide a leave date then employee and admin lists agree; date-less request 404; device-change approval clears both tokens and `check-session` returns `device_change_approved`.

### P1-16 Payslip preview month (row 138)

**Current `[V]`.** `core_functions/salary_structure.py:545-553` stamps the response with `get_utc_now()` month/year and builds the preview from the template only; the branch join is unscoped (`:511`).
**Change.** Optional `month` (1..12) and `year` query parameters (default: current IST month); `month` in the response is the English month name for the requested period, plus `month_number`. Figures stay template-based (the app labels it a preview); if product wants attendance-based amounts reuse the salary engine (NEW-12) and add `net_pay_actual`. Scope branch/department joins (NEW-1).
**Legacy compatibility.** No parameter means today's behaviour. **Tests.** API: `month=2&year=2026` returns "February"/2026; month 13 422; two orgs with the same branch id do not leak names.

### P1-17 Manager scope on the server (rows 86 and the staff/report families)

**Current `[V]`.** `get_manager_scope` (`core_functions/manager_functions.py:376-404`) resolves an org id or a manager's own user id to `(org_id, branch_ids, dept_ids)` and is used by the dashboard, lists, daily attendance and `/requests/admin`; not by `get_department_stats` (`home_functions.py:389-470`, which also treats the manager id as the org id and so returns nothing), branch routes (`GET /branch/staff/{id}?org_id=<managerId>` finds no staff because `u.parent_id = %s` takes the org), salary, reports and PDFs.
**Change.** `core_functions/scope.py`: `resolve_scope(id_param) -> Scope(org_id, role_id, actor_user_id, branch_ids|None, dept_ids|None)` (extends `get_manager_scope`; a manager id or an org id both resolve) and `scope_sql(scope, alias='u')` returning `(" AND u.branch_id = ANY(%s) AND u.dept_id = ANY(%s)", params)`. Apply to: department stats, branch routes (manager id resolves to the org, results limited to assigned branches, delete/reassign refused for branches outside the assignment), salary breakdown, monthly-list, overtime report, `/api/reports/*`. With P3-1 the scope is derived from the token and the id parameters are only validated against it.
**Legacy compatibility.** The convention "manager id in the org-id slot" keeps working. **Tests.** API: manager sees only assigned branch/department rows in each listed endpoint; admin unchanged; manager id on `/branch/staff` works; unassigned manager (no rows) sees nothing rather than everything (today `None` means unrestricted, `:400`; decide, see roadmap D-10).

## 6. Designs for the findings added by this review

### NEW-1 `branch_id` is not unique across organisations (rows 30, 31, 73, 74, 96, 138)

**Current `[V]`.** Branch ids are allocated three ways: `MAX(branch_id)+1` over the **whole table** (`branch_functions.py:83`, `company_functions.py:62, 292`), `MAX+1` per organisation (`add_staff_function.py:342, 545`), and 1, 2, 3, ... per organisation at registration (`auth_function.py:392-399`). Queries join `branch_info` on the id alone: `face_attendance_functions.py:41, 157` (geofence), `attendance_functions.py:77` (daily attendance), `holiday_functions.py:149`, `salary_structure.py:511`, `add_staff_function.py:110, 204`; lookups and deletes by id alone: `branch_functions.py:131-135` (`GET /branch/{id}`), `:241-244` (delete). With two organisations each owning a branch 1, a staff member's geofence is evaluated against the first matching row (`fetchone()`), the daily attendance report duplicates staff rows, and `GET /branch/1` can return another tenant's branch (including its `org_id`).
**Change (phase A, code only, ships first).** Add `AND b.org_id = u.parent_id` (staff) or `= COALESCE(u.org_id, u.parent_id)` (admin rows) to the six joins; `GET /branch/{id}` takes an optional `org_id` query and filters by it (the app can pass it, `src/api/branches.ts:22`); delete and reassign scoped by org (P1-9). **Phase B (migration, only for a database that already has colliding ids; skip for a fresh Clockyn database):** detect `SELECT branch_id FROM branch_info GROUP BY branch_id HAVING count(DISTINCT org_id) > 1;`, create `CREATE SEQUENCE branch_id_seq` starting above `max(branch_id)`, re-key all but one organisation per id (update `branch_info`, `users.branch_id`, `manager_assignments.branch_id`, `announcement.branch_id`, `leads_assignment_info.branch_id` in one transaction per organisation), and switch registration, add-staff auto-create, add-company and add-branch to `nextval('branch_id_seq')`. For a fresh database apply the sequence from day one.
**Legacy compatibility.** Phase A returns identical data for single-tenant data; phase B changes ids only for colliding organisations and must be run with the app idle. **Tests.** Unit: two orgs with a branch 1 each at different GPS: geofence checks use the staff member's own org; daily attendance returns one row per staff; `GET /branch/1?org_id=B` returns B's row. Migration test on a seeded collision.

### NEW-2 Unique index versus two sessions per day, and the admin edit (row 98)

**Current `[V]`.** The dump has `UNIQUE INDEX attendance_user_id_attendance_date_key (user_id, attendance_date)`, while `punch_with_face` inserts a second row per user per date (max two, `face_attendance_functions.py:203-209`) and every report aggregates over several rows per date (`attendance_functions.py:79-86, 172-187`). With the unique index a second punch-in fails with a database error (`:375-378` returns it as `Database error: ...`); without it, `edit_attendance` fails instead (`ON CONFLICT (user_id, attendance_date)` needs the index, `attendance_functions.py:314-320`), and when two rows exist it collapses them into one. One of the two features is broken on any given database [U: check `\d attendance` on the deployed schema].
**Change (recommended).** Keep two sessions (DD-04). Replace the unique index by `CREATE INDEX IF NOT EXISTS idx_attendance_user_date ON attendance (user_id, attendance_date)` (already present) and a partial unique index that enforces "at most one open session": `CREATE UNIQUE INDEX ux_attendance_one_open ON attendance (user_id) WHERE punch_out IS NULL;` (the advisory lock at `:187-190` already serialises punch-ins). `edit_attendance` gets an optional `session` (1 or 2, default 1 = earliest by `punch_in`): `UPDATE attendance SET punch_in=%s, punch_out=%s, work_hours=%s WHERE id = (SELECT id FROM attendance WHERE user_id=%s AND attendance_date=%s ORDER BY punch_in LIMIT 1 OFFSET %s)`; if no row, `INSERT` (no `ON CONFLICT`). Keep the response shape (`success, message, work_hours, punch_in, punch_out`).
Two related defects in the same function: a session left open on an earlier day blocks every later punch-in (the open-session check at `:194-201` is not date-scoped; recovery today is an admin edit that supplies the missing punch-out; the policy for forgotten punch-outs is decision D-22), and the "do not fail the punch if location logging fails" try/except (`:216-224`, `:309-317`) cannot protect the transaction because a failed INSERT aborts it in PostgreSQL; wrap those two inserts in a `SAVEPOINT`.
**Legacy compatibility.** Removes a latent failure; same API. **Tests.** DB: second punch-in same day succeeds, third refused (`"Maximum 2 punch-in sessions allowed per day."`), a second open session refused; edit session 2; edit a day without rows creates one; admin edit does not merge sessions.

### NEW-3 `/attendance/sessions` time format and shape (row 6)

`router/face_attendance.py:78-79` formats `punch_in.strftime("%I:%M:%S %p")` without converting to IST (the sibling `/attendance/today` converts, `face_attendance_functions.py:420-432`), omits `work_hours`, and returns HTTP 200 `{"success": false, "sessions": []}` on any database error (`:86-88`). **Change:** `format_ist_time(..., "%I:%M:%S %p")`, add `work_hours` per session, raise 500 on failure. Response unchanged otherwise: `{"success": true, "sessions": [{"punch_in": "09:15:00 AM", "punch_out": "01:00:00 PM", "work_hours": 3.75, "punch_in_photo": "https://...", "punch_out_photo": null}]}`. **Tests:** a punch stored as 03:45 UTC reads "09:15:00 AM" on both endpoints.

### NEW-4 Location log uses a single arbitrary row (row 116)

`log_staff_location` (`core_functions/staff_location_functions.py:56-69`) reads `attendance WHERE user_id AND attendance_date = <UTC today>` with `fetchone()`: with two sessions it sees an arbitrary one, so tracking in the second session can answer 403 "stopped after punch out" while the user is punched in; it also uses the UTC date. **Change:** `SELECT 1 FROM attendance WHERE user_id=%s AND punch_out IS NULL LIMIT 1` for "currently punched in"; the 403 messages stay (`"Location tracking only active after punch in"`, `"Location tracking stopped after punch out"`, chosen by whether any session exists for the IST day). `latitude`/`longitude` of `0` must be accepted (`if not all([...])` rejects 0). The two read paths have the sibling defect: `get_staff_locations` (`staff_location_functions.py:130-137`) and `home_functions.get_staff_locations` (`:1269-1276`) `LEFT JOIN attendance` on the date without aggregation, so a staff member with two sessions appears twice on the Staff Location screen; aggregate with `MIN(punch_in)`/`MAX(punch_out)` as `get_daily_attendance` does (`attendance_functions.py:79-86`). **Tests:** session 1 closed, session 2 open: log accepted; both closed: 403; punch at 00:30 IST.

### NEW-6 / NEW-7 Realtime delivery and forced logout (WS rows 238-239)

NEW-6 is specified in P1-7. **NEW-7:** the app signs a user out on an FCM data message `{type: "force_logout"}` (`src/components/layout/bootstrap/sessionWatch.ts:67-70`), but the backend never sends one; it only offers an SSE endpoint the app does not use (`router/auth.py:409-446`, `core_functions/session_registry.py`) and the polled `check-session`. **Change:** in `validate_otp` (before the `UPDATE users SET device_token ...` at `auth_function.py:733-744`) read the user's previous `device_token` and `push_token`; when a different non-null device token existed and the new token differs, send a silent FCM data message `{"type": "force_logout", "user_id": "..."}` to the old push token through `push_service.send_push(..., silent=True)` (P0-9). **Tests:** takeover login triggers exactly one silent push to the old token; same-device re-login triggers none.

### NEW-12 / NEW-13 Salary engine (rows 189-191, 179)

**Current `[V]`.** `get_staff_salary` (`core_functions/report_functions.py:532-775`) is one function used per staff member by the breakdown (N+1, each with its own connection, `router/salary.py:55-57`), whose failures are silently turned into zero rows (`:87-101`). Defects: `present_days = COUNT(*)` of attendance rows (`:566-575`) so a two-session day counts twice (daily wage doubled, overtime shift subtracted twice); overtime shift minutes use `end_time - start_time` without wrap (`:665-675`, negative for night shifts, inflating overtime); `salary_type` is the constant `MONTHLY_FIXED` and `fine` the constant 0 (`router/salary.py:69, 76`); basic pay is never prorated.
**Change.** New `core_functions/salary_engine.py`: `compute_org_salaries(org_id, year, month, user_ids=None) -> list[SalaryRow]` using one set-based query per component (payslips with custom lines, attendance `COUNT(DISTINCT attendance_date)` and `SUM(work_hours)` per user for the IST month range, wages, incentives, overtime (P0-5 helper), odometer with stamped rates (P1-12), approved expenses, advances, late penalty (P0-4), paid state (P0-6)). `get_staff_salary(user_id, year, month)` becomes a one-user wrapper (it backs `GET /api/reports/staff-salary/{user_id}`, `router/report.py:49-51`, the response shape stays). `staff-breakdown` and `monthly-list` call the batch function; a failure for one user is logged and returned as a row with `"error": true`, never as zeros. `salary_type` = `MONTHLY_FIXED` when a payslip is assigned, else `DAILY_WAGES` when a daily template is assigned, else `HOURLY`, else `MONTHLY_FIXED`; additive `salary_types` array lists all that apply. `fine` = late penalty (P0-4).
**Numbers that will change (release note):** (1) two-session days now count once for daily wages and overtime; (2) night-shift overtime is computed with a positive shift duration; (3) `fine` for organisations that saved an attendance rule. Everything else must be byte-identical: add a snapshot test over a fixed seed before refactoring.
**Decisions still open:** prorating monthly-fixed basic pay by attendance (DD-60/61), and the incentive formulas: COUNT and TURNOVER templates store `count_upto`, `next_count` and `turnover_upto` but the engine never uses them (`report_functions.py:619-647`: a count template pays the flat `each_incentive` to anyone assigned, turnover pays `basic x percent`) because the database holds no per-staff count or turnover figure; today's formula is kept and documented (roadmap D-21). Not part of this change.
**Tests.** Snapshot: engine output equals the old function for a seed without two-session days or night shifts; batch equals single for every staff member; query count of the batch is constant in the number of staff (assert with a statement counter); error row for a broken payslip.

### NEW-14 Schema baseline and migrations (all `Check` rows)

**Current `[V]`.** `migrations/` contains only a README (instructions for a legacy dev-to-public schema copy); `migrate.py` applies one named file; the dump is stale; handlers run DDL (`leads_functions.py:273-289, 324-337, 366-379`); code references objects that are not in the dump (Appendix A).
**Plan.** (1) `pg_dump --schema-only` of the **deployed** database becomes `migrations/000_baseline.sql` (taken by someone with access; this review did not connect to anything). (2) Extend `migrate.py` to apply every `migrations/NNN_*.sql` not recorded in `schema_migrations (version text primary key, applied_at timestamptz default now())`, one transaction per file, honouring `DB_SCHEMA`; the container start command runs it before `uvicorn` when `RUN_MIGRATIONS=1`. (3) Migrations, in order: `001_drift_objects` (`org_off_days`, `org_holidays.block_attendance`, `app_version_control` + `app_id`, `ticket_replies`, `gen_orders.coupon_code`, `gen_invoices.coupon_code`, `gen_invoice_seq`, `admin_subject_request.user_id`, each `IF NOT EXISTS`); `002_attendance_sessions` (NEW-2); `003_attendance_rule` (P0-4); `004_salary_payments` (P0-6); `005_org_allowance_rate` (P1-12); `006_leads` (dedupe, unique index, `carry_forward_requests`, `prev_status`); `007_branch_id_seq` (NEW-1, only if applicable); `008_gen_orders_intent` (P1-13); `009_indexes` (`assigned_wages (user_id, temp_id)`, `requests (user_id, request_type, status)`, `attendance (user_id) WHERE punch_out IS NULL` as in NEW-2). (4) CI check: apply 000-009 to an empty database and run the contract tests. (5) Remove all handler DDL once migrations exist.
**Tests.** Empty database + all migrations = schema the code expects (run the whole API test suite); re-running the migrator is a no-op.

### NEW-15 Staff "delete" is a deactivation (row `DELETE /addstaff/{id}`)

`delete_staff` sets `status = 0` (`core_functions/add_staff_function.py:641-645`), identical to `PUT /addstaff/{id}/status?status=0`, so deleted staff stay in `/home/all-staff` as "Inactive" (that query has no status filter, `home_functions.py:487-552`), can be re-activated, and keep blocking their employee id and mobile number (the duplicate check ignores status, `add_staff_function.py:312-322`). The mock models delete as status 2 and hides it. **Change:** delete sets `status = 2`, clears `device_token`/`push_token`, and `status = 2` rows are excluded from every list and from the duplicate checks; keep `DELETE /addstaff/{id}/permanent` for hard deletion; re-activation of status 2 is refused (403). Login already reports status 2 as deleted (`auth_function.py:565, 800, 971, 1056`). **Tests:** delete then list (absent), re-add the same mobile and emp id (succeeds), login as the deleted user (403 "deleted"), re-activate (403).

### NEW-17 `PUT /company/update/{org}` re-keys departments and branches (row `PUT /company/update`)

**Current `[V]`.** `core_functions/company_functions.py:232-335`: the department block (`:300-314`) deletes **all** `dept_info` rows of the organisation and re-inserts them with new random ids whenever the set of names differs from the stored one (adding, renaming or removing one department), which orphans `users.dept_id`, `manager_assignments.dept_id`, `announcement.dept_id` and holiday scopes; when `branches` is non-empty the branch block (`:285-297`) deletes all branches (GPS and radius lost) and re-inserts them at `POINT(0,0)` with new table-wide ids. The app always sends `departments` (`src/api/mappers/org.ts:toCompanyBody`) and sends `branches: []` in edit mode. There is no ownership check: the `user_id` in the body is ignored.
**Change.** Diff by name: names present in the request but not stored are inserted (new id); names stored but absent are removed **only** when no active staff, manager assignment or announcement uses them (otherwise 409 `{"detail": "Department 'Sales' still has staff"}`; the app can then offer reassignment, DD-43); names present in both keep their id; a rename needs the optional additive field `department_ids` (parallel list of existing ids) which, when present, updates the name in place. Branches: never delete in this route (branch edit/delete have their own routes); non-empty `branches` only inserts missing names. Require `body.user_id == org_info.parent_id` (403 otherwise). The remaining behaviour (org fields, additional info, admin mobile/e-mail) is unchanged; add the missing duplicate check for the new mobile/e-mail against other users.
**Legacy compatibility.** The old company form sends the same payload; the only visible change is that it no longer loses staff-to-department links. **Tests.** API: add one department (existing ids unchanged, users keep their department), remove an unused department (gone), remove a used one (409), `branches: []` leaves branches and GPS untouched, wrong `user_id` 403, rename with `department_ids`.

## 7. Appendices

### Appendix A. Schema drift inventory (code versus `*_ddl_latest.sql`)

| Object | Used at | In dump | Action |
|---|---|---|---|
| table `org_off_days` | `core_functions/holiday_functions.py:6-43` | no | create (P1-5 / NEW-14) |
| column `org_holidays.block_attendance` | `holiday_functions.py:44-76, 106-121, 160` | no | add `boolean NOT NULL DEFAULT true` |
| table `app_version_control` | `router/version.py:15` | no | create / add `app_id` (P0-8) |
| table `ticket_replies` | `core_functions/support_functions.py:78-82, 130-171`; fallback queries without it | no | create; the route answers 503 mentioning an unavailable `change_db.sql` (`router/support.py:83-84`), that script is not in the repository |
| column `admin_subject_request.user_id` | `support_functions.py:27-30, 135, 175` | no | add `varchar(36)` |
| column `gen_orders.coupon_code` | `core_functions/gen_orders_functions.py:96-110` | no | add `varchar(50)` |
| column `gen_invoices.coupon_code` | `core_functions/gen_payment_functions.py:139-158`, `router/gen_orders.py:48-70`, `utils/email.py:1067` | no | add `varchar(50)` |
| sequence `gen_invoice_seq` | `gen_payment_functions.py:148` | no | create `START 1` (continue from the highest existing invoice number) |
| columns `branch_info.branch_latitude`, `branch_longitude` | `core_functions/add_staff_function.py:550-556` | no (the table has `gps_location point`) | code bug, fix the statement (P1-4) |
| unique index `attendance (user_id, attendance_date)` | dump; contradicts `face_attendance_functions.py:203-209` | yes | NEW-2 |
| enum `order_status` labels | code writes `CREATED` (`gen_orders_functions.py:107`) and reads `PAID`; dump default is `PENDING` | enum body not in the dump | verify on the deployed database [U] |
| seed rows (not DDL) | `role_info` 1/2/3 (names used by `get_staff_profile`), `gen_plans` ids 1/2/3 (constants at `gen_plans_functions.py:159-161`), the ten manager permission names in the legacy-prefixed settings table, `gen_razorpay_key`, `gen_coupons`, `admin_support` subjects, `gen_admin_login` operators, `app_version_control` | no data in the dump | `testing_strategy.md` section 3 lists the seed; production needs the owner's values |
| permission-name mismatch | `core_functions/permission_checker.py:78-114` names `Department Management` and `Payroll Management`; the app's catalogue (`src/features/team-rules/utils/permissions.ts:14-25`) and the mock use `Salary Management` and `Requests Management` and no department permission | the settings rows are not in the dump | the stored names must equal the app's ten strings exactly (the session list is delivered at login, `auth_function.py:875, 1104`); `permission_checker` names are only used by `/managers/permissions/{id}`, which the app does not call |

### Appendix B. Realtime and push contract matrix

| Event | Emitted by the backend | Channel | App consumer | Gap |
|---|---|---|---|---|
| `new_message` | `router/chat.py:187` -> `socket_manager.broadcast_chat_message` | `/ws/chat/{user}` (receiver and sender) + FCM `type=new_message` | `interpretSocketFrame`, `interpretPushData` | push lacks `message_id` (P1-7) |
| `new_group_message` | `router/chat.py:273` | each member's `/ws/chat` socket + FCM `type=new_group_message` for offline members | same | push lacks `message_id`; group socket registers the same user again (duplicate delivery, app de-dupes) |
| `read_update` (1:1) | `router/chat.py:209-212` | sender's `/ws/chat` | `readAppliesToDirect` | none |
| `read_update` (group) | `router/chat.py:305-310` | every member including the reader | `readAppliesToGroup` | no `message_id`/`read_count`/`total_members` (P1-6) |
| `group_created` | `router/chat.py:241` | members except the creator | `interpretChatFrame` | not sent on add-members (P1-6) |
| `announcement` | **never** | none | `interpretSocketFrame` (`socket`), `interpretPushData` (`push`) | frame missing (P1-7); push exists (`announcement_functions.py:107-118`) |
| `request_submitted` | `requests_functions.py:84-101` via `router/ws_badge.py` | **`/ws/badge` only** + FCM `type=request_submitted` (`:159-160`) | `/ws/chat` + push | socket frame never reaches the app (NEW-6); push works |
| `badge_update` (punch) | `auth_function.py:261-263` via `ws_badge` | **`/ws/badge` only** + FCM `type=punch_in/punch_out` | `/ws/chat` + push | as above, and the frame has no `notification` object (the app falls back to the push for the text) |
| `request_updated` | `requests_functions.py:494-505` | FCM only (`type=request_updated`) | `notificationRouting.ts:101` | none; the mock also sends a WS `request_update` (not needed) |
| `force_logout` | **never** (SSE `/auth/session-events` only) | none | `sessionWatch.ts:67-70` | NEW-7; the polled `check-session` still works |
| presence (`is_online`) | hard-coded false (`chat_functions.py:143`) | none | chat list | derive from `socket_manager.active_connections` (P2) |

Single-process limit: `socket_manager.manager.active_connections` (`socket_manager.py:9`), `router/ws_badge.py:9` and `core_functions/session_registry.py:5` are in-memory dictionaries. The container runs one uvicorn process (`Dockerfile` CMD), so this works; a second process or instance splits delivery silently. Document "single worker" in the deployment notes or move fan-out to Redis pub/sub before scaling out (P3). None of the three WebSocket routes authenticates the caller (P3-1).

### Appendix C. Where the mock differs from the real backend (fix the mock or the backend, decide per row)

| # | Topic | Mock | Real backend | Recommendation |
|---|---|---|---|---|
| 1 | `salary_type` | `Monthly/Daily/Hourly/Not assigned` (`routes/reports.js:44`) | constant `MONTHLY_FIXED` (`router/salary.py:69`); the app's keys are `MONTHLY_FIXED/WEEKLY/DAILY_WAGES/HOURLY` | fix the mock to the app's keys; backend per NEW-12 |
| 2 | geofence with `force_accept` | skipped (`routes/attendance.js:95-98`) | still enforced for non-field staff (`face_attendance_functions.py:170-178`) | fix the mock |
| 3 | staff punch-in | success | `success:false` after commit (P1-8) | add a mock flag to reproduce, remove after the fix |
| 4 | socket routing | any frame reaches any socket of the user (`index.js:327-358`) | `/ws/chat` and `/ws/badge` are separate registries (NEW-6) | make the mock deliver `request_submitted` and punch frames only on `/ws/badge` until NEW-6 ships |
| 5 | chat `is_online` | computed | always false | implement in the backend |
| 6 | leave `status` / employee list | aggregated, no `date_statuses` on the employee list | `NULL`, no `date_statuses` (P1-1/P1-15) | keep the mock; add the aggregate in the backend |
| 7 | incentive assign | moves staff off other templates of the same kind (`routes/payroll.js:240-247`) | adds, no rule (`incentive_functions.py:359-443`) | decide DD-76 then align |
| 8 | `/addstaff/{id}` | includes `parent_id`, `is_field_staff` | no `parent_id`/ids (P1-2) | backend change |
| 9 | location stats window | 30 minutes | 15 minutes (`staff_location_functions.py:281`) | align (app computes its own freshness) |
| 10 | admin requests | include `emp_id`, `profile_photo` | do not (P2-8) | backend change |
| 11 | `/home/staff-detail` | `org_id`, `branch_address` | `branch_address`, no `org_id`, no `branch_id` | backend change (P1-2) |
| 12 | `/carry-forward/my` `request_date` | submitted timestamp | forward-to date | add `created_at` |
| 13 | `/version/check` | object at top level | `{success, data}` | app accepts both; keep |
| 14 | staff add over the limit | HTTP 400 | HTTP 403 (`add_staff_function.py:306-309`) | align the mock |
| 15 | staff delete | status 2, hidden everywhere | status 0, shown as Inactive (NEW-15) | backend change |
| 16 | ids | `stf-0001`, `org-0001` | UUID strings; several routes validate UUID (`router/add_staff.py:200-210`) | seed with UUIDs for real-backend runs |
| 17 | shifts | `DELETE` is a soft delete (status 0) | hard delete (`shift_functions.py:187-229`) | none for the app |
| 18 | chat/group list gating | HTTP 500 `"403: ..."` | same (P2-2) | keep until P2-2 ships, then the app's unwrapping is a no-op |

### Appendix D. Backend routes the Clockyn app does not call (86 of 303)

| Group | Routes | Note |
|---|---|---|
| Operator console | 21 `/gen-dashboard/*`, 6 operator-login/admin-user routes in `router/auth.py:54-262` | identified only by a `phone_number` parameter or an `x-admin-id` header, can delete organisations (P3-9); keep behind the operator authentication, never expose with the app |
| Plans and coupons admin | `/plans/all`, `POST/PUT/DELETE /plans`, 4 coupon CRUD routes | admin tooling |
| Duplicates | `router/holiday.py` and `router/report.py` copies of `/api/holidays/*` (10 rows), `/addstaff/branches/` and `/addstaff/departments/` aliases | remove (P1-5) |
| Legacy | `/announcement/*` (5, `router/chat.py:323-352, 486`), `/home/department-status` (answers 500 for any non-empty organisation: it builds `DepartmentSummaryResponse` with the field spelled `overall_status` while the model declares `overall_Status`, `home_functions.py:102-104, 1000-1003`), `/home/staff-list`, `/home/format-date`, `/home/staff-locations`, `/addstaff/shifts/`, `/addstaff/staff-by-role`, `/addstaff/branch-info`, `/addstaff/department-info`, `/managers/get-staff-by-manager`, `/managers/settings/all`, `/managers/permissions/{id}`, `/managers/{id}/check-permission/...`, `/managers/{id}/enabled-settings`, `/orders/history/{org}`, `GET /api/reports/staff-salary/{user}` | keep for the legacy client until retired |
| Support/suggestion/attribution admin | `/support/update/{id}`, `/support/requests/all`, 4 `/company/suggest-feature*` read/reply routes, `/company/deletion-requests`, `/attribution/reports/meta-attribution` | operator side |
| Debug / infrastructure | `GET /auth/push-token-status/{id}` (P3-10), `GET /auth/session-events/{id}` (SSE), `DELETE /staff-location/cleanup` (P3-9), `POST /webhook/razorpay`, `POST /webhook/msg91/whatsapp`, `/ws/badge/{id}`, `POST /auth/register-company` (alias of register) | webhooks are required by the providers |
| Unmounted | root `chat.py` `/send`, `/upload` | dead code, delete |

### Appendix E. Cross-cutting hardening notes found while reading (all P3 unless stated)

- Hard-coded review-account OTP bypass: four numbers and a fixed OTP at `core_functions/auth_function.py:29-34`, honoured at `:517` and `:645` (credential-like constant, not reproduced). Gate behind an environment flag that production never sets.
- Biometric data in the repository: `known_faces/` holds 24 `.npy` face-embedding files for 6 user ids (`README.md:124` calls it a fallback cache) and `.dockerignore` does not exclude it, so it ships in the image. Remove before the Clockyn repository is published; embeddings are loaded from S3 (`ml/s3.py:72-98`).
- Two secrets-bearing paths exist and must stay out of git: the Razorpay key pair is read from the database table `gen_razorpay_key` (`utils/razorpay.py:42-68`), not from the environment; the FCM service account comes from `FCM_*` variables.
- CORS answers `*` for every origin, method and the headers `Content-Type, x-admin-id, x-role-id, Authorization` (`app.py:20-34`); restrict when the web portal URL is known (C-8). `Authorization` is already allowed, so adding a bearer token (P3-1) needs no CORS change.
- Token logging: device tokens are logged at info level (`auth_function.py:818`).
- Static assets carry the legacy brand (`static/*.png|webp`, `utils/email.py:726-729` support contact and link, `whatsapp_service.py:32` store package, `utils/email.py:209` invoice address, `gen_payment_functions.py:171, 179`): move to configuration (P2-10, owner items in the roadmap).
- **Billing integrity (NEW-16, P1, not called by the app but decisive for P1-13).** `POST /webhook/razorpay` (`core_functions/gen_webhook_functions.py:12-72`) verifies the signature over `json.dumps(parsed_payload)` instead of the raw request body (`:15-21`, likely never equal to the provider's), and on `payment.captured` marks the order `PAID` and inserts into `gen_payments` **without `org_id`** (`:53-60`; the column is `NOT NULL` in the dump), so the handler fails and rolls back today. If it were repaired as it stands it would create a worse bug: `verify_payment_process` returns "Payment already verified" for any order whose status is already `PAID` (`gen_payment_functions.py:51-52`) without activating the subscription, so a webhook arriving before the client's verify call would leave a paying customer on the old plan. Fix together: signature over the raw body, shared `activate_subscription(order)` used by both paths, idempotency keyed on "subscription exists for this order" (add `gen_orders.activated_at` or a `subscription_id` link) instead of on order status, `org_id` copied from the order. Test: webhook first then verify; verify first then webhook; duplicate webhook; invalid signature 400.
