# Clockyn backend planner (internal reference)

> **Status (2026-11-02): ALL PLANNED WORK WRITTEN; DATABASE LAYER VERIFIED ON REAL POSTGRESQL.** The schema is built on RDS PostgreSQL 17 (schema `clockyn_dev`: 72 tables, 3 enums, 199 indexes, seeds; `migrate.py` second run is a no-op), the two DB test files pass (95 tests, run in a temporary `clockyn_dev_test_<hex>` schema), and a live check prepared and planned all 812 SQL statements on the server (5 real bugs found and fixed). **Not yet run: SES email, Firebase push, real phones, the real-backend browser run.** Item-level list: `clockyn_progress_tracker.xlsx` (tab 'Start Here').
> **Placeholders used here:** `<BE>` = this folder (the Clockyn backend, a copy of the legacy backend); `<FE>` = the Clockyn frontend repo's `Clockyn` folder (real paths are in the project memory file `reference_clockyn_repos.md`). Legacy product names are intentionally not typed anywhere in this document.
> Detailed sources this plan summarises: `<BE>/project_complete_context/implementation_roadmap.md` (WP definitions), `clockyn_backend_gap_analysis.md` (1,075 lines, per-endpoint rows + designs), `testing_strategy.md`, `overall_context.md`, `database/schema.md`, `database/migrations_and_changes.md`, `api/endpoint_catalog.md`, `domains/*.md`, `rename_inventory.md`; and `<FE>/backend_changes.md`, `<FE>/docs/DEFERRED_DECISIONS.md`, `<FE>/e2e/mock-server/` (executable contract).

---

## 0. TL;DR

- **Goal:** make the legacy FastAPI backend (copy in `<BE>`) fully serve the new Clockyn app, fix its critical defects, and rebrand it — without breaking the legacy client until it is retired.
- **Size:** ~20k lines of Python, 302 mounted routes, 62 tables. Gap analysis vs the frontend: of 239 called method+path rows → 186 match, 26 differ in shape, 5 mounted wrong, **22 missing** (21 real). Priorities: P0 22 rows, P1 64, P2 30, P3 21.
- **Biggest facts:** (1) only 6 of 293 live routes check any secret — effectively no authentication; (2) wage/hourly routes were overwritten by a holiday-router copy (restorable from git history); (3) staff punch-in crashes after commit (`face_attendance_functions.py:264`, undefined `img_url`); (4) live badge/request events go to a socket the app never opens; (5) `branch_id` repeats across orgs and deletes/lookups aren't org-scoped; (6) order endpoint trusts the client's amount; (7) schema dump ≠ code ≠ (unknown) deployed DB.
- **Recommended execution order (value per token):** Phase 0 (prereqs + harness) → Phase 1 "app works" (P0 routes, punch fix, push/realtime, config) → Phase 2 "data correct" → Phase 3 "secure" → Phase 4 polish → Phase 5 cutover/retire legacy.
- **Budget stance:** the owner has limited credits. This plan is designed so each phase is independently shippable and each work package can be run alone; see §9 for the cheapest-first ordering, minimum-viable sets and stop/resume protocol.

---

## 1. Context snapshot (as of 2026-10-19)

| Item | State |
|---|---|
| Frontend | Clockyn app done: Expo SDK 58 / RN 0.88, 88 legacy screens ported/merged/dropped, 2,257 unit tests, ~1,400 Playwright test instances green on web against the mock backend, Android+iOS bundles build. Not run on a real device. Needs Expo Go 58.0.4 (stores only have 57) or a dev-client build. |
| Frontend ↔ backend contract | `<FE>/src/api/*.ts` (typed client) + `<FE>/e2e/mock-server` (stateful mock; 18 known differences from the real backend — gap analysis Appendix C). |
| Backend docs | 32 documents, ~9,600 lines, written by 9 agents; claims tagged UNVERIFIED where static reading couldn't settle them. |
| Backend code | Changed since this snapshot (see §9 status): Step 1 routes and DDL, face Tier A, `USER_AUTH`, quick wins, hygiene, OTP throttle. Git: the backend folder is still untracked inside the legacy repo. Plan below proposes `git init` + baseline commit **only with owner approval**. |
| Local tooling | Python 3.13, Node 26, npm/pip registries reachable; **no Docker, no PostgreSQL, no FastAPI installed**; Java 8 (no Android SDK). curl to GitHub fails TLS from this machine (use npm/pip). |
| Unknowns that need the owner | Live DB schema dump; whether the deployed server already has fixes (e.g. punch `img_url`); Firebase project for the new bundle id; Razorpay account & webhook; SES sender; SMS/WhatsApp template approvals; seller legal entity/GSTIN for invoices. |

---

## 2. Engineering principles ("best-in-class bar", staged)

