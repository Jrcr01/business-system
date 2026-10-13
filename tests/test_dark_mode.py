"""Dark mode (owner, 2026-10-13: "our app should have a night mode or dark mode"): each person's own choice -- light, dark, or auto -- kept on
their account; everyone starts on light; the sign-in page keeps its own look; paper always prints black on white."""
import re

from tests.test_phone import _n, _on
from tests.test_web_d0 import _admin, _sign_in_new


def test_each_person_chooses_their_own_look(web, db):
    _admin(web)
    page = web.get("/").content.decode()
    assert '<html lang="en" data-theme="light">' in page and 'action="/theme/"' in page          # everyone starts on light
    r = web.post("/theme/", {"theme": "dark", "next": "/checks/"})
    assert r["Location"] == "/checks/" and _n("SELECT theme FROM app_users WHERE email = 'admin@slicks.test'") == "dark"
    assert '<html lang="en" data-theme="dark">' in web.get("/checks/").content.decode()
    _on()
    assert '<html lang="en" data-theme="dark">' in web.get("/phone/").content.decode()          # the phone pages follow the same choice
    other = _sign_in_new("pat@slicks.test", "owner")
    assert 'data-theme="light"' in other.get("/").content.decode()                               # one person's choice is theirs alone
    web.post("/theme/", {"theme": "auto", "next": "//evil.example/"})
    assert 'data-theme="auto"' in web.get("/").content.decode()
    assert web.post("/theme/", {"theme": "auto", "next": "//evil.example/"})["Location"] == "/"  # never sent off the site
    web.post("/theme/", {"theme": "purple"})
    assert _n("SELECT theme FROM app_users WHERE email = 'admin@slicks.test'") == "auto"         # an unknown look changes nothing


def test_every_colour_has_a_dark_partner_and_paper_stays_light(web, db):
    _admin(web)
    css = web.get("/static/core/slicks.css")
    text = (b"".join(css.streaming_content) if css.streaming else css.content).decode()
    head, dark = text.split("/* ---- dark mode", 1)
    used = set(re.findall(r"var\((--(?:fg|bg|bd)-[0-9a-f]{6})\)", text))
    phone = web.get("/static/phone/phone.css")
    used |= set(re.findall(r"var\((--(?:fg|bg|bd)-[0-9a-f]{6})\)", (b"".join(phone.streaming_content) if phone.streaming else phone.content).decode()))
    light_block, dark_block = dark.split("@media screen", 1)
    for name in used:
        assert f"{name}:" in light_block and f"{name}:" in dark_block, name
    assert 'html[data-theme="dark"]' in dark_block                                              # inside "@media screen": print is never dark
    web.post("/logout/")
    assert 'data-theme="light"' in web.get("/login/").content.decode()                           # nobody signed in: the sign-in page as it always was
