-- Read-only checks to run on a COPY of an EXISTING database before applying migrations 024-033.
-- A fresh database built from punchapp_ddl_latest.sql does not need any of this.
-- Run with: psql -v ON_ERROR_STOP=0 -f scripts/pre_migration_checks.sql   (every query should return ZERO rows unless noted)
SET search_path TO punchapp_prod, public;

-- 025 (holiday unique index): rows listed here must be de-duplicated first
SELECT org_id, date, COALESCE(branch_name, '') AS branch, COALESCE(dept_name, '') AS dept, count(*)
FROM org_holidays GROUP BY 1, 2, 3, 4 HAVING count(*) > 1;

-- 028 (attendance sessions): any user with more than one OPEN session blocks the migration
SELECT user_id, count(*) FROM attendance
WHERE punch_in IS NOT NULL AND punch_out IS NULL GROUP BY 1 HAVING count(*) > 1;
-- informational: open sessions older than 18 hours (they will be auto-closed at the next punch-in)
SELECT user_id, attendance_date, punch_in FROM attendance
WHERE punch_in IS NOT NULL AND punch_out IS NULL AND punch_in < now() - interval '18 hours';
-- informational: rows whose stored date differs from the IST date of the punch-in (legacy UTC dates; optional back-fill is at the end of 028)
SELECT count(*) AS rows_with_utc_based_date FROM attendance
WHERE punch_in IS NOT NULL AND attendance_date <> (punch_in AT TIME ZONE 'Asia/Kolkata')::date;
-- the old unique index must be the one 028 expects (name below); any OTHER unique index over (user_id, attendance_date) also raises
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'punchapp_prod' AND tablename = 'attendance' AND indexdef ILIKE '%unique%';

-- 029 (CRM): duplicate assignments (run scripts/dedupe_leads.sql, then re-run 029)
SELECT leads_id, user_id, count(*) FROM leads_assignment_info
WHERE leads_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1;
-- listings join on org_id: rows with NULL org_id would vanish from the lead lists
SELECT count(*) AS assignments_without_org FROM leads_assignment_info WHERE org_id IS NULL;
SELECT count(*) AS categories_without_org FROM leads_category_info WHERE org_id IS NULL;

-- 031 (billing): duplicate payment ids or invoice numbers make the unique indexes fail
SELECT razorpay_payment_id, count(*) FROM gen_invoices GROUP BY 1 HAVING count(*) > 1;
SELECT invoice_number, count(*) FROM gen_invoices GROUP BY 1 HAVING count(*) > 1;
-- if gen_invoice_seq is new on this database, set it past the highest existing invoice number afterwards:
--   SELECT setval('gen_invoice_seq', <highest numeric part of invoice_number>);

-- 032 (payroll): allowance_rate int4 -> numeric(8,2) overflows from 1,000,000 and takes an ACCESS EXCLUSIVE lock
SELECT DISTINCT allowance_rate FROM odometer_readings ORDER BY 1 DESC LIMIT 20;
SELECT count(*) AS non_positive_rates FROM odometer_readings WHERE allowance_rate IS NOT NULL AND allowance_rate <= 0;

-- staff email rule (USER_AUTH=email): the same email on two live accounts makes login ambiguous (409)
SELECT lower(email) AS email, count(*) FROM users WHERE status IN (0, 1) GROUP BY 1 HAVING count(*) > 1;
-- staff who cannot sign in under USER_AUTH=email (no usable email): fill these in
SELECT user_id, name, role_id FROM users WHERE status = 1 AND (email IS NULL OR email !~ '^[^@\s]+@[^@\s]+\.[^@\s]{2,}$');

-- 033 (auth epoch) must be applied BEFORE AUTH_TOKEN_SECRET is set; the app_version_control.app_id column exists only in the DDL:
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'punchapp_prod' AND table_name = 'app_version_control' AND column_name = 'app_id';  -- expect 1 row, else add the column before using /version/check
