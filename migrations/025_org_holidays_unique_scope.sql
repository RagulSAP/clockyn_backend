-- 025: holidays and weekly offs hardening (router/holiday.py is now the single owner of /api/holidays*).
-- Apply with the target schema on the search_path, e.g. python migrate.py <schema> migrations/025_org_holidays_unique_scope.sql
-- (the unique index is also in punchapp_ddl_latest.sql as punchapp_prod.uq_org_holidays_org_date_scope). Safe to re-run.
--
-- 1) Columns / tables the code already relies on (older databases may lack them):
ALTER TABLE org_holidays ADD COLUMN IF NOT EXISTS block_attendance bool DEFAULT true NOT NULL;
CREATE TABLE IF NOT EXISTS org_off_days (
	org_id varchar(36) NOT NULL,
	off_days int4[] DEFAULT '{6}' NOT NULL,
	CONSTRAINT org_off_days_pkey PRIMARY KEY (org_id)
);
--
-- 2) At most one holiday per org + date + branch + department scope (NULL branch / department = whole organisation).
--    DE-DUPLICATE FIRST: this statement fails if existing rows already collide, and this migration deliberately
--    deletes nothing. Find the collisions, decide which row to keep and delete the others by id, then re-run:
--      SELECT org_id, date, COALESCE(branch_name, '') AS branch, COALESCE(dept_name, '') AS dept,
--             array_agg(id ORDER BY id) AS ids, array_agg(name ORDER BY id) AS names
--      FROM org_holidays
--      GROUP BY 1, 2, 3, 4 HAVING count(*) > 1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_org_holidays_org_date_scope
	ON org_holidays USING btree (org_id, date, (COALESCE(branch_name, '')), (COALESCE(dept_name, '')));
