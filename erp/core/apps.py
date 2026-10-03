from django.apps import AppConfig


class CoreConfig(AppConfig):
    """core: what every module shares. Skeleton (2026-09-28): the database hook and the home page; users / roles, audit, numbering,
    parties, items and carve-out tags come in the core step."""
    name = "core"

    def ready(self):
        from django.db.backends.signals import connection_created

        from core.db import add_sql_functions

        connection_created.connect(add_sql_functions, dispatch_uid="core.add_sql_functions")
        from django.conf import settings

        from core.db import point_engines_at
        point_engines_at(settings.DATABASES["default"]["NAME"])
