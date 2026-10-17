"""Stage D1 (Masters, docs/mockups/d1_masters.html, approved 2026-10-03): Creditors (list + one edit form each), Settings (numbers and the Home notice),
Users, and Who can do what. Every rule is in engines/; these views only draw the forms and pass on what was typed."""
from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from core.menu import pages_for

TERMS = ["", "COD", "30", "60", "90", "120", "140", "150"]          # the same choices as Streamlit (views/shared.py SUPPLIER_TERMS_OPTIONS)
TYPES = [("SUPPLIER", "Supplier"), ("LENDER", "Lender"), ("LESSOR", "Lessor (rent)"), ("BANK_LOAN", "Bank loan")]


def page_needed(page_id: str, action: str | None = None, phone_actions: tuple = ()):
    """The page is open only to roles allowed it (and, when given, the action): others get the 'not for your role' page.
    A page the person may only see (owners, 2026-10-09: view access to every module) is read-only: its forms are hidden and any save is refused here,
    before the view runs -- many engines rely on the page check alone."""
    def deco(view):
        @wraps(view)
        def wrapper(request, *a, **k):
            from core.menu import PAGES, doable_pages
            from engines.permissions import can
            role = request.slicks_user["role"]
            if role == "scanner" and not request.path.startswith(("/stock/labels/", "/stock/items.json", "/stock/learn/")):
                return redirect("/phone/")                      # the staff phone's account works on the phone pages only (owner, 2026-10-12)
            if page_id not in pages_for(role) or (action and not can(role, action)):
                return render(request, "core/not_allowed.html", {"page": PAGES[page_id]}, status=403)
            request.view_only = page_id not in doable_pages(role)
            from core.phone_rules import LAPTOP_WHY, PHONE_INSTEAD, kind
            from core.views import _on_a_phone
            rule = kind(page_id) if _on_a_phone(request) else None      # a phone or an iPad (owner, 2026-10-17)
            if rule == "laptop":
                return render(request, "core/laptop_only.html", {"page": PAGES[page_id], "why": LAPTOP_WHY.get(page_id, "")})
            if rule == "look" and phone_actions:                   # a look-only page that still takes these steps on a phone (2026-10-17: invoices)
                request.phone_partial, rule = True, None
                if request.method == "POST" and request.POST.get("action", "") not in phone_actions:
                    messages.error(request, "On a phone this is done on the laptop.")
                    return redirect(request.get_full_path())
            request.phone_look = rule == "look"
            if request.phone_look:
                request.view_only, request.phone_instead = True, PHONE_INSTEAD.get(page_id)
                if request.method == "POST":
                    messages.error(request, f"On a phone, {PAGES[page_id].title} is for looking. Do this on the laptop.")
                    return redirect(request.get_full_path())
            if request.view_only and request.method == "POST":
                messages.error(request, f"{PAGES[page_id].title} is view only for you. Ask the System admin to switch it on if you need to change things here.")
                return redirect(request.get_full_path())
            return view(request, *a, **k)
        return wrapper
    return deco


# --- Creditors --------------------------------------------------------------------------------------------------------------------------------------

@page_needed("creditors")
def creditors(request):
    from engines.creditor_rules import TYPE_LABEL, list_creditors
    search, ctype, show_off = request.GET.get("q", ""), request.GET.get("type", ""), request.GET.get("off") == "1"
    rows = list_creditors(search, ctype, show_off)
    for r in rows:
        r["type_label"] = TYPE_LABEL.get(r["type"], r["type"])
    return render(request, "masters/creditors.html", {"page_id": "creditors", "rows": rows, "q": search, "type": ctype, "off": show_off, "types": TYPES})


def _form_values(post) -> dict:
    return {k: post.get(k, "").strip() for k in ("name", "type", "tin", "contact_person", "contact_number", "email", "terms", "lead_days")}


@page_needed("creditors")
def creditor_new(request):
    """Add a creditor. A name already on file asks 'Did you mean …?' first (engines/duplicate_service.creditor_warnings), as in Streamlit."""
    from engines.creditor_rules import add_creditor, set_lead_days
    from engines.duplicate_service import creditor_warnings
    v = {"name": "", "type": "SUPPLIER", "tin": "", "contact_person": "", "contact_number": "", "email": "", "terms": "120", "lead_days": ""}
    warnings = []
    if request.method == "POST":
        v = _form_values(request.POST)
        if not v["name"]:
            messages.error(request, "Enter the name.")
        elif v["type"] not in dict(TYPES):
            messages.error(request, "Pick a type.")
        elif v["lead_days"] and (not v["lead_days"].isdigit() or int(v["lead_days"]) > 365):
            messages.error(request, "Usual delivery days: a whole number from 0 to 365, or leave it empty.")
        else:
            warnings = [] if request.POST.get("confirmed") == "1" else creditor_warnings(v["name"], v["type"])
            if not warnings:
                new_id = add_creditor(v["name"], v["type"], v["tin"], v["contact_person"], v["contact_number"], v["email"], v["terms"] if v["type"] == "SUPPLIER" else "")
                if v["lead_days"] and v["type"] == "SUPPLIER":
                    set_lead_days(new_id, int(v["lead_days"]))
                from engines.creditor_rules import get_creditor
                messages.success(request, f"Added {get_creditor(new_id)['creditor_name']}.")      # the name as saved (proper case, full bank name)
                return redirect(f"/creditors/{new_id}/")
    return render(request, "masters/creditor_form.html", {"page_id": "creditors", "v": v, "new": True, "types": TYPES, "terms": TERMS, "warnings": warnings})


