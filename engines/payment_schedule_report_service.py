"""PDF exports for the two "everything still owed, by due date" reports (Reports tab) —
Uncleared Checks (Supplier + Lender) and the combined all-tracks Schedule of Payments. Modeled
on a real legacy "All Time Payee Checks" report the user shared: grouped by due date, each
date's rows followed by a per-day subtotal, a grand total at the end. Pure presentation layer
over the already-tested engines.reports_service query functions — no data logic lives here.
"""
import io
from datetime import date, datetime
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from engines.reports_service import get_payment_schedule_all_tracks, get_uncleared_checks_report, get_parked_checks_report
from engines.clock import manila_now

ACCENT = colors.HexColor("#1f3a5f")
ACCENT_LIGHT = colors.HexColor("#eef2f7")
GREY = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#d1d5db")

LEFT_MARGIN = RIGHT_MARGIN = 0.4 * inch
TOP_MARGIN = 0.4 * inch
BOTTOM_MARGIN = 0.5 * inch
CONTENT_WIDTH = LETTER[0] - LEFT_MARGIN - RIGHT_MARGIN


def _money(v) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def _or_dash(v) -> str:
    # A NULL column (e.g. "batch" for the three tracks that have none) comes back through
    # pandas as NaN, not None -- and NaN is truthy in Python, so a plain `v or "—"` lets a
    # literal "nan" string through instead of the intended dash. Check explicitly.
    if v is None or v == "" or (isinstance(v, float) and v != v):
        return "—"
    return str(v)


def _title_band(title: str, subtitle: str, styles) -> Table:
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=18, leading=21.4, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=10, leading=13.5, alignment=2)
    band = Table(
        [[Paragraph(_xml_escape(title), title_style), Paragraph(_xml_escape(subtitle), sub_style)]],
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 15.75),
        ("RIGHTPADDING", (0, 0), (-1, -1), 15.75),
        ("TOPPADDING", (0, 0), (-1, -1), 13.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 13.5),
    ]))
    return band


def _date_text(d: date) -> str:
    # %-d (no leading zero) is a glibc extension, not portable to Windows -- build it manually.
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}" if isinstance(d, date) else str(d)


