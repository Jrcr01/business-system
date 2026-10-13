"""A card statement typed wrong can be corrected (owner, 2026-10-13: "why i cant edit this ... for example error"): the month and the due date
on any statement; the amounts on the card's newest statement only, since a later statement is built on the ones before it."""
import sqlite3

from conftest import fetchone, make_creditor
from tests.test_phone import _db, _n
from tests.test_web_d0 import _admin, _sign_in_new


def _card(db):
    issuer = make_creditor(db, name="China Bank", creditor_type="CREDIT_CARD")
    conn = sqlite3.connect(db)
    cid = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, statement_day) VALUES (?, 'CB Mastercard', '6000', 8)", (issuer,)).lastrowid
    conn.commit()
    conn.close()
    return cid


def _add(web, card, month, due, total, payments=""):
    r = web.post("/credit-cards/", {"action": "statement", "card": card, "month": month, "due": due, "total": total, "payments": payments, "interest": ""})
    assert r.status_code == 302
    return _n("SELECT MAX(cc_statement_id) FROM cc_statements")


def test_the_newest_statement_is_corrected_in_full_and_an_older_one_in_its_dates(web, db):
    _admin(web)
    card = _card(db)
    old = _add(web, card, "2026-08", "2026-09-01", "272,000")
    new = _add(web, card, "2026-09", "2026-09-30", "386,200", payments="272,000")
    page = web.get(f"/credit-cards/?card={card}&show=all").content.decode()
    assert page.count(">Edit</a>") == 2 and page.count(">Delete</button>") == 1          # every statement can be opened; only the newest deleted
    page = web.get(f"/credit-cards/?card={card}&show=all&edit={new}").content.decode()
    assert "Correct the statement" in page and 'id="es_total"' in page and "386,200.00" in page
    assert 'id="es_total"' not in web.get(f"/credit-cards/?card={card}&show=all&edit={old}").content.decode()      # an older one: dates only
    # the newest: total and due date typed wrong
    r = web.post("/credit-cards/", {"action": "correct_statement", "statement_id": new, "card": card, "month": "2026-09", "due": "2026-10-01", "total": "368,200", "interest": "0"})
    assert r.status_code == 302 and "edit=" not in r["Location"]
    assert fetchone(_db(), "SELECT due_date, total_amount_due, new_charges, unpaid_balance_next, status FROM cc_statements WHERE cc_statement_id = ?", (new,)) == (
        "2026-10-01", 368200.0, 368200.0, 368200.0, "Unpaid")
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CC_STATEMENT_CORRECTED'") == 1
    assert "386,200.00" in _n("SELECT old_values FROM audit_logs WHERE user_action = 'CC_STATEMENT_CORRECTED'")
    # an older one: its month label put right; its amount is refused
    web.post("/credit-cards/", {"action": "correct_statement", "statement_id": old, "card": card, "month": "2026-07", "due": "2026-08-31"})
    assert fetchone(_db(), "SELECT statement_month, due_date, total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (old,)) == ("2026-07", "2026-08-31", 272000.0)
    from engines.card_rules import correct_cc_statement
    ok, msg = correct_cc_statement(old, "2026-07", "2026-08-31", 1.0, None)
    assert not ok and "newest statement" in msg and _n("SELECT total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (old,)) == 272000
    # a due date may not jump over a neighbour
    ok, msg = correct_cc_statement(old, "2026-07", "2026-10-05")
    assert not ok and "neighbours" in msg
    assert correct_cc_statement(old, "2026-07", "2026-08-31") == (False, "Nothing was changed.")


def test_a_total_below_what_is_already_paid_is_refused_and_roles(web, db):
    from engines.card_rules import correct_cc_statement
    _admin(web)
    card = _card(db)
    sid = _add(web, card, "2026-09", "2026-09-30", "10,000")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE cc_statements SET amount_paid = 6000, status = 'Partially Paid' WHERE cc_statement_id = ?", (sid,))
    conn.commit()
    conn.close()
    ok, msg = correct_cc_statement(sid, "2026-09", "2026-09-30", 5000, 0)
    assert not ok and "already recorded as paid" in msg
    assert correct_cc_statement(sid, "2026-09", "2026-09-30", 9000, 0)[0]
    assert fetchone(_db(), "SELECT total_amount_due, unpaid_balance_next, status FROM cc_statements WHERE cc_statement_id = ?", (sid,)) == (9000.0, 3000.0, "Partially Paid")
    viewer = _sign_in_new("view@slicks.test", "viewer")
    viewer.post("/credit-cards/", {"action": "correct_statement", "statement_id": sid, "month": "2026-01", "due": "2026-09-29"})
    assert _n("SELECT due_date FROM cc_statements WHERE cc_statement_id = ?", (sid,)) == "2026-09-30"      # not theirs to change
