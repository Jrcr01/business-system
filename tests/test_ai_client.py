"""The AI door (owner, 2026-10-20: "I want to ready my system for this"): off until a key and the switch, a hard monthly cap in pesos, every call
logged with its cost, 80% warning on the Review list. The internet is never called: a stand-in client answers."""
from types import SimpleNamespace

import pytest

from conftest import fetchone
from tests.test_web_d0 import _admin

KEY = "sk-ant-api03-" + "x" * 40 + "WXYZ"


def _answer(text="read ok", tin=10_000, tout=2_000, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=tin, output_tokens=tout, cache_creation_input_tokens=0, cache_read_input_tokens=0))


@pytest.fixture
def fake(monkeypatch):
    from engines import ai_client
    calls = []

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                calls.append(kw)
                return Client.reply
        reply = _answer()
    monkeypatch.setattr(ai_client, "_client", lambda: Client)
    return SimpleNamespace(calls=calls, client=Client)


def _on(key=KEY):
    from engines import ai_client, settings_registry as sr
    assert ai_client.save_key(key)[0]
    assert sr.save("ai_on", 1)[0]


def test_off_until_a_key_and_the_switch(web, db, fake):
    from engines import ai_client, settings_registry as sr
    ok, text, msg = ai_client.ask("price_photo", "hello")
    assert not ok and "switched off" in msg and fake.calls == []                  # never called while off
    assert not ai_client.save_key("not a key")[0]
    assert ai_client.save_key(KEY)[0]
    st = ai_client.status()
    assert st["has_key"] and st["key_end"] == "WXYZ" and not st["on"] and KEY not in str(st)
    assert not ai_client.ask("price_photo", "hello")[0] and fake.calls == []      # a key alone is not enough
    assert sr.save("ai_on", 1)[0]
    ok, text, msg = ai_client.ask("price_photo", "hello")
    assert ok and text == "read ok" and fake.calls[0]["model"] == "claude-opus-5-5"


def test_each_call_is_logged_in_pesos_and_the_cap_stops_it(web, db, fake):
    from engines import ai_client, settings_registry as sr
    _on()
    ai_client.ask("price_photo", "hello")                                        # 10k in x $4/M + 2k out x $20/M = $0.08 -> x 58 = 4.64 pesos
    row = fetchone(db, "SELECT feature, input_tokens, output_tokens, cost_php, ok FROM ai_usage")
    assert row[:3] == ("price_photo", 10_000, 2_000) and abs(row[3] - 4.64) < 0.001 and row[4] == 1
    assert ai_client.month_spent() == 4.64
    assert sr.save("ai_monthly_cap", 4)[0]                                       # spent 4.64 >= cap 4: refused, not called
    n = len(fake.calls)
    ok, text, msg = ai_client.ask("price_photo", "hello")
    assert not ok and "used up" in msg and len(fake.calls) == n


def test_warned_once_at_80_percent(web, db, fake):
    from engines import ai_client, settings_registry as sr
    _on()
    assert sr.save("ai_monthly_cap", 10)[0]
    ai_client.ask("price_photo", "a")                                            # 4.64 of 10: under 80%
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'AI_SPEND_80'")[0] == 0
    ai_client.ask("price_photo", "b")                                            # 9.28: past 80%
    ai_client.ask("price_photo", "c") if ai_client.month_spent() < 10 else None
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'AI_SPEND_80'")[0] == 1


def test_a_declined_or_cut_answer_is_not_used(web, db, fake):
    from engines import ai_client
    _on()
    fake.client.reply = _answer(stop="refusal")
    ok, text, msg = ai_client.ask("price_photo", "x")
    assert not ok and "declined" in msg
    fake.client.reply = _answer(text="half", stop="max_tokens")
    ok, text, msg = ai_client.ask("price_photo", "x")
    assert not ok and "cut short" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM ai_usage WHERE ok = 0")[0] == 2


def test_settings_ai_page_for_the_system_admin(web, db, fake):
    _admin(web)
    page = web.get("/settings/ai/").content.decode()
    assert "none yet" in page and "Switch AI help on" in page
    web.post("/settings/ai/", {"action": "switch", "on": "1"})
    assert "Add the key first" in web.get("/settings/ai/").content.decode()
    web.post("/settings/ai/", {"action": "key", "key": KEY})
    page = web.get("/settings/ai/").content.decode()
    assert "ends …WXYZ" in page and KEY not in page                               # never shown again
    web.post("/settings/ai/", {"action": "switch", "on": "1"})
    page = web.get("/settings/ai/").content.decode()
    assert "Switch AI help off" in page
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'AI_KEY_SAVED'")[0] == 1
