"""Statement versions and their public A4 copy (Thermal Statement Printing, build step 2, 2026-09-27).

A batch's statement has versions (statement_versions, migration 048). Each version's A4 PDF is uploaded to Drive ONCE and shared
anyone-with-link; the slip's QR encodes that link, and a reprint of the same version reuses it. Printing a newer version switches the
older versions' sharing off (revoke_older_versions) -- their files stay, privately. Deciding WHEN a new version starts (a batch edited
after an unlock and re-locked -> "Needs reprint") is build step 3; this module keeps the fingerprint (content_hash) it will compare.

Any failure here raises, and the caller must abort the print: no slip without a working QR.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime

from engines.batch_statement_service import _fetch_batch_data, generate_batch_statement_pdf
from engines.drive_service import DriveError, statement_folder_id

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def content_hash(statement_id: int) -> str | None:
    """Fingerprint of what the statement shows (every invoice, credit memo and check with their amounts, dates and statuses). Two
    moments with the same fingerprint have the same statement; a different one means the content changed."""
    data = _fetch_batch_data(statement_id, True, True)
    if data is None:
        return None
    s = data["statement"]
    payload = {
        "batch": [s["statement_ref"], s["creditor_name"]],
        "invoices": [[i["doc_ref"], i["invoice_date"], i["due_date"], i["amount"], i["status"]] for i in data["invoices"]],
        "memos": [[m["memo_ref"], m["memo_date"], m["amount"], m["reason"]] for m in data["memos"]],
        "checks": [[c["check_number"], c["check_date"], c["amount"], c["status"]] for c in data["checks"]],
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


def ensure_published(statement_id: int, drive, sandbox: bool = False) -> dict:
    """The current version with a working public link: uploaded + shared now if it hasn't been yet, otherwise reused as it is (a new
    upload never replaces an existing file). Raises DriveError / DriveNotConnected on any failure -- nothing is recorded as uploaded then."""
    v = ensure_current_version(statement_id)
    if v["drive_file_id"] and v["drive_public"]:
        return v
    with _conn() as conn:
        ref = conn.execute("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (int(statement_id),)).fetchone()[0]
    pdf = generate_batch_statement_pdf(int(statement_id), True, "supplier", False)
    if not pdf:
        raise DriveError("Could not make the A4 statement PDF for this batch.")
    name = f"{ref} - statement v{v['version_no']} - {datetime.now():%Y%m%d-%H%M%S}.pdf"
    folder_id = statement_folder_id(drive, sandbox)
    file_id, link = drive.upload_pdf(pdf, name, folder_id)
    drive.share_public(file_id)                                        # only after this succeeds is the version recorded as published
    with _conn() as conn:
        conn.execute("""UPDATE statement_versions SET pdf_file_name = ?, drive_file_id = ?, drive_link = ?, drive_public = 1,
                        uploaded_at = CURRENT_TIMESTAMP WHERE version_id = ?""", (name, file_id, link, v["version_id"]))
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
