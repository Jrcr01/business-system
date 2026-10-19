"""'Possible duplicates' scan across ALL suppliers and batches, including the migrated ones (2026-09-21).

The save-time pop-ups only look at what is being added right now. This looks at everything already on
file, so a double entry that slipped through -- or that was approved with "continue anyway" -- still
turns up. Read-only apart from remembering which findings the user dismissed as "not a duplicate".
"""
import os
import sqlite3

import pandas as pd

from engines.duplicate_service import normalize_doc_ref
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
COLUMNS = ["dup_key", "kind", "supplier", "detail", "amount", "where_found", "count"]


def _php(v) -> str:
    return f"PHP {float(v):,.2f}"


def _findings() -> list[dict]:
    with get_conn(DB_PATH) as conn:
        inv = pd.read_sql_query(
            """SELECT i.invoice_id, i.creditor_id, cr.creditor_name AS supplier, i.doc_ref, i.invoice_date, i.gross_amount, os.statement_ref
               FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id LEFT JOIN order_statements os ON os.statement_id = i.statement_id
               WHERE i.status != 'Cancelled' AND i.is_opening_balance = 0""", conn)
        bat = pd.read_sql_query(
            """SELECT os.statement_id, os.creditor_id, cr.creditor_name AS supplier, os.statement_ref, os.total_amount
               FROM order_statements os JOIN creditors cr ON cr.creditor_id = os.creditor_id
               WHERE os.status IN ('Draft', 'Issued') AND os.total_amount > 0""", conn)
    out = []
    flagged_invoice_ids: set[int] = set()
    if not inv.empty:
        inv["norm"] = inv["doc_ref"].map(normalize_doc_ref)
        twice = inv[inv.duplicated(["creditor_id", "norm"], keep=False)]           # only the keys seen twice or more (speed, 2026-10-19)
        for (cid, norm), grp in twice.groupby(["creditor_id", "norm"]):
            if len(grp) < 2:
                continue
            flagged_invoice_ids.update(grp["invoice_id"])
            out.append({
                "dup_key": f"inv:{cid}:{norm}", "kind": "Same invoice number", "supplier": grp["supplier"].iloc[0],
                "detail": "; ".join(f"{r.doc_ref} ({_php(r.gross_amount)}, {r.invoice_date})" for r in grp.itertuples()),
                "amount": float(grp["gross_amount"].sum()), "where_found": ", ".join(sorted({str(x) for x in grp["statement_ref"]})), "count": len(grp),
            })
        rest = inv[~inv["invoice_id"].isin(flagged_invoice_ids)]
        rest = rest[rest.duplicated(["creditor_id", "invoice_date", "gross_amount"], keep=False)]
        for (cid, day, amount), grp in rest.groupby(["creditor_id", "invoice_date", "gross_amount"]):
            if len(grp) < 2:
                continue
            out.append({
                "dup_key": f"amt:{cid}:{day}:{round(float(amount), 2)}", "kind": "Same amount and date", "supplier": grp["supplier"].iloc[0],
                "detail": "; ".join(str(r.doc_ref) for r in grp.itertuples()) + f" -- all {_php(amount)} on {day}",
                "amount": float(grp["gross_amount"].sum()), "where_found": ", ".join(sorted({str(x) for x in grp["statement_ref"]})), "count": len(grp),
            })
    if not bat.empty:
        bat["total_r"] = bat["total_amount"].round(2)
        for (cid, total), grp in bat[bat.duplicated(["creditor_id", "total_r"], keep=False)].groupby(["creditor_id", "total_r"]):
            if len(grp) < 2:
                continue
            out.append({
                "dup_key": f"batch:{cid}:{total}", "kind": "Same batch total", "supplier": grp["supplier"].iloc[0],
                "detail": f"{len(grp)} batches with the same total of {_php(total)}" + (" (includes a migrated batch)" if grp["statement_ref"].str.startswith("LEGACY-").any() else ""),
                "amount": float(total), "where_found": ", ".join(grp["statement_ref"]), "count": len(grp),
            })
    return out


def scan_possible_duplicates(include_dismissed: bool = False) -> pd.DataFrame:
    df = pd.DataFrame(_findings(), columns=COLUMNS)
    with get_conn(DB_PATH) as conn:
        dismissed = {r[0] for r in conn.execute("SELECT dup_key FROM duplicate_dismissals")}
    df["dismissed"] = df["dup_key"].isin(dismissed)
    if not include_dismissed:
        df = df[~df["dismissed"]]
    return df.sort_values(["kind", "supplier"]).reset_index(drop=True)


def dismiss(keys: list[str], note: str = "") -> int:
    with get_conn(DB_PATH) as conn:
        conn.executemany("INSERT OR IGNORE INTO duplicate_dismissals (dup_key, note) VALUES (?, ?)", [(k, note or None) for k in keys])
        conn.commit()
    return len(keys)


def restore(keys: list[str]) -> int:
    with get_conn(DB_PATH) as conn:
        conn.executemany("DELETE FROM duplicate_dismissals WHERE dup_key = ?", [(k,) for k in keys])
        conn.commit()
    return len(keys)
