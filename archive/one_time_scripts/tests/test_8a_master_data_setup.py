"""Phase 8a: Master Data Setup -- tests for phase8a_master_data_setup.py.

Counts and behavior here are grounded in the real tagged export (data/source/TblPayee-tagged.csv)
and the real order history it was reconciled against, not the original spec's rough guesses --
see the Phase 8a plan for the reconciliation (98 suppliers, not ~110; 0 near-duplicate suppliers
at the 0.85 threshold, not "~3"). Cardholders/credit cards are imported from the real legacy
export (TblCreditCardHolder.txt, TblCreditCard.txt, TblBank.txt) -- 6 real cardholders and 59
real cards, not the original spec's 3 invented placeholder names.
"""
import sqlite3

import pytest

import phase8a_master_data_setup as p8a


@pytest.fixture
def real_rows():
    return p8a.parse_payee_tagged_csv()


@pytest.fixture
def real_buckets(real_rows):
    return p8a.categorize_and_resolve_payees(real_rows)


def _write_csv(tmp_path, rows_text):
    path = tmp_path / "fixture.csv"
    path.write_text(rows_text, encoding="utf-8")
    return str(path)


FIXTURE_HEADER = "PayeeId,Classification,FirstName,MiddleInitial,LastName,PayeeSuffix,ContactNo,Email,Address,Representative_FirstName,Representative_LastName,Representative_MiddleName\n"


def test_parse_payee_tagged_csv_loads_and_cleans_data(tmp_path):
    csv_text = FIXTURE_HEADER + (
        '1,Supplier,  Acom Trading  ,,,,,,,,,\n'
        '2,Lender,Neth,,Canlas,,,,,,,\n'
        '3,Supplier,MOTMM Marketing,,,,,,,,,\n'
    )
    path = _write_csv(tmp_path, csv_text)

    rows = p8a.parse_payee_tagged_csv(path)

    assert len(rows) == 3
    assert all(r["full_name"] for r in rows)
    assert rows[0]["full_name"] == "Acom Trading"
    assert rows[1]["full_name"] == "Neth Canlas"


def test_validate_payee_data_detects_duplicates(tmp_path):
    # Near-identical names (same company, abbreviated legal suffix) -- difflib's ratio is
    # length-sensitive, so the pair needs to be close enough in length as well as content to
    # clear the 0.85 threshold; a short/long mismatch like "ABC Inc" vs "ABC Incorporated"
    # (very different total length) does not.
    csv_text = FIXTURE_HEADER + (
        '1,Supplier,Juan Dela Cruz Trading,,,,,,,,,\n'
        '2,Supplier,Juan Dela Cruz Trading Co,,,,,,,,,\n'
        '3,Supplier,Totally Different Company,,,,,,,,,\n'
    )
    path = _write_csv(tmp_path, csv_text)
    rows = p8a.parse_payee_tagged_csv(path)

    results = p8a.validate_payee_data(rows)

    assert len(results["duplicates"]) >= 1
    assert results["duplicates"][0]["similarity"] > 0.85


def test_categorize_and_resolve_payees_groups_correctly(real_buckets):
    # Lenders is 22, not 21: PayeeId 48 (Rona De Vera) was originally blank/unclassified
    # (excluded, no order history) but the user confirmed 2026-09-15 she's a Lender.
    assert len(real_buckets["suppliers"]) == 98
    assert len(real_buckets["lenders"]) == 22
    assert len(real_buckets["bank_loans"]) == 30
    assert len(real_buckets["internal_accounts"]) == 5


def test_extract_bank_name_works_for_all_bank_loan_names():
    assert p8a.extract_bank_name("LOAN - BDO Housing_005660136452") in ("BDO Housing", "BDO")
    assert p8a.extract_bank_name("LOAN-PNB 20TH OF EVERY MONTH") == "PNB"
    assert p8a.extract_bank_name("LOAN - CB_618000022369") == "CB"
    assert p8a.extract_bank_name("Every 11th - RAP BPI") == "BPI"


def test_extract_beneficiary_name_recognizes_known_patterns():
    assert p8a.extract_beneficiary_name("LOAN - BPI/EMELITA 30TH OF EVERY MONTH") == "Emelita Roxas"
    assert p8a.extract_beneficiary_name("LOAN - BPI_EMELITA") == "Emelita Roxas"
    # Normalized against the real cardholder registry (Jessie Roxas), not kept as the raw
    # "Jessie G. Roxas" with the middle initial the source payee text happened to include.
    assert p8a.extract_beneficiary_name("PNB Credit Cards FAO: Jessie G. Roxas") == "Jessie Roxas"
    # An account-number FAO must not be mistaken for a name.
    assert p8a.extract_beneficiary_name("PNB CREDIT FAO 1234567812345678") is None
    # "RAP" user-confirmed as Julian Ralph Roxas (a real TblCreditCardHolder).
    assert p8a.extract_beneficiary_name("Every 11th - RAP BPI") == "Julian Ralph Roxas"


def test_bank_loans_with_known_beneficiaries_get_linked(db, real_buckets):
    conn = sqlite3.connect(db)
    emelita_payee = next(p for p in real_buckets["bank_loans"] if "EMELITA" in p["full_name"].upper())

    cache: dict = {}
    beneficiary_id = p8a.get_or_create_beneficiary(conn, "Emelita Roxas", cache)
    row = p8a.create_bank_loan_placeholder(conn, emelita_payee, beneficiary_id=beneficiary_id)
    conn.commit()

    assert row["beneficiary_id"] == beneficiary_id
    name = conn.execute("SELECT name FROM loan_beneficiaries WHERE beneficiary_id = ?", (beneficiary_id,)).fetchone()[0]
    assert name == "Emelita Roxas"
    conn.close()


