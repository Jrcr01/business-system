"""Purchase orders (cash disbursement cycle, stage B2): make an order from the supplier's price list, see when it will fall due, order it and download the
PO for the supplier. Rules live in engines/po_rules.py, the PDF in engines/po_pdf.py."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import streamlit as st

from engines.clock import manila_today
from engines.po_pdf import build_po_pdf
from engines.cash_insights_service import scheduled_outflows
import altair as alt

from engines.disbursement_trail_service import pipeline, trail_for_po
from engines.po_rules import acknowledge_over_plan, cancel_po, close_short, cash_check, default_delivery, delivery_for_week, expected_pay_date, days_strip, week_schedule, weeks_strip, get_po, list_pos, order_po, orders_over_plan, save_po, suggest_order_date
from engines.discount_rules import chain_label, net_price, parse_chain, standing_chain
from engines.price_list_service import item_names, price_on
from engines.settings_registry import forecast_shown
from engines.permissions import can, hide_check_details
from views.login import can_now, current_role
from views.po_receive import render_goods_form, render_receive_form
from engines.home_tiles import peso_short
from views.shared import MONEY_FORMAT, confirm_change_dialog, cycle_bar, date_col, flash, next_steps, page_header, php, refresh, section, selectbox_creditor

PO_STATUS = {"DRAFT": "Draft", "ORDERED": "Ordered", "PARTIAL": "Partly received", "RECEIVED": "Received", "CANCELLED": "Cancelled"}
MODE_ICON = {"Orders": ":material/list_alt: Orders", "New order": ":material/add_shopping_cart: New order", "Receive": ":material/inventory_2: Receive goods",
             "Match invoice": ":material/receipt_long: Match invoice"}

LINE_COLS = ["item_name", "unit", "qty", "list_price", "discount_chain"]
FORM_KEYS = ("po_edit_id", "po_supplier", "po_order_date", "po_delivery", "po_notes", "po_lines", "po_preview")


def _reset_form() -> None:
    for k in FORM_KEYS:
        st.session_state.pop(k, None)


def _start_edit(po_id: int) -> None:
    po = get_po(po_id)
    h = po["header"]
    st.session_state.update({
        "po_mode": "New order", "po_edit_id": po_id, "po_supplier": int(h["creditor_id"]), "po_order_date": pd.to_datetime(h["order_date"]).date(),
        "po_delivery": pd.to_datetime(h["expected_delivery_date"]).date() if h["expected_delivery_date"] else None, "po_notes": h["notes"] or "",
        "po_lines": _lines_for_edit(po["lines"]), "_po_sig": (int(h["creditor_id"]), pd.to_datetime(h["order_date"]).date())})


def _lines_for_edit(lines: pd.DataFrame) -> pd.DataFrame:
    """A line typed with a net price (no list price) is edited as list = net, no discount: the same amount."""
    out = lines.copy()
    out["list_price"] = out["list_price"].where(out["list_price"].notna(), out["est_unit_price"])
    out["discount_chain"] = out["discount_chain"].fillna("").replace("", "0")        # "0" = no discount on this line, so a later change of the supplier's usual discount does not alter a saved order
    return out[LINE_COLS]


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
    if also_order:
        st.session_state["po_next"] = {"kind": "ordered", "po_id": po_id, "po_no": get_po(po_id)["header"]["po_no"]}
    st.cache_data.clear()
    flash(msg if also_order else "Draft saved.")


def _use_delivery(delivery) -> None:
    """Button callback: take the suggested lighter delivery day."""
    st.session_state["po_delivery"] = delivery


def _use_dates(order_date, delivery) -> None:
    """Button callback: take the suggested dates (a callback may change the page's own widgets)."""
    st.session_state["po_order_date"] = order_date
    st.session_state["po_delivery"] = delivery


def _cash_panel(cid: int, estimate: float, order_date, delivery, edit_id) -> None:
    """Does the cash on the pay date cover this order? Says so, or says how far short and offers the first later order date that fits."""
    if estimate <= 0 or delivery is None:
        return
    if not forecast_shown():                                # no cash verdict while the forecast is hidden: the payables schedule above shows what is committed
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


def _start_receive(po_id: int, creditor_id: int, mode: str = "Receive") -> None:
    """Button callback: open the Receive goods (or Match invoice) form for this order's supplier with its lines ticked."""
    st.session_state.update({"po_mode": mode, "rcv_supplier": int(creditor_id), "rcv_po": int(po_id)})


def _receive() -> None:
    """Step 1: the goods that arrived (Purchasing). No invoice, no batch."""
    st.caption("A delivery arrived: record what came. The supplier's invoice is matched later by Payables, who put it in a batch.")
    cid = selectbox_creditor("Supplier", "SUPPLIER", "rcv_supplier")
    if cid is None:
        return
    if can_now("po.receive"):                              # a person who may match invoices can do both at once when the invoice came with the delivery
        together = st.segmented_control("What came with the delivery?", ["Goods only", "Goods and the supplier's invoice"], default="Goods only", required=True, key="rcv_together",
                                        help="If the invoice came with the goods, do both here: no second visit to Match invoice. If it comes later, record the goods now and match the invoice then.")
        if together != "Goods only":
            from views.po_receive import render_receive_and_invoice
            render_receive_and_invoice(int(cid), preselect_po=st.session_state.get("rcv_po"))
            return
    render_goods_form(int(cid), preselect_po=st.session_state.get("rcv_po"))


def _match_invoice() -> None:
    """Step 2: the supplier's invoice arrived (Payables): match it to the goods received and put it in a payables batch."""
    st.caption("The supplier's invoice arrived: match it to the goods received. It goes into a payables batch at the price the supplier actually charged.")
    cid = selectbox_creditor("Supplier", "SUPPLIER", "rcv_supplier")
    if cid is None:
        return
    render_receive_form(int(cid), preselect_po=st.session_state.get("rcv_po"))


def _price_differences() -> None:
    from engines.po_rules import price_differences

    df = price_differences()
    if df.empty:
        st.caption("No order has been received yet, so there is no price difference to show.")
        return
    changed = df[df["difference"].abs() > 0.004]
    st.caption(f"{len(changed)} of {len(df)} received line(s) were charged a different price than estimated; net {php(float(df['difference'].sum()))} "
               "(positive = the suppliers charged more). Keep the price lists current to shrink this.")
    st.dataframe(changed if not changed.empty else df, hide_index=True, width="stretch",
                 column_config={"po_no": "PO", "supplier": "Supplier", "item_name": "Item", "qty_received": st.column_config.NumberColumn("Qty", format="%g"),
                                "est_unit_price": st.column_config.NumberColumn("Estimated", format=MONEY_FORMAT),
                                "actual_unit_price": st.column_config.NumberColumn("Charged", format=MONEY_FORMAT),
                                "difference": st.column_config.NumberColumn("Difference", format=MONEY_FORMAT),
                                "difference_pct": st.column_config.NumberColumn("%", format="%+.1f%%")})


def _pick_week(cid: int, week_start, order_date) -> None:
    """Button callback: move the delivery date so the order falls due in the chosen week."""
    d = delivery_for_week(cid, week_start, order_date)
    if d:
        st.session_state["po_delivery"] = d


def _set_preview(on: bool) -> None:
    st.session_state["po_preview"] = on


def _with_order(frame: pd.DataFrame, selected_col: str, estimate: float, on: bool) -> pd.DataFrame:
    """Adds the columns the preview needs: this_order (the order's amount on its own bar only) and top (committed + this order)."""
    out = frame.copy()
    out["this_order"] = [estimate if (on and sel) else 0.0 for sel in out[selected_col]]
    out["top"] = out["total"] + out["this_order"]
    return out


def _schedule_panel(cid: int, order_date, delivery, edit_id, estimate: float) -> None:
    """Payables when this order falls due (shown before any item is added): the week's schedule day by day, an 8-week strip with a total per week (press a week to
    order for it), and what this order adds. Everything already committed counts: checks, loan and card dues, other orders. No deposits are guessed."""
    section("Payables when this order falls due", help="What is already committed in the week this order would be paid (delivery date + the supplier's terms). Choose a lighter week by pressing it.")
    pay = expected_pay_date(cid, delivery)
    if pay is None:
        st.info("Pick the expected delivery date to see the payables for that week.")
        return
    st.markdown(f"This order would fall due on **{pay:%a, %b %d, %Y}** (delivery {delivery:%b %d} plus the supplier's terms).")
    from engines.po_rules import day_guard
    guard = day_guard(cid, delivery, estimate, edit_id)
    if guard["on"] and guard["over"]:                       # the guard rail: a warning for the user to weigh, never a block
        st.warning(f"**High payables on {guard['pay_date']:%a, %b %d}.** {php(guard['already'])} is already due that day; with this order it would be {php(guard['with_order'])}, "
                   f"over your daily limit of {php(guard['limit'])}. You can still order it: it will be noted on the Review list.", icon=":material/warning:")
        sug = guard["suggestion"]
        if sug:
            st.button(f"Deliver on {sug['delivery_date']:%b %d} instead: falls due {sug['pay_date']:%a, %b %d}, that day would be {php(sug['with_order'])}", icon=":material/event_repeat:",
                      key="po_use_light_day", on_click=_use_delivery, args=(sug["delivery_date"],), help="Moves the expected delivery date to the nearest day whose payables stay within the limit.")
        else:
            st.caption("No delivery day in the next three weeks keeps that day within the limit; the order alone may be above it. Consider splitting it into two orders on different days.")
    preview = estimate > 0                                   # the chart always shows this order, on the day or week it falls due, once it has an item (no Preview button, 2026-10-03)
    if preview:
        st.markdown(f"**This order:** ₱{estimate:,.2f} falls due on **{pay:%a, %b %d, %Y}**. The chart follows the items below as you change them.")
    else:
        st.caption("Add an item to see this order on the chart.")
    view = st.segmented_control("Chart", ["Weekly", "Daily"], default="Daily", required=True, key="po_chart_view", label_visibility="collapsed",
                                help="Weekly: eight weeks, one bar each. Daily: each day around the pay day, to see which day is lightest.")
    strip = weeks_strip(pay, exclude_po_id=edit_id)
    if view == "Daily":
        days = days_strip(pay, exclude_po_id=edit_id)
        days = days.assign(label=[f"{d:%b %d} {d:%a}" for d in days["day"]],
                           role=["This order's day" if sel else ("Lightest day" if lt else "Other days") for sel, lt in zip(days["selected"], days["lightest"])])
        days = _with_order(days, "selected", estimate, preview)
        daily = alt.Chart(days).mark_bar().encode(
            x=alt.X("label:N", title="Day", sort=list(days["label"]), axis=alt.Axis(labelAngle=-60)), y=alt.Y("total:Q", title="Already committed (PHP)"),
            color=alt.Color("role:N", title=None, scale=alt.Scale(domain=["This order's day", "Lightest day", "Other days"], range=["#F2B880" if preview else "#C26A00", "#2E7D32", "#9FB3D1"])),
            tooltip=[alt.Tooltip("label:N", title="Day"), alt.Tooltip("total:Q", title="Committed", format=",.2f")])
        if preview:
            mine = alt.Chart(days[days["this_order"] > 0]).mark_bar(color="#C26A00").encode(
                x=alt.X("label:N", sort=list(days["label"])), y="total:Q", y2="top:Q",
                tooltip=[alt.Tooltip("label:N", title="Day"), alt.Tooltip("this_order:Q", title="This order", format=",.2f"), alt.Tooltip("top:Q", title="Day becomes", format=",.2f")])
            note = alt.Chart(days[days["this_order"] > 0]).mark_text(dy=-8, color="#C26A00", fontWeight="bold").encode(
                x=alt.X("label:N", sort=list(days["label"])), y="top:Q", text=alt.Text("this_order:Q", format="+,.0f"))
            daily = daily + mine + note
        st.altair_chart(daily.properties(height=200), width="stretch")
        st.caption("Each bar is what is already due that day (checks, loan and card dues, other orders). To move the order, press a week below or change the delivery date.")
    strip = strip.assign(label=[f"{w:%b %d}" for w in strip["week_start"]],
                         role=["This order's week" if sel else ("Lightest week" if lt else "Other weeks") for sel, lt in zip(strip["selected"], strip["lightest"])])
    strip = _with_order(strip, "selected", estimate, preview)
    chart = alt.Chart(strip.assign(week=pd.to_datetime(strip["week_start"]))).mark_bar().encode(
        x=alt.X("label:N", title="Week of", sort=list(strip["label"])), y=alt.Y("total:Q", title="Already committed (PHP)"),
        color=alt.Color("role:N", title=None, scale=alt.Scale(domain=["This order's week", "Lightest week", "Other weeks"], range=["#F2B880" if preview else "#C26A00", "#2E7D32", "#9FB3D1"])),
        tooltip=[alt.Tooltip("label:N", title="Week of"), alt.Tooltip("total:Q", title="Committed", format=",.2f")])
    if preview:
        mine = alt.Chart(strip[strip["this_order"] > 0]).mark_bar(color="#C26A00").encode(
            x=alt.X("label:N", sort=list(strip["label"])), y="total:Q", y2="top:Q",
            tooltip=[alt.Tooltip("label:N", title="Week of"), alt.Tooltip("this_order:Q", title="This order", format=",.2f"), alt.Tooltip("top:Q", title="Week becomes", format=",.2f")])
        note = alt.Chart(strip[strip["this_order"] > 0]).mark_text(dy=-8, color="#C26A00", fontWeight="bold").encode(
            x=alt.X("label:N", sort=list(strip["label"])), y="top:Q", text=alt.Text("this_order:Q", format="+,.0f"))
        chart = chart + mine + note
    if view != "Daily":
        st.altair_chart(chart.properties(height=170), width="stretch")
    cols = st.columns(len(strip))
    for col, r in zip(cols, strip.itertuples()):
        reachable = delivery_for_week(cid, r.week_start, order_date) is not None
        col.button(f"{r.label}\n\n{peso_short(r.total)}" + (" ★" if r.lightest else ""), key=f"po_wk_{r.week_start}", disabled=not reachable or bool(r.selected), width="stretch",
                   help=("The order's week." if r.selected else "This week is before the order could be paid." if not reachable else
                         f"Order for the week of {r.week_start:%b %d}: moves the delivery date so it falls due then. ★ = the lightest week."),
                   on_click=_pick_week, args=(cid, r.week_start, order_date))
    sched = week_schedule(pay, exclude_po_id=edit_id)
    st.markdown(f"**The week of {sched['week_start']:%b %d} to {sched['week_end']:%b %d}: {php(sched['total'])} already committed**")
    if sched["rows"].empty:
        st.caption("Nothing else is committed that week.")
    else:
        cols = ["day", "payee", "kind", "amount"] if hide_check_details(current_role()) else ["day", "payee", "kind", "reference", "amount"]     # check numbers are the owner's
        st.dataframe(sched["rows"][cols], hide_index=True, width="stretch",
                     column_config={"day": st.column_config.DateColumn("Day", format="ddd, MMM D"), "payee": "Pay to", "kind": "What", "reference": "Reference",
                                    "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
    if estimate > 0:
        st.info(f"This order adds **{php(estimate)}**, so the week becomes **{php(sched['total'] + estimate)}**.", icon=":material/event_upcoming:")


STATE_MARK = {"done": ":green[:material/check_circle:]", "partly": ":orange[:material/timelapse:]", "late": ":red[:material/error:]", "waiting": ":gray[:material/radio_button_unchecked:]"}


def _trail(po_id: int) -> None:
    """Follow this order to the bank: the stages with what happened at each, the planned dates beside the actual ones, the invoices and the checks."""
    tr = trail_for_po(po_id)
    if tr is None:
        return
    cols = st.columns(len(tr["stages"]))
    for col, stg in zip(cols, tr["stages"]):
        col.markdown(f"{STATE_MARK[stg['state']]} **{stg['title']}**" + (f"  \n:gray[{stg['date']:%b %d}]" if stg["date"] else ""))
    nxt = next((x for x in tr["stages"] if x["key"] == tr["current"]), None)
    st.markdown((f"**Next:** {nxt['title']}. {nxt['detail']}" if nxt else "**Complete:** the order has been paid and cleared by the bank."))
    for stg in tr["stages"]:
        st.markdown(f"{STATE_MARK[stg['state']]} **{stg['title']}** — {stg['detail']}")
    if not tr["dates"].empty:
        st.markdown("**Planned against actual**")
        st.dataframe(tr["dates"], hide_index=True, width="stretch", column_config={"what": "What", "date": date_col("Date")})
    if not tr["invoices"].empty:
        st.markdown("**Invoices**")
        st.dataframe(tr["invoices"][["doc_ref", "invoice_date", "due_date", "batch", "est_amount", "actual_amount"]], hide_index=True, width="stretch",
                     column_config={"doc_ref": "Invoice", "invoice_date": date_col("Dated"), "due_date": date_col("Due"), "batch": "Batch",
                                    "est_amount": st.column_config.NumberColumn("Estimated", format=MONEY_FORMAT), "actual_amount": st.column_config.NumberColumn("Invoiced", format=MONEY_FORMAT)})
    if not tr["checks"].empty and not hide_check_details(current_role()):
        st.markdown("**Checks**")
        st.dataframe(tr["checks"][["check_number", "check_date", "allocated", "released_date", "released_to", "bank_cleared_date"]], hide_index=True, width="stretch",
                     column_config={"check_number": "Check no.", "check_date": date_col("Dated"), "allocated": st.column_config.NumberColumn("For this order", format=MONEY_FORMAT),
                                    "released_date": date_col("Released"), "released_to": "Collected by", "bank_cleared_date": date_col("Cleared")})


def _pipeline() -> None:
    only_open = st.toggle("Only orders not yet cleared by the bank", value=True, key="po_pipe_open")
    df = pipeline(only_open=only_open)
    if df.empty:
        st.caption("No orders to show.")
        return
    st.dataframe(df.assign(late=df["late"].map({True: "Late", False: ""})), hide_index=True, width="stretch",
                 column_config={"po_id": None, "po_no": "PO", "supplier": "Supplier", "estimated": st.column_config.NumberColumn("Estimated", format=MONEY_FORMAT),
                                "invoiced": st.column_config.NumberColumn("Invoiced", format=MONEY_FORMAT), "stage": "Reached", "next_step": "Next", "late": ""})


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
    if cid is not None and st.session_state.get("_po_sig") != (int(cid), order_date):       # a new supplier or order date: start from the supplier's usual delivery
        st.session_state["_po_sig"] = (int(cid), order_date)
        if not edit_id:
            st.session_state["po_delivery"] = default_delivery(int(cid), order_date)
    delivery = d2.date_input("Expected delivery", key="po_delivery", help="Starts at the order date plus the supplier's usual delivery days (Creditors page). The payment falls due after this, by the supplier's terms.")
    if cid is None:
        return
    cid = int(cid)
    schedule_box = st.container()                           # filled below, once the order's estimate is known


    section("Add an item")
    names = item_names(cid)
    a1, a2, a3, a4, a5 = st.columns([3, 1, 1, 1.3, 1.2])
    item = a1.selectbox("Item", options=names, index=None, accept_new_options=True, placeholder="Choose from the price list or type an item", key=f"po_item_{cid}")
    listed = price_on(cid, item, order_date) if item else None
    qty = a2.number_input("Qty", min_value=0.0, step=1.0, value=1.0, key=f"po_qty_{cid}")
    unit = a3.text_input("Unit", value=listed["unit"] if listed else "", key=f"po_unit_{cid}_{item}")
    price = a4.number_input("List price", min_value=0.0, step=1.0, format="%.2f", value=float(listed["unit_price"]) if listed else 0.0, key=f"po_price_{cid}_{item}",
                            help="The supplier's price before discount.")
    chain = a5.text_input("Discount %", value=chain_label(standing_chain(cid, item, order_date)).replace("less ", "").replace("%", "").replace(" + ", ", ") if item else "",
                          key=f"po_chain_{cid}_{item}", placeholder="5, 5", help="Less 5%, less 5% is typed 5, 5. Starts with the supplier's discount for this brand; change it for this line only, or clear it for none.")
    if item:
        net = net_price(price, chain) if parse_chain(chain) is not None else price
        st.caption((f"Price list: {php(listed['unit_price'])} since {listed['effective_date']}. " if listed
                    else "Not on this supplier's price list on the order date. Type the price, or add it under Price lists. ")
                   + (f"After {chain_label(chain)}: **{php(net)}** each." if parse_chain(chain) else ""))
    if st.button("Add to the order", icon=":material/add:", key="po_add_line", disabled=not item or parse_chain(chain) is None):
        new = {"item_name": item, "unit": unit, "qty": qty, "list_price": price, "discount_chain": ", ".join(f"{v:g}" for v in parse_chain(chain)) or "0"}
        st.session_state["po_lines"] = pd.concat([_lines_df(), pd.DataFrame([new])], ignore_index=True)
        st.rerun()

    section("Items on this order", "Change a quantity, price or discount right in the table, or select a row and press Delete to remove it. A blank price uses the price list. "
                                   "Net price = list price less each discount in turn.")
    edited = st.data_editor(
        _lines_df(), num_rows="dynamic", hide_index=True, width="stretch", key=f"po_grid_{len(_lines_df())}_{edit_id}",
        column_config={"item_name": st.column_config.TextColumn("Item", required=True), "unit": "Unit",
                       "qty": st.column_config.NumberColumn("Qty", min_value=0.0, format="%g"),
                       "list_price": st.column_config.NumberColumn("List price", min_value=0.0, format=MONEY_FORMAT),
                       "discount_chain": st.column_config.TextColumn("Discount %", help="5, 5 = less 5%, less 5%. Blank = the supplier's usual discount for the brand; type 0 for none.")})
    lines = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in edited.to_dict("records") if str(r.get("item_name") or "").strip()]
    for ln in lines:                                           # a blank discount cell = the supplier's usual one; 0 = none on this line
        text = str(ln.get("discount_chain") or "").strip()
        ln["discount_chain"] = None if text == "" else ("" if parse_chain(text) == [0.0] else text)
    estimate, detail = 0.0, []
    for ln in lines:                                           # the estimate shown here is the same sum the engine will store
        listed_now = price_on(cid, ln["item_name"], order_date)
        lp = ln["list_price"] if ln["list_price"] is not None else ((listed_now or {}).get("unit_price") or 0.0)
        ch = standing_chain(cid, (listed_now or {}).get("item_name", ln["item_name"]), order_date) if ln["discount_chain"] is None else ln["discount_chain"]
        net = net_price(float(lp), ch) if parse_chain(ch) is not None else float(lp)
        amount = round(float(ln["qty"] or 0) * net, 2)
        estimate += amount
        detail.append({"item": ln["item_name"], "discount": chain_label(ch) or "none", "net": net, "amount": amount})
    if detail:
        st.dataframe(pd.DataFrame(detail), hide_index=True, width="stretch",
                     column_config={"item": "Item", "discount": "Discount", "net": st.column_config.NumberColumn("Net price", format=MONEY_FORMAT), "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
    pay = expected_pay_date(cid, delivery)
    m1, m2 = st.columns(2)
    m1.metric("Estimated total", php(estimate), border=True, help="From the price list. When the invoice comes, the actual price prevails.")
    m2.metric("Payment falls due", f"{pay:%b %d, %Y}" if pay else "Needs a delivery date", border=True, help="Delivery date + the supplier's terms, on a bank day.")
    with schedule_box:
        _schedule_panel(cid, order_date, delivery, edit_id, estimate)
    _cash_panel(cid, estimate, order_date, delivery, edit_id)
    notes = st.text_area("Notes for the supplier (optional)", key="po_notes", height=70)

    if st.session_state.get("_po_error"):
        st.error(st.session_state.pop("_po_error"))
    b1, b2, _ = st.columns([1.2, 1.6, 3])
    args = (edit_id, cid, order_date, delivery, lines, notes)
    b1.button("Save draft", icon=":material/save:", key="po_save", on_click=_save, args=(False, *args))
    b2.button("Save and order", icon=":material/send:", key="po_save_order", type="primary", on_click=_save, args=(True, *args))


def _noted(po_id: int) -> None:
    ok, msg = acknowledge_over_plan(po_id)
    if ok:
        st.cache_data.clear()
    flash(msg) if ok else st.session_state.update({"_po_error": msg})


def _close_short_dialog(po_id: int, po_no: str, supplier: str) -> None:
    @st.dialog(f"Close {po_no} short")
    def _dlg():
        st.write(f"Part of this order from **{supplier}** was delivered. Close it if the rest will not come: what was not delivered stops counting as money owed.")
        reason = st.text_input("Why will the rest not come?", key="po_short_reason", placeholder="e.g. supplier out of stock")
        if st.button("Close the order", type="primary", icon=":material/done_all:", key="po_short_go"):
            ok, msg = close_short(po_id, reason)
            if ok:
                flash(msg)
                st.cache_data.clear()
                st.rerun()
            st.error(msg)
    _dlg()


def _over_plan_section() -> None:
    over = orders_over_plan()
    if over.empty:
        return
    with st.container(border=True):
        st.markdown(f"**:material/warning: {len(over)} order(s) placed over the cash plan**")
        for r in over.itertuples():
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{r.po_no}** · {r.supplier} · {php(r.total)}  \n{r.cash_note or ''}")
            if can_now("po.ack_over_plan"):
                c2.button("Noted", key=f"po_seen_{r.po_id}", icon=":material/done:", on_click=_noted, args=(int(r.po_id),),
                          help="You have seen it. It stops showing as a warning on the Dashboard.")


def _by_week() -> None:
    """What is going out, simply: just the total per day (or per week), from today. Checks, loan and card dues and open purchase orders all count."""
    out = scheduled_outflows(manila_today(), manila_today() + timedelta(days=120))
    if out.empty:
        st.caption("Nothing is scheduled to go out in the next 120 days.")
        return
    c1, c2 = st.columns([1.4, 1.6])
    view = c1.segmented_control("Schedule", ["Daily", "Weekly"], default="Daily", required=True, key="po_sched_view", label_visibility="collapsed")
    shape = c2.segmented_control("Show as", ["List", "Line graph"], default="List", required=True, key="po_sched_shape", label_visibility="collapsed")
    day = pd.to_datetime(out["day"]).dt.date
    if view == "Weekly":
        key = day.map(lambda d: d - timedelta(days=d.weekday()))
        table = out.assign(when=key).groupby("when", as_index=False)["amount"].sum().rename(columns={"when": "day"})
        label = "Week of"
    else:
        table = out.assign(day=day).groupby("day", as_index=False)["amount"].sum()
        label = "Day"
    table = table.sort_values("day")
    if shape == "Line graph":
        plot = table.assign(label=pd.to_datetime(table["day"]))
        line = alt.Chart(plot).mark_line(point=alt.OverlayMarkDef(filled=True, size=45), color="#0B4F8A").encode(
            x=alt.X("label:T", title=label, axis=alt.Axis(format="%b %d", labelAngle=-45)), y=alt.Y("amount:Q", title="Going out (PHP)", axis=alt.Axis(format="~s")),
            tooltip=[alt.Tooltip("label:T", title=label, format="%a, %b %d, %Y"), alt.Tooltip("amount:Q", title="Total going out", format=",.2f")])
        st.altair_chart(line.properties(height=260), width="stretch")
        st.caption("Each point is the total going out that " + ("week." if view == "Weekly" else "day."))
        return
    st.dataframe(table, hide_index=True, width="stretch", height=min(40 + 35 * len(table), 420),
                 column_config={"day": st.column_config.DateColumn(label, format="ddd, MMM DD, YYYY" if view != "Weekly" else "MMM DD, YYYY"),
                                "amount": st.column_config.NumberColumn("Total going out", format=MONEY_FORMAT)})
    st.caption("Checks, loan and card dues, and open purchase orders, from today. Check the details by day in the New order screen before you order.")


def _orders() -> None:
    _over_plan_section()
    with st.expander("What is going out (total per day, orders included)"):
        _by_week()
    with st.expander("Where every order stands (order to bank)", icon=":material/route:"):
        _pipeline()
    with st.expander("Price differences: estimated against what the suppliers charged"):
        _price_differences()
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
    show = df.assign(flag=df["delivery_overdue"].map({True: "Late", False: ""}), status=df["status"].map(PO_STATUS).fillna(df["status"]))
    st.dataframe(show[["po_no", "supplier", "order_date", "expected_delivery_date", "expected_pay_date", "total", "status", "flag"]], hide_index=True, width="stretch",
                 column_config={"po_no": "PO no.", "supplier": "Supplier", "order_date": date_col("Ordered"),
                                "expected_delivery_date": date_col("Delivery"), "expected_pay_date": date_col("Falls due"),
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
        section(f"{h['po_no']} — {po['supplier']}  ·  {PO_STATUS.get(h['status'], h['status'])}")
        st.markdown(f"Ordered **{h['order_date']}** · delivery **{h['expected_delivery_date'] or 'not set'}** · terms **{h['terms_snapshot'] or 'not set'}** · "
                    f"falls due **{h['expected_pay_date'] or 'not set'}** · estimated **{php(po['total'])}**")
        if h["notes"]:
            st.caption(f"Notes: {h['notes']}")
        st.dataframe(po["lines"][["item_name", "unit", "qty", "est_unit_price", "est_amount"]], hide_index=True, width="stretch",
                     column_config={"item_name": "Item", "unit": "Unit", "qty": st.column_config.NumberColumn("Qty", format="%g"),
                                    "est_unit_price": st.column_config.NumberColumn("Unit price", format=MONEY_FORMAT),
                                    "est_amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
        status = h["status"]
        if status != "DRAFT":
            with st.expander("Follow this order to the bank", icon=":material/route:"):
                _trail(int(pick))
        owner_of = h.get("created_by")
        may_order, may_edit, may_cancel = can_now("po.order", owner_of), can_now("po.create", owner_of), can_now("po.cancel", owner_of)
        if st.session_state.get("_po_error"):
            st.error(st.session_state.pop("_po_error"))
        c1, c2, c3, c4 = st.columns(4)
        if status == "DRAFT" and may_order:
            if c1.button("Order it", icon=":material/send:", key="po_order_btn", type="primary", help="Gives it its PO number and makes the PDF available."):
                ok, msg = order_po(int(pick))
                (refresh(done=msg) if ok else st.error(msg))
        if status in ("ORDERED", "PARTIAL") and may_order:
            c1.download_button("Download PO (PDF)", data=lambda i=int(pick): build_po_pdf(i) or b"", file_name=f"{h['po_no']}.pdf".replace(" ", "_"), mime="application/pdf",
                               icon=":material/picture_as_pdf:", key="po_pdf", on_click="ignore")          # the PDF is made only when pressed
        if status in ("ORDERED", "PARTIAL") and can_now("po.receive_goods"):
            c2.button("Receive goods", icon=":material/inventory_2:", key="po_receive_btn", on_click=_start_receive, args=(int(pick), int(h["creditor_id"]), "Receive"))
        elif status in ("ORDERED", "PARTIAL", "RECEIVED") and can_now("po.receive"):
            c2.button("Match invoice", icon=":material/receipt_long:", key="po_match_btn", on_click=_start_receive, args=(int(pick), int(h["creditor_id"]), "Match invoice"))
        if status in ("DRAFT", "ORDERED") and may_edit:
            c4.button("Change", icon=":material/edit:", key="po_edit_btn", on_click=_start_edit, args=(int(pick),))
        if status == "PARTIAL" and may_cancel:
            if c3.button("Close short", icon=":material/done_all:", key="po_short_btn", help="The rest will not come: close the order so it stops counting as owed."):
                _close_short_dialog(int(pick), h["po_no"], po["supplier"])
        if status in ("DRAFT", "ORDERED") and may_cancel:
            if c3.button("Cancel order", icon=":material/cancel:", key="po_cancel_btn"):
                def _cancel():
                    ok, msg = cancel_po(int(pick))
                    if ok:
                        flash(msg)
                    return ok, msg

                confirm_change_dialog(f"Cancel **{h['po_no']}** for **{po['supplier']}** ({php(po['total'])})?", "po_cancel_confirm", _cancel, "Yes, cancel it",
                                      warning="A cancelled order stops counting as money owed. If the supplier already has the PO, tell them.")


def _next_panel() -> None:
    """After a step: what to do now. Shown once, with only the buttons this role may use."""
    nxt = st.session_state.pop("po_next", None)
    if not nxt:
        return
    new_order = ("New order", ":material/add_shopping_cart:", "purchase-orders", {"po_mode": "New order"}, "po.create", False)
    if nxt["kind"] == "ordered":
        next_steps(f"**{nxt['po_no']}** is ordered. Open it below to download the PO (PDF) for the supplier; when the goods come, record them under Receive.",
                   [("Receive goods", ":material/inventory_2:", "purchase-orders", {"po_mode": "Receive"}, "po.receive_goods", True), new_order])
    elif nxt["kind"] == "received":
        next_steps("Goods recorded. The supplier's invoice is matched to them next.",
                   [("Match the invoice now", ":material/receipt_long:", "purchase-orders", {"po_mode": "Match invoice", "rcv_supplier": nxt["cid"]}, "po.receive", True),
                    ("Receive another delivery", ":material/inventory_2:", "purchase-orders", {"po_mode": "Receive"}, "po.receive_goods", False)])
    elif nxt["kind"] == "matched":
        next_steps(f"Invoice **{nxt['doc']}** is in its batch at the price the supplier charged.",
                   [("Open the batch and park checks", ":material/description:", "order-statements", {"os_mode": "Browse batches", "selected_statement_id": nxt["sid"]}, "invoice.enter", True),
                    ("Match another invoice", ":material/receipt_long:", "purchase-orders", {"po_mode": "Match invoice"}, "po.receive", False)])


def available_modes(role: str | None, email: str | None = None) -> list[str]:
    """The modes of this screen a role may use: everyone sees the orders; New order and Receive follow the access matrix."""
    return (["Orders"] + (["New order"] if can(role, "po.create", created_by=email, me=email) else []) + (["Receive"] if can(role, "po.receive_goods") else [])
            + (["Match invoice"] if can(role, "po.receive") else []))


def render() -> None:
    page_header("Purchase orders", "An order shows what we will owe, and when, before the supplier's invoice exists. Prices come from the Price lists.")
    st.session_state.setdefault("po_mode", "Orders")
    modes = available_modes(current_role(), (st.session_state.get("auth_user") or {}).get("email"))          # a role sees only the modes it may use (the engines refuse the rest anyway)
    if st.session_state.get("po_mode") not in modes:
        st.session_state["po_mode"] = "Orders"
    mode = st.segmented_control("What do you want to do?", modes, key="po_mode", label_visibility="collapsed", required=True, format_func=MODE_ICON.get)
    cycle_bar({"Receive": "Receive", "Match invoice": "Match"}.get(mode, "Order"))
    _next_panel()
    if mode == "New order":
        _form()
    elif mode == "Receive":
        _receive()
    elif mode == "Match invoice":
        _match_invoice()
    else:
        if st.session_state.get("po_edit_id"):
            _reset_form()
        _orders()
