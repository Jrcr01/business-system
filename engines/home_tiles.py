"""The Home launchpad's tiles (Fiori-style, 2026-10-01). Home itself (views/home.py) has no tile written into it: it draws whatever is registered here, grouped
and in order. A new module (the revenue cycle next) adds its own tiles with register_tile() -- one line each, from its own engines file -- and a new row with
one more Group; a tile whose page is not installed is simply not drawn.

A tile's figure is a small function returning (figure, unit, level):
    figure -- the big text on the tile ("5", "₱288K"); '' for a plain shortcut tile
    unit   -- the words under it ("to print", "as of Oct 01")
    level  -- 'none' (grey, nothing to do), 'ok' (fine), 'warn' (something is waiting, orange) or 'alert' (a real problem, red)
evaluate() runs one safely: a failing source gives a dash and never breaks Home."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

LEVELS = ("none", "ok", "warn", "alert")


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    hint: str = ""


@dataclass
class Tile:
    page_id: str                                     # the page's id in app.py (PAGES / menu)
    group: str                                       # a Group.key
    title: str
    figure: Callable[[], tuple[str, str, str]] | None = None
    icon: str = ""                                   # Material icon, ':material/...:'; empty = the page's own menu icon
    hint: str = ""                                   # shown under the title when there is no figure


GROUPS: list[Group] = [
    Group("daily", "Daily work", "What you open every day"),
    Group("purchasing", "Purchasing", "Orders and prices, before the invoice"),
    Group("other", "Other payables", "Loans and cards"),
    Group("records", "Records and reports", "Look things up, decide"),
    Group("system", "System", "Settings and safety"),
]
TILES: list[Tile] = []


def register_tile(tile: Tile) -> None:
    TILES.append(tile)


def register_group(group: Group, before: str | None = None) -> None:
    """Adds a row (a new module's), at the end or before an existing one."""
    if any(g.key == group.key for g in GROUPS):
        return
    at = next((i for i, g in enumerate(GROUPS) if g.key == before), len(GROUPS))
    GROUPS.insert(at, group)


def peso_short(amount: float) -> str:
    a = abs(float(amount))
    sign = "-" if amount < 0 and round(a) != 0 else ""
    if a < 1000:
        return f"{sign}₱{a:,.0f}"
    if a < 1_000_000:
        return f"{sign}₱{round(a / 1000):,}K"
    return f"{sign}₱{a / 1_000_000:.2f}M"


def evaluate(tile: Tile) -> dict:
    if tile.figure is None:
        return {"figure": "", "unit": tile.hint, "level": "none"}
    try:
        figure, unit, level = tile.figure()
        return {"figure": str(figure), "unit": unit, "level": level if level in LEVELS else "none"}
    except Exception:                                # a missing table or a bad row must not take the whole Home page down
        return {"figure": "—", "unit": "", "level": "none"}


def layout(installed_pages: set[str]) -> list[tuple[str, list[Tile]]]:
    """[(group key, tiles)] in group order, tiles in registration order, leaving out tiles whose page is not installed and groups left empty."""
    rows = []
    for g in GROUPS:
        tiles = [t for t in TILES if t.group == g.key and t.page_id in installed_pages]
        if tiles:
            rows.append((g.key, tiles))
    return rows


# --- the figures --------------------------------------------------------------------------------------------------------------------------------

def _count(sql: str, params: tuple = ()) -> int:
    from engines.common import get_conn
    with get_conn() as conn:
        return int(conn.execute(sql, params).fetchone()[0] or 0)


def _waiting(n: int, unit: str, level: str = "warn") -> tuple[str, str, str]:
    return str(n), unit, level if n else "none"


def _payment_run():
    from engines.payment_run_service import get_run_stages, waiting_count
    return _waiting(waiting_count(get_run_stages()), "waiting")


def _order_statements():
    n = _count("""SELECT COUNT(*) FROM order_statements os JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
                  WHERE os.status != 'Cancelled' AND v.net_total - COALESCE((SELECT SUM(amount) FROM checks_issued k
                        WHERE k.statement_id = os.statement_id AND k.status = 'Active'), 0) > 0.005""")
    return _waiting(n, "with a balance")


def _checks():
    from engines.check_run_service import get_pending_check_run
    return _waiting(len(get_pending_check_run()), "to print")


def _bank_cash():
    from engines.cash_insights_service import cash_position, forecast
    pos = cash_position()
    if pos is None:
        return "", "No bank statement yet", "none"
    f = forecast(30)
    dip = f.get("first_below") if f else None
    return peso_short(pos["balance"]), (f"under the buffer {dip:%b %d}" if dip else f"as of {pos['as_of']:%b %d}"), "alert" if dip else "ok"


def _planner():
    from engines.check_date_planner import open_items
    return _waiting(len(open_items()), "to plan")


def _purchase_orders():
    n = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('DRAFT', 'ORDERED', 'PARTIAL')")
    over = _count("SELECT COUNT(*) FROM purchase_orders WHERE over_cash_plan = 1 AND cash_ack_at IS NULL AND status IN ('ORDERED', 'PARTIAL')")
    if over:
        return str(n), f"open · {over} over plan", "alert"
    return _waiting(n, "open", "ok")


def _price_lists():
    n = _count("SELECT COUNT(DISTINCT creditor_id) FROM supplier_price_list WHERE is_active = 1")
    return str(n), "suppliers with prices", "ok" if n else "none"


def _lenders():
    n = _count("SELECT COUNT(*) FROM loan_notes WHERE status = 'Active'")
    return str(n), "active loan notes", "ok" if n else "none"


def _bank_loans():
    from datetime import timedelta
    import pandas as pd
    from engines.clock import manila_today
    from engines.reports_service import get_payment_schedule_all_tracks
    sched = get_payment_schedule_all_tracks()
    if sched.empty:
        return "₱0", "due in 30 days", "none"
    due = pd.to_datetime(sched["due_date"]).dt.date
    mine = sched[(sched["track"] == "Bank Loan") & (due >= manila_today()) & (due <= manila_today() + timedelta(days=30))]
    total = float(mine["amount"].sum())
    return peso_short(total), "due in 30 days", "warn" if total else "none"


def _credit_cards():
    n = _count("SELECT COUNT(*) FROM v_cc_due_alerts WHERE urgency_status LIKE 'CRITICAL%'")
    return _waiting(n, "due within 3 days", "alert")


def _dashboard():
    from engines.attention_service import attention_count
    return _waiting(attention_count(), "need attention")


def _creditors():
    n = _count("SELECT COUNT(*) FROM creditors WHERE is_active = 1 AND creditor_type = 'SUPPLIER'")
    return str(n), "active suppliers", "none"


def _data_health():
    from engines.data_health_service import open_count
    return _waiting(open_count(), "to look at")


for _t in (
    Tile("payment-run", "daily", "Payment run", _payment_run),
    Tile("add-data", "daily", "Add data", hint="Invoices, checks, memos"),
    Tile("order-statements", "daily", "Order statements", _order_statements),
    Tile("checks", "daily", "Checks and checkbook", _checks),
    Tile("bank-cash", "daily", "Bank and cash", _bank_cash),
    Tile("planner", "daily", "Check-date planner", _planner),
    Tile("purchase-orders", "purchasing", "Purchase orders", _purchase_orders),
    Tile("price-lists", "purchasing", "Price lists", _price_lists),
    Tile("lenders", "other", "Lenders", _lenders),
    Tile("bank-loans", "other", "Bank loans", _bank_loans),
    Tile("credit-cards", "other", "Credit cards", _credit_cards),
    Tile("dashboard", "records", "Dashboard", _dashboard),
    Tile("creditors", "records", "Creditors", _creditors),
    Tile("analytics", "records", "Analytics", hint="Trends and mix"),
    Tile("reports", "records", "Reports", hint="Lists and decisions"),
    Tile("settings", "system", "Settings", hint="Numbers and shortcuts"),
    Tile("admin", "system", "Admin and audit", hint="Booklets, backup"),
    Tile("data-health", "system", "Data health", _data_health),
):
    register_tile(_t)
