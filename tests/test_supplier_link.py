"""The supplier's QR link (owner, 2026-10-14): a code on the strip and the supplier's copy opens that one payment statement's supplier copy with no
sign-in; it opens nothing else, is recorded, expires, and can be switched off."""
import io
import sqlite3
from datetime import timedelta

from conftest import make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _numbered(web, db, days_ahead=5):
    from engines.clock import manila_today
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-1", gross="10,000")
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": (manila_today() + timedelta(days=days_ahead)).isoformat(), "a1": "10,000"})
    with sqlite3.connect(_db()) as conn:
        ref = conn.execute("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (sid,)).fetchone()[0]
        if ref.startswith("DRAFT-"):
            conn.execute("UPDATE order_statements SET statement_ref = '2026 - PS 0001' WHERE statement_id = ?", (sid,))
    return sid


def _token(sid):
    with sqlite3.connect(_db()) as conn:
        row = conn.execute("SELECT token FROM statement_links WHERE statement_id = ?", (sid,)).fetchone()
    return row[0] if row else None


def _text(pdf):
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_printing_makes_the_link_and_a_supplier_opens_it_without_signing_in(web, db):
    from django.test import Client
    _admin(web)
    sid = _numbered(web, db)
    assert _token(sid) is None
    strip = web.get(f"/order-statements/{sid}/thermal.pdf")
    assert strip.status_code == 200 and "Scan to see the full statement" in _text(strip.content)
    token = _token(sid)
    assert token and len(token) == 32
    assert "Scan to see" not in _text(web.get(f"/order-statements/{sid}/a4.pdf?copies=supplier").content)     # owner, 2026-10-18: no QR on short bond
    assert "Scan to see" not in _text(web.get(f"/order-statements/{sid}/a4.pdf?copies=filing").content)      # the copy we keep carries none
    assert _token(sid) == token                                                                                 # one code per statement, kept
    supplier = Client()                                                                                         # nobody signed in
    r = supplier.get(f"/s/{token}/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r["Cache-Control"] == "no-store"
    text = _text(r.content)
    assert "SUPPLIER COPY" in text and "INV-1" in text and "INTERNAL FILING COPY" not in text and "Prepared by" not in text
    assert supplier.get("/")["Location"].startswith("/login/") and supplier.get(f"/order-statements/{sid}/")["Location"].startswith("/login/")
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "The supplier's QR link" in page and "Opened 1 time" in page and "Switch off this link" in page


def test_a_wrong_code_a_switched_off_link_and_an_expired_one_open_nothing(web, db):
    from django.test import Client
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    _admin(web)
    sid = _numbered(web, db)
    web.get(f"/order-statements/{sid}/thermal.pdf")
    token, supplier = _token(sid), Client()
    assert supplier.get("/s/" + "A" * 32 + "/").status_code == 404 and supplier.get("/s/short/").status_code == 404
    assert web.post(f"/order-statements/{sid}/", {"action": "link_off"}).status_code == 302
    r = supplier.get(f"/s/{token}/")
    assert r.status_code == 410 and b"no longer in use" in r.content
    assert "Scan to see" not in _text(web.get(f"/order-statements/{sid}/thermal.pdf").content)                 # a switched-off link is not printed again
    web.post(f"/order-statements/{sid}/", {"action": "link_on"})
    assert supplier.get(f"/s/{token}/").status_code == 200
    with sqlite3.connect(_db()) as conn:                                                                        # the last check was long ago
        conn.execute("UPDATE checks_issued SET check_date = '2026-01-05' WHERE statement_id = ?", (sid,))
    r = supplier.get(f"/s/{token}/")
    assert r.status_code == 410 and b"expired" in r.content
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET check_date = date('now') WHERE statement_id = ?", (sid,))
    assert supplier.get(f"/s/{token}/").status_code == 200
    with acting_as("admin@slicks.test", "admin"):
        sr.save("supplier_qr", "0")                                                                             # the whole feature off
    assert supplier.get(f"/s/{token}/").status_code == 404
    assert "Scan to see" not in _text(web.get(f"/order-statements/{sid}/thermal.pdf").content)


def test_a_draft_gets_no_code_and_many_wrong_tries_are_refused(web, db):
    from django.test import Client
    from engines import statement_links as sl
    _admin(web)
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-1", gross="10,000")
    assert "Scan to see" not in _text(web.get(f"/order-statements/{sid}/thermal.pdf").content) and _token(sid) is None
    sl._MISSES.clear()
    c = Client()
    for _ in range(sl.MISS_LIMIT):
        assert c.get("/s/" + "B" * 32 + "/").status_code == 404
    assert c.get("/s/" + "B" * 32 + "/").status_code == 429
    sl._MISSES.clear()


def test_print_many_can_make_the_summary_page_alone(web, db):
    _admin(web)
    sid = _numbered(web, db)
    r = web.get(f"/order-statements/print-many/?go=1&scope=all&ids={sid}&type=summary&unprinted=1")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    text = _text(r.content)
    assert "Sunny Tire Supply" in text and "Summary only" in text and "INTERNAL FILING COPY" not in text and "SUPPLIER COPY" not in text
