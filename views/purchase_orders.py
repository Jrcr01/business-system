"""Purchase orders (cash disbursement cycle, stage B2): make an order from the supplier's price list, see when it will fall due, order it and download the
PO for the supplier. Rules live in engines/po_rules.py, the PDF in engines/po_pdf.py."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from engines.clock import manila_today
from engines.po_pdf import build_po_pdf
from engines.cash_insights_service import scheduled_outflows
from engines.po_rules import acknowledge_over_plan, cancel_po, cash_check, expected_pay_date, get_po, list_pos, order_po, orders_over_plan, save_po, suggest_order_date
from engines.price_list_service import item_names, price_on
from views.shared import MONEY_FORMAT, confirm_change_dialog, flash, page_header, php, refresh, section, selectbox_creditor

LINE_COLS = ["item_name", "unit", "qty", "est_unit_price"]
FORM_KEYS = ("po_edit_id", "po_supplier", "po_order_date", "po_delivery", "po_notes", "po_lines")


def _reset_form() -> None:
    for k in FORM_KEYS:
        st.session_state.pop(k, None)


def _start_edit(po_id: int) -> None:
    po = get_po(po_id)
    h = po["header"]
    st.session_state.update({
        "po_mode": "New order", "po_edit_id": po_id, "po_supplier": int(h["creditor_id"]), "po_order_date": pd.to_datetime(h["order_date"]).date(),
        "po_delivery": pd.to_datetime(h["expected_delivery_date"]).date() if h["expected_delivery_date"] else None, "po_notes": h["notes"] or "",
        "po_lines": po["lines"][LINE_COLS].copy()})


def _save(also_order: bool, edit_id, cid, order_date, delivery, lines, notes) -> None:
    """Runs when a Save button is pressed (a button callback may change the page's own widgets, which the page body may not do)."""
    ok, msg, po_id = save_po(edit_id, cid, order_date, delivery, lines, notes)
    if ok and also_order:
        ok, msg = order_po(po_id)
    if not ok:
        st.session_state["_po_error"] = msg
        return
    _reset_form()
    st.session_state["po_mode"] = "Orders"
    st.session_state["po_open"] = po_id
    st.cache_data.clear()
    flash(msg if also_order else "Draft saved.")


def _use_dates(order_date, delivery) -> None:
    """Button callback: take the suggested dates (a callback may change the page's own widgets)."""
    st.session_state["po_order_date"] = order_date
    st.session_state["po_delivery"] = delivery


def _cash_panel(cid: int, estimate: float, order_date, delivery, edit_id) -> None:
    """Does the cash on the pay date cover this order? Says so, or says how far short and offers the first later order date that fits."""
    if estimate <= 0 or delivery is None:
        return
    chk = cash_check(cid, estimate, delivery, exclude_po_id=edit_id)
    if not chk["known"]:
        st.info(chk["message"], icon=":material/info:")
        return
    day, low = chk["lowest"]
    if chk["ok"]:
        st.success(f"Fits the cash plan: after this order the lowest balance is {php(low)} on {day:%b %d, %Y} (buffer {php(chk['buffer'])}).", icon=":material/check_circle:")
        return
    st.warning(f"Over the cash plan: when this falls due on {chk['pay_date']:%b %d, %Y} the balance would drop to {php(low)} on {day:%b %d, %Y}, "
               f"{php(chk['shortfall'])} under the buffer of {php(chk['buffer'])}. You can still save it; the owner is told.", icon=":material/warning:")
    sug = suggest_order_date(cid, estimate, order_date, delivery, exclude_po_id=edit_id)
    if sug:
        st.button(f"Order on {sug['order_date']:%b %d, %Y} instead ({sug['days_later']} days later): delivery {sug['delivery_date']:%b %d}, falls due {sug['pay_date']:%b %d, %Y}",
                  icon=":material/event_repeat:", key="po_use_dates", on_click=_use_dates, args=(sug["order_date"], sug["delivery_date"]),
                  help="Moves the order date and the delivery date together, keeping the same lead time. That is the first date the cash covers it.")
    else:
        st.caption("No order date within the next days fits either. The cash needs to come in first, or the order has to be smaller.")


def _lines_df() -> pd.DataFrame:
    if "po_lines" not in st.session_state:
        st.session_state["po_lines"] = pd.DataFrame(columns=LINE_COLS)
    return st.session_state["po_lines"]


def _form() -> None:
    edit_id = st.session_state.get("po_edit_id")
    if edit_id:
        st.info(f"Changing {get_po(edit_id)['header']['po_no']}. Save to keep the changes.", icon=":material/edit:")
    sup_col, d1, d2 = st.columns([3, 1.4, 1.4])
    with sup_col:
        cid = selectbox_creditor("Supplier", "SUPPLIER", "po_supplier")
    st.session_state.setdefault("po_order_date", manila_today())
    st.session_state.setdefault("po_delivery", manila_today() + timedelta(days=7))
    order_date = d1.date_input("Order date", key="po_order_date")
    delivery = d2.date_input("Expected delivery", key="po_delivery", help="When the goods should arrive. The payment falls due after this, by the supplier's terms.")
    if cid is None:
        return
    cid = int(cid)

    section("Add an item")
    names = item_names(cid)
    a1, a2, a3, a4 = st.columns([3, 1, 1, 1.4])
    item = a1.selectbox("Item", options=names, index=None, accept_new_options=True, placeholder="Choose from the price list or type an item", key=f"po_item_{cid}")
    listed = price_on(cid, item, order_date) if item else None
    qty = a2.number_input("Qty", min_value=0.0, step=1.0, value=1.0, key=f"po_qty_{cid}")
    unit = a3.text_input("Unit", value=listed["unit"] if listed else "", key=f"po_unit_{cid}_{item}")
    price = a4.number_input("Unit price", min_value=0.0, step=1.0, format="%.2f", value=float(listed["unit_price"]) if listed else 0.0, key=f"po_price_{cid}_{item}")
    if item:
        st.caption(f"Price list: {php(listed['unit_price'])} since {listed['effective_date']}." if listed
                   else "Not on this supplier's price list on the order date. Type the price, or add it under Price lists.")
    if st.button("Add to the order", icon=":material/add:", key="po_add_line", disabled=not item):
        new = {"item_name": item, "unit": unit, "qty": qty, "est_unit_price": None if (listed and abs(price - listed["unit_price"]) < 0.005) else price}
        st.session_state["po_lines"] = pd.concat([_lines_df(), pd.DataFrame([new])], ignore_index=True)
        st.rerun()

    section("Items on this order", "Change a quantity or price right in the table, or select a row and press Delete to remove it. A blank price uses the price list.")
    edited = st.data_editor(
        _lines_df(), num_rows="dynamic", hide_index=True, width="stretch", key=f"po_grid_{len(_lines_df())}_{edit_id}",
        column_config={"item_name": st.column_config.TextColumn("Item", required=True), "unit": "Unit",
                       "qty": st.column_config.NumberColumn("Qty", min_value=0.0, format="%g"),
                       "est_unit_price": st.column_config.NumberColumn("Unit price", min_value=0.0, format=MONEY_FORMAT)})
    lines = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in edited.to_dict("records") if str(r.get("item_name") or "").strip()]
    estimate = 0.0
    for ln in lines:                                           # the estimate shown here is the same sum the engine will store
        p = ln["est_unit_price"] if ln["est_unit_price"] is not None else ((price_on(cid, ln["item_name"], order_date) or {}).get("unit_price") or 0.0)
        estimate += round(float(ln["qty"] or 0) * float(p), 2)
    pay = expected_pay_date(cid, delivery)
    m1, m2 = st.columns(2)
    m1.metric("Estimated total", php(estimate), border=True, help="From the price list. When the invoice comes, the actual price prevails.")
    m2.metric("Payment falls due", f"{pay:%b %d, %Y}" if pay else "Needs a delivery date", border=True, help="Delivery date + the supplier's terms, on a bank day.")
    _cash_panel(cid, estimate, order_date, delivery, edit_id)
    notes = st.text_area("Notes for the supplier (optional)", key="po_notes", height=70)

    if st.session_state.get("_po_error"):
        st.error(st.session_state.pop("_po_error"))
    b1, b2, _ = st.columns([1.2, 1.6, 3])
    args = (edit_id, cid, order_date, delivery, lines, notes)
    b1.button("Save draft", icon=":material/save:", key="po_save", on_click=_save, args=(False, *args))
    b2.button("Save and order", icon=":material/send:", key="po_save_order", type="primary", on_click=_save, args=(True, *args))


