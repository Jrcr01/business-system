"""A credit card statement's month (owner, 2026-09-29): the month of the card's STATEMENT DATE -- usually the month before the due
month, sometimes the same month; one statement per card per month. The statement date is the card's statement day that falls
10-45 days before the due date (measured on 151 statements: 15-29 days); with no statement day on file, the due date minus 21 days."""
from datetime import date

from engines.cc_dates import statement_date_for, statement_month_for


def test_real_examples():
    assert statement_date_for(date(2026, 9, 28), 3) == date(2026, 9, 3)      # Visa Flex: early statement day -> same month as due
    assert statement_date_for(date(2026, 9, 24), 30) == date(2026, 8, 30)    # Jcb Luckycat: late statement day -> month before
    assert statement_date_for(date(2026, 7, 7), 8) == date(2026, 6, 8)       # Mastercard Toyota 4047: June's statement ...
    assert statement_date_for(date(2026, 7, 29), 8) == date(2026, 7, 8)      # ... and July's -- two dues in July, two months
    assert statement_date_for(date(2026, 10, 19), 25) == date(2026, 9, 25)


def test_short_months_and_no_statement_day():
    assert statement_date_for(date(2026, 3, 20), 31) == date(2026, 2, 28)    # day 31 in February -> the last day
    assert statement_date_for(date(2026, 9, 10), None) == date(2026, 8, 20)  # no statement day on file: due date minus 21 days
    assert statement_date_for(date(2026, 9, 10), 0) == date(2026, 8, 20)


def test_month_label():
    assert statement_month_for("2026-09-28", 3) == "2026-09"
    assert statement_month_for("2026-09-24", 30) == "2026-08"
    assert statement_month_for("2026-01-20", None) == "2025-12"
