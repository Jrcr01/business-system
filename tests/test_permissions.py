"""The access matrix (B8): four roles, one table (engines/permissions.ACTIONS) that the menu, the engines and the 'Who can do what' screen all read."""
import re
from datetime import date
from pathlib import Path

import pytest

from conftest import fetchone, make_creditor
from engines import auth_service as au
from engines import discount_rules as dr
from engines import pdc_release_service as pdc
from engines import permissions as perm
from engines.acting_user import acting_as
from engines.permissions import ACTIONS, ROLES, allowed_pages, can, filter_menu, hide_check_details, matrix_table, may_search, require
from engines.po_rules import cancel_po, order_po, receive_into_batch, save_po
from engines.price_list_service import add_price

ROOT = Path(__file__).resolve().parent.parent
BROTHER, OTHER = "brother@example.com", "other@example.com"


def _all_pages() -> set[str]:
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    block = text[text.index("PAGES.update({"):text.index("})", text.index("PAGES.update({"))]
    return set(re.findall(r'^\s*"([a-z-]+)": st\.Page', block, re.M))


def test_every_page_is_the_admins_and_every_page_is_in_the_matrix():
    pages = _all_pages()
    assert allowed_pages("admin", pages) == pages
    assert allowed_pages("owner", pages) == pages - {"settings", "admin"}              # a family owner has every business page, no system page
    assert pages - set(perm.PAGE_ACTION) == set(), "a new page needs an entry in PAGE_ACTION (else it is the owner's only, silently)"
    assert set(perm.PAGE_ACTION.values()) <= set(ACTIONS)


def test_each_roles_pages():
    pages = _all_pages()
    assert allowed_pages("purchasing", pages) == {"home", "price-lists", "purchase-orders"}
    assert allowed_pages("viewer", pages) == {"home", "price-lists", "purchase-orders", "reports"}
    pay = allowed_pages("payables", pages)
    assert {"payment-run", "add-data", "order-statements", "checks", "bank-cash", "payables-due", "creditors", "lenders", "reports"} <= pay
    assert not pay & {"settings", "admin"}
    for secret in ("bank-cash", "checks", "payables-due", "settings", "admin", "creditors"):
        assert secret not in allowed_pages("purchasing", pages) | allowed_pages("viewer", pages)


def test_an_unknown_role_gets_nothing():
    assert allowed_pages("janitor", _all_pages()) == set() and allowed_pages(None, _all_pages()) == set()
    assert not can("janitor", "po.create") and not can(None, "home.open")


def test_the_menu_is_cut_down_to_what_the_role_may_open_and_empty_sections_go():
    menu = {"Home": ["home"], "Daily work": ["payment-run", "bank-cash"], "Purchasing": ["purchase-orders", "price-lists"], "System": ["settings"]}
    assert filter_menu(menu, "purchasing") == {"Home": ["home"], "Purchasing": ["purchase-orders", "price-lists"]}
    assert filter_menu(menu, "admin") == menu
    assert filter_menu(menu, "owner") == {k: v for k, v in menu.items() if k != "System"}


def test_search_and_check_details():
    assert may_search("owner") and may_search("payables") and may_search("viewer") and not may_search("purchasing") and not may_search(None)
    assert not hide_check_details("owner") and not hide_check_details("payables") and hide_check_details("purchasing") and hide_check_details("viewer")


def test_the_matrix_rows_the_owner_asked_for():
    expect = {                                     # action: (admin, payables, purchasing, viewer)
        "po.create": ("Y", None, "own", None), "po.receive": ("Y", "Y", None, None), "check.park": ("Y", "Y", None, None), "check.issue": ("Y", None, None, None),
        "check.void": ("Y", None, None, None), "price.write": ("Y", None, "Y", None), "users.manage": ("Y", None, None, None), "settings.edit": ("Y", None, None, None),
        "audit.view": ("Y", None, None, None), "reports.use": ("Y", "Y", None, "Y"), "account.own": ("Y", "Y", "Y", "Y"),
    }
    for action, levels in expect.items():
        assert tuple(ACTIONS[action]["levels"][r] for r in ("admin", "payables", "purchasing", "viewer")) == levels, action
        assert ACTIONS[action]["levels"]["sales"] in (None, "Y"), action                   # the counter role: only its own pages
    assert all(ACTIONS[a]["levels"]["admin"] == "Y" for a in ACTIONS)                 # the System admin can always do everything


def test_a_family_owner_does_every_business_action_but_no_system_action():
    system = {"carveout.use", "users.manage", "audit.view", "settings.edit", "health.fix"}
    assert perm.SYSTEM_ONLY == system
    for action in ACTIONS:
        assert can("owner", action) == (action not in system), action
    assert not can("owner", "users.manage") and can("owner", "check.issue") and can("owner", "check.void") and can("owner", "po.receive")


def test_the_releaser_only_receives_goods_and_releases_checks():
    yes = {a for a in ACTIONS if can("releaser", a)}
    assert yes == {"home.open", "account.own", "po.receive_goods", "check.release"}
    assert allowed_pages("releaser", _all_pages()) == {"home", "release"}              # Home, and the page where she records what the supplier collected


