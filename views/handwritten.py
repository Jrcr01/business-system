"""Record a handwritten check (owner, 2026-09-30/10-01). The owner's mother hand-writes urgent checks and sends photos; the owner records them. Usually
the photo is a new invoice that needs a post-dated check at the same time, or a check that corrects an earlier one. One form, one save, one
confirmation (engines/handwritten_check_service.py does the work and refuses everything wrong before writing anything)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from engines.common import has_value, php
from engines.handwritten_check_service import problems_with, record_handwritten_checks
from views.shared import MONEY_FORMAT, confirm_create_dialog, flash, q, selectbox_creditor


def _scope_sql(statement_id, loan_note_id) -> tuple[str, tuple]:
    """Only the checks of the batch / loan note picked above (a new invoice in a new batch has no scope: any of the payee's checks)."""
    if loan_note_id is not None:
        return " AND loan_note_id = ?", (int(loan_note_id),)
    if statement_id is not None:
        return " AND statement_id = ?", (int(statement_id),)
    return "", ()


def _replace_options(creditor_id: int, statement_id=None, loan_note_id=None) -> dict:
    """The checks that could be the one being replaced -- those of the batch / loan note picked above, or all of the payee's when none is picked yet:
    voided ones first (the usual case), then still-active ones. Already replaced are left out."""
    scope, params = _scope_sql(statement_id, loan_note_id)
    df = q("""SELECT check_id, check_number, check_date, amount, status FROM checks_issued
              WHERE creditor_id = ? AND status IN ('Active', 'Cancelled') AND replacement_check_id IS NULL""" + scope + """
              ORDER BY (status = 'Cancelled') DESC, check_id DESC LIMIT 200""", (int(creditor_id),) + params)
    label = {"Cancelled": "voided", "Active": "still active — will be voided"}
    return {int(r["check_id"]): f"{r['check_number'] or 'no number'} · {r['check_date']} · {php(r['amount'])} · {label[r['status']]}" for _, r in df.iterrows()}


def _old_check_terms(creditor_id: int, statement_id=None, loan_note_id=None) -> dict:
    """check_id -> (date, amount) of the checks in _replace_options, so a replacement starts with the same date and amount."""
    scope, params = _scope_sql(statement_id, loan_note_id)
    df = q("""SELECT check_id, check_date, amount FROM checks_issued WHERE creditor_id = ? AND status IN ('Active', 'Cancelled') AND replacement_check_id IS NULL"""
           + scope + """ ORDER BY check_id DESC LIMIT 200""", (int(creditor_id),) + params)
    return {int(r["check_id"]): (pd.to_datetime(r["check_date"]).date(), float(r["amount"])) for _, r in df.iterrows()}


def _show_loan_note_checks(loan_note_id: int) -> None:
    """What this loan note already has: its total payable, the active checks issued against it and what is still left to cover (so a handwritten check is
    recorded against the right note, and nothing is entered twice)."""
    note = q("SELECT total_payable, principal_amount FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
    checks = q("""SELECT check_number, check_date, amount, CASE WHEN is_handwritten = 1 THEN 'handwritten' WHEN printed_at IS NOT NULL THEN 'printed' ELSE 'to print' END AS how
                  FROM checks_issued WHERE loan_note_id = ? AND status = 'Active' ORDER BY check_date, check_id""", (loan_note_id,))
    payable = float(note["total_payable"][0]) if not note.empty and has_value(note["total_payable"][0]) else None
    issued = float(checks["amount"].sum()) if not checks.empty else 0.0
    if payable is None:
        st.caption(f"No total payable on this loan note yet. {len(checks)} check(s) issued, {php(issued)}.")
    else:
        st.caption(f"Total payable {php(payable)} · checks already issued {len(checks)} = {php(issued)} · still to cover **{php(payable - issued)}**")
    if not checks.empty:
        with st.expander(f"Checks already on this loan note ({len(checks)})"):
            st.dataframe(checks, hide_index=True, column_config={"check_number": "Check no.", "check_date": "Date", "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT), "how": "State"})


def render_handwritten_form() -> None:
    st.caption("For a check someone wrote by hand and you are recording afterwards. It is saved with its real number, marked as already printed and tagged "
               "handwritten, so it never goes into Checks to print or the Chrysanth file. Add the invoice at the same time if it is a new one.")
    to = st.radio("Paid to", ["A supplier", "A private lender (loan note)"], horizontal=True, key="hw_to")
    is_supplier = to == "A supplier"
    creditor = selectbox_creditor("Supplier" if is_supplier else "Lender", "SUPPLIER" if is_supplier else "LENDER", "hw_supplier" if is_supplier else "hw_lender")
    if not creditor:
        return
    cid = int(creditor)
    statement_id = loan_note_id = invoice = None
    n = st.session_state.get("hw_nonce", 0)                       # bumped after a save, so every box starts empty again

    if is_supplier:
        batches = q("""SELECT os.statement_id, os.statement_ref, os.status, v.net_total FROM order_statements os
                       JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
                       WHERE os.creditor_id = ? AND os.status != 'Cancelled' AND os.is_locked = 0
                       ORDER BY (os.status = 'Draft') DESC, os.statement_id DESC""", (cid,))
        labels = {int(r["statement_id"]): f"{r['statement_ref']} · {r['status']} · net {php(r['net_total'])}" for _, r in batches.iterrows()}
        statement_id = st.selectbox("Batch", [None] + list(labels), format_func=lambda s: "New batch for this invoice" if s is None else labels[s], key=f"hw_batch_{cid}_{n}",
                                    help="Locked and cancelled batches are not listed. Pick 'New batch' when this is a brand-new invoice.")
        new_invoice = st.checkbox("This is a new invoice — add it together with the check", value=statement_id is None, key=f"hw_new_inv_{cid}_{statement_id}_{n}")
        if new_invoice:
            c1, c2, c3, c4 = st.columns([2, 1.6, 1.6, 1])
            doc_ref = c1.text_input("Invoice number", key=f"hw_doc_{cid}_{n}")
            inv_date = c2.date_input("Date received", value=date.today(), key=f"hw_idate_{cid}_{n}")
            gross = c3.number_input("Invoice amount", min_value=0.0, step=100.0, format="%.2f", key=f"hw_gross_{cid}_{n}")
            less = c4.number_input("Less %", min_value=0.0, max_value=100.0, step=1.0, key=f"hw_less_{cid}_{n}")
            invoice = {"doc_ref": doc_ref, "invoice_date": inv_date, "gross": gross, "less_pct": less}
        elif statement_id is None:
            st.info("Pick a batch, or tick \"new invoice\".")
            return
    else:
        notes = q("""SELECT loan_note_id, note_ref, status FROM loan_notes WHERE creditor_id = ? AND status NOT IN ('Cancelled', 'Settled', 'Defaulted')
                     ORDER BY loan_note_id DESC""", (cid,))
        if notes.empty:
            st.info("This lender has no open loan note.")
            return
        opts = {int(r["loan_note_id"]): f"{r['note_ref']} · {r['status']}" for _, r in notes.iterrows()}
        loan_note_id = st.selectbox("Loan note", list(opts), format_func=opts.get, key=f"hw_ln_{cid}_{n}")
        _show_loan_note_checks(int(loan_note_id))

    repl = _replace_options(cid, statement_id, loan_note_id)
    replaces = st.selectbox("Does this replace an earlier check?", [None] + list(repl), format_func=lambda c: "No" if c is None else repl[c], key=f"hw_repl_{cid}_{statement_id}_{loan_note_id}_{n}",
                            help="Pick the voided (or wrong) check this one takes the place of; the two are linked, and a still-active one is voided first. "
                                 "The new check starts with the same date and amount — change them if they differ.")
    start_date, start_amount = _old_check_terms(cid, statement_id, loan_note_id).get(replaces, (date.today(), 0.0)) if replaces else (date.today(), 0.0)

    st.markdown("**The handwritten check(s)**")
    grid = st.data_editor(
        pd.DataFrame({"check_number": [""], "check_date": [start_date], "amount": [start_amount]}), num_rows="dynamic", hide_index=True, key=f"hw_checks_{cid}_{n}_{replaces}",
        column_config={"check_number": st.column_config.TextColumn("Check number", help="Exactly as printed on the check, e.g. 3992554297."),
                       "check_date": st.column_config.DateColumn("Check date", default=date.today()),
                       "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT, min_value=0.0, step=0.01, default=0.0)})
    checks = []
    for _, r in grid.iterrows():
        number = str(r["check_number"] or "").strip()
        amount = float(r["amount"] or 0)
        if not number and amount <= 0:
            continue                                             # an untouched blank row
        checks.append({"number": number, "date": pd.to_datetime(r["check_date"]).date() if pd.notna(r["check_date"]) else None, "amount": amount})

    if st.button("Record the handwritten check(s)", icon=":material/edit_note:", type="primary", key="hw_save"):
        problem = problems_with(cid, checks, statement_id, loan_note_id, invoice, replaces)
        if problem:
            st.error(problem)
            return
        lines = [f"- check **{c['number']}** · {c['date']} · **{php(c['amount'])}**" for c in checks]
        summary = (f"Record {len(checks)} handwritten check(s) totaling **{php(sum(c['amount'] for c in checks))}**?\n\n" + "\n".join(lines)
                   + (f"\n\nTogether with new invoice **{invoice['doc_ref'].strip()}** for {php(invoice['gross'])}"
                      + (" in a new batch" if statement_id is None else "") + "." if invoice else "")
                   + (f"\n\nThis replaces **{repl[replaces]}**." if replaces else ""))
        if replaces:
            old_amount = _old_check_terms(cid, statement_id, loan_note_id).get(replaces, (None, None))[1]
            new_total = sum(c["amount"] for c in checks)
            if old_amount is not None and abs(new_total - old_amount) > 0.005:
                summary += f"\n\n:warning: **The amount differs from the check it replaces:** {php(old_amount)} → {php(new_total)} (difference {php(abs(new_total - old_amount))}). Make sure that is right."

        def _do(c=list(checks), s=statement_id, ln=loan_note_id, inv=invoice, rp=replaces, who=cid):
            ok, msg, _ids = record_handwritten_checks(who, c, statement_id=s, loan_note_id=ln, invoice=inv, replaces_check_id=rp)
            if ok:
                flash(msg)
                st.session_state["hw_nonce"] = st.session_state.get("hw_nonce", 0) + 1       # empty form for the next one
            return ok, msg

        confirm_create_dialog(summary, "hw_save_confirm", _do)
