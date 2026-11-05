# Staff management (`/addstaff`, `/profile-photo`) — backend context

Conventions: `file:LINE` under `C:\POC\pa_backend\Clockyn\`; "DDL:N" = line in the schema dump at the repo root (legacy-named .sql); `<legacy>_` = table prefix that carries the legacy product name (exact names in the DDL). FE = `C:\POC\pa_frontend\Clockyn`.

## 1. Scope & files

| File | Lines | Role | FE caller |
|---|---|---|---|
| `router/add_staff.py` | 243 | all `/addstaff/*` routes; 3 handlers contain inline SQL (`/count`, `/all-staff`, `/{id}/status`) | `src/api/staff.ts` (all), `features/org-structure/utils/employeeId.ts` |
| `core_functions/add_staff_function.py` | 929 | models + add/update/delete/permanent-delete, option lists, emp-id helpers | same |
| `router/profile_photo.py` | 21 | `GET/POST /profile-photo/{user_id}` | `src/api/auth.ts:143`, `src/api/face.ts:90` |
| `ml/s3.py:154-187, 356-398` | — | profile photo save/get; `delete_employee_s3_assets` | — |
| `core_functions/whatsapp_service.py` | 100+ | onboarding WhatsApp | (side effect of add) |

`app.py:40` mounts the router (no auth dependency). Router-order facts: `/count/{org_id}`, `/all-staff/{org_id}`, `/branch-info/{id}`, `/department-info/{id}` are declared before `/{user_id}` (`add_staff.py:79-169`) so they are not shadowed. `GET /addstaff/managers/` and `/shifts/` are declared **with** a trailing slash only (`:54, :63`); without it the request falls through to `/{user_id}` and returns 400 "Invalid user ID format". `/branches` and `/departments` accept both forms (`:36-37, 45-46`).

**Identity convention:** in this router the parameter named `user_id` is the **organisation id** (`AddStaffRequest.user_id` description, `add_staff_function.py:18`; `org_id = data.user_id`, `:285`); only `GET/PUT/DELETE /{user_id}` and `staff-by-role` use a real user id.

## 2. Data model touched

| Table (DDL) | Columns | Meaning / rules |
|---|---|---|
| `users` (991) | `user_id` (UUID, unique), `org_id` (admin only, unique idx), `name`≤50, `emp_id`≤50 (no unique index), `branch_id` int, `role_id` (1 admin, 2 manager, 3 staff), `dept_id`, **`parent_id`** (org_id for normal staff; manager's `user_id` when a manager was chosen — see D3), `mobile_number` NOT NULL, `email`≤50 NOT NULL, `status` (1 active, 0 inactive/soft-deleted), `is_field_staff` 0/1, `push_token` (`''` on create), timestamps | soft delete = `status 0`; hard delete via `/permanent` |
| `users_additional_info` (1032) | `user_id` (non-unique idx), `acc_no, ifsc_code, bank_name, bank_branch, dob, gender, blood_group, address, emergency_contact_no` (all NOT NULL except dob/blood_group; blanks stored as `''`), `shift_id` (NULL or 0 = no shift) | created with the user; update does `UPDATE` only |
| `branch_info` (160) / `dept_info` (267) | see org-structure doc | auto-create paths here (D6) |
| `<legacy>_subscription` (872) | `org_id, status('ACTIVE'), end_date, staff_limit int default 10 NOT NULL, created_at` | limit lookup `add_staff_function.py:292-297` |
| `free_trial` (328) | unique `org_id`, `end_date`, `is_active` | trials have no row in the subscription table → unlimited (D5) |
| `manager_assignments` (670), `role_info` (921), `shift` (934) | read for option lists / role name | |
| Tables purged by `/permanent` | `leave_dates, requests, chat_activity, chat, group_members, announcement_recipients, attendance, payroll, odometer_readings, travel_bill_expenses, advance_details, assigned_incentives, assigned_wages, daily_hourly_wages, manager_assignments, <legacy>_manager_permissions, leads_assignment_info, staff_location_logs, activity_tracking, incentive_*_details, incentive_templates, users_additional_info, users` | `add_staff_function.py:739-772` |
| S3 | `employee_profile/{user_id}.jpg|png`, `known_faces/{user_id}/*`, punch photos and chat files harvested from rows | `ml/s3.py:154-187, 356-398` |

## 3. Endpoints

All unauthenticated; role/org identity is client-supplied (`backend_changes.md` P3-1, P3-14).

| Method path | Handler | Identity | Request | Success response | Errors |
|---|---|---|---|---|---|
| POST `/addstaff/` | `add_staff_route` add_staff.py:14 → `add_staff_function.py:279` | body `user_id` = org id | JSON `AddStaffRequest` (`:13-34`): required `name, emp_id, mobile_number, email, user_id`; optional `branch_name, dept_name, parent_id, gender, shift_id=1, is_field_staff=0, dob(YYYY-MM-DD), address, emergency_contact_no, blood_group, bank_name, bank_branch, acc_no, ifsc_code` | `{success:true,message:"Staff added successfully!",user_id}` | 400 missing core fields (`:281-282`); 403 `"Staff limit reached (c/l). Please upgrade your plan to add more staff."` (`:305-309`); 400 `"Employee ID, mobile number, or email already exists"` (`:323-324`); 400 invalid dob (`:410`); 500 "General department not found..." (`:387`); 500 "Failed to add staff" (also for auto-create branch, D6) |
| GET `/addstaff/next-emp-id?user_id&role_id` | add_staff.py:19 → `:876` | org id | both query required (`role_id` unused) | `{next_emp_id:"1001"}` | never errors (returns `"1001"` on any exception) |
| GET `/addstaff/validate-emp-id?user_id&role_id&emp_id` | add_staff.py:27 → `:906` | org id | all required | `{is_available:bool}` | fails open `true` on exception |
| GET `/addstaff/branches` (also `/branches/`) | add_staff.py:36 → `:95` | `user_id` org id (role 2: the manager's own id) , `role_id` | query | `[{branch_id:str,branch_name,branch_address,status}]` active only; role 2 → via `manager_assignments` | 400 for `undefined`/`null` id; 500 |
| GET `/addstaff/departments` (also `/departments/`) | add_staff.py:45 → `:130` | same | | `[{dept_id,dept_name,status}]` active only | same |
| GET `/addstaff/managers/?user_id&role_id&dept_id?` | add_staff.py:54 → `:165` | org id | optional `dept_id` | `[{user_id,name,emp_id,dept_id}]` role 2, active, `parent_id = org`, optional `users.dept_id` filter (not `manager_assignments`) | 500 |
| GET `/addstaff/shifts/?user_id&role_id` | add_staff.py:63 → `:858` | org id | | `[{id,name}]` active shifts, or `[{"id":1,"name":"Default Shift"}]` when none/any error | never errors |
| GET `/addstaff/staff-by-role?user_id&target_role_id?` | add_staff.py:71 → `:805` | requesting user | | `[{user_id,name,emp_id,role_id}]` | 404 "User not found"; 500 |
| GET `/addstaff/count/{org_id}` | add_staff.py:79 | org id | | `{success,count}` active role 2+3 with `parent_id = org` (`:88`) | 500 `detail=str(e)` |
| GET `/addstaff/all-staff/{org_id}?branch_id&dept_id` | add_staff.py:100 | org id | optional filters; `branch_id` int-cast (`:126`) | `{success,data:[{user_id,name,emp_id,branch_id,dept_id,branch_name,dept_name,created_at}]}` active, any role under `parent_id = org` (`:120`) | 500 (non-numeric branch_id) |
| GET `/addstaff/branch-info/{user_id}?role_id` | add_staff.py:145 → `:192` | user id (UUID validated) | | `{branch_id:str,branch_name}` | 400 invalid uuid; 404; 500 (D7) |
| GET `/addstaff/department-info/{user_id}?role_id` | add_staff.py:157 → `:223` | | | `{dept_id,dept_name}` | 400/404/500 |
| GET `/addstaff/{user_id}` | add_staff.py:169 → `:464` | user id | | `StaffDetailResponse` (`:70-93`): `user_id,name,emp_id,mobile_number,email,dept_name,branch_name,role_name,status,is_field_staff,dob,gender,blood_group,address,emergency_contact_no,acc_no,ifsc_code,bank_name,bank_branch,shift_id,profile_photo_url` — **no `branch_id`, `dept_id`, `role_id`, `parent_id`** | 400 uuid; 404 "Staff member not found" (any status, any role); 500 |
| PUT `/addstaff/{user_id}` | add_staff.py:178 → `:511` | user id + body `user_id` (org id) | same body as POST (required fields enforced by model) | `{success,message:"Staff updated successfully!"}` | 400; 404; 500 |
| DELETE `/addstaff/{user_id}` | add_staff.py:190 → `:626` | | | `{success,message}` (sets `status=0`) | 404 |
| DELETE `/addstaff/{user_id}/permanent?requested_by_role_id=` | add_staff.py:200 → `:664` | **role from query** | | `{success,message:"<name> has been permanently deleted."}` | 403 role != 1 (`:697-698`) or target role not 2/3 (`:704-705`); 404; 409 manager has subordinates (`:707-715`); 500 |
| PUT `/addstaff/{user_id}/status?status=0|1` | add_staff.py:213 | | `status` 0/1 | `{success,message:"Staff activated|deactivated successfully"}` | 400; 404 |
| GET `/profile-photo/{user_id}` | profile_photo.py:9 | | | `{success,photo_url:str|null}` (7-day presigned URL) | never errors |
| POST `/profile-photo/{user_id}` | profile_photo.py:16 | | multipart `photo` | `{success,photo_url}` | unhandled S3 error → 500 |

## 4. Business logic

**POST /addstaff/** (`add_staff_function.py:279-462`), one transaction, rollback on any error (`:452-459`):
1. Require `name, emp_id, mobile_number, user_id` truthy (`:281`). `org_id = data.user_id`.
2. **Staff limit** (`:291-310`): latest `<legacy>_subscription` with `status='ACTIVE' AND end_date >= date.today()` (server-local date) for the org; if found, `limit = staff_limit or 10`; `current = COUNT(users WHERE parent_id = org AND role_id IN (2,3) AND status = 1)`; `current >= limit` → 403. No subscription row (trial, expired, none) → no limit.
3. **Uniqueness** (`:311-324`): `(emp_id = X OR mobile_number = Y [OR email = Z]) AND parent_id = org` — only among users whose `parent_id` is the org (inactive included; case-sensitive); the message does not say which field clashed. Email check skipped when blank.
4. **Branch** (`:329-353`): when `branch_name` given: lookup by exact name, `org_id`, `status=1`; missing → `INSERT branch_info (...,"Default Address",...)` with `branch_id = MAX(branch_id WHERE org_id)+1` — this INSERT omits NOT-NULL `gps_location` → fails (D6). No branch name → `branch_id NULL`.
5. **Department** (`:355-387`): name given → lookup by exact name/org/`status=1`, else create new dept (uuid). None given/none found → **must** find the dept named `General` else 500.
6. `parent_id = data.parent_id or org_id` (`:393`); `role_id` hard-coded 3 (`:395-401`); `status 1`, `push_token ''`, `created_at/updated_at = get_utc_now()`.
7. dob parsed `%Y-%m-%d` (`:404-410`; the 400 is raised after the user INSERT but inside the transaction → rolled back).
8. INSERT `users_additional_info` with blanks as `''` and **`shift_id = data.shift_id if not None else 1`** (`:421`): the FE sends `0` when no shift (`mappers/staff.ts`), other callers default to `1` — an id that may belong to another org or not exist (D8). No shift/org validation.
9. COMMIT, then best-effort WhatsApp (`:426-444`) — see §5. Failure never affects the response.

**PUT /addstaff/{id}** (`:511-624`): load `parent_id` of the target (no org/role check) → `parent_id = data.parent_id or existing` (`:527`); resolve branch/dept by name inside `data.user_id` org **without `status=1` filter**; unlike add, a missing branch/dept is created with invalid columns (D6); `branch_id` and `dept_id` start as `None` (`:529-530`) so **omitting `branch_name`/`dept_name` writes NULL** to both (`:584`); `UPDATE users SET ..., role_id = 3 (literal), parent_id, ...` (`:579-586`) → managers are demoted; no uniqueness check on emp_id/mobile/email; `UPDATE users_additional_info` overwrites every listed column with request values or blanks (`:597-607`), `shift_id` falls back to `1` when null. `status` is not touched. Mutating `role_id` here does **not** delete `manager_assignments`/permissions, leaving a role-3 user with manager rows.

**Soft delete** (`:626-662`): `UPDATE users SET status = 0` (no role check — admin can be deactivated; keeps `manager_assignments`, permissions, tokens). **Status toggle** (`add_staff.py:213-244`): sets `status` with an IST timestamp (the rest of the module uses UTC); no org/role/limit check → reactivation bypasses the staff limit (D5).

**Permanent delete** (`:664-802`): admin-only by query param; targets role 2/3 only; manager with `COUNT(users WHERE parent_id = target)` > 0 → 409; harvest S3 URLs (attendance punch photos, chat files), delete ~28 tables in order, commit, then best-effort S3 cleanup (`delete_employee_s3_assets`, profile photo both extensions, face prefix) and `EMBEDDING_CACHE.pop` (`:786-797`). Support tickets, chat groups created, `action_by` references intentionally left.

**Employee id helpers**: `next-emp-id` = max numeric `emp_id` (regex `^[0-9]+$`, ordered by `CAST(... AS INTEGER)`) among users with `parent_id = org` (all statuses) + 1, default `1001` (`:882-897`). A numeric id above int range makes the cast raise → handler returns `"1001"` (collision). `validate-emp-id` counts `emp_id = X AND parent_id = org` (`:913-920`). Both exclude users whose `parent_id` is a manager and the admin row. FE then loops `validate` while incrementing (`employeeId.ts`) and falls back to `'1000'`/available on errors (`staff.ts:149-170`).

**Option lists**: `get_branches/get_departments` for `role_id == 2` read `manager_assignments` of the manager, otherwise treat the passed id as org id (`:104-120, 139-155`). `get_managers` filters by `users.dept_id` of the manager (`:174-179`), not by `manager_assignments`. `get_staff_by_role` treats `org_id = requesting_user_id` for role 1 (`:824`) which is wrong whenever the admin's user id ≠ org id (always for orgs created by registration) → returns `[]`; FE does not call it.

**Photo upload/delete**: staff create does not accept a photo; the FE creates the user then calls `POST /profile-photo/{user_id}` (field `photo`) (`src/api/face.ts:90`, also invoked by face enrolment `face_attendance_functions.py:101-102`). Server writes `employee_profile/{user_id}.jpg` with `ContentType image/jpeg` whatever the upload (`ml/s3.py:154-167`); `GET` checks `.jpg` then `.png` (`:170-187`). There is **no delete endpoint** for the profile photo; only permanent delete removes it (`ml/s3.py:379-384`).

## 5. Side effects & integrations

| Trigger | Integration | Payload / notes |
|---|---|---|
| after commit in `POST /addstaff/` | WhatsApp via MSG91 outbound template API (`whatsapp_service.py:62-100`) | recipient `91`+digits of `mobile_number` if not already prefixed (`:23-26`); template components `body_var_1 = employee name`, `body_var_2 = org label`, `body_var_3 = app store link` (`:44-52`). Org label comes from `SELECT name FROM users WHERE user_id = <org_id>` (`add_staff_function.py:429-431`): `org_id` is not a user id for registration-created orgs (D9) so the label is the fallback `'Your Organization'` (UNVERIFIED for legacy orgs whose org id equals the admin user id). Default template name and store link carry the legacy brand (`whatsapp_service.py:29-33`). Not configured → logged and skipped (`:75-80`). |
| profile photo upload | S3 `put_object` (`ml/s3.py:154`) | |
| permanent delete | S3 deletes + face-embedding cache eviction | |
| none | no push, no WebSocket, no e-mail on add/update/status | |

### Changes (WP6 remainder: staff and company data lifecycle, migration 034; decisions D-10, D-14)

- **`users.manager_id varchar(36) NULL` + `idx_users_manager_id`** (DDL and `migrations/034_users_manager_id.sql`). `parent_id` is now ALWAYS the organisation: `POST /addstaff/` stores the chosen reporting manager (`parent_id` of the request body, the organisation id meaning "none") in `manager_id`, after checking it is an active role-2 user of the same organisation (400 `The selected manager was not found in this organisation.` otherwise). `PUT /addstaff/{id}` never rewrites `parent_id`, updates `manager_id` only when the body carries a manager (the organisation id clears it) and still never writes `role_id` (QW-6 verified: the UPDATE has no `role_id`). Migration 034 backfills `manager_id = parent_id` only for role 2/3 rows whose `parent_id` is not an `org_info.org_id` but the `user_id` of another user (rows created by the old code path, defect D3); their `parent_id` is NOT rewritten (a commented optional UPDATE is in the migration). Rows that cannot be classified stay NULL.
- **Login of manager-owned staff (D3).** `employee_login` and `verify_device_token` resolve the organisation with `o.org_id = u.parent_id OR o.org_id = (SELECT m.parent_id FROM users m WHERE m.user_id = u.manager_id)`, so a legacy row (`parent_id` = manager) still reaches its organisation; new rows match on `parent_id`.
- **Staff limit on reactivation (P2-11).** `PUT /addstaff/{id}/status?status=1` now calls the same `assert_staff_capacity` as add-staff when the user was not active (0, or a soft-deleted 2): active-plan limit, trial limit 10, 403 `Staff limit reached (c/l). Please upgrade your plan to add more staff.`; the organisation row is locked (`FOR UPDATE`) first. The count is active role 2/3 users with `parent_id = org` or `manager_id` of a manager of the org (legacy rows counted). Deactivation never checks the limit. The handler logic moved to `set_staff_status` (route unchanged).
- **Soft delete and retention purge (D-14).** `DELETE /addstaff/{id}` stays the soft delete (`status = 2`, QW-18). `core_functions/data_lifecycle.py`: the table list of `permanently_delete_staff` is now ONE tuple (`STAFF_STEPS`, same statements and order, plus `user_push_tokens` and `face_attempt_log`), used by both `permanently_delete_staff` (which also un-assigns staff whose `manager_id` is the deleted manager) and `purge_deleted_staff(cursor, days=30, dry_run=True)`. The purge selects `status = 2`, role 2/3, `updated_at < now - days`, name not yet `Deleted user`; per staff member (one transaction each, rollback and continue on failure): a manager with active/inactive staff still linked by `parent_id` is skipped; staff WITH payroll history (`salary_payments` or `payroll` rows) are anonymised in place (private data deleted, punch-photo references nulled, bank/identity details blanked, name `Deleted user`, mobile `deleted-<user_id>`, e-mail `del-<user_id>@x.invalid`, tokens cleared, `auth_epoch + 1`); everyone else is fully deleted. S3 objects (face data, profile photo incl. HEIC, punch photos, chat files) are removed after the commit via the monkeypatchable `delete_s3_assets`. Any later write to a status-2 row (it bumps `updated_at`) restarts its retention clock.
- **Operator route** `POST /admin/lifecycle/purge-deleted?days=30&dry_run=true` (`router/lifecycle.py`, operator class in `auth_policy.OPERATOR_RULES`, guard `require_operator_super`: X-Operator-Key or a role-1 platform login, enforced even while `REQUIRE_AUTH` is off). Defaults to a dry run; answers `{success, dry_run, days, cutoff, eligible, delete, anonymise, skipped:[{user_id,reason}], purged:[{user_id,action}], failed:[user_id]}`; `days` outside 1..3650 is 400; at most 500 staff per call.
- **Account deletion (flag `ACCOUNT_DELETE_ANONYMISE`, default off).** `DELETE /company/delete-account/{id}`: off = the existing hard delete, now ONE transaction (the pending-request update used to run after the first commit). On = `anonymise_account`: every user of the owner's organisations (including legacy manager-owned rows), then the owner, is anonymised in place; leads' customer name/mobile/address/remarks blanked; `org_info` (name, address, GST, coordinates, state...) and `company_additional_info` blanked; `account_deletion_requests` closed and its mobile/e-mail removed; billing records untouched; one transaction, S3 removed after the commit. `POST /company/request-deletion/{id}` only files the request and needs no change.
- **Manager scope helper** (`core_functions/scope.py`, flag `ENFORCE_MANAGER_SCOPE`): see `managers_shifts_holidays.md` section 3.1.

## 6. Config & env vars

`MSG91_WHATSAPP_AUTH_KEY`, `MSG91_WHATSAPP_NUMBER`, `MSG91_WHATSAPP_TEMPLATE`, `APP_PLAY_STORE_LINK`, `DB_SCHEMA`, `S3_BUCKET_NAME`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; WP6: `ENFORCE_MANAGER_SCOPE`, `ACCOUNT_DELETE_ANONYMISE` (both default off).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | `add_staff_function.py:579-586` (`:584` literal `3`) | **Update demotes managers**: every `PUT /addstaff/{id}` writes `role_id = 3` (P1-4). Manager rows in `manager_assignments` and permissions stay behind. |
| D2 | High | `:529-530, :584` | `PUT` with no `branch_name`/`dept_name` sets `branch_id` and `dept_id` to NULL; blanks overwrite stored dob/address/bank fields and `shift_id` becomes `1` when omitted (`:606`). Edit is a full replace, not a patch. |
| D3 | High | `:393`; `auth_function.py:1067-1073`; many `parent_id = org` queries (table below) | **Manager-owned staff (`parent_id = manager user_id`) become invisible and cannot log in.** Employee login joins `org_info ON users.parent_id = org_info.org_id`, so such a user fails the join; every org-wide query filters `parent_id = org_id`. |
| D4 | High | `:296-308`, `add_staff.py:88` | Staff-limit count also uses `parent_id = org`, so manager-owned staff are not counted and the limit is under-enforced. |
| D5 | Med | `:292-310`; `add_staff.py:213-244` | Limit only enforced when an ACTIVE, unexpired subscription row exists (trial/expired/no plan = unlimited); not enforced on reactivation (`status 0→1`); race between COUNT and INSERT (no lock); `date.today()` is server-local; `int(staff_limit or 10)` treats 0 as 10. |
| D6 | High | `:347-352` and `:550-555` | Auto-create branch: `add` omits NOT-NULL `gps_location` (DDL:160-170) and `update` names non-existent columns `branch_latitude/branch_longitude` → both raise → generic 500. Branch typo in the staff form cannot create a branch; update additionally uses per-org `MAX+1` for `branch_id` (collisions, see org-structure D1) and no `status=1` filter on lookups. |
| D7 | Med | `:213` (`BranchInfoResponse(**result)`) | `BranchInfoResponse.branch_id: str` (`:37`) is fed an int (Pydantic 2.12 does not coerce) → 500 (UNVERIFIED: static). |
| D8 | Med | `:23, :421` | `shift_id` default `1` (model) and in SQL; no check that the shift exists/belongs to the org/is active; FE sends `0` for "no shift" → stored as 0 (readers treat `NULL`/`<=0` as none: `home_functions.py:186`). |
| D9 | Low | `:429-431` | WhatsApp org label query is wrong (uses user name, wrong key) → generic label. |
| D10 | Med | `:311-324` | Uniqueness is only per `parent_id = org`: ignores manager-owned staff; mobile uniqueness across orgs not enforced although login resolves by mobile with `LIMIT 1` (`auth_function.py:1067-1076`); staff may reuse the admin's mobile (admin row has `parent_id NULL`) and then be blocked by "This is an admin account" at login. `emp_id` has no DB unique index (race). |
| D11 | Med | `:876-904, :906-927` | Fail-open on every error (`"1001"` / `true`); numeric-id overflow → duplicate suggestion; manager-owned staff ignored; `role_id` param unused. |
| D12 | Med | `add_staff.py:200-210`, `:213-244`; `:626` | Authorisation: permanent delete trusts `requested_by_role_id` from the query string; status toggle/soft delete accept any caller, can deactivate an admin; no org check on target (P3-14, P3-9). |
| D13 | Med | `:464-509` | `GET /addstaff/{id}` returns PII/bank fields to any caller, any status/role, and lacks `branch_id` (P1-2). Branch/dept names are joined with `org_id = u.parent_id` so manager-owned staff show NULL names. |
| D14 | Low | `add_staff.py:126` | `int(branch_id)` ValueError → 500; `/all-staff` includes managers and uses `SELECT DISTINCT` needlessly. |
| D15 | Med | `add_staff.py:213-244` vs module | Status toggle writes an IST-aware timestamp; the other writers use UTC. |
| D16 | Med | `profile_photo.py`, `ml/s3.py:154` | No auth, no type/size validation; any caller can overwrite any user's photo; always stored as `.jpg` (a stale `.png` is shadowed on read); no delete. |
| D17 | Low | `:664-802` | Permanent delete has no org check (an admin of org A can delete a user of org B by id), 28 sequential deletes in one transaction, S3 cleanup best-effort. |
| D18 | Low | `:858-874` | `get_shift_options` swallows all errors and returns a fake "Default Shift" id 1. |

Queries that hide manager-owned staff (all filter `parent_id = org_id`): `add_staff_function.py:175,178,301,314,319,829,832,838,883,914`; `add_staff.py:88,120`; `branch_functions.py:232,281,320`; `manager_functions.py:68,96,178`; `home_functions.py:135,159,184,322,409,431,501,582,659,733,811` (and the shift joins `s.org_id = u.parent_id` at `:183,650`, branch/dept joins `b.org_id = u.parent_id` at `:312-313,491-492,579-580,647-648,723-724,801-802`); `daily_hourly_functions.py:129-131,153-155`; login `auth_function.py:1071`. Queries that **do** include them: department counts (`dept_functions.py:25-27`, join by dept only), shift delete (`shift_functions.py:204-215`, by shift id).

## 8. Clockyn requirements

Cross-refs: `backend_changes.md` P1-2, P1-4, P1-17, P2-11, P3-8, P3-14, DD-17, DD-42, DD-53.

### R1 Staff-limit enforcement on `POST /addstaff/` (P2-11, DD-17)
- **Current:** `add_staff_function.py:291-310` (see D4/D5).
- **Required:** (a) one shared helper `_assert_staff_capacity(cursor, org_id, exclude_user_id=None)` used by add **and** reactivation; (b) count = active role 2/3 users of the org including manager-owned (use R4 first or `parent_id = org OR parent_id IN (managers of org)`); (c) trial orgs: look up `free_trial (org_id, is_active, end_date >= today)` and apply a product-defined trial limit (DD-17; suggest 10 = the default of the subscription column); (d) compute "today" in IST (`datetime.now(IST).date()`), (e) take a row lock to prevent races: `SELECT ... FROM org_info WHERE org_id = %s FOR UPDATE` before counting; (f) return **403** `{"detail": "Staff limit reached (c/l). Please upgrade your plan to add more staff.", "staff_limit": l, "current": c}` (keep message prefix for the FE); (g) `GET /addstaff/count/{org}` should return `{success,count,staff_limit}` so the banner needs one call.
- **Files:** new helper in `add_staff_function.py`; call from `add_staff` (`:291`) and from `toggle_staff_status` (`add_staff.py:213`) when `status == 1` and the user is currently 0. **Compat:** keep status code and message start. **Acceptance:** at limit → add 403; deactivate one then add OK; reactivate at limit → 403; trial org at trial limit → 403; manager-owned staff counted; two concurrent adds at limit-1 → exactly one succeeds.

### R2 Emp-id generation / validation
- **Required:** make `next-emp-id` and `validate-emp-id` consider every user of the org (R4: `parent_id = org`), make numeric parsing safe (`emp_id ~ '^[0-9]{1,9}$'`), return 503/500 instead of a made-up id on DB errors (FE already handles errors: `staff.ts:149-170`), add a DB unique index `CREATE UNIQUE INDEX users_org_emp_uq ON users (parent_id, emp_id) WHERE parent_id IS NOT NULL` (after de-duplicating) and map the violation to 400 `"Employee ID already exists"`; `add_staff` should return which field clashed (`"Employee ID already exists"` / `"Mobile number already exists"` / `"Email already exists"`) — FE shows server text. Also enforce mobile uniqueness across orgs for active users (login resolves by mobile) if product agrees.
- **Acceptance:** suggested id never collides; ids ≥ 2^31 do not produce 1001; duplicate emp-id → 400 naming the field.

### R3 Staff update must not demote managers and must patch (P1-4, DD-53)
- **Current:** D1, D2.
- **Required:** remove `role_id` and (unless R4) `parent_id` from the UPDATE (`:579-586`); scope the target: `WHERE user_id = %s AND parent_id = %s` (org from `data.user_id`) → 404 otherwise; keep `branch_id`/`dept_id` when the names are omitted (`COALESCE`/load existing); lookups with `status = 1`; duplicate check excluding self (emp_id/mobile/email within org); additional info: UPDATE only provided fields (None = keep), INSERT the row if `rowcount == 0` (there is no unique key on `user_id`); `shift_id` validated (see R7); never default to 1.
- **Backward compat:** FE sends every field except blanks; blanks currently clear values — keep "empty string clears" for text fields but not for ids.
- **Acceptance:** edit a manager's phone → `role_id` stays 2, assignments intact; omit `branch_name` → branch unchanged; edit to another staff's emp_id → 400.

### R4 `parent_id` / manager scoping (P1-17, DD-42)
- **Current:** `parent_id` means org for normal staff and a manager's id when `AddStaffRequest.parent_id` is sent (D3); FE sends it when the manager picker is used (`mappers/staff.ts:153`).
- **Required (recommended):** separate "org" from "reports to": `ALTER TABLE users ADD COLUMN manager_id varchar(36) NULL; CREATE INDEX idx_users_manager_id ON users(manager_id);` Backfill: `UPDATE users u SET manager_id = u.parent_id, parent_id = m.parent_id FROM users m WHERE u.parent_id = m.user_id AND u.parent_id NOT IN (SELECT org_id FROM org_info);`. Change `add_staff`/`update_staff` to write `manager_id = data.parent_id` (validated: role 2, same org, active) and always `parent_id = org_id`; accept an explicit `manager_id` field on `AddStaffRequest` and keep `parent_id` as deprecated alias (ignored unless it is a manager of the org). `get_manager_scope` consumers stay as they are. Add `manager_id` to `GET /addstaff/{id}`.
- **Immediate guard (no migration):** in `add_staff`/`update_staff` ignore `data.parent_id` unless it equals `org_id`; until migrated, treat existing manager-owned rows with the OR-subquery listed in R1(b).
- **Acceptance:** staff assigned to a manager can log in (`auth_function.py:1071` join succeeds), appears in `/home/all-staff`, `/branch/staff`, counts, and in the manager's `GET /managers/{id}/staff`.

### R5 `GET /addstaff/{id}` must return `branch_id` (+ more) (P1-2)
- **Current:** query `add_staff_function.py:473-484`, model `:70-93`.
- **Required:** add `u.branch_id` as `CAST(u.branch_id AS VARCHAR) AS branch_id`, `u.dept_id`, `u.role_id`, `u.parent_id`/`manager_id`, `b.branch_address`; add matching `Optional` fields to `StaffDetailResponse` (additive). Fix the joins to not depend on `parent_id` (join `branch_info b ON b.branch_id = u.branch_id AND b.org_id = (SELECT org_id FROM org_info o WHERE o.org_id = u.parent_id)` or use R4). Same additive change for `/home/staff-profile` and `/home/staff-detail` (home domain).
- **Acceptance:** response contains `branch_id` equal to `users.branch_id` as string; old clients unaffected.

### R6 Staff photo upload / delete (P3-8, DD-42)
- **Required:** `DELETE /profile-photo/{user_id}` → new `ml/s3.py:delete_employee_profile(user_id)` deleting both `employee_profile/{user_id}.jpg` and `.png` (pattern from `ml/s3.py:379-384`), response `{success:true}` (idempotent). Harden `POST`: accept only `image/jpeg|png`, ≤ 5 MB, store with the real extension and delete the other one; verify the target user exists (404). Optionally let `POST /addstaff/` accept an optional multipart photo — not required by the FE (two-call flow stays).
- **Acceptance:** after DELETE, `GET /profile-photo/{id}` → `photo_url:null` and `GET /addstaff/{id}` → `profile_photo_url: null`; second DELETE still 200; oversize/non-image → 400/415.

### R7 Shift on staff create/update
- Validate `shift_id`: `0`/null → store `NULL`; otherwise require `SELECT 1 FROM shift WHERE shift_id=%s AND org_id=%s AND status=1`, else 400 `"Invalid shift"`. Remove the default `1` (`AddStaffRequest.shift_id`, `:23`; SQL `:421`, `:606`).

### R8 Branch/department auto-create (D6)
- Either remove auto-create (return 400 "Branch not found" — FE only offers existing names) or fix the INSERTs: add `gps_location POINT(0,0)` and use `branch_id = MAX(branch_id)+1` over the whole table; delete the `branch_latitude/longitude` columns from `update_staff`. Prefer accepting `branch_id`/`dept_id` in the request (additive) and falling back to names.

### R9 Authorisation (P3-14)
- Derive `role_id` from the authenticated caller for `/permanent` and `/status`; require same-org on target; block deactivating role 1; on deactivating/demoting a manager, delete `manager_assignments` (see managers doc).

## 9. Test notes

- Fixtures: org O with admin, 2 branches, 2 depts + General, shift S1; subscription limit 3 (active) and a trial org without subscription; one manager M (role 2) with assignments; staff under M (after R4: `manager_id = M`).
- Critical cases: add at limit−1/at limit/after deactivate/after reactivate; trial org; duplicate emp-id/mobile/email (same org, other org, manager-owned); next-emp-id with 1001, 1002, numeric overflow, alphanumeric ids; update of manager keeps role; update omitting names keeps branch/dept; `GET /addstaff/{id}` fields; permanent delete of manager with subordinates (409) and of an admin (403); photo upload → get → delete → get; `/addstaff/managers` without trailing slash (400 today).
- No automated tests exist in the repo; use a scratch schema from the DDL.
