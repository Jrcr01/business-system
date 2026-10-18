"""Stock counts (owner, 2026-10-17): "put also a by size" -- a count of one rim, as the stock notebook is tabbed; "make this editable until closed or
finished" -- while counting, who counts and what it covers can be changed; a figure already typed is never lost; a finished count cannot be changed."""
from conftest import fetchone
from tests.test_web_d0 import _admin
from tests.test_web_stock import _item


def _lines(db, cid):
    return fetchone(db, "SELECT COUNT(*) FROM stock_count_lines WHERE count_id = ?", (cid,))[0]


def test_a_count_by_rim_and_changed_while_counting(web, db):
    from engines import count_rules as cr
    _admin(web)
    a, _ = _item("Westlake", "205/55R16", "SA37")
    b, _ = _item("Westlake", "215/65R16", "SU318")
    c, _ = _item("Westlake", "185/65R15", "RP28")
    ok, msg, cid = cr.start_count("", "", False, "", "Ramon", "16")
    assert ok and _lines(db, cid) == 2 and cr.get_count(cid)["scope"] == "Rim 16" and cr.get_count(cid)["choices"]["rim"] == "16"
    line_a = fetchone(db, "SELECT line_id FROM stock_count_lines WHERE count_id = ? AND item_id = ?", (cid, a))[0]
    assert cr.save_counted(cid, {line_a: (4, "rack 2")})[0]
    ok, msg = cr.change_count(cid, "", "", False, "", "Ramon and Jun", "15")             # now rim 15: the 15 comes on; the typed 16 stays; the untyped 16 goes
    assert ok and "1 item(s) added, 1 removed, 1 already counted kept" in msg
    ids = {r[0] for r in __import__("sqlite3").connect(db).execute("SELECT item_id FROM stock_count_lines WHERE count_id = ?", (cid,))}
    assert ids == {a, c} and cr.get_count(cid)["counted_by"] == "Ramon and Jun"
    assert fetchone(db, "SELECT counted FROM stock_count_lines WHERE line_id = ?", (line_a,))[0] == 4
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert "Change this count" in page and '<option value="15" selected>Rim 15</option>' in page
    r = web.post(f"/stock-counts/{cid}/", {"action": "change", "rim": "16", "counted_by": "Ramon"}, follow=True)
    assert "now covers Rim 16" in r.content.decode()
    assert cr.finish(cid)[0]
    assert "can no longer be changed" in cr.change_count(cid, "", "", False, "", "x", "15")[1]
    assert "Rim 16" in web.get("/stock-counts/").content.decode()
