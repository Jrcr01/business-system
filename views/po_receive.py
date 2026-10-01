"""Matching a supplier's invoice to its purchase order (cash disbursement cycle, stage B4). One form, used on the Purchase orders page (Receive) and inside a batch's
Invoices section ("From a purchase order"): tick what arrived, check the quantity and the price the supplier actually charged (the ACTUAL price prevails over the
estimate), type the invoice number, and it lands in the batch. Rules: engines/po_rules.py (receive_into_batch)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.clock import manila_today
from engines.common import php
from engines.po_rules import po_links_for_batch, receivable_lines, receive_into_batch
from engines.statement_rules import create_order_statement
from views.shared import MONEY_FORMAT, q, refresh


def render_receive_form(creditor_id: int, statement_id: int | None = None, preselect_po: int | None = None, key: str = "rcv") -> None:
    """creditor_id: the supplier. statement_id: the batch to put the invoice in (None: choose one, or start a new batch). preselect_po: tick that order's lines first."""
    lines = receivable_lines(creditor_id)
    if lines.empty:
        st.info("No purchase order is waiting for goods from this supplier. Order one under Purchase orders, or type the invoice in directly.", icon=":material/inventory_2:")
        return
    k = f"{key}_{creditor_id}_{statement_id or 'pick'}"
    st.caption("Tick what arrived. The invoice is made at the price the supplier actually charged: change the price when it differs from the estimate.")
    grid = lines.assign(receive=lines["po_id"].eq(preselect_po) if preselect_po else False, qty_now=lines["remaining"], actual_unit_price=lines["est_unit_price"])
    grid["amount"] = grid["qty_now"] * grid["actual_unit_price"]
    shown = grid[["receive", "po_no", "item_name", "unit", "remaining", "qty_now", "est_unit_price", "actual_unit_price", "amount"]]
    edited = st.data_editor(
        shown, hide_index=True, width="stretch", key=f"{k}_grid_{preselect_po}", num_rows="fixed",
        disabled=["po_no", "item_name", "unit", "remaining", "est_unit_price", "amount"],
        column_config={"receive": st.column_config.CheckboxColumn("Arrived"), "po_no": "PO", "item_name": "Item", "unit": "Unit",
                       "remaining": st.column_config.NumberColumn("Still to come", format="%g"),
                       "qty_now": st.column_config.NumberColumn("Qty arrived", min_value=0.0, format="%g"),
                       "est_unit_price": st.column_config.NumberColumn("Estimated price", format=MONEY_FORMAT),
                       "actual_unit_price": st.column_config.NumberColumn("Actual price", min_value=0.0, format=MONEY_FORMAT),
                       "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
    ticked = edited[edited["receive"]].copy()
    ticked["amount"] = ticked["qty_now"] * ticked["actual_unit_price"]
    total = float(ticked["amount"].sum())
    est_total = float((ticked["qty_now"] * ticked["est_unit_price"]).sum())

    c1, c2, c3 = st.columns([2, 1.4, 1.6])
    doc = c1.text_input("Supplier's invoice number", key=f"{k}_doc")
    inv_date = c2.date_input("Invoice date", value=manila_today(), key=f"{k}_date", help="The due date is worked out from this date and the supplier's terms.")
    override = c3.number_input("Invoice total, if it differs (0 = as ticked)", min_value=0.0, value=0.0, step=1.0, format="%.2f", key=f"{k}_override",
                               help="Only when the supplier's invoice total is not quantity × price (freight, a rounding, a line not on the order).")
    m1, m2 = st.columns(2)
    m1.metric("Invoice amount", php(override if override > 0 else total), border=True)
    diff = (override if override > 0 else total) - est_total
    m2.metric("Against the estimate", php(est_total), f"{'+' if diff >= 0 else '-'}{php(abs(diff))}" if ticked.shape[0] else None, delta_color="inverse", border=True,
              help="Positive = the supplier charged more than the order estimated.")

    batch_id = statement_id
    if statement_id is None:
        batches = q("""SELECT statement_id, statement_ref FROM order_statements WHERE creditor_id = ? AND status != 'Cancelled' AND is_locked = 0
                       ORDER BY statement_id DESC""", (int(creditor_id),))
        opts = {0: "A new batch"} | {int(r.statement_id): str(r.statement_ref) for r in batches.itertuples()}
        batch_id = st.selectbox("Put the invoice in", options=list(opts), format_func=opts.get, index=1 if len(opts) > 1 else 0, key=f"{k}_batch")
    update_prices = st.checkbox("Update the price list where the actual price differs", key=f"{k}_upd",
                                help="Adds the actual price to this supplier's price list, starting on the invoice date. Earlier orders keep their price.")
    if st.button("Add to the batch", icon=":material/playlist_add:", type="primary", key=f"{k}_go", disabled=ticked.empty):
        sid = int(batch_id) if batch_id else create_order_statement(int(creditor_id))
        picks = [{"line_id": int(lines.loc[i, "line_id"]), "qty": float(r.qty_now), "actual_unit_price": float(r.actual_unit_price)} for i, r in ticked.iterrows()]
        ok, msg, _ = receive_into_batch(int(creditor_id), sid, doc, inv_date, picks, update_prices=update_prices, gross_override=override if override > 0 else None)
        if ok:
            refresh(done=msg)
        else:
            st.error(msg)


def render_po_match_notes(statement_id: int, creditor_id: int) -> None:
    """Under a batch's invoices: which invoices came from purchase orders (✓ same as the estimate, Δ the actual price differed) and how many were typed with no PO."""
    links = po_links_for_batch(int(statement_id))
    total = q("SELECT COUNT(*) AS n FROM invoices WHERE statement_id = ? AND status != 'Cancelled' AND is_opening_balance = 0", (int(statement_id),))["n"][0]
    if links.empty:
        return
    show = links.assign(mark=["✓" if m else "Δ" for m in links["matches"]])
    st.markdown(f"**Matched to purchase orders** — {len(links)} of {int(total)} invoice(s); {int(total) - len(links)} typed in with no PO.")
    st.dataframe(show[["mark", "doc_ref", "po_nos", "est_amount", "actual_amount", "difference"]], hide_index=True, width="stretch",
                 column_config={"mark": "", "doc_ref": "Invoice", "po_nos": "PO", "est_amount": st.column_config.NumberColumn("Estimated", format=MONEY_FORMAT),
                                "actual_amount": st.column_config.NumberColumn("Invoiced", format=MONEY_FORMAT),
                                "difference": st.column_config.NumberColumn("Difference", format=MONEY_FORMAT)})
