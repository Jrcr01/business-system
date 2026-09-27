"""Bank and cash page (2026-09-27, owner: "I do not have the time to reconcile which checks are cleared"). The owner downloads the Metrobank
MBOS statement and drops it here; the app reads it, stores each bank line once, and matches our checks automatically
(engines/bank_statement_service.py). Only the exceptions need a look. The insight tabs (engines/cash_insights_service.py) are computed on
this laptop -- nothing is sent anywhere (owner: local first; an AI write-up may come later, optional).

Show-both rule (owner): the rest of the app still treats a check as cleared on its check date; the bank's real cleared date is shown here
and next to the checks."""
from __future__ import annotations

import hashlib

import altair as alt
import pandas as pd
import streamlit as st

from engines.bank_statement_service import (
    exceptions, import_statement, last_import, mark_reviewed, not_yet_cleared, parse_statement, preview_matches, statement_coverage,
)
from engines.cash_insights_service import (
    cash_position, float_summary, forecast, get_buffer, inflow_trends, set_buffer, unusual_items, working_capital,
)
from engines.common import php
from views.shared import date_col, guard_duplicate_submit, money_col, page_header, q, refresh, section

KIND_LABELS = {"CHECK": "Our checks clearing", "DEPOSIT": "Money in (deposits, transfers)", "RETURNED_CHECK": "Returned (bounced) deposited checks",
               "BILLS_PAYMENT": "Bills payments", "DEBIT_MEMO": "Bank debit memos (loan debits, charges)", "OTHER": "Other"}
EXCEPTION_LABELS = {
    "cancelled_check_cleared": "A check we cancelled / replaced was cleared by the bank",
    "amount_mismatch": "Cleared amount differs from our check",
    "returned_check": "A deposited check was returned (bounced)",
    "not_in_system": "Check not in the system (older than the system, or never recorded)",
}


def _import_tab() -> None:
    st.caption("Download the Statement of Account from Metrobank MBOS (PDF works; CSV / Excel too if MBOS offers them) and drop it here. "
               "The same file is never imported twice, and overlapping periods are fine — lines already on file are skipped.")
    up = st.file_uploader("Bank statement", type=["pdf", "csv", "xlsx", "xls"], key="bank_stmt_upload", label_visibility="collapsed")
    if up is None:
        return
    data = up.getvalue()
    h = hashlib.sha256(data).hexdigest()
    cache = st.session_state.get("_bank_preview")
    if not cache or cache["hash"] != h:
        with st.spinner("Reading the statement..."):
            parsed = parse_statement(data, up.name)
        cache = {"hash": h, "parsed": parsed, "preview": preview_matches(parsed) if parsed.lines else {}}
        st.session_state["_bank_preview"] = cache
    parsed, prev = cache["parsed"], cache["preview"]
    for m in parsed.messages:
        st.warning(m)
    if not parsed.lines:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Period", f"{parsed.period_from:%b %d} – {parsed.period_to:%b %d, %Y}", border=True)
    c2.metric("Lines", f"{len(parsed.lines):,}", border=True)
    c3.metric("Closing balance", php(parsed.closing_balance) if parsed.closing_balance is not None else "—", border=True)
    c4.metric("Running balances", "All add up" if parsed.balance_ok else f"{parsed.bad_balance_steps} don't add up", border=True)
    st.caption(f"Account {parsed.masked_account() or '—'} · opening balance {php(parsed.opening_balance) if parsed.opening_balance is not None else '—'}")
    kinds = pd.Series([x.kind for x in parsed.lines]).value_counts()
    st.dataframe(pd.DataFrame({"What": [KIND_LABELS.get(k, k) for k in kinds.index], "Lines": kinds.values}), hide_index=True)
    if prev:
        st.markdown(f"Checks on this statement: **{prev['matched']} match our checks** (number and amount)"
                    + (f", {prev['amount_mismatch']} with a different amount" if prev["amount_mismatch"] else "")
                    + (f", **{prev['cancelled_check_cleared']} that we cancelled**" if prev["cancelled_check_cleared"] else "")
                    + (f", {prev['not_in_system']} not in the system" if prev["not_in_system"] else "") + ".")
    if st.button("Import this statement", icon=":material/upload_file:", type="primary", key="bank_import_btn"):
        if not guard_duplicate_submit("bank_import", h):
            st.warning("Duplicate click ignored — identical to the click just before it.")
            return
        with st.spinner("Importing and matching..."):
            res = import_statement(data, up.name)
        st.session_state.pop("_bank_preview", None)
        if res.get("already_imported"):
            st.info(res["messages"][0])
            return
        refresh(done=f"Imported {res['new_lines']} new line(s)" + (f" ({res['duplicate_lines']} already on file)" if res["duplicate_lines"] else "")
                + f": {res['matched']} check(s) confirmed cleared, {res['exceptions']} to look at.")


