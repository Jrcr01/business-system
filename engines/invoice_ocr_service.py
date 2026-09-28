"""Invoice field extraction via local OCR (Tesseract) — no external API, no per-call cost.

Not a replacement for manual review: every extracted result lands in a staging
grid in the UI that the user confirms or corrects before it becomes a real
invoice row (via the same add_invoice() path as manual entry), same as any
other data-entry shortcut in this app. Local OCR's raw-text + regex extraction
is less accurate than a vision model on messy/varied invoice layouts — expect
more manual correction than a cloud OCR approach would need.
"""
from __future__ import annotations

import io
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime

import pdfplumber
import pytesseract
from PIL import Image, ImageOps
from engines.clock import manila_today

# pytesseract only finds the engine via PATH by default. A fresh Windows install
# (e.g. via winget) often isn't on PATH yet for an already-running process — fall
# back to the standard install location so this doesn't require a machine restart.
if shutil.which("tesseract") is None:
    _default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(_default_win_path):
        pytesseract.pytesseract.tesseract_cmd = _default_win_path

_SUPPORTED_EXTS = {"png", "jpg", "jpeg", "pdf"}

# "Net" labels (e.g. "Net DR Amount") take priority over "Total"/gross labels
# (e.g. "Total DR Amtount") when a document prints both — the net figure is
# the actual amount due after any discount, same principle as this app's own
# gross-vs-net invoice fields. The gap between the label word and the number
# is matched loosely ([^\d\n]{0,25}) rather than requiring the literal word
# "amount" right after it, since real documents vary the wording a lot
# ("DR Amount", "Amount Due", even a printed typo like "Amtount").
_NET_AMOUNT_RE = re.compile(r"\bnet\b[^\d\n]{0,25}?([\d,]+\.\d{2})", re.IGNORECASE)
_TOTAL_AMOUNT_RE = re.compile(r"\b(?:total|grand\s*total|amount\s*due|balance\s*due)\b[^\d\n]{0,25}?([\d,]+\.\d{2})", re.IGNORECASE)
_ANY_AMOUNT_RE = re.compile(r"(?:php|p|₱|\$)?\s*([\d,]{1,3}(?:,\d{3})*\.\d{2})")

# "no" is only 2 letters, so a bare `no\.?` sub-pattern matches the first two
# letters of any longer word starting the same way (Nothing, Note, Normal,
# November, ...) and then greedily captures the rest of that word as if it
# were the value — this is exactly what happened with a printed
# "*** Nothing Follows ***" footer. Guard with a negative lookahead requiring
# the next character NOT be a letter, so "No" only matches as a standalone
# token (optionally followed by a period), never as a word prefix.
_NO = r"no(?![a-zA-Z])\.?"

# Doc-ref labels across the common Philippine trading-document conventions:
# Invoice No. (plain), Delivery Receipt (DR), Sales Invoice (SI),
# Official Receipt (OR), Charge Invoice (CI) — spelled out or abbreviated.
_DOC_REF_LABEL_RE = re.compile(
    rf"\b(?:"
    rf"invoice\s*(?:{_NO}|number|#)|inv\.?\s*{_NO}|"
    rf"doc\.?\s*ref\.?|reference\s*{_NO}|"
    rf"delivery\s*receipt\s*(?:{_NO}|number|#)|d\.?\s*r\.?\s*{_NO}|"
    rf"sales\s*invoice\s*(?:{_NO}|number|#)|s\.?\s*i\.?\s*{_NO}|"
    rf"official\s*receipt\s*(?:{_NO}|number|#)|o\.?\s*r\.?\s*{_NO}|"
    rf"charge\s*invoice\s*(?:{_NO}|number|#)|c\.?\s*i\.?\s*{_NO}"
    r")\s*[:.\-]?\s*([A-Za-z0-9\-\/]+)",
    re.IGNORECASE,
)

