"""Bank statement import and automatic check matching (2026-09-27, owner: "I do not have the time to reconcile which checks are cleared").

The owner downloads the statement from Metrobank MBOS and drops the file in the app; everything else is automatic:
  * parse_statement()  -- reads the MBOS "Statement of Account" PDF (one table per page, 11 columns, headers printed sideways) and, if MBOS
                          offers them, CSV / Excel exports with the same columns. Amounts that wrap onto two lines in the PDF
                          ("1000000.0 0") are joined back; every running balance is re-checked, so a misread line is caught.
  * classify()         -- what each line is: CHECK (a check of ours clearing), DEPOSIT, RETURNED_CHECK (a deposited customer check that
                          bounced), BILLS_PAYMENT, DEBIT_MEMO (bank debit, e.g. loan ADA / charges), OTHER.
  * import_statement() -- stores the lines once (a line key makes overlapping statement periods and re-imported files harmless) and
                          matches them.
  * match_checks()     -- a CHECK line whose number (7- and 10-digit forms are the same leaf, as in the no-duplicate rule) and amount
                          match one of our checks marks it bank-confirmed cleared, with the bank's date. Everything else is an exception
                          the owner only has to look at: amount differs, a cancelled / replaced check cleared anyway, a returned
                          customer check, a check the system does not know.

Show-both rule (owner, 2026-09-27): the rest of the app keeps treating a check as cleared once its check date has passed; the bank-confirmed date is shown
next to it and drives the reconciliation lists. Switching the totals over is a later decision.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime

import db_location
from engines.common import get_conn, write_tx
from engines.clock import manila_now
from engines.common import us_date

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# Header words after un-reversing the MBOS sideways headers (or in a CSV / Excel export). Keys are the fields the app uses.
_HEADER_SYNONYMS = {
    "txn_date": ["transaction date", "trans date", "date"],
    "posting_date": ["posting date", "post date", "value date"],
    "txn_time": ["time"],
    "check_number": ["check number", "cheque number", "check no", "check no.", "chk no"],
    "description": ["transaction description", "description", "particulars", "details"],
    "debit": ["debit amount", "debit", "withdrawal", "withdrawals"],
    "credit": ["credit amount", "credit", "deposit", "deposits"],
    "balance": ["balance", "running balance", "available balance"],
    "channel": ["branch/channel", "branch channel", "channel", "branch"],
    "subscriber": ["subscriber number/tin", "subscriber number tin", "subscriber number/tin number", "subscriber number", "tin"],
    "sequence": ["sequence number", "sequence no", "reference number", "reference"],
}
MBOS_COLUMNS = ["txn_date", "posting_date", "txn_time", "check_number", "description", "debit", "credit", "balance", "channel", "subscriber", "sequence"]
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


@dataclass
class StatementLine:
    txn_date: date
    posting_date: date
    txn_time: str
    check_number: str | None
    description: str
    debit: float
    credit: float
    balance: float | None
    channel: str = ""
    sequence: str = ""
    kind: str = "OTHER"


@dataclass
class ParsedStatement:
    lines: list = field(default_factory=list)
    account_number: str = ""
    period_from: date | None = None
    period_to: date | None = None
    opening_balance: float | None = None
    closing_balance: float | None = None
    balance_ok: bool = True
    bad_balance_steps: int = 0
    messages: list = field(default_factory=list)
    raw_rows: int = 0            # transaction rows seen in the file (a date in the first column), read or not -- for the capture check

    def masked_account(self) -> str:
        return ("•" * max(0, len(self.account_number) - 4) + self.account_number[-4:]) if self.account_number else ""


# ------------------------------------------------------------------ small parsers
def clean_amount(v) -> float:
    """'1,234.56' / '1000000.0 0' (wrapped in the PDF) / '' -> float. Blank = 0 (MBOS prints 0.00 in the unused debit/credit column)."""
    s = re.sub(r"[,\s₱]|PHP", "", str(v or ""), flags=re.I)
    if s in ("", "-"):
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s)


def parse_date(v) -> date | None:
    """MBOS dates are MM/DD/YYYY (confirmed by 'Period Covered ... From 01/01/2026 To 02/28/2026')."""
    s = str(v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y"):   # reading the bank's file: keep "Oct 09, 2026"
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm_check_number(n) -> str:
    """The same leaf whether written 2554301 or 3992554301 (as in the no-duplicate rule, migration 047). All zeros = no check."""
    s = re.sub(r"\D", "", str(n or ""))
    from engines.bank_accounts import prefixes
    for prefix, digits in prefixes():                       # the accounts' prefixes (Settings > Bank accounts)
        if len(s) == len(prefix) + digits and s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.lstrip("0")


def _normalize_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h or "").replace("_", " ").replace("|", " ")).strip().lower()


def _words(h: str) -> frozenset:
    return frozenset(_normalize_header(h).split())


def _is_known(h: str) -> bool:
    return any(_words(h) == _words(s) for syns in _HEADER_SYNONYMS.values() for s in syns)


def _unreverse(cell: str) -> str:
    """MBOS prints its header row sideways; pdfplumber reads each word backwards ('noitcasnarT' = 'Transaction') and sometimes the word
    order too ('tnuomA tibeD' = 'Amount Debit'). Reverse word by word when that gives a known header (word order is ignored)."""
    raw = " ".join(p.strip() for p in str(cell or "").replace("|", "\n").split("\n") if p.strip())
    rev = " ".join(w[::-1] for w in raw.split(" "))
    for cand in (rev, raw):
        if _is_known(cand):
            return cand
    return raw


def _map_columns(headers: list[str]) -> dict[str, int]:
    """field -> column index; a header matches a synonym when it has the same words, in any order."""
    words = [_words(h) for h in headers]
    out = {}
    for fieldname, syns in _HEADER_SYNONYMS.items():
        for syn in syns:
            i = next((i for i, w in enumerate(words) if i not in out.values() and w == _words(syn)), None)
            if i is not None:
                out[fieldname] = i
                break
    return out


def classify(description: str, debit: float, credit: float, check_number: str | None) -> str:
    """Metrobank MBOS descriptions seen in the owner's statement (Jan-Feb 2026): IM IN- CLRNG- BATCH / MO CHK IN-CLRNG / MO ENCSHMNT (our
    checks clearing), MO CSH DEP / MO LOC CHK DEP / ET CR IBFT / WA CR / MO CM (money in), DE DM RET CHK (returned deposited check),
    MO DM PAY UTLTY (bills payment), MO DM (debit memo)."""
    d = re.sub(r"\s+", " ", description or "").upper()
    if "RET CHK" in d or "RETURN" in d:
        return "RETURNED_CHECK"
    if debit > 0 and check_number:
        return "CHECK"
    if credit > 0:
        return "DEPOSIT"
    if "PAY UTLTY" in d or "BILLS" in d:
        return "BILLS_PAYMENT"
    if re.search(r"\bDM\b", d):
        return "DEBIT_MEMO"
    return "OTHER"


def _line_from(values: dict) -> StatementLine | None:
    td = parse_date(values.get("txn_date"))
    if td is None:
        return None
    pdt = parse_date(values.get("posting_date")) or td
    num = norm_check_number(values.get("check_number"))
    raw_num = re.sub(r"\D", "", str(values.get("check_number") or ""))
    debit, credit = clean_amount(values.get("debit")), clean_amount(values.get("credit"))
    bal_raw = values.get("balance")
    balance = clean_amount(bal_raw) if str(bal_raw or "").strip() else None
    desc = re.sub(r"\s+", " ", str(values.get("description") or "")).strip()
    line = StatementLine(txn_date=td, posting_date=pdt, txn_time=str(values.get("txn_time") or "").strip(),
                         check_number=raw_num if num else None, description=desc, debit=debit, credit=credit, balance=balance,
                         channel=re.sub(r"\s+", " ", str(values.get("channel") or "")).strip(), sequence=str(values.get("sequence") or "").strip())
    line.kind = classify(desc, debit, credit, line.check_number)
    return line


def _finish(parsed: ParsedStatement) -> ParsedStatement:
    """Opening / closing balance and the running-balance check (each line's balance = previous balance - debit + credit)."""
    L = parsed.lines
    if L and L[0].balance is not None:
        parsed.opening_balance = round(L[0].balance + L[0].debit - L[0].credit, 2)
        parsed.closing_balance = L[-1].balance
        bad = sum(1 for a, b in zip(L, L[1:]) if a.balance is not None and b.balance is not None
                  and abs(a.balance - b.debit + b.credit - b.balance) > 0.005)
        parsed.bad_balance_steps, parsed.balance_ok = bad, bad == 0
        if bad:
            parsed.messages.append(f"{bad} line(s) do not add up with the running balance -- the file may have been misread; check before importing.")
    if L:
        parsed.period_from = parsed.period_from or min(x.posting_date for x in L)
        parsed.period_to = parsed.period_to or max(x.posting_date for x in L)
    return parsed


def _parse_pdf(data: bytes) -> ParsedStatement:
    import pdfplumber

    parsed = ParsedStatement()
    colmap = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first_text = pdf.pages[0].extract_text() or "" if pdf.pages else ""
        m = re.search(r"Account Number:\s*([\d\- ]+)", first_text)
        if m:
            parsed.account_number = re.sub(r"\D", "", m.group(1))
        m = re.search(r"From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})", first_text)
        if m:
            parsed.period_from, parsed.period_to = parse_date(m.group(1)), parse_date(m.group(2))
        if not first_text.strip():
            parsed.messages.append("This PDF has no text layer (a scan?). Download the statement again from MBOS as PDF, CSV or Excel.")
            return parsed
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    cells = [(c or "") for c in row]
                    if colmap is None and not DATE_RE.match(cells[0].strip()):
                        cand = _map_columns([_unreverse(c) for c in cells])
                        if {"txn_date", "debit", "credit"} <= cand.keys():
                            colmap = cand
                        continue
                    if not DATE_RE.match(cells[0].strip()):
                        continue
                    parsed.raw_rows += 1
                    standard = {f: i for i, f in enumerate(MBOS_COLUMNS)} if len(cells) == len(MBOS_COLUMNS) else {}
                    cm = ({**standard, **colmap} if colmap else standard) or None      # headings found win; the rest by MBOS position
                    if cm is None:
                        parsed.messages.append(f"Skipped a line with {len(cells)} columns (unknown layout): {' / '.join(c.strip() for c in cells)[:80]}")
                        continue
                    values = {f: cells[i].replace("\n", " ") for f, i in cm.items() if i < len(cells)}
                    try:
                        line = _line_from(values)
                    except ValueError:
                        parsed.messages.append(f"Could not read the amounts on a {cells[0]} line: {' / '.join(c.strip() for c in cells)[:80]}")
                        continue
                    if line:
                        parsed.lines.append(line)
    if colmap is None and parsed.lines:
        parsed.messages.append("Column headings not recognised -- read the columns in the standard MBOS order.")
    return parsed


def _parse_sheet(data: bytes, filename: str) -> ParsedStatement:
    import pandas as pd

    parsed = ParsedStatement()
    # MBOS CSV / Excel exports (checked 2026-09-28) put 9-11 title lines (bank name, Account Number:, Period Covered: ...) above the
    # column headings, and the Excel file has empty spacer columns -- so read without a header and look for the headings row.
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, header=None, names=range(64), engine="python")
    else:
        df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False, header=None)
    rows = [[str(c).strip() for c in r] for r in df.values.tolist()]
    head_at, cm = None, {}
    for i, r in enumerate(rows[:60]):
        cand = _map_columns(r)
        if {"txn_date", "debit", "credit"} <= cand.keys():
            head_at, cm = i, cand
            break
        if r and _normalize_header(next((c for c in r if c), "")) == "account number:":
            parsed.account_number = re.sub(r"\D", "", next((c for c in r[1:] if re.search(r"\d", c)), ""))
    if head_at is None:
        parsed.messages.append("Could not find the Date / Debit / Credit column headings in this file.")
        return parsed
    for r in rows[head_at + 1:]:
        if parse_date(r[cm["txn_date"]] if cm["txn_date"] < len(r) else "") is not None:
            parsed.raw_rows += 1
        try:
            line = _line_from({f: r[i] for f, i in cm.items() if i < len(r)})
        except ValueError:
            parsed.messages.append(f"Could not read the amounts on one line: {' / '.join(c for c in r if c)[:80]}")
            continue
        if line:
            parsed.lines.append(line)
    return parsed


