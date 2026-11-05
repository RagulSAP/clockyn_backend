# Face recognition & punch (write path) — backend context

Domain owner: BD3. Read paths of the same table are in `attendance_and_home.md`; location logging side effects in `staff_location.md`. Frontend: `C:\POC\pa_frontend\Clockyn\src\api\face.ts` (+ `attendance.ts`), notes `C:\POC\pa_frontend\project_complete_context\shared\api\face_recognition.md`.

> **CURRENT DESIGN (AWS Rekognition).** Face matching no longer runs locally: the `insightface` package, the `.npy` embeddings in S3 and `EMBEDDING_CACHE` are gone. Faces live in an AWS Rekognition collection behind `ml/face_service.py`; see **Section 9 (Rekognition design and setup)** for the flow, failure mapping, IAM policy, cost notes and the exact setup steps. Sections 4.1, 4.3, 4.5, 4.6 and the "Face tuning" table below describe the OLD local model and are kept for history; where they differ, section 9 wins. Endpoints, response shapes, `code` / `reason` vocabulary and the app contract are unchanged (one additive `reason`, `multiple_faces`, and one additive `code`, `spoof_detected`, which is only produced when `FACE_ANTISPOOF=1`).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/face_attendance.py` | 91 | 4 face/punch routes + 2 attendance read routes (mounted `app.py:50`, no prefix) |
| `core_functions/face_attendance_functions.py` | 449 | geofence check, enrolment, `punch_with_face`, `get_today_attendance` |
| `core_functions/face_recognition.py` | 39 | thin facade: re-exports `ml.face.encode_face`, `ml.s3.save/load_face_embeddings`, `EMBEDDING_CACHE`; defines `verify_face` (threshold 0.62) |
| `ml/face.py` | 239 | model loading (InsightFace `buffalo_l` + ONNX anti-spoof session), `encode_face`, cache, unused global matcher |
| `ml/antispoof.py` | 25 | `check_liveness` (Laplacian variance); **unused** (no import anywhere) |
| `ml/s3.py` | 422 | S3 helpers: embeddings, profile photo, punch photos, org logo, chat/support files, employee asset deletion |
| `ml/models/modelrgb.onnx` | 1.36 MB | RGB anti-spoof model (loaded at import; its check is commented out) |
| `known_faces/` | 6 sub-folders | local leftovers (see below) |
| `core_functions/holiday_functions.py:140-170` | - | `check_holiday_block` used before punch-in |
| `core_functions/gen_plans_functions.py:167-196` | - | `check_plan_feature` gate |
| `core_functions/auth_function.py:211-281` | - | `send_punch_in_out_notification` (admin/manager push + WebSocket badge) |

Frontend callers: `src/api/face.ts` (`/face/check/{id}`, `/face/check-location`, `/face/punch`, `/face/register`), `src/api/attendance.ts` (`/attendance/today`, `/attendance/sessions`); UI `src/features/employee/components/FaceRecognitionModal.tsx`, `src/features/employee/utils/punchFlow.ts`, `punchState.ts`.

`known_faces/` (repo folder, not read by code): six UUID-named sub-folders each with four `.npy` files of 2,176 bytes. Nothing in the code opens this directory; embeddings live in S3 under prefix `known_faces/{user_id}/` (`ml/s3.py:16,26-27`). The local files look like `np.save` output (2,048 bytes of float32 payload plus a 128-byte header, UNVERIFIED origin: dev/export leftovers) whereas S3 objects are raw `tobytes()` blobs (`ml/s3.py:62`) read with `np.frombuffer` (`:94`); copying the local files to S3 would yield 544-float vectors and break matching. They are biometric data of real users: do not commit/ship them (the folder is currently untracked; no `.dockerignore` exists, and the Dockerfile copies the whole tree, `Dockerfile` `COPY . .`).

## 2. Data model touched

| Table | Use |
|---|---|
| `attendance` (see `attendance_and_home.md` §2) | INSERT on punch-in (`user_id, org_id, attendance_date, punch_in, punch_in_location, face_mismatch, created_at, updated_at`, `RETURNING id`); UPDATE on punch-out (`punch_out, work_hours, punch_out_location, updated_at, face_mismatch`); background UPDATE of `punchinface` / `punchoutface` |
| `staff_location_logs` (dump:955-965: `id, user_id, org_id, location point, date_time timestamp`) | one row per punch-in / punch-out WITH coordinates, for ALL roles (`face_attendance_functions.py:216-224,309-317`) |
| `users` | `is_field_staff, branch_id, role_id, org_id, parent_id` of the punching user |
| `branch_info` | `gps_location (lng,lat)`, `punch_radius` (default 100) |
| `org_holidays` | `check_holiday_block`: `date = CURRENT_DATE AND block_attendance = TRUE AND (branch_name IS NULL OR = user branch) AND (dept_name IS NULL OR = user dept)`; the dump lacks `block_attendance` (see D-FP-08) |
| plan tables (via `get_org_plan_status`) | `camera_punch` gate |
| S3 objects | `known_faces/{user_id}/{uuid}.npy` (raw float32 512-d), `employee_profile/{user_id}.jpg`, `empolyee-punch-photos/{org_id}/{user_id}/{attendance_id}_{punch_in|punch_out}.jpg` (prefix spelled this way in code, `ml/s3.py:187`) |

Biometric record format: up to 4 embeddings per user (`save_face_embeddings(max_faces=4)`, `ml/s3.py:30-69`), each 512 float32 (InsightFace `normed_embedding`) = 2,048 bytes, no header, random UUID file names. Images are not stored for enrolment except the first image, saved as the profile photo (`face_attendance_functions.py:101-102`).

## 3. Endpoints

All: auth none (client-supplied `user_id`/`org_id`, P3-1). Plan gate = `check_plan_feature(org_id, 'camera_punch', is_admin=False)` where `org_id` is the client form value (403 `{detail}`: expired plan only, see 4.9). Handler lines in `router/face_attendance.py` (RT) / `face_attendance_functions.py` (FAF).

| # | METHOD path | Handler | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| 1 | GET `/face/check/{user_id}` | RT:12 -> FAF:24 `check_face_registered` | path `user_id` | `{registered: bool}` | no gate; **now** one `user_face_index` lookup (Rekognition design, section 9); DB failure -> 500 `Failed to check face registration` |
| 2 | POST `/face/check-location` | RT:18 -> FAF:30 `check_punch_location` | form: `user_id`, `latitude` float, `longitude` float (all required) | `{success:true,message:"Location check passed"}` or `{success:false,message:"You are {N}m away from your branch. Must be within {R}m to punch."}` | always HTTP 200; `{success:false,message:"User not found"}`; `{success:false,message:"Location check failed: ..."}`; field staff and branches without GPS always pass; no gate |
| 3 | POST `/face/register` (Rekognition: section 9) | RT:28 -> FAF:80 `register_face` | multipart: `user_id`, `org_id`, `faces` (>=4 files) | `{success:true,message:"Face registered successfully"}` | gate first (403); fewer than 4 files -> 400 `Please provide at least 4 face images`; a file with no usable face -> 400 with the encoder message (`No face detected` / `Face not captured properly`); S3 failure -> 500 `Failed to register face: ...`; **no ownership check: any caller can overwrite any user's face** |
| 4 | POST `/face/punch` | RT:38 -> FAF:108 `punch_with_face` | multipart: `user_id`, `org_id`, `action` (`punch_in`/`punch_out`), `face` file, `latitude?` float, `longitude?` float, `force_accept` bool (default false) | punch-in `{success:true,message:"Punched in successfully",attendance_id,time:"hh:mm AM/PM" IST,face_mismatch}`; punch-out `{success:true,message:"Punched out successfully",time,work_hours,total_work_hours,face_mismatch}` | business failures are **HTTP 200 `{success:false,message}`** (list in 4.2); gate 403; holiday `{success:false,message:"Today is a holiday (<name>). Attendance is not required.",is_holiday:true}` |
| 5 | GET `/attendance/today/{user_id}` | RT:52 -> FAF:388 | path | see `attendance_and_home.md` #23 | |
| 6 | GET `/attendance/sessions/{user_id}?date=` | RT:58 | | see `attendance_and_home.md` #24 | |
| 7 | GET `/face/warmup/{user_id}` (new) | `router/face_attendance.py` -> `warmup_user` | path | `{success:true, registered:bool, ms:int}`; **now** only reports whether the user has registered a face (nothing to pre-load with Rekognition; call when the punch screen opens); unregistered -> `registered:false`, no error | DB failure -> `success:false, registered:false` (logged); no plan gate |

**Changes (WP5, migration 028):** `POST /face/punch` uses the IST business date for `attendance_date` and the 2-session count; the advisory lock is per user (`hashtext(user_id)`); an open session blocks punch-in only while it is younger than 18 h, an older one (forgotten punch-out, D-22) is auto-closed at the shift end (punch-in + 9 h without a shift) with `auto_closed = TRUE` and the punch-in proceeds, the response gaining `auto_closed_previous: true` (only then); punch-out of such a stale session answers `no_active_punch`; the location inserts run in `SAVEPOINT sp_punch_location`; `GET /attendance/today/{user_id}` and `GET /attendance/sessions/{user_id}` follow the IST day (details in `attendance_and_home.md` section 3, Changes). Open question handled: two sessions per day now work because the old unique index is dropped (`ux_attendance_one_open` replaces it).

**Tier A additions (additive, existing `success`/`message` unchanged):**
- `POST /face/punch` — every response carries `code`: success `ok`; failures `holiday_blocked | not_registered | image_unreadable | no_face | no_match | out_of_range | location_required | already_punched_in | max_sessions | no_active_punch | invalid_action | server_error`. `no_face`/`image_unreadable` also carry `reason` (`too_dark | too_blurry | too_small | unknown`), computed only after a failed detection (an image the detector accepts is never rejected). `server_error` now returns the generic message "Something went wrong. Please try again." (the exception is logged, no SQL/driver text is returned).
- `POST /face/register` — failures keep HTTP 400 and the string `detail` but the JSON body is `{detail, code, reason?}` (`code`: `insufficient_images | no_face | image_unreadable`); S3/other failure -> HTTP 500 `{detail:"Failed to register face. Please try again.", code:"server_error"}`; success gains `code:"ok"`.
- Inference (`encode_face`/`verify_face`) runs on a bounded thread pool (`FACE_MAX_CONCURRENCY`), the event loop is no longer blocked. A model warm-up runs in a background thread at startup (`app.py` startup hook).
- One JSON line per attempt on logger `face.metrics` (own handler, stdout) and a best-effort row in `face_attempt_log` (own short DB connection, errors swallowed).

**Changes (AWS Rekognition, migration 035):** matching moved to an AWS Rekognition collection (section 9). Contract unchanged: same endpoints, response shapes and `code`s. `GET /face/check/{id}` and `GET /face/warmup/{id}` now answer from `user_face_index` (one indexed lookup; no S3, nothing is cached in the process; warm-up pre-loads nothing and only reports `registered`); `POST /face/register` still needs >= 4 photos and every photo must show exactly one face; `POST /face/punch` matches when the best Rekognition similarity of THIS user is `>= REKOGNITION_THRESHOLD` (default 90). Additive only: `reason: "multiple_faces"` (several people in the frame), `code: "spoof_detected"` (only with `FACE_ANTISPOOF=1`), `face_cleanup_failed` in the purge result, and a 503 for register when Rekognition is throttled / unreachable (punch answers `server_error` with a "try again" message). Users registered under the old model must register once again.

## 4. Business logic

### 4.1 Face encoding (`ml/face.py:138-180`)
1. `load_image` (`:76-89`): PIL decode -> RGB numpy; if width > 480 px resize to width 480 keeping aspect (downscale only); any decode error -> `None` -> `{success:false,"Face not captured properly"}`. No size limit on the upload (`await face.read()`).
2. `face_model.get(image)` (InsightFace `buffalo_l`, CPU provider, `det_size=(320,320)`, `ml/face.py:48-52`). No faces -> `"No face detected"`.
3. Largest bbox area wins (`:156`); multiple faces are not rejected. Empty crop -> `"Face not captured properly"`.
4. Anti-spoofing is **disabled**: the block calling `anti_spoof_check(face_crop)` is commented out (`:167-175`, "disabled for testing"). `anti_spoof_check` (ONNX `modelrgb.onnx`, score < 0.5 = real, `:105-132`) and `antispoof.check_liveness` are unreachable. The ONNX session is still created at import (`:59-64`) with a CWD-relative path `ml/models/modelrgb.onnx` (import fails if the process starts outside the repo root).
5. Returns `face.normed_embedding` float32 (512-d).

### 4.2 `POST /face/punch` (`FAF:108-385`) — step by step
1. **Holiday gate (punch-in only)**: `check_holiday_block(org_id, user_id)` (`:114-122`, outside any try; a DB error here, e.g. missing `block_attendance` column, becomes an unhandled 500). Uses DB `CURRENT_DATE` and the client `org_id`.
2. **Registered?** embeddings from the in-process `EMBEDDING_CACHE` (dict `user_id -> list[ndarray]`), loading from S3 on a miss or empty list (`:126-129`). None -> `{success:false,"Face not registered. Please register your face first."}`. The cache never expires and is per process.
3. Read image bytes. **If `force_accept`**: skip detection/matching entirely, `face_mismatch = true` (`:134-136`) — the photo is not even decoded. **Else** `encode_face(img_bytes)` (failure -> `{success:false,message}` with the encoder text) then `verify_face` (`face_recognition.py:15-39`): average of the stored embeddings (L2-normalised) dot the query (normalised) = cosine score; `score >= 0.62` -> match (constant duplicated at `face_recognition.py:35` and `ml/face.py:54`); else `{success:false,"Face not recognized"}` (score not returned or logged).
4. **Geofence** (`:149-178`), single DB connection: load `is_field_staff, branch_id, role_id, org_id, parent_id`, branch `gps_location[0]/[1]` and `punch_radius` (join on `branch_id` only, no org scope). If the user exists, is NOT field staff and the branch has GPS (`branch_lat and branch_lng` truthy):
   - coordinates given -> haversine distance (R = 6,371,000 m); `distance > punch_radius` -> `{success:false,"You are {int(distance)}m away from your branch. Must be within {R}m to punch."}`;
   - coordinates missing -> `{success:false,"Location access is required to punch in/out."}`.
   Not enforced when: field staff, user has no branch / branch without GPS, user row missing. **`force_accept` does not bypass the geofence.** Applies identically to punch-out. The coordinates are client-reported (spoofable) and no accuracy/mock-location check exists.
5. `now = get_utc_now()`; `today = now.date()` = **UTC date** (`:180-181`).
6. **punch_in** (`:184-273`):
   a. `pg_advisory_xact_lock(hashtext(user_id || today))` (serialises concurrent punch-ins of the same user+UTC-date, `:187-190`).
   b. Any row with `punch_out IS NULL` for the user (any date) -> `{success:false,"Already punched in. Please punch out first."}` (`:194-201`). A forgotten open session from an earlier day therefore blocks all future punch-ins (no auto-close).
   c. `COUNT(*) WHERE user_id AND attendance_date=today >= 2` -> `{success:false,"Maximum 2 punch-in sessions allowed per day."}` (`:204-209`). **This is a server-side rule** (hard-coded 2, counts rows of the UTC date, regardless of closed/open).
   d. If coordinates given: `location_point = "(lng,lat)"` and INSERT into `staff_location_logs (user_id, org_id=<client>, POINT(lng,lat), now)`; failure of the log insert is swallowed (`:213-224`) — but a failed statement in PostgreSQL aborts the transaction, so the next statement then fails (D-FP-09).
   e. `INSERT INTO attendance (...) RETURNING id` and `COMMIT` (`:226-233`).
   f. Background task `_save_punch_in_photo` (`:236-251`, `asyncio.ensure_future`): `save_punch_image` in the default executor -> S3 PUT + presigned URL, then `UPDATE attendance SET punchinface = <url>`; all errors only printed.
   g. For role 2/3: format IST time, **evaluate `send_punch_in_out_notification(..., image_url=img_url)`** (`:253-265`) -> `NameError` (see D-FP-01).
   h. Return `{success:true,...,attendance_id,time,face_mismatch}` (never reached for roles 2/3).
7. **punch_out** (`:275-370`): latest row with `punch_out IS NULL` for the user (any date) else `{success:false,"No active punch in found"}`; `work_hours = round((now - punch_in)/3600, 2)` (naive punch_in treated as UTC); `prev_hours = SUM(work_hours)` of closed rows of the same `attendance_date`; `total_work_hours = round(prev + work, 2)`; staff_location_logs insert (if coordinates; swallowed errors); `UPDATE attendance SET punch_out, work_hours, punch_out_location = POINT(%s,%s) (NULL,NULL when no coordinates), updated_at, face_mismatch = <current attempt's flag>`; COMMIT; background photo upload into `punchoutface`; role 2/3 notification with `image_url=None` (`:347,:360`); response `{success,message,time,work_hours,total_work_hours,face_mismatch}`.
   Note: `face_mismatch` is overwritten by the punch-out attempt; an earlier mismatch on punch-in is lost.
