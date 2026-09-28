import secrets
from pathlib import Path

import django
from django.conf import settings
from django.contrib import messages
from django.db import connection, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core import audit
from core.forms import RoleForm, UserAddForm
from core.models import Role, User
from core.roles import role_required
from engines.clock import manila_now


@require_safe                      # GET and HEAD only: read-only
def home(request):
    """Home: the skeleton's figures (the ERP runs, on Manila time, reading the shared database) until real dashboards exist."""
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM creditors")
        creditors = cur.fetchone()[0]
        # v_effective_checks needs the SQL function manila_today() -- proves core/db.py is wired in
        cur.execute("SELECT COUNT(*) FROM v_effective_checks WHERE effective_status = 'Active'")
        checks_not_due = cur.fetchone()[0]
    return render(request, "core/home.html", {
        "audit_warnings": _audit_warnings() if request.user.is_owner else [],
        "django_version": django.get_version(),
        "db_name": Path(settings.DATABASES["default"]["NAME"]).name,
        "creditors": creditors,
        "checks_not_due": checks_not_due,
    })


def _audit_warnings() -> list[str]:
    """The audit log's lock and its last daily tamper check (core step C2) -- shown to the owner on Home when something is wrong."""
    from engines.audit_seal import _missing_triggers, last_result

    connection.ensure_connection()
    raw = connection.connection
    out = [f"The audit log lock {t} is missing." for t in _missing_triggers(raw)]
    res = last_result(raw)
    if res and not res.get("ok"):
        out += [f"Check of {res.get('at')}: {p}" for p in res.get("problems") or []]
    return out


# ---- users (owner only) ----------------------------------------------------------------------------------------------------

def _temporary_password() -> str:
    return secrets.token_urlsafe(9)        # 12 characters; shown to the owner once, the user must replace it at first sign-in


def _active_owners() -> int:
    return User.objects.filter(role=Role.OWNER, is_active=True).count()


@role_required(Role.OWNER)
@require_safe
def users(request):
    return render(request, "core/users.html", {"users": User.objects.all()})


@role_required(Role.OWNER)
@require_http_methods(["GET", "HEAD", "POST"])
def user_add(request):
    form = UserAddForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        temp = _temporary_password()
        with transaction.atomic():
            user = User.objects.create_user(form.cleaned_data["email"], form.cleaned_data["name"], form.cleaned_data["role"], temp)
            audit.log("USER_ADDED", "core_user", user.pk, new=f"{user.email} as {user.get_role_display()}")
        return render(request, "core/temp_password.html", {"u": user, "temp": temp, "added": True})
    return render(request, "core/user_add.html", {"form": form})


@role_required(Role.OWNER)
@require_safe
def user_detail(request, pk):
    user = get_object_or_404(User, pk=pk)
    return render(request, "core/user_detail.html", {"u": user, "role_form": RoleForm(initial={"role": user.role})})


def _refuse_if_last_owner(request, user, change: str):
    """Never leave the system without an active owner (nobody could manage users any more)."""
    if user.role == Role.OWNER and user.is_active and _active_owners() <= 1:
        messages.error(request, f"{user.name} is the only active owner, so you can't {change}. Make someone else owner first.")
        return True
    return False


@role_required(Role.OWNER)
@require_POST
def user_role(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = RoleForm(request.POST)
    if form.is_valid() and form.cleaned_data["role"] != user.role:
        new_role = form.cleaned_data["role"]
        with transaction.atomic():
            if new_role != Role.OWNER and _refuse_if_last_owner(request, user, "change the role"):
                return redirect("user_detail", pk=pk)
            old = user.get_role_display()
            user.role = new_role
            user.save(update_fields=["role"])
            audit.log("USER_ROLE_CHANGED", "core_user", user.pk, old=old, new=user.get_role_display())
        messages.success(request, f"{user.name} is now {user.get_role_display()}.")
    return redirect("user_detail", pk=pk)


@role_required(Role.OWNER)
@require_POST
def user_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    make_active = request.POST.get("active") == "1"
    if make_active != user.is_active:
        with transaction.atomic():
            if not make_active and _refuse_if_last_owner(request, user, "deactivate them"):
                return redirect("user_detail", pk=pk)
            user.is_active = make_active
            user.save(update_fields=["is_active"])
            audit.log("USER_REACTIVATED" if make_active else "USER_DEACTIVATED", "core_user", user.pk, new=user.email)
        if not make_active:     # sign them out everywhere now: Django refuses inactive users on their next request anyway
            messages.success(request, f"{user.name} is deactivated and can't sign in.")
        else:
            messages.success(request, f"{user.name} can sign in again.")
    return redirect("user_detail", pk=pk)


@role_required(Role.OWNER)
@require_POST
def user_reset_password(request, pk):
    user = get_object_or_404(User, pk=pk)
    temp = _temporary_password()
    with transaction.atomic():
        user.set_password(temp)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        audit.log("PASSWORD_RESET", "core_user", user.pk, new="temporary password set by the owner")
    return render(request, "core/temp_password.html", {"u": user, "temp": temp, "added": False})


# ---- audit log (owner only) ------------------------------------------------------------------------------------------------

@role_required(Role.OWNER)
@require_safe
def audit_log(request):
    who = (request.GET.get("who") or "").strip()
    action = (request.GET.get("action") or "").strip()
    sql = ("SELECT log_id, datetime(timestamp, '+8 hours'), user_email, user_action, entity_name, entity_id, old_values, new_values "
           "FROM audit_logs WHERE 1 = 1")
    params = []
    if who:
        sql += " AND user_email = %s"
        params.append(who)
    if action:
        sql += " AND user_action = %s"
        params.append(action)
    sql += " ORDER BY log_id DESC LIMIT 300"
    with connection.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("SELECT DISTINCT user_email FROM audit_logs WHERE user_email IS NOT NULL ORDER BY 1")
        people = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT user_action FROM audit_logs ORDER BY 1")
        actions = [r[0] for r in cur.fetchall()]
    return render(request, "core/audit_log.html", {"rows": rows, "people": people, "actions": actions, "who": who, "action": action})


def manila_header(request):
    """Template context: the Manila date for the top bar and the sidebar for this user's role."""
    from core.roles import nav_for

    now = manila_now()
    ctx = {"manila_date": f"{now:%b} {now.day}, {now.year}", "manila_time": now.strftime("%I:%M %p").lstrip("0")}
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        ctx["nav"] = nav_for(request.user)
    return ctx
