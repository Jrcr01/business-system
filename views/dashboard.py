"""Dashboard screen -- "today" only (plan H, 2026-10-01): one key row, ONE combined list of what needs attention (engines/attention_service.py), the payment
calendar with the next 7 days under it, and the pinned reports. The supplier / debt analytics moved to Analytics; the audit log is in Admin and audit."""
from __future__ import annotations

import altair as alt
import calendar
import pandas as pd
import streamlit as st

from datetime import date
from datetime import timedelta
from engines.attention_service import attention_count, attention_items
from engines.cash_insights_service import dashboard_bank_summary
from engines.common import php
from engines.common import prospective_year
from engines.common import remove_blocked_date
from engines.common import set_blocked_date
from engines.dashboard_kpi_service import get_total_ap_outstanding
from engines.dashboard_kpi_service import get_upcoming_cash_obligations
from engines.payment_run_service import get_run_stages, waiting_count
from engines.reports_service import attach_calendar_totals, get_payment_calendar_items

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    go_to,
    get_blocked_dates_for_month,
    page_header,
    q,
    refresh,
)
from engines.clock import manila_today

LEVEL_ICON = {"error": ":red[:material/error:]", "warning": ":orange[:material/warning:]", "info": ":blue[:material/info:]"}


def _key_row(bank: dict | None, n_attention: int) -> None:
    with st.container(horizontal=True):
        if bank is None:
            st.metric("Bank balance", "—", "no statement imported", delta_color="off", delta_arrow="off", border=True, help="Import the Metrobank statement on Bank and cash.")
        else:
            st.metric(f"Bank balance ({bank['as_of']:%b %d})", php(bank["balance"]), f"{bank['age_days']} day(s) old" if bank["age_days"] else "today",
                      delta_color="off", delta_arrow="off", border=True, help="The last balance on the imported bank statements.")
        st.metric("Due in next 7 days", php(get_upcoming_cash_obligations(7)), border=True,
                  help="Supplier / lender checks, bank-loan installments and card statements still owed and due within 7 days — same scope as the Schedule of payments.")
        from engines.settings_registry import forecast_shown

        if forecast_shown():
            low = bank["lowest"] if bank else None
            st.metric("Lowest balance, next 30 days", php(low[1]) if low else "—",
                      (f"on {low[0]:%b %d} · buffer {php(bank['buffer'])}" if low else "needs a recent statement"), delta_color="off", delta_arrow="off", border=True,
                      help="From the cash forecast: bank balance + expected money in − checks not cleared and loan / card dues.")
        else:                                                      # the forecast is hidden until the Sales / AR cycle brings real deposits (Settings)
            st.metric("Due in next 30 days", php(get_upcoming_cash_obligations(30)), border=True,
                      help="Supplier / lender checks, bank-loan installments and card statements still owed and due within 30 days. No forecast of deposits.")
        st.metric("To do today", str(waiting_count(get_run_stages())), border=True, help="Steps waiting on the Payment run page.")
        st.metric("Needs attention", str(n_attention), border=True, help="Errors and warnings in the list below. Information lines are not counted.")


def _attention_list(items: list[dict]) -> None:
    with st.container(border=True):
        st.subheader("Needs attention")
        if not items:
            st.success("Nothing needs attention right now.", icon=":material/check_circle:")
            return
        for n, item in enumerate(items):
            c1, c2 = st.columns([8, 1.3], vertical_alignment="center")
            c1.markdown(f"{LEVEL_ICON[item['level']]} {item['text']}")
            if c2.button("Open", key=f"dash_attn_{n}", icon=":material/arrow_forward:", width="stretch"):
                go_to(item["page"])


