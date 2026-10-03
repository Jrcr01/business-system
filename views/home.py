"""Home: the launchpad (2026-10-01; restyled 2026-10-02 after the SAP Fiori / EY Mercury launchpad the owner pointed to, in Slick's own colours).

  * a top bar: the Slick's logo, "Home", and "Welcome, <name>" with the role;
  * a yellow reminder strip, only when something needs attention (the most urgent items) or the owner has typed a notice for everyone (Settings);
  * a blue bar with the groups (Daily work, Purchasing ...): pressing one jumps to its row of tiles;
  * a row of big yellow tiles per group: name at the top, icon at the bottom left, the live figure at the bottom right; a red mark when it is urgent.

Nothing is written into this file: the tiles, their groups and their figures come from engines/home_tiles.py, so a new module adds its tiles there and Home
picks them up."""
from __future__ import annotations

import base64
import io
import os
import re
from html import escape

import streamlit as st

from engines.home_tiles import GROUPS, TILES, evaluate, layout, panels_enabled
from views.shared import PAGES

BLUE, YELLOW, RED = "#0B4F8A", "#F3E03B", "#C62828"
ACCENT = {"none": "#1f2937", "ok": "#0b6e4f", "warn": "#9a3412", "alert": RED}
LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "slicks_logo.jpg")

CSS = f"""
<style>
[data-testid="stMainBlockContainer"] {{ padding-top: 1.2rem; }}          /* Streamlit leaves ~6rem empty above every page; Home starts right under the top edge */
.home-top {{ display:flex; align-items:center; gap:18px; background:#ffffff; border-bottom:1px solid #e5e7eb; padding:6px 4px 10px 4px; }}
.home-top img {{ height:46px; border-radius:6px; }}
.home-top .ttl {{ font-size:1.35rem; font-weight:600; color:#111827; }}
.home-top .who {{ color:#4b5563; font-size:.95rem; }}
.home-top .role {{ margin-left:auto; color:#4b5563; font-size:.85rem; background:#f3f4f6; border-radius:999px; padding:4px 12px; }}
.home-notice {{ background:{YELLOW}; color:#111827; border-radius:6px; padding:8px 14px; margin:10px 0 0 0; font-size:.92rem; }}
.home-notice b.r {{ color:{RED}; }}
.home-notice a {{ color:{BLUE}; font-weight:600; }}
.home-groups {{ display:flex; flex-wrap:wrap; gap:4px 26px; background:{BLUE}; border-radius:6px; padding:10px 18px; margin:10px 0 6px 0; }}
.home-groups a {{ color:#ffffff !important; text-decoration:none !important; font-size:1rem; opacity:.92; }}
.home-groups a:first-child {{ font-weight:700; letter-spacing:.04em; opacity:1; }}
.home-groups a:hover {{ opacity:1; text-decoration:underline !important; }}
.home-sec {{ color:#374151; font-size:1.15rem; margin:18px 0 2px 4px; scroll-margin-top:70px; }}
.home-sec span {{ color:#6b7280; font-size:.82rem; margin-left:10px; }}
.home-line {{ border:none; border-top:1px solid #d1d5db; margin:4px 0 12px 0; }}
div[class*="st-key-grid_"] {{ display:grid !important; grid-template-columns:repeat(auto-fill, minmax(172px, 1fr)); gap:14px; margin-bottom:6px; }}
div[class*="st-key-grid_"] > div {{ width:100% !important; }}
[class*="st-key-tile_"] {{ position:relative; background:{YELLOW}; border-radius:10px; height:150px; min-height:150px; box-shadow:0 1px 3px rgba(0,0,0,.18);
                          transition:transform .12s ease, box-shadow .12s ease; overflow:hidden; }}
[class*="st-key-tile_"]:hover {{ transform:translateY(-3px); box-shadow:0 8px 16px rgba(0,0,0,.22); }}
[class*="st-key-tile_"] .stElementContainer:has([data-testid="stPageLink"]), [class*="st-key-tile_"] .stElementContainer:has(button) {{ position:static !important; }}
[class*="st-key-tile_"] [data-testid="stPageLink"], [class*="st-key-tile_"] [data-testid="stPageLink"] a,
[class*="st-key-tile_"] .stButton, [class*="st-key-tile_"] .stButton button {{ position:absolute; inset:0; z-index:5; opacity:0; width:100%; height:100%; }}
[class*="st-key-tile_"] [data-testid="stHtml"], [class*="st-key-tile_"] [data-testid="stHtml"] > div {{ height:100%; }}
.home-tile {{ height:150px; box-sizing:border-box; padding:12px 14px 12px 14px; display:flex; flex-direction:column; justify-content:space-between; border-left:5px solid transparent; }}
.home-tile .t {{ font-size:.95rem; line-height:1.25; color:#1f2937; font-weight:700; }}
.home-tile .b {{ display:flex; align-items:flex-end; justify-content:space-between; }}
.home-tile .i {{ font-family:'Material Symbols Rounded'; font-size:32px; color:#1f2937; line-height:1; font-variation-settings:'FILL' 0; }}
.home-tile .f {{ text-align:right; }}
.home-tile .f b {{ font-size:1.7rem; font-weight:600; line-height:1; display:block; }}
.home-tile .f span {{ font-size:.75rem; color:#374151; }}
.home-panel-fig {{ font-size:.95rem; margin:-6px 0 10px 0; }}
@media (max-width:640px) {{ div[class*="st-key-grid_"] {{ grid-template-columns:repeat(2, 1fr); }} .home-top .role {{ display:none; }} }}
</style>
"""