8. Invalid `action` -> `{success:false,"Invalid action"}`; any exception inside the DB block: `rollback`, `{success:false,"Database error: <exception text>"}` (`:375-378`); outer catch-all `{success:false,"Error: ..."}` (`:383-385`). Exception text (SQL, driver) is returned to the client.

### 4.3 `POST /face/register` (`FAF:80-105`)
1. `len(faces) < 4` -> 400.
2. For each file: read bytes, `encode_face(img, for_enroll=True)` (same as punch; anti-spoof flag has no effect now). First failing file -> 400 with its message (so one bad frame aborts enrolment).
3. `save_face_embeddings`: lists S3 prefix, **deletes all existing `.npy`** objects, writes at most 4 embeddings (extra faces beyond the 4th are validated but dropped) with `uuid4` names; raises if none saved.
4. `EMBEDDING_CACHE.pop(user_id)` (this process only).
5. `save_employee_profile(user_id, first_image_bytes)` -> `employee_profile/{user_id}.jpg` overwritten with the first enrolment frame.
No check that the caller is the user or an admin, that a face is already registered, or that the face is already enrolled under another user.

### 4.4 `POST /face/check-location` (`FAF:30-77`)
Same branch/radius lookup as 4.2 step 4: unknown user -> `{success:false,"User not found"}`; field staff -> pass; no branch GPS -> pass; distance > radius -> fail message; exception -> `{success:false,"Location check failed: ..."}`. Returns HTTP 200 in all cases. It uses `punch_radius` default 100 (`COALESCE`).

