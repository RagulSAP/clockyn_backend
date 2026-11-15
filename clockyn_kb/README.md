# clockyn_kb

The customer knowledge base of the Clockyn app (staff attendance and workforce management: face punch and geofence, shifts, leave requests,
expenses, payroll, reports, CRM, chat, plans and billing, Refer & Earn).

It is plain Markdown in git. Today it is only content plus tooling. Later it becomes the retrieval source of an AI assistant inside the app's
Live Chat, so customers get quick answers and Clockyn support only handles what needs a human.

## The future Live Chat flow (nothing calls Bedrock yet)

1. A company admin opens Live Chat in the Clockyn app and asks a question.
2. The backend sends the question to an AWS Bedrock Knowledge Base whose data source is this repo's customer documents (exported to S3).
3. The assistant answers only from the retrieved documents, in the customer's words, following the rules in `kb/playbook`.
4. Every answer names the article it came from and links it.
5. If the customer asks for a person, if the retrieved documents do not answer the question, or if the topic is marked `handoff: required`
   (payments, refunds, account deletion, anything the playbook lists), the assistant stops answering.
6. It collects the details support needs (company name, admin e-mail, what happened, order or payment reference) as listed in the playbook.
7. The conversation is handed to the Clockyn support team, who see it in the ClockynConsole as a notification and continue in the same chat.
8. Everything else (documents, playbook, evaluation questions, the plan in `docs/live-chat-ai-plan.md`) is prepared here first.

## Repository layout

```
README.md                       this file
package.json                    npm scripts (no dependencies)
docs/CONTRACT.md                the document format and the rules the checker enforces
docs/live-chat-ai-plan.md       the future architecture: Bedrock, hand-off to the console, evaluation
docs/OPEN_QUESTIONS.md          decisions the owner still has to make (policies the KB must not guess)
kb/faq/<bucket>/<category>.md   GENERATED from the FAQ (397 questions, 18 files). Never edit by hand.
kb/guides/<slug>.md             hand-written how-to guides in customer language
kb/playbook/<slug>.md           INTERNAL rules for the assistant (hand-off, tone, safety). Not a customer document.
kb/index.json                   generated index (metadata, headings, word counts)
eval/questions.jsonl            test questions with the expected document and hand-off decision
scripts/                        faq-to-kb.js, export-faq-from-db.py, build-index.js, check-kb.js, export-bedrock.js, tests
dist/                           export output (git-ignored)
```

## Everyday commands

| Command | What it does |
| --- | --- |
| `npm run build` | regenerate `kb/faq` from the FAQ, then rebuild `kb/index.json` |
| `npm run check` | validate every document and the eval questions; also fails if the generated files are out of date |
| `npm test` | unit tests of the scripts (node:test, plus a small Python test) |
| `npm run export` | write the Bedrock-ready copy of the customer documents to `dist/bedrock` |
| `npm run export:playbook` | the same, plus the playbook in `dist/bedrock/playbook` (never the customer data source) |

Node 18 or newer. There are no npm dependencies. Python is only needed for the database export.

## How the FAQ files are generated, and why you do not edit them

The FAQ is a table (`support_faq`) that Clockyn support edits in the console FAQ Hub; `FAQ.sql` in the backend root (the folder above this one) is its starter set.
This repo turns the FAQ into documents, so there is still one place to edit FAQ text: the FAQ table.

- From the starter set: `node scripts/faq-to-kb.js` reads `../FAQ.sql` (and the edits in `FAQ_referral_update.sql` when they
  differ) and writes one file per category: `kb/faq/<bucket>/<category>.md`. Each question is a `##` section whose body is the answer, so every
  section stands alone when it is cut into chunks. Use `--sql <path>` for another location.
- From the live database (to pick up edits made in the FAQ Hub):
  ```
  python scripts/export-faq-from-db.py --out rows.json     # reads DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SCHEMA; one SELECT, read-only
  node scripts/faq-to-kb.js --rows rows.json
  ```
  `rows.json` is the documented intermediate, so there is one renderer for both sources. Only active questions (status 1) are exported.
- `--check` fails when the files differ from what would be generated. A hand edit in `kb/faq` is therefore overwritten or reported.
  To change an answer: edit it in the FAQ Hub (or in `FAQ.sql` for the starter set), export, regenerate, commit.
- The front matter of generated files (handoff, plan, tags) comes from small tables in `scripts/lib/faqrender.js`. Change them there.

## Adding or changing a guide

1. Create `kb/guides/<slug>.md` (kebab-case file name = slug). Start with the front matter described in `docs/CONTRACT.md`.
2. Write in the second person, short sentences, in the customer's words. Name the screen and the exact in-app labels. Each `##` section must
   make sense on its own, because retrieval returns single sections.
3. Include the three required sections: `## Common questions` (4 to 8 `###` questions), `## What can go wrong` and `## When to contact support`.
4. Only state what the app or the FAQ confirms. Never invent prices, limits, refund rules, response times, phone numbers, e-mails or URLs.
   Where a policy is missing, write "Please contact Clockyn support" and add the question to `docs/OPEN_QUESTIONS.md`.
5. Add a few matching questions to `eval/questions.jsonl`.
6. Run `npm run build && npm run check`. Fix every ERROR; read every WARN.

## Exporting for AWS Bedrock

1. `npm run build && npm run check && npm run export`
2. Upload with the commands the script prints, for example
   ```
   aws s3 sync dist/bedrock/faq    s3://<your-bucket>/clockyn-kb/faq/    --delete
   aws s3 sync dist/bedrock/guides s3://<your-bucket>/clockyn-kb/guides/ --delete
   ```
3. Create a Bedrock Knowledge Base whose data source is the S3 prefix `clockyn-kb/`. Start an ingestion job (console or the `StartIngestionJob`
   API) after every upload.
4. Next to every document the export writes `<file>.md.metadata.json` with `metadataAttributes` (slug, title, category, audience, source, plan,
   handoff, app_path, updated, tags). Use them to filter retrieval, for example by `audience` or `plan`, and to show the article title and link.
5. Keep the playbook out of the customer data source. `npm run export:playbook` writes it to a separate folder; use it as the assistant's
   instructions, not as something customers can be quoted from.

Chunking is configured on the Knowledge Base data source, not in this repo. The documents are written so that each `##` section stands alone,
which suits both default and hierarchical chunking. Verify the metadata file format, its size limit and the chunking options against the
current AWS documentation when you integrate; they are described here from the documentation at the time of writing.

## Ownership and review

- Product owner: approves every statement about prices, plans, refunds, data retention, support response times and legal topics. Those are
  listed in `docs/OPEN_QUESTIONS.md` until decided. The checker prints a WARN for refund, SLA, guarantee, legal and "within N hours/days"
  wording so nothing slips in unreviewed.
- Support lead: owns the FAQ table and the playbook; reviews guides for tone and for what customers actually ask.
- Developers: own the scripts and keep guides correct when a screen changes.
- Every document has an `updated` date. Change it when you change the text. Review each document at least every quarter.

## Keeping the KB in step with the product

- Changing a screen, a label, a limit or a price rule in the app or backend? Change the matching guide and FAQ answer in the same release.
- Changing the FAQ in the console? Run the database export and regenerate before the next KB export.
- Add the question customers were asking when the answer was missing to `eval/questions.jsonl`, then fix the document.
- Before every export run `npm run check`. A stale generated file, a broken link, a legacy product name, a stray e-mail address or a phone
  number-like value fails the check.
