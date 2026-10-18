"""Corrections to saved entries, before and after (owner, 2026-10-19: "make all things for now editable since my brother is adapting the system"; "my worry
is they incorrectly input data"): every edit is kept here, and the owner's "Changed today" list on Checks and review reads it, with a Seen tick."""
from __future__ import annotations

from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx


def log(conn, kind: str, ref_type: str, ref_id: int | None, ref_text: str, before: str, after: str, why: str = "") -> None:
    conn.execute("""INSERT INTO entry_changes (kind, ref_type, ref_id, ref_text, before_text, after_text, why, changed_by) VALUES (?, ?, ?, ?, ?, ?, ?, acting_user())""",
                 (kind, ref_type, ref_id, ref_text, before, after, " ".join(str(why or "").split()) or None))


def changes(day: date | None = None, ref_type: str | None = None, ref_id: int | None = None) -> list[dict]:
    """The changes of a day (Manila; the timestamps are UTC), or every change of one record."""
    where, args = [], []
    if ref_type is not None:
        where.append("ref_type = ? AND ref_id = ?")
        args += [ref_type, ref_id]
    else:
        d = day or manila_today()
        where.append("date(changed_at, '+8 hours') = ?")
        args.append(str(d))
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT change_id, kind, ref_type, ref_id, ref_text, before_text, after_text, why, changed_by, datetime(changed_at, '+8 hours'), seen_by
                                FROM entry_changes WHERE {' AND '.join(where)} ORDER BY change_id""", args).fetchall()
    keys = ("id", "kind", "ref_type", "ref_id", "ref_text", "before", "after", "why", "by", "at", "seen_by")
    return [dict(zip(keys, r)) for r in rows]


def unseen_count() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM entry_changes WHERE seen_at IS NULL").fetchone()[0])


def mark_seen(change_id: int) -> tuple[bool, str]:
    with write_tx() as conn:
        n = conn.execute("UPDATE entry_changes SET seen_by = acting_user(), seen_at = CURRENT_TIMESTAMP WHERE change_id = ? AND seen_at IS NULL", (int(change_id),)).rowcount
    return (True, "Marked seen.") if n else (False, "Already seen, or not found.")
