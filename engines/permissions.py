"""The access matrix: who can do what (B8, 2026-10-02). ONE table, ACTIONS, says it all; the menu, the buttons and the engines read from it, and the Settings screen
"Who can do what" prints it, so the screen and the rules cannot disagree.

Roles:  admin      the System admin (JRCR only): everything, including users and roles, settings, the audit log, data fixes and the developer view.
        owner      family members (2026-10-03): every business action (orders, receiving, invoices, checks, bank, loans, reports, release, approvals) but no system
                   action: not users and roles, settings, the audit log, data fixes or the carve-out.
        sales      the counter: sales, the day sheet, booklets, customers, items and stock (the Django ERP's Sales role).
        payables   a clerk for the payables side: enters invoices, prepares and parks checks, receives purchase orders, imports bank statements; cannot issue,
                   void or change settings.
        purchasing the owner's brother: price lists, discounts and purchase orders (his own); sees the committed payables by week, never checks or the bank.
        releaser   hands the checks to the supplier (the owner's mother): records goods received and marks checks released; sees nothing else.
        viewer     read-only: reports, the order list.

Levels in the table:  "Y" = may do it   "view" = may see, not change   "own" = only what this person created   None = no.
Nothing is ever hard-deleted for any role: 'delete' means cancel, void or switch off, with a reason, and it stays in the audit log.

Per-person switches (owner, 2026-10-08): "all users are family members hence all owners. System admin is the supreme account. The system admin can limit
privileges of the users." Everyone is an Owner; the System admin switches off, per person, the actions they may NOT do (table user_restrictions, shown grouped
by workflow: WORKFLOW). can(), require() and allowed_pages() apply the switches of the person acting right now (engines/acting_user.py), so the Django menus and
buttons, the /more/ screens and every engine guard obey them without any change where they are called. The System admin is never switched off.

  * can(role, action, created_by=None, me=None)  -- may this role do it (for "own": is it theirs); False when it is switched off for the person acting
  * require(action, created_by=None)             -- the engines' guard: (ok, message), for whoever is acting right now (no one signed in = the owner, as in scripts and tests)
  * allowed_pages / filter_menu / may_search / hide_check_details -- what the menu and sidebar use"""
from __future__ import annotations

from engines.acting_user import acting_email, acting_role

ROLES = ("admin", "owner", "sales", "payables", "purchasing", "releaser", "viewer")
ROLE_LABEL = {"admin": "System admin", "owner": "Owner", "sales": "Sales", "payables": "Payables", "purchasing": "Purchasing", "releaser": "Releaser", "viewer": "Viewer"}
Y, VIEW, OWN = "Y", "view", "own"

