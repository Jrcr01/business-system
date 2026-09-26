"""audit_booklet_gaps() (2026-09-17 fix): a check assigned from a booklet is stored as the
branch-code-prefixed, zero-padded Metrobank format (e.g. sequence 1000 on branch 399 ->
"3990001000"), not the raw booklet sequence number. The audit used to compare that against the
booklet's raw start_number/end_number range via a plain CAST-to-integer, which could never match
-- silently making the whole gap/duplicate safety net report "all clear" no matter what had
actually been issued."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone
from engines.check_engine import format_metrobank_check_number
from engines.checkbook_service import audit_booklet_gaps, get_checkbook_alerts

BANK_ACCOUNT_ID = 1  # seeded by schema.sql, branch_code '399'


def _insert_check(db_path, statement_id, creditor_id, sequence_number, amount=1000.0):
    import sqlite3
    check_number = format_metrobank_check_number(sequence_number, "399")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) "
        "VALUES (?, ?, ?, ?, ?, ?, 'Active')",
        (BANK_ACCOUNT_ID, statement_id, creditor_id, check_number, amount, str(date(2026, 1, 1))),
    )
    conn.commit()
    conn.close()
    return check_number


def test_audit_recognizes_booklet_formatted_checks_as_accounted_for(db):
    """Before the fix, every one of these correctly-issued checks was invisible to the audit --
    the whole range would have shown as 'CRITICAL: MISSING GAP' instead."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-1", 1000, 1010, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-1'")[0]
    _insert_check(db, statement_id, creditor_id, 1000)
    _insert_check(db, statement_id, creditor_id, 1001)
    _insert_check(db, statement_id, creditor_id, 1003)

    result = audit_booklet_gaps(booklet_id)

    assert result["missing_gaps_count"] == 1
    assert result["missing_gaps_list"] == [str(1002).zfill(10)]
    assert result["duplicate_numbers_count"] == 0


def test_audit_flags_true_duplicate_check_number(db):
    """The 2026-09-17 UNIQUE index (migration 038) now stops a literal identical check_number
    string from being recorded twice -- so the audit's own duplicate detection is exercised here
    against the case that index can't catch: the same raw leaf recorded under two DIFFERENT text
    representations (a legacy freehand "2000" alongside a booklet-issued "3990002000"), exactly
    the "1 vs 01"-style artifact audit_booklet_gaps()'s own docstring describes."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-2", 2000, 2005, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-2'")[0]
    _insert_check(db, statement_id, creditor_id, 2000)  # "3990002000", the modern booklet format
    import sqlite3
    conn = sqlite3.connect(db)
    for _t in ("trg_checks_issued_no_duplicate_bi", "trg_checks_issued_no_duplicate_bu", "trg_spoiled_checks_no_duplicate_bi"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_t}")      # simulate legacy data written before the database-level rule existed
    conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) "
        "VALUES (?, ?, ?, '2000', ?, ?, 'Active')",
        (BANK_ACCOUNT_ID, statement_id, creditor_id, 1000.0, str(date(2026, 1, 1))),
    )
    conn.commit()
    conn.close()

    result = audit_booklet_gaps(booklet_id)

    assert result["duplicate_numbers_count"] == 1


def test_get_checkbook_alerts_surfaces_the_gap(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-3", 3000, 3005, date(2026, 1, 1))
    _insert_check(db, statement_id, creditor_id, 3000)
    _insert_check(db, statement_id, creditor_id, 3002)  # skips 3001

    alerts = get_checkbook_alerts()

    assert len(alerts) == 1
    assert alerts.iloc[0]["booklet_code"] == "TEST-BOOK-3"
    assert alerts.iloc[0]["missing_gaps_count"] == 1


def test_get_checkbook_alerts_includes_exhausted_booklets(db):
    """2026-09-17 fix: get_checkbook_alerts() only ever looked at Active booklets -- a gap in an
    already fully-assigned (Exhausted) booklet stopped being flagged the moment its last leaf
    was handed out."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-4", 4000, 4003, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-4'")[0]
    _insert_check(db, statement_id, creditor_id, 4000)
    _insert_check(db, statement_id, creditor_id, 4003)  # skips 4001, 4002
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE check_booklets SET status = 'Exhausted' WHERE booklet_id = ?", (booklet_id,))
    conn.commit()
    conn.close()

    alerts = get_checkbook_alerts()

    assert len(alerts) == 1
    assert alerts.iloc[0]["booklet_code"] == "TEST-BOOK-4"


