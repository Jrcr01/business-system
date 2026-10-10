"""Bank -> Bank and cash in Django (Phase 3, owner 2026-10-08; mock-up approved): import the bank statement (checked before anything is saved), the
bank lines that need a look, the reconciliation statement, and the check booklets. The rules are engines/bank_statement_service.py (the bank is the final
word; a difference is a tagged adjustment, never an edit of the book), engines/bank_recon_report.py, engines/check_rules.py and engines/checkbook_service.py."""
from __future__ import annotations

import secrets
import time
from pathlib import Path

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date
from masters.views import page_needed

KINDS_SHOWN = {"CHECK": "Checks", "DEPOSIT": "Deposits", "DEBIT_MEMO": "Bank debits", "BILLS_PAYMENT": "Bills payments", "RETURNED_CHECK": "Returned checks", "OTHER": "Other"}
STATUS_TEXT = {"amount_mismatch": "amount differs", "not_in_system": "not in the system", "cancelled_check_cleared": "a voided check cleared",
               "cleared_twice": "cleared twice", "returned_check": "customer check bounced", "unmatched_debit": "bank debit, no match",
               "guarantee_check_presented": "guarantee check presented"}


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _folder() -> Path:
    import engines.db_engine as d
    return Path(d.DB_PATH).resolve().parent / "web_uploads"


def _keep(data: bytes, name: str) -> str:
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("bank_*"):                         # a file checked but never imported: gone after a day
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    ext = Path(name).suffix.lower()[:6]
    (folder / f"bank_{token}{ext}").write_bytes(data)
    return f"{token}{ext}"


def _kept(token: str) -> Path | None:
    stem, _, ext = (token or "").partition(".")
    if not stem.isalnum():
        return None
    p = _folder() / f"bank_{stem}.{ext}" if ext else _folder() / f"bank_{stem}"
    return p if p.exists() else None


def _preview(data: bytes, filename: str) -> dict:
    """What importing this file would do, nothing saved: the period, lines new vs already on file, what clears, what will need a look, and the
    capture check (any failure blocks the import)."""
    from engines import bank_statement_service as bs
    from engines.common import get_conn
    parsed = bs.parse_statement(data, filename)
    checks = bs.capture_check(parsed, bs.known_account_last4(1))
    with get_conn() as conn:
        have = {r[0] for r in conn.execute("SELECT line_key FROM bank_transactions")}
        ticked = conn.execute("""SELECT check_number FROM checks_issued WHERE status = 'Active' AND manual_cleared_at IS NOT NULL AND bank_cleared_date IS NULL
                                 AND manual_cleared_at BETWEEN ? AND ?""", (str(parsed.period_from or ""), str(parsed.period_to or ""))).fetchall()
    new = [x for x in parsed.lines if bs.line_key(1, x) not in have]
    on_bank = {bs.norm_check_number(x.check_number) for x in parsed.lines if x.kind == "CHECK"}
    kinds = {}
    for x in new:
        kinds[KINDS_SHOWN.get(x.kind, x.kind)] = kinds.get(KINDS_SHOWN.get(x.kind, x.kind), 0) + 1
    return {"period_from": parsed.period_from, "period_to": parsed.period_to, "lines": len(parsed.lines), "new": len(new), "already": len(parsed.lines) - len(new),
            "kinds": kinds, "match": bs.preview_matches(parsed), "capture": checks, "ok": all(c["ok"] for c in checks),
            "closing": parsed.closing_balance, "account": parsed.masked_account(),
            "ticked_missing": sum(1 for (n,) in ticked if n and bs.norm_check_number(n) not in on_bank)}


