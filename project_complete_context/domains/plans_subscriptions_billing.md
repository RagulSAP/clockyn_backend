# Plans, subscriptions & plan gating — backend context

Companion docs: `orders_payments_webhooks_invoices.md` (checkout, verify, webhook, invoices) and `platform_admin_dashboard.md` (super-admin API). Naming note: the subscription table carries the legacy product prefix in its name (`<legacy-prefix>_subscription`, first referenced at `core_functions/gen_plans_functions.py:44`); this doc calls it **`sub_tbl`**. Table renames are out of scope here.

## 1. Scope & files

| File | Lines | Role |
|---|---|---|
| `router/gen_plans.py` | 87 | 13 routes under `/plans` (catalogue, coupons, subscription, plan-status, admin CRUD) |
| `core_functions/gen_plans_functions.py` | 425 | all logic: catalogue, coupons, subscription/plan-status, `check_plan_feature`, `PLAN_BLOCKED_FEATURES` |
| `core_functions/auth_function.py:450-457` | – | creates the 1-month free trial at admin registration |
| `core_functions/auth_function.py:1000-1030` | – | admin login returns `plan_id`, `is_free_trial`, `free_trial_end` |
| `core_functions/add_staff_function.py:292-312` | – | staff-limit enforcement |
| `router/{chat,face_attendance,leads,salary,staff_location}.py` | – | the only callers of `check_plan_feature` (see §3 gate map) |
| `core_functions/gen_payment_functions.py:67-117` | – | writes `sub_tbl` / `free_trial` on payment (see orders doc) |

Frontend callers: `src/api/plans.ts` (`getOrgPlanStatus`, `getSubscription`, `listPlans`), `src/api/billing.ts` (`getFirstCoupon`, `validateCoupon`), `src/config/plans.ts` (client copy of the gate matrix), `src/features/billing/**` (pricing). The new app never calls the plan/coupon **write** routes (those belong to the legacy platform-admin web panel).

## 2. Data model touched

| Table | Columns used | Notes |
|---|---|---|
| `gen_plans` | `id` (identity), `plan_id` int NOT NULL UNIQUE, `plan_name`, `plan_month` (duration, months), `plan_amount` numeric(10,2) = **INR per staff per month** (derived from the client maths, e.g. 15 x 40 x 12; the server never multiplies it), `created_at`, `updated_at` | One row = one (tier name x duration). Hard-coded gate keys are plan_id 1/2/3 (`gen_plans_functions.py:159-161`). Whether other durations have other plan_ids is **UNVERIFIED** (catalogue rows are not in the repo); the client groups rows by name and tiers by months (`planState.ts` `groupPlans`) and its tests use ids such as 22, so ids beyond 3 are likely (see D-6). |
| `gen_coupons` | `id`, `coupon_id` int UNIQUE, `coupon_name` varchar(50), `discount` numeric(5,2) (percent), `status` int2 (1 active) | No expiry, usage-count, per-org or plan scope columns. `coupon_name` not unique. |
| `sub_tbl` | `id`, `org_id`, `user_id`, `plan_id`, `subscription_id` varchar UNIQUE (uuid string), `status` varchar(20) default 'ACTIVE', `start_date`, `end_date`, `staff_limit` int NOT NULL default 10, `created_at`, `updated_at` | Statuses actually written: `ACTIVE`, `INACTIVE` (`gen_payment_functions.py:106-109`). `EXPIRED` is **read** (`gen_plans_functions.py:45,77`) but **never written anywhere** (grep: only those two reads). An expired plan is an `ACTIVE` row whose `end_date < today`. No FK to `org_info`/`gen_plans`; no index on `org_id`. |
| `free_trial` | `org_id` UNIQUE, `user_id`, `start_date`, `end_date`, `is_active` bool | One row per org, created only by `admin_register_json` (`auth_function.py:452-457`, `ON CONFLICT DO NOTHING`). `add_company` (additional orgs) creates none (grep `free_trial` shows no other INSERT). |
| `gen_orders` | `plan_id` (read by `delete_plan` count) | see orders doc |
| `users`, `org_info` | staff count / ownership | staff limit count (`add_staff_function.py:301-304`) |

## 3. Endpoints

All handlers are `async def` but use blocking psycopg2 (blocks the event loop; same pattern everywhere). **No endpoint here has authentication or authorisation** (identity is whatever id is in the URL). Errors from the DB layer are returned as `500 {"detail":"Server error: <raw exception text>"}` (leaks internals).

