"""Supplier statement check (owner, 2026-10-13: "our suppliers give different formats", "mostly photos and paper", "the common field are document
reference usually delivery receipt, date, amount"). Nothing is uploaded or read: the page lists what OUR records say we owe a supplier, the person
goes down the supplier's paper ticking what is on it (changing the amount where theirs differs), and types only the lines we do not have.

  * our_lines(creditor_id)   -- every delivery not yet paid by a cleared check, oldest first
  * compare(...)             -- five groups: matches, same number different amount, on theirs not recorded, on theirs already paid, ours not on theirs
  * save / saved / history   -- a check kept with its date, so next month shows what was settled

It only compares. No invoice, batch or check is changed here."""
from __future__ import annotations

import json

from engines.common import get_conn, write_tx

GROUPS = (("match", "Matches", "nothing"),
          ("amount", "Same number, different amount", "check the DR paper; correct yours or ask them to correct theirs"),
          ("missing", "On theirs, not recorded by you", "if it was delivered, record it on Receive"),
          ("paid", "On theirs, already paid by you", "send them the check number and the date it cleared"),
          ("ours", "In your records, not on theirs", "they may not have billed it yet, or it is on another statement"))


def _clean(ref) -> str:
    return "".join(str(ref or "").upper().split())


def _number(text) -> float | None:
    raw = str(text if text is not None else "").replace(",", "").replace("₱", "").strip()
    if raw == "":
        return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _checks(conn, statement_id) -> list[dict]:
    """The checks of the batch an invoice is in: number, date, amount, cleared (the bank's date, or the day it was ticked or settled outside)."""
    if not statement_id:
        return []
    rows = conn.execute("""SELECT check_number, check_date, amount, COALESCE(bank_cleared_date, date(manual_cleared_at), date(settled_outside_at))
                           FROM checks_issued WHERE statement_id = ? AND status = 'Active' ORDER BY check_date, check_id""", (int(statement_id),)).fetchall()
    return [{"number": n or "no number yet", "date": str(d or "")[:10], "amount": float(a or 0), "cleared": str(c)[:10] if c else ""} for n, d, a, c in rows]


def _mdy(iso: str) -> str:
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if len(iso) >= 10 else iso


def _waiting_note(waiting: list[dict]) -> str:
    """Checks written for the line's batch that the bank has not cleared: one says its number and date, several say how many and the first date."""
    if not waiting:
        return ""
    if len(waiting) == 1:
        return f"check {waiting[0]['number']} dated {_mdy(waiting[0]['date'])}, not cleared yet"
    return f"its batch has {len(waiting)} checks not cleared yet, the first dated {_mdy(waiting[0]['date'])}"


def _shown_ref(doc_ref, dr_no) -> str:
    return f"{dr_no} · SI {doc_ref}" if dr_no and _clean(dr_no) != _clean(doc_ref) else str(doc_ref)


def our_lines(creditor_id: int) -> list[dict]:
    """What our records say we owe this supplier: invoice_id, date, ref (the DR no., with the SI no. when it differs), refs (what a typed number may
    match), amount, checks (dated but not cleared: shown beside the line)."""
    with get_conn() as conn:
        rows = conn.execute("""SELECT invoice_id, invoice_date, doc_ref, dr_no, amount, status, statement_id FROM invoices
                               WHERE creditor_id = ? AND status IN ('Unpaid', 'Partially Paid') AND is_opening_balance = 0 AND paid_via_card_id IS NULL
                               ORDER BY invoice_date, invoice_id""", (int(creditor_id),)).fetchall()
        out = []
        for iid, d, doc_ref, dr_no, amount, status, sid in rows:
            waiting = [c for c in _checks(conn, sid) if not c["cleared"]]
            out.append({"invoice_id": int(iid), "date": str(d)[:10], "ref": _shown_ref(doc_ref, dr_no), "refs": sorted({_clean(doc_ref), _clean(dr_no)} - {""}),
                        "amount": round(float(amount or 0), 2), "partly": status == "Partially Paid",
                        "note": _waiting_note(waiting)})
    return out


def _find(conn, creditor_id: int, ref: str):
    """An invoice of this supplier by the number on their paper: the SI no. or the DR no., spaces and capitals ignored."""
    key = _clean(ref)
    if not key:
        return None
    for row in conn.execute("""SELECT invoice_id, invoice_date, doc_ref, dr_no, amount, status, statement_id FROM invoices
                               WHERE creditor_id = ? AND is_opening_balance = 0 AND status != 'Cancelled'""", (int(creditor_id),)):
        if key in (_clean(row[2]), _clean(row[3])):
            return row
    return None