def parse_statement(data: bytes, filename: str) -> ParsedStatement:
    """Read a bank statement file. Never raises: problems are in .messages."""
    try:
        parsed = _parse_pdf(data) if filename.lower().endswith(".pdf") else _parse_sheet(data, filename)
    except Exception as e:                                             # unreadable / corrupt file
        parsed = ParsedStatement(messages=[f"The file could not be read ({e})."])
    if not parsed.lines and not parsed.messages:
        parsed.messages.append("No transaction lines were found in this file.")
    return _finish(parsed)


# ------------------------------------------------------------------ capture check
def capture_check(parsed: ParsedStatement, known_last4: str | None = None) -> list[dict]:
    """Owner, 2026-09-28: "Always remember bank statement is ultimate truth and have a pass test to ensure system captures the bank statement
    well." Proves the file was read completely and exactly; ANY failed item blocks the import (owner's choice). MBOS prints no totals of its
    own, so the proof is the lines themselves: every transaction row read, every running balance adds up (a missing or misread line breaks
    the chain), and opening + money in - money out = closing. Returns [{'name', 'ok', 'detail'}]."""
    L = parsed.lines
    money_in, money_out = round(sum(x.credit for x in L), 2), round(sum(x.debit for x in L), 2)
    unreadable = [m for m in parsed.messages if m.startswith(("Could not read", "Skipped a line"))]
    keys = [line_key(0, x) for x in L]
    no_balance = sum(1 for x in L if x.balance is None)
    out = [
        {"name": "Every transaction line read", "ok": bool(L) and len(L) == parsed.raw_rows and not unreadable,
         "detail": f"{len(L):,} of {parsed.raw_rows:,} line(s) read" + (f"; {len(unreadable)} could not be read" if unreadable else "")},
        {"name": "Every line has a balance", "ok": bool(L) and no_balance == 0,
         "detail": "all lines" if not no_balance else f"{no_balance} line(s) without a balance"},
        {"name": "Running balance adds up on every line", "ok": bool(L) and parsed.balance_ok and no_balance == 0,
         "detail": "every line = the one before + money in - money out" if parsed.balance_ok else f"{parsed.bad_balance_steps} line(s) do not add up"},
    ]
    if L and parsed.opening_balance is not None and parsed.closing_balance is not None:
        diff = round(parsed.opening_balance + money_in - money_out - parsed.closing_balance, 2)
        out.append({"name": "Opening + money in - money out = closing", "ok": abs(diff) < 0.005,
                    "detail": f"{parsed.opening_balance:,.2f} + {money_in:,.2f} - {money_out:,.2f} = {parsed.closing_balance:,.2f}"
                              + ("" if abs(diff) < 0.005 else f" (off by {diff:,.2f})")})
    else:
        out.append({"name": "Opening + money in - money out = closing", "ok": False, "detail": "opening or closing balance missing"})
    out.append({"name": "No line would be lost as a duplicate", "ok": len(set(keys)) == len(keys),
                "detail": "every line is distinct" if len(set(keys)) == len(keys) else f"{len(keys) - len(set(keys))} line(s) are exact copies of another line"})
    last4 = parsed.account_number[-4:]
    out.append({"name": "Same bank account", "ok": not (known_last4 and last4 and last4 != known_last4),
                "detail": (f"account ending {last4}" if last4 else "account number not shown in the file")
                          + (f" -- earlier statements are for the account ending {known_last4}" if known_last4 and last4 and last4 != known_last4 else "")})
    return out