### 4.5 Model loading cost / startup
- Importing `router.face_attendance` -> `face_attendance_functions` -> `core_functions.face_recognition` -> `ml.face` loads at process start: `FaceAnalysis("buffalo_l")` (detection + recognition + landmark + genderage ONNX models via `prepare`, `ml/face.py:48-52`) and the anti-spoof ONNX session (`:59-64`). Stdout/stderr are redirected to `os.devnull` during load (`SuppressOutput`, `:12-24`), so load failures are silent until the exception propagates.
- InsightFace downloads `buffalo_l` into its model home (default `~/.insightface/models`, ~hundreds of MB) on first start if absent; the Dockerfile does not pre-fetch it and sets no model volume, so a fresh container needs outbound network on first boot and a writable home (UNVERIFIED for the deployed image).
- CPU only (`CPUExecutionProvider`), `det_size 320`; `encode_face` is synchronous CPU work executed inside an `async def` handler, so each punch blocks the event loop for the duration of detection + embedding. Comment at `ml/face.py:46` records that switching to `buffalo_sc` requires re-enrolling every user (different embedding space).
- Single global `EMBEDDING_CACHE` per worker: not shared across uvicorn workers/containers; a re-enrolment is invisible to other processes until restart.

### 4.6 Image / embedding storage
| Item | Where | Stored in DB as |
|---|---|---|
| embeddings | S3 `known_faces/{user_id}/{uuid}.npy` | n/a |
| profile photo | S3 `employee_profile/{user_id}.jpg` (reads also try `.png`) | not stored; presigned on read (`ml/s3.py:170-183`) |
| punch photos | S3 `empolyee-punch-photos/{org_id}/{user_id}/{attendance_id}_{action}.jpg` | **full presigned URL** (7-day TTL) in `attendance.punchinface` / `punchoutface`; re-signed on read by `refresh_punch_image_url` which extracts the key from the URL path and does a `head_object` (`ml/s3.py:401-423`). If the stored URL is path-style, the key fails the prefix test and the stale URL is returned unchanged; if the S3 HEAD fails the stale URL is returned. |
Key is derivable from `(org_id, user_id, attendance_id, action)` (`get_punch_image_url`, `ml/s3.py:215-230`), so storing only the key (or nothing) is possible.

