"""Builds the printable System_Manual.pdf (run: python make_system_manual.py).

For the owner and his mother: the whole workflow of the AP system in plain words -- the one-page picture, the map of the program, the daily / weekly / month-end
routines, every recording job, the bank, what to do when something looks wrong, the rules the system keeps, and a words list. All the wording lives in the
SECTIONS list near the bottom, so a text change never needs a layout change. Re-run this script whenever a workflow changes.

Only characters that the built-in PDF font can print are used (no peso sign or arrows): amounts are written "PHP 1,000" and "then" is written ">"."""
from __future__ import annotations

import os
from datetime import date

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "System_Manual.pdf")
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#f2b705")
PALE = colors.HexColor("#f3f6fa")
GREY = colors.HexColor("#6b7280")
GREEN = colors.HexColor("#2e7d32")
RED = colors.HexColor("#b3261e")
W, H = LETTER
M = 0.7 * inch
CW = W - 2 * M

base = getSampleStyleSheet()["Normal"]
S = {
    "h1": ParagraphStyle("h1", parent=base, fontSize=19, leading=23, textColor=NAVY, fontName="Helvetica-Bold", spaceAfter=4),
    "h2": ParagraphStyle("h2", parent=base, fontSize=13, leading=16, textColor=NAVY, fontName="Helvetica-Bold", spaceBefore=9, spaceAfter=3),
    "body": ParagraphStyle("body", parent=base, fontSize=10.5, leading=14, spaceAfter=4),
    "small": ParagraphStyle("small", parent=base, fontSize=9, leading=11.5, textColor=GREY),
    "step": ParagraphStyle("step", parent=base, fontSize=10.5, leading=14, leftIndent=18, bulletIndent=2, spaceAfter=2.5),
    "bul": ParagraphStyle("bul", parent=base, fontSize=10.5, leading=14, leftIndent=16, bulletIndent=3, spaceAfter=2),
    "cell": ParagraphStyle("cell", parent=base, fontSize=9.8, leading=12.5),
    "cellb": ParagraphStyle("cellb", parent=base, fontSize=9.8, leading=12.5, fontName="Helvetica-Bold", textColor=NAVY),
    "cover": ParagraphStyle("cover", parent=base, fontSize=30, leading=35, textColor=colors.white, fontName="Helvetica-Bold"),
    "coversub": ParagraphStyle("coversub", parent=base, fontSize=13, leading=17, textColor=colors.white),
    "toc1": ParagraphStyle("toc1", parent=base, fontSize=11, leading=17, leftIndent=0),
}


# ------------------------------------------------------------------------------------------------------------ small building blocks
def P(text, style="body"):
    return Paragraph(text, S[style])


def steps(items):
    return [Paragraph(t, S["step"], bulletText=f"{i}.") for i, t in enumerate(items, 1)]


def bullets(items):
    return [Paragraph(t, S["bul"], bulletText="-") for t in items]


def callout(title, lines, accent=GOLD, fill=colors.HexColor("#fff8e1")):
    inner = [Paragraph(f"<b>{title}</b>", S["cell"])] + [Paragraph(t, S["cell"]) for t in lines]
    t = Table([[inner]], colWidths=[CW])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), fill), ("LINEBEFORE", (0, 0), (0, -1), 4, accent), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def for_mom(text):
    return callout("FOR MOM", [text], accent=GREEN, fill=colors.HexColor("#e8f5e9"))


def table(rows, widths, header=True):
    data = [[Paragraph(str(c), S["cellb"] if (header and r == 0) else S["cell"]) for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=[w * CW for w in widths], repeatRows=1 if header else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d3e0")), ("LEFTPADDING", (0, 0), (-1, -1), 6),
             ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), PALE))
    t.setStyle(TableStyle(style))
    return t


def checklist(items):
    rows = [["[  ]", Paragraph(t, S["cell"])] for t in items]
    t = Table(rows, colWidths=[0.38 * inch, CW - 0.38 * inch])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5dce6")), ("FONTNAME", (0, 0), (0, -1), "Courier-Bold"),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


class Heading(Paragraph):
    """A chapter title: it starts a new page and feeds the table of contents."""


# ------------------------------------------------------------------------------------------------------------ the one-page picture
def wrap(text, width, font="Helvetica", size=8):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def flow_box(d, x, y, w, h, title, sub="", fill=PALE, edge=NAVY, tsize=8.6):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=edge, strokeWidth=1.1, rx=4, ry=4))
    tl, sl = wrap(title, w - 10, "Helvetica-Bold", tsize), wrap(sub, w - 10, "Helvetica", 7.2) if sub else []
    total = len(tl) * (tsize + 2) + len(sl) * 9
    yy = y + h / 2 + total / 2 - tsize
    for line in tl:
        d.add(String(x + w / 2, yy, line, fontName="Helvetica-Bold", fontSize=tsize, fillColor=NAVY, textAnchor="middle"))
        yy -= tsize + 2
    for line in sl:
        d.add(String(x + w / 2, yy - 1, line, fontName="Helvetica", fontSize=7.2, fillColor=colors.HexColor("#374151"), textAnchor="middle"))
        yy -= 9


def arrow_down(d, x, y_from, y_to, color=NAVY):
    d.add(Line(x, y_from, x, y_to + 5, strokeColor=color, strokeWidth=1.4))
    d.add(Polygon([x - 4, y_to + 6, x + 4, y_to + 6, x, y_to], fillColor=color, strokeColor=color))


def arrow_right(d, x_from, x_to, y, color=NAVY):
    d.add(Line(x_from, y, x_to - 5, y, strokeColor=color, strokeWidth=1.4))
    d.add(Polygon([x_to - 6, y - 4, x_to - 6, y + 4, x_to, y], fillColor=color, strokeColor=color))


def arrow_left(d, x_from, x_to, y, color=NAVY):
    d.add(Line(x_from, y, x_to + 5, y, strokeColor=color, strokeWidth=1.4))
    d.add(Polygon([x_to + 6, y - 4, x_to + 6, y + 4, x_to, y], fillColor=color, strokeColor=color))


def one_page_picture():
    dw, dh = CW, 7.75 * inch
    d = Drawing(dw, dh)
    d.add(String(0, dh - 12, "THE LIFE OF A PAYMENT, FROM INVOICE TO BANK", fontName="Helvetica-Bold", fontSize=12, fillColor=NAVY))
    d.add(String(0, dh - 25, "Follow the boxes down. The small grey text names the page and the button.", fontName="Helvetica", fontSize=8, fillColor=GREY))
    lx, lw, bh = 0, 285, 46
    steps_main = [
        ("1  An invoice arrives from a supplier", "Add data > Invoices / credit memos. One batch (order statement) per supplier holds its invoices.", PALE),
        ("2  Issue the checks (post-dated is fine)", "Order statements > open the batch > Checks > Issue checks, then 'Park Metrobank checks'. A parked check has no number yet.", PALE),
        ("3  Send the checks to Chrysanth and print", "Payment run stage 2: 'Make Chrysanth file'. Print in Chrysanth, then stage 3: 'Mark as printed'.", PALE),
        ("4  Give each printed check its number", "Payment run stage 4: 'Assign numbers' (pick the leaves from the check booklet).", PALE),
        ("5  Print the batch statements, hand out the checks", "Payment run stage 5: 'Print statements' (file copy + supplier copy).", PALE),
        ("6  The supplier deposits; the bank clears the check", "Nothing to do. The check stays 'not yet cleared' until the bank shows it.", colors.HexColor("#e3f2fd")),
        ("7  Import the bank statement", "Bank and cash > Import a statement. Checks are matched by number; anything odd is 'Need a look'.", PALE),
        ("8  Resolve what needs a look", "Bank and cash > Reconciliation. The bank is the truth: differences become tagged adjustments, the books are never edited.", PALE),
        ("9  Weekly and month-end", "Check the lists on pages 'Weekly and month-end', save the reports, backup runs by itself at 6 PM.", colors.HexColor("#e8f5e9")),
    ]
    top = dh - 40
    y = top - bh
    ys = []
    for title, sub, fill in steps_main:
        flow_box(d, lx, y, lw, bh, title, sub, fill=fill)
        ys.append(y)
        if len(ys) > 1:
            arrow_down(d, lx + lw / 2, ys[-2], y + bh)
        y -= bh + 12
    # side lane, stacked from the top of step 2 down: Mom, mistakes, other payees, guarantee checks
    rx, rw = 322, 198
    cy = ys[1] + bh
    specs = [
        (92, "Mom hand-writes an urgent check", "Photo to Messenger. Add data > Handwritten check: type the real number, date and amount (+ the invoice if new). Saved as already printed - it skips steps 3 and 4.", colors.HexColor("#e8f5e9"), GREEN),
        (80, "A check was wrong?", "Void it (Order statements > Checks > Void a check). 'Void and reissue' can take the new check's number at the same time. A torn leaf: Checks > Log a spoiled check.", colors.HexColor("#ffebee"), RED),
        (104, "The other three kinds of payee", "Private lender: Lenders > loan note > schedule > checks (same steps 3 to 8). Bank loan: Bank loans > installments (paid by the bank, no check). Credit card: Add data > Credit card statement, pay by the due date.", colors.HexColor("#fff8e1"), GOLD),
        (76, "Undated guarantee checks", "Given to a lender as security, not a payment. Add data > Guarantee check. Return it when the loan is settled (Checks > Guarantee checks).", colors.HexColor("#ede7f6"), colors.HexColor("#5e35b1")),
    ]
    for n, (h, title, sub, fill, edge) in enumerate(specs):
        flow_box(d, rx, cy - h, rw, h, title, sub, fill=fill, edge=edge, tsize=8.4)
        if n == 0:
            arrow_left(d, rx, lx + lw + 2, cy - h / 2, GREEN)
        cy -= h + 12
    return d


# ------------------------------------------------------------------------------------------------------------ document
class Doc(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(path, pagesize=LETTER, leftMargin=M, rightMargin=M, topMargin=0.75 * inch, bottomMargin=0.7 * inch,
                         title="Slick's Tire Supply - AP System Manual", author="Slick's Tire Supply")
        frame = Frame(M, 0.7 * inch, CW, H - 1.45 * inch, id="f", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self._decorate)])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFillColor(NAVY)
            canvas.rect(0, H - 0.42 * inch, W, 0.42 * inch, fill=1, stroke=0)
            canvas.setFillColor(GOLD)
            canvas.rect(0, H - 0.46 * inch, W, 0.04 * inch, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont("Helvetica-Bold", 9)
            canvas.drawString(M, H - 0.28 * inch, "Slick's Tire Supply  |  AP System Manual")
            canvas.setFont("Helvetica", 8.5)
            canvas.setFillColor(GREY)
            canvas.drawString(M, 0.4 * inch, f"Made {date.today():%B %d, %Y}. If a screen looks different from this manual, the manual is older - ask to have it remade.")
            canvas.drawRightString(W - M, 0.4 * inch, f"Page {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Heading):
            text = flowable.getPlainText()
            key = f"h{self.page}-{abs(hash(text))}"
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, 0, 0)
            self.notify("TOCEntry", (0, text, self.page, key))


def cover():
    logo = os.path.join(HERE, "assets", "slicks_logo.jpg")
    left = Image(logo, width=1.1 * inch, height=1.1 * inch) if os.path.exists(logo) else ""
    head = [P("The AP System Manual", "cover"), Spacer(1, 6), P("Slick's Tire Supply", "coversub"),
            P("How the whole system works - every day, every week, every month, and the special cases.", "coversub")]
    t = Table([[left, head]], colWidths=[1.4 * inch, CW - 1.4 * inch])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 18),
                           ("TOPPADDING", (0, 0), (-1, -1), 28), ("BOTTOMPADDING", (0, 0), (-1, -1), 28), ("LINEBELOW", (0, 0), (-1, -1), 6, GOLD)]))
    return t


def build(path=OUT):
    doc = Doc(path)
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"]]
    story = [cover(), Spacer(1, 16)]
    story += [P("<b>How to use this manual</b>", "h2")]
    story += bullets([
        "Turn to <b>The one-page picture</b> first. It shows the whole journey of a payment on one page.",
        "Pages marked <b>FOR MOM</b> in green are the ones you need for handwritten checks.",
        "Each job has its own page with numbered steps. Do the steps in order; the program refuses what is wrong and tells you why.",
        "Nothing you do in the program is ever really lost: every change is written to the audit log, and the database is backed up every day at 6 PM.",
    ])
    story += [Spacer(1, 10), P("<b>Contents</b>", "h2"), toc]
    for sec in SECTIONS:
        story.append(PageBreak())
        story.append(Heading(sec["title"], S["h1"]))
        story.extend(sec["build"]())
    doc.multiBuild(story)
    return path


# ------------------------------------------------------------------------------------------------------------ the chapters
def ch_picture():
    return [P("Read it from top to bottom. The green box is where your mother's handwritten checks join the journey; the red box is for mistakes.", "small"), Spacer(1, 4), one_page_picture()]


def ch_map():
    rows = [["Menu group", "Page", "Open it when you want to..."],
            ["Daily work", "Payment run", "See what is waiting today: batches that need checks, checks to send to Chrysanth, runs to mark printed, checks to number, statements to print. Also the booklet check."],
            ["", "Add data", "Record anything new in one place: invoices and credit memos, a credit card statement, loans, records and checkbook, a <b>handwritten check</b>, a <b>guarantee check</b>."],
            ["", "Order statements", "Work inside one supplier batch: its invoices, credit memos and checks; print its statement; void or correct a check."],
            ["", "Checks and checkbook", "Make the Chrysanth file, see batch runs, assign check numbers, log a spoiled check, see voided / replaced / spoiled checks and the guarantee checks."],
            ["", "Bank and cash", "Import the bank statement, reconcile, see checks not yet cleared, the cash forecast, money in, unusual items."],
            ["", "Check-date planner", "Ask the program for the best dates for checks so cash stays safe."],
            ["Other payables", "Lenders", "Private lender loan notes: terms, repayment schedule, checks, editing details."],
            ["", "Bank loans", "Bank loans and their repayment schedules; fill in the real details of the old ones."],
            ["", "Credit cards", "Card statements, cards and cardholders, forecast of card dues."],
            ["Records and reports", "Dashboard", "Today: key figures, ONE list of what needs attention, the payment calendar and the next 7 days."],
            ["", "Creditors", "The list of suppliers, lenders and banks (name, TIN, contact, terms)."],
            ["", "Analytics", "Charts: where the money goes, who we owe, how we pay, cost of borrowing, cash ahead."],
            ["", "Reports", "Printable and downloadable reports: Overview, Schedule of payments, Cleared checks, histories, rollups, <b>Lists</b> (who are our suppliers, lenders, banks, batches, cards, booklets... with one download for all) and <b>Decision reports</b> (are we paying late, which debt costs most, where did the money go...), plus 'My reports'."],
            ["System", "Settings", "Change the numbers the program uses (stale days, cash buffer...) and the keyboard shortcuts."],
            ["", "Admin and audit", "Check booklets, backup and restore, the audit log, printer set-up."],
            ["", "Data health", "A scan for things that look wrong: double numbers, odd dates, unmatched bank lines."]]
    out = [table(rows, [0.17, 0.2, 0.63]), Spacer(1, 8),
           KeepTogether([P("Keyboard shortcuts", "h2"),
                         table([["Keys", "What it does"], ["Alt + 1 ... 9, 0", "Opens menu page 1 to 10 (hover a menu item to see its key)"], ["/", "Opens Search everything (checks, invoices, batches, loans, creditors, cards)"],
                                ["Alt + P", "Presses the screen's main Park / Save / Add button"], ["Alt + N", "Opens the New... form (new batch, loan note, bank loan)"],
                                ["Enter / Esc", "In a confirmation window: Enter = Yes, Esc = go back"]], [0.25, 0.75]),
                         P("You can change these keys in Settings > Keyboard shortcuts.", "small")])]
    return out


def ch_daily():
    out = [P("Start the day on <b>Payment run</b>. Every line is a stage; a green line means nothing is waiting there.")]
    out += [table([["Stage", "What it means", "Your action"],
                   ["1 - Checks", "A batch has invoices that are not yet covered by checks.", "Press <b>Issue checks</b>; it opens that batch on the Checks tab."],
                   ["2 - Parked", "Checks are issued but not yet sent to Chrysanth.", "Press <b>Make Chrysanth file</b> (Checks and checkbook page), then print them in Chrysanth."],
                   ["3 - Exported", "A Chrysanth run was made but not marked printed.", "After printing in Chrysanth, press <b>Mark ... as printed</b> and confirm."],
                   ["4 - Numbers", "Printed checks still have no check number.", "Press <b>Assign numbers</b>; choose the leaves from the booklet, in the same order as printed."],
                   ["5 - Statements", "Batches changed since the last print.", "Press <b>Print statements</b>: one combined PDF, or the thermal printer."]], [0.16, 0.38, 0.46])]
    out += [P("The Dashboard", "h2"),
            P("The Dashboard shows only <b>today</b>: the bank balance, what is due in 7 days, the lowest balance expected in 30 days, how many Payment-run steps wait, and "
              "<b>Needs attention</b>. Under it is one list with every problem the program knows about - each line has an <b>Open</b> button that takes you to the page that fixes it. "
              "Red is an error, orange a warning, blue is only information. Below that: the payment calendar (green = already paid, blue = still to pay, outlined = today) and the next 7 days."),
            P("The booklet check", "h2"),
            P("Your mother's booklet is registered as 'Mom - urgent'. On the Payment run page type the last check number visible on her latest photo ('Mom's booklet is up to no. ___'). "
              "The program lists every leaf up to that number that is not recorded, spoiled or voided, so a forgotten photo cannot hide.")]
    out.append(for_mom("Every leaf of your booklet needs a photo - used, torn or cancelled. Spoiled and cancelled leaves are logged too, so the booklet always adds up."))
    return out


def ch_record_supplier():
    out = [P("A. A new supplier invoice with post-dated checks", "h2")]
    out += steps(["Add data > <b>Invoices / credit memos</b>. Pick the supplier and the batch (or 'New batch').",
                  "Type the invoice number, date received, amount and any 'less %'. Save. The program warns if the same invoice looks entered twice.",
                  "Open the batch (Order statements) > <b>Checks</b> > <b>Issue checks</b>. Choose how many checks or the dates, and the amounts. The total must equal the batch's balance.",
                  "Press <b>Park Metrobank checks</b> and confirm. The batch becomes 'Issued' and gets its permanent OS number.",
                  "Continue on the Payment run page from stage 2."])
    out += [callout("Remember", ["A check can never be for more than the batch still owes. A batch locks by itself once its checks cover the full amount, and opens again by itself when you void or replace a check. To change an invoice after that, use Order statements > Edit this batch, give a short reason, change it, and it closes again when you save."])]
    out += [P("B. Credit memos and corrections", "h2")]
    out += bullets(["<b>Credit memo</b> (supplier gives a discount or return): Add data > Invoices / credit memos > credit memo. It lowers what the batch owes.",
                    "<b>Edit parked checks</b>: while a check has no number you can change its date and amount.",
                    "<b>Correct an active check</b>: once a check is numbered or printed, use Correct a check (number, date or amount). It needs a reason and is logged. Correcting an amount also corrects the invoice it pays.",
                    "<b>Un-park</b>: removes checks that were typed by mistake before they are printed."])
    out += [P("C. Voiding or replacing a check", "h2")]
    out += steps(["Order statements > open the batch > Checks > <b>Void a check</b>.",
                  "Pick the check. Tick <b>Reissue</b> if a replacement is needed. The new check starts with the same date and amount.",
                  "If the replacement was already written by hand, type its number in <b>New check number</b>: it is saved at once as a handwritten, printed check. Leave it blank to park it instead.",
                  "Give a reason (it is kept) and confirm. The old check becomes 'Replaced' and points to the new one."])
    out += [P("D. A spoiled leaf (torn, jammed, misprinted)", "h2")]
    out += steps(["Checks and checkbook > <b>Log a spoiled check</b>. Type the number and a reason.", "The leaf now counts as accounted for in its booklet and its number can never be used again."])
    return out


def ch_handwritten():
    out = [for_mom("This is the page for the photos you send. The owner types them in; this tells you what to put in each photo so nothing is missed."),
           P("What the photo must show", "h2")]
    out += bullets(["The <b>check number</b> printed on the check (all digits), the <b>date</b> and the <b>amount</b>.", "The <b>payee</b> (supplier or lender) and, if it is a new invoice, the <b>invoice number</b>.",
                    "If it <b>replaces</b> another check, say which one (its number or amount).", "A torn or cancelled leaf: send a photo too, so it can be logged as spoiled."])
    out += [P("Recording it (Add data > Handwritten check)", "h2")]
    out += steps(["Choose <b>A supplier</b> or <b>A private lender (loan note)</b>, and the name.",
                  "Supplier: pick the batch, or tick <b>This is a new invoice</b> and type its number, date, amount and less %. Lender: pick the loan note - the page shows what it already has and what is left to cover.",
                  "If this replaces an earlier check, choose it first in <b>Does this replace an earlier check?</b> - the date and amount are filled in for you. Change them if they differ.",
                  "In the table type the <b>check number</b>, date and amount (one row per check).",
                  "Press <b>Record the handwritten check(s)</b> and confirm. The check is saved with its number, marked printed and handwritten, tied to the invoice, and never goes to Chrysanth."])
    out += [callout("Good to know", ["A handwritten check skips payment-run stages 2 to 4 because it is already numbered and printed.",
                                     "If the number is already used or was logged as spoiled, the program refuses and says so before saving anything.",
                                     "A check dated in the future leaves the invoice 'Unpaid' until the check date passes - that is normal."])]
    out += [P("Old (MS Access) checks", "h2"),
            P("Checks written before the system started live in batches called LEGACY-MS-ACCESS-... Each has one line called <b>Beginning balance (from MS Access)</b>. A handwritten check added to such a "
              "batch gets its own beginning-balance line, so the batch still balances. These lines are not purchases and are left out of the spending and ageing figures.")]
    return out


def ch_other_payees():
    out = [P("Private lenders (Lenders page)", "h2")]
    out += steps(["Add the lender on the Creditors page if new. Lenders > <b>New loan note</b>: lender, issue date, principal, monthly interest rate.",
                  "Add the repayment schedule (installments: due date, principal, interest) or import it from a spreadsheet.",
                  "Issue the PDC for each installment (Checks tab). Then follow the same steps as supplier checks: Chrysanth, print, number.",
                  "A loan note gets its permanent LN number with <b>Promote to real LN No.</b>",
                  "Wrong lender, date or number? Open the note > <b>Edit loan note details</b>. Wrong principal or rate? <b>Edit loan note terms</b>."])
    out += [P("Bank loans (Bank loans page)", "h2")]
    out += bullets(["<b>New bank loan</b>: bank, loan reference, account, dates, principal. Then enter the repayment schedule (due date and amount). The bank takes the payment automatically; no check is issued.",
                    "The loans brought over from the old system are labelled <b>Beginning balance (from MS Access)</b>. Open one > <b>Edit bank loan details</b> and enter its real start date and principal: the label disappears.",
                    "An installment whose due date has passed counts as paid."])
    out += [P("Credit cards (Credit cards page)", "h2")]
    out += steps(["Credit cards > Statements > <b>Add statement</b> once a month for each card, when the statement arrives. The statement month is the month of the statement date.",
                  "Enter the amounts exactly as printed. Pay by the due date (a check, or the bank). A card with no bill that month: press <b>No due this month</b>.",
                  "The Dashboard list warns if a statement seems overlooked."])
    out += [P("Lessors (rent)", "h2"),
            P("A lessor is someone we pay rent to. It is a creditor with <b>Type = Lessor</b> (Creditors page). Rent is paid with checks <b>like a loan</b> - there is no contract record: on the Lenders page choose "
              "'Lender or lessor', make the note with no interest, and issue the rent checks as usual (or record handwritten ones). The note shows as a <b>Lease</b>, rent is not counted as debt in the debt "
              "reports, and Reports > Lists has a Lessors list.")]
    out += [P("Guarantee checks (held by lenders)", "h2")]
    out += bullets(["A lender sometimes keeps an undated check as security. It is <b>not a payment</b>. Add data > <b>Guarantee check</b>: number, lender, optional loan note, amount if known, date given.",
                    "It is never counted as money going out. The Dashboard shows how much lenders hold. If the bank ever pays one, the program raises an alert.",
                    "When the loan is settled and the lender returns the check: Checks and checkbook > Guarantee checks > <b>Returned</b> (with the date). There is an Undo."])
    return out


def ch_bank():
    out = [P("The bank is the truth. If the bank statement and the program disagree, the program is corrected by a <b>tagged adjustment</b> - the recorded check is never edited.")]
    out += steps(["Download the Metrobank statement (Excel or CSV is fastest; PDF works).",
                  "Bank and cash > <b>Import a statement</b>, drop the file, check the preview and confirm. The same file is never imported twice and overlapping periods are fine.",
                  "Bank and cash > <b>Reconciliation</b>: lines that match one of our checks are cleared automatically. The rest are 'Need a look' - resolve each one (link it to a check, tag it, or accept the suggestion).",
                  "Bank and cash > <b>Checks not yet cleared</b>: checks past their date that the bank has not paid. If the payee gave a check back and was paid in cash, use <b>Settled outside the bank</b> with a reason and date (it can be undone).",
                  "Weekly, look at the <b>Cash forecast</b> to see whether the balance stays above the cash buffer."])
    out += [callout("What 'Need a look' usually is", ["A bank line for one of our check numbers that is not in the system - probably a handwritten check not yet recorded. Record it (Add data > Handwritten check).",
                                                      "A large debit nobody explained. Tag it with what it was.", "A guarantee check the bank paid - tell the owner immediately."])]
    return out


