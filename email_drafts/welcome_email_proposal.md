# Welcome e-mail: proposal for approval (nothing is implemented yet)

Preview: open `welcome_email_draft.html` in a browser (sample data: Asha, Sunrise Traders, trial ends 16 Dec 2026). It is built as table-based HTML with inline styles (works in Gmail, Outlook, Apple Mail), shrinks to phone width and has a dark-mode version.

## When it is sent
- Once per company admin, the first time the admin's Home screen loads after registration. The Home screen's first call is `GET /home/dashboard-status`; on the first successful call for an admin the backend claims the send and sends in the background. A send problem never slows down or breaks Home.
- Only the admin (role 1), to the e-mail they registered with. No e-mail, or an address that is clearly invalid: nothing is sent.
- Exactly once: the claim is one atomic database update on the admin's row (new columns `welcome_email_sent_at`, `welcome_email_attempts`, `welcome_email_last_attempt_at` on `users`), so two phones or two quick loads can never send two e-mails.
- If Amazon SES fails, the next Home load retries, at most 3 attempts at least 1 hour apart; then it stops.
- Everyone who is already registered when this ships is marked as done in the migration, so existing customers do not suddenly get a welcome e-mail.
- Switch: nothing is sent until `WELCOME_EMAIL_ENABLED=1` is set, so you can deploy first and turn it on when ready.

## What it says
Subject (pick one): 
1. `Welcome to Clockyn, Asha! Your 1-month free trial has started`
2. `Sunrise Traders is live on Clockyn: here is how to start`
3. `You are in, Asha. Let us set up Sunrise Traders`

Preview line: "Add your team, set up attendance and run payroll, all from one app. Your 1-month free trial has started."

Blocks, top to bottom: header and welcome with an Open Clockyn button; a small example card of a face-verified punch-in; trial at a glance (1 month, all features, 10 staff and managers; trial end date); 3 steps to get started; 8 feature cards (face attendance, geofenced locations, shifts/holidays/rules, leave and requests, payroll and payslips, expenses and advances, reports, chat and announcements) plus a line about incentives, CRM and leads, Refer & Earn; help block (FAQ, Video Hub, Live Chat); closing button with store links; sign-off; footer with why they got it.

## Where the values come from
| Value | Source |
|---|---|
| First name, company name | the admin's name and organisation at registration |
| Trial end date | the free trial record created at registration |
| "1 month", "10 staff" | the trial rules in the backend (TRIAL_STAFF_LIMIT, trial length) |
| Open Clockyn, Google Play, App Store links | `ANDROID_STORE_URL`, `IOS_STORE_URL`, `WEBSITE_URL` (a block whose link is not set is left out) |
| Support address, company name and address in the footer | `SUPPORT_EMAIL`, `SELLER_NAME`, `SELLER_ADDRESS` |
| Sender | `EMAIL_USER` through Amazon SES (same as OTP and invoice e-mails), shown as "Clockyn", Reply-To = `SUPPORT_EMAIL` when set |

All names are escaped, so a company called `<b>x</b>` cannot break or abuse the e-mail. A plain-text version is sent with the HTML one.

## Things I would like you to confirm (claims in the copy)
- "Takes about 5 minutes to set up" is a marketing estimate I could not verify in the product.
- "No buddy punching, no paper registers" is a benefit claim of face verification.
- The example punch-in card shows an invented person (Priya Sharma). It is an illustration; say if you want an "Example" label or no card.
- The e-mail does not mention prices or plan names (prices are placeholders and shown in the app).

## Not decided yet
- Logo: the header uses a text wordmark so it always displays. A real logo image needs a public URL; give me one (or I can add an `EMAIL_LOGO_URL` setting).
- Whether managers and staff should get a short welcome too (not in this proposal; admins only).
- Language: English only for now.

## Tests I will add when approved
Claim happens once (also under two parallel requests); not sent for managers or staff, a blank e-mail, or already-welcomed users; retry limits; the migration marks existing admins as done; the HTML escapes hostile company names and omits blocks with no link; the plain-text version matches; the preview script renders all three variants (desktop, phone, dark).

## Update: approved look, logo and template (implementation of the template part)
- Look: graphite and mint (the app's own palette), serif headlines, no emoji and no invented content (the example punch-in card was removed). The approved design reference stays `welcome_email_draft.html`.
- Template: `utils/welcome_email_template.py` -> `render_welcome_email(first_name, org_name, trial_end, staff_limit, trial_months)` returns subject, html, text, logo_cid, logo_bytes.
- Header logo, in this order: `EMAIL_LOGO_URL` (an https address you host; nothing is attached) > `static/<EMAIL_LOGO_FILE>` (default `email_logo.png`, attached inline as `cid:clockyn-logo`; the sender must attach `logo_bytes` as an inline image with that Content-ID) > the text wordmark with the mint dot. An http:// or malformed URL is ignored.
- Previews: `python scripts/preview_welcome_email.py` (sample links) and `--links-off` (nothing configured) write `welcome_email_preview*.html/.txt` here. The logo is embedded as a data: URI in the PREVIEW file only; the real HTML never contains one.
