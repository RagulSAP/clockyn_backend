-- 027: multi-device push tokens (services/push_service.py).
-- Apply with the target schema on the search_path, e.g. python migrate.py <schema> migrations/027_user_push_tokens.sql
-- (the same statements are in punchapp_ddl_latest.sql as punchapp_prod.user_push_tokens). Safe to re-run; deletes nothing.
-- users.push_token is NOT touched: it remains the legacy single token and the fallback for users with no row here.
-- Rows are created by POST /auth/update-push-token and by the login endpoints (validate-otp / verify-device-token),
-- and deleted when FCM reports a token as dead.
-- No data pre-check is needed: the table is new, so the unique constraint cannot collide with existing rows.
CREATE TABLE IF NOT EXISTS user_push_tokens (
	id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
	user_id varchar(36) NOT NULL,
	"token" text NOT NULL,
	platform varchar(16) NULL,
	device_id varchar(64) NULL,
	updated_at timestamptz DEFAULT now() NULL,
	CONSTRAINT user_push_tokens_pkey PRIMARY KEY (id),
	CONSTRAINT uq_user_push_tokens_user_token UNIQUE (user_id, "token")
);
CREATE INDEX IF NOT EXISTS idx_user_push_tokens_token ON user_push_tokens USING btree ("token");

-- Optional one-off back-fill so existing single-device users already have a row (idempotent). A token shared by
-- several users (one phone, several logins) is given to the most recently updated user only.
INSERT INTO user_push_tokens (user_id, "token")
SELECT DISTINCT ON (u.push_token) u.user_id, u.push_token
FROM users u
WHERE u.push_token IS NOT NULL AND u.push_token <> ''
ORDER BY u.push_token, u.updated_at DESC NULLS LAST
ON CONFLICT (user_id, "token") DO NOTHING;
