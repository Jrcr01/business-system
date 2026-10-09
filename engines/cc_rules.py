"""One shared definition of how much of a credit card statement still counts as owed (2026-09-21).

A bank statement's total already includes any balance carried over from the statement before it.
So once a card has a NEWER statement, an older statement's carried-over balance (unpaid_balance_next)
must not be counted again -- that double counted it (a 300 balance carried into a 1,500 statement
showed 1,800 payable). Older statements that carry no balance (the migrated ones) are unchanged.
"""


def superseded_sql(alias: str) -> str:
    """SQL condition: this statement has a newer, non-cancelled statement for the same card."""
    return (f"EXISTS (SELECT 1 FROM cc_statements _n WHERE _n.card_id = {alias}.card_id AND _n.status != 'Cancelled' "
            f"AND (_n.due_date > {alias}.due_date OR (_n.due_date = {alias}.due_date AND _n.cc_statement_id > {alias}.cc_statement_id)))")


def cc_owed_case(alias: str = "cs") -> str:
    """SQL CASE giving the amount of one statement row (aliased `alias`) that counts as still owed."""
    a = alias
    return (f"CASE WHEN {a}.unpaid_balance_next > 0 THEN (CASE WHEN {superseded_sql(a)} THEN 0 ELSE {a}.unpaid_balance_next END) "
            f"ELSE {a}.total_amount_due END")          # owed until it is paid, after its due date too (daily payables, owner 2026-10-09)
