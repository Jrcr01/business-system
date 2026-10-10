"""A batch's Checks tab (Phase 2 step 2, owner 2026-10-08): one flow for every check -- park -> write (a Chrysanth run file, or by hand) -> number ->
release -> cleared. The rules are the engines' own (check_rules, check_run_service, pdc_release_service, clearing); this file only draws the tab and
passes on what was ticked or typed.

  * Parked: no number, not written. Can be edited or deleted.
  * Written: printed (the Chrysanth file was made) or numbered by hand. Same meaning whichever way.
  * Released: someone recorded who collected it (anyone whose Release switch is on).
  * Cleared: the bank, settled outside it, or ticked (engines/clearing.py).
After numbers are given, any leaf of the booklet left without a check is shown at once: log it spoiled, or "check later" (a Review list line)."""
from __future__ import annotations

from datetime import date

from django.http import HttpResponse
from django.shortcuts import redirect

from buying.views import _date


PARK_ROWS = 50


def park_context(b: dict, rows_fn, due_today) -> dict:
    """What the Park checks grid needs (owner, 2026-10-09: the "How to split" of the old screen): the day the even split is dated (the batch's earliest unpaid
    invoice due date, as before; else the terms from today) and the open invoices with what is left of each, for "one check per invoice"."""
    from engines.statement_rules import get_invoices_with_remaining
    inv = get_invoices_with_remaining(int(b["statement_id"]))
    col = "remaining" if "remaining" in inv.columns else None
    open_ = [] if inv.empty or col is None else [{"due": str(r["due_date"])[:10], "remaining": round(float(r[col]), 2)}
                                                  for _, r in inv.iterrows() if float(r[col]) > 0.004]
    day = min((x["due"] for x in open_), default=None)
    from datetime import date as _d
    try:                                                     # "Within the daily limit" (owner, 2026-10-09): dates that keep each day under the target
        from engines.day_cap import soft_line, suggest_batch_dates
        suggested = [{"date": s["date"].isoformat(), "amount": s["amount"], "invoices": s["invoices"], "status": s["status"],
                      "day_total": s["day_total"]} for s in suggest_batch_dates(int(b["statement_id"]))] if soft_line() else []
    except Exception:                                        # noqa: BLE001 -- the grid must never break
        suggested = []
    return {"split_day": _d.fromisoformat(day) if day else due_today, "open_invoices": open_, "park_rows": range(1, PARK_ROWS + 1),
            "suggested": suggested}


def booklet_plan(request, num_checks: list[dict]) -> dict:
    """For the numbering panel (owner, 2026-10-09: "make the assigning of checks easier like in /more"): the active booklets with their first free
    leaf; the chosen one (?bk=, default the first with leaves left) and start leaf (?start=, default its first free one); the plan -- these checks, in
    the order shown, get the next free leaves (used or spoiled ones stepped over). Nothing is written here."""
    from engines.check_rules import PRIMARY_BANK_ACCOUNT_ID, get_available_booklet_numbers, get_booklets, plan_sequential_numbers
    rows = get_booklets(PRIMARY_BANK_ACCOUNT_ID).to_dict("records")
    booklets = []
    for b in rows:
        free = get_available_booklet_numbers(PRIMARY_BANK_ACCOUNT_ID, b)
        booklets.append({"id": int(b["booklet_id"]), "code": b.get("booklet_code") or f"Booklet #{b['booklet_id']}", "start": int(b["start_number"]),
                         "end": int(b["end_number"]), "first_free": free[0] if free else None, "left": len(free)})
    if not booklets or not num_checks:
        return {"booklets": booklets, "plan": [], "bk": None}
    want = request.GET.get("bk", "")
    bk = next((b for b in booklets if str(b["id"]) == want), None) or next((b for b in booklets if b["left"]), booklets[0])
    start = int(request.GET.get("start")) if (request.GET.get("start") or "").isdigit() else bk["first_free"]
    pairs, skipped, err = plan_sequential_numbers(bk["id"], start, [int(c["check_id"]) for c in num_checks])
    leaf = dict(pairs)
    plan = [{**c, "leaf": leaf.get(int(c["check_id"]))} for c in num_checks]
    return {"booklets": booklets, "bk": bk, "start": start, "plan": plan if not err else [], "plan_err": err, "skipped": skipped,
            "plan_ids": ",".join(str(c["check_id"]) for c in num_checks)}


