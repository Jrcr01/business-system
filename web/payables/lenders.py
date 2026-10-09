"""Other payables -> Lenders in Django (Phase 7, owner 2026-10-08; mock-up approved): the lenders and their loan notes; a new loan note with the payments on the
dates you choose and the interest by days (balance x monthly rate x days / 30); a loan note's schedule (move a payment, park its check) and its Checks tab, the same
flow as a batch's (web/payables/batch_checks.py). The rules: engines/loan_schedule_service.py, loan_note_rules.py, check_rules.py."""
from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date
from masters.views import page_needed

MAX_ROWS = 24


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
        conn.row_factory = __import__("sqlite3").Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _can(request, action: str) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], action)


PAID = "(c.bank_cleared_date IS NOT NULL OR c.manual_cleared_at IS NOT NULL OR c.settled_outside_at IS NOT NULL)"


@page_needed("lenders")
def lenders(request):
    if request.method == "POST":
        if request.POST.get("action", "").startswith("gc_"):
            return _guarantee_post(request)
        return _create(request)
    if request.GET.get("tab") == "guarantees":
        return _guarantees(request)
    today = date.today().isoformat()
    lenders_all = _rows("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'LENDER' AND is_lessor = 0 AND is_active = 1 ORDER BY creditor_name")
    q, who, status = request.GET.get("q", "").strip(), request.GET.get("s", ""), request.GET.get("status", "open")
    where, params = ["n.is_lease = 0"], [today]
    if status == "open":
        where.append("n.status IN ('Active', 'Pending Setup')")
    elif status:
        where.append("n.status = ?"); params.append(status)
    if who.isdigit():
        where.append("n.creditor_id = ?"); params.append(int(who))
    if q:
        where.append("n.note_ref LIKE ?"); params.append(f"%{q}%")
    rows = _rows(f"""SELECT n.loan_note_id, n.note_ref, n.creditor_id, cr.creditor_name, n.issue_date, n.principal_amount, n.monthly_interest_rate, n.total_payable, n.status,
                            n.total_payable - (SELECT COALESCE(SUM(c.amount), 0) FROM checks_issued c WHERE c.loan_note_id = n.loan_note_id AND c.status = 'Active' AND {PAID}) AS outstanding,
                            (SELECT MIN(i.due_date) FROM loan_installments i WHERE i.loan_note_id = n.loan_note_id AND i.due_date >= ?) AS next_day
                     FROM loan_notes n JOIN creditors cr ON cr.creditor_id = n.creditor_id
                     WHERE {" AND ".join(where)} ORDER BY n.issue_date DESC, n.loan_note_id DESC""", tuple(params))
    from engines.guarantee_check_service import exposure
    ctx = {"page_id": "lenders", "tab": "notes", "g_exposure": exposure(), "rows": rows, "all_lenders": lenders_all, "q": q, "who": who, "status": status,
           "statuses": [("open", "Open (active or being set up)"), ("Active", "Active"), ("Pending Setup", "Pending setup"), ("Settled", "Settled"),
                        ("Defaulted", "Defaulted"), ("Cancelled", "Cancelled"), ("", "All")],
           "outstanding": sum(float(r["outstanding"] or 0) for r in rows if r["status"] == "Active"),
           "new": bool(request.GET.get("new")), "pick": request.GET.get("lender", ""), "may_write": _can(request, "loans.use"), "today": date.today(),
           "n_rows": range(1, MAX_ROWS + 1), "typed": request.session.pop("loan_typed", None)}
    return render(request, "payables/lenders.html", ctx)


def _guarantees(request):
    """Lenders -> Guarantee checks (owner, 2026-10-09): checks given to a lender as security -- not payments, so never in payables. Held ones are what a
    lender could present; they are marked returned when the loan is settled; one the bank pays turns Presented (flagged on Bank and cash)."""
    from engines.guarantee_check_service import exposure, guarantee_checks
    show = request.GET.get("show", "Held")
    df = guarantee_checks(None if show == "all" else show)
    rows = df.astype(object).where(df.notna(), None).to_dict("records")
    ids = [int(r["guarantee_id"]) for r in rows]
    extra = {r["guarantee_id"]: r for r in _rows(f"SELECT guarantee_id, creditor_id, loan_note_id FROM guarantee_checks WHERE guarantee_id IN ({','.join('?' * len(ids))})",
                                                 tuple(ids))} if ids else {}
    for r in rows:
        r.update({k: extra.get(int(r["guarantee_id"]), {}).get(k) for k in ("creditor_id", "loan_note_id")})
    counts = {s: int((guarantee_checks(s)["status"] == s).sum()) for s in ("Held", "Returned", "Presented")}
    lenders_all = _rows("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'LENDER' AND is_lessor = 0 AND is_active = 1 ORDER BY creditor_name")
    notes = _rows("""SELECT n.loan_note_id, n.creditor_id, COALESCE(n.note_ref, 'Loan note') AS note_ref FROM loan_notes n
                     WHERE n.is_lease = 0 AND n.status IN ('Active', 'Pending Setup') ORDER BY n.issue_date DESC""")
    return render(request, "payables/lenders.html", {"page_id": "lenders", "tab": "guarantees", "g_rows": rows, "g_show": show, "g_counts": counts,
                                                     "g_exposure": exposure(), "all_lenders": lenders_all, "g_notes": notes,
                                                     "g_edit": int(request.GET.get("edit") or 0),
                                                     "may_write": _can(request, "loans.use"), "today": date.today()})


