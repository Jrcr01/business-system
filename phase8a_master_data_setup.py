"""Phase 8a: Master Data Setup (Test).

One-time migration script that reads the hand-tagged export of the old Access payee list
(data/source/TblPayee-tagged.csv, 157 raw payees classified as Supplier / Lender / Bank Loan /
Internal Account / a few odd categories), plus the real credit card data
(TblCreditCardHolder.txt, TblCreditCard.txt, TblBank.txt), and builds a fresh TEST database
(data/ap_disbursement_TEST.db) populated with clean, resolved master data.

Deliberately conservative, per the Phase 8a spec: no historical invoices/checks/balances/credit
card statements are migrated, no numbers are invented for lenders, bank loans, or credit cards
that don't have them on file yet (those come in "Pending Setup" / has_amortization=0 /
credit_limit=NULL, for the user to complete later), and nothing here ever touches the live
data/ap_disbursement.db. Run directly:

    python phase8a_master_data_setup.py

to (re)build data/ap_disbursement_TEST.db from schema.sql and write report_8a.txt. Every function
below is also unit-tested individually in tests/test_8a_master_data_setup.py.
"""
import csv
import difflib
import os
import re
import sqlite3
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "schema.sql")
TAGGED_CSV_PATH = os.path.join(PROJECT_ROOT, "data", "source", "TblPayee-tagged.csv")
CC_HOLDERS_PATH = os.path.join(PROJECT_ROOT, "data", "source", "TblCreditCardHolder.txt")
CC_CARDS_PATH = os.path.join(PROJECT_ROOT, "data", "source", "TblCreditCard.txt")
BANK_LOOKUP_PATH = os.path.join(PROJECT_ROOT, "data", "source", "TblBank.txt")
TEST_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ap_disbursement_TEST.db")
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8a.txt")

# Categories that fold into SUPPLIER: all four have real order/check history in TblOrder.txt
# (Accountant 12 orders, Insurance 3, Consultant 1, Misc 0) -- they were paid through the exact
# same statement/check pipeline as suppliers, and creditors.creditor_type only recognizes
# SUPPLIER/LENDER/BANK_LOAN/CREDIT_CARD, so there's no dedicated bucket for them.
SUPPLIER_LIKE_CLASSIFICATIONS = ("Supplier", "Accountant", "Insurance", "Consultant", "Misc")

# Internal control accounts to keep -- "NOT YET USED (FOR CONTROL ONLY)" (legacy payee 76) is
# deliberately excluded: it has zero order history and isn't one of the "standard 5".
INTERNAL_ACCOUNT_EXCLUDE_MARKER = "NOT YET USED"

# Bank-loan payees whose name never resolves to a known bank code (extract_bank_name falls back
# to the raw name), but that the user has explicitly confirmed really are bank loans -- kept as
# their own standalone creditor, same as any other unresolved name, just no longer flagged for
# review in the report. PayeeId 138 "PN8004 7053 5001 8969" and 154 "HONDA GIORNO" confirmed
# 2026-09-15.
CONFIRMED_UNRESOLVED_BANK_LOAN_PAYEE_IDS = {138, 154}

# Known bank codes, checked as whole tokens (see extract_bank_name) so e.g. "CB_618000022369"
# and "LOAN - BDO Housing_005660136452" both resolve correctly regardless of separator style.
_BANK_TOKEN_MAP = {
    "BDO": "BDO",
    "BPI": "BPI",
    "PNB": "PNB",
    "MBTC": "MBTC",
    "METROBANK": "MBTC",
    "CHINA": "CB",
    "CB": "CB",
    "EW": "EW",
    "EASTWEST": "EW",
}


