"""Proves the flake and its fix: the clock's half-minute turns over between making the code and the server checking it."""
import engines.auth_service as a
from tests.test_web_d0 import PW, _code_step


def test_a_code_made_just_before_the_half_minute_turns_still_signs_in(web, db, monkeypatch):
    r = web.post("/setup/", {"name": "Admin", "email": "admin@slicks.test", "password": PW, "password2": PW})
    real, calls = a._now_step, {"n": 0}

    def ticking():                                            # the first two looks at the clock are in one step, every later one in the next
        calls["n"] += 1
        return real() + (0 if calls["n"] <= 2 else 1)
    monkeypatch.setattr(a, "_now_step", ticking)
    r = _code_step(web, r, "admin@slicks.test")
    assert r.status_code == 302 and r["Location"] == "/"
