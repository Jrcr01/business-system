# Slicks ERP — Master Blueprint

*Slick's Tire Supply · first written 2026-09-28 · owner: JRCR · status marks: **Decided** / **To decide***

This is the map for the whole system: what we are building, how it is built, how it is kept safe, how it looks, what controls it
enforces, which modules come in which order, and the full plan for the next module (Sales). Every future piece of work starts here.

---

## 1. Vision and principles

**Slicks ERP is one system for the business and the family's affairs**, built one module at a time, "like a puzzle". The AP system
(module 1) is already live. **Sales + AR is module 2 and next.** AP and Sales are the heart of the business, so they come first.

Principles — these apply to every module:

1. **One system, many modules.** One login, one database, one look. A module goes live only when it is ready and stable.
2. **One source of truth per fact.** A customer, an item, a supplier is entered once and used everywhere.
3. **The bank statement is the ultimate truth.** When our records and the bank disagree, the bank wins.
4. **Never edit — void, reverse or adjust.** A recorded figure is never overwritten. A mistake is corrected with a tagged
   adjustment or a void, so the history stays visible. (Already the AP rule.)
5. **Every action records who and when.** Nothing happens anonymously.
6. **Controls are built in, not added later.** The screens themselves stop the common leaks (missing invoices, unrecorded cash,
   unapproved discounts, lost stock).
7. **Tailored to Slick's, not generic.** We build what this business and this family need — nothing more.
8. **Stable before new.** The live system is never put at risk by work on the next module.

---

## 2. What we borrow from commercial ERPs — and what we don't

**Decided (2026-09-28):** we do not copy a commercial ERP. There is no plan to hire an accountant; the goal is *to have a system in
place*.

**Borrowed, because it protects the business**
* Shared lists — customers, suppliers, affiliates, items — kept once, used by every module.
* Document numbering (batch numbers, sale numbers …) that never repeats and never skips silently.
* Approvals for risky actions (voids, big discounts, credit beyond a limit, price changes).
* Roles — each person sees and does only their part.
* A complete audit trail, and backups that are tested.
* Business intelligence: every module comes with the analysis needed to make decisions.

**Not built**
* General ledger, chart of accounts, journal entries, month-end close, financial statements.
* Multi-company / "software for other businesses" features.

Every record keeps its full details (amounts, payment type, customer or supplier, dates, purpose), so formal accounting could be added
later **if ever wanted** — it is not planned.

---

## 3. Architecture — how it is built

**Decided: Python + Django**, a "modular monolith" — one program made of clearly separated modules.

