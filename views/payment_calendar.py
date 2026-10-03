"""The payment calendar (2026-10-03): ONE component, drawn in two places -- the Calendar tab of Payables due, and a pop-up opened from the top bar of any page.
A month grid: blue = still to pay (darker = more), green = paid, outlined = today. Click a day for what is due that day, and (Owner / Payables) mark it:
red = holiday (a bank day off), orange = no-check day, purple = note (moves nothing). The sidebar mini calendar and the Dashboard's calendar became this."""
from __future__ import annotations

import calendar
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from engines.calendar_marks import KINDS, mark_day, marks_for_month, moved_by_marking, unmark_day
from engines.clock import manila_today
from engines.common import prospective_year
from engines.reports_service import attach_calendar_totals, get_payment_calendar_items
from views.shared import MONEY_FORMAT, refresh

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MARK_COLOR = {"holiday": "#E57373", "custom": "#FFB74D", "note": "#CE93D8"}


def _step(prefix: str, delta: int) -> None:
    m, y = st.session_state[f"{prefix}_m"] + delta, st.session_state[f"{prefix}_y"]
    if m < 1:
        m, y = 12, y - 1
    elif m > 12:
        m, y = 1, y + 1
    st.session_state[f"{prefix}_m"], st.session_state[f"{prefix}_y"] = m, y


def _month_picked(prefix: str) -> None:
    st.session_state[f"{prefix}_y"] = prospective_year(st.session_state[f"{prefix}_m"])


def _mark(prefix: str, day: date, kind: str, label: str) -> None:
    ok, msg = mark_day(day, kind, label)
    st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")
    if ok:
        refresh()


def _unmark(day: date) -> None:
    ok, msg = unmark_day(day)
    st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")
    if ok:
        refresh()


def render(prefix: str = "pcal", height: int = 230) -> None:
    from views.login import can_now
    today = manila_today()
    st.session_state.setdefault(f"{prefix}_y", today.year)
    st.session_state.setdefault(f"{prefix}_m", today.month)
    years = sorted(set(range(today.year - 5, today.year + 11)) | {st.session_state[f"{prefix}_y"]})
    c1, c2, c3, c4 = st.columns([0.8, 2.2, 1.6, 0.8], vertical_alignment="center")
    c1.button("◀", key=f"{prefix}_prev", on_click=_step, args=(prefix, -1))
    c2.selectbox("Month", list(range(1, 13)), format_func=lambda m: date(2000, m, 1).strftime("%B"), key=f"{prefix}_m", label_visibility="collapsed",
                 on_change=_month_picked, args=(prefix,))
    c3.selectbox("Year", years, key=f"{prefix}_y", label_visibility="collapsed")
    c4.button("▶", key=f"{prefix}_next", on_click=_step, args=(prefix, 1))
    year, month = st.session_state[f"{prefix}_y"], st.session_state[f"{prefix}_m"]

    rows = [{"date": date(year, month, n), "week": wi, "weekday": WEEKDAYS[di], "day": n}
            for wi, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(year, month)) for di, n in enumerate(week) if n]
    grid = attach_calendar_totals(pd.DataFrame(rows))
    marks = marks_for_month(year, month)
    grid["mark"] = grid["date"].map(lambda d: marks.get(d, {}).get("kind", ""))
    grid["mark_label"] = grid["date"].map(lambda d: marks.get(d, {}).get("label", ""))
    grid["marked"] = grid["mark"] != ""
    grid["is_today"] = grid["date"] == today
    top = max(1.0, float(grid["total_amount"].max()))
    axis = alt.Axis(orient="top", labelAngle=0, labelFontSize=11)
    x, y = alt.X("weekday:N", title=None, sort=WEEKDAYS, axis=axis), alt.Y("week:O", title=None, axis=None)
    tip = [alt.Tooltip("date:T", title="Date"), alt.Tooltip("total_amount:Q", title="Total", format=",.2f"), alt.Tooltip("num_items:Q", title="Items"),
           alt.Tooltip("mark_label:N", title="Marked")]

    def heat(frame, scheme):
        return alt.Chart(frame).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4).encode(
            x=x, y=y, color=alt.Color("total_amount:Q", scale=alt.Scale(scheme=scheme, type="sqrt", domain=[0, top]), legend=None), tooltip=tip)

    plain, paid = grid[~grid["marked"] & ~grid["is_paid"]], grid[~grid["marked"] & grid["is_paid"]]
    layers = [heat(plain, "blues"), heat(paid, "greens")]
    for kind, colour in MARK_COLOR.items():
        layers.append(alt.Chart(grid[grid["mark"] == kind]).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4, color=colour).encode(x=x, y=y, tooltip=tip))
    layers.append(alt.Chart(grid[grid["is_today"]]).mark_rect(fill=None, stroke="#1f3a5f", strokeWidth=3, cornerRadius=4).encode(x=x, y=y))
    layers.append(alt.Chart(grid).mark_text(baseline="middle", fontSize=12).encode(
        x=x, y=y, text="day:N", tooltip=tip,
        color=alt.when(alt.datum.marked).then(alt.value("#404040")).when(alt.datum.total_amount > grid["total_amount"].max() * 0.5).then(alt.value("white")).otherwise(alt.value("black"))))
    click = alt.selection_point(name="pick", fields=["day"], on="click", empty=False)     # the plain day number: a temporal selection comes back a day off in some time zones
    chart = alt.layer(*layers).resolve_scale(color="independent").add_params(click)
    event = st.altair_chart(chart.properties(height=height), width="stretch", on_select="rerun", key=f"{prefix}_chart")
    st.caption(":blue[Blue] = to pay (darker = more) · :green[green] = paid · outlined = today · :red[red] = holiday · :orange[orange] = no-check day · :violet[purple] = note. Click a day.")

    sel = event.selection.get("pick") if event and event.selection else None
    if not sel:
        return
    day = date(year, month, int(sel[0]["day"]))
    with st.container(border=True):
        st.markdown(f"**{day:%A, %B %d, %Y}**")
        items = get_payment_calendar_items()
        if not items.empty:
            items = items[pd.to_datetime(items["actual_payment_date"]).dt.date == day]
        if items.empty:
            st.caption("Nothing was due on this day.")
        else:
            st.dataframe(items[["state", "track", "payee_name", "reference", "amount"]], hide_index=True,
                         column_config={"amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT)})
        if not can_now("calendar.mark"):
            if day in marks:
                st.info(f"Marked: **{marks[day]['label'] or KINDS[marks[day]['kind']]}**")
            return
        if day in marks:
            m = marks[day]
            st.info(f"Marked: **{m['label'] or KINDS[m['kind']]}** ({KINDS[m['kind']].lower()}).")
            st.button("Unmark this day", icon=":material/clear:", key=f"{prefix}_unmark", on_click=_unmark, args=(day,))
            return
        a, b = st.columns([2, 2])
        label = a.text_input("Label (optional for a holiday or no-check day)", key=f"{prefix}_label", placeholder="e.g. Eid'l Fitr, Supplier visit, Payday")
        kind = b.selectbox("Mark as", list(KINDS), format_func=lambda k: KINDS[k], key=f"{prefix}_kind")
        n = moved_by_marking(day) if kind != "note" else 0
        if n:
            st.warning(f"{n} payment(s) fall on this day. Marking it moves them to the next bank day.")
        st.button("Mark this day", icon=":material/event_busy:", key=f"{prefix}_mark", on_click=_mark, args=(prefix, day, kind, label), type="primary")


@st.dialog("Payment calendar", width="large")
def calendar_dialog() -> None:
    render("pcd")
