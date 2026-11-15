---
title: Reports, exports and the attendance dashboard
summary: How to read the Home dashboard, generate attendance, late, overtime and employee reports, download them as PDF, Excel or CSV, and find saved reports.
category: Attendance & Time
audience: admin
source: guide
app_path: Settings > View & Download Reports; Home > View Reports
plan: all
handoff: none
tags: reports, report, monthly report, attendance report, late report, overtime report, staff report, export, download, pdf, excel, csv, dashboard, staff overview, absent, late, present, analytics
updated: 2026-11-15
related: faq-reports-and-analytics, attendance-and-face-punch, shifts-and-schedules, holidays-and-attendance-rules
---

Clockyn gives the company admin a live attendance dashboard on Home and a set of downloadable reports. This guide explains what each number means, how to generate a report, and where your earlier downloads are saved.

## The Home dashboard: Staff Overview

**Staff Overview** shows today's numbers as rings you can swipe: **Total Staff**, **Absent**, **Late Punched-In**, **Punched-In**, **Working** and **Punched Out**. Each ring shows its share of total staff. The carousel moves by itself and stops when you touch it.

- **Punched-In** counts staff who punched in on the chosen date.
- **Working** counts staff who are punched in right now.
- **Punched Out** counts staff who finished and punched out.
- **Absent** counts staff without attendance.
- **Late Punched-In** counts staff whose first punch in was after the shift start.

Tap any figure to open the staff list already filtered. Tap a person to see the punch times and photos for that date.

## Check an earlier date or one branch

Use the filter bar on Home. Choose a **Branch** (or **ALL**), switch to the Department view if you want, and pick a **Date**. Future dates cannot be selected. If a past date has no punches the app shows "No attendance record for this date".

## Open the reports

Open **Settings > View & Download Reports**, or tap **View Reports** in the Home Quick Actions. The **Generate** tab lists the report types, and the **View Reports** tab shows the ones you generated before. Reports are grouped as **Attendance Reports**, **Salary Reports**, **Employee Reports** and **CRM**.

| Group | Available now | Greyed out (not available yet) |
| --- | --- | --- |
| Attendance Reports | Monthly Summary, Staff-wise Detailed Report, Late Report, Overtime Report | none |
| Salary Reports | Monthly Salary, View Staff Salary | Incentives, Loss of Pay, Reimbursement |
| Employee Reports | Employee Details, Employee Shift / Timing | Previous Employees, Employees Salary Structures |
| CRM | none | Meeting Report, Travel Report, Expense Report |

## Generate the monthly attendance report

1. Open the reports and choose **Monthly Summary**. The screen is titled **Monthly Report**.
2. Pick the month and year.
3. Choose **All Staff** or one employee.
4. Tap **Generate & View PDF**.

The PDF opens and is saved in **View Reports** for later.

## Other reports

- **Late Report:** lists each late day of the selected month with employee, branch, department, shift start, punch in time and **Late By**, for example 25 min or 1h 5m. If shift times are missing, those cells show a dash.
- **Overtime Report:** shows each employee's overtime and the amount earned for the month, based on the Overtime setting in Salary Structure. Employees without overtime appear in grey. Days with a forgotten punch out are left out until the admin corrects them.
- **Staff-wise Detailed Report:** one row per employee for the month with working days, present, absent, late, half day, leave, Sundays and hours worked, with branch and department.
- **Employee Details:** your employees with employee ID, department and branch. Tap an employee to see their profile report.
- **Employee Shift / Timing:** employees grouped by shift with the timing and duration. People without a shift appear under **Unassigned**.

## Download in another format

The **Export Report** sheet offers **Download as PDF**, **Download as DOC**, **Download as Excel** and **Download as CSV**. The monthly report is generated as a PDF. Employee and other reports open the format list.

## Find a report you generated earlier

Open the reports and tap the **View Reports** tab. Every report you generated appears with its type, so you can open it again. If nothing is there the app says **No reports yet** and tells you to use the **Generate** tab.

## Reports for managers

A manager sees reports only if the admin gave the **Attendance Reports** permission in **Managers**. The manager then finds a reports entry in their Settings with **Monthly Summary**, **Late Report**, **Monthly Salary** and **View Staff Salary**, limited to their staff. The other reports stay admin-only.

## See one employee's attendance calendar

Open **Staff**, tap the employee and use the **Calendar** tab to see each day coloured by status. Use **Day-Wise** for a list of punch in, punch out and hours. Tap a day for the details and photos, and change the month at the top.

## Common questions

### Why is my report empty?
Check that you picked the right month and that staff have attendance in it. Tap **Retry** if the screen shows an error.

### Can I get a report in Excel?
Yes. Use the **Export Report** sheet and choose **Download as Excel** or **Download as CSV**.

### Where did my downloaded report go?
In the **View Reports** tab of the reports screen.

### What is the difference between Punched-In and Working?
**Punched-In** counts everyone who punched in that day. **Working** counts only those punched in right now.

### Why can a manager not see a report?
The manager needs the **Attendance Reports** permission, and some reports are admin-only.

### Why are some report rows grey?
They are planned but not available yet, such as **Incentives** and **Loss of Pay**.

## What can go wrong

- **"Failed to generate PDF" or "Failed to download CSV":** check your internet connection and try again.
- **Late Report shows dashes for shift:** the employee has no shift assigned. See [Shifts](kb:shifts-and-schedules).
- **Overtime missing for a day:** that day has an auto-closed session. Correct the punch out in **Edit Attendance**.

## When to contact support

Contact Clockyn support through **Support > Live Chat** if a report keeps failing or the numbers do not match the punches you can see. Have ready your company name, the admin e-mail, the report name, the month, and a screenshot of the report.
