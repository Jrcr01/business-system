"""The accounts-payable waterfall (owner, 2026-10-09: "all invoices must be recorded right away so we can have live visibility on the payables").

Each peso we will pay sits at the MOST ADVANCED step it has reached and shows ONCE, on that step's date:

    ordered, not delivered (open PO)   expected pay date   committed  -- the buying view / day check only, never "owed"
    delivered, SI not recorded         expected pay date   owed (an estimate, at the PO price)
    SI recorded, no check covers it    its due date        owed
    check not cleared                  its check date      owed      (a check never reduces what we owe; clearing does)
    cleared by the bank                --                  paid

The schedule of payments (the paying view) always carried the checks, bank loans and cards; this module adds what it never had: SIs no check
covers yet (stage 'invoice') and deliveries waiting for their SI (stage 'received'), in the schedule's own columns plus `stage`."""
from __future__ import annotations

import pandas as pd

from engines.common import get_conn

COLS = ["due_date", "payee_name", "track", "reference", "batch", "amount", "stage"]


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def uncovered_invoices() -> pd.DataFrame:
    """One row per SI with money no active check covers yet, on its due date (stage 'invoice').
    Batched: the batch's net total (credit memos taken off) less its active checks, spread over its open invoices with the checks covering the
    oldest first -- the same split as engines/check_date_planner.open_items, but every batch that isn't cancelled (a locked batch that still owes
    counts too). Unbatched: the invoice's full amount (not paid, not cancelled, not paid by card, not an opening-balance line)."""
    rows = []
    for b, parts in _batches():
        batch = "Draft batch" if str(b.batch or "").startswith("DRAFT-") else b.batch
        for p in parts:
            rows.append((p["due"], b.supplier, "Supplier", f"SI {p['doc_ref']} · no check yet", batch, p["amount"], "invoice"))
    loose = _read("""SELECT i.due_date, c.creditor_name AS supplier, i.doc_ref, i.amount FROM invoices i JOIN creditors c ON c.creditor_id = i.creditor_id
                     WHERE i.statement_id IS NULL AND i.status NOT IN ('Cancelled', 'Paid') AND i.is_opening_balance = 0 AND i.paid_via_card_id IS NULL
                       AND i.amount > 0.005""")
    for r in loose.itertuples():
        rows.append((str(r.due_date)[:10], r.supplier, "Supplier", f"SI {r.doc_ref} · no check yet", "Not in a batch", round(float(r.amount), 2), "invoice"))
    return pd.DataFrame(rows, columns=COLS)


def uncovered_by_batch(statement_id: int) -> list[dict]:
    """One batch's SIs that no check covers yet: [{'doc_ref', 'due', 'amount'}] (the same split as the schedule)."""
    return next((parts for b, parts in _batches(int(statement_id))), [])


def _batches(statement_id: int | None = None):
    """(batch row, [{'doc_ref', 'due', 'amount'}]) for each batch still owing more than its active checks cover."""
    where, params = ("AND b.statement_id = ?", (int(statement_id),)) if statement_id is not None else ("", ())
    batches = _read(f"""SELECT b.statement_id, b.statement_ref AS batch, cr.creditor_name AS supplier, b.net_total,
                              COALESCE((SELECT SUM(c.amount) FROM checks_issued c WHERE c.statement_id = b.statement_id AND c.status = 'Active'), 0) AS checks
                       FROM v_batch_net_totals b JOIN order_statements os ON os.statement_id = b.statement_id
                       JOIN creditors cr ON cr.creditor_id = b.creditor_id
                       WHERE os.status != 'Cancelled' {where}""", params)
    for b in batches.itertuples():
        remaining = round(float(b.net_total or 0) - float(b.checks or 0), 2)
        if remaining < 0.01:
            continue
        inv = _read("""SELECT i.doc_ref, i.due_date,
                              i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a WHERE a.invoice_id = i.invoice_id), 0) AS open_amount
                       FROM invoices i WHERE i.statement_id = ? AND i.status NOT IN ('Cancelled', 'Paid') ORDER BY i.due_date, i.invoice_id""", (int(b.statement_id),))
        inv = inv[inv["open_amount"] > 0.005].reset_index(drop=True)
        if inv.empty:
            continue
        amounts = [round(float(x), 2) for x in inv["open_amount"]]
        extra = round(sum(amounts) - remaining, 2)
        i = 0
        while extra > 0.005 and i < len(amounts):              # what checks (or credit memos) already cover comes off the oldest invoices first
            cut = min(amounts[i], extra)
            amounts[i] = round(amounts[i] - cut, 2)
            extra = round(extra - cut, 2)
            i += 1
        if extra < -0.005:                                     # the batch owes more than its open invoices show: on the last one
            amounts[-1] = round(amounts[-1] - extra, 2)
        yield b, [{"doc_ref": r.doc_ref, "due": str(r.due_date)[:10], "amount": amt} for r, amt in zip(inv.itertuples(), amounts) if amt >= 0.01]


def received_not_invoiced() -> pd.DataFrame:
    """Goods delivered against a purchase order whose SI is not recorded yet, at the PO price, on the order's expected pay date (stage 'received')."""
    df = _read("""SELECT p.po_no, c.creditor_name AS supplier, p.expected_pay_date AS due_date,
                         SUM(l.est_amount * MAX(l.qty_received - COALESCE(l.qty_invoiced, l.qty_received), 0) / l.qty) AS amount
                  FROM purchase_orders p JOIN creditors c ON c.creditor_id = p.creditor_id JOIN purchase_order_lines l ON l.po_id = p.po_id
                  WHERE p.status IN ('ORDERED', 'PARTIAL', 'RECEIVED') AND p.expected_pay_date IS NOT NULL AND l.qty > 0
                  GROUP BY p.po_id""")
    rows = [(str(r.due_date)[:10], r.supplier, "Supplier", f"{r.po_no} · received, no SI yet", None, round(float(r.amount), 2), "received")
            for r in df.itertuples() if float(r.amount or 0) > 0.004]
    return pd.DataFrame(rows, columns=COLS)


def owed_extra() -> pd.DataFrame:
    """What the schedule of payments adds to its checks, bank loans and cards: SIs no check covers yet, and deliveries with no SI yet."""
    parts = [x for x in (uncovered_invoices(), received_not_invoiced()) if not x.empty]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