def _next_seven_days() -> None:
    with st.container(border=True):
        st.subheader("Next 7 days")
        items = get_payment_calendar_items()
        if not items.empty:
            day = pd.to_datetime(items["actual_payment_date"]).dt.date
            items = items[(items["state"] == "Due") & (day >= manila_today()) & (day <= manila_today() + timedelta(days=6))]
        if items.empty:
            st.caption("Nothing falls due in the next 7 days.")
            return
        st.dataframe(items[["actual_payment_date", "track", "payee_name", "reference", "amount"]], hide_index=True,
                     column_config={"actual_payment_date": st.column_config.DateColumn("Day", format="ddd, MMM DD"), "track": "Track", "payee_name": "Payee",
                                    "reference": "Reference", "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
        st.caption(f"{len(items)} payment(s), {php(items['amount'].sum())}.")


def render() -> None:
    page_header("Dashboard", "Today: what is due, what the bank can cover and what needs you.")

    try:
        bank = dashboard_bank_summary()
    except Exception:                                   # a database without the bank tables yet
        bank = None
    items = attention_items()
    _key_row(bank, attention_count(items))
    active = q("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE status = 'Active'")
    st.caption(f"Total AP outstanding {php(get_total_ap_outstanding())} · Active checks in circulation {int(active['n'][0])} · {php(active['total'][0])}")
    if bank is not None and bank["age_days"] > 14:
        st.caption(f":material/history: The latest bank balance is from {bank['as_of']:%b %d, %Y} ({bank['age_days']} days ago) — import a recent statement for current figures.")

    _attention_list(items)

    # TRIAL C (2026-09-14): calendar heatmap — hover a day to see that day's all-tracks total,
    # instead of picking a date (Trial A) or reading it off the issue-check grid (Trial B). A
    # third option to compare, not a replacement — drop this block if it doesn't win.
    with st.container(border=True):
        st.subheader("Payment calendar (all tracks)")
        st.caption("Hover a day for its total. Blue = still to pay (darker = more), green = paid (days that have passed), outlined = today, grey = holiday or marked date.")
        cal_year = st.session_state.get("cal_year", manila_today().year)
        cal_month = st.session_state.get("cal_month", manila_today().month)
        cal_month_names = [date(2000, m, 1).strftime("%B") for m in range(1, 13)]
        # Wide enough to cover the postdated-check horizon this business actually schedules
        # against (the payables trend chart above already runs years out), padded a bit past
        # today on both sides rather than tied to whatever data happens to exist right now.
        cal_year_options = list(range(manila_today().year - 5, manila_today().year + 11))
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

        cal_merged = attach_calendar_totals(cal_grid_df)
        cal_merged["is_today"] = cal_merged["date"] == manila_today()

        # Greyed-out dates (2026-09-14): Philippine holidays (seeded, migration 024) and any
        # custom date the user has marked — both stored in calendar_blocked_dates, rendered as
        # a flat grey layer that overrides the heat color for just those cells.
        cal_blocked = get_blocked_dates_for_month(cal_year, cal_month)
        cal_merged["is_blocked"] = cal_merged["date"].isin(cal_blocked.keys())
        cal_merged["blocked_label"] = cal_merged["date"].apply(lambda d: cal_blocked.get(d, {}).get("label") or cal_blocked.get(d, {}).get("kind", "").title())
        cal_normal_df = cal_merged[~cal_merged["is_blocked"] & ~cal_merged["is_paid"]]
        cal_paid_df = cal_merged[~cal_merged["is_blocked"] & cal_merged["is_paid"]]
        cal_blocked_df = cal_merged[cal_merged["is_blocked"]]

        cal_tooltip = [
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("total_amount:Q", title="Total", format=",.2f"),
            alt.Tooltip("num_items:Q", title="Items"),
            alt.Tooltip("status:N", title="Status"),
        ]
        cal_tooltip_blocked = cal_tooltip + [alt.Tooltip("blocked_label:N", title="Marked")]
        # Transposed (2026-09-17) to the conventional calendar layout -- weekday across the top
        # as column headers, week running down as rows -- rather than weekday-as-row/week-as-
        # column, which read more like a generic pivot table than a calendar at a glance.
        cal_weekday_axis = alt.Axis(orient="top", labelAngle=0)
        cal_heat = alt.Chart(cal_normal_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4).encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            color=alt.Color("total_amount:Q", title="Payable", scale=alt.Scale(scheme="blues", type="sqrt", domain=[0, max(1.0, float(cal_merged["total_amount"].max()))]), legend=None),
            tooltip=cal_tooltip,
        )
        cal_paid_heat = alt.Chart(cal_paid_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4).encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            color=alt.Color("total_amount:Q", title="Paid", scale=alt.Scale(scheme="greens", type="sqrt", domain=[0, max(1.0, float(cal_merged["total_amount"].max()))]), legend=None),
            tooltip=cal_tooltip,
        )
        cal_today = alt.Chart(cal_merged[cal_merged["is_today"]]).mark_rect(fill=None, stroke="#1f3a5f", strokeWidth=3, cornerRadius=4).encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis), y=alt.Y("week:O", title=None, axis=None),
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
        cal_combined = (cal_heat + cal_paid_heat + cal_grey + cal_today + cal_text).resolve_scale(color="independent").add_params(cal_click)
        cal_event = st.altair_chart(cal_combined.properties(height=220), width="stretch", on_select="rerun", key="cal_chart")

        cal_picked_date = None
        cal_sel = cal_event.selection.get("cal_pick") if cal_event and cal_event.selection else None
        if cal_sel:
            cal_picked_date = date(cal_year, cal_month, int(cal_sel[0]["day"]))

        if cal_picked_date is not None:
            cal_day_items = get_payment_calendar_items()
            if not cal_day_items.empty:
                cal_day_items = cal_day_items[pd.to_datetime(cal_day_items["actual_payment_date"]).dt.date == cal_picked_date]
            with st.container(border=True):
                st.markdown(f"**{cal_picked_date.strftime('%B %d, %Y')} — breakdown**")
                if cal_day_items.empty:
                    st.caption("Nothing was due on this date.")
                else:
                    st.dataframe(
                        cal_day_items[["state", "track", "payee_name", "reference", "amount", "due_date"]],
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

    _next_seven_days()

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
