-- ============================================================================================
-- Clockyn FAQ starter set for the Support > FAQ screen of the app and the FAQ Hub of the web console.
-- 379 questions in 6 buckets. Generated 2026-11-08.
--
-- Run AFTER the 'CREATE: FAQ' block of clockyn_ddl_latest.sql (table clockyn_dev.support_faq).
-- Safe to run more than once: a question that already exists (compared case-insensitively) is skipped, so FAQs you
-- edited or deleted in the FAQ Hub are not overwritten (a deleted one comes back, though, if you run this again).
-- Everything runs in one transaction: either all rows are loaded or none.
--
--   Set up & Account: Getting Started (15), Account & Security (26), Company Setup (25), Staff Management (32)
--   Attendance & Time: Attendance & Face Punch (45), Shifts, Holidays & Rules (21), Leave & Requests (15), Reports & Analytics (16)
--   Pay & Expenses: Payroll & Salary (39), Expenses & Advances (22)
--   Team & Customers: CRM & Leads (18), Chat & Announcements (18)
--   Plans & Payments: Plans & Billing (22), Payments & Invoices (17)
--   Help & Fixes: Notifications & Permissions (15), Troubleshooting (20), Support & Feedback (13)
-- ============================================================================================

BEGIN;

-- ───── Set up & Account ──────────────────────────────────────────────────────
-- Set up & Account / Getting Started
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do I register my company on Clockyn?', 'Open the app and choose Admin Portal. Enter your e-mail address, tap Get OTP and enter the 6-digit code from your inbox. If this e-mail has no account yet, the app takes you straight to Register Company.
1. Fill in Company Name, Address, State, District and Pincode.
2. Add at least one Branch (you can add departments too).
3. Verify your contact details with a one-time code.
4. Tap Register Company.', 10, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I register my company on Clockyn?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What information do I need to register my company?', 'Required: Company Name, Company Address, State, District, a 6-digit Pincode and at least one Branch. Your contact details are verified with a one-time code.
Optional: GST Number (15 characters) and, under Add More Details, PAN, Website, CIN, TAN, MSME Number and Business Category. Departments are optional too. Everything optional can be added later from Settings > Company Settings > View Company > Edit Company.', 20, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What information do I need to register my company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'Is there a free trial when I sign up?', 'Yes. A new company starts with a free trial of 1 month from the day you register. During the trial you can add up to 10 active managers and staff. The trial end date and your plan status are shown under Settings > Your Plan & Validity, and the Home screen warns you with a Free Trial Ending Soon message before it ends.
For prices and plan details, see Your Plan & Validity or ask in Support > Live Chat.', 30, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Is there a free trial when I sign up?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What should I set up first after registering?', 'A good order for a new company:
1. Check Settings > Company Settings > View Company and add your logo.
2. Add your branches with GPS location and radius (Home > Manage > Branches).
3. Add departments if you need more than the default ones.
4. Create shifts (Settings > Attendance & Devices > Shift Setting).
5. Add your staff (Home > Manage > Staff > Add Staff).
6. Set the Attendance Rule and holidays.
Staff can then sign in and punch with their face.', 40, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What should I set up first after registering?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What is the difference between Admin Portal and Employee Access?', 'Admin Portal is for the company owner or administrator: it opens the dashboard, staff, reports and settings. Employee Access is for managers and staff: it opens their own attendance, requests and personal records.
Use the portal that matches the account. If you pick the wrong one, the app tells you to use the other portal.', 50, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between Admin Portal and Employee Access?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What are the roles in Clockyn: admin, manager and staff?', 'Admin: owns the company and can use every screen, including plans, company settings and managers.
Manager: a staff member you promote. A manager sees only the Admin Settings shortcuts you have allowed through permissions, for the branches and departments you assigned.
Staff: punches attendance, sends requests and views their own records. Staff never see admin shortcuts.', 60, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What are the roles in Clockyn: admin, manager and staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What do the tabs at the bottom of the app do?', 'As an admin you see five tabs:
1. Home: today''s staff overview, filters and quick actions.
2. CRM: leads.
3. Chat: direct chats, groups and announcements.
4. Settings: company, salary, attendance, managers, expenses, appearance and more.
5. Support: FAQ, Live Chat and Video Hub.
Managers and staff see Home, CRM, Chat and Settings.', 70, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the tabs at the bottom of the app do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'What does the Home screen show?', 'Home shows Staff Overview for the selected date with these figures: Total Staff, Absent, Late Punched-In, Punched-In, Working and Punched Out. Above it are the Branch, Date and Department filters. Below are Manage cards (Staff, Branches, Departments) and Quick Actions (Staff Location, View Reports, Requests with a pending count).
If your plan or trial is ending, a reminder pop-up appears here too.', 80, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the Home screen show?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do I see who is absent or late today?', 'On the Home tab, tap the Absent or Late Punched-In figure in Staff Overview. The Staff list opens with only those people. The same works for Total Staff, Punched-In, Working and Punched Out. Change the Date, Branch or Department filter at the top of Home first if you want a different day or group.', 90, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see who is absent or late today?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do I filter Home by branch, department or date?', 'At the top of Home use the three filters:
1. Branch: ALL or one branch.
2. Date: pick the day to look at.
3. Department: narrow the figures to one department.
The Staff Overview figures and the staff lists you open from them follow these filters.', 100, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I filter Home by branch, department or date?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do I quickly add staff, a branch or a department from Home?', 'On Home, scroll to the Manage section. Each card (Staff, Branches, Departments) has a view button (View Staff, View Branch, View Dept) and an add button (Add Staff, Add Branch, Add Dept). The card also shows how many you have, for example staff on roll.', 110, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I quickly add staff, a branch or a department from Home?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'Can I use Clockyn on a computer or only on my phone?', 'Clockyn runs on Android, iOS and in a web browser. The screens adapt to the screen size, so on a larger screen some pages show two columns. Sign in the same way on every device with your e-mail and a one-time code. For face punch, the device needs a camera.', 120, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I use Clockyn on a computer or only on my phone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do I switch to dark mode?', 'Open Settings and find Appearance. Choose Light, Dark or System. System follows your phone or computer theme. Managers and staff have the same Appearance choice in their Settings tab.', 130, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I switch to dark mode?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'Where can I find all the settings?', 'Tap the Settings tab. The sections are: Your Plan & Validity, Company Settings, Salary Structure, Attendance & Devices, View & Download Reports, Managers, Expenses & Allowances, Appearance and Beyond. Beyond holds Suggest a Feature, Support, More Options, App Permissions and Logout.', 140, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I find all the settings?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Getting Started', 'How do my staff start using Clockyn?', '1. Add them in Home > Manage > Staff > Add Staff with a correct e-mail address.
2. Ask them to install the app and choose Employee Access.
3. They enter their e-mail, tap Get OTP and enter the code.
4. They allow Camera and Location when asked.
5. On the first punch they register their face with 4 photos.
Staff you have not added cannot sign in.', 150, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do my staff start using Clockyn?'));

-- Set up & Account / Account & Security
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How do I sign in to the admin app?', '1. Open the app and choose Admin Portal.
2. Enter your e-mail address and tap Get OTP.
3. Enter the 6-digit code from your e-mail and tap Verify & Continue.
Some set-ups sign in with a mobile number instead; the sign-in screen shows which one to enter. Use Change email if you typed it wrongly.', 160, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I sign in to the admin app?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'I did not get the OTP e-mail. What should I do?', '1. Check that the e-mail address is typed correctly (tap Change email to fix it).
2. Look in your spam or junk folder.
3. Wait a little and request a new code with Get OTP.
If you see "We could not send the email right now. Please try again in a moment", just try again shortly. Codes can only be requested a limited number of times per hour, so avoid tapping repeatedly. If it keeps failing, message Support > Live Chat.', 170, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I did not get the OTP e-mail. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How long is my OTP valid?', 'Each OTP is a 6-digit code that is valid for 5 minutes. After that you need to tap Get OTP again. Requesting a new code replaces the old one, so always use the latest code you received.', 180, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How long is my OTP valid?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What does "Too many incorrect OTP attempts" mean?', 'You entered a wrong code 5 times for the same OTP, so that code is locked. Wait for it to expire (it is valid for 5 minutes in total) and then request a new code. Until then the app will say "Please wait for the current OTP to expire before requesting a new one".', 190, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does "Too many incorrect OTP attempts" mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Why does it say "Please wait seconds before requesting another code"?', 'To protect your account, a new code cannot be requested straight after the previous one. Normally you must wait about 30 seconds between requests. The message shows the exact seconds left. Wait, then tap Get OTP again.', 200, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does it say "Please wait seconds before requesting another code"?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What does "Too many codes requested. Try again later." mean?', 'You asked for more codes than allowed within one hour; the usual limit is 5 codes per hour. Wait a while and try again, and use the newest code you received instead of requesting more. If you are locked out for long, contact Support > Live Chat.', 210, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does "Too many codes requested. Try again later." mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What does "Invalid or expired OTP" mean?', 'The code you entered is wrong, older than 5 minutes, or has been replaced by a newer code. Tap Get OTP to receive a fresh one and enter it carefully. After 5 wrong tries the code is locked until it expires.', 220, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does "Invalid or expired OTP" mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Do I have to enter an OTP every time I open the app?', 'No. After a successful sign-in the device is remembered for up to 90 days, so you open the app straight in. You are asked for a code again after 90 days, after you log out, or when you sign in on a new device.', 230, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Do I have to enter an OTP every time I open the app?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How do I log out of the app?', 'Admin: Settings > Beyond > Logout, then confirm Logout.
Managers and staff: Settings tab > Logout button, then Confirm Logout.
You will need a new one-time code to sign in again on that device.', 240, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I log out of the app?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What happens when I log out? Is my data deleted?', 'Nothing is deleted. Logging out only signs this device out and forgets its trusted-device status, so the next sign-in needs an OTP. Your company, staff and attendance data stay as they are.', 250, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens when I log out? Is my data deleted?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Can I be signed in on more than one phone?', 'Admin accounts can be signed in on several devices at the same time. For managers and staff it depends on how your Clockyn is configured: if one device per person is enforced, a second phone is refused with "This account is active on another device. Request access from your admin." The person then sends a device change request.', 260, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I be signed in on more than one phone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Why does it say "This is an employee account. Please use employee login portal"?', 'You chose Admin Portal but the e-mail belongs to a manager or staff member. Go back to the first screen and choose Employee Access. The opposite message, "This is an admin account. Please use admin login portal", appears when an admin picks Employee Access.', 270, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does it say "This is an employee account. Please use employee login portal"?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'A staff member sees "There is no user with this email address". Why?', 'Employee sign-in only works for people already added by an admin. Check that the e-mail they typed is exactly the one in their staff record (Staff > open the person > Edit). If it is wrong, correct it in Edit Staff. If the person was never added, use Add Staff first.', 280, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('A staff member sees "There is no user with this email address". Why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'A staff member sees "Your account is inactive". What should I do?', 'That person was deactivated. Open Staff, choose the Inactive filter, find them and switch Active on. The app then lets them sign in again. Note that reactivating counts as a new seat, so your staff limit must have room.', 290, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('A staff member sees "Your account is inactive". What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What does "Your account has been deleted" mean?', 'The account was removed, for example after a permanent delete or an approved account deletion request. The person cannot sign in with it any more. For a new start, an admin registers again or an admin adds the staff member again. For details, contact Support > Live Chat.', 300, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does "Your account has been deleted" mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What does "This email address is linked to more than one account" mean?', 'The same e-mail is used by more than one account, which is not allowed for sign-in. Ask your admin to give each person a unique e-mail. Admins can fix a staff e-mail in Edit Staff. If you are the admin, contact Support > Live Chat.', 310, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does "This email address is linked to more than one account" mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Registration says "Email already registered". What now?', 'That e-mail already has an admin account. Go back to the first screen, choose Admin Portal and sign in with it instead of registering. If you do not remember registering, use a different e-mail or contact Support > Live Chat.', 320, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Registration says "Email already registered". What now?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How does a staff member ask to use a new phone?', 'When they sign in on a new phone the app says a different device was detected and offers Request Device Change.
1. Enter a Reason for Device Change (for example lost phone).
2. Tap Submit Request.
Their admin or manager approves it under Requests > Device Change. After approval the person signs in again with an OTP on the new phone.', 330, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does a staff member ask to use a new phone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How do I approve a device change request?', 'Open Home > Quick Actions > Requests, then Device Change. Review the reason and approve or reject. On approval the old phone is signed out and the person can sign in on the new phone with an OTP. A rejection keeps things as they are.', 340, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I approve a device change request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How many device change requests can one person send?', 'Only one request can be pending at a time ("A device change request is already pending"), and a person can send at most 3 requests in 24 hours. After that the app says "Too many device change requests. Please try again later."', 350, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How many device change requests can one person send?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Where can I read the privacy policy?', 'Admin: Settings > Beyond > More Options > Privacy Policy. Managers and staff: Settings tab > More Options > Privacy Policy. The page opens inside the app. Tap I Understand, Go Back to return.', 360, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I read the privacy policy?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How do I delete my account?', '1. Open Settings > Beyond > More Options > Privacy Policy.
2. Tap Request Account Deletion.
3. Confirm with Submit Request.
The request is sent for review, and once reviewed all your data is permanently deleted. Right after you submit, the app signs you out.', 370, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I delete my account?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'What happens right after I request account deletion?', 'The app shows a confirmation, marks your session inactive and signs you out after a moment. If you send the request again while it is still waiting, you get "Deletion request already submitted. Awaiting admin review." Nothing is removed until the request has been reviewed.', 380, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens right after I request account deletion?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Can I cancel an account deletion request?', 'The app has no cancel button for it. If you changed your mind, send a message in Support > Live Chat as soon as possible.', 390, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I cancel an account deletion request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'Can I change the e-mail I use to sign in?', 'Admin: Settings > Company Settings > View Company > Edit Company, then change the e-mail under Contact. A changed e-mail must be verified with a one-time code before saving.
Staff: the admin changes it in Staff > open the person > Edit. The new e-mail must not be used by another account.', 400, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I change the e-mail I use to sign in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Account & Security', 'How can a staff member change their profile photo?', '1. Open the Settings tab > Profile Settings.
2. Tap Edit.
3. Tap the photo and choose Take Photo or Choose from Gallery.
4. Tap Save Changes.
Admins can also set a photo for a staff member in Edit Staff with Upload Photo or Change Photo.', 410, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can a staff member change their profile photo?'));

