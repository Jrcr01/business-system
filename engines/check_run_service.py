"""The "current check run" workflow: park checks (already fully recorded in checks_issued the
moment they're issued) across as many batches as needed, watch a running total build up, then
print the whole run at once and get a consolidated summary. Unlike the 4-track Payment Schedule
(engines/reports_service.py), this only ever concerns Supplier/Lender checks_issued rows -- Bank
Loan and Credit Card settlement is untracked cash and never touches checks_issued at all -- and it
needs printed_at (migration 020), which that report doesn't select, so it isn't reused directly.
"""
import io
import sqlite3
from datetime import datetime

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from pypdf import PdfWriter, PdfReader
except ImportError:
    PdfWriter = PdfReader = None

from engines.check_engine import generate_metrobank_check_pdf

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

ACCENT = colors.HexColor("#1f3a5f")
LEFT_MARGIN = RIGHT_MARGIN = 0.55 * inch
TOP_MARGIN = 0.5 * inch
BOTTOM_MARGIN = 0.65 * inch
CONTENT_WIDTH = LETTER[0] - LEFT_MARGIN - RIGHT_MARGIN

_CHECK_ROW_SELECT = """
    SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name AS payee_name,
           CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track,
           COALESCE(os.statement_ref, ln.note_ref) AS reference
    FROM checks_issued c
    JOIN creditors cr ON cr.creditor_id = c.creditor_id
    LEFT JOIN order_statements os ON os.statement_id = c.statement_id
    LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
"""


def get_pending_check_run() -> pd.DataFrame:
    """Every committed-but-not-yet-printed check across every batch, Supplier and Lender alike --
    this *is* "the current check run," derived on demand rather than tracked as a separate
    entity (no check_runs table)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            _CHECK_ROW_SELECT + " WHERE c.status = 'Active' AND c.printed_at IS NULL ORDER BY c.check_date ASC, c.check_id ASC",
            conn,
        )


def generate_pending_check_run_excel(check_ids: list[int] | None = None) -> bytes:
    """Excel export of the current pending check run, for batch-printing outside this system
    (2026-09-17, per user's workflow: park every check here across all batches, export once,
    import the file into Chrysanth's own batch-print feature, then come back and assign check
    numbers -- printing itself no longer happens in this app). check_ids optionally narrows the
    export to a subset of get_pending_check_run()'s rows (e.g. the checkboxes the user picked),
    matching the same all-selected-by-default pattern the old in-app print picker used.

    Column names/order are a reasonable generic default (Payee Name, Amount, Check Date,
    Reference), not copied from Chrysanth's own import spec -- adjust here if Chrysanth's import
    wizard expects specific headers or a specific order."""
    df = get_pending_check_run()
    if check_ids is not None:
        df = df[df["check_id"].isin(check_ids)]
    export_df = pd.DataFrame({
        "Payee Name": df["payee_name"],
        "Amount": df["amount"],
        "Check Date": pd.to_datetime(df["check_date"]),
        "Reference": df["reference"],
    })
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Checks to Print")
    buffer.seek(0)
    return buffer.getvalue()


CHRYSANTH_COLUMNS = ["Date", "Account No", "Payee", "Amount", "Cheque No", "Remark", "A/C Payee Only"]


def _chrysanth_csv(df: pd.DataFrame) -> bytes:
    dates = pd.to_datetime(df["check_date"])
    export_df = pd.DataFrame({
        "Date": [f"{d.month}/{d.day}/{d.year}" for d in dates],
        "Account No": "",
        "Payee": df["payee_name"].values,
        "Amount": [f"{float(a):.2f}" for a in df["amount"]],
        "Cheque No": df["check_number"].fillna("").astype(str).values,
        "Remark": df["reference"].fillna("").astype(str).values,
        "A/C Payee Only": "Y",
    }, columns=CHRYSANTH_COLUMNS)
    return export_df.to_csv(index=False).encode("utf-8-sig")


def generate_pending_check_run_csv(check_ids: list[int] | None = None) -> bytes:
    """CSV in Chrysanth's own cheque-transaction layout (2026-09-21, from the sample the user shared):
    Date, Account No, Payee, Amount, Cheque No, Remark, A/C Payee Only. Account No is left empty (Chrysanth
    uses its default account), Cheque No is only filled when a number has already been assigned (numbers are
    normally assigned after printing), Remark is the batch / loan-note reference, and every check is
    crossed "A/C Payee Only" (Y). Dates are month/day/year like the sample; UTF-8 with a BOM so Excel opens it cleanly."""
    df = get_pending_check_run()
    if check_ids is not None:
        df = df[df["check_id"].isin(check_ids)]
    return _chrysanth_csv(df)


def generate_batch_csv(print_batch_id: int) -> bytes:
    """The same CSV for a batch that was already exported -- so a file that was lost or never downloaded can
    be produced again from the batch history (the checks stay tagged with their print_batch_id)."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            _CHECK_ROW_SELECT + " WHERE c.print_batch_id = ? ORDER BY c.check_date ASC, c.check_id ASC", conn, params=(int(print_batch_id),))
    return _chrysanth_csv(df)


