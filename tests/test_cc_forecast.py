"""Credit card dues in the forecast and the planner (owner, 2026-09-29, changed 2026-10-02): only cards marked 'paid from Metrobank' count against the
Metrobank balance, and ONLY STATEMENTS THAT ARE ENTERED count. The old estimates for statements not in yet were removed (owner, 2026-10-02:
"it can be misleading")."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.cc_service import paid_from_bank_ids, set_paid_from_bank

TODAY = date(2026, 9, 29)


def _card(db, name, last4, statement_day=None, active=1):
    bank = make_creditor(db, name=f"Bank {name}", creditor_type="CREDIT_CARD", terms="")
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, is_active, statement_day) "
                            "VALUES (?, ?, ?, 1, ?, ?)", (bank, name, last4, active, statement_day)).lastrowid


def _stmt(db, card, due, amount, status="Paid"):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, ?)", (card, due, amount, status))


def test_a_card_with_history_but_no_entered_statement_adds_nothing(db):
    """The months after the last entered statement used to be guessed from the card's history. They are not any more."""
    from engines import check_date_planner as cp
    from engines.cash_insights_service import scheduled_outflows

    card = _card(db, "Habit Card", "7777")
    for due in ("2026-07-05", "2026-08-05", "2026-09-05"):
        _stmt(db, card, due, 500_000)
    set_paid_from_bank([card])
    out = scheduled_outflows(TODAY, date(2027, 2, 28))
    assert out[out["track"].str.startswith("Credit Card")].empty                       # all three are paid; nothing is guessed after them
    base = cp._baseline(TODAY, date(2027, 2, 28), 1_000_000, 0, set())
    assert sum(base["out"]) == 0


def test_an_entered_statement_counts_for_a_marked_card_only(db):
    from engines.cash_insights_service import outside_card_dues, scheduled_outflows

    inside = _card(db, "Bank Card", "1111")
    outside = _card(db, "Cash Card", "2222")
    for card, base in ((inside, 100_000), (outside, 400_000)):
        _stmt(db, card, "2026-08-29", base)
        _stmt(db, card, "2026-10-20", base * 2, status="Unpaid")             # a recorded, unpaid October bill
    assert paid_from_bank_ids() == set()

    def cards_out():
        out = scheduled_outflows(TODAY, date(2026, 12, 31))
        return out[out["track"].isin(["Credit Card", "Credit Card (estimated)"])]

    assert cards_out().empty                                                  # nothing marked: no card is deducted from the bank
    gone = outside_card_dues(TODAY, date(2026, 12, 31))
    assert gone == {"recorded": 1_000_000, "cards": 2}                         # 200,000 + 800,000 October bills

    set_paid_from_bank([inside])
    now = cards_out()
    assert list(now["track"].unique()) == ["Credit Card"] and now["amount"].sum() == 200_000     # the recorded bill of the marked card only
    after = outside_card_dues(TODAY, date(2026, 12, 31))
    assert after == {"recorded": 800_000, "cards": 1}                         # the other card is now the only one outside


def test_the_marked_cards_are_remembered_and_audited(db):
    a, b = _card(db, "A", "0001"), _card(db, "B", "0002")
    set_paid_from_bank([a])
    assert paid_from_bank_ids() == {a}
    set_paid_from_bank([a, b])
    set_paid_from_bank([])
    assert paid_from_bank_ids() == set()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CC_BANK_CARDS_SET'").fetchone()[0] == 3


def test_the_typed_cash_planner_path_also_skips_cards_paid_outside(db):
    from engines import check_date_planner as cp

    c = _card(db, "Outside", "3333")
    _stmt(db, c, "2026-08-29", 300_000)
    _stmt(db, c, "2026-10-20", 300_000, status="Unpaid")
    base = cp._baseline(TODAY, date(2026, 11, 30), 1_000_000, 0, set())
    assert sum(base["out"]) == 0                                              # a card paid outside the bank takes nothing from the balance
    set_paid_from_bank([c])
    base = cp._baseline(TODAY, date(2026, 11, 30), 1_000_000, 0, set())
    assert sum(base["out"]) == 300_000                                        # exactly the recorded bill: no estimates on top


def test_the_removed_estimate_code_is_gone():
    import importlib.util
    assert importlib.util.find_spec("engines.cc_forecast_service") is None
    assert importlib.util.find_spec("views.card_dues_note") is None