-- Set up & Account / Company Setup
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I view my company details?', 'Open Settings > Company Settings > View Company. You see your Contact Information (mobile, e-mail, website, address, state, district, pincode), Legal & Compliance details, and the number of Branches and Departments. The Edit Company button is at the bottom.', 420, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I view my company details?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I edit my company details?', '1. Open Settings > Company Settings > View Company.
2. Tap Edit Company.
3. Change what you need, such as name, address, GST or logo.
4. Tap Update Company.
You will see Company Updated! when it is saved.', 430, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I edit my company details?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I add another company?', 'Option 1: Settings > Company Settings > Add Company.
Option 2: on Home tap the switch company icon, then Add New Company.
Fill in the same details as at registration (name, address, state, district, pincode and at least one branch) and tap Add Company. You will see Company Added Successfully!', 440, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add another company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I switch between my companies?', 'On Home, tap the switch company icon (up and down arrows) next to the company name. The Switch Company list shows all your companies with the current one marked. Tap another company and the app reloads with that company''s staff, branches and settings.', 450, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I switch between my companies?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Can I run more than one company with one login?', 'Yes. Add more companies with Settings > Company Settings > Add Company and switch between them from Home. Each company has its own branches, departments, staff and shifts. The staff limit is checked for the company you are currently in.', 460, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I run more than one company with one login?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I delete a company?', '1. Switch to the company you want to remove.
2. Open Settings > Company Settings > View Company.
3. Tap Delete and confirm. The app warns that this action cannot be undone.
The Delete button only appears when you own more than one company. After deleting, the app moves you to another company.', 470, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I delete a company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Why can''t I delete my company?', 'You cannot delete your only company ("Cannot delete the only company"), so the Delete button is hidden on View Company until you have added another one. If you want your account removed completely, use Request Account Deletion under More Options.', 480, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I delete my company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What happens to my staff and branches if I delete a company?', 'Deleting a company also removes its staff accounts, branches, departments, shifts and additional details. This cannot be undone, so think twice. If you only want people to stop using the app, deactivate them instead of deleting the company.', 490, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens to my staff and branches if I delete a company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I upload or change my company logo?', '1. Open Settings > Company Settings > View Company > Edit Company.
2. In the Company section tap Choose image (or Change if you already have one).
3. Pick the picture and tap Update Company.
Use Remove to take the logo off. If the logo upload fails, your other changes are still saved and the app tells you to try again from Edit Company.', 500, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I upload or change my company logo?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Is the GST number mandatory?', 'No, GST Number is optional. If you enter one it must be 15 characters in the usual GST format, for example 22AAAAA0000A1Z5. You can add or change it later in Edit Company.', 510, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Is the GST number mandatory?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What are the Additional Details (PAN, CIN, TAN, MSME)?', 'They are optional business details. In Edit Company tap Add More Details to open PAN Number, Company Website, CIN Number, TAN Number, MSME Number and Business Category. They appear under Legal & Compliance in View Company. The app shows a format hint if a number looks wrong, but it does not stop you from saving.', 520, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What are the Additional Details (PAN, CIN, TAN, MSME)?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What format should PAN, CIN and TAN be in?', 'PAN: 10 characters, 5 letters, 4 digits, 1 letter (for example ABCDE1234F). TAN: 10 characters, 4 letters, 5 digits, 1 letter. CIN: 21 characters starting with L or U. Letters are converted to capitals as you type. A wrong format only shows a hint such as Invalid PAN format.', 530, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What format should PAN, CIN and TAN be in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What is Max Daily Punch Sessions?', 'It is how many times a staff member can punch in during one day. The default is 3, and you can set any number from 1 to 10. A session is one punch-in with its punch-out. You set it in Edit Company under Attendance Settings.', 540, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is Max Daily Punch Sessions?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I change the number of punch-ins allowed per day?', '1. Open Settings > Company Settings > View Company > Edit Company.
2. Scroll to Attendance Settings.
3. Change Max Daily Punch Sessions (1 to 10).
4. Tap Update Company.
It applies to everyone in that company.', 550, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change the number of punch-ins allowed per day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I change the company mobile number or e-mail?', 'Open Edit Company and change the number or e-mail under Contact. A changed e-mail must be verified with a one-time code before you can save (the app says Please verify new email address). If your set-up also uses a mobile number, that one is verified the same way.', 560, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change the company mobile number or e-mail?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I add a branch?', '1. On Home open Manage > Branches and tap Add Branch (or use Add Branch on the card).
2. Enter Branch Name and Branch Address.
3. Tap the GPS Location box to locate the branch on the map and Save Location.
4. Choose the Attendance Radius.
5. Tap Add Branch.
Name, address and GPS location are all required.', 570, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a branch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What is the attendance radius of a branch?', 'It is how close a staff member must be to the branch to punch in. Choose 100, 250, 500, 750 or 1000 metres; the default is 100 m. Outside it the app says how far away you are, for example "You are 250m away from your branch. Must be within 100m to punch." Field staff are not limited by it.', 580, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the attendance radius of a branch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I set or change a branch''s GPS location?', 'Open Home > Manage > Branches, tap the pencil on the branch, then tap the GPS Location box. Stand at the branch and let the app find your position, or move the pin on the map, then tap Save Location (Update Location if one is already set). Finish with Update Branch.', 590, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I set or change a branch''s GPS location?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Why does it say "Branch name already exists"?', 'Each branch in a company needs its own name. Pick a different name, for example add the city or area. Check the Branch list first, because the name may already be used by an existing branch.', 600, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does it say "Branch name already exists"?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I delete a branch?', '1. Open Home > Manage > Branches.
2. Tap the trash icon on the branch.
3. Confirm Delete Branch (this cannot be undone).
If staff are still assigned to it, the app first asks you to move them with Reassign Staff.', 610, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I delete a branch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Can I delete a branch that still has staff?', 'Not directly. A branch with staff cannot be deleted until they move. The app shows "N staff in this branch must be reassigned before deletion". Select the staff (or Select All), pick another branch and tap Reassign All & Delete Branch, or Reassign to move only some of them first.', 620, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I delete a branch that still has staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'How do I add a department?', '1. On Home open Manage > Departments and tap Add Dept.
2. Type the Department Name (for example Sales, HR, Engineering).
3. Tap Save Department.
You can also list departments while adding a company. Department names must be unique inside the company.', 630, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What happens to staff when I delete a department?', 'The department is removed and the people in it are left without a department. Edit each of them and choose a new Department Name. Confirm the Delete Department prompt carefully, as it cannot be undone.', 640, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens to staff when I delete a department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'Why does it say "Department name already exists"?', 'Department names must be unique in your company. Choose another name or edit the existing department instead. The same message appears when you rename a department to a name that another one already has.', 650, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does it say "Department name already exists"?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Company Setup', 'What is Assign Devices in Settings?', 'Settings > Attendance & Devices > Assign Devices opens the Add Devices screen with Add Fingerprint Device, Add Iris Device, Add Face Authentication Device and Add RF Tag Device. These are coming soon and only show a message for now. Staff punch today with face and location through the app.', 660, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is Assign Devices in Settings?'));

-- Set up & Account / Staff Management
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I add a new staff member?', '1. On Home open Manage > Staff and tap Add Staff.
2. Fill in Full Name, Employee ID, Phone Number, Email Address, Branch Name, Department Name and Gender.
3. Optionally pick a Shift and switch on Field Staff.
4. Add a photo or More details if you wish.
5. Tap Add Staff.
The staff member can then sign in with Employee Access.', 670, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a new staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Which fields are required when adding staff?', 'Required: Full Name, Employee ID, Phone Number, Branch Name, Department Name and Gender. Email Address is also required when your Clockyn signs in by e-mail, because staff need it to sign in. Everything under More details (date of birth, blood group, emergency contact, address and bank details) is optional.', 680, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which fields are required when adding staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How is the Employee ID chosen?', 'The app suggests the next free Employee ID when you open Add Staff, and you can type your own. Each ID must be unique in your company, otherwise you see "Employee ID already exists". Plain numbers are counted up automatically until an unused one is found.', 690, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is the Employee ID chosen?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Why does it say "Employee ID, mobile number, or email already exists"?', 'Another person already uses the same Employee ID, phone number or e-mail. Check the staff list (search by name) and change the value you typed. Each staff e-mail must be unique, because it is how they sign in; the app may also say "This email address is already used by another account".', 700, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does it say "Employee ID, mobile number, or email already exists"?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How many staff can I add?', 'While on the free trial or without an active plan, up to 10 active managers and staff. With a paid plan the limit is the one in your plan. The Add Staff screen shows it as "Staff: X / Y" with the slots remaining. The admin account itself is not counted.', 710, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How many staff can I add?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'It says "Staff limit reached". What can I do?', 'You have used all seats of your plan. Tap Upgrade on the red banner (on iOS it says View Status) or open Settings > Your Plan & Validity to change your plan. You can also free a seat by deactivating someone who has left. For plan options, ask in Support > Live Chat.', 720, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('It says "Staff limit reached". What can I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Do inactive staff count towards my staff limit?', 'No. Only active managers and staff are counted. Deactivating someone frees their seat. Switching them back to Active takes a seat again and is checked against the limit, so it fails with Staff limit reached if you are full.', 730, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Do inactive staff count towards my staff limit?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I find a staff member?', 'Open Home > Manage > View Staff. Type in the search box (Search by name, branch, dept...) and use the chips All, Staff, Manager or Inactive to narrow the list. Tap a person to open their Staff Details.', 740, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I find a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I edit a staff member''s details?', '1. Open the staff list and tap the person.
2. Tap Edit (on the card) or Profile > Edit Staff.
3. Change the fields.
4. Tap Update Staff.
If you leave with unsaved changes, the app asks whether to Save, Exit or Stay.', 750, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I edit a staff member''s details?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I change a staff member''s branch or department?', 'Open the person, tap Edit, change Branch Name or Department Name and tap Update Staff. To move many people at once when closing a branch, use the Reassign Staff step shown while deleting that branch.', 760, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change a staff member''s branch or department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I deactivate a staff member?', 'Open the person''s Staff Details and switch Active off, or use the switch in the staff list. Confirm Deactivate. The app says they will no longer be able to punch in, and you can reactivate them later.', 770, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I deactivate a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I reactivate a staff member?', 'Open Home > Manage > View Staff, choose the Inactive chip, find the person and switch Active on. The staff limit is checked again, so you need a free seat. After that they can sign in again.', 780, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I reactivate a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What is the difference between deactivating and deleting a staff member?', 'Deactivate is reversible: the person cannot punch in or sign in, and you can switch them back on. Permanent delete removes the person and all their records (attendance, salary, requests, chat and photos) and cannot be undone. Use deactivate for people who may return.', 790, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between deactivating and deleting a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I permanently delete a staff member?', 'Only the organization admin can do this.
1. Open the person''s Staff Details.
2. Tap the trash icon (Delete staff permanently).
3. Type the person''s name to confirm.
4. Tap Delete.
All their records are removed for good, so be sure.', 800, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I permanently delete a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Why can''t I delete a manager?', 'A manager who still has staff reporting to them cannot be deleted. The app says how many staff still report to them and asks you to reassign those staff to another manager first. Alternatively use Settings > Managers to remove the manager role, which turns them back into normal staff.', 810, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I delete a manager?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Who can permanently delete staff?', 'Only the organization admin (the owner account). Managers and staff do not see the delete option, and the admin account itself can never be deleted this way.', 820, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Who can permanently delete staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I make someone a manager?', '1. Open Settings > Managers and tap Assign Manager (or +).
2. Optionally filter by Branch and Department, then choose the Staff Member.
3. Under Assign To pick at least one branch and one department.
4. Tap Assign Manager.
Then set what they may do with the permissions button.', 830, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I make someone a manager?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What can a manager do? What are the manager permissions?', 'You choose per manager from 10 permissions: Staff Management, Branch Management, Attendance Reports, Salary Management, Holiday Management, Shift Management, Leads Management, Incentive Management, Company Settings and Requests Management. A manager sees a shortcut in Settings only for the permissions you switched on.', 840, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What can a manager do? What are the manager permissions?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I change a manager''s permissions?', '1. Open Settings > Managers.
2. Tap the shield icon on the manager.
3. Switch permissions on or off (Select All is available).
4. Tap Save Permissions.
The sheet shows how many permissions are active, for example 3/10.', 850, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change a manager''s permissions?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I remove a manager?', 'Open Settings > Managers, tap the trash icon on the manager and confirm Remove. The person becomes normal staff again and their branch and department assignments are removed. They stay in your staff list.', 860, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I remove a manager?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I see which branches and staff a manager looks after?', 'On Settings > Managers use View Branch on the manager''s card to see the assigned branches with their departments, and the people icon to see the staff under that manager. The top shows Total managers, Assigned and Assignments.', 870, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see which branches and staff a manager looks after?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Why does a manager need a branch and a department?', 'Managers only work within the branches and departments you assign. The Manager form asks for at least one of each ("A manager needs at least one branch and one department"). Pick every department to cover a whole branch. An Access Preview shows the combinations.', 880, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does a manager need a branch and a department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What is a field staff member?', 'Field staff work outside the office, for example sales or delivery. They can punch from anywhere, so the branch attendance radius does not apply to them. Switch on Field Staff in Add Staff or Edit Staff ("Enable if this employee works in the field").', 890, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is a field staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How do I assign a shift to a staff member?', 'In Add Staff or Edit Staff use the Shift field and pick a shift; its start and end time are shown. Need a new one? Tap + Add Shift to open Shift Setting. From a person''s Staff Details you can also tap Assign and choose Shift.', 900, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I assign a shift to a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What does the Assign button on the staff screen do?', 'Assign opens a sheet. Settings group: Manager and Shift. Salary group: Monthly Fixed, Daily Wages, Hourly Charges, Incentive, Overtime (OT) and Bonus. Tap one to open that setup screen.', 910, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the Assign button on the staff screen do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What can I see on a staff member''s detail screen?', 'Staff Details has the person''s card (Profile, Assign, Edit) and five tabs: Overview (details and month snapshot), Calendar, Day-Wise, Location and Advance. The Active switch is on Overview.', 920, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What can I see on a staff member''s detail screen?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How can I add bank details for a staff member?', 'In Add Staff or Edit Staff tap Add More Details and fill Bank Name, Bank Branch, Account Number and IFSC Code. Date of Birth, Blood Group, Emergency Contact Number and Address are there too. The saved values appear under Profile > Bank Details.', 930, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can I add bank details for a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How does a staff member register their face?', 'The first time a staff member punches, the app opens face registration.
1. Allow the camera.
2. Take 4 photos: look straight, tilt slightly left, tilt slightly right, look straight again.
3. Tap Register Face.
4. Tap Get Started when it says registered.
After that, punches are matched against this face.', 940, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does a staff member register their face?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Face registration keeps failing. What can I tell my staff?', 'Tell them to:
1. Face a light source and avoid strong light behind them.
2. Remove the mask, and glasses if they cause glare.
3. Hold the phone at eye level, at arm''s length.
4. Fill the oval with their face and keep still.
The app also says things like too dark, hold the phone steady or move closer. If it still fails, contact Support > Live Chat.', 950, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Face registration keeps failing. What can I tell my staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'Can a staff member register their face again?', 'The app has no Re-register button in Settings or on the staff screen. If a face is not recognised, first follow the photo tips (good light, no mask, steady phone). If it still does not work, message Support > Live Chat and the team can help.', 960, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can a staff member register their face again?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'What do managers see in their Settings tab?', 'Managers see an Admin Settings block with shortcuts only for the permissions you granted, for example Add Staff and View Staff, Add Branch and View Branch, Attendance Reports, Holiday Management or Requests Management. Below are General Settings: Profile Settings, App Permissions, Appearance and More Options. Staff see only the General Settings.', 970, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do managers see in their Settings tab?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Set up & Account', 'Staff Management', 'How can I upload a photo for a staff member?', 'In Add Staff or Edit Staff use Upload Photo (Change Photo once one exists) and pick a picture. Remove clears it. Tap the photo on the staff list to view it larger. Staff can also change their own photo under Settings > Profile Settings.', 980, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can I upload a photo for a staff member?'));

