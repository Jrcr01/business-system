"""PDF "Statement of Account" exports for the Reports tab's per-entity history drill-downs
(Supplier, Private Lender, Bank Loan, Credit Card) -- added 2026-09-14 per the user's own framing
of these reports: "the purpose of these reports are whenever i am asked for history of
transactions... include pdf reports just a simple reports (like it is a simple statement of
account)". Pure presentation layer: every function here takes DataFrames already filtered by
app.py (same date-range/status filters the on-screen tables use) and lays them out -- no query
logic lives here, matching the split already used by payment_schedule_report_service.py over
engines.reports_service. Visual style (title band, section table, ACCENT color) is deliberately
the same recipe as batch_statement_service.py / payment_schedule_report_service.py rather than a
third look invented for this feature; each PDF service file in this app keeps its own small copy
of these constants/helpers rather than sharing a base module, which is consistent with the
existing pattern here.
"""
import io
from datetime import datetime
from xml.sax.saxutils import escape as _esc

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from engines.clock import manila_now

MIN_FONT_PT = 9.0  # nothing in these reports is ever printed smaller than this

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
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ACCENT_LIGHT]),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
]


def _money(v) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def _styles():
    styles = getSampleStyleSheet()
    return {
        "base": styles,
        "grey": ParagraphStyle("Grey", parent=styles["Normal"], fontSize=8.5, textColor=GREY),
        "grey_small": ParagraphStyle("GreySmall", parent=styles["Normal"], fontSize=7.5, textColor=GREY),
        "section": ParagraphStyle("Section", parent=styles["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=12, spaceAfter=5),
        "empty": ParagraphStyle("Empty", parent=styles["Normal"], fontSize=8.5, textColor=GREY, spaceAfter=6),
    }


def _title_band(entity_label: str, entity_name: str) -> Table:
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    band = Table(
        [[Paragraph("STATEMENT OF ACCOUNT", title_style), Paragraph(_esc(f"{entity_label.upper()}: {entity_name}"), sub_style)]],
        colWidths=[CONTENT_WIDTH * 0.55, CONTENT_WIDTH * 0.45],
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


def _period_text(date_from, date_to) -> str:
    if date_from and date_to:
        return f"Period: {date_from} to {date_to}"
    return "Period: all dates on file"


def _section_table(headers: list, rows: list, col_widths: list, right_cols: tuple = ()) -> Table:
    """Table sized to its content: col_widths are only the preferred proportions. If any cell is
    too wide for its column, columns are re-fitted to the text and, if needed, the font shrinks
    (never below 9pt) and long text wraps -- so nothing overlaps its neighbour."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    total = sum(col_widths)
    pad = 12
    size = MIN_FONT_PT

    def needs(sz):
        return [max([stringWidth(str(h), "Helvetica-Bold", sz)] + [stringWidth(str(r[c]), "Helvetica", sz) for r in rows]) + pad
                for c, h in enumerate(headers)]

    need = needs(size)
    if all(n <= w for n, w in zip(need, col_widths)):
        t = Table([headers] + rows, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle(SECTION_TABLE_STYLE + [("FONTSIZE", (0, 0), (-1, -1), size)] + [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols]))
        return t
    while sum(need) > total and size > MIN_FONT_PT:
        size -= 0.25
        need = needs(size)
    if sum(need) <= total:
        spare = total - sum(need)
        widths, data = [n + spare * (n / sum(need)) for n in need], [headers] + rows
    else:
        # Numbers and dates never wrap: they keep the width they need, and only the text columns
        # (names, references) give up room and wrap onto a second line.
        is_text = [any(any(ch.isalpha() for ch in str(r[c])) for r in rows) and need[c] > 130 for c in range(len(headers))]
        fixed = sum(n for n, t_ in zip(need, is_text) if not t_)
        flex = sum(n for n, t_ in zip(need, is_text) if t_) or 1
        room = max(total - fixed, 60)
        widths = [n if not t_ else n * room / flex for n, t_ in zip(need, is_text)]
        cs = ParagraphStyle("SecCell", parent=getSampleStyleSheet()["Normal"], fontSize=size, leading=size + 1.5)
        data = [headers] + [[Paragraph(_esc(str(x)), cs) if is_text[c] else x for c, x in enumerate(r)] for r in rows]   # escaped: 'S&P' must not break the PDF
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle(SECTION_TABLE_STYLE + [("FONTSIZE", (0, 0), (-1, -1), size)] + [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols]))
    return t


def _totals_block(totals: list) -> Table:
    label_style = ParagraphStyle("TotLabel", fontSize=9.5, textColor=ACCENT, fontName="Helvetica-Bold")
    val_style = ParagraphStyle("TotVal", fontSize=9.5, textColor=ACCENT, fontName="Helvetica-Bold", alignment=2)
    rows = [[Paragraph(label, label_style), Paragraph(f"PHP {_money(amount)}", val_style)] for label, amount in totals]
    t = Table(rows, colWidths=[CONTENT_WIDTH * 0.75, CONTENT_WIDTH * 0.25])
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 1.0, ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _doc(buffer) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )


def _finish(doc, story) -> bytes:
    st = _styles()
    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Generated {manila_now().strftime('%m/%d/%Y %H:%M')}", st["grey_small"]))
    doc.build(story)


def _invoices_table(df, with_batch_col: bool = True):
    headers = ["Doc Ref", "Invoice Date", "Due Date", "Gross", "Net", "Status"] + (["Batch"] if with_batch_col else [])
    widths = [CONTENT_WIDTH * w for w in ((0.16, 0.14, 0.14, 0.14, 0.14, 0.12, 0.16) if with_batch_col else (0.18, 0.16, 0.16, 0.16, 0.16, 0.18))]
    rows = []
    for r in df.itertuples():
        row = [r.doc_ref, str(r.invoice_date), str(r.due_date), _money(r.gross_amount), _money(r.amount), r.status]
        if with_batch_col:
            row.append(r.statement_ref or "—")
        rows.append(row)
    return _section_table(headers, rows, widths)


def _checks_table(df, with_batch_col: bool = True, batch_col_label: str = "Batch"):
    headers = ["Check Date", "Check #", "Amount", "Status", "Cleared"] + ([batch_col_label] if with_batch_col else [])
    widths = [CONTENT_WIDTH * w for w in ((0.15, 0.2, 0.16, 0.12, 0.15, 0.22) if with_batch_col else (0.2, 0.26, 0.2, 0.16, 0.18))]
    ref_field = "note_ref" if batch_col_label == "Loan Note" else "statement_ref"
    rows = []
    for r in df.itertuples():
        cleared = getattr(r, "bank_cleared_date", None)
        row = [str(r.check_date), r.check_number or "—", _money(r.amount), r.status, str(cleared)[:10] if cleared and str(cleared) not in ("nan", "None", "NaT") else "—"]
        if with_batch_col:
            row.append(getattr(r, ref_field, None) or "—")
        rows.append(row)
    return _section_table(headers, rows, widths)


def generate_supplier_statement_pdf(supplier_name: str, statements_df, invoices_df, checks_df, date_from=None, date_to=None, group_by_batch: bool = False) -> bytes:
    """Simple statement of account for one supplier -- combines every batch's invoices and
    checks into one statement by default (group_by_batch=False), or breaks it into a section
    per order statement with its own subtotal when the user wants a per-batch breakdown
    instead ("combine all batches... make it have an option")."""
    buffer = io.BytesIO()
    doc = _doc(buffer)
    st = _styles()
    story = [_title_band("Supplier", supplier_name), Spacer(1, 6), Paragraph(_period_text(date_from, date_to), st["grey"]), Spacer(1, 8)]

    inv_live = invoices_df[invoices_df["status"] != "Cancelled"] if not invoices_df.empty else invoices_df
    chk_live = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df

    if group_by_batch and not statements_df.empty:
        for batch in statements_df.itertuples():
            ref = batch.statement_ref
            story.append(Paragraph(_esc(f"Batch {ref} — {batch.status}"), st["section"]))
            b_inv = invoices_df[invoices_df["statement_ref"] == ref] if not invoices_df.empty else invoices_df
            b_chk = checks_df[checks_df["statement_ref"] == ref] if not checks_df.empty else checks_df
            story.append(Paragraph("Invoices", ParagraphStyle("Sub", fontSize=9, fontName="Helvetica-Bold", spaceAfter=3)))
            story.append(_invoices_table(b_inv, with_batch_col=False) if not b_inv.empty else Paragraph("No invoices in this period.", st["empty"]))
            story.append(Spacer(1, 4))
            story.append(Paragraph("Checks", ParagraphStyle("Sub", fontSize=9, fontName="Helvetica-Bold", spaceAfter=3)))
            story.append(_checks_table(b_chk, with_batch_col=False) if not b_chk.empty else Paragraph("No checks in this period.", st["empty"]))
            b_inv_live = b_inv[b_inv["status"] != "Cancelled"] if not b_inv.empty else b_inv
            b_chk_live = b_chk[b_chk["status"] == "Active"] if not b_chk.empty else b_chk
            story.append(Spacer(1, 4))
            story.append(_totals_block([
                ("Batch invoiced (excl. cancelled)", b_inv_live["amount"].sum() if not b_inv_live.empty else 0),
                ("Batch paid (active checks)", b_chk_live["amount"].sum() if not b_chk_live.empty else 0),
            ]))
            story.append(Spacer(1, 10))
    else:
        story.append(Paragraph("Invoices", st["section"]))
        story.append(_invoices_table(invoices_df) if not invoices_df.empty else Paragraph("No invoices in this period.", st["empty"]))
        story.append(Paragraph("Checks issued", st["section"]))
        story.append(_checks_table(checks_df) if not checks_df.empty else Paragraph("No checks in this period.", st["empty"]))

    story.append(Spacer(1, 6))
    story.append(_totals_block([
        ("TOTAL INVOICED (excl. cancelled)", inv_live["amount"].sum() if not inv_live.empty else 0),
        ("TOTAL PAID (active checks)", chk_live["amount"].sum() if not chk_live.empty else 0),
    ]))
    _finish(doc, story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_lender_statement_pdf(lender_name: str, notes_df, checks_df, date_from=None, date_to=None, group_by_note: bool = False) -> bytes:
    """Simple statement of account for one private lender -- combines every loan note's checks
    into one statement by default, or breaks it into a section per note when requested."""
    buffer = io.BytesIO()
    doc = _doc(buffer)
    st = _styles()
    story = [_title_band("Lender", lender_name), Spacer(1, 6), Paragraph(_period_text(date_from, date_to), st["grey"]), Spacer(1, 8)]

    chk_live = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df

    if group_by_note and not notes_df.empty:
        for note in notes_df.itertuples():
            ref = note.note_ref
            story.append(Paragraph(_esc(f"Loan Note {ref} — {note.status}"), st["section"]))
            n_chk = checks_df[checks_df["note_ref"] == ref] if not checks_df.empty else checks_df
            story.append(_section_table(
                ["Issue Date", "Principal", "Interest", "Total Payable", "Outstanding"],
                [[str(note.issue_date), _money(note.principal_amount), _money(note.total_interest), _money(note.total_payable), _money(note.outstanding_uncleared)]],
                [CONTENT_WIDTH * w for w in (0.2, 0.2, 0.2, 0.2, 0.2)],
            ))
            story.append(Spacer(1, 4))
            story.append(Paragraph("Checks", ParagraphStyle("Sub", fontSize=9, fontName="Helvetica-Bold", spaceAfter=3)))
            story.append(_checks_table(n_chk, with_batch_col=False) if not n_chk.empty else Paragraph("No checks in this period.", st["empty"]))
            story.append(Spacer(1, 10))
    else:
        story.append(Paragraph("Loan notes", st["section"]))
        if notes_df.empty:
            story.append(Paragraph("No loan notes in this period.", st["empty"]))
        else:
            rows = [[n.note_ref, str(n.issue_date), _money(n.principal_amount), _money(n.total_interest), _money(n.total_payable), _money(n.outstanding_uncleared), n.status] for n in notes_df.itertuples()]
            story.append(_section_table(
                ["Note Ref", "Issue Date", "Principal", "Interest", "Total Payable", "Outstanding", "Status"],
                rows, [CONTENT_WIDTH * w for w in (0.16, 0.14, 0.14, 0.14, 0.14, 0.14, 0.14)],
            ))
        story.append(Paragraph("Checks issued", st["section"]))
        story.append(_checks_table(checks_df, batch_col_label="Loan Note") if not checks_df.empty else Paragraph("No checks in this period.", st["empty"]))

    story.append(Spacer(1, 6))
    story.append(_totals_block([
        ("Outstanding (uncleared, all notes)", notes_df["outstanding_uncleared"].sum() if not notes_df.empty else 0),
        ("TOTAL PAID (active checks)", chk_live["amount"].sum() if not chk_live.empty else 0),
    ]))
    _finish(doc, story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_bank_loan_statement_pdf(info_row, installments_df, date_from=None, date_to=None) -> bytes:
    """Simple statement for one bank loan -- header facts + its installment schedule."""
    buffer = io.BytesIO()
    doc = _doc(buffer)
    st = _styles()
    story = [_title_band("Bank Loan", f"{info_row['loan_ref']} — {info_row['bank_name']}"), Spacer(1, 6), Paragraph(_period_text(date_from, date_to), st["grey"]), Spacer(1, 8)]

    story.append(_section_table(
        ["Account #", "Start Date", "Maturity Date", "Principal", "Status"],
        [[info_row.get("account_number") or "—", str(info_row["start_date"]), str(info_row["maturity_date"]), _money(info_row["principal_amount"]), info_row["status"]]],
        [CONTENT_WIDTH * w for w in (0.2, 0.2, 0.2, 0.2, 0.2)],
    ))
    story.append(Paragraph("Installments", st["section"]))
    if installments_df.empty:
        story.append(Paragraph("No installments in this period.", st["empty"]))
        outstanding_total = 0
    else:
        rows = [[i.installment_no, str(i.due_date), _money(i.amount), i.status] for i in installments_df.itertuples()]
        story.append(_section_table(["#", "Due Date", "Amount", "Status"], rows, [CONTENT_WIDTH * w for w in (0.15, 0.3, 0.3, 0.25)]))
        outstanding_total = installments_df[installments_df["status"] == "Outstanding"]["amount"].sum()

    story.append(Spacer(1, 6))
    story.append(_totals_block([("OUTSTANDING (not yet due)", outstanding_total)]))
    _finish(doc, story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_credit_card_statement_pdf(info_row, statements_df, date_from=None, date_to=None) -> bytes:
    """Simple statement for one credit card -- header facts + its statement history."""
    buffer = io.BytesIO()
    doc = _doc(buffer)
    st = _styles()
    story = [_title_band("Credit Card", f"{info_row['card_name']} - {info_row['card_number_last4']}"), Spacer(1, 6), Paragraph(_period_text(date_from, date_to), st["grey"]), Spacer(1, 8)]

    story.append(_section_table(
        ["Issuer", "Credit Limit", "Cardholder"],
        [[info_row["issuer"], _money(info_row["credit_limit"]), info_row.get("cardholder") or "—"]],
        [CONTENT_WIDTH * w for w in (0.34, 0.33, 0.33)],
    ))
    story.append(Paragraph("Statements", st["section"]))
    if statements_df.empty:
        story.append(Paragraph("No statements in this period.", st["empty"]))
        unpaid_total = 0
    else:
        rows = [[str(s.due_date), _money(s.total_amount_due), s.status, s.payment_method, str(s.payment_date) if s.payment_date else "—"] for s in statements_df.itertuples()]
        story.append(_section_table(
            ["Due Date", "Amount Due", "Status", "Payment Method", "Payment Date"],
            rows, [CONTENT_WIDTH * w for w in (0.18, 0.18, 0.18, 0.28, 0.18)],
        ))
        unpaid_total = statements_df[~statements_df["status"].isin(["Paid", "Cancelled"])]["total_amount_due"].sum()

    story.append(Spacer(1, 6))
    story.append(_totals_block([("UNPAID (not Paid/Cancelled)", unpaid_total)]))
    _finish(doc, story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_cardholder_statement_pdf(holder_name: str, cards_df, statements_df, date_from=None, date_to=None, group_by_card: bool = False) -> bytes:
    """Combined statement of account across every card belonging to one cardholder -- the "All
    cards" option, for a cardholder with more than one card who wants one combined history
    instead of pulling each card separately. Combines by default (group_by_card=False); set it
    to break the statement into a section per card with its own subtotal instead, same toggle
    shape as the Supplier/Lender group_by options."""
    buffer = io.BytesIO()
    doc = _doc(buffer)
    st = _styles()
    story = [_title_band("Cardholder", holder_name), Spacer(1, 6), Paragraph(_period_text(date_from, date_to), st["grey"]), Spacer(1, 8)]

    story.append(Paragraph("Cards", st["section"]))
    if cards_df.empty:
        story.append(Paragraph("No cards on file.", st["empty"]))
    else:
        rows = [[c.card_name, c.card_number_last4, c.issuer, _money(c.credit_limit)] for c in cards_df.itertuples()]
        story.append(_section_table(["Card Name", "Last 4", "Issuer", "Credit Limit"], rows, [CONTENT_WIDTH * w for w in (0.3, 0.15, 0.35, 0.2)]))

    unpaid_live = statements_df[~statements_df["status"].isin(["Paid", "Cancelled"])] if not statements_df.empty else statements_df

    if group_by_card and not cards_df.empty:
        for card in cards_df.itertuples():
            story.append(Paragraph(_esc(f"{card.card_name} - {card.card_number_last4}"), st["section"]))
            c_stmts = statements_df[statements_df["card_number_last4"] == card.card_number_last4] if not statements_df.empty else statements_df
            if c_stmts.empty:
                story.append(Paragraph("No statements in this period.", st["empty"]))
            else:
                rows = [[str(s.due_date), _money(s.total_amount_due), s.status, s.payment_method, str(s.payment_date) if s.payment_date else "—"] for s in c_stmts.itertuples()]
                story.append(_section_table(
                    ["Due Date", "Amount Due", "Status", "Payment Method", "Payment Date"],
                    rows, [CONTENT_WIDTH * w for w in (0.18, 0.18, 0.18, 0.28, 0.18)],
                ))
            c_unpaid = c_stmts[~c_stmts["status"].isin(["Paid", "Cancelled"])] if not c_stmts.empty else c_stmts
            story.append(Spacer(1, 4))
            story.append(_totals_block([("Unpaid on this card", c_unpaid["total_amount_due"].sum() if not c_unpaid.empty else 0)]))
            story.append(Spacer(1, 10))
    else:
        story.append(Paragraph("Statements (all cards combined)", st["section"]))
        if statements_df.empty:
            story.append(Paragraph("No statements in this period.", st["empty"]))
        else:
            rows = [[f"{s.card_name} - {s.card_number_last4}", str(s.due_date), _money(s.total_amount_due), s.status, str(s.payment_date) if s.payment_date else "—"] for s in statements_df.itertuples()]
            story.append(_section_table(
                ["Card", "Due Date", "Amount Due", "Status", "Payment Date"],
                rows, [CONTENT_WIDTH * w for w in (0.26, 0.18, 0.18, 0.2, 0.18)],
            ))

    story.append(Spacer(1, 6))
    story.append(_totals_block([("TOTAL UNPAID, all cards (not Paid/Cancelled)", unpaid_live["total_amount_due"].sum() if not unpaid_live.empty else 0)]))
    _finish(doc, story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_rundown_pdf(title: str, scope: str, headers: list, rows: list, widths: list, right_cols: list, summary: list, period: str,
                         before_table: list | None = None) -> bytes:
    """Landscape 'rundown' report used by the Reports tab's batch and invoice rundowns: a title
    band, a Paid / Partially Paid / Pending summary, then one table (right_cols are the column
    indexes holding money). Columns are sized to their content; when the table is too wide for the
    page, numbers and dates keep their width and the text columns share what is left in proportion
    to `widths` (fractions of the page, one per column) -- 2026-09-28: `widths` used to be ignored. `before_table`: flowables (a chart)
    placed between the summary and the table."""
    from reportlab.lib.pagesizes import landscape
    page = landscape(LETTER)
    width = page[0] - LEFT_MARGIN - RIGHT_MARGIN
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=page, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN)
    st = _styles()
    styles = getSampleStyleSheet()
    band = Table(
        [[Paragraph(_esc(title.upper()), ParagraphStyle("RdTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")),
          Paragraph(_esc(scope), ParagraphStyle("RdSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2))]],
        colWidths=[width * 0.55, width * 0.45],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story = [band, Spacer(1, 6), Paragraph(_esc(period), st["grey"]), Spacer(1, 8)]
    if summary:
        srows = [[label, count, f"PHP {_money(amount)}"] for label, count, amount in summary]
        sumt = Table([["Status", "Count", "Amount"]] + srows, colWidths=[width * 0.25, width * 0.15, width * 0.25], hAlign="LEFT")
        sumt.setStyle(TableStyle(SECTION_TABLE_STYLE + [("ALIGN", (1, 0), (2, -1), "RIGHT")]))
        story += [sumt, Spacer(1, 10)]
    for flowable in before_table or []:              # e.g. the "My reports" chart
        story += [flowable, Spacer(1, 8)]
    if rows:
        from reportlab.pdfbase.pdfmetrics import stringWidth
        pad = 8  # left + right cell padding, in points (tightened below to match)
        # Auto-fit at a fixed 9pt: measure the widest text in each column and share any spare room
        # out in proportion. If the columns don't all fit, numbers and dates keep the width they
        # need and only the text columns (names, references) shrink and wrap onto a second line.
        size = MIN_FONT_PT
        need = [max([stringWidth(str(headers[c]), "Helvetica-Bold", size)] + [stringWidth(str(r[c]), "Helvetica", size) for r in rows]) + pad
                for c in range(len(headers))]
        total_need = sum(need)
        if total_need <= width:
            spare = width - total_need
            col_w = [n + spare * (n / total_need) for n in need]
            data = [headers] + rows
        else:
            # Only long text columns wrap; short words like a status ("Pending") stay on one line.
            is_text = [any(any(ch.isalpha() for ch in str(r[c])) for r in rows) and need[c] > 130 for c in range(len(headers))]
            fixed = sum(n for n, t_ in zip(need, is_text) if not t_)
            weight = [float(widths[c]) if widths and c < len(widths) and widths[c] else need[c] for c in range(len(headers))]
            flex = sum(w for w, t_ in zip(weight, is_text) if t_) or 1
            room = max(width - fixed, 60)
            col_w = [n if not t_ else w * room / flex for n, w, t_ in zip(need, weight, is_text)]
            cell_style = ParagraphStyle("RdCell", parent=styles["Normal"], fontSize=size, leading=size + 1.5)
            data = [headers] + [[Paragraph(_esc(str(x)), cell_style) if is_text[c] else x for c, x in enumerate(r)] for r in rows]   # escaped: 'S&P' must not break the PDF
        t = Table(data, colWidths=col_w, repeatRows=1)
        t.setStyle(TableStyle(SECTION_TABLE_STYLE + [("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)] + [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in right_cols]))
        story.append(t)
    else:
        story.append(Paragraph("Nothing to show for this selection.", st["empty"]))
    _finish(doc, story)
    return buffer.getvalue()
