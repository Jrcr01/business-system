"""What each role can open and see (B8). The owner (Payables) has everything. Purchasing (the owner's brother) has Home, Purchase orders and Price lists, and no
bank, check, batch or AP record: no search across everything, no mini calendar of payments, no check numbers. This is the one place that says so."""
from __future__ import annotations

PURCHASING_PAGES = {"home", "purchase-orders", "price-lists"}


def allowed_pages(role: str | None, all_pages: set[str]) -> set[str]:
    """The menu pages this role may open."""
    if role == "owner":
        return set(all_pages)
    if role == "purchasing":
        return set(all_pages) & PURCHASING_PAGES
    return set()


def filter_menu(menu: dict[str, list[str]], role: str | None) -> dict[str, list[str]]:
    """The menu sections cut down to the pages the role may open; a section left empty goes."""
    every = {pid for ids in menu.values() for pid in ids}
    ok = allowed_pages(role, every)
    out = {section: [pid for pid in ids if pid in ok] for section, ids in menu.items()}
    return {section: ids for section, ids in out.items() if ids}


def may_search(role: str | None) -> bool:
    """'Search everything' reaches checks, invoices, batches and loans, so it is the owner's."""
    return role == "owner"


def hide_check_details(role: str | None) -> bool:
    """Check numbers, who collected a check and the like stay with the owner."""
    return role != "owner"
