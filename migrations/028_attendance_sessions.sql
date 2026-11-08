-- 028: attendance sessions (WP5, NEW-2, D-22). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/028_attendance_sessions.sql
-- Idempotent (safe to re-run) and it deletes no data. The same objects are in clockyn_ddl_latest.sql.
-- Runs in ONE transaction (migrate.py): any RAISE EXCEPTION below rolls the whole migration back.
--
-- Why: the old unique index on (user_id, attendance_date) allows ONE row per user per day, while the punch code
-- stores one row per SESSION (up to two per day, ordered by punch_in; there is no session-number column).
-- A second punch-in of the day failed with a unique violation. The replacement keeps the only uniqueness that
-- matters: at most one OPEN session per user.
--
-- 1) Pre-check for the new partial unique index. It fails loudly (instead of leaving the constraint half applied)
--    when a user already has more than one open session. Find them and close all but one (admin edit, or
--    UPDATE attendance SET punch_out = <time>, work_hours = <hours> WHERE id = <id>), then re-run:
--      SELECT user_id, array_agg(id ORDER BY punch_in) AS ids, array_agg(punch_in ORDER BY punch_in) AS punch_ins
--      FROM attendance WHERE punch_in IS NOT NULL AND punch_out IS NULL
--      GROUP BY user_id HAVING count(*) > 1;
DO $$
BEGIN
	IF EXISTS (
		SELECT 1 FROM attendance
		WHERE punch_in IS NOT NULL AND punch_out IS NULL
		GROUP BY user_id HAVING count(*) > 1
	) THEN
		RAISE EXCEPTION 'migration 028 stopped: some users have more than one open attendance session; close the extra ones (see the query in the header of 028_attendance_sessions.sql) and re-run';
	END IF;
END
$$;
--
-- 2) Forgotten punch-out closed by the server at the next punch-in (decision D-22): flag for admin review.
ALTER TABLE attendance ADD COLUMN IF NOT EXISTS auto_closed boolean NOT NULL DEFAULT false;
--
-- 3) Drop the stale uniqueness. Depending on how the database was built the name is a UNIQUE constraint or a plain
--    unique index: DROP INDEX fails on a constraint-backed index, so drop the constraint first when it exists.
DO $$
BEGIN
	IF EXISTS (
		SELECT 1 FROM pg_constraint c
		JOIN pg_class t ON t.oid = c.conrelid
		JOIN pg_namespace n ON n.oid = t.relnamespace
		WHERE c.conname = 'attendance_user_id_attendance_date_key'
		  AND t.relname = 'attendance' AND n.nspname = current_schema()
	) THEN
		ALTER TABLE attendance DROP CONSTRAINT attendance_user_id_attendance_date_key;
	END IF;
END
$$;
DROP INDEX IF EXISTS attendance_user_id_attendance_date_key;
--
-- 4) The correct uniqueness: at most one open session per user (the punch code also serialises punch-ins per user
--    with an advisory lock). The two-sessions-per-day limit stays in the code.
CREATE UNIQUE INDEX IF NOT EXISTS ux_attendance_one_open
	ON attendance USING btree (user_id) WHERE (punch_in IS NOT NULL AND punch_out IS NULL);
--
-- 5) Post-check: no other unique index over exactly (user_id, attendance_date) may remain (it would still block the
--    second session). To list unique indexes of the table:
--      SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'attendance' AND indexdef LIKE 'CREATE UNIQUE%';
DO $$
DECLARE
	leftover text;
BEGIN
	SELECT indexname INTO leftover FROM pg_indexes
	WHERE schemaname = current_schema() AND tablename = 'attendance'
	  AND indexdef LIKE 'CREATE UNIQUE INDEX % USING btree (user_id, attendance_date)'
	LIMIT 1;
	IF leftover IS NOT NULL THEN
		RAISE EXCEPTION 'migration 028 stopped: unique index % on attendance(user_id, attendance_date) still forbids two sessions per day; drop it and re-run', leftover;
	END IF;
END
$$;
--
-- OPTIONAL, NOT RUN: attendance_date of rows written before this release is the UTC date of the punch (punches between
-- 00:00 and 05:30 IST sit on the previous IST day); new rows use the IST date (decision D-2). For a database with real
-- history, review and run once (changes no row count, deletes nothing):
--   UPDATE attendance SET attendance_date = (punch_in AT TIME ZONE 'Asia/Kolkata')::date
--   WHERE punch_in IS NOT NULL AND attendance_date <> (punch_in AT TIME ZONE 'Asia/Kolkata')::date;