def known_account_last4(bank_account_id: int = 1) -> str | None:
    """The account the earlier imported statements were for (by their last 4 digits)."""
    with _conn() as conn:
        r = conn.execute("SELECT account_last4 FROM bank_statement_imports WHERE bank_account_id = ? AND account_last4 IS NOT NULL "
                         "ORDER BY import_id DESC LIMIT 1", (int(bank_account_id),)).fetchone()
    return r[0] if r else None


class CaptureCheckFailed(Exception):
    pass


# ------------------------------------------------------------------ storing and matching
def _conn() -> sqlite3.Connection:
    conn = get_conn(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def line_key(bank_account_id: int, x: StatementLine) -> str:
    """The same bank line always gets the same key, so overlapping statement periods / a re-imported file never double up."""
    raw = "|".join(map(str, (bank_account_id, x.posting_date, x.txn_date, x.txn_time, x.check_number, x.description, f"{x.debit:.2f}",
                             f"{x.credit:.2f}", "" if x.balance is None else f"{x.balance:.2f}", x.sequence)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stored_check(conn, bank_account_id: int, parsed: ParsedStatement) -> dict:
    """After saving: every line of the file is in the database (new or from an earlier overlapping statement) with the same amounts."""
    keys = [line_key(bank_account_id, x) for x in parsed.lines]
    n, out_, in_ = 0, 0.0, 0.0
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        a, b, c = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0) FROM bank_transactions WHERE line_key IN "
                               f"({','.join('?' * len(part))})", part).fetchone()
        n, out_, in_ = n + a, out_ + b, in_ + c
    want_out, want_in = round(sum(x.debit for x in parsed.lines), 2), round(sum(x.credit for x in parsed.lines), 2)
    ok = n == len(keys) and abs(round(out_, 2) - want_out) < 0.005 and abs(round(in_, 2) - want_in) < 0.005
    return {"name": "Saved lines match the file", "ok": ok,
            "detail": f"{n:,} of {len(keys):,} line(s) on file; money out {out_:,.2f} vs {want_out:,.2f}; money in {in_:,.2f} vs {want_in:,.2f}"}


def bank_dir() -> str:
    """Where imported statement files are kept (outside the project and git): a 'bank' folder beside the Drive login folder / live DB.
    Without a Drive set-up (no AP_DRIVE_DIR, no live-database pointer: the server), beside the database in use -- the home folder is
    read-only for the server's program (2026-10-09: "Permission denied: '/home/slicks'")."""
    if os.environ.get("AP_DRIVE_DIR") or os.path.exists(db_location.POINTER):
        return os.path.join(os.path.dirname(db_location.drive_dir()), "bank")
    import engines.db_engine as dbe
    return os.path.join(os.path.dirname(os.path.abspath(dbe.DB_PATH)), "bank")


KEEP_STATEMENT_YEARS = 5          # owner, 2026-10-09: the original statement files are kept 5 years (every line stays in the database for good)


def prune_statement_files(today: date | None = None, years: int = KEEP_STATEMENT_YEARS, folder: str | None = None) -> list[str]:
    """Deletes kept statement files imported more than `years` ago (by the date the import put in front of the name, else the file's date), and writes
    one audit line per file. Run by the server's nightly job. Returns the names removed."""
    import calendar
    today = today or manila_now().date()
    y = today.year - years
    cutoff = date(y, today.month, min(today.day, calendar.monthrange(y, today.month)[1]))
    folder = folder or bank_dir()
    if not os.path.isdir(folder):
        return []
    gone = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        try:
            on = datetime.strptime(name[:8], "%Y%m%d").date()
        except ValueError:
            on = datetime.fromtimestamp(os.path.getmtime(path)).date()
        if on < cutoff:
            os.remove(path)
            gone.append(name)
    if gone:
        with write_tx(DB_PATH) as conn:
            for name in gone:
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES ('system', 'BANK_FILE_EXPIRED', "
                             "'bank_statement_files', NULL, ?)", (f"{name}: original statement file older than {years} years removed (its lines stay)",))
    return gone


def import_statement(data: bytes, filename: str, bank_account_id: int = 1, keep_copy: bool = True) -> dict:
    """Parse, store (new lines only) and match. Returns {'import_id', 'new_lines', 'duplicate_lines', 'matched', 'exceptions', 'messages',
    'already_imported'}. The same file (by content) is never imported twice."""
    file_hash = hashlib.sha256(data).hexdigest()
    with _conn() as conn:
        prev = conn.execute("SELECT import_id, datetime(imported_at, '+8 hours') AS imported_at FROM bank_statement_imports WHERE file_hash = ?", (file_hash,)).fetchone()
    if prev:
        return {"already_imported": True, "import_id": prev["import_id"], "imported_at": prev["imported_at"], "new_lines": 0,
                "duplicate_lines": 0, "matched": 0, "exceptions": 0, "messages": [f"This exact file was already imported on {prev['imported_at'][:16]}."]}
    parsed = parse_statement(data, filename)
    checks = capture_check(parsed, known_account_last4(bank_account_id))
    if not all(c["ok"] for c in checks):                               # owner: a statement that fails the capture check never goes in
        failed = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
        return {"already_imported": False, "blocked": True, "capture": checks, "import_id": None, "new_lines": 0, "duplicate_lines": 0,
                "matched": 0, "exceptions": 0, "messages": ["Not imported -- the capture check failed: " + "; ".join(failed)] + parsed.messages}
    stored_path = None
    if keep_copy:
        os.makedirs(bank_dir(), exist_ok=True)
        stored_path = os.path.join(bank_dir(), f"{manila_now():%Y%m%d-%H%M%S} {re.sub(r'[^A-Za-z0-9._ -]+', '_', filename)}")
        with open(stored_path, "wb") as f:
            f.write(data)
    new = dup = 0
    with write_tx(DB_PATH) as conn:          # Phase F1.4: "already imported?" and the save in ONE locked step
        conn.row_factory = sqlite3.Row
        again = conn.execute("SELECT import_id, datetime(imported_at, '+8 hours') AS imported_at FROM bank_statement_imports WHERE file_hash = ?",
                             (file_hash,)).fetchone()
        if again:                                                         # someone imported the same file a moment ago
            if stored_path and os.path.exists(stored_path):
                os.remove(stored_path)
            return {"already_imported": True, "import_id": again["import_id"], "imported_at": again["imported_at"], "new_lines": 0,
                    "duplicate_lines": 0, "matched": 0, "exceptions": 0,
                    "messages": [f"This exact file was already imported on {again['imported_at'][:16]}."]}
        cur = conn.execute("""INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash, stored_path, account_last4, period_from,
                                  period_to, opening_balance, closing_balance, line_count, balance_ok) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                           (bank_account_id, filename, file_hash, stored_path, parsed.account_number[-4:] or None,
                            parsed.period_from.isoformat() if parsed.period_from else None, parsed.period_to.isoformat() if parsed.period_to else None,
                            parsed.opening_balance, parsed.closing_balance, len(parsed.lines), int(parsed.balance_ok)))
        import_id = cur.lastrowid
        for x in parsed.lines:
            try:
                conn.execute("""INSERT INTO bank_transactions (import_id, bank_account_id, txn_date, posting_date, txn_time, check_number, description,
                                debit, credit, balance, channel, sequence_no, kind, line_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (import_id, bank_account_id, x.txn_date.isoformat(), x.posting_date.isoformat(), x.txn_time, x.check_number,
                              x.description, x.debit, x.credit, x.balance, x.channel, x.sequence, x.kind, line_key(bank_account_id, x)))
                new += 1
            except sqlite3.IntegrityError:                               # already imported from an overlapping statement
                dup += 1
        conn.execute("UPDATE bank_statement_imports SET new_lines = ? WHERE import_id = ?", (new, import_id))
        try:
            checks.append(_stored_check(conn, bank_account_id, parsed))
            if not checks[-1]["ok"]:
                raise CaptureCheckFailed(checks[-1]["detail"])
        except Exception:
            conn.rollback()                                              # nothing of this file stays in the database
            if stored_path and os.path.exists(stored_path):
                os.remove(stored_path)
            raise
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_STATEMENT_CAPTURE_CHECK', "
                     "'bank_statement_imports', ?, ?)", (import_id, "PASS -- " + "; ".join(f"{c['name']}: {c['detail']}" for c in checks)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_STATEMENT_IMPORTED', 'bank_statement_imports', ?, ?)",
                     (import_id, f"{filename}: {new} new line(s), {dup} already on file, period {parsed.period_from} to {parsed.period_to}"))
    result = match_checks()
    return {"already_imported": False, "blocked": False, "capture": checks, "import_id": import_id, "new_lines": new, "duplicate_lines": dup, "messages": parsed.messages,
            "balance_ok": parsed.balance_ok, "period_from": parsed.period_from, "period_to": parsed.period_to,
            "closing_balance": parsed.closing_balance, **result}


def match_checks() -> dict:
    """Match every still-open CHECK line to our checks (number + amount), then the other debits (bank-loan installments, credit-card
    statements), then set lines from before the system started aside. Idempotent: safe to run again at any time; a line already matched or
    resolved by the owner is never touched again.
    match_status: matched / amount_mismatch / cancelled_check_cleared / not_in_system (checks); matched_loan / matched_card /
    unmatched_debit (other debits); returned_check."""
    matched = exceptions = 0
    cleared_now: list[int] = []                                          # the checks this run clears: their batches and loans follow (engines/clearing.py)
    with _conn() as conn:
        ours = {}
        for r in conn.execute("SELECT check_id, check_number, amount, status, bank_txn_id FROM checks_issued WHERE check_number IS NOT NULL AND check_number != ''"):
            ours.setdefault(norm_check_number(r["check_number"]), []).append(dict(r))
        pending = conn.execute("""SELECT txn_id, check_number, debit, posting_date, rejected_check_ids FROM bank_transactions
                                  WHERE kind = 'CHECK' AND reviewed = 0
                                    AND (match_status IS NULL OR match_status IN ('amount_mismatch', 'not_in_system'))""").fetchall()
        try:                                                              # guarantee checks (migration 057): given to a lender as security, never meant to be paid
            guarantees = {norm_check_number(r["check_number"]): int(r["guarantee_id"]) for r in conn.execute(
                "SELECT guarantee_id, check_number FROM guarantee_checks WHERE status IN ('Held', 'Presented')")}
        except sqlite3.OperationalError:                                  # database without the table yet
            guarantees = {}
        for t in pending:
            gid = guarantees.get(norm_check_number(t["check_number"]))
            if gid is not None and norm_check_number(t["check_number"]) not in ours:
                conn.execute("UPDATE bank_transactions SET match_status = 'guarantee_check_presented' WHERE txn_id = ?", (t["txn_id"],))
                conn.execute("UPDATE guarantee_checks SET status = 'Presented', presented_on = ?, bank_txn_id = ? WHERE guarantee_id = ?",
                             (t["posting_date"], t["txn_id"], gid))
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'GUARANTEE_CHECK_PRESENTED', 'guarantee_checks', ?, ?)",
                             (gid, f"The bank paid / received guarantee check {t['check_number']} on {t['posting_date']} for PHP {float(t['debit']):,.2f}"))
                exceptions += 1
                continue
            rejected = {x for x in (t["rejected_check_ids"] or "").split(",") if x}           # a pair the owner took back (Reopen) is never re-matched
            cands = [c for c in ours.get(norm_check_number(t["check_number"]), []) if str(c["check_id"]) not in rejected]
            same_amount = [c for c in cands if abs(float(c["amount"]) - float(t["debit"])) < 0.005]
            free = [c for c in same_amount if not c["bank_txn_id"] or int(c["bank_txn_id"]) == int(t["txn_id"])]
            if same_amount and not free:
                # Double-entry guard (owner, 2026-09-28): one check is cleared by one bank line only. A second clearing of the same number and
                # amount never overwrites the first -- it waits for the owner (bank double debit, or two checks sharing a number).
                conn.execute("UPDATE bank_transactions SET matched_check_id = ?, match_status = 'cleared_twice' WHERE txn_id = ?",
                             (same_amount[0]["check_id"], t["txn_id"]))
                exceptions += 1
                continue
            if same_amount:
                c = next((c for c in free if c["status"] == "Active"), free[0])
                status = "matched" if c["status"] in ("Active", "Cleared") else "cancelled_check_cleared"
                conn.execute("UPDATE bank_transactions SET matched_check_id = ?, match_status = ? WHERE txn_id = ?", (c["check_id"], status, t["txn_id"]))
                conn.execute("UPDATE checks_issued SET bank_cleared_date = ?, bank_txn_id = ? WHERE check_id = ?", (t["posting_date"], t["txn_id"], c["check_id"]))
                c["bank_txn_id"] = t["txn_id"]
                cleared_now.append(int(c["check_id"]))
                if status == "matched":
                    matched += 1
                else:
                    exceptions += 1
            else:
                status = "amount_mismatch" if cands else "not_in_system"
                conn.execute("UPDATE bank_transactions SET matched_check_id = ?, match_status = ? WHERE txn_id = ?",
                             (cands[0]["check_id"] if cands else None, status, t["txn_id"]))
                exceptions += 1
        conn.execute("UPDATE bank_transactions SET match_status = 'returned_check' WHERE kind = 'RETURNED_CHECK' AND match_status IS NULL")
        matched += _match_other_debits(conn)
        _set_aside_before_system(conn)
    update_reconciled()
    from engines.clearing import check_ticks_against_bank, resync
    resync(cleared_now)                                                  # paid = cleared (2026-10-08): the invoices and loans of these checks follow
    check_ticks_against_bank()                                           # a tick the statement does not confirm goes on the Review list
    return {"matched": matched, "exceptions": exceptions}


