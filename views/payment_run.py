"""Payment run page (2026-09-27, per user): the first page of the menu. Top: today's quick report (due today, checks dated
today, next 7 days, overdue) with a one-page PDF. Below: the supplier payment cycle, one row per stage with what is waiting and
the button that goes where the work is done. It creates no new rules and marks nothing by itself: the only action done here is
"Mark as printed" for a Chrysanth run, the same function and wording as Checks and checkbook. Check numbers are still assigned
one order statement at a time on that batch's own screen, with its own confirmation (owner's rule)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.bank_alerts import all_alerts
from engines.data_health_service import open_count
from engines.bank_names import card_display_name
from engines.cc_service import get_statement_gap_alerts
from engines.check_rules import get_booklets
from engines.check_run_service import mark_batch_printed
from engines.checkbook_service import set_reported_up_to, unrecorded_alerts
from engines.common import has_value, php
from engines.drive_service import GoogleDrive
from engines.park_draft_service import list_park_drafts
from engines.statement_version_service import daily_link_sweep, expired_public_versions
from engines.payment_run_service import get_quick_report, get_run_stages
from engines.today_report_pdf import generate_today_report_pdf

from views.bank_cash import show_alerts
from views.credit_cards import render_missing_statements
from views.guarantee import guarantee_notice
from views.shared import (  # noqa: F401
    LN_BROWSE,
    confirm_change_dialog,
    flash,
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


def _booklet_notice() -> None:
    """Checks written by hand (owner, 2026-10-01): the owner's mother hand-writes urgent checks from her own booklet and sends photos; a forgotten
    photo means a check missing from the system. Warns about leaves that look used but are not recorded, and takes "her booklet is up to no. ___"."""
    try:
        alerts = unrecorded_alerts()
        booklets = get_booklets(statuses=("Active", "Exhausted"))
    except Exception:                                   # a database without the booklet tables / column yet
        return
    if not alerts.empty:
        st.warning(f":material/menu_book: **{int(alerts['count'].sum())} check number(s) look used but are not recorded** — "
                   + "; ".join(f"{r.booklet}: {int(r.count)}" for r in alerts.itertuples())
                   + ". Record them (or log them as spoiled), or open the list below.")
    if booklets.empty:
        return
    with st.expander("Check booklets — checks written by hand", icon=":material/menu_book:", expanded=not alerts.empty):
        st.caption("Type the last check number on the latest photo you received, and the app lists every number up to it that is not recorded, not logged as "
                   "spoiled and not already on a bank statement. A voided check counts as recorded.")
        for r in alerts.itertuples():
            text = r.leaves if len(r.leaves) <= 600 else r.leaves[:600] + " …"
            st.markdown(f"**{r.booklet}** — {int(r.count)} number(s) not recorded:\n\n{text}")
        names = {int(b): (c or f"Booklet #{int(b)}") + f" ({int(s)}–{int(e)})" for b, c, s, e in zip(booklets["booklet_id"], booklets["booklet_code"], booklets["start_number"], booklets["end_number"])}
        c1, c2, c3 = st.columns([3, 2.4, 1.6], vertical_alignment="bottom")
        pick = c1.selectbox("Booklet", list(names), format_func=names.get, key="pr_bk_pick")
        row = booklets[booklets["booklet_id"] == pick].iloc[0]
        start_n, end_n = int(row["start_number"]), int(row["end_number"])
        current = int(row["reported_up_to"]) if has_value(row.get("reported_up_to")) else start_n       # q() turns SQL NULL into ''
        number = c2.number_input("Last check number seen on a photo", min_value=start_n, max_value=end_n, value=min(max(current, start_n), end_n), step=1,
                                 key=f"pr_bk_num_{int(pick)}", help="The number printed on the last check you received a photo of.")
        if c3.button("Save", icon=":material/save:", key="pr_bk_save", width="stretch"):
            ok, msg = set_reported_up_to(int(pick), int(number))
            if ok:
                refresh(done=msg)
            st.error(msg)
        if st.button("Record a handwritten check", icon=":material/edit_note:", key="pr_bk_record"):
            go_to("add-data", ad_kind="Handwritten check")


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


def _loan_installments_without_checks() -> pd.DataFrame:
    """Loan-note installments due within 45 days that have no check yet (the schedule rows still Pending), one row per loan note."""
    from datetime import timedelta
    from engines.clock import manila_today
    return q("""SELECT ln.loan_note_id, ln.note_ref, c.creditor_name, COUNT(*) AS installments, SUM(i.principal + i.interest) AS total
                FROM loan_installments i JOIN loan_notes ln ON ln.loan_note_id = i.loan_note_id JOIN creditors c ON c.creditor_id = ln.creditor_id
                WHERE i.status = 'Pending' AND ln.status NOT IN ('Cancelled', 'Settled', 'Defaulted') AND i.due_date <= ?
                GROUP BY ln.loan_note_id ORDER BY MIN(i.due_date)""", (str(manila_today() + timedelta(days=45)),))


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
            st.caption(f"{int(n7['items'].sum())} item(s), {peso(rep['next7_total'])} in the next 7 days. The day-by-day list is in one place: Payables due.")
        if st.button("Open Payables due", icon=":material/event_upcoming:", key="pr_n7_link"):
            go_to("payables-due", pd_tab="Week by week")
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
    # Statement QR links past their date (3 months after the batch's last check): switched off once a day here too, in case the laptop
    # was off when the nightly task runs. If Drive can't be reached, say so -- the links stay open until it can.
    try:
        swept = daily_link_sweep(GoogleDrive)
    except Exception:                                   # e.g. the database has no statement tables yet
        swept = None
    if swept and swept.get("switched_off"):
        st.toast(f"{len(swept['switched_off'])} statement QR link(s) past their date were switched off.", icon=":material/link_off:")
    try:
        overdue_links = len(expired_public_versions())
    except Exception:
        overdue_links = 0
    if overdue_links:
        st.warning(f":material/link: {overdue_links} statement QR link(s) are past their date but Google Drive could not be reached to switch them "
                   "off. Double-click **drive_connect.bat** to sign in again; they are switched off the next day.")
    # Bank and cash alerts (engines/bank_alerts.py): low cash ahead, large unexplained bank debits, checks not deposited N days after their
    # date, an old bank statement. Same list as the Bank and cash page.
    try:
        bank_alerts = all_alerts()
    except Exception:                                   # database without the bank tables yet
        bank_alerts = []
    if bank_alerts:
        b1, b2 = st.columns([5, 1.6], vertical_alignment="center")
        with b1:
            show_alerts(bank_alerts)
        if b2.button("Open Bank and cash", icon=":material/account_balance_wallet:", key="pr_bank_btn", width="stretch"):
            go_to("bank-cash")
    # Data health (2026-09-28, owner): one line when the scan of everything on file finds something not yet marked "It's fine".
    try:
        n_health = open_count()
    except Exception:                                   # a database still missing a table the scan reads
        n_health = 0
    if n_health:
        h1, h2 = st.columns([5, 1.6], vertical_alignment="center")
        h1.info(f":material/health_and_safety: **{n_health} data health finding(s)** — records that look wrong. Open them to fix, or mark them fine.")
        if h2.button("Open Data health", icon=":material/health_and_safety:", key="pr_health_btn", width="stretch"):
            go_to("data-health")
    _booklet_notice()
    guarantee_notice()
    reprint = _stages().get("needs_reprint")
    if reprint is not None and not reprint.empty:
        rp1, rp2 = st.columns([5, 1.6], vertical_alignment="center")
        rp_opts = {int(r["statement_id"]): f"{r['statement_ref']} · {r['creditor_name']}" for _, r in reprint.iterrows()}
        rp1.warning(f":material/print: **{len(reprint)} statement(s) need a reprint** — changed after they were printed: "
                    + ", ".join(rp_opts.values()) + ". The new slip says REVISED; the old QR link is switched off once it is printed.")
        rp_pick = rp2.selectbox("Batch", list(rp_opts), format_func=rp_opts.get, key="pr_reprint_pick", label_visibility="collapsed")
        if rp2.button("Open to reprint", icon=":material/print:", key="pr_reprint_btn", width="stretch"):
            go_to("order-statements", selected_statement_id=int(rp_pick), os_mode=OS_BROWSE, bulk_print_exp=False)
    drafts = list_park_drafts()
    if drafts:
        st.warning(":material/edit_note: **Checks typed but not parked yet:** " + "; ".join(f"{d['label']} ({d['n']} check(s), {php(d['total'])})" for d in drafts)
                   + " — open the batch and click **Park Metrobank checks**.")
    gaps = get_statement_gap_alerts()
    missing = gaps[gaps["alert"] == "Possibly missing a statement"] if not gaps.empty else gaps
    if not missing.empty:
        rc1, rc2 = st.columns([5, 1.4], vertical_alignment="center")
        rc1.error(f":material/report: {len(missing)} credit card statement(s) may have been overlooked — " + "; ".join(card_display_name(i, n) for i, n in zip(missing["issuer"], missing["card_name"])) + ".")
        if rc2.button("Open Credit cards", icon=":material/credit_card:", key="pr_cc_gap_btn", width="stretch"):
            go_to("credit-cards")
        with st.expander("Nothing due on one of these cards? Mark it", icon=":material/event_busy:"):
            render_missing_statements(missing, "pr")

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

    loans_due = _loan_installments_without_checks()
    right = _stage_row("1b · Loan checks", "gray", loans_due.empty,
                       "Every loan installment due soon has its check." if loans_due.empty else
                       f"**{int(loans_due['installments'].sum())} loan installment(s)** due in the next 45 days have no check yet — {php(float(loans_due['total'].sum()))}",
                       None if loans_due.empty else ", ".join(f"{r['note_ref']} ({r['creditor_name']})" for _, r in loans_due.head(6).iterrows()))
    if not loans_due.empty:
        lopts = {int(r["loan_note_id"]): f"{r['note_ref']} · {r['creditor_name']} · {int(r['installments'])} installment(s) · {php(r['total'])}" for _, r in loans_due.iterrows()}
        lpick = right.selectbox("Loan note", options=list(lopts), format_func=lopts.get, key="pr_loan_pick", label_visibility="collapsed")
        if right.button("Issue the checks", icon=":material/payments:", type="primary", key="pr_loan_btn", width="stretch"):
            go_to("lenders", selected_loan_note_id=int(lpick), ln_mode=LN_BROWSE, ln_subtab="Checks")

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
            def _mark_run(pb=int(r["print_batch_id"])):
                ok, msg = mark_batch_printed(pb, True)
                if ok:
                    flash(msg)
                return ok, msg

            confirm_change_dialog(f"Mark **{r['tag']}** as printed ({int(r['check_count'])} check(s), {php(float(r['total_amount']))})?  \nOnly do this once the run is really printed in Chrysanth.",
                                  f"pr_mark_confirm_{int(r['print_batch_id'])}", _mark_run, "Yes, it is printed")

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
