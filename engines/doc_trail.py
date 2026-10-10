"""Related documents (Phase 8, owner 2026-10-09; SAP Business One's relationship map): from any document, the chain it belongs to, in the order the money
moves. Supplier side: purchase order -> goods received -> sales invoice -> batch -> check -> bank line. Lender side: loan note -> payment -> check -> bank line.

trail(kind, id) walks up (towards the order) and down (towards the bank) from the document, never sideways, so an invoice shows its own PO and its batch's
checks, not its batch's other invoices. Returns [{'label', 'docs': [{'title', 'sub', 'url', 'me', 'off'}]}], one column per step; a step with nothing
yet is {'docs': []} (drawn as "none yet").

Owner, 2026-10-11 (the map of a batch with 12 sales invoices): a step with more than 3 documents is drawn as ONE summary card that opens its list; each step
shows its count and total; a line on top says where the chain stands and what is next (trail_summary); a batch made without a purchase order says
"No PO (allowed)" instead of "none yet"; an unpaid document is Not due / Due soon / Overdue by its due date; the batch card names its supplier."""
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


GROUP_OVER = 3            # a step with more documents than this is drawn as one summary card
DUE_SOON_DAYS = 7


def _d(i, title, status, done, amount, date_label, day, url, off=False, tone="") -> dict:
    return {"id": i, "title": title, "status": status, "done": done, "amount": float(amount or 0), "date_label": date_label, "date": str(day or "")[:10],
            "url": url, "off": off, "tone": tone}


