"""Stage D3b (Payables bulk tools, docs/mockups/d3_payables.html screen 6, approved 2026-10-03): invoices from photos/PDFs (read offline by Tesseract) and from a
spreadsheet into one batch, many batches from one Excel workbook, many batches printed as one PDF, and the vendor SOA matcher.
Every tool reads first and shows what it found; nothing is saved until the final button, as in Streamlit. What was read waits on the server between the two
steps (a few rows can be more than a sign-in cookie holds), beside the database, and is removed when used or after a day."""
from __future__ import annotations

import io
import json
import secrets
import sqlite3
import time
from datetime import timedelta

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date, _suppliers, _uploads
from masters.views import page_needed
from payables.views import _batch_or_404, _batches, _num, _say


# --- what was read, waiting between "read" and "save" -------------------------------------------------------------------------------------------------

def _stage(data: dict) -> str:
    folder = _uploads()
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    (folder / f"{token}.json").write_text(json.dumps(data, default=str), encoding="utf-8")
    return token


def _staged(token: str) -> dict | None:
    if not token or not token.isalnum():
        return None
    p = _uploads() / f"{token}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _drop(token: str) -> None:
    if token and token.isalnum():
        (_uploads() / f"{token}.json").unlink(missing_ok=True)


# --- invoices into one batch: from photos / PDFs, or from a spreadsheet -----------------------------------------------------------------------------

@page_needed("order-statements")
def invoice_import(request, sid: int):
    from engines.statement_rules import resolve_due_date
    b = _batch_or_404(sid)
    back = f"/order-statements/{sid}/import/"
    if b["status"] == "Cancelled" or b["is_locked"]:
        _say(request, False, "This payment statement is closed or cancelled, so invoices cannot be added to it.")
        return redirect(f"/order-statements/{sid}/")
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "read_ocr":
            from engines.invoice_ocr_service import extract_invoice_fields
            files = request.FILES.getlist("files")
            if not files:
                _say(request, False, "Choose at least one photo or PDF.")
                return redirect(back)
            rows, notes, raw = [], [], {}
            for f in files[:40]:
                fields, message, raw_text = extract_invoice_fields(f.read(), f.name)
                if raw_text:
                    raw[f.name] = raw_text
                if fields:
                    rows.append({"source": f.name, "doc": fields.doc_ref or "", "date": str(fields.invoice_date or ""), "gross": fields.amount or 0, "less": 0})
                    if message:
                        notes.append(f"{f.name}: {message}")
                else:
                    notes.append(f"{f.name}: {message}")
            return redirect(back + "?staged=" + _stage({"sid": sid, "rows": rows, "notes": notes, "raw": raw, "kind": "photos"}))
        if what == "read_sheet":
            from engines.invoice_import_service import parse_invoice_spreadsheet
            f = request.FILES.get("file")
            if not f or not f.name.lower().endswith((".csv", ".xlsx")):
                _say(request, False, "Choose a .csv or .xlsx file.")
                return redirect(back)
            parsed, notes = parse_invoice_spreadsheet(f.read(), f.name)
            rows = [{"source": f.name, "doc": r.doc_ref, "dr": r.dr_no, "date": str(r.invoice_date), "gross": r.amount, "less": r.less_pct} for r in parsed]
            return redirect(back + "?staged=" + _stage({"sid": sid, "rows": rows, "notes": notes, "raw": {}, "kind": "spreadsheet"}))
        if what == "add":
            return _add_staged(request, b)
        if what == "discard":
            _drop(request.POST.get("staged", ""))
            return redirect(back)
    staged = _staged(request.GET.get("staged", ""))
    if staged and staged.get("sid") != sid:
        staged = None
    rows = []
    if staged:
        for i, r in enumerate(staged["rows"]):
            day = _date(r["date"])
            rows.append({**r, "i": i, "due": resolve_due_date(day, b["terms"] or "") if day else None})
    return render(request, "payables/invoice_import.html", {"page_id": "order-statements", "b": b, "staged": staged, "token": request.GET.get("staged", ""),
                                                           "rows": rows, "warnings": request.session.pop("bulk_warnings", None)})


