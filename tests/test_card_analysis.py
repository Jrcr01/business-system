"""Analytics -> Credit cards (owner, 2026-10-15: "Add credit card analysis", "include finance charges incurred"): owed per card and cardholder, charged vs
paid by statement month, the finance charges incurred this year, each card's latest statement; no limit warning; the family owners see it too."""
import sqlite3
from datetime import timedelta

from conftest import make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _card(db, holder="Rhodora", limit=300_000):
    bank = make_creditor(db, "BPI Cards", creditor_type="CREDIT_CARD")
    with sqlite3.connect(_db()) as conn:
        hid = conn.execute("INSERT INTO cardholders (name) VALUES (?)", (holder,)).lastrowid
        return conn.execute("INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, credit_limit) VALUES (?, ?, 'Amore', '4411', ?)",
                            (bank, hid, limit)).lastrowid


def _stmt(card, due, total, status="Unpaid", new=0.0, finance=0.0, prev=0.0, carried=0.0, paid=0.0):
    with sqlite3.connect(_db()) as conn:
        conn.execute("""INSERT INTO cc_statements (card_id, due_date, total_amount_due, previous_balance, new_charges, interest_charged, amount_paid,
                        unpaid_balance_next, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", (card, due.isoformat(), total, prev, new, finance, paid, carried, status))


def test_finance_charges_owed_and_each_card_come_from_the_statements(web, db):
    from engines.card_analysis import analysis
    from engines.clock import manila_today
    today = manila_today()
    card = _card(db)
    first = today.replace(day=1)
    _stmt(card, first - timedelta(days=40), 100_000.0, status="Paid")                                     # paid in full, no charge
    _stmt(card, first - timedelta(days=10), 80_000.0, new=78_000.0, finance=2_000.0, carried=30_000.0, paid=50_000.0, status="Partially Paid")
    _stmt(card, today + timedelta(days=12), 150_000.0, new=118_500.0, finance=1_500.0, prev=30_000.0)       # carries the 30,000 forward
    r = analysis(today)
    if today.month > 1 or (first - timedelta(days=40)).year == today.year:
        assert r["finance_year"] == 3_500.0 and r["finance_cards"] == 1
    assert r["owed"] == 150_000.0                                         # the carried 30,000 is owed once, inside the latest statement
    assert r["holders"][0]["label"] == "Rhodora" and r["holders"][0]["amount"] == 150_000.0
    c = r["cards"][0]
    assert c["used"] == 50 and c["finance"] == 1_500.0 and ("warn", "Finance charge") in c["watch"]
    assert not any("limit" in w.lower() for _, w in c["watch"])           # no limit warning (owner: "not need")
    assert r["due30"] == 150_000.0 and r["used_pct"] == 50


def test_the_tab_draws_and_follows_the_credit_cards_page(web, db):
    from engines.acting_user import acting_as
    from engines.clock import manila_today
    from engines.permissions import analytics_tabs
    _admin(web)
    card = _card(db)
    _stmt(card, manila_today() + timedelta(days=5), 60_000.0, new=59_000.0, finance=1_000.0)
    page = web.get("/analytics/?tab=cards").content.decode()
    assert "could not be drawn" not in page and "Finance charges incurred" in page and "Charged vs paid" in page and "Amore ·4411" in page
    with sqlite3.connect(_db()) as conn:
        uid = conn.execute("INSERT INTO app_users (email, role, name, password_hash) VALUES ('fam@slicks.test', 'owner', 'Fam', 'x')").lastrowid
    with acting_as("fam@slicks.test", "owner"):
        assert "cards" in analytics_tabs()                                # the family owners see it too
    with sqlite3.connect(_db()) as conn:
        conn.execute("INSERT INTO user_hidden_pages (user_id, page_id) VALUES (?, 'credit-cards')", (uid,))
    with acting_as("fam@slicks.test", "owner"):
        assert "cards" not in analytics_tabs()                            # Credit cards hidden from a person hides this tab