1. **Contract-first.** The frontend mock fixtures are the executable contract. A route is "done" when the contract test passes on the real backend or the delta is recorded and decided.
2. **Additive and backward compatible** until the legacy client is retired: new routes, optional fields, optional params. Behaviour changes ship behind env flags (`ENFORCE_SINGLE_DEVICE`, `REQUESTS_ENFORCE_ACTOR`, `REQUIRE_AUTH`, …).
3. **Expand → migrate → contract** for schema: numbered, idempotent (`IF NOT EXISTS`) migration files applied by one migrator; no DDL in request handlers; forward-only; destructive steps only after a snapshot.
4. **Deny by default security:** every route has an auth dependency or is on an explicit public allow-list (enforced by a test that iterates the route table); identity and org derived from the token, never from client-typed ids; org scoping on every query that uses a per-org-numbered id (branch ids repeat across orgs).
5. **One time model:** store UTC; business logic in IST via a single helper module; pin DB session time zone to UTC; never compare a tz-cast time to a local shift time.
6. **Honest errors:** re-raise `HTTPException` (stop wrapping into 500); one JSON error envelope `{detail}`; no `str(e)` leakage; real 403/404/409/422 codes; request-id on every response.
7. **Idempotency & concurrency** for money and attendance: payment verify/webhook idempotent on order; punch endpoints safe on retry; unique constraints that match the business rules (two sessions/day vs the stale unique index).
8. **Observability:** structured JSON logs (fix the root logger set to ERROR by the ML module), request ids, audit log for admin actions, `/health` + `/ready`, error tracking hook, basic metrics (latency, 5xx, WS connections, push success).
9. **Test pyramid:** pure-logic unit tests → DB integration tests (real Postgres) → contract tests from mock fixtures → e2e (frontend Playwright against the real backend) → security tests (route-auth matrix, injection, IDOR) → migration tests (empty DB, re-run no-op, upgrade path).
10. **Small, reviewable changes**, docs updated with code (`domains/*.md` §3/§8 are kept current by each WP), no secrets in repo, `known_faces` data removed.

---

## 3. Decisions register (defaults = roadmap recommendations; **all pending owner confirmation**)

| ID | Decision | Default | Notes |
|---|---|---|---|
| D-1 | Own backend + DB for Clockyn vs shared with legacy | **Own** | Simplifies version check (seed row), FCM (one project), rebrand, behaviour changes. If shared: add `app_id`, dual FCM, flag every behaviour change. |
| D-2 | DB session TZ & attendance business date | Pin session UTC; `attendance_date` = IST date for new rows | Boundary-day effect on DBs with history; optional backfill. |
| D-3 | Face check advisory vs enforced | Keep advisory (`force_accept`), return `face_mismatch`, flag in report | Enforcement later (override token / 2 failures). |
| D-4 | Geofence | Keep as coded | Non-field staff enforced when branch has GPS. |
| D-5 | Day types for calendars | Server day types via `detail=v2` | Frontend FE-2 later. |
| D-6 | Shift-change approval | One-day override | `shift_for(user,date)` helper. |
| D-7 | Leave approval effect | Attendance day type only | No salary/balance at launch. |
| D-8 | Salary proration / attendance rule | Rule drives `fine`; no proration of basic | Parity with v1. |
| D-9 | Overtime formula | Keep monthly aggregate; fix two-session & night shift only | |
| D-10 | Manager scope | Server scope, deny by default | Today `None` = unrestricted. |
| D-11 | Single-device | Block + request access for roles 2/3 behind flag; admins multi-device; add `POST /auth/logout` | |
| D-12 | Plan gating | Keep matrix; also gate chat child routes & payroll routes server-side; IST expiry inclusive | |
| D-13 | Billing | Server quote (`POST /orders/quote`), renew extends validity, trial staff limit 10 | |
| D-14 | Data lifecycle | Soft delete + 30-day purge; PII anonymised on account deletion | |
| D-15 | Payroll validation bounds | `0 < x ≤ 9,999,999.99`, 2 decimals, % in 0..100, uniqueness per org+type | |
| D-16 | Chat/announcements | Keep; server-resolved announcement targets; chat delete = both sides (document) | |
| D-17 | Request/CRM rules | Server-enforce (no overlap leave, 6 h permission, persist carry-forward, my-leads = due today/undated, calendar returns holidays/weekly offs) | |
| D-18 | Auth token | Bearer at OTP/device login, verified everywhere, behind `REQUIRE_AUTH` rollout | Needs FE-4 (header in one client file). |
| D-19 | OTP test bypass | Env-gated, off in prod | Reviewers get a seeded org. |
| D-20 | Webhook-first activation | Both webhook and client verify, idempotent | |
| D-21 | Incentive formulas | Keep flat payout; document thresholds unused | |
| D-22 | Forgotten punch-out | Auto-close at next punch-in when open session > 18 h, `auto_closed` flag | |
| D-23 (new) | Rename scope | Backend rebrand in code/config/templates/assets as part of WP11; DB object names (legacy prefixes) only with a migration plan | See `rename_inventory.md`. |
| D-24 (new) | Git | `git init` in `<BE>` + baseline commit to enable worktrees/diffs | **Needs explicit approval.** |

---

## 4. Roadmap

Effort units (from the roadmap): S < 1 day, M 1–3 days, L > 3 days for one engineer including tests. "Agent-time" is the indicative wall-clock when run by an agent (observed agents took 30–90 min each; a WP of size M ≈ 1–2 agents-hours). Budget class: see §9.