def _guarantee_post(request):
    from engines import guarantee_check_service as gc
    p, what = request.POST, request.POST.get("action")
    back = "/lenders/?tab=guarantees"
    if not _can(request, "loans.use"):
        _say(request, False, "Loan work is switched off for you.")
        return redirect(back)
    if what == "gc_record":
        amount = _num(p.get("amount"))
        ok, msg, _ = gc.record_guarantee_check(int(p.get("lender") or 0), p.get("number", ""), amount, _date(p.get("given_on")),
                                               int(p.get("loan_note")) if (p.get("loan_note") or "").isdigit() else None, p.get("note", ""))
    elif what == "gc_return":
        ok, msg = gc.return_guarantee_check(int(p.get("guarantee_id") or 0), _date(p.get("returned_on")) or date.today(), p.get("note", ""))
    elif what == "gc_edit":
        amount = p.get("amount", "").strip()
        ok, msg = gc.edit_guarantee_check(int(p.get("guarantee_id") or 0), _num(amount) if amount else None, _date(p.get("given_on")),
                                          int(p.get("loan_note")) if (p.get("loan_note") or "").isdigit() else None, p.get("note", ""))
    elif what == "gc_undo":
        ok, msg = gc.undo_return_guarantee(int(p.get("guarantee_id") or 0))
    else:
        ok, msg = False, "Unknown action."
    _say(request, ok, msg)
    return redirect(back + ("&show=all" if what in ("gc_undo",) else "") + ("&show=" + p.get("show", "") if what == "gc_edit" and p.get("show") else ""))


def _create(request):
    from engines.loan_schedule_service import create_note_with_payments
    p = request.POST
    if not _can(request, "loans.use"):
        _say(request, False, "Loan work is switched off for you.")
        return redirect("/lenders/")
    lender, day, principal, rate = int(p.get("lender") or 0), _date(p.get("issue_date")), _num(p.get("principal")), _num(p.get("rate"))
    pays, override = [], {}
    for i in range(1, MAX_ROWS + 1):
        d = _date(p.get(f"d{i}"))
        if not d:
            continue
        pays.append((d, _num(p.get(f"p{i}"), 0.0) or 0.0))
        typed_interest = p.get(f"i{i}", "").strip()
        if p.get(f"io{i}") == "1" and typed_interest:                  # the interest was typed over on this row
            override[d] = _num(typed_interest)
    if not lender or not day or principal is None or rate is None:
        _say(request, False, "Pick the lender and give the loan date, the principal and the interest per month.")
        request.session["loan_typed"] = dict(p.items())
        return redirect("/lenders/?new=1")
    ok, msg, lnid = create_note_with_payments(lender, day, principal, rate, pays, override)
    _say(request, ok, msg)
    if not ok:
        request.session["loan_typed"] = dict(p.items())
        return redirect("/lenders/?new=1")
    return redirect(f"/lenders/{lnid}/")


