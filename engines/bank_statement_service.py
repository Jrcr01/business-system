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

Show-both rule (owner, 2026-09-27): the rest of the app keeps treating a check as cleared on its check date; the bank-confirmed date is shown
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
    "subscriber": ["subscriber number/tin", "subscriber number tin", "subscriber number", "tin"],
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
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm_check_number(n) -> str:
    """The same leaf whether written 2554301 or 3992554301 (as in the no-duplicate rule, migration 047). All zeros = no check."""
    s = re.sub(r"\D", "", str(n or ""))
    if len(s) == 10 and s.startswith("399"):
        s = s[3:]
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
    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    else:
        df = pd.read_excel(io.BytesIO(data), dtype=str, keep_default_na=False)
    cm = _map_columns(list(df.columns))
    if not {"txn_date", "debit", "credit"} <= cm.keys():
        parsed.messages.append("Could not find the Date / Debit / Credit columns in this file. Columns found: " + ", ".join(map(str, df.columns)))
        return parsed
    cols = list(df.columns)
    for _, r in df.iterrows():
        try:
            line = _line_from({f: r[cols[i]] for f, i in cm.items()})
        except ValueError:
            parsed.messages.append(f"Could not read the amounts on one line: {' / '.join(map(str, r.values))[:80]}")
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


# ------------------------------------------------------------------ storing and matching
def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def line_key(bank_account_id: int, x: StatementLine) -> str:
    """The same bank line always gets the same key, so overlapping statement periods / a re-imported file never double up."""
    raw = "|".join(map(str, (bank_account_id, x.posting_date, x.txn_date, x.txn_time, x.check_number, x.description, f"{x.debit:.2f}",
                             f"{x.credit:.2f}", "" if x.balance is None else f"{x.balance:.2f}", x.sequence)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bank_dir() -> str:
    """Where imported statement files are kept (outside the project and git): a 'bank' folder beside the Drive login folder / live DB."""
    return os.path.join(os.path.dirname(db_location.drive_dir()), "bank")


def import_statement(data: bytes, filename: str, bank_account_id: int = 1, keep_copy: bool = True) -> dict:
    """Parse, store (new lines only) and match. Returns {'import_id', 'new_lines', 'duplicate_lines', 'matched', 'exceptions', 'messages',
    'already_imported'}. The same file (by content) is never imported twice."""
    file_hash = hashlib.sha256(data).hexdigest()
    with _conn() as conn:
        prev = conn.execute("SELECT import_id, imported_at FROM bank_statement_imports WHERE file_hash = ?", (file_hash,)).fetchone()
    if prev:
        return {"already_imported": True, "import_id": prev["import_id"], "imported_at": prev["imported_at"], "new_lines": 0,
                "duplicate_lines": 0, "matched": 0, "exceptions": 0, "messages": [f"This exact file was already imported on {prev['imported_at'][:16]}."]}
    parsed = parse_statement(data, filename)
    if not parsed.lines:
        return {"already_imported": False, "import_id": None, "new_lines": 0, "duplicate_lines": 0, "matched": 0, "exceptions": 0,
                "messages": parsed.messages}
    stored_path = None
    if keep_copy:
        os.makedirs(bank_dir(), exist_ok=True)
        stored_path = os.path.join(bank_dir(), f"{datetime.now():%Y%m%d-%H%M%S} {re.sub(r'[^A-Za-z0-9._ -]+', '_', filename)}")
        with open(stored_path, "wb") as f:
            f.write(data)
    new = dup = 0
    with _conn() as conn:
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
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BANK_STATEMENT_IMPORTED', 'bank_statement_imports', ?, ?)",
                     (import_id, f"{filename}: {new} new line(s), {dup} already on file, period {parsed.period_from} to {parsed.period_to}"))
    result = match_checks()
    return {"already_imported": False, "import_id": import_id, "new_lines": new, "duplicate_lines": dup, "messages": parsed.messages,
            "balance_ok": parsed.balance_ok, "period_from": parsed.period_from, "period_to": parsed.period_to,
            "closing_balance": parsed.closing_balance, **result}


def match_checks() -> dict:
    """Match every not-yet-matched CHECK line to our checks (number + amount). Idempotent: safe to run again at any time.
    match_status: matched / amount_mismatch / cancelled_check_cleared / not_in_system."""
    matched = exceptions = 0
    with _conn() as conn:
        ours = {}
        for r in conn.execute("SELECT check_id, check_number, amount, status FROM checks_issued WHERE check_number IS NOT NULL AND check_number != ''"):
            ours.setdefault(norm_check_number(r["check_number"]), []).append(dict(r))
        pending = conn.execute("""SELECT txn_id, check_number, debit, posting_date FROM bank_transactions
                                  WHERE kind = 'CHECK' AND (match_status IS NULL OR match_status != 'matched')""").fetchall()
        for t in pending:
            cands = ours.get(norm_check_number(t["check_number"]), [])
            same_amount = [c for c in cands if abs(float(c["amount"]) - float(t["debit"])) < 0.005]
            if same_amount:
                c = next((c for c in same_amount if c["status"] == "Active"), same_amount[0])
                status = "matched" if c["status"] in ("Active", "Cleared") else "cancelled_check_cleared"
                conn.execute("UPDATE bank_transactions SET matched_check_id = ?, match_status = ? WHERE txn_id = ?", (c["check_id"], status, t["txn_id"]))
                conn.execute("UPDATE checks_issued SET bank_cleared_date = ?, bank_txn_id = ? WHERE check_id = ?", (t["posting_date"], t["txn_id"], c["check_id"]))
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
    return {"matched": matched, "exceptions": exceptions}


def exceptions() -> list[dict]:
    """Bank lines that need the owner's eye (everything that is not a clean match / a normal deposit / a known debit)."""
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT t.txn_id, t.posting_date, t.check_number, t.description, t.debit, t.credit, t.kind, t.match_status, t.reviewed,
                      c.check_number AS our_number, c.amount AS our_amount, c.status AS our_status, cr.creditor_name
               FROM bank_transactions t LEFT JOIN checks_issued c ON c.check_id = t.matched_check_id
               LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE t.match_status IN ('amount_mismatch', 'cancelled_check_cleared', 'returned_check', 'not_in_system')
               ORDER BY t.posting_date DESC, t.txn_id DESC""").fetchall()]


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
               WHERE c.status = 'Active' AND c.check_number IS NOT NULL AND c.check_number != '' AND c.bank_cleared_date IS NULL
                 AND c.check_date <= ? ORDER BY c.check_date""", (as_of.isoformat(),)).fetchall()
    return [dict(r, days_waiting=(as_of - date.fromisoformat(str(r["check_date"])[:10])).days) for r in rows]


def last_import() -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM bank_statement_imports ORDER BY import_id DESC LIMIT 1").fetchone()
    return dict(r) if r else None


def mark_reviewed(txn_id: int, note: str = "") -> None:
    with _conn() as conn:
        conn.execute("UPDATE bank_transactions SET reviewed = 1, note = ? WHERE txn_id = ?", (note or None, int(txn_id)))


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
