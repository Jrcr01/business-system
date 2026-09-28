"""Bank loans screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import pandas as pd
import sqlite3
import streamlit as st

from datetime import date
from engines.bank_loan_import_service import get_template_csv as get_bank_loan_template_csv
from engines.bank_loan_import_service import parse_bank_loan_spreadsheet
from engines.bank_loan_report_service import generate_bank_loan_pdf
from engines.bank_loan_service import add_bank_loan_installment
from engines.bank_loan_service import add_loan_beneficiary
from engines.bank_loan_service import create_bank_loan
from engines.bank_loan_service import delete_bank_loan
from engines.bank_loan_service import delete_bank_loan_installment
from engines.bank_loan_service import edit_bank_loan_beneficiary
from engines.bank_loan_service import edit_bank_loan_installment
from engines.common import php
from engines.duplicate_service import bank_loan_warnings
from engines.duplicate_service import bank_schedule_warnings
from engines.last_modified_sql import bank_loan_modified_sql
from engines.reports_service import get_bank_loan_servicing_report
from engines.validators import money_amount_error

from views.shared import (  # noqa: F401
    BL_BROWSE,
    BL_NEW,
    MONEY_FORMAT,
    confirm_cancel_bank_loan_dialog,
    confirm_create_dialog,
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
    section,
    selectbox_creditor,
    selectbox_creditor_label,
    status_badge,
    status_text,
)
from engines.clock import manila_today


def render_new_bank_loan_form() -> None:
    """The beneficiary registry + Create bank loan form. Shared by Bank loans and Add data."""
    bl_beneficiaries_df = q("SELECT * FROM loan_beneficiaries ORDER BY name")
    with st.expander("Loan beneficiary registry"):
        st.caption("Who actually uses/benefits from a loan the company pays for — a person or an affiliate. Reusable across multiple loans.")
        st.dataframe(bl_beneficiaries_df, hide_index=True)
        with st.form("new_loan_beneficiary_form", border=False):
            lb_name = st.text_input("Name")
            lb_type = st.selectbox("Type", ["Person", "Affiliate"])
            lb_contact = st.text_input("Contact number (optional)")
            if st.form_submit_button("Add beneficiary", icon=":material/person_add:"):
                if lb_name:
                    if guard_duplicate_submit("add_loan_beneficiary", lb_name, lb_type, lb_contact):
                        add_loan_beneficiary(lb_name, lb_type, lb_contact)
                        refresh(done=f"Added {lb_type.lower()} {lb_name}.")
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")
                else:
                    st.error("Name is required.")

    with st.form("new_bank_loan_form", border=False):
        bl_creditor = selectbox_creditor("Bank", "BANK_LOAN", "bl_creditor")
        bl_ref = st.text_input("Loan reference", help="The loan / promissory-note number on the bank's document — each loan needs its own.")
        bl_account_number = st.text_input("Bank account number (optional)")
        if bl_beneficiaries_df.empty:
            st.caption("Add a beneficiary above first, or leave unattributed for now.")
            bl_beneficiary = None
        else:
            bl_beneficiary = st.selectbox(
                "Attributable to (optional)", options=[None] + list(bl_beneficiaries_df["beneficiary_id"]),
                format_func=lambda bid: "—" if bid is None else f"{bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'name']} ({bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'beneficiary_type']})",
                help='Whose loan this is in practice (a person or company). Shown with the loan in reports; optional.',
            )
        c1, c2, c3 = st.columns(3)
        bl_start = c1.date_input("Start date", value=manila_today())
        bl_maturity = c2.date_input("Maturity date", value=manila_today())
        bl_repricing = c3.date_input("Repricing date (optional)", value=None, help='When the bank may change the interest rate (from the loan document) — for your reference.')
        bl_principal = st.number_input("Principal amount (original loan amount)", min_value=0.0, step=10000.0, help='The original amount borrowed. The monthly dues come from the schedule you enter or import, not from this.')
        bl_mode_of_payment = st.text_input("Mode of payment (informational only, from the bank's document)", value="ADA")
        st.caption("Mode of payment is descriptive only — it doesn't drive any settlement logic. This system tracks payment as untracked cash by default regardless of what's entered here.")
        if st.form_submit_button("Create bank loan", icon=":material/account_balance:", type="primary"):
            if bl_creditor and bl_ref:
                err = money_amount_error(bl_principal, "Principal amount")
                if not err and bl_maturity <= bl_start:
                    err = "Maturity date must be after the start date."
                if err:
                    st.error(err)
                else:
                    def _do_create_bank_loan(args=(bl_creditor, bl_ref, bl_account_number, bl_start, bl_maturity, bl_repricing, bl_principal, bl_mode_of_payment, bl_beneficiary)):
                        try:
                            new_blid = create_bank_loan(*args)
                        except sqlite3.IntegrityError:
                            return False, "This loan reference already exists."
                        except ValueError as e:
                            return False, str(e)
                        st.session_state["selected_bank_loan_id"] = new_blid
                        st.session_state["_bl_mode_next"] = BL_BROWSE
                        return True, ""
                    confirm_create_dialog(
                        f"Create bank loan **{bl_ref}** with **{selectbox_creditor_label('BANK_LOAN', bl_creditor)}**: "
                        f"principal **{php(bl_principal)}**, {bl_start:%b %d, %Y} to {bl_maturity:%b %d, %Y}?"
                        + dup_note(bank_loan_warnings(bl_creditor, bl_account_number, bl_principal, bl_start)),
                        "create_bank_loan", _do_create_bank_loan,
                    )
            else:
                st.error("Bank and loan reference are required.")


def render_bank_loan_schedule(selected_blid: int) -> None:
    """A bank loan's repayment schedule: spreadsheet import + the editable Due Date / Amount grid. Shared by Bank loans and Add data."""
    st.subheader("Repayment schedule")
    st.caption("Due Date and Amount, straight off the bank's document. Interest is optional — fill it in when you know it, straight off the same document, to feed the interest-tracking dashboard; leave it 0 otherwise. A row is treated as Paid automatically once its due date is in the past; add a row with the blank row at the bottom of the table.")

    bl_schedule_df = q(
        """SELECT installment_id, installment_no, due_date, amount, interest_portion
           FROM bank_loan_installments WHERE bank_loan_id = ? ORDER BY due_date, installment_id""",
        (selected_blid,),
    )
    if not bl_schedule_df.empty:
        bl_schedule_df["due_date"] = pd.to_datetime(bl_schedule_df["due_date"]).dt.date
        bl_schedule_df["status"] = bl_schedule_df["due_date"].apply(lambda d: "Paid" if d < manila_today() else "Pending")

    bl_import_staged_key = f"bl_import_staged_{selected_blid}"
    bl_import_errors_key = f"bl_import_errors_{selected_blid}"
    with st.expander("Import schedule from spreadsheet"):
        st.caption("Upload a .csv or .xlsx with Due Date/Amount columns (any reasonable header variant works). Rows are staged for review below — nothing is saved until you confirm.")
        st.download_button(
            "Download CSV template", data=get_bank_loan_template_csv(), file_name="bank_loan_schedule_import_template.csv",
            mime="text/csv", icon=":material/download:", key=f"bl_import_template_btn_{selected_blid}",
        )
        bl_import_file = st.file_uploader("Schedule spreadsheet", type=["csv", "xlsx"], key=f"bl_import_uploader_{selected_blid}", help="The bank's schedule as .csv or .xlsx with Due Date and Amount columns — rows are shown for review before anything is saved. A template is above.")
        if st.button("Parse schedule file", icon=":material/upload_file:", key=f"bl_import_parse_btn_{selected_blid}"):
            if not bl_import_file:
                st.warning("Upload a file first.")
            else:
                bl_imported_rows, bl_import_messages = parse_bank_loan_spreadsheet(bl_import_file.getvalue(), bl_import_file.name)
                st.session_state[bl_import_staged_key] = [{"due_date": r.due_date, "amount": r.amount} for r in bl_imported_rows]
                st.session_state[bl_import_errors_key] = bl_import_messages

        for m in st.session_state.get(bl_import_errors_key, []):
            st.warning(m)

        bl_staged_rows = st.session_state.get(bl_import_staged_key, [])
        if bl_staged_rows:
            st.caption(f"{len(bl_staged_rows)} row(s) staged — review/correct below, then add.")
            bl_edited_import_df = st.data_editor(
                pd.DataFrame(bl_staged_rows),
                hide_index=True,
                num_rows="dynamic",
                key=f"bl_import_staging_editor_{selected_blid}",
                column_config={
                    "due_date": st.column_config.DateColumn("Due Date", format="MMM DD, YYYY"),
                    "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT, step=0.01),
                },
            )
            bl_import_col_add, bl_import_col_discard = st.columns(2)
            with bl_import_col_add:
                if st.button("Add these installments", icon=":material/playlist_add:", key=f"bl_import_commit_btn_{selected_blid}") or dup_resume("bl_import_commit"):
                  if dup_needs_confirm("bl_import_commit", bank_schedule_warnings(selected_blid, [
                      (r["due_date"], float(r["amount"] or 0)) for _, r in bl_edited_import_df.iterrows() if not pd.isna(r["due_date"])
                  ])):
                    pass
                  elif not guard_duplicate_submit("bl_import_commit", selected_blid, bl_edited_import_df.to_csv(index=False)):
                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                  else:
                    bl_added, bl_import_commit_errors = 0, []
                    for _, r in bl_edited_import_df.sort_values("due_date").iterrows():
                        if pd.isna(r["due_date"]):
                            continue
                        ok, msg = add_bank_loan_installment(selected_blid, r["due_date"], float(r["amount"] or 0))
                        if ok:
                            bl_added += 1
                        else:
                            bl_import_commit_errors.append(f"{r['due_date']}: {msg}")
                    if bl_added:
                        st.success(f"{bl_added} installment(s) added.")
                    for e in bl_import_commit_errors:
                        st.error(e)
                    st.session_state[bl_import_staged_key] = []
                    st.session_state[bl_import_errors_key] = []
                    if bl_added:
                        refresh(done=f"{bl_added} installment(s) added.")
            with bl_import_col_discard:
                if st.button("Discard parsed rows", icon=":material/delete_sweep:", key=f"bl_import_discard_btn_{selected_blid}"):
                    st.session_state[bl_import_staged_key] = []
                    st.session_state[bl_import_errors_key] = []
                    st.rerun()

    bl_editor_template = bl_schedule_df if not bl_schedule_df.empty else pd.DataFrame(columns=["installment_id", "installment_no", "due_date", "amount", "interest_portion", "status"])
    bl_edited_schedule_df = st.data_editor(
        bl_editor_template,
        hide_index=True,
        num_rows="dynamic",
        disabled=["installment_id", "installment_no", "status"],
        key="bl_schedule_editor",
        column_config={
            "installment_id": st.column_config.NumberColumn("ID"),
            "installment_no": st.column_config.NumberColumn("No."),
            "due_date": st.column_config.DateColumn("Due Date", format="MMM DD, YYYY"),
            "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT, min_value=0.0, step=0.01),
            "interest_portion": st.column_config.NumberColumn("Interest (optional)", format=MONEY_FORMAT, min_value=0.0, step=0.01, default=0.0),
            "status": st.column_config.TextColumn("Status (auto)"),
        },
    )
    if (st.button("Save schedule changes", icon=":material/save:", key="bl_save_schedule_btn") or dup_resume("bl_save_schedule")) and not dup_needs_confirm("bl_save_schedule", bank_schedule_warnings(selected_blid, [
        (r["due_date"], float(r["amount"] or 0)) for _, r in bl_edited_schedule_df.iterrows() if pd.isna(r["installment_id"]) and not pd.isna(r["due_date"])
    ])):
        bl_original_by_id = bl_schedule_df.set_index("installment_id") if not bl_schedule_df.empty else pd.DataFrame()
        bl_seen_ids, bl_added_n, bl_updated_n, bl_deleted_n, bl_sched_errors = set(), 0, 0, 0, []
        for _, r in bl_edited_schedule_df.iterrows():
            if pd.isna(r["due_date"]):
                continue
            amount = float(r["amount"] or 0)
            interest_portion = float(r.get("interest_portion") or 0)

            if pd.isna(r["installment_id"]):
                ok, msg = add_bank_loan_installment(selected_blid, r["due_date"], amount, interest_portion)
                if ok:
                    bl_added_n += 1
                else:
                    bl_sched_errors.append(msg)
                continue

            iid = int(r["installment_id"])
            bl_seen_ids.add(iid)
            orig = bl_original_by_id.loc[iid]
            if (str(r["due_date"]) != str(orig["due_date"]) or abs(amount - float(orig["amount"])) > 0.001
                    or abs(interest_portion - float(orig.get("interest_portion") or 0)) > 0.001):
                ok, msg = edit_bank_loan_installment(iid, r["due_date"], amount, interest_portion)
                if ok:
                    bl_updated_n += 1
                else:
                    bl_sched_errors.append(f"Installment {iid}: {msg}")

        if not bl_original_by_id.empty:
            for removed_id in set(bl_original_by_id.index) - bl_seen_ids:
                ok, msg = delete_bank_loan_installment(int(removed_id))
                if ok:
                    bl_deleted_n += 1
                else:
                    bl_sched_errors.append(f"Installment {removed_id}: {msg}")

        if bl_added_n or bl_updated_n or bl_deleted_n:
            st.success(f"Saved — {bl_added_n} added, {bl_updated_n} updated, {bl_deleted_n} deleted.")
        for e in bl_sched_errors:
            st.error(e)
        if bl_added_n or bl_updated_n or bl_deleted_n:
            refresh(done=f"Schedule saved — {bl_added_n} added, {bl_updated_n} updated, {bl_deleted_n} deleted.")


