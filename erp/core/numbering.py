"""ERP document numbers (core step C2, 2026-09-28; owner: "like AP: 2026 - CR 0001").

    number = next_number("CR")          # inside transaction.atomic(): '2026 - CR 0001'

* Format: year, a 2-letter code per document type, 4 digits (more if ever needed); starts again at 0001 each year (Manila year).
* The SAME counter table as AP's batch / loan-note numbers (doc_counters, via engines.db_engine.next_doc_number): taken inside the
  save's locked step (transaction_mode IMMEDIATE), so two people saving at once never get the same number, and a number is never reused.
* A document that is voided keeps its number (voids never delete), so numbers are never skipped silently.
* Each module registers its codes in SERIES when it adds a document type; 'OS' and 'LN' belong to AP."""
import re

from django.db import connection

from engines.clock import manila_today
from engines.db_engine import next_doc_number

AP_SERIES = {"OS": "Order statement (AP batch)", "LN": "Loan note (AP)"}
SERIES: dict[str, str] = {}              # code -> document type, added by each module (e.g. Sales: "CR": "Collection receipt")
_CODE = re.compile(r"^[A-Z]{2}$")


def register(code: str, name: str) -> None:
    if not _CODE.match(code or ""):
        raise ValueError(f"A document code is 2 capital letters, not {code!r}.")
    if code in AP_SERIES or SERIES.get(code, name) != name:
        raise ValueError(f"The code {code} is already used for {AP_SERIES.get(code) or SERIES[code]}.")
    SERIES[code] = name


def format_number(code: str, year: int, n: int) -> str:
    return f"{year} - {code} {n:04d}"


def next_number(code: str, year: int | None = None) -> str:
    """The next number of a registered series. Must be called inside the transaction.atomic() that saves the document."""
    if code not in SERIES:
        raise ValueError(f"Unknown document code {code!r} -- register it first.")
    if not connection.in_atomic_block:
        raise RuntimeError("next_number() must run inside the transaction.atomic() that saves the document.")
    year = int(year or manila_today().year)
    connection.ensure_connection()
    n = next_doc_number(connection.connection, code, year)
    return format_number(code, year, n)
