# Legacy name inventory and rename plan (cleanup worklist)

**This is the only document that spells the legacy product and vendor names.** Every other document refers to them as "the legacy product name"/"legacy brand". Scope: the whole backend tree `C:\POC\pa_backend\Clockyn` (87 text files scanned, binary assets listed by name, `.git` history inspected for names only). Tokens searched case-insensitively: `punchapp`, `punch app`, `punch-app`, `PunchApp` (**legacy product name**), `gensharp`, `GenSharp` (**legacy vendor name**), and the vendor-derived short prefix `gen_` / `gen-` on tables, modules and routes (the prefix is probably the vendor abbreviation, `(UNVERIFIED: no document says so)`). The generic words "punch in / punch out" (attendance terminology), the Postgres function `gen_random_uuid`, and the S3 prefix `empolyee-punch-photos` are **not** brand names and are not listed as findings (the prefix is a data-coupled path, section 7).

Kinds: **cosmetic** (comments, docs, internal identifiers, log tags) - safe, no coordination; **user-visible** (strings reaching users, API consumers, PDFs, e-mails, store links) - rebrand with the product; **functional-coupled** (DB object names, URL paths, module names, provider-registered templates, asset file names, ops runbooks) - needs a migration/coordination plan.

## 1. Summary

| Measure | Count |
|---|---|
| Occurrence rows in source/docs (excluding the DDL file) | 214 (cosmetic 63, user-visible 17, functional-coupled 134) |
| of which legacy product name | 73 |
| of which vendor name | 7 |
| of which `gen_` family identifiers | 134 |
| Lines in the root DDL file containing the legacy product name | 190 (187 schema qualifiers + 3 constraint-name-only lines) |
| Lines in the root DDL file with `gen_` objects | 21 |
| Files whose **name** contains a legacy token | 4 (+5 router and 5 core modules with the `gen_` prefix) |
| Database objects with the legacy product name | 1 schema, 3 tables, 3 primary keys, 1 unique index (+ their identity sequences) |
| External registrations carrying the names | MSG91 WhatsApp template, SMS (DLT) template/sender, SES sender identity, Firebase project, Play Store/App Store listings, Razorpay account profile, Meta app, RDS host name, S3 bucket name |
| Hard-coded seller identity (legal entity, CIN, address, logo, signature, support e-mail, website) | `utils/email.py` (section 6) |

## 1a. Status after WP11 (identity, configuration, branding)

Legend: **Done** = fixed in code/config (line numbers in the tables below are from the original scan and have moved); **Kept** = deliberately stays, reason given; **External** = needs an action outside the repo. All brand, contact and seller strings now come from one module, `utils/branding.py`, driven by env variables (README "Environment Variables"). The guard test `tests/test_branding.py` fails when a legacy product/vendor name appears in `*.py`, `*.sql`, templates or the README outside its small allow-list (schema qualifier, the three legacy table names and their key names, the root DDL file name); the `gen_` prefix is not guarded because of decision 8.1 (default: keep).

| Section / item | Status | Detail |
|---|---|---|
| 2. `punchapp_ddl_latest.sql` file name | Kept | renamed together with the schema in the DB rename (8.5); the guard allows the file name |
| 2. `static/Gensharp_logo.png` | Done (reference) / needs artwork | no code references it any more: the invoice logo is `static/<BRAND_LOGO_FILE>` (default `brand_logo.png`); when that file is missing the PDF prints the product initials. The old file is kept untouched. **Needs new artwork from the owner**, then delete the old file |
| 2. `static/Gensharp_logo.webp` | Kept (dead asset) | never referenced; **needs new artwork / deletion by the owner** |
| 2. `static/sign.png` | Done (reference) / needs artwork | signature is `static/<BRAND_SIGNATURE_FILE>` (default `signature.png`; blank space when absent). The old file is a named person's signature of the former seller: **owner to delete and supply the new signatory image** |
| 2. `PA_Backend_Audit_Report.xlsx` | Kept | binary workbook, outside code; rename together with the repository rename |
| 2. `router/gen_*.py`, `core_functions/gen_*_functions.py` | Kept | `gen_` prefix decision 8.1 (default keep): 5 routers import them, URL prefixes are used by the operator console |
| 2. Git repository / folder name, deleted history | Kept | process item (8.7) |
| 3. Schema `punchapp_prod` / `punchapp_dev` / `punchapp_qa` | Kept | DB migration plan (8.5); allowed identifiers in the guard |
| 3. Tables `punchapp_subscription`, `punchapp_settings`, `punchapp_manager_permissions` (+ keys, sequences) | Kept | 38 SQL sites change in the same release as the table rename (8.5); allowed identifiers in the guard |
| 3. `gen_*` tables and `gen_invoice_seq` | Kept | decision 8.1 |
| 3. Stored data embedding names (store_url, presigned URLs, invoice rows) | Kept / External | data, not code; `app_version_control` rows belong to the version work package |
| 4. `app.py` OpenAPI title, description, `GET /` text | Done | `branding.app_name()`; root message is now `Welcome to <APP_NAME> API` |
| 4. `app.py` / routers `gen_*` module names, URLs `/gen-dashboard`, `/auth/gen-admin-users` | Kept | 8.4: needs the alias-router plan with the operator console |
| 4. `router/auth.py` `Gen*` class/function names, `gen_admin_login` SQL, `[GenAdmin]` log tag | Kept | vendor-derived `gen_` family (8.1); the log tag is documented in the README |
| 4. `router/*` `check_plan_feature` import lines (`gen_plans_functions`) | Kept | module rename is 8.4 |
| 4. `punchapp_*` SQL in `manager.py`, `add_staff_function.py`, `company_functions.py`, `manager_functions.py`, `permission_checker.py`, `gen_dashboard_functions.py`, `gen_plans_functions.py`, `gen_payment_functions.py`, `utils/email.py`, `auth_function.py` | Kept | table names (see 3) |
| 4. `gen_*` SQL in `gen_dashboard_functions.py`, `gen_orders_functions.py`, `gen_payment_functions.py`, `gen_plans_functions.py`, `gen_webhook_functions.py`, `utils/razorpay.py`, `utils/email.py` | Kept | table names (decision 8.1) |
| 4. `auth_function.py` OTP e-mail subject | Done | `Your <Purpose> OTP - <APP_NAME>`; body also names the product and adds the support e-mail line when `SUPPORT_EMAIL` is set |
| 4. `auth_function.py` MSG91 docstring | Done | neutral wording |
| 4. `gen_payment_functions.py` invoice item name | Done | `branding.invoice_item_name()` = `<plan> - <APP_NAME> Subscription`; place of supply and SAC now `SELLER_PLACE_OF_SUPPLY` / `SELLER_SAC` |
| 4. `gen_payment_functions.py` comment on the subscription upsert | Done | neutral wording |
| 4. `whatsapp_service.py` default template name | Done | no default: `MSG91_WHATSAPP_TEMPLATE` is required, otherwise the invitation is skipped with a `[WA SKIP]` warning. **External:** create/approve the template at the provider |
| 4. `whatsapp_service.py` default Play Store link | Done | link = `ANDROID_STORE_URL`, else `IOS_STORE_URL`, else `WEBSITE_URL` (older `APP_PLAY_STORE_LINK` / `APP_APP_STORE_LINK` still read as fallbacks); no link configured = skipped with a warning |
| 4. `ml/s3.py` docstring (bucket name) | Done | says "bucket from S3_BUCKET_NAME" |
| 4. `utils/email.py` logo file name, "GS" fallback | Done | `branding.logo_path()`, text mark = product initials |
| 4. `utils/email.py` seller legal name, CIN, address (PDF and e-mail) | Done | `SELLER_NAME` (default `APP_NAME`), `SELLER_CIN`, `SELLER_ADDRESS`, `SELLER_GSTIN`, `SELLER_EMAIL`, `SELLER_PHONE`; unset lines are left out. The invoice PDF used to print the **customer's** GSTIN under the seller block; it is now printed under Bill To and the seller block shows `SELLER_GSTIN` |
| 4. `utils/email.py` invoice e-mail title, footer e-mail and website | Done | `APP_NAME`, `SUPPORT_EMAIL` (else `SELLER_EMAIL`), `WEBSITE_URL` (else `SUPPORT_URL`); neither set = footer shows the product name only |
| 4. `utils/email.py` attachment and zip/xlsx file names, line-item names (3 sites) | Done | `branding.invoice_pdf_filename()` (`<AppName>_Invoice.pdf`), `invoice_zip_filename()`, `invoice_summary_filename()`, `invoice_item_name()` |
| 4. `utils/email.py` docstring | Done | neutral wording |
| 4. `utils/timezone.py` docstring | Done | neutral wording |
| 4. `README.md` title, intro, folder name, docker image name, closing paragraph | Done | product name / `clockyn-backend` |
| 4. `README.md` table names, ERD, `[GenAdmin]` | Kept | documents existing DB objects / log tag (see 3) |
| 4. `migrations/README.md` (5 rows) | Kept | runbook with real QA host and database names; update after the database rename (8.5, 8.9 row 7). Not scanned by the guard (Markdown other than the root README) |
| 4. `punchapp_ddl_latest.sql` qualifiers and `gen_*` objects | Kept | 8.5; the guard allows the schema qualifier and the three table names |
| 5. WhatsApp template | Done (code) / External | see whatsapp_service above |
| 5. Play Store link | Done (code) / External | new application id and listing |
| 5. SMS DLT template and sender | External | provider registration; text is not in this repository |
| 5. SES sender identity | External | verify the new address; `SUPPORT_EMAIL` is separate from `EMAIL_USER` |
| 5. Firebase project, `app_version_control` row | Kept | other work packages (push, version) |
| 5. Razorpay account profile, Meta app, RDS host, S3 bucket, Android/iOS ids in stored links, commit metadata | Kept / External | gateway or infrastructure or data; no remaining occurrence in code |
| 6. Seller identity (name, CIN, address, place of supply, logo, signature, support e-mail, website, file names) | Done | all configurable, see 4. Owner/finance must **fill `SELLER_*` before billing goes live** (legal text on a tax invoice). Invoice number format and sequence name stay (finance decision) |
| 7. Names that stay | Kept | unchanged |

