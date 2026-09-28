"""Data health check (2026-09-28, owner-approved): one read-only scan of everything on file for records that look wrong, so a mistake is caught
before it reaches a check, a report or the bank reconciliation. Owner's choices: its own page under System; four groups (Checks, Invoices and
batches, Bank vs our records, Suppliers); each finding can be opened where it is fixed, or marked "It's fine" with a note so it stops showing
(the scan itself never changes a record); a one-line reminder on Payment run.

Each finding: {'key', 'group', 'check', 'detail', 'amount', 'payee', 'page', 'state'} -- 'page' / 'state' open the record (as the global
search does). "It's fine" is remembered in duplicate_dismissals with a 'health|' key (no new table); the Possible-duplicates scan never
matches those keys.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from engines.common import get_conn
from engines.clock import manila_today

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

GROUPS = ["Checks", "Invoices and batches", "Bank vs our records", "Suppliers", "Credit cards", "Audit log"]
KEY_PREFIX = "health|"
BROWSE_BATCHES, BROWSE_LOAN_NOTES = "Browse batches", "Browse loan notes"
STALE_CHECK_DAYS = 180          # Philippine checks go stale six months after their date
CC_GUESS_MIN_DAYS = 25          # card with no statement day: dues this far apart are two statements (its statement dates are only guessed)


def _conn() -> sqlite3.Connection:
    conn = get_conn(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _check_target(r) -> tuple[str, dict]:
    if r["statement_id"]:
        return "order-statements", {"selected_statement_id": int(r["statement_id"]), "os_mode": BROWSE_BATCHES, "os_subtab": "Checks"}
    if r["loan_note_id"]:
        return "lenders", {"selected_loan_note_id": int(r["loan_note_id"]), "ln_mode": BROWSE_LOAN_NOTES, "ln_subtab": "Checks"}
    return "checks", {}


def _batch_target(statement_id) -> tuple[str, dict]:
    return "order-statements", {"selected_statement_id": int(statement_id), "os_mode": BROWSE_BATCHES}


def _f(out: list, group: str, check: str, key: str, detail: str, amount=None, payee: str = "", target: tuple[str, dict] = ("", {})) -> None:
    out.append({"key": KEY_PREFIX + key, "group": group, "check": check, "detail": detail, "amount": None if amount is None else float(amount),
                "payee": payee or "", "page": target[0], "state": target[1]})


# ------------------------------------------------------------------ the four groups
_CHECK_SQL = """SELECT c.check_id, c.check_number, c.check_date, c.amount, c.status, c.printed_at, c.statement_id, c.loan_note_id,
                       c.bank_cleared_date, cr.creditor_name
                FROM checks_issued c LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id"""


def _checks(conn, today: date, out: list) -> None:
    from engines.bank_statement_service import norm_check_number

    g = "Checks"
    rows = conn.execute(_CHECK_SQL).fetchall()
    live = [r for r in rows if r["status"] != "Cancelled"]
    by_number: dict[str, list] = {}
    for r in live:
        if r["check_number"]:
            by_number.setdefault(norm_check_number(r["check_number"]), []).append(r)
    for num, same in by_number.items():
        if num and len(same) > 1:
            _f(out, g, "Same check number used twice", f"dup_number|{num}",
               f"Check number {same[0]['check_number']} is on {len(same)} checks: "
               + "; ".join(f"{r['check_date']} · PHP {r['amount']:,.2f} · {r['creditor_name']} ({r['status']})" for r in same),
               sum(r["amount"] for r in same), same[0]["creditor_name"], _check_target(same[0]))
    bank_first: dict[str, str] = {}
    if _has_table(conn, "bank_transactions"):
        for t in conn.execute("SELECT check_number, MIN(posting_date) AS first FROM bank_transactions WHERE kind = 'CHECK' AND check_number IS NOT NULL "
                              "GROUP BY check_number"):
            n = norm_check_number(t["check_number"])
            bank_first[n] = min(bank_first.get(n, t["first"]), t["first"])
    for r in rows:
        if r["status"] != "Active":
            continue
        cd, who, tgt = _d(r["check_date"]), r["creditor_name"], _check_target(r)
        label = r["check_number"] or f"(no number, id {r['check_id']})"
        first = bank_first.get(norm_check_number(r["check_number"])) if r["check_number"] else None
        if first and cd and first < cd.isoformat() and not r["bank_cleared_date"]:      # once adjusted and cleared it is dealt with
            _f(out, g, "Bank paid this check number before our check's date", f"bank_before|{r['check_id']}",
               f"Check {label} is dated {cd} but the bank already cleared that number on {first}. Compare it with the check stub.",
               r["amount"], who, tgt)
        if r["printed_at"] and not r["check_number"]:
            _f(out, g, "Printed but no check number", f"no_number|{r['check_id']}",
               f"Printed {str(r['printed_at'])[:10]}, dated {cd}: assign the leaf number so the bank statement can match it.", r["amount"], who, tgt)
        if cd and cd < today - timedelta(days=STALE_CHECK_DAYS) and not r["bank_cleared_date"]:
            _f(out, g, "Stale check (over 6 months, not cleared)", f"stale|{r['check_id']}",
               f"Check {label} dated {cd} has not been cleared by the bank; a check goes stale six months after its date.", r["amount"], who, tgt)
        if cd and cd > today + timedelta(days=365):
            _f(out, g, "Dated more than a year ahead", f"far_ahead|{r['check_id']}", f"Check {label} is dated {cd} — a typo in the year?",
               r["amount"], who, tgt)
        if (r["amount"] or 0) <= 0:
            _f(out, g, "Zero or negative amount", f"zero_check|{r['check_id']}", f"Check {label} dated {cd} is for PHP {r['amount'] or 0:,.2f}.",
               r["amount"], who, tgt)


def _invoices_and_batches(conn, today: date, out: list) -> None:
    from engines.check_rules import get_batch_balance

    g = "Invoices and batches"
    for r in conn.execute("""SELECT i.invoice_id, i.doc_ref, i.invoice_date, i.due_date, i.amount, i.statement_id, i.paid_via_card_id, cr.creditor_name
                             FROM invoices i LEFT JOIN creditors cr ON cr.creditor_id = i.creditor_id WHERE i.status != 'Cancelled'"""):
        inv, due, who = _d(r["invoice_date"]), _d(r["due_date"]), r["creditor_name"]
        tgt = _batch_target(r["statement_id"]) if r["statement_id"] else ("order-statements", {})
        ref = r["doc_ref"] or f"invoice id {r['invoice_id']}"
        if inv and due and due < inv:
            _f(out, g, "Due date before the invoice date", f"due_before|{r['invoice_id']}", f"{ref}: invoice {inv}, due {due}.", r["amount"], who, tgt)
        odd = [f"{name} {d}" for name, d in (("invoice", inv), ("due", due)) if d and not (2020 <= d.year <= today.year + 2)]
        if odd:
            _f(out, g, "Unusual year", f"odd_year|{r['invoice_id']}", f"{ref}: " + ", ".join(odd) + " — a typo in the year?", r["amount"], who, tgt)
        if (r["amount"] or 0) <= 0:
            _f(out, g, "Zero or negative invoice", f"zero_inv|{r['invoice_id']}", f"{ref} is for PHP {r['amount'] or 0:,.2f}.", r["amount"], who, tgt)
        if not r["statement_id"] and not r["paid_via_card_id"]:
            _f(out, g, "Invoice not in any batch", f"no_batch|{r['invoice_id']}", f"{ref} dated {inv} belongs to no batch, so it is never paid.",
               r["amount"], who, tgt)
    for b in conn.execute("""SELECT os.statement_id, os.statement_ref, os.creditor_id, cr.creditor_name,
                                    (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = os.statement_id) AS n_inv,
                                    (SELECT COUNT(*) FROM checks_issued k WHERE k.statement_id = os.statement_id) AS n_chk
                             FROM order_statements os LEFT JOIN creditors cr ON cr.creditor_id = os.creditor_id WHERE os.status != 'Cancelled'"""):
        tgt, who = _batch_target(b["statement_id"]), b["creditor_name"]
        if not b["n_inv"] and not b["n_chk"]:
            _f(out, g, "Empty batch", f"empty_batch|{b['statement_id']}", f"Batch {b['statement_ref']} has no invoices and no checks.", None, who, tgt)
            continue
        net_total, active_sum, _ = get_batch_balance(int(b["statement_id"]))
        if active_sum > net_total + 0.005:
            _f(out, g, "Batch overpaid", f"overpaid|{b['statement_id']}",
               f"Batch {b['statement_ref']}: checks PHP {active_sum:,.2f} against PHP {net_total:,.2f} owed (over by PHP {active_sum - net_total:,.2f}).",
               active_sum - net_total, who, tgt)
    for k in conn.execute("""SELECT k.check_id, k.check_number, k.amount, k.statement_id, os.statement_ref, a.creditor_name AS on_check, b.creditor_name AS on_batch
                             FROM checks_issued k JOIN order_statements os ON os.statement_id = k.statement_id
                             LEFT JOIN creditors a ON a.creditor_id = k.creditor_id LEFT JOIN creditors b ON b.creditor_id = os.creditor_id
                             WHERE k.status = 'Active' AND k.creditor_id != os.creditor_id"""):
        _f(out, g, "Check payee differs from the batch supplier", f"payee_diff|{k['check_id']}",
           f"Check {k['check_number'] or '(no number)'} in batch {k['statement_ref']} is payable to {k['on_check']}, but the batch is {k['on_batch']}.",
           k["amount"], k["on_check"], _batch_target(k["statement_id"]))


def _bank(conn, today: date, out: list) -> None:
    g = "Bank vs our records"
    if not _has_table(conn, "bank_transactions"):
        return
    from engines.bank_alerts import stale_days
    from engines.bank_statement_service import not_yet_cleared

    what = {"amount_mismatch": "Bank cleared a different amount", "cancelled_check_cleared": "Bank cleared a check we cancelled",
            "cleared_twice": "Bank cleared the same check twice"}
    for t in conn.execute("""SELECT t.txn_id, t.posting_date, t.check_number, t.debit, t.match_status, c.amount, c.check_date, c.status,
                                    cr.creditor_name
                             FROM bank_transactions t LEFT JOIN checks_issued c ON c.check_id = t.matched_check_id
                             LEFT JOIN creditors cr ON cr.creditor_id = c.creditor_id
                             WHERE t.reviewed = 0 AND t.match_status IN ('amount_mismatch', 'cancelled_check_cleared', 'cleared_twice')"""):
        ours = f"; ours: {t['check_date']} · PHP {t['amount']:,.2f} · {t['status']}" if t["amount"] is not None else ""
        _f(out, g, what[t["match_status"]], f"bank_line|{t['txn_id']}",
           f"Bank {t['posting_date']} · check {t['check_number']} · PHP {t['debit']:,.2f}{ours}. Deal with it on Bank and cash.",
           t["debit"], t["creditor_name"], ("bank-cash", {}))
    limit = stale_days()
    for w in not_yet_cleared():
        if w["days_waiting"] > limit:
            _f(out, g, f"Not cleared {limit}+ days after its date", f"not_cleared|{w['check_id']}",
               f"Check {w['check_number']} dated {w['check_date']} ({w['reference']}): {w['days_waiting']} days and the bank has not cleared it.",
               w["amount"], w["creditor_name"], ("bank-cash", {}))


def _suppliers(conn, today: date, out: list) -> None:
    g = "Suppliers"
    groups: dict[str, list] = {}
    for r in conn.execute("SELECT creditor_id, creditor_name, creditor_type, is_active, terms FROM creditors"):
        groups.setdefault(" ".join(str(r["creditor_name"] or "").lower().split()), []).append(r)
    for name, same in groups.items():
        types = [r["creditor_type"] for r in same]
        if len(same) > 1 and len(set(types)) < len(types):        # a bank set up once for its loans and once for its cards is by design
            _f(out, g, "Same name twice", f"dup_name|{name}", f"{same[0]['creditor_name']} is on file {len(same)} times ("
               + ", ".join(sorted(set(t.replace('_', ' ').title() for t in types))) + ").", None, same[0]["creditor_name"],
               ("creditors", {"creditor_master_search": same[0]["creditor_name"]}))
    for r in conn.execute("""SELECT cr.creditor_id, cr.creditor_name, SUM(i.amount) AS owed, COUNT(*) AS n FROM creditors cr
                             JOIN invoices i ON i.creditor_id = cr.creditor_id
                             WHERE cr.is_active = 0 AND i.status NOT IN ('Paid', 'Cancelled') GROUP BY cr.creditor_id"""):
        _f(out, g, "Inactive but still owed", f"inactive_owed|{r['creditor_id']}",
           f"{r['creditor_name']} is marked inactive but has {r['n']} unpaid invoice(s).", r["owed"], r["creditor_name"],
           ("creditors", {"creditor_master_search": r["creditor_name"]}))
    for r in conn.execute("SELECT creditor_id, creditor_name FROM creditors WHERE is_active = 1 AND creditor_type = 'SUPPLIER' AND "
                          "(terms IS NULL OR TRIM(terms) = '')"):
        _f(out, g, "Supplier with no terms", f"no_terms|{r['creditor_id']}", f"{r['creditor_name']} has no payment terms, so due dates can't be worked out.",
           None, r["creditor_name"], ("creditors", {"creditor_master_search": r["creditor_name"]}))


AUDIT_CHECK_MAX_DAYS = 2          # the daily backup checks the audit log; warn when it hasn't for this long


def _audit_log(conn, today: date, out: list) -> None:
    """core step C2: the audit log's lock and its daily tamper check (engines/audit_seal.py, run by the daily backup)."""
    from engines.audit_seal import _missing_triggers, last_result

    g = "Audit log"
    if not _has_table(conn, "audit_seals"):
        return
    for t in _missing_triggers(conn):
        _f(out, g, "Audit log lock removed", f"audit_lock|{t}", f"The database protection {t} is missing, so audit entries could be changed. "
           "Run update_database.bat to put it back, and ask what happened.")
    res = last_result(conn)
    if res is None:
        _f(out, g, "Audit log not checked yet", "audit_never", "The daily backup checks and seals the audit log; it hasn't run since this "
           "protection was added. It runs by itself at 18:00, or run run_backup.bat now.")
        return
    checked = _d(res.get("at"))
    for i, p in enumerate(res.get("problems") or []):
        _f(out, g, "Audit log was changed outside the system", f"audit_problem|{res.get('at')}|{i}",
           f"Found by the check on {res.get('at')}: {p} New entries are not sealed until this is looked at.")
    if checked and (today - checked).days > AUDIT_CHECK_MAX_DAYS:
        _f(out, g, "Audit log not checked lately", f"audit_stale|{checked}", f"The last check was {checked} ({(today - checked).days} days ago). "
           "It runs with the daily backup -- is the laptop's backup task running?")


