# How schema changes are applied today, and the schema changes Clockyn needs

Scope: `migrate.py`, `migrations/README.md`, `fix.sql`, runtime DDL in handlers, git history of migrations, and the proposed new tables/columns for the Clockyn frontend (`C:\POC\pa_frontend\Clockyn\backend_changes.md`, cross-checked against the root DDL file; table/column facts are in `schema.md`). DDL below is a **sketch inside this document only** (no migration files were created). `LP_subscription` stands for the table that carries the legacy product prefix (see `rename_inventory.md`).

## 1. Current change-management mechanism

| Piece | What it does | Evidence | Problems |
|---|---|---|---|
| `migrate.py` | CLI: `python migrate.py <schema> <file.sql>`. Opens a psycopg2 connection from `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME/DB_PORT`, runs `SET search_path TO <schema>`, executes the **whole file as one statement string**, commits, prints a result. | `migrate.py:12-38,40-52` | No version table (nothing records which files ran), no checksum, no ordering, no dry run, no down-migrations. `schema` comes from `argv` and is interpolated with an f-string, unlike `db_function.py` which validates it with a regex (`migrate.py:24` vs `core_functions/db_function.py:18-23`). `CREATE INDEX CONCURRENTLY` and `VACUUM` cannot run because everything is one transaction. The `✓/✗` print raises `UnicodeEncodeError` on a cp1252 console (cosmetic, after commit). `DB_SCHEMA` is **not** used by the script. |
| `migrations/README.md` | 4-step manual: write `XXX_description.sql`; test on the dev schema; apply to `public`; set `DB_SCHEMA=public`. | `migrations/README.md:1-19` | Refers to `migrations/001_create_new_tables.sql`, which no longer exists (the folder holds only this README). Hard-codes a QA RDS host name, database and legacy schema names (lines 10, 15, 3, 8, 19; recorded in `rename_inventory.md`; the host is infrastructure data, not a secret). Describes `public` as the target, but the DDL dump is qualified with a separate production schema, so environments are inconsistent (UNVERIFIED which is live). |
| `fix.sql` | One-off data fix for shifts with implausible duration (wrong AM/PM): a SELECT listing shifts ordered by computed duration, then an UPDATE with the placeholder `<id_from_above>`. | `fix.sql:1-15` | Not executable as-is (placeholder). Mirrors `_validate_shift_duration` (18 h cap, `core_functions/shift_functions.py:42-62`), which guards only new writes. Companion data: `C:\POC\pa_backend\Shift Issue\shift_202609051215.csv` (75 active shifts exported 5 Sept; 6 are >= 18 h: shift ids 71, 29, 51, 15, 48, 70) and three screenshots of a monthly attendance sheet showing a 20:00-08:00 shift with *Total Hrs 12:00 / Difference -24:00* (cross-midnight handling in reports). |
| Runtime DDL in handlers | `CREATE TABLE IF NOT EXISTS carry_forward_requests` (3 places), `ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS description`, `ALTER COLUMN status TYPE VARCHAR(20)` run inside request handlers. | `core_functions/leads_functions.py:274,288-289,324,366` | Takes an ACCESS EXCLUSIVE lock on every carry-forward request; requires the app DB user to have DDL rights; schema changes escape review. Must move to a migration (backend_changes P1-11 / P2-15). |
| DDL dump | A DB-client generated dump of the production schema, committed as the root DDL file on 2026-07-25 (`123d72f`) and edited on 2026-09-05 (`36a555b`). | git log | **Stale**: no `app_version_control`, `ticket_replies`, `org_off_days`, `gen_invoice_seq`, enum types, `block_attendance`, `coupon_code` columns, `admin_subject_request.user_id` (see `schema.md` section 4). |
| Env/schema model | `DB_SCHEMA` (required) sets `search_path` at connection time and again after each checkout. | `core_functions/db_function.py:25-38,52-66` | One module reads `DB_SCHEMA` at import and qualifies a table with it (`core_functions/gen_dashboard_functions.py:9,128`). Any schema rename must update the env var in every environment. |

### 1.1 Migration history recovered from git (deleted from the working tree)

