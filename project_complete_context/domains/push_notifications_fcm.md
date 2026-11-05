# Push notifications (FCM V1) — backend context

Companions: [chat_and_groups.md](chat_and_groups.md), [websockets_realtime.md](websockets_realtime.md). Frontend: `C:\POC\pa_frontend\Clockyn\src\components\layout\bootstrap\{usePushRegistration,usePushEvents,pushToken,notificationGlue}.ts`, `src/lib/platform/notificationRouting.ts`, `src/lib/realtime/events.ts` (`interpretPushData`), `src/api/auth.ts:113` (`updatePushToken`). Requirements: `backend_changes.md` P0-9, P1-7, P3-7, P3-10; DD-30, DD-34.

## Changes (WP8, implemented; the sections below describe the code as it was before)
* **One sender**: `services/push_service.py` (`send_push`, `send_push_to_user(s)`, `send_push_to_tokens`, `get_fcm_access_token`, `get_fcm_credentials`). `auth_function.get_fcm_access_token`, `_send_push_to_token`, `send_push_notification`, `send_punch_in_out_notification` stay importable as thin wrappers; the second copy in `router/chat.py` is gone (`chat._send_fcm_push` is a wrapper too).
* **Credentials** only from env, read on every call: `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY` (`
` unescaped, surrounding quotes stripped), `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID`; local fallback `FCM_SERVICE_ACCOUNT_PATH` (project id read from the file when `FCM_PROJECT_ID` is empty). `FCM_ANDROID_CHANNEL_ID` (default `attendance_notifications`). OAuth token cached 55 min (a changed credential set drops the cache; a 401 from FCM refreshes once and retries). Unconfigured = log once, return False, no DB access, never raises.
* **Stale tokens**: FCM `UNREGISTERED`, `NOT_FOUND` (JSON body) and `INVALID_ARGUMENT` that is about the token (message or field name mentions it; a payload bug never prunes) delete the token from `user_push_tokens` and null `users.push_token`. Other errors are logged only.
* **Multi-device**: new table `user_push_tokens(id, user_id, token, platform, device_id, updated_at, unique(user_id, token))` (migration `027_user_push_tokens.sql`, also in `punchapp_ddl_latest.sql`). `POST /auth/update-push-token` (new optional body field `platform`), `validate-otp` (both paths) and `verify-device-token` save the legacy `users.push_token` AND upsert the table (inside a SAVEPOINT: a missing table never breaks login). The same token under another user is removed; same user + same `device_id` (the app's `device_token`) with a rotated token replaces the old row. Senders fan out to every token of the user and fall back to `users.push_token` when the table has no row. Device-change approval clears all of the user's tokens.
* **`message_id`** is in all four chat push payloads (verified); offline-only group pushes use the socket registry (`realtime.offline_user_ids`).
* **`force_logout` push (NEW-7)**: on a login that replaces another device of a role 2/3 user, a silent FCM data message `{"type":"force_logout","user_id":...}` goes to the previous device's tokens (never to the token being registered). Same-device re-login, first login and admins (role 1) send nothing. The org deactivation flow (`session_registry.force_logout_user`) sends it to every device of each affected user.
* Expo tokens (`ExponentPushToken[...]`): no Expo send path exists in the code, so they are skipped (not sent to FCM, not pruned).
* Announcements: recipients are resolved server-side (active users of the org only; `selected` is org-filtered) and each gets the socket frame plus a push; `recipient_count` is now the resolved count for every target.

## 1. Scope & files

There are **two** near-identical FCM senders plus one credential loader:

| Function | File:line | Used by |
|---|---|---|
| `get_fcm_access_token()` | `core_functions/auth_function.py:61-115` | both senders |
| `_send_push_to_token(token,title,body,data,image_url)` / `send_push_notification(...)` | `auth_function.py:164-208` | announcements (`announcement_functions.py:106-118`, `chat_functions.py:649,723`), request pushes (`requests_functions.py:9-28`), punch pushes (`auth_function.py:278`) |
| `_send_fcm_push(token,title,body,data)` | `router/chat.py:27-63` | chat 1:1 and group pushes only |
| `_get_push_token`, `_get_group_member_tokens` | `router/chat.py:66-97` | token lookup for chat |
| `send_punch_in_out_notification` | `auth_function.py:211-283` | `face_attendance_functions.py:259, 355` |
| `_clear_admin_push_token` | `auth_function.py:286-302` | **no callers** (dead) |
| `update_push_token` + `UpdatePushTokenRequest` | `auth_function.py:116-162` | `POST /auth/update-push-token` (`router/auth.py:277-282`) |
| `get_push_token_status` | `router/auth.py:386-406` | debug endpoint |
| request-push helpers | `core_functions/requests_functions.py:9-126, 428-512` | request submit / decision |
| login flows that also write `push_token` | `auth_function.py:654-657` (test-account OTP bypass), `:732-738` (`validate_otp`), `:841-848` (`verify_device_token`), `:433-435` (admin register inserts `''`) | |

Not present: any Firebase Admin SDK (only `google-auth==2.29.0` + `httpx`), topic/multicast/batch sends, token table, logout endpoint, `GET /auth/push-token/{id}` (declared in the client `chat.ts` but never routed; no screen calls it).

## 2. Data model touched

| Column | Type | Use |
|---|---|---|
| `users.push_token` | `text`, nullable (`ddl` users table) | The **only** token storage: one raw FCM registration token per user. `NULL` or `''` = none (registration inserts `''`, `auth_function.py:435`). |
| `users.device_token` | `varchar(36)` | Single-device session id for roles 2/3 (not an FCM token). |
| `users.updated_at` | timestamp | set by `update_push_token` (:145). |
| `users.role_id`, `parent_id`, `status` | – | recipient resolution (admin = role 1 joined via `org_info.parent_id`; managers = role 2, `parent_id = org`; staff = role 3). |
| `org_info.parent_id` | – | links org → admin user. |

## 3. Endpoints

| Method path | Handler | Identity | Plan gate | Request | Response | Errors |
|---|---|---|---|---|---|---|
| POST `/auth/update-push-token` | `update_push_token_route` `router/auth.py:277` → `update_push_token` `auth_function.py:131` | body `user_id` (no auth) | none | JSON `{user_id: str, push_token: str, device_token?: str}`; `device_token` is ignored (no session gate, :133-134) | `{success:true, message:"Push token updated successfully"}` | empty token → model validator raises (`model_post_init` :125-129; surfaced as 422 expected, UNVERIFIED) — the explicit 400 branch (:136-138) is then unreachable; unknown user → 404 `detail:{success:false,message:"User not found"}`; other → 500 `detail:{success:false,message:"Internal server error"}` (dict `detail`, unlike most routes). Overwrites any previous token of that user. |
| GET `/auth/push-token-status/{user_id}` | `router/auth.py:386` | path | none | – | `{success, user_id, has_push_token, token_preview (first 30 chars), updated_at}` | 404 if unknown. **Debug endpoint leaking a token prefix to anyone** (BC P3-10). |
| GET `/auth/session-events/{user_id}?device_token=` | `router/auth.py:409` | device token | none | – | SSE `force_logout` (see websockets doc) | 403 on mismatch |

**Changes (WP8):** request body gains optional `platform` (`android|ios|web`, max 16 chars; stored in `user_push_tokens`); `device_token`, when sent, is stored as the device id (it lets a rotated token replace the old row of the same device). Response and errors unchanged. The call now also upserts `user_push_tokens` (savepoint-protected).

Push is otherwise a side effect of other endpoints (see §5).

## 4. Business logic

### 4.1 Credentials and OAuth token (`auth_function.py:48-115`)
* Env vars (names only): `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY` (a secret; `\n` escapes replaced at :79), `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID`, fallback file `FCM_SERVICE_ACCOUNT_PATH` (default `firebase-service-account.json`, :55; a secret file).
* Selection: if `FCM_PRIVATE_KEY and FCM_CLIENT_EMAIL and FCM_PROJECT_ID` → in-memory service-account dict (:77-96); elif file exists → file credentials (:97-102); else warn and return `(None, None)`.
* Scope `https://www.googleapis.com/auth/firebase.messaging`; `credentials.refresh(...)` is a blocking call, therefore invoked via `run_in_executor` (:172 / `chat.py:36`).
* Cached in module dict `_fcm_token_cache` for 3300 s (55 min) using `time.monotonic()` (:57-58, :69-71, :107-112). **Bug:** the cached/returned `project_id` is always the env var `FCM_PROJECT_ID` (:109, :112) even in file mode, so with the file fallback and no `FCM_PROJECT_ID` env the senders see an empty project id and skip the push ("FCM not configured", `chat.py:37-39`, `auth_function.py:173-175`). Single project only.
* Exceptions → logged, `(None, None)`, push silently skipped.

### 4.2 Sender (both implementations)
1. Skip if token empty (`auth_function.py:166-168`, `chat.py:29-30`).
2. `data` values coerced to `str` (`None` → `''`) because FCM data must be strings (:169, `chat.py:31`).
3. Get OAuth token + project id; skip if missing.
4. `POST https://fcm.googleapis.com/v1/projects/{project_id}/messages:send` with `Authorization: Bearer <oauth>`; timeout 30 s (`auth_function.py:191`) vs 10 s (`chat.py:40`).
5. Body (identical in both, `auth_function.py:176-190`, `chat.py:47-58`):
```json
{"message": {
  "token": "<raw FCM registration token>",
  "notification": {"title": "...", "body": "...", "image": "<optional, auth sender only>"},
  "data": {"type": "...", "...": "string values only"},
  "android": {"priority": "high",
              "notification": {"sound": "default", "channel_id": "attendance_notifications", "image": "<optional>"}},
  "apns": {"payload": {"aps": {"sound": "default"}}}
}}
```
   Always includes a `notification` block (OS displays it when the app is backgrounded/killed; the app re-displays chat pushes as local notifications when foregrounded). No `ttl`, `collapse_key`/`tag`, `apns-priority` header, `badge`, `mutable-content` or `content-available`. A **data-only/silent** push is not supported by either function.
6. Response: non-200 → `logger.error` (auth sender, :198-199) / `logger.warning` (chat sender, `chat.py:60-61`) with the body; **no retry, no parsing of FCM error codes, no stale-token cleanup**. Any exception is swallowed (`:200-201`, `chat.py:62-63`). Calls are fire-and-forget (`asyncio.create_task` / `ensure_future`) and the task handles are not retained.

### 4.3 Token storage / lookup / multi-device
* Registration paths: client calls `POST /auth/update-push-token` on login, app start and every foreground (`usePushRegistration.ts`); the OTP-verify and device-verify flows also write `push_token` if the client includes one.
* One column → **one device per user**: the most recent registration wins. Two phones of an admin (admins have no device-token gate) alternate. Roles 2/3 are single-device by design.
* Lookup: chat 1:1 → `SELECT push_token FROM users WHERE user_id=%s` (`chat.py:73`); group → members with `status=1`, `member_id != sender`, token not null/empty (`:87-94`); announcements/requests/punch → role/org queries (see §5).
* Sign-out does **not** clear tokens (no logout endpoint; empty tokens are rejected by `update-push-token`), so the previous user keeps receiving pushes on that device until another account registers the same device token (BC P3-7, DD-34). Approved device-change request clears both `device_token` and `push_token` (`requests_functions.py:487-492`).
* Dead-token behaviour: nothing is invalidated automatically; `_clear_admin_push_token` is never called.

### 4.4 Chat pushes
* 1:1 (`router/chat.py:189-199`, `:403-412`): always sent when the receiver has a token (even if online). Title = `result['sender_name']` or `'Someone'` (send-file: form `sender_name` first, :406). Body = `req.message` (**full text, un-truncated**; FCM total message limit is 4 KB) when `req.message_type == 'text'` else `'📎 Sent an attachment'`.
* Group (`:275-287`, `:469-479`): sent to active members (excluding the sender) with a token who have **no entry in `shared_manager.active_connections`** at all (any connected socket counts as online, even a stale one). Title = sender name; same body rule.
* Announcement fan-out: one task per token (no batching; large orgs create thousands of concurrent HTTP clients).

## 5. Side effects & integrations — what is actually sent

`data` always includes `type` (string). All values are strings.

| `data.type` | Triggered by (file:line) | Recipients | `notification.title` / `body` | `data` keys |
|---|---|---|---|---|
| `new_message` | `POST /chat/send` (`router/chat.py:194-199`), `/chat/send-file` (`:407-412`) | receiver (if token) | sender name / text or `📎 Sent an attachment` | `type, sender_id, receiver_id` |
| `new_group_message` | `POST /group-chat/send` (`:282-287`), `/send-file` (`:474-479`) | active members with token and no socket, not sender | sender name / text or `📎 Sent an attachment` | `type, group_id, sender_id` |
| `announcement` (live) | `POST /api/announcements` (`announcement_functions.py:107-118`) | by `target`: all / selected / branch / dept (§4.9 of chat doc) | `req.title` / `req.message[:100]` | `type, announcement_id, org_id, target` |
| `announcement` (legacy) | `POST /announcement/send-all|send-branch-dept` (`chat_functions.py:650-655, 724-735`) | broken audience (`users.org_id` is NULL for staff) | `Announcement from <name>` / `message[:100]` | `type, announcement_id, org_id` (+ `branch_id, dept_id` for branch/dept) |
| `request_submitted` | submit device-change/shift-change/leave/permission (`requests_functions.py:157-160, 191-194, 228-231, 263-266`) | admin + all managers of the org (role 1 via `org_info`, role 2 with `parent_id=org`), excluding requester, with token (`:31-55`) | `Device Change Request` / `Shift Change Request` / `Leave Request` / `Permission Request`; body `<name> submitted a <x> request.` | `type, request_type (device_change|shift_change|leave|permission), request_id` |
| `request_updated` | `POST /requests/update-status` (`requests_functions.py:494-505`) | the requester | `<Type> <Status>` (e.g. `Leave Approved`) / `Your <type> has been <status>.` | `type, request_type, request_id, status (approved|rejected|pending)` |
| `punch_in` / `punch_out` | face punch for roles 2/3 (`face_attendance_functions.py:259-265`, `:355-361`) → `auth_function.py:271-278` | admin + managers with token, excluding the puncher | `Punch In` / `Punch Out`; body `<name> punch in|punch out at 09:05 AM` (IST, `%I:%M %p`) | `type, user_id, image_url ('' when none)`; `image_url` also placed in `notification.image` when set. **Punch-in is never sent** (NameError, `face_attendance_functions.py:264`, BC P1-8). |

Types the Clockyn app understands vs. what the backend sends:

| App type (`notificationRouting.ts`, `events.ts`) | Sent? | Gap |
|---|---|---|
| `new_message` | yes | add `message_id` (P1-7) |
| `new_chat_message` | no (legacy alias) | none needed |
| `new_group_message` | yes | add `message_id` (P1-7) |
| `announcement` | yes (live API) | none; WS `announcement` event also needed (websockets doc §8.4) |
| `request_submitted` | yes | none |
| `request_updated` | yes, **except** device-change approval: `push_token` is cleared (:489) before the token lookup (:498), so nothing is sent | decide: send before clearing, or accept |
| `punch_in` / `punch_out` | punch-out yes; punch-in no | fix P1-8 |
| `force_logout` | **no** (SSE only) | add data-only FCM (§8.4) |
| `ticket_resolved`, `new_poll`, `poll_approved` | no; no backend code references them | not required for Clockyn unless support/polls ship |

Data keys the app reads: `type`, `message_id` (dedupe), `sender_id` (direct-chat route param), `group_id` (group route param), `target`, `user_id`, `image_url`; role is taken from the session, so `role_id` need not be sent.

## 6. Config & env vars
`FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY`, `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID`, `FCM_SERVICE_ACCOUNT_PATH`. Secrets exist at `auth_function.py:49-55` (read from env/file; values never in the repo files read). Hard-coded: Android channel `attendance_notifications`, token cache 3300 s, timeouts 10 s/30 s, FCM URL.

## 7. Known defects & risks

| # | Where | Sev | Defect / impact |
|---|---|---|---|
| P-1 | `auth_function.py:164-201` vs `router/chat.py:27-63` | Med | Duplicate sender implementations with different timeouts/log levels; fixes must be made twice. |
| P-2 | `auth_function.py:109,112` | Med | `project_id` always from env; file fallback broken without `FCM_PROJECT_ID`. |
| P-3 | both senders | Med | No handling of FCM errors (`UNREGISTERED`, `INVALID_ARGUMENT`, `SENDER_ID_MISMATCH`): stale tokens live forever; `_clear_admin_push_token` (:286) is dead; failures only logged. |
| P-4 | `users.push_token` | High | One token per user; multi-device loses pushes; sign-out never revokes (BC P3-7). |
| P-5 | `face_attendance_functions.py:264` | High | NameError blocks staff punch-in push + `badge_update` (BC P1-8). |
| P-6 | no code | High | No FCM `force_logout`; app expects it. The SSE path only works while the old device has the stream open. |
| P-7 | `router/chat.py:276-281` | Med | Group push suppressed for any user with a connected socket (stale sockets of backgrounded apps) → missed notifications; 1:1 always pushes (inconsistent). |
| P-8 | `router/chat.py:197,285` | Low | Full message text as push body; FCM payload limit 4 KB. |
| P-9 | `requests_functions.py:487-498` | Low | Device-change approval clears the token before notifying → requester never gets `request_updated`. |
| P-10 | `announcement_functions.py:107-118`, `chat_functions.py:650` | Med | N unbatched concurrent sends; task handles dropped (asyncio may collect them). |
| P-11 | `router/auth.py:386` | Med | Debug endpoint exposes token preview without auth (BC P3-10). |
| P-12 | `auth_function.py:136-138` | Low | Dead 400 branch; error `detail` is a dict (inconsistent with other routes). |
| P-13 | `auth_function.py:204-208` | Info | Only one Firebase project; Clockyn needs its own (BC P0-9). |
| P-14 | `auth_function.py:745-747` | Low | SSE `force_logout_user` runs on every successful OTP validation even if the device token is unchanged. |

## 8. Clockyn requirements

### 8.1 New Firebase project / credentials — BC P0-9
* Current: one service account for one Firebase project (`auth_function.py:48-55, 61-115`); a token minted by the Clockyn app (new bundle id, new Firebase project) fails on the legacy project (`SENDER_ID_MISMATCH`/403) → every push silently dropped, though nothing else breaks.
* Required: either (a) **cut-over** — change the five `FCM_*` variables (+ ensure `FCM_PROJECT_ID` is set) when v1 is retired; or (b) **dual project** while both apps share the backend (recommended per the owner note).
* Implementation outline for (b):
  1. Create `services/fcm.py` with one `send_push(token, title, body, data, image_url=None, data_only=False, project='legacy')` and make `router/chat.py:_send_fcm_push` and `auth_function._send_push_to_token` delegate to it (removes P-1).
  2. Generalise `get_fcm_access_token(project)` with a cache per project; second env set e.g. `FCM_CLOCKYN_PROJECT_ID`, `FCM_CLOCKYN_PRIVATE_KEY_ID`, `FCM_CLOCKYN_PRIVATE_KEY`, `FCM_CLOCKYN_CLIENT_EMAIL`, `FCM_CLOCKYN_CLIENT_ID` (names only; values in the secret store); derive `project_id` from the credentials, not only env (fixes P-2).
  3. Record which project issued each token: add `users.push_token_project varchar(20) default 'legacy'` (or the token table in 8.2); extend `UpdatePushTokenRequest` with optional `app` (`'clockyn'` sent by the new client; absent ⇒ legacy) and write it in `update_push_token` (:144-147) and the three login writers (`:654-657, :732-738, :841-848`).
  4. Every caller passes the recipient's project; all recipient queries must also select the project column (`chat.py:73,87`, `announcement_functions.py:75-101`, `requests_functions.py:37,109`, `auth_function.py:228`, `chat_functions.py:641,707`).
  5. Keep `channel_id: attendance_notifications` (the app creates it and sets it as default). iOS needs the APNs auth key uploaded to the new Firebase project (outside the backend, BC C-2).
* Migration: `ALTER TABLE users ADD COLUMN push_token_project varchar(20) DEFAULT 'legacy';` (new numbered file under `migrations/`; `migrations/` has no history today).
* Acceptance: a token registered with `app:'clockyn'` receives a chat push through the new credentials; a legacy token still works through the old ones; a missing second credential set logs a warning and skips (no exception into request handlers).

### 8.2 Multi-device and sign-out — BC P3-7, DD-34, DD-30
* Required: `POST /auth/logout {user_id, push_token?, device_token?}` that clears `users.push_token` only if it equals the supplied token (so a newer device's token is not removed) and optionally the device token. Replace the single column with a `user_push_tokens(user_id, token unique, project, platform, created_at, last_seen_at)` table when real multi-device is wanted (admins): upsert in `update_push_token`, send to **all** tokens of a user, delete tokens on FCM `UNREGISTERED`. Roles 2/3 keep single-device semantics (keep only the latest token or tie tokens to `device_token`).
* Acceptance: after logout the previous user's token receives nothing; a second admin device receives pushes on both devices.

### 8.3 Add `message_id` to chat/group pushes — BC P1-7
Change `data` at `router/chat.py:198` (`'message_id': result['message_id']`), `:286`, `:411`, `:478` (also group `sender_name` optional). The app de-duplicates a foreground push against the socket frame by id (`events.ts` `interpretPushData`). Once ids are present the group rule can be simplified to "push to every member except the sender regardless of socket state" (removes P-7); keep the app-side dedupe. Acceptance: push `data.message_id` equals the REST/WS `message_id`.

### 8.4 `force_logout` push (roles 2/3)
* Current: only SSE (`session_registry.force_logout_user`) and polling.
* Required: data-only FCM `{"data":{"type":"force_logout"}}` (no `notification` block) to the **old** device token when (a) a different device completes login, (b) an organisation is deactivated/deleted (`gen_dashboard_functions.py:127-139`). Implementation: add `data_only=True` to the shared sender (omit `notification`; android `priority: high`; apns headers `apns-push-type: background`, `apns-priority: 5`, `aps: {content-available: 1}`); in `validate_otp` read the old `push_token` and old `device_token` **before** the UPDATE at `auth_function.py:732-737` (which overwrites the old token), and after commit send the push only if the old `device_token` is non-null and differs from the new one; for org deactivation select `user_id, push_token` in the existing `affected_user_ids` query (`gen_dashboard_functions.py:107-110`). Keep SSE for v1.
* Notes: background delivery of data-only pushes is best-effort on iOS; the app's 30 s session poll and next launch still enforce it. Admins are not force-logged-out (DD-30).
* Acceptance: login on phone B → phone A receives `force_logout` and signs out; same-device re-login sends nothing.

### 8.5 Other push types the app routes on
* `announcement`, `request_submitted`, `request_updated`, `punch_out`: already sent with the keys the app reads; no change except BC P1-8 for `punch_in` (define `img_url`/pass `None`, `face_attendance_functions.py:264`; move the notification after the response is built).
* `request_updated` on device-change approval: send **before** clearing tokens (`requests_functions.py:487-498`) or document that it is intentionally dropped (the requester must re-login anyway).
* `ticket_resolved`, `new_poll`, `poll_approved`: not sent and no backend feature; implement only if the support/polls features need them (not in BC).

### 8.6 Hygiene
* Parse the FCM error body; on `UNREGISTERED`/`NOT_FOUND` run `UPDATE users SET push_token = NULL WHERE push_token = %s` (token-specific, not the org-admin helper).
* Remove or admin-gate `GET /auth/push-token-status/{id}` (BC P3-10).
* Truncate chat push body to ~100 characters (matches announcements) and keep the full text in the app.
* Do not log full tokens (the sender logs the first 30 chars at `auth_function.py:197`).

## 9. Test notes
* Patch `get_fcm_access_token` to return `("tok","proj")` and `httpx.AsyncClient.post` to capture requests; assert URL, `Authorization` header, and the exact JSON in §4.2 (string-only `data`, channel id, `apns` sound).
* Cases: no credentials → no HTTP call and no exception; non-200 → logged only; empty/NULL token skipped; two members with tokens, one with an open socket → only the offline one gets a group push (current behaviour; update after 8.3); chat 1:1 push always sent; `request_submitted` audience excludes the requester; device-change approval order (P-9); announcement audience for each `target`.
* Token flows: `update-push-token` 200/404/validation; overwrite semantics; login paths writing the token; (after 8.1/8.2) project column and logout clearing only a matching token.
* Manual: real device on the new Firebase project (needs C-2): chat push in background, tap routing (`sender_id`/`group_id` params), `force_logout` data-only behaviour on iOS and Android.
