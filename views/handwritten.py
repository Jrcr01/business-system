"""Record a handwritten check (owner, 2026-09-30/10-01). The owner's mother hand-writes urgent checks and sends photos; the owner records them. Usually
the photo is a new invoice that needs a post-dated check at the same time, or a check that corrects an earlier one. One form, one save, one
confirmation (engines/handwritten_check_service.py does the work and refuses everything wrong before writing anything)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from engines.common import php
from engines.handwritten_check_service import problems_with, record_handwritten_checks
from views.shared import MONEY_FORMAT, confirm_create_dialog, q, selectbox_creditor


def _replace_options(creditor_id: int) -> dict:
    """The payee's checks that could be the one being replaced: voided ones first (the usual case), then still-active ones. Already replaced are left out."""
    df = q("""SELECT check_id, check_number, check_date, amount, status FROM checks_issued
              WHERE creditor_id = ? AND status IN ('Active', 'Cancelled') AND replacement_check_id IS NULL
              ORDER BY (status = 'Cancelled') DESC, check_id DESC LIMIT 200""", (int(creditor_id),))
    label = {"Cancelled": "voided", "Active": "still active — will be voided"}
    return {int(r["check_id"]): f"{r['check_number'] or 'no number'} · {r['check_date']} · {php(r['amount'])} · {label[r['status']]}" for _, r in df.iterrows()}


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

    if is_supplier:
        batches = q("""SELECT os.statement_id, os.statement_ref, os.status, v.net_total FROM order_statements os
                       JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
                       WHERE os.creditor_id = ? AND os.status != 'Cancelled' AND os.is_locked = 0
                       ORDER BY (os.status = 'Draft') DESC, os.statement_id DESC""", (cid,))
        labels = {int(r["statement_id"]): f"{r['statement_ref']} · {r['status']} · net {php(r['net_total'])}" for _, r in batches.iterrows()}
        statement_id = st.selectbox("Batch", [None] + list(labels), format_func=lambda s: "New batch for this invoice" if s is None else labels[s], key=f"hw_batch_{cid}",
                                    help="Locked and cancelled batches are not listed. Pick 'New batch' when this is a brand-new invoice.")
        new_invoice = st.checkbox("This is a new invoice — add it together with the check", value=statement_id is None, key=f"hw_new_inv_{cid}_{statement_id}")
        if new_invoice:
            c1, c2, c3, c4 = st.columns([2, 1.6, 1.6, 1])
            doc_ref = c1.text_input("Invoice number", key=f"hw_doc_{cid}")
            inv_date = c2.date_input("Date received", value=date.today(), key=f"hw_idate_{cid}")
            gross = c3.number_input("Invoice amount", min_value=0.0, step=100.0, format="%.2f", key=f"hw_gross_{cid}")
            less = c4.number_input("Less %", min_value=0.0, max_value=100.0, step=1.0, key=f"hw_less_{cid}")
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
        loan_note_id = st.selectbox("Loan note", list(opts), format_func=opts.get, key=f"hw_ln_{cid}")

    st.markdown("**The handwritten check(s)**")
    grid = st.data_editor(
        pd.DataFrame({"check_number": [""], "check_date": [date.today()], "amount": [0.0]}), num_rows="dynamic", hide_index=True, key=f"hw_checks_{cid}",
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

    repl = _replace_options(cid)
    replaces = st.selectbox("Does this replace an earlier check?", [None] + list(repl), format_func=lambda c: "No" if c is None else repl[c], key=f"hw_repl_{cid}",
                            help="Pick the voided (or wrong) check this one takes the place of; the two are linked, and a still-active one is voided first.")

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

        def _do(c=list(checks), s=statement_id, ln=loan_note_id, inv=invoice, rp=replaces, who=cid):
            ok, msg, _ids = record_handwritten_checks(who, c, statement_id=s, loan_note_id=ln, invoice=inv, replaces_check_id=rp)
            if ok:
                st.toast(msg)
            return ok, msg

        confirm_create_dialog(summary, "hw_save_confirm", _do)
