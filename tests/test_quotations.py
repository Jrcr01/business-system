"""Stage C, customers (owner, 2026-10-17: "for quotation we need the quick one", "just build it even i do not need now"; mock-up f_stage_c_customers):
the quick quote kept for a customer as QT-0001; an agreed price used by itself; Make it a sale marks it sold; lapsed after its day; follow-ups and
contact notes; the customer's copy never shows a cost; off on live until switched on."""
import json
from datetime import timedelta

from conftest import fetchone
from tests.test_web_d0 import _admin
from tests.test_web_stock import _item, _stock


def _customer(web, name="RVC Trucking", ctype="FLEET"):
    from engines.sales_rules import save_customer
    ok, msg, cid = save_customer(None, name, ctype, "30 days", "Mang Ruben", "0917", False)
    assert ok, msg
    return cid


def test_a_quick_quote_kept_for_a_customer_then_sold(web, db):
    from engines import quote_rules as qr
    from engines.clock import manila_today
    _admin(web)
    a, _ = _item("Sunny", "205/55R16", "NP226")
    b, _ = _item("Westlake", "205/55R16", "SA37")
    _stock(a, 8, 2550.0)
    _stock(b, 6, 3100.0)
    cid = _customer(web)
    page = web.get("/quotations/new/?size=205%2F55R16").content.decode()
    assert "Save as quotation" in page and "New quotation" in page
    lines = json.loads(web.get("/quotations/data/?size=205%2F55R16").content)["lines"]
    assert [ln["name"] for ln in lines] == ["Sunny NP226", "Westlake SA37"]                 # on hand, cheapest first
    r = web.post("/quotations/new/", {"customer": "RVC Trucking", "good_until": "", "lines": json.dumps([{"item_id": a, "qty": 4, "price": 2650}, {"item_id": b, "qty": 4, "price": 3200}])})
    assert r.status_code == 302
    q = qr.get_quote(int(r["Location"].strip("/").split("/")[-1]))
    assert q["no"] == "QT-0001" and q["customer_id"] == cid and q["total"] == 23400.0 and q["state"] == "WAITING"
    assert q["good_until"] == str(manila_today() + timedelta(days=7))                       # the default life
    printed = web.get(f"/quotations/{q['id']}/?print=1").content.decode()
    assert "QT-0001" in printed and "2,650.00" in printed and "2,550" not in printed and "3,100" not in printed   # never a cost
    assert "2,550" not in qr.share_text(q)
    sale_form = web.get(f"/sales/?view=new&qt={q['id']}").content.decode()
    assert 'name="qt"' in sale_form and "RVC Trucking" in sale_form
    from engines.sales_rules import register_booklet, save_sale
    register_booklet(4501, 4550, "Julian")
    ok, msg, sid = save_sale(4501, manila_today(), cid, "", [{"item_id": a, "qty": 4, "price": 2650}], [], [{"method": "CASH", "amount": 10600}])
    assert ok
    qr.mark_sold(q["id"], sid)
    assert qr.get_quote(q["id"])["state"] == "SOLD"


def test_agreed_prices_follow_ups_and_the_customer_page(web, db):
    from engines import quote_rules as qr
    from engines.clock import manila_today
    _admin(web)
    a, _ = _item("Sunny", "205/55R16", "NP226")
    b, _ = _item("Westlake", "205/55R16", "SA37")
    cid = _customer(web)
    assert qr.set_agreed(cid, None, "Westlake", "", "5")[0]
    assert qr.set_agreed(cid, None, "", "", "3")[0]
    ag = qr.agreed(cid, [a, b])
    assert ag[b]["pct"] == 5 and ag[a]["pct"] == 3                                          # the brand's own wins over "everything"
    assert "not both" in qr.set_agreed(cid, a, "", "100", "5")[1]
    ok, msg, qid = qr.save_quote(cid, "", manila_today(), [{"item_id": a, "qty": 2, "price": 2600}])
    assert ok
    fu = qr.follow_ups()
    assert fu and fu[0]["qt_no"] == "QT-0001" and fu[0]["kind"] == "waiting"
    assert qr.note_contact(cid, "will decide after payday", manila_today(), qid)[0]
    assert any(f["kind"] == "call" for f in qr.follow_ups())
    with __import__("sqlite3").connect(db) as conn:                                          # its day passed
        conn.execute("UPDATE quotations SET good_until = ? WHERE qt_id = ?", (str(manila_today() - timedelta(days=1)), qid))
    assert qr.get_quote(qid)["state"] == "LAPSED" and qr.counts()["lapsed"] == 1
    page = web.get(f"/customers/{cid}/").content.decode()
    assert "Agreed prices" in page and "5% off" in page and "QT-0001" in page and "will decide after payday" in page
    assert "/customers/%d/" % cid in web.get("/customers/").content.decode()
    assert "Follow-ups" in web.get("/follow-ups/").content.decode()
    assert qr.cancel_quote(qid, "bought elsewhere")[0] and qr.get_quote(qid)["state"] == "CANCELLED"


def test_stage_c_is_off_until_switched_on(web, db, monkeypatch):
    monkeypatch.setenv("WEB_STAGE_C", "0")
    _admin(web)
    assert web.get("/quotations/").status_code == 403
    assert "/quotations/" not in web.get("/").content.decode()
