"""Dates shown mm/dd/yyyy across the system (owner, 2026-10-09)."""
from datetime import date, datetime


def test_dates_are_shown_dd_mm_yyyy(web, db):
    from core.templatetags.slicks import dmy
    assert dmy("2026-10-09") == "10/09/2026" and dmy(date(2026, 1, 5)) == "01/05/2026"
    assert dmy("2026-10-09 14:30:00") == "10/09/2026 14:30" and dmy(datetime(2026, 10, 9, 8, 5)) == "10/09/2026 08:05"
    assert dmy(None) == "" and dmy("") == "" and dmy("not a date") == "not a date"
    from tests.test_web_d0 import _admin
    _admin(web)
    page = web.get("/checks/").content.decode()
    assert "mm/dd/yyyy" in page                      # the date boxes are typed mm/dd/yyyy


def test_the_search_bar_finds_records_and_opens_them_here(web, db):
    # owner, 2026-10-09: "how about search bar?" -- the old screens' search, in the top bar; dates mm/dd/yyyy
    from conftest import make_creditor
    from tests.test_web_d3a import _add_inv, _new_batch
    from tests.test_web_d0 import _admin
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-48812", gross="10,000", date="2026-09-08")
    assert 'action="/search/"' in web.get("/").content.decode()
    page = web.get("/search/?q=48812").content.decode()
    assert "Invoices" in page and f'href="/order-statements/{sid}/"' in page
    page = web.get("/search/?q=Goodyear").content.decode()
    assert f'href="/creditors/{cid}/"' in page and "Creditors" in page
    assert "Nothing found" in web.get("/search/?q=zzzzzz").content.decode()