def test_own_means_only_what_the_person_created():
    assert can("purchasing", "po.cancel", created_by=BROTHER, me="Brother@Example.com")
    assert not can("purchasing", "po.cancel", created_by=OTHER, me=BROTHER) and not can("purchasing", "po.cancel", created_by=None, me=BROTHER)
    assert can("owner", "po.cancel", created_by=OTHER, me="owner@example.com")


def test_the_who_can_do_what_table_is_the_matrix():
    rows = matrix_table()
    assert len(rows) == len(ACTIONS) and {"group", "operation", "System admin", "Owner", "Sales", "Payables", "Purchasing", "Releaser", "Viewer"} <= set(rows[0])
    assert [r["Purchasing"] for r in rows if r["operation"].startswith("Create and edit a purchase order")] == ["Own only"]


# --- the engines refuse what the role may not do, even if a button were left showing ---------------------------------------------------------

def _lines():
    return [{"item_name": "Tire", "qty": 2, "est_unit_price": 100}]


def test_purchasing_can_make_and_order_its_own_orders_but_not_someone_elses_or_receive(db):
    cid = make_creditor(db)
    with acting_as(BROTHER, "purchasing"):
        ok, msg, mine = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), _lines())
        assert ok, msg
        assert order_po(mine)[0] and not receive_into_batch(cid, 1, "INV-1", date(2026, 10, 9), [])[0]
    with acting_as(OTHER, "purchasing"):
        ok, msg, _ = save_po(mine, cid, date(2026, 10, 1), date(2026, 10, 8), _lines())
        assert not ok and "create" in msg.lower()
        assert not cancel_po(mine)[0]
    with acting_as("owner@example.com", "owner"):
        assert cancel_po(mine, "test")[0]                                       # a family owner may cancel anyone's order


def test_payables_and_viewer_cannot_create_orders_and_a_viewer_cannot_change_prices(db):
    cid = make_creditor(db)
    for role in ("payables", "viewer"):
        with acting_as("x@example.com", role):
            assert not save_po(None, cid, date(2026, 10, 1), None, _lines())[0]
    for role in ("payables", "viewer"):
        with acting_as("x@example.com", role):
            assert not add_price(cid, "Tire", "pc", 100, date(2026, 10, 1))[0]
            assert not dr.set_discount(cid, "Westlake", "5", date(2026, 10, 1))[0]
    with acting_as(BROTHER, "purchasing"):
        assert add_price(cid, "Tire", "pc", 100, date(2026, 10, 1))[0] and dr.set_discount(cid, "Westlake", "5", date(2026, 10, 1))[0]
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list")[0] == 1


def test_a_payables_clerk_may_release_checks_but_not_cancel_them(db):
    from engines.check_rules import cancel_check
    with acting_as("clerk@example.com", "payables"):
        assert require("check.release")[0] and not require("check.issue")[0]
        ok, msg = cancel_check(999999, "no")
        assert not ok and "not allowed" in msg.lower()


def test_only_the_system_admin_manages_users(db):
    au.create_owner("owner@example.com", "Owner", "Owner123x")                  # the first account is the System admin
    with acting_as("family@example.com", "owner"):
        with pytest.raises(PermissionError):
            au.create_user("new@example.com", "New", "viewer")
    with acting_as("clerk@example.com", "payables"):
        with pytest.raises(PermissionError):
            au.create_user("new@example.com", "New", "viewer")
        assert not au.set_role(1, "viewer")[0]
    with acting_as("owner@example.com", "admin"):
        ok, _, uid, _ = au.create_user("new@example.com", "New", "viewer")
        assert ok and au.set_role(uid, "payables")[0]


def test_no_signed_in_person_acts_as_the_system_admin_so_scripts_and_tests_still_work(db):
    assert require("users.manage") == (True, "")


def test_the_system_admin_can_view_the_system_as_another_role_without_changing_any_role(monkeypatch):
    import streamlit as st
    from views import login
    owner = {"user_id": 1, "email": "o@x.c", "name": "O", "role": "admin", "is_active": 1, "must_change_password": 0}
    st.session_state["view_as"] = "purchasing"
    seen = login._apply_view_as(owner)
    assert seen["role"] == "purchasing" and seen["real_role"] == "admin" and owner["role"] == "admin" and seen["email"] == "o@x.c"
    assert filter_menu({"Home": ["home"], "Daily": ["checks"], "Purchasing": ["purchase-orders"]}, seen["role"]) == {"Home": ["home"], "Purchasing": ["purchase-orders"]}
    st.session_state["view_as"] = "janitor"
    assert login._apply_view_as(owner) is owner                                  # an unknown role is ignored
    st.session_state["view_as"] = "admin"
    assert login._apply_view_as(owner) is owner
    family = {**owner, "role": "owner"}
    st.session_state["view_as"] = "viewer"
    assert login._apply_view_as(family) is family                                # a family owner has no developer view
    clerk = {**owner, "role": "payables"}
    st.session_state["view_as"] = "viewer"
    assert login._apply_view_as(clerk) is clerk                                  # only a real System admin can do this
    st.session_state.pop("view_as", None)
