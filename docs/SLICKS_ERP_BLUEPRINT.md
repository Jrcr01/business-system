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
2. **Django skeleton** — the `erp/` project: Manila time zone, the shared database, security defaults, pinned versions.
3. **`core`** — users and roles (owner, sales), audit, numbering, shared lists: customers (suppliers and affiliates as party types)
   and items (tires), the carve-out tag list.
4. **Server + logins** — before your brother uses it from his own computer (HTTPS, Google sign-in with 2-step).

### Sales v1 — what it does (stable first)
* **Customers:** "walk-in" by default, plus named credit customers with terms, credit limit and contact details.
* **Items and prices:** tires (brand, size, pattern, code) and services if sold; one price list; only allowed roles change prices.
* **Counter sale screen** (fast, keyboard-first): customer, item lines (quantity, price, discount), the **booklet invoice number**
  (required, unique; booklet ranges registered, so missing / duplicate / out-of-range numbers are flagged), cash or credit.
* **Payments:** cash; checks (bank, number, check date → a list of post-dated checks to deposit); transfer / GCash / Maya (reference
  number); card (approval code); one sale can be paid several ways.
* **Credit and AR:** open invoices per customer, collections applied to invoices, customer ledger and statements, aging (who is late);
  the credit limit is checked — going over needs the owner's approval, logged.
* **Voids and returns:** never deleted; a void needs a reason and the owner's approval; a return becomes a credit note.
* **End-of-day cash-up:** expected vs counted per payment type, the deposit recorded, differences explained; the owner reviews.
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

**To decide**
* Server provider (DigitalOcean / AWS Lightsail / Vultr) and the exact domain.
* Where statement QR copies live (Google Drive or served by the app).
* The order of the modules after Sales.
* Whether the ERP ever becomes the official books (not planned).
* The GitHub repository name change (`slicks-ap-system` → `slicks-erp`) — at go-live.
