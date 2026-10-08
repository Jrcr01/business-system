import io
import sqlite3
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import CondPageBreak, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import os
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

def _section_style(pad: float = 5) -> list:
    """Table style with the given top/bottom cell padding (5 = normal; smaller when a statement only just overflows a page, see _fit_pad)."""
    return [
    ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
    ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ACCENT_LIGHT]),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("TOPPADDING", (0, 0), (-1, -1), pad),
    ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
    ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]


SECTION_TABLE_STYLE = _section_style(5)


def _money(v) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def _printed_sql(alias: str) -> str:
    """SQL condition: this check counts as PRINTED (2026-09-27, per user): it belongs to a Chrysanth run that has been marked printed (Batch run
    history > Mark as printed) -- or it was printed the old in-app way (no run, printed_at set). A check that is only parked, or exported in a
    run not marked printed yet (e.g. RUN 005), is NOT printed."""
    a = alias
    return (f"((({a}.print_batch_id IS NOT NULL) AND EXISTS (SELECT 1 FROM check_print_batches _b WHERE _b.print_batch_id = {a}.print_batch_id AND _b.printed_confirmed_at IS NOT NULL))"
            f" OR ({a}.print_batch_id IS NULL AND {a}.printed_at IS NOT NULL))")


def _invoice_display_status(inv, cover) -> str:
    """The status printed on the statement (2026-09-27, per user): 'Unpaid' on a statement whose checks are already issued read as
    confusing. So: Unpaid = no check covers it; Partly covered = checks cover only part; 'Check issued . <date>' = fully covered by issued
    checks that have not all cleared yet (date = the last of those checks); Paid = every covering check has cleared. An invoice with no
    per-invoice check tracking (issued before it existed) keeps its stored status. Only the printed label -- the stored status that the
    payables and reports rely on is unchanged."""
    stored = inv["status"]
    if stored == "Cancelled":
        return "Cancelled"
    if cover is None:
        return {"Partially Paid": "Partly paid"}.get(stored, stored)
    amount, covered, cleared = float(inv["amount"]), float(cover["covered"] or 0), float(cover["cleared"] or 0)
    if amount - cleared <= 0.005:
        return "Paid"
    if amount - covered <= 0.005:
        return f"Check issued \u00b7 {str(cover['last_check_date'])[:10]}"
    return "Partly covered" if covered > 0.005 else "Unpaid"


