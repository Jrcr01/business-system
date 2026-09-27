"""Order Statement (batches, invoices, credit memos, checks) screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this tab is open."""
from __future__ import annotations

import io
import pandas as pd
import re
import sqlite3
import streamlit as st

from datetime import date
from datetime import datetime
from datetime import timedelta
from engines.batch_statement_service import bulk_summary_rows
from engines.batch_statement_service import generate_batch_statement_pdf
from engines.batch_statement_service import generate_batch_statement_thermal_pdf
from engines.batch_statement_service import generate_bulk_summary_pdf
from engines.check_rules import allocate_check_to_invoices
from engines.check_rules import apply_check_correction
from engines.check_rules import cancel_check
from engines.check_rules import edit_parked_check
from engines.check_rules import get_existing_check_dates_for_creditor
from engines.check_rules import get_latest_check_for_creditor
from engines.check_rules import issue_checks_grid
from engines.check_rules import replace_check
from engines.check_rules import unpark_checks
from engines.common import cell_text
from engines.common import has_value
from engines.common import php
from engines.common import q_live
from engines.db_engine import promote_draft_statement
from engines.duplicate_service import check_warnings
from engines.duplicate_service import credit_memo_warnings
from engines.duplicate_service import invoice_warnings
from engines.invoice_import_service import get_template_csv
from engines.invoice_import_service import match_supplier
from engines.invoice_import_service import parse_invoice_spreadsheet
from engines.invoice_import_service import parse_invoice_workbook
from engines.invoice_ocr_service import extract_invoice_fields
from engines.invoice_template_service import build_invoice_entry_workbook
from engines.last_modified_sql import statement_modified_sql
from engines.park_draft_service import clear_park_draft
from engines.park_draft_service import load_park_draft
from engines.park_draft_service import save_park_draft
from engines.soa_matcher import reconcile_vendor_soa
from engines.statement_rules import add_credit_memo
from engines.statement_rules import add_invoice
from engines.statement_rules import confirm_credit_memos
from engines.statement_rules import create_order_statement
from engines.statement_rules import delete_credit_memo
from engines.statement_rules import delete_invoice
from engines.statement_rules import edit_credit_memo
from engines.statement_rules import edit_invoice
from engines.statement_rules import get_batch_balance
from engines.statement_rules import get_batch_memo_status
from engines.statement_rules import get_invoices_with_remaining
from engines.statement_rules import lock_statement
from engines.statement_rules import parse_less_pct
from engines.statement_rules import resolve_due_date
from engines.statement_rules import split_remaining_evenly
from engines.statement_rules import sync_invoice_payment_status
from engines.statement_rules import waterfall_allocate_preview

from views.shared import (  # noqa: F401
    PRINT_CHECKS_IN_APP,
    PdfReader,
    PdfWriter,
    batch_scoped_key,
    confirm_cancel_dialog,
    confirm_create_dialog,
    confirm_unlock_dialog,
    creditor_options,
    date_preset_filter,
    default_index,
    dup_needs_confirm,
    dup_resume,
    guard_duplicate_submit,
    pdf_preview_and_download,
    q,
    refresh,
    render_assign_number_section,
    render_check_print_section,
    selectbox_creditor,
)


