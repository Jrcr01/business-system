"""The revenue cycle, stage A, the rules (owner, 2026-10-11): a sale is ONE document that takes the stock and records how it was paid in the same step;
controls flag and never block (below cost, above stock, a charge without "credit allowed", a skipped number, a sale typed late, a void); what is refused
protects the record (a number used twice, payments that do not add up, a check without its details, a charge with no named customer). Affiliates: a
lend or a borrow moves stock without a sale and stays on the balance until it is returned, paid (at cost) or set off."""
from datetime import timedelta

from conftest import fetchone
from tests.test_web_stock import _item, _stock


def _n(db, sql, params=()):
    return fetchone(db, sql, params)[0]


def _flags(db, kind):
    return _n(db, "SELECT COUNT(*) FROM review_flags WHERE kind = ?", (kind,))


def _cash(amount):
    return [{"method": "CASH", "amount": amount}]


def _setup(db, qty=12):
    from engines.sales_rules import register_booklet
    a, name = _item()
    _stock(a, qty)
    assert register_booklet(4501, 4550, "Julian")[0]
    return a, name


def test_a_sale_takes_the_stock_and_records_the_payment_in_one_step(db):
    from engines.sales_rules import get_sale, next_si_no, save_sale
    a, name = _setup(db)
    assert next_si_no() == 4501
    from engines.clock import manila_today
    ok, msg, sid = save_sale(4501, manila_today(), None, "", [{"item_id": a, "qty": 4, "price": 3600}],
                             [{"description": "Mounting and balancing", "amount": 600}], [{"method": "CASH", "amount": 5000}, {"method": "CHECK", "amount": 10000, "bank": "BDO",
                                                                                         "check_no": "0048812", "check_date": "2026-12-25"}])
    assert ok, msg
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 8                    # the same step
    s = get_sale(sid)
    assert (s["ref"], s["total"], s["customer"], s["profit"]) == ("SI 004501", 15000.0, "Walk-in", 3640.0)     # 4 x (3,600 - 2,840) + the service
    assert [p["method"] for p in s["payments"]] == ["CASH", "CHECK"] and s["charged"] == 0                  # a post-dated check: the sale is paid
    assert fetchone(db, "SELECT kind, qty, ref_type FROM stock_ledger WHERE item_id = ? ORDER BY move_id DESC LIMIT 1", (a,)) == ("OUT", -4.0, "sales")
    assert next_si_no() == 4502


def test_what_protects_the_record_is_refused(db):
    from engines.clock import manila_today
    from engines.sales_rules import cancel_leaf, save_sale
    a, _ = _setup(db)
    today = manila_today()
    line = [{"item_id": a, "qty": 1, "price": 3600}]
    assert save_sale(4501, today, None, "", line, [], _cash(3600))[0]
    assert "already used" in save_sale(4501, today, None, "", line, [], _cash(3600))[1]                    # a number is used once
    assert "add up to" in save_sale(4502, today, None, "", line, [], _cash(3000))[1]                       # the payments must make the total
    assert "bank, its number and its date" in save_sale(4502, today, None, "", line, [], [{"method": "CHECK", "amount": 3600}])[1]
    assert "named customer" in save_sale(4502, today, None, "Mang Tony", line, [], [{"method": "CHARGE", "amount": 3600}])[1]
    assert "type the price agreed" in save_sale(4502, today, None, "", [{"item_id": a, "qty": 1, "price": ""}], [], _cash(1))[1]
    assert "cannot be after today" in save_sale(4502, today + timedelta(days=1), None, "", line, [], _cash(3600))[1]
    assert cancel_leaf(4502, "torn")[0] and "spoiled leaf" in save_sale(4502, today, None, "", line, [], _cash(3600))[1]
    assert _n(db, "SELECT COUNT(*) FROM sales") == 1 and _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 11     # a refusal writes nothing