def _fetch_batch_data(statement_id: int, include_full_history: bool, include_unprinted: bool = True) -> dict | None:
    with get_conn(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        stmt = cur.execute(
            """SELECT os.*, c.creditor_name, v.credit_memo_total, v.net_total
               FROM order_statements os
               JOIN creditors c ON os.creditor_id = c.creditor_id
               JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
               WHERE os.statement_id = ?""",
            (statement_id,),
        ).fetchone()
        if not stmt:
            return None
        # The status printed in the header follows the same rule as the order statement browser: Draft = no checks, Parked = checks parked but none
        # printed yet, Issued = at least one check already exported / printed (2026-09-27).
        printed, active = cur.execute(
            "SELECT COALESCE(SUM(printed_at IS NOT NULL), 0), COUNT(*) FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (statement_id,)).fetchone()
        stage = "Cancelled" if stmt["status"] == "Cancelled" else "Issued" if printed else "Parked" if active else "Draft"
        stmt = dict(stmt, status=stage)
        invoice_rows = cur.execute(
            """SELECT invoice_id, doc_ref, dr_no, invoice_date, due_date, gross_amount, amount, status
               FROM invoices WHERE statement_id = ? ORDER BY invoice_id""",
            (statement_id,),
        ).fetchall()
        cover = {
            r["invoice_id"]: r for r in cur.execute(
                """SELECT a.invoice_id, SUM(a.amount) AS covered,
                          SUM(CASE WHEN c.bank_cleared_date IS NOT NULL OR c.manual_cleared_at IS NOT NULL OR c.settled_outside_at IS NOT NULL THEN a.amount ELSE 0 END) AS cleared,
                          MAX(c.check_date) AS last_check_date
                   FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id
                   WHERE c.status = 'Active' AND a.invoice_id IN (SELECT invoice_id FROM invoices WHERE statement_id = ?)
                     """ + ("" if include_unprinted else "AND " + _printed_sql("c")) + """
                   GROUP BY a.invoice_id""",
                (statement_id,),
            ).fetchall()
        }
        invoices = [dict(r, display_status=_invoice_display_status(r, cover.get(r["invoice_id"]))) for r in invoice_rows]
        memos = cur.execute(
            """SELECT memo_ref, memo_date, amount, reason
               FROM credit_memos WHERE statement_id = ? ORDER BY credit_memo_id""",
            (statement_id,),
        ).fetchall()
        check_sql = "SELECT check_number, check_date, amount, status FROM checks_issued WHERE statement_id = ?"
        if not include_full_history:
            check_sql += " AND status = 'Active'"
        if not include_unprinted:
            check_sql += " AND " + _printed_sql("checks_issued")
        check_sql += " ORDER BY check_id"
        checks = cur.execute(check_sql, (statement_id,)).fetchall()

        # Payment application ledger source data: only Active checks count (a Cancelled/Replaced
        # check's money never actually left, so it doesn't belong in a "how it was paid down"
        # ledger) -- each check's own allocation rows (migration 025) if it has any, else the
        # check itself as one "Not itemized" line so the ledger still reconciles to the real
        # balance even for a check issued before per-invoice tracking existed.
        active_checks = cur.execute(
            "SELECT check_id, check_number, check_date, amount FROM checks_issued WHERE statement_id = ? AND status = 'Active'"
            + ("" if include_unprinted else " AND " + _printed_sql("checks_issued")) + " ORDER BY check_date, check_id",
            (statement_id,),
        ).fetchall()
        alloc_rows = cur.execute(
            """SELECT a.check_id, i.doc_ref, a.amount FROM invoice_check_allocations a
               JOIN invoices i ON i.invoice_id = a.invoice_id
               WHERE a.check_id IN (SELECT check_id FROM checks_issued WHERE statement_id = ?)
               ORDER BY i.invoice_id""",
            (statement_id,),
        ).fetchall()
    allocations_by_check: dict[int, list] = {}
    for r in alloc_rows:
        allocations_by_check.setdefault(r["check_id"], []).append((r["doc_ref"], r["amount"]))

    running_balance = float(stmt["net_total"])
    payment_ledger = []
    for chk in active_checks:
        allocs = allocations_by_check.get(chk["check_id"])
        if allocs:
            for doc_ref, amount in allocs:
                running_balance -= float(amount)
                payment_ledger.append({"invoice": doc_ref, "check_number": chk["check_number"] or "Pending", "check_date": chk["check_date"], "amount": amount, "balance": running_balance})
        else:
            running_balance -= float(chk["amount"])
            payment_ledger.append({"invoice": "Not itemized", "check_number": chk["check_number"] or "Pending", "check_date": chk["check_date"], "amount": chk["amount"], "balance": running_balance})

    return {"statement": stmt, "invoices": invoices, "memos": memos, "checks": checks, "payment_ledger": payment_ledger}


def _title_band(copy_label: str, styles) -> Table:
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    band = Table(
        [[Paragraph("BATCH / ORDER STATEMENT", title_style), Paragraph(copy_label.upper(), sub_style)]],
        colWidths=[CONTENT_WIDTH * 0.68, CONTENT_WIDTH * 0.32],
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


def _info_panel(stmt, styles) -> Table:
    label_style = ParagraphStyle("InfoLabel", parent=styles["Normal"], fontSize=7.5, textColor=GREY, leading=10)
    value_style = ParagraphStyle("InfoValue", parent=styles["Normal"], fontSize=9.5, leading=13, fontName="Helvetica-Bold")
    rows = [
        [Paragraph("STATEMENT REF", label_style), Paragraph("SUPPLIER", label_style),
         Paragraph("STATUS", label_style), Paragraph("GENERATED", label_style)],
        [Paragraph(stmt["statement_ref"], value_style), Paragraph(stmt["creditor_name"], value_style),
         Paragraph(stmt["status"], value_style), Paragraph(manila_now().strftime("%Y-%m-%d %H:%M"), value_style)],
    ]
    panel = Table(rows, colWidths=[CONTENT_WIDTH * 0.3, CONTENT_WIDTH * 0.3, CONTENT_WIDTH * 0.18, CONTENT_WIDTH * 0.22])
    panel.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return panel


def _section_heading(text: str, styles) -> Paragraph:
    style = ParagraphStyle("SectionHeading", parent=styles["Heading2"], textColor=ACCENT, fontSize=11, spaceBefore=14, spaceAfter=6)
    return Paragraph(text, style)


def _add_section(story: list, text: str, styles) -> None:
    """A section heading that is never left stranded at the bottom of a page away from its table (2026-09-27): if less than ~1.6 inch is left,
    the page breaks first, so the heading always travels with the table's header row and its first few rows."""
    story.append(CondPageBreak(1.6 * inch))
    story.append(_section_heading(text, styles))


def _build_copy(story: list, data: dict, copy_label: str, styles, show_payment_application: bool = False, pad: float = 5) -> None:
    stmt = data["statement"]

    story.append(_title_band(copy_label, styles))
    story.append(Spacer(1, 10))
    story.append(_info_panel(stmt, styles))

    _add_section(story, "Invoices", styles)
    inv_rows = [["Sales Invoice", "Invoice Date", "Due Date", "Gross (PHP)", "Net (PHP)", "Status"]]
    for i in data["invoices"]:
        inv_rows.append([i["doc_ref"] + (f" (DR {i['dr_no']})" if i.get("dr_no") else ""), i["invoice_date"], i["due_date"], _money(i["gross_amount"]), _money(i["amount"]), i["display_status"]])
    if len(inv_rows) == 1:
        inv_rows.append(["—", "—", "—", "—", "—", "—"])
    inv_table = Table(inv_rows, colWidths=[CONTENT_WIDTH * w for w in (0.15, 0.14, 0.14, 0.14, 0.14, 0.29)], repeatRows=1)
    inv_table.setStyle(TableStyle(_section_style(pad) + [("ALIGN", (3, 1), (4, -1), "RIGHT")]))
    story.append(inv_table)

    if data["memos"]:
        _add_section(story, "Credit Memos (applied pre-issuance)", styles)
        memo_rows = [["Memo Ref", "Date", "Amount (PHP)", "Reason"]]
        for m in data["memos"]:
            memo_rows.append([m["memo_ref"], m["memo_date"], _money(m["amount"]), m["reason"] or ""])
        memo_table = Table(memo_rows, colWidths=[CONTENT_WIDTH * w for w in (0.2, 0.17, 0.18, 0.45)], repeatRows=1)
        memo_table.setStyle(TableStyle(_section_style(pad) + [("ALIGN", (2, 1), (2, -1), "RIGHT")]))
        story.append(memo_table)

    story.append(Spacer(1, 10))
    totals_rows = [
        ["Gross Total", f"PHP {stmt['total_amount']:,.2f}"],
        ["Credit Memos", f"PHP {stmt['credit_memo_total']:,.2f}"],
        ["Net Total (checks cut against this)", f"PHP {stmt['net_total']:,.2f}"],
    ]
    totals_table = Table(totals_rows, colWidths=[CONTENT_WIDTH * 0.72, CONTENT_WIDTH * 0.28])
    totals_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), ACCENT),
        ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(totals_table)

    _add_section(story, "Checks", styles)
    chk_rows = [["Check Number", "Check Date", "Amount (PHP)", "Status"]]
    for c in data["checks"]:
        chk_rows.append([c["check_number"] or "Pending", c["check_date"] or "—", _money(c["amount"]), c["status"]])
    if len(chk_rows) == 1:
        chk_rows.append(["—", "—", "—", "—"])
    chk_table = Table(chk_rows, colWidths=[CONTENT_WIDTH * w for w in (0.3, 0.23, 0.22, 0.25)], repeatRows=1)
    chk_table.setStyle(TableStyle(_section_style(pad) + [("ALIGN", (2, 1), (2, -1), "RIGHT")]))
    story.append(chk_table)

    if show_payment_application:
        _add_section(story, "Payment Application", styles)
        ledger = data["payment_ledger"]
        led_rows = [["Invoice", "Check Number", "Check Date", "Amount Applied (PHP)", "Balance (PHP)"]]
        for r in ledger:
            led_rows.append([r["invoice"], r["check_number"], r["check_date"] or "—", _money(r["amount"]), _money(r["balance"])])
        if len(led_rows) == 1:
            led_rows.append(["—", "—", "—", "—", "—"])
        led_table = Table(led_rows, colWidths=[CONTENT_WIDTH * w for w in (0.26, 0.22, 0.16, 0.18, 0.18)], repeatRows=1)
        led_table.setStyle(TableStyle(_section_style(pad) + [("ALIGN", (3, 1), (4, -1), "RIGHT")]))
        story.append(led_table)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "One row per invoice a check's amount was applied to, in the order checks were issued — balance is the batch's net total running down as each one is applied. "
            "Only Active checks are included (a Cancelled/Replaced check's money never actually left); “Not itemized” means that check was issued before per-invoice tracking existed.",
            ParagraphStyle("Note", parent=styles["Normal"], fontSize=7, textColor=GREY),
        ))
    # (no trailing spacer: on a page the content fills almost to the bottom it spilled onto a page of its own -- a blank sheet with only the footer)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(LEFT_MARGIN, BOTTOM_MARGIN - 10, LETTER[0] - RIGHT_MARGIN, BOTTOM_MARGIN - 10)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GREY)
    canvas.drawString(LEFT_MARGIN, BOTTOM_MARGIN - 22, "AP Disbursement System — Metrobank Account 1 · Branch 399")
    canvas.drawRightString(LETTER[0] - RIGHT_MARGIN, BOTTOM_MARGIN - 22, f"Page {doc.page}")
    canvas.restoreState()