# action id: (group, what it means, admin, sales, payables, purchasing, viewer) -- the Owner column is the admin's, less SYSTEM_ONLY; the Releaser's is RELEASER
SYSTEM_ONLY = {"carveout.use", "users.manage", "audit.view", "settings.edit", "health.fix"}      # the System admin's alone (family owners never get these)
RELEASER = {"home.open": "Y", "account.own": "Y", "po.receive_goods": "Y", "check.release": "Y"}
_ROWS: list[tuple[str, str, str, tuple]] = [
    ("home.open", "Purchasing", "Home", (Y, Y, Y, Y, Y)),
    ("more.use", "System", "Sign in to the old screens (/more/) not yet in the main system", (Y, Y, Y, Y, Y)),
    ("price.view", "Purchasing", "See price lists, the product catalog and prices side by side", (Y, None, VIEW, Y, VIEW)),
    ("price.write", "Purchasing", "Add or import prices (Excel template), add items, set supplier discounts", (Y, None, None, Y, None)),
    ("po.view", "Purchasing", "See purchase orders and their trail", (Y, None, Y, Y, VIEW)),
    ("po.create", "Purchasing", "Create and edit a purchase order draft", (Y, None, None, OWN, None)),
    ("po.order", "Purchasing", "Order (send) a purchase order and download its PDF", (Y, None, None, OWN, None)),
    ("po.cancel", "Purchasing", "Cancel a purchase order that is not received yet", (Y, None, None, OWN, None)),
    ("po.receive_goods", "Purchasing", "Record the goods that arrived against a purchase order (no invoice, no batch)", (Y, None, Y, Y, None)),
    ("po.receive", "Purchasing", "Match the supplier's invoice to received goods, which creates the payable in a batch", (Y, None, Y, None, None)),
    ("po.ack_over_plan", "Purchasing", "Acknowledge 'orders over the cash plan'", (Y, None, None, None, None)),
    ("committed.view", "Purchasing", "Committed payables by week (totals by kind, no check numbers)", (Y, None, Y, Y, VIEW)),
    ("payables_due.view", "Payables", "See the payables calendar", (Y, None, Y, None, None)),
    ("payables.mark_paid", "Payables", "Mark payables paid on Daily payables: checks, bank-loan payments, card payments", (Y, None, Y, None, None)),
    ("invoice.enter", "Payables", "Enter invoices and credit memos; work with batches (order statements)", (Y, None, Y, None, None)),
    ("check.park", "Payables", "Park (prepare) checks", (Y, None, Y, None, None)),
    ("check.issue", "Payables", "Issue and print checks; handwritten and guarantee checks", (Y, None, None, None, None)),
    ("check.release", "Payables", "Mark post-dated checks as released to the supplier, or undo it", (Y, None, Y, None, None)),
    ("check.void", "Payables", "Cancel or void a check, cancel a batch, change a locked batch", (Y, None, None, None, None)),
    ("bank.use", "Payables", "Bank and cash: import a bank statement, reconcile", (Y, None, Y, None, None)),
    ("loans.use", "Payables", "Lenders and bank loans", (Y, None, Y, None, None)),
    ("cards.use", "Payables", "Credit cards: statements (SOA), cards and cardholders", (Y, None, Y, None, None)),
    ("creditors.edit", "Payables", "Add and change suppliers (terms, delivery days)", (Y, None, Y, None, None)),
    ("calendar.mark", "Payables", "Mark holidays, no-check days and notes on the payment calendar", (Y, None, Y, None, None)),
    ("search.use", "Records", "Search everything; the mini calendar of payments", (Y, None, Y, None, Y)),
    ("reports.use", "Records", "Reports (with Analytics inside), the Needs-attention list and downloads", (Y, None, Y, None, Y)),
    ("health.view", "Records", "Data health: see the findings", (Y, None, Y, None, None)),
    ("health.fix", "Records", "Data health: fix findings", (Y, None, None, None, None)),
    ("sales.use", "Sales", "Record sales, the day sheet, invoice booklets, customers", (Y, Y, None, None, VIEW)),
    ("inventory.use", "Sales", "Items and stock: add items, stock in, count adjustments", (Y, Y, None, None, VIEW)),
    ("controls.view", "System", "Roles and controls: who can do what, and what the system checks (read-only)", (Y, None, None, None, None)),
    ("review.use", "System", "The Review list: see what was flagged and tick it OK", (Y, None, None, None, None)),
    ("carveout.use", "System", "Business vs family carve-out tags", (Y, None, None, None, None)),
    ("users.manage", "System", "Manage users and roles; reset passwords", (Y, None, None, None, None)),
    ("audit.view", "System", "View the audit log", (Y, None, None, None, None)),
    ("settings.edit", "System", "Change settings, shortcuts and the forecast switch", (Y, None, None, None, None)),
    ("account.own", "System", "Change my own password; sign out", (Y, Y, Y, Y, Y)),
]
def _levels(action: str, row: tuple) -> dict:
    admin, sales, payables, purchasing, viewer = row
    return {"admin": admin, "owner": None if action in SYSTEM_ONLY else admin, "sales": sales, "payables": payables, "purchasing": purchasing,
            "releaser": RELEASER.get(action), "viewer": viewer}


ACTIONS: dict[str, dict] = {a: {"group": g, "label": text, "levels": _levels(a, lv)} for a, g, text, lv in _ROWS}

# the menu page that needs an action (a page is open to a role that has that action at any level except None; a page with writes also guards them in the engine)
PAGE_ACTION = {
    "home": "home.open", "price-lists": "price.view", "purchase-orders": "po.view", "payables-due": "payables_due.view", "calendar": "payables_due.view",
    "payment-run": "invoice.enter", "add-data": "invoice.enter", "order-statements": "invoice.enter", "checks": "payables.mark_paid", "release": "check.release", "controls": "controls.view", "bank-cash": "bank.use", "planner": "bank.use",
    "lenders": "loans.use", "bank-loans": "loans.use", "credit-cards": "cards.use", "creditors": "creditors.edit",
    "reports": "reports.use", "analytics": "reports.use", "review": "review.use", "data-health": "health.view",
    "settings": "settings.edit", "admin": "audit.view",
}
# a "view" level opens the page only where the page can show without forms that change AP records
VIEW_PAGES = {"home", "price-lists", "purchase-orders", "payables-due", "calendar"}