@page_needed("creditors")
def creditor_edit(request, creditor_id: int):
    from engines.creditor_rules import delete_creditor, get_creditor, last_change, save_creditor, set_creditor_active
    c = get_creditor(creditor_id)
    if c is None or c["creditor_type"] == "CREDIT_CARD":
        raise Http404
    changed_by = typed = None
    if request.method == "POST":
        what, stamp = request.POST.get("action", "save"), request.POST.get("stamp", "")
        if what == "save":
            v = _form_values(request.POST)
            ok, msg = save_creditor(creditor_id, stamp, v["name"], v["type"], v["tin"], v["contact_person"], v["contact_number"], v["email"], v["terms"], v["lead_days"])
        elif what in ("off", "on"):
            ok, msg = set_creditor_active(creditor_id, stamp, what == "on")
        elif what == "delete":
            ok, msg = delete_creditor(creditor_id, stamp)
            if ok:
                messages.success(request, msg)
                return redirect("/creditors/")
        else:
            ok, msg = False, "Unknown action."
        if ok:
            messages.success(request, msg)
            return redirect(f"/creditors/{creditor_id}/")
        if msg == "CHANGED":
            changed_by = last_change(creditor_id) or ("someone", "")
            c = get_creditor(creditor_id)                                       # their version, so the change can be made again on top of it
            if c is None:
                messages.error(request, "Someone else deleted this creditor.")
                return redirect("/creditors/")
        else:
            messages.error(request, msg)
            if what == "save":
                typed = _form_values(request.POST)                              # keep what was typed, to fix and save again
    v = typed or {"name": c["creditor_name"], "type": c["type"], "tin": c["tin"] or "", "contact_person": c["contact_person"] or "",
                  "contact_number": c["contact_number"] or "", "email": c["email"] or "", "terms": c["terms"] or "",
                  "lead_days": "" if c["lead_days"] in (None, "") else c["lead_days"]}
    history = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in c["history"].items())
    return render(request, "masters/creditor_form.html", {"page_id": "creditors", "c": c, "v": v, "new": False, "types": TYPES, "terms": TERMS,
                                                          "history": history, "changed_by": changed_by})


# --- Settings -----------------------------------------------------------------------------------------------------------------------------------------

def _settings_tabs(role: str) -> list[tuple[str, str]]:
    from engines.permissions import can
    tabs = [("/settings/", "Numbers")]
    if can(role, "users.manage"):
        tabs += [("/settings/users/", "Users"), ("/settings/notifications/", "Notifications"), ("/settings/audit/", "Audit log"), ("/settings/backup/", "Backup")]          # "Who can do what" dropped (owner, 2026-10-09): each person's page says what they may do
    return tabs


@page_needed("settings")
def settings_numbers(request):
    from engines import settings_registry as sr
    from engines.settings_store import get_setting
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "notice":
            ok, msg = sr.save_notice(request.POST.get("notice", ""), request.POST.get("until", ""))
        elif what in ("save", "reset"):
            key = request.POST.get("key", "")
            raw = request.POST.get("value", "").replace(",", "").replace("₱", "").strip()
            ok, msg = sr.save(key, raw) if what == "save" else sr.reset(key)
        else:
            ok, msg = False, "Unknown action."
        (messages.success if ok else messages.error)(request, msg)
        return redirect("/settings/" + (f"#{request.POST.get('key')}" if request.POST.get("key") else ""))
    from engines.features import REVENUE_SETTINGS_GROUPS, revenue_on
    groups = []
    for g in sr.GROUPS:
        if g in REVENUE_SETTINGS_GROUPS and not revenue_on():
            continue
        items = []
        for m in [x for x in sr.registry() if x["group"] == g]:
            cur, d = sr.value(m["key"]), m["decimals"]
            fmt = (lambda x, d=d: f"{x:,.{d}f}" if d else f"{int(round(x)):,}")
            items.append({**m, "shown": fmt(cur), "default_shown": fmt(float(m["default"])), "is_default": abs(cur - m["default"]) < 1e-9})
        groups.append({"title": g, "items": items})
    return render(request, "masters/settings_numbers.html", {"page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/",
                                                             "groups": groups, "notice": get_setting(sr.NOTICE_KEY, "") or "",
                                                             "notice_until": get_setting(sr.NOTICE_UNTIL_KEY, "") or ""})


