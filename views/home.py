"""Home: a Fiori-style launchpad (2026-10-01). A teal band with a row of white tiles per group; each tile shows the page's name, its icon and one live figure, and
a click anywhere on it opens the page. Nothing is written into this file: the tiles, their groups and their figures come from engines/home_tiles.py, so a new
module adds its tiles there and Home picks them up."""
from __future__ import annotations

import re
from html import escape

import streamlit as st

from engines.home_tiles import GROUPS, TILES, evaluate, layout
from views.shared import PAGES

ACCENT = {"none": "#6b7280", "ok": "#0b6e4f", "warn": "#d97706", "alert": "#c62828"}

CSS = """
<style>
.st-key-home_band { background: linear-gradient(135deg, #0b5f7a 0%, #1783a0 45%, #2fa7a3 100%); border-radius: 14px; padding: 22px 22px 8px 22px; }
.st-key-home_band h3, .st-key-home_band .home-hint { color: #ffffff !important; margin: 6px 0 2px 2px; }
.st-key-home_band .home-hint { opacity: .8; font-size: .85rem; margin-bottom: 8px; }
div[class*="st-key-grid_"] { display: grid !important; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 12px; margin-bottom: 14px; }
div[class*="st-key-grid_"] > div { width: 100% !important; }
[class*="st-key-tile_"] { position: relative; background: #ffffff; border-radius: 10px; height: 124px; min-height: 124px; box-shadow: 0 1px 4px rgba(0,0,0,.25);
                          transition: transform .12s ease, box-shadow .12s ease; overflow: hidden; }
[class*="st-key-tile_"]:hover { transform: translateY(-3px); box-shadow: 0 6px 14px rgba(0,0,0,.3); }
[class*="st-key-tile_"] .stElementContainer:has([data-testid="stPageLink"]), [class*="st-key-tile_"] .stElementContainer:has(button) { position: static !important; }   /* so the link / button below can stretch over the whole tile */
[class*="st-key-tile_"] [data-testid="stPageLink"], [class*="st-key-tile_"] [data-testid="stPageLink"] a,
[class*="st-key-tile_"] .stButton, [class*="st-key-tile_"] .stButton button { position: absolute; inset: 0; z-index: 5; opacity: 0; width: 100%; height: 100%; }
.home-panel-fig { font-size: .95rem; margin: -6px 0 10px 0; }
.home-panel-fig b { font-size: 1.3rem; }
[class*="st-key-tile_"] [data-testid="stHtml"], [class*="st-key-tile_"] [data-testid="stHtml"] > div { height: 100%; }
.home-tile { height: 124px; box-sizing: border-box; padding: 12px 12px 10px 12px; display: flex; flex-direction: column; justify-content: space-between; border-top: 4px solid transparent; }
.home-tile .t { font-size: .85rem; line-height: 1.2; color: #1f2937; font-weight: 500; }
.home-tile .s { font-size: .72rem; color: #6b7280; margin-top: 2px; }
.home-tile .b { display: flex; align-items: flex-end; justify-content: space-between; }
.home-tile .i { font-family: 'Material Symbols Rounded'; font-size: 30px; color: #1783a0; line-height: 1; font-variation-settings: 'FILL' 0; }
.home-tile .f { text-align: right; }
.home-tile .f b { font-size: 1.75rem; font-weight: 500; line-height: 1; display: block; }
.home-tile .f span { font-size: .72rem; color: #6b7280; }
@media (max-width: 640px) { div[class*="st-key-grid_"] { grid-template-columns: repeat(2, 1fr); } }
</style>
"""


@st.cache_data(ttl=60, show_spinner=False)
def _figures(page_ids: tuple) -> dict:
    """Every tile's figure, worked out once a minute so Home opens at once."""
    from engines.home_tiles import TILES
    return {t.page_id: evaluate(t) for t in TILES if t.page_id in page_ids}


def _icon_name(icon: str) -> str:
    m = re.match(r":material/([a-z0-9_]+):", icon or "")
    return m.group(1) if m else "apps"


def _tile_html(title: str, icon: str, info: dict) -> str:
    color, level = ACCENT[info["level"]], info["level"]
    top = f"border-top-color: {color};" if level in ("warn", "alert") else ""
    figure = (f'<b style="color:{color}">{escape(info["figure"])}</b><span>{escape(info["unit"])}</span>' if info["figure"]
              else f'<span>{escape(info["unit"])}</span>')
    return (f'<div class="home-tile" style="{top}"><div class="t">{escape(title)}</div>'
            f'<div class="b"><div class="i">{_icon_name(icon)}</div><div class="f">{figure}</div></div></div>')


def _open(page_id: str, state: dict | None = None) -> None:
    """Open a page with some widgets pre-set, through the same hand-over go_to() and the search pop-up use. A button inside a pop-up only re-runs the
    pop-up, so the whole app is re-run here (app.py reads the hand-over before any page draws)."""
    st.session_state["_global_jump"] = {"page": page_id, "state": dict(state or {})}
    st.rerun()


@st.dialog("What do you want to do?", width="small")
def _panel(page_id: str) -> None:
    """The tile's action panel: one wide button per action, each opening its page with the right mode already chosen, and the whole page last."""
    tile = next(t for t in TILES if t.page_id == page_id)
    info = _figures((page_id,)).get(page_id) or evaluate(tile)
    st.markdown(f"**{escape(tile.title)}**" + (f" — {escape(info['figure'])} {escape(info['unit'])}" if info["figure"] else ""))
    for i, a in enumerate(tile.actions):
        hint = ((("\n\n" + a.hint) if i == 0 else ("\n\n:gray[" + a.hint + "]")) if a.hint else "")
        if st.button(f"**{a.label}**" + hint, key=f"act_{page_id}_{i}", icon=a.icon or None, width="stretch", type="primary" if i == 0 else "secondary"):
            _open(a.page_id or page_id, dict(a.state))
    st.divider()
    if st.button("Open the whole page", key=f"act_{page_id}_all", icon=":material/open_in_new:", width="stretch"):
        _open(page_id)


def render() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.header("Home", divider="blue")
    st.caption("What is waiting for you. Press a tile to open that page.")
    rows = layout(set(PAGES))
    figures = _figures(tuple(sorted(t.page_id for _, ts in rows for t in ts)))
    titles = {g.key: g for g in GROUPS}
    with st.container(key="home_band"):
        for gkey, tiles in rows:
            g = titles[gkey]
            st.html(f'<h3>{escape(g.title)}</h3><div class="home-hint">{escape(g.hint)}</div>')
            with st.container(key=f"grid_{gkey}"):
                for t in tiles:
                    with st.container(key=f"tile_{t.page_id}"):
                        page = PAGES[t.page_id]
                        st.html(_tile_html(t.title, t.icon or getattr(page, "icon", "") or "", figures.get(t.page_id, {"figure": "—", "unit": "", "level": "none"})))
                        if t.has_panel:
                            if st.button(t.title, key=f"open_{t.page_id}"):
                                _panel(t.page_id)
                        else:
                            st.page_link(page, label=t.title)