-- ───── Attendance & Time ─────────────────────────────────────────────────────
-- Attendance & Time / Attendance & Face Punch
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How does an employee punch in?', 'Employees punch in from the Home screen with face verification.
1. Open the Clockyn app and go to Home.
2. Tap the PUNCH IN button.
3. Allow Location and Camera access if the app asks.
4. Look at the camera, keep your face inside the oval and tap to capture.
After the face is verified you see ''Punched In!'' with the time. The app first checks that the employee is inside the branch area (field staff are exempt).', 990, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does an employee punch in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How does an employee punch out?', 'After punching in, the same big button on Home changes to PUNCH OUT and the status shows ''Currently Working''.
1. Tap PUNCH OUT.
2. Verify your face in the camera, the same way as for punch in.
You see ''Punched Out!'' with the time. The employee must be inside the branch area when punching out, unless they are field staff.', 1000, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does an employee punch out?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What do the punch button labels PUNCH IN, PUNCH OUT, CHECKING, SETUP FACE and LIMIT REACHED mean?', 'PUNCH IN: you are not punched in and can start work (status ''Not Punched In'').
PUNCH OUT: you are working right now (status ''Currently Working'').
CHECKING: the app is still loading your face registration or attendance, wait a moment.
SETUP FACE: your face is not registered yet (status ''Setup Required''), tap it to register.
LIMIT REACHED: you used all punch-in sessions allowed for today (status ''Max Sessions Reached''), the button is locked until tomorrow.', 1010, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the punch button labels PUNCH IN, PUNCH OUT, CHECKING, SETUP FACE and LIMIT REACHED mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What does LIMIT REACHED mean on the punch button?', 'LIMIT REACHED means the employee has already completed the maximum number of punch-in sessions allowed for today and is not punched in. The button is disabled until the next day. If it appears too early, the admin can raise the daily limit (see the question on daily punch sessions) or correct the day with Edit Attendance.', 1020, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does LIMIT REACHED mean on the punch button?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How many times can an employee punch in per day?', 'Each punch in followed by a punch out is one session. If your company never changed the setting, an employee can have up to 3 sessions per day. The admin can change this from 1 to 10 in the company form under Settings > Company Settings, field ''Max Daily Punch Sessions''. After the limit is reached and the employee is punched out, the button shows LIMIT REACHED.', 1030, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How many times can an employee punch in per day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How do I change the maximum daily punch sessions for my company?', '1. Open Settings > Company Settings.
2. Open the company form (Add Company).
3. Find ''Max Daily Punch Sessions'' and choose a value from 1 to 10.
4. Save.
The new limit applies to all employees of the company, and Edit Attendance by the admin also follows it. If you do not set one, 3 sessions per day are allowed.', 1040, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change the maximum daily punch sessions for my company?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Why does the app ask me to set up my face before I can punch?', 'Clockyn marks attendance only by face verification, so every employee registers their face once. When someone taps the button for the first time it shows SETUP FACE and asks for Location and Camera permission. After a successful registration they can punch in and out with their face. Admins who want to punch also register their face the same way.', 1050, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app ask me to set up my face before I can punch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How do I register my face?', '1. Tap SETUP FACE on the Home screen and allow Camera and Location access.
2. Take 4 photos as instructed: look straight, tilt your head slightly left, tilt slightly right, then look straight again.
3. Review each preview (''Does this look clear?'') and tap Register Face, or Retake if it is dark or blurry.
4. Wait for ''Registering your face...''.
You see ''Face Registered'' when it works. Tap Get Started and you can punch.', 1060, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I register my face?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What are the tips for good face photos during registration?', 'Use a well-lit place and face the light. Look straight at the camera and keep your face centred in the frame. Remove masks, caps and glasses that cause glare. Keep the phone steady at about arm''s length and take the photo when the preview looks sharp. Registration needs all 4 photos to show your face clearly, so retake any photo that looks dark, blurry or cropped.', 1070, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What are the tips for good face photos during registration?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Face registration failed with ''Face not visible in some photos''. What should I do?', 'This means one or more of the 4 photos did not clearly show your face, and Clockyn does not save a registration with a bad photo. Move to a brighter place, face the light, remove glare-causing glasses or a mask, and fill the oval with your face. Then register again and use Retake on any preview that does not look clear. You get up to 3 tries per photo.', 1080, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Face registration failed with ''Face not visible in some photos''. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Can an employee register or change their face again?', 'Face registration is a one-time setup that appears as SETUP FACE until it is done, and after that the button becomes PUNCH IN. If an employee''s face is often not recognised, first follow the tips for a clear photo. If it still fails, for example after a big change in appearance, ask Support > Live Chat what can be done.', 1090, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can an employee register or change their face again?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Does the face I register also become my profile photo?', 'Yes. When the registration succeeds, Clockyn also saves the first photo as the employee''s profile photo. This is done in the background, so if it fails for any reason the face registration itself still works and the employee can update their photo later.', 1100, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Does the face I register also become my profile photo?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'The app says ''Photo is not matching - please try again''. What does it mean?', 'The face in the camera did not match the face registered for this employee. Nothing is sent to the admin for review, and you simply try again. Tips: face a light source, avoid strong light behind you, remove mask or glare-causing glasses, hold the phone at eye level and fill the oval. Make sure the employee punching is the account owner, because punching for someone else will not work.', 1110, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says ''Photo is not matching - please try again''. What does it mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What does ''We couldn''t see your face'' mean while punching?', 'The camera could not find a face in the photo. Fill the oval with your face, look straight at the camera and make sure nothing covers it. If the message repeats, check the lighting and wipe the camera lens, then tap to capture again.', 1120, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does ''We couldn''t see your face'' mean while punching?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'The camera says it is too dark or too blurry. What should I do?', 'For ''It''s too dark - move to a brighter place'', go near a window or a light and face it, not the other way round. For ''Hold the phone steady and try again'', rest your elbows, keep still and capture again. If your face is too small the app says ''Move closer - fill the oval with your face''.', 1130, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The camera says it is too dark or too blurry. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'I keep failing face punch. Is there any help in the app?', 'Yes. After every second failed attempt in a row the app shows ''Tips for a clear photo'' with a ''Try again'' button. The tips are: face a light source and avoid strong back light, remove mask and glasses that cause glare, hold the phone at eye level at arm''s length, and fill the oval and keep still. If it still fails after that, contact Support > Live Chat.', 1140, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I keep failing face punch. Is there any help in the app?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What happens when someone holds a photo or video in front of the camera?', 'Clockyn checks that the face is a real, live person. A printed photo or a screen is refused with ''Spoof detected. Please use a real face.'' and attendance is not marked. The employee should try again with their real face in good light.', 1150, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens when someone holds a photo or video in front of the camera?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'I get ''More than one face detected'' when I punch. What should I do?', 'Only one person may be in the frame. Step away from others, make sure no posters or screens show faces behind you, and capture again. Attendance is marked only when exactly one face is found.', 1160, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I get ''More than one face detected'' when I punch. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Why does the app say I am outside the branch area?', 'For employees who are not field staff, Clockyn checks that the phone is inside the radius of the branch before punching. If you are too far, the app shows ''Outside Branch Area'' with the distance and the allowed distance, for example ''You are 250m away from your branch. Must be within 100m to punch.'' Move closer to the office, wait for a good GPS signal and try again. The default radius is 100 metres.', 1170, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say I am outside the branch area?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What is the punch radius of a branch?', 'It is the distance around the branch location within which employees can punch. If the branch has no special value set, Clockyn uses 100 metres. Employees outside the radius cannot punch in or out. If you see many ''Outside Branch Area'' messages for people who are at the office, check the branch location and radius or ask Support > Live Chat.', 1180, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the punch radius of a branch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What does ''Location Required'' or ''Location Error'' mean when I punch?', '''Location Required'' means Location permission is off. Clockyn needs your location to check that you are inside your branch, so enable Location for the app in your phone Settings. ''Location Error'' means the phone could not get your position, so check that GPS is on, step outside or near a window, and tap again.', 1190, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does ''Location Required'' or ''Location Error'' mean when I punch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Why does the app ask for Camera and Location permission?', 'The camera is used to verify your face. The location is used to confirm that you are within your branch area when punching. Allow both in the pop-up or in your phone Settings. You can review them in Settings > Beyond > App Permissions. Without them the punch cannot be completed.', 1200, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app ask for Camera and Location permission?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Can field staff punch from anywhere?', 'Yes. If an employee is marked as Field Staff, the branch distance check is skipped and they can punch in and out from any place. To mark someone, switch on ''Field Staff'' (Enable if this employee works in the field) in the staff form. Branches without a saved location also skip the distance check.', 1210, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can field staff punch from anywhere?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How does location tracking for field staff work?', 'For employees marked as Field Staff, the app starts sharing their location after they punch in and stops after they punch out. The location is sent about every 20 seconds while they work. On Android it can continue in the background, on other devices the app should stay open. The admin watches it under Staff Location.', 1220, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does location tracking for field staff work?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Where can I see where my field staff are?', 'Open Staff Location from the Home quick actions. Choose the Branch and Date, then use the Map or Satellite view. Staff are split into those who updated within the last 15 minutes and those whose last update is older than 15 minutes. You can also open a staff member''s details and use the Location tab for their route history.', 1230, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I see where my field staff are?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Why can''t I see location for a staff member?', 'Location is recorded only for staff marked as Field Staff, and only between their punch in and punch out. If the person has not punched in, has already punched out, or the app has no location permission, nothing is shown. Also check that you picked the right date and branch on the Staff Location screen.', 1240, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I see location for a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Can someone punch in on a holiday?', 'It depends on how the holiday was created. If the holiday has ''Block Attendance'' on, staff in that branch and department see ''Today is a holiday (name). Attendance is not required.'' and cannot start a new punch in. If it is off, staff can still punch in. Staff who are already working can still punch out.', 1250, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can someone punch in on a holiday?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'I punched in but forgot to punch out. What happens?', 'If a session stays open for more than 18 hours, Clockyn treats it as a forgotten punch out. At the employee''s next punch in, the old session is closed automatically at the shift end time (or 9 hours after the punch in when there is no shift) and flagged as auto-closed. The punch out button will not work on such an old session and says no active punch in was found. Admin can fix the exact time with Edit Attendance.', 1260, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I punched in but forgot to punch out. What happens?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What does it mean when attendance shows an auto-closed day?', 'It means the employee did not punch out and the system closed the session automatically. The hours are an estimate, so overtime for that day is skipped until it is reviewed. The admin can set the real punch out in Edit Attendance, which removes the auto-closed flag.', 1270, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does it mean when attendance shows an auto-closed day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Can an employee punch in twice without punching out?', 'No. If the employee is already punched in, the app shows PUNCH OUT instead. A direct attempt shows ''Already punched in. Please punch out first.'' The employee needs to punch out before starting a new session. Likewise punch out needs an open punch in.', 1280, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can an employee punch in twice without punching out?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'I can''t punch out and it says ''No active punch in found''. Why?', 'This appears when there is no open session to close. Typical reasons are that you have not punched in today, you already punched out, or your last punch in was more than 18 hours ago and was treated as forgotten. Check Home and your attendance calendar, and ask your admin to correct the day with Edit Attendance if needed.', 1290, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I can''t punch out and it says ''No active punch in found''. Why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What do the Home status chips SETUP, WORKING, COMPLETE and NOT IN mean?', 'SETUP: face registration is pending. WORKING: the employee is punched in. COMPLETE: the day''s work is done, with a punch in and punch out recorded or the daily session limit used. NOT IN: no punch in yet today. The chip changes immediately after each punch.', 1300, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the Home status chips SETUP, WORKING, COMPLETE and NOT IN mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'I get ''Feature Unavailable'' or ''Your plan has expired'' when punching. Why?', 'Face punching is part of the company plan. If the plan has expired or does not include it, the app shows ''Feature Unavailable''. Only the admin can fix this. Open Settings > Your Plan & Validity to check the status and renew, then the employees can punch again.', 1310, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I get ''Feature Unavailable'' or ''Your plan has expired'' when punching. Why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'The app shows ''Connection error'' or ''Capture failed'' while punching. What should I do?', '''Connection error - check your network'' means the photo could not reach the server, so check mobile data or Wi-Fi and try again. ''Capture failed - tap to retry'' means the photo could not be taken, so tap again. For ''Camera error - close and retry'', close the camera screen, open it again and make sure no other app is using the camera.', 1320, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app shows ''Connection error'' or ''Capture failed'' while punching. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What does ''The face service is busy'' mean?', 'The verification service had too many requests at that moment. Wait a few seconds and try again. Your attendance is not marked until the face is verified successfully.', 1330, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does ''The face service is busy'' mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Can an employee punch in from a different phone?', 'The face is checked on the server, so the employee can punch from the phone they log in with. Companies that use single-device login ask managers and staff to send a Device Change request from their account, which the admin approves. After approval, the old phone is signed out and the employee can log in on the new one.', 1340, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can an employee punch in from a different phone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How can the admin fix a wrong or missing punch?', '1. Open Staff from the dashboard and tap the employee.
2. Open the Day-Wise tab.
3. Tap the punch in or punch out time of the date.
4. In Edit Attendance set the hours and minutes and tap Save Changes.
You can edit any session up to the company''s daily limit, and a session cannot be created before the earlier sessions of that day exist. If another session is still open, close it first.', 1350, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can the admin fix a wrong or missing punch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How do I see an employee''s punch photos and times for a day?', 'Open Staff, tap the employee and use the Calendar or Day-Wise tab. Tapping a day opens Attendance Details with the Punch In and Punch Out times and photos. You can also open a person from the filtered staff list on the Home dashboard for a chosen date, to see their punch times and photos.', 1360, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see an employee''s punch photos and times for a day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How does an employee see their own attendance?', 'On the employee side, open the Attendance Calendar from the employee screens. Each day is coloured as Present, Absent, Leave, Half Day or Sunday, and tapping a day shows its punch details. Approved leave also appears on the calendar.', 1370, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does an employee see their own attendance?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'What do the attendance day types Present, Late, Half Day, Absent, Leave, Holiday and Weekly Off mean?', 'Present: the employee punched in. Late: first punch in was after the shift start. Half Day: the employee worked less than half of the shift and has no open session. Absent: a working day in the past with no punch, leave or holiday. Leave: an approved leave date. Holiday and Weekly Off: no work expected. If several apply, punches win over leave, then holiday, then weekly off.', 1380, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the attendance day types Present, Late, Half Day, Absent, Leave, Holiday and Weekly Off mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'When is an employee marked Late?', 'Clockyn looks at the first punch in of the day. If it is later than the shift start time, the day is Late. For night shifts, a punch up to 12 hours after the start time counts as late, so arriving early at 8 PM for a 9 PM shift is not late. Employees without an assigned shift cannot be late.', 1390, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('When is an employee marked Late?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'When is a day counted as Half Day?', 'A day becomes Half Day when the employee worked at least a minute but less than half of the shift length, and has no session still open. If no shift is assigned, a 9 hour day is used as the base. This is separate from the Attendance Rule, which turns repeated late arrivals into a half day or leave.', 1400, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('When is a day counted as Half Day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Which date does a night shift punch belong to?', 'A session that starts at night belongs to the date it started, even if the punch out is after midnight. So a 10 PM to 6 AM shift started on Monday is shown on Monday and not split over two days. Dates are based on Indian Standard Time.', 1410, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which date does a night shift punch belong to?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'Which time zone does Clockyn use for attendance?', 'Attendance dates and punch times follow Indian Standard Time (IST). The date of a punch is its IST date. Late and half day decisions use IST as well.', 1420, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which time zone does Clockyn use for attendance?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Attendance & Face Punch', 'How do I mark someone present if they have no phone or could not punch?', 'Use Edit Attendance. Open Staff, tap the employee, go to the Day-Wise tab, tap the punch in or punch out time for that date, enter the times and tap Save Changes. A punch in or punch out time is required. The edit follows your company''s daily session limit.', 1430, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I mark someone present if they have no phone or could not punch?'));

