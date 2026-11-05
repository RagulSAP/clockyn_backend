# Backend documentation brief (applies to every documentation agent)

**Goal.** Produce rebuild-/change-grade documentation of this FastAPI backend (`C:\POC\pa_backend\Clockyn`, a copy of the legacy backend that will become the Clockyn backend) so that engineers can (a) understand every endpoint, table, rule and integration, and (b) implement the backend changes required by the new Clockyn mobile/web app (frontend repo: `C:\POC\pa_frontend\Clockyn`; its backend requirements are in `C:\POC\pa_frontend\Clockyn\backend_changes.md`, decisions in `docs/DEFERRED_DECISIONS.md`, API client in `src/api/*.ts`, contract notes in `C:\POC\pa_frontend\project_complete_context\shared\api\*.md`).

**Rules**
1. **Documentation only.** Do NOT modify any backend source/config/SQL file. Write only under `C:\POC\pa_backend\Clockyn\project_complete_context\` (your assigned files). Use the Write tool (shell heredocs are unreliable here). Do not run the server, migrations or anything touching a database; static reading and small throwaway scripts in your scratchpad are fine. Never connect to the network services referenced by the code.
2. **Read the source completely** for your scope (routers, core functions, helpers, SQL). Verify, don't guess. If unclear write `(UNVERIFIED: why)`. Cite `file.py:LINE` for every non-trivial rule, query, side effect and bug. Line numbers must be real.
3. **Legacy naming:** the code base still contains the previous product/vendor names (file names, strings, logo files, SQL file name, e-mail templates…). Do NOT retype those names in your docs unless you are writing the dedicated rename inventory; refer to them as "the legacy product name" / "legacy brand" and cite `file:line`. The goal is that after the rename work, these docs stay free of legacy tokens.
4. **No secrets.** If code/config contains keys, tokens, passwords, connection strings or credentials, record only "a secret exists at file:line" — never copy the value.
5. Be precise and compact: tables over prose. No filler.

**Per-domain document template** (`domains/<name>.md`; one per domain you own — keep the headings):
```
# <Domain> — backend context
## 1. Scope & files           (routers, core functions, helpers; line counts; who calls them in the frontend: `src/api/<module>.ts`)
## 2. Data model touched      (tables + columns used, keys, status/enum meanings, soft-delete rules, indexes if known)
## 3. Endpoints               (table: METHOD path · handler fn (file:line) · auth/identity param · plan gate · request (query/path/body w/ types, required) · success response shape · error responses incl. HTTP status quirks)
## 4. Business logic          (step-by-step per endpoint/flow: validations in order, SQL operations, transactions, calculations/formulas, time-zone handling, ID generation, idempotency)
## 5. Side effects & integrations (push/FCM, e-mail, WhatsApp, S3, WebSocket events, Razorpay, ML) with exact payloads
## 6. Config & env vars       (names only)
## 7. Known defects & risks   (bugs, security issues, inconsistent behaviour — each with file:line, severity, impact)
## 8. Clockyn requirements    (what the new frontend needs from this domain: cross-reference items in `backend_changes.md` by ID/title; for each: current behaviour, required change, implementation outline (files/functions to change, SQL/migration needs), backward-compat notes, acceptance tests)
## 9. Test notes              (how to test it: fixtures needed, critical cases, existing tests if any)
```
Every endpoint in your scope must appear in §3; every table the domain touches in §2.

**Final reply** (≤8 lines): files written, endpoint count documented, items marked UNVERIFIED, the 3 most important defects or gaps you found.
