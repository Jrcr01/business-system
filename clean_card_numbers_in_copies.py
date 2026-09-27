"""Removes full credit card numbers from the OLD COPIES of the data (2026-09-27, system assessment item #2), cleaning each file in place:
only the card number values change (masked to the last 4 digits, e.g. ****-****-****-4047); everything else in each file stays as it was,
so old backups remain usable. Nothing is deleted. The live database is handled by remove_full_card_numbers.py and is skipped here.

Files covered:
  - Google Drive: the project's data\\ folder (old databases), data\\source\\TblCreditCard.txt (legacy Access export), Cards_to_verify.md,
    and the daily backups in AP_Backups
  - This laptop: the pre-change copies in C:\\AP_Data (not the live database itself)

    python clean_card_numbers_in_copies.py           -> only shows what would change (changes nothing)
    python clean_card_numbers_in_copies.py --apply   -> cleans them

No backup copies are made -- a backup would hold the very numbers being removed. Google Drive keeps earlier versions of a changed file
for about 30 days (version history); those age out on their own.
"""
import glob
import os
import re
import sqlite3
import sys

_args = sys.argv[1:]
# --root <project folder> / --local <local data folder>: only for testing this script on copies
HERE = _args[_args.index("--root") + 1] if "--root" in _args else os.path.dirname(os.path.abspath(__file__))
LOCAL = _args[_args.index("--local") + 1] if "--local" in _args else r"C:\AP_Data"
LIVE = os.path.normcase(os.path.abspath(os.path.join(LOCAL, "ap_disbursement.db")))
apply = "--apply" in _args

DB_FILES = (sorted(glob.glob(os.path.join(HERE, "data", "*.db*")))
            + sorted(glob.glob(os.path.join(os.path.dirname(HERE), "AP_Backups", "*.db")))
            + sorted(glob.glob(os.path.join(LOCAL, "ap_disbursement.db.*"))))
TEXT_FILES = [os.path.join(HERE, "data", "source", "TblCreditCard.txt"), os.path.join(HERE, "Cards_to_verify.md")]

CARD_TEXT = re.compile(r"(?<![\d-])(\d{4})-?(\d{4})-?(\d{4})-?(\d{3,4})(?![\d-])")


def is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def masked(num: str) -> str:
    return "****-****-****-" + digits(num)[-4:]


# ---- 1. every full card number known anywhere (the live database may already be clean, so gather them from the copies too)
known: set[str] = set()
db_files = [p for p in DB_FILES if os.path.normcase(os.path.abspath(p)) != LIVE and os.path.isfile(p) and is_sqlite(p)]
for p in db_files + [os.path.join(LOCAL, "ap_disbursement.db")]:
    try:
        c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        if "card_number_full" in [r[1] for r in c.execute("PRAGMA table_info(credit_cards)")]:
            known |= {digits(v) for (v,) in c.execute("SELECT card_number_full FROM credit_cards WHERE card_number_full IS NOT NULL") if len(digits(v)) >= 15}
        c.close()
    except sqlite3.Error:
        pass
for p in TEXT_FILES:
    if os.path.isfile(p):
        known |= {"".join(m.groups()) for m in CARD_TEXT.finditer(open(p, encoding="utf-8", errors="replace").read())}
forms = {}                                                     # every way a known number may be written -> masked form
for k in known:
    forms[k] = masked(k)
    if len(k) == 16:
        forms["-".join([k[:4], k[4:8], k[8:12], k[12:]])] = masked(k)
        forms[" ".join([k[:4], k[4:8], k[8:12], k[12:]])] = masked(k)
pattern = re.compile("|".join(sorted(map(re.escape, forms), key=len, reverse=True))) if forms else None
print(f"Distinct full card numbers found across all copies: {len(known)}")


def mask_text(text: str) -> tuple[str, int]:
    if not pattern or not isinstance(text, str):
        return text, 0
    return pattern.subn(lambda m: forms[m.group(0)], text)


# ---- 2. databases
total_db, changed_db = 0, 0
for p in db_files:
    conn = sqlite3.connect(p)
    hits = 0
    try:
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            for col in cols:
                rows = conn.execute(f'SELECT rowid, "{col}" FROM "{t}" WHERE typeof("{col}") = \'text\'').fetchall()
                for rid, val in rows:
                    new, n = mask_text(val)
                    if col == "card_number_full" and val and digits(val) in known:
                        new, n = None, max(n, 1)
                    if n:
                        hits += n
                        if apply:
                            conn.execute(f'UPDATE "{t}" SET "{col}" = ? WHERE rowid = ?', (new, rid))
        if apply and hits:
            conn.commit()
            conn.execute("VACUUM")                             # also wipes the old values left in free pages
        total_db += hits
        changed_db += 1 if hits else 0
        if hits:
            print(f"  {'cleaned' if apply else 'would clean'}: {hits:>3} value(s)  {p}")
    except sqlite3.Error as e:
        print(f"  !! skipped {p}: {e}")
    finally:
        conn.close()

# ---- 3. text files (keep their encoding and line endings)
changed_txt = 0
for p in TEXT_FILES:
    if not os.path.isfile(p):
        continue
    raw = open(p, "rb").read()
    text = raw.decode("utf-8")
    new, n = mask_text(text)
    if n:
        changed_txt += 1
        print(f"  {'cleaned' if apply else 'would clean'}: {n:>3} value(s)  {p}")
        if apply:
            open(p, "wb").write(new.encode("utf-8"))

print(f"\n{changed_db} database file(s) and {changed_txt} text file(s) {'cleaned' if apply else 'hold full card numbers'}.")
if not apply:
    print("Nothing changed. Run again with --apply to clean them.")