def compare(creditor_id: int, ticked: dict, typed: list, their_total=None) -> dict:
    """ticked: {invoice_id: their amount (None = the same as ours)}; typed: [(date, ref, amount)] for lines on their paper we did not list.
    Returns {'groups': {key: [line]}, 'summary': [...], 'their_total', 'found', 'left', 'ours_total'}. A typed number that is one of our open
    lines counts as that line ticked; one we have as paid goes under 'paid' with its check; one we do not have at all under 'missing'."""
    ours = our_lines(creditor_id)
    by_id = {r["invoice_id"]: r for r in ours}
    seen: dict[int, float] = {}
    groups: dict[str, list] = {k: [] for k, _, _ in GROUPS}
    for iid, amount in ticked.items():
        if int(iid) in by_id:
            seen[int(iid)] = by_id[int(iid)]["amount"] if amount is None else round(float(amount), 2)
    with get_conn() as conn:
        for d, ref, amount in typed:
            amount = _number(amount)
            if not _clean(ref) or amount is None:
                continue
            row = _find(conn, creditor_id, ref)
            if row is None:
                groups["missing"].append({"date": str(d or ""), "ref": str(ref).strip(), "theirs": amount, "ours": None, "diff": amount, "note": "not in your records"})
            elif int(row[0]) in by_id:
                seen[int(row[0])] = amount                    # typed, though it was on the list above: the same as ticking it
            else:                                             # we have it, and it is paid
                cleared = [c for c in _checks(conn, row[6]) if c["cleared"]]
                note = "; ".join(f"check {c['number']} cleared {_mdy(c['cleared'])}" for c in cleared[:3]) or "marked paid in your records"
                groups["paid"].append({"date": str(row[1])[:10], "ref": _shown_ref(row[2], row[3]), "theirs": amount, "ours": round(float(row[4] or 0), 2), "diff": amount, "note": note})
    for r in ours:
        if r["invoice_id"] in seen:
            theirs = seen[r["invoice_id"]]
            diff = round(theirs - r["amount"], 2)
            line = {"date": r["date"], "ref": r["ref"], "theirs": theirs, "ours": r["amount"], "diff": diff, "note": r["note"]}
            if abs(diff) < 0.005:
                groups["match"].append(line)
            else:
                line["note"] = f"theirs is {abs(diff):,.2f} {'more' if diff > 0 else 'less'}" + (f"; {r['note']}" if r["note"] else "")
                groups["amount"].append(line)
        else:
            groups["ours"].append({"date": r["date"], "ref": r["ref"], "theirs": None, "ours": r["amount"], "diff": -r["amount"], "note": r["note"]})
    found = round(sum(x["theirs"] for k in ("match", "amount", "missing", "paid") for x in groups[k]), 2)
    total = _number(their_total)
    summary = []
    for key, label, todo in GROUPS:
        rows = groups[key]
        amount = sum((x["diff"] if key == "amount" else (x["ours"] if key == "ours" else x["theirs"])) for x in rows)
        summary.append({"key": key, "label": label, "todo": todo, "lines": len(rows), "amount": round(amount, 2)})
    return {"groups": groups, "summary": summary, "their_total": total, "found": found, "left": None if total is None else round(total - found, 2),
            "ours_total": round(sum(r["amount"] for r in ours), 2)}


def save(creditor_id: int, statement_date: str, result: dict) -> tuple[bool, str, int | None]:
    """Keep this check as it stands now (a copy of the five groups): nothing in the payables changes."""
    lines =sum(s["lines"] for s in result["summary"] if s["key"] != "ours")
    if not lines:
        return False, "Tick or type at least one line from their paper first.", None
    with write_tx() as conn:
        name = conn.execute("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (int(creditor_id),)).fetchone()
        if not name:
            return False, "Supplier not found.", None
        cid = conn.execute("""INSERT INTO supplier_statement_checks (creditor_id, statement_date, their_total, found_total, ours_total, result, saved_by)
                              VALUES (?, ?, ?, ?, ?, ?, acting_user()) RETURNING check_id""",
                           (int(creditor_id), str(statement_date or "")[:10] or None, result["their_total"], result["found"], result["ours_total"],
                            json.dumps({"groups": result["groups"], "summary": result["summary"], "left": result["left"]}))).fetchone()[0]
        differ = sum(s["lines"] for s in result["summary"] if s["key"] in ("amount", "missing", "paid", "ours"))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SUPPLIER_STATEMENT_CHECKED', 'creditors', ?, ?)",
                     (int(creditor_id), f"{name[0]}: statement dated {statement_date or 'not dated'}, {lines} line(s) on their paper, {differ} to look at"))
    return True, "Saved. Nothing in your records was changed.", int(cid)


def saved(check_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""SELECT k.check_id, k.creditor_id, c.creditor_name, k.statement_date, k.their_total, k.found_total, k.ours_total, k.result, k.saved_by,
                                     datetime(k.saved_at, '+8 hours') FROM supplier_statement_checks k JOIN creditors c ON c.creditor_id = k.creditor_id
                              WHERE k.check_id = ?""", (int(check_id),)).fetchone()
    if not row:
        return None
    out = dict(zip(("check_id", "creditor_id", "supplier", "statement_date", "their_total", "found", "ours_total", "result", "saved_by", "saved_at"), row))
    out.update(json.loads(out.pop("result")))
    return out


def history(creditor_id: int | None = None, limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(f"""SELECT k.check_id, c.creditor_name, k.statement_date, k.their_total, k.ours_total, k.saved_by, datetime(k.saved_at, '+8 hours')
                                FROM supplier_statement_checks k JOIN creditors c ON c.creditor_id = k.creditor_id
                                {'WHERE k.creditor_id = ?' if creditor_id else ''} ORDER BY k.check_id DESC LIMIT ?""",
                            ((int(creditor_id),) if creditor_id else ()) + (int(limit),)).fetchall()
    return [dict(zip(("check_id", "supplier", "statement_date", "their_total", "ours_total", "saved_by", "saved_at"), r)) for r in rows]
