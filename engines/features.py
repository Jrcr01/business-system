"""Parts of the system that are built but not released (owner, 2026-10-11: "no plan for deploying revenue cycle keep it in sandbox for the meantime").
The revenue cycle (Stock and Sell, stage A) is ON on a sandbox database (a file with TEST in its name) and OFF everywhere else, the server included.
WEB_REVENUE_CYCLE=1 switches it on by hand the day it is released; WEB_REVENUE_CYCLE=0 switches it off on a sandbox (the tests use that)."""
from __future__ import annotations

import os
from pathlib import Path

REVENUE_PAGES = frozenset({"stock", "stock-counts", "affiliates", "sales", "customers", "si-booklets"})
STAGE_B_PAGES = frozenset({"collect"})               # collect, deposit, cash in by day (owner's go 2026-10-11); released separately from stage A
BOOKS_PAGES = frozenset({"expenses", "income-statement"})     # expenses and the month's profit (owner's go 2026-10-11 evening); its own release
REVENUE_SETTINGS_GROUPS = frozenset({"Stock", "Sell"})
REVENUE_NOTES = frozenset({"2026-10-13a"})


def revenue_on() -> bool:
    asked = os.environ.get("WEB_REVENUE_CYCLE")
    if asked in ("0", "1"):
        return asked == "1"
    from engines import common
    db = Path(common.DB_PATH)
    if (db.parent / "revenue_on.txt").exists():
        # the server's ON switch (owner, 2026-10-11: "Stock and Sell, all on"): a file beside the database, like cf_access.txt,
        # because /etc/slicks-erp/env belongs to root and the deploy account may not edit it. Delete the file and restart to hide it all again.
        return True
    return "TEST" in db.name.upper()


def stage_b_on() -> bool:
    """Stage B (collect and receivables) shows like the revenue cycle: on a TEST database, with WEB_STAGE_B=1, or when stage_b_on.txt sits beside
    the server's database. Off until the owner's click-through and its own release."""
    asked = os.environ.get("WEB_STAGE_B")
    if asked in ("0", "1"):
        return asked == "1"
    from engines import common
    db = Path(common.DB_PATH)
    return (db.parent / "stage_b_on.txt").exists() or "TEST" in db.name.upper()


def books_on() -> bool:
    """Expenses and the Income statement, like stage B: a TEST database, WEB_BOOKS=1, or books_on.txt beside the server's database."""
    asked = os.environ.get("WEB_BOOKS")
    if asked in ("0", "1"):
        return asked == "1"
    from engines import common
    db = Path(common.DB_PATH)
    return (db.parent / "books_on.txt").exists() or "TEST" in db.name.upper()


def off_pages() -> frozenset:
    """The pages this site must not show at all: no menu entry, no Home card, no access row, and a 'not for you' page for the address typed by hand."""
    off = frozenset() if revenue_on() else REVENUE_PAGES
    if not (revenue_on() and stage_b_on()):          # stage B needs the revenue cycle on AND its own switch
        off = off | STAGE_B_PAGES
    if not books_on():
        off = off | BOOKS_PAGES
    return off
