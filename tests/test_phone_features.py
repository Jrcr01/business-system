"""Phone features 2, 4, 5, 6 (owner, 2026-10-17: "All four" of docs/mockups/ak_phone_features.html): a receipt photo kept with an expense, a check's
photos with a collection (both shown only to whoever may open their page), a tire scanned to its stock and price, Share buttons that never carry a
cost or a margin."""
import io
import os

from django.core.files.uploadedfile import SimpleUploadedFile

from tests.test_phone import _db, _n, _on
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def _jpg(color="white"):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (2400, 1800), color).save(buf, format="JPEG")
    return SimpleUploadedFile("receipt.jpg", buf.getvalue(), content_type="image/jpeg")


def test_an_expense_keeps_its_receipt_photo_made_smaller(web, db):
    _admin(web)
    from engines.expense_rules import categories
    cat = categories()[0]["category_id"]
    r = web.post("/expenses/", {"action": "add", "expense_date": "2026-10-17", "category_id": cat, "paid_from": "Cash (drawer)", "detail": "ink, OR 4512",
                                "amount": "1,250", "receipt": _jpg()}, follow=True)
    page = r.content.decode()
    assert "Receipt photo kept." in page and 'href="/photos/' in page
    pid = _n("SELECT photo_id FROM photos WHERE ref_type = 'expenses' AND kind = 'receipt'")
    pic = web.get(f"/photos/{pid}.jpg")
    assert pic.status_code == 200 and pic["Content-Type"] == "image/jpeg"
    from PIL import Image
    from engines.photos import one
    assert max(Image.open(one(pid)["path"]).size) == 1600                                            # made smaller on the way in
    r = web.post("/expenses/", {"action": "add", "expense_date": "2026-10-17", "category_id": cat, "paid_from": "Cash (drawer)", "detail": "x",
                                "amount": "10", "receipt": SimpleUploadedFile("x.jpg", b"not a picture", content_type="image/jpeg")}, follow=True)
    assert "The photo was not kept: That file is not a picture" in r.content.decode()                  # the expense itself is saved
    rack = _sign_in_new("rack@slicks.test", "scanner")
    assert rack.get(f"/photos/{pid}.jpg").status_code != 200                                          # only whoever may open Expenses


def test_a_collection_keeps_the_checks_front_and_back(web, db):
    from tests.test_web_collect import _charged
    _admin(web)
    cust, sid = _charged(web, db)
    page = web.get(f"/collect/?c={cust}").content.decode()
    assert 'name="check_front"' in page and 'name="check_back"' in page and 'enctype="multipart/form-data"' in page
    r = web.post("/collect/", {"action": "collect", "customer_id": cust, f"amt_{sid}": "4,000", "collect_date": "2026-10-17", "pay_method_0": "CHECK",
                               "pay_amount_0": "4000", "pay_bank_0": "BDO", "pay_no_0": "0001234", "pay_date_0": "2026-10-30",
                               "check_front": _jpg("lightgreen"), "check_back": _jpg("lightgray")}, follow=True)
    page = r.content.decode()
    assert "Check, front photo kept." in page and "Check, back photo kept." in page
    assert _n("SELECT COUNT(*) FROM photos WHERE ref_type = 'collections'") == 2 and page.count('href="/photos/') == 2


def test_a_scanned_tire_shows_its_stock_and_only_some_see_the_price(web, db):
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 12)
    from engines.common import get_conn
    with get_conn() as conn:
        code = conn.execute("SELECT code FROM items WHERE id = ?", (a,)).fetchone()[0]
    assert 'href="/phone/look/"' in web.get("/phone/").content.decode()
    d = web.get(f"/phone/look/?code={code}").json()
    assert d["ok"] and d["on_hand"] == 12 and "prices" in d
    assert web.get("/phone/look/?code=NOPE123").json()["ok"] is False
    rack = _sign_in_new("rack@slicks.test", "scanner")
    assert "prices" not in rack.get(f"/phone/look/?code={code}").json()                               # never on the staff phone


def test_share_buttons_carry_no_cost(web, db):
    _admin(web)
    _on()
    a, _ = _item()
    _stock(a, 3)
    page = web.get(f"/phone/stock/?id={a}").content.decode()
    assert "data-share-text=" in page and "in stock" in page and "/static/core/share.js" in page
    i = page.index("data-share-text=")
    assert "₱" not in page[i:i + 200]
