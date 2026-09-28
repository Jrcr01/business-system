# sales — Sales + AR (module 2)

Read `erp/CLAUDE.md`, `erp/inventory/CLAUDE.md` and blueprint section 9 (every owner decision) first.

## S2 (done 2026-09-29) — counter sale, invoice booklets, Excel day sheet
* **The ERP records the BIR booklet invoices, never prints them.** `InvoiceBooklet` (label, first / last number, printed width);
  the oldest booklet not finished is IN USE and only its numbers can be used (one at a time — owner); it finishes by itself when
  every number is used or cancelled; the owner can close it early (unused numbers cancelled with the note). `CancelledNumber` =
  a spoiled leaf (Sales or Owner, reason required). **Gaps** = numbers below the highest used in a booklet, neither used nor
  cancelled: flagged on Booklets and the owner's Home, never blocking.
* **`Sale` + `SaleLine`:** number (unique), date (not future), customer (Walk-in by default; charge only for `credit_allowed`,
  due date = date + terms), ONE payment type (cash / check / e-wallet / transfer / card / charge; check needs bank + no. + date;
  e-wallet / transfer / card need the reference or approval code). Lines: item, qty (whole, >= 1), agreed price; cost = the item's
  average at that moment; profit per line; **below cost allowed**, flagged with the optional reason, reviewed by the owner
  (Below cost page; `mark_reviewed`). Each line takes stock out through `inventory.stock._move(kind=SALE)` in the same locked save
  (stock may go below zero — flagged). Never deleted; cancel / return comes with S3 (owner approval).
* **Rules in `sales/rules.py`** (`check_sale` = everything checked, nothing saved; `record_sale` re-checks inside the lock). Audit:
  SALE_RECORDED, SALES_BOOKLET_REGISTERED / FINISHED / CLOSED, SALES_NUMBER_CANCELLED, SALE_BELOW_COST_REVIEWED, SALES_DAY_UPLOADED.
* **Excel day sheet (`sales/daysheet.py`)** — NO macros yet (a macro workbook needs the owner's Excel VBA-trust setting; the owner
  decides): Day tab (B1 date, day totals row 2, rows 5.. = qty, item, price, cost / amount / profit by formula, customer, invoice
  no., payment, check / reference, below-cost reason) with dropdowns from hidden Lists (items with average cost, customers,
  unused numbers of the booklet in use, payment types); Cancelled tab. Upload -> `parse()` groups rows by invoice no. and runs
  `check_sale` per invoice (the formulas are never trusted — everything is recomputed); the checked day waits in the session;
  "Save the day" = `save_day()`: every sale and cancelled number in ONE locked save (re-checked; any change since the check =
  nothing saved).
* Pages (Owner + Sales; owner only: register / close booklets, Below cost): Sales (day or range, totals, by payment type), sale
  detail, New sale (Check then Save; next unused number suggested), Day sheet, Invoice booklets (number grid, gap list, cancel a
  spoiled leaf, register).
* **Not yet (next):** the macro pop-up form (owner's decision on the Excel setting); "released, invoice later" (goods out before
  the invoice); split payments (several types on one invoice); tagging sales for the carve-out (untagged = Business meanwhile).
  **S3:** collections, customer statements / aging, post-dated checks, deposits check, cancel / return approvals, write-offs,
  opening receivables.
