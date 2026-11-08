-- 029: CRM leads hardening (WP9). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/029_crm_leads_hardening.sql
-- Idempotent (safe to re-run) and it deletes nothing. The same objects are in clockyn_ddl_latest.sql.
--
-- 1) carry_forward_requests used to be created by request handlers at run time; it now lives here.
CREATE TABLE IF NOT EXISTS carry_forward_requests (
	id int4 GENERATED ALWAYS AS IDENTITY NOT NULL,
	request_id varchar(36) NOT NULL,
	leads_id varchar(36) NOT NULL,
	org_id varchar(36) NOT NULL,
	user_id varchar(36) NOT NULL,
	description text NULL,
	request_date date NOT NULL,
	status varchar(20) DEFAULT 'pending'::character varying NULL,
	actioned_by varchar(36) NULL,
	actioned_at timestamp NULL,
	created_at timestamp DEFAULT CURRENT_TIMESTAMP NULL,
	CONSTRAINT carry_forward_requests_pkey PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS carry_forward_requests_request_id_key ON carry_forward_requests USING btree (request_id);
CREATE INDEX IF NOT EXISTS idx_carry_forward_org_user ON carry_forward_requests USING btree (org_id, user_id);
CREATE INDEX IF NOT EXISTS idx_carry_forward_leads_id ON carry_forward_requests USING btree (leads_id);
--
-- 2) Columns the code relies on (the handler used to add some of them on every request).
ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS request_date date;
ALTER TABLE leads_assignment_info ADD COLUMN IF NOT EXISTS prev_status varchar(64);
--
-- 3) Status widths. leads_info.status holds new / contacted / interested / converted / not_interested (longest
--    14 characters) and used to be varchar(10) in older databases. Widening never rewrites data. Each column is
--    only altered when it is narrower than 64, so a re-run is a no-op.
DO $$
DECLARE
	w int;
BEGIN
	SELECT character_maximum_length INTO w FROM information_schema.columns
	 WHERE table_schema = current_schema() AND table_name = 'leads_info' AND column_name = 'status';
	IF w IS NOT NULL AND w < 64 THEN
		ALTER TABLE leads_info ALTER COLUMN status TYPE varchar(64);
	END IF;
	SELECT character_maximum_length INTO w FROM information_schema.columns
	 WHERE table_schema = current_schema() AND table_name = 'leads_assignment_info' AND column_name = 'status';
	IF w IS NOT NULL AND w < 64 THEN
		ALTER TABLE leads_assignment_info ALTER COLUMN status TYPE varchar(64);
	END IF;
	SELECT character_maximum_length INTO w FROM information_schema.columns
	 WHERE table_schema = current_schema() AND table_name = 'carry_forward_requests' AND column_name = 'status';
	IF w IS NOT NULL AND w < 20 THEN
		ALTER TABLE carry_forward_requests ALTER COLUMN status TYPE varchar(20);
	END IF;
END
$$;
--
-- 4) The lead pipeline default was 'pending', which is not a pipeline value. New default 'new'; existing
--    'pending' / NULL rows are relabelled 'new' (an update, nothing is deleted).
ALTER TABLE leads_info ALTER COLUMN status SET DEFAULT 'new';
UPDATE leads_info SET status = 'new' WHERE status IS NULL OR status = 'pending';
--
-- 5) One assignment row per (lead, staff). The index is created only when it can be created: if duplicates
--    exist a NOTICE is raised and the index is skipped (the application also guards against duplicates, so the
--    service keeps working). Pre-check query:
--      SELECT leads_id, user_id, count(*) FROM leads_assignment_info
--      WHERE leads_id IS NOT NULL GROUP BY leads_id, user_id HAVING count(*) > 1;
--    To remove duplicates (keeps the newest row, backs the others up) run scripts/dedupe_leads.sql by hand on a
--    copy first, then re-run this migration. migrate.py never runs that script.
DO $$
DECLARE
	dup_groups bigint;
BEGIN
	SELECT count(*) INTO dup_groups FROM (
		SELECT 1 FROM leads_assignment_info WHERE leads_id IS NOT NULL
		GROUP BY leads_id, user_id HAVING count(*) > 1
	) d;
	IF dup_groups > 0 THEN
		RAISE NOTICE '029: ux_leads_assignment_lead_user NOT created: % (lead, staff) pairs have duplicate assignment rows. Run scripts/dedupe_leads.sql, then re-run this migration.', dup_groups;
	ELSE
		CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_assignment_lead_user ON leads_assignment_info USING btree (leads_id, user_id);
	END IF;
END
$$;
