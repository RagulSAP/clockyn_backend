# Employee expenses (odometer, travel tickets, bills, allowance rate) — backend context

Domain owner: BD4. Line numbers verified against the files as read.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/emp_expenses.py` | 169 | 13 endpoints, prefix `/expenses` (`router/emp_expenses.py:25`), mounted at `app.py:51` |
| `core_functions/emp_expenses_functions.py` | 599 | all logic + request models + S3 photo helpers |
| `ml/s3.py:11-22` | n/a | shared boto3 `s3` client, `S3_BUCKET`, `AWS_REGION` |
| `core_functions/report_functions.py:676-726` | n/a | **consumer**: payroll adds approved odometer and travel/bill amounts |
| `core_functions/add_staff_function.py:755-756`, `core_functions/company_functions.py:429-430` | n/a | delete a user's rows (no S3 clean-up) |

Frontend consumer: `src/api/expenses.ts` (+ `src/api/mappers/expenses.ts`). Contract note: `C:\POC\pa_frontend\project_complete_context\shared\api\expenses.md`.

## 2. Data model touched

Schema dump: `odometer_readings` (dump line 717), `travel_bill_expenses` (970). `org_info` (validity check), `users` (`parent_id` = org id for staff/managers, `name`).

### `odometer_readings`
| Column | Type | Meaning |
|---|---|---|
| `id` | int identity PK | internal |
| `user_id`, `org_id` | varchar(36) NOT NULL | org is taken from `users.parent_id` server-side on employee calls; from the path/body on admin calls |
| `readings_id` | varchar(36), UNIQUE index | uuid4; external key used by the admin status endpoint |
| `reading_date` | date NOT NULL | the day; **one row per user per day** is enforced by code only (no unique constraint) |
| `start_km`, `stop_km`, `distance_km` | numeric(10,2) | `distance_km = stop - start` written on stop |
| `status` | varchar(20) default `'pending'` | `pending / approved / rejected` |
| `allowance_rate` | int4 NULL | rupees per km, **integer**; per-row stamp (see §4.6). Org rate is not stored anywhere else |
| `created_at`, `updated_at` | timestamp | `get_utc_now()` written explicitly |
Indexes: `reading_date`, `org_id`, `readings_id` (unique), `user_id`.

### `travel_bill_expenses`
| Column | Type | Meaning |
|---|---|---|
| `id` | int identity PK | internal |
| `user_id`, `org_id` | varchar(36) NOT NULL | org from `users.parent_id` |
| `expense_id` | varchar(36) NULL, non-unique index | uuid4, external key |
| `expense_date` | date NOT NULL | client supplied, unvalidated |
| `expense_type` | varchar(20) NOT NULL | stored upper case: `TRAVEL` or `BILL` |
| `amount` | numeric(12,2) NOT NULL | client supplied, sign and size unvalidated |
| `status` | varchar(20) default `'pending'` | `pending / approved / rejected` |
| `created_at`, `updated_at` | timestamp | written explicitly |
No photo column: the receipt lives only in S3 under a deterministic key.

### S3 keys (bucket from env)
* `odo_meter_photos/{org_id}/{user_id}/{reading_date}_{photo_type}.png` (`emp_expenses_functions.py:32`, `:46`); content type forced `image/png`.
* `travel_bill_photos/{org_id}/{user_id}/{expense_id}.png` (`:241`, `:255`).

No soft delete. No FK. Rows are removed only by user/company deletion (`add_staff_function.py:755-756`, `company_functions.py:429-430`), which do not remove the S3 objects (UNVERIFIED for S3: the deletion helpers in `ml/s3.py` target other prefixes).

## 3. Endpoints

Handlers are thin `async def` wrappers (`router/emp_expenses.py:38-169`); the functions raise `HTTPException` themselves (400/404 propagate correctly through the `except HTTPException: raise` in the functions) except the two upload routes (see below). No authentication, no plan gate. Responses use `{success:true,...}` envelopes.

| METHOD path | Handler (file:line) | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| POST `/expenses/odometer/start` | `submit_start_reading` (`router/emp_expenses.py:38`) -> `submit_odometer_start` (`emp_expenses_functions.py:58`) | body `user_id` | JSON `{user_id:str, reading_date:str, start_km:float}` | `{success:true, message:"Start reading submitted successfully", readings_id}` | 404 `User or organization not found`; 400 `Start reading already submitted for this date`; bad date -> 500 |
| POST `/expenses/odometer/stop` | `:44` -> `submit_odometer_stop` (`:119`) | body | `{user_id, reading_date, stop_km:float}` | `{success:true, message, distance:float}` | 400 `Please submit start reading first`; 400 `Start reading not found` (also when `start_km==0`); 400 `Stop reading must be greater than start reading` |
| GET `/expenses/odometer/readings` | `:50` -> `get_odometer_readings` (`:184`) | query `user_id` | query `user_id` (required), `reading_date?` | `{success:true, data:[<all odometer_readings columns>, start_photo_url, stop_photo_url]}` | no date -> last 30 by `reading_date DESC, created_at DESC`; photo URLs presigned 7 days or `null` |
| GET `/expenses/admin/odometer-readings/{org_id}` | `:59` -> `get_admin_odometer_readings` (`:366`) | path | none | `{success:true, data:[{id,user_id,readings_id,reading_date,start_km,stop_km,distance_km,status,allowance_rate,created_at,updated_at,user_name,start_photo_url,stop_photo_url}]}` | whole org history, no pagination; `distance_km` recomputed in code if NULL |
| PUT `/expenses/admin/odometer-readings/{readings_id}/status` | `:65` -> `update_odometer_status` (`:420`) | body `org_id` | `{status:'approved'\|'rejected', org_id}` | `{success:true, message:"Reading <status> successfully"}` | 400 `Status must be 'approved' or 'rejected'`; 404 `Reading not found` (wrong id or org). `org_id` is client supplied |
| POST `/expenses/admin/allowance-rate` | `:71` -> `save_allowance_rate` (`:470`) | body `org_id` | `{org_id:str, rate:int}` | `{success:true, message:"Allowance rate saved successfully", rate}` | `rate` must be an integer: `3.5` -> 422 (pydantic 2.12). Succeeds with no effect when the org has no readings |
| GET `/expenses/admin/allowance-rate/{org_id}` | `:77` -> `get_allowance_rate` (`:497`) | path | none | `{success:true, rate:int\|null}` | `null` when no reading carries a rate |
| POST `/expenses/odometer/upload-photo` | `upload_odometer_photo` (`:83`) | query `user_id` | query `user_id`, `reading_date`, `photo_type` (all required), multipart `file` | `{success:true, url:<S3 KEY>}` | **returns the S3 key, not a URL**. Unknown user -> inner 404 is caught by `except Exception` and re-raised as **500** `"404: User not found"` (`:103-111`) |
| GET `/expenses/admin/travel-bill/{org_id}` | `:114` -> `get_admin_travel_bill_expenses` (`:522`) | path | optional `expense_type` (any case) | `{success:true, data:[{id,user_id,expense_id,expense_date,expense_type,amount,status,created_at,user_name,photo_url}]}` | whole history, no pagination |
| PUT `/expenses/admin/travel-bill/{expense_id}/status` | `:120` -> `update_travel_bill_status` (`:566`) | body `org_id` | `{status:'approved'\|'rejected', org_id}` | `{success:true, message:"Expense <status> successfully"}` | 400 invalid status; 404 `Expense not found` |
| POST `/expenses/travel-bill/submit` | `:126` -> `submit_travel_bill_expense` (`:267`) | body `user_id` | `{user_id, expense_date:str, expense_type:str, amount:float}` | `{success:true, message:"Expense submitted successfully", expense_id}` | 400 `Expense type must be TRAVEL or BILL` (case-insensitive); 404 user/org |
| GET `/expenses/travel-bill/expenses` | `:132` -> `get_travel_bill_expenses` (`:319`) | query `user_id` | `user_id`, `expense_type?`, `expense_date?` | `{success:true, data:[<all columns>, photo_url]}` | max 30 rows, `expense_date DESC, created_at DESC` |
| POST `/expenses/travel-bill/upload-photo` | `upload_travel_bill_photo` (`:142`) | query `user_id` | query `user_id`, `expense_id`, multipart `file` | `{success:true, url:<S3 KEY>}` | same 404-as-500 wrapping (`:161-169`) |

Endpoint count: 13. There is **no delete endpoint** for readings, expenses or photos, and none to list/read a single expense.

## 4. Business logic

### 4.1 Common employee guard
Every employee call (`start`, `stop`, `readings`, `travel-bill/submit`, `travel-bill/expenses`) runs `SELECT u.parent_id FROM users u WHERE u.user_id=%s AND u.parent_id IN (SELECT org_id FROM org_info WHERE status=1)` (`emp_expenses_functions.py:65-72` and equivalents); no row -> 404 `User or organization not found`. So an **admin's own user id** (whose `parent_id` is not an org id, UNVERIFIED) or any user of an inactive org gets 404. The two upload routes use a weaker lookup (`SELECT parent_id FROM users`, `router/emp_expenses.py:98`) without the org-status check.

### 4.2 Odometer start (`:58-116`)
1. guard; 2. `SELECT id FROM odometer_readings WHERE user_id AND org_id AND reading_date` -> if present 400 (**one reading per user per day**, regardless of status); 3. `readings_id = uuid4`; INSERT `(user_id, org_id, readings_id, reading_date, start_km, created_at, updated_at)` (status default `pending`, `allowance_rate` NULL); commit; return `readings_id`. No check that `start_km >= 0`, nor against yesterday's stop, nor that the date is today.

### 4.3 Odometer stop (`:119-181`)
1. guard; 2. read the day's row (404-style 400 if absent); 3. `if not start_km` -> 400 (so a legitimate `0` start is rejected); 4. `stop_km <= start_km` -> 400; 5. `distance = stop_km - start_km` (float arithmetic on `float(Decimal)`); `UPDATE ... SET stop_km, distance_km, updated_at`. **No status check**: stop can be re-posted after approval/rejection, silently changing an approved distance (payroll uses `distance_km`, `report_functions.py:700-708`). Response `distance` is the float difference.

### 4.4 Readings read (`:184-236`)
Guard; with `reading_date`: that day newest first; without: `ORDER BY reading_date DESC, created_at DESC LIMIT 30`. For each row two S3 `head_object` calls (`get_odometer_photo_url`, `:44-55`) and presigned GET URLs with `ExpiresIn=604800` (7 days); any exception -> `None` (bare `except:`). `SELECT *` exposes every column including `id` and `allowance_rate`; `Decimal` columns are serialised by FastAPI as numbers.

### 4.5 Travel / bill expenses
* Submit (`:267-316`): validate `expense_type.upper() in {TRAVEL,BILL}` first (before the user guard), guard, `expense_id = uuid4`, INSERT with `expense_type.upper()` and `amount` as given (negative/zero/huge accepted; numeric(12,2) overflow -> 500). The row is created **before** any photo exists. Status `pending`.
* List (`:319-363`): `expense_type` filter is upper-cased; `LIMIT 30`; adds `photo_url` per row (one `head_object` + presign each).
* Admin list (`:522-563`): `LEFT JOIN users` for `user_name`, optional upper-cased type filter, no limit; `photo_url` per row; failures -> `None`.
* Status update (`:566-599`): `UPDATE ... WHERE expense_id AND org_id RETURNING id`; any transition allowed (approved <-> rejected), only the two values accepted (not `pending`), no actor recorded (no `action_by`).
* `expense_type` casing: the stored/returned value is always upper case (`TRAVEL`/`BILL`); input is case-insensitive in submit and in all filters. The Clockyn API layer upper-cases filters (`src/api/expenses.ts` `listMyExpenses`) and DD-93 notes the v1 app sent lower case on submit.

### 4.6 Photos
* Upload routes read the whole file into memory (`await file.read()`, no size/type limit), derive `org_id` from `users.parent_id` and `put_object` at the deterministic key with `ContentType=image/png` regardless of the real format. `photo_type` (odometer) and `expense_id` (expense) are not validated or sanitised; `reading_date` is used verbatim in the key. Re-uploading overwrites. The response field is named `url` but contains the **S3 key**; the frontend ignores the value (`assertSuccess` only, `src/api/expenses.ts`), and the displayed URL comes from the list endpoints' presigned `photo_url` / `start_photo_url` / `stop_photo_url`.
* The upload route does not verify that the reading/expense exists or belongs to `user_id`.
* Failure modes: odometer photo is uploaded **before** the reading exists in the Clockyn flow ("taken before submitting the km"), so a failed start leaves an orphan S3 object; the expense flow is the reverse (row first, photo second): a failed/aborted upload leaves an **expense without a photo** that is still approvable and payable, and since no delete endpoint exists the employee cannot withdraw it.

### 4.7 Allowance rate (`:420-519`)
* **Save** (`save_allowance_rate`, `:470-494`): `UPDATE odometer_readings SET allowance_rate=%s, updated_at=%s WHERE org_id=%s` — **every** reading of the org (pending, approved, rejected, past months). The docstring and comment (`:471`, `:476`) say "pending / those without one" but the WHERE clause has no such filter. If the org has no readings the statement updates 0 rows, commits and still answers `success:true`: the rate is **lost**. Parameter `rate: int` (`router/emp_expenses.py:33-35`).
* **Get** (`:497-519`): `SELECT allowance_rate ... WHERE org_id AND allowance_rate IS NOT NULL ORDER BY updated_at DESC LIMIT 1`; `{success:true, rate}`; `null` if none. A newly created reading has `allowance_rate NULL` until approval or the next save, so the rate "exists" only through other rows.
* **Approve** (`update_odometer_status`, `:420-467`): when approving, reads the org's latest non-null rate by `updated_at` and writes `allowance_rate = COALESCE(rate_to_set, allowance_rate)`; so approval (or re-approval) stamps/overwrites the row with the **current** org rate; rejecting leaves the rate alone. Missing org rate -> no stamp.
* **Payroll consumer** (`report_functions.py:680-709`): `SUM(distance_km * COALESCE(allowance_rate, org_latest_rate, 1))` over the month's approved readings, so the per-row stamp is what pays out. Because saving a new rate rewrites all rows, **a rate change retroactively re-prices already approved months**, i.e. recomputes past payslips. Approved travel/bill amounts (`expense_type` TRAVEL / BILL, `status='approved'`) are summed separately (`report_functions.py:711-726`).
* The Clockyn admin UI additionally multiplies distance by the *current* org rate on every render (DD-92), so displayed amounts also move when the rate is edited.

## 5. Side effects & integrations

| Operation | Integration | Detail |
|---|---|---|
| photo upload | S3 `put_object` | keys above, `ContentType=image/png` |
| reading/expense lists | S3 `head_object` + `generate_presigned_url` (7 days) | N+1 per row, per photo |
| status updates | none | **no push, no WebSocket, no e-mail** to the employee on approve/reject; no notification to admins on submit |
| payroll | DB read | approved rows feed `report_functions.py:680-726` |

## 6. Config & env vars

`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (secrets read at `ml/s3.py:10-11`), `AWS_S3_REGION`, `S3_BUCKET_NAME`; DB settings (`DB_SCHEMA`, connection variables).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| E-1 | High | `emp_expenses_functions.py:477-483` | Allowance rate save overwrites **all** readings incl. approved/past; retroactively changes payroll (`report_functions.py:700-708`) and contradicts its own comment. |
| E-2 | High | `:470-494` | Rate is stored only on reading rows: orgs without readings lose the saved rate while the API answers success; `get_allowance_rate` returns `null` afterwards. |
| E-3 | High | `:420-467` | Re-approving a reading re-stamps it with the current org rate (COALESCE prefers the new value), so approved amounts are not frozen. |
| E-4 | High | `:119-181` | Stop reading can be re-submitted after approval/rejection: distance (and therefore payout) can be altered by the employee. |
| E-5 | High | all routes | No authentication/authorisation: employee `user_id`, admin `org_id` in path/body are trusted; anyone can approve any reading/expense if they know the id and org id; upload endpoints let anyone write into any user's S3 folder. |
| E-6 | Medium | `:267-316`, `router/emp_expenses.py:142-169` | Orphan expense records: row created before the photo upload; failed/aborted upload leaves a photo-less pending expense that admins can approve; no delete or "withdraw" endpoint exists. |
| E-7 | Medium | `router/emp_expenses.py:109`, `:167` | `upload-photo` returns the S3 **key** under the name `url`; clients must not use it as a link. |
| E-8 | Medium | `router/emp_expenses.py:103-111`, `:161-169` | Inner `HTTPException(404)` is wrapped as HTTP 500 `"404: User not found"`. |
| E-9 | Medium | `:30-41`, `:239-250` | Uploads: no size or content-type validation, whole file in memory, content type forced to PNG whatever the bytes; `photo_type`/`reading_date`/`expense_id` unsanitised in the S3 key. |
| E-10 | Medium | `:267-316` | `amount` not validated (negative, zero, huge); `expense_date` not validated (future/far past); no duplicate detection. |
| E-11 | Medium | `:77-87`, `:150-151` | One reading per user per day enforced in code only; a start `0` km is treated as "not found" at stop; no monotonic check against the previous stop. (DD-90 notes the app allows several trips per day; the server does not.) |
| E-12 | Medium | `router/emp_expenses.py:33-36` | `rate: int` rejects decimal rates (422); DB column `allowance_rate int4`. |
| E-13 | Medium | `:184-236`, `:366-417`, `:522-563` | N+1 S3 `head_object` per row per photo (up to 60 calls for 30 readings; unbounded for the admin lists); admin lists have no pagination/date filter. |
| E-14 | Low | `:566-599`, `:420-467` | No actor/timestamp of the decision stored (`action_by` absent); `pending` cannot be restored; no employee notification. |
| E-15 | Low | `:274`, `:337-346` | `expense_type` accepted in any case but returned upper case; lists are capped at 30 without cursor, so older rows are unreachable for employees. |
| E-16 | Low | `:54-55`, `:263-264` | Bare `except:` swallows S3 errors (returns `None` photo URL silently). |
| E-17 | Low | `add_staff_function.py:755-756`, `company_functions.py:429-430` | User/company deletion removes DB rows but leaves the S3 photos (privacy/retention). |
| E-18 | Info | `:107`, `:307` | `RETURNING id, readings_id`: `id` unused. |

