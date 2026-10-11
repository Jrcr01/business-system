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
from engines.common import us_date

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
    needs: str | None = None                         # the access-matrix action this button leads to (engines/permissions.py): hidden from a role without it


def act(label: str, hint: str = "", icon: str = "", page_id: str | None = None, forecast: bool = False, needs: str | None = None, **state) -> Action:
    return Action(label, hint, icon, tuple(state.items()), page_id, forecast, needs)


@dataclass
class Tile:
    page_id: str                                     # the page's id in app.py (PAGES / menu)
    group: str                                       # a Group.key
    title: str
    figure: Callable[[], tuple[str, str, str]] | None = None
    icon: str = ""                                   # Material icon, ':material/...:'; empty = the page's own menu icon
    hint: str = ""                                   # shown under the title when there is no figure
    actions: list = field(default_factory=list)      # Action buttons; two or more make the tile open a panel, fewer open the page straight away
    key: str = ""                                    # a tile of its own on a page that already has a tile (Receive goods opens Purchase orders): its unique id; empty = the page id
    state: tuple = ()                                # ((widget key, value), ...) pre-set on the page when the tile is pressed (the same keys the actions use)
    needs: str | None = None                         # the access-matrix action behind the tile: the tile is left out for a role without it

    @property
    def tid(self) -> str:
        return self.key or self.page_id

    @property
    def visible_actions(self) -> list:
        from engines.acting_user import acting_email, acting_role
        from engines.permissions import can
        from engines.settings_registry import forecast_shown
        shown, role, me = forecast_shown(), acting_role() or "admin", acting_email()
        return [a for a in self.actions if (shown or not a.forecast) and (a.needs is None or can(role, a.needs, created_by=me, me=me))]

    @property
    def has_panel(self) -> bool:
        return len(self.visible_actions) >= 2


PANELS_KEY = "home_tile_panels"


def panels_enabled() -> bool:
    """Do tiles with several actions open the 'What do you want to do?' pop-up? Off by default (owner, 2026-10-02: a tile opens its page); Settings can switch it on."""
    from engines.settings_store import get_setting
    return str(get_setting(PANELS_KEY, "0")) == "1"


