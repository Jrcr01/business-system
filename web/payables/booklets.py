"""Bank > Check booklets (owner, 2026-10-09: "add, delete, update, run audit gap"; "it must generate checkbooklet code to account proper sequencing";
"assigned checkbooklet for example me or to someone"): the booklets with who holds each, what is used, spoiled and open, the gaps; add, change, hand over,
archive or delete a booklet; the gap audit of one booklet; "up to no."; log a spoiled leaf. The rules are engines/booklet_rules.py and
engines/checkbook_service.py; this view draws the forms and passes on what was typed. A person who may only see the page gets no forms and no saves
(page_needed)."""
from __future__ import annotations

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render

from masters.views import page_needed


def _say(request, ok: bool, msg: str) -> None:
    (messages.success if ok else messages.error)(request, msg)


def _date(text):
    from datetime import date
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def _int(text):
    try:
        return int(str(text or "").replace(",", "").strip())
    except ValueError:
        return None


def _me(request) -> int:
    from engines.common import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM app_users WHERE LOWER(email) = ?", (str(request.slicks_user["email"]).lower(),)).fetchone()
    return int(row[0]) if row else 0


def _people() -> list[tuple[int, str]]:
    from engines.common import get_conn
    with get_conn() as conn:
        return [(int(r[0]), r[1] or r[2]) for r in conn.execute("SELECT user_id, name, email FROM app_users WHERE is_active = 1 ORDER BY name COLLATE NOCASE")]


def _holder_from(p) -> tuple[int | None, str]:
    """The holder as picked: a person's id, or 'other' with a typed name, or nobody."""
    pick = p.get("holder", "")
    if pick.isdigit():
        return int(pick), ""
    return None, (p.get("holder_name", "").strip() if pick == "other" else "")


@page_needed("booklets")
def booklets(request):
    from engines import booklet_rules as br
    from engines.checkbook_service import audit_booklet_gaps, compress_leaf_ranges, set_reported_up_to
    ask = None
    if request.method == "POST":
        p = request.POST
        what, bid = p.get("action"), _int(p.get("booklet_id")) or 0
        back = "/booklets/" + (f"?show={p.get('show')}" if p.get("show") else "")
        if what == "add":
            uid, name = _holder_from(p)
            ok, msg, _, gap = br.add_booklet(p.get("start"), p.get("end"), _date(p.get("received")), uid, name, confirm_gap=p.get("confirm_gap") == "1")
            if not ok and msg == br.GAP:                         # ask first: nothing is saved yet
                ask = {"gap": gap, "what": "Add anyway", "carry": [(k, v) for k, v in p.items() if k not in ("csrfmiddlewaretoken", "confirm_gap")]}
            else:
                _say(request, ok, msg)
                return redirect(back)
        elif what == "edit":
            ok, msg, gap = br.update_booklet(bid, p.get("start"), p.get("end"), _date(p.get("received")), p.get("status", "Active"), confirm_gap=p.get("confirm_gap") == "1")
            if not ok and msg == br.GAP:
                ask = {"gap": gap, "what": "Save anyway", "carry": [(k, v) for k, v in p.items() if k not in ("csrfmiddlewaretoken", "confirm_gap")]}
            else:
                _say(request, ok, msg)
                return redirect(back if ok else f"/booklets/?edit={bid}")
        elif what == "hand_over":
            uid, name = _holder_from(p)
            _say(request, *br.hand_over(bid, uid, name))
            return redirect(back)
        elif what in ("archive", "unarchive"):
            _say(request, *br.set_status(bid, "Archived" if what == "archive" else "Active"))
            return redirect(back)
        elif what == "delete":
            _say(request, *br.delete_booklet(bid))
            return redirect(back)
        elif what == "up_to":
            n = _int(p.get("number"))
            _say(request, *(set_reported_up_to(bid, n) if n is not None else (False, "Type the last check number used.")))
            return redirect(back)
        elif what == "spoiled":
            from engines.check_rules import log_spoiled_check
            nums = [n.strip() for n in p.get("numbers", "").replace(";", ",").split(",") if n.strip()]
            errors = []
            for n in nums:
                ok, msg = log_spoiled_check(n, p.get("reason", "").strip() or "Spoiled leaf")
                if not ok:
                    errors.append(msg)
            _say(request, not errors and bool(nums), f"{len(nums) - len(errors)} logged as spoiled." + (" " + "; ".join(errors) if errors else ""))
            return redirect(f"/booklets/?audit={bid}" if bid else back)
        else:
            _say(request, False, "Unknown action.")
            return redirect(back)
    g = request.GET
    show_all, mine = g.get("show") == "all", g.get("mine") == "1"
    me = _me(request)
    rows = br.listing(include_archived=show_all)
    for b in rows:
        b["is_mine"] = bool(me) and b.get("holder_user_id") == me
        b["problem"] = bool(b["skipped"] or b["duplicates"])
    alerts = [b for b in rows if b["problem"] and b["status"] != "Archived"]
    shown = [b for b in rows if b["is_mine"]] if mine else rows
    ctx = {"page_id": "booklets", "rows": shown, "alerts": alerts, "show_all": show_all, "mine": mine, "n_mine": sum(1 for b in rows if b["is_mine"]),
           "people": _people(), "me": me, "next_code": br.next_code(), "ask": ask, "statuses": br.STATUSES}
    first, last = br.suggest_range()
    ctx["suggest"] = {"start": first or "", "end": last or ""}
    from engines.clock import manila_today
    ctx["today"] = manila_today()
    pick = _int(g.get("audit")) or (_int(request.POST.get("booklet_id")) if False else None)
    if pick:
        try:
            a = audit_booklet_gaps(pick)
        except ValueError:
            raise Http404
        skipped = [int(x) for x in a["missing_gaps_list"]]
        ctx["audit"] = {"booklet_id": pick, "code": a["booklet_code"], "total": a["total_leaves"], "skipped_n": a["missing_gaps_count"], "dup_n": a["duplicate_numbers_count"],
                        "spoiled_n": a["spoiled_count"], "skipped": compress_leaf_ranges(skipped) if skipped else "", "skipped_list": skipped[:60],
                        "skipped_csv": ", ".join(str(n) for n in skipped), "dups": [str(int(x)) for x in a["duplicate_numbers_list"]],
                        "leaves": a["detailed_breakdown"].to_dict("records")}
    edit = _int(g.get("edit"))
    if edit:
        ctx["edit"] = next((b for b in br.listing(include_archived=True) if int(b["booklet_id"]) == edit), None)
    if g.get("voided"):
        from engines.voided_checks_service import voided_checks
        ctx["voided"] = voided_checks().head(30).to_dict("records")
    return render(request, "payables/booklets.html", ctx)


@page_needed("booklets")
def audit_xlsx(request, booklet_id: int):
    """The leaf-by-leaf breakdown of one booklet, as an Excel file."""
    import io

    from engines.checkbook_service import audit_booklet_gaps
    try:
        a = audit_booklet_gaps(booklet_id)
    except ValueError:
        raise Http404
    buf = io.BytesIO()
    a["detailed_breakdown"].rename(columns={"check_number": "Check number", "state": "State", "amount": "Amount", "note": "Note"}).to_excel(buf, index=False, sheet_name="Leaves")
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{str(a["booklet_code"]).replace(" ", "_")}_leaves.xlsx"'
    return resp
