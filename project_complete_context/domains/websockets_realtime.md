# WebSockets & realtime — backend context

Companions: [chat_and_groups.md](chat_and_groups.md), [push_notifications_fcm.md](push_notifications_fcm.md). Frontend: `C:\POC\pa_frontend\Clockyn\src\lib\realtime\{socketCore,socket,events,globalRealtime}.ts`, `src/api/chat.ts` (`parseChatSocketEvent`), `src/features/chat/utils/frames.ts`. Requirements: `backend_changes.md` P1-6, P1-7; DD-80, DD-84.

## Changes (WP8, implemented; the sections below describe the code as it was before)
* **`services/realtime.py`** is the one place that publishes live events. Every frame goes to every open socket of the user on `/ws/chat/{user_id}` (`socket_manager.manager`, new `send_event`/`is_online`); `badge_update` and `request_submitted` are ALSO mirrored to the legacy `/ws/badge/{user_id}` socket, and `force_logout` also to the SSE queue (both legacy channels keep working, additive).
* **Frames on the chat socket** (shapes of the frontend mock): `badge_update` `{type, notification:{id,title,body,type,image_url}}` (punch in/out, to the org's admin and managers, never to the punching user); `request_submitted` `{type, data:{request_id, request_type}}`; `announcement` `{type, target, data:{announcement_id,title,target,org_id}}` to every resolved recipient; group `read_update` `{type, data:{reader_id,group_id,message_id,read_count,total_members}}` to the message sender only (more than 50 messages in one call = one `{reader_id, group_id}` refetch frame per sender); `group_created` `{group_id,group_name,created_by}` to NEW members after add-members and `group_updated` `{group_id, member_count}` to the existing members; `force_logout` `{type, data:{user_id}}`.
* `socket_manager`: a send that does not complete within 5 s drops that socket (a stuck client no longer holds up the HTTP request); `/ws/chat` and `/ws/group-chat` answer a text `ping` with `pong`, any end of the connection removes exactly that socket. `ws_badge`: a closing old socket no longer removes a newer one; `notify_org_badge_update` is now org-scoped (admin + managers).
* `router/chat.py` routes now re-raise `HTTPException` (the group-send 403 was a 500) and return a generic 500 detail.
* Still single process (in-memory registries): run one uvicorn worker.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `socket_manager.py` | 79 | `ConnectionManager` singleton `manager` (`:80`): `user_id -> [WebSocket]`; broadcasters for 1:1 and group chat events |
| `router/chat.py` | 137-157 | `/ws/chat/{user_id}`, `/ws/group-chat/{group_id}/{user_id}`; import `from socket_manager import manager as shared_manager` (:135); emitters for `read_update`, `group_created` (:209, :241, :305) |
| `router/ws_badge.py` | 52 | **Second, independent** registry `_connections: user_id -> single WebSocket`; `/ws/badge/{user_id}`; helpers `notify_badge_update`, `notify_org_badge_update` |
| `core_functions/auth_function.py:259-265` | – | punch events call `notify_badge_update(uid,'badge_update')` |
| `core_functions/requests_functions.py:84-101` | – | request submissions call `notify_badge_update(uid,'request_submitted')` |
| `router/auth.py:409-446`, `core_functions/session_registry.py` | – | SSE channel for `force_logout` (the only non-WebSocket realtime channel) |
| `chat.py` (root) | 51 | dead, imports non-existent `app.core.socket_manager`; ignore |

Mounting: `app.py:54` (`chat.router`) and `app.py:68` (`ws_badge.router`), no prefix, no middleware that touches WebSockets (the CORS middleware at `app.py:19-32` is an HTTP `BaseHTTPMiddleware`). Single process: `Dockerfile:43` runs one uvicorn worker, so the in-memory registries work only with **one instance**. Horizontal scaling (the hosted environment is a managed container service per the frontend notes) would silently drop events for users connected to another instance. Neither registry is shared (no Redis/pub-sub).

## 2. Data model touched
No tables are read or written by the socket layer itself. Event payloads are built from rows returned by the chat functions (`chat`, `chat_activity`, `group`, `group_members`, `users`; see chat_and_groups.md §2). Recipient lookups: `get_group_members` (active members), `users` joins on `org_info`/`parent_id` for admin/manager recipients (`auth_function.py:243-253`, `requests_functions.py:58-81`). In-memory state only: `manager.active_connections` and `ws_badge._connections`.

## 3. Endpoints (WebSocket / SSE)

| Channel | Path | Handler | Identity | Plan gate | Behaviour |
|---|---|---|---|---|---|
| WS | `/ws/chat/{user_id}` | `chat_websocket` `router/chat.py:137-144` | path `user_id` (**no auth, no validation that the user exists**) | none | `accept()` first (`socket_manager.py:11-16`), append to the user's list, then `while True: await websocket.receive_text()` (frames are read and ignored; client `ping` text gets **no pong**). Only `WebSocketDisconnect` is caught; other exceptions leave the entry registered until a failed send prunes it. |
| WS | `/ws/group-chat/{group_id}/{user_id}` | `group_chat_websocket` `:147-156` | path | none | `group_id` is **ignored** and membership is **not checked**; connects to the same `shared_manager` under `user_id`, so it delivers the same per-user stream (all chats, all groups), not a group-scoped one. |
| WS | `/ws/badge/{user_id}` | `badge_websocket` `ws_badge.py:36-52` | path | none | accept; `_connections[user_id] = ws` (**replaces any previous socket of that user; one device only**); replies text `pong` to text `ping`; on disconnect/any error `pop(user_id)`. |
| SSE | `GET /auth/session-events/{user_id}?device_token=` | `session_events` `router/auth.py:409-446` | verifies `users.device_token == device_token` else 403 | none | `text/event-stream`; yields `data: force_logout` once (then closes) or `: heartbeat` every 30 s; registry `session_registry._sessions[user_id]` is **one queue per user**. |

**Changes (WP8):** `/ws/chat/{user_id}` and `/ws/group-chat/{group_id}/{user_id}` reply `pong` to a text `ping` and clean up on any error; the chat socket now also carries `badge_update`, `request_submitted`, `announcement`, `group_updated`, `force_logout` (see the Changes block). `/ws/badge/{user_id}` and the SSE route are unchanged for legacy clients.

Frontend wiring (Clockyn): the global socket (`useGlobalSocket`, `socket.ts`) and the chat screens use `/ws/chat/{userId}` only; group screens use `/ws/group-chat/{groupId}/{userId}`; a 30 s keep-alive `ping` is sent; reconnect with backoff and a refetch after reconnect (`socket.ts`, DD-84). **The app never opens `/ws/badge/{user_id}`** (grep of `src/` finds no use).

## 4. Business logic

### 4.1 `ConnectionManager` (`socket_manager.py`)
* `connect` (:11): `accept`, key `str(user_id)`, append → multi-device/multi-tab: every socket of the user is kept.
* `disconnect(user, ws)` (:18): remove that socket; delete the key when the list becomes empty; if `ws` is not found it **pops all sockets of the user** (fallback branch :23-26).
* `_send_to_user_sessions` (:31): iterate a copy of the list; `send_json`; on any exception `disconnect` that socket. Sends are sequential and awaited on the request task, so a slow client delays the HTTP response of the sender.
* `broadcast_chat_message(sender, receiver, msg)` (:41): payload built from `msg` fields, sent to receiver sessions then sender sessions.
* `broadcast_group_message(sender, member_ids, msg)` (:61): same payload to every `member_id` (the caller passes active members **including the sender**).
* Because the group socket and the global socket both register under the same user key, **every event is delivered once per open socket**: a user with the global socket, a chat-screen socket and a group-screen socket receives each event 3 times (the frontend de-dupes counts by message id, `events.ts`/`globalRealtime.ts`).
* No replay, no sequence numbers, no acknowledgements, no presence (`is_online` in the chat list is a constant false), no typing events. Missed events are recovered only by client refetch.

### 4.2 Badge socket (`ws_badge.py`)
* `notify_badge_update(user_id, event_type)` (:12-19): sends `{"type": event_type}` to the single registered socket if any.
* `notify_org_badge_update(org_id, exclude_user_id)` (:22-33): **unused**, and it ignores `org_id`, broadcasting to every connected user.
* Disconnect race: when a user reconnects, the new socket replaces the old; when the old socket's handler later exits it runs `_connections.pop(user_id)` and removes the **new** socket (:48-52).

### 4.3 Session SSE
`force_logout_user(user_id)` (`session_registry.py:16-19`) puts `"force_logout"` on the registered queue. Called from `validate_otp` after a new-device login (`auth_function.py:745-747`) and from the org deactivation flow for all role 2/3 users (`gen_dashboard_functions.py:137-139`). Polling alternative: `GET /requests/check-session/{user_id}` (`router/requests.py:150` → `check_device_session`, `requests_functions.py:537`).

## 5. Side effects & integrations — event catalogue (exact payloads)

Envelope for every JSON frame: `{"type": <string>, "data": {...}}` except the badge-socket frames (flat, `type` only).

### 5.1 Socket `/ws/chat/{uid}` (and `/ws/group-chat/{gid}/{uid}`)

| `type` | Emitted by (file:line) | Trigger | Recipients | `data` fields |
|---|---|---|---|---|
| `new_message` | `socket_manager.py:43-58` via `router/chat.py:187` (send), `:402` (send-file) | 1:1 text/file send | all sockets of the receiver **and** of the sender (sender echo) | `message_id` (str uuid), `timestamp` (`…Z` UTC), `message_type` (`text|image|file`), `message`, `sender_id`, `receiver_id`, `sender_name`, `file_url` (presigned for files, else null), `is_delivered:true`, `is_read:false`. **Missing** vs REST: `filename`, `content_type`. |
| `new_group_message` | `socket_manager.py:63-77` via `router/chat.py:273` (send), `:468` (send-file) | group send | all sockets of **every active member including the sender** | `message_id`, `timestamp`, `message_type`, `message`, `sender_id`, `group_id`, `sender_name`, `file_url`, `is_read:false`. **Missing:** `read_count`, `total_members`, `filename`, `content_type`. |
| `read_update` (1:1) | `router/chat.py:209-212` | `PUT /chat/mark-read` | all sockets of the original `sender_id` only (not the reader's other devices) | `reader_id` (= request `receiver_id`), `sender_id`. Sent even when `marked_count == 0`. |
| `read_update` (group) | `router/chat.py:305-308` | `PUT /group-chat/mark-read` | all sockets of every active member **including the reader** | `reader_id`, `group_id`. **No** `message_id`, `read_count`, `total_members`. Sent even when nothing was marked. |
| `group_created` | `router/chat.py:241` | `POST /group-chat/create` | all sockets of each member except the creator | `group_id`, `group_name`, `created_by`, `member_count`, `unread_count:0` (the create response) |

Not emitted on this socket today: `delivered`, `new_chat_message`, `group_updated`, `chat_deleted`, `announcement`, `badge_update`, `request_submitted`, `request_updated`, `typing`, presence.

### 5.2 Socket `/ws/badge/{uid}` (flat frames)

| `type` | Emitted by | Trigger | Recipients | Fields |
|---|---|---|---|---|
| `badge_update` | `auth_function.py:263` (`send_punch_in_out_notification`) | staff/manager (role 2/3) punch-in or punch-out, **both** (indistinguishable) | admin of the org (role 1 via `org_info.parent_id`) and managers (`role_id=2, parent_id=org`), excluding the punching user; regardless of push-token presence | `{"type":"badge_update"}` only; **no `notification` object** (title/body/image/type) |
| `request_submitted` | `requests_functions.py:91` via `_ws_notify_request` (called :157, :191, :228, :263) | leave / permission / shift-change / device-change submission | same audience (admin + managers), excluding requester | `{"type":"request_submitted"}` only |

Note: `send_punch_in_out_notification` is called with `image_url=img_url` where `img_url` is undefined (`face_attendance_functions.py:264`), so the **punch-in** notification (push **and** `badge_update`) is never reached for staff punch-in (BC P1-8); punch-out (`:355-361`) works.

### 5.3 SSE
`data: force_logout\n\n` once, `: heartbeat\n\n` every 30 s (`router/auth.py:428-446`).

### 5.4 Client-side only
`socket_reconnected` (frontend `RealtimeEvent`, `events.ts`) is raised by the app after a reconnect; the backend never sends it. `poll_refresh` (old client) likewise.

### 5.5 Multi-device delivery summary
| Stream | Multi-device behaviour |
|---|---|
| Chat socket events | every open socket of the target user (loop in `_send_to_user_sessions`) |
| `read_update` 1:1 | original sender's devices only; the reader's other devices are not told |
| Badge socket | **last connection wins** (single slot) |
| FCM | one token per user (`users.push_token`), so one device (see push doc) |
| SSE force_logout | one queue per user, last connection wins |

## 6. Config & env vars
None specific. Ping/keep-alive and reconnect timing are client-side; server has no heartbeat on the chat/badge sockets and relies on the client's `ping` or TCP failure detection. No WebSocket-specific uvicorn flags (default max message size, ping interval/timeout of the `websockets`/`wsproto` implementation apply, UNVERIFIED which library is installed).

## 7. Known defects & risks

| # | Where | Sev | Defect / impact |
|---|---|---|---|
| W-1 | `router/chat.py:137-156`, `ws_badge.py:36-39` | High | No authentication or origin check; anyone who knows/guesses a `user_id` (a UUID) can subscribe and read that user's chat events. |
| W-2 | `router/chat.py:147-156` | Med | Group socket ignores `group_id` and membership; duplicate delivery per socket. |
| W-3 | `socket_manager.py:61-78`, `router/chat.py:271-273` | Low | Group event payload lacks `read_count/total_members`; sender receives its own message back. |
| W-4 | `router/chat.py:305-308` | Med | Group `read_update` gives no `message_id/read_count/total_members` and goes to the reader too (BC P1-6). |
| W-5 | `ws_badge.py` vs `chat.py` | High | Two separate registries. The new app listens for `badge_update`/`request_submitted`/`announcement` on `/ws/chat/{uid}` (`events.ts` `interpretSocketFrame`) but the backend emits the first two only on `/ws/badge/{uid}`, which the app never opens; `announcement` is emitted nowhere. Those events never reach the app today (it relies on pushes and refetch). |
| W-6 | `ws_badge.py:36-52` | Med | Single-slot registry and reconnect race removes the live socket; multi-device badge delivery impossible. |
| W-7 | `ws_badge.py:22-33` | Low | `notify_org_badge_update` broadcasts to all users (ignores `org_id`); unused. |
| W-8 | `router/chat.py:137-144` | Low | Only `WebSocketDisconnect` handled → leaked registry entries on other errors; no server-side stale-socket eviction, which also makes "offline" detection for group pushes unreliable. |
| W-9 | `socket_manager.py:31-39` | Med | Sequential awaited sends inside HTTP handlers: slow/unresponsive client delays the sender's REST response. |
| W-10 | `Dockerfile:43` | High (when scaling) | In-memory registries are per process; with >1 instance events are lost. |
| W-11 | `router/chat.py:100-127` | Info | `_format_ws_timestamp`/`_build_ws_event` are partly unused (`_build_ws_event` never called). |
| W-12 | `auth_function.py:264`/`face_attendance_functions.py:264` | High | NameError prevents the staff punch-in notification and badge event (BC P1-8). |

## 8. Clockyn requirements

### 8.1 Where badge-type events must be delivered — BC P1-7, W-5
* Current: `badge_update` and `request_submitted` go to `/ws/badge/{uid}` with no payload; the Clockyn global socket is `/ws/chat/{uid}`.
* Required: deliver all realtime events on the per-user chat socket. Implementation outline: add to `socket_manager.py` a `send_event(user_id, payload)` wrapper over `_send_to_user_sessions`; change `ws_badge.notify_badge_update` (and `_ws_notify_request`, `auth_function.py:259-265`) to call `manager._send_to_user_sessions` **and** keep the legacy `/ws/badge` send for old clients until they are retired (sending to both registries is idempotent for the old client). Prefer moving the helper to `socket_manager.py` and having `ws_badge.py` import it (avoid a circular import: `requests_functions` imports `router.ws_badge` lazily today).
* Acceptance: a manager punches in → admin socket on `/ws/chat/{admin}` receives `badge_update`; a leave request → admin and managers receive `request_submitted` on `/ws/chat/`.

### 8.2 `badge_update` payload — frontend expects `notification` (title/body/image/type)
`interpretSocketFrame` (`events.ts`) reads `msg.notification.{id,title,body,image_url,type}`; without it the punch notification is `null`. Required frame: `{"type":"badge_update","notification":{"id":"<uuid>","title":"Punch In|Punch Out","body":"<name> punch in at 09:05 AM","image_url":"<url or ''>","type":"punch_in|punch_out"},"user_id":"<punching user>"}`. Source values already exist at `auth_function.py:271-273` (`title`, `body`, `data`); build once and reuse for push and socket. Fix BC P1-8 first (`face_attendance_functions.py:264`).

### 8.3 Group `read_update` — BC P1-6, DD-80
* Current: `{reader_id, group_id}` to all members including the reader (`router/chat.py:299-310`).
* Required: `{"type":"read_update","data":{"reader_id","group_id","message_id","read_count","total_members"}}` per message **whose sender is not the reader**, sent to that message's **sender's** sockets only (not the reader). Implementation: have `mark_group_as_read` return the marked `(chat_id, sender_id)` rows (the SELECT at `chat_functions.py:530-542` already has `chat_id`; add `c.sender_id`); after the inserts compute, per message, `read_count = COUNT(DISTINCT receiver_id)` of seen rows and `total_members = active members − 1` (same formula as REST, `:429-433`); emit one frame per message (cap, e.g., the latest 50, then fall back to one frame without `message_id` so the client refetches). Keep `reader_id` and `group_id`.
* Back-compat: Clockyn `frames.ts` accepts both shapes; old clients ignore extra fields.
* Acceptance: A sends 2 messages to a 3-member group; B marks read → A receives 2 frames with `read_count:1,total_members:2`; C marks read → `read_count:2` (double tick); B receives nothing.

### 8.4 `announcement` event — BC P1-7
Not sent today. Required frame to each recipient's `/ws/chat/` sockets: `{"type":"announcement","data":{"announcement_id","target","org_id"}}` (the app reads `target` from `msg.target` or `msg.data.target`, `events.ts`; values `all|selected|broadcast` count as organisation-wide, others as targeted). Implementation: in `announcement_functions.send_announcement` select `user_id` together with `push_token` for the audience (all four branches at :74-101; select user ids regardless of token presence) and `await manager._send_to_user_sessions(uid, frame)` after the commit/recipient inserts. Acceptance: staff with an open app receive the frame without a refetch; admins are not recipients (not in the audience).

### 8.5 `request_submitted` and `request_updated`
`request_submitted` exists (flat, badge socket) — move to chat socket per 8.1 and add `data: {request_type, request_id}` (additive). `request_updated` is only a push today; optionally emit `{"type":"request_updated","data":{"request_type","request_id","status"}}` to the requester so the staff request list refreshes (the app does not require it).

### 8.6 `new_message` / `new_group_message`
Already sent with fields the app uses (`mapChatMessage`/`mapGroupMessage`). Gaps: add `filename`, `content_type`, group `read_count:0`, `total_members` (so a freshly received own-device echo renders ticks), and `is_delivered`. `new_chat_message` and `delivered` are accepted by the client but need not be sent. Acceptance: file message arrives with `file_url` presigned and `message_type` `image|file`.

### 8.7 Group membership change events — BC P1-6
`POST /group-chat/add-members` must emit `group_updated` (existing members) and `group_created` (new members); see chat_and_groups.md §8.6. Add `group_updated` to the frontend interpreter in the same release (unknown types are ignored by the current client, so rollout order is safe).

### 8.8 `force_logout`
The app also listens for an FCM data message `force_logout` (push doc §8.4); the SSE endpoint stays for the old client. Optionally also emit `{"type":"force_logout"}` on the chat socket from `force_logout_user` (`session_registry.py:16-19`) so a foreground Clockyn device is logged out instantly without SSE (the app currently polls `check-session` every 30 s, DD-30/`sessionWatch.ts`).

### 8.9 Hardening (not in BC, recommended)
Validate `user_id` against the bearer token once an auth layer exists (W-1); membership check on the group socket; dedupe by connection kind; replace in-memory registry with Redis pub/sub before running more than one instance (W-10).

## 9. Test notes
* Unit-test `ConnectionManager` with fake sockets: two sockets per user receive each event; a socket raising on `send_json` is pruned; `disconnect` fallback behaviour.
* Integration (Starlette `TestClient.websocket_connect`): connect `/ws/chat/u1` and `/ws/chat/u2`, `POST /chat/send` → both receive `new_message` with the exact field set; `PUT /chat/mark-read` → sender receives `read_update`; `POST /group-chat/create` → non-creator receives `group_created`.
* Regression for W-5/8.1: connect `/ws/chat/admin`, trigger a request submission and (after BC P1-8) a staff punch-out → frames arrive with the payloads in 8.2/8.5.
* Multi-device: open two sockets for the same user and confirm both receive; reconnect race on `/ws/badge`.
* Load: slow-client send inside a handler should not exceed an acceptable latency; run with `--workers 2` to demonstrate the single-process limitation.
