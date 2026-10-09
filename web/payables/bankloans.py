"""Other payables -> Bank loans in Django (owner, 2026-10-09; mock-up A approved): the same look as Lenders -- a "New bank loan for" bar, one list of every
bank loan with what is still to pay, and a loan page with its schedule (paid = on the bank statement or ticked on Daily payables, never by its date), its
details, and a schedule file import. The rules: engines/bank_loan_service.py, loan_edit_service.py, bank_loan_import_service.py, clearing.py."""
from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date
from masters.views import page_needed


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _num(text, default=None):
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    import sqlite3
    from engines.common import get_conn
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _can(request, action: str) -> bool:
    from engines.permissions import can
    return can(request.slicks_user["role"], action)


def _owed(alias: str) -> str:
    from engines.clearing import bank_loan_owed_sql
    return bank_loan_owed_sql(alias)


@page_needed("bank-loans")
def bank_loans(request):
    if request.method == "POST":
        return _create(request)
    banks = _rows("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'BANK_LOAN' AND is_active = 1 ORDER BY creditor_name")
    who, status, q = request.GET.get("s", ""), request.GET.get("status", "active"), request.GET.get("q", "").strip()
    where, params = [], [date.today().isoformat()]
    if status == "active":
        where.append("bl.is_cancelled = 0 AND bl.status = 'Active'")
    elif status == "closed":
        where.append("bl.is_cancelled = 0 AND bl.status = 'Closed'")
    elif status == "cancelled":
        where.append("bl.is_cancelled = 1")
    if who.isdigit():
        where.append("bl.creditor_id = ?"); params.append(int(who))
    if q:
        where.append("(bl.loan_ref LIKE ? OR bl.account_number LIKE ?)"); params += [f"%{q}%", f"%{q}%"]
    rows = _rows(f"""SELECT bl.bank_loan_id, bl.loan_ref, c.creditor_name, lb.name AS beneficiary, bl.principal_amount, bl.mode_of_payment, bl.is_beginning_balance,
                            CASE WHEN bl.is_cancelled = 1 THEN 'Cancelled' ELSE bl.status END AS status,
                            CASE WHEN bl.is_cancelled = 1 THEN 0 ELSE COALESCE((SELECT SUM(i.amount) FROM bank_loan_installments i
                                 WHERE i.bank_loan_id = bl.bank_loan_id AND {_owed('i')}), 0) END AS still_to_pay,
                            (SELECT MIN(i.due_date) FROM bank_loan_installments i WHERE i.bank_loan_id = bl.bank_loan_id AND i.due_date >= ? AND {_owed('i')}) AS next_due
                     FROM bank_loans bl JOIN creditors c ON c.creditor_id = bl.creditor_id LEFT JOIN loan_beneficiaries lb ON lb.beneficiary_id = bl.beneficiary_id
                     {"WHERE " + " AND ".join(where) if where else ""} ORDER BY c.creditor_name, bl.loan_ref""", tuple(params))
    ctx = {"page_id": "bank-loans", "rows": rows, "banks": banks, "who": who, "status": status, "q": q,
           "statuses": [("active", "Active"), ("closed", "Closed"), ("cancelled", "Cancelled"), ("", "All")],
           "still_to_pay": sum(float(r["still_to_pay"] or 0) for r in rows), "new": bool(request.GET.get("new")), "pick": request.GET.get("bank", ""),
           "beneficiaries": _rows("SELECT beneficiary_id, name, beneficiary_type FROM loan_beneficiaries ORDER BY name"),
           "may_write": _can(request, "loans.use"), "today": date.today(), "typed": request.session.pop("bl_typed", None)}
    return render(request, "payables/bank_loans.html", ctx)


