"""My to-do (2026-10-03): on Home, only the steps THIS person may do now, so nobody meets a step that is not theirs. The Releaser sees the slips to collect; Purchasing the orders
waiting for delivery; Payables the deliveries to match and the batches that need checks; the owner the checks to print and what the controls flagged. Each item opens the page and mode
that does it. Nothing here counts toward any other screen's numbers."""
from __future__ import annotations

from engines.permissions import can


def _count(sql: str, params: tuple = ()) -> int:
    from engines.common import get_conn
    with get_conn() as conn:
        return int(conn.execute(sql, params).fetchone()[0] or 0)


def items(role: str | None) -> list[dict]:
    """[{'text', 'page', 'state', 'icon'}] for this role, most pressing first. A source that fails contributes nothing."""
    role = role or "admin"
    out: list[dict] = []

    def add(text: str, page: str, icon: str, state: dict | None = None) -> None:
        out.append({"text": text, "page": page, "state": state or {}, "icon": icon})

    def safe(fn) -> None:
        try:
            fn()
        except Exception:
            pass

    if can(role, "check.release"):
        def slips():
            from engines.release_slip_service import open_slips
            df = open_slips()
            if not df.empty:
                add(f"{len(df)} release slip(s) to collect — {int((df['days_waiting'] > 7).sum())} over a week", "release", ":material/outbox:")
        safe(slips)
    if can(role, "po.receive_goods"):
        def deliveries():
            from engines.clock import manila_today
            n = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL')")
            late = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL') AND expected_delivery_date < ?", (str(manila_today()),))
            if n:
                add(f"{n} order(s) waiting for delivery" + (f" — {late} late" if late else ""), "purchase-orders", ":material/inventory_2:", {"po_mode": "Receive"})
        safe(deliveries)
    if can(role, "po.receive"):
        def invoices():
            n = _count("SELECT COUNT(DISTINCT po_id) FROM purchase_order_lines WHERE qty_received - COALESCE(qty_invoiced, qty_received) > 0.0000001")
            if n:
                add(f"{n} order(s) with goods in, the SI or DR not recorded yet", "purchase-orders", ":material/receipt_long:", {"po_mode": "Match invoice"})
        safe(invoices)
    if can(role, "invoice.enter"):
        def batches():
            from engines.payment_run_service import get_run_stages
            need = get_run_stages(light=True)["needs_checks"]
            if len(need):
                add(f"{len(need)} batch(es) with invoices still need checks", "payment-run", ":material/payments:")
        safe(batches)
    if can(role, "check.issue"):
        def to_print():
            from engines.check_run_service import get_pending_check_run
            n = len(get_pending_check_run())
            if n:
                add(f"{n} parked check(s) to send to Chrysanth", "checks", ":material/upload_file:")
        safe(to_print)
    if can(role, "review.use"):
        def review():
            from engines.review_flags import open_count
            n = open_count()
            if n:
                add(f"{n} thing(s) the controls flagged, to review", "home", ":material/fact_check:")
        safe(review)
    return out