| METHOD path | Handler (file:line) | Identity | Plan gate | Request | Success response | Errors / quirks |
|---|---|---|---|---|---|---|
| GET `/plans/get_plans` | `get_plans_route` `router/gen_plans.py:36` -> `get_plans` `gen_plans_functions.py:11` | none | – | – | `{success:true, data:[{plan_id,plan_name,plan_amount(Decimal->number? see D-12),plan_month}]}` ordered by `plan_amount ASC` | 500 on DB error |
| GET `/plans/all` | `:73` -> `list_plans` `:327` | none | – | – | identical query/shape to `get_plans` (duplicate) | – |
| GET `/plans/subscription/{org_id}` | `:40` -> `get_org_subscription` `:27-86` | `org_id` path | – | – | With a sub row: `{success, data:{plan_id,status,start_date,end_date,staff_limit(COALESCE 10),plan_name,plan_amount,plan_month,is_expired,is_free_trial,free_trial_end,billing_date}}`. Without: placeholder, see §4.2 | dates are `date` objects serialised by FastAPI to ISO strings in the row case, `str()` in the placeholder case; `plan_amount` is Decimal (FastAPI jsonable_encoder -> float) |
| GET `/plans/plan-status/{org_id}` | `:44` (lazy import) -> `get_org_plan_status` `:121-156` | `org_id` path | – | – | **bare object** (no `success`): `{plan_id, is_free_trial, is_expired}` | – |
| GET `/plans/coupon/first` | `:49` -> `get_first_coupon` `:199` | none | – | – | `{success:true,data:{coupon_id,coupon_name,discount}}` lowest `coupon_id` with `status=1` | none active -> **HTTP 200** `{success:false,message:'No active coupons'}` |
| GET `/plans/coupon/validate` | `:53` -> `validate_coupon` `:219` | none | – | query `coupon_name` (required str) | `{success:true,data:{coupon_id,coupon_name,discount}}`, match is `UPPER(name)=UPPER(arg) AND status=1` | invalid -> **HTTP 200** `{success:false,message:'Invalid or expired coupon'}` |
| GET `/plans/coupons` | `:57` -> `list_coupons` `:240` | none | – | – | `{success,data:[{coupon_id,coupon_name,discount,status}]}` | **admin data, unauthenticated** |
| POST `/plans/coupons` | `:61` -> `create_coupon` `:256` | none | – | body `{coupon_name:str, discount:float}` | `{success,data:{coupon_id,coupon_name,discount,status:1}}` name upper-cased; `coupon_id = MAX+1` (race) | no validation of discount range or duplicate names; unauthenticated |
| PUT `/plans/coupons/{coupon_id}` | `:65` -> `update_coupon` `:302` | none | – | body `{coupon_name, discount, status:int}` | same shape | 404 `Coupon not found`; unauthenticated |
| DELETE `/plans/coupons/{coupon_id}` | `:69` -> `delete_coupon` `:281` | none | – | – | `{success,message:'Coupon deleted'}` | 404; hard delete; unauthenticated |
| POST `/plans/` | `:77` -> `create_plan` `:343` | none | – | body `{plan_name,plan_amount:float,plan_month:int}` | `{success,data:{...}}`; `plan_id = MAX+1` (race) | trailing slash required (POST `/plans` gets a 307 redirect); unauthenticated |
| PUT `/plans/{plan_id}` | `:81` -> `update_plan` `:368` | none | – | same body | `{success,data}` | 404 `Plan not found`; edits price in place, no audit; unauthenticated |
| DELETE `/plans/{plan_id}` | `:85` -> `delete_plan` `:393` | none | – | – | `{success,message:'Plan deleted'}` | 400 if any `sub_tbl` or `gen_orders` row references it; 404; unauthenticated |

Route-order note: `/plans/coupons/{id}` is declared before `/plans/{plan_id}`; no collision because `{plan_id}` is `int` and has one segment.

### Plan gate map (`check_plan_feature`, defined `gen_plans_functions.py:174-196`)

