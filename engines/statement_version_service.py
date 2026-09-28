"""Statement versions and their public A4 copy (Thermal Statement Printing, build step 2, 2026-09-27).

A batch's statement has versions (statement_versions, migration 048). Each version's A4 PDF is uploaded to Drive ONCE and shared
anyone-with-link; the slip's QR encodes that link, and a reprint of the same version reuses it. Printing a newer version switches the
older versions' sharing off (revoke_older_versions) -- their files stay, privately. A new version starts when the statement's content
changed after it was published or printed (step 3): on re-lock after an unlock (the batch is then flagged "Needs reprint"), and again at
print time as a safety net. Print types per copy: ORIGINAL (nothing printed yet), REPRINT (this version's copy already printed), REVISED
(an older version was printed). Viewing -- the preview, the PDFs, the Drive link -- is never recorded as a print.

Any failure here raises, and the caller must abort the print: no slip without a working QR.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime

from engines.batch_statement_service import _fetch_batch_data, generate_batch_statement_pdf
from engines.drive_service import DriveError, statement_folder_id
from engines.statement_rules import add_terms_bank_rule
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _conn() -> sqlite3.Connection:
    conn = get_conn(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def content_hash(statement_id: int) -> str | None:
    """Fingerprint of what the statement shows (every invoice, credit memo and check with their amounts, dates and statuses). Two
    moments with the same fingerprint have the same statement; a different one means the content changed -- a revision. Check NUMBERS are
    left out on purpose: numbers are assigned after the checks are printed, which is the normal flow, not a revision of the statement."""
    data = _fetch_batch_data(statement_id, True, True)
    if data is None:
        return None
    s = data["statement"]
    payload = {
        "batch": [s["statement_ref"], s["creditor_name"]],
        "invoices": [[i["doc_ref"], i["invoice_date"], i["due_date"], i["amount"], i["status"]] for i in data["invoices"]],
        "memos": [[m["memo_ref"], m["memo_date"], m["amount"], m["reason"]] for m in data["memos"]],
        "checks": [[c["check_date"], c["amount"], c["status"]] for c in data["checks"]],
    }
    return hashlib.sha256(json.dumps(payload, default=str, sort_keys=True).encode("utf-8")).hexdigest()


def current_version(statement_id: int) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM statement_versions WHERE statement_id = ? ORDER BY version_no DESC LIMIT 1", (int(statement_id),)).fetchone()
    return dict(row) if row else None


def ensure_current_version(statement_id: int) -> dict:
    """The batch's current version, creating version 1 the first time."""
    v = current_version(statement_id)
    if v:
        return v
    with _conn() as conn:
        conn.execute("INSERT INTO statement_versions (statement_id, version_no, content_hash) VALUES (?, 1, ?)",
                     (int(statement_id), content_hash(statement_id)))
    return current_version(statement_id)


def _plus_3_months(d) -> date:
    return add_terms_bank_rule(str(d)[:10], "90")          # same day 3 months later; month-end clamp (Nov 30 -> Feb 28)


def _as_date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def link_expiry(statement_id: int, version: dict | None = None) -> date | None:
    """Until when the version's public link stays on (owner, 2026-09-27): the batch's last Active check date + 3 months (a check counts as
    cleared on its date); a batch with no checks yet uses its last invoice due date + 3 months. A reprint after expiry re-opened it
    until reopened_until -- the later date wins. Recomputed every time, so re-dated / replaced / un-parked checks move it. None = no date
    yet (no checks and no due dates)."""
    with _conn() as conn:
        last_check = conn.execute("SELECT MAX(check_date) FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
                                  (int(statement_id),)).fetchone()[0]
        base = last_check or conn.execute("SELECT MAX(due_date) FROM invoices WHERE statement_id = ? AND status != 'Cancelled'",
                                          (int(statement_id),)).fetchone()[0]
    dates = [d for d in (_plus_3_months(base) if base else None, _as_date((version or {}).get("reopened_until"))) if d]
    return max(dates) if dates else None


def _audit(conn, action: str, statement_id: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES (?, 'order_statements', ?, ?)",
                 (action, int(statement_id), text))