@page_needed("lenders")
def loan_note(request, lnid: int):
    note = next(iter(_rows("""SELECT n.*, cr.creditor_name FROM loan_notes n JOIN creditors cr ON cr.creditor_id = n.creditor_id WHERE n.loan_note_id = ?""", (lnid,))), None)
    if not note:
        raise Http404
    if request.method == "POST":
        return _note_post(request, note)
    from payables.batch_checks import checks_for
    tab = request.GET.get("tab", "schedule")
    inst = _rows("""SELECT i.installment_id, i.installment_no, i.due_date, i.principal, i.interest, i.outstanding_balance, i.status, i.check_id,
                           c.check_number, c.printed_at, c.released_date, c.bank_cleared_date, c.manual_cleared_at, c.settled_outside_at, c.status AS check_status
                    FROM loan_installments i LEFT JOIN checks_issued c ON c.check_id = i.check_id
                    WHERE i.loan_note_id = ? ORDER BY i.due_date, i.installment_id""", (lnid,))
    prev = date.fromisoformat(str(note["issue_date"])[:10])
    from payables.batch_checks import stage
    for r in inst:
        d = date.fromisoformat(str(r["due_date"])[:10])
        r["days"], prev = (d - prev).days, d
        r["total"] = round(float(r["principal"]) + float(r["interest"]), 2)
        r["stage"] = stage({"status": r["check_status"], **r}) if r["check_id"] and r["check_status"] else ""
    checks = checks_for(_rows, lnid, "loan_note_id")
    ctx = {"page_id": "lenders", "n": note, "tab": tab, "inst": inst, "checks": checks, "today": date.today(),
           "total_interest": sum(float(r["interest"]) for r in inst), "total_pay": sum(r["total"] for r in inst),
           "move": int(request.GET.get("move") or 0), "may_park": _can(request, "check.park"), "may_write": _can(request, "check.issue"),
           "may_release": _can(request, "check.release"), "may_void": _can(request, "check.void"), "may_loans": _can(request, "loans.use"),
           "skipped": request.session.pop("skipped", None),
           "n_parked": sum(1 for c in checks if c["stage"] == "Parked"), "n_unnumbered": sum(1 for c in checks if c["stage"] == "Written" and not c["check_number"]),
           "n_to_release": sum(1 for c in checks if c["stage"] == "Written" and c["check_number"]), "n_unparked": sum(1 for r in inst if not r["check_id"])}
    if tab == "checks":
        picked = {int(k[5:]) for k, v in request.GET.items() if k.startswith("tick_") and v == "1" and k[5:].isdigit()}
        ctx["num_mode"] = "hand" if request.GET.get("hand") else ("number" if request.GET.get("number") else "")
        ctx["num_checks"] = [c for c in checks if c["check_id"] in picked and c["stage"] in ("Parked", "Written") and not c["check_number"]] if ctx["num_mode"] else []
        if ctx["num_checks"]:
            from payables.batch_checks import booklet_plan
            ctx.update(booklet_plan(request, ctx["num_checks"]))
        one = int(request.GET.get("void") or 0)
        ctx["void_check"] = next((c for c in checks if c["check_id"] == one and c["status"] == "Active" and c["stage"] != "Cleared"), None) if one else None
    ctx["guarantees"] = _rows("""SELECT check_number, face_amount, given_on, status, returned_on, note FROM guarantee_checks WHERE loan_note_id = ?
                                 ORDER BY guarantee_id""", (lnid,))
    return render(request, "payables/loan_note.html", ctx)


def _note_post(request, note: dict):
    from engines import loan_schedule_service as ls
    p, what, lnid = request.POST, request.POST.get("action", ""), int(note["loan_note_id"])
    if what.startswith("chk_"):
        from payables import batch_checks
        return batch_checks.post(request, {"creditor_id": note["creditor_id"]}, lambda ok, msg: _say(request, ok, msg), lambda a: _can(request, a), loan_note_id=lnid)
    back = f"/lenders/{lnid}/"
    if what == "move":
        if not _can(request, "loans.use"):
            _say(request, False, "Loan work is switched off for you.")
        else:
            _say(request, *ls.change_installment_date(int(p.get("installment_id") or 0), _date(p.get("new_date")) or date.today()))
    elif what == "park_one":
        if not _can(request, "check.park"):
            _say(request, False, "Parking checks is switched off for you.")
        else:
            from engines.check_rules import issue_check
            row = next(iter(_rows("SELECT due_date, principal + interest AS amount, status FROM loan_installments WHERE installment_id = ? AND loan_note_id = ?",
                                  (int(p.get("installment_id") or 0), lnid))), None)
            if not row or row["status"] == "Active":
                _say(request, False, "That payment already has its check.")
            else:
                ok, msg, _ = issue_check(int(note["creditor_id"]), round(float(row["amount"]), 2), date.fromisoformat(str(row["due_date"])[:10]),
                                         loan_note_id=lnid, installment_id=int(p.get("installment_id")))
                _say(request, ok, msg)
    elif what == "park_all":
        if not _can(request, "check.park"):
            _say(request, False, "Parking checks is switched off for you.")
        else:
            ok, msg, _ = ls.issue_all_pdcs(lnid)
            _say(request, ok, msg)
    else:
        _say(request, False, "Unknown action.")
    return redirect(back)


@page_needed("lenders")
def loan_note_pdf(request, lnid: int):
    from engines.loan_note_service import generate_loan_note_pdf
    data = generate_loan_note_pdf(int(lnid))
    if not data:
        raise Http404
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="loan_note_{lnid}.pdf"'
    return resp
