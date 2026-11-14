# DDL cross-check: code versus `clockyn_ddl_latest.sql`

Method (static, no database was available; the "executed on an empty database" run is NOT RUN, see the "DDL status" note in `schema.md`). Two scripts, kept in the build scratchpad (not in the repository):

1. `ddl_static.py` parses the whole DDL file with the PostgreSQL parser (`pglast`), then replays it statement by statement against a simulated catalog: schema, enum types, tables, columns, primary key / unique constraints, indexes, foreign keys, the sequence, the seed inserts. It reports syntax errors, objects used before they exist, duplicate relation or constraint names, unknown types and enum labels, foreign keys whose target has no unique key, seed inserts that omit NOT NULL columns, `ON CONFLICT` targets without a unique key and non-idempotent seed inserts.
2. `ddl_crosscheck.py` takes the catalog from step 1 and scans every string constant (f-strings included) in `core_functions/`, `router/`, `utils/`, `services/` and the root `*.py` (81 files, including the Step-1 files) that looks like SQL: `INSERT INTO t (cols)`, `UPDATE t SET col =`, `FROM/JOIN t alias`, `alias.col`, `ON CONFLICT (cols)`, bare columns of single-table statements, `col = %s` fragments of dynamically built `SET` clauses, and NOT NULL columns without a default that an `INSERT` omits. Best effort: columns of dynamically assembled statements (`{}` placeholders) and bare columns of multi-table statements cannot be checked.

Result on the final DDL file: 195 statements parse, 0 structural errors; the cross-check finds 4 items, all code-side (listed below). The same scripts on the DDL file as it was before this change report 9 structural errors and 26 code-versus-DDL mismatches, which is how the added objects were derived (the DDL-side mismatches are fixed in the file).

## Code-side mismatches (code NOT changed)

| # | Where | Statement | Problem | Suggested code fix |
|---|---|---|---|---|
| 1 | `core_functions/add_staff_function.py:550-556` | `INSERT INTO branch_info (org_id, branch_id, branch_name, branch_address, branch_latitude, branch_longitude, created_at, updated_at)` | Columns `branch_latitude`/`branch_longitude` do not exist (the table has `gps_location point`); the statement raises `UndefinedColumn`. Dead branch of the staff-update path (gap analysis P1-4). | Write `gps_location` as `POINT(lng, lat)` (or omit it: it now defaults to `point(0,0)`). |
| 2 | `core_functions/gen_webhook_functions.py:50-56` | `INSERT INTO gen_payments (user_id, razorpay_payment_id, razorpay_order_id, amount, status) SELECT ... FROM gen_orders` | `gen_payments.org_id` is `NOT NULL` without default and is not supplied: every `payment.captured` webhook fails and rolls back (gap analysis NEW-16). The DDL keeps the NOT NULL (a payment without an organisation is invalid). | Select `org_id` from `gen_orders` as well. |
| 3 | `core_functions/emp_pers_detail_functions.py:189-196` | dynamic `INSERT INTO users_additional_info ({cols})` | When no `users_additional_info` row exists, only the supplied non-empty fields are inserted, but `acc_no`, `ifsc_code`, `bank_name`, `bank_branch`, `gender`, `address`, `emergency_contact_no` are `NOT NULL` without default: the insert fails unless the request supplies all seven. All column names used exist. | Insert `''` for the seven columns as `shift_functions.py:341` and `add_staff_function.py:414` do. |
| 4 | `core_functions/add_staff_function.py:348-353` | `INSERT INTO branch_info (...)` without `gps_location` | `gps_location` was `NOT NULL` without default, so adding staff with a new branch name failed. **Fixed on the DDL side**: the column now has `DEFAULT point(0, 0)` (same placeholder `company_functions` uses). Listed for completeness: that branch has no usable geofence until the admin sets it. | Collect the location in the flow or reject the unknown branch name. |

