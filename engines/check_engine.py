import io
from datetime import datetime
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
DEFAULT_BRANCH_CODE = "399"

# Defaults match the pre-calibration guess (8in x 3in, no shift) -- overridden per bank account
# via bank_accounts.check_width_mm/check_height_mm/check_offset_x_mm/check_offset_y_mm
# (migration 019). No physical Metrobank check leaf has been measured yet, so these exist to be
# tuned after a real test print rather than guessed correctly up front -- mirrors Chrysanth Check
# Writer's own per-account "Shift Right"/"Shift Down" Alignment tab.
DEFAULT_CHECK_WIDTH_MM = 203.2
DEFAULT_CHECK_HEIGHT_MM = 76.2

_ONES = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN",
         "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN", "SEVENTEEN",
         "EIGHTEEN", "NINETEEN"]
_TENS = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]
_SCALES = ["", "THOUSAND", "MILLION", "BILLION", "TRILLION"]


def format_metrobank_check_number(raw_sequence: int, branch_code: str | None = None) -> str:
    """The full check number for a leaf: the check account's prefix and digits (Settings > Bank accounts; Metrobank: 399 + 7 digits)."""
    from engines.bank_accounts import check_account, format_number
    a = check_account()
    if branch_code is not None and branch_code != a["check_prefix"]:
        return f"{branch_code}{str(raw_sequence).zfill(int(a['check_digits']))}"
    return format_number(a, int(raw_sequence))


def _three_digit_words(n: int) -> str:
    """n in 0..999. US style (no "and" before tens/units) -- matches this account's current
    Chrysanth Locale tab setting ("US Style": "One Hundred Twenty", not "One Hundred and Twenty")."""
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} HUNDRED")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10])
        if n % 10:
            parts.append(_ONES[n % 10])
    elif n > 0:
        parts.append(_ONES[n])
    return " ".join(parts)


def _int_to_words(n: int) -> str:
    if n == 0:
        return "ZERO"
    groups = []
    scale_idx = 0
    while n > 0:
        n, rem = divmod(n, 1000)
        if rem:
            groups.append(f"{_three_digit_words(rem)} {_SCALES[scale_idx]}".strip())
        scale_idx += 1
    return " ".join(reversed(groups))


def amount_to_words(amount: float) -> str:
    """Legal-amount line for a check face: no currency word (this account's real Chrysanth
    sample omits "PESOS" entirely), always uppercase, "AND {cents}/100" only when there are
    centavos, "ONLY" always last -- e.g. 10000.0 -> "TEN THOUSAND ONLY",
    1166866.67 -> "ONE MILLION ONE HUNDRED SIXTY SIX THOUSAND EIGHT HUNDRED SIXTY SIX AND 67/100 ONLY"."""
    pesos = int(amount)
    centavos = int(round((amount - pesos) * 100))
    if centavos == 100:
        pesos += 1
        centavos = 0
    words = _int_to_words(pesos)
    return f"{words} AND {centavos}/100 ONLY" if centavos > 0 else f"{words} ONLY"


def _fit_font_size(c: canvas.Canvas, text: str, font_name: str, max_size: float, max_width: float, min_size: float = 5.0) -> float:
    """Shrinks font size until text fits max_width (down to min_size) -- a long supplier name or
    a many-digit amount-in-words line must never run into whatever sits next to it."""
    size = max_size
    while size > min_size and c.stringWidth(text, font_name, size) > max_width:
        size -= 0.5
    return size


def generate_metrobank_check_pdf(
    payee_name: str,
    amount: float,
    check_date: datetime,
    check_width_mm: float = DEFAULT_CHECK_WIDTH_MM,
    check_height_mm: float = DEFAULT_CHECK_HEIGHT_MM,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> bytes:
    """Generates a check PDF overlay for blank Metrobank Account 1 check stock: date, payee,
    amount numeral, and amount-in-words only. No check number (pre-printed on the physical leaf,
    per this app's own checkbook-leaf-range model), no MICR line, and no "A/C PAYEE ONLY"
    crossing -- all three are pre-printed on the real check stock, not something this software
    draws. check_width_mm/check_height_mm/offset_x_mm/offset_y_mm are this bank account's
    calibration (bank_accounts, migration 019) -- tune them after a real test print.

    Amount numeral and amount-in-words sit on separate rows (not sharing a baseline) -- a long
    payee name or a many-digit amount-in-words line (e.g. a 7-figure amount) would otherwise run
    into whatever it shares a row with; both also auto-shrink to fit rather than overflowing."""
    width = check_width_mm * mm
    height = check_height_mm * mm
    dx = offset_x_mm * mm
    dy = offset_y_mm * mm
    left_margin = 26.46 * mm
    right_edge = width - 10 * mm

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))

    # Date: individually spaced digit boxes, MM DD YYYY, with a wider gap between the three
    # groups -- matches the real Chrysanth sample's date box style.
    date_str = check_date.strftime("%m%d%Y")
    c.setFont("Courier-Bold", 11)
    start_x, date_y, spacing = 140.4 * mm, height - 12 * mm, 4.76 * mm
    for idx, char in enumerate(date_str):
        gap = (1.4 * mm if idx >= 2 else 0) + (1.4 * mm if idx >= 4 else 0)
        c.drawString(dx + start_x + (idx * spacing) + gap, dy + date_y, char)

    # Amount numeral: its own row below the date, right-aligned to the same margin used for the
    # date block. Plain, no currency symbol (pre-printed on the stock), no asterisks.
    amount_text = f"{amount:,.2f}"
    amount_font_size = _fit_font_size(c, amount_text, "Helvetica-Bold", 11, right_edge - 100 * mm)
    c.setFont("Helvetica-Bold", amount_font_size)
    c.drawRightString(dx + right_edge, dy + height - 22 * mm, amount_text)

    # Payee: its own row, plain text, natural case (the sample's "Acom Trading" is not
    # force-capitalized -- only the amount wording gets the "Print in Capital Letters" treatment).
    payee_font_size = _fit_font_size(c, payee_name, "Helvetica-Bold", 10, right_edge - left_margin)
    c.setFont("Helvetica-Bold", payee_font_size)
    c.drawString(dx + left_margin, dy + height - 30 * mm, payee_name)

    # Amount in words: its own row below the payee, full width available, always uppercase, no
    # "PESOS" word.
    words_text = amount_to_words(amount)
    words_font_size = _fit_font_size(c, words_text, "Helvetica", 9, right_edge - left_margin)
    c.setFont("Helvetica", words_font_size)
    c.drawString(dx + left_margin, dy + height - 38 * mm, words_text)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
