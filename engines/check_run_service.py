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
from engines.pdf_tail import keep_tail
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from pypdf import PdfWriter, PdfReader
except ImportError:
    PdfWriter = PdfReader = None

from engines.permissions import require as require_permission
from engines.check_engine import generate_metrobank_check_pdf

import os
from engines.common import get_conn
from engines.clock import manila_now
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

ACCENT = colors.HexColor("#1f3a5f")
LEFT_MARGIN = RIGHT_MARGIN = 0.55 * inch
TOP_MARGIN = 0.5 * inch
BOTTOM_MARGIN = 0.65 * inch
CONTENT_WIDTH = LETTER[0] - LEFT_MARGIN - RIGHT_MARGIN

_CHECK_ROW_SELECT = """
    SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name AS payee_name,
           c.statement_id, c.loan_note_id, c.print_batch_id, c.export_placeholder,
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
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            _CHECK_ROW_SELECT + " WHERE c.status = 'Active' AND c.printed_at IS NULL ORDER BY c.check_date ASC, c.check_id ASC",
            conn,
        )




def get_unnumbered_checks() -> pd.DataFrame:
    """Every Active check that doesn't have a check_number yet, across every batch, Supplier and
    Lender alike -- regardless of printed_at (Phase 6 Redux: printing and number-assignment are
    independent actions, so a check can be printed-but-unnumbered or numbered-but-unprinted)."""
    with get_conn(DB_PATH) as conn:
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
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            _CHECK_ROW_SELECT + f" WHERE c.check_id IN ({placeholders}) ORDER BY c.check_date ASC, c.check_id ASC",
            conn, params=check_ids,
        )


def mark_checks_printed(check_ids: list[int]) -> None:
    """Stamps printed_at for the given checks -- only where it's still NULL, so re-invoking on a
    reprint doesn't overwrite the original first-printed timestamp. Independent of check_number
    (Phase 6 Redux: printing and number-assignment are separate actions) -- this never touches
    check_number/booklet_id."""
    _ok, _msg = require_permission('check.issue')
    if not _ok:
        raise PermissionError(_msg)
    if not check_ids:
        return
    with get_conn(DB_PATH) as conn:
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
    _ok, _msg = require_permission('check.issue')
    if not _ok:
        raise PermissionError(_msg)
    if not check_ids:
        return None
    with get_conn(DB_PATH) as conn:
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
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            "SELECT print_batch_id, datetime(exported_at, '+8 hours') AS exported_at, check_count, total_amount, "
            "datetime(printed_confirmed_at, '+8 hours') AS printed_confirmed_at FROM check_print_batches ORDER BY print_batch_id DESC",
            conn,
        )


def mark_batch_printed(print_batch_id: int, printed: bool = True) -> tuple[bool, str]:
    """Records that an export run has (or has not) actually been printed in Chrysanth -- Chrysanth itself never remembers, so this
    is the app's memory of it (2026-09-27). Logged in the audit trail."""
    _ok, _msg = require_permission('check.issue')
    if not _ok:
        return False, _msg
    with get_conn(DB_PATH) as conn:
        if not conn.execute("SELECT 1 FROM check_print_batches WHERE print_batch_id = ?", (int(print_batch_id),)).fetchone():
            return False, "Run not found."
        conn.execute(
            "UPDATE check_print_batches SET printed_confirmed_at = " + ("CURRENT_TIMESTAMP" if printed else "NULL") + " WHERE print_batch_id = ?",
            (int(print_batch_id),),
        )
        conn.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'check_print_batches', ?, ?)",
            ("BATCH_MARKED_PRINTED" if printed else "BATCH_UNMARKED_PRINTED", int(print_batch_id), "marked as printed in Chrysanth" if printed else "printed mark removed"),
        )
        conn.commit()
    tag = print_batch_tag(print_batch_id)
    return True, f"{tag} marked as printed." if printed else f"{tag} is no longer marked as printed."


def get_unprinted_batches() -> pd.DataFrame:
    """Runs that were exported but are not marked printed yet, oldest first, with their run number tag."""
    df = get_check_print_batches()
    df = df[df["printed_confirmed_at"].isna() | (df["printed_confirmed_at"] == "")].sort_values("print_batch_id").reset_index(drop=True)
    df["tag"] = [print_batch_tag(int(b)) for b in df["print_batch_id"]]
    return df