GROUPS: list[Group] = [                              # in the order the money moves (2026-10-03); a new module adds its row with register_group(..., before="buy")
    Group("buy", "Buy", "Prices and orders, before the invoice"),
    Group("stock", "Stock", "What is on hand; counts and lending"),          # the revenue cycle, stage A (owner, 2026-10-11)
    Group("sell", "Sell", "Scan out by day, the invoices after close, customers"),
    Group("pay", "Pay suppliers", "Invoice to check to bank"),
    Group("bank", "Bank", "The bank statement is the truth"),
    Group("other", "Other payables", "Loans and cards"),
    Group("records", "Records", "Look things up"),
    Group("analytics", "Analytics", "Dashboards: owed, due, cleared, spend"),
    Group("system", "Set-up and system", "Settings and safety"),
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


def _tile_allowed(tile: Tile) -> bool:
    if tile.needs is None:
        return True
    from engines.acting_user import acting_email, acting_role
    from engines.permissions import can
    return can(acting_role() or "admin", tile.needs, created_by=acting_email(), me=acting_email())


def layout(installed_pages: set[str]) -> list[tuple[str, list[Tile]]]:
    """[(group key, tiles)] in group order, tiles in registration order, leaving out tiles whose page is not installed and groups left empty."""
    rows = []
    for g in GROUPS:
        tiles = [t for t in TILES if t.group == g.key and t.page_id in installed_pages and _tile_allowed(t)]
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
    return _waiting(waiting_count(get_run_stages(light=True)), "waiting")


def _order_statements():
    n = _count("""SELECT COUNT(*) FROM order_statements os JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
                  WHERE os.status != 'Cancelled' AND v.net_total - COALESCE((SELECT SUM(amount) FROM checks_issued k
                        WHERE k.statement_id = os.statement_id AND k.status = 'Active'), 0) > 0.005""")
    return _waiting(n, "with a balance")


def _review():
    """Checks and review (owner, 2026-10-09): the review lines, data checks and possible duplicates still to look at."""
    from engines import data_health_service as dh
    from engines import review_flags as rf
    from engines.duplicate_scan_service import scan_possible_duplicates
    n = rf.open_count() + dh.open_count() + len(scan_possible_duplicates())
    return str(n), "to look at", ("warn" if n else "ok")


def _calendar():
    """Calendar (owner, 2026-10-09; replaces Payables due): what falls on today, every kind."""
    from engines.clock import manila_today
    from engines.reports_service import get_payment_schedule_all_tracks
    df = get_payment_schedule_all_tracks()
    if df.empty:
        return "₱0", "due today", "none"
    import pandas as pd
    today = df[pd.to_datetime(df["actual_payment_date"]).dt.date == manila_today()]
    return peso_short(float(today["amount"].sum())), f"due today · {len(today)} item(s)", ("warn" if len(today) else "none")


def _over_limit_days():
    """Days in the next 30 above the daily ceiling (owner, 2026-10-09: "no AP above 350,000 to 400,000 per day"), counting everything that goes out."""
    from datetime import timedelta
    from engines import day_cap
    from engines.clock import manila_today
    c = day_cap.ceiling()
    if c <= 0:
        return "—", "daily limit off", "none"
    today = manila_today()
    over = {d: v for d, v in day_cap.day_totals(today, today + timedelta(days=30)).items() if v > c + 0.005}
    if not over:
        return "0", "days over the limit · next 30 days", "ok"
    first = min(over)
    return str(len(over)), f"over ₱{c:,.0f} · first {first:%m/%d} · ₱{sum(over.values()) - c * len(over):,.0f} above", "alert"


def _checks():
    """Daily payables (owner, 2026-10-09): what is due today or overdue, every track, until it is paid."""
    from engines.clearing import daily_payables
    from engines.clock import manila_today
    df = daily_payables()
    if df.empty:
        return "0", "nothing due today", "none"
    due = df[df["ticked"].isna() & df["due"]]
    overdue = int((due["day"] < manila_today().isoformat()).sum())
    return str(len(due)), f"{peso_short(float(due['amount'].sum()))} due · {overdue} overdue", ("warn" if overdue else ("ok" if len(due) else "none"))


def _bank_cash():
    from engines.cash_insights_service import cash_position, forecast_if_shown
    pos = cash_position()
    if pos is None:
        return "", "No bank statement yet", "none"
    f = forecast_if_shown(30)
    dip = f.get("first_below") if f else None
    return peso_short(pos["balance"]), (f"under the buffer {dip:%m/%d}" if dip else f"as of {pos['as_of']:%m/%d}"), "alert" if dip else "ok"


def _booklets():
    """Open leaves in the booklets I hold (or in all of them when I hold none), and whether a booklet has leaves unaccounted for."""
    from engines.acting_user import acting_email
    from engines.booklet_rules import listing
    books = [b for b in listing() if b["status"] == "Active"]
    if not books:
        return "", "No booklet registered", "none"
    me = (acting_email() or "").strip().lower()
    uid = _count("SELECT COALESCE((SELECT user_id FROM app_users WHERE LOWER(email) = ?), 0)", (me,)) if me else 0
    mine = [b for b in books if uid and b.get("holder_user_id") == uid]
    shown = mine or books
    gaps = [b for b in shown if b["unrecorded"] or b["skipped"] or b["duplicates"]]
    text = ("leaves open in my " if mine else "leaves open in ") + (f"{len(shown)} booklets" if len(shown) != 1 else "booklet " + shown[0]["code"])
    if gaps:
        return str(sum(b["open"] for b in shown)), text + f" · {len(gaps)} with leaves to account for", "alert"
    return str(sum(b["open"] for b in shown)), text, "ok"


def _payables_due():
    from engines.payables_due_service import next_days
    r = next_days(7)
    return peso_short(r["amount"]), f"next 7 days · {r['checks']} check(s)", "warn" if r["amount"] else "none"


def _release():
    from engines.release_slip_service import open_slips
    slips = open_slips()
    late = int((slips["days_waiting"] > 7).sum()) if not slips.empty else 0
    return str(len(slips)), "slips to collect" + (f" · {late} over a week" if late else ""), "alert" if late else ("warn" if len(slips) else "none")


def _planner():
    from engines.check_date_planner import open_items
    return _waiting(len(open_items()), "to plan")


def _purchase_orders():
    n = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('DRAFT', 'ORDERED', 'PARTIAL')")
    from engines.po_rules import orders_over_plan
    over = len(orders_over_plan())                  # empty while the cash forecast is hidden
    if over:
        return str(n), f"open · {over} over plan", "alert"
    sent = _count("SELECT COUNT(*) FROM purchase_orders WHERE status = 'DRAFT' AND requested_at IS NOT NULL")       # owner, 2026-10-11: waiting for the supplier's answer
    if sent:
        from datetime import timedelta

        from engines.clock import manila_now
        cutoff = (manila_now() - timedelta(days=2, hours=8)).strftime("%Y-%m-%d %H:%M:%S")       # requested_at is stamped in UTC
        slow = _count("SELECT COUNT(*) FROM purchase_orders WHERE status = 'DRAFT' AND requested_at IS NOT NULL AND requested_at < ?", (cutoff,))
        return str(n), f"open · {sent} waiting for the supplier" + (f" ({slow} over 2 days)" if slow else ""), "warn"
    return _waiting(n, "open", "ok")


def _stock_items():
    low = _count("SELECT COUNT(*) FROM items i WHERE is_active = 1 AND on_hand >= 0 AND on_hand <= reorder_level AND EXISTS (SELECT 1 FROM stock_ledger s WHERE s.item_id = i.id)")
    neg = _count("SELECT COUNT(*) FROM items WHERE is_active = 1 AND on_hand < 0")
    if neg:
        return str(neg), "below zero" + (f" · {low} low" if low else ""), "alert"
    return _waiting(low, "low on stock")


def _stock_counts():
    from engines.count_rules import waiting_count
    return _waiting(waiting_count(), "differences to approve", "alert")


def _affiliates():
    n = _count("SELECT COUNT(*) FROM affiliate_move_lines WHERE qty - qty_settled > 0.0000001")
    return _waiting(n, "lent or borrowed, open")


def _scan_out():
    """Pieces scanned out today, on slips (owner, 2026-10-11: the scan is the monitoring; the invoice follows after close)."""
    from engines.clock import manila_today
    from engines.common import get_conn
    with get_conn() as conn:
        pcs = conn.execute("""SELECT COALESCE(SUM(l.qty), 0) FROM sales s JOIN sale_lines l ON l.sale_id = s.sale_id
                              WHERE s.release_no IS NOT NULL AND s.status != 'VOID' AND s.sale_date = ?""", (str(manila_today()),)).fetchone()[0]
    return f"{float(pcs):g}", "pcs scanned out today", "none"


def _to_record():
    """The evening's work: slips waiting for their invoice; quiet, the next SI number."""
    from engines.sales_rules import next_si_no
    open_n = _count("SELECT COUNT(*) FROM sales WHERE status = 'RELEASED'")
    if open_n:
        return str(open_n), "slip(s) to invoice", "alert"
    nxt = next_si_no()
    return (f"{nxt:06d}", "the next SI no.", "none") if nxt else ("—", "no booklet in use", "warn")


def _sales_today():
    from engines.clock import manila_today
    from engines.common import get_conn
    with get_conn() as conn:
        n, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(total), 0) FROM sales WHERE status = 'SAVED' AND sale_date = ?", (str(manila_today()),)).fetchone()
    return str(n), f"invoices · ₱{float(total):,.0f}", "ok" if n else "none"


