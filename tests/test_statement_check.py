"""Supplier statement check (owner, 2026-10-13: "our suppliers give different formats", "mostly photos and paper", "the common field are document
reference usually delivery receipt, date, amount"): tick their paper against our records; type only what we do not have; nothing is changed."""
import sqlite3

from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_d3a import _add_inv, _db, _inv, _new_batch


def _paid(db, doc):
    """That invoice as paid in our records (its batch's check cleared)."""
    conn = sqlite3.connect(db)
    conn.execute("UPDATE invoices SET status = 'Paid' WHERE doc_ref = ?", (doc,))
    conn.commit()
    conn.close()


def test_tick_their_paper_and_see_the_five_groups(web, db):
    _admin(web)
    cid = make_creditor(db, "New Century City Marketing")
    sid = _new_batch(web, cid)
    for doc, gross in (("216420", "90,168.77"), ("217074", "122,690.81"), ("217332", "341,240.66"), ("218147", "344,392.19")):
        _add_inv(web, sid, doc=doc, gross=gross)
    _paid(db, "218147")
    a, b, c = (_inv(d)["invoice_id"] if isinstance(_inv(d), dict) else _inv(d)[0] for d in ("216420", "217074", "217332"))
    assert 'href="/order-statements/check/"' in web.get("/order-statements/").content.decode()
    page = web.get(f"/order-statements/check/?s={cid}").content.decode()
    assert "216420" in page and "217332" in page and "218147" not in page                 # the paid one is not owed, so not listed
    before = fetchone(_db(), "SELECT COUNT(*), SUM(amount) FROM invoices")
    form = {"s": cid, "statement_date": "2026-12-02", "their_total": "876,223.62", "action": "compare",
            f"t_{a}": "1", f"a_{a}": "",                                                  # on theirs, the same amount
            f"t_{b}": "1", f"a_{b}": "126,290.81",                                        # on theirs, 3,600 more
            "x_date": ["2026-11-15", "2026-10-29", ""], "x_ref": ["219386", "218147", ""], "x_amount": ["315,371.85", "344,392.19", ""]}
    page = web.post("/order-statements/check/", form).content.decode()
    for text in ("Matches", "Same number, different amount", "theirs is 3,600.00 more", "On theirs, not recorded by you", "219386",
                 "On theirs, already paid by you", "In your records, not on theirs", "217332", "This page only compares"):
        assert text in page, text
    from engines import statement_check as sc
    r = sc.compare(cid, {a: None, b: 126290.81}, [("2026-11-15", "219386", "315,371.85"), ("2026-10-29", "218147", "344,392.19")], "876,223.62")
    got = {s["key"]: (s["lines"], s["amount"]) for s in r["summary"]}
    assert got == {"match": (1, 90168.77), "amount": (1, 3600.0), "missing": (1, 315371.85), "paid": (1, 344392.19), "ours": (1, 341240.66)}
    assert r["found"] == 876223.62 and r["left"] == 0.0 and r["ours_total"] == round(90168.77 + 122690.81 + 341240.66, 2)
    assert fetchone(_db(), "SELECT COUNT(*), SUM(amount) FROM invoices") == before         # nothing in the payables changed


def test_a_typed_number_that_is_on_our_list_counts_as_ticked_and_the_dr_number_matches_too(web, db):
    from engines import statement_check as sc
    _admin(web)
    cid = make_creditor(db, "Express Dymans")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-900", gross="24,144")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE invoices SET dr_no = '26828' WHERE doc_ref = 'SI-900'")
    conn.commit()
    conn.close()
    lines = sc.our_lines(cid)
    assert lines[0]["ref"] == "26828 · SI SI-900" and lines[0]["refs"] == ["26828", "SI-900"]
    r = sc.compare(cid, {}, [("", " 26828 ", "24,144.00"), ("", "", "5"), ("", "X1", "")], None)      # their paper lists the DR no.; half-typed lines are left out
    got = {s["key"]: s["lines"] for s in r["summary"]}
    assert got == {"match": 1, "amount": 0, "missing": 0, "paid": 0, "ours": 0} and r["left"] is None


def test_a_check_is_saved_and_opened_again_and_roles(web, db):
    _admin(web)
    cid = make_creditor(db, "RPG Battery Ventures")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="2033160", gross="12,600")
    inv = _inv("2033160")
    iid = inv["invoice_id"] if isinstance(inv, dict) else inv[0]
    r = web.post("/order-statements/check/", {"s": cid, "action": "save", "statement_date": "2026-09-30", "their_total": "12,600", "x_date": [""], "x_ref": [""], "x_amount": [""]})
    assert r.status_code == 200 and b"Tick or type at least one line" in r.content        # nothing ticked: nothing to save
    r = web.post("/order-statements/check/", {"s": cid, "action": "save", "statement_date": "2026-09-30", "their_total": "12,600", f"t_{iid}": "1", f"a_{iid}": ""})
    kid = fetchone(_db(), "SELECT check_id FROM supplier_statement_checks")[0]
    assert r["Location"] == f"/order-statements/check/{kid}/"
    page = web.get(f"/order-statements/check/{kid}/").content.decode()
    assert "RPG Battery Ventures" in page and "saved" in page and "12,600.00" in page
    assert "Checks saved before" in web.get(f"/order-statements/check/?s={cid}").content.decode()
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'SUPPLIER_STATEMENT_CHECKED'")[0] == 1
    assert web.get("/order-statements/check/999/").status_code == 404
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")                                # whoever cannot open Order statements cannot open this
    assert buyer.get("/order-statements/check/").status_code == 403
