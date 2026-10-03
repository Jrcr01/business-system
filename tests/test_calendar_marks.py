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


def test_a_payment_on_a_marked_day_is_paid_the_next_bank_day_in_the_schedule_and_the_calendar_and_comes_back_when_unmarked(db):
    import app
    from conftest import make_creditor
    from engines.check_rules import issue_check
    from engines.reports_service import get_payment_calendar_items, get_payment_schedule_all_tracks, next_banking_day
    from engines.statement_rules import add_invoice
    cid = make_creditor(db, name="Marked Day Supplier")
    sid = app.create_order_statement(cid)
    add_invoice(cid, "MK-1", date(2026, 10, 1), date(2026, 11, 1), 5000.0, [], sid)
    wed = date(2026, 11, 11)                                                                  # a Wednesday
    with acting_as("owner@example.com", "owner"):
        assert issue_check(cid, 5000.0, wed, statement_id=sid)[0]

    def paid_on():
        df = get_payment_schedule_all_tracks()
        return {str(d)[:10] for d in df[df["amount"] == 5000.0]["actual_payment_date"]}

    assert paid_on() == {"2026-11-11"} and next_banking_day(wed) == wed
    with acting_as("clerk@example.com", "payables"):
        assert cm.payments_on(wed) == 1
        assert cm.mark_day(wed, "holiday", "Test holiday")[0]
        assert next_banking_day(wed) == date(2026, 11, 12)
        assert paid_on() == {"2026-11-12"}                                                    # the check stays dated Nov 11; it is paid the next bank day
        items = get_payment_calendar_items()
        assert str(items[items["amount"] == 5000.0]["due_date"].iloc[0])[:10] == "2026-11-11"   # its own date did not move
        assert cm.mark_day(date(2026, 11, 12), "custom", "")[0]
        assert next_banking_day(wed) == date(2026, 11, 13)                                      # two marked days in a row: past both
        assert cm.mark_day(date(2026, 11, 14), "note", "Payday")[0]
        assert next_banking_day(date(2026, 11, 14)) == date(2026, 11, 16)                       # a note moves nothing: Saturday goes to Monday as always
        assert cm.unmark_day(wed)[0] and cm.unmark_day(date(2026, 11, 12))[0]
    assert paid_on() == {"2026-11-11"}
