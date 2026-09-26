"""PDF for the overall report -- designed as a management document.

Short = cover + one summary page (highlights, key numbers, trend chart, needs attention).
Full  = cover + summary + a page of charts + the detail tables.
Brand: the logo's deep blue and yellow. Numbers are right-aligned, nothing is smaller than 9pt.
"""
import io
import os
from datetime import datetime

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable, KeepTogether

from engines.account_history_pdf_service import (
    ACCENT, ACCENT_LIGHT, CONTENT_WIDTH, GREY, LEFT_MARGIN, MIN_FONT_PT, RIGHT_MARGIN, _section_table,
)
from engines.overview_report_service import _pct_change

DEEP = colors.HexColor("#12304f")      # cover banner base
TREAD = colors.HexColor("#1c4a78")     # tread pattern on the banner
GOLD = colors.HexColor("#f2b705")      # accent (logo yellow, toned for print)
RULE = colors.HexColor("#d9dee5")
LOGO_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "slicks_logo.jpg")
COMPANY = "Slick's Tire Supply"
PAGE_W, PAGE_H = LETTER
BANNER_H = 3.3 * inch


# ------------------------------------------------------------------ formatting
def _m(v) -> str:
    return f"{float(v):,.2f}"


def _chg(now: float, before: float) -> str:
    c = _pct_change(now, before)
    return "-" if c is None else f"{c:+.0f}%"


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _axis_format(top: float):
    if top >= 1_000_000:
        return lambda v: f"{v / 1_000_000:.1f}M"
    if top >= 1_000:
        return lambda v: f"{v / 1_000:.0f}K"
    return lambda v: f"{v:.0f}"


# ------------------------------------------------------------------ cover art (drawn straight on the page)
def _draw_tread_banner(c) -> None:
    """Full-width banner of stylised herringbone tire tread, fading to deep blue on the left, with a gold edge."""
    y0 = PAGE_H - BANNER_H
    c.saveState()
    p = c.beginPath()
    p.rect(0, y0, PAGE_W, BANNER_H)
    c.clipPath(p, stroke=0, fill=0)
    c.setFillColor(DEEP)
    c.rect(0, y0, PAGE_W, BANNER_H, stroke=0, fill=1)
    # herringbone tread: rows of V-shaped grooves, every other row shifted half a step
    step, row_h, lw = 64.0, 44.0, 10.0
    c.setStrokeColor(TREAD)
    c.setLineWidth(lw)
    c.setLineCap(1)
    row = 0
    y = y0 - row_h
    while y < PAGE_H + row_h:
        x = -step + (step / 2 if row % 2 else 0)
        while x < PAGE_W + step:
            c.line(x + 9, y + 4, x + step / 2, y + row_h * 0.78)          # left arm of the V groove
            c.line(x + step - 9, y + 4, x + step / 2, y + row_h * 0.78)   # right arm; the gaps keep grooves separate
            x += step
        y += row_h
        row += 1
    # centre rib in gold: one thin chevron row, like a tread wear marker
    c.setStrokeColor(GOLD)
    c.setLineWidth(3.2)
    ymid = y0 + BANNER_H * 0.5
    x = 0.0
    while x < PAGE_W + step:
        c.line(x, ymid, x + step / 2, ymid + row_h * 0.55)
        c.line(x + step / 2, ymid + row_h * 0.55, x + step, ymid)
        x += step
    # fade the left side into the base colour so the pattern feels intentional, not busy
    c.setFillColor(DEEP)
    strips = 28
    for i in range(strips):
        c.setFillAlpha(0.85 * (1 - i / strips) ** 1.6)
        c.rect(PAGE_W * 0.55 * i / strips, y0, PAGE_W * 0.55 / strips + 1, BANNER_H, stroke=0, fill=1)
    c.setFillAlpha(1)
    c.restoreState()
    # gold edge under the banner
    c.saveState()
    c.setFillColor(GOLD)
    c.rect(0, y0 - 5, PAGE_W, 5, stroke=0, fill=1)
    c.restoreState()


def _cover_page(c, doc) -> None:
    _draw_tread_banner(c)
    c.saveState()
    c.setFillColor(GREY)
    c.setFont("Helvetica", 9)
    c.drawCentredString(PAGE_W / 2, 0.45 * inch, f"{COMPANY}  |  Accounts payable overview  |  Prepared {datetime.now():%B %d, %Y}")
    c.restoreState()


