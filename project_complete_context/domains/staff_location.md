# Staff location (field tracking & punch pins) — backend context

Domain owner: BD3. Related: `face_recognition_punch.md` (punch-time location logging), `attendance_and_home.md` (day basis table). Frontend: `C:\POC\pa_frontend\Clockyn\src\api\location.ts` (`logLocation` every 20 s while punched in, `locations`, `history`, `stats`, `/home/staff-punch-locations`), notes `C:\POC\pa_frontend\project_complete_context\shared\api\staff_location.md`.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/staff_location.py` | 45 | 5 routes under `/staff-location` (mounted `app.py:53`) |
| `core_functions/staff_location_functions.py` | 322 | log, latest locations, history, cleanup, freshness stats |
| `core_functions/home_functions.py:1230-1369` | - | `/home/staff-locations` and `/home/staff-punch-locations` (served by `router/home.py:158-172`) |
| `core_functions/face_attendance_functions.py:216-224,309-317` | - | punch-in/out coordinates also written to `staff_location_logs` |
| `core_functions/gen_plans_functions.py:174-196` | - | `check_plan_feature(..., 'staff_location')` |

## 2. Data model touched

| Table | Columns | Notes |
|---|---|---|
| `staff_location_logs` (dump:955-965) | `id int identity PK`, `user_id varchar(36)`, `org_id varchar(36)`, `location point` stored `(lng,lat)`, `date_time timestamp` (**without time zone**, default `CURRENT_TIMESTAMP`) | indexes `(org_id, date_time DESC)`, `(user_id, date_time DESC)`, `(user_id, date_time)`. No FK, no retention job (only the manual cleanup endpoint). `org_id` is whatever the client sent. |
| `users` | `user_id, name, emp_id, branch_id, dept_id, parent_id, org_id, role_id (2,3 only), status=1, is_field_staff=1` | "field staff" flag `users.is_field_staff` int (0/1) |
| `attendance` | `punch_in, punch_out, attendance_date, punch_in_location, punch_out_location` | joined for punch times / pins |
| `branch_info`, `dept_info` | names | `LEFT JOIN`s scoped by `org_id` here (unlike the punch path) |

Read convention: `location[0]` = longitude, `location[1]` = latitude (`staff_location_functions.py:112-114,189-191`); in the home queries the text form is split (`SPLIT_PART(REPLACE(REPLACE(col::text,'(',''),')',''),',',1|2)` = lng|lat, `home_functions.py:1331-1334`).

## 3. Endpoints

Auth: none on all routes (ids from the client, P3-1). Plan gate column: `check_plan_feature(org_id,'staff_location', is_admin=True)` -> 403 `{detail}` for an expired plan and for the Core plan (`PLAN_BLOCKED_FEATURES`, trial allows all).

| # | METHOD path | Handler (file:line) | Plan gate | Request | Success response | Errors |
|---|---|---|---|---|---|---|
| 1 | POST `/staff-location/log` | `router/staff_location.py:12` -> `log_staff_location` (`SLF:32`) | none | JSON `LocationLogRequest{user_id, org_id, latitude float, longitude float, timestamp? ISO string}` | `{success:true,message:"Location logged successfully",is_field_staff:true}` | 400 any of user/org/lat/lng falsy (so `0.0` rejected); 403 not an active role 2/3 user; 403 `Location tracking only available for field staff`; 403 `Location tracking only active after punch in` / `Location tracking stopped after punch out`; 500 `Failed to log location: ...` (bad `timestamp` -> `ValueError` -> 500) |
| 2 | GET `/staff-location/locations` | `:16` -> `get_staff_locations` (`SLF:95`) | yes (admin) | q: `org_id` req, `target_date?` YYYY-MM-DD | list `StaffLocationResponse{user_id,name,emp_id,branch_name,dept_name,latitude,longitude,location_updated_at,punch_in,punch_out}` | 400 empty org; 500 `Failed to get staff locations: ...` (bad date string -> SQL error 500) |
| 3 | GET `/staff-location/history/{user_id}` | `:24` -> `get_user_location_history` (`SLF:173`) | none | path `user_id`; q: `start_date?`, `end_date?` (default last 7 days to today UTC) | `{success:true,data:[{latitude,longitude,timestamp}]}` oldest first | 500 on error; works for any user id (also non-field staff) |
| 4 | GET `/staff-location/stats` | `:32` -> `get_staff_location_stats` (`SLF:252`) | yes (admin) | q: `org_id` req, `target_date?` | `{success:true,data:{recent,outdated,no_location,total},date}` | 500 on error |
| 5 | DELETE `/staff-location/cleanup` | `:40` -> `delete_old_location_logs` (`SLF:222`) | none | q: `days_to_keep` int default 30 | `{success:true,message,deleted_count}` | **unauthenticated destructive endpoint**; `days_to_keep=0/negative` deletes (almost) everything |
| 6 | GET `/home/staff-locations` | `router/home.py:158` -> `get_staff_locations` (`HF:1230`) | none | q: `org_id` req, `target_date?` | list of dicts `{user_id,name,emp_id,dept_name,branch_name,punch_in "HH24:MI",punch_out,latitude,longitude,location_updated_at}` | not called by the frontend; INNER JOIN: only staff that have a log that day |
| 7 | GET `/home/staff-punch-locations` | `router/home.py:166` -> `get_staff_punch_locations` (`HF:1306`) | none | q: `org_id` req, `target_date?` | list of dicts `{user_id,name,emp_id,dept_name,branch_name,punch_in "hh:mm AM",punch_out,punch_in_lat,punch_in_lng,punch_out_lat,punch_out_lng}` | one row per attendance row of the day for ALL active role 2/3 with a punch-in; no `staff_id` filter |

(`SLF` = `core_functions/staff_location_functions.py`, `HF` = `core_functions/home_functions.py`.) Frontend uses #1-#4 and #7.

## 4. Business logic

### 4.1 Log a ping (#1, `SLF:32-93`)
1. Reject if any of `user_id, org_id, latitude, longitude` is falsy (`:34`).
2. Load user `status=1 AND role_id IN (2,3)`; none -> 403. `is_field_staff` falsy -> 403.
3. `today = get_utc_now().date()` (UTC) and `SELECT punch_in,punch_out FROM attendance WHERE user_id AND attendance_date = today` -> `fetchone()` with **no ORDER BY** (`:56-62`). No row or no `punch_in` -> 403 "only active after punch in"; row with `punch_out` -> 403 "stopped after punch out".
4. INSERT `staff_location_logs(user_id, org_id=<client>, POINT(lng,lat), date_time)` where `date_time = datetime.fromisoformat(timestamp)` when supplied (client-controlled, not bounded; a tz-aware value is converted by the DB session zone then stored without zone; a naive value is stored as given) else `get_utc_now()`.
5. Commit. Returns the fixed success object.
Resulting behaviour: with two sessions on the day the arbitrary row may be the closed first session -> 403 although a second session is open; after UTC midnight (05:30 IST) an overnight session (dated the previous UTC day) finds no row -> 403, so tracking stops mid-shift.

### 4.2 Latest locations (#2, `SLF:95-171`; #6 same shape in `HF:1230-1304`)
- `target_date` default = UTC today (#2) / IST today (#6).
- CTE `latest_locations`: `DISTINCT ON (user_id)` newest log with `org_id = %s AND DATE(date_time) = target_date` (`DATE()` of the stored zone-less timestamp, i.e. UTC day assuming a UTC session).
- Main query: active role 2/3, `is_field_staff = 1`, `(parent_id = org OR org_id = org)`, `LEFT JOIN latest_locations` (#2) so staff with no ping still appear with null coordinates; #6 uses INNER JOIN. `LEFT JOIN attendance a ... AND a.attendance_date = target_date` — **one output row per attendance session**, so a staff member with two sessions appears twice with different punch times.
- `punch_in/punch_out` are `TO_CHAR(timestamptz, 'HH12:MI AM')` (#2) or `'HH24:MI'` (#6) evaluated in the DB session zone (UTC unless configured): **UTC clock times, not IST**.
- `location_updated_at = date_time.isoformat()` of a zone-less timestamp (no offset; UTC by convention), lat/lng cast to float, `0` -> null.

### 4.3 History (#3, `SLF:173-220`)
`start_date` default today-7, `end_date` default today (UTC); `WHERE user_id AND DATE(date_time) BETWEEN start AND end ORDER BY date_time ASC`; returns `latitude`, `longitude` (floats) and `timestamp` ISO (no offset). Includes the punch-in/punch-out pings written by the punch path. No limit (a 7-day history at 3 pings/minute is ~30,000 rows), no org check, no plan gate.

### 4.4 Stats (#4, `SLF:252-323`)
Same CTE for the date; for each active role 2/3 field-staff user: `no_location` (no ping that day), `recent` (`date_time >= NOW() - INTERVAL '15 minutes'`), else `outdated`; counts keyed by status; `total` = sum. A past `target_date` yields only `outdated`/`no_location`. `NOW()` (timestamptz) compared with a zone-less column relies on the session zone being the zone the timestamps were written in.

### 4.5 Punch pins (#7, `HF:1306-1369`)
`INNER JOIN attendance a ON u.user_id = a.user_id AND a.attendance_date = target_date`, `a.punch_in IS NOT NULL`, role 2/3, active; coordinates parsed from the point text; response keys as in §3. IST default date; times in DB session zone (UTC), 12-hour format with AM/PM.

### 4.6 Cleanup (#5)
`DELETE FROM staff_location_logs WHERE DATE(date_time) < (UTC today - days_to_keep)`; returns count. No schedule exists in the code; nothing calls it (UNVERIFIED externally, e.g. cron).

### 4.7 Where else rows are written
Every punch-in and punch-out WITH coordinates inserts a log row for any role (`face_attendance_functions.py:216-224,309-317`), with the client `org_id`. These rows are the only pings for non-field staff; field-staff map queries ignore other users.

## 5. Side effects & integrations
None beyond PostgreSQL (no push, no S3). Map display and reverse geocoding are client concerns (`backend_changes.md` P2-14). Android background-location permissions are an app concern (DD-06).

## 6. Config & env vars
`DB_SCHEMA`, `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`. Hard-coded: freshness window 15 minutes (`SLF:281`), default history 7 days (`:184`), default retention 30 days (`:222`), ping interval 20 s is client-side (`location.ts:11`).

## 7. Known defects & risks

| ID | Sev | Where | Defect / impact |
|---|---|---|---|
| D-SL-01 | High | `SLF:56-66` | Ping gate reads `attendance` by UTC date with an unordered `fetchone`: (a) second session -> may read the closed first session -> 403 although punched in; (b) overnight sessions fail after 05:30 IST. Field-staff tracking stops silently (the client only logs the 403). |
| D-SL-02 | High | `SLF:105-106,117-119,184-186,262`, `HF:1257` | Day filters use `DATE(date_time)` of a zone-less UTC timestamp and a UTC default date; the frontend sends IST dates, so pings between 00:00 and 05:30 IST fall on the previous day and disappear from the requested IST day (P2-5). |
| D-SL-03 | High | `SLF:130-131`, `HF:1269-1270,1335-1336` | Punch times are formatted in the DB session zone (UTC by default), not IST; `HH12:MI AM` text without date; `location_updated_at` has no offset (`SLF:159`, `HF:1297`). |
| D-SL-04 | High | `SLF:132-137`, `HF:1275-1276` | `LEFT JOIN attendance` on the date yields duplicate rows per staff when two sessions exist (and the punch_in/out pair of each row is its own session). Map/list show the same person twice. |
| D-SL-05 | High | `router/staff_location.py:40-44` | `DELETE /staff-location/cleanup` is unauthenticated and destructive (`days_to_keep=0` or negative wipes history; P3-9). |
| D-SL-06 | Med | `router/staff_location.py:24-30` | `/history/{user_id}` has no auth, no org check, no plan gate: anyone can pull any user's movement trail (privacy). |
| D-SL-07 | Med | `SLF:69-74` | client-controlled `timestamp` (arbitrary past/future, unbounded) and `org_id` (arbitrary) stored as truth; bad format -> 500. |
| D-SL-08 | Med | `SLF:34` | `all([...latitude, longitude])` rejects a legitimate `0.0` coordinate. |
| D-SL-09 | Med | `HF:1359-1362`, `SLF:157-158` | `float(x) if x else None` nulls `0.0` coordinates. |
| D-SL-10 | Med | `HF:1306-1363` | `/home/staff-punch-locations` returns every staff member's pins (no `staff_id` filter) for the whole org each call and one row per session; the frontend loads it per staff detail (P2-9, P1-10). |
| D-SL-11 | Med | table | no retention: 3 pings/min x field staff x working hours, no purge job, no partitioning; indexes on `(org_id, date_time)` help the daily queries but history is unbounded. |
| D-SL-12 | Low | `router/staff_location.py:12-14` | `/log` is not plan gated (the read routes are), so a Core-plan org still stores pings. |
| D-SL-13 | Low | `HF:1230-1304` vs `SLF:95-171` | two near-identical implementations with different semantics (INNER vs LEFT JOIN, IST vs UTC default date, `HH24` vs `HH12` format). |

## 8. Clockyn requirements

### R-SL-1 IST day basis and timestamps with offset — P2-5 [V]
- **Current:** D-SL-02/03.
- **Required:** all `staff_location` date filters use the IST calendar day; all returned timestamps are ISO-8601 with `+05:30` (or `Z`), punch times IST.
- **Outline:** `staff_location_functions.py`: `today = convert_utc_to_ist(get_utc_now()).date()` for defaults; filter `date_time >= (%s::date AT TIME ZONE 'Asia/Kolkata') AND date_time < ((%s::date + 1) AT TIME ZONE 'Asia/Kolkata')` after changing the column to `timestamptz` (migration `ALTER TABLE staff_location_logs ALTER COLUMN date_time TYPE timestamptz USING date_time AT TIME ZONE 'UTC'`) — or, without a migration, `date_time AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'` in the predicate; serialise with `convert_utc_to_ist(dt).isoformat()`; format punch times with `AT TIME ZONE 'Asia/Kolkata'` in `TO_CHAR` (or in Python via `format_ist_time`).
- **Back-compat:** frontend already sends IST dates and parses zone-less timestamps as UTC (`src/lib/datetime.ts`); returning an explicit offset is read correctly by both.
- **Acceptance:** a ping at 01:00 IST on 19 Oct (19:30 UTC on 18 Oct) is returned for `target_date=2026-10-19` and not for 18 Oct; `punch_in` shows IST time; `location_updated_at` ends with an offset.

