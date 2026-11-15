# Document contract

The rules every document in `kb/` follows. `npm run check` (scripts/check-kb.js) enforces them. Customers are company admins; the documents
also describe what managers and staff can do.

## Folders and slugs

| Folder | Content | Slug |
| --- | --- | --- |
| `kb/faq/<bucket>/<category>.md` | generated from the FAQ, never hand-edited | `faq-<file name>` |
| `kb/guides/<slug>.md` | hand-written how-to guides | the file name |
| `kb/playbook/<slug>.md` | internal rules for the assistant (audience `bot`) | the file name |

- Slugs are kebab-case and unique across the whole repo. Guides and playbook files are flat (no sub-folders).
- The category slug is the category name lower-cased, `&` written as `and`, every run of other characters turned into one `-`
  ("Shifts, Holidays & Rules" becomes `shifts-holidays-and-rules`). Bucket folders use the same rule.
- For FAQ files the folder must match the bucket (`category`) and the file name must match the title.

## Front matter

Simple `key: value` lines between two `---` lines (the value is everything after the first colon; no nesting).

| Key | Required | Values |
| --- | --- | --- |
| `title` | yes | max 80 characters. Guides: what a customer would search for. FAQ files: the category name. |
| `summary` | yes | one sentence, max 200 characters |
| `category` | yes | FAQ: the bucket name. Guides: one of the FAQ buckets or `Guides`. Playbook: `Playbook`. |
| `audience` | yes | `admin`, `manager`, `staff`, `all`, or `bot` (playbook only) |
| `source` | yes | `faq`, `guide` or `playbook`, matching the folder |
| `handoff` | yes | `none` (answer from the KB), `offer` (answer, then offer a human), `required` (collect details and hand over; do not try to resolve) |
| `tags` | yes | comma separated, lower-case words and synonyms a customer would type |
| `updated` | yes | a real date, `YYYY-MM-DD` |
| `plan` | no (default `all`) | `all`, `prime-elite`, `elite`, `core-only`: which plans have the feature |
| `app_path` | no | where the feature is in the app, with the exact labels, for example `Settings > Refer & Earn` |
| `related` | no | comma separated slugs that exist |

The FAQ buckets are: Set up & Account, Attendance & Time, Pay & Expenses, Team & Customers, Plans & Payments, Help & Fixes.

## Body

A small Markdown subset; raw HTML is not allowed (outside code).

- Paragraphs, `-` bullets, `1.` numbered lists, pipe tables, fenced code, inline `code`, **bold**.
- Links: `[text](kb:slug)` for another document, `[text](https://...)` for an outside page (only hosts listed in `scripts/allowlist.json`).
- Callouts are blockquotes that start with `**Note**`, `**Tip**` or `**Warning**`.
- No emoji, no images, no `# H1` (the title comes from the front matter), no skipped heading levels.
- FAQ files contain only `##` sections: the heading is the question exactly as in the FAQ, the body is the answer. Nothing sits before the
  first question and no answer may be empty. A line in an answer that would read as a heading gets a leading backslash when generated.

## Guides

- 250 to 1500 words. Second person, short sentences, plain words.
- A short introduction, then `##` sections that each stand alone: name the feature and screen explicitly, never "above" or "here".
- Tasks are numbered steps with the exact in-app labels.
- Required sections: `## Common questions` (4 to 8 `###` questions phrased the way customers ask), `## What can go wrong`
  (symptom, cause, fix) and a final `## When to contact support` that lists the details to have ready (company name, admin e-mail,
  what you tried, a screenshot).
- Never mention internals: console, tables, API, endpoints, servers, migrations (WARN in the checker).
- Never state a price (say "shown in the app"), a limit, a policy, a refund rule, a response time, a phone number, an e-mail address or a
  URL that is not confirmed. Missing policy: "Please contact Clockyn support" and an entry in `docs/OPEN_QUESTIONS.md`.

## Playbook

Plain, short, testable instructions for the assistant ("Always ...", "Never ..."): when to hand over, what to collect, tone, what it may and
may not say. `audience: bot`, `source: playbook`, `category: Playbook`. It is not part of the customer data source.

## eval/questions.jsonl

One JSON object per line:

```
{"id":"q001","question":"how do i add staff","expected_docs":["faq-staff-management","adding-and-managing-staff"],"handoff":"none","notes":"optional"}
```

`id` is unique, `question` has at least 6 characters, `expected_docs` is a non-empty list of existing slugs, `handoff` is `none`, `offer` or
`required`. Questions are written the way customers write them: short, informal, typos and some Hinglish allowed.

## What the checker rejects (ERROR) and flags (WARN)

- ERROR: missing or invalid front matter, wrong source or audience for the folder, duplicate slugs, unknown `kb:` or `related` slugs,
  links that are not `kb:` or https, a URL, e-mail address or phone-number-like value that is not allow-listed (e-mail addresses at
  `example.com` are always fine), raw HTML, an H1 or skipped heading levels, an unclosed code fence, TODO or FIXME or lorem text, secret-like
  strings, a legacy product name anywhere (including `README.md` and `docs/`), a guide outside 250 to 1500 words or missing a required
  section, an FAQ file with anything but `##` question sections, invalid eval lines, a stale `kb/index.json` or stale generated FAQ files.
- WARN: a directory or the eval file is missing, an unknown front matter key, internal words in customer documents (console, endpoint, API,
  database, table, server, backend, migration), policy-sensitive words outside the playbook (refund, SLA, guarantee, legal, "within N hours or
  days"), a guide that has fewer than 4 or more than 8 common questions, an FAQ category or guide that no eval question expects.