def test_get_checkbook_alerts_excludes_archived_booklets(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-5", 5000, 5003, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-5'")[0]
    _insert_check(db, statement_id, creditor_id, 5000)
    _insert_check(db, statement_id, creditor_id, 5003)  # skips 5001, 5002
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE check_booklets SET status = 'Archived' WHERE booklet_id = ?", (booklet_id,))
    conn.commit()
    conn.close()

    alerts = get_checkbook_alerts()

    assert alerts.empty


def test_create_booklet_rejects_overlapping_range_on_same_bank_account(db):
    """2026-09-17 fix: create_booklet() never checked for an overlapping range at all -- two
    booklets could cover the same leaf numbers, making every number in the overlap ambiguous
    about which physical checkbook it belongs to."""
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-6", 6000, 6050, date(2026, 1, 1))

    ok, msg = app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-7", 6025, 6075, date(2026, 1, 2))

    assert not ok
    assert "overlap" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM check_booklets WHERE booklet_code = 'TEST-BOOK-7'") == (0,)


def test_create_booklet_allows_adjacent_non_overlapping_range(db):
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-8", 7000, 7050, date(2026, 1, 1))

    ok, msg = app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-9", 7051, 7100, date(2026, 1, 2))

    assert ok


def test_create_booklet_allows_reusing_an_archived_booklets_range(db):
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-10", 8000, 8050, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-10'")[0]
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE check_booklets SET status = 'Archived' WHERE booklet_id = ?", (booklet_id,))
    conn.commit()
    conn.close()

    ok, msg = app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-11", 8000, 8050, date(2026, 1, 2))

    assert ok


# --- 2026-09-20: checkbook ranges are the FULL printed number; checks are stored under it as typed ---

def test_audit_understands_a_full_number_booklet(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "REAL-1", 3992554301, 3992554310, date(2026, 9, 20))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'REAL-1'")[0]
    import sqlite3
    conn = sqlite3.connect(db)
    for number in ("3992554301", "3992554302", "3992554304"):   # skips ...303
        conn.execute(
            "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (?, ?, ?, ?, 100, '2026-10-01', 'Active')",
            (BANK_ACCOUNT_ID, statement_id, creditor_id, number),
        )
    conn.commit(); conn.close()

    result = audit_booklet_gaps(booklet_id)

    assert result["missing_gaps_count"] == 1
    assert result["missing_gaps_list"] == [str(3992554303).zfill(10)]


def test_assigning_from_a_booklet_uses_the_registered_number_as_is(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "REAL-2", 3992554301, 3992554305, date(2026, 9, 20))
    import sqlite3
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    booklet = dict(conn.execute("SELECT * FROM check_booklets WHERE booklet_code = 'REAL-2'").fetchone())

    number, err = app.assign_check_number_from_booklet(conn.cursor(), BANK_ACCOUNT_ID, booklet, 3992554301)

    assert err is None
    assert number == "3992554301"      # not "3993992554301"


