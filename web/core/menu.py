"""The pages and the top bar's menu, the same as the Streamlit system's (app.py PAGES and MENU, 2026-10-03), so the team finds everything where it was.

A page that has not moved to Django yet opens a short "coming in stage Dx" page; the stage numbers are those of docs/DJANGO_REBUILD_PROPOSAL.md.
When a stage is built, its page ids are added to BUILT and their views to core.urls (or the module's own app)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    title: str
    icon: str            # a Material Symbols name, e.g. 'shopping_cart'
    stage: str           # the rebuild stage that brings it to Django


PAGES: dict[str, Page] = {
    "home": Page("Home", "home", "D0"),
    "creditors": Page("Creditors", "contacts", "D1"),
    "settings": Page("Settings", "tune", "D1"),
    "price-lists": Page("Price lists", "sell", "D2"),
    "purchase-orders": Page("Purchase orders", "shopping_cart", "D2"),
    "order-statements": Page("Order statements", "description", "D3"),
    "calendar": Page("Calendar", "calendar_month", "D4"),      # replaces Payables due (owner, 2026-10-09)
    "checks": Page("Daily payables", "fact_check", "D4"),      # was "Checks and checkbook": the daily checklist of every track (owner, 2026-10-09)
    "lenders": Page("Lenders", "handshake", "D6"),
    "bank-loans": Page("Bank loans", "account_balance", "D6"),
    "credit-cards": Page("Credit cards", "credit_card", "D7"),
    "bank-cash": Page("Bank and cash", "account_balance_wallet", "D7"),
    "reports": Page("Reports", "summarize", "D8"),
    "analytics": Page("Analytics", "monitoring", "D8"),
    "review": Page("Checks and review", "fact_check", "D8"),     # Roles and controls + Admin and audit + Data health, combined (owner, 2026-10-09)
}

BUILT = {"home", "creditors", "settings", "price-lists", "purchase-orders", "order-statements", "calendar",
         "reports", "checks", "bank-cash", "lenders", "analytics", "bank-loans", "credit-cards", "review"}     # drawn by Django (D0-D3; Reports: the schedule of payments first; Checks: waiting for the bank + the cleared tick, 2026-10-08)

STAGE_NAME = {"D1": "Masters", "D2": "Buying", "D3": "Payables", "D4": "Paying", "D5": "Release", "D6": "Loans", "D7": "Cards and bank", "D8": "Oversight"}

# (label, icon, page id, query, needed action): a shortcut that opens a page already in a mode, shown only to a role allowed that action
MENU: dict[str, list] = {
    "Buy": ["price-lists", "purchase-orders", ("Receive goods", "inventory_2", "purchase-orders", "mode=receive", "po.receive_goods")],
    "Pay suppliers": [("Match invoices", "receipt_long", "purchase-orders", "mode=match", "po.receive"),
                      "order-statements", "checks", "calendar"],     # Payment run, Release checks and Add data removed (owner, 2026-10-09): its work is in the batch and Daily payables
    "Bank": ["bank-cash"],     # the Check-date planner dropped with /more/ (owner, 2026-10-09)
    "Other payables": ["lenders", "bank-loans", "credit-cards"],
    "Records": ["reports"],
    "Analytics": [("Payables", "receipt_long", "analytics", "tab=payables", "reports.use"), ("Lenders", "handshake", "analytics", "tab=lenders", "reports.use"),
                  ("Bank and cash", "account_balance", "analytics", "tab=bank", "reports.use"), ("Purchasing", "shopping_cart", "analytics", "tab=purchasing", "reports.use")],
    "Set-up": ["creditors", "settings", "review"],
}

# each top-bar section's row of tiles on Home (engines/home_tiles.py GROUPS): clicking the section name goes there, resting on it opens its pages (owner, 2026-10-03:
# "combine" the top bar and Home's blue group bar)
SECTION_GROUP = {"Buy": "buy", "Pay suppliers": "pay", "Bank": "bank", "Other payables": "other", "Records": "records", "Analytics": "analytics", "Set-up": "system"}

# the Home tiles' page states (engines/home_tiles.py, written for Streamlit's widget keys) in this site's words
TILE_QUERY = {("po_mode", "Receive"): "mode=receive", ("po_mode", "Match invoice"): "mode=match",
              **{("analytics_tab", t): f"tab={t}" for t in ("payables", "lenders", "bank", "purchasing")}}


def page_url(page_id: str, query: str = "") -> str:
    """Where a menu entry or a Home tile goes: the Django page; on the server, a page not moved yet opens straight in the Streamlit screens
    (go-live side by side, 2026-10-03), which run on the same database."""
    from django.conf import settings
    if page_id not in BUILT and getattr(settings, "STREAMLIT_URL", ""):
        return f"{settings.STREAMLIT_URL.rstrip('/')}/{page_id}"
    return f"/{page_id}/" + (f"?{query}" if query else "")


def installed_pages() -> set[str]:
    """Every page id this site knows; the planner only while the cash forecast is switched on (Settings), as in Streamlit."""
    from engines.settings_registry import forecast_shown
    ids = set(PAGES)
    if not forecast_shown():
        ids.discard("planner")
    return ids


def pages_for(role: str | None) -> set[str]:
    from engines.permissions import allowed_pages
    return allowed_pages(role, installed_pages())


def menu_for(role: str | None) -> list[dict]:
    """The top bar for this role: [{'title', 'url', 'items': [{'label', 'icon', 'url', 'page'}]}], sections left empty dropped. 'url' = the section's tiles on Home."""
    from engines.permissions import can
    mine = pages_for(role)
    out = []
    for title, entries in MENU.items():
        items = []
        for e in entries:
            if isinstance(e, tuple):
                label, icon, pid, query, needs = e
                if pid in mine and (needs is None or can(role, needs)):
                    items.append({"label": label, "icon": icon, "url": page_url(pid, query), "page": pid})
            elif e in mine:
                items.append({"label": PAGES[e].title, "icon": PAGES[e].icon, "url": page_url(e), "page": e})
        if items:
            out.append({"title": title, "url": f"/#g-{SECTION_GROUP[title]}", "items": items})
    return out