def generate_batch_statement_pdf(statement_id: int, include_full_history: bool = True, copies: str = "both", show_payment_application: bool = False,
                                 include_unprinted_checks: bool = True) -> bytes | None:
    """Batch Statement PDF. copies: "both" (internal filing + supplier, default),
    "filing" (internal filing copy only), or "supplier" (supplier copy only).

    include_full_history=True lists every check ever issued against the batch
    (issued/cancelled/replaced); False lists only the currently active check(s).

    show_payment_application=True (2026-09-14, opt-in) adds a "Payment Application" ledger below
    the Checks table -- one row per invoice a check's amount was applied to (migration 025),
    with a running balance -- off by default so the existing report is unchanged unless asked for.
    """
    data = _fetch_batch_data(statement_id, include_full_history, include_unprinted_checks)
    if data is None:
        return None
    labels = []
    if copies in ("both", "filing"):
        labels.append("Internal Filing Copy")
    if copies in ("both", "supplier"):
        labels.append("Supplier Copy")

    def build(pad: float) -> tuple[bytes, int]:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=LETTER,
            leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
        )
        styles = getSampleStyleSheet()
        story: list = []
        for i, label in enumerate(labels):
            if i > 0:
                story.append(PageBreak())
            _build_copy(story, data, label, styles, show_payment_application, pad)
        doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
        return buffer.getvalue(), doc.page

    # A statement that overflows a page by just a row or two used to leave a page holding almost nothing (2026-09-27). So: build at the normal
    # row height, and only if tighter rows would save whole pages, use the roomiest tightening that gets the fewest pages.
    best_pdf, best_pages = build(5)
    for pad in (4, 3, 2.5):
        if best_pages <= len(labels):
            break
        pdf, pages = build(pad)
        if pages < best_pages:
            best_pdf, best_pages = pdf, pages
    return best_pdf


