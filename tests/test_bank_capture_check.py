"""Capture check (owner, 2026-09-28: "Always remember bank statement is ultimate truth and have a pass test to ensure system captures the bank
statement well"). A statement goes in only when the app can prove it read every line exactly; any failure blocks the import and nothing is
saved. Made-up statements in the real MBOS download layout -- real statements never go into the project."""
import sqlite3

import pytest

import engines.bank_statement_service as bss
from engines.bank_statement_service import capture_check, import_statement, parse_statement

TITLE = ["STATEMENT OF ACCOUNT,,", "SLICK'S TIRE SUPPLY,,", "Account Number:,{acct},", "Currency:,PHP,", "Period Covered:,Last 12 Months,"]
HEADER = "Transaction Date,Posting Date,Time,Check Number,Transaction Description,Debit Amount,Credit Amount,Balance,Branch/Channel,Subscriber Number/TIN Number,Sequence Number,"
ROWS = [  # (date, check, description, debit, credit) -- balances worked out below from an opening of 100,000.00
    ("09/21/2026", "0000000000", "MO CSH DEP", 0.0, 25000.0),
    ("09/21/2026", "3992554251", "Inward Clearing", 9440.0, 0.0),
    ("09/22/2026", "0000000000", "MO DM PAY UTLTY", 13500.0, 0.0),
    ("09/23/2026", "0000000000", "ET CR IBFT", 0.0, 50000.0),
    ("09/24/2026", "3992554432", "Inward Clearing", 16618.2, 0.0),
]


def _file(rows=ROWS, acct="3997399014901", opening=100000.0, drop=None, override=None):
    bal, out = opening, []
    for i, (d, chk, desc, debit, credit) in enumerate(rows):
        bal = round(bal - debit + credit, 2)
        line = f'{d},{d},10:00:0{i},{chk},{desc},"{debit:,.2f}","{credit:,.2f}","{bal:,.2f}",PULILAN-RIS,,00{i},'
        if i != drop:
            out.append((override or {}).get(i, line))
    text = "\n".join(t.format(acct=acct) for t in TITLE) + "\n" + HEADER + "\n" + "\n".join(out) + "\nConfidential Information,,,,,,,,,,,Metrobank\n"
    return text.encode("utf-8")


def _failed(data, known=None):
    return [c["name"] for c in capture_check(parse_statement(data, "s.csv"), known) if not c["ok"]]


def _stored(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0], conn.execute("SELECT COUNT(*) FROM bank_statement_imports").fetchone()[0]


def test_a_good_statement_passes_every_check_and_the_pass_is_audited(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    assert _failed(_file()) == []
    res = import_statement(_file(), "s.csv")
    assert not res["blocked"] and res["new_lines"] == 5 and all(c["ok"] for c in res["capture"])
    assert res["capture"][-1]["name"] == "Saved lines match the file"
    with sqlite3.connect(db) as conn:
        out_, in_ = conn.execute("SELECT SUM(debit), SUM(credit) FROM bank_transactions").fetchone()
        assert (round(out_, 2), in_) == (39558.2, 75000.0)
        assert conn.execute("SELECT new_values FROM audit_logs WHERE user_action = 'BANK_STATEMENT_CAPTURE_CHECK'").fetchone()[0].startswith("PASS")


def test_a_missing_line_breaks_the_running_balance_and_is_blocked(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    data = _file(drop=2)
    assert "Running balance adds up on every line" in _failed(data)
    res = import_statement(data, "s.csv")
    assert res["blocked"] and "capture check failed" in res["messages"][0] and _stored(db) == (0, 0)


def test_a_line_the_app_cannot_read_is_blocked():
    bad = '09/22/2026,09/22/2026,10:00:02,0000000000,MO DM PAY UTLTY,"13,5OO.00","0.00","115,560.00",,,002,'   # letter O, not zero
    assert "Every transaction line read" in _failed(_file(override={2: bad}))


def test_a_wrong_amount_is_caught_by_the_balances():
    wrong = '09/22/2026,09/22/2026,10:00:02,0000000000,MO DM PAY UTLTY,"13,000.00","0.00","102,060.00",,,002,'
    assert "Running balance adds up on every line" in _failed(_file(override={2: wrong}))


def test_a_statement_for_another_account_is_blocked():
    assert _failed(_file(acct="3990000001111"), known="4901") == ["Same bank account"]
    assert _failed(_file(), known="4901") == []


def test_two_identical_lines_are_blocked_so_none_is_lost():
    same = ROWS[:2] + [ROWS[1]]
    data = _file(rows=same).replace(b"10:00:02", b"10:00:01").replace(b",002,", b",001,")
    p = parse_statement(data, "s.csv")
    # running balances differ on the copy, so it is not a true duplicate -- make it one by hand
    p.lines[2] = p.lines[1]
    assert "No line would be lost as a duplicate" in [c["name"] for c in capture_check(p) if not c["ok"]]


def test_an_empty_or_unreadable_file_never_passes():
    assert _failed(b"") and _failed(b"hello,world\n1,2\n")


def test_if_the_saved_lines_do_not_match_nothing_is_kept(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    monkeypatch.setattr(bss, "_stored_check", lambda conn, acct, parsed: {"name": "Saved lines match the file", "ok": False, "detail": "4 of 5"})
    with pytest.raises(bss.CaptureCheckFailed):
        import_statement(_file(), "s.csv")
    assert _stored(db) == (0, 0)
    assert not list((tmp_path / "bank").glob("*")) if (tmp_path / "bank").exists() else True
