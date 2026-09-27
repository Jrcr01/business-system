"""Thermal statement slip for an 80mm ESC/POS printer (2026-09-27, Thermal Statement Printing -- see CLAUDE.md, build step 1).

Hardware-independent: the slip is built as a list of lines (text + style), then
  * render_text()  -> the 48-column plain-text preview shown in the app, and
  * build_job()    -> the exact ESC/POS byte stream, rendered with python-escpos's Dummy printer (ESC @ first, Font A, native QR bottom
                      center, feed, partial cut). send_job() later pushes those bytes to a real backend in chunks (step 5).

Correctness rules (locked in the spec):
  * Totals are COMPUTED from the invoice / credit-memo / check lines printed on the slip -- never read from order_statements.total_amount.
  * A missing value prints as "—" (never "None"); a missing amount is never shown as 0.00 -- and if any counted line has no amount, the
    total prints "—" too (with a note) instead of a figure that silently left it out.
  * Currency is "PHP 1,234.56" (no peso glyph -- thermal code pages don't have it).
The content is the same as today's statement (owner, 2026-09-27): invoices, credit memos, checks, totals -- via the same data loader
(batch_statement_service._fetch_batch_data), so the slip and the A4 / thermal PDFs list the same rows.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from engines.batch_statement_service import _fetch_batch_data

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

COLUMNS = 48                       # Font A on 80mm paper
DASH = "—"                         # printed for any missing value (code page 1252 has it)
COMPANY = "SLICK'S TIRE SUPPLY"
COPY_LABELS = {"FILE": "FILE COPY", "SUPPLIER": "SUPPLIER COPY"}
QR_LINES_ESTIMATE = 22             # a native QR at size 6 plus its spacing, in text lines (for the Bluetooth guardrail)
FEED_BEFORE_CUT = 6              # cut(feed=True) feeds 6 lines (ESC d 6) so the last line clears the cutter

# Printer settings live in the `settings` table (category thermal_printer), never in code; these are only the defaults.
PRINTER_DEFAULTS = {
    "thermal_backend": "dummy",            # dummy (no hardware) | win32raw | serial | network
    "thermal_win32_printer": "",           # Windows printer name for Win32Raw (USB via the Xprinter driver)
    "thermal_serial_port": "",             # e.g. COM5 (Bluetooth virtual COM port)
    "thermal_network_host": "",            # printer IP for LAN
    "thermal_network_port": "9100",
    "thermal_bt_line_threshold": "120",    # Bluetooth guardrail: warn above this many lines (owner chose 120, 2026-09-27)
    "thermal_chunk_bytes": "1024",
    "thermal_qr_size": "6",
}


def get_printer_config() -> dict:
    """Printer settings from the settings table, falling back to PRINTER_DEFAULTS for anything not saved yet."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = dict(conn.execute(
            "SELECT key, value FROM settings WHERE key IN (%s)" % ",".join("?" * len(PRINTER_DEFAULTS)), tuple(PRINTER_DEFAULTS)).fetchall())
    cfg = {k: (rows.get(k) if rows.get(k) not in (None, "") else v) for k, v in PRINTER_DEFAULTS.items()}
    for k in ("thermal_network_port", "thermal_bt_line_threshold", "thermal_chunk_bytes", "thermal_qr_size"):
        try:
            cfg[k] = int(cfg[k])
        except (TypeError, ValueError):
            cfg[k] = int(PRINTER_DEFAULTS[k])
    return cfg


# ------------------------------------------------------------------ formatting (NULL-safe)
def fmt_text(v) -> str:
    """Any value as text; None / NaN / blank -> '—'. Never the word 'None'."""
    if v is None:
        return DASH
    s = str(v).strip()
    return DASH if s == "" or s.lower() in ("none", "nan", "nat") else s


def fmt_date(v) -> str:
    s = fmt_text(v)
    return s if s == DASH else s[:10]