def get_check_print_batch_detail(batch_id: int) -> pd.DataFrame:
    """Every check that went out in this specific export batch -- check number (as of now; a
    batch is logged at export time, before numbers are typically known, so this reflects
    whatever's been assigned to each check SINCE, not a snapshot frozen at export), check date,
    payee, which booklet its number came from (if assigned), and the OS/lender reference it
    belongs to."""
    with get_conn(DB_PATH) as conn:
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
    with get_conn(DB_PATH) as conn:
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
        [[Paragraph("Check Run Summary", title_style), Paragraph(manila_now().strftime("Printed %m/%d/%Y %I:%M %p"), sub_style)]],
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
        table = keep_tail(Table(data, colWidths=col_widths, repeatRows=1))
        table.setStyle(TableStyle(cmds))
        story.append(table)

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# ---------------------------------------------------------------------------------------------
# Chrysanth (paid version) Import Cheque Transaction: ChequeTransactionTemplate saved as Text (MS-DOS) (2026-09-26)
# ---------------------------------------------------------------------------------------------
CHRYSANTH_TXT_COLUMNS = ["AccountNo", "Payee", "Amount", "Date", "ChequeNo", "CrossingOption", "Remark", "Place"]
CHRYSANTH_CROSSING_OPTIONS = {
    0: "0 - Cross A/C Payee + Or Bearer",
    1: "1 - Cross A/C Payee + Not Negotiable + Or Bearer",
    2: "2 - Cross A/C Payee",
    3: "3 - Cross Only",
    4: "4 - No Crossing",
}
DEFAULT_CHRYSANTH_CROSSING = 2      # "A/C Payee Only", what every check was crossed as before this template


def get_chrysanth_settings() -> dict:
    """{'account_no': str, 'crossing': int}. The account number must be the one already added inside Chrysanth
    (its import refuses an unknown account), so it is a setting the user types once, not something guessed."""
    with get_conn(DB_PATH) as conn:
        rows = dict(conn.execute("SELECT key, value FROM settings WHERE key IN ('chrysanth_account_no', 'chrysanth_crossing_option')").fetchall())
    try:
        crossing = int(rows.get("chrysanth_crossing_option", DEFAULT_CHRYSANTH_CROSSING))
    except (TypeError, ValueError):
        crossing = DEFAULT_CHRYSANTH_CROSSING
    return {"account_no": (rows.get("chrysanth_account_no") or "").strip(), "crossing": crossing if crossing in CHRYSANTH_CROSSING_OPTIONS else DEFAULT_CHRYSANTH_CROSSING}