def bulk_summary_rows(statement_ids: list[int], include_unprinted_checks: bool = True) -> list[dict]:
    """One line per order statement for the bulk-print summary: ref, supplier, stage (Draft / Parked / Issued / Cancelled -- the same rule as the
    order statement browser), invoice count, net total, active checks and their total."""
    if not statement_ids:
        return []
    marks = ",".join("?" * len(statement_ids))
    unprinted_filter = "" if include_unprinted_checks else "AND " + _printed_sql("k")
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            f"""SELECT os.statement_id, os.statement_ref, cr.creditor_name, os.status, v.net_total,
                       (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = os.statement_id AND i.status != 'Cancelled'),
                       (SELECT COUNT(*) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active' {unprinted_filter}),
                       (SELECT COALESCE(SUM(k.amount), 0) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active' {unprinted_filter}),
                       (SELECT COUNT(*) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active' AND k.printed_at IS NOT NULL)
                FROM order_statements os JOIN creditors cr ON cr.creditor_id = os.creditor_id JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
                WHERE os.statement_id IN ({marks})""",
            [int(x) for x in statement_ids],
        ).fetchall()
    by_id = {r[0]: r for r in rows}
    out = []
    for sid in statement_ids:
        r = by_id.get(int(sid))
        if not r:
            continue
        stage = "Cancelled" if r[3] == "Cancelled" else "Issued" if r[8] else "Parked" if r[6] else "Draft"
        out.append({"statement_id": r[0], "ref": r[1], "supplier": r[2], "stage": stage, "net_total": float(r[4] or 0), "invoices": int(r[5]),
                    "checks": int(r[6]), "checks_total": float(r[7] or 0)})
    return out