@page_needed("bank-cash")
def bank(request):
    from engines import bank_statement_service as bs
    if request.method == "POST":
        return _post(request)
    tab = request.GET.get("tab", "import")
    first, last = bs.statement_coverage()
    ctx = {"page_id": "bank-cash", "tab": tab, "first": first, "last": last, "last_import": bs.last_import(),
           "n_look": len([e for e in bs.exceptions() if not e["reviewed"]]) + _ticks_open()}
    if tab == "import":
        token = request.GET.get("file", "")
        path = _kept(token)
        if path:
            ctx.update({"file": token, "file_name": request.session.get("bank_file_name", path.name), "pv": _preview(path.read_bytes(), path.name)})
        ctx["imports"] = bs.imports_status()[:12]
    elif tab == "look":
        rows = [e for e in bs.exceptions() if not e["reviewed"]]
        sugg = {}
        for s in bs.suggestions():
            sugg.setdefault(s["txn_id"], []).append(s)
        for r in rows:
            r["what"] = STATUS_TEXT.get(r["match_status"], r["match_status"])
            r["suggest"] = sugg.get(r["txn_id"], [])[:2]
            r["fix"] = bs.bank_correction(r["txn_id"]) if r["match_status"] == "amount_mismatch" else None
            r["amount"] = float(r["debit"] or 0) or float(r["credit"] or 0)
        ctx.update({"rows": rows, "resolutions": bs.RESOLUTIONS, "ticks": _ticks(), "resolved": bs.resolved_lines()[:30],
                    "link_for": int(request.GET.get("link") or 0),
                    "candidates": bs.link_candidates(int(request.GET.get("link")), 25) if request.GET.get("link") else []})
    elif tab == "recon":
        imports = bs.imports_status()
        ctx["imports"] = imports
        pick = int(request.GET.get("import") or (imports[0]["import_id"] if imports else 0))
        if pick:
            from engines.bank_recon_report import recon_statement
            r = recon_statement(pick)
            if r:
                ctx.update({"pick": pick, "r": r, "outstanding": r["outstanding"].to_dict("records"), "bank_items": r["bank_items"].to_dict("records")})
    elif tab == "booklets":                                      # the booklets have their own page now (owner, 2026-10-11)
        return redirect("/booklets/" + ("?voided=1#voided" if request.GET.get("voided") else ""))
    elif tab == "booklets_old":
        from engines.check_rules import get_booklets
        from engines.checkbook_service import unrecorded_alerts
        gaps = {int(a["booklet_id"]): a for a in unrecorded_alerts().to_dict("records")}
        books = get_booklets(1, ("Active", "Exhausted")).to_dict("records")
        from engines.common import get_conn
        with get_conn() as conn:
            for b in books:
                b["used"] = conn.execute("SELECT COUNT(*) FROM checks_issued WHERE booklet_id = ?", (b["booklet_id"],)).fetchone()[0]
                b["gap"] = gaps.get(int(b["booklet_id"]))
        from engines.voided_checks_service import voided_checks
        ctx.update({"books": books, "voided": voided_checks().head(30).to_dict("records") if request.GET.get("voided") else None})
    return render(request, "payables/bank.html", ctx)


def _ticks() -> list[dict]:
    from engines.common import get_conn
    with get_conn() as conn:
        conn.row_factory = __import__("sqlite3").Row
        return [dict(r) for r in conn.execute(
            """SELECT f.flag_id, f.ref_id AS check_id, f.detail, c.check_number, c.amount, c.manual_cleared_at, cr.creditor_name
               FROM review_flags f JOIN checks_issued c ON c.check_id = f.ref_id JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE f.kind = 'CLEARED_NOT_ON_BANK' AND f.reviewed_at IS NULL ORDER BY f.flag_id""")]


def _ticks_open() -> int:
    from engines.common import get_conn
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM review_flags WHERE kind = 'CLEARED_NOT_ON_BANK' AND reviewed_at IS NULL").fetchone()[0]