def _admin_only(view):
    @wraps(view)
    def wrapper(request, *a, **k):
        from engines.permissions import can
        if not can(request.slicks_user["role"], "users.manage"):
            from core.menu import PAGES
            return render(request, "core/not_allowed.html", {"page": PAGES["settings"]}, status=403)
        return view(request, *a, **k)
    return wrapper


@page_needed("settings")
@_admin_only
def settings_users(request):
    """People who can sign in. A new person gets a temporary password, shown on this page once (never put in a link or a cookie)."""
    from engines import auth_service as au
    from engines.permissions import ROLE_LABEL, ROLES
    shown = None
    if request.method == "POST":
        ok, msg, uid, temp = au.create_user(request.POST.get("email", ""), request.POST.get("name", ""), request.POST.get("role", ""), request.POST.get("username", ""))
        if ok:
            shown = (au.get_user(uid)["email"], temp)
            messages.success(request, msg)
        else:
            messages.error(request, msg)
    users = au.list_users().to_dict("records")
    from engines.phone_approval import push_phone
    for u in users:
        u["role_label"] = ROLE_LABEL.get(u["role"], u["role"])
        u["phone_ready"] = u["role"] != "scanner" and push_phone(int(u["user_id"])) is not None      # owner, 2026-10-15: who still has to set up their phone
    return render(request, "masters/settings_users.html", {"page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/users/",
                                                           "users": users, "roles": [(r, ROLE_LABEL[r]) for r in ROLES], "shown": shown,
                                                           "me_id": int(request.slicks_user["user_id"]),
                                                           "one_admin": sum(1 for u in users if u["role"] == "admin" and u["is_active"]) == 1})


@page_needed("settings")
@_admin_only
def settings_user(request, user_id: int):
    """One person: a new temporary password, switch off / on, change the role. Nobody can switch themselves off or change their own role."""
    from engines import auth_service as au
    from engines.permissions import ROLE_LABEL, ROLES
    u = au.get_user(user_id)
    if u is None:
        raise Http404
    is_me = int(user_id) == int(request.slicks_user["user_id"])
    shown = None
    if request.method == "POST":
        what = request.POST.get("action")
        if what == "reset":
            ok, msg, temp = au.reset_password(user_id)
            shown = (u["email"], temp) if ok else None
        elif what in ("off", "on") and not is_me:
            ok, msg = au.set_active(user_id, what == "on")
        elif what == "role" and not is_me:
            ok, msg = au.set_role(user_id, request.POST.get("role", ""))
        elif what == "access" and u["role"] != "admin":                # what they see and what they do, per page (owner, 2026-10-09)
            from engines.features import off_pages
            from engines.permissions import ACCESS_PAGES, LEVELS, access_from_choices
            off = off_pages()                                          # pages not on this site keep whatever they were set to
            levels = {p: request.POST.get(f"lv_{p}") for p in ACCESS_PAGES if request.POST.get(f"lv_{p}") in LEVELS}
            hidden, denied = access_from_choices(levels, set(request.POST.getlist("x")), au.get_restrictions(user_id), skip=off)
            hidden |= set(au.get_hidden_pages(user_id)) & off
            ok, msg = au.set_access(user_id, hidden, denied)
        elif what == "email":
            ok, msg = au.set_email(user_id, request.POST.get("email", ""))
        elif what == "username":
            ok, msg = au.set_username(user_id, request.POST.get("username", ""))
        elif what == "reset_code":
            ok, msg = au.reset_totp(user_id)
        elif what == "allow_code":                                     # an authenticator is set up only while the System admin allows it (owner, 2026-10-11)
            ok, msg = au.allow_totp_setup(user_id)
        elif what == "fp_remove":                                      # a lost or sold phone (owner, 2026-10-13)
            from engines import fingerprint_signin as fp
            ok, msg = fp.remove(int(request.POST.get("key_id") or 0)) if any(
                d["key_id"] == int(request.POST.get("key_id") or 0) for d in fp.devices(user_id)) else (False, "No such device.")
        elif what == "clear_phone":                                    # a lost phone, or one that no longer gets the sign-in notification (owner, 2026-10-15)
            from engines.phone_approval import clear_phone
            ok, msg = clear_phone(user_id, request.slicks_user["email"])
        elif what == "signout_all":
            ok, msg = au.sign_out_everywhere(user_id)
        else:
            ok, msg = False, "You can't do that to your own account."
        (messages.success if ok else messages.error)(request, msg)
        if not shown:
            return redirect(f"/settings/users/{user_id}/")
        u = au.get_user(user_id)
    return render(request, "masters/settings_user.html", {"page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/users/",
                                                          "u": u, "role_label": ROLE_LABEL.get(u["role"], u["role"]), "roles": [(r, ROLE_LABEL[r]) for r in ROLES],
                                                          "is_me": is_me, "shown": shown, "code_since": (au.totp_enabled_at(user_id) or "")[:10],
                                                          "setup_until": au.shown_time(au.totp_setup_until(user_id)), "sign_ins": au.recent_sign_ins(user_id),
                                                          "fp_devices": __import__("engines.fingerprint_signin", fromlist=["devices"]).devices(user_id),
                                                          "push_phone": __import__("engines.phone_approval", fromlist=["push_phone"]).push_phone(user_id),
                                                          "push_on": __import__("engines.phone_approval", fromlist=["push_on"]).push_on(),
                                                          **_switch_ctx(request, u, user_id)})


