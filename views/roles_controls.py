"""Roles and controls (2026-10-03): for the System admin and the owners, read-only. Who can do what (the access matrix), the controls the system applies and what each does,
and what is on the Review list's rules. Nothing here changes anything; it exists so nobody has to guess why a button is there or not."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.controls import CONTROLS, KIND_LABEL
from views.shared import page_header


def render() -> None:
    from views.settings import _matrix_tab

    page_header("Roles and controls", "Who can do what, and what the system checks. Read-only.")
    tab_who, tab_controls, tab_ok = st.tabs(["Who can do what", "Controls", "Is main OK?"])
    with tab_who:
        _matrix_tab()
    with tab_controls:
        st.caption("Controls guide and record; they rarely block. Only four things are ever refused. Everything else goes through, and anything worth a second look lands on the "
                   "Review list on Home for the System admin or an owner to tick off.")
        df = pd.DataFrame(CONTROLS, columns=["process", "kind", "control", "what", "test"])
        df["kind"] = df["kind"].map(KIND_LABEL)
        kinds = st.multiselect("Show", list(KIND_LABEL.values()), default=list(KIND_LABEL.values()), key="ctl_kinds")
        st.dataframe(df[df["kind"].isin(kinds)][["process", "kind", "control", "what"]], hide_index=True, width="stretch",
                     column_config={"process": "Process", "kind": "What happens", "control": "Control", "what": st.column_config.TextColumn("In plain words", width="large")})
    with tab_ok:
        _main_ok()


def _tick(key: str, on: bool) -> None:
    from engines.main_ok import tick
    ok, msg = tick(key, on)
    st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")


def _main_ok() -> None:
    from engines.main_ok import progress, status
    from views.login import can_now
    done, total = progress()
    st.caption("The live AP keeps running until you say this system is OK. Then the live data is moved in, with a backup and your go-ahead. These are the things to see first. "
               "Only the System admin ticks them.")
    st.progress(done / total, text=f"{done} of {total} done")
    for it in status():
        st.checkbox(it["text"] + (f"  — {it['by']}, {it['on']}" if it["done"] else ""), value=it["done"], key=f"mainok_{it['key']}", disabled=not can_now("settings.edit"),
                    on_change=lambda k=it["key"]: _tick(k, st.session_state[f"mainok_{k}"]))
