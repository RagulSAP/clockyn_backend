-- 035: user_face_index (face recognition on AWS Rekognition collections: ml/face_service.py).
-- Apply with the target schema on the search_path, e.g. python migrate.py <schema> migrations/035_user_face_index.sql
-- (the same statements are in punchapp_ddl_latest.sql as punchapp_prod.user_face_index). Safe to re-run; deletes nothing.
--
-- One row per face that was indexed into the Rekognition collection for a user (a registration indexes 4+ photos).
-- face_id is the FaceId Rekognition returned from IndexFaces. The rows are how the app knows (a) whether a user has
-- registered a face (any row) and (b) which Rekognition faces to delete on re-registration, staff deletion, the retention
-- purge and account deletion (DeleteFaces takes FaceIds). There is deliberately NO foreign key to users: the rows must
-- outlive a deleted users row until the Rekognition faces are confirmed deleted (a failed DeleteFaces keeps the rows so
-- the cleanup can be retried).
-- The old design kept .npy embeddings in S3 (known_faces/<user>/); those are NOT migrated: embeddings of the old model
-- cannot be searched in a Rekognition collection, so every user registers once again after the switch. Until then
-- GET /face/check/{id} answers registered=false and a punch answers code `not_registered` (the app's re-register flow).
-- No data pre-check is needed: the table is new, so the unique constraint on face_id cannot collide with existing rows.
CREATE TABLE IF NOT EXISTS user_face_index (
	id int8 GENERATED ALWAYS AS IDENTITY NOT NULL,
	user_id varchar(36) NOT NULL,
	face_id varchar(64) NOT NULL,
	photo_no int2 NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT user_face_index_pkey PRIMARY KEY (id),
	CONSTRAINT uq_user_face_index_face_id UNIQUE (face_id)
);
CREATE INDEX IF NOT EXISTS idx_user_face_index_user_id ON user_face_index USING btree (user_id);
