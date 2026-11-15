-- ============================================================================================
-- Clockyn FAQ update for EXISTING databases: referral percent now comes from the database, partner promo codes,
-- plan durations with free months, and support starting a Live Chat conversation. Generated 2026-11-15 together with FAQ.sql
-- (same answer texts, word for word).
--
-- WHEN TO RUN: once, after the backend / console / app versions with these features are deployed.
-- Run it in the same schema as FAQ.sql (table clockyn_dev.support_faq); everything runs in one transaction.
--
-- !! Part 1 DELIBERATELY OVERWRITES the answer of 14 existing questions (matched by lower(question)) and sets
-- !! updated_at = now(). If you edited one of these answers in the FAQ Hub, your edit is replaced.
-- !! Questions you deleted are not brought back by part 1 (the UPDATE finds nothing).
-- Part 2 adds 9 new questions with the same INSERT ... WHERE NOT EXISTS pattern as FAQ.sql, so it is safe to run again:
-- running the whole file twice gives the same result (the updates set the same text, the inserts skip existing questions).
-- Sort orders: Plans & Billing rows 3141-3143 (the free numbers after 3140); the Refer & Earn rows use 3319, the value of the
-- last older Refer & Earn row (the app orders by sort_order, then id, so they follow it).
-- ============================================================================================

BEGIN;

-- ───── Part 1: updated answers ───────────────────────────────────────────────
UPDATE clockyn_dev.support_faq SET answer = 'Refer & Earn lets you invite other companies to Clockyn. You share your own referral code. When a new company enters it while registering, both companies get a discount on a plan: the company you referred gets the discount on its first plan, and you get the same discount on your next plan or renewal once they have paid for their first plan.
The discount percent is set by Clockyn and is shown on the Refer & Earn screen. The reward works on Prime and Elite plans only. Refer & Earn is available to the company admin.', updated_at = now()
WHERE lower(question) = lower('What is Refer & Earn?');

UPDATE clockyn_dev.support_faq SET answer = 'The new company types your code in the optional Referral code box on the register screen, when it creates its Clockyn account. When the code is right, the screen shows Referred by followed by your company name and the discount it will get.
The code can only be entered while registering. It cannot be added to an account that already exists, a company cannot use its own code, and a company can be referred only once. If the code is wrong, the register screen shows a message and asks for the correct code.', updated_at = now()
WHERE lower(question) = lower('How does the company I refer use my code?');

UPDATE clockyn_dev.support_faq SET answer = 'Each referral gives a discount to both companies. The percent is shown at the top of the Refer & Earn screen, and again on the Payment Summary when you apply the reward.
A reward keeps the percent it had when it was issued. If Clockyn changes the percent later, only new referrals get the new percent.
The company you referred can use its reward right away, on its first paid plan after the free trial.
Your reward shows as Pending until that company pays for its first plan. Then it changes to Available, and you can use it on your next plan or renewal. Rewards from a friend''s code do not expire.
The discount is taken off the staff charge before GST, after any plan credit.', updated_at = now()
WHERE lower(question) = lower('How much do I save, and when can I use my reward?');

UPDATE clockyn_dev.support_faq SET answer = '1. Open Settings > Your Plan & Validity and choose your plan (or renew), then continue to the Payment Summary.
2. Under the coupon box you will see a Referral reward card with its discount percent and the number of rewards you have available.
3. Tap Apply. The card turns green and the price breakdown shows Referral reward with its percent and your new Grand Total.
4. Tap Pay as usual. To change your mind before paying, tap Remove.
The reward is used up only when the payment goes through. On iPhone the app only shows your plan status, so do this on an Android phone or the web version.', updated_at = now()
WHERE lower(question) = lower('How do I use my referral reward when I pay?');

UPDATE clockyn_dev.support_faq SET answer = 'No. A purchase can use either a coupon or a referral reward, not both. This is true for a reward from a friend''s code and for a reward from a partner promo code. If you apply one while the other is applied, the first one is removed and the app tells you. Choose whichever saves you more.', updated_at = now()
WHERE lower(question) = lower('Can I use a referral reward and a coupon together?');

UPDATE clockyn_dev.support_faq SET answer = 'Yes. Every company you refer gives you one reward, and you can use one reward per purchase. If you referred 2 companies, you can use a reward on two separate purchases, for example on two renewals. Each reward is available once the company you referred has paid for its first plan, and it has the percent that applied when that company signed up.', updated_at = now()
WHERE lower(question) = lower('I referred more than one company. Can I use the reward more than once?');

