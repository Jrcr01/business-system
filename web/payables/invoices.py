"""Pay suppliers > Invoices (owner, 2026-10-13: "invoices can be input seperately not thru batch or order statement. then in making checks i'll just
pullup the floating invoices"). An invoice is a payable the moment it is recorded here; a payment statement is made only when it is paid: tick
the invoices, Make checks, and one statement per supplier is made at that moment. Open to everyone who may open Payment statements."""
from __future__ import annotations

import sqlite3

from django.shortcuts import redirect, render

from buying.views import _date, _suppliers
from masters.views import page_needed
from payables.views import _num, _rows, _say, _stamp

HERE = "/order-statements/invoices/"


def _floating() -> list[dict]:
    """Every invoice in no payment statement yet (not cancelled, not an opening balance, not paid by card), by supplier then date."""
    from engines.statement_rules import parse_less_pct
    rows = _rows("""SELECT i.invoice_id, i.creditor_id, c.creditor_name AS supplier, c.terms, i.doc_ref, i.dr_no, i.invoice_date, i.due_date, i.gross_amount,
                           i.trade_discounts, i.amount, (SELECT p.po_no FROM po_receipts r JOIN purchase_orders p ON p.po_id = r.po_id WHERE r.invoice_id = i.invoice_id LIMIT 1) AS po_no
                    FROM invoices i JOIN creditors c ON c.creditor_id = i.creditor_id
                    WHERE i.statement_id IS NULL AND i.status != 'Cancelled' AND i.is_opening_balance = 0 AND i.paid_via_card_id IS NULL
                    ORDER BY c.creditor_name COLLATE NOCASE, i.invoice_date, i.invoice_id""")
    for r in rows:
        r["less"] = parse_less_pct(r["trade_discounts"])
        r["stamp"] = _stamp(r["doc_ref"], r["dr_no"], str(r["invoice_date"])[:10], r["gross_amount"], r["amount"])
        dr, doc = str(r["dr_no"] or "").strip(), str(r["doc_ref"]).strip()
        r["ref"] = f"{dr} · SI {doc}" if dr and dr.upper() not in doc.upper() else doc       # the DR was the only number given: it is the reference itself
    return rows


@page_needed("order-statements")
def invoices(request):
    from engines.duplicate_service import invoice_warnings
    from engines.statement_rules import add_invoice, delete_invoice, doc_reference, edit_invoice, pay_invoices, resolve_due_date
    suppliers = _suppliers()
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action", "")
        if what == "add":
            cid = int(p.get("s") or 0)
            doc, day, gross, less = doc_reference(p.get("doc"), p.get("dr")), _date(p.get("date")), _num(p.get("gross"), 0), _num(p.get("less"), 0) or 0
            typed = {k: p.get(k, "") for k in ("s", "doc", "dr", "date", "gross", "less")}
            if cid not in dict(suppliers):
                _say(request, False, "Pick the supplier.")
            elif not doc or not day or gross <= 0:
                _say(request, False, "An invoice needs its SI no. or DR no., the date received and a positive amount.")
            elif not 0 <= less <= 100:
                _say(request, False, "Less % must be between 0 and 100.")
            else:
                warns = [] if p.get("confirmed") == "1" else invoice_warnings(cid, [(doc, day, gross)])
                if warns:
                    request.session["finv_warnings"], request.session["finv_typed"] = warns, typed
                    return redirect(HERE)
                terms = (_rows("SELECT terms FROM creditors WHERE creditor_id = ?", (cid,)) or [{"terms": ""}])[0]["terms"] or ""
                try:
                    net, _, _ = add_invoice(cid, doc, day, resolve_due_date(day, terms), gross, [less] if less else [], None, dr_no=p.get("dr", ""))
                    _say(request, True, f"Invoice {doc} recorded (net ₱{net:,.2f}). It is owed from now; it waits here until you pay it.")
                    request.session["finv_typed"] = {"s": str(cid)}          # the same supplier is offered for the next one
                    return redirect(HERE)
                except sqlite3.IntegrityError:
                    _say(request, False, f"Invoice {doc} already exists for this supplier.")
            request.session["finv_typed"] = typed
            return redirect(HERE)
        if what == "pay":
            ok, msg, made = pay_invoices([int(x) for x in p.getlist("pay") if x.isdigit()])
            _say(request, ok, msg)
            if ok and len(made) == 1:
                return redirect(f"/order-statements/{made[0]}/?tab=checks")
            return redirect("/order-statements/?stage=Draft" if ok else HERE)
        if what in ("save", "delete"):
            inv_id = int(p.get("invoice_id") or 0)
            now = next((r for r in _floating() if r["invoice_id"] == inv_id), None)
            if now is None or now["stamp"] != p.get("stamp"):
                _say(request, False, "Someone else changed, paid or removed this invoice after you opened it. Nothing was saved; this is the current list.")
                return redirect(HERE)
            if what == "delete":
                _say(request, *delete_invoice(inv_id))
                return redirect(HERE)
            day, gross, less = _date(p.get("date")), _num(p.get("gross"), 0), _num(p.get("less"), 0) or 0
            ref = doc_reference(p.get("doc"), p.get("dr"))
            if not day or not ref:
                _say(request, False, "An invoice needs its SI no. or DR no. and the date received.")
                return redirect(HERE + f"?edit={inv_id}#edit")
            ok, msg = edit_invoice(inv_id, ref, day, resolve_due_date(day, now["terms"] or ""), gross, less, dr_no=p.get("dr", ""))
            _say(request, ok, msg)
            return redirect(HERE if ok else HERE + f"?edit={inv_id}#edit")
        _say(request, False, "Unknown action.")
        return redirect(HERE)
    rows = _floating()
    groups: list[dict] = []
    for r in rows:
        if not groups or groups[-1]["creditor_id"] != r["creditor_id"]:
            groups.append({"creditor_id": r["creditor_id"], "supplier": r["supplier"], "terms": r["terms"], "rows": [], "total": 0.0})
        groups[-1]["rows"].append(r)
        groups[-1]["total"] += float(r["amount"] or 0)
    one = int(request.GET.get("edit") or 0) if (request.GET.get("edit") or "").isdigit() else 0
    return render(request, "payables/invoices.html", {"page_id": "order-statements", "suppliers": suppliers, "groups": groups, "n": len(rows),
                                                      "total": sum(float(r["amount"] or 0) for r in rows), "edit": next((r for r in rows if r["invoice_id"] == one), None),
                                                      "typed": request.session.pop("finv_typed", None) or {}, "warnings": request.session.pop("finv_warnings", None)})