## 8. Clockyn requirements

References: `backend_changes.md` (BC), `docs/DEFERRED_DECISIONS.md` (DD).

### X-1 Allowance rate storage and stamping (BC P1-12, DD-92)
* **Current:** see §4.7, E-1..E-3.
* **Required change:** store the org rate in its own table and stop rewriting approved rows. Saving a rate updates the org rate and, at most, **pending** readings; approval stamps the rate once; an already stamped row keeps its stamp (payroll then uses the frozen figure); `GET` returns the org rate even when no readings exist; accept decimals.
* **Implementation outline:**
  1. Migration: `CREATE TABLE org_allowance_rate (org_id varchar(36) PRIMARY KEY, rate numeric(8,2) NOT NULL, updated_by varchar(36), updated_at timestamp NOT NULL DEFAULT now())` (+ back-fill from `SELECT DISTINCT ON (org_id) org_id, allowance_rate ... ORDER BY org_id, updated_at DESC`); `ALTER TABLE odometer_readings ALTER COLUMN allowance_rate TYPE numeric(8,2)`.
  2. `save_allowance_rate` (`emp_expenses_functions.py:470`): UPSERT the org row; optionally `UPDATE odometer_readings SET allowance_rate = %s WHERE org_id = %s AND status = 'pending'`; remove the unfiltered UPDATE. Change `AllowanceRateRequest.rate` to `float` (`router/emp_expenses.py:33-35`) with `gt=0`.
  3. `get_allowance_rate` (`:497`): read from the new table.
  4. `update_odometer_status` (`:420`): stamp only `WHERE allowance_rate IS NULL` (i.e. `COALESCE(allowance_rate, %s)` with the org rate first), never overwrite on re-approval; return `allowance_rate` in the response.
  5. `report_functions.py:690-705` read the org rate from the new table.
