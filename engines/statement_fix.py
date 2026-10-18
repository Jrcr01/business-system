"""Editing an invoice of a payment statement that checks already cover, in one step (owner, 2026-10-18: "i find it too complicated when editing locked
batch because i have to delete first checks to modify invoices"; his answer B: a numbered check that would now pay too much is REPLACED -- voided, and
the new check number typed for the right amount).

plan()  says, before anything changes, what an edit / a delete / a take-out does to the checks:
        the checks may never pay more than the statement owes, so the excess comes off the checks --
          * a parked check (no number yet) is lowered, or removed when nothing is left for it;
          * a numbered check is replaced: voided, and a new check for the right amount under the number the person types (or simply voided when
            nothing is left for it);
          * a check the bank already cleared never changes: then the edit is refused (a credit memo is the way).
apply() does it all in one go: reopens a closed statement (with the reason), changes the checks, rebuilds which check pays which invoice, makes the
        invoice change, and the statement closes again by itself when its checks cover it. Every step is in the audit log."""
from __future__ import annotations

from engines.common import get_conn, write_tx

EPS = 0.005


def _php(v: float) -> str:
    return f"₱{v:,.2f}"


def _checks(conn, sid: int) -> list[dict]:
    rows = conn.execute("""SELECT check_id, check_number, amount, check_date, bank_account_id, creditor_id, bank_cleared_date, manual_cleared_at,
                                  settled_outside_at, released_date, printed_at
                           FROM checks_issued WHERE statement_id = ? AND status = 'Active' ORDER BY check_date DESC, check_id DESC""", (sid,)).fetchall()
    keys = ("check_id", "number", "amount", "date", "bank", "creditor", "bank_cleared", "manual_cleared", "settled", "released", "printed")
    out = [dict(zip(keys, r)) for r in rows]
    for c in out:
        c["cleared"] = bool(c["bank_cleared"] or c["manual_cleared"] or c["settled"])
    return out


def plan(sid: int, invoice_id: int, mode: str, new_net: float | None = None) -> dict:
    """mode: 'edit' (new_net = the invoice's net after the edit), 'delete' or 'take_out'. Returns {'ok', 'msg', 'excess', 'steps': [...],
    'needs_number': [check_id, ...]}; each step: {'kind': lower|remove|replace|void, 'check_id', 'number', 'old', 'new'}."""
    from engines.statement_rules import get_batch_balance
    with get_conn() as conn:
        inv = conn.execute("SELECT amount, statement_id, doc_ref FROM invoices WHERE invoice_id = ?", (int(invoice_id),)).fetchone()
        if not inv or inv[1] != int(sid):
            return {"ok": False, "msg": "That invoice is not in this payment statement.", "steps": [], "needs_number": [], "excess": 0.0}
        old = float(inv[0] or 0)
        after = old if mode == "edit" and new_net is None else (float(new_net) if mode == "edit" else 0.0)
        net, active, _ = get_batch_balance(int(sid))
        excess = round(active - (net - old + after), 2)
        steps, needs = [], []
        if excess > EPS:
            left = excess
            checks = _checks(conn, int(sid))
            for c in [c for c in checks if not c["number"] and not c["printed"] and not c["cleared"]] + [c for c in checks if (c["number"] or c["printed"]) and not c["cleared"]]:
                if left <= EPS:
                    break
                cut = min(float(c["amount"]), left)
                new = round(float(c["amount"]) - cut, 2)
                parked = not c["number"] and not c["printed"]
                kind = ("remove" if new <= EPS else "lower") if parked else ("void" if new <= EPS else "replace")
                steps.append({"kind": kind, "check_id": c["check_id"], "number": c["number"], "old": float(c["amount"]), "new": max(new, 0.0),
                              "date": c["date"], "released": bool(c["released"])})
                if kind == "replace":
                    needs.append(c["check_id"])
                left = round(left - cut, 2)
            if left > EPS:
                return {"ok": False, "excess": excess, "steps": steps, "needs_number": needs,
                        "msg": f"A check the bank already cleared pays this, so the checks would pay {_php(left)} more than is owed. "
                               f"Record a credit memo for {inv[2]} instead."}
    return {"ok": True, "msg": "", "excess": max(excess, 0.0), "steps": steps, "needs_number": needs}


