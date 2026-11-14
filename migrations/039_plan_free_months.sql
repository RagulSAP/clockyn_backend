-- 039: plans with several durations and free months. Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/039_plan_free_months.sql
-- Idempotent (safe to re-run). The same columns and index are in clockyn_ddl_latest.sql (gen_plans).
--
-- A plan (Core, Prime, Elite) is sold in several durations: one gen_plans row per duration, same plan_name, e.g.
--   Core  1 month  49.00 / staff / month
--   Core  6 months 49.00 / staff / month, free_months 1   -> the customer pays for 5 months and gets 6   (5 x 49 = 245 per staff)
--   Core 12 months 49.00 / staff / month, free_months 2   -> pays for 10 months and gets 12             (10 x 49 = 490 per staff)
-- plan_amount stays the LIST price per staff per month on every row; free_months says how many of plan_month are not charged.
--
-- 1) free_months: months of plan_month that are not charged. Existing rows get 0, so nothing changes for them.
--    The check keeps a row from pricing a purchase at zero (a plan always charges at least one month).
--    ADD COLUMN with a constant default is a metadata-only change.
ALTER TABLE gen_plans ADD COLUMN IF NOT EXISTS free_months int4 NOT NULL DEFAULT 0
    CONSTRAINT gen_plans_free_months_check CHECK (free_months >= 0 AND free_months < plan_month);
--
-- 2) offer_text: optional wording the app shows on the duration card instead of the automatic "1 month free".
ALTER TABLE gen_plans ADD COLUMN IF NOT EXISTS offer_text varchar(80) NULL;
--
-- 3) One row per plan and duration (so "Core 6 months" cannot exist twice). Created only when no duplicates exist yet.
--    Duplicate check:  SELECT lower(plan_name), plan_month, count(*) FROM gen_plans GROUP BY 1, 2 HAVING count(*) > 1;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM gen_plans GROUP BY lower(plan_name), plan_month HAVING count(*) > 1) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS gen_plans_name_month_key ON gen_plans USING btree (lower(plan_name), plan_month);
    ELSE
        RAISE NOTICE 'gen_plans_name_month_key not created: gen_plans has duplicate (plan_name, plan_month) rows';
    END IF;
END $$;
--
-- NOT done here (owner decision: the prices are yours): the 6 and 12 month rows themselves. Example for Core, Prime and Elite
-- (copies the monthly price of the 1 month row; 6 months = 1 free, 12 months = 2 free; safe to re-run):
--   INSERT INTO gen_plans (plan_id, plan_name, plan_month, plan_amount, free_months)
--   SELECT (SELECT COALESCE(MAX(plan_id), 0) FROM gen_plans) + row_number() OVER (ORDER BY b.plan_id, t.m),
--          b.plan_name, t.m, b.plan_amount, t.f
--   FROM gen_plans b CROSS JOIN (VALUES (6, 1), (12, 2)) AS t(m, f)
--   WHERE b.plan_month = 1 AND lower(b.plan_name) IN ('core', 'prime', 'elite')
--     AND NOT EXISTS (SELECT 1 FROM gen_plans x WHERE lower(x.plan_name) = lower(b.plan_name) AND x.plan_month = t.m);