def fmt_money(v, sign: str = "") -> str:
    """'PHP 1,234.56' ; a missing amount -> '—' (never 0.00)."""
    if v is None or fmt_text(v) == DASH:
        return DASH
    try:
        x = float(v)
        if x < 0:                                          # a negative amount reads "-PHP 1,500.00", like the "less" lines
            return f"{'' if sign == '-' else '-'}PHP {abs(x):,.2f}"
        return f"{sign}PHP {x:,.2f}"
    except (TypeError, ValueError):
        return DASH


C1, C2 = 14, 14                    # table columns: reference | date | amount (right-aligned, the rest of the 48)


def row3(first: str, second: str, amount: str) -> str:
    """One table row: reference (14), date (14), amount right-aligned in the remaining 20 columns."""
    return f"{first:<{C1}}{second:<{C2}}{amount:>{COLUMNS - C1 - C2}}"


def fmt_date_long(v) -> str:
    """'Jan 08, 2027' -- easier to read on paper than 2027-01-08; a missing date -> '—'."""
    s = fmt_date(v)
    if s == DASH:
        return DASH
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        return s


def _num(v):
    """float, or None when the amount is missing / not a number."""
    if v is None or fmt_text(v) == DASH:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def two_col(left: str, right: str, width: int = COLUMNS) -> list[str]:
    """Left text and right-aligned right text on one line; if they don't fit, the left wraps and the right goes on its own last line."""
    left, right = str(left), str(right)
    if len(left) + 1 + len(right) <= width:
        return [left + " " * (width - len(left) - len(right)) + right]
    return wrap(left, width) + [right.rjust(width)]


def wrap(text: str, width: int = COLUMNS, indent: str = "") -> list[str]:
    words, out, cur = str(text).split(), [], ""
    for w in words:
        while len(w) > width - len(indent):                  # a single word longer than the line: hard-break it
            if cur:
                out.append(cur)
                cur = ""
            out.append((indent if out else "") + w[: width - len(indent)])
            w = w[width - len(indent):]
        cand = (cur + " " + w) if cur else ((indent if out else "") + w)
        if len(cand) <= width:
            cur = cand
        else:
            out.append(cur)
            cur = indent + w
    if cur:
        out.append(cur)
    return out or [""]


# ------------------------------------------------------------------ the slip
@dataclass
class Line:
    text: str
    align: str = "left"            # left | center
    bold: bool = False
    big: bool = False              # double height (same 48-column width)


@dataclass
class Slip:
    lines: list
    totals: dict                   # the computed totals (gross, memos, net, checks, balance; None where not computable)
    copy: str
    statement_ref: str
    qr_link: str | None


def counted_invoices(data: dict) -> list:
    """Invoices that are on the slip and counted: every one except Cancelled."""
    return [i for i in data["invoices"] if (i.get("display_status") or i.get("status")) != "Cancelled"]


def counted_checks(data: dict) -> list:
    """Checks that are on the slip and counted: Active only (cancelled / replaced ones are in the A4 copy behind the QR)."""
    return [c for c in data["checks"] if c["status"] == "Active"]


def statement_totals(data: dict) -> dict:
    """Totals computed from the lines that are printed. Cancelled invoices and non-Active checks are listed but not counted.
    A counted line without an amount makes that total (and everything built on it) None -> printed as '—'."""
    counted_inv = counted_invoices(data)
    inv_amounts = [_num(i["amount"]) for i in counted_inv]
    memo_amounts = [_num(m["amount"]) for m in data["memos"]]
    active_checks = counted_checks(data)
    chk_amounts = [_num(c["amount"]) for c in active_checks]
    gross = None if None in inv_amounts else round(sum(inv_amounts), 2)
    memos = None if None in memo_amounts else round(sum(memo_amounts), 2)
    net = None if gross is None or memos is None else round(gross - memos, 2)
    checks = None if None in chk_amounts else round(sum(chk_amounts), 2)
    balance = None if net is None or checks is None else round(net - checks, 2)
    missing = sum(a is None for a in inv_amounts + memo_amounts + chk_amounts)
    return {"gross": gross, "memos": memos, "net": net, "checks": checks, "balance": balance, "invoices": len(counted_inv),
            "memo_count": len(memo_amounts), "active_checks": len(active_checks), "missing_amounts": missing}


