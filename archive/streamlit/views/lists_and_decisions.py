"""Two entries of the Reports page (plan F + G, 2026-10-01): LISTS of the records themselves (who are our suppliers, lenders, banks ...) and DECISION reports (each answers one
question with a plain 'what this says'). The data comes from engines/lists_service.py and engines/decision_reports_service.py."""
from __future__ import annotations

from engines.clock import manila_today
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from engines import decision_reports_service as dr
from engines import lists_service as ls
from engines.analytics_flow_service import PERIODS, cash_ahead_weeks, debt_due_by_month, period_range, supplier_spend
from views.shared import export_buttons, show_table


def render_lists() -> None:
    st.subheader("Lists — suppliers, lenders, banks, batches and more")
    st.caption("A list of the records themselves (not transactions). Pick a list; search it; download it. One button downloads every list in one Excel workbook.")
    keys = ls.list_keys()
    key = st.selectbox("List", keys, format_func=lambda k: ls.LISTS[k]["title"], key="lst_pick")
    spec = ls.LISTS[key]
    st.caption(spec["help"])
    c1, c2 = st.columns([1, 2])
    active_only = c1.checkbox("Active only", value=True, key=f"lst_active_{key}", disabled=not spec["active_col"]) if spec["active_col"] else False
    search = c2.text_input("Search", key=f"lst_search_{key}", placeholder="type part of any name, number or word")
    df = ls.friendly_list(key, active_only)
    if search.strip() and not df.empty:
        needle = search.strip().lower()
        df = df[df.apply(lambda r: needle in " ".join(str(v) for v in r.values).lower(), axis=1)]
    st.metric("Rows", len(df), border=True)
    if df.empty:
        st.caption("Nothing in this list.")
    else:
        kinds = {spec["labels"][c]: k for c, k in ls.kinds(key).items()}
        show_table(df, kinds=kinds)
        export_buttons(df, f"list_{key}", f"lst_dl_{key}", kinds=kinds, what=spec["title"].lower())
    st.divider()
    sheets, fmt = ls.export_workbook(active_only=False)
    st.markdown("**Download all lists**")
    export_buttons(sheets, "all_lists", "lst_dl_all", kinds=fmt, what="all lists")


DECISIONS = {
    "spend": "Supplier spend ranking — who do we buy from most?",
    "perf": "Supplier payment performance — are we paying late?",
    "owed": "What is still owed after each month end",
    "outflow": "Monthly outflow summary — where did the money go?",
    "cost": "Cost of borrowing — which debt is most expensive?",
    "ladder": "Debt falling due by month — when is the heavy month?",
    "cards": "Credit card usage — who uses the cards, what interest?",
    "cash": "Cash needs vs available (13 weeks) — can we cover the weeks?",
    "family": "Business vs family — how much debt is for whom?",
    "memos": "Credit memos by supplier — who has the most returns?",
}


def _says(text: str) -> None:
    st.info(text, icon=":material/lightbulb:")


def _period(key: str, default: str = "Last 3 months") -> tuple[date, date]:
    choice = st.selectbox("Period", PERIODS, index=PERIODS.index(default), key=key)
    return period_range(choice)


