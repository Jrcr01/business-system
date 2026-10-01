"""What each role can open and see (B8). Owner (Payables) everything; Purchasing: Home, Purchase orders, Price lists -- no bank, no checks, no AP records."""
import re
from pathlib import Path

from engines.permissions import PURCHASING_PAGES, allowed_pages, filter_menu, hide_check_details, may_search

ROOT = Path(__file__).resolve().parent.parent


def _all_pages() -> set[str]:
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    block = text[text.index("PAGES.update({"):text.index("})", text.index("PAGES.update({"))]
    return set(re.findall(r'^\s*"([a-z-]+)": st\.Page', block, re.M))


def test_the_owner_can_open_every_page():
    pages = _all_pages()
    assert allowed_pages("owner", pages) == pages


def test_purchasing_can_open_only_home_orders_and_price_lists():
    pages = _all_pages()
    assert PURCHASING_PAGES == {"home", "purchase-orders", "price-lists"} and PURCHASING_PAGES <= pages
    assert allowed_pages("purchasing", pages) == PURCHASING_PAGES
    for secret in ("bank-cash", "checks", "payables-due", "dashboard", "order-statements", "settings", "admin", "reports", "lenders", "creditors", "data-health"):
        assert secret not in allowed_pages("purchasing", pages)


def test_an_unknown_role_gets_nothing():
    assert allowed_pages("janitor", _all_pages()) == set() and allowed_pages(None, _all_pages()) == set()


def test_the_menu_is_cut_down_to_what_the_role_may_open_and_empty_sections_go():
    menu = {"Home": ["home"], "Daily work": ["payment-run", "bank-cash"], "Purchasing": ["purchase-orders", "price-lists"], "System": ["settings"]}
    assert filter_menu(menu, "purchasing") == {"Home": ["home"], "Purchasing": ["purchase-orders", "price-lists"]}
    assert filter_menu(menu, "owner") == menu


def test_search_and_check_details_are_for_the_owner_only():
    assert may_search("owner") and not may_search("purchasing") and not may_search(None)
    assert hide_check_details("purchasing") and not hide_check_details("owner")
