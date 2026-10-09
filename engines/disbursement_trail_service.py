"""Disbursement trail (stage B6, 2026-10-02): follow one purchase order all the way to the bank.

    Ordered -> Delivered -> Invoiced -> In a batch -> Checks issued -> Released to the supplier -> Cleared by the bank

and, beside it, the planned dates against the actual ones (delivery expected / delivered, payment expected / invoice due / check dated / released / cleared). Nothing here is
stored: it is read from the order, its receipts, the invoices, the batch, the checks and the bank clearing that already exist.

  * trail_for_po(po_id) -- the stages, what is next, the invoices, the checks, the dates
  * pipeline()          -- every order with the stage it has reached (the overview)"""
from __future__ import annotations

from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn
from engines.po_rules import LIVE_STATUSES, get_po

STAGES = [("ordered", "Ordered"), ("delivered", "Delivered"), ("invoiced", "Invoiced"), ("batch", "In a batch"), ("checks", "Checks issued"),
          ("released", "Released to the supplier"), ("cleared", "Cleared by the bank")]
PIPELINE_NAMES = {"ordered": "Ordered", "delivered": "Delivered", "invoiced": "Invoiced", "checks": "Checks issued", "released": "Released", "cleared": "Cleared"}


def _d(value) -> date | None:
    if value is None or value == "" or (isinstance(value, float) and value != value):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _money(v) -> str:
    return f"₱{float(v):,.2f}"