@st.cache_data(ttl=60, show_spinner=False)
def _figures(page_ids: tuple, role: str | None) -> dict:
    """Every tile's figure, worked out once a minute so Home opens at once (the heavy ones are also remembered by the engines until something is saved)."""
    return {t.tid: evaluate(t) for t in TILES if t.tid in page_ids}


@st.cache_resource
def _logo_uri() -> str:
    """The Slick's logo, made small once, as a data URI for the top bar."""
    try:
        from PIL import Image
        im = Image.open(LOGO).convert("RGB")
        im.thumbnail((180, 180))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _icon_name(icon: str) -> str:
    m = re.match(r":material/([a-z0-9_]+):", icon or "")
    return m.group(1) if m else "apps"


def _tile_html(title: str, icon: str, info: dict) -> str:
    color, level = ACCENT.get(info["level"], ACCENT["none"]), info["level"]
    side = f"border-left-color:{RED};" if level == "alert" else (f"border-left-color:{BLUE};" if level == "warn" else "")
    figure = (f'<b style="color:{color}">{escape(info["figure"])}</b><span>{escape(info["unit"])}</span>' if info["figure"]
              else f'<span>{escape(info["unit"])}</span>')
    return (f'<div class="home-tile" style="{side}"><div class="t">{escape(title)}</div>'
            f'<div class="b"><div class="i">{_icon_name(icon)}</div><div class="f">{figure}</div></div></div>')


def _notices(role: str | None) -> list[str]:
    """The reminder strip: the owner's notice for everyone, then (for the roles that handle payables) the most urgent things waiting."""
    from engines.settings_store import get_setting
    out = []
    note = (get_setting("home_notice", "") or "").strip()
    if note:
        out.append(f"<b>Notice:</b> {escape(note)}")
    if role in (None, "admin", "owner", "payables"):
        try:
            from engines.attention_service import attention_items
            urgent = [i for i in attention_items() if i["level"] == "error"]
        except Exception:
            urgent = []
        if urgent:
            more = f" (and {len(urgent) - 1} more below)" if len(urgent) > 1 else ""
            out.append(f'<b class="r">Needs attention:</b> {escape(urgent[0]["text"])}{escape(more)}')
    from engines.permissions import can
    if can(role or "admin", "check.release"):
        try:
            from engines.release_slip_service import waiting_over
            late = waiting_over(7)
        except Exception:
            late = 0
        if late:
            out.append(f"<b class=\"r\">{late} release slip(s)</b> printed but not collected after 7 days")
    if can(role or "admin", "review.use"):
        from engines.review_flags import open_count
        n = open_count()
        if n:
            out.append(f"<b>{n} to review</b> (below)")
    return out


LEVEL_ICON = {"error": ":red[:material/error:]", "warning": ":orange[:material/warning:]", "info": ":blue[:material/info:]"}


def _attention_box() -> None:
    """What the Dashboard's 'Needs attention' list was: one list of what needs a person, each line with the button that opens the page that fixes it."""
    from engines.attention_service import attention_items
    from views.shared import go_to
    try:
        items = attention_items()
    except Exception:
        return
    if not items:
        return
    count = sum(1 for i in items if i["level"] in ("error", "warning"))
    with st.expander(f"Needs attention ({count})", icon=":material/notification_important:", expanded=False):
        for n, item in enumerate(items):
            c1, c2 = st.columns([8, 1.3], vertical_alignment="center")
            c1.markdown(f"{LEVEL_ICON.get(item['level'], '')} {item['text']}")
            if c2.button("Open", key=f"home_attn_{n}", icon=":material/arrow_forward:", width="stretch"):
                go_to(item["page"])


def _todo_box(role: str | None) -> None:
    """My to-do: only the steps this person may do now (engines/my_todo.py), each opening the page and mode that does it."""
    from engines.my_todo import items
    todo = items(role)
    if not todo:
        return
    with st.container(border=True):
        st.markdown("**My to-do**")
        for n, it in enumerate(todo):
            c1, c2 = st.columns([8, 1.6], vertical_alignment="center")
            c1.markdown(f"{it['text']}")
            c2.button("Open", key=f"home_todo_{n}", icon=it["icon"], width="stretch", on_click=_jump_cb, args=(it["page"], it["state"]))


def _review_ok(flag_id: int) -> None:
    from engines.review_flags import acknowledge
    ok, msg = acknowledge(flag_id)
    st.toast(msg, icon=":material/check_circle:" if ok else ":material/error:")