def ensure_published(statement_id: int, drive, sandbox: bool = False, today: date | None = None) -> dict:
    """The current version with a working public link: uploaded + shared now if it hasn't been yet, otherwise reused as it is (a new
    upload never replaces an existing file). If the current version's link was switched off because it EXPIRED, printing it again
    re-opens the same file for 3 more months from today (owner, 2026-09-27) -- only older versions are ever switched off by a revision,
    and those are never re-opened. A first print of a batch whose expiry has already passed also gets today + 3 months, so a printed QR
    always works. Raises DriveError / DriveNotConnected on any failure -- nothing is recorded as uploaded / re-opened then."""
    today = today or date.today()
    sync_version(statement_id)                                         # changed since it went out -> the next version is printed
    v = ensure_current_version(statement_id)
    if v["drive_file_id"] and v["drive_public"]:
        return v
    if v["drive_file_id"]:                                             # current version, switched off because it expired -> re-open
        drive.share_public(v["drive_file_id"])
        reopened = _plus_3_months(today)
        until = link_expiry(statement_id, dict(v, reopened_until=reopened.isoformat()))
        with _conn() as conn:
            conn.execute("""UPDATE statement_versions SET drive_public = 1, revoked_at = NULL, reopened_until = ?, public_until = ?
                            WHERE version_id = ?""", (reopened.isoformat(), until.isoformat(), v["version_id"]))
            _audit(conn, "STATEMENT_PDF_REOPENED", statement_id, f"v{v['version_no']} public link re-opened by a reprint until {until.isoformat()} ({v['drive_file_id']})")
        return current_version(statement_id)
    with _conn() as conn:
        ref = conn.execute("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (int(statement_id),)).fetchone()[0]
    pdf = generate_batch_statement_pdf(int(statement_id), True, "supplier", False)
    if not pdf:
        raise DriveError("Could not make the A4 statement PDF for this batch.")
    name = f"{ref} - statement v{v['version_no']} - {datetime.now():%Y%m%d-%H%M%S}.pdf"
    folder_id = statement_folder_id(drive, sandbox)
    file_id, link = drive.upload_pdf(pdf, name, folder_id)
    drive.share_public(file_id)                                        # only after this succeeds is the version recorded as published
    until = link_expiry(statement_id, v)
    reopened = None
    if until is not None and until < today:                            # first print of an old batch: keep the QR working 3 months
        reopened = _plus_3_months(today)
        until = reopened
    with _conn() as conn:
        conn.execute("""UPDATE statement_versions SET pdf_file_name = ?, drive_file_id = ?, drive_link = ?, drive_public = 1,
                        uploaded_at = CURRENT_TIMESTAMP, public_until = ?, reopened_until = ? WHERE version_id = ?""",
                     (name, file_id, link, until.isoformat() if until else None, reopened.isoformat() if reopened else None, v["version_id"]))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('STATEMENT_PDF_PUBLISHED', 'order_statements', ?, ?)",
                     (int(statement_id), f"v{v['version_no']} uploaded to Drive and shared by link: {name} ({file_id})"))
    return current_version(statement_id)


def revoke_older_versions(statement_id: int, keep_version_id: int, drive) -> list[int]:
    """Switch off public sharing on every other version of this batch that is still shared (the file is kept, privately). Returns the
    version numbers revoked. Raises DriveError if Drive refuses -- the version stays marked public then, so it is retried next time."""
    with _conn() as conn:
        rows = conn.execute("""SELECT version_id, version_no, drive_file_id FROM statement_versions
                               WHERE statement_id = ? AND version_id != ? AND drive_public = 1 AND drive_file_id IS NOT NULL""",
                            (int(statement_id), int(keep_version_id))).fetchall()
    revoked = []
    for r in rows:
        drive.revoke_public(r["drive_file_id"])
        with _conn() as conn:
            conn.execute("UPDATE statement_versions SET drive_public = 0, revoked_at = CURRENT_TIMESTAMP WHERE version_id = ?", (r["version_id"],))
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('STATEMENT_PDF_REVOKED', 'order_statements', ?, ?)",
                         (int(statement_id), f"v{r['version_no']} public link switched off ({r['drive_file_id']}); file kept privately"))
        revoked.append(int(r["version_no"]))
    return revoked


def expired_public_versions(today: date | None = None) -> list[dict]:
    """Versions whose link is still public but past its expiry date (no Drive call) -- for the sweep and the Payment run reminder."""
    today = today or date.today()
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM statement_versions WHERE drive_public = 1 AND drive_file_id IS NOT NULL ORDER BY statement_id, version_no").fetchall()]
    out = []
    for v in rows:
        until = link_expiry(v["statement_id"], v)
        if until is not None and today > until:
            out.append(dict(v, expiry=until))
    return out


