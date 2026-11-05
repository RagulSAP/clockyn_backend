-- dedupe_leads.sql: OPTIONAL, MANUAL clean-up of duplicate lead assignments. migrate.py never runs this file.
--
-- Why: before WP9 a lead could be assigned to the same staff twice, leaving several leads_assignment_info rows
-- for one (leads_id, user_id). Migration 029 only creates the unique index ux_leads_assignment_lead_user when
-- no such duplicates exist. Run this script, then re-run migration 029.
--
-- What it does: keeps the NEWEST row of every (leads_id, user_id) group (latest updated_at, then highest id),
-- copies the removed rows into leads_assignment_dedupe_backup, and deletes them. It changes nothing else.
--
-- How to run (use a copy of the database first, with the target schema on the search_path):
--   psql -d <db> -c "SET search_path TO <schema>;" -f scripts/dedupe_leads.sql
--   (or: psql "options=-csearch_path=<schema>" -d <db> -f scripts/dedupe_leads.sql)
-- The script ends with ROLLBACK so a first run changes nothing. Review the output, then change the final
-- ROLLBACK to COMMIT and run it again.

-- Step 1: dry run. Duplicate groups and the rows that WOULD be removed (rn > 1).
SELECT leads_id, user_id, count(*) AS rows_in_group
FROM leads_assignment_info
WHERE leads_id IS NOT NULL
GROUP BY leads_id, user_id
HAVING count(*) > 1
ORDER BY rows_in_group DESC, leads_id;

SELECT id, leads_id, user_id, status, updated_at
FROM (
	SELECT id, leads_id, user_id, status, updated_at,
	       row_number() OVER (PARTITION BY leads_id, user_id ORDER BY updated_at DESC NULLS LAST, id DESC) AS rn
	FROM leads_assignment_info
	WHERE leads_id IS NOT NULL
) r
WHERE r.rn > 1
ORDER BY leads_id, user_id, id;

-- Step 2: the clean-up, inside one transaction.
BEGIN;

CREATE TABLE IF NOT EXISTS leads_assignment_dedupe_backup (LIKE leads_assignment_info INCLUDING DEFAULTS);

WITH doomed AS (
	SELECT id FROM (
		SELECT id,
		       row_number() OVER (PARTITION BY leads_id, user_id ORDER BY updated_at DESC NULLS LAST, id DESC) AS rn
		FROM leads_assignment_info
		WHERE leads_id IS NOT NULL
	) r
	WHERE r.rn > 1
), removed AS (
	DELETE FROM leads_assignment_info a
	USING doomed d
	WHERE a.id = d.id
	RETURNING a.*
)
INSERT INTO leads_assignment_dedupe_backup SELECT * FROM removed;

-- Step 3: verification. Must return zero rows.
SELECT leads_id, user_id, count(*) AS rows_in_group
FROM leads_assignment_info
WHERE leads_id IS NOT NULL
GROUP BY leads_id, user_id
HAVING count(*) > 1;

-- Change to COMMIT; once the dry run and the verification above look right.
ROLLBACK;