def test_flags_never_block(db):
    from engines.clock import manila_today
    from engines.sales_rules import save_customer, save_sale, set_credit_allowed
    a, name = _setup(db, qty=1)
    today = manila_today()
    ok, _, cust = save_customer(None, "RJM Trucking", "FLEET")
    assert ok
    ok, msg, sid = save_sale(4503, today - timedelta(days=3), cust, "", [{"item_id": a, "qty": 2, "price": 2500}], [], [{"method": "CHARGE", "amount": 5000}], "loyal customer")
    assert ok, msg                                                                         # below cost, above stock, no credit, late, a skipped number: all saved
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == -1
    assert "loyal customer" in _n(db, "SELECT detail FROM review_flags WHERE kind = 'SALE_BELOW_COST'")
    assert _flags(db, "STOCK_NEGATIVE") == 1 and _flags(db, "SALE_NO_CREDIT") == 1 and _flags(db, "SALE_TYPED_LATE") == 1
    assert _flags(db, "SI_SKIPPED") == 2                                                   # 4501 and 4502 were skipped
    assert set_credit_allowed(cust, True)[0]
    assert save_sale(4504, today, cust, "", [{"item_id": a, "qty": 1, "price": 3600}], [], [{"method": "CHARGE", "amount": 3600}])[0]
    assert _flags(db, "SALE_NO_CREDIT") == 1                                               # credit allowed now: no new flag
    assert save_sale(9001, today, None, "", [{"item_id": a, "qty": 1, "price": 3600}], [], _cash(3600))[0]      # outside every booklet: saved, flagged
    assert "not in any registered booklet" in _n(db, "SELECT detail FROM review_flags WHERE kind = 'SI_SKIPPED' ORDER BY flag_id DESC LIMIT 1")


def test_release_first_invoice_later(db):
    from engines.clock import manila_today
    from engines.sales_rules import cancel_release, get_sale, invoice_release, open_releases, release
    a, name = _setup(db)
    ok, msg, rid = release(None, "Mang Tony", [{"item_id": a, "qty": 2}])
    assert ok and "RL-0001" in msg and _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 10      # out of stock at release
    assert [r["release_no"] for r in open_releases()] == ["RL-0001"]
    line = get_sale(rid)["lines"][0]["line_id"]
    assert "type the price agreed" in invoice_release(rid, 4501, manila_today(), {}, [], _cash(1))[1]
    ok, msg = invoice_release(rid, 4501, manila_today(), {line: "3600"}, [], _cash(7200))
    assert ok, msg
    s = get_sale(rid)
    assert (s["status"], s["ref"], s["total"]) == ("SAVED", "SI 004501", 7200.0) and _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 10     # not taken twice
    assert open_releases() == []
    ok, _, second = release(None, "Walk-in", [{"item_id": a, "qty": 1}])
    assert cancel_release(second, "")[0] is False and cancel_release(second, "customer did not take it")[0]
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 10 and get_sale(second)["status"] == "VOID"


def test_void_and_return_put_stock_back_and_are_flagged(db):
    from engines.clock import manila_today
    from engines.sales_rules import day_sheet, get_sale, return_item, save_sale, void_sale
    a, name = _setup(db)
    today = manila_today()
    _, _, one = save_sale(4501, today, None, "", [{"item_id": a, "qty": 4, "price": 3600}], [], _cash(14400))
    _, _, two = save_sale(4502, today, None, "", [{"item_id": a, "qty": 2, "price": 3600}], [], _cash(7200))
    line = get_sale(one)["lines"][0]["line_id"]
    assert not return_item(one, line, 5, "wrong size")[0] and not return_item(one, line, 1, "")[0]
    assert return_item(one, line, 1, "wrong size for the rim")[0]
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 7 and get_sale(one)["net"] == 10800.0 and _flags(db, "SALE_RETURN") == 1
    assert not void_sale(two, "")[0] and void_sale(two, "written for the wrong customer")[0]
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 9 and get_sale(two)["status"] == "VOID" and _flags(db, "SALE_VOIDED") == 1
    assert not void_sale(two, "again")[0]
    d = day_sheet(today)
    assert (d["invoices"], d["pieces"], d["sales"], d["profit"]) == (1, 3.0, 10800.0, 2280.0)             # the void is out; the returned piece too
    assert d["money"]["CASH"] == 14400.0 and [v["ref"] for v in d["voided"]] == ["SI 004502"]
    assert _n(db, "SELECT si_no FROM sales WHERE sale_id = ?", (two,)) == 4502                              # the number stays used


