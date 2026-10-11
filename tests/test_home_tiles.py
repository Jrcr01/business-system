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
    assert {t.page_id for t in TILES} <= pages | {"analytics", "calendar", "review", "booklets", "stock", "stock-counts", "affiliates", "sales", "customers", "si-booklets", "collect"}      # Django-only pages (2026-10-09; Check booklets 2026-10-11)
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
    assert by["checks"]["figure"] == "0"           # Daily payables (2026-10-09): a parked check (no number yet) is not on the checklist
    assert by["order-statements"]["figure"] == "1"                                     # 600 still to cover
    from engines import settings_registry as sr
    sr.save("show_cash_forecast", 1)                         # the over-plan warning is part of the cash plan, hidden by default
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
    register_tile(Tile("sales-invoices", "pay", "Sales invoices", lambda: ("3", "today", "ok")))
    rows = layout({t.page_id for t in home_tiles.TILES})
    daily = dict(rows)["pay"]
    assert "sales-invoices" in [t.page_id for t in daily]
    assert "sales-invoices" not in [t.page_id for g, ts in layout(_menu_pages()) for t in ts]     # page not installed yet: not drawn


def test_layout_keeps_group_order_and_drops_empty_groups():
    rows = layout({"payment-run", "settings"})
    assert [g for g, _ in rows] == ["pay", "system"]


@pytest.mark.parametrize("amount,text", [(0, "₱0"), (950, "₱950"), (287_943.21, "₱288K"), (1_480_000, "₱1.48M"), (-3_726_506, "-₱3.73M")])
def test_money_is_short_on_a_tile(amount, text):
    assert peso_short(amount) == text


# --- tile actions (Home v2) ---------------------------------------------------------------------------------------------------------------------

def _page_files() -> dict[str, str]:
    """{page id: views file text}, read from app.py's page list so nothing has to run."""
    text = (ROOT / "app.py").read_text(encoding="utf-8")
    block = text[text.index("PAGES.update({"):text.index("})", text.index("PAGES.update({"))]
    out = {}
    for pid, mod in re.findall(r'"([a-z-]+)": st\.Page\(views\.(\w+)\.render', block):
        out[pid] = (ROOT / "views" / f"{mod}.py").read_text(encoding="utf-8")
    return out


def test_every_action_opens_a_real_page_and_sets_a_widget_that_page_really_has():
    files = _page_files()
    for t in TILES:
        for a in t.actions:
            target = a.page_id or t.page_id
            assert target in files, (t.page_id, a.label)
            for key, _ in a.state:
                assert f'"{key}"' in files[target], f"{t.title} / {a.label}: widget key {key} is not on the {target} page"


def test_the_mode_values_the_actions_set_are_the_pages_own_constants_and_labels():
    from views import shared
    from views.add_data import KINDS
    from views.reports import REPORT_OPTIONS
    for t in TILES:
        for a in t.actions:
            st_ = dict(a.state)
            if "ad_kind" in st_:
                assert st_["ad_kind"] in KINDS
            if "report_type_picker" in st_:
                assert st_["report_type_picker"] in REPORT_OPTIONS
    assert {"Browse batches", "New batch"} == {shared.OS_BROWSE, shared.OS_NEW}
    assert {"Browse loan notes", "New loan note"} == {shared.LN_BROWSE, shared.LN_NEW}
    assert {"Browse bank loans", "New bank loan"} == {shared.BL_BROWSE, shared.BL_NEW}


def test_only_tiles_with_two_or_more_actions_get_a_panel_and_the_daily_ones_are_in_priority_order():
    by = {t.page_id: t for t in TILES}
    assert not by["payment-run"].has_panel and not by["checks"].has_panel and not by["creditors"].has_panel
    assert by["add-data"].has_panel and by["order-statements"].has_panel and by["bank-cash"].has_panel
    assert by["add-data"].actions[0].label == "Enter an invoice"                    # invoice entry first, as the owner uses it most
    assert all(len(t.actions) != 1 for t in TILES)                                  # one action would just be the page itself


def test_a_module_can_bring_its_own_actions(monkeypatch):
    from engines.home_tiles import act
    monkeypatch.setattr(home_tiles, "TILES", list(home_tiles.TILES))
    register_tile(Tile("sales", "pay", "Sales", actions=[act("New sale", sales_mode="New"), act("Day sheet", sales_mode="Day")]))
    t = next(x for x in home_tiles.TILES if x.page_id == "sales" and x.title == "Sales")
    assert t.has_panel and dict(t.actions[0].state) == {"sales_mode": "New"}


def test_the_rows_follow_the_cycle_and_receiving_has_a_tile_of_its_own():
    assert [g.key for g in GROUPS] == ["buy", "stock", "sell", "pay", "bank", "other", "records", "analytics", "system"]     # the goods, then the money (2026-10-11)
    rows = dict(layout(_menu_pages()))
    assert [t.tid for t in rows["buy"]][:2] == ["price-lists", "purchase-orders"] and "receive-goods" in [t.tid for t in rows["buy"]]
    by = {t.tid: t for t in TILES}
    assert dict(by["receive-goods"].state) == {"po_mode": "Receive"} and by["receive-goods"].page_id == "purchase-orders"
    assert "match-invoices" not in by            # goods never come without their DR or SI (owner, 2026-10-11): Receive records both, so no second card


def test_each_role_sees_only_the_receiving_tiles_it_may_use(db):
    from engines.acting_user import acting_as
    seen = {}
    for role in ("purchasing", "payables", "releaser"):
        with acting_as(f"{role}@example.com", role):
            seen[role] = {t.tid for _, ts in layout(_menu_pages()) for t in ts}
    assert "receive-goods" in seen["purchasing"] and "receive-goods" in seen["payables"] and "receive-goods" in seen["releaser"]


def test_the_new_tile_figures_count_orders_waiting_and_deliveries_without_an_invoice(db):
    from datetime import date
    from conftest import make_creditor
    from engines.po_rules import order_po, receive_goods, save_po, get_po
    cid = make_creditor(db)
    ok, msg, po = save_po(None, cid, date(2026, 9, 1), date(2026, 9, 5), [{"item_name": "Tire", "qty": 2, "est_unit_price": 100}])
    assert ok and order_po(po)[0]
    assert evaluate(next(t for t in TILES if t.tid == "receive-goods"))["figure"] == "1"
    line = int(get_po(po)["lines"].iloc[0]["line_id"])
    assert receive_goods(cid, [{"line_id": line, "qty": 2}])[0]
    assert evaluate(next(t for t in TILES if t.tid == "receive-goods"))["figure"] == "0"
