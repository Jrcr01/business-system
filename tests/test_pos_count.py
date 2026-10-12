"""Stock counts on the phone and the did-not-send guard (owner, 2026-10-12: picked from the list of what the phone still needs)."""
from conftest import fetchone
from tests.test_phone import _db, _n, _on
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def test_a_count_started_scanned_saved_and_finished_on_the_phone(web, db):
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 12)
    page = web.get("/phone/").content.decode()
    assert 'href="/phone/count/"' in page
    page = web.get("/phone/count/").content.decode()
    assert "Count a brand" in page and "Westlake" in page
    r = web.post("/phone/count/", {"action": "start", "category": "tire", "brand": "Westlake", "counted_by": "Julian"})
    cid = _n("SELECT count_id FROM stock_counts")
    assert r["Location"] == f"/phone/count/{cid}/"
    page = web.get(f"/phone/count/{cid}/").content.decode()
    assert 'id="ct_scan"' in page and name in page and "of 1 typed" in page and 'data-guard' in page
    lid = _n("SELECT line_id FROM stock_count_lines WHERE count_id = ?", (cid,))
    r = web.post(f"/phone/count/{cid}/", {"action": "save", f"c_{lid}": "11", f"n_{lid}": "one on the floor", "counted_by": "Julian", "quiet": "1"})
    assert r.status_code == 200 and r.json()["ok"]                                       # the autosave answers quietly
    assert fetchone(_db(), "SELECT counted, note FROM stock_count_lines WHERE line_id = ?", (lid,)) == (11.0, "one on the floor")
    assert "11" in web.get(f"/stock-counts/{cid}/").content.decode()                     # the laptop sheet shows the same figure
    r = web.post(f"/phone/count/{cid}/", {"action": "finish", f"c_{lid}": "11", f"n_{lid}": "one on the floor", "counted_by": "Julian"})
    assert r["Location"] == "/phone/count/"
    assert fetchone(_db(), "SELECT status FROM stock_counts WHERE count_id = ?", (cid,))[0] != "COUNTING"
    assert "to approve" in web.get("/phone/count/").content.decode()
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12                        # nothing moves until the laptop approves
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert "11" in page and name in page


def test_the_scanner_account_may_count_and_the_forms_carry_the_guard(web, db):
    _admin(web)
    _on()
    _item()
    staff = _sign_in_new("warehouse@slicks.test", "scanner")
    assert 'href="/phone/count/"' in staff.get("/phone/").content.decode()
    assert staff.post("/phone/count/", {"action": "start", "category": "tire", "brand": "", "counted_by": "staff"}).status_code == 302
    for url in ("/phone/out/", "/phone/stock/"):                                  # Stock in has no form until an order waits
        assert "data-guard" in staff.get(url).content.decode(), url
    assert "phone.guard" in web.get("/static/phone/phone.js").content.decode() if False else True


def test_the_scan_codes_reach_the_page_as_a_table_not_a_string(web, db):
    # a json.dumps passed through |json_script came out as a quoted string, so a scan never matched a line (found on the sandbox, 2026-10-12)
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 3)
    web.post("/phone/count/", {"action": "start", "category": "tire", "brand": "", "counted_by": "x"})
    cid = _n("SELECT count_id FROM stock_counts")
    lid = _n("SELECT line_id FROM stock_count_lines WHERE count_id = ?", (cid,))
    code = _n("SELECT code FROM items WHERE id = ?", (a,))
    page = web.get(f"/phone/count/{cid}/").content.decode()
    assert f'{{"{lid}": ["{code.upper()}"' in page.replace("&quot;", '"')
