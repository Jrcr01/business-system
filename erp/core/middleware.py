from django.shortcuts import redirect
from django.urls import reverse

from engines.acting_user import acting_as

ANONYMOUS = "anonymous"


class ActingUserMiddleware:
    """Everything saved during a request is recorded as done by the signed-in person (audit_logs.user_email). A request with nobody
    signed in (the sign-in page) is 'anonymous' -- never the owner by default."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        email = user.email if user is not None and user.is_authenticated else ANONYMOUS
        with acting_as(email):
            return self.get_response(request)


class MustChangePasswordMiddleware:
    """A user with a temporary password (new, or reset by the owner) can only open the change-password page and sign out."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password:
            allowed = {reverse("password_change"), reverse("logout")}
            if request.path not in allowed:
                return redirect("password_change")
        return self.get_response(request)