def _match_other_debits(conn) -> int:
    """Bank debits that are not our checks: a debit memo of exactly a bank-loan installment's amount within 5 days of its due date is that
    installment (ADA); a bills payment of exactly a credit-card statement's amount due within 10 days of its due date is that card payment.
    Anything else stays 'unmatched_debit' for the owner to tag. Only when Settings -> Bank -> bank_match_other_debits is 1 (owner, 2026-10-17:
    "checks only ... since it is confirmed thru check number"; default 0): otherwise these debits are left for the owner to tag, and no card or loan
    is marked paid from the bank statement."""
    from engines.settings_registry import int_value
    if not int_value("bank_match_other_debits"):
        conn.execute("""UPDATE bank_transactions SET match_status = 'unmatched_debit' WHERE kind IN ('DEBIT_MEMO', 'BILLS_PAYMENT', 'OTHER') AND debit > 0
                        AND reviewed = 0 AND match_status IS NULL""")
        return 0
    n = 0
    rows = conn.execute("""SELECT txn_id, kind, debit, posting_date FROM bank_transactions
                           WHERE kind IN ('DEBIT_MEMO', 'BILLS_PAYMENT', 'OTHER') AND debit > 0 AND reviewed = 0
                             AND (match_status IS NULL OR match_status = 'unmatched_debit')""").fetchall()
    for t in rows:
        hit = None
        if t["kind"] == "DEBIT_MEMO":
            hit = conn.execute("""SELECT i.installment_id FROM bank_loan_installments i JOIN bank_loans bl ON bl.bank_loan_id = i.bank_loan_id
                                  WHERE bl.is_cancelled = 0 AND ABS(i.amount - ?) < 0.005 AND ABS(JULIANDAY(i.due_date) - JULIANDAY(?)) <= 5
                                    AND i.installment_id NOT IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL)
                                  ORDER BY ABS(JULIANDAY(i.due_date) - JULIANDAY(?)) LIMIT 1""", (t["debit"], t["posting_date"], t["posting_date"])).fetchone()
            if hit:
                conn.execute("UPDATE bank_transactions SET match_status = 'matched_loan', matched_installment_id = ? WHERE txn_id = ?", (hit[0], t["txn_id"]))
        if not hit and t["kind"] in ("BILLS_PAYMENT", "DEBIT_MEMO"):
            hit = conn.execute("""SELECT cc_statement_id FROM cc_statements
                                  WHERE status != 'Cancelled' AND ABS(JULIANDAY(due_date) - JULIANDAY(?)) <= 10
                                    AND (ABS(total_amount_due - ?) < 0.005 OR ABS(COALESCE(unpaid_balance_next, -1) - ?) < 0.005)
                                    AND cc_statement_id NOT IN (SELECT matched_cc_statement_id FROM bank_transactions WHERE matched_cc_statement_id IS NOT NULL)
                                  ORDER BY ABS(JULIANDAY(due_date) - JULIANDAY(?)) LIMIT 1""",
                               (t["posting_date"], t["debit"], t["debit"], t["posting_date"])).fetchone()
            if hit:
                conn.execute("UPDATE bank_transactions SET match_status = 'matched_card', matched_cc_statement_id = ? WHERE txn_id = ?", (hit[0], t["txn_id"]))
                # the bank statement is the truth: a debit of exactly the statement's total is that card paid. Marked Paid on the day the bank posted it (an amount that
                # differs from the total stays matched only, for the owner to look at).
                paid = conn.execute("""UPDATE cc_statements SET status = 'Paid', payment_method = 'Direct Debit', payment_date = ?, amount_paid = total_amount_due, unpaid_balance_next = 0
                                       WHERE cc_statement_id = ? AND status IN ('Unpaid', 'Partially Paid') AND ABS(total_amount_due - ?) < 0.005""",
                                    (str(t["posting_date"])[:10], hit[0], t["debit"])).rowcount
                if paid:
                    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CC_STATEMENT_PAID_FROM_BANK', 'cc_statements', ?, ?)",
                                 (hit[0], f"Paid PHP {float(t['debit']):,.2f} on {us_date(t['posting_date'])} (matched to the bank debit)"))
        if hit:
            n += 1
        else:
            conn.execute("UPDATE bank_transactions SET match_status = 'unmatched_debit' WHERE txn_id = ?", (t["txn_id"],))
    return n


