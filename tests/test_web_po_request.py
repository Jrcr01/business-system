"""Ask the supplier first (owner, 2026-10-11: "for purchase order dont list down the items. i will search the product i need to order. calendar should be
seen the first page ... there should be a draft first to be sent to supplier then once we received feedback from them that items are available then thats
the only time it will become PO"): the first screen shows the calendar and a search, a draft is SENT (RQ-0001, an Order request PDF), the supplier's
answer per line makes the purchase order, and a request still waiting is tentative -- counted on the order screens only. No shortcut."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _draft(db, lines=None, name="Acme Tires", days=5):
    from engines.clock import manila_today
    from engines.po_rules import save_po
    from engines.price_list_service import add_price
    cid = make_creditor(db, name, terms="30")
    for item, price in {"Tire A": 50_000, "Tire B": 30_000, "Tube": 1_000}.items():
        add_price(cid, item, "pc", price, date(2026, 9, 1))
    ok, msg, po = save_po(None, cid, manila_today(), manila_today() + timedelta(days=days), lines or [{"item_name": "Tire A", "unit": "pc", "qty": 2}], "")
    assert ok, msg
    return cid, po


def _head(po):
    from engines.po_rules import get_po
    return get_po(po)["header"]


def _lines(po):
    from engines.po_rules import get_po
    return {r.item_name: r for r in get_po(po)["lines"].itertuples()}


def test_the_first_screen_has_the_calendar_and_a_search_and_lists_no_items(web, db):
    _admin(web)
    cid, _ = _draft(db)
    page = web.get(f"/purchase-orders/new/?s={cid}").content.decode()
    assert 'class="cal"' in page and "Payables by pay day" in page            # the calendar before anything is saved
    assert "Type to search this supplier" in page and 'id="pk_q"' in page
    assert "Tire A" not in page.split('<datalist')[0].split('id="picker"')[1]  # no item rows are drawn by the server; the picker shows them only on a search
    assert "Save the draft and add items" not in page


def test_clicking_a_day_on_a_new_order_sets_the_delivery_date(web, db):
    from engines.clock import manila_today
    from engines.po_rules import delivery_for_day, expected_pay_date
    _admin(web)
    cid, _ = _draft(db)
    start = manila_today() + timedelta(days=45)
    monday = start + timedelta(days=(7 - start.weekday()) % 7)
    while delivery_for_day(cid, monday, manila_today()) is None:              # a Monday that is a holiday is never a pay day
        monday += timedelta(days=7)
    page = web.get("/purchase-orders/new/", {"s": cid, "order_date": manila_today().isoformat(), "day": monday.isoformat()}).content.decode()
    delivery = date.fromisoformat(page.split('id="delivery" type="date" name="delivery" value="')[1][:10])
    assert expected_pay_date(cid, delivery) == monday and f"{monday:%a %m/%d/%Y}" in page


def test_send_gives_a_request_number_and_no_po_number(web, db):
    from engines.po_rules import request_po
    _admin(web)
    cid, po = _draft(db)
    assert web.get(f"/purchase-orders/{po}/request-pdf/").status_code == 404   # a plain draft has nothing to send
    r = web.post(f"/purchase-orders/{po}/", {"action": "request"})
    h = _head(po)
    assert r.status_code == 302 and h["status"] == "DRAFT" and h["request_no"] == "RQ-0001" and h["requested_at"] and str(h["po_no"]).startswith("DRAFT-")
    page = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "Request RQ-0001" in page and "Sent to supplier" in page and "Supplier confirmed" in page and "Order it" not in page
    pdf = web.get(f"/purchase-orders/{po}/request-pdf/")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF" and "Order_request_RQ-0001" in pdf["Content-Disposition"]
    assert web.get(f"/purchase-orders/{po}/pdf/").status_code == 404           # not a purchase order yet
    assert request_po(po)[0] and _head(po)["request_no"] == "RQ-0001"          # sending again changes nothing
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_REQUESTED'")[0] == 1
    listing = web.get("/purchase-orders/?show=SENT").content.decode()
    assert "RQ-0001" in listing and "Sent to supplier" in listing
    assert "RQ-0001" not in web.get("/purchase-orders/?show=DRAFT").content.decode()


def test_a_draft_cannot_become_a_po_without_the_request_step(web, db):
    from engines.po_rules import confirm_request, order_po
    _admin(web)
    cid, po = _draft(db)
    ok, msg = order_po(po)
    assert not ok and "Send this draft to the supplier first" in msg
    assert not confirm_request(po)[0]
    web.post(f"/purchase-orders/{po}/", {"action": "order"})
    from engines.po_rules import po_stamp
    web.post(f"/purchase-orders/{po}/edit/", {"action": "order", "stamp": po_stamp(po), "order_date": str(_head(po)["order_date"])[:10], "delivery": str(_head(po)["expected_delivery_date"])[:10]})
    assert _head(po)["status"] == "DRAFT"
    assert "Send to supplier" in web.get(f"/purchase-orders/{po}/edit/").content.decode()


def test_all_available_becomes_the_purchase_order(web, db):
    from engines.po_rules import po_stamp
    _admin(web)
    cid, po = _draft(db)
    web.post(f"/purchase-orders/{po}/", {"action": "request"})
    page = web.get(f"/purchase-orders/{po}/confirm/").content.decode()
    assert "The supplier's answer" in page and "Asked" in page and "Available" in page
    r = web.post(f"/purchase-orders/{po}/confirm/", {"stamp": po_stamp(po), "delivery": str(_head(po)["expected_delivery_date"])[:10]})
    h = _head(po)
    assert r["Location"] == f"/purchase-orders/{po}/" and h["status"] == "ORDERED" and " - PO 0001" in h["po_no"] and h["request_no"] == "RQ-0001"
    page = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "From request RQ-0001" in page and "Download PO (PDF)" in page
    assert web.get(f"/purchase-orders/{po}/pdf/").status_code == 200 and web.get(f"/purchase-orders/{po}/request-pdf/").status_code == 404


def test_part_available_only_that_becomes_the_po_and_what_was_asked_is_kept(web, db):
    from engines.po_rules import po_stamp
    _admin(web)
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 10}, {"item_name": "Tire B", "unit": "pc", "qty": 4}, {"item_name": "Tube", "unit": "pc", "qty": 20}])
    h = _head(po)                                                              # ₱640,000: over the daily limit, so sending asks first
    assert web.post(f"/purchase-orders/{po}/", {"action": "request"})["Location"].endswith("?confirm=1") and not _head(po)["requested_at"]
    web.post(f"/purchase-orders/{po}/edit/", {"action": "request", "confirm_over": "1", "stamp": po_stamp(po), "order_date": str(h["order_date"])[:10],
                                              "delivery": str(h["expected_delivery_date"])[:10]})
    assert _head(po)["requested_at"]
    ln = _lines(po)
    later = date.fromisoformat(str(_head(po)["expected_delivery_date"])[:10]) + timedelta(days=3)
    r = web.post(f"/purchase-orders/{po}/confirm/", {"stamp": po_stamp(po), "delivery": later.isoformat(), f"avail_{ln['Tire A'].line_id}": "6",
                                                    f"avail_{ln['Tire B'].line_id}": "0", f"avail_{ln['Tube'].line_id}": "20", "confirm_over": "1"})
    assert r.status_code == 302
    after = _lines(po)
    assert set(after) == {"Tire A", "Tube"}                                    # the line with nothing available is dropped
    assert (after["Tire A"].qty, after["Tire A"].qty_requested, after["Tire A"].est_amount) == (6.0, 10.0, 300_000.0)
    assert (after["Tube"].qty, after["Tube"].qty_requested) == (20.0, 20.0)
    h = _head(po)
    assert h["status"] == "ORDERED" and str(h["expected_delivery_date"])[:10] == later.isoformat()      # the date the supplier promised
    note = fetchone(_db(), "SELECT new_values FROM audit_logs WHERE user_action = 'PO_CONFIRMED'")[0]
    assert "RQ-0001" in note and "asked 640,000.00, confirmed 320,000.00" in note and "dropped: Tire B" in note and "Tire A 6 of 10" in note
    assert "of 10 asked" in web.get(f"/purchase-orders/{po}/").content.decode()


def test_more_than_asked_is_refused_and_nothing_available_cancels(web, db):
    from engines.po_rules import confirm_request, request_po
    _admin(web)
    cid, po = _draft(db)
    assert request_po(po)[0]
    lid = int(_lines(po)["Tire A"].line_id)
    ok, msg = confirm_request(po, {lid: 3})
    assert not ok and "between 0 and the 2 asked" in msg and _head(po)["status"] == "DRAFT"
    ok, msg = confirm_request(po, {lid: 0})
    assert ok and "cancelled" in msg and _head(po)["status"] == "CANCELLED"


def test_a_request_waiting_is_tentative_counted_on_the_order_screens_only(web, db):
    from engines import day_cap
    from engines.clock import manila_today
    from engines.po_rules import confirm_request, day_guard, open_commitments, request_po, request_rows
    from engines.reports_service import get_payment_schedule_all_tracks
    _admin(web)
    cid, po = _draft(db)                                                       # ₱100,000
    h = _head(po)
    pay = date.fromisoformat(str(h["expected_pay_date"])[:10])
    assert day_guard(cid, h["expected_delivery_date"], 1.0)["already"] == 0.0   # a plain draft counts nowhere
    assert request_po(po)[0]
    assert day_guard(cid, h["expected_delivery_date"], 1.0)["already"] == 100_000.0          # someone else's order on that day sees it
    assert day_guard(cid, h["expected_delivery_date"], 1.0, exclude_po_id=po)["already"] == 0.0   # the request itself is not counted twice
    rows = request_rows(pay)
    assert list(rows["reference"]) == ["Request RQ-0001 (not confirmed)"] and list(rows["kind"]) == ["Order requests"]
    today = manila_today()
    assert day_cap.day_totals(today, pay).get(pay, 0.0) == 0.0                 # the Payables calendar, the limit on checks, Home: not there
    assert day_cap.day_totals(today, pay, requests=True).get(pay, 0.0) == 100_000.0
    assert open_commitments(today, pay + timedelta(days=5)).empty              # not owed, not committed
    sched = get_payment_schedule_all_tracks()
    assert sched.empty or not sched["reference"].astype(str).str.contains("RQ-0001").any()
    other = _draft(db, name="Other Supplier")[1]
    assert "Request RQ-0001 (not confirmed)" in web.get(f"/purchase-orders/{other}/edit/").content.decode()   # another order due that week lists it
    assert confirm_request(po)[0]                                              # once confirmed it is a real commitment
    assert day_cap.day_totals(today, pay).get(pay, 0.0) == 100_000.0 and request_rows(pay).empty


def test_take_back_keeps_the_number_and_a_change_keeps_the_request(web, db):
    from engines.po_rules import po_stamp, request_po
    _admin(web)
    cid, po = _draft(db)
    web.post(f"/purchase-orders/{po}/", {"action": "request"})
    h = _head(po)
    web.post(f"/purchase-orders/{po}/edit/", {"action": "update", "stamp": po_stamp(po), "order_date": str(h["order_date"])[:10], "delivery": str(h["expected_delivery_date"])[:10],
                                              "qty_0": "3", "list_price_0": "50000", "chain_0": "0", "unit_0": "pc"})
    h = _head(po)
    assert h["request_no"] == "RQ-0001" and h["requested_at"] and _lines(po)["Tire A"].qty == 3.0      # changed while with the supplier: still the same request
    web.post(f"/purchase-orders/{po}/", {"action": "unsend"})
    h = _head(po)
    assert h["requested_at"] is None and h["request_no"] == "RQ-0001"
    assert request_po(po)[0] and _head(po)["request_no"] == "RQ-0001"          # sent again under the same number
    _, second = _draft(db, name="Second Supplier")
    assert request_po(second)[0] and _head(second)["request_no"] == "RQ-0002"


def test_a_person_who_may_only_look_cannot_send_or_confirm(web, db):
    from engines.po_rules import request_po
    _admin(web)
    cid, po = _draft(db)
    assert request_po(po)[0]
    from engines.auth_service import set_access
    from tests.test_web_d0 import fetchone_uid
    other = _sign_in_new("brother@slicks.test", "owner")
    uid = fetchone_uid("brother@slicks.test")
    from engines.permissions import ACCESS_ROWS, access_from_choices
    levels = {page: "see" for _, rows in ACCESS_ROWS for page, _, _, _ in rows}             # See everywhere, do nowhere
    hidden, denied = access_from_choices(levels, set(), set())
    assert set_access(uid, hidden, denied)[0]
    other.post(f"/purchase-orders/{po}/", {"action": "unsend"})
    other.post(f"/purchase-orders/{po}/confirm/", {"delivery": str(_head(po)["expected_delivery_date"])[:10]})
    h = _head(po)
    assert h["status"] == "DRAFT" and h["requested_at"]


def test_the_going_out_tab_is_gone(web, db):
    _admin(web)
    page = web.get("/purchase-orders/").content.decode()
    assert "Going out" not in page and "tab=out" not in page
    old = web.get("/purchase-orders/?tab=out")                                 # an old link opens the Orders tab
    assert old.status_code == 200 and "No orders here yet" in old.content.decode()


def test_home_says_how_many_requests_wait_for_the_supplier(db):
    from engines.home_tiles import _purchase_orders
    from engines.po_rules import request_po
    _, po = _draft(db)
    assert request_po(po)[0]
    assert "1 waiting for the supplier" in _purchase_orders()[1]


# --- the card reserve (owner, 2026-10-11) -----------------------------------------------------------------------------------------------------

def _card(db, statement_day=None):
    bank = make_creditor(db, "RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, statement_day) VALUES (?, 'Platinum', '5003', 1, ?)",
                            (bank, statement_day)).lastrowid


def _month(today, back, day):
    y, m = today.year, today.month - back
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, day)


def test_the_reserve_sits_on_the_statement_day_plus_the_average_gap(db):
    from engines.cc_dates import statement_date_for
    from engines.clock import manila_today
    from engines.day_cap import _bank_day, card_reserve_rows
    card, today = _card(db, statement_day=20), manila_today()
    with sqlite3.connect(db) as conn:
        for back, gap in ((3, 21), (2, 20), (1, 22)):                           # statement on the 20th, due 21, 20 and 22 days later: the usual gap is 21
            due = _month(today, back, 20) + timedelta(days=gap)
            assert statement_date_for(due, 20) == _month(today, back, 20)
            conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, 100000, 'Paid')", (card, due.isoformat()))
    rows = card_reserve_rows(today - timedelta(days=5), today + timedelta(days=80))
    assert not rows.empty
    first = rows.sort_values("day").iloc[0]
    assert first["day"] == _bank_day(_month(today, 0, 20) + timedelta(days=21))
    assert "21 days after the statement day (20)" in first["reference"] and first["amount"] == 100_000.0


def test_the_reserve_leaves_out_the_unpaid_balance_carried_over(db):
    from engines.clock import manila_today
    from engines.day_cap import card_reserve_rows
    card, today = _card(db), manila_today()
    with sqlite3.connect(db) as conn:                                          # 80,000; then 150,000 of which 50,000 was unpaid from before; then 120,000
        for back, total, new, unpaid_next in ((3, 80_000, 0, 50_000), (2, 150_000, 0, 0), (1, 120_000, 120_000, 0)):
            conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, new_charges, unpaid_balance_next, status) VALUES (?, ?, ?, ?, ?, 'Paid')",
                         (card, _month(today, back, 15).isoformat(), total, new, unpaid_next))
    rows = card_reserve_rows(today, today + timedelta(days=95))
    assert not rows.empty and set(rows["amount"]) == {100_000.0}               # (80,000 + 100,000 + 120,000) / 3, not 116,666.67


# --- one Receive page; the DR is the payable; one DR per order unless the switch says otherwise (owner, 2026-10-11) -------------------------------
# "receive goods and match invoice can be combined"; "the important is either DR or SI is recorded"; "our suppliers dont give SI just DR then that only
# binds us"; "make it a option first because since we have now a PO we can ask them to give 1 dr or si per PO".

def _ordered(db, lines=None):
    from engines.po_rules import confirm_request, request_po
    cid, po = _draft(db, lines)
    assert request_po(po)[0] and confirm_request(po)[0]
    return cid, po, int(_lines(po)["Tire A"].line_id)


def _arrive(web, cid, lid, qty, price="50000", **extra):
    data = {"s": cid, "action": "arrive", f"pick_{lid}": "1", f"qty_{lid}": str(qty), f"price_{lid}": price, "doc": "", "dr": "", "inv_date": "2026-10-11", "batch": ""}
    data.update(extra)
    return web.post("/purchase-orders/receive/", data)


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _many(on=True):
    from engines.settings_store import set_setting
    set_setting("po_many_drs", "1" if on else "0")


def test_with_the_si_the_goods_and_the_payable_are_recorded_together(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    page = web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
    assert f'name="pick_{lid}" value="1" checked' in page and "Goods still to come" in page and "Record what arrived" in page
    assert "</span>Match invoice</a>" not in page and "Goods only" not in page   # one Receive button on the Purchase orders screens
    _arrive(web, cid, lid, 2, doc="SI-501", dr="DR-9")
    assert _head(po)["status"] == "RECEIVED"
    assert fetchone(_db(), "SELECT amount, dr_no FROM invoices WHERE doc_ref = 'SI-501'") == (100_000.0, "9")


def test_a_dr_alone_records_the_payable(web, db):
    from engines.statement_rules import resolve_due_date
    _admin(web)
    cid, po, lid = _ordered(db)
    _arrive(web, cid, lid, 2, price="49000", dr="DR-77")
    assert _head(po)["status"] == "RECEIVED"
    doc, amount, dr_no, due, status = fetchone(_db(), "SELECT doc_ref, amount, dr_no, due_date, status FROM invoices")
    assert (doc, amount, dr_no, status) == ("DR 77", 98_000.0, "77", "Unpaid")      # the DR is the document, at the price actually charged
    assert str(due)[:10] == str(resolve_due_date(date(2026, 10, 11), "30"))[:10]    # owed on its due date like an SI
    page = web.get("/purchase-orders/receive/").content.decode()
    assert "document not recorded yet" not in page and "Nothing to receive" in page      # nothing waits for an SI that will never come
    order = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "Deliveries" in order and "DR 77" in order and "2 × Tire A" in order and "not in a payment statement yet" in order


def test_neither_si_nor_dr_is_refused(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    r = web.post("/purchase-orders/receive/", {"s": cid, "action": "arrive", f"pick_{lid}": "1", f"qty_{lid}": "2", f"price_{lid}": "50000", "doc": "", "dr": ""}, follow=True)
    assert "Type the SI no. or the DR no.: at least one." in r.content.decode() and _head(po)["status"] == "ORDERED"


def test_one_order_chosen_shows_only_its_lines(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    from engines.clock import manila_today
    from engines.po_rules import confirm_request, request_po, save_po
    one = web.get(f"/purchase-orders/receive/?s={cid}").content.decode()        # one order waiting: it is the one that arrived, already ticked
    assert " checked" in one.split('id="form_r"')[1].split("</table>")[0]
    ok, _, second = save_po(None, cid, manila_today(), manila_today() + timedelta(days=9), [{"item_name": "Tire B", "unit": "pc", "qty": 1}], "")
    assert ok and request_po(second)[0] and confirm_request(second)[0]
    both = web.get(f"/purchase-orders/receive/?s={cid}").content.decode()
    assert "Tire A" in both and "Tire B" in both and " checked" not in both.split('id="form_r"')[1].split("</table>")[0]
    one = web.get(f"/purchase-orders/receive/?s={cid}&po={second}").content.decode()
    table = one.split('id="form_r"')[1].split("</table>")[0]
    assert "Tire B" in table and "Tire A" not in table and " checked" in table


def test_receive_lists_only_the_suppliers_with_something_to_receive(web, db):
    _admin(web)
    make_creditor(db, "No Orders Trading")
    _draft(db, name="Only A Draft Corp")                                       # a draft (or a request) is not something to receive
    assert "Nothing to receive" in web.get("/purchase-orders/receive/").content.decode()
    cid, po, lid = _ordered(db)
    pick = web.get("/purchase-orders/receive/").content.decode().split('id="s"')[1].split("</select>")[0]
    assert "Acme Tires" in pick and "No Orders Trading" not in pick and "Only A Draft Corp" not in pick
    _arrive(web, cid, lid, 2, dr="DR-1")
    assert "Nothing to receive" in web.get("/purchase-orders/receive/").content.decode()


def test_one_dr_per_order_a_short_delivery_asks_first(web, db):
    _admin(web)
    cid, po, lid = _ordered(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}])
    r = _arrive(web, cid, lid, 4, dr="DR-1")
    page = r.content.decode()
    assert r.status_code == 200 and "An order should come on one DR or SI" in page and "2 pc(s) have not arrived" in page
    assert "The rest will not come" in page and "Keep it open for another DR" in page
    assert _head(po)["status"] == "ORDERED" and _n("SELECT COUNT(*) FROM invoices") == 0 and _n("SELECT COUNT(*) FROM po_receipts") == 0      # nothing saved yet
    _arrive(web, cid, lid, 4, dr="DR-1", rest="close")
    assert _head(po)["status"] == "RECEIVED" and _lines(po)["Tire A"].qty_received == 4.0
    assert _n("SELECT amount FROM invoices WHERE doc_ref = 'DR 1'") == 200_000.0
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_CLOSED_SHORT'") == 1 and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_MORE_THAN_ONE_DR'") == 0
    from engines.po_rules import open_commitments
    from engines.clock import manila_today
    assert open_commitments(manila_today(), manila_today() + timedelta(days=90)).empty     # the 2 that will not come stopped counting


def test_one_dr_per_order_kept_open_is_recorded_and_flagged(web, db):
    _admin(web)
    cid, po, lid = _ordered(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}])
    _arrive(web, cid, lid, 4, dr="DR-1", rest="open")
    assert _head(po)["status"] == "PARTIAL" and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_MORE_THAN_ONE_DR'") == 1
    assert "Still to come: 2 pc(s)" in web.get(f"/purchase-orders/{po}/").content.decode()
    _arrive(web, cid, lid, 2, dr="DR-2")                                        # the rest comes: nothing remains, no question
    assert _head(po)["status"] == "RECEIVED" and _n("SELECT COUNT(*) FROM invoices") == 2


def test_the_whole_order_on_one_dr_asks_nothing(web, db):
    _admin(web)
    cid, po, lid = _ordered(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}])
    assert _arrive(web, cid, lid, 6, dr="DR-1").status_code == 302
    assert _head(po)["status"] == "RECEIVED" and _n("SELECT COUNT(*) FROM invoices") == 1 and _n("SELECT COUNT(*) FROM review_flags WHERE kind LIKE 'PO_%'") == 0


def test_switch_on_three_drs_fill_one_order(web, db):
    from engines.po_rules import deliveries
    _admin(web)
    _many()
    cid, po, lid = _ordered(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}])
    assert _arrive(web, cid, lid, 2, dr="DR-1").status_code == 302 and _head(po)["status"] == "PARTIAL"      # no question
    assert "Still to come: 4 pc(s)" in web.get(f"/purchase-orders/{po}/").content.decode()
    _arrive(web, cid, lid, 2, dr="DR-2")
    assert _head(po)["status"] == "PARTIAL" and "Still to come: 2 pc(s)" in web.get(f"/purchase-orders/{po}/").content.decode()
    _arrive(web, cid, lid, 2, dr="DR-3")
    assert _head(po)["status"] == "RECEIVED" and "Still to come" not in web.get(f"/purchase-orders/{po}/").content.decode()
    ds = deliveries(po)
    assert [d["doc"] for d in ds] == ["DR 1", "DR 2", "DR 3"] and all(d["items"] == "2 × Tire A" and d["amount"] == 100_000.0 for d in ds)
    assert _n("SELECT COUNT(*) FROM invoices") == 3 and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_MORE_THAN_ONE_DR'") == 0
    r = _arrive(web, cid, lid, 1, dr="DR-3")                                    # nothing is left to receive on it


def test_the_same_dr_number_twice_is_refused(web, db):
    _admin(web)
    _many()
    cid, po, lid = _ordered(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}])
    _arrive(web, cid, lid, 2, dr="DR-1")
    r = web.post("/purchase-orders/receive/", {"s": cid, "action": "arrive", f"pick_{lid}": "1", f"qty_{lid}": "2", f"price_{lid}": "50000", "doc": "", "dr": "DR 1",
                                               "inv_date": "2026-10-12", "batch": ""}, follow=True)
    assert "already exists for this supplier" in r.content.decode()
    assert _n("SELECT COUNT(*) FROM invoices") == 1 and _lines(po)["Tire A"].qty_received == 2.0


def test_more_than_ordered_asks_then_is_received_and_flagged(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)                                                 # 2 ordered
    r = _arrive(web, cid, lid, 3, dr="DR-1")
    assert r.status_code == 200 and "More than ordered" in r.content.decode() and "1 more than ordered" in r.content.decode()
    assert _n("SELECT COUNT(*) FROM invoices") == 0
    _arrive(web, cid, lid, 3, dr="DR-1", confirm_more="1")
    ln = _lines(po)["Tire A"]
    assert (ln.qty, ln.qty_received) == (2.0, 3.0) and _head(po)["status"] == "RECEIVED"       # the order keeps what was agreed; the delivery what came
    assert _n("SELECT amount FROM invoices") == 150_000.0 and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_RECEIVED_MORE'") == 1
    assert "3 received of 2 ordered" in web.get(f"/purchase-orders/{po}/").content.decode()


def test_an_item_that_was_not_on_the_order_is_added_at_delivery(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    page = web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
    assert "An item came that was not on the order" in page and 'id="pk_q"' in page
    _arrive(web, cid, lid, 2, dr="DR-5", po=str(po), pick_name_0="Tire B", pick_qty_0="1", pick_unit_0="pc")
    after = _lines(po)
    assert after["Tire B"].added_at_delivery == 1 and after["Tire B"].qty_received == 1.0 and _head(po)["status"] == "RECEIVED"
    assert _n("SELECT amount FROM invoices WHERE doc_ref = 'DR 5'") == 130_000.0
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_ITEM_ADDED_AT_DELIVERY'") == 1
    order = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "added at delivery" in order and "1 × Tire B" in order


def test_undo_a_delivery_puts_it_back_and_is_refused_once_the_batch_is_locked(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    _arrive(web, cid, lid, 2, dr="DR-77")
    rid = _n("SELECT receipt_id FROM po_receipts")
    r = web.post(f"/purchase-orders/{po}/", {"action": "undo_delivery", "receipt_id": rid, "reason": ""}, follow=True)
    assert "Say why the delivery is undone" in r.content.decode() and _n("SELECT COUNT(*) FROM invoices") == 1
    web.post(f"/purchase-orders/{po}/", {"action": "undo_delivery", "receipt_id": rid, "reason": "wrong quantity typed"})
    ln = _lines(po)["Tire A"]
    assert _n("SELECT COUNT(*) FROM invoices") == 0 and _n("SELECT COUNT(*) FROM po_receipts") == 0 and _n("SELECT COUNT(*) FROM po_receipt_lines") == 0
    assert ln.qty_received == 0.0 and _head(po)["status"] == "ORDERED"
    note = _n("SELECT new_values FROM audit_logs WHERE user_action = 'PO_RECEIPT_UNDONE'")
    assert "DR 77" in note and "wrong quantity typed" in note and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_RECEIPT_UNDONE'") == 1
    _arrive(web, cid, lid, 2, dr="DR-78", batch="new")                          # recorded again, correctly, into a batch
    assert _head(po)["status"] == "RECEIVED" and _n("SELECT doc_ref FROM invoices") == "DR 78"
    rid = _n("SELECT receipt_id FROM po_receipts")
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE order_statements SET is_locked = 1")
    r = web.post(f"/purchase-orders/{po}/", {"action": "undo_delivery", "receipt_id": rid, "reason": "try"}, follow=True)
    assert "can't be undone" in r.content.decode() and "credit memo" in r.content.decode() and _n("SELECT COUNT(*) FROM invoices") == 1


def test_an_si_that_comes_later_is_added_to_the_same_payable(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    _arrive(web, cid, lid, 2, dr="DR-77")
    rid = _n("SELECT receipt_id FROM po_receipts")
    assert "Add the SI no." in web.get(f"/purchase-orders/{po}/").content.decode()
    web.post(f"/purchase-orders/{po}/", {"action": "add_si", "receipt_id": rid, "si": "SI-900"})
    assert fetchone(_db(), "SELECT doc_ref, dr_no, amount FROM invoices") == ("SI-900", "77", 100_000.0) and _n("SELECT COUNT(*) FROM invoices") == 1
    order = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "SI-900" in order and "DR 77" in order and "Add the SI no." not in order


def test_receiving_always_records_the_payable_whoever_receives(web, db):
    # owner, 2026-10-11: "it cant be happen they give goods without documentatin"; "receiving always records the payable" -- one switch, one step
    _admin(web)
    cid, po, lid = _ordered(db)
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")                      # Purchasing: may receive; batches are the payables side's
    page = buyer.get(f"/purchase-orders/receive/?s={cid}").content.decode()
    assert 'name="doc"' in page and 'name="dr"' in page and "Record what arrived" in page and 'name="batch"' not in page
    buyer.post("/purchase-orders/receive/", {"s": cid, "action": "arrive", f"pick_{lid}": "1", f"qty_{lid}": "2", f"price_{lid}": "49,500.00", "doc": "", "dr": "DR-5",
                                             "inv_date": "2026-10-11", "batch": "new"})
    assert _head(po)["status"] == "RECEIVED"
    assert fetchone(_db(), "SELECT doc_ref, dr_no, amount, statement_id FROM invoices") == ("DR 5", "5", 99_000.0, None)      # the payable, at once; not in a batch (not theirs to choose)
    assert _n("SELECT invoice_id FROM po_receipts") is not None
    assert "payable not recorded" not in web.get("/purchase-orders/receive/").content.decode() and "Nothing to receive" in web.get("/purchase-orders/receive/").content.decode()


def test_the_two_older_switches_become_one(web, db):
    import sqlite3

    import engines.auth_service as au
    from tests.test_web_d0 import fetchone_uid
    _admin(web)
    _sign_in_new("family@slicks.test", "owner")
    _sign_in_new("cousin@slicks.test", "owner")
    a, b = fetchone_uid("family@slicks.test"), fetchone_uid("cousin@slicks.test")
    from engines.settings_store import set_setting
    set_setting("switches_receive_one_2026_10_11", "0")
    with sqlite3.connect(_db()) as conn:                                         # one had only "receive goods"; the other had neither
        conn.execute("DELETE FROM user_restrictions WHERE action IN ('po.receive_goods', 'po.receive')")
        conn.execute("INSERT INTO user_restrictions (user_id, action) VALUES (?, 'po.receive')", (a,))
        conn.execute("INSERT INTO user_restrictions (user_id, action) VALUES (?, 'po.receive'), (?, 'po.receive_goods')", (b, b))
    au.split_new_switches()
    assert not ({"po.receive", "po.receive_goods"} & au.get_restrictions(a))    # could receive: still can, and it records the payable
    assert {"po.receive", "po.receive_goods"} <= au.get_restrictions(b)         # had neither: still neither
    page = web.get(f"/settings/users/{a}/").content.decode()
    assert "Receive goods with their DR or SI (this records the payable)" in page and "Record the SI or DR (the payable)" not in page

def test_an_added_item_takes_the_price_typed_from_the_document(web, db):
    _admin(web)
    cid, po, lid = _ordered(db)
    page = web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
    assert 'data-price="1"' in page and "Actual price" in page
    _arrive(web, cid, lid, 2, dr="DR-5", po=str(po), pick_name_0="Tire B", pick_qty_0="1", pick_unit_0="pc", pick_price_0="29000")
    assert _n("SELECT amount FROM invoices WHERE doc_ref = 'DR 5'") == 129_000.0     # 100,000 + the 29,000 on the DR, not the list's 30,000
    assert _lines(po)["Tire B"].actual_unit_price == 29_000.0


def test_a_delivery_shows_its_batchs_checks_and_credit_memos(web, db):
    from engines.po_rules import deliveries
    _admin(web)
    cid, po, lid = _ordered(db)
    _arrive(web, cid, lid, 2, dr="DR-77", batch="new")
    d = deliveries(po)[0]
    assert d["checks"] == "no check yet" and d["memos"] == []
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) VALUES (?, 1, ?, '2026-11-11', 60000, 'Active')", (cid, d["statement_id"]))
        conn.execute("INSERT INTO credit_memos (statement_id, memo_ref, memo_date, amount, reason) VALUES (?, 'CM-1', '2026-10-12', 5000, 'one tire returned')", (d["statement_id"],))
    d = deliveries(po)[0]
    assert d["checks"] == "check parked 11/11/2026" and d["memos"][0]["ref"] == "CM-1" and d["undo_block"]      # a check exists: no undo, a credit memo instead
    page = web.get(f"/purchase-orders/{po}/").content.decode()
    assert "check parked 11/11/2026" in page and "credit memo CM-1" in page and "one tire returned" in page and "can't be undone" in page


def test_the_week_line_counts_what_the_calendar_counts(db):
    from engines import day_cap
    from engines.clock import manila_today
    from engines.po_rules import month_calendar, request_po, week_schedule, weeks_strip
    card, today = _card(db), manila_today()
    with sqlite3.connect(db) as conn:                                          # a card with statements: its coming months carry a reserve the old week list left out
        for back in (3, 2, 1):
            conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, 90000, 'Paid')", (card, _month(today, back, 15).isoformat()))
    _, po = _draft(db)
    assert request_po(po)[0]                                                    # and a request waiting for the supplier
    rows = day_cap.loads(today, today + timedelta(days=70), requests=True)
    assert not rows.empty and (rows["kind"] == "Order requests").any() and rows["reference"].str.startswith("Card reserve").any()
    for day in sorted(set(rows["day"])):
        wk = week_schedule(day)
        in_week = rows[(rows["day"] >= wk["week_start"]) & (rows["day"] <= wk["week_end"])]
        assert round(wk["total"], 2) == round(float(in_week["amount"].sum()), 2)
        strip = weeks_strip(day)
        assert round(float(strip[strip["selected"]]["total"].iloc[0]), 2) == round(wk["total"], 2)
        cells = [c for w in month_calendar(day, day)["weeks"] for c in w if wk["week_start"] <= c["day"] <= wk["week_end"]]
        assert round(sum(c["total"] for c in cells), 2) == round(wk["total"], 2)   # the calendar's days of that week add up to the week line


def test_one_menu_entry_for_receive(web, db):
    _admin(web)
    page = web.get("/").content.decode()
    assert 'href="/purchase-orders/?mode=receive"' in page and "Match invoices" not in page
    assert ">Record the SI or DR<" not in page                                 # no second card: goods never come without their DR or SI (owner, 2026-10-11)
    payables = _sign_in_new("ap@slicks.test", "payables")                     # records payables, does not receive goods: still finds Receive in the menu
    assert 'href="/purchase-orders/?mode=receive"' in payables.get("/").content.decode()