### 4.7 Notification path (role 2/3 only)
`send_punch_in_out_notification(user_id, org_id=<user.parent_id or client org_id>, punch_type, punch_time "hh:mm AM" IST, image_url)` (`auth_function.py:211-281`): resolves the employee name; recipients = admin of the org (`org_info.parent_id`) and managers with `parent_id = org` that have a `push_token`; WebSocket `badge_update` is sent to every admin/manager of the org (also without push token); push title `Punch In`/`Punch Out`, body `"{name} punch in at {time}"`, data `{type, user_id, image_url}`. Executed with `asyncio.ensure_future` (fire-and-forget).

### 4.8 Concurrency
Advisory lock only covers punch-in (per user + UTC date). Punch-out has no lock: two simultaneous punch-outs can both update the same open row (last wins). The `Already punched in` check runs before the lock-protected insert only in the same transaction, which is correct for punch-ins.

### 4.9 Plan gate
`check_plan_feature` (`gen_plans_functions.py:174-196`): free trial -> allow; expired -> 403 for everything; otherwise blocked set per plan (`PLAN_BLOCKED_FEATURES`, `:167-171`): Core `{staff_location, crm}`, Prime `{crm}`, Elite none. `camera_punch` is therefore never blocked by tier (the docstring claiming otherwise is stale, P2-2). The gate uses the client-provided `org_id`, so a caller can pass an org with an active plan.

## 5. Side effects & integrations
| Integration | Detail |
|---|---|
| S3 (boto3) | read/write embeddings, profile photo, punch photos; env `AWS_*`, `S3_BUCKET_NAME` |
| PostgreSQL | attendance, staff_location_logs, users, branch_info, org_holidays |
| FCM push + WebSocket | `send_punch_in_out_notification` (role 2/3 punches), push payload `{type:"punch_in"|"punch_out", user_id, image_url}`; WS `badge_update` via `router/ws_badge.notify_badge_update` |
| ML | InsightFace `buffalo_l` (onnxruntime CPU), ONNX anti-spoof (unused), OpenCV, Pillow, NumPy |

## 6. Config & env vars
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME` (`ml/s3.py:10-14`); `DB_SCHEMA`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`; push uses the `FCM_*` variables (names in `backend_changes.md` P0-9, defined in `auth_function.py`, not copied here). Hard-coded: similarity threshold 0.62 (`ml/face.py:54`, `face_recognition.py:35`), max 2 sessions (`FAF:208`), min 4 enrolment photos (`FAF:82`), max 4 stored embeddings (`ml/s3.py:33`), image width cap 480 px, `det_size` 320, presign TTL 604800 s, default radius 100 m. Env flags set inside `ml/face.py` (`TF_CPP_MIN_LOG_LEVEL`, `GLOG_minloglevel`). No variable controls anti-spoof, thresholds or model name.

## 7. Known defects & risks

