# Chat, group chat & announcements — backend context

Companion docs: [websockets_realtime.md](websockets_realtime.md) (socket events), [push_notifications_fcm.md](push_notifications_fcm.md) (FCM). Frontend: `C:\POC\pa_frontend\Clockyn\src\api\chat.ts`, `groupChat.ts`, `announcements.ts`; requirements in `backend_changes.md` (P1-6, P1-7, P2-1, P2-2, P2-13) and `docs/DEFERRED_DECISIONS.md` (DD-11, DD-52, DD-80, DD-83, DD-85).

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/chat.py` | 492 | All chat/group/announcement(legacy) HTTP routes, 2 chat WebSockets, FCM sender `_send_fcm_push` (:27), token lookups (:66, :80), WS payload normalisers (:100-127), file-upload routes (:359, :418) |
| `core_functions/chat_functions.py` | 829 | SQL + business logic for 1:1, group, legacy announcements; Pydantic request models (:14-51) |
| `core_functions/announcement_functions.py` | 251 | Live announcements (`/api/announcements*`) incl. push fan-out (adjacent, documented here because it shares the push path) |
| `router/announcement.py` | 39 | Routes for the live announcement family |
| `socket_manager.py` | 79 | In-process `ConnectionManager` singleton `manager` (see websockets doc) |
| `ml/s3.py:233-290` | – | `upload_chat_file`, `get_chat_file_presigned_url`, `get_employee_profile_url` (:170) |
| `core_functions/gen_plans_functions.py:174-196` | – | `check_plan_feature` (plan gate) |
| `chat.py` (repo root, 51 lines) | 51 | **Dead file.** Not imported by `app.py` (which imports from `router`). Would not even import: `from app.core.socket_manager import manager` (:3, no such package), `MessageCreate`, `get_db`, `get_current_user`, `Message`, `GroupMember` undefined (:10-33). Routes `POST /send`, `POST /upload` are never mounted. Safe to delete. |

`app.py:54` mounts `chat.router` (no prefix); `app.py:55` mounts `announcement.router`; `app.py:68` mounts `ws_badge.router`.

Frontend callers: `chat.ts` (list, search, messages, send, send-file, mark-read, delete), `groupChat.ts` (list, create, messages, send, members, add-members, mark-read, send-file). `GET /auth/push-token/{id}` (declared in `chat.ts`) is **not** a backend route and no screen calls it. The legacy `/announcement/*` routes have no frontend callers; live screens use `/api/announcements*`.

Cross-cutting facts (apply to every endpoint below):
* **No authentication/authorisation anywhere.** Identity = whatever `user_id`/`sender_id` the client puts in the path/query/body. No `Depends`, no token.
* **Every handler** is `try: ... except Exception as e: raise HTTPException(500, str(e))` (e.g. `router/chat.py:163-166`, 20 handlers). No `except HTTPException: raise`, so any inner `HTTPException(403/…)` becomes **HTTP 500 with `detail: "<status>: <detail>"`** (Starlette `str(HTTPException)` = `"403: msg"`).
* **Sync DB / S3 inside `async def`**: psycopg2 and boto3 calls block the event loop (including all WebSocket traffic; single uvicorn worker, `Dockerfile:43`).
* IDs: `chat_id`, `group_id`, `announcement_id` = `str(uuid.uuid4())` generated app-side (`chat_functions.py:267,393,623`).
* Timestamps: `utils.timezone.get_utc_now()` (tz-aware UTC from the app server clock, `utils/timezone.py:18`) is written to `chat.date_time` (`timestamptz`).

## 2. Data model touched

DDL source: the schema dump at the repo root (`*_ddl_latest.sql`, file name carries the legacy product name; cited as `ddl:N`; older than code per `backend_changes.md`). `migrations/` contains only a README, so there is no migration history.

| Table | Columns used (type) | Notes |
|---|---|---|
| `chat` (`ddl:199-211`) | `id int identity PK`, `chat_id varchar(36)` (unique idx `chat_chat_id_key`), `message_type varchar(10) default 'text'`, `message text`, `date_time timestamptz default now()`, `group_id varchar(36)`, `sender_id`, `receiver_id`, `file_url text` | One table for 1:1 (`group_id IS NULL`, `receiver_id` set) and group (`group_id` set, `receiver_id` NULL). For file messages `message` = original filename and `file_url` = **S3 key** (legacy rows may hold a full presigned URL; `ml/s3.py:259-273` handles both). **No FKs, no indexes** on sender/receiver/group_id/date_time. |
| `chat_activity` (`ddl:215-222`) | `id`, `chat_id`, `group_id` (NULL for 1:1), `receiver_id` (the reader), `seen_at timestamp` | One row = "reader X saw message Y". **No unique constraint on (chat_id, receiver_id)** and no index; duplicates possible (see §7 D-7). There is no "delivered" state. |
| `group` (`ddl:469-480`, quoted name) | `group_id` (unique idx), `user_id` (creator), `org_id`, `group_name varchar(255)`, `status int default 1`, `created_at`, `updated_at` (both `timestamp` without tz) | `status=1` active. No code path sets another status; no delete/rename route. |
| `group_members` (`ddl:485-494`) | `group_id`, `member_id`, `status int default 1`, `created_at`, `updated_at`; UNIQUE (`group_id`,`member_id`) | `status=1` active member. No remove/leave route; `add_group_members` re-activates `status != 1` rows (:596-600). Hard-deleted when a staff user is deleted (`add_staff_function.py:751`). |
| `announcement` (`ddl:79-94`) | `announcement_id` (unique), `announcement_type varchar(10) 'text'`, `message`, `org_id`, `branch_id int`, `dept_id varchar(36)`, `title varchar(100)`, `target varchar(20) default 'all'`, `sent_by_id`, `created_at`, `updated_at` | `target` ∈ `all|selected|branch|dept` (live code). Legacy chat-router inserts omit `title/target/sent_by_id`, so `target` takes its default `'all'` even for branch/dept rows (`chat_functions.py:697-701`). |
| `announcement_recipients` (`ddl:98-104`) | `announcement_id`, `user_id`, `created_at` | Only for `target='selected'`. |
| `users` | `user_id, name, emp_id, role_id (1 admin, 2 manager, 3 staff), org_id, parent_id, branch_id, dept_id, status (1 active), push_token text, device_token` | **Org resolution rule** (`chat_functions.py:90-96`, repeated :187): role 1 → `users.org_id`; roles 2/3 → `users.org_id` is NULL, org = `parent_id`. `users_org_id_key` is a UNIQUE index, so one admin user per org. |
| `org_info` | `org_id`, `parent_id` (= owning admin's user_id) | Used to find the org's admin (`chat_functions.py:154-156`). |
| `role_info`, `dept_info`, `branch_info` | `role_name`, `dept_name`, `branch_name` (joined on `org_id = u.parent_id`) | Display labels only. |
| `free_trial`, `<LP>_subscription` (legacy-prefixed subscription table, `gen_plans_functions.py:138-143`) | read by `get_org_plan_status` (`gen_plans_functions.py:121-156`) | Plan gate input. |

Enumerations: `message_type` ∈ {`text`,`image`,`file`}; anything else is coerced to `text` (`chat_functions.py:54-59`, `router/chat.py:116-119`). `chat.message_type` column is `varchar(10)`.

## 3. Endpoints

Plan gate: only the two list endpoints call `check_plan_feature(org_id, 'chat', is_admin=False)`. `PLAN_BLOCKED_FEATURES` (`gen_plans_functions.py:167-171`) has no `chat` entry, so in practice chat is blocked **only when the plan is expired/absent** (trial OK, any active plan OK). `is_admin=False` means the message is always the employee text "Your plan has expired. Please contact your admin." even for an admin caller (`:188-192`).

### 3.1 One-to-one chat

| Method path | Handler (router → function) | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| GET `/chat/search/{user_id}` | `chat_search_route` `router/chat.py:161` → `search_users_for_chat` `chat_functions.py:179` | path `user_id` | query `q` (required) | `[{user_id,name,role_name,dept_name,branch_name,profile_photo_url}]`, name ASC | 500 on any error. `[]` if caller unknown. `ILIKE %q%` with `%`/`_` unescaped. One S3 `head_object` (jpg, then png) per row (`ml/s3.py:170-183`). Not plan-gated. |
| GET `/chat/list/{user_id}` | `chat_list_route` `:168` → `get_chat_list` `:82` | path | query `org_id` (**required**, str) | `[{user_id,name,role_name,dept_name,branch_name,last_message,last_message_type,last_message_time,msg_count,unread_count,is_online(false const),profile_photo_url}]` | Plan block → **500** `detail:"403: Your plan has expired. Please contact your admin."` (`:170-174`). Missing `org_id` → 422. `last_message_time` is a raw datetime (serialised by FastAPI), not the `…Z` format used for messages. |
| GET `/chat/messages/{user_id}/{other_user_id}` | `:176` → `get_chat_messages` `:218` | path (no check that caller is `user_id`) | – | `[{message_id,sender_id,receiver_id,message,message_type,file_url,timestamp,is_delivered(true),is_read}]` oldest first | Whole history, no pagination. No `sender_name`. |
| POST `/chat/send` | `:183` → `send_chat_message` `:264` | body `sender_id` | JSON `{sender_id,receiver_id,message,message_type='text'}` (extra keys such as `sender_name` ignored) | `{message_id,sender_id,receiver_id,message,message_type,file_url(null),timestamp,sender_name,is_read:false,is_delivered:false}` | 500 `{detail}` on any error. No validation of ids, empty text, length, org. Side effects: WS `new_message`, FCM push (§5). |
| POST `/chat/send-file` | `chat_send_file_route` `:359` | form `sender_id` | multipart: `sender_id`, `receiver_id`, `file`, optional `sender_name` | as send, plus `filename`, `content_type`; `file_url` = presigned GET URL (7 days) | Whole file read into memory; no size/type limit (§4.6). 500 on any error. |
| PUT `/chat/mark-read` | `:204` → `mark_as_read` `:292` | body | JSON `{sender_id,receiver_id}`; **viewer = `receiver_id`** (messages sent by `sender_id` to `receiver_id` are marked) | `{success:true,marked_count:int}` | Always emits WS `read_update` to `sender_id` even if `marked_count==0`. |
| DELETE `/chat/delete/{user_id}/{other_user_id}` | `:217` → `delete_chat` `:772` | path | – | `{success:true}` | **Hard-deletes both directions for both users** (§4.7). No auth, no event. |

### 3.2 Group chat

| Method path | Handler | Request | Success response | Errors / quirks |
|---|---|---|---|---|
| GET `/group-chat/list/{user_id}` | `group_list_route` `router/chat.py:226` → `get_group_chat_list` `:334` | query `org_id` (**required**) | `[{group_id,group_name,created_by,member_count,last_message,last_message_sender,last_message_time,unread_count}]` ordered by last message DESC NULLS LAST | `org_id` used **only** for the plan gate; list = groups where caller is an active member (not filtered by org). Plan block → 500 `"403: …"` (`:228-232`). `last_message` for files = filename; no `last_message_type`. Old client param `organization_id` → 422 (see §8.1). |
| POST `/group-chat/create` | `:234` → `create_group` `:390` | JSON `{group_name,created_by,member_ids[],org_id}` | `{group_id,group_name,created_by,member_count(=len(member_ids)),unread_count:0}` | **Creator is NOT auto-added** (the client must include self; `groupChat.ts` does). Duplicate ids in `member_ids` hit `uq_group_members_group_member` → whole create rolls back → 500. No validation that members exist/belong to the org; `org_id` unchecked; empty name accepted. Emits WS `group_created` to members except creator. |
| GET `/group-chat/messages/{group_id}` | `:246` → `get_group_messages` `:423` | query `user_id` (required) | `[{message_id,group_id,sender_id,sender_name,message,message_type,file_url,timestamp,read_count,is_read,total_members}]` oldest first | No membership check (any `user_id`/group). `INNER JOIN users` on sender → messages of deleted senders disappear. `file_url` presigned fresh (`:470-471`). |
| POST `/group-chat/send` | `:253` → `send_group_message` `:480` | JSON `{group_id,sender_id,message,message_type='text'}` | `{message_id,group_id,sender_id,message,message_type,file_url,timestamp,sender_name,is_read:false,read_count:0}` | Non-member → **500** `detail:"403: User is not a member of this group"` (`:262-266` wrapped by `:289-290`). Membership checked twice (router :258-268 and `chat_functions.py:487-492`). No `total_members` in response. WS `new_group_message` to all active members incl. sender; push to "offline" members. |
| GET `/group-chat/members/{group_id}` | `:292` → `get_group_members` `:560` | – | `[{user_id,name,emp_id,role_name,dept_name,branch_name,is_admin}]`, admin first | `is_admin` = creator (`g.user_id = u.user_id`). No auth, no group-exists check (returns `[]`). |
| PUT `/group-chat/mark-read` | `:299` → `mark_group_as_read` `:520` | JSON `{group_id,user_id}` | `{success:true,marked_count}` | Emits WS `read_update` `{reader_id,group_id}` to every active member including the reader, even when `marked_count==0`. |
| POST `/group-chat/add-members` | `:314` → `add_group_members` `:582` | JSON `{group_id,member_ids[]}` | `{success:true,added:int}` | **No event, no push**, no caller identity, no group-exists check (inserts rows for any `group_id`), no org check. Existing active members skipped; inactive re-activated (counted in `added`). |
| POST `/group-chat/send-file` | `group_send_file_route` `:418` | multipart `sender_id,group_id,file` | as group send plus `filename`,`content_type`, presigned `file_url` | Membership checked **after** the S3 upload (`:433` vs `:443-446`) → orphan S3 object for non-members; error is 500 `detail:"User is not a member of this group"` (no `403:` prefix). |

### 3.3 Announcements

Live family (used by the apps) — `router/announcement.py`, `core_functions/announcement_functions.py`:

| Method path | Handler | Request | Response | Quirks |
|---|---|---|---|---|
| POST `/api/announcements` | `send_announcement_route` `announcement.py:10` → `send_announcement` `announcement_functions.py:30` | JSON `{org_id,sent_by_id,title,message,announcement_type='text',target='all',selected_user_ids?,branch_id?,dept_id?}` | `{success:true,data:{announcement_id,title,message,org_id,target,created_at,recipient_count}}` | Admin check = `users.role_id == 1` for `sent_by_id` (:35-38) but `org_id` is not tied to the sender. Row committed (:57) **before** recipients inserted (:63-67) — partial failure leaves a `selected` announcement with no recipients. `title` > 100 chars → DB error → 500. 500 `"Only admin can send announcements"`. Push fan-out §5. **No WebSocket event.** |
| GET `/api/announcements/{org_id}` | `:18` → `get_announcements` `:131` | – | `{success,data:[{id,title,message,target,branch_id,dept_id,created_at,sent_by,branch_name,dept_name,recipient_count}]}` | Admin list, no auth. |
| DELETE `/api/announcements/{announcement_id}` | `:26` → `delete_announcement` `:170` | – | `{success:true}` or HTTP 200 `{success:false,message:"Announcement not found"}` | Unscoped delete (P1-9). |
| GET `/api/announcements/employee/{user_id}` | `:34` → `get_announcements_for_employee` `:198` | – | `{success,data:[{id,title,message,target,created_at,sent_by,branch_name,dept_name}]}` | Org = `users.parent_id`. Visibility: `target='all'` OR (`branch` and same branch) OR (`dept` and same dept) OR (`selected` and in recipients). |

Legacy family in `router/chat.py` (no frontend callers; **functionally broken for staff**): they resolve the audience with `users.org_id = %s` (`chat_functions.py:641-643, 707-710`, `:807-826`), but roles 2/3 have `org_id = NULL` (org is in `parent_id`; `add_staff_function.py:398` inserts no `org_id`). So legacy sends push to nobody and `GET /announcement/employee/{id}` returns `[]` for staff.

| Method path | Handler (router:line → function) | Notes |
|---|---|---|
| POST `/announcement/send-all` | `:323` → `send_announcement_all` `chat_functions.py:620` | role 1 only; inserts without title/target; push type `announcement` with keys `announcement_id, org_id` |
| GET `/announcement/all/{org_id}` | `:330` → `get_announcements_all` `:665` | rows with branch & dept NULL |
| POST `/announcement/send-branch-dept` | `:339` → `send_announcement_branch_dept` `:683` | requires `branch_id` or `dept_id`; push keys add `branch_id, dept_id` |
| GET `/announcement/branch-dept/{org_id}` | `:346` → `get_announcements_branch_dept` `:745` | optional `branch_id` (int), `dept_id` filters |
| GET `/announcement/employee/{user_id}` | `:486` → `get_announcements_for_employee` `:803` | see above |

### 3.4 WebSockets declared in `router/chat.py`

| Path | Handler | Notes |
|---|---|---|
| WS `/ws/chat/{user_id}` | `chat_websocket` `:137` | full detail in [websockets_realtime.md](websockets_realtime.md) |
| WS `/ws/group-chat/{group_id}/{user_id}` | `group_chat_websocket` `:147` | `group_id` ignored; same manager as above |

(`/ws/badge/{user_id}` lives in `router/ws_badge.py`.) Endpoint tally for this document: 15 chat/group REST + 5 legacy announcement + 4 live announcement REST = 24 REST rows (the two send-file routes are counted in the first 15) and 2 chat WebSockets.

## 4. Business logic

### 4.1 Chat list (`get_chat_list`, `chat_functions.py:82-176`)
1. Load caller `role_id, org_id, parent_id`; unknown caller → `[]`. Caller org = `org_id` if role 1 else `parent_id` (:93-96). **The `org_id` query param is not used for this** — only the plan gate sees it.
2. One query with CTEs:
   * `msg_counts`: per counterpart, `COUNT(*)` and `MAX(date_time)` over `chat` with `group_id IS NULL` where caller is sender or receiver (:99-108).
   * `last_msgs`: `DISTINCT ON` counterpart, latest message text/type (:109-122).
   * `unread`: messages **to** caller, `group_id IS NULL`, with **no** `chat_activity` row for (`chat_id`, caller) with `seen_at` not null (:123-134).
3. Contact set = every active (`status=1`) user in the caller's org except the caller: the org's admin (`role_id=1` joined through `org_info.parent_id`) plus all `role_id IN (2,3)` with `parent_id = org` (:151-159). **This is org-wide, not team-scoped** (answers DD-85: staff added under a manager are still in everyone's list; managers and staff are siblings under `parent_id`).
4. Order `last_message_time DESC NULLS LAST, name ASC`; `is_online` constant `false`; then per row `get_employee_profile_url` (S3 HEAD ×≤2, serial).

### 4.2 Send 1:1 (`send_chat_message` :264-289, route :183-202)
1. Coerce `message_type`; insert `chat` row (`chat_id`, sender, receiver, message, type, `date_time = now`), commit.
2. Lookup sender name (`'Someone'` fallback); response flags `is_read:false, is_delivered:false`.
3. Route: `broadcast_chat_message(sender, receiver, result)` (WS `new_message` to receiver **and** sender sessions), then look up `users.push_token` of the receiver and `create_task(_send_fcm_push(...))` (always, even when the receiver is online). Body = `req.message` when `req.message_type == 'text'` (raw, un-truncated), else `"📎 Sent an attachment"` (:197).
4. Any exception → rollback → HTTP 500.

### 4.3 Read receipts
* 1:1 (`mark_as_read` :292): select `chat_id`s sent by `sender_id` to `receiver_id` (`group_id IS NULL`) without a seen row by `receiver_id`; insert one `chat_activity(chat_id, receiver_id, seen_at=now)` per message; commit; return count. Route then pushes WS `read_update {reader_id: receiver_id, sender_id}` to `sender_id`'s sessions only (:209-212).
* Group (`mark_group_as_read` :520): same, over messages in the group not sent by `user_id`, inserting `(chat_id, group_id, receiver_id, seen_at)`. Route sends WS `read_update {reader_id, group_id}` to every active member (:303-308).
* Read-then-insert, no locking, no unique key → concurrent calls double-insert.

### 4.4 Group read model and counts
* REST `get_group_messages` (:423-477): `total_members = COUNT(active members) - 1` (:429-433, :469) — identical for every message in the response; "minus one" assumes the requester is still a member.
* `read_count` = `COUNT(*)` of `chat_activity` rows with `seen_at` for that message **only when the requester sent it**, else 0 (:438-446). Counts rows, not distinct readers.
* `is_read` = true for own messages; for others = caller has a seen row (:447-457).
* Frontend rule (confirmed by DD-80): everyone has read when `read_count >= total_members`.
* Group list unread (`get_group_chat_list` :357-370): messages not sent by caller with no seen row by caller, **across all history** — a member added later sees the whole backlog as unread.
* The `last_msgs`/`unread`/`member_counts` CTEs are not restricted to the caller's groups (scan all group messages and all memberships), a scaling risk.

### 4.5 Group create / add / send
* Create (:390-420): insert `group` (status 1), insert each member (status 1) in a loop, single commit; route then emits `group_created` to each non-creator.
* Add (:582-615): per id: SELECT; if exists and inactive → UPDATE status=1; if absent → INSERT; commit once. Returns number added/reactivated.
* Send: see 3.2; router builds member list via `get_group_members` (active members, **including sender**) and calls `broadcast_group_message`, then pushes to members with a non-empty token who have no WS connection at all.

### 4.6 File upload (both routes, `router/chat.py:359-415`, `:418-482`)
1. `file_bytes = await file.read()` — entire body in memory; **no size limit, no MIME/extension allow-list, no filename sanitising** (`ml/s3.py:237` key = `documents/{uuid4 hex}_{client filename}`; filename `None` → `..._None`). Upload is a synchronous `s3.put_object` with the client-supplied `ContentType` (`ml/s3.py:238-243`).
2. Presigned GET URL, 7 days (`ExpiresIn=604800`, `ml/s3.py:244-248`); DB stores the **key** only.
3. `message_kind = "image"` iff `content_type` starts with `image/`; `message` column = `file.filename`.
4. Insert row (`RETURNING … date_time AS timestamp`), commit, response overlays presigned URL and adds `filename`, `content_type`.
5. History endpoints regenerate a fresh presigned URL per row on every call (`chat_functions.py:243-254`, `:465-471`). 1:1 history also **re-derives `message_type` from the URL's extension** (jpg/jpeg/png/gif/webp/heic → image, else file; :250-254), which can disagree with the stored type (e.g. an `image/*` upload with another extension is returned as `file`). Group history keeps the stored type.
6. Limits actually in force: none in code. Upstream proxy/ASGI body limits are UNVERIFIED (App Runner/uvicorn defaults not in repo). Client timeout is 60 s (frontend notes).

### 4.7 Delete conversation (`delete_chat` :772-800)
Deletes `chat_activity` rows then `chat` rows for **both directions** between the two users (`group_id IS NULL`), one commit. Effect: the conversation disappears for **both** participants permanently; S3 objects are not deleted; the other party is not notified (no WS event); no authorisation (any caller can delete any pair). There is no group-chat delete. Staff deletion removes all of a user's messages (including group messages they sent) and memberships (`add_staff_function.py:745-751`).

### 4.8 Ordering & time zones
* All history queries `ORDER BY date_time ASC` with no tie-breaker (`id`); equal timestamps order unpredictably.
* Message timestamps are formatted by `_format_utc_iso` (`chat_functions.py:62-67`): UTC, **seconds precision**, suffix `Z` (e.g. `2026-10-19T09:05:03Z`). Sub-second order is lost client-side. Zone-less values are treated as UTC. Chat-list/group-list `last_message_time` and announcement `created_at` are returned as driver datetimes (timestamptz in the session zone; `announcement.created_at` is `timestamp` without tz holding UTC), so their ISO strings differ in format/offset from messages (zone depends on the DB session `TimeZone`, UNVERIFIED but normally UTC on RDS).
* `chat_activity.seen_at`, `group.*_at`, `group_members.*_at` are `timestamp` without tz; tz-aware UTC values are inserted, so correctness relies on the DB session zone being UTC (UNVERIFIED).

### 4.9 Announcement push audiences (live, `announcement_functions.py:72-118`)
`all` → `users.parent_id = org AND role_id IN (2,3)`; `selected` → `user_id = ANY(selected_user_ids)` (any role); `branch`/`dept` → `parent_id = org AND branch_id|dept_id = x AND role IN (2,3)`; anything else → none. Tokens with NULL/'' are skipped. One `ensure_future(send_push_notification(...))` per token, title = `req.title`, body = `req.message[:100]`.

## 5. Side effects & integrations

| Trigger | Channel | Exact payload |
|---|---|---|
| `POST /chat/send`, `/chat/send-file` | WS `new_message` → receiver + sender sessions | `{type:"new_message",data:{message_id,timestamp,message_type,message,sender_id,receiver_id,sender_name,file_url,is_delivered:true,is_read:false}}` (`socket_manager.py:43-57`) |
| same | FCM → receiver's single `users.push_token` | notification `{title: sender name, body}`; data `{type:"new_message",sender_id,receiver_id}` (`router/chat.py:198`, `:411`). Body for files `"📎 Sent an attachment"`. In send-file the title uses form `sender_name` if given. |
| `PUT /chat/mark-read` | WS `read_update` → `sender_id` sessions | `{type:"read_update",data:{reader_id,sender_id}}` (:209-212) |
| `POST /group-chat/send`, `/send-file` | WS `new_group_message` → all active members incl. sender | `{type:"new_group_message",data:{message_id,timestamp,message_type,message,sender_id,group_id,sender_name,file_url,is_read:false}}` (`socket_manager.py:63-76`); no `read_count`/`total_members` |
| same | FCM → active members with token, **no WS connection**, excluding sender | data `{type:"new_group_message",group_id,sender_id}` (:286, :478); title = sender name |
| `PUT /group-chat/mark-read` | WS `read_update` → all active members incl. reader | `{reader_id,group_id}` (:305-308) |
| `POST /group-chat/create` | WS `group_created` → members except creator | `{type:"group_created",data:{group_id,group_name,created_by,member_count,unread_count}}` (:241) |
| `POST /group-chat/add-members` | none | – |
| `/api/announcements` POST | FCM per recipient token | data `{type:"announcement",announcement_id,org_id,target}` (`announcement_functions.py:112-117`) |
| legacy announcement POSTs | FCM (audience broken) | data `{type:"announcement",announcement_id,org_id[,branch_id,dept_id]}` |
| Chat files | S3 (`documents/` prefix) | key `documents/{uuid}_{filename}`, 7-day presigned GET |

Full socket/FCM mechanics: see the two companion documents.

## 6. Config & env vars
`DB_SCHEMA` (+ DB connection variables in `core_functions/db_function.py`), `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (secrets read at `ml/s3.py:10-11`), `AWS_S3_REGION`, `S3_BUCKET_NAME`, `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY`, `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID`, `FCM_SERVICE_ACCOUNT_PATH` (`auth_function.py:49-55`). Hard-coded: presigned expiry 604800 s, FCM Android channel `attendance_notifications`.

## 7. Known defects & risks

| # | Where | Sev | Defect / impact |
|---|---|---|---|
| D-1 | `router/chat.py` all handlers (e.g. :168-175, :226-232, :253-290) | Med | Blanket `except Exception → 500` hides real status codes: plan block (403), non-member send (403) arrive as 500 `"403: …"`. |
| D-2 | whole router | High | No authentication/authorisation: read any 1:1 history (:176), any group's messages/members (:246, :292), delete any conversation (:217), impersonate any `sender_id`, add anyone to any group (:314). |
| D-3 | `chat.py` plan gate :171, :229 | Med | Plan check uses the **caller-supplied `org_id`**, independent of `user_id`; any non-expired org id passes. `is_admin=False` message shown to admins. |
| D-4 | `router/chat.py:418-482` | Low | Group file upload checks membership after the S3 write → orphan objects; error lacks the `403` prefix unlike text send. |
| D-5 | `router/chat.py:359-415` | Med | No file size/type validation, entire file in memory, client-controlled `ContentType` and filename in key; sync S3 call blocks the loop. |
| D-6 | `chat_functions.py:469` | Low | `total_members = active - 1` is wrong if the requester is not an active member (non-members can call it) or the sender has left. |
| D-7 | `chat_functions.py:438-446`, `ddl:215-222` | Med | `read_count` counts rows, `chat_activity` has no unique key → double-insert from concurrent/dup mark-read inflates ticks (can show "all read" early). Rows of members who later left still count. |
| D-8 | `chat_functions.py:357-370` | Med | Newly added members see the entire backlog as unread (no join-date cut-off). |
| D-9 | `chat_functions.py:582-615` | Med | add-members: no event/push, no caller, no group existence check, no cap. |
| D-10 | `chat_functions.py:401-405` | Low | Duplicate ids in `create` → unique violation → 500; creator not auto-added (silently creates a group the creator can't see). |
| D-11 | `chat_functions.py:772-800` | High | Destructive unscoped delete, both sides, no event, S3 files remain. |
| D-12 | DDL | Med | `chat`, `chat_activity` have no indexes beyond PK/`chat_id`; the NOT-EXISTS unread subqueries and list CTEs scan; group CTEs scan all groups. History endpoints unbounded. |
| D-13 | `chat_functions.py:168-172, 207-211` | Med | N serial S3 HEAD calls per list/search on the event loop. |
| D-14 | `chat_functions.py:641-643,707-710,807-826` | Med | Legacy announcement endpoints use `users.org_id` (NULL for staff): no push, empty employee list. Branch/dept rows stored with `target='all'`. |
| D-15 | `announcement_functions.py:57-67` | Low | Announcement committed before recipients; no org↔sender check. |
| D-16 | `chat.py` (root) | Low | Dead, non-importable file; remove. |
| D-17 | `router/chat.py:275-287` vs `:189-199` | Low | Group push skipped for any user with an open socket (a backgrounded app can keep a stale server-side socket) while 1:1 pushes always; inconsistent and can lose group notifications. |
| D-18 | `chat_functions.py:218-261` | Low | 1:1 `is_read` for received messages is always `true` in history; unread state only available from the list endpoint. |
| D-19 | `router/chat.py` `_build_ws_event` (:123) | Info | Defined, never used. |

## 8. Clockyn requirements

Cross-refs are to `C:\POC\pa_frontend\Clockyn\backend_changes.md` (BC) and `docs/DEFERRED_DECISIONS.md` (DD). Realtime-event and push items are detailed in the companion documents; the list below is the chat/group REST-facing subset plus pointers.

### 8.1 Group list parameter (`org_id` vs `organization_id`) — BC P2-2 / group list
* Current: both list endpoints declare `org_id: str = Query(...)` (`router/chat.py:169, 227`); the Clockyn client sends `org_id` (`groupChat.ts` `listGroups`) and works. The old client sent `organization_id` (and the legacy badge service sent none) → **422** every time, so nothing to keep alive for old builds beyond making them work.
* The param is used **only** for `check_plan_feature`; the list itself is determined by `user_id` membership. The org that `get_chat_list` uses for contacts is derived from the caller's row (`chat_functions.py:93-96`).
* Required: derive the plan-gate org from `user_id` on the server (role 1 → `users.org_id`, else `parent_id`; same helper as :93-96/:187) and make `org_id` **optional** (`Optional[str] = Query(None)`); if both given and different, ignore the client value (or 403). To keep old builds working, declare **both** `org_id` and `organization_id` as optional query params (FastAPI aliases map one name only) and use them only as a fallback when the server-derived org is unavailable.
* Acceptance: `GET /group-chat/list/U?org_id=X`, `?organization_id=X` and no param all return 200 for an active org; an expired org returns a real 403; a spoofed `org_id` of a paid org for a user of an expired org still returns 403.

### 8.2 Plan block must be a real 403 — BC P2-2, P2-1
* Current: `check_plan_feature` raises `HTTPException(403, detail=emp_msg)` inside the handler `try`, so the response is HTTP 500 `{"detail":"403: Your plan has expired. Please contact your admin."}` (`router/chat.py:170-174, 228-232`).
* Change: add `except HTTPException: raise` before `except Exception` in all 20 handlers of `router/chat.py` (preferred: replace the pattern with a small decorator or register an exception handler; a global fix also resolves P2-1 for other routers). Alternatively call `check_plan_feature` before the `try`. Keep the detail text. Fix the stale docstring `gen_plans_functions.py:176-182` (matrix has no `chat`; only expiry blocks chat). If product wants chat blocked on Core (DD-10/DD-11), add `'chat'` to `PLAN_BLOCKED_FEATURES[CORE_PLAN_ID]` and gate the send/mark-read/messages routes as well (they are ungated today, so deep links bypass the hubs).
* Back-compat: the Clockyn client already unwraps `"403: …"` (`src/lib/api/errors.ts`), so both forms work; v1 treats any non-2xx as failure.
* Tests: expired org → 403 + same `detail`; trial → 200; DB error in plan lookup → 500; admin caller gets the admin message when `is_admin` is derived from role.

### 8.3 Non-member group send → 403
* Current: 500 `"403: User is not a member of this group"` (text) and 500 `"User is not a member of this group"` (file; after upload). Fix with 8.2's re-raise; in `group_send_file_route` move the membership SELECT **before** `upload_chat_file`, raise `HTTPException(403, …)`. Remove the duplicate check in `send_group_message` or keep it raising a domain error mapped to 403.
* Test: send as non-member (text and file) → 403, no `chat` row, no S3 object, no WS/push.

### 8.4 `total_members` excludes sender; read formula — DD-80 (resolved)
* Current: `total_members = active members − 1` for every message (`chat_functions.py:469`); `read_count` = seen rows (own messages only). Clockyn rule `read_count >= total_members` is consistent.
* Hardening: compute `read_count` as `COUNT(DISTINCT ca.receiver_id)` restricted to **currently active members other than the sender**; compute `total_members` as active members minus 1 only when the sender is an active member (else all active members). Add a unique index `chat_activity (chat_id, receiver_id)` (dedupe first). WS payloads must use the same numbers (§8 of websockets doc).
* Migration (new numbered SQL under `migrations/`): dedupe `DELETE ... USING` on duplicate (`chat_id`,`receiver_id`), then `CREATE UNIQUE INDEX`; use `INSERT ... ON CONFLICT DO NOTHING` in `mark_as_read`/`mark_group_as_read`.

### 8.5 Group `read_update` payload — BC P1-6
See websockets doc §8.3 (payload `message_id`, `read_count`, `total_members`, not sent to the reader). Data needed from this domain: make `mark_group_as_read` return the marked `chat_id`s/sender ids (`INSERT … RETURNING` or the `rows` already selected at :530-542) and compute counts per message.

### 8.6 `add-members` event, authorisation, backlog — BC P1-6, DD-52
* Current: silent. Required: after commit emit `group_updated` (to pre-existing active members: `{group_id, group_name, member_count, added_member_ids}`) and `group_created` (to each newly added member, same shape as create) over the user sockets; optionally an FCM `group_added` is **not** understood by the app (routes unknown types to nothing) so keep it socket-only unless the frontend adds a type.
* Add optional `added_by`/`user_id` to `AddGroupMembersRequest`; reject (403) if the group does not exist, or the caller is not an active member/creator (product call DD-52: creator-only vs any member); validate all ids belong to the group's org (`group.org_id`).
* Backlog: decide whether new members see history; if not, add `group_members.joined_at` usage (`created_at` already exists, reset on re-activation at :597-599) and filter messages/unread with `c.date_time >= gm.created_at`.
* Tests: add two users → existing members get `group_updated`, new get `group_created`; non-member caller 403; unknown group 404; re-adding an active member → `added:0`.

### 8.7 Delete-chat scope — BC P2-13
* Current behaviour (documented, verified): permanent delete of the whole 1:1 history for **both** users; no event; files stay in S3; ungated. Clockyn copy says "This cannot be undone" which is accurate.
* Required: document and keep, plus (a) authorise (caller must be one of the pair; needs identity from a token), (b) emit a WS `chat_deleted {user_id, other_user_id}` to the other party so the open screen clears (new frontend event, additive), (c) optional S3 cleanup of the deleted keys, (d) if product wants "delete for me" later, add `chat_hidden(user_id, other_user_id, hidden_at)` and filter lists/messages by it instead of hard delete.
* Test: after delete both `GET /chat/messages` directions return `[]`; list `msg_count=0`; `chat_activity` empty.

### 8.8 Ordering & timestamps — DD-83
Confirmed: messages are `…Z` UTC (resolved). Required hardening: `ORDER BY c.date_time ASC, c.id ASC` in both history queries; emit `timespec="milliseconds"` (or keep seconds but tie-break by id); normalise `last_message_time` in the two list endpoints with `_format_utc_iso` so all chat timestamps share one format. Acceptance: two messages in the same second are returned in insertion order; list timestamps end in `Z`.

### 8.9 File upload — see §4.6
Required: enforce a max size (e.g. 10-25 MB) via `Content-Length`/streamed read and return 413; allow-list content types (images, pdf, office docs, txt); sanitise filename (strip path separators, cap length) when composing the S3 key; set `ContentDisposition` for non-images; move the membership check before upload (8.3); offload `put_object` to a thread (`run_in_executor`). Keep the 7-day presigned URL and the key-in-DB design (so `file_url` stays fresh). Tests: oversize → 413, disallowed type → 415, valid image → `message_type:'image'` and a working URL.

### 8.10 Unread counting
Current: derived per request from `chat_activity` (see 4.1, 4.4); 1:1 list gives `unread_count` per contact, group list per group; Clockyn sums both (`sumChatUnread`, `sumGroupUnread`). Required: indexes (D-12), the backlog rule (8.6), and a distinct-reader read model (8.4). No new endpoint is needed; optionally `GET /chat/unread-total/{user_id}` to avoid fetching two full lists for the badge (not requested by BC).

### 8.11 Chat contact scope — DD-85
Answer for product: `/chat/list` and `/chat/search` are org-wide (admin + all managers + all staff of the org, active only). To restrict to team, add `manager_assignments` filtering in the two queries; not required by BC.

### 8.12 `announcement` realtime event & chat plan gate on child routes — BC P1-7, DD-11
Pointer: WS event in websockets doc §8.4. DD-11 (gate send/messages/mark-read/add-members) is optional product work: implement by calling the same server-derived-org gate (8.1) in those routes.

### 8.13 Resource scoping (G4-A Changes)
* `POST /chat/send`, `POST /chat/send-file`: the receiver must share an organisation with the sender (admin: `users.org_id` plus owned `org_info` rows; manager / staff: `parent_id`), else 404 `User not found` (send-file checks before the upload).
* `GET /group-chat/messages/{group_id}` and `PUT /group-chat/mark-read`: `user_id` must be an active member (or the creator) of the group, else 404 `Group not found`; with a token `user_id` must also be the caller (403).
* `GET /group-chat/members/{group_id}`: with a token the caller must be a member / the creator (404 otherwise).
* `POST /group-chat/add-members`: the group must exist (404), with a token the caller must be a member / the creator (404), every added user must belong to the group's organisation (404 `User not found`, nothing written); repeated ids are de-duplicated.
* `POST /group-chat/create`: the creator and every member must be users of `org_id` (404 otherwise); with a token `created_by` must be the caller.
* Left as is: the message history of a 1:1 chat is limited to the pair by its own predicate; `message_id` is never an input.

## 9. Test notes

* No automated tests exist for this domain in the backend repo (UNVERIFIED beyond the files read; none referenced).
* Fixtures: one org with admin (role 1, `org_info.parent_id = admin.user_id`), one manager (role 2), two staff (role 3), all `parent_id = org_id`; `free_trial` or subscription-table rows for active/expired cases; S3 stubbed (`ml.s3.upload_chat_file`, `get_chat_file_presigned_url`, `get_employee_profile_url`); a fake FCM (patch `get_fcm_access_token` and `httpx.AsyncClient`); `ConnectionManager` with fake WebSocket objects.
* Critical cases: (1) send/receive 1:1 and WS fan-out to receiver and sender devices; (2) mark-read inserts one row per unread message and is idempotent; (3) group read ticks (`read_count` vs `total_members`) with 3 members; (4) non-member send (text, file); (5) plan block status/detail; (6) group list with `org_id`, `organization_id`, none; (7) add-members behaviour and unread backlog; (8) delete-chat scope; (9) file upload size/type; (10) announcement audience per target; (11) timestamps end in `Z` and sort stably.
* Manual: two simulators on the same user to confirm multi-device delivery; background the app to confirm the group push is not suppressed by a stale socket.