def ch_checklists():
    out = [P("Every day (5 minutes)", "h2"),
           checklist(["Open the Dashboard. Read the <b>Needs attention</b> list; press Open on anything red.", "Payment run: work stages 1 to 5 until they are green.",
                      "Record every photo from Mom (Add data > Handwritten check). Type the booklet's last number in the Payment run booklet box."]),
           P("Every week", "h2"),
           checklist(["Import the newest bank statement; clear the 'Need a look' lines.", "Enter any credit card statements that arrived.", "Open Data health; fix or mark each finding.",
                      "Look at checks not yet cleared and at the stale-check warning.", "Check the Cash forecast against the cash buffer.", "Admin and audit > Check booklets > Booklet gap audit: every leaf accounted for."]),
           P("Every month-end", "h2"),
           checklist(["Reports > <b>Schedule of payments</b> and <b>Overview</b>: save them as PDF.", "Reports > Master rollups: lender and bank-loan balances; compare with your own records.",
                      "Analytics > Where the money goes: note the month's total paid.", "Run <b>restore_test.bat</b> once a month. It must end in PASSED.",
                      "Check that the daily backup files are in the Google Drive folder (newest 30 are kept)."])]
    return out


def ch_wrong():
    qa = [["Question", "Answer"],
          ["I typed a wrong amount on a check.", "Not numbered yet: Order statements > Checks > Edit parked checks. Numbered or printed: Correct an active check (needs a reason). The invoice follows the correction."],
          ["The batch does not balance (a few pesos left).", "Compare the checks with the invoices; usually one check was typed a little short. Correct that check."],
          ["A check shows in the bank but not in the program.", "Probably a handwritten check not yet recorded. Add data > Handwritten check."],
          ["'Possible double recording' appears.", "The same supplier, number or amount was seen before. Read it; if it is really different, continue."],
          ["The program says the batch is closed (locked).", "Order statements > Edit this batch, give a reason, make the change. It closes again when you save."],
          ["What does 'Beginning balance (from MS Access)' mean?", "Old checks brought from the previous system with no invoice detail. It makes the batch add up and is not a purchase."],
          ["The calendar day is green / blue / grey.", "Green = already paid, blue = still to pay (darker = more), outlined = today, grey = holiday or a marked day."],
          ["A check was never cashed.", "After the stale-check days (Settings, normally 180) the Dashboard warns. Ask the payee; void and reissue, or Settled outside the bank."],
          ["I forgot to record a photo from Mom.", "The booklet box on Payment run lists the missing numbers. Record each or log it as spoiled."],
          ["I made a mistake in a loan or bank loan.", "Open it and use Edit loan note details / Edit bank loan details. Every change is logged and asks first."],
          ["I want to undo something.", "Most things have an Undo or a Reopen. Otherwise the audit log (Admin and audit) shows exactly what was changed and when."]]
    return [table(qa, [0.34, 0.66])]


def ch_rules():
    return [P("These are the rules the program keeps for you, and why it sometimes says no."),
            table([["Rule", "Why"],
                   ["Checks can never add up to more than the batch (or loan) owes.", "To stop overpaying a supplier or lender."],
                   ["A check number exists once - a check or a spoiled leaf, never both.", "So two checks never share a number."],
                   ["An invoice number is unique per supplier.", "To catch double entry."],
                   ["Due today still counts as owed.", "The check has not left yet."],
                   ["An invoice is Paid only after its check date has passed.", "A post-dated check is a promise, not a payment."],
                   ["A bank-loan installment or card statement whose due date has passed counts as paid.", "The bank takes them automatically."],
                   ["The bank statement is the truth.", "Differences become tagged adjustments; the books are never edited."],
                   ["Nothing is erased without a trace.", "Every change and delete is written to the audit log with old and new values."],
                   ["Confirmation windows for risky changes.", "Voiding, correcting, deleting rows and changing terms ask 'Are you sure?' first."],
                   ["Cancelled loans and notes are permanent.", "They stay on record for history."]], [0.5, 0.5])]


