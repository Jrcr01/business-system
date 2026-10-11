"""Stage B part 2: the customers' checks on one list by check date; deposited, then cleared or bounced from the bank statement; a bounced check
on a sale makes the invoice owed again; a bounced check on a receipt is settled by taking the receipt back, so the books stay exact."""
from conftest import fetchone

from tests.test_web_collect import _collect
from tests.test_web_d0 import _admin
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _pdc_sale(web, si="004501", price="5000"):
    """A sale paid by a post-dated check, to a named customer."""
    a, _ = _ready(web)
    web.post("/customers/", {"action": "save", "name": "RGV Trucking", "type": "FLEET", "terms": "30"})
    cust = _n("SELECT customer_id FROM customers WHERE customer_name = 'RGV Trucking'")
    r = _sale(web, a, si=si, qty="2", price=price, customer="RGV Trucking", pay_method_0="CHECK",
              pay_bank_0="BDO", pay_no_0="0044812", pay_date_0="2099-01-15")
    assert r.status_code == 302
    return cust, _n("SELECT MAX(sale_id) FROM sales"), _n("SELECT payment_id FROM sale_payments WHERE method = 'CHECK'")


def test_a_pdc_at_the_counter_waits_on_the_list_and_clears(web, db):
    _admin(web)
    cust, sid, pid = _pdc_sale(web)
    page = web.get("/collect/?view=checks").content.decode()
    assert "0044812" in page and "Post-dated" in page and "RGV Trucking" in page
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "deposited"})
    assert "Deposited" in web.get("/collect/?view=checks").content.decode()
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "cleared"})
    page = web.get("/collect/?view=checks&show=all").content.decode()
    assert "Cleared" in page and "Deposited" not in page.split("0044812")[1][:300]
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'AR_CHECK_CLEARED'") == 1


def test_a_bounced_check_makes_the_invoice_owed_again_and_is_collected_later(web, db):
    from engines.collect_rules import balance
    _admin(web)
    cust, sid, pid = _pdc_sale(web)
    assert balance(cust)["owed"] == 0.0                                           # the PDC made the sale paid (owner, 2026-10-11)
    r = web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "bounced", "note": "account closed"}, follow=True)
    assert "owed again" in r.content.decode()
    assert balance(cust)["owed"] == 10000.0                                       # the receivable returns
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'AR_CHECK_BOUNCED'") == 1
    _collect(web, cust, {sid: "10000"}, pay_method_0="CASH", pay_amount_0="10000")
    assert balance(cust)["owed"] == 0.0                                           # paid in cash after all
    home = web.get("/").content.decode()
    assert "owed by" not in home or "owed by 0" not in home


def test_a_collection_checks_bounce_points_at_taking_the_receipt_back(web, db):
    from engines.collect_rules import balance
    _admin(web)
    cust, sid, _pid = _pdc_sale(web)
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": _pid, "what": "bounced"})
    _collect(web, cust, {sid: "10000"}, pay_method_0="CHECK", pay_amount_0="10000", pay_bank_0="MBT", pay_no_0="99001", pay_date_0="2099-02-02")
    kpid = _n("SELECT payment_id FROM collection_payments WHERE method = 'CHECK'")
    r = web.post("/collect/", {"action": "mark_check", "source": "collection", "payment_id": kpid, "what": "bounced"}, follow=True)
    assert "take the receipt back instead" in r.content.decode()
    assert balance(cust)["owed"] == 0.0                                           # nothing moved
    web.post("/collect/", {"action": "void", "collection_id": _n("SELECT collection_id FROM collections"), "reason": "its check bounced", "c": cust})
    assert balance(cust)["owed"] == 10000.0


def test_a_wrong_mark_goes_back_to_waiting(web, db):
    _admin(web)
    cust, sid, pid = _pdc_sale(web)
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "bounced"})
    from engines.collect_rules import balance
    assert balance(cust)["owed"] == 10000.0
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "waiting"})
    assert balance(cust)["owed"] == 0.0 and _n("SELECT COUNT(*) FROM customer_check_state") == 0
