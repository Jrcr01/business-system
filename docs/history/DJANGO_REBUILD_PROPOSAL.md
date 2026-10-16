# Proposal: rebuild Slick's system on a fresh Django project

Prepared 2026-10-03 for JRCR's approval. Nothing in this proposal is built until it is approved.

## 1. Why

- **The requirement:** many people use the system at the same time, remotely, and it runs stably. The users are the family owners, the Releaser (mother) and staff.
- **Streamlit can't meet it:** it was right for designing and piloting the workflows, but it signs people out on refresh and keeps a separate copy of the app running for every open browser.
- **Django is built for exactly this requirement.**
- **The old Django site was removed** (2026-10-03) because its modules are to be redone. The new project starts clean.

## 2. What we start from

- **The Streamlit system is the prototype and the spec.**
  - Its 30 screens and their workflow tests describe exactly what each Django screen must do.
  - The pilot (8506) and the test copy (8502) keep running while Django is built.
- **The business rules carry over unchanged.**
  - About 20,000 lines in `engines/` cover checks, releases, loans, cards, bank matching, controls and reports.
  - They are plain Python with no Streamlit code, and Django calls them directly.
  - The 971 existing tests keep guarding them.
- **The access rules carry over unchanged.**
  - The roles (admin, owner, sales, payables, purchasing, releaser, viewer), the 4 hard stops and the review-list flags are already decided and coded in `engines/permissions.py` and `engines/review_flags.py`.
- **The live AP (`C:\Slicks\AP_System`) is never touched.**
  - It keeps running until you say the new system is OK.
  - Its data then moves in once, with a backup taken first.

## 3. How the new system is built

| Part | Choice | Why |
|---|---|---|
| Framework | Django | Built for many users at the same time; mature and stable |
| Screens | Pages drawn on the server plus HTMX for quick in-page updates | Fast and simple, with no separate app to maintain; works on phone and laptop |
| Sign-in | Django sessions | Stays signed in after a refresh or a dropped connection; signs out after an idle time you set |
| Roles | The approved access rules | No new rules to learn |
| Two people saving the same record | The second person is told "this changed, reload", so neither change is silently overwritten | Safe simultaneous use |
| Hard stops (check above balance, duplicate invoice, check number reuse, releasing an unprinted check) | Checked inside the save itself | Cannot be bypassed by two people saving at the same moment |
| Database | **Decision for you: see section 6** | |
| Look | The current design (top bar with hover menus, Home tiles), redrawn in Django | Your team already knows it |

## 4. Stages

Each stage ends with your sign-off before the next one starts.

| Stage | What gets built | You sign off when |
|---|---|---|
| **D0 Foundation** | Project set-up, sign-in, roles, top bar, Home, audit log, sandbox copy. A **load test: 10 simulated users working at once** | You sign in from two devices at once; the load test passes with no errors and no lost saves |
| **D1 Masters** | Suppliers, lenders and lessors, banks, lists, settings, users | You add and edit masters from two devices |
| **D2 Buying** | Price lists, purchase orders (with the daily payables guard rail), receive with invoice, review flags | One order goes through, ordered then received then invoiced |
| **D3 Payables** | Add invoices, order statements, payables due | The payables totals match Streamlit on the same data |
| **D4 Paying** | Checks and checkbook, payment run, check-date planner, calendar with marked days, thermal print | A payment run is printed and numbered |
| **D5 Release** | Release slips in 3 copies (supplier, filing, finance), releasing by slip | Your mother releases a slip from her own sign-in |
| **D6 Loans** | Lenders and loan notes with the generator, bank loans, guarantee checks, handwritten checks | Your Oct 1 sample note matches your sheet |
| **D7 Cards and bank** | Credit cards, bank and cash, statement matching (the bank is the truth) | A month's bank statement reconciles |
| **D8 Oversight** | Reports, analytics, my to-do, roles and controls, admin and audit, data health | The reports match Streamlit on the same data |
| **D9 Go-live** | Move the live AP data in (backup first), server, HTTPS, nightly backups, auto-restart | You say the system is OK and approve the switch |

- **Sales and stock** come after D9, as the next module of the ERP.
- **Mock-ups first:** in D0 you approve a mock-up of the look. Any screen that differs from the Streamlit version gets its own mock-up before it is built.
- **Testing:** every stage gets automatic tests, plus a walk-through of its workflows done side by side with Streamlit on the same sandbox data.
- **No server or paid accounts** are set up until D9, as agreed.