def ch_settings_backup():
    out = [P("Settings (System menu)", "h2"),
           P("Change the numbers the program uses: when a check is called stale, how old a bank balance may be, the cash buffer, the heavy card week, the alert level for one big supplier, and the planner limits. "
             "Each has a plain explanation and a 'Back to default' button; every change is logged. The second tab changes the keyboard shortcuts."),
           P("Backups and restore", "h2")]
    out += bullets(["The database is backed up <b>every day at 6 PM</b> to the Google Drive folder AP_Backups (the newest 30 are kept). <b>run_backup.bat</b> backs up right now.",
                    "Once a month run <b>restore_test.bat</b>: it proves the newest backup can rebuild the system and must end in PASSED.",
                    "If the laptop is lost: install Python 3.12 and the packages, copy the newest backup to the data folder, and start the program (see README).",
                    "After a program update that says 'database update needed': close the program and run <b>update_database.bat</b>."])
    out += [P("Where things live", "h2"),
            table([["What", "Where"], ["The live database", "C:\\Slicks\\Data\\ap_disbursement.db (on the laptop, not on Google Drive)"], ["The program", "C:\\Slicks\\AP_System"],
                   ["Start / restart / stop", "start_live.bat, restart_live.bat, stop_live.bat in the program folder. The program opens at http://localhost:8501 - only this laptop can open it."],
                   ["What changed and when", "WHATS_NEW.md (the program also shows the newest note once per session)"]], [0.3, 0.7])]
    return out


def ch_words():
    words = [["Word", "Meaning"],
             ["Batch / Order statement (OS)", "One supplier's folder of invoices and the checks that pay them. Its number looks like 2026 - OS 0012."],
             ["LN / Loan note", "A private loan from a lender, with its repayment schedule. Numbered 2026 - LN 0001."],
             ["PDC", "Post-dated check: a check dated in the future."],
             ["Parked", "A check that is issued in the program but has no number and is not printed yet."],
             ["Chrysanth", "The check printing program. The Chrysanth file carries the parked checks to it."],
             ["Handwritten check", "A check written by hand (usually by Mom). Recorded with its real number and marked already printed."],
             ["Spoiled / voided / replaced", "Spoiled = a leaf that cannot be used. Voided = a check cancelled. Replaced = voided and another check took its place."],
             ["Not yet cleared (float)", "A check past its date that the bank has not paid yet."],
             ["Stale check", "A check older than the stale days (about 6 months) that is still not cleared."],
             ["Guarantee check", "An undated check held by a lender as security - not a payment."],
             ["Beginning balance", "The line that stands for old checks brought from MS Access."],
             ["Reconcile", "Compare the bank statement with the program and explain every difference."],
             ["Cash buffer", "The minimum balance you want to keep in the bank (Settings)."]]
    out = [table(words, [0.3, 0.7]), P("I want to... go to...", "h2"),
           table([["I want to...", "Go to..."], ["record a supplier invoice", "Add data > Invoices / credit memos"], ["record a photo from Mom", "Add data > Handwritten check"],
                  ["send checks to print", "Payment run > stage 2"], ["give checks their numbers", "Payment run > stage 4"], ["void or replace a check", "Order statements > Checks > Void a check"],
                  ["log a torn leaf", "Checks and checkbook > Log a spoiled check"], ["import the bank statement", "Bank and cash > Import a statement"],
                  ["see what is due this week", "Dashboard > Next 7 days"], ["see all checks of a supplier", "Reports > Supplier - invoice and check history"], ["list all suppliers / lenders / banks", "Reports > Lists"], ["answer a question about spending, late payments or cost of debt", "Reports > Decision reports (has a question guide)"],
                  ["change a loan's details", "Lenders / Bank loans > Edit ... details"], ["change a setting or shortcut", "Settings"], ["find anything", "Press / (Search everything)"]], [0.5, 0.5])]
    return out


SECTIONS = [
    {"title": "The one-page picture", "build": ch_picture},
    {"title": "The map of the program", "build": ch_map},
    {"title": "Every day", "build": ch_daily},
    {"title": "Recording supplier invoices and checks", "build": ch_record_supplier},
    {"title": "Handwritten checks from photos", "build": ch_handwritten},
    {"title": "Lenders, bank loans, credit cards and guarantee checks", "build": ch_other_payees},
    {"title": "The bank statement", "build": ch_bank},
    {"title": "Daily, weekly and month-end checklists", "build": ch_checklists},
    {"title": "When something looks wrong", "build": ch_wrong},
    {"title": "The rules the system keeps", "build": ch_rules},
    {"title": "Settings, backups and where things live", "build": ch_settings_backup},
    {"title": "Words and a quick index", "build": ch_words},
]

if __name__ == "__main__":
    print("Built", build())