### R-SL-2 Reliable tracking across sessions/overnight — DD-05, DD-06 [V]
- **Current:** D-SL-01.
- **Required:** accept pings whenever the user has an OPEN session (any date), reject otherwise with a clear code.
- **Outline:** replace the attendance query (`SLF:56-62`) by `SELECT 1 FROM attendance WHERE user_id=%s AND punch_in IS NOT NULL AND punch_out IS NULL LIMIT 1`; return 403 code `NOT_PUNCHED_IN` otherwise; ignore client `timestamp` or clamp to `[now-5 min, now+1 min]`; derive `org_id` from `users.parent_id`.
- **Back-compat:** same status codes/messages (the client treats 403 as "stop pinging").
- **Acceptance:** second open session accepts pings; overnight session accepts pings after 05:30 IST; closed session -> 403.

### R-SL-3 One row per staff, staff filter, bulk-friendly — P1-10, P2-9 [V]
- **Current:** duplicates per session; no `staff_id` filter on `/home/staff-punch-locations`.
- **Required:** one row per staff in `/staff-location/locations` (first punch-in, last punch-out, open flag); `staff_id` (and optional `branch_id`/`dept_id`) filter on `/home/staff-punch-locations`; return all sessions as `sessions:[{punch_in,punch_out,in_lat,in_lng,out_lat,out_lng}]` while keeping the legacy top-level fields (first in / last out).
- **Outline:** aggregate attendance in a subquery like `get_daily_attendance`'s `agg`; add `staff_id` query param to `router/home.py:166` and `HF:get_staff_punch_locations` (`AND u.user_id = %s`).
- **Back-compat:** legacy keys retained; new keys additive.
- **Acceptance:** staff with two sessions -> one row; `staff_id` returns exactly that staff's pins; response size for a 200-staff org without filter unchanged.

