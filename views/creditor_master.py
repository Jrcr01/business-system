"""Creditor Master screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.common import cell_text
from engines.creditor_rules import add_creditor
from engines.creditor_rules import creditor_reference_counts
from engines.creditor_rules import deactivate_creditor
from engines.creditor_rules import delete_or_deactivate_creditor
from engines.creditor_rules import reactivate_creditor
from engines.creditor_rules import update_creditor
from engines.duplicate_service import creditor_warnings
from engines.statement_rules import DEFAULT_SUPPLIER_TERMS

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    SUPPLIER_TERMS_OPTIONS,
    date_col,
    dup_needs_confirm,
    dup_resume,
    guard_duplicate_submit,
    money_col,
    page_header,
    q,
    refresh,
    section,
    status_badge,
    status_text,
)


def render() -> None:
    page_header("Creditors")
    st.caption("Master registry of payees tagged supplier, lender, or bank loan. Credit card payees stay in their own track (Credit cards page).")

    creditor_master_df = q(
        "SELECT creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email, terms, is_active, datetime(created_at, 'localtime') AS created_at "
        "FROM creditors WHERE creditor_type IN ('SUPPLIER', 'LENDER', 'BANK_LOAN') ORDER BY creditor_type, creditor_name"
    )
    creditor_master_df["is_active"] = creditor_master_df["is_active"].astype(int) == 1
    filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 1])
    with filter_col1:
        search_master = st.text_input(
            "Search", placeholder="Name, TIN, contact person/number, or email", key="creditor_master_search"
        )
    with filter_col2:
        type_filter_master = st.multiselect(
            "Filter: type", options=["SUPPLIER", "LENDER", "BANK_LOAN"], key="creditor_master_type_filter"
        )
    with filter_col3:
        show_inactive = st.checkbox("Show inactive too", key="creditor_master_show_inactive")
    original_master_df = creditor_master_df[creditor_master_df["creditor_type"].isin(type_filter_master)] if type_filter_master else creditor_master_df
    if not show_inactive:
        original_master_df = original_master_df[original_master_df["is_active"]]
    if search_master.strip():
        needle = search_master.strip().lower()
        search_cols = ["creditor_name", "tin", "contact_person", "contact_number", "email"]
        hit = pd.Series(False, index=original_master_df.index)
        for col in search_cols:
            hit = hit | original_master_df[col].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
        original_master_df = original_master_df[hit]
    original_master_df = original_master_df.reset_index(drop=True)

    st.caption(
        "Add, edit, or deactivate creditors directly in the grid below, then Save. A new row needs a Name and Type. "
        "Uncheck Active to deactivate (reversible — check it again to reactivate); deleting a row outright only "
        "removes it completely if it has no transaction history yet, otherwise it's deactivated instead, same as "
        "unchecking Active. A row's Type can't be changed once it has transaction history, to avoid silently "
        "reclassifying existing batches, loan notes, or bank loans."
    )
    edited_master_df = st.data_editor(
        original_master_df,
        hide_index=True,
        num_rows="dynamic",
        disabled=["creditor_id", "created_at"],
        key="creditor_master_editor",
        column_config={
            "creditor_id": st.column_config.NumberColumn("ID"),
            "creditor_name": st.column_config.TextColumn("Name", default=""),
            "creditor_type": st.column_config.SelectboxColumn("Type", options=["SUPPLIER", "LENDER", "BANK_LOAN"], default="SUPPLIER"),
            "tin": st.column_config.TextColumn("TIN", default=""),
            "contact_person": st.column_config.TextColumn("Contact person", default=""),
            "contact_number": st.column_config.TextColumn("Contact number", default=""),
            "email": st.column_config.TextColumn("Email", default=""),
            "terms": st.column_config.SelectboxColumn("Terms", options=SUPPLIER_TERMS_OPTIONS, default=DEFAULT_SUPPLIER_TERMS),
            "is_active": st.column_config.CheckboxColumn("Active", default=True),
            "created_at": st.column_config.TextColumn("Created"),
        },
    )

    if st.button("Save creditor changes", icon=":material/save:", type="primary", key="save_creditor_master_edits") or dup_resume("save_creditor_master"):
        if dup_needs_confirm("save_creditor_master", [
            w for _, r in edited_master_df.iterrows()
            if pd.isna(r["creditor_id"]) and cell_text(r["creditor_name"]).strip()
            for w in creditor_warnings(cell_text(r["creditor_name"]), cell_text(r["creditor_type"]))
        ]):
            pass
        elif not guard_duplicate_submit("save_creditor_master", edited_master_df.to_csv(index=False)):
            st.warning("Duplicate submission ignored — identical to the click just before it.")
        else:
            original_by_id = original_master_df.set_index("creditor_id")
            seen_ids = set()
            added = updated = reactivated = deactivated = removed = 0
            row_errors = []

            for _, r in edited_master_df.iterrows():
                if pd.isna(r["creditor_id"]):
                    name = cell_text(r["creditor_name"]).strip()
                    ctype = cell_text(r["creditor_type"]).strip()
                    if not name and not ctype:
                        continue  # untouched blank template row
                    if not name or ctype not in ("SUPPLIER", "LENDER", "BANK_LOAN"):
                        row_errors.append("New creditor row needs a Name and a Type.")
                        continue
                    add_creditor(
                        name, ctype, cell_text(r["tin"]), cell_text(r["contact_person"]),
                        cell_text(r["contact_number"]), cell_text(r["email"]), cell_text(r["terms"]),
                    )
                    added += 1
                    continue

                cid = int(r["creditor_id"])
                seen_ids.add(cid)
                orig = original_by_id.loc[cid]

                new_type = cell_text(r["creditor_type"]).strip() or orig["creditor_type"]
                if new_type != orig["creditor_type"] and creditor_reference_counts(cid):
                    row_errors.append(f"{orig['creditor_name']}: can't change type — it already has transaction history. Kept as {orig['creditor_type']}.")
                    new_type = orig["creditor_type"]

                details_changed = (
                    cell_text(r["creditor_name"]) != cell_text(orig["creditor_name"])
                    or new_type != orig["creditor_type"]
                    or cell_text(r["tin"]) != cell_text(orig["tin"])
                    or cell_text(r["contact_person"]) != cell_text(orig["contact_person"])
                    or cell_text(r["contact_number"]) != cell_text(orig["contact_number"])
                    or cell_text(r["email"]) != cell_text(orig["email"])
                    or cell_text(r["terms"]) != cell_text(orig["terms"])
                )
                if details_changed:
                    update_creditor(
                        cid, cell_text(r["creditor_name"]), new_type, cell_text(r["tin"]),
                        cell_text(r["contact_person"]), cell_text(r["contact_number"]), cell_text(r["email"]),
                        cell_text(r["terms"]),
                    )
                    updated += 1

                if bool(r["is_active"]) != bool(orig["is_active"]):
                    if r["is_active"]:
                        reactivate_creditor(cid)
                        reactivated += 1
                    else:
                        deactivate_creditor(cid)
                        deactivated += 1

            for removed_id in set(original_by_id.index) - seen_ids:
                ok, msg = delete_or_deactivate_creditor(int(removed_id))
                if ok:
                    removed += 1
                else:
                    row_errors.append(msg)

            summary = [
                f"{n} {label}" for n, label in
                [(added, "added"), (updated, "updated"), (reactivated, "reactivated"), (deactivated, "deactivated"), (removed, "removed")]
                if n
            ]
            if summary:
                st.success("Creditors: " + ", ".join(summary) + ".")
            for e in row_errors:
                st.error(e)
            if not summary and not row_errors:
                st.info("No changes to save.")
            if summary:
                refresh()