UPDATE clockyn_dev.support_faq SET answer = '1. Open Settings > Your Plan & Validity.
2. Tap Plan Details. Under each plan (Core, Prime, Elite) scroll sideways to see its durations, for example 1 Month, 6 Months and 1 Year, and tap the duration you want. Longer durations show the free months you get.
3. On the Set Staff Count step choose how many staff you need.
4. Review the Payment Summary, add a coupon if you have one, and tap Continue to Pay.
5. Complete the payment in the payment window. Your plan becomes active as soon as the payment is confirmed.
If you are still in the free trial, tap Buy a Plan to start this flow.', updated_at = now()
WHERE lower(question) = lower('How do I buy a plan for the first time?');

UPDATE clockyn_dev.support_faq SET answer = 'The price is: number of staff x price per staff per month x the months you pay for. The price per staff per month is the same on every duration of a plan. Longer durations include free months: a 6-month plan with 1 free month is charged for 5 months, and a 12-month plan with 2 free months is charged for 10 months. The plan still runs for the full 6 or 12 months. If you have an unused current plan, a credit is deducted (shown as Plan Upgrade Credit), then any coupon or referral discount is deducted, and 18% GST is added on top. The Payment Summary screen shows every line (Staff Charges, Plan Upgrade Credit, Subtotal, GST and Grand Total) before you pay. Prices per staff and the free months of each duration are shown in Plan Details.', updated_at = now()
WHERE lower(question) = lower('How is the price of a plan calculated?');

UPDATE clockyn_dev.support_faq SET answer = '1. Open Settings > Your Plan & Validity.
2. Tap Renew Plan.
3. Choose your staff count, review the summary and tap Continue to Pay.
Renewing extends your current plan by 12 months at the same plan level. The price per staff per month stays the same, and a renewal keeps the same share of free months as your plan has, so it can be charged for fewer than 12 months (for example 10 of 12 months). The Payment Summary shows exactly how many. If your plan is still active, the new period starts after your current Valid Until date, so you lose no days. If it has already expired, the new period starts today.', updated_at = now()
WHERE lower(question) = lower('How do I renew my plan?');

UPDATE clockyn_dev.support_faq SET answer = 'You pay only for the additional staff, not the ones you already have. The charge is extra staff x price per staff per month x the full calendar months left in your plan (at least 1 month), plus 18% GST. Free months do not apply to extra staff: you pay the monthly price for every month left, even if your plan includes free months. The exact amount is shown on the Payment Summary before you pay.', updated_at = now()
WHERE lower(question) = lower('How much will adding extra staff cost?');

UPDATE clockyn_dev.support_faq SET answer = 'Open Settings > Your Plan & Validity > Plan Details, pick the plan and the duration you want and go through the checkout. If your current plan is still running, a credit for the unused part is applied automatically and shown as Plan Upgrade Credit in the Payment Summary. The credit is worked out from what your current plan cost, so its free months are not counted. For special cases, ask the team in Support > Live Chat.', updated_at = now()
WHERE lower(question) = lower('How do I switch to a different plan?');

UPDATE clockyn_dev.support_faq SET answer = 'The app does not let you move from your current plan to a lower plan on your own. It also does not let you pick the same plan with the same or a shorter duration than you have now, or a higher plan with a shorter duration. Choose a longer duration instead, or write to our team in Support > Live Chat and they will guide you.', updated_at = now()
WHERE lower(question) = lower('Why does the app say Downgrade Not Allowed?');

UPDATE clockyn_dev.support_faq SET answer = 'Live Chat lets you talk directly to the Clockyn support team from the Support tab. It is available to organisation admins. Messages from our team reach you as notifications and the Live Chat row shows an unread badge until you open it. Our team may also message you first, for example about your plan or a question you raised: the message appears in Support > Live Chat and you can reply there. It is the best place to ask about billing, refunds or special requests.', updated_at = now()
WHERE lower(question) = lower('What is Live Chat and who can use it?');

UPDATE clockyn_dev.support_faq SET answer = 'Yes. When our team replies in Live Chat, or sends you a message first, admins receive a notification and the Live Chat row in Support shows an unread badge. Live Chat is available only to organisation admins.', updated_at = now()
WHERE lower(question) = lower('Do Live Chat replies from the support team send a notification?');