def _inner_page(label: str):
    def draw(c, doc) -> None:
        c.saveState()
        if os.path.exists(LOGO_PATH):
            c.drawImage(LOGO_PATH, LEFT_MARGIN, PAGE_H - 0.72 * inch, width=0.4 * inch, height=0.4 * inch, mask=None)
        c.setFillColor(ACCENT)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(LEFT_MARGIN + 0.5 * inch, PAGE_H - 0.5 * inch, COMPANY)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 9)
        c.drawString(LEFT_MARGIN + 0.5 * inch, PAGE_H - 0.66 * inch, "Overview report")
        c.drawRightString(PAGE_W - RIGHT_MARGIN, PAGE_H - 0.5 * inch, label)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.6)
        c.line(LEFT_MARGIN, PAGE_H - 0.8 * inch, PAGE_W - RIGHT_MARGIN, PAGE_H - 0.8 * inch)
        c.setStrokeColor(RULE)
        c.setLineWidth(0.6)
        c.line(LEFT_MARGIN, 0.6 * inch, PAGE_W - RIGHT_MARGIN, 0.6 * inch)
        c.setFillColor(GREY)
        c.setFont("Helvetica", 9)
        c.drawString(LEFT_MARGIN, 0.42 * inch, f"Prepared {datetime.now():%Y-%m-%d %H:%M}")
        c.drawRightString(PAGE_W - RIGHT_MARGIN, 0.42 * inch, f"Page {doc.page}")
        c.restoreState()
    return draw


# ------------------------------------------------------------------ charts
def _legend(d: Drawing, x: float, y: float, items) -> None:
    for name, col in items:
        d.add(Rect(x, y, 9, 9, fillColor=col, strokeColor=None))
        d.add(String(x + 13, y + 1, name, fontName="Helvetica", fontSize=MIN_FONT_PT, fillColor=GREY))
        x += 26 + len(name) * 5


def _chart_title(d: Drawing, text: str, height: float) -> None:
    d.add(String(0, height - 12, text, fontName="Helvetica-Bold", fontSize=10.5, fillColor=ACCENT))


def column_chart(title: str, categories: list[str], series: dict[str, list[float]], height: float = 190, labels: bool = True) -> Drawing:
    width = CONTENT_WIDTH
    d = Drawing(width, height)
    _chart_title(d, title, height)
    names = list(series)
    cols = [ACCENT, GOLD, colors.HexColor("#5b7fa8")]
    top = max([v for vals in series.values() for v in vals] + [1.0])
    fmt = _axis_format(top)
    ch = VerticalBarChart()
    ch.x, ch.y, ch.width, ch.height = 52, 32, width - 66, height - 64
    ch.data = [series[n] for n in names]
    ch.categoryAxis.categoryNames = categories
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Helvetica", MIN_FONT_PT
    ch.categoryAxis.labels.dy = -2
    ch.categoryAxis.strokeColor = colors.HexColor("#9ca3af")
    ch.valueAxis.valueMin, ch.valueAxis.valueMax = 0, top * 1.18
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = "Helvetica", MIN_FONT_PT
    ch.valueAxis.labelTextFormat = fmt
    ch.valueAxis.strokeColor = None
    ch.valueAxis.visibleTicks = False
    ch.valueAxis.visibleGrid = True
    ch.valueAxis.gridStrokeColor = colors.HexColor("#e5e7eb")
    ch.groupSpacing = 10
    ch.barSpacing = 1.5
    if labels:
        ch.barLabelFormat = lambda v: "" if not v else fmt(v)
        ch.barLabels.nudge = 7
        ch.barLabels.fontName, ch.barLabels.fontSize = "Helvetica", MIN_FONT_PT
        ch.barLabels.fillColor = colors.HexColor("#374151")
    for i, _ in enumerate(names):
        ch.bars[i].fillColor = cols[i % len(cols)]
        ch.bars[i].strokeColor = None
    d.add(ch)
    if len(names) > 1:
        _legend(d, width - 130, height - 14, list(zip(names, cols)))
    return d


