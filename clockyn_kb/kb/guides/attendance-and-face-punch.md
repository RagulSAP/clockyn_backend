---
title: How attendance and face punch work
summary: How staff register their face and punch in and out, what the punch button states mean, the daily punch limit, and how you fix a wrong day.
category: Attendance & Time
audience: all
source: guide
app_path: Home > PUNCH IN
plan: all
handoff: offer
tags: attendance, punch in, punch out, check in, check out, clock in, face scan, face unlock, face registration, setup face, punch button, sessions, daily limit, mark attendance, edit attendance, haziri
updated: 2026-11-15
related: faq-attendance-and-face-punch, troubleshooting-punch-in, geofencing-and-locations, shifts-and-schedules, device-change-requests
---

Clockyn marks attendance with face verification. Every employee registers their face once, and after that they punch in and out from the Home screen with a quick face scan. The app also checks that the person is at their branch, unless they are marked as field staff. This guide explains the full day for staff, managers and the company admin.

## Who can do what

- **Staff and managers** register their own face, then punch in and punch out from their Home screen.
- **The company admin** can also register a face and punch, in the same way as staff.
- **The company admin** sees everyone's attendance and can correct a day with **Edit Attendance**.

## Register your face (first time only)

You do this once. Until it is done, the round button on the Home screen says **SETUP FACE** and the status shows **Setup Required**.

1. Open the Clockyn app and go to **Home**.
2. Tap **SETUP FACE**. Allow **Camera** and **Location** if the phone asks.
3. Tap the shutter. The app takes 4 photos one after the other. Follow the prompts: look straight, tilt your head slightly left, tilt slightly right, then look straight again.
4. On the screen **Does this look clear?** check the 4 thumbnails. Tap **Register Face** if they are sharp, or **Retake** to take all 4 again.
5. Wait for **Registering your face...**. You see **Face Registered**. Tap **Get Started**.

The first photo is also saved as your profile photo. If that background step fails, your face registration still works.

**Tip** Use a bright place, face the light, keep the phone steady at arm's length, and remove a mask or glasses that cause glare.

## Punch in and punch out

1. On **Home**, tap the big button. It shows **PUNCH IN** when you are not working.
2. The app checks your plan, your location permission, your position, and the branch area. Only then does the camera open.
3. Keep your face inside the oval and capture. When your face matches, you see **Punched In!** with the time.
4. To finish, tap the same button, now **PUNCH OUT**, and verify your face again. You see **Punched Out!** with the time.

One punch in followed by one punch out is one session. You cannot punch in twice in a row. If you are already punched in the app shows **PUNCH OUT** instead.

## What the punch button and chips mean

| Button | Status text | Meaning |
| --- | --- | --- |
| PUNCH IN | Not Punched In | You can start work. |
| PUNCH OUT | Currently Working | You are punched in now. |
| CHECKING... | Currently Working or Not Punched In | The app is loading your face and attendance. Wait a moment. |
| SETUP FACE | Setup Required | Your face is not registered yet. |
| LIMIT REACHED | Max Sessions Reached | You used all sessions allowed today. The button stays locked until tomorrow. |

The small chip on Home shows **SETUP**, **WORKING**, **COMPLETE** or **NOT IN**. **COMPLETE** means the day's work is done: a punch in and punch out exist, or the daily session limit is used.

## Daily punch sessions (admin setting)

Each company has a daily limit of punch sessions per employee. If you never changed it, the limit is **3**. The admin can set any whole number from **1 to 10**.

1. Open **Settings > Company Settings > View Company**.
2. Tap **Edit Company**.
3. In the **Attendance Settings** section, set **Max Daily Punch Sessions**.
4. Tap **Update Company**.

The new limit applies to all employees of that company, and **Edit Attendance** also follows it. After the limit is reached and the employee is punched out, the button shows **LIMIT REACHED**.

## Forgotten punch out

If a session stays open for more than 18 hours, Clockyn treats it as a forgotten punch out. At the employee's next punch in, the old session is closed automatically at the shift end time. If the employee has no shift it is closed 9 hours after the punch in. The day is flagged as auto-closed, and its hours are an estimate. The admin can set the real time in **Edit Attendance**, which removes the flag.

## Fix a wrong or missing punch (admin)

1. From the dashboard open **Staff** and tap the employee.
2. Open the **Day-Wise** tab.
3. Tap the punch in or punch out time of the date.
4. In **Edit Attendance** enter the hours and minutes and tap **Save Changes**.

You can also use this to mark someone present if they had no phone. A punch in or punch out time is required. A session cannot be created before the earlier sessions of that day exist, so close any open session first.

## See punches and photos

Open **Staff**, tap the employee, and use the **Calendar** tab for the month or **Day-Wise** for a list of punch in, punch out and hours. Tap a day to see **Punch In Photo** and **Punch Out Photo**. Attendance dates and times use Indian Standard Time (IST). A night session belongs to the date it started.

## Common questions

### Do I need to register my face again on a new phone?
No. Your face is checked by Clockyn, so you can punch from the phone you log in with. Whether you can log in on a new phone depends on the device rules, see [Device change requests](kb:device-change-requests).

### Why does the button say LIMIT REACHED?
You used all punch sessions allowed for today. The button unlocks tomorrow. The admin can raise the limit or correct the day with **Edit Attendance**.

### Can an employee punch in for a colleague?
No. The face must match the account owner, so punching for someone else fails.

### Does the admin get an alert when staff punch?
Yes. The admin gets a notification when a staff member or manager punches in or out. Notifications must be allowed on the admin's phone.

### Can the admin change a punch time later?
Yes, with **Edit Attendance** in the **Day-Wise** tab. The change follows the company's daily session limit.

### Which time zone is used?
Indian Standard Time. The date of a punch is its IST date.

## What can go wrong

- **Face not matching or camera messages:** see [Troubleshooting punch in](kb:troubleshooting-punch-in).
- **Outside Branch Area:** the phone is too far from the branch. See [Geofencing and locations](kb:geofencing-and-locations).
- **Feature Unavailable or "Your plan has expired":** face punch is part of the plan. Only the admin can fix this in **Settings > Your Plan & Validity**.
- **No active punch in found when punching out:** you are not punched in, you already punched out, or the last punch in is older than 18 hours. Ask the admin to correct the day.

## When to contact support

Contact Clockyn support through **Support > Live Chat** if face punch keeps failing after you follow the tips, or if a whole team cannot punch. Have ready your company name, the admin e-mail, the employee name, the exact message on screen, and a screenshot.
