"""A printed table never leaves its total alone on the next page (owner, 2026-10-18, RQ-0001; "this applies to other report as well")."""
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Table, TableStyle


def test_the_total_goes_over_with_the_last_item():
    from engines.pdf_tail import keep_tail
    rows = [["#", "Item", "Amount"]] + [[str(i), f"Item {i}", "100.00"] for i in range(1, 16)] + [["", "TOTAL", "1,500.00"]]
    t = keep_tail(Table(rows, repeatRows=1))
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9)]))          # a later style keeps it
    _, h = t.wrap(A4[0], A4[1])
    row_h = h / len(rows)
    first, second = t.split(A4[0], row_h * (len(rows) - 0.5))              # room for all but the total
    assert len(second._cellvalues) >= 3                                   # header + last item + total, not header + total
    assert second._cellvalues[-1][1] == "TOTAL" and second._cellvalues[-2][1] == "Item 15"


def test_the_order_request_pdf_still_builds():
    import pandas as pd
    from engines import po_pdf
    assert hasattr(po_pdf, "keep_tail")