def system_start() -> date | None:
    """The earliest check date in the system (checks before it were never recorded here)."""
    with _conn() as conn:
        v = conn.execute("SELECT MIN(check_date) FROM checks_issued").fetchone()[0]
    return date.fromisoformat(str(v)[:10]) if v else None


def _set_aside_before_system(conn) -> None:
    """A cleared check the system does not know, dated on the bank before the system's first check, is resolved automatically as
    'Before the system' -- it was written before this system was used, so there is nothing to do. Since 2026-09-28 (owner: "resolve this
    all", after a 12-month import left 115 old lines) bank debits that matched nothing and bounced customer checks from before then are
    tagged the way the owner tags them by hand: 'Expected bank debit' / 'Customer bounced check'."""
    v = conn.execute("SELECT MIN(check_date) FROM checks_issued").fetchone()[0]
    if not v:
        return
    start = str(v)[:10]
    for status, resolution, note in (("not_in_system", "Before the system", "Cleared before the system started"),
                                     ("unmatched_debit", "Expected bank debit", "Dated before the system started"),
                                     ("returned_check", "Customer bounced check", "Dated before the system started")):
        conn.execute("""UPDATE bank_transactions SET reviewed = 1, resolution = ?, note = COALESCE(note, ? || ' (' || ? || ')')
                        WHERE match_status = ? AND reviewed = 0 AND posting_date < ?""", (resolution, note, start, status, start))


EXCEPTION_STATUSES = ("guarantee_check_presented", "cleared_twice", "amount_mismatch", "cancelled_check_cleared", "returned_check", "not_in_system", "unmatched_debit")
RESOLUTIONS = ["Linked by hand", "Adjusted to the bank", "Corrected to the bank", "Not recorded in the system", "Bank error", "Legacy record", "Customer bounced check", "Expected bank debit", "Before the system", "Other"]


def exceptions(import_id: int | None = None) -> list[dict]:
    """Bank lines that need the owner's eye: checks that did not match cleanly, returned deposits, and bank debits that are not our checks
    and matched nothing (optionally for one imported statement). 'reviewed' = resolved."""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT t.txn_id, t.import_id, t.posting_date, t.check_number, t.description, t.debit, t.credit, t.kind, t.match_status, t.reviewed,
                      t.resolution, t.note, t.matched_check_id, c.check_number AS our_number, c.amount AS our_amount, c.status AS our_status,
                      c.statement_id, c.loan_note_id, cr.creditor_name
               FROM bank_transactions t LEFT JOIN checks_issued c ON c.check_id = t.matched_check_id
               LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE t.match_status IN (%s) %s
               ORDER BY t.posting_date DESC, t.txn_id DESC""" % (",".join("?" * len(EXCEPTION_STATUSES)), "AND t.import_id = ?" if import_id else ""),
            EXCEPTION_STATUSES + ((int(import_id),) if import_id else ())).fetchall()]


def resolved_lines(import_id: int | None = None) -> list[dict]:
    """Lines the owner (or the app) resolved: tagged exceptions and hand-linked checks -- listed so a wrong one can be reopened. The owner's
    own come first; the ones the app set aside as dated before the system started last."""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT t.txn_id, t.import_id, t.posting_date, t.check_number, t.description, t.debit, t.credit, t.kind, t.match_status,
                      t.resolution, t.note, c.check_number AS our_number, cr.creditor_name
               FROM bank_transactions t LEFT JOIN checks_issued c ON c.check_id = t.matched_check_id
               LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE t.reviewed = 1 AND (t.match_status IN (%s) OR t.match_status = 'matched_manual') %s
               ORDER BY COALESCE(t.note, '') LIKE '%%before the system started (%%', t.posting_date DESC, t.txn_id DESC""" % (",".join("?" * len(EXCEPTION_STATUSES)), "AND t.import_id = ?" if import_id else ""),
            EXCEPTION_STATUSES + ((int(import_id),) if import_id else ())).fetchall()]


