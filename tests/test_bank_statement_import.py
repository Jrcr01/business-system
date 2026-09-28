"""Bank statement import (2026-09-27): reading the Metrobank MBOS layout, classifying lines, storing them once, matching our checks.
Uses a made-up statement in the MBOS column layout -- real statements never go into the project."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.bank_statement_service import (
    _map_columns, _unreverse, clean_amount, exceptions, import_statement, not_yet_cleared, norm_check_number, parse_statement,
    preview_matches, statement_coverage,
)
from engines.bank_statement_service import adjust_check_to_bank, adjustments, bank_correction, reopen

HEADER = "Transaction Date,Posting Date,Time,Check Number,Transaction Description,Debit Amount,Credit Amount,Balance,Branch/Channel,Subscriber Number/TIN,Sequence Number"


def _csv(lines):
    return (HEADER + "\n" + "\n".join(lines) + "\n").encode("utf-8")


def _row(d, check, desc, debit, credit, bal, seq=""):
    return f'{d},{d},23:59:59,{check},{desc},"{debit}","{credit}","{bal}",,,{seq}'


def test_small_readers_handle_the_mbos_quirks():
    assert clean_amount("1000000.0 0") == 1000000.0 and clean_amount("1,234.56") == 1234.56 and clean_amount("") == 0.0
    assert norm_check_number("3992554301") == norm_check_number("2554301") == "2554301" and norm_check_number("0000000000") == ""
    sideways = ["noitcasnarT\netaD", "gnitsoP\netaD", "emiT", "rebmuN\nkcehC", "noitcasnarT noitpircseD", "tnuomA\ntibeD",
                "tnuomA\ntiderC", "ecnalaB", "ahC/hcnarB\nlenn", "NIT/rebmuN\nrebircsbuS\nrebmuN", "ecneuqeS\nrebmuN"]
    cm = _map_columns([_unreverse(h) for h in sideways])                  # headers exactly as pdfplumber reads the real MBOS PDF
    assert cm["txn_date"] == 0 and cm["check_number"] == 3 and cm["debit"] == 5 and cm["credit"] == 6 and cm["balance"] == 7


def test_a_statement_is_read_classified_and_its_balances_checked():
    data = _csv([
        _row("01/02/2026", "0000000000", "MO CSH DEP", "0.00", "500,000.00", "843,188.70", "1"),
        _row("01/02/2026", "3992514799", "IM IN- CLRNG- BATCH", "54,577.32", "0.00", "788,611.38"),
        _row("01/03/2026", "0000000000", "DE DM RET CHK", "9,500.00", "0.00", "779,111.38", "2"),
        _row("01/03/2026", "0000000000", "MO DM PAY UTLTY", "100,000.00", "0.00", "679,111.38", "3"),
        _row("01/04/2026", "0000000000", "MO DM", "10,000.00", "0.00", "669,111.38", "4"),
    ])
    p = parse_statement(data, "statement.csv")
    assert [x.kind for x in p.lines] == ["DEPOSIT", "CHECK", "RETURNED_CHECK", "BILLS_PAYMENT", "DEBIT_MEMO"]
    assert p.opening_balance == 343188.70 and p.closing_balance == 669111.38 and p.balance_ok and p.messages == []
    assert p.lines[1].check_number == "3992514799" and p.lines[0].check_number is None
    broken = parse_statement(data.replace(b'"669,111.38"', b'"669,000.00"'), "statement.csv")
    assert not broken.balance_ok and broken.bad_balance_steps == 1 and "running balance" in broken.messages[0]


def test_real_mbos_csv_and_excel_exports_with_title_lines_above_the_headings(tmp_path):
    """MBOS CSV / Excel downloads (2026-09-28) start with title lines; the Excel one also has empty spacer columns."""
    title = ["STATEMENT OF ACCOUNT,,", "SLICK'S TIRE SUPPLY,,", "Account Number:,3990000001234,", "Period Covered:,Last 12 Months,"]
    rows = [_row("01/02/2026", "0000000000", "MO CSH DEP", "0.00", "500,000.00", "843,188.70", "1"),
            _row("01/02/2026", "3992514799", "Inward Clearing", "54,577.32", "0.00", "788,611.38", "2")]
    csv = ("\n".join(title) + "\n").encode() + _csv(rows) + b"Confidential Information,,\n"
    p = parse_statement(csv, "statement.csv")
    assert len(p.lines) == 2 and p.balance_ok and p.messages == [] and p.account_number == "3990000001234"
    assert [x.kind for x in p.lines] == ["DEPOSIT", "CHECK"] and p.closing_balance == 788611.38

    import pandas as pd
    spaced = [["", "Account Number:", "", "3990000001234"],
              ["", "Transaction Date", "", "Check Number", "Debit Amount", "", "Credit Amount", "Balance"],
              ["", "01/02/2026", "", "0000000000", "0.00", "", "500,000.00", "843,188.70"],
              ["", "01/02/2026", "", "3992514799", "54,577.32", "", "0.00", "788,611.38"]]
    path = tmp_path / "s.xlsx"
    pd.DataFrame(spaced).to_excel(path, header=False, index=False)
    x = parse_statement(path.read_bytes(), "s.xlsx")
    assert len(x.lines) == 2 and x.balance_ok and x.lines[1].check_number == "3992514799" and x.account_number == "3990000001234"


def _checks(db):
    """Our checks: A (10-digit number), B (stored as 7 digits), C (amount will differ), D (cancelled, but the bank clears it), E (not cleared)."""
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 5000.0, [], sid)
    ids = {}
    for key, num, amt, d in (("A", "3992000001", 1000.0, "2026-10-01"), ("B", "2000002", 1100.0, "2026-10-02"), ("C", "3992000003", 1200.0, "2026-10-02"),
                             ("D", "3992000004", 900.0, "2026-10-03"), ("E", "3992000005", 800.0, "2026-10-03")):
        _, _, cid = app.issue_check(creditor_id, amt, date.fromisoformat(d), statement_id=sid)
        ids[key] = cid
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (num, cid))
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET status = 'Cancelled' WHERE check_id = ?", (ids["D"],))
    return ids


def _statement(extra=()):
    return _csv([
        _row("10/05/2026", "3992000001", "IM IN- CLRNG- BATCH", "1,000.00", "0.00", "99,000.00"),     # A: matched
        _row("10/05/2026", "3992000002", "IM IN- CLRNG- BATCH", "1,100.00", "0.00", "97,900.00"),     # B: 10-digit on the bank, 7-digit ours
        _row("10/06/2026", "3992000003", "MO CHK IN-CLRNG", "1,250.00", "0.00", "96,650.00"),         # C: amount differs
        _row("10/06/2026", "3992000004", "IM IN- CLRNG- BATCH", "900.00", "0.00", "95,750.00"),       # D: we cancelled it!
        _row("10/07/2026", "3991234567", "IM IN- CLRNG- BATCH", "500.00", "0.00", "95,250.00"),       # not in the system
        _row("10/07/2026", "0000000000", "DE DM RET CHK", "300.00", "0.00", "94,950.00", "9"),        # returned deposited check
        _row("10/07/2026", "0000000000", "ET CR IBFT", "0.00", "5,000.00", "99,950.00", "10"),        # deposit
        *extra,
    ])


def test_import_matches_our_checks_and_lists_only_the_exceptions(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))                     # the kept copy goes to tmp_path/bank
    ids = _checks(db)
    data = _statement()
    assert preview_matches(parse_statement(data, "s.csv")) == {"matched": 2, "amount_mismatch": 1, "cancelled_check_cleared": 1, "not_in_system": 1}
    res = import_statement(data, "s.csv")
    assert res["new_lines"] == 7 and res["matched"] == 2 and res["exceptions"] == 3 and res["balance_ok"]
    assert (tmp_path / "bank").exists() and len(list((tmp_path / "bank").iterdir())) == 1
    with sqlite3.connect(db) as conn:
        cleared = dict(conn.execute("SELECT check_id, bank_cleared_date FROM checks_issued WHERE bank_cleared_date IS NOT NULL").fetchall())
    assert cleared == {ids["A"]: "2026-10-05", ids["B"]: "2026-10-05", ids["D"]: "2026-10-06"}
    statuses = sorted(e["match_status"] for e in exceptions())
    assert statuses == ["amount_mismatch", "cancelled_check_cleared", "not_in_system", "returned_check"]
    waiting = {w["check_id"] for w in not_yet_cleared()}
    assert waiting == {ids["C"], ids["E"]}                                             # C's amount didn't match; E never cleared
    assert statement_coverage() == (date(2026, 10, 5), date(2026, 10, 7))


def test_the_same_file_or_an_overlapping_period_never_doubles_up(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _checks(db)
    first = import_statement(_statement(), "s.csv")
    again = import_statement(_statement(), "s-copy.csv")
    assert first["new_lines"] == 7 and again["already_imported"] and again["new_lines"] == 0
    overlap = import_statement(_statement(extra=[_row("10/08/2026", "0000000000", "MO CSH DEP", "0.00", "1,000.00", "100,950.00", "11")]), "s2.csv")
    assert overlap["new_lines"] == 1 and overlap["duplicate_lines"] == 7
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 8


def test_nothing_else_in_the_app_changes_meaning(db, monkeypatch, tmp_path):
    """Show-both rule: importing does not change check status or the payables totals (they still use the check date)."""
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    _checks(db)
    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT check_id, status, check_date, amount FROM checks_issued ORDER BY check_id").fetchall()
    import_statement(_statement(), "s.csv")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT check_id, status, check_date, amount FROM checks_issued ORDER BY check_id").fetchall() == before


def test_adjust_to_the_bank_never_edits_our_check(db, monkeypatch, tmp_path):
    """Owner, 2026-09-28: "Bank statement is the ultimate truth" + "not direct edit the record but tag it as adjustment due to incorrect
    recording in the book" + adjust the batch off too. Our check C is PHP 1,200 dated Oct 10; the bank paid that number PHP 1,250 on Oct 6."""
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    ids = _checks(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP, check_date = '2026-10-10' WHERE check_id = ?", (ids["C"],))
    import_statement(_statement(), "s.csv")
    txn = next(e["txn_id"] for e in exceptions() if e["match_status"] == "amount_mismatch")
    fix = bank_correction(txn)
    assert fix["ok"] and fix["diff"] == 50.0 and fix["date_differs"] and "stub" in fix["warn"]
    from engines.statement_rules import get_batch_balance
    with sqlite3.connect(db) as conn:
        sid = conn.execute("SELECT statement_id FROM checks_issued WHERE check_id = ?", (ids["C"],)).fetchone()[0]
    before = get_batch_balance(sid)
    ok, msg = adjust_check_to_bank(txn)
    assert ok, msg
    with sqlite3.connect(db) as conn:
        amount, cdate, cleared = conn.execute("SELECT amount, check_date, bank_cleared_date FROM checks_issued WHERE check_id = ?", (ids["C"],)).fetchone()
        assert (amount, cdate, cleared) == (1200.0, "2026-10-10", "2026-10-06")                 # the book is untouched; cleared by the bank
        assert conn.execute("SELECT COUNT(*) FROM check_corrections").fetchone()[0] == 0
        kinds = dict(conn.execute("SELECT kind, amount FROM bank_adjustments WHERE reversed_at IS NULL").fetchall())
        assert kinds == {"CHECK_AMOUNT": 50.0, "BATCH_BALANCE": 50.0, "CHECK_DATE": 0.0}
        assert conn.execute("SELECT resolution FROM bank_transactions WHERE txn_id = ?", (txn,)).fetchone()[0] == "Adjusted to the bank"
    after = get_batch_balance(sid)
    assert after[0] == before[0] + 50 and after[1] == before[1] + 50 and abs(after[2] - before[2]) < 0.005   # owes exactly what the bank paid
    assert [a["kind"] for a in adjustments()] and bank_correction(txn) is None
    reopen(txn)                                                                                 # undo: adjustments reversed, kept
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bank_adjustments WHERE reversed_at IS NULL").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM bank_adjustments").fetchone()[0] == 3
        assert conn.execute("SELECT bank_cleared_date FROM checks_issued WHERE check_id = ?", (ids["C"],)).fetchone()[0] is None
    assert get_batch_balance(sid) == before and adjustments() == []


