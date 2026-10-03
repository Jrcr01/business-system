"""Matching a supplier's invoice to its purchase order (cash disbursement cycle, stage B4). One form, used on the Purchase orders page (Receive) and inside a batch's
Invoices section ("From a purchase order"): tick what arrived, check the quantity and the price the supplier actually charged (the ACTUAL price prevails over the
estimate), type the invoice number, and it lands in the batch. Rules: engines/po_rules.py (receive_into_batch)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.clock import manila_today
from engines.common import php
from engines.po_rules import invoiceable_lines, po_links_for_batch, receivable_lines, receive_goods, receive_into_batch
from engines.statement_rules import create_order_statement
from views.shared import MONEY_FORMAT, q, refresh


def render_goods_form(creditor_id: int, preselect_po: int | None = None, key: str = "goods") -> None:
    """Step 1 (Purchasing): tick what arrived and how many. No invoice, no batch: Payables matches the supplier's invoice later (Match invoice)."""
    lines = receivable_lines(creditor_id)
    if lines.empty:
        st.info("No purchase order is waiting for goods from this supplier. Order one under New order.", icon=":material/inventory_2:")
        return
    k = f"{key}_{creditor_id}"
    st.caption("Tick what arrived and how many. This only records the delivery: the supplier's invoice is matched later by Payables, and until then the goods still count as money owed.")
    grid = lines.assign(receive=lines["po_id"].eq(preselect_po) if preselect_po else False, qty_now=lines["remaining"])
    edited = st.data_editor(
        grid[["receive", "po_no", "item_name", "unit", "remaining", "qty_now"]], hide_index=True, width="stretch", key=f"{k}_grid_{preselect_po}", num_rows="fixed",
        disabled=["po_no", "item_name", "unit", "remaining"],
        column_config={"receive": st.column_config.CheckboxColumn("Arrived"), "po_no": "PO", "item_name": "Item", "unit": "Unit",
                       "remaining": st.column_config.NumberColumn("Still to come", format="%g"),
                       "qty_now": st.column_config.NumberColumn("Qty arrived", min_value=0.0, format="%g")})
    ticked = edited[edited["receive"]]
    c1, c2 = st.columns([2, 1.4])
    kind = c1.segmented_control("The supplier gave us", ["Delivery receipt", "Invoice"], default="Delivery receipt", required=True, key=f"{k}_kind",
                                help="Some suppliers give no delivery receipt, only their invoice. Pick Invoice and type its number: Payables then finds it when matching the invoice.")
    ref = c1.text_input(("Delivery receipt no." if kind == "Delivery receipt" else "Supplier's invoice no.") + " (optional)", key=f"{k}_ref_{kind}")
    when = c2.date_input("Date received", value=manila_today(), max_value=manila_today(), key=f"{k}_date")
    if st.button("Record goods received", icon=":material/inventory_2:", type="primary", key=f"{k}_go", disabled=ticked.empty):
        picks = [{"line_id": int(lines.loc[i, "line_id"]), "qty": float(r.qty_now)} for i, r in ticked.iterrows()]
        ok, msg, _ = receive_goods(int(creditor_id), picks, when, ref, "invoice" if kind == "Invoice" else "delivery receipt")
        if ok:
            st.session_state["po_next"] = {"kind": "received", "cid": int(creditor_id)}
            refresh(done=msg)
        else:
            st.error(msg)


def render_receive_and_invoice(creditor_id: int, preselect_po: int | None = None, key: str = "both") -> None:
    """Goods and the supplier's invoice in ONE step, for a delivery that came with its invoice (a person who may match invoices: Payables, the owners): tick what arrived, check the
    prices against the invoice, pick the batch. The goods are counted at the price charged, the invoice goes into the batch, and the order is updated, all together."""
    lines = receivable_lines(creditor_id)
    if lines.empty:
        st.info("No purchase order is waiting for goods from this supplier. Order one under New order.", icon=":material/inventory_2:")
        return
    _invoice_form(creditor_id, None, lines, False, preselect_po, key)


