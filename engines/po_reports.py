"""Records > Purchase order reports (owner, 2026-10-12: "the reports first" -> "go"). Read-only, straight from the documents:

follow_through() -- one line per purchase order: ordered -> billed on its DRs / SIs -> with a check -> paid by the bank, and where it stands.
with_and_without() -- how much of what is bought goes through an order first: a purchase is "with a PO" when a delivery of an order made or matched
its payable. Nothing is blocked either way (No PO is allowed); this only shows the habit.

"Paid by the bank" is the clearing rule of engines/clearing.py, as everywhere else: the statement's word, never a check's date."""
from __future__ import annotations

import io

import pandas as pd

from engines.clearing import cleared_sql
from engines.clock import manila_today
from engines.common import get_conn

OPEN_STATES = ("ORDERED", "PARTIAL", "RECEIVED")
SHOWS = [("open", "Open orders"), ("month", "Ordered this month"), ("differs", "Billed differs from ordered"), ("all", "All")]


def _po_rows(conn) -> list[dict]:
    marks = ",".join("?" for _ in OPEN_STATES)
    pos = conn.execute(f"""SELECT p.po_id, p.po_no, cr.creditor_id, cr.creditor_name, substr(p.order_date, 1, 10), p.status,
                                  COALESCE((SELECT SUM(l.est_amount) FROM purchase_order_lines l WHERE l.po_id = p.po_id), 0)
                           FROM purchase_orders p JOIN creditors cr ON cr.creditor_id = p.creditor_id
                           WHERE p.status IN ({marks}) ORDER BY p.order_date DESC, p.po_id DESC""", OPEN_STATES).fetchall()
    receipts: dict = {}
    for po_id, billed, est, invoice_id in conn.execute("SELECT po_id, actual_amount, est_amount, invoice_id FROM po_receipts"):
        r = receipts.setdefault(po_id, {"billed": 0.0, "est": 0.0, "invoices": set()})
        r["billed"] += float(billed or 0)
        r["est"] += float(est or 0)
        if invoice_id:
            r["invoices"].add(int(invoice_id))
    money: dict = {}                                            # invoice -> (with a check, paid by the bank)
    for invoice_id, written, paid in conn.execute(f"""SELECT a.invoice_id, SUM(a.amount), SUM(CASE WHEN {cleared_sql('c')} THEN a.amount ELSE 0 END)
                                                     FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id
                                                     WHERE c.status = 'Active' GROUP BY a.invoice_id"""):
        money[int(invoice_id)] = (float(written or 0), float(paid or 0))
    for invoice_id, amount in conn.execute("SELECT invoice_id, amount FROM invoices WHERE paid_via_card_id IS NOT NULL AND status = 'Paid'"):
        money[int(invoice_id)] = (float(amount or 0), float(amount or 0))      # charged to a card: the supplier is paid
    today = manila_today()
    out = []
    for po_id, po_no, creditor_id, supplier, day, status, ordered in pos:
        r = receipts.get(po_id, {"billed": 0.0, "est": 0.0, "invoices": set()})
        billed = round(r["billed"], 2)
        written = round(min(billed, sum(money.get(i, (0, 0))[0] for i in r["invoices"])), 2)
        paid = round(min(billed, sum(money.get(i, (0, 0))[1] for i in r["invoices"])), 2)
        to_come = round(max(0.0, float(ordered) - r["est"]), 2) if status in ("ORDERED", "PARTIAL") else 0.0
        diff = round(billed - r["est"], 2) if r["invoices"] or billed else 0.0
        if status == "ORDERED" and not billed:
            days = (today - pd.to_datetime(day).date()).days
            stage, tone = f"not received · {days} day{'s' if days != 1 else ''}", "red"
        elif status == "PARTIAL":
            stage, tone = "part received", "warn"
        elif billed - paid < 0.005:
            stage, tone = "paid", "ok"
        elif written < 0.005:
            stage, tone = "received, no check", "warn"
        elif billed - written > 0.005:
            stage, tone = "part with a check", "warn"
        else:
            stage, tone = "checks written", ""
        out.append({"po_id": po_id, "po_no": po_no, "creditor_id": creditor_id, "supplier": supplier, "day": day, "ordered": round(float(ordered), 2),
                    "billed": billed, "diff": diff, "written": written, "paid": paid, "owed": round(billed - paid, 2), "to_come": to_come,
                    "stage": stage, "tone": tone, "done": stage == "paid"})
    return out