def _over_plan_section() -> None:
    over = orders_over_plan()
    if over.empty:
        return
    with st.container(border=True):
        st.markdown(f"**:material/warning: {len(over)} order(s) placed over the cash plan**")
        for r in over.itertuples():
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{r.po_no}** · {r.supplier} · {php(r.total)}  \n{r.cash_note or ''}")
            c2.button("Noted", key=f"po_seen_{r.po_id}", icon=":material/done:", on_click=lambda i=int(r.po_id): (acknowledge_over_plan(i), st.cache_data.clear()),
                      help="You have seen it. It stops showing as a warning on the Dashboard.")


def _by_week() -> None:
    out = scheduled_outflows(manila_today(), manila_today() + timedelta(days=120))
    if out.empty:
        st.caption("Nothing is scheduled to go out in the next 120 days.")
        return
    out = out.assign(week=pd.to_datetime(out["day"]).dt.to_period("W-SUN").dt.start_time.dt.date)
    pivot = out.pivot_table(index="week", columns="track", values="amount", aggfunc="sum", fill_value=0.0)
    pivot["Total"] = pivot.sum(axis=1)
    st.dataframe(pivot.reset_index(), hide_index=True, width="stretch",
                 column_config={"week": st.column_config.DateColumn("Week of"), **{c: st.column_config.NumberColumn(c, format=MONEY_FORMAT) for c in pivot.columns}})
    st.caption("Everything the cash forecast counts as going out, by week: checks, loan and card dues, and open purchase orders (column Purchase order).")


