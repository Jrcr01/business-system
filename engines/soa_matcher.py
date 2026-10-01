import sqlite3
import pandas as pd
from engines.soa_parser import parse_soa_document

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

def reconcile_vendor_soa(creditor_id: int, file_bytes: bytes, file_name: str) -> dict:
    """Reconciles vendor SOAs provided as CSV, XLSX, PDF, or Image formats."""
    fname_lower = file_name.lower()

    if fname_lower.endswith(('.csv', '.xlsx', '.xls')):
        try:
            soa_df = pd.read_csv(pd.io.common.BytesIO(file_bytes)) if fname_lower.endswith('.csv') else pd.read_excel(pd.io.common.BytesIO(file_bytes))
            col_map = {col: str(col).strip().lower().replace(' ', '_') for col in soa_df.columns}
            soa_df.rename(columns=col_map, inplace=True)
            ref_col = next((c for c in soa_df.columns if any(k in c for k in ['doc', 'ref', 'inv', 'number'])), None)
            amt_col = next((c for c in soa_df.columns if any(k in c for k in ['amount', 'total', 'bal', 'val'])), None)
            soa_df['clean_ref'] = soa_df[ref_col].astype(str).str.strip().str.upper()
            soa_df['soa_amount'] = pd.to_numeric(soa_df[amt_col], errors='coerce').fillna(0.0)
        except Exception as e:
            return {"success": False, "error": f"Failed to parse spreadsheet: {str(e)}"}
    elif fname_lower.endswith(('.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.bmp')):
        soa_df = parse_soa_document(file_bytes, file_name)
        if soa_df.empty:
            return {"success": False, "error": "Could not extract readable invoice references and amounts."}
        soa_df['clean_ref'] = soa_df['doc_ref'].astype(str).str.strip().str.upper()
    else:
        return {"success": False, "error": "Unsupported file format."}

    with sqlite3.connect(DB_PATH) as conn:
        internal_df = pd.read_sql_query("SELECT doc_ref, amount AS internal_amount, status FROM invoices WHERE creditor_id = ? AND is_opening_balance = 0", conn, params=(creditor_id,))

    internal_df['clean_ref'] = internal_df['doc_ref'].astype(str).str.strip().str.upper()
    merged = pd.merge(soa_df[['clean_ref', 'soa_amount']], internal_df, on='clean_ref', how='outer')

    matched_list, missing_in_ap_list, mismatch_list, omitted_list = [], [], [], []

    for _, row in merged.iterrows():
        ref, soa_amt, int_amt = row['clean_ref'], row.get('soa_amount'), row.get('internal_amount')
        if pd.isna(int_amt):
            missing_in_ap_list.append({"doc_ref": ref, "soa_amount": soa_amt, "issue": "Missing in AP"})
        elif pd.isna(soa_amt):
            omitted_list.append({"doc_ref": ref, "internal_amount": int_amt, "status": row['status'], "issue": "Omitted from SOA"})
        else:
            variance = round(float(soa_amt) - float(int_amt), 2)
            if abs(variance) < 0.01:
                matched_list.append({"doc_ref": ref, "amount": int_amt, "status": row['status']})
            else:
                mismatch_list.append({"doc_ref": ref, "soa_amount": soa_amt, "internal_amount": int_amt, "variance": variance})

    return {
        "success": True,
        "matched": pd.DataFrame(matched_list),
        "missing_in_ap": pd.DataFrame(missing_in_ap_list),
        "amount_mismatches": pd.DataFrame(mismatch_list),
        "omitted_from_soa": pd.DataFrame(omitted_list),
        "summary": {
            "matched": len(matched_list),
            "missing_in_ap": len(missing_in_ap_list),
            "mismatches": len(mismatch_list),
            "omitted_from_soa": len(omitted_list)
        }
    }