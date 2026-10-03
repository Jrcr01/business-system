"""Every Django connection gets the same SQL functions as AP's one door (engines/db_engine.open_db), so the ERP can read AP's views
(v_effective_checks, v_cc_due_alerts need manila_today()) and ERP audit rows record who did it the same way:
  manila_today() / manila_now()  -- one clock (engines/clock.py, Phase F1.3)
  acting_user()                  -- who did it (engines/acting_user.py, Phase F1.5)"""
from engines.acting_user import acting_user
from engines.clock import sql_now, sql_today


def point_engines_at(path: str) -> list[str]:
    """Every engines module already imported reads the same database file as Django (a module imported later reads AP_DB_PATH, which
    settings.py has set). Returns the module names that were changed."""
    import sys
    changed = []
    for name, mod in list(sys.modules.items()):
        if mod is not None and name.startswith("engines.") and isinstance(getattr(mod, "DB_PATH", None), str) and mod.DB_PATH != path:
            mod.DB_PATH = path
            changed.append(name)
    return changed


def add_sql_functions(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    raw = connection.connection
    raw.create_function("manila_today", 0, sql_today)
    raw.create_function("manila_now", 0, sql_now)
    raw.create_function("acting_user", 0, acting_user)
