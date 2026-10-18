"""Set-up, combined (owner, 2026-10-09; mock-up approved): five cards became three -- Creditors, Settings and Checks and review.
  * Settings gains two tabs, the System admin's alone: Audit log (every change, who and when) and Backup (make a copy, list and download them; a restore
    stays a server step, never a button).
  * Checks and review: the Review list (what the system flagged; tick it reviewed, with a note), Data checks (the Data health findings; "it's fine" /
    "show again") and Possible duplicates (moved from the old Reports)."""
from __future__ import annotations

import os
from datetime import date, datetime

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render

from buying.views import _date
from masters.views import _admin_only, _settings_tabs, page_needed


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    import sqlite3
    from engines.common import get_conn
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ------------------------------------------------------------------ Settings -> Audit log
@page_needed("settings")
@_admin_only
def settings_audit(request):
    who, what, q = request.GET.get("who", ""), request.GET.get("what", ""), request.GET.get("q", "").strip()
    start, end = _date(request.GET.get("from")), _date(request.GET.get("to"))
    where, params = [], []
    if who:
        where.append("COALESCE(user_email, '') = ?"); params.append(who)
    if what:
        where.append("user_action = ?"); params.append(what)
    if start:
        where.append("date(timestamp, '+8 hours') >= ?"); params.append(str(start))
    if end:
        where.append("date(timestamp, '+8 hours') <= ?"); params.append(str(end))
    if q:
        where.append("(COALESCE(new_values, '') LIKE ? OR COALESCE(old_values, '') LIKE ? OR entity_name LIKE ?)"); params += [f"%{q}%"] * 3
    sql = (f"""SELECT log_id, datetime(timestamp, '+8 hours') AS at, COALESCE(user_email, '') AS who, user_action, entity_name, entity_id,
                      COALESCE(new_values, old_values, '') AS details FROM audit_logs {"WHERE " + " AND ".join(where) if where else ""}
               ORDER BY log_id DESC LIMIT ?""")
    if request.GET.get("download") == "xlsx":
        import io
        import pandas as pd
        rows = _rows(sql, tuple(params) + (50000,))
        buf = io.BytesIO()
        pd.DataFrame([{"When": r["at"], "Who": r["who"], "What": r["user_action"], "On": r["entity_name"], "Id": r["entity_id"], "Details": r["details"]} for r in rows]
                     ).to_excel(buf, index=False, sheet_name="Audit log")
        resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="audit_log_{date.today():%Y%m%d}.xlsx"'
        return resp
    names = {r["email"].lower(): r["name"] for r in _rows("SELECT lower(email) AS email, name FROM app_users")}
    rows = _rows(sql, tuple(params) + (300,))
    for r in rows:
        r["who_name"] = names.get(r["who"].lower(), r["who"] or "—")
        r["what"] = r["user_action"].replace("_", " ").capitalize()
    return render(request, "masters/settings_audit.html", {
        "page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/audit/", "rows": rows, "q": q, "who": who, "what": what,
        "start": start, "end": end, "query": request.GET.urlencode(),
        "people": _rows("SELECT DISTINCT COALESCE(user_email, '') AS e FROM audit_logs WHERE user_email IS NOT NULL ORDER BY 1"),
        "actions": _rows("SELECT DISTINCT user_action AS a FROM audit_logs ORDER BY 1"), "names": names})


# ------------------------------------------------------------------ Settings -> Backup
def _backups() -> tuple[str, list[dict]]:
    from db_location import backup_dir
    folder = backup_dir()
    out = []
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if f.lower().endswith(".db") and os.path.isfile(p):
                st = os.stat(p)
                out.append({"name": f, "size_mb": st.st_size / 1_048_576, "at": datetime.fromtimestamp(st.st_mtime)})
    out.sort(key=lambda x: x["at"], reverse=True)
    return folder, out