def _orders() -> None:
    _over_plan_section()
    with st.expander("What is going out, week by week (orders included)"):
        _by_week()
    f1, f2 = st.columns(2)
    scope = f1.selectbox("Show", ["Open (draft, ordered, partly received)", "Draft", "Ordered", "Partly received", "Received", "Cancelled", "All"], key="po_scope")
    everyone = list_pos()
    names = dict(zip(everyone["creditor_id"], everyone["supplier"]))
    sup = f2.selectbox("Supplier", options=[None] + list(names), format_func=lambda c: "All suppliers" if c is None else names[c], key="po_filter_supplier")
    status = {"Draft": "DRAFT", "Ordered": "ORDERED", "Partly received": "PARTIAL", "Received": "RECEIVED", "Cancelled": "CANCELLED"}.get(scope)
    df = list_pos(status=status, creditor_id=sup)
    if scope.startswith("Open"):
        df = df[df["status"].isin(["DRAFT", "ORDERED", "PARTIAL"])]
    if df.empty:
        st.info("No orders here yet. Make one under New order.")
        return
    late = int(df["delivery_overdue"].sum())
    st.caption(f"{len(df)} order(s), estimated {php(df['total'].sum())}." + (f" **{late} past the expected delivery date and not yet received.**" if late else ""))
    show = df.assign(flag=df["delivery_overdue"].map({True: "Late", False: ""}))
    st.dataframe(show[["po_no", "supplier", "order_date", "expected_delivery_date", "expected_pay_date", "total", "status", "flag"]], hide_index=True, width="stretch",
                 column_config={"po_no": "PO no.", "supplier": "Supplier", "order_date": st.column_config.DateColumn("Ordered"),
                                "expected_delivery_date": st.column_config.DateColumn("Delivery"), "expected_pay_date": st.column_config.DateColumn("Falls due"),
                                "total": st.column_config.NumberColumn("Estimated", format=MONEY_FORMAT), "status": "Status", "flag": "Delivery"})

    ids = list(df["po_id"])
    st.session_state.setdefault("po_open", ids[0])
    if st.session_state["po_open"] not in ids:
        st.session_state["po_open"] = ids[0]
    labels = {int(r.po_id): f"{r.po_no} · {r.supplier} · {php(r.total)}" for r in df.itertuples()}
    pick = st.selectbox("Open an order", options=ids, format_func=labels.get, key="po_open")
    po = get_po(int(pick))
    h = po["header"]
    with st.container(border=True):
        st.markdown(f"#### {h['po_no']} — {po['supplier']}  ·  {h['status'].title()}")
        st.markdown(f"Ordered **{h['order_date']}** · delivery **{h['expected_delivery_date'] or 'not set'}** · terms **{h['terms_snapshot'] or 'not set'}** · "
                    f"falls due **{h['expected_pay_date'] or 'not set'}** · estimated **{php(po['total'])}**")
        if h["notes"]:
            st.caption(f"Notes: {h['notes']}")
        st.dataframe(po["lines"][["item_name", "unit", "qty", "est_unit_price", "est_amount"]], hide_index=True, width="stretch",
                     column_config={"item_name": "Item", "unit": "Unit", "qty": st.column_config.NumberColumn("Qty", format="%g"),
                                    "est_unit_price": st.column_config.NumberColumn("Unit price", format=MONEY_FORMAT),
                                    "est_amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
        status = h["status"]
        c1, c2, c3, c4 = st.columns(4)
        if status == "DRAFT":
            if c1.button("Order it", icon=":material/send:", key="po_order_btn", type="primary", help="Gives it its PO number and makes the PDF available."):
                ok, msg = order_po(int(pick))
                (refresh(done=msg) if ok else st.error(msg))
        if status in ("ORDERED", "PARTIAL"):
            pdf = build_po_pdf(int(pick))
            if pdf:
                c1.download_button("Download PO (PDF)", pdf, file_name=f"{h['po_no']}.pdf".replace(" ", "_"), mime="application/pdf", icon=":material/picture_as_pdf:", key="po_pdf")
        if status in ("DRAFT", "ORDERED"):
            c2.button("Change", icon=":material/edit:", key="po_edit_btn", on_click=_start_edit, args=(int(pick),))
        if status in ("DRAFT", "ORDERED", "PARTIAL"):
            if c3.button("Cancel order", icon=":material/cancel:", key="po_cancel_btn"):
                def _cancel():
                    ok, msg = cancel_po(int(pick))
                    if ok:
                        flash(msg)
                    return ok, msg

                confirm_change_dialog(f"Cancel **{h['po_no']}** for **{po['supplier']}** ({php(po['total'])})?", "po_cancel_confirm", _cancel, "Yes, cancel it",
                                      warning="A cancelled order stops counting as money owed. If the supplier already has the PO, tell them.")


def render() -> None:
    page_header("Purchase orders", "An order shows what we will owe, and when, before the supplier's invoice exists. Prices come from the Price lists.")
    st.session_state.setdefault("po_mode", "Orders")
    mode = st.segmented_control("What do you want to do?", ["Orders", "New order"], key="po_mode", label_visibility="collapsed")
    if mode == "New order":
        _form()
    else:
        if st.session_state.get("po_edit_id"):
            _reset_form()
        _orders()