def _due(day) -> tuple[str, str]:
    """An unpaid document by its due date: ('Overdue', 'late'), ('Due soon', 'soon') within a week, ('Not due', 'quiet') otherwise."""
    from datetime import date

    from engines.clock import manila_today
    try:
        left = (date.fromisoformat(str(day)[:10]) - manila_today()).days
    except (TypeError, ValueError):
        return "Unpaid", ""
    return ("Overdue", "late") if left < 0 else (("Due soon", "soon") if left <= DUE_SOON_DAYS else ("Not due", "quiet"))


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
            ref = str(r[4] or "")                                # the DR, or the SI when the goods came with it
            out.append(_d(r[0], "Goods received" + (f" ({ref if ref.upper().startswith('DR') else 'SI ' + ref})" if ref else ""), "Received", True, r[3], "Received", r[2], f"/purchase-orders/{r[1]}/"))
    elif kind == "invoice":
        for r in conn.execute(f"SELECT invoice_id, doc_ref, dr_no, amount, statement_id, status, due_date FROM invoices WHERE invoice_id IN ({marks}) ORDER BY invoice_date", p):
            dr_only = str(r[1] or "").upper().startswith("DR ")   # the supplier gave only a DR: that is the document (owner, 2026-10-11)
            status, tone = (r[5], "") if r[5] in ("Paid", "Cancelled") else _due(r[6])
            if r[5] == "Partially Paid":
                status = f"Partly paid · {status.lower()}"
            out.append(_d(r[0], (str(r[1]) if dr_only else f"SI {r[1]}" + (f" · DR {r[2]}" if r[2] else "")), status, r[5] == "Paid", r[3], "Due", r[6],
                          f"/order-statements/{r[4]}/" if r[4] else "", r[5] == "Cancelled", tone))
    elif kind == "statement":
        for r in conn.execute(f"""SELECT s.statement_id, s.statement_ref, s.total_amount, s.status, s.created_at, s.is_locked, c.creditor_name
                                  FROM order_statements s LEFT JOIN creditors c ON c.creditor_id = s.creditor_id WHERE s.statement_id IN ({marks})""", p):
            name = str(r[6] or "")
            who = (" · " + (name if len(name) <= 26 else name[:25].rstrip() + "…")) if name else ""      # whose batch it is, on the card
            out.append(_d(r[0], ("Draft batch" if str(r[1] or "").startswith("DRAFT-") else str(r[1])) + who, r[3] or ("Locked" if r[5] else "Open"), r[3] == "Paid", r[2], "Made",
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
            word, tone = ("", "") if r[5] else _due(r[2])
            status = "Paid" if r[5] else (("Check parked" if r[4] else "No check yet") + (f" · {word.lower()}" if tone in ("late", "soon") else ""))
            out.append(_d(r[0], f"Payment {us_date(r[2])}", status, bool(r[5]), r[3], "Due", r[2], f"/lenders/{r[1]}/", False, tone))
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
        no_po = chain is SUPPLIER and kind != "po" and not found.get("po")       # a batch or document made without a purchase order: allowed ("No PO" rule)
        for k in chain:
            docs = _docs(conn, k, found.get(k, set()), (kind, int(doc_id)))
            live = [d for d in docs if not d["off"]]
            dates = sorted(d["date"] for d in live if d["date"] and not d["done"]) or sorted(d["date"] for d in live if d["date"])
            grouped = len(docs) > GROUP_OVER
            cols.append({"key": k, "label": "This " + LABELS[k].lower() if k == kind else LABELS[k], "icon": ICONS[k], "docs": docs[:limit], "more": max(0, len(docs) - limit),
                         "done": bool(docs) and all(d["done"] or d["off"] for d in docs), "has": bool(docs),
                         "count": len(live), "total": round(sum(d["amount"] for d in live), 2), "done_count": sum(1 for d in live if d["done"]),
                         "earliest": dates[0] if dates else "", "date_label": (live[0]["date_label"] if live else ""), "name": LABELS[k].lower(),
                         "grouped": grouped, "me_doc": next((d for d in docs if d["me"]), None) if grouped else None,
                         "rest": [d for d in docs[:limit] if not d["me"]] if grouped else [],
                         "late": sum(1 for d in live if d["tone"] == "late"),
                         "empty_note": "No PO (allowed)" if no_po and k in ("po", "receipt") else "none yet"})
    return cols


def _peso(x: float) -> str:
    return f"₱{x:,.2f}"


def trail_summary(kind: str, cols: list[dict]) -> str:
    """One sentence on where the chain stands and what is next, read from the columns of trail(): e.g. "Draft batch · Battery Depot · ₱781,821.19 owed ·
    no checks yet → Next: park checks." '' when there is nothing to say."""
    by = {c["key"]: c for c in cols}

    def has(k):
        return k in by and by[k]["count"] > 0

    if "loan" in by:
        if not has("loan"):
            return ""
        note = next((d for d in by["loan"]["docs"] if not d["off"]), None)
        pay = by.get("payment")
        if not pay or not pay["count"]:
            return f"{note['title']} · {_peso(note['amount'])} · no payments scheduled yet."
        open_ = sorted((d for d in pay["docs"] if not d["done"] and not d["off"]), key=lambda d: d["date"])
        head = f"{note['title']} · {pay['count']} payment{'s' if pay['count'] != 1 else ''}, {pay['done_count']} paid"
        return head + (f" → Next: payment due {us_date(open_[0]['date'])}." if open_ else " → All paid.")
    lead = ""
    if has("statement"):
        b = next(d for d in by["statement"]["docs"] if not d["off"])
        lead = f"{b['title']} · {_peso(by['invoice']['total'] if has('invoice') else b['amount'])} owed"
    elif has("invoice"):
        n = by["invoice"]["count"]
        lead = f"{n} document{'s' if n != 1 else ''} · {_peso(by['invoice']['total'])} owed"
    elif has("po"):
        lead = next(d for d in by["po"]["docs"] if not d["off"])["title"]
    if has("check"):
        checks = [d for d in by["check"]["docs"] if not d["off"]]
        cleared = [d for d in checks if d["done"]]
        parked = [d for d in checks if d["status"] == "Parked"]
        waiting = [d for d in checks if not d["done"] and d["status"] != "Parked"]
        text = f"{len(checks)} check{'s' if len(checks) != 1 else ''} {_peso(sum(d['amount'] for d in checks))}"
        if cleared:
            text += f" · {len(cleared)} cleared {_peso(sum(d['amount'] for d in cleared))}"
        if len(cleared) == len(checks):
            nxt = "Paid: every check cleared the bank."
        elif parked:
            nxt = f"Next: write {len(parked)} parked check{'s' if len(parked) != 1 else ''}."
        else:
            nxt = f"Next: wait for the bank on {len(waiting)} check{'s' if len(waiting) != 1 else ''}."
        return " · ".join(x for x in (lead, text) if x) + " → " + nxt
    if "check" not in by and (has("statement") or has("invoice")):            # a person kept from check details: no word about checks
        return lead + "."
    if has("statement"):
        late = by["invoice"]["late"] if has("invoice") else 0
        return lead + " · no checks yet" + (f" · {late} overdue" if late else "") + " → Next: park checks."
    if has("invoice"):
        return lead + " · not in a batch yet → Next: put " + ("them" if by["invoice"]["count"] != 1 else "it") + " in a batch."
    if has("receipt"):
        return (lead + " · " if lead else "") + "goods received · no document yet → Next: record the SI or DR."
    if has("po"):
        po = next(d for d in by["po"]["docs"] if not d["off"])
        nxt = {"Draft": "send it to the supplier", "Ordered": "receive the goods", "Partial": "receive the rest"}.get(po["status"], "")
        word = {"Draft": "a draft", "Ordered": "ordered · not delivered yet", "Partial": "partly received"}.get(po["status"], po["status"].lower())
        return f"{po['title']} · {word}" + (f" → Next: {nxt}." if nxt else ".")
    return ""


def trail_for(role: str | None, kind: str, doc_id: int) -> list[dict]:
    """trail() for a page: empty if it fails (the panel must never break the page); a role kept from check details doesn't see the check and bank steps."""
    from engines.permissions import hide_check_details
    try:
        cols = trail(kind, doc_id)
    except Exception:                                   # noqa: BLE001
        return []
    if hide_check_details(role):
        cols = [c for c in cols if c["key"] not in ("check", "bank")]
    return cols
