"""Statement of account (owner, 2026-10-09: the one history report kept; "whenever I am asked for the history of transactions"): one dated ledger for a supplier,
a private lender, a bank loan or a credit card, with a running balance. Charges add, payments subtract; a payment counts on the day it CLEARED (engines/clearing.py:
the bank, settled outside it, or ticked) -- written checks not cleared yet are listed apart, as "waiting for the bank".

  * supplier  -- invoices (net) and credit memos; its checks
  * lender    -- each loan note's total payable on its loan date; its checks
  * bank loan -- each payment due on its due date; paid on the day it cleared (bank line, or ticked)
  * card      -- each statement's new charges and finance charge (the first statement's carried balance opens it); the payments recorded on it

ledger(kind, id, start, end) -> {'name', 'kind', 'lines': [{'day', 'doc', 'ref', 'charge', 'payment', 'balance'}], 'opening', 'charges', 'payments', 'closing',
'waiting': [{'day', 'doc', 'ref', 'amount'}]}; pdf(...) lays it out (the look of account_history_pdf_service)."""
from __future__ import annotations

import io
from datetime import date

from engines.clearing import bank_loan_cleared_sql, cleared_sql
from engines.common import get_conn

KINDS = {"supplier": "Supplier", "lender": "Lender", "bankloan": "Bank loan", "card": "Credit card"}
CLEARED_ON = "COALESCE(c.bank_cleared_date, c.manual_cleared_at, c.settled_outside_at)"