def bar_chart_h(title: str, categories: list[str], values: list[float]) -> Drawing:
    n = max(len(categories), 1)
    height = 46 + 26 * n
    width = CONTENT_WIDTH
    d = Drawing(width, height)
    _chart_title(d, title, height)
    top = max(values + [1.0])
    fmt = _axis_format(top)
    ch = HorizontalBarChart()
    ch.x, ch.y, ch.width, ch.height = 200, 16, width - 265, height - 46
    ch.data = [list(reversed(values))]
    ch.categoryAxis.categoryNames = [c if len(c) <= 32 else c[:31] + "..." for c in reversed(categories)]
    ch.categoryAxis.labels.fontName, ch.categoryAxis.labels.fontSize = "Helvetica", MIN_FONT_PT
    ch.categoryAxis.labels.boxAnchor = "e"
    ch.categoryAxis.strokeColor = colors.HexColor("#9ca3af")
    ch.valueAxis.valueMin, ch.valueAxis.valueMax = 0, top * 1.2
    ch.valueAxis.labels.fontName, ch.valueAxis.labels.fontSize = "Helvetica", MIN_FONT_PT
    ch.valueAxis.labelTextFormat = fmt
    ch.valueAxis.strokeColor = None
    ch.valueAxis.visibleTicks = False
    ch.valueAxis.visibleGrid = True
    ch.valueAxis.gridStrokeColor = colors.HexColor("#e5e7eb")
    ch.bars[0].fillColor = ACCENT
    ch.bars[0].strokeColor = None
    ch.barSpacing = 4
    ch.barLabelFormat = lambda v: fmt(v)
    ch.barLabels.nudge = 16
    ch.barLabels.fontName, ch.barLabels.fontSize = "Helvetica", MIN_FONT_PT
    ch.barLabels.fillColor = colors.HexColor("#374151")
    d.add(ch)
    return d


# ------------------------------------------------------------------ building blocks
def _styles() -> dict:
    base = getSampleStyleSheet()
    n = base["Normal"]
    return {
        "body": ParagraphStyle("Body", parent=n, fontSize=MIN_FONT_PT + 0.5, leading=14, spaceAfter=3),
        "section": ParagraphStyle("Sec", parent=n, textColor=ACCENT, fontSize=12.5, leading=15, fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=2),
        "grey": ParagraphStyle("Grey", parent=n, fontSize=MIN_FONT_PT, textColor=GREY),
        "cover_company": ParagraphStyle("CC", parent=n, fontSize=13, leading=16, alignment=1, textColor=GREY, fontName="Helvetica-Bold", spaceAfter=4),
        "cover_title": ParagraphStyle("CT", parent=n, fontSize=34, leading=38, alignment=1, textColor=ACCENT, fontName="Helvetica-Bold"),
        "cover_period": ParagraphStyle("CP", parent=n, fontSize=18, leading=24, alignment=1, textColor=colors.HexColor("#111827"), spaceBefore=6),
        "cover_sub": ParagraphStyle("CS", parent=n, fontSize=10.5, leading=14, alignment=1, textColor=GREY),
        "kpi_label": ParagraphStyle("KL", parent=n, fontSize=9, leading=11, textColor=GREY),
        "kpi_value": ParagraphStyle("KV", parent=n, fontSize=15, leading=19, textColor=ACCENT, fontName="Helvetica-Bold"),
        "kpi_delta": ParagraphStyle("KD", parent=n, fontSize=9, leading=11, textColor=GREY),
        "big": ParagraphStyle("Big", parent=n, fontSize=18, leading=22, alignment=1, textColor=ACCENT, fontName="Helvetica-Bold"),
        "big_label": ParagraphStyle("BigL", parent=n, fontSize=9, leading=12, alignment=1, textColor=GREY),
        "bullet": ParagraphStyle("Bul", parent=n, fontSize=MIN_FONT_PT + 0.5, leading=14.5, leftIndent=12, bulletIndent=0, spaceAfter=3),
    }


def _section(title: str, st: dict) -> list:
    return [Paragraph(title, st["section"]), HRFlowable(width="100%", thickness=0.8, color=GOLD, spaceBefore=1, spaceAfter=6)]


