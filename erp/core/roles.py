"""Who may open what. Every page declares its roles; a hidden menu item is not a permission."""
from functools import wraps

from django.shortcuts import render

from core.models import Role

# The sidebar: (section, label, url name, icon, roles). Pages not built yet are shown as "Coming soon".
NAV = [
    (None, "Home", "home", "home", {Role.OWNER, Role.SALES, Role.PAYABLES, Role.PURCHASING, Role.VIEWER}),
    ("Purchasing", "Price lists", "pl_home", "tag", {Role.OWNER, Role.PURCHASING, Role.PAYABLES, Role.VIEWER}),
    ("Purchasing", "Purchase orders", "po_list", "shopping-cart", {Role.OWNER, Role.PURCHASING, Role.PAYABLES, Role.VIEWER}),
    ("Purchasing", "Receive an invoice", "po_receive", "truck-delivery", {Role.OWNER, Role.PAYABLES}),
    ("Sales and AR", "Sales", "sales_list", "receipt", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "New sale", "sales_new", "plus", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "Day sheet", "sales_daysheet", "file-spreadsheet", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "Invoice booklets", "sales_booklets", "notebook", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "Parties", "parties", "users-group", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "Items and stock", "inv_items", "package", {Role.OWNER, Role.SALES}),
    ("Sales and AR", "Stock in", "inv_stock_ins", "truck-delivery", {Role.OWNER, Role.SALES}),
    ("Carve-out", "Business vs family", "carveout_summary", "chart-pie", {Role.OWNER}),
    ("Carve-out", "Tag records", "tag_records", "tags", {Role.OWNER}),
    ("Carve-out", "Tags", "tags", "tag", {Role.OWNER}),
    ("System", "Users", "users", "users", {Role.OWNER}),
    ("System", "Audit log", "audit_log", "list-search", {Role.OWNER}),
]


def role_required(*roles):
    """Allow only these roles; anyone else gets a friendly "not for your role" page (403)."""
    def deco(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                return render(request, "core/forbidden.html", status=403)
            return view(request, *args, **kwargs)
        wrapped.allowed_roles = set(roles)
        return wrapped
    return deco


def nav_for(user, path: str = "") -> list[dict]:
    """The sidebar for this role; the ONE item whose address is the closest match to the page is marked "on"."""
    from django.urls import NoReverseMatch, reverse

    items, last_section = [], object()
    for section, label, url_name, icon, roles in NAV:
        if user.role not in roles:
            continue
        try:
            href = reverse(url_name) if url_name else ""
        except NoReverseMatch:
            href = ""
        items.append({"section": section if section != last_section else None, "label": label, "url_name": url_name, "icon": icon,
                      "href": href, "on": False})
        last_section = section
    matches = [i for i in items if i["href"] and (path == i["href"] or (i["href"] != "/" and path.startswith(i["href"])))]
    if matches:
        max(matches, key=lambda i: len(i["href"]))["on"] = True
    return items
