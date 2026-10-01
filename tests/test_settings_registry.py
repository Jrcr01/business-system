"""Settings page engine (owner, 2026-10-01; plan D1 / C / D6): every adjustable number has a default equal to today's behaviour, saves are validated and
audited, reset goes back to the default, and the keyboard shortcuts are editable with conflict checks."""
import json
import sqlite3

from conftest import fetchone
from engines import settings_registry as sr


def _sql(db, sql, params=()):
    with sqlite3.connect(db) as conn:
        conn.execute(sql, params)


def _rows(db, sql):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def test_every_default_is_inside_its_own_limits_and_keys_are_unique():
    keys = [m["key"] for m in sr.registry()]
    assert len(keys) == len(set(keys))
    for m in sr.registry():
        assert m["min"] <= m["default"] <= m["max"], m["key"]


def test_value_is_the_default_until_saved_and_a_bad_stored_value_falls_back(db):
    assert sr.value("stale_check_days") == 180
    ok, _ = sr.save("stale_check_days", 120)
    assert ok and sr.value("stale_check_days") == 120 and sr.int_value("stale_check_days") == 120
    _sql(db, "UPDATE settings SET value = 'abc' WHERE key = 'stale_check_days'")
    assert sr.value("stale_check_days") == 180


def test_save_refuses_out_of_range_and_non_numbers_and_writes_nothing(db):
    assert sr.save("stale_check_days", 5)[0] is False
    assert sr.save("stale_check_days", "abc")[0] is False
    assert sr.save("nope", 1)[0] is False
    assert fetchone(db, "SELECT COUNT(*) FROM settings WHERE key = 'stale_check_days'")[0] == 0
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE entity_name = 'settings'")[0] == 0


def test_a_change_is_audited_old_to_new_and_an_unchanged_save_is_not(db):
    sr.save("inflow_weeks", 12)
    sr.save("inflow_weeks", 12)
    rows = _rows(db, "SELECT user_action, new_values FROM audit_logs WHERE entity_name = 'settings'")
    assert len(rows) == 1 and rows[0][0] == "SETTING_CHANGED" and "8 -> 12" in rows[0][1]


def test_existing_setting_keys_keep_working(db):
    from engines.bank_alerts import stale_days
    from engines.cc_service import missing_wait_days
    sr.save("stale_deposit_days", 10)
    sr.save("cc_missing_wait_days", 3)
    assert stale_days() == 10 and missing_wait_days() == 3


def test_reset_removes_the_saved_value_and_is_audited(db):
    sr.save("planner_max_days_late", 12)
    ok, msg = sr.reset("planner_max_days_late")
    assert ok and sr.value("planner_max_days_late") == 7
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'SETTING_RESET'")[0] == 1
    assert sr.changed_from_default() == []


def test_the_engines_read_the_settings(db):
    from engines import cash_insights_service as ci
    from engines import data_health_service as dh
    from engines import overview_report_service as ov
    from engines import validators
    sr.save("forecast_fresh_days", 20)
    sr.save("inflow_weeks", 4)
    sr.save("min_money_amount", 50)
    sr.save("stale_check_days", 90)
    assert ci.forecast_fresh_days() == 20 and ci.inflow_weeks() == 4
    assert validators.money_amount_error(10, "Principal") is not None and validators.money_amount_error(60, "Principal") is None
    assert dh.stale_check_days() == 90 and ov.stale_days() == 90


# ---- keyboard shortcuts ----
def test_default_shortcuts_equal_the_old_fixed_keys(db):
    s = sr.shortcuts()
    assert s["search"] == {"mod": "None", "key": "/"} and s["park"] == {"mod": "Alt", "key": "P"}
    assert s["new"] == {"mod": "Alt", "key": "N"} and s["pages"] == {"mod": "Alt"}
    assert sr.problems(s) == []


def _cfg(**over):
    c = sr.shortcuts()
    c.update(over)
    return c


def test_a_shortcut_can_be_changed_audited_and_reset(db):
    ok, msg = sr.save_shortcuts(_cfg(park={"mod": "Alt", "key": "s"}))
    assert ok, msg
    assert sr.shortcuts()["park"] == {"mod": "Alt", "key": "S"}                # letters are stored upper-case
    assert "Alt+P -> Alt+S" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'SHORTCUTS_CHANGED'")[0]
    sr.reset_shortcuts()
    assert sr.shortcuts() == sr.DEFAULT_SHORTCUTS
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'SHORTCUTS_RESET'")[0] == 1


def test_conflicts_and_unusable_keys_are_refused(db):
    two_on_one = sr.problems(_cfg(new={"mod": "Alt", "key": "P"}))
    assert any("already used" in p for p in two_on_one)
    assert any("plain letter" in p for p in sr.problems(_cfg(park={"mod": "None", "key": "P"})))
    assert any("browser menu" in p for p in sr.problems(_cfg(park={"mod": "Alt", "key": "F"})))
    assert any("menu page" in p for p in sr.problems(_cfg(park={"mod": "Alt", "key": "3"})))
    assert any("one letter" in p for p in sr.problems(_cfg(park={"mod": "Alt", "key": "PQ"})))
    assert sr.problems(_cfg(pages={"mod": "None"}))
    ok, _ = sr.save_shortcuts(_cfg(new={"mod": "Alt", "key": "P"}))
    assert not ok and sr.shortcuts() == sr.DEFAULT_SHORTCUTS


def test_changing_the_page_modifier_frees_the_digit_for_other_actions(db):
    assert sr.save_shortcuts(_cfg(pages={"mod": "Ctrl+Alt"}, park={"mod": "Alt", "key": "3"}))[0]


def test_a_damaged_shortcut_setting_falls_back_to_defaults(db):
    _sql(db, "INSERT INTO settings (key, value, category, description) VALUES (?, '{not json', 'settings', 'x')", (sr.SHORTCUT_KEY,))
    assert sr.shortcuts() == sr.DEFAULT_SHORTCUTS
    _sql(db, "UPDATE settings SET value = ? WHERE key = ?", (json.dumps({"park": {"mod": "Bogus", "key": "Z"}}), sr.SHORTCUT_KEY))
    assert sr.shortcuts()["park"] == {"mod": "Alt", "key": "P"}
