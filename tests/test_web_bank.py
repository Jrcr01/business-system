"""Bank -> Bank and cash in Django (Phase 3, owner 2026-10-08): the statement is checked before anything is saved, then imported (checks clear, their
invoices follow); the lines that need a look are fixed (adjust to the bank, link, tag, suggestion) and can be reopened -- an automatic match too; a tick
the statement doesn't show is undone or kept; the reconciliation and its PDF; booklets and spoiled checks."""
import io
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor, make_statement
from tests.test_bank_recon_v2 import _csv
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _file(data: bytes, name="statement.csv"):
    f = io.BytesIO(data)
    f.name = name
    return f


def _checks(db):
    sup = make_creditor(db, name="Goodyear Philippines")
    sid = make_statement(db, sup)
    app.add_invoice(sup, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 5000.0, [], sid)
    ids = []
    for num, amt, d in (("3992000001", 1000.0, "2026-10-01"), ("3992000002", 1500.0, "2026-10-02"), ("3992000003", 2500.0, "2026-10-03")):
        _, _, cid = app.issue_check(sup, amt, date.fromisoformat(d), statement_id=sid)
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (num, cid))
        ids.append(cid)
    return sid, ids


STATEMENT = [("10/05/2026", "3992000001", "IM IN- CLRNG- BATCH", 1000.0, 0.0),      # clears
             ("10/06/2026", "3992000002", "IM IN- CLRNG- BATCH", 1550.0, 0.0),      # amount differs (ours 1,500)
             ("10/07/2026", "3992000009", "IM IN- CLRNG- BATCH", 777.0, 0.0),       # not in the system
             ("10/07/2026", "0000000000", "MO CSH DEP", 0.0, 20000.0)]


def test_check_the_file_then_import_and_fix_what_needs_a_look(web, db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _admin(web)
    sid, (a, b, c) = _checks(db)
    r = web.post("/bank-cash/", {"action": "check_file", "file": _file(_csv(STATEMENT))})
    assert r.status_code == 302 and "file=" in r["Location"]
    page = web.get(r["Location"]).content.decode()
    assert "before anything is saved" in page and "4 new" in page and "1 check(s) clear" in page and "Capture check: passed" in page
    assert fetchone(_db(), "SELECT COUNT(*) FROM bank_transactions")[0] == 0                        # nothing saved by the check
    token = r["Location"].split("file=")[1]
    r = web.post("/bank-cash/", {"action": "import_file", "file": token}, follow=True)
    assert b"4 new line(s)" in r.content and b"1 check(s) cleared" in r.content
    assert fetchone(_db(), "SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (a,))[0] == "2026-10-05"
    look = web.get("/bank-cash/?tab=look").content.decode()
    assert "amount differs" in look and "not in the system" in look and "Adjust to the bank" in look
    mismatch = fetchone(_db(), "SELECT txn_id FROM bank_transactions WHERE debit = 1550")[0]
    web.post("/bank-cash/", {"action": "adjust", "txn_id": mismatch})
    assert fetchone(_db(), "SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (b,))[0] == "2026-10-06"
    stray = fetchone(_db(), "SELECT txn_id FROM bank_transactions WHERE debit = 777")[0]
    web.post("/bank-cash/", {"action": "tag", "txn_id": stray, "resolution": "Bank error", "note": "asked the branch"})
    assert fetchone(_db(), "SELECT resolution, reviewed FROM bank_transactions WHERE txn_id = ?", (stray,)) == ("Bank error", 1)
    assert "Nothing needs a look" in web.get("/bank-cash/?tab=look").content.decode()
    web.post("/bank-cash/", {"action": "reopen", "txn_id": stray})
    assert fetchone(_db(), "SELECT reviewed FROM bank_transactions WHERE txn_id = ?", (stray,))[0] == 0


def test_reopening_an_automatic_match_undoes_it_for_good(web, db, monkeypatch, tmp_path):
    from engines.bank_statement_service import import_statement
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _admin(web)
    sid, (a, b, c) = _checks(db)
    import_statement(_csv(STATEMENT[:1]), "s.csv")
    assert fetchone(_db(), "SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (a,))[0] == "2026-10-05"
    line = fetchone(_db(), "SELECT txn_id FROM bank_transactions WHERE debit = 1000")[0]
    web.post("/bank-cash/", {"action": "reopen", "txn_id": line})
    assert fetchone(_db(), "SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (a,))[0] is None      # owed again
    assert fetchone(_db(), "SELECT match_status, reviewed FROM bank_transactions WHERE txn_id = ?", (line,)) == ("not_in_system", 0)  # needs a look


def test_a_tick_the_bank_does_not_show_is_undone_or_kept(web, db, monkeypatch, tmp_path):
    from engines import clearing
    from engines.bank_statement_service import import_statement
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _admin(web)
    sid, (a, b, c) = _checks(db)
    assert clearing.mark_cleared(c, date(2026, 10, 6))[0]
    import_statement(_csv(STATEMENT), "s.csv")
    look = web.get("/bank-cash/?tab=look").content.decode()
    assert "Ticked cleared, but not on the bank statement" in look and "3992000003" in look
    flag = fetchone(_db(), "SELECT flag_id FROM review_flags WHERE kind = 'CLEARED_NOT_ON_BANK'")[0]
    web.post("/bank-cash/", {"action": "tick_undo", "flag_id": flag, "check_id": c})
    assert fetchone(_db(), "SELECT manual_cleared_at FROM checks_issued WHERE check_id = ?", (c,))[0] is None
    assert fetchone(_db(), "SELECT reviewed_at IS NOT NULL FROM review_flags WHERE flag_id = ?", (flag,))[0] == 1


def test_reconciliation_pdf_booklets_spoiled_and_the_bank_switch(web, db, monkeypatch, tmp_path):
    from engines.auth_service import set_restrictions
    from engines.bank_statement_service import import_statement
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _admin(web)
    _checks(db)
    import_statement(_csv(STATEMENT), "s.csv")
    page = web.get("/bank-cash/?tab=recon").content.decode()
    assert "Balance per bank" in page and "Available" in page
    imp = fetchone(_db(), "SELECT import_id FROM bank_statement_imports")[0]
    pdf = web.get(f"/bank-cash/recon/{imp}.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    web.post("/bank-cash/", {"action": "new_booklet", "code": "B-4451", "start": "4451", "end": "4500", "received": "2026-10-01"})
    assert "B-4451" in web.get("/bank-cash/?tab=booklets").content.decode()
    web.post("/bank-cash/", {"action": "spoiled", "numbers": "4496, 4497", "reason": "torn"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM spoiled_checks")[0] == 2
    family = _sign_in_new("family@slicks.test", "owner")
    set_restrictions(fetchone_uid("family@slicks.test"), {"bank.use"})
    page = family.get("/bank-cash/")                                          # owners see every page (2026-10-09); the switch now means "may change"
    assert page.status_code == 200 and "View only" in page.content.decode()
    family.post("/bank-cash/", {"action": "spoiled", "numbers": "4498", "reason": "x"})
    assert fetchone(_db(), "SELECT COUNT(*) FROM spoiled_checks")[0] == 2          # refused: nothing saved
