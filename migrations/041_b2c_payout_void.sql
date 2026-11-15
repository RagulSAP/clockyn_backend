-- 041: a payout to a B2C promoter can be VOIDED (recorded by mistake, or the transfer bounced). Apply with the target schema on
-- the search_path, e.g.   python migrate.py <schema> migrations/041_b2c_payout_void.sql
-- Idempotent (safe to re-run); deletes nothing. The same columns are in clockyn_ddl_latest.sql (b2c_payout).
--
-- A voided payout stays in the ledger (audit trail: who voided it, when, why) but no longer counts as paid, so the commission
-- due of the promoter goes up by its amount again. NULL voided_at = a valid payout. ADD COLUMN without a default is metadata-only.
ALTER TABLE b2c_payout ADD COLUMN IF NOT EXISTS voided_at timestamptz NULL;
ALTER TABLE b2c_payout ADD COLUMN IF NOT EXISTS voided_by varchar(255) NULL;
ALTER TABLE b2c_payout ADD COLUMN IF NOT EXISTS void_reason varchar(300) NULL;
