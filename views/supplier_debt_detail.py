"""Supplier and debt detail (aging, payment health, top suppliers, payables trend, interest tracker). It used to sit in a collapsed box at the bottom of the
Dashboard; the Dashboard is now "today" only (plan H, 2026-10-01), so it lives on the Analytics page. Moved unchanged."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from datetime import date
from engines.common import php
from engines.dashboard_kpi_service import get_average_dpo_suppliers
from engines.dashboard_kpi_service import get_overdue_cancelled_checks
from engines.dashboard_kpi_service import get_payables_trend
from engines.dashboard_kpi_service import get_top_suppliers
from engines.interest_service import get_monthly_interest_summary
from engines.interest_service import get_yearly_interest_trend
from engines.reports_service import get_supplier_ap_aging_report
from views.shared import MONEY_FORMAT


def render_supplier_debt_detail() -> None:
    st.subheader("Supplier AP aging")
    aging_df = get_supplier_ap_aging_report()
    if aging_df.empty:
        st.caption("No open supplier invoices.")
    else:
        bucket_totals = pd.DataFrame(
            {
                "bucket": ["Current", "1-30 days", "31-60 days", "60+ days"],
                "amount": [
                    aging_df["current_due"].sum(),
                    aging_df["days_1_30"].sum(),
                    aging_df["days_31_60"].sum(),
                    aging_df["days_over_60"].sum(),
                ],
            }
        )
        st.bar_chart(bucket_totals, x="bucket", y="amount", x_label="Aging bucket", y_label="Amount (PHP)")

    st.divider()
    st.subheader("Supplier payment health")
    st.caption("Lag is measured from a batch's invoice-amount-weighted due date to the check's own date — the closest thing to a per-check due date, since one check pays a batch's net total rather than a single invoice.")
    overdue_threshold = st.number_input(
        "Flag a cancelled/replaced check as \"long overdue\" past this many days", min_value=1, step=5,
        value=int(st.session_state.get("overdue_threshold", 30)), key="overdue_threshold",
        help="A cancelled or replaced check is flagged 'long overdue' when it was voided more than this many days after the due date of what it was paying.",
    )
    avg_dpo = get_average_dpo_suppliers()
    overdue_cancelled_df = get_overdue_cancelled_checks(int(overdue_threshold))
    dpo_col, overdue_col = st.columns(2)
    dpo_col.metric(
        "Avg Days Payable Outstanding (Suppliers)",
        f"{avg_dpo:.1f} days" if avg_dpo is not None else "—",
        border=True,
        help="Average of (check date − batch's weighted invoice due date) across cleared supplier checks. Positive = paid later than due; negative = paid early.",
    )
    overdue_col.metric(
        f"Checks cancelled while {int(overdue_threshold)}+ days overdue",
        str(len(overdue_cancelled_df)),
        border=True,
        help="No bank-return/NSF tracking exists in this system — this counts cancelled or replaced checks whose own date was already well past the batch's due date, as a proxy for \"check returns due to being long overdue.\"",
    )
    if not overdue_cancelled_df.empty:
        st.dataframe(
            overdue_cancelled_df[["check_number", "creditor_name", "check_date", "amount", "status", "lag_days"]],
            hide_index=True,
            column_config={
                "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT),
                "lag_days": st.column_config.NumberColumn("Days overdue when voided", format="%.0f"),
            },
        )

    st.divider()
    kpi_col_left, kpi_col_right = st.columns(2)
    with kpi_col_left:
        st.subheader("Top suppliers")
        supplier_metric = st.segmented_control(
            "Rank by", options=["outstanding", "paid"],
            format_func=lambda m: "Outstanding payable" if m == "outstanding" else "Total paid (cleared, all-time)",
            default="outstanding", key="top_supplier_metric",
        )
        top_suppliers_df = get_top_suppliers(supplier_metric or "outstanding", limit=10)
        if top_suppliers_df.empty:
            st.caption("No supplier data yet for this metric.")
        else:
            st.bar_chart(
                top_suppliers_df, x="supplier_name", y="value", horizontal=True,
                x_label="Amount (PHP)", y_label="Supplier",
            )
    with kpi_col_right:
        st.subheader("Total payables trend")
        trend_granularity = st.segmented_control(
            "Group by", options=["month", "quarter", "year"],
            format_func=lambda g: g.title(), default="month", key="payables_trend_granularity",
        )
        trend_df = get_payables_trend(trend_granularity or "month")
        if trend_df.empty:
            st.caption("No payables data yet.")
        else:
            # Friendly period labels ("Sep 2026") aren't alphabetically sortable the way
            # "2026-09" was, so the chronological order has to be passed explicitly rather
            # than relying on sort=None -- trend_df is already in period order (built via
            # get_payables_trend()'s own sort-by-key-then-label step), so this list is safe
            # to use as-is.
            period_order = list(trend_df["period"])

            bars = alt.Chart(trend_df).mark_bar(color="#4C78A8").encode(
                x=alt.X("period:N", title="Period", sort=period_order),
                y=alt.Y("total_amount:Q", title="Total payables (PHP)"),
                tooltip=["period", alt.Tooltip("total_amount:Q", title="Total", format=",.2f")],
            )
            trend_line = alt.Chart(trend_df).mark_line(color="#E45756", point=True).encode(
                x=alt.X("period:N", sort=period_order),
                y=alt.Y("moving_avg:Q"),
                tooltip=["period", alt.Tooltip("moving_avg:Q", title="4-period avg", format=",.2f")],
            )
            st.altair_chart((bars + trend_line).properties(height=320), width="stretch")
            # 4 periods only lines up with the business's 120-day longest credit term at
            # Month granularity (4 months ~= 120 days) -- at Quarter/Year it's still a
            # 4-period trailing average, just no longer a ~120-day one (120 days is under a
            # single quarter/year bucket), so the "~120-day" framing is only claimed here.
            sma_caption = "4-period (~120-day) trailing moving average" if trend_granularity == "month" else "4-period trailing moving average"
            st.caption(f"Bars: total payables per period. Orange line: {sma_caption}.")

    st.divider()
    st.subheader("Debt interest tracker")
    st.caption(
        "Interest across all three debt sources — Private Loans (already tracked in full), Bank Loans "
        "(only where an installment's interest portion is filled in), and Credit Cards (only from statements "
        "entered with the interest breakdown). A source with nothing recorded yet just shows ₱0.00, not an error."
    )
    interest_today = date.today()
    interest_month_key = "interest_dashboard_month"
    interest_month = st.selectbox(
        "Month", options=list(range(1, 13)), index=interest_today.month - 1,
        format_func=lambda m: f"{date(2000, m, 1).strftime('%B')} {interest_today.year}", key=interest_month_key,
    )
    interest_summary = get_monthly_interest_summary(interest_today.year, interest_month)

    st.metric(f"Total interest paid ({interest_summary['period']})", php(interest_summary["total_interest_paid"]), help="Sum across all three debt sources.")

    ic1, ic2, ic3 = st.columns(3)
    with ic1:
        st.markdown("**Private loans**")
        pl = interest_summary["private_loans"]
        if pl["count"] == 0:
            st.caption("No interest this month.")
        else:
            for name, amt in pl.items():
                if name not in ("subtotal", "count"):
                    st.write(f"{name}: **{php(amt)}**")
        st.metric("Subtotal", php(pl["subtotal"]), border=True)
    with ic2:
        st.markdown("**Bank loans**")
        bl_int = interest_summary["bank_loans"]
        if bl_int["count"] == 0:
            st.caption("No interest recorded this month.")
        else:
            for name, amt in bl_int.items():
                if name not in ("subtotal", "count"):
                    st.write(f"{name}: **{php(amt)}**")
        st.metric("Subtotal", php(bl_int["subtotal"]), border=True)
    with ic3:
        st.markdown("**Credit cards**")
        cc_int = interest_summary["credit_cards"]
        if cc_int["count"] == 0:
            st.caption("No interest recorded this month.")
        else:
            for name, amt in cc_int.items():
                if name not in ("subtotal", "count"):
                    st.write(f"{name}: **{php(amt)}**")
        st.metric("Subtotal", php(cc_int["subtotal"]), border=True)

    st.divider()
    st.markdown("**Interest trend (last 6 months)**")
    interest_trend_df = get_yearly_interest_trend(interest_today.year).tail(6)
    if interest_trend_df.empty or interest_trend_df["total_interest"].sum() == 0:
        st.caption("Not enough data for a trend yet.")
    else:
        interest_chart_df = interest_trend_df.set_index("month_name")[
            ["private_loans_interest", "bank_loans_interest", "credit_cards_interest", "total_interest"]
        ]
        st.line_chart(interest_chart_df, height=300)
        itc1, itc2, itc3 = st.columns(3)
        itc1.metric("6-month average", php(interest_trend_df["total_interest"].mean()))
        itc2.metric("Peak month", php(interest_trend_df["total_interest"].max()))
        itc3.metric("YTD total", php(interest_trend_df["total_interest"].sum()))