| ID | Sev | Where | Defect / impact |
|---|---|---|---|
| D-FP-01 | **Critical** | `FAF:264` | **FIXED (Tier A):** the notification is now scheduled with `image_url=None` through a wrapper that logs and swallows any notification failure, so a committed punch is never reported as `success:false`; regression tests in `tests/test_face_tier_a.py`. Original defect: **`img_url` is undefined** (grep: the only other names are `punch_out_img_url`, `:347,:360`; no module-level or import definition). For every punch-in by a role 2/3 user the row is inserted and committed (`:226-233`), the photo upload task is already scheduled (`:251`), then the call `send_punch_in_out_notification(..., image_url=img_url)` raises `NameError` while evaluating its arguments (so no notification and no WS badge are sent), the exception is caught by `except Exception` (`:375`) -> `rollback()` (no-op after commit) -> response `{success:false,"Database error: name 'img_url' is not defined"}`. The staff member sees a failure while attendance WAS recorded; a retry returns `Already punched in`. Role 1 (admin) punches are unaffected. Verified by static reading (UNVERIFIED at runtime; the deployed server may be patched, `backend_changes.md` P1-8). |
| D-FP-02 | High | `FAF:132-136`, `router:46` | `force_accept=true` skips face matching AND image decoding entirely; any API caller can punch in/out as any `user_id` with an empty/garbage file (needs only geofence compliance, which uses client coordinates). The flag only sets `face_mismatch`, which no endpoint ever reads (grep: written at `FAF:227-230,319-324` only) so "flagged for admin review" has no review surface. Punch-out resets the flag (`:322-324`). (P3-3, DD-01) |
| D-FP-03 | High | `FAF:80-105`, `router:28-35` | `/face/register` has no authentication/ownership check and deletes the user's existing embeddings first: anyone can re-enrol a colleague with their own face (then punch as them) or wipe a face. |
| D-FP-04 | High | `FAF:203-209` vs dump:150 | 2-session rule vs UNIQUE `(user_id, attendance_date)` index in the schema dump: if the index exists in production the second punch-in fails with `Database error: duplicate key value ...`. See `attendance_and_home.md` D-AH-02. |
| D-FP-05 | High | `ml/face.py:167-175` | Anti-spoofing disabled ("for testing"); printed/photo attacks pass if similarity >= 0.62; `antispoof.py` and ONNX scoring unused although the model is loaded (wasted memory/startup). |
| D-FP-06 | High | `FAF:180-181,204-209,394` | UTC `attendance_date`: IST punches before 05:30 belong to the previous day; the 2-session limit resets at 05:30 IST instead of midnight; `/attendance/today` loses an open overnight session after UTC midnight (P2-5). |
| D-FP-07 | Med | `FAF:194-201` | Any open session (even from a previous day, e.g. forgot punch-out) blocks new punch-ins forever; no server-side auto-close or admin close endpoint other than the date-based edit (which cannot clear a punch). |
| D-FP-08 | Med | `FAF:114-122`, `holiday_functions.py:47` | `check_holiday_block` runs outside try; the schema dump has no `block_attendance` column, so on a DB created from the dump every punch-in would 500 (deployed state UNVERIFIED, P1-5). Uses DB `CURRENT_DATE`. |
| D-FP-09 | Med | `FAF:216-224,309-317` | The "do not fail punch if location logging fails" try/except cannot protect the transaction: a failed INSERT leaves the transaction aborted, the following attendance statement then raises and the punch fails. Location rows are also written for non-field staff with client `org_id`. |
| D-FP-10 | Med | `FAF:126,18-19` (cache) | `EMBEDDING_CACHE` is never invalidated outside the enrolling process and never expires; empty lists are re-fetched from S3 on every punch of an unregistered user. Multi-instance deployments serve stale embeddings after re-enrolment. |
| D-FP-11 | Med | `FAF:108-130,138` | **MITIGATED (Tier A):** inference now runs on a bounded thread pool (`FACE_MAX_CONCURRENCY`, default 2) and the model is warmed at startup; model load at import time (memory, optional download) is unchanged. Original: `encode_face`/ONNX inference runs synchronously in the event loop; one punch blocks all requests. Whole model stack loads at import (startup time/memory, optional network download). |
| D-FP-12 | Med | `FAF:149-160,35-44` | geofence uses a `branch_info` join on `branch_id` only (not org-scoped, per-org counters) -> may use another org's coordinates; `fetchone` picks an arbitrary row when duplicates exist. Also `if not branch_lat or not branch_lng` treats a coordinate of exactly 0 as "no GPS". |
| D-FP-13 | Med | `FAF:183-378` | Error text (`Database error: ...`, `Error: ...`) leaks SQL/driver details; every business failure is HTTP 200 (client must read `success`); the client's retry-with-`force_accept` logic masks failures (`DD-01`). |
| D-FP-14 | Low | `FAF:182` | `location_str` computed and unused; `latitude and longitude` truthiness would drop 0.0. |
| D-FP-15 | Low | `FAF:347,360` | punch-out notification always carries `image_url=None`; punch-in intent (`img_url`) was to attach the photo, which is uploaded asynchronously and does not exist yet at notification time. |
| D-FP-16 | Low | `FAF:113-129,383-385` | `org_id` from the client is stored in `attendance.org_id` and used for the plan gate and holiday check without verifying it matches the user's org. |
| D-FP-17 | Low | `known_faces/`, `Dockerfile` | local biometric files in the build context; no `.dockerignore`. |

## 8. Clockyn requirements

### R-FP-1 Fix `img_url` NameError — P1-8 (highest P1) [V]
- **Current:** D-FP-01. The client works around it by re-reading `/attendance/today` after a failed forced punch (`FaceRecognitionModal.tsx`).
- **Required:** punch-in succeeds for roles 2/3 and admins are notified.
- **Outline (`face_attendance_functions.py`):** (1) replace `image_url=img_url` by `image_url=None` (minimal fix) or better (2) move the `send_punch_in_out_notification(...)` scheduling into `_save_punch_in_photo` after `save_punch_image` returned the URL (`:236-251`) so the push carries the photo; keep a no-photo fallback when the upload fails. Move all notification scheduling after the response payload is built and wrap it in `try/except` so notification errors can never turn a committed punch into `success:false`. Add a regression test for roles 2 and 3.
- **Back-compat:** none; response shape unchanged.
- **Acceptance:** role 3 punch-in -> `success:true`, one attendance row, one notification attempt; role 1 unchanged; notification failure (raise inside sender) still `success:true`.

### R-FP-2 `force_accept` semantics — P3-3, DD-01, DD-02 [V]
- **Current:** skips matching, still enforces geofence for non-field staff with GPS branch (so the v1 "geofence is advisory" statement is false); no review UI/endpoint.
- **Required (needs product decision DD-01):** accept the flag only (a) after N failed matches recorded server-side for the same user within a window, or (b) with an admin-issued override token; add a response field `status: "accepted"|"pending_review"`; add an admin endpoint to list and resolve `face_mismatch` rows.
- **Outline:** table `punch_review (attendance_id, user_id, org_id, reason, status, reviewed_by, reviewed_at)` or columns on `attendance`; in `punch_with_face` record failed attempts (`face_attempts` counter in a small table with TTL) and reject `force_accept` when `attempts < N`; return HTTP 403/409 code `FORCE_NOT_ALLOWED`; keep `face_mismatch` semantic per session and do not overwrite on punch-out (use separate `punch_in_mismatch`/`punch_out_mismatch`).
- **Back-compat:** existing clients retry with `force_accept` after any failure; ship server rule first behind a feature flag, then update the client (`DD-01` option c).
- **Acceptance:** `force_accept=true` as first attempt -> rejected; after N non-matching attempts -> accepted with `face_mismatch:true` and a review record; geofence still enforced.

### R-FP-3 Geofence enforcement contract — DD-02, P1-2 [V]
- **Current:** enforced server-side on punch-in and punch-out for non-field staff when branch has GPS; missing coordinates -> `Location access is required to punch in/out.`; `force_accept` does not bypass; `/face/check-location` mirrors it; branch has no `branch_id` on staff detail endpoints.
- **Required:** keep behaviour, document it, expose `branch_id`/geofence in staff detail (see `attendance_and_home.md` R-3), scope the branch lookup by org, return a machine-readable `code` (`OUTSIDE_GEOFENCE`, `LOCATION_REQUIRED`) alongside `message`.
- **Outline:** `LEFT JOIN branch_info b ON u.branch_id=b.branch_id AND b.org_id = u.parent_id` in both queries (`FAF:35-43,151-159`); add `code` keys (additive).
- **Acceptance:** staff 150 m from a 100 m branch -> `success:false,code:"OUTSIDE_GEOFENCE"`; field staff passes anywhere; same user ids in two orgs with identical `branch_id` use their own org's branch.