def expire_public_links(drive, today: date | None = None) -> dict:
    """The daily switch-off (owner, 2026-09-27): every version whose public link is past its expiry (link_expiry) has its
    anyone-with-link sharing removed -- the file stays in Drive, privately -- and is audited. Also refreshes public_until on every public
    version. One failure does not stop the others; a failed one stays marked public and is tried again next time.
    Returns {'switched_off': [(statement_id, version_no, expiry)], 'failed': [(statement_id, version_no, error)], 'checked': n}."""
    today = today or date.today()
    with _conn() as conn:
        public = [dict(r) for r in conn.execute(
            "SELECT * FROM statement_versions WHERE drive_public = 1 AND drive_file_id IS NOT NULL").fetchall()]
    switched_off, failed = [], []
    for v in public:
        until = link_expiry(v["statement_id"], v)
        with _conn() as conn:
            conn.execute("UPDATE statement_versions SET public_until = ? WHERE version_id = ?", (until.isoformat() if until else None, v["version_id"]))
        if until is None or today <= until:
            continue
        try:
            drive.revoke_public(v["drive_file_id"])
        except Exception as e:                                         # DriveError, lapsed sign-in, no internet
            failed.append((v["statement_id"], v["version_no"], str(e)))
            continue
        with _conn() as conn:
            conn.execute("UPDATE statement_versions SET drive_public = 0, revoked_at = CURRENT_TIMESTAMP WHERE version_id = ?", (v["version_id"],))
            _audit(conn, "STATEMENT_PDF_EXPIRED", v["statement_id"],
                   f"v{v['version_no']} public link switched off: expired {until.isoformat()} ({v['drive_file_id']}); file kept privately")
        switched_off.append((v["statement_id"], v["version_no"], until))
    return {"switched_off": switched_off, "failed": failed, "checked": len(public)}



SWEEP_KEY = "statement_links_last_sweep"


def daily_link_sweep(make_drive, today: date | None = None) -> dict | None:
    """Once a day (the first time Payment run opens), the same switch-off as the nightly task -- in case the laptop was off at 18:00.
    make_drive() is only called when something is actually past its date. Returns None when today's sweep already ran; otherwise the
    expire_public_links() result, or {'error': ..., 'due': n} when Drive could not be reached (the reminder then asks for drive_connect.bat).
    The date is recorded either way, so a closed Drive never slows every page load; the nightly task and tomorrow try again."""
    today = today or date.today()
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (SWEEP_KEY,)).fetchone()
    if row and row[0] == today.isoformat():
        return None
    due = expired_public_versions(today)
    result = {"switched_off": [], "failed": [], "checked": 0}
    if due:
        try:
            result = expire_public_links(make_drive(), today)
        except Exception as e:                                         # not signed in / sign-in lapsed / offline
            result = {"error": str(e), "due": len(due)}
    with _conn() as conn:
        if row:
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (today.isoformat(), SWEEP_KEY))
        else:
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, 'thermal_printer', ?)",
                         (SWEEP_KEY, today.isoformat(), "Last day the app switched off expired statement QR links"))
    return result


# ---------------------------------------------------------------- step 3: versions, "Needs reprint", ORIGINAL / REPRINT / REVISED
def version_printed(version_id: int, copy: str | None = None) -> bool:
    """True if this version (optionally: this copy) was printed successfully. A FAILED attempt never counts."""
    sql, args = "SELECT 1 FROM statement_prints WHERE version_id = ? AND status = 'SUCCESS'", [int(version_id)]
    if copy:
        sql, args = sql + " AND copy_type = ?", args + [copy]
    with _conn() as conn:
        return conn.execute(sql + " LIMIT 1", args).fetchone() is not None


def batch_ever_printed(statement_id: int) -> bool:
    with _conn() as conn:
        return conn.execute("SELECT 1 FROM statement_prints WHERE statement_id = ? AND status = 'SUCCESS' LIMIT 1",
                            (int(statement_id),)).fetchone() is not None


def _needs_new_version(v: dict, current_hash: str | None) -> bool:
    """The content changed since this version, and this version already went out (its PDF was published or a copy was printed)."""
    return v["content_hash"] != current_hash and (bool(v["drive_file_id"]) or version_printed(v["version_id"]))


def sync_version(statement_id: int) -> dict | None:
    """Bring the current version up to date with the statement: if the content changed after the current version went out, start the
    next version (unpublished; its PDF / link are made when it is printed); if the current version never went out, just refresh its
    fingerprint. Returns the (possibly new) current version, or None if nothing was ever published or printed for the batch."""
    v = current_version(statement_id)
    if v is None:
        return None
    h = content_hash(statement_id)
    if v["content_hash"] == h:
        return v
    with _conn() as conn:
        if _needs_new_version(v, h):
            conn.execute("INSERT INTO statement_versions (statement_id, version_no, content_hash) VALUES (?, ?, ?)",
                         (int(statement_id), v["version_no"] + 1, h))
            _audit(conn, "STATEMENT_VERSION_CREATED", statement_id,
                   f"v{v['version_no'] + 1} started: the statement changed after v{v['version_no']} went out")
        else:
            conn.execute("UPDATE statement_versions SET content_hash = ? WHERE version_id = ?", (h, v["version_id"]))
    return current_version(statement_id)


