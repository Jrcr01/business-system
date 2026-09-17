"""Phase 7 — Invoice Extraction Tool: deterministic, geometry-based field extraction.

Standalone from the main AP system (no imports from engines/ or app.py) — this
tool's whole job is to produce a clean CSV that gets imported into the main app
separately, per the Phase 7 design.

Design note on the function signatures below: the spec sketch for
get_surrounding_text()/learn_pattern_from_first_invoice()/auto_extract_field()
describes them as operating on plain text, but a bounding box has no meaningful
"surrounding text" or "position" relative to an already-flattened string —
proximity and re-projection onto a different page both need real word geometry
(each word's own box), not just its text. So these three take a "page dict" —
the richer per-page structure extract_text_from_file() actually returns via its
'pages' list — instead of a bare string. page['text'] still gives the plain
string when that's all that's needed.

Coordinate space: every box and every word position is in the pixel space of
that page's own rendered image (page['image'], page['width']/['height']) — for
a PDF page that means rasterized at DISPLAY_DPI (words scaled up from
pdfplumber's 72-dpi points to match), for an image file it's that image's own
native pixels post-EXIF-transpose. The annotation UI is responsible for mapping
its on-screen (possibly downscaled for display) click coordinates back to this
native space before calling anything here — see extractor_app.py's
render_for_display().
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Callable

import pdfplumber
import pytesseract
from PIL import Image, ImageOps

# pytesseract only finds the engine via PATH by default — fall back to the
# standard Windows install location so a fresh install doesn't need a restart.
import shutil
if shutil.which("tesseract") is None:
    _default_win_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.isfile(_default_win_path):
        pytesseract.pytesseract.tesseract_cmd = _default_win_path

DISPLAY_DPI = 150  # rasterization resolution for PDF pages; also the coordinate space words are reported in
FIELDS = ("invoice_number", "invoice_date", "total_amount")

_LINE_BUCKET_PX = 10  # words within this many px of vertical alignment are treated as "same line" for reading order


# --------------------------------------------------------------------------
# Text/word extraction
# --------------------------------------------------------------------------

def _reading_order_words(words: list[dict]) -> list[dict]:
    """Top-to-bottom, then left-to-right — bucketing y so words on the same
    printed line (which rarely share an exact pixel y) still sort together."""
    return sorted(words, key=lambda w: (round(w["y0"] / _LINE_BUCKET_PX), w["x0"]))


def _extract_pdf_page(pl_page) -> dict:
    scale = DISPLAY_DPI / 72.0
    raw_words = pl_page.extract_words(keep_blank_chars=False)
    words = [
        {
            "text": w["text"],
            "x0": w["x0"] * scale,
            "y0": w["top"] * scale,
            "x1": w["x1"] * scale,
            "y1": w["bottom"] * scale,
        }
        for w in raw_words
    ]
    words = _reading_order_words(words)
    image = pl_page.to_image(resolution=DISPLAY_DPI).original.convert("RGB")
    return {"text": " ".join(w["text"] for w in words), "width": image.width, "height": image.height, "words": words, "image": image}


def _preprocess_for_ocr(image: Image.Image) -> Image.Image:
    """Grayscale + autocontrast + upscale small images for OCR accuracy. Whatever
    this returns is used for BOTH pytesseract and on-screen display, so the
    resulting word boxes always line up with what the user actually sees and
    clicks on — no separate coordinate mapping needed for this step."""
    image = ImageOps.exif_transpose(image)
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    min_dim = 1600
    if max(image.size) < min_dim:
        s = min_dim / max(image.size)
        image = image.resize((int(image.width * s), int(image.height * s)), Image.LANCZOS)
    return image.convert("RGB")


# Two Tesseract passes, merged: the default page-segmentation mode (PSM 3,
# assumes a fairly uniform block of text) can miss isolated text sitting in
# its own visually-distinct area (e.g. a total-amount cell off to the side of
# the main body) — found on a real photographed delivery receipt where PSM 3
# missed the total entirely but PSM 11 ("sparse text", no layout assumption)
# caught it. Running both and merging catches more real invoices than either
# alone, at the cost of ~2x OCR time per image (acceptable for a <50-file batch).
_OCR_CONFIGS = ("", "--psm 11")


def _extract_image_page(image: Image.Image) -> dict:
    image = _preprocess_for_ocr(image)
    words, seen = [], set()
    for config in _OCR_CONFIGS:
        data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)
        for i in range(len(data["text"])):
            txt = data["text"][i].strip()
            if not txt:
                continue
            try:
                if float(data["conf"][i]) < 0:
                    continue
            except (TypeError, ValueError):
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            key = (txt, round(x / 8), round(y / 8))  # collapses near-duplicate hits across passes
            if key in seen:
                continue
            seen.add(key)
            words.append({"text": txt, "x0": float(x), "y0": float(y), "x1": float(x + w), "y1": float(y + h)})
    words = _reading_order_words(words)
    return {"text": " ".join(w["text"] for w in words), "width": image.width, "height": image.height, "words": words, "image": image}


def extract_text_from_file(file_path: str) -> dict:
    """Returns {'file_type': 'pdf'|'image', 'text': full_document_text,
    'pages': [page_dict, ...], 'warning': str|None}. Each page_dict is
    {'text','width','height','words','image'} — see module docstring.
    pdfplumber for PDFs with a real text layer (near-perfect); pytesseract for
    standalone image files. A scanned/image-only PDF (no text layer) is NOT
    OCR'd here — same convention as this project's existing OCR service
    (engines/invoice_ocr_service.py): re-upload it as a PNG/JPG instead. Never
    raises — every failure mode comes back as 'warning' so one bad file in a
    50-invoice batch doesn't abort the rest."""
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")

    if ext == "pdf":
        try:
            with pdfplumber.open(file_path) as pdf:
                pages = [_extract_pdf_page(p) for p in pdf.pages]
        except Exception as e:
            return {"file_type": "pdf", "text": "", "pages": [], "warning": f"Could not read PDF: {e}"}
        if not any(p["words"] for p in pages):
            return {
                "file_type": "pdf", "text": "", "pages": pages,
                "warning": "This PDF has no extractable text (likely scanned without a text layer) — upload it as a PNG/JPG instead.",
            }
        return {"file_type": "pdf", "text": "\n\n".join(p["text"] for p in pages), "pages": pages, "warning": None}

    if ext in ("png", "jpg", "jpeg"):
        try:
            image = ImageOps.exif_transpose(Image.open(file_path))
        except Exception as e:
            return {"file_type": "image", "text": "", "pages": [], "warning": f"Could not read image: {e}"}
        try:
            page = _extract_image_page(image)
        except pytesseract.TesseractNotFoundError:
            return {"file_type": "image", "text": "", "pages": [], "warning": "Tesseract OCR engine not found on this machine."}
        except Exception as e:
            return {"file_type": "image", "text": "", "pages": [], "warning": f"OCR failed: {e}"}
        if not page["words"]:
            return {"file_type": "image", "text": "", "pages": [page], "warning": "No readable text found in this image."}
        return {"file_type": "image", "text": page["text"], "pages": [page], "warning": None}

    return {"file_type": "unknown", "text": "", "pages": [], "warning": f"Unsupported file type: .{ext or '(none)'}"}