def statement_coverage() -> tuple[date | None, date | None]:
    """First and last bank date covered by imported statements."""
    with _conn() as conn:
        a, b = conn.execute("SELECT MIN(posting_date), MAX(posting_date) FROM bank_transactions").fetchone()
    return (date.fromisoformat(a) if a else None, date.fromisoformat(b) if b else None)


def not_yet_cleared(as_of: date | None = None) -> list[dict]:
    """Our numbered, active checks dated on or before the last imported bank date that the bank has not cleared yet -- the checks
    suppliers have not deposited (or that the bank has not shown yet). Days waiting counted to the last imported bank date."""
    _, last = statement_coverage()
    if last is None:
        return []
    as_of = min(as_of or last, last)
    with _conn() as conn:
        rows = conn.execute(
            """SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name,
                      COALESCE(os.statement_ref, ln.note_ref) AS reference
               FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
               LEFT JOIN order_statements os ON os.statement_id = c.statement_id LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
               WHERE c.status = 'Active' AND c.check_number IS NOT NULL AND c.check_number != '' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL
                 AND c.check_date <= ? ORDER BY c.check_date""", (as_of.isoformat(),)).fetchall()
    return [dict(r, days_waiting=(as_of - date.fromisoformat(str(r["check_date"])[:10])).days) for r in rows]


def local_times(conn, row) -> dict:
    """An import row with imported_at / reconciled_at in local (Philippine) time -- SQLite's CURRENT_TIMESTAMP stores UTC, 8 hours behind,
    which made "imported N days ago" off by a day just after midnight."""
    d = dict(row)
    for k in ("imported_at", "reconciled_at"):
        if d.get(k):
            d[k] = conn.execute("SELECT datetime(?, '+8 hours')", (d[k],)).fetchone()[0]
    return d


def last_import() -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM bank_statement_imports ORDER BY import_id DESC LIMIT 1").fetchone()
        return local_times(conn, r) if r else None


def resolve(txn_id: int, resolution: str, note: str = "") -> None:
    """The owner has dealt with a line needing a look: record why (RESOLUTIONS) and an optional note. Audited."""
    with _conn() as conn:
        conn.execute("UPDATE bank_transactions SET reviewed = 1, resolution = ?, note = ? WHERE txn_id = ?", (resolution, note or None, int(txn_id)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_LINE_RESOLVED', 'bank_transactions', ?, ?)",
                     (int(txn_id), resolution + (f": {note}" if note else "")))
    update_reconciled()


def mark_reviewed(txn_id: int, note: str = "") -> None:        # kept for older callers
    resolve(txn_id, "Other", note)


def link_to_check(txn_id: int, check_id: int, how: str = "Linked by hand") -> tuple[bool, str]:
    """The owner says this bank line IS that check (a typo in the number, a check the matcher could not pair). Marks the check cleared by
    the bank on the line's date. Refused when the check is already cleared by another bank line."""
    with _conn() as conn:
        t = conn.execute("SELECT txn_id, posting_date, debit FROM bank_transactions WHERE txn_id = ?", (int(txn_id),)).fetchone()
        c = conn.execute("SELECT check_id, check_number, amount, bank_txn_id FROM checks_issued WHERE check_id = ?", (int(check_id),)).fetchone()
        if not t or not c:
            return False, "Bank line or check not found."
        if c["bank_txn_id"] and int(c["bank_txn_id"]) != int(txn_id):
            return False, f"Check {c['check_number'] or c['check_id']} is already cleared by another bank line."
        conn.execute("UPDATE bank_transactions SET matched_check_id = ?, match_status = 'matched_manual', reviewed = 1, resolution = ? WHERE txn_id = ?",
                     (c["check_id"], how, t["txn_id"]))
        conn.execute("UPDATE checks_issued SET bank_cleared_date = ?, bank_txn_id = ? WHERE check_id = ?", (t["posting_date"], t["txn_id"], c["check_id"]))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_LINE_LINKED', 'bank_transactions', ?, ?)",
                     (t["txn_id"], f"{how}: check {c['check_number'] or c['check_id']} (PHP {c['amount']:,.2f}) cleared {t['posting_date']} for PHP {t['debit']:,.2f}"))
    update_reconciled()
    from engines.clearing import resync
    resync([int(check_id)])
    return True, f"Linked: check {c['check_number'] or c['check_id']} is cleared by the bank on {t['posting_date']}."


BOOK_ERROR = "Incorrect recording in the book"
CLEARING_DIFF = "Bank clearing difference"


def bank_correction(txn_id: int) -> dict | None:
    """Owner rules (2026-09-28): "Bank statement is the ultimate truth of checks record" and "not direct edit the record but tag it as
    adjustment due to incorrect recording in the book". For a bank line that cleared one of our check numbers for a different amount:
    what the adjustments would be. The check itself is never changed. None when the line is not such a case.
    'date_differs': the bank paid BEFORE our recorded check date -- a check can't be paid before its date, so the recorded date is wrong
    (or it is a different check: 'warn' asks the owner to compare with the stub first)."""
    with _conn() as conn:
        r = conn.execute("""SELECT t.txn_id, t.posting_date, t.debit, c.check_id, c.check_number, c.check_date, c.amount, c.status,
                                   c.statement_id, c.loan_note_id, c.bank_txn_id
                            FROM bank_transactions t JOIN checks_issued c ON c.check_id = t.matched_check_id
                            WHERE t.txn_id = ? AND t.match_status = 'amount_mismatch'""", (int(txn_id),)).fetchone()
    if not r:
        return None
    rec_date = str(r["check_date"])[:10]
    out = {"check_id": r["check_id"], "check_number": r["check_number"], "check_date": rec_date, "bank_date": r["posting_date"],
           "old_amount": float(r["amount"]), "new_amount": float(r["debit"]), "diff": round(float(r["debit"]) - float(r["amount"]), 2),
           "date_differs": str(r["posting_date"]) < rec_date, "statement_id": r["statement_id"], "loan_note_id": r["loan_note_id"],
           "ok": True, "why": "", "warn": ""}
    if r["status"] != "Active":
        out.update(ok=False, why=f"Our check is {r['status']}, not Active.")
    elif r["bank_txn_id"] and int(r["bank_txn_id"]) != int(txn_id):
        out.update(ok=False, why="Our check is already cleared by another bank line.")
    elif out["date_differs"]:
        out["warn"] = (f"The bank paid this check number on {r['posting_date']}, before the recorded date {rec_date}. Only adjust if the check "
                       "stub shows it is the same check — otherwise tag the bank line instead.")
    return out


