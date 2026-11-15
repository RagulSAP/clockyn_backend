---
title: Set up payslips, daily wages, hourly pay and late fines
summary: How to create and assign a monthly payslip, set up daily wage and hourly templates, add extra earnings and deductions, and charge a fine for late punch-ins.
category: Pay & Expenses
audience: admin
source: guide
app_path: Settings > Salary Structure
plan: all
handoff: none
tags: salary structure, payslip, template, basic salary, hra, house rent allowance, epf, professional tax, health insurance, deduction, extra earning, daily wages, hourly, day pay, late fine, penalty, fine, attendance rule
updated: 2026-11-15
related: faq-payroll-and-salary, payroll-and-salary, incentives
---

The Salary Structure screens are where you decide how each person is paid: a monthly payslip, a daily wage, or an hourly rate. Nothing changes in anyone's salary until you assign the template to staff. This guide covers the setup and the deductions. For how the final figure is worked out, see [how salary is calculated and paid](kb:payroll-and-salary).

## Create a monthly payslip

1. Open Settings > Salary Structure > Monthly Fixed.
2. Tap Add.
3. Enter the Payslip Name, Basic Salary and House Rent Allowance. All three are required.
4. Fill EPF, Professional Tax and Health Insurance if they apply. They are optional and count as zero when blank.
5. Use Add Earning or Add Deduction for any extra lines, for example Travel Allowance.
6. Tap Save Payslip. You will see "Payslip Added!".

An extra line counts only when you fill both its name and its amount. Money amounts can be up to 9,999,999.99 with at most two decimals. If a value is not accepted you will see a message such as "Must be a valid number".

## Assign a payslip to staff

1. Open Settings > Salary Structure > Monthly Fixed.
2. On the payslip card, tap the three-dot menu and choose Assign Staff.
3. Select the staff members and tap Assign. The button shows how many you picked, for example Assign 3 Staff.

The card then shows "View N Assigned Staff". A staff member can have only one monthly payslip. If you assign a second one, Clockyn says "Already assigned to" and the name of the current payslip. To move the person, open Assign Staff on the old payslip, tap Remove next to their name, then assign the new payslip.

## Edit or delete a payslip

To edit, open the three-dot menu on the payslip, choose Edit, change the values and tap Update Payslip. The new figures apply to everyone on that payslip for months that are not marked as paid yet. Months already marked as paid keep their saved figures.

Delete is offered only on the newest payslip in the list. Older payslips show Edit and Assign Staff only. Clockyn asks "Delete this template?" before it removes the newest one. If an older payslip needs a change, use Edit.

Every payslip needs its own name. "A template with this name already exists" means the name is taken, so add the grade or year to the name.

## Set up daily wages

1. Open Settings > Salary Structure > Daily Wages.
2. Tap Add and enter the amount under "If one day present".
3. Save. The template is named Day Pay 1, Day Pay 2 and so on.
4. Tap Assign Staff and pick the staff who are paid by the day.

The pay for the month is that amount multiplied by the days the person was present. Each amount can be used in only one Day Pay template, and assigning someone here moves them off their other Day Pay template.

## Set up hourly pay

1. Open Settings > Salary Structure > Hourly Calculation. The screen is titled Hourly Charges.
2. Tap Add and enter the amount under "If one hour present". The template is named Hour-Pay-1, Hour-Pay-2 and so on.
3. Tap Assign Staff and select the staff.

Hourly pay is that amount multiplied by the hours of the person's completed sessions in the month. A session is complete when it has both a punch-in and a punch-out.

## Deductions in a salary

Clockyn subtracts these from earnings: EPF, Professional Tax and Health Insurance from the payslip, advances dated in the month, the late fine, and any extra deduction lines. You can follow each one in Settings > View & Download Reports > Salary Reports > View Staff Salary.

## Charge a fine for late punch-ins

The Fine / Deduction menu under Salary Structure shows "Coming soon", but late fines already work through the Attendance Rule.

1. Open Settings > Attendance & Devices > Attendance Rule.
2. Set Late Punch-In Count (1 to 10) and Consider As (Half Day or Leave).
3. Tap Save Rule.

For every group of that many late punch-ins in a month, one penalty applies. A Half Day penalty counts as 0.5 day and a Leave penalty counts as 1 day. The fine is penalty days multiplied by Basic Salary, divided by the working days of the month. Working days exclude holidays and weekly offs.

Example: the rule is 3 late punch-ins = Half Day. A staff member is late 7 times, which gives 2 penalties, so 1 day. With Basic Salary ₹30,000 and 26 working days, the fine is 1 x 30,000 / 26 = ₹1,153.85. It shows under Fine (₹) in the salary.

No fine is charged if you have not saved an Attendance Rule, or if the person has no monthly payslip.

## Remove someone from a template

1. Open the template under Settings > Salary Structure (Daily Wages, Hourly Calculation, Incentive, Overtime or Bonus).
2. Tap Assign Staff on its card.
3. In the list of assigned staff, tap Remove beside the name.

The person stops receiving that amount from the next calculation of any month that is not marked as paid.

## Common questions

### Which payslip fields are compulsory?

Payslip Name, Basic Salary and House Rent Allowance. EPF, Professional Tax and Health Insurance are optional.

### Can one staff member be on two monthly payslips?

No. Remove them from the old payslip first, then assign the new one.

### Why can I not delete an old payslip?

Delete is only offered on the newest payslip. Use Edit for older ones.

### What is the highest amount I can enter?

Money amounts go up to 9,999,999.99 with at most two decimals. Percentages must be between 0 and 100.

### Why was no late fine charged?

Either no Attendance Rule is saved, the person has no monthly payslip, or they had fewer late punch-ins than the Late Punch-In Count.

### Does editing a payslip change salaries I have already paid?

No. Months already marked as paid keep their saved figures.

## What can go wrong

- **"Already assigned to ..." when assigning.** Cause: the person is on another payslip. Fix: remove them there first.
- **"A template with this name already exists".** Cause: duplicate name. Fix: choose a different Payslip Name.
- **A Day Pay or Hour-Pay amount is refused.** Cause: another template already uses that amount. Fix: edit the existing template or choose a different amount.
- **No payslip shows for a staff member.** Cause: none is assigned. Fix: assign one under Monthly Fixed.

## When to contact support

If a saved salary looks wrong or you need help with a template that cannot be changed, contact Clockyn support through Support > Live Chat. Have ready your company name, the admin e-mail, the payslip name, the staff member, the month and what you tried, with a screenshot.