def test_a_short_legacy_number_blocks_the_same_leaf_in_full_form(db):
    """Until migrated, an old 7-digit check number and the full 10-digit form are the SAME physical leaf."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (?, ?, ?, '2554301', 100, '2026-10-01', 'Active')",
        (BANK_ACCOUNT_ID, statement_id, creditor_id),
    )
    conn.commit()
    cur = conn.cursor()

    assert app._is_check_number_available(cur, BANK_ACCOUNT_ID, "3992554301") is False
    assert app._is_check_number_available(cur, BANK_ACCOUNT_ID, "2554301") is False
    assert app._is_check_number_available(cur, BANK_ACCOUNT_ID, "3992554302") is True


# --- 2026-09-21: retracting a wrongly assigned number; a typed number beats the booklet dropdown ---

def _numbered_check(db, statement_id, creditor_id, leaf):
    import sqlite3
    conn = sqlite3.connect(db)
    cid = conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, amount, check_date, status) VALUES (?, ?, ?, 100, '2026-10-01', 'Active')",
        (BANK_ACCOUNT_ID, statement_id, creditor_id)).lastrowid
    conn.commit(); conn.close()
    return cid


def test_retracting_a_booklet_number_frees_the_leaf_and_leaves_no_gap(db):
    import sqlite3
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "RT-1", 3992554301, 3992554310, date(2026, 9, 20))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'RT-1'")[0]
    ids = [_numbered_check(db, statement_id, creditor_id, n) for n in range(3)]
    for check_id, leaf in zip(ids, (3992554301, 3992554302, 3992554303)):
        assert app.assign_check_number(check_id, booklet_id, leaf, None)[0]
    assert fetchone(db, "SELECT next_available_number FROM check_booklets WHERE booklet_id = ?", (booklet_id,)) == (3992554304,)

    ok, _ = app.retract_check_number(ids[2])
    assert ok
    assert fetchone(db, "SELECT check_number, booklet_id FROM checks_issued WHERE check_id = ?", (ids[2],)) == (None, None)
    assert fetchone(db, "SELECT next_available_number FROM check_booklets WHERE booklet_id = ?", (booklet_id,)) == (3992554303,)
    app.retract_check_number(ids[1]); app.retract_check_number(ids[0])
    assert fetchone(db, "SELECT next_available_number FROM check_booklets WHERE booklet_id = ?", (booklet_id,)) == (3992554301,)
    result = audit_booklet_gaps(booklet_id)
    assert result["missing_gaps_count"] == 0 and result["duplicate_numbers_count"] == 0
    assert 3992554301 in app.get_available_booklet_numbers(BANK_ACCOUNT_ID, {"booklet_id": booklet_id, "start_number": 3992554301, "end_number": 3992554310})
    assert app.assign_check_number(ids[0], None, None, "3992542553")[0]              # now the typed number can go on
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT action FROM (SELECT user_action AS action FROM audit_logs WHERE user_action = 'CHECK_NUMBER_RETRACTED')").fetchall()


def test_retract_only_from_the_top_moves_the_pointer_back_over_free_leaves(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "RT-2", 3992560001, 3992560010, date(2026, 9, 20))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'RT-2'")[0]
    first, second = _numbered_check(db, statement_id, creditor_id, 0), _numbered_check(db, statement_id, creditor_id, 1)
    app.assign_check_number(first, booklet_id, 3992560001, None); app.assign_check_number(second, booklet_id, 3992560002, None)

    app.retract_check_number(first)             # the lower leaf is freed, but the higher one is still used

    assert fetchone(db, "SELECT next_available_number FROM check_booklets WHERE booklet_id = ?", (booklet_id,)) == (3992560003,)
    assert app.retract_check_number(first)[0] is False                                  # nothing left to retract on it


def test_a_typed_number_always_wins_over_the_booklet_dropdown():
    assert app.pick_number_source(1, 3992554304, "3992542553") == ("manual", "3992542553")
    assert app.pick_number_source(1, 3992554304, "  ") == ("booklet", (1, 3992554304))
    assert app.pick_number_source(None, None, None) == ("none", None)


def test_audit_shows_spoiled_leaves_as_error_checks_not_open_stock_or_gaps(db):
    """2026-09-27: leaves logged under 'Log a spoiled check' were invisible to the audit -- still 'Open Stock', or a 'MISSING GAP' when an
    issued number lay beyond them."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    app.create_booklet(BANK_ACCOUNT_ID, "TEST-BOOK-3", 3990003000, 3990003005, date(2026, 1, 1))
    booklet_id = fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'TEST-BOOK-3'")[0]
    assert app.log_spoiled_check("3990003001", "printer jam")[0]
    assert app.log_spoiled_check("3990003003", "")[0]
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (?, ?, ?, '3990003004', 100, '2026-01-01', 'Active')",
                 (BANK_ACCOUNT_ID, statement_id, creditor_id))
    conn.commit()
    conn.close()

    result = audit_booklet_gaps(booklet_id)
    states = dict(zip(result["detailed_breakdown"]["check_number"], result["detailed_breakdown"]["state"]))

    assert states["3990003001"] == "Spoiled (error check)" and states["3990003003"] == "Spoiled (error check)"
    assert result["spoiled_count"] == 2
    notes = dict(zip(result["detailed_breakdown"]["check_number"], result["detailed_breakdown"]["note"]))
    assert notes["3990003001"] == "Error check: printer jam"
    assert result["missing_gaps_list"] == ["3990003000", "3990003002"]     # only the genuinely unaccounted leaves before the last issued one
    assert states["3990003005"] == "Open Stock"
