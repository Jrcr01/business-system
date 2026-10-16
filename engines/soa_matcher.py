"""Vendor SOA matcher: a supplier's statement of account against our invoices for that supplier -- matched, missing in AP, amount differs, not on the
SOA. The rows come from a CSV / Excel file, or (owner, 2026-10-16: stage 3 of the snip) from a table snipped off the supplier's PDF or photo on the page
itself. The old whole-page OCR reader for PDFs and pictures (soa_parser) is retired: a scan is read through the snip, box by box, with the person
confirming doubtful cells."""
from __future__ import annotations

import pandas as pd

from engines.common import get_conn
from engines.db_engine import DB_PATH

SNIP_INSTEAD = "A PDF or a picture is read on this page under \"Snip from the statement\": open it there, drag a box over the table, and match."


def _clean_ref(v) -> str:
    return " ".join(str(v or "").split()).upper()


def reconcile_rows(creditor_id: int, rows: list[dict]) -> dict:
    """rows: [{'ref', 'amount'}] as the supplier's statement lists them. Returns the four tables and the counts."""
    soa_df = pd.DataFrame([{"clean_ref": _clean_ref(r.get("ref")), "soa_amount": float(r.get("amount") or 0)} for r in rows if _clean_ref(r.get("ref"))],
                          columns=["clean_ref", "soa_amount"])
    with get_conn(DB_PATH) as conn:
        internal_df = pd.read_sql_query("SELECT doc_ref, amount AS internal_amount, status FROM invoices WHERE creditor_id = ? AND is_opening_balance = 0", conn, params=(creditor_id,))
    internal_df["clean_ref"] = internal_df["doc_ref"].map(_clean_ref)
    merged = pd.merge(soa_df, internal_df, on="clean_ref", how="outer")
    matched_list, missing_in_ap_list, mismatch_list, omitted_list = [], [], [], []
    for _, row in merged.iterrows():
        ref, soa_amt, int_amt = row["clean_ref"], row.get("soa_amount"), row.get("internal_amount")
        if pd.isna(int_amt):
            missing_in_ap_list.append({"doc_ref": ref, "soa_amount": soa_amt, "issue": "Missing in AP"})
        elif pd.isna(soa_amt):
            omitted_list.append({"doc_ref": ref, "internal_amount": int_amt, "status": row["status"], "issue": "Omitted from SOA"})
        else:
            variance = round(float(soa_amt) - float(int_amt), 2)
            if abs(variance) < 0.01:
                matched_list.append({"doc_ref": ref, "amount": int_amt, "status": row["status"]})
            else:
                mismatch_list.append({"doc_ref": ref, "soa_amount": soa_amt, "internal_amount": int_amt, "variance": variance})
    return {
        "success": True,
        "matched": pd.DataFrame(matched_list),
        "missing_in_ap": pd.DataFrame(missing_in_ap_list),
        "amount_mismatches": pd.DataFrame(mismatch_list),
        "omitted_from_soa": pd.DataFrame(omitted_list),
        "summary": {"matched": len(matched_list), "missing_in_ap": len(missing_in_ap_list), "mismatches": len(mismatch_list), "omitted_from_soa": len(omitted_list)},
    }


def reconcile_vendor_soa(creditor_id: int, file_bytes: bytes, file_name: str) -> dict:
    """A CSV or Excel statement: the reference column and the amount column found by their headings. A PDF or picture goes through the snip."""
    fname_lower = file_name.lower()
    if fname_lower.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp")):
        return {"success": False, "error": SNIP_INSTEAD}
    if not fname_lower.endswith((".csv", ".xlsx", ".xls")):
        return {"success": False, "error": "Unsupported file format."}
    try:
        soa_df = pd.read_csv(pd.io.common.BytesIO(file_bytes)) if fname_lower.endswith(".csv") else pd.read_excel(pd.io.common.BytesIO(file_bytes))
        col_map = {col: str(col).strip().lower().replace(" ", "_") for col in soa_df.columns}
        soa_df.rename(columns=col_map, inplace=True)
        ref_col = next((c for c in soa_df.columns if any(k in c for k in ["doc", "ref", "inv", "number"])), None)
        amt_col = next((c for c in soa_df.columns if any(k in c for k in ["amount", "total", "bal", "val"])), None)
        rows = [{"ref": r, "amount": a} for r, a in zip(soa_df[ref_col].astype(str), pd.to_numeric(soa_df[amt_col], errors="coerce").fillna(0.0))]
    except Exception as e:                                   # noqa: BLE001 -- a file it cannot read is reported, never a crash page
        return {"success": False, "error": f"Failed to parse spreadsheet: {e}"}
    return reconcile_rows(creditor_id, rows)
