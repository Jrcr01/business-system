"""Stage B part 1 (owner, 2026-10-11: "the important to us is receivables can be track, any payments thereafter, BIR CR is optional"): a charge
sale is a receivable; collections pay the oldest first, partial fine, the extra stays as the customer's advance; the receipt number is optional;
write-off is the System admin's alone and a later payment lifts it; a receipt can be taken back; hidden behind the stage B switch."""
import pytest

from conftest import fetchone

from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _charged(web, db, si="004501", qty="2", price="5000"):
    """A charge sale to RGV Trucking; returns (customer_id, sale_id)."""
    a, _ = _ready(web)
    web.post("/customers/", {"action": "save", "name": "RGV Trucking", "type": "FLEET", "terms": "30"})
    cust = _n("SELECT customer_id FROM customers WHERE customer_name = 'RGV Trucking'")
    r = _sale(web, a, si=si, qty=qty, price=price, customer="RGV Trucking", pay_method_0="CHARGE")
    assert r.status_code == 302
    return cust, _n("SELECT MAX(sale_id) FROM sales")


def _collect(web, cust, lines, **extra):
    follow = extra.pop("follow", False)
    data = {"action": "collect", "customer_id": cust, "collect_date": fetchone(_db(), "SELECT sale_date FROM sales")[0], "cr_no": ""}
    for sid, amt in lines.items():
        data[f"amt_{sid}"] = amt
    data.update(extra)
    return web.post("/collect/", data, follow=follow)


def test_a_charge_sale_is_owed_and_a_partial_collection_tracks_it(web, db):
    _admin(web)
    cust, sid = _charged(web, db)                                               # 10,000 on charge
    page = web.get("/collect/").content.decode()
    assert "RGV Trucking" in page and "10,000.00" in page
    r = _collect(web, cust, {sid: "4,000"}, pay_method_0="CASH", pay_amount_0="4000")
    assert r.status_code == 302
    page = web.get(f"/collect/?c={cust}").content.decode()
    assert "owes ₱6,000.00" in page and "CR-0001" in page                       # the system's own number: no booklet (owner: "BIR CR is optional")
    assert _n("SELECT COUNT(*) FROM collections") == 1 and _n("SELECT received FROM collections") == 4000.0


def test_paid_more_than_owed_stays_as_the_advance_and_pays_later(web, db):
    _admin(web)
    cust, sid = _charged(web, db)
    r = _collect(web, cust, {sid: "10,000"}, pay_method_0="CASH", pay_amount_0="12000", follow=True)
    from engines.collect_rules import advance_balance, balance
    assert advance_balance(cust) == 2000.0 and balance(cust)["owed"] == 0.0
    # a later charge sale is then paid from the advance
    web.post("/si-booklets/", {"action": "cancel_leaf", "si_no": "4502", "reason": "torn"})
    a2 = _n("SELECT id FROM items")
    _sale(web, a2, si="004503", qty="1", price="1500", customer="RGV Trucking", pay_method_0="CHARGE")
    sid2 = _n("SELECT MAX(sale_id) FROM sales")
    _collect(web, cust, {sid2: "1500"}, pay_method_0="ADVANCE", pay_amount_0="1500")
    assert advance_balance(cust) == 500.0 and balance(cust)["owed"] == 0.0
    _sale(web, a2, si="004504", qty="1", price="900", customer="RGV Trucking", pay_method_0="CHARGE")
    sid3 = _n("SELECT MAX(sale_id) FROM sales")
    r = _collect(web, cust, {sid3: "900"}, pay_method_0="ADVANCE", pay_amount_0="900", follow=True)
    assert b"advance is available" in r.content                                 # only the 500 that is there
    assert balance(cust)["owed"] == 900.0


def test_a_check_needs_its_details_and_more_than_owed_on_a_line_is_refused(web, db):
    _admin(web)
    cust, sid = _charged(web, db)
    r = _collect(web, cust, {sid: "3000"}, pay_method_0="CHECK", pay_amount_0="3000", follow=True)
    assert b"bank, check number and check date" in r.content
    r = _collect(web, cust, {sid: "11000"}, pay_method_0="CASH", pay_amount_0="11000", follow=True)
    assert b"more than the" in r.content
    assert _n("SELECT COUNT(*) FROM collections") == 0


