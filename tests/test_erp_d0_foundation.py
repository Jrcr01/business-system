"""Django-only move, step D0 (2026-10-02): the Django ERP can safely use every business rule in engines/.
  * the rules read the SAME database file as Django (never the live default);
  * the signed-in person's role reaches the rules, so engines/permissions.require() refuses what the role may not do;
  * nobody signed in is 'anonymous' and may do nothing (no role would have meant the owner);
  * Django's roles and the access matrix are one list (Owner, Sales, Payables, Purchasing, Viewer);
  * engines/ no longer imports Streamlit."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")


def test_every_engine_reads_the_same_database_as_django(erp):
    import importlib
    from django.conf import settings

    from core.db import point_engines_at

    point_engines_at(settings.DATABASES["default"]["NAME"])
    for name in ("engines.common", "engines.cash_insights_service", "engines.payment_run_service", "engines.settings_store", "engines.db_engine"):
        mod = importlib.import_module(name)
        assert Path(mod.DB_PATH).resolve() == Path(settings.DATABASES["default"]["NAME"]).resolve(), name
    assert Path(__import__("os").environ["AP_DB_PATH"]).resolve() == Path(settings.DATABASES["default"]["NAME"]).resolve()


def test_the_erp_database_wins_over_an_inherited_ap_setting(erp, monkeypatch, tmp_path):
    import os
    import runpy

    monkeypatch.setenv("ERP_DB_PATH", str(tmp_path / "a_TEST.db"))
    monkeypatch.setenv("AP_DB_PATH", str(tmp_path / "b.db"))                      # e.g. left over from starting the Streamlit app in the same window
    runpy.run_path(str(Path(__file__).resolve().parent.parent / "erp" / "config" / "settings.py"))
    assert Path(os.environ["AP_DB_PATH"]).name == "a_TEST.db"


def test_django_roles_and_the_access_matrix_are_one_list(erp):
    from core.models import Role
    from engines.permissions import ROLES

    assert set(Role.values) == set(ROLES)


def test_the_signed_in_role_reaches_the_business_rules(erp):
    from django.http import HttpResponse
    from django.test import RequestFactory

    from core.middleware import ANONYMOUS, ActingUserMiddleware
    from engines.acting_user import acting_email, acting_role
    from engines.permissions import require

    seen = {}

    def view(request):
        seen.update(email=acting_email(), role=acting_role(), may_receive=require("po.receive")[0], may_price=require("price.write")[0])
        return HttpResponse("ok")

    class U:                                    # a signed-in Purchasing user, as Django's auth would give it
        is_authenticated, email, role = True, "brother@example.com", "purchasing"

    req = RequestFactory().get("/")
    req.user = U()
    ActingUserMiddleware(view)(req)
    assert seen == {"email": "brother@example.com", "role": "purchasing", "may_receive": False, "may_price": True}

    class Nobody:
        is_authenticated = False

    req.user = Nobody()
    ActingUserMiddleware(view)(req)
    assert seen["email"] == ANONYMOUS and seen["may_price"] is False and seen["may_receive"] is False    # nobody signed in is not the owner


def test_engines_do_not_import_a_screen_framework():
    root = Path(__file__).resolve().parent.parent / "engines"
    offenders = [p.name for p in root.glob("*.py") if "import streamlit" in p.read_text(encoding="utf-8")]
    assert offenders == []