def render() -> None:
    st.header("Order Statement")
    st.caption("Supplier records now live in Creditor Master. Open or create a batch below — Invoices, Credit Memo, and Checks all follow whichever batch is open.")

    statements_df = q(
        """SELECT os.statement_id, os.statement_ref, c.creditor_name, os.total_amount,
                  v.credit_memo_total, v.net_total,
                  v.net_total - COALESCE(
                      (SELECT SUM(amount) FROM checks_issued
                       WHERE statement_id = os.statement_id AND status = 'Active' AND check_date <= DATE('now', 'localtime')), 0
                  ) AS outstanding,
                  os.status, os.is_locked, datetime(os.created_at, 'localtime') AS created_at,
                  """ + statement_modified_sql("os") + """ AS last_modified,
                  CASE WHEN os.status = 'Cancelled' THEN 'Cancelled'
                       WHEN EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active' AND k.printed_at IS NOT NULL) THEN 'Issued'
                       WHEN EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active') THEN 'Parked'
                       ELSE 'Draft' END AS stage
           FROM order_statements os
           JOIN creditors c ON os.creditor_id = c.creditor_id
           JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
           ORDER BY os.statement_id DESC"""
    )
    selected_sid = st.session_state.get("selected_statement_id")

    # Streamlit forbids writing to st.session_state["os_mode"] after the os_mode widget below
    # has already been instantiated this run (even right before a rerun) -- so a mode switch
    # requested by a button click has to be staged here, before the widget renders, and applied
    # on the rerun that follows.
    if "_os_mode_next" in st.session_state:
        st.session_state["os_mode"] = st.session_state.pop("_os_mode_next")

    os_mode = st.segmented_control(
        "What do you want to do?",
        options=["🔍 Browse Batches", "➕ New Batch"],
        default="🔍 Browse Batches",
        key="os_mode",
    )

    if os_mode == "➕ New Batch":
        st.subheader("Create order statement")
        st.caption("Total starts at ₱0.00 and is never typed in — it recalculates automatically as invoice lines are added to the batch.")
        with st.form("new_statement_form", border=False):
            os_creditor = selectbox_creditor("Supplier", "SUPPLIER", "os_creditor")
            if st.form_submit_button("Create draft", icon=":material/note_add:") and os_creditor:
                if guard_duplicate_submit("create_order_statement", os_creditor):
                    new_sid = create_order_statement(os_creditor)
                    st.session_state["selected_statement_id"] = new_sid
                    st.session_state["_os_mode_next"] = "🔍 Browse Batches"
                    st.success("Draft order statement created — opened below.")
                    refresh()
                else:
                    st.warning("Duplicate submission ignored — identical to the click just before it.")

        with st.expander("Import many batches from one Excel file (one sheet = one new batch)"):
            st.caption(
                "Upload an .xlsx or .xlsm (the macro entry workbook) where each sheet (tab) holds one supplier's invoices in the usual columns "
                "(Doc Ref / Date / Amount / Less %). Name each tab after its supplier. The same supplier can have "
                "several tabs — every tab becomes its own new batch. You review everything before anything is saved."
            )
            st.download_button(
                "Download Excel entry template (with pop-up form)", data=build_invoice_entry_workbook,
                file_name=f"invoice_entry_{date.today():%Y-%m-%d}.xlsm", mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                icon=":material/download:", key="multi_import_template_dl",
                help="A macro-enabled workbook with the supplier list refreshed from the system right now. Open it, enable macros, and use the Add invoice button.",
            )
            mb_file = st.file_uploader("Workbook", type=["xlsx", "xlsm"], key="multi_import_uploader")
            if st.button("Read workbook", icon=":material/upload_file:", key="multi_import_read_btn"):
                if not mb_file:
                    st.warning("Upload a file first.")
                else:
                    mb_sheets, mb_problems = parse_invoice_workbook(mb_file.getvalue())
                    st.session_state["multi_import_sheets"] = [
                        {"sheet": sh.sheet_name, "messages": sh.messages,
                         "rows": [{"doc_ref": r.doc_ref, "invoice_date": r.invoice_date, "amount": r.amount, "less_pct": r.less_pct} for r in sh.rows]}
                        for sh in mb_sheets
                    ]
                    st.session_state["multi_import_problems"] = mb_problems
                    st.session_state.pop("multi_import_result", None)
            for problem in st.session_state.get("multi_import_problems", []):
                st.error(problem)

            mb_supplier_options = creditor_options("SUPPLIER")
            mb_state = st.session_state.get("multi_import_sheets", [])
            mb_plan = []
            if mb_state:
                st.markdown("**Review** — pick the supplier for each tab (tabs set to skip are ignored).")
                for i, sh in enumerate(mb_state):
                    good_rows = [r for r in sh["rows"] if r["doc_ref"].strip() and r["amount"] > 0]
                    guess = match_supplier(sh["sheet"], mb_supplier_options)
                    options = [None] + list(mb_supplier_options)
                    rc1, rc2 = st.columns([2, 3])
                    pick = rc2.selectbox(
                        "Supplier for tab: " + sh["sheet"], options=options,
                        index=options.index(guess) if guess in options else 0,
                        format_func=lambda cid: "— skip this tab —" if cid is None else mb_supplier_options[cid],
                        key=f"multi_import_pick_{i}_{sh['sheet']}",
                    )
                    rc1.markdown(f"**{sh['sheet']}**")
                    rc1.caption(f"{len(good_rows)} invoice(s) · {php(sum(r['amount'] for r in good_rows))}" + (f" · {len(sh['rows']) - len(good_rows)} row(s) will be skipped (missing Doc Ref or amount)" if len(sh["rows"]) != len(good_rows) else ""))
                    for m in sh["messages"][:3]:
                        rc1.caption(f":warning: {m}")
                    if pick is not None and good_rows:
                        mb_plan.append((sh["sheet"], pick, good_rows))
                mb_promote = st.checkbox("Also promote each new batch to Issued", value=True, key="multi_import_promote")
                mb_warnings = [f"{sheet}: {w}" for sheet, cid, rows_ in mb_plan
                               for w in invoice_warnings(cid, [(r["doc_ref"].strip(), r["invoice_date"], r["amount"]) for r in rows_])]
                if st.button("Create batches and import invoices", icon=":material/playlist_add:", key="multi_import_go_btn", disabled=not mb_plan) or dup_resume("multi_import"):
                    if dup_needs_confirm("multi_import", mb_warnings):
                        pass
                    elif guard_duplicate_submit("multi_import", repr([(a, b, len(c)) for a, b, c in mb_plan])):
                        results = []
                        for sheet, cid, rows_ in mb_plan:
                            terms_df = q("SELECT terms FROM creditors WHERE creditor_id = ?", (cid,))
                            terms = terms_df["terms"][0] if not terms_df.empty else ""
                            sid = create_order_statement(cid)
                            added, skipped = 0, []
                            for r in rows_:
                                try:
                                    add_invoice(cid, r["doc_ref"].strip(), r["invoice_date"], resolve_due_date(r["invoice_date"], terms),
                                                r["amount"], [r["less_pct"]] if r["less_pct"] else [], sid)
                                    added += 1
                                except sqlite3.IntegrityError:
                                    skipped.append(r["doc_ref"].strip())
                            ref = q("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (sid,))["statement_ref"][0]
                            if mb_promote and added:
                                try:
                                    ref = promote_draft_statement(sid, "Issued")
                                except ValueError:
                                    pass
                            results.append({"Tab": sheet, "Supplier": mb_supplier_options[cid], "Batch": ref, "Invoices added": added,
                                            "Already on file (skipped)": ", ".join(skipped)})
                        st.session_state["multi_import_result"] = results
                        st.session_state.pop("multi_import_sheets", None)
                        refresh()
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")
            if st.session_state.get("multi_import_result"):
                st.success("Done — new batches created:")
                st.dataframe(pd.DataFrame(st.session_state["multi_import_result"]), hide_index=True)

    else:
        st.subheader("Order statement browser")
        if statements_df.empty:
            st.caption("No order statements yet — switch to \"New Batch\" above to create the first one.")
        else:
            ref_search = st.text_input(
                "Search: statement ref", key="batch_filter_ref_search", placeholder="e.g. DRAFT-20260905...",
            )
            fc1, fc2, fc3 = st.columns(3)
            supplier_filter = fc1.multiselect(
                "Filter: supplier", options=sorted(statements_df["creditor_name"].unique()), key="batch_filter_supplier"
            )
            status_filter_browser = fc2.multiselect(
                "Filter: status", options=[s for s in ("Draft", "Parked", "Issued", "Cancelled") if s in set(statements_df["stage"])], key="batch_filter_status",
                help="Draft = no checks yet · Parked = checks parked, none printed yet · Issued = at least one check already exported/printed · Cancelled"
            )
            created_dates = pd.to_datetime(statements_df["created_at"]).dt.date
            full_range = (min(created_dates.min(), date.today()), max(created_dates.max(), date.today()))
            date_preset = fc3.segmented_control(
                "Filter: created date",
                options=["Today", "Last 7 days", "Last 30 days", "All time", "Custom"],
                default="All time",
                required=True,
                key="batch_filter_date_preset",
            )
            if date_preset == "Today":
                date_range = (date.today(), date.today())
            elif date_preset == "Last 7 days":
                date_range = (date.today() - timedelta(days=6), date.today())
            elif date_preset == "Last 30 days":
                date_range = (date.today() - timedelta(days=29), date.today())
            elif date_preset == "Custom":
                date_range = fc3.date_input("Custom range", value=full_range, key="batch_filter_date_range")
            else:  # "All time"
                date_range = full_range
            if date_preset != "Custom":
                fc3.caption(f"{date_range[0]:%Y-%m-%d} to {date_range[1]:%Y-%m-%d}")
            mod_col, _mod_spacer = st.columns([1, 2])

            filtered_statements_df = statements_df.copy()
            if ref_search.strip():
                filtered_statements_df = filtered_statements_df[
                    filtered_statements_df["statement_ref"].str.contains(ref_search.strip(), case=False, na=False)
                ]
            if supplier_filter:
                filtered_statements_df = filtered_statements_df[filtered_statements_df["creditor_name"].isin(supplier_filter)]
            if status_filter_browser:
                filtered_statements_df = filtered_statements_df[filtered_statements_df["stage"].isin(status_filter_browser)]
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
                row_dates = pd.to_datetime(filtered_statements_df["created_at"]).dt.date
                filtered_statements_df = filtered_statements_df[(row_dates >= start_d) & (row_dates <= end_d)]
            filtered_statements_df = date_preset_filter(mod_col, filtered_statements_df, "last_modified", "Filter: last modified", "batch_filter_modified_preset")
            filtered_statements_df = filtered_statements_df.reset_index(drop=True)

            st.caption("Click a row to open that batch — every action below (invoices, credit memo, lock/unlock, issue check, export) defaults to it.")
            browser_event = st.dataframe(
                filtered_statements_df.assign(status=filtered_statements_df["stage"]).drop(columns=["stage"]),      # shown status: Draft / Parked / Issued
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="batch_browser_df",
                column_config={
                    "total_amount": st.column_config.NumberColumn("Gross total", format="₱ %.2f"),
                    "credit_memo_total": st.column_config.NumberColumn("Credit memos", format="₱ %.2f"),
                    "net_total": st.column_config.NumberColumn("Net total (checks cut against this)", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding (uncleared)", format="₱ %.2f"),
                    "is_locked": st.column_config.CheckboxColumn("Locked"),
                },
            )
            selected_rows = browser_event.selection.rows if browser_event and browser_event.selection else []
            if selected_rows and selected_rows[0] < len(filtered_statements_df):
                st.session_state["selected_statement_id"] = int(filtered_statements_df.iloc[selected_rows[0]]["statement_id"])
            selected_sid = st.session_state.get("selected_statement_id")

            with st.expander("Print statements in bulk"):
                st.caption("Print the statements of several order statements in one go, as one combined PDF. Printing here does not lock batches and nothing is marked as printed, "
                           "so you can always reprint after an adjustment or revision.")
                bp1, bp2 = st.columns([2, 3])
                # "Session" = since the day checks were last printed (2026-09-27, per user: checks are not issued every day, so a calendar day or a page
                # visit is the wrong yardstick). The last print day is the latest date any check was exported / printed.
                _lp = q_live("SELECT DATE(MAX(printed_at), 'localtime') AS d FROM checks_issued")["d"][0]
                bp_last_print_day = str(_lp)[:10] if has_value(_lp) else None
                bp_scope = bp1.radio("Which order statements", ["Since last check print", "Today", "Last 7 days", "All"], horizontal=True, key="bulk_print_scope",
                                     help="'Since last check print' = created or changed on or after the day checks were last printed / exported to Chrysanth"
                                          + (f" ({bp_last_print_day})." if bp_last_print_day else " (no check has been printed yet, so this shows everything)."))
                bp_since = {"Since last check print": f"{bp_last_print_day} 00:00:00" if bp_last_print_day else "0000-00-00", "Today": f"{date.today():%Y-%m-%d} 00:00:00",
                            "Last 7 days": f"{date.today() - timedelta(days=6):%Y-%m-%d} 00:00:00", "All": "0000-00-00"}[bp_scope]
                bp_pool = statements_df[(statements_df["stage"] != "Cancelled") & (statements_df["last_modified"].astype(str) >= bp_since)].sort_values("statement_ref")
                if bp_pool.empty:
                    st.info(f"No order statements {f'created or changed since {bp_last_print_day}, the day checks were last printed,' if bp_scope == 'Since last check print' else 'in that period'} — pick a wider range above.")
                else:
                    bp_labels = {int(r["statement_id"]): f"{r['statement_ref']} · {r['creditor_name']} · {php(r['net_total'])} · {r['stage']}" for _, r in bp_pool.iterrows()}
                    bp_picked = st.multiselect(f"Statements to print ({len(bp_labels)} found — untick any you don't want)", options=list(bp_labels), default=list(bp_labels),
                                               format_func=lambda sid: bp_labels[sid], key=f"bulk_print_pick_{bp_scope}_{len(bp_labels)}")
                    bt1, bt2 = st.columns(2)
                    bp_type = bt1.radio("Statement type", ["Batch Statement (A4)", "Thermal 80mm"], horizontal=True, key="bulk_print_type")
                    bp_history = bt2.toggle("Include full check history (incl. cancelled/replaced)", value=True, key="bulk_print_history")
                    bp_unprinted = bt2.toggle("Include checks not yet printed", value=True, key="bulk_print_unprinted",
                                              help="On: every check is listed, as usual. Off: only checks already printed appear on the statements, and only they count as covering an invoice. "
                                                   "A check counts as printed once its Chrysanth run is marked printed (Batch run history > Mark as printed); parked checks and checks in a run "
                                                   "not marked printed yet (e.g. RUN 005) are left out.")
                    bp_summary_on = st.toggle("Put a summary page of the included order statements in front", value=True, key="bulk_print_summary")
                    bp_summary = bulk_summary_rows([int(x) for x in bp_picked], bp_unprinted)
                    if bp_summary:
                        st.dataframe(pd.DataFrame(bp_summary).drop(columns=["statement_id"]).rename(columns={
                            "ref": "Order statement", "supplier": "Supplier", "stage": "Status", "invoices": "Invoices", "net_total": "Net total", "checks": "Checks", "checks_total": "Checks total"}),
                            hide_index=True, column_config={"Net total": st.column_config.NumberColumn(format="₱ %.2f"), "Checks total": st.column_config.NumberColumn(format="₱ %.2f")})
                        st.caption(f"{len(bp_summary)} order statement(s) · net total {php(sum(r['net_total'] for r in bp_summary))} · {sum(r['checks'] for r in bp_summary)} check(s) {php(sum(r['checks_total'] for r in bp_summary))}")
                    bp_copies = "both"
                    bp_apply = False
                    if bp_type == "Batch Statement (A4)":
                        bp_copies = st.radio("Copies to print", ["both", "filing", "supplier"], horizontal=True, key="bulk_print_copies",
                                             format_func=lambda c: {"both": "Both (filing + supplier)", "filing": "Filing copy only", "supplier": "Supplier copy only"}[c])
                        bp_apply = st.toggle("Include payment application & running balance", value=False, key="bulk_print_apply")
                    if st.button(f"Generate one PDF for {len(bp_picked)} statement(s)", icon=":material/picture_as_pdf:", type="primary", disabled=not bp_picked, key="bulk_print_btn"):
                        merged = PdfWriter()
                        made, failed = 0, []
                        if bp_summary_on and bp_summary:
                            _scope_text = f"Since last check print ({bp_last_print_day})" if bp_scope == "Since last check print" and bp_last_print_day else bp_scope
                            _type_text = (bp_type + (f" · {dict(both='both copies', filing='filing copy', supplier='supplier copy')[bp_copies]}" if bp_type == "Batch Statement (A4)" else "")
                                          + ("" if bp_unprinted else " · printed checks only"))
                            for page in PdfReader(io.BytesIO(generate_bulk_summary_pdf(bp_summary, _scope_text, _type_text))).pages:
                                merged.add_page(page)
                        for sid in bp_picked:
                            one = (generate_batch_statement_pdf(sid, bp_history, bp_copies, bp_apply, bp_unprinted) if bp_type == "Batch Statement (A4)"
                                   else generate_batch_statement_thermal_pdf(sid, bp_history, bp_unprinted))
                            if not one:
                                failed.append(bp_labels[sid])
                                continue
                            for page in PdfReader(io.BytesIO(one)).pages:
                                merged.add_page(page)
                            made += 1
                        if made:
                            out = io.BytesIO()
                            merged.write(out)
                            st.success(f"{made} statement(s) combined into one PDF." + (f" Could not generate: {'; '.join(failed)}" if failed else ""))
                            pdf_preview_and_download(out.getvalue(), f"statements_{datetime.now():%Y%m%d_%H%M}{'_thermal' if bp_type != 'Batch Statement (A4)' else ''}.pdf",
                                                     key="bulk_print_download", label="Combined statements PDF")
                        else:
                            st.error("Could not generate any statement PDF.")

            with st.expander("Promote statement status"):
                st.caption("Informational only — checks can be issued from any status once the batch has a payable balance. Promotion is not required.")
                promotable = statements_df[statements_df["status"] == "Draft"]
                with st.form("promote_statement_form", border=False):
                    if promotable.empty:
                        st.caption("No draft statements to promote.")
                        stmt_choice = None
                    else:
                        stmt_choice = st.selectbox(
                            "Statement",
                            options=promotable["statement_id"],
                            format_func=lambda sid: f"{promotable.set_index('statement_id').loc[sid, 'statement_ref']} ({promotable.set_index('statement_id').loc[sid, 'status']})",
                            index=default_index(promotable["statement_id"], st.session_state.get("selected_statement_id")),
                            key=batch_scoped_key("stmt_choice"),
                        )
                    if st.form_submit_button("Promote to Issued", icon=":material/arrow_upward:") and stmt_choice is not None:
                        if guard_duplicate_submit("promote_statement", int(stmt_choice)):
                            try:
                                new_ref = promote_draft_statement(int(stmt_choice), "Issued")
                                st.success(f"Promoted to Issued as {new_ref}.")
                                refresh()
                            except ValueError as e:
                                st.error(str(e))
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

    batch_is_open = selected_sid is not None and selected_sid in set(statements_df["statement_id"])
    batch_scope_pick = selected_sid if batch_is_open else None

    if not batch_is_open:
        st.info("Open a batch above to work with its invoices, credit memos, and checks.")
    else:
        scoped_row = statements_df.set_index("statement_id").loc[int(batch_scope_pick)]
        scoped_creditor_id = int(q("SELECT creditor_id FROM order_statements WHERE statement_id = ?", (int(batch_scope_pick),))["creditor_id"][0])
        supplier_terms_row = q("SELECT terms FROM creditors WHERE creditor_id = ?", (scoped_creditor_id,))
        supplier_terms = supplier_terms_row["terms"][0] if not supplier_terms_row.empty else ""
        batch_is_locked = int(scoped_row["is_locked"]) == 1

        with st.container(border=True):
            batch_is_cancelled = scoped_row["status"] == "Cancelled"
            hc1, hc2, hc3, hc4 = st.columns([4, 2, 2, 1])
            with hc1:
                st.markdown(f"**{scoped_row['statement_ref']}** — {scoped_row['creditor_name']}")
                lock_badge = "🔒 Locked" if batch_is_locked else "🔓 Unlocked"
                st.caption(f"Net {php(scoped_row['net_total'])} · {scoped_row['status']} · {lock_badge} · Terms: {supplier_terms or 'not set'}")
            with hc2:
                if batch_is_cancelled:
                    st.caption("Cancelled — permanently locked.")
                elif batch_is_locked:
                    if st.button("Unlock batch", icon=":material/lock_open:", key="unlock_btn_open"):
                        confirm_unlock_dialog(int(selected_sid), scoped_row["statement_ref"])
                else:
                    has_checks = not q("SELECT 1 FROM checks_issued WHERE statement_id = ? LIMIT 1", (int(selected_sid),)).empty
                    if has_checks:
                        if st.button("Re-lock batch", icon=":material/lock:", key="relock_btn_open"):
                            ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if ok else st.error)(lock_msg)
                            if ok:
                                refresh()
                    else:
                        st.caption("Locks automatically once a check is issued.")
            with hc3:
                if not batch_is_cancelled:
                    if st.button("Cancel batch", icon=":material/cancel:", key="cancel_batch_btn"):
                        active_checks_row = q(
                            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
                            (int(selected_sid),),
                        )
                        invoice_count_row = q(
                            "SELECT COUNT(*) AS n FROM invoices WHERE statement_id = ? AND status != 'Cancelled'",
                            (int(selected_sid),),
                        )
                        confirm_cancel_dialog(
                            int(selected_sid), scoped_row["statement_ref"],
                            int(active_checks_row["n"][0]), float(active_checks_row["total"][0]),
                            int(invoice_count_row["n"][0]),
                        )
            with hc4:
                if st.button("Clear", key="clear_batch_selection"):
                    st.session_state["selected_statement_id"] = None
                    refresh()

            with st.expander("Export Batch Statement"):
                export_net, export_active_sum, export_remaining = get_batch_balance(int(selected_sid))
                export_is_overpaid = export_remaining < -0.005
                export_is_covered = export_net > 0 and abs(export_remaining) <= 0.005
                if batch_is_locked:
                    st.caption("This batch is locked. Regenerating the report keeps it locked.")
                elif export_is_overpaid:
                    st.error(f"This batch is overpaid by {php(-export_remaining)} — active checks exceed the net total. This can't be locked until you correct or cancel a check.")
                elif export_is_covered:
                    st.caption("This batch is balanced — generating the report will lock it automatically for collection.")
                else:
                    st.caption(f"This batch is not yet balanced (remaining {php(export_remaining)}) — generating now will NOT lock it.")

                export_full_history = st.toggle("Include full check history (incl. cancelled/replaced)", value=True, key="export_history_toggle")
                export_show_payment_application = st.toggle(
                    "Include payment application & running balance", value=False, key="export_payment_application_toggle",
                    help="Adds an 'Applied To' column (which invoice(s) each check paid, where tracked) and a running balance column to the Checks table.",
                )
                export_copy_choice = st.radio(
                    "Copies to print",
                    options=["both", "filing", "supplier"],
                    format_func=lambda c: {"both": "Both (filing + supplier)", "filing": "Filing copy only", "supplier": "Supplier copy only"}[c],
                    horizontal=True,
                    key="export_copy_choice",
                )
                if st.button("Generate Batch Statement PDF", icon=":material/picture_as_pdf:", key="export_generate_btn"):
                    pdf_bytes = generate_batch_statement_pdf(int(selected_sid), export_full_history, export_copy_choice, export_show_payment_application)
                    if pdf_bytes:
                        if export_is_covered and not batch_is_locked:
                            lock_ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if lock_ok else st.error)(
                                "Batch is balanced — locked automatically after generating the report for collection." if lock_ok else lock_msg
                            )
                        ref = scoped_row["statement_ref"]
                        suffix = {"both": "", "filing": "_filing", "supplier": "_supplier"}[export_copy_choice]
                        pdf_preview_and_download(
                            pdf_bytes, f"batch_statement_{ref}{suffix}.pdf".replace(" ", "_"),
                            key="export_download", label="Batch Statement PDF",
                        )
                    else:
                        st.error("Could not generate the Batch Statement PDF.")

                st.divider()
                st.caption("Thermal printer statement — 80mm roll (3.15\" wide), one continuous strip listing the same invoices and checks, no page breaks.")
                if st.button("Generate Thermal Statement (80mm) PDF", icon=":material/receipt_long:", key="export_thermal_generate_btn"):
                    thermal_pdf_bytes = generate_batch_statement_thermal_pdf(int(selected_sid), export_full_history)
                    if thermal_pdf_bytes:
                        if export_is_covered and not batch_is_locked:
                            lock_ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if lock_ok else st.error)(
                                "Batch is balanced — locked automatically after generating the report for collection." if lock_ok else lock_msg
                            )
                        ref = scoped_row["statement_ref"]
                        pdf_preview_and_download(
                            thermal_pdf_bytes, f"batch_statement_{ref}_thermal.pdf".replace(" ", "_"),
                            key="export_thermal_download", label="Thermal Statement PDF",
                        )
                    else:
                        st.error("Could not generate the thermal statement PDF.")

        tab_invoices_sub, tab_memo_sub, tab_checks_sub = st.tabs(
            ["Invoices", "Credit Memo", "Checks"], key="os_subtab", default="Invoices", on_change="rerun",
        )

        with tab_invoices_sub:
            scoped_invoices_df = q(
                """SELECT invoice_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status
                   FROM invoices
                   WHERE statement_id = ?
                   ORDER BY invoice_id""",
                (int(batch_scope_pick),),
            )
            if batch_is_locked:
                if scoped_invoices_df.empty:
                    st.caption("No invoices linked to this order statement yet.")
                else:
                    st.dataframe(
                        scoped_invoices_df[["invoice_id", "doc_ref", "invoice_date", "due_date", "gross_amount", "amount", "status"]],
                        hide_index=True,
                        column_config={
                            "gross_amount": st.column_config.NumberColumn("Gross amount", format="₱ %.2f"),
                            "amount": st.column_config.NumberColumn("Net amount", format="₱ %.2f"),
                        },
                    )
                st.caption("This batch is cancelled and can't be edited." if batch_is_cancelled else "Batch is locked — unlock it above to add, edit, or delete an invoice.")
            else:
                st.caption(f"Add, edit, or delete rows below, then Save. A new row needs a Doc Ref and a positive Amount; use the row's menu (or select + Delete) to remove one. Due Date is always recomputed as Date Received + this supplier's Terms ({supplier_terms or 'not set'}) when you Save — it's a computed reference field, not manually edited; whatever you type into it is overwritten on Save. The grid itself won't visibly update Due Date while you're still typing, only after Save. Every change is logged automatically (old → new + timestamp).")

                with st.expander("Add invoices from a photo, PDF, or spreadsheet"):
                    bulk_staged_key = batch_scoped_key("bulk_staged")
                    bulk_errors_key = batch_scoped_key("bulk_errors")
                    ocr_raw_text_key = batch_scoped_key("ocr_raw_text")

                    tab_ocr, tab_import = st.tabs(["From images/PDFs (local OCR)", "From spreadsheet (CSV/Excel)"])

                    with tab_ocr:
                        st.caption("Upload photos or PDF scans of this supplier's invoices — one file per invoice. Runs fully offline (Tesseract), no external service or cost. Best-effort read of Doc Ref, Date, and Amount — double-check the results below before adding; nothing is saved until you confirm.")
                        ocr_files = st.file_uploader(
                            "Invoice images or PDFs", type=["png", "jpg", "jpeg", "pdf"],
                            accept_multiple_files=True, key=batch_scoped_key("ocr_uploader"),
                        )
                        if st.button("Extract fields", icon=":material/document_scanner:", key=batch_scoped_key("ocr_extract_btn")):
                            if not ocr_files:
                                st.warning("Upload at least one file first.")
                            else:
                                staged, extract_errors, raw_texts = [], [], {}
                                with st.spinner(f"Extracting {len(ocr_files)} invoice(s)..."):
                                    for f in ocr_files:
                                        fields, message, raw_text = extract_invoice_fields(f.getvalue(), f.name)
                                        if raw_text:
                                            raw_texts[f.name] = raw_text
                                        if fields:
                                            staged.append({
                                                "source_file": f.name,
                                                "doc_ref": fields.doc_ref,
                                                "invoice_date": fields.invoice_date,
                                                "due_date": resolve_due_date(fields.invoice_date, supplier_terms),
                                                "gross_amount": fields.amount,
                                                "less_pct": 0.0,
                                            })
                                            if message:
                                                extract_errors.append(("warning", f"{f.name}: {message}"))
                                        else:
                                            extract_errors.append(("error", f"{f.name}: {message}"))
                                st.session_state[bulk_staged_key] = staged
                                st.session_state[bulk_errors_key] = extract_errors
                                st.session_state[ocr_raw_text_key] = raw_texts

                        ocr_raw_texts = st.session_state.get(ocr_raw_text_key, {})
                        if ocr_raw_texts:
                            with st.expander("Show raw OCR text (for diagnosing a bad read)"):
                                for fname, rtext in ocr_raw_texts.items():
                                    st.caption(fname)
                                    st.text(rtext)

                    with tab_import:
                        st.caption("Upload a .csv or .xlsx with columns like Doc Ref / Date / Amount / Less % (any reasonable header variant works). More reliable than OCR since it's reading real data, not a photo.")
                        st.download_button(
                            "Download CSV template", data=get_template_csv(),
                            file_name=re.sub(r"[^A-Za-z0-9._-]+", "_", f"{scoped_row['creditor_name']}_{scoped_row['statement_ref']}_invoice_template").strip("_") + ".csv",
                            mime="text/csv", icon=":material/download:", key=batch_scoped_key("import_template_btn"),
                        )
                        import_file = st.file_uploader(
                            "Invoice spreadsheet", type=["csv", "xlsx"],
                            accept_multiple_files=False, key=batch_scoped_key("import_uploader"),
                        )
                        if st.button("Import rows", icon=":material/upload_file:", key=batch_scoped_key("import_btn")):
                            if not import_file:
                                st.warning("Upload a file first.")
                            else:
                                imported_rows, import_messages = parse_invoice_spreadsheet(import_file.getvalue(), import_file.name)
                                staged = [{
                                    "source_file": import_file.name,
                                    "doc_ref": r.doc_ref,
                                    "invoice_date": r.invoice_date,
                                    "due_date": resolve_due_date(r.invoice_date, supplier_terms),
                                    "gross_amount": r.amount,
                                    "less_pct": r.less_pct,
                                } for r in imported_rows]
                                st.session_state[bulk_staged_key] = staged
                                st.session_state[bulk_errors_key] = [("warning", m) for m in import_messages]
                                st.session_state[ocr_raw_text_key] = {}

                    for level, e in st.session_state.get(bulk_errors_key, []):
                        (st.warning if level == "warning" else st.error)(e)

                    staged_rows = st.session_state.get(bulk_staged_key, [])
                    if staged_rows:
                        st.caption(f"{len(staged_rows)} invoice(s) staged — review/correct below, then add.")
                        staging_editor_key = batch_scoped_key("bulk_staging_editor")
                        edited_staged_df = st.data_editor(
                            pd.DataFrame(staged_rows),
                            hide_index=True,
                            num_rows="dynamic",
                            disabled=["source_file", "due_date"],
                            key=staging_editor_key,
                            column_config={
                                "source_file": st.column_config.TextColumn("Source"),
                                "doc_ref": st.column_config.TextColumn("Doc Ref"),
                                "invoice_date": st.column_config.DateColumn("Date Received"),
                                "due_date": st.column_config.DateColumn("Due Date (computed, recalculated on save)"),
                                "gross_amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01),
                                "less_pct": st.column_config.NumberColumn("Less %", format="%.2f%%", min_value=0.0, max_value=100.0, step=1.0),
                            },
                        )
                        bulk_promote_after = False
                        if scoped_row["status"] == "Draft":
                            bulk_promote_after = st.checkbox(
                                "Also promote this batch to Issued once the invoices are added", value=True,
                                key=batch_scoped_key("bulk_promote_after"),
                                help="Gives the batch its permanent number (e.g. 2026 - OS 0007) in the same step, so you don't need to open \"Promote statement status\" separately.",
                            )
                        bulk_col_add, bulk_col_discard = st.columns(2)
                        with bulk_col_add:
                            if st.button("Add these invoices to the batch", icon=":material/playlist_add:", key=batch_scoped_key("bulk_commit_btn")) or dup_resume("bulk_add_invoices"):
                                if dup_needs_confirm("bulk_add_invoices", invoice_warnings(scoped_creditor_id, [
                                    (cell_text(r["doc_ref"]).strip(), r["invoice_date"], float(r["gross_amount"] or 0))
                                    for _, r in edited_staged_df.iterrows() if cell_text(r["doc_ref"]).strip() and float(r["gross_amount"] or 0) > 0
                                ])):
                                    pass
                                elif guard_duplicate_submit("bulk_add_invoices", int(batch_scope_pick), edited_staged_df.to_csv(index=False)):
                                    added, commit_errors = 0, []
                                    for _, r in edited_staged_df.iterrows():
                                        doc_ref = cell_text(r["doc_ref"]).strip()
                                        gross = float(r["gross_amount"] or 0)
                                        if not doc_ref or gross <= 0:
                                            commit_errors.append(f"Skipped a row missing Doc Ref or a positive Amount ({r.get('source_file', '?')}).")
                                            continue
                                        resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                                        try:
                                            add_invoice(
                                                scoped_creditor_id, doc_ref, r["invoice_date"], resolved_due,
                                                gross, [float(r["less_pct"])] if r["less_pct"] else [], int(batch_scope_pick),
                                            )
                                            added += 1
                                        except sqlite3.IntegrityError:
                                            commit_errors.append(f"'{doc_ref}' already exists for this supplier.")
                                    if added:
                                        st.success(f"{added} invoice(s) added.")
                                        if bulk_promote_after and not commit_errors:
                                            try:
                                                st.toast(f"{added} invoice(s) added; batch promoted to Issued as {promote_draft_statement(int(batch_scope_pick), 'Issued')}.")
                                            except ValueError as e:
                                                commit_errors.append(f"Invoices were added, but the batch could not be promoted: {e}")
                                    for e in commit_errors:
                                        st.error(e)
                                    st.session_state[bulk_staged_key] = []
                                    st.session_state[bulk_errors_key] = []
                                    if added:
                                        refresh()
                                else:
                                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                        with bulk_col_discard:
                            if st.button("Discard staged rows", icon=":material/delete_sweep:", key=batch_scoped_key("bulk_discard_btn")):
                                st.session_state[bulk_staged_key] = []
                                st.session_state[bulk_errors_key] = []
                                st.rerun()

                original_invoices_df = scoped_invoices_df.copy()
                original_invoices_df["less_pct"] = original_invoices_df["trade_discounts"].apply(parse_less_pct)
                editable_display_df = original_invoices_df[
                    ["invoice_id", "doc_ref", "invoice_date", "due_date", "gross_amount", "less_pct", "amount", "status"]
                ].copy()
                editable_display_df["invoice_date"] = pd.to_datetime(editable_display_df["invoice_date"]).dt.date
                editable_display_df["due_date"] = pd.to_datetime(editable_display_df["due_date"]).dt.date
                invoices_editor_key = batch_scoped_key("edit_invoices_editor")
                edited_invoices_df = st.data_editor(
                    editable_display_df,
                    hide_index=True,
                    num_rows="dynamic",
                    disabled=["invoice_id", "due_date", "amount", "status"],
                    key=invoices_editor_key,
                    column_config={
                        "invoice_id": st.column_config.NumberColumn("ID"),
                        "doc_ref": st.column_config.TextColumn("Doc Ref", default=""),
                        "invoice_date": st.column_config.DateColumn("Date Received", default=date.today()),
                        "due_date": st.column_config.DateColumn(
                            "Due Date (computed, recalculated on save)", default=resolve_due_date(date.today(), supplier_terms)
                        ),
                        "gross_amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01, default=0.0),
                        "less_pct": st.column_config.NumberColumn("Less %", format="%.2f%%", min_value=0.0, max_value=100.0, step=1.0, default=0.0),
                        "amount": st.column_config.NumberColumn("Net Amount (recalculated on save)", format="₱ %.2f"),
                        "status": st.column_config.TextColumn("Status"),
                    },
                )
                if st.button("Save invoice changes", icon=":material/save:", key=batch_scoped_key("save_invoice_edits")) or dup_resume("save_invoice_changes"):
                  if dup_needs_confirm("save_invoice_changes", invoice_warnings(scoped_creditor_id, [
                      (cell_text(r["doc_ref"]).strip(), r["invoice_date"], float(r["gross_amount"] or 0))
                      for _, r in edited_invoices_df.iterrows()
                      if pd.isna(r["invoice_id"]) and cell_text(r["doc_ref"]).strip() and float(r["gross_amount"] or 0) > 0
                  ])):
                    pass
                  elif not guard_duplicate_submit("save_invoice_changes", int(batch_scope_pick), edited_invoices_df.to_csv(index=False)):
                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                  else:
                    original_by_id = original_invoices_df.set_index("invoice_id")
                    seen_ids, added, updated, row_errors = set(), 0, 0, []
                    for _, r in edited_invoices_df.iterrows():
                        if pd.isna(r["invoice_id"]):
                            doc_ref, gross = cell_text(r["doc_ref"]).strip(), float(r["gross_amount"] or 0)
                            if not doc_ref and gross <= 0:
                                continue  # untouched blank template row
                            if not doc_ref or gross <= 0:
                                row_errors.append("New invoice row needs a Doc Ref and a positive Amount.")
                                continue
                            resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                            try:
                                add_invoice(
                                    scoped_creditor_id, doc_ref, r["invoice_date"], resolved_due,
                                    gross, [float(r["less_pct"])] if r["less_pct"] else [], int(batch_scope_pick),
                                )
                                added += 1
                            except sqlite3.IntegrityError:
                                row_errors.append(f"'{doc_ref}' already exists for this supplier.")
                            continue

                        inv_id = int(r["invoice_id"])
                        seen_ids.add(inv_id)
                        orig = original_by_id.loc[inv_id]
                        resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                        is_changed = (
                            cell_text(r["doc_ref"]) != cell_text(orig["doc_ref"])
                            or str(r["invoice_date"]) != str(orig["invoice_date"])
                            or str(resolved_due) != str(orig["due_date"])
                            or abs(float(r["gross_amount"]) - float(orig["gross_amount"])) > 0.001
                            or abs(float(r["less_pct"]) - float(orig["less_pct"])) > 0.001
                        )
                        if not is_changed:
                            continue
                        ok, msg = edit_invoice(
                            inv_id, cell_text(r["doc_ref"]), r["invoice_date"], resolved_due,
                            float(r["gross_amount"]), float(r["less_pct"]),
                        )
                        if ok:
                            updated += 1
                        else:
                            row_errors.append(f"Invoice {inv_id}: {msg}")

                    deleted = 0
                    for removed_id in set(original_by_id.index) - seen_ids:
                        ok, msg = delete_invoice(int(removed_id))
                        if ok:
                            deleted += 1
                        else:
                            row_errors.append(msg)

                    summary = [f"{n} {label}" for n, label in [(added, "added"), (updated, "updated"), (deleted, "deleted")] if n]
                    if summary:
                        st.success("Invoices: " + ", ".join(summary) + ".")
                    for e in row_errors:
                        st.error(e)
                    if not summary and not row_errors:
                        st.info("No changes to save.")
                    if summary:
                        refresh()

        with tab_memo_sub:
            st.caption("Applied before checks are issued — reduces the batch's net payable.")
            scoped_memos_df = q(
                """SELECT credit_memo_id, memo_ref, memo_date, amount, reason
                   FROM credit_memos WHERE statement_id = ? ORDER BY credit_memo_id""",
                (int(batch_scope_pick),),
            )
            if batch_is_locked:
                if scoped_memos_df.empty:
                    st.caption("No credit memos applied to this order statement yet.")
                else:
                    st.dataframe(
                        scoped_memos_df,
                        hide_index=True,
                        column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")},
                    )
                st.caption("This batch is cancelled and can't be edited." if batch_is_cancelled else "Batch is locked — unlock it above to add, edit, or delete a credit memo.")
            else:
                st.caption("Add, edit, or delete rows below, then Save. A new row needs a reference and a positive Amount.")
                original_memos_df = scoped_memos_df.copy()
                original_memos_df["memo_date"] = pd.to_datetime(original_memos_df["memo_date"]).dt.date
                edited_memos_df = st.data_editor(
                    original_memos_df,
                    hide_index=True,
                    num_rows="dynamic",
                    disabled=["credit_memo_id"],
                    key=batch_scoped_key("edit_memos_editor"),
                    column_config={
                        "credit_memo_id": st.column_config.NumberColumn("ID"),
                        "memo_ref": st.column_config.TextColumn("Reference", default=""),
                        "memo_date": st.column_config.DateColumn("Memo Date", default=date.today()),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01, default=0.0),
                        "reason": st.column_config.TextColumn("Reason", default=""),
                    },
                )
                if st.button("Save credit memo changes", icon=":material/save:", key=batch_scoped_key("save_memo_edits")) or dup_resume("save_memo_changes"):
                  if dup_needs_confirm("save_memo_changes", [
                      w for _, r in edited_memos_df.iterrows()
                      if pd.isna(r["credit_memo_id"]) and cell_text(r["memo_ref"]).strip() and float(r["amount"] or 0) > 0
                      for w in credit_memo_warnings(scoped_creditor_id, cell_text(r["memo_ref"]), r["memo_date"], float(r["amount"]))
                  ]):
                    pass
                  elif not guard_duplicate_submit("save_memo_changes", int(batch_scope_pick), edited_memos_df.to_csv(index=False)):
                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                  else:
                    original_by_id = original_memos_df.set_index("credit_memo_id")
                    seen_ids, added, updated, row_errors = set(), 0, 0, []
                    for _, r in edited_memos_df.iterrows():
                        if pd.isna(r["credit_memo_id"]):
                            memo_ref, amount = cell_text(r["memo_ref"]).strip(), float(r["amount"] or 0)
                            if not memo_ref and amount <= 0:
                                continue  # untouched blank template row
                            if not memo_ref or amount <= 0:
                                row_errors.append("New credit memo row needs a reference and a positive Amount.")
                                continue
                            ok, msg = add_credit_memo(int(batch_scope_pick), memo_ref, r["memo_date"], amount, cell_text(r["reason"]))
                            if ok:
                                added += 1
                            else:
                                row_errors.append(msg)
                            continue

                        memo_id = int(r["credit_memo_id"])
                        seen_ids.add(memo_id)
                        orig = original_by_id.loc[memo_id]
                        is_changed = (
                            cell_text(r["memo_ref"]) != cell_text(orig["memo_ref"])
                            or str(r["memo_date"]) != str(orig["memo_date"])
                            or abs(float(r["amount"]) - float(orig["amount"])) > 0.001
                            or cell_text(r["reason"]) != cell_text(orig["reason"])
                        )
                        if not is_changed:
                            continue
                        ok, msg = edit_credit_memo(memo_id, cell_text(r["memo_ref"]), r["memo_date"], float(r["amount"]), cell_text(r["reason"]))
                        if ok:
                            updated += 1
                        else:
                            row_errors.append(f"Credit memo {memo_id}: {msg}")

                    deleted = 0
                    for removed_id in set(original_by_id.index) - seen_ids:
                        ok, msg = delete_credit_memo(int(removed_id))
                        if ok:
                            deleted += 1
                        else:
                            row_errors.append(msg)

                    summary = [f"{n} {label}" for n, label in [(added, "added"), (updated, "updated"), (deleted, "deleted")] if n]
                    if summary:
                        st.success("Credit memos: " + ", ".join(summary) + ".")
                    for e in row_errors:
                        st.error(e)
                    if not summary and not row_errors:
                        st.info("No changes to save.")
                    if summary:
                        refresh()

        with tab_checks_sub:
            st.markdown("##### Issue a check")
            _park_flash = st.session_state.pop("_park_flash", None)
            if _park_flash:
                st.success(_park_flash)
            net_total, active_checks_sum, remaining_balance = get_batch_balance(int(batch_scope_pick))
            is_balanced = abs(remaining_balance) <= 0.005
            is_overpaid = remaining_balance < -0.005

            if net_total <= 0:
                st.info("No payable total yet — add invoices (and any credit memos) before issuing checks.")
            elif is_overpaid:
                st.warning(f"Overpaid — active checks ({php(active_checks_sum)}) exceed the net total ({php(net_total)}) by {php(-remaining_balance)}. Correct or replace a check to restore balance.")
            elif is_balanced:
                st.success(f"Balanced — net total {php(net_total)} fully covered by {php(active_checks_sum)} in active checks.")
            else:
                st.info(f"Net total {php(net_total)} · Active checks so far {php(active_checks_sum)} · Remaining balance {php(remaining_balance)}")

            is_payable = not batch_is_locked and scoped_row["status"] != "Cancelled" and remaining_balance >= 0.01
            if not is_payable:
                reasons = []
                if scoped_row["status"] == "Cancelled":
                    reasons.append("batch is cancelled")
                elif batch_is_locked:
                    reasons.append("batch is locked")
                if remaining_balance < 0.01:
                    reasons.append("no remaining balance to pay" if net_total > 0 else "batch has no payable total yet")
                st.caption("Cannot issue a new check here: " + "; ".join(reasons) + ".")
            else:
                st.caption(f"Remaining balance: {php(remaining_balance)} — split it into as many checks as you like; amounts are pre-calculated evenly and fully editable. No check number yet — that's assigned when the check is printed.")

                latest_check_for_supplier = get_latest_check_for_creditor(scoped_creditor_id)
                if latest_check_for_supplier:
                    latest_date, latest_amount = latest_check_for_supplier
                    st.caption(f"Last check issued to this supplier: {latest_date.strftime('%b %d, %Y')} — {php(latest_amount)}.")

                num_checks_key = batch_scoped_key("issue_checks_count")
                grid_key = batch_scoped_key("issue_checks_editor")
                last_signature_key = batch_scoped_key("issue_checks_last_signature")

                open_invoices_df = get_invoices_with_remaining(int(batch_scope_pick))
                # Guardrail (2026-09-27): checks typed into this grid but never parked used to vanish when you opened another
                # order statement. The grid is now remembered per batch for the session and put back when you return.
                park_bases = st.session_state.setdefault("_park_bases", {})
                _sid = int(batch_scope_pick)
                _draft = load_park_draft(_sid)
                _split_key = batch_scoped_key("issue_split_mode")
                _restoring = False
                if _draft and _split_key not in st.session_state:          # widgets were dropped while another batch was open
                    if abs(_draft["balance"] - round(remaining_balance, 2)) <= 0.005 and _draft["invoice_ids"] == tuple(open_invoices_df["invoice_id"]):
                        st.session_state[_split_key] = _draft["mode"]
                        st.session_state[num_checks_key] = _draft["count"]
                        _restoring = True
                    else:
                        clear_park_draft(_sid)
                        park_bases.pop(_sid, None)
                        _draft = None
                        st.info("The checks you had typed for this batch were cleared because its invoices or balance changed in the meantime.")
                split_mode = st.radio(
                    "How to split", ["By number of checks", "One check per invoice"], horizontal=True, key=batch_scoped_key("issue_split_mode"),
                    help="Some suppliers want one check for each invoice. 'One check per invoice' makes a row for every invoice still to be paid, dated on that invoice's due date.",
                )
                per_invoice = split_mode == "One check per invoice" and not open_invoices_df.empty
                if per_invoice:
                    num_checks = len(open_invoices_df)
                    st.caption(f"{num_checks} invoice(s) still to pay — one check each, dated on the invoice's due date and for its remaining amount. Every date and amount stays editable below.")
                else:
                    if split_mode == "One check per invoice":
                        st.caption("No open invoices to make checks for.")
                    num_checks = st.number_input(
                        "Number of checks", min_value=1, max_value=50, step=1,
                        value=int(st.session_state.get(num_checks_key, 1)), key=num_checks_key,
                    )
                    st.caption("Two ways to issue: use the count above to pre-split evenly (whole pesos, editable), or just add/remove rows in the table below and type amounts yourself.")

                # Re-seed the grid whenever the count OR the remaining balance changes (e.g. a check
                # in this batch got cancelled/corrected) — otherwise a stale pre-split from before
                # that change would silently linger in session_state.
                # Default check date = the batch's invoice due date (the earliest one still unpaid if there are several),
                # so a check is dated when it is actually due; every date stays editable in the grid.
                due_val = q_live("SELECT MIN(due_date) AS d FROM invoices WHERE statement_id = ? AND status IN ('Unpaid', 'Partially Paid')", (int(batch_scope_pick),))["d"][0]
                if not has_value(due_val):
                    due_val = q_live("SELECT MIN(due_date) AS d FROM invoices WHERE statement_id = ? AND status != 'Cancelled'", (int(batch_scope_pick),))["d"][0]
                default_check_date = date.fromisoformat(str(due_val)[:10]) if has_value(due_val) else date.today()
                signature = (int(num_checks), round(remaining_balance, 2), default_check_date, per_invoice,
                             tuple(open_invoices_df["invoice_id"]) if per_invoice else ())
                if st.session_state.get(last_signature_key) != signature:
                    st.session_state[last_signature_key] = signature
                    st.session_state.pop(grid_key, None)
                    if not _restoring:
                        park_bases.pop(_sid, None)

                if per_invoice:
                    default_checks_df = pd.DataFrame({
                        "check_date": [date.fromisoformat(str(d)[:10]) for d in open_invoices_df["due_date"]],
                        "amount": [round(float(x), 2) for x in open_invoices_df["remaining"]],
                    })
                else:
                    default_checks_df = pd.DataFrame({
                        "check_date": [default_check_date] * int(num_checks),
                        "amount": split_remaining_evenly(remaining_balance, int(num_checks)),
                    })
                computed_default_df = default_checks_df
                if st.session_state.get(last_signature_key) is not None and _restoring:
                    park_bases[_sid] = pd.DataFrame({"check_date": [date.fromisoformat(d) for d, _ in _draft["rows"]], "amount": [x for _, x in _draft["rows"]]})
                elif grid_key not in st.session_state:
                    park_bases.pop(_sid, None)
                default_checks_df = park_bases.get(_sid, computed_default_df)
                edited_checks_df = st.data_editor(
                    default_checks_df,
                    hide_index=True,
                    num_rows="dynamic",
                    key=grid_key,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date", default=default_check_date),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01, default=0.0),
                    },
                )

                _typed = [(str(r["check_date"])[:10], round(float(r["amount"]), 2)) for _, r in edited_checks_df.iterrows() if pd.notna(r["amount"]) and float(r["amount"]) > 0]
                _computed = [(str(r["check_date"])[:10], round(float(r["amount"]), 2)) for _, r in computed_default_df.iterrows()]
                grid_is_edited = bool(_typed) and _typed != _computed
                if grid_is_edited:
                    _now_draft = {"mode": split_mode, "count": int(num_checks), "balance": round(remaining_balance, 2),
                                  "invoice_ids": tuple(int(i) for i in open_invoices_df["invoice_id"]), "rows": _typed}
                    if not _draft or any(_draft.get(k) != v for k, v in _now_draft.items()):
                        save_park_draft(_sid, split_mode, int(num_checks), remaining_balance, list(open_invoices_df["invoice_id"]), _typed)
                    st.warning(":material/edit_note: These checks are typed in but **not parked yet** — click **Park Metrobank checks** below to save them. "
                               "They are remembered — even if you open another order statement or refresh the page — until you park them.")
                elif _draft:
                    clear_park_draft(_sid)
                grid_total = float(edited_checks_df["amount"].sum())
                grid_diff = remaining_balance - grid_total
                grid_matches = abs(grid_diff) <= 0.005
                if grid_matches:
                    st.success(f"Grid total {php(grid_total)} matches the remaining balance.")
                else:
                    st.warning(f"Grid total {php(grid_total)} vs remaining balance {php(remaining_balance)} — off by {php(grid_diff)}. Adjust amounts to match exactly before issuing.")

                # num_rows="dynamic" always leaves one blank "add a row" affordance in the
                # returned data — drop it rather than reject it as "needs an amount".
                rows = [r for r in edited_checks_df.to_dict("records") if float(r.get("amount") or 0) > 0]

                # Same-day-duplicate warning (2026-09-17, per user request): this business
                # deliberately avoids ever writing two checks to the same supplier on the same
                # day, postdating past the due date instead specifically to prevent it. A
                # warning, not a block -- st.warning() doesn't disable the submit button below,
                # since there can be a genuine reason to do it anyway and the owner asked to keep
                # that override available.
                existing_check_dates = get_existing_check_dates_for_creditor(scoped_creditor_id)
                grid_dates = [r["check_date"] for r in rows]
                same_day_conflicts = sorted({
                    d for d in grid_dates
                    if d in existing_check_dates or grid_dates.count(d) > 1
                })
                if same_day_conflicts:
                    conflict_list = ", ".join(d.strftime("%b %d, %Y") for d in same_day_conflicts)
                    st.warning(
                        f":material/warning: This supplier already has (or would have) more than one check dated {conflict_list} — "
                        "double-check before parking. Still allowed if that's genuinely intended."
                    )

                alloc_ready = False
                alloc_grid_key = batch_scoped_key("issue_checks_alloc_editor")
                edited_alloc_df = pd.DataFrame(columns=["check_row", "check_date", "invoice_id", "doc_ref", "allocated"])

                if grid_matches and rows:
                    st.markdown("##### Allocate to invoices")
                    if open_invoices_df.empty:
                        st.error("No open invoices left to allocate these checks to — every invoice in this batch is already fully allocated or Cancelled. Add more invoices, or reduce the check amounts.")
                    else:
                        st.caption("Every check must be tied to the specific invoice(s) it pays — auto-filled oldest due date first below; adjust the amounts if a check should be split differently.")
                        alloc_signature_key = batch_scoped_key("issue_checks_alloc_signature")
                        alloc_signature = (repr(rows), repr(open_invoices_df["invoice_id"].tolist()), repr(open_invoices_df["remaining"].tolist()))
                        if st.session_state.get(alloc_signature_key) != alloc_signature:
                            st.session_state[alloc_signature_key] = alloc_signature
                            st.session_state.pop(alloc_grid_key, None)

                        preview_df = waterfall_allocate_preview(rows, open_invoices_df)
                        if preview_df.empty:
                            st.error("Couldn't auto-allocate — check the amounts above.")
                        else:
                            edited_alloc_df = st.data_editor(
                                preview_df[["check_row", "check_date", "doc_ref", "allocated"]],
                                hide_index=True,
                                disabled=["check_row", "check_date", "doc_ref"],
                                key=alloc_grid_key,
                                column_config={
                                    "check_row": st.column_config.NumberColumn("Check #"),
                                    "check_date": st.column_config.DateColumn("Check Date"),
                                    "doc_ref": st.column_config.TextColumn("Invoice"),
                                    "allocated": st.column_config.NumberColumn("Allocated", format="₱ %.2f", min_value=0.01, step=0.01),
                                },
                            )
                            edited_alloc_df = edited_alloc_df.copy()
                            edited_alloc_df["invoice_id"] = preview_df["invoice_id"].values

                            alloc_ok = True
                            per_check_totals = edited_alloc_df.groupby("check_row")["allocated"].sum()
                            for idx, row in enumerate(rows, start=1):
                                allocated_for_row = float(per_check_totals.get(idx, 0.0))
                                if abs(allocated_for_row - float(row["amount"])) > 0.01:
                                    alloc_ok = False
                                    st.warning(f"Check #{idx} ({php(row['amount'])}) has {php(allocated_for_row)} allocated — must match exactly.")

                            per_invoice_totals = edited_alloc_df.groupby("invoice_id")["allocated"].sum()
                            for _, inv in open_invoices_df.iterrows():
                                allocated_for_inv = float(per_invoice_totals.get(inv["invoice_id"], 0.0))
                                if allocated_for_inv > float(inv["remaining"]) + 0.01:
                                    alloc_ok = False
                                    st.warning(f"Invoice {inv['doc_ref']} only has {php(inv['remaining'])} remaining — {php(allocated_for_inv)} allocated.")

                            alloc_ready = alloc_ok

                memo_status = get_batch_memo_status(int(batch_scope_pick))
                memo_guard_blocking = memo_status["unapplied_count"] > 0 and not memo_status["confirmed"]
                if memo_guard_blocking:
                    st.warning(
                        f"This batch has {memo_status['unapplied_count']} credit memo(s) totaling {php(memo_status['total_amount'])} "
                        f"reducing the net total to {php(net_total)} — confirm you've reviewed them, or remove one, before issuing checks."
                    )
                    mc1, mc2 = st.columns(2)
                    with mc1:
                        if st.button("✓ Confirm", key=batch_scoped_key("confirm_memos_btn")):
                            confirm_credit_memos(int(batch_scope_pick))
                            refresh()
                    with mc2:
                        remove_toggle_key = batch_scoped_key("show_remove_memo_form")
                        if st.button("Remove memo", key=batch_scoped_key("remove_memo_toggle_btn")):
                            st.session_state[remove_toggle_key] = not st.session_state.get(remove_toggle_key, False)
                    if st.session_state.get(batch_scoped_key("show_remove_memo_form")):
                        batch_memos_df = q("SELECT credit_memo_id, memo_ref, amount FROM credit_memos WHERE statement_id = ?", (int(batch_scope_pick),))
                        if batch_memos_df.empty:
                            st.caption("No credit memos left on this batch.")
                        else:
                            memo_options = {
                                int(r["credit_memo_id"]): f"{r['memo_ref']} — {php(r['amount'])}"
                                for _, r in batch_memos_df.iterrows()
                            }
                            memo_pick = st.selectbox(
                                "Which memo to remove?", options=list(memo_options), format_func=lambda mid: memo_options[mid],
                                key=batch_scoped_key("remove_memo_pick"),
                            )
                            if st.button("Confirm removal", key=batch_scoped_key("remove_memo_confirm_btn")):
                                ok, msg = delete_credit_memo(int(memo_pick))
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.session_state.pop(batch_scoped_key("show_remove_memo_form"), None)
                                    refresh()

                if st.button(
                    "Park Metrobank checks", icon=":material/payments:",
                    key=batch_scoped_key("issue_checks_submit"), disabled=not grid_matches or not alloc_ready or memo_guard_blocking,
                ) or dup_resume("issue_checks_grid"):
                    if dup_needs_confirm("issue_checks_grid", check_warnings(scoped_creditor_id, [(r["amount"], r["check_date"]) for r in rows])):
                        pass
                    else:
                        # Confirmation first: shows which invoice each check pays and for how much, then parks the checks, ties them to
                        # their invoices and -- if the batch is still a Draft -- promotes it to Issued (2026-09-26, per user).
                        _park_lines = []
                        for _row_no, _row in enumerate(rows, start=1):
                            _parts = edited_alloc_df[edited_alloc_df["check_row"] == _row_no]
                            _bits = []
                            _full = len(_parts) == 1
                            for _, _p in _parts.iterrows():
                                _inv = open_invoices_df[open_invoices_df["invoice_id"] == _p["invoice_id"]].iloc[0]
                                _left = round(float(_inv["remaining"]) - float(_p["allocated"]), 2)
                                _full = _full and abs(_left) <= 0.005
                                _bits.append(f"invoice **{_inv['doc_ref']}** {php(_p['allocated'])}" + ("" if abs(_left) <= 0.005 else f" (of {php(_inv['remaining'])}, {php(_left)} still unpaid)"))
                            _mark = "✓ matches the invoice" if _full else "⚠ check the split"
                            _park_lines.append(f"- {_row['check_date']} · **{php(_row['amount'])}** → " + " + ".join(_bits) + f" — {_mark}")
                        _is_draft = str(scoped_row["status"]) == "Draft"
                        _park_summary = (
                            f"Park **{len(rows)} check(s)** totaling **{php(sum(float(r['amount']) for r in rows))}** for this batch?" + chr(10) + chr(10)
                            + chr(10).join(_park_lines) + chr(10) + chr(10)
                            + ("This batch is still a **Draft**: parking promotes it to **Issued** and gives it its permanent OS number." if _is_draft
                               else "The batch is already Issued.")
                        )

                        def _do_park(sid=int(batch_scope_pick), cid=scoped_creditor_id, park_rows=list(rows), alloc_df=edited_alloc_df.copy(), draft=_is_draft):
                            ok, msg, check_ids = issue_checks_grid(cid, sid, park_rows)
                            if not ok:
                                return False, msg
                            alloc_by_row: dict[int, list[tuple[int, float]]] = {i + 1: [] for i in range(len(park_rows))}
                            for _, arow in alloc_df.iterrows():
                                alloc_by_row[int(arow["check_row"])].append((int(arow["invoice_id"]), float(arow["allocated"])))
                            problems = []
                            for row_num, check_id in enumerate(check_ids, start=1):
                                a_ok, a_msg = allocate_check_to_invoices(check_id, alloc_by_row.get(row_num, []))
                                if not a_ok:
                                    problems.append(a_msg)
                            sync_invoice_payment_status(sid)
                            promoted = ""
                            if draft:
                                try:
                                    promoted = f" The batch is now Issued as {promote_draft_statement(sid, 'Issued')}."
                                except ValueError as e:
                                    problems.append(f"the batch could not be promoted ({e})")
                            st.session_state.pop(grid_key, None)
                            st.session_state.pop(alloc_grid_key, None)
                            clear_park_draft(sid)
                            st.session_state.get("_park_bases", {}).pop(sid, None)
                            st.session_state["_park_flash"] = msg + promoted + (" Problems: " + "; ".join(problems) if problems else "")
                            return True, msg

                        confirm_create_dialog(_park_summary, batch_scoped_key("issue_checks_confirm"), _do_park)

            st.markdown("##### Checks already issued against this order statement")
            scoped_checks_df = q(
                "SELECT check_id, check_number, check_date, amount, status, datetime(printed_at, 'localtime') AS printed_at FROM checks_issued WHERE statement_id = ? ORDER BY check_id",
                (int(batch_scope_pick),),
            )
            if scoped_checks_df.empty:
                st.caption("No checks issued against this order statement yet.")
            else:
                display_scoped_checks = scoped_checks_df.copy()
                display_scoped_checks["check_number"] = display_scoped_checks["check_number"].fillna("Pending (not yet printed)")
                st.dataframe(
                    display_scoped_checks,
                    hide_index=True,
                    column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")},
                )

            active_checks_df = scoped_checks_df[scoped_checks_df["status"] == "Active"].copy() if not scoped_checks_df.empty else scoped_checks_df
            parked_active_df = active_checks_df[~active_checks_df["check_number"].apply(has_value)] if not active_checks_df.empty else active_checks_df
            numbered_active_df = active_checks_df[active_checks_df["check_number"].apply(has_value)] if not active_checks_df.empty else active_checks_df

            if PRINT_CHECKS_IN_APP:
                render_check_print_section(scoped_checks_df, scoped_row["creditor_name"], key_prefix=batch_scoped_key("os_check_print"))
            render_assign_number_section(scoped_checks_df, key_prefix=batch_scoped_key("os_check_assign"))

            st.markdown("##### Edit parked checks")
            if parked_active_df.empty:
                st.caption("No check without a number in this batch to edit — once a check has its number it is final (use Correct an active check).")
            else:
                st.caption("Date and amount stay freely editable until a check has its check number — no unlock needed, even if it was already exported to Chrysanth (change it there too). Every change is logged automatically (old → new + timestamp).")
                original_parked_df = parked_active_df[["check_id", "check_date", "amount"]].copy()
                original_parked_df["check_date"] = pd.to_datetime(original_parked_df["check_date"]).dt.date
                edited_parked_df = st.data_editor(
                    original_parked_df,
                    hide_index=True,
                    num_rows="fixed",
                    disabled=["check_id"],
                    key=batch_scoped_key("edit_parked_editor"),
                    column_config={
                        "check_id": st.column_config.NumberColumn("ID"),
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                    },
                )
                if st.button("Save parked check edits", icon=":material/save:", key=batch_scoped_key("save_parked_edits")):
                    original_by_id = original_parked_df.set_index("check_id")
                    updated, row_errors = 0, []
                    for _, r in edited_parked_df.iterrows():
                        chk_id = int(r["check_id"])
                        orig = original_by_id.loc[chk_id]
                        if str(r["check_date"]) != str(orig["check_date"]) or abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                            ok, msg = edit_parked_check(chk_id, float(r["amount"]), r["check_date"])
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                    if updated:
                        st.success(f"{updated} parked check(s) updated.")
                    for e in row_errors:
                        st.error(e)
                    if not updated and not row_errors:
                        st.info("No changes to save.")
                    if updated:
                        refresh()

            st.markdown("##### Un-park checks")
            unparkable_df = parked_active_df[~parked_active_df["check_number"].apply(has_value)] if not parked_active_df.empty else parked_active_df
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to un-park checks.")
            elif unparkable_df.empty:
                st.caption("No check to un-park — only checks that have no number yet can be taken back.")
            else:
                st.caption("Took the wrong split, or parked by mistake? Pick parked checks to take back — they are deleted completely (with their invoice links), "
                           "the balance returns and you can park again. A check that already has its number is final and never touched (cancel or replace it instead). "
                           "If one was already exported to Chrysanth, remove it there too.")
                unpark_labels = {int(r["check_id"]): f"{r['check_date']} — {php(r['amount'])}" for _, r in unparkable_df.iterrows()}
                unpark_pick = st.multiselect("Parked checks to take back", options=list(unpark_labels), format_func=lambda cid: unpark_labels[cid],
                                             key=batch_scoped_key("unpark_pick"))
                if st.button("Un-park selected checks", icon=":material/undo:", disabled=not unpark_pick, key=batch_scoped_key("unpark_btn")):
                    def _do_unpark(sid=int(batch_scope_pick), picked=list(unpark_pick)):
                        ok, msg = unpark_checks(sid, picked)
                        if ok:
                            st.session_state["_park_flash"] = msg
                            st.session_state.pop(batch_scoped_key("unpark_pick"), None)
                        return ok, msg
                    confirm_create_dialog(
                        f"Delete **{len(unpark_pick)} parked check(s)** totaling **{php(sum(float(unparkable_df[unparkable_df['check_id'] == c]['amount'].iloc[0]) for c in unpark_pick))}**? "
                        "They are removed completely, their invoice links are released and the balance is available to park again."
                        + (" Some were already exported to Chrysanth — remove them there too." if unparkable_df[unparkable_df["check_id"].isin(unpark_pick) & unparkable_df["printed_at"].apply(has_value)].shape[0] else ""),
                        batch_scoped_key("unpark_confirm"), _do_unpark)

            st.markdown("##### Correct an active check")
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to correct a check.")
            elif numbered_active_df.empty:
                st.caption("No numbered active check in this batch to correct (assign a number first — above — or edit an unprinted check via 'Edit parked checks').")
            else:
                st.caption("Edit Check Number, Check Date, or Amount directly, then Save. Every change is logged automatically (old → new + timestamp). Amount/Date can only be corrected once a check has been printed — otherwise edit it via 'Edit parked checks' above.")
                original_active_df = numbered_active_df[["check_id", "check_number", "check_date", "amount"]].copy()
                original_active_df["check_date"] = pd.to_datetime(original_active_df["check_date"]).dt.date
                edited_active_df = st.data_editor(
                    original_active_df,
                    hide_index=True,
                    num_rows="fixed",
                    disabled=["check_id"],
                    key=batch_scoped_key("correct_checks_editor"),
                    column_config={
                        "check_id": st.column_config.NumberColumn("ID"),
                        "check_number": st.column_config.TextColumn("Check Number"),
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                    },
                )
                if st.button("Save check corrections", icon=":material/save:", key=batch_scoped_key("save_check_corrections")):
                    original_by_id = original_active_df.set_index("check_id")
                    updated, row_errors = 0, []
                    for _, r in edited_active_df.iterrows():
                        chk_id = int(r["check_id"])
                        orig = original_by_id.loc[chk_id]
                        new_number = cell_text(r["check_number"]).strip()
                        if new_number != str(orig["check_number"]).strip():
                            ok, msg = apply_check_correction(chk_id, "Check Number", new_number, "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                        if str(r["check_date"]) != str(orig["check_date"]):
                            ok, msg = apply_check_correction(chk_id, "Check Date", str(r["check_date"]), "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                        if abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                            ok, msg = apply_check_correction(chk_id, "Amount", str(float(r["amount"])), "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                    if updated:
                        st.success(f"{updated} correction(s) saved.")
                    for e in row_errors:
                        st.error(e)
                    if not updated and not row_errors:
                        st.info("No changes to save.")
                    if updated:
                        refresh()

                with st.expander("Add a note to a check"):
                    note_pick = st.selectbox(
                        "Check",
                        options=active_checks_df["check_id"],
                        format_func=lambda chid: active_checks_df.set_index("check_id").loc[chid, "check_number"] or "Pending (not yet printed)",
                        key=batch_scoped_key("note_pick"),
                    )
                    note_text = st.text_input("Note", key=batch_scoped_key("note_text"))
                    if st.button("Add note", icon=":material/sticky_note_2:", key=batch_scoped_key("add_note_btn")):
                        if note_text.strip():
                            ok, msg = apply_check_correction(int(note_pick), "Payee Note", note_text.strip(), "")
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()
                        else:
                            st.error("Note can't be blank.")

            st.markdown("##### Void a check")
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to void a check.")
            elif active_checks_df.empty:
                st.caption("No active check in this batch to void.")
            else:
                st.caption("Void just cancels it (money returns to the available balance); tick 'Reissue' to also cut a replacement (parked, no number yet) in one step.")
                void_pick = st.selectbox(
                    "Check",
                    options=active_checks_df["check_id"],
                    format_func=lambda chid: f"{active_checks_df.set_index('check_id').loc[chid, 'check_number'] or 'Pending'} ({php(active_checks_df.set_index('check_id').loc[chid, 'amount'])})",
                    key=batch_scoped_key("void_pick"),
                )
                void_reissue = st.checkbox("Reissue as a new (parked) check", key=batch_scoped_key("void_reissue_toggle"))
                if void_reissue:
                    void_new_date = st.date_input("New check date", value=date.today(), key=batch_scoped_key("void_new_date"))
                void_reason = st.text_input("Reason (optional)", placeholder="e.g. postdated past new due date", key=batch_scoped_key("void_reason"))
                if st.button("Void & reissue" if void_reissue else "Void check", icon=":material/cancel:", key=batch_scoped_key("void_submit_btn")):
                    if void_reissue:
                        ok, msg, new_check_id = replace_check(int(void_pick), void_new_date, void_reason)
                    else:
                        ok, msg = cancel_check(int(void_pick))
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()

    with st.expander("Vendor SOA matcher"):
        soa_creditor = selectbox_creditor("Supplier", "SUPPLIER", "soa_creditor")
        soa_file = st.file_uploader("Upload vendor statement of account", type=["csv", "xlsx", "xls", "pdf", "png", "jpg", "jpeg", "tiff", "bmp"])
        if soa_creditor is not None and soa_file is not None and st.button("Reconcile SOA", icon=":material/fact_check:"):
            try:
                result = reconcile_vendor_soa(soa_creditor, soa_file.getvalue(), soa_file.name)
            except Exception as e:
                result = {"success": False, "error": f"Could not process this file: {e}"}
            if not result["success"]:
                st.error(result["error"])
            else:
                s = result["summary"]
                st.info(f"Matched {s['matched']} · Missing in AP {s['missing_in_ap']} · Mismatches {s['mismatches']} · Omitted from SOA {s['omitted_from_soa']}")
                m1, m2, m3, m4 = st.tabs(["Matched", "Missing in AP", "Amount mismatches", "Omitted from SOA"])
                with m1:
                    st.dataframe(result["matched"], hide_index=True)
                with m2:
                    st.dataframe(result["missing_in_ap"], hide_index=True)
                with m3:
                    st.dataframe(result["amount_mismatches"], hide_index=True)
                with m4:
                    st.dataframe(result["omitted_from_soa"], hide_index=True)