### Phase 0 — Prerequisites (can start immediately, in parallel)
| WP | Scope | Size | Depends |
|---|---|---|---|
| **WP0** Schema baseline & migrator | `000_baseline.sql` (from live DB if provided; else repo DDL + 8 drift objects), `schema_migrations`, `migrate.py --all/--dry-run`, `001_drift_objects`, `RUN_MIGRATIONS=1` on container start | M | live schema dump (owner) |
| **WP1** Platform hygiene & harness | route-uniqueness test; `utils/timezone.py` helpers + UTC session; re-raise-HTTPException pattern + global handler; fix connection-pool leak (`db_function.py:57-66`); pytest scaffold with real Postgres fixture; test adapters (`STORAGE/FACE/PAYMENT/PUSH/SMS/MAIL_BACKEND`), `APP_ENV=test` guard, `/__test/*` control plane; remove token logging & debug route; env-gate OTP bypass; relax UUID checks in `add_staff` routes | M | — |
| **Env** local test stack | pip: fastapi, uvicorn, pydantic, psycopg2-binary, httpx, python-multipart, razorpay, boto3, reportlab, pypdf, pytz, pytest(+asyncio); skip heavy ML/weasyprint via adapters/lazy imports; real Postgres via the npm `embedded-postgres` package (Docker unavailable) | S | — |
| **Owner requests** | Start provider lead-time items now: Firebase project + APNs key, SES verified sender, SMS (DLT) + WhatsApp template approvals for the new name, Razorpay account/webhook, seller entity & GSTIN | — | owner |

**Exit gate P0:** clean DB created by one command; API boots; `pytest` green; route-table snapshot test green; no DDL in handlers beyond those scheduled for removal.

### Phase 1 — "The app works" (additive, safe for the legacy client)
| WP | Scope | Size | Depends |
|---|---|---|---|
| **WP2** Wages + shift assignment routes | 16 routes: `/api/daily-wages`, `/api/hourly-charges`, `/api/wages` (restore from history: `git show 36a555b^:router/daily_hourly.py` in the legacy repo), 4 shift assign/unassign/assigned/unassigned routes; fix cascade/`moved_from`; swap the router import | M | WP1 (tests) |
| **WP4a** New payroll routes only | `/api/attendance-rule` GET/POST, `/api/incentive/overtime-report/{org}`, `/salary/monthly-list`, `/salary/mark-paid`, allowance-rate table (stop overwriting every reading) — **without** the engine refactor | M–L | WP0 (tables) |
| **WP8** Push & realtime | `push_service.py` (single FCM sender, new Firebase creds via env, multi-device tokens, stale-token pruning), `realtime.py` routing **all** events through the socket the app opens (`/ws/chat/{uid}`): `badge_update` with `notification`, `request_submitted`, `announcement`, group `read_update` with `message_id/read_count/total_members`, add-members event; chat pushes carry `message_id`; `force_logout` via FCM | M | owner Firebase (C-2) |
| **WP11** Identity, config, branding | version check seed/`app_id` (D-1); OTP send throttle (5/h, 30 s gap, `otp_send_log`); externalise all brand strings/links (OTP mail, invoice header/legal entity, WhatsApp template/store link, notification titles, static logos replaced by Clockyn assets); new env vars (`APP_NAME`, `SUPPORT_EMAIL`, `SELLER_*`, `S3_KEY_PREFIX`, `CORS_ALLOWED_ORIGINS`, …); remove `known_faces/` from repo & image; delete unmounted root `chat.py` | M | owner inputs |
| **QW** Quick-win pack | the 20 sub-day fixes (below) | S–M | WP1 |

**Quick wins (each < 1 day, no migration):** (1) `img_url` fix + guard post-commit notify; (2) `message_id` in chat pushes; (3) `role_id/is_active` on live-status lists; (4) 404 handling + drop `status=1` in staff detail/profile; (5) `branch_id/dept_id/org_id` on detail/profile and `/addstaff/{id}`; (6) `PUT /addstaff` stops writing `role_id`, keeps branch/dept/shift when omitted; (7) employee requests: `date_statuses` + shift ids; (8) aggregate leave/permission status after decision; (9) `/attendance/sessions` IST + `work_hours` + real status; (10) group `read_update` counts, skip reader; (11) plan check outside the `try` in chat list routes (real 403); (12) registration re-raises `HTTPException`; (13) `check-mobile/email` unauthenticated `{exists}`; (14) remove token logging + debug route; (15) branch delete/reassign scoped by org; (16) scope the six `branch_info` joins by org; (17) staff-location log uses open session, accepts 0 coordinates; (18) `delete_staff` → status 2; (19) *(frontend)* drop dead `getReceiverPushToken`; (20) `employee-login` returns `email`.

**Exit gate P1:** every endpoint the frontend calls resolves to a real route (missing = 0); contract tests for those routes pass; frontend Playwright smoke passes against the real backend for the unblocked screens; no legacy tokens in code/config/templates except `rename_inventory.md`.

### Phase 2 — "Data correct" (behaviour-affecting; flags/announcements)
| WP | Scope | Size | Depends |
|---|---|---|---|
| **WP3** Holidays & weekly offs | one registration per holiday route; `block_attendance` round-trip; `org_off_days`; unique index; bulk add; delete `router/daily_hourly.py` copy & `report.py` copy (after WP2 swap) | M | WP0, WP1, WP2 |
| **WP5** Attendance integrity | IST business date, two-session day vs unique index, `/attendance/today` after midnight, lateness in IST, savepoints around location inserts, stale-open-session policy (D-22), `detail=v2` monthly attendance with shift times & day types, location queries aggregate sessions | L | WP0, WP1, D-2 |
| **WP6** Staff & org data | org-scoped branch/dept/company queries & deletes, `branch_id` sequence (`manager_id` column for manager-owned staff, backfill), manager edit no longer demotes, soft delete + purge, staff-limit on reactivation, scope helper (`scope.py`), manager-owned staff can log in | L | WP0, D-10, D-14 |
| **WP7** Requests & sessions | server validation of leave/permission rules, per-date status round-trip, device-change dedupe + rate limit, shift swap affects lateness for that date, update-status actor check (log-only first), single-device flag, `POST /auth/logout` + token revocation | M | D-6/7/11, WP8 |
| **WP9** CRM | no DDL in handlers; `leads_info.status` width; one row per lead with `lead_status`; duplicate-assign guard; carry-forward persisted; dedupe migration (on a copy first) | M | WP0 |
| **WP10** Billing | `POST /orders/quote`; server price recompute (reproduces 8,496 / 7,646 / 8,602 / 3,540 / 1,510); renew extends validity; webhook+verify idempotent; `EXPIRED` written; invoice seller entity from config; webhook signature on raw body | L | D-12/13/20, owner Razorpay |
| **WP4b** Payroll engine | engine refactor with snapshot test, two-session & night-shift fixes, `fine` from attendance rule, overtime helper shared by report/breakdown, paid state integration | L | WP5, WP3, D-8/9 |

