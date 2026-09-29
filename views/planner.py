"""Check-date planner page (2026-09-28). Suggested dates for invoices with no check yet, within the owner's rules (engines/
check_date_planner.py). Nothing is issued here: "Send to the batch" puts the dates in that batch's Issue-checks grid as a remembered draft,
and the owner reviews and parks them there as usual."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from engines import check_date_planner as cp
from engines.cc_forecast_service import MONTHS_AHEAD, estimated_card_dues, forecast_by_week
from engines.common import php
from views.shared import OS_BROWSE, creditor_options_all, export_buttons, go_to, page_header, refresh, section, show_table

STATUS_ICON = {"On time": "🟢", "Moved": "🟠", "Overdue": "🔴", "Doesn't fit": "⛔"}


def _rules(s: cp.PlanSettings) -> None:
    with st.expander("Rules", icon=":material/tune:", expanded=False):
        r1, r2, r3, r4 = st.columns(4)
        late = r1.number_input("Most days a check may move past its due date", min_value=0, max_value=90, value=int(s.max_days_late), step=1,
                               key="pl_late", help="When cash is short, lower-priority checks move later — never more than this many days after the due date.")
        buf = r2.number_input("Cash buffer (lowest balance to keep)", min_value=0.0, value=float(s.buffer), step=50_000.0, key="pl_buffer",
                              help="The plan never lets the forecast bank balance go under this. 0 = never under zero. Same setting as the Bank and "
                                   "cash forecast.")
        lim = r3.number_input("Daily limit — amount (0 = none)", min_value=0.0, value=float(s.daily_limit), step=50_000.0, key="pl_limit",
                              help="Most money going out on one day, counting checks already issued and loan / card dues that day.")
        mx = r4.number_input("Daily limit — checks (0 = none)", min_value=0, max_value=200, value=int(s.daily_max_checks), step=1, key="pl_max",
                             help="Most checks dated on one day, counting checks already issued.")
        sups = creditor_options_all("SUPPLIER")
        pri = st.multiselect("Priority suppliers", list(sups), default=[c for c in s.priority if c in sups], format_func=sups.get, key="pl_priority",
                             help="Paid on time first when cash is tight; the others are the ones moved later.")
        if st.button("Save rules", icon=":material/save:", key="pl_save"):
            cp.save_settings(cp.PlanSettings(int(late), float(lim), int(mx), set(int(x) for x in pri), float(buf)))
            refresh(done="Planner rules saved.")


def _open_batch(statement_id: int) -> None:
    """Forget the batch's Issue-checks widgets for this session so its grid picks up the planner's draft, then open the batch there."""
    sid = int(statement_id)
    for base in ("issue_split_mode", "issue_checks_count", "issue_checks_editor", "issue_checks_last_signature"):
        st.session_state.pop(f"{base}__{sid}", None)
    st.session_state.get("_park_bases", {}).pop(sid, None)
    go_to("order-statements", selected_statement_id=sid, os_mode=OS_BROWSE, os_subtab="Checks", **{f"os_checks_view__{sid}": "Issue checks"})


def render() -> None:
    _render_plan()
    _card_dues_ahead()


def _card_dues_ahead() -> None:
    """Credit card dues ahead (owner, 2026-09-29): a card's bill is known only a month before it is due, but supplier checks are dated
    4-5 months ahead (the usual terms) -- so estimate every card's coming bills from its last 3 statements. The estimates already count
    as cash going out in the suggested dates above and in the Cash forecast."""
    from datetime import timedelta

    from engines.clock import manila_today  # one clock (Phase F1.3)

    section(f"Credit card dues ahead ({MONTHS_AHEAD} months)",
            help="What the cards will likely ask for, week by week. Amounts already on a statement are shown as they are; the rest is "
                 "estimated from each card's last 3 statements (the middle one), with the lowest-highest of those 3 as the range.")
    weeks = forecast_by_week()
    if weeks.empty:
        st.caption("No card history to estimate from yet.")
        return
    total_rec, total_est = float(weeks["recorded"].sum()), float(weeks["estimated"].sum())
    peak = weeks.assign(total=weeks["recorded"] + weeks["estimated"]).sort_values("total", ascending=False).iloc[0]
    m1, m2, m3 = st.columns(3)
    m1.metric("Already on statements", php(total_rec), border=True, help="Unpaid card statements you have entered, due in this period.")
    m2.metric("Estimated (statement not in yet)", php(total_est), border=True,
              help=f"Likely range {php(float(weeks['low'].sum()) - total_rec)} – {php(float(weeks['high'].sum()) - total_rec)}.")
    m3.metric("Heaviest week", f"{peak['week_start']:%b %d}", php(float(peak["total"])), delta_color="off", delta_arrow="off", border=True,
              help="The week with the most card dues -- avoid dating big supplier checks around it.")
    chart = weeks.melt(id_vars=["week_start"], value_vars=["recorded", "estimated"], var_name="kind", value_name="amount")
    chart = chart.assign(week_start=pd.to_datetime(chart["week_start"]), kind=chart["kind"].map({"recorded": "On a statement", "estimated": "Estimated"}))
    st.altair_chart(alt.Chart(chart).mark_bar().encode(
        x=alt.X("week_start:T", title="Week of"), y=alt.Y("sum(amount):Q", title="Card dues (PHP)"),
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["On a statement", "Estimated"], range=["#16305B", "#9FB3D1"])),
        tooltip=[alt.Tooltip("week_start:T", title="Week of"), alt.Tooltip("kind:N", title=""), alt.Tooltip("sum(amount):Q", title="Amount", format=",.2f")]),
        width="stretch")
    show_table(weeks.assign(range=[f"{php(lo)} – {php(hi)}" for lo, hi in zip(weeks["low"], weeks["high"])])[
        ["week_start", "recorded", "estimated", "range", "cards"]],
        {"week_start": "Week of", "recorded": "On a statement", "estimated": "Estimated", "range": "Likely range", "cards": "Cards"},
        kinds={"week_start": "date", "recorded": "money", "estimated": "money", "cards": "count"})
    with st.expander("Each card's estimate", icon=":material/credit_card:"):
        today = manila_today()
        est = estimated_card_dues(today, today + timedelta(days=31 * MONTHS_AHEAD), today=today)
        if est.empty:
            st.caption("Nothing to estimate.")
        else:
            show_table(est[["due", "payee", "cardholder", "amount", "low", "high", "based_on"]],
                       {"due": "Likely due", "payee": "Card", "cardholder": "Cardholder", "amount": "Estimate", "low": "Lowest",
                        "high": "Highest", "based_on": "From statements"},
                       kinds={"due": "date", "amount": "money", "low": "money", "high": "money", "based_on": "count"})
    st.caption("Estimates are replaced by the real amount as soon as the statement is entered. They already count as cash going out in "
               "the suggested check dates above and in Bank and cash → Cash forecast. A month with nothing due can be marked \"No due\" "
               "on the Credit cards page, and its estimate disappears.")


def _render_plan() -> None:
    page_header("Check-date planner", "Suggested dates for the invoices that don't have a check yet — within your cash, daily limits and "
                                      "priorities. Nothing is issued until you park the checks yourself.")
    s = cp.load_settings()
    _rules(s)

    src = cp.cash_source()
    c1, c2 = st.columns(2)
    with c1:
        modes = (["Bank balance from the statement", "Type today's cash"] if src["fresh"] else ["Type today's cash"])
        mode = st.radio("Cash to start from", modes, horizontal=True, key="pl_cash_mode",
                        help="The latest bank balance is used when it is at most 14 days old; otherwise type what is in the bank today.")
        start_cash = None
        if mode == "Type today's cash":
            start_cash = st.number_input("Cash in the bank today", min_value=0.0, value=None, step=50_000.0, key="pl_cash",
                                         placeholder="e.g. 1,500,000")
            if src["balance"] is not None and not src["fresh"]:
                st.caption(f"The last imported bank balance is from {src['as_of']:%b %d, %Y} — too old to plan from.")
        else:
            st.caption(f"{php(src['balance'])} on {src['as_of']:%b %d, %Y}, less the checks the bank has not cleared yet.")
    with c2:
        in_mode = st.radio("Money coming in", ["Average deposits from the bank statements", "Type an amount per bank day"], horizontal=True,
                           key="pl_in_mode", help="Deposits are estimated per weekday from the imported statements. With no statements, type an estimate.")
        inflow = None
        if in_mode == "Type an amount per bank day":
            inflow = st.number_input("Expected money in per bank day", min_value=0.0, value=0.0, step=10_000.0, key="pl_inflow")
    if mode == "Type today's cash" and start_cash is None:
        st.info("Type the cash in the bank today to make a plan.", icon=":material/edit:")
        return

    try:
        p = cp.plan(start_cash=start_cash, inflow_per_day=inflow, settings=s)
    except ValueError as e:
        st.warning(str(e))
        return
    sm = p.summary
    if sm["items"] == 0:
        st.success("Nothing needs a check right now — every batch is covered by checks.", icon=":material/task_alt:")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Invoices to plan", f"{sm['items']}", php(sm["total"]), delta_color="off", delta_arrow="off", border=True)
    m2.metric("Suggested checks", f"{sm['checks']}", border=True, help="A supplier's invoices in one batch landing on the same day become one check.")
    m3.metric("Moved later", f"{sm['moved']}", f"{sm['doesnt_fit']} don't fit" if sm["doesnt_fit"] else None, delta_color="inverse",
              delta_arrow="off", border=True, help="Moved past the due date because of cash, a daily limit or another check to the same supplier that day.")
    m4.metric("Lowest balance", php(sm["lowest"][1]), f"on {sm['lowest'][0]:%b %d}", delta_color="off", delta_arrow="off", border=True)
    st.caption(f"Starting from {sm['source']} ({php(sm['start'])}); money in: {sm['inflow']}; buffer {php(sm['buffer'])}"
               + (" — no buffer set yet, so the plan only keeps the balance above zero (set one under Rules)." if sm["buffer"] <= 0 else ".")
               + " Checks are dated on bank days only (no weekends or marked days) and never before the due date.")
    if sm["already_short"]:
        day, bal = sm["already_short"]
        st.warning(f"Even before any new check, the checks already issued and the loan / card dues take the balance to {php(bal)} on "
                   f"{day:%b %d, %Y} — under your {'buffer' if sm['buffer'] > 0 else 'zero line'}. New checks can only fit after enough money "
                   "comes in; check the cash and money-in figures above.", icon=":material/trending_down:")
    if sm["doesnt_fit"]:
        st.error(f"{sm['doesnt_fit']} invoice(s) can't be paid within {s.max_days_late} day(s) of the due date without breaking a rule — shown on the "
                 "last allowed day. Options: more cash coming in, a longer delay, a smaller buffer, or talk to the supplier.", icon=":material/block:")

    d = p.daily.assign(day=pd.to_datetime(p.daily["day"]))
    line = alt.Chart(d).mark_line(point=True).encode(
        x=alt.X("day:T", title=None), y=alt.Y("balance:Q", title="Forecast balance (PHP)"),
        tooltip=[alt.Tooltip("day:T", title="Day"), alt.Tooltip("balance:Q", title="Balance", format=",.2f"),
                 alt.Tooltip("new_checks:Q", title="Suggested checks", format=",.2f"), alt.Tooltip("already_out:Q", title="Already going out", format=",.2f"),
                 alt.Tooltip("money_in:Q", title="Money in", format=",.2f")])
    bars = alt.Chart(d[d["new_checks"] > 0]).mark_bar(opacity=0.35, color="#C26A00").encode(x="day:T", y="new_checks:Q")
    rule = alt.Chart(pd.DataFrame({"y": [sm["buffer"]]})).mark_rule(color="#C62828", strokeDash=[6, 4]).encode(y="y:Q")
    st.altair_chart(line + bars + rule, width="stretch")
    st.caption("Line = forecast bank balance with the suggested checks; orange bars = suggested checks that day; dashed red = your buffer.")

    section("Suggested checks", help="One row per check to issue. Send a batch's dates to its Issue checks grid, review there, and park.")
    ch = p.checks.assign(status=[f"{STATUS_ICON.get(x, '')} {x}" for x in p.checks["status"]])
    show_table(ch[["suggested_date", "supplier", "batch", "invoices", "amount", "status"]],
               {"suggested_date": "Check date", "invoices": "Invoice(s)", "status": "Plan"}, kinds={"suggested_date": "date"})
    batches = p.checks.groupby(["statement_id", "batch", "supplier"], as_index=False).agg(n=("amount", "size"), total=("amount", "sum"))
    opts = {int(r.statement_id): f"{r.batch} · {r.supplier} — {r.n} check(s), {php(r.total)}" for r in batches.itertuples()}
    b1, b2, b3 = st.columns([3, 1.4, 1.4], vertical_alignment="bottom")
    pick = b1.selectbox("Batch", list(opts), format_func=opts.get, key="pl_batch")
    if b2.button("Send to the batch", icon=":material/send:", type="primary", key="pl_send", width="stretch",
                 help="Puts these dates in the batch's Issue checks grid (not parked yet) and opens it."):
        ok, msg = cp.send_to_batch(p, pick)
        if ok:
            _open_batch(pick)
        st.error(msg)
    if b3.button("Send to every batch", icon=":material/done_all:", key="pl_send_all", width="stretch",
                 help="Fills every batch's Issue checks grid. Each one still waits for you to review and park it (see the reminder in the sidebar)."):
        results = [cp.send_to_batch(p, sid) for sid in opts]
        for sid in opts:
            for base in ("issue_split_mode", "issue_checks_count", "issue_checks_editor", "issue_checks_last_signature"):
                st.session_state.pop(f"{base}__{sid}", None)
        bad = [m for ok, m in results if not ok]
        refresh(done=f"Sent to {len(results) - len(bad)} batch(es)." + (f" {len(bad)} not sent: {bad[0]}" if bad else "")
                + " Open each batch's Issue checks to review and park.")

    with st.expander("Every invoice and why it got its date", icon=":material/list_alt:"):
        it = p.items.assign(status=[f"{STATUS_ICON.get(x, '')} {x}" for x in p.items["status"]])
        show_table(it[["supplier", "batch", "doc_ref", "due_date", "amount", "suggested_date", "days_late", "status", "reason"]],
                   {"suggested_date": "Suggested date", "days_late": "Days after due", "status": "Plan", "reason": "Why"},
                   kinds={"suggested_date": "date", "days_late": "count"})
    export_buttons({"Suggested checks": p.checks.drop(columns=["statement_id", "creditor_id"]),
                    "Invoices": p.items.drop(columns=["statement_id", "creditor_id", "invoice_id", "due"]),
                    "Cash by day": p.daily}, "check_date_plan", key="pl_dl", what="plan",
                   labels={"suggested_date": "Check date", "invoices": "Invoice(s)", "n_invoices": "Invoices", "days_late": "Days after due",
                           "reason": "Why", "already_out": "Already going out", "new_checks": "Suggested checks", "under_buffer": "Under buffer",
                           "money_in": "Money in", "day": "Day"},
                   kinds={"suggested_date": "date", "day": "date", "already_out": "money", "new_checks": "money", "money_in": "money"})
