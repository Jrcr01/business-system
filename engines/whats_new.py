"""What's new (owner, 2026-10-09): a short note per release, in plain words, shown once to each person on Home until they press "Got it".
Newest first. Each entry: (id, date, title, [lines]). The id only has to be new and sort after the earlier ones (date + letter)."""

NOTES = [
    ("2026-10-13a", "10/13/2026", "New: Stock and Sell", [
        "Stock, Items and stock: what is on hand, at what cost, and everything that happened to each item. Stock goes up when goods are received and down when they are sold or lent. It is never typed over.",
        "Labels and scanning: print labels for a delivery (one per piece) and scan them at the counter, on a count, or when lending. A maker's barcode is learned the first time it is scanned.",
        "Stock counts: count one type or one brand at a time. The sheet does not show what the system expects. Stock changes only when the System admin approves a difference, with a reason.",
        "Affiliates: lend tires to, or borrow them from, the relatives' businesses. It stays on their balance until it is returned, paid for at cost, or set off.",
        "Sell, Record the sales: after closing, or in an idle moment, type each invoice from the booklet one after another; saving one offers the next number. Saving takes the stock.",
        "Nothing is refused for being below cost, above stock or on charge. It is saved and noted on the Review list.",
        "Scan out (hidden until Settings says sales start from a scan): the day the scanning routine begins, what leaves is scanned on the phone and the stock moves that moment; in the evening each slip becomes its invoice with the items already filled in.",
        "Sales today: the day's sales in the notebook's order, with the day's money by how it was paid.",
    ]),
    ("2026-10-12b", "10/11/2026", "Sign-in: Google Authenticator for everyone", [
        "Signing in now takes your password and the 6-digit code from Google Authenticator on your own phone. The code is asked once a day on each device.",
        "An authenticator is set up, or replaced after a lost or new phone, only when the System admin allows it. The allowance lasts 24 hours.",
        "Settings, Users: each person's page lists their last 20 sign-ins with the device, the browser and the place. A device not used before is noted on the Review list.",
        "Settings, Users: \"Sign out everywhere\" ends all of a person's sign-ins at once.",
    ]),
    ("2026-10-12a", "10/12/2026", "Purchase orders: ask the supplier first", [
        "New order: choose the supplier and the dates, and the payables calendar shows straight away. Click a day to make the order fall due that day.",
        "Items: click the item box and the supplier's list drops down; type a size, a brand or a pattern to narrow it, click an item to choose it, then set its quantity.",
        "A draft is now sent to the supplier first: \"Send to supplier\" gives it a request number (RQ-0001) and an Order request PDF to send them.",
        "When the supplier answers, press \"Supplier confirmed\" and type what is available on each line (0 drops the line). Only then does it become a purchase order with a PO number.",
        "A request still waiting for the supplier shows on the order calendar as not confirmed, so the day does not look free.",
        "Receive goods and Match invoice are now one page, Receive: tick what arrived and type the SI no. or the DR no. (at least one). Either one records the goods and what we owe together; a DR alone is enough when the supplier gives no SI.",
        "An order should come on one DR or SI. If part of it has not arrived you are asked: the rest will not come, or keep it open for another DR. Settings has a switch to allow several DRs per order.",
        "Each order's page lists its deliveries with what came on each. A delivery typed wrongly can be undone there while it has no check; an SI that comes later for a DR is added to the same payable.",
        "More pieces than ordered, or an item that was not on the order, can be received after you confirm it was agreed; both are noted on the Review list.",
        "The \"Going out\" tab is gone from Purchase orders: the same totals are on the order calendar and on Payables, Calendar.",
        "Card reserve: the estimate now sits on the card's statement day plus its usual number of days to the due date, and leaves out unpaid balances carried over.",
    ]),
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


def _notes() -> list:
    """The notes of what this site has: a part built but not released (engines/features.py) says nothing yet."""
    from engines.features import REVENUE_NOTES, revenue_on
    return NOTES if revenue_on() else [n for n in NOTES if n[0] not in REVENUE_NOTES]


def latest_id() -> str:
    notes = _notes()
    return notes[0][0] if notes else ""


def unseen(seen: str | None) -> list[dict]:
    """The notes newer than the one this person last saw."""
    return [{"id": i, "date": d, "title": t, "lines": lines} for i, d, t, lines in _notes() if not seen or i > seen]
