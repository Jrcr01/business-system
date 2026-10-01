"""Settings page (System menu, 2026-10-01, plan D1 / C / D6): every adjustable number in one place, and the keyboard shortcuts.
Each number shows what it does and its default; a change is audited (old -> new) and "Back to default" undoes it. The logic is in
engines/settings_registry.py (the screens and engines read the numbers through it)."""
from __future__ import annotations

import streamlit as st

from engines import settings_registry as sr
from views.shared import page_header, refresh, section


def _numbers_tab() -> None:
    st.caption("Change a number and press **Save**. The grey line shows what it was before this page existed (the default). Every change is written to the audit log.")
    for group in sr.GROUPS:
        section(group)
        for m in [x for x in sr.registry() if x["group"] == group]:
            key = m["key"]
            cur = sr.value(key)
            c1, c2, c3, c4 = st.columns([3.4, 1.7, 1.5, 2], vertical_alignment="bottom")
            step = 1.0 if m["decimals"] == 0 else (0.1 if m["decimals"] == 1 else 0.01)
            if m["unit"] == "₱":
                step = 100.0
            new = c1.number_input(f"{m['label']} ({m['unit']})", min_value=float(m["min"]), max_value=float(m["max"]), value=float(cur), step=step,
                                  format="%.0f" if m["decimals"] == 0 else f"%.{m['decimals']}f", key=f"set_{key}", help=m["help"])
            c2.caption(f"Default: {m['default']:,}" if m["decimals"] == 0 else f"Default: {float(m['default']):,.{m['decimals']}f}")
            if c3.button("Save", key=f"set_save_{key}", icon=":material/save:", disabled=abs(new - cur) < 1e-9, width="stretch"):
                ok, msg = sr.save(key, new)
                if ok:
                    refresh(msg)
                st.error(msg)
            if c4.button("Back to default", key=f"set_reset_{key}", disabled=abs(cur - m["default"]) < 1e-9, width="stretch"):
                _, msg = sr.reset(key)
                refresh(msg)
        st.divider()


def _shortcuts_tab() -> None:
    st.caption("Choose the keys. Plain letters are not allowed (they would fire while you type), and a key already used by another action is refused. "
               "The change shows on the next page you open.")
    cur = sr.shortcuts()
    new: dict = {}
    for action, text in sr.SHORTCUT_ACTIONS.items():
        c1, c2, c3 = st.columns([4, 2, 1.2], vertical_alignment="bottom")
        c1.markdown(f"**{text}**")
        mod = c2.selectbox("Modifier", sr.MODIFIERS, index=sr.MODIFIERS.index(cur[action]["mod"]), key=f"sc_mod_{action}", label_visibility="collapsed")
        key = c3.text_input("Key", value=cur[action]["key"], max_chars=1, key=f"sc_key_{action}", label_visibility="collapsed")
        new[action] = {"mod": mod, "key": key}
    c1, c2, _ = st.columns([4, 2, 1.2], vertical_alignment="bottom")
    c1.markdown("**Open a menu page** (the modifier goes with the digits 1 … 9, 0)")
    pm = [m for m in sr.MODIFIERS if m != "None"]
    new["pages"] = {"mod": c2.selectbox("Modifier for the menu digits", pm, index=pm.index(cur["pages"]["mod"]), key="sc_mod_pages", label_visibility="collapsed")}
    bad = sr.problems({**new, **{a: {"mod": new[a]["mod"], "key": new[a]["key"]} for a in sr.SHORTCUT_ACTIONS}})
    for p in bad:
        st.error(p)
    b1, b2, _ = st.columns([1.6, 1.6, 4])
    if b1.button("Save shortcuts", icon=":material/save:", disabled=bool(bad), type="primary", width="stretch"):
        ok, msg = sr.save_shortcuts(new)
        if ok:
            refresh(msg)
        st.error(msg)
    if b2.button("Reset to defaults", icon=":material/restart_alt:", width="stretch"):
        _, msg = sr.reset_shortcuts()
        refresh(msg)
    st.markdown("**Fixed keys** (always the same): **Enter** = Yes in a pop-up, **Esc** = go back, **Tab / Shift+Tab** = next / previous box.")


def render() -> None:
    page_header("Settings", "Adjustable numbers and keyboard shortcuts. Nothing here changes a record — only how the system warns, forecasts and checks.")
    tab_numbers, tab_keys = st.tabs(["Numbers", "Keyboard shortcuts"])
    with tab_numbers:
        _numbers_tab()
    with tab_keys:
        _shortcuts_tab()
