"""Parked checks (owner, 2026-10-18: "before I only use Chrysanth if I will print all the parked checks but now I have to go per batch"): every parked
check of every payment statement on one page, written with Chrysanth in one run file."""
from conftest import make_creditor
from tests.test_web_batch_checks import _checks, _park
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch


def _statement(web, db, name, gross, day):
    cid = make_creditor(db, name, terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc=f"SI-{name[:3]}", gross=gross)
    _park(web, sid, (day, gross))
    return sid


def test_every_parked_check_on_one_page_and_one_run_file(web, db):
    _admin(web)
    a = _statement(web, db, "CSS Autoparts Inc.", "6,000", "2026-11-20")
    b = _statement(web, db, "Battery Depot", "4,000", "2026-12-20")
    assert "Parked checks (2)" in web.get("/order-statements/").content.decode()
    assert web.get("/order-statements/?view=parked")["Location"] == "/order-statements/parked/"
    page = web.get("/order-statements/parked/").content.decode()
    assert "CSS Autoparts Inc." in page and "Battery Depot" in page and "2 parked" in page
    assert "Battery Depot" not in web.get("/order-statements/parked/?upto=2026-11-30").content.decode()    # up to a check date
    ids = [_checks(a)[0][0], _checks(b)[0][0]]
    r = web.post("/order-statements/parked/", {f"tick_{i}": "1" for i in ids})
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    body = r.content.decode()
    assert body.count("\n") >= 2 and "6000" in body and "4000" in body                                     # both checks, one file
    assert "No parked checks" in web.get("/order-statements/parked/").content.decode()
    assert "No parked checks" in web.get("/order-statements/").content.decode()