def stage(c: dict) -> str:
    if c["status"] != "Active":
        return c["status"]
    if c.get("bank_cleared_date") or c.get("manual_cleared_at") or c.get("settled_outside_at"):
        return "Cleared"
    if c.get("released_date"):
        return "Released"
    if c.get("printed_at") or c.get("check_number"):
        return "Written"
    return "Parked"


def checks_for(rows_fn, owner_id: int, column: str = "statement_id") -> list[dict]:
    """The checks of a batch (statement_id) or of a loan note (loan_note_id), each with its stage."""
    assert column in ("statement_id", "loan_note_id")
    rows = rows_fn(f"""SELECT check_id, check_number, check_date, amount, status, datetime(printed_at, '+8 hours') AS printed_at, print_batch_id,
                              bank_cleared_date, manual_cleared_at, settled_outside_at, released_date, released_to, is_handwritten, cancel_reason
                       FROM checks_issued WHERE {column} = ? ORDER BY check_date, check_id""", (owner_id,))
    for r in rows:
        r["stage"] = stage(r)
        r["how"] = "Chrysanth" if r["print_batch_id"] else ("by hand" if r["check_number"] or r["printed_at"] else "")
    return rows


def _ids(p, prefix: str = "tick_") -> list[int]:
    return [int(k[len(prefix):]) for k, v in p.items() if k.startswith(prefix) and v == "1" and k[len(prefix):].isdigit()]


def _skipped_after(numbers: list[str]) -> list[dict]:
    """The booklets these numbers came from, and the leaves in them now known to be used but left without a check (not recorded, not spoiled, not
    on the bank): [{'booklet', 'code', 'leaves'}]."""
    from engines.check_rules import PRIMARY_BANK_ACCOUNT_ID
    from engines.checkbook_service import _raw_sequence_number, unrecorded_leaves
    from engines.common import get_conn
    out, seen = [], set()
    with get_conn() as conn:
        branch = (conn.execute("SELECT branch_code FROM bank_accounts WHERE bank_account_id = ?", (PRIMARY_BANK_ACCOUNT_ID,)).fetchone() or [""])[0] or ""
        for n in numbers:
            n = (n or "").strip()
            leaves = {x for x in ((int(n) if n.isdigit() else None), _raw_sequence_number(n, branch)) if x is not None}   # booklets kept in full (3992575601) or short (1000)
            row = None
            for leaf in leaves:
                row = conn.execute("SELECT booklet_id, booklet_code FROM check_booklets WHERE bank_account_id = ? AND start_number <= ? AND end_number >= ? ORDER BY booklet_id LIMIT 1",
                                   (PRIMARY_BANK_ACCOUNT_ID, leaf, leaf)).fetchone()
                if row:
                    break
            if row and row[0] not in seen:
                seen.add(row[0])
                leaves = unrecorded_leaves(int(row[0]))
                if leaves:
                    out.append({"booklet": int(row[0]), "code": row[1] or f"Booklet #{row[0]}", "leaves": leaves})
    return out


