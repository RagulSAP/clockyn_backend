---
title: Holidays, weekly offs and the late punch-in rule
summary: How the admin adds holidays, blocks attendance on them, sets weekly off days, and sets the Attendance Rule that turns repeated late arrivals into a half day or leave.
category: Attendance & Time
audience: admin
source: guide
app_path: Settings > Company Settings > Holiday Management; Settings > Attendance & Devices > Attendance Rule
plan: all
handoff: none
tags: holiday, holidays, block attendance, weekly off, sunday off, off days, attendance rule, late punch, late penalty, half day rule, grace time, late count, deduction, public holiday
updated: 2026-11-15
related: faq-shifts-holidays-and-rules, shifts-and-schedules, attendance-and-face-punch, faq-payroll-and-salary
---

This guide covers three company-wide settings that decide how a day is counted: holidays, weekly off days, and the Attendance Rule for late punch ins. Only the company admin sets them, and managers can do so with the right permission.

## Add a holiday

Who can do this: the company admin, and managers who have the **Holiday Management** permission.

1. Open **Settings > Company Settings > Holiday Management**.
2. Tap one or more dates on the calendar. The **Selected Date** card lists them.
3. Enter the **Holiday Name**, for example Diwali.
4. Choose the **Branch** and **Department** it applies to, or leave **All Branches** and **All Departments** for the whole company.
5. Set **Block Attendance** on or off.
6. Tap **Add Holiday**.

The month list shows how many holidays the month has. The same day cannot be added twice for the same branch and department.

## What Block Attendance does

- **On** (the default for new holidays): staff in that branch and department cannot start a new punch in. The app shows "Today is a holiday (name). Attendance is not required."
- **Off**: staff can still punch in if they choose.
- Staff who are already punched in can still punch out.

## Holidays for one branch or department

Each employee gets the company-wide holidays plus the holidays that match their own branch and department. That is why two branches can have different holidays on the same day. Check the **Branch** and **Department** on each holiday if staff see the wrong days.

## Edit or delete a holiday

1. Open **Holiday Management** and select the month.
2. Tap the holiday in the list to open its menu.
3. **Edit Holiday** lets you change the **Holiday Name**, the selected dates of that month, and **Block Attendance**. To add more dates to the same holiday use the add dates option.
4. **Delete** asks **Delete Holiday?** before removing one date or all dates of that holiday. Deleting cannot be undone.

The branch and department of a holiday are chosen when you add it. To use a different branch or department, add a new holiday for it.

## Set weekly off days

1. In **Holiday Management** tap **Configure Off Days**.
2. Select the weekdays that are off for your company. Any days from Monday to Sunday can be chosen.
3. Tap **Save**.

If you never set it, Sunday is the weekly off. Weekly offs are not counted as absent.

## The Attendance Rule

The Attendance Rule decides what happens when an employee is late too many times.

1. Open **Settings > Attendance & Devices > Attendance Rule**.
2. Under **Half Day Rule** choose the **Late Punch-In Count** from 1 to 10.
3. Choose **Consider As**: **Half Day** or **Leave**.
4. Tap **Save Rule**. You see "Rule saved successfully".

A sentence under the fields summarises it, for example "If an employee is late 3 times, it will be considered as Half Day."

How the penalty works:

- Each full set of late days counts as one penalty unit. With **3** and **Half Day**, 3 late days make half a day and 6 late days make one day. With **Leave**, each set counts as a full day.
- The penalty goes into the salary as a deduction.
- A day is late when the first punch in is after the shift start. The employee needs a shift, see [Shifts](kb:shifts-and-schedules).
- The app has no grace time setting. A punch in is late as soon as it is after the shift start.

**Note** The Attendance Rule screen always shows a value (3 times, Half Day) even if you never saved one. A default rule with these values may already exist for your company. To be sure the rule you see is the rule that is used, set the values you want and tap **Save Rule** once.

## Common questions

### Can I give staff a grace time for late punch in?
Not in the app today. Ask support through **Support > Live Chat** if your company needs it.

### Does a late day always cost salary?
Only when the rule turns late days into a half day or leave. One late day on its own does not make a penalty unit when the count is more than 1.

### Can a holiday be for one department only?
Yes. Choose the **Branch** and **Department** when you add it.

### Why can staff still punch in on a holiday?
**Block Attendance** is off for that holiday. Turn it on in **Edit Holiday**.

### Where are weekly offs set?
In **Holiday Management** under **Configure Off Days**.

### Which manager permissions control these screens?
**Holiday Management**, **Shift Management** and **Company Settings**. Give them in **Managers** and tap **Save Permissions**.

## What can go wrong

- **A duplicate holiday is refused:** a holiday already exists on that date for the same branch and department. Edit the existing one.
- **Staff in one branch got the day off, another did not:** the holiday is set for a specific branch or department.
- **Salary deductions for late arrivals that you did not expect:** a default Attendance Rule may be active. Open **Attendance Rule**, check the values, and save the rule you want.

## When to contact support

Contact Clockyn support through **Support > Live Chat** if a holiday or weekly off does not show correctly in attendance after you checked the branch, department and dates. Have ready your company name, the admin e-mail, the holiday name and date, the branch and department, and a screenshot.
