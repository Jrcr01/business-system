"""PDC release (B5): a post-dated check is printed, then the supplier collects it. 'For release' lists the printed checks nobody has collected yet; marking them released
records the date and who took them. Handwritten checks are handed over when recorded, so they are never waiting."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines.pdc_release_service import for_release, mark_released, recent_releases, release_summary, undo_release

TODAY = date(2026, 10, 19)


def _check(db, printed=True, amount=1000.0, on=date(2026, 12, 1), handwritten=False):
    cid = make_creditor(db, name="Acme Tires")
    sid = app.create_order_statement(cid)
    app.add_invoice(cid, f"INV-{amount}-{on}", date(2026, 9, 1), date(2026, 10, 1), amount * 2, [], sid)
    ok, _, check_id = app.issue_check(cid, amount, on, statement_id=sid)
    assert ok
    con = sqlite3.connect(db)
    if printed:
        con.execute("UPDATE checks_issued SET printed_at = '2026-10-15 10:00:00', check_number = ?, is_handwritten = ? WHERE check_id = ?", (f"39920{check_id:05d}", int(handwritten), check_id))
    con.commit()
    con.close()
    return check_id


def test_only_printed_uncollected_checks_are_waiting_for_release(db):
    waiting = _check(db, amount=1000.0)
    _check(db, printed=False, amount=2000.0)                                  # not printed yet: nothing to hand over
    _check(db, amount=3000.0, handwritten=True)                               # handwritten: given to the supplier when recorded
    df = for_release(today=TODAY)
    assert df["check_id"].tolist() == [waiting]
    r = df.iloc[0]
    assert r["supplier"] == "Acme Tires" and r["amount"] == 1000.0 and r["days_waiting"] == 4      # printed Oct 15, today Oct 19
    assert release_summary(today=TODAY) == {"waiting": 1, "amount": 1000.0, "oldest_days": 4}


def test_marking_released_records_the_date_and_who_collected_it(db):
    a, b = _check(db, amount=1000.0), _check(db, amount=2000.0, on=date(2026, 12, 8))
    ok, msg = mark_released([a, b], date(2026, 10, 20), "  Juan dela Cruz ")
    assert ok, msg
    rows = {r[0]: r[1:] for r in sqlite3.connect(db).execute("SELECT check_id, released_date, released_to FROM checks_issued").fetchall()}
    assert rows[a] == ("2026-10-20", "Juan dela Cruz") and rows[b] == ("2026-10-20", "Juan dela Cruz")
    assert for_release(today=TODAY).empty
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_RELEASED'")[0] == 2
    rec = recent_releases(days=30, today=date(2026, 10, 25))
    assert sorted(rec["check_id"]) == sorted([a, b]) and rec["released_to"].iloc[0] == "Juan dela Cruz"


def test_bad_releases_are_refused_and_write_nothing(db):
    ok_check, unprinted = _check(db, amount=1000.0), _check(db, printed=False, amount=2000.0)
    assert not mark_released([], date(2026, 10, 20), "x")[0]
    assert not mark_released([unprinted], date(2026, 10, 20), "x")[0]                      # never printed
    assert not mark_released([ok_check, 99999], date(2026, 10, 20), "x")[0]                # an unknown check stops the whole save
    assert fetchone(db, "SELECT released_date FROM checks_issued WHERE check_id = ?", (ok_check,))[0] is None
    assert mark_released([ok_check], date(2026, 10, 20), "x")[0]
    assert not mark_released([ok_check], date(2026, 10, 21), "y")[0]                       # already released
    assert app.cancel_check(ok_check, "test")[0]
    assert not mark_released([ok_check], date(2026, 10, 22), "z")[0]                       # a cancelled check is not handed out


def test_a_release_can_be_undone_until_the_bank_has_cleared_the_check(db):
    c = _check(db)
    mark_released([c], date(2026, 10, 20), "Juan")
    assert undo_release(c)[0] and for_release(today=TODAY)["check_id"].tolist() == [c]
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_RELEASE_UNDONE'")[0] == 1
    mark_released([c], date(2026, 10, 20), "Juan")
    sqlite3.connect(db).execute("UPDATE checks_issued SET bank_cleared_date = '2026-10-30' WHERE check_id = ?", (c,)).connection.commit()
    ok, msg = undo_release(c)
    assert not ok and "cleared" in msg.lower()
    assert not undo_release(99999)[0]


def test_a_released_date_cannot_be_before_the_check_was_printed(db):
    c = _check(db)
    ok, msg = mark_released([c], date(2026, 10, 1), "Juan")
    assert not ok and "printed" in msg.lower()


def test_the_one_time_catch_up_marks_old_prints_as_already_collected(db):
    from engines.pdc_release_service import preview_release_before, release_before
    old, new = _check(db, amount=1000.0), _check(db, amount=2000.0, on=date(2026, 12, 8))
    sqlite3.connect(db).execute("UPDATE checks_issued SET printed_at = '2026-10-18 09:00:00' WHERE check_id = ?", (new,)).connection.commit()
    assert preview_release_before(date(2026, 10, 15)) == {"checks": 1, "amount": 1000.0}
    ok, msg = release_before(date(2026, 10, 15))
    assert ok, msg
    assert fetchone(db, "SELECT released_date, released_to FROM checks_issued WHERE check_id = ?", (old,)) == ("2026-10-15", "before release tracking")
    assert for_release(today=TODAY)["check_id"].tolist() == [new]                         # the newer print is still waiting
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECKS_RELEASED_BULK'")[0] == 1
    assert not release_before(date(2026, 10, 15))[0]                                       # nothing left to catch up