def render() -> None:
    page_header("Bank loans")
    st.caption("Add or edit lending bank records on the Creditors page. Just Due Date and Amount per installment — no computation, with one optional addition: an interest portion, when you know it, to feed the interest-tracking dashboard. A due date is treated as Paid automatically once it's in the past (same convention as a check's cleared status elsewhere in this app), not a manual flag. Settlement is untracked cash: no check or bank debit is recorded.")

    if "_bl_mode_next" in st.session_state:
        st.session_state["bl_mode"] = st.session_state.pop("_bl_mode_next")

    bl_beneficiaries_df = q("SELECT * FROM loan_beneficiaries ORDER BY name")

    bank_loans_df = q(
        """SELECT bl.bank_loan_id, c.creditor_name, bl.loan_ref, bl.account_number, bl.start_date, bl.maturity_date,
                  bl.principal_amount,
                  CASE WHEN bl.is_cancelled = 1 THEN 0 ELSE COALESCE(
                      (SELECT SUM(amount) FROM bank_loan_installments
                       WHERE bank_loan_id = bl.bank_loan_id AND due_date >= manila_today()), 0
                  ) END AS outstanding,
                  bl.mode_of_payment, CASE WHEN bl.is_cancelled = 1 THEN 'Cancelled' ELSE bl.status END AS status, bl.is_cancelled, lb.name AS beneficiary, lb.beneficiary_type, bl.beneficiary_id,
                  """ + bank_loan_modified_sql("bl") + """ AS last_modified
           FROM bank_loans bl JOIN creditors c ON bl.creditor_id = c.creditor_id
           LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
           ORDER BY bl.bank_loan_id DESC"""
    )

    bl_mode = st.segmented_control(
        "What do you want to do?",
        options=[BL_BROWSE, BL_NEW],
        default=BL_BROWSE,
        format_func=mode_label,
        key="bl_mode",
    )

    if bl_mode == BL_NEW:
        st.subheader("Create bank loan")
        render_new_bank_loan_form()
    else:
        st.subheader("Bank loans")
        if bank_loans_df.empty:
            st.caption("No bank loans yet — switch to \"New Bank Loan\" above to create the first one.")
        else:
            bl_mod_col, _bl_spacer = st.columns([1, 2])
            filtered_bank_loans_df = date_preset_filter(bl_mod_col, bank_loans_df, "last_modified", "Filter: last modified", "bl_filter_modified_preset").reset_index(drop=True)
            bl_browser_event = st.dataframe(
                filtered_bank_loans_df.drop(columns=["beneficiary_id", "is_cancelled"]),
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="bl_browser_df",
                column_config={
                    "principal_amount": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT),
                    "outstanding": st.column_config.NumberColumn("Outstanding", format=MONEY_FORMAT),
                    "beneficiary": st.column_config.TextColumn("Attributable To"),
                    "beneficiary_type": st.column_config.TextColumn("Type"),
                },
            )
            bl_selected_rows = bl_browser_event.selection.rows if bl_browser_event and bl_browser_event.selection else []
            if bl_selected_rows and bl_selected_rows[0] < len(filtered_bank_loans_df):
                st.session_state["selected_bank_loan_id"] = int(filtered_bank_loans_df.iloc[bl_selected_rows[0]]["bank_loan_id"])

    selected_blid = st.session_state.get("selected_bank_loan_id") if not bank_loans_df.empty else None

    if selected_blid is not None and not bank_loans_df.empty and selected_blid in bank_loans_df["bank_loan_id"].values:
        bl_row = bank_loans_df.set_index("bank_loan_id").loc[selected_blid]
        st.divider()
        with st.container(border=True):
            st.markdown(f"#### {bl_row['loan_ref']} — {bl_row['creditor_name']}  " + status_text(bl_row["status"]))
            bl_beneficiary_text = f"{bl_row['beneficiary']} ({bl_row['beneficiary_type']})" if pd.notna(bl_row["beneficiary_id"]) and bl_row["beneficiary_id"] != "" else "not set"
            st.caption(f"Status: {bl_row['status']} · Mode of payment (informational): {bl_row['mode_of_payment']} · Outstanding: {php(bl_row['outstanding'])} · Attributable to: {bl_beneficiary_text}")
            if not bl_beneficiaries_df.empty:
                with st.expander("Edit attribution (who this loan is for)"):
                    with st.form("edit_bl_beneficiary_form", border=False):
                        current_bid = None if pd.isna(bl_row["beneficiary_id"]) or bl_row["beneficiary_id"] == "" else int(bl_row["beneficiary_id"])
                        beneficiary_options = [None] + list(bl_beneficiaries_df["beneficiary_id"])
                        new_beneficiary = st.selectbox(
                            "Attributable to", options=beneficiary_options,
                            index=beneficiary_options.index(current_bid) if current_bid in beneficiary_options else 0,
                            format_func=lambda bid: "—" if bid is None else f"{bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'name']} ({bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'beneficiary_type']})",
                        )
                        if st.form_submit_button("Save", icon=":material/save:"):
                            edit_bank_loan_beneficiary(selected_blid, new_beneficiary)
                            refresh(done="Updated.")
            bl_installment_count = int(q("SELECT COUNT(*) AS n FROM bank_loan_installments WHERE bank_loan_id = ?", (selected_blid,))["n"][0])
            bl_col_export, bl_col_delete = st.columns(2)
            with bl_col_export:
                with st.expander("Export Bank Loan PDF"):
                    if st.button("Generate Bank Loan PDF", icon=":material/picture_as_pdf:", key="bl_export_btn"):
                        bl_pdf_bytes = generate_bank_loan_pdf(selected_blid)
                        if bl_pdf_bytes:
                            pdf_preview_and_download(bl_pdf_bytes, f"bank_loan_{bl_row['loan_ref']}.pdf".replace(" ", "_"), key="bl_pdf_download", label="Bank Loan PDF")
                        else:
                            st.error("Could not generate the Bank Loan PDF.")
            with bl_col_delete:
                if int(bl_row["is_cancelled"]):
                    st.caption("Cancelled — repayment rows are kept on record but not counted as due.")
                elif bl_installment_count > 0:
                    if st.button("Cancel this bank loan", icon=":material/cancel:", key="bl_cancel_btn"):
                        confirm_cancel_bank_loan_dialog(selected_blid, bl_row["loan_ref"], bl_installment_count)
                if not int(bl_row["is_cancelled"]) and bl_installment_count == 0:
                    if st.button("Delete this bank loan (no schedule rows entered yet)", icon=":material/delete:", key="bl_delete_btn"):
                        ok, msg = delete_bank_loan(selected_blid)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.session_state["selected_bank_loan_id"] = None
                            refresh(done=msg)

        render_bank_loan_schedule(int(selected_blid))
    with st.expander("Bank loan servicing report"):
        st.dataframe(get_bank_loan_servicing_report(), hide_index=True)
