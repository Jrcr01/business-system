import difflib
import sqlite3
import pandas as pd

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# Nothing this business handles (a loan principal, a card limit) is ever under a peso; a typed
# value below this is almost always a mistyped number field (e.g. 100000 appended after "0.00"
# reads back as 0.001) rather than a real amount.
MIN_MONEY_AMOUNT = 1.0          # default; the owner can change it in Settings (min_money_amount)


def money_amount_error(value: float, label: str) -> str | None:
    """None if `value` is a sane amount for a loan/credit-limit field, else a user-facing message."""
    if value <= 0:
        return f"{label} must be greater than zero."
    from engines.settings_registry import value as setting
    minimum = setting("min_money_amount")
    if value < minimum:
        return f"{label} of {value:g} looks like a typo -- the minimum is ₱{minimum:,.2f}."
    return None


def calculate_trade_discount(gross_amount: float, discount_rates: list[float]) -> tuple[float, float]:
    """Calculates net invoice amount using compound trade discounts."""
    current_amount = gross_amount
    for rate in discount_rates:
        # A rate of exactly 100 (a full write-off/waiver) used to fall through this check
        # untouched, silently charging the full gross amount instead of zero.
        if 0 < rate <= 100:
            current_amount *= (1.0 - (rate / 100.0))
            
    net_payable = round(current_amount, 2)
    total_discount = round(gross_amount - net_payable, 2)
    return total_discount, net_payable

def detect_invoice_anomalies(creditor_id: int, doc_ref: str, amount: float, invoice_date: str, conn) -> list[str]:
    """Scans for duplicate invoice amounts within 7 days or transposed reference numbers."""
    warnings = []
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT doc_ref, invoice_date, amount 
        FROM invoices 
        WHERE creditor_id = ? 
          AND ABS(JULIANDAY(invoice_date) - JULIANDAY(?)) <= 7
          AND amount = ?
    """, (creditor_id, invoice_date, amount))
    
    for match in cursor.fetchall():
        warnings.append(f"ANOMALY: Identical amount (PHP {amount:,.2f}) billed on {match[1]} under Ref '{match[0]}'.")

    cursor.execute("SELECT doc_ref FROM invoices WHERE creditor_id = ?", (creditor_id,))
    existing_refs = [row[0] for row in cursor.fetchall()]
    
    for ref in existing_refs:
        similarity = difflib.SequenceMatcher(None, doc_ref.upper(), ref.upper()).ratio()
        if 0.85 <= similarity < 1.0:
            warnings.append(f"POSSIBLE TYPO: Reference '{doc_ref}' is highly similar to existing invoice '{ref}'.")

    return warnings