"""Analytics screen, rebuilt (plan H, 2026-10-01): five plainly named sections with past AND future periods, the bank's real clearing data and a download behind
every table. The numbers come from engines/analytics_flow_service.py; the older charts are kept inside the section they belong to."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from datetime import date, timedelta
from engines import settings_registry
from engines.analytics_flow_service import (
    PERIODS, cash_ahead_weeks, cleared_by_week, debt_due_by_month, money_flow, period_range, supplier_spend, voids_by_month,
)
from engines.analytics_service import get_all_tracks_pareto
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
from engines.interest_service import get_yearly_interest_trend

from views.supplier_debt_detail import render_supplier_debt_detail
from views.shared import (  # noqa: F401
    export_buttons,
    page_header,
    section,
    status_badge,
    status_text,
    money_col,
    date_col,
    MONEY_FORMAT,
)
from engines.clock import manila_today

_TRACK_COLORS = alt.Scale(domain=["Supplier", "Lender", "Bank Loan", "Credit Card"], scheme="tableau10")


def _where_the_money_goes() -> None:
    st.caption("What was paid (past months) and what is still scheduled (this month and later), by month and by kind of payee. Solid = paid, pale = still to pay.")
    choice = st.selectbox("Period", PERIODS, index=PERIODS.index("Last 12 + next 12 months"), key="an_period")
    start, end = period_range(choice)
    flow = money_flow(start, end)
    c1, c2, c3 = st.columns(3)
    paid, due = (float(flow.loc[flow["state"] == s, "amount"].sum()) for s in ("Paid", "Due")) if not flow.empty else (0.0, 0.0)
    c1.metric("Paid in this period", php(paid), border=True, help="Checks dated on days that have passed, bank-loan installments and card statements that fell due.")
    c2.metric("Still scheduled", php(due), border=True, help="From today on: checks, installments and card statements still owed.")
    c3.metric("Period", f"{start:%b %Y} – {end:%b %Y}", border=True)
    if flow.empty:
        st.caption("Nothing paid or scheduled in this period.")
    else:
        order = [d for d in sorted(flow["month"].unique())]
        flow = flow.assign(month_label=[f"{m:%b %Y}" for m in flow["month"]])
        labels = [f"{m:%b %Y}" for m in order]
        chart = alt.Chart(flow).mark_bar().encode(
            x=alt.X("month_label:N", title=None, sort=labels), y=alt.Y("amount:Q", title="PHP", stack=True),
            color=alt.Color("track:N", title="Kind of payee", scale=_TRACK_COLORS),
            opacity=alt.Opacity("state:N", scale=alt.Scale(domain=["Paid", "Due"], range=[1.0, 0.45]), legend=alt.Legend(title="State")),
            tooltip=["month_label", "track", "state", alt.Tooltip("amount:Q", title="Amount", format=",.2f"), alt.Tooltip("items:Q", title="Payments")])
        st.altair_chart(chart.properties(height=340), width="stretch")
        export_buttons(flow[["month", "track", "state", "amount", "items"]], "money_by_month", "an_flow_dl",
                       labels={"month": "Month", "track": "Kind of payee", "state": "Paid / still to pay", "amount": "Amount", "items": "Payments"}, what="months")

    section("Who we buy from", "Purchases (invoices, net of their less %) per supplier. Beginning-balance lines from MS Access are not purchases and are left out.")
    spend_end = min(end, manila_today())
    if spend_end < start:
        st.caption("Pick a period that includes past days to see purchases.")
        return
    spend = supplier_spend(start, spend_end)
    if spend.empty:
        st.caption("No supplier invoices in this period yet (the migrated history is a beginning balance, not purchases).")
        return
    st.dataframe(spend.head(15), hide_index=True, column_config={
        "supplier": "Supplier", "spend": st.column_config.NumberColumn("Purchases", format=MONEY_FORMAT), "share_pct": st.column_config.NumberColumn("Share", format="%.1f%%"),
        "previous": st.column_config.NumberColumn("Previous period", format=MONEY_FORMAT), "change_pct": st.column_config.NumberColumn("Change", format="%+.0f%%"), "invoices": "Invoices"})
    export_buttons(spend, "supplier_spend", "an_spend_dl", labels={"supplier": "Supplier", "spend": "Purchases", "share_pct": "Share %", "previous": "Previous period",
                                                                    "change_pct": "Change %", "invoices": "Invoices"}, what="suppliers")


def _who_we_owe_most() -> None:
    conc = get_supplier_concentration(limit=5)
    limit = settings_registry.value("supplier_concentration_pct")
    k1, k2 = st.columns(2)
    k1.metric("Top 5 suppliers' share of what we owe suppliers", f"{conc['pct']:.0f}%" if conc["total_outstanding"] > 0 else "—", border=True,
              help="Share of all supplier payable held by the 5 largest suppliers by current exposure.")
    k2.metric("Dashboard alert level for one supplier", f"{limit:g}%", border=True, help="Change it in Settings.")
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


def _how_we_pay() -> None:
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

    weeks_back = st.selectbox("Bank clearing history", [8, 13, 26, 52], index=1, format_func=lambda w: f"Last {w} weeks", key="an_clear_weeks")
    cleared = cleared_by_week(manila_today() - timedelta(weeks=weeks_back), manila_today())
    section("Checks the bank cleared, per week", "From the imported bank statements: how many checks cleared each week, and on average how many days after the check's date.")
    if cleared.empty:
        st.caption("No cleared checks in this window yet (import bank statements on Bank and cash).")
    else:
        weekly = cleared.assign(week_label=[f"{w:%b %d}" for w in cleared["week"]])
        bars = alt.Chart(weekly).mark_bar(color="#4C78A8").encode(x=alt.X("week_label:N", title="Week starting", sort=list(weekly["week_label"])), y=alt.Y("amount:Q", title="PHP cleared"),
                                                                    tooltip=["week_label", "checks", alt.Tooltip("amount:Q", format=",.2f"), alt.Tooltip("avg_days:Q", title="Avg days after check date", format=".1f")])
        st.altair_chart(bars.properties(height=280), width="stretch")
        st.metric("Average days from check date to the bank clearing it", f"{(cleared['avg_days'] * cleared['checks']).sum() / cleared['checks'].sum():.1f} days", border=True)
        export_buttons(cleared, "checks_cleared_per_week", "an_cleared_dl", labels={"week": "Week starting", "checks": "Checks", "amount": "Amount", "avg_days": "Avg days to clear"}, what="weeks")

    voids = voids_by_month(manila_today() - timedelta(days=365), manila_today())
    section("Checks voided and replaced, per month", "Counted from the day the void was recorded (kept since Sep 30, 2026).")
    if voids.empty:
        st.caption("Nothing voided or replaced since the dates were kept.")
    else:
        long = voids.melt(id_vars="month", var_name="what", value_name="checks")
        st.altair_chart(alt.Chart(long).mark_bar().encode(x=alt.X("month:N", title=None), y=alt.Y("checks:Q", title="Checks"), color=alt.Color("what:N", title=None),
                                                           tooltip=["month", "what", "checks"]).properties(height=240), width="stretch")
        export_buttons(voids, "voided_replaced_per_month", "an_voids_dl", labels={"month": "Month", "voided": "Voided", "replaced": "Replaced"}, what="months")


def _cost_of_borrowing() -> None:
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

    st.divider()
    section("Interest paid, per month", "Private loans, bank loans (where the interest part is filled in) and credit cards (where entered).")
    trend = get_yearly_interest_trend(manila_today().year)
    if trend.empty or trend["total_interest"].sum() == 0:
        st.caption("No interest recorded this year yet.")
    else:
        st.line_chart(trend.set_index("month_name")[["private_loans_interest", "bank_loans_interest", "credit_cards_interest", "total_interest"]], height=280)
        export_buttons(trend, "interest_per_month", "an_interest_dl", what="months")

    section("Debt falling due, month by month (next 12 months)", "Private loan checks, bank-loan installments and card statements still owed — where the heavy months are.")
    due = debt_due_by_month(12)
    if due.empty:
        st.caption("No debt falls due in the next 12 months.")
    else:
        due = due.assign(month_label=[f"{m:%b %Y}" for m in due["month"]])
        order = list(dict.fromkeys(due.sort_values("month")["month_label"]))
        st.altair_chart(alt.Chart(due).mark_bar().encode(x=alt.X("month_label:N", title=None, sort=order), y=alt.Y("amount:Q", title="PHP"), color=alt.Color("source:N", title=None),
                                                         tooltip=["month_label", "source", alt.Tooltip("amount:Q", format=",.2f")]).properties(height=300), width="stretch")
        export_buttons(due[["month", "source", "amount"]], "debt_due_by_month", "an_due_dl", labels={"month": "Month", "source": "Source", "amount": "Amount"}, what="months")


def _cash_ahead() -> None:
    st.caption("The cash forecast week by week for the next 13 weeks: the bank balance from the latest statement, expected deposits, and what is scheduled to go out.")
    weeks = cash_ahead_weeks(13)
    if weeks.empty:
        st.info("No recent bank statement is on file, so there is no forecast yet. Import the latest Metrobank statement on Bank and cash.")
        return
    short = weeks[weeks["short"]]
    c1, c2 = st.columns(2)
    c1.metric("Lowest balance in the next 13 weeks", php(float(weeks["lowest_balance"].min())), border=True)
    c2.metric("Weeks under your cash buffer", str(len(short)), border=True, help="Set the buffer in Settings.")
    weekly = weeks.assign(week_label=[f"{w:%b %d}" for w in weeks["week"]])
    order = list(weekly["week_label"])
    flow_long = weekly.melt(id_vars="week_label", value_vars=["money_in", "money_out"], var_name="what", value_name="amount")
    flow_long["what"] = flow_long["what"].map({"money_in": "Expected in", "money_out": "Going out"})
    bars = alt.Chart(flow_long).mark_bar().encode(x=alt.X("week_label:N", title="Week starting", sort=order), xOffset="what:N", y=alt.Y("amount:Q", title="PHP"),
                                                  color=alt.Color("what:N", title=None, scale=alt.Scale(domain=["Expected in", "Going out"], range=["#54A24B", "#E45756"])),
                                                  tooltip=["week_label", "what", alt.Tooltip("amount:Q", format=",.2f")])
    line = alt.Chart(weekly).mark_line(point=True, color="#1f3a5f").encode(x=alt.X("week_label:N", sort=order), y=alt.Y("end_balance:Q", title="Balance at week end"),
                                                                           tooltip=["week_label", alt.Tooltip("end_balance:Q", title="Balance at week end", format=",.2f")])
    st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent").properties(height=340), width="stretch")
    st.dataframe(weeks, hide_index=True, column_config={"week": "Week starting", "money_in": st.column_config.NumberColumn("Expected in", format=MONEY_FORMAT),
                                                        "money_out": st.column_config.NumberColumn("Going out", format=MONEY_FORMAT),
                                                        "end_balance": st.column_config.NumberColumn("Balance at week end", format=MONEY_FORMAT),
                                                        "lowest_balance": st.column_config.NumberColumn("Lowest that week", format=MONEY_FORMAT), "short": "Under buffer"})
    export_buttons(weeks, "cash_next_13_weeks", "an_cash_dl", labels={"week": "Week starting", "money_in": "Expected in", "money_out": "Going out", "end_balance": "Balance at week end",
                                                                       "lowest_balance": "Lowest that week", "short": "Under buffer"}, what="weeks")


def render() -> None:
    page_header("Analytics", "Where the money goes, who we owe, how we pay, what borrowing costs and what cash is ahead.")
    render_body()


def render_body() -> None:
    """The analytics tabs without the page header: Reports shows them as the 'Analytics' report (the Analytics page is gone, 2026-10-03)."""
    st.caption("This system tracks the AP side only (no sales or general ledger), so ratios like current ratio are not offered — everything here comes from the checks, loans, "
               "cards and bank statements on file.")
    from engines.settings_registry import forecast_shown

    labels = [":material/payments: Where the money goes", ":material/groups: Who we owe most", ":material/speed: How we pay", ":material/percent: Cost of borrowing"]
    if forecast_shown():                                    # hidden until the Sales / AR cycle brings real deposits (Settings)
        labels.append(":material/account_balance: Cash ahead")
    tabs = st.tabs(labels + [":material/more_horiz: More detail"])
    tab_money, tab_owe, tab_pay, tab_cost, tab_more = tabs[0], tabs[1], tabs[2], tabs[3], tabs[-1]
    tab_cash = tabs[4] if forecast_shown() else None
    with tab_money:
        _where_the_money_goes()
    with tab_owe:
        _who_we_owe_most()
    with tab_pay:
        _how_we_pay()
    with tab_cost:
        _cost_of_borrowing()
    if tab_cash is not None:
        with tab_cash:
            _cash_ahead()
    with tab_more:
        st.caption("Aging, payment health, top suppliers, payables trend and the interest tracker (moved from the Dashboard).")
        render_supplier_debt_detail()