-- Attendance & Time / Shifts, Holidays & Rules
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I create a shift?', '1. Open Settings > Attendance & Devices > Shift Setting.
2. Tap Add New Shift.
3. Enter the Shift Name.
4. Pick the Start Time and End Time.
5. Tap Save.
The shift appears in the list with its duration, and you can then assign staff to it.', 1440, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I create a shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Can I create a night shift that ends the next day?', 'Yes. Set the start time in the evening and the end time in the morning, for example 9:00 PM to 6:00 AM. Clockyn understands that the end is on the next day. Late and half day rules work for night shifts too, and the session is counted on the date it started.', 1450, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I create a night shift that ends the next day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Why does the app say my shift works out to too many hours?', 'A new or edited shift cannot be longer than 18 hours. The message shows the computed hours and asks you to check the start and end time, especially AM or PM. A common cause is choosing PM instead of AM for the end time.', 1460, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say my shift works out to too many hours?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I assign staff to a shift?', '1. Open Settings > Attendance & Devices > Shift Setting.
2. Open the shift menu and choose Assign Staff.
3. Select one or more employees.
4. Confirm.
The staff are now on that shift (''Staff assigned!''). An employee has one shift at a time, so assigning moves them from their previous shift. To take someone off, use Remove on the shift.', 1470, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I assign staff to a shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I remove an employee from a shift?', 'Open Settings > Attendance & Devices > Shift Setting, open the shift''s staff list and tap Remove next to the employee. The app asks ''Remove name from this shift?''. After confirming, the employee has no shift assigned until you assign another one.', 1480, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I remove an employee from a shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'What happens if I delete a shift?', 'The app asks you to confirm because the action cannot be undone. All staff who were on that shift are unassigned, and the message tells you how many. Assign them to another shift afterwards so late and half day rules keep working for them.', 1490, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens if I delete a shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Can I edit a shift''s timing?', 'Yes. In Settings > Attendance & Devices > Shift Setting open the menu of the shift and choose Edit, change the name, start time or end time and save. The same limit of 18 hours applies. Staff stay on the shift.', 1500, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I edit a shift''s timing?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'What happens if an employee has no shift?', 'Attendance still works, but there is no shift start to compare with, so the employee is never marked Late. For half day and overtime Clockyn uses a 9 hour working day as the base. We recommend assigning a shift to every employee. You can see who is unassigned in the Employee Shift / Timing report.', 1510, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens if an employee has no shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I see which employees are on which shift?', 'Go to Settings > View & Download Reports > Employee Shift / Timing. Employees are grouped by shift with the shift timing and duration, and those without a shift appear under Unassigned. You can also open a shift in Shift Setting to see its assigned staff.', 1520, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see which employees are on which shift?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I add a holiday?', '1. Open Settings > Company Settings > Holiday Management.
2. Tap a date on the calendar.
3. Enter the Holiday Name, for example Diwali.
4. Choose the branch and department it applies to, or leave them for everyone.
5. Set Block Attendance on or off.
6. Tap Add Holiday.
The month list shows the count of holidays.', 1530, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a holiday?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'What does Block Attendance mean on a holiday?', 'With Block Attendance on, staff cannot punch in on that day (''Staff cannot punch in on this day''). With it off, staff can still punch in if they choose (''Staff can still punch in if they choose''). New holidays have blocking on by default.', 1540, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does Block Attendance mean on a holiday?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Can I set a holiday for only one branch or department?', 'Yes. When adding a holiday choose a Branch and a Department. Leaving them as All means the whole company. Two holidays cannot share the same date and the same branch and department, and trying to add a duplicate is refused.', 1550, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I set a holiday for only one branch or department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I edit or delete a holiday?', 'Open Settings > Company Settings > Holiday Management and select the month. Open the holiday in the list to use Edit Holiday, where you can change the name, the branch and department, or Block Attendance. To remove it, choose Delete, and the app asks ''Delete Holiday?'' before removing one date or all dates of that holiday. Deleting cannot be undone.', 1560, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I edit or delete a holiday?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How do I set weekly offs like Sunday or Saturday?', 'In Holiday Management tap Configure Off Days and select the days of the week that are off for your company. The list can include any days from Monday to Sunday. If you never set it, Sunday is the weekly off. Weekly offs are not counted as absent.', 1570, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I set weekly offs like Sunday or Saturday?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'What is the Attendance Rule and where do I find it?', 'The Attendance Rule decides what happens when an employee is late too many times. Open Settings > Attendance & Devices > Attendance Rule. Under Half Day Rule choose the Late Punch-In Count (1 to 10) and Consider As (Half Day or Leave), then tap Save Rule. A sentence below the fields summarises it, for example ''If an employee is late 3 times, it will be considered as Half Day.''', 1580, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the Attendance Rule and where do I find it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How is the late penalty calculated from the Attendance Rule?', 'For each full set of late days, one penalty unit is counted. With the rule set to 3 times and Half Day, 3 late days make half a day and 6 late days make one day. If Consider As is Leave, each set counts as a full day. The penalty feeds into the salary as a deduction. Until you save a rule, no late penalty is applied.', 1590, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is the late penalty calculated from the Attendance Rule?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Is there a grace time for late punch in?', 'The Attendance Rule screen in the app does not offer a grace period setting. A punch in is Late when it is after the shift start time. If your company needs a grace period, ask the team through Support > Live Chat.', 1600, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Is there a grace time for late punch in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'How does overtime work in Clockyn?', 'Overtime is set up under Settings > Salary Structure > Incentive / OT / Bonus > Overtime. For each employee Clockyn adds the hours worked in the month and subtracts the shift hours for each present day. Anything above that is overtime, and it is paid per block of minutes that you define in the overtime setting. Days with an auto-closed session are skipped until reviewed. Check the Overtime Report for the figures.', 1610, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does overtime work in Clockyn?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Why do two branches have different holidays on the same day?', 'Holidays can be set for the whole company or for a specific branch and department. Each employee gets the holidays that match their own branch and department plus the company-wide ones. Open Holiday Management and check the Branch and Department on each holiday.', 1620, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why do two branches have different holidays on the same day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Which Settings menu has shifts, holidays and the attendance rule?', 'Shift Setting, Assign Devices and Attendance Rule are under Settings > Attendance & Devices. Holiday Management is under Settings > Company Settings. Reports are in Settings > View & Download Reports. Managers see only the parts they have permission for.', 1630, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which Settings menu has shifts, holidays and the attendance rule?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Shifts, Holidays & Rules', 'Which permissions can I give a manager for attendance rules and shifts?', 'In Managers you can switch on permissions such as Shift Management, Holiday Management, Attendance Reports, Company Settings and Requests Management, then tap Save Permissions. Other permissions available are Staff Management, Branch Management, Salary Management, Leads Management and Incentive Management. The manager sees only the screens they are allowed to use.', 1640, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which permissions can I give a manager for attendance rules and shifts?'));

-- Attendance & Time / Leave & Requests
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What types of requests can employees raise?', 'From the Requests option employees can send four types: Leave Request, Permission Request, Shift Change Request and Device Change Request. Admins and managers see them all in one place, with a badge counting pending ones, and approve or reject each.', 1650, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What types of requests can employees raise?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'How does an employee apply for leave?', '1. Open Requests and choose Leave Request.
2. Tap the dates on the calendar, you can select several days.
3. Write the reason (maximum 500 characters).
4. Tap Submit Leave Request.
You see ''Leave request submitted successfully''. Leave dates cannot be in the past, and a request cannot include a date that already has a pending or approved leave.', 1660, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does an employee apply for leave?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What is a permission request?', 'A permission request is for a short absence inside one day, such as leaving early or coming late. Pick the date, a start time and an end time, add a reason and tap Submit Permission Request. It can be at most 6 hours, the end time must be after the start time, and the date cannot be in the past.', 1670, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is a permission request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'How do I request a shift change?', '1. Open Requests and choose Shift Change Request.
2. Select the Preferred Shift from the list of company shifts.
3. Choose the Requested Shift Date.
4. Write the reason and tap Submit Request.
You cannot ask for the shift you are already on, the date cannot be in the past, and only one request per date is allowed. After approval the new shift applies for that day.', 1680, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I request a shift change?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What is a device change request and when do I need it?', 'It is used when an employee gets a new phone or loses theirs and the company allows one device per employee. The employee fills in the reason, such as ''Lost phone, damaged device, new phone'', and submits. Once the admin approves, the old device is signed out and the employee can log in on the new one. Only one request can be pending, and at most 3 can be sent in 24 hours.', 1690, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is a device change request and when do I need it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'How does the admin approve or reject a request?', '1. Tap Requests in the Home quick actions.
2. Open the type, for example Leave Requests.
3. Check the employee, dates and reason on the card.
4. Tap Approve or Reject.
The employee gets a notification with the decision. You can change a decision later using ''Change to Approve'' or ''Change to Reject''.', 1700, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does the admin approve or reject a request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'Can I approve only some dates of a leave request?', 'Yes. Leave and permission requests are decided date by date. Approve or reject each date on the card, or use Approve and Reject to decide all dates at once. The request is shown as pending while any date is pending, approved if at least one date is approved, and rejected if all are rejected.', 1710, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I approve only some dates of a leave request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What are the request statuses Pending, Approved and Rejected?', 'Pending means no decision yet. Approved means the admin or manager accepted it, and Rejected means it was declined. Employees see these in My Requests, and a notification arrives on every decision. For leave, the status of each date can differ.', 1720, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What are the request statuses Pending, Approved and Rejected?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'Does an approved leave change attendance?', 'Yes. An approved leave date shows as Leave on the attendance calendar instead of Absent. If the employee still punches in on that date, the punch takes priority and the day is shown as present. Rejected or pending dates are treated like normal days.', 1730, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Does an approved leave change attendance?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'Who can approve requests?', 'The admin can decide every request of the company. A manager can handle requests only when they have the Requests Management permission, and normally for staff in their assigned branches and departments. Give the permission in Managers and tap Save Permissions.', 1740, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Who can approve requests?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What is the number on the Requests tile or badge?', 'It counts the requests that still need a decision, across leave, permission, shift change and device change. When you open a list, its new-request badge clears, and new requests raise it again. On the employee side the badge counts the employee''s own pending requests.', 1750, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the number on the Requests tile or badge?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'I see ''A leave request already exists for one of the selected dates''. Why?', 'One of the days you picked is already covered by a pending or approved leave request of yours. Open My Requests to find it, then choose only dates that are free. Rejected requests do not block a new one.', 1760, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I see ''A leave request already exists for one of the selected dates''. Why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'Why can''t I choose a past date for leave, permission or shift change?', 'Requests are for today or a future date only. The app shows messages such as ''Leave dates cannot be in the past''. For a past day, the admin can fix attendance directly using Edit Attendance.', 1770, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I choose a past date for leave, permission or shift change?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'How do I see the status of my requests as an employee?', 'Open Requests and choose My Requests to see the leave, permission, shift change and device change requests you sent, with their status. New Request starts a new one. The list refreshes when you pull down, and a notification arrives when a request is decided.', 1780, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see the status of my requests as an employee?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Leave & Requests', 'What is the ''Reason is required'' or ''too long'' error on requests?', 'A reason is needed for every request type. It must not be empty and can be up to 500 characters. Add a short clear reason and submit again.', 1790, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the ''Reason is required'' or ''too long'' error on requests?'));

-- Attendance & Time / Reports & Analytics
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'Where do I find reports?', 'Open Settings > View & Download Reports, or tap View Reports in the Home quick actions. The Generate tab lists the report types, and the View Reports tab shows the ones you generated earlier. Reports are grouped as Attendance Reports, Salary Reports, Employee Reports and CRM.', 1800, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where do I find reports?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'Which attendance reports are available?', 'Attendance Reports has Monthly Summary, Staff-wise Detailed Report, Late Report and Overtime Report. Employee Reports has Employee Details and Employee Shift / Timing. Salary Reports has Monthly Salary and View Staff Salary. Some rows such as Incentives, Loss of Pay and Previous Employees appear greyed out because they are not available yet.', 1810, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which attendance reports are available?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'How do I generate the monthly attendance report?', '1. Open Reports and choose Monthly Summary.
2. Pick the month and year.
3. Choose All staff or one employee.
4. Tap Generate & View PDF.
The PDF opens and is saved in View Reports for later.', 1820, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I generate the monthly attendance report?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'Can I download reports in Excel or CSV?', 'Yes. The Export Report sheet offers Download as PDF, DOC, Excel and CSV. The monthly report is generated as a PDF, and employee and other reports open the format list. Your earlier downloads appear in the View Reports tab.', 1830, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I download reports in Excel or CSV?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What does the Late Report show?', 'It lists each late day of the selected month with employee, branch, department, shift start, punch in time and how late they were (''Late By'', for example 25 min or 1h 5m). When shift times are missing the shift and Late By cells show a dash. Choose the month at the top and export it if needed.', 1840, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the Late Report show?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What does the Overtime Report show?', 'For the month it shows each employee''s overtime and the amount earned, based on the Overtime setting in Salary Structure. Employees without overtime appear in grey. Days with a forgotten punch out are left out until the admin corrects them.', 1850, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the Overtime Report show?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What is the Staff Detailed Report?', 'Staff-wise Detailed Report gives one row per employee for the chosen month with working days, present, absent, late, half day, leave, Sundays and the hours worked, along with branch and department. Select the month, check the figures on the screen and download it in your preferred format.', 1860, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the Staff Detailed Report?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What is in the Employee Details report?', 'Employee Details lists your employees with employee ID, department and branch. Tap an employee to see their profile report, then View it or Download it as a file in the format you choose.', 1870, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is in the Employee Details report?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What does the Staff Overview on the Home dashboard show?', 'Staff Overview shows today''s numbers as rings you can swipe: Total Staff, Absent, Late Punched-In, Punched-In, Working and Punched Out. Each ring shows its share of total staff. The carousel moves by itself and stops when you touch it.', 1880, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the Staff Overview on the Home dashboard show?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'What is the difference between Punched-In, Working and Punched Out on the dashboard?', 'Punched-In counts staff who punched in on the chosen date. Working counts those who are punched in right now. Punched Out counts those who have finished and punched out. Absent counts staff without attendance, and Late Punched-In counts those whose first punch in was after the shift start. Tap any figure to see the people.', 1890, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between Punched-In, Working and Punched Out on the dashboard?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'How do I see the list of absent or late staff?', 'On the Home dashboard tap the Absent or Late Punched-In figure. The staff list opens already filtered, and you can also choose Total, Punched-In, Working or Punched Out. Tap a person to see their punch times and photos for that date.', 1900, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see the list of absent or late staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'How do I check attendance of an earlier date or one branch?', 'Use the filter bar on the Home dashboard. Choose a Branch (or ALL), switch to Department view if you want, and pick a Date. Future dates cannot be selected. If a past date has no punches, the app shows ''No attendance record for this date''.', 1910, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I check attendance of an earlier date or one branch?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'How can a manager see reports?', 'Give the manager the Attendance Reports permission under Managers. The manager then finds a reports entry in their Settings. They can open Monthly Summary, Late Report, Monthly Salary and View Staff Salary, limited to their staff, while other reports stay admin-only.', 1920, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can a manager see reports?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'Where are my downloaded reports saved?', 'Open Reports and tap the View Reports tab. Every report you generated appears there with its type (PDF, Excel and so on) so you can open it again. If nothing is there the app says ''No reports yet'' and asks you to use the Generate tab.', 1930, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where are my downloaded reports saved?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'Why is my report empty or failing to download?', 'Check that you picked the right month and that staff have attendance in it. If the screen shows an error, tap Retry. For a failed download the app says ''Failed to generate PDF'' or ''Failed to download CSV''. Try again after checking your internet connection, and contact Support > Live Chat if it keeps failing.', 1940, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is my report empty or failing to download?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Attendance & Time', 'Reports & Analytics', 'How do I see a staff member''s monthly attendance calendar?', 'Open Staff, tap the employee and use the Calendar tab to see each day coloured by status for the month, or Day-Wise for a table of punch in, punch out and hours. Tap a day for the details and photos. You can change the month at the top.', 1950, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see a staff member''s monthly attendance calendar?'));

