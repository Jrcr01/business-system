"""Keeps the last rows of a printed table together (owner, 2026-10-18, RQ-0001: "I want to avoid this total only in the next page. It is ok if there are
other listed" -- "this applies to other report as well"): when a table runs onto another page, its last item goes over with the total, never the total
alone under a repeated header."""
from reportlab.platypus import Table


def keep_tail(table: Table, rows: int = 2) -> Table:
    """Keep the last `rows` rows on one page (the header row is never part of it). Returns the table."""
    n = len(getattr(table, "_cellvalues", []) or [])
    head = int(getattr(table, "repeatRows", 0) or 0) if isinstance(getattr(table, "repeatRows", 0), int) else 1
    k = min(rows, n - head)
    if k >= 2:
        table.setStyle([("NOSPLIT", (0, n - k), (-1, n - 1))])
    return table
