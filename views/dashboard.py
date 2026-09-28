"""Dashboard screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import altair as alt
import calendar
import pandas as pd
import streamlit as st

from datetime import date
from datetime import timedelta
from engines.cc_service import get_statement_gap_alerts
from engines.check_run_service import get_pending_check_run
from engines.checkbook_service import get_checkbook_alerts
from engines.bank_alerts import all_alerts
from engines.cash_insights_service import dashboard_bank_summary
from engines.common import fix_nullable_int
from engines.common import php
from engines.common import prospective_year
from engines.common import remove_blocked_date
from engines.common import set_blocked_date
from engines.dashboard_kpi_service import get_average_dpo_suppliers
from engines.dashboard_kpi_service import get_overdue_cancelled_checks
from engines.dashboard_kpi_service import get_payables_trend
from engines.dashboard_kpi_service import get_stale_checks
from engines.dashboard_kpi_service import get_supplier_concentration
from engines.dashboard_kpi_service import get_top_supplier_dependency
from engines.dashboard_kpi_service import get_top_suppliers
from engines.dashboard_kpi_service import get_total_ap_outstanding
from engines.dashboard_kpi_service import get_upcoming_cash_obligations
from engines.interest_service import get_monthly_interest_summary
from engines.interest_service import get_yearly_interest_trend
from engines.reports_service import get_payment_schedule_all_tracks
from engines.reports_service import get_payment_schedule_daily_totals
from engines.reports_service import get_supplier_ap_aging_report

from views.bank_cash import show_alerts
from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    date_col,
    go_to,
    get_blocked_dates_for_month,
    money_col,
    page_header,
    q,
    refresh,
    section,
    status_badge,
    status_text,
)


def render() -> None:
    page_header("Dashboard")

    # "No statement on file yet" (a card with zero statement history, typically not in active use)
    # is routine, not urgent -- a legacy import can leave 40+ of these, which used to fire this
    # banner in red (st.error) every single day. Only "Possibly missing a statement" (a card that
    # HAD statements but seems to have skipped a cycle) is a real, actionable risk worth an error.
    dash_gap_alerts_df = get_statement_gap_alerts()
    dash_missing_recent_df = dash_gap_alerts_df[dash_gap_alerts_df["alert"] == "Possibly missing a statement"] if not dash_gap_alerts_df.empty else dash_gap_alerts_df
    dash_never_had_df = dash_gap_alerts_df[dash_gap_alerts_df["alert"] == "No statement on file yet"] if not dash_gap_alerts_df.empty else dash_gap_alerts_df
    if not dash_missing_recent_df.empty:
        st.error(
            f":material/report: {len(dash_missing_recent_df)} credit card statement(s) may have been overlooked — "
            + "; ".join(f"{r['card_name']} ({r['alert'].lower()})" for _, r in dash_missing_recent_df.iterrows())
            + ". Check the Credit cards page."
        )
    if not dash_never_had_df.empty:
        st.caption(f":material/info: {len(dash_never_had_df)} card(s) have never had a statement recorded — routine for cards not yet in active use. See the Credit cards page.")

    dash_checkbook_alerts_df = get_checkbook_alerts()
    if not dash_checkbook_alerts_df.empty:
        st.error(
            f":material/report: {len(dash_checkbook_alerts_df)} checkbook(s) have unaccounted checks — "
            + "; ".join(
                f"{r['booklet_code']} ({r['missing_gaps_count']} skipped, {r['duplicate_numbers_count']} duplicate)"
                for _, r in dash_checkbook_alerts_df.iterrows()
            )
            + ". Check Admin and audit (Check booklets)."
        )

    # Supplier concentration alert (2026-09-17, per user request): names the single largest
    # supplier relationship rather than a blended top-5 percentage, since that's the one whose
    # disruption would actually hurt -- 25% is a common rule-of-thumb concentration-risk
    # threshold, not derived from anything this business told me specifically.
    SUPPLIER_DEPENDENCY_ALERT_THRESHOLD_PCT = 25.0
    dash_supplier_dependency = get_top_supplier_dependency()
    if dash_supplier_dependency and dash_supplier_dependency["pct"] >= SUPPLIER_DEPENDENCY_ALERT_THRESHOLD_PCT:
        st.warning(
            f":material/warning: Supplier concentration risk — {dash_supplier_dependency['name']} alone accounts for "
            f"{dash_supplier_dependency['pct']:.0f}% of total outstanding supplier payable "
            f"({php(dash_supplier_dependency['amount'])} of {php(dash_supplier_dependency['total_outstanding'])}). "
            "See Analytics → Supplier concentration (Pareto)."
        )

    # Supplier + Lender checks not yet past their date, bank-loan installments not yet past due, cards still owed. Due today = still owed
    # (owner, 2026-09-28). The bank's real cleared dates (Bank and cash) are shown in the Bank and cash panel below, not in this total.
    ap_outstanding = get_total_ap_outstanding()

    active_checks = q("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE status = 'Active'")
    cc_alerts = q("SELECT * FROM v_cc_due_alerts")
    critical_alerts = cc_alerts[cc_alerts["urgency_status"] == "CRITICAL (0-3 Days)"] if not cc_alerts.empty else cc_alerts
    # Same 180-day cutoff v_effective_checks (schema.sql) uses to call a check "Stale" -- but a check the imported bank statement shows as
    # cleared is not stale (2026-09-28, reconciliation).
    stale_checks = get_stale_checks(180)
    pending_checks_df = get_pending_check_run()
    pending_week_df = (
        pending_checks_df[pd.to_datetime(pending_checks_df["check_date"]).dt.date <= (date.today() + timedelta(days=7))]
        if not pending_checks_df.empty else pending_checks_df
    )

    with st.container(horizontal=True):
        st.metric("Total AP outstanding", php(ap_outstanding), border=True, help='Everything still owed today: supplier and lender checks dated today or later, bank-loan installments due today or later, and credit-card amounts still owed. Due today counts as still owed.')
        st.metric("Active checks in circulation", f"{int(active_checks['n'][0])} · {php(active_checks['total'][0])}", border=True, help='Every check with status Active — issued and not cancelled or replaced, whatever its date.')
        st.metric(
            "Needs attention", str(len(critical_alerts) + len(dash_missing_recent_df) + len(dash_checkbook_alerts_df)),
            border=True,
            help=f"{len(critical_alerts)} critical credit card alert(s), {len(dash_missing_recent_df)} possibly missed CC statement(s), {len(dash_checkbook_alerts_df)} checkbook issue(s) -- see the banners above, the Credit cards tab, and the Admin & audit tab. ({len(dash_never_had_df)} more card(s) have never had a statement recorded — routine, not counted here.)",
        )

    cash_7d = get_upcoming_cash_obligations(7)
    cash_30d = get_upcoming_cash_obligations(30)
    concentration = get_supplier_concentration(limit=5)
    with st.container(horizontal=True):
        st.metric(
            "Due in next 7 days (all tracks)", php(cash_7d), border=True,
            help="Supplier/Lender checks, Bank Loan installments, and Credit Card statements still owed and due within 7 days — same scope as the Schedule of Payments report.",
        )
        st.metric(
            "Due in next 30 days (all tracks)", php(cash_30d), border=True,
            help="Same scope as above, 30-day window.",
        )
        st.metric(
            "Top 5 suppliers' share of outstanding",
            f"{concentration['pct']:.0f}%" if concentration["total_outstanding"] > 0 else "—",
            border=True,
            help=f"{php(concentration['top_total'])} of {php(concentration['total_outstanding'])} total outstanding supplier payable sits with the 5 largest suppliers by current exposure.",
        )

    with st.container(horizontal=True):
        st.metric(
            "Stale checks", f"{stale_checks['n']} · {php(stale_checks['total'])}", border=True,
            help="Still Active, dated more than 180 days ago, and not shown as cleared on any imported bank statement. May have been lost or never deposited.",
        )
        st.metric(
            "Checks pending print/signature this week",
            f"{len(pending_week_df)} · {php(pending_week_df['amount'].sum() if not pending_week_df.empty else 0.0)}",
            border=True,
            help="Already issued but not yet printed, dated within the next 7 days. Full list: Checks and checkbook → Checks to print.",
        )

    # Bank and cash panel (2026-09-28, reconciliation plan phase 2): figures from the imported Metrobank statements
    # (engines/cash_insights_service.dashboard_bank_summary) and the same alerts as Payment run (engines/bank_alerts.py).
    try:
        dash_bank = dashboard_bank_summary()
        dash_bank_alerts = all_alerts()
    except Exception:                                   # a database without the bank tables yet
        dash_bank, dash_bank_alerts = None, []
    with st.container(border=True):
        bh1, bh2 = st.columns([5, 1.6], vertical_alignment="center")
        bh1.subheader("Bank and cash")
        if bh2.button("Open Bank and cash", icon=":material/account_balance_wallet:", key="dash_bank_btn", width="stretch"):
            go_to("bank-cash")
        if dash_bank is None:
            st.caption("No bank statement imported yet. Drop the Metrobank MBOS statement on Bank and cash to see the bank balance, which checks "
                       "really cleared and a cash forecast here.")
        else:
            if dash_bank["age_days"] > 14:
                st.caption(f":material/history: The latest bank balance is from {dash_bank['as_of']:%b %d, %Y} ({dash_bank['age_days']} days ago) — "
                           "import a recent statement for current figures.")
            with st.container(horizontal=True):
                st.metric(f"Bank balance ({dash_bank['as_of']:%b %d})", php(dash_bank["balance"]), border=True,
                          help="The last balance on the imported bank statements.")
                st.metric("Checks confirmed cleared", f"{dash_bank['pct_cleared']:.0f}%" if dash_bank["pct_cleared"] is not None else "—",
                          f"{dash_bank['checks_cleared']} of {dash_bank['checks_in_period']}", delta_color="off", delta_arrow="off", border=True,
                          help="Of our numbered active checks dated within the imported statements, how many the bank shows as cleared.")
                st.metric("Not yet cleared (past their date)", php(dash_bank["float_amount"]), f"{dash_bank['float_count']} check(s)",
                          delta_color="off", delta_arrow="off", border=True,
                          help="Checks dated on or before the last bank date that the bank has not cleared — suppliers have not deposited them yet.")
                st.metric("Bank lines needing a look", str(dash_bank["open_items"]), border=True,
                          help="Resolve them on Bank and cash → Reconciliation.")
                low = dash_bank["lowest"]
                st.metric("Lowest balance, next 30 days", php(low[1]) if low else "—",
                          (f"on {low[0]:%b %d} · buffer {php(dash_bank['buffer'])}" if low else "needs a recent statement"),
                          delta_color="off", delta_arrow="off", border=True,
                          help="From the cash forecast: bank balance + expected money in − checks not cleared and loan / card dues.")
        show_alerts(dash_bank_alerts)

    # My reports pinned to the Dashboard (2026-09-28, plan phase 4; engines/report_builder.py): each one's headline figure and chart.
    try:
        from engines.report_builder import pinned_results

        dash_pins = pinned_results()
    except Exception:                                   # a database without saved_reports yet
        dash_pins = []
    if dash_pins:
        from views.my_reports import headline_text, show_chart

        st.subheader("My reports")
        for start in range(0, len(dash_pins), 3):
            for col, (pin_row, pin_res) in zip(st.columns(3), dash_pins[start:start + 3]):
                with col.container(border=True):
                    st.markdown(f"**{pin_row['name']}**")
                    if isinstance(pin_res, str):
                        st.warning(f"This report no longer runs: {pin_res}")
                    else:
                        st.metric(pin_res.headline[0], headline_text(pin_res))
                        st.caption(" · ".join(pin_res.criteria[1:]) or pin_res.dataset_label)
                        show_chart(pin_res, height=170)
                    if st.button("Open", icon=":material/open_in_new:", key=f"dash_pin_open_{pin_row['report_id']}"):
                        go_to("reports", rpt_mode="My reports", _mr_pick_next=int(pin_row["report_id"]))

    # TRIAL C (2026-09-14): calendar heatmap — hover a day to see that day's all-tracks total,
    # instead of picking a date (Trial A) or reading it off the issue-check grid (Trial B). A
    # third option to compare, not a replacement — drop this block if it doesn't win.
    with st.container(border=True):
        st.subheader("Payment calendar (all tracks)")
        st.caption("Hover a day for its total. Darker = more payable that day.")
        cal_year = st.session_state.get("cal_year", date.today().year)
        cal_month = st.session_state.get("cal_month", date.today().month)
        cal_month_names = [date(2000, m, 1).strftime("%B") for m in range(1, 13)]
        # Wide enough to cover the postdated-check horizon this business actually schedules
        # against (the payables trend chart above already runs years out), padded a bit past
        # today on both sides rather than tied to whatever data happens to exist right now.
        cal_year_options = list(range(date.today().year - 5, date.today().year + 11))
        if cal_year not in cal_year_options:
            cal_year_options = sorted(set(cal_year_options) | {cal_year})

        cal_col_prev, cal_col_month_pick, cal_col_year_pick, cal_col_next = st.columns([1, 2, 1.4, 1])
        # Both arrow buttons are checked here, before the month/year selectboxes below are
        # instantiated (Streamlit raises a StreamlitAPIException if session_state[key] is set
        # for a widget's key after that widget has already been instantiated in the same script
        # run). `with cal_col_next:` still renders the button in the rightmost column even
        # though its code now runs before the middle columns' widgets are declared -- column
        # placement follows the `st.columns(...)` call, not code order.
        with cal_col_prev:
            if st.button("◀", key="cal_prev_month"):
                cal_month -= 1
                if cal_month < 1:
                    cal_month, cal_year = 12, cal_year - 1
                st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month
                # The month/year picker widgets below keep their own state under their own keys
                # once set -- their `index=` argument is only consulted before that state exists,
                # so the prev/next buttons have to push the new value into those keys directly or
                # the pickers would silently stay on the old month/year after a prev/next click.
                st.session_state["cal_month_pick"] = cal_month_names[cal_month - 1]
                st.session_state["cal_year_pick"] = cal_year
                st.rerun()
        with cal_col_next:
            if st.button("▶", key="cal_next_month"):
                cal_month += 1
                if cal_month > 12:
                    cal_month, cal_year = 1, cal_year + 1
                st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month
                st.session_state["cal_month_pick"] = cal_month_names[cal_month - 1]
                st.session_state["cal_year_pick"] = cal_year
                st.rerun()
        def _cal_month_picked() -> None:
            st.session_state["cal_year_pick"] = prospective_year(cal_month_names.index(st.session_state["cal_month_pick"]) + 1)

        with cal_col_month_pick:
            cal_picked_month_name = st.selectbox(
                "Month", cal_month_names, index=cal_month - 1, key="cal_month_pick", label_visibility="collapsed", on_change=_cal_month_picked,
            )
            cal_month = cal_month_names.index(cal_picked_month_name) + 1
        with cal_col_year_pick:
            cal_year = st.selectbox(
                "Year", cal_year_options, index=cal_year_options.index(cal_year), key="cal_year_pick", label_visibility="collapsed",
            )
        st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month

        weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        cal_rows = []
        for week_idx, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(cal_year, cal_month)):
            for day_idx, day_num in enumerate(week):
                if day_num == 0:
                    continue
                cal_rows.append({"date": date(cal_year, cal_month, day_num), "week": week_idx, "weekday": weekday_names[day_idx], "day": day_num})
        cal_grid_df = pd.DataFrame(cal_rows)

        cal_daily = get_payment_schedule_daily_totals()
        if not cal_daily.empty:
            cal_daily = cal_daily.copy()
            cal_daily["actual_payment_date"] = pd.to_datetime(cal_daily["actual_payment_date"]).dt.date
        cal_merged = cal_grid_df.merge(cal_daily, left_on="date", right_on="actual_payment_date", how="left") if not cal_daily.empty else cal_grid_df.assign(total_amount=0.0, num_items=0)
        cal_merged["total_amount"] = cal_merged["total_amount"].fillna(0.0)
        cal_merged["num_items"] = cal_merged["num_items"].fillna(0).astype(int)

        # Greyed-out dates (2026-09-14): Philippine holidays (seeded, migration 024) and any
        # custom date the user has marked — both stored in calendar_blocked_dates, rendered as
        # a flat grey layer that overrides the heat color for just those cells.
        cal_blocked = get_blocked_dates_for_month(cal_year, cal_month)
        cal_merged["is_blocked"] = cal_merged["date"].isin(cal_blocked.keys())
        cal_merged["blocked_label"] = cal_merged["date"].apply(lambda d: cal_blocked.get(d, {}).get("label") or cal_blocked.get(d, {}).get("kind", "").title())
        cal_normal_df = cal_merged[~cal_merged["is_blocked"]]
        cal_blocked_df = cal_merged[cal_merged["is_blocked"]]

        cal_tooltip = [
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("total_amount:Q", title="Total payable", format=",.2f"),
            alt.Tooltip("num_items:Q", title="Items"),
        ]
        cal_tooltip_blocked = cal_tooltip + [alt.Tooltip("blocked_label:N", title="Marked")]
        # Transposed (2026-09-17) to the conventional calendar layout -- weekday across the top
        # as column headers, week running down as rows -- rather than weekday-as-row/week-as-
        # column, which read more like a generic pivot table than a calendar at a glance.
        cal_weekday_axis = alt.Axis(orient="top", labelAngle=0)
        cal_heat = alt.Chart(cal_normal_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4).encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            color=alt.Color("total_amount:Q", title="Payable", scale=alt.Scale(scheme="blues", type="sqrt"), legend=None),
            tooltip=cal_tooltip,
        )
        cal_grey = alt.Chart(cal_blocked_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4, color="#C4C4C4").encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            tooltip=cal_tooltip_blocked,
        )
        cal_dark_threshold = cal_merged["total_amount"].max() * 0.5  # 0 when every day is 0 -> "> 0" is False for all -> black text throughout
        cal_text = alt.Chart(cal_merged).mark_text(baseline="middle", fontSize=12).encode(
            x=alt.X("weekday:N", sort=weekday_names, axis=cal_weekday_axis), y=alt.Y("week:O", axis=None),
            text="day:N",
            color=(
                alt.when(alt.datum.is_blocked).then(alt.value("#404040"))
                .when(alt.datum.total_amount > cal_dark_threshold).then(alt.value("white"))
                .otherwise(alt.value("black"))
            ),  # Altair 6 dropped support for nesting alt.condition() calls -- alt.when()/.then()/.otherwise() is the current chained-condition API.
            tooltip=cal_tooltip,  # the text glyph sits on top of the rect per-pixel, so it needs its
            # own copy of the same tooltip fields -- otherwise hovering exactly on a day number shows
            # this mark's own bare x/y/text encoding (week/weekday/day) instead of the rect's tooltip.
        )
        # Select on the plain integer "day" field, not the temporal "date" field: Vega-Lite
        # returns a temporal selection as milliseconds-since-epoch in UTC, and converting that
        # back with pandas landed one calendar day off the actually-clicked cell (confirmed
        # live: clicking the 15th came back as the 14th) -- a real UTC/local timezone mismatch,
        # not worth fighting when "day" + the already-known cal_year/cal_month reconstructs the
        # exact date with no serialization round-trip at all.
        cal_click = alt.selection_point(name="cal_pick", fields=["day"], on="click", empty=False)
        cal_combined = (cal_heat + cal_grey + cal_text).add_params(cal_click)
        cal_event = st.altair_chart(cal_combined.properties(height=220), width="stretch", on_select="rerun", key="cal_chart")

        cal_picked_date = None
        cal_sel = cal_event.selection.get("cal_pick") if cal_event and cal_event.selection else None
        if cal_sel:
            cal_picked_date = date(cal_year, cal_month, int(cal_sel[0]["day"]))

        if cal_picked_date is not None:
            cal_day_items = get_payment_schedule_all_tracks()
            if not cal_day_items.empty:
                cal_day_items = cal_day_items[pd.to_datetime(cal_day_items["actual_payment_date"]).dt.date == cal_picked_date]
            with st.container(border=True):
                st.markdown(f"**{cal_picked_date.strftime('%B %d, %Y')} — breakdown**")
                if cal_day_items.empty:
                    st.caption("Nothing scheduled for this date.")
                else:
                    st.dataframe(
                        cal_day_items[["track", "payee_name", "reference", "amount", "due_date"]],
                        hide_index=True,
                        column_config={"amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)},
                    )

                cal_existing_block = cal_blocked.get(cal_picked_date)
                if cal_existing_block:
                    st.info(f"Marked as **{cal_existing_block['label'] or cal_existing_block['kind'].title()}** ({cal_existing_block['kind']}).")
                    if st.button("Unmark this date", icon=":material/clear:", key="cal_unmark_btn"):
                        remove_blocked_date(cal_picked_date)
                        refresh()
                else:
                    cal_mark_col1, cal_mark_col2, cal_mark_col3 = st.columns([2, 1, 1])
                    with cal_mark_col1:
                        cal_mark_label = st.text_input("Label (optional)", key="cal_mark_label", placeholder="e.g. Eid'l Fitr, or a personal note")
                    with cal_mark_col2:
                        cal_mark_kind = st.selectbox("Type", options=["custom", "holiday"], format_func=lambda k: "Custom / no-issue day" if k == "custom" else "Holiday", key="cal_mark_kind")
                    with cal_mark_col3:
                        st.write("")
                        st.write("")
                        if st.button("Mark this date", icon=":material/event_busy:", key="cal_mark_btn"):
                            set_blocked_date(cal_picked_date, cal_mark_label.strip(), cal_mark_kind)
                            refresh()

    with st.container(border=True):
        st.subheader("Credit card due alerts")
        if cc_alerts.empty:
            st.caption("No unpaid credit card statements.")
        else:
            st.dataframe(
                cc_alerts,
                hide_index=True,
                column_config={
                    "total_amount_due": st.column_config.NumberColumn("Amount due", format=MONEY_FORMAT),
                    "days_until_due": st.column_config.NumberColumn("Days until due"),
                },
            )

    # Collapsed (2026-09-17, per user request): these six sections measure supplier payment
    # behavior over time (aging, DPO, rankings, trend, interest) -- genuinely useful, but not a
    # daily decision-driver, and several read as blank/zero for legacy-migrated data (no
    # itemized due dates survived Phase 8c's consolidated-batch import) until enough new,
    # normal-workflow invoices accumulate post-launch. Grouped here instead of occupying
    # permanent space on the main Overview.
    with st.expander("Supplier & debt analytics"):
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

    with st.expander("Recent audit log"):
        audit_df = fix_nullable_int(q("SELECT log_id, user_action, entity_name, entity_id, old_values, new_values, datetime(timestamp, 'localtime') AS timestamp FROM audit_logs ORDER BY log_id DESC LIMIT 15"), "entity_id")
        st.dataframe(audit_df, hide_index=True)
