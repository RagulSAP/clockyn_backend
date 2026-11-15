---
title: Punch in or punch out is not working
summary: A step-by-step decision tree for punch problems, covering face not recognised, outside the branch area, location or camera blocked, already punched in, limit reached, wrong time and a changed phone.
category: Help & Fixes
audience: all
source: guide
app_path: Home > PUNCH IN
plan: all
handoff: offer
tags: punch not working, cannot punch, face not recognised, face not matching, photo is not matching, outside branch area, location required, location error, camera not opening, camera blocked, already punched in, limit reached, wrong time, time zone, new phone, spoof detected, too dark, attendance problem, punch failed
updated: 2026-11-15
related: faq-troubleshooting, faq-attendance-and-face-punch, attendance-and-face-punch, geofencing-and-locations, notifications-and-permissions, device-change-requests
---

Use this guide when an employee says "I cannot punch". Start at the top, find the message or button the employee sees, and follow that branch. Each message below is the wording shown in the app. If you are the admin and several people have the same problem, start with the branch area and the plan, because those affect everyone.

## Step 1: What does the punch button say?

- **SETUP FACE:** the face is not registered yet. Tap it and register, see [Attendance and face punch](kb:attendance-and-face-punch).
- **LIMIT REACHED** (status **Max Sessions Reached**): all punch sessions allowed today are used. The button unlocks tomorrow. The admin can raise the limit in **Settings > Company Settings > View Company > Edit Company > Max Daily Punch Sessions** (1 to 10), or correct the day with **Edit Attendance**.
- **CHECKING...:** the app is loading. Wait a few seconds. If it never ends, close and reopen the app.
- **PUNCH IN** or **PUNCH OUT:** go to Step 2.

## Step 2: What message appears after tapping?

### "Feature Unavailable" or "Your plan has expired"
Face punch is part of the company plan. Only the admin can fix this: open **Settings > Your Plan & Validity**, renew, and then everyone can punch again.

### "Location Required"
Location permission is off. Allow Location for Clockyn in the phone settings or in **Settings > Beyond > App Permissions** (staff: their **App Permissions**).

### "Location Error" or "Unable to get your location"
The phone could not get a GPS position. Turn Location on, go near a window or outside, wait a few seconds, and tap again.

### "Outside Branch Area"
The phone is farther from the branch than the allowed distance, for example "You are 250m away from your branch. Must be within 100m to punch." Move closer to the office and try again. If staff who are at the office see this, the admin should check the branch pin and radius, see [Geofencing and locations](kb:geofencing-and-locations). Field staff are exempt.

### "Today is a holiday ... Attendance is not required."
The admin created a holiday with **Block Attendance** on. A new punch in is not allowed that day. Someone who is already punched in can still punch out.

### "Already punched in. Please punch out first."
The employee is already working. The button should show **PUNCH OUT**. Punch out first, then start a new session.

### "No active punch in found"
There is no open session to close. The employee has not punched in today, already punched out, or the last punch in is older than 18 hours and was treated as forgotten. Check the attendance calendar. The admin can correct the day with **Edit Attendance**.

## Step 3: Face problems

| Message | What to do |
| --- | --- |
| We couldn't see your face | Fill the oval with your face, look straight, remove anything covering it, and capture again. Wipe the camera lens. |
| It's too dark - move to a brighter place | Go near a window or a light and face it. |
| Hold the phone steady and try again | Rest your elbows and keep still. |
| Move closer - fill the oval with your face | Bring the phone closer. |
| Photo is not matching - please try again | The face did not match the registered one. Face a light, avoid strong light behind you, remove a mask or glasses that cause glare, hold the phone at eye level. Only the account owner can punch. |
| Spoof detected. Please use a real face. | A photo or a screen was shown. Use your real face. |
| More than one face detected | Only one person may be in the frame. Step away from others and from posters. |
| The face service is busy | Wait a few seconds and try again. Nothing is marked until the face is verified. |

After every second failed attempt in a row the app shows **Tips for a clear photo** with a **Try again** button. The tips: face a light source, remove a mask or glare-causing glasses, hold the phone at eye level at arm's length, fill the oval and keep still. A failed match is not sent to the admin, the employee simply tries again.

## Step 4: Camera and connection messages

- **Camera error - close and retry:** close the camera screen, open it again, and make sure no other app is using the camera.
- **Capture failed - tap to retry:** the photo could not be taken. Tap again.
- **Connection error - check your network:** the photo could not reach Clockyn. Check mobile data or Wi-Fi and try again.
- The camera does not open at all: **Camera** and **Location** must both be allowed, see [App permissions and notifications](kb:notifications-and-permissions).

## Step 5: Wrong time or date

Clockyn records every punch in Indian Standard Time (IST). The date of a punch is its IST date, and late and half day decisions use IST. A night session counts on the date it started. The time is recorded by Clockyn, not taken from the phone clock.

## Step 6: "You are logging in from a different device"

This message appears at login, not at punch time, when the account is tied to another phone. Staff tap **Request Access** to send a device change request, and the admin approves it. See [Device change requests](kb:device-change-requests).

## Common questions

### Why does face punch work for some staff and not others?
Usually lighting, a mask or glasses, or a face that was registered in poor light. Ask the employee to follow the tips. If it fails often, contact support through **Support > Live Chat**.

### Can the admin mark attendance if punching is impossible?
Yes. Use **Edit Attendance** in **Staff > employee > Day-Wise**.

### Can staff punch in from a different phone?
The face is checked by Clockyn, so the phone they log in with works. Logging in on a new phone may need a device change request.

### What if punches are marked outside working hours?
Check the shift and time rules in [Shifts](kb:shifts-and-schedules).

## What can go wrong

- **Everyone in one branch is outside the area:** the branch pin or radius is wrong.
- **Nobody can punch since this morning:** the plan or trial has expired.
- **One person is always refused:** their face registration may be poor. Contact support.

## When to contact support

If none of the steps help, contact Clockyn support through **Support > Live Chat** (the admin's Support tab). Have ready your company name, the admin e-mail, the employee name, the exact message shown, the time it happened, and a screenshot.
