"""Release checks (2026-10-03): the supplier collects the checks, signs the release slip printed with the statement, and the person handing them over (the Releaser, or Payables)
records it here BY THE SLIP NUMBER: every check on the slip is marked released at once, with who received them and when. The owner, working remotely, sees which slips are still
waiting to be collected. A single check can still be released from the list below (a check that has no slip). Engines: engines/release_slip_service.py, pdc_release_service.py."""
from __future__ import annotations

import streamlit as st

from engines.clock import manila_today
from engines.common import php
from engines.release_slip_service import open_slips, recent_slips, release_slip
from views.shared import MONEY_FORMAT, date_col, page_header, refresh, section


def _slips_to_collect() -> None:
    with st.container(border=True):
        section("Slips to collect")
        st.caption("Each slip was printed with a supplier's statement. When the supplier collects their checks and signs the slip, pick its number here, type who received them, and release.")
        slips = open_slips()
        if slips.empty:
            st.caption("Every printed slip has been collected.")
            return
        st.metric("Printed, not collected", f"{len(slips)} slip(s) · {php(slips['total'].sum())}", f"oldest {int(slips['days_waiting'].max())} day(s)", delta_color="off", delta_arrow="off", border=True)
        st.dataframe(slips[["slip_no", "supplier", "statement", "checks", "total", "printed_on", "days_waiting"]], hide_index=True, width="stretch",
                     column_config={"slip_no": "Slip", "supplier": "Supplier", "statement": "Statement", "checks": st.column_config.NumberColumn("Checks", format="%d"),
                                    "total": st.column_config.NumberColumn("Total", format=MONEY_FORMAT), "printed_on": date_col("Printed"),
                                    "days_waiting": st.column_config.NumberColumn("Days waiting", format="%d")})
        labels = {int(r.slip_id): f"{r.slip_no} · {r.supplier} · {r.checks} check(s) · {php(r.total)}" for r in slips.itertuples()}
        c1, c2, c3 = st.columns([3, 2, 1.4], vertical_alignment="bottom")
        pick = c1.selectbox("Slip", list(labels), format_func=labels.get, key="slip_pick", index=None, placeholder="Pick the slip number the supplier signed")
        who = c2.text_input("Received by", key="slip_who", placeholder="the name on the signed slip")
        when = c3.date_input("Collected on", value=manila_today(), key="slip_date")
        if st.button("Release this slip", icon=":material/outbox:", type="primary", key="slip_go", disabled=pick is None):
            ok, msg = release_slip(int(pick), who, when)
            (refresh(done=msg) if ok else st.error(msg))


def _recent() -> None:
    recent = recent_slips(30)
    if recent.empty:
        return
    with st.expander(f"Collected in the last 30 days ({len(recent)})", icon=":material/history:"):
        st.dataframe(recent[["slip_no", "supplier", "checks", "total", "released_on", "received_by", "released_by"]], hide_index=True, width="stretch",
                     column_config={"slip_no": "Slip", "supplier": "Supplier", "checks": st.column_config.NumberColumn("Checks", format="%d"),
                                    "total": st.column_config.NumberColumn("Total", format=MONEY_FORMAT), "released_on": date_col("Collected"),
                                    "received_by": "Received by", "released_by": "Recorded by"})


def render() -> None:
    from views.checks import render_for_release

    page_header("Release checks", "The supplier collects their checks and signs the release slip. Record it here by the slip number.")
    _slips_to_collect()
    _recent()
    render_for_release()
