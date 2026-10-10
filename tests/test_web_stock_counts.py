"""Stock counts (the revenue cycle, stage A; owner, 2026-10-11: stock counts chosen; "employee" counts and they "do not need" a sign-in): a count is a
blind sheet for a part of the stock, typed in by a signed-in person with the employee's name as "Counted by"; stock changes only when the System admin
approves a difference with a reason, by the difference, so a sale made after the count is not undone."""
from conftest import fetchone
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _three(web):
    a, an = _item()
    b, bn = _item("Westlake", "195/65R15", "RP18")
    c, cn = _item("Apollo", "8.25-16", "AMAR DELUXE")
    _stock(a, 12)
    _stock(b, 12, 2850.0)
    _stock(c, 3, 7310.25)
    return (a, an), (b, bn), (c, cn)


def _start(web, **data):
    r = web.post("/stock-counts/", {"action": "start", "type": "", "brand": "", "q": "", "counted_by": "Rico", **data})
    assert r.status_code == 302 and r["Location"] != "/stock-counts/", r
    return int(r["Location"].strip("/").split("/")[-1])


def _lines(cid):
    import sqlite3
    with sqlite3.connect(_db()) as conn:
        return {r[1]: r[0] for r in conn.execute("SELECT line_id, item_id FROM stock_count_lines WHERE count_id = ?", (cid,))}


def test_a_count_covers_the_part_chosen_and_the_sheet_shows_nothing_expected(web, db):
    _admin(web)
    (a, an), (b, bn), (c, cn) = _three(web)
    cid = _start(web, brand="Westlake")
    assert _n("SELECT count_no FROM stock_counts WHERE count_id = ?", (cid,)) == "CT-0001" and _n("SELECT COUNT(*) FROM stock_count_lines WHERE count_id = ?", (cid,)) == 2
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert an in page and bn in page and cn not in page                          # the brand chosen, nothing else
    assert "does <b>not</b> show how many it expects" in page and "2,840" not in page and "Expected" not in page     # blind: no expected quantity, no cost
    sheet = web.get(f"/stock-counts/{cid}/?print=1").content.decode()            # the blank sheet for the employee
    assert an in sheet and "Counted by: ____" in sheet and "2,840" not in sheet
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12              # starting a count changes nothing


def test_matching_items_close_and_a_difference_waits_for_the_system_admin(web, db):
    _admin(web)
    (a, an), (b, bn), (c, cn) = _three(web)
    cid = _start(web, brand="Westlake")
    ln = _lines(cid)
    r = web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "", f"c_{ln[a]}": "11", f"c_{ln[b]}": "12"}, follow=True)
    assert "Type who counted" in r.content.decode()                              # the employee's name is required
    web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "11", f"n_{ln[a]}": "1 with a cut sidewall", f"c_{ln[b]}": "12"})
    assert _n("SELECT status FROM stock_counts WHERE count_id = ?", (cid,)) == "WAITING"
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12              # nothing changes until it is approved
    assert _n("SELECT decision FROM stock_count_lines WHERE line_id = ?", (ln[b],)) == "MATCHED" and _n("SELECT last_counted_at FROM items WHERE id = ?", (b,)) is not None
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert "Waiting for approval" in page and "1 with a cut sidewall" in page and "-2,840.00" in page and "Rico" in page
    r = web.post(f"/stock-counts/{cid}/", {"action": "approve", "line_id": ln[a], "reason": ""}, follow=True)
    assert "Say why it differs" in r.content.decode() and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12
    web.post(f"/stock-counts/{cid}/", {"action": "approve", "line_id": ln[a], "reason": "damaged, scrapped"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 11 and _n("SELECT status FROM stock_counts WHERE count_id = ?", (cid,)) == "CLOSED"
    assert fetchone(_db(), "SELECT kind, qty, ref_type FROM stock_ledger WHERE item_id = ? ORDER BY move_id DESC LIMIT 1", (a,)) == ("ADJ", -1.0, "stock_counts")
    note = _n("SELECT detail FROM review_flags WHERE kind = 'COUNT_DIFFERENCE'")
    assert "CT-0001" in note and "counted 11 against 12" in note and "Rico" in note and "damaged, scrapped" in note
    assert "Counted" in web.get(f"/stock/{a}/").content.decode()                 # on the item's history


def test_a_sale_after_the_count_is_not_undone_by_the_approval(web, db):
    from engines.common import write_tx
    from engines.stock_ledger import stock_out
    _admin(web)
    (a, an), _, _ = _three(web)
    cid = _start(web, q="205/55")
    ln = _lines(cid)
    web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "11"})       # 1 short of 12
    with write_tx() as conn:
        stock_out(conn, a, 4, "sales", 1, note="sold after the count")                                       # 8 on hand now
    web.post(f"/stock-counts/{cid}/", {"action": "approve", "line_id": ln[a], "reason": "one missing"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 7               # 8 less the 1 that was missing, not back to 11


def test_count_again_sends_one_item_back_to_the_sheet(web, db):
    _admin(web)
    (a, an), (b, bn), _ = _three(web)
    cid = _start(web, brand="Westlake")
    ln = _lines(cid)
    web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "10", f"c_{ln[b]}": "12"})
    web.post(f"/stock-counts/{cid}/", {"action": "recount", "line_id": ln[a]})
    assert _n("SELECT status FROM stock_counts WHERE count_id = ?", (cid,)) == "COUNTING" and _n("SELECT counted FROM stock_count_lines WHERE line_id = ?", (ln[a],)) is None
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert an in page and bn not in page                                         # only the item to count again
    web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "12"})
    assert _n("SELECT status FROM stock_counts WHERE count_id = ?", (cid,)) == "CLOSED" and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12


