<!--
Plain-language changelog, newest section first. Claude updates this file whenever something
finishes and is ready to bring live -- the app shows the newest ## section as a one-time popup
each fresh browser session (app.py, near the top of the Dashboard tab). Format:

## YYYY-MM-DD
- Plain-English bullet, no jargon -- written for someone who doesn't read code.
-->

## 2026-09-22
- **Credit card statements: a much shorter form** - you now type only the Total amount due, the payments shown on the statement (optional) and the finance charge (optional). The system works out the new charges itself (earlier statement on file - payments = carried over; the rest is new charges). A line under the form shows what payables will be for that card after saving, and it replaces the earlier statement instead of adding to it. If you type the payments, the earlier statement is marked Paid / Partially Paid automatically.
- **Safer statement entry** - a second statement for the same card and month, or a total more than double (or under half) of the last one, now asks you to confirm. An older statement that a newer one has replaced shows "Replaced by newer statement".
- **Credit card statements: outstanding only + search** - above the Statements table there is now an "Outstanding only" switch (on by default; it shows only statements that still count as owed) and a search box (card, cardholder, bank, last 4 digits, month, status - several words narrow it down). The Edit / delete a statement list follows the same filter. Turn the switch off to see everything.

## 2026-09-21
- **Issue a check: the date starts as the invoice due date** - in a batch's Checks tab the Check Date now defaults to the invoice's due date (the earliest unpaid one if the batch has several) instead of today. You can still change any date.
- **Create schedule from existing checks** - on a loan note whose checks aren't tied to its repayment schedule, a new section gives each check its own schedule row (date = check date, amount = principal, interest 0) after you confirm. Nothing is saved if the checks add up to more than the loan's principal.
- **Correct a printed loan note check** - under a loan note's Checks tab there is now a "Correct a numbered check (number, date or amount)" section. Change the check number, date or amount right in the table and Save; every change is logged. It warns you if the change no longer matches the repayment schedule.
- **Retract a wrongly assigned check number** - in Assign a check number there is now an "Assigned the wrong number? Retract it" section. It takes the number off the check and, if it came from a booklet, puts the leaf back with no false gap.
- **Assigning a check number is safer** - a number you type directly now always wins over the booklet dropdown (it used to be ignored), you get a confirmation showing exactly which number will be used, and the typed box clears after each assignment.
- **Chrysanth CSV export** - the checks-to-print export is now a CSV in Chrysanth's own layout (Date, Account No, Payee, Amount, Cheque No, Remark, A/C Payee Only). Every check is marked A/C Payee Only = Y, Account No is left empty, and Cheque No stays empty until a number is assigned. Fixed: the download button used to disappear the moment the batch was logged; it now stays, and every past batch in Batch run history has its own Download CSV button.
- **Credit card payments come from the next statement** - you record statements only; the balance carried over on the next statement tells the system what was paid on the earlier one. The finance charge field is available on every statement.
- **Finance charge on every statement** - the simple entry mode now also has an optional field for the finance charge shown on the statement, so card interest feeds the cost-of-financing figures.
- **Credit card payable no longer double counted** - a balance carried over from one statement into the next is now counted once. Your current numbers do not change.
- **Smarter invoice-number matching** - SI-00123, SI 123 and 123 are recognised as the same invoice number, and a pop-up warns when you add one that matches an invoice already on file.
- **Possible duplicates report** - Reports > Possible duplicates scans every supplier and batch (migrated ones included) for the same number written differently, the same amount on the same date, and batches with the same total. Tick a finding and mark it as not a duplicate to stop it showing.
- **Startup check** - if the program has been updated but the database has not, the system now says so in plain words and points to the new Update Database file (update_database.bat), instead of showing a confusing error.
- **Credit card payment confirmed by the next statement** - when you add a statement you can leave the amount paid as unknown (ticked by default). It stays counted as owed and is marked 'Not yet confirmed'. When the next statement arrives, type the balance it shows as carried over and the system works out how much was paid on the earlier one and updates its status.
- **Schedule of payments sorting** - within each day the schedule now lists Lender, Bank Loan, Supplier, then Credit Card, and the smallest amount first inside each.
- **Simple credit card statement entry** - when adding a statement there is now a switch, "I only have the total amount due and the due date". Turn it on and you type just the total amount due and the due date (and any amount paid); previous balance, new charges and interest are left blank.
- **Overview report** - Reports > "Overview - highlights & insights": pick Day, Week, Month, Quarter or Year and see highlights written in plain sentences, paid vs scheduled, where the money went by track, top payees, cost of financing, payment behavior, what is coming up next period and a watch list, all compared with the previous period. Short and full versions, both with a PDF. The PDF now has a cover page (logo, period, three headline numbers, contents) and bar charts - trend over the last 6 periods, paid vs scheduled by track, top payees, coming up by week and interest by source (PDF only; the screen stays as tables).
- **Loan-note check warning quieter** - migrated loan notes that have no repayment schedule on file are no longer flagged for having checks not tied to a schedule row.
- **Bank names in full** - banks appear as BDO Unibank, Inc., Bank of the Philippine Islands, Philippine National Bank and so on everywhere; typing an abbreviation like BDO still finds the right bank.
- **Creditor names in proper case** - supplier, lender and bank names read in proper case (BDO Unibank, Inc., CSS Autoparts Inc.). Names you type in ALL CAPS or all lowercase are tidied automatically; a name typed in mixed case is left as you wrote it.
- **Dates shown in your local time** - created dates and times (batches, creditors, audit log, print times) now show in local time, so the "Today" filter includes what you created after midnight.
- **Excel invoice-entry form** - a macro-enabled Excel workbook with a pop-up form (supplier list, batch tab, Doc Ref, date, amount, Less %) that writes each invoice to the right supplier tab, warns on a repeated Doc Ref, and produces exactly the workbook the multi-batch import reads. Files are in the excel_invoice_entry folder. The workbook can now be downloaded from the system (Order Statement > New Batch > import section), with the supplier list refreshed at the moment of download.
- **Import many batches from one Excel file** - on the Order Statement tab (New Batch), upload one workbook where each sheet is one batch of invoices, named after its supplier. The system matches each tab to a supplier, shows you a review, then creates a new batch per tab, imports its invoices and (optionally) promotes it to Issued. The same supplier can have several tabs.
- **Promote right after import** - when you add invoices to a draft batch from a spreadsheet or photo, a checkbox (ticked by default) also promotes the batch to Issued in the same step.
- **Shorter draft numbers and named templates** - new draft batches are numbered like DRAFT-26-09-01 (year, month, then a running number that restarts each month) instead of a long string of digits, and the invoice CSV template downloads as SupplierName_DraftNumber_invoice_template.csv. Existing drafts keep their old numbers.
- **Double-recording reminder** - before saving, a pop-up asks whether you still want to continue if something looks already recorded. Most important: supplier invoices and checks, loan-note checks, bank loan monthly payables, and credit card statements (same card, due date and amount due). Also new loan notes, bank loans, cards, suppliers and credit memos.
- **Loan note checks must match the schedule** - each check has to equal its installment's principal + interest on its due date. A warning shows on the loan note if any check doesn't, and a pop-up asks before you save an edit that breaks the match.
- **Cancel bank loan** - a bank loan with a schedule can be cancelled: it stays on record but stops counting as an amount due.
- **New reports** - batch rundown and invoice rundown (paid / partially paid / pending, with running balance and PDF), plus PDFs for the three master rollups. PDFs now fit their text at 9pt.
- **Supplier terms** - 140 and 150 days added; new suppliers default to 120 days.

