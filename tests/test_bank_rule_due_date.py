"""Due dates by the bank's rule (2026-09-26): whole-month terms land on the same day of the month as the invoice."""
from datetime import date

import app


def test_whole_month_terms_keep_the_same_day_of_the_month():
    f = app.add_terms_bank_rule
    assert f(date(2026, 9, 8), "120") == date(2027, 1, 8)      # not Jan 6, which is what counting 120 days gives
    assert f(date(2026, 9, 11), "120") == date(2027, 1, 11)
    assert f(date(2026, 12, 15), "60") == date(2027, 2, 15)
    assert f(date(2026, 9, 8), "150") == date(2027, 2, 8)
    assert f(date(2026, 10, 5), "30") == date(2026, 11, 5)


def test_month_without_that_day_uses_the_last_day_of_the_month():
    f = app.add_terms_bank_rule
    assert f(date(2026, 1, 31), "30") == date(2026, 2, 28)
    assert f(date(2026, 11, 30), "90") == date(2027, 2, 28)
    assert f(date(2027, 12, 31), "60") == date(2028, 2, 29)     # leap year


def test_cod_and_terms_that_are_not_whole_months_keep_the_plain_day_count():
    f = app.add_terms_bank_rule
    assert f(date(2026, 9, 8), "COD") == date(2026, 9, 8)
    assert f(date(2026, 9, 8), "140") == date(2027, 1, 26)
    assert f(date(2026, 9, 8), "") == date(2026, 9, 8)


def test_resolve_due_date_uses_the_bank_rule():
    assert app.resolve_due_date("2026-09-08", "120") == date(2027, 1, 8)
