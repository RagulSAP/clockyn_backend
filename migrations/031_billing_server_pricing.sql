-- 031: billing hardening (server-side pricing, idempotent activation, invoice uniqueness, coupon rules).
-- Apply with the target schema on the search_path (python migrate.py --all). Safe to re-run; deletes nothing.
-- The same objects are in punchapp_ddl_latest.sql (punchapp_prod.*).
--
-- 1) gen_orders: snapshot of the server-computed price and the purchase intent (read at activation time by the
--    payment verify call AND by the payment webhook; both are idempotent on the order).
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS purchase_mode varchar(10) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS term_months int4 NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS base_amount numeric(10, 2) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS gst_amount numeric(10, 2) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS discount_amount numeric(10, 2) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS credit_amount numeric(10, 2) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS client_amount numeric(10, 2) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS pricing jsonb NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS payment_flag varchar(30) NULL;
ALTER TABLE gen_orders ADD COLUMN IF NOT EXISTS activated_at timestamp NULL;
--
-- 2) gen_coupons: optional rules. Existing rows keep working unchanged (percent coupon, no limits).
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS discount_type varchar(10) DEFAULT 'percent' NOT NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS flat_amount numeric(10, 2) NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS max_discount numeric(10, 2) NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS min_amount numeric(10, 2) NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS valid_from date NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS valid_until date NULL;
ALTER TABLE gen_coupons ADD COLUMN IF NOT EXISTS plan_ids int4[] NULL;
--
-- 3) gen_invoices: IGST column (inter-state customers).
ALTER TABLE gen_invoices ADD COLUMN IF NOT EXISTS igst numeric(10, 2) DEFAULT 0 NOT NULL;
--
-- 4) The invoice sequence. When gen_invoices already holds rows the sequence is moved to the highest numeric invoice
--    number (so the next number never collides); a no-op on an empty table. Idempotent.
CREATE SEQUENCE IF NOT EXISTS gen_invoice_seq START 1;
DO $$
BEGIN
	IF EXISTS (SELECT 1 FROM gen_invoices) THEN
		PERFORM setval('gen_invoice_seq', GREATEST(
			COALESCE((SELECT max(substring(invoice_number FROM '([0-9]{1,15})$')::bigint) FROM gen_invoices), 0), 1));
	END IF;
END
$$;
--
-- 5) One invoice per payment and one number per invoice. THESE TWO STATEMENTS FAIL (loudly, rolling this file back)
--    if existing rows collide; this migration deliberately deletes nothing. Find the collisions, fix or renumber the
--    rows you decide to keep, then re-run:
--      SELECT razorpay_payment_id, count(*), array_agg(invoice_number ORDER BY id)
--      FROM gen_invoices GROUP BY 1 HAVING count(*) > 1;
--      SELECT invoice_number, count(*), array_agg(razorpay_payment_id ORDER BY id)
--      FROM gen_invoices GROUP BY 1 HAVING count(*) > 1;
--    (the old number format truncated at 10000, so a duplicate number is possible after that many invoices)
CREATE UNIQUE INDEX IF NOT EXISTS uq_gen_invoices_payment_id ON gen_invoices USING btree (razorpay_payment_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_gen_invoices_invoice_number ON gen_invoices USING btree (invoice_number);
--
-- 6) Subscription lookups and the expiry sweep are per organisation.
CREATE INDEX IF NOT EXISTS idx_sub_org_status ON punchapp_subscription USING btree (org_id, status);