@page_needed("settings")
@_admin_only
def settings_backup(request):
    folder, files = _backups()
    if request.method == "POST":
        import engines.db_engine as dbe
        from db_location import safe_copy
        os.makedirs(folder, exist_ok=True)
        name = f"manual_{datetime.now():%Y%m%d_%H%M%S}.db"
        try:
            safe_copy(dbe.DB_PATH, os.path.join(folder, name))
            from engines.common import get_conn
            with get_conn() as conn:
                conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES (acting_user(), 'BACKUP_MADE', 'database', ?)", (name,))
            messages.success(request, f"Copy made: {name}.")
        except Exception as e:                                       # noqa: BLE001 -- a full disk must not take the page down
            messages.error(request, f"The copy could not be made: {e}")
        return redirect("/settings/backup/")
    want = request.GET.get("download", "")
    if want:
        hit = next((f for f in files if f["name"] == want), None)          # only a file from the list: never a path from outside
        if not hit:
            raise Http404
        return FileResponse(open(os.path.join(folder, hit["name"]), "rb"), as_attachment=True, filename=hit["name"])
    return render(request, "masters/settings_backup.html", {"page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/backup/",
                                                            "files": files[:60], "count": len(files), "folder": folder})


# ------------------------------------------------------------------ Checks and review
REF_URL = {"checks_issued": lambda i: _check_url(i), "cc_statements": lambda i: _card_url(i), "check_booklets": lambda i: "/bank-cash/?tab=booklets",
           "purchase_orders": lambda i: f"/purchase-orders/{i}/", "order_statements": lambda i: f"/order-statements/{i}/", "invoices": lambda i: _invoice_url(i),
           "loan_notes": lambda i: f"/lenders/{i}/", "bank_loans": lambda i: f"/bank-loans/{i}/", "creditors": lambda i: "/creditors/"}


def _check_url(cid) -> str:
    r = _rows("SELECT statement_id, loan_note_id FROM checks_issued WHERE check_id = ?", (cid,))
    if not r:
        return ""
    return f"/order-statements/{r[0]['statement_id']}/?tab=checks" if r[0]["statement_id"] else (f"/lenders/{r[0]['loan_note_id']}/?tab=checks" if r[0]["loan_note_id"] else "")


def _card_url(sid) -> str:
    r = _rows("SELECT card_id FROM cc_statements WHERE cc_statement_id = ?", (sid,))
    return f"/credit-cards/?card={r[0]['card_id']}&show=all" if r else "/credit-cards/"


def _invoice_url(iid) -> str:
    r = _rows("SELECT statement_id FROM invoices WHERE invoice_id = ?", (iid,))
    return f"/order-statements/{r[0]['statement_id']}/" if r and r[0]["statement_id"] else ""


def to_look_at() -> int:
    """The Home card's number: open review lines + data checks + possible duplicates."""
    from engines import data_health_service as dh
    from engines import review_flags as rf
    from engines.duplicate_scan_service import scan_possible_duplicates
    return rf.open_count() + dh.open_count() + len(scan_possible_duplicates())


