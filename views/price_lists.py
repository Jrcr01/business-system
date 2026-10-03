"""Price lists (cash disbursement cycle, stage B1): each supplier's prices, typed in or imported from the supplier's own file. A purchase order takes its
estimate from here; the actual invoice price always prevails. Rules and lookups live in engines/price_list_service.py."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines import discount_rules as dr
from engines import price_template_service as pt
from engines import product_catalog as pc
from engines.clock import manila_today
from engines.price_list_service import add_price, current_prices, item_names, price_history, set_price_active
from views.shared import MONEY_FORMAT, confirm_change_dialog, date_col, done_or_error, flash, page_header, php, refresh, section, selectbox_creditor

STATUS_TEXT = {"new": "new", "changed": "changed", "same": "same", "missing": "not in file"}


def _current_tab(cid: int, writer: bool = True) -> None:
    as_of = st.date_input("Prices in force on", value=manila_today(), key="pl_as_of", help="Today by default. Pick a later date to see a price change that has not started yet.")
    df = current_prices(cid, on=as_of)
    if df.empty:
        st.caption("No prices for this supplier yet." + (" Add one under Add a price, or use Import Excel." if writer else ""))
        return
    show = df.copy()
    show["change"] = ((show["unit_price"] - show["previous_price"]) / show["previous_price"] * 100).where(show["previous_price"].notna() & (show["previous_price"] > 0))
    st.dataframe(
        show[["item_name", "item_code", "unit", "unit_price", "effective_date", "previous_price", "change"]], hide_index=True, width="stretch",
        column_config={
            "item_name": "Item", "item_code": "Code", "unit": "Unit",
            "unit_price": st.column_config.NumberColumn("Price", format=MONEY_FORMAT),
            "effective_date": date_col("Since"),
            "previous_price": st.column_config.NumberColumn("Price before", format=MONEY_FORMAT),
            "change": st.column_config.NumberColumn("Change", format="%+.1f%%"),
        })
    st.caption(f"{len(df)} item(s).")

    with st.expander("History of one item" + (", and switching a wrong price off" if writer else "")):
        item = st.selectbox("Item", options=list(df["item_name"]), key="pl_hist_item")
        hist = price_history(cid, item)
        st.dataframe(hist[["unit_price", "effective_date", "is_active"]], hide_index=True, width="stretch",
                     column_config={"unit_price": st.column_config.NumberColumn("Price", format=MONEY_FORMAT), "effective_date": date_col("Starts"),
                                    "is_active": st.column_config.CheckboxColumn("In use")})
        if not writer:
            return
        labels = {int(r.price_id): f"{php(r.unit_price)} from {r.effective_date}" + ("" if r.is_active else " (off)") for r in hist.itertuples()}
        pick = st.selectbox("Price row", options=list(labels), format_func=labels.get, key="pl_hist_pick")
        row_on = bool(hist.set_index("price_id").loc[pick, "is_active"])
        if st.button("Switch this price off" if row_on else "Switch this price back on", icon=":material/toggle_off:" if row_on else ":material/toggle_on:", key="pl_toggle"):
            if row_on:
                confirm_change_dialog(f"Switch off the price **{labels[pick]}** for **{item}**?", "pl_toggle_confirm", lambda: _toggle(int(pick), False), "Yes, switch it off",
                                      warning="It stops being used for new orders. It stays in the history and can be switched back on.")
            else:
                done_or_error(*set_price_active(int(pick), True))


def _reconcile_section(cid: int, writer: bool) -> None:
    """Items whose last matched invoice was priced differently from the list: the invoice is the truth, so the list is corrected here with one button (engines/price_reconcile.py)."""
    from engines.price_reconcile import invoice_vs_list, update_to_invoice
    diff = invoice_vs_list(cid, st.session_state.get("pl_as_of"))
    if diff.empty:
        return
    section("Different from the last invoice", help="The last invoice matched to an order was priced differently from this list (after the supplier's discount). The invoice is the truth: update the list when the supplier's new price stands.")
    st.dataframe(diff, hide_index=True, width="stretch",
                 column_config={"item_name": "Item", "unit": "Unit", "list_net": st.column_config.NumberColumn("List, net", format=MONEY_FORMAT),
                                "invoiced": st.column_config.NumberColumn("Last invoice, net", format=MONEY_FORMAT), "difference": st.column_config.NumberColumn("Difference", format="%+,.2f"),
                                "difference_pct": st.column_config.NumberColumn("%", format="%+.1f%%"), "invoice_no": "Invoice", "invoice_date": date_col("Dated")})
    if writer:
        c1, c2 = st.columns([4, 1.6], vertical_alignment="bottom")
        pick = c1.selectbox("Item", list(diff["item_name"]), key="pl_recon_pick")
        if c2.button("Update the list", icon=":material/published_with_changes:", type="primary", key="pl_recon_go", width="stretch",
                     help="Adds the invoice price to the list from the invoice's date. The old price stays in the history."):
            done_or_error(*update_to_invoice(cid, pick))


def _prices_tab(cid: int, writer: bool) -> None:
    """ONE prices screen (owner, 2026-10-03): this supplier's prices with what the last invoices charged, or every supplier side by side with the catalog."""
    show = st.segmented_control("Show", ["This supplier", "All suppliers side by side"], default="This supplier", required=True, key="pl_show", label_visibility="collapsed")
    if show == "This supplier":
        _current_tab(cid, writer)
        _reconcile_section(cid, writer)
    else:
        _products_tab(cid, writer)