| Commit (date) | Deleted/added | Content | In root DDL file? |
|---|---|---|---|
| `581bd3c` (2026-06-25, initial push) | added `migrations/001_create_new_tables.sql` (178 lines), `019_add_push_token_to_users.sql`, `020_add_name_to_gen_admin_login.sql`, `021_add_meta_attribution.sql`, `022_account_deletion_requests.sql`, `023_create_email_log.sql`, `app_version_control.sql`, `migrate_schema.py`, an earlier design DDL (714 lines, with `CREATE TYPE` for the 3 enums and ~45 `FOREIGN KEY`s) | Numbering jumps 001 -> 019, i.e. files 002-018 were never in the repo | partly: push_token, name, meta attribution, deletion requests, email_log are present; `app_version_control` is not |
| `1081288` (2026-06-26, "cleaned") | deleted all of the above except the README | | - |
| `123d72f` (2026-07-25) | added the root DDL file | production dump | - |
| `36a555b` (2026-09-05) | deleted `change_db.sql` (suggestion_replies table; `coupon_code` on orders/invoices; `ticket_replies` table + `admin_subject_request.user_id`; `org_holidays.block_attendance`) and an empty `migrations/001_create_new_tables.sql`; edited the DDL by 14 lines | `change_db.sql` was the last "pending migration" file | `suggestion_replies` was copied into the DDL; **the other three changes were not** |
| `36a555b` (same commit) | rewrote `router/daily_hourly.py` (165 lines changed): the daily/hourly wages router became a copy of the holiday router | recoverable: `git show 36a555b^:router/daily_hourly.py` (159 lines; 8 wage-template routes + 4 assign routes) | n/a |

Recovered DDL fragments (from git history) that belong in the next baseline migration:

```sql
-- app_version_control (from the deleted migration)
CREATE TABLE IF NOT EXISTS app_version_control (
    id             SERIAL PRIMARY KEY,
    platform       VARCHAR(10) NOT NULL UNIQUE,      -- 'android' | 'ios'
    latest_version VARCHAR(20) NOT NULL,
    min_supported  VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    force_update   BOOLEAN     NOT NULL DEFAULT false,
    message        TEXT,
    store_url      TEXT,
    updated_at     TIMESTAMP DEFAULT NOW()
);
-- change_db.sql leftovers
ALTER TABLE gen_orders   ADD COLUMN IF NOT EXISTS coupon_code VARCHAR(50);
ALTER TABLE gen_invoices ADD COLUMN IF NOT EXISTS coupon_code VARCHAR(50);
ALTER TABLE admin_subject_request ADD COLUMN IF NOT EXISTS user_id VARCHAR(36);
CREATE TABLE IF NOT EXISTS ticket_replies (
    id SERIAL PRIMARY KEY, ticket_id INTEGER NOT NULL, reply_text TEXT NOT NULL,
    author_name VARCHAR(100) DEFAULT 'Admin', is_admin BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_ticket_replies_ticket_id ON ticket_replies(ticket_id);
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS block_attendance BOOLEAN NOT NULL DEFAULT TRUE;
```

No DDL exists anywhere (repo or history) for `org_off_days`, the sequence `gen_invoice_seq`, or the enum types (`order_status`, `incentive_type`, `request_type_enum`; values are in the earlier design DDL).

## 2. Recommended process (applies to every item in section 3)

1. **Freeze a baseline.** Dump the real production schema (`pg_dump --schema-only`), commit it as `schema/baseline.sql`, diff it against the root DDL file, and treat differences as the first migration (section 1.1 fragments + the three missing objects).
2. **Adopt a runner with a version table** (Alembic, Flyway or sqitch; or extend `migrate.py` with a `schema_migrations(version, checksum, applied_at)` table, per-file transactions, `--dry-run`, and schema-name validation). Keep migrations idempotent (`IF NOT EXISTS`) because production already drifted.
3. **Expand / contract**: add nullable columns and new tables first (safe with the old app), deploy code that writes both, backfill, then add `NOT NULL`/unique/FK as `NOT VALID` + `VALIDATE`, finally drop old objects. The mobile v1 app may keep calling the API during rollout (see `backend_changes.md` P0-8/C-10).
4. Remove runtime DDL from `leads_functions.py` in the same release that ships the migration.
5. Run migrations as a separate privileged DB role; give the application role DML only.

