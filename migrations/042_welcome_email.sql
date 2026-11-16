-- 042: one-time welcome e-mail to a company admin (users.welcome_email_*). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/042_welcome_email.sql
-- Idempotent AND the backfill runs exactly once (see below). The same columns are in clockyn_ddl_latest.sql (users).
--
-- The e-mail is sent the first time an admin's Home screen loads after registering (core_functions/welcome_email.py). The claim is one
-- atomic UPDATE on these columns, so it is sent once, and a failed send is retried at most 3 times, at least 1 hour apart:
--   welcome_email_sent_at           set when the e-mail was sent (NULL = not sent yet)
--   welcome_email_attempts          how many sends were tried
--   welcome_email_last_attempt_at   when the last try started (the 1 hour spacing)
--
-- BACKFILL: every user who exists when this migration first runs is marked as already welcomed (welcome_email_sent_at = now()), so existing
-- customers do not suddenly receive a "welcome" e-mail. Only users registered AFTER the migration are pending. The columns and the UPDATE
-- are in ONE block that runs only while welcome_email_sent_at does not exist yet, so running the file again can never mark newer, still
-- pending users as welcomed. (ADD COLUMN ... NULL / DEFAULT <constant> is a metadata-only change in PostgreSQL 11+.)
DO $$
BEGIN
	IF NOT EXISTS (
		SELECT 1 FROM information_schema.columns
		WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = 'welcome_email_sent_at'
	) THEN
		ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_email_sent_at timestamptz NULL;
		ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_email_attempts int4 NOT NULL DEFAULT 0;
		ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_email_last_attempt_at timestamptz NULL;
		UPDATE users SET welcome_email_sent_at = now() WHERE welcome_email_sent_at IS NULL;
	END IF;
END $$;
