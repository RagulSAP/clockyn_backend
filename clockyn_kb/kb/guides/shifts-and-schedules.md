---
title: Create shifts and assign staff to them
summary: How to create, edit and delete shifts (including night shifts), assign staff, and how a shift decides who is late or on half day.
category: Attendance & Time
audience: admin
source: guide
app_path: Settings > Attendance & Devices > Shift Setting
plan: all
handoff: none
tags: shift, shifts, shift timing, night shift, assign shift, shift setting, shift change, late, half day, working hours, duty time, roster, schedule
updated: 2026-11-15
related: faq-shifts-holidays-and-rules, holidays-and-attendance-rules, leave-and-requests, attendance-and-face-punch
---

A shift is a working time window, such as 9:00 AM to 6:00 PM. Clockyn compares each employee's first punch in with their shift start to decide if they are late, and uses the shift length to decide half days and overtime. This guide shows how the company admin creates shifts, assigns staff, and what happens when an employee has no shift.

## Create a shift

Who can do this: the company admin, and managers who have the **Shift Management** permission.

1. Open **Settings > Attendance & Devices > Shift Setting**. The screen is titled **Shift Settings**.
2. Tap **Add New Shift**.
3. Enter the **Shift Name**.
4. Pick the **Start Time** and **End Time**.
5. Tap **Save**.

The shift appears in the list with its duration, for example 9h. The app refuses a shift in these cases:

- The name is empty, or a start or end time is missing.
- Start and end time are the same: "Start time and end time cannot be the same".
- Another shift already has the same name: "Shift with this name already exists".
- Another shift already has the same start and end time: "Shift with this time already exists".
- The shift is longer than **18 hours**. The message shows the computed hours and asks you to check the times, especially AM or PM. A PM chosen instead of AM for the end time is the usual cause.

## Create a night shift

Set the start time in the evening and the end time in the morning, for example 9:00 PM to 6:00 AM. Clockyn understands that the end time is on the next day. A night session counts on the date it started, even if the punch out is after midnight. For a night shift, a punch in up to 12 hours after the start time counts as late, so arriving early at 8 PM for a 9 PM shift is not late.

## Assign staff to a shift

1. In **Shift Setting**, tap the three-dot menu of the shift and choose **Assign Staff**.
2. The sheet shows **ASSIGNED** staff and **AVAILABLE** staff. Select one or more people from the available list.
3. Tap **Assign N Staff** and confirm. You see "Staff assigned!".

An employee is on **one shift at a time**. Assigning moves them from their previous shift to this one. The available list includes staff who are on other shifts, and it lists staff and managers, not the admin.

## Remove someone from a shift

Open the shift's **Assign Staff** sheet and tap **Remove** next to the person in the **ASSIGNED** list. The app asks "Remove name from this shift?". After you confirm, the employee has no shift until you assign another.

## Edit or delete a shift

- **Edit:** open the shift's menu, choose **Edit**, change the name, start time or end time, and save. The same 18-hour limit applies. Staff stay on the shift.
- **Delete:** choose **Delete**. The app says the action cannot be undone and that staff on this shift will be unassigned. Afterwards assign those staff to another shift so late and half day rules keep working for them.

## What happens if an employee has no shift

Attendance still works, but there is no shift start to compare with, so the employee is never marked **Late**. For half day and overtime Clockyn uses a 9 hour working day. Assign a shift to every employee. To see who has none, open **Settings > View & Download Reports > Employee Shift / Timing**. People without a shift appear under **Unassigned**.

## How a shift affects the day

- **Late:** the first punch in of the day is later than the shift start time.
- **Half Day:** the employee worked less than half of the shift length and has no open session. If there is no shift, a 9 hour day is used.
- Repeated late days can turn into a half day or leave through the **Attendance Rule**, see [Holidays, weekly offs and the attendance rule](kb:holidays-and-attendance-rules).
- There is no grace time setting in the app. A punch in is late as soon as it is after the shift start.

## One-day shift change

An employee can ask for a different shift for one date with a **Shift Change Request**. When it is approved, the requested shift applies **only on that date**. Every other day the assigned shift applies. See [Leave and requests](kb:leave-and-requests).

## Common questions

### Can one employee have two shifts?
No. An employee has one shift at a time. Assigning a new shift replaces the old one.

### Can I create a shift that ends the next day?
Yes. Set the start in the evening and the end in the morning.

### Why does the app say my shift is too many hours?
A shift cannot be longer than 18 hours. Check the AM and PM of the start and end time.

### What happens to staff when I delete a shift?
They are unassigned and the app tells you how many. Assign them to another shift.

### Which Settings menu has shifts?
**Settings > Attendance & Devices > Shift Setting**. Holiday Management is under **Settings > Company Settings**.

### Can a manager manage shifts?
Yes, a manager with the **Shift Management** permission. Give it in **Managers** and tap **Save Permissions**.

## What can go wrong

- **Employee shows as never late:** they have no shift assigned. Assign one.
- **Shift cannot be saved:** the name or time is a duplicate, or the shift is over 18 hours. Read the message on screen.
- **Late marks look wrong for night staff:** check the shift start time and AM or PM.

## When to contact support

Contact Clockyn support through **Support > Live Chat** if late or half day marks are wrong even though the shift and punch times are correct. Have ready your company name, the admin e-mail, the employee name, the date, the shift name and times, and a screenshot of the day.
