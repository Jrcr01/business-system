"""Short bank names for card lists and warnings (owner, 2026-09-29: "yes i need short names ... banner include bank and card name").
The short names are the family's own bank codes already used in card names ("MBTC Mastercard", "EW Mastercard", "SB Mastercard").
Real cards from the owner's list; the bank is not repeated when the card name already starts with it ("BPI Mastercard")."""
from engines.bank_names import card_display_name, short_bank_name


def test_short_names_of_the_banks_on_file():
    assert short_bank_name("Bank of the Philippine Islands") == "BPI"
    assert short_bank_name("Philippine National Bank") == "PNB"
    assert short_bank_name("East West Banking Corporation") == "EW"
    assert short_bank_name("Security Bank Corporation") == "SB"
    assert short_bank_name("Metropolitan Bank & Trust Company") == "MBTC"
    assert short_bank_name("Rizal Commercial Banking Corporation") == "RCBC"
    assert short_bank_name("BDO Unibank, Inc.") == "BDO"
    assert short_bank_name("China Banking Corporation") == "CB"
    assert short_bank_name("Union Bank of the Philippines") == "UB"
    assert short_bank_name("Bank of Commerce") == "BC"


def test_an_unknown_bank_is_tidied_not_lost():
    assert short_bank_name("Maybank Philippines, Inc.") == "Maybank Philippines"
    assert short_bank_name("Acme Banking Corporation") == "Acme"
    assert short_bank_name("") == "" and short_bank_name(None) == ""


def test_bank_plus_card_without_repeating_the_bank():
    assert card_display_name("Bank of the Philippine Islands", "Mastercard Plat Rewards") == "BPI Mastercard Plat Rewards"
    assert card_display_name("Bank of the Philippine Islands", "BPI Mastercard") == "BPI Mastercard"          # already starts with BPI
    assert card_display_name("Philippine National Bank", "PNB Mastercard") == "PNB Mastercard"
    assert card_display_name("Security Bank Corporation", "SB Mastercard") == "SB Mastercard"                 # SB = Security Bank
    assert card_display_name("Metropolitan Bank & Trust Company", "MBTC Mastercard") == "MBTC Mastercard"
    assert card_display_name("East West Banking Corporation", "EW Mastercard") == "EW Mastercard"
    assert card_display_name("Rizal Commercial Banking Corporation", "RCBC JCB") == "RCBC JCB"
    assert card_display_name("Metropolitan Bank & Trust Company", "Platinum") == "MBTC Platinum"
    assert card_display_name("BDO Unibank, Inc.", "Jcb Luckycat") == "BDO Jcb Luckycat"
    assert card_display_name(None, "Gold") == "Gold"