def follow_through(show: str = "open", creditor_id: int | None = None) -> dict:
    """{'rows', 'show', 'counts', 'total', 'not_received', 'no_check', 'waiting_bank', 'suppliers'}: the three headline sums are of every order, so
    they do not move with the chip."""
    show = show if show in dict(SHOWS) else "open"
    with get_conn() as conn:
        rows = _po_rows(conn)
    suppliers = sorted({(r["creditor_id"], r["supplier"]) for r in rows}, key=lambda x: x[1])
    if creditor_id:
        rows = [r for r in rows if r["creditor_id"] == int(creditor_id)]
    month = manila_today().strftime("%Y-%m")
    sets = {"open": [r for r in rows if not r["done"]], "month": [r for r in rows if r["day"][:7] == month],
            "differs": [r for r in rows if abs(r["diff"]) > 0.005], "all": rows}
    shown = sets[show]
    keys = ("ordered", "billed", "diff", "written", "paid", "owed")
    return {"rows": shown, "show": show, "shows": SHOWS, "counts": {k: len(v) for k, v in sets.items()}, "suppliers": suppliers,
            "total": {k: round(sum(r[k] for r in shown), 2) for k in keys},
            "not_received": round(sum(r["to_come"] for r in rows), 2), "no_check": round(sum(r["billed"] - r["written"] for r in rows), 2),
            "waiting_bank": round(sum(r["written"] - r["paid"] for r in rows), 2)}


def _purchases(conn) -> pd.DataFrame:
    """Every real payable document (not a beginning balance, not cancelled) and whether an order's delivery stands behind it."""
    return pd.read_sql_query("""SELECT i.invoice_id, substr(i.invoice_date, 1, 10) AS day, cr.creditor_name AS supplier, i.doc_ref, i.dr_no, i.amount, i.statement_id,
                                       EXISTS (SELECT 1 FROM po_receipts r WHERE r.invoice_id = i.invoice_id) AS with_po
                                FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id
                                WHERE i.is_opening_balance = 0 AND i.status != 'Cancelled' ORDER BY i.invoice_date, i.invoice_id""", conn)


def with_and_without(by: str = "month", month: str | None = None) -> dict:
    """{'by', 'groups': [{key, label, n, amount, n_po, amt_po, n_no, amt_no, pct, so_far}], 'month', 'no_po': the No PO purchases of that month}.
    pct is by amount: of each peso bought, how much went through an order."""
    by = "supplier" if by == "supplier" else "month"
    this = manila_today().strftime("%Y-%m")
    with get_conn() as conn:
        df = _purchases(conn)
    if df.empty:
        return {"by": by, "groups": [], "month": month or this, "no_po": [], "months": []}
    df["month"] = df["day"].str[:7]
    df["with_po"] = df["with_po"].astype(bool)
    months = sorted(df["month"].unique(), reverse=True)
    month = month if month in months else (this if this in months else months[0])
    groups = []
    keyed = df.groupby("month" if by == "month" else "supplier", sort=True)
    for key, part in (reversed(list(keyed)) if by == "month" else keyed):
        po, no = part[part["with_po"]], part[~part["with_po"]]
        amount = float(part["amount"].sum())
        label = pd.to_datetime(key + "-01").strftime("%B %Y") if by == "month" else key
        groups.append({"key": key, "label": label, "n": len(part), "amount": round(amount, 2), "n_po": len(po), "amt_po": round(float(po["amount"].sum()), 2),
                       "n_no": len(no), "amt_no": round(float(no["amount"].sum()), 2), "pct": round(100 * float(po["amount"].sum()) / amount) if amount else 0,
                       "so_far": by == "month" and key == this})
    if by == "supplier":
        groups.sort(key=lambda g: -g["amt_no"])
    part = df[(df["month"] == month) & ~df["with_po"]]
    no_po = part.astype(object).where(part.notna(), None).to_dict("records")       # a blank DR or statement shows blank, not "nan" / "73.0"
    return {"by": by, "groups": groups, "month": month, "month_label": pd.to_datetime(month + "-01").strftime("%B %Y"), "no_po": no_po, "months": months}


def follow_through_xlsx(data: dict) -> bytes:
    out = pd.DataFrame([{"PO": r["po_no"], "Supplier": r["supplier"], "Ordered on": r["day"], "Ordered": r["ordered"], "Billed (DR / SI)": r["billed"],
                         "Difference": r["diff"], "With a check": r["written"], "Paid by the bank": r["paid"], "Still owed": r["owed"],
                         "Still to come": r["to_come"], "Where it stands": r["stage"]} for r in data["rows"]])
    buf = io.BytesIO()
    out.to_excel(buf, index=False, sheet_name="PO follow-through")
    return buf.getvalue()


def with_and_without_xlsx(data: dict) -> bytes:
    first = "Month" if data["by"] == "month" else "Supplier"
    groups = pd.DataFrame([{first: g["label"], "Purchases": g["n"], "Amount": g["amount"], "With a PO": g["n_po"], "With a PO, amount": g["amt_po"],
                            "No PO": g["n_no"], "No PO, amount": g["amt_no"], "Through a PO %": g["pct"]} for g in data["groups"]])
    no_po = pd.DataFrame([{"Date": r["day"], "Supplier": r["supplier"], "SI": r["doc_ref"], "DR": r["dr_no"], "Amount": r["amount"]} for r in data["no_po"]])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        groups.to_excel(w, index=False, sheet_name="With and without a PO")
        no_po.to_excel(w, index=False, sheet_name="No PO " + data["month"])
    return buf.getvalue()