def adjust_check_to_bank(txn_id: int) -> tuple[bool, str]:
    """One click: leave our check exactly as recorded and add the adjustments -- CHECK_AMOUNT (bank - recorded) and CHECK_DATE (only when
    the bank paid before the recorded date), both "Incorrect recording in the book", plus BATCH_BALANCE of the same amount ("Bank clearing
    difference", owner: adjust it off too) so the batch / loan note owes exactly what the bank paid. Then the line is linked (the check is
    bank-cleared). All audited; Reopen on the bank line reverses the adjustments (kept, marked reversed)."""
    fix = bank_correction(txn_id)
    if not fix:
        return False, "This bank line is not one of our checks with a different amount."
    if not fix["ok"]:
        return False, fix["why"]
    rows = []
    if abs(fix["diff"]) >= 0.005:
        rows.append(("CHECK_AMOUNT", f"{fix['old_amount']:.2f}", f"{fix['new_amount']:.2f}", fix["diff"], BOOK_ERROR))
        if fix["statement_id"] or fix["loan_note_id"]:
            rows.append(("BATCH_BALANCE", None, None, fix["diff"], CLEARING_DIFF))
    if fix["date_differs"]:
        rows.append(("CHECK_DATE", fix["check_date"], fix["bank_date"], 0.0, BOOK_ERROR))
    with _conn() as conn:
        for kind, rec, bank, amount, reason in rows:
            conn.execute("""INSERT INTO bank_adjustments (kind, check_id, bank_txn_id, statement_id, loan_note_id, recorded_value, bank_value,
                            amount, reason) VALUES (?,?,?,?,?,?,?,?,?)""",
                         (kind, fix["check_id"], int(txn_id), fix["statement_id"], fix["loan_note_id"], rec, bank, amount, reason))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_ADJUSTMENT', 'checks_issued', ?, ?)",
                         (fix["check_id"], f"{kind}: {reason}; recorded {rec} / bank {bank} / {amount:+,.2f} (bank line {txn_id})"))
    ok, msg = link_to_check(txn_id, fix["check_id"], how="Adjusted to the bank")
    if not ok:
        return False, msg
    parts = [f"amount {fix['diff']:+,.2f}"] if abs(fix["diff"]) >= 0.005 else []
    parts += [f"date {fix['check_date']} -> {fix['bank_date']}"] if fix["date_differs"] else []
    return True, (f"Check {fix['check_number']} kept as recorded; adjustment added ({', '.join(parts)}) and marked cleared by the bank on "
                  f"{fix['bank_date']}.")


def adjustments(include_reversed: bool = False) -> list[dict]:
    """Every bank adjustment with its check and payee, newest first."""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            f"""SELECT a.*, datetime(a.created_at, '+8 hours') AS created_local, c.check_number, cr.creditor_name, COALESCE(os.statement_ref, ln.note_ref) AS reference
                FROM bank_adjustments a LEFT JOIN checks_issued c ON c.check_id = a.check_id
                LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id LEFT JOIN order_statements os ON os.statement_id = a.statement_id
                LEFT JOIN loan_notes ln ON ln.loan_note_id = a.loan_note_id
                {'' if include_reversed else 'WHERE a.reversed_at IS NULL'} ORDER BY a.adjustment_id DESC""").fetchall()]


def adjustment_totals(statement_id: int | None = None, loan_note_id: int | None = None) -> tuple[float, float]:
    """(batch adjustments, check-amount adjustments) still in force for a batch or loan note -- 0, 0 when the table is not there yet."""
    col, val = ("statement_id", statement_id) if statement_id is not None else ("loan_note_id", loan_note_id)
    try:
        with _conn() as conn:
            r = conn.execute(f"""SELECT COALESCE(SUM(CASE WHEN kind = 'BATCH_BALANCE' THEN amount END), 0),
                                        COALESCE(SUM(CASE WHEN kind = 'CHECK_AMOUNT' THEN amount END), 0)
                                 FROM bank_adjustments WHERE {col} = ? AND reversed_at IS NULL""", (val,)).fetchone()
    except sqlite3.OperationalError:
        return 0.0, 0.0
    return float(r[0]), float(r[1])


def reopen(txn_id: int) -> None:
    """Undo a resolution (a wrong tag or a wrong hand link): the line goes back to needing a look and is matched afresh. A check linked to it
    by hand is no longer cleared by the bank. Audited."""
    with _conn() as conn:
        t = conn.execute("SELECT txn_id, resolution FROM bank_transactions WHERE txn_id = ?", (int(txn_id),)).fetchone()
        if not t:
            return
        auto = conn.execute("SELECT matched_check_id FROM bank_transactions WHERE txn_id = ? AND match_status = 'matched' AND matched_check_id IS NOT NULL",
                            (t["txn_id"],)).fetchone()
        undone = [r[0] for r in conn.execute("SELECT matched_check_id FROM bank_transactions WHERE txn_id = ? AND match_status IN ('matched_manual', 'matched') "
                                             "AND matched_check_id IS NOT NULL", (t["txn_id"],))]
        conn.execute("UPDATE checks_issued SET bank_cleared_date = NULL, bank_txn_id = NULL WHERE bank_txn_id = ? AND check_id IN "
                     "(SELECT matched_check_id FROM bank_transactions WHERE txn_id = ? AND match_status IN ('matched_manual', 'matched'))", (t["txn_id"], t["txn_id"]))
        if auto:                                             # a wrong automatic match (2026-10-08 gap fix): undone, and that pair is not matched again
            row = conn.execute("SELECT rejected_check_ids FROM bank_transactions WHERE txn_id = ?", (t["txn_id"],)).fetchone()
            ids = [x for x in ((row[0] or "").split(",") if row else []) if x] + [str(int(auto[0]))]
            conn.execute("UPDATE bank_transactions SET rejected_check_ids = ? WHERE txn_id = ?", (",".join(ids), t["txn_id"]))
        try:
            n_adj = conn.execute("UPDATE bank_adjustments SET reversed_at = CURRENT_TIMESTAMP WHERE bank_txn_id = ? AND reversed_at IS NULL",
                                 (t["txn_id"],)).rowcount
        except sqlite3.OperationalError:                                  # database without the adjustments table yet
            n_adj = 0
        if n_adj:
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'BANK_ADJUSTMENT_REVERSED', "
                         "'bank_transactions', ?, ?)", (t["txn_id"], f"{n_adj} adjustment(s) reversed"))
        conn.execute("""UPDATE bank_transactions SET reviewed = 0, resolution = NULL, note = NULL, matched_check_id = NULL, matched_installment_id = NULL,
                            matched_cc_statement_id = NULL, match_status = NULL WHERE txn_id = ?""", (t["txn_id"],))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'BANK_LINE_REOPENED', 'bank_transactions', ?, ?)",
                     (t["txn_id"], t["resolution"]))
    match_checks()
    from engines.clearing import resync
    resync([int(i) for i in undone])                                     # a check no longer cleared is owed again


def link_candidates(txn_id: int, limit: int = 60) -> list[dict]:
    """Our active checks the bank has not cleared, for linking a bank line by hand: same amount first, then the nearest check date."""
    with _conn() as conn:
        t = conn.execute("SELECT posting_date, debit FROM bank_transactions WHERE txn_id = ?", (int(txn_id),)).fetchone()
        if not t:
            return []
        return [dict(r) for r in conn.execute(
            """SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name, ABS(c.amount - ?) < 0.005 AS same_amount
               FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL
               ORDER BY same_amount DESC, ABS(JULIANDAY(c.check_date) - JULIANDAY(?)) LIMIT ?""", (t["debit"], t["posting_date"], int(limit))).fetchall()]


def _one_digit_apart(a: str, b: str) -> bool:
    """Same length and either exactly one digit different, or two neighbouring digits swapped."""
    if len(a) != len(b) or a == b:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])


