"""resolve_due_date() / parse_terms_days() -- the unconditional Date Received +
Terms recompute, per the 2026-09-14 fix (MEMORY.md). No DB needed: these are
pure functions."""
from datetime import date

import app


def test_terms_30_60_90_120():
    for terms, days in [("30", 30), ("60", 60), ("90", 90), ("120", 120)]:
        assert app.parse_terms_days(terms) == days


def test_terms_cod_is_zero_days():
    assert app.parse_terms_days("COD") == 0
    assert app.parse_terms_days("cod") == 0  # case-insensitive, per cell_text().strip().upper()


def test_terms_blank_or_garbage_defaults_to_zero():
    assert app.parse_terms_days("") == 0
    assert app.parse_terms_days(None) == 0
    assert app.parse_terms_days("not a number") == 0


def test_resolve_due_date_adds_terms_days_to_invoice_date():
    assert app.resolve_due_date(date(2026, 1, 15), "120") == date(2026, 5, 15)
    assert app.resolve_due_date(date(2026, 1, 15), "30") == date(2026, 2, 14)
    assert app.resolve_due_date(date(2026, 1, 15), "COD") == date(2026, 1, 15)


def test_resolve_due_date_accepts_string_invoice_date():
    """The grid can hand this a str (e.g. from a data_editor cell) as easily as
    a date object -- _as_date() inside resolve_due_date() must handle both."""
    assert app.resolve_due_date("2026-01-15", "30") == date(2026, 2, 14)


def test_resolve_due_date_is_unconditional_no_override_path():
    """The whole point of the 2026-09-14 simplification: there is no prior-value
    comparison left in the signature at all -- calling it twice with the same
    invoice_date/terms always returns the same answer, regardless of what a
    caller might have previously stored as "the due date"."""
    first = app.resolve_due_date(date(2026, 1, 15), "120")
    second = app.resolve_due_date(date(2026, 1, 15), "120")
    assert first == second == date(2026, 5, 15)