* **Backward compat:** `{success, rate}` unchanged; `rate` may now be non-integer (the current app rounds, `setAllowanceRate`, and will keep working). After the fix the app can show approved rows at `allowanceRate` (already mapped, `src/api/mappers/expenses.ts`).
* **Acceptance:** (1) save rate 5 for an org with no readings -> `GET` returns 5; (2) approve a reading at rate 5, change rate to 7 -> that reading still has 5, a later approval gets 7; payroll for the old month is unchanged; (3) `rate: 3.5` is accepted; `0` and negative -> 422; (4) re-approving does not change the stamp; (5) pending readings (if the product keeps retro-pricing for pending) show the new rate.

### X-2 Amounts "re-priced when the rate changes" (DD-92, BC P1-12)
* **Current:** client multiplies `distance_km` by the current org rate for every row; server stamps per row but overwrites on save.
* **Required change:** return per-row `amount` (distance x stamped rate for approved rows, distance x current rate for pending) computed server-side from X-1 so the app can drop its own maths; keep `allowance_rate` per row.
* **Outline:** add computed `amount` to `get_odometer_readings` (`:184`) and `get_admin_odometer_readings` (`:366`) SELECTs: `distance_km * COALESCE(allowance_rate, (SELECT rate FROM org_allowance_rate WHERE org_id = o.org_id))`.
* **Compat:** additive field. **Acceptance:** admin list `amount` equals payroll's contribution for approved rows.

