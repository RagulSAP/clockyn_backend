-- 030: requests and sessions (WP7). Apply with the target schema on the search_path, e.g.
--   python migrate.py <schema> migrations/030_requests_sessions.sql
-- Idempotent (safe to re-run) and it deletes nothing. The same index is in clockyn_ddl_latest.sql.
--
-- 1) Rate limit of device-change requests (3 per user per rolling 24 hours) counts a user's rows by type and age;
--    the pending-request lookup and the one-day shift override lookup use the same leading columns.
CREATE INDEX IF NOT EXISTS idx_requests_user_type_created ON requests USING btree (user_id, request_type, created_at);
--
-- 2) Leave requests used to be stored with a NULL status. New requests start as 'pending' and every decision
--    recomputes the request-level status from the per-date decisions (any date pending -> pending, else any
--    approved -> approved, else rejected: the app's deriveStatus). Bring older leave rows to the same rule so the
--    admin list filter ?status= matches them. Only rows whose stored value differs are touched, so a re-run is a no-op.
--    Pre-check (rows this will change):
--      SELECT count(*) FROM requests r WHERE r.request_type = 'leave' AND r.status IS NULL;
UPDATE requests r
SET status = x.agg, updated_at = now()
FROM (
	SELECT request_id,
	       CASE WHEN bool_or(COALESCE(status, 'pending') = 'pending') THEN 'pending'
	            WHEN bool_or(status = 'approved') THEN 'approved'
	            ELSE 'rejected' END AS agg
	FROM leave_dates
	GROUP BY request_id
) x
WHERE r.request_id = x.request_id AND r.request_type = 'leave' AND r.status IS DISTINCT FROM x.agg;