| Layer | Choice | Why |
|---|---|---|
| Language | **Python** | Everything so far is Python; the AP rules and ~400 tests carry over. Odoo, a major commercial ERP, is Python too. |
| Framework | **Django** | Commercial-grade: logins, roles and permissions, security protections, admin screens and database migrations built in. |
| Screens | Django templates + **HTMX** + a Tailwind-based component kit | Fast, professional screens without a heavy JavaScript app to maintain. |
| Database | **SQLite** on the server now; **PostgreSQL** later | Enough for fewer than 5 users. Moving to PostgreSQL is a settings change in Django — needed only at ~10+ users at once or if another program must read the database directly. |
| Server | A small cloud server in **Singapore**, HTTPS only, your own domain (Slick's Tire Supply name) | Staff (your brother) can use it from their own computer. Provider and exact domain **to decide**. |
| Mobile / other apps | Django REST framework — only if ever needed | Not planned. |

**How the pieces fit**
* One Django project (`erp/`), one **app per module**: `core` (users, roles, audit, numbering, shared lists, carve-out tags), `sales`,
  `ar`, `inventory`, `purchasing`, `affiliates`, `rentals`, `family`, and later `ap`.
* **AP during the transition:** the live AP keeps its current screens (Streamlit) and rules. The ERP shares the same database — the
  foundation work (one door, safe saves, one clock) makes two programs on one database safe. The ERP reads AP's records directly.
* **AP moves to Django after Sales is stable** (**Decided**): page by page, each new page running beside the old one until it shows
  the same figures; then the old page is retired. AP never goes offline. Benefits: one login and roles for everyone, AP linked to
  purchase orders / inventory / affiliates / carve-out, one way of building, stronger security.
* Money is stored exactly (no rounding drift); every business date is **Manila** time; records are voided, never deleted.

**Foundation already built (on the ERP branch, not live yet):** one door to the database (link checks, "wait instead of error",
read-while-saving), one Manila clock, safe saves for several users (a check can never overpay a batch even when two people issue at
once; numbers never collide), a broken-link check (live data: 0 broken).

---

## 4. Security / cybersecurity

**Decided: security is a first-class requirement.** Written as *threat → protection* for a small family business.

| Threat | Protection |
|---|---|
| Stolen or guessed password, phishing | Sign-in with Google, **2-step verification required for every user**; no separate app passwords; sessions end when idle; alert on a new device. |
| Someone seeing or doing too much | Roles and permissions per module and per action (your brother = Sales only). **Family / personal records visible to the owner only.** Separation of duties. Approvals for voids, discounts, price and credit changes. |
| Attacks on the website | HTTPS only (with HSTS). Django's built-in protections against SQL injection, cross-site scripting, forged requests (CSRF) and clickjacking. Login attempts rate-limited. Strict security headers. Only the web port open; server access by key only; automatic security updates. |
| Weak software parts | Every library version pinned. Monthly vulnerability check (`pip-audit`). Updates tried on the sandbox first. |
| Leaked secrets | Passwords and keys only in the server's settings file — never in the code or on GitHub. GitHub account with 2-step login; the repository stays private. |
| Ransomware, a lost laptop, a server failure | Encrypted backups in three places (on the server, off the server on Google Drive, the provider's weekly server copy); a restore test every month; the laptop's disk encrypted (BitLocker). |
| Misuse from inside | An audit log nobody can edit. Alerts for unusual activity (many voids or deletions, changes after hours). |
| Personal data (customers, tenants, family) | Data Privacy Act (RA 10173) basics: collect only what is needed, access by role, kept safe. |

A **security checklist** is run before every release, and the whole setup is reviewed once a year.

---

## 5. UI — professional yet functional

**Decided:** one look across all modules; the owner (the UI person) approves a **mock-up of every new screen before it is built**.

* **Layout:** left sidebar grouped by module; top bar with global search, alerts and the signed-in user; each page with a clear
  heading, key-number cards, then the working area.
* **Brand:** Slick's navy and the Inter font (kept from AP), calm colours, generous spacing, one main action button per screen.
* **Tables:** sort, filter, search, export; money right-aligned in ₱; dates as *Sep 28, 2026*; status badges with the same colours as
  AP (Draft grey, Parked orange, Issued blue, Paid green, Cancelled red).
* **Fast entry for the counter:** keyboard-first (Tab / Enter), barcode-scanner-ready, no unnecessary clicks; works on a tablet at the
  counter.
* **Clear feedback:** friendly error messages that say what to do, empty states that explain what goes there, confirmation for risky
  actions.
* **Print-friendly** pages and reports; accessible colour contrast.

---

## 6. Engineering practices — how changes are made safely

* **Gated steps:** each step is built, tested and shown to the owner before the next begins.
* **Two workspaces:** `C:\Slicks\AP_System` (branch `main`) = the **live** AP; `C:\Slicks\Slicks_ERP` (branch `erp`) = the ERP build with its own
  sandbox database. Use two Claude sessions: *"AP live — fixes"* on `C:\Slicks\AP_System`, *"Slicks ERP — build"* on `C:\Slicks\Slicks_ERP`.
* **Going live once per release:** backup → update → a checklist of known figures compared before and after → owner OK.
* **Tests for every rule**, including a fake clock (Manila time at any hour) and two-users-at-once tests.
* **Database changes only ever add** (new tables / columns); nothing is dropped or narrowed without a hand-checked plan.
* **"What's new" notes** for the owner in the app; **data-health checks** that flag suspicious records.
* **Definition of done for a module:** screens approved as mock-ups, rules tested, controls working, reports and analysis in place,
  security checklist passed, parallel run matched, owner sign-off.

---

## 7. Controls — not accounting

The checks a family business needs, built into the screens:

* **Money in = money recorded.** Daily cash count per payment type (cash, checks, transfers / e-wallets, card) against what the system
  expects; deposits intact; every customer check tracked from receipt → deposit → cleared by the bank.
* **BIR booklet number control.** Every sale records its booklet invoice number; missing, duplicate or out-of-range numbers are flagged.
* **Approvals** for discounts above a limit, credit beyond a customer's limit, price changes and voids — each logged with who and why.
* **Business vs personal money kept apart.** Owner withdrawals and family spending are recorded as such (see the carve-out).
* **Stock counted against the system.** Regular counts; every difference needs a reason and approval.
* **Bank reconciliation.** Money out already reconciled in AP; money in (customer deposits) added with Sales / later.
* **Simple owner reports:** sales, collections, who owes us, whom we owe, cash position and forecast.

**BIR note** (to confirm with the BIR or a tax adviser — not tax advice): the business is non-VAT (percentage tax). Keep issuing
invoices from the **registered pre-printed booklets**; the ERP *records* sales and their booklet numbers but does **not** print
invoices, so no registration of a computerized invoicing system is needed for it.

---

## 8. Module map — the puzzle

The system covers the business **and** the family's affairs.

| Module | What it does | Status |
|---|---|---|
| **AP** | Supplier invoices, batches, checks, lenders, bank loans, credit cards, bank reconciliation, planner | **Live** (Streamlit) |
| **Sales + AR** | Counter sales, payments, credit, collections, statements | **Next** (Django) — see §9 |
| **Inventory** | One stock ledger fed by every module: sales out, receiving in, affiliate borrow / lend, returns, counts and adjustments (with reason and approval). Stock by item, reorder levels, count differences. *Crucial — stock keeps getting lost.* | Later |
| **Affiliates** | Stock borrowed from / lent to affiliated businesses; settled by **returning stock, paying, or offsetting** against what they borrowed from us; a running balance per affiliate in pesos and in items | Later |
| **Purchasing (PO)** | Orders to suppliers (not tracked today) → receiving → the supplier invoice in AP; order vs received vs billed | Later |
| **Rentals** | Properties / units, tenants, rent due, collections, arrears | Later |
| **Family / personal** | Non-business spending and income | Later |
| **Carve-out** (across everything) | Every money record tagged **Business / Rental / Family-personal** (a record can be split). Every report can show them side by side, business-only, or all together. For AP the tags live in an ERP table that points at AP's records — AP itself is not changed. | Tag list in the foundation; used by every module |
| **Business intelligence** (in every module) | Sales: best / slow sellers, margins, customer ranking. Inventory: stock value, dead stock, shrinkage. AR: who pays late. Purchasing: supplier price trends. Rentals: occupancy, arrears. Overall: business vs family spending, cash position and forecast. | Grows with each module |

**Order** — **Decided:** Foundation → **Sales + AR**. **To decide** (suggested): Inventory + Affiliates (both move stock) →
Purchasing → AP screens into Django → Rentals → Family / personal with the carve-out reports.

---

## 9. The Sales module (module 2) — plan

**Who uses it:** mostly the owner's **brother** (sales); the owner approves and reviews.
**How sales happen:** walk-in customers at the counter; cash and credit; invoices written from **BIR pre-printed booklets**; payments
by cash, checks (including post-dated), bank transfer / GCash / Maya, and card.

### Foundation needed first (and nothing more yet)
1. **F1.5** *(done 2026-09-28)* — the audit log records *who* did each action; typing several check numbers at once becomes all-or-nothing.
2. **Django skeleton** *(done 2026-09-28)* — the `erp/` project: Manila time zone, the shared database, security defaults, pinned versions.
3. **`core`** — users and roles (owner, sales), audit, numbering, shared lists: customers (suppliers and affiliates as party types)
   and items (tires), the carve-out tag list.
4. **Server + logins** — before your brother uses it from his own computer (HTTPS, Google sign-in with 2-step).

### The revenue cycle as Slick's practices it (agreed with the owner 2026-09-28)
Started from a standard revenue cycle (customer set-up → counter sale → release → paid now or on credit → billing → collection →
deposit → bank check → reports, with exceptions at any step); the owner marked it up to match how the business really runs:

| Step | How Slick's does it | What the system does |
|---|---|---|
| **Credit customers** | Credit is the owner's own judgement — only trusted, usually loyal customers. No peso limits. | "Credit allowed" is a flag only the owner sets (logged). No limit; **alerts** when a customer has overdue invoices or a balance higher than usual. |
| **Prices** | A price list exists, but prices vary per customer and are often agreed **verbally** — sometimes at break-even or a loss to keep the customer. | **Record, don't block:** the seller types the agreed price; the system keeps list price, price charged, discount and who agreed. Below-cost sales go to a review list for the owner; reports show profit per customer. |
| **Invoice** | **Sales invoice** from the BIR booklet, for cash and credit sales. | The booklet number is required and unique; booklet ranges registered; missing / duplicate / out-of-range numbers flagged. |
| **Release** | Goods can leave **before** the invoice is written (e.g. installed first, invoiced later). | A "released, not yet invoiced" record; **must be invoiced the same day** — anything still open shows at the end of the day and to the owner next morning. |
| **Payments** | Cash, checks **including post-dated**, transfer / GCash / Maya, card. | Each method recorded; post-dated checks on a "to deposit" list by date; a bounced check makes the invoice owed again. |
| **Proof of payment (credit)** | When a credit customer pays, they receive the **original (white) copy of the sales invoice**. | The collection is recorded against the invoice number(s); the system tracks that the original copy was handed over. (Own collection number `2026 - CR 0001` kept internally.) |
| **Deposits** | The owner's **mother** handles cash — trusted — and deposits **several times a day** (cash is tight). | **Light check:** each deposit recorded against the day's cash sales and collections; deposits are **traced in the bank statement** (the existing bank import matches them). Differences shown for a glance — no formal count sheet. |
| **Returns, bounced checks, bad debts** | As in the standard cycle. | Credit note for a return (stock back); bounced check re-opens the invoice; write-off only with owner approval; nothing deleted. |
| **Affiliates** | The mother's siblings' businesses help each other with tires: borrow / lend, settled by **offset, paying as a customer, or returning** the stock. | In Sales: customer type **Affiliate**, so their purchases are tagged from day one. Borrow / lend / offset = the Affiliates module. |
| **Cost of tires** | There is a supplier price list; the encoder records what was **actually received**, at the actual cost — under, over or equal to the list. A **PO system** will be added. | Cost per tire comes from **receiving** (Purchasing / PO): actual received cost vs the supplier's list price, differences shown. Profit per sale and below-cost flags use that cost. |

**Today's sales record (the brother's notebook, owner 2026-09-28):** one line per item sold, copied from the sales invoice booklet:
date, quantity, item type (**tire, battery or mags**), profit (selling price − cost from the **paper price list**), and the customer's
name when it is a credit sale; plus the day's total quantity and total. **Not** in the notebook: the invoice number and how it was paid.
So the counter sale screen should feel like the notebook (fast item lines, profit shown per line, day totals) and add only what the
notebook lacks: the booklet invoice number (one invoice, several lines) and the payment type. The notebook is replaced after the
parallel run (S5) once the system's daily totals and profit match it.

**Improving the brother's workflow (owner 2026-09-28: "his way is tedious"):**
* **Excel batch entry, uploaded at the end of each day.** He downloads a fresh **template from the system** (current price list with
  list price and cost, customer list — both as dropdowns); types one row per item line like the notebook (date, invoice no., customer,
  item, qty, price charged, payment type / reference); the sheet works out **profit and the day's totals** as he types. Upload → every
  row checked before anything is saved (unknown item / customer, invoice number outside the open booklet or already used, below-cost
  price flagged not blocked, credit for a customer without "credit allowed") → he fixes and re-uploads → confirm = all rows saved or
  none (same pattern as AP's spreadsheet imports).
  * **Product reference table** in the template (hidden sheet from the system's price list): he picks the item once (searchable
    dropdown — he uses Microsoft 365 / Excel 2021+, so typing "185/65" narrows the list) and brand, size, pattern, category, list
    price and cost fill in; he types only quantity and the price charged (list price suggested).
  * **Pre-registered invoice numbers:** the template carries the open booklet's unused numbers; each row = one item line with the
    invoice no. picked from that dropdown (next number first); the same number on several rows = one invoice with several items (the
    sheet shows each invoice's total). Numbers not used today reappear in tomorrow's template (not a gap); a spoiled / cancelled
    paper invoice is marked "Cancelled" with a reason in the sheet.
  * **Macro entry form, like AP's `invoice_entry_master.xlsm`** (owner 2026-09-28): `sales_entry_master.xlsm` with an "Add sale"
    pop-up (button / Ctrl+Shift+S): invoice no. (unused numbers, next first), date, customer, payment type (+ check no. / date or
    e-wallet reference), item lines with product search, qty, suggested price and profit per line; Save writes the lines into the
    day's sheet with running totals (qty, sales, profit); a number can be marked Cancelled with a reason. Every download rewrites the
    hidden reference sheets (products with list price and cost, customers with credit allowed, the booklet's unused numbers) — the
    same pattern as `engines/invoice_template_service.py`. Built in S2 after its mock-up is approved.
* **Plus a quick screen** for a single sale or a correction.

**Opening balances, collections, returns (owner 2026-09-28):**
* **Opening receivables:** the brother encodes every unpaid invoice on the start date (customer, invoice no., date, original amount,
  balance still owed); **his entry is final** (owner's choice — no approval step), tagged "opening balance" and visible in reports.
  Old invoice numbers from finished booklets are allowed for these entries only.
* **Collections — all cases:** one invoice paid in full; several invoices with one payment; **partial payments**; a payment bigger
  than what is due (kept as an advance / unapplied, used on the next invoice). Any payment type (cash, check incl. post-dated, e-wallet,
  card). The original white invoice copy is handed over **only when the invoice is fully paid**; partial payments are recorded with
  date, amount and how paid, and the system lists which originals are still with the shop.
* **Returns:** a full return **cancels the invoice** and the items **go back to stock**; a partial return (e.g. 1 of 4 tires) keeps
  the invoice, puts **just that item back to stock** and lowers the invoice amount (a credit note). Nothing is deleted; each is logged
  with a reason.
* **Bad debts / write-off** (owner 2026-09-28): **owner only**, per invoice (or what is left of it), with a reason; the invoice is
  kept and marked "Written off", leaves the AR balance and aging; a later payment is recorded as a **recovery**; writing off turns the
  customer's credit **off** automatically; the customer shows a red **"Bad debt"** badge (with the amount written off) on the Parties
  list and page. Built with AR (S3).
* **Reports:** to discuss later.
* **Sales invoice booklet control, like the checkbook:** each booklet's range registered; **one booklet in use at a time**; every
  number used once (the database refuses duplicates); cancelled / spoiled invoices logged with a reason (all copies kept, as BIR
  requires); a **daily gap check** — any number skipped and not accounted for is listed for the owner the next morning. Numbers may
  be used out of order (flagged by the gap check, not blocked).

### Sales v1 — what it does (stable first)
* **Customers:** "walk-in" by default, plus named customers with terms and contact details; "credit allowed" set by the owner only
  (no peso limit — alerts instead); customer type Affiliate for the mother's siblings' businesses.
* **Items and prices:** tires (brand, size, pattern, code), **batteries, mags**, and services if sold (item categories); one price list (only allowed roles change it); the price
  actually charged is typed per sale and recorded against the list price, with who agreed.
* **Counter sale screen** (fast, keyboard-first): customer, item lines (quantity, price, discount), the **booklet invoice number**
  (required, unique; booklet ranges registered, so missing / duplicate / out-of-range numbers are flagged), cash or credit.
* **Payments:** cash; checks — **check details are required** when paid by check, especially a post-dated check (owner 2026-09-28):
  bank, check number, check date and amount (a sale / collection can't be saved without them; the Excel form asks for them too) →
  a list of post-dated checks to deposit by date; transfer / GCash / Maya (reference
  number); card (approval code); one sale can be paid several ways.
* **Credit and AR:** open invoices per customer, collections applied to invoices (original white copy handed over), customer ledger
  and statements, aging (who is late); alerts for overdue or unusually high balances.
* **Voids and returns:** never deleted; a void (cancelled invoice) needs a reason and the owner's approval and puts the items back to
  stock; a partial return becomes a credit note for just that item (back to stock).
* **Deposits (light check):** deposits recorded against the day's cash sales and collections and traced in the bank statement;
  differences shown for the owner to glance at. Released-not-invoiced items listed at the end of the day.
* **Reports and analysis:** daily / monthly sales; by payment type; by item and brand; best and slow sellers; sales by customer;
  collections vs credit given; late payers; checks to deposit; booklet number control.
* **Stock:** v1 records the quantities sold (it does not block a sale); the Inventory module later builds stock from these records.
* **Carve-out:** a sale is *Business* by default; the tag exists from day one, so nothing needs rework later.

### Build order (each step gated)
| Step | What | Owner's part |
|---|---|---|
| **S0** | Walk-through of a real sales day with your brother: booklets, price list, credit customers, opening balances (who owes what today). **Mock-ups of every Sales screen.** | Approve the mock-ups |
| **S1** | Customers, items and prices (+ importing your current lists) | Check the imported lists |
| **S2** | Counter sale + payments + booklet number control | Try it on the sandbox |
| **S3** | Credit, AR and collections | Check balances and statements |
| **S4** | End-of-day cash-up + reports and analysis | Check the figures |
| **S5** | **Parallel run** for 1–2 weeks: booklet and system side by side, figures compared every day | Sign off |
| Go-live | On the server, with logins | Go / no-go |

---

## 10. Decisions log

**Decided (2026-09-28)**
* Slicks ERP = one system for the business and family affairs; AP = module 1 (live), **Sales + AR = module 2 (next)**.
* Not a commercial-ERP copy; no general ledger / financial statements; controls and analysis instead.
* **Stack: Python + Django**; AP moves to Django after Sales is stable, page by page.
* Build in `C:\Slicks\Slicks_ERP` (branch `erp`); live AP in `C:\Slicks\AP_System` stays untouched; go live once per release.
* Security and UI are first-class; every screen is approved as a mock-up first.
* ERP records BIR booklet invoices; does not print invoices.
* Carve-out tags: Business / Rental / Family-personal.
* SQLite now (fewer than 5 users); PostgreSQL later if needed. Cloud server in Singapore.
* Domain follows the business name; one address for all modules; laptop → Google Drive backups; thermal printing = download and print.
* Early-pay discounts: not needed (not practiced in the Philippines).

* Revenue cycle as practiced (2026-09-28): credit = owner's list, no limits, alerts; verbal prices recorded not blocked, below-cost
  reviewed; sales invoice booklet; release before invoice allowed but invoiced the same day; post-dated checks accepted; the original
  white invoice copy is the proof of payment; deposits (by the owner's mother, trusted) several times a day, traced in the bank
  statement — light check only; affiliates tagged as a customer type; tire cost = actual received cost, from a PO / receiving system.

* **Cleared checks on the mother's phone** (owner 2026-09-28): the owner's mother confirms cleared checks today with pen and paper
  against the passbook / bank statement (the Schedule of payments matters to the family for this). Planned: a phone-first page for a
  new role **Checker** — she sees only the checks due (date, payee, amount) and taps **Cleared** (or undoes her own tick). Her tick
  marks the check cleared on that date right away (Schedule of payments and cash figures update); the bank statement import then
  confirms it, and a different date or amount is flagged for the owner — the bank statement stays the final truth. Access only once
  the ERP is on the server (F3), with her own login and 2-step verification (she does not want to use a laptop).

**To decide**
* When the PO / receiving system comes relative to Sales (it is the source of tire cost for profit and below-cost checks).
  Today the price list is updated by hand on paper; no cost per sale (no FIFO / specific identification). **Owner leans to the
  weighted average cost method** (average recalculated at each receipt from quantities on hand and received — needs receiving +
  inventory). Suggested: Sales first with the paper price list's cost as an estimate; receiving / inventory later switches every
  item to its weighted average (opening average = last price-list cost, checked by a stock count). To confirm.
* Server provider (DigitalOcean / AWS Lightsail / Vultr) and the exact domain.
* Where statement QR copies live (Google Drive or served by the app).
* The order of the modules after Sales.
* Whether the ERP ever becomes the official books (not planned).
* The GitHub repository name change (`slicks-ap-system` → `slicks-erp`) — at go-live.