def _reconcile_tab() -> None:
    first, last = statement_coverage()
    if last is None:
        st.info("No bank statement imported yet. Use the Import tab.")
        return
    waiting = not_yet_cleared()
    exc = [e for e in exceptions() if not e["reviewed"]]
    serious = [e for e in exc if e["match_status"] != "not_in_system"]
    confirmed = q("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS t FROM checks_issued WHERE bank_cleared_date IS NOT NULL")
    m1, m2, m3 = st.columns(3)
    m1.metric("Confirmed cleared by the bank", f"{int(confirmed['n'][0])}", php(confirmed["t"][0]), delta_color="off", delta_arrow="off", border=True)
    m2.metric("Not yet cleared (past their date)", f"{len(waiting)}", php(sum(w["amount"] for w in waiting)), delta_color="off", delta_arrow="off",
              border=True, help=f"Our numbered checks dated on or before {last:%b %d, %Y} (the last imported bank date) that the bank has not cleared.")
    m3.metric("Need a look", f"{len(serious)}", border=True)
    st.caption(f"Bank lines on file: {first:%b %d, %Y} to {last:%b %d, %Y}.")

    section("Need a look")
    if not serious:
        st.caption("Nothing — every check the bank cleared matches ours.")
    else:
        df = pd.DataFrame(serious)
        df["what"] = df["match_status"].map(EXCEPTION_LABELS)
        st.dataframe(df.assign(posting_date=pd.to_datetime(df["posting_date"]))[
            ["what", "posting_date", "check_number", "description", "debit", "credit", "our_amount", "our_status", "creditor_name"]],
            hide_index=True, column_config={"what": "What", "posting_date": date_col("Bank date"), "check_number": "Check #", "description": "Bank description",
                                            "debit": money_col("Out"), "credit": money_col("In"), "our_amount": money_col("Our amount"),
                                            "our_status": "Our status", "creditor_name": "Payee"})
        opts = {e["txn_id"]: f"{e['posting_date']} · {EXCEPTION_LABELS[e['match_status']]} · {e['check_number'] or e['description']}" for e in serious}
        r1, r2, r3 = st.columns([3, 3, 1.4], vertical_alignment="bottom")
        pick = r1.selectbox("Mark as looked at", list(opts), format_func=opts.get, key="bank_review_pick")
        note = r2.text_input("Note (optional)", key="bank_review_note", placeholder="e.g. bank error, corrected with Metrobank")
        if r3.button("Done", icon=":material/check:", key="bank_review_btn", width="stretch"):
            mark_reviewed(int(pick), note)
            refresh(done="Marked as looked at.")
    older = [e for e in exc if e["match_status"] == "not_in_system"]
    if older:
        with st.expander(f"{len(older)} cleared check(s) not in the system"):
            st.caption("Usually checks written before this system was used (it starts Sep 20, 2026) — nothing to do. If a recent one appears here, "
                       "it may be a check that was never recorded.")
            st.dataframe(pd.DataFrame(older)[["posting_date", "check_number", "debit"]], hide_index=True,
                         column_config={"posting_date": "Bank date", "check_number": "Check #", "debit": money_col("Amount")})

    section("Checks not yet cleared", help="Checks we issued, dated on or before the last imported bank date, that the bank has not cleared yet.")
    if not waiting:
        st.caption("None — every check dated within the imported period has cleared.")
    else:
        w = pd.DataFrame(waiting)
        st.dataframe(w.assign(check_date=pd.to_datetime(w["check_date"]))[["check_number", "check_date", "days_waiting", "creditor_name", "reference", "amount"]],
                     hide_index=True, column_config={"check_number": "Check #", "check_date": date_col("Check date"), "days_waiting": "Days waiting",
                                                     "creditor_name": "Payee", "reference": "Batch / loan note", "amount": money_col("Amount")})


def _stale_note(pos) -> None:
    age = (pd.Timestamp.today().date() - pos["as_of"]).days
    if age > 14:
        st.warning(f":material/history: The latest bank balance on file is from **{pos['as_of']:%b %d, %Y}** ({age} days ago). Everything below "
                   "starts from that balance, so it is only as good as the statement: import a recent statement from MBOS for real figures.")