def render_decisions() -> None:
    st.subheader("Decision reports — answers for choices")
    with st.expander("If you are asked ... open ... (question guide)"):
        st.table(pd.DataFrame(dr.QUESTION_GUIDE, columns=["If you are asked", "Open"]))
    pick = st.selectbox("Report", list(DECISIONS), format_func=DECISIONS.get, key="dec_pick")
    today = manila_today()
    if pick == "spend":
        start, end = _period("dec_spend_period")
        out = supplier_spend(start, min(end, today))
        if out.empty:
            st.caption("No supplier purchases in this period (the migrated history is a beginning balance, not purchases).")
            return
        top = out.iloc[0]
        _says(f"{top['supplier']} is the biggest: {top['share_pct']:.0f}% of purchases in the period.")
        labels = {"supplier": "Supplier", "spend": "Purchases", "share_pct": "Share %", "previous": "Previous period", "change_pct": "Change %", "invoices": "Invoices"}
        kinds = {"spend": "money", "previous": "money"}
        show_table(out, labels, kinds=kinds)
        export_buttons(out, "supplier_spend", "dec_dl_spend", labels, kinds=kinds)
    elif pick == "perf":
        start, end = _period("dec_perf_period")
        r = dr.supplier_payment_performance(start, min(end, today))
        _says(r["says"])
        labels = {"supplier": "Supplier", "paid": "Paid", "within_terms_pct": "Paid on time %", "late_amount": "Paid late", "avg_days_late": "Average days late"}
        kinds = {"paid": "money", "late_amount": "money"}
        show_table(r["table"], labels, kinds=kinds)
        export_buttons(r["table"], "payment_performance", "dec_dl_perf", labels, kinds=kinds)
    elif pick == "owed":
        r = dr.payables_after_month_end(12)
        _says(r["says"])
        t = r["table"].assign(month_label=lambda d: [f"{m:%b %Y}" for m in d["month_end"]])
        order = list(dict.fromkeys(t["month_label"]))
        st.altair_chart(alt.Chart(t).mark_area().encode(x=alt.X("month_label:N", title="At the end of", sort=order), y=alt.Y("still_to_pay:Q", title="Still to pay (PHP)"),
                                                        color=alt.Color("track:N", title="Kind of payee")).properties(height=320), width="stretch")
        labels = {"month_end": "Month end", "track": "Kind of payee", "still_to_pay": "Still to pay"}
        export_buttons(r["table"], "owed_after_month_end", "dec_dl_owed", labels, kinds={"month_end": "date", "still_to_pay": "money"})
    elif pick == "outflow":
        first = manila_today().replace(day=1)
        months = [(first - timedelta(days=31 * i)).replace(day=1) for i in range(0, 13)]
        month = st.selectbox("Month", list(dict.fromkeys(months)), format_func=lambda d: f"{d:%B %Y}", key="dec_outflow_month")
        r = dr.monthly_outflow(month)
        _says(r["says"])
        labels = {"track": "Kind of payee", "this_month": "This month", "last_month": "Last month", "same_month_last_year": "Same month last year", "change_pct": "Change vs last month %"}
        kinds = {"this_month": "money", "last_month": "money", "same_month_last_year": "money"}
        show_table(r["table"], labels, kinds=kinds)
        st.markdown("**Top 10 payees**")
        show_table(r["top"], {"payee_name": "Payee", "amount": "Paid"}, kinds={"amount": "money"})
        export_buttons({"By kind": r["table"], "Top payees": r["top"]}, "monthly_outflow", "dec_dl_outflow", labels | {"payee_name": "Payee", "amount": "Paid"}, kinds=kinds | {"amount": "money"})
    elif pick == "cost":
        r = dr.cost_of_borrowing()
        _says(r["says"])
        labels = {"source": "Source", "name": "Lender / bank", "reference": "Reference", "monthly_rate": "Monthly rate %", "interest_paid": "Interest paid", "interest_to_pay": "Interest to pay",
                  "interest_total": "Total interest"}
        kinds = {"interest_paid": "money", "interest_to_pay": "money", "interest_total": "money"}
        show_table(r["table"], labels, kinds=kinds)
        export_buttons(r["table"], "cost_of_borrowing", "dec_dl_cost", labels, kinds=kinds)
    elif pick == "ladder":
        d = debt_due_by_month(12)
        if d.empty:
            st.caption("No debt falls due in the next 12 months.")
            return
        worst = d.groupby("month")["amount"].sum().idxmax()
        _says(f"The heaviest month is {worst:%B %Y}: {dr.php(float(d.groupby('month')['amount'].sum().max()))} falls due.")
        d = d.assign(month_label=[f"{m:%b %Y}" for m in d["month"]])
        order = list(dict.fromkeys(d.sort_values("month")["month_label"]))
        st.altair_chart(alt.Chart(d).mark_bar().encode(x=alt.X("month_label:N", title=None, sort=order), y=alt.Y("amount:Q", title="PHP"), color=alt.Color("source:N", title=None),
                                                       tooltip=["month_label", "source", alt.Tooltip("amount:Q", format=",.2f")]).properties(height=320), width="stretch")
        export_buttons(d[["month", "source", "amount"]], "debt_due_by_month", "dec_dl_ladder", {"month": "Month", "source": "Source", "amount": "Amount"}, kinds={"month": "date", "amount": "money"})
    elif pick == "cards":
        r = dr.card_usage()
        _says(r["says"])
        labels = {"issuer": "Issuer", "card_name": "Card", "last4": "Last 4", "cardholder": "Cardholder", "credit_limit": "Limit", "charges": "Charges (12 months)",
                  "interest": "Interest (12 months)", "latest_due": "Latest amount due", "utilization_pct": "Used of limit %"}
        kinds = {"credit_limit": "money", "charges": "money", "interest": "money", "latest_due": "money"}
        show_table(r["table"], labels, kinds=kinds)
        export_buttons(r["table"], "card_usage", "dec_dl_cards", labels, kinds=kinds)
    elif pick == "cash":
        w = cash_ahead_weeks(13)
        if w.empty:
            st.info("No recent bank statement is on file, so there is no forecast. Import the latest Metrobank statement on Bank and cash.")
            return
        low = w.loc[w["lowest_balance"].idxmin()]
        _says(f"The lowest balance in the next 13 weeks is {dr.php(float(low['lowest_balance']))} in the week of {low['week']:%b %d}. {int(w['short'].sum())} week(s) end under your cash buffer.")
        labels = {"week": "Week starting", "money_in": "Expected in", "money_out": "Going out", "end_balance": "Balance at week end", "lowest_balance": "Lowest that week", "short": "Under buffer"}
        kinds = {"week": "date", "money_in": "money", "money_out": "money", "end_balance": "money", "lowest_balance": "money"}
        show_table(w, labels, kinds=kinds)
        export_buttons(w, "cash_13_weeks", "dec_dl_cash", labels, kinds=kinds)
    elif pick == "family":
        r = dr.business_vs_family()
        _says(r["says"])
        labels = {"for_whom": "For", "type": "Type", "loans": "Bank loans", "still_to_pay": "Still to pay", "interest": "Interest"}
        kinds = {"still_to_pay": "money", "interest": "money"}
        show_table(r["table"], labels, kinds=kinds)
        export_buttons(r["table"], "business_vs_family", "dec_dl_family", labels, kinds=kinds)
    else:
        start, end = _period("dec_memo_period", "Last 12 months")
        r = dr.memos_by_supplier(start, min(end, today))
        _says(r["says"])
        labels = {"supplier": "Supplier", "memos": "Credit memos", "memo_total": "Memo total", "purchases": "Purchases", "memo_pct": "Memos as % of purchases"}
        kinds = {"memo_total": "money", "purchases": "money"}
        show_table(r["table"], labels, kinds=kinds)
        export_buttons(r["table"], "credit_memos_by_supplier", "dec_dl_memos", labels, kinds=kinds)