def describe(p: dict) -> list[str]:
    """The plan in plain words, for the confirmation box."""
    out = []
    for s in p["steps"]:
        name = f"check {s['number']}" if s["number"] else f"the parked check of {_php(s['old'])}"
        if s["kind"] == "lower":
            out.append(f"{name.capitalize()} is lowered from {_php(s['old'])} to {_php(s['new'])}.")
        elif s["kind"] == "remove":
            out.append(f"{name.capitalize()} is removed: nothing is left for it to pay.")
        elif s["kind"] == "void":
            out.append(f"{name.capitalize()} ({_php(s['old'])}) is voided: nothing is left for it to pay." + (" Get the paper check back from the supplier." if s["released"] else ""))
        else:
            out.append(f"{name.capitalize()} ({_php(s['old'])}) is voided and replaced by a new check for {_php(s['new'])}: type the new check's number."
                       + (" Get the old paper check back from the supplier." if s["released"] else ""))
    return out


def _tracks(cur, sid: int) -> bool:
    return bool(cur.execute("""SELECT 1 FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id WHERE c.statement_id = ? LIMIT 1""",
                            (sid,)).fetchone())


def _rebuild_allocations(cur, sid: int, tracks: bool) -> None:
    """Which check pays which invoice, again: the checks oldest first against the invoices due first (only for a statement that tracks it)."""
    if not tracks:
        return
    cur.execute("DELETE FROM invoice_check_allocations WHERE check_id IN (SELECT check_id FROM checks_issued WHERE statement_id = ?)", (sid,))
    invs = [[iid, float(a or 0)] for iid, a in cur.execute("""SELECT invoice_id, amount FROM invoices WHERE statement_id = ? AND status != 'Cancelled'
                                                              ORDER BY due_date, invoice_id""", (sid,)).fetchall()]
    k = 0
    for cid, amt in cur.execute("SELECT check_id, amount FROM checks_issued WHERE statement_id = ? AND status = 'Active' ORDER BY check_date, check_id", (sid,)).fetchall():
        left = float(amt or 0)
        while left > EPS and k < len(invs):
            take = round(min(left, invs[k][1]), 2)
            if take > EPS:
                cur.execute("INSERT INTO invoice_check_allocations (invoice_id, check_id, amount) VALUES (?, ?, ?)", (invs[k][0], cid, take))
            invs[k][1] = round(invs[k][1] - take, 2)
            left = round(left - take, 2)
            if invs[k][1] <= EPS:
                k += 1