def build_slip(statement_id: int, copy: str = "FILE", *, include_full_history: bool = True, include_unprinted_checks: bool = True,
               print_type: str = "ORIGINAL", version: int = 1, reprint_date: str | None = None, qr_link: str | None = None,
               printed_at: datetime | None = None) -> Slip | None:
    """One copy of the slip. print_type ORIGINAL / REPRINT (with reprint_date) / REVISED (version N supersedes N-1) -- the header lines for
    REPRINT / REVISED are here already; deciding which one applies is build step 3."""
    copy = copy.upper()
    if copy not in COPY_LABELS:
        raise ValueError(f"copy must be FILE or SUPPLIER, not {copy!r}")
    data = _fetch_batch_data(statement_id, include_full_history, include_unprinted_checks)
    if data is None:
        return None
    stmt = data["statement"]
    t = statement_totals(data)
    L: list[Line] = []
    add = L.append
    rule = lambda ch="-": add(Line(ch * COLUMNS))  # noqa: E731

    add(Line(COMPANY, "center", bold=True, big=True))
    add(Line("BATCH PAYMENT STATEMENT", "center", bold=True))
    add(Line(COPY_LABELS[copy], "center", bold=True, big=True))
    if print_type == "REPRINT":
        add(Line(f"REPRINT - {fmt_date_long(reprint_date or datetime.now().date().isoformat())}", "center", bold=True))
    elif print_type == "REVISED":
        add(Line(f"REVISED v{version} – supersedes v{version - 1}", "center", bold=True))
    rule("=")
    for label, value in (("Batch", stmt["statement_ref"]), ("Supplier", stmt["creditor_name"]), ("Status", stmt["status"]),
                         ("Version", str(version)), ("Printed", (printed_at or datetime.now()).strftime("%b %d, %Y %I:%M %p"))):
        for i, ln in enumerate(wrap(fmt_text(value), COLUMNS - 10)):
            add(Line((f"{label + ':':<10}" if i == 0 else " " * 10) + ln))

    # Summary slip (owner, 2026-09-27: "make it more readable and not confusing" ... "if detailed information is needed then that is
    # the use of QR code"): small tables with column headings, dates written out (Jan 08, 2027), one line per item, no status lines or
    # memo reasons, and only what is counted -- cancelled invoices and cancelled / replaced checks are in the A4 copy behind the QR --
    # so every amount on the slip adds up to the summary.
    def table(title, headers, rows):
        add(Line(""))
        add(Line(title, bold=True))
        add(Line(row3(*headers), bold=True))
        rule()
        if not rows:
            add(Line("  (none)"))
        for first, second, amount in rows:
            if len(first) > C1:                                   # a long reference gets its own line
                add(Line(first))
                first = ""
            add(Line(row3(first, second, amount)))

    invoices, checks = counted_invoices(data), counted_checks(data)
    table(f"INVOICES ({len(invoices)})", ("Invoice #", "Received", "Amount"),
          [(fmt_text(i["doc_ref"]), fmt_date_long(i["invoice_date"]), fmt_money(i["amount"])) for i in invoices])   # date received, not due date (owner)
    if data["memos"]:
        table(f"CREDIT MEMOS ({len(data['memos'])})", ("Memo #", "Date", "Amount"),
              [(fmt_text(m["memo_ref"]), fmt_date_long(m["memo_date"]), fmt_money(m["amount"], sign="-")) for m in data["memos"]])
    table(f"CHECKS ({len(checks)})", ("Check #", "Check date", "Amount"),
          [(fmt_text(c["check_number"]) if fmt_text(c["check_number"]) != DASH else "Not numbered", fmt_date_long(c["check_date"]),
            fmt_money(c["amount"])) for c in checks])

    add(Line(""))
    add(Line("SUMMARY", bold=True))
    rule()
    for label, value, bold in ((f"Invoices ({t['invoices']})", fmt_money(t["gross"]), False),
                               ("Less credit memos" + ("" if t["memo_count"] else " (none)"), fmt_money(t["memos"], sign="-" if t["memos"] else ""), False),
                               ("NET AMOUNT DUE", fmt_money(t["net"]), True),
                               (f"Less checks issued ({t['active_checks']})", fmt_money(t["checks"], sign="-" if t["checks"] else ""), False),
                               ("REMAINING BALANCE", fmt_money(t["balance"]), True)):
        for ln in two_col(label, value):
            add(Line(ln, bold=bold))
    if t["missing_amounts"]:
        for ln in wrap(f"Note: {t['missing_amounts']} line(s) have no amount on file, so the affected totals show {DASH}.", COLUMNS):
            add(Line(ln))
    rule("=")

    if copy == "FILE":
        add(Line(""))
        add(Line("Received by: " + "_" * (COLUMNS - 13)))
        add(Line(""))
        add(Line("Date: " + "_" * 20))
        add(Line(""))
    add(Line("Scan for the full statement:", "center"))
    if not qr_link:
        add(Line("[QR code to the A4 copy goes here]", "center"))
    add(Line("Internal summary - not an official receipt.", "center"))
    add(Line("*** Nothing follows ***", "center"))
    return Slip(lines=L, totals=t, copy=copy, statement_ref=str(stmt["statement_ref"]), qr_link=qr_link)


