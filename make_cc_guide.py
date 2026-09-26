"""Builds the one-page printable guide: Credit_Card_Workflow_Guide.pdf (run: python make_cc_guide.py)."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = os.path.dirname(os.path.abspath(__file__))
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#f2b705")
PALE = colors.HexColor("#f3f6fa")
GREY = colors.HexColor("#6b7280")
W, H = LETTER
M = 0.55 * inch
CW = W - 2 * M

base = getSampleStyleSheet()["Normal"]
S = {
    "title": ParagraphStyle("t", parent=base, fontSize=19, leading=22, textColor=colors.white, fontName="Helvetica-Bold"),
    "sub": ParagraphStyle("s", parent=base, fontSize=9.5, leading=12, textColor=colors.white),
    "h": ParagraphStyle("h", parent=base, fontSize=11.5, leading=14, textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=3),
    "step": ParagraphStyle("st", parent=base, fontSize=9.5, leading=12.4, leftIndent=14, bulletIndent=0, spaceAfter=2),
    "body": ParagraphStyle("b", parent=base, fontSize=9.5, leading=12.4, spaceAfter=2),
    "small": ParagraphStyle("sm", parent=base, fontSize=9, leading=11.5, textColor=GREY),
    "tip": ParagraphStyle("tp", parent=base, fontSize=9.5, leading=12.4, leftIndent=10, bulletIndent=0, spaceAfter=1.5),
}


def steps(items):
    return [Paragraph(t, S["step"], bulletText=f"{i}.") for i, t in enumerate(items, 1)]


def box(title, flow, accent=NAVY):
    inner = [Paragraph(title, S["h"])] + flow
    t = Table([[inner]], colWidths=[CW / 2 - 6])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), PALE), ("LINEABOVE", (0, 0), (-1, 0), 3, accent),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                           ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def banner_and_logo():
    logo = os.path.join(HERE, "assets", "slicks_logo.jpg")
    left = Image(logo, width=0.62 * inch, height=0.62 * inch) if os.path.exists(logo) else ""
    head = [Paragraph("Recording credit card statements - the simple workflow", S["title"]),
            Paragraph("Slick's Tire Supply  |  AP Disbursement System  |  Print and keep by the computer", S["sub"])]
    t = Table([[left, head]], colWidths=[0.85 * inch, CW - 0.85 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                           ("LINEBELOW", (0, 0), (-1, -1), 4, GOLD)]))
    return t


def build(path):
    doc = SimpleDocTemplate(path, pagesize=LETTER, leftMargin=M, rightMargin=M, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title="Recording credit card statements - the simple workflow", author="Slick's Tire Supply")
    a = box("A.  A statement is sent to you  (about once a month per card)", steps([
        "Credit cards > Statements > <b>Add statement</b>.",
        "Pick the <b>cardholder</b>, the <b>issuer</b> (bank) and the <b>card</b>.",
        "Turn ON <b>I only have the total amount due and the due date</b>.",
        "Type the <b>Total amount due</b> and the <b>Due date</b>. Add the <b>Finance charge</b> if the statement lists one.",
        "Check the <b>Statement month</b> (it is pre-filled; change it only if wrong).",
        "Leave <b>I don't know yet how much was paid</b> ticked - you are not the one paying, so the next statement will tell you.",
        "If the card had an earlier statement, type the <b>Balance carried over from the earlier statement</b>, exactly as printed on this new statement. Type <b>0</b> if it shows none.",
        "Click <b>Add statement</b>. The earlier statement is now marked Paid or Partially Paid automatically.",
    ]))
    b = box("B.  What the carried-over balance tells you", [
        Paragraph("The new statement's carried-over (previous) balance is what the <b>cardholder did not pay</b> on the earlier statement.", S["body"]),
        Paragraph("<b>0</b> = paid in full.  <b>Same as the earlier total</b> = nothing was paid.  <b>In between</b> = partly paid.", S["body"]),
        Paragraph("If a balance is carried over, tell the cardholder so they can pay it and avoid charges.", S["body"]),
        Spacer(1, 3),
        Paragraph("<b>Until the next statement arrives</b> the earlier one is marked <i>Not yet confirmed</i> and stays counted as owed - the safe way, nothing is assumed paid.", S["body"]),
    ], accent=GOLD)
    c = box("You can ignore", [
        Paragraph("Payments: you do not record them. The next statement shows what was paid, and the system works it out from the carried-over balance.", S["tip"], bulletText="•"),
        Paragraph("Previous balance, new charges and interest - unless the statement shows them and you want them recorded.", S["tip"], bulletText="•"),
        Paragraph("Double counting - the system counts a carried-over balance only once.", S["tip"], bulletText="•"),
    ])
    d = box("Rules of thumb", [
        Paragraph("<b>The statement is the truth.</b> Always type figures as printed on it.", S["tip"], bulletText="•"),
        Paragraph("<b>Same number twice?</b> A pop-up asks whether to continue. Say No unless it is really a second one.", S["tip"], bulletText="•"),
        Paragraph("<b>Made a mistake?</b> Credit cards > Statements > <b>Edit / delete a statement</b> (only the card's latest one).", S["tip"], bulletText="•"),
        Paragraph("Interest is only known if you type the <b>Finance charge</b>.", S["tip"], bulletText="•"),
    ], accent=GOLD)
    e = box("Monthly check  (two minutes)", [
        Paragraph("Dashboard > <b>Needs attention</b>: any card listed as a missing statement? Ask that cardholder to send it.", S["tip"], bulletText="[   ]"),
        Paragraph("Every card has this month's statement recorded.", S["tip"], bulletText="[   ]"),
        Paragraph("Reports > <b>Possible duplicates</b>: it should say \"Nothing suspicious found\".", S["tip"], bulletText="[   ]"),
    ])
    f = box("Where the numbers show up", [
        Paragraph("<b>Total payable</b> (Dashboard, reports): each card's unpaid amount, counted once.", S["tip"], bulletText="•"),
        Paragraph("<b>Schedule of payments and calendar</b>: each statement on its due date, so you can remind cardholders before it is due.", S["tip"], bulletText="•"),
        Paragraph("<b>Cost of financing</b>: the finance charges you typed.", S["tip"], bulletText="•"),
    ], accent=GOLD)
    grid = Table([[a, [b]], [KeepInFrame(CW / 2 - 6, 3.6 * inch, [c], mode="shrink"), [d]], [e, f]], colWidths=[CW / 2, CW / 2], hAlign="LEFT")
    grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    doc.build([banner_and_logo(), Spacer(1, 8), grid, Spacer(1, 6),
               Paragraph("Stuck? Note the message on screen and ask - nothing you enter is lost, and every change is backed up daily.", S["small"])])


if __name__ == "__main__":
    out = os.path.join(HERE, "Credit_Card_Workflow_Guide.pdf")
    build(out)
    print(out)