def test_honda_giorno_attributed_to_rhodora(db, real_buckets):
    # "HONDA GIORNO" has no textual pattern to extract a name from -- user-confirmed as a
    # direct payee_id override (_BENEFICIARY_BY_PAYEE_ID), not a text match.
    giorno_payee = next(p for p in real_buckets["bank_loans"] if p["PayeeId"] == 154)
    assert p8a.extract_beneficiary_name(giorno_payee["full_name"]) is None  # confirms it's not text-derivable

    conn = sqlite3.connect(db)
    summary = p8a.run_migration(conn)
    giorno_row = next(r for r in summary["bank_loan_rows"] if r["loan_ref"] == "HONDA GIORNO")
    beneficiary_name = conn.execute(
        "SELECT name FROM loan_beneficiaries WHERE beneficiary_id = ?", (giorno_row["beneficiary_id"],)
    ).fetchone()[0]
    assert beneficiary_name == "Rhodora Roxas"
    conn.close()


def test_internal_accounts_consolidated_to_standard_5(real_buckets):
    internal = real_buckets["internal_accounts"]
    assert len(internal) == 5
    names_upper = " | ".join(r["full_name"].upper() for r in internal)
    for expected in ("ERROR CHECKS", "CASH", "ENCASHMENT", "ADJUSTMENT", "CANCELLED"):
        assert expected in names_upper
    assert "NOT YET USED" not in names_upper


def test_lender_placeholders_created_with_pending_setup_status(db, real_buckets):
    conn = sqlite3.connect(db)
    lender_payee = real_buckets["lenders"][0]

    note = p8a.create_lender_with_placeholder(conn, lender_payee)

    assert note["status"] == "Pending Setup"
    assert note["principal_amount"] is None
    assert note["monthly_interest_rate"] is None
    assert note["total_payable"] is None
    conn.close()


def test_bank_loans_marked_has_amortization_zero(db, real_buckets):
    conn = sqlite3.connect(db)
    bank_loan_payee = real_buckets["bank_loans"][0]

    bank_loan = p8a.create_bank_loan_placeholder(conn, bank_loan_payee)

    assert bank_loan["has_amortization"] == 0
    assert bank_loan["status"] == "Active"
    conn.close()


def test_settings_table_populated_with_seed_values(db):
    conn = sqlite3.connect(db)

    p8a.setup_system_settings(conn)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    assert count >= 5
    bank_name = conn.execute("SELECT value FROM settings WHERE key = 'bank_name'").fetchone()[0]
    assert bank_name == "Metrobank"
    terms = conn.execute("SELECT value FROM settings WHERE key = 'default_terms_picklist'").fetchone()[0]
    assert "COD,30,60,90,120" in terms
    conn.close()


def test_cardholders_and_cards_imported_from_legacy(db):
    conn = sqlite3.connect(db)
    holders = p8a.parse_credit_card_holders()
    cards = p8a.parse_credit_cards()
    bank_lookup = p8a.parse_bank_lookup()

    cardholder_id_map = p8a.create_cardholders_from_legacy(conn, holders)
    conn.commit()

    assert len(cardholder_id_map) == 6
    names = {row[0] for row in conn.execute("SELECT name FROM cardholders")}
    assert "Jessie Roxas" in names
    assert "Rhodora Roxas" in names

    bank_creditor_by_code = {}
    card_row = p8a.create_credit_card_with_bank(conn, cards[0], cardholder_id_map, bank_creditor_by_code, bank_lookup)
    conn.commit()

    # credit_limit is NULL -- the legacy export never tracked it (migration 034 made this
    # column nullable specifically for this, same "no invented numbers" rule as lenders/bank
    # loans). The full card number is preserved (payment-schedule reference need), separate
    # from the last4 used everywhere else in the UI.
    assert card_row["credit_limit"] is None
    assert card_row["card_number_full"] == cards[0]["card_number_full"]
    assert card_row["card_number_last4"] == cards[0]["card_number_last4"]
    assert card_row["legacy_card_id"] == cards[0]["legacy_card_id"]
    issuer_type = conn.execute(
        "SELECT creditor_type FROM creditors WHERE creditor_id = ?", (card_row["creditor_id"],)
    ).fetchone()[0]
    assert issuer_type == "CREDIT_CARD"
    conn.close()


def test_bank_code_lookup_resolves_real_bank_names():
    bank_lookup = p8a.parse_bank_lookup()
    # Spot-check the codes actually used in TblCreditCard.txt against TblBank.txt.
    assert bank_lookup[3]["short"] == "BDO"
    assert bank_lookup[6]["short"] == "BPI"
    assert bank_lookup[29]["short"] == "AMEX"


def test_validation_passes_for_complete_master_data(db):
    conn = sqlite3.connect(db)

    summary = p8a.run_migration(conn)

    assert summary["integrity"]["validation_passed"] is True
    assert summary["integrity"]["issues"] == []
    assert len(summary["supplier_ids"]) == 98
    assert len(summary["lender_notes"]) == 22
    assert len(summary["bank_loan_rows"]) == 30
    assert len(summary["internal_ids"]) == 5
    assert len(summary["cardholder_ids"]) == 6
    assert len(summary["credit_card_rows"]) == 59
    assert len(summary["cc_bank_creditor_by_code"]) == 11
    conn.close()
