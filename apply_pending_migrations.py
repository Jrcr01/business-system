"""Sandbox -> live schema promotion (2026-09-17).

The sandbox/live setup this project uses: one shared codebase (app.py, engines/*.py,
migrations/*.sql, schema.sql) against two separate database files -- data/ap_disbursement_TEST.db
for designing/testing changes, data/ap_disbursement.db for the real thing. Code changes apply to
both automatically (there's only one copy). Schema changes don't -- each database's on-disk
tables only actually change when someone runs this against that specific file.

Design note (2026-09-17): this used to replay individual migrations/*.sql files in order,
tracked against a schema_migrations table. That broke the first real time it ran against TEST --
several early migrations are intermediate table-rebuild steps (SQLite can't ALTER a NOT NULL/CHECK
constraint away, so a handful of migrations recreate a table under a temporary "_new" name, copy
data across, then drop/rename) that assume the data shape at THAT point in this project's history.
Replayed against a database that was actually built by running schema.sql fresh (which is how
every database in this project is really built -- migrations/ is a changelog merged into
schema.sql, not something ever replayed in sequence), the data no longer matches what an
intermediate migration's rebuild step assumed, and it can fail outright (as it did: a rebuild step
tried to copy already-NULL placeholder values into a column an EARLIER, superseded version of the
same table still had marked NOT NULL).

So instead: this diffs the target database's actual current schema against schema.sql (the one
place this project already keeps as the authoritative, fully-current state) and applies only
what's actually missing -- new tables verbatim, new columns via ALTER TABLE ADD COLUMN, and view
definitions kept in sync (a view holds no data, so it's always safe to drop and recreate).
Existing columns are never altered, narrowed, or dropped -- a real breaking change still needs a
hand-written migration and manual review; this tool only ever adds.

Usage:
    python apply_pending_migrations.py                    # targets data/ap_disbursement.db (live)
    python apply_pending_migrations.py --db data/ap_disbursement_TEST.db
    python apply_pending_migrations.py --dry-run           # report what would change, touch nothing
    AP_DB_PATH=... python apply_pending_migrations.py     # honors the same override every other
                                                            # script in this project uses

Always back up the target file first for anything other than the TEST db.
"""
import argparse
import os
import shutil
import sqlite3
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schema.sql")
from db_location import live_db_path
LIVE_DB_PATH = live_db_path()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    # sqlite_sequence is SQLite's own internal bookkeeping table for AUTOINCREMENT columns --
    # it exists as soon as any table needs it, not something schema.sql ever creates itself, so
    # diffing it as a "missing table" trips over SQLite refusing to let anything but SQLite
    # itself create it.
    return {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not r[0].startswith("sqlite_")
    }


def _view_defs(conn: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='view'")}


def _index_defs(conn: sqlite3.Connection) -> dict[str, str]:
    # sqlite_autoindex_* entries are SQLite's own implicit indexes for PRIMARY KEY/UNIQUE column
    # constraints -- those already travel with the table's own CREATE TABLE statement, so only
    # explicitly-named CREATE INDEX statements need diffing here.
    return {
        r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")
    }


def _create_table_sql(conn: sqlite3.Connection, table: str) -> str | None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    return row[0] if row else None


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def diff_and_sync(target_db_path: str, dry_run: bool = False) -> dict:
    """Returns {"tables_added": [...], "columns_added": [...], "views_synced": [...], "skipped": [...]}.
    skipped holds anything this tool deliberately would NOT do safely on its own (e.g. a new NOT
    NULL column with no default -- ALTER TABLE ADD COLUMN can't add that against a table with
    existing rows), so nothing here is silently lost, just flagged for a manual migration."""
    ref = sqlite3.connect(":memory:")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        ref.executescript(f.read())

    target = sqlite3.connect(target_db_path)
    report = {"tables_added": [], "columns_added": [], "views_synced": [], "indexes_added": [], "skipped": []}

    ref_tables = _table_names(ref)
    target_tables = _table_names(target)

    for table in sorted(ref_tables - target_tables):
        sql = _create_table_sql(ref, table)
        report["tables_added"].append(table)
        if not dry_run:
            target.execute(sql)

    for table in sorted(ref_tables & target_tables):
        ref_cols = _columns(ref, table)
        target_cols = _columns(target, table)
        for col_name, col_row in ref_cols.items():
            if col_name in target_cols:
                continue
            _, _, col_type, notnull, default, _ = col_row
            if notnull and default is None:
                report["skipped"].append(f"{table}.{col_name} (NOT NULL with no default -- needs a real migration)")
                continue
            stmt = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type or 'TEXT'}"
            if default is not None:
                stmt += f" DEFAULT {default}"
            report["columns_added"].append(f"{table}.{col_name}")
            if not dry_run:
                target.execute(stmt)

    ref_views = _view_defs(ref)
    target_views = _view_defs(target)
    for view_name, view_sql in ref_views.items():
        if target_views.get(view_name) != view_sql:
            report["views_synced"].append(view_name)
            if not dry_run:
                target.execute(f"DROP VIEW IF EXISTS {view_name}")
                target.execute(view_sql)

    ref_indexes = _index_defs(ref)
    target_indexes = _index_defs(target)
    for index_name, index_sql in ref_indexes.items():
        if target_indexes.get(index_name) == index_sql:
            continue
        # A UNIQUE index can't be created if the target table already has real duplicate data --
        # that's a genuine data problem to fix by hand, not something to force through here.
        try:
            if not dry_run:
                target.execute(f"DROP INDEX IF EXISTS {index_name}")
                target.execute(index_sql)
            report["indexes_added"].append(index_name)
        except sqlite3.IntegrityError as e:
            report["skipped"].append(f"index {index_name} ({e} -- existing data violates this constraint, needs manual cleanup first)")

    if not dry_run:
        target.commit()
    target.close()
    ref.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=os.environ.get("AP_DB_PATH", LIVE_DB_PATH), help="Path to the target database (default: live).")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change without touching the database.")
    parser.add_argument("--no-backup", action="store_true", help="Skip the automatic .bak_pre_schema_sync_<timestamp> copy (dry-run never needs one).")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        raise FileNotFoundError(f"No database at {args.db}")

    print(f"Target: {args.db}")
    print(f"Mode: {'DRY RUN (no changes will be made)' if args.dry_run else 'LIVE'}")
    print()

    if not args.dry_run and not args.no_backup:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{args.db}.bak_pre_schema_sync_{timestamp}"
        shutil.copy2(args.db, backup_path)
        print(f"Backed up to {backup_path}")
        print()

    report = diff_and_sync(args.db, dry_run=args.dry_run)

    if not any(report.values()):
        print("Already up to date -- nothing to sync.")
        return

    if report["tables_added"]:
        print(f"Tables added ({len(report['tables_added'])}):")
        for t in report["tables_added"]:
            print(f"  + {t}")
    if report["columns_added"]:
        print(f"Columns added ({len(report['columns_added'])}):")
        for c in report["columns_added"]:
            print(f"  + {c}")
    if report["views_synced"]:
        print(f"Views synced ({len(report['views_synced'])}):")
        for v in report["views_synced"]:
            print(f"  ~ {v}")
    if report["indexes_added"]:
        print(f"Indexes added ({len(report['indexes_added'])}):")
        for i in report["indexes_added"]:
            print(f"  + {i}")
    if report["skipped"]:
        print(f"Skipped -- needs a hand-written migration ({len(report['skipped'])}):")
        for s in report["skipped"]:
            print(f"  ! {s}")

    if args.dry_run:
        print()
        print("Dry run only -- nothing was changed. Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
