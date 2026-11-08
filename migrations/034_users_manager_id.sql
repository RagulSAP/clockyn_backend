-- 034: users.manager_id (WP6, planner D-10). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/034_users_manager_id.sql
-- Idempotent (safe to re-run). The same column and index are in clockyn_ddl_latest.sql (users).
--
-- users.manager_id: the manager a staff member reports to / was created for. users.parent_id stays the ORGANISATION id.
-- Until now the add-staff form wrote the chosen manager's user_id into parent_id, which made that staff member invisible
-- to every org-wide query and unable to sign in (the employee login joins org_info on parent_id). The code now writes
-- parent_id = organisation and manager_id = manager. manager_id also feeds the server-side manager scope
-- (core_functions/scope.py, flag ENFORCE_MANAGER_SCOPE).
--
-- 1) Column + index. ADD COLUMN ... NULL without a default is a metadata-only change (no rewrite, no long lock).
ALTER TABLE users ADD COLUMN IF NOT EXISTS manager_id varchar(36) NULL;
CREATE INDEX IF NOT EXISTS idx_users_manager_id ON users USING btree (manager_id);
--
-- 2) Backfill (the only data this migration touches; it only fills the NEW column, nothing else is changed):
--    a manager / staff row whose parent_id is NOT an organisation id but the user_id of another user was created
--    "under a manager" by the old code path; that user is the manager. The guard `manager_id IS NULL` makes it re-runnable.
--    Preview of the rows it will touch:
--      SELECT u.user_id, u.name, u.parent_id AS manager_user_id
--      FROM users u
--      WHERE u.manager_id IS NULL AND u.role_id IN (2, 3) AND u.parent_id IS NOT NULL
--        AND NOT EXISTS (SELECT 1 FROM org_info o WHERE o.org_id = u.parent_id)
--        AND EXISTS (SELECT 1 FROM users m WHERE m.user_id = u.parent_id AND m.user_id <> u.user_id);
UPDATE users u
SET manager_id = u.parent_id
WHERE u.manager_id IS NULL
  AND u.role_id IN (2, 3)
  AND u.parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM org_info o WHERE o.org_id = u.parent_id)
  AND EXISTS (SELECT 1 FROM users m WHERE m.user_id = u.parent_id AND m.user_id <> u.user_id);
--
-- NOT done here (owner decision, deliberately left out because it rewrites parent_id): re-pointing those legacy rows to
-- their organisation so every parent_id = org query sees them:
--   UPDATE users u SET parent_id = m.parent_id FROM users m
--   WHERE u.manager_id = m.user_id AND u.parent_id = u.manager_id AND m.parent_id IS NOT NULL;
-- The employee login and device-token lookups already resolve the organisation of such rows through manager_id.