| Feature key | Enforced at | `is_admin` arg (message) | Blocked on active paid plan? |
|---|---|---|---|
| `chat` | `router/chat.py:171` (`GET /chat/list/{user_id}`), `:229` (`GET /group-chat/list/{user_id}`) | False | never (only when expired) |
| `camera_punch` | `router/face_attendance.py:34` (`POST /face/register`), `:48` (`POST /face/punch`) | False | never |
| `crm` | `router/leads.py:17` (create), `:30` (assign), `:35` (employee leads), `:84` (`GET /api/leads/{org_id}`) | True/False | Core, Prime |
| `salary_structure` | `router/salary.py:32,107,112` | True | never |
| `staff_location` | `router/staff_location.py:21,37` | True | Core |

Everything else in the API is **not** plan-gated server-side (the client hides UI). When blocked: `HTTPException(403, detail)` with admin text "Your plan has expired. Please renew your plan to continue using this feature." or staff text "Your plan has expired. Please contact your admin." (`:188-189`). Note the text says "expired" even for a tier block.

`check_org_access` (`:89-118`, raises 403 when trial and plan both lapsed) is imported by `router/gen_plans.py:7` but **called nowhere** (dead code).

## 4. Business logic

### 4.1 State derivation (no stored transitions)
Date source is `date.today()` (server-local date; container has no `TZ` set, so UTC) in `gen_plans_functions.py:57,77,100,106,134,147`, and `get_utc_now().date()` (UTC) when payment stamps dates (`gen_payment_functions.py:89,92,94`). The client uses IST dates; between 00:00 and 05:30 IST the server is a day behind (backend_changes P2-5, DD-14). Boundary: `end_date >= today` is valid, i.e. inclusive end day.

| Condition (evaluation order in `get_org_plan_status` :129-151) | Result |
|---|---|
| `free_trial.is_active AND end_date >= today` | `{plan_id:null, is_free_trial:true, is_expired:false}` (trial wins even if a paid row exists) |
| else latest `sub_tbl` row with `status='ACTIVE'` (ORDER BY created_at DESC LIMIT 1) and `end_date >= today` | `{plan_id, false, false}` |
| same row but `end_date < today` | `{plan_id, false, true}` |
| no ACTIVE row | `{plan_id:null, is_free_trial:false, is_expired:true}` (never-subscribed org after trial, or an org that never had a trial, e.g. extra orgs from `/company/add`) |

State machine (derived on read):
`REGISTERED -> TRIAL_ACTIVE (1 calendar month, relativedelta) -> TRIAL_ENDED (just a date comparison; is_active stays TRUE forever)`; `any -> PAID_ACTIVE` on verified payment (`free_trial.is_active=FALSE`, `end_date=LEAST(end_date, today)`, `gen_payment_functions.py:112-115`); `PAID_ACTIVE -> PAID_EXPIRED` purely when `end_date < today` (row stays `ACTIVE`); `PAID_EXPIRED -> PAID_ACTIVE` only via a payment that sets a future `end_date` (broken for same-plan renewals, see D-2). No cron/job exists.

### 4.2 `GET /plans/subscription/{org_id}` (`:27-86`)
1. Latest `sub_tbl` row `status IN ('ACTIVE','EXPIRED')` joined to `gen_plans` (`INNER JOIN`: a row whose plan was deleted disappears) `ORDER BY created_at DESC LIMIT 1`.
2. Trial row: `is_free_trial = trial.is_active AND trial.end_date >= today`; `free_trial_end = str(trial.end_date)` whenever a trial row exists (even if ended).
3. **No sub row -> non-null placeholder** (never `data:null`): `{is_free_trial, free_trial_end, billing_date:null, plan_name:'Free Trial'|null, plan_amount:null, start_date:null, end_date:free_trial_end, staff_limit:null, status:'FREE_TRIAL'|'INACTIVE'}`. Missing keys vs the paid shape: `plan_id`, `plan_month`, `is_expired`. `status` is `FREE_TRIAL` only while the trial is currently active; an ended trial yields `INACTIVE` with `plan_name:null`. The client maps absent `plan_id` to `null`, treats `staff_limit:null` as 10 and derives `paid-active|paid-expired|trial-active|none` itself (`src/features/billing/utils/planState.ts`).
4. Sub row present: `is_expired = status=='EXPIRED' or end_date < today`, `billing_date = str(start_date)` (start of the latest purchase, not a billing cycle).
Trial flags are inside `data` here, while the login response carries them top-level.