def _review_box() -> None:
    """The Review list: what the controls flagged (a duty overlap, a price far above its order, a new payee). Nothing was blocked; the System admin or an owner ticks each line OK."""
    from engines.review_flags import KINDS, open_flags
    flags = open_flags()
    if flags.empty:
        return
    with st.expander(f"To review ({len(flags)})", icon=":material/fact_check:"):
        st.caption("These went through and were noted. Look at them when you like and tick each one OK.")
        for _, f in flags.iterrows():
            c1, c2 = st.columns([6, 1], vertical_alignment="center")
            c1.markdown(f"**{escape(KINDS.get(f['kind'], f['kind']))}** · {escape(f['detail'])}  \n:gray[{escape(str(f['raised_by'] or ''))} · {escape(str(f['raised_at'] or '')[:16])}]")
            c2.button("OK", key=f"review_ok_{f['flag_id']}", on_click=_review_ok, args=(int(f["flag_id"]),), icon=":material/check:")


def _jump_cb(page_id: str, state: dict) -> None:
    """A tile that opens its page already set to a mode (Receive goods, Match invoices): a button callback, so the page change happens on the rerun that follows it."""
    st.session_state["_global_jump"] = {"page": page_id, "state": dict(state)}


def _open(page_id: str, state: dict | None = None) -> None:
    """Open a page with some widgets pre-set, through the same hand-over go_to() and the search pop-up use. A button inside a pop-up only re-runs the
    pop-up, so the whole app is re-run here (app.py reads the hand-over before any page draws)."""
    st.session_state["_global_jump"] = {"page": page_id, "state": dict(state or {})}
    st.rerun()


@st.dialog("What do you want to do?", width="small")
def _panel(page_id: str) -> None:
    """The tile's action panel: one wide button per action, each opening its page with the right mode already chosen, and the whole page last."""
    from views.login import current_role
    tile = next(t for t in TILES if t.page_id == page_id)
    info = _figures((page_id,), current_role()).get(page_id) or evaluate(tile)
    st.markdown(f"**{escape(tile.title)}**" + (f" — {escape(info['figure'])} {escape(info['unit'])}" if info["figure"] else ""))
    for i, a in enumerate(tile.visible_actions):
        hint = ((("\n\n" + a.hint) if i == 0 else ("\n\n:gray[" + a.hint + "]")) if a.hint else "")
        if st.button(f"**{a.label}**" + hint, key=f"act_{page_id}_{i}", icon=a.icon or None, width="stretch", type="primary" if i == 0 else "secondary"):
            _open(a.page_id or page_id, dict(a.state))
    st.divider()
    if st.button("Open the whole page", key=f"act_{page_id}_all", icon=":material/open_in_new:", width="stretch"):
        _open(page_id)


def render() -> None:
    from engines.permissions import ROLE_LABEL
    from views.login import current_role, current_user

    st.markdown(CSS, unsafe_allow_html=True)
    user, role = current_user() or {}, current_role()
    name = (user.get("name") or "").strip() or "there"
    logo = _logo_uri()
    st.html(f'<div class="home-top">{f"<img src={chr(34)}{logo}{chr(34)} alt=logo>" if logo else ""}<div class="ttl">Home</div>'
            f'<div class="who">Welcome, {escape(name)}</div><div class="role">{escape(ROLE_LABEL.get(role or "admin", role or ""))}</div></div>')
    for note in _notices(role):
        st.html(f'<div class="home-notice">{note}</div>')
    from views.login import can_now
    if role != "viewer":
        _todo_box(role)
    if role in (None, "admin", "owner", "payables"):
        _attention_box()
    if can_now("review.use"):
        _review_box()

    rows = layout(set(PAGES))
    use_panels = panels_enabled()
    figures = _figures(tuple(sorted(t.tid for _, ts in rows for t in ts)), role)
    titles = {g.key: g for g in GROUPS}
    st.html('<div class="home-groups"><a href="#home-top">SLICK\'S</a>' + "".join(f'<a href="#sec-{k}">{escape(titles[k].title)}</a>' for k, _ in rows) + "</div>")
    for gkey, tiles in rows:
        g = titles[gkey]
        st.html(f'<div class="home-sec" id="sec-{gkey}">{escape(g.title)}<span>{escape(g.hint)}</span></div><hr class="home-line">')
        with st.container(key=f"grid_{gkey}"):
            for t in tiles:
                with st.container(key=f"tile_{t.tid}"):
                    page = PAGES[t.page_id]
                    st.html(_tile_html(t.title, t.icon or getattr(page, "icon", "") or "", figures.get(t.tid, {"figure": "—", "unit": "", "level": "none"})))
                    if t.state:
                        st.button(t.title, key=f"open_{t.tid}", on_click=_jump_cb, args=(t.page_id, dict(t.state)))
                    elif t.has_panel and use_panels:
                        if st.button(t.title, key=f"open_{t.page_id}"):
                            _panel(t.page_id)
                    else:
                        st.page_link(page, label=t.title)
