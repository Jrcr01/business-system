"""Loan Note / repayment-schedule PDF — the printable document that serves as the LN/MOU
itself, matching the layout of the lender's own reference spreadsheets: header block (LN No.,
Date, Principal, Monthly Interest, Loan Schedule vs. Payment Schedule vs. Difference), the
repayment schedule table, and the checks-issued table.
"""
import io
import os
import sqlite3
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from engines.common import get_conn
from engines.clock import manila_now

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

ACCENT = colors.HexColor("#1f3a5f")
ACCENT_LIGHT = colors.HexColor("#eef2f7")
GREY = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#d1d5db")

LEFT_MARGIN = RIGHT_MARGIN = 0.55 * inch
TOP_MARGIN = 0.5 * inch
BOTTOM_MARGIN = 0.65 * inch
CONTENT_WIDTH = LETTER[0] - LEFT_MARGIN - RIGHT_MARGIN

SECTION_TABLE_STYLE = [
    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ACCENT_LIGHT]),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]


def _money(v) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def _fetch_loan_note_data(loan_note_id: int) -> dict | None:
    with get_conn(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        note = cur.execute(
            """SELECT ln.*, c.creditor_name
               FROM loan_notes ln JOIN creditors c ON ln.creditor_id = c.creditor_id
               WHERE ln.loan_note_id = ?""",
            (loan_note_id,),
        ).fetchone()
        if not note:
            return None
        installments = cur.execute(
            """SELECT installment_no, due_date, principal, interest, outstanding_balance, status, check_id
               FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date, installment_id""",
            (loan_note_id,),
        ).fetchall()
        checks = cur.execute(
            """SELECT li.installment_no, chk.check_date, chk.check_number, chk.amount, chk.status
               FROM loan_installments li JOIN checks_issued chk ON li.check_id = chk.check_id
               WHERE li.loan_note_id = ? AND chk.status != 'Cancelled'
               ORDER BY li.installment_no"""
        , (loan_note_id,)).fetchall()
    return {"note": note, "installments": installments, "checks": checks}


def _title_band(styles) -> Table:
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    band = Table(
        [[Paragraph("LOAN NOTE", title_style), Paragraph("PRIVATE LENDER REPAYMENT SCHEDULE", sub_style)]],
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    return band


def _info_panel(note, styles) -> Table:
    label_style = ParagraphStyle("InfoLabel", parent=styles["Normal"], fontSize=7.5, textColor=GREY, leading=10)
    value_style = ParagraphStyle("InfoValue", parent=styles["Normal"], fontSize=9.5, leading=13, fontName="Helvetica-Bold")
    rows = [
        [Paragraph("LN. NO.", label_style), Paragraph("LENDER", label_style),
         Paragraph("DATE", label_style), Paragraph("STATUS", label_style)],
        [Paragraph(note["note_ref"], value_style), Paragraph(note["creditor_name"], value_style),
         Paragraph(note["issue_date"], value_style), Paragraph(note["status"], value_style)],
        [Paragraph("PRINCIPAL", label_style), Paragraph("MONTHLY INTEREST", label_style),
         Paragraph("GENERATED", label_style), Paragraph("", label_style)],
        [Paragraph(f"PHP {_money(note['principal_amount'])}", value_style),
         Paragraph("—" if note["monthly_interest_rate"] is None else f"{note['monthly_interest_rate']:g}%", value_style),
         Paragraph(manila_now().strftime("%Y-%m-%d %H:%M"), value_style), Paragraph("", value_style)],
    ]
    panel = Table(rows, colWidths=[CONTENT_WIDTH * 0.25] * 4)
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, BORDER),
        ("LINEBELOW", (0, 2), (-1, 2), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return panel


def _section_heading(text: str, styles) -> Paragraph:
    style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=14, spaceAfter=6)
    return Paragraph(text, style)


def generate_loan_note_pdf(loan_note_id: int) -> bytes | None:
    data = _fetch_loan_note_data(loan_note_id)
    if data is None:
        return None
    note, installments, checks = data["note"], data["installments"], data["checks"]

    loan_schedule_total = sum(r["principal"] + r["interest"] for r in installments)
    payment_schedule_total = sum(c["amount"] for c in checks)
    difference = round(loan_schedule_total - payment_schedule_total, 2)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )
    styles = getSampleStyleSheet()
    story: list = [_title_band(styles), Spacer(1, 10), _info_panel(note, styles), Spacer(1, 10)]

    recon_rows = [
        ["Loan Schedule (principal + interest)", f"PHP {_money(loan_schedule_total)}"],
        ["Payment Schedule (checks issued)", f"PHP {_money(payment_schedule_total)}"],
        ["Difference", f"PHP {_money(difference)}" if abs(difference) > 0.005 else "—"],
    ]
    recon_table = Table(recon_rows, colWidths=[CONTENT_WIDTH * 0.72, CONTENT_WIDTH * 0.28])
    recon_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), ACCENT),
        ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(recon_table)

    story.append(_section_heading("Repayment Schedule", styles))
    sched_rows = [["Date", "Principal (PHP)", "Interest (PHP)", "Outstanding Balance (PHP)", "Status"]]
    for r in installments:
        sched_rows.append([r["due_date"], _money(r["principal"]) if r["principal"] else "—", _money(r["interest"]) if r["interest"] else "—", _money(r["outstanding_balance"]), r["status"]])
    if len(sched_rows) == 1:
        sched_rows.append(["—", "—", "—", "—", "—"])
    sched_table = Table(sched_rows, colWidths=[CONTENT_WIDTH * w for w in (0.2, 0.22, 0.22, 0.24, 0.12)], repeatRows=1)
    sched_table.setStyle(TableStyle(SECTION_TABLE_STYLE + [("ALIGN", (1, 1), (3, -1), "RIGHT")]))
    story.append(sched_table)

    story.append(_section_heading("Checks Issued", styles))
    chk_rows = [["No.", "Date", "Check Number", "Amount (PHP)"]]
    for c in checks:
        chk_rows.append([c["installment_no"], c["check_date"], c["check_number"] or "Pending", _money(c["amount"])])
    if len(chk_rows) == 1:
        chk_rows.append(["—", "—", "—", "—"])
    chk_table = Table(chk_rows, colWidths=[CONTENT_WIDTH * w for w in (0.12, 0.25, 0.35, 0.28)], repeatRows=1)
    chk_table.setStyle(TableStyle(SECTION_TABLE_STYLE + [("ALIGN", (3, 1), (3, -1), "RIGHT")]))
    story.append(chk_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