@page_needed("review")
def checks_review(request):
    from engines import data_health_service as dh
    from engines import review_flags as rf
    from engines.duplicate_scan_service import dismiss, restore, scan_possible_duplicates
    from engines.permissions import can
    role = request.slicks_user["role"]
    may_health = can(role, "health.view")
    if request.method == "POST":
        p, what = request.POST, request.POST.get("action", "")
        ticked = [k[5:] for k, v in p.items() if k.startswith("pick_") and v == "1"]
        note = " ".join(p.get("note", "").split())
        if what == "reviewed":
            done = 0
            for fid in ticked:
                ok, _ = rf.acknowledge(int(fid))
                done += ok
            if note and done:
                from engines.common import get_conn
                with get_conn() as conn:
                    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, new_values) VALUES (acting_user(), 'REVIEW_NOTE', 'review_flags', ?)",
                                 (f"{', '.join(ticked)}: {note}",))
            messages.success(request, f"{done} line(s) marked reviewed." if done else "Tick the lines you looked into.")
            return redirect("/review/" + ("?tab=signins" if p.get("tab") == "signins" else ""))
        if what == "seen":                                         # Changed today: the owner looked at a correction (2026-10-19)
            from engines.entry_changes import mark_seen
            ok, msg = mark_seen(int(p.get("change_id") or 0))
            (messages.success if ok else messages.error)(request, msg)
            return redirect("/review/?tab=changes" + (f"&d={p.get('d')}" if p.get("d") else ""))
        if what in ("fine", "again") and may_health:
            (dh.mark_fine(ticked, note) if what == "fine" else dh.show_again(ticked))
            messages.success(request, f"{len(ticked)} finding(s) " + ("marked fine." if what == "fine" else "shown again."))
            return redirect("/review/?tab=data" + ("&fine=1" if what == "again" else ""))
        if what in ("dismiss", "restore") and may_health:
            (dismiss(ticked, note) if what == "dismiss" else restore(ticked))
            messages.success(request, f"{len(ticked)} line(s) " + ("set aside: not duplicates." if what == "dismiss" else "shown again."))
            return redirect("/review/?tab=dups" + ("&all=1" if what == "restore" else ""))
        return redirect("/review/")
    tab = request.GET.get("tab", "review")
    rf.close_old_signins()
    every = rf.open_flags().to_dict("records")
    signins = [f for f in every if f["kind"] in rf.SIGNIN_KINDS]
    flags = signins if tab == "signins" else [f for f in every if f["kind"] not in rf.SIGNIN_KINDS]
    names = {r["email"].lower(): r["name"] for r in _rows("SELECT lower(email) AS email, name FROM app_users")}
    ref = {r["flag_id"]: r for r in _rows("SELECT flag_id, ref_type, ref_id FROM review_flags WHERE reviewed_at IS NULL")}
    for f in flags:
        f["label"] = rf.KINDS.get(f["kind"], f["kind"].replace("_", " ").capitalize())
        f["who"] = names.get(str(f.get("raised_by") or "").lower(), f.get("raised_by") or "")
        r = ref.get(f["flag_id"], {})
        try:
            f["url"] = REF_URL[r["ref_type"]](r["ref_id"]) if r.get("ref_type") in REF_URL and r.get("ref_id") else ""
        except Exception:                                            # noqa: BLE001
            f["url"] = ""
    health = dh.findings(include_fine=request.GET.get("fine") == "1") if may_health else []
    dups = scan_possible_duplicates(include_dismissed=request.GET.get("all") == "1") if may_health else None
    dup_rows = [] if dups is None or dups.empty else dups.astype(object).where(dups.notna(), None).to_dict("records")
    from engines.entry_changes import changes, unseen_count
    from engines.clock import manila_today
    from datetime import date as _date, timedelta as _td
    try:
        cday = _date.fromisoformat(request.GET.get("d", ""))
    except ValueError:
        cday = manila_today()
    day_changes = changes(cday)
    for c in day_changes:
        c["who"] = names.get(str(c.get("by") or "").lower(), c.get("by") or "")
        c["url"] = {"sales": f"/sales/{c['ref_id']}/", "purchase_orders": f"/purchase-orders/{c['ref_id']}/", "items": f"/stock/{c['ref_id']}/"}.get(c["ref_type"], "")
    return render(request, "masters/checks_review.html", {
        "changes": day_changes, "cday": cday, "cprev": cday - _td(days=1), "cnext": cday + _td(days=1) if cday < manila_today() else None, "n_unseen": unseen_count(),
        "page_id": "review", "tab": tab, "flags": flags, "health": health, "dups": dup_rows, "may_health": may_health,
        "n_flags": len(every) - len(signins), "n_signins": len(signins), "n_health": len([h for h in health if not h["fine"]]), "n_dups": len([d for d in dup_rows if not d.get("dismissed")]),
        "show_fine": request.GET.get("fine") == "1", "show_all": request.GET.get("all") == "1"})