### 4.3 Staff limit
* Stored: `sub_tbl.staff_limit` (default 10; `COALESCE(...,10)` on read).
* Enforced only in `add_staff_function.py:292-312`: when an ACTIVE, not-expired subscription exists, `count(users where parent_id=org and role_id in (2,3) and status=1) >= staff_limit` -> `403 "Staff limit reached (n/limit). Please upgrade your plan to add more staff."`. Trial, expired and no-sub orgs are **unlimited** (backend_changes P2-11). The client counts inactive users too (DD-17).
* Raised by payment: same-plan order **adds** `gen_orders.staff_limit`; a different plan **replaces** it (`gen_payment_functions.py:77-92`).

### 4.4 Coupons
`validate_coupon` returns the percent only. No redemption is recorded, no single-use/first-payment rule, no plan or mode scoping; `check_first_payment` (orders router) is the only "welcome" logic and the server never enforces it at order time. Staff-only upgrades never get a coupon on the client (`quote.ts` `noCoupon`), the server does not know that.

### 4.5 Catalogue admin
`create_*` use `SELECT COALESCE(MAX(id),0)+1` inside the INSERT for the business key (race -> unique violation -> 500). `update_plan` changes price for future quotes only; existing subscriptions store no price.

## 5. Side effects & integrations
None in this module (no push/e-mail/Razorpay). Payment-time writes: see orders doc. `check_plan_feature` calls hit the DB on every gated request (no caching; 2 queries).

## 6. Config & env vars
No env vars read here. Trial length (1 month), plan ids 1/2/3 and the blocked-feature matrix are constants in code (`auth_function.py:450-452`, `gen_plans_functions.py:159-171`).

## 7. Known defects & risks

| ID | Sev | Where | Defect / impact |
|---|---|---|---|
| D-1 | High | `gen_plans.py:57-87`, `gen_dashboard.py` | Plan/coupon/catalogue **write** routes and coupon list are unauthenticated: anyone can set a plan price to 0, delete plans/coupons, add 100% coupons. |
| D-2 | High | `gen_payment_functions.py:70-92`; `gen_plans_functions.py` (no EXPIRED writer) | Expired org renewing the same plan: existing `ACTIVE` row (expired by date) matches `same_plan`, `end_date` is **kept** (past) -> customer pays, plan stays expired, staff_limit also grows. See orders doc D-5. |
| D-3 | Med | `gen_plans_functions.py:167-171,198` | `PLAN_BLOCKED_FEATURES.get(plan_id, set())`: any `plan_id` other than 1/2/3 blocks nothing. If the live catalogue has per-duration ids (UNVERIFIED), those orgs get Elite-level access. Client has the same fail-open (`plans.ts` `isPlanFeatureBlocked`). |
| D-4 | Med | `gen_plans_functions.py:178-181` | Docstring says Core blocks chat, salary_structure, camera_punch; code matrix does not (stale; DD-10, backend_changes P2-2). |
| D-5 | Med | `router/chat.py:170-174,228-232` | `check_plan_feature` is inside `try/except Exception -> HTTPException(500, str(e))`, so the 403 becomes **500** with `detail` like `"403: Your plan has expired..."` (Starlette `str(HTTPException)` = `"<status>: <detail>"`; UNVERIFIED at runtime). Other gated routes call it outside any `try` (leads, salary, staff_location, face) and give real 403s. |
| D-6 | Med | `gen_plans_functions.py:57,77,100,106,134,147` vs `gen_payment_functions.py:89-94` | Mixed date bases (server-local vs UTC vs client IST); up to 5.5 h skew at expiry and at the start/end of a purchase. |
| D-7 | Med | `gen_plans_functions.py:76-81` | `end_date`/`start_date` serialised as date objects in the row case but strings in the placeholder case; `plan_amount` Decimal. Placeholder lacks `plan_id`, `plan_month`, `is_expired`; `staff_limit:null`. |
| D-8 | Med | `add_staff_function.py:292-312` | Limit not enforced on trial/expired; count query ignores plan expiry for existing users; race between check and insert. |
| D-9 | Med | `auth_function.py:450-457` only | Extra orgs (`/company/add`) get no trial -> immediately "expired" (all gated features 403) until a plan is bought. Conversely the trial is keyed by `org_id` only, so re-registering with a new mobile/e-mail yields a fresh trial. |
| D-10 | Low | `gen_plans_functions.py:199-216,219-237` | Errors returned as HTTP 200 with `success:false`. |
| D-11 | Low | `gen_plans_functions.py:256-278,343-365` | `MAX+1` ids race; no uniqueness on coupon name; `discount` unbounded (can be >100 or negative). |
| D-12 | Low | `gen_plans_functions.py:19` | `dict(p)` of RealDictRow with Decimal; relies on FastAPI encoder (Decimal -> float). |
| D-13 | Low | `gen_plans_functions.py:20-21` etc. | `detail=f'Server error: {str(e)}'` leaks SQL/driver text. |
| D-14 | Low | `gen_plans_functions.py:89-118` | `check_org_access` dead code. |

