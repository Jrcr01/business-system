"""Vendor SOA matcher: a supplier's statement of account against our invoices for that supplier -- matched, missing in AP, amount differs, not on the
SOA. The rows come from a CSV / Excel file, or (owner, 2026-10-16: stage 3 of the snip) from a table snipped off the supplier's PDF or photo on the page
itself. The old whole-page OCR reader for PDFs and pictures (soa_parser) is retired: a scan is read through the snip, box by box, with the person
confirming doubtful cells."""
from __future__ import annotations

import re

import pandas as pd

from engines.common import get_conn
from engines.db_engine import DB_PATH

SNIP_INSTEAD = "A PDF or a picture is read on this page under \"Snip from the statement\": open it there, drag a box over the table, and match."


def _clean_ref(v) -> str:
    return " ".join(str(v or "").split()).upper()


_PREFIX = re.compile(r"^(?:DR|SI|SO|OM|OS|INV|INVOICE|NO|NUMBER|REF|#|\.|:|-|\s)+", re.I)


def _loose(v) -> str:
    """The number inside a reference, as a supplier might write it: 'DR 55433', 'DR-055433', 'OM# 55433' and '55433' are all '55433' (owner,
    2026-10-17: the statement listed 55433 while our record says DR 55433, and the match called it missing)."""
    t = _PREFIX.sub("", _clean_ref(v))
    t = re.sub(r"[^A-Z0-9]", "", t)
    return t.lstrip("0") or ("0" if t else "")


def reconcile_rows(creditor_id: int, rows: list[dict]) -> dict:
    """rows: [{'ref', 'amount'}] as the supplier's statement lists them. Returns the four tables and the counts. A statement line finds our invoice by
    the exact reference first (SI number or 'DR n'), then by the bare number against the SI number or the DR number."""
    with get_conn(DB_PATH) as conn:
        cols = ("invoice_id", "doc_ref", "dr_no", "internal_amount", "status")
        ours = [dict(zip(cols, r)) for r in conn.execute("SELECT invoice_id, doc_ref, dr_no, amount, status FROM invoices WHERE creditor_id = ? AND is_opening_balance = 0",
                                                         (creditor_id,)).fetchall()]
    exact, loose = {}, {}
    for inv in ours:
        for k in {_clean_ref(inv["doc_ref"]), _clean_ref(inv["dr_no"])} - {""}:
            exact.setdefault(k, inv["invoice_id"])
        for k in {_loose(inv["doc_ref"]), _loose(inv["dr_no"])} - {""}:
            loose.setdefault(k, inv["invoice_id"])
    by_id = {inv["invoice_id"]: inv for inv in ours}
    matched_list, missing_in_ap_list, mismatch_list, omitted_list, seen = [], [], [], [], set()
    for r in rows:
        ref = _clean_ref(r.get("ref"))
        if not ref:
            continue
        soa_amt = float(r.get("amount") or 0)
        iid = exact.get(ref) or loose.get(_loose(ref))
        if iid is None or iid in seen:
            missing_in_ap_list.append({"doc_ref": ref, "soa_amount": soa_amt, "issue": "Missing in AP"})
            continue
        seen.add(iid)
        inv = by_id[iid]
        variance = round(soa_amt - float(inv["internal_amount"]), 2)
        if abs(variance) < 0.01:
            matched_list.append({"doc_ref": inv["doc_ref"], "amount": inv["internal_amount"], "status": inv["status"]})
        else:
            mismatch_list.append({"doc_ref": inv["doc_ref"], "soa_amount": soa_amt, "internal_amount": inv["internal_amount"], "variance": variance})
    for inv in ours:
        if inv["invoice_id"] not in seen:
            omitted_list.append({"doc_ref": inv["doc_ref"], "internal_amount": inv["internal_amount"], "status": inv["status"], "issue": "Omitted from SOA"})
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
