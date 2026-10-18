"""Keeps the last rows of a printed table together (owner, 2026-10-18, RQ-0001: "I want to avoid this total only in the next page. It is ok if there are
other listed" -- "this applies to other report as well"): when a table runs onto another page, its last item goes over with the total, never the total
alone under a repeated header."""
from reportlab.platypus import Table


def keep_tail(table: Table, rows: int = 2) -> Table:
    """Keep the last `rows` rows on one page (the header row is never part of it). Returns the table."""
    n = len(getattr(table, "_cellvalues", []) or [])
    head = int(getattr(table, "repeatRows", 0) or 0) if isinstance(getattr(table, "repeatRows", 0), int) else 1
    k = min(rows, n - head)
    if k >= 2:
        table.setStyle([("NOSPLIT", (0, n - k), (-1, n - 1))])
    return table


# ---- "Page 1 of 2 · continued on the next page" (owner, 2026-10-19: "is it possible to have a note 'continue to next page' ... especially when the
# statement needs more than 1") ----------------------------------------------------------------------------------------------------------------------

from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Flowable


class CopyStart(Flowable):
    """Put at the start of each copy in a printout (the filing copy, the supplier copy): pages are counted per copy, and the last page of a copy
    never says 'continued'."""

    def wrap(self, *args):
        return 0, 0

    def draw(self):
        starts = getattr(self.canv, "_copy_starts", None)
        if starts is not None:
            starts.add(self.canv.getPageNumber())


def continued_canvas(right_x: float, y: float, font: str = "Helvetica", size: float = 7.5, gray: float = 0.42):
    """A canvasmaker for doc.build(): at the foot of every page, right-aligned at (right_x, y), "Page i of n", plus "· continued on the next page"
    when the same copy goes on."""
    class _Continued(Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._kept, self._copy_starts = [], {1}

        def showPage(self):
            self._kept.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._kept)
            starts = sorted(s for s in self._copy_starts if s <= total) or [1]
            for n, state in enumerate(self._kept, start=1):
                self.__dict__.update(state)
                first = max(s for s in starts if s <= n)
                later = [s for s in starts if s > first]
                last = (later[0] - 1) if later else total
                text = f"Page {n - first + 1} of {last - first + 1}" + ("  ·  Continued on the next page" if n < last else "")
                self.saveState()
                self.setFont(font, size)
                self.setFillGray(gray)
                self.drawRightString(right_x, y, text)
                self.restoreState()
                Canvas.showPage(self)
            Canvas.save(self)
    return _Continued
