# FAQ answers that disagree with the app or the backend

The guides in `kb/guides` follow the CODE. The FAQ table (and `FAQ.sql` in the backend root) still says something different in the places below. Fix them in the console FAQ Hub (then run
`python scripts/export-faq-from-db.py` and `node scripts/faq-to-kb.js` to refresh `kb/faq`), or edit `FAQ.sql` and `FAQ_referral_update.sql` for new databases. Until then the assistant could quote a wrong FAQ answer.
Found 2026-11-15 while writing the guides. Each item is a proposal for the owner to confirm.

| # | FAQ category | What the FAQ says | What the code does | Suggested fix |
|---|---|---|---|---|
| 1 | Plans & Billing | "How many staff can I choose when I buy a plan?" says at least 1. | The minimum is 10 for a first plan or renewal and the current staff limit when switching plan. Only Upgrade Plan can add as few as 1 extra. | State the minimums. |
| 2 | Plans & Billing | Credit comes from "what your current plan cost". | Credit = 10 staff x current price x months actually charged, for any company size. | Reword to "a credit for your current plan" or change the rule (see OPEN_QUESTIONS). |
| 3 | Attendance & Face Punch | You review each of the 4 face previews and Retake one; up to 3 tries per photo. | One tap takes all 4 photos; one "Does this look clear?" screen shows 4 thumbnails; Retake redoes all 4. "3 tries" is a camera-capture retry limit. | Rewrite the registration steps. |
| 4 | Attendance & Face Punch / Troubleshooting | Max Daily Punch Sessions is in the Add Company form, or under Settings > Attendance & Devices. | It is under Settings > Company Settings > View Company > Edit Company > Attendance Settings, then Update Company. | Correct the path. |
| 5 | Shifts, Holidays & Rules | Edit Holiday can change branch and department. | It changes only name, dates of the visible month and Block Attendance. | Correct. |
| 6 | Leave & Requests | A rejected request does not block a new one. | The app calendar blocks any date that already has a request (rejected too); Sundays are unselectable whatever the weekly off. | Correct or fix the app. |
| 7 | Account & Security / Attendance | Staff accounts are tied to one phone / "the company allows one device". | A Clockyn-wide server setting (ENFORCE_SINGLE_DEVICE, off by default); when off, a new phone logging in with the one-time code takes the session over. | Reword. |
| 8 | Shifts, Holidays & Rules | No late penalty applies until the admin saves an Attendance Rule. | A default rule (3 late days = Half Day) is created automatically when a company is added, so deductions can apply without the admin opening the screen. | Reword; tell admins to review the rule. |
| 9 | Notifications & Permissions | App Permissions has 4 cards with "All Active"/"Pending". | That is the admin screen; staff and managers see 3 cards (Location, Camera, Notifications) marked Enabled/Disabled. | Split by role. |
| 10 | Reports & Analytics | Three rows are greyed. | Eight are: Incentives, Loss of Pay, Reimbursement, Previous Employees, Employees Salary Structures, Meeting Report, Travel Report, Expense Report. | Update the list. |
| 11 | Attendance & Face Punch | Face registration is one-time, ask Live Chat; another answer says re-register from the staff profile. | No re-register path was found in the app. | Make the two answers agree. |
| 12 | Payroll & Salary | Overtime needs a shift so Clockyn can tell when it starts. | With no shift every worked minute counts as overtime (pay is inflated, not zero). | Warn to assign a shift first. |
| 13 | Account & Security | Deleting an account permanently deletes all data. | Depends on ACCOUNT_DELETE_ANONYMISE: off = hard delete, on = anonymise in place; orders, payments, invoices and subscriptions are never touched. | Reword (and see OPEN_QUESTIONS). |
| 14 | Account & Security / Support | Managers and staff can read the privacy policy under More Options. | Their More Options has only "Delete My Account", which deletes immediately without review. | Correct. |
| 15 | Chat & Announcements | Chat is blocked when the plan does not include it. | Blocked only when the plan has expired or there is none. CRM is Elite-only. | Correct. |
| 16 | Payroll & Salary | "Count Upto Basic Salary", "Next Count", "Turnover Upto Basic Salary" are inputs. | Only Each Incentive and After Target Incentive affect pay. | Correct. |
| 17 | Payroll & Salary | Odometer rate may be decimal. | The backend accepts decimals; the app saves whole rupees. | Follow the app. |
| 18 | Notifications & Permissions | Logging out and in re-registers the phone for notifications; managers can open Staff Location. | Not verified in code. | Verify, then keep or remove. |
