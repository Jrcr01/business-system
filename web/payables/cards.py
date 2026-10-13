"""Other payables -> Credit cards in Django (owner, 2026-10-09; mock-up B approved). Two ways a card payment becomes known:
  * the owner's: the next SOA -- "Add statement" takes its total, the payments it shows and its finance charge, and confirms the earlier statement
    (card_rules.add_statement_from_soa -> confirm_cc_payment_from_next_statement, which flags a difference with the daily payments);
  * the co-owners': each payment recorded on Daily payables, on the day it posts (cc_payments).
Tabs: Statements, Cards and cardholders, Missing statement check -- as the old screen."""
from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
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


CARD_NAME = "cc.card_name || ' - ' || cc.card_number_last4"


@page_needed("credit-cards")
def credit_cards(request):
    if request.method == "POST":
        return _post(request)
    tab = request.GET.get("tab", "statements")
    may = _can(request, "cards.use")
    cards = _rows(f"""SELECT cc.card_id, {CARD_NAME} AS name, cc.card_name, cc.card_number_last4, cc.credit_limit, cc.statement_day, cc.annual_fee_due_month,
                             cc.is_active, cc.creditor_id, cc.cardholder_id, cr.creditor_name AS issuer, ch.name AS holder
                      FROM credit_cards cc JOIN creditors cr ON cr.creditor_id = cc.creditor_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
                      ORDER BY cc.is_active DESC, ch.name, cr.creditor_name, cc.card_name""")
    ctx = {"page_id": "credit-cards", "tab": tab, "may_write": may, "cards": cards, "active_cards": [c for c in cards if c["is_active"]], "today": date.today()}
    if tab == "cards":
        from engines.cc_service import paid_from_bank_ids
        ctx["holders"] = _rows("SELECT cardholder_id, name, department, contact_number, is_active FROM cardholders ORDER BY is_active DESC, name")
        ctx["issuers"] = _rows("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'CREDIT_CARD' AND is_active = 1 ORDER BY creditor_name")
        ctx["from_bank"] = paid_from_bank_ids()
        one = int(request.GET.get("edit_card") or 0)
        ctx["edit_card"] = next((c for c in cards if c["card_id"] == one), None)
        ctx["months"] = [(i, date(2000, i, 1).strftime("%B")) for i in range(1, 13)]
    elif tab == "missing":
        from engines.cc_service import get_statement_coverage, missing_wait_days
        cov = get_statement_coverage()
        rows = [] if cov.empty else cov.astype(object).where(cov.notna(), None).to_dict("records")
        show_all = request.GET.get("all") == "1"
        ctx.update({"cov": [r for r in rows if show_all or r["status"] not in ("OK", "Not yet expected", "No due")], "show_all": show_all,
                    "counts": {s: sum(1 for r in rows if r["status"] == s) for s in ("Missing this month", "Missing both months", "No statement ever", "Not yet expected", "No due", "OK")},
                    "wait": missing_wait_days(), "this_month": date.today().strftime("%Y-%m"), "this_label": date.today().strftime("%B %Y")})
    else:
        card, show = request.GET.get("card", ""), request.GET.get("show", "open")
        where, params = ["cs.status != 'Cancelled'"], []
        if card.isdigit():
            where.append("cs.card_id = ?"); params.append(int(card))
        if show == "open":
            where.append("(cs.status IN ('Unpaid', 'Partially Paid') OR cs.paid_confirmed = 0)")
        stmts = _rows(f"""SELECT cs.cc_statement_id, cs.card_id, {CARD_NAME} AS card, ch.name AS holder, cs.statement_month, cs.due_date, cs.total_amount_due,
                                 COALESCE(cs.amount_paid, 0) AS amount_paid, cs.status, cs.paid_confirmed,
                                 (SELECT MAX(x.due_date) FROM cc_statements x WHERE x.card_id = cs.card_id AND x.status != 'Cancelled') = cs.due_date AS is_latest
                          FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
                          WHERE {" AND ".join(where)} ORDER BY cs.due_date DESC, cs.cc_statement_id DESC LIMIT 300""", tuple(params))
        pays = {}
        if stmts:
            ids = [s["cc_statement_id"] for s in stmts]
            for p in _rows(f"SELECT cc_statement_id, payment_date, amount FROM cc_payments WHERE cc_statement_id IN ({','.join('?' * len(ids))}) ORDER BY payment_date",
                           tuple(ids)):
                pays.setdefault(p["cc_statement_id"], []).append(p)
        for s in stmts:
            s["payments"] = pays.get(s["cc_statement_id"], [])
        # for the Add statement arithmetic shown as you type: each card's latest statement and what was recorded day by day against it
        latest = {r["card_id"]: r for r in _rows("""SELECT cs.card_id, cs.cc_statement_id, cs.total_amount_due, cs.due_date, cs.paid_confirmed,
                                                             (SELECT COALESCE(SUM(p.amount), 0) FROM cc_payments p WHERE p.cc_statement_id = cs.cc_statement_id) AS daily
                                                      FROM cc_statements cs WHERE cs.status != 'Cancelled' AND cs.cc_statement_id = (
                                                          SELECT x.cc_statement_id FROM cc_statements x WHERE x.card_id = cs.card_id AND x.status != 'Cancelled'
                                                          ORDER BY x.due_date DESC, x.cc_statement_id DESC LIMIT 1)""")}
        for c in ctx["active_cards"]:
            c["latest"] = latest.get(c["card_id"])
        one = int(request.GET.get("edit") or 0) if (request.GET.get("edit") or "").isdigit() else 0
        if one and may:
            hit = _rows(f"""SELECT cs.cc_statement_id, cs.card_id, {CARD_NAME} AS card, cs.statement_month, cs.due_date, cs.total_amount_due, cs.interest_charged,
                                   (SELECT x.cc_statement_id FROM cc_statements x WHERE x.card_id = cs.card_id ORDER BY x.due_date DESC, x.cc_statement_id DESC LIMIT 1)
                                       = cs.cc_statement_id AS is_latest
                            FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id WHERE cs.cc_statement_id = ? AND cs.status != 'Cancelled'""", (one,))
            ctx["edit_stmt"] = hit[0] if hit else None
        ctx.update({"stmts": stmts, "card": card, "show": show, "typed": request.session.pop("cc_typed", None),
                    "month_default": date.today().strftime("%Y-%m")})
    return render(request, "payables/credit_cards.html", ctx)


