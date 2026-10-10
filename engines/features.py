"""Parts of the system that are built but not released (owner, 2026-10-11: "no plan for deploying revenue cycle keep it in sandbox for the meantime").
The revenue cycle (Stock and Sell, stage A) is ON on a sandbox database (a file with TEST in its name) and OFF everywhere else, the server included.
WEB_REVENUE_CYCLE=1 switches it on by hand the day it is released; WEB_REVENUE_CYCLE=0 switches it off on a sandbox (the tests use that)."""
from __future__ import annotations

import os
from pathlib import Path

REVENUE_PAGES = frozenset({"stock", "stock-counts", "affiliates", "sales", "customers", "si-booklets"})
REVENUE_SETTINGS_GROUPS = frozenset({"Stock"})
REVENUE_NOTES = frozenset({"2026-10-13a"})


def revenue_on() -> bool:
    asked = os.environ.get("WEB_REVENUE_CYCLE")
    if asked in ("0", "1"):
        return asked == "1"
    from engines import common
    return "TEST" in Path(common.DB_PATH).name.upper()


def off_pages() -> frozenset:
    """The pages this site must not show at all: no menu entry, no Home card, no access row, and a 'not for you' page for the address typed by hand."""
    return frozenset() if revenue_on() else REVENUE_PAGES