def test_taking_a_receipt_back_makes_the_invoices_owed_again(web, db):
    from engines.collect_rules import balance
    _admin(web)
    cust, sid = _charged(web, db)
    _collect(web, cust, {sid: "10000"}, pay_method_0="CASH", pay_amount_0="10000")
    assert balance(cust)["owed"] == 0.0
    web.post("/collect/", {"action": "void", "collection_id": _n("SELECT collection_id FROM collections"), "reason": "typed on the wrong customer", "c": cust})
    assert balance(cust)["owed"] == 10000.0
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AR_COLLECTION_VOIDED'") == 1


def test_write_off_is_the_admins_and_a_payment_after_lifts_it(web, db):
    from engines.collect_rules import balance
    _admin(web)
    cust, sid = _charged(web, db)
    seller = _sign_in_new("sales@slicks.test", "sales")
    seller.post("/collect/", {"action": "write_off", "sale_id": sid, "reason": "gone"})
    assert _n("SELECT written_off_at FROM sales WHERE sale_id = ?", (sid,)) is None       # not the seller's call
    web.post("/collect/", {"action": "write_off", "sale_id": sid, "reason": "closed shop, unreachable"})
    assert _n("SELECT written_off_at FROM sales WHERE sale_id = ?", (sid,)) is not None
    assert balance(cust)["owed"] == 0.0 and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AR_WRITTEN_OFF'") == 1
    _collect(web, cust, {sid: "2500"}, pay_method_0="CASH", pay_amount_0="2500")          # they pay after all: a recovery
    assert _n("SELECT written_off_at FROM sales WHERE sale_id = ?", (sid,)) is None
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AR_RECOVERED'") == 1
    assert balance(cust)["owed"] == 7500.0


def test_the_opening_receivables_typed_once_by_the_admin(web, db):
    from engines.collect_rules import balance
    _admin(web)
    _ready(web)
    r = web.post("/collect/", {"action": "opening", "op_cust_0": "Mang Ruben", "op_si_0": "4471", "op_date_0": "2026-09-02", "op_amt_0": "7,400",
                               "op_cust_1": "RGV Trucking", "op_amt_1": "43,800"}, follow=True)
    assert b"2 opening receivable(s) saved" in r.content
    cust = _n("SELECT customer_id FROM customers WHERE customer_name = 'Mang Ruben'")
    assert balance(cust)["owed"] == 7400.0
    assert fetchone(_db(), "SELECT sale_date FROM sales WHERE si_no = 4471")[0] == "2026-09-02"      # the notebook's own date is kept
    page = web.get(f"/collect/?c={cust}").content.decode()
    assert "SI 004471" in page and "opening" in page
    seller = _sign_in_new("sales@slicks.test", "sales")
    r = seller.post("/collect/", {"action": "opening", "op_cust_0": "X", "op_amt_0": "1"}, follow=True)
    assert _n("SELECT COUNT(*) FROM sales WHERE is_opening = 1") == 2            # the opening is the System admin's alone


def test_overdue_goes_on_the_review_list_and_the_home_card_counts(web, db):
    _admin(web)
    cust, sid = _charged(web, db)
    from engines.common import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE sales SET sale_date = date(sale_date, '-45 days') WHERE sale_id = ?", (sid,))
    web.get("/collect/")                                                         # the page raises the flags
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AR_OVERDUE'") == 1
    home = web.get("/").content.decode()
    assert "owed by 1 customer(s)" in home and "10,000" in home


def test_stage_b_hides_behind_its_own_switch(web, db, monkeypatch):
    _admin(web)
    monkeypatch.setenv("WEB_STAGE_B", "0")
    assert web.get("/collect/").status_code == 403
    home = web.get("/").content.decode()
    assert 'href="/collect/"' not in home
    assert web.get("/sales/").status_code == 200                                 # stage A unaffected
    monkeypatch.setenv("WEB_STAGE_B", "1")
    assert web.get("/collect/").status_code == 200