## 3. Proposed schema changes for the Clockyn frontend

Legend: **Need** = what the frontend/backend_changes item requires; **Compat** = backward-compatibility note. IDs refer to `backend_changes.md`; DD = `docs/DEFERRED_DECISIONS.md`. Items with no schema impact are listed in 3.2.

### 3.1 Items that need DDL

#### M1 - Daily/hourly wage routers (P0-1, P0-2, P0-3, DD-76/77)
*Current:* tables `daily_hourly_wages` (templates with `user_id IS NULL`, per-user copies with `user_id`) and `assigned_wages` exist; router missing (restore from git, section 1.1). No uniqueness: duplicate template names per org, a user can be assigned to many templates of the same type; `assigned_wages` has no `temp_type` so the "one template per type" rule needs a join.
*DDL sketch:*
```sql
-- no duplicate template names per org+type (templates only)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wage_template_name
  ON daily_hourly_wages (org_id, temp_type, temp_name) WHERE user_id IS NULL;
-- (temp_id is the business key; make it unique for templates)
CREATE UNIQUE INDEX IF NOT EXISTS uq_wage_temp_id
  ON daily_hourly_wages (temp_id) WHERE user_id IS NULL;
ALTER TABLE assigned_wages ADD COLUMN IF NOT EXISTS temp_type CHAR(1);
UPDATE assigned_wages aw SET temp_type = d.temp_type
  FROM daily_hourly_wages d WHERE d.temp_id = aw.temp_id AND d.user_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_assigned_wage_user_type ON assigned_wages (user_id, temp_type);
ALTER TABLE daily_hourly_wages ADD CONSTRAINT chk_wage_type CHECK (temp_type IN ('D','H')) NOT VALID;
```
*Compat:* additive; clean duplicates before validating. *Also fix:* delete must remove per-user copies and assignments (P1-9).

