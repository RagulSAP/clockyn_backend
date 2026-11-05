# Announcements — backend context

Domain owner: BD4. Line numbers verified against the files as read.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/announcement.py` | 39 | **live** endpoints `/api/announcements*` (no router prefix; each path spelled out), mounted at `app.py:55` |
| `core_functions/announcement_functions.py` | 251 | send / admin list / delete / employee feed, `SendAnnouncementRequest` (`:15-25`) |
| `router/chat.py:321-353`, `:484-491` | n/a | **legacy** `/announcement/*` family mounted through the chat router (`router/chat.py:23`, mounted at `app.py:54`) |
| `core_functions/chat_functions.py:620-742`, `:745-800`, `:803-` | n/a | legacy send/list functions (with a different request model `SendAnnouncementRequest`, `chat_functions.py:32-38`) |
| `core_functions/auth_function.py:164-208` | n/a | `send_push_notification` used for push delivery |
| `core_functions/add_staff_function.py:752` | n/a | permanent staff delete removes the user's `announcement_recipients` rows |

"Find where mounted": the live family is `router/announcement.py` (app.py:55). The frontend uses **only** the live family (`src/api/announcements.ts`, header comment: the legacy `/announcement/*` family has no callers and is not ported).

Frontend consumer: `src/api/announcements.ts`; push/WS handling in `src/lib/realtime/events.ts`, `globalRealtime.ts`.

## 2. Data model touched

Schema dump: `announcement` (dump line 79), `announcement_recipients` (98). Joins: `users`, `branch_info`, `dept_info`.

### `announcement`
| Column | Type | Meaning |
|---|---|---|
| `id` | int identity PK | internal |
| `announcement_id` | varchar(36), UNIQUE index | uuid4; the id exposed as `id` in lists |
| `announcement_type` | varchar(10) default `'text'` | request default `"text"`; >10 chars -> DB error |
| `title` | varchar(100) | >100 chars -> DB error (500). NULL for legacy rows |
| `message` | text | full text; push truncates to 100 chars |
| `org_id` | varchar(36) | owner org |
| `target` | varchar(20) default `'all'` | `all`, `selected`, `branch`, `dept` (not validated: any string is stored) |
| `branch_id` | int4 NULL | stored only when `target in ('branch','dept')` (`announcement_functions.py:53`) |
| `dept_id` | varchar(36) NULL | same rule (`:54`) |
| `sent_by_id` | varchar(36) | sender user id (must be role 1) |
| `created_at`, `updated_at` | timestamp | `get_utc_now()` written explicitly (UTC wall time, zone-less in JSON) |

### `announcement_recipients`
`announcement_id`, `user_id`, `created_at`; PK is only the surrogate `id`; **no unique key** on (`announcement_id`,`user_id`), no FK. Populated only for `target='selected'`.

No soft delete; hard delete of both tables' rows by `DELETE /api/announcements/{id}`. No read/seen state is stored server-side (the app keeps seen ids locally, `unseenAnnouncementIds` in `src/api/announcements.ts`).

## 3. Endpoints

Live family (all handlers catch `Exception` and re-raise as **HTTP 500** `detail=str(e)`; no authentication; no plan gate):

| METHOD path | Handler (file:line) | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| POST `/api/announcements` | `send_announcement_route` (`router/announcement.py:10`) -> `send_announcement` (`announcement_functions.py:30`) | body `sent_by_id` | JSON `{org_id:str, sent_by_id:str, title:str, message:str, announcement_type:str='text', target:str='all', selected_user_ids?:str[], branch_id?:int, dept_id?:str}` | `{success:true, data:{announcement_id, title, message, org_id, target, created_at, recipient_count}}` | non-admin or unknown sender -> **500** `Only admin can send announcements`; over-long title/type -> 500 DB error |
| GET `/api/announcements/{org_id}` | `get_announcements_route` (`:18`) -> `get_announcements` (`:131`) | path | none | `{success:true, data:[{id, title, message, target, branch_id, dept_id, created_at, sent_by, branch_name, dept_name, recipient_count}]}` newest first | unauthenticated org history; no pagination |
| DELETE `/api/announcements/{announcement_id}` | `delete_announcement_route` (`:26`) -> `delete_announcement` (`:170`) | path | none | `{success:true}` | unknown id -> **200** `{success:false, message:"Announcement not found"}`; not org-scoped, no caller check |
| GET `/api/announcements/employee/{user_id}` | `get_employee_announcements_route` (`:34`) -> `get_announcements_for_employee` (`:198`) | path | none | `{success:true, data:[{id, title, message, target, created_at, sent_by, branch_name, dept_name}]}` | unknown user -> `{success:true, data:[]}`; no `branch_id/dept_id/recipient_count` |

Route-matching note: `GET /api/announcements/employee/{user_id}` has two path segments and cannot be captured by `GET /api/announcements/{org_id}` (one segment); no ordering problem.

Legacy family (mounted via the chat router, **unused by Clockyn**, still publicly reachable; documented for completeness and risk):

| METHOD path | Handler | Request | Response |
|---|---|---|---|
| POST `/announcement/send-all` | `router/chat.py:323` -> `send_announcement_all` (`chat_functions.py:620`) | `{sender_id, org_id, message, announcement_type='text', branch_id?, dept_id?}` | bare row `{announcement_id, message, org_id, created_at}`; 500 `Only admin can send announcements` |
| GET `/announcement/all/{org_id}` | `:330` -> `get_announcements_all` (`:665`) | none | bare list of rows where `branch_id IS NULL AND dept_id IS NULL` |
| POST `/announcement/send-branch-dept` | `:339` -> `send_announcement_branch_dept` (`:683`) | same model; `branch_id` or `dept_id` required | bare row |
| GET `/announcement/branch-dept/{org_id}` | `:346` -> `get_announcements_branch_dept` (`:745`) | optional `branch_id`, `dept_id` | bare list |
| GET `/announcement/employee/{user_id}` | `:486` -> `get_announcements_for_employee` (`chat_functions.py:803`) | none | bare list (org-wide + user's branch/dept) |

Endpoint count: 4 live + 5 legacy = 9.

## 4. Business logic

### 4.1 Send (`announcement_functions.py:30-126`)
1. Sender check: `SELECT role_id FROM users WHERE user_id = sent_by_id`; reject unless `role_id == 1` (`:35-38`). The sender's **organisation is not compared with `req.org_id`**: any admin can post into any org id.
2. `announcement_id = uuid4`, `now = get_utc_now()`.
3. INSERT into `announcement` with `branch_id`/`dept_id` kept only for targets `branch` and `dept` (`:53-54`); for `dept` both ids are stored if both were sent; `RETURNING announcement_id, title, message, org_id, target, created_at`; **commit** (`:57`).
4. `target == 'selected'` and non-empty `selected_user_ids`: INSERT one `announcement_recipients` row per id (duplicates in the list produce duplicate rows) and **commit again** (`:61-67`); `recipient_count = len(list)` (not de-duplicated). Otherwise `recipient_count = 0`. Because the announcement row is committed first, a failure while inserting recipients leaves an announcement with no/partial recipients.
5. Push-token lookup (`:72-103`), all require `push_token` non-empty:
   * `all`: `users WHERE parent_id = org AND role_id IN (2,3)`.
   * `selected`: `user_id = ANY(selected_user_ids)` (**no org or role filter**).
   * `branch` (requires truthy `branch_id`): `parent_id = org AND branch_id = %s AND role_id IN (2,3)`.
   * `dept` (requires truthy `dept_id`): `parent_id = org AND dept_id = %s AND role_id IN (2,3)` (branch ignored).
   * anything else (unknown target, `branch` without `branch_id`, `selected` with an empty list): no tokens; the row is stored anyway.
6. One `asyncio.ensure_future(send_push_notification(...))` per token (fire and forget, errors only logged inside the sender). Response is returned without waiting.

Validation gaps: `target` not validated; `selected` with empty list is accepted; `branch`/`dept` without id is accepted and stored with NULL ids (visible in admin history, to nobody in the employee feed); `title`/`message` emptiness is not checked.

### 4.2 Admin list (`:131-165`)
`announcement a LEFT JOIN users u (sent_by = u.name) LEFT JOIN branch_info b ON b.branch_id = a.branch_id AND b.org_id = a.org_id LEFT JOIN dept_info d ON d.dept_id = a.dept_id AND d.org_id = a.org_id WHERE a.org_id = %s ORDER BY a.created_at DESC`; `recipient_count` = count of `announcement_recipients` for `selected`, else `0`.

### 4.3 Delete (`:170-193`)
`DELETE FROM announcement_recipients WHERE announcement_id`; `DELETE FROM announcement WHERE announcement_id RETURNING announcement_id`; commit; `success:false` if nothing deleted. No org or sender check; irreversible; devices that already received the push keep it.

### 4.4 Employee feed: recipient filtering and ordering (`:198-251`)
1. Read the caller's `parent_id` (org), `branch_id`, `dept_id` from `users` (no status/role check: inactive users and role-1 ids work; for an admin id whose `parent_id` is not the org the feed is empty, UNVERIFIED).
2. `WHERE a.org_id = <user's org> AND ( a.target='all' OR (a.target='branch' AND a.branch_id = <user branch>) OR (a.target='dept' AND a.dept_id = <user dept>) OR (a.target='selected' AND EXISTS (recipient row for this user)) )`, `ORDER BY a.created_at DESC` (newest first), no limit/paging.
3. Consequences: a dept announcement ignores the branch it was created with (feed and push both match on `dept_id` only); a user with NULL branch/dept never matches branch/dept targets (`NULL = x` is false); announcements created before the user joined are shown; legacy rows (below) match as `target='all'`.
4. `id` is the string `announcement_id`; `created_at` zone-less UTC.

### 4.5 Legacy functions
`send_announcement_all` / `send_announcement_branch_dept` (`chat_functions.py:620-742`) insert rows with **no `title` and no `target`** (the column default `'all'` applies), so a legacy **branch/dept** announcement appears in the new employee feed as an org-wide `all` announcement with an empty title. Their recipient query filters on `users.org_id` (not `parent_id`) and the push payloads are `{type:'announcement', announcement_id, org_id}` (+ `branch_id`, `dept_id` for the targeted one) with title `Announcement from <sender name>`; they omit `target`.

## 5. Side effects & integrations

| Event | Channel | Recipients | Payload |
|---|---|---|---|
| live send | FCM push (`send_push_notification`, `auth_function.py:204`) | tokens per §4.1 step 5 | title = `req.title`; body = `req.message[:100]`; data `{type:"announcement", announcement_id:<uuid>, org_id, target:<all\|selected\|branch\|dept>}` (values stringified by the sender, `auth_function.py:169`); Android channel `attendance_notifications` |
| live send | WebSocket | **none**: no `announcement` event is published although the client handles one (`events.ts:77`, `:126`) | n/a |
| legacy send | FCM push | org staff | see §4.5 |
| delete | none | n/a | n/a |

No e-mail, no WhatsApp. Push is best effort (no delivery receipt, no retry, no per-recipient record).

## 6. Config & env vars

FCM service-account variables `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`, `FCM_PRIVATE_KEY_ID`, `FCM_CLIENT_ID` (secret at `core_functions/auth_function.py:61-100`); DB settings.

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| A-1 | High | `announcement_functions.py:35-38` | Sender is checked only as "some role-1 user"; org not matched; no authentication, so any admin id (or any caller who knows one) can broadcast to any org; Delete and admin list are fully unauthenticated and unscoped (`:131`, `:170`). |
| A-2 | Medium | `:81-87` | `selected` push goes to any user ids supplied (no org/role filter): cross-org push of an announcement title/body is possible, while the feed (org-scoped) will never show it. |
| A-3 | Medium | `:30-126` | Non-atomic: announcement committed before recipients (`:57` vs `:67`); duplicate ids create duplicate recipient rows and an inflated `recipient_count`. |
| A-4 | Medium | `:20-23`, `:72-103` | `target` unvalidated; `branch`/`dept`/`selected` with missing ids are stored but reach nobody; admin sees them in history with no recipients and no error. |
| A-5 | Medium | `router/announcement.py` | All failures (including "Only admin..." and DB length errors) are HTTP 500 with the raw message; delete of unknown id is 200 `success:false`. |
| A-6 | Medium | `chat_functions.py:620-742` + `announcement_functions.py:236-238` | Legacy branch/dept rows have `target='all'` by column default and no title, so they show org-wide with an empty title in the live feed; the legacy endpoints are still public. |
| A-7 | Medium | `:18-23` (schema dump) | `title varchar(100)`, `announcement_type varchar(10)`: over-length input -> DB error 500; no length validation. |
| A-8 | Low | `:72-103` | Dept targeting ignores the branch (push and feed); the Clockyn picker does not scope departments to the chosen branch (DD-82). |
| A-9 | Low | `:198-251` | Feed has no pagination/limit, no read state, includes pre-join announcements; inactive users still query it. |
| A-10 | Low | `:105-118` | Pushes are `ensure_future` tasks never awaited or collected: a restart loses them; no failure surface to the sender; no WebSocket event (BC P1-7). |
| A-11 | Low | `:105-118` | The push body is cut at 100 chars and the title is the admin-entered title (not the sender name) so recipients cannot see who sent it. |

## 8. Clockyn requirements

References: `backend_changes.md` (BC), `docs/DEFERRED_DECISIONS.md` (DD).

### N-1 Targeting rules: all / selected / branch / dept (DD-82)
* **Current:** §4.1 and §4.4. The server already filters recipients per user's own branch, department and selection and returns newest first with a string id (confirmed). Known gaps: dept ignores branch, unvalidated `target`, missing-id targets stored silently, picker offers inactive users.
* **Required change:** validate `target in {all, selected, branch, dept}` and the matching id (400 with a clear `detail`); reject `selected` with an empty list; for `dept` also honour `branch_id` when sent (treat as AND) or document dept-only semantics; de-duplicate `selected_user_ids`; restrict `selected` recipients to active users of the org (ignore/reject others); store an explicit recipient snapshot count.
* **Outline:** `send_announcement` (`announcement_functions.py:30`): add a validation block after the admin check; filter token queries by `status = 1`; in the `selected` token query add `AND parent_id = %s`; wrap announcement + recipient inserts in one transaction (remove the first commit at `:57`); add `UNIQUE (announcement_id, user_id)` on `announcement_recipients` after cleaning duplicates; widen `title` to `varchar(150)` only if product wants longer titles, else validate length.
* **Compat:** same request/response shape; stricter 400s for invalid combos the client already prevents (`createAnnouncement` checks them, `src/api/announcements.ts`).
* **Acceptance:** `target='branch'` without `branch_id` -> 400; `selected` with `[]` -> 400; `selected` with a user of another org -> that user is ignored (not pushed, not in feed); duplicate ids create one recipient row and `recipient_count` equals distinct ids; a user in branch A never sees a branch-B announcement; dept announcements reach only that department.

### N-2 Push payload (`announcement_id`, `target`) and `message_id` parity (BC P1-7)
* **Current:** the live push already carries `type:"announcement"`, `announcement_id`, `org_id`, `target` (`announcement_functions.py:112-117`) — this contradicts the older engineer log that said it was missing. Gaps: no WebSocket event; the legacy pushes omit `target`.
* **Required change:** keep the payload; add `branch_id` and `dept_id` for targeted sends (the legacy payload had them) so the client can classify without a refetch; publish the realtime event (N-3). Optionally put the sender name in the body (`<sender>: <message[:100]>`).
* **Outline:** extend the dict at `announcement_functions.py:112-117`; no change to the sender (`auth_function.py:169` stringifies).
* **Compat:** additive keys only. **Acceptance:** captured FCM data for each target contains the documented keys with string values; `target` equals the request target.

### N-3 Realtime `announcement` event (BC P1-7)
* **Current:** none published. The client handles `{type:'announcement', target|data.target}` on its realtime socket (`events.ts:77`, `:126`) and refetches the list after any event.
* **Required change:** after the commit publish `{type:"announcement", data:{announcement_id, target, org_id}}` to each recipient's open socket (same recipient set as the push, but without the push-token filter so users without a token still get it).
* **Outline:** resolve recipient user ids with the same queries as §4.1 but selecting `user_id`; call the global socket helper used for badges (`router/ws_badge.py:10` `notify_badge_update(user_id, event_type)` sends only `{"type": ...}`; extend it to accept a `data` dict or reuse the chat socket manager in `socket_manager.py`). 
* **Compat:** additive event type; clients already tolerate it. **Acceptance:** a connected recipient receives exactly one event per send; a non-recipient receives none.

### N-4 Employee feed ordering and filtering (DD-82, DD-95-like)
* **Current:** newest first by `created_at`; org scoped; matches target rules; no paging; legacy rows leak as `all`.
* **Required change:** order by `created_at DESC, id DESC` (stable); add optional `limit`/`before` paging; exclude announcements created before the user's join date if product wants; exclude legacy rows without `title` or migrate them (set `target='branch'`/`'dept'` where `branch_id`/`dept_id` is not null; `title = LEFT(message, 50)`); return `sent_by` role-neutral; add `GET /api/announcements/employee/{user_id}/unread-count?since=` only if a server-side seen state is introduced.
* **Outline:** one-off migration `UPDATE announcement SET target = CASE WHEN dept_id IS NOT NULL THEN 'dept' WHEN branch_id IS NOT NULL THEN 'branch' ELSE target END WHERE title IS NULL`; add the `ORDER BY` tiebreaker and optional params to `get_announcements_for_employee` (`:198`).
* **Compat:** default behaviour unchanged. **Acceptance:** migrated legacy branch rows no longer reach other branches; identical `created_at` rows keep a stable order.

### N-5 Delete scoping (BC P1-9, P3-9)
* **Current:** A-1.
* **Required change:** `DELETE /api/announcements/{id}` requires `org_id` and `sent_by_id`/caller (role 1 of that org) and returns 404 (not 200 `success:false`) for an unknown id; same for the admin list.
* **Outline:** add `org_id` query param; `WHERE announcement_id = %s AND org_id = %s`; return `HTTPException(404)` and fix the router wrapper to re-raise `HTTPException` (BC P2-1).
* **Compat:** until the client sends `org_id`, accept it as optional and log. **Acceptance:** deleting another org's announcement returns 404 and removes nothing.

### N-6 Legacy family (BC P2-15)
Remove `/announcement/*` from `router/chat.py` (or gate behind a flag) once confirmed unused by v1 apps in production. Acceptance: routes return 404; the live family is unaffected.

## 9. Test notes

* **Fixtures:** one org with admin, manager and staff in two branches and two departments; users with and without `push_token`; a second org; patch `core_functions.auth_function._send_push_to_token` to capture payloads; legacy rows with NULL `title`.
* **Critical cases:** non-admin sender rejected (500 text today); each target's recipient set for push and feed; `selected` push to a foreign-org user (current defect); dept vs branch matching with NULL user branch/dept; ordering; delete leaves recipients none; the first commit surviving a recipient failure (inject an error on the recipient insert); legacy branch row visible as `all`; over-length title -> 500.
* **Existing tests:** none in the backend; frontend `src/api/__tests__/announcementsLeads.test.ts`, `features/announcements/__tests__/targets.test.ts`.

### Changes (explicit recipients scoped to the organisation)

* `send_announcement` with `target='selected'` now keeps only ids of active users of the announcement's organisation before anything is written: one query `SELECT user_id FROM users WHERE user_id = ANY(%s) AND (org_id = %s OR parent_id = %s) AND status = 1` (staff by `parent_id`, owner/admin row by `org_id`). Foreign, unknown, inactive and duplicate ids are not stored in `announcement_recipients`; `recipient_count` is the number stored (response shape unchanged).
* A non-empty list with no valid id answers **400** `No valid recipients selected` and creates no announcement (the route re-raises `HTTPException`). An empty list behaves as before (accepted, 0 recipients).
* Tests: `tests/test_announce_template_scope.py`. Not verified without a database: the member-filter statement against real PostgreSQL.