# ------------------------------------------------------------------ public
def _credit_cards(conn, today: date, out: list) -> None:
    """Card statements entered twice (2026-09-29, owner: "why our guardrail did not detect it?" -- a Visa Gold statement typed twice
    in MS Access came in through the Sep 19 history loader, which bypassed the Add statement form's warning, and nothing re-checked
    card statements afterwards). Cancelled statements are ignored."""
    g = "Credit cards"
    if not _has_table(conn, "cc_statements"):
        return
    label = "COALESCE(cc.card_name, '') || CASE WHEN cc.card_number_last4 IS NOT NULL THEN ' ' || cc.card_number_last4 ELSE '' END"
    for r in conn.execute(f"""SELECT s.card_id, {label} AS card, s.due_date, ROUND(s.total_amount_due, 2) AS amount, COUNT(*) AS n,
                                     GROUP_CONCAT(s.cc_statement_id) AS ids
                              FROM cc_statements s JOIN credit_cards cc ON cc.card_id = s.card_id
                              WHERE s.status != 'Cancelled'
                              GROUP BY s.card_id, s.due_date, ROUND(s.total_amount_due, 2) HAVING COUNT(*) > 1"""):
        _f(out, g, "Card statement entered twice", f"cc_dup|{r['card_id']}|{r['due_date']}|{r['amount']}",
           f"{r['card']}: {r['n']} statements due {r['due_date']} for PHP {r['amount']:,.2f} (statement ids {r['ids']}). Cancel the extra copy.",
           r["amount"], r["card"], ("credit-cards", {}))
    # Owner rule (2026-09-29): "no two statement in a month" -- by STATEMENT month (engines/cc_dates.py), so two DUE dates in one
    # calendar month (a card due near the 1st and the 30th) are fine.
    from engines.cc_dates import statement_month_for

    by_month: dict = {}
    has_day: dict = {}
    for r in conn.execute(f"""SELECT s.card_id, {label} AS card, cc.statement_day, s.due_date, s.total_amount_due
                              FROM cc_statements s JOIN credit_cards cc ON cc.card_id = s.card_id
                              WHERE s.status != 'Cancelled' AND s.due_date IS NOT NULL ORDER BY s.due_date"""):
        month = statement_month_for(r["due_date"], r["statement_day"])
        by_month.setdefault((r["card_id"], r["card"], month), []).append((r["due_date"], r["total_amount_due"]))
        has_day[r["card_id"]] = bool(r["statement_day"])
    for (card_id, card, month), items in by_month.items():
        if len(items) < 2 or len(set(items)) < 2:
            continue                                                   # one statement, or an exact duplicate (reported above)
        dues = sorted(date.fromisoformat(d) for d, _ in items)
        if not has_day[card_id] and min((b - a).days for a, b in zip(dues, dues[1:])) >= CC_GUESS_MIN_DAYS:
            continue                                                   # no statement day on file: a month apart = two statements
        what = "; ".join(f"due {d} PHP {a:,.2f}" for d, a in items)
        _f(out, g, "Two statements for the same month", f"cc_twice|{card_id}|{month}",
           f"{card}: {len(items)} statements for the {month} statement month ({what}). A card has one statement a month -- one may be "
           "typed twice, or have the wrong due date (or the card's statement day is wrong).", None, card, ("credit-cards", {}))


