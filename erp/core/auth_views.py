"""Sign in / sign out / change password. Lockout (owner's choice): 5 wrong tries in a row for one email = locked for 15 minutes.
Every sign-in, wrong try, lock, sign-out and password change goes to the audit log."""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import SetPasswordForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from core import audit
from core.clock import now_aware
from core.models import LoginAttempt, User
from engines.acting_user import acting_as
from engines.clock import MANILA


def _ip(request):
    return request.META.get("REMOTE_ADDR")


def lock_until(email: str):
    """When this email may try again, or None when it is not locked. Counts the wrong tries since the last right one."""
    recent = list(LoginAttempt.objects.filter(email=email).order_by("-at", "-id")[: settings.LOGIN_MAX_FAILS])
    if len(recent) < settings.LOGIN_MAX_FAILS or any(a.succeeded for a in recent):
        return None
    until = recent[0].at + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
    return until if until > now_aware() else None


def tries_left(email: str) -> int:
    fails = 0
    for a in LoginAttempt.objects.filter(email=email).order_by("-at", "-id")[: settings.LOGIN_MAX_FAILS]:
        if a.succeeded:
            break
        fails += 1
    return max(settings.LOGIN_MAX_FAILS - fails, 0)


@login_not_required
@require_http_methods(["GET", "HEAD", "POST"])
def sign_in(request):
    if request.user.is_authenticated:
        return redirect("home")
    error, email = None, ""
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        locked = lock_until(email) if email else None
        if locked:
            error = f"Too many wrong tries. Try again after {locked.astimezone(MANILA).strftime('%I:%M %p').lstrip('0')}."
        else:
            user = authenticate(request, email=email, password=password) if email and password else None
            known = User.objects.filter(email=email).first()
            LoginAttempt.objects.create(email=email, succeeded=user is not None, ip=_ip(request))
            if user is not None:
                login(request, user)
                with acting_as(user.email):
                    audit.log("LOGIN", "core_user", user.pk, new=f"signed in from {_ip(request)}")
                return redirect(request.GET["next"] if _safe_next(request) else "home")
            left = tries_left(email)
            audit.log("LOGIN_FAILED", "core_user", known.pk if known else None,
                      new=f"{email}: {'wrong password' if known else 'no such user'} from {_ip(request)}")
            if left == 0:
                audit.log("LOGIN_LOCKED", "core_user", known.pk if known else None,
                          new=f"{email}: {settings.LOGIN_MAX_FAILS} wrong tries, locked {settings.LOGIN_LOCK_MINUTES} min")
                error = f"Too many wrong tries. Locked for {settings.LOGIN_LOCK_MINUTES} minutes."
            else:
                error = f"Wrong email or password. {left} {'try' if left == 1 else 'tries'} left, then locked for {settings.LOGIN_LOCK_MINUTES} minutes."
    return render(request, "core/login.html", {"error": error, "email": email})


def _safe_next(request) -> bool:
    from django.utils.http import url_has_allowed_host_and_scheme

    nxt = request.GET.get("next")
    return bool(nxt) and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())


@require_POST
def sign_out(request):
    audit.log("LOGOUT", "core_user", request.user.pk)
    logout(request)
    return redirect("login")


@require_http_methods(["GET", "HEAD", "POST"])
def password_change(request):
    form = SetPasswordForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.must_change_password = False
        user.save()
        update_session_auth_hash(request, user)        # stay signed in on this device
        audit.log("PASSWORD_CHANGED", "core_user", user.pk, new="by the user")
        return redirect("home")
    return render(request, "core/password_change.html", {"form": form, "forced": request.user.must_change_password})
