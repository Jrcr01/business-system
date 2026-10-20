"""The purchase order the supplier receives (stage B2). It carries the order only: items, prices, delivery date and terms. Nothing about our cash or
forecast ever goes on it. A plain draft has no PDF. A draft SENT to the supplier (owner, 2026-10-11) has the ORDER REQUEST: the same sheet under its
request number, saying it is not yet a purchase order; the PURCHASE ORDER exists once the supplier confirmed (engines/po_rules.confirm_request)."""
from __future__ import annotations

import io
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib import colors
from engines.pdf_tail import continued_canvas, keep_tail
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from engines.common import get_conn
from engines.po_rules import get_po

def _company():
    from engines.company import name
    return name()
ACCENT = colors.HexColor("#1f3a5f")
ACCENT_LIGHT = colors.HexColor("#eef2f7")
GREY = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#d1d5db")
MARGIN = 0.6 * inch


def _day(value) -> str:
    return date.fromisoformat(str(value)[:10]).strftime("%m/%d/%Y") if value else "To be advised"


def _qty(q: float) -> str:
    return f"{q:,.0f}" if float(q).is_integer() else f"{q:,.2f}"


def _terms_text(terms) -> str:
    text = str(terms or "").strip()
    if not text:
        return "As agreed"
    return f"{text} days" if text.isdigit() else text


def _may_send(po_id: int) -> bool:
    from engines.permissions import require
    from engines.common import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT created_by FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
    return require("po.order", row[0] if row else None)[0]


