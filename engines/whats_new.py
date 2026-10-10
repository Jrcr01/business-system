"""What's new (owner, 2026-10-09): a short note per release, in plain words, shown once to each person on Home until they press "Got it".
Newest first. Each entry: (id, date, title, [lines]). The id only has to be new and sort after the earlier ones (date + letter)."""

NOTES = [
    ("2026-10-10a", "10/10/2026", "Payables, the daily limit and price lists", [
        "Every recorded SI now counts as owed on its due date, even before a check is parked. The Schedule of payments, Calendar and Home show the full amount.",
        "Daily limit: ₱350,000 target, ₱400,000 ceiling. The Calendar shows each day's planned amount in green, amber or red. Parking or moving a check, or placing an order, onto a day over ₱400,000 asks you to confirm.",
        "Parking checks: a new choice, \"Within the daily limit\", suggests dates that keep each day under the target.",
        "Purchase orders: \"Split into parts\" on a draft order makes separate orders on lighter pay days.",
        "Price lists: the template now has one sheet per type (Tires, Battery, Mags, Rims) with drop-downs. Download a supplier's template, type the new prices, upload.",
    ]),
    ("2026-10-09b", "10/09/2026", "Updates today", [
        "Sign in with your email: after Cloudflare's code, the system asks only for your password.",
        "The System admin now sets, page by page, what each person sees and what each person may change. A page you may only look at says \"View only\".",
        "Credit cards: the bank's statement (SOA) is the final word on what was paid. A daily payment that differs is flagged for review.",
        "Bank statements import again; each imported file is kept for 5 years.",
    ]),
]


def latest_id() -> str:
    return NOTES[0][0] if NOTES else ""


def unseen(seen: str | None) -> list[dict]:
    """The notes newer than the one this person last saw."""
    return [{"id": i, "date": d, "title": t, "lines": lines} for i, d, t, lines in NOTES if not seen or i > seen]
