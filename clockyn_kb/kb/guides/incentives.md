---
title: Incentives, overtime and bonus
summary: How to set up Count Basis and Turnover Basis incentives, overtime pay and a monthly bonus, assign them to staff, and understand how each amount is calculated.
category: Pay & Expenses
audience: admin
source: guide
app_path: Settings > Salary Structure > Incentive / OT / Bonus
plan: all
handoff: none
tags: incentive, overtime, ot, bonus, count basis, turnover basis, extra pay, target, over time, assign staff, bonus percentage
updated: 2026-11-15
related: faq-payroll-and-salary, payroll-and-salary, salary-structure-and-deductions
---

Incentives, overtime and bonus are extra amounts added to a staff member's monthly earnings. You create a template, then assign staff to it. This guide explains each type and exactly how Clockyn calculates it. The amounts appear in View Staff Salary in the Count, Turnover, OT and Bonus columns.

All four types are under Settings > Salary Structure > Incentive / OT / Bonus. Incentive has two kinds (Count Basis and Turnover Basis); Overtime and Bonus have their own entries.

## Count Basis incentive

A fixed amount added every month.

1. Open Settings > Salary Structure > Incentive / OT / Bonus > Incentive > Count Basis.
2. Tap Add and fill Count Upto Basic Salary, Next Count and Each Incentive.
3. Save, then tap Assign Staff and choose who receives it.

Every month, the Each Incentive amount is added to the earnings of each assigned staff member. Only the Each Incentive amount is used in the salary. The other two fields are saved with the template but do not change the amount.

Example: Each Incentive is ₹500, so every assigned person gets ₹500 added in every month that is not yet marked as paid.

## Turnover Basis incentive

A percentage of the Basic Salary.

1. Open Settings > Salary Structure > Incentive / OT / Bonus > Incentive > Turnover Basis.
2. Tap Add and enter Turnover Upto Basic Salary and After Target Incentive (a percentage).
3. Save and use Assign Staff.

The incentive is Basic Salary multiplied by the After Target Incentive percentage, divided by 100. Only the percentage is used. Example: Basic Salary ₹30,000 and 2% gives ₹600. It shows in the Turnover (₹) column. The person needs a monthly payslip with a Basic Salary, otherwise the amount is zero.

## Overtime pay

Overtime pays an amount for every full block of extra time worked in the month.

1. Open Settings > Salary Structure > Incentive / OT / Bonus > Overtime.
2. Tap Add, enter the duration in hours and minutes (minutes 0 to 59) and the Amount.
3. Save and tap Assign Staff.

How it is calculated for a month: Clockyn adds up the hours of completed sessions on the days the person was present, then subtracts the shift hours for each of those days. What is left is the overtime. The pay is the number of full duration blocks multiplied by the template amount. Partial blocks are not paid.

Example: shift 9 hours, present 20 days, worked 190 hours. Extra time = 190 - 180 = 10 hours = 600 minutes. With a template of 30 minutes = ₹100, the pay is 20 blocks x ₹100 = ₹2,000.

Things to know:

- Assign a shift to every person who has an Overtime template. Without a shift, Clockyn cannot tell how long a normal day is, and the whole worked time can be treated as extra, so the amount may look far too high.
- A short day is balanced against longer days, because the calculation is for the whole month.
- A day where a punch-out was forgotten and Clockyn closed the session automatically is left out of overtime until the admin corrects the punch-out.
- Each duration can be used in only one Overtime template.

## Bonus

A percentage of Basic Salary, paid only in the month and year you choose.

1. Open Settings > Salary Structure > Incentive / OT / Bonus > Bonus.
2. Tap Add and enter the percentage (0 to 100), the Month and the Year.
3. Save and use Assign Staff. The dialog is titled Staff Details. If someone already has another bonus, Clockyn warns you before you select them.

Bonus is Basic Salary multiplied by the percentage, divided by 100. Example: 10% of ₹30,000 is ₹3,000 in the chosen month only.

## Removing someone from a template

Open the template, tap Assign Staff on its card, then tap Remove beside the name. The person stops receiving the amount from the next calculation of any month that is not yet marked as paid.

## Common questions

### Why do I get "This value already exists" when saving?

Each value must be different from the values in your other templates of the same kind. "This value already exists in Count 1" means another Count template already uses that number. Change the value or edit the existing template.

### Why is overtime not showing for a staff member?

Check that they are assigned to an Overtime template, they have a shift assigned, they worked more than their shift hours in total for the month, their sessions are closed, and the extra time fills at least one full block.

### Why is the bonus not in the salary?

The staff member must be assigned to the Bonus template, you must be looking at the same month and year the bonus was set for, and they must have a payslip with a Basic Salary.

### Are incentives shown in the Reports list?

The Incentives row under Salary Reports is greyed out and not available yet. See the amounts in View Staff Salary instead.

### What percentages are allowed?

Percentages must be between 0 and 100 with at most two decimals. Amounts can be up to 9,999,999.99.

### Does a Count Basis incentive depend on the number of sales?

No. Clockyn adds the Each Incentive amount as a fixed monthly amount for each assigned person.

## What can go wrong

- **Overtime is much higher than expected.** Cause: no shift is assigned, so every worked hour counts as extra. Fix: assign a shift to the staff member and check the figure in View Staff Salary again.
- **Overtime is missing for some days.** Cause: a session was closed automatically after a forgotten punch-out. Fix: the admin corrects the punch-out for that day.
- **A turnover or bonus amount is zero.** Cause: no payslip with a Basic Salary. Fix: assign a monthly payslip.
- **The bonus shows in one month only.** Cause: that is how it works. Fix: create a bonus for each month you want.

## When to contact support

If a figure still looks wrong after you checked the assignment and month, contact Clockyn support through Support > Live Chat. Have ready your company name, the admin e-mail, the staff member's name, the month and year, the template name and a screenshot of View Staff Salary.
