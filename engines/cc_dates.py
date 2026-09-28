"""Which month a credit card statement belongs to (owner, 2026-09-29).

"card's statement date but this is usually due month minus 1 there are also instances where due date is within the same month of
statement date. one thing is for sure no two statement in a month."

So a statement's month is the month of its STATEMENT DATE: the card's statement day (credit_cards.statement_day) that falls 10-45 days
before the due date (measured on 151 statements on 2026-09-29: 15-29 days; 76% land in the month before the due month, 24% in the
same month). With no statement day on file: the due date minus 21 days. The typed cc_statements.statement_month is NOT used -- most old
statements were labelled with their due month. One statement per card per statement month; two DUE dates in one calendar month are
normal for a card on a ~30-day cycle (due near the 1st and the 30th). No other module is imported here.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

MIN_DAYS_BEFORE_DUE = 10
MAX_DAYS_BEFORE_DUE = 45
DEFAULT_DAYS_BEFORE_DUE = 21


def _as_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def statement_date_for(due, statement_day: int | None) -> date:
    """The statement date behind a due date: the latest date with the card's statement day that is 10-45 days before the due date;
    the due date minus 21 days when the card has no statement day (or none fits)."""
    due = _as_date(due)
    try:
        day = int(statement_day) if statement_day is not None else 0
    except (TypeError, ValueError):
        day = 0
    if day > 0:
        y, m = due.year, due.month
        for _ in range(3):
            candidate = date(y, m, min(day, calendar.monthrange(y, m)[1]))
            if MIN_DAYS_BEFORE_DUE <= (due - candidate).days <= MAX_DAYS_BEFORE_DUE:
                return candidate
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return due - timedelta(days=DEFAULT_DAYS_BEFORE_DUE)


def statement_month_for(due, statement_day: int | None) -> str:
    """'YYYY-MM' of the statement date behind a due date."""
    return statement_date_for(due, statement_day).strftime("%Y-%m")
