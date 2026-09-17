"""Bank Loan schedule PDF — the printable report for a bank loan, matching the same treatment
Order Statements (Batch Statement) and Loan Notes (LN/MOU) already get: header block (Loan Ref,
Bank, Account Number, Start/Maturity dates, Mode of Payment, Principal) plus the Due Date/Amount
schedule table. "Status" per row is computed the same way as everywhere else in this system --
due_date <= today means cleared -- never a stored value.
"""
import io
import sqlite3
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import os
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


def _fetch_bank_loan_data(bank_loan_id: int) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        loan = cur.execute(
            """SELECT bl.*, c.creditor_name, lb.name AS beneficiary, lb.beneficiary_type
               FROM bank_loans bl
               JOIN creditors c ON bl.creditor_id = c.creditor_id
               LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
               WHERE bl.bank_loan_id = ?""",
            (bank_loan_id,),
        ).fetchone()
        if not loan:
            return None
        installments = cur.execute(
            """SELECT installment_no, due_date, amount FROM bank_loan_installments
               WHERE bank_loan_id = ? ORDER BY due_date, installment_id""",
            (bank_loan_id,),
        ).fetchall()
    return {"loan": loan, "installments": installments}


def _title_band(styles) -> Table:
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    band = Table(
        [[Paragraph("BANK LOAN", title_style), Paragraph("MONTHLY DUES SCHEDULE", sub_style)]],
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


def _info_panel(loan, styles) -> Table:
    label_style = ParagraphStyle("InfoLabel", parent=styles["Normal"], fontSize=7.5, textColor=GREY, leading=10)
    value_style = ParagraphStyle("InfoValue", parent=styles["Normal"], fontSize=9.5, leading=13, fontName="Helvetica-Bold")
    rows = [
        [Paragraph("LOAN REF.", label_style), Paragraph("BANK", label_style),
         Paragraph("ACCOUNT NO.", label_style), Paragraph("STATUS", label_style)],
        [Paragraph(loan["loan_ref"], value_style), Paragraph(loan["creditor_name"], value_style),
         Paragraph(loan["account_number"] or "—", value_style), Paragraph(loan["status"], value_style)],
        [Paragraph("START DATE", label_style), Paragraph("MATURITY DATE", label_style),
         Paragraph("PRINCIPAL", label_style), Paragraph("MODE OF PAYMENT", label_style)],
        [Paragraph(loan["start_date"], value_style), Paragraph(loan["maturity_date"], value_style),
         Paragraph(f"PHP {_money(loan['principal_amount'])}", value_style), Paragraph(loan["mode_of_payment"] or "—", value_style)],
        [Paragraph("ATTRIBUTABLE TO", label_style), Paragraph("", label_style),
         Paragraph("", label_style), Paragraph("", label_style)],
        [Paragraph(f"{loan['beneficiary']} ({loan['beneficiary_type']})" if loan["beneficiary"] else "—", value_style),
         Paragraph("", value_style), Paragraph("", value_style), Paragraph("", value_style)],
    ]
    panel = Table(rows, colWidths=[CONTENT_WIDTH * 0.25] * 4)
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, BORDER),
        ("LINEBELOW", (0, 2), (-1, 2), 0.5, BORDER),
        ("LINEBELOW", (0, 4), (-1, 4), 0.5, BORDER),
        ("SPAN", (0, 4), (-1, 4)),
        ("SPAN", (0, 5), (-1, 5)),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return panel


def _section_heading(text: str, styles) -> Paragraph:
    style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=14, spaceAfter=6)
    return Paragraph(text, style)


def generate_bank_loan_pdf(bank_loan_id: int) -> bytes | None:
    data = _fetch_bank_loan_data(bank_loan_id)
    if data is None:
        return None
    loan, installments = data["loan"], data["installments"]
    today = date.today()

    total_amount = sum(r["amount"] for r in installments)
    outstanding = sum(r["amount"] for r in installments if date.fromisoformat(r["due_date"]) > today)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )
    styles = getSampleStyleSheet()
    story: list = [_title_band(styles), Spacer(1, 10), _info_panel(loan, styles), Spacer(1, 10)]

    recon_rows = [
        ["Total Scheduled", f"PHP {_money(total_amount)}"],
        ["Outstanding (not yet due)", f"PHP {_money(outstanding)}"],
        ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M")],
    ]
    recon_table = Table(recon_rows, colWidths=[CONTENT_WIDTH * 0.72, CONTENT_WIDTH * 0.28])
    recon_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica"),
        ("TEXTCOLOR", (0, 0), (-1, 1), ACCENT),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(recon_table)

    story.append(_section_heading("Repayment Schedule", styles))
    sched_rows = [["No.", "Due Date", "Amount (PHP)", "Status"]]
    for r in installments:
        cleared = date.fromisoformat(r["due_date"]) <= today
        sched_rows.append([r["installment_no"], r["due_date"], _money(r["amount"]), "Paid" if cleared else "Pending"])
    if len(sched_rows) == 1:
        sched_rows.append(["—", "—", "—", "—"])
    sched_table = Table(sched_rows, colWidths=[CONTENT_WIDTH * w for w in (0.12, 0.3, 0.33, 0.25)], repeatRows=1)
    sched_table.setStyle(TableStyle(SECTION_TABLE_STYLE + [("ALIGN", (2, 1), (2, -1), "RIGHT")]))
    story.append(sched_table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
