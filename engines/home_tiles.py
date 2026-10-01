"""The Home launchpad's tiles (Fiori-style, 2026-10-01). Home itself (views/home.py) has no tile written into it: it draws whatever is registered here, grouped
and in order. A new module (the revenue cycle next) adds its own tiles with register_tile() -- one line each, from its own engines file -- and a new row with
one more Group; a tile whose page is not installed is simply not drawn.

A tile's figure is a small function returning (figure, unit, level):
    figure -- the big text on the tile ("5", "₱288K"); '' for a plain shortcut tile
    unit   -- the words under it ("to print", "as of Oct 01")
    level  -- 'none' (grey, nothing to do), 'ok' (fine), 'warn' (something is waiting, orange) or 'alert' (a real problem, red)
evaluate() runs one safely: a failing source gives a dash and never breaks Home."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

LEVELS = ("none", "ok", "warn", "alert")


@dataclass(frozen=True)
class Group:
    key: str
    title: str
    hint: str = ""


@dataclass(frozen=True)
class Action:
    """One button in a tile's 'What do you want to do?' panel: it opens `page_id` (default: the tile's page) with these page widgets pre-set
    (`state` = the session keys views.shared.go_to() already sets for the global search)."""
    label: str
    hint: str = ""
    icon: str = ""
    state: tuple = ()                                # ((widget key, value), ...) -- a tuple so the dataclass stays hashable
    page_id: str | None = None
    forecast: bool = False                           # belongs to the cash forecast: hidden while the forecast is switched off (Settings)


def act(label: str, hint: str = "", icon: str = "", page_id: str | None = None, forecast: bool = False, **state) -> Action:
    return Action(label, hint, icon, tuple(state.items()), page_id, forecast)


@dataclass
class Tile:
    page_id: str                                     # the page's id in app.py (PAGES / menu)
    group: str                                       # a Group.key
    title: str
    figure: Callable[[], tuple[str, str, str]] | None = None
    icon: str = ""                                   # Material icon, ':material/...:'; empty = the page's own menu icon
    hint: str = ""                                   # shown under the title when there is no figure
    actions: list = field(default_factory=list)      # Action buttons; two or more make the tile open a panel, fewer open the page straight away

    @property
    def visible_actions(self) -> list:
        from engines.settings_registry import forecast_shown
        return [a for a in self.actions if forecast_shown() or not a.forecast]

    @property
    def has_panel(self) -> bool:
        return len(self.visible_actions) >= 2


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
    from engines.pdc_release_service import release_summary
    n, rel = len(get_pending_check_run()), release_summary()["waiting"]
    return _waiting(n + rel, f"{n} to print · {rel} to release")


def _bank_cash():
    from engines.cash_insights_service import cash_position, forecast_if_shown
    pos = cash_position()
    if pos is None:
        return "", "No bank statement yet", "none"
    f = forecast_if_shown(30)
    dip = f.get("first_below") if f else None
    return peso_short(pos["balance"]), (f"under the buffer {dip:%b %d}" if dip else f"as of {pos['as_of']:%b %d}"), "alert" if dip else "ok"


def _payables_due():
    from engines.payables_due_service import next_days
    r = next_days(7)
    return peso_short(r["amount"]), f"next 7 days · {r['checks']} check(s)", "warn" if r["amount"] else "none"


def _planner():
    from engines.check_date_planner import open_items
    return _waiting(len(open_items()), "to plan")


def _purchase_orders():
    n = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('DRAFT', 'ORDERED', 'PARTIAL')")
    from engines.po_rules import orders_over_plan
    over = len(orders_over_plan())                  # empty while the cash forecast is hidden
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


A = act
for _t in (
    Tile("payment-run", "daily", "Payment run", _payment_run),
    Tile("add-data", "daily", "Add data", hint="Invoices, checks, memos", actions=[
        A("Enter an invoice", "Supplier invoice into a batch", ":material/receipt:", ad_kind="Invoices / credit memos", ad_inv_what="Invoices"),
        A("Credit memo", "A discount or return from a supplier", ":material/undo:", ad_kind="Invoices / credit memos", ad_inv_what="Credit memos"),
        A("Handwritten check", "Record a check written by hand", ":material/edit_note:", ad_kind="Handwritten check"),
        A("Guarantee check", "An undated check held as security", ":material/verified_user:", ad_kind="Guarantee check"),
        A("Credit card statement", "Enter a card's statement", ":material/credit_card:", ad_kind="Credit card statement"),
        A("Loan note or bank loan", "New loan, installment or schedule", ":material/handshake:", ad_kind="Loans"),
        A("Supplier, booklet or spoiled check", "Records and checkbook", ":material/contacts:", ad_kind="Records and checkbook")]),
    Tile("order-statements", "daily", "Order statements", _order_statements, actions=[
        A("Find a batch", "Open a supplier's batch: invoices, checks, void, print", ":material/search:", os_mode="Browse batches"),
        A("New batch", "Start a batch for a supplier", ":material/add:", os_mode="New batch"),
        A("Print statements in bulk", "One PDF for many batches", ":material/picture_as_pdf:", os_mode="Browse batches", bulk_print_exp=True)]),
    Tile("checks", "daily", "Checks and checkbook", _checks),
    Tile("bank-cash", "daily", "Bank and cash", _bank_cash, actions=[
        A("Import a bank statement", "Match your checks to the bank", ":material/upload_file:", bank_cash_tab=":material/upload_file: Import a statement"),
        A("Reconciliation", "What still needs a look", ":material/rule:", bank_cash_tab=":material/rule: Reconciliation"),
        A("Cash forecast", "Day by day, orders included", ":material/trending_up:", forecast=True, bank_cash_tab=":material/trending_up: Cash forecast"),
        A("Checks not yet cleared", "Waiting to be deposited", ":material/hourglass_top:", bank_cash_tab=":material/hourglass_top: Checks not yet cleared")]),
    Tile("payables-due", "daily", "Payables due", _payables_due, actions=[
        A("Week by week", "What can hit the bank, by week", ":material/calendar_view_week:", pd_tab="Week by week"),
        A("By supplier", "Who holds checks, which orders are coming", ":material/groups:", pd_tab="By supplier"),
        A("Past their date", "Checks the suppliers have not deposited yet", ":material/hourglass_top:", pd_tab="Past their date")]),
    Tile("planner", "daily", "Check-date planner", _planner),
    Tile("purchase-orders", "purchasing", "Purchase orders", _purchase_orders, actions=[
        A("Match a supplier invoice", "Goods arrived: invoice into a batch at the actual price", ":material/inventory_2:", po_mode="Receive"),
        A("New order", "From a supplier's price list", ":material/add_shopping_cart:", po_mode="New order"),
        A("Orders", "Open, late and over-plan orders; PO PDF", ":material/list_alt:", po_mode="Orders")]),
    Tile("price-lists", "purchasing", "Price lists", _price_lists, actions=[
        A("Current prices", "What each supplier charges", ":material/sell:", pl_tab="Current prices"),
        A("Add a price", "Type one in", ":material/edit:", pl_tab="Add a price"),
        A("Import a supplier file", "CSV or Excel", ":material/upload_file:", pl_tab="Import a file")]),
    Tile("lenders", "other", "Lenders", _lenders, actions=[
        A("Browse loan notes", "Schedule, checks, status", ":material/search:", ln_mode="Browse loan notes"),
        A("New loan note", "", ":material/add:", ln_mode="New loan note")]),
    Tile("bank-loans", "other", "Bank loans", _bank_loans, actions=[
        A("Browse bank loans", "Schedule and servicing", ":material/search:", bl_mode="Browse bank loans"),
        A("New bank loan", "", ":material/add:", bl_mode="New bank loan")]),
    Tile("credit-cards", "other", "Credit cards", _credit_cards, actions=[
        A("Statements", "Who, how much, when due", ":material/receipt_long:", cc_tab=":material/receipt_long: Statements"),
        A("Cards and cardholders", "", ":material/credit_card:", cc_tab=":material/credit_card: Cards & cardholders"),
        A("Missing statement check", "", ":material/fact_check:", cc_tab=":material/fact_check: Missing statement check")]),
    Tile("dashboard", "records", "Dashboard", _dashboard),
    Tile("creditors", "records", "Creditors", _creditors),
    Tile("analytics", "records", "Analytics", hint="Trends and mix"),
    Tile("reports", "records", "Reports", hint="Lists and decisions", actions=[
        A("Overview", "Highlights and insights", ":material/insights:", rpt_mode="Standard reports", report_type_picker="Overview — highlights & insights"),
        A("Schedule of payments", "Everything still owed, by date", ":material/event:", rpt_mode="Standard reports", report_type_picker="Schedule of payments — all tracks"),
        A("Cleared checks", "From the bank", ":material/done_all:", rpt_mode="Standard reports", report_type_picker="Cleared checks — from the bank"),
        A("Supplier history", "Invoices and checks of one supplier", ":material/history:", rpt_mode="Standard reports", report_type_picker="Supplier — invoice & check history"),
        A("Possible duplicates", "Check everything on file", ":material/content_copy:", rpt_mode="Standard reports", report_type_picker="Possible duplicates — check everything on file"),
        A("Decision reports", "Answers for choices", ":material/lightbulb:", rpt_mode="Standard reports", report_type_picker="Decision reports — answers for choices")]),
    Tile("settings", "system", "Settings", hint="Numbers and shortcuts"),
    Tile("admin", "system", "Admin and audit", hint="Booklets, backup"),
    Tile("data-health", "system", "Data health", _data_health),
):
    register_tile(_t)