### R-FP-4 2-session/day rule and IST day — DD-04, DD-05, P2-5 [V]
- **Current:** server-side, hard-coded 2, counted per UTC date.
- **Required:** count per IST date of the session start; keep limit configurable (`MAX_SESSIONS_PER_DAY` env or org setting); DB guard (partial unique index on open sessions) and drop the unique `(user_id, attendance_date)` index.
- **Outline:** `face_attendance_functions.py:180-181` -> `today = convert_utc_to_ist(now).date()`; `:204-209` unchanged logic on that date; migration `DROP INDEX attendance_user_id_attendance_date_key; CREATE UNIQUE INDEX uq_attendance_open ON attendance(user_id) WHERE punch_out IS NULL;` plus the date back-fill from `attendance_and_home.md` R-4.
- **Back-compat:** message text unchanged; frontend limit logic (`punchState.ts`) unchanged.
- **Acceptance:** two sessions at 23:00 IST and 00:30 IST are on different IST dates; third session the same IST date -> `Maximum 2 punch-in sessions allowed per day.`; concurrent punch-ins -> one succeeds.

### R-FP-5 Anti-spoof and model operations — new (security hardening) [V]
- **Current:** disabled; models load at import.
- **Required:** decision to enable (product) or remove dead code; model pre-baked into the image; lazy/async inference.
- **Outline:** re-enable `ml/face.py:167-175` behind env `ANTISPOOF_ENABLED` (default false until validated); copy `buffalo_l` into the image during build (`insightface` model home set via `INSIGHTFACE_HOME`/`~/.insightface`, UNVERIFIED variable name for the installed version) and resolve the ONNX path from `__file__`; run `encode_face` in `run_in_executor`.
- **Acceptance:** cold start with no network succeeds; event loop not blocked (parallel `/health`-style requests during a punch stay fast).

### R-FP-6 Auth for enrolment — P3-1 [V]
- **Required:** `/face/register` only for the user themself (token) or an admin of the same org; reject duplicate-face enrolment (compare against `verify_face_global`-style search within the org).
- **Acceptance:** register for another user's id without privilege -> 403.

### R-FP-7 Photo storage — new [V]
- **Current:** DB stores 7-day presigned URLs; reads re-sign with HEAD.
- **Required:** store the S3 **key** (or compute from ids) and sign on read without HEAD; migrate existing URLs with `ml/s3.py:_extract_s3_key`.
- **Acceptance:** monthly attendance for 31 days issues zero HEAD calls.

### Face tuning (Tier A reliability knobs)
All are env vars with safe defaults; none changes the embedding space (still `buffalo_l`, no re-enrolment). Invalid or out-of-range values silently fall back to the default.

| Var | Default | Effect |
|---|---|---|
| `FACE_MAX_WIDTH` | 640 | uploads wider than this are downscaled (was 480). Higher = better for small/distant faces, slower |
| `FACE_DET_SIZE` | 480 | square detector input, rounded to a multiple of 32 (was 320). 320 fastest; 480 finds smaller / off-centre faces at roughly 1.5-2x detection time; 640 most sensitive and slowest |
| `FACE_SIMILARITY_THRESHOLD` | 0.62 | cosine threshold for a match (0-1). Raise to reduce false accepts, lower to reduce false rejects; tune using `face_attempt_log` |
| `FACE_MAX_CONCURRENCY` | 2 | size of the inference thread pool (extra requests queue); set near the CPU core count per worker |
| `FACE_ATTEMPT_LOG` | 1 | `0` disables the best-effort `face_attempt_log` inserts (the `face.metrics` JSON log line is always written) |
| `FACE_DARK_THRESHOLD` / `FACE_BLUR_THRESHOLD` / `FACE_MIN_SIDE` | 45 / 40 / 200 | failure-reason classifier limits (mean luminance, variance of Laplacian, shorter side in px); diagnostics only |

Also: EXIF orientation is now applied when decoding. Review queries (PostgreSQL), `outcome` values are the punch `code`s plus `ok`; `score` is only set when a match was computed:

```sql
-- false-reject proxy by hour: share of non-forced attempts that failed on a face reason
SELECT date_trunc('hour', created_at) AS hr,
       count(*) AS attempts,
       round(100.0 * count(*) FILTER (WHERE outcome IN ('no_match','no_face','image_unreadable')) / count(*), 1) AS pct_face_fail
FROM face_attempt_log
WHERE action IN ('punch_in','punch_out') AND NOT forced AND created_at > now() - interval '14 days'
GROUP BY 1 ORDER BY 1;

-- score distribution: successful matches vs no_match (look for overlap around the threshold)
SELECT outcome, width_bucket(score, 0, 1, 20) AS bucket, count(*)
FROM face_attempt_log
WHERE outcome IN ('ok','no_match') AND score IS NOT NULL AND NOT forced
GROUP BY 1, 2 ORDER BY 1, 2;

-- why detection fails (needs the quality columns): average brightness/blur of failed vs ok attempts
SELECT outcome, count(*), round(avg(brightness)::numeric,1) AS avg_brightness, round(avg(blur)::numeric,1) AS avg_blur, round(avg(ms_total)) AS avg_ms
FROM face_attempt_log GROUP BY outcome ORDER BY count(*) DESC;
```
The table is created by `punchapp_ddl_latest.sql` (`face_attempt_log`, index `(org_id, created_at)`); apply that `CREATE TABLE` on existing databases before enabling `FACE_ATTEMPT_LOG` (until then inserts are skipped with a warning).

## 9. Rekognition design and setup (current)

### 9.1 Files
| File | Role |
|---|---|
| `ml/face_service.py` | the facade the app calls: `register(user_id, photos)`, `verify(user_id, photo)`, `identify(photo)`, `remove(user_id)`, `is_registered(user_id)`, `warmup()`; backend per env `FACE_BACKEND` (`rekognition` default, `mock`); `user_face_index` access (`DbFaceStore`) |
| `ml/rekognition_face.py` | the only module that calls AWS: `DetectFaces`, `IndexFaces`, `SearchFacesByImage`, `DeleteFaces` (+ `CreateCollection` / `DescribeCollection`), error mapping, image fitting (JPEG/PNG, <= 5 MB, longest side <= 1920 px, EXIF applied), user id <-> `ExternalImageId` mapping |
| `ml/face.py` | local only: decode + EXIF + quality numbers + hard pre-check (`prepare_face`), anti-spoof (`anti_spoof_check`, `crop_face`), `warmup_model` hook (no-op for Rekognition) |
| `core_functions/face_recognition.py` | glue `prepare_face` / `verify_face` run on the face thread pool |
| `core_functions/face_attendance_functions.py` | register / punch / check / warm-up flows (unchanged contract) |
| `core_functions/data_lifecycle.py` (`remove_face_data`) | face deletion on staff delete, retention purge and account deletion |
| `migrations/035_user_face_index.sql`, `punchapp_ddl_latest.sql` | table `user_face_index(id, user_id, face_id UNIQUE, photo_no, created_at)`, index on `user_id`; no FK to `users` on purpose |
| `scripts/rekognition_setup.py` | `check`, `create`, `policy [--admin]` |