def test_the_booklets_one_in_use_and_skipped_numbers(db):
    from engines.clock import manila_today
    from engines.sales_rules import booklets, cancel_leaf, register_booklet, save_sale, skipped, use_booklet
    a, _ = _setup(db)
    assert "overlap" in register_booklet(4540, 4600)[1]
    ok, msg = register_booklet(4551, 4600)
    assert ok and "waits" in msg
    b = {x["code"]: x for x in booklets()}
    assert (b["SB-0001"]["status"], b["SB-0002"]["status"], b["SB-0001"]["next"]) == ("IN_USE", "WAITING", 4501)
    save_sale(4503, manila_today(), None, "", [{"item_id": a, "qty": 1, "price": 3600}], [], _cash(3600))
    assert skipped() == [4501, 4502]
    assert cancel_leaf(4502, "spoiled")[0] and skipped() == [4501] and not cancel_leaf(4503, "x")[0] and not cancel_leaf(99, "x")[0]
    assert use_booklet(b["SB-0002"]["booklet_id"])[0]
    b = {x["code"]: x for x in booklets()}
    assert (b["SB-0001"]["status"], b["SB-0002"]["status"]) == ("WAITING", "IN_USE")                       # it still has unused leaves


def test_lend_borrow_and_the_three_ways_to_settle(db):
    from engines.affiliate_rules import balances, flag_long_open, open_lines, record_move, settle
    from engines.sales_rules import get_sale, save_customer
    a, name = _setup(db)
    b, bname = _item("Apollo", "8.25-16", "AMAR DELUXE")
    ok, _, aff = save_customer(None, "Tita Baby Tire Center", "OTHER", is_affiliate=True)
    ok, _, plain = save_customer(None, "RJM Trucking", "FLEET")
    assert "Choose an affiliate" in record_move(plain, "LEND", [{"item_id": a, "qty": 1}])[1]
    ok, msg, lend = record_move(aff, "LEND", [{"item_id": a, "qty": 6}], taken_by="Rico")
    assert ok and "AL-0001" in msg and _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 6          # out, and not a sale
    assert _n(db, "SELECT COUNT(*) FROM sales") == 0
    assert "what the tires cost them" in record_move(aff, "BORROW", [{"item_id": b, "qty": 2}])[1]          # we have no cost for it yet
    ok, msg, _ = record_move(aff, "BORROW", [{"item_id": b, "qty": 2, "price": "4900"}])
    assert ok and "AB-0001" in msg and _n(db, "SELECT on_hand FROM items WHERE id = ?", (b,)) == 2
    bal = balances()[0]
    assert (bal["lent_pcs"], bal["lent_value"], bal["borrowed_pcs"], bal["borrowed_value"], bal["net"]) == (6.0, 17040.0, 2.0, 9800.0, 7240.0)
    lines = {x["direction"]: x for x in open_lines(aff)}
    assert settle(aff, [{"line_id": lines["LEND"]["line_id"], "qty": 2}], "RETURN")[0]                      # they return 2 of ours
    assert _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 8
    ok, msg = settle(aff, [{"line_id": lines["LEND"]["line_id"], "qty": 1}], "PAY", si_no=4501, payments=_cash(2840))      # they pay for 1, at our cost
    assert ok, msg
    sale = get_sale(_n(db, "SELECT sale_id FROM sales"))
    assert (sale["total"], sale["customer"], sale["profit"]) == (2840.0, "Tita Baby Tire Center", 0.0) and _n(db, "SELECT on_hand FROM items WHERE id = ?", (a,)) == 8      # stock left with the lend
    assert "tick tires they hold of ours AND" in settle(aff, [{"line_id": lines["LEND"]["line_id"], "qty": 1}], "OFFSET")[1]
    ok, msg = settle(aff, [{"line_id": lines["LEND"]["line_id"], "qty": 3}, {"line_id": lines["BORROW"]["line_id"], "qty": 2}], "OFFSET")
    assert ok and "we still owe ₱1,280.00" in msg                                                         # 3 x 2,840 against 2 x 4,900
    assert open_lines(aff) == [] and balances()[0]["net"] == 0
    from engines.clock import manila_today
    record_move(aff, "LEND", [{"item_id": a, "qty": 1}], move_date=manila_today() - timedelta(days=45))
    assert flag_long_open() == 1 and _flags(db, "AFFILIATE_LONG_OPEN") == 1 and balances()[0]["long_open"]
