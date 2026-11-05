# Support tickets, feature suggestions & deletion requests — backend context

Domain owner: BD4. Line numbers verified against the files as read.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/support.py` | 101 | 6 endpoints, no prefix (`/support/...`), mounted at `app.py:69`; request models `UpdateSupportRequest`, `ReplyRequest` (`:13-21`) |
| `core_functions/support_functions.py` | 217 | ticket logic (subjects, submit, update, reply, lists) |
| `ml/s3.py:286-320` | n/a | `upload_support_photo`, `get_support_photo_url` |
| `router/company.py:43-50`, `:53-107` | 107 | account-deletion request endpoints and the 5 suggest-feature endpoints (mounted under prefix `/company`, `router/company.py:7`, `app.py:42`) |
| `core_functions/suggest_feature_functions.py` | 225 | suggestion store, replies, SES e-mail fan-out |
| `core_functions/company_functions.py:336-402` | n/a | `request_account_deletion`, `get_deletion_requests` (the account deletion itself, `delete_user_account` `:408-`, belongs to the company domain) |
| `utils/email.py:34`, `:768`, `:863`, `:921`, `:1096` | n/a | `/invoice/*` endpoints (billing domain; listed in §7 for the unauthenticated-endpoint finding only) |

Frontend consumers: `src/api/support.ts` (subjects, list, submit, reply), `src/api/system.ts:97-118` (`POST /company/suggest-feature`, `POST /company/request-deletion/{id}`), `src/api/company.ts:71`. The app does **not** call `PUT /support/update`, `GET /support/requests/all`, `GET /company/suggest-feature*` (list/mine/with-replies/reply) or `GET /company/deletion-requests`; those serve an operator console.

## 2. Data model touched

Schema dump: `admin_subject_request` (dump line 41), `admin_support` (56), `email_log` (284), `suggestion_replies` (1062), `active_mail` (18), `account_deletion_requests` (2). **`ticket_replies` is not in the dump** (code references it, `support_functions.py:80`, `:147`, `:195`); the error text points at a migration file `change_db.sql` that does not exist in the repository.

### `admin_support` (ticket subjects, master list)
`id` int identity, `sub_id` varchar(36), `sub_name` varchar(255), `status` int2 default 0 (1 = active; only `status = 1` rows are offered or accepted), timestamps. Seeded manually (no endpoint).

### `admin_subject_request` (ticket)
| Column | Type | Meaning |
|---|---|---|
| `id` | int identity | ticket id (integer, used in URLs) |
| `support_id` | int4 | `admin_support.id` |
| `sub_id` | varchar(36) | `admin_support.sub_id` |
| `org_id` | varchar(36) | submitting org (client supplied) |
| `user_id` | varchar(36) | submitter (**column used by the code but absent from the dump**; UNVERIFIED in production) |
| `client_msg` | text | the ticket message |
| `status` | varchar(20) default `'created'` | observed values: `created`, `in_progress`, `resolved` (set by `reply_to_ticket` and `PUT /support/update`, which accepts any string) |
| `reply` | text | legacy single admin reply, written only by `PUT /support/update` |
| `photo_url` | text | **S3 key** (despite the name) of `client_support/{uuid}_{filename}`; converted to a presigned URL on read |
| `created_at`, `updated_at` | timestamp default `CURRENT_TIMESTAMP` | DB clock |

### `ticket_replies` (not in the dump; columns inferred from the code)
`id`, `ticket_id` (-> `admin_subject_request.id`), `reply_text`, `author_name`, `is_admin` bool, `created_at` (default assumed). Inferred only (UNVERIFIED).

### `email_log` (feature suggestions)
`id serial4` (**no primary key in the dump**), `org_id varchar(36)`, `date` date, `subject` text, `text` text, `created_at` timestamp. Reply table `suggestion_replies(id, email_log_id int4 NOT NULL, reply_text, admin_name default 'Admin', created_at default now)`, index on `email_log_id`, no FK. `active_mail(id, email varchar(50), status int4)`: operator recipients, `status = 1` active, no constraints.

### `account_deletion_requests`
`id`, `request_id` varchar(36) UNIQUE, `user_id`, `org_name`, `mobile_number`, `email`, `status` varchar(20) default `'pending'` (`pending`; `deleted` set by `delete_user_account`), `requested_at`, `actioned_at`.

No soft delete in any of these; there is no endpoint to delete tickets, suggestions or deletion requests.

## 3. Endpoints

### 3.1 Support (`router/support.py`; all handlers wrap exceptions; ValueError -> 400 or 404; others 500; no authentication; no plan gate)

| METHOD path | Handler (file:line) | Identity | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|
| GET `/support/subjects` | `support_subjects_route` (`:24`) -> `get_support_subjects` (`support_functions.py:9`) | none | none | **bare list** `[{sub_id, sub_name}]` ordered by `id`, `status=1` only | 500 on DB error |
| POST `/support/submit` | `support_submit_route` (`:32`) -> `submit_support_request` (`:19`) | form `user_id`, `org_id` | **multipart form**: `user_id`, `org_id`, `subject` (the subject **name**), `message` (all required), `photo?` file | `{success:true, data:{id, support_id, sub_id, org_id, user_id, client_msg, status:'created', photo_url:<S3 KEY or null>, created_at}}` | unknown/inactive subject -> **400** `Invalid subject: <x>`; photo upload failure is swallowed (`:45-51`) and the ticket is created without it with `success:true` |
| PUT `/support/update/{request_id}` | `support_update_route` (`:61`) -> `update_support_request` (`:43`) | none | path int; JSON `{status:str, reply?:str}` | `{success:true, data:{id, status, reply, updated_at}}` | unknown id -> **404** (ValueError); `status` unvalidated; `reply` omitted -> **stored as NULL (clears the previous legacy reply)** |
| POST `/support/{request_id}/reply` | `support_reply_route` (`:72`) -> `reply_to_ticket` (`:66`) | body `author_name`, `is_admin` | path int; JSON `{reply_text:str, author_name:str='Admin', is_admin:bool=True}` | `{success:true, data:{id, ticket_id, reply_text, author_name, is_admin, created_at, status}}` where `status` is the ticket's status after the call | blank text -> 400 `reply_text is required`; unknown ticket -> 404; missing `ticket_replies` table -> **503** `Reply feature requires DB migration...`; else 500 |
| GET `/support/requests/all` | `support_all_requests_route` (`:88`) -> `get_all_support_requests` (`:123`) | none | none | **bare list** of every ticket of every org incl. `user_mobile` | unauthenticated cross-org PII |
| GET `/support/requests/{org_id}` | `support_requests_route` (`:96`) -> `get_support_requests` (`:171`) | path | none | **bare list** `[{id, sub_id, sub_name, org_id, org_name, user_id, user_name, user_mobile, client_msg, status, reply, photo_url:<presigned or null>, created_at, updated_at, replies:[{reply_text, author_name, is_admin, created_at}]}]` newest first | falls back to a query without `replies` when the first query raises *any* error (`:201-214`) |

`/support/requests/all` is declared before `/support/requests/{org_id}` so the literal wins (`router/support.py:88` vs `:96`).

### 3.2 Feature suggestions (`router/company.py`, prefix `/company`; unauthenticated)

| METHOD path | Handler | Request | Success response | Errors / quirks |
|---|---|---|---|---|
| POST `/company/suggest-feature` | `suggest_feature_route` (`router/company.py:69`) -> `submit_suggest_feature` (`suggest_feature_functions.py:89`) | JSON `SuggestFeatureRequest {org_id:str='', subject:str, content:str, company_name:str='', user_email:str=''}` (`:53-59`) | `{success:true, message:"Suggestion submitted successfully"}` | stored text is `content` or `From: <company> (<email>)\n\n<content>` when `company_name` is set (`:72-74`); empty `org_id` allowed; 500 with raw error |
| GET `/company/suggest-feature` | `get_suggestions_route` (`:61`) -> `get_suggestions` (`:60`) | none | **bare list** `[{log_id, org_id, date, subject, text, content, created_at, company_name}]` all orgs newest first | unauthenticated, cross-org |
| GET `/company/suggest-feature/mine` | `get_my_suggestions_route` (`:84`) -> `get_my_suggestions` (`:141`) | query `org_id` (required) | **bare list** `[{log_id, org_id, subject, content, created_at, replies:[{reply_text, admin_name, created_at}]}]` | `GROUP BY el.id` while selecting other `email_log` columns: valid only if `id` is a primary key; the dump shows none (UNVERIFIED in production), otherwise this query errors with HTTP 500 |
| GET `/company/suggest-feature/with-replies` | `:89` -> `get_suggestions_with_reply_count` (`:181`) | none | **bare list** like the first plus `reply_count`, `last_reply_at`, `replies[]` | any exception silently falls back to `get_suggestions()` (`:93-95`) |
| POST `/company/suggest-feature/{log_id}/reply` | `reply_to_suggestion_route` (`:98`) -> `reply_to_suggestion` (`:118`) | path int; JSON dict `{reply_text, admin_name='Admin'}` | `{success:true, message:"Reply sent successfully"}` | blank -> 400; missing table -> 503 text mentioning `change_db.sql`; unknown `log_id` is **not checked** (orphan reply row); anyone can reply as any admin name |

### 3.3 Account deletion requests (`router/company.py`)

| METHOD path | Handler | Request | Success response | Errors / quirks |
|---|---|---|---|---|
| POST `/company/request-deletion/{user_id}` | `request_deletion_route` (`:43`) -> `request_account_deletion` (`company_functions.py:336`) | path | `{success:true, message:"Account deletion request submitted. An admin will review and process it."}` or, if one is already pending, `{success:true, message:"Deletion request already submitted. Awaiting admin review."}` | 404 `User not found`; 500 `Internal server error`. Does **not** deactivate or restrict the user |
| GET `/company/deletion-requests` | `deletion_requests_route` (`:48`) -> `get_deletion_requests` (`company_functions.py:376`) | none | **bare list** of pending rows `[{request_id, user_id, org_name, mobile, email, status, requested_at:"%d %b %Y %H:%M"}]` (blank fields shown as `—`) | unauthenticated PII; time string is UTC wall time |

Endpoint count in this document: 6 + 5 + 2 = 13.

## 4. Business logic

### 4.1 Submit ticket (`router/support.py:32-58`, `support_functions.py:19-40`)
1. Optional photo: `await photo.read()`; if bytes non-empty call `upload_support_photo(bytes, photo.filename, content_type or 'image/jpeg')` which puts `client_support/{uuid4 hex}_{filename}` into the bucket (`ml/s3.py:286-302`). **Any exception is logged and ignored** (the ticket is still created, `photo_key = None`), so a failed upload is invisible to the user. No size/type validation; the client file name is embedded in the key unsanitised.
2. `SELECT id, sub_id FROM admin_support WHERE sub_name = %s AND status = 1` (the subject is matched by **name**); none -> `ValueError("Invalid subject: ...")` -> HTTP 400.
3. INSERT `admin_subject_request(support_id, sub_id, org_id, user_id, client_msg, status='created', photo_url=<key>)` `RETURNING ...`; commit. The response `data.photo_url` is the raw **S3 key**, not a URL. `user_id`/`org_id` are not validated against `users`/`org_info`; empty message is not rejected server-side.

### 4.2 Reply and reopen semantics (`support_functions.py:66-106`)
1. Load `status` of the ticket (404 if absent).
2. INSERT into `ticket_replies(ticket_id, reply_text, author_name, is_admin)` `RETURNING ...` (`created_at` by column default, UNVERIFIED).
3. Status transitions: **admin reply on `created` -> `in_progress`**; **user (non-admin) reply on `resolved` -> `in_progress`** (reopen); every other combination leaves the status unchanged (admin reply on `in_progress` or `resolved` does not change it; user reply on `created`/`in_progress` does not change it). `resolved` is never set by a reply; only `PUT /support/update` can set it.
4. Commit; the response is the new reply row plus `status` = `new_status or old status`.
5. `is_admin` defaults to **true** and `author_name` to `"Admin"` when omitted (`router/support.py:18-21`); neither is authenticated, so any caller can post as the support team, and a customer client must explicitly send `is_admin:false` (the Clockyn client does, `src/api/support.ts replyToTicket`). No notification (push/e-mail/WS) is sent to either side for new tickets or replies.

### 4.3 Operator update (`:43-63`)
`UPDATE admin_subject_request SET status = %s, reply = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s RETURNING ...`; with `reply` omitted the legacy `reply` column becomes NULL. This path does not write `ticket_replies` and does not apply the reopen rule.

### 4.4 Lists and photo handling (`:109-217`)
`_rows_with_replies_and_photos`: `photo_url` (S3 key) -> `get_support_photo_url` (`ml/s3.py:305-320`): `head_object`, then a presigned GET valid 7 days (`ExpiresIn=604800`), `None` when the object is missing or on any error (printed, not logged); one S3 `head_object` per ticket with a photo (N+1). Query joins `admin_support`, `org_info`, `users` and `LEFT JOIN ticket_replies` with `json_agg(... ORDER BY tr.created_at ASC) FILTER (WHERE tr.id IS NOT NULL)`; `replies` is `[]` when none. On **any** exception the first query is retried in a reduced form without replies after `conn.rollback()` (so a transient DB error silently hides all replies). Replies carry no ids. `created_at` inside `replies` is a Postgres-rendered ISO string without zone.

### 4.5 Suggestions (`suggest_feature_functions.py`)
* **Submit** (`:89-115`): INSERT `email_log(org_id, date, subject, text, created_at)` with `now = get_utc_now()` and `date = now.date()` (UTC date, not IST); commit; then start a daemon thread running `_send_suggestion_emails` and return immediately.
* **E-mail** (`:12-57`): `SELECT email FROM active_mail WHERE status = 1`; sender `EMAIL_USER`; region `AWS_SES_REGION` (default `ap-south-1`); one `ses.send_email` per recipient with subject `[Feature Suggestion] <subject>` and an HTML body into which `org_id`, `subject` and `text` are interpolated **without HTML escaping** (HTML injection into operator inboxes); per-recipient failures are only logged; no retry; if `EMAIL_USER` is unset nothing is sent (error logged) while the API still returns success.
* **Lists/replies** (`:60-86`, `:118-225`): described in §3.2. Replies are stored in `suggestion_replies` with `admin_name`; nothing notifies the org; `get_my_suggestions` / `get_suggestions_with_reply_count` use `GROUP BY el.id` (see §3.2 note).

### 4.6 Deletion request (`company_functions.py:336-402`)
Look up the user with a `LEFT JOIN org_info ON o.parent_id = u.user_id` (so the organisation name is filled only for admins; for an admin with several orgs `LIMIT 1` picks an arbitrary one); 404 if the user does not exist; if a `pending` request exists for the user return the "already submitted" message with `success:true`; else INSERT `account_deletion_requests(request_id uuid4, user_id, org_name, mobile_number, email, 'pending', requested_at=UTC)` and commit. Nothing else changes: the account stays active, no e-mail/notification is sent. The only transition out of `pending` is `delete_user_account` (`company_functions.py:468-472`), which sets `status='deleted'`, `actioned_at` after physically deleting the account; there is no reject/cancel endpoint.

## 5. Side effects & integrations

| Flow | Integration | Detail |
|---|---|---|
| ticket photo | S3 `put_object` at `client_support/{uuid}_{filename}`; presigned GET (7 days) on every list | content type from the client (`image/jpeg` default) |
| suggestion | AWS SES `send_email` (HTML) from a background thread | recipients = `active_mail.status=1` |
| tickets, replies, deletion requests | none | **no push, WebSocket, e-mail or WhatsApp** to the user or to support staff |

## 6. Config & env vars

`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME` (S3; secrets read at `ml/s3.py:10-11`); `EMAIL_USER`, `AWS_SES_REGION` (suggestion e-mail; the SES client uses the default boto credential chain, `suggest_feature_functions.py:34`); DB settings (`DB_SCHEMA`, connection).

## 7. Known defects & risks

| # | Sev | Where | Defect / impact |
|---|---|---|---|
| S-1 | High | `router/support.py:88-93`, `:96-101` | **Unauthenticated** `/support/requests/all` returns every org's tickets including user names and mobile numbers; `/support/requests/{org_id}` is readable by anyone who knows an org id. |
| S-2 | High | `router/support.py:18-21`, `:72-85` | Reply endpoint is unauthenticated; `is_admin` defaults to true and `author_name` is free text: anyone can post a fake "Admin" reply into any ticket; no ownership/org check. |
| S-3 | High | `router/support.py:61-69`, `support_functions.py:43-63` | `PUT /support/update` unauthenticated: anyone can set any status/reply; omitting `reply` wipes the legacy reply; status unvalidated. |
| S-4 | High | `router/company.py:61-107`, `:43-50` | Unauthenticated operator-style endpoints: list of all suggestions (with org ids), reply as any admin name, list of pending account deletions with mobile/email (`/company/deletion-requests`), and (company domain) account deletion itself; `/invoice/history`, `/invoice/download`, `/invoice/details` (`utils/email.py:768`, `:863`, `:921`) take only `user_id`/`org_id`/`invoice_number` and `GET /invoice/bulk-download` (`utils/email.py:1096`) needs only a date range to download every customer's invoices (billing domain documents these in full; also `GET /orders/history/{org_id}`, `router/gen_orders.py:47`). |
| S-5 | Medium | `router/support.py:41-51` | Photo upload failures are swallowed: ticket reports success without the photo; no indicator in the response. |
| S-6 | Medium | `ml/s3.py:286-302` | Support photo: no size/type validation, client `filename` in the S3 key, client content type trusted; response and DB hold the S3 key under the name `photo_url`. |
| S-7 | Medium | `support_functions.py:128-166`, `:176-215` | Blanket `except Exception` fallback to a no-replies query hides real DB errors (replies silently vanish); 7-day presigned URLs minted on every list; N+1 `head_object` per ticket. |
| S-8 | Medium | schema dump | `ticket_replies` and `admin_subject_request.user_id` are absent from the dump while required by the code; the 503 message points to a non-existent `change_db.sql`; the repository has no migration for them. |
| S-9 | Medium | `suggest_feature_functions.py:35-43` | Unescaped HTML in the operator e-mail (subject/text/org id) -> HTML/phishing injection; recipients limited to `active_mail`. |
| S-10 | Medium | `suggest_feature_functions.py:141-178`, `:181-225` (+ dump) | `GROUP BY el.id` with non-aggregated columns requires `email_log.id` to be a primary key; the dump has none, so `/mine` and `/with-replies` may fail (the latter silently falls back, `router/company.py:93-95`). UNVERIFIED in production. |
| S-11 | Medium | `router/company.py:98-107` | Suggestion reply does not verify the suggestion exists and does not notify the org; replies are visible to the org only through `/mine`, which the Clockyn client does not call. |
| S-12 | Low | `company_functions.py:336-373` | Deletion request leaves the account fully active (the client comment says "the user is deactivated"); no cancel/reject; `requested_at` rendered in UTC without zone; admin with several orgs gets an arbitrary `org_name`. |
| S-13 | Low | `router/support.py:55-58` | Exceptions are mapped by type only: invalid subject is 400, but other `ValueError`s on update/reply are reported as 404; DB errors leak raw messages as 500. |
| S-14 | Low | `support_functions.py:19-40` | `user_id`/`org_id` of a ticket are trusted (a ticket can be filed for another org); empty/blank `message` accepted; no rate limit on tickets, suggestions or deletion requests (BC P2-7). |
| S-15 | Low | `support_functions.py:79-83` | `ticket_replies.created_at` relies on a column default for ordering; two replies in the same instant have no tie-breaker (`ORDER BY tr.created_at` only). |

## 8. Clockyn requirements

References: `backend_changes.md` (BC), `docs/DEFERRED_DECISIONS.md` (DD).

### F-1 Support ticket reply and reopen semantics (BC P2-12 area, DD-106 neighbour)
* **Current:** §4.2. The Clockyn client sends customer replies with `is_admin:false` and shows `status` mapped as open (`created`/`open`), `in_progress`, resolved (`resolved`/`closed`) (`mapTicketStatus`, `src/api/support.ts`).
* **Required change:** (a) default `is_admin` to **false**, and ignore the flag from unauthenticated callers once auth exists (derive admin from the caller role, BC P3-1); (b) block replies on tickets of other orgs (verify `admin_subject_request.org_id` equals the caller's org); (c) define and document the status machine: `created -> in_progress` on admin reply, `resolved -> in_progress` on user reply (existing), `resolved`/`closed` set only by an operator endpoint with validated values (`created`, `in_progress`, `resolved`); (d) make `PUT /support/update` stop clearing `reply` when omitted (`COALESCE`) and validate status; (e) add a push/e-mail to the user on an admin reply and to support on new tickets/replies (new; reuse `send_push_notification`, `core_functions/auth_function.py:204`).
* **Outline:** edit `router/support.py:18-21` and `support_functions.py:43-106`; add org check using a `org_id` query/body field the client already has; create the migration for `ticket_replies` (`id serial PK, ticket_id int REFERENCES admin_subject_request(id) ON DELETE CASCADE, reply_text text NOT NULL, author_name varchar(100), is_admin boolean NOT NULL DEFAULT false, created_at timestamp NOT NULL DEFAULT now()` + index on `ticket_id`) and `ALTER TABLE admin_subject_request ADD COLUMN IF NOT EXISTS user_id varchar(36)`, then remove the table-missing fallbacks (`:128-166`, `:176-215`, `router/support.py:83-84`).
* **Compat:** responses unchanged; existing clients that omit `is_admin` would now be treated as customers (the Clockyn client always sends it).
* **Acceptance:** admin reply on `created` -> `in_progress`; customer reply on `resolved` -> `in_progress`; customer reply on another org's ticket -> 403/404; `PUT update` with status only keeps the previous legacy reply; invalid status -> 400; reply order is chronological.

### F-2 Support ticket photo handling (BC P2-12)
* **Current:** failure swallowed (S-5); S3 key stored and returned as `photo_url`; presigned 7-day URLs on list.
* **Required change:** return `photo_uploaded: true|false` (and `photo_error` text) in the submit response, or fail with 502 when the client asked for a photo; validate type (`image/jpeg|png|webp`) and size (<= 10 MB), sanitise the file name (store `{uuid}.{ext}`), store the real content type; rename the DB/response semantics: keep `photo_url` as the presigned URL on reads, add `photo_key` internally; stop returning the key from submit.
* **Outline:** `router/support.py:41-53`, `ml/s3.py:286-302`, `support_functions.py:19-40`; use `abort-on-failure` flag `require_photo` form field (optional).
* **Compat:** additive response fields; the app (`submitTicket`, `ensureSuccess` only) keeps working and can start surfacing `photo_uploaded === false`.
* **Acceptance:** an oversized/invalid file returns 400/413; an S3 outage returns `photo_uploaded:false`; list rows carry a working `photo_url` for 7 days; the key never appears in submit responses.

### F-3 Unauthenticated support and invoice endpoints (BC P3-1, P3-6, P3-12)
* **Current:** S-1..S-4; Clockyn calls only the four customer endpoints (`subjects`, `requests/{org}`, `submit`, `{id}/reply`) plus suggestion and deletion request; it never calls the operator ones.
* **Required change:** move operator endpoints (`/support/requests/all`, `/support/update`, `/company/suggest-feature` GET/with-replies/reply, `/company/deletion-requests`, bulk invoice download) behind operator authentication; require a user token on customer endpoints and derive `org_id`/`user_id` from it; for invoices use signed URLs or authenticated download.
* **Outline:** introduce a FastAPI dependency (token from the existing session/device machinery) and apply it with `Depends` on these routers; for operators reuse the operator check pattern at `router/auth.py:148-170` (header `x-admin-id`) as a stop-gap.
* **Compat:** the four customer endpoints keep their paths; the client must attach the token once login issues one (`src/lib/api/client.ts`).
* **Acceptance:** each operator endpoint returns 401/403 without operator credentials; a customer token for org A cannot read org B's tickets or invoices.

### F-4 Suggest-feature (BC P2-7)
* **Current:** Clockyn only POSTs `{org_id, subject, content, company_name, user_email}`.
* **Required change:** validate `org_id` exists, cap `subject` (<= 150) and `content` (<= 4000), rate-limit per org/IP, HTML-escape all interpolated values in the e-mail (`html.escape`) and use a plain-text alternative part; give `email_log` a primary key (`ALTER TABLE email_log ADD PRIMARY KEY (id)`) so `/mine` and `/with-replies` work; verify the replies table with an FK to `email_log(id)`; surface e-mail failure in logs/metrics (the response may stay `success:true`).
* **Outline:** `suggest_feature_functions.py:35-43`, `:89-115`; migration for the PK/FK.
* **Compat:** same request/response. **Acceptance:** `<script>` in the content arrives escaped; a 10th request in an hour from one org returns 429; `GET /company/suggest-feature/mine?org_id=...` returns 200 with `replies` arrays.

### F-5 Deletion request semantics (BC P3-9, DD-40)
* **Current:** §4.6; the app text says the account is deactivated and it asks the support team to delete.
* **Required change:** decide whether a pending request suspends the account; if yes set `users.status = 0` (and clear tokens) for the requester and block login with a clear message; add authenticated operator endpoints to approve (calls `delete_user_account`) or reject/cancel the request; send a confirmation e-mail to the requester; keep the "already pending" idempotence.
* **Outline:** extend `request_account_deletion` (`company_functions.py:336`) and add `POST /company/deletion-requests/{request_id}/action`; data retention: also delete tickets, suggestions, S3 support photos and CFRs for the account (today they survive, see `delete_user_account`).
* **Acceptance:** second request returns the "already submitted" message; rejection restores access; approval removes the account and sets `actioned_at`.

### F-6 Envelope consistency for the lists (BC P2-6)
Support and suggestion lists are **bare arrays**; the client's `listOf` tolerates both. If an envelope is introduced return `{success:true, data:[...]}` only behind a new versioned path or query flag. Acceptance: old clients still parse the bare array.

## 9. Test notes

* **Fixtures:** `admin_support` rows (`status=1`/`0`), orgs and users, S3 mocked (patch `ml.s3.s3`), the `ticket_replies` and `suggestion_replies` tables created (and one run without them to exercise the 503 and fallback paths), `active_mail` rows, SES patched (`boto3.client`).
* **Critical cases:** submit with invalid subject (400); submit with photo while S3 raises (still `success:true`, no photo); reply transition matrix (admin/user x created/in_progress/resolved); reply with `is_admin` omitted (defaults true); `PUT update` without `reply` clears it; list falls back when `ticket_replies` is missing; presigned URL generation only when the object exists; suggestion e-mail with HTML in the text; `/mine` on a table without a primary key; duplicate deletion request returns the "already submitted" message; `/support/requests/all` reachable without credentials.
* **Existing tests:** none in the backend repository; frontend contract tests under `src/api/__tests__/`.