def on_relock(statement_id: int) -> bool:
    """Called when a batch is locked again (lock_statement): if the statement changed after a printed version, a new version starts and
    the batch is flagged "Needs reprint" until the revised statement is printed. Returns True when the flag was set."""
    before = current_version(statement_id)
    after = sync_version(statement_id)
    if not before or not after or after["version_no"] == before["version_no"] or not batch_ever_printed(statement_id):
        return False
    with _conn() as conn:
        conn.execute("UPDATE order_statements SET needs_reprint = 1 WHERE statement_id = ?", (int(statement_id),))
        _audit(conn, "BATCH_NEEDS_REPRINT", statement_id,
               f"edited after unlock and re-locked: v{after['version_no']} must be printed (supersedes v{before['version_no']})")
    return True


def needs_reprint(statement_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute("SELECT needs_reprint FROM order_statements WHERE statement_id = ?", (int(statement_id),)).fetchone()
    return bool(row and row[0])


def print_type_for(statement_id: int, copy: str, version: dict | None = None) -> dict:
    """ORIGINAL / REPRINT / REVISED for one copy of the current version: REPRINT when this copy of this version was already printed
    successfully; REVISED when an older version of the batch was printed; else ORIGINAL (a FAILED attempt never counts, so the retry is
    still the ORIGINAL). Returns {'print_type', 'version_no', 'supersedes'}."""
    v = version or current_version(statement_id)
    if v is None:
        return {"print_type": "ORIGINAL", "version_no": 1, "supersedes": None}
    if version_printed(v["version_id"], copy):
        return {"print_type": "REPRINT", "version_no": v["version_no"], "supersedes": None}
    with _conn() as conn:
        older = conn.execute("""SELECT MAX(sv.version_no) FROM statement_prints p JOIN statement_versions sv ON sv.version_id = p.version_id
                                WHERE p.statement_id = ? AND p.status = 'SUCCESS' AND sv.version_no < ?""",
                             (int(statement_id), v["version_no"])).fetchone()[0]
    if older:
        return {"print_type": "REVISED", "version_no": v["version_no"], "supersedes": v["version_no"] - 1}
    return {"print_type": "ORIGINAL", "version_no": v["version_no"], "supersedes": None}


def planned_print(statement_id: int, copy: str) -> dict:
    """What printing this copy now would be -- read-only, for the preview (mirrors sync_version + print_type_for without writing)."""
    v = current_version(statement_id)
    if v is None:
        return {"print_type": "ORIGINAL", "version_no": 1, "supersedes": None}
    if _needs_new_version(v, content_hash(statement_id)):
        revised = batch_ever_printed(statement_id)
        return {"print_type": "REVISED" if revised else "ORIGINAL", "version_no": v["version_no"] + 1,
                "supersedes": v["version_no"] if revised else None}
    return print_type_for(statement_id, copy, v)


def record_print(statement_id: int, version: dict, copy: str, print_type: str, backend: str, status: str, error: str | None = None) -> int:
    """One row in statement_prints for one copy, plus its audit-log entry (step 4: batch, version, copy, print type, printer, SUCCESS /
    FAILED, Drive file id; the audit row carries the time). A SUCCESS of the latest version clears "Needs reprint". Returns the print id."""
    with _conn() as conn:
        cur = conn.execute("""INSERT INTO statement_prints (statement_id, version_id, copy_type, print_type, backend, status, error, drive_file_id)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                           (int(statement_id), version["version_id"], copy, print_type, backend, status, error, version.get("drive_file_id")))
        _audit(conn, "STATEMENT_PRINTED" if status == "SUCCESS" else "STATEMENT_PRINT_FAILED", statement_id,
               f"print #{cur.lastrowid}: v{version['version_no']} {copy} COPY, {print_type}, printer {backend}, {status}, "
               f"Drive file {version.get('drive_file_id') or '-'}" + (f"; {error}" if error else ""))
        if status == "SUCCESS":
            latest = conn.execute("SELECT MAX(version_no) FROM statement_versions WHERE statement_id = ?", (int(statement_id),)).fetchone()[0]
            if version["version_no"] == latest:
                conn.execute("UPDATE order_statements SET needs_reprint = 0 WHERE statement_id = ?", (int(statement_id),))
        return cur.lastrowid