### X-3 `expense_type` casing (DD-93)
* **Current:** server accepts any case, stores/returns `TRAVEL`/`BILL`; the v1 app sent `travel`/`bill` on submit and `TRAVEL`/`BILL` on lists.
* **Required change:** keep accepting both; document `TRAVEL|BILL` as canonical; add a CHECK constraint (`expense_type IN ('TRAVEL','BILL')`) after confirming existing data. **Compat:** no change for clients. **Acceptance:** `travel`, `Travel`, `TRAVEL` all create `TRAVEL`; `foo` -> 400.

### X-4 `upload-photo` returns a key, not a URL (BC P3-8)
* **Current:** `{success:true, url:<key>}` (E-7). The app ignores the field.
* **Required change:** keep the key under a new field `key` and make `url` a presigned URL (or drop `url`); validate size (<= 10 MB suggested) and content type (`image/jpeg|png|webp`), store the real content type and extension; return 404 (not 500) for unknown user; verify the reading/expense exists and belongs to `user_id`; create the photo **after** the record for odometer too (or accept the photo in the same multipart call).
* **Outline:** modify `save_*_photo` and both routes (`router/emp_expenses.py:83-169`); add `except HTTPException: raise`.
* **Compat:** field `url` stays present. **Acceptance:** upload for a non-owned expense id -> 403/404; 11 MB file -> 413; text file -> 400; response contains a working URL.