# Last-resort fallback: a bare "No.:" (common when the document type is a
# separate heading/badge — e.g. a "DELIVERY RECEIPT" badge next to a plain
# "No.: 0355" — rather than grammatically joined as "Delivery Receipt No.").
# Excludes "P.O. No." / "S.O. No." right before it, since those are a
# different reference number (the customer's purchase/sales order), not
# this document's own number.
_BARE_NO_RE = re.compile(r"(?<!P\.O\.\s)(?<!PO\s)(?<!S\.O\.\s)(?<!SO\s)\bNo\.?\s*[:.]?\s*([A-Za-z0-9\-\/]+)", re.IGNORECASE)

_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b"), lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
]
_MONTH_NAME_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)


@dataclass
class InvoiceFields:
    doc_ref: str
    invoice_date: date
    amount: float


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Grayscale + autocontrast + upscale small images — cheap, generic accuracy
    wins for phone photos (uneven lighting, small text relative to frame) that
    don't depend on any particular document's layout."""
    image = ImageOps.exif_transpose(image)  # phones often store rotation as metadata only
    image = image.convert("L")  # grayscale
    image = ImageOps.autocontrast(image)
    min_dim = 1600
    if max(image.size) < min_dim:
        scale = min_dim / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    return image


def _extract_text(file_bytes: bytes, filename: str) -> tuple[str | None, str | None]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _SUPPORTED_EXTS:
        return None, "Unsupported file type (use PNG, JPG, or PDF)."

    if ext == "pdf":
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            return None, f"Could not read PDF: {e}"
        if not text.strip():
            return None, "This PDF has no extractable text (likely a scanned image without a text layer) — try uploading it as a JPG/PNG instead."
        return text, None

    try:
        image = Image.open(io.BytesIO(file_bytes))
        image = _preprocess_for_ocr(image)
        text = pytesseract.image_to_string(image)
    except pytesseract.TesseractNotFoundError:
        return None, "Tesseract OCR engine not found on this machine — install it and restart the app."
    except Exception as e:
        return None, f"Could not read image: {e}"
    if not text.strip():
        return None, "No readable text found in this image."
    return text, None


def _find_doc_ref(text: str) -> str | None:
    m = _DOC_REF_LABEL_RE.search(text)
    if m:
        return m.group(1).strip()
    m = _BARE_NO_RE.search(text)
    return m.group(1).strip() if m else None


def _find_date(text: str) -> date | None:
    for pattern, build in _DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                return build(m)
            except ValueError:
                continue
    m = _MONTH_NAME_DATE_RE.search(text)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
            except ValueError:
                return None
    return None


def _find_amount(text: str) -> float | None:
    for pattern in (_NET_AMOUNT_RE, _TOTAL_AMOUNT_RE):
        m = pattern.search(text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    # Fall back to the largest decimal amount on the page — invoice totals are
    # usually the biggest number present, but this is a much weaker guess than
    # a labeled match, so results here deserve extra scrutiny in the review grid.
    candidates = [float(v.replace(",", "")) for v in _ANY_AMOUNT_RE.findall(text)]
    return max(candidates) if candidates else None


def extract_invoice_fields(file_bytes: bytes, filename: str) -> tuple[InvoiceFields | None, str | None, str | None]:
    """Returns (fields, warning_or_none, raw_text). fields is populated with
    best-effort defaults whenever any text was read at all — warning names
    what couldn't be confidently parsed and needs a manual look in the review
    grid. fields is None only on a hard failure (unsupported file, unreadable
    scan, no text at all), where there's nothing to stage; raw_text is still
    returned when available (e.g. text was read but every field parse failed)
    so a failure can be diagnosed from what OCR actually produced instead of
    guessing. Never raises — every failure mode is reported as a message so
    one bad upload doesn't abort the whole batch."""
    text, err = _extract_text(file_bytes, filename)
    if text is None:
        return None, err, None

    doc_ref = _find_doc_ref(text)
    invoice_date = _find_date(text)
    amount = _find_amount(text)

    missing = [label for label, val in [("Doc Ref", doc_ref), ("Date", invoice_date), ("Amount", amount)] if val is None]
    fields = InvoiceFields(
        doc_ref=doc_ref or "",
        invoice_date=invoice_date or manila_today(),
        amount=amount if amount is not None else 0.0,
    )
    warning = f"Could not read {', '.join(missing)} — check/fill in manually." if missing else None
    return fields, warning, text
