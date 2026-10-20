"""One-page "Today" report (2026-09-27, per user: a quick report of what's due today and such, printable). Presentation only:
takes engines.payment_run_service.get_quick_report()'s result and lays it out -- the same numbers the Payment run page shows.
Same look as the Statement of Account PDFs (title band, section tables, nothing under 9pt). Always one page: when a day is
busy, the longest lists are shortened with a '+ N more' line (the full lists are on screen and in Reports)."""
from __future__ import annotations

import io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from reportlab.lib.pagesizes import LETTER

from engines.account_history_pdf_service import (
    ACCENT, BOTTOM_MARGIN, CONTENT_WIDTH, GREY, LEFT_MARGIN, RIGHT_MARGIN, TOP_MARGIN, _money, _section_table,
)
from engines.clock import manila_now

def _company():
    from engines.company import name
    return name()


def _d(v) -> str:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").strftime("%a %m/%d")
    except ValueError:
        return str(v)


def _band(report: dict) -> Table:
    ss = getSampleStyleSheet()
    title = ParagraphStyle("TTitle", parent=ss["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub = ParagraphStyle("TSub", parent=ss["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    t = Table([[Paragraph("TODAY'S PAYMENT REPORT", title),
                Paragraph(f"{_company()}<br/>{report['today']:%A, %m/%d/%Y}", sub)]],
              colWidths=[CONTENT_WIDTH * 0.55, CONTENT_WIDTH * 0.45])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                           ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
    return t


def _build(report: dict, caps: dict) -> tuple[bytes, int]:
    ss = getSampleStyleSheet()
    sec = ParagraphStyle("Sec", parent=ss["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=10, spaceAfter=4)
    note = ParagraphStyle("Note", parent=ss["Normal"], fontSize=9, textColor=GREY, spaceAfter=2)
    story = [_band(report), Spacer(1, 6)]

    def rows_with_more(rows, cap, width_cols):
        if len(rows) <= cap:
            return rows
        more = len(rows) - cap
        return rows[:cap] + [[f"+ {more} more (see the Payment run page or Reports)"] + [""] * (width_cols - 1)]

    # 1. due on the bank day
    due, bank_day = report["due_today"], report["bank_day"]
    head = "Due today" if bank_day == report["today"] else f"Due next bank day ({_d(bank_day)})"
    story.append(Paragraph(f"{head} — PHP {_money(float(due['amount'].sum()) if not due.empty else 0.0)} · {len(due)} item(s)", sec))
    if due.empty:
        story.append(Paragraph("Nothing due.", note))
    else:
        rows = [[r["track"], r["payee_name"], str(r["reference"] or ""), _money(float(r["amount"]))] for _, r in due.iterrows()]
        story.append(_section_table(["Track", "Payee", "Reference", "Amount"], rows_with_more(rows, caps["due"], 4),
                                    [CONTENT_WIDTH * f for f in (0.14, 0.40, 0.28, 0.18)], right_cols=(3,)))

    # 2. checks dated today
    ck = report["checks_today"]
    story.append(Paragraph(f"Checks dated today — PHP {_money(float(ck['amount'].sum()) if not ck.empty else 0.0)} · {len(ck)} check(s)", sec))
    if ck.empty:
        story.append(Paragraph("No checks are dated today.", note))
    else:
        rows = [[r["check_number"] or "no number yet", r["payee"], f"{r['reference'] or ''} ({r['track']})", r["state"], _money(float(r["amount"]))]
                for _, r in ck.iterrows()]
        story.append(_section_table(["Check #", "Payee", "Batch / loan note", "Status", "Amount"], rows_with_more(rows, caps["checks"], 5),
                                    [CONTENT_WIDTH * f for f in (0.15, 0.33, 0.24, 0.11, 0.17)], right_cols=(4,)))

    # 3. next 7 days
    n7 = report["next7"]
    story.append(Paragraph(f"Next 7 days (due today to {report['today'] + timedelta(days=7):%m/%d}) — "
                           f"PHP {_money(report['next7_total'])}", sec))
    if n7.empty:
        story.append(Paragraph("Nothing due in the next 7 days.", note))
    else:
        rows = [[_d(r["day"]), _money(r["Supplier"]), _money(r["Lender"]), _money(r["Bank Loan"]), _money(r["Credit Card"]), _money(r["Total"])]
                for _, r in n7.iterrows()]
        rows.append(["Total", *[_money(float(n7[c].sum())) for c in ("Supplier", "Lender", "Bank Loan", "Credit Card", "Total")]])
        t = _section_table(["Paid on", "Supplier", "Lender", "Bank loan", "Credit card", "Total"], rows,
                           [CONTENT_WIDTH * f for f in (0.20, 0.16, 0.16, 0.16, 0.16, 0.16)], right_cols=(1, 2, 3, 4, 5))
        t.setStyle(TableStyle([("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 1.0, ACCENT)]))
        story.append(t)

    # 4. overdue
    od = report["overdue"]
    story.append(Paragraph(f"Overdue, not covered — PHP {_money(float(od['amount'].sum()) if not od.empty else 0.0)} · {len(od)} item(s)", sec))
    if od.empty:
        story.append(Paragraph("Nothing overdue.", note))
    else:
        rows = [[r["kind"], r["payee"], str(r["reference"]), _d(r["due_date"]), _money(float(r["amount"]))] for _, r in od.iterrows()]
        story.append(_section_table(["What", "Payee", "Reference", "Was due", "Amount"], rows_with_more(rows, caps["overdue"], 5),
                                    [CONTENT_WIDTH * f for f in (0.16, 0.32, 0.22, 0.13, 0.17)], right_cols=(4,)))

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"A Saturday/Sunday due date is paid the next banking day (Monday). Printed {manila_now():%m/%d/%Y %I:%M %p}.", note))

    buf = io.BytesIO()
    pages = {"n": 0}

    def count(canvas, doc):
        pages["n"] = doc.page

    SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN,
                      bottomMargin=BOTTOM_MARGIN, title="Today's payment report").build(story, onFirstPage=count, onLaterPages=count)
    return buf.getvalue(), pages["n"]


def generate_today_report_pdf(report: dict) -> bytes:
    """The quick report as a one-page PDF (lists shortened step by step until it fits)."""
    caps = {"due": 22, "checks": 12, "overdue": 10}
    pdf, n = _build(report, caps)
    while n > 1:
        # trim one row from the longest list shown, until it fits (each list keeps at least 3 rows)
        sizes = {"due": min(caps["due"], len(report["due_today"])), "checks": min(caps["checks"], len(report["checks_today"])),
                 "overdue": min(caps["overdue"], len(report["overdue"]))}
        k = max(sizes, key=sizes.get)
        if sizes[k] <= 3:
            break
        caps[k] = sizes[k] - 1
        pdf, n = _build(report, caps)
    return pdf