def test_an_item_left_empty_is_not_part_of_the_count_and_a_count_can_be_cancelled(web, db):
    _admin(web)
    (a, an), (b, bn), _ = _three(web)
    cid = _start(web, brand="Westlake")
    ln = _lines(cid)
    web.post(f"/stock-counts/{cid}/", {"action": "save", "counted_by": "Rico", f"c_{ln[a]}": "12"})
    assert _n("SELECT counted FROM stock_count_lines WHERE line_id = ?", (ln[a],)) == 12.0 and _n("SELECT status FROM stock_counts WHERE count_id = ?", (cid,)) == "COUNTING"
    web.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "12"})
    assert _n("SELECT COUNT(*) FROM stock_count_lines WHERE count_id = ?", (cid,)) == 1 and _n("SELECT last_counted_at FROM items WHERE id = ?", (b,)) is None
    other = _start(web, brand="Westlake")
    web.post(f"/stock-counts/{other}/", {"action": "cancel"})
    assert _n("SELECT status FROM stock_counts WHERE count_id = ?", (other,)) == "CANCELLED"
    assert "CT-0001" in web.get("/stock-counts/").content.decode()


def test_only_the_system_admin_approves_a_difference(web, db):
    _admin(web)
    (a, an), _, _ = _three(web)
    seller = _sign_in_new("sales@slicks.test", "sales")
    r = seller.post("/stock-counts/", {"action": "start", "type": "", "brand": "", "q": "205/55", "counted_by": "Rico"})
    cid = int(r["Location"].strip("/").split("/")[-1])
    ln = _lines(cid)
    seller.post(f"/stock-counts/{cid}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "9"})
    page = seller.get(f"/stock-counts/{cid}/").content.decode()
    assert "waiting for the System admin" in page and 'value="approve"' not in page
    seller.post(f"/stock-counts/{cid}/", {"action": "approve", "line_id": ln[a], "reason": "mine"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12
    web.post(f"/stock-counts/{cid}/", {"action": "approve", "line_id": ln[a], "reason": "3 not found"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9


def test_the_opening_count_sets_stock_at_the_price_list_cost_in_one_approval(web, db):
    from datetime import date

    from conftest import make_creditor
    from engines.price_list_service import add_price
    _admin(web)
    cid = make_creditor(db, "Acme Tires", terms="30")
    a, an = _item()
    b, bn = _item("Westlake", "195/65R15", "RP18")
    add_price(cid, an, "pc", 2840, date(2026, 9, 1))                              # never received: no cost yet, only the supplier's price
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 0 and an not in web.get("/stock/?show=low").content.decode()      # not stocked yet is not "low"
    count = _start(web, brand="Westlake")
    ln = _lines(count)
    web.post(f"/stock-counts/{count}/", {"action": "finish", "counted_by": "Rico", f"c_{ln[a]}": "12", f"c_{ln[b]}": "4"})
    r = web.post(f"/stock-counts/{count}/", {"action": "approve_all", "reason": ""}, follow=True)
    assert "one reason for all" in r.content.decode()
    web.post(f"/stock-counts/{count}/", {"action": "approve_all", "reason": "opening count"})
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (a,)) == (12, 2840)
    assert _n("SELECT on_hand FROM items WHERE id = ?", (b,)) == 4 and _n("SELECT status FROM stock_counts WHERE count_id = ?", (count,)) == "CLOSED"
    assert "34,080.00" in web.get("/stock/").content.decode()                     # 12 x 2,840
