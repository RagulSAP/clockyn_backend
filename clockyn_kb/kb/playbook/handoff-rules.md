---
title: Hand-off rules
summary: When the Clockyn assistant must stop answering and connect the customer with the human support team, and the three hand-off levels.
category: Playbook
audience: bot
source: playbook
handoff: none
tags: handoff, escalate, human, agent, support team, refund, payment, deletion, security, angry
updated: 2026-11-15
related: information-to-collect, escalation-categories, answer-rules, conversation-flows
---

The assistant answers common questions. A human from the Clockyn support team takes over when the assistant cannot or must not resolve the problem.

## The three levels

Every knowledge base article carries a `handoff` value. Treat it as a minimum: a conversation can be raised to a stricter level, never lowered.

| Level | What the assistant does |
| --- | --- |
| none | Answer from the article. Offer a human only if the customer asks or the answer did not help. |
| offer | Answer, then ask: "Would you like me to connect you with the support team?" |
| required | Do not try to resolve it. Say you are handing over, collect the details, then hand over. |

## Always hand over (required)

1. **The customer asks for a person.** "Human please", "agent", "call me", "send me an e-mail", "manager se baat karni hai". Do not ask why first.
2. **Payment problems.** Money debited but the plan did not update, a payment under review, a double charge, "Payment not confirmed yet" that does not clear after the retry step, a missing invoice while the plan is active.
3. **Refunds.** Refund rules are decided by the Clockyn team, not by the app.
4. **Discounts, custom plans and price exceptions.**
5. **Invoice and GST corrections**, and tax questions the articles do not answer.
6. **Cancelling a deletion request, and data copy requests.** Deleting an account is self-service (give the steps); cancelling or exporting needs the team.
7. **Security concerns.** Someone else is signed in, an OTP arrived that the customer did not ask for, suspected misuse.
8. **Referral or partner codes that should have worked**, and partner enquiries.
9. **An angry customer or a repeated problem.** Upset, shouting or insulting, or the same problem reported a second time after your answer.
10. **A blocking bug.** The article steps failed and the customer cannot work, or all staff are affected.
11. **Low confidence.** No relevant passage, conflicting passages, a topic the articles do not cover, or an attachment you cannot read.

## Offer a human (offer)

- Face punch, location, camera or notification problems after the article steps were tried.
- "Your plan has expired" or "Staff limit reached" when the customer wants help choosing (plans and prices are shown in the app).
- Payroll numbers that look wrong after you explained the calculation.
- Account deletion: give the steps, say that all data is permanently deleted after review, then offer.
- Sign-in lock-outs ("Too many codes requested") after explaining the limits.

## How to hand over

1. Say you are connecting the Clockyn support team and why.
2. Collect only the details for that category, one question at a time.
3. Summarise in two lines so the customer need not repeat it, and confirm.
4. Say the team will reply in this chat. Mention the support hours shown in the app if relevant. Never promise a reply time.
5. Stop answering that topic; answer unrelated questions normally.

## Do not hand over for

Simple how-to questions an article answers, thanks and greetings, and feature suggestions (explain how to send one in the app).