def render_receive_form(creditor_id: int, statement_id: int | None = None, preselect_po: int | None = None, key: str = "rcv") -> None:
    """Step 2 (Payables): match the supplier's invoice to goods already received and put it in a batch. statement_id: the batch (None: choose one, or start a new batch).
    Goods not yet recorded as received can be received and invoiced in one go (second box)."""
    waiting, unreceived = invoiceable_lines(creditor_id), receivable_lines(creditor_id)
    if waiting.empty and unreceived.empty:
        st.info("No received goods are waiting for an invoice from this supplier, and no order is waiting for goods. Type the invoice in directly if it has no order.", icon=":material/inventory_2:")
        return
    if not waiting.empty:
        st.caption("Goods already received and waiting for the supplier's invoice.")
        _invoice_form(creditor_id, statement_id, waiting, True, preselect_po, f"{key}_inv")
    if not unreceived.empty:
        with st.expander("Goods not recorded as received yet: receive and invoice in one step", expanded=waiting.empty):
            _invoice_form(creditor_id, statement_id, unreceived, False, preselect_po, f"{key}_one")


def _invoice_form(creditor_id: int, statement_id: int | None, lines: pd.DataFrame, goods_received: bool, preselect_po: int | None, key: str) -> None:
    k = f"{key}_{creditor_id}_{statement_id or 'pick'}"
    st.caption("Tick what the invoice covers. The invoice is made at the price the supplier actually charged: change the price when it differs from the estimate.")
    came_with = ""
    if "received_ref" in lines.columns:                                      # the supplier's invoice number typed when the goods were received, if that is what came with them
        refs = [str(r) for r in lines["received_ref"].dropna().unique() if r and not str(r).startswith("DR ")]
        came_with = refs[0] if len(refs) == 1 else ""
    pre = lines["po_id"].eq(preselect_po) if preselect_po else (lines["received_ref"].eq(came_with) if came_with else False)       # an invoice that came with the goods: those lines arrive ticked
    grid = lines.assign(receive=pre, qty_now=lines["remaining"], actual_unit_price=lines["est_unit_price"])
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
    doc = c1.text_input("Supplier's invoice number", value=came_with, key=f"{k}_doc")
    got = pd.to_datetime(lines["received_date"], errors="coerce").max() if "received_date" in lines.columns and came_with else None
    inv_default = min(got.date(), manila_today()) if got is not None and not pd.isna(got) else manila_today()          # an invoice that came with the goods is dated the day they arrived
    inv_date = c2.date_input("Invoice date", value=inv_default, key=f"{k}_date", help="The due date is worked out from this date and the supplier's terms.")
    override = c3.number_input("Invoice total, if it differs (0 = as ticked)", min_value=0.0, value=0.0, step=1.0, format="%.2f", key=f"{k}_override",
                               help="Only when the supplier's invoice total is not quantity × price (freight, a rounding, a line not on the order).")
    m1, m2 = st.columns(2)
    m1.metric("Invoice amount", php(override if override > 0 else total), border=True)
    diff = (override if override > 0 else total) - est_total
    m2.metric("Against the estimate", php(est_total), f"{'+' if diff >= 0 else '-'}{php(abs(diff))}" if ticked.shape[0] else None, delta_color="inverse", border=True,
              help="Positive = the supplier charged more than the order estimated.")

    batch_id = statement_id
    if statement_id is None:
        batches = q("""SELECT statement_id, statement_ref, status FROM order_statements WHERE creditor_id = ? AND status != 'Cancelled' AND is_locked = 0
                       ORDER BY statement_id DESC""", (int(creditor_id),))
        opts = {0: "A new batch"} | {int(r.statement_id): str(r.statement_ref) for r in batches.itertuples()}
        drafts = [int(r.statement_id) for r in batches.itertuples() if str(r.status) == "Draft"]
        default_batch = drafts[0] if drafts else (int(batches.iloc[0]["statement_id"]) if len(batches) else 0)                   # the supplier's open Draft first, else the newest
        batch_id = st.selectbox("Put the invoice in", options=list(opts), format_func=opts.get, index=list(opts).index(default_batch), key=f"{k}_batch")
    update_prices = st.checkbox("Update the price list where the actual price differs", key=f"{k}_upd",
                                help="Adds the actual price to this supplier's price list, starting on the invoice date. Earlier orders keep their price.")
    if st.button("Add to the batch", icon=":material/playlist_add:", type="primary", key=f"{k}_go", disabled=ticked.empty or not str(doc).strip()):
        sid = int(batch_id) if batch_id else create_order_statement(int(creditor_id))
        picks = [{"line_id": int(lines.loc[i, "line_id"]), "qty": float(r.qty_now), "actual_unit_price": float(r.actual_unit_price)} for i, r in ticked.iterrows()]
        ok, msg, _ = receive_into_batch(int(creditor_id), sid, doc, inv_date, picks, update_prices=update_prices, gross_override=override if override > 0 else None,
                                        goods_received=goods_received)
        if ok:
            st.session_state["po_next"] = {"kind": "matched", "sid": int(sid), "doc": doc}
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