def _forecast_tab() -> None:
    pos = cash_position()
    if pos is None:
        st.info("Import a bank statement first — the forecast starts from the bank's latest balance.")
        return
    _stale_note(pos)
    f1, f2 = st.columns([1, 1])
    days = f1.segmented_control("Look ahead", [14, 30, 60, 90], default=30, required=True, format_func=lambda d: f"{d} days", key="cash_fc_days")
    buf = f2.number_input("Cash buffer (the lowest balance you want to keep)", min_value=0.0, step=50000.0, value=get_buffer(), key="cash_buffer_input",
                          help="Days when the projected balance falls under this line are flagged.")
    if abs(buf - get_buffer()) > 0.005:
        set_buffer(buf)
    fc = forecast(int(days))
    d = fc["daily"]
    m1, m2, m3 = st.columns(3)
    m1.metric(f"Bank balance ({fc['as_of']:%b %d})", php(fc["opening"]), border=True)
    m2.metric("Lowest projected", php(fc["lowest"][1]), f"on {fc['lowest'][0]:%b %d}", delta_color="off", delta_arrow="off", border=True)
    m3.metric("First day under the buffer", f"{fc['first_below']:%b %d}" if fc["first_below"] else "None", border=True)
    chart_df = d.assign(day=pd.to_datetime(d["day"]))
    line = alt.Chart(chart_df).mark_line(point=True).encode(x=alt.X("day:T", title=None), y=alt.Y("balance:Q", title="Projected balance (PHP)"),
                                                            tooltip=[alt.Tooltip("day:T", title="Day"), alt.Tooltip("balance:Q", title="Balance", format=",.2f"),
                                                                     alt.Tooltip("money_in:Q", title="Expected in", format=",.2f"),
                                                                     alt.Tooltip("money_out:Q", title="Going out", format=",.2f")])
    rule = alt.Chart(pd.DataFrame({"y": [fc["buffer"]]})).mark_rule(color="#C62828", strokeDash=[6, 4]).encode(y="y:Q")
    st.altair_chart(line + rule, width="stretch")
    st.caption("How it is worked out: the bank's latest balance + expected money in (your average deposits for that weekday over recent weeks) "
               "- money going out (checks the bank has not cleared yet, on their check date — or tomorrow if already past; bank-loan and credit-card "
               "dues). Weekend dues move to Monday. It is an estimate to plan with, not a promise.")
    busy = d[d["money_out"] > 0].sort_values("money_out", ascending=False).head(10)
    if not busy.empty:
        st.markdown("**Heaviest days going out**")
        st.dataframe(busy.assign(day=pd.to_datetime(busy["day"]))[["day", "money_out", "money_in", "balance"]], hide_index=True,
                     column_config={"day": date_col("Day"), "money_out": money_col("Going out"), "money_in": money_col("Expected in"), "balance": money_col("Balance after")})


def _float_tab() -> None:
    fs = float_summary()
    if not fs:
        st.info("Import a bank statement first.")
        return
    overdue, ahead = fs["overdue"], fs["ahead"]
    m1, m2 = st.columns(2)
    m1.metric(f"Past their date, not cleared (to {fs['as_of']:%b %d})", php(float(overdue["amount"].sum()) if not overdue.empty else 0.0),
              f"{len(overdue)} check(s)", delta_color="off", delta_arrow="off", border=True,
              help="Checks dated on or before the last imported bank date that the bank has not cleared: suppliers have not deposited them yet.")
    m2.metric("Dated ahead (not due yet)", php(float(ahead["amount"].sum()) if not ahead.empty else 0.0), f"{len(ahead)} check(s)",
              delta_color="off", delta_arrow="off", border=True)
    if overdue.empty:
        st.caption("No check is waiting to be deposited within the imported period.")
        return
    st.dataframe(fs["buckets"], hide_index=True, column_config={"age": "Waiting", "checks": "Checks", "amount": money_col("Amount")})
    st.markdown("**By payee**")
    st.dataframe(fs["by_payee"], hide_index=True, column_config={"payee": "Payee", "checks": "Checks", "amount": money_col("Amount")})


def _inflow_tab() -> None:
    it = inflow_trends()
    if not it:
        st.info("Import a bank statement first — money in comes from its deposit lines.")
        return
    m1, m2, m3 = st.columns(3)
    m1.metric("Money in (imported period)", php(it["total"]), border=True)
    m2.metric("Average per week", php(it["weekly_average"]), border=True)
    m3.metric("Slow weeks", f"{int(it['weekly']['slow'].sum())} of {int((~it['weekly']['partial']).sum())}", border=True,
              help="Complete weeks under 75% of the weekly average.")
    wk = it["weekly"]
    wk = wk.assign(shade=["Slow week" if s else "Partial week" if p else "Week" for s, p in zip(wk["slow"], wk["partial"])])
    bars = alt.Chart(wk).mark_bar().encode(
        x=alt.X("label:N", title="Week ending", sort=list(wk["label"]), axis=alt.Axis(labelAngle=0)), y=alt.Y("money_in:Q", title="Money in (PHP)"),
        color=alt.Color("shade:N", legend=None, scale=alt.Scale(domain=["Week", "Slow week", "Partial week"], range=["#015088", "#C26A00", "#9FB3C8"])),
        tooltip=[alt.Tooltip("label:N", title="Week ending"), alt.Tooltip("money_in:Q", title="Money in", format=",.2f")])
    avg = alt.Chart(pd.DataFrame({"y": [it["weekly_average"]]})).mark_rule(strokeDash=[6, 4]).encode(y="y:Q")
    st.altair_chart(bars + avg, width="stretch")
    st.caption("Orange = a slow week (under 75% of the average). Light blue = a week only partly covered by the imported statements (not counted "
               "in the average). Dashed line = weekly average.")
    st.markdown("**By type**")
    st.dataframe(it["by_type"], hide_index=True, column_config={"type": "Type", "lines": "Lines", "amount": money_col("Amount")})


