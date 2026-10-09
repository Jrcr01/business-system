"""Reconciling the price list with the invoices (2026-10-03). A price list goes out of date, or a supplier charges something other than what was agreed and nobody corrected it. Matching
an invoice to its order is where that shows up: the invoice price is the truth. This lists, per supplier, every item whose LAST INVOICE price differs from the net price the list gives
today, and updates the list to the invoice price in one step (the invoice price is NET, so the list price is worked back through the discount chain, as receiving already does).

  * invoice_vs_list(creditor_id)            -- item, list net today, last invoiced net, difference, % and the invoice it came from
  * update_to_invoice(creditor_id, item)    -- adds the invoice price to the price list (as a new dated price; the history is kept)"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn
from engines.discount_rules import net_price_on, parse_chain
from engines.price_list_service import add_price, price_on

TOLERANCE = 0.005          # a difference of half a centavo or less is rounding, not a different price


def _last_invoiced(creditor_id: int) -> pd.DataFrame:
    """One row per item: the actual (net) unit price on the supplier's latest invoice for it."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT l.item_name, l.unit, l.actual_unit_price AS invoiced, r.supplier_invoice_no AS invoice_no, COALESCE(r.supplier_invoice_date, r.received_date) AS invoice_date, l.line_id
               FROM purchase_order_lines l JOIN purchase_orders p ON p.po_id = l.po_id
               JOIN po_receipts r ON r.po_id = p.po_id AND r.invoice_id IS NOT NULL
               WHERE p.creditor_id = ? AND l.actual_unit_price IS NOT NULL
               ORDER BY COALESCE(r.supplier_invoice_date, r.received_date) DESC, r.receipt_id DESC""", conn, params=(creditor_id,))
    if df.empty:
        return df
    df["_k"] = df["item_name"].str.lower()
    return df.drop_duplicates("_k").drop(columns="_k")


def invoice_vs_list(creditor_id: int, on: date | None = None) -> pd.DataFrame:
    """Items whose last invoiced net price differs from today's net price on the list. Columns: item_name, unit, list_net, invoiced, difference, difference_pct, invoice_no, invoice_date.
    An item the list has no price for is shown with list_net empty (the list is missing it)."""
    on = on or manila_today()
    cols = ["item_name", "unit", "list_net", "invoiced", "difference", "difference_pct", "invoice_no", "invoice_date"]
    last = _last_invoiced(creditor_id)
    rows = []
    for r in last.itertuples():
        listed = net_price_on(creditor_id, r.item_name, on)
        net = float(listed["net_price"]) if listed else None
        diff = None if net is None else round(float(r.invoiced) - net, 2)
        if net is not None and abs(diff) <= TOLERANCE:
            continue
        rows.append({"item_name": r.item_name, "unit": r.unit, "list_net": net, "invoiced": float(r.invoiced), "difference": diff,
                     "difference_pct": None if not net else round(diff / net * 100, 1), "invoice_no": r.invoice_no, "invoice_date": r.invoice_date})
    return pd.DataFrame(rows, columns=cols)


def update_to_invoice(creditor_id: int, item_name: str, effective_date: date | None = None) -> tuple[bool, str]:
    """Puts the last invoice price on the list from `effective_date` (default: the invoice's date): the net invoice price worked back to a list price through the item's discount chain."""
    last = _last_invoiced(creditor_id)
    hit = last[last["item_name"].str.lower() == str(item_name).lower()]
    if hit.empty:
        return False, "No invoice has been matched for that item."
    row = hit.iloc[0]
    day = effective_date or date.fromisoformat(str(row["invoice_date"])[:10])
    listed = net_price_on(creditor_id, item_name, day)
    factor = 1.0
    for d in (parse_chain(listed["chain"]) or []) if listed else []:
        factor *= 1 - d / 100
    new_list = round(float(row["invoiced"]) / factor, 2) if factor else float(row["invoiced"])
    unit = row["unit"] or (listed or {}).get("unit") or ""
    ok, msg = add_price(creditor_id, item_name, unit, new_list, day, replace=True)
    return ok, (f"{item_name}: list price now {new_list:,.2f} from {day:%m/%d/%Y} (the invoice price {float(row['invoiced']):,.2f} after the discount)." if ok else msg)