def get_unnumbered_checks() -> pd.DataFrame:
    """Every Active check that doesn't have a check_number yet, across every batch, Supplier and
    Lender alike -- regardless of printed_at (Phase 6 Redux: printing and number-assignment are
    independent actions, so a check can be printed-but-unnumbered or numbered-but-unprinted)."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            _CHECK_ROW_SELECT + " WHERE c.status = 'Active' AND c.check_number IS NULL ORDER BY c.check_date ASC, c.check_id ASC",
            conn,
        )


def get_checks_by_ids(check_ids: list[int]) -> pd.DataFrame:
    """Same row shape as get_pending_check_run(), but by check_id regardless of printed_at --
    used for the run summary, which must still report correctly on a set of checks that have
    already been marked printed by the time it's generated (calling order shouldn't matter)."""
    if not check_ids:
        return pd.DataFrame(columns=["check_id", "check_number", "check_date", "amount", "payee_name", "track", "reference"])
    placeholders = ",".join("?" * len(check_ids))
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            _CHECK_ROW_SELECT + f" WHERE c.check_id IN ({placeholders}) ORDER BY c.check_date ASC, c.check_id ASC",
            conn, params=check_ids,
        )


def mark_checks_printed(check_ids: list[int]) -> None:
    """Stamps printed_at for the given checks -- only where it's still NULL, so re-invoking on a
    reprint doesn't overwrite the original first-printed timestamp. Independent of check_number
    (Phase 6 Redux: printing and number-assignment are separate actions) -- this never touches
    check_number/booklet_id."""
    if not check_ids:
        return
    with sqlite3.connect(DB_PATH) as conn:
        placeholders = ",".join("?" * len(check_ids))
        conn.execute(f"UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP WHERE check_id IN ({placeholders}) AND printed_at IS NULL", check_ids)
        conn.commit()


def create_check_print_batch(check_ids: list[int]) -> int | None:
    """Logs one row in check_print_batches for this export and tags each of the given checks
    with its print_batch_id -- the "what went out for printing, and when" record now that
    printing happens outside this system (Chrysanth). Also stamps printed_at, same as
    mark_checks_printed() did for the old in-app print button, and for the same reason: this
    export IS the moment these checks left for printing. Returns None (and touches nothing) if
    check_ids is empty -- there's no batch to log."""
    if not check_ids:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        placeholders = ",".join("?" * len(check_ids))
        total_row = conn.execute(f"SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE check_id IN ({placeholders})", check_ids).fetchone()
        cur = conn.execute(
            "INSERT INTO check_print_batches (check_count, total_amount) VALUES (?, ?)",
            (len(check_ids), float(total_row[0])),
        )
        batch_id = cur.lastrowid
        conn.execute(
            f"UPDATE checks_issued SET print_batch_id = ?, printed_at = COALESCE(printed_at, CURRENT_TIMESTAMP) WHERE check_id IN ({placeholders})",
            [batch_id] + check_ids,
        )
        conn.commit()
    return batch_id


