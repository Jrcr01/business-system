"""Full names for the banks the system knows by abbreviation (2026-09-21).

Bank loans and credit cards used to be filed under the short codes from the old Access data (BDO,
BPI, PNB ...). Everything now uses the full name. expand_bank_name() is used wherever a bank name is
typed or created, so typing "BDO" still lands on "BDO Unibank, Inc." instead of making a second record.
"""

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
