from pathlib import Path

import django
from django.conf import settings
from django.db import connection
from django.shortcuts import render
from django.views.decorators.http import require_safe

from engines.clock import manila_now


@require_safe                      # GET and HEAD only: read-only
def home(request):
    """The skeleton's "it works" page: the ERP runs, on Manila time, reading the shared database. Read-only."""
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM creditors")
        creditors = cur.fetchone()[0]
        # v_effective_checks needs the SQL function manila_today() -- proves core/db.py is wired in
        cur.execute("SELECT COUNT(*) FROM v_effective_checks WHERE effective_status = 'Active'")
        checks_not_due = cur.fetchone()[0]
    now = manila_now()
    return render(request, "core/home.html", {
        "manila_date": f"{now:%b} {now.day}, {now.year}",
        "manila_time": now.strftime("%I:%M %p").lstrip("0"),
        "django_version": django.get_version(),
        "db_name": Path(settings.DATABASES["default"]["NAME"]).name,
        "creditors": creditors,
        "checks_not_due": checks_not_due,
    })