# --------------------------------------------------------------------------
# Geometry helpers
# --------------------------------------------------------------------------

def _overlaps(w: dict, x1: float, y1: float, x2: float, y2: float) -> bool:
    return w["x0"] < x2 and w["x1"] > x1 and w["y0"] < y2 and w["y1"] > y1


def _words_in_box(words: list[dict], x1: float, y1: float, x2: float, y2: float) -> str:
    inside = _reading_order_words([w for w in words if _overlaps(w, x1, y1, x2, y2)])
    return " ".join(w["text"] for w in inside)


def get_surrounding_text(page: dict, x1: float, y1: float, x2: float, y2: float, radius: int = 50) -> str:
    """Words within `radius` px of the box (excluding words inside it), in
    reading order, capped at 20 words — the context hint shown to the user
    while annotating, and the source of a field's learned keywords."""
    words = page.get("words") or []
    if not words:
        return ""
    ex1, ey1, ex2, ey2 = x1 - radius, y1 - radius, x2 + radius, y2 + radius
    nearby = [w for w in words if _overlaps(w, ex1, ey1, ex2, ey2) and not _overlaps(w, x1, y1, x2, y2)]
    nearby = _reading_order_words(nearby)
    return " ".join(w["text"] for w in nearby[:20])


_KEYWORD_RE = re.compile(r"[A-Za-z]{2,}")