def _build_due_date_schedule_pdf(title: str, subtitle: str, df, ref_label: str) -> bytes:
    """One continuous table for the whole report (column header repeats on every page via
    repeatRows) rather than a separate boxed table per due date -- compact enough to hold
    hundreds of rows across many months without every single date burning its own header bar
    and vertical margin, matching how the reference report itself reads (one running list with
    inline date/subtotal rows, not a new table per day)."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )
    styles = getSampleStyleSheet()
    story: list = [_title_band(title, subtitle, styles), Spacer(1, 8)]
    # Payee Name and Reference can both be long dynamic text (a full company name; a bank-loan
    # reference now prefixed with its beneficiary, e.g. "Emelita Roxas - PENDING-153") -- a
    # plain string in a Table cell doesn't wrap to its column width, it just overflows and
    # visually overlaps the next column. Paragraph cells do wrap (growing the row height
    # instead), so those two columns use this style rather than raw strings. escape() guards
    # against ReportLab's Paragraph parsing "&"/"<" in a real name (e.g. "S&P Enterprises
    # Inc.") as markup instead of literal text.
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=9, leading=10.7)

    def _cell(text) -> Paragraph:
        return Paragraph(_xml_escape(str(text)), cell_style)

    if df.empty:
        story.append(Paragraph("Nothing scheduled.", styles["Normal"]))
    else:
        # Grouped by actual_payment_date, not the raw due_date -- a due date landing on a
        # weekend is paid the next banking day (Monday), so each row also shows its own
        # original Due Date in case it differs from the group's payment date heading.
        # Payee Name and Reference need the room (full company names; a bank-loan reference can
        # carry a beneficiary prefix, e.g. "Emelita Roxas - PENDING-153") and wrap as Paragraph
        # cells. Due Date, Track ("Credit Card") and Amount are unwrapped raw text, so their slice
        # is measured (reportlab stringWidth at Helvetica 9pt + 11pt of cell padding): Due Date
        # header >= 0.094, Track >= 0.107, Amount's own header ("Amount (PHP)") >= 0.135 -- anything
        # tighter overflows past the column instead of wrapping.
        headers = ["Due Date", "Payee Name", "Track", ref_label, "Amount (PHP)"]
        col_widths = [CONTENT_WIDTH * w for w in (0.098, 0.299, 0.111, 0.352, 0.14)]
        n_cols = len(headers)
        amount_col = n_cols - 1
        data = [headers]
        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5.6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5.6),
            ("ALIGN", (amount_col, 0), (amount_col, -1), "RIGHT"),
        ]
        row = 1
        grand_count = 0
        grand_total = 0.0
        for payment_date, group in df.groupby("actual_payment_date", sort=True):
            d = payment_date if isinstance(payment_date, date) else date.fromisoformat(str(payment_date))
            data.append([_date_text(d)] + [""] * (n_cols - 1))
            cmds += [
                ("SPAN", (0, row), (-1, row)),
                ("BACKGROUND", (0, row), (-1, row), ACCENT_LIGHT),
                ("TEXTCOLOR", (0, row), (-1, row), ACCENT),
                ("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"),
                ("TOPPADDING", (0, row), (-1, row), 4.5),
                ("BOTTOMPADDING", (0, row), (-1, row), 2.25),
            ]
            row += 1

            day_total = 0.0
            for _, r in group.iterrows():
                due = r["due_date"] if isinstance(r["due_date"], date) else date.fromisoformat(str(r["due_date"]))
                due_text = due.strftime("%b %d") + (" *" if due != d else "")
                line = [due_text, _cell(r["payee_name"]), r["track"], _cell(_or_dash(r.get("reference")))]
                line.append(_money(r["amount"]))
                data.append(line)
                cmds.append(("LINEBELOW", (0, row), (-1, row), 0.4, BORDER))
                day_total += float(r["amount"])
                row += 1

            data.append([f"{len(group)} item(s)"] + [""] * (n_cols - 2) + [_money(day_total)])
            cmds += [
                ("SPAN", (0, row), (amount_col - 1, row)),
                ("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"),
                ("LINEBELOW", (0, row), (-1, row), 0.75, ACCENT),
                ("BOTTOMPADDING", (0, row), (-1, row), 6.75),
            ]
            row += 1
            grand_count += len(group)
            grand_total += day_total

        # SPAN keeps only the top-left cell's content, so the merged text must live in the
        # first cell of the span, not a later one -- putting it anywhere else silently
        # discards it (caught the hard way earlier this session). "GRAND TOTAL" and the item
        # count used to be two separate, unspanned cells in columns 0/1 -- fine at the old
        # (wider) Due Date column width, but "GRAND TOTAL" alone overflowed the narrower one
        # after the column rebalance, the same overlap class as the payee-name fix above.
        # Combined into one spanned cell, same pattern the daily subtotal row already uses.
        data.append([f"GRAND TOTAL — {grand_count} item(s)"] + ["", ""] + [f"PHP {_money(grand_total)}"] + [""] * (n_cols - 4))
        cmds += [
            ("SPAN", (0, row), (2, row)),
            ("SPAN", (3, row), (amount_col, row)),
            ("FONTNAME", (0, row), (-1, row), "Helvetica-Bold"),
            ("FONTSIZE", (0, row), (-1, row), 11.25),
            ("TEXTCOLOR", (0, row), (-1, row), ACCENT),
            ("LINEABOVE", (0, row), (-1, row), 1.2, ACCENT),
            ("TOPPADDING", (0, row), (-1, row), 9),
            ("ALIGN", (3, row), (3, row), "RIGHT"),
        ]

        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(cmds))
        story.append(table)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Grouped by actual payment date — a due date landing on a weekend is paid the next banking day. "
            "* marks a row whose original due date differs from the payment date shown above it.",
            ParagraphStyle("Note", parent=styles["Normal"], fontSize=7.9, textColor=GREY),
        ))

    story.append(Spacer(1, 10))
    story.append(Paragraph(f"Generated {manila_now().strftime('%Y-%m-%d %H:%M')}", ParagraphStyle("Gen", parent=styles["Normal"], fontSize=8.4, textColor=GREY)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_uncleared_checks_pdf() -> bytes:
    # get_uncleared_checks_report() names its identifier column "check_number", not "reference" --
    # normalize before handing off to the shared builder, which only knows "reference".
    df = get_uncleared_checks_report().rename(columns={"check_number": "reference"})
    return _build_due_date_schedule_pdf("UNCLEARED CHECKS", "BY PAYMENT DATE — SUPPLIER & LENDER", df, "Check Number")


def generate_parked_checks_pdf(month: str | None = None) -> bytes:
    """Every check already recorded but not yet printed (Phase 6 Redux -- check_number is
    assigned at print time), grouped by day the same way as the other two due-date reports.
    month: optional "YYYY-MM" for the usual monthly export."""
    df = get_parked_checks_report()
    if month:
        df = df[df["actual_payment_date"].astype(str).str.startswith(month)]
    subtitle = (date.fromisoformat(month + "-01").strftime("%B %Y").upper() + " — BY CHECK DATE") if month else "ALL PENDING — BY CHECK DATE"
    return _build_due_date_schedule_pdf("PARKED CHECKS (NOT YET PRINTED)", subtitle, df, "Reference")


def generate_payment_schedule_pdf(tracks: list[str] | None = None, month: str | None = None, date_from: date | None = None,
                                  date_to: date | None = None, payees: list[str] | None = None) -> bytes:
    """tracks: optional subset (e.g. just ["Bank Loan"]) to print a single track's schedule
    instead of all four. month: optional "YYYY-MM" to print just one month's payments (the usual
    monthly export) instead of the whole future schedule; date_from / date_to (2026-09-28) any
    payment-date range; payees: only these payees. All mirror whatever's currently filtered on
    screen (reports_service.filter_schedule), so the PDF/CSV exports show exactly what's displayed."""
    from calendar import monthrange

    from engines.reports_service import filter_schedule

    if month:
        y, m = int(month[:4]), int(month[5:7])
        date_from, date_to = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    df = filter_schedule(get_payment_schedule_all_tracks(), tracks, date_from, date_to, payees)
    subtitle_bits = []
    if tracks:
        subtitle_bits.append(", ".join(tracks).upper())
    if month:
        subtitle_bits.append(date.fromisoformat(month + "-01").strftime("%B %Y").upper())
    elif date_from or date_to:
        subtitle_bits.append(f"{date_from:%b %d, %Y}".upper() + " TO " + f"{date_to:%b %d, %Y}".upper() if date_from and date_to
                             else (f"FROM {date_from:%b %d, %Y}" if date_from else f"UNTIL {date_to:%b %d, %Y}").upper())
    if payees:
        subtitle_bits.append((", ".join(payees) if len(payees) <= 3 else f"{len(payees)} PAYEES").upper())
    subtitle = f"{' — '.join(subtitle_bits)} — BY PAYMENT DATE" if subtitle_bits else "ALL TRACKS — BY PAYMENT DATE"
    return _build_due_date_schedule_pdf("SCHEDULE OF PAYMENTS", subtitle, df, "Reference")
