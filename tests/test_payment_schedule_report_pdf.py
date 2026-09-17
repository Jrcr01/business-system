"""Payment schedule PDF report -- regression test for the long-payee-name column overlap bug.

A plain string in a ReportLab Table cell doesn't wrap to its column width; it just overflows and
visually overlaps the next column (reported live: "STONEWELL PHILIPPINES WHEELS & TIRES, INC."
running into the Track column). Fixed by wrapping Payee Name/Reference in Paragraph cells, which
also required escaping ("&" in a real name like "S&P Enterprises Inc." is markup to ReportLab's
Paragraph parser, not literal text, and raises un-escaped).

Also covers a second overlap the column-width rebalance (done as a follow-up to the fix above)
introduced by itself: "GRAND TOTAL" and the item count used to be two separate, unspanned cells
in columns 0/1 -- fine at the old, wider Due Date column width, but "GRAND TOTAL" alone no
longer fit the narrowed one. Fixed by combining them into one spanned cell, same pattern the
daily subtotal row already used."""
import io

import pandas as pd
from pypdf import PdfReader

from engines.payment_schedule_report_service import _build_due_date_schedule_pdf


def test_long_and_ampersand_payee_names_render_without_crashing():
    df = pd.DataFrame([
        {
            "actual_payment_date": "2026-11-21", "due_date": "2026-11-21",
            "payee_name": "STONEWELL PHILIPPINES WHEELS & TIRES, INC.",
            "track": "Supplier", "reference": "2565364", "amount": 115000.00,
        },
        {
            "actual_payment_date": "2026-11-21", "due_date": "2026-11-21",
            "payee_name": "S&P Enterprises Inc.",
            "track": "Supplier", "reference": "2565460", "amount": 4392.00,
        },
        {
            "actual_payment_date": "2026-11-21", "due_date": "2026-11-21",
            "payee_name": "BPI", "track": "Bank Loan",
            "reference": "Emelita Roxas - PENDING-153", "amount": 101100.00,
        },
    ])

    pdf_bytes = _build_due_date_schedule_pdf("SCHEDULE OF PAYMENTS", "TEST", df, "Reference")

    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 500

    text = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()
    # A combined, single-cell label -- not "GRAND TOTAL" and "3 item(s)" as two separately
    # positioned (and therefore overlap-prone) cells.
    assert "GRAND TOTAL — 3 item(s)" in text
    assert "S&P Enterprises Inc." in text
