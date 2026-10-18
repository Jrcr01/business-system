"""My home learns each person's most-used pages (owner, 2026-10-19: "my home tiles ... should be the basic tile should have or frequent click by the
user"; "auto learn with pinning, admin too"). A click on a Home card or a menu entry is counted per person and day; My home is the person's pins,
then their most-clicked cards of the last 30 days -- counted up to yesterday, so the cards do not move during the day. Too few clicks yet: the basic six."""
from __future__ import annotations

from datetime import timedelta

from engines.clock import manila_today
from engines.common import get_conn, write_tx

DAYS = 30
SIZE = 6
ENOUGH = 15                                  # clicks in the window before My home follows them


def count(email: str, card: str) -> None:
    email, card = (email or "").strip().lower(), (card or "").strip()[:80]
    if not email or not card:
        return
    with write_tx() as conn:
        conn.execute("""INSERT INTO home_clicks (user_email, card, day, clicks) VALUES (?, ?, ?, 1)
                        ON CONFLICT (user_email, card, day) DO UPDATE SET clicks = clicks + 1""", (email, card, str(manila_today())))


def pins(email: str) -> list[str]:
    with get_conn() as conn:
        return [r[0] for r in conn.execute("SELECT card FROM home_pins WHERE user_email = ? ORDER BY pinned_at", ((email or "").lower(),))]


def toggle_pin(email: str, card: str) -> bool:
    """Pins the card, or unpins it if it was pinned. Returns True when it is pinned now."""
    email, card = (email or "").strip().lower(), (card or "").strip()[:80]
    with write_tx() as conn:
        if conn.execute("SELECT 1 FROM home_pins WHERE user_email = ? AND card = ?", (email, card)).fetchone():
            conn.execute("DELETE FROM home_pins WHERE user_email = ? AND card = ?", (email, card))
            return False
        conn.execute("INSERT INTO home_pins (user_email, card) VALUES (?, ?)", (email, card))
        return True


def ranked(email: str) -> dict[str, int]:
    """{card: clicks} over the last 30 days, up to yesterday."""
    today = manila_today()
    with get_conn() as conn:
        rows = conn.execute("""SELECT card, SUM(clicks) FROM home_clicks WHERE user_email = ? AND day >= ? AND day < ? GROUP BY card""",
                            ((email or "").lower(), str(today - timedelta(days=DAYS)), str(today))).fetchall()
    return {c: int(n) for c, n in rows}


def my_home(email: str, cards: list[dict], basics: list[str]) -> list[dict]:
    """The person's My home from the cards they may open: pins first, then the most clicked (a card counts its own id and, for a page's main card, the
    menu clicks on that page), up to six; the basics while there are too few clicks. cards: [{'key', 'page', ...}]."""
    by_key = {c["key"]: c for c in cards}
    out, seen = [], set()
    for k in pins(email):
        if k in by_key and k not in seen:
            out.append(dict(by_key[k], pinned=True))
            seen.add(k)
    counts = ranked(email)
    if sum(counts.values()) >= ENOUGH:
        def score(c):
            return counts.get(c["key"], 0) + (counts.get(c["page"], 0) if c.get("main") else 0)
        order = sorted((c for c in cards if c["key"] not in seen and score(c) > 0), key=lambda c: -score(c))
    else:                                                       # the basics, all of them, until the clicks say otherwise
        return out + [by_key[k] for k in basics if k in by_key and k not in seen]
    for c in order:
        if len(out) >= SIZE:
            break
        out.append(c)
        seen.add(c["key"])
    return out
