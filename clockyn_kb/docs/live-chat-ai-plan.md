# Live Chat AI assistant: plan

Audience: maintainers. Status: **proposal**. Nothing here is built; today only this knowledge base exists. Everything under "Today" describes the code as it is on 2026-11-15; everything else is a proposal to be decided.

## Today: how Live Chat works

- **Data.** One thread per organisation (`live_chat_thread`, status `open` or `closed`) and its messages (`live_chat_message`). `sender_type` is `org` (the company admin) or `support` (a console operator), enforced by a database check. Text is limited to 4,000 characters; images and files are allowed and stored in S3.
- **App.** Only admins have Live Chat (Support tab). Routes `/live-chat/messages`, `/unread`, `/send`, `/send-file`, `/mark-read`; rate limit `LIVE_CHAT_SEND` per user; not plan-gated, so a lapsed customer can still reach support. Live updates arrive on the user's chat socket; a push notification is sent when support writes. The app also shows support hours (Monday to Friday, 10 AM to 6 PM IST) and a delay note.
- **Console.** Operators use `/gen-dashboard/live-chat/*` and the `/ws/support-ops` socket (frames: `live_chat_message`, `live_chat_read`, `live_chat_status`, `live_chat_message_deleted`). The notifier (`js/live-chat-notify.js`) counts unread customer messages (`sender_type = 'org'` with no `read_at`) through `GET /gen-dashboard/live-chat/unread-count`, fills the badge, the bell row "Live chat" and the floating chat button, and plays the chime only for `org` frames.
- **Support can start a thread.** An operator's Chat icon on the console Organizations page opens the chat for any organisation; the first support message creates the thread (a deleted organisation is refused with 404, one without an active admin with 409), and the admin gets a push. The old rule that support never starts a conversation is gone, so a thread can begin with a `support` message.
- **Two traps for a new sender type.** The app maps every sender type other than `support` to `org` (`src/api/liveChat.ts`), so an assistant reply would look like the customer's own message. The console treats every unread `org` message as work for a human, so AI-answered chats would inflate the badge.

## Target flow (proposal)

1. The customer's message is stored exactly as today.
2. After the commit, a background job (never inside the request) decides whether the assistant may answer: skip if the thread is handled by a human (this includes a thread an operator started with the first message: create it with `handled_by = 'human'` so the assistant never auto-answers there), the kill switch is off, the message has an attachment (phase 1), or the message matches a hand-off trigger.
3. **Retrieve** from a Bedrock Knowledge Base whose data source is this repo's `kb/faq` and `kb/guides` only. Filter by metadata (`audience` admin or all). The files in `kb/playbook` are the assistant's instructions, not retrieved content.
4. **Answer** with citations (article title and in-app path), following the playbook.
5. **Decide.** Hand over when: nothing relevant was retrieved, a retrieved article has `handoff: required`, the customer asked for a person, the same problem was reported twice, a guardrail intervened, or the grounding check failed. Otherwise answer.
6. Store the reply as a new `sender_type = 'assistant'` ("Clockyn Assistant") with its citations and a run id, and publish it on the same frames as support messages.
7. **Hand-off.** Set `needs_human` on the thread with a category and a summary, then notify the console through the existing badge, bell, socket and chime. The human sees the AI transcript and the details collected.
8. When a human replies, the assistant pauses on that thread until it is closed.

## Backend changes (proposal)