## 8. Clockyn requirements

**R-1 Server-side price recompute in `POST /orders/create` (backend_changes P3-2; DD-15, DD-18, DD-19).**
* Current: the client posts `amount` (grand total incl. GST); the server only checks `amount > 0` (`gen_orders_functions.py:31-32`) and sends it to Razorpay. The client computes credit locally and never sends it; the server has no idea which mode (new / upgrade / renew) was intended.
* Required: server derives everything from `org_id`, `plan_id`, `staff_limit`, `coupon_code` plus an explicit **`mode`** (`new|upgrade|renew`; new request field, default inferred only for old clients) and the org's subscription row; **ignores** client `amount` (return it as `quote` so the app can display; optional 409 `price_changed` when the client amount differs).
* Reference algorithm (matches `src/features/billing/utils/pricing.ts` + `quote.ts` exactly; use `Decimal` with `ROUND_HALF_UP`, which equals JS `Math.round` for non-negative values; Python `round()` is half-even and **must not** be used):
  1. `today` = IST date. `active = sub_tbl ACTIVE row with end_date >= today` (latest by created_at). `cur_staff = active.staff_limit` else 10 (min 10).
  2. `mode=new`: `tier = gen_plans[plan_id]` (404 if absent); `staff >= cur_staff if active else >= 10`; `months = tier.plan_month`; `charge = staff * tier.plan_amount * months`; `credit = 10 * cur_plan.plan_amount * cur_plan.plan_month` only when `active` (trial placeholder or expired -> 0); `after = max(0, charge - credit)`; coupon allowed.
  3. `mode=renew`: tier = the org's **current** plan row (client sends the current `plan_id`); `months = 12` (`RENEW_MONTHS`); `staff >= 10`; `charge = staff * cur.plan_amount * 12`; credit 0; coupon allowed.
  4. `mode=upgrade` (staff-only): tier = current plan; `staff_limit` in the request = **extra** staff (client posts the delta, e.g. 8 for 12 -> 20); `months_left = max(1, (end.year-today.year)*12 + (end.month-today.month))` (day of month ignored); `charge = extra * cur.plan_amount * months_left`; no credit, **no coupon**; requires an active paid plan and `extra >= 1`.
  5. `discount = round_half_up(after * pct / 100)` (whole rupee); `subtotal = max(0, after - discount)`; `gst = round_half_up(subtotal * 0.18)` (whole rupee); `total = subtotal + gst`. `total == 0` -> reject (DD-18; client disables Pay).
  6. Razorpay `amount` paise = `int(total * 100)` computed from an integer/Decimal (never `int(float*100)`; see orders doc D-9).
* Worked examples the implementation must reproduce (frontend `__tests__/pricing.test.ts`, `CheckoutScreen.test.tsx`):

| Case | Inputs | Steps | Total |
|---|---|---|---|
| New plan | 15 staff, 40/staff/mo, 12 mo | 7,200; GST 1,296 | **8,496** |
| + 10% coupon | same | discount 720 -> 6,480; GST round(1,166.4)=1,166 | **7,646** |
| Plan change, credit + coupon | 20 staff x 40 x 12 = 9,600; current Core 25/staff/mo x 6 mo -> credit 10x25x6 = 1,500 | 8,100; discount 810 -> 7,290; GST round(1,312.2)=1,312 | **8,602** |
| Renew | current plan 25/staff/mo, 10 staff (stepper min), 12 mo | 3,000; GST 540 | **3,540** (org currently has 12 staff yet is charged for 10: product decision needed, see R-2) |
| Staff-only upgrade | 12 -> 20 (8 extra), 4 whole months left, 40/staff/mo | 1,280; GST round(230.4)=230; no credit/coupon | **1,510** |

