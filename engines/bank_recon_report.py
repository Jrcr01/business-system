"""Bank reconciliation statement (2026-09-28, owner-approved: "bank vs issued checks" -- the system has no cash book, so this reconciles the
bank balance with the checks we issued, not a full book-vs-bank reconciliation).

For one imported statement (as of its last bank date):
    Balance per bank statement
  - Outstanding checks  (our active checks, numbered or printed, dated on or before that date, not cleared by the bank by then -- listed)
  = Available cash after outstanding checks
plus the bank items the system does not have (debit memos, bills payments, bounced deposits, other debits) with how each was resolved, the
money in during the statement, and a signature line. Screen: views/bank_cash.py; PDF: generate_recon_pdf().
"""
from __future__ import annotations

import io
import os
import sqlite3
from datetime import date, datetime
from xml.sax.saxutils import escape

import pandas as pd
from engines.common import get_conn
from engines.clock import manila_now

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def recon_statement(import_id: int) -> dict | None:
    imp = _read("SELECT * FROM bank_statement_imports WHERE import_id = ?", (int(import_id),))
    if imp.empty:
        return None
    imp = imp.iloc[0].to_dict()
    with get_conn(DB_PATH) as conn:                   # stored in UTC (CURRENT_TIMESTAMP): show local time
        for k in ("imported_at", "reconciled_at"):
            if imp.get(k) and isinstance(imp[k], str):
                imp[k] = conn.execute("SELECT datetime(?, '+8 hours')", (imp[k],)).fetchone()[0]
    lines = _read("SELECT * FROM bank_transactions WHERE import_id = ? ORDER BY posting_date, txn_id", (int(import_id),))
    as_of = date.fromisoformat(str(lines["posting_date"].max())[:10]) if not lines.empty else date.fromisoformat(str(imp["period_to"])[:10])
    last = _read("""SELECT balance FROM bank_transactions WHERE balance IS NOT NULL AND posting_date <= ? ORDER BY posting_date DESC, txn_id DESC LIMIT 1""",
                 (as_of.isoformat(),))
    bank_balance = float(last["balance"][0]) if not last.empty else float(imp["closing_balance"] or 0)
    outstanding = _read(
        """SELECT c.check_number, c.check_date, cr.creditor_name AS payee, COALESCE(os.statement_ref, ln.note_ref) AS reference, c.amount
           FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
           LEFT JOIN order_statements os ON os.statement_id = c.statement_id LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
           WHERE c.status = 'Active' AND ((c.check_number IS NOT NULL AND c.check_number != '') OR c.printed_at IS NOT NULL)
             AND c.check_date <= ? AND (c.bank_cleared_date IS NULL OR c.bank_cleared_date > ?)
             AND (c.settled_outside_at IS NULL OR c.settled_outside_at > ?)
           ORDER BY c.check_date, c.check_number""", (as_of.isoformat(), as_of.isoformat(), as_of.isoformat()))
    items = lines[lines["kind"].isin(["DEBIT_MEMO", "BILLS_PAYMENT", "RETURNED_CHECK", "OTHER"]) & (lines["debit"] > 0)].copy()
    items["how"] = [
        {"matched_loan": "Bank-loan installment (matched)", "matched_card": "Credit-card payment (matched)"}.get(ms)
        or (res if isinstance(res, str) and res else "Not resolved yet")
        for ms, res in zip(items["match_status"], items["resolution"])]
    out_total = float(outstanding["amount"].sum()) if not outstanding.empty else 0.0
    return {"import": imp, "as_of": as_of, "bank_balance": bank_balance, "outstanding": outstanding, "outstanding_total": out_total,
            "available": round(bank_balance - out_total, 2), "bank_items": items, "money_in": float(lines["credit"].sum()) if not lines.empty else 0.0,
            "checks_cleared": int((lines["match_status"].isin(["matched", "matched_manual"])).sum()) if not lines.empty else 0,
            "open_items": int(((lines["reviewed"] == 0) & lines["match_status"].isin(
                ["guarantee_check_presented", "cleared_twice", "amount_mismatch", "cancelled_check_cleared", "returned_check", "not_in_system",
                 "unmatched_debit"])).sum()) if not lines.empty else 0}           # guarantee checks the bank presented count too (2026-10-08 gap fix)


def _us(v) -> str:
    from engines.common import us_date
    return us_date(v)