# the per-person switches, in the order of the work (owner, 2026-10-08); every business action is in exactly one group. Home, own password and the System admin's
# own actions (SYSTEM_ONLY) are never switches.
WORKFLOW: list[tuple[str, list[str]]] = [
    ("Buy", ["price.view", "price.write", "po.view", "po.create", "po.order", "po.cancel", "po.receive_goods", "committed.view"]),
    ("Pay suppliers", ["po.receive", "invoice.enter", "payables_due.view", "check.park", "check.issue", "check.void", "creditors.edit", "calendar.mark",
                       "payables.mark_paid"]),
    ("Release", ["check.release"]),
    ("Bank", ["bank.use"]),
    ("Loans and cards", ["loans.use", "cards.use"]),
    ("Records", ["search.use", "reports.use", "health.view"]),
    ("Review", ["review.use", "po.ack_over_plan", "controls.view"]),
    ("Sales", ["sales.use", "inventory.use"]),
    ("Old screens", ["more.use"]),
]
SWITCHES = {a for _, acts in WORKFLOW for a in acts}
_DENIED: dict = {}


def denied_for(email: str | None) -> frozenset:
    """The actions switched off for this person (none for the System admin, nobody signed in, or a database without the table). Read again whenever the
    database changes (the same stamp the settings use), so a change by the System admin applies at the person's next click."""
    if not email:
        return frozenset()
    from engines import common
    from engines.settings_store import _stamp
    key = (common.DB_PATH, email.strip().lower())
    stamp = _stamp(common.DB_PATH)
    hit = _DENIED.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with common.get_conn() as conn:
            rows = conn.execute("""SELECT r.action FROM user_restrictions r JOIN app_users u ON u.user_id = r.user_id
                                   WHERE lower(u.email) = ? AND u.role != 'admin'""", (key[1],)).fetchall()
        out = frozenset(r[0] for r in rows if r[0] in SWITCHES)
    except Exception:                                   # an older database without the table: nothing switched off
        out = frozenset()
    if len(_DENIED) > 200:
        _DENIED.clear()
    _DENIED[key] = (stamp, out)
    return out


def _switched_off(action: str) -> bool:
    return action in SWITCHES and action in denied_for(acting_email())


def level(role: str | None, action: str) -> str | None:
    return ACTIONS.get(action, {}).get("levels", {}).get(role or "")


def can(role: str | None, action: str, created_by: str | None = None, me: str | None = None) -> bool:
    """May this role do this? For 'own' the record must have been created by the same person (compare emails, ignoring case).
    False when the System admin switched the action off for the person acting now."""
    if _switched_off(action):
        return False
    lv = level(role, action)
    if lv == Y:
        return True
    if lv == OWN:
        return bool(created_by) and bool(me) and created_by.strip().lower() == me.strip().lower()
    return False


def require(action: str, created_by: str | None = None) -> tuple[bool, str]:
    """The engines' guard, for whoever is acting now. Nobody signed in (a script or a test) acts as the System admin."""
    role = acting_role() or "admin"
    if can(role, action, created_by, acting_email()):
        return True, ""
    what = ACTIONS.get(action, {}).get("label", action)
    if _switched_off(action):
        return False, f"The System admin has switched this off for you: {what}."
    if level(role, action) == OWN:
        return False, f"You can only change what you created yourself ({what})."
    if level(role, action) == VIEW:
        return False, f"Your role can view this but not change it ({what})."
    return False, f"Your role ({ROLE_LABEL.get(role, role)}) is not allowed to do this: {what}."


def allowed_pages(role: str | None, all_pages: set[str]) -> set[str]:
    """The menu pages this role may open. A page not named in PAGE_ACTION is the owner's."""
    if role not in ROLES:
        return set()
    out = set()
    for page in all_pages:
        action = PAGE_ACTION.get(page)
        if action is None:
            if role in ("admin", "owner"):
                out.add(page)
            continue
        if _switched_off(action):
            continue
        lv = level(role, action)
        if lv in (Y, OWN) or (lv == VIEW and page in VIEW_PAGES):
            out.add(page)
    return out


def filter_menu(menu: dict[str, list[str]], role: str | None) -> dict[str, list[str]]:
    """The menu sections cut down to the pages the role may open; a section left empty goes."""
    every = {pid for ids in menu.values() for pid in ids}
    ok = allowed_pages(role, every)
    out = {section: [pid for pid in ids if pid in ok] for section, ids in menu.items()}
    return {section: ids for section, ids in out.items() if ids}


def may_search(role: str | None) -> bool:
    """'Search everything' reaches checks, invoices, batches and loans."""
    return level(role, "search.use") == Y and not _switched_off("search.use")


def hide_check_details(role: str | None) -> bool:
    """Check numbers, who collected a check and the like are for roles that handle checks."""
    return level(role, "check.park") != Y or _switched_off("check.park")


