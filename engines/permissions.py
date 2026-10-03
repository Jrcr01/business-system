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

  * can(role, action, created_by=None, me=None)  -- may this role do it (for "own": is it theirs)
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
    ("payables_due.view", "Payables", "Payables due: checks by week and supplier", (Y, None, Y, None, None)),
    ("invoice.enter", "Payables", "Enter invoices and credit memos; work with batches (order statements)", (Y, None, Y, None, None)),
    ("check.park", "Payables", "Park (prepare) checks", (Y, None, Y, None, None)),
    ("check.issue", "Payables", "Issue and print checks; handwritten and guarantee checks", (Y, None, None, None, None)),
    ("check.release", "Payables", "Mark post-dated checks as released to the supplier, or undo it", (Y, None, Y, None, None)),
    ("check.void", "Payables", "Cancel or void a check, cancel a batch, change a locked batch", (Y, None, None, None, None)),
    ("bank.use", "Payables", "Bank and cash: import a bank statement, reconcile", (Y, None, Y, None, None)),
    ("loans.use", "Payables", "Lenders, bank loans, credit cards", (Y, None, Y, None, None)),
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
    "home": "home.open", "price-lists": "price.view", "purchase-orders": "po.view", "payables-due": "payables_due.view",
    "payment-run": "invoice.enter", "add-data": "invoice.enter", "order-statements": "invoice.enter", "checks": "check.park", "release": "check.release", "controls": "controls.view", "bank-cash": "bank.use", "planner": "bank.use",
    "lenders": "loans.use", "bank-loans": "loans.use", "credit-cards": "loans.use", "creditors": "creditors.edit",
    "reports": "reports.use", "data-health": "health.view",
    "settings": "settings.edit", "admin": "audit.view",
}
# a "view" level opens the page only where the page can show without forms that change AP records
VIEW_PAGES = {"home", "price-lists", "purchase-orders", "payables-due"}


def level(role: str | None, action: str) -> str | None:
    return ACTIONS.get(action, {}).get("levels", {}).get(role or "")


def can(role: str | None, action: str, created_by: str | None = None, me: str | None = None) -> bool:
    """May this role do this? For 'own' the record must have been created by the same person (compare emails, ignoring case)."""
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
    return level(role, "search.use") == Y


def hide_check_details(role: str | None) -> bool:
    """Check numbers, who collected a check and the like are for roles that handle checks."""
    return level(role, "check.park") != Y


def matrix_table() -> list[dict]:
    """The matrix as rows for the 'Who can do what' screen: group, operation, and the cell text for each role."""
    text = {Y: "Yes", VIEW: "View only", OWN: "Own only", None: "No"}
    return [{"group": a["group"], "operation": a["label"], **{ROLE_LABEL[r]: text[a["levels"][r]] for r in ROLES}} for a in ACTIONS.values()]