def findings(include_fine: bool = False, today: date | None = None) -> list[dict]:
    """Everything the scan flags, grouped in GROUPS order. Items marked "It's fine" are left out unless include_fine (then 'fine' + 'note')."""
    today = today or manila_today()
    out: list[dict] = []
    with _conn() as conn:
        for part in (_checks, _invoices_and_batches, _bank, _suppliers, _credit_cards, _audit_log):
            part(conn, today, out)
        fine = {r["dup_key"]: r["note"] for r in conn.execute("SELECT dup_key, note FROM duplicate_dismissals WHERE dup_key LIKE ?", (KEY_PREFIX + "%",))}
    for f in out:
        f["fine"], f["note"] = f["key"] in fine, fine.get(f["key"]) or ""
    out.sort(key=lambda f: (GROUPS.index(f["group"]), f["check"], f["payee"]))
    return out if include_fine else [f for f in out if not f["fine"]]


def open_count() -> int:
    """How many findings are waiting (for the Payment run reminder)."""
    return len(findings())


def mark_fine(keys: list[str], note: str = "") -> None:
    """The owner looked and it is correct: stop showing these. Audited."""
    with _conn() as conn:
        for k in keys:
            conn.execute("INSERT OR REPLACE INTO duplicate_dismissals (dup_key, note) VALUES (?, ?)", (k, note or None))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'DATA_HEALTH_FINE', 'data_health', NULL, ?)",
                         (k[len(KEY_PREFIX):] + (f": {note}" if note else ""),))


def show_again(keys: list[str]) -> None:
    """Undo "It's fine"."""
    with _conn() as conn:
        for k in keys:
            conn.execute("DELETE FROM duplicate_dismissals WHERE dup_key = ?", (k,))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'DATA_HEALTH_SHOW_AGAIN', 'data_health', NULL, ?)",
                         (k[len(KEY_PREFIX):],))
