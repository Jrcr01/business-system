"""Startup check: is the database missing anything the current program needs? (2026-09-21)

New features sometimes add a table or column. If the program is updated before the database is, screens
fail with a confusing "no such column" error. This compares the database with schema.sql -- read only,
nothing is changed -- so the app can say plainly what to do instead.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from apply_pending_migrations import diff_and_sync  # noqa: E402


def pending_schema_updates(db_path: str) -> list[str]:
    """Plain descriptions of the tables/columns the database lacks; empty list = up to date."""
    report = diff_and_sync(db_path, dry_run=True)
    return [f"table {t}" for t in report["tables_added"]] + [f"column {c}" for c in report["columns_added"]]