def test_double_entry_guard_a_check_cleared_twice_never_overwrites_the_first(db, monkeypatch, tmp_path):
    """Owner, 2026-09-28: "put a guard rail for double entry". The same file, the same lines from an overlapping statement and a second
    clearing of the same check are each counted once; the second clearing waits in Need a look."""
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    ids = _checks(db)
    first = import_statement(_statement(), "s.csv")
    assert import_statement(_statement(), "copy.csv")["already_imported"]
    again = _row("10/08/2026", "3992000001", "IM IN- CLRNG- BATCH", "1,000.00", "0.00", "98,950.00", "11")    # A clears a second time
    res = import_statement(_statement(extra=[again]), "s2.csv")
    assert res["new_lines"] == 1 and res["duplicate_lines"] == 7 and not res["blocked"]
    with sqlite3.connect(db) as conn:
        cleared, txn = conn.execute("SELECT bank_cleared_date, bank_txn_id FROM checks_issued WHERE check_id = ?", (ids["A"],)).fetchone()
        assert cleared == "2026-10-05"                                              # the first clearing stands
        assert conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0] == 8
    twice = [e for e in exceptions() if e["match_status"] == "cleared_twice"]
    assert len(twice) == 1 and twice[0]["posting_date"] == "2026-10-08" and twice[0]["txn_id"] != txn and first["matched"] == 2
