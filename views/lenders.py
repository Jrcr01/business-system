"""Lenders (loan notes) screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from datetime import date
from engines.check_rules import apply_check_correction
from engines.check_rules import cancel_check
from engines.check_rules import edit_parked_check
from engines.check_rules import issue_check
from engines.check_rules import replace_check
from engines.common import cell_text
from engines.common import has_value
from engines.common import php
from engines.duplicate_service import check_warnings
from engines.duplicate_service import loan_check_mismatches
from engines.duplicate_service import loan_installment_warnings
from engines.duplicate_service import loan_note_warnings
from engines.last_modified_sql import loan_note_modified_sql
from engines.loan_note_rules import create_loan_note
from engines.loan_note_rules import promote_draft_loan_note
from engines.loan_note_rules import update_loan_note_status
from engines.loan_note_service import generate_loan_note_pdf
from engines.loan_schedule_import_service import get_template_csv as get_schedule_template_csv
from engines.loan_schedule_import_service import parse_loan_schedule_spreadsheet
from engines.loan_schedule_service import add_loan_installment
from engines.loan_schedule_service import create_schedule_from_checks
from engines.loan_schedule_service import delete_loan_installment
from engines.loan_schedule_service import edit_loan_installment
from engines.loan_schedule_service import edit_loan_note_terms
from engines.loan_schedule_service import suggest_installment_interest
from engines.loan_schedule_service import unlinked_loan_checks
from engines.validators import money_amount_error

from views.shared import (  # noqa: F401
    LN_BROWSE,
    LN_NEW,
    MONEY_FORMAT,
    PRINT_CHECKS_IN_APP,
    confirm_cancel_loan_note_dialog,
    confirm_create_dialog,
    confirm_delete_loan_note_dialog,
    date_col,
    date_preset_filter,
    dup_needs_confirm,
    dup_note,
    dup_resume,
    guard_duplicate_submit,
    mode_label,
    money_col,
    page_header,
    pdf_preview_and_download,
    q,
    refresh,
    render_assign_number_section,
    render_check_print_section,
    section,
    selectbox_creditor,
    selectbox_creditor_label,
    status_badge,
    status_text,
)


def render() -> None:
    page_header("Lenders")
    st.caption("Add or edit lender records on the Creditors page.")

    if "_ln_mode_next" in st.session_state:
        st.session_state["ln_mode"] = st.session_state.pop("_ln_mode_next")

    loan_notes_df = q(
        """SELECT ln.loan_note_id, c.creditor_name, ln.note_ref, ln.issue_date, ln.principal_amount,
                  ln.monthly_interest_rate, ln.total_interest, ln.total_payable,
                  ln.total_payable - COALESCE(
                      (SELECT SUM(amount) FROM checks_issued
                       WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date <= DATE('now', 'localtime')), 0
                  ) AS outstanding,
                  ln.status, """ + loan_note_modified_sql("ln") + """ AS last_modified
           FROM loan_notes ln JOIN creditors c ON ln.creditor_id = c.creditor_id
           ORDER BY ln.loan_note_id DESC"""
    )

    ln_mode = st.segmented_control(
        "What do you want to do?",
        options=[LN_BROWSE, LN_NEW],
        default=LN_BROWSE,
        format_func=mode_label,
        key="ln_mode",
    )

    if ln_mode == LN_NEW:
        st.subheader("Create loan note")
        with st.form("new_loan_note_form", border=False):
            ln_creditor = selectbox_creditor("Lender", "LENDER", "ln_creditor")
            ln_issue_date = st.date_input("Issue date", value=date.today())
            ln_principal = st.number_input("Principal amount", min_value=0.0, step=1000.0)
            ln_rate = st.number_input("Monthly interest rate (%)", min_value=0.0, step=0.5, format="%.2f")
            st.caption("LN No. starts as a draft placeholder — use \"Promote to real LN No.\" below once it's ready to assign the permanent number. Total interest/payable are computed from the repayment schedule, not typed in.")
            if st.form_submit_button("Create loan note", icon=":material/description:", type="primary"):
                if ln_creditor:
                    err = money_amount_error(ln_principal, "Principal amount")
                    if err:
                        st.error(err)
                    else:
                        def _do_create_loan_note(c=ln_creditor, d=ln_issue_date, p=ln_principal, r=ln_rate):
                            ok, msg, new_lnid = create_loan_note(c, d, p, r)
                            if ok:
                                st.session_state["selected_loan_note_id"] = new_lnid
                                st.session_state["_ln_mode_next"] = LN_BROWSE
                            return ok, msg
                        confirm_create_dialog(
                            f"Create a loan note for **{selectbox_creditor_label('LENDER', ln_creditor)}** dated "
                            f"{ln_issue_date:%b %d, %Y}: principal **{php(ln_principal)}** at **{ln_rate:g}% per month**?"
                            + dup_note(loan_note_warnings(ln_creditor, ln_issue_date, ln_principal)),
                            "create_loan_note", _do_create_loan_note,
                        )
                else:
                    st.error("Lender is required.")
    else:
        st.subheader("Loan notes")
        if loan_notes_df.empty:
            st.caption("No loan notes yet — switch to \"New Loan Note\" above to create the first one.")
        else:
            ln_status_filter = st.multiselect(
                "Filter: status", options=sorted(loan_notes_df["status"].unique()), key="ln_filter_status"
            )
            filtered_loan_notes_df = loan_notes_df.copy()
            if ln_status_filter:
                filtered_loan_notes_df = filtered_loan_notes_df[filtered_loan_notes_df["status"].isin(ln_status_filter)]
            ln_mod_col, _ln_spacer = st.columns([1, 2])
            filtered_loan_notes_df = date_preset_filter(ln_mod_col, filtered_loan_notes_df, "last_modified", "Filter: last modified", "ln_filter_modified_preset")
            filtered_loan_notes_df = filtered_loan_notes_df.reset_index(drop=True)

            ln_browser_event = st.dataframe(
                filtered_loan_notes_df,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="ln_browser_df",
                column_config={
                    "principal_amount": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT),
                    "monthly_interest_rate": st.column_config.NumberColumn("Monthly rate", format="%.2f%%"),
                    "total_interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT),
                    "total_payable": st.column_config.NumberColumn("Total payable", format=MONEY_FORMAT),
                    "outstanding": st.column_config.NumberColumn("Outstanding (uncleared)", format=MONEY_FORMAT),
                },
            )
            ln_selected_rows = ln_browser_event.selection.rows if ln_browser_event and ln_browser_event.selection else []
            if ln_selected_rows and ln_selected_rows[0] < len(filtered_loan_notes_df):
                st.session_state["selected_loan_note_id"] = int(filtered_loan_notes_df.iloc[ln_selected_rows[0]]["loan_note_id"])

    selected_lnid = st.session_state.get("selected_loan_note_id") if not loan_notes_df.empty else None

    if selected_lnid is not None and not loan_notes_df.empty and selected_lnid in loan_notes_df["loan_note_id"].values:
        ln_row = loan_notes_df.set_index("loan_note_id").loc[selected_lnid]
        ln_creditor_id = int(q("SELECT creditor_id FROM loan_notes WHERE loan_note_id = ?", (selected_lnid,))["creditor_id"][0])
        ln_is_settled_or_defaulted = ln_row["status"] in ("Settled", "Defaulted", "Cancelled")
        ln_is_cancelled = ln_row["status"] == "Cancelled"

        with st.container(border=True):
            # Remaining/Outstanding only subtracts CLEARED checks (check_date already in the
            # past, same "effective_status" convention v_checks_ledger already uses) — a
            # postdated check hasn't actually left the bank yet, so it shouldn't count as paid
            # down until its date arrives.
            st.markdown(f"**{ln_row['note_ref']}** — {ln_row['creditor_name']}")
            if ln_row["total_payable"] in (None, ""):
                # Pending Setup (Phase 8a placeholder) -- no principal/rate on file yet. q()'s
                # underlying df_query() coerces SQL NULL to '' (fillna('')), not None -- so a
                # NULL total_payable arrives here as an empty string, not None.
                st.caption(f"Pending Setup — principal and rate not yet entered. Fill them in below (\"Edit loan note terms\") to activate this lender. Status: {ln_row['status']}")
            else:
                diff = round(ln_row["total_payable"] - float(q(
                    "SELECT COALESCE(SUM(amount),0) AS s FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_date <= DATE('now', 'localtime')",
                    (selected_lnid,),
                )["s"][0]), 2)
                st.caption(
                    f"Principal {php(ln_row['principal_amount'])} · Rate {ln_row['monthly_interest_rate']:g}%/mo · "
                    f"Total payable {php(ln_row['total_payable'])} · Remaining (uncleared) {php(diff)} · Status: {ln_row['status']}"
                )
            ln_col_promote, ln_col_status, ln_col_export, ln_col_remove = st.columns(4)
            with ln_col_promote:
                if str(ln_row["note_ref"]).startswith("DRAFT-"):
                    if st.button("Promote to real LN No.", icon=":material/arrow_upward:", key="ln_promote_btn"):
                        new_ref = promote_draft_loan_note(selected_lnid)
                        refresh(done=f"Promoted to {new_ref}.")
                else:
                    st.caption("LN No. is permanent.")
            with ln_col_status:
                if ln_is_cancelled:
                    st.caption("Cancelled — permanent.")
                elif ln_row["status"] == "Pending Setup":
                    # Not a manual-override target -- "Edit loan note terms" below is how a
                    # placeholder note actually activates (entering real principal/rate flips
                    # it to Active there), and "Pending Setup" isn't one of this override's
                    # own option list anyway.
                    st.caption("Pending Setup — use \"Edit loan note terms\" below to activate.")
                else:
                    with st.expander("Override status"):
                        st.caption("Settled fires automatically once Outstanding (uncleared) hits ₱0 — every check issued must also have actually reached its date, not just been written. Use this only to force Defaulted, or to correct a mistake.")
                        override_status = st.selectbox("Status", ["Active", "Settled", "Defaulted"], index=["Active", "Settled", "Defaulted"].index(ln_row["status"]), key="ln_status_override")
                        if st.button("Save status", icon=":material/update:", key="ln_status_save_btn"):
                            ok, msg = update_loan_note_status(selected_lnid, override_status)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh(done=msg)
            with ln_col_export:
                with st.expander("Export Loan Note PDF"):
                    if st.button("Generate Loan Note PDF", icon=":material/picture_as_pdf:", key="ln_export_btn"):
                        ln_pdf_bytes = generate_loan_note_pdf(selected_lnid)
                        if ln_pdf_bytes:
                            pdf_preview_and_download(ln_pdf_bytes, f"loan_note_{ln_row['note_ref']}.pdf".replace(" ", "_"), key="ln_pdf_download", label="Loan Note PDF")
                        else:
                            st.error("Could not generate the Loan Note PDF.")
            with ln_col_remove:
                if ln_is_cancelled:
                    st.caption("Already cancelled.")
                else:
                    ln_check_count = int(q("SELECT COUNT(*) AS n FROM checks_issued WHERE loan_note_id = ?", (selected_lnid,))["n"][0])
                    if ln_check_count == 0:
                        if st.button("Delete loan note", icon=":material/delete_forever:", key="ln_delete_btn"):
                            confirm_delete_loan_note_dialog(selected_lnid, ln_row["note_ref"])
                    else:
                        if st.button("Cancel loan note", icon=":material/cancel:", key="ln_cancel_btn"):
                            active_ln_check_row = q(
                                "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'",
                                (selected_lnid,),
                            )
                            confirm_cancel_loan_note_dialog(
                                selected_lnid, ln_row["note_ref"],
                                int(active_ln_check_row["n"][0]), float(active_ln_check_row["total"][0]),
                            )

            if not ln_is_settled_or_defaulted:
                with st.expander("Edit loan note terms (Principal / Rate)"):
                    st.caption("Only Principal and Monthly Interest Rate can be changed here — the schedule's balances and this note's Total Interest/Payable recompute automatically. Existing installment rows keep their own already-entered Interest values; only future suggestions pick up a changed rate.")
                    edit_principal = st.number_input("Principal amount", min_value=0.0, step=1000.0, value=float(ln_row["principal_amount"] or 0.0), key="ln_edit_principal")
                    edit_rate = st.number_input("Monthly interest rate (%)", min_value=0.0, step=0.5, format="%.2f", value=float(ln_row["monthly_interest_rate"] or 0.0), key="ln_edit_rate")
                    if st.button("Save changes", icon=":material/save:", key="ln_edit_terms_btn"):
                        ok, msg = edit_loan_note_terms(selected_lnid, edit_principal, edit_rate)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh(done=msg)

        tab_schedule, tab_ln_checks = st.tabs(
            ["Repayment Schedule", "Checks"], key="ln_subtab", default="Repayment Schedule", on_change="rerun",
        )

        with tab_schedule:
            installments_df = q(
                """SELECT installment_id, installment_no, due_date, principal, interest, outstanding_balance, status
                   FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date, installment_id""",
                (selected_lnid,),
            )
            if not installments_df.empty:
                installments_df["due_date"] = pd.to_datetime(installments_df["due_date"]).dt.date
            if ln_is_settled_or_defaulted:
                st.caption(f"This loan note is {ln_row['status']} — schedule is read-only.")
                st.dataframe(
                    installments_df,
                    hide_index=True,
                    column_config={
                        "principal": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT),
                        "interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT),
                        "outstanding_balance": st.column_config.NumberColumn("Outstanding Balance", format=MONEY_FORMAT),
                    },
                )
            else:
                st.caption("Add installments as real payment dates are committed to — Interest is a suggested value (rate × outstanding balance × days since the previous row ÷ 30) that you can freely override for lender-specific conventions.")
                current_balance = float(installments_df["outstanding_balance"].iloc[-1]) if not installments_df.empty else float(ln_row["principal_amount"] or 0.0)

                with st.expander("Add an installment", expanded=installments_df.empty):
                    add_due_date = st.date_input("Due date", value=date.today(), key="ln_add_due_date")
                    if st.button("Use remaining balance as principal (lump-sum payoff)", icon=":material/payments:", key="ln_lumpsum_btn"):
                        st.session_state["ln_add_principal"] = current_balance
                    add_principal = st.number_input("Principal", min_value=0.0, step=1000.0, key="ln_add_principal")
                    suggested_interest, balance_before = suggest_installment_interest(selected_lnid, add_due_date)
                    st.caption(f"Suggested interest: {php(suggested_interest)} ({(ln_row['monthly_interest_rate'] or 0):g}% × {php(balance_before)} balance × days elapsed ÷ 30) — editable below.")
                    add_interest = st.number_input("Interest", min_value=0.0, step=100.0, value=suggested_interest, key="ln_add_interest")
                    if st.button("Add installment", icon=":material/playlist_add:", type="primary", key="ln_add_installment_btn") or dup_resume("add_loan_installment"):
                        if dup_needs_confirm("add_loan_installment", loan_installment_warnings(selected_lnid, [(add_due_date, add_principal, add_interest)])):
                            pass
                        elif guard_duplicate_submit("add_loan_installment", selected_lnid, add_due_date, add_principal, add_interest):
                            ok, msg = add_loan_installment(selected_lnid, add_due_date, add_principal, add_interest)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                del st.session_state["ln_add_principal"]
                                refresh(done=msg)
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

                ln_import_staged_key = f"ln_import_staged_{selected_lnid}"
                ln_import_errors_key = f"ln_import_errors_{selected_lnid}"
                with st.expander("Import schedule from spreadsheet"):
                    st.caption("Upload a .csv or .xlsx with Date/Principal/Interest columns (any reasonable header variant works, including Outstanding Balance for a sanity check against what this system computes). Rows are staged for review below, added via the same rule-checked path as manually typing them in.")
                    st.download_button(
                        "Download CSV template", data=get_schedule_template_csv(), file_name="loan_schedule_import_template.csv",
                        mime="text/csv", icon=":material/download:", key=f"ln_import_template_btn_{selected_lnid}",
                    )
                    ln_import_file = st.file_uploader("Schedule spreadsheet", type=["csv", "xlsx"], key=f"ln_import_uploader_{selected_lnid}")
                    if st.button("Parse schedule file", icon=":material/upload_file:", key=f"ln_import_parse_btn_{selected_lnid}"):
                        if not ln_import_file:
                            st.warning("Upload a file first.")
                        else:
                            imported_rows, import_messages = parse_loan_schedule_spreadsheet(ln_import_file.getvalue(), ln_import_file.name)
                            staged = [{
                                "due_date": r.due_date, "principal": r.principal, "interest": r.interest,
                                "sheet_balance": r.outstanding_balance if r.outstanding_balance is not None else float("nan"),
                            } for r in imported_rows]
                            st.session_state[ln_import_staged_key] = staged
                            st.session_state[ln_import_errors_key] = import_messages

                    for m in st.session_state.get(ln_import_errors_key, []):
                        st.warning(m)

                    ln_staged_rows = st.session_state.get(ln_import_staged_key, [])
                    if ln_staged_rows:
                        st.caption(f"{len(ln_staged_rows)} row(s) staged — review/correct below, then add. 'Sheet Balance' (from an imported file, if it had one) is shown for comparison only; this system always computes its own.")
                        ln_edited_import_df = st.data_editor(
                            pd.DataFrame(ln_staged_rows),
                            hide_index=True,
                            num_rows="dynamic",
                            disabled=["sheet_balance"],
                            key=f"ln_import_staging_editor_{selected_lnid}",
                            column_config={
                                "due_date": st.column_config.DateColumn("Due Date", format="MMM DD, YYYY"),
                                "principal": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT, min_value=0.0, step=0.01),
                                "interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT, min_value=0.0, step=0.01),
                                "sheet_balance": st.column_config.NumberColumn("Sheet Balance", format=MONEY_FORMAT),
                            },
                        )
                        ln_import_col_add, ln_import_col_discard = st.columns(2)
                        with ln_import_col_add:
                            if st.button("Add these installments", icon=":material/playlist_add:", key=f"ln_import_commit_btn_{selected_lnid}") or dup_resume("ln_import_commit"):
                              if dup_needs_confirm("ln_import_commit", loan_installment_warnings(selected_lnid, [
                                  (r["due_date"], float(r["principal"] or 0), float(r["interest"] or 0))
                                  for _, r in ln_edited_import_df.iterrows() if not pd.isna(r["due_date"])
                              ])):
                                pass
                              elif not guard_duplicate_submit("ln_import_commit", selected_lnid, ln_edited_import_df.to_csv(index=False)):
                                st.warning("Duplicate submission ignored — identical to the click just before it.")
                              else:
                                added, import_commit_errors = 0, []
                                for _, r in ln_edited_import_df.sort_values("due_date").iterrows():
                                    if pd.isna(r["due_date"]):
                                        continue
                                    ok, msg = add_loan_installment(selected_lnid, r["due_date"], float(r["principal"] or 0), float(r["interest"] or 0))
                                    if ok:
                                        added += 1
                                    else:
                                        import_commit_errors.append(f"{r['due_date']}: {msg}")
                                if added:
                                    st.success(f"{added} installment(s) added.")
                                for e in import_commit_errors:
                                    st.error(e)
                                st.session_state[ln_import_staged_key] = []
                                st.session_state[ln_import_errors_key] = []
                                if added:
                                    refresh()
                        with ln_import_col_discard:
                            if st.button("Discard parsed rows", icon=":material/delete_sweep:", key=f"ln_import_discard_btn_{selected_lnid}"):
                                st.session_state[ln_import_staged_key] = []
                                st.session_state[ln_import_errors_key] = []
                                st.rerun()

                if not installments_df.empty:
                    edited_installments_df = st.data_editor(
                        installments_df,
                        hide_index=True,
                        num_rows="dynamic",
                        disabled=["installment_id", "installment_no", "outstanding_balance", "status"],
                        key="ln_schedule_editor",
                        column_config={
                            "installment_id": st.column_config.NumberColumn("ID"),
                            "installment_no": st.column_config.NumberColumn("No."),
                            "due_date": st.column_config.DateColumn("Due Date", format="MMM DD, YYYY"),
                            "principal": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT, min_value=0.0, step=0.01),
                            "interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT, min_value=0.0, step=0.01),
                            "outstanding_balance": st.column_config.NumberColumn("Outstanding Balance", format=MONEY_FORMAT),
                            "status": st.column_config.TextColumn("Status"),
                        },
                    )
                    if st.button("Save schedule changes", icon=":material/save:", key="ln_save_schedule_btn"):
                        original_by_id = installments_df.set_index("installment_id")
                        seen_ids, updated, deleted, sched_errors = set(), 0, 0, []
                        for _, r in edited_installments_df.iterrows():
                            if pd.isna(r["installment_id"]):
                                continue  # blank template row from the dynamic editor's add affordance
                            iid = int(r["installment_id"])
                            seen_ids.add(iid)
                            orig = original_by_id.loc[iid]
                            if (str(r["due_date"]) != str(orig["due_date"]) or abs(float(r["principal"]) - float(orig["principal"])) > 0.001
                                    or abs(float(r["interest"]) - float(orig["interest"])) > 0.001):
                                ok, msg = edit_loan_installment(iid, r["due_date"], float(r["principal"]), float(r["interest"]))
                                if ok:
                                    updated += 1
                                else:
                                    sched_errors.append(f"Installment {iid}: {msg}")
                        for removed_id in set(original_by_id.index) - seen_ids:
                            ok, msg = delete_loan_installment(int(removed_id))
                            if ok:
                                deleted += 1
                            else:
                                sched_errors.append(msg)
                        summary = [f"{n} {label}" for n, label in [(updated, "updated"), (deleted, "deleted")] if n]
                        if summary:
                            st.success("Schedule: " + ", ".join(summary) + ".")
                        for e in sched_errors:
                            st.error(e)
                        if summary:
                            refresh()

        with tab_ln_checks:
            pending_installments = q(
                "SELECT installment_id, installment_no, due_date, principal, interest FROM loan_installments WHERE loan_note_id = ? AND status = 'Pending' ORDER BY due_date",
                (selected_lnid,),
            )
            if ln_is_settled_or_defaulted:
                st.caption(f"This loan note is {ln_row['status']} — no further checks can be issued.")
            elif pending_installments.empty:
                st.caption("No pending installments to issue a check against — add one in the Repayment Schedule tab.")
            else:
                with st.expander("Issue PDC for an installment", expanded=True):
                    pending_pick = st.selectbox(
                        "Installment",
                        options=pending_installments["installment_id"],
                        format_func=lambda iid: (lambda r: f"#{r['installment_no']} — due {r['due_date']} — {php(r['principal'] + r['interest'])}")(pending_installments.set_index("installment_id").loc[iid]),
                        key="ln_pdc_installment_pick",
                    )
                    pending_row = pending_installments.set_index("installment_id").loc[int(pending_pick)]
                    pdc_amount = float(pending_row["principal"] + pending_row["interest"])
                    pdc_date = pd.to_datetime(pending_row["due_date"]).date()
                    st.caption(f"Check Date and Amount are taken directly from this installment's schedule row — {pdc_date} for {php(pdc_amount)}. To change either, edit the schedule row itself (in the Repayment Schedule tab) before issuing. No check number yet — that's assigned when the check is printed.")
                    if st.button("Issue Metrobank PDC", icon=":material/payments:", key="ln_pdc_issue_btn") or dup_resume("issue_pdc"):
                        if dup_needs_confirm("issue_pdc", check_warnings(ln_creditor_id, [(pdc_amount, pdc_date)])):
                            pass
                        elif guard_duplicate_submit("issue_pdc", int(pending_pick), pdc_amount, pdc_date):
                            ok, msg, check_id = issue_check(ln_creditor_id, pdc_amount, pdc_date, loan_note_id=selected_lnid, installment_id=int(pending_pick))
                            if ok:
                                refresh(done=msg)
                            else:
                                st.error(msg)
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

            ln_mismatches = loan_check_mismatches(selected_lnid)
            if ln_mismatches:
                st.warning("Checks not matching the repayment schedule:" + chr(10) + chr(10) + chr(10).join(f"- {m}" for m in ln_mismatches))
            ln_unlinked = unlinked_loan_checks(selected_lnid) if not ln_is_settled_or_defaulted else []
            if ln_unlinked and ln_row["total_payable"] not in (None, ""):
                with st.expander(f"Create schedule from existing checks ({len(ln_unlinked)} checks not tied to the schedule)"):
                    st.caption("Gives each of these checks its own schedule row: due date = check date, principal = check amount, interest = 0 "
                               "(a check can't say how much of it is interest — edit a row later if you know). Nothing is saved if the checks add up to more than the loan's principal.")
                    st.dataframe(pd.DataFrame(ln_unlinked, columns=["check_id", "check_number", "check_date", "amount"]), hide_index=True,
                                 column_config={"amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
                    st.caption(f"Total of these checks: {php(sum(float(c[3]) for c in ln_unlinked))} · Loan principal: {php(ln_row['principal_amount'])}")
                    if st.button("Create schedule rows from these checks", icon=":material/playlist_add_check:", key="ln_sched_from_checks_btn"):
                        def _do_sched(lnid=selected_lnid):
                            ok, msg = create_schedule_from_checks(lnid)
                            if ok:
                                st.toast(msg)
                            return ok, msg
                        confirm_create_dialog(f"Create a schedule row for each of these {len(ln_unlinked)} checks (interest 0)?", "ln_sched_from_checks", _do_sched)
            st.caption("Checks issued against this loan note")
            ln_checks_df = q(
                """SELECT chk.check_id, chk.check_number, chk.check_date, chk.amount, chk.status, datetime(chk.printed_at, 'localtime') AS printed_at,
                          li.installment_no
                   FROM checks_issued chk LEFT JOIN loan_installments li ON li.check_id = chk.check_id
                   WHERE chk.loan_note_id = ? ORDER BY chk.check_id DESC""",
                (selected_lnid,),
            )
            display_ln_checks = ln_checks_df.copy()
            if not display_ln_checks.empty:
                display_ln_checks["check_number"] = display_ln_checks["check_number"].fillna("Pending (not yet printed)")
            st.dataframe(display_ln_checks, hide_index=True, column_config={"amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})

            if PRINT_CHECKS_IN_APP:
                render_check_print_section(ln_checks_df, ln_row["creditor_name"], key_prefix=f"ln_check_print__{selected_lnid}")
            render_assign_number_section(ln_checks_df, key_prefix=f"ln_check_assign__{selected_lnid}")

            active_ln_checks = ln_checks_df[ln_checks_df["status"] == "Active"]
            parked_ln_checks = active_ln_checks[~active_ln_checks["check_number"].apply(has_value)] if not active_ln_checks.empty else active_ln_checks
            if not parked_ln_checks.empty:
                with st.expander("Edit parked checks (date & amount stay editable until numbered)"):
                    original_ln_parked = parked_ln_checks[["check_id", "check_date", "amount"]].copy()
                    original_ln_parked["check_date"] = pd.to_datetime(original_ln_parked["check_date"]).dt.date
                    edited_ln_parked = st.data_editor(
                        original_ln_parked, hide_index=True, num_rows="fixed", disabled=["check_id"], key="ln_edit_parked_editor",
                        column_config={
                            "check_id": st.column_config.NumberColumn("ID"),
                            "check_date": st.column_config.DateColumn("Check Date", format="MMM DD, YYYY"),
                            "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT, min_value=0.01, step=0.01),
                        },
                    )
                    if (st.button("Save parked check edits", icon=":material/save:", key="ln_save_parked_edits") or dup_resume("ln_save_parked_edits")) and not dup_needs_confirm(
                        "ln_save_parked_edits",
                        loan_check_mismatches(selected_lnid, {int(r["check_id"]): (float(r["amount"]), r["check_date"]) for _, r in edited_ln_parked.iterrows()
                         if str(r["check_date"]) != str(original_ln_parked.set_index("check_id").loc[int(r["check_id"]), "check_date"])
                         or abs(float(r["amount"]) - float(original_ln_parked.set_index("check_id").loc[int(r["check_id"]), "amount"])) > 0.001}),
                    ):
                        original_by_id = original_ln_parked.set_index("check_id")
                        updated, row_errors = 0, []
                        for _, r in edited_ln_parked.iterrows():
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

            numbered_ln_checks = active_ln_checks[active_ln_checks["check_number"].apply(has_value)] if not active_ln_checks.empty else active_ln_checks
            if not ln_is_settled_or_defaulted and not numbered_ln_checks.empty:
                with st.expander("Correct a numbered check (number, date or amount)"):
                    st.caption("Edit Check Number, Check Date or Amount directly, then Save. Every change is logged automatically (old → new + timestamp). "
                               "Date and Amount can only be corrected once a check has been printed — an unprinted check is edited under 'Edit parked checks' instead.")
                    original_ln_corr = numbered_ln_checks[["check_id", "check_number", "check_date", "amount"]].copy()
                    original_ln_corr["check_date"] = pd.to_datetime(original_ln_corr["check_date"]).dt.date
                    edited_ln_corr = st.data_editor(
                        original_ln_corr, hide_index=True, num_rows="fixed", disabled=["check_id"], key="ln_correct_checks_editor",
                        column_config={
                            "check_id": st.column_config.NumberColumn("ID"),
                            "check_number": st.column_config.TextColumn("Check Number"),
                            "check_date": st.column_config.DateColumn("Check Date", format="MMM DD, YYYY"),
                            "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT, min_value=0.01, step=0.01),
                        },
                    )
                    corr_by_id = original_ln_corr.set_index("check_id")

                    def _ln_corr_changes():
                        out = {}
                        for _, r in edited_ln_corr.iterrows():
                            o = corr_by_id.loc[int(r["check_id"])]
                            if str(r["check_date"]) != str(o["check_date"]) or abs(float(r["amount"]) - float(o["amount"])) > 0.001:
                                out[int(r["check_id"])] = (float(r["amount"]), r["check_date"])
                        return out

                    if (st.button("Save check corrections", icon=":material/save:", key="ln_save_check_corrections") or dup_resume("ln_save_check_corrections")) \
                            and not dup_needs_confirm("ln_save_check_corrections", loan_check_mismatches(selected_lnid, _ln_corr_changes())):
                        updated, row_errors = 0, []
                        for _, r in edited_ln_corr.iterrows():
                            chk_id = int(r["check_id"])
                            orig = corr_by_id.loc[chk_id]
                            new_number = cell_text(r["check_number"]).strip()
                            for ctype, changed, value in (
                                ("Check Number", new_number != str(orig["check_number"]).strip(), new_number),
                                ("Check Date", str(r["check_date"]) != str(orig["check_date"]), str(r["check_date"])),
                                ("Amount", abs(float(r["amount"]) - float(orig["amount"])) > 0.001, str(float(r["amount"]))),
                            ):
                                if changed:
                                    ok, msg = apply_check_correction(chk_id, ctype, value, "")
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

            if not ln_is_settled_or_defaulted and not active_ln_checks.empty:
                with st.expander("Cancel or replace a check"):
                    cr_pick = st.selectbox(
                        "Check",
                        options=active_ln_checks["check_id"],
                        format_func=lambda cid: active_ln_checks.set_index("check_id").loc[cid, "check_number"] or "Pending (not yet printed)",
                        key="ln_cr_pick",
                    )
                    cr_col_cancel, cr_col_replace = st.columns(2)
                    with cr_col_cancel:
                        if st.button("Cancel this check", icon=":material/cancel:", key="ln_check_cancel_btn"):
                            ok, msg = cancel_check(int(cr_pick))
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh(done=msg)
                    with cr_col_replace:
                        new_date = st.date_input("New check date", value=date.today(), key="ln_replace_date")
                        if st.button("Replace this check (new one parked)", icon=":material/sync_alt:", key="ln_replace_btn"):
                            ok, msg, *_ = replace_check(int(cr_pick), new_date, "")
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh(done=msg)
