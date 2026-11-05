-- 024: OTP send throttle log (POST /auth/generate-otp).
-- Apply with the target schema on the search_path, e.g. python migrate.py <schema> migrations/024_otp_send_log.sql
-- (the same statements are in punchapp_ddl_latest.sql as punchapp_prod.otp_send_log). Safe to re-run.
CREATE TABLE IF NOT EXISTS otp_send_log (
	id int4 GENERATED ALWAYS AS IDENTITY( INCREMENT BY 1 MINVALUE 1 MAXVALUE 2147483647 START 1 CACHE 1 NO CYCLE) NOT NULL,
	contact varchar(255) NOT NULL,
	sent_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT otp_send_log_pkey PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS idx_otp_send_log_contact_sent ON otp_send_log USING btree (contact, sent_at);
