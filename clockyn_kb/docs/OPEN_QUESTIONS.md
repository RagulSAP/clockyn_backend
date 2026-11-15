# Open questions for the owner

Things the knowledge base must not guess. Each entry says what is unknown, where it comes up and who decides. Until an item is answered,
documents say "Please contact Clockyn support" instead of making a promise, and the assistant hands the customer to a person.

Format of an entry:

- [ ] **Short title** (documents or FAQ where it appears): what is unknown; who decides.

Collected 2026-11-15 from the agents that wrote the guides and the playbook. FAQ answers that disagree with the code are in `FAQ_FIXES.md`.

## Open

### Money and billing
- [ ] **Refund policy and timing** (faq-payments-and-invoices, faq-plans-and-billing, payments-and-invoices): no rules exist for refunds, double charges or "money debited but plan not updated"; no response-time promise. Owner + finance decide.
- [ ] **Downgrades and custom plans** (plans-and-pricing, buying-renewing-and-changing-plans): the app has no route for either; guides send people to support. Owner decides whether support may grant them.
- [ ] **Credit when changing plan** (buying-renewing-and-changing-plans): credit is 10 staff x current price x paid months whatever the company size. Intended, or should it use the real staff count? Owner decides.
- [ ] **Renewal can lower the staff limit** (buying-renewing-and-changing-plans): the server does not check that the chosen staff count covers active staff. Owner decides whether to add a check.
- [ ] **GST, tax and input-credit advice** (faq-payments-and-invoices): the assistant must not advise beyond the KB. Owner + finance decide what may be said.
- [ ] **Invoice numbering and corrections** (payments-and-invoices): who corrects an invoice and how. Owner + finance decide.
- [ ] **Partner programme terms** (partner-promo-codes): no published eligibility, commission terms or contact route beyond "message support". Owner decides.

### Support promises
- [ ] **Support hours and reply times** (contacting-support-and-live-chat, playbook/conversation-flows): the app shows Mon-Fri 10 AM-6 PM IST and "usually reply within 5 minutes". Promises or targets? No FAQ mentions them. What does the assistant do outside hours? Owner decides.
- [ ] **Who may start a chat with a customer, and when** (contacting-support-and-live-chat; console: start-a-chat-with-a-company): any operator can now open a conversation with any organisation from the Organizations page. Should this be limited to some operators, or to certain reasons? Owner decides.
- [ ] **Outreach policy for support-started chats** (console: start-a-chat-with-a-company): which messages are allowed (renewal reminders, onboarding help, payment follow-ups) and which are not (marketing, bulk), what the first message must say, and whether important outcomes must be logged in a ticket. Owner decides.
- [ ] **Quiet hours for push notifications** (contacting-support-and-live-chat): a message from support reaches the admin's phone immediately, at any hour; there are no quiet hours. Should messages sent outside the support hours (Mon-Fri 10 AM-6 PM IST) be held or flagged? Owner decides.
- [ ] **What support can do for a failing face or re-registration** (troubleshooting-punch-in, attendance-and-face-punch): no re-register path was found in the app; what can support actually do? Owner decides.

### Data and privacy
- [ ] **What account deletion removes and how long it takes** (data-privacy-and-account-deletion, faq-account-and-security): the result depends on the server setting ACCOUNT_DELETE_ANONYMISE; billing records are untouched; no timeframe for the review; the app has no cancel button for a request. Owner decides what customers are told.
- [ ] **Data copy / export requests** (data-privacy-and-account-deletion): no process defined. Owner decides.
- [ ] **Retention of punch photos and billing records** (data-privacy-and-account-deletion): no promise written. Owner decides.
- [ ] **What happens after a security report** ("someone else is logged in"): handled by a person; the steps and any account lock are undefined. Owner decides.
- [ ] **Staff deleting their own account** (data-privacy-and-account-deletion): the app deletes immediately with no admin review. Intended? Owner decides.

### Product behaviour customers will ask about
- [ ] **Single-device login** (device-change-requests): ENFORCE_SINGLE_DEVICE is a Clockyn-wide server setting, off by default. Is it on in production, and may a company ask for it? Owner decides.
- [ ] **Late-punch grace time** (holidays-and-attendance-rules): the backend supports it, the app has no setting. Should support be able to set it? Owner decides.
- [ ] **Default attendance rule** (holidays-and-attendance-rules): a rule of "3 late days = Half Day" is created automatically when a company is added. Intended, and should customers be told? Owner decides.
- [ ] **Undoing a salary marked paid by mistake** (payroll-and-salary): today only support can. Owner decides the process.
- [ ] **Where the KB is published** (playbook/answer-rules): citation links need a public URL per article. Owner decides.

## Decided
