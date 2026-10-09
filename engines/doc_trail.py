"""Related documents (Phase 8, owner 2026-10-09; SAP Business One's relationship map): from any document, the chain it belongs to, in the order the money
moves. Supplier side: purchase order -> goods received -> sales invoice -> batch -> check -> bank line. Lender side: loan note -> payment -> check -> bank line.

trail(kind, id) walks up (towards the order) and down (towards the bank) from the document, never sideways, so an invoice shows its own PO and its batch's
checks, not its batch's other invoices. Returns [{'label', 'docs': [{'title', 'sub', 'url', 'me', 'off'}]}], one column per step; a step with nothing
yet is {'docs': []} (drawn as "none yet")."""
from __future__ import annotations

from engines.common import get_conn
from engines.common import us_date

SUPPLIER = ["po", "receipt", "invoice", "statement", "check", "bank"]
LENDER = ["loan", "payment", "check", "bank"]
LABELS = {"po": "Purchase order", "receipt": "Goods received", "invoice": "Sales invoice", "statement": "Batch", "check": "Check", "bank": "Bank line",
          "loan": "Loan note", "payment": "Payment"}


def _ids(conn, sql: str, ids: set) -> set:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(sql.format(marks=marks), tuple(ids)) if r[0] is not None}


# (from, to) -> SQL giving the `to` ids for a set of `from` ids
_STEP = {
    ("po", "receipt"): "SELECT receipt_id FROM po_receipts WHERE po_id IN ({marks})",
    ("receipt", "po"): "SELECT po_id FROM po_receipts WHERE receipt_id IN ({marks})",
    ("receipt", "invoice"): "SELECT invoice_id FROM po_receipts WHERE receipt_id IN ({marks})",
    ("invoice", "receipt"): "SELECT receipt_id FROM po_receipts WHERE invoice_id IN ({marks})",
    ("invoice", "statement"): "SELECT statement_id FROM invoices WHERE invoice_id IN ({marks})",
    ("statement", "invoice"): "SELECT invoice_id FROM invoices WHERE statement_id IN ({marks})",
    ("statement", "check"): "SELECT check_id FROM checks_issued WHERE statement_id IN ({marks})",
    ("check", "statement"): "SELECT statement_id FROM checks_issued WHERE check_id IN ({marks})",
    ("check", "bank"): "SELECT txn_id FROM bank_transactions WHERE matched_check_id IN ({marks}) UNION SELECT bank_txn_id FROM checks_issued WHERE check_id IN ({marks})",
    ("bank", "check"): "SELECT matched_check_id FROM bank_transactions WHERE txn_id IN ({marks}) UNION SELECT check_id FROM checks_issued WHERE bank_txn_id IN ({marks})",
    ("loan", "payment"): "SELECT installment_id FROM loan_installments WHERE loan_note_id IN ({marks})",
    ("payment", "loan"): "SELECT loan_note_id FROM loan_installments WHERE installment_id IN ({marks})",
    ("payment", "check"): "SELECT check_id FROM loan_installments WHERE installment_id IN ({marks})",
    ("check", "payment"): "SELECT installment_id FROM loan_installments WHERE check_id IN ({marks})",
}


def _step(conn, a: str, b: str, ids: set) -> set:
    sql = _STEP[(a, b)]
    if sql.count("{marks}") == 2:                       # the UNION ones take the ids twice
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        return {r[0] for r in conn.execute(sql.format(marks=marks), tuple(ids) * 2) if r[0] is not None}
    return _ids(conn, sql, ids)


ICONS = {"po": "shopping_cart", "receipt": "inventory_2", "invoice": "receipt_long", "statement": "description", "check": "payments", "bank": "account_balance",
         "loan": "handshake", "payment": "event"}


def _d(i, title, status, done, amount, date_label, day, url, off=False) -> dict:
    return {"id": i, "title": title, "status": status, "done": done, "amount": float(amount or 0), "date_label": date_label, "date": str(day or "")[:10],
            "url": url, "off": off}


