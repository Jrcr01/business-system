"""Analytics screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from datetime import date
from engines.analytics_service import get_all_tracks_pareto
from engines.analytics_service import get_ap_composition_by_track
from engines.analytics_service import get_ap_growth_rate
from engines.analytics_service import get_bank_loan_pareto
from engines.analytics_service import get_check_aging_distribution
from engines.analytics_service import get_check_status_breakdown
from engines.analytics_service import get_credit_card_pareto
from engines.analytics_service import get_debt_cost_comparison
from engines.analytics_service import get_lender_pareto
from engines.analytics_service import get_missing_debt_terms
from engines.analytics_service import get_supplier_pareto
from engines.common import php
from engines.dashboard_kpi_service import get_average_dpo_suppliers
from engines.dashboard_kpi_service import get_overdue_cancelled_checks
from engines.dashboard_kpi_service import get_supplier_concentration

from views.shared import (  # noqa: F401
    page_header,
    section,
    status_badge,
    status_text,
    money_col,
    date_col,
    MONEY_FORMAT,
)


def render() -> None:
    page_header("Analytics")
    st.caption(
        "AP composition, payment behavior, and cost-of-debt analysis. This system tracks the AP side only "
        "(no revenue, general ledger, or balance sheet), so ratios like current ratio or DSO aren't offered here "
        "-- everything below is grounded in checks, loans, and credit cards actually on file."
    )

    analytics_composition_tab, analytics_payment_tab, analytics_debt_tab = st.tabs([
        ":material/stacked_bar_chart: AP Composition & Trend",
        ":material/speed: Payment Behavior",
        ":material/percent: Cost of Debt",
    ])

    _TRACK_COLORS = alt.Scale(domain=["Supplier", "Lender", "Bank Loan", "Credit Card"], scheme="tableau10")

    with analytics_composition_tab:
        an_filter_col1, an_filter_col2 = st.columns(2)
        with an_filter_col1:
            an_granularity = st.segmented_control(
                "Group by", options=["month", "quarter", "year"],
                format_func=lambda g: g.title(), default="month", key="analytics_granularity",
            )
            an_granularity = an_granularity or "month"
        with an_filter_col2:
            # Bounded by default (2026-09-17, per user feedback): a handful of bank loan
            # amortization schedules run to 2031, which otherwise stretches these two charts
            # across 5 years of mostly-empty periods -- real scheduled debt, but noise for
            # reading a trend. "All time" is still one click away for whoever wants the full
            # picture (e.g. to see that same long tail explicitly).
            an_window_today = pd.Timestamp(date.today())
            an_window_end_dates = {
                "Next 1 day": (an_window_today + pd.Timedelta(days=1)).date(),
                "Next 1 week": (an_window_today + pd.Timedelta(days=7)).date(),
                "Next 1 month": (an_window_today + pd.DateOffset(months=1)).date(),
                "Next 2 months": (an_window_today + pd.DateOffset(months=2)).date(),
                "Next 3 months": (an_window_today + pd.DateOffset(months=3)).date(),
                "Next 6 months": (an_window_today + pd.DateOffset(months=6)).date(),
                "Next 12 months": (an_window_today + pd.DateOffset(months=12)).date(),
                "Next 24 months": (an_window_today + pd.DateOffset(months=24)).date(),
            }
            an_window_choice = st.selectbox(
                "Show", options=list(an_window_end_dates.keys()) + ["All time"],
                index=6, key="analytics_window",
            )
        an_end_date = an_window_end_dates.get(an_window_choice)

        an_comp_df = get_ap_composition_by_track(an_granularity, end_date=an_end_date)
        an_growth_df = get_ap_growth_rate(an_granularity, end_date=an_end_date)
        an_concentration = get_supplier_concentration(limit=5)

        kpi1, kpi2, kpi3 = st.columns(3)
        if not an_growth_df.empty:
            kpi1.metric(f"Latest period total ({an_growth_df['period'].iloc[-1]})", php(an_growth_df["total_amount"].iloc[-1]), border=True)
            latest_change = an_growth_df["pct_change"].iloc[-1]
            kpi2.metric(
                f"Change vs prior {an_granularity}",
                f"{latest_change:+.1f}%" if pd.notna(latest_change) else "—",
                border=True,
            )
        else:
            kpi1.metric("Latest period total", "—", border=True)
            kpi2.metric(f"Change vs prior {an_granularity}", "—", border=True)
        kpi3.metric(
            "Top 5 supplier concentration",
            f"{an_concentration['pct']:.0f}%" if an_concentration["total_outstanding"] > 0 else "—",
            border=True,
            help="Share of total outstanding supplier payable held by the 5 largest suppliers by current exposure.",
        )

        st.subheader("AP mix over time, by track")
        if an_comp_df.empty:
            st.caption("No payables data yet.")
        else:
            # an_comp_df's rows are already in chronological order (analytics_service sorts on
            # the raw "2026-09"-style key before converting to this friendly "Sep 2026" label,
            # same pitfall get_payables_trend()'s own period_order already documents elsewhere
            # in this file) -- re-sorting the friendly label here would sort "Dec"/"Feb"/"Jan"
            # alphabetically instead of chronologically, which is exactly the bug this avoids.
            comp_period_order = list(dict.fromkeys(an_comp_df["period"]))
            # Standalone "AP growth rate" line chart replaced (2026-09-17, per user feedback)
            # with an average reference line on this chart plus the same period-total/%-change
            # numbers folded into each bar segment's own tooltip -- one chart instead of two,
            # same information on hover.
            period_totals_df = (
                an_growth_df[["period", "total_amount", "pct_change"]] if not an_growth_df.empty
                else pd.DataFrame(columns=["period", "total_amount", "pct_change"])
            )
            comp_with_context = an_comp_df.merge(period_totals_df, on="period", how="left")
            avg_total = float(period_totals_df["total_amount"].mean()) if not period_totals_df.empty else 0.0

            comp_chart = alt.Chart(comp_with_context).mark_bar().encode(
                x=alt.X("period:N", title="Period", sort=comp_period_order),
                y=alt.Y("amount:Q", title="Total payables (PHP)", stack=True),
                color=alt.Color("track:N", title="Track", scale=_TRACK_COLORS),
                tooltip=[
                    "period", "track",
                    alt.Tooltip("amount:Q", title="This track", format=",.2f"),
                    alt.Tooltip("total_amount:Q", title="Period total (all tracks)", format=",.2f"),
                    alt.Tooltip("pct_change:Q", title="Change vs prior period", format="+.1f"),
                ],
            )
            avg_rule = alt.Chart(pd.DataFrame({"y": [avg_total]})).mark_rule(
                color="#E45756", strokeDash=[6, 4], size=2,
            ).encode(y="y:Q", tooltip=[alt.Tooltip("y:Q", title="Average per period", format=",.2f")])
            st.altair_chart((comp_chart + avg_rule).properties(height=340), width="stretch")
            st.caption(f"Dashed red line: average total payables per {an_granularity} across the window shown ({php(avg_total)}). Hover a bar segment for that period's total and its change vs the prior period.")

        st.subheader("Concentration (Pareto)")
        # One chart with a track filter, not three near-identical ones stacked (2026-09-17, per
        # user feedback) -- "which track" is a filter on the same question ("who/what makes up
        # most of what I owe"), not three different questions.
        an_pareto_track = st.segmented_control(
            "Track", options=["All Tracks", "Supplier", "Lender", "Bank Loan", "Credit Card"],
            default="Supplier", key="analytics_pareto_track",
        )
        an_pareto_track = an_pareto_track or "Supplier"
        if an_pareto_track == "All Tracks":
            an_pareto_df = get_all_tracks_pareto(limit=15)
            an_pareto_noun = "payee/facility, across every track"
        elif an_pareto_track == "Supplier":
            an_pareto_df = get_supplier_pareto(limit=10).rename(columns={"supplier_name": "name"})
            an_pareto_noun = "supplier"
        elif an_pareto_track == "Lender":
            an_pareto_df = get_lender_pareto(limit=10)
            an_pareto_noun = "lender"
        elif an_pareto_track == "Bank Loan":
            an_pareto_df = get_bank_loan_pareto(limit=10)
            an_pareto_noun = "bank loan facility"
        else:
            # Empty for every real business migrated so far -- no cc_statements exist yet (Phase
            # 8a skipped historical CC statement import by design). Kept as a selectable track
            # anyway rather than hidden, same "ready but honestly empty" pattern as the Cost of
            # Debt tab, so it self-populates once real statements get entered.
            an_pareto_df = get_credit_card_pareto(limit=10)
            an_pareto_noun = "credit card"

        if an_pareto_df.empty:
            st.caption(f"No {an_pareto_noun} data yet for this metric.")
        else:
            # Full names truncated/rotated on the x-axis read as noise, not simpler (2026-09-17,
            # per user feedback) -- rank numbers on the axis, full name on hover and in the table
            # underneath, is the same information without the illegible labels.
            an_pareto_df = an_pareto_df.copy()
            an_pareto_df["rank"] = [f"#{i + 1}" for i in range(len(an_pareto_df))]
            rank_order = list(an_pareto_df["rank"])
            # Only get_all_tracks_pareto() returns a "track" column -- fold it into the tooltip
            # when present, so hovering a bar in the All Tracks view also says what kind of
            # obligation it is, not just its name.
            an_pareto_tooltip = ["name"] + (["track"] if "track" in an_pareto_df.columns else []) + [alt.Tooltip("value:Q", title="Outstanding", format=",.2f")]
            an_pareto_line_tooltip = ["name"] + (["track"] if "track" in an_pareto_df.columns else []) + [alt.Tooltip("cumulative_pct:Q", title="Cumulative %", format=".1f")]
            pareto_bars = alt.Chart(an_pareto_df).mark_bar(color="#4C78A8").encode(
                x=alt.X("rank:N", title=None, sort=rank_order),
                y=alt.Y("value:Q", title="Outstanding (PHP)"),
                tooltip=an_pareto_tooltip,
            )
            pareto_line = alt.Chart(an_pareto_df).mark_line(point=True, color="#E45756").encode(
                x=alt.X("rank:N", sort=rank_order),
                y=alt.Y("cumulative_pct:Q", title="Cumulative %", scale=alt.Scale(domain=[0, 100])),
                tooltip=an_pareto_line_tooltip,
            )
            st.altair_chart(
                alt.layer(pareto_bars, pareto_line).resolve_scale(y="independent").properties(height=340),
                width="stretch",
            )
            st.caption(f"Bars (left axis): outstanding per {an_pareto_noun}. Red line (right axis): running cumulative % of total. Hover a bar for its name.")
            an_pareto_table_cols = ["rank", "name"] + (["track"] if "track" in an_pareto_df.columns else []) + ["value", "cumulative_pct"]
            st.dataframe(
                an_pareto_df[an_pareto_table_cols],
                hide_index=True,
                column_config={
                    "rank": "Rank",
                    "name": "Name",
                    "track": "Track",
                    "value": st.column_config.NumberColumn("Outstanding", format=MONEY_FORMAT),
                    "cumulative_pct": st.column_config.NumberColumn("Cumulative %", format="%.1f%%"),
                },
            )

    with analytics_payment_tab:
        an_status_df = get_check_status_breakdown()
        an_aging_df = get_check_aging_distribution()
        an_avg_dpo = get_average_dpo_suppliers()
        an_cancel_rate = (
            an_status_df[an_status_df["effective_status"].isin(["Cancelled", "Replaced", "Spoiled"])]["n"].sum() / an_status_df["n"].sum() * 100
            if not an_status_df.empty else 0.0
        )

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric(
            "Avg Days Payable Outstanding (Suppliers)", f"{an_avg_dpo:.1f} days" if an_avg_dpo is not None else "—",
            border=True,
            help="Average of (check date − batch's weighted invoice due date) across cleared supplier checks. Only meaningful for batches with itemized invoices/due dates on file.",
        )
        kpi2.metric(
            "Cancelled/replaced/spoiled rate", f"{an_cancel_rate:.1f}%" if not an_status_df.empty else "—", border=True,
            help="Share of all Supplier/Lender checks ever issued that ended up Cancelled, Replaced, or Spoiled.",
        )
        kpi3.metric(
            "Still-outstanding checks", f"{int(an_status_df[an_status_df['effective_status'] == 'Active']['n'].iloc[0])}" if not an_status_df.empty and (an_status_df["effective_status"] == "Active").any() else "0",
            border=True,
        )

        st.subheader("Check status breakdown")
        if an_status_df.empty:
            st.caption("No checks issued yet.")
        else:
            status_donut = alt.Chart(an_status_df).mark_arc(innerRadius=60).encode(
                theta=alt.Theta("n:Q", title="Count"),
                color=alt.Color("effective_status:N", title="Status", scale=alt.Scale(scheme="tableau10")),
                tooltip=["effective_status", "n", alt.Tooltip("total:Q", title="Amount", format=",.2f")],
            )
            st.altair_chart(status_donut.properties(height=300), width="stretch")

        st.subheader("Outstanding checks by days until due")
        if an_aging_df.empty:
            st.caption("No outstanding checks right now.")
        else:
            aging_order = [b[2] for b in [(None, 0, "Overdue (not yet marked cleared)"), (0, 7, "Due in 0-7 days"), (8, 30, "Due in 8-30 days"), (31, 60, "Due in 31-60 days"), (61, 90, "Due in 61-90 days"), (91, None, "Due in 90+ days")]]
            aging_bars = alt.Chart(an_aging_df).mark_bar(color="#4C78A8").encode(
                x=alt.X("bucket:N", title=None, sort=aging_order),
                y=alt.Y("amount:Q", title="Amount (PHP)"),
                tooltip=["bucket", "count", alt.Tooltip("amount:Q", title="Amount", format=",.2f")],
            )
            st.altair_chart(aging_bars.properties(height=300), width="stretch")

        with st.expander("Checks cancelled/replaced while overdue"):
            an_overdue_threshold = st.number_input(
                "Flag a cancelled/replaced check as \"long overdue\" past this many days", min_value=1, step=5,
                value=30, key="analytics_overdue_threshold",
                help="A cancelled or replaced check is flagged 'long overdue' when it was voided more than this many days after the due date of what it was paying.",
            )
            an_overdue_df = get_overdue_cancelled_checks(int(an_overdue_threshold))
            if an_overdue_df.empty:
                st.caption("None flagged at this threshold.")
            else:
                st.dataframe(
                    an_overdue_df[["check_number", "creditor_name", "check_date", "amount", "status", "lag_days"]],
                    hide_index=True,
                    column_config={
                        "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT),
                        "lag_days": st.column_config.NumberColumn("Days overdue when voided", format="%.0f"),
                    },
                )

    with analytics_debt_tab:
        an_debt_df = get_debt_cost_comparison()
        st.caption(
            "Effective interest rate per facility, where a real rate is actually on file -- private loan terms, a bank "
            "loan's entered interest portions, or a credit card's entered interest breakdown. A facility with nothing "
            "entered is simply left out, not shown as 0%."
        )
        if an_debt_df.empty:
            st.info("No interest-rate data on file yet for any facility. This populates as real loan terms, bank loan interest portions, or credit card interest breakdowns get entered.")
        else:
            kpi1, kpi2 = st.columns(2)
            kpi1.metric("Facilities with a rate on file", str(len(an_debt_df)), border=True)
            costliest = an_debt_df.iloc[0]
            kpi2.metric(
                "Costliest facility (monthly)", f"{costliest['monthly_rate_pct']:.2f}%", border=True,
                help=f"{costliest['name']} ({costliest['source']}, {costliest['reference']}) -- {costliest['annualized_rate_pct']:.1f}% annualized.",
            )

            debt_bars = alt.Chart(an_debt_df).mark_bar().encode(
                x=alt.X("monthly_rate_pct:Q", title="Monthly rate (%)"),
                y=alt.Y("name:N", title=None, sort="-x"),
                color=alt.Color("source:N", title="Source", scale=alt.Scale(scheme="tableau10")),
                tooltip=["name", "source", "reference", alt.Tooltip("monthly_rate_pct:Q", title="Monthly %", format=".2f"), alt.Tooltip("annualized_rate_pct:Q", title="Annualized %", format=".1f")],
            )
            st.altair_chart(debt_bars.properties(height=max(200, 28 * len(an_debt_df))), width="stretch")
            st.dataframe(
                an_debt_df[["source", "name", "reference", "monthly_rate_pct", "annualized_rate_pct"]],
                hide_index=True,
                column_config={
                    "monthly_rate_pct": st.column_config.NumberColumn("Monthly rate", format="%.2f%%"),
                    "annualized_rate_pct": st.column_config.NumberColumn("Annualized rate", format="%.1f%%"),
                },
            )

        st.divider()
        st.subheader("Priority list: facilities missing rate data")
        st.caption(
            "Real money outstanding on these, but no interest rate/principal entered -- ranked by exposure, so entering "
            "real terms starts with whichever one would move the comparison above the most. Edit a facility's terms from "
            "the Lenders page (loan note) or the Bank loans page."
        )
        an_missing_df = get_missing_debt_terms(limit=10)
        if an_missing_df.empty:
            st.caption("Every facility with outstanding money has rate data on file.")
        else:
            st.dataframe(
                an_missing_df[["source", "name", "reference", "outstanding"]],
                hide_index=True,
                column_config={"outstanding": st.column_config.NumberColumn("Outstanding", format=MONEY_FORMAT)},
            )
