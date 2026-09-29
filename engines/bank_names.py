"""Full names for the banks the system knows by abbreviation (2026-09-21).

Bank loans and credit cards used to be filed under the short codes from the old Access data (BDO,
BPI, PNB ...). Everything now uses the full name. expand_bank_name() is used wherever a bank name is
typed or created, so typing "BDO" still lands on "BDO Unibank, Inc." instead of making a second record.
"""
import re

BANK_FULL_NAMES = {
    "BDO": "BDO Unibank, Inc.",
    "BPI": "Bank of the Philippine Islands",
    "PNB": "Philippine National Bank",
    "MBTC": "Metropolitan Bank & Trust Company",
    "CB": "China Banking Corporation",
    "EW": "East West Banking Corporation",
    "SB": "Security Bank Corporation",
    "UB": "Union Bank of the Philippines",
    "BC": "Bank of Commerce",
    "RCBC": "Rizal Commercial Banking Corporation",
}


def expand_bank_name(name: str) -> str:
    """The full name if `name` is one of the known abbreviations (any capitalisation), else unchanged."""
    text = (name or "").strip()
    return BANK_FULL_NAMES.get(text.upper(), text)


# ---- short names for lists and warnings (2026-09-29, owner: "yes i need short names ... include bank and card name") ----
# The family's own codes -- the same ones already used in card names ("MBTC Mastercard", "EW Mastercard", "SB Mastercard").
SHORT_BANK_NAMES = {full.lower(): code for code, full in BANK_FULL_NAMES.items()}
_TRAILING = re.compile(r",?\s+(Inc\.?|Corp\.?|Corporation|Company|Co\.)$", re.I)
_BANKING = re.compile(r"\s+Banking$", re.I)


def short_bank_name(name) -> str:
    """'Bank of the Philippine Islands' -> 'BPI'. A bank we have no code for is tidied ('Acme Banking Corporation' -> 'Acme'), never lost."""
    text = (name or "").strip()
    if not text:
        return ""
    known = SHORT_BANK_NAMES.get(text.lower())
    if known:
        return known
    text = _BANKING.sub("", _TRAILING.sub("", text)).strip()
    return text


def card_display_name(issuer, card_name) -> str:
    """Bank + card as the owner reads it: 'BPI Mastercard Plat Rewards'. The bank is not repeated when the card name already starts with
    it ('BPI Mastercard', 'MBTC Mastercard', 'RCBC JCB')."""
    card = (card_name or "").strip()
    bank = short_bank_name(issuer)
    if not bank or card.upper() == bank.upper() or card.upper().startswith(bank.upper() + " "):
        return card or bank
    return f"{bank} {card}".strip()