def _docs(conn, kind: str, ids: set, me: tuple) -> list[dict]:
    """One card per document: title, status (done = this step is finished), amount, a date."""
    out = []
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    p = tuple(ids)
    if kind == "po":
        for r in conn.execute(f"""SELECT po_id, po_no, order_date, status, (SELECT SUM(est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id)
                                  FROM purchase_orders p WHERE po_id IN ({marks}) ORDER BY order_date""", p):
            out.append(_d(r[0], r[1] or f"Draft PO #{r[0]}", str(r[3]).title(), r[3] in ("RECEIVED", "CLOSED"), r[4], "Ordered", r[2], f"/purchase-orders/{r[0]}/", r[3] == "CANCELLED"))
    elif kind == "receipt":
        for r in conn.execute(f"SELECT receipt_id, po_id, received_date, COALESCE(actual_amount, est_amount), supplier_invoice_no FROM po_receipts WHERE receipt_id IN ({marks}) ORDER BY received_date", p):
            out.append(_d(r[0], "Goods received" + (f" (SI {r[4]})" if r[4] else ""), "Received", True, r[3], "Received", r[2], f"/purchase-orders/{r[1]}/"))
    elif kind == "invoice":
        for r in conn.execute(f"SELECT invoice_id, doc_ref, dr_no, amount, statement_id, status, due_date FROM invoices WHERE invoice_id IN ({marks}) ORDER BY invoice_date", p):
            out.append(_d(r[0], f"SI {r[1]}" + (f" · DR {r[2]}" if r[2] else ""), r[5], r[5] == "Paid", r[3], "Due", r[6], f"/order-statements/{r[4]}/" if r[4] else "", r[5] == "Cancelled"))
    elif kind == "statement":
        for r in conn.execute(f"SELECT statement_id, statement_ref, total_amount, status, created_at, is_locked FROM order_statements WHERE statement_id IN ({marks})", p):
            out.append(_d(r[0], "Draft batch" if str(r[1] or "").startswith("DRAFT-") else r[1], r[3] or ("Locked" if r[5] else "Open"), r[3] == "Paid", r[2], "Made",
                          r[4], f"/order-statements/{r[0]}/", r[3] == "Cancelled"))
    elif kind == "check":
        for r in conn.execute(f"""SELECT check_id, check_number, check_date, amount, status, statement_id, loan_note_id, released_date,
                                         COALESCE(bank_cleared_date, manual_cleared_at, settled_outside_at), printed_at FROM checks_issued WHERE check_id IN ({marks})
                                  ORDER BY check_date, check_id""", p):
            state = r[4] if r[4] != "Active" else ("Cleared" if r[8] else ("Released" if r[7] else ("Written" if r[1] or r[9] else "Parked")))
            url = f"/order-statements/{r[5]}/?tab=checks" if r[5] else (f"/lenders/{r[6]}/?tab=checks" if r[6] else "")
            out.append(_d(r[0], f"Check {r[1]}" if r[1] else "Check (no number yet)", state, state == "Cleared", r[3], "Dated", r[2], url, r[4] != "Active"))
    elif kind == "bank":
        for r in conn.execute(f"SELECT txn_id, COALESCE(posting_date, txn_date), debit, match_status, check_number FROM bank_transactions WHERE txn_id IN ({marks}) ORDER BY 2", p):
            out.append(_d(r[0], "Bank line" + (f" · check {r[4]}" if r[4] else ""), "Cleared", True, r[2], "Posted", r[1], "/bank-cash/?tab=look"))
    elif kind == "loan":
        for r in conn.execute(f"SELECT loan_note_id, note_ref, issue_date, principal_amount, status FROM loan_notes WHERE loan_note_id IN ({marks})", p):
            out.append(_d(r[0], r[1] or "Loan note", r[4], r[4] == "Settled", r[3], "Loan date", r[2], f"/lenders/{r[0]}/", r[4] == "Cancelled"))
    elif kind == "payment":
        for r in conn.execute(f"""SELECT i.installment_id, i.loan_note_id, i.due_date, i.principal + i.interest, i.check_id,
                                         (SELECT COALESCE(bank_cleared_date, manual_cleared_at, settled_outside_at) FROM checks_issued c WHERE c.check_id = i.check_id)
                                  FROM loan_installments i WHERE installment_id IN ({marks}) ORDER BY due_date""", p):
            out.append(_d(r[0], f"Payment {us_date(r[2])}", "Paid" if r[5] else ("Check parked" if r[4] else "No check yet"), bool(r[5]), r[3], "Due", r[2], f"/lenders/{r[1]}/"))
    for d in out:
        d["me"] = (kind, d["id"]) == me
    return out


def trail(kind: str, doc_id: int, limit: int = 12) -> list[dict]:
    """The chain of the document `kind` ('po', 'invoice', 'statement', 'check', 'loan', ...) with id `doc_id`; at most `limit` boxes per step (+ "and N more")."""
    with get_conn() as conn:
        chain = SUPPLIER
        if kind in ("loan", "payment") or (kind == "check" and conn.execute("SELECT loan_note_id FROM checks_issued WHERE check_id = ?", (doc_id,)).fetchone() not in (None, (None,))):
            chain = LENDER
        at = chain.index(kind)
        found = {kind: {int(doc_id)}}
        for i in range(at, 0, -1):                          # up, towards the order
            found[chain[i - 1]] = _step(conn, chain[i], chain[i - 1], found[chain[i]])
        for i in range(at, len(chain) - 1):                 # down, towards the bank
            found[chain[i + 1]] = _step(conn, chain[i], chain[i + 1], found[chain[i]])
        cols = []
        for k in chain:
            docs = _docs(conn, k, found.get(k, set()), (kind, int(doc_id)))
            cols.append({"label": "This " + LABELS[k].lower() if k == kind else LABELS[k], "icon": ICONS[k], "docs": docs[:limit], "more": max(0, len(docs) - limit),
                         "done": bool(docs) and all(d["done"] or d["off"] for d in docs), "has": bool(docs)})
    return cols


def trail_for(role: str | None, kind: str, doc_id: int) -> list[dict]:
    """trail() for a page: empty if it fails (the panel must never break the page); a role kept from check details doesn't see the check and bank steps."""
    from engines.permissions import hide_check_details
    try:
        cols = trail(kind, doc_id)
    except Exception:                                   # noqa: BLE001
        return []
    if hide_check_details(role):
        cols = [c for c in cols if c["label"] not in ("Check", "Bank line", "This check")]
    return cols
