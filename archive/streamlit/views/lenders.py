"""Lenders (loan notes) screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from datetime import date
from engines.check_rules import apply_check_correction
from engines.check_rules import cancel_check
from engines.check_rules import edit_parked_check
from engines.check_rules import issue_check
from engines.handwritten_check_service import replace_check_with_number
from engines.beginning_balance_service import remove_beginning_label
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
from engines.loan_edit_service import edit_loan_note_details
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
    confirm_change_dialog,
    creditor_options_all,
    confirm_create_dialog,
    flash,
    grid_risk_lines,
    grid_risk_summary,
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
from engines.clock import manila_today


def render_loan_sheet(sheet: dict) -> None:
    """The loan note sheet (the owner's layout, 2026-10-03): the header, Loan Schedule / Payment Schedule / Difference, the loan schedule with its opening row, and the payment schedule
    with a Check Number column that fills in as checks are issued and numbered. Used for the preview in New loan note and for a saved note."""
    h = sheet["header"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LN. No.", h["ln_no"], border=True)
    c2.metric("Date", f"{h['date']:%a, %b %d, %Y}", border=True)
    c3.metric("Principal", f"{h['principal']:,.0f}", border=True)
    c4.metric("Monthly interest", f"{h['monthly_rate']:g}%", border=True)
    t1, t2, t3 = st.columns(3)
    t1.metric("Loan Schedule", f"{sheet['loan_total']:,.2f}", border=True, help="Every payment on the loan schedule: principal + interest.")
    t2.metric("Payment Schedule", f"{sheet['payment_total']:,.2f}", border=True, help="The payments as they are paid: the check's amount once a check is issued, else the scheduled amount.")
    t3.metric("Difference", "-" if abs(sheet["difference"]) < 0.005 else f"{sheet['difference']:,.2f}", border=True, help="Loan schedule less payment schedule. Anything but a dash means a check differs from the schedule.")
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**Loan schedule**")
        st.dataframe(pd.DataFrame(sheet["loan_rows"]), hide_index=True, width="stretch",
                     column_config={"date": date_col("Date"), "principal": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT), "interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT),
                                    "balance": st.column_config.NumberColumn("Outstanding balance", format=MONEY_FORMAT)})
    with right:
        st.markdown("**Payment schedule**")
        st.dataframe(pd.DataFrame(sheet["payment_rows"], columns=["no", "date", "check_number", "amount"]), hide_index=True, width="stretch",
                     column_config={"no": st.column_config.NumberColumn("No.", format="%d"), "date": date_col("Date"), "check_number": "Check number",
                                    "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})


def _last_rate(creditor_id) -> float | None:
    df = q("SELECT monthly_interest_rate AS r FROM loan_notes WHERE creditor_id = ? AND monthly_interest_rate IS NOT NULL ORDER BY loan_note_id DESC LIMIT 1", (int(creditor_id),))
    return float(df["r"][0]) if not df.empty and df["r"][0] not in ("", None) else None


def render_new_loan_note_form() -> None:
    """New loan note with the generator (owner, 2026-10-03): the lender, the principal and when to start paying the principal; the sheet shows the whole loan as you type, and one button
    makes the note, its real LN number and its schedule. 'I will upload or type it later' makes the note alone (the spreadsheet import is on the note)."""
    from engines.common import add_months
    from engines.loan_schedule_service import build_schedule, create_note_with_schedule, interest_only_months, loan_sheet
    ln_creditor = selectbox_creditor("Lender or lessor (rent)", "LENDER_OR_LESSOR", "ln_creditor")
    c1, c2, c3 = st.columns(3)
    ln_principal = c1.number_input("Principal amount", min_value=0.0, step=1000.0, key="ln_new_principal")
    ln_issue_date = c2.date_input("Loan date", value=manila_today(), key="ln_new_issue", help="The date the money was lent. Payments fall monthly on this day of the month, the first one month later.")
    last = _last_rate(ln_creditor) if ln_creditor else None
    ln_rate = c3.number_input("Monthly interest (%)", min_value=0.0, step=0.5, format="%.2f", value=last if last is not None else 0.0, key=f"ln_new_rate_{ln_creditor}",
                              help="Per month, e.g. 3 for 3%. Starts at this lender's last rate; type it for a new lender." + ("" if last is not None else " This lender has no earlier note."))
    mode = st.segmented_control("Schedule", ["Make it here", "I will upload or type it later"], default="Make it here", required=True, key="ln_new_schedule")
    rows = sheet = None
    if mode == "Make it here":
        d1, d2 = st.columns(2)
        start = d1.date_input("Start paying principal on", value=add_months(ln_issue_date, 1), key=f"ln_new_start_{ln_issue_date}",
                              help="The first payment on or after this date is the first principal payment. The months before it pay interest only.")
        n_pay = d2.number_input("Number of principal payments", min_value=1, max_value=120, value=12, step=1, key="ln_new_count", help="The principal is split equally; the last one takes any odd centavos.")
        try:
            rows = build_schedule(ln_principal, ln_rate, ln_issue_date, start, int(n_pay))
        except ValueError as e:
            st.info(str(e))
        if rows:
            only = interest_only_months(ln_issue_date, start)
            st.caption(f"{only} interest-only month(s), principal from {next(r['due_date'] for r in rows if r['principal'] > 0):%b %d, %Y}, last payment {rows[-1]['due_date']:%b %d, %Y}.")
            sheet = loan_sheet("given when created", ln_issue_date, ln_principal, ln_rate, rows)
            render_loan_sheet(sheet)
    else:
        st.caption("The note is created without a schedule. On the note, use Import schedule from spreadsheet (Excel/CSV), or type the installments.")
    ready = bool(ln_creditor) and ln_principal > 0 and (mode != "Make it here" or bool(rows))
    if st.button("Create the loan note and its schedule" if mode == "Make it here" else "Create the loan note", icon=":material/description:", type="primary", key="ln_new_go", disabled=not ready):
        err = money_amount_error(ln_principal, "Principal amount")
        if err:
            st.error(err)
        else:
            def _do_create_loan_note(c=ln_creditor, d=ln_issue_date, p=ln_principal, r=ln_rate, here=(mode == "Make it here"), s=locals().get("start"), n=locals().get("n_pay")):
                if here:
                    ok, msg, new_lnid = create_note_with_schedule(c, d, p, r, s, int(n))
                else:
                    ok, msg, new_lnid = create_loan_note(c, d, p, r)
                    if ok:
                        from engines.loan_note_rules import promote_draft_loan_note
                        msg += f" It is {promote_draft_loan_note(new_lnid)}."          # given its real number at once: no separate Promote step (owner, 2026-10-03)
                if ok:
                    st.session_state["selected_loan_note_id"] = new_lnid
                    st.session_state["_ln_mode_next"] = LN_BROWSE
                    st.session_state["ln_subtab"] = "Repayment Schedule"
                return ok, msg
            confirm_create_dialog(
                f"Create a loan note for **{selectbox_creditor_label('LENDER', ln_creditor)}** dated "
                f"{ln_issue_date:%b %d, %Y}: principal **{php(ln_principal)}** at **{ln_rate:g}% per month**"
                + (f", with its whole schedule ({len(rows)} payments, total {php(sheet['loan_total'])})?" if sheet else "?")
                + dup_note(loan_note_warnings(ln_creditor, ln_issue_date, ln_principal)),
                "create_loan_note", _do_create_loan_note,
            )


def render_add_installment_form(loan_note_id: int) -> None:
    """Add one installment to a loan note (suggested interest, lump-sum payoff button). Shared by Lenders and Add data."""
    selected_lnid = int(loan_note_id)
    ln_row = q("SELECT monthly_interest_rate, principal_amount FROM loan_notes WHERE loan_note_id = ?", (selected_lnid,)).iloc[0]
    _bal = q("SELECT outstanding_balance FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date, installment_id", (selected_lnid,))
    current_balance = float(_bal["outstanding_balance"].iloc[-1]) if not _bal.empty else float(ln_row["principal_amount"] or 0.0)
    from engines.common import add_months
    last_due = q("SELECT MAX(due_date) AS d FROM loan_installments WHERE loan_note_id = ?", (selected_lnid,))["d"][0]
    due_default = add_months(date.fromisoformat(str(last_due)[:10])) if last_due and str(last_due) not in ("None", "nan") else manila_today()       # a month after the last installment
    add_due_date = st.date_input("Due date", value=due_default, key=f"ln_add_due_date_{selected_lnid}_{due_default}", help='When this installment is due. Its interest is suggested for the days since the previous installment.')
    if st.button("Use remaining balance as principal (lump-sum payoff)", icon=":material/payments:", key="ln_lumpsum_btn"):
        st.session_state["ln_add_principal"] = current_balance
    add_principal = st.number_input("Principal", min_value=0.0, step=1000.0, key="ln_add_principal", help='The part of this payment that reduces the loan balance.')
    suggested_interest, balance_before = suggest_installment_interest(selected_lnid, add_due_date)
    st.caption(f"Suggested interest: {php(suggested_interest)} ({(ln_row['monthly_interest_rate'] or 0):g}% × {php(balance_before)} balance × days elapsed ÷ 30) — editable below.")
    add_interest = st.number_input("Interest", min_value=0.0, step=100.0, value=suggested_interest, key="ln_add_interest", help='Pre-filled with the suggestion above — change it to what the lender actually charges.')
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


def _release_slip_section(loan_note_id: int) -> None:
    """The release slip of this loan note: printed in three copies for the lender to sign when they collect their checks; the release is then recorded by its number on Release checks."""
    from engines.release_slip_service import create_slip
    from engines.thermal_print_service import BACKEND_LABELS, PrintAborted, print_loan_release_slip
    from engines.thermal_statement import build_loan_release_slip, get_printer_config, render_text
    from views.login import can_now
    if not can_now("check.release"):
        return
    waiting = q("SELECT COUNT(*) AS n FROM checks_issued WHERE loan_note_id = ? AND status = 'Active' AND released_date IS NULL AND is_handwritten = 0 AND printed_at IS NOT NULL", (loan_note_id,))["n"][0]
    if not waiting:
        return
    with st.expander(f"Release slip for the lender ({int(waiting)} printed check(s) to hand over)", icon=":material/outbox:"):
        st.caption("Print the slip in three copies (lender, filing, finance). The lender signs it when they collect their checks; you then record it on Release checks by the slip number.")
        slip = build_loan_release_slip(loan_note_id, "SUPPLIER")
        if slip is not None:
            st.code(render_text(slip), language=None, width="content")
        cfg = get_printer_config()
        if st.button(f"Print the slip, three copies — {BACKEND_LABELS.get(cfg['thermal_backend'], cfg['thermal_backend'])}", icon=":material/print:", type="primary", key=f"ln_slip_print_{loan_note_id}"):
            ok, msg, made = create_slip(loan_note_id=loan_note_id)
            if not ok:
                st.error(msg)
            else:
                try:
                    res = print_loan_release_slip(loan_note_id, ["SUPPLIER", "FILE", "FINANCE"], cfg["thermal_backend"], cfg, slip_no=made["slip_no"])
                    st.success(f"Slip {made['slip_no']} printed ({len(res['done'])} copies).")
                except PrintAborted as e:
                    st.error(str(e))


def render() -> None:
    page_header("Lenders")
    st.caption("Add or edit lender records on the Creditors page.")

    if "_ln_mode_next" in st.session_state:
        st.session_state["ln_mode"] = st.session_state.pop("_ln_mode_next")

    loan_notes_df = q(
        """SELECT ln.loan_note_id, ln.creditor_id, c.creditor_name, ln.note_ref, CASE WHEN ln.is_lease = 1 THEN 'Lease' ELSE 'Loan' END AS kind, ln.issue_date, ln.principal_amount,
                  ln.monthly_interest_rate, ln.total_interest, ln.total_payable,
                  ln.total_payable - COALESCE(
                      (SELECT SUM(amount) FROM checks_issued
                       WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date < manila_today()), 0
                  ) AS outstanding,
                  ln.status, CASE WHEN ln.is_beginning_balance = 1 THEN 'Beginning balance (from MS Access)' ELSE '' END AS source,
                  """ + loan_note_modified_sql("ln") + """ AS last_modified
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
        render_new_loan_note_form()
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
                    "creditor_id": None,          # used by the edit form, not shown
                    "kind": st.column_config.TextColumn("Kind", help="Loan = money we borrowed. Lease = rent we pay a lessor (no interest; the rent checks are its schedule)."),
                    "outstanding": st.column_config.NumberColumn("Outstanding (uncleared)", format=MONEY_FORMAT),
                    "source": st.column_config.TextColumn("Source", help="Beginning balance (from MS Access): migrated from the old system with its interest / terms not split out yet."),
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
            st.markdown(f"#### {ln_row['note_ref']} — {ln_row['creditor_name']}  " + status_text(ln_row["status"]))
            if ln_row["source"]:
                bb1, bb2 = st.columns([5, 1.8], vertical_alignment="center")
                bb1.info(f":material/history: **{ln_row['source']}** — migrated from the old system; its interest / terms are not split out yet. "
                         "The label goes away by itself when you enter the real terms below.")
                if bb2.button("Remove the label", icon=":material/label_off:", key="ln_bb_remove_btn", width="stretch"):
                    def _remove_ln_label(lnid=int(selected_lnid)):
                        ok, msg = remove_beginning_label("loan_note", lnid)
                        if ok:
                            flash(msg)
                        return ok, msg

                    confirm_change_dialog(f"Remove the beginning-balance label from loan note **{ln_row['note_ref']}**?  \nDo this only when its real terms are known. The label is not put back.",
                                          "ln_bb_remove_confirm", _remove_ln_label, "Yes, remove the label")
            if ln_row["total_payable"] in (None, ""):
                # Pending Setup (Phase 8a placeholder) -- no principal/rate on file yet. q()'s
                # underlying df_query() coerces SQL NULL to '' (fillna('')), not None -- so a
                # NULL total_payable arrives here as an empty string, not None.
                st.caption(f"Pending Setup — principal and rate not yet entered. Fill them in below (\"Edit loan note terms\") to activate this lender. Status: {ln_row['status']}")
            else:
                diff = round(ln_row["total_payable"] - float(q(
                    "SELECT COALESCE(SUM(amount),0) AS s FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_date < manila_today()",
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
                        def _promote_ln(lnid=int(selected_lnid)):
                            flash(f"Promoted to {promote_draft_loan_note(lnid)}.")
                            return True, ""

                        confirm_change_dialog(f"Give loan note **{ln_row['note_ref']}** its permanent LN number?  \nThe next number in the sequence is used and cannot be given back.",
                                              "ln_promote_confirm", _promote_ln, "Yes, give it the number")
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
                        st.caption("Settled fires automatically once Outstanding (uncleared) hits ₱0 — every check issued must also have actually passed its date, not just been written. Use this only to force Defaulted, or to correct a mistake.")
                        override_status = st.selectbox("Status", ["Active", "Settled", "Defaulted"], index=["Active", "Settled", "Defaulted"].index(ln_row["status"]), key="ln_status_override", help='Settled happens by itself once everything is paid. Use this only to mark Defaulted or to fix a mistake.')
                        if st.button("Save status", icon=":material/update:", key="ln_status_save_btn"):
                            def _save_ln_status(lnid=int(selected_lnid), new_status=override_status):
                                ok, msg = update_loan_note_status(lnid, new_status)
                                if ok:
                                    flash(msg)
                                return ok, msg

                            confirm_change_dialog(f"Change loan note **{ln_row['note_ref']}** from **{ln_row['status']}** to **{override_status}**?",
                                                  "ln_status_confirm", _save_ln_status, "Yes, change the status")
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
                        def _save_ln_terms(lnid=int(selected_lnid), principal=edit_principal, rate=edit_rate):
                            ok, msg = edit_loan_note_terms(lnid, principal, rate)
                            if ok:
                                flash(msg)
                            return ok, msg

                        confirm_change_dialog(f"Change the terms of loan note **{ln_row['note_ref']}**?\n\n"
                                              f"- principal: {php(float(ln_row['principal_amount'] or 0))} → **{php(edit_principal)}**\n"
                                              f"- monthly rate: {float(ln_row['monthly_interest_rate'] or 0):g}% → **{edit_rate:g}%**\n\n"
                                              "The schedule's balances and the note's total interest / payable are recomputed.",
                                              "ln_terms_confirm", _save_ln_terms, "Yes, save the terms")

            if not ln_is_cancelled:
                with st.expander("Edit loan note details (LN No., issue date, lender)"):
                    st.caption("For fixing what was entered wrongly or left as a placeholder (e.g. PENDING-123 → its real LN number). Changing the lender moves the note's checks to "
                               "that lender; this is refused once the bank has cleared any of them. Principal and rate are under \"Edit loan note terms\".")
                    lender_options = creditor_options_all("LENDER")
                    d1, d2, d3 = st.columns(3)
                    new_ref = d1.text_input("Loan note no.", value=str(ln_row["note_ref"]), key=f"ln_edit_ref_{selected_lnid}")
                    new_issue = d2.date_input("Issue date", value=pd.to_datetime(ln_row["issue_date"]).date(), key=f"ln_edit_issue_{selected_lnid}")
                    cur_lender = int(ln_row["creditor_id"])
                    lender_ids = list(lender_options)
                    new_lender = d3.selectbox("Lender", lender_ids, index=lender_ids.index(cur_lender) if cur_lender in lender_ids else 0,
                                              format_func=lambda cid: lender_options[cid], key=f"ln_edit_lender_{selected_lnid}")
                    if st.button("Save details", icon=":material/save:", key="ln_edit_details_btn"):
                        def _save_ln_details(lnid=int(selected_lnid), ref=new_ref, issue=new_issue, lender=int(new_lender)):
                            ok, msg = edit_loan_note_details(lnid, ref, issue, lender)
                            if ok:
                                flash(msg)
                            return ok, msg

                        n_checks = int(q("SELECT COUNT(*) AS n FROM checks_issued WHERE loan_note_id = ?", (selected_lnid,))["n"][0])
                        confirm_change_dialog(f"Change the details of loan note **{ln_row['note_ref']}**?\n\n"
                                              f"- loan note no.: {ln_row['note_ref']} → **{new_ref.strip()}**\n"
                                              f"- issue date: {pd.to_datetime(ln_row['issue_date']):%b %d, %Y} → **{new_issue:%b %d, %Y}**\n"
                                              f"- lender: {ln_row['creditor_name']} → **{lender_options.get(int(new_lender), '')}**",
                                              "ln_details_confirm", _save_ln_details, "Yes, save the details",
                                              warning=(f"Its {n_checks} check(s) will be payable to the new lender." if int(new_lender) != cur_lender and n_checks else ""))

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
                from engines.loan_schedule_service import loan_note_sheet
                saved_sheet = loan_note_sheet(int(selected_lnid))
                if saved_sheet:
                    render_loan_sheet(saved_sheet)
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

                if installments_df.empty and float(ln_row["principal_amount"] or 0) > 0:
                    from engines.common import add_months
                    from engines.loan_schedule_service import generate_schedule, preview_schedule
                    with st.expander("Make the whole schedule in one go", expanded=True):
                        st.caption("Equal monthly principal and interest on the balance still owed (the monthly rate × the balance). Look at the table, change the number or the first date, then create it; "
                                   "every row can still be edited afterwards.")
                        g1, g2, g3 = st.columns(3)
                        first_due = g1.date_input("First due date", value=add_months(pd.to_datetime(ln_row["issue_date"]).date(), 1), key=f"ln_gen_first_{selected_lnid}")
                        how_many = g2.number_input("Number of installments", min_value=1, max_value=120, value=12, step=1, key=f"ln_gen_n_{selected_lnid}")
                        only_months = g3.number_input("Interest-only months first", min_value=0, max_value=60, value=0, step=1, key=f"ln_gen_only_{selected_lnid}",
                                                      help="Months that pay interest alone before the principal payments start.")
                        preview = preview_schedule(int(selected_lnid), first_due, int(how_many), int(only_months))
                        st.dataframe(pd.DataFrame(preview), hide_index=True, width="stretch",
                                     column_config={"due_date": date_col("Due"), "principal": st.column_config.NumberColumn("Principal", format=MONEY_FORMAT),
                                                    "interest": st.column_config.NumberColumn("Interest", format=MONEY_FORMAT), "total": st.column_config.NumberColumn("Pay", format=MONEY_FORMAT),
                                                    "balance": st.column_config.NumberColumn("Balance after", format=MONEY_FORMAT)})
                        if st.button("Create this schedule", icon=":material/calendar_add_on:", type="primary", key=f"ln_gen_go_{selected_lnid}"):
                            ok, msg = generate_schedule(int(selected_lnid), first_due, int(how_many), int(only_months))
                            (refresh(done=msg) if ok else st.error(msg))
                with st.expander("Add an installment", expanded=installments_df.empty and not float(ln_row["principal_amount"] or 0) > 0):
                    render_add_installment_form(int(selected_lnid))
                ln_import_staged_key = f"ln_import_staged_{selected_lnid}"
                ln_import_errors_key = f"ln_import_errors_{selected_lnid}"
                with st.expander("Import schedule from spreadsheet"):
                    st.caption("Upload a .csv or .xlsx with Date/Principal/Interest columns (any reasonable header variant works, including Outstanding Balance for a sanity check against what this system computes). Rows are staged for review below, added via the same rule-checked path as manually typing them in.")
                    st.download_button(
                        "Download CSV template", data=get_schedule_template_csv(), file_name="loan_schedule_import_template.csv",
                        mime="text/csv", icon=":material/download:", key=f"ln_import_template_btn_{selected_lnid}",
                    )
                    ln_import_file = st.file_uploader("Schedule spreadsheet", type=["csv", "xlsx"], key=f"ln_import_uploader_{selected_lnid}", help="A .csv or .xlsx with Date / Principal / Interest columns (Outstanding Balance optional, as a check) — rows are shown for review before they're added.")
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
                        def _save_schedule():
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
                            if sched_errors:
                                if summary:
                                    st.cache_data.clear()
                                return False, "\n\n".join(sched_errors) + (f"\n\nSaved before stopping: {', '.join(summary)}." if summary else "")
                            if not summary:
                                return True, "No changes to save."
                            refresh(done="Schedule: " + ", ".join(summary) + ".")
                            return True, ""

                        removed, changed = grid_risk_lines(installments_df, edited_installments_df, "installment_id",
                                                           lambda r: f"installment #{r['installment_no']} due {str(r['due_date'])[:10]}", ["principal", "interest"])
                        if removed or changed:
                            confirm_change_dialog(grid_risk_summary("installment(s)", removed, changed), "ln_save_schedule_confirm", _save_schedule, "Yes, save the changes")
                        else:
                            ok, msg = _save_schedule()
                            if msg:
                                (st.info if ok else st.error)(msg)

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
                from engines.loan_schedule_service import issue_all_pdcs
                if len(pending_installments) > 1:
                    if st.button(f"Issue PDCs for all {len(pending_installments)} pending installments", icon=":material/payments:", type="primary", key="ln_pdc_all_btn"):
                        def _issue_all(lnid=selected_lnid):
                            ok, msg, _n = issue_all_pdcs(lnid)
                            if ok:
                                flash(msg)
                            return ok, msg

                        total = float((pending_installments["principal"] + pending_installments["interest"]).sum())
                        confirm_create_dialog(f"Park **{len(pending_installments)} post-dated checks** totaling **{php(total)}**, one for each pending installment, each dated on its due date?",
                                              "ln_pdc_all_confirm", _issue_all)
                with st.expander("Issue PDC for one installment", expanded=len(pending_installments) <= 1):
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
            _release_slip_section(int(selected_lnid))
            st.caption("Checks issued against this loan note")
            ln_checks_df = q(
                """SELECT chk.check_id, chk.check_number, chk.check_date, chk.amount, chk.status, datetime(chk.printed_at, '+8 hours') AS printed_at,
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
                        pending = []
                        for _, r in edited_ln_parked.iterrows():
                            chk_id = int(r["check_id"])
                            orig = original_by_id.loc[chk_id]
                            if str(r["check_date"]) != str(orig["check_date"]) or abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                                pending.append({"id": chk_id, "amount": float(r["amount"]), "date": r["check_date"],
                                                "old_amount": float(orig["amount"]), "old_date": orig["check_date"]})
                        if not pending:
                            st.info("No changes to save.")
                        else:
                            def _save_ln_parked(rows=pending):
                                updated, row_errors = 0, []
                                for x in rows:
                                    ok, msg = edit_parked_check(x["id"], x["amount"], x["date"])
                                    if ok:
                                        updated += 1
                                    else:
                                        row_errors.append(f"Check {x['id']}: {msg}")
                                if row_errors:
                                    if updated:
                                        st.cache_data.clear()
                                    return False, "\n\n".join(row_errors) + (f"\n\n{updated} other change(s) were saved." if updated else "")
                                flash(f"{updated} parked check(s) updated.")
                                return True, ""

                            confirm_change_dialog(f"Change **{len(pending)} parked check(s)**?\n\n" + "\n".join(
                                f"- check ID {x['id']}: date {x['old_date']} → **{x['date']}** · amount {php(x['old_amount'])} → **{php(x['amount'])}**" for x in pending),
                                "ln_save_parked_confirm", _save_ln_parked, "Yes, save the changes")

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
                        pending = []                                   # (check id, what, value to save, old shown, new shown, check number)
                        for _, r in edited_ln_corr.iterrows():
                            chk_id = int(r["check_id"])
                            orig = corr_by_id.loc[chk_id]
                            shown_no = str(orig["check_number"]).strip()
                            new_number = cell_text(r["check_number"]).strip()
                            if new_number != shown_no:
                                pending.append((chk_id, "Check Number", new_number, shown_no, new_number, shown_no))
                            if str(r["check_date"]) != str(orig["check_date"]):
                                pending.append((chk_id, "Check Date", str(r["check_date"]), str(orig["check_date"]), str(r["check_date"]), shown_no))
                            if abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                                pending.append((chk_id, "Amount", str(float(r["amount"])), php(float(orig["amount"])), php(float(r["amount"])), shown_no))
                        if not pending:
                            st.info("No changes to save.")
                        else:
                            def _save_ln_corrections(rows=pending):
                                updated, row_errors = 0, []
                                for chk_id, what, value, *_ in rows:
                                    ok, msg = apply_check_correction(chk_id, what, value, "")
                                    if ok:
                                        updated += 1
                                    else:
                                        row_errors.append(f"Check {chk_id}: {msg}")
                                if row_errors:
                                    if updated:
                                        st.cache_data.clear()
                                    return False, "\n\n".join(row_errors) + (f"\n\n{updated} other correction(s) were saved." if updated else "")
                                flash(f"{updated} correction(s) saved.")
                                return True, ""

                            confirm_change_dialog(f"Correct **{len(pending)} item(s)** on printed check(s)?\n\n" + "\n".join(
                                f"- check **{no or x[0]}** · {x[1]}: {x[3]} → **{x[4]}**" for x in pending for no in [x[5]]),
                                "ln_save_corrections_confirm", _save_ln_corrections, "Yes, correct it",
                                warning="These checks are already printed. Every change is logged (old → new, with the time) and cannot be undone from the screen.")

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
                        cr_reason = st.text_input("Reason (optional)", key="ln_cancel_reason", placeholder="e.g. wrong amount",
                                                  help="Kept with the voided check — it shows in Checks and checkbook → Voided, replaced and spoiled checks.")
                        if st.button("Cancel this check", icon=":material/cancel:", key="ln_check_cancel_btn"):
                            def _cancel_ln_check(pick=int(cr_pick), why=cr_reason):
                                ok, msg = cancel_check(pick, why)
                                if ok:
                                    flash(msg)
                                return ok, msg

                            crow = active_ln_checks.set_index("check_id").loc[int(cr_pick)]
                            confirm_change_dialog(f"Void check **{crow['check_number'] or 'no number yet'}** for **{php(crow['amount'])}**?  \nThe amount goes back to what is still payable on this loan note."
                                                  + (f"  \nReason: {cr_reason.strip()}" if cr_reason.strip() else ""),
                                                  "ln_cancel_confirm", _cancel_ln_check, "Yes, void it",
                                                  warning="A voided check is cancelled for good. If it was already handed out, make sure the payee gives it back.")
                    with cr_col_replace:
                        rep_old = active_ln_checks.set_index("check_id").loc[int(cr_pick)]
                        rep_old_date = pd.to_datetime(rep_old.get("check_date")).date() if has_value(rep_old.get("check_date")) else manila_today()
                        new_number = st.text_input("New check number (optional)", key=f"ln_replace_number_{int(cr_pick)}",
                                                   help="If the replacement was already written by hand, type its number: it is saved with that number, marked printed and handwritten. Leave blank to park it.")
                        new_date = st.date_input("New check date", value=rep_old_date, key=f"ln_replace_date_{int(cr_pick)}", help="The replacement check's date. The old check is cancelled; the new one is parked (no number yet).")
                        if st.button("Replace this check", icon=":material/sync_alt:", key="ln_replace_btn"):
                            def _replace_ln_check(pick=int(cr_pick), on=new_date, number=new_number):
                                ok, msg, *_ = replace_check_with_number(pick, on, number, "")
                                if ok:
                                    flash(msg)
                                return ok, msg

                            rrow = active_ln_checks.set_index("check_id").loc[int(cr_pick)]
                            confirm_change_dialog(f"Replace check **{rrow['check_number'] or 'no number yet'}** for **{php(rrow['amount'])}**?  \nThe old check is voided and a new one is parked for the same amount, dated {new_date:%b %d, %Y}, with no number yet.",
                                                  "ln_replace_confirm", _replace_ln_check, "Yes, replace it",
                                                  warning="The old check is cancelled for good. If it was already handed out, make sure the payee gives it back.")