def _customers():
    return str(_count("SELECT COUNT(*) FROM customers WHERE is_active = 1")), "customers", "none"


def _si_booklets():
    from engines.sales_rules import booklets
    rows = booklets()
    skipped = sum(len(b["skipped"]) for b in rows)
    if skipped:
        return str(skipped), "numbers skipped", "alert"
    use = next((b for b in rows if b["status"] == "IN_USE"), None)
    return (str(use["unused"]), "leaves left in " + use["code"], "warn" if use["unused"] <= 5 else "none") if use else ("0", "no booklet in use", "warn")


def _receive_goods():
    from engines.clock import manila_today
    n = _count("SELECT COUNT(DISTINCT po_id) FROM purchase_order_lines WHERE qty - qty_received > 0.0000001 AND po_id IN (SELECT po_id FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL'))")
    late = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL') AND expected_delivery_date < ?", (str(manila_today()),))
    return str(n), "orders waiting" + (f" · {late} late" if late else ""), "alert" if late else ("warn" if n else "none")


def _price_lists():
    n = _count("SELECT COUNT(DISTINCT creditor_id) FROM supplier_price_list WHERE is_active = 1")
    return str(n), "suppliers with prices", "ok" if n else "none"


def _lenders():
    n = _count("SELECT COUNT(*) FROM loan_notes WHERE status = 'Active'")
    return str(n), "active loan notes", "ok" if n else "none"