def _then_flash(result: tuple[bool, str]) -> tuple[bool, str]:
    if result[0]:
        flash(result[1])
    return result


def _toggle(price_id: int, on: bool):
    ok, msg = set_price_active(price_id, on)
    if ok:
        flash(msg)
    return ok, msg


def _add_tab(cid: int) -> None:
    st.caption("Choose a product from the catalog (or one already on this supplier's list) to give it a price, or type a new item. Many products at once: use Import Excel.")
    catalog = dict(zip(pc.list_products()["name"], pc.list_products()["product_id"]))
    names = sorted(set(item_names(cid)) | set(catalog), key=str.lower)
    c1, c2 = st.columns([3, 1])
    item = c1.selectbox("Item", options=names, index=None, accept_new_options=True, placeholder="Choose or type an item", key="pl_add_item")
    unit = c2.text_input("Unit", placeholder="pc, set, box", key="pl_add_unit")
    c3, c4, c5 = st.columns(3)
    price = c3.number_input("Price", min_value=0.0, step=1.0, format="%.2f", key="pl_add_price")
    start = c4.date_input("Starts on", value=manila_today(), key="pl_add_date", help="The price applies from this date until a later one is added.")
    code = c5.text_input("Item code (optional)", key="pl_add_code")
    replace = st.checkbox("Replace the price already on this date, if there is one", key="pl_add_replace")
    if st.button("Save price", type="primary", icon=":material/save:", key="pl_add_btn"):
        ok, msg = add_price(cid, item or "", unit, price, start, code, replace=replace)
        if ok and item in catalog:
            pc.link_price_rows(cid, item, int(catalog[item]))             # the price belongs to the catalog product
        (refresh(done=msg) if ok else st.error(msg))


