"""Item labels (the revenue cycle, stage A; owner, 2026-10-11: "print barcode for each tire then upon sale we just scan it"). One label per piece; every
piece of an item carries the same code -- the item's own code (TR-0142) as a Code 128 barcode any scanner reads, and as a QR code a phone camera reads.
No price on a label: the price is agreed at the counter. The size is chosen when printing (the owner has not settled on one): one label per page for a
label printer, or a sheet of 24 on A4. Tread labels or hang tags: paper does not hold on rubber."""
from __future__ import annotations

import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

SIZES = {"50x30": ("50 × 30 mm, one per page", 50, 30), "60x40": ("60 × 40 mm, one per page", 60, 40), "100x50": ("100 × 50 mm, one per page", 100, 50),
         "sheet24": ("A4 sheet of 24 (70 × 37 mm)", 70, 37)}
DEFAULT_SIZE = "50x30"


def _fit(c, text: str, font: str, size: float, width: float) -> list[str]:
    """The name on at most two lines that fit the label's width."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if c.stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
        while c.stringWidth(lines[1] + "…", font, size) > width and len(lines[1]) > 4:
            lines[1] = lines[1][:-1]
        lines[1] += "…"
    return lines


def _draw(c, x: float, y: float, w: float, h: float, code: str, name: str, dot: str) -> None:
    """One label with its bottom-left corner at (x, y)."""
    pad = 2.2 * mm
    side = min(h - 2 * pad, w * 0.30)                            # the QR code, on the right
    widget = qr.QrCodeWidget(code, barLevel="M")
    b = widget.getBounds()
    d = Drawing(side, side, transform=[side / (b[2] - b[0]), 0, 0, side / (b[3] - b[1]), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x + w - pad - side, y + (h - side) / 2)
    left = w - side - 3 * pad
    font_size = max(6.0, min(9.5, h / mm * 0.24))
    c.setFont("Helvetica-Bold", font_size)
    ty = y + h - pad - font_size
    for line in _fit(c, name, "Helvetica-Bold", font_size, left):
        c.drawString(x + pad, ty, line)
        ty -= font_size + 1
    bar_h = max(6 * mm, min(h * 0.34, 14 * mm))
    bar = code128.Code128(code, barHeight=bar_h, barWidth=0.33 * mm, quiet=False)
    if bar.width > left:                                         # a long code: thinner bars, never clipped
        bar = code128.Code128(code, barHeight=bar_h, barWidth=max(0.33 * mm * left / bar.width, 0.19 * mm), quiet=False)
    bar.drawOn(c, x + pad, y + pad + 3.4 * mm)
    c.setFont("Helvetica", max(5.5, font_size - 2.5))
    c.drawString(x + pad, y + pad, code + (f"  ·  DOT {dot}" if dot else ""))


def labels_pdf(rows: list[dict], size: str = DEFAULT_SIZE) -> bytes:
    """rows: [{'code', 'name', 'dot' (text, may be ''), 'copies'}] -> the PDF, `copies` labels of each in order."""
    _, w_mm, h_mm = SIZES.get(size, SIZES[DEFAULT_SIZE])
    w, h = w_mm * mm, h_mm * mm
    labels = [r for r in rows for _ in range(max(int(r.get("copies") or 0), 0))]
    buf = io.BytesIO()
    if size == "sheet24":
        c = canvas.Canvas(buf, pagesize=A4)
        c.setTitle("Item labels")
        cols, per_page = 3, 24
        left, top = (A4[0] - cols * w) / 2, A4[1] - (A4[1] - 8 * h) / 2
        for n, r in enumerate(labels):
            k = n % per_page
            if n and k == 0:
                c.showPage()
            _draw(c, left + (k % cols) * w, top - (k // cols + 1) * h, w, h, r["code"], r["name"], r.get("dot") or "")
    else:
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setTitle("Item labels")
        for n, r in enumerate(labels):
            if n:
                c.showPage()
            _draw(c, 0, 0, w, h, r["code"], r["name"], r.get("dot") or "")
    if not labels:
        c.setFont("Helvetica", 8)
        c.drawString(4 * mm, h / 2 if size != "sheet24" else A4[1] - 20 * mm, "No labels to print.")
    c.save()
    return buf.getvalue()