## 5. What does not change

- The live AP system and its data.
- The workflows you approved: receive with invoice, one Prices screen, release slips, flag-don't-block controls, the bank being the truth, marked days.
- Commits happen only when you ask.

## 6. Decisions for you

1. **Database** (to settle before D0)
   - **A. Keep the current database type (SQLite) first, upgrade if the load test says so** *(recommended)*
     - All 55 business-rule files work unchanged, so the rebuild is faster.
     - It handles many readers at once and one save at a time; each save takes milliseconds, which suits about 15 users.
     - The D0 load test proves it with your own workflows.
     - If the test fails, we switch to PostgreSQL before any screens are built, so no screen work is lost.
   - **B. PostgreSQL from day one**
     - The strongest database for many people saving at once.
     - It needs a database server and adds about 2–3 weeks up front to adapt the 55 business-rule files.
2. **Look**
   - Keep the current design *(recommended)*, or have a new one drawn in D0.
3. **Pilot**
   - Keep piloting Streamlit while Django is built *(recommended)*: real feedback goes straight into the Django screens.

## Decisions taken (2026-10-03)

1. **Database:** keep SQLite first; the D0 load test decides.
2. **Look:** keep the current design.
3. **Pilot:** JRCR keeps piloting Streamlit while Django is built.

JRCR also noted that **fewer than 5 people will use the system at the same time for now.**

## D0 results (2026-10-03), waiting for sign-off