def post(request, b: dict, say, can, loan_note_id: int | None = None) -> HttpResponse:
    """The Checks tab's buttons, for a batch (b = the batch) or a loan note (loan_note_id given; b = {'creditor_id'}). `say(ok, msg)` shows the message;
    `can(action)` is the person's right."""
    from engines import check_rules as cr
    from engines.common import get_conn
    sid, cid = (int(b["statement_id"]) if loan_note_id is None else None), int(b["creditor_id"])
    p, what = request.POST, request.POST.get("action", "")
    back = f"/order-statements/{sid}/?tab=checks" if loan_note_id is None else f"/lenders/{loan_note_id}/?tab=checks"
    ticked = _ids(p)
    if loan_note_id is not None and what in ("chk_park", "chk_edit"):
        say(False, "A lender check is parked from its payment on the Schedule tab.")
        return redirect(back)

    if what == "chk_park":
        if not can("check.park"):
            say(False, "Parking checks is switched off for you.")
            return redirect(back)
        rows = []
        for i in range(1, PARK_ROWS + 1):                       # up to 50 checks at once (the Split choices, owner 2026-10-09)
            day, amount = _date(p.get(f"d{i}")), p.get(f"a{i}", "").replace(",", "").strip()
            if day and amount:
                try:
                    rows.append({"check_date": day, "amount": float(amount)})
                except ValueError:
                    say(False, f"Row {i}: the amount '{amount}' is not a number.")
                    return redirect(back)
        if not rows:
            say(False, "Give each check a date and an amount.")
            return redirect(back)
        from payables import day_limit
        held, over = day_limit.check(request, [(r["check_date"], r["amount"]) for r in rows], back, "Parking checks")
        if held:
            return held
        ok, msg, _ = cr.issue_checks_grid(cid, sid, rows)
        if ok:
            day_limit.flag(over, "a parked check of")
        say(ok, msg)
        return redirect(back)

    if what == "chk_edit":
        if not can("check.park"):
            say(False, "Changing parked checks is switched off for you.")
            return redirect(back)
        one = int(p.get("check_id") or 0)
        day, amount = _date(p.get("date")), p.get("amount", "").replace(",", "").strip()
        try:
            new_amount = float(amount)
        except (TypeError, ValueError):
            say(False, "Give the check a date and an amount.")
            return redirect(back)
        from payables import day_limit
        with get_conn() as conn:
            old = conn.execute("SELECT check_date, amount FROM checks_issued WHERE check_id = ?", (one,)).fetchone()
        over = []
        if day and old:
            from datetime import date as _d
            held, over = day_limit.check(request, [(day, new_amount)], back, "Changing a parked check",
                                         minus=[(_d.fromisoformat(str(old[0])[:10]), float(old[1]))])
            if held:
                return held
        ok, msg = cr.edit_parked_check(one, new_amount, day)
        if ok:
            day_limit.flag(over, "a moved check of")
        say(ok, msg)
        return redirect(back)

    if what == "chk_delete":
        if not can("check.park"):
            say(False, "Deleting parked checks is switched off for you.")
            return redirect(back)
        if loan_note_id is None:
            say(*cr.unpark_checks(sid, ticked))
        else:
            from engines.loan_schedule_service import delete_parked_loan_checks
            say(*delete_parked_loan_checks(loan_note_id, ticked))
        return redirect(back)

    if what == "chk_chrysanth":
        if not can("check.issue"):
            say(False, "Writing checks is switched off for you.")
            return redirect(back)
        from engines.check_run_service import file_name_for_checks, generate_pending_check_run_txt
        if not ticked:
            say(False, "Tick the parked checks to write.")
            return redirect(back)
        data, problems, done = generate_pending_check_run_txt(ticked, log_batch=True)
        if not done:
            say(False, "None of the ticked checks could go in the Chrysanth file: " + "; ".join(problems))
            return redirect(back)
        with get_conn() as conn:
            run = conn.execute("SELECT print_batch_id FROM checks_issued WHERE check_id = ?", (done[0],)).fetchone()[0]
        resp = HttpResponse(data, content_type="text/plain")
        resp["Content-Disposition"] = f'attachment; filename="{file_name_for_checks(done, run)}"'
        return resp

    if what in ("chk_booklet_hand", "chk_booklet"):              # from a booklet: the next free leaves, all at once (as in the old screens)
        if not can("check.issue"):
            say(False, "Writing and numbering checks is switched off for you.")
            return redirect(back)
        from engines.check_rules import assign_check_numbers_from_booklet, plan_sequential_numbers
        ids = [int(x) for x in p.get("ids", "").split(",") if x.strip().isdigit()]
        start = int(p.get("start")) if (p.get("start") or "").isdigit() else None
        pairs, _, err = plan_sequential_numbers(int(p.get("bk") or 0), start, ids)
        if err:
            say(False, err)
            return redirect(back)
        ok, msg, done = assign_check_numbers_from_booklet(pairs, int(p.get("bk")))
        if ok and what == "chk_booklet_hand":
            from engines.check_run_service import mark_checks_printed
            mark_checks_printed(done)
            msg = f"{len(done)} check(s) written by hand and numbered {pairs[0][1]} to {pairs[-1][1]}."
        say(ok, msg)
        return redirect(back)

    if what in ("chk_hand", "chk_number"):                      # write by hand (numbers + written), or give numbers to checks already written
        if not can("check.issue"):
            say(False, "Writing and numbering checks is switched off for you.")
            return redirect(back)
        typed = {int(k[4:]): v.strip() for k, v in p.items() if k.startswith("num_") and k[4:].isdigit() and v.strip()}
        if not typed:
            say(False, "Type the check number of each check.")
            return redirect(back)
        ok, msg, done = cr.assign_check_numbers_manual(typed, None)
        if ok and what == "chk_hand":
            from engines.check_run_service import mark_checks_printed
            mark_checks_printed(done)                           # written by hand = written
            msg = f"{len(done)} check(s) written by hand and numbered."
        say(ok, msg)
        if ok:
            gaps = _skipped_after(list(typed.values()))
            if gaps:
                request.session["skipped"] = gaps
        return redirect(back)

    if what == "chk_skip_spoiled":
        nums =[n for n in p.get("leaves", "").split(",") if n.strip()]
        done, errors = 0, []
        for n in nums:
            ok, msg = cr.log_spoiled_check(n.strip(), "Skipped when numbering checks (logged from the batch's Checks tab)")
            done += ok
            if not ok:
                errors.append(msg)
        say(not errors, f"{done} number(s) logged as spoiled." + (" " + "; ".join(errors) if errors else ""))
        return redirect(back)

    if what == "chk_skip_later":
        from engines.common import write_tx
        from engines.review_flags import flag
        with write_tx() as conn:
            flag(conn, "CHECK_NUMBER_SKIPPED", "check_booklets", int(p.get("booklet") or 0) or None,
                 f"{p.get('code', 'A booklet')}: number(s) {p.get('leaves', '')} were skipped when checks were numbered -- find out what happened to them")
        say(True, "Put on the Review list so it isn't forgotten.")
        return redirect(back)

    if what == "chk_release":
        if not can("check.release"):
            say(False, "Releasing checks is switched off for you.")
            return redirect(back)
        from engines.pdc_release_service import mark_released
        say(*mark_released(ticked, _date(p.get("released_date")) or date.today(), p.get("released_to", "")))
        return redirect(back)

    if what == "chk_unrelease":
        if not can("check.release"):
            say(False, "Releasing checks is switched off for you.")
            return redirect(back)
        from engines.pdc_release_service import undo_release
        say(*undo_release(int(p.get("check_id") or 0)))
        return redirect(back)

    if what == "chk_void":
        if not can("check.void"):
            say(False, "Voiding checks is switched off for you.")
            return redirect(back)
        reason = " ".join(p.get("reason", "").split())
        if not reason:
            say(False, "Say why the check is voided.")
            return redirect(back)
        say(*cr.cancel_check(int(p.get("check_id") or 0), reason))
        return redirect(back)

    if what == "chk_replace":                                   # void and write its replacement right away (owner, 2026-10-08)
        if not can("check.void"):
            say(False, "Voiding and replacing checks is switched off for you.")
            return redirect(back)
        reason = " ".join(p.get("reason", "").split())
        day = _date(p.get("new_date"))
        if not reason or not day:
            say(False, "Say why the check is replaced, and give the new check's date.")
            return redirect(back + f"&void={p.get('check_id', '')}")
        from engines.handwritten_check_service import replace_check_with_number
        number = p.get("new_number", "").strip()
        ok, msg, new_id = replace_check_with_number(int(p.get("check_id") or 0), day, number, reason)
        if ok and number and new_id:                            # written here and now, like "Write by hand": released when it is collected
            with get_conn() as conn:
                conn.execute("UPDATE checks_issued SET is_handwritten = 0 WHERE check_id = ?", (int(new_id),))
        say(ok, msg + ("" if number or not ok else " Write it with Chrysanth or by hand below."))
        if ok and number:
            gaps = _skipped_after([number])
            if gaps:
                request.session["skipped"] = gaps
        return redirect(back)

    say(False, "Unknown action.")
    return redirect(back)