### 9.2 Flow
- **Register** (`POST /face/register`, >= 4 photos as before): every photo is decoded locally (EXIF, quality, hard pre-check: a tiny / black / flat photo is refused as `no_face` without any AWS call), then per photo `DetectFaces` (exactly one significant face, else `no_face` + `multiple_faces`) and `IndexFaces` (`MaxFaces=1`, `QualityFilter=AUTO`, `ExternalImageId` = encoded user id). All photos must be usable (the old rule); a refused photo undoes the whole attempt (the faces indexed so far are deleted). The rows are inserted in ONE transaction (`user_face_index`), THEN the user's previous faces are deleted from Rekognition and their rows removed, so a failed re-registration never destroys a working enrolment. If the insert fails the new faces are deleted from Rekognition (the ids are logged when even that fails). The first photo is still saved as the profile photo (best effort once the faces are registered).
- **Registered?** (`GET /face/check/{id}`, `GET /face/warmup/{id}`, and the punch pre-check) = at least one `user_face_index` row. Warm-up has nothing to pre-load now; it only answers `registered`.
- **Punch** (`POST /face/punch`): holiday gate, registered check, then (unless `force_accept`, unchanged) `prepare_face` -> `DetectFaces` (one significant face; the optional anti-spoof model runs here on a crop of the Rekognition bounding box with a 10 % margin) -> `SearchFacesByImage` (`FaceMatchThreshold` = `REKOGNITION_THRESHOLD`, `MaxFaces=5`, `QualityFilter=AUTO`). A **match** is a result whose `ExternalImageId` is THIS user's and whose similarity is `>= REKOGNITION_THRESHOLD` (default 90.0; 89.99 does not match, 90.0 does); another user's face above the threshold is `no_match`. The geofence, sessions and `face_mismatch` logic are untouched.
- **Scores**: `face_attempt_log.score` and the `face.metrics` line keep the 0..1 scale: `score = similarity / 100` (numeric(5,4)); `NULL` when Rekognition returned no candidate (it only returns faces at or above the threshold, so a clear mismatch has no score). Old rows (cosine) and new rows (similarity / 100) are different scales: do not mix them in one histogram; filter on `created_at` from the switch date.
- **Several people in the frame**: a second face counts only when its confidence is >= 90 and its width is >= 50 % of the main face's width, so a passer-by far in the background does not block a punch.
- **Removal**: permanent staff delete, the retention purge (`purge_deleted_staff`, result key `face_cleanup_failed`), account deletion (both the anonymising and the hard-delete path) and re-registration call `face_service.remove` / replace the faces. Failures are logged and never block the deletion; rows of faces that could not be deleted are kept so the cleanup can be retried.
- **Users registered under the old design** have no `user_face_index` rows: `GET /face/check` answers `registered:false` and a punch answers `not_registered` (the app's register flow). Everyone registers once again; the old `known_faces/*.npy` objects are only deleted when the user is deleted (`delete_employee_s3_assets`).

### 9.3 Failure mapping (app contract unchanged)
| Situation | Punch (HTTP 200 body) | Register |
|---|---|---|
| unreadable bytes | `code image_unreadable`, `reason unknown`, "Face not captured properly" | 400 same |
| no face / quality filter | `code no_face`, `reason` too_dark / too_blurry / too_small / unknown (from the Rekognition reason when given, else from the local pixels) | 400 same |
| more than one face | `code no_face`, **`reason multiple_faces`** (new, additive), "More than one face detected. Please make sure only you are in the frame." | 400 same |
| not this user | `code no_match`, "Face not recognized" | n/a |
| spoof (only with `FACE_ANTISPOOF=1`) | **`code spoof_detected`** (new, additive; the old code had this check commented out), "Spoof detected. Please use a real face." | n/a |
| throttled / Rekognition unreachable (botocore retries used up) | `code server_error`, "The face service is busy. Please try again in a moment." | 503 `{detail, code:"server_error"}` same text |
| collection missing, access denied, bad credentials, unexpected | `code server_error`, generic "Something went wrong. Please try again."; the log line names the cause and the missing IAM action | 500 generic |

The current app drops unknown `reason` / `code` values (`src/api/mappers/face.ts`) and still shows `message`, so the two additive values degrade to a plain message on the old app build.

### 9.4 Environment
`FACE_BACKEND` (`rekognition` | `mock`), `APP_ENV`, `FACE_MOCK_ALWAYS_MATCH`, `REKOGNITION_REGION` (else `AWS_S3_REGION`, else `ap-south-1`), `REKOGNITION_COLLECTION_ID` (default `clockyn-faces`), `REKOGNITION_AUTO_CREATE`, `REKOGNITION_THRESHOLD` (default 90.0), `REKOGNITION_MAX_RETRIES` (default 3, botocore `standard` mode; timeouts 5 s connect / 10 s read), `FACE_PRECHECK`, `FACE_ANTISPOOF`, plus the unchanged `FACE_MAX_WIDTH` (width of the array used for the quality numbers), `FACE_DARK_THRESHOLD` / `FACE_BLUR_THRESHOLD` / `FACE_MIN_SIDE`, `FACE_MAX_CONCURRENCY` (default now 8: the work is network bound), `FACE_ATTEMPT_LOG`. `FACE_SIMILARITY_THRESHOLD` is deprecated and ignored (a warning is logged); `FACE_DET_SIZE` no longer exists. All are in the README environment table and in `.env` (commented).

`FACE_BACKEND=mock` is for local development and browser tests without AWS: any decodable image is one face; a photo matches when the SHA-256 of its first 64 KB equals one enrolled for that user, or always with `FACE_MOCK_ALWAYS_MATCH=1`; state is in memory (per process, lost on restart); the local hard pre-check is off for it (`FACE_PRECHECK=1` forces it). It **refuses to start when `APP_ENV=production`** (or `prod`).

### 9.5 Cost notes (check the AWS pricing page for the current rates; no numbers are quoted here)
Rekognition is billed per image processed (`DetectFaces`, `IndexFaces` and `SearchFacesByImage` each count as one image) and per stored face (collection storage, per month). Per operation in this design: **register** = 2 images per photo (DetectFaces + IndexFaces), i.e. 8 for 4 photos, and 4 stored faces per user; **punch** = 2 images (DetectFaces + SearchFacesByImage); `force_accept` punches and photos refused by the local pre-check cost nothing. A re-registration replaces the faces, so storage stays at 4 per user. Face deletion (`DeleteFaces`) and `DescribeCollection` / `ListFaces` are not image-processing calls. Free-tier terms and regional prices change: confirm them before sizing a budget. Each `scripts/rekognition_setup.py check` run processes up to three 1x1 images (probes) and each live test run (`tests/test_rekognition_live.py`) a handful of images.

### 9.6 AWS Rekognition setup (exact steps; the owner creates the collection and the IAM permissions)
Region: the same as the app (`REKOGNITION_REGION`, default `ap-south-1` = Asia Pacific (Mumbai); check that Rekognition is available in the region you pick). Collection id: `clockyn-faces` (or your `REKOGNITION_COLLECTION_ID`). Replace `<ACCOUNT_ID>` with your 12-digit account id.

1. **Operator policy (human who creates the collection).** Print it with `python scripts/rekognition_setup.py policy --admin` (uses `sts get-caller-identity` for the account id when credentials work), create a policy from the JSON (IAM console > Policies > Create policy > JSON, name e.g. `ClockynRekognitionAdmin`) and attach it to the operator's IAM user / role. It grants `rekognition:CreateCollection` and `rekognition:DeleteCollection` on the collection, `rekognition:ListCollections` on `*`, and (for the optional live test) the face actions on `collection/clockyn-test-*`.
2. **Create the collection** (one of):
   - `python scripts/rekognition_setup.py create` (uses the same env / `--region` / `--collection`), or
   - AWS CLI: `aws rekognition create-collection --collection-id clockyn-faces --region ap-south-1`, or
   - Console: Amazon Rekognition > Face collections (under "Face Liveness / Collections" in the left menu) > Create collection > id `clockyn-faces`.
3. **App policy (least privilege, for the role / user the backend runs as).** `python scripts/rekognition_setup.py policy` prints exactly:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ClockynFacesCollection",
         "Effect": "Allow",
         "Action": [
           "rekognition:IndexFaces",
           "rekognition:SearchFacesByImage",
           "rekognition:DeleteFaces",
           "rekognition:ListFaces",
           "rekognition:DescribeCollection"
         ],
         "Resource": "arn:aws:rekognition:ap-south-1:<ACCOUNT_ID>:collection/clockyn-faces"
       },
       {
         "Sid": "ClockynDetectFaces",
         "Effect": "Allow",
         "Action": ["rekognition:DetectFaces"],
         "Resource": "*"
       }
     ]
   }
   ```
   Create it as a policy (e.g. `ClockynRekognitionApp`) and attach it to the service's IAM role (App Runner / ECS task role / EC2 instance profile) or, for a local run with keys, to the IAM user whose keys are in `.env`. Do NOT give the app role `CreateCollection` / `DeleteCollection` (`REKOGNITION_AUTO_CREATE=1` needs `CreateCollection`: use it only in development).
4. **Credentials.** On AWS prefer the role attached to the service (leave `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` empty: boto3's default chain is used). Locally the keys in `.env` are used.
5. **Configure** `.env` (all lines are in `.env`, commented): `REKOGNITION_REGION`, `REKOGNITION_COLLECTION_ID`, `REKOGNITION_THRESHOLD` (start with the default 90.0 and tune with `face_attempt_log`), `FACE_BACKEND=rekognition` (the default).
6. **Apply the database migration** `migrations/035_user_face_index.sql` (`python migrate.py --all`; a new database gets the table from `punchapp_ddl_latest.sql`).
7. **Verify**: `python scripts/rekognition_setup.py check` prints the region, collection id, face count, which IAM actions work (each missing one is named) and ends with `READY` (exit code 0) or `NOT READY` (exit code 1). The `ListCollections` line is informational (operator only).
8. **Optional live test** with a real photo of one face: `REKOGNITION_LIVE=1 REKOGNITION_TEST_IMAGE=C:\path\face.jpg python -m pytest tests/test_rekognition_live.py -q -s`. It creates and deletes its own collection `clockyn-test-<hex>`, enrols a throw-away user, verifies, and cleans up in `finally` (needs the operator policy of step 1).
9. **Tune and monitor**: Rekognition quotas (transactions per second per region, adjustable by AWS Support) are not documented here; a burst of punches at shift start can be throttled: botocore retries (`REKOGNITION_MAX_RETRIES`), then the app answers "try again". Watch the `face.metrics` lines (`outcome`, `score`, `total_ms`) and the warnings of logger `face.rekognition` / `face.service`.
10. **Legacy data**: after every user has registered again, the old S3 `known_faces/` prefix can be deleted (it holds biometric data of the old model that nothing reads any more).

### 9.7 Unverified without AWS
No Rekognition call was made while building this (tests use fake clients): the default threshold 90, the accuracy of `SearchFacesByImage` on the real user population, the per-region TPS and the exact error wording of Rekognition (the `no face` detection of `InvalidParameterException` is matched on the word "face"), the botocore retry behaviour under real throttling, and whether `check`'s harmless probes (1x1 image) are answered with `InvalidParameterException` before or after the authorisation check (an `AccessDenied` is always reported as DENIED; any other error as ERROR).

## 10. Test notes
- Rekognition design: `tests/test_face_service.py`, `tests/test_rekognition_face.py` (fake boto3 client, in-memory store, no AWS, no database), `tests/face_fakes.py`, optional `tests/test_rekognition_live.py`; `tests/conftest.py` pins `FACE_BACKEND=mock` for every test so nothing can reach AWS or the face table. The old notes below were written for the local model.
- No existing tests. Unit-test `_haversine_distance` (known coordinate pairs), `verify_face` (synthetic unit vectors: identical -> 1.0 match, orthogonal -> no match, boundary 0.62), `_parse_time`-style helpers.
- Integration (Postgres from the schema dump with and without the unique index, S3 via `moto`, `ml.face.encode_face` monkeypatched to return fixed embeddings so the 1 GB model is not needed): punch-in role 3 (expect `success:true`; today it fails — D-FP-01), role 1; punch-in twice (Already punched in); two complete sessions then a third (limit); punch-out without punch-in; geofence in/out/no-coordinates/field staff/no-GPS branch; `force_accept` flow and `face_mismatch` persistence; holiday block; plan gate with expired plan; overnight session punch-out next day; photo background upload sets `punchinface`.
- Manual: real `buffalo_l` on 4 enrolment photos then punch with the same/different person; confirm cold start time and model download behaviour in the container.
- Fixtures: org with branch GPS + `punch_radius`, user with 4 stored embeddings in a fake bucket, field-staff user, holiday row with `block_attendance = TRUE`.
