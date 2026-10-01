"""Home launchpad (Fiori-style tiles, 2026-10-01): the tiles come from one registry so new modules (the revenue cycle next) add their own without
touching the Home page; every figure is safe on any database; a broken source shows a dash instead of breaking Home."""
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from conftest import make_creditor
from engines import home_tiles
from engines.home_tiles import GROUPS, TILES, Tile, evaluate, layout, peso_short, register_tile

ROOT = Path(__file__).resolve().parent.parent


def _menu_pages() -> set[str]:
    """The page ids app.py puts in the menu (read from the source, so app.py does not have to run)."""
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    block = text[text.index("PAGES.update({"):text.index("})", text.index("PAGES.update({"))]
    return set(re.findall(r'^\s*"([a-z-]+)": st\.Page', block, re.M))


def test_every_tile_points_at_a_real_page_and_a_known_group():
    pages, groups = _menu_pages(), {g.key for g in GROUPS}
    assert {t.page_id for t in TILES} <= pages
    assert {t.group for t in TILES} <= groups
    assert pages - {"home"} <= {t.page_id for t in TILES}          # every menu page has a tile


def test_every_figure_works_on_an_empty_database(db):
    for t in TILES:
        r = evaluate(t)
        assert set(r) == {"figure", "unit", "level"}
        assert r["level"] in ("none", "ok", "warn", "alert"), t.page_id


def test_waiting_work_turns_tiles_orange_and_an_order_over_the_plan_turns_red(db):
    cid = make_creditor(db)
    import app
    sid = app.create_order_statement(cid)
    app.add_invoice(cid, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    app.issue_check(cid, 400.0, date(2026, 10, 30), statement_id=sid)                   # a parked check: waiting to print
    by = {t.page_id: evaluate(t) for t in TILES}
    assert by["checks"]["figure"] == "1" and by["checks"]["level"] == "warn"
    assert by["order-statements"]["figure"] == "1"                                     # 600 still to cover
    sqlite3.connect(db).execute(
        "INSERT INTO purchase_orders (po_no, creditor_id, order_date, status, over_cash_plan) VALUES ('2026 - PO 0001', ?, '2026-10-01', 'ORDERED', 1)",
        (cid,)).connection.commit()
    po = evaluate(next(t for t in TILES if t.page_id == "purchase-orders"))
    assert po["level"] == "alert" and po["figure"] == "1" and "over plan" in po["unit"]


def test_a_broken_source_shows_a_dash_not_an_error(db):
    def boom():
        raise RuntimeError("table missing")
    r = evaluate(Tile("checks", "daily", "Broken", boom))
    assert r == {"figure": "—", "unit": "", "level": "none"}


def test_a_new_module_adds_its_tile_without_touching_home(db, monkeypatch):
    monkeypatch.setattr(home_tiles, "TILES", list(home_tiles.TILES))
    register_tile(Tile("sales-invoices", "daily", "Sales invoices", lambda: ("3", "today", "ok")))
    rows = layout({t.page_id for t in home_tiles.TILES})
    daily = dict(rows)["daily"]
    assert "sales-invoices" in [t.page_id for t in daily]
    assert "sales-invoices" not in [t.page_id for g, ts in layout(_menu_pages()) for t in ts]     # page not installed yet: not drawn


def test_layout_keeps_group_order_and_drops_empty_groups():
    rows = layout({"payment-run", "settings"})
    assert [g for g, _ in rows] == ["daily", "system"]


@pytest.mark.parametrize("amount,text", [(0, "₱0"), (950, "₱950"), (287_943.21, "₱288K"), (1_480_000, "₱1.48M"), (-3_726_506, "-₱3.73M")])
def test_money_is_short_on_a_tile(amount, text):
    assert peso_short(amount) == text
