import sqlite3
import pandas as pd

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def audit_booklet_gaps(booklet_id: int) -> dict:
    """Audits a registered booklet's leaf range and flags leaves that were skipped without a
    reason on file. Under Phase 6 Redux v2's manual-dropdown assignment model, every number comes
    from a dropdown of that booklet's own still-open leaves, so a normal assignment shouldn't
    produce a gap at all -- a real gap here usually means a leaf was assigned outside this app
    (historical pre-Redux data, typed in freehand back then), or a spoiled leaf logged after the
    fact. Still flags a leaf recorded under two different check_number strings meaning the same
    number (e.g. "1" vs "01"), a realistic artifact of that older free-typed era."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT bank_account_id, start_number, end_number, booklet_code FROM check_booklets WHERE booklet_id = ?", (booklet_id,))
        booklet = cursor.fetchone()
        if not booklet:
            raise ValueError(f"Booklet ID {booklet_id} not found.")

        bank_account_id, start_num, end_num, booklet_code = booklet

        df_recorded = pd.read_sql_query("""
            SELECT check_number, amount, status, check_date
            FROM checks_issued
            WHERE bank_account_id = ? AND check_number IS NOT NULL AND CAST(check_number AS INTEGER) BETWEEN ? AND ?
        """, conn, params=(bank_account_id, start_num, end_num))

    df_recorded['check_num_int'] = df_recorded['check_number'].astype(int)
    max_issued = df_recorded['check_num_int'].max() if not df_recorded.empty else start_num - 1

    inventory_breakdown, missing_gaps, duplicate_numbers = [], [], []

    for num in range(start_num, end_num + 1):
        num_str = str(num).zfill(10)
        matches = df_recorded[df_recorded['check_num_int'] == num]
        if len(matches) > 1:
            texts = ", ".join(sorted(matches['check_number'].unique()))
            inventory_breakdown.append({
                'check_number': num_str, 'state': 'CRITICAL: DUPLICATE CHECK NUMBER',
                'amount': float(matches['amount'].sum()), 'note': f"Recorded under conflicting text: {texts}",
            })
            duplicate_numbers.append(num_str)
        elif len(matches) == 1:
            row = matches.iloc[0]
            inventory_breakdown.append({'check_number': num_str, 'state': row['status'], 'amount': row['amount'], 'note': 'Ledger Record'})
        elif num <= max_issued:
            inventory_breakdown.append({'check_number': num_str, 'state': 'CRITICAL: MISSING GAP', 'amount': 0.0, 'note': 'Unaccounted Stock'})
            missing_gaps.append(num_str)
        else:
            inventory_breakdown.append({'check_number': num_str, 'state': 'Open Stock', 'amount': 0.0, 'note': 'Available Leaf'})

    return {
        'booklet_code': booklet_code or f"Booklet #{booklet_id}",
        'total_leaves': (end_num - start_num) + 1,
        'missing_gaps_count': len(missing_gaps),
        'missing_gaps_list': missing_gaps,
        'duplicate_numbers_count': len(duplicate_numbers),
        'duplicate_numbers_list': duplicate_numbers,
        'detailed_breakdown': pd.DataFrame(inventory_breakdown)
    }


def get_checkbook_alerts() -> pd.DataFrame:
    """Runs the gap audit for every Active booklet and returns only the ones with a real
    problem (a missing gap or a duplicate check number) -- the audit itself is a manual,
    on-demand tool, so nothing else prompts anyone to actually run it. Meant for a Dashboard-
    level "you should check this" banner, same reasoning as the credit-card statement-gap
    reminder: "properly account all checks... i should be warned or notified otherwise.\""""
    with sqlite3.connect(DB_PATH) as conn:
        booklets = pd.read_sql_query("SELECT booklet_id FROM check_booklets WHERE status = 'Active'", conn)
    rows = []
    for booklet_id in booklets['booklet_id']:
        result = audit_booklet_gaps(int(booklet_id))
        if result['missing_gaps_count'] > 0 or result['duplicate_numbers_count'] > 0:
            rows.append({
                'booklet_id': booklet_id,
                'booklet_code': result['booklet_code'],
                'missing_gaps_count': result['missing_gaps_count'],
                'duplicate_numbers_count': result['duplicate_numbers_count'],
            })
    return pd.DataFrame(rows, columns=['booklet_id', 'booklet_code', 'missing_gaps_count', 'duplicate_numbers_count'])
