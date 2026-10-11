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
    "stock": Page("Items and stock", "inventory_2", "R-A"),         # the revenue cycle, stage A (owner, 2026-10-11)
    "stock-counts": Page("Stock counts", "fact_check", "R-A"),
    "affiliates": Page("Affiliates", "handshake", "R-A"),
    "sales": Page("Sales", "point_of_sale", "R-A"),
    "customers": Page("Customers", "group", "R-A"),
    "si-booklets": Page("Invoice booklets", "receipt_long", "R-A"),
    "booklets": Page("Check booklets", "menu_book", "D7"),          # add, change, hand over, gap audit (owner, 2026-10-11)
    "reports": Page("Reports", "summarize", "D8"),
    "analytics": Page("Analytics", "monitoring", "D8"),
    "review": Page("Checks and review", "fact_check", "D8"),     # Roles and controls + Admin and audit + Data health, combined (owner, 2026-10-09)
}

BUILT = {"stock", "stock-counts", "affiliates", "sales", "customers", "si-booklets", "home", "creditors", "settings", "price-lists", "purchase-orders", "order-statements", "calendar",
         "reports", "checks", "bank-cash", "booklets", "lenders", "analytics", "bank-loans", "credit-cards", "review"}     # drawn by Django (D0-D3; Reports: the schedule of payments first; Checks: waiting for the bank + the cleared tick, 2026-10-08)

STAGE_NAME = {"R-A": "Sell and stock", "D1": "Masters", "D2": "Buying", "D3": "Payables", "D4": "Paying", "D5": "Release", "D6": "Loans", "D7": "Cards and bank", "D8": "Oversight"}

# (label, icon, page id, query, needed action): a shortcut that opens a page already in a mode, shown only to a role allowed that action (a tuple: any of them)
MENU: dict[str, list] = {
    "Buy": ["price-lists", "purchase-orders", ("Receive", "inventory_2", "purchase-orders", "mode=receive", ("po.receive_goods", "po.receive"))],   # one page (owner, 2026-10-11)
    "Stock": ["stock", "stock-counts", "affiliates"],
    "Sell": [("Scan out", "barcode_scanner", "sales", "view=scan", "sales.use"), ("Record the day's invoices", "receipt_long", "sales", "", "sales.use"),
             ("Sales today", "today", "sales", "view=day", "sales.use"), "customers", "si-booklets"],
    "Pay suppliers": ["order-statements", "calendar", "checks"],     # Payment run, Release checks and Add data removed (owner, 2026-10-09): its work is in the batch and Daily payables
    "Bank": ["bank-cash", "booklets"],     # the Check-date planner dropped with /more/ (owner, 2026-10-09)
    "Other payables": ["lenders", "bank-loans", "credit-cards"],
    "Records": ["reports"],
    "Analytics": [("Purchasing", "shopping_cart", "analytics", "tab=purchasing", "reports.use"), ("Payables", "receipt_long", "analytics", "tab=payables", "reports.use"),
                  ("Bank and cash", "account_balance", "analytics", "tab=bank", "reports.use"), ("Lenders", "handshake", "analytics", "tab=lenders", "reports.use")],
    "Set-up": ["creditors", "settings", "review"],
}

# each top-bar section's row of tiles on Home (engines/home_tiles.py GROUPS): clicking the section name goes there, resting on it opens its pages (owner, 2026-10-03:
# "combine" the top bar and Home's blue group bar)
SECTION_GROUP = {"Stock": "stock", "Sell": "sell", "Buy": "buy", "Pay suppliers": "pay", "Bank": "bank", "Other payables": "other", "Records": "records", "Analytics": "analytics", "Set-up": "system"}

# the Home tiles' page states (engines/home_tiles.py, written for Streamlit's widget keys) in this site's words
TILE_QUERY = {("po_mode", "Receive"): "mode=receive", ("po_mode", "Match invoice"): "mode=match", ("sales_view", "day"): "view=day", ("sales_view", "released"): "view=released", ("sales_view", "scan"): "view=scan",
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
    from engines.features import off_pages
    return ids - off_pages()                                 # the revenue cycle stays on the sandbox until the owner releases it (engines/features.py)


def pages_for(role: str | None) -> set[str]:
    """The pages this person may open (owners: every page but Set-up, 2026-10-09)."""
    from engines.permissions import viewable_pages
    return viewable_pages(role, installed_pages())


def doable_pages(role: str | None) -> set[str]:
    """The pages this person may also change things on; a page in pages_for() but not here is view only."""
    from engines.permissions import allowed_pages
    return allowed_pages(role, installed_pages())


def menu_for(role: str | None) -> list[dict]:
    """The top bar for this role: [{'title', 'url', 'items': [{'label', 'icon', 'url', 'page'}]}], sections left empty dropped. 'url' = the section's tiles on Home."""
    from engines.permissions import PAGE_ACTION, analytics_tabs, can
    mine = pages_for(role)
    tabs = analytics_tabs()
    out = []
    for title, entries in MENU.items():
        items = []
        for e in entries:
            if isinstance(e, tuple):
                label, icon, pid, query, needs = e
                wanted = needs if isinstance(needs, tuple) else (needs,)
                if pid == "analytics" and query.removeprefix("tab=") not in tabs:     # its page is hidden from this person: so is its dashboard
                    continue
                if pid in mine and (needs is None or PAGE_ACTION.get(pid) in wanted or any(can(role, n) for n in wanted)):     # the page's own action: seeing it is enough
                    items.append({"label": label, "icon": icon, "url": page_url(pid, query), "page": pid})
            elif e in mine:
                items.append({"label": PAGES[e].title, "icon": PAGES[e].icon, "url": page_url(e), "page": e})
        if items:
            out.append({"title": title, "url": f"/#g-{SECTION_GROUP[title]}", "items": items})
    return out