### R-SL-4 Plan gate, auth and retention — P3-1, P3-9, D-SL-05/06/11 [V]
- **Required:** `cleanup` and `history` need authentication and org ownership; `history` plan-gated as the other reads; scheduled retention (e.g. nightly delete of rows older than N days, N configurable) instead of a public endpoint.
- **Outline:** remove or protect `delete_old_location_logs` route; add `STAFF_LOCATION_RETENTION_DAYS` env + scheduler (cron/ECS task/pg_cron); add `check_plan_feature(org_id,'staff_location')` to `/log` and `/history` after resolving org from the user.
- **Acceptance:** unauthenticated cleanup -> 401/403; history for a user of another org -> 403; Core plan `/log` -> 403.

### R-SL-5 Unify duplicate implementations [V]
Delete `/home/staff-locations` (unused) or make both call one function; keep semantics of R-SL-1/3.

## 9. Test notes
- No automated tests. Fixtures: org with field and non-field staff, shifts, attendance rows (one session, two sessions, open overnight session dated the previous UTC day), `staff_location_logs` rows around 18:30 UTC (= 00:00 IST), Postgres session zone `UTC` and `Asia/Kolkata`.
- Critical cases: ping accepted for field staff with open session; 403 for non-field, inactive, role 1, no attendance, closed session; two sessions (must accept while second open); overnight after 05:30 IST; IST day boundary for `locations`/`stats`/`history`; duplicate staff rows with two sessions; `stats` buckets with pings 5 min/30 min old and none; bad `timestamp`; `cleanup` with `days_to_keep=30` removes only older rows.
- Mock contract: `C:\POC\pa_frontend\Clockyn\e2e\mock-server\routes\attendance.js` / `home.js` (location shapes).
