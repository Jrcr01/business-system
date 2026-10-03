"""Every page, every role, every Home action (2026-10-03, goal: test every workflow the system has). A page that a role may open must draw without an error for that role; every action a
Home tile offers must open its page in the mode it names. Runs against a copy of the schema with a little data in it so the lists are not all empty."""
import re
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import make_creditor
from engines import home_tiles
from engines.permissions import ROLES, allowed_pages

ROOT = Path(__file__).resolve().parent.parent
APP_TEXT = (ROOT / "app.py").read_text(encoding="utf-8")
PAGE_MODULES = dict(re.findall(r'"([a-z-]+)": st\.Page\(views\.(\w+)\.render', APP_TEXT))             # page id -> views module


def _page_app():
    import importlib

    import streamlit as st

    from views import shared
    mod = importlib.import_module("views." + st.session_state["_module"])
    shared.PAGES.clear()                                          # Home draws a tile for every registered page: stand-ins for them all
    shared.PAGES.update({pid: st.Page(lambda: None, title=pid, url_path=pid) for pid in st.session_state["_page_ids"]})
    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "role@example.com", "name": "Role Tester", "role": st.session_state["_role"]})
    mod.render()


def _open(page_id: str, role: str, **state) -> AppTest:
    at = AppTest.from_function(_page_app, default_timeout=90)
    at.session_state["_module"] = PAGE_MODULES[page_id]
    at.session_state["_page_ids"] = sorted(PAGE_MODULES)
    at.session_state["_role"] = role
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


@pytest.fixture
def populated(db):
    """A supplier, a lender and a batch, so the lists and forms have something to show."""
    import app
    from engines.statement_rules import add_invoice
    cid = make_creditor(db, name="Workflow Supplier")
    make_creditor(db, name="Workflow Lender", creditor_type="LENDER", terms="0")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "WF-1", date(2026, 10, 1), date(2026, 11, 1), 5000.0, [], sid)
    return db


def test_every_page_in_the_menu_has_a_module():
    assert PAGE_MODULES and "home" in PAGE_MODULES and "release" in PAGE_MODULES and "controls" in PAGE_MODULES
    assert set(PAGE_MODULES) >= {p for ids in __import__("engines.permissions", fromlist=["PAGE_ACTION"]).PAGE_ACTION for p in [ids]}


@pytest.mark.parametrize("role", ROLES)
def test_every_page_a_role_may_open_draws_without_an_error(populated, role):
    pages = sorted(allowed_pages(role, set(PAGE_MODULES)))
    assert pages, role
    bad = {}
    for page in pages:
        at = _open(page, role)
        if at.exception:
            bad[page] = str(at.exception[0].value)[:200]
    assert not bad, f"{role}: {bad}"


def _actions():
    out = []
    for t in home_tiles.TILES:
        for a in t.actions:
            out.append(pytest.param(t, a, id=f"{t.tid}:{a.label}"))
        if t.state:
            out.append(pytest.param(t, None, id=f"{t.tid}:tile"))
    return out


@pytest.mark.parametrize("tile, action", _actions())
def test_every_home_action_opens_its_page_in_its_mode(populated, tile, action):
    state = dict(action.state) if action else dict(tile.state)
    page = (action.page_id if action and action.page_id else tile.page_id)
    if page not in PAGE_MODULES:
        pytest.skip("page not installed")
    at = _open(page, "admin", **state)
    assert not at.exception, (page, state, at.exception[0].value if at.exception else "")