CONFIDENCE_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def suggestions() -> list[dict]:
    """Suggested (never automatic) matches for unmatched check lines: one of our active checks not cleared yet with the SAME amount and a
    number one digit off / two digits swapped, or with no number yet and a check date within 7 days of the bank date. Rejected pairs are
    not suggested again.
    Smarter since 2026-09-28 (owner: "make suggestions more intelligent", after leaf ...673 paid Sep 21 was accepted as our ...678 dated
    Sep 29): each suggestion has a 'confidence' (High / Medium / Low) and 'reasons' (why), best first. A check dated AFTER the day the
    bank paid it is always Low -- it cannot be that check unless our date is wrong. 'reason' = the matching rule (unchanged)."""
    with _conn() as conn:
        lines = conn.execute("""SELECT txn_id, posting_date, check_number, debit, rejected_check_ids FROM bank_transactions
                                WHERE kind = 'CHECK' AND reviewed = 0 AND match_status IN ('amount_mismatch', 'not_in_system')""").fetchall()
        ours = conn.execute("""SELECT c.check_id, c.check_number, c.check_date, c.amount, cr.creditor_name FROM checks_issued c
                               JOIN creditors cr ON cr.creditor_id = c.creditor_id WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL""").fetchall()
    out = []
    for t in lines:
        rejected = {x for x in (t["rejected_check_ids"] or "").split(",") if x}
        tnum = norm_check_number(t["check_number"])
        bank_day = date.fromisoformat(t["posting_date"])
        found = []
        for c in ours:
            if str(c["check_id"]) in rejected or abs(float(c["amount"]) - float(t["debit"])) >= 0.005:
                continue
            cnum = norm_check_number(c["check_number"])
            gap = (bank_day - date.fromisoformat(str(c["check_date"])[:10])).days          # days from our check date to the bank payment
            if cnum and _one_digit_apart(tnum, cnum):
                reason = "same amount, check number one digit off"
            elif not cnum and abs(gap) <= 7:
                reason = "same amount, our check has no number yet, dated within 7 days"
            else:
                continue
            found.append((c, reason, gap))
        for c, reason, gap in found:
            reasons = ["same amount", "number one digit off" if c["check_number"] else "our check has no number yet"]
            if gap < 0:
                reasons.append(f"our check is dated {-gap} day(s) AFTER the bank paid it — unlikely to be this check")
                conf = "Low"
            else:
                reasons.append("paid on its date" if gap == 0 else f"paid {gap} day(s) after its date")
                others = sum(1 for _, _, g in found if g >= 0) - 1
                if others:
                    reasons.append(f"{others} other check(s) also fit")
                conf = "High" if c["check_number"] and not others and gap <= 60 else "Medium"
            out.append({"txn_id": t["txn_id"], "posting_date": t["posting_date"], "bank_check_number": t["check_number"], "amount": t["debit"],
                        "check_id": c["check_id"], "our_number": c["check_number"], "check_date": c["check_date"], "payee": c["creditor_name"],
                        "reason": reason, "confidence": conf, "reasons": reasons, "gap_days": gap})
    out.sort(key=lambda s: (s["posting_date"], s["txn_id"], CONFIDENCE_ORDER[s["confidence"]], abs(s["gap_days"])))
    return out


def series_hints(span: int = 5) -> list[dict]:
    """A leaf the bank paid that is not in the system, next to leaves that ARE ours for the same payee and amount (Battery Depot ...674 to
    ...678 at PHP 62,637 each; the bank paid ...673): most likely a check of that payee's series that was never recorded here. The owner
    tags it with one click (owner, 2026-09-28: tag with the payee -- no check is created)."""
    with _conn() as conn:
        lines = conn.execute("""SELECT txn_id, posting_date, check_number, debit FROM bank_transactions
                                WHERE kind = 'CHECK' AND reviewed = 0 AND match_status = 'not_in_system'""").fetchall()
        ours = conn.execute("""SELECT c.check_number, c.amount, cr.creditor_name FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                               WHERE c.status != 'Cancelled' AND c.check_number IS NOT NULL AND c.check_number != ''""").fetchall()
    by_num = {}
    for c in ours:
        n = norm_check_number(c["check_number"])
        if n.isdigit():
            by_num[int(n)] = c
    out = []
    for t in lines:
        n = norm_check_number(t["check_number"])
        if not n.isdigit():
            continue
        near = [by_num[int(n) + d] for d in range(-span, span + 1) if d and int(n) + d in by_num
                and abs(float(by_num[int(n) + d]["amount"]) - float(t["debit"])) < 0.005]
        payees = {c["creditor_name"] for c in near}
        if len(near) >= 2 and len(payees) == 1:
            nums = sorted(c["check_number"] for c in near)
            out.append({"txn_id": t["txn_id"], "posting_date": t["posting_date"], "bank_check_number": t["check_number"], "amount": t["debit"],
                        "payee": near[0]["creditor_name"], "neighbours": nums,
                        "reason": f"leaves …{nums[0][-4:]}–…{nums[-1][-4:]} near it are {near[0]['creditor_name']} checks for the same amount; "
                                  "this leaf is not in the system"})
    return out


def tag_series(txn_id: int, payee: str, note: str = "") -> None:
    """Resolve a series leaf as a check of that payee not recorded in the system (no check is created). Audited through resolve()."""
    resolve(txn_id, "Not recorded in the system", f"Probably a {payee} check" + (f" -- {note}" if note else ""))


def reject_suggestion(txn_id: int, check_id: int) -> None:
    with _conn() as conn:
        row = conn.execute("SELECT rejected_check_ids FROM bank_transactions WHERE txn_id = ?", (int(txn_id),)).fetchone()
        ids = [x for x in ((row[0] or "").split(",") if row else []) if x] + [str(int(check_id))]
        conn.execute("UPDATE bank_transactions SET rejected_check_ids = ? WHERE txn_id = ?", (",".join(ids), int(txn_id)))


def update_reconciled() -> None:
    """An imported statement is Reconciled once none of its lines needing a look is open (and back to Open if one reopens)."""
    with _conn() as conn:
        for imp in conn.execute("SELECT import_id, reconciled_at FROM bank_statement_imports").fetchall():
            open_n = conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE import_id = ? AND reviewed = 0 AND match_status IN (%s)"
                                  % ",".join("?" * len(EXCEPTION_STATUSES)), (imp["import_id"],) + EXCEPTION_STATUSES).fetchone()[0]
            if open_n == 0 and not imp["reconciled_at"]:
                conn.execute("UPDATE bank_statement_imports SET reconciled_at = CURRENT_TIMESTAMP WHERE import_id = ?", (imp["import_id"],))
            elif open_n and imp["reconciled_at"]:
                conn.execute("UPDATE bank_statement_imports SET reconciled_at = NULL WHERE import_id = ?", (imp["import_id"],))


def imports_status() -> list[dict]:
    """Every imported statement with its period, lines, closing balance, open items and Open / Reconciled status."""
    with _conn() as conn:
        rows = [local_times(conn, r) for r in conn.execute("SELECT * FROM bank_statement_imports ORDER BY period_to DESC, import_id DESC").fetchall()]
        for r in rows:
            r["open_items"] = conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE import_id = ? AND reviewed = 0 AND match_status IN (%s)"
                                           % ",".join("?" * len(EXCEPTION_STATUSES)), (r["import_id"],) + EXCEPTION_STATUSES).fetchone()[0]
            r["status"] = "Reconciled" if r["reconciled_at"] else "Open"
    return rows


def preview_matches(parsed: ParsedStatement) -> dict:
    """Read-only: how the CHECK lines of a parsed (not yet imported) statement would match -- for the preview before importing."""
    with _conn() as conn:
        ours = {}
        for r in conn.execute("SELECT check_number, amount, status FROM checks_issued WHERE check_number IS NOT NULL AND check_number != ''"):
            ours.setdefault(norm_check_number(r["check_number"]), []).append(dict(r))
    out = {"matched": 0, "amount_mismatch": 0, "cancelled_check_cleared": 0, "not_in_system": 0}
    for x in parsed.lines:
        if x.kind != "CHECK":
            continue
        cands = ours.get(norm_check_number(x.check_number), [])
        same = [c for c in cands if abs(float(c["amount"]) - x.debit) < 0.005]
        if same:
            out["matched" if any(c["status"] in ("Active", "Cleared") for c in same) else "cancelled_check_cleared"] += 1
        else:
            out["amount_mismatch" if cands else "not_in_system"] += 1
    return out
