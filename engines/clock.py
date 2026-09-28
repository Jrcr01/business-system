"""One clock (Phase F1.3, 2026-09-28): the business date and time are always MANILA's, whatever the computer's clock says.

Cloud servers run on UTC. Without this, "today" would be yesterday from 00:00 to 08:00 Manila time on a server, and the due-today rule
("a check dated today is still owed"), the aging buckets and the planner would be wrong for those hours. So:
  * Python:  manila_today() / manila_now() -- never date.today() / datetime.now().
  * SQL:     manila_today() / manila_now() are SQL functions registered on every connection by the one door (db_engine.open_db), so
             queries and the views use the same clock -- never DATE('now', 'localtime'). Times STORED by CURRENT_TIMESTAMP are UTC;
             show them with datetime(x, '+8 hours') (Manila has no daylight saving; on this laptop that equals 'localtime').
  * Tests:   set_fake_now(datetime) pins the clock (both Python and SQL); set_fake_now(None) releases it.
Needs no other module (imported by db_engine, the lowest layer)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    MANILA = ZoneInfo("Asia/Manila")
except Exception:                      # no time-zone database: Manila is UTC+8 all year (no daylight saving)
    MANILA = timezone(timedelta(hours=8), "Asia/Manila")

MANILA_OFFSET_SQL = "+8 hours"         # for datetime(<UTC column>, '+8 hours') in SQL

_fake_now: datetime | None = None


def set_fake_now(when: datetime | None) -> None:
    """Tests only: pin the clock to `when` (a naive datetime = Manila time), or None to use the real clock again."""
    global _fake_now
    _fake_now = when


def manila_now() -> datetime:
    """Now in Manila, as a naive datetime (the same shape as datetime.now() on this laptop)."""
    if _fake_now is not None:
        return _fake_now
    return datetime.now(MANILA).replace(tzinfo=None)


def manila_today() -> date:
    """Today's business date in Manila."""
    return manila_now().date()


def sql_today() -> str:
    """For the SQL function manila_today(): 'YYYY-MM-DD'."""
    return manila_today().isoformat()


def sql_now() -> str:
    """For the SQL function manila_now(): 'YYYY-MM-DD HH:MM:SS'."""
    return manila_now().strftime("%Y-%m-%d %H:%M:%S")