Items 1 to 3 are the only mismatches the scan reports after the DDL changes (item 4 is no longer reported).

## DDL-side mismatches fixed in the file

| Object | Code reference | Fix |
|---|---|---|
| schema `punchapp_prod` | every qualified statement | `CREATE SCHEMA IF NOT EXISTS` |
| enum types `order_status`, `incentive_type`, `request_type_enum` | `gen_orders`/`gen_payments.status`, `incentive_templates.type`, `requests.request_type` | `CREATE TYPE` (labels: union of code literals and the original design DDL in git history) |
| 4 foreign keys referenced `incentive_templates` before it was created (3 of 4 were out of order) | `incentive_bonus/count/overtime/turnover_details` | all four moved to a foreign-key section after all tables |
| `org_off_days` | `holiday_functions.py:12, 27` | table (`org_id` PK, `off_days int4[]`) |
| `org_holidays.block_attendance` | `holiday_functions.py:72, 119, 157` | column `bool NOT NULL DEFAULT true` |
| `app_version_control` | `router/version.py:15` | table with `app_id` (design P0-8), unique `(app_id, platform)` |
| `ticket_replies` | `support_functions.py:79, 128, 176` | table + index on `ticket_id` |
| `admin_subject_request.user_id` | `support_functions.py:28, 128, 155, 176, 203` | column `varchar(36)` |
| `gen_orders.coupon_code`, `gen_invoices.coupon_code` | `gen_orders_functions.py:95`, `gen_payment_functions.py:34, 139`, `utils/email.py:1108` | columns `varchar(50)` |
| `gen_invoice_seq` | `gen_payment_functions.py:148` | sequence `START 1` |
| `activity_tracking.changed_by`, `.status` | `gen_dashboard_functions.py:128` | columns `varchar(20)`, `int4`; and `activity` made nullable (the insert never supplies it) |
| `attendance_rule`, `salary_payments` (Step 1) | `attendance_rule_functions.py`, `salary_payment_functions.py` | tables as designed (P0-4, P0-6) |
| `idx_assigned_wages_user_temp`, `idx_salary_payments_org_period` | designs | indexes |
| `branch_info.gps_location` | `add_staff_function.py:348` | `DEFAULT point(0,0)` |
| `leads_info.status`, `carry_forward_requests.status` | `leads_functions.py:446-458` writes `not_interested` (14 chars) | widened `varchar(10)` to `varchar(20)` |
| `gen_invoices` had no primary key | table has `id` identity | `PRIMARY KEY (id)` + index on `org_id` |

## Known conflicts deliberately left in the DDL

- Unique index `attendance_user_id_attendance_date_key` (gap analysis NEW-2): contradicts the two-sessions-per-day punch code (`face_attendance_functions.py:203-209`) but `edit_attendance` needs it for `ON CONFLICT (user_id, attendance_date)`. Marked with a comment in the file; the later attendance step replaces it by the partial unique index `ux_attendance_one_open` (design NEW-2) and changes `edit_attendance` together.
- Permission names: the seed follows the app's ten strings; `core_functions/permission_checker.py` still looks up `Department Management` and `Payroll Management`, which do not exist in the seed (only used by `/managers/permissions/{id}` and the legacy decorators).
- `router/version.py` filters on `platform` only and ignores `app_id`; keep one row per platform until the route is updated (the seed inserts one per platform).
- `users.org_id` has a unique index (`users_org_id_key`): only the admin row carries `org_id` (staff keep it NULL and use `parent_id`); a second user with the same `org_id` fails. `payroll(user_id)` is unique, `shift(shift_id)` is globally unique (also unique with `org_id`). Existing behaviour, not changed.
- Handlers still run `CREATE TABLE IF NOT EXISTS carry_forward_requests` / `ALTER TABLE leads_assignment_info ...` at request time (`leads_functions.py:274-289, 324, 366`); harmless now that the table exists, remove later.
