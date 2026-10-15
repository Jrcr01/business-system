"""Analytics -> Stock (owner, 2026-10-15: "Go for 3"): stock value at average cost, days of stock, below reorder, slow and fast movers, tire age by the
oldest DOT, stock value by month end from the ledger."""
import sqlite3
from datetime import timedelta

from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _item(conn, code, brand, on_hand, cost, dot="", reorder=2, category="tire"):
    return conn.execute("""INSERT INTO items (code, category, brand, size_model, key, name, on_hand, avg_cost, reorder_level, oldest_dot)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (code, category, brand, code, code, f"{brand} {code}", on_hand, cost, reorder, dot or None)).lastrowid


def _move(conn, item, kind, qty, cost, after, avg, day):
    conn.execute("INSERT INTO stock_ledger (item_id, kind, qty, unit_cost, on_hand_after, avg_after, moved_on) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (item, kind, qty, cost, after, avg, str(day)))


def test_the_stock_figures_equal_the_hand_sums(web, db):
    from engines.bi_service import stock
    from engines.clock import manila_today
    _admin(web)
    t = manila_today()
    with sqlite3.connect(_db()) as conn:
        fast = _item(conn, "185/65R15", "Sunny", 6, 2000.0, dot="1226")         # sells: 24 out in 30 days
        _move(conn, fast, "IN", 30, 2000, 30, 2000, t - timedelta(days=60))
        _move(conn, fast, "OUT", -24, 2000, 6, 2000, t - timedelta(days=10))
        slow = _item(conn, "275/40R20", "Yokohama", 8, 12000.0, dot="0523")     # in 200 days ago, never sold
        _move(conn, slow, "IN", 8, 12000, 8, 12000, t - timedelta(days=200))
        gone = _item(conn, "NS60", "Motolite", 0, 3000.0, category="battery", reorder=1)
        _move(conn, gone, "IN", 2, 3000, 2, 3000, t - timedelta(days=40))
        _move(conn, gone, "OUT", -2, 3000, 0, 3000, t - timedelta(days=5))
        _item(conn, "NEVER", "Apollo", 0, 0.0)                                  # in the catalog, never stocked: not "below reorder"
    r = stock(t)
    assert r["value"] == 6 * 2000 + 8 * 12000 and r["pieces"] == 14 and r["items"] == 2
    assert r["cogs30"] == 24 * 2000 + 2 * 3000 and r["days"] == round(108000 / (54000 / 30))
    assert r["below"] == 1 and r["zero"] == 1                                    # the Motolite at zero; the never-stocked Apollo is left out
    assert r["slow_n"] == 1 and r["slow_value"] == 96000 and r["slow"][0]["label"] == "Yokohama 275/40R20"
    assert [b["label"] for b in r["fast"]] == ["Sunny 185/65R15", "Motolite NS60"] and r["fast"][0]["note"] == "24 out · 8 d left"
    assert {b["label"]: b["amount"] for b in r["age"]} == {"2026": 6, f"{t.year - 2} and older": 8}
    assert r["line"]["a"]                                                        # the month-end line is drawn
    page = web.get("/analytics/?tab=stock").content.decode()
    assert "could not be drawn" not in page and "Stock value (at average cost)" in page and "Yokohama 275/40R20" in page and "sell first" in page
