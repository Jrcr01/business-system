"""Proper-case creditor names (2026-09-21).

smart_proper_case() only steps in when a name was typed ALL CAPS or all lowercase; a name typed in
mixed case (e.g. "CSS Autoparts Inc.", "CTGlobal Enterprises Corp.") is trusted and left exactly as is.
When it does convert, known acronyms and short consonant-only codes (BDO, CSS, S&P) stay capital.
"""
import re

KEEP_UPPER = {
    "BDO", "BPI", "PNB", "MBTC", "RCBC", "JCB", "USA", "LLC", "II", "III", "IV", "PH", "PHIL", "OK", "ATM", "LPG", "SM", "GT", "GTC", "AMK", "OPC", "YHI", "GHC",
}
SMALL_WORDS = {"and", "of", "the", "for", "de", "dela", "del", "at", "in", "on", "to"}
_VOWELS = set("AEIOU")


def smart_proper_case(name: str) -> str:
    text = (name or "").strip()
    if not text:
        return text
    has_lower = any(c.islower() for c in text)
    has_upper = any(c.isupper() for c in text)
    if has_lower and has_upper:
        return text          # already mixed case -- the user's own spelling wins
    seen = {"first": True}

    def fix(match: re.Match) -> str:
        word = match.group(0)
        upper = word.upper()
        is_first, seen["first"] = seen["first"], False
        if upper in KEEP_UPPER or len(word) == 1:
            return upper
        if len(word) <= 3 and not (set(upper) & _VOWELS):
            return upper     # CSS, LBC ...
        if not is_first and word.lower() in SMALL_WORDS:
            return word.lower()
        # capitalise the first letter; keep letters after an apostrophe lowercase (O'Neil, not O'neil is fine either way)
        return word[0].upper() + word[1:].lower()

    return re.sub(r"[A-Za-z][A-Za-z']*", fix, text)
