"""Stage D3a (Payables, docs/mockups/d3_payables.html screens 1-5, approved 2026-10-03): the batches (order statements), one batch with its invoices, credit
memos, checks (read only until D4) and printing, Add data, and Payables due. The rules are in engines/ (statement_rules, check_rules, db_engine, duplicate_service,
batch_statement_service, payables_due_service); these views draw the forms and pass on what was typed.

Two people editing the same invoice or credit memo: each edit form carries a fingerprint of the row as it was shown, and a save is refused when the row changed since."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date, _suppliers
from masters.views import page_needed

CREATED = [("all", "All time"), ("today", "Today"), ("7", "Last 7 days"), ("30", "Last 30 days")]
STAGES = ["Draft", "Parked", "Issued", "Cancelled"]
BATCHES_SQL = """SELECT os.statement_id, os.statement_ref, os.creditor_id, c.creditor_name, c.terms, os.total_amount, v.credit_memo_total, v.net_total,
       v.net_total - COALESCE((SELECT SUM(amount) FROM checks_issued WHERE statement_id = os.statement_id AND status = 'Active'
                                AND (bank_cleared_date IS NOT NULL OR manual_cleared_at IS NOT NULL OR settled_outside_at IS NOT NULL)), 0) AS outstanding,
       (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = os.statement_id AND i.status != 'Cancelled') AS n_invoices,
       os.status, os.is_locked, os.needs_reprint, datetime(os.created_at, '+8 hours') AS created_at, {modified} AS last_modified,
       CASE WHEN os.status = 'Cancelled' THEN 'Cancelled'
            WHEN EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active' AND k.printed_at IS NOT NULL) THEN 'Issued'
            WHEN EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active') THEN 'Parked'
            ELSE 'Draft' END AS stage