def _switch_ctx(request, u: dict, user_id: int) -> dict:
    """What the person sees and does (owner, 2026-10-09): a row per page -- Hidden / See / See and do -- with its extra actions; starting points; another
    person's settings to copy (not saved until Save)."""
    if u["role"] == "admin":
        return {"access": None}
    import json

    from engines import auth_service as au
    from engines.features import off_pages
    from engines.permissions import ACCESS_PRESETS, row_levels
    others = [x for x in au.list_users().to_dict("records") if x["role"] != "admin" and int(x["user_id"]) != int(user_id)]
    copy = int(request.GET.get("copy")) if (request.GET.get("copy") or "").isdigit() else None
    source = next((x for x in others if int(x["user_id"]) == copy), None)
    who = source["user_id"] if source else user_id
    return {"access": row_levels(au.get_restrictions(who), au.get_hidden_pages(who), skip=off_pages()), "others": others, "copied_from": source,
            "presets": [(k, label) for k, (label, _) in ACCESS_PRESETS.items()], "presets_json": json.dumps({k: v for k, (_, v) in ACCESS_PRESETS.items()})}




@page_needed("settings")
@_admin_only
def settings_notifications(request):
    """Settings > Notifications (owner, 2026-10-13): who receives the credit card reminders -- none, their own cards, or all cards -- and which
    cardholder each person is. The switches and the two hours are numbers on the first tab."""
    from engines import card_reminders as cr, settings_registry as sr
    from engines.common import get_conn
    from engines import mail_out, morning_email
    if request.method == "POST":
        p = request.POST
        what = p.get("action", "")
        if what == "mail_setup":                                       # owner, 2026-10-14: the morning email; the key is typed here by the System admin
            ok, msg = mail_out.save_config(p.get("provider", ""), p.get("sender", ""), p.get("key", ""))
        elif what == "mail_pref":
            ok, msg = morning_email.set_pref(p.get("email", ""), p.get("receives") == "1")
        elif what == "mail_test":
            from engines.clock import manila_today
            me = request.slicks_user
            ok, msg = morning_email.send_to(me["email"], me["name"], manila_today(), f"{request.scheme}://{request.get_host()}")
            msg = f"Today's email was sent to {me['email']}. It should arrive within a minute; look in Spam too the first time." if ok else msg
        else:
            ok, msg = cr.set_pref(p.get("email", ""), p.get("scope", ""), p.get("cardholder_id", ""))
        (messages.success if ok else messages.error)(request, msg)
        return redirect("/settings/notifications/")
    with get_conn() as conn:
        holders = conn.execute("SELECT cardholder_id, name FROM cardholders WHERE is_active = 1 ORDER BY name").fetchall()
    kinds = [(sr._meta(k)["label"].split(" (")[0].split(": ", 1)[-1].capitalize(), sr.int_value(k) == 1, k) for k in ("notify_card_statement", "notify_card_due", "notify_card_fee")]
    return render(request, "masters/settings_notifications.html", {"page_id": "settings", "tabs": _settings_tabs(request.slicks_user["role"]), "tab": "/settings/notifications/",
                                                                   "people": cr.people(), "holders": holders, "missing": cr.missing(), "kinds": kinds,
                                                                   "mail": mail_out.config(), "mail_on": morning_email.on(), "mail_people": morning_email.people(),
                                                                   "mail_hour": sr.int_value("email_morning_hour"), "providers": list(mail_out.PROVIDERS.items()),
                                                                   "hour": sr.int_value("notify_hour"), "second": sr.int_value("notify_second_hour")})