def _callout(paragraphs: list) -> Table:
    t = Table([[paragraphs]], colWidths=[CONTENT_WIDTH])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT_LIGHT), ("LINEBEFORE", (0, 0), (0, -1), 4, GOLD),
                           ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _kpi_cards(cards: list[tuple[str, str, str]], st: dict) -> Table:
    n = len(cards)
    cells = [[[Paragraph(label, st["kpi_label"]), Paragraph(value, st["kpi_value"]), Paragraph(delta, st["kpi_delta"])] for label, value, delta in cards]]
    gap = 6
    t = Table(cells, colWidths=[(CONTENT_WIDTH - gap * (n - 1)) / n] * n, hAlign="LEFT")
    style = [("BOX", (0, 0), (-1, -1), 0, colors.white), ("LEFTPADDING", (0, 0), (-1, -1), 9), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    for i in range(n):
        style += [("BACKGROUND", (i, 0), (i, 0), colors.HexColor("#f6f8fb")), ("LINEABOVE", (i, 0), (i, 0), 2.5, ACCENT),
                  ("BOX", (i, 0), (i, 0), 0.5, RULE)]
    t.setStyle(TableStyle(style))
    return t


def _delta_text(now: float, before: float, prev_label: str) -> str:
    c = _pct_change(now, before)
    return f"vs {prev_label}: n/a" if c is None else f"{'+' if c >= 0 else '-'}{abs(c):.0f}% vs {prev_label}"


def _cover(data: dict, short: bool, st: dict) -> list:
    cur = data["current"]
    story = [Spacer(1, BANNER_H - 0.5 * inch + 0.25 * inch)]
    if os.path.exists(LOGO_PATH):
        story.append(Image(LOGO_PATH, width=1.5 * inch, height=1.5 * inch, hAlign="CENTER"))
    story += [
        Spacer(1, 0.18 * inch),
        Paragraph(COMPANY.upper(), st["cover_company"]),
        Paragraph(f"{data['kind']} Overview", st["cover_title"]),
        Paragraph(_esc(data["label"]), st["cover_period"]),
        Paragraph(f"compared with {_esc(data['prev_label'])}", st["cover_sub"]),
        Spacer(1, 0.3 * inch),
    ]
    big = Table(
        [[Paragraph(_m(cur["paid"]), st["big"]), Paragraph(_m(cur["scheduled"]), st["big"]), Paragraph(_m(data["outstanding_today"]), st["big"])],
         [Paragraph("PAID OUT (PHP)", st["big_label"]), Paragraph("SCHEDULED, NOT YET DUE (PHP)", st["big_label"]),
          Paragraph("TOTAL OUTSTANDING TODAY (PHP)", st["big_label"])]],
        colWidths=[CONTENT_WIDTH / 3] * 3,
    )
    big.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f8fb")), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                             ("TOPPADDING", (0, 0), (-1, 0), 14), ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
                             ("LINEABOVE", (0, 0), (-1, 0), 2.5, GOLD), ("LINEAFTER", (0, 0), (1, -1), 0.5, RULE)]))
    story += [big, Spacer(1, 0.3 * inch)]
    contents = [["Highlights and key numbers", "2"]]
    if not short:
        contents += [["Charts", "3"], ["Detail: top payees, financing, behavior, coming up", "4"]]
    story += [_section_table(["Contents", "Page"], contents, [CONTENT_WIDTH * 0.85, CONTENT_WIDTH * 0.15], right_cols=(1,)), PageBreak()]
    return story


