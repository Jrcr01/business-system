"""Add data page (2026-09-27, the owner's idea: "an add data tab where it focuses inputs"). One place for typing in new
records. Every form here is the SAME function its own page uses (moved out of those pages unchanged), so the rules, the
duplicate / double-recording checks and the confirmations cannot drift apart. Nothing is removed from the other pages.
Invoices: the owner picks the batch each time -- the supplier's open Draft batch is suggested, or a new batch."""
from __future__ import annotations

import streamlit as st

from engines.common import php
from engines.statement_rules import create_order_statement
from views.admin import render_new_booklet_form
from views.bank_loans import render_bank_loan_schedule, render_new_bank_loan_form
from views.checks import render_log_spoiled_form
from views.credit_cards import render_add_cc_statement_form
from views.guarantee import render_guarantee_form
from views.handwritten import render_handwritten_form
from views.creditor_master import render_new_creditor_form
from views.lenders import render_add_installment_form, render_new_loan_note_form
from views.order_statement import render_batch_invoices, render_batch_memos
from views.shared import (  # noqa: F401
    BL_BROWSE,
    LN_BROWSE,
    OS_BROWSE,
    go_to,
    guard_duplicate_submit,
    page_header,
    q,
    refresh,
    section,
    selectbox_creditor,
    status_badge,
)

KINDS = {
    "Invoices / credit memos": ":material/receipt: Invoices / credit memos",
    "Credit card statement": ":material/credit_card: Credit card statement",
    "Loans": ":material/handshake: Loans",
    "Records and checkbook": ":material/contacts: Records and checkbook",
    "Handwritten check": ":material/edit_note: Handwritten check",
    "Guarantee check": ":material/verified_user: Guarantee check",
}


def _invoices() -> None:
    supplier = selectbox_creditor("Supplier", "SUPPLIER", "ad_supplier")
    if not supplier:
        return
    batches = q(
        """SELECT os.statement_id, os.statement_ref, os.status, datetime(os.created_at, '+8 hours') AS created_at, v.net_total,
                  (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = os.statement_id) AS invoices,
                  EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active') AS has_checks
           FROM order_statements os JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
           WHERE os.creditor_id = ? AND os.status != 'Cancelled' AND os.is_locked = 0
           ORDER BY (os.status = 'Draft') DESC, os.statement_id DESC""",
        (int(supplier),),
    )
    labels = {int(r["statement_id"]): f"{r['statement_ref']} · {r['status']}{' (has checks)' if int(r['has_checks']) else ''} · "
                                         f"{int(r['invoices'])} invoice(s) · net {php(r['net_total'])} · created {str(r['created_at'])[:10]}"
              for _, r in batches.iterrows()}
    if batches.empty and guard_duplicate_submit("auto_create_first_batch", supplier):          # a supplier with no open batch: one is made, so there is no separate click before typing the invoice
        st.session_state["_ad_batch_next"] = create_order_statement(int(supplier))
        st.session_state["selected_statement_id"] = st.session_state["_ad_batch_next"]
        st.rerun()
    options = list(labels) + ["new"]
    has_draft = not batches.empty and (batches["status"] == "Draft").any()
    radio_key = f"ad_batch_{int(supplier)}"
    if "_ad_batch_next" in st.session_state:                     # a batch just created: select it (set before the radio draws)
        st.session_state[radio_key] = st.session_state.pop("_ad_batch_next")
    if st.session_state.get(radio_key) not in options:            # first visit, or the remembered batch is gone: suggest one
        st.session_state[radio_key] = options[0] if has_draft else "new"
    pick = st.radio("Which batch?", options, key=radio_key,
                    format_func=lambda o: ":material/add: New batch for this supplier" if o == "new" else labels[o],
                    help="Suggested: this supplier's newest open Draft batch. Locked or cancelled batches are not listed.")
    if pick == "new":
        st.caption("Creates a Draft order statement for this supplier (it gets its permanent OS number when it is promoted / parked).")
        if st.button("Create the new batch", icon=":material/note_add:", type="primary", key="ad_create_batch"):
            if guard_duplicate_submit("create_order_statement", supplier):
                new_sid = create_order_statement(int(supplier))
                st.session_state["_ad_batch_next"] = new_sid
                st.session_state["selected_statement_id"] = new_sid
                refresh(done="Draft batch created.")
            else:
                st.warning("Duplicate submission ignored — identical to the click just before it.")
        return

    sid = int(pick)
    st.session_state["selected_statement_id"] = sid             # the shared sections key their widgets to the open batch
    row = batches.set_index("statement_id").loc[sid]
    with st.container(border=True):
        h1, h2, h3 = st.columns([4, 1.4, 1.6], vertical_alignment="center")
        h1.markdown(f"**{row['statement_ref']}** · net {php(row['net_total'])} · {int(row['invoices'])} invoice(s)")
        status_badge(row["status"], h2)
        if h3.button("Open this batch", icon=":material/open_in_new:", key="ad_open_batch", width="stretch"):
            go_to("order-statements", selected_statement_id=sid, os_mode=OS_BROWSE, os_subtab="Invoices")
    what = st.segmented_control("Add", ["Invoices", "Credit memos"], default="Invoices", required=True, key="ad_inv_what", label_visibility="collapsed")
    if what == "Invoices":
        render_batch_invoices(sid)
    else:
        render_batch_memos(sid)