def _extract_keywords(context: str, max_n: int = 8) -> list[str]:
    """Alphabetic label-ish tokens from a context string, deduplicated
    case-insensitively, in order of appearance — e.g. "Invoice No: INV-2024-001
    Date:" -> ['Invoice', 'No', 'Date']."""
    seen, out = set(), []
    for tok in context.split():
        m = _KEYWORD_RE.match(tok)
        if not m:
            continue
        word, key = m.group(0), m.group(0).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
        if len(out) >= max_n:
            break
    return out


# --------------------------------------------------------------------------
# Pattern learning + auto-extraction
# --------------------------------------------------------------------------

def learn_pattern_from_first_invoice(first_invoice_boxes: dict, first_page: dict) -> dict:
    """first_invoice_boxes: {field_name: (x1, y1, x2, y2)} in first_page's own
    pixel space. Returns {field_name: {'box_norm': (x1,y1,x2,y2) as 0..1
    fractions of page width/height, 'keywords': [...], 'sample_value': str}} —
    normalized so the box can be re-projected onto a different invoice's own
    pixel dimensions in auto_extract_field()."""
    width, height = first_page["width"], first_page["height"]
    pattern = {}
    for field, (x1, y1, x2, y2) in first_invoice_boxes.items():
        context = get_surrounding_text(first_page, x1, y1, x2, y2, radius=80)
        pattern[field] = {
            "box_norm": (x1 / width, y1 / height, x2 / width, y2 / height),
            "keywords": _extract_keywords(context),
            "sample_value": _words_in_box(first_page["words"], x1, y1, x2, y2),
        }
    return pattern


def _extract_by_keyword_anchor(words: list[dict], keywords: list[str], max_words: int = 4) -> str:
    """Fallback for when the projected box misses entirely (layout drifted more
    than the tolerance pad allows): find a learned label word anywhere on the
    page, then read the few words immediately after it on the same printed
    line — the same physical label-then-value relationship an invoice uses."""
    if not keywords:
        return ""
    lower_kw = {k.lower() for k in keywords}
    ordered = _reading_order_words(words)
    for i, w in enumerate(ordered):
        if w["text"].strip(":.#-").lower() not in lower_kw:
            continue
        same_line = [
            w2 for w2 in ordered[i + 1: i + 11]
            if w2["y0"] < w["y1"] and w2["y1"] > w["y0"] and w2["x0"] >= w["x0"]
        ]
        if same_line:
            return " ".join(w2["text"] for w2 in same_line[:max_words])
    return ""


_AMOUNT_JUNK_RE = re.compile(r"[^\d.]")
_DATE_NUMERIC_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
    (re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b"), lambda m: date(int(m[3]), int(m[1]), int(m[2]))),
]
_MONTH_NAME_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)


def _parse_date_flexible(raw: str) -> date | None:
    for pattern, build in _DATE_NUMERIC_PATTERNS:
        m = pattern.search(raw)
        if m:
            try:
                return build(m)
            except ValueError:
                continue
    m = _MONTH_NAME_RE.search(raw)
    if m:
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
            except ValueError:
                continue
    return None