def save_chrysanth_settings(account_no: str, crossing: int) -> tuple[bool, str]:
    account_no = (account_no or "").strip()
    if not account_no:
        return False, "Type the account number exactly as it is set up in Chrysanth."
    if len(account_no) > 20:
        return False, "Chrysanth allows at most 20 characters for the account number."
    if int(crossing) not in CHRYSANTH_CROSSING_OPTIONS:
        return False, "Pick one of the crossing options."
    with get_conn(DB_PATH) as conn:
        for key, value, desc in (("chrysanth_account_no", account_no, "Account number as added in Chrysanth (AccountNo column of the batch-print import)"),
                                 ("chrysanth_crossing_option", str(int(crossing)), "Chrysanth CrossingOption code (0-4) used for every exported cheque")):
            conn.execute(
                """INSERT INTO settings (key, value, category, description) VALUES (?, ?, 'chrysanth', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                (key, value, desc),
            )
        conn.commit()
    return True, "Saved."


PLACEHOLDER_NUMBER_PREFIX = "9"


def placeholder_cheque_number(check_id: int, statement_ref: str | None = None, statement_id: int | None = None, loan_note_id: int | None = None) -> str:
    """A stand-in cheque number for a check that has no real number yet: '9' + a 5-digit ORDER STATEMENT key + the check id padded
    to 6 digits (12 digits, within Chrysanth's 18). Chrysanth prints in cheque-number order, so leading with the order statement number
    (2026 - OS 0012 -> 00012) makes a batch print grouped and ordered by order statement, and by check id within each one
    (2026-09-27, per user). Lender checks come after every order statement (90000 + loan note id); a batch still on a DRAFT- ref sits
    just before them (80000 + statement id). Unique per check because the id is; unmistakably not a real number (real ones start 399)."""
    import re
    m = re.search(r"(?:OS|PS)\s*(\d+)\s*$", statement_ref or "")      # OS = numbered before 2026-10-13, PS = after: one count
    if m:
        key = int(m.group(1))
    elif statement_id is not None:
        key = 80000 + int(statement_id)
    elif loan_note_id is not None:
        key = 90000 + int(loan_note_id)
    else:
        key = 99999
    return f"{PLACEHOLDER_NUMBER_PREFIX}{min(key, 99999):05d}{int(check_id):06d}"


def _legacy_placeholder(check_id: int) -> str:
    """The first scheme ('9' + the check id, 11 digits) -- what files exported before 2026-09-27 carry, kept for those checks so a
    re-download still matches what Chrysanth already holds."""
    return f"{PLACEHOLDER_NUMBER_PREFIX}{int(check_id):011d}"


def chrysanth_export_problems(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Returns (rows for the file, plain-language notes). Chrysanth's import needs a cheque number on every row, so a
    check with no number yet gets a placeholder one (see placeholder_cheque_number) and is noted; a real number must
    be digits only, at most 18, and the amount above zero, otherwise that check is left out and reported. A payee over
    120 characters is cut (noted, not blocked)."""
    notes, keep, placeholders = [], [], 0
    out = df.copy()
    out["_new_placeholder"] = False
    for idx, r in df.iterrows():
        who = f"{r['payee_name']} {float(r['amount']):,.2f} ({__import__('engines.common', fromlist=['us_date']).us_date(r['check_date'])})"
        number = "" if pd.isna(r["check_number"]) else str(r["check_number"]).strip()
        if number and (not number.isdigit() or len(number) > 18):
            notes.append(f"{who}: check number {number} must be digits only, at most 18 — left out.")
        elif float(r["amount"]) <= 0:
            notes.append(f"{who}: the amount must be above zero — left out.")
        else:
            keep.append(idx)
            if not number:
                stored = r.get("export_placeholder") if "export_placeholder" in df.columns else None
                if isinstance(stored, str) and stored:
                    out.at[idx, "check_number"] = stored                                   # same number Chrysanth already has
                elif "print_batch_id" in df.columns and pd.notna(r.get("print_batch_id")):
                    out.at[idx, "check_number"] = _legacy_placeholder(int(r["check_id"]))  # exported before the order-statement scheme
                else:
                    out.at[idx, "check_number"] = placeholder_cheque_number(
                        int(r["check_id"]), r.get("reference"),
                        None if pd.isna(r.get("statement_id")) else int(r["statement_id"]),
                        None if pd.isna(r.get("loan_note_id")) else int(r["loan_note_id"]))
                    out.at[idx, "_new_placeholder"] = True
                placeholders += 1
            if len(str(r["payee_name"])) > 120:
                notes.append(f"{who}: the payee name is longer than 120 characters and was cut short in the file.")
    if placeholders:
        notes.insert(0, f"{placeholders} check(s) had no number yet, so the file carries a temporary placeholder cheque number (starting with 9) for them. "
                        "Nothing was assigned in this system — assign the real numbers under Assign a check number once they are printed.")
    return out.loc[keep], notes


def _txt_field(value) -> str:
    """One field the way Excel writes it to a tab-delimited text file: tabs/line breaks flattened, and a field with a
    double quote gets wrapped in quotes with the quotes doubled."""
    text = str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    return '"' + text.replace('"', '""') + '"' if '"' in text else text


def _txt_amount(amount: float) -> str:
    """Amount the way Excel's General format writes it: no trailing zeros (10000, not 10000.00; 12345.6, not 12345.60)."""
    text = f"{round(float(amount), 2):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def print_batch_tag(print_batch_id: int) -> str:
    """The run number put at the start of every Remark in that export's file, e.g. 'RUN 004' (= the batch's position in the export history,
    zero-padded so 'RUN 004' can never be mistaken for 'RUN 0041' when searching). Chrysanth does not remember what was already printed,
    but its Cheque Transaction History can be searched / filtered by Remark -- so the run number is how one batch is picked out from the
    rest (2026-09-27)."""
    with get_conn(DB_PATH) as conn:
        n = conn.execute("SELECT COUNT(*) FROM check_print_batches WHERE print_batch_id <= ?", (int(print_batch_id),)).fetchone()[0]
    return f"RUN {int(n):03d}"


def _chrysanth_txt(df: pd.DataFrame, account_no: str, crossing: int, batch_tag: str | None = None) -> bytes:
    """The file Chrysanth's Import Cheque Transaction accepts: ChequeTransactionTemplate saved from Excel as
    "Text (MS-DOS) (.txt)" -- plain single-byte text (no byte-order mark, NOT UTF-16), tab-separated, CRLF line ends,
    one header row, every row carrying all 8 columns (so an empty Remark/Place ends in tabs). Checked byte for byte
    against a template file that Chrysanth imported successfully. Amount has no trailing zeros, Date is yyyy-mm-dd,
    Remark is cut to 100 characters and Place is left empty ("only in certain editions")."""
    lines = ["\t".join(CHRYSANTH_TXT_COLUMNS)]
    for _, r in df.iterrows():
        lines.append("\t".join([
            _txt_field(account_no),
            _txt_field(str(r["payee_name"])[:120]),
            _txt_amount(r["amount"]),
            pd.to_datetime(r["check_date"]).strftime("%Y-%m-%d"),
            _txt_field(str(r["check_number"]).strip()),
            str(int(crossing)),
            # With a run number the Remark is JUST that (2026-09-27, per user): Chrysanth's Remark column filter then holds one tick-box per run,
            # and choosing that single value prints exactly that run. (Choosing several values there printed everything.)
            _txt_field(batch_tag if batch_tag else ("" if pd.isna(r["reference"]) else str(r["reference"]))[:100]),
            "",
        ]))
    return ("\r\n".join(lines) + "\r\n").encode("cp437", errors="replace")


def generate_pending_check_run_txt(check_ids: list[int] | None = None, log_batch: bool = False) -> tuple[bytes, list[str], list[int]]:
    """Chrysanth batch-print file for the pending checks (or the ticked subset). Returns (file bytes, problems,
    the check_ids that actually went into the file) -- unnumbered / invalid checks are left out and reported.
    With log_batch=True the export is logged as a new batch (checks stamped printed / tagged with it) and every Remark in the file
    is the batch's run number (RUN 004), so that batch can be searched out inside Chrysanth."""
    df = get_pending_check_run()
    if check_ids is not None:
        df = df[df["check_id"].isin(check_ids)]
    good, problems = chrysanth_export_problems(df)
    s = get_chrysanth_settings()
    ids = [int(x) for x in good["check_id"]]
    tag = None
    if log_batch and ids:
        tag = print_batch_tag(create_check_print_batch(ids))
        fresh = good[good["_new_placeholder"]]
        if not fresh.empty:       # remember the stand-in number each check carried, so re-downloads match what Chrysanth holds
            with get_conn(DB_PATH) as conn:
                conn.executemany("UPDATE checks_issued SET export_placeholder = ? WHERE check_id = ?",
                                 [(str(r["check_number"]), int(r["check_id"])) for _, r in fresh.iterrows()])
                conn.commit()
    return _chrysanth_txt(good, s["account_no"], s["crossing"], tag), problems, ids


def chrysanth_file_name(refs: list[str], tag: str) -> str:
    """Name for a Chrysanth file that says which order statements it holds (2026-09-27, per user), e.g.
    'ChequeTransaction_RUN004_OS0006-OS0012.txt'. One order statement -> 'OS0006'; a few separate ones are listed
    ('OS0006+OS0008+OS0011'); more than that show the range. Lender checks add '_Lenders', a batch still on a DRAFT ref '_Draft'."""
    import re
    seqs, lenders, drafts = set(), False, False
    for ref in refs:
        ref = str(ref or "")
        m = re.search(r"OS\s*(\d+)\s*$", ref)
        if m:
            seqs.add(int(m.group(1)))
        elif ref.upper().startswith("DRAFT"):
            drafts = True
        elif ref:
            lenders = True
    ordered = sorted(seqs)
    if not ordered:
        part = ""
    elif len(ordered) == 1:
        part = f"OS{ordered[0]:04d}"
    elif len(ordered) <= 3 and ordered[-1] - ordered[0] + 1 != len(ordered):
        part = "+".join(f"OS{n:04d}" for n in ordered)
    else:
        part = f"OS{ordered[0]:04d}-OS{ordered[-1]:04d}"
    pieces = ["ChequeTransaction", tag.replace(" ", "")] + ([part] if part else []) + (["Lenders"] if lenders else []) + (["Draft"] if drafts else [])
    return "_".join(pieces) + ".txt"


def _refs_for_checks(check_ids: list[int]) -> list[str]:
    if not check_ids:
        return []
    with get_conn(DB_PATH) as conn:
        rows = conn.execute(
            f"""SELECT DISTINCT COALESCE(os.statement_ref, ln.note_ref) FROM checks_issued c
                LEFT JOIN order_statements os ON os.statement_id = c.statement_id
                LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
                WHERE c.check_id IN ({','.join('?' * len(check_ids))})""",
            [int(x) for x in check_ids],
        ).fetchall()
    return [r[0] for r in rows]


def file_name_for_checks(check_ids: list[int], print_batch_id: int) -> str:
    return chrysanth_file_name(_refs_for_checks(check_ids), print_batch_tag(print_batch_id))


def file_name_for_batch(print_batch_id: int) -> str:
    with get_conn(DB_PATH) as conn:
        ids = [r[0] for r in conn.execute("SELECT check_id FROM checks_issued WHERE print_batch_id = ?", (int(print_batch_id),)).fetchall()]
    return file_name_for_checks(ids, print_batch_id)


def generate_batch_txt(print_batch_id: int) -> tuple[bytes, list[str]]:
    """The same file for a batch that was already exported (lost or never downloaded), from the batch history."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(_CHECK_ROW_SELECT + " WHERE c.print_batch_id = ? ORDER BY c.check_date ASC, c.check_id ASC", conn, params=(int(print_batch_id),))
    good, problems = chrysanth_export_problems(df)
    s = get_chrysanth_settings()
    return _chrysanth_txt(good, s["account_no"], s["crossing"], print_batch_tag(print_batch_id)), problems