def _import_tab(cid: int, supplier: str) -> None:
    st.caption("Download the template, fill the sheet for each kind of product (Tires, Battery, Mags, Rims), and upload it. You see every line first; nothing is saved until you press Import. "
               "List prices only: the supplier's discount is set on the Discounts tab.")
    d1, d2 = st.columns(2)
    xlsx = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"          # both files are made only when their button is pressed
    d1.download_button("Download the blank template", data=pt.build_template, file_name="price_list_template.xlsx", icon=":material/download:", key="pt_blank",
                       mime=xlsx, on_click="ignore")
    d2.download_button("Download with this supplier's current prices", data=lambda c=cid: pt.build_template(c), file_name=f"price_list_{supplier}.xlsx".replace(" ", "_"),
                       icon=":material/edit_note:", key="pt_current", mime=xlsx, on_click="ignore",
                       help="Change the prices (and the date) in this file and upload it back. Rows you leave alone are ignored as 'same'.")
    upload = st.file_uploader("The filled template (.xlsx)", type=["xlsx"], key="pt_upload")
    if upload is None:
        return
    start = st.date_input("Prices start on", value=manila_today(), key="pt_default_date", help="Used for every row that has no Effective date of its own.")
    parsed = pt.parse_workbook(upload.getvalue(), default_date=start)
    counts = ", ".join(f"{k}: {v}" for k, v in parsed["counts"].items()) or "no sheets read"
    st.caption(f"Rows read: {counts}.")
    if parsed["problems"]:
        with st.expander(f"{len(parsed['problems'])} problem(s): these rows are not saved until fixed", expanded=True, icon=":material/warning:"):
            for p in parsed["problems"]:
                st.warning(p)
    if not parsed["rows"]:
        return
    cmp = pt.compare(cid, parsed)
    summary = cmp["status"].value_counts().to_dict()
    st.markdown("  ·  ".join(f"**{summary.get(k, 0)}** {STATUS_TEXT[k]}" for k in ("new", "changed", "same", "missing")))
    cats = [c for c in pc.CATEGORIES if c in set(cmp["category"])]
    for tab, cat in zip(st.tabs(cats, key="pt_prev_tab"), cats):
        with tab:
            view = cmp[cmp["category"] == cat]
            st.dataframe(view[["name", "old_price", "new_price", "change_pct", "status"]].assign(status=view["status"].map(STATUS_TEXT)), hide_index=True, width="stretch",
                         column_config={"name": "Product", "old_price": st.column_config.NumberColumn("List before", format=MONEY_FORMAT),
                                        "new_price": st.column_config.NumberColumn("List now", format=MONEY_FORMAT), "change_pct": st.column_config.NumberColumn("Change", format="%+.1f%%"),
                                        "status": "Status"})
    dup = st.radio("If a product already has a price on the same date", ["Keep the old price", "Replace it with the file's price"], horizontal=True, key="pt_dup")
    if st.button(f"Import {len(parsed['rows'])} row(s)", icon=":material/upload_file:", key="pt_go", type="primary",
                 help="Rows with problems are left out." if parsed["problems"] else None):
        ok, msg, _ = pt.import_workbook(cid, parsed, "replace" if dup.startswith("Replace") else "skip")
        (refresh(done=msg) if ok else st.error(msg))


def _discounts_tab(cid: int) -> None:
    st.caption("Suppliers give a chain of discounts on their list prices, like less 5%, less 5%. Each discount is taken off what is left by the one before, "
               "so 5, 5 on 7,600 gives 6,859.00 (not 10% off). A purchase order line starts with the discount set here and can be changed on that line.")
    current = dr.list_discounts(cid)
    if current.empty:
        st.info("No discount set for this supplier. List prices are used as they are.")
    else:
        st.dataframe(current.assign(brand=current["brand"].replace("", "All brands")), hide_index=True, width="stretch",
                     column_config={"brand": "Brand", "chain": None, "label": "Discount", "effective_date": st.column_config.DateColumn("Since")})
    section("Set a discount")
    c1, c2, c3 = st.columns([2, 2, 1.5])
    brand = c1.selectbox("Brand", options=pc.brands(), index=None, accept_new_options=True, placeholder="All brands of this supplier", key="dr_brand")
    chain = c2.text_input("Discounts in %, in order", placeholder="5, 5", key="dr_chain", help="Type 5, 5 for 'less 5%, less 5%'. Leave blank for no discount from the date.")
    start = c3.date_input("Starts on", value=manila_today(), key="dr_start")
    parsed = dr.parse_chain(chain)
    if parsed:
        st.caption(f"Example: a list price of 10,000 becomes {dr.net_price(10000, parsed):,.2f} ({dr.chain_label(parsed)}).")
    if st.button("Save discount", type="primary", icon=":material/save:", key="dr_save"):
        ok, msg = dr.set_discount(cid, brand or "", chain, start)
        (refresh(done=msg) if ok else st.error(msg))


