# Clockyn backend testing strategy

Companion to `clockyn_backend_gap_analysis.md` (route table, designs, Appendix C mock divergences) and `implementation_roadmap.md` (work packages).
Written 2026-10-19 by BD9. Documentation only: nothing was run, no database or network service was contacted, no file outside `project_complete_context/` was changed. Legacy names are not typed; no secret appears (placeholders such as `<local-dev-only>` are throwaway values for a disposable local database).

## 1. Layers and what each one proves

| Layer | Tool | Runs against | Proves | Owner WP |
|---|---|---|---|---|
| L0 Unit | pytest | pure functions (lateness, overtime, aggregates, penalty maths, version compare, scope SQL builder) | formulas and edge cases | each WP |
| L1 API integration | pytest + FastAPI `TestClient` + Postgres | real routers, real SQL, stubbed externals | behaviour and SQL of every changed route, multi-tenant isolation, time zones | each WP |
| L2 Contract | pytest + manifest generated from the mock | the same routes, asserted against the shapes the app was verified with | the real backend returns what `src/api` mappers read | WP1 (runner), each WP (rows) |
| L3 Typed-client live smoke | jest (`jest-expo`) in the frontend repo | a running backend, using the app's own `src/api/*.ts` | the app's mappers and envelopes accept the real responses end to end | frontend + WP1 |
| L4 Playwright live | Playwright (`e2e/`) | built web export + running backend in test mode | whole screens against real data | WP1 (control plane) |
| L5 Migrations | pytest | empty database + `migrations/000..N`, then a copy of a production-like dump | schema equals what the code expects; migrations idempotent | WP0 |
| L6 Non-functional | pytest markers | clocks, concurrency, statement counts, WebSockets, PDFs | time zone boundaries, races, N+1, realtime, binary outputs | WP4/5/8 |

Gate for each release: L0-L2 and L5 100 %, L3 100 %, L4 smoke subset 100 %, full L4 reviewed (section 6).

## 2. Contract tests derived from `e2e/mock-server`

The mock is "the executable contract" (`backend_changes.md` section 0): every response shape the app was verified against is produced by `e2e/mock-server/routes/*.js` over the seed in `state.js`/`fixtures.js`. Two corrections apply: the mock is lenient in places and wrong in a few (gap-analysis Appendix C), and the app's mappers (`src/api/mappers/*.ts`, `src/api/*.ts`) are the real consumer.

### 2.1 Endpoint-existence test (prevents the P0 class of defects)

A generated manifest `contract/endpoints.json` lists the 239 concrete method+path rows of gap-analysis section 3 (method, path template, caller, request example). Generation: parse `src/api/*.ts` for `api.<verb>(` calls and the URL builders (`apiUrl`, `chatSocketPath`, `groupSocketPath`) and expand the templated families (`{count,turnover,overtime,bonus}`, `{assigned,unassigned}`, the two wage bases, the five live-status paths, the four PDF variants); the same inventory already exists as per-module unit tests in `src/api/__tests__`. The test imports the FastAPI app and asserts every row matches a registered route of the same method (`app.routes`, converting `{id}` segments), prints the unmatched rows, and also asserts that no `(method, path)` is registered twice (gap P2-15). Expected result today: 22 unmatched rows (21 real, 1 dead export, listed in the gap table); after WP2/WP4/WP11 the only unmatched row is the dead export, which the frontend removes (FE-7). This test is the single most valuable one: it fails in seconds when a router is mislabelled, as `router/daily_hourly.py:8` is today.

### 2.2 Shape contracts