## 2026-09-17
- **Analytics tab** — AP composition/trend by track, supplier/lender/bank-loan concentration (Pareto), payment behavior, and cost-of-debt comparison, with a track filter and adjustable time window.
- **Fixed "Total AP outstanding"** on the Dashboard — it was badly undercounting (₱21.9M shown vs. the real ~₱61.6M) because it was reading from an old, unused table. Now accurate.
- **Mini calendar** in the sidebar — visible on every tab, for checking what's already scheduled while you're picking a check date.
- **Supplier concentration alert** — a warning banner if one supplier ever makes up more than 25% of what you owe.
- **Excel export for Chrysanth** — checks & checkbook now generates an Excel file of pending checks to import into Chrysanth's batch printer, replacing this app's own check-printing (which is now hidden).
- **Batch run history** — a log of every Chrysanth export batch: when it went out, which checks, check numbers/booklets once assigned.
- **Sandbox indicator** — an orange banner and tab title make it obvious when you're looking at the sandbox instead of the live app.
- **"App started" timestamp** in the sidebar — confirms a restart actually picked up the latest changes.
- Typo protection on money fields — loan notes, bank loans and credit card limits now reject amounts under ₱1 (a mistyped number field used to save silently as something like ₱0.001), and show a "please confirm" summary with the amount spelled out before saving. A bank loan's maturity date must now be after its start date.
- **Missing statement check** — Credit cards tab: compares last month with this month for every active card and lists the ones still missing a statement, grouped by card holder with a ready-to-copy follow-up message for each person, plus Excel/CSV download.
- **Card issuer is now just the bank name on the card** — pick it from the list or type a new one when adding/editing a card; the separate "Add / Edit card issuer" screens are gone.