- **Schema.** Allow `assistant` in the sender check. Thread columns: `handled_by` (`assistant` or `human`), `needs_human`, `needs_human_at`, `handoff_category`, `handoff_summary`. New tables `assistant_run` (thread, retrieved article ids, model id, latency, token counts, decision, guardrail result) and `assistant_feedback` (message, rating).
- **Unread rules.** Customer messages the assistant answered are marked read by the assistant. The console unread count and badge then mean "needs a human"; the unread-count response gains `needs_human_threads`.
- **Assistant module.** A service that calls Bedrock with timeouts, retries and a circuit breaker. If it fails or is slow, post "A support team member will reply to you here." and set `needs_human`.
- **Guardrails.** Use Bedrock Guardrails: sensitive-information filters for OTPs, card numbers and similar in input and output, denied topics (refund promises, legal and tax advice, other companies' data), and the contextual grounding check on answers. Also strip six-digit codes in code before any model call.
- **Limits and cost.** Cap characters sent per message, output tokens, AI replies per organisation per day and per month, and alarm on spend. No attachments go to a model in phase 1.
- **Operations.** A kill switch (environment flag), a per-organisation allow list for the rollout, and metrics for latency, hand-off rate and guardrail hits.

## App changes (proposal)

- Map `assistant` to its own type first, before any backend emits it.
- Show assistant bubbles with the name "Clockyn Assistant" and an "AI" label, the source article titles, and thumbs up and down.
- Add a "Talk to a human" action that sets `needs_human` without free text.
- Keep the support hours banner; during a hand-off outside hours, use the playbook's after-hours behaviour once the owner defines it.

## Console changes (proposal)

- A "Needs a human" filter and queue in the thread list; the bell row "Chats waiting for a human" and the badge count `needs_human_threads`.
- A hand-off card at the top of the thread: category, summary, details collected.
- The AI transcript shown with a distinct style, the cited articles, and a **Take over** button that sets `handled_by = human`.
- The notifier reacts to a new `live_chat_handoff` frame, not to every customer message.

## Data and privacy (proposal)

- Send the model only the customer's message text, the last few messages of the thread and the plan name. Do not send names, e-mails, phone numbers or staff data.
- The knowledge base holds no customer data. Log retrieved article ids and decisions; decide the retention period of message text in `assistant_run`.
- Tell customers they are chatting with an assistant. Have the privacy policy and data protection obligations reviewed before launch.

## Evaluation

`eval/questions.jsonl` has 150 questions with expected articles and the expected hand-off level (30 of them are hand-off cases).

- **Retrieval hit rate:** at least one expected article in the top 3 results. Runs without generation cost, so it can gate every change to `kb/`.
- **Hand-off precision and recall:** treat `required` as the positive class. Target (proposal): recall of 100% on `required` questions before any auto-answer.
- **Groundedness:** a support person reviews 30 sampled answers per release for invented facts, prices or promises.
- Re-run the set when the model, the guardrail or any article changes.

## Rollout (proposal)

1. **Prepare:** ingest the KB, run the evaluation, fix gaps.
2. **Shadow:** the assistant runs on every customer message and writes only to `assistant_run`. Humans answer as today; compare.
3. **Assist:** the draft appears in the console for the operator to edit and send as `support`.
4. **Auto-answer** for articles with `handoff: none`, for allow-listed organisations, then everyone. Keep the kill switch.

## AWS facts (checked 2026-11-15; re-check at build time)

- The S3 data source supports a sidecar `.metadata.json` file with a `metadataAttributes` object next to each document ([metadata in a data source](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-metadata.html), [S3 data source](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-managed-ds-s3.html)).
- Metadata filters can be passed to Retrieve and RetrieveAndGenerate ([AWS blog](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-knowledge-bases-now-supports-metadata-filtering-to-improve-retrieval-accuracy)). Ingestion runs through `StartIngestionJob` or the console Sync button.
- Guardrails offer sensitive-information filters, denied topics and a contextual grounding check that applies to outputs only; `ApplyGuardrail` works without calling a model ([guide](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html)).
- Aurora PostgreSQL can be the vector store (pgvector), not a document source ([Aurora guide](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.VectorDB.html)).
- **To verify:** which models and embeddings are available in Asia Pacific (Mumbai) and whether inference can run outside India ([supported regions](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-supported.html)); the exact filter syntax and API parameters; whether to use RetrieveAndGenerate or Retrieve plus the Converse API.

## Open decisions

1. What the assistant does outside support hours.
2. Refund, data export, account security and GST statements the assistant may make (see `OPEN_QUESTIONS.md`).
3. Where the knowledge base is published for citation links.
4. Model, region and data residency; monthly budget.
5. Whether replies in Hindi or Hinglish are needed.
6. Retention of AI transcripts; who reviews them.
7. Who owns the knowledge base and how fast edits made in the console FAQ reach it.