# ------------------------------------------------------------------ the report
def generate_overview_pdf(data: dict, short: bool = False) -> bytes:
    cur, prev = data["current"], data["previous"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=0.95 * inch, bottomMargin=0.8 * inch,
                            title=f"{COMPANY} - {data['kind']} overview - {data['label']}", author=COMPANY)
    st = _styles()
    story = _cover(data, short, st)
    pl = data["prev_label"]
    total = cur["paid"] + cur["scheduled"]

    # ---- page 2: highlights and key numbers
    story += _section("Highlights", st)
    story.append(_callout([Paragraph(_esc(h), st["bullet"], bulletText="•") for h in data["highlights"]]))
    story.append(Spacer(1, 10))
    story.append(_kpi_cards([
        ("PAID OUT", _m(cur["paid"]), _delta_text(cur["paid"], prev["paid"], pl)),
        ("SCHEDULED, NOT YET DUE", _m(cur["scheduled"]), _delta_text(cur["scheduled"], prev["scheduled"], pl)),
        ("INTEREST COST", _m(cur["interest_total"]), _delta_text(cur["interest_total"], prev["interest_total"], pl)),
        ("OUTSTANDING TODAY", _m(data["outstanding_today"]), "all tracks"),
    ], st))
    story += _section("Check activity", st)
    rows = [
        ["Checks issued", str(cur["checks"]["issued"]), str(prev["checks"]["issued"]), _chg(cur["checks"]["issued"], prev["checks"]["issued"])],
        ["Checks cancelled / replaced", str(cur["checks"]["voided"]), str(prev["checks"]["voided"]), "-"],
    ]
    story.append(_section_table(["Measure", "This period", "Previous", "Change"], rows, [CONTENT_WIDTH * w for w in (0.4, 0.22, 0.22, 0.16)], right_cols=(1, 2, 3)))
    story.append(Paragraph("Paid = checks and installments whose date has arrived; scheduled = dated later than today. Figures are in Philippine pesos.", st["grey"]))

    trend = data["trend"]
    story.append(Spacer(1, 10))
    story.append(column_chart(f"Trend - paid out and scheduled, last {len(trend)} {data['kind'].lower()}s",
                              [t["label"] for t in trend], {"Paid": [t["paid"] for t in trend], "Scheduled": [t["scheduled"] for t in trend]},
                              height=165, labels=False))
    if data["watch"]:
        story += _section("Needs attention", st)
        story.append(_section_table(["Item", "Count", "Detail"], [[w["item"], str(w["count"]), w["detail"]] for w in data["watch"]],
                                    [CONTENT_WIDTH * w for w in (0.5, 0.1, 0.4)], right_cols=(1,)))

    if not short:
        # ---- charts page
        story.append(PageBreak())
        story += _section("Charts", st)
        bt = cur["by_track"]
        story.append(column_chart("Paid vs scheduled by track", list(bt["track"]), {"Paid": list(bt["paid"]), "Scheduled": list(bt["scheduled"])}, height=185))
        story.append(Spacer(1, 10))
        top5 = cur["payees"].head(5)
        if not top5.empty:
            story.append(bar_chart_h("Top 5 payees (paid + scheduled)", list(top5["payee"]), [float(v) for v in top5["total"]]))
            story.append(Spacer(1, 10))
        up = data["upcoming"]
        if up["by_week"]:
            story.append(column_chart(f"Coming up - {up['label']}, by week starting", [w[5:] for w, _ in up["by_week"]], {"Amount due": [a for _, a in up["by_week"]]}, height=175))
            story.append(Spacer(1, 10))
        if not cur["interest"].empty:
            src = cur["interest"].groupby("source")["interest"].sum()
            story.append(column_chart("Interest cost by source", list(src.index), {"Interest": [float(v) for v in src.values]}, height=150))

        # ---- detail
        story.append(PageBreak())
        story += _section("Where the money went", st)
        rows = []
        prev_tracks = prev["by_track"].set_index("track")
        for r in cur["by_track"].itertuples():
            t = r.paid + r.scheduled
            pt = prev_tracks.loc[r.track]
            rows.append([r.track, _m(r.paid), _m(r.scheduled), f"{t / total * 100:.0f}%" if total else "-", _chg(t, pt["paid"] + pt["scheduled"])])
        story.append(_section_table(["Track", "Paid", "Scheduled", "Share", "vs previous"], rows, [CONTENT_WIDTH * w for w in (0.32, 0.22, 0.22, 0.12, 0.12)], right_cols=(1, 2, 3, 4)))
        if not cur["payees"].empty:
            story += _section("Top payees", st)
            rows = [[r.payee, r.track, _m(r.paid), _m(r.scheduled), f"{r.total / total * 100:.0f}%" if total else "-"] for r in cur["payees"].head(10).itertuples()]
            story.append(_section_table(["Payee", "Track", "Paid", "Scheduled", "Share"], rows, [CONTENT_WIDTH * w for w in (0.38, 0.16, 0.18, 0.18, 0.10)], right_cols=(2, 3, 4)))
        story += _section("Cost of financing", st)
        if cur["interest"].empty:
            story.append(Paragraph("No interest recorded in this period.", st["grey"]))
        else:
            rows = [[r.source, r.name, _m(r.interest)] for r in cur["interest"].head(8).itertuples()]
            story.append(_section_table(["Source", "Facility", "Interest"], rows, [CONTENT_WIDTH * w for w in (0.22, 0.5, 0.28)], right_cols=(2,)))
        b, pb = cur["behavior"], prev["behavior"]
        story += _section("Payment behavior", st)
        if not b["count"]:
            story.append(Paragraph("Not enough itemised supplier invoices in this period to measure how early or late checks were.", st["grey"]))
        else:
            story.append(Paragraph(f"{b['count']} supplier check(s) cleared; on average {abs(b['avg_days']):.0f} day(s) "
                                   f"{'after' if b['avg_days'] > 0 else 'before'} the due date; {b['on_time_pct']:.0f}% on time"
                                   + (f" (previous: {pb['on_time_pct']:.0f}%)." if pb["count"] else "."), st["body"]))
        story += _section(f"Coming up - {_esc(up['label'])}", st)
        if not up["total"]:
            story.append(Paragraph("Nothing scheduled.", st["grey"]))
        else:
            rows = [[k, _m(v)] for k, v in up["by_track"].items()] + [["TOTAL", _m(up["total"])]]
            story.append(_section_table(["Track", "Amount due"], rows, [CONTENT_WIDTH * w for w in (0.5, 0.5)], right_cols=(1,)))
            if up["peak_days"]:
                story.append(Spacer(1, 6))
                story.append(Paragraph("Heaviest days: " + "; ".join(f"{d} ({_m(a)})" for d, a in up["peak_days"]), st["body"]))

    doc.build(story, onFirstPage=_cover_page, onLaterPages=_inner_page(f"{data['kind']} overview - {data['label']}"))
    return buf.getvalue()
