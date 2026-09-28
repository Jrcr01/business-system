"""Data health page (System menu, 2026-09-28, owner-approved): the findings of engines/data_health_service.py. Read-only: each finding is
opened where it is fixed, or marked "It's fine" with a note so it stops showing (undo with "Show again")."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.data_health_service import GROUPS, findings, mark_fine, show_again
from views.shared import go_to, guard_duplicate_submit, money_col, page_header, refresh, section

GROUP_HELP = {
    "Checks": "Check numbers used twice, printed checks without a number, stale or far-future dates, zero amounts, and check numbers the bank "
              "paid before our check's date.",
    "Invoices and batches": "Due dates before invoice dates, unusual years, zero amounts, invoices outside any batch, empty or overpaid batches, "
                            "and checks payable to someone other than the batch's supplier.",
    "Bank vs our records": "Bank lines that disagree with our checks and are not yet dealt with on Bank and cash, and checks the bank has not "
                           "cleared long after their date.",
    "Suppliers": "The same name on file twice (a bank set up for loans and for cards is fine), inactive suppliers still owed, suppliers "
                 "without payment terms.",
}


def _table(rows: list[dict], key: str, fine: bool) -> list[str]:
    """The findings with a Select column; returns the keys ticked."""
    df = pd.DataFrame(rows)
    view = df[["check", "payee", "detail", "amount"] + (["note"] if fine else [])].copy()
    view.insert(0, "select", False)
    edited = st.data_editor(view, hide_index=True, key=key, disabled=[c for c in view.columns if c != "select"],
                            column_config={"select": st.column_config.CheckboxColumn("Select"), "check": "What looks wrong", "payee": "Payee / supplier",
                                           "detail": st.column_config.TextColumn("Details", width="large"), "amount": money_col("Amount"),
                                           "note": "Your note"})
    return [df.loc[i, "key"] for i in edited.index[edited["select"]]]


def render() -> None:
    page_header("Data health", "Records that look wrong, found by checking everything on file. Nothing here changes your records.")
    rows = findings()
    counts = {g: sum(f["group"] == g for f in rows) for g in GROUPS}
    cols = st.columns(len(GROUPS))
    for c, g in zip(cols, GROUPS):
        c.metric(g, counts[g] or "OK", border=True, help=GROUP_HELP[g])
    if not rows:
        st.success("Everything looks right — no findings.", icon=":material/verified:")
    else:
        which = st.segmented_control("Show", ["All"] + [g for g in GROUPS if counts[g]], default="All", key="dh_group")
        shown = [f for f in rows if which in (None, "All") or f["group"] == which]
        for g in GROUPS:
            part = [f for f in shown if f["group"] == g]
            if part:
                section(f"{g} ({len(part)})", help=GROUP_HELP[g])
                st.session_state.setdefault("_dh_picked", {})[g] = _table(part, f"dh_tbl_{g}", fine=False)
        picked = [k for g in GROUPS for k in st.session_state.get("_dh_picked", {}).get(g, []) if any(f["key"] == k and f in shown for f in rows)]

        section("Deal with a finding")
        a1, a2 = st.columns(2, gap="large")
        with a1:
            st.markdown("**Open it to fix it**")
            opts = {f["key"]: f"{f['check']} · {f['payee'] or '—'}" + (f" · PHP {f['amount']:,.2f}" if f["amount"] is not None else "") for f in shown}
            first = next((k for k in picked if k in opts), None)
            pick = st.selectbox("Finding", list(opts), index=list(opts).index(first) if first else 0, format_func=opts.get, key="dh_open_pick")
            target = next(f for f in shown if f["key"] == pick)
            if st.button("Open", icon=":material/open_in_new:", key="dh_open_btn", disabled=not target["page"],
                         help="Opens the batch, loan note, supplier or Bank and cash page where this record is fixed."):
                go_to(target["page"], **target["state"])
        with a2:
            st.markdown(f"**It's fine** — {len(picked)} selected")
            note = st.text_input("Note (optional)", key="dh_fine_note", placeholder="e.g. checked the stub; numbers come after printing")
            if st.button("Mark selected as fine", icon=":material/check:", type="primary", key="dh_fine_btn", disabled=not picked,
                         help="Tick findings in the tables above first. They stop showing here and on Payment run; undo below."):
                if not guard_duplicate_submit("dh_fine", *sorted(picked)):
                    st.warning("Duplicate click ignored — identical to the click just before it.")
                    return
                mark_fine(picked, note)
                st.session_state.pop("_dh_picked", None)
                refresh(done=f"Marked {len(picked)} finding(s) as fine.")

    done = [f for f in findings(include_fine=True) if f["fine"]]
    if done:
        with st.expander(f"Marked as fine ({len(done)})", icon=":material/task_alt:"):
            back = _table(done, "dh_tbl_fine", fine=True)
            if st.button("Show again", icon=":material/undo:", key="dh_again_btn", disabled=not back):
                show_again(back)
                refresh(done=f"{len(back)} finding(s) will show again.")