def build_po_pdf(po_id: int, request: bool = False) -> bytes | None:
    """The purchase order; with request=True the order request of a draft that was sent to the supplier. None when the order is not at that step."""
    from engines.po_rules import is_request
    po = get_po(po_id)
    if po is None:
        return None
    if request:
        if not is_request(po["header"]):
            return None
    elif po["header"]["status"] in ("DRAFT", "CANCELLED") or str(po["header"]["po_no"]).startswith("DRAFT-"):
        return None
    if not _may_send(po_id):                                # the supplier PDF IS the order being sent: same permission as ordering
        return None
    h = po["header"]
    with get_conn() as conn:
        sup = conn.execute("SELECT contact_person, contact_number, tin FROM creditors WHERE creditor_id = ?", (h["creditor_id"],)).fetchone() or (None, None, None)

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8.5, textColor=GREY)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9.5, leading=13)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=9, leading=11)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=0.55 * inch, bottomMargin=0.6 * inch,
                            title=f"Order request {h['request_no']}" if request else f"Purchase order {h['po_no']}")
    width = LETTER[0] - 2 * MARGIN
    story: list = []

    head = Table([[Paragraph(f"<font size=15 color='#1f3a5f'><b>{escape(_company())}</b></font>", styles["Normal"]),
                   Paragraph(f"<font size=16 color='#1f3a5f'><b>{'ORDER REQUEST' if request else 'PURCHASE ORDER'}</b></font>", ParagraphStyle("r", parent=styles["Normal"], alignment=2))]],
                 colWidths=[width * 0.55, width * 0.45])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, ACCENT), ("BOTTOMPADDING", (0, 0), (-1, 0), 8)]))
    story += [head, Spacer(1, 10)]

    sup_lines = [f"<b>{escape(h['supplier'])}</b>"]
    if sup[0]:
        sup_lines.append(f"Attn: {escape(str(sup[0]))}")
    if sup[1]:
        sup_lines.append(f"Tel: {escape(str(sup[1]))}")
    if sup[2]:
        sup_lines.append(f"TIN: {escape(str(sup[2]))}")
    meta = [["Request no.", h["request_no"]] if request else ["PO no.", h["po_no"]], ["Date", _day(h["order_date"])] if request else ["Order date", _day(h["order_date"])],
            ["Delivery wanted" if request else "Expected delivery", _day(h["expected_delivery_date"])],
            ["Payment terms", _terms_text(h["terms_snapshot"])]]
    top = Table([[[Paragraph("TO", small)] + [Paragraph(t, body) for t in sup_lines],
                  Table([[Paragraph(f"<font color='#6b7280'>{k}</font>", cell), Paragraph(f"<b>{escape(str(v))}</b>", cell)] for k, v in meta], colWidths=[1.25 * inch, 2.0 * inch])]],
                colWidths=[width * 0.5, width * 0.5])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [top, Spacer(1, 14)]

    from engines.discount_rules import chain_label
    discounted = any(chain_label(c) for c in po["lines"]["discount_chain"].fillna(""))      # the list price and the agreed discount are shown only when there is one
    if discounted:
        rows = [["#", "Item", "Unit", "Qty", "List price", "Discount", "Net price", "Amount"]]
        for i, ln in enumerate(po["lines"].itertuples(), start=1):
            listed = ln.list_price if ln.list_price == ln.list_price and ln.list_price is not None else ln.est_unit_price
            rows.append([str(i), Paragraph(escape(str(ln.item_name)), cell), ln.unit or "", _qty(ln.qty), f"{listed:,.2f}", Paragraph(chain_label(ln.discount_chain or "") or "-", cell),
                         f"{ln.est_unit_price:,.2f}", f"{ln.est_amount:,.2f}"])
        rows.append(["", "", "", "", "", "", Paragraph("<b>TOTAL</b>", cell), Paragraph(f"<b>{po['total']:,.2f}</b>", cell)])
        widths = [0.3 * inch, width - 0.3 * inch - 5.5 * inch, 0.45 * inch, 0.5 * inch, 0.95 * inch, 1.1 * inch, 0.95 * inch, 1.25 * inch]
    else:
        rows = [["#", "Item", "Unit", "Qty", "Unit price", "Amount"]]
        for i, ln in enumerate(po["lines"].itertuples(), start=1):
            rows.append([str(i), Paragraph(escape(str(ln.item_name)), cell), ln.unit or "", _qty(ln.qty), f"{ln.est_unit_price:,.2f}", f"{ln.est_amount:,.2f}"])
        rows.append(["", "", "", "", Paragraph("<b>TOTAL</b>", cell), Paragraph(f"<b>{po['total']:,.2f}</b>", cell)])
        widths = [0.35 * inch, width - 0.35 * inch - 3.9 * inch, 0.6 * inch, 0.7 * inch, 1.15 * inch, 1.45 * inch]
    table = keep_tail(Table(rows, colWidths=widths, repeatRows=1))
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -2), 0.5, BORDER), ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ACCENT_LIGHT]),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEABOVE", (len(rows[0]) - 2, -1), (-1, -1), 1, ACCENT),
    ]))
    story += [table, Spacer(1, 10)]

    if h.get("notes"):
        story += [Paragraph("NOTES", small), Paragraph(escape(str(h["notes"])), body), Spacer(1, 10)]
    if request:
        story += [Paragraph("<b>Not yet a purchase order.</b> Please confirm which items and quantities are available, and the delivery date. "
                            "Our purchase order follows your confirmation.", body), Spacer(1, 6)]
    story += [Paragraph("Prices are per your current price list." + ("" if request else " Please quote this PO number on your delivery receipt and invoice."), small), Spacer(1, 34)]

    sign = Table([["Prepared by", "", "Confirmed by supplier"] if request else ["Prepared by", "Approved by", "Received by supplier"], ["", "", ""]], colWidths=[width / 3] * 3, rowHeights=[14, 26])
    sign.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("TEXTCOLOR", (0, 0), (-1, 0), GREY), ("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.black),
                              ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    story.append(sign)
    doc.build(story, canvasmaker=continued_canvas(doc.pagesize[0] - doc.rightMargin, max(doc.bottomMargin - 22, 18)))
    return buf.getvalue()