def get_check_print_batches() -> pd.DataFrame:
    """Every export batch logged so far, most recent first -- the "Batch 1, Batch 2, ..."
    history list."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT print_batch_id, datetime(exported_at, 'localtime') AS exported_at, check_count, total_amount FROM check_print_batches ORDER BY print_batch_id DESC",
            conn,
        )


def get_check_print_batch_detail(batch_id: int) -> pd.DataFrame:
    """Every check that went out in this specific export batch -- check number (as of now; a
    batch is logged at export time, before numbers are typically known, so this reflects
    whatever's been assigned to each check SINCE, not a snapshot frozen at export), check date,
    payee, which booklet its number came from (if assigned), and the OS/lender reference it
    belongs to."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name AS payee_name,
                      CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track,
                      COALESCE(os.statement_ref, ln.note_ref) AS reference,
                      cb.booklet_code
               FROM checks_issued c
               JOIN creditors cr ON cr.creditor_id = c.creditor_id
               LEFT JOIN order_statements os ON os.statement_id = c.statement_id
               LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
               LEFT JOIN check_booklets cb ON cb.booklet_id = c.booklet_id
               WHERE c.print_batch_id = ?
               ORDER BY c.check_date ASC, c.check_id ASC""",
            conn, params=(batch_id,),
        )


def generate_check_run_pdf(check_ids: list[int], get_calibration) -> bytes | None:
    """Merges one page per distinct check (not two copies of the same one -- that pattern is for
    a Batch Statement's filing/supplier copies, not an actual negotiable instrument). Reuses
    generate_metrobank_check_pdf() per check, with that check's own bank account's calibration
    (get_calibration is app.py's get_check_print_calibration, passed in rather than imported to
    avoid this engine reaching into app.py)."""
    if PdfWriter is None or not check_ids:
        return None
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(check_ids))
        rows = conn.execute(
            f"SELECT check_id, bank_account_id, check_date, amount, creditor_id FROM checks_issued WHERE check_id IN ({placeholders})",
            check_ids,
        ).fetchall()
        payee_by_creditor = {
            r["creditor_id"]: r["creditor_name"]
            for r in conn.execute("SELECT creditor_id, creditor_name FROM creditors").fetchall()
        }

    writer = PdfWriter()
    for r in rows:
        calib = get_calibration(r["bank_account_id"])
        page_bytes = generate_metrobank_check_pdf(
            payee_by_creditor.get(r["creditor_id"], ""), float(r["amount"]),
            datetime.strptime(r["check_date"], "%Y-%m-%d"),
            check_width_mm=calib["check_width_mm"], check_height_mm=calib["check_height_mm"],
            offset_x_mm=calib["offset_x_mm"], offset_y_mm=calib["offset_y_mm"],
        )
        reader = PdfReader(io.BytesIO(page_bytes))
        writer.add_page(reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def generate_check_run_summary_pdf(check_ids: list[int]) -> bytes:
    """A consolidated internal-control record of exactly what got printed in one run -- distinct
    from the existing per-batch Batch Statement (that one is for the supplier to collect against;
    this one is for whoever printed the run to keep on file)."""
    df = get_checks_by_ids(check_ids)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN, topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("BandTitle", parent=styles["Normal"], textColor=colors.white, fontSize=16, leading=19, fontName="Helvetica-Bold")
    sub_style = ParagraphStyle("BandSub", parent=styles["Normal"], textColor=colors.white, fontSize=9, leading=12, alignment=2)
    band = Table(
        [[Paragraph("Check Run Summary", title_style), Paragraph(datetime.now().strftime("Printed %B %d, %Y %I:%M %p"), sub_style)]],
        colWidths=[CONTENT_WIDTH * 0.5, CONTENT_WIDTH * 0.5],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story: list = [band, Spacer(1, 10)]

    if df.empty:
        story.append(Paragraph("No checks in this run.", styles["Normal"]))
    else:
        col_widths = [CONTENT_WIDTH * w for w in (0.28, 0.1, 0.17, 0.15, 0.12, 0.18)]
        data = [["Payee", "Track", "Reference", "Check Number", "Check Date", "Amount (PHP)"]]
        for _, r in df.iterrows():
            data.append([r["payee_name"], r["track"], r["reference"] or "", r["check_number"] or "Pending", r["check_date"], f"{r['amount']:,.2f}"])
        total = float(df["amount"].sum())
        data.append(["", "", "", "", "TOTAL", f"{total:,.2f}"])

        cmds = [
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1, ACCENT),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("ALIGN", (5, 0), (5, -1), "RIGHT"),
        ]
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle(cmds))
        story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
