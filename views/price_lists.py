"""Price lists (cash disbursement cycle, stage B1): each supplier's prices, typed in or imported from the supplier's own file. A purchase order takes its
estimate from here; the actual invoice price always prevails. Rules and lookups live in engines/price_list_service.py."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.clock import manila_today
from engines.price_list_service import (
    add_price, current_prices, import_price_rows, item_names, parse_price_list_file, price_history, set_price_active, template_csv,
)
from views.shared import MONEY_FORMAT, page_header, refresh, selectbox_creditor


def _current_tab(cid: int) -> None:
    as_of = st.date_input("Prices in force on", value=manila_today(), key="pl_as_of", help="Today by default. Pick a later date to see a price change that has not started yet.")
    df = current_prices(cid, on=as_of)
    if df.empty:
        st.info("No prices for this supplier yet. Add one under Add a price, or import the supplier's file.")
        return
    show = df.copy()
    show["change"] = ((show["unit_price"] - show["previous_price"]) / show["previous_price"] * 100).where(show["previous_price"].notna() & (show["previous_price"] > 0))
    st.dataframe(
        show[["item_name", "item_code", "unit", "unit_price", "effective_date", "previous_price", "change"]], hide_index=True, width="stretch",
        column_config={
            "item_name": "Item", "item_code": "Code", "unit": "Unit",
            "unit_price": st.column_config.NumberColumn("Price", format=MONEY_FORMAT),
            "effective_date": st.column_config.DateColumn("Since"),
            "previous_price": st.column_config.NumberColumn("Price before", format=MONEY_FORMAT),
            "change": st.column_config.NumberColumn("Change", format="%+.1f%%"),
        })
    st.caption(f"{len(df)} item(s).")

    with st.expander("History of one item, and switching a wrong price off"):
        item = st.selectbox("Item", options=list(df["item_name"]), key="pl_hist_item")
        hist = price_history(cid, item)
        st.dataframe(hist[["unit_price", "effective_date", "is_active"]], hide_index=True, width="stretch",
                     column_config={"unit_price": st.column_config.NumberColumn("Price", format=MONEY_FORMAT), "effective_date": st.column_config.DateColumn("Starts"),
                                    "is_active": st.column_config.CheckboxColumn("In use")})
        labels = {int(r.price_id): f"{r.unit_price:,.2f} from {r.effective_date}" + ("" if r.is_active else " (off)") for r in hist.itertuples()}
        pick = st.selectbox("Price row", options=list(labels), format_func=labels.get, key="pl_hist_pick")
        row_on = bool(hist.set_index("price_id").loc[pick, "is_active"])
        if st.button("Switch this price off" if row_on else "Switch this price back on", icon=":material/toggle_off:" if row_on else ":material/toggle_on:", key="pl_toggle"):
            ok, msg = set_price_active(int(pick), not row_on)
            (refresh(done=msg) if ok else st.error(msg))


def _add_tab(cid: int) -> None:
    st.caption("Type the item the way the supplier writes it. Choose an item already on the list to give it a new price, or type a new name.")
    names = item_names(cid)
    c1, c2 = st.columns([3, 1])
    item = c1.selectbox("Item", options=names, index=None, accept_new_options=True, placeholder="Choose or type an item", key="pl_add_item")
    unit = c2.text_input("Unit", placeholder="pc, set, box", key="pl_add_unit")
    c3, c4, c5 = st.columns(3)
    price = c3.number_input("Price", min_value=0.0, step=1.0, format="%.2f", key="pl_add_price")
    start = c4.date_input("Starts on", value=manila_today(), key="pl_add_date", help="The price applies from this date until a later one is added.")
    code = c5.text_input("Item code (optional)", key="pl_add_code")
    replace = st.checkbox("Replace the price already on this date, if there is one", key="pl_add_replace")
    if st.button("Save price", icon=":material/save:", key="pl_add_btn"):
        ok, msg = add_price(cid, item or "", unit, price, start, code, replace=replace)
        (refresh(done=msg) if ok else st.error(msg))


def _import_tab(cid: int) -> None:
    st.caption("Upload the supplier's price list (.csv or .xlsx). Each line needs an item and a price; Unit, Item code and Effective date are used when the file has them. "
               "You see the lines first, nothing is saved until you press Import.")
    st.download_button("Download a starter file", template_csv(), file_name="price_list_template.csv", mime="text/csv", icon=":material/download:", key="pl_template")
    upload = st.file_uploader("Price list file", type=["csv", "xlsx"], key="pl_upload")
    if upload is None:
        return
    rows, messages = parse_price_list_file(upload.getvalue(), upload.name)
    for m in messages:
        (st.error if not rows else st.warning)(m)
    if not rows:
        return
    default_date = st.date_input("Prices start on", value=manila_today(), key="pl_import_date", help="Used for every line that has no date of its own in the file.")
    dup = st.radio("If an item already has a price on the same date", ["Keep the old price", "Replace it with the file's price"], horizontal=True, key="pl_import_dup")
    preview = pd.DataFrame(rows)
    preview["effective_date"] = preview["effective_date"].apply(lambda d: d or default_date)
    st.dataframe(preview[["item_name", "item_code", "unit", "unit_price", "effective_date"]], hide_index=True, width="stretch",
                 column_config={"item_name": "Item", "item_code": "Code", "unit": "Unit", "unit_price": st.column_config.NumberColumn("Price", format=MONEY_FORMAT),
                                "effective_date": st.column_config.DateColumn("Starts")})
    if st.button(f"Import {len(rows)} price(s)", icon=":material/upload_file:", key="pl_import_btn"):
        result = import_price_rows(cid, rows, default_date, "replace" if dup.startswith("Replace") else "skip")
        refresh(done=f"Price list: {result['added']} added, {result['replaced']} replaced, {result['skipped']} skipped.")


def render() -> None:
    page_header("Price lists", "Each supplier's prices. A purchase order takes its estimate from here; the actual invoice price always prevails.")
    cid = selectbox_creditor("Supplier", "SUPPLIER", "pl_supplier")
    if cid is None:
        return
    tab_current, tab_add, tab_import = st.tabs(["Current prices", "Add a price", "Import a file"], key="pl_tab", default="Current prices", on_change="rerun")
    with tab_current:
        _current_tab(int(cid))
    with tab_add:
        _add_tab(int(cid))
    with tab_import:
        _import_tab(int(cid))