def generate_bulk_summary_pdf(rows: list[dict], scope_label: str, statement_type: str) -> bytes:
    """Cover sheet for a bulk print: what is in the combined PDF -- one line per order statement with its supplier, status, invoices, net total and
    checks, plus grand totals -- so the stack can be checked against it."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=LETTER, leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
                            title="Bulk statement print - summary")
    styles = getSampleStyleSheet()
    story: list = [_title_band("Bulk print summary", styles), Spacer(1, 10)]
    info_style = ParagraphStyle("BulkInfo", parent=styles["Normal"], fontSize=9, leading=13, textColor=colors.HexColor("#374151"))
    story.append(Paragraph(
        f"<b>{len(rows)}</b> order statement(s) &nbsp;·&nbsp; {scope_label} &nbsp;·&nbsp; {statement_type} &nbsp;·&nbsp; printed {manila_now():%Y-%m-%d %H:%M}", info_style))
    story.append(Spacer(1, 8))
    table_rows = [["#", "Order statement", "Supplier", "Status", "Inv.", "Net total (PHP)", "Checks", "Checks total (PHP)"]]
    for n, r in enumerate(rows, start=1):
        table_rows.append([str(n), r["ref"], r["supplier"][:34], r["stage"], str(r["invoices"]), _money(r["net_total"]), str(r["checks"]), _money(r["checks_total"])])
    table_rows.append(["", "TOTAL", "", "", str(sum(r["invoices"] for r in rows)), _money(sum(r["net_total"] for r in rows)),
                       str(sum(r["checks"] for r in rows)), _money(sum(r["checks_total"] for r in rows))])
    widths = [CONTENT_WIDTH * w for w in (0.05, 0.15, 0.27, 0.09, 0.06, 0.14, 0.08, 0.16)]
    table = Table(table_rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle(_section_style(4) + [
        ("ALIGN", (4, 1), (4, -1), "RIGHT"), ("ALIGN", (5, 1), (5, -1), "RIGHT"), ("ALIGN", (6, 1), (6, -1), "RIGHT"), ("ALIGN", (7, 1), (7, -1), "RIGHT"),
        ("ALIGN", (0, 1), (0, -1), "RIGHT"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT), ("BACKGROUND", (0, -1), (-1, -1), ACCENT_LIGHT),
    ]))
    story.append(table)
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


# --- 80mm thermal-printer statement (continuous roll, single copy) ---------

THERMAL_WIDTH_MM = 80
THERMAL_MAX_HEIGHT_MM = 3276  # printer/driver's typical max continuous-roll length
THERMAL_MARGIN_MM = 3
THERMAL_WIDTH_PT = THERMAL_WIDTH_MM * mm
THERMAL_MAX_HEIGHT_PT = THERMAL_MAX_HEIGHT_MM * mm
THERMAL_CONTENT_WIDTH_PT = THERMAL_WIDTH_PT - 2 * THERMAL_MARGIN_MM * mm

THERMAL_FONT_HEAD = "Helvetica-Bold"
THERMAL_FONT_BODY = "Helvetica"
THERMAL_FONT_MONO = "Courier"

THERMAL_SIZE_TITLE = 10
THERMAL_SIZE_SECTION = 8.5
THERMAL_SIZE_BODY = 7.5
THERMAL_SIZE_SMALL = 6.5


def _thermal_divider(ch: str = "-", font: str = THERMAL_FONT_MONO, size: float = THERMAL_SIZE_SMALL) -> str:
    char_w = stringWidth(ch, font, size)
    n = max(1, int(THERMAL_CONTENT_WIDTH_PT // char_w))
    return ch * n


def _thermal_lines(data: dict) -> list[dict]:
    """Flat top-to-bottom list of drawable line specs for the thermal strip.
    Built (not drawn) first so the exact page height can be computed before
    a Canvas is created — a fixed-size ReportLab page can't grow to fit content."""
    stmt = data["statement"]
    lines: list[dict] = []

    def add(text, *, font=THERMAL_FONT_BODY, size=THERMAL_SIZE_BODY, align="left", color=colors.black, gap_after=0.0):
        lines.append({"text": text, "font": font, "size": size, "align": align, "height": size * 1.45, "color": color})
        if gap_after:
            lines.append({"text": "", "font": font, "size": size, "align": align, "height": gap_after, "color": color})

    def add_divider(ch="-"):
        add(_thermal_divider(ch), font=THERMAL_FONT_MONO, size=THERMAL_SIZE_SMALL, align="center")

    add("AP DISBURSEMENT SYSTEM", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_TITLE, align="center")
    add("BATCH / ORDER STATEMENT", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_SECTION, align="center", gap_after=2)
    add_divider("=")
    add(f"Ref: {stmt['statement_ref']}")
    add(f"Supplier: {stmt['creditor_name']}")
    add(f"Status: {stmt['status']}")
    add(f"Generated: {manila_now().strftime('%Y-%m-%d %H:%M')}", gap_after=2)
    add_divider()

    add("INVOICES", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_SECTION)
    if not data["invoices"]:
        add("  (none)")
    for i in data["invoices"]:
        add(i["doc_ref"])
        add(f"  Recv {i['invoice_date']}  Due {i['due_date']}", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_SMALL, color=GREY)
        add(f"  Net PHP {_money(i['amount'])}   [{i['display_status']}]", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_SMALL)
    add_divider()

    if data["memos"]:
        add("CREDIT MEMOS", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_SECTION)
        for m in data["memos"]:
            add(f"{m['memo_ref']}  {m['memo_date']}")
            add(f"  -PHP {_money(m['amount'])}  {m['reason'] or ''}", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_SMALL)
        add_divider()

    add(f"Gross Total:   PHP {stmt['total_amount']:,.2f}", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_BODY)
    add(f"Credit Memos:  PHP {stmt['credit_memo_total']:,.2f}", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_BODY)
    add(f"NET TOTAL:     PHP {stmt['net_total']:,.2f}", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_BODY + 0.5)
    add_divider()

    add("CHECKS", font=THERMAL_FONT_HEAD, size=THERMAL_SIZE_SECTION)
    if not data["checks"]:
        add("  (none)")
    for c in data["checks"]:
        add(f"Check #{c['check_number'] or 'Pending'}   [{c['status']}]")
        add(f"  {c['check_date'] or '--'}   PHP {_money(c['amount'])}", font=THERMAL_FONT_MONO, size=THERMAL_SIZE_SMALL)
    add_divider("=")
    add("Metrobank Account 1 - Branch 399", font=THERMAL_FONT_BODY, size=THERMAL_SIZE_SMALL, align="center")
    add("*** Nothing follows ***", font=THERMAL_FONT_BODY, size=THERMAL_SIZE_SMALL, align="center", gap_after=6)

    return lines


