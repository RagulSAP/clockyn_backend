# Organisation structure (company / branch / department) — backend context

Conventions: line refs are `file:LINE` under `C:\POC\pa_backend\Clockyn\`. "DDL:N" = line N of the schema dump at the repo root (its file name carries the legacy product name). Tables with the prefix `<legacy>_` carry the legacy product name; the exact names are in the DDL. "FE" = frontend repo `C:\POC\pa_frontend\Clockyn`. Evidence tag `(UNVERIFIED: why)` where only static reasoning was possible.

## 1. Scope & files

| File | Lines | Role | FE caller |
|---|---|---|---|
| `router/company.py` | 108 | `/company/*` routes (also hosts 5 suggest-feature routes, owned by another domain) | `src/api/company.ts`, `src/api/system.ts` (suggest-feature, request-deletion) |
| `core_functions/company_functions.py` | 483 | company add/update/details/list/delete, account deletion request/hard delete | same |
| `router/branch.py` | 48 | `/branch/*` | `src/api/branches.ts` |
| `core_functions/branch_functions.py` | 374 | branch CRUD, branch staff list, reassign | same |
| `router/department.py` | 27 | `/department/*` | `src/api/departments.ts` |
| `core_functions/dept_functions.py` | 143 | department CRUD | same |
| `ml/s3.py:116-151` | — | org logo save/get (S3 key `org_logo/{org_id}.png`) | `src/api/company.ts:59` |

Mounting: `app.py` includes `branch` (`app.py:41`), `company` (`:42`), `department` (`:57`) routers without any auth dependency. Listing endpoints used by the FE for branches/departments are **not here**: `GET /home/branches`, `GET /home/departments` (`src/api/branches.ts:16`, `departments.ts:11`) belong to the home domain; the staff-form dropdowns `GET /addstaff/branches|departments` belong to `staff_management.md`.

Identity model (important for every rule below):
- `users.user_id` of an admin (role 1) is distinct from the organisation id. Registration generates two UUIDs (`core_functions/auth_function.py:381-382`); `org_info.parent_id = admin user_id` (`:387`), `users.org_id = org_id` for the admin row only (`:434`; DDL unique index `users_org_id_key` on `users.org_id`, so staff rows can never carry `org_id`).
- An admin can own N companies: `org_info.parent_id = user_id`. `add_company` creates an org with a fresh UUID (`company_functions.py:49-55`).
- Everything below the org is scoped by `org_id` strings, but **`branch_id` is a bare int that is not globally unique** (see §7 D1).

## 2. Data model touched

| Table (DDL line) | Columns used | Notes |
|---|---|---|
| `org_info` (755) | `org_id` (unique idx), `org_name`, `org_address`, `org_gst`, `org_latitude/longitude` (NOT NULL, always 0), `parent_id` (= admin user_id), `status` (never written by this domain), `state`, `district`, `pincode`, `created_at`, `updated_at` | no soft delete; company delete is a hard delete |
| `company_additional_info` (227) | `user_id`, `org_id`, `pan_number`, `company_website`, `cin_number`, `tan_number`, `msme_number`, `business_category`, `gst_number` | all NOT NULL except `gst_number`; code writes `''` for blanks |
| `branch_info` (160) | `id` (identity PK), `org_id`, `branch_id` int (NOT unique; idx only), `branch_name`, `branch_address`, `gps_location point NOT NULL` (x = longitude, y = latitude), `punch_radius` default 100, `status` (1 active; no code ever sets 0), `created_at`, `updated_at` | no `branch_latitude/longitude` columns exist (see D6) |
| `dept_info` (267) | `id`, `org_id`, `dept_id` varchar (UUID string), `dept_name`, `status` default 1, timestamps | no uniqueness constraint (dup check in code only, case-insensitive) |
| `users` (991) | `branch_id`, `dept_id`, `parent_id`, `role_id`, `status`, `name`, `emp_id`, `mobile_number`, `email` | read for counts / reassignment / cascade |
| `manager_assignments` (670) | `org_id,user_id,branch_id,dept_id` | **not cleaned** by branch/dept delete (D4) |
| `shift` (934) | `org_id` | deleted by company delete only |
| `account_deletion_requests` (2) | `request_id,user_id,org_name,mobile_number,email,status('pending'/'deleted'),requested_at,actioned_at` | |
| Tables deleted by `delete_user_account` | `users_additional_info, attendance, payroll, odometer_readings, travel_bill_expenses, incentive_*, requests, leave_dates, leads_*, shift, <legacy>_manager_permissions, firm_holidays, daily_hourly_wages, activity_tracking, pay_slip*, dept_info, branch_info, org_info, company_additional_info, users` | `company_functions.py:413-465` |

Enumerations: `branch_info.status` 1 = active; `users.status` 1 = active / 0 = inactive; `role_id` 1 admin, 2 manager, 3 staff (`role_info` DDL:921).

## 3. Endpoints

All routes unauthenticated (no dependency); the caller-supplied ids are trusted. Errors listed are what the code raises; see D-items for the HTTP-500 wrapping quirks.

### 3.1 Company (`/company`)

| Method path | Handler | Identity | Plan gate | Request | Success response | Errors |
|---|---|---|---|---|---|---|
| POST `/company/add` | `add_company_route` company.py:9 → `company_functions.py:31` | body `user_id` (admin) | none | JSON `AddCompanyRequest` (`:11-29`): `user_id`, `org_name`, `org_address` required; `branches: List[str]` required & non-empty; `departments: List[str]=[]`; `org_gst=''`; optional `gst_number,pan_number,company_website,cin_number,tan_number,msme_number,business_category,state,district,pincode:int,mobile_number,email` | `{success,message,user_id,org_id,org_name,role_id:1}` | 400 missing fields (`:33,36`); "User not found" 400 is **re-wrapped to 500 "Failed to add company"** (`:100-104`, no `except HTTPException`) |
| PUT `/company/update/{org_id}` | company.py:13 → `:232` | path `org_id` | none | same `AddCompanyRequest` (branches/departments may be `[]`) | `{success,message,org_id,org_name}` | 500 "Failed to update company". Unknown `org_id` → silent success (0 rows) |
| POST `/company/update-logo/{org_id}` | company.py:17 | path | none | multipart `logo` (file) | `{success,logo_url,message}` | 500 `detail=str(e)` |
| GET `/company/details/{org_id}` | company.py:26 → `:109` | path | none | — | `{success,data:{org_info.* + users.mobile_number,email + org_logo,logo_url + company_additional_info.*}}` | unknown org: 404 is **re-wrapped to 500 "Failed to get company details"** (`:145-148`) |
| GET `/company/list/{user_id}` | company.py:30 → `:153` | path `user_id` | none | — | `{success,data:[{org_id,org_name,org_address,org_gst,created_at}]}` oldest first | 500 |
| DELETE `/company/delete/{org_id}/{user_id}` | company.py:34 → `:175` | both path | none | — | `{success,message,remaining_companies:[...]}` | 400 "Cannot delete the only company" (`:187-188`); 500 |
| DELETE `/company/delete-account/{user_id}` | company.py:38 → `:408` | path | none | — | `{success,message}` | 500 "Failed to delete account" |
| POST `/company/request-deletion/{user_id}` | company.py:43 → `:336` | path | none | — | `{success,message}` (idempotent while a pending row exists) | 404 "User not found" (but see D8: any exception path raises `NameError`) |
| GET `/company/deletion-requests` | company.py:48 → `:376` | none | none | — | bare list `[{request_id,user_id,org_name,mobile,email,status,requested_at("%d %b %Y %H:%M")}]` pending only | 500 |
| GET/POST `/company/suggest-feature`, GET `/company/suggest-feature/mine`, GET `/company/suggest-feature/with-replies`, POST `/company/suggest-feature/{log_id}/reply` | company.py:61-109 | — | — | — | out of this doc's scope (suggest_feature_functions) | listed for route inventory only |

### 3.2 Branch (`/branch`)

| Method path | Handler | Request | Success | Errors |
|---|---|---|---|---|
| POST `/branch/add` | branch.py:10 → `branch_functions.py:56` | JSON `AddBranchRequest`: `branch_name`, `branch_address` (both required, non-blank), `branch_latitude=0.0`, `branch_longitude=0.0`, `punch_radius=100`, `org_id` | `{message,branch_id:str,branch_name}` | 400 blank name/address (`:59-66`); 400 "Branch name already exists" (`:73-80`, exact-case compare, includes inactive) ; 500 |
| PUT `/branch/update/{branch_id}` | branch.py:19 → `:150` | same body as add (`UpdateBranchRequest`), path `branch_id:str` | `{message,branch_id,branch_name}` | 400; 404 "Branch not found or access denied" (needs `status=1` and `org_id` match, `:166-173`); no duplicate-name check; 500 |
| POST `/branch/delete/{branch_id}?org_id=` | branch.py:24 → `:208` | path `branch_id:int`, query `org_id` required | `{message,branch_id,branch_name}` | 404 not found/other org; 400 "Cannot delete branch. N staff members are assigned to this branch." (`:237-238`); 500 |
| GET `/branch/staff/{branch_id}?org_id=` | branch.py:33 → `:263` | query `org_id` | bare list `[{user_id,name,emp_id,mobile_number,email,dept_name,current_branch_id:str,current_branch_name}]` (role != 1, status 1, `parent_id = org_id`) | 500 |
| GET `/branch/{branch_id}` | branch.py:41 → `:122` | path | `BranchResponse{branch_id,branch_name,branch_address,branch_latitude,branch_longitude,punch_radius,org_id,status}` | 404; see D7 (response model likely rejects int `branch_id`) |
| POST `/branch/reassign-staff` | branch.py:46 → `:298` | JSON `ReassignStaffRequest{staff_ids[],new_branch_id,org_id,delete_branch_id?}` | `{message,reassigned_count,branch_deleted}` | 400 missing; 404 "Target branch not found"; 500 |

Route order: `/staff/{branch_id}` (l.33) is registered before `/{branch_id}` (l.41), so no shadowing; `/reassign-staff` is POST only.

### 3.3 Department (`/department`)

| Method path | Handler | Request | Success | Errors |
|---|---|---|---|---|
| GET `/department/list/{org_id}` | department.py:10 → `dept_functions.py:13` | path | bare list `[{dept_id,dept_name,staff_count}]` (active role!=1 staff; **all** `dept_info.status` values listed) | 400; 500 (message includes exception text) |
| POST `/department/add` | department.py:15 → `:42` | JSON `DeptRequest{dept_name,org_id}` | `{success,message,dept_id,dept_name}` | 400 blank / "Department name already exists" (case-insensitive, `:52-57`); 500 |
| PUT `/department/update/{dept_id}` | department.py:20 → `:76` | JSON `DeptRequest` | `{success,message,dept_id,dept_name}` | 404 "Department not found"; 400 duplicate; 500 |
| POST `/department/delete/{dept_id}?org_id=` | department.py:25 → `:117` | query `org_id` | `{success,message,dept_id,dept_name}` | 404; 500 |

## 4. Business logic

**add_company** (`company_functions.py:31-107`): (1) validate `user_id, org_name, branches` truthy (`:32`). (2) `SELECT * FROM users WHERE user_id` must exist (`:43-46`). (3) `org_id = uuid4` (`:49`), INSERT `org_info` with `org_latitude/longitude = 0`, `parent_id = user_id`, `created_at = UTC now` (`:52-55`). (4) For each non-blank branch name: `branch_id = MAX(branch_id)+1` **over the whole table** (`:62`), INSERT with `gps_location = POINT(0,0)`, `branch_address=''`, `status=1` (`:64-67`). (5) Departments: one UUID per non-blank name; **no "General" default** here (registration adds it only when none supplied, `auth_function.py:413-428`; the FE sends `['General']` when empty, `companyForm.ts:164`). (6) Insert `company_additional_info` only if any extra field truthy (`:83-87`). (7) commit; role_id hard-coded `1` in response. Single transaction; any exception → rollback + 500.

**update_company** (`:232-333`): (1) UPDATE `org_info` name/address/gst/state/district/pincode (`:238-241`) — no existence/ownership check. (2) Look up owner `parent_id`; if mobile/email supplied UPDATE the **owner's `users`** row (3 variants, `:249-264`; no uniqueness check). (3) Upsert `company_additional_info`: if row exists overwrite **all** extras with `''` for omitted values (`:273-277`), else insert only if any provided. (4) **Branch sync**: if `branches` non-empty → `DELETE FROM branch_info WHERE org_id` then re-insert by name (`:285-297`): wipes GPS, address, radius and re-issues all branch_ids. The FE never sends branches on edit (`companyForm.ts:163`: `[]`), so this only fires for other clients. (5) **Department sync**: if `departments` non-empty and name-set differs from existing → `DELETE FROM dept_info WHERE org_id` and recreate with new UUIDs (`:300-314`) → every `users.dept_id`, `manager_assignments.dept_id` dangles. FE sends the departments list on edit (`companyForm.ts:164`), so any add/rename/remove on the edit form re-keys all departments.

**delete_company** (`:175-230`): count orgs with `parent_id = user_id`; `<= 1` → 400 "Cannot delete the only company". Then unconditionally (not restricted to org owned by `user_id`): `DELETE users WHERE parent_id = org_id`, `company_additional_info`, `dept_info`, `branch_info`, `shift` by `org_id` (`:191-203`); finally `DELETE org_info WHERE org_id AND parent_id = user_id` (`:206`). No cascade to attendance, payroll, requests, leads, chat, holidays, off-days, manager rows, S3 faces/photos/logo. Returns remaining companies oldest first (FE picks the next active org from this, `companyForm.ts:~185`).

**delete_user_account** (`:408-482`): for every org owned by `user_id`: per-staff delete of 8 tables (`:424-434`), then `users`, leads_*, incentive_*, shift, manager permissions, additional info, `firm_holidays`, wages, `activity_tracking`, payslips, dept, branch, `org_info` (`:436-460`); then the user's own `users_additional_info`, `company_additional_info`, `users` row (`:463-465`); commit; mark pending deletion request `deleted` in a second commit (`:469-473`). Called with an employee/manager id it only removes that user's own rows (loop over zero orgs), leaving attendance/requests (see `backend_changes.md` P1-9, DD-40). Does not delete: `org_holidays`, `org_off_days`, `manager_assignments`, `chat*`, `announcement*`, `staff_location_logs`, `assigned_*`, `advance_details`, `group_members`, S3 objects.

**add_branch** (`branch_functions.py:56-120`): trims; duplicate check `branch_name = %s AND org_id = %s` (exact case, any status); `branch_id = MAX(branch_id)+1` globally (`:83`); INSERT with `POINT(longitude, latitude)` (note order: x=lng) and `punch_radius`, `status 1`, timestamps `CURRENT_TIMESTAMP` (DB clock, not `get_utc_now`).

**delete_branch** (`:208-261`): verify branch `(branch_id, org_id, status=1)` (404 otherwise); `COUNT(users) WHERE branch_id = %s AND parent_id = org_id AND status=1` (**includes role 2 and 3, excludes role 1 admin whose `parent_id` is NULL, excludes inactive and manager-owned staff**, `:231-235`); >0 → 400; then `DELETE FROM branch_info WHERE branch_id = %s` — **no `org_id` predicate** (`:241-244`). Hard delete; leaves `manager_assignments`, inactive users' `branch_id`, admin's `branch_id`, `org_holidays.branch_name`.

**reassign_staff** (`:298-373`): target branch must be `(branch_id, org_id, status=1)`; `UPDATE users SET branch_id = new WHERE user_id IN (...) AND parent_id = org_id AND status = 1` (staff not under `org_id` as parent are silently skipped; `rowcount` returned). If `delete_branch_id` supplied: counts remaining active users in that branch **globally (no org filter)** `:331-335`; if 0 → `DELETE branch_info WHERE branch_id AND org_id` (this one is org-scoped, `:339-342`). Single commit.

**get_branch_staff** (`:263-296`): joins branch/dept with `org_id` filter; `role_id != 1`, `status = 1`, `parent_id = org_id` → manager-owned staff are invisible.

**Departments** (`dept_functions.py`): list counts `LEFT JOIN users u ON u.dept_id = d.dept_id AND u.status=1 AND u.role_id != 1` (`:22-30`); add/update dedupe by `LOWER(dept_name)` per org; delete: `UPDATE users SET dept_id = NULL WHERE dept_id = %s` then `DELETE dept_info WHERE dept_id` (`:132-133`) — never blocks, no `org_id` in either statement (dept ids are UUIDs so safe in practice), does not touch `manager_assignments`, holidays or the mandatory "General" fallback.

## 5. Side effects & integrations

| Flow | Effect |
|---|---|
| `update-logo` | S3 `put_object` key `org_logo/{org_id}.png`, `ContentType: image/png` regardless of the uploaded type, returns 7-day presigned URL (`ml/s3.py:116-131`) |
| `company/details` | S3 `head_object` + presign (`ml/s3.py:134-151`); returns `null` logo when missing |
| none of company/branch/department | sends push, e-mail, WhatsApp or WebSocket events |

## 6. Config & env vars

`DB_SCHEMA` (search_path per connection, `core_functions/db_function.py:59`), `S3_BUCKET_NAME`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (`ml/s3.py:10-14`; a secret is read from env there, not hard-coded).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| D1 | High | `branch_functions.py:241-244`; `auth_function.py:393-397`; `add_staff_function.py:342` | **Branch delete is not org-scoped and `branch_id` is not unique across orgs.** Registration numbers each org's branches `1..n` (`enumerate(..., start=1)`), `add_staff` auto-create uses per-org `MAX+1`, while `add_branch`/`add_company` use global `MAX+1`. `branch_info.branch_id` has only a non-unique index (DDL:160-178). Deleting branch 1 of org A therefore deletes branch 1 of **every** org (after A's own staff-count check passed). Also unscoped joins on `branch_id` alone (e.g. `holiday_functions.py:149`, `branch_functions.py:132` `get_branch_by_id`) can return another org's branch. |
| D2 | High | `company_functions.py:191-203` | `delete_company` cascades are keyed only by `org_id`; ownership (`parent_id = user_id`) is checked only on the last statement. A caller who owns ≥2 orgs can pass any other org's id: that org's staff, branches, departments, shifts are deleted while its `org_info` row survives. Nothing is authenticated. |
| D3 | High | `company_functions.py:285-297, 300-314` | `update_company` destroys and re-keys branches (if `branches` sent) and departments (if the name set differs) — orphans `users.branch_id/dept_id`, `manager_assignments`, wipes GPS/radius. FE edit form triggers the department part on every change. |
| D4 | Med | `branch_functions.py:241`, `dept_functions.py:132-133` | Branch/department delete leaves `manager_assignments` rows (managers lose scope silently), holiday scope names, inactive/admin users' `branch_id`. Department delete never blocks (staff become dept-less; the next `POST /addstaff/` without dept requires a "General" dept — `add_staff_function.py:376-387` — which can have been deleted/renamed). |
| D5 | Med | `branch_functions.py:231-238` | Staff-count guard misses: inactive staff, admin, and staff whose `parent_id` is a manager (see staff doc). `reassign_staff` remaining-count check (`:331-335`) is global across orgs. |
| D6 | Med | `add_staff_function.py:347-352` / `:550-555` | Auto-create branch inserts omit NOT-NULL `gps_location` (add) or reference non-existent `branch_latitude/branch_longitude` columns (update) → 500. Documented in staff doc; relevant because a branch name typo cannot create a branch. |
| D7 | Med | `branch_functions.py:140` (+ `add_staff_function.py:213`) | `BranchResponse(**row)` with `branch_id: str` (`:31`) is fed an int from psycopg2; Pydantic 2.12 (`requirements.txt`) does not coerce int→str → ValidationError swallowed as 500 "Failed to get branch" (UNVERIFIED: static reading, not executed). FE `getBranch` falls back to the list. Same for `get_branch_info` (`BranchInfoResponse.branch_id: str`, row int). |
| D8 | Med | `company_functions.py:369,401` | `logger` is never defined/imported in this module → `NameError` inside the `except` blocks of `request_account_deletion` / `get_deletion_requests` (masks the real error; response is still a 500). |
| D9 | Med | `company_functions.py:100-104, 145-148` | HTTPExceptions raised inside `try` (400 "User not found", 404 "Company not found") are converted to HTTP 500 (no `except HTTPException: raise`); `backend_changes.md` P2-1. |
| D10 | Med | `company_functions.py:238-241` | `update_company` on unknown/foreign `org_id` returns success; any caller can rewrite any company and its owner's mobile/e-mail (`:249-264`, also no uniqueness check on the user's mobile/e-mail). |
| D11 | Low | `branch_functions.py:73-80` | Duplicate-branch check is exact-case and includes `status != 1`; `update_branch` has no duplicate check; department checks are case-insensitive (inconsistent). |
| D12 | Low | `branch_functions.py:89,178` vs rest | Branch timestamps use `CURRENT_TIMESTAMP` (DB session tz) rather than `get_utc_now()`. |
| D13 | Low | `company.py:17-24` | Logo upload: no type/size validation, bytes always stored as `.png`; any caller can overwrite any org logo; no delete endpoint. |
| D14 | Low | `company_functions.py:408-482` | Hard delete leaves orphan rows (see §4) and S3 objects; two commits (deletion-request status update can fail after data is gone). |
| D15 | Low | `branch.py:30` | `print` of request params in delete handler. |

## 8. Clockyn requirements

Cross-refs: `backend_changes.md` P1-9 (delete semantics), P1-17 (manager scoping), P3-1, P3-8 (photos), P3-9, P2-1, DD-38/41/43/45.

### R1 Branch delete rules & org scoping (P1-9; DD-43)
- **Current:** guard counts only `status=1` users with `parent_id = org_id`; delete statement ignores `org_id` (D1, D5).
- **Required:** (a) always scope: `DELETE FROM branch_info WHERE branch_id = %s AND org_id = %s`; (b) guard counts **all** users (any status) of the org with that `branch_id` whose `branch_id` still points there — block for active, auto-null for inactive (`UPDATE users SET branch_id = NULL WHERE branch_id=%s AND parent_id=%s AND status=0`) before delete; (c) refuse if it is the org's last active branch; (d) delete `manager_assignments WHERE org_id=%s AND branch_id=%s` in the same transaction; (e) return 409 (not 400) with `{"detail": "...", "staff_count": n}` when staff exist.
- **Implementation outline:** `branch_functions.py:delete_branch` — rewrite the count query to `... WHERE branch_id=%s AND (parent_id=%s OR parent_id IN (SELECT user_id FROM users WHERE parent_id=%s AND role_id=2))` until the `manager_id` migration in `staff_management.md` §8 R4 lands, then plain `parent_id=%s`. Same scoping fix in `reassign_staff` `:331-335`.
- **Fix id collisions (prerequisite):** migration `ALTER TABLE branch_info ADD CONSTRAINT branch_info_org_branch_uq UNIQUE (org_id, branch_id);` after de-duplicating; change `auth_function.py:393-397`, `add_staff_function.py:342` to allocate `COALESCE(MAX(branch_id),0)+1` over the **whole table** (same as `add_branch`) or switch to a sequence (`CREATE SEQUENCE branch_id_seq`, `setval` to current max). Every query joining `branch_info` on `branch_id` alone must add `AND b.org_id = ...` (e.g. `holiday_functions.py:149`, `branch_functions.py:131-133`, `add_staff_function.py:204`, `:111`).
- **Compat:** FE already treats 400/404 as errors via message; keep key `detail`. Keep `POST /branch/delete/{id}` (not DELETE) since the FE calls it (`branches.ts:45`).
- **Acceptance:** two orgs each with branch_id 1 → deleting org A's branch 1 leaves org B's intact; branch with only inactive staff deletes and nulls their `branch_id`; branch with a manager-owned active staff is refused; deleting removes the manager's assignment for it.

### R2 Department delete not blocking (DD-43)
- **Current:** silently nulls `users.dept_id` (`dept_functions.py:132`); no check on managers/holidays/General.
- **Required:** keep non-blocking for the FE (decision DD-43 pending) but (a) scope statements by `org_id` (`UPDATE users SET dept_id=NULL WHERE dept_id=%s AND parent_id=%s` — plus manager-owned staff until R4), (b) delete `manager_assignments` rows with that `dept_id`, (c) refuse deletion of the default "General" department (400), (d) optionally accept `?reassign_to=<dept_id>` to move staff instead of nulling; (e) return `{"affected_staff": n}`.
- **Files:** `dept_functions.py:117-143`. **Acceptance:** deleting a department used by a manager removes the assignment; staff of the deleted dept get `reassign_to` or NULL; deleting General → 400.

### R3 Company delete (hard-delete cascade; "cannot delete the only company") (P1-9, DD-41)
- **Current:** D2/D14; "only company" rule is `COUNT(org_info WHERE parent_id=user_id) <= 1` (`:181-188`).
- **Required:** (a) first `SELECT 1 FROM org_info WHERE org_id=%s AND parent_id=%s` else 404/403 **before** any delete; (b) keep the 400 rule; (c) cascade the same table list as `delete_user_account` per org (factor a shared `_purge_org(cursor, org_id)` used by both) plus `org_holidays`, `org_off_days`, `manager_assignments`, `org_attendance_rule` (new), chat/announcement rows of those users, then S3 cleanup (`delete_employee_s3_assets` per user + `org_logo/{org_id}.png`) **after** commit; (d) alternatively soft-delete (`org_info.status = 0`, users `status=0`) — `org_info.status` exists (DDL:755) but no query filters on it, so every org read would need `AND status = 1`.
- **Compat:** response shape (`remaining_companies`) unchanged; FE picks next active org from it.
- **Acceptance:** user with 1 company → 400; with 2 → deleting B leaves A untouched; calling with A's `user_id` and a foreign `org_id` → 403/404 and nothing deleted; no rows left for B in any listed table.

### R4 Company update must not rebuild structure (D3)
- **Required:** `update_company` must be a patch: never delete/re-key branches or departments. Departments: add missing names, rename only via `PUT /department/update`, never delete. Branches: ignored (managed through `/branch/*`). Also 404 for unknown org, and guard owner e-mail/mobile updates with a uniqueness check.
- **Files:** `company_functions.py:232-333` (remove `:285-297` and replace `:300-314` by an additive insert-if-missing). **Acceptance:** edit form with an extra department keeps all existing `dept_id`s and `manager_assignments`.

### R5 Company logo & other photo endpoints (P3-8)
- **Required:** `DELETE /company/logo/{org_id}` → `ml/s3.py` new `delete_org_logo(org_id)` (`s3.delete_object(Key=f"org_logo/{org_id}.png")`); validate `content_type in {image/png,image/jpeg}` and size ≤ 5 MB in `update_company_logo_route`. `get_company_details` already returns `null` when missing. **Acceptance:** after DELETE, `details.logo_url == null`.

### R6 Return proper HTTP statuses (P2-1)
Add `except HTTPException: raise` before `except Exception` in `add_company`, `get_company_details`, `get_user_companies`, `update_company`; import `logging`/define `logger` in `company_functions.py` (D8).

## 9. Test notes

- Fixtures: two orgs (O1 owned by admin A with 2 companies; O2 by admin B), each with branch_id 1, a General department, active + inactive staff, one manager with assignments, one staff whose `parent_id` is the manager.
- Critical cases: branch delete with/without staff and cross-org id collision; `reassign-staff` + `delete_branch_id`; department delete with manager assignment; company delete only-company / ownership mismatch / cascade completeness; update_company with departments changed (ids stable after R4); `GET /branch/{id}` returns 200 (D7); error status codes for 404/400.
- No automated tests exist for these modules in the repo (no `tests/` directory found). Use a throw-away schema built from the DDL (`DB_SCHEMA`) — never the shared DB.

## 10. Org scoping (applied)

`branch_info.branch_id` repeats across organisations, so every statement that touches `branch_info` / `dept_info` now also matches the org (`users.parent_id` for staff/managers, `users.org_id` for admins, i.e. `COALESCE(u.parent_id, u.org_id)` when only a user row is known). A branch/department of another org behaves as "not found". Guarded by `tests/test_org_scoping.py` (includes a static check that every `JOIN branch_info|dept_info` carries an org predicate).

Changed:
- `branch_functions.delete_branch` (DELETE now `AND org_id`), `reassign_staff` (branch-to-delete must belong to the org -> 404 before any UPDATE; remaining-staff count and DELETE org-scoped), `get_branch_by_id` (optional `org_id`, also accepted as optional query param on `GET /branch/{id}`).
- `dept_functions.delete_department` (UPDATE users / DELETE dept_info org-scoped), `get_departments` (staff_count join org-scoped).
- Joins scoped: `add_staff_function.get_branches/get_departments` (manager path), `get_branch_info`, `get_department_info`; `attendance_functions.get_daily_attendance`; `face_attendance_functions.check_punch_location` and the punch notify lookup; `holiday_functions.check_holiday_block`; `gen_dashboard_functions.get_all_users` (dept), `get_org_employees`, `get_org_attendance_by_date` (dept); `manager_functions.get_staff_for_manager`; `report_functions.generate_monthly_attendance_pdf`; `salary_structure.get_payslip_preview`; `home_functions` department stats (users joined to `dept_info` now `u.parent_id = d.org_id`).
- Already scoped (no change): `update_branch`, dept add/update, `get_branch_staff`, staff_location, chat/announcement/incentive/salary/daily_hourly/emp_pers_detail joins, remaining home_functions joins.
- Not addressed (needs DDL / owner decision): global `MAX(branch_id)+1` in `add_branch` and `company_functions` (ids are in practice global there); per-org composite keys/FKs on `branch_info(org_id, branch_id)` and `users`; `shift` joins that match by `shift_id` only.