def _unusual_tab() -> None:
    u = unusual_items()
    if not u:
        st.info("Import a bank statement first.")
        return
    if not u["returned"].empty:
        st.error(f":material/report: **{len(u['returned'])} deposited check(s) were returned (bounced)** — follow up with the customer(s).")
        st.dataframe(u["returned"][["posting_date", "description", "debit"]], hide_index=True,
                     column_config={"posting_date": "Bank date", "description": "Bank description", "debit": money_col("Amount")})
    if not u["mismatch"].empty:
        st.warning(f"{len(u['mismatch'])} cleared check(s) differ from our records — see Reconciliation.")
    st.markdown("**Bank debits that are not our checks** (loan debits, bills payments, charges)")
    if u["debits"].empty:
        st.caption("None.")
    else:
        st.dataframe(u["debits_by_month"].reset_index(), hide_index=True)
        st.dataframe(u["debits"][["posting_date", "description", "debit"]], hide_index=True,
                     column_config={"posting_date": "Bank date", "description": "Bank description", "debit": money_col("Amount")})
    st.markdown("**Much larger than usual for their type**")
    if u["big"].empty:
        st.caption("None.")
    else:
        st.dataframe(u["big"][["posting_date", "description", "group", "debit", "credit"]].head(20), hide_index=True,
                     column_config={"posting_date": "Bank date", "description": "Bank description", "group": "Type",
                                    "debit": money_col("Out"), "credit": money_col("In")})


def _working_capital_tab() -> None:
    w = working_capital()
    if not w:
        st.info("Import a bank statement first — this compares the bank balance with what you owe.")
        return
    _stale_note({"as_of": w["as_of"]})
    m1, m2, m3 = st.columns(3)
    m1.metric(f"Bank balance ({w['as_of']:%b %d})", php(w["balance"]), border=True)
    m2.metric("Days of cash", f"{w['days_of_cash']:.1f}" if w["days_of_cash"] is not None else "—", border=True,
              help="How many days the bank balance would last at the average daily payments of the next 30 days, with no money coming in.")
    m3.metric("Supplier payment days (DPO)", f"{w['dpo']:.0f}" if w["dpo"] is not None else "—", border=True,
              help="Average days between an invoice's due date and the check that paid it — same figure as the Dashboard.")
    st.dataframe(w["windows"], hide_index=True, column_config={
        "window": "Due within", "due": money_col("Payables due"), "cover": st.column_config.NumberColumn("Cash cover", format="%.2fx",
        help="Bank balance ÷ payables due. Under 1.0x means money coming in is needed to pay them."), "left": money_col("Cash left after")})
    if w["by_track"]:
        st.caption("Due in the next 90 days by type: " + " · ".join(f"{k} {php(v)}" for k, v in sorted(w["by_track"].items(), key=lambda kv: -kv[1])))
    st.caption("Receivables and inventory are not in the system yet, so this is cash against payables only (your choice for now).")


def render() -> None:
    page_header("Bank and cash", "Drop the MBOS bank statement here: the app matches your checks and shows only what needs a look.")
    li = last_import()
    if li:
        st.caption(f"Last statement imported {str(li['imported_at'])[:16]} · covers {li['period_from']} to {li['period_to']}.")
    tabs = st.tabs([":material/upload_file: Import a statement", ":material/rule: Reconciliation", ":material/trending_up: Cash forecast",
                    ":material/hourglass_top: Checks not yet cleared", ":material/savings: Money in", ":material/report: Unusual items",
                    ":material/balance: Working capital"], key="bank_cash_tab", on_change="rerun")
    for tab, fn in zip(tabs, (_import_tab, _reconcile_tab, _forecast_tab, _float_tab, _inflow_tab, _unusual_tab, _working_capital_tab)):
        if tab.open:                                      # only the open tab is worked out
            with tab:
                fn()
