"""The top bar (2026-10-03, owner: "remove the sidebar"). One bar across the top of every page: the logo (Home), one drop-down per section that opens when the mouse rests
on it, then search, the payment calendar, reminders and the account on the right. The sidebar is gone; its jobs moved here:
  search ....... Search everything (the same pop-up as the [ / ] key)
  calendar ..... the payment calendar pop-up, with marked days
  reminders .... checks typed but not parked yet
  account ...... who is signed in, Developer view (System admin), change password, sign out, Refresh data, the keyboard shortcuts
A menu entry is a page link, or (Receive goods, Match invoices) a button that opens Purchase orders already in that mode. Hover works with CSS only (a section's list is shown
while the mouse is over it, or a tap has focus inside it on a phone), so the pages still change in-app and nothing is lost on the way."""
from __future__ import annotations

from html import escape

import streamlit as st

CSS = """
<style>
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stHeader"] { display: none !important; }
[data-testid="stMainBlockContainer"] { padding-top: .6rem; }
.stElementContainer:has([data-testid="stMarkdown"]):not(:has(p, li, table, h1, h2, h3, h4, h5, code, img, a, button, details)) { display: none; }     /* style-only blocks leave an empty gap */
.st-key-topbar { display: flex !important; flex-direction: row !important; flex-wrap: wrap; align-items: center; gap: 0 !important; background: #0f2a4a; border-radius: 8px;
                 padding: 0 8px; position: sticky; top: 0; z-index: 1000; margin-bottom: 10px; min-height: 46px; }
.st-key-topbar > div { width: auto !important; flex: 0 0 auto; }
.st-key-topbar a, .st-key-topbar button { color: #fff !important; }
.st-key-tb_home a { font-weight: 800; letter-spacing: .06em; color: #F3E03B !important; padding: 8px 14px 8px 6px; text-decoration: none; }
.st-key-tb_home a p { color: #F3E03B !important; }
[class*="st-key-tb_sec_"] { position: relative; }
[class*="st-key-tb_sec_"] .tb-label { color: #fff; padding: 13px 10px; cursor: pointer; font-size: .88rem; white-space: nowrap; }
[class*="st-key-tb_sec_"]:hover .tb-label, [class*="st-key-tb_sec_"]:focus-within .tb-label { background: #1b3f6b; }
[class*="st-key-tb_menu_"] { display: none; position: absolute; top: 100%; left: 0; min-width: 250px; background: #fff; border-radius: 0 0 8px 8px; padding: 6px 0 !important;
                             box-shadow: 0 10px 24px rgba(0,0,0,.28); z-index: 1001; gap: 0 !important; }
[class*="st-key-tb_sec_"]:hover [class*="st-key-tb_menu_"], [class*="st-key-tb_sec_"]:focus-within [class*="st-key-tb_menu_"] { display: block; }
[class*="st-key-tb_menu_"] a, [class*="st-key-tb_menu_"] button { color: #1c2733 !important; width: 100%; justify-content: flex-start; border: none; background: transparent;
                                                                  padding: 7px 14px; border-radius: 0; box-shadow: none; }
[class*="st-key-tb_menu_"] a p, [class*="st-key-tb_menu_"] button p { color: #1c2733 !important; }
[class*="st-key-tb_menu_"] a:hover, [class*="st-key-tb_menu_"] button:hover { background: #eef3fa; }
.st-key-tb_gap { flex: 1 1 auto !important; }
.st-key-topbar [class*="st-key-tb_icon_"] button, .st-key-topbar [data-testid="stPopover"] button { background: transparent; border: none; color: #fff !important; }
.st-key-topbar [class*="st-key-tb_icon_"] button:hover, .st-key-topbar [data-testid="stPopover"] button:hover { background: #1b3f6b; }
.st-key-topbar [data-testid="stPopover"] button p, .st-key-topbar [class*="st-key-tb_icon_"] button p { color: #fff !important; }
.tb-tag { background: #FFA500; color: #000; font-weight: 700; font-size: .72rem; padding: 3px 8px; border-radius: 4px; margin-right: 8px; white-space: nowrap; }
@media (max-width: 800px) { [class*="st-key-tb_sec_"] .tb-label { padding: 13px 8px; font-size: .8rem; } .st-key-topbar { flex-wrap: wrap; } }
</style>
"""

# Alt+1 ... Alt+0 open these pages, whatever the menu order (fixed 2026-10-03, so a keyboard habit never moves when the menu does)
HOTKEY_PAGES = ["payment-run", "add-data", "order-statements", "checks", "bank-cash", "payables-due", "lenders", "bank-loans", "credit-cards", "reports"]


def _jump_cb(page_id: str, state: dict) -> None:
    st.session_state["_global_jump"] = {"page": page_id, "state": dict(state)}


def render(sections: dict, pages: dict, user: dict, role: str | None, is_sandbox: bool, pending_drafts: list, extras: dict) -> "st.delta_generator.DeltaGenerator":
    """Draws the bar. sections: {title: [entry, ...]} where an entry is a page id or (label, icon, page id, {widget state}). Returns the slot where the 'checks typed but not
    parked' reminder is written at the end of the run."""
    from engines.permissions import can
    from views.login import render_account_box
    from views.payment_calendar import calendar_dialog
    from views.shared import global_search_dialog

    st.markdown(CSS, unsafe_allow_html=True)
    with st.container(key="topbar"):
        with st.container(key="tb_home"):
            if "home" in pages:
                st.page_link(pages["home"], label="SLICK'S")
        for i, (title, entries) in enumerate(sections.items()):
            if entries == ["home"]:
                continue                                                              # Home is the logo
            with st.container(key=f"tb_sec_{i}"):
                st.html(f'<div class="tb-label">{escape(title)} &#9662;</div>')
                with st.container(key=f"tb_menu_{i}"):
                    for e in entries:
                        if isinstance(e, tuple):
                            label, icon, page_id, state, needs = e
                            if needs is None or can(role, needs):
                                st.button(label, key=f"tbm_{i}_{label}", icon=icon, on_click=_jump_cb, args=(page_id, state))
                        elif e in pages:
                            st.page_link(pages[e])
        st.container(key="tb_gap")
        if is_sandbox:
            with st.container(key="tb_tag"):
                st.html('<span class="tb-tag">SANDBOX</span>')
        if extras.get("search"):
            with st.container(key="tb_icon_search"):
                if st.button("", icon=":material/search:", key="tb_search_btn", help="Search everything  [ / ]"):
                    global_search_dialog()
        if extras.get("calendar"):
            with st.container(key="tb_icon_cal"):
                if st.button("", icon=":material/calendar_month:", key="tb_cal_btn", help="Payment calendar"):
                    calendar_dialog()
        with st.container(key="tb_bell"):
            with st.popover(f"{len(pending_drafts)}" if pending_drafts else "", icon=":material/notifications:", help="Reminders"):
                park_draft_slot = st.empty()
                if not pending_drafts:
                    st.caption("Nothing to remind you of.")
        with st.container(key="tb_user"):
            from engines.permissions import ROLE_LABEL
            with st.popover(ROLE_LABEL.get(user["role"], user["role"]), icon=":material/person:", help=user["name"]):
                render_account_box(user)
                st.button("Refresh data", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), width="stretch")
                if extras.get("shortcuts"):
                    with st.expander("Keyboard shortcuts (change them in Settings)", icon=":material/keyboard:"):
                        st.markdown(extras["shortcuts"])
    return park_draft_slot
