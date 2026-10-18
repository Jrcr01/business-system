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


def test_a_long_statement_says_continued_on_each_page_but_the_last_of_its_copy(web, db):
    """Owner, 2026-10-19: "is it possible to have a note 'continue to next page' ... especially when the statement needs more than 1"."""
    import io
    import pypdf
    from conftest import make_creditor
    from engines.batch_statement_service import generate_batch_statement_pdf
    from tests.test_web_d0 import _admin
    from tests.test_web_d3a import _add_inv, _new_batch
    _admin(web)
    sid = _new_batch(web, make_creditor(db, "New Century", terms="30"))
    for n in range(45):
        _add_inv(web, sid, doc=f"SI-{1000 + n}", gross=f"{1000 + n * 7:,}", confirmed="1")
    out = generate_batch_statement_pdf(sid, copies="both")
    pdf = out[0] if isinstance(out, tuple) else out
    pages = [p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages]
    assert len(pages) >= 4
    first_copy_pages = int(pages[0].split("Page 1 of ")[1].split()[0])
    assert first_copy_pages >= 2
    for k in range(first_copy_pages - 1):
        assert "Continued on the next page" in pages[k]
    assert "Continued on the next page" not in pages[first_copy_pages - 1]                  # the copy's last page
    assert "Page 1 of" in pages[first_copy_pages]                                            # the supplier copy counts from 1 again
