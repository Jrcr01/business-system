"""Reports -> Statement of account (owner, 2026-10-09: the reports reviewed and trimmed -- kept: the Schedule of payments and one Statement of account;
dropped: Cleared checks, Overview, Lists, the rundowns and the Decision reports, which the new pages cover; supplier aging moved to Analytics).
The rules: engines/account_statement.py."""
from __future__ import annotations

import io
from datetime import date

from django.http import HttpResponse
from django.shortcuts import render

from buying.views import _date


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    import sqlite3
    from engines.common import get_conn
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _who(kind: str) -> list[tuple[int, str]]:
    if kind == "supplier":
        rows = _rows("SELECT creditor_id AS id, creditor_name AS name FROM creditors WHERE creditor_type = 'SUPPLIER' ORDER BY is_active DESC, creditor_name")
    elif kind == "lender":
        rows = _rows("SELECT creditor_id AS id, creditor_name AS name FROM creditors WHERE creditor_type = 'LENDER' AND is_lessor = 0 ORDER BY creditor_name")
    elif kind == "bankloan":
        rows = _rows("""SELECT bl.bank_loan_id AS id, bl.loan_ref || ' · ' || c.creditor_name AS name FROM bank_loans bl JOIN creditors c ON c.creditor_id = bl.creditor_id
                        ORDER BY bl.is_cancelled, c.creditor_name, bl.loan_ref""")
    else:
        rows = _rows("""SELECT cc.card_id AS id, cc.card_name || ' - ' || cc.card_number_last4 || COALESCE(' · ' || ch.name, '') AS name FROM credit_cards cc
                        LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id ORDER BY cc.is_active DESC, ch.name, cc.card_name""")
    return [(r["id"], r["name"]) for r in rows]


def soa(request):
    from engines.account_statement import KINDS, ledger, pdf
    kind = request.GET.get("kind") if request.GET.get("kind") in KINDS else "supplier"
    who = _who(kind)
    pick = int(request.GET.get("id")) if (request.GET.get("id") or "").isdigit() else None
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    st = ledger(kind, pick, start, end) if pick else None
    what = request.GET.get("download")
    if st and what == "pdf":
        resp = HttpResponse(pdf(st), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="statement_of_account_{kind}_{pick}.pdf"'
        return resp
    if st and what == "xlsx":
        import pandas as pd
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            pd.DataFrame([{"Date": date.fromisoformat(x["day"]), "Document": x["doc"], "Reference": x["ref"], "Charge": x["charge"] or None, "Payment": x["payment"] or None,
                           "Balance": x["balance"]} for x in st["lines"]]).to_excel(xw, index=False, sheet_name="Statement of account")
            if st["waiting"]:
                pd.DataFrame([{"Dated": date.fromisoformat(x["day"]), "Check": x["doc"], "Reference": x["ref"], "Amount": x["amount"]} for x in st["waiting"]]
                             ).to_excel(xw, index=False, sheet_name="Waiting for the bank")
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="statement_of_account_{kind}_{pick}.xlsx"'
        return resp
    return render(request, "payables/report_soa.html", {"page_id": "reports", "r": "soa", "kinds": list(KINDS.items()), "kind": kind, "who": who, "pick": pick,
                                                        "start": start, "end": end, "st": st, "query": request.GET.urlencode()})
