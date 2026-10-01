"""Checks typed into an order statement's Issue-a-check grid but not parked yet (2026-09-27, per user: "as long as it does not have
a check number assigned then it is not final"). The typed rows are remembered in the database, per batch, so opening another
order statement -- or refreshing the page -- never loses them. Cleared when the checks are parked (or the batch changes under them)."""
from __future__ import annotations

import json
import os
import sqlite3
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def save_park_draft(statement_id: int, mode: str, count: int, balance: float, invoice_ids: list[int], rows: list[tuple[str, float]]) -> None:
    payload = json.dumps({"mode": mode, "count": int(count), "balance": round(float(balance), 2), "invoice_ids": [int(i) for i in invoice_ids],
                          "rows": [[str(d)[:10], round(float(a), 2)] for d, a in rows]})
    with get_conn(DB_PATH) as conn:
        conn.execute(
            """INSERT INTO park_drafts (statement_id, payload) VALUES (?, ?)
               ON CONFLICT(statement_id) DO UPDATE SET payload = excluded.payload, updated_at = CURRENT_TIMESTAMP""",
            (int(statement_id), payload),
        )
        conn.commit()


def load_park_draft(statement_id: int) -> dict | None:
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT payload FROM park_drafts WHERE statement_id = ?", (int(statement_id),)).fetchone()
    if not row:
        return None
    d = json.loads(row[0])
    d["invoice_ids"] = tuple(d["invoice_ids"])
    d["rows"] = [(r[0], float(r[1])) for r in d["rows"]]
    return d


def clear_park_draft(statement_id: int) -> None:
    with get_conn(DB_PATH) as conn:
        conn.execute("DELETE FROM park_drafts WHERE statement_id = ?", (int(statement_id),))
        conn.commit()


def list_park_drafts() -> list[dict]:
    """Every batch that has typed-but-not-parked checks: [{'statement_id', 'label', 'n', 'total'}], newest first."""
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT d.statement_id, d.payload, os.statement_ref, cr.creditor_name,
                      COALESCE((SELECT v.net_total FROM v_batch_net_totals v WHERE v.statement_id = d.statement_id), 0) AS net,
                      COALESCE((SELECT v.net_total FROM v_batch_net_totals v WHERE v.statement_id = d.statement_id), 0)
                        - COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = d.statement_id AND k.status = 'Active'), 0) AS remaining
               FROM park_drafts d
               JOIN order_statements os ON os.statement_id = d.statement_id JOIN creditors cr ON cr.creditor_id = os.creditor_id
               WHERE os.status != 'Cancelled' ORDER BY d.updated_at DESC"""
        ).fetchall()
        # A draft for a batch with nothing left to pay (its checks were recorded another way, e.g. as handwritten checks) can never be parked: drop it
        # instead of nagging forever.
        stale = [r[0] for r in rows if r[4] > 0.005 and r[5] <= 0.005]
        for sid in stale:
            conn.execute("DELETE FROM park_drafts WHERE statement_id = ?", (sid,))
        conn.commit()
    out = []
    for sid, payload, ref, name, net, remaining in rows:
        if net > 0.005 and remaining <= 0.005:
            continue
        r = json.loads(payload)["rows"]
        out.append({"statement_id": sid, "label": f"{ref} · {name}", "n": len(r), "total": sum(float(x[1]) for x in r)})
    return out