# The person's page (owner, 2026-10-09): one line per thing a person does, in plain words, naming the pages it opens; a line may stand for several actions.
# (section, [(key, label, pages, [actions])]). Sales and inventory are left off until they exist; their switches are kept as they are.
SWITCH_LINES: list[tuple[str, list[tuple[str, str, str, list[str]]]]] = [
    ("Buy", [
        ("see_po", "See prices and purchase orders", "Price lists, Purchase orders", ["price.view", "po.view", "committed.view"]),
        ("write_po", "Add prices, create and send purchase orders", "Price lists, New order", ["price.write", "po.create", "po.order"]),
        ("receive", "Receive goods against an order", "Receive goods", ["po.receive_goods"]),
        ("cancel_po", "Cancel an order", "", ["po.cancel"])]),
    ("Pay suppliers", [
        ("invoices", "Match invoices, enter invoices and batches", "Match invoice, Order statements, Add data", ["po.receive", "invoice.enter"]),
        ("checks", "Park, write and number checks; release them", "a batch's Checks tab", ["check.park", "check.issue", "check.release"]),
        ("mark_paid", "Mark payables paid (checks, bank loans, card payments)", "Daily payables", ["payables.mark_paid"]),
        ("calendar", "See the calendar; mark holidays and no-check days", "Calendar", ["payables_due.view", "calendar.mark"]),
        ("void", "Void or replace a check, cancel or reopen a batch", "", ["check.void"]),
        ("suppliers", "Add and change suppliers", "Creditors", ["creditors.edit"])]),
    ("Bank, loans and cards", [
        ("bank", "Import bank statements, reconcile, booklets", "Bank and cash", ["bank.use"]),
        ("loans", "Lenders and bank loans", "Lenders, Bank loans", ["loans.use"]),
        ("cards", "Card statements (SOA) and cards", "Credit cards", ["cards.use"])]),
    ("Records", [
        ("reports", "Reports and analytics", "Reports, Analytics", ["reports.use"]),
        ("search", "Search everything", "", ["search.use"]),
        ("health", "Data health checks", "Data health", ["health.view"])]),
    ("Review", [
        ("review", "The Review list; roles and controls (read only)", "", ["review.use", "controls.view"]),
        ("over_plan", "Confirm an order over the cash plan", "", ["po.ack_over_plan"])]),
    ("Old screens", [
        ("more", "Sign in to the old screens (/more/)", "", ["more.use"])]),
]
_ALL_LINES = [k for _, lines in SWITCH_LINES for k, _, _, _ in lines]
# quick starting points; the person's page then changes only the exceptions
SWITCH_PRESETS = {
    "daily": ("Daily operations", set(_ALL_LINES) - {"void", "suppliers", "bank", "cards", "health", "review", "over_plan", "more"}),
    "purchasing": ("Purchasing only", {"see_po", "write_po", "receive", "cancel_po", "search"}),
    "view": ("View only", {"see_po", "calendar", "reports", "search"}),
    "all": ("Everything", set(_ALL_LINES)),
}


def switch_lines(denied: set) -> list[dict]:
    """The person's page: [{'section', 'lines': [{'key', 'label', 'pages', 'on'}]}] (on = none of its actions is switched off)."""
    return [{"section": s, "lines": [{"key": k, "label": label, "pages": pages, "on": not (set(acts) & set(denied))} for k, label, pages, acts in lines]}
            for s, lines in SWITCH_LINES]


def denied_from_lines(on_keys: set, denied_before: set) -> set:
    """What to switch off when the page is saved: every action of a line left off; the switches not on the page (sales, inventory) stay as they were."""
    shown = {a for _, lines in SWITCH_LINES for _, _, _, acts in lines for a in acts}
    off = {a for _, lines in SWITCH_LINES for k, _, _, acts in lines if k not in on_keys for a in acts}
    return off | (set(denied_before) & (SWITCHES - shown))


def switch_table(denied: set) -> list[dict]:
    """The switches for one person's page: [{'group', 'actions': [{'id', 'label', 'on'}]}] in WORKFLOW order (on = may do it)."""
    return [{"group": g, "actions": [{"id": a, "label": ACTIONS[a]["label"], "on": a not in denied} for a in acts if a in ACTIONS]} for g, acts in WORKFLOW]


def matrix_table() -> list[dict]:
    """The matrix as rows for the 'Who can do what' screen: group, operation, and the cell text for each role."""
    text = {Y: "Yes", VIEW: "View only", OWN: "Own only", None: "No"}
    return [{"group": a["group"], "operation": a["label"], **{ROLE_LABEL[r]: text[a["levels"][r]] for r in ROLES}} for a in ACTIONS.values()]