def _clean_field_value(field_name: str, raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if field_name == "total_amount":
        digits = _AMOUNT_JUNK_RE.sub("", raw)
        if not digits:
            return ""
        try:
            return f"{float(digits):.2f}"
        except ValueError:
            return raw
    if field_name == "invoice_date":
        parsed = _parse_date_flexible(raw)
        return parsed.isoformat() if parsed else raw
    return raw.strip(" :#.-")


def _extract_value_near_box(words: list[dict], x1: float, y1: float, x2: float, y2: float, pad_x: float, pad_y: float) -> str:
    """Words overlapping a padded box, restricted to the single printed line
    closest to the box's own vertical center — a generous pad_x tolerates
    left/right layout drift without also sweeping in an adjacent line's label
    (e.g. a "Date:" line sitting just a few px above/below on a tightly
    leaded invoice), since drift is overwhelmingly horizontal in practice
    (columns shift; row order doesn't)."""
    px1, py1, px2, py2 = x1 - pad_x, y1 - pad_y, x2 + pad_x, y2 + pad_y
    candidates = [w for w in words if _overlaps(w, px1, py1, px2, py2)]
    if not candidates:
        return ""
    target_center = (y1 + y2) / 2
    buckets: dict[int, list[dict]] = {}
    for w in candidates:
        buckets.setdefault(round(w["y0"] / _LINE_BUCKET_PX), []).append(w)
    best_bucket = min(buckets, key=lambda b: abs(b * _LINE_BUCKET_PX - target_center))
    return " ".join(w["text"] for w in _reading_order_words(buckets[best_bucket]))


def _strip_leading_keywords(value: str, keywords: list[str]) -> str:
    """Drops leading label tokens (e.g. "No:", "Date:") that bled into the
    extracted line alongside the real value, using the field's own learned
    keywords to recognize them."""
    lower_kw = {k.lower().strip(":#.-") for k in keywords}
    tokens = value.split()
    while tokens and tokens[0].strip(":#.-").lower() in lower_kw:
        tokens.pop(0)
    return " ".join(tokens)


def auto_extract_field(page: dict, pattern: dict, field_name: str) -> str:
    """Projects this field's learned normalized box onto `page`'s own pixel
    dimensions (this document's layout may render at different pixel
    dimensions than the reference invoice), reads the words on the closest
    line inside a tolerance-padded version of it, and falls back to
    keyword-anchored search if that comes up empty. Returns '' when nothing
    can be extracted with reasonable confidence — the review grid is where
    the user fills gaps."""
    field_pattern = pattern.get(field_name)
    words = page.get("words") or []
    if not field_pattern or not words:
        return ""
    width, height = page["width"], page["height"]
    nx1, ny1, nx2, ny2 = field_pattern["box_norm"]
    x1, y1, x2, y2 = nx1 * width, ny1 * height, nx2 * width, ny2 * height
    pad_x = min(40.0, max(10.0, (x2 - x1) * 0.3))
    pad_y = min(10.0, max(4.0, (y2 - y1) * 0.25))

    keywords = field_pattern.get("keywords", [])
    value = _extract_value_near_box(words, x1, y1, x2, y2, pad_x, pad_y)
    value = _strip_leading_keywords(value, keywords)
    if not value:
        value = _extract_by_keyword_anchor(words, keywords)
    return _clean_field_value(field_name, value)


# --------------------------------------------------------------------------
# Batch processing
# --------------------------------------------------------------------------

def process_batch(
    file_list: list[str],
    first_invoice_pattern: dict,
    first_invoice_values: dict | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """Processes every file in file_list. file_list[0] is assumed to be the
    already-annotated reference invoice — if first_invoice_values (the user's
    confirmed {'invoice_number','invoice_date','total_amount'}) is given, it's
    used directly for that file (extracted_by='manual') instead of re-running
    auto-extraction on it. Every other file is auto-extracted with
    first_invoice_pattern. on_progress(done, total, filename), if given, is
    called before each file (and once more at the end) — for a UI progress bar.

    Returns a list of dicts matching the CSV export schema: filename,
    invoice_number, invoice_date, total_amount, extracted_by
    ('manual'|'auto'|'manual_required'), reviewed_by (always None here — set
    later, in the review grid), notes."""
    results = []
    total = len(file_list)
    for idx, path in enumerate(file_list):
        filename = os.path.basename(path)
        if on_progress:
            on_progress(idx, total, filename)

        if idx == 0 and first_invoice_values is not None:
            results.append({
                "filename": filename,
                "invoice_number": first_invoice_values.get("invoice_number", ""),
                "invoice_date": first_invoice_values.get("invoice_date", ""),
                "total_amount": first_invoice_values.get("total_amount", ""),
                "extracted_by": "manual",
                "reviewed_by": None,
                "notes": "",
            })
            continue

        doc = extract_text_from_file(path)
        if not doc.get("pages"):
            results.append({
                "filename": filename, "invoice_number": "", "invoice_date": "", "total_amount": "",
                "extracted_by": "manual_required", "reviewed_by": None, "notes": doc.get("warning") or "Could not read this file.",
            })
            continue

        page = doc["pages"][0]
        results.append(_auto_extract_row(filename, page, first_invoice_pattern, doc.get("warning") or ""))

    if on_progress:
        on_progress(total, total, "Done")
    return results


def _auto_extract_row(display_name: str, page: dict, pattern: dict, warning: str) -> dict:
    row = {
        "filename": display_name,
        "invoice_number": auto_extract_field(page, pattern, "invoice_number"),
        "invoice_date": auto_extract_field(page, pattern, "invoice_date"),
        "total_amount": auto_extract_field(page, pattern, "total_amount"),
        "extracted_by": "auto",
        "reviewed_by": None,
        "notes": warning,
    }
    missing = [f for f in FIELDS if not row[f]]
    if missing:
        row["notes"] = (row["notes"] + f" Could not confidently extract: {', '.join(missing)}.").strip()
    return row


def process_batch_per_page(
    file_list: list[str],
    first_invoice_pattern: dict,
    first_invoice_values: dict | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> list[dict]:
    """"One invoice per page" mode (DataSnipper calls the equivalent toggle
    "Extract per document" vs. per-page) — for a batch that was scanned as one
    or more multi-page PDFs, each holding several invoices back-to-back,
    rather than one file per invoice. Every page of every file in file_list
    becomes its own row (a single-page image file still contributes exactly
    one row, same as process_batch()). The very first PAGE of the very first
    FILE is still the annotated reference invoice — first_invoice_values
    applies to it alone; every other page, including later pages of that same
    first file, is auto-extracted. A multi-page file's rows are labeled
    "filename.pdf (page N)" so they're traceable back to their source; a
    single-page file keeps its plain filename."""
    units: list[tuple[str, dict | None]] = []  # (display_name, page_dict_or_None)
    warnings_by_unit: list[str] = []
    for path in file_list:
        doc = extract_text_from_file(path)
        base = os.path.basename(path)
        pages = doc.get("pages") or []
        if not pages:
            units.append((base, None))
            warnings_by_unit.append(doc.get("warning") or "Could not read this file.")
            continue
        multi = len(pages) > 1
        for i, page in enumerate(pages):
            units.append((f"{base} (page {i + 1})" if multi else base, page))
            warnings_by_unit.append("")

    results = []
    total = len(units)
    for idx, (display_name, page) in enumerate(units):
        if on_progress:
            on_progress(idx, total, display_name)

        if idx == 0 and first_invoice_values is not None:
            results.append({
                "filename": display_name,
                "invoice_number": first_invoice_values.get("invoice_number", ""),
                "invoice_date": first_invoice_values.get("invoice_date", ""),
                "total_amount": first_invoice_values.get("total_amount", ""),
                "extracted_by": "manual",
                "reviewed_by": None,
                "notes": "",
            })
            continue

        if page is None:
            results.append({
                "filename": display_name, "invoice_number": "", "invoice_date": "", "total_amount": "",
                "extracted_by": "manual_required", "reviewed_by": None, "notes": warnings_by_unit[idx],
            })
            continue

        results.append(_auto_extract_row(display_name, page, first_invoice_pattern, warnings_by_unit[idx]))

    if on_progress:
        on_progress(total, total, "Done")
    return results