def _add_staged(request, b: dict):
    from engines.db_engine import promote_draft_statement
    from engines.duplicate_service import invoice_warnings
    from engines.statement_rules import add_invoice, resolve_due_date
    p, sid, cid = request.POST, int(b["statement_id"]), int(b["creditor_id"])
    token = p.get("staged", "")
    staged = _staged(token)
    back = f"/order-statements/{sid}/import/"
    if not staged:
        _say(request, False, "What was read is gone (older than a day?). Read the files again.")
        return redirect(back)
    rows, skipped = [], []
    for i in range(len(staged["rows"])):
        if p.get(f"keep_{i}") != "1":
            continue
        from engines.statement_rules import doc_reference
        doc, day, gross, less = doc_reference(p.get(f"doc_{i}"), p.get(f"dr_{i}")), _date(p.get(f"date_{i}")), _num(p.get(f"gross_{i}"), 0), _num(p.get(f"less_{i}"), 0) or 0
        if not doc or not day or gross <= 0:
            skipped.append(f"Line {i + 1}: needs its sales invoice no., date and a positive amount.")
            continue
        rows.append((doc, day, gross, less, p.get(f"dr_{i}", "").strip()))
    if not rows:
        _say(request, False, "Nothing to add. " + " ".join(skipped))
        return redirect(back + f"?staged={token}")
    if p.get("confirmed") != "1":
        warns = invoice_warnings(cid, [(d, day, g) for d, day, g, _, _ in rows])
        if warns:
            staged["rows"] = [{"source": staged["rows"][i]["source"], "doc": p.get(f"doc_{i}", ""), "dr": p.get(f"dr_{i}", ""), "date": p.get(f"date_{i}", ""),
                               "gross": p.get(f"gross_{i}", ""), "less": p.get(f"less_{i}", ""), "keep": p.get(f"keep_{i}") == "1"} for i in range(len(staged["rows"]))]
            _drop(token)
            request.session["bulk_warnings"] = warns
            return redirect(back + "?staged=" + _stage(staged))
    added, errors = 0, list(skipped)
    for doc, day, gross, less, dr in rows:
        try:
            add_invoice(cid, doc, day, resolve_due_date(day, b["terms"] or ""), gross, [less] if less else [], sid, dr_no=dr)
            added += 1
        except sqlite3.IntegrityError:
            errors.append(f"{doc} already exists for this supplier.")
    _drop(token)
    note = ""
    if added and p.get("promote") == "1" and str(b["statement_ref"]).startswith("DRAFT-") and not errors:
        try:
            note = f" The payment statement is now {promote_draft_statement(sid, 'Issued')}."
        except ValueError as e:
            errors.append(f"The invoices were added, but the payment statement could not get its number: {e}")
    if added:
        messages.success(request, f"{added} sales invoice(s) added.{note}")
    for e in errors:
        messages.error(request, e)
    return redirect(f"/order-statements/{sid}/")


@page_needed("order-statements")
def invoice_template_csv(request):
    from engines.invoice_import_service import get_template_csv
    resp = HttpResponse(get_template_csv(), content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="invoice_template.csv"'
    return resp


# --- many batches from one Excel workbook (one sheet = one new batch) -------------------------------------------------------------------------------------

@page_needed("order-statements")
def import_many(request):
    from engines.invoice_import_service import match_supplier, parse_invoice_workbook
    suppliers = dict(_suppliers())
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "read":
            f = request.FILES.get("file")
            if not f or not f.name.lower().endswith((".xlsx", ".xlsm")):
                _say(request, False, "Choose the workbook (.xlsx or .xlsm).")
                return redirect("/order-statements/import-many/")
            sheets, problems = parse_invoice_workbook(f.read())
            for pr in problems:
                messages.error(request, pr)
            data = {"sheets": [{"name": sh.sheet_name, "messages": sh.messages,
                                "rows": [{"doc": r.doc_ref, "dr": r.dr_no, "date": str(r.invoice_date), "amount": r.amount, "less": r.less_pct} for r in sh.rows]} for sh in sheets]}
            return redirect("/order-statements/import-many/" + ("?staged=" + _stage(data) if sheets else ""))
        if what == "create":
            return _create_many(request, suppliers)
    staged = _staged(request.GET.get("staged", ""))
    plan = []
    if staged and "sheets" in staged:
        for i, sh in enumerate(staged["sheets"]):
            good = [r for r in sh["rows"] if (str(r["doc"]).strip() or str(r.get("dr") or "").strip()) and float(r["amount"] or 0) > 0]
            plan.append({"i": i, "name": sh["name"], "good": len(good), "total": sum(float(r["amount"]) for r in good), "bad": len(sh["rows"]) - len(good),
                         "messages": sh["messages"][:3], "guess": match_supplier(sh["name"], suppliers)})
    results = _staged(request.GET.get("done", ""))
    return render(request, "payables/import_many.html", {"page_id": "order-statements", "suppliers": sorted(suppliers.items(), key=lambda x: str(x[1]).lower()),
                                                        "plan": plan, "token": request.GET.get("staged", ""), "results": (results or {}).get("results")})


def _create_many(request, suppliers: dict):
    from engines.db_engine import promote_draft_statement
    from engines.statement_rules import add_invoice, create_order_statement, resolve_due_date
    p = request.POST
    staged = _staged(p.get("staged", ""))
    if not staged:
        _say(request, False, "What was read is gone (older than a day?). Read the workbook again.")
        return redirect("/order-statements/import-many/")
    results = []
    for i, sh in enumerate(staged["sheets"]):
        pick = p.get(f"sup_{i}", "")
        if not pick.isdigit() or int(pick) not in suppliers:
            continue
        cid = int(pick)
        rows = [r for r in sh["rows"] if (str(r["doc"]).strip() or str(r.get("dr") or "").strip()) and float(r["amount"] or 0) > 0]
        if not rows:
            continue
        from engines.common import get_conn
        with get_conn() as conn:
            terms = (conn.execute("SELECT terms FROM creditors WHERE creditor_id = ?", (cid,)).fetchone() or [""])[0] or ""
        sid = create_order_statement(cid)
        added, skipped = 0, []
        for r in rows:
            day = _date(r["date"])
            try:
                add_invoice(cid, __import__("engines.statement_rules", fromlist=["x"]).doc_reference(r["doc"], r.get("dr")), day, resolve_due_date(day, terms), float(r["amount"]), [float(r["less"])] if r["less"] else [], sid,
                            dr_no=str(r.get("dr") or "").strip())
                added += 1
            except sqlite3.IntegrityError:
                skipped.append(str(r["doc"]).strip())
        ref = "Draft"
        if p.get("promote") == "1" and added:
            try:
                ref = promote_draft_statement(sid, "Issued")
            except ValueError:
                pass
        results.append({"sheet": sh["name"], "supplier": suppliers[cid], "sid": sid, "ref": ref, "added": added, "skipped": ", ".join(skipped)})
    _drop(p.get("staged", ""))
    if not results:
        _say(request, False, "No sheet had a supplier picked, so nothing was created.")
        return redirect("/order-statements/import-many/")
    _say(request, True, f"{len(results)} payment statement(s) created.")
    return redirect("/order-statements/import-many/?done=" + _stage({"results": results}))


@page_needed("order-statements")
def entry_workbook(request):
    from engines.clock import manila_today
    from engines.invoice_template_service import build_invoice_entry_workbook
    resp = HttpResponse(build_invoice_entry_workbook(), content_type="application/vnd.ms-excel.sheet.macroEnabled.12")
    resp["Content-Disposition"] = f'attachment; filename="invoice_entry_{manila_today():%Y-%m-%d}.xlsm"'
    return resp


# --- many batches printed as one PDF -----------------------------------------------------------------------------------------------------------------------

SCOPES = [("since", "Since the last check print"), ("today", "Today"), ("7", "Last 7 days"), ("all", "All")]


def _last_print_day() -> str | None:
    from engines.common import get_conn
    with get_conn() as conn:
        v = conn.execute("SELECT DATE(MAX(printed_at), '+8 hours') FROM checks_issued").fetchone()[0]
    return str(v)[:10] if v else None


@page_needed("order-statements")
def print_many(request):
    from engines.batch_statement_service import bulk_summary_rows
    from engines.clock import manila_today
    g = request.GET
    scope = g.get("scope", "since") if g.get("scope") in dict(SCOPES) else "since"
    last = _last_print_day()
    since = {"since": f"{last} 00:00:00" if last else "0000-00-00", "today": f"{manila_today():%Y-%m-%d} 00:00:00",
             "7": f"{manila_today() - timedelta(days=6):%Y-%m-%d} 00:00:00", "all": "0000-00-00"}[scope]
    pool = [r for r in _batches() if r["stage"] != "Cancelled" and str(r["last_modified"]) >= since]
    pool.sort(key=lambda r: str(r["statement_ref"]))
    if g.get("go") == "1":
        ids = [int(x) for x in g.getlist("ids") if x.isdigit()]
        if not ids:
            _say(request, False, "Tick at least one payment statement.")
            return redirect(f"/order-statements/print-many/?scope={scope}")
        return _merged_pdf(request, ids, scope, last)
    summary = bulk_summary_rows([int(r["statement_id"]) for r in pool], True) if pool else []
    return render(request, "payables/print_many.html", {"page_id": "order-statements", "scopes": SCOPES, "scope": scope, "last": last, "pool": pool,
                                                       "summary_total": sum(r["net_total"] for r in summary), "n_checks": sum(r["checks"] for r in summary)})


def _merged_pdf(request, ids: list[int], scope: str, last: str | None):
    from pypdf import PdfReader, PdfWriter

    from engines.batch_statement_service import bulk_summary_rows, generate_batch_statement_pdf, generate_batch_statement_thermal_pdf, generate_bulk_summary_pdf
    from engines.clock import manila_now
    g = request.GET
    a4, history, unprinted = g.get("type", "a4") != "thermal", g.get("history") == "1", g.get("unprinted") == "1"
    copies = g.get("copies", "both") if g.get("copies") in ("both", "filing", "supplier") else "both"
    merged, made = PdfWriter(), 0
    if not a4:                                                   # owner, 2026-10-14: one continuous strip, a cut line between statements; no letter-size summary page
        from engines.batch_statement_service import generate_thermal_many_pdf
        strip = generate_thermal_many_pdf(ids, history, unprinted, qr_base=f"{request.scheme}://{request.get_host()}")
        if not strip:
            raise Http404
        resp = HttpResponse(strip, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="statements_{manila_now():%Y%m%d_%H%M}_thermal.pdf"'
        return resp
    if g.get("summary") == "1":
        rows = bulk_summary_rows(ids, unprinted)
        if rows:
            scope_text = f"Since last check print ({last})" if scope == "since" and last else dict(SCOPES)[scope]
            type_text = ("Payment statement (short bond)" + f" · {dict(both='both copies', filing='filing copy', supplier='supplier copy')[copies]}" if a4 else "Thermal 80mm") \
                + ("" if unprinted else " · printed checks only")
            for page in PdfReader(io.BytesIO(generate_bulk_summary_pdf(rows, scope_text, type_text))).pages:
                merged.add_page(page)
    site = f"{request.scheme}://{request.get_host()}"
    for sid in ids:
        one = generate_batch_statement_pdf(sid, history, copies, g.get("apply") == "1", unprinted, qr_base=site) if a4 else generate_batch_statement_thermal_pdf(sid, history, unprinted, qr_base=site)
        if not one:
            continue
        for page in PdfReader(io.BytesIO(one)).pages:
            merged.add_page(page)
        made += 1
    if not made:
        raise Http404
    out = io.BytesIO()
    merged.write(out)
    resp = HttpResponse(out.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="statements_{manila_now():%Y%m%d_%H%M}{"" if a4 else "_thermal"}.pdf"'
    return resp


# --- vendor SOA matcher ------------------------------------------------------------------------------------------------------------------------------------

@page_needed("order-statements")
def soa(request):
    from engines.soa_matcher import reconcile_vendor_soa
    suppliers = _suppliers()
    result, cid = None, int(request.POST.get("s") or request.GET.get("s") or 0)
    if request.method == "POST":
        f = request.FILES.get("file")
        if not f or cid not in dict(suppliers):
            _say(request, False, "Pick the supplier and choose its statement of account.")
            return redirect("/order-statements/soa/")
        try:
            result = reconcile_vendor_soa(cid, f.read(), f.name)
        except Exception as e:                               # noqa: BLE001 -- a file it cannot read is reported, never a crash page
            result = {"success": False, "error": f"Could not process this file: {e}"}
        if result.get("success"):
            for key in ("matched", "missing_in_ap", "amount_mismatches", "omitted_from_soa"):
                df = result[key]
                result[key] = {"cols": [str(c) for c in df.columns], "rows": df.astype(str).values.tolist()}
    return render(request, "payables/soa.html", {"page_id": "order-statements", "suppliers": suppliers, "cid": cid, "result": result})


# --- supplier statement check: tick their paper against our records (owner, 2026-10-13) --------------------------------------------------------------------

def _with_rows(result: dict) -> dict:
    for row in result["summary"]:
        row["rows"] = result["groups"][row["key"]]
    return result


@page_needed("order-statements")
def statement_check(request):
    """A supplier's statement arrives as a photo or on paper, each supplier's in its own layout; every line is a document number, a date and an
    amount. The page lists what OUR records say we owe; the person ticks what is on the paper and types what we do not have. It only compares."""
    from engines import statement_check as sc
    suppliers = _suppliers()
    cid = int(request.POST.get("s") or request.GET.get("s") or 0)
    if cid not in dict(suppliers):
        return render(request, "payables/statement_check.html", {"page_id": "order-statements", "suppliers": suppliers, "cid": 0, "history": sc.history()})
    ours, ctx = sc.our_lines(cid), {"page_id": "order-statements", "suppliers": suppliers, "cid": cid, "supplier": dict(suppliers)[cid], "typed": []}
    if request.method == "POST":
        p = request.POST
        ticked = {r["invoice_id"]: sc._number(p.get(f"a_{r['invoice_id']}")) for r in ours if p.get(f"t_{r['invoice_id']}") == "1"}
        typed = [t for t in zip(p.getlist("x_date"), p.getlist("x_ref"), p.getlist("x_amount")) if t[1].strip() or t[2].strip()]
        result = sc.compare(cid, ticked, typed, p.get("their_total"))
        if p.get("action") == "save":
            ok, msg, check_id = sc.save(cid, p.get("statement_date", ""), result)
            _say(request, ok, msg)
            if ok:
                return redirect(f"/order-statements/check/{check_id}/")
        for r in ours:                                           # the form as it was typed, under the result
            r["ticked"] = r["invoice_id"] in ticked
            r["theirs"] = p.get(f"a_{r['invoice_id']}", "")
        ctx.update({"result": _with_rows(result), "typed": typed, "statement_date": p.get("statement_date", ""), "their_total": p.get("their_total", "")})
    ctx.update({"ours": ours, "ours_total": sum(r["amount"] for r in ours), "history": sc.history(cid)})
    return render(request, "payables/statement_check.html", ctx)


@page_needed("order-statements")
def statement_check_saved(request, check_id: int):
    from engines import statement_check as sc
    kept = sc.saved(check_id)
    if not kept:
        raise Http404
    result = _with_rows({"groups": kept["groups"], "summary": kept["summary"], "their_total": kept["their_total"], "found": kept["found"], "left": kept["left"],
                         "ours_total": kept["ours_total"]})
    return render(request, "payables/statement_check.html", {"page_id": "order-statements", "kept": kept, "result": result, "statement_date": kept["statement_date"], "cid": 0})