Manifest `contract/shapes/<family>.json`, one entry per call:
```json
{ "id": "requests.employee.list",
  "request": { "method": "GET", "path": "/requests/employee/{user}/{org}", "as": "staff" },
  "status": 200,
  "envelope": "success+data",
  "required": { "data[]": { "request_id": "string", "request_type": "enum:leave|permission|shift_change|device_change",
                            "status": "string|null", "dates": "string[]", "date_statuses?": "map<date,{status,action_by,action_at}>",
                            "requested_shift_id?": "int|null", "created_at": "string" } },
  "app_reads": ["src/api/mappers/requests.ts#mapRequest"],
  "notes": "P1-1: date_statuses and shift ids become required after WP7" }
```
Rules: keys listed in `required` must exist with the type (JSON number vs numeric string accepted where the app's `num()` accepts both, `src/api/mappers/common.ts`); keys under `?` are optional until the work package flips them to required (the manifest is the backlog made executable); envelopes accepted per endpoint exactly as the app tolerates (`listOf`, `objectOf`): bare array, `{data: []}`, `{success, data}`; HTTP status and `{detail}` shape for error cases; values are never compared, except for seeded deterministic fields (counts, ids) in dedicated semantic tests.

Generation: run the mock, replay a call plan (the one in `e2e/mock-server/selfcheck.js` is a ready-made starting point, extend it to every row of section 2.1), infer key/type/optionality from the responses, then hand-correct with an `overrides.json` that encodes the app-verified truth where the mock is wrong (for example `salary_type` enum, Appendix C row 1). The extractor is a 100-line node script in the frontend repo (`e2e/tools/extract-contract.js`), its output is committed to the backend repo as test data and regenerated when `src/api` changes.

### 2.3 Priority contract cases (must exist before the matching WP ships)

| Case id | Route(s) | Seed dependency | Gap |
|---|---|---|---|
| wages.* (12) | `/api/daily-wages/*`, `/api/hourly-charges/*`, `/api/wages/*` | templates `dw-1`, `dw-2`, `hc-1`, assigned staff | P0-1..3 |
| rule.get, rule.save | `/api/attendance-rule/{org}` | none / saved rule | P0-4 |
| ot.report | `/api/incentive/overtime-report/{org}` | overtime template `inc-o1` with assigned staff, attendance | P0-5 |
| salary.monthlyList, salary.markPaid | `/salary/monthly-list`, `/salary/mark-paid` | payslips `ps-1`, `ps-2`, assigned staff | P0-6 |
| shift.assign.* (4) | `/shift/{assigned,unassigned}_staff`, `assign_staff`, `unassign_staff` | shifts 1-3 | P0-7 |
| version.check | `/version/check` | version rows | P0-8 |
| punch.in, punch.out, today, sessions | `/face/punch`, `/attendance/today`, `/attendance/sessions` | face-enrolled staff `stf-0001` (not punched in), `stf-0002` field staff | P1-8, NEW-2/3 |
| att.monthly(.v2) | `/home/monthly-attendance` | 45 days of history, holidays, weekly offs, leave | P1-3 |
| requests.* | employee list, admin list, update-status, calendar, check-session | requests `req-001..301` | P1-1, P1-15 |
| staff.detail, staff.profile, staff.lists | `/home/*staff*`, `/addstaff/{id}` | inactive staff `stf-0005` | P1-2, P1-10 |
| leads.list, leads.assign, cfr.* | `/api/leads/*` | leads `lead-001..012`, 2 CF requests | P1-11 |
| chat.* / group.* / ann.* | REST + WS frames | groups `grp-1`, `grp-2`, announcements `ann-1..4` | P1-6, P1-7 |
| billing.* | plans, subscription, order, verify (fake gateway), invoices | plan states trial/paid/expired/none | P1-13 |
| allowance.* | rate get/set, approve stamping | odometer readings | P1-12 |

## 3. Local run recipe (Docker, Postgres, no secrets)

Prerequisites: Docker with Compose, Node 20+ (frontend tooling), nothing else on the host. The Python environment is the project's own image (`Dockerfile`: `python:3.11-slim`, system libraries for the PDF stack); do not use the host Python (the pins in `requirements.txt` target 3.11).

`docker-compose.test.yml` (to be added by WP1; shown here as the specification):
```yaml
services:
  db:
    image: postgres:16-alpine
    environment: { POSTGRES_USER: postgres, POSTGRES_PASSWORD: "<local-dev-only>", POSTGRES_DB: clockyn_test }
    ports: ["55432:5432"]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U postgres -d clockyn_test"], interval: 3s, retries: 20 }
  api:
    build: .
    depends_on: { db: { condition: service_healthy } }
    environment:
      APP_ENV: test
      DB_HOST: db
      DB_PORT: "5432"
      DB_NAME: clockyn_test
      DB_USER: postgres
      DB_PASSWORD: "<local-dev-only>"
      DB_SCHEMA: public
      RUN_MIGRATIONS: "1"
      OTP_TEST_BYPASS_ENABLED: "1"
      STORAGE_BACKEND: memory      # test adapters, section 3.2
      FACE_BACKEND: fake
      PAYMENT_BACKEND: fake
      PUSH_BACKEND: capture
      SMS_BACKEND: capture
      MAIL_BACKEND: capture
      TEST_NOW_ISO: ""             # set to freeze the clock
    ports: ["8080:8080"]
    command: ["sh", "-c", "python migrate.py --all && python -m tests.seed && uvicorn app:app --host 0.0.0.0 --port 8080"]
```
Commands: `docker compose -f docker-compose.test.yml up -d db`, then `docker compose -f docker-compose.test.yml run --rm api pytest -q` (tests create an isolated schema per test session through `DB_SCHEMA`), or `up api` to get a running backend for L3/L4. `.env` stays git-ignored (`.gitignore:138`); the compose file carries only the placeholders above. Setting `DB_SCHEMA` is mandatory (`core_functions/db_function.py:23-27`).

### 3.1 Test configuration needed in the code (WP1)

| Need | Why | Design |
|---|---|---|
| `APP_ENV=test` switch | enables test adapters and the `/__test/*` control plane; refuses to start if the database host is not local or the env is `production` | single module `core_functions/runtime.py` |
| Importable app without the ML stack | `ml/face.py:46-51` loads an InsightFace model and an ONNX session at import (network download on first use); `ml/s3.py` builds a boto3 client at import | with `FACE_BACKEND=fake` register a stub `ml.face` module (`encode_face`, `verify_face_global`, `load_all_embeddings`, `EMBEDDING_CACHE`) before `core_functions.face_recognition` imports it; fake encode returns a fixed unit vector, verify matches unless the `faceMismatch` flag is set |
| In-memory object storage | S3 calls in `ml/s3.py` and `emp_expenses_functions.py:25-60, 239-265` | `STORAGE_BACKEND=memory`: one dict-backed implementation of the 15 functions, presigned URLs returned as `http://<host>/files/<key>` served by a test-only route |
| Payment gateway fake | `utils/razorpay.py` calls the provider | `PAYMENT_BACKEND=fake`: `create_order` returns `order_test_<n>`, signature check uses a fixed test secret; the browser's Razorpay script is already stubbed by Playwright (`e2e/support/external.ts`) |
| Push, SMS, mail capture | `push_service.send_push`, `_send_msg91_sms`, SES calls | capture into an in-memory list exposed by `GET /__test/pushes` (same name as the mock) |
| Clock control | lateness, holidays, plan expiry, month ranges | `utils/timezone.get_utc_now()` honours `TEST_NOW_ISO` and an offset set through `POST /__test/state {clock}`; no other code calls `datetime.now()`/`date.today()` (gap P2-5 removes the stragglers) |
| Test OTP | the seeded logins use OTP `123456` | `OTP_TEST_BYPASS_ENABLED=1` accepts `123456` for the seeded mobiles only (replaces the hard-coded numbers at `auth_function.py:29-34`) |
| Relax the UUID check on user ids | six routes call `uuid.UUID(user_id)` (`router/add_staff.py:152, 164, 173, 185, 194, 207`), so the mock-style ids (`stf-0001`) answer 400 | replace with a charset/length check (`^[A-Za-z0-9_-]{1,36}$`); real ids (UUIDs) still pass; this lets the Playwright fixtures run unchanged |

### 3.2 Stub boundaries

```
HTTP -> router/* -> core_functions/* -> psycopg2 (real Postgres)
                       |-> ml.face (stub)        |-> ml.s3 / storage adapter (memory)
                       |-> push_service (capture) |-> utils.razorpay (fake) |-> SES / MSG91 / WhatsApp (capture)
```
Everything between the HTTP layer and Postgres is real, so SQL defects such as the unscoped branch joins (NEW-1) and the unique-index conflict (NEW-2) are caught.

## 4. Seed data

**Source of truth: `e2e/mock-server/state.js` + `fixtures.js`.** Do not hand-write a second dataset. A node exporter (`e2e/tools/export-seed-sql.js`, frontend repo) calls `createState()` with a fixed clock and writes `seed.sql` for the real schema; the backend repo commits the generated file and a Python loader (`python -m tests.seed`) applies it. Dates are produced relative to a seed "today" (IST) so the data is always about 45 days of history; tests that need exact dates freeze the clock (`TEST_NOW_ISO`).

| Mock state | Real tables |
|---|---|
| `orgs`, branches, departments, shifts | `org_info`, `branch_info` (`gps_location` point, `punch_radius`), `dept_info`, `shift` |
| `users` (admin, 2 managers, 23 staff; roles 1/2/3; field, inactive, face-enrolled flags) | `users` (`parent_id` = org id for staff, `org_id` for the admin, `device_token`), `users_additional_info` (all NOT NULL text columns as `''`), `manager_assignments`, manager permissions in the legacy-prefixed permission table |
| attendance sessions (up to two per day, late flags) | `attendance` (UTC `punch_in/out`, `work_hours`, `attendance_date`, `punch_in_location`) |
| requests (leave, permission, shift, device) with `date_statuses` | `requests`, `leave_dates` |
| leads, categories, assignments, carry-forward | `leads_info`, `leads_category_info`, `leads_assignment_info`, `carry_forward_requests` |
| messages, groups, announcements | `chat`, `chat_activity`, `"group"`, `group_members`, `announcement`, `announcement_recipients` |
| payslips, wages, incentives, advances, odometer, expenses | `pay_slip`, `payslip_add_*`, `payroll`, `daily_hourly_wages`, `assigned_wages`, `incentive_*`, `assigned_incentives`, `advance_details`, `odometer_readings`, `travel_bill_expenses` |
| plan catalogue, subscription, invoices, coupon, support | `gen_plans`, subscription table (legacy-prefixed), `free_trial`, `gen_invoices`, `gen_coupons`, `admin_support`, `admin_subject_request` |

Extra rows the mock never needed: `role_info` (1 Admin, 2 Manager, 3 Staff), the ten permission names exactly as in `src/features/team-rules/utils/permissions.ts:14-25` in the settings table (ids 1-10), `gen_razorpay_key` (one `status = 1` row with dummy values), `app_version_control` rows, `active_mail` (one local address), `gen_admin_login` (none).
Plan states (`trial`, `core`, `prime`, `elite`, `expired`, `none`) are produced by SQL helpers (`free_trial` and the subscription row) behind `POST /__test/state {plan}`.
Second-tenant fixture for isolation tests: organisation `org-0002` with a branch whose `branch_id` equals the first organisation's branch 101 and different GPS, plus staff and attendance (used by NEW-1, P1-9, P1-17 tests).
Login identities and tokens are those of `fixtures.js`: mobiles `9000000001-4`, device tokens `devtok-mgr-0001`, `devtok-stf-0001`, `devtok-stf-0002`, OTP `123456`.

## 5. Regression suite per domain (L1; each bullet is at least one test)

| Domain | Must-have cases |
|---|---|
| Auth | login per role and per portal mismatch (403 messages), OTP generate/validate incl. 5-attempt lock (429) and expiry, device-token verify matrix (success, mismatch + `request_pending`, approved-clears-token, expired, inactive, deleted), register (duplicate 400 not 500), push-token update, `check-session` reasons, logout (WP12), bypass disabled in production config |
| Home, dashboard | counts equal list lengths, IST boundaries (clock 18:29 and 18:31 UTC), lateness at 08:59/09:00/09:01 IST, department stats with manager scope, absent/late/working lists carry `role_id`/`is_active`, inactive detail, 404 not 500 |
| Staff, org | add (limit with trial/paid, duplicate emp id/mobile/e-mail, unknown branch), edit manager keeps role, delete = status 2 and reuse of mobile, activate over limit, branch add/update/delete with two organisations, reassign, department delete, company add/update/delete cascade or soft delete, managers assign/remove/permissions |
| Shifts | CRUD, 18-hour rule, assign/unassign four routes, delete shift nulls assignments |
| Holidays | CRUD with `block_attendance`, duplicate 409, bulk, weekly offs, working days, route uniqueness |
| Attendance | punch in/out per role and both actions, second session, third refused, one open session only, geofence inside/outside/no GPS/field staff, `force_accept` flags `face_mismatch`, holiday block, today/sessions IST, admin edit (session 1 and 2, overnight), PDF is a valid `%PDF` with the right page count |
| Location | log accepted only while a session is open (session 2 too), stats/locations on IST date, history range, plan gate 403 |
| Requests | submit four types, employee list = admin row, update-status per date and all dates, aggregate status, authorisation matrix, date-less 404, shift override lookup, device change dedupe and approval clears tokens, calendar |
| Expenses | odometer start/stop/photo/readings, travel/bill submit + photo, admin review, rate save pending-only, stamp on approve, decimal rate, default rate |
| Chat, groups, announcements | send/list/messages/mark-read/delete (both sides), group create/send/members/add/mark-read counts, announcement targets and recipient counts, push payload `message_id`, plan 403 as 403 |
| CRM | create/update/delete, assign idempotent, reassign one row, work/pipeline status, categories duplicate, carry-forward lifecycle, `lead_status` present, employee "due today or undated" |
| Payroll | payslip CRUD + totals + delete refuses with assignment, assign/employee/preview month, wages (12 routes), incentives (CRUD, assign, unassign, delete cascade), advances (admin only, enriched row), engine snapshot, overtime report equals breakdown, paid state idempotent, attendance rule and `fine`, batch statement count |
| Billing | plans, subscription `state` for four situations, coupon first/validate, order create with fake gateway, verify (bad signature 400, idempotent, renew intent extends), webhook-first and verify-first orders, invoice history/download is a PDF, staff-limit after top-up |
| Support, suggestions | subjects, submit with and without photo (photo failure reported), reply, status flow |
| Version | per-app rows, force/soft update, unparsable version |
| Realtime | `/ws/chat` receives `new_message`, group frames, `read_update`, `request_submitted`, punch `badge_update` with `notification`, `announcement`; `/ws/badge` still works for legacy; reconnect replaces nothing |
| Multi-tenant | every list and delete with `org-0002` colliding ids returns only the caller's rows |
| Migrations | empty database to head, idempotent re-run, `\d attendance` has the partial unique index and not the old one |
| Time | the matrix `{17:30, 18:29, 18:31, 23:59 UTC}` x `{punch, today, sessions, daily, monthly, holiday, plan expiry}` |
| Security (WP12) | route-table test: every route has an auth dependency or is on the public list; token tamper; cross-org access 403; manager scope |

## 6. Running the frontend Playwright suite against a real backend

### 6.1 How the harness is wired (facts from `e2e/`)

- 41 spec files, 579 tests, two projects (desktop 1280x800, phone 390x844), locale `en-IN`, time zone `Asia/Kolkata`, geolocation fixed at Head Office (12.9716, 77.5946), Chromium fake camera (`e2e/playwright.config.ts`).
- Ports: `MOCK_PORT` (default 8787) hosts **both** the mock API and the `/__test/*` control plane; web exports are served on `E2E_WEB_PORT` (8788) and the next `E2E_WEB_PORTS - 1` ports, one per worker (`e2e/ports.js`, `e2e/serve.js`); `E2E_WORKERS` default 4 (max = web ports).
- The web export bakes the API base at build time: `E2E_API_BASE_URL` (default the mock URL) -> `EXPO_PUBLIC_API_BASE_URL` (`e2e/build.js`); the WebSocket base is derived from it (`src/config/env.ts`). The app's built-in default API host is the legacy one (`src/config/env.ts:11`), so any Clockyn build must set it.
- Isolation: each worker has its own web origin, and the mock keys all state by the browser `Origin` (or `x-e2e-tenant`), so workers do not share data (`e2e/mock-server/index.js`, `support/test.ts`). `playwright.config.ts` starts `node e2e/mock-server/index.js` and `node e2e/serve.js` with `reuseExistingServer: !CI`, health URL `/__test/health`.
- Specs use the control plane heavily (counts over all specs): `waitForRequest` 131, `failNext` 108, `requests` 96, `fixtures` 51, `setPlan` 45, `setState` 27, `waitForSocket` 24, `setClock` 12, `forceLogout` 8, `setLatency` 6, `push` 4, `dropSockets` 4. Only 9 tests (smoke, layout regressions, two visual specs) make no control-plane call. `loginAs` seeds `localStorage` with a session built from `fixtures.js` ids and the plan flag, then the app talks to the API.
- OTP for every login is `123456`; users `adm-0001`, `mgr-0001`, `stf-0001`, `stf-0002`; org `org-0001`; branches 101/102; shifts 1/2/3 (`fixtures.js`).

### 6.2 Two options

**Option A, subset now (S, about 1 day after the stub adapters exist).** Add a Playwright project `live` that runs only specs/tests that need no control plane (`00-smoke`, `q2-layout-regressions`, the two visual specs, login-through-UI with the test OTP) against a real backend seeded as in section 4, `E2E_WORKERS=1`, `autoReset` off, `failOnPageIssues` on. This already exercises login, routing, guards and a dozen real screens and catches P0-type defects (an unmounted route shows an error state that the issue collector turns into a failing test).

**Option B, full parity (L, about 5-6 days, WP1).** Implement the mock's control plane inside the backend, active only when `APP_ENV=test`, so that `e2e/support/mock.ts` works unchanged by pointing `MOCK_PORT` at the backend (`MOCK_PORT=8080`, `E2E_API_BASE_URL=http://127.0.0.1:8080`). Required surface (names and bodies as in `e2e/README.md`):

| Endpoint / behaviour | Implementation in the backend |
|---|---|
| `GET /__test/health` | `{ok, port, today, tenant}` |
| `POST /__test/reset {patch}` | truncate and reload `seed.sql` (about 1-2 s), clear injected failures, clock, request log; apply the patch |
| `GET/POST /__test/state` | `plan` (`elite`, `prime`, `core`, `trial`, `expired` or `none`, via SQL helpers), `latencyMs`, `failNext` (middleware: exact, `prefix*`, `re:`, method, status, body, times, `networkError` by closing the socket), `clearFailures`, `clock` (offset for `get_utc_now`), `forceLogout`, `faceMismatch`, `otp`, `noCoupon`, `birthdays`, `version`, `locationLogStatus`, `userPatch`, `dupLeadRows` |
| `GET/DELETE /__test/requests` | ring buffer written by a middleware: id, ts, method, path, query, JSON/form body, multipart file parts, status, `injected` (this is what `waitForRequest` and `requests` read) |
| `POST /__test/push`, `GET /__test/sockets`, `/__test/pushes`, `POST /__test/drop-sockets` | send a frame through `core_functions/realtime.publish_to_user`, list `socket_manager.active_connections`, captured pushes, terminate sockets |
| `GET /__test/fixtures` | rows of the seed (ids and names) for specs that look up ids |
| Tenancy (`x-e2e-tenant`, one state per worker origin) | not available with one database: run `E2E_WORKERS=1`, or run N backend instances each with its own schema (`DB_SCHEMA=e2e_w<n>`, ports 8081..) behind a 30-line node proxy that routes by `Origin`; start with one worker |

Run recipe (Option B):
```
# 1. database and backend in test mode, seeded
docker compose -f docker-compose.test.yml up -d db api        # api listens on 8080
# 2. build the web export against it (frontend repo)
MOCK_PORT=8080 E2E_API_BASE_URL=http://127.0.0.1:8080 npm run e2e:build
# 3. run (the harness reuses the already running server on MOCK_PORT)
MOCK_PORT=8080 E2E_WORKERS=1 npx playwright test -c e2e/playwright.config.ts --project desktop
# 4. mock for comparison (default ports)
npm run e2e -- --project desktop
```
Provide before running: the backend answers `/__test/health`; the seed uses the fixture ids (UUID check relaxed, section 3.1); OTP bypass for the four seeded mobiles; geofence data of branches 101/102 as in `fixtures.js` (the browser location is Head Office); S3, face, payment, push, mail adapters in `capture`/`fake` mode; time zone of the database pinned to UTC with explicit IST logic (roadmap D-2) so `setClock` works.

### 6.3 Triage and gate

1. Run the full suite against the mock (green, the baseline) and against the backend (live).
2. Every spec that passes on the mock and fails live is classified: (a) **backend gap**, link the gap-table row or `NEW-n` and fix the backend; (b) **mock-only assumption**, a row of Appendix C, fix the mock and the expectation; (c) **test environment gap**, a missing adapter or flag.
3. Exclusions are listed in `e2e/live-exclusions.json` with the classification; the file must shrink every sprint.
4. Launch gate: L2 contract 100 %, Option A subset 100 %, Option B suite with every exclusion classified and no unexplained failure.

### 6.4 Expected first findings (from this review, to confirm by running)

Admin request/punch badges do not move without a reload (NEW-6); staff punch-in shows "Sent for admin review" or a failure toast (P1-8); Daily Wages, Hourly Charges, Attendance Rule, Overtime report, Monthly Salary paid state and Shift assignment sheets show their error states until WP2/WP4; holiday "Punch Allowed" switch does not persist (P1-5); calendars show absent on holidays and weekly offs (P1-3); lead pipeline status missing on the admin CRM list (NEW-5); deleted staff reappear as Inactive (NEW-15); approved leave/shift changes change nothing (NEW-8).

## 7. Layer 3: the app's own client against the backend

A jest project `live` in the frontend repo (`src/api/__live__/*.live.test.ts`, excluded from the default `npm test`) imports the real `src/api/*.ts` modules with `configureApi({ baseUrl: process.env.LIVE_API_BASE_URL })` and walks the same call plan as section 2.2 with the seeded ids. It proves the mappers, envelopes and `assertSuccess` handling against real responses without a browser, runs in about a minute, and is the quickest way to detect a shape drift introduced by a backend change. Run: `LIVE_API_BASE_URL=http://127.0.0.1:8080 npx jest -c jest.live.config.js`.

## 8. Non-functional checks

| Check | Method |
|---|---|
| Time zone boundaries | parametrised clock fixture across 17:30, 18:29, 18:31 and 23:59 UTC for punch, today, sessions, daily, monthly, holiday block, plan expiry, month ranges |
| Concurrency | two simultaneous punch-ins for one user (advisory lock `face_attendance_functions.py:187-190`): one wins; two mark-paid calls: one inserts; two renewals |
| N+1 / performance | statement counter fixture: salary batch, staff lists, requests counts constant in the number of staff (seed 300 staff in a dedicated marker `slow`) |
| WebSockets | `websockets` client in pytest: connect, receive, reconnect, two sockets for one user, `/ws/badge` legacy |
| Binary outputs | PDFs begin with `%PDF`, page count equals staff count for the all-staff report; invoice PDF contains the seller name from configuration and no legacy token |
| Uploads | multipart field names and size limits for face (`faces` x4), photos (`photo`, `logo`, `file`) |
| Static checks | route table snapshot (OpenAPI JSON diff) so accidental route changes fail review; grep test that no legacy brand token appears in rendered e-mails, PDFs, notification titles and API strings |
| Security (WP12) | every route has an auth dependency or is on the explicit public list; tampered/missing token 401; cross-org id 403 |

## 9. CI outline

`lint` (ruff + type check) -> `unit` (L0) -> `api` (compose db, migrations, seed, L1 + L2 + L5) -> `contract-endpoints` (2.1, runs first, fails fast) -> nightly: `live-smoke` (Option A), `live-full` (Option B, once available), `slow` markers. Artifacts: pytest report, contract diff, Playwright HTML report and traces, migration log. The frontend repo runs the L3 jest project and the mock suite on its own changes and publishes the regenerated `contract/` manifests as a build artifact consumed by the backend pipeline.

## 10. Keeping the mock honest (backlog from gap-analysis Appendix C)

Fix in the mock repository in the same pull request as the matching backend change: salary type keys; geofence applies with `force_accept`; staff add over the limit answers 403; socket routing separated (`/ws/badge` vs `/ws/chat`) until NEW-6 ships; incentive assignment rule per DD-76; staff delete semantics per NEW-15; carry-forward `created_at`; `current-date` string; location stats window; admin requests fields once the backend adds them. A contract-diff job (mock manifest vs real manifest) reports drift in either direction.

## 11. Static SQL check

`scripts/sql_static_check.py` is the safety net for SQL written without a database. It walks `core_functions`, `router`, `services`, `utils`, `ml`, `migrate.py` (plus `app.py`, `socket_manager.py`) with `ast`, resolves every string passed to `.execute(` / `.executemany(` (constants, implicit concatenation, triple-quoted strings, f-strings, `.format`, `a + b`, and variables assigned earlier in the same function, including `query += "..."` and if/else branches), and then:

1. converts psycopg2 placeholders (`%s`, `%(name)s`, `%%`, `IN %s`) to `$N` and compares the count with a literal params tuple/list/dict (`PARAM_COUNT_MISMATCH`; a lone `%` in SQL run with params is `BAD_PERCENT`);
2. parses the statement with the real PostgreSQL parser, pglast (`SYNTAX_ERROR`, with file:line and a snippet);
3. resolves aliases against `clockyn_ddl_latest.sql` and flags `UNKNOWN_COLUMN` / `UNKNOWN_ALIAS` for qualified and unqualified column references and for `INSERT` / `UPDATE` / `ON CONFLICT` target columns of known tables, and `UNKNOWN_TABLE` (warn, once per file) for tables that are not in the DDL. CTEs, subquery aliases, set-returning functions and `EXCLUDED` are understood.

f-strings whose `{...}` parts cannot be resolved are checked as "approximate": each part is replaced with a harmless identifier or predicate (`x_placeholder`, `1 = 1`, several candidates are tried until the statement parses), the parameter count is only compared when every placeholder is an identifier, and unqualified columns are not checked.

```
python scripts/sql_static_check.py                       # whole backend, fails (exit 1) on errors not in the baseline
python scripts/sql_static_check.py --paths core_functions/shift_functions.py --all
python scripts/sql_static_check.py --json
python scripts/sql_static_check.py --write-baseline scripts/sql_static_baseline.json   # only after triage
```

`scripts/sql_static_baseline.json` holds the findings accepted as pre-existing (keys have no line numbers, so edits elsewhere in a file do not invalidate them). `tests/test_sql_static_check.py` unit-tests the checker and has one test that runs it over the repo against the baseline, so any new SQL mistake fails the DB-free suite. Remove a baseline entry when its bug is fixed. The tool needs `pglast` (`pip install pglast`; the script also looks in `PGLAST_LIBS`). It cannot see: runtime-built SQL held in unresolvable variables or returned by helper functions, column types and `GROUP BY` completeness, row-shape access in Python (`row['alias']`), and constraint violations; those still need the real-database run.


## 12. Live SQL check

`scripts/sql_live_check.py` validates every SQL statement of the backend with a real PostgreSQL (tested on 17.9) using PostgreSQL's own parser, analyser and planner, without executing anything.

```
PGSSLMODE=require python scripts/sql_live_check.py            # exit 1 on real errors or a failing migration
PGSSLMODE=require python scripts/sql_live_check.py --json --all
PGSSLMODE=require python scripts/sql_live_check.py --paths core_functions/shift_functions.py --no-migrations
PGSSLMODE=require python -m pytest tests/test_sql_live.py -q -p no:cacheprovider   # skipped when no database is configured
```

How it works. The statements come from the extractor of `sql_static_check.py` plus three additions: `.format(...)` templates with literal arguments are formatted for real (for example `_STAFF_ROW_SQL.format(join="INNER", ...)`); SQL constants that are not passed to `execute()` directly (dict values such as `_DUP_SQL`, lists such as the `DELETE` list in `data_lifecycle.py`, helper arguments) are validated too; f-string parts the extractor cannot see are resolved from `placeholder_hints` in `scripts/sql_live_baseline.json`, or from the real `core_functions/scope.py` predicates. `%s` / `%(name)s` become `$n` (every placeholder gets its own number, because psycopg2 sends independent literals). Each statement is sent as `PREPARE s(..) AS ...` and then `EXPLAIN (COSTS OFF) EXECUTE s(NULL, ..)` (plans, never runs: this also catches planner-stage errors such as an `ON CONFLICT` target without a unique index), on a read-only session, inside a transaction that is rolled back. A retry loop declares parameter types (up to 6 attempts) when PostgreSQL cannot infer one or reports `operator does not exist: integer = text` and similar. The migration files `migrations/*.sql` are applied one by one in a transaction that is always rolled back.

Classes: `OK`; `UNTYPED` (a parameter type cannot be inferred: noise, because psycopg2 sends literals; the keys are listed in the baseline file and only a new one is reported); `REAL` (syntax, missing relation / column / function, ambiguity, `GROUP BY`, persistent type mismatch, planner error); `APPROX` (an f-string part could not be resolved and no rendering prepared: reported, never fails the run); `UTILITY` (`SET`, `SAVEPOINT`, ... cannot be prepared: the parser only). One artefact is handled: `GROUP BY CASE WHEN a = %s ...` equal to the select-list expression is valid with psycopg2 (identical literals) but not for a server-side prepared statement (`$1` vs `$4`), so a 42803 on a statement with several parameters is re-checked with the parameters replaced by `NULL`.

Safety rules (the server also holds production data in other schemas): the connection only comes from `tests/pg_scratch.py` (settings from the backend `.env`, never printed); all work happens in one scratch schema `clockyn_dev_test_<hex>` created and dropped (in `finally`) by that helper; `search_path` is that schema only; a statement is sent only after the PostgreSQL parser confirmed it is exactly one select / insert / update / delete; migrations are text-guarded (no schema, database, role or extension statements, no foreign schema names). No database is created or dropped and no other schema is touched.

First run (2026-11-02): 812 distinct statements (841 `execute()` call variants, 45 constants outside `execute()`), 10 of 10 migrations apply. Real defects found and fixed: `core_functions/suggest_feature_functions.py` (two queries) and `core_functions/support_functions.py` (two queries) selected columns that were not in `GROUP BY` while `email_log` and `admin_subject_request` have no primary key in the schema (PostgreSQL error 42803; the support code hid it behind a fallback that dropped the replies); `router/attribution.py` compared the integer column `users.status` with `'active'` (22P02 on every call of the meta attribution report). DB-free regression tests: `tests/test_sql_live_regressions.py` (GROUP BY completeness of the grouped statements, the status comparison, and unit tests of the extractor helpers). Remaining blind spots: statements built from variables the extractor cannot resolve and not hinted (about 20 approximate statements pass only with substituted fragments), column types of values at run time, row-shape access in Python (`row['alias']`), and constraint or data errors (those need the API tests against a seeded database).
