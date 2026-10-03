# Slick's system: pilot guide and workflows

Written 2026-10-03, for the pilot test before anything is deployed. The live AP is not touched by any of this: the pilot runs on a clean database of its own.

---

## 1. What you are testing

The new main system (the "ERP copy", Streamlit, `C:\Slicks\Slicks_ERP`, branch `erp`). It covers the whole payables cycle:

**price list → purchase order → receive goods → match the supplier's invoice → batch → park checks → Chrysanth file → printed and numbered → statement with release slip → supplier collects → bank clears it**

plus private lenders, bank loans, credit cards, the bank statement, the payment calendar, reports, and the people who use it (seven roles).

Not in the pilot: sales and stock screens (the tables are ready, no screens), the Django site (parked), moving the live AP data in (done only when the owner says the system is OK, with a backup).

**Test status before the pilot:** 1,102 automated tests pass (every page for every role, every mode and tab, start-to-finish stories for each workflow, buttons pressed as in a browser). A dry run on a clean database worked: first-run set-up, sign-in, forced password change, a role seeing only its own menu.

---

## 2. Starting the pilot

1. Double-click **`run_pilot_streamlit.bat`** in `C:\Slicks\Slicks_ERP`.
2. Open **http://127.0.0.1:8506** in the browser. (Only this laptop can open it. To let the brother try it on the same Wi-Fi, change `127.0.0.1` to `0.0.0.0` in the .bat, then he opens `http://<this laptop's address>:8506`.)
3. The first time, the system builds a clean database (`data\ap_disbursement_PILOT_TEST.db`) and shows **Welcome**. Create the **System admin** account (your email, name, a password you choose).
4. Sign in. An orange **SANDBOX** tag in the top bar tells everyone this is test data.
5. **Settings → Users:** add the people (see section 3). Each new person gets a temporary password, shown **once**; give it to them. At their first sign-in they must choose their own.
6. To start over: close the window, delete `ap_disbursement_PILOT_TEST.db` in `data`, start again.