### X-5 Orphan expense records and photo-less delete (BC P3-8, P1-9)
* **Current:** E-6; no delete endpoint; employees see no status on lists (DD-93).
* **Required change:** (a) `DELETE /expenses/travel-bill/{expense_id}?user_id=` allowed only while `status='pending'` and owner = caller, removing the S3 object; (b) optionally create the expense and photo atomically (multipart submit with `photo`); (c) a nightly job (or admin filter) flags pending expenses with no photo older than N hours; (d) ensure list endpoints return `status` (already returned) and add `has_photo`.
* **Outline:** add function `delete_travel_bill_expense` in `emp_expenses_functions.py`, route in `router/emp_expenses.py`; use `s3.delete_object` on `travel_bill_photos/{org}/{user}/{expense_id}.png`; `has_photo` from the existing `head_object` call.
* **Compat:** additive. **Acceptance:** owner can delete a pending expense (row and object gone), cannot delete an approved one (409), another user cannot delete it (403/404).

### X-6 Stop-reading integrity and decimal `rate` (BC P2-4)
Block stop re-submission once status != `pending` (409), treat `start_km = 0` as valid, add a unique index `(user_id, reading_date)` on `odometer_readings`, and make `rate` decimal (see X-1). Acceptance: approved reading cannot be altered; start 0 then stop 10 gives distance 10.

### X-7 Notifications for decisions (new, optional)
Send a push to the employee when a reading/expense is approved/rejected (reuse `send_push_notification`, `core_functions/auth_function.py:204`) with data `{type:"expense_updated", expense_kind:"odometer"|"travel_bill", id, status}`; record `action_by`/`action_at` columns. Acceptance: employee receives exactly one push per decision.

## 9. Test notes

* **Fixtures:** an active org (`org_info.status=1`), staff user with `parent_id` = org, admin; S3 mocked (e.g. `moto` or patch `ml.s3.s3`); several readings in different statuses and months.
* **Critical cases:** second start on the same day -> 400; stop <= start -> 400; stop after approval changes distance (current defect); save rate when no readings exist (nothing persisted); save rate with approved rows (all re-stamped); approve then save new rate (stamp changes); `rate=3.5` -> 422; expense type case-insensitivity; photo upload for unknown user (500 with `404:` text); presigned URLs only when the object exists; payroll month totals before/after a rate change (`report_functions.py:680-726`).
* **Existing tests:** none in the backend repository; frontend contract tests in `src/api/__tests__/staffRequestsExpensesContract.test.ts`, odometer maths in `src/features/expenses/admin/__tests__/odometerMath.test.ts`.