def _draw_thermal(c: pdfcanvas.Canvas, lines: list[dict], y_start: float) -> None:
    x_left = THERMAL_MARGIN_MM * mm
    x_right = THERMAL_WIDTH_PT - THERMAL_MARGIN_MM * mm
    x_center = THERMAL_WIDTH_PT / 2
    y = y_start
    for spec in lines:
        y -= spec["height"]
        if spec["text"]:
            c.setFont(spec["font"], spec["size"])
            c.setFillColor(spec["color"])
            if spec["align"] == "center":
                c.drawCentredString(x_center, y, spec["text"])
            elif spec["align"] == "right":
                c.drawRightString(x_right, y, spec["text"])
            else:
                c.drawString(x_left, y, spec["text"])
    c.setFillColor(colors.black)


def generate_batch_statement_thermal_pdf(statement_id: int, include_full_history: bool = True, include_unprinted_checks: bool = True) -> bytes | None:
    """80mm-wide thermal-printer Batch Statement: one continuous strip (no page
    breaks), auto-sized to the batch's actual content and capped at the
    printer's max continuous-roll length (3276mm) rather than always printing
    a fixed-length page. Single copy — a thermal roll isn't meant to hold two."""
    data = _fetch_batch_data(statement_id, include_full_history, include_unprinted_checks)
    if data is None:
        return None
    lines = _thermal_lines(data)
    margin = THERMAL_MARGIN_MM * mm
    content_height = sum(l["height"] for l in lines)
    page_height = min(content_height + 2 * margin, THERMAL_MAX_HEIGHT_PT)

    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(THERMAL_WIDTH_PT, page_height))
    _draw_thermal(c, lines, page_height - margin)
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