Things to know:
- **Refreshing the browser (F5) or opening a link in a new tab signs you out.** Move around with the top bar, not the address bar.
- Five wrong passwords lock that account for 15 minutes. The System admin can give a new temporary password.
- Keyboard: **Alt+1 … 0** open Payment run, Add data, Order statements, Checks, Bank and cash, Payables due, Lenders, Bank loans, Credit cards, Reports. **Alt+H** is Home. **/** opens Search everything.
- The pilot copy and the test copy of live data (`run_test_streamlit.bat`, port 8502, sign-in skipped) are separate. Do not mix them.

---

## 3. Who can do what

| Role | Who | Sees and does |
|---|---|---|
| **System admin** | JRCR only | Everything, plus Users and roles, Settings, the audit log, data fixes, the Developer view (look at the system as another role) |
| **Owner** | Family members | Every business action: orders, receiving, invoices, checks, bank, loans, reports, release. **Not** Users, Settings, Admin/audit, data fixes |
| **Payables** | Clerk | Invoices and batches, park checks, receive and match, bank and cash, loans and cards, release, calendar marks. Cannot print/number checks (Chrysanth) or void |
| **Purchasing** | The brother | Price lists, discounts, his own orders, receiving goods. Sees only the **Buy** menu |
| **Releaser** | The mother | Release checks (by slip), receiving goods. Sees Home and Release checks |
| **Sales** | Counter (later) | Reserved for the Sales module |
| **Viewer** | Read-only | Reports, prices, order list |

Full table: **Set-up → Roles and controls → Who can do what** (read-only, for the System admin and the owners).

**How the system treats mistakes.** It guides and records; it rarely blocks. Only four things are always refused: a check above the batch balance; the same invoice number twice for one supplier; a check number used twice; releasing a check that was never printed. Everything else goes through and, if worth a second look, lands on the **Review list** on Home (the System admin or an owner ticks it OK). The full list is under **Roles and controls → Controls**.

---

## 4. Finding your way

- **Top bar** (hover a section): **Buy** (Price lists, Purchase orders, Receive goods) · **Pay suppliers** (Match invoices, Order statements, Payment run, Checks and checkbook, Release checks, Payables due, Add data) · **Bank** (Bank and cash) · **Other payables** (Lenders, Bank loans, Credit cards) · **Records** (Reports) · **Set-up** (Creditors, Roles and controls, Settings, Admin and audit, Data health).
- **Right side:** search, the payment calendar pop-up, reminders (checks typed but not parked), your account (sign out, change password, Refresh data, keyboard shortcuts).
- **Home:** *My to-do* (only the steps you may do now), *Needs attention*, *To review* (the Review list), then a tile per page with its live figure. A tile with a red mark is urgent.
- **Cycle bar** on the order, batch, checks and bank pages: Order › Receive › Match › Batch › Check › Bank. After a step, buttons offer the next one.

---

## 5. The workflows

Each one: who does it, where, what to do, what you should see. "Test" lines are what to check in the pilot.

### W1. Set up a supplier, prices and discounts
*Who: System admin / Owner / Payables (supplier), Purchasing or owners (prices).*
1. **Pay suppliers → Add data → Records and checkbook → New creditor.** Name, type *Supplier*, terms (default 150 days). A supplier added by anyone but the System admin is flagged on the Review list; it is usable at once.
2. **Set-up → Creditors:** edit terms, **Usual delivery days** (a new order's delivery starts that many days after the order date).
3. **Buy → Price lists → Import Excel:** download the blank template (sheets Tires, Battery, Mags, Rims), fill it, upload, read the preview (new / changed / same, problems by sheet and row), import. Or **Add a price** for one item.
4. **Price lists → Discounts:** one chain per supplier and brand, for example `5, 5` = less 5%, less 5%. Net = list less each discount in turn (7,600 → 6,859.00).
5. **Prices → Show:** *This supplier*, or *All suppliers side by side* (cheapest marked; the catalog of items TR-/BA-/MG-/RM- is managed there).

*Test:* the net price on the Prices tab equals list less each discount; two suppliers' prices for the same item show side by side.

### W2. Buy: purchase order
*Who: Purchasing (his own orders), owners, System admin.*
1. **Purchase orders → New order.** Supplier; order date; expected delivery (starts at the supplier's usual days).
2. Under **Payables when this order falls due** you see what is already committed that week and day, with this order added; pick a lighter week or day if you want.
3. **Add an item** (list price, discount from the supplier's chain, net), then **Save and order** (or Save draft).
4. **Guard rail:** if a *daily payables limit* is set (**Settings → Purchasing**), a day that would pass it shows a warning and a button for the nearest lighter delivery day. It never blocks; ordering anyway is noted on the Review list.
5. Open the order: **Download PO (PDF)** for the supplier. Cancel (own orders), Close short (partly delivered, rest will not come).

*Test:* order total = quantity × net price; the PDF shows List / Discount / Net; the order appears in *Payables due* on its pay date.

### W3. Receive goods
*Who: Purchasing, Payables, Releaser, owners.*
- **Goods only:** Buy → **Receive goods** (or Purchase orders → Receive goods). Tick what arrived and the quantity; choose *Delivery receipt* (its number) or *Invoice* (for a supplier who gives no delivery receipt). The goods count as stock and stay counted as owed until the invoice is matched.
- **Goods and the supplier's invoice together** (Payables and owners, when the invoice came with the delivery): choose *Goods and the supplier's invoice*; tick, check the actual prices, choose the batch, **Add to the batch**.
- If the buyer receives his own order, it goes through and is flagged on the Review list.

*Test:* order status moves to *Partly received* / *Received*; stock (on hand, average cost) rises by the quantity.

### W4. Match the supplier's invoice
*Who: Payables, owners.* **Pay suppliers → Match invoices** (or the *Match the invoice now* button after receiving).
1. Pick the supplier; goods waiting for an invoice are listed. If the invoice came with the goods, those lines arrive ticked and the invoice number and date are filled in.
2. Correct the **actual price** where the supplier charged differently; add the invoice total if it differs (freight, rounding); pick the batch (the supplier's open Draft is preselected).
3. Optionally tick *Update the price list*. **Add to the batch.**

*Test:* the invoice appears in the batch at the price charged; the order shows ✓ (same as estimate) or Δ; stock cost is corrected to the invoice price; a price more than 10% above the order is flagged. **Prices → This supplier** then lists *Different from the last invoice* with an *Update the list* button.

### W5. Batches, credit memos and parking checks
*Who: Payables, owners.* **Pay suppliers → Order statements.**
1. Browse batches / New batch. A supplier with no open batch gets one automatically when picked in Add data.
2. **Invoices** and **Credit Memo** tabs. Invoices can also come from a photo/PDF or spreadsheet.
3. **Checks** tab: *Park checks* (number of checks, dates, amounts; one check for one invoice needs no allocation grid). The confirmation also confirms any credit memos. A check above the batch balance is refused.
4. A batch locks by itself when its checks cover it fully, and unlocks if you void or correct a check.

*Test:* the batch balance falls by each parked check; the batch locks at zero balance.

### W6. Chrysanth file, printed, numbered
*Who: owners and System admin (Payables parks, owners print).* **Pay suppliers → Checks and checkbook.**
1. First time only: **Chrysanth settings** (account number as set up inside Chrysanth, crossing).
2. **Checks to print:** choose the parked checks, **Generate Chrysanth file (.txt)**; print in Chrysanth.
3. **Batch run history:** select the run, **Mark as printed**. In the same panel type the **First check number of this run**; the others follow in the listed order; review; **Give these numbers**.
4. Other things on this page: *Assign check numbers* (a backfill), *Log a spoiled check*, the *Voided, replaced and spoiled* register, guarantee checks.

*Test:* checks leave *Checks to print*; numbers are in sequence; a number used twice is refused.

### W7. Statement, release slip, release
*Who: statement printed by owners/Payables/Releaser; release recorded by the Releaser.*
1. **Order statements → Print this batch** (button at the top). The thermal statement prints **three copies**: *Supplier*, *Filing* (attach to the sales invoice original) and *Finance*. Each ends with **Release slip: RS-yyyy-nnnn**, the line "Please issue a collection receipt. Ensure everything stated in the statement is included.", and Received by / Date / Signature. (A4 PDF is also there. In the pilot the printer is the test printer, no paper.)
2. The supplier collects the checks and signs.
3. **Release checks:** pick the slip number, type who received, the date, **Release this slip**. Every check on the slip is marked released at once.
4. **Loan notes** have their own slip: **Lenders → the loan note → Checks → Release slip for the lender**.
5. Home warns when a slip has waited over a week; **Payment run** shows a *6 · Release* stage.

*Test:* printing the statement again keeps the same slip number; releasing an unprinted check is refused.

### W8. Bank: statement, reconciliation, clearing
*Who: Payables, owners.* **Bank → Bank and cash.**
1. **Import a statement** (the bank's CSV). Checks match automatically by number and amount; near-matches become suggestions (accept/reject); everything else lands in *Need a look*, where each line is tagged or resolved.
2. A bank loan installment matches a debit memo of the same amount within 5 days; a **credit card statement is marked Paid by the bank's bills payment** of exactly its total.
3. **Reconciliation** shows the bank balance less checks not yet cleared. Checks past their date and not cleared are listed in **Payables due → Past their date** (one list).
4. Data health flags a bank loan installment past due with no bank debit.

*Test:* a cleared check shows its cleared date; the card statement turns Paid on the bank's date.

### W9. Private lenders (loan notes)
*Who: Payables, owners.* **Other payables → Lenders.**
1. **Lenders → New loan note** is the loan note generator: enter the **lender**, the **principal**, the **loan date**, the **monthly interest** (it starts at this lender's last rate) and **when to start paying the principal** and **how many principal payments**. Payments fall monthly on the loan date's day; the months before the principal starts pay interest only. The sheet shows as you type: LN. No., date, principal, monthly interest; Loan Schedule / Payment Schedule / Difference; the loan schedule (with its opening row) and the payment schedule (check numbers fill in later). One button makes the note, its real LN number and its whole schedule. Example: 200,000 at 3% from Oct 1 2026, principal from Jan 1 2027, 4 payments gives 6,000 · 6,000 · 56,000 · 54,500 · 53,000 · 51,500 (total 227,000).
   Choose **I will upload or type it later** to create the note alone.
2. **Repayment Schedule tab:** the same sheet for the saved note. A note with no schedule can still use *Make the whole schedule in one go* (now with *Interest-only months first*), add installments one by one, or **import a spreadsheet (Excel/CSV)**. **Generate Loan Note PDF** prints the sheet.
3. **Checks:** *Issue PDCs for all pending installments* (one confirmation) or one by one.
4. Payment run lists installments due within 45 days that have no check.
5. Printing, numbering and release work as for suppliers (W6, W7).

### W10. Bank loans and credit cards
- **Other payables → Bank loans:** create the loan; **Same amount every month: fill the schedule in one go**, or import. Installments are matched to the bank's debit (W8).
- **Other payables → Credit cards:** cardholders and cards; **Statements → Add statement** (the card says its bank; the due date starts a month after the last). Missing-statement check; "No due" marks.

### W11. Other checks
**Add data:** *Handwritten check* (recorded with its real number, never goes to Chrysanth; the check starts from the invoice amount and due date), *Guarantee check* (given to a lender as security, not a payment), *Spoiled check*, *New checkbook booklet* (the next booklet is prefilled from the last).
Void or replace a check from the batch or loan note; the money returns to the batch.

### W12. The payment calendar
Calendar button in the top bar (or **Payables due → Calendar**). Click a day to see what is due. Owner/Payables: mark it **Holiday**, **No-check day** or **Note**. A payment due on a holiday or no-check day is **paid the next bank day** (Payables due, the calendar, Payment run, the reports); new orders and the planner skip it. An invoice's stored due date and a written check keep their dates. Philippine holidays are already marked.

### W13. Reports, Payables due, Data health
- **Payables due:** Week by week (with the next 7 days), By supplier, Past their date, Calendar, **Everything owed** (all tracks, with CSV/Excel/PDF downloads).
- **Reports:** Overview, Analytics, Possible duplicates, Cleared checks, **Statement of account** (supplier / supplier rundown / private lender / bank loan / credit card), Master rollups, Lists, Decision reports, My reports.
- **Data health:** records that look wrong; open one to fix it, or mark it "It's fine". On a brand-new database it shows one item, "audit log not checked yet", until the daily backup runs.

### W14. People, roles and the audit
*System admin only.* **Settings → Users:** add a person (role, temporary password shown once), give a new temporary password, switch off, change role. The last System admin can never be switched off or demoted. **Settings → Numbers:** daily payables limit, stale-check days, bank alert settings, notice on Home, and more. **Admin and audit:** booklets, backup, the audit log. **Developer view** (account menu): look at the system as another role without changing anyone's role.

---

## 6. Pilot checklist

Tick these as you try them. Note anything that surprises you.

**Start**
- [ ] Pilot starts from `run_pilot_streamlit.bat`; Welcome appears; System admin created; signed in
- [ ] Brother and mother added; each signs in with the temporary password and must choose their own
- [ ] Brother sees only *Buy*; mother sees Home and *Release checks*; Payables sees Pay suppliers; an owner does not see Settings or Admin

**Buy**
- [ ] Supplier added; Excel price template imported; discount set; net price correct
- [ ] Order made by the brother; guard rail warns (set a small daily limit to try it); PO PDF downloads
- [ ] Goods received (goods only), then invoice matched; stock and price difference as expected
- [ ] Invoice received with the goods in one step (Payables)

**Pay**
- [ ] Batch with invoice and credit memo; checks parked; balance and lock behave
- [ ] Chrysanth file made; run marked printed; numbers in sequence from the first
- [ ] Statement printed in three copies with the same slip number; slip released by the mother
- [ ] Bank statement imported; checks clear; reconciliation reads right

**Loans and cards**
- [ ] Loan note: schedule in one go, all PDCs in one press, slip, release
- [ ] Bank loan: flat schedule; bank debit matched. Card statement paid by the bank line

**Controls**
- [ ] Hard stops: overpaying a batch, a duplicate invoice number, a repeated check number, releasing an unprinted check are all refused
- [ ] Review list shows the buyer receiving his own order and a new supplier added by Payables; an owner ticks them OK
- [ ] A holiday marked on the calendar moves a payment to the next bank day (and back when unmarked)

**Daily feel**
- [ ] Is every step in the order you expect? Anything you had to look for? Anything you typed twice?
- [ ] Speed: does any page feel slow? (Say which.)

---

## 7. Known limits

- A browser refresh signs you out (see section 2). A "stay signed in" option can be added if this gets in the way.
- Thermal printing uses the test printer until the printer arrives; the statement's QR copy is a local file until Google Drive is connected (`drive_connect.bat`, done before deploying).
- The cash forecast and the check-date planner are hidden until Sales brings real deposits (Settings → Cash forecast brings them back).
- The Dashboard and Analytics pages no longer exist (their content moved to Home, Payables due and Reports).
- Sales, customers and stock tables are ready, with no screens. The parked Django site is not part of the pilot.
- A marked holiday does not change an invoice's stored due date or a check already written, only the day the payment is shown and totalled.

## 8. Before deploying (not done yet)

1. Pilot findings fixed, and the checklist above ticked.
2. A backup taken and restored on a copy, to prove it works (`run_backup.bat`, `restore_test.bat`).
3. The owner says "main is OK" (Roles and controls → *Is main OK?*), then the live data is migrated in with a backup and the owner's go-ahead.
4. Server, domain and accounts only after that, per the owner's rule.

## 9. When something looks wrong

Write down: who was signed in (role), the page and the steps, what you expected, what happened, the time. **Set-up → Data health** and **Admin and audit** (audit log) help trace it. A red box with a long text is a program error: copy that text.