FROM order_statements os JOIN creditors c ON os.creditor_id = c.creditor_id JOIN v_batch_net_totals v ON v.statement_id = os.statement_id"""


def _can(request, action: str) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], action)


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _num(text, default=None):
    try:
        return float(str(text).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    from engines.common import get_conn
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _batches(where: str = "", params: tuple = ()) -> list[dict]:
    from engines.last_modified_sql import statement_modified_sql
    return _rows(BATCHES_SQL.format(modified=statement_modified_sql("os")) + (f" WHERE {where}" if where else "") + " ORDER BY os.statement_id DESC", params)


def _stamp(*values) -> str:
    return hashlib.sha256(repr(values).encode()).hexdigest()[:16]


# --- the batches --------------------------------------------------------------------------------------------------------------------------------------

@page_needed("order-statements")
def statements(request):
    from engines.clock import manila_today
    rows = _batches()
    g = request.GET
    search, sup, stage, created = g.get("q", "").strip(), g.get("s", ""), g.get("stage", ""), g.get("created", "all")
    suppliers = sorted({(r["creditor_id"], r["creditor_name"]) for r in rows}, key=lambda x: str(x[1]).lower())
    since = {"today": manila_today(), "7": manila_today() - timedelta(days=6), "30": manila_today() - timedelta(days=29)}.get(created)
    out = []
    for r in rows:
        if search and search.lower() not in str(r["statement_ref"]).lower():
            continue
        if sup and str(r["creditor_id"]) != sup:
            continue
        if stage and r["stage"] != stage:
            continue
        if since and str(r["created_at"])[:10] < since.isoformat():
            continue
        r["is_draft"] = str(r["statement_ref"]).startswith("DRAFT-")
        out.append(r)
    from engines.statement_rules import unbatched_invoices
    if g.get("view") == "invoices":                             # the menu's Invoices entry (a shortcut of this page: the same rights)
        return redirect("/order-statements/invoices/")
    waiting = unbatched_invoices()
    return render(request, "payables/statements.html", {"page_id": "order-statements", "rows": out, "q": search, "sup": sup, "stage": stage, "created": created,
                                                        "suppliers": suppliers, "stages": STAGES, "createds": CREATED, "waiting_n": len(waiting), "waiting_total": float(waiting["amount"].sum()) if len(waiting) else 0.0})


@page_needed("order-statements")
def statement_new(request):
    """A new Draft batch for a supplier (its total starts at 0 and follows its invoices)."""
    from engines.statement_rules import create_order_statement
    if request.method != "POST":
        return redirect("/order-statements/")
    cid = int(request.POST.get("s") or 0)
    if cid not in dict(_suppliers()):
        _say(request, False, "Pick the supplier.")
        return redirect("/order-statements/")
    sid = create_order_statement(cid)
    _say(request, True, "Draft payment created. Add its invoices below.")
    return redirect(f"/order-statements/{sid}/")


# --- one batch ----------------------------------------------------------------------------------------------------------------------------------------

def _batch_or_404(sid: int) -> dict:
    found = _batches("os.statement_id = ?", (sid,))
    if not found:
        raise Http404
    return found[0]


def _invoices(sid: int) -> list[dict]:
    from engines.statement_rules import parse_less_pct
    rows = _rows("""SELECT i.invoice_id, i.doc_ref, i.dr_no, i.invoice_date, i.due_date, i.gross_amount, i.trade_discounts, i.amount, i.status,
                           EXISTS (SELECT 1 FROM po_receipts r WHERE r.invoice_id = i.invoice_id) AS from_po
                    FROM invoices i WHERE i.statement_id = ? ORDER BY i.invoice_id""", (sid,))
    for r in rows:
        r["less_pct"] = parse_less_pct(r["trade_discounts"])
        r["stamp"] = _stamp(r["doc_ref"], r["dr_no"], r["invoice_date"], r["due_date"], r["gross_amount"], r["trade_discounts"], r["amount"], r["status"])
    return rows


def _memos(sid: int) -> list[dict]:
    rows = _rows("SELECT credit_memo_id, memo_ref, memo_date, amount, reason FROM credit_memos WHERE statement_id = ? ORDER BY credit_memo_id", (sid,))
    for r in rows:
        r["stamp"] = _stamp(r["memo_ref"], r["memo_date"], r["amount"], r["reason"])
    return rows


@page_needed("order-statements")
def statement(request, sid: int):
    b = _batch_or_404(sid)
    if request.method == "POST":
        return _statement_post(request, b)
    from engines.po_rules import po_links_for_batch
    from engines.statement_rules import resolve_due_date
    from engines.clock import manila_today
    tab = request.GET.get("tab", "invoices")
    head = _rows("""SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total, SUM(printed_at IS NOT NULL AND printed_at != '') AS exported,
                           SUM(check_number IS NOT NULL AND check_number != '') AS numbered,
                           (SELECT COUNT(*) FROM invoices WHERE statement_id = ? AND status != 'Cancelled') AS invoices
                    FROM checks_issued WHERE statement_id = ? AND status = 'Active'""", (sid, sid))[0]
    n, checks_total = int(head["n"] or 0), float(head["total"] or 0)
    balance = float(b["net_total"]) - checks_total
    exported, numbered, n_inv = int(head["exported"] or 0), int(head["numbered"] or 0), int(head["invoices"] or 0)
    steps = [(f"{n_inv} invoice(s)", "done" if n_inv else "wait"),
             ("Checks parked", "done" if n and abs(balance) <= 0.005 else ("part" if n else "wait")),
             ("Sent to Chrysanth", "done" if n and exported == n else ("part" if exported else "wait")),
             ("Numbered", "done" if n and numbered == n else ("part" if numbered else "wait"))]
    links = po_links_for_batch(sid)
    invoices, memos = _invoices(sid), _memos(sid)
    from payables.batch_checks import checks_for
    checks = checks_for(_rows, sid)
    cancelled, locked = b["status"] == "Cancelled", bool(b["is_locked"])
    from engines.statement_rules import unbatched_invoices
    waiting = unbatched_invoices(int(b["creditor_id"])).to_dict("records") if not cancelled and not locked else []
    ctx = {"page_id": "order-statements", "b": b, "tab": tab, "n": n, "checks_total": checks_total, "balance": balance, "steps": steps,
           "links": links.to_dict("records"), "n_links": len(links), "invoices": invoices, "memos": memos, "checks": checks,
           "no_po": sum(1 for r in invoices if not r["from_po"] and r["status"] != "Cancelled"), "waiting": waiting,
           "cancelled": cancelled, "locked": locked, "editable": not cancelled and not locked,
           "may_reopen": locked and not cancelled and _can(request, "check.void"), "may_cancel": not cancelled and _can(request, "check.void"),
           "may_delete": str(b["statement_ref"]).startswith("DRAFT-") and not memos and not checks and _can(request, "check.void"),
           "may_number": b["status"] == "Draft" and str(b["statement_ref"]).startswith("DRAFT-") and n_inv > 0 and not cancelled,
           "today": manila_today(), "due_today": resolve_due_date(manila_today(), b["terms"] or ""),
           "edit_inv": request.GET.get("edit_inv", ""), "edit_memo": request.GET.get("edit_memo", ""),
           "warnings": request.session.pop("inv_warnings", None), "typed": request.session.pop("inv_typed", None),
           "memo_warnings": request.session.pop("memo_warnings", None), "memo_typed": request.session.pop("memo_typed", None),
           "skipped": request.session.pop("skipped", None), "remaining": max(balance, 0.0),
           "may_park": _can(request, "check.park"), "may_write": _can(request, "check.issue"), "may_release": _can(request, "check.release"),
           "may_void": _can(request, "check.void"),
           "n_parked": sum(1 for c in checks if c["stage"] == "Parked"), "n_unnumbered": sum(1 for c in checks if c["stage"] == "Written" and not c["check_number"]),
           "n_to_release": sum(1 for c in checks if c["stage"] == "Written" and c["check_number"])}
    if tab == "checks":                                         # a panel the row buttons open: number these, edit / void that one
        picked = {int(k[5:]) for k, v in request.GET.items() if k.startswith("tick_") and v == "1" and k[5:].isdigit()}
        ctx["num_mode"] = "hand" if request.GET.get("hand") else ("number" if request.GET.get("number") else "")
        ctx["num_checks"] = [c for c in checks if c["check_id"] in picked and c["stage"] in ("Parked", "Written") and not c["check_number"]] if ctx["num_mode"] else []
        if ctx["num_checks"]:
            from payables.batch_checks import booklet_plan
            ctx.update(booklet_plan(request, ctx["num_checks"]))
        one = int(request.GET.get("edit") or request.GET.get("void") or 0)
        ctx["edit_check"] = next((c for c in checks if c["check_id"] == one and c["stage"] == "Parked"), None) if request.GET.get("edit") else None
        ctx["void_check"] = next((c for c in checks if c["check_id"] == one and c["status"] == "Active" and c["stage"] != "Cleared"), None) if request.GET.get("void") else None
    if ctx.get("may_park") and ctx.get("remaining", 0) > 0.004:
        from payables.batch_checks import park_context
        ctx.update(park_context(b, _rows, ctx["due_today"]))
    return render(request, "payables/statement.html", ctx)


def _statement_post(request, b: dict):
    from engines.check_rules import cancel_order_statement
    from engines.db_engine import promote_draft_statement
    from engines.duplicate_service import credit_memo_warnings, invoice_warnings
    from engines.statement_rules import (add_credit_memo, add_invoice, delete_credit_memo, delete_invoice, edit_credit_memo, edit_invoice,
                                         resolve_due_date, unlock_statement)
    sid, cid, terms = int(b["statement_id"]), int(b["creditor_id"]), b["terms"] or ""
    p, what = request.POST, request.POST.get("action", "")
    back = f"/order-statements/{sid}/"
    if what.startswith("chk_"):                                 # the Checks tab (web/payables/batch_checks.py)
        from payables import batch_checks
        return batch_checks.post(request, b, lambda ok, msg: _say(request, ok, msg), lambda action: _can(request, action))
    if what == "add_invoice":
        from engines.statement_rules import doc_reference
        doc, day, gross, less = doc_reference(p.get("doc"), p.get("dr")), _date(p.get("date")), _num(p.get("gross"), 0), _num(p.get("less"), 0) or 0
        if not doc or not day or gross <= 0:
            _say(request, False, "A new invoice needs a document reference (its SI no. or DR no.), the date received and a positive amount.")
        elif not 0 <= less <= 100:
            _say(request, False, "Less % must be between 0 and 100.")
        else:
            warns = [] if p.get("confirmed") == "1" else invoice_warnings(cid, [(doc, day, gross)])
            if warns:
                request.session["inv_warnings"], request.session["inv_typed"] = warns, {"doc": doc, "dr": p.get("dr", ""), "date": str(day), "gross": p.get("gross"), "less": p.get("less", "")}
                return redirect(back + "#add_invoice")
            try:
                net, _, _ = add_invoice(cid, doc, day, resolve_due_date(day, terms), gross, [less] if less else [], sid, dr_no=p.get("dr", ""))
                _say(request, True, f"Invoice {doc} added (net ₱{net:,.2f}).")
            except sqlite3.IntegrityError:
                _say(request, False, f"Invoice {doc} already exists for this supplier.")
        return redirect(back + "#add_invoice")
    if what in ("save_invoice", "delete_invoice"):
        inv_id = int(p.get("invoice_id", 0))
        now = next((r for r in _invoices(sid) if r["invoice_id"] == inv_id), None)
        if now is None or now["stamp"] != p.get("stamp"):
            _say(request, False, "Someone else changed or removed this invoice after you opened it. Nothing was saved; this is the current list.")
            return redirect(back)
        if what == "delete_invoice":
            _say(request, *delete_invoice(inv_id))
            return redirect(back)
        day, gross, less = _date(p.get("date")), _num(p.get("gross"), 0), _num(p.get("less"), 0) or 0
        if not day:
            _say(request, False, "Enter the date received.")
            return redirect(back + f"?edit_inv={inv_id}")
        from engines.statement_rules import doc_reference
        ref = doc_reference(p.get("doc"), p.get("dr"))
        ok, msg = (edit_invoice(inv_id, ref, day, resolve_due_date(day, terms), gross, less, dr_no=p.get("dr", "")) if ref
                   else (False, "An invoice needs a document reference: its SI no. or DR no."))
        _say(request, ok, msg)
        return redirect(back if ok else back + f"?edit_inv={inv_id}")
    if what == "put_in_batch":
        from engines.statement_rules import put_in_batch
        ids = [int(k[5:]) for k, v in p.items() if k.startswith("pick_") and v == "1" and k[5:].isdigit()]
        _say(request, *put_in_batch(sid, ids))
        return redirect(back)
    if what == "take_out":
        from engines.statement_rules import take_out_of_batch
        inv_id = int(p.get("invoice_id", 0))
        now = next((r for r in _invoices(sid) if r["invoice_id"] == inv_id), None)
        if now is None or now["stamp"] != p.get("stamp"):
            _say(request, False, "Someone else changed or removed this invoice after you opened it. Nothing was changed; this is the current list.")
        else:
            _say(request, *take_out_of_batch(inv_id))
        return redirect(back)
    if what == "add_memo":
        ref, day, amount, reason = p.get("ref", "").strip(), _date(p.get("date")), _num(p.get("amount"), 0), p.get("reason", "").strip()
        if not ref or not day or amount <= 0:
            _say(request, False, "A new credit memo needs its reference, date and a positive amount.")
        else:
            warns = [] if p.get("confirmed") == "1" else credit_memo_warnings(cid, ref, day, amount)
            if warns:
                request.session["memo_warnings"], request.session["memo_typed"] = warns, {"ref": ref, "date": str(day), "amount": p.get("amount"), "reason": reason}
                return redirect(back + "?tab=memos#add_memo")
            _say(request, *add_credit_memo(sid, ref, day, amount, reason))
        return redirect(back + "?tab=memos#add_memo")
    if what in ("save_memo", "delete_memo"):
        memo_id = int(p.get("memo_id", 0))
        now = next((r for r in _memos(sid) if r["credit_memo_id"] == memo_id), None)
        if now is None or now["stamp"] != p.get("stamp"):
            _say(request, False, "Someone else changed or removed this credit memo after you opened it. Nothing was saved; this is the current list.")
            return redirect(back + "?tab=memos")
        if what == "delete_memo":
            _say(request, *delete_credit_memo(memo_id))
        else:
            day = _date(p.get("date"))
            ok, msg = edit_credit_memo(memo_id, p.get("ref", "").strip(), day or now["memo_date"], _num(p.get("amount"), 0), p.get("reason", "").strip())
            _say(request, ok, msg)
            if not ok:
                return redirect(back + f"?tab=memos&edit_memo={memo_id}")
        return redirect(back + "?tab=memos")
    if what == "number":
        try:
            _say(request, True, f"The payment statement is now {promote_draft_statement(sid, 'Issued')}.")
        except ValueError as e:
            _say(request, False, str(e))
        return redirect(back)
    if what == "reopen":
        if not _can(request, "check.void"):
            _say(request, False, "Your role cannot reopen a closed payment statement.")
            return redirect(back)
        reason = " ".join(p.get("reason", "").split())
        if not reason:
            _say(request, False, "Say why the payment statement is reopened.")
            return redirect(back)
        ok, msg = unlock_statement(sid, f"Payment statement reopened to edit: {reason}")
        _say(request, ok, "Reopened for editing. It closes again by itself when you save and the checks still cover it." if ok else msg)
        return redirect(back)
    if what == "cancel":
        _say(request, *cancel_order_statement(sid))
        return redirect(back)
    if what == "delete_draft":                                   # owner, 2026-10-13: "Draft OS can delete. OS number is only cancelled"
        from engines.check_rules import delete_draft_statement
        ok, msg = delete_draft_statement(sid)
        _say(request, ok, msg)
        return redirect("/order-statements/" if ok else back)
    _say(request, False, "Unknown action.")
    return redirect(back)


@page_needed("order-statements")
def statement_pdf(request, sid: int, kind: str):
    from engines.batch_statement_service import generate_batch_statement_pdf, generate_batch_statement_thermal_pdf
    b = _batch_or_404(sid)
    history = request.GET.get("history", "1") == "1"
    if kind == "thermal":
        data, suffix = generate_batch_statement_thermal_pdf(sid, history), "_thermal"
    else:
        copies = request.GET.get("copies", "both") if request.GET.get("copies") in ("both", "filing", "supplier") else "both"
        data = generate_batch_statement_pdf(sid, history, copies, request.GET.get("apply") == "1")
        suffix = {"both": "", "filing": "_filing", "supplier": "_supplier"}[copies]
    if not data:
        raise Http404
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="batch_statement_{b["statement_ref"]}{suffix}.pdf"'.replace(" ", "_")
    return resp


# --- Add data -----------------------------------------------------------------------------------------------------------------------------------------

# --- Payables due -------------------------------------------------------------------------------------------------------------------------------------

KIND_COLORS = {"Supplier checks": "#16305B", "Lender checks": "#4C78A8", "Bank loans": "#C26A00", "Credit cards": "#9FB3D1", "Purchase orders": "#2E7D32"}


DAILY_SHOWS = [("due", "Today + overdue"), ("week", "Next 7 days"), ("ticked", "Ticked, waiting for the bank"), ("all", "All")]


def _daily(df, show: str, today, by: str = "bank") -> dict:
    """The daily payables checklist, grouped by day (owner, 2026-10-09): items stay on their day until cleared; days before today are overdue.
    By default the day is the banking day (owner, 2026-10-12: "if it falls weekends consider it in the next business day ... deafult this. then
    optional is per day"): a Saturday, a Sunday or a marked day off counts on the next banking day. by="date" is each item's own date."""
    from datetime import timedelta
    show = show if show in dict(DAILY_SHOWS) else "due"
    by = "date" if by == "date" else "bank"
    if df.empty:
        return {"days": [], "shows": DAILY_SHOWS, "show": show, "by": by, "counts": {k: 0 for k, _ in DAILY_SHOWS}, "n_overdue": 0, "total_due": 0.0}
    df = df.assign(group=df["pay_day"] if by == "bank" else df["day"])
    open_ = df[df["ticked"].isna()]
    week = (today + timedelta(days=7)).isoformat()
    sets = {"due": open_[open_["group"] <= today.isoformat()], "week": open_[open_["group"] <= week], "ticked": df[df["ticked"].notna()], "all": df}
    rows = sets[show].astype(object).where(sets[show].notna(), None)       # no NaN reaching the page (a NaN "ticked" would read as ticked)
    days = []
    for day, part in rows.groupby("group", sort=True):
        items = part.to_dict("records")
        days.append({"day": day, "overdue": day < today.isoformat() and show != "ticked", "today": day == today.isoformat(), "items": items,
                     "total": float(sum(float(x or 0) for x in part["amount"])), "n_open": sum(1 for x in part["ticked"] if x is None)})
    due = sets["due"]
    return {"days": days, "shows": DAILY_SHOWS, "show": show, "by": by, "counts": {k: len(x) for k, x in sets.items()},
            "n_overdue": int((due["group"] < today.isoformat()).sum()), "total_due": float(due["amount"].sum())}


@page_needed("checks")
def checks(request):
    """Pay suppliers -> Checks (Phase 2, step 1, owner 2026-10-08): the written checks waiting for the bank, the "cleared" tick (confirmed later by the
    bank statement), and, once, the cut-off for the checks from before the first bank statement. Booklets and the rest still live in the old screens."""
    from engines import clearing
    from engines.permissions import can
    role = request.slicks_user["role"]
    may_tick = can(role, "payables.mark_paid")          # its own switch (2026-10-09); was the Bank switch
    is_admin = role == "admin"
    if request.method == "POST":
        what, cid = request.POST.get("action"), int(request.POST.get("check_id") or 0)
        if what == "clear_many" and may_tick:                   # the daily payables checklist: everything ticked, one date and note (owner, 2026-10-09)
            on, note = _date(request.POST.get("on")), request.POST.get("note", "")
            picked = [k[5:].split("_", 1) for k, val in request.POST.items() if k.startswith("tick_") and val == "1"]
            done, refused = 0, []
            for kind, item in picked:
                if not item.isdigit():
                    continue
                fn = {"check": clearing.mark_cleared, "loan": clearing.mark_loan_cleared, "card": clearing.pay_card}.get(kind)
                if fn is None:
                    continue
                if kind == "card":                                  # a card can be paid partly: the amount typed on its line (owner, 2026-10-09)
                    typed = request.POST.get(f"amt_card_{item}", "").replace(",", "").strip()
                    try:
                        one_ok, one_msg = fn(int(item), on, note, float(typed) if typed else None)
                    except ValueError:
                        one_ok, one_msg = False, f"'{typed}' is not an amount."
                else:
                    one_ok, one_msg = fn(int(item), on, note)
                done += one_ok
                if not one_ok:
                    refused.append(one_msg)
            if not picked:
                ok, msg = False, "Tick the payables that are paid."
            else:
                ok, msg = not refused, f"{done} marked paid." + (" Not marked: " + " · ".join(refused) if refused else "")
            _say(request, ok, msg)
            return redirect("/checks/?show=" + request.POST.get("show", "due") + ("&by=date" if request.POST.get("by") == "date" else ""))
        if what == "unpay_card" and may_tick:
            _say(request, *clearing.unpay_card(int(request.POST.get("item_id") or 0)))
            return redirect("/checks/?show=ticked")
        if what == "unclear_loan" and may_tick:
            _say(request, *clearing.unmark_loan_cleared(int(request.POST.get("item_id") or 0)))
            return redirect("/checks/?show=ticked")
        if what == "clear" and may_tick:
            ok, msg = clearing.mark_cleared(cid, _date(request.POST.get("on")), request.POST.get("note", ""))
        elif what == "unclear" and may_tick:
            ok, msg = clearing.unmark_cleared(cid)
        elif what == "cutoff" and is_admin:
            from pathlib import Path

            from db_location import safe_copy
            from engines.clock import manila_now
            import engines.db_engine as dbe
            backups = Path(dbe.DB_PATH).resolve().parent.parent / "backups"
            backups.mkdir(exist_ok=True)
            safe_copy(dbe.DB_PATH, str(backups / f"before_clearing_cutoff_{manila_now():%Y%m%d_%H%M%S}.db"))
            ok, msg = clearing.apply_cutoff()
        else:
            ok, msg = False, "You can't do that here."
        _say(request, ok, msg)
        return redirect("/checks/" + ("?tab=cutoff" if what == "cutoff" else "?show=ticked" if what == "unclear" else ""))
    tab = request.GET.get("tab", "waiting")
    today = __import__("engines.clock", fromlist=["manila_today"]).manila_today()
    ctx = {"page_id": "checks", "tab": tab, "may_tick": may_tick, "is_admin": is_admin, "today": today, "today_iso": today.isoformat(),
           "more": _more_page("checks") if can(role, "more.use") else ""}
    if tab != "cutoff":
        ctx.update(_daily(clearing.daily_payables(), request.GET.get("show", "due"), today, request.GET.get("by", "bank")))
    if is_admin:
        from engines.common import get_conn
        with get_conn() as conn:
            ctx["cutoff_done"] = conn.execute("SELECT MAX(datetime(timestamp, '+8 hours')) FROM audit_logs WHERE user_action = 'CLEARING_CUTOFF'").fetchone()[0]
        if tab == "cutoff":
            prev = clearing.cutoff_preview()
            ctx.update({"first_day": prev["first_day"], "before_n": len(prev["before"]), "before_total": float(prev["before"]["amount"].sum()) if len(prev["before"]) else 0.0,
                        "inside": prev["inside"].to_dict("records"), "loans_n": len(prev["loans_before"]),
                        "loans_total": float(prev["loans_before"]["amount"].sum()) if len(prev["loans_before"]) else 0.0,
                        "cards_before": prev["cards_before"].to_dict("records")})
    return render(request, "payables/checks.html", ctx)


def _more_page(page_id: str) -> str:
    from django.conf import settings
    base = getattr(settings, "STREAMLIT_URL", "")
    return f"{base.rstrip('/')}/{page_id}" if base else ""


SCHEDULE_KINDS = [("Supplier", "Suppliers"), ("Lender", "Lenders"), ("Bank Loan", "Bank loans"), ("Credit Card", "Credit cards")]


def _schedule(request):
    """The schedule of payments narrowed by the page's filters (reports_service.filter_schedule: the page, the Excel and the PDF show the same rows)."""
    from calendar import monthrange

    from engines.clock import manila_today
    from engines.reports_service import filter_schedule, get_payment_schedule_all_tracks
    today = manila_today()
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    g = request.GET
    d_from = _date(g.get("from")) or (None if "from" in g else today)
    d_to = _date(g.get("to")) or (None if "to" in g else nxt.replace(day=monthrange(nxt.year, nxt.month)[1]))
    kind = g.get("kind", "") if g.get("kind", "") in dict(SCHEDULE_KINDS) else ""
    payee = g.get("payee", "")
    every = get_payment_schedule_all_tracks()
    payees = sorted(every["payee_name"].dropna().unique().tolist()) if not every.empty else []
    df = filter_schedule(every, [kind] if kind else None, d_from, d_to, [payee] if payee else None)
    return df, {"from": d_from, "to": d_to, "kind": kind, "payee": payee}, payees


@page_needed("reports")
def reports(request):
    """Reports -> Schedule of payments (owner, 2026-10-08, stage D8 begun for the co-owner): everything still owed across the four kinds, by pay day, with
    Excel and PDF of exactly what is shown. The other reports are still in the old screens (/more/), for the people allowed there."""
    from engines.permissions import can, hide_check_details
    if request.GET.get("r") == "soa":                           # the reports kept after the review (owner, 2026-10-09; Decision reports dropped too)
        from payables import report_more
        return report_more.soa(request)
    if request.GET.get("r") in ("po", "nopo"):                  # the PO reports (owner, 2026-10-12)
        from payables import report_po
        return report_po.follow_through(request) if request.GET.get("r") == "po" else report_po.with_and_without(request)
    df, f, payees = _schedule(request)
    what = request.GET.get("download")
    show_ref = not hide_check_details(request.slicks_user["role"])
    if what == "pdf":
        from engines.payment_schedule_report_service import generate_payment_schedule_pdf
        data = generate_payment_schedule_pdf([f["kind"]] if f["kind"] else None, None, f["from"], f["to"], [f["payee"]] if f["payee"] else None)
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = 'attachment; filename="schedule_of_payments.pdf"'
        return resp
    if what == "xlsx":
        import io

        import pandas as pd
        out = pd.DataFrame({"Pay day": pd.to_datetime(df["actual_payment_date"]).dt.date if not df.empty else [], "Due date": df["due_date"].astype(str).str[:10] if not df.empty else [],
                            "Pay to": df.get("payee_name", []), "Kind": df.get("track", []), **({"Reference": df.get("reference", [])} if show_ref else {}),
                            "Batch": df.get("batch", []), "Amount": df.get("amount", [])})
        buf = io.BytesIO()
        out.to_excel(buf, index=False, sheet_name="Schedule of payments")
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = 'attachment; filename="schedule_of_payments.xlsx"'
        return resp
    days = []
    if not df.empty:
        import pandas as pd
        df = df.assign(_day=pd.to_datetime(df["actual_payment_date"]).dt.date, _due=pd.to_datetime(df["due_date"]).dt.date)
        for day, rows in df.groupby("_day", sort=True):
            days.append({"day": day, "total": float(rows["amount"].sum()), "n": len(rows), "shifted": bool((rows["_due"] != day).any()),
                         "rows": [{"payee": r.payee_name, "kind": {"Bank Loan": "Bank loan", "Credit Card": "Credit card"}.get(r.track, r.track), "reference": r.reference if isinstance(r.reference, str) else "",
                                   "batch": r.batch if isinstance(r.batch, str) else "", "amount": float(r.amount)} for r in rows.itertuples()]})
    heaviest = max(days, key=lambda d: d["total"]) if days else None
    from urllib.parse import urlencode
    query = urlencode({k: ("" if v is None else str(v)) for k, v in f.items()})
    return render(request, "payables/reports.html", {"page_id": "reports", "r": "schedule", "f": f, "kinds": SCHEDULE_KINDS, "payees": payees, "days": days, "show_ref": show_ref,
                                                     "total": sum(d["total"] for d in days), "count": sum(d["n"] for d in days), "heaviest": heaviest, "query": query,
                                                     "more": _more_reports() if can(request.slicks_user["role"], "more.use") else ""})


def _more_reports() -> str:
    from django.conf import settings
    base = getattr(settings, "STREAMLIT_URL", "")
    return f"{base.rstrip('/')}/reports" if base else ""


