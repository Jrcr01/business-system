"""Marking days on the payment calendar (2026-10-03): holidays and no-check days move dates, notes only remind; Owner and Payables mark, others only look; every change is audited."""
from datetime import date

from conftest import fetchone
from engines import calendar_marks as cm
from engines.acting_user import acting_as

DAY = date(2026, 10, 9)


def test_a_note_is_stored_apart_and_moves_no_date(db):
    with acting_as("clerk@example.com", "payables"):
        assert cm.mark_day(DAY, "note", "Supplier visit")[0]
    assert cm.marks_for_month(2026, 10)[DAY] == {"kind": "note", "label": "Supplier visit"}
    assert fetchone(db, "SELECT COUNT(*) FROM calendar_blocked_dates WHERE blocked_date = '2026-10-09'")[0] == 0         # not a blocked day: no payment moves because of it


def test_a_holiday_and_a_no_check_day_are_blocked_days(db):
    with acting_as("clerk@example.com", "payables"):
        assert cm.mark_day(DAY, "holiday", "Test holiday")[0] and cm.mark_day(date(2026, 10, 12), "custom", "")[0]
    marks = cm.marks_for_month(2026, 10)
    assert marks[DAY]["kind"] == "holiday" and marks[date(2026, 10, 12)]["kind"] == "custom"
    assert fetchone(db, "SELECT kind FROM calendar_blocked_dates WHERE blocked_date = '2026-10-12'")[0] == "custom"


def test_marking_again_replaces_the_old_mark_and_unmarking_clears_it(db):
    with acting_as("clerk@example.com", "payables"):
        cm.mark_day(DAY, "note", "Payday")
        assert cm.mark_day(DAY, "custom", "Skip")[0]
        assert cm.marks_for_month(2026, 10)[DAY]["kind"] == "custom" and fetchone(db, "SELECT COUNT(*) FROM calendar_notes")[0] == 0
        assert cm.unmark_day(DAY)[0]
    assert DAY not in cm.marks_for_month(2026, 10)


def test_a_note_needs_its_text_and_only_owner_payables_and_admin_may_mark(db):
    with acting_as("clerk@example.com", "payables"):
        assert not cm.mark_day(DAY, "note", "")[0] and not cm.mark_day(DAY, "party", "x")[0]
    for role in ("purchasing", "viewer", "releaser"):
        with acting_as("x@example.com", role):
            assert not cm.mark_day(DAY, "holiday", "x")[0] and not cm.unmark_day(DAY)[0]
    for role in ("owner", "admin"):
        with acting_as("x@example.com", role):
            assert cm.mark_day(DAY, "note", "ok")[0]


def test_every_change_is_audited_with_who_did_it(db):
    with acting_as("clerk@example.com", "payables"):
        cm.mark_day(DAY, "holiday", "Test")
        cm.unmark_day(DAY)
    rows = [r[0] for r in __import__("sqlite3").connect(db).execute("SELECT user_email FROM audit_logs WHERE user_action IN ('CALENDAR_MARKED', 'CALENDAR_UNMARKED') ORDER BY rowid")]
    assert rows == ["clerk@example.com", "clerk@example.com"]