-- ───── Pay & Expenses ────────────────────────────────────────────────────────
-- Pay & Expenses / Payroll & Salary
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How is a staff member''s monthly salary calculated?', 'Net salary = Total Earnings minus Total Deductions.
Earnings: Basic Salary, House Rent Allowance, daily wages, hourly pay, incentives, bonus, overtime, odometer allowance, approved travel tickets and bills, and any extra earnings you added to the payslip.
Deductions: EPF, Professional Tax, Health Insurance, advances given in that month, a late fine (if you saved an Attendance Rule) and any extra deductions you added.
To see every figure, open Settings > View & Download Reports > Salary Reports > View Staff Salary.', 1960, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is a staff member''s monthly salary calculated?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I create a salary structure (payslip) for monthly fixed staff?', '1. Open Settings > Salary Structure > Monthly Fixed.
2. Tap Add.
3. Enter the Payslip Name, Basic Salary and House Rent Allowance (all three are required).
4. Fill EPF, Professional Tax and Health Insurance if they apply. These are optional.
5. Use Add Earning or Add Deduction for any extra lines.
6. Tap Save Payslip. You will see ''Payslip Added!''.
The payslip only affects salaries after you assign it to staff.', 1970, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I create a salary structure (payslip) for monthly fixed staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I assign a payslip to my staff?', '1. Open Settings > Salary Structure > Monthly Fixed.
2. On the payslip card, tap the three-dot menu and choose Assign Staff.
3. Select the staff members.
4. Tap Assign (the button shows how many you picked, for example Assign 3 Staff).
The card then shows ''View N Assigned Staff'' so you can check who is on it.', 1980, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I assign a payslip to my staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Can one staff member be on two payslips at the same time?', 'No. A staff member can have only one monthly payslip. If you try to assign a second one, Clockyn tells you ''Already assigned to'' followed by the name of the current payslip.
To move the person, open Assign Staff on the old payslip, tap Remove next to their name, and then assign the new payslip.', 1990, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can one staff member be on two payslips at the same time?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Which payslip fields are compulsory?', 'Payslip Name, Basic Salary and House Rent Allowance are required. EPF, Professional Tax and Health Insurance are optional; leave them blank if they do not apply and they count as zero.
Extra earning or deduction lines (Add Earning, Add Deduction) only count when you fill both a name and an amount.', 2000, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which payslip fields are compulsory?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'What is the maximum amount I can enter in salary fields?', 'Money amounts can be up to 9,999,999.99 with at most two decimal places. Percentages (such as Bonus or Turnover incentive) must be between 0 and 100, also with at most two decimals. Counts and minutes must be whole numbers.
If a value is not accepted you will see a message such as ''Must be a valid number'' or ''Enter a number between 0-100''. Correct the value and save again.', 2010, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the maximum amount I can enter in salary fields?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I edit a payslip I already created?', '1. Open Settings > Salary Structure > Monthly Fixed.
2. Tap the three-dot menu on the payslip and choose Edit.
3. Change the values and tap Update Payslip. You will see ''Payslip Updated!''.
The new figures apply to every staff member on that payslip for months that are not marked as paid yet. Months already marked as paid keep their saved figures.', 2020, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I edit a payslip I already created?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why can''t I delete an old payslip?', 'In Settings > Salary Structure > Monthly Fixed the Delete option is offered only on the newest payslip in the list. Older payslips show Edit and Assign Staff only.
When you delete the newest one, Clockyn asks ''Delete this template?'' before removing it. If you need an older payslip changed, use Edit instead.', 2030, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I delete an old payslip?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'What does ''A template with this name already exists'' mean?', 'Every payslip in your organization needs its own name. This message appears when you save a payslip using a name that is already taken. Choose a different Payslip Name (for example add the grade or year) and tap Save Payslip again.', 2040, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does ''A template with this name already exists'' mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I set up daily wages?', '1. Open Settings > Salary Structure > Daily Wages.
2. Tap Add and enter the amount under ''If one day present''.
3. Save. The template is named Day Pay 1, Day Pay 2 and so on.
4. Tap Assign Staff and pick the staff who are paid by the day.
Pay for the month = that amount x the number of days the person was present. Each amount can be used in only one Day Pay template, and assigning someone here moves them off their other Day Pay template.', 2050, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I set up daily wages?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How are present days counted for daily wages?', 'A day counts as present when the staff member has at least one punch-in on that date. A day with two sessions still counts as one present day.
Daily wages for the month = Day Pay amount x present days. You can check each person''s present days from Staff Details > Calendar or the attendance reports.', 2060, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How are present days counted for daily wages?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I pay staff by the hour?', '1. Open Settings > Salary Structure > Hourly Calculation (the screen is titled Hourly Charges).
2. Tap Add and enter the amount under ''If one hour present''. The template is named Hour-Pay-1, Hour-Pay-2 and so on.
3. Tap Assign Staff and select the staff.
Hourly pay = that amount x the total hours of the staff member''s completed sessions in the month (sessions with both a punch-in and a punch-out).', 2070, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I pay staff by the hour?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why is a staff member''s hourly pay lower than I expected?', 'Hourly pay counts only completed sessions, meaning sessions that have both a punch-in and a punch-out. Hours of a day where the person forgot to punch out are not added until the session is closed.
Open Staff Details > Day-Wise to see the punches for each day and check for missing punch-outs.', 2080, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is a staff member''s hourly pay lower than I expected?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'What types of incentives are available?', 'Open Settings > Salary Structure > Incentive / OT / Bonus > Incentive. The ''Select Incentive Type'' screen offers two kinds:
- Count Basis: a fixed Each Incentive amount.
- Turnover Basis: a percentage of the Basic Salary.
Overtime and Bonus have their own entries in the same group. After creating an incentive, use Assign Staff to choose who receives it.', 2090, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What types of incentives are available?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How does the Count Basis incentive work?', '1. Open Settings > Salary Structure > Incentive / OT / Bonus > Incentive > Count Basis.
2. Tap Add and fill Count Upto Basic Salary, Next Count and Each Incentive.
3. Save, then tap Assign Staff.
Every month, the Each Incentive amount of the template is added to the earnings of each assigned staff member. You can see the result in View Staff Salary.', 2100, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does the Count Basis incentive work?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How does the Turnover Basis incentive work?', '1. Open Settings > Salary Structure > Incentive / OT / Bonus > Incentive > Turnover Basis.
2. Tap Add and enter Turnover Upto Basic Salary and After Target Incentive (a percentage).
3. Save and use Assign Staff.
For each assigned staff member the incentive = Basic Salary x After Target Incentive % / 100, added to the month''s earnings. It shows in the Turnover (₹) column of View Staff Salary.', 2110, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does the Turnover Basis incentive work?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why do I get ''This value already exists'' when saving an incentive?', 'Each value must be different from the values in your other templates of the same kind. For example ''This value already exists in Count 1'' means another Count template already uses that number; ''This percentage already exists in Bonus 2'' means a Bonus template uses that percentage.
Change the value, or edit the existing template instead of creating a new one.', 2120, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why do I get ''This value already exists'' when saving an incentive?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I set up overtime pay?', '1. Open Settings > Salary Structure > Incentive / OT / Bonus > Overtime.
2. Tap Add, enter the duration in hours and minutes (minutes 0-59) and the Amount.
3. Save and tap Assign Staff.
For every full block of that duration worked beyond the shift, the amount is paid. The staff member must have a shift assigned so Clockyn knows when overtime starts. Each duration can be used in only one template.', 2130, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I set up overtime pay?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How is overtime calculated? Can you show an example?', 'For the month Clockyn adds up the hours of completed sessions and subtracts the shift hours for every day present. What is left is overtime. Overtime pay = number of full duration blocks x the template amount.
Example: shift 9 hours, present 20 days, worked 190 hours. Extra time = 190 - 180 = 10 hours = 600 minutes. With a template of 30 minutes = ₹100, pay = 20 blocks x ₹100 = ₹2,000. Partial blocks are not paid.', 2140, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is overtime calculated? Can you show an example?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why is overtime not showing for a staff member?', 'Check these points:
1. The staff member is assigned to an Overtime template (Settings > Salary Structure > Incentive / OT / Bonus > Overtime > Assign Staff).
2. They have a shift assigned, otherwise Clockyn cannot tell how long a normal day is.
3. They worked more than the shift in total for the month; a short day is balanced against longer days.
4. Their sessions are closed (punched in and out). Extra time must also fill at least one full block of the template duration.', 2150, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is overtime not showing for a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I give a bonus to my staff?', '1. Open Settings > Salary Structure > Incentive / OT / Bonus > Bonus.
2. Tap Add and enter the percentage (0-100), the Month and the Year.
3. Save and use Assign Staff (the dialog is titled Staff Details). If someone already has another bonus, Clockyn warns you before selecting them.
Bonus = Basic Salary x percentage / 100, paid only in the month and year you chose.', 2160, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I give a bonus to my staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why is the bonus not appearing in a staff member''s salary?', 'A bonus is added only when all of these are true:
1. The staff member is assigned to the Bonus template.
2. You are looking at the same month and year that you picked in the template. Other months show no bonus.
3. The staff member has a payslip with a Basic Salary, because the bonus is a percentage of it.', 2170, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is the bonus not appearing in a staff member''s salary?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How can I charge a fine for late punch-ins? The Fine / Deduction menu says Coming soon.', 'The Fine / Deduction menu is not available yet, but late fines already work through the Attendance Rule.
1. Open Settings > Attendance & Devices > Attendance Rule.
2. Set Late Punch-In Count (1 to 10) and Consider As (Half Day or Leave).
3. Tap Save Rule.
For every group of that many late punch-ins in a month, one penalty applies: a Half Day counts 0.5 day and Leave counts 1 day. The fine shows under Fine (₹) in the salary.', 2180, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can I charge a fine for late punch-ins? The Fine / Deduction menu says Coming soon.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How is the late fine amount calculated?', 'Fine = penalty days x Basic Salary / working days of the month.
Example: the rule is 3 late punch-ins = Half Day. A staff member is late 7 times, so 2 penalties = 1 day. With Basic Salary ₹30,000 and 26 working days, fine = 1 x 30,000 / 26 = ₹1,153.85.
No fine is charged if you have not saved an Attendance Rule, or if the staff member has no monthly payslip.', 2190, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is the late fine amount calculated?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I add a travel allowance or any extra earning to a payslip?', '1. Open Settings > Salary Structure > Monthly Fixed and edit the payslip (or tap Add for a new one).
2. Under earnings tap Add Earning, then type the name (for example Travel Allowance) and the amount.
3. For recurring deductions tap Add Deduction and do the same.
4. Tap Update Payslip or Save Payslip.
A line is counted only when both name and amount are filled.', 2200, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a travel allowance or any extra earning to a payslip?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Where can I see the salary of all my staff for a month?', 'Open Settings > View & Download Reports > Salary Reports.
- View Staff Salary (titled Staff Salary) lists every component per person: Basic, Daily Wages, Hourly Calc, Count, Turnover, OT, Bonus, Fine, Odometer, Ticket, Other Bills and Advance.
- Monthly Salary shows who is paid or unpaid and the totals.
Use the month and year picker to change the month.', 2210, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I see the salary of all my staff for a month?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I mark salary as paid?', '1. Open Settings > View & Download Reports > Salary Reports > Monthly Salary.
2. Choose the month and year.
3. Tap Pay beside one person, or tick several people and tap Pay Selected, or tap Pay All.
4. Confirm ''Mark salaries as paid?'' with Mark as paid.
You will see ''Payment Processed!'' and the staff show a Paid badge. Paid people cannot be selected again.', 2220, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I mark salary as paid?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Does Mark as paid transfer money to my staff?', 'No. Mark as paid only records in Clockyn that you have paid the salary for that month, so the list, the totals and the payslip show it as Paid. The actual bank transfer or cash payment is done by you outside the app.', 2230, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Does Mark as paid transfer money to my staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'What happens to a salary after it is marked as paid?', 'Clockyn saves the net amount and a copy of the full calculation. From then on the salary list, the breakdown and the payslip show those saved figures with a Paid status, even if you later change attendance, advances or templates.
Marking the same person again does nothing, so a saved payment is never overwritten.', 2240, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens to a salary after it is marked as paid?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Can I undo a salary that was marked as paid by mistake?', 'The app has no option to unmark a paid salary, and a saved payment cannot be overwritten. Please double-check the figures in View Staff Salary before you tap Mark as paid. If you already marked one by mistake, contact Support > Live Chat and our team will help.', 2250, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I undo a salary that was marked as paid by mistake?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How many staff can I mark as paid at once?', 'Up to 500 people in one go. You must select at least one person, otherwise Clockyn says ''Select at least one employee''. If your team is larger, mark them in two or more rounds with Pay Selected. If the salary of even one selected person cannot be calculated, nobody in that batch is marked.', 2260, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How many staff can I mark as paid at once?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'What do Total Amt, Unpaid Amt and Payment Progress mean on the Monthly Salary screen?', 'The cards at the top summarise the selected month:
- Total: number of staff in the list
- Paid and Unpaid: how many are paid or still pending
- Total Amt: the sum of net salaries
- Unpaid Amt: the net salary still to be paid
Payment Progress shows the percentage of staff already marked paid.', 2270, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do Total Amt, Unpaid Amt and Payment Progress mean on the Monthly Salary screen?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Who appears in the Monthly Salary list?', 'All your active managers and staff appear in the list, each with their salary type, basic salary and net salary for the month. The admin account itself is not listed. Staff you have deactivated are not included in new salary lists.', 2280, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Who appears in the Monthly Salary list?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Can I see or pay the salary of a previous month?', 'Yes. Use the month and year picker on Monthly Salary or View Staff Salary to move to an earlier month. Unpaid months are recalculated from the attendance, advances and expenses of that month. Months already marked as paid show the saved figures.', 2290, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I see or pay the salary of a previous month?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Does the salary change during the month?', 'Yes, until you mark it as paid. The figures follow the current attendance, approved expenses and advances of the month, so they grow as the month goes on. Once a month is marked as Paid, the saved amount no longer changes.', 2300, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Does the salary change during the month?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I download the salary report?', '1. Open Settings > View & Download Reports > Salary Reports > View Staff Salary.
2. Pick the month and year.
3. Tap Export.
4. Choose Download as PDF, Download as DOC or Download as Excel.
Previously downloaded files are kept in the report history.', 2310, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I download the salary report?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How can a staff member see their own payslip?', '1. Open the Attendance Calendar from Home.
2. Tap Payslip.
The payslip shows the company name, Earnings, Deductions, the net pay in figures and in words. If you have not assigned a payslip to the person yet, it says ''No payslip assigned yet.'' Assign one from Settings > Salary Structure > Monthly Fixed.', 2320, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can a staff member see their own payslip?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'Why is a staff member''s net salary lower than their basic salary?', 'Deductions are taken from earnings. Check these items in View Staff Salary:
- EPF, Professional Tax and Health Insurance from the payslip
- Advance given in that month
- Fine for late punch-ins (needs an Attendance Rule)
- Any extra deduction lines on the payslip
Also note that days without punches do not earn daily or hourly pay.', 2330, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is a staff member''s net salary lower than their basic salary?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Payroll & Salary', 'How do I remove a staff member from a wage, incentive or bonus template?', '1. Open the template (Daily Wages, Hourly Calculation, Incentive, Overtime or Bonus) under Settings > Salary Structure.
2. Tap Assign Staff on its card.
3. In the list of assigned staff tap Remove beside the name.
The person stops receiving that amount from the next calculation of any month that is not paid yet.', 2340, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I remove a staff member from a wage, incentive or bonus template?'));