def _post(request):
    from engines import card_rules as cr
    p, what = request.POST, request.POST.get("action", "")
    if not _can(request, "cards.use"):
        _say(request, False, "Card work is switched off for you.")
        return redirect("/credit-cards/")
    if what == "statement":
        card, due, total = int(p.get("card") or 0), _date(p.get("due")), _num(p.get("total"))
        payments = p.get("payments", "").strip()
        month = (p.get("month") or "").strip()[:7] or None
        if not card or not due or total is None:
            ok, msg = False, "Pick the card and give the due date and the total amount due."
        else:
            ok, msg, _ = cr.add_statement_from_soa(card, month, due, total, _num(payments) if payments else None, _num(p.get("interest"), 0.0) or 0.0)
        _say(request, ok, msg)
        if not ok:
            request.session["cc_typed"] = {k: v for k, v in p.items() if k != "csrfmiddlewaretoken"}
        return redirect("/credit-cards/" + (f"?card={card}" if card else ""))
    if what == "correct_statement":
        sid = int(p.get("statement_id") or 0)
        total, fee = (_num(p.get("total")) if "total" in p else None), (_num(p.get("interest"), 0.0) if "interest" in p else None)
        ok, msg = cr.correct_cc_statement(sid, p.get("month", ""), _date(p.get("due")), total, fee)
        _say(request, ok, msg)
        back = f"?card={p.get('card')}&show=all" if (p.get("card") or "").isdigit() else "?show=all"
        return redirect("/credit-cards/" + back + ("" if ok else f"&edit={sid}#edit"))
    if what == "delete_statement":
        _say(request, *cr.delete_cc_statement(int(p.get("statement_id") or 0)))
        return redirect("/credit-cards/")
    if what in ("add_card", "edit_card"):
        limit = _num(p.get("limit"), 0.0) or 0.0
        holder = int(p.get("holder")) if (p.get("holder") or "").isdigit() else None
        fee = int(p.get("fee_month")) if (p.get("fee_month") or "").isdigit() else None
        sday = int(p.get("statement_day")) if (p.get("statement_day") or "").isdigit() else None
        if what == "add_card":
            last4 = "".join(ch for ch in p.get("last4", "") if ch.isdigit())[-4:]
            if not p.get("issuer") or not p.get("card_name", "").strip() or len(last4) != 4:
                ok, msg = False, "Pick the issuer and give the card name and its last 4 digits."
            else:
                ok, msg = cr.add_credit_card(int(p.get("issuer")), holder, p.get("card_name").strip(), last4, limit, fee, sday)
        else:
            ok, msg = cr.edit_credit_card(int(p.get("card_id") or 0), p.get("card_name", ""), int(p.get("issuer") or 0), holder, limit, fee, sday)
        _say(request, ok, msg)
        return redirect("/credit-cards/?tab=cards")
    if what in ("deactivate_card", "reactivate_card"):
        (cr.deactivate_credit_card if what == "deactivate_card" else cr.reactivate_credit_card)(int(p.get("card_id") or 0))
        _say(request, True, "Card " + ("deactivated: its history stays." if what == "deactivate_card" else "reactivated."))
        return redirect("/credit-cards/?tab=cards")
    if what == "from_bank":
        from engines.cc_service import set_paid_from_bank
        set_paid_from_bank({int(k[5:]) for k, v in p.items() if k.startswith("bank_") and v == "1" and k[5:].isdigit()})
        _say(request, True, "Saved: the cards paid straight from the Metrobank account.")
        return redirect("/credit-cards/?tab=cards")
    if what == "add_holder":
        name = " ".join(p.get("name", "").split())
        if not name:
            _say(request, False, "Type the cardholder's name.")
        else:
            cr.add_cardholder(name, p.get("department", ""), p.get("contact", ""))
            _say(request, True, f"{name} added.")
        return redirect("/credit-cards/?tab=cards")
    if what in ("deactivate_holder", "reactivate_holder"):
        (cr.deactivate_cardholder if what == "deactivate_holder" else cr.reactivate_cardholder)(int(p.get("holder_id") or 0))
        _say(request, True, "Cardholder " + ("deactivated." if what == "deactivate_holder" else "reactivated."))
        return redirect("/credit-cards/?tab=cards")
    if what in ("no_due", "undo_no_due"):
        from engines.cc_service import mark_no_due, undo_no_due
        card, month = int(p.get("card_id") or 0), p.get("month", "")[:7]
        (mark_no_due(card, month, p.get("note", "")) if what == "no_due" else undo_no_due(card, month))
        _say(request, True, "Marked: nothing due this month." if what == "no_due" else "The 'no due' mark is taken back.")
        return redirect("/credit-cards/?tab=missing")
    if what == "wait":
        from engines.cc_service import set_missing_wait_days
        set_missing_wait_days(max(0, min(60, int(_num(p.get("days"), 7) or 7))))
        return redirect("/credit-cards/?tab=missing")
    _say(request, False, "Unknown action.")
    return redirect("/credit-cards/")