def trail_for_po(po_id: int) -> dict | None:
    po = get_po(po_id)
    if po is None:
        return None
    h, lines, today = po["header"], po["lines"], manila_today()
    with get_conn() as conn:
        inv = pd.read_sql_query(
            """SELECT r.receipt_id, r.invoice_id, i.doc_ref, i.invoice_date, i.due_date, r.received_date, r.est_amount, r.actual_amount, i.statement_id,
                      os.statement_ref AS batch, os.status AS batch_status
               FROM po_receipts r JOIN invoices i ON i.invoice_id = r.invoice_id LEFT JOIN order_statements os ON os.statement_id = i.statement_id
               WHERE r.po_id = ? ORDER BY r.receipt_id""", conn, params=(po_id,))
        checks = pd.DataFrame()
        if not inv.empty:
            marks = ",".join("?" * len(inv))
            checks = pd.read_sql_query(
                f"""SELECT c.check_id, c.check_number, c.check_date, c.amount, c.printed_at, c.released_date, c.released_to, c.is_handwritten, c.bank_cleared_date,
                           SUM(a.amount) AS allocated
                    FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id
                    WHERE a.invoice_id IN ({marks}) AND c.status = 'Active' GROUP BY c.check_id ORDER BY c.check_date, c.check_id""", conn, params=list(inv["invoice_id"]))
            if checks.empty:                                           # older batches were paid without per-invoice allocation: use the batch's own checks
                sids = [int(s) for s in inv["statement_id"].dropna().unique()]
                if sids:
                    m2 = ",".join("?" * len(sids))
                    checks = pd.read_sql_query(
                        f"""SELECT check_id, check_number, check_date, amount, printed_at, released_date, released_to, is_handwritten, bank_cleared_date, amount AS allocated
                            FROM checks_issued WHERE statement_id IN ({m2}) AND status = 'Active' ORDER BY check_date, check_id""", conn, params=sids)

    total_qty, got_qty = float(lines["qty"].sum()), float(lines["qty_received"].sum())
    expected_delivery = _d(h["expected_delivery_date"])
    S: dict[str, dict] = {}

    S["ordered"] = ({"state": "done", "date": _d(h["order_date"]), "detail": f"{h['po_no']} · {po['supplier']} · estimate {_money(po['total'])}, falls due {h['expected_pay_date'] or 'when delivery is set'}"}
                    if h["status"] != "DRAFT" else {"state": "waiting", "date": None, "detail": "Still a draft: not sent to the supplier yet."})
    if got_qty >= total_qty - 1e-9 and total_qty > 0:
        S["delivered"] = {"state": "done", "date": _d(inv["received_date"].max()), "detail": f"All {total_qty:g} received."}
    elif got_qty > 0:
        S["delivered"] = {"state": "partly", "date": _d(inv["received_date"].max()), "detail": f"{got_qty:g} of {total_qty:g} received; the rest is still to come."}
    else:
        late = h["status"] in LIVE_STATUSES and expected_delivery is not None and expected_delivery < today
        S["delivered"] = {"state": "late" if late else "waiting", "date": expected_delivery,
                          "detail": (f"Expected {expected_delivery:%m/%d/%Y}" + (f" — {(today - expected_delivery).days} day(s) late." if late else ".")) if expected_delivery else "No delivery date set."}
    if inv.empty:
        S["invoiced"] = {"state": "waiting", "date": None, "detail": "No supplier invoice matched yet."}
        S["batch"] = {"state": "waiting", "date": None, "detail": "Goes into a batch when the invoice is matched."}
    else:
        est, act = float(inv["est_amount"].sum()), float(inv["actual_amount"].sum())
        diff = round(act - est, 2)
        S["invoiced"] = {"state": "done", "date": _d(inv["invoice_date"].max()), "detail": f"{', '.join(inv['doc_ref'])} · {_money(act)} against the estimate {_money(est)}" + (f" ({'+' if diff > 0 else ''}{_money(diff)})" if abs(diff) > 0.004 else " (same)")}
        S["batch"] = {"state": "done", "date": None, "detail": ", ".join(f"{b} ({st})" for b, st in dict.fromkeys(zip(inv["batch"].fillna("—"), inv["batch_status"].fillna("")))) }
    invoiced_total = float(inv["actual_amount"].sum()) if not inv.empty else 0.0
    if checks.empty:
        S["checks"] = {"state": "waiting", "date": None, "detail": "No check written for it yet." if not inv.empty else "After the invoice is matched."}
        S["released"] = {"state": "waiting", "date": None, "detail": "After the checks are printed."}
        S["cleared"] = {"state": "waiting", "date": None, "detail": "After the supplier deposits."}
    else:
        covered = float(checks["allocated"].sum())
        S["checks"] = {"state": "done" if covered >= invoiced_total - 0.005 else "partly", "date": _d(checks["check_date"].max()),
                       "detail": f"{len(checks)} check(s), {_money(covered)} of {_money(invoiced_total)}; dated {', '.join(sorted({_d(x).strftime('%m/%d/%Y') for x in checks['check_date']}))}."}
        collected = checks[(checks["released_date"].notna()) | (checks["is_handwritten"] == 1)]
        who = ", ".join(sorted({str(w) for w in collected["released_to"].dropna()}))
        if len(collected) == len(checks):
            S["released"] = {"state": "done", "date": _d(collected["released_date"].max()), "detail": f"All {len(checks)} collected" + (f" by {who}" if who else "") + "."}
        elif len(collected):
            S["released"] = {"state": "partly", "date": _d(collected["released_date"].max()), "detail": f"{len(collected)} of {len(checks)} collected" + (f" ({who})" if who else "") + "."}
        else:
            printed = int(checks["printed_at"].notna().sum())
            S["released"] = {"state": "waiting", "date": None, "detail": f"{printed} of {len(checks)} printed, none collected yet." if printed else "Not printed yet."}
        cleared = checks[checks["bank_cleared_date"].notna()]
        if len(cleared) == len(checks):
            S["cleared"] = {"state": "done", "date": _d(cleared["bank_cleared_date"].max()), "detail": f"All {len(checks)} cleared by the bank."}
        elif len(cleared):
            S["cleared"] = {"state": "partly", "date": _d(cleared["bank_cleared_date"].max()), "detail": f"{len(cleared)} of {len(checks)} cleared by the bank."}
        else:
            S["cleared"] = {"state": "waiting", "date": None, "detail": "Not cleared by the bank yet."}

    stages = [{"key": k, "title": t, **S[k]} for k, t in STAGES]
    current = next((s["key"] for s in stages if s["state"] != "done"), None)
    rows = [("Delivery expected", expected_delivery), ("Delivered", _d(inv["received_date"].max()) if not inv.empty else None),
            ("Payment expected (order)", _d(h["expected_pay_date"])), ("Invoice due", _d(inv["due_date"].max()) if not inv.empty else None),
            ("Check dated", _d(checks["check_date"].max()) if not checks.empty else None), ("Released", _d(checks["released_date"].max()) if not checks.empty else None),
            ("Cleared by the bank", _d(checks["bank_cleared_date"].max()) if not checks.empty else None)]
    dates = pd.DataFrame([(w, d) for w, d in rows if d is not None], columns=["what", "date"])
    return {"header": h, "supplier": po["supplier"], "lines": lines, "stages": stages, "current": current, "invoices": inv, "checks": checks, "dates": dates}


def pipeline(only_open: bool = False) -> pd.DataFrame:
    """Every order that is not cancelled, with the furthest stage it has reached: po_id, po_no, supplier, estimated, invoiced, stage, next_step, late."""
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT po_id FROM purchase_orders WHERE status != 'CANCELLED' ORDER BY po_id DESC")]
    rows = []
    for po_id in ids:
        tr = trail_for_po(po_id)
        done = [s["key"] for s in tr["stages"] if s["state"] == "done" and s["key"] in PIPELINE_NAMES]
        if only_open and tr["current"] is None:
            continue
        stage = PIPELINE_NAMES[done[-1]] if done else "Draft"
        nxt = next((s for s in tr["stages"] if s["key"] == tr["current"]), None) if tr["current"] else None
        rows.append({"po_id": po_id, "po_no": tr["header"]["po_no"], "supplier": tr["supplier"], "estimated": float(tr["lines"]["est_amount"].sum()),
                     "invoiced": float(tr["invoices"]["actual_amount"].sum()) if not tr["invoices"].empty else 0.0, "stage": stage,
                     "next_step": nxt["title"] if nxt else "Done", "late": bool(nxt and nxt["state"] == "late")})
    return pd.DataFrame(rows, columns=["po_id", "po_no", "supplier", "estimated", "invoiced", "stage", "next_step", "late"])