def generate_recon_pdf(r: dict) -> bytes:
    """The reconciliation statement as a PDF (Statement of Account look: navy band, section tables, nothing under 9 pt)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from engines.account_history_pdf_service import (
        ACCENT, BOTTOM_MARGIN, CONTENT_WIDTH, GREY, LEFT_MARGIN, RIGHT_MARGIN, TOP_MARGIN, _money, _section_table,
    )

    ss = getSampleStyleSheet()
    title = ParagraphStyle("RT", parent=ss["Normal"], textColor=colors.white, fontSize=15, leading=18, fontName="Helvetica-Bold")
    sub = ParagraphStyle("RS", parent=ss["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    sec = ParagraphStyle("RH", parent=ss["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=10, spaceAfter=4)
    note = ParagraphStyle("RN", parent=ss["Normal"], fontSize=9, textColor=GREY)
    imp = r["import"]
    band = Table([[Paragraph("BANK RECONCILIATION", title),
                   Paragraph(f"Slick's Tire Supply<br/>Metrobank account ending {escape(str(imp.get('account_last4') or '----'))}<br/>"
                             f"As of {r['as_of']:%m/%d/%Y}", sub)]], colWidths=[CONTENT_WIDTH * 0.55, CONTENT_WIDTH * 0.45])
    band.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                              ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    story = [band, Spacer(1, 8),
             Paragraph(f"Statement period {escape(_us(imp['period_from']))} to {escape(_us(imp['period_to']))} · imported {escape(_us(str(imp['imported_at'])[:16]))} · "
                       f"status {'Reconciled' if imp.get('reconciled_at') else 'Open'}", note)]
    summary = [["Balance per bank statement", _money(r["bank_balance"])],
               [f"Less: outstanding checks ({len(r['outstanding'])})", ("-" if r["outstanding_total"] else "") + _money(r["outstanding_total"])],
               ["Available cash after outstanding checks", _money(r["available"])]]
    t = Table(summary, colWidths=[CONTENT_WIDTH * 0.7, CONTENT_WIDTH * 0.3])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 10), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [Paragraph("Summary", sec), t]
    story.append(Paragraph(f"Outstanding checks — issued, dated on or before {r['as_of']:%m/%d/%Y}, not yet cleared by the bank", sec))
    if r["outstanding"].empty:
        story.append(Paragraph("None.", note))
    else:
        rows = [[str(x.check_number or "no number"), __import__("engines.common", fromlist=["us_date"]).us_date(x.check_date), str(x.payee), str(x.reference or ""), _money(x.amount)]
                for x in r["outstanding"].itertuples()]
        story.append(_section_table(["Check #", "Check date", "Payee", "Payment statement / loan note", "Amount (PHP)"], rows,
                                    [CONTENT_WIDTH * f for f in (0.16, 0.14, 0.34, 0.2, 0.16)], right_cols=(4,)))
    story.append(Paragraph("Bank items not in the system (debits that are not our checks)", sec))
    if r["bank_items"].empty:
        story.append(Paragraph("None.", note))
    else:
        rows = [[__import__("engines.common", fromlist=["us_date"]).us_date(x.posting_date), __import__("engines.common", fromlist=["us_dates_in"]).us_dates_in(x.description), __import__("engines.common", fromlist=["us_dates_in"]).us_dates_in(x.how), _money(x.debit)] for x in r["bank_items"].itertuples()]
        story.append(_section_table(["Bank date", "Bank description", "How it was resolved", "Amount (PHP)"], rows,
                                    [CONTENT_WIDTH * f for f in (0.14, 0.34, 0.34, 0.18)], right_cols=(3,)))
    story += [Spacer(1, 10), Paragraph(f"Money in during the statement: PHP {_money(r['money_in'])} · checks cleared and matched: {r['checks_cleared']} · "
                                       f"items still open: {r['open_items']}", note),
              Spacer(1, 26), Paragraph("Prepared by: ______________________________ &nbsp;&nbsp;&nbsp; Date: ________________", ss["Normal"]),
              Spacer(1, 14), Paragraph(f"This reconciles the bank balance with the checks issued; the system keeps no cash book. Printed {manila_now():%m/%d/%Y %I:%M %p}.", note)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
                      title="Bank reconciliation").build(story)
    return buf.getvalue()
