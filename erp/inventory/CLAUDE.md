# inventory — items and stock (Sales step S1, 2026-09-29)

Read `erp/CLAUDE.md` and blueprint section 9 first. Grows into the Inventory module later (counts with approval, affiliates, PO).

* **Items** (`Item`): category tire / battery / mags (services are sold but NOT tracked — owner), brand, size (tires) or model
  (batteries, mags), optional remark (tells two lots apart, e.g. "old stock 2023"); `key` = category|brand|size|remark normalised
  (unique); codes TR- / BA- / MG-#### made by the system; `reorder_level` default 2 (low stock at or below it).
* **Stock ledger** (`StockMove`): only ever added to — never edit or delete a movement; mistakes are reversed by a new movement.
  `Item.on_hand` / `avg_cost` are the running totals, updated in the same locked save by `inventory/stock.py::_move` (a test checks
  on hand = sum of movements). Kinds: opening, stock_in, stock_in_reversal, adjustment, sale / sale_reversal (S2 / S3).
* **Weighted-average cost** (owner): recalculated at each stock in / opening: (on hand x avg + qty x cost) / (on hand + qty); when
  nothing or less than nothing is on hand, the delivery's cost; a reversed stock in leaves at its own cost; sales and adjustments
  move at the current average (average unchanged).
* **Rules (owner):** a sale above stock is allowed — on hand goes below zero and is flagged for the owner; corrections only by the
  owner's "adjust to a count" with a reason; stock in by Sales and Owner (active AP suppliers only; numbered `2026 - RR ####`;
  Check shows on hand and the new average before Save; the owner reverses, never edits); Sales sees cost and average cost.
* **Opening list:** `inventory/opening.py` builds the template (Tires / Batteries / Mags tabs with the paper list's columns: brand,
  size or model, qty = on hand, remarks, cost) and checks an upload row by row (nothing kept when anything is wrong); a clean upload
  waits (`OpeningImport` pending) until the **owner confirms the totals**; items that already have movements are never re-opened.
* All changes audited (INV_ITEM_ADDED / STOCK_IN / STOCK_IN_REVERSED / ADJUSTED / ITEM_SETTINGS / OPENING_UPLOADED / CONFIRMED /
  DISCARDED). Every view declares `@role_required` (Owner, Sales; owner-only for adjust / settings / reverse / confirm).
* Next: S2 — counter sale + Excel "Add sale" workbook + upload check + invoice booklets; a sale calls `_move(kind=SALE)` at the
  average cost inside the sale's own atomic save.
