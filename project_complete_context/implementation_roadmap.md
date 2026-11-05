# Clockyn backend implementation roadmap

Companion to `clockyn_backend_gap_analysis.md` (item IDs `P0-n`, `P1-n`, `P2-n`, `P3-n`, `NEW-n`, `C-n` and `DD-n` are defined there and in the frontend's `backend_changes.md` / `docs/DEFERRED_DECISIONS.md`) and `testing_strategy.md`.
Written 2026-10-19 by BD9. Documentation only. Legacy product names are not typed here (cited as "legacy" with `file:line`); no secret values appear, only variable names.
Effort: S under 1 day, M 1-3 days, L over 3 days, for one engineer including tests. Day estimates are planning figures, not commitments.

## 1. Ground rules

1. **Check the deployed server first.** `backend_changes.md` was verified against a snapshot dated 2026-09-05 and warns that the deployed code may be newer. For every P0 route and for P1-8 (`img_url`), the first task is "does the deployed server already have it, and what does it return?" (curl the route on the legacy host with a throwaway account); port the deployed code if it exists.
2. **Legacy client keeps working until retired.** Every change below is additive (new routes, new optional fields, new optional parameters) unless it is listed under "behaviour changes" in section 6. Behaviour changes ship behind an environment flag or after the legacy client is retired.
3. **One migration mechanism.** No DDL in request handlers; all DDL as numbered, idempotent (`IF NOT EXISTS`) files applied by the migrator (NEW-14, WP0).
4. **Contract = mock fixtures.** A change is done when the contract test for its routes (built from `e2e/mock-server`, see `testing_strategy.md` section 2) passes against the real backend, or the difference is recorded in gap-analysis Appendix C with a decision.
5. **Time.** Storage stays UTC; business logic uses IST explicitly through one helper (`utils/timezone.py`: new `ist_today()`, `business_date(dt)`, `ist_month_range(year, month)`); no query compares a `timestamptz` cast to `time` with a local shift time.

## 2. Must-decide-first (backend behaviour), with recommendation

Needed-by names the first work package that cannot start (or must not ship) without the answer. "Rec." is this review's recommendation; the product owner decides.

| # | Decision (DD refs) | Options | Rec. | Backend impact | Needed by |
|---|---|---|---|---|---|
| D-1 | Does Clockyn run on its **own backend and database**, or share the legacy deployment? (C-10, P0-8, P0-9) | own / shared | **Own deployment and database** (new app identity, rebrand, cleanup of drift and ids, no legacy constraint on behaviour changes) | own: P0-8 is a seed, P0-9 is five env vars, no `app_id`, no dual FCM; shared: both need code and every behaviour change must be flagged | WP0, WP8, WP11 |
| D-2 | Database session time zone and the business date of an attendance row (P2-5, DD-05, DD-14) | leave as is / pin UTC and use IST business date for new rows | **Pin the session to UTC** (`options=-c timezone=UTC` in `db_function.get_db_config`) and store `attendance_date` as the IST date of the punch for new rows; compare lateness in IST | removes every "[U] depends on DB time zone" caveat; boundary-day shift only for a database with history | WP1, WP5 |
| D-3 | Face check: advisory or enforced; `force_accept` (DD-01, P3-3) | keep / admin-approved override / N failed matches | **Parity release keeps `force_accept`** but returns `face_mismatch` per row and flags it in the daily report; next release requires an override token or 2 failed matches within 10 minutes | WP5 adds the flag to responses; enforcement is a later change | WP5 |
| D-4 | Geofence (DD-02, DD-03) | enforced as coded / configurable per branch | **Keep as coded** (enforced for non-field staff when the branch has GPS, `face_attendance_functions.py:170-178`); update product wording; "Punch Anywhere" stays a field-staff switch | none now | none |
| D-5 | Day types and absence rules for calendars (DD-07, DD-67) | client inference / server day types | **Server day types** through `detail=v2` (P1-3), client drops its inference later | WP5; FE-2 | WP5 |
| D-6 | Shift-change approval meaning (NEW-8, DD-90) | permanent shift change / one-day override | **One-day override** for `shift_change_date` (the request carries one date) | WP7 `shift_for(user, date)` | WP7 |
| D-7 | What leave approval does (DD-90, DD-91) | nothing / attendance day type / paid-unpaid in salary | **Attendance day type only**; no salary effect and no balance at launch; decisions stay all-dates (DD-91 keep) | WP5/WP7 | WP7 |
| D-8 | Salary proration and the attendance rule (DD-60, DD-61, P0-4) | rule stored only / rule drives `fine` / basic prorated by absences | **Rule drives `fine`** for orgs that saved a rule; **no proration** of basic at launch (parity with v1) | WP4; documents that absent days do not reduce monthly-fixed pay | WP4 |
| D-9 | Overtime formula (P0-5) | monthly aggregate (today) / per day | **Keep the monthly aggregate** so numbers do not move; fix only two-session and night-shift defects | WP4 | WP4 |
| D-10 | Manager scope (DD-50, DD-51, P1-17) | client convention / server scope | **Server scope, deny by default** (a manager without assignments sees nothing; today `None` means unrestricted, `manager_functions.py:400`) | WP12 (token) and WP6 helper | WP6 |
| D-11 | Single-device enforcement (DD-30, DD-31, DD-34) | OTP take-over (today) / block + request access | **Block + request access for roles 2/3** behind `ENFORCE_SINGLE_DEVICE`, admins stay multi-device, add `POST /auth/logout` | WP7 | WP7 |
| D-12 | Plan gating (DD-10, DD-11, DD-13, DD-14) | keep matrix / change | **Keep the matrix**, gate chat child routes and payroll/wage/incentive routes server-side too, plan expiry on the IST date inclusive of the end date, fix the stale docstring (`gen_plans_functions.py:174-196`) | WP6/WP10 | WP10 |
| D-13 | Billing: who prices, what renew means, trial limit, zero-amount (DD-15 to DD-18) | client / server quote | **Server quote** (`POST /orders/quote`), renew extends validity (P1-13), trial staff limit 10, zero-amount "switch plan" later | WP10; FE-3 | WP10 |
| D-14 | Data lifecycle (DD-40, DD-41, DD-43) | hard delete / soft delete + retention | **Soft delete with 30-day retention purge**, PII anonymised on account deletion, department delete keeps silent un-assign with an optional `reassign_to` | WP6 | WP6 |
| D-15 | Payroll validation bounds (DD-70 to DD-74) | client only / server | **Server validators**: amounts `0 < x <= 9,999,999.99`, two decimals, percentages `0..100`, uniqueness per org and type | WP2/WP4 | WP2 |
| D-16 | Chat, announcements (DD-80 to DD-85) | keep | **Keep**; announcement targets resolved server-side for push and socket (P1-7); chat delete = both sides (document) | WP8 | WP8 |
| D-17 | Request validation and CRM lifecycle (DD-90, DD-94, DD-95, DD-97) | client only / server | **Server**: reject overlapping leave dates of the same user, permission within 6 hours, carry-forward status persisted, "my leads" = due today or undated, calendar returns holiday and weekly-off entries | WP7/WP9 | WP9 |
| D-18 | Authentication token (DD-33, P3-1) | none / bearer | **Bearer token issued at OTP/device login, verified everywhere**, rolled out after launch behind `REQUIRE_AUTH`; frontend adds the header in `src/lib/api/client.ts` (one place) | WP12; FE-4 | WP12 |
| D-19 | Test/review accounts (hard-coded OTP bypass) | keep / env-gated | **Env-gated, off in production**; reviewers get a seeded org instead | WP11 | WP11 |
| D-20 | Webhook-first activation (NEW-16) | client verify only / both | **Both**, idempotent on "subscription exists for the order" | WP10 | WP10 |
| D-21 | Incentive formulas (`domains/incentives.md` D2) | keep flat payout / build per-staff count and turnover inputs | **Keep today's formula and document it**: COUNT/TURNOVER thresholds are stored but the database holds no per-staff count or turnover figure (`report_functions.py:619-647`); inputs are a separate feature | none now; WP4 documents it | WP4 |
| D-22 | Forgotten punch-out (`face_attendance_functions.py:194-201`) | admin edit only (today) / auto-close | **Auto-close at the next punch-in attempt when the open session is older than the 18-hour shift limit**, closing it at punch-in + shift length (8 h without a shift), `auto_closed` flag for admin review; until decided the admin edit remains the only recovery | WP5 (optional column) | WP5 |

## 3. Work packages

Lanes: A = data and platform, B = payroll, C = attendance and org data, D = realtime and requests, E = billing and identity, F = security. Packages in the same wave can run in parallel (different files); the "Files" lists show where they touch the same file so merge order can be planned.

### Wave 0: prerequisites (parallel, start immediately)

**WP0 Schema baseline and migrator (NEW-14)**; lane A; M (3 days).
Scope: `migrations/000_baseline.sql` from `pg_dump --schema-only` of the deployed database (someone with access; also dump the seed tables listed in gap-analysis Appendix A), `schema_migrations` table, extend `migrate.py` (apply all pending files in order, one transaction each, `DB_SCHEMA` honoured, `--all` and `--dry-run`), `001_drift_objects` (Appendix A objects, all `IF NOT EXISTS`), container start applies migrations when `RUN_MIGRATIONS=1`.
Files: `migrate.py`, `migrations/*.sql`, `Dockerfile` (command), `README` section (do not touch the legacy README text in this task, add a new file when implemented).
Prerequisites: read access to the deployed schema. Risk: low if every statement is `IF NOT EXISTS`; the unknown is what the deployed database really contains (constraint names). Tests: empty database plus 000-001 equals what the code expects (run the contract suite on it); re-run is a no-op. DoD: a clean database can be created by one command and the API boots against it; no DDL left in handlers except removed ones listed in WP9.
Rollout: schema only, no API change.

**WP1 Platform hygiene and test harness**; lane A; M (3 days).
Scope: (a) fix `app.py` mounts: remove the duplicate holiday routers only together with WP3, add a startup test that no `(method, path)` is registered twice (P2-15); (b) `utils/timezone.py` helpers (D-2) and `db_function` session time zone; (c) error handling: replace the ~90 `except Exception: raise HTTPException(500, str(e))` blocks by a pattern that re-raises `HTTPException` (P2-1), mechanically (`except HTTPException: raise` first), plus a global handler returning `{detail}` for unexpected errors without leaking internals; (d) pytest scaffold: Docker Postgres fixture, factories, `TestClient`, contract-test loader (`testing_strategy.md`); (e) remove token logging (`auth_function.py:818`) and the debug route `GET /auth/push-token-status/{id}` (P3-10), env-gated OTP bypass (D-19, `auth_function.py:29-34,517,645`); (f0) fix the connection leak in `get_db_connection` (a failed `SET search_path` after checkout never returns the connection, shrinking the 20-connection pool, `core_functions/db_function.py:57-66`; also roll back in `return_connection`); (f) test adapters (`STORAGE_BACKEND`, `FACE_BACKEND`, `PAYMENT_BACKEND`, `PUSH_BACKEND`, `SMS_BACKEND`, `MAIL_BACKEND`), `APP_ENV=test` guard, clock control and the `/__test/*` control plane described in `testing_strategy.md` section 3.1 and 6.2; (g) replace the six `uuid.UUID(user_id)` checks in `router/add_staff.py:152, 164, 173, 185, 194, 207` by a charset/length check so non-UUID fixture ids work.
Files: `app.py`, `utils/timezone.py`, `core_functions/db_function.py`, `router/*.py` and `core_functions/*.py` (mechanical), `tests/` (new), `requirements-dev.txt` (new).
Risk: the mechanical error-handling change touches every router; do it in one reviewable commit with the route table snapshot test proving no route changed. DoD: route table snapshot test, `pytest` green, no behaviour change except status codes of previously re-wrapped errors (listed in the commit).

### Wave 1: ships first, additive only

**WP2 Wages and shift assignment routes (P0-1, P0-2, P0-3, P0-7, P1-9 wages part)**; lane B; M (3 days).
Files: new `router/wages.py`, `core_functions/daily_hourly_functions.py` (scoping, cascade, `moved_from`), `core_functions/shift_functions.py` (four functions), `router/shift.py`, `app.py` (swap the import of the holiday-copy router). Prerequisites: WP0 optional (no DDL needed), check D-15 bounds. Risk: low. Tests: gap-analysis P0-1/2/3/7 test lists plus contract tests rows 139-150 and 202-205. Rollout: first release; the Daily Wages, Hourly Charges and Shift Assign screens start working. DoD: the 16 routes answer the mock contract; salary breakdown reflects assignments.

**WP11 App identity, configuration and branding (P0-8, P2-10, C-items)**; lane E; M (3 days).
Scope: version check per D-1 (seed rows, or `app_id`), an OTP throttle before public launch (per contact: at most 5 sends per hour and a 30-second resend gap, kept in a small `otp_send_log` table because the current OTP row is deleted and its attempt counter reset on every regeneration, which allows unbounded guessing and SMS/e-mail bombing, `auth_function.py:585-606`), and externalise every legacy brand string and link into configuration: OTP e-mail subject/body (`auth_function.py:304-362`), invoice header/legal entity/e-mail (`utils/email.py:209, 600-640, 726-729`, `gen_payment_functions.py:171, 179`), WhatsApp template name and store link (`whatsapp_service.py:29-33`), notification titles, static logos (`static/`); new env vars `APP_NAME`, `SUPPORT_EMAIL`, `SUPPORT_URL`, `SELLER_NAME`, `SELLER_ADDRESS`, `SELLER_GSTIN`, `SELLER_CIN`, `APP_PLAY_STORE_LINK` (exists), `APP_APP_STORE_LINK`, `S3_KEY_PREFIX`, `CORS_ALLOWED_ORIGINS`, `PUNCH_PHOTO_PREFIX`; delete `known_faces/` from the repository and add it to `.dockerignore`; remove unmounted root `chat.py`.
Files: `router/version.py`, `utils/email.py`, `core_functions/auth_function.py`, `core_functions/whatsapp_service.py`, `core_functions/gen_payment_functions.py`, `ml/s3.py`, `core_functions/emp_expenses_functions.py`, `app.py`, `.dockerignore`.
Risk: invoice legal text is a tax document; the seller entity and GSTIN are owner decisions (section 7). Tests: rendered e-mail/invoice contain no legacy token (grep test over rendered output), version contract tests. DoD: `grep -ri` for the legacy tokens over code returns only the rename inventory.

**WP8 Push service and realtime delivery (P0-9, NEW-6, NEW-7, P1-7)**; lane D; M (4 days).
Files: new `core_functions/push_service.py`, new `core_functions/realtime.py`, `core_functions/auth_function.py` (delegate send, force_logout push), `router/chat.py`, `router/ws_badge.py`, `core_functions/requests_functions.py`, `core_functions/announcement_functions.py`, `socket_manager.py`. Prerequisites: Firebase project for the new bundle id and its service account (C-2); D-1 decides the fallback. Risk: medium (touches the hot path of chat and punch notifications); keep the old functions as thin wrappers for one release. Tests: gap-analysis P0-9 and P1-7 lists; two-socket integration test. DoD: a real push reaches one device per platform from the Clockyn project; the admin badge updates live on `/ws/chat`.

### Wave 2: correctness of existing endpoints (parallel lanes)

**WP3 Holidays and weekly offs (P1-5)**; lane C; M (2-3 days). Prereq WP0, WP1. Files: `router/holiday.py`, delete `router/daily_hourly.py` (only after WP2 swapped it) and the copy in `router/report.py:25-47`, `core_functions/holiday_functions.py`, migration for `block_attendance`/`org_off_days`/unique index. Risk: the live handlers change owner in this release; run the holiday contract test before and after. Tests: P1-5 list. DoD: one registration per holiday route; `block_attendance` round-trips; bulk add works.

**WP5 Attendance integrity (NEW-2, P1-8, NEW-3, NEW-4, P1-3, P2-5)**; lane C; L (6 days). Prereq WP0, WP1 (time helpers), D-2. Files: `core_functions/face_attendance_functions.py`, `core_functions/attendance_functions.py`, `router/face_attendance.py`, `core_functions/staff_location_functions.py`, `core_functions/home_functions.py` (lateness in the four dashboard queries), `core_functions/holiday_functions.py` (`check_holiday_block` IST), migration `002_attendance_sessions`. Also: savepoints around the location inserts (a failed insert aborts the transaction, `face_attendance_functions.py:216-224, 309-317`), the stale-open-session policy (D-22), and aggregation of two-session rows in the staff-location queries (NEW-4). Quick fix P1-8 ships immediately as its own commit (section 5). Risk: highest data-semantics risk in the project: `attendance_date` becomes the IST date for new rows; run the boundary tests (punch at 00:30 IST) and decide on the optional backfill only for a database with history. Tests: P1-3, NEW-2, NEW-3, NEW-4 lists; two-session day end to end (punch, punch out, punch, punch out, today, sessions, monthly, daily, PDF). DoD: second session works with the real index; no endpoint returns a UTC clock string; monthly attendance `detail=v2` implemented behind the parameter.

**WP6 Staff and organisation data (NEW-1, NEW-15, NEW-17, P1-2, P1-4, P1-9, P1-10, P2-11, P1-17 helper)**; lane C; L (6 days). Prereq WP0, D-10, D-14. Files: `core_functions/add_staff_function.py`, `core_functions/home_functions.py`, `core_functions/branch_functions.py`, `core_functions/dept_functions.py`, `core_functions/company_functions.py`, `core_functions/salary_structure.py` (delete), `core_functions/manager_functions.py` + new `core_functions/scope.py`, migration `007_branch_id_seq`. Risk: delete semantics change (cascade/soft delete): ship soft delete first, purge job later. Tests: P1-2, P1-4, P1-9, P1-10, NEW-1, NEW-15, NEW-17 lists; two-organisation collision fixture. DoD: no query joins `branch_info` without the org; all deletes are org-scoped; manager edit no longer demotes.

**WP7 Requests and sessions (P1-1, P1-14, P1-15, NEW-8, NEW-10, P2-8, P3-4)**; lane D; M (5 days). Prereq D-6, D-7, D-11; WP8 for the push/WS changes. Files: `core_functions/requests_functions.py`, `router/requests.py`, `core_functions/auth_function.py` (`verify_device_token`, `validate_otp`), `core_functions/shift_functions.py` (`shift_for`), counts route. Risk: authorization on update-status can lock out a client that sends a wrong `action_by`; log-only for one release (`REQUESTS_ENFORCE_ACTOR=0`) then enforce. Tests: P1-1, P1-14, P1-15 lists. DoD: leave status round-trips; device-change dedupe; approved shift swap affects lateness for that date only.

### Wave 3: payroll

**WP4 Payroll and reports (NEW-12, NEW-13, P0-4, P0-5, P0-6, P1-12, P1-16)**; lane B; L (10 days). Prereq WP0 (tables), WP5 (distinct-day and IST fixes), WP3 (working-days for `fine`), D-8, D-9. Files: new `core_functions/salary_engine.py`, `core_functions/overtime.py`, `core_functions/attendance_rule_functions.py`, `core_functions/salary_payment_functions.py`, `router/attendance_rule.py`, `router/salary_payments.py`, `router/incentive.py`, `router/salary.py`, `core_functions/report_functions.py` (wrapper), `core_functions/emp_expenses_functions.py`, migrations 003, 004, 005. Order inside the package: (1) snapshot test of the old engine on a fixed seed; (2) engine refactor with identical output; (3) overtime helper and report; (4) paid state; (5) attendance rule and `fine`; (6) allowance-rate table. Risk: payroll numbers; the three intentional changes are listed in NEW-12. Tests: P0-4/5/6 and NEW-12 lists; snapshot; statement-count test. DoD: breakdown, monthly list and overtime report agree row by row; `fine` non-zero only for configured orgs.

### Wave 4

**WP9 CRM leads (P1-11, NEW-5, P2-7 categories, `leads_info.status` width)**; lane D; M (4-5 days). Prereq WP0 (migration 006: dedupe, unique index, `prev_status`; run on a copy first, the dedupe deletes rows). Files: `core_functions/leads_functions.py`, `router/leads.py`. Tests: P1-11 list. DoD: no DDL in handlers; list returns one row per lead with `lead_status`.

**WP10 Billing (P1-13, NEW-16, P3-2, P2-10 invoices, D-12/D-13)**; lane E; L (6 days). Prereq D-12, D-13, D-20, owner Razorpay account (section 7), FE-3. Files: `core_functions/gen_orders_functions.py`, `core_functions/gen_payment_functions.py`, `core_functions/gen_webhook_functions.py`, `core_functions/gen_plans_functions.py`, `utils/email.py`, migration 008, new `POST /orders/quote`. Risk: money; test with Razorpay test keys, webhook replay, duplicate verify. DoD: renew extends; webhook and verify race both activate exactly once; amount recomputed server-side; invoice shows the seller entity from configuration.

### Wave 5

**WP12 Security (P3-1, P1-17 complete, P3-2..P3-14)**; lane F; L (10-15 days). Prereq D-18, FE-4, WP6 scope helper. Scope: bearer token at login/OTP/device verify, `Depends(current_user)` on every route, identity and org derived from the token (id parameters validated against it), roles and manager permissions enforced, WebSocket handshake token, operator routes behind operator auth, CORS origins from `CORS_ALLOWED_ORIGINS`, upload limits, signed invoice/PDF URLs, `POST /auth/logout`, rate limits (OTP, support, suggestions, device requests), `REQUIRE_AUTH` flag (off, then on). Risk: breaks any client without the header; roll out per environment. DoD: every route in the route table has an auth dependency or is on an explicit public list; a test iterates the route table to assert it.

**WP13 Performance and consistency (P2-8, P2-9, P2-6)**; lane A; M (5 days). Counts endpoint, pagination, bulk attendance/late report endpoint, `shift_id` on the staff list, envelopes. Optional after launch.

**WP14 Legacy removal (after retirement)**; S. Delete Appendix D "legacy" routes, `/ws/badge`, SSE route, duplicate aliases.

## 4. Order and parallelism

```
Wave 0: WP0 ─┬─ WP1                       (start together; WP1 helpers feed everything)
Wave 1: WP2 ─┤ WP11 ─┤ WP8              (additive; WP2 can ship before WP0 finishes)
Wave 2: WP3 ─┤ WP5 ─┤ WP6 ─┤ WP7         (WP5 needs WP1+WP0; WP7 needs WP8 for push/WS; WP3 before WP4)
Wave 3: WP4                                (needs WP5, WP3, D-8, D-9)
Wave 4: WP9 ─┤ WP10                        (WP10 needs owner Razorpay account and D-13)
Wave 5: WP12, WP13, WP14
```
Critical path: WP0 -> WP1 -> WP5 -> WP4 (about 22 engineer-days sequential). Two engineers can finish waves 0-3 in about 5 weeks; three or four engineers can cover waves 0-4 in about 4 weeks plus the owner-dependent items.

## 5. Quick wins (each under one day, no migration, safe for the legacy client)

| # | Change | Where | Gap ref |
|---|---|---|---|
| 1 | Replace `img_url` with `None`, guard the post-commit notification | `core_functions/face_attendance_functions.py:259-265` | P1-8 |
| 2 | Add `message_id` to the four chat push payloads | `router/chat.py:198, 286, 411, 478` | P1-7 |
| 3 | Add `role_id`, `is_active` to the five live-status lists | `core_functions/home_functions.py:603, 680, 753, 831` | P1-10 |
| 4 | Re-raise 404 in staff detail/profile and drop `status = 1` | `home_functions.py:1112, 1146, 1172, 1225` | P1-10 |
| 5 | Add `branch_id`, `dept_id`, `org_id` to detail/profile and `/addstaff/{id}` | `home_functions.py`, `add_staff_function.py:70-93, 473-501` | P1-2 |
| 6 | `PUT /addstaff/{id}`: stop writing `role_id`, keep branch/dept/shift when omitted | `add_staff_function.py:579-606` | P1-4 |
| 7 | Employee requests: reuse the admin join, add `date_statuses` and shift ids | `requests_functions.py:276-326` | P1-1 |
| 8 | Leave/permission aggregate status after a decision | `requests_functions.py:445-476` | P1-15 |
| 9 | `/attendance/sessions`: IST format, `work_hours`, real error status | `router/face_attendance.py:58-91` | NEW-3 |
| 10 | Group `read_update`: counts and message id, skip the reader | `router/chat.py:299-311`, `chat_functions.py:520-557` | P1-6 |
| 11 | Plan check outside the `try` in the two chat list routes | `router/chat.py:168-174, 226-232` | P2-2 |
| 12 | Registration: re-raise `HTTPException` | `auth_function.py:473-478` | P2-3 |
| 13 | `check-mobile` / `check-email`: allow unauthenticated `{exists}` | `router/auth.py:304-329` | P3-5 |
| 14 | Remove token logging and the debug route | `auth_function.py:818`, `router/auth.py:386-407` | P3-10 |
| 15 | Branch delete and reassign count scoped by org | `branch_functions.py:241-244, 331-335` | P1-9 |
| 16 | Scope the six `branch_info` joins by org | NEW-1 phase A list | NEW-1 |
| 17 | Staff location log: use the open session, accept 0 coordinates | `staff_location_functions.py:34-69` | NEW-4 |
| 18 | `delete_staff` to status 2, exclude 2 from lists | `add_staff_function.py:641-645`, `home_functions.py:473-552` | NEW-15 |
| 19 | Remove the dead export `getReceiverPushToken` (frontend) | `src/api/chat.ts:150-159` | row 39 |
| 20 | `employee-login`: return `email` | `auth_function.py:1038-1111` | row 9 |

## 6. Rollout

**Ships first (additive, safe for the legacy client):** WP2 routes, WP4's new routes (attendance rule, overtime report, monthly list, mark paid, allowance-rate table), WP11 seeds and configuration, WP8 push module and extra WebSocket frames, additive response fields (P1-1, P1-2, P1-10, NEW-3), migrations 001-005, quick wins 1-3, 5, 7, 9-14, 17, 19-20.

**Behaviour changes (coordinate; flag or announce):**

| Change | Flag / mitigation |
|---|---|
| Lateness in IST (dashboards, monthly attendance) | none: it is a bug fix; announce that numbers change if the database time zone was UTC |
| `attendance_date` as IST date for new rows | D-2; boundary tests; optional backfill only with history |
| Two-session day counted once in daily wages and overtime; night-shift overtime; `fine` | release note (NEW-12) |
| Delete cascades / soft delete; payslip delete refuses with 409 | announce; `DELETE /api/salary/assign/{org}/{staff}` ships in the same release |
| Update-status authorisation | `REQUESTS_ENFORCE_ACTOR` log-only first |
| Single-device enforcement | `ENFORCE_SINGLE_DEVICE` default off |
| Staff delete = status 2 | announce (deleted staff vanish from lists) |
| Branch id re-key (only if collisions exist) | maintenance window, snapshot first |
| `REQUIRE_AUTH` | per environment, after FE-4 |

**Frontend dependencies** (small, listed so they are scheduled): FE-1 `app_id` on `GET /version/check` (shared backend only); FE-2 consume `detail=v2` monthly attendance and drop inference; FE-3 send order `intent`; FE-4 `Authorization` header in `src/lib/api/client.ts`; FE-5 pass `org_id` on `GET /branch/{id}`; FE-6 use the bulk holiday route; FE-7 remove the dead `getReceiverPushToken`; FE-8 show "deleted for both" on chat delete.

**Rollback:** every release is a container image; migrations are forward-only and additive, so rollback is redeploying the previous image. Destructive migrations (leads dedupe, branch re-key, backfills) run only after a snapshot and are the only ones that need a restore to undo.

**Smoke after every deploy (5 minutes):** route-table snapshot test, contract suite, one OTP login per role, punch in and out, a leave request and approval, a chat message with push, `GET /version/check`.

## 7. Owner configuration items and the backend settings they drive

| ID | Item (frontend) | Backend setting / location | Effect | Needed by |
|---|---|---|---|---|
| C-1 | Package and bundle id (`APP_ID`, default `com.clockyn.app`) | `app_version_control.app_id` seed (shared backend) or the seed row only (own backend); FCM project must contain the same ids | version prompt targets the right listing | WP11 |
| C-2 | New Firebase project, service account, APNs key | `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY`, `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID` (or `FCM_SERVICE_ACCOUNT_PATH` for local); optional `FCM_LEGACY_*` for a dual-send period; `FCM_ANDROID_CHANNEL_ID` (keep `attendance_notifications`); the APNs key is uploaded in the Firebase console, not in the backend | push delivery (P0-9) | WP8 |
| C-3 | Native maps key | none (client only) | Android map tiles | none |
| C-4 | Expo project | none | builds | none |
| C-5 | Support e-mail | `SUPPORT_EMAIL`, `SUPPORT_URL` (replace the legacy contact in the invoice footer and OTP e-mails), `EMAIL_USER` must be an SES-verified sender in `AWS_SES_REGION` and the account out of the SES sandbox | e-mail footers and delivery | WP11 |
| C-6 | Sales e-mail | `active_mail` rows (recipients of feature-suggestion e-mails, `suggest_feature_functions.py:12-27`); no env var | who receives suggestions | WP11 |
| C-7 | Privacy policy URL | none in the backend; reference it in e-mail templates if wanted | store forms | none |
| C-8 | Web portal URL | `CORS_ALLOWED_ORIGINS` (replaces the wildcard at `app.py:20-34`) | browser access | WP12 (or WP11) |
| C-9 | Store listing URLs | `app_version_control.store_url` (per platform), `APP_PLAY_STORE_LINK` and `APP_APP_STORE_LINK` (WhatsApp invitation) | update prompt, invitations | WP11 |
| C-10 | Backend base URL | DNS and TLS certificate, reverse-proxy settings: WebSocket upgrade on `/ws/*`, idle timeout of at least 300 s (the sockets are quiet between messages), upload body limit of at least 25 MB (four face photos, chat files), `--proxy-headers` for uvicorn behind a proxy; single worker unless realtime fan-out is moved to Redis (gap Appendix B) | all traffic | WP0 |
| C-11 | Payment account and branding | row in `gen_razorpay_key` with `status = 1` (key id, secret, mode; read with a 5-minute cache, `utils/razorpay.py:42-68`), `RAZORPAY_WEBHOOK_SECRET`, the webhook URL `https://<api>/webhook/razorpay` registered in the Razorpay dashboard, seller legal entity and GST details for invoices (`SELLER_*`), SAC code and place of supply (`gen_payment_functions.py:171, 181`) | checkout and tax invoices | WP10 |
| (new) | SMS OTP | `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID`; the wording and header are the provider's DLT-registered template, which must be re-registered for the new name (lead time) | OTP SMS | WP11 |
| (new) | WhatsApp invitation | `MSG91_WHATSAPP_AUTH_KEY`, `MSG91_WHATSAPP_NUMBER`, `MSG91_WHATSAPP_TEMPLATE` (default name is the legacy template, `whatsapp_service.py:29`), approval lead time at the provider | staff onboarding message | WP11 |
| (new) | Meta install attribution | `META_ACCESS_TOKEN`, `META_PIXEL_ID` (optional; `services/meta_events.py:23-24`) | `POST /attribution/meta` forwards events | optional |
| (new) | Object storage | `S3_BUCKET_NAME`, `AWS_S3_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (prefer an instance role), proposed `S3_KEY_PREFIX` | photos, logos, embeddings, chat files | WP0/WP11 |
| (new) | Database | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SCHEMA` (required, `db_function.py:23-27`) | all data | WP0 |

**S3 naming.** One bucket (`S3_BUCKET_NAME`). Prefixes written by the code: `known_faces/<user_id>/<uuid>.npy` (`ml/s3.py:24-62`), `org_logo/<org_id>.png` (`:118`), `employee_profile/<user_id>.jpg` (`:156`), `<PUNCH_PHOTO_PREFIX>/<org_id>/<user_id>/<attendance_id>_<action>.jpg` (`:187-195`; the constant carries a misspelling that "matches the existing S3 path", make it an environment variable with the legacy value as default so a new bucket can use a correct name), `documents/<uuid>_<file>` (chat, `:237`), `client_support/<uuid>_<file>` (`:290`), `odo_meter_photos/<org>/<user>/<date>_<type>.png` (`emp_expenses_functions.py:32`), `travel_bill_photos/<org>/<user>/<expense>.png` (`:241`). Presigned URLs live 7 days (`expires_in=604800`). Recommendation: a **new bucket** for Clockyn (or the new prefix `S3_KEY_PREFIX=clockyn/` in a shared bucket) so face embeddings of legacy users are never reachable by the new app; IAM needs `s3:GetObject`, `PutObject`, `DeleteObject`, `ListBucket` on the bucket and `head_object`. Existing objects are not migrated unless users are migrated (D-1).

**Environment variable inventory (names only).** Existing, read by code: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SCHEMA`; `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_REGION`, `S3_BUCKET_NAME`; `AWS_SES_REGION`, `EMAIL_USER`; `MSG91_AUTH_KEY`, `MSG91_TEMPLATE_ID`; `MSG91_WHATSAPP_AUTH_KEY`, `MSG91_WHATSAPP_NUMBER`, `MSG91_WHATSAPP_TEMPLATE`, `APP_PLAY_STORE_LINK`; `FCM_PROJECT_ID`, `FCM_PRIVATE_KEY_ID`, `FCM_PRIVATE_KEY`, `FCM_CLIENT_EMAIL`, `FCM_CLIENT_ID`, `FCM_SERVICE_ACCOUNT_PATH`; `RAZORPAY_WEBHOOK_SECRET`; `META_ACCESS_TOKEN`, `META_PIXEL_ID`; development: `WATCHFILES_IGNORE_PATHS`, `TF_CPP_MIN_LOG_LEVEL`. The README also lists `MSG91_SENDER_ID`, `META_APP_ID`, `META_CLIENT_TOKEN`, which no code reads. Proposed new: `ENFORCE_SINGLE_DEVICE`, `REQUESTS_ENFORCE_ACTOR`, `REQUIRE_AUTH`, `TRIAL_STAFF_LIMIT`, `DEFAULT_ODOMETER_RATE`, `FCM_ANDROID_CHANNEL_ID`, `FCM_LEGACY_PROJECT_ID`, `FCM_LEGACY_PRIVATE_KEY_ID`, `FCM_LEGACY_PRIVATE_KEY`, `FCM_LEGACY_CLIENT_EMAIL`, `FCM_LEGACY_CLIENT_ID`, `APP_NAME`, `SUPPORT_EMAIL`, `SUPPORT_URL`, `SELLER_NAME`, `SELLER_ADDRESS`, `SELLER_GSTIN`, `SELLER_CIN`, `APP_APP_STORE_LINK`, `S3_KEY_PREFIX`, `PUNCH_PHOTO_PREFIX`, `CORS_ALLOWED_ORIGINS`, `RUN_MIGRATIONS`, `OTP_TEST_BYPASS_ENABLED`.

## 8. Risks and open questions

| Risk | Mitigation |
|---|---|
| The deployed database differs from the dump in ways nobody knows (constraint names, enum labels, extra tables from the missing `change_db.sql`) | WP0 baseline from the real database before any DDL; all DDL idempotent |
| Database session time zone unknown | pin it (D-2) and test lateness with explicit offsets |
| Two sessions per day versus the unique index (NEW-2) | `\d attendance` on the deployed database is the first command of WP5 |
| Payroll numbers change | snapshot test, intentional changes listed, release note |
| The mock is more lenient than the backend (Appendix C) | contract tests run against the real backend; fix the mock in the same pull request as each backend change |
| No authentication (P3-1) until WP12 | document the exposure; keep the operator routes off the public host; rate limits at the proxy |
| Realtime state is in process memory | run one worker; document; move to Redis only when scaling out |
| Owner items late (Firebase, Razorpay, SES, DLT, WhatsApp template approvals have provider lead times) | start the provider requests in wave 0 |
