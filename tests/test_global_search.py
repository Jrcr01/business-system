"""Search everything (2026-09-27)."""
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.global_search_service import search_everything


def _seed(db):
    creditor_id = make_creditor(db, name="Tire Asia Group Inc")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-69055", date(2026, 1, 1), date(2026, 1, 31), 29751.5, [], statement_id)
    app.issue_check(creditor_id, 29751.5, date(2026, 3, 1), statement_id=statement_id)
    app.log_spoiled_check("3992554303", "jam")
    return creditor_id, statement_id


def _titles(term):
    return {g["title"]: g for g in search_everything(term)}


def test_a_name_finds_every_kind_of_record_it_appears_in(db):
    _seed(db)
    found = _titles("Tire Asia")
    assert {"Checks", "Invoices", "Order statements", "Creditors"} <= set(found)
    assert found["Invoices"]["rows"][0]["tab"] == 3 and "selected_statement_id" in found["Invoices"]["rows"][0]["state"]


def test_an_invoice_number_an_exact_amount_and_a_spoiled_number_are_found(db):
    _seed(db)
    assert "Invoices" in _titles("69055")
    by_amount = _titles("29,751.50")
    assert "Invoices" in by_amount and "Checks" in by_amount                       # commas and decimals are understood
    assert _titles("3992554303")["Spoiled checks (error checks)"]["rows"][0]["tab"] == 7


def test_too_short_or_unknown_terms_find_nothing(db):
    _seed(db)
    assert search_everything("a") == [] and search_everything("") == []
    assert search_everything("zzzqqq-nothing") == []