def _loans() -> None:
    what = st.segmented_control("Loan", ["New loan note", "Loan note installment", "New bank loan", "Bank loan schedule"],
                                default="New loan note", required=True, key="ad_loan_what", label_visibility="collapsed")
    if what == "New loan note":
        render_new_loan_note_form()
    elif what == "Loan note installment":
        notes = q("""SELECT ln.loan_note_id, ln.note_ref, c.creditor_name, ln.status FROM loan_notes ln JOIN creditors c ON c.creditor_id = ln.creditor_id
                     WHERE ln.status NOT IN ('Cancelled', 'Settled', 'Defaulted') ORDER BY c.creditor_name, ln.note_ref""")
        if notes.empty:
            st.info("No open loan notes. Create one first.")
            return
        opts = {int(r["loan_note_id"]): f"{r['creditor_name']} · {r['note_ref']} · {r['status']}" for _, r in notes.iterrows()}
        c1, c2 = st.columns([4, 1.6], vertical_alignment="bottom")
        lnid = c1.selectbox("Loan note", list(opts), format_func=opts.get, key="ad_ln_pick")
        if c2.button("Open this loan note", icon=":material/open_in_new:", key="ad_open_ln", width="stretch"):
            go_to("lenders", selected_loan_note_id=int(lnid), ln_mode=LN_BROWSE, ln_subtab="Repayment Schedule")
        render_add_installment_form(int(lnid))
    elif what == "New bank loan":
        render_new_bank_loan_form()
    else:
        loans = q("""SELECT bl.bank_loan_id, bl.loan_ref, c.creditor_name FROM bank_loans bl JOIN creditors c ON c.creditor_id = bl.creditor_id
                     WHERE bl.is_cancelled = 0 ORDER BY c.creditor_name, bl.loan_ref""")
        if loans.empty:
            st.info("No bank loans yet. Create one first.")
            return
        opts = {int(r["bank_loan_id"]): f"{r['creditor_name']} · {r['loan_ref']}" for _, r in loans.iterrows()}
        c1, c2 = st.columns([4, 1.6], vertical_alignment="bottom")
        blid = c1.selectbox("Bank loan", list(opts), format_func=opts.get, key="ad_bl_pick")
        if c2.button("Open this bank loan", icon=":material/open_in_new:", key="ad_open_bl", width="stretch"):
            go_to("bank-loans", selected_bank_loan_id=int(blid), bl_mode=BL_BROWSE)
        render_bank_loan_schedule(int(blid))


def _records() -> None:
    what = st.segmented_control("Record", ["New creditor", "New checkbook booklet", "Spoiled check"], default="New creditor", required=True,
                                key="ad_rec_what", label_visibility="collapsed")
    if what == "New creditor":
        render_new_creditor_form()
    elif what == "New checkbook booklet":
        render_new_booklet_form()
    else:
        render_log_spoiled_form()


def render() -> None:
    page_header("Add data", "One place to type in new records. Each form is the same one used on its own page, with the same checks and confirmations.")
    kind = st.segmented_control("What do you want to add?", list(KINDS), format_func=KINDS.get, default="Invoices / credit memos", required=True, key="ad_kind")
    with st.container(border=True):
        if kind == "Invoices / credit memos":
            _invoices()
        elif kind == "Credit card statement":
            render_add_cc_statement_form()
        elif kind == "Loans":
            _loans()
        elif kind == "Handwritten check":
            render_handwritten_form()
        elif kind == "Guarantee check":
            render_guarantee_form()
        else:
            _records()
