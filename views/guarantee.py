"""Guarantee checks screens (owner, 2026-10-01): a check given to a lender as security, usually undated, taken back once the loan is settled. It is NOT a
payment (engines/guarantee_check_service.py): this is the form to record one, the list with Return / Undo, and the one-line reminder of what lenders hold."""
from __future__ import annotations

from datetime import date

import streamlit as st

from engines.common import php
from engines.guarantee_check_service import (
    exposure, guarantee_checks, presented_count, record_guarantee_check, return_guarantee_check, undo_return_guarantee,
)
from views.shared import MONEY_FORMAT, confirm_create_dialog, export_buttons, go_to, q, selectbox_creditor, show_table  # noqa: F401

LABELS = {"check_number": "Check #", "holder": "Held by", "loan_note": "Loan note", "face_amount": "Face amount", "given_on": "Given on", "status": "Status",
          "returned_on": "Returned on", "note": "Note", "return_note": "Return note", "presented_on": "Paid by bank on"}
KINDS = {"face_amount": "money", "given_on": "date", "returned_on": "date", "presented_on": "date"}


def render_guarantee_form() -> None:
    st.caption("A guarantee check is given to a lender as security — usually with no date, and cancelled or taken back once the loan is settled. It is **not a payment**: "
               "it stays out of payables and the cash forecast. It is recorded here so the leaf is accounted for, you can see how much lenders hold, and you are "
               "warned if the bank ever pays one. Lender not listed? Add them first (Records and checkbook → New creditor, type Lender).")
    holder = selectbox_creditor("Held by (lender)", "LENDER", "gc_holder")
    if not holder:
        return
    cid = int(holder)
    notes = q("SELECT loan_note_id, note_ref, status FROM loan_notes WHERE creditor_id = ? ORDER BY loan_note_id DESC", (cid,))
    labels = {int(r["loan_note_id"]): f"{r['note_ref']} · {r['status']}" for _, r in notes.iterrows()}
    loan_note = st.selectbox("Loan note it guarantees (optional)", [None] + list(labels), format_func=lambda n: "— none / not recorded yet —" if n is None else labels[n],
                             key=f"gc_ln_{cid}")
    number = st.text_input("Check number", key="gc_number", help="Exactly as printed on the check, e.g. 3992554212.")
    c1, c2 = st.columns(2)
    face = c1.number_input("Face amount (0 = not stated)", min_value=0.0, step=1000.0, format="%.2f", key="gc_face",
                           help="What the check is written for, if known (e.g. 2,000,000). Leave 0 when nobody wrote it down.")
    given = c2.date_input("Date given to the lender", value=date.today(), max_value=date.today(), key="gc_given")
    note = st.text_input("Note (optional)", key="gc_note", placeholder='e.g. "2M" guarantee for the 2026 loan')
    if st.button("Record the guarantee check", icon=":material/verified_user:", type="primary", key="gc_save"):
        if not number.strip():
            st.error("Enter the check number.")
            return
        who = selectbox_label(holder)

        def _do(c=cid, n=number, f=face, g=given, ln=loan_note, nt=note):
            ok, msg, _ = record_guarantee_check(c, n, f if f > 0 else None, g, ln, nt)
            if ok:
                st.toast(msg)
            return ok, msg

        confirm_create_dialog(f"Record check **{number.strip()}** as a **guarantee check held by {who}**"
                              + (f" for **{php(face)}**" if face > 0 else " (amount not stated)")
                              + f", given {given:%b %d, %Y}?\n\nIt is not a payment and will not count in payables or the cash forecast.", "gc_confirm", _do)


