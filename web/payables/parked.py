"""Parked checks (owner, 2026-10-18: "before I only use Chrysanth if I will print all the parked checks but now I have to go per batch"): every parked
check of every payment statement and loan on one page, ticked and written with Chrysanth in one run file -- the same as each statement's own button."""
from django.http import HttpResponse
from django.shortcuts import redirect, render

from masters.views import page_needed

from payables.views import _can, _date, _rows, _say

PARKED_SQL = """
    SELECT c.check_id, c.check_date, c.amount, c.bank_account_id, c.statement_id, c.loan_note_id, cr.creditor_name AS payee,
           os.statement_ref, ln.note_ref, COALESCE(b.bank_name, '') AS bank_name
    FROM checks_issued c
    JOIN creditors cr ON cr.creditor_id = c.creditor_id
    LEFT JOIN order_statements os ON os.statement_id = c.statement_id
    LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
    LEFT JOIN bank_accounts b ON b.bank_account_id = c.bank_account_id
    WHERE c.status = 'Active' AND c.printed_at IS NULL AND COALESCE(TRIM(c.check_number), '') = ''
      AND c.released_date IS NULL AND c.bank_cleared_date IS NULL AND c.manual_cleared_at IS NULL AND c.settled_outside_at IS NULL
      AND (c.statement_id IS NOT NULL OR c.loan_note_id IS NOT NULL)
"""


def parked_rows(up_to: str = "", bank: str = "") -> list[dict]:
    """The parked checks (no number, never sent to Chrysanth), oldest date first; up to a check date and one bank when asked."""
    where, params = "", []
    if up_to:
        where += " AND c.check_date <= ?"
        params.append(up_to)
    if bank.isdigit():
        where += " AND c.bank_account_id = ?"
        params.append(int(bank))
    return _rows(PARKED_SQL + where + " ORDER BY c.check_date, c.check_id", tuple(params))


def parked_count() -> int:
    return len(parked_rows())


@page_needed("order-statements")
def parked(request):
    may_write = _can(request, "check.issue")
    if request.method == "POST":
        back = "/order-statements/parked/?" + request.POST.get("back", "")
        if not may_write:
            _say(request, False, "Writing checks is switched off for you.")
            return redirect(back)
        ticked = [int(k[5:]) for k, v in request.POST.items() if k.startswith("tick_") and k[5:].isdigit() and v == "1"]
        allowed = {r["check_id"] for r in parked_rows()}
        ticked = [i for i in ticked if i in allowed]                 # only what is still parked (a second tab may have written some)
        if not ticked:
            _say(request, False, "Tick the parked checks to write.")
            return redirect(back)
        from engines.check_run_service import file_name_for_checks, generate_pending_check_run_txt
        data, problems, done = generate_pending_check_run_txt(ticked, log_batch=True)
        if not done:
            _say(request, False, "None of the ticked checks could go in the Chrysanth file: " + "; ".join(problems))
            return redirect(back)
        run = _rows("SELECT print_batch_id FROM checks_issued WHERE check_id = ?", (done[0],))[0]["print_batch_id"]
        resp = HttpResponse(data, content_type="text/plain")
        resp["Content-Disposition"] = f'attachment; filename="{file_name_for_checks(done, run)}"'
        return resp
    g = request.GET
    up_to = _date(g.get("upto")) if g.get("upto") else None
    bank = g.get("bank", "")
    rows = parked_rows(up_to.isoformat() if up_to else "", bank)
    groups, key = [], None
    for r in rows:
        r["for"] = r["statement_ref"] or (("Loan · " + r["note_ref"]) if r["note_ref"] else "Loan")
        r["link"] = f"/order-statements/{r['statement_id']}/?tab=checks" if r["statement_id"] else ""
    for r in sorted(rows, key=lambda x: (x["for"], str(x["check_date"]), x["check_id"])):
        k = (r["payee"], r["for"])
        if k != key:
            groups.append({"payee": r["payee"], "for": r["for"], "link": r["link"], "rows": []})
            key = k
        groups[-1]["rows"].append(r)
    groups.sort(key=lambda gr: str(gr["rows"][0]["check_date"]))
    banks = _rows("SELECT DISTINCT b.bank_account_id, b.bank_name FROM bank_accounts b JOIN checks_issued c ON c.bank_account_id = b.bank_account_id "
                  "WHERE c.status = 'Active' AND c.printed_at IS NULL AND COALESCE(TRIM(c.check_number), '') = '' ORDER BY b.bank_name")
    return render(request, "payables/parked_checks.html", {
        "page_id": "order-statements", "groups": groups, "n": len(rows), "total": sum(float(r["amount"] or 0) for r in rows),
        "upto": up_to.isoformat() if up_to else "", "bank": bank, "banks": banks, "may_write": may_write, "back": request.GET.urlencode()})
