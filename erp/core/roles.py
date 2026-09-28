"""Who may open what. Every page declares its roles; a hidden menu item is not a permission."""
from functools import wraps

from django.shortcuts import render

from core.models import Role

# The sidebar: (section, label, url name, icon, roles). Pages not built yet are shown as "Coming soon".
NAV = [
    (None, "Home", "home", "home", {Role.OWNER, Role.SALES, Role.VIEWER}),
    ("Sales and AR", "Parties", "parties", "users-group", {Role.OWNER, Role.SALES}),
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


def nav_for(user) -> list[dict]:
    items, last_section = [], object()
    for section, label, url_name, icon, roles in NAV:
        if user.role not in roles:
            continue
        items.append({"section": section if section != last_section else None, "label": label, "url_name": url_name, "icon": icon})
        last_section = section
    return items