def _post(request):
    from engines import bank_statement_service as bs
    from engines.permissions import can
    p, what = request.POST, request.POST.get("action", "")
    role = request.slicks_user["role"]
    if not can(role, "bank.use"):
        _say(request, False, "Bank work is switched off for you.")
        return redirect("/bank-cash/")
    if what == "check_file":
        f = request.FILES.get("file")
        if not f or not f.name.lower().endswith((".pdf", ".xlsx", ".xls", ".csv")):
            _say(request, False, "Choose the bank's statement file (PDF, Excel or CSV).")
            return redirect("/bank-cash/")
        if f.size > 20 * 1024 * 1024:
            _say(request, False, "The file is larger than 20 MB. Is it the right file?")
            return redirect("/bank-cash/")
        token = _keep(f.read(), f.name)
        request.session["bank_file_name"] = f.name
        return redirect(f"/bank-cash/?file={token}")
    if what == "import_file":
        path = _kept(p.get("file", ""))
        if not path:
            _say(request, False, "The checked file is gone (older than a day?). Check it again.")
            return redirect("/bank-cash/")
        try:
            res = bs.import_statement(path.read_bytes(), request.session.get("bank_file_name", path.name))
        except Exception as e:                                  # noqa: BLE001 -- the capture check failing inside the save: nothing kept
            _say(request, False, f"Not imported: {e}")
            return redirect("/bank-cash/")
        if res.get("already_imported") or res.get("blocked"):
            _say(request, False, " ".join(res.get("messages", [])) or "Not imported.")
            return redirect("/bank-cash/")
        path.unlink(missing_ok=True)
        _say(request, True, f"Imported: {res['new_lines']} new line(s), {res['duplicate_lines']} already on file. {res['matched']} check(s) cleared; "
                            f"{res['exceptions']} line(s) need a look.")
        return redirect("/bank-cash/?tab=look" if res["exceptions"] else "/bank-cash/")
    txn = int(p.get("txn_id") or 0)
    back = "/bank-cash/?tab=look"
    if what == "adjust":
        _say(request, *bs.adjust_check_to_bank(txn))
    elif what == "link":
        _say(request, *bs.link_to_check(txn, int(p.get("check_id") or 0)))
    elif what == "accept":                                     # a suggestion: "yes, that one"
        _say(request, *bs.link_to_check(txn, int(p.get("check_id") or 0), how="Linked by hand (suggestion)"))
    elif what == "reject":
        bs.reject_suggestion(txn, int(p.get("check_id") or 0))
        _say(request, True, "That suggestion won't be shown again for this line.")
    elif what == "tag":
        res = p.get("resolution", "")
        if res not in bs.RESOLUTIONS:
            _say(request, False, "Pick what this line is.")
        else:
            bs.resolve(txn, res, p.get("note", "").strip())
            _say(request, True, f"Tagged: {res}.")
    elif what == "reopen":
        bs.reopen(txn)
        _say(request, True, "Reopened: the line needs a look again, and any check it cleared is owed again.")
    elif what in ("tick_undo", "tick_keep"):
        from engines import clearing
        from engines.common import write_tx
        flag_id, check_id = int(p.get("flag_id") or 0), int(p.get("check_id") or 0)
        if what == "tick_undo":
            ok, msg = clearing.unmark_cleared(check_id)
        else:
            ok, msg = True, "Kept as cleared (noted)."
        if ok:
            with write_tx() as conn:
                conn.execute("UPDATE review_flags SET reviewed_at = CURRENT_TIMESTAMP, reviewed_by = acting_user() WHERE flag_id = ?", (flag_id,))
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CLEARED_TICK_REVIEWED', "
                             "'checks_issued', ?, ?)", (check_id, "tick taken back" if what == "tick_undo" else "still cleared: confirmed although not on the bank statement"))
        _say(request, ok, msg)
    elif what == "new_booklet":
        from engines.check_rules import create_booklet
        try:
            start, end = int(p.get("start", "")), int(p.get("end", ""))
        except ValueError:
            _say(request, False, "Type the first and last leaf numbers.")
            return redirect("/bank-cash/?tab=booklets")
        _say(request, *create_booklet(1, p.get("code", "").strip() or f"B-{start}", start, end, _date(p.get("received")) or __import__("datetime").date.today()))
        return redirect("/bank-cash/?tab=booklets")
    elif what == "spoiled":
        from engines.check_rules import log_spoiled_check
        nums = [n.strip() for n in p.get("numbers", "").replace(";", ",").split(",") if n.strip()]
        errors = []
        for n in nums:
            ok, msg = log_spoiled_check(n, p.get("reason", "").strip() or "Spoiled leaf")
            if not ok:
                errors.append(msg)
        _say(request, not errors and bool(nums), f"{len(nums) - len(errors)} logged as spoiled." + (" " + "; ".join(errors) if errors else ""))
        return redirect("/bank-cash/?tab=booklets")
    else:
        _say(request, False, "Unknown action.")
    return redirect(back)


@page_needed("bank-cash")
def recon_pdf(request, import_id: int):
    from engines.bank_recon_report import generate_recon_pdf, recon_statement
    r = recon_statement(int(import_id))
    if not r:
        raise Http404
    resp = HttpResponse(generate_recon_pdf(r), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="bank_reconciliation_{r["as_of"]}.pdf"'
    return resp