def _create(request):
    p = request.POST
    if not _can(request, "loans.use"):
        _say(request, False, "Loan work is switched off for you.")
        return redirect("/bank-loans/")
    if p.get("action") == "beneficiary":
        from engines.bank_loan_service import add_loan_beneficiary
        name = " ".join(p.get("b_name", "").split())
        if not name:
            _say(request, False, "Type the name.")
        else:
            add_loan_beneficiary(name, p.get("b_type", "Person"), p.get("b_contact", ""))
            _say(request, True, f"{name} added: pick them in 'Attributable to'.")
        request.session["bl_typed"] = {k: v for k, v in p.items() if not k.startswith("b_") and k != "csrfmiddlewaretoken"}
        return redirect("/bank-loans/?new=1#new")
    import sqlite3
    from engines.bank_loan_service import create_bank_loan
    bank, ref = int(p.get("bank") or 0), " ".join(p.get("ref", "").split())
    start, maturity, repricing = _date(p.get("start")), _date(p.get("maturity")), _date(p.get("repricing"))
    principal = _num(p.get("principal"))
    if not bank or not ref or not start or not maturity or principal is None:
        _say(request, False, "Give the bank, the loan reference, the start and maturity dates and the principal.")
        request.session["bl_typed"] = dict(p.items())
        return redirect("/bank-loans/?new=1#new")
    try:
        blid = create_bank_loan(bank, ref, p.get("account", "").strip(), start, maturity, repricing, principal, p.get("mode", "ADA").strip() or "ADA",
                                int(p.get("beneficiary")) if (p.get("beneficiary") or "").isdigit() else None)
    except sqlite3.IntegrityError:
        _say(request, False, "This loan reference already exists.")
        request.session["bl_typed"] = dict(p.items())
        return redirect("/bank-loans/?new=1#new")
    except ValueError as e:
        _say(request, False, str(e))
        request.session["bl_typed"] = dict(p.items())
        return redirect("/bank-loans/?new=1#new")
    _say(request, True, f"Bank loan {ref} created. Now enter its repayment schedule.")
    return redirect(f"/bank-loans/{blid}/")


def _loan(blid: int) -> dict | None:
    return next(iter(_rows("""SELECT bl.*, c.creditor_name, lb.name AS beneficiary, lb.beneficiary_type FROM bank_loans bl JOIN creditors c ON c.creditor_id = bl.creditor_id
                             LEFT JOIN loan_beneficiaries lb ON lb.beneficiary_id = bl.beneficiary_id WHERE bl.bank_loan_id = ?""", (blid,))), None)


@page_needed("bank-loans")
def bank_loan(request, blid: int):
    loan = _loan(blid)
    if not loan:
        raise Http404
    if request.method == "POST":
        return _loan_post(request, loan)
    tab = request.GET.get("tab", "schedule")
    inst = _rows("""SELECT i.installment_id, i.installment_no, i.due_date, i.amount, i.interest_portion, i.cleared_at, i.cleared_note,
                           (SELECT MIN(COALESCE(t.posting_date, t.txn_date)) FROM bank_transactions t WHERE t.matched_installment_id = i.installment_id) AS bank_date
                    FROM bank_loan_installments i WHERE i.bank_loan_id = ? ORDER BY i.due_date, i.installment_id""", (blid,))
    for r in inst:
        r["paid"] = ("bank " + str(r["bank_date"])[:10]) if r["bank_date"] else (("ticked " + str(r["cleared_at"])[:10]) if r["cleared_at"] else "")
    ctx = {"page_id": "bank-loans", "l": loan, "tab": tab, "inst": inst, "edit": int(request.GET.get("edit") or 0),
           "total": sum(float(r["amount"]) for r in inst), "still": sum(float(r["amount"]) for r in inst if not r["paid"]) if not loan["is_cancelled"] else 0.0,
           "interest": sum(float(r["interest_portion"] or 0) for r in inst),
           "banks": _rows("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'BANK_LOAN' ORDER BY creditor_name"),
           "beneficiaries": _rows("SELECT beneficiary_id, name, beneficiary_type FROM loan_beneficiaries ORDER BY name"),
           "may_write": _can(request, "loans.use") and not loan["is_cancelled"], "staged": request.session.get(f"bl_import_{blid}"), "today": date.today()}
    return render(request, "payables/bank_loan.html", ctx)


