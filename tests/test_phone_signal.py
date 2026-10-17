"""Phone pages when the signal drops (owner, 2026-10-17: "what problem occurs if there is loss of connectivity"; "Agree with this", "Yes all 3";
mock-up docs/mockups/ah_phone_signal_loss.html): a send pressed again after its answer was lost is not saved twice; the paper count sheet has row
numbers and a Rack / area column and prints from the phone too."""
from conftest import fetchone
from tests.test_phone import _db, _n, _on
from tests.test_web_d0 import _admin
from tests.test_web_stock import _item, _stock


def test_a_stock_out_pressed_again_after_a_lost_answer_is_not_recorded_twice(web, db):
    _admin(web)
    _on()
    a, _ = _item()
    _stock(a, 12)
    slip = {"action": "slip", "customer": "", "si_hint": "", "ln_item_0": a, "ln_qty_0": "3", "ln_price_0": "", "once": "a1b2c3d4e5f6a7b8c9d0e1f2"}
    r = web.post("/phone/out/", slip)
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9
    r = web.post("/phone/out/", slip, follow=True)                                # the signal dropped: the phone never saw the answer and pressed again
    assert "Already saved at" in r.content.decode() and "Nothing was added twice" in r.content.decode()
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9 and _n("SELECT COUNT(*) FROM sales") == 1
    r = web.post("/phone/out/", {**slip, "once": "ffffeeeeddddccccbbbbaaaa"})   # a new slip (a new code) is a new slip
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 6


def test_a_refused_send_frees_its_code_and_no_code_works_as_before(web, db):
    _admin(web)
    _on()
    a, _ = _item()
    _stock(a, 2)
    code = "0123456789abcdef01234567"
    r = web.post("/phone/out/", {"action": "slip", "customer": "", "si_hint": "", "once": code})       # no lines: refused
    assert r.status_code == 200 and fetchone(_db(), "SELECT COUNT(*) FROM form_once WHERE token = ?", (code,)) == (0,)
    r = web.post("/phone/out/", {"action": "slip", "customer": "", "si_hint": "", "ln_item_0": a, "ln_qty_0": "1", "ln_price_0": "", "once": code})
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 1             # pressed again after fixing: saved
    r = web.post("/phone/out/", {"action": "slip", "customer": "", "si_hint": "", "ln_item_0": a, "ln_qty_0": "1", "ln_price_0": ""})
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 0             # an older page without a code: as before


def test_the_paper_count_sheet_numbers_its_rows_has_a_rack_column_and_prints_from_the_phone(web, db):
    _admin(web)
    _on()
    a, _ = _item()
    b, _ = _item(size="195R15", pattern="RY55")
    _stock(a, 4)
    _stock(b, 2)
    from engines.count_rules import start_count
    ok, _, cid = start_count("Tires", "Westlake")
    assert ok
    sheet = web.get(f"/stock-counts/{cid}/?print=1").content.decode()
    assert "<th>Rack / area</th>" in sheet and "<td>1</td>" in sheet and "<td>2</td>" in sheet
    assert web.get(f"/phone/count/{cid}/print/").content.decode() == sheet                                  # the same sheet from the phone
    phone = web.get(f"/phone/count/{cid}/").content.decode()
    assert f'href="/phone/count/{cid}/print/"' in phone and "#1</span>" in phone and "rack / area, note" in phone
    laptop = web.get(f"/stock-counts/{cid}/").content.decode()
    assert '<td class="hint">1</td>' in laptop and "rack / area, note" in laptop
    page = web.get("/phone/").content.decode()
    assert 'id="ph_net"' in page and 'data-me="' in page                                                   # the signal mark; the kept copy is per person


def test_the_app_icon_has_each_persons_own_shortcuts(web, db):
    """Owner, 2026-10-17 ("1" of ak_phone_features): long-press the icon -> Stock in, Stock out, Stock count, Record a sale; the staff phone never
    gets Record a sale."""
    import json
    from tests.test_web_d0 import _sign_in_new
    _admin(web)
    _on()
    names = [s["name"] for s in json.loads(web.get("/phone/manifest-ops.webmanifest").content)["shortcuts"]]
    assert names == ["Stock in", "Stock out", "Stock count", "Record a sale"]
    erp = [s["name"] for s in json.loads(web.get("/phone/manifest.webmanifest").content)["shortcuts"]]
    assert erp == ["Slick's POS", "Slicks Inventory"]                         # owner, 2026-10-17: POS, then ERP pages
    pics = json.loads(web.get("/phone/manifest.webmanifest").content)["shortcuts"]
    assert all(s["icons"][0]["src"].startswith("data:image/png;base64,") for s in pics)     # pictures inside: Cloudflare cannot stop them
    rack = _sign_in_new("rack@slicks.test", "scanner")
    staff = [s["name"] for s in json.loads(rack.get("/phone/manifest-ops.webmanifest").content)["shortcuts"]]
    assert "Record a sale" not in staff and "Stock count" in staff
    staff_erp = [x["name"] for x in json.loads(rack.get("/phone/manifest.webmanifest").content)["shortcuts"]]
    assert staff_erp[0] == "Slick's POS" and "Daily payables" not in staff_erp and "Stock in" in staff_erp     # the staff phone: the jobs


def test_the_icon_shortcuts_are_complete_when_android_asks_without_a_sign_in(web, db):
    """Owner, 2026-10-17: "Why no daily payable and record invoices" -- Android refreshed the app without the sign-in and got only POS."""
    import json
    from django.test import Client
    _admin(web)                                                  # the system is set up (else everything goes to /setup/)
    _on()
    anon = Client()
    erp = [s["name"] for s in json.loads(anon.get("/phone/manifest.webmanifest").content)["shortcuts"]]
    assert erp == ["Slick's POS", "Slicks Inventory"]
    pos = [s["name"] for s in json.loads(anon.get("/phone/manifest-ops.webmanifest").content)["shortcuts"]]
    assert pos == ["Stock in", "Stock out", "Stock count", "Record a sale"]