def apply(sid: int, invoice_id: int, mode: str, reason: str, numbers: dict, change, planned: dict) -> tuple[bool, str]:
    """Does what plan() said, then change() -- the invoice change itself (edit_invoice / delete_invoice / take_out_of_batch, already checked by the
    caller so it cannot fail on its inputs) -- in one go. numbers: {check_id: the new check's number} for every check to replace. A check that changed
    meanwhile, or a number already used, stops everything before a check is touched."""
    from engines.check_rules import _is_check_number_available
    from engines.permissions import require
    from engines.statement_rules import _resync_lock, recompute_statement_total, sync_invoice_payment_status, unlock_statement
    reason = " ".join(str(reason or "").split())
    if not planned["ok"]:
        return False, planned["msg"]
    with get_conn() as conn:
        locked, status = conn.execute("SELECT is_locked, status FROM order_statements WHERE statement_id = ?", (int(sid),)).fetchone()
    if status == "Cancelled":
        return False, "This payment statement is cancelled."
    if locked and not reason:
        return False, "Say why the closed payment statement is changed."
    if planned["steps"]:
        ok, why = require("check.void")
        if not ok:
            return False, why
    for cid in planned["needs_number"]:
        if not str(numbers.get(cid) or "").strip():
            return False, "Type the new check's number for every check that is replaced."
    done = []
    with get_conn() as conn:
        tracks = _tracks(conn.cursor(), int(sid))
    try:
        with write_tx() as conn:
            cur = conn.cursor()
            for s in planned["steps"]:                           # everything is checked before anything is written
                c = cur.execute("SELECT status, amount, bank_account_id FROM checks_issued WHERE check_id = ?", (s["check_id"],)).fetchone()
                if not c or c[0] != "Active" or abs(float(c[1]) - s["old"]) > EPS:
                    raise ValueError("A check changed while you were editing. Nothing was saved: open the payment statement again.")
                if s["kind"] == "replace" and not _is_check_number_available(cur, c[2], " ".join(str(numbers[s["check_id"]]).split())):
                    raise ValueError(f"Check number {numbers[s['check_id']]} is already used. Nothing was saved: type another number.")
    except ValueError as e:
        return False, str(e)
    if locked:
        ok, msg = unlock_statement(int(sid), f"Payment statement reopened to edit: {reason}")
        if not ok:
            return False, msg
    with write_tx() as conn:
        cur = conn.cursor()
        for s in planned["steps"]:
            c = cur.execute("SELECT bank_account_id, creditor_id, check_date FROM checks_issued WHERE check_id = ?", (s["check_id"],)).fetchone()
            if s["kind"] == "lower":
                cur.execute("UPDATE checks_issued SET amount = ? WHERE check_id = ?", (s["new"], s["check_id"]))
                cur.execute("INSERT INTO check_corrections (check_id, statement_id, correction_type, old_value, new_value, reason) VALUES (?, ?, 'Amount', ?, ?, ?)",
                            (s["check_id"], int(sid), str(s["old"]), str(s["new"]), f"Lowered with the invoice change: {reason or 'invoice changed'}"))
                done.append(f"parked check lowered {_php(s['old'])} → {_php(s['new'])}")
            elif s["kind"] == "remove":
                cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (s["check_id"],))
                cur.execute("DELETE FROM check_corrections WHERE check_id = ?", (s["check_id"],))
                cur.execute("DELETE FROM checks_issued WHERE check_id = ?", (s["check_id"],))
                done.append(f"parked check {_php(s['old'])} removed")
            elif s["kind"] == "void":
                cur.execute("UPDATE checks_issued SET status = 'Cancelled', cancelled_at = CURRENT_TIMESTAMP, cancel_reason = ? WHERE check_id = ?",
                            (f"nothing left to pay after the invoice change: {reason or 'invoice changed'}", s["check_id"]))
                cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (s["check_id"],))
                done.append(f"check {s['number'] or s['check_id']} voided")
            else:
                number = " ".join(str(numbers[s["check_id"]]).split())
                new_id = cur.execute("""INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status, is_handwritten)
                                        VALUES (?, ?, ?, ?, ?, ?, 'Active', 1) RETURNING check_id""", (c[1], c[0], int(sid), number, c[2], s["new"])).fetchone()[0]
                cur.execute("""UPDATE checks_issued SET status = 'Replaced', replacement_check_id = ?, replacement_reason = ?, replaced_at = CURRENT_TIMESTAMP
                               WHERE check_id = ?""", (new_id, f"amount changed with the invoice change: {reason or 'invoice changed'}", s["check_id"]))
                cur.execute("UPDATE invoice_check_allocations SET check_id = ? WHERE check_id = ?", (new_id, s["check_id"]))
                done.append(f"check {s['number']} ({_php(s['old'])}) replaced by check {number} ({_php(s['new'])})")
            cur.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_FITTED_TO_INVOICES', 'checks_issued', ?, ?)",
                        (s["check_id"], done[-1] + (f". Why: {reason}" if reason else "")))
        cur.execute("DELETE FROM invoice_check_allocations WHERE invoice_id = ?", (int(invoice_id),))    # rebuilt below, after the change
    ok, msg = change()
    with write_tx() as conn:
        _rebuild_allocations(conn.cursor(), int(sid), tracks)
    recompute_statement_total(int(sid))
    sync_invoice_payment_status(int(sid))
    _resync_lock(int(sid))
    if not ok:
        return False, msg + (" The checks were already fitted: " + "; ".join(done) + "." if done else "")
    return True, msg + (" Checks: " + "; ".join(done) + "." if done else "")