def parse_payee_tagged_csv(path: str = TAGGED_CSV_PATH):
    """Loads the hand-tagged payee CSV into a list of dicts (one per payee), trimmed and with a
    resolved `full_name`: FirstName+MiddleInitial+LastName+Suffix for an individual (LastName
    present), or just FirstName for a company/description-style row (LastName blank) -- the CSV
    uses FirstName to hold the whole name in that case (e.g. "Acom Trading")."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for raw in csv.DictReader(f):
            row = {k: (v or "").strip() for k, v in raw.items()}
            if not row.get("PayeeId"):
                continue
            row["PayeeId"] = int(row["PayeeId"])
            if row["LastName"]:
                parts = [row["FirstName"], row["MiddleInitial"], row["LastName"], row["PayeeSuffix"]]
                row["full_name"] = " ".join(p for p in parts if p)
            else:
                row["full_name"] = row["FirstName"]
            rows.append(row)
    return rows


def validate_payee_data(rows) -> dict:
    """Flags data-quality issues without fixing anything (Phase 8a explicitly leaves dedup/merge
    decisions to the user). `duplicates`: near-identical Supplier-like names (difflib ratio >
    0.85). `unclassified`: rows with no Classification tag at all."""
    supplier_like = [r for r in rows if r["Classification"] in SUPPLIER_LIKE_CLASSIFICATIONS]
    duplicates = []
    for i in range(len(supplier_like)):
        for j in range(i + 1, len(supplier_like)):
            a, b = supplier_like[i], supplier_like[j]
            ratio = difflib.SequenceMatcher(None, a["full_name"].lower(), b["full_name"].lower()).ratio()
            if ratio > 0.85:
                duplicates.append({"a": a, "b": b, "similarity": ratio})
    unclassified = [r for r in rows if not r["Classification"]]
    return {"duplicates": duplicates, "unclassified": unclassified}


def categorize_and_resolve_payees(rows, existing_map: dict | None = None) -> dict:
    """Buckets payees by resolved category. `existing_map` (legacy_payee_id -> creditor_id, or
    similar) lets a re-run skip payees already migrated; empty/omitted for a first run."""
    existing_map = existing_map or {}

    def _bucket(predicate):
        return [r for r in rows if predicate(r) and r["PayeeId"] not in existing_map]

    return {
        "suppliers": _bucket(lambda r: r["Classification"] in SUPPLIER_LIKE_CLASSIFICATIONS),
        "lenders": _bucket(lambda r: r["Classification"] == "Lender"),
        "bank_loans": _bucket(lambda r: r["Classification"] == "Bank Loan"),
        "internal_accounts": _bucket(
            lambda r: r["Classification"] == "Internal Account"
            and INTERNAL_ACCOUNT_EXCLUDE_MARKER not in r["full_name"].upper()
        ),
        "unclassified": _bucket(lambda r: not r["Classification"]),
    }


def extract_bank_name(raw_name: str) -> str:
    """Matches whole tokens (split on any non-alphanumeric, so both "BDO Housing_005660136452"
    and "CB_618000022369" tokenize cleanly) against known bank codes. Falls back to the raw name
    unchanged when nothing matches (e.g. "HONDA GIORNO", the bare card-number-looking name) --
    those are flagged separately by the caller as needing manual bank verification, never
    guessed."""
    tokens = re.findall(r"[A-Za-z0-9]+", raw_name.upper())
    for tok in tokens:
        if tok in _BANK_TOKEN_MAP:
            return _BANK_TOKEN_MAP[tok]
    return raw_name.strip()


def bank_name_was_resolved(raw_name: str) -> bool:
    """True if extract_bank_name matched a known bank code rather than falling back to the raw
    name -- used by the report to list which bank-loan rows need manual verification."""
    tokens = re.findall(r"[A-Za-z0-9]+", raw_name.upper())
    return any(tok in _BANK_TOKEN_MAP for tok in tokens)


# First-name-only patterns in bank-loan payee names that map to a specific family member --
# confirmed via cross-reference with the same person appearing elsewhere in this dataset with a
# full name (e.g. "Emelita Roxas" as both a real Lender, payee 126, and a TblCreditCardHolder).
# "RAP" (payee 162, "Every 11th - RAP BPI") is user-confirmed as Julian Ralph Roxas (a real
# TblCreditCardHolder, id 5) -- same cross-reference pattern as EMELITA.
# Extend this map if more such patterns turn up in the source data.
_BENEFICIARY_NAME_MAP = {
    "EMELITA": "Emelita Roxas",
    "RAP": "Julian Ralph Roxas",
}

# Direct payee_id -> beneficiary overrides for bank loans whose name has no textual pattern to
# match at all (e.g. "HONDA GIORNO" doesn't encode a name the way "BPI/EMELITA" does) --
# user-confirmed assignments, checked before/instead of the text-pattern matching above.
_BENEFICIARY_BY_PAYEE_ID = {
    154: "Rhodora Roxas",  # HONDA GIORNO
}


def _cardholder_first_name_map() -> dict:
    """Maps each real cardholder's first name (from TblCreditCardHolder.txt, upper-cased) to
    their canonical "First Last" name -- used to normalize a beneficiary name extracted from
    bank-loan payee text (e.g. "Jessie G. Roxas", with a middle initial the source happened to
    include) to match the exact same person's name everywhere else in the system, per the
    user's "infer from these [cardholders]" instruction, rather than keeping whatever
    formatting variation happened to be in that one payee string."""
    mapping = {}
    for h in parse_credit_card_holders():
        first_token = h["full_name"].split()[0].upper()
        mapping[first_token] = h["full_name"]
    return mapping


def extract_beneficiary_name(raw_name: str) -> str | None:
    """Some bank-loan payee names encode who the loan is attributable to -- e.g.
    "LOAN - BPI/EMELITA 30TH OF EVERY MONTH" or "PNB Credit Cards FAO: Jessie G. Roxas" (FAO =
    "for account of"). Returns the beneficiary's display name if recognized, else None.
    Deliberately conservative: no guessing from ambiguous abbreviations without corroborating
    evidence (e.g. "RAP"/"EMELITA" are only recognized because the user separately confirmed
    who they are, cross-referenced against the real TblCreditCardHolder registry)."""
    m = re.search(r"FAO:?\s*([A-Za-z.\s]+?)\s*$", raw_name, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        # "FAO 1234567812345678" is an account number, not a name -- must not match.
        if candidate and not any(ch.isdigit() for ch in candidate):
            first_token = candidate.split()[0].upper()
            return _cardholder_first_name_map().get(first_token, candidate)
    upper = raw_name.upper()
    for token, full_name in _BENEFICIARY_NAME_MAP.items():
        if token in upper:
            return full_name
    return None


def get_or_create_beneficiary(conn: sqlite3.Connection, name: str, cache: dict) -> int:
    """Returns the loan_beneficiaries.beneficiary_id for `name`, creating it (type='Person') on
    first use. `cache` is a plain dict the caller keeps across calls so the same beneficiary
    name only ever gets one row, no matter how many bank loans reference it."""
    if name in cache:
        return cache[name]
    cur = conn.execute(
        "INSERT INTO loan_beneficiaries (name, beneficiary_type) VALUES (?, 'Person')",
        (name,),
    )
    cache[name] = cur.lastrowid
    return cache[name]


def _fetch_dict(conn: sqlite3.Connection, sql: str, params: tuple) -> dict:
    """Runs a single-row SELECT and returns it as a plain dict keyed by column name, regardless
    of the connection's row_factory setting (works whether the caller uses sqlite3.Row, the
    default tuple factory, or anything else)."""
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    return dict(zip(cols, row))


def create_lender_with_placeholder(conn: sqlite3.Connection, payee: dict) -> dict:
    """Inserts a LENDER creditor plus a loan_notes row with no invented numbers: status
    'Pending Setup', principal_amount/monthly_interest_rate/total_payable all NULL (migration
    032 made these nullable specifically for this). Returns the inserted loan_notes row."""
    cur = conn.execute(
        "INSERT INTO creditors (creditor_name, creditor_type, contact_number, email, legacy_payee_id) "
        "VALUES (?, 'LENDER', ?, ?, ?)",
        (payee["full_name"], payee.get("ContactNo") or None, payee.get("Email") or None, payee["PayeeId"]),
    )
    creditor_id = cur.lastrowid
    note_ref = f"PENDING-{payee['PayeeId']}"
    cur2 = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, "
        "monthly_interest_rate, total_payable, status) "
        "VALUES (?, ?, ?, NULL, NULL, NULL, 'Pending Setup')",
        (creditor_id, note_ref, date.today().isoformat()),
    )
    return _fetch_dict(conn, "SELECT * FROM loan_notes WHERE loan_note_id = ?", (cur2.lastrowid,))


def create_bank_loan_placeholder(
    conn: sqlite3.Connection, payee: dict, creditor_id: int | None = None, beneficiary_id: int | None = None
) -> dict:
    """Inserts a bank_loans placeholder row (has_amortization=0, no invented dates/principal --
    migration 033 made those nullable). If `creditor_id` is given, the new row is attached to
    that existing BANK_LOAN creditor (the real-world shape: one bank can have several loan
    facilities/legacy payee rows, e.g. BDO has 6 in this dataset); otherwise a new BANK_LOAN
    creditor is created for the extracted bank name. `beneficiary_id` (see
    extract_beneficiary_name/get_or_create_beneficiary) records who the loan is attributable to,
    when the legacy name encodes that (e.g. "LOAN - BPI/EMELITA ..."). Returns the inserted
    bank_loans row (with creditor_id included)."""
    if creditor_id is None:
        bank_name = extract_bank_name(payee["full_name"])
        cur = conn.execute(
            "INSERT INTO creditors (creditor_name, creditor_type, legacy_payee_id) VALUES (?, 'BANK_LOAN', ?)",
            (bank_name, payee["PayeeId"]),
        )
        creditor_id = cur.lastrowid
    loan_ref = f"PENDING-{payee['PayeeId']}"
    cur2 = conn.execute(
        "INSERT INTO bank_loans (creditor_id, beneficiary_id, loan_ref, principal_amount, has_amortization, status) "
        "VALUES (?, ?, ?, NULL, 0, 'Active')",
        (creditor_id, beneficiary_id, loan_ref),
    )
    return _fetch_dict(conn, "SELECT * FROM bank_loans WHERE bank_loan_id = ?", (cur2.lastrowid,))


def create_internal_accounts(conn: sqlite3.Connection, payees: list) -> list[int]:
    """Inserts the standard internal control accounts (Cash, Error Checks, Encashment,
    Adjustment, Cancelled) into creditor_master_internal -- not creditors, since these aren't
    real payees."""
    ids = []
    for p in payees:
        cur = conn.execute(
            "INSERT INTO creditor_master_internal (name, type, legacy_payee_id) VALUES (?, ?, ?)",
            (p["full_name"], p["Classification"], p["PayeeId"]),
        )
        ids.append(cur.lastrowid)
    return ids


def create_suppliers(conn: sqlite3.Connection, payees: list) -> list[int]:
    ids = []
    for p in payees:
        cur = conn.execute(
            "INSERT INTO creditors (creditor_name, creditor_type, contact_number, email, legacy_payee_id) "
            "VALUES (?, 'SUPPLIER', ?, ?, ?)",
            (p["full_name"], p.get("ContactNo") or None, p.get("Email") or None, p["PayeeId"]),
        )
        ids.append(cur.lastrowid)
    return ids


def setup_system_settings(conn: sqlite3.Connection) -> None:
    """Seeds the settings table with what's actually known: the primary bank (Metrobank, matches
    schema.sql's own bank_accounts seed row) and the standard creditor-terms picklist, plus a
    couple of migration-provenance rows."""
    seed = [
        ("bank_name", "Metrobank", "general", "Primary bank display name"),
        ("default_terms_picklist", "COD,30,60,90,120", "creditors", "Comma-separated terms offered when adding a creditor"),
        ("primary_bank_account_id", "1", "general", "bank_accounts.bank_account_id for the primary account"),
        ("legacy_source", "Access TblPayee.txt export", "migration", "Origin of Phase 8a migrated master data"),
        ("phase8a_migration_date", date.today().isoformat(), "migration", "Date Phase 8a master data setup was run"),
    ]
    for key, value, category, description in seed:
        conn.execute(
            "INSERT INTO settings (key, value, category, description) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value, category, description),
        )


def parse_bank_lookup(path: str = BANK_LOOKUP_PATH) -> dict:
    """Loads the Access TblBank code lookup (legacy_bank_id -> {'short': 'BDO', 'full': 'Banco
    De Oro'}). Needed to resolve TblCreditCard's numeric bank-code column, which has no meaning
    on its own."""
    lookup = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            legacy_id = int(row[0])
            lookup[legacy_id] = {"short": row[1].strip(), "full": row[2].strip()}
    return lookup


def parse_credit_card_holders(path: str = CC_HOLDERS_PATH) -> list:
    """Loads the Access TblCreditCardHolder export (legacy_id, first name, last name -- no
    header row)."""
    holders = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            legacy_id = int(row[0])
            full_name = " ".join(p.strip() for p in row[1:3] if p.strip())
            holders.append({"legacy_cardholder_id": legacy_id, "full_name": full_name})
    return holders


def parse_credit_cards(path: str = CC_CARDS_PATH) -> list:
    """Loads the Access TblCreditCard export: legacy_card_id, legacy_cardholder_id, bank_code,
    card_number (full, dashes optional), network, is_active, nickname (often blank, and the
    trailing column is sometimes omitted entirely for a fully-blank nickname)."""
    cards = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            row = row + [""] * (7 - len(row))
            card_number_full = row[3].strip()
            cards.append({
                "legacy_card_id": int(row[0]),
                "legacy_cardholder_id": int(row[1]),
                "bank_code": int(row[2]),
                "card_number_full": card_number_full,
                "card_number_last4": re.sub(r"\D", "", card_number_full)[-4:],
                "network": row[4].strip() or None,
                "is_active": 1 if row[5].strip() == "1" else 0,
                "nickname": row[6].strip() or None,
            })
    return cards


def create_cardholders_from_legacy(conn: sqlite3.Connection, holders: list) -> dict:
    """Inserts real cardholders from the legacy export. Returns {legacy_cardholder_id:
    cardholder_id} so create_credit_card_with_bank() can link each card to the right one."""
    id_map = {}
    for h in holders:
        cur = conn.execute(
            "INSERT INTO cardholders (name, legacy_cardholder_id) VALUES (?, ?)",
            (h["full_name"], h["legacy_cardholder_id"]),
        )
        id_map[h["legacy_cardholder_id"]] = cur.lastrowid
    return id_map


def create_credit_card_with_bank(
    conn: sqlite3.Connection, card: dict, cardholder_id_map: dict, bank_creditor_by_code: dict, bank_lookup: dict
) -> dict:
    """Inserts one credit_cards row, creating (or reusing) a CREDIT_CARD creditor for its
    issuing bank -- same one-creditor-per-issuer grouping already used for bank loan
    placeholders, since one bank can issue several family members' cards (e.g. Mastercard,
    Visa, and JCB cards can all come from the same bank). credit_limit is left NULL -- the
    legacy export never tracked it, and inventing one would break the same "no invented
    numbers" rule the lender/bank-loan placeholders follow. card_number_full is preserved
    separately from card_number_last4 (used everywhere else in the UI) per the real need for
    it: a reference when paying the bill through a banking app."""
    bank_code = card["bank_code"]
    creditor_id = bank_creditor_by_code.get(bank_code)
    bank_short = bank_lookup.get(bank_code, {}).get("short") or f"Bank #{bank_code}"
    if creditor_id is None:
        cur = conn.execute(
            "INSERT INTO creditors (creditor_name, creditor_type) VALUES (?, 'CREDIT_CARD')",
            (bank_short,),
        )
        creditor_id = cur.lastrowid
        bank_creditor_by_code[bank_code] = creditor_id

    if card["nickname"]:
        card_name = card["nickname"]
    elif card["network"]:
        card_name = f"{bank_short} {card['network']}"
    else:
        card_name = f"{bank_short} Card"

    cur2 = conn.execute(
        "INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, "
        "card_number_full, network, credit_limit, is_active, legacy_card_id) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
        (
            creditor_id, cardholder_id_map.get(card["legacy_cardholder_id"]), card_name,
            card["card_number_last4"], card["card_number_full"], card["network"],
            card["is_active"], card["legacy_card_id"],
        ),
    )
    return _fetch_dict(conn, "SELECT * FROM credit_cards WHERE card_id = ?", (cur2.lastrowid,))


def validate_master_data_integrity(conn: sqlite3.Connection) -> dict:
    """Sanity-checks the migrated data: no NULL/blank names or ids, every LENDER creditor has a
    loan_notes row, every BANK_LOAN creditor has at least one bank_loans row, the internal
    accounts table has exactly the standard 5."""
    issues = []

    for name, in conn.execute("SELECT creditor_name FROM creditors WHERE creditor_name IS NULL OR TRIM(creditor_name) = ''"):
        issues.append(f"Creditor with blank name: {name!r}")

    lenders_missing_notes = conn.execute(
        "SELECT c.creditor_id, c.creditor_name FROM creditors c "
        "WHERE c.creditor_type = 'LENDER' "
        "AND NOT EXISTS (SELECT 1 FROM loan_notes ln WHERE ln.creditor_id = c.creditor_id)"
    ).fetchall()
    for creditor_id, name in lenders_missing_notes:
        issues.append(f"Lender {name!r} (creditor_id={creditor_id}) has no loan_notes row")

    bank_loans_missing = conn.execute(
        "SELECT c.creditor_id, c.creditor_name FROM creditors c "
        "WHERE c.creditor_type = 'BANK_LOAN' "
        "AND NOT EXISTS (SELECT 1 FROM bank_loans bl WHERE bl.creditor_id = c.creditor_id)"
    ).fetchall()
    for creditor_id, name in bank_loans_missing:
        issues.append(f"Bank-loan creditor {name!r} (creditor_id={creditor_id}) has no bank_loans row")

    internal_count = conn.execute("SELECT COUNT(*) FROM creditor_master_internal").fetchone()[0]
    if internal_count != 5:
        issues.append(f"Expected 5 internal accounts, found {internal_count}")

    for card_id, in conn.execute("SELECT card_id FROM credit_cards WHERE card_name IS NULL OR TRIM(card_name) = ''"):
        issues.append(f"Credit card {card_id} has a blank name")
    for card_id, in conn.execute("SELECT card_id FROM credit_cards WHERE card_number_last4 IS NULL OR TRIM(card_number_last4) = ''"):
        issues.append(f"Credit card {card_id} has no last4")
    cards_missing_creditor = conn.execute(
        "SELECT cc.card_id FROM credit_cards cc "
        "JOIN creditors c ON cc.creditor_id = c.creditor_id "
        "WHERE c.creditor_type != 'CREDIT_CARD'"
    ).fetchall()
    for card_id, in cards_missing_creditor:
        issues.append(f"Credit card {card_id} is not linked to a CREDIT_CARD-type creditor")

    return {"validation_passed": len(issues) == 0, "issues": issues}


def build_test_database(db_path: str = TEST_DB_PATH) -> None:
    """(Re)builds a fresh SQLite database at db_path from schema.sql. Never touches the live
    data/ap_disbursement.db -- db_path defaults to the TEST db and every caller in this script
    uses that default."""
    if os.path.exists(db_path):
        os.remove(db_path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema_sql = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()


def run_migration(conn: sqlite3.Connection, rows=None) -> dict:
    """Runs the full Phase 8a import against an already-open connection (a fresh schema.sql
    build, or a test fixture's db). Returns a summary dict used both by main() to write
    report_8a.txt and by tests that want the end-to-end result without re-parsing everything."""
    rows = rows if rows is not None else parse_payee_tagged_csv()
    validation = validate_payee_data(rows)
    buckets = categorize_and_resolve_payees(rows)

    supplier_ids = create_suppliers(conn, buckets["suppliers"])

    lender_notes = [create_lender_with_placeholder(conn, p) for p in buckets["lenders"]]

    bank_loan_rows = []
    bank_creditor_by_name: dict[str, int] = {}
    beneficiary_cache: dict[str, int] = {}
    unresolved_bank_names = []
    for p in buckets["bank_loans"]:
        if not bank_name_was_resolved(p["full_name"]):
            unresolved_bank_names.append(p)
        bank_name = extract_bank_name(p["full_name"])
        creditor_id = bank_creditor_by_name.get(bank_name)
        beneficiary_name = _BENEFICIARY_BY_PAYEE_ID.get(p["PayeeId"]) or extract_beneficiary_name(p["full_name"])
        beneficiary_id = get_or_create_beneficiary(conn, beneficiary_name, beneficiary_cache) if beneficiary_name else None
        row = create_bank_loan_placeholder(conn, p, creditor_id=creditor_id, beneficiary_id=beneficiary_id)
        bank_creditor_by_name.setdefault(bank_name, row["creditor_id"])
        bank_loan_rows.append(row)

    internal_ids = create_internal_accounts(conn, buckets["internal_accounts"])
    setup_system_settings(conn)

    cc_holders = parse_credit_card_holders()
    cc_cards = parse_credit_cards()
    bank_lookup = parse_bank_lookup()
    cardholder_id_map = create_cardholders_from_legacy(conn, cc_holders)
    cc_bank_creditor_by_code: dict = {}
    credit_card_rows = [
        create_credit_card_with_bank(conn, c, cardholder_id_map, cc_bank_creditor_by_code, bank_lookup)
        for c in cc_cards
    ]
    conn.commit()

    integrity = validate_master_data_integrity(conn)

    return {
        "validation": validation,
        "buckets": buckets,
        "supplier_ids": supplier_ids,
        "lender_notes": lender_notes,
        "bank_loan_rows": bank_loan_rows,
        "bank_creditor_by_name": bank_creditor_by_name,
        "unresolved_bank_names": unresolved_bank_names,
        "internal_ids": internal_ids,
        "cardholder_ids": list(cardholder_id_map.values()),
        "credit_card_rows": credit_card_rows,
        "cc_bank_creditor_by_code": cc_bank_creditor_by_code,
        "cc_bank_lookup": bank_lookup,
        "integrity": integrity,
    }


def write_report(summary: dict, path: str = REPORT_PATH) -> None:
    lines = []
    lines.append("=" * 78)
    lines.append("PHASE 8a: MASTER DATA SETUP -- REPORT")
    lines.append(f"Run date: {date.today().isoformat()}")
    lines.append("=" * 78)
    lines.append("")
    lines.append("COUNTS")
    lines.append(f"  Suppliers:          {len(summary['supplier_ids'])}")
    lines.append(f"  Lenders:            {len(summary['lender_notes'])} (all status 'Pending Setup')")
    lines.append(f"  Bank loans:         {len(summary['bank_loan_rows'])} across {len(summary['bank_creditor_by_name'])} bank creditors")
    for bank_name, creditor_id in summary["bank_creditor_by_name"].items():
        n = sum(1 for r in summary["bank_loan_rows"] if r["creditor_id"] == creditor_id)
        lines.append(f"      {bank_name}: {n} loan facilities")
    lines.append(f"  Internal accounts:  {len(summary['internal_ids'])}")
    lines.append(f"  Cardholders:        {len(summary['cardholder_ids'])} (from legacy TblCreditCardHolder)")
    lines.append(f"  Credit cards:       {len(summary['credit_card_rows'])} across {len(summary['cc_bank_creditor_by_code'])} card-issuing banks")
    for bank_code, creditor_id in summary["cc_bank_creditor_by_code"].items():
        bank_name = summary["cc_bank_lookup"].get(bank_code, {}).get("short", f"Bank #{bank_code}")
        n = sum(1 for r in summary["credit_card_rows"] if r["creditor_id"] == creditor_id)
        lines.append(f"      {bank_name}: {n} cards")
    lines.append("  (Statement history NOT migrated -- ~1,900 legacy rows, Jan 2022-Oct 2026, skipped")
    lines.append("   per the same 'fresh start, no historical transactions' rule as everything else in Phase 8a.)")
    lines.append("")
    lines.append("DUPLICATE CHECK (difflib ratio > 0.85 among supplier-like names)")
    dups = summary["validation"]["duplicates"]
    if dups:
        for d in dups:
            lines.append(f"  {d['similarity']:.2f}  {d['a']['full_name']!r}  <->  {d['b']['full_name']!r}")
    else:
        lines.append("  None found.")
    lines.append("")
    lines.append("BANK LOANS NEEDING MANUAL VERIFICATION (no known bank code recognized)")
    unresolved = [p for p in summary["unresolved_bank_names"] if p["PayeeId"] not in CONFIRMED_UNRESOLVED_BANK_LOAN_PAYEE_IDS]
    if unresolved:
        for p in unresolved:
            lines.append(f"  PayeeId {p['PayeeId']}: {p['full_name']!r}")
    else:
        lines.append("  None -- every bank-loan name resolved to a known bank code, or was user-confirmed below.")
    confirmed = [p for p in summary["unresolved_bank_names"] if p["PayeeId"] in CONFIRMED_UNRESOLVED_BANK_LOAN_PAYEE_IDS]
    if confirmed:
        lines.append("  User-confirmed as genuine bank loans (bank identity just not derivable from the name):")
        for p in confirmed:
            lines.append(f"    PayeeId {p['PayeeId']}: {p['full_name']!r}")
    lines.append("")
    lines.append("UNCLASSIFIED / EXCLUDED FROM IMPORT (needs manual decision)")
    unclassified = summary["buckets"]["unclassified"]
    if unclassified:
        for p in unclassified:
            lines.append(f"  PayeeId {p['PayeeId']}: {p['full_name']!r} (no Classification tag, no order history)")
    else:
        lines.append("  None.")
    lines.append("")
    lines.append("VALIDATION")
    integrity = summary["integrity"]
    lines.append(f"  validation_passed: {integrity['validation_passed']}")
    for issue in integrity["issues"]:
        lines.append(f"  - {issue}")
    lines.append("")
    lines.append("READY FOR LAUNCH" if integrity["validation_passed"] and not dups else "REVIEW NEEDED BEFORE SIGN-OFF")
    lines.append("=" * 78)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    build_test_database(TEST_DB_PATH)
    conn = sqlite3.connect(TEST_DB_PATH)
    try:
        summary = run_migration(conn)
    finally:
        conn.close()
    write_report(summary)
    ok = summary["integrity"]["validation_passed"]
    print(f"Built {TEST_DB_PATH}")
    print(f"Wrote {REPORT_PATH}")
    print("READY FOR LAUNCH" if ok and not summary["validation"]["duplicates"] else "REVIEW NEEDED -- see report_8a.txt")


if __name__ == "__main__":
    main()