**Exit gate P2:** pytest + contract + snapshot tests green; two-organisation collision fixture passes; attendance boundary tests (00:30 IST punch, overnight session, two sessions) pass; payroll snapshot differences limited to the three intentional changes and documented in release notes.

### Phase 3 — Security (needs frontend header change FE-4)
**WP12** bearer token at OTP/device login; `Depends(current_user)` on every route; ids validated against token; roles + manager permissions enforced; WS handshake token; operator routes behind operator auth (`/gen-dashboard/*`, plan/coupon writes, invoices, support/suggestions admin); close open face-register/cleanup endpoints; CORS origins from env; upload limits; signed invoice/PDF URLs; rate limits (OTP, support, suggestions, device requests); `REQUIRE_AUTH` flag staged off → on. **Size L (10–15 days).**
**Exit gate P3:** route-table test proves every route has auth or is on the public list; IDOR test matrix (cross-org read/write) passes; no hard-coded secrets; `npm audit`-equivalent (`pip-audit`) reviewed.

### Phase 4 — Scale & polish (optional)
Counts endpoint, pagination (chat history, staff lists), bulk attendance/late report endpoint, `shift_id` on staff list, consistent envelopes, indexes from `database/schema.md` risks, single worker → Redis fan-out only if scaling out.

### Phase 5 — Cutover & legacy retirement
Migrate users (if D-1 = own backend), final DNS/TLS, WebSocket/proxy settings (upgrade on `/ws/*`, idle ≥ 300 s, body ≥ 25 MB), then WP14 removal of legacy routes (`/ws/badge`, SSE, duplicate aliases).

### Critical path & parallelism
`WP0 → WP1 → WP5 → WP4b` (~22 engineer-days sequential). Parallel in Phase 1: WP2, WP4a, WP8, WP11, QW. Parallel in Phase 2: WP3, WP6, WP7, WP9, WP10 (different files) once WP0/WP1 land.

**File-conflict map (plan merges around these):** `auth_function.py` (WP1, WP7, WP11, QW), `home_functions.py` (QW, WP5, WP6), `router/chat.py` (QW, WP8), `requests_functions.py` (QW, WP7, WP8), `face_attendance_functions.py` (QW, WP5), `app.py` (WP1, WP2, WP3, WP11), `shift_functions.py` (WP2, WP7), `utils/email.py` (WP10, WP11), `report_functions.py` (WP4b, WP5), `gen_*_functions.py` (WP10, WP11).

---

## 5. Agent orchestration playbook

