"""Payables due (2026-10-02): what is already COMMITTED to go out, and when. No forecast and no guess about deposits: the suppliers deposit the checks they hold
when they like, so what matters is how much can hit the bank, and when. Engine: engines/payables_due_service.py."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from engines.cash_insights_service import float_summary
from engines.common import php
from engines.payables_due_service import COLUMNS, by_supplier, by_week, next_days
from views.shared import MONEY_FORMAT, date_col, page_header, section

KIND_COLORS = ["#16305B", "#4C78A8", "#C26A00", "#9FB3D1", "#2E7D32"]


def _top() -> None:
    a, b = st.columns(2)
    w, m = next_days(7), next_days(30)
    a.metric("Can hit the bank in the next 7 days", php(w["amount"]), f"{w['checks']} check(s), {w['orders']} order(s)", delta_color="off", delta_arrow="off", border=True,
             help="Checks the suppliers hold that can be deposited (post-dated checks are counted on their date; checks already past their date count today), loan and card dues "
                  "from entered statements, and ordered purchase orders on the day they fall due.")
    b.metric("In the next 30 days", php(m["amount"]), f"{m['checks']} check(s), {m['orders']} order(s)", delta_color="off", delta_arrow="off", border=True)
    if w["balance"] is not None:
        st.caption(f"Bank balance on {w['balance_as_of']:%b %d, %Y}: {php(w['balance'])}. Shown beside the figures, not compared with them: the suppliers decide when they deposit.")
    else:
        st.caption("No bank statement imported yet, so no balance is shown.")


def _next_seven_days() -> None:
    """The next 7 days, payment by payment (moved from the Dashboard)."""
    from datetime import timedelta
    from engines.clock import manila_today
    from engines.reports_service import get_payment_calendar_items
    items = get_payment_calendar_items()
    if not items.empty:
        day = pd.to_datetime(items["actual_payment_date"]).dt.date
        items = items[(items["state"] == "Due") & (day >= manila_today()) & (day <= manila_today() + timedelta(days=6))]
    section("Next 7 days")
    if items.empty:
        st.caption("Nothing falls due in the next 7 days.")
        return
    st.dataframe(items[["actual_payment_date", "track", "payee_name", "reference", "amount"]], hide_index=True, width="stretch",
                 column_config={"actual_payment_date": st.column_config.DateColumn("Day", format="ddd, MMM DD"), "track": "Track", "payee_name": "Payee",
                                "reference": "Reference", "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})


def _weeks() -> None:
    _next_seven_days()
    weeks = by_week(12)
    st.caption("Week by week for the next 12 weeks. A check already past its date counts in the first week, because the supplier can deposit it any day.")
    chart = weeks.melt(id_vars=["week"], value_vars=COLUMNS, var_name="kind", value_name="amount")
    chart = chart.assign(week=pd.to_datetime(chart["week"]))
    st.altair_chart(alt.Chart(chart).mark_bar().encode(
        x=alt.X("week:T", title="Week of"), y=alt.Y("sum(amount):Q", title="Going out (PHP)"),
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=COLUMNS, range=KIND_COLORS)),
        tooltip=[alt.Tooltip("week:T", title="Week of"), alt.Tooltip("kind:N", title=""), alt.Tooltip("sum(amount):Q", title="Amount", format=",.2f")]),
        width="stretch")
    st.dataframe(weeks, hide_index=True, width="stretch",
                 column_config={"week": date_col("Week of"), **{c: st.column_config.NumberColumn(c, format=MONEY_FORMAT) for c in COLUMNS + ["Total"]}})


def _suppliers() -> None:
    df = by_supplier(30)
    st.caption("Who holds checks that can be deposited in the next 30 days, and which ordered purchase orders are coming due. Biggest first.")
    if df.empty:
        st.info("Nothing is committed to suppliers in the next 30 days.")
        return
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"payee": "Supplier", "checks": st.column_config.NumberColumn("Checks", format="%d"),
                                "check_amount": st.column_config.NumberColumn("Checks amount", format=MONEY_FORMAT),
                                "orders": st.column_config.NumberColumn("Orders", format="%d"),
                                "order_amount": st.column_config.NumberColumn("Orders amount", format=MONEY_FORMAT),
                                "total": st.column_config.NumberColumn("Total", format=MONEY_FORMAT), "next_day": date_col("First date")})


def _past() -> None:
    fs = float_summary()
    if not fs:
        st.info("Import a bank statement first (Bank and cash): the checks past their date are the ones the bank has not cleared yet.")
        return
    overdue = fs["overdue"]
    st.caption(f"Checks dated on or before the last imported bank date ({fs['as_of']:%b %d}) that the bank has not cleared: the suppliers have not deposited them yet, so they can hit the bank any day.")
    if overdue.empty:
        st.success("No check is waiting to be deposited within the imported period.")
        return
    st.metric("Past their date, not cleared", php(float(overdue["amount"].sum())), f"{len(overdue)} check(s)", delta_color="off", delta_arrow="off", border=True)
    st.dataframe(fs["by_payee"], hide_index=True, width="stretch",
                 column_config={"payee": "Supplier", "checks": "Checks", "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})


def render() -> None:
    page_header("Payables due", "What is already committed to go out, and when. The suppliers deposit the checks they hold when they like, so this shows how much can hit the bank, not a plan.")
    _top()
    tab_weeks, tab_suppliers, tab_past, tab_cal = st.tabs(["Week by week", "By supplier", "Past their date", "Calendar"], key="pd_tab", default="Week by week", on_change="rerun")
    with tab_weeks:
        _weeks()
    with tab_suppliers:
        _suppliers()
    with tab_past:
        _past()
    with tab_cal:
        from views.payment_calendar import render as render_calendar
        render_calendar("pcal")