* Rounding contrast: client rounds discount and GST to **whole rupees** (staff charge and credit unrounded). The server must charge exactly that whole-rupee total; the invoice later back-computes base/CGST/SGST to paise (`base=round(total/1.18,2)`, `cgst=sgst=round(base*0.09,2)`), e.g. 8,602 -> base 7,289.83, CGST/SGST 656.08 each (sums to 8,601.99 while the Total row prints 8,602.00); 7,646 -> 6,479.66 / 583.17. Recommended: persist the recompute breakdown (staff_charge, credit, discount, subtotal, gst, total, mode, months, months_left) on `gen_orders` (new columns) and make invoices read it instead of dividing by 1.18.
* Files: `core_functions/gen_orders_functions.py` (new `compute_quote()` shared by a new `POST /orders/quote`), `router/gen_orders.py`, migration adding `gen_orders.mode`, breakdown columns, `coupon_code`. Backward compat: with `mode` absent the intent cannot be inferred reliably (new, renew and staff-only all send the current `plan_id`), so require `mode` from Clockyn; for legacy v1 clients (no `mode`) compute all applicable candidate totals and accept the legacy amount only if it equals one of them (else 400), then process with today's same-plan rule.
* Acceptance: the five rows above return exactly those `amount`s with a tampered client `amount=1`; unknown plan 404; coupon on `upgrade` ignored; expired org gets credit 0.