def render_text(slip: Slip) -> str:
    """The 48-column plain-text preview (what the paper will show, without bold / size)."""
    out = []
    for ln in slip.lines:
        out.append(ln.text.center(COLUMNS).rstrip() if ln.align == "center" else ln.text)
    text = "\n".join(out)
    if slip.qr_link:                                       # the QR prints just before the closing lines
        head, tail = "\n".join(out[:-2]), "\n".join(out[-2:])
        text = head + "\n" + "[ QR ]".center(COLUMNS).rstrip() + "\n" + tail          # the paper shows only the QR code, not the link text
    return text


def estimate_lines(slip: Slip) -> int:
    """Rendered length in text lines (for the Bluetooth guardrail): the lines, the QR, and the feed before the cut."""
    return len(slip.lines) + sum(1 for ln in slip.lines if ln.big) + (QR_LINES_ESTIMATE if slip.qr_link else 0) + FEED_BEFORE_CUT


def build_job(slips: list[Slip], qr_size: int = 6) -> bytes:
    """The ESC/POS byte stream for one or more copies, rendered with python-escpos's Dummy printer: ESC @ (initialize), Font A, each
    line with its alignment / bold / height, the native QR bottom center, feed, partial cut -- per copy."""
    from escpos.printer import Dummy

    p = Dummy()
    for slip in slips:
        p.hw("INIT")                                           # ESC @
        p.set(font="a", align="left", bold=False, normal_textsize=True)
        qr_at = len(slip.lines) - 2                            # before "Internal summary" / "Nothing follows"
        for i, ln in enumerate(slip.lines):
            if i == qr_at and slip.qr_link:
                p.set(align="center", bold=False, normal_textsize=True)
                p.qr(slip.qr_link, native=True, size=qr_size)
            if ln.big:
                p.set(align=ln.align, bold=ln.bold, double_height=True)
            else:
                p.set(align=ln.align, bold=ln.bold, normal_textsize=True)
            p.text(ln.text + "\n")
        p.set(align="left", bold=False, normal_textsize=True)
        p.cut(mode="PART", feed=True)                          # feed before cut: python-escpos sends ESC d 6, then GS V (partial cut)
    return p.output


def send_job(job: bytes, printer, chunk_bytes: int = 1024) -> None:
    """Push a prepared job to a printer backend in chunks (step 5 uses this with Win32Raw / Serial / Network; any write error propagates
    so the caller can mark the print FAILED)."""
    for i in range(0, len(job), max(1, int(chunk_bytes))):
        printer._raw(job[i:i + chunk_bytes])
