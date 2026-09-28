"""Every Django connection gets the same SQL functions as AP's one door (engines/db_engine.open_db), so the ERP can read AP's views
(v_effective_checks, v_cc_due_alerts need manila_today()) and ERP audit rows record who did it the same way:
  manila_today() / manila_now()  -- one clock (engines/clock.py, Phase F1.3)
  acting_user()                  -- who did it (engines/acting_user.py, Phase F1.5)"""
from engines.acting_user import acting_user
from engines.clock import sql_now, sql_today


def add_sql_functions(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    raw = connection.connection
    raw.create_function("manila_today", 0, sql_today)
    raw.create_function("manila_now", 0, sql_now)
    raw.create_function("acting_user", 0, acting_user)