**R-2 Renew semantics (backend_changes P1-13; DD-16).**
* Current: same `plan_id` while an ACTIVE row exists = staff top-up: `staff_limit = existing + order.staff_limit`, `end_date` unchanged, new row `start_date=today` (`gen_payment_functions.py:77-92`). The UI promises "valid till +12 months". Verified consequences: renewing an **active** plan adds 10+ staff and does not extend; renewing an **expired** plan (still `ACTIVE` row) keeps the past `end_date` -> still expired.
* Required: explicit `mode=renew`: `new_end = (end_date if end_date >= today else today) + plan_month months` (client `calcValidTill`: existing end + 12 when active else today + 12); `staff_limit`: **product decision** (options: keep capacity = existing; or `max(existing, purchased)`; the client currently charges a stepper value that starts at 10) -- do not default to additive. `mode=upgrade`: `staff_limit = existing + extra`, `end_date` kept (that is today's same-plan behaviour and is correct only for this mode). `mode=new`: replace staff and set `today + plan_month`.
* Also expose an explicit state in `GET /plans/subscription/{org}`: `billing_state: paid_active|paid_expired|trial|none`, `trial_staff_limit`, `plan_id`/`plan_month`/`is_expired` in the placeholder, `staff_limit` (never null).
* Acceptance: renew on active org with end 2027-03-01 -> 2028-03-01; renew on expired org -> today+12 months and `plan-status.is_expired=false`; top-up leaves end date unchanged.

**R-3 Placeholder subscription (P1-13).** Keep a non-null `data` object for no-plan orgs but make it schema-stable: always include `plan_id` (null), `plan_month`, `is_expired`, numeric `staff_limit`, `status` in `{ACTIVE,EXPIRED,TRIAL,NONE}`; set `EXPIRED` explicitly in the response when `end_date < today` (do not rely on a status that is never stored). Optional: a daily job (or lazy update in `get_org_plan_status`) that writes `status='EXPIRED'` so `sub_tbl` and the dashboards (which count `status='ACTIVE'`) agree.

**R-4 Plan gate parity and 403 (P2-2; DD-10, DD-11).** Keep the matrix (`core`: `staff_location`,`crm`; `prime`: `crm`; `elite`: none; expired/none: all five) which already equals the client `src/config/plans.ts` `PLAN_BLOCKED_FEATURES` and its evaluation order (trial -> never blocked; expired -> blocked; null plan -> blocked). Changes: (a) fix the stale docstring (`:178-181`); (b) move `check_plan_feature` in `router/chat.py:171,229` outside the `try` (or `except HTTPException: raise`) so the response is a true 403; (c) key the matrix by **plan name** (or add a `tier`/`rank` column to `gen_plans`) instead of ids 1/2/3 so per-duration plan rows cannot fail open; (d) add the gate to the routes the client expects to be gated (all chat child routes, DD-11) only after a product decision; (e) return a machine-readable `code: "PLAN_BLOCKED"`, `feature`, `required_plan` alongside `detail`.

**R-5 Trial and staff limits (P2-11; DD-17).** Define a trial staff limit (config constant), enforce it in `add_staff_function.py`, count only `status=1` users (the same query already does), and return `staff_limit` for trial in the subscription payload. Give extra orgs created via `/company/add` a trial or an explicit "no plan" state (D-9).

**R-6 IST dates (P2-5; DD-14).** Replace `date.today()` with `datetime.now(IST).date()` in all plan/trial comparisons and when stamping `start_date`/`end_date`, so the client's inclusive IST end-day rule holds.

**R-7 Authorisation (P3-1).** Plan/coupon CRUD must require a platform-admin identity (see platform admin doc R-1); read endpoints `/plans/get_plans`, `/plans/coupon/*` can stay public but should be rate-limited; `subscription` and `plan-status` should require an org member token.

**R-8 Backward compatibility.** Legacy (v1) clients share this backend: keep response keys of `get_plans`, `subscription`, `plan-status` unchanged (additive fields only); do not change `plan-status` to the `{success,data}` envelope.

## 8b. Changes (WP10, decisions D-12 / D-13; migration 031)

* **Plan gate (D-12).** The matrix is unchanged (`core`: staff_location, crm; `prime`: crm; `elite`: none; expired / no plan after the trial: everything). New `plan_gate(feature, is_admin)` FastAPI dependency (`gen_plans_functions.py`) finds the organisation of a request (`org_id` in path / query / JSON or form body, else the org of `user_id` / `sender_id` / `created_by`, else the org of `group_id`) and calls `check_plan_feature`, so a lapsed plan answers a real `403 {"detail": "Your plan has expired. ..."}` (the texts the app already shows). Wired on: every `/chat/*` and `/group-chat/*` route (per route, feature `chat`, staff text; announcements and websockets are not gated), and as router-level dependencies on `/api/salary`, `/salary` (payments), `/api/daily-wages`, `/api/hourly-charges`, `/api/wages` and the incentive router (feature `salary_structure`, admin text). A request whose organisation cannot be determined (for example `GET /api/incentive/count/details/{incentive_id}`) is not gated. Orgs on a valid plan or trial see no change. The stale docstring is fixed. Still true: plan ids other than 1/2/3 block nothing (D-3).
* **IST and EXPIRED.** All trial / plan comparisons use the IST date (`utils.timezone.ist_today()`), end date inclusive. `expire_due_subscriptions(today_ist, org_id=None, cursor=None)` writes `status='EXPIRED'` on ACTIVE rows with `end_date < today`; it runs (org-scoped) before `GET /plans/subscription/{org}` and `GET /plans/plan-status/{org}` read, and those reads now take `status IN ('ACTIVE','EXPIRED')` so an expired org keeps its `plan_id` in the response. A failure of the sweep is logged and never breaks the read.
* **Subscription payload (additive).** `billing_state` (`trial|none|paid_active|paid_expired`) and `trial_staff_limit` (10); the no-plan placeholder now also carries `plan_id:null`, `plan_month:null` and a numeric `staff_limit` (10) instead of null.
* **Trial staff limit (D-13).** `add_staff` enforces `TRIAL_STAFF_LIMIT = 10` when there is no active paid plan (trial, expired, none); an active plan keeps its own `staff_limit`.
* **Coupons.** `GET /plans/coupon/first` and `/validate` also respect the optional `valid_from` / `valid_until` (IST, inclusive). New optional `gen_coupons` columns (`discount_type`, `flat_amount`, `max_discount`, `min_amount`, `valid_from`, `valid_until`, `plan_ids`) are applied by the pricing module (`core_functions/pricing.py`), see the orders doc §8b.
* **Prices** are always read from `gen_plans.plan_amount` / `plan_month` (the seeded 49 / 99 / 149 per staff per month are placeholders the owner replaces); no price is hard-coded in code.

## 9. Test notes
* No automated tests exist in the backend repo (no `tests/` directory found). Needs a Postgres fixture with `gen_plans` (>=3 rows: Core/Prime/Elite, several durations), `gen_coupons`, one org with each state: trial active, trial ended, ACTIVE future, ACTIVE past (expired), no rows.
* Critical cases: state table in §4.1 (including `end_date == today`, and `00:00-05:30 IST` boundary once R-6 lands); placeholder shape; `check_plan_feature` matrix (3 plans x 5 features x {trial, expired, active, unknown plan id}); 403 vs 500 on `/chat/list` and `/group-chat/list`; staff limit at exactly `limit` and `limit-1`; plan delete with referencing rows -> 400; coupon case-insensitive validate; recompute table in R-1 as unit tests of `compute_quote`.