#### M2 - Attendance rule (P0-4, DD-60)
*Current:* no table.
```sql
CREATE TABLE org_attendance_rule (
  org_id               VARCHAR(36) PRIMARY KEY,
  late_punch_in_count  SMALLINT NOT NULL DEFAULT 3 CHECK (late_punch_in_count BETWEEN 1 AND 10),
  consider_as          VARCHAR(10) NOT NULL DEFAULT 'Half Day' CHECK (consider_as IN ('Half Day','Leave')),
  grace_minutes        SMALLINT NOT NULL DEFAULT 0,        -- optional: DD-60 grace question
  updated_by           VARCHAR(36),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
*Need:* GET/POST `/api/attendance-rule/{org}` and **enforcement** in monthly attendance and salary (`home/monthly-attendance`, `/api/salary/staff-breakdown`). *Compat:* absent row = defaults 3 / Half Day.

#### M3 - Salary paid state (P0-6)
*Current:* nothing records a paid salary.
```sql
CREATE TABLE salary_payments (
  id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id       VARCHAR(36) NOT NULL,
  user_id      VARCHAR(36) NOT NULL,
  year         SMALLINT NOT NULL,
  month        SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
  net_salary   NUMERIC(12,2) NOT NULL,
  paid_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_by      VARCHAR(36),
  UNIQUE (user_id, year, month)                       -- makes mark-paid idempotent
);
CREATE INDEX idx_salary_payments_org_period ON salary_payments (org_id, year, month);
```
*Need:* `POST /salary/mark-paid` (`INSERT ... ON CONFLICT DO NOTHING`), `GET /salary/monthly-list` joining it. Store the net at pay time (do not recompute later).

#### M4 - App version control per app (P0-8)
```sql
ALTER TABLE app_version_control ADD COLUMN IF NOT EXISTS app_id VARCHAR(100) NOT NULL DEFAULT 'legacy';
ALTER TABLE app_version_control DROP CONSTRAINT IF EXISTS app_version_control_platform_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_version ON app_version_control (app_id, platform);
-- seed the new app (values supplied by the owner)
-- INSERT INTO app_version_control(app_id,platform,latest_version,min_supported,force_update,store_url) VALUES (...);
```
*Need:* `GET /version/check?platform&version&app_id` (default `legacy` when absent so the old app keeps working). *Compat:* additive.

#### M5 - Push credentials per Firebase project, logout, sessions (P0-9, P3-7, DD-33/34, P3-1)
*Current:* one FCM service account from env (`core_functions/auth_function.py:49-55,61-100`); `users.push_token` single text value, no project marker; no logout; `users.device_token` plaintext; no sessions.
```sql
-- one row per installed app instance; supports multi-device and multi-project
CREATE TABLE device_sessions (
  session_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         VARCHAR(36) NOT NULL,
  app_id          VARCHAR(100) NOT NULL DEFAULT 'legacy',
  platform        VARCHAR(10),
  device_token_hash CHAR(64) NOT NULL,                  -- sha256 of the opaque token, never the token itself
  push_token      TEXT,
  push_project    VARCHAR(100),                          -- which Firebase project issued push_token
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at    TIMESTAMPTZ,
  expires_at      TIMESTAMPTZ NOT NULL,
  revoked_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX uq_device_session_token ON device_sessions (device_token_hash);
CREATE INDEX idx_device_sessions_user ON device_sessions (user_id) WHERE revoked_at IS NULL;
-- interim, minimal variant if sessions are deferred:
ALTER TABLE users ADD COLUMN IF NOT EXISTS push_project VARCHAR(100);
```
*Need:* `POST /auth/logout` clears the session's `push_token`; push sender picks credentials by `push_project`; auth dependency verifies the bearer token against `device_sessions` and derives `user_id/org_id/role_id` (P3-1). *Compat:* keep `users.device_token` reads for v1 during migration; backfill one session per user from `users`.

#### M6 - Odometer allowance rate store (P1-12, P2-4, DD-92)
*Current:* the org rate lives only in `odometer_readings.allowance_rate` (int); `save_allowance_rate` rewrites every reading (`core_functions/emp_expenses_functions.py:470-483`).
```sql
CREATE TABLE org_odometer_settings (
  org_id          VARCHAR(36) PRIMARY KEY,
  allowance_rate  NUMERIC(8,2) NOT NULL DEFAULT 3,
  updated_by      VARCHAR(36),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE odometer_readings ALTER COLUMN allowance_rate TYPE NUMERIC(8,2);   -- accepts decimals (P2-4)
```
*Need:* GET/POST rate use the new table; `save` updates only `status='pending'` rows (or none); approval stamps `allowance_rate`; approved rows keep their stamp. *Compat:* backfill the table from the latest reading per org.

#### M7 - Subscription renewal semantics, trial limit, price integrity (P1-13, P2-11, P3-2, DD-15/16/17/19)
*Current:* same-plan order adds staff and keeps `end_date` (`core_functions/gen_payment_functions.py:75-92`); trial has no staff limit; `gen_orders.amount` is the client's number (`gen_orders_functions.py:12-18`); invoices have no PK/unique number.
```sql
ALTER TABLE free_trial          ADD COLUMN IF NOT EXISTS staff_limit INT NOT NULL DEFAULT 10;   -- value = product decision
ALTER TABLE gen_orders          ADD COLUMN IF NOT EXISTS intent VARCHAR(10) NOT NULL DEFAULT 'new'
        CHECK (intent IN ('new','renew','topup','upgrade'));
ALTER TABLE gen_orders          ADD COLUMN IF NOT EXISTS base_amount NUMERIC(10,2),
                                ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(10,2) NOT NULL DEFAULT 0,
                                ADD COLUMN IF NOT EXISTS gst_amount NUMERIC(10,2),
                                ADD COLUMN IF NOT EXISTS credit_amount NUMERIC(10,2) NOT NULL DEFAULT 0;
ALTER TABLE LP_subscription     ADD COLUMN IF NOT EXISTS renewed_from INT;     -- previous subscription id (history chain)
-- invoice integrity
ALTER TABLE gen_invoices ADD PRIMARY KEY (id);   -- the identity column `id` exists but is not declared as PK
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_number ON gen_invoices (invoice_number);
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_id ON gen_invoices (invoice_id);
ALTER TABLE gen_invoices ADD COLUMN IF NOT EXISTS igst NUMERIC(10,2) NOT NULL DEFAULT 0,
                         ADD COLUMN IF NOT EXISTS place_of_supply VARCHAR(60);
CREATE SEQUENCE IF NOT EXISTS gen_invoice_seq START 1;
```
*Need:* explicit renew intent that extends `end_date`; server recomputes price from `gen_plans.plan_amount`, `staff_limit`, `coupon_code`, credit and GST and ignores the client amount; subscription state field. *Compat:* new columns nullable/defaulted; old clients send no `intent` (-> `new`, current behaviour).

#### M8 - Holidays: one router, block flag, uniqueness, stable ids (P1-5, DD-44, DD-97)
*Current:* three routers on `/api/holidays`; `block_attendance` missing from DDL; scope by branch/department **name**; `org_holidays.id` has no PK; `check_holiday_block` uses `CURRENT_DATE` (`core_functions/holiday_functions.py:160`).
```sql
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS block_attendance BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE org_holidays ADD PRIMARY KEY (id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_holiday
  ON org_holidays (org_id, "date", name, COALESCE(branch_name,''), COALESCE(dept_name,''));
-- optional (DD-44): id-based scope instead of names
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS branch_id INT, ADD COLUMN IF NOT EXISTS dept_id VARCHAR(50);
-- formalise the weekly-off table whose DDL is missing
CREATE TABLE IF NOT EXISTS org_off_days (org_id VARCHAR(36) PRIMARY KEY, off_days INT[] NOT NULL DEFAULT '{6}');
```
*Compat:* null flag treated as blocking (the client already does so).

#### M9 - Attendance model, time zone, shift snapshot and day types (P1-3, P2-5, DD-04/05/07, P1-8)
*Current:* unique `(user_id, attendance_date)` contradicts 2 sessions; `attendance_date` is the UTC date; no shift snapshot, so lateness is recomputed against a UTC `.time()`.
```sql
ALTER TABLE org_info ADD COLUMN IF NOT EXISTS timezone VARCHAR(40) NOT NULL DEFAULT 'Asia/Kolkata';
DROP INDEX IF EXISTS attendance_user_id_attendance_date_key;
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS session_no SMALLINT NOT NULL DEFAULT 1,
                       ADD COLUMN IF NOT EXISTS shift_id INT,
                       ADD COLUMN IF NOT EXISTS shift_start TIME,
                       ADD COLUMN IF NOT EXISTS shift_end TIME,
                       ADD COLUMN IF NOT EXISTS late_minutes INT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_session ON attendance (user_id, attendance_date, session_no);
-- backfill attendance_date to the org-local date once, in a maintenance window:
-- UPDATE attendance a SET attendance_date = (a.punch_in AT TIME ZONE 'Asia/Kolkata')::date WHERE a.punch_in IS NOT NULL;
```
*Need:* `GET /home/monthly-attendance` returns `shift_start_time`, `shift_end_time`, `type` (working/holiday/weekly_off/leave), correct absent counting; `edit_attendance` upsert key becomes `(user_id, attendance_date, session_no)`. *Compat:* additive until the backfill/index swap; run the swap together with the code change.

#### M10 - Requests (P1-1, P1-15, P3-4, DD-91)
```sql
-- one pending device-change request per user (P3-4)
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_device_change
  ON requests (user_id) WHERE request_type = 'device_change' AND status = 'pending';
-- request-level decision timestamp for aggregate leave status (P1-15)
ALTER TABLE requests ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_leave_dates_request_status ON leave_dates (request_id, status);
```
*Need:* leave decisions update `requests.status` (approved when all dates approved, rejected when all rejected, else partially decided or keep pending); employee list returns `date_statuses`, `current_shift_id`, `requested_shift_id`.

#### M11 - CRM leads (P1-11, DD-94/95/96)
*Current:* `leads_assignment_info` runtime-altered; `carry_forward_requests` runtime-created; `leads_info.status varchar(10)` overflows `not_interested`; duplicate rows per assignment.
```sql
ALTER TABLE leads_info ALTER COLUMN status TYPE VARCHAR(20);
ALTER TABLE leads_info ALTER COLUMN status SET DEFAULT 'new';
CREATE UNIQUE INDEX IF NOT EXISTS uq_lead_assignment ON leads_assignment_info (leads_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_carry_forward
  ON carry_forward_requests (leads_id, user_id) WHERE status = 'pending';
ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS description TEXT, ADD COLUMN IF NOT EXISTS request_date DATE;
```
Keep the baseline `CREATE TABLE carry_forward_requests` in the migration and delete the three runtime copies.

#### M12 - Safe deletes, tenant integrity, ids (P1-9, DD-41/43/77)
```sql
-- soft delete columns (extend to dept_info, shift, pay_slip, leads_info, group)
ALTER TABLE branch_info ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE dept_info   ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
-- branch identity must include the tenant
CREATE UNIQUE INDEX IF NOT EXISTS uq_branch_org_branch ON branch_info (org_id, branch_id) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dept_org_dept ON dept_info (org_id, dept_id);
-- first foreign keys (stage with NOT VALID, then VALIDATE after cleaning orphans)
ALTER TABLE attendance          ADD CONSTRAINT fk_att_user  FOREIGN KEY (user_id)    REFERENCES users(user_id) NOT VALID;
ALTER TABLE leave_dates         ADD CONSTRAINT fk_ld_req    FOREIGN KEY (request_id) REFERENCES requests(request_id) NOT VALID;
ALTER TABLE payroll             ADD CONSTRAINT fk_pr_slip   FOREIGN KEY (pay_slip_id) REFERENCES pay_slip(pay_slip_id) NOT VALID;
-- the FK targets need unique keys: users(user_id) and requests(request_id) already have them; pay_slip(pay_slip_id) needs one:
CREATE UNIQUE INDEX IF NOT EXISTS uq_pay_slip_id ON pay_slip (pay_slip_id);
```
*Need:* branch delete scoped by `org_id` (P1-9), refuse/cascade when children exist, null-out inactive staff. *Compat:* code must use `(org_id, branch_id)`; unify the three `MAX()+1` generators first.

#### M13 - Billing entity and branding configuration (P2-10, C-11)
*Current:* the invoice PDF/e-mail hard-code the legal entity name, CIN, registered address, place of supply, signature image, logo, support e-mail and website (`utils/email.py:172-214,505-518,623-631,726-730`) (SMS/WhatsApp template ids come from env).
```sql
CREATE TABLE billing_entity (
  id               SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),     -- single active seller
  legal_name       VARCHAR(200) NOT NULL,
  cin              VARCHAR(25),
  gstin            VARCHAR(15),
  address_lines    TEXT NOT NULL,
  state_name       VARCHAR(60) NOT NULL,
  state_code       CHAR(2) NOT NULL,
  support_email    VARCHAR(120),
  website          VARCHAR(200),
  logo_key         VARCHAR(200),           -- object key under the static/assets store
  signature_key    VARCHAR(200),
  invoice_prefix   VARCHAR(10) NOT NULL DEFAULT 'INV',
  product_name     VARCHAR(80) NOT NULL    -- single place for the product name used in OTP/invoice/WhatsApp texts
);
```
*Compat:* seed with the current values so output is unchanged until the owner updates them. Alternative: env vars/config file; a table allows change without redeploy.

#### M14 - Server-side OTP throttling (P2-7, DD-37)
```sql
CREATE TABLE otp_throttle (
  scope       VARCHAR(10) NOT NULL,              -- 'contact' | 'ip'
  key         VARCHAR(255) NOT NULL,
  window_start TIMESTAMPTZ NOT NULL,
  hits        INT NOT NULL DEFAULT 1,
  PRIMARY KEY (scope, key, window_start)
);
```
(or a Redis/WAF rate limit). The existing `attempts` counter limits guesses per OTP (`core_functions/auth_function.py:585-595,697-714`) but not the number of OTP sends per number/IP.

#### M15 - Indexes and integrity fixes found while cross-checking (not tied to one frontend item)
```sql
CREATE INDEX IF NOT EXISTS idx_chat_receiver ON chat (receiver_id, sender_id, date_time DESC);
CREATE INDEX IF NOT EXISTS idx_chat_group    ON chat (group_id, date_time DESC);
CREATE INDEX IF NOT EXISTS idx_chat_activity ON chat_activity (chat_id, receiver_id);
CREATE INDEX IF NOT EXISTS idx_announcement_org ON announcement (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_announcement_recipient ON announcement_recipients (announcement_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gen_admin_phone ON gen_admin_login (phone_number);
-- fix schema drift that breaks code today
ALTER TABLE activity_tracking ADD COLUMN IF NOT EXISTS changed_by VARCHAR(20), ADD COLUMN IF NOT EXISTS status INT;
ALTER TABLE activity_tracking ALTER COLUMN activity DROP NOT NULL;     -- or change the INSERT in gen_dashboard_functions.py:127-131
```
Also a code fix (no DDL): the Razorpay webhook must insert `org_id` into `gen_payments` (`core_functions/gen_webhook_functions.py:50-55`) because the column is NOT NULL.

### 3.2 Items from `backend_changes.md` with no schema impact (cross-check)

| ID | Why no DDL |
|---|---|
| P0-5 overtime report | Computed from `attendance`, `shift`, `users_additional_info.shift_id` and the assigned overtime template (`incentive_overtime_details`); new endpoint only. (If historical stability is wanted, snapshot via M9 columns.) |
| P0-7 shift staff assignment | Uses existing `users_additional_info.shift_id` (indexed `idx_users_additional_shift_id`); needs a unique `(user_id)` on `users_additional_info` to be safe (see `schema.md` risks) and the 18-hour rule reuse. Optional history table if needed: `shift_assignments(user_id, shift_id, effective_from)`. |
| P1-2 branch_id on staff detail | Column exists (`users.branch_id`); select it. |
| P1-4 role_id demotion | One-line code change (`add_staff_function.py:578-585`). |
| P1-6/P1-7 chat/announcement events | Event payload changes only (add indexes from M15 for history queries). |
| P1-10, P1-16, P1-17 | Query/param changes. |
| P2-1, P2-2, P2-3, P2-6 | Error handling/envelopes. |
| P2-8 requests counts | New endpoint over `requests` (index `idx_requests_org_id`, `idx_requests_status`). |
| P2-9 N+1 | New bulk endpoints. |
| P2-12 support photo | Code (`photo_url` stores the key). |
| P2-13 chat delete | Decide semantics; current table has no per-user hide flag, so "delete for me" needs `chat_hidden(chat_id, user_id)` (add if per-side delete is wanted). |
| P3-3 `force_accept` | Code policy; optional `attendance.override_by` if admin-approved overrides are introduced. |
| P3-5, P3-6, P3-8..P3-14 | Authorization/hardening in code (depends on M5 for tokens). |

### 3.3 Suggested order of work

1. Baseline dump + runner (section 2). 2. Drift repair migration (1.1 fragments + M15 drift fixes + `org_off_days`, `gen_invoice_seq`, enum types). 3. Unblock P0: restore wages router (no DDL; apply M1 constraints after cleaning), M2, M3, shift assignment endpoints. 4. M4 + M5 (version row per app, push project) before the first store release. 5. M9 (attendance model) together with the IST/session code change. 6. M7/M13 with the rebrand and legal entity decision. 7. M10-M12 hardening; FKs last.

### 3.4 Acceptance checks for any migration

- `migrate` run twice leaves the schema unchanged (idempotent).
- `pg_dump --schema-only` after the run equals the committed baseline plus applied migrations.
- Old (v1) client smoke tests still pass against the migrated schema (additive changes only until the contract phase).
- For unique/FK additions: a pre-check query listing violating rows returns 0 before `VALIDATE`.