-- ───── Part 2: new questions ─────────────────────────────────────────────────
-- Plans & Payments / Plans & Billing
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Which plan durations can I choose, and how do free months work?', 'Core, Prime and Elite each come in several durations, for example 1 Month, 6 Months and 1 Year. More durations may be added later. In Plan Details, scroll sideways under a plan to see every duration with its price and what you save.
The price per staff per month is the same on every duration. Longer durations include free months: for example a 6-month plan can be charged as 5 months and a 1-year plan as 10 months, and you still get the full time. Each duration card shows its offer, such as 1 month free, and the saving per staff.', 3141, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which plan durations can I choose, and how do free months work?'));

-- Plans & Payments / Plans & Billing
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'What happens to my credit when I move to a longer plan?', 'When you move to another plan or a longer duration while your current plan is still running, a credit is applied and shown as Plan Upgrade Credit on the Payment Summary. The credit is worked out from what your current plan cost, counting only the months that were charged, not its free months. It is deducted from the new plan''s staff charge before any coupon or referral discount and before GST.', 3142, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens to my credit when I move to a longer plan?'));

-- Plans & Payments / Plans & Billing
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Do extra staff get the free months?', 'No. When you add staff with Upgrade Plan, the extra staff pay the normal monthly price for every full calendar month left in your plan, even if your plan includes free months. Your Valid Until date does not change. The exact amount is shown on the Payment Summary before you pay.', 3143, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Do extra staff get the free months?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'What is a partner promo code and where do I enter it?', 'A partner promo code is a code from a Clockyn partner, for example a YouTube, Instagram or Facebook channel that recommends Clockyn. It looks like a friend''s referral code: 6 letters and digits.
Enter it in the optional Referral code box on the register screen when you create your company account. When the code is right, the screen shows Referred by, then the channel name and the discount, for example 15% off Prime and Elite.
The code can only be entered while registering. It cannot be added to a company that already exists. The partner never sees your company''s data.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is a partner promo code and where do I enter it?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'How much discount does a partner promo code give, and how long is it valid?', 'The discount percent is set for each code, so different partners can give different discounts. You see the percent on the register screen when you enter the code, and again on Settings > Refer & Earn and on the Payment Summary.
It works like a friend''s reward: once, on your first plan after the free trial or on a renewal, and on Prime and Elite plans only.
A code can have a validity period. If it does, your discount is valid for a set number of days after you sign up, and Settings > Refer & Earn shows Valid until with the date. After that date the reward shows as Expired and cannot be used. If no date is shown, the reward does not expire.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How much discount does a partner promo code give, and how long is it valid?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'Why does my referral or promo code say it is not valid?', 'The register screen shows This referral code is not valid when the code cannot be used. Check these:
1. A code has exactly 6 characters, letters and digits only, with no spaces. Capital and small letters both work.
2. Look-alike characters are easy to mix up, for example the digit 0 and the letter O, or the digit 1 and the letter I.
3. The code may have been switched off by the partner or by Clockyn.
4. The code can only be entered while registering, not after your company is created.
If the code is still refused, ask the person who gave it to you to confirm it, or write to us in Support > Live Chat.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does my referral or promo code say it is not valid?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'Why was my promo code reward not applied, or why does it show Expired?', 'A partner promo code reward is used on the Payment Summary like a friend''s reward. If you cannot apply it, check these:
1. It has expired. Settings > Refer & Earn shows Expired when the validity days after your sign-up have passed. An expired reward cannot be used.
2. You picked the Core plan. The reward works on Prime and Elite only.
3. You are adding staff with Upgrade Plan. The reward works on a first plan or a renewal only.
4. You already applied a coupon. Use either a coupon or a reward, not both.
5. The reward was already used on an earlier payment.
The reward card on the Payment Summary says which one applies. If your reward expired before you could buy and you think it should still work, write to us in Support > Live Chat with your code and your sign-up date.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why was my promo code reward not applied, or why does it show Expired?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'Can I use a friend''s referral code and a partner promo code together?', 'No. The Referral code box on the register screen takes one code, and a code cannot be added or changed after your company is created. Decide which code to use before you register.
Your own referral code is separate: after you register you can still share it, and you get a reward when a company you refer pays for its first plan.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I use a friend''s referral code and a partner promo code together?'));

-- Plans & Payments / Refer & Earn
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Refer & Earn', 'How can my business or channel become a Clockyn partner?', 'If you run a YouTube, Instagram or Facebook channel, or have another audience, and want to recommend Clockyn, write to us. Open the Support tab and send a message in Live Chat, or raise a ticket from Settings > Beyond > Support, with your channel name, its link and your contact details. Our team will explain the partner programme and, if you are accepted, give you your own code.
Every company that registers with your code gets a discount on Prime or Elite. We never share a company''s data with partners.', 3319, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can my business or channel become a Clockyn partner?'));

COMMIT;