**Roles:** Builder (one WP), Reviewer (security/SQL review of a builder's diff), QA-Contract (contract + integration tests), QA-E2E (frontend Playwright against real backend), Integrator (me: merge, gates, route-diff), Docs (update `domains/*.md`).

**Setup once:** (1) owner approves D-24 → `git init` in `<BE>`, baseline commit; (2) create `tests/`, `requirements-dev.txt`; (3) local stack (§6); (4) `scripts/gate.py` = import app, route-table snapshot, route-diff vs frontend route list, `pytest -q`, legacy-token grep, secret-pattern grep.

**Brief template (keep short — cost driver):** goal + WP id; exact files and doc sections to read (not "the whole repo"); contract examples (path in mock fixtures); acceptance tests; flags/env; rules (additive, no secrets, no legacy names, tests colocated); "do not spawn sub-agents"; report ≤ 8 lines.

**Isolation & merging:** one git worktree per builder; builders commit locally; I merge in dependency order and run the gate after each merge; conflicts resolved by me, not agents.

**Harness quirks learned (apply to every brief):** write files with the Write tool, not shell heredocs (backslashes get mangled; use Edit/Write for regex/path strings); some agent writes of report-style .md files are refused by the harness → agent must return text and the lead saves it; never `taskkill /IM node.exe` (kills the user's processes — stop only own PIDs); isolate ports/artifact dirs when several E2E leads run in parallel; scratch scripts go to the scratchpad, not the repo; no commits/pushes unless the owner asks.

**Cost control:** cap concurrent agents at the number of non-conflicting WPs; no QA agent for trivial packages (use the gate script); each suite is run once per agent; avoid re-reading large docs (point to line ranges); prefer the integrator running a final gate over per-agent full-suite runs.

---

## 6. Test & verification strategy

| Layer | What | How |
|---|---|---|
| Unit | pricing/GST rounding, time helpers, overtime/fine, scope helper, validators | pytest, no DB |
| Integration | every route with a real Postgres | embedded Postgres (npm `embedded-postgres`), migration baseline + `001+`, factories, adapters for S3/FCM/Razorpay/SES/SMS/face |
| Contract | each frontend-called route returns the shape in `e2e/mock-server` fixtures | loader that replays mock fixtures against `TestClient`; the 18 mock-vs-backend differences are fixed in whichever side is wrong (same PR) |
| Route-table | no duplicate (method,path); every route has auth or is public-listed | snapshot test + iterating test |
| Migration | empty DB → baseline+migrations = code expectations; re-run = no-op; upgrade from legacy dump | scripted |
| Security | IDOR matrix (cross-org), auth missing → 401, OTP throttle, webhook signature replay, SQL injection probes on string params | pytest + small fuzz list |
| Payroll snapshot | breakdown for a fixed seed before/after refactor; intentional deltas asserted | golden files |
| Attendance boundary | 00:30 IST punch, overnight session, two sessions/day, holiday/leave days | parametrised tests with injected clock |
| E2E | frontend Playwright smoke + guards + selected journeys against the real backend | needs `/__test/*` control plane (reset/state/clock/push) enabled only when `APP_ENV=test`, and relaxed UUID checks |
| Performance smoke | 100-staff monthly PDF < 15 s without blocking the loop; list endpoints p95 | simple load script |

**Phase gates:** P0 (boot+pytest+snapshot), P1 (missing routes = 0 + contract + smoke e2e), P2 (+ boundary + payroll snapshot + collision fixture), P3 (+ auth matrix + IDOR), release (smoke after every deploy: route snapshot, contract, OTP login per role, punch in/out, leave request/approve, chat message with push, `/version/check`).

---

## 7. Release, rollout, rollback, operations

- **Ships first (additive):** WP2 routes, WP4a routes, WP8 push/WS frames, WP11 seeds/config, additive response fields, migrations 001–005, quick wins that only add fields.
- **Behaviour changes (announce/flag):** IST lateness & `attendance_date`, two-session counting in wages/overtime, `fine`, soft delete & payslip-delete 409, update-status authorisation (log-only first), single-device enforcement, staff delete = status 2, branch-id re-key (maintenance window + snapshot), `REQUIRE_AUTH`.
- **Rollback:** container image redeploy; migrations additive/forward-only; destructive migrations (lead dedupe, branch re-key, backfills) only after snapshot.
- **Ops:** single worker (realtime state is in process memory); proxy WS upgrade/idle/body limits; DB backups + PITR; structured logs + request ids; `/health` `/ready`; secrets via env/secret manager (rotate anything that was ever committed); runbooks for OTP/push/payment incidents.

---

## 8. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Live DB differs from dump/code | migrations fail or data surprises | owner supplies schema dump first; all DDL idempotent; WP0 before DDL |
| Unknown DB session TZ | wrong late/absent counts | pin UTC (D-2); tests with explicit offsets |
| Two sessions/day vs unique index | punch failures | inspect `\d attendance` on live DB first; migration `002_attendance_sessions` |
| Payroll numbers change | disputes | snapshot test; 3 intentional deltas in release notes |
| No auth until Phase 3 | data exposure | keep operator routes off public host; proxy rate limits; prioritise OTP-bypass off + face-register/admin endpoints |
| Mock more lenient than backend | false confidence | contract tests on real backend; fix mock in same PR |
| Provider lead times (Firebase, DLT, WhatsApp, Razorpay, SES) | schedule | start requests in Phase 0 |
| File conflicts between agents | merge pain | worktrees + conflict map + integrator merges |
| Agent harness quirks | rework | playbook §5 |
| Budget exhaustion mid-phase | half-done state | phase-sized shippable increments; checkpoint commits; §9 protocol |

---

## 9. Budget plan & stop/resume protocol

**Observed agent token usage in this project** (reported per agent): documentation agents ~230k–970k; screen builders ~300k–550k; QA leads ~500k–600k; E2E harness ~520k. Planning classes: **S ≈ 150–300k**, **M ≈ 300–500k**, **L ≈ 500k–1M** tokens per WP (including its own tests). Do not translate into currency here — check the live balance before each phase.

**Cheapest-first ordering (value per token):**
1. **MV-1 (≈S–M, 1 agent):** restore wage routes from history + `img_url` punch fix + OTP-bypass env flag. Unblocks Daily Wages/Hourly Charges and staff punch notifications.
2. **MV-2 (≈M, 1–2 agents):** shift assign routes + attendance rule + overtime report + monthly list/mark-paid + allowance-rate fix. Unblocks the remaining blocked screens.
3. **MV-3 (≈M):** push/realtime (WP8) + version/config/branding (WP11) — needs owner Firebase/e-mail values.
4. **QW pack (≈M):** the 20 quick wins.
5. Then Phases 2 → 3 in the order: WP6 (cross-org delete bug) → WP5 → WP10 → WP7 → WP9 → WP4b → WP12.

**Protocol:** work in checkpoints (one WP or one wave at a time); after each checkpoint report done/remaining; stop on request; commit/branch checkpoints only with owner approval; keep `implementation_roadmap.md` and this planner's status table current; never start a new wave without confirming budget.

**Status tracker (update as work proceeds):**
| Phase / WP | Status | Notes |
|---|---|---|
| Docs (backend context, gap analysis, roadmap, testing strategy) | DONE | 32 docs |
| Step 1 (done 2026-10-31): 21 missing routes (WP2 wages + shift assign, WP4a attendance rule / overtime report / salary monthly-list + mark-paid) and a runnable DDL (`punchapp_ddl_latest.sql`: schema, 3 enums, 5 new tables, drift fixes, seed) | **CODE DONE, DB TESTS NOT RUN** | 21/21 routes register; DDL passes the PG parser (195 stmts, 0 errors); ~80 DB tests written, not run (no Postgres yet — owner will set up AWS Postgres). Next: run `createdb` + `psql -f` on the new DB, then the pytest files `tests/test_wages_shifts.py`, `tests/test_attendance_rule_overtime_salary.py` (env: `TEST_PG_*` / `DB_TEST_*`, PGSSLMODE=require for RDS). Open decisions: attendance unique index vs two sessions/day (NEW-2), placeholder seed prices, `app_version_control.app_id` vs version route. |
| Face Tier A (reliability) — done 2026-10-31 | **CODE DONE, NOT VERIFIED** | Backend: photo rotation fix, tunable detector/threshold, thread-pool offload, failure `code`/`reason`, `face_attempt_log`, warm-up route, staff punch crash fix (15 stubbed tests). App: capture quality, 720 px downscale, specific guidance, hold-still, tips sheet, warm-up call, mock + 21/21 punch browser specs. Pending: real-device check of front-camera rotation/mirroring, threshold tuning from `face_attempt_log`, tips sheet on the registration screen, liveness/enforce decision (NEW-4). Existing DBs need `face_attempt_log` created. |
| Sign-in mode switch `USER_AUTH` — done 2026-10-31 | **CODE DONE, NOT VERIFIED** | `otp` (mobile + SMS, default) or `email` (email + SES code, no SMS). `GET /auth/config`; email lookups (case-insensitive, 409 when an email maps to two active accounts); phone refused in email mode; failed SES send answers 503. App: email login, registration verifies only the email, company edit skips mobile OTP; 4 new browser specs. Pending: real SES send (sender verified, out of sandbox), staff need an email on record. Details: `domains/identity_auth_onboarding.md` "Sign-in mode switch", frontend `docs/changes/user-auth-mode.md`. |
| Quick wins — batches 1 and 2 (done 2026-10-31) | **14 of 20 DONE (stub-tested)** | Done: QW-2, 3, 4, 5, 6, 7, 8 (partial rule, see NEW-5), 10, 11, 12, 17, 18, 19, 20; QW-1 was already fixed by the face work. Notes: QW-6 changed the `AddStaffRequest.shift_id` default from 1 to None; QW-7 adds `date_statuses`, `requested_shift_id`, `current_shift_id`, `current_shift_timing`; QW-8 recomputes the aggregate (any pending = pending, else any approved = approved, else rejected; the app has no partially-approved value); QW-10 sends `read_update` per newly read message to its sender only; QW-18 sets status 2 and directory lists use `status IN (0, 1)` (announcement push lookups and the platform-admin list still include status 2). Remaining: QW-9 (with WP5), QW-13 (security decision), QW-15 and QW-16 (with WP6), plus QW-4b (the skipped `status=1` filter change). |
| WP3 Holidays and weekly offs — done 2026-10-31 | **CODE DONE, NOT VERIFIED** | `router/holiday.py` now owns `/api/holidays*` (the copies in `daily_hourly.py` — deleted — and `report.py` are gone; `ALLOWED_DUPLICATES` is empty). `block_attendance` round-trips; blank names / bad dates 422; duplicate org+date+branch+dept → `success:false`; update/delete scoped by org; weekly offs validated 0..6; `check_holiday_block` uses the IST date. No bulk-add endpoint (client sends one POST per date). Unique index `uq_org_holidays_org_date_scope` in the DDL + `migrations/025_org_holidays_unique_scope.sql` (**fails on a DB that already has same-day, same-scope holidays: de-duplicate first, query is in the migration comment**). 24 stubbed tests. Open: `get_working_days` still counts `block_attendance=false` holidays (NEW-6); routes still trust the URL `org_id` (WP12). |
| Platform hygiene (partial WP1) — done 2026-10-31 | **DONE (stub-tested)** | Connection-pool leak (4 leaks, thread-safe pool); route-uniqueness test over 323 mounted routes (known duplicates: `/api/holidays` in 3 files); no OTP/token/row logging, debug route and `GET /auth/push-token-status` removed; OTP bypass numbers only with `OTP_BYPASS=1` (default off — store-review accounts need it in `otp` mode). |
| OTP send throttle — done 2026-10-31 | **CODE DONE, NOT VERIFIED** | 5 sends per contact per hour, 30 s gap (`OTP_MAX_PER_HOUR`, `OTP_MIN_GAP_SECONDS`), 429 before anything is stored or sent; `otp_send_log` table in the DDL (69 tables) + `migrations/024_otp_send_log.sql` (apply it to any existing DB or `generate-otp` returns 500). |
| Staff email rule + dead-file clean-up — done 2026-10-31 | **CODE DONE, NOT VERIFIED** | With `USER_AUTH=email`, add/edit staff needs a valid email that no other live account uses (stored lower-case; `otp` mode unchanged); the add-staff form requires it. Unmounted `chat.py` deleted; `known_faces/` (embeddings of 6 users) moved to `C:/POC/backend_removed_files` and added to `.gitignore` / `.dockerignore`. 9 stubbed backend tests; backend total now 160. |
| Org scoping (QW-15, QW-16, part of WP6) — done 2026-11-02 | **CODE DONE, NOT VERIFIED** | About 28 statements now match the company: branch delete / reassign (foreign branch id = 404), department delete, and the `branch_info` / `dept_info` joins in staff lists, attendance, punch location, holiday check, payslip preview, dashboard and manager lists; `GET /branch/{id}` accepts an optional `org_id`. 17 stubbed tests incl. a static check that every such join carries an org predicate (backend total 177). Open (needs DDL or a decision): `add_branch` / `company_functions` use a global `MAX(branch_id)+1`; no composite key on `branch_info(org_id, branch_id)`; some shift joins match by `shift_id` only. Rest of WP6 not started (manager_id column, soft delete, scope helper). |
| Phase 0 (WP0 migrator, WP1 hygiene) — done 2026-11-02 | **CODE DONE, NOT VERIFIED** | `migrate.py --all / --dry-run / --status` (schema_migrations, advisory lock, baseline from the schema file, `RUN_MIGRATIONS=1` startup option); global error handler + `X-Request-ID`; UTC DB session; IST time helpers; relaxed id checks in add-staff; `/health`, `/ready`. Not done: test adapters / `APP_ENV` guard (not needed so far). |
| Phase 1 (WP8, WP11, P0-8) — done 2026-11-02 | **CODE DONE, NOT VERIFIED** | WP8: `services/push_service.py` (single FCM sender, env credentials, stale-token pruning, multi-device `user_push_tokens`, migration 027) and `services/realtime.py` (all events on `/ws/chat`, legacy sockets kept). WP11: `utils/branding.py` (name, seller, support, store links, CORS from env; WhatsApp template env-only; invoice fixes; static logo/signature need new artwork). P0-8: app-id aware `GET /version/check`, the app now sends its app id. Needs Firebase credentials (P0-9) and the owner's seller/support details. |
| Phase 2 (WP5, WP7, WP9, WP10, WP4b) — done 2026-11-02 | **CODE DONE, NOT VERIFIED** | WP5 attendance (IST dates, one-open-session partial unique index replaces the old per-day unique index, 18 h auto-close, lateness in IST, monthly `detail=v2`; migration 028). WP7 requests/devices (validation, per-date status, device-change dedupe + limit, `shift_for` one-day override wired into lateness, `ENFORCE_SINGLE_DEVICE`, `POST /auth/logout`; migration 030). WP9 CRM (migration 029, `scripts/dedupe_leads.sql`). WP10 billing (`pricing.py`, `POST /orders/quote`, idempotent verify + webhook, plan gating, trial limit 10; migration 031). WP4b payroll (`payroll_engine.py`, golden tests, paid-month snapshot; migration 032). **WP6 remainder not started** (manager column, soft delete, scope helper; org scoping is done). |
| Group 4 hardening — done 2026-11-02 | **CODE DONE, NOT VERIFIED** | Resource-id checks in handlers (requests, chat membership, incentives, wage templates, leads, expense photos); `check-mobile`/`check-email` public (`{exists}` only, rate limited); inactive staff detail; WP6 remainder: `scope.py` (`ENFORCE_MANAGER_SCOPE`, off), `users.manager_id` (migration 034), soft delete purge `POST /admin/lifecycle/purge-deleted` (operator, dry run default), account anonymisation (`ACCOUNT_DELETE_ANONYMISE`, off), staff limit on reactivation; HEIC uploads (app converts to JPEG; backend accepts HEIC on generic endpoints); checkout shows the server quote (fallback local); Docker non-root + health check, `packaging` pinned, boto3 1.35.99 / python-dotenv / google-auth bumped. Refused: uvicorn, onnxruntime upgrades. |
| Real database verification — done 2026-11-02 | **DONE** | Schema built on RDS PostgreSQL 17.9 in `clockyn_dev` (legacy schemas untouched). DB tests: 95 pass. Live SQL check (`scripts/sql_live_check.py`): 812 statements OK, 5 real bugs fixed: `JOIN JOIN` in the shift staff lists (`shift_functions.py`), 2 GROUP BY errors in support ticket replies and suggestions, `status = 'active'` against an integer column in `router/attribution.py`. The DB tests now run in a temporary schema (never a database create/drop). |
| Face matching on AWS Rekognition — done 2026-11-03 | **CODE DONE; NOT YET RUN AGAINST AWS** | The local face library (`insightface`, not installable on Windows) is replaced by AWS Rekognition collections: `ml/face_service.py` (register / verify / identify / remove), `ml/rekognition_face.py`, `FACE_BACKEND=rekognition|mock`, `user_face_index` table (migration 035, applied to `clockyn_dev`), local pre-checks (EXIF, dark/blurry/small) and optional anti-spoof (`FACE_ANTISPOOF=1`), faces removed on every delete path, `scripts/rekognition_setup.py check|create|policy`. No existing users, so everyone registers fresh. Owner steps in AWS: IAM policy + collection + env vars (`clockyn_run_commands.html` section 2b). Unverified: threshold 90, accuracy, throttling limits. |
| Phase 3 (WP12 security) — done 2026-11-02 | **CODE DONE, NOT VERIFIED; OFF BY DEFAULT** | HS256 bearer token (`utils/auth_token.py`), central guard with role/company/id-key policy (`core_functions/auth_policy.py`, `auth_dependency.py`), revocation by `users.auth_epoch` (migration 033), websocket handshake check, operator key (`OPERATOR_API_KEY`), upload limits, signed invoice links, rate limits, owner-only routes. Everything is enforced only with `REQUIRE_AUTH=1` (default off). App: token stored with the session, sent on every API call and socket, 401 signs out once. Apply migration 033 before setting `AUTH_TOKEN_SECRET`; set `TRUST_PROXY=1` on AWS. |
| Phase 4–5 | NOT STARTED | |

**Test status (2026-11-02):** backend 2,881 DB-free tests pass; on real PostgreSQL 95 DB tests (`test_wages_shifts` 43, `test_attendance_rule_overtime_salary` 52) and 17 live-SQL tests pass (`PGSSLMODE=require python -m pytest tests/test_wages_shifts.py tests/test_attendance_rule_overtime_salary.py tests/test_sql_live.py -q`; connection from `.env` via `tests/pg_scratch.py`, scratch schemas only) (`python -m pytest tests -q --ignore=tests/test_wages_shifts.py --ignore=tests/test_attendance_rule_overtime_salary.py`; needs `tzdata` on Windows); static checks: `scripts/sql_static_check.py` (834 statements, 0 errors), `scripts/check_schema_consistency.py` (71 tables, schema file == migrations 024-033), `scripts/check_frontend_contract.py` (235 app calls, 0 without a route).  App: 2,391 unit tests, 874 desktop browser tests (5 skipped, mock backend). Two independent reviews found and fixed about 15 defects (rate-limit bypass, body-check bypass, remote logout, IGST invoices, location dates, CRM scoping, invoice retry). Run `scripts/pre_migration_checks.sql` on a copy of any existing database before applying migrations 025-033.

**Next, in order:** (1) owner sets up AWS Postgres + SES + IAM role + env vars → `python migrate.py --all`, run the DB tests, fix failures, then a real API test pass with the browser suite pointed at the real backend (V-1 to V-6); (2) Firebase → real push test; (3) leftovers (per-resource id checks in handlers, checkout via `/orders/quote`, HEIC uploads, dependency versions, rest of WP6); (4) staged `REQUIRE_AUTH=1` rollout; (5) Phase 4-5. Full list: `clockyn_progress_tracker.xlsx`.

---

## 10. Owner inputs & external dependencies checklist

- [ ] D-1 own vs shared backend/DB; D-24 approve `git init` baseline; D-23 rename scope.
- [ ] `pg_dump --schema-only` of the live database (+ seed tables listed in gap analysis Appendix A); confirmation whether the live server already fixed `img_url`.
- [ ] Firebase project for the new bundle id (service account → `FCM_*` env vars; APNs key uploaded in Firebase console).
- [ ] SES verified sender and out-of-sandbox status; `SUPPORT_EMAIL`/`SUPPORT_URL`.
- [ ] SMS provider DLT template re-registration; WhatsApp template approval (new name).
- [ ] Razorpay account: key row (`gen_razorpay_key`), webhook URL + `RAZORPAY_WEBHOOK_SECRET`.
- [ ] Seller legal entity, address, GSTIN/CIN, SAC/place of supply for invoices.
- [ ] S3: new bucket or prefix (`S3_KEY_PREFIX`), IAM scope; decision whether legacy objects migrate.
- [ ] Domain/TLS, proxy settings, `CORS_ALLOWED_ORIGINS`, store URLs, privacy URL, web portal URL.
- [ ] Immediate hardening that needs no code agent: restrict operator/face-register/invoice endpoints at the proxy, rotate any committed secrets. (The 4 OTP-bypass numbers are now off unless `OTP_BYPASS=1`.)
- [ ] AWS: Postgres (host, port, user with CREATEDB, password), IAM role for the service (S3 read/write/list/delete on the bucket + `ses:SendEmail`), SES sender verified and production access requested.
- [ ] Staff need an email on record to sign in when `USER_AUTH=email`.

---

## 11. Resume checklist (first actions when "full update" is approved)

1. Read this planner + `implementation_roadmap.md` §2–§4 + `clockyn_backend_gap_analysis.md` rows for the chosen phase.
2. Confirm balance and the phase to run; confirm D-1, D-24, schema dump availability.
3. Environment: `pip install` dev set; `npm i embedded-postgres` (outside the repo); verify `python -c "import app"` with adapters; start local Postgres.
4. `git init` + baseline commit (if approved); create `tests/`, `scripts/gate.py`; run gate on baseline (expect: route-snapshot passes after recording current duplicates).
5. Launch Phase 0 agents (WP0, WP1) → gate → Phase 1 agents in parallel (WP2, WP4a, WP8, WP11, QW) with per-agent worktrees → merge + gate → run frontend smoke e2e against the backend.
6. Update §9 tracker and the domain docs after each merge.

---

## 12. Global definition of done

Route diff vs frontend = 0 missing; all contract tests pass; pytest green; migrations idempotent on an empty and a legacy-shaped DB; no secrets in repo; no legacy tokens outside the rename inventory (or the inventory resolved); every new/changed endpoint documented in `domains/*.md`; behaviour changes behind flags with release notes; frontend Playwright smoke + key journeys pass against the real backend; rollback tested once.

---

## Appendix — where details live
- Endpoint rows & designs: `clockyn_backend_gap_analysis.md` (rows by frontend caller; P0/P1 designs; Appendix A drift objects, B realtime state, C mock-vs-backend diffs, D legacy routes).
- WP specs: `implementation_roadmap.md` §3; decisions §2; owner config §7; S3 naming; env var inventory.
- Tables/ERD/DDL sketches M1–M15: `database/schema.md`, `database/migrations_and_changes.md`.
- Domain behaviour/defects: `domains/*.md` (§7 defects, §8 Clockyn requirements, §9 test notes).
- Security posture & architecture: `overall_context.md`; audit workbook status re-check in §8.2.
- Rename worklist (only doc containing legacy tokens): `rename_inventory.md`.