**Built in `web/`** (start it with `run_web_sandbox.bat`, http://127.0.0.1:8505):

- First-run set-up (the System admin makes their own account), Sign in, Change password and Sign out.
- The sign-in survives a refresh and a server restart.
- Switching someone off, or a password change, signs them out on every device.
- A temporary password must be changed first.
- The top bar shows only the menu each role may open, and pages a role may not open are refused.
- Home shows the same tiles and live figures as Streamlit.
- Screens still to be built say which stage brings them.
- Every save records the person signed in.
- Django keeps no tables of its own: it uses the same people list (`app_users`) and the same business rules as Streamlit.

**Load test** (`tools/load_test_web.py`): simulated people working at the same moment. Each one opens pages and saves a loan note with its whole schedule every round.

| Set-up | People at once | Result |
|---|---|---|
| One server process | 10 | Passed: every save there once, LN numbers unique, audit names right, no mix-ups. Most pages under 0.6 s |
| 4 server processes (the real server runs several) | 20 | Passed: slowest page 0.8 s |
| 4 server processes | 30 | One save waited over 10 s for the database and was refused cleanly. It exposed a bug (below), now fixed |

**What the load test found and fixed:**

- **Pages waiting 10–50 s.** Each time the last database connection closed, SQLite tidied its journal under a lock. The site now keeps one connection open.
- **A request that hung.** Django's development server queues only 10 connections. The real server will be gunicorn with several processes.
- **A half-made loan note.** When a save failed midway, a draft note with no schedule was left behind. The generator now cancels the note, and a test covers this case.

**Verdict:** SQLite is ample for fewer than 5 people at once, and for 20. No move to PostgreSQL is needed now.

## D1 results (2026-10-03), waiting for sign-off

The mock-up `docs/mockups/d1_masters.html` was approved, with keyboard shortcuts deferred to D8 and the "tiles open a menu" switch dropped.

**Built in `web/masters/`:**

- **Creditors:** a list with search, a type filter and "Show switched-off too", and one edit form per creditor with the delivery days in it.
  - "Did you mean …?" asks before a name already on file is added.
  - If someone else saved the creditor after you opened it, nothing is saved and the form shows their version.
  - The type is locked once the creditor has transactions.
  - Switch off and on; delete only when the creditor has no history.
- **Settings:** all the numbers, with Save and "Back to default", and the Home notice, which now shows on Home.
- **Users:** add a person (the temporary password is shown once), give a new temporary password, switch a person off or on, change a role. You can't switch yourself off or change your own role.
- **Who can do what:** the access table, read only.

**Business rules (shared with Streamlit):**

- Every creditor change is now in the audit log with who made it: added, edited (old → new), switched off or on, deleted.
- Saving and resetting a number now also check the settings permission inside the rules, not only on the screen.

**Tests:** 14 new D1 tests; the whole suite passes (1,000).

## D2a results (2026-10-03), waiting for sign-off

The mock-up `docs/mockups/d2_buying.html` was approved, and D2 was split into D2a (price lists) and D2b (purchase orders, receive goods, match invoice).

**Built in `web/buying/`: Buy → Price lists**

- Pick a supplier; the page remembers it.
- **Prices:** the prices in force on any date, with the price before and the % change.
  - **History** for each item, where a wrong price can be switched off (it stays on record).
  - **"Different from the last invoice"**, with "Update the list".
- **Add a price:** suggests catalog and price-list names.
- **Import Excel:**
  - downloads a blank template, or one filled with the supplier's current prices;
  - "Check the file" shows the new / changed / same / not-in-file counts and any problems, and saves nothing;
  - "Import" then saves the rows (keep or replace a price on the same date).
- **Discounts:** a live example as you type the chain.
- **Catalog:** suppliers' prices side by side with the cheapest marked, the products (discontinue or bring back), and "Add a product" with the category's fields.

**Roles:** Payables and the Viewer see the prices and the catalog only. Purchasing, the owners and the System admin change them.

**Tests:** 8 new tests; all 36 Django tests pass.

## D2b results (2026-10-03), waiting for sign-off

**Built in `web/buying/`: Buy → Purchase orders, Receive goods; Pay suppliers → Match invoices**

- **Orders:** the list (show by status, by supplier; late deliveries marked). Tabs: Going out (daily or weekly totals), Order to bank, Price differences. Orders over the cash plan show with a Noted button (only while the cash forecast is switched on).
- **One order:** the facts, the stages from ordered to cleared by the bank, the lines and the actions the role may use.
  - Actions: Order it, Download PO (PDF), Receive goods, Match invoice, Change, Cancel (with a reason), Close short.
  - "Follow this order to the bank" lists its invoices and checks; check details are hidden from roles that do not handle checks.
- **New order, saved as it is built:**
  - The supplier, the dates and the first item make the draft.
  - Then you add, change or remove items. Choosing an item fills its unit, list price and the supplier's usual discount.
  - The payables when the order falls due show by day or by week, with this order on its day.
  - Pressing a week moves the delivery so the order falls due in that week.
  - The daily-limit warning offers a lighter delivery day.
  - The week's list hides check numbers from roles that do not handle checks.
- **Receive goods:** tick what arrived and how many; delivery receipt or invoice number; date received.
  - Payables and the owners can also pick "Goods and the invoice" to do both at once.
- **Match invoice:** goods received waiting for an invoice (lines that came with that invoice arrive ticked).
  - Enter the actual price, the invoice number and date, an optional invoice total, and the batch (the open draft, or a new one).
  - Optionally update the price list from the invoice.
  - Goods not yet recorded can be received and invoiced in one step.
  - The invoice amount and its difference from the estimate add up as you tick.

**Business rules made safe for several people at once (shared with Streamlit):**

1. **Receiving the same delivery at the same moment** can no longer count it twice. The check now happens inside the locked save.
2. **Matching the same goods at the same moment** makes one invoice. The other person's invoice is taken back out, with a plain message.
3. **The same invoice number at the same moment** gets the usual "already exists" message, not a crash.
4. **Two people changing the same draft:** the second is refused ("someone else changed this order"). The fingerprint is `po_stamp`, and `save_po` takes `expected_stamp`.

`tests/test_po_concurrency.py` proves points 1–4 with two threads. Against the old rules, points 1–3 failed.

**Tests:** 10 new D2b tests and 4 same-moment tests.

## D3a results (2026-10-03), waiting for sign-off

The mock-up `docs/mockups/d3_payables.html` was approved, and D3 was split into D3a (the daily work) and D3b (the bulk tools).

**Built in `web/payables/`: Pay suppliers → Order statements, Add data, Payables due**

- **Order statements:** the batches, with search and filters by supplier, status and created date, and a New batch button.
- **One batch:**
  - Net, checks, balance and terms, with the steps (invoices, checks parked, sent to Chrysanth, numbered).
  - "Give it its OS number" while it is a draft with invoices.
  - Cancel batch and Edit this batch (reopen, with a reason): owners and the System admin only, as in Streamlit.
  - Tabs:
    - **Invoices:** add, edit and delete per row; the due date is worked out from the terms; the duplicate number is refused; a near double asks first; which invoices came from orders; and "From a purchase order" opens Match invoice with the batch chosen.
    - **Credit memos:** add, edit and delete, with the double check.
    - **Checks:** read only until D4.
  - Print as an A4 PDF (copies, history, payment application) or a thermal 80mm PDF.
- **Add data:** cards for each kind of record (later stages are marked). For invoices or credit memos, pick the supplier and the batch (the open draft is suggested, or a new one).
- **Payables due:** the next 7 and 30 days with the bank balance beside them.
  - **Week by week:** the next 7 days and 12 weeks of bars split by kind, with the table.
  - **By supplier** and **Past their date.**
  - Calendar and Everything owed say which stage brings them.
- **Two people at once:** each invoice and credit-memo edit carries a fingerprint of the row as shown. A save is refused when someone else changed the row since.

**Fixed in the shared rules:** deleting an invoice that had ever been edited crashed (FOREIGN KEY constraint failed), in Streamlit too. Its correction history now goes with it, and the audit log keeps every edit. An invoice matched to a purchase order is refused with a plain message, because the order's invoiced quantities point at it.

**Tests:** 13 new tests.

## Go-live decision (owner, 2026-10-03): side by side, as soon as possible

- **Streamlit:** JRCR: "stop editing the pilot streamlit ... focus here django because i want to use it live ASAP". The Streamlit screens (`views/`, `app.py`) are no longer changed.
- **Go live side by side.** On the server, Django is the main system. The project's Streamlit app runs beside it on the **same database** for the screens Django does not have yet: Paying, Release, Loans, Cards, Bank, Reports and Admin. Each later stage moves a screen across.
- **Day-one set:** D3b (the bulk tools), now built. Then the server and the move of the live data.

## D3b results (2026-10-03), waiting for sign-off

**Built in `web/payables/bulk.py`:**

- **Invoices from photos/PDFs** (read offline by Tesseract, which must be installed on the server too) **or from a spreadsheet**, from a batch.
  - Read, then check and correct every line, then add.
  - A double entry asks first. The batch can get its OS number in the same step.
  - The text read from each file can be shown when a read is bad.
- **Many batches from one Excel file:** one sheet = one new batch. The supplier is guessed from the sheet name, then created, with OS numbers if wanted. The macro entry workbook can be downloaded.
- **Print many batches as one PDF:** since the last check print / today / last 7 days / all; untick any; A4 (copies) or thermal; a summary page; history; printed checks only if wanted.
- **Vendor SOA matcher:** matched, missing in AP, amount mismatches, omitted from the SOA.
- **Where things wait:** what was read waits on the server between the two steps (`web_uploads/`, beside the database) and is removed when used or after a day.
- **Shared rule change:** the spreadsheet reader now also knows "Sales invoice no" / "SI number" column headers.

**Tests:** 7 new tests; the full suite passes (1,048).

## The supplier's sales invoice (owner's decisions, 2026-10-03)

- **The reference is the supplier's sales invoice (SI) number.** It used to be the delivery receipt. "Doc Ref" is now "Sales invoice no. (SI)" on the Django batch, Match invoice, the Streamlit invoice grid and Match box, and the printed statement ("Sales Invoice").
- **The DR number is kept beside it** in its own optional column (`invoices.dr_no`, migration 075, added to the four sandbox databases). "DR 77", "D.R. 77" and "DR-77" are all stored as "77". A DR recorded when the goods were received goes on the matched invoice by itself. The printed statement shows "SI-55 (DR 12)".
- **A purchase order is ideal but not required for now.** An invoice typed in with no order is allowed and tagged **No PO** on its batch, with a count.
- **"Put the invoice in" is optional when matching.** The default is "Not in a batch yet". Waiting invoices show on Order statements by supplier; a batch lists its supplier's waiting invoices to tick in; an invoice can be taken back out of an open batch while no check pays it.
  - The rules are `unbatched_invoices`, `put_in_batch` and `take_out_of_batch`; batching is checked inside the lock, so two people batching the same invoice at once cannot both win.
  - Streamlit still asks for a batch when matching.
- **Fixed on the way:** the DR cleaner must not eat the start of a word (for example "Driver").

## 7. Housekeeping before D0

- Commit the removal of the old Django site, when you say so.
- `deploy/` still points at the old Django layout. It will be rewritten in D9.
- `docs/SLICKS_ERP_BLUEPRINT.md` and `CLAUDE.md` still describe the old site. They will be updated in D0.
