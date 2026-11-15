---
title: Escalation categories
summary: A table of hand-off categories matched to the FAQ buckets, with examples, priority, what the support team needs and what the assistant says while the customer waits.
category: Playbook
audience: bot
source: playbook
handoff: none
tags: escalation, categories, priority, handoff, payments, security, billing, support team
updated: 2026-11-15
related: handoff-rules, information-to-collect, conversation-flows
---

Use one category per hand-off. Names follow the FAQ buckets and categories, so every rule traces to the help articles. Priority tells the support team what to open first.

## Categories

| Category (FAQ bucket / category) | Example | Priority | Team needs | Assistant says while waiting |
| --- | --- | --- | --- | --- |
| Plans & Payments / Payments & Invoices: payment not applied | "Money deducted, plan still expired" | High | Amount, date and time, last digits of the transaction reference | "I have passed this to our support team. Please do not pay again." |
| Payments & Invoices: double charge or review | "Charged twice" | High | Both payment times and references | "I have shared both payments with the team. Please do not pay again." |
| Payments & Invoices: refund | "I want my money back" | High | Payment details, one-line reason | "Refund decisions are made by our team. I have shared your request with them." |
| Payments & Invoices: invoice or GST change | "Change the GSTIN on my invoice" | Normal | Invoice number, the change and the correct value | "I have sent the change to our team." |
| Plans & Billing: discount or custom plan | "Bigger discount for 80 staff" | Normal | Number of staff, plan and duration | "Offers are decided by our team. I have shared your request." |
| Refer & Earn: code problem or partner enquiry | "Promo reward was not applied", "I run a YouTube channel" | Normal | Code and exact message, or channel name, link and contact details | "I have passed your details to our team." |
| Account & Security: suspected misuse | "Someone else is logged in" | High | When noticed, what looked wrong; never a code | "I am passing this to our team now. Please do not share any code with anyone." |
| Account & Security: deletion cancel or data copy | "Cancel my deletion request" | High | What was requested and when | "I have passed your request to our team." |
| Account & Security: cannot sign in | "No OTP for two days" | Normal | Registered e-mail or mobile, exact error, what was tried | "I have shared the details with our team." |
| Attendance & Time, Help & Fixes: blocking problem | "Face punch fails for all staff" | Normal | Staff name or ID, branch, time, message, phone model, screenshot | "I have sent the details to our team." |
| Pay & Expenses: disputed numbers | "Salary still wrong after your steps" | Normal | Staff name or ID, month, expected and shown amounts | "I have passed the figures to our team." |
| Support & Feedback: asked for a person | "Human please" | Normal | Company, one-line summary | "Connecting you with our support team now." |
| Any: angry or repeated problem | "Third time, nothing works!" | High | Summary of the whole chat | "Sorry this is still not fixed. I am bringing in our support team." |

## Rules for the waiting message

- Mention only the support hours the app shows (Monday to Friday, 10 AM to 6 PM IST).
- Never promise a reply time or an outcome such as "you will get your money back".
- Say "I have passed this to the team", never "I will" about something only a human can do. Keep it to two or three sentences.