def _an_payables():
    from engines.reports_service import get_combined_ap_summary
    df = get_combined_ap_summary()
    v = float(df.loc[df["track"].str.startswith("Supplier"), "outstanding"].sum())
    return peso_short(v), "owed to suppliers", "ok" if v else "none"


def _an_lenders():
    from engines.reports_service import get_combined_ap_summary
    df = get_combined_ap_summary()
    v = float(df.loc[df["track"].str.startswith("Private Lender"), "outstanding"].sum())
    return peso_short(v), "owed to lenders", "ok" if v else "none"


def _an_bank():
    from engines.common import get_conn
    with get_conn() as conn:
        r = conn.execute("SELECT COALESCE(posting_date, txn_date), balance FROM bank_transactions WHERE balance IS NOT NULL "
                         "ORDER BY COALESCE(posting_date, txn_date) DESC, txn_id DESC LIMIT 1").fetchone()
    if not r:
        return "—", "no bank statement yet", "none"
    return peso_short(float(r[1])), f"bank balance, {us_date(r[0])}", "ok"


def _an_purchasing():
    n = _count("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('ORDERED', 'PARTIAL')")
    return str(n), "open purchase orders", "ok" if n else "none"


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
    Tile("price-lists", "buy", "Price lists", _price_lists, actions=[
        A("Current prices", "What each supplier charges", ":material/sell:", pl_tab="Prices", pl_show="This supplier"),
        A("Add a price", "Type one in", ":material/edit:", needs="price.write", pl_tab="Add a price"),
        A("Import the Excel price list", "The template with Tires, Battery, Mags, Rims", ":material/upload_file:", needs="price.write", pl_tab="Import Excel"),
        A("Products and prices side by side", "Our item codes; which supplier is cheapest", ":material/compare_arrows:", pl_tab="Prices", pl_show="All suppliers side by side")]),
    Tile("purchase-orders", "buy", "Purchase orders", _purchase_orders, actions=[
        A("New order", "From a supplier's price list", ":material/add_shopping_cart:", needs="po.create", po_mode="New order"),
        A("Orders", "Open, late and over-plan orders; PO PDF", ":material/list_alt:", po_mode="Orders")]),
    Tile("purchase-orders", "buy", "Receive", _receive_goods, key="receive-goods", icon=":material/inventory_2:", state=(("po_mode", "Receive"),), needs="po.receive_goods"),
    Tile("order-statements", "pay", "Order statements", _order_statements, actions=[
        A("Find a batch", "Open a supplier's batch: invoices, checks, void, print", ":material/search:", os_mode="Browse batches"),
        A("New batch", "Start a batch for a supplier", ":material/add:", os_mode="New batch"),
        A("Print statements in bulk", "One PDF for many batches", ":material/picture_as_pdf:", os_mode="Browse batches", bulk_print_exp=True)]),
    Tile("payment-run", "pay", "Payment run", _payment_run),
    Tile("checks", "pay", "Daily payables", _checks, icon=":material/fact_check:"),
    Tile("calendar", "pay", "Calendar", _calendar, icon=":material/calendar_month:"),
    Tile("calendar", "pay", "Days over the limit", _over_limit_days, key="over-limit-days", icon=":material/warning:"),
    Tile("release", "pay", "Release checks", _release),
    Tile("payables-due", "pay", "Payables due", _payables_due, actions=[
        A("Everything owed, with downloads", "All tracks, by date; CSV, Excel and PDF", ":material/event:", pd_tab="Everything owed"),
        A("Week by week", "What can hit the bank, by week", ":material/calendar_view_week:", pd_tab="Week by week"),
        A("By supplier", "Who holds checks, which orders are coming", ":material/groups:", pd_tab="By supplier"),
        A("Past their date", "Checks the suppliers have not deposited yet", ":material/hourglass_top:", pd_tab="Past their date")]),
    Tile("add-data", "pay", "Add data", hint="Invoices without an order, handwritten checks, loans, cards", actions=[
        A("Enter an invoice", "Supplier invoice into a batch", ":material/receipt:", ad_kind="Invoices / credit memos", ad_inv_what="Invoices"),
        A("Credit memo", "A discount or return from a supplier", ":material/undo:", ad_kind="Invoices / credit memos", ad_inv_what="Credit memos"),
        A("Handwritten check", "Record a check written by hand", ":material/edit_note:", ad_kind="Handwritten check"),
        A("Guarantee check", "An undated check held as security", ":material/verified_user:", ad_kind="Guarantee check"),
        A("Credit card statement", "Enter a card's statement", ":material/credit_card:", ad_kind="Credit card statement"),
        A("Loan note or bank loan", "New loan, installment or schedule", ":material/handshake:", ad_kind="Loans"),
        A("Supplier, booklet or spoiled check", "Records and checkbook", ":material/contacts:", ad_kind="Records and checkbook")]),
    Tile("planner", "pay", "Check-date planner", _planner),
    Tile("booklets", "bank", "Check booklets", _booklets, icon=":material/menu_book:", needs="booklets.use"),
    Tile("stock", "stock", "Items and stock", _stock_items, needs="inventory.use"),
    Tile("stock-counts", "stock", "Stock counts", _stock_counts, needs="inventory.use"),
    Tile("affiliates", "stock", "Affiliates", _affiliates, needs="inventory.use"),
    Tile("sales", "sell", "Scan out", _scan_out, key="scan-out", icon=":material/barcode_scanner:", state=(("sales_view", "scan"),), needs="sales.use"),
    Tile("sales", "sell", "Record the day's invoices", _to_record, needs="sales.use"),
    Tile("sales", "sell", "Sales today", _sales_today, key="sales-today", icon=":material/today:", state=(("sales_view", "day"),), needs="sales.use"),
    Tile("customers", "sell", "Customers", _customers, needs="sales.use"),
    Tile("si-booklets", "sell", "Invoice booklets", _si_booklets, needs="sales.use"),
    Tile("bank-cash", "bank", "Bank and cash", _bank_cash, actions=[
        A("Import a bank statement", "Match your checks to the bank", ":material/upload_file:", bank_cash_tab=":material/upload_file: Import a statement"),
        A("Reconciliation", "What still needs a look", ":material/rule:", bank_cash_tab=":material/rule: Reconciliation"),
        A("Cash forecast", "Day by day, orders included", ":material/trending_up:", forecast=True, bank_cash_tab=":material/trending_up: Cash forecast"),
        A("Checks not yet cleared", "Waiting to be deposited", ":material/hourglass_top:", bank_cash_tab=":material/hourglass_top: Checks not yet cleared")]),
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
    Tile("reports", "records", "Reports", hint="Lists and decisions", actions=[
        A("Overview", "Highlights and insights", ":material/insights:", rpt_mode="Standard reports", report_type_picker="Overview — highlights & insights"),

        A("Cleared checks", "From the bank", ":material/done_all:", rpt_mode="Standard reports", report_type_picker="Cleared checks — from the bank"),
        A("Statement of account", "A supplier's, lender's, bank loan's or card's invoices, checks and dues", ":material/history:", rpt_mode="Standard reports",
          report_type_picker="Statement of account — supplier, lender, bank loan or card", rpt_soa_view="Supplier history"),
        A("Possible duplicates", "Check everything on file", ":material/content_copy:", rpt_mode="Standard reports", report_type_picker="Possible duplicates — check everything on file"),
        A("Decision reports", "Answers for choices", ":material/lightbulb:", rpt_mode="Standard reports", report_type_picker="Decision reports — answers for choices")]),
    Tile("analytics", "analytics", "Payables", _an_payables, key="an-payables", icon=":material/receipt_long:", state=(("analytics_tab", "payables"),), needs="reports.use"),
    Tile("analytics", "analytics", "Lenders", _an_lenders, key="an-lenders", icon=":material/handshake:", state=(("analytics_tab", "lenders"),), needs="reports.use"),
    Tile("analytics", "analytics", "Bank and cash", _an_bank, key="an-bank", icon=":material/account_balance:", state=(("analytics_tab", "bank"),), needs="reports.use"),
    Tile("analytics", "analytics", "Purchasing", _an_purchasing, key="an-purchasing", icon=":material/shopping_cart:", state=(("analytics_tab", "purchasing"),), needs="reports.use"),
    Tile("creditors", "system", "Creditors", _creditors),
    Tile("controls", "system", "Roles and controls", hint="Who can do what, and what is checked"),
    Tile("settings", "system", "Settings", hint="Numbers and shortcuts"),
    Tile("admin", "system", "Admin and audit", hint="Booklets, backup"),
    Tile("data-health", "system", "Data health", _data_health),
    Tile("review", "system", "Checks and review", _review, icon=":material/fact_check:"),
):
    register_tile(_t)
