import sys

from django.apps import AppConfig
from django.conf import settings

_KEEPER = None          # one connection held open for the life of the server process (see CoreConfig.ready)


def point_engines_at(path: str) -> list[str]:
    """Every engines module already imported reads the same database file as the web site (a module imported later reads AP_DB_PATH, which settings.py has set).
    Returns the module names that were changed."""
    changed = []
    for name, mod in list(sys.modules.items()):
        if mod is not None and name.startswith("engines.") and isinstance(getattr(mod, "DB_PATH", None), str) and mod.DB_PATH != path:
            mod.DB_PATH = path
            changed.append(name)
    return changed


def keep_database_open(path: str):
    """Hold one connection open for as long as the server runs (load test, 2026-10-03). The business rules open and close a connection for every question; when the LAST
    connection to the file closes, SQLite tidies its journal (a checkpoint) under a lock, and everyone opening one at that moment waits: with 20 people at once that made
    some pages wait 10-50 seconds. With this connection always open that never happens; the journal is tidied as it fills (an automatic checkpoint that does not block
    readers)."""
    global _KEEPER
    if _KEEPER is None:
        from engines.db_engine import open_db
        _KEEPER = open_db(path)
    return _KEEPER


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Slick's core: sign-in, roles, top bar, Home"

    def ready(self):
        point_engines_at(settings.DB_PATH)
        keep_database_open(settings.DB_PATH)