def selectbox_label(creditor_id) -> str:
    df = q("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (int(creditor_id),))
    return str(df["creditor_name"][0]) if not df.empty else str(creditor_id)


def render_guarantee_list() -> None:
    df = guarantee_checks()
    with st.container(border=True):
        st.subheader("Guarantee checks given to lenders")
        st.caption("Checks held by a lender as security. Not payments. Mark one returned when the loan is settled and the check is taken back or cancelled.")
        if df.empty:
            st.caption("None recorded. Add one under Add data → Guarantee check.")
            return
        ex = exposure()
        m1, m2, m3 = st.columns(3)
        m1.metric("Held by lenders", ex["held"], border=True)
        m2.metric("Face amounts held", php(ex["total"]), (f"+ {ex['no_amount']} with no amount stated" if ex["no_amount"] else None), delta_color="off", delta_arrow="off", border=True)
        m3.metric("Paid by the bank", presented_count(), border=True, help="A guarantee check the bank paid or received — this should not happen. Look into it.")
        if presented_count():
            st.error("The bank paid or received a guarantee check. See Bank and cash → Need a look.", icon=":material/warning:")
        show_table(df, LABELS, hide=["guarantee_id"], kinds=KINDS)
        export_buttons(df.drop(columns=["guarantee_id"]), "guarantee_checks", key="dl_guarantee", labels=LABELS, what="guarantee checks", kinds=KINDS)
        held = df[df["status"] == "Held"]
        if not held.empty:
            with st.expander("Mark one as returned / cancelled", icon=":material/assignment_return:"):
                opts = {int(r.guarantee_id): f"{r.check_number} · {r.holder}" + (f" · {php(r.face_amount)}" if r.face_amount == r.face_amount and r.face_amount else "") for r in held.itertuples()}
                gid = st.selectbox("Guarantee check", list(opts), format_func=opts.get, key="gc_ret_pick")
                r1, r2 = st.columns(2)
                when = r1.date_input("Date returned / cancelled", value=date.today(), max_value=date.today(), key="gc_ret_date")
                why = r2.text_input("Note (optional)", key="gc_ret_note", placeholder="e.g. loan settled, check taken back")
                if st.button("Mark returned", icon=":material/check:", key="gc_ret_btn"):
                    def _do(g=int(gid), w=when, n=why):
                        ok, msg = return_guarantee_check(g, w, n)
                        if ok:
                            st.toast(msg)
                        return ok, msg

                    confirm_create_dialog(f"Mark guarantee check **{opts[int(gid)]}** as **returned / cancelled** on {when:%b %d, %Y}? It stops counting as held. You can undo this.", "gc_ret", _do)
        returned = df[df["status"] == "Returned"]
        if not returned.empty:
            with st.expander("Marked returned by mistake? Undo", icon=":material/undo:"):
                opts = {int(r.guarantee_id): f"{r.check_number} · {r.holder} · returned {r.returned_on}" for r in returned.itertuples()}
                gid = st.selectbox("Returned check", list(opts), format_func=opts.get, key="gc_undo_pick")
                if st.button("Undo — held again", icon=":material/undo:", key="gc_undo_btn"):
                    ok, msg = undo_return_guarantee(int(gid))
                    st.toast(msg)
                    st.rerun()


def guarantee_notice(show_button: bool = True) -> None:
    """One line on the Payment run / Dashboard: what lenders hold as guarantee, and an alert if the bank paid one."""
    try:
        ex, paid = exposure(), presented_count()
    except Exception:                                   # a database without the guarantee table yet
        return
    if paid:
        st.error(f":material/warning: **The bank paid or received {paid} guarantee check(s).** They were only given as security — look into it (Bank and cash → Need a look).")
    if ex["held"]:
        c1, c2 = st.columns([5, 1.6], vertical_alignment="center") if show_button else (st.container(), None)
        c1.info(f":material/verified_user: **Guarantee checks held by lenders:** {ex['held']} · {php(ex['total'])}"
                + (f" (+ {ex['no_amount']} with no amount stated)" if ex["no_amount"] else "") + " — not payments; return them when the loans are settled.")
        if c2 is not None and c2.button("Open the list", icon=":material/verified_user:", key="gc_open_btn", width="stretch"):
            go_to("checks")
