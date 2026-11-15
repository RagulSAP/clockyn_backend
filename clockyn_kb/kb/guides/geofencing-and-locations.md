---
title: Branch location, punch area and field staff tracking
summary: How the branch punch area works, how admins set a branch location and radius, how field staff punch from anywhere, and how to see where field staff are.
category: Attendance & Time
audience: admin
source: guide
app_path: Home > Branches, Home > Staff Location
plan: all
handoff: offer
tags: geofence, geofencing, location, gps, branch, radius, punch area, outside branch area, field staff, staff location, live location, tracking, map, work area, branch address
updated: 2026-11-15
related: faq-attendance-and-face-punch, attendance-and-face-punch, troubleshooting-punch-in, notifications-and-permissions
---

Clockyn checks where an employee is when they punch. Each branch has a GPS location and a punch radius. Employees must be inside that circle to punch in or out. Employees marked as **Field Staff** are the exception: they can punch from anywhere, and the app shares their location while they work. This guide is for the company admin who sets this up.

## How the punch area works

When a non-field employee taps **PUNCH IN** or **PUNCH OUT**, the app reads the phone's GPS position and compares it with the employee's branch. The camera opens only if the phone is inside the branch radius.

- If the phone is too far away, the app shows **Outside Branch Area** with the distance and the allowed distance. For example: "You are 250m away from your branch. Must be within 100m to punch."
- The default radius is **100 metres** when nothing else is set.
- If location permission is off the app shows **Location Required**. If the phone cannot find its position it shows **Location Error**.
- A branch with no saved GPS location skips the distance check. Branches created in the app always need a location, so this only applies to very old branches.

## Add a branch with a location and radius

Who can do this: the company admin, and managers who have the **Branch Management** permission.

1. On **Home**, in the **Branches** card, tap **Add Branch**. To change an existing one tap **View Branch**, then open the branch and choose edit.
2. Enter **Branch Name** and **Branch Address**. Both are required.
3. In **Location & radius**, tap **Tap to locate GPS** and place the pin on the map at the building entrance or the middle of the office.
4. Choose the radius: **100**, **250**, **500**, **750** or **1000** metres. Each branch card then shows a pill such as "100m radius".
5. Save. If you forget the location the app says **Please select GPS location**.

**Tip** Pick a radius that covers the whole office and the car park, but not the road outside. If many people at the office see **Outside Branch Area**, check the pin and enlarge the radius.

## Mark an employee as field staff

1. Open **Staff**, then add or edit the employee.
2. Switch on **Field Staff** ("Enable if this employee works in the field").
3. Save.

Field staff can punch in and out from any place. The branch distance check is skipped for them.

## How field staff location tracking works

For employees marked as **Field Staff**, the app starts sharing the location after they punch in and stops after they punch out. The location is sent about every 20 seconds while they work.

- On Android it can continue in the background. The phone must have location set to **Allow all the time** (the app asks for this under **Settings > Beyond > App Permissions**).
- On other devices the app should stay open for tracking to continue.
- Nothing is recorded before punch in or after punch out.

## See where your field staff are

Staff Location is available on the Prime and Elite plans. On the Core plan the **Staff Location** card on Home is locked.

1. On **Home**, tap **Staff Location** in the Quick Actions.
2. Choose the **Branch** and the **Date**.
3. Switch between **Map** and **Satellite** views.
4. Staff are split into **UPDATED WITHIN 15 MINS** and **UPDATED BEFORE 15 MINS**, so you can see who is sharing live and who has gone quiet.
5. To see one person's route, open **Staff**, tap the employee and use the **Location** tab.

## Common questions

### Can field staff punch from anywhere?
Yes. Once the **Field Staff** switch is on, the branch distance check is skipped for that employee.

### Why can I not see a staff member on the map?
Location is recorded only for field staff, and only between their punch in and punch out. If the person has not punched in, has already punched out, or location permission is off, nothing shows. Also check that you chose the right date and branch.

### What is the default radius?
100 metres. You can choose 100, 250, 500, 750 or 1000 metres for each branch.

### Do managers see Staff Location?
The Staff Location screen is an admin feature on the Home screen and depends on your plan. Ask support if a manager needs access.

### Why does the app ask for Allow all the time on Android?
So tracking for field staff can continue while the app is in the background. iPhones do not ask for this.

## What can go wrong

- **Many staff see Outside Branch Area at the office:** the branch pin is in the wrong place or the radius is too small. Edit the branch and set the pin and radius again.
- **Location Required:** location permission is off. Allow it for Clockyn in the phone settings or in **Settings > Beyond > App Permissions**.
- **Location Error:** the phone has no GPS fix. Turn location on, go near a window or outside, and try again.
- **Staff Location card is locked:** your plan does not include it. Check **Settings > Your Plan & Validity**.

## When to contact support

Contact Clockyn support through **Support > Live Chat** if punches are refused for people who are clearly at the branch after you reset the pin and radius. Have ready your company name, the admin e-mail, the branch name, the employee name, the exact message and a screenshot.