-- Pay & Expenses / Expenses & Advances
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How do my employees submit expenses?', 'On the staff Home screen they open the Expenses tile and choose one of three cards:
- ODO Meter Reading: daily vehicle readings
- Travel Tickets: travel tickets and transport bills
- Other Bills: invoices and miscellaneous expenses
Every submission goes to you for approval. You review them under Settings > Expenses & Allowances.', 2350, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do my employees submit expenses?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How does a staff member record an odometer reading?', 'Start of trip:
1. Open Expenses > ODO Meter Reading.
2. Take a photo of the odometer and enter Odometer Reading (KM).
3. Tap Save Start Reading. You will see ''Start Recorded!''.
End of trip:
4. Open the same screen, take a photo, enter the stop KM and tap Complete Reading. You will see ''Trip Complete!''.
Clockyn works out the distance for you.', 2360, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How does a staff member record an odometer reading?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Can a staff member record more than one odometer trip in a day?', 'No. Clockyn keeps one odometer reading per day. After a start reading is saved for a date it must be completed with a stop reading, and saving a second start for the same date is refused with ''Start reading already submitted for this date''. For several trips, enter the first start KM and the final stop KM of the day.', 2370, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can a staff member record more than one odometer trip in a day?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Why do I see ''Stop reading must be greater than start reading''?', 'The stop KM must be higher than the start KM saved earlier for that day. Equal readings are not accepted. Check that you typed the figure from the odometer correctly and did not swap start and stop. If the start reading was wrong, contact Support > Live Chat.', 2380, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why do I see ''Stop reading must be greater than start reading''?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'What does ''Please upload image and enter KM value'' mean?', 'The odometer screen needs both a photo of the meter and the reading. Tap Take Photo to capture it, type the kilometres in Odometer Reading (KM), then save again. KM can have up to two decimals and the stop reading cannot be lower than the start.', 2390, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does ''Please upload image and enter KM value'' mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How do I submit a travel ticket?', '1. Open Expenses > Travel Tickets.
2. Check the date at the top. You can move back to earlier days but not into the future.
3. Take a photo of the ticket.
4. Enter the Amount (more than 0, up to 2 decimals).
5. Tap Submit Ticket.
You will see ''Ticket Submitted!'' and the ticket appears in the list with the day''s total.', 2400, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I submit a travel ticket?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'What is the difference between Travel Tickets and Other Bills?', 'Travel Tickets is for travel tickets and transport bills. Other Bills is for invoices and any miscellaneous expense. Both work the same way: a photo plus an amount, then approval by you.
In salary they appear in separate columns, Ticket and Other Bills, so choose the right one.', 2410, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between Travel Tickets and Other Bills?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How do I submit an Other Bill?', '1. Open Expenses > Other Bills.
2. Select the bill date.
3. Tap to take a photo of the bill.
4. Enter the Amount.
5. Tap Submit Bill. You will see ''Bill Submitted!''.
If you tap Submit without a photo or amount, Clockyn says ''Please add a photo and enter amount''.', 2420, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I submit an Other Bill?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'The expense was saved but the photo failed to upload. What now?', 'Clockyn shows ''Saved, but the photo failed to upload''. The expense record is kept, so do not submit it again. Check your internet connection and tap Retry photo upload; this uploads only the photo for the same record, so no duplicate is created.', 2430, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The expense was saved but the photo failed to upload. What now?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'The camera does not open when I try to add an expense photo.', 'Clockyn shows ''Camera permission is required'' when the camera is blocked. Allow the camera for Clockyn in your phone settings (or see Settings > Beyond > App Permissions), then try again. You can use Retake to replace a photo or Remove to delete it before submitting.', 2440, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The camera does not open when I try to add an expense photo.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Where do I approve or reject expense claims?', 'Open Settings > Expenses & Allowances and choose Odometer, Travel Ticket or Other Bills. Each record shows the employee, date, amount and photo. Tap Approve or Reject and confirm. These buttons appear only while a claim is pending. You can filter by date and see how many records are shown.', 2450, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where do I approve or reject expense claims?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Can I change my decision after approving or rejecting an expense?', 'No. Clockyn asks you to confirm and warns ''This cannot be changed later.'' Approve or reject is final. Open the photos first (the Photo or Photos button) and check the amount before you decide.', 2460, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I change my decision after approving or rejecting an expense?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How do approved expenses reach the salary?', 'Approved odometer readings, travel tickets and other bills are added to the staff member''s earnings for the month of the reading or expense date. They show in the Odometer, Ticket and Other Bills columns of View Staff Salary. Pending and rejected claims are not added.', 2470, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do approved expenses reach the salary?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How is the odometer allowance calculated and how do I change the rate per km?', 'Allowance = distance travelled x rate per km. Until you set a rate the app shows ₹3 per km.
To change it:
1. Open Settings > Expenses & Allowances > Odometer.
2. Tap edit on the rate card (Edit Allowance Rate).
3. Enter Rate per KM (₹) and tap Save Rate. The app saves whole rupees only.
The new rate applies to readings still pending; approved and rejected readings keep the rate they had.', 2480, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is the odometer allowance calculated and how do I change the rate per km?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'What should I do if an employee entered a wrong odometer reading?', 'Open Settings > Expenses & Allowances > Odometer and look at the Odometer Photos of that day (Start Reading and Stop Reading). If the figures do not match the photos, tap Reject while the claim is pending so the distance is not paid. Then ask the employee to contact you. Approval is final, so check before approving.', 2490, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What should I do if an employee entered a wrong odometer reading?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How do I give a salary advance to a staff member?', '1. Open the staff list from Home and tap the staff member to see Staff Details.
2. Open the Advance tab and choose Add Advance.
3. Pick the Date (it cannot be in the future) and enter the Advance Amount (₹), more than 0.
4. Tap Save Advance.
You will see a success pop-up, and the record is listed under View Advance.', 2500, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I give a salary advance to a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'How is an advance deducted from salary?', 'The total of advances dated within a month is deducted from that month''s salary, shown in the Advance (₹) column and as the Advance deduction on the payslip. For example, an advance of ₹5,000 dated 12 March is deducted in the March salary. Choose the advance date carefully because it decides the month.', 2510, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is an advance deducted from salary?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Who can give advances, and to whom?', 'Only the admin can give an advance. It can be given to managers and staff of your organization; the admin account cannot receive one. Other users who try get the message ''Only admin (role_id = 1) can assign advances''.', 2520, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Who can give advances, and to whom?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'What do ''Advance date cannot be in the future'' and ''Amount must be greater than 0'' mean?', 'The advance form checks two things. The date must be today or earlier, so pick a past or current date. The amount must be a number above zero with at most two decimals, up to 9,999,999.99. Fix the field and tap Save Advance again.', 2530, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do ''Advance date cannot be in the future'' and ''Amount must be greater than 0'' mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Can I edit or delete an advance after saving it?', 'Clockyn does not show an option to edit or delete a saved advance. Each record in View Advance carries a Paid badge and the name of who added it. If an advance was entered by mistake, contact Support > Live Chat before the month''s salary is marked as paid.', 2540, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I edit or delete an advance after saving it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'Where can a staff member see the advances they received?', '1. Open the Attendance Calendar from Home.
2. Tap Advance Salary.
The Bonus / Advance sheet lists each advance with its amount, date and who added it. If there are none you see ''No bonus or advance records yet.''', 2550, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can a staff member see the advances they received?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Pay & Expenses', 'Expenses & Advances', 'What is the difference between an advance and an expense claim?', 'An advance is money you give to a staff member in advance; it is entered by you and deducted from their salary. An expense claim (odometer, travel ticket or other bill) is money the employee spent for work; they submit it with a photo, you approve it, and it is added to their salary. Advance is a deduction, an expense is an earning.', 2560, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between an advance and an expense claim?'));

-- ───── Team & Customers ──────────────────────────────────────────────────────
-- Team & Customers / CRM & Leads
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'What is the CRM tab used for?', 'CRM helps you collect client leads and hand them to your staff. As admin you add leads (Enter Leads), assign them (View / Assign Leads) and follow their progress under Work Status Overview and Carry Forward Requests. Staff and managers see their own tasks under My Leads and update the status as they work.', 2570, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the CRM tab used for?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do I add a new lead?', '1. Open CRM > Enter Leads.
2. Fill Name, Mobile, Address and Category. Remarks and the customer required date are optional.
3. Tap Save Lead.
If a field is missing you will see ''Please fill all required fields''. The mobile number must be exactly 10 digits.', 2580, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add a new lead?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do I create a new lead category?', '1. While entering or editing a lead, tap the Category field to open Select Category.
2. Tap Add and type the name under Category name.
3. Tap Add again to save.
Category names are not case sensitive, so ''Category already exists'' appears if the name is already in use. You can also search the list and delete a category you no longer need.', 2590, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I create a new lead category?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do I assign a lead to a staff member?', '1. Open CRM > View / Assign Leads.
2. Pick the day with the date chips (Yesterday, Today, Tomorrow or Pick Date).
3. Tap Assign on the lead.
4. Filter by Branch or Department if needed, then tap Assign next to the employee.
A success pop-up confirms the assignment and the employee sees the lead in My Leads.', 2600, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I assign a lead to a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'Can a lead be assigned to more than one person?', 'A lead is handled by one employee at a time. Assigning the same lead to the same person again returns ''Lead is already assigned''. To change the person, tap Reassign on the lead; afterwards only the new employee is assigned.', 2610, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can a lead be assigned to more than one person?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do I edit or delete a lead?', 'In CRM > View / Assign Leads, each lead card has Edit, Delete and Assign buttons.
- Edit opens the lead form; tap Update Lead to save.
- Delete asks ''Are you sure you want to delete this lead?'' and removes it for good.
Only leads that are not completed can be changed.', 2620, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I edit or delete a lead?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'Why are the Edit, Delete and Assign buttons disabled on a lead?', 'A lead marked Completed is locked. The card shows a Completed badge, Edit, Delete and Assign are disabled and tapping a locked lead says ''Completed leads cannot be assigned''. Completed work is kept as a record; if a client needs a new job, add a fresh lead.', 2630, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why are the Edit, Delete and Assign buttons disabled on a lead?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'What do the numbers on the Work Status Overview mean?', 'Work Status Overview shows tiles for Total, Unassigned, Pending, Working, Completed and Carry Fwd leads for the selected day, followed by a Lead-wise Status list. Tap a tile to filter the list, for example Unassigned shows the leads that still need a staff member.', 2640, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the numbers on the Work Status Overview mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'What does the lead status Pending, Working, Completed or Carry Forward mean?', 'Status shows the progress of the employee''s work on the lead:
- Pending: assigned, not started
- Working: the employee is on it
- Completed: finished (the lead is then locked for you)
- Carry Forward: the employee has asked to move it to another date and waits for your decision', 2650, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the lead status Pending, Working, Completed or Carry Forward mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do my staff see and update their leads?', 'Staff and managers open CRM > My Leads (Assigned to you). It lists the leads assigned to them that are due today or have no date. Filter with All, Pending, Working or Completed. Tap a lead to open the status sheet and choose a new status. Opening My Leads also clears the new-lead badge on the CRM tab.', 2660, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do my staff see and update their leads?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'My employee says they cannot see an assigned lead. Why?', 'My Leads shows leads assigned to the employee that are due today or have no due date. A lead dated for another day appears on that day. Also ask them to pull down to refresh. If CRM is blocked by the plan they see a CRM Unavailable card instead. Check the lead''s date and assignee in CRM > View / Assign Leads.', 2670, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('My employee says they cannot see an assigned lead. Why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'What is a carry forward request?', 'When an employee cannot finish a lead today, they choose Carry Forward in the status sheet, write a Reason, pick the Forward to date (tomorrow by default) and tap Send Request. They see ''Request Sent!''. The request waits for you under CRM > Carry Forward Requests, where you approve or reject it.', 2680, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is a carry forward request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How do I approve or reject a carry forward request?', '1. Open CRM and go to Carry Forward Requests. A red badge shows how many are pending.
2. Read the Employee, Reason, Requested for date and Submitted on.
3. Tap Approve (confirm ''Yes, Approve'') or Reject.
Approval moves the lead to the requested date. Rejecting puts the lead back to the status it had before. A request can be actioned only once.', 2690, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I approve or reject a carry forward request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'Why can''t my employee send a carry forward request?', 'Clockyn refuses a request in these cases:
- No reason written: ''Description is required''
- The forward date is in the past: ''forward_date cannot be in the past''
- A request for the same lead is already waiting: ''A pending carry forward request already exists''
- The lead is not assigned to that employee
Fix the reason or date, or wait for your decision on the earlier request.', 2700, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t my employee send a carry forward request?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'Can staff add leads themselves?', 'Yes. Staff and managers tap Add Lead on My Leads and fill the Add New Lead sheet (name, a 10-digit mobile, address and category, with optional remarks). They see ''Lead saved and sent to your admin for assignment''. The lead then appears as Unassigned for you in CRM > View / Assign Leads so you can assign it.', 2710, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can staff add leads themselves?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'What does the mobile number error on a lead mean?', 'The mobile number of a lead must contain exactly 10 digits. Clockyn shows ''Mobile number must be exactly 10 digits'' (or ''...must be 10 digits.'' in the staff form). The field accepts digits only and stops at 10, so remove country codes, spaces or dashes.', 2720, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the mobile number error on a lead mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'Why does CRM say ''CRM Unavailable''?', 'Your organization''s plan does not currently include CRM, or the plan has expired. Admin sees a Renew Plan button (View Plan Status on iPhone) that opens Your Plan & Validity. Managers and staff only see the message and need to ask their admin. Check Settings > Your Plan & Validity.', 2730, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does CRM say ''CRM Unavailable''?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'CRM & Leads', 'How can I search leads by date?', 'In CRM > View / Assign Leads use the date chips at the top: All, Yesterday, Today, Tomorrow, or Pick Date to choose any day. If nothing matches you see ''No leads found for this date''. The same date chips filter the Work Status Overview.', 2740, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can I search leads by date?'));

-- Team & Customers / Chat & Announcements
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I start a chat with a staff member?', '1. Open the Chat tab and tap Individual Chat.
2. In the search box type a name, branch or department.
3. Tap the person to open the conversation and type your message.
People you already talked to stay in the list. Staff can message colleagues of the same organization in the same way.', 2750, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I start a chat with a staff member?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Can I send photos or documents in chat?', 'Yes. In the message box tap the camera icon to take a photo, or the attach icon to choose Photo library or Document. While a file uploads the camera and attach buttons are disabled. The default file size limit is 25 MB. If a file fails to send you can retry or discard it.', 2760, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I send photos or documents in chat?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'What do the tick marks next to my messages mean?', '- Clock: the message is still sending
- Red alert icon: it failed to send (you can retry)
- One tick: sent
- Two ticks: read by the other person
The time of the message is shown beside the ticks.', 2770, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the tick marks next to my messages mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I delete a chat?', '1. Open the conversation and tap the menu in the header.
2. Choose Delete Chat.
3. Confirm. Clockyn says ''This cannot be undone.''
The whole conversation with that person is removed for both people. To look for something first, use Search in the same menu.', 2780, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I delete a chat?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I view a staff member''s contact details from chat?', 'Open the conversation, tap the person''s name or the header menu and choose View Contact. The sheet shows Emp ID, Mobile, Email, Department, Branch and Role.', 2790, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I view a staff member''s contact details from chat?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I create a group chat?', '1. Open Chat > Group Chat.
2. Tap Create Group.
3. Type the Group name (up to 50 characters) and select the members. At least one member is required.
4. Tap Create. You will see ''Group created!''.
Only the admin can create groups. Staff see their groups in the list once the admin adds them.', 2800, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I create a group chat?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I add more people to a group?', '1. Open the group and tap the menu in the header.
2. Choose Add Staff.
3. Select the people (only active staff who are not in the group are listed) and tap Add.
Any member of the group can add staff. Use View Members to see everyone, with the group admin marked as Admin.', 2810, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add more people to a group?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Can I remove someone from a group or leave a group?', 'Clockyn currently has no Remove Member or Leave Group option. The group menu offers View Members, Add Staff and, for admin, Search and Chat Theme. If someone must be removed, contact Support > Live Chat.', 2820, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I remove someone from a group or leave a group?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I change the colour theme of a group chat?', '1. Open the group as admin and tap the header menu.
2. Choose Chat Theme.
3. Pick one of Default, Ocean, Forest, Sunset, Lavender, Rose, Dark or Teal.
The theme is saved on your device for that group, so it only changes how the chat looks for you.', 2830, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I change the colour theme of a group chat?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Why does the Chat tab say ''Feature Unavailable'' or ''Chat Unavailable''?', 'Chat is blocked when your organization''s plan does not include it or has lapsed. Admin sees ''Feature Unavailable'' with a Renew Plan button (View Plan Status on iPhone). Managers and staff see ''Chat Unavailable'' and should ask the admin. Check Settings > Your Plan & Validity.', 2840, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the Chat tab say ''Feature Unavailable'' or ''Chat Unavailable''?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I send an announcement to all my staff?', '1. Open Chat > Announcement and stay on the Compose tab.
2. Enter the Title (up to 100 characters) and Message (up to 1000).
3. Under Send To keep All Staff.
4. Tap Send to All Staff.
You see ''All staff have been notified'' and every active manager and staff member gets a notification.', 2850, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I send an announcement to all my staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I send an announcement to only some people?', '1. Open Chat > Announcement.
2. Write the Title and Message.
3. Under Send To choose Select Staff.
4. Search and tick the people in Select Staff, then tap Done.
5. Tap the Send button (for example Send to 5 Staff).
Use Edit to change the selection before sending.', 2860, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I send an announcement to only some people?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I send an announcement to one branch or department?', '1. Open Chat > Branch & Dept.
2. Choose Branch or Department and then pick the one you want under Select Branch or Select Department.
3. Write the Title and Message.
4. Tap the send button, for example Send to Mumbai.
Only active managers and staff of that branch or department receive it.', 2870, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I send an announcement to one branch or department?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Why can''t I send my announcement?', 'The send button stays disabled, or an error shows, until everything is filled:
- ''Title is required''
- ''Message is required''
- ''Select at least 1 staff member'' (for Select Staff)
- ''Select a branch'' or ''Select a department'' (for Branch & Dept)
Fix the missing item and send again.', 2880, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can''t I send my announcement?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do my staff see announcements?', 'Staff get a notification. In the Chat tab they open Broadcast (announcements from admin) or Targeted Announcement (for their branch or department) and can search by text. Staff can read announcements but only the admin can send them.', 2890, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do my staff see announcements?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Can I edit or delete an announcement after sending it?', 'You can delete but not edit. Open the History tab of Announcement or Branch & Dept, find it with the search box and tap Delete. Confirm ''Delete Announcement?''. It is removed for good and staff no longer see it. To correct a mistake, delete it and send a new one.', 2900, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I edit or delete an announcement after sending it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'Who receives an announcement sent to All Staff?', 'All active managers and staff of your organization receive it, as a notification and in their Broadcast list. Staff you have deactivated do not. The admin account that sends it is not counted as a recipient.', 2910, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Who receives an announcement sent to All Staff?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Team & Customers', 'Chat & Announcements', 'How do I see my past announcements?', 'Open Chat > Announcement (or Branch & Dept) and switch from Compose to History. The tab shows the count, for example History (12), with the most recent first. Each card shows who it was sent to. Search by title; in Branch & Dept you can also search by branch or department name.', 2920, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see my past announcements?'));

-- ───── Plans & Payments ──────────────────────────────────────────────────────
-- Plans & Payments / Plans & Billing
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Where can I see my current plan and its validity?', 'Open Settings and tap Your Plan & Validity (Manage subscription). The top card shows your plan status badge (FREE TRIAL, ACTIVE, EXPIRED or NO PLAN) with a short sentence about it. Below it you can see Billing Date, Valid Until, Staff Limit, Used Staff and Remaining staff seats, plus a usage ring that shows how full your staff limit is.', 2930, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I see my current plan and its validity?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'What do Billing Date, Valid Until, Staff Limit, Used Staff and Remaining mean?', 'Billing Date is the day your current plan started. Valid Until is the last day you can use it; the plan still works on that day itself. Staff Limit is the number of staff your plan allows, Used Staff is how many active staff you already have, and Remaining is the difference, which is how many more staff you can add.', 2940, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do Billing Date, Valid Until, Staff Limit, Used Staff and Remaining mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'What do the FREE TRIAL, ACTIVE, EXPIRED and NO PLAN badges mean?', 'FREE TRIAL means you are still in the starter period that comes with a new company. ACTIVE means you have a paid plan that is still valid. EXPIRED means your paid plan has passed its Valid Until date. NO PLAN means the free trial is over and you have not bought a plan yet. The card under the badge explains what to do next.', 2950, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What do the FREE TRIAL, ACTIVE, EXPIRED and NO PLAN badges mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How long is the free trial and what does it include?', 'Every new company starts with a free trial of 1 month. During the trial all features are available, and you can add up to 10 staff members. When the month is over you need to buy a plan to keep using the features. Your Valid Until date for the trial is shown under Settings > Your Plan & Validity.', 2960, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How long is the free trial and what does it include?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'What happens when my free trial or plan expires?', 'Your data stays safe, but the features that need a plan are switched off until you renew or buy a plan. This includes face punch with the camera, chat, salary structure, staff location tracking and CRM, and the app tells you that your free trial and plan have expired and asks you to subscribe. Buy or renew from Settings > Your Plan & Validity and everything works again.', 2970, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens when my free trial or plan expires?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How do I buy a plan for the first time?', '1. Open Settings > Your Plan & Validity.
2. Tap Plan Details and pick the plan you want.
3. On the Set Staff Count step choose how many staff you need.
4. Review the Payment Summary, add a coupon if you have one, and tap Continue to Pay.
5. Complete the payment in the payment window. Your plan becomes active as soon as the payment is confirmed.
If you are still in the free trial, tap Buy a Plan to start this flow.', 2980, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I buy a plan for the first time?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How is the price of a plan calculated?', 'The price is: number of staff x price per staff per month x number of months in the plan. If you have an unused current plan, a credit is deducted (shown as Plan Upgrade Credit), then any coupon discount is deducted, and 18% GST is added on top. The Payment Summary screen shows every line (Staff Charges, Plan Upgrade Credit, Subtotal, GST and Grand Total) before you pay. Prices per staff are shown in Plan Details.', 2990, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How is the price of a plan calculated?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How do I renew my plan?', '1. Open Settings > Your Plan & Validity.
2. Tap Renew Plan.
3. Choose your staff count, review the summary and tap Continue to Pay.
Renewing extends your current plan by 12 months at the same plan level. If your plan is still active, the new period starts after your current Valid Until date, so you lose no days. If it has already expired, the new period starts today.', 3000, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I renew my plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Do I lose the remaining days if I renew early?', 'No. While your plan is still active, renewal adds 12 months starting from your current Valid Until date, so the days you have left are kept. Only after a plan has expired does the new period start from the day you pay.', 3010, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Do I lose the remaining days if I renew early?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How do I add more staff seats in the middle of my plan?', '1. Open Settings > Your Plan & Validity.
2. Tap Upgrade Plan.
3. Choose the new, higher staff count and continue to the Payment Summary.
4. Pay to confirm.
Only the extra staff are charged, and your Valid Until date does not change. Coupons and plan credit are not used for this type of upgrade.', 3020, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I add more staff seats in the middle of my plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How much will adding extra staff cost?', 'You pay only for the additional staff, not the ones you already have. The charge is extra staff x price per staff per month x the full calendar months left in your plan (at least 1 month), plus 18% GST. The exact amount is shown on the Payment Summary before you pay.', 3030, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How much will adding extra staff cost?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Can I upgrade or renew during the free trial?', 'Upgrade Plan and Renew Plan are locked while the free trial is running. The app shows Not Available During Free Trial with the date the trial ends. You can still tap Buy a Plan to purchase a plan whenever you are ready; you do not have to wait for the trial to end.', 3040, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I upgrade or renew during the free trial?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How do I switch to a different plan?', 'Open Settings > Your Plan & Validity > Plan Details, pick the plan you want and go through the checkout. If your current plan is still running, a credit for the unused part is applied automatically and shown as Plan Upgrade Credit in the Payment Summary. For special cases, ask the team in Support > Live Chat.', 3050, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I switch to a different plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Why does the app say Downgrade Not Allowed?', 'The app does not let you move from your current plan to a lower plan on your own. If you need a lower level, write to our team in Support > Live Chat and they will guide you.', 3060, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say Downgrade Not Allowed?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Why does the app say You''re on This Plan?', 'You picked the plan you already have. To extend it, use Renew Plan. To add more staff, use Upgrade Plan. To move to another plan, pick a different plan in Plan Details.', 3070, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say You''re on This Plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Which features are included in my plan?', 'Features depend on your plan level. During the free trial everything is available. On paid plans, some features such as CRM and staff location tracking are only available on higher plan levels, while the rest work on every active plan. Open Plan Details to compare plans before you buy. If a feature you need is locked, upgrade or contact Support > Live Chat.', 3080, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which features are included in my plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Why can I not open CRM or staff location tracking?', 'These two features are not part of every plan. Lower plan levels do not include them, and they are also blocked when your plan has expired. Check Settings > Your Plan & Validity to see your plan, and open Plan Details to see which plan includes them. Switching to a higher plan unlocks them.', 3090, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can I not open CRM or staff location tracking?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Why does the app say my staff limit is reached?', 'Your plan allows a fixed number of active staff, and you already have that many. The message looks like Staff limit reached with your count out of your limit. To add more people, open Settings > Your Plan & Validity > Upgrade Plan and buy extra seats. You can also deactivate staff who no longer work with you to free a seat.', 3100, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say my staff limit is reached?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'How many staff can I choose when I buy a plan?', 'On the Set Staff Count step you choose the number of staff with the plus and minus buttons. It must be a whole number of at least 1. The price scales with the number of staff you choose, so you can start with the number you need today and add more later with Upgrade Plan.', 3110, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How many staff can I choose when I buy a plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Is GST added to the plan price?', 'Yes. 18% GST is added to the amount after discounts. The Payment Summary shows the GST amount and the Grand Total you will pay, and your invoice shows the same GST details.', 3120, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Is GST added to the plan price?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Can I buy or renew a plan on an iPhone?', 'On iPhone and iPad the app only shows your plan status (current plan and Valid Till date) and does not offer buying, renewing or upgrading. To purchase, use Clockyn on an Android phone or the web version, or ask the team in Support > Live Chat.', 3130, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I buy or renew a plan on an iPhone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Plans & Billing', 'Can I get a custom plan or a bigger discount?', 'Plan prices and offers are decided by the Clockyn team, so we cannot quote them here. On the Payment Summary there is an option to email the team asking for a better discount coupon. You can also chat with us in Support > Live Chat.', 3140, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I get a custom plan or a bigger discount?'));

-- Plans & Payments / Payments & Invoices
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'How do I pay for my plan?', 'After you pick a plan and a staff count, the Payment Summary screen opens. Tap Continue to Pay and finish the payment in the payment window that opens. When the payment is confirmed you will see a success message and your plan details update.', 3150, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I pay for my plan?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'How do I apply a coupon code?', '1. On the Payment Summary screen find the coupon box.
2. Type your code in Enter coupon code and apply it.
3. If it is valid, the discount shows in the price breakdown and the Grand Total updates.
Coupons apply to the staff charge after any plan credit. They are not available for the add-staff Upgrade Plan flow.', 3160, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I apply a coupon code?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Why is my coupon not working?', 'A coupon can be rejected for these reasons: the code is wrong or expired, it is not active yet, it is not valid for the plan you selected, or your order is below the coupon''s minimum amount. The message under the coupon box says which one applies. Check the code spelling, try a different plan, or ask Support > Live Chat for a valid code.', 3170, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is my coupon not working?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Is the coupon discount percentage or a fixed amount?', 'It depends on the coupon. Some give a percentage off and some give a fixed rupee amount, and a percentage coupon may have an upper limit on the discount. The discount is applied before GST. The Payment Summary shows exactly how much you saved.', 3180, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Is the coupon discount percentage or a fixed amount?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'I closed the payment window. Was I charged?', 'If you close the window before paying, the app shows Payment Cancelled and nothing is charged. If you did pay and closed the window afterwards, wait a few minutes and check Settings > Your Plan & Validity. If your bank shows a debit but the plan did not change, see the question about money debited and plan not updated.', 3190, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I closed the payment window. Was I charged?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'What does Verification Failed or Payment not confirmed yet mean?', 'It means the payment may have gone through but the app could not confirm it with our server, for example because of a network drop. Do not pay again. On the Payment Summary tap Retry verification. If it still fails, note the payment time and amount and send them to us in Support > Live Chat.', 3200, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does Verification Failed or Payment not confirmed yet mean?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Money was debited but my plan did not update. What should I do?', 'First open the Payment Summary again and tap Retry verification if it is shown. Our system also gets a confirmation directly from the payment provider, so a successful payment is normally applied automatically within a short time. If the plan is still not updated after a while, contact us in Support > Live Chat with the date, time, amount and the last digits of the transaction reference.', 3210, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Money was debited but my plan did not update. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Why is my payment shown as under review?', 'When the amount we receive does not match the amount we expected for the order, the payment is held for a check by our team instead of activating the plan automatically. You do not need to pay again. Share the details in Support > Live Chat and we will sort it out.', 3220, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why is my payment shown as under review?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Will I be charged twice if I tap pay again?', 'Each time you start a payment a new payment order is created, so a second attempt is a second charge if it succeeds. If you see Payment not confirmed yet or Verification Failed, use Retry verification instead of paying again. If you were charged twice by mistake, contact Support > Live Chat.', 3230, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Will I be charged twice if I tap pay again?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Where can I find my invoices?', 'Open Settings > Your Plan & Validity and tap Download Invoices. The Invoices screen has two tabs: Invoice History, which lists every invoice from your successful payments, and Downloaded, which lists the invoice files saved on this device.', 3240, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I find my invoices?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'How do I download an invoice?', '1. Open Settings > Your Plan & Validity > Download Invoices.
2. Stay on the Invoice History tab and pick the invoice.
3. Choose to download it. You will see a message that the invoice was downloaded successfully.
The file then appears in the Downloaded tab, where you can open the PDF, or share it.', 3250, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I download an invoice?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'How do I share an invoice by email or WhatsApp?', 'Open Download Invoices, go to the Downloaded tab and pick the invoice. Choose the share option (Send via email, messaging apps & more) and select the app you want, such as your email app or a messaging app. If you do not see the invoice there, download it first from the Invoice History tab.', 3260, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I share an invoice by email or WhatsApp?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'The app says No PDF viewer found. What do I do?', 'Your phone has no app that can open PDF files. Install a PDF reader from your app store, or use the share option on the invoice to send it to your email or another device and open it there.', 3270, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says No PDF viewer found. What do I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'How do I delete a downloaded invoice?', 'In Download Invoices, open the Downloaded tab, pick the invoice and choose Remove from this device. Confirm Delete Invoice. This only removes the file from your phone. The invoice stays in Invoice History, so you can download it again whenever you need.', 3280, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I delete a downloaded invoice?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'What does the invoice number look like and what GST details does it show?', 'Invoice numbers look like INV-2025-0001, which is the word INV, the year and a running number. The invoice shows your plan, the amount and the GST split, either as CGST and SGST or as IGST. Use these details for your accounts and GST filing.', 3290, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What does the invoice number look like and what GST details does it show?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'I do not see an invoice for my last payment.', 'Invoices are created only after a payment is successfully confirmed. If your payment is still unconfirmed or under review, the invoice will not appear yet. Pull down to refresh the Invoice History tab. If the plan is active but the invoice is missing, contact Support > Live Chat.', 3300, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I do not see an invoice for my last payment.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Plans & Payments', 'Payments & Invoices', 'Can I get a refund?', 'Refund rules are decided by the Clockyn team and are not set inside the app. Please write to us in Support > Live Chat with your payment details and we will tell you what is possible.', 3310, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Can I get a refund?'));

-- ───── Help & Fixes ──────────────────────────────────────────────────────────
-- Help & Fixes / Notifications & Permissions
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Which permissions does the app need and why?', 'Location: GPS check-in and attendance tracking. Camera: face recognition and photo capture. Notifications: alerts, reminders and updates. Photos: attach images to requests and expenses. You can see and change all four under Settings > Beyond > App Permissions.', 3320, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Which permissions does the app need and why?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Where do I check or change app permissions?', '1. Open Settings.
2. Tap Beyond, then App Permissions.
You will see a card for Location, Camera, Notifications and Photos, with a summary like 3 of 4 permissions granted and a badge that says All Active or the number Pending. Tap a card to allow it.', 3330, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where do I check or change app permissions?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'What happens when I tap a permission card?', 'If the permission was never asked, the phone shows its normal permission prompt. If you already allowed it, nothing needs to be done. If you had blocked it earlier, the app opens your phone settings so you can switch it on, and the status updates when you come back to the app.', 3340, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens when I tap a permission card?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why does the app ask for location Allow all the time on Android?', 'On Android the app also asks for the all the time location option so that attendance tracking can work in the background. iPhones do not ask for this extra step. If you refuse it, the app opens the phone settings so you can change it.', 3350, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app ask for location Allow all the time on Android?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'How do I allow camera, location or notifications in a web browser?', 'A browser does not let the app open its settings for you. The app shows a message telling you to allow the permission for this site in your browser''s site settings, usually from the lock icon next to the web address. Allow it and then reload the page.', 3360, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I allow camera, location or notifications in a web browser?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why am I not getting notifications on my phone?', 'Check these in order:
1. Settings > Beyond > App Permissions: Notifications should be allowed.
2. Make sure the phone itself has notifications on for Clockyn and that Do Not Disturb or battery saver is not blocking them.
3. Check your internet connection.
4. Log out and log in again so the phone registers for alerts again.', 3370, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why am I not getting notifications on my phone?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'What notifications will I receive as an admin?', 'You are alerted when staff punch in or out, when a staff member submits a request such as leave or a device change, when someone sends you a chat message or a group message, and when our support team replies in Live Chat. Tapping a notification opens the matching screen.', 3380, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What notifications will I receive as an admin?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'What notifications do my staff receive?', 'Staff are alerted about announcements, new chat and group messages, new polls, decisions on their requests, and resolved support tickets. Tapping the alert opens the related screen in their app.', 3390, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What notifications do my staff receive?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'What happens when I tap a notification?', 'The app opens the screen the alert is about. Punch alerts open the home screen, request alerts open the Requests section, chat alerts open the conversation, and Live Chat alerts open Support > Live Chat. If the alert is of a type the app does not recognise, nothing special opens.', 3400, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What happens when I tap a notification?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why did I not get a notification when staff punched in?', 'Punch alerts go to the phones of your admins. Make sure Notifications are allowed for the app, the phone is online, and you are logged in on that phone. You can always check punches from the home screen or the attendance screens without the alert.', 3410, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why did I not get a notification when staff punched in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Do Live Chat replies from the support team send a notification?', 'Yes. When our team replies in Live Chat, admins receive a notification and the Live Chat row in Support shows an unread badge. Live Chat is available only to organisation admins.', 3420, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Do Live Chat replies from the support team send a notification?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'How do I stop notifications or turn them off?', 'Switch Notifications off for Clockyn in your phone settings. The App Permissions screen shows the current status but a blocked permission can only be changed in the phone''s own settings. Note that you may then miss punch alerts, requests and chat messages.', 3430, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I stop notifications or turn them off?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why does the app need access to my photos?', 'Photos permission lets you attach images to requests and expenses and to support tickets. If you deny it, the app shows Please allow photo library access when you try to pick a picture. Allow it from Settings > Beyond > App Permissions.', 3440, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app need access to my photos?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why does the app say Allow location to punch in?', 'Punching checks that you are inside your branch area, so location permission is needed first. The app says Clockyn needs your location to verify you are within your branch and asks you to enable Location access in Settings. Allow it in App Permissions and try again.', 3450, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the app say Allow location to punch in?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Notifications & Permissions', 'Why does the camera not open when I punch?', 'Face punch needs the camera permission and a working location check. The app checks the permission, then GPS, then your branch area, and only then opens the camera. Check Settings > Beyond > App Permissions that Camera and Location are both allowed.', 3460, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why does the camera not open when I punch?'));

-- Help & Fixes / Troubleshooting
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'I did not receive the OTP. What should I do?', 'Wait for the message and check your spam folder if it is an email. If it does not arrive, tap resend after a short wait, which is about 30 seconds between codes. You can request only a limited number of codes per hour. If it still does not arrive, check the number or email you typed and your internet connection, then try again later.', 3470, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('I did not receive the OTP. What should I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says Invalid or expired OTP.', 'A code works for 5 minutes only. Type the latest code you received, not an older one. If it has expired, request a new code. Typing the wrong code too many times locks the code, so request a fresh one after that.', 3480, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says Invalid or expired OTP.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Too many incorrect OTP attempts. What now?', 'After 5 wrong tries the code is locked. Wait until the current code expires, which takes up to 5 minutes, and then request a new one. Make sure you read the digits carefully from the latest message.', 3490, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Too many incorrect OTP attempts. What now?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Too many codes requested. When can I try again?', 'To protect your account, only a limited number of codes can be sent per hour and each new code needs a short wait after the last one. Try again after some time, and use the latest code you received instead of requesting many.', 3500, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Too many codes requested. When can I try again?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says Your account is inactive. Please contact your admin.', 'Your account has been switched off by your organisation. Staff should ask their admin to make the account active again. If you are the admin and cannot log in, contact the Clockyn team.', 3510, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says Your account is inactive. Please contact your admin.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says Your account has been deleted.', 'That account was removed. You can create a new account by registering again. For details about the deletion, contact your admin or Clockyn support.', 3520, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says Your account has been deleted.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says This is an admin account. Please use admin login portal.', 'Admins and staff sign in through different portals. You entered an admin number or email on the staff login (or the other way round). Go back to the sign-in screen and choose the correct portal for your role.', 3530, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says This is an admin account. Please use admin login portal.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Mobile number or email already registered. How can I sign up?', 'That mobile number or email is already used by an account. Do not register again. Use the sign-in option instead and log in with an OTP. If you do not remember registering, contact Support > Live Chat or your admin.', 3540, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Mobile number or email already registered. How can I sign up?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'My staff see You are logging in from a different device.', 'Staff accounts are linked to one phone for safety. On a new phone they tap Request Access to send a device change request. You approve it in the Requests section under Device Change Request. After approval they can log in on the new phone with an OTP. Until then the screen says the request is pending admin approval.', 3550, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('My staff see You are logging in from a different device.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Face punch says Photo is not matching.', 'The photo did not match the registered face well enough. Try again and follow the on-screen tips: face a light source, avoid strong light behind you, remove your mask and glasses if they cause glare, hold the phone at eye level at arm''s length and fill the oval with your face. If it keeps failing, re-register the face from the staff profile.', 3560, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Face punch says Photo is not matching.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Punch says It''s too dark or We couldn''t see your face.', 'The camera could not see a clear face. Move to a brighter place, keep the phone steady, move closer until your face fills the oval, and remove anything covering your face. Then try again.', 3570, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Punch says It''s too dark or We couldn''t see your face.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Punch says Unable to get your location. What can I do?', 'The phone could not find your GPS position. Turn on Location on the phone, step outside or near a window, wait a few seconds for the GPS signal and try again. Also check that Location is allowed in Settings > Beyond > App Permissions.', 3580, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Punch says Unable to get your location. What can I do?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'Punch is refused because I am outside the branch area.', 'The app checks your GPS position against your branch location before opening the camera. If you are outside the allowed area, the message from the server is shown and the camera does not open. Move closer to the branch and try again, or ask your admin to check the branch location and area in the company settings.', 3590, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Punch is refused because I am outside the branch area.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The button says Max Sessions Reached.', 'Your company allows a fixed number of punch sessions per day and this staff member has used them all. The limit is set by your organisation in the attendance settings under Settings > Attendance & Devices. They can punch again the next day.', 3600, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The button says Max Sessions Reached.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says Access denied: your free trial and plan have expired.', 'Your plan or trial has ended, so the feature you opened is blocked. Open Settings > Your Plan & Validity to buy or renew a plan. Once the payment is confirmed the feature works again. iPhone users should renew from Android or the web version.', 3610, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says Access denied: your free trial and plan have expired.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app asks me to update and I cannot continue.', 'Some updates are required. If your version is older than the minimum supported version, the update prompt cannot be dismissed. Tap Update now to go to the store and install the latest version. For optional updates you can tap Later and the prompt returns on the next check.', 3620, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app asks me to update and I cannot continue.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The app says Too many requests. Please try again later.', 'You or someone on your network sent too many requests in a short time, for example repeated logins, codes or messages. Wait a minute or two and try again.', 3630, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The app says Too many requests. Please try again later.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'A screen shows an error or stays empty. How do I fix it?', 'Pull down on the screen to refresh, or tap Retry if it is shown. Check that your internet connection is working. If it still fails, close and reopen the app, update to the latest version, and if the problem remains tell us in Support > Live Chat what you were doing.', 3640, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('A screen shows an error or stays empty. How do I fix it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'My photo could not be uploaded.', 'Photos for support tickets must be real images and not too large. Try a smaller picture, take a fresh screenshot or choose a different image. The ticket can still be sent without a photo.', 3650, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('My photo could not be uploaded.'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Troubleshooting', 'The admin login says there is no user with this email address.', 'No account is registered with that email. Check the spelling, try the email you used when creating the company, or register your company. If you see a message that the email is linked to more than one account, contact your admin or Support > Live Chat.', 3660, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('The admin login says there is no user with this email address.'));

-- Help & Fixes / Support & Feedback
INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How can I contact Clockyn support?', 'Open the Support tab. You will find FAQ for answers, Live Chat to talk to our support team, and Video Hub for step-by-step guides. You can also raise a ticket from Settings > Beyond > Support. Support options are available to organisation admins.', 3670, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How can I contact Clockyn support?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'What is Live Chat and who can use it?', 'Live Chat lets you talk directly to the Clockyn support team from the Support tab. It is available to organisation admins. Replies reach you as notifications and the Live Chat row shows an unread badge until you open it. It is the best place to ask about billing, refunds or special requests.', 3680, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is Live Chat and who can use it?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'What is the Video Hub?', 'Video Hub in the Support tab has step-by-step guide videos on how to use the app. Tap a video to play it full screen. New videos appear there as soon as the Clockyn team adds them.', 3690, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the Video Hub?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'Why can I not see the Support tab?', 'The Support tab with FAQ, Live Chat and Video Hub is meant for organisation admins. Staff and managers do not see it. Sign in with the admin account to use it.', 3700, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can I not see the Support tab?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How do I raise a support ticket?', '1. Open Settings > Beyond > Support.
2. Choose a subject.
3. Describe your issue or question.
4. Optionally tap Upload Photo to add a screenshot.
5. Tap Submit Request.
You will see Support request submitted. You can send up to 10 tickets per hour.', 3710, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I raise a support ticket?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How do I see the status of my tickets?', 'Open Settings > Beyond > Support and scroll to My Tickets. Each ticket shows a status: Open, In Progress or Resolved, with your message, the photo if any, and the replies from our team.', 3720, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I see the status of my tickets?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How do I reply to a ticket or reopen a resolved one?', 'Open the ticket in My Tickets and type in the reply box at the bottom, then send. When a ticket is resolved the box says Reply to reopen this ticket, and replying moves it back to In Progress. You will see a Reply sent confirmation.', 3730, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I reply to a ticket or reopen a resolved one?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'What is the difference between Support in Settings and the Support tab?', 'The Support tab is for self-help and quick contact: FAQ, Live Chat and Video Hub. Support under Settings > Beyond is for raising a ticket with a subject, a description and an optional photo, and following its replies. Use Live Chat for fast answers and tickets for issues you want tracked.', 3740, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What is the difference between Support in Settings and the Support tab?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How do I suggest a new feature?', '1. Open Settings > Beyond > Suggest a Feature.
2. Type a short title, for example Add dark mode support.
3. Describe your idea in detail.
4. Tap Send Suggestion.
You will see Thank you, your suggestion has been sent. You can send up to 10 suggestions per hour.', 3750, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I suggest a new feature?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'How do I request deletion of my account?', '1. Open Settings > Beyond > More Options.
2. Open Privacy Policy.
3. Tap Request Account Deletion and confirm with Submit Request.
The request is sent for review, and once it is reviewed, all the data is permanently deleted. After you send it, the app signs you out.', 3760, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('How do I request deletion of my account?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'What should I tell support so they can help me faster?', 'Mention what you were trying to do, what the screen said (the exact message helps), your company name and the time it happened. For payment issues add the amount, the date and time and the last digits of the transaction reference. A screenshot attached to a ticket also helps.', 3770, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('What should I tell support so they can help me faster?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'Why can I not submit another support ticket?', 'To prevent spam, each user can send up to 10 tickets per hour. If you reach that you will see a message asking you to try again later. Wait a while, or add your update as a reply on an existing ticket or in Live Chat.', 3780, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Why can I not submit another support ticket?'));

INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)
SELECT 'Help & Fixes', 'Support & Feedback', 'Where can I see the Fine / Deduction feature?', 'Fine / Deduction appears under Settings > Salary Structure, but it is not ready yet and shows Coming soon when you tap it. Use Suggest a Feature if you want to tell us how you would like it to work.', 3790, 1
WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('Where can I see the Fine / Deduction feature?'));

COMMIT;

-- Check:  SELECT faq_bucket, category, count(*) FROM clockyn_dev.support_faq GROUP BY 1, 2 ORDER BY min(sort_order);
