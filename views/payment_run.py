"""Payment run page (2026-09-27, per user): the first page of the menu. Top: today's quick report (due today, checks dated
today, next 7 days, overdue) with a one-page PDF. Below: the supplier payment cycle, one row per stage with what is waiting and
the button that goes where the work is done. It creates no new rules and marks nothing by itself: the only action done here is
"Mark as printed" for a Chrysanth run, the same function and wording as Checks and checkbook. Check numbers are still assigned
one order statement at a time on that batch's own screen, with its own confirmation (owner's rule)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.cc_service import get_statement_gap_alerts
from engines.check_run_service import mark_batch_printed
from engines.common import php
from engines.park_draft_service import list_park_drafts
from engines.payment_run_service import get_quick_report, get_run_stages
from engines.today_report_pdf import generate_today_report_pdf

from views.shared import (  # noqa: F401
    LN_BROWSE,
    OS_BROWSE,
    date_col,
    go_to,
    money_col,
    page_header,
    pdf_preview_and_download,
    q,
    refresh,
    section,
)


@st.cache_data(ttl="15s")
def _quick_report() -> dict:
    return get_quick_report()


@st.cache_data(ttl="15s")
def _stages() -> dict:
    return get_run_stages()


def _open_schedule_row(row: pd.Series) -> None:
    """Go to the record behind one line of the schedule (it carries a track, a reference and, for suppliers, the batch ref)."""
    if row["track"] == "Supplier" and row.get("batch"):
        sid = q("SELECT statement_id FROM order_statements WHERE statement_ref = ?", (str(row["batch"]),))
        if not sid.empty:
            go_to("order-statements", selected_statement_id=int(sid["statement_id"][0]), os_mode=OS_BROWSE, os_subtab="Checks")
    if row["track"] == "Lender":
        ln = q("SELECT loan_note_id FROM checks_issued WHERE check_number = ? AND loan_note_id IS NOT NULL", (str(row["reference"]),))
        if not ln.empty:
            go_to("lenders", selected_loan_note_id=int(ln["loan_note_id"][0]), ln_mode=LN_BROWSE, ln_subtab="Checks")
        go_to("lenders")
    go_to({"Bank Loan": "bank-loans", "Credit Card": "credit-cards"}.get(row["track"], "order-statements"))


def _stage_row(step: str, color: str, done: bool, text: str, detail: str | None = None) -> st.delta_generator.DeltaGenerator:
    """One stage line: coloured step pill, what is waiting, and a right-hand column (returned) for its button(s)."""
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.3, 5, 2.2], vertical_alignment="center")
        c1.badge(step, color="green" if done else color, icon=":material/check_circle:" if done else None)
        c2.markdown(text + (f"  \n:gray[{detail}]" if detail else ""))
        return c3


def render() -> None:
    page_header("Payment run", "What's due, and everything waiting to finish this payment cycle. Each button opens the place where the work is done.")

    # ------------------------------------------------------------------ quick report
    rep = _quick_report()
    due, ck, n7, od = rep["due_today"], rep["checks_today"], rep["next7"], rep["overdue"]
    weekend = rep["bank_day"] != rep["today"]
    due_label = f"Due {rep['bank_day']:%a, %b %d}" if weekend else "Due today"

    def peso(v: float) -> str:          # whole pesos on the cards so they fit; exact amounts are in the lists and the PDF
        return f"₱{float(v):,.0f}"

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(due_label, peso(due["amount"].sum() if not due.empty else 0.0), f"{len(due)} item(s)", delta_color="off", delta_arrow="off", border=True,
              help=("Today is not a bank day, so this is the next bank day: a weekend due date is paid on Monday. " if weekend else "")
                   + "Everything still owed that is paid this day, all tracks.")
    m2.metric("Checks dated today", peso(ck["amount"].sum() if not ck.empty else 0.0), f"{len(ck)} check(s)", delta_color="off", delta_arrow="off", border=True,
              help="Active checks with today's date: what the bank account must cover today.")
    m3.metric("Due in next 7 days", peso(rep["next7_total"]), f"{int(n7['items'].sum()) if not n7.empty else 0} item(s)", delta_color="off", delta_arrow="off", border=True,
              help="All tracks, due today up to 7 days ahead — the same figure as the Dashboard.")
    m4.metric("Overdue, not covered", peso(od["amount"].sum() if not od.empty else 0.0), f"{len(od)} item(s)",
              delta_color="red" if not od.empty else "off", delta_arrow="off", border=True,
              help="Past the due date and not covered: supplier invoices without a check for their balance, loan installments without a check, "
                   "and credit card statements still owed.")

    t_due, t_ck, t_7, t_od = st.tabs([due_label, "Checks dated today", "Next 7 days", "Overdue"])
    with t_due:
        if due.empty:
            st.caption("Nothing due.")
        else:
            st.caption("Click a line to open it.")
            show = due.assign(batch=due["batch"].fillna(""), due_date=pd.to_datetime(due["due_date"]))
            ev = st.dataframe(show[["track", "payee_name", "reference", "batch", "due_date", "amount"]], hide_index=True, on_select="rerun",
                              selection_mode="single-row", key="pr_due_pick",
                              column_config={"track": "Track", "payee_name": "Payee", "reference": "Reference", "batch": "Batch",
                                             "due_date": date_col("Due date"), "amount": money_col("Amount")})
            if ev.selection.rows:
                _open_schedule_row(due.iloc[ev.selection.rows[0]])
    with t_ck:
        if ck.empty:
            st.caption("No checks are dated today.")
        else:
            st.caption("Click a line to open its batch or loan note.")
            ev = st.dataframe(ck[["check_number", "payee", "reference", "track", "state", "amount"]], hide_index=True, on_select="rerun",
                              selection_mode="single-row", key="pr_ck_pick",
                              column_config={"check_number": "Check #", "payee": "Payee", "reference": "Batch / loan note", "track": "Track",
                                             "state": "Status", "amount": money_col("Amount")})
            if ev.selection.rows:
                r = ck.iloc[ev.selection.rows[0]]
                if pd.notna(r["statement_id"]):
                    sid = int(r["statement_id"])
                    go_to("order-statements", selected_statement_id=sid, os_mode=OS_BROWSE, os_subtab="Checks", **{f"os_checks_view__{sid}": "Checks in this batch"})
                go_to("lenders", selected_loan_note_id=int(r["loan_note_id"]), ln_mode=LN_BROWSE, ln_subtab="Checks")
    with t_7:
        if n7.empty:
            st.caption("Nothing due in the next 7 days.")
        else:
            show = n7.drop(columns=["items"]).assign(day=pd.to_datetime(n7["day"]))
            st.dataframe(show, hide_index=True, column_config={"day": date_col("Paid on"), **{c: money_col(c) for c in ["Supplier", "Lender", "Bank Loan", "Credit Card", "Total"]}})
            st.caption("A Saturday/Sunday due date is paid the next banking day (Monday). Full list: Reports → Schedule of payments — all tracks.")
    with t_od:
        if od.empty:
            st.caption("Nothing overdue.")
        else:
            st.dataframe(od.assign(due_date=pd.to_datetime(od["due_date"]))[["kind", "payee", "reference", "due_date", "amount"]], hide_index=True,
                         column_config={"kind": "What", "payee": "Payee", "reference": "Reference", "due_date": date_col("Was due"), "amount": money_col("Amount")})

    if st.button("Print today's report", icon=":material/print:", key="pr_today_pdf_btn"):
        st.session_state["_pr_today_pdf"] = generate_today_report_pdf(rep)
    if st.session_state.get("_pr_today_pdf"):
        pdf_preview_and_download(st.session_state["_pr_today_pdf"], f"today_{rep['today']:%Y%m%d}.pdf", key="pr_today_pdf_dl", label="Today's report PDF")

    # ------------------------------------------------------------------ reminders
    drafts = list_park_drafts()
    if drafts:
        st.warning(":material/edit_note: **Checks typed but not parked yet:** " + "; ".join(f"{d['label']} ({d['n']} check(s), {php(d['total'])})" for d in drafts)
                   + " — open the batch and click **Park Metrobank checks**.")
    gaps = get_statement_gap_alerts()
    missing = gaps[gaps["alert"] == "Possibly missing a statement"] if not gaps.empty else gaps
    if not missing.empty:
        rc1, rc2 = st.columns([5, 1.4], vertical_alignment="center")
        rc1.error(f":material/report: {len(missing)} credit card statement(s) may have been overlooked — " + "; ".join(missing["card_name"].astype(str)) + ".")
        if rc2.button("Open Credit cards", icon=":material/credit_card:", key="pr_cc_gap_btn", width="stretch"):
            go_to("credit-cards")

    # ------------------------------------------------------------------ the cycle
    section("Payment cycle", help="Draft → Parked → Exported to Chrysanth → Printed → Numbered → Statements. Nothing here is marked by itself.")
    stg = _stages()

    need = stg["needs_checks"]
    right = _stage_row("1 · Checks", "gray", need.empty,
                       "Every batch with invoices is covered by checks." if need.empty else
                       f"**{len(need)} batch(es)** still need checks — balance {php(float(need['balance'].sum()))}",
                       None if need.empty else ", ".join(f"{r['statement_ref']} ({r['creditor_name']})" for _, r in need.head(6).iterrows()) + (" …" if len(need) > 6 else ""))
    if not need.empty:
        opts = {int(r["statement_id"]): f"{r['statement_ref']} · {r['creditor_name']} · balance {php(r['balance'])}" for _, r in need.iterrows()}
        pick = right.selectbox("Batch", options=list(opts), format_func=opts.get, key="pr_need_pick", label_visibility="collapsed")
        if right.button("Issue checks", icon=":material/payments:", type="primary", key="pr_need_btn", width="stretch"):
            go_to("order-statements", selected_statement_id=int(pick), os_mode=OS_BROWSE, os_subtab="Checks")

    parked = stg["parked"]
    right = _stage_row("2 · Parked", "orange", parked.empty,
                       "No parked checks waiting for Chrysanth." if parked.empty else
                       f"**{len(parked)} parked check(s)** not sent to Chrysanth yet — {php(float(parked['amount'].sum()))}",
                       None if parked.empty else f"{parked['reference'].nunique()} batch(es) / loan note(s)")
    if not parked.empty and right.button("Make Chrysanth file", icon=":material/upload_file:", type="primary", key="pr_parked_btn", width="stretch"):
        go_to("checks")

    runs = stg["unprinted"]
    right = _stage_row("3 · Exported", "blue", runs.empty,
                       "Every Chrysanth run is marked printed." if runs.empty else
                       f"**{', '.join(runs['tag'])}** exported, not marked printed yet — {int(runs['check_count'].sum())} check(s), {php(float(runs['total_amount'].sum()))}",
                       None if runs.empty else "In Chrysanth, search the Remark box for the run number, print, then mark it printed here.")
    for _, r in runs.iterrows():
        if right.button(f"Mark {r['tag']} as printed", icon=":material/print:", key=f"pr_mark_{int(r['print_batch_id'])}", width="stretch"):
            ok, msg = mark_batch_printed(int(r["print_batch_id"]), True)
            if ok:
                refresh(done=msg)
            st.error(msg)

    num = stg["to_number"]
    right = _stage_row("4 · Numbers", "blue", num.empty,
                       "No printed check is waiting for its number." if num.empty else
                       f"**{int(num['checks'].sum())} printed check(s)** need their check numbers — {len(num)} batch(es) / loan note(s)",
                       None if num.empty else "Numbers are assigned one batch at a time, with its own confirmation.")
    if not num.empty:
        nopts = {i: f"{r['reference']} · {r['creditor_name']} · {int(r['checks'])} check(s)" for i, r in num.iterrows()}
        npick = right.selectbox("Batch", options=list(nopts), format_func=nopts.get, key="pr_num_pick", label_visibility="collapsed")
        if right.button("Assign numbers", icon=":material/format_list_numbered:", type="primary", key="pr_num_btn", width="stretch"):
            r = num.loc[npick]
            if pd.notna(r["statement_id"]):
                sid = int(r["statement_id"])
                go_to("order-statements", selected_statement_id=sid, os_mode=OS_BROWSE, os_subtab="Checks", **{f"os_checks_view__{sid}": "Assign numbers"})
            go_to("lenders", selected_loan_note_id=int(r["loan_note_id"]), ln_mode=LN_BROWSE, ln_subtab="Checks")

    tp = stg["to_print"]
    lpd = stg["last_print_day"]
    since = f"since {pd.Timestamp(lpd):%b %d}, the day checks were last printed" if lpd else "(no check printed yet)"
    right = _stage_row("5 · Statements", "violet", False,
                       f"**{len(tp)} order statement(s)** created or changed {since}" if not tp.empty else f"No order statement changed {since}.",
                       "Print their statements in one combined PDF (nothing is locked or marked; reprint any time).")
    if not tp.empty and right.button("Print statements", icon=":material/picture_as_pdf:", key="pr_print_btn", width="stretch"):
        go_to("order-statements", os_mode=OS_BROWSE, bulk_print_scope="Since last check print", bulk_print_exp=True)
