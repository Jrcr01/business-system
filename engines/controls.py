"""The control register (2026-10-03): every control the system applies, by process, in plain words, so a person (and a reviewer) can see what is checked, when, and what happens.
Controls guide and record; they rarely block. Kind: STOP = refused whoever asks (four only), FLAG = goes through and lands on the Review list, GUIDE = a default, a prefill or a
warning so the mistake is not made. `test` names the test that proves it. Read by the Roles and controls page; tests/test_controls.py checks every `test` exists."""
from __future__ import annotations

CONTROLS = [
    # process, kind, control, what happens, test
    ("Buy", "GUIDE", "Prices come from the price list", "An order line takes the supplier's net price (list less the discount chain); a line with no price cannot be ordered.", "test_discounts.py"),
    ("Buy", "GUIDE", "Delivery date and pay date", "The delivery date starts at the supplier's usual days; the pay date is delivery plus terms on a bank day, and the chart shows what is already due that week.", "test_po_schedule.py"),
    ("Buy", "FLAG", "A high-payables day", "If the day an order falls due would pass the daily limit (Settings, 0 = off), the form warns and suggests a lighter delivery day; the user decides, and ordering anyway is noted on the Review list.", "test_web_day_limit.py"),
    ("Buy", "FLAG", "Ordered it and received it", "The buyer may record the delivery of his own order; it is flagged for review.", "test_review_flags.py"),
    ("Pay", "FLAG", "Received the goods and matched the invoice", "The same person matching the invoice to goods they received is flagged for review.", "test_review_flags.py"),
    ("Pay", "FLAG", "Invoice price well above the order", "An invoice price more than 10% above the order's net price goes through at the price charged and is flagged.", "test_review_flags.py"),
    ("Pay", "STOP", "A check above the batch balance", "Refused: a check can never pay more than the batch owes.", "test_hard_stops.py"),
    ("Pay", "STOP", "The same invoice number twice for one supplier", "Refused: one supplier cannot have the same invoice number twice.", "test_hard_stops.py"),
    ("Pay", "STOP", "A check number used twice", "Refused: a check number can be given once; the same number twice in one pass is refused too.", "test_hard_stops.py"),
    ("Pay", "GUIDE", "Check numbers follow from the first", "Type the first number of a run; the rest follow in order and can be corrected.", "test_check_rules.py"),
    ("Release", "STOP", "Releasing a check that was never printed", "Refused: a check cannot be handed to a supplier before it is printed.", "test_hard_stops.py"),
    ("Release", "GUIDE", "Release by slip number", "The statement is printed with a release slip number; one entry marks every check on the slip released, with who received them.", "test_web_d0.py"),
    ("Release", "GUIDE", "Slips not collected", "Home warns when a slip has waited more than 7 days.", "test_web_d0.py"),
    ("Bank", "GUIDE", "A card is paid when the bank debits it", "A bank debit of exactly a card statement's total marks that statement Paid on the bank's date.", "test_bank_recon_v2.py"),
    ("Bank", "FLAG", "A loan installment with no bank debit", "An installment past due with no matching debit shows in Data health.", "test_bank_recon_v2.py"),
    ("Set-up", "FLAG", "A new supplier or lender", "Anyone but the System admin may add one and use it at once; it is flagged so the owner notices before a check is printed.", "test_review_flags.py"),
    ("Set-up", "GUIDE", "System functions are the System admin's", "Users and roles, settings, the audit log, data fixes and the developer view are not available to owners.", "test_permissions.py"),
    ("Pay", "GUIDE", "A marked day is a bank day off", "A payment due on a holiday or a no-check day is paid the next bank day in Payables due, the calendar and the reports; new orders and the planner skip it; stored due dates and written checks keep their dates.", "test_calendar_marks.py"),
    ("Set-up", "GUIDE", "Calendar marks are audited", "Marking a holiday, a no-check day or a note is recorded with who did it; only Owner and Payables may.", "test_calendar_marks.py"),
]

KIND_LABEL = {"STOP": "Refused", "FLAG": "Goes through, flagged", "GUIDE": "Guides"}