def _rows(sql: str, params: tuple) -> list[tuple]:
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def _raw(kind: str, item_id: int) -> tuple[str, list[tuple], list[tuple]]:
    """(name, [(day, doc, ref, charge, payment)], [(day, doc, ref, amount) waiting])"""
    if kind == "supplier":
        name = (_rows("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (item_id,)) or [("?",)])[0][0]
        lines = [(d, ("" if str(doc).startswith("DR ") else "SI ") + str(doc) + (f" · DR {dr}" if dr and not str(doc).startswith("DR ") else ""), ref or "", float(a or 0), 0.0)
                 for d, doc, dr, ref, a in _rows("""SELECT i.invoice_date, i.doc_ref, i.dr_no, os.statement_ref, i.amount FROM invoices i
                                                    LEFT JOIN order_statements os ON os.statement_id = i.statement_id
                                                    WHERE i.creditor_id = ? AND i.status != 'Cancelled'""", (item_id,))]
        lines += [(d, f"Credit memo {m}", ref or "", 0.0, float(a or 0))
                  for d, m, ref, a in _rows("""SELECT COALESCE(cm.memo_date, date(cm.created_at)), cm.memo_ref, os.statement_ref, cm.amount FROM credit_memos cm
                                               JOIN order_statements os ON os.statement_id = cm.statement_id WHERE os.creditor_id = ?""", (item_id,))]
        checks = _rows(f"""SELECT c.check_date, c.check_number, os.statement_ref, c.amount, {CLEARED_ON} FROM checks_issued c
                           LEFT JOIN order_statements os ON os.statement_id = c.statement_id
                           WHERE c.creditor_id = ? AND c.statement_id IS NOT NULL AND c.status = 'Active'""", (item_id,))
    elif kind == "lender":
        name = (_rows("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (item_id,)) or [("?",)])[0][0]
        lines = [(d, f"Loan note {ref or ''}".strip(), ref or "", float(a or 0), 0.0)
                 for d, ref, a in _rows("""SELECT issue_date, note_ref, total_payable FROM loan_notes
                                           WHERE creditor_id = ? AND is_lease = 0 AND status NOT IN ('Cancelled', 'Pending Setup')""", (item_id,))]
        checks = _rows(f"""SELECT c.check_date, c.check_number, n.note_ref, c.amount, {CLEARED_ON} FROM checks_issued c JOIN loan_notes n ON n.loan_note_id = c.loan_note_id
                           WHERE n.creditor_id = ? AND c.status = 'Active' AND n.status NOT IN ('Cancelled', 'Pending Setup')""", (item_id,))
    elif kind == "bankloan":
        info = _rows("SELECT bl.loan_ref, c.creditor_name FROM bank_loans bl JOIN creditors c ON c.creditor_id = bl.creditor_id WHERE bl.bank_loan_id = ?", (item_id,))
        name = f"{info[0][0]} · {info[0][1]}" if info else "?"
        rows = _rows(f"""SELECT i.due_date, i.installment_no, i.amount,
                                COALESCE((SELECT MIN(COALESCE(t.posting_date, t.txn_date)) FROM bank_transactions t WHERE t.matched_installment_id = i.installment_id), i.cleared_at)
                         FROM bank_loan_installments i JOIN bank_loans bl ON bl.bank_loan_id = i.bank_loan_id
                         WHERE i.bank_loan_id = ? AND bl.is_cancelled = 0""", (item_id,))
        lines = [(d, f"Payment #{n} due", "", float(a or 0), 0.0) for d, n, a, _ in rows]
        lines += [(paid, f"Payment #{n} paid", "", 0.0, float(a or 0)) for d, n, a, paid in rows if paid]
        return name, lines, []
    elif kind == "card":
        info = _rows("""SELECT cc.card_name || ' - ' || cc.card_number_last4, COALESCE(ch.name, '') FROM credit_cards cc
                        LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id WHERE cc.card_id = ?""", (item_id,))
        name = (info[0][0] + (f" · {info[0][1]}" if info[0][1] else "")) if info else "?"
        stmts = _rows("""SELECT cc_statement_id, due_date, COALESCE(statement_month, ''), previous_balance, new_charges, interest_charged, total_amount_due,
                                COALESCE(amount_paid, 0), payment_date, soa_confirmed_at
                         FROM cc_statements WHERE card_id = ? AND status != 'Cancelled' ORDER BY due_date, cc_statement_id""", (item_id,))
        lines = []
        for k, (sid, due, month, prev, new, fin, total, paid, pday, soa) in enumerate(stmts):
            fresh = float(new or 0) + float(fin or 0)
            if k == 0 and float(prev or 0) > 0:
                lines.append((due, "Balance brought over", month, float(prev), 0.0))
            if not new and not fin and not prev:
                fresh = float(total or 0)                      # statements recorded with the total only
            lines.append((due, "Statement" + (f" {month}" if month else ""), month, fresh, 0.0))
            recorded = _rows("SELECT payment_date, amount FROM cc_payments WHERE cc_statement_id = ?", (sid,))
            if soa and abs(sum(float(a) for _, a in recorded) - float(paid)) > 0.005:
                recorded = []                                   # the SOA is final (owner, 2026-10-09): the daily records it contradicts are not counted
            for d, a in recorded:
                lines.append((d, "Payment", month, 0.0, float(a)))
            rest = round(float(paid) - sum(float(a) for _, a in recorded), 2)
            if rest > 0.005:
                lines.append((pday or due, "Payment (per the next SOA)", month, 0.0, rest))
        return name, lines, []
    else:
        raise ValueError("kind")
    waiting = []
    for d, num, ref, a, cleared in checks:
        if cleared:
            lines.append((cleared, f"Check {num or '(no number)'} cleared", ref or "", 0.0, float(a or 0)))
        elif num:
            waiting.append({"day": str(d)[:10], "doc": f"Check {num}", "ref": ref or "", "amount": float(a or 0)})
    return name, lines, waiting


def ledger(kind: str, item_id: int, start: date | None = None, end: date | None = None) -> dict:
    name, raw, waiting = _raw(kind, int(item_id))
    raw = sorted(((str(d)[:10], doc, ref, c, p) for d, doc, ref, c, p in raw if d), key=lambda x: (x[0], -x[3]))
    s, e = (start.isoformat() if start else ""), (end.isoformat() if end else "9999")
    opening = round(sum(c - p for d, _, _, c, p in raw if d < s), 2)
    bal, lines = opening, []
    for d, doc, ref, c, p in raw:
        if s <= d <= e:
            bal = round(bal + c - p, 2)
            lines.append({"day": d, "doc": doc, "ref": ref, "charge": c, "payment": p, "balance": bal})
    charges, payments = round(sum(x["charge"] for x in lines), 2), round(sum(x["payment"] for x in lines), 2)
    waiting = [w for w in waiting if w["day"] <= e]
    return {"name": name, "kind": kind, "kind_label": KINDS[kind], "start": start, "end": end, "lines": lines, "opening": opening, "charges": charges,
            "payments": payments, "closing": round(opening + charges - payments, 2), "waiting": waiting, "waiting_total": round(sum(w["amount"] for w in waiting), 2)}


def _dmy(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso and len(iso) >= 10 else (iso or "")


def pdf(st: dict) -> bytes:
    """The statement as a PDF: the same title band, tables and totals as the other statements of account."""
    from reportlab.platypus import Paragraph, Spacer
    from engines import account_history_pdf_service as h
    buf = io.BytesIO()
    doc = h._doc(buf)
    styles = h._styles()
    period = ("From " + st["start"].strftime("%m/%d/%Y") if st["start"] else "From the start") + " to " + (st["end"].strftime("%m/%d/%Y") if st["end"] else "today")
    story = [h._title_band(st["kind_label"], st["name"]), Spacer(1, 6), Paragraph(period, styles["grey"]), Spacer(1, 8)]
    rows = [["", "Balance brought forward", "", "", "", h._money(st["opening"])]] if st["start"] else []
    rows += [[_dmy(x["day"]), x["doc"], x["ref"], h._money(x["charge"]) if x["charge"] else "", h._money(x["payment"]) if x["payment"] else "", h._money(x["balance"])]
             for x in st["lines"]]
    w = h.CONTENT_WIDTH
    story.append(h._section_table(["Date", "Document", "Reference", "Charge", "Payment", "Balance"], rows or [["", "Nothing in this period.", "", "", "", ""]],
                                  [w * f for f in (0.13, 0.3, 0.17, 0.13, 0.13, 0.14)], right_cols=(3, 4, 5)))
    if st["waiting"]:
        story += [Spacer(1, 8), Paragraph("Checks written, waiting for the bank (not counted as paid yet)", styles["section"]),
                  h._section_table(["Dated", "Check", "Reference", "Amount"], [[_dmy(x["day"]), x["doc"], x["ref"], h._money(x["amount"])] for x in st["waiting"]],
                                   [w * f for f in (0.18, 0.32, 0.25, 0.25)], right_cols=(3,))]
    story += [Spacer(1, 8), h._totals_block([("Charges", st["charges"]), ("Payments (cleared)", st["payments"]), ("BALANCE", st["closing"])]
                                          + ([("Checks waiting for the bank", st["waiting_total"])] if st["waiting"] else []))]
    h._finish(doc, story)
    buf.seek(0)
    return buf.getvalue()
