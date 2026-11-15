---
title: Signing in, one-time codes and account problems
summary: How admins and staff sign in with a one-time code, what the common sign-in messages mean, device changes, logging out and requesting account deletion.
category: Set up & Account
audience: all
source: guide
app_path: Admin Portal / Employee Access
plan: all
handoff: offer
tags: login, sign in, otp, one time code, verification code, not receiving otp, invalid otp, logout, new phone, device change, account deleted, inactive account, delete account, password
updated: 2026-11-15
related: adding-and-managing-staff, getting-started-register-company, faq-account-and-security
---

Clockyn has no password. You sign in with a 6-digit one-time code (OTP) sent to you. This guide explains how that works, what the messages mean and how to fix common account problems.

## How to sign in

1. Open the app and choose **Admin Portal** (company owner or administrator) or **Employee Access** (managers and staff). Use the portal that matches the account.
2. Enter your e-mail address and tap **Get OTP**. Some set-ups sign in with a mobile number instead. The sign-in screen shows which one to enter.
3. Enter the 6-digit code and tap **Verify & Continue**.

Use **Change email** if you typed it wrongly.

## One-time code rules

- Each code is valid for **5 minutes**. Requesting a new code replaces the old one, so always use the latest.
- After **5 wrong tries** the code is locked until it expires.
- A new code cannot be requested straight away. Normally you wait about **30 seconds** between requests.
- You can request a limited number of codes per hour. The usual limit is 5 codes per hour.

## How long you stay signed in

After a successful sign-in the device is remembered for up to **90 days**, so you open the app straight in. You are asked for a code again after 90 days, after you log out, or when you sign in on a new device.

## Log out

- Admin: **Settings > Beyond > Logout**, then confirm **Logout**.
- Managers and staff: **Settings** tab > **Logout** button, then **Confirm Logout**.

Nothing is deleted. Logging out only signs this device out. Your company, staff and attendance data stay as they are.

## A staff member gets a new phone

When someone signs in on a new phone, the app says a different device was detected and offers **Request Device Change**.

1. They enter a **Reason for Device Change** (for example lost phone).
2. They tap **Submit Request**.

The admin or manager approves it under Home > Quick Actions > **Requests** > **Device Change**. After approval, the old phone is signed out and the person signs in again with an OTP on the new phone. Only one request can be pending at a time, and a person can send at most 3 requests in 24 hours.

Admin accounts can be signed in on several devices at once. For managers and staff, it depends on how your Clockyn is set up. If one device per person is enforced, a second phone is refused with "This account is active on another device. Request access from your admin."

## Request account deletion

1. Open **Settings > Beyond > More Options > Privacy Policy**.
2. Tap **Request Account Deletion**.
3. Confirm with **Submit Request**.

The request is sent for review. Once reviewed, all your data is permanently deleted. Right after you submit, the app signs you out. Nothing is removed until the request has been reviewed. The app has no cancel button for it. If you change your mind, message support in Support > Live Chat as soon as possible.

## Change the e-mail you sign in with

- Admin: Settings > Company Settings > View Company > Edit Company, then change the e-mail under Contact. The new e-mail must be verified with a one-time code before saving.
- Staff: the admin changes it in Staff > open the person > Edit. The new e-mail must not be used by another account.

## Common questions

### I did not get the OTP e-mail. What should I do?
Check the e-mail address (tap Change email to fix it) and look in spam or junk. Wait a little and request a new code with Get OTP. If you see "We could not send the email right now. Please try again in a moment", try again shortly.

### What does "Invalid or expired OTP" mean?
The code is wrong, older than 5 minutes, or replaced by a newer code. Tap Get OTP for a fresh one. After 5 wrong tries the code is locked until it expires.

### What does "Too many codes requested. Try again later." mean?
You asked for more codes than allowed within one hour. Wait a while and use the newest code you received.

### Why does it say "This is an employee account. Please use employee login portal"?
You chose Admin Portal but the e-mail belongs to a manager or staff member. Go back and choose Employee Access. The opposite message appears when an admin picks Employee Access.

### What does "Your account is inactive" mean?
That person was deactivated. The admin can switch them Active again. Reactivating takes a seat, so the staff limit must have room.

### What does "Your account has been deleted" mean?
The account was removed, for example after an approved account deletion request. The person cannot sign in with it any more. Please contact Clockyn support for details.

## What can go wrong

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| No code arrives | Wrong e-mail, spam folder, or a sending delay | Check the e-mail, then request a new code after the wait time |
| "Too many incorrect OTP attempts" | 5 wrong tries on one code | Wait for it to expire (5 minutes in total), then request a new code |
| "There is no user with this email address" | The staff member was never added or the e-mail differs | Admin checks the e-mail in Edit Staff |
| "This email address is linked to more than one account" | The same e-mail is used by more than one account | Give each person a unique e-mail. If you are the admin, contact support |
| "Email already registered" | That e-mail already has an admin account | Sign in with Admin Portal instead |

## When to contact support

Ask in Support > Live Chat if you are locked out for long, see a message not listed here, or want to stop an account deletion. Have these ready:

- Your company name and the e-mail you sign in with
- The exact message the app showed, with a screenshot if you can
- The approximate time you tried and what you already tried
