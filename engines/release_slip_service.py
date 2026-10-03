"""The check release slip (2026-10-03). When a batch's statement is printed for hand-over (supplier copy, filing copy, finance copy) it carries a slip number
(RS-2026-0001). The supplier signs a copy when they collect their checks; whoever hands the checks over (the Releaser) then records the release BY THAT NUMBER: all the
checks on the slip are marked released at once (engines/pdc_release_service.py), with who received them and when. The owner, working remotely, sees from the slip list which
slips are still waiting to be collected.

  * create_slip(statement_id)            -- the slip for this batch's checks (the same open slip is reused for a reprint of an unchanged batch)
  * open_slips() / recent_slips()        -- printed, not yet collected / collected lately
  * release_slip(slip_id, received_by, date) -- the supplier collected: marks every check on the slip released (all or nothing)
  * waiting_over(days)                   -- slips still not collected after this many days (Home notice)"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.db_engine import next_doc_number
from engines.pdc_release_service import mark_released
from engines.permissions import require as require_permission

SERIES = "RS"


def slip_label(year: int, number: int) -> str:
    return f"RS-{int(year)}-{int(number):04d}"


def _batch_checks(conn, statement_id: int) -> list:
    return [r[0] for r in conn.execute("SELECT check_id FROM checks_issued WHERE statement_id = ? AND status = 'Active' AND released_date IS NULL AND is_handwritten = 0 ORDER BY check_id",
                                       (int(statement_id),)).fetchall()]


def create_slip(statement_id: int) -> tuple[bool, str, dict | None]:
    """Returns (ok, message, {'slip_id', 'slip_no', 'checks', 'total'}). The batch's active, not yet released checks go on the slip. An open slip with exactly those checks is
    reused, so printing the statement again does not use up a number."""
    ok, why = require_permission("check.release")
    if not ok:
        return False, why, None
    with write_tx() as conn:
        batch = conn.execute("SELECT creditor_id FROM order_statements WHERE statement_id = ?", (int(statement_id),)).fetchone()
        if not batch:
            return False, "Batch not found.", None
        ids = _batch_checks(conn, statement_id)
        if not ids:
            return False, "This batch has no check waiting to be handed over, so there is no slip to make.", None
        for slip_id, slip_no in conn.execute("SELECT slip_id, slip_no FROM release_slips WHERE statement_id = ? AND released_at IS NULL ORDER BY slip_id DESC", (int(statement_id),)).fetchall():
            if sorted(r[0] for r in conn.execute("SELECT check_id FROM release_slip_checks WHERE slip_id = ?", (slip_id,)).fetchall()) == ids:
                return True, f"Slip {slip_no}.", _summary(conn, slip_id)
        year = manila_today().year
        number = next_doc_number(conn, SERIES, year)
        total = conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE check_id IN ({','.join('?' * len(ids))})", ids).fetchone()[0]
        slip_no = slip_label(year, number)
        slip_id = conn.execute("INSERT INTO release_slips (slip_no, statement_id, creditor_id, check_count, total, printed_by, printed_at) VALUES (?, ?, ?, ?, ?, acting_user(), CURRENT_TIMESTAMP) RETURNING slip_id",
                               (slip_no, int(statement_id), batch[0], len(ids), round(float(total), 2))).fetchone()[0]
        conn.executemany("INSERT INTO release_slip_checks (slip_id, check_id) VALUES (?, ?)", [(slip_id, c) for c in ids])
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'RELEASE_SLIP_PRINTED', 'release_slips', ?, ?)",
                     (slip_id, f"{slip_no}: {len(ids)} check(s), {float(total):,.2f}"))
        return True, f"Slip {slip_no}.", _summary(conn, slip_id)


def _summary(conn, slip_id: int) -> dict:
    r = conn.execute("SELECT slip_id, slip_no, check_count, total FROM release_slips WHERE slip_id = ?", (slip_id,)).fetchone()
    return {"slip_id": r[0], "slip_no": r[1], "checks": r[2], "total": r[3]}


def open_slips() -> pd.DataFrame:
    """Slips printed and not yet collected: slip_id, slip_no, supplier, statement, checks, total, printed_on, printed_by, days_waiting. Oldest first."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT s.slip_id, s.slip_no, c.creditor_name AS supplier, o.statement_ref AS statement, s.check_count AS checks, s.total, DATE(s.printed_at) AS printed_on, s.printed_by
               FROM release_slips s JOIN creditors c ON c.creditor_id = s.creditor_id JOIN order_statements o ON o.statement_id = s.statement_id
               WHERE s.released_at IS NULL ORDER BY s.slip_id""", conn)
    today = manila_today()
    df["days_waiting"] = [max((today - date.fromisoformat(str(d)[:10])).days, 0) for d in df["printed_on"]]
    return df


def recent_slips(days: int = 30) -> pd.DataFrame:
    since = manila_today() - timedelta(days=days)
    with get_conn() as conn:
        return pd.read_sql_query(
            """SELECT s.slip_id, s.slip_no, c.creditor_name AS supplier, s.check_count AS checks, s.total, DATE(s.released_at) AS released_on, s.received_by, s.released_by
               FROM release_slips s JOIN creditors c ON c.creditor_id = s.creditor_id WHERE s.released_at IS NOT NULL AND DATE(s.released_at) >= ? ORDER BY s.slip_id DESC""",
            conn, params=(str(since),))


def waiting_over(days: int = 7) -> int:
    df = open_slips()
    return int((df["days_waiting"] > days).sum()) if not df.empty else 0


def release_slip(slip_id: int, received_by: str, released_date) -> tuple[bool, str]:
    """The supplier collected: every check on the slip is marked released with who received them and the date. All or nothing; a check that was never printed stops it
    (a check cannot be handed over before it exists on paper)."""
    ok, why = require_permission("check.release")
    if not ok:
        return False, why
    who = " ".join((received_by or "").split())
    if not who:
        return False, "Type who received the checks."
    with get_conn() as conn:
        row = conn.execute("SELECT slip_no, released_at FROM release_slips WHERE slip_id = ?", (int(slip_id),)).fetchone()
        if not row:
            return False, "That slip is not on file."
        if row[1]:
            return False, f"Slip {row[0]} was already collected."
        ids = [r[0] for r in conn.execute("SELECT k.check_id FROM release_slip_checks k JOIN checks_issued c ON c.check_id = k.check_id WHERE k.slip_id = ? AND c.status = 'Active' "
                                          "AND c.released_date IS NULL", (int(slip_id),)).fetchall()]
    if not ids:
        return False, f"Nothing on slip {row[0]} is left to release (its checks were cancelled or already released)."
    ok, msg = mark_released(ids, released_date, who)
    if not ok:
        return False, msg
    with write_tx() as conn:
        conn.execute("UPDATE release_slips SET released_at = CURRENT_TIMESTAMP, received_by = ?, released_by = acting_user() WHERE slip_id = ?", (who, int(slip_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'RELEASE_SLIP_COLLECTED', 'release_slips', ?, ?)",
                     (int(slip_id), f"{row[0]}: {len(ids)} check(s) received by {who}"))
    return True, f"Slip {row[0]}: {len(ids)} check(s) marked released to {who}."