def _products_tab(cid: int, writer: bool = True) -> None:
    st.caption("The catalog holds each product once, whichever supplier sells it, under our own item code (SKU). Two suppliers' prices for the same product are compared here. "
               "Products are created when you import the template; you can also add one here.")
    category = st.segmented_control("Category", pc.CATEGORIES, default=pc.CATEGORIES[0], key="prod_cat", required=True)
    section("Prices side by side")
    df = pc.supplier_prices(category=category)
    show_all = st.toggle("Show products with only one supplier", value=False, key="prod_all")
    shown = df if (show_all or df.empty) else df[df.groupby("product_id")["supplier"].transform("count") > 1]
    if shown.empty:
        st.info("No product has prices from two or more suppliers yet." if not show_all else "No priced products in this category yet.")
    else:
        st.dataframe(shown.assign(cheapest=shown["cheapest"].map({True: "cheapest", False: ""}), chain=shown["chain"].map(dr.chain_label)), hide_index=True, width="stretch",
                     column_config={"product_id": None, "sku": "SKU", "product": "Product", "brand": "Brand", "supplier": "Supplier", "list_price": st.column_config.NumberColumn("List", format=MONEY_FORMAT),
                                    "chain": "Discount", "net_price": st.column_config.NumberColumn("Net", format=MONEY_FORMAT), "effective_date": st.column_config.DateColumn("Since"),
                                    "cheapest": ""})
    section("Products in the catalog")
    fields = pc.CATEGORY_FIELDS[category]
    products = pc.list_products(category, active_only=not st.toggle("Include discontinued", value=False, key="prod_old"))
    if products.empty:
        st.caption("No products in this category yet.")
    else:
        wide = pc.spec_columns(products, category)
        st.dataframe(wide[["sku", "name", "brand"] + [f["key"] for f in fields] + ["suppliers", "is_active"]], hide_index=True, width="stretch",
                     column_config={"sku": "SKU", "name": "Standard name", "brand": "Brand", "suppliers": "Suppliers", "is_active": st.column_config.CheckboxColumn("Active"),
                                    **{f["key"]: f["label"] for f in fields}})
        if writer:
            labels = {int(r.product_id): f"{r.sku} · {r.name}" + ("" if r.is_active else " (discontinued)") for r in products.itertuples()}
            c1, c2 = st.columns([4, 1.4], vertical_alignment="bottom")
            pick = c1.selectbox("Change a product", options=list(labels), format_func=labels.get, key=f"prod_pick_{category}")
            on = bool(products.set_index("product_id").loc[pick, "is_active"])
            if c2.button("Discontinue" if on else "Bring back", icon=":material/block:" if on else ":material/restore:", key=f"prod_toggle_{category}", width="stretch",
                         help="A product is never deleted: its prices and history stay."):
                if on:
                    confirm_change_dialog(f"Discontinue **{labels[pick]}**?", "prod_off_confirm", lambda p=int(pick): _then_flash(pc.set_product_active(p, False)), "Yes, discontinue",
                                          warning="It is no longer offered on new orders. Its prices and history stay, and it can be brought back.")
                else:
                    done_or_error(*pc.set_product_active(int(pick), True))
    if not writer:
        return
    section("Add a product")
    brand = st.selectbox("Brand", options=pc.brands(category), index=None, accept_new_options=True, placeholder="Choose or type the brand", key=f"prod_brand_{category}")
    cols = st.columns(min(len(fields), 4))
    values = {}
    for i, f in enumerate(fields):
        box = cols[i % len(cols)]
        key = f"prod_{category}_{f['key']}"
        values[f["key"]] = (box.selectbox(f["label"] + (" *" if f["required"] else ""), [""] + f["choices"], key=key) if f["choices"]
                            else box.text_input(f["label"] + (" *" if f["required"] else ""), key=key, placeholder=f["note"]))
    ready = bool(brand) and all(values[f["key"]].strip() for f in fields if f["required"])
    if ready:
        st.caption(f"Standard name: **{pc.standard_name(category, brand, **values)}**")
    if st.button("Add product", type="primary", icon=":material/add:", key="prod_add", disabled=not ready):
        _, name, created = pc.get_or_create_product(category, brand, **values)
        refresh(done=f"{name} added." if created else f"{name} is already in the catalog.")


def render() -> None:
    page_header("Price lists", "Each supplier's prices. A purchase order takes its estimate from here; the actual invoice price always prevails.")
    cid = selectbox_creditor("Supplier", "SUPPLIER", "pl_supplier")
    if cid is None:
        return
    from engines.permissions import can
    from views.login import current_role
    from views.shared import selectbox_creditor_label

    writer = can(current_role(), "price.write")                  # a role that may only look sees the prices and the catalog, not the tabs that change them
    names = ["Prices", "Add a price", "Import Excel", "Discounts"] if writer else ["Prices"]
    tabs = dict(zip(names, st.tabs(names, key="pl_tab", default="Prices", on_change="rerun")))
    draw = {"Prices": lambda: _prices_tab(int(cid), writer), "Add a price": lambda: _add_tab(int(cid)),
            "Import Excel": lambda: _import_tab(int(cid), selectbox_creditor_label("SUPPLIER", int(cid))), "Discounts": lambda: _discounts_tab(int(cid))}
    for name, tab in tabs.items():
        if tab.open:                                      # only the tab you are looking at runs
            with tab:
                draw[name]()