Needs new artwork from the owner: `static/Gensharp_logo.png`, `static/Gensharp_logo.webp` (legacy logo, the PNG was the invoice header), `static/sign.png` (signature). Supply a logo as `static/brand_logo.png` and a signature as `static/signature.png` (or point `BRAND_LOGO_FILE` / `BRAND_SIGNATURE_FILE` at other file names), then delete the three legacy files. `known_faces/` is already excluded from the image (`.dockerignore`) and does not exist in the tree.

## 2. File and folder names

| Path | Token | Kind | Referenced by | Note |
|---|---|---|---|---|
| `punchapp_ddl_latest.sql` (repo root, 43 KB) | legacy product | functional-coupled (docs/runbooks only) | not read by code; cited as "the root DDL file" in the documentation | rename to e.g. `schema/baseline.sql` and regenerate after the DB rename; its content is schema-qualified with the legacy schema name (section 3) |
| `static/Gensharp_logo.png` (22,554 B) | vendor | user-visible + functional | `utils/email.py:173` (invoice PDF header; falls back to a text "GS" at `utils/email.py:201` when missing) | replace asset and path together; a "G" mark on white |
| `static/Gensharp_logo.webp` (16,380 B) | vendor | user-visible | **no code reference** (dead asset) | delete or replace |
| `static/sign.png` | (none; an individual's handwritten signature) | user-visible / legal | `utils/email.py:505-518` ("Authorized Signature" on every invoice) | replace with the new signatory or drop; personal data of a named person |
| `PA_Backend_Audit_Report.xlsx` (repo root) | legacy short name `PA`; the workbook title also reads "PA Backend" and its cells cite the legacy table name `punchapp_subscription` | cosmetic | none | rename to a neutral audit file name; shared strings inside need editing |
| `router/gen_dashboard.py`, `gen_orders.py`, `gen_payments.py`, `gen_plans.py`, `gen_webhook.py` | `gen_` prefix | functional-coupled (imports in `app.py:3,58-61,66`) | `app.py` | rename together with imports; URL prefixes stay unless changed in section 4 |
| `core_functions/gen_dashboard_functions.py`, `gen_orders_functions.py`, `gen_payment_functions.py`, `gen_plans_functions.py`, `gen_webhook_functions.py` | `gen_` prefix | functional-coupled | `router/gen_*.py`, `router/chat.py:6`, `router/face_attendance.py:3`, `router/leads.py:2`, `router/salary.py:3`, `router/staff_location.py:2`, `router/gen_plans.py:3,46` | `check_plan_feature` is imported from `gen_plans_functions` by 5 routers |
| Git repository name (`origin` = `.../pa_backend.git`) and local folder `pa_backend` | legacy short name | cosmetic / process | CI, clone URLs | rename the repo on the host; GitHub keeps redirects |
| Deleted-but-in-history files: `punchapp_ddl_postgresql_New.sql`, `migrations/*`, `test_*` | legacy product | n/a | `git log` | cannot be renamed without rewriting history; see 8.7 |

Folder names `Clockyn`, `Shift Issue`, `known_faces`, `router`, `core_functions`, `ml`, `services`, `utils`, `static`, `migrations`: no legacy token.

## 3. Database objects (functional-coupled, need a migration plan)

| Object | Legacy name | Where it appears | Count |
|---|---|---|---|
| Schema | `punchapp_prod` (production DDL); README also names `punchapp_dev` (dev schema) and database `punchapp_qa` | every object in the root DDL file is qualified (lines 2, 18, 26, 35-37, 41, 56, 67, 79, 94, 98, 108, 120, 133, 150-155, 160, 173-175, 180, 194, 199, 211, 215, 227, 242-243, 248, 261-262, 267, 277-279, 284, 296, 306-307, 312, 322-323, 328, 343, 352, 367, 389, 397, 403, 408, 415, 420, 424, 434, 438, 451, 455, 457, 465, 469, 480, 485, 499, 508, 511, 513, 518, 527, 530, 532, 537, 545, 548, 550, 555, 559, 566-569, 574, 582, 585, 587, 591, 607-611, 616, 625-627, 632, 647-649, 653, 664-665, 670, 680, 685, 695-697, 702, 712-713, 717, 732-736, 741, 750, 755, 772-775, 780, 794-795, 800, 809-812, 817, 830, 843, 851, 853-855, 860, 866, 872, 884, 886, 891, 896, 911-916, 921, 930, 934, 946-950, 955, 963-965, 970, 983-986, 991, 1017-1027, 1032, 1057-1058, 1062, 1070); `migrations/README.md:3,8,10,15,19`; runtime value is env `DB_SCHEMA` (`core_functions/db_function.py:25-38`), also read at `gen_dashboard_functions.py:9,128` | 187 DDL lines |
| Table | `punchapp_manager_permissions` | DDL 843-855; code: `add_staff_function.py:762`, `auth_function.py:868,1097`, `company_functions.py:450`, `manager_functions.py:254,291,299,332,361`, `permission_checker.py:32,143` | 11 SQL sites |
| Table | `punchapp_settings` | DDL 860-866; code: `auth_function.py:869,1098`, `manager_functions.py:249,286,333,362`, `permission_checker.py:33,131,144`, `router/manager.py:82` | 9 SQL sites |
| Table | `punchapp_subscription` | DDL 872-886; code: `add_staff_function.py:293`, `auth_function.py:999`, `gen_dashboard_functions.py:162,168,317,396,497,704,918`, `gen_payment_functions.py:67,71,81,98,107`, `gen_plans_functions.py:43,103,140,401`, `utils/email.py:797` | 18 SQL sites |
| Constraints / indexes named after the tables | `punchapp_manager_permissions_pkey` (DDL 851), `punchapp_settings_pkey` (866), `punchapp_subscription_pkey` (884), `punchapp_subscription_subscription_id_key` (886) | DDL | 4 |
| Identity sequences (not in the DDL text) | `punchapp_manager_permissions_id_seq`, `punchapp_settings_id_seq`, `punchapp_subscription_id_seq` (PostgreSQL default names) | database only `(UNVERIFIED: not visible in the dump)` | 3 |
| Vendor-prefixed tables `gen_*` | `gen_admin_login`, `gen_coupons`, `gen_invoices`, `gen_orders`, `gen_payments`, `gen_plans`, `gen_razorpay_key`, `gen_webhook_events` (8 tables) and sequence `gen_invoice_seq` | DDL lines 343, 352, 360-361, 367, 389, 401, 403, 408, 418, 420, 424, 432, 434, 438, 446, 451, 455, 457, 463, 465; SQL in the files of section 4 (about 85 sites) | 8 + 1 |
| README names | tables `punchapp_*` and `gen_*` in the table list and ERD (`README.md:172,257,263,264,281,321`) | docs | cosmetic |
| Enum types / columns | none carry the name (`order_status`, `incentive_type`, `request_type_enum`) | - | - |

Stored **data** that embeds names: `app_version_control.store_url` (the deleted seed migration contained the legacy Android application id and an App Store id; the live row is probably still legacy `(UNVERIFIED: live data not inspected)`), invoice rows (`gen_invoices.plan_name`, item names are generated at read time), `attendance.punchinface/punchoutface` and legacy `chat.file_url` (presigned URLs that embed the **bucket host name**), `gen_webhook_events.payload` (provider payloads).

## 4. Occurrences in source and documentation (by file)

Kind legend: cosmetic / user-visible / functional-coupled (see top). `Token` is the matched text. Snippets are trimmed to 140 characters.

### `app.py` (13 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_dashboard` | functional-coupled | module name (import / include_router) | `from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, daily_hourly, incentive, face_attendance` |
| 3 | `gen_orders` | functional-coupled | module name (import / include_router) | `from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, daily_hourly, incentive, face_attendance` |
| 3 | `gen_payments` | functional-coupled | module name (import / include_router) | `from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, daily_hourly, incentive, face_attendance` |
| 3 | `gen_plans` | functional-coupled | module name (import / include_router) | `from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, daily_hourly, incentive, face_attendance` |
| 3 | `gen_webhook` | functional-coupled | module name (import / include_router) | `from router import add_staff, auth, home, branch, company, salary, leads, manager, shift, employee, daily_hourly, incentive, face_attendance` |
| 9 | `Punch APP` | user-visible | OpenAPI title (shown at /docs, /openapi.json) | `title="Punch APP",` |
| 10 | `Punch App` | user-visible | OpenAPI description | `description="FastAPI backend for Punch App",` |
| 58 | `gen_orders` | functional-coupled | module name (import / include_router) | `app.include_router(gen_orders.router)` |
| 59 | `gen_payments` | functional-coupled | module name (import / include_router) | `app.include_router(gen_payments.router)` |
| 60 | `gen_plans` | functional-coupled | module name (import / include_router) | `app.include_router(gen_plans.router)` |
| 61 | `gen_webhook` | functional-coupled | module name (import / include_router) | `app.include_router(gen_webhook.router)` |
| 66 | `gen_dashboard` | functional-coupled | module name (import / include_router) | `app.include_router(gen_dashboard.router)` |
| 75 | `Punch app` | user-visible | response text of `GET /` | `return {"message": "Welcome to Punch app Fast API"}` |

### `router/auth.py` (34 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 46 | `GenAdminLoginRequest` | cosmetic | internal identifier (class/function name) | `class GenAdminLoginRequest(BaseModel):` |
| 49 | `GenAdminOTPVerifyRequest` | cosmetic | internal identifier (class/function name) | `class GenAdminOTPVerifyRequest(BaseModel):` |
| 54 | `GenAdminLoginRequest` | cosmetic | internal identifier (class/function name) | `async def gen_admin_login_route(data: GenAdminLoginRequest):` |
| 54 | `gen_admin_login_route` | cosmetic | internal identifier (class/function name) | `async def gen_admin_login_route(data: GenAdminLoginRequest):` |
| 64 | `gen_admin_login` | cosmetic | comment | `# Check gen_admin_login table` |
| 66 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT phone_number, role_id FROM gen_admin_login WHERE phone_number = %s",` |
| 84 | `GenAdmin` | cosmetic | stdout log tag (also prints the OTP: security defect) | `print(f"[GenAdmin] OTP for {data.phone}: {otp_code}")` |
| 98 | `GenAdminOTPVerifyRequest` | cosmetic | internal identifier (class/function name) | `async def gen_admin_verify_otp_route(data: GenAdminOTPVerifyRequest):` |
| 98 | `gen_admin_verify_otp_route` | cosmetic | internal identifier (class/function name) | `async def gen_admin_verify_otp_route(data: GenAdminOTPVerifyRequest):` |
| 121 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT phone_number, role_id FROM gen_admin_login WHERE phone_number = %s",` |
| 138 | `GenAdminUserRequest` | cosmetic | internal identifier (class/function name) | `class GenAdminUserRequest(BaseModel):` |
| 143 | `GenAdminUserUpdateRequest` | cosmetic | internal identifier (class/function name) | `class GenAdminUserUpdateRequest(BaseModel):` |
| 148 | `gen_admin` | cosmetic | internal identifier (class/function name) | `def _require_gen_admin(request):` |
| 149 | `gen_admin` | cosmetic | docstring | `"""Allow any authenticated gen_admin (any role_id in gen_admin_login)."""` |
| 149 | `gen_admin_login` | cosmetic | docstring | `"""Allow any authenticated gen_admin (any role_id in gen_admin_login)."""` |
| 157 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT role_id FROM gen_admin_login WHERE phone_number = %s",` |
| 176 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT role_id FROM gen_admin_login WHERE phone_number = %s",` |
| 186 | `gen-admin` | functional-coupled | public URL `GET /auth/gen-admin-users` | `@router.get("/gen-admin-users")` |
| 187 | `gen_admin_users` | cosmetic | internal identifier (class/function name) | `async def list_gen_admin_users(request: Request):` |
| 192 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT phone_number, name, role_id FROM gen_admin_login ORDER BY phone_number")` |
| 199 | `gen-admin` | functional-coupled | public URL `POST /auth/gen-admin-users` | `@router.post("/gen-admin-users")` |
| 200 | `GenAdminUserRequest` | cosmetic | internal identifier (class/function name) | `async def add_gen_admin_user(data: GenAdminUserRequest, request: Request):` |
| 200 | `gen_admin_user` | cosmetic | internal identifier (class/function name) | `async def add_gen_admin_user(data: GenAdminUserRequest, request: Request):` |
| 205 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT phone_number FROM gen_admin_login WHERE phone_number = %s", (data.phone_number,))` |
| 209 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"INSERT INTO gen_admin_login (phone_number, name, role_id) VALUES (%s, %s, %s)",` |
| 224 | `gen-admin` | functional-coupled | public URL `PUT /auth/gen-admin-users/{phone_number}` | `@router.put("/gen-admin-users/{phone_number}")` |
| 225 | `GenAdminUserUpdateRequest` | cosmetic | internal identifier (class/function name) | `async def update_gen_admin_user(phone_number: str, data: GenAdminUserUpdateRequest, request: Request):` |
| 225 | `gen_admin_user` | cosmetic | internal identifier (class/function name) | `async def update_gen_admin_user(phone_number: str, data: GenAdminUserUpdateRequest, request: Request):` |
| 231 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"UPDATE gen_admin_login SET phone_number = %s, name = %s, role_id = %s WHERE phone_number = %s",` |
| 248 | `gen-admin` | functional-coupled | public URL `DELETE /auth/gen-admin-users/{phone_number}` | `@router.delete("/gen-admin-users/{phone_number}")` |
| 249 | `gen_admin_user` | cosmetic | internal identifier (class/function name) | `async def delete_gen_admin_user(phone_number: str, request: Request):` |
| 254 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("DELETE FROM gen_admin_login WHERE phone_number = %s", (phone_number,))` |
| 306 | `gen_admin` | cosmetic | internal identifier (class/function name) | `_require_gen_admin(request)` |
| 319 | `gen_admin` | cosmetic | internal identifier (class/function name) | `_require_gen_admin(request)` |

### `router/gen_dashboard.py` (2 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_dashboard_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_dashboard_functions import (` |
| 27 | `gen-dashboard` | functional-coupled | URL prefix `/gen-dashboard` and OpenAPI tag of 21 operator routes | `router = APIRouter(prefix="/gen-dashboard", tags=["gen-dashboard"])` |

### `router/gen_orders.py` (3 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_orders_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_orders_functions import create_new_order` |
| 36 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT COUNT(*) AS cnt FROM gen_orders WHERE user_id = %s AND status = 'PAID'",` |
| 60 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_invoices` |

### `router/gen_payments.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 4 | `gen_payment_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_payment_functions import verify_payment_process` |

### `router/gen_plans.py` (2 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import (` |
| 46 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import get_org_plan_status` |

### `router/gen_webhook.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 2 | `gen_webhook_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_webhook_functions import handle_razorpay_webhook, handle_msg91_whatsapp_webhook` |

### `router/chat.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 6 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import check_plan_feature` |

### `router/face_attendance.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import check_plan_feature` |

### `router/leads.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 2 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import check_plan_feature` |

### `router/manager.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 82 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT setting_id, setting_name FROM punchapp_settings ORDER BY setting_id")` |

### `router/salary.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import check_plan_feature` |

### `router/staff_location.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 2 | `gen_plans_functions` | functional-coupled | DB object name in SQL (needs the DB rename) | `from core_functions.gen_plans_functions import check_plan_feature` |

### `core_functions/add_staff_function.py` (2 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 293 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT staff_limit FROM punchapp_subscription` |
| 762 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("DELETE FROM punchapp_manager_permissions WHERE user_id = %s", (user_id,))` |

### `core_functions/auth_function.py` (7 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 327 | `PunchApp` | user-visible | subject of the OTP e-mail sent by SES | `'Subject': {'Data': f"Your {purpose.title()} OTP - PunchApp"},` |
| 868 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT ps.setting_name FROM punchapp_manager_permissions mp` |
| 869 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |
| 909 | `punchapp` | cosmetic | docstring | `"""Send OTP via MSG91 API (synchronous version for punchapp)"""` |
| 999 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT plan_id FROM punchapp_subscription` |
| 1097 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT ps.setting_name FROM punchapp_manager_permissions mp` |
| 1098 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |

### `core_functions/company_functions.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 450 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("DELETE FROM punchapp_manager_permissions WHERE org_id = %s", (org_id,))` |

### `core_functions/gen_dashboard_functions.py` (23 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 13 | `gen_admin` | cosmetic | comment | `changed_by: str  # phone number of the logged-in gen_admin` |
| 162 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT COUNT(*) AS cnt FROM punchapp_subscription WHERE status = 'ACTIVE'")` |
| 168 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT 1 FROM punchapp_subscription s` |
| 176 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_payments` |
| 182 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT COUNT(*) AS cnt FROM gen_orders WHERE status = 'CREATED'")` |
| 185 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT COUNT(*) AS cnt FROM gen_payments WHERE status = 'FAILED'")` |
| 317 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM punchapp_subscription` |
| 337 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans gp ON gp.plan_id = sub.plan_id` |
| 396 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM punchapp_subscription` |
| 401 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans gp ON gp.plan_id = sub.plan_id` |
| 437 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_payments gp` |
| 439 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_orders go ON go.razorpay_order_id = gp.razorpay_order_id` |
| 440 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans gpl ON gpl.plan_id = go.plan_id` |
| 471 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_payments` |
| 496 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_plans gp` |
| 497 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN punchapp_subscription ps` |
| 531 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_admin_login WHERE phone_number = %s` |
| 556 | `gen_admin_login` | functional-coupled | DB object name in SQL (needs the DB rename) | `"UPDATE gen_admin_login SET name = %s WHERE phone_number = %s",` |
| 704 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN punchapp_subscription ps ON ps.org_id = o.org_id AND ps.status = 'ACTIVE'` |
| 705 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans p ON p.plan_id = ps.plan_id` |
| 918 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM punchapp_subscription ps` |
| 921 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans p ON p.plan_id = ps.plan_id` |
| 925 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_invoices gi2` |

### `core_functions/gen_orders_functions.py` (3 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 48 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans gp ON gp.plan_id = %s` |
| 76 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT COUNT(*) AS cnt FROM gen_orders` |
| 96 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_orders (` |

### `core_functions/gen_payment_functions.py` (12 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 40 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_orders go` |
| 41 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans gp ON go.plan_id = gp.plan_id` |
| 56 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_payments (user_id, org_id, razorpay_payment_id, razorpay_order_id, amount, status)` |
| 63 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `"UPDATE gen_orders SET status = 'PAID' WHERE razorpay_order_id = %s",` |
| 67 | `punchapp` | cosmetic | comment | `# Upsert punchapp_subscription` |
| 71 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT staff_limit, end_date FROM punchapp_subscription` |
| 81 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT plan_id FROM punchapp_subscription` |
| 98 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO punchapp_subscription (org_id, user_id, plan_id, subscription_id, status, start_date, end_date, staff_limit)` |
| 107 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `UPDATE punchapp_subscription SET status = 'INACTIVE'` |
| 140 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_invoices (` |
| 148 | `gen_invoice_seq` | cosmetic | internal identifier or comment | `'INV-' \|\| TO_CHAR(NOW(), 'YYYY') \|\| '-' \|\| LPAD(nextval('gen_invoice_seq')::TEXT, 4, '0'),` |
| 179 | `Punch App` | user-visible | invoice item name returned to the client / shown in the invoice | `'name':        f"{plan_name} - Punch App Subscription",` |

### `core_functions/gen_plans_functions.py` (19 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 17 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT plan_id, plan_name, plan_amount, plan_month FROM gen_plans ORDER BY plan_amount ASC")` |
| 43 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM punchapp_subscription ps` |
| 44 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN gen_plans gp ON ps.plan_id = gp.plan_id` |
| 103 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `SELECT 1 FROM punchapp_subscription` |
| 140 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM punchapp_subscription ps` |
| 206 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT coupon_id, coupon_name, discount FROM gen_coupons WHERE status = 1 ORDER BY coupon_id ASC LIMIT 1"` |
| 226 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT coupon_id, coupon_name, discount FROM gen_coupons WHERE UPPER(coupon_name) = UPPER(%s) AND status = 1",` |
| 246 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT coupon_id, coupon_name, discount, status FROM gen_coupons ORDER BY coupon_id ASC")` |
| 264 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_coupons (coupon_id, coupon_name, discount, status)` |
| 265 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `VALUES ((SELECT COALESCE(MAX(coupon_id), 0) + 1 FROM gen_coupons), %s, %s, 1)` |
| 287 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("DELETE FROM gen_coupons WHERE coupon_id = %s", (coupon_id,))` |
| 309 | `gen_coupons` | functional-coupled | DB object name in SQL (needs the DB rename) | `"UPDATE gen_coupons SET coupon_name=%s, discount=%s, status=%s, updated_at=NOW() WHERE coupon_id=%s RETURNING coupon_id, coupon_name, discou` |
| 333 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT plan_id, plan_name, plan_amount, plan_month FROM gen_plans ORDER BY plan_amount ASC")` |
| 351 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_plans (plan_id, plan_name, plan_amount, plan_month)` |
| 352 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `VALUES ((SELECT COALESCE(MAX(plan_id), 0) + 1 FROM gen_plans), %s, %s, %s)` |
| 375 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `"UPDATE gen_plans SET plan_name=%s, plan_amount=%s, plan_month=%s, updated_at=NOW() WHERE plan_id=%s RETURNING plan_id, plan_name, plan_amou` |
| 401 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT COUNT(*) AS cnt FROM punchapp_subscription WHERE plan_id = %s", (plan_id,)` |
| 405 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT COUNT(*) AS cnt FROM gen_orders WHERE plan_id = %s", (plan_id,)` |
| 413 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("DELETE FROM gen_plans WHERE plan_id = %s", (plan_id,))` |

### `core_functions/gen_webhook_functions.py` (6 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 35 | `gen_webhook_events` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_webhook_events (event_id, event_type, payload)` |
| 46 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `UPDATE gen_orders SET status='PAID'` |
| 51 | `gen_payments` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_payments` |
| 54 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_orders WHERE razorpay_order_id=%s` |
| 60 | `gen_orders` | functional-coupled | DB object name in SQL (needs the DB rename) | `UPDATE gen_orders SET status='FAILED'` |
| 121 | `gen_webhook_events` | functional-coupled | DB object name in SQL (needs the DB rename) | `INSERT INTO gen_webhook_events (event_id, event_type, payload)` |

### `core_functions/manager_functions.py` (9 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 249 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT setting_id, setting_name FROM punchapp_settings ORDER BY setting_id")` |
| 254 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"SELECT setting_id FROM punchapp_manager_permissions WHERE user_id = %s AND org_id = %s AND status = 1",` |
| 286 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT setting_id, setting_name FROM punchapp_settings")` |
| 291 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"DELETE FROM punchapp_manager_permissions WHERE user_id = %s AND org_id = %s",` |
| 299 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""INSERT INTO punchapp_manager_permissions (org_id, user_id, setting_id, status, created_at, updated_at)` |
| 332 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT mp.status FROM punchapp_manager_permissions mp` |
| 333 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |
| 361 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT ps.setting_name FROM punchapp_manager_permissions mp` |
| 362 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |

### `core_functions/permission_checker.py` (5 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 32 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT mp.status FROM punchapp_manager_permissions mp` |
| 33 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |
| 131 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `cursor.execute("SELECT setting_name FROM punchapp_settings ORDER BY setting_id")` |
| 143 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `"""SELECT ps.setting_name FROM punchapp_manager_permissions mp` |
| 144 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `JOIN punchapp_settings ps ON mp.setting_id = ps.setting_id` |

### `core_functions/whatsapp_service.py` (2 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 29 | `punchapp` | functional-coupled / user-visible | default WhatsApp template name registered at the provider; staff receive the message | `template_name = os.getenv("MSG91_WHATSAPP_TEMPLATE", "punchapp_user_invitation")` |
| 32 | `punchapp` | functional-coupled / user-visible | default store link embedded in the invitation (legacy Android application id) | `"https://play.google.com/store/apps/details?id=com.punchapp"` |

### `ml/s3.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 192 | `punchapp` | cosmetic | docstring mentioning the legacy bucket name (bucket itself comes from env `S3_BUCKET_NAME`) | `Save a punch-in or punch-out face image to S3 (punchapp-prod bucket).` |

### `utils/email.py` (20 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 82 | `gen_invoices` | cosmetic | docstring | `"""Normalize a gen_invoices row for API responses."""` |
| 173 | `Gensharp` | functional-coupled / user-visible | logo asset file name used in the invoice PDF header | `os.path.dirname(os.path.dirname(__file__)), 'static', 'Gensharp_logo.png'` |
| 214 | `GenSharp` | user-visible | seller legal name in the invoice PDF | `[[Paragraph('<b>GenSharp Solutions Private Limited</b>',` |
| 611 | `Punch App` | user-visible | HTML `<title>` of the invoice e-mail | `<title>Invoice – Punch App</title>` |
| 623 | `GenSharp` | user-visible | seller legal name in the invoice e-mail header | `GenSharp Solutions Private Limited</td>` |
| 726 | `gensharp` | user-visible | support e-mail (mailto) in the invoice e-mail footer | `<a href="mailto:support@gensharp.in"` |
| 727 | `gensharp` | user-visible | support e-mail text in the footer | `style="color:#2563EB;text-decoration:none;">support@gensharp.in</a>` |
| 729 | `gensharp` | user-visible | website link in the footer | `<a href="https://www.gensharp.in"` |
| 730 | `gensharp` | user-visible | website text in the footer | `style="color:#2563EB;text-decoration:none;">www.gensharp.in</a>` |
| 743 | `PunchApp` | user-visible | attachment file name of the invoice PDF | `filename='PunchApp_Invoice.pdf')` |
| 797 | `punchapp` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN punchapp_subscription ps  ON u.org_id   = ps.org_id` |
| 799 | `gen_plans` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_plans             gp  ON ps.plan_id  = gp.plan_id` |
| 800 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `LEFT JOIN gen_invoices          gi  ON gi.user_id  = u.user_id` |
| 893 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_invoices gi` |
| 945 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_invoices gi` |
| 997 | `Punch App` | user-visible | invoice line-item name (PDF/API) | `'name':        f"{row['plan_name']} - Punch App Subscription",` |
| 1116 | `gen_invoices` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_invoices gi` |
| 1155 | `Punch App` | user-visible | invoice line-item name (bulk export) | `"name":        f"{row['plan_name']} - Punch App Subscription",` |
| 1182 | `punchapp` | user-visible | file name of the Excel summary inside the bulk zip | `zf.writestr(f"punchapp_invoices_summary_{from_date}_to_{to_date}.xlsx", excel_bytes)` |
| 1185 | `punchapp` | user-visible | file name of the bulk invoice zip download | `zip_filename = f"punchapp_invoices_{from_date}_to_{to_date}.zip"` |

### `utils/razorpay.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 52 | `gen_razorpay_key` | functional-coupled | DB object name in SQL (needs the DB rename) | `FROM gen_razorpay_key` |

### `utils/timezone.py` (1 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 2 | `PunchApp` | cosmetic | module docstring | `Centralized timezone handling for PunchApp backend.` |

### `README.md` (35 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 1 | `Punch App` | cosmetic | documentation | `# Punch App — Backend` |
| 3 | `Punch App` | cosmetic | documentation | `FastAPI backend for Punch App — a workforce management platform with face-based attendance, payroll, chat, CRM, and subscription billing.` |
| 29 | `punchapp` | cosmetic | documentation | `punchapp-backend/` |
| 60 | `gen_orders` | cosmetic | documentation | `│   ├── gen_orders.py       # /orders/*` |
| 61 | `gen_payments` | cosmetic | documentation | `│   ├── gen_payments.py     # /payments/*` |
| 62 | `gen_plans` | cosmetic | documentation | `│   ├── gen_plans.py        # /plans/*` |
| 63 | `gen_webhook` | cosmetic | documentation | `│   ├── gen_webhook.py      # /webhook/*` |
| 64 | `gen_dashboard` | cosmetic | documentation | `│   ├── gen_dashboard.py    # /dashboard/*` |
| 80 | `gen_plans_functions` | cosmetic | documentation | `│   ├── gen_plans_functions.py        # Plans, subscriptions, free trial, feature gating` |
| 81 | `gen_payment_functions` | cosmetic | documentation | `│   ├── gen_payment_functions.py      # Razorpay payment verify, subscription upsert, invoice` |
| 82 | `gen_orders_functions` | cosmetic | documentation | `│   ├── gen_orders_functions.py       # Razorpay order creation` |
| 102 | `gen_dashboard_functions` | cosmetic | documentation | `│   ├── gen_dashboard_functions.py    # Super-admin dashboard` |
| 103 | `gen_webhook_functions` | cosmetic | documentation | `│   ├── gen_webhook_functions.py      # Razorpay webhook handler` |
| 172 | `punchapp` | cosmetic | documentation | `- Managers have granular permissions stored in `punchapp_manager_permissions`.` |
| 254 | `gen_plans` | cosmetic | documentation | `\| `gen_plans` \| Subscription plan catalogue (Core/Prime/Elite). \|` |
| 255 | `gen_orders` | cosmetic | documentation | `\| `gen_orders` \| Razorpay orders. \|` |
| 256 | `gen_payments` | cosmetic | documentation | `\| `gen_payments` \| Verified payments. \|` |
| 257 | `punchapp` | cosmetic | documentation | `\| `punchapp_subscription` \| Active/expired subscriptions per org. \|` |
| 259 | `gen_invoices` | cosmetic | documentation | `\| `gen_invoices` \| Tax invoices generated after payment. \|` |
| 260 | `gen_razorpay_key` | cosmetic | documentation | `\| `gen_razorpay_key` \| Razorpay key/secret stored in DB (not .env). \|` |
| 263 | `punchapp` | cosmetic | documentation | `\| `punchapp_settings` \| Master list of permission settings. \|` |
| 264 | `punchapp` | cosmetic | documentation | `\| `punchapp_manager_permissions` \| Per-manager permission assignments. \|` |
| 268 | `gen_admin_login` | cosmetic | documentation | `\| `gen_admin_login` \| Super-admin accounts for the internal dashboard. \|` |
| 269 | `gen_coupons` | cosmetic | documentation | `\| `gen_coupons` \| Discount coupons for plans. \|` |
| 281 | `punchapp` | cosmetic | documentation | `org_info \|\|--o\| punchapp_subscription : "has"` |
| 316 | `gen_orders` | cosmetic | documentation | `API->>DB: INSERT gen_orders` |
| 321 | `gen_orders` | cosmetic | documentation | `API->>DB: INSERT gen_payments, UPDATE gen_orders, UPSERT punchapp_subscription` |
| 321 | `gen_payments` | cosmetic | documentation | `API->>DB: INSERT gen_payments, UPDATE gen_orders, UPSERT punchapp_subscription` |
| 321 | `punchapp` | cosmetic | documentation | `API->>DB: INSERT gen_payments, UPDATE gen_orders, UPSERT punchapp_subscription` |
| 322 | `gen_invoices` | cosmetic | documentation | `API->>DB: Deactivate free_trial, INSERT gen_invoices` |
| 437 | `gen_razorpay_key` | cosmetic | documentation | `> Razorpay keys are stored in the `gen_razorpay_key` DB table, not in `.env`.` |
| 446 | `punchapp` | cosmetic | documentation | `docker build -t punchapp-backend .` |
| 447 | `punchapp` | cosmetic | documentation | `docker run -p 8080:8080 --env-file .env punchapp-backend` |
| 477 | `GenAdmin` | cosmetic | documentation | `- Key log prefixes: `[FCM]`, `[Notify]`, `[PushToken]`, `[Meta CAPI]`, `[GenAdmin]`.` |
| 529 | `Punch App` | cosmetic | documentation | `Punch App backend is a production-grade FastAPI monolith serving a multi-tenant workforce management SaaS. It integrates ML-based face recog` |

### `migrations/README.md` (5 rows)

| Line | Token | Kind | Note | Snippet |
|---|---|---|---|---|
| 3 | `punchapp` | functional-coupled | ops runbook: legacy schema/database/host names used in commands | `## How to migrate from punchapp_dev to public schema` |
| 8 | `punchapp` | functional-coupled | ops runbook: legacy schema/database/host names used in commands | `### Step 2: Test in punchapp_dev` |
| 10 | `punchapp` | functional-coupled | ops runbook: legacy schema/database/host names used in commands | `psql -h punchapp-qa.crgm4ow00dzd.ap-south-1.rds.amazonaws.com -U postgres -d punchapp_qa -c "SET search_path TO punchapp_dev;" -f migrations` |
| 15 | `punchapp` | functional-coupled | ops runbook: legacy schema/database/host names used in commands | `psql -h punchapp-qa.crgm4ow00dzd.ap-south-1.rds.amazonaws.com -U postgres -d punchapp_qa -c "SET search_path TO public;" -f migrations/001_c` |
| 19 | `punchapp` | functional-coupled | ops runbook: legacy schema/database/host names used in commands | `Change `DB_SCHEMA=punchapp_dev` to `DB_SCHEMA=public`` |

### `punchapp_ddl_latest.sql` (the root DDL file)

| Lines | Content | Kind |
|---|---|---|
| 2, 18, 26, 35-37, 41, 56, 67, 79, 94, 98, 108, 120, 133, 150-155, 160, 173-175, 180, 194, 199, 211, 215, 227, 242-243, 248, 261-262, 267, 277-279, 284, 296, 306-307, 312, 322-323, 328, 343, 352, 367, 389, 397, 403, 408, 415, 420, 424, 434, 438, 451, 455, 457, 465, 469, 480, 485, 499, 508, 511, 513, 518, 527, 530, 532, 537, 545, 548, 550, 555, 559, 566-569, 574, 582, 585, 587, 591, 607-611, 616, 625-627, 632, 647-649, 653, 664-665, 670, 680, 685, 695-697, 702, 712-713, 717, 732-736, 741, 750, 755, 772-775, 780, 794-795, 800, 809-812, 817, 830, 843, 851, 853-855, 860, 866, 872, 884, 886, 891, 896, 911-916, 921, 930, 934, 946-950, 955, 963-965, 970, 983-986, 991, 1017-1027, 1032, 1057-1058, 1062, 1070 | every `CREATE TABLE/INDEX/ALTER TABLE` and the three enum-typed columns are qualified with the legacy schema name (190 lines contain the legacy product name) | functional-coupled |
| 843, 851, 853-855 | table `punchapp_manager_permissions`, its PK and 3 indexes | functional-coupled |
| 860, 866 | table `punchapp_settings`, PK | functional-coupled |
| 872, 884, 886 | table `punchapp_subscription`, PK, unique index on `subscription_id` | functional-coupled |
| 343, 352, 360-361, 367, 389, 401, 403, 408, 418, 420, 424, 432, 434, 438, 446, 451, 455, 457, 463, 465 | `gen_*` tables, PKs, unique indexes, enum default casts | functional-coupled (if the prefix is renamed) |

## 5. External and provider-coupled identities

| Item | Where | Kind | What must change outside the repo |
|---|---|---|---|
| WhatsApp invitation template name `punchapp_user_invitation` (default) | `core_functions/whatsapp_service.py:29` (override: env `MSG91_WHATSAPP_TEMPLATE`) | functional + user-visible | create and get approved a new template at the WhatsApp provider (approval lead time), then set the env var; the template body (with the legacy brand) lives at the provider |
| Play Store link `com.punchapp` (default) | `core_functions/whatsapp_service.py:32` (override: env `APP_PLAY_STORE_LINK`) | user-visible | new application id/store listing (frontend `APP_ID`, `backend_changes.md` C-1, C-9) |
| SMS OTP template (DLT) and sender id | env `MSG91_TEMPLATE_ID` (`core_functions/auth_function.py:39`); README also lists `MSG91_SENDER_ID` (unused in code) | user-visible | new DLT-registered template/header with the new brand; text is not in this repo |
| SES sender identity | env `EMAIL_USER` (`auth_function.py:44`, `utils/email.py:586`, `suggest_feature_functions.py:28`) | user-visible | verify the new domain/address (DKIM/SPF), request production access if needed `(UNVERIFIED: current identity not visible)` |
| Firebase project (FCM) | env `FCM_*` (`auth_function.py:49-55`) | functional | new project and service account for the new bundle id (`backend_changes.md` P0-9); channel id `attendance_notifications` is neutral and stays |
| App version policy row | table `app_version_control` (`router/version.py:15`) | user-visible data | add a row for the new app (`migrations_and_changes.md` M4) |
| Razorpay account profile (business name, checkout descriptor, invoices from the gateway) | gateway dashboard, key in `gen_razorpay_key` | user-visible | account is in the legacy entity's name; legal/finance decision `(UNVERIFIED)` |
| Meta app / pixel | `services/meta_events.py:7` (docstring embeds the legacy Meta app id), env `META_ACCESS_TOKEN`, `META_PIXEL_ID` | functional (dead code) | new app/pixel if the service is ever wired |
| RDS host and database names | `migrations/README.md:10,15` (QA host, database `punchapp_qa`) | functional (ops) | rename/replace in the runbook; host name is infrastructure, not a secret |
| S3 bucket | env `S3_BUCKET_NAME`; docstring names the legacy bucket (`ml/s3.py:192`) | functional | bucket rename = copy objects (`known_faces/`, `org_logo/`, `employee_profile/`, `empolyee-punch-photos/`, `documents/`, `client_support/`) and rewrite stored presigned URLs (section 8) |
| Android/iOS application ids in stored links | `app_version_control` seed history (`com.gensharp...`) | user-visible data | update rows |
| Commit metadata | 9 of 25 commits carry an author address on the vendor domain | cosmetic | cannot be changed without a history rewrite; accept or use `.mailmap` |

## 6. Hard-coded seller identity in invoices (vendor identity, compliance-relevant)

| Item | Location | Note |
|---|---|---|
| Seller legal name | `utils/email.py:214` (PDF), `:623` (e-mail) | the legacy vendor entity |
| CIN and registered office address | `utils/email.py:206-209` (PDF), `:629-631` (e-mail) | text constants |
| Place of supply default `Tamil Nadu (33)` | `utils/email.py:260` | assumes the seller state; GST split CGST/SGST 9%+9% only (`gen_payment_functions.py:124-126`), no IGST |
| Logo / "GS" fallback | `utils/email.py:172-174,198-201` | asset file and initials |
| Authorized signature image | `utils/email.py:505-518` | `static/sign.png` |
| Support e-mail and website | `utils/email.py:726-730` | footer |
| Invoice number format `INV-YYYY-NNNN` from sequence `gen_invoice_seq` | `gen_payment_functions.py:148` | continuity of numbering under a new legal entity is a finance decision |
| File names `PunchApp_Invoice.pdf`, `punchapp_invoices_*.zip/xlsx` | `utils/email.py:743,1182,1185` | download names |

Recommendation: move all of these into one configuration object (see `database/migrations_and_changes.md` M13) so the rename and any later legal-entity change need no code edit.

## 7. Names that look legacy but should be kept

| Name | Why keep |
|---|---|
| S3 key prefixes `known_faces/`, `org_logo/`, `employee_profile/`, `empolyee-punch-photos/` (typo), `documents/`, `client_support/` | no brand; the keys are stored in the DB (`punchinface`, `file_url`, `photo_url`) and in object names; renaming requires copying and rewriting data for no benefit |
| Notification channel id `attendance_notifications` | already used by the new app (`app.config.ts`), not branded |
| Terms "punch in/out", route `/face/punch`, `punch_endpoint`, column names `punch_in`, `punchinface` | attendance vocabulary, not the product name |
| `gen_random_uuid()` | PostgreSQL built-in |
| Typos `moblie_otp_verification`, `empolyee-punch-photos` | not brand; fix only as separate hygiene |
| JWT issuer / user-agent strings | none exist (no JWT, no outbound `User-Agent` customisation) |

## 8. Safe rename plan (ordered)

Principles: one backend codebase serves the v1 app, the Clockyn app and the operator console, so any public URL or response-field change must keep an alias until every client is updated; database renames are metadata-only in PostgreSQL but invalidate running connections and cached plans, so they ship together with the code in a maintenance window.

### 8.1 Phase 0 - decisions and prerequisites
1. Decide the new neutral/branded names: schema (e.g. `<new>_prod`), the three tables (suggested neutral names `manager_permissions`, `permission_settings`, `org_subscription`), whether `gen_*` (tables, routes, modules) is renamed at all (recommended: **modules and tables yes only if the operator console and v1 app are updated at the same time; otherwise leave the `gen_` prefix**, since it is internal), the product name string and seller identity (section 6).
2. Take a verified backup/snapshot of the production database and the S3 bucket; freeze the schema baseline (see `database/migrations_and_changes.md` section 2).
3. Add a single config source for the product name, support e-mail, website, store links, template names (env or the `billing_entity` table M13) so Phase 2 is data, not code.

### 8.2 Phase 1 - cosmetic (any time, no coordination)
Docstrings/comments (section 4 rows marked cosmetic), `README.md`, `app.py:9-10` is user-visible (see 8.3), the `[GenAdmin]` log tag, internal class/function names, the audit workbook title and file name. Risk: none. Do it in one commit to keep history clean.

### 8.3 Phase 2 - user-visible strings (ship with the new app)
Order: (1) FastAPI title/description and the `GET /` text (`app.py:9,10,75`); (2) OTP e-mail subject (`auth_function.py:327`); (3) invoice texts: item names (`gen_payment_functions.py:179`, `utils/email.py:997,1155`), e-mail title and footer, attachment/zip file names (`utils/email.py:611,726-730,743,1182,1185`); (4) seller identity (section 6); (5) WhatsApp default template/link and store links (env vars first, then change the code defaults to neutral placeholders, `whatsapp_service.py:29,32`); (6) logo and signature assets. Risks: invoice wording is legal text (GST invoice content rules); the WhatsApp template must exist and be approved **before** the env var flips or invitations silently fail (`send_employee_onboard_whatsapp` returns False and only logs); SMS DLT template/sender approval lead time; clients that match on file names of downloads. Test: send an OTP, an invoice e-mail, a bulk invoice zip and a staff invitation in a staging environment.

### 8.4 Phase 3 - code identifiers and public URLs (functional-coupled)
1. Rename modules `gen_*` and update imports (`app.py:3,58-61,66`; 5 routers import `check_plan_feature`; `router/gen_*.py` import their core module). Pure refactor; verify with the route-count test (`api/endpoint_catalog.md`: 302 mounted decorators must remain).
2. Public URLs `/gen-dashboard/*` (21 routes), `/auth/gen-admin-users*` (4) and `/auth/admin-login*` (2) are used only by the operator console and not by Clockyn (no occurrence in `C:\POC\pa_frontend\Clockyn\src`). Add the new prefix, keep the old one as an alias router for one release, switch the console, then remove the alias. Do not rename `/auth/*`, `/plans/*`, `/orders/*`, `/payments/*`, `/webhook/*` (not branded; webhook URLs are registered at Razorpay and MSG91).
3. The `tags=["gen-dashboard"]` OpenAPI tag changes with the prefix.

### 8.5 Phase 4 - database (maintenance window, one release)
Order matters:
1. Pre-checks: no long transactions; list dependent objects (`pg_depend`, views, functions, roles with `ALTER ROLE ... SET search_path`, BI/export jobs, pg_dump scripts, RDS Proxy); confirm no other consumer uses the legacy schema name.
2. In one transaction: `ALTER TABLE LEGACY_x RENAME TO new_x;` for the three tables; `ALTER INDEX ... RENAME` for the three `_pkey` constraints (`ALTER TABLE ... RENAME CONSTRAINT`) and the unique index; rename the identity sequences (`ALTER SEQUENCE ... RENAME`); optionally the `gen_*` tables and `gen_invoice_seq`. (`ALTER TABLE ... RENAME` is instant; foreign keys, indexes and data follow automatically.)
3. `ALTER SCHEMA legacy_schema RENAME TO new_schema;` is also instant, but **every open session keeps resolving `search_path` by the old name and fails** (`options=-c search_path=` at connect and `SET search_path` per checkout, `db_function.py:37,61`). Therefore: stop traffic (or scale the service to zero) -> run the rename -> set `DB_SCHEMA=<new>` in the environment -> start the new code. `gen_dashboard_functions.py:128` qualifies with `DB_SCHEMA` and follows automatically.
4. Update the SQL in the files listed in section 3 in the **same release** (about 38 sites for the three tables, about 85 for `gen_*`). Views are not a safe compatibility layer: `INSERT ... ON CONFLICT` (used at `gen_payment_functions.py:98-102`) does not work on views, so plan a big-bang cut-over instead of a dual-name period.
5. Rename the DDL file and regenerate the baseline from the migrated database; update `migrations/README.md` (database, schema and host names).
6. Rollback: reverse the same renames (kept in the migration as a down script) and redeploy the previous image.
Risks: forgetting a SQL string (runtime "relation does not exist" only on that code path, e.g. rarely used operator screens); cached prepared statements in long-lived connections (restart the service); the permission table drives manager access, so a mistake locks managers out (smoke test `GET /managers/{id}/permissions`, `/auth/employee-login` for a manager); a missed `punchapp_subscription` reference breaks plan gating for every tenant (smoke test `/plans/subscription/{org}`, a punch with plan gate).

### 8.6 Phase 5 - external services
WhatsApp template and SMS DLT template (long lead time, start in Phase 0), SES identity verification, new Firebase project and service account (dual-send during the transition, `backend_changes.md` P0-9), store listings and `app_version_control` rows, Razorpay account/descriptor, Meta app. Each can be switched by env/data once the code in Phases 2-4 is in place.

### 8.7 Phase 6 - repository and storage
Rename the git repository and update CI/clone URLs; accept the legacy names and vendor-domain author addresses in history (a rewrite would change every commit id; use `.mailmap` if display matters). S3 bucket rename is optional: if done, copy objects with `aws s3 sync`, update env `S3_BUCKET_NAME`, and either re-sign or rewrite the stored presigned URLs (`attendance.punchinface/punchoutface`, legacy `chat.file_url` rows) - `refresh_punch_image_url` re-signs by key only for keys under the punch-photo prefix (`ml/s3.py:401-423`) and `get_chat_file_presigned_url` extracts the key from legacy URLs (`:252-283`), so a prefix-preserving copy keeps most links working.

### 8.8 Phase 7 - verification
1. `rg -n -i "punch ?app|gensharp" C:\POC\pa_backend\Clockyn --glob "!project_complete_context/rename_inventory.md" --glob "!.git"` must return nothing (this document is the only allowed place).
2. `rg -n "\bgen[_-]" ...` only for the decision taken in 8.1 (zero if the prefix is renamed).
3. `psql -c "\dt+ <schema>.*"` and `\ds` show no legacy-named table or sequence; `SELECT nspname FROM pg_namespace` shows only the new schema.
4. Run the AST route extraction again: route count unchanged (302), no import errors (`python -c "import app"` in a throw-away environment).
5. Smoke the flows touched: OTP e-mail/SMS, manager permission screens, plan gating, payment verify + invoice e-mail with PDF, bulk invoice download, operator console login, staff invitation WhatsApp, push delivery.

### 8.9 Risk register

| # | Risk | Mitigation |
|---|---|---|
| 1 | Schema/table rename breaks running sessions or a forgotten SQL string | maintenance window, grep gate (8.8), staged rollout on QA first, tested down-migration |
| 2 | Legal/GST non-compliance if invoices change entity or wording without finance sign-off | treat section 6 as a business change request; keep old invoices immutable (already stored in `gen_invoices`) |
| 3 | Provider approvals (WhatsApp, DLT, SES, Firebase) lag the release | start in Phase 0; keep env-driven defaults so code can ship first |
| 4 | Operator console or v1 app calls renamed URLs | alias router for one release, coordinate console release |
| 5 | Stored presigned URLs embed the bucket host | keep the bucket, or re-sign/rewrite after a copy |
| 6 | `gen_` prefix removal is a large mechanical change with little value | decide explicitly in Phase 0; default is to keep |
| 7 | Documentation drift: README and runbook still show old names | update `README.md` and `migrations/README.md` in Phase 1 and after Phase 4 |

## Update 2026-11-14: table prefix removed

The three tables `punchapp_subscription`, `punchapp_settings` and `punchapp_manager_permissions` were renamed to `subscription`, `settings` and `manager_permissions`, together with their primary keys, the unique index of `subscription` and the identity sequences. Code, tests, `clockyn_ddl_latest.sql` and the README use the new names; `migrations/038_drop_legacy_table_prefix.sql` renames the objects in an existing database. Only migrations 031 and 038 may still spell the old names (the brand guard in `tests/test_branding.py` enforces it). The rows above that call these tables "Kept" describe the state before this change.