def _loan_post(request, loan: dict):
    from engines import bank_loan_service as bls
    p, what, blid = request.POST, request.POST.get("action", ""), int(loan["bank_loan_id"])
    back = f"/bank-loans/{blid}/"
    if not _can(request, "loans.use"):
        _say(request, False, "Loan work is switched off for you.")
        return redirect(back)
    if what == "add":
        day, amount = _date(p.get("due")), _num(p.get("amount"))
        if not day or amount is None:
            _say(request, False, "Give the due date and the amount.")
        else:
            _say(request, *bls.add_bank_loan_installment(blid, day, amount, _num(p.get("interest"), 0.0) or 0.0))
    elif what == "fill":
        day, n, amount = _date(p.get("first_due")), int(_num(p.get("n"), 0) or 0), _num(p.get("monthly"), 0.0) or 0.0
        if not day:
            _say(request, False, "Give the first due date.")
        else:
            _say(request, *bls.fill_flat_schedule(blid, day, n, amount, _num(p.get("interest"), 0.0) or 0.0))
    elif what == "edit":
        day, amount = _date(p.get("due")), _num(p.get("amount"))
        if not day or amount is None:
            _say(request, False, "Give the due date and the amount.")
        else:
            _say(request, *bls.edit_bank_loan_installment(int(p.get("installment_id") or 0), day, amount, _num(p.get("interest"), 0.0) or 0.0))
    elif what == "delete_row":
        _say(request, *bls.delete_bank_loan_installment(int(p.get("installment_id") or 0)))
    elif what == "details":
        from engines.loan_edit_service import edit_bank_loan_details
        principal = _num(p.get("principal"))
        _say(request, *edit_bank_loan_details(blid, int(p.get("bank") or loan["creditor_id"]), p.get("ref", ""), p.get("account", "").strip() or None,
                                              _date(p.get("start")), _date(p.get("maturity")), _date(p.get("repricing")), principal or None,
                                              p.get("mode", "ADA").strip() or "ADA", p.get("amort") == "1"))
        bls.edit_bank_loan_beneficiary(blid, int(p.get("beneficiary")) if (p.get("beneficiary") or "").isdigit() else None)
        back += "?tab=details"
    elif what == "parse":
        from engines.bank_loan_import_service import parse_bank_loan_spreadsheet
        f = request.FILES.get("file")
        if not f:
            _say(request, False, "Choose the bank's schedule file (Excel or CSV).")
        else:
            rows, notes = parse_bank_loan_spreadsheet(f.read(), f.name)
            request.session[f"bl_import_{blid}"] = {"rows": [{"due": str(r.due_date), "amount": float(r.amount)} for r in rows], "notes": list(notes), "file": f.name}
            _say(request, bool(rows), f"{len(rows)} row(s) read from {f.name}." + (" " + " ".join(notes) if notes else ""))
        back += "?tab=import"
    elif what == "import":
        staged = request.session.pop(f"bl_import_{blid}", None) or {"rows": []}
        done = 0
        for r in staged["rows"]:
            ok, _ = bls.add_bank_loan_installment(blid, date.fromisoformat(r["due"]), float(r["amount"] or 0))
            done += ok
        _say(request, bool(done), f"{done} installment(s) added to the schedule.")
    elif what == "discard":
        request.session.pop(f"bl_import_{blid}", None)
        back += "?tab=import"
    elif what == "cancel":
        _say(request, *bls.cancel_bank_loan(blid))
    elif what == "delete":
        ok, msg = bls.delete_bank_loan(blid)
        _say(request, ok, msg)
        if ok:
            return redirect("/bank-loans/")
    elif what == "unlabel":
        from engines.beginning_balance_service import remove_beginning_label
        _say(request, *remove_beginning_label("bank_loan", blid))
    else:
        _say(request, False, "Unknown action.")
    return redirect(back)


@page_needed("bank-loans")
def bank_loan_pdf(request, blid: int):
    from engines.bank_loan_report_service import generate_bank_loan_pdf
    data = generate_bank_loan_pdf(int(blid))
    if not data:
        raise Http404
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="bank_loan_{blid}.pdf"'
    return resp


@page_needed("bank-loans")
def schedule_template(request):
    from engines.bank_loan_import_service import get_template_csv
    resp = HttpResponse(get_template_csv(), content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="bank_loan_schedule_template.csv"'
    return resp
