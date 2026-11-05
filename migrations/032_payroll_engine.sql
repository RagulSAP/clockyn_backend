-- 032: payroll engine (WP4b). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/032_payroll_engine.sql
-- Idempotent (safe to re-run) and it deletes no data. The same objects are in punchapp_ddl_latest.sql.
--
-- 1) salary_payments.snapshot (jsonb, nullable): the computed salary stored when a month is marked paid, so a paid
--    salary is returned as it was paid and is never recomputed silently. Rows paid before this migration keep
--    snapshot NULL (they show the stored net amount, the components stay live).
ALTER TABLE salary_payments ADD COLUMN IF NOT EXISTS snapshot jsonb;
--
-- 2) org_allowance_rate (P1-12): the organisation-wide odometer allowance (rupees per km) in its own table, instead of
--    "the rate of the most recently updated reading", which every save used to overwrite on EVERY reading
--    (approved ones included).
CREATE TABLE IF NOT EXISTS org_allowance_rate (
	org_id varchar(36) NOT NULL,
	rate numeric(8, 2) NOT NULL,
	updated_by varchar(36) NULL,
	updated_at timestamptz NOT NULL DEFAULT now(),
	CONSTRAINT org_allowance_rate_pkey PRIMARY KEY (org_id),
	CONSTRAINT org_allowance_rate_rate_check CHECK (rate > 0)
);
--
-- 3) odometer_readings.allowance_rate int4 -> numeric(8,2) (decimal rates such as 3.5). int4 values convert exactly.
--    Guarded so a second run (column already numeric) does nothing.
DO $$
BEGIN
	IF EXISTS (
		SELECT 1 FROM information_schema.columns
		WHERE table_schema = current_schema() AND table_name = 'odometer_readings'
		  AND column_name = 'allowance_rate' AND data_type = 'integer'
	) THEN
		ALTER TABLE odometer_readings ALTER COLUMN allowance_rate TYPE numeric(8, 2);
	END IF;
END
$$;
--
-- 4) Seed org_allowance_rate from the rate that is in force today (the latest non-null reading rate of each
--    organisation). A non-positive legacy value violates the CHECK and is skipped. Existing rows are kept.
INSERT INTO org_allowance_rate (org_id, rate)
SELECT DISTINCT ON (org_id) org_id, allowance_rate
FROM odometer_readings
WHERE allowance_rate IS NOT NULL AND allowance_rate > 0
ORDER BY org_id, updated_at DESC NULLS LAST
ON CONFLICT (org_id) DO NOTHING;
