"""AP Disbursement System — Streamlit UI.

Primary Bank Account: Metrobank Account 1 (Branch Code 399).
"""
import ast
import base64
import calendar
import hashlib
import io
import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from engines.db_engine import df_query, promote_draft_statement
from engines.batch_statement_service import generate_batch_statement_pdf, generate_batch_statement_thermal_pdf
from engines.loan_note_service import generate_loan_note_pdf
from engines.loan_schedule_import_service import parse_loan_schedule_spreadsheet, get_template_csv as get_schedule_template_csv
from engines.bank_loan_service import (
    create_bank_loan,
    delete_bank_loan,
    cancel_bank_loan,
    add_bank_loan_installment,
    edit_bank_loan_installment,
    delete_bank_loan_installment,
    add_loan_beneficiary,
    edit_bank_loan_beneficiary,
)
from engines.bank_loan_import_service import parse_bank_loan_spreadsheet, get_template_csv as get_bank_loan_template_csv
from engines.bank_loan_report_service import generate_bank_loan_pdf
from engines.invoice_ocr_service import extract_invoice_fields
from engines.bank_names import expand_bank_name
from engines.cc_payment_service import recorded_total as cc_recorded_total
from engines.cc_rules import cc_owed_case, superseded_sql
from engines.schema_check import pending_schema_updates
from engines.name_case import smart_proper_case
from engines.invoice_template_service import build_invoice_entry_workbook
from engines.duplicate_scan_service import dismiss as dismiss_duplicates, restore as restore_duplicates, scan_possible_duplicates
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import PERIOD_KINDS, get_overview
from engines.invoice_import_service import parse_invoice_spreadsheet, parse_invoice_workbook, match_supplier, get_template_csv
from engines.loan_schedule_service import (
    suggest_installment_interest,
    add_loan_installment,
    unlinked_loan_checks,
    create_schedule_from_checks,
    edit_loan_installment,
    edit_loan_note_terms,
    delete_loan_installment,
    mark_installment_active,
    unlink_installment_for_check,
    retarget_installment_check,
)
from engines.check_engine import (
    DEFAULT_BRANCH_CODE,
    DEFAULT_CHECK_WIDTH_MM,
    DEFAULT_CHECK_HEIGHT_MM,
    format_metrobank_check_number,
    generate_metrobank_check_pdf,
)
from engines.cc_service import settle_cc_statement_via_cash, get_statement_gap_alerts, get_annual_fee_alerts, get_statement_coverage
from engines.checkbook_service import audit_booklet_gaps, get_checkbook_alerts
from engines.last_modified_sql import statement_modified_sql, loan_note_modified_sql, bank_loan_modified_sql
from engines.park_draft_service import save_park_draft, load_park_draft, clear_park_draft, list_park_drafts
from engines.check_run_service import (
    get_pending_check_run,
    get_unnumbered_checks,
    mark_checks_printed,
    generate_check_run_pdf,
    generate_check_run_summary_pdf,
    generate_batch_csv,
    generate_pending_check_run_csv,
    generate_batch_txt,
    generate_pending_check_run_txt,
    print_batch_tag,
    file_name_for_checks,
    file_name_for_batch,
    mark_batch_printed,
    get_unprinted_batches,
    get_chrysanth_settings,
    save_chrysanth_settings,
    CHRYSANTH_CROSSING_OPTIONS,
    create_check_print_batch,
    get_check_print_batches,
    get_check_print_batch_detail,
)
from engines.backup_service import export_database_backup, restore_database_from_bytes
from engines.duplicate_service import (
    loan_check_mismatches,
    bank_loan_warnings, bank_schedule_warnings, cc_statement_warnings, check_warnings, credit_card_warnings,
    credit_memo_warnings, cc_statement_month_warnings, creditor_warnings, invoice_warnings, loan_installment_warnings, loan_note_warnings,
)
from engines.reports_service import (
    get_payables_rundown,
    get_invoice_rundown,
    get_bank_loan_servicing_report,
    get_bank_loan_history,
    get_cardholder_all_cards_history,
    get_credit_card_history,
    get_lender_history,
    get_payment_schedule_all_tracks,
    get_payment_schedule_daily_totals,
    get_private_lender_master_report,
    get_supplier_ap_aging_report,
    get_supplier_history,
    get_parked_checks_report,
)
from engines.payment_schedule_report_service import generate_payment_schedule_pdf, generate_parked_checks_pdf
from engines.interest_service import get_monthly_interest_summary, get_yearly_interest_trend
from engines.account_history_pdf_service import (
    generate_supplier_statement_pdf,
    generate_rundown_pdf,
    generate_lender_statement_pdf,
    generate_bank_loan_statement_pdf,
    generate_credit_card_statement_pdf,
    generate_cardholder_statement_pdf,
)
from engines.dashboard_kpi_service import (
    get_average_dpo_suppliers,
    get_overdue_cancelled_checks,
    get_top_suppliers,
    get_payables_trend,
    get_upcoming_cash_obligations,
    get_supplier_concentration,
    get_top_supplier_dependency,
)
from engines.search_service import search_records
from engines.soa_matcher import reconcile_vendor_soa
from engines.validators import calculate_trade_discount, detect_invoice_anomalies, money_amount_error
from engines.analytics_service import (
    get_ap_composition_by_track,
    get_ap_growth_rate,
    get_supplier_pareto,
    get_lender_pareto,
    get_bank_loan_pareto,
    get_credit_card_pareto,
    get_all_tracks_pareto,
    get_check_status_breakdown,
    get_check_aging_distribution,
    get_debt_cost_comparison,
    get_missing_debt_terms,
)

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    PdfReader = PdfWriter = None

# AP_DB_PATH override lets a separate process point at a different database (e.g. the Phase 8a
# TEST db) without touching the live path every other launch uses by default.
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
PRIMARY_BANK_ACCOUNT_ID = 1

# 2026-09-17, per user request: checks are now printed outside this system entirely (Chrysanth's
# batch-print feature, via the Excel export on the Checks & checkbook tab) instead of this app's
# own PDF generation. Flip back to True to bring the in-app "Print a check"/"Print these checks"
# buttons back -- the underlying PDF-generation code (render_check_print_section(),
# generate_check_run_pdf(), etc.) is untouched, just not rendered while this is False.
PRINT_CHECKS_IN_APP = False

# --------------------------------------------------------------------------
# Data access helpers
# --------------------------------------------------------------------------


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@st.cache_data(ttl="15s")
def q(sql: str, params: tuple = ()) -> pd.DataFrame:
    return df_query(sql, params)


def q_live(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Uncached read — use for anything that enforces uniqueness (e.g. check number
    sequencing), since the 15s cache on q() can otherwise hand out the same check
    number twice across back-to-back mutations."""
    return df_query(sql, params)


def refresh() -> None:
    st.cache_data.clear()
    st.rerun()


def guard_duplicate_submit(action_key: str, *values, cooldown_seconds: float = 3.0) -> bool:
    """True if this submission should actually proceed. Streamlit has no built-in re-submit
    protection — a fast double-click sends two separate script reruns before the first one's
    response can visually disable anything, so a create/insert action fires twice with identical
    inputs (the real bug: a rapid double-click on "Add creditor" created two identical creditor
    rows). Fingerprints the submitted values themselves rather than counting clicks, and silently
    swallows a second submission whose fingerprint matches the immediately-prior one within
    cooldown_seconds — a genuine second submission with different values always goes through."""
    sig = hashlib.sha256(repr(values).encode("utf-8", "ignore")).hexdigest()
    state_key = f"_dupe_guard::{action_key}"
    now = time.monotonic()
    last = st.session_state.get(state_key)
    st.session_state[state_key] = {"sig": sig, "ts": now}
    return not (last is not None and last["sig"] == sig and (now - last["ts"]) < cooldown_seconds)


@st.dialog("Possible double recording")
def duplicate_warning_dialog(key: str, fp: str, warnings: list) -> None:
    st.warning("This looks like it may already be on file:")
    for w in warnings:
        st.markdown(f"- {w}")
    st.markdown("**Do you still want to continue?**")
    c1, c2 = st.columns(2)
    if c1.button("Yes, continue anyway", icon=":material/check:", width="stretch", key=f"{key}_dup_yes"):
        st.session_state[f"_dup_ok::{key}"] = {"fp": fp, "ts": time.monotonic()}
        st.session_state[f"_dup_resume::{key}"] = time.monotonic()
        st.rerun()
    if c2.button("No, go back", width="stretch", key=f"{key}_dup_no"):
        st.rerun()


def dup_resume(key: str) -> bool:
    """True on the rerun right after 'Yes, continue anyway' -- lets the original button's action run
    again without the user having to click it a second time."""
    ts = st.session_state.pop(f"_dup_resume::{key}", None)
    return ts is not None and time.monotonic() - ts < 120


def dup_needs_confirm(key: str, warnings: list) -> bool:
    """True if the action must NOT run yet: warnings exist and the user hasn't said to continue
    (the pop-up is opened here). Once they confirm, the same warnings pass through once."""
    if not warnings:
        return False
    fp = hashlib.sha256(repr(warnings).encode("utf-8", "ignore")).hexdigest()
    ok = st.session_state.get(f"_dup_ok::{key}")
    if ok and ok["fp"] == fp and time.monotonic() - ok["ts"] < 120:
        st.session_state.pop(f"_dup_ok::{key}", None)
        return False
    duplicate_warning_dialog(key, fp, warnings)
    return True


def dup_note(warnings: list) -> str:
    """Warning block to append to a 'Please confirm before saving' summary."""
    nl = chr(10)
    return "" if not warnings else nl + nl + ":warning: **Possible double recording:**" + nl + nl.join(f"- {w}" for w in warnings) + nl + nl + "Save it anyway?"


def fix_nullable_int(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def php(amount) -> str:
    try:
        if pd.isna(amount):
            # float('nan') doesn't raise -- without this, a genuine NaN (pandas' own null for a
            # nullable numeric column read via raw pd.read_sql_query, as opposed to q()'s ''
            # convention) would silently format as the literal string "₱nan" instead of ₱0.00.
            return "₱0.00"
        return f"₱{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "₱0.00"


def date_preset_filter(container, df: pd.DataFrame, column: str, label: str, key: str) -> pd.DataFrame:
    """Today / Last 7 days / Last 30 days / All time / Custom picker on a datetime-text column of `df`; returns the matching rows.
    Used for the 'Last modified' filter of the order statement, loan note and bank loan browsers."""
    days = pd.to_datetime(df[column], errors="coerce").dt.date if not df.empty else pd.Series([], dtype=object)
    valid = days.dropna()
    full_range = (min(valid.min(), date.today()), max(valid.max(), date.today())) if not valid.empty else (date.today(), date.today())
    preset = container.segmented_control(label, options=["Today", "Last 7 days", "Last 30 days", "All time", "Custom"],
                                         default="All time", required=True, key=key)
    if preset == "Today":
        start, end = date.today(), date.today()
    elif preset == "Last 7 days":
        start, end = date.today() - timedelta(days=6), date.today()
    elif preset == "Last 30 days":
        start, end = date.today() - timedelta(days=29), date.today()
    elif preset == "Custom":
        picked = container.date_input("Custom range", value=full_range, key=key + "_range")
        if not (isinstance(picked, (tuple, list)) and len(picked) == 2):
            return df
        start, end = picked
    else:
        return df
    if preset != "Custom":
        container.caption(f"{start:%Y-%m-%d} to {end:%Y-%m-%d}")
    return df[(days >= start) & (days <= end)]


def has_value(v) -> bool:
    """True if v is a real, non-blank value. q()/q_live() (engines/db_engine.py's df_query)
    coerce every SQL NULL to '' app-wide, which breaks a plain pd.isna()/pd.notna() check on a
    nullable column read through them (an empty string is not NaN) -- found live via a genuinely
    unnumbered/unprinted check that both "already has a number" and "already printed" checks
    misreported as true. Use this instead of pd.isna()/pd.notna() on any column that came from q()
    or q_live() and can legitimately be NULL."""
    return not (pd.isna(v) or v == "")


def default_index(options, selected_id) -> int:
    """Index of selected_id within options (a pandas Series/list of ids), for defaulting a
    selectbox to whatever batch is currently open in the Order Statement browser. Falls back
    to 0 (first option) if there's no active selection or it isn't in this particular list."""
    if selected_id is None:
        return 0
    try:
        return list(options).index(selected_id)
    except ValueError:
        return 0


def batch_scoped_key(base: str) -> str:
    """A widget key that changes whenever the open batch changes, so Streamlit treats it as a
    brand-new widget instead of reusing stale session_state from a previously-open batch — a
    plain fixed key would otherwise ignore index= after its first render (Streamlit widgets keep
    their session_state value across reruns regardless of a changed default)."""
    return f"{base}__{st.session_state.get('selected_statement_id')}"


def pdf_preview_and_download(pdf_bytes: bytes, file_name: str, key: str, label: str = "PDF") -> None:
    """Opens the PDF in a new browser tab as soon as it's ready — no second click needed after the
    Generate button — plus the usual download button below it. Built via components.v1.html (not
    st.markdown's unsafe_allow_html, which strips <script> tags so it can't run JS) that turns the
    PDF bytes into a Blob URL and calls window.open() — a popup, not a top-level navigation, so it
    isn't subject to the block that made an <a href="data:..." target="_blank"> link silently do
    nothing (logged earlier this session). The open call fires immediately on mount; the visible
    button is a fallback for when a browser's popup blocker still catches the automatic one (the
    Generate click's "user activation" can expire during the server round-trip that produces this
    PDF, so the auto-open isn't 100% guaranteed — that's a browser security policy, not a bug)."""
    b64 = base64.b64encode(pdf_bytes).decode()
    safe_key = re.sub(r"\W", "_", key)
    dom_id = f"openpdf-{key}"
    components.html(
        f"""
        <button id="{dom_id}" style="width:100%;padding:0.5rem 1rem;border:1px solid rgba(49,51,63,0.2);
        border-radius:0.5rem;background:#fff;cursor:pointer;font-size:14px;font-family:inherit;">
          Open {label} in new tab (click if it didn't open automatically)
        </button>
        <script>
          function openPdf_{safe_key}() {{
            const byteChars = atob("{b64}");
            const byteNumbers = new Array(byteChars.length);
            for (let i = 0; i < byteChars.length; i++) {{
              byteNumbers[i] = byteChars.charCodeAt(i);
            }}
            const blob = new Blob([new Uint8Array(byteNumbers)], {{type: "application/pdf"}});
            return window.open(URL.createObjectURL(blob), "_blank");
          }}
          document.getElementById("{dom_id}").addEventListener("click", openPdf_{safe_key});
          openPdf_{safe_key}();
        </script>
        """,
        height=50,
    )
    st.download_button(f"Download {label}", data=pdf_bytes, file_name=file_name, mime="application/pdf", key=key)


def get_check_print_calibration(bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> dict:
    """This bank account's check-print calibration (bank_accounts, migration 019) -- falls back
    to check_engine.py's own defaults if the account row is somehow missing."""
    row = q_live(
        "SELECT check_width_mm, check_height_mm, check_offset_x_mm, check_offset_y_mm FROM bank_accounts WHERE bank_account_id = ?",
        (bank_account_id,),
    )
    if row.empty:
        return {"check_width_mm": DEFAULT_CHECK_WIDTH_MM, "check_height_mm": DEFAULT_CHECK_HEIGHT_MM, "offset_x_mm": 0.0, "offset_y_mm": 0.0}
    r = row.iloc[0]
    return {
        "check_width_mm": float(r["check_width_mm"]),
        "check_height_mm": float(r["check_height_mm"]),
        "offset_x_mm": float(r["check_offset_x_mm"]),
        "offset_y_mm": float(r["check_offset_y_mm"]),
    }


def render_check_print_section(checks_df: pd.DataFrame, payee_name: str, key_prefix: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> None:
    """Reusable 'print a check' block: pick any Active check (parked or already printed), generate
    its PDF, preview + download via pdf_preview_and_download(). Used identically by Order
    Statement and Lenders' Checks sub-tabs.

    Phase 6 Redux: printing is completely independent of the check number -- the number isn't
    even drawn on the PDF (it's pre-printed on the physical blank stock), so there's nothing to
    pick or assign here. Any parked check can be printed with no booklet/number involved at all;
    an already-printed check just reprints. Use 'Assign a check number' separately (below) to
    give a check its number, whenever that's actually known — before, during, or after this."""
    st.markdown("##### Print a check")
    active_df = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df
    if active_df.empty:
        st.caption("No active check here to print.")
        return
    options = {
        int(r["check_id"]): f"{r['check_number'] or 'No number yet'} — {php(r['amount'])} — {r['check_date']}"
        + (" (already printed)" if has_value(r.get("printed_at")) else "")
        for _, r in active_df.iterrows()
    }
    picked = st.selectbox("Check to print", options=list(options.keys()), format_func=lambda cid: options[cid], key=f"{key_prefix}_print_pick")

    if st.button("Generate check PDF", icon=":material/picture_as_pdf:", key=f"{key_prefix}_print_btn"):
        ok, msg, pdf_bytes = print_check_pdf(int(picked))
        if not ok:
            st.error(msg)
        else:
            st.success(msg)
            pdf_preview_and_download(
                pdf_bytes, f"check_{int(picked)}.pdf",
                key=f"{key_prefix}_print_dl", label="Check PDF",
            )


def _render_retract_number_section(active_df: pd.DataFrame, key_prefix: str) -> None:
    """'Retract a wrongly assigned number': pick a numbered check, confirm, and its number (and booklet link) is removed."""
    numbered = active_df[active_df["check_number"].apply(has_value)] if not active_df.empty else active_df
    if numbered.empty:
        return
    with st.expander("Assigned the wrong number? Retract it"):
        st.caption("Removes the number from the check so you can assign the right one. If it came from a booklet, that leaf goes back to the booklet. "
                   "Only do this if the check was NOT physically written or printed with that number.")
        r_options = {int(r["check_id"]): f"{r['check_number']} — {php(r['amount'])} — {r['check_date']}" for _, r in numbered.iterrows()}
        r_pick = st.selectbox("Numbered check", options=list(r_options.keys()), format_func=lambda cid: r_options[cid], key=f"{key_prefix}_retract_pick")
        if st.button("Retract this number", icon=":material/undo:", key=f"{key_prefix}_retract_btn"):
            def _do_retract(check_id=int(r_pick)):
                ok, msg = retract_check_number(check_id)
                if ok:
                    st.toast(msg)
                return ok, msg
            confirm_create_dialog(f"Take the number off **{r_options[int(r_pick)]}**? The check goes back to having no number.", f"{key_prefix}_retract_check_number", _do_retract)


def render_assign_number_section(checks_df: pd.DataFrame, key_prefix: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> None:
    """Reusable 'assign a check number' block: pick any Active check that doesn't have a number
    yet (regardless of whether it's been printed), pick a booklet, pick one of that booklet's
    still-available numbers from a dropdown, assign. Independent of printing -- see
    render_check_print_section().

    Phase 6 Redux v2 (2026-09-13): auto-sequencing is gone -- this single-operator, low-volume
    workflow doesn't need it, and it added real complexity (spoiled-number skipping, exhaustion
    tracking) for no real benefit over just picking a number. The dropdown already lists every
    open leaf, including ones below the booklet's "suggested next" cursor, so manual selection
    covers backfill too; a plain typed-in fallback remains for a check with no booklet on file."""
    st.markdown("##### Assign a check number")
    if st.session_state.pop(f"_clear_{key_prefix}_assign_manual_number", False):
        st.session_state[f"{key_prefix}_assign_manual_number"] = ""   # a typed number never lingers into the next assignment
    active_df = checks_df[checks_df["status"] == "Active"] if not checks_df.empty else checks_df
    unnumbered_df = active_df[~active_df["check_number"].apply(has_value)] if not active_df.empty else active_df
    if unnumbered_df.empty:
        st.caption("Every active check here already has a number.")
        _render_retract_number_section(active_df, key_prefix)
        return
    options = {
        int(r["check_id"]): f"{php(r['amount'])} — {r['check_date']}" + (" (already printed)" if has_value(r.get("printed_at")) else " (not yet printed)")
        for _, r in unnumbered_df.iterrows()
    }
    picked = st.selectbox("Check", options=list(options.keys()), format_func=lambda cid: options[cid], key=f"{key_prefix}_assign_pick")

    booklets_df = get_booklets(bank_account_id)
    booklet_id, chosen_number, manual_number = None, None, None
    if booklets_df.empty:
        st.caption("No Active booklet on file for this bank account — register one in Admin & audit, or type the number directly below.")
        manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number")
    else:
        b_options = {
            int(r["booklet_id"]): f"{r['booklet_code'] or ('Booklet #' + str(int(r['booklet_id'])))} ({r['start_number']}-{r['end_number']})"
            for _, r in booklets_df.iterrows()
        }
        booklet_id = st.selectbox(
            "Booklet", options=list(b_options.keys()), format_func=lambda bid: b_options[bid], key=f"{key_prefix}_assign_booklet",
        )
        booklet_row = booklets_df.set_index("booklet_id").loc[int(booklet_id)]
        available_numbers = get_available_booklet_numbers(bank_account_id, booklet_row)
        if not available_numbers:
            st.caption("This booklet has no available leaves left — pick another booklet, or type a number directly below.")
        else:
            chosen_number = st.selectbox(
                "Check number", options=available_numbers,
                format_func=str,
                key=f"{key_prefix}_assign_number",
            )
            st.caption(f"{len(available_numbers)} leaf/leaves still available in this booklet.")
        with st.expander("No booklet, or need to type a number directly?"):
            st.caption("If you type a number here, it is used instead of the booklet number above.")
            manual_number = st.text_input("Check number (typed exactly as physically written)", key=f"{key_prefix}_assign_manual_number") or None

    if st.button("Assign check number", icon=":material/tag:", key=f"{key_prefix}_assign_btn"):
        source, value = pick_number_source(booklet_id, chosen_number, manual_number)
        if source == "none":
            st.error("Pick a booklet number, or type the check number directly.")
        else:
            if source == "manual":
                what = f"the **typed number {value}** (no booklet)"
            else:
                what = f"**{value[1]}** from **{b_options[value[0]]}**"
            summary = f"Give the check {options[int(picked)]} the number {what}?"

            def _do_assign(check_id=int(picked), src=source, val=value):
                ok, msg = assign_check_number(check_id, *(val[0], val[1], None) if src == "booklet" else (None, None, val))
                if ok:
                    st.toast(msg)
                    st.session_state[f"_clear_{key_prefix}_assign_manual_number"] = True
                return ok, msg
            confirm_create_dialog(summary, f"{key_prefix}_assign_check_number", _do_assign)
    _render_retract_number_section(active_df, key_prefix)


def creditor_options(creditor_type: str) -> dict:
    df = q(
        "SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = ? AND is_active = 1 ORDER BY creditor_name",
        (creditor_type,),
    )
    return dict(zip(df.creditor_id, df.creditor_name))


def selectbox_creditor(label: str, creditor_type: str, key: str):
    options = creditor_options(creditor_type)
    if not options:
        st.warning(f"No {creditor_type.replace('_', ' ').title()} creditors on file yet. Add one below first.")
        return None
    return st.selectbox(label, options=list(options), format_func=lambda cid: options[cid], key=key)


def selectbox_creditor_label(creditor_type: str, creditor_id: int) -> str:
    return creditor_options_all(creditor_type).get(creditor_id, str(creditor_id))


def _pdf_money(v) -> str:
    return f"{float(v):,.2f}"


def creditor_options_all(creditor_type: str) -> dict:
    """Same as creditor_options() but including inactive creditors -- a history/report picker
    still needs to find a supplier or lender that's since been deactivated."""
    df = q(
        "SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = ? ORDER BY creditor_name",
        (creditor_type,),
    )
    return dict(zip(df.creditor_id, df.creditor_name))


def bank_loan_options() -> dict:
    df = q(
        "SELECT bl.bank_loan_id, bl.loan_ref, c.creditor_name FROM bank_loans bl "
        "JOIN creditors c ON c.creditor_id = bl.creditor_id ORDER BY bl.loan_ref"
    )
    return {row.bank_loan_id: f"{row.loan_ref} — {row.creditor_name}" for row in df.itertuples()}


def credit_card_options() -> dict:
    df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards ORDER BY card_name")
    return {row.card_id: f"{row.card_name} - {row.card_number_last4}" for row in df.itertuples()}


def cardholder_options_with_cards() -> dict:
    """Cardholders that have at least one card on file, for the Reports tab's two-level
    Cardholder -> Card picker ("1 card holder = many cards," same grouping the Credit Cards
    tab's own "Add statement" flow already uses). `None` is included as a synthetic
    "Unassigned" option whenever a card exists with no cardholder_id set."""
    df = q(
        "SELECT DISTINCT ch.cardholder_id, ch.name FROM credit_cards cc "
        "JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id ORDER BY ch.name"
    )
    options = dict(zip(df.cardholder_id, df.name))
    unassigned = q("SELECT COUNT(*) AS n FROM credit_cards WHERE cardholder_id IS NULL")["n"].iloc[0]
    if unassigned:
        options[None] = "Unassigned"
    return options


def credit_card_options_for_holder(cardholder_id) -> dict:
    if cardholder_id is None:
        df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards WHERE cardholder_id IS NULL ORDER BY card_name")
    else:
        df = q("SELECT card_id, card_name, card_number_last4 FROM credit_cards WHERE cardholder_id = ? ORDER BY card_name", (cardholder_id,))
    return {row.card_id: f"{row.card_name} - {row.card_number_last4}" for row in df.itertuples()}


def history_date_range(date_series_list: list, key_prefix: str):
    """Reports tab history drill-downs: a From/To date-range picker defaulting to the full span
    of whatever dates actually exist across every table for the currently-picked entity (so it
    starts unfiltered, not empty). Returns (date_from, date_to) as date objects, or (None, None)
    when there's no data yet to range over. key_prefix should embed the entity's id (e.g.
    f"...{sup_id}") so switching entities gets a fresh widget instead of carrying over a stale
    date range that may not even overlap the newly-picked entity's dates."""
    non_empty = [s for s in date_series_list if s is not None and not s.empty]
    if not non_empty:
        return None, None
    parsed = pd.to_datetime(pd.concat(non_empty), errors="coerce").dropna()
    if parsed.empty:
        return None, None
    min_d, max_d = parsed.min().date(), parsed.max().date()
    f1, f2 = st.columns(2)
    with f1:
        date_from = st.date_input("From", value=min_d, min_value=min_d, max_value=max_d, key=f"{key_prefix}_from")
    with f2:
        date_to = st.date_input("To", value=max_d, min_value=min_d, max_value=max_d, key=f"{key_prefix}_to")
    return date_from, date_to


def filter_by_date(df: pd.DataFrame, col: str, date_from, date_to) -> pd.DataFrame:
    if df.empty or date_from is None or date_to is None:
        return df
    parsed = pd.to_datetime(df[col], errors="coerce").dt.date
    return df[(parsed >= date_from) & (parsed <= date_to)]


def filter_by_status(df: pd.DataFrame, col: str, key: str, label: str = "Status") -> pd.DataFrame:
    """A status multiselect that narrows what's shown/exported for one table — defaults to every
    status present (so it starts unfiltered), independent of the summary metrics above it, which
    always exclude cancelled/replaced regardless of this filter (see Reports tab callers)."""
    if df.empty:
        return df
    options = sorted(df[col].dropna().unique())
    if not options:
        return df
    picked = st.multiselect(label, options=options, default=options, key=key)
    return df[df[col].isin(picked)] if picked else df.iloc[0:0]


def add_creditor(name: str, creditor_type: str, tin: str, contact_person: str, contact_number: str, email: str, terms: str = "") -> int:
    if creditor_type in ("BANK_LOAN", "CREDIT_CARD"):
        name = expand_bank_name(name)
    name = smart_proper_case(name)  # ALL CAPS / all lowercase becomes proper case; mixed case is left as typed
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO creditors (creditor_name, creditor_type, tin, contact_person, contact_number, email, terms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name, creditor_type, tin, contact_person, contact_number, email, terms or (DEFAULT_SUPPLIER_TERMS if creditor_type == "SUPPLIER" else None)),
        )
        conn.commit()
        return cur.lastrowid


CREDITOR_REFERENCE_TABLES = (
    "order_statements", "invoices", "loan_notes", "bank_loans", "checks_issued", "credit_cards",
)


def get_or_create_card_issuer(name: str) -> int:
    """Issuer as a plain attribute of a card (2026-09-19): the card form just takes a bank name;
    this finds the matching CREDIT_CARD creditor behind the scenes (case-insensitive, reactivating
    one that was deactivated) or creates it. The creditors table stays as the storage, but nobody
    has to manage issuers as records of their own anymore."""
    clean = expand_bank_name(name)
    if not clean:
        raise ValueError("Issuer name is required.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT creditor_id, is_active FROM creditors WHERE creditor_type = 'CREDIT_CARD' AND LOWER(creditor_name) = LOWER(?) ORDER BY is_active DESC LIMIT 1",
            (clean,),
        ).fetchone()
        if row:
            if not row[1]:
                conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (row[0],))
                conn.commit()
            return row[0]
    return add_creditor(clean, "CREDIT_CARD", "", "", "", "")


def update_creditor(creditor_id: int, name: str, creditor_type: str, tin: str, contact_person: str, contact_number: str, email: str, terms: str) -> None:
    """Edits a creditor's full record, including creditor_type and terms — both now editable
    in-place in the Creditor Master grid. A type change is only safe to apply for a creditor with
    no transaction history yet (checked by the caller via creditor_reference_counts()), since
    reclassifying one that's already in use would silently move it out of the pickers that filter
    by type elsewhere (batches, loan notes, bank loans)."""
    if creditor_type in ("BANK_LOAN", "CREDIT_CARD"):
        name = expand_bank_name(name)
    name = smart_proper_case(name)
    with get_conn() as conn:
        conn.execute(
            "UPDATE creditors SET creditor_name = ?, creditor_type = ?, tin = ?, contact_person = ?, contact_number = ?, email = ?, terms = ? WHERE creditor_id = ?",
            (name, creditor_type, tin, contact_person, contact_number, email, terms or None, creditor_id),
        )
        conn.commit()


def creditor_reference_counts(creditor_id: int) -> dict:
    """How many rows in each transactional table point at this creditor -- the basis for deciding
    hard-delete (zero references anywhere, safe to remove outright) vs. soft-delete/inactivate
    (real history exists, removing the row would break it)."""
    counts = {}
    with get_conn() as conn:
        for table in CREDITOR_REFERENCE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE creditor_id = ?", (creditor_id,)).fetchone()[0]
            if n:
                counts[table] = n
    return counts


def delete_or_deactivate_creditor(creditor_id: int) -> tuple[bool, str]:
    """Hard-deletes a creditor with zero references anywhere; otherwise soft-deletes it
    (is_active = 0) so its transaction history stays intact but it stops appearing as pickable
    for new batches/loan notes/bank loans (creditor_options() already filters on is_active)."""
    refs = creditor_reference_counts(creditor_id)
    with get_conn() as conn:
        if refs:
            conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
            conn.commit()
            detail = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in refs.items())
            return True, f"Creditor has existing history ({detail}) — marked inactive instead of deleted. It stays visible here and keeps its history, just won't appear when starting new transactions."
        conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (creditor_id,))
        conn.commit()
        return True, "Creditor had no transaction history — deleted outright."


def reactivate_creditor(creditor_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (creditor_id,))
        conn.commit()


def deactivate_creditor(creditor_id: int) -> None:
    """Plain, reversible is_active toggle — used when unchecking Active in the Creditor Master
    grid. Deliberately not delete_or_deactivate_creditor(): a checkbox uncheck should always just
    flip the flag (reversible by checking it again), never hard-delete."""
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
        conn.commit()


# --------------------------------------------------------------------------
# Metrobank 399 check issuance (shared by Suppliers + Lenders tracks)
#
# Phase 6 Redux: a check number is assigned at PRINT time, not at issue/park time (checks_issued
# .check_number is nullable — migration 021). Multi-booklet management (check_booklets,
# migration 022) replaces the single "active checkbook" concept: several booklets can be Active
# at once (e.g. Booklet A 1000-1050, Booklet B 2000-2050), so the caller now picks which one to
# auto-sequence from, rather than the system assuming "the" active one.
# --------------------------------------------------------------------------


def get_booklets(bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID, statuses: tuple[str, ...] = ("Active",)) -> pd.DataFrame:
    placeholders = ",".join("?" * len(statuses))
    return q_live(
        f"SELECT * FROM check_booklets WHERE bank_account_id = ? AND status IN ({placeholders}) ORDER BY booklet_id",
        (bank_account_id, *statuses),
    )


def create_booklet(bank_account_id: int, booklet_code: str, start_num: int, end_num: int, date_received: date) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        # Two booklets covering the same leaf numbers on the same bank account would make every
        # number in the overlap ambiguous about which physical checkbook it actually belongs to.
        # Archived booklets are excluded -- their range is retired stock, safe to reuse.
        cur.execute(
            """SELECT booklet_code, start_number, end_number FROM check_booklets
               WHERE bank_account_id = ? AND status != 'Archived' AND start_number <= ? AND end_number >= ?""",
            (bank_account_id, end_num, start_num),
        )
        overlap = cur.fetchone()
        if overlap:
            other_code, other_start, other_end = overlap
            return False, f"Overlaps {other_code or 'an existing booklet'}'s range ({other_start}-{other_end}) on this bank account."
        cur.execute(
            """INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received, status)
               VALUES (?, ?, ?, ?, ?, ?, 'Active')""",
            (bank_account_id, booklet_code, start_num, end_num, start_num, str(date_received)),
        )
        conn.commit()
    return True, "Booklet registered."


def archive_booklet(booklet_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        conn.execute("UPDATE check_booklets SET status = 'Archived' WHERE booklet_id = ?", (booklet_id,))
        conn.commit()
    return True, "Booklet archived."


def _check_number_key(num) -> str:
    """Canonical form of a check number for comparing: digits only, the bank's 399 prefix dropped
    from a full 10-digit number, leading zeros dropped -- so "2554301", "02554301" and "3992554301"
    are all the same physical leaf. Non-numeric text is compared as typed."""
    text = str(num).strip()
    if not text.isdigit():
        return text
    if len(text) == 10 and text.startswith(DEFAULT_BRANCH_CODE):
        text = text[len(DEFAULT_BRANCH_CODE):]
    return text.lstrip("0") or "0"


def _check_number_variants(num) -> list[str]:
    """Every stored form the same leaf could be recorded under (as typed, bare, and 399-prefixed)."""
    text = str(num).strip()
    key = _check_number_key(text)
    variants = {text, key}
    if key.isdigit():
        variants.add(DEFAULT_BRANCH_CODE + key.zfill(7))
    return list(variants)


def _is_check_number_available(cur: sqlite3.Cursor, bank_account_id: int, number_str: str, exclude_check_id: int | None = None) -> bool:
    variants = _check_number_variants(number_str)
    marks = ",".join("?" * len(variants))
    if exclude_check_id is None:
        cur.execute(f"SELECT 1 FROM checks_issued WHERE bank_account_id = ? AND check_number IN ({marks})", (bank_account_id, *variants))
    else:
        cur.execute(
            f"SELECT 1 FROM checks_issued WHERE bank_account_id = ? AND check_number IN ({marks}) AND check_id != ?",
            (bank_account_id, *variants, exclude_check_id),
        )
    if cur.fetchone():
        return False
    cur.execute(f"SELECT 1 FROM spoiled_checks WHERE bank_account_id = ? AND check_number IN ({marks})", (bank_account_id, *variants))
    if cur.fetchone():
        return False
    return True


def get_available_booklet_numbers(bank_account_id: int, booklet_row) -> list[int]:
    """Phase 6 Redux v2: manual per-booklet assignment needs every still-open leaf in a booklet's
    range, not just a single 'next' cursor — the user picks whichever one they like from a
    dropdown (it shrinks as numbers get used), auto-sequencing having been dropped as unneeded
    complexity for this single-operator, low-volume workflow."""
    start, end = int(booklet_row["start_number"]), int(booklet_row["end_number"])
    with get_conn() as conn:
        cur = conn.cursor()
        used = set()
        cur.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL", (bank_account_id,))
        used.update(r[0] for r in cur.fetchall())
        cur.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))
        used.update(r[0] for r in cur.fetchall())
    used_keys = {_check_number_key(u) for u in used}
    return [n for n in range(start, end + 1) if _check_number_key(n) not in used_keys]


def assign_check_number_from_booklet(cur: sqlite3.Cursor, bank_account_id: int, booklet_row: dict, chosen_number: int) -> tuple[str | None, str | None]:
    """Phase 6 Redux v2: the user picks any still-available leaf in the booklet's range from a
    dropdown — not forced to take "the next one" the way auto-sequencing worked. next_available_
    number is kept only as a soft "suggested default" for that dropdown: it advances past the
    chosen leaf only when the choice was at or beyond the current cursor, so picking an earlier,
    backfilled leaf doesn't move it backwards or interfere with the normal forward case."""
    booklet_id = int(booklet_row["booklet_id"])
    start, end = int(booklet_row["start_number"]), int(booklet_row["end_number"])
    if not (start <= chosen_number <= end):
        return None, f"{chosen_number} is outside this booklet's range ({start}-{end})."
    check_number = str(chosen_number)
    if not _is_check_number_available(cur, bank_account_id, check_number):
        return None, f"Check number {check_number} is already on file or logged as spoiled."

    if chosen_number >= int(booklet_row["next_available_number"]):
        seq = chosen_number + 1
        while seq <= end and not _is_check_number_available(cur, bank_account_id, str(seq)):
            seq += 1
        new_status = "Exhausted" if seq > end else "Active"
        cur.execute("UPDATE check_booklets SET next_available_number = ?, status = ? WHERE booklet_id = ?", (min(seq, end + 1), new_status, booklet_id))
        booklet_row["next_available_number"] = min(seq, end + 1)
        booklet_row["status"] = new_status
    return check_number, None


def assign_check_number_manual(cur: sqlite3.Cursor, bank_account_id: int, raw_number: str) -> tuple[str | None, str | None]:
    """Backfill/manual path: assigns exactly what's typed, no booklet cursor touched — for
    retroactively recording a check already found/written from an exhausted or older booklet."""
    num = (raw_number or "").strip()
    if not num:
        return None, "Check number is required."
    if not _is_check_number_available(cur, bank_account_id, num):
        return None, f"Check number {num} is already on file or logged as spoiled."
    return num, None


def _booklet_row_dict(cur: sqlite3.Cursor, booklet_id: int) -> dict | None:
    cur.execute("SELECT * FROM check_booklets WHERE booklet_id = ?", (booklet_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def print_check_pdf(check_id: int) -> tuple[bool, str, bytes | None]:
    """Generates a check's PDF and stamps printed_at (first time only — a reprint doesn't move
    it). Deliberately independent of check_number: printing and number-assignment are two
    separate actions that can happen in either order, since the number isn't even drawn on the
    PDF (it's pre-printed on the physical blank stock) — "print" just means the date/payee/
    amount overlay is ready to go on whichever leaf gets fed into the printer."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT creditor_id, bank_account_id, amount, check_date, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found.", None
        creditor_id, bank_account_id, amount, check_date, status = row
        if status != "Active":
            return False, f"Only Active checks can be printed (current status: {status}).", None
        cur.execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP WHERE check_id = ? AND printed_at IS NULL", (check_id,))
        conn.commit()

    payee_row = q_live("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,))
    payee_name = payee_row["creditor_name"][0] if not payee_row.empty else ""
    calib = get_check_print_calibration(bank_account_id)
    pdf_bytes = generate_metrobank_check_pdf(
        payee_name, float(amount), datetime.combine(pd.to_datetime(check_date).date(), datetime.min.time()),
        check_width_mm=calib["check_width_mm"], check_height_mm=calib["check_height_mm"],
        offset_x_mm=calib["offset_x_mm"], offset_y_mm=calib["offset_y_mm"],
    )
    return True, f"Check printed for {php(amount)}.", pdf_bytes


def assign_check_number(check_id: int, booklet_id: int | None, chosen_number: int | None, manual_number: str | None) -> tuple[bool, str]:
    """Assigns a check_number to a check that doesn't have one yet — a specific leaf manually
    picked from booklet_id's dropdown (Phase 6 Redux v2 — no auto-sequencing), or exactly
    manual_number for a check with no booklet on file. Independent of printed_at (works whether
    the check has already been printed, is about to be, or won't be printed by this software at
    all) and needs no unlock: filling in a still-missing number isn't a correction to an existing
    value, so it doesn't need that workflow's audit friction. Once a number exists, changing it
    goes through apply_check_correction() instead (locked)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT bank_account_id, check_number, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        bank_account_id, existing_number, status = row
        if status != "Active":
            return False, f"Only Active checks can be assigned a number (current status: {status})."
        if existing_number is not None:
            return False, f"This check already has number {existing_number} — use 'Correct an active check' to change it."

        if booklet_id is not None and chosen_number is not None:
            brow = _booklet_row_dict(cur, booklet_id)
            if not brow:
                return False, "Booklet not found."
            check_number, err = assign_check_number_from_booklet(cur, bank_account_id, brow, chosen_number)
        elif manual_number is not None:
            check_number, err = assign_check_number_manual(cur, bank_account_id, manual_number)
        else:
            return False, "Pick a booklet and a number, or type a check number directly."
        if err:
            return False, err

        cur.execute("UPDATE checks_issued SET check_number = ?, booklet_id = ? WHERE check_id = ?", (check_number, booklet_id, check_id))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_NUMBER_ASSIGNED', 'checks_issued', ?, ?)",
            (check_id, f"Assigned check number {check_number}" + (" (from booklet)" if booklet_id else " (manual)")),
        )
        conn.commit()
    return True, f"Check number {check_number} assigned."


def pick_number_source(booklet_id, chosen_number, manual_number) -> tuple[str, object]:
    """Which number an 'Assign' click should use. A number typed in by hand ALWAYS wins over the booklet dropdown --
    typing one is an explicit choice, and ignoring it (as this used to) silently assigned a booklet leaf instead."""
    if manual_number and str(manual_number).strip():
        return "manual", str(manual_number).strip()
    if booklet_id is not None and chosen_number is not None:
        return "booklet", (int(booklet_id), int(chosen_number))
    return "none", None


def retract_check_number(check_id: int) -> tuple[bool, str]:
    """Takes a wrongly assigned number back off a check: the check returns to 'no number yet' and, if the number
    came from a booklet, that leaf becomes available again (no false gap) and the booklet's next-number pointer
    steps back over the freed leaves. Logged in the audit trail. Refused for a locked batch (unlock it first),
    the same rule as any other correction to an existing check value."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT check_number, booklet_id, status, statement_id, bank_account_id FROM checks_issued WHERE check_id = ?", (check_id,))
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        number, booklet_id, status, statement_id, bank_account_id = row
        if status != "Active":
            return False, f"Only Active checks can have a number retracted (current status: {status})."
        if number is None:
            return False, "This check has no number to retract."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to retract a number."
        cur.execute("UPDATE checks_issued SET check_number = NULL, booklet_id = NULL WHERE check_id = ?", (check_id,))
        pointer_note = ""
        if booklet_id is not None:
            brow = _booklet_row_dict(cur, booklet_id)
            if brow:
                start, end, old_next = int(brow["start_number"]), int(brow["end_number"]), int(brow["next_available_number"])
                cur.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL", (bank_account_id,))
                used = {_check_number_key(r[0]) for r in cur.fetchall()}
                cur.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))
                used |= {_check_number_key(r[0]) for r in cur.fetchall()}
                new_next = old_next
                while new_next - 1 >= start and _check_number_key(new_next - 1) not in used:
                    new_next -= 1
                if new_next != old_next:
                    cur.execute("UPDATE check_booklets SET next_available_number = ?, status = CASE WHEN status = 'Exhausted' THEN 'Active' ELSE status END WHERE booklet_id = ?",
                                (new_next, booklet_id))
                    pointer_note = f"; booklet's next number is back at {new_next}"
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values, new_values) VALUES ('CHECK_NUMBER_RETRACTED', 'checks_issued', ?, ?, ?)",
            (check_id, f"check_number {number}", "number removed" + (" (booklet leaf returned)" if booklet_id else "") + pointer_note),
        )
        conn.commit()
    return True, f"Number {number} retracted" + (" — the booklet leaf is available again." if booklet_id else ".")


def assign_check_numbers_manual(assignments: dict[int, str], booklet_id: int | None) -> tuple[bool, str, list[int]]:
    """assignments: {check_id: raw_check_number}. Assigns exactly what's typed per check — no
    booklet cursor is touched. Works regardless of printed_at (a backfill/handwritten check may
    never go through this software's printer at all). booklet_id is optional, purely for
    record-keeping, and may be any status (Active/Exhausted/Archived) since a backfill is often
    against an already-exhausted one."""
    if not assignments:
        return False, "No checks selected.", []
    seen = set()
    for check_id, raw in assignments.items():
        num = (raw or "").strip()
        if not num:
            return False, f"Check {check_id}: check number is required.", []
        if num in seen:
            return False, f"Check number {num} is entered more than once in this batch.", []
        seen.add(num)

    assigned_ids = []
    with get_conn() as conn:
        cur = conn.cursor()
        for check_id, raw in assignments.items():
            cur.execute("SELECT bank_account_id FROM checks_issued WHERE check_id = ? AND status = 'Active' AND check_number IS NULL", (check_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Check {check_id} not found, not Active, or already has a number.", []
            check_number, err = assign_check_number_manual(cur, row[0], raw)
            if err:
                return False, err, []
            cur.execute("UPDATE checks_issued SET check_number = ?, booklet_id = ? WHERE check_id = ?", (check_number, booklet_id, check_id))
            cur.execute(
                "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_NUMBER_ASSIGNED', 'checks_issued', ?, ?)",
                (check_id, f"Assigned check number {check_number} (manual/backfill)"),
            )
            assigned_ids.append(check_id)
        conn.commit()
    return True, f"Assigned check numbers to {len(assigned_ids)} check(s).", assigned_ids


def get_batch_balance(statement_id: int) -> tuple[float, float, float]:
    """(net_total, active_checks_sum, remaining_balance) for a batch. Uncached (q_live) since
    this drives issuance eligibility and the auto-lock decision — both correctness-critical."""
    net_row = q_live("SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (statement_id,))
    net_total = float(net_row["net_total"][0]) if not net_row.empty else 0.0
    sum_row = q_live(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
        (statement_id,),
    )
    active_sum = float(sum_row["s"][0])
    return net_total, active_sum, net_total - active_sum


def split_remaining_evenly(remaining_balance: float, num_checks: int) -> list[float]:
    """Even split of a balance across N checks in whole pesos (no centavos) — e.g. 33,500 / 3
    -> [11166, 11166, 11168]. Every row but the last floors to a whole peso; the last row takes
    whatever is left (including any centavos on the original balance) so the rows always sum
    exactly to the balance."""
    if num_checks <= 0:
        return []
    base = float(int(remaining_balance // num_checks))
    amounts = [base] * num_checks
    amounts[-1] = round(remaining_balance - base * (num_checks - 1), 2)
    return amounts


def sync_invoice_payment_status(statement_id: int) -> None:
    """Marks this batch's invoices Paid/Partially Paid/Unpaid based on how much has actually
    CLEARED (check_date already reached — same "effective_status" convention as v_checks_ledger,
    schema.sql) — not merely issued. A postdated check hasn't actually left the bank yet, so it
    shouldn't mark an invoice Paid just because a check has been written for it. Deliberately
    separate from get_batch_balance()/lock_statement()'s issuance-based "remaining", which
    correctly stays issuance-based since the batch lock/export workflow is tied to handing checks
    to the supplier to collect, not to them clearing.

    Per-invoice tracking (2026-09-14, migration 025): once any check issued against this batch
    has been allocated to specific invoices (invoice_check_allocations), every invoice in the
    batch switches to a per-invoice computation — each invoice's own cleared allocations vs its
    own amount — instead of the old blanket "every invoice in the batch gets the same status"
    behavior. A batch that has never had an allocated check yet (fully legacy) keeps the original
    batch-level blanket computation untouched. Going-forward only, by design: a legacy check's
    amount is invisible at the per-invoice level once a batch switches to per-invoice mode — only
    its contribution to the batch-level balance (get_batch_balance) stays intact.

    Called after anything that changes a batch's checks (issue, correct amount, replace,
    cancel) or its net total (invoice edit, credit memo)."""
    has_allocations = q_live(
        """SELECT COUNT(*) AS n FROM invoice_check_allocations a
           JOIN checks_issued c ON c.check_id = a.check_id WHERE c.statement_id = ?""",
        (statement_id,),
    )["n"][0] > 0

    if not has_allocations:
        net_row = q_live("SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (statement_id,))
        net_total = float(net_row["net_total"][0]) if not net_row.empty else 0.0
        cleared_row = q_live(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM checks_issued WHERE statement_id = ? AND status = 'Active' AND check_date <= DATE('now', 'localtime')",
            (statement_id,),
        )
        cleared_sum = float(cleared_row["s"][0])
        remaining_uncleared = net_total - cleared_sum
        if net_total > 0 and remaining_uncleared <= 0.005:
            new_status = "Paid"
        elif cleared_sum > 0.005:
            new_status = "Partially Paid"
        else:
            new_status = "Unpaid"
        with get_conn() as conn:
            conn.execute(
                "UPDATE invoices SET status = ? WHERE statement_id = ? AND status != 'Cancelled'",
                (new_status, statement_id),
            )
            conn.commit()
        return

    invoices_df = q_live("SELECT invoice_id, amount FROM invoices WHERE statement_id = ? AND status != 'Cancelled'", (statement_id,))
    cleared_df = q_live(
        """SELECT a.invoice_id, COALESCE(SUM(a.amount), 0) AS cleared
           FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id
           WHERE c.statement_id = ? AND c.status = 'Active' AND c.check_date <= DATE('now', 'localtime')
           GROUP BY a.invoice_id""",
        (statement_id,),
    )
    cleared_map = dict(zip(cleared_df["invoice_id"], cleared_df["cleared"])) if not cleared_df.empty else {}
    with get_conn() as conn:
        for _, inv in invoices_df.iterrows():
            cleared = float(cleared_map.get(inv["invoice_id"], 0.0))
            inv_amount = float(inv["amount"])
            remaining = inv_amount - cleared
            if inv_amount > 0 and remaining <= 0.005:
                new_status = "Paid"
            elif cleared > 0.005:
                new_status = "Partially Paid"
            else:
                new_status = "Unpaid"
            conn.execute("UPDATE invoices SET status = ? WHERE invoice_id = ?", (new_status, int(inv["invoice_id"])))
        conn.commit()


def get_invoices_with_remaining(statement_id: int) -> pd.DataFrame:
    """This batch's non-cancelled invoices with how much of each is still unallocated to any
    check — feeds the mandatory check-to-invoice allocation step (2026-09-14, migration 025).
    'Remaining' subtracts every existing allocation regardless of whether that check has cleared
    yet, since allocating reserves the invoice amount the moment it's made — the same dollar
    can't be double-booked to two different checks. Sorted oldest due date first, matching the
    order the auto-fill allocation preview drains invoices in."""
    df = q_live(
        """SELECT i.invoice_id, i.doc_ref, i.due_date, i.amount,
                  i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a WHERE a.invoice_id = i.invoice_id), 0) AS remaining
           FROM invoices i WHERE i.statement_id = ? AND i.status != 'Cancelled'
           ORDER BY i.due_date ASC, i.invoice_id ASC""",
        (statement_id,),
    )
    if df.empty:
        return df
    return df[df["remaining"] > 0.005].reset_index(drop=True)


def waterfall_allocate_preview(check_rows: list[dict], invoices_df: pd.DataFrame) -> pd.DataFrame:
    """Auto-fills which invoice(s) each about-to-be-issued check pays, oldest invoice due date
    first — check_rows in the order they'll be issued (row 0 first), draining each invoice's
    remaining balance before moving to the next one, and moving to the next check once the
    current one runs out. Pure preview: returns one row per (check row, invoice) pair with a
    suggested allocated amount, fully editable in the UI before the checks are actually issued."""
    remaining_by_invoice = dict(zip(invoices_df["invoice_id"], invoices_df["remaining"])) if not invoices_df.empty else {}
    invoice_order = list(invoices_df["invoice_id"]) if not invoices_df.empty else []
    doc_ref_by_invoice = dict(zip(invoices_df["invoice_id"], invoices_df["doc_ref"])) if not invoices_df.empty else {}
    preview_rows = []
    inv_cursor = 0
    for row_idx, row in enumerate(check_rows):
        check_left = float(row["amount"])
        while check_left > 0.005 and inv_cursor < len(invoice_order):
            invoice_id = invoice_order[inv_cursor]
            inv_left = remaining_by_invoice[invoice_id]
            if inv_left <= 0.005:
                inv_cursor += 1
                continue
            take = min(check_left, inv_left)
            preview_rows.append({
                "check_row": row_idx + 1,
                "check_date": row["check_date"],
                "invoice_id": invoice_id,
                "doc_ref": doc_ref_by_invoice[invoice_id],
                "allocated": round(take, 2),
            })
            remaining_by_invoice[invoice_id] -= take
            check_left -= take
            if remaining_by_invoice[invoice_id] <= 0.005:
                inv_cursor += 1
    return pd.DataFrame(preview_rows, columns=["check_row", "check_date", "invoice_id", "doc_ref", "allocated"])


def allocate_check_to_invoices(check_id: int, allocations: list[tuple[int, float]]) -> tuple[bool, str]:
    """Records which invoice(s) one newly-issued check pays and how much of each — mandatory for
    every check issued from here on (2026-09-14, migration 025). Re-validates against each
    invoice's live remaining balance even though the UI already enforces it, since this is the
    actual write path."""
    with get_conn() as conn:
        for invoice_id, amount in allocations:
            if amount <= 0.005:
                continue
            row = conn.execute(
                """SELECT i.amount - COALESCE((SELECT SUM(amount) FROM invoice_check_allocations WHERE invoice_id = i.invoice_id), 0)
                   FROM invoices i WHERE i.invoice_id = ?""",
                (invoice_id,),
            ).fetchone()
            remaining = row[0] if row else 0
            if amount > remaining + 0.01:
                conn.rollback()
                return False, f"Invoice #{invoice_id} only has {php(remaining)} remaining — can't allocate {php(amount)}."
            conn.execute(
                "INSERT INTO invoice_check_allocations (invoice_id, check_id, amount) VALUES (?, ?, ?)",
                (invoice_id, check_id, round(amount, 2)),
            )
        conn.commit()
    return True, "Allocated."


def unlink_invoice_allocations_for_check(cur: sqlite3.Cursor, check_id: int) -> None:
    """Called from cancel_check() within its own transaction -- a cancelled check no longer
    represents a real payment, so its invoice_check_allocations rows must go with it. Without
    this, get_invoices_with_remaining() kept counting the invoice as paid down by a check that no
    longer exists, permanently hiding that slice of the invoice from every future allocation
    (2026-09-17 fix -- mirrors unlink_installment_for_check()'s handling of the loan-note side)."""
    cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (check_id,))


def retarget_invoice_allocations_for_check(cur: sqlite3.Cursor, old_check_id: int, new_check_id: int) -> None:
    """Called from replace_check() within its own transaction -- a replaced check (void+reissue,
    not a cancellation) still pays the same invoice(s), just under a new check id, so the
    allocation rows move with it rather than being dropped (mirrors
    retarget_installment_check())."""
    cur.execute("UPDATE invoice_check_allocations SET check_id = ? WHERE check_id = ?", (new_check_id, old_check_id))


def get_latest_check_for_creditor(creditor_id: int) -> tuple[date, float] | None:
    """The most recent (by check_date) Active check ever issued to this creditor -- a quick
    reference point when picking a new one's date ("last time we paid this supplier was..."),
    surfaced right where a new check's date gets entered. Excludes Cancelled/Replaced/Spoiled
    checks since those aren't real payment history anymore. None when nothing's been issued yet."""
    row = q_live(
        "SELECT check_date, amount FROM checks_issued WHERE creditor_id = ? AND status = 'Active' ORDER BY check_date DESC LIMIT 1",
        (creditor_id,),
    )
    if row.empty:
        return None
    return date.fromisoformat(row["check_date"][0]), float(row["amount"][0])


def get_existing_check_dates_for_creditor(creditor_id: int) -> set[date]:
    """Every date this creditor already has an Active check on file for -- backs the
    same-day-duplicate warning when issuing a new check. This business deliberately avoids ever
    writing a second check to the same supplier on the same day (postdating past the due date
    instead, per the owner's own standing practice), so a new check landing on a date that
    already has one is worth flagging before it's parked, not after."""
    df = q_live("SELECT check_date FROM checks_issued WHERE creditor_id = ? AND status = 'Active'", (creditor_id,))
    if df.empty:
        return set()
    return {date.fromisoformat(d) for d in df["check_date"]}


def issue_check(
    creditor_id: int,
    amount: float,
    check_date: date,
    statement_id: int | None = None,
    loan_note_id: int | None = None,
    bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID,
    installment_id: int | None = None,
) -> tuple[bool, str, int | None]:
    """Records (parks) a check with no check number yet — Phase 6 Redux: a number is assigned
    separately, whenever it's actually known (see assign_check_number()), independent of
    printing. Every check still belongs to a batch and is fully recorded the moment it's issued,
    same as before; only the number is deferred."""
    # Hard rule: a check can never be issued for more than what's actually payable.
    if installment_id is not None:
        inst_row = q_live("SELECT principal, interest FROM loan_installments WHERE installment_id = ?", (installment_id,))
        if not inst_row.empty:
            payable = float(inst_row["principal"][0]) + float(inst_row["interest"][0])
            if amount > payable + 0.005:
                return False, f"Amount {php(amount)} exceeds this installment's payable of {php(payable)}.", None
    elif loan_note_id is not None:
        note_row = q_live("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        if not note_row.empty:
            active_sum_row = q_live("SELECT COALESCE(SUM(amount),0) AS s FROM checks_issued WHERE loan_note_id = ? AND status='Active'", (loan_note_id,))
            total_payable = float(note_row["total_payable"][0])
            active_sum = float(active_sum_row["s"][0])
            if active_sum + amount > total_payable + 0.005:
                return False, f"Amount would push total checks issued to {php(active_sum + amount)}, exceeding this loan's total payable of {php(total_payable)}.", None
    elif statement_id is not None:
        net_total, active_sum, remaining = get_batch_balance(statement_id)
        if amount > remaining + 0.005:
            return False, f"Amount {php(amount)} exceeds the batch's remaining balance of {php(remaining)}.", None

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO checks_issued
               (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
               VALUES (?, ?, ?, ?, NULL, ?, ?, 'Active')""",
            (creditor_id, bank_account_id, statement_id, loan_note_id, str(check_date), amount),
        )
        check_id = cur.lastrowid
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_ISSUED', 'checks_issued', ?, ?)",
            (check_id, f"Parked check for {php(amount)} to creditor {creditor_id} — number to be assigned at print time"),
        )
        if installment_id is not None:
            mark_installment_active(cur, installment_id, check_id)
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check parked for {php(amount)} — pending (number assigned when printed).", check_id


def issue_checks_grid(
    creditor_id: int,
    statement_id: int,
    rows: list[dict],
    bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID,
) -> tuple[bool, str, list[int] | None]:
    """Bulk-issues (parks) multiple checks against a batch in one transaction — the discretionary
    split-into-N-checks workflow. No check number is collected here (Phase 6 Redux: assigned at
    print time), just date + amount per row."""
    cleaned = []
    for r in rows:
        amount = float(r["amount"])
        if amount <= 0:
            return False, "Every row's amount must be greater than zero.", None
        cleaned.append({"check_date": r["check_date"], "amount": amount})

    if not cleaned:
        return False, "No check rows to issue.", None

    # Hard rule: this batch of checks can never push total active checks past the net total.
    net_total, active_sum, remaining = get_batch_balance(statement_id)
    grid_total = sum(r["amount"] for r in cleaned)
    if grid_total > remaining + 0.005:
        return False, f"These checks total {php(grid_total)}, exceeding the batch's remaining balance of {php(remaining)}.", None

    with get_conn() as conn:
        cur = conn.cursor()
        check_ids = []
        for r in cleaned:
            cur.execute(
                """INSERT INTO checks_issued
                   (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
                   VALUES (?, ?, ?, NULL, NULL, ?, ?, 'Active')""",
                (creditor_id, bank_account_id, statement_id, str(r["check_date"]), r["amount"]),
            )
            check_ids.append(cur.lastrowid)
            cur.execute(
                "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_ISSUED', 'checks_issued', ?, ?)",
                (check_ids[-1], f"Parked check for {php(r['amount'])} to creditor {creditor_id} — number to be assigned at print time"),
            )
        conn.commit()

    sync_invoice_payment_status(statement_id)
    total = sum(r["amount"] for r in cleaned)
    return True, f"Parked {len(cleaned)} check(s) totaling {php(total)} — pending (numbers assigned when printed).", check_ids


def cancel_check(check_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bank_account_id, amount, status, statement_id FROM checks_issued WHERE check_id = ?", (check_id,))
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        bank_account_id, amount, status, statement_id = row
        if status != "Active":
            return False, f"Only Active checks can be cancelled (current status: {status})."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to cancel a check."
        cur.execute("UPDATE checks_issued SET status = 'Cancelled' WHERE check_id = ?", (check_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_CANCELLED', 'checks_issued', ?, ?)",
            (check_id, f"Cancelled check for {php(amount)}"),
        )
        unlink_installment_for_check(cur, check_id)
        unlink_invoice_allocations_for_check(cur, check_id)
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check cancelled ({php(amount)})."


def _batch_lock_status(cur: sqlite3.Cursor, statement_id: int | None) -> bool:
    if statement_id is None:
        return False
    cur.execute("SELECT is_locked FROM order_statements WHERE statement_id = ?", (statement_id,))
    row = cur.fetchone()
    return bool(row and row[0] == 1)


def unpark_checks(statement_id: int, check_ids: list[int]) -> tuple[bool, str]:
    """Undoes "Park checks" for the chosen checks: deletes them outright, along with their invoice links, as if never made
    (2026-09-26, per user). Only a check that is Active and has no check number yet can go (2026-09-27: even if it was already
    exported to Chrysanth, which only holds a placeholder number for it) -- a numbered check is final, and is cancelled or replaced instead. All-or-nothing. If that leaves the batch with no
    active check and it was Issued, it goes back to Draft (keeping its OS number). Logged in the audit trail."""
    ids = [int(c) for c in check_ids]
    if not ids:
        return False, "Pick at least one parked check."
    with get_conn() as conn:
        cur = conn.cursor()
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked — unlock it first."
        details, exported, print_batches = [], 0, set()
        for cid in ids:
            cur.execute("SELECT print_batch_id FROM checks_issued WHERE check_id = ?", (cid,))
            pb = cur.fetchone()
            if pb and pb[0] is not None:
                print_batches.add(pb[0])
            cur.execute("SELECT statement_id, status, check_number, printed_at, amount, check_date FROM checks_issued WHERE check_id = ?", (cid,))
            row = cur.fetchone()
            if not row or row[0] != statement_id:
                return False, f"Check {cid} isn't part of this batch."
            _, status, number, printed_at, amount, check_date = row
            if status != "Active":
                return False, f"Check {cid} is {status}, so it can't be un-parked."
            if number is not None:
                return False, f"Check {cid} already has the number {number} — un-park only removes unnumbered checks; cancel or replace it instead."
            if printed_at is not None:
                exported += 1
            for table, column in (("loan_installments", "check_id"), ("cc_payments", "payment_check_id")):
                try:
                    used = cur.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (cid,)).fetchone()
                except sqlite3.OperationalError:
                    used = None
                if used:
                    return False, f"Check {cid} has other records attached ({table.replace('_', ' ')}) — cancel it instead."
            details.append((cid, amount, str(check_date)[:10]))
        for cid, amount, check_date in details:
            cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (cid,))
            cur.execute("DELETE FROM check_corrections WHERE check_id = ?", (cid,))      # only the edit log of a check that never became final
            cur.execute("DELETE FROM checks_issued WHERE check_id = ?", (cid,))
            cur.execute(
                "INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('CHECK_UNPARKED', 'checks_issued', ?, ?)",
                (cid, f"parked check {php(amount)} dated {check_date} removed from batch {statement_id}"),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('CHECKS_UNPARKED', 'order_statements', ?, ?)",
            (statement_id, f"{len(details)} parked check(s) totaling {php(sum(d[1] for d in details))} removed"),
        )
        for pbid in print_batches:      # keep the export history's counts honest after removing some of its checks
            cur.execute(
                "UPDATE check_print_batches SET check_count = (SELECT COUNT(*) FROM checks_issued WHERE print_batch_id = ?), "
                "total_amount = (SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE print_batch_id = ?) WHERE print_batch_id = ?",
                (pbid, pbid, pbid),
            )
        cur.execute("SELECT status, statement_ref FROM order_statements WHERE statement_id = ?", (statement_id,))
        batch_status, batch_ref = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (statement_id,))
        back_to_draft = ""
        if cur.fetchone()[0] == 0 and batch_status == "Issued":
            cur.execute("UPDATE order_statements SET status = 'Draft' WHERE statement_id = ?", (statement_id,))
            cur.execute(
                "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('STATEMENT_BACK_TO_DRAFT', 'order_statements', ?, ?)",
                (statement_id, f"no active checks left after un-park; {batch_ref} is a Draft again (number kept)"),
            )
            back_to_draft = f" The batch has no active checks left, so it is a Draft again (it keeps {batch_ref})."
        conn.commit()
    sync_invoice_payment_status(statement_id)
    chrysanth_note = f" {exported} of them had already been exported to Chrysanth — remove them there too." if exported else ""
    return True, f"Un-parked {len(details)} check(s) totaling {php(sum(d[1] for d in details))}; the balance is available again." + chrysanth_note + back_to_draft


def apply_check_correction(check_id: int, correction_type: str, new_value: str, reason: str) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, loan_note_id, status, amount, check_date, bank_account_id, check_number, printed_at FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        statement_id, loan_note_id, status, old_amount, old_check_date, bank_account_id, old_check_number, printed_at = row
        if status != "Active":
            return False, f"Only Active checks can be corrected (current status: {status})."
        # Per-field gating: correcting a *value that already exists* is the trigger for this
        # locked/audited workflow -- printing and number-assignment are independent now (Phase 6
        # Redux), so each field checks its own "is there something here to correct" condition
        # rather than one blanket printed_at gate. Assigning a still-missing number goes through
        # assign_check_number() instead (no lock needed -- nothing to overwrite yet).
        if correction_type == "Check Number" and old_check_number is None:
            return False, "This check has no number yet — use 'Assign a check number' instead of correcting one."
        if correction_type in ("Amount", "Check Date") and printed_at is None:
            return False, "This check hasn't been printed yet — edit it via 'Edit parked checks' instead."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to apply a correction."

        if correction_type == "Check Number":
            new_number = (new_value or "").strip()
            if not new_number:
                return False, "New check number cannot be blank."
            if not _is_check_number_available(cur, bank_account_id, new_number, exclude_check_id=check_id):
                return False, f"Check number {new_number} is already on file or logged as spoiled for this bank account."
            old_value = str(old_check_number)
            cur.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (new_number, check_id))
        elif correction_type == "Amount":
            try:
                new_amount = float(new_value)
            except ValueError:
                return False, "New amount must be a number."
            if new_amount <= 0:
                return False, "New amount must be greater than zero."

            # Hard rule: a correction can never push checks issued past what's payable either.
            cur.execute("SELECT installment_id, principal, interest FROM loan_installments WHERE check_id = ?", (check_id,))
            inst_row = cur.fetchone()
            if inst_row is not None:
                _, inst_principal, inst_interest = inst_row
                payable = inst_principal + inst_interest
                if new_amount > payable + 0.005:
                    return False, f"New amount {php(new_amount)} exceeds this installment's payable of {php(payable)}."
            elif loan_note_id is not None:
                cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
                (total_payable,) = cur.fetchone()
                cur.execute("SELECT COALESCE(SUM(amount),0) FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_id != ?", (loan_note_id, check_id))
                (other_active_sum,) = cur.fetchone()
                if other_active_sum + new_amount > total_payable + 0.005:
                    return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding this loan's total payable of {php(total_payable)}."
            elif statement_id is not None:
                net_total, active_sum, _ = get_batch_balance(statement_id)
                other_active_sum = active_sum - old_amount
                if other_active_sum + new_amount > net_total + 0.005:
                    return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding the batch's net total of {php(net_total)}."

            old_value = str(old_amount)
            cur.execute("UPDATE checks_issued SET amount = ? WHERE check_id = ?", (new_amount, check_id))
        elif correction_type == "Check Date":
            try:
                datetime.strptime(new_value.strip(), "%Y-%m-%d")
            except ValueError:
                return False, "New check date must be in YYYY-MM-DD format."
            old_value = str(old_check_date)
            cur.execute("UPDATE checks_issued SET check_date = ? WHERE check_id = ?", (new_value.strip(), check_id))
        else:
            old_value = ""

        cur.execute(
            """INSERT INTO check_corrections (check_id, statement_id, correction_type, old_value, new_value, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (check_id, statement_id, correction_type, old_value, new_value, reason),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_CORRECTION', 'checks_issued', ?, ?)",
            (check_id, f"{correction_type}: {old_value} -> {new_value} ({reason or 'no reason given'})"),
        )
        conn.commit()
    if correction_type == "Amount" and statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, "Correction applied and logged."


def replace_check(check_id: int, new_check_date: date, reason: str) -> tuple[bool, str, int | None]:
    """Void + reissue. The replacement check is parked, same as any newly issued check (Phase 6
    Redux: no number until print time) — if the original check had already been printed, that
    physical leaf's number is simply retired (still on file, permanently tied to the voided
    check) rather than reused."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT creditor_id, bank_account_id, statement_id, loan_note_id, amount, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found.", None
        creditor_id, bank_account_id, statement_id, loan_note_id, amount, status = row
        if status != "Active":
            return False, f"Only Active checks can be replaced (current status: {status}).", None
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to replace a check.", None

        cur.execute(
            """INSERT INTO checks_issued
               (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
               VALUES (?, ?, ?, ?, NULL, ?, ?, 'Active')""",
            (creditor_id, bank_account_id, statement_id, loan_note_id, str(new_check_date), amount),
        )
        new_check_id = cur.lastrowid

        cur.execute(
            """UPDATE checks_issued
               SET status = 'Replaced', replacement_check_id = ?, replacement_reason = ?, replaced_at = CURRENT_TIMESTAMP
               WHERE check_id = ?""",
            (new_check_id, reason, check_id),
        )
        retarget_installment_check(cur, check_id, new_check_id)
        retarget_invoice_allocations_for_check(cur, check_id, new_check_id)
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_REPLACED', 'checks_issued', ?, ?)",
            (check_id, f"Replaced by a new parked check (check_id {new_check_id}): {reason or 'no reason given'}"),
        )
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check replaced. New check parked for {php(amount)} — pending (number assigned when printed).", new_check_id


def edit_parked_check(check_id: int, new_amount: float, new_check_date: date) -> tuple[bool, str]:
    """Checks with no check number yet stay freely editable regardless of the batch's lock state
    (2026-09-27, per user: no number assigned = not final -- so a check already exported to Chrysanth, which only carries a
    placeholder number, is still editable) — nothing final exists yet, so the usual unlock gate (built around a real, numbered check)
    doesn't apply. Auto-logs old -> new + timestamp, same no-friction pattern as editing an
    invoice while unlocked. Once a check is printed, further changes go through
    apply_check_correction() instead (requires unlocking the batch first)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, loan_note_id, status, amount, check_date, check_number FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        statement_id, loan_note_id, status, old_amount, old_check_date, check_number = row
        if status != "Active":
            return False, f"Only Active checks can be edited (current status: {status})."
        if check_number is not None:
            return False, "This check already has a check number — use 'Correct an active check' instead (requires unlocking the batch)."
        if new_amount <= 0:
            return False, "Amount must be greater than zero."

        # Same hard payable-ceiling rule as issue_check()/apply_check_correction().
        cur.execute("SELECT installment_id, principal, interest FROM loan_installments WHERE check_id = ?", (check_id,))
        inst_row = cur.fetchone()
        if inst_row is not None:
            _, inst_principal, inst_interest = inst_row
            payable = inst_principal + inst_interest
            if new_amount > payable + 0.005:
                return False, f"New amount {php(new_amount)} exceeds this installment's payable of {php(payable)}."
        elif loan_note_id is not None:
            cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
            (total_payable,) = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_id != ?", (loan_note_id, check_id))
            (other_active_sum,) = cur.fetchone()
            if other_active_sum + new_amount > total_payable + 0.005:
                return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding this loan's total payable of {php(total_payable)}."
        elif statement_id is not None:
            net_total, active_sum, _ = get_batch_balance(statement_id)
            other_active_sum = active_sum - old_amount
            if other_active_sum + new_amount > net_total + 0.005:
                return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding the batch's net total of {php(net_total)}."

        new_date_s = str(new_check_date)
        changes = []
        if abs(new_amount - old_amount) > 0.001:
            changes.append(("Amount", str(old_amount), str(new_amount)))
        if new_date_s != str(old_check_date):
            changes.append(("Check Date", str(old_check_date), new_date_s))
        if not changes:
            return True, "No changes to save."

        cur.execute("UPDATE checks_issued SET amount = ?, check_date = ? WHERE check_id = ?", (new_amount, new_date_s, check_id))
        for field, old_v, new_v in changes:
            cur.execute(
                """INSERT INTO check_corrections (check_id, statement_id, correction_type, old_value, new_value, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (check_id, statement_id, field, old_v, new_v, "Parked-check edit (auto-logged)"),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('PARKED_CHECK_EDITED', 'checks_issued', ?, ?)",
            (check_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"{len(changes)} field(s) updated."


def log_spoiled_check(check_number: str, reason: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO spoiled_checks (bank_account_id, check_number, reason) VALUES (?, ?, ?)",
            (bank_account_id, check_number, reason),
        )
        spoiled_id = cur.lastrowid
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_SPOILED', 'spoiled_checks', ?, ?)",
            (spoiled_id, f"Check {check_number} logged as spoiled: {reason or 'no reason given'}"),
        )
        conn.commit()
    return True, f"Check {check_number} logged as spoiled under 'Error Checks Only (for control)'."


def build_dual_copy_check_pdf(payee_name: str, amount: float, check_date: date, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> bytes | None:
    if PdfReader is None:
        return None
    calib = get_check_print_calibration(bank_account_id)
    writer = PdfWriter()
    for _ in range(2):
        page_bytes = generate_metrobank_check_pdf(
            payee_name, amount, datetime.combine(check_date, datetime.min.time()),
            check_width_mm=calib["check_width_mm"], check_height_mm=calib["check_height_mm"],
            offset_x_mm=calib["offset_x_mm"], offset_y_mm=calib["offset_y_mm"],
        )
        reader = PdfReader(io.BytesIO(page_bytes))
        writer.add_page(reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# --------------------------------------------------------------------------
# Suppliers (Track 1) write helpers
# --------------------------------------------------------------------------


def new_draft_ref(table: str, column: str) -> str:
    """Short draft reference in the form DRAFT-YY-MM-NN, e.g. DRAFT-26-09-01, DRAFT-26-09-02 -- the
    year, the month, then a running number that restarts each month."""
    prefix = f"DRAFT-{datetime.now().strftime('%y-%m')}-"
    with get_conn() as conn:
        used = [r[0][len(prefix):] for r in conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",))]
    highest = max((int(x) for x in used if x.isdigit()), default=0)
    return f"{prefix}{highest + 1:02d}"


def create_order_statement(creditor_id: int) -> int:
    """Creates a Draft batch from a supplier alone. total_amount always starts at 0 and is
    never manually entered — it is recalculated from linked invoices (see recompute_statement_total)."""
    ref = new_draft_ref("order_statements", "statement_ref")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0.0, 'Draft')",
            (creditor_id, ref),
        )
        conn.commit()
        return cur.lastrowid


def recompute_statement_total(statement_id: int) -> None:
    """Keeps order_statements.total_amount in sync with SUM(invoices.amount) for that batch.
    Must be called after any invoice insert/update/delete/re-link against a statement_id."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = ?", (statement_id,)
        )
        new_total = cur.fetchone()[0]
        cur.execute("UPDATE order_statements SET total_amount = ? WHERE statement_id = ?", (new_total, statement_id))
        conn.commit()


def add_credit_memo(statement_id: int, memo_ref: str, memo_date: date, amount: float, reason: str) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        status, is_locked = row
        if status == "Cancelled":
            return False, "Cannot apply a credit memo to a cancelled statement."
        if is_locked == 1:
            return False, "This batch is locked. Unlock it first to apply a credit memo."
        cur.execute(
            """INSERT INTO credit_memos (statement_id, memo_ref, memo_date, amount, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (statement_id, memo_ref, str(memo_date), amount, reason),
        )
        # Credit Memo Deduction Guard (migration 026): a new memo needs re-confirming even if
        # earlier memos on this batch were already confirmed -- the net total just changed again.
        cur.execute("UPDATE order_statements SET credit_memos_confirmed = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_APPLIED', 'order_statements', ?, ?)",
            (statement_id, f"Memo {memo_ref} for {php(amount)}: {reason or 'no reason given'}"),
        )
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Credit memo {memo_ref} for {php(amount)} applied to the batch."


def edit_credit_memo(credit_memo_id: int, memo_ref: str, memo_date: date, amount: float, reason: str) -> tuple[bool, str]:
    """In-place edit of a saved credit memo, gated behind the batch's unlock state — same
    pattern as edit_invoice(). credit_memo_total is summed live in v_batch_net_totals, so
    no recompute step is needed beyond re-syncing invoice payment status."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, memo_ref, memo_date, amount, reason FROM credit_memos WHERE credit_memo_id = ?",
            (credit_memo_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Credit memo not found."
        statement_id, old_ref, old_date, old_amount, old_reason = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to edit a credit memo."
        if not memo_ref or not memo_ref.strip():
            return False, "Credit memo reference cannot be blank."
        if amount <= 0:
            return False, "Amount must be greater than zero."

        new_date_s = str(memo_date)
        changes = []
        if memo_ref != old_ref:
            changes.append(("memo_ref", old_ref, memo_ref))
        if new_date_s != str(old_date):
            changes.append(("memo_date", str(old_date), new_date_s))
        if abs(amount - old_amount) > 0.001:
            changes.append(("amount", str(old_amount), str(amount)))
        if (reason or "") != (old_reason or ""):
            changes.append(("reason", old_reason or "", reason or ""))

        if not changes:
            return True, "No changes to save."

        cur.execute(
            "UPDATE credit_memos SET memo_ref = ?, memo_date = ?, amount = ?, reason = ? WHERE credit_memo_id = ?",
            (memo_ref, new_date_s, amount, reason, credit_memo_id),
        )
        # Credit Memo Deduction Guard: an amount change alters the net total again, so it needs
        # re-confirming -- a ref/date/reason-only edit doesn't affect what's owed, so it's left
        # alone rather than forcing a re-confirm for a purely cosmetic change.
        if any(c[0] == "amount" for c in changes):
            cur.execute("UPDATE order_statements SET credit_memos_confirmed = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_EDITED', 'credit_memos', ?, ?)",
            (credit_memo_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Credit memo updated ({len(changes)} field(s) changed)."


def delete_credit_memo(credit_memo_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, memo_ref, amount FROM credit_memos WHERE credit_memo_id = ?", (credit_memo_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Credit memo not found."
        statement_id, memo_ref, amount = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to delete a credit memo."
        cur.execute("DELETE FROM credit_memos WHERE credit_memo_id = ?", (credit_memo_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_DELETED', 'credit_memos', ?, ?)",
            (credit_memo_id, f"Deleted {memo_ref} ({php(amount)})"),
        )
        # Credit Memo Deduction Guard: re-evaluate after the delete -- if that was the batch's
        # last remaining memo, there's nothing left to confirm, so clear the guard automatically
        # rather than leaving it permanently blocked with no memo left to review or remove.
        remaining = cur.execute("SELECT COUNT(*) FROM credit_memos WHERE statement_id = ?", (statement_id,)).fetchone()[0]
        if remaining == 0:
            cur.execute("UPDATE order_statements SET credit_memos_confirmed = 1 WHERE statement_id = ?", (statement_id,))
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Deleted credit memo {memo_ref}."


def get_batch_memo_status(statement_id: int) -> dict:
    """Credit Memo Deduction Guard (migration 026): whether this batch's credit memos (if any)
    still need confirming before checks can be issued. unapplied_count/total_amount describe
    what's on the batch right now regardless of confirmed state -- the caller decides whether to
    actually block (unapplied_count > 0 and not confirmed)."""
    memo_row = q_live(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM credit_memos WHERE statement_id = ?",
        (statement_id,),
    )
    stmt_row = q_live("SELECT credit_memos_confirmed FROM order_statements WHERE statement_id = ?", (statement_id,))
    confirmed = bool(int(stmt_row["credit_memos_confirmed"][0])) if not stmt_row.empty else True
    return {
        "unapplied_count": int(memo_row["n"][0]),
        "total_amount": float(memo_row["total"][0]),
        "confirmed": confirmed,
    }


def confirm_credit_memos(statement_id: int) -> bool:
    """Marks this batch's current credit memos as reviewed -- clears the Deduction Guard so
    checks can be issued. Re-triggered (confirmed reset to 0) the moment a memo is added or its
    amount changed, so this is a fresh acknowledgment each time the net total actually moves."""
    with get_conn() as conn:
        conn.execute("UPDATE order_statements SET credit_memos_confirmed = 1 WHERE statement_id = ?", (statement_id,))
        conn.commit()
    return True


def unlock_statement(statement_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        if row[0] == "Cancelled":
            return False, "This batch is cancelled and can't be unlocked."
        cur.execute("UPDATE order_statements SET is_locked = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_UNLOCKED', 'order_statements', ?, ?)",
            (statement_id, "Batch unlocked for correction/replacement"),
        )
        conn.commit()
    return True, "Batch unlocked."


def lock_statement(statement_id: int) -> tuple[bool, str]:
    """Hard rule: a batch can never be locked while its active checks exceed the net total —
    overpayment must be corrected (or the offending check cancelled) first, not locked past."""
    net_total, active_sum, remaining = get_batch_balance(statement_id)
    if remaining < -0.005:
        return False, f"Cannot lock — checks issued ({php(active_sum)}) exceed the net total ({php(net_total)}) by {php(-remaining)}. Correct or cancel a check first."
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_LOCKED', 'order_statements', ?, ?)",
            (statement_id, "Batch re-locked"),
        )
        conn.commit()
    return True, "Batch locked."


@st.dialog("Confirm unlock")
def confirm_unlock_dialog(statement_id: int, statement_ref: str) -> None:
    st.write(f"Are you sure you want to unlock batch **{statement_ref}**?")
    st.caption("Unlocking allows corrections and check replacements against this batch.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, unlock", icon=":material/lock_open:", width="stretch"):
        ok, msg = unlock_statement(statement_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Cancel", width="stretch"):
        st.rerun()


def cancel_order_statement(statement_id: int) -> tuple[bool, str]:
    """Cancels a batch: every Active check issued against it gets cancelled (restores the bank
    balance, stays on record with status='Cancelled' — never deleted, per the audit-trail rule),
    and every non-Cancelled invoice in the batch is marked Cancelled (soft, not deleted —
    invoices.status='Cancelled' is already excluded from AP totals/aging everywhere in this app,
    same as sync_invoice_payment_status already protects it from being overwritten). The batch
    itself is locked permanently; unlock_statement() refuses to reopen a Cancelled batch."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        if row[0] == "Cancelled":
            return False, "This batch is already cancelled."
        cur.execute("SELECT check_id FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (statement_id,))
        active_check_ids = [r[0] for r in cur.fetchall()]

    cancelled_checks = 0
    for check_id in active_check_ids:
        ok, _ = cancel_check(int(check_id))
        if ok:
            cancelled_checks += 1

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE invoices SET status = 'Cancelled' WHERE statement_id = ? AND status != 'Cancelled'",
            (statement_id,),
        )
        cancelled_invoices = cur.rowcount
        cur.execute(
            "UPDATE order_statements SET status = 'Cancelled', is_locked = 1 WHERE statement_id = ?",
            (statement_id,),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_CANCELLED', 'order_statements', ?, ?)",
            (statement_id, f"Cancelled batch: {cancelled_checks} check(s) cancelled, {cancelled_invoices} invoice(s) marked Cancelled"),
        )
        conn.commit()
    return True, f"Batch cancelled — {cancelled_checks} check(s) cancelled, {cancelled_invoices} invoice(s) marked Cancelled."


@st.dialog("Confirm cancel batch")
def confirm_cancel_dialog(statement_id: int, statement_ref: str, active_check_count: int, active_check_total: float, invoice_count: int) -> None:
    st.write(f"Are you sure you want to cancel batch **{statement_ref}**?")
    if active_check_count:
        st.warning(f"This will cancel {active_check_count} active check(s) totaling {php(active_check_total)}, restoring that amount to the bank balance.")
    if invoice_count:
        st.caption(f"{invoice_count} invoice(s) in this batch will be marked Cancelled — kept on record, excluded from AP totals, not deleted.")
    st.caption("The batch locks permanently — this can't be undone from the UI.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel batch", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_order_statement(statement_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


def add_invoice(creditor_id: int, doc_ref: str, invoice_date: date, due_date: date, gross_amount: float, discount_rates: list, statement_id: int | None):
    total_discount, net_amount = calculate_trade_discount(gross_amount, discount_rates)
    with get_conn() as conn:
        warnings = detect_invoice_anomalies(creditor_id, doc_ref, net_amount, str(invoice_date), conn)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO invoices
               (creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'Unpaid', ?)""",
            (creditor_id, doc_ref, str(invoice_date), str(due_date), gross_amount, str(discount_rates), net_amount, statement_id),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return net_amount, total_discount, warnings


SUPPLIER_TERMS_OPTIONS = ["", "COD", "30", "60", "90", "120", "140", "150"]
DEFAULT_SUPPLIER_TERMS = "120"


def parse_terms_days(terms) -> int:
    """Days to add to an invoice date to get a suggested due date from the supplier's agreed
    Terms picklist (COD/30/60/90/120). A suggestion, not a hard lock — see
    resolve_due_date() below, which recomputes Due Date from this whenever Date Received changes
    but still lets Due Date be freely overridden afterward for negotiated leeway."""
    text = cell_text(terms).strip().upper()
    if text == "COD" or not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def prospective_year(month: int, today: date | None = None) -> int:
    """The year a picked month is assumed to belong to, looking forward from today (2026-09-26, per user): from September,
    picking January means January of NEXT year, picking November means this year. A month that is still ahead (or the
    current one) stays in this year; one already behind us rolls to the next. The year picker can still override it."""
    today = today or date.today()
    return today.year if month >= today.month else today.year + 1


def _as_date(v):
    if isinstance(v, str):
        return datetime.strptime(v, "%Y-%m-%d").date()
    if isinstance(v, datetime):
        return v.date()
    return v


def resolve_due_date(invoice_date, terms) -> date:
    """Due Date is a fully computed reference field — Date Received + this supplier's Terms,
    recomputed unconditionally on every save. Originally tried preserving a manual Due Date edit
    (comparing against its prior value), matching the old Phase 1 "stays independently editable
    for negotiated leeway" rule — but the user confirmed (2026-09-14) they never edit Due Date
    directly and just want it to always reflect the current Date Received + Terms with no
    exceptions, so the override-preservation logic was dropped rather than debugged further."""
    return add_terms_bank_rule(invoice_date, terms)


def add_terms_bank_rule(invoice_date, terms) -> date:
    """Due date by the bank's rule (2026-09-26, per user): terms that are whole months (30/60/90/120/150 days = 1/2/3/4/5
    months) land on the SAME DAY of the month as the invoice, not on an exact day count -- an invoice dated Sep 8 on 120-day
    terms is due Jan 8, not Jan 6. A month with no such day (Jan 31 + 1 month) uses its last day. Terms that are not whole
    months (e.g. 140) and COD keep the plain day count."""
    d = _as_date(invoice_date)
    days = parse_terms_days(terms)
    if days > 0 and days % 30 == 0:
        year_offset, month_index = divmod(d.month - 1 + days // 30, 12)
        year, month = d.year + year_offset, month_index + 1
        return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))
    return d + timedelta(days=days)


def cell_text(v) -> str:
    """Safe blank-if-missing text read from a data_editor cell (None/NaN -> '')."""
    return "" if pd.isna(v) else str(v)


def parse_less_pct(trade_discounts_repr) -> float:
    """Best-effort read of the first discount rate out of trade_discounts' stringified-list
    storage (e.g. "[10.0]" or "[]"), for showing/editing a single Less % in the UI."""
    try:
        rates = ast.literal_eval(str(trade_discounts_repr)) if trade_discounts_repr else []
        return float(rates[0]) if rates else 0.0
    except (ValueError, SyntaxError, TypeError, IndexError):
        return 0.0


def edit_invoice(invoice_id: int, doc_ref: str, invoice_date: date, due_date: date, gross_amount: float, less_pct: float) -> tuple[bool, str]:
    """True in-place edit of a saved invoice row, gated behind the batch's unlock state.
    Auto-logs old->new per changed field (no manual reason — consistent with the
    no-friction unlock philosophy), then keeps the batch total and invoice payment
    status in sync since gross_amount/less % changes affect both."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts FROM invoices WHERE invoice_id = ?",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Invoice not found."
        statement_id, old_doc_ref, old_invoice_date, old_due_date, old_gross, old_discounts = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to edit an invoice."
        if not doc_ref or not doc_ref.strip():
            return False, "Doc Ref cannot be blank."
        if gross_amount <= 0:
            return False, "Amount must be greater than zero."
        if not (0 <= less_pct <= 100):
            return False, "Less % must be between 0 and 100."

        total_discount, new_amount = calculate_trade_discount(gross_amount, [less_pct] if less_pct else [])
        new_invoice_date_s, new_due_date_s = str(invoice_date), str(due_date)
        new_discounts_repr = str([less_pct] if less_pct else [])

        changes = []
        if doc_ref != old_doc_ref:
            changes.append(("doc_ref", old_doc_ref, doc_ref))
        if new_invoice_date_s != str(old_invoice_date):
            changes.append(("invoice_date", str(old_invoice_date), new_invoice_date_s))
        if new_due_date_s != str(old_due_date):
            changes.append(("due_date", str(old_due_date), new_due_date_s))
        if abs(gross_amount - old_gross) > 0.001:
            changes.append(("gross_amount", str(old_gross), str(gross_amount)))
        if abs(parse_less_pct(old_discounts) - less_pct) > 0.001:
            changes.append(("less_pct", str(parse_less_pct(old_discounts)), str(less_pct)))

        if not changes:
            return True, "No changes to save."

        try:
            cur.execute(
                """UPDATE invoices SET doc_ref = ?, invoice_date = ?, due_date = ?, gross_amount = ?, trade_discounts = ?, amount = ?
                   WHERE invoice_id = ?""",
                (doc_ref, new_invoice_date_s, new_due_date_s, gross_amount, new_discounts_repr, new_amount, invoice_id),
            )
        except sqlite3.IntegrityError:
            return False, f"'{doc_ref}' already exists for this supplier — choose a different Doc Ref."
        for field_name, old_val, new_val in changes:
            cur.execute(
                """INSERT INTO invoice_corrections (invoice_id, statement_id, field_name, old_value, new_value)
                   VALUES (?, ?, ?, ?, ?)""",
                (invoice_id, statement_id, field_name, old_val, new_val),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('INVOICE_EDITED', 'invoices', ?, ?)",
            (invoice_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return True, f"Invoice updated ({len(changes)} field(s) changed)."


def delete_invoice(invoice_id: int) -> tuple[bool, str]:
    """Removes an invoice row entirely, gated behind the batch's unlock state like edit_invoice().
    Unlike edit, there's no per-field log (nothing to diff) — one audit_logs entry captures what
    was removed."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, doc_ref, amount FROM invoices WHERE invoice_id = ?", (invoice_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Invoice not found."
        statement_id, doc_ref, amount = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to delete an invoice."
        (alloc_count,) = cur.execute(
            "SELECT COUNT(*) FROM invoice_check_allocations WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if alloc_count:
            # Deleting the invoice row here would otherwise hit invoice_check_allocations' FK
            # constraint as a raw, uncaught sqlite3.IntegrityError -- a check is already tied to
            # this invoice, so the invoice needs to stay (or the check cancelled/reallocated first).
            return False, f"This invoice has {alloc_count} check(s) already allocated to it — cancel or reallocate those first, then delete."
        cur.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('INVOICE_DELETED', 'invoices', ?, ?)",
            (invoice_id, f"Deleted {doc_ref} ({php(amount)})"),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return True, f"Deleted invoice {doc_ref}."


# --------------------------------------------------------------------------
# Lenders (Track 2) write helpers
# --------------------------------------------------------------------------


@st.dialog("Please confirm before saving")
def confirm_create_dialog(summary: str, action_key: str, action) -> None:
    """Second look at what's about to be saved, in plain words with the amounts formatted -- used
    on the loan note / bank loan / card forms, where a mistyped number field otherwise saves
    silently (a stored 0.001 displays as PHP 0.00 afterwards, hiding the typo). action() does the
    real write and returns (ok, message); guard_duplicate_submit stops a double-clicked Confirm."""
    st.markdown(summary)
    c1, c2 = st.columns(2)
    if c1.button("Yes, save it", icon=":material/check:", width="stretch", key=f"{action_key}_confirm"):
        if guard_duplicate_submit(action_key + "_confirm", summary):
            ok, msg = action()
            if ok:
                st.cache_data.clear()
                st.rerun()
            else:
                st.error(msg)
    if c2.button("Go back and edit", width="stretch", key=f"{action_key}_back"):
        st.rerun()


def create_loan_note(creditor_id: int, issue_date: date, principal_amount: float, monthly_interest_rate: float) -> tuple[bool, str, int | None]:
    """note_ref starts as a DRAFT-<timestamp> placeholder (same pattern as
    create_order_statement()) — promote_draft_loan_note() assigns the permanent "YYYY - LN
    XXXX" number once it's ready, mirroring promote_draft_statement()'s exact scheme.

    total_interest/total_payable start at 0/principal — the schedule is empty at creation
    and these are recomputed from loan_installments as rows get added (recompute_schedule()).

    No placeholder row is seeded — issue_date/principal_amount on loan_notes itself already
    serve as the day-zero anchor (suggest_installment_interest() falls back to them when the
    schedule is empty), so a visible "day-zero" row in loan_installments was never functionally
    necessary, just confusing (it displayed as a Paid installment for money going out, not a
    payment collected).

    Validates principal_amount/monthly_interest_rate itself (same convention as
    edit_loan_note_terms()) rather than trusting the caller — loan_notes.principal_amount has a
    CHECK(principal_amount > 0) constraint, and a 0-or-blank "Principal amount" widget left
    untouched used to hit that raw sqlite3.IntegrityError uncaught, all the way up to the user."""
    if (err := money_amount_error(principal_amount, "Principal amount")):
        return False, err, None
    if monthly_interest_rate < 0:
        return False, "Monthly interest rate cannot be negative.", None
    note_ref = new_draft_ref("loan_notes", "note_ref")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status)
               VALUES (?, ?, ?, ?, ?, 0, ?, 'Active')""",
            (creditor_id, note_ref, str(issue_date), principal_amount, monthly_interest_rate, principal_amount),
        )
        loan_note_id = cur.lastrowid
        conn.commit()
        return True, "Draft loan note created.", loan_note_id


def promote_draft_loan_note(loan_note_id: int) -> str:
    """Assigns a permanent "YYYY - LN XXXX" sequence number if the note is still on its
    DRAFT- placeholder ref — same numbering scheme as promote_draft_statement()."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT note_ref FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Loan note {loan_note_id} not found.")
        current_ref = row[0]
        if not current_ref.startswith("DRAFT-"):
            return current_ref
        prefix = f"{datetime.now().year} - LN "
        # MAX sequence number in use, not "whichever row has the highest loan_note_id" -- same
        # fix as promote_draft_statement() (engines/db_engine.py): promotion order and
        # loan_note_id order aren't the same thing, so picking the highest-id row's ref could
        # yield a lower sequence number than one already assigned elsewhere, colliding on UNIQUE.
        cur.execute("SELECT note_ref FROM loan_notes WHERE note_ref LIKE ?", (f"{prefix}%",))
        existing_seqs = [int(suffix) for (ref,) in cur.fetchall() if (suffix := ref.replace(prefix, "").strip()).isdigit()]
        next_seq = max(existing_seqs) + 1 if existing_seqs else 1
        permanent_ref = f"{prefix}{next_seq:04d}"
        cur.execute("UPDATE loan_notes SET note_ref = ? WHERE loan_note_id = ?", (permanent_ref, loan_note_id))
        conn.commit()
        return permanent_ref


_LOAN_NOTE_STATUSES = ("Active", "Settled", "Defaulted", "Cancelled", "Pending Setup")


def update_loan_note_status(loan_note_id: int, status: str) -> tuple[bool, str]:
    """Only the UI's own constrained selectbox stopped an invalid status from ever being passed
    here -- any other caller would have hit the raw sqlite3.IntegrityError from loan_notes' own
    CHECK(status IN (...)) uncaught. Whitelisted server-side too now."""
    if status not in _LOAN_NOTE_STATUSES:
        return False, f"'{status}' isn't a valid loan note status."
    with get_conn() as conn:
        conn.execute("UPDATE loan_notes SET status = ? WHERE loan_note_id = ?", (status, loan_note_id))
        conn.commit()
    return True, "Status updated."


def delete_loan_note(loan_note_id: int) -> tuple[bool, str]:
    """Hard delete — only when nothing real has happened yet (no checks issued against it),
    same "nothing real yet = deletable" principle delete_loan_installment() already applies to
    a single Pending row. Once a check exists, use cancel_loan_note() instead (soft-cancel,
    keeps the audit trail — mirrors cancel_order_statement())."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        if not cur.fetchone():
            return False, "Loan note not found."
        cur.execute("SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ?", (loan_note_id,))
        (check_count,) = cur.fetchone()
        if check_count > 0:
            return False, "This loan note has checks on record and can't be deleted — use Cancel instead."
        cur.execute("DELETE FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute("DELETE FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('LOAN_NOTE_DELETED', 'loan_notes', ?, ?)",
            (loan_note_id, "Deleted loan note with no checks on record"),
        )
        conn.commit()
    return True, "Loan note deleted."


def cancel_loan_note(loan_note_id: int) -> tuple[bool, str]:
    """Cancels a loan note: every Active check issued against it gets cancelled (restores the
    bank balance, stays on record — never deleted, per the audit-trail rule), and the note
    itself is marked Cancelled permanently. Mirrors cancel_order_statement()'s exact pattern."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        row = cur.fetchone()
        if not row:
            return False, "Loan note not found."
        if row[0] == "Cancelled":
            return False, "This loan note is already cancelled."
        cur.execute("SELECT check_id FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'", (loan_note_id,))
        active_check_ids = [r[0] for r in cur.fetchall()]

    cancelled_checks = 0
    for check_id in active_check_ids:
        ok, _ = cancel_check(int(check_id))
        if ok:
            cancelled_checks += 1

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE loan_notes SET status = 'Cancelled' WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('LOAN_NOTE_CANCELLED', 'loan_notes', ?, ?)",
            (loan_note_id, f"Cancelled loan note: {cancelled_checks} check(s) cancelled"),
        )
        conn.commit()
    return True, f"Loan note cancelled. {cancelled_checks} check(s) cancelled."


@st.dialog("Confirm cancel loan note")
def confirm_cancel_loan_note_dialog(loan_note_id: int, note_ref: str, active_check_count: int, active_check_total: float) -> None:
    st.write(f"Are you sure you want to cancel loan note **{note_ref}**?")
    if active_check_count:
        st.warning(f"This will cancel {active_check_count} active check(s) totaling {php(active_check_total)}, restoring that amount to the bank balance.")
    st.caption("This can't be undone from the UI.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel loan note", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_loan_note(loan_note_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


@st.dialog("Confirm cancel bank loan")
def confirm_cancel_bank_loan_dialog(bank_loan_id: int, loan_ref: str, installment_count: int) -> None:
    st.write(f"Are you sure you want to cancel bank loan **{loan_ref}**?")
    st.caption(f"Its {installment_count} repayment row(s) stay on record, but they stop counting as amounts due on the dashboard, reports and interest figures.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, cancel bank loan", icon=":material/cancel:", width="stretch"):
        ok, msg = cancel_bank_loan(bank_loan_id)
        if ok:
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


@st.dialog("Confirm delete loan note")
def confirm_delete_loan_note_dialog(loan_note_id: int, note_ref: str) -> None:
    st.write(f"Are you sure you want to delete loan note **{note_ref}**?")
    st.caption("No checks are on record for this note, so this is a hard delete — the note and its schedule are removed entirely, not just marked Cancelled. This can't be undone.")
    c1, c2 = st.columns(2)
    if c1.button("Yes, delete loan note", icon=":material/delete_forever:", width="stretch"):
        ok, msg = delete_loan_note(loan_note_id)
        if ok:
            st.session_state["selected_loan_note_id"] = None
            st.cache_data.clear()
            st.rerun()
        else:
            st.error(msg)
    if c2.button("Go back", width="stretch"):
        st.rerun()


# --------------------------------------------------------------------------
# Bank loans (Track 3) — write helpers live in engines/bank_loan_service.py.
# Settlement is untracked cash by default (confirmed 2026-09-06, see CLAUDE.md Phase 3):
# marking a due date Paid is a no-op against bank cash, which this app no longer tracks at all
# (bank_accounts.current_balance was dropped outright — see migration 016).
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Credit cards (Track 4) write helpers
# --------------------------------------------------------------------------


def add_cardholder(name: str, department: str, contact_number: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cardholders (name, department, contact_number) VALUES (?, ?, ?)",
            (name, department or None, contact_number or None),
        )
        conn.commit()
        return cur.lastrowid


def edit_cardholder(cardholder_id: int, name: str, department: str, contact_number: str) -> tuple[bool, str]:
    if not name.strip():
        return False, "Name can't be blank."
    with get_conn() as conn:
        conn.execute(
            "UPDATE cardholders SET name = ?, department = ?, contact_number = ? WHERE cardholder_id = ?",
            (name.strip(), department or None, contact_number or None, cardholder_id),
        )
        conn.commit()
    return True, "Cardholder updated."


def deactivate_cardholder(cardholder_id: int) -> None:
    """Reversible is_active toggle -- a cardholder who's left stops appearing as pickable for new
    cards/statements but keeps their existing history intact (mirrors deactivate_creditor())."""
    with get_conn() as conn:
        conn.execute("UPDATE cardholders SET is_active = 0 WHERE cardholder_id = ?", (cardholder_id,))
        conn.commit()


def reactivate_cardholder(cardholder_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE cardholders SET is_active = 1 WHERE cardholder_id = ?", (cardholder_id,))
        conn.commit()


def add_credit_card(
    creditor_id: int, cardholder_id: int | None, card_name: str, last4: str, credit_limit: float,
    annual_fee_due_month: int | None, statement_day: int | None,
) -> tuple[bool, str]:
    """Validates credit_limit itself rather than trusting the caller — credit_cards.credit_limit
    has a CHECK(credit_limit > 0) constraint, and a 0-or-blank widget left untouched would
    otherwise hit that raw sqlite3.IntegrityError uncaught, same class of bug as
    create_loan_note()'s principal_amount."""
    if (err := money_amount_error(credit_limit, "Credit limit")):
        return False, err
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, statement_day)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (creditor_id, cardholder_id, card_name, last4, credit_limit, annual_fee_due_month, statement_day),
        )
        conn.commit()
    return True, "Card added."


def edit_credit_card(
    card_id: int, card_name: str, creditor_id: int, cardholder_id: int | None,
    credit_limit: float, annual_fee_due_month: int | None, statement_day: int | None,
) -> tuple[bool, str]:
    if not card_name.strip():
        return False, "Card name can't be blank."
    if (err := money_amount_error(credit_limit, "Credit limit")):
        return False, err
    with get_conn() as conn:
        conn.execute(
            "UPDATE credit_cards SET card_name = ?, creditor_id = ?, cardholder_id = ?, credit_limit = ?, annual_fee_due_month = ?, statement_day = ? WHERE card_id = ?",
            (card_name.strip(), creditor_id, cardholder_id, credit_limit, annual_fee_due_month, statement_day, card_id),
        )
        conn.commit()
    return True, "Card updated."


def deactivate_credit_card(card_id: int) -> None:
    """Reversible is_active toggle -- a closed/replaced card stops appearing as pickable for a new
    statement but keeps its existing statement history intact (mirrors deactivate_creditor())."""
    with get_conn() as conn:
        conn.execute("UPDATE credit_cards SET is_active = 0 WHERE card_id = ?", (card_id,))
        conn.commit()


def reactivate_credit_card(card_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE credit_cards SET is_active = 1 WHERE card_id = ?", (card_id,))
        conn.commit()


def get_last_cc_statement_unpaid_balance(card_id: int) -> float:
    """This card's most recent statement's unpaid_balance_next — auto-fills the next
    statement's previous_balance, so the rolling balance chains correctly month to month.
    A card with no statements yet, or whose statements predate rolling-balance tracking
    (unpaid_balance_next defaults to 0), simply starts from 0."""
    row = q_live(
        "SELECT unpaid_balance_next FROM cc_statements WHERE card_id = ? ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    )
    return float(row["unpaid_balance_next"][0]) if not row.empty else 0.0


def get_unconfirmed_cc_statement(card_id: int) -> dict | None:
    """The card's latest statement whose payment isn't known yet (paid_confirmed = 0), if any."""
    row = q_live(
        "SELECT cc_statement_id, due_date, total_amount_due FROM cc_statements WHERE card_id = ? AND paid_confirmed = 0 AND status != 'Cancelled' "
        "ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    )
    return None if row.empty else {"id": int(row["cc_statement_id"][0]), "due_date": row["due_date"][0], "total": float(row["total_amount_due"][0])}


def confirm_cc_payment_from_next_statement(cc_statement_id: int, carried_over: float) -> tuple[float, str]:
    """The next statement shows what was carried over from this one, so what was paid is its total
    minus that balance (never below 0 or above the total). Marks the statement confirmed and sets its
    status. Returns (amount_paid, new_status)."""
    with get_conn() as conn:
        (total,) = conn.execute("SELECT total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        paid = round(min(max(total - carried_over, 0.0), total), 2)
        unpaid_next = round(carried_over, 2)
        status = "Paid" if unpaid_next <= 0.005 else ("Partially Paid" if paid > 0.005 else "Unpaid")
        conn.execute(
            "UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ?, paid_confirmed = 1 WHERE cc_statement_id = ?",
            (paid, unpaid_next, status, cc_statement_id),
        )
        conn.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_PAYMENT_CONFIRMED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"paid {php(paid)}, carried over {php(unpaid_next)} (from the next statement)"),
        )
        conn.commit()
    return paid, status


def get_default_statement_month(card_id: int) -> tuple[int, int]:
    """(year, month) to pre-select for this card's NEXT statement: the month after the latest
    statement month already recorded (a statement with no explicit month counts as its due date's
    month), rolling December over to January of the following year automatically. A card with no
    statements yet starts at the current month."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(COALESCE(statement_month, SUBSTR(due_date, 1, 7))) FROM cc_statements WHERE card_id = ?", (card_id,)
        ).fetchone()
    if not row or not row[0]:
        today = date.today()
        return today.year, today.month
    year, month = int(row[0][:4]), int(row[0][5:7])
    return (year + 1, 1) if month == 12 else (year, month + 1)


def add_cc_statement(
    card_id: int, due_date: date, previous_balance: float, new_charges: float,
    interest_charged: float, amount_paid: float, statement_month: str | None = None, paid_confirmed: int = 1,
) -> int:
    """Records a CC statement with a real rolling balance (migration 027, 2026-09-15):
    total_amount_due is computed, not typed in, as previous_balance + new_charges +
    interest_charged; whatever amount_paid doesn't cover becomes unpaid_balance_next, which
    the next statement's previous_balance auto-fills from (get_last_cc_statement_unpaid_balance).
    Status reflects the same computation up front, then stays subject to whatever settles it
    later (an edit to the statement, etc.) — same as every other status field in this app."""
    total_amount_due = round(previous_balance + new_charges + interest_charged, 2)
    unpaid_balance_next = round(total_amount_due - amount_paid, 2)
    if unpaid_balance_next <= 0.005:
        status = "Paid"
    elif amount_paid > 0.005:
        status = "Partially Paid"
    else:
        status = "Unpaid"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO cc_statements
               (card_id, statement_month, due_date, total_amount_due, previous_balance, new_charges, interest_charged,
                amount_paid, unpaid_balance_next, status, paid_confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (card_id, statement_month, str(due_date), total_amount_due, previous_balance, new_charges, interest_charged,
             amount_paid, unpaid_balance_next, status, int(paid_confirmed)),
        )
        conn.commit()
        return cur.lastrowid


def _is_latest_cc_statement(cur: sqlite3.Cursor, cc_statement_id: int, card_id: int) -> bool:
    """A statement's unpaid_balance_next auto-fills the NEXT statement's previous_balance
    (get_last_cc_statement_unpaid_balance) -- editing or deleting anything but the latest one for
    its card would leave that later statement's previous_balance chained to a now-stale number.
    Restricting edit/delete to the latest keeps the rolling balance always consistent."""
    latest_id = cur.execute(
        "SELECT cc_statement_id FROM cc_statements WHERE card_id = ? ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    ).fetchone()[0]
    return latest_id == cc_statement_id


def edit_cc_statement(
    cc_statement_id: int, due_date: date, previous_balance: float, new_charges: float,
    interest_charged: float, amount_paid: float, statement_month: str | None = None,
) -> tuple[bool, str]:
    """In-place edit of a CC statement's rolling-balance figures -- only allowed on the latest
    statement for its card (see _is_latest_cc_statement), same recompute as add_cc_statement()."""
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT card_id FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        if not row:
            return False, "Statement not found."
        card_id = row[0]
        if not _is_latest_cc_statement(cur, cc_statement_id, card_id):
            return False, "Only the most recent statement for a card can be edited — a later statement already carried its balance forward."
        total_amount_due = round(previous_balance + new_charges + interest_charged, 2)
        unpaid_balance_next = round(total_amount_due - amount_paid, 2)
        if unpaid_balance_next <= 0.005:
            status = "Paid"
        elif amount_paid > 0.005:
            status = "Partially Paid"
        else:
            status = "Unpaid"
        cur.execute(
            """UPDATE cc_statements SET due_date = ?, total_amount_due = ?, previous_balance = ?, new_charges = ?,
                      interest_charged = ?, amount_paid = ?, unpaid_balance_next = ?, status = ?, paid_confirmed = 1,
                      statement_month = COALESCE(?, statement_month)
               WHERE cc_statement_id = ?""",
            (str(due_date), total_amount_due, previous_balance, new_charges, interest_charged,
             amount_paid, unpaid_balance_next, status, statement_month, cc_statement_id),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_STATEMENT_EDITED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"due {due_date}, total {php(total_amount_due)}"),
        )
        conn.commit()
    return True, "Statement updated."


def delete_cc_statement(cc_statement_id: int) -> tuple[bool, str]:
    """Removes a CC statement entirely -- only allowed on the latest statement for its card, same
    guard as edit_cc_statement(), so a mistakenly-added statement can be undone cleanly."""
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT card_id, due_date, total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)
        ).fetchone()
        if not row:
            return False, "Statement not found."
        card_id, due_date, total_amount_due = row
        if not _is_latest_cc_statement(cur, cc_statement_id, card_id):
            return False, "Only the most recent statement for a card can be deleted — a later statement already carried its balance forward."
        cur.execute("DELETE FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_STATEMENT_DELETED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"Deleted statement due {due_date} ({php(total_amount_due)})"),
        )
        conn.commit()
    return True, "Statement deleted."


def get_blocked_dates_for_month(year: int, month: int) -> dict:
    """{date: {'label': str, 'kind': 'holiday'|'custom'}} for every greyed-out date in one
    calendar month — Philippine holidays (seeded, migration 024) and any date the user has
    marked as a custom no-issue day, both stored in calendar_blocked_dates."""
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    df = q("SELECT blocked_date, label, kind FROM calendar_blocked_dates WHERE blocked_date >= ? AND blocked_date < ?", (str(start), str(end)))
    return {datetime.strptime(r["blocked_date"], "%Y-%m-%d").date(): {"label": r["label"], "kind": r["kind"]} for _, r in df.iterrows()}


def set_blocked_date(d: date, label: str, kind: str = "custom") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calendar_blocked_dates (blocked_date, label, kind) VALUES (?, ?, ?) "
            "ON CONFLICT(blocked_date) DO UPDATE SET label = excluded.label, kind = excluded.kind",
            (str(d), label or None, kind),
        )
        conn.commit()


def remove_blocked_date(d: date) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM calendar_blocked_dates WHERE blocked_date = ?", (str(d),))
        conn.commit()


# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "slicks_logo.jpg")   # absolute: works whatever folder the app was started from

# Sandbox vs live indicator (2026-09-17): both instances run the exact same code, so with nothing
# else distinguishing them, the only visible difference used to be the port number in the URL --
# an easy mistake to make with two tabs open. Detected from AP_DB_PATH itself rather than a
# separate flag, so it can never drift out of sync with which database is actually behind this
# instance.
IS_SANDBOX = "TEST" in os.path.basename(DB_PATH).upper()
_PAGE_TITLE = ("🧪 SANDBOX — " if IS_SANDBOX else "") + "Slick's Tire Supply — AP Disbursement"


@st.cache_resource
def _get_app_start_time() -> datetime:
    """Computed once per process, cached for that process's whole lifetime (st.cache_resource,
    unlike st.cache_data, doesn't expire or get cleared by "Refresh data") -- so this only ever
    changes when the app is actually restarted, not on an ordinary page interaction."""
    return datetime.now()


WHATS_NEW_PATH = "WHATS_NEW.md"


def _read_whats_new() -> str:
    """Full changelog text, HTML-comment header stripped -- backs the permanent Changelog
    section in Admin & audit. Missing file (e.g. a fresh checkout without it yet) degrades to a
    plain message instead of crashing the page."""
    if not os.path.exists(WHATS_NEW_PATH):
        return "No changelog yet."
    with open(WHATS_NEW_PATH, encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"<!--.*?-->\s*", "", text, count=1, flags=re.DOTALL).strip()


def _read_whats_new_latest_section() -> str | None:
    """Just the newest "## YYYY-MM-DD" section, for the one-time popup -- None if the file is
    missing or empty so the caller can just skip showing anything."""
    full = _read_whats_new()
    if full == "No changelog yet.":
        return None
    sections = re.split(r"(?=^## )", full, flags=re.MULTILINE)
    sections = [s.strip() for s in sections if s.strip().startswith("## ")]
    return sections[0] if sections else None


@st.cache_resource
def _whats_new_popup_tracker() -> dict:
    """A mutable object living for this process's whole lifetime (unlike st.session_state, which
    is per browser tab/session) -- so the "What's new" popup fires exactly once per actual
    restart, for whoever happens to load the page first, and never again until the next restart
    -- not once per new tab someone opens against an already-running process."""
    return {"shown": False}


st.set_page_config(page_title=_PAGE_TITLE, page_icon=LOGO_PATH, layout="wide")

# Startup check: if the program is newer than the database, say so plainly instead of failing on a missing column.
_schema_stamp = os.path.getmtime(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql"))
if st.session_state.get("_schema_checked") != _schema_stamp:   # re-checks whenever a newer schema.sql arrives, even mid-session
    _pending_updates = pending_schema_updates(DB_PATH)
    if _pending_updates:
        st.error("The database needs a quick update before the system can run.")
        _nl = chr(10)
        st.markdown(
            "New features were added that need a small change to the database. Nothing is lost, and it takes a few seconds:" + _nl + _nl
            + "1. Close this browser tab." + _nl
            + "2. Double-click **Update Database** (`update_database.bat`) in the project folder. It backs up your data first, updates it, and restarts the system." + _nl + _nl
            + "What is missing: " + ", ".join(_pending_updates)
        )
        st.stop()
    st.session_state["_schema_checked"] = _schema_stamp
try:
    st.logo(LOGO_PATH, size="large")
except Exception:
    pass   # a missing/unreadable logo file (e.g. Google Drive not synced yet) must never stop the app from opening

_whats_new_tracker = _whats_new_popup_tracker()
# st.runtime.exists() is False during a bare `import app` (pytest's conftest.py, or any other
# script-mode import) -- st.dialog needs a real, running Streamlit server behind it, and app.py's
# top-level code (every `with tab_X:` block) already runs unconditionally on import regardless of
# fixtures, same reason get_check_print_batches() needed guarding against test collection earlier.
if st.runtime.exists() and not _whats_new_tracker["shown"]:
    _whats_new_tracker["shown"] = True
    _latest_whats_new = _read_whats_new_latest_section()
    if _latest_whats_new:
        @st.dialog("What's new")
        def _show_whats_new_dialog(text: str) -> None:
            st.markdown(text)
            if st.button("Got it", width="stretch"):
                st.rerun()
        _show_whats_new_dialog(_latest_whats_new)

tab_labels = [
    ":material/dashboard: Dashboard",
    ":material/query_stats: Analytics",
    ":material/contacts: Creditor Master",
    ":material/description: Order Statement",
    ":material/handshake: Lenders",
    ":material/account_balance: Bank loans",
    ":material/credit_card: Credit cards",
    ":material/receipt_long: Checks & checkbook",
    ":material/summarize: Reports",
    ":material/admin_panel_settings: Admin & audit",
]
TAB_ORDER_STATEMENT = tab_labels[3]
TAB_LENDERS = tab_labels[4]
TAB_BANK_LOANS = tab_labels[5]


def _jump(tab_label: str, **state) -> None:
    """Sets the session_state that drives the sidebar quick-search's "single match auto-jumps"
    behavior. Runs from inside the sidebar block, which executes before the tab/sub-tab widgets
    it targets are instantiated later in this same script pass -- so plain assignment here is
    enough (this is the "set session_state before the widget renders" case, not the "set after
    instantiation" case that needs the _os_mode_next-style staging trick elsewhere in this app)."""
    st.session_state["main_tab"] = tab_label
    for k, v in state.items():
        st.session_state[k] = v


with st.sidebar:
    park_draft_slot = st.empty()      # "checks typed but not parked" reminder, filled at the end of the script
    if IS_SANDBOX:
        st.markdown(
            "<div style='background-color:#FFA500;color:#000;text-align:center;"
            "font-weight:bold;padding:8px;border-radius:4px;margin-bottom:8px;'>"
            "🧪 SANDBOX — test data, not real</div>",
            unsafe_allow_html=True,
        )
    st.image(LOGO_PATH, width=180)
    st.caption("AP Disbursement System" + (" (SANDBOX)" if IS_SANDBOX else ""))
    bank_df = q("SELECT * FROM bank_accounts WHERE bank_account_id = ?", (PRIMARY_BANK_ACCOUNT_ID,))
    if not bank_df.empty:
        bank = bank_df.iloc[0]
        st.caption(f"{bank['bank_name']} · Branch {bank['branch_code']}")
    # "Did my restart actually pick up the change" indicator (2026-09-17, for a non-coding user
    # who can't verify that by reading code): st.cache_resource holds its value for the life of
    # this Python process and only recomputes on a fresh one, so this timestamp changes exactly
    # when restart_live.bat/run_test_streamlit.bat actually restarts the app -- never on an
    # ordinary click/rerun within the same still-running process.
    st.caption(f"App started: {_get_app_start_time().strftime('%b %d, %Y %I:%M %p')}")

    st.divider()
    st.caption("Jump to a check, invoice, batch, loan note, or bank loan")
    search_term = st.text_input(
        "Quick search", key="quick_search_term",
        placeholder="Check #, invoice ref, batch/LN/loan ref, or name",
        label_visibility="collapsed",
    )

    if search_term.strip():
        result = search_records(search_term)
        kind, matches = result["kind"], result["matches"]

        if kind == "none":
            st.warning(f"No check or invoice found matching '{search_term}'.")

        elif kind == "spoiled":
            for m in matches:
                extra = f" ({m['reason']})" if m["reason"] else ""
                st.info(f"Check {m['check_number']} is logged as spoiled — no batch attached.{extra}")

        elif kind == "check":
            st.caption(f"{len(matches)} check(s) match:")
            for m in matches:
                label = f"{m['check_number']} · {m['creditor_name']} · {php(m['amount'])} · {m['status']}"
                if st.button(label, key=f"jump_check_{m['check_id']}", width="stretch"):
                    if m["statement_id"] is not None:
                        _jump(
                            TAB_ORDER_STATEMENT,
                            selected_statement_id=m["statement_id"], os_mode="🔍 Browse Batches", os_subtab="Checks",
                        )
                    else:
                        _jump(
                            TAB_LENDERS,
                            selected_loan_note_id=m["loan_note_id"], ln_mode="🔍 Browse Loan Notes", ln_subtab="Checks",
                        )

        elif kind == "invoice":
            st.caption(f"{len(matches)} invoice(s) match:")
            for m in matches:
                label = f"{m['doc_ref']} · {m['creditor_name']} · {php(m['amount'])} · {m['statement_ref']}"
                if st.button(label, key=f"jump_inv_{m['invoice_id']}", width="stretch"):
                    _jump(
                        TAB_ORDER_STATEMENT,
                        selected_statement_id=m["statement_id"], os_mode="🔍 Browse Batches", os_subtab="Invoices",
                    )

        elif kind == "ref":
            st.caption(f"{len(matches)} record(s) match:")
            for m in matches:
                label = f"[{m['type']}] {m['ref']} · {m['payee']} · {php(m['amount'])} · {m['status']}"
                if st.button(label, key=f"jump_ref_{m['type']}_{m['id']}", width="stretch"):
                    if m["type"] == "Order Statement":
                        _jump(TAB_ORDER_STATEMENT, selected_statement_id=m["id"], os_mode="🔍 Browse Batches")
                    elif m["type"] == "Loan Note":
                        _jump(TAB_LENDERS, selected_loan_note_id=m["id"], ln_mode="🔍 Browse Loan Notes")
                    else:
                        _jump(TAB_BANK_LOANS, selected_bank_loan_id=m["id"], bl_mode="🔍 Browse Bank Loans")

    st.divider()
    # Mini calendar grid, not a flat list (2026-09-17, revised per user's actual purpose: a quick
    # visual reference for which dates are already loaded while typing in a check date on the
    # Order Statement/Lenders/Checks & checkbook tabs). The sidebar is the one part of this app
    # that stays on screen regardless of which tab is active -- a modal/dialog popup would close
    # the moment you switched tabs, so this is the only surface that actually works for that.
    st.caption("Mini calendar — for reference while issuing checks")
    # Month/year pickers + prev/next arrows. The arrows change the pickers through on_click callbacks, NOT by calling
    # st.rerun() from here: this sidebar is drawn before the main tabs, so an early st.rerun() made Streamlit forget
    # which tab was open and dropped you back on the Dashboard every time (2026-09-26). A callback runs before the page
    # is redrawn, so the tabs keep their state.
    side_cal_years = list(range(date.today().year - 5, date.today().year + 11))
    side_cal_month_names = [date(2000, m, 1).strftime("%b") for m in range(1, 13)]
    if st.session_state.get("side_cal_year_pick") not in side_cal_years:
        st.session_state["side_cal_year_pick"] = date.today().year
    if st.session_state.get("side_cal_month_pick") not in range(1, 13):
        st.session_state["side_cal_month_pick"] = date.today().month

    def _side_cal_step(delta: int) -> None:
        m, y = st.session_state["side_cal_month_pick"] + delta, st.session_state["side_cal_year_pick"]
        if m < 1:
            m, y = 12, y - 1
        elif m > 12:
            m, y = 1, y + 1
        if y in side_cal_years:
            st.session_state["side_cal_month_pick"], st.session_state["side_cal_year_pick"] = m, y

    side_cal_prev_col, side_cal_month_col, side_cal_year_col, side_cal_next_col = st.columns([1, 2.4, 2.2, 1], vertical_alignment="center")
    side_cal_prev_col.button("◀", key="side_cal_prev", on_click=_side_cal_step, args=(-1,))
    def _side_cal_month_picked() -> None:
        st.session_state["side_cal_year_pick"] = prospective_year(st.session_state["side_cal_month_pick"])

    side_cal_month_col.selectbox("Month", options=list(range(1, 13)), format_func=lambda m: side_cal_month_names[m - 1],
                                 key="side_cal_month_pick", label_visibility="collapsed", on_change=_side_cal_month_picked)
    side_cal_year_col.selectbox("Year", options=side_cal_years, key="side_cal_year_pick", label_visibility="collapsed")
    side_cal_next_col.button("▶", key="side_cal_next", on_click=_side_cal_step, args=(1,))
    side_cal_year, side_cal_month = st.session_state["side_cal_year_pick"], st.session_state["side_cal_month_pick"]

    # Single letters ("M","T","W","T","F","S","S") collided -- Tue/Thu both became "T" and
    # Sat/Sun both became "S", merging those column pairs into one on the x-axis instead of
    # keeping 7 distinct days. Two-letter codes stay unique while still fitting the narrow
    # sidebar width.
    side_cal_weekday_names = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
    side_cal_rows = []
    for side_cal_week_idx, side_cal_week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(side_cal_year, side_cal_month)):
        for side_cal_day_idx, side_cal_day_num in enumerate(side_cal_week):
            if side_cal_day_num == 0:
                continue
            side_cal_rows.append({
                "date": date(side_cal_year, side_cal_month, side_cal_day_num),
                "week": side_cal_week_idx, "weekday": side_cal_weekday_names[side_cal_day_idx], "day": side_cal_day_num,
            })
    side_cal_grid_df = pd.DataFrame(side_cal_rows)
    side_cal_daily_df = get_payment_schedule_daily_totals()
    if not side_cal_daily_df.empty:
        side_cal_daily_df = side_cal_daily_df.copy()
        side_cal_daily_df["actual_payment_date"] = pd.to_datetime(side_cal_daily_df["actual_payment_date"]).dt.date
    side_cal_merged_df = (
        side_cal_grid_df.merge(side_cal_daily_df, left_on="date", right_on="actual_payment_date", how="left")
        if not side_cal_daily_df.empty else side_cal_grid_df.assign(total_amount=0.0, num_items=0)
    )
    side_cal_merged_df["total_amount"] = side_cal_merged_df["total_amount"].fillna(0.0)
    side_cal_merged_df["num_items"] = side_cal_merged_df["num_items"].fillna(0).astype(int)

    # Both layers must share the exact same Axis object, not one Axis(orient="top") and one
    # axis=None (2026-09-17 -- this was the actual bug, not a missing-label rendering issue as
    # it first looked): when Altair layers two charts whose axis specs for the same channel
    # disagree, it has to resolve the conflict, and it silently dropped the labels entirely
    # rather than keeping either version. The main Dashboard calendar avoids this by giving every
    # layer the identical axis object -- matched here.
    side_cal_weekday_axis = alt.Axis(orient="top", labelFontSize=9, labelAngle=0)
    # Marked days (2026-09-26, per user): the same grey days the Dashboard's payment calendar shows -- Philippine holidays
    # and any date marked as blocked (calendar_blocked_dates) -- drawn as a flat grey layer over the heat colour.
    side_cal_blocked = get_blocked_dates_for_month(side_cal_year, side_cal_month)
    side_cal_merged_df["is_blocked"] = side_cal_merged_df["date"].isin(side_cal_blocked.keys())
    side_cal_merged_df["blocked_label"] = side_cal_merged_df["date"].apply(
        lambda d: side_cal_blocked.get(d, {}).get("label") or side_cal_blocked.get(d, {}).get("kind", "").title())
    side_cal_normal_df = side_cal_merged_df[~side_cal_merged_df["is_blocked"]]
    side_cal_blocked_df = side_cal_merged_df[side_cal_merged_df["is_blocked"]]
    side_cal_tooltip = [alt.Tooltip("date:T", title="Date"), alt.Tooltip("total_amount:Q", title="Total", format=",.2f"), alt.Tooltip("num_items:Q", title="Items")]
    side_cal_heat = alt.Chart(side_cal_normal_df).mark_rect(stroke="white", strokeWidth=1, cornerRadius=3).encode(
        x=alt.X("weekday:N", title=None, sort=side_cal_weekday_names, axis=side_cal_weekday_axis),
        y=alt.Y("week:O", title=None, axis=None),
        color=alt.Color("total_amount:Q", scale=alt.Scale(scheme="blues", type="sqrt"), legend=None),
        tooltip=side_cal_tooltip,
    )
    side_cal_grey = alt.Chart(side_cal_blocked_df).mark_rect(stroke="white", strokeWidth=1, cornerRadius=3, color="#C4C4C4").encode(
        x=alt.X("weekday:N", title=None, sort=side_cal_weekday_names, axis=side_cal_weekday_axis),
        y=alt.Y("week:O", title=None, axis=None),
        tooltip=side_cal_tooltip + [alt.Tooltip("blocked_label:N", title="Marked")],
    )
    side_cal_dark_threshold = side_cal_merged_df["total_amount"].max() * 0.5
    side_cal_text = alt.Chart(side_cal_merged_df).mark_text(baseline="middle", fontSize=9).encode(
        x=alt.X("weekday:N", sort=side_cal_weekday_names, axis=side_cal_weekday_axis), y=alt.Y("week:O", axis=None),
        text="day:N",
        color=(
            alt.when(alt.datum.is_blocked).then(alt.value("#404040"))
            .when(alt.datum.total_amount > side_cal_dark_threshold).then(alt.value("white"))
            .otherwise(alt.value("#333333"))
        ),
        tooltip=side_cal_tooltip,
    )
    st.altair_chart((side_cal_heat + side_cal_grey + side_cal_text).properties(height=150), width="stretch")
    st.caption("Hover a day for its total. Grey = holiday or marked date. Full calendar: Dashboard tab.")

    st.divider()
    st.button("Refresh data", icon=":material/refresh:", on_click=lambda: st.cache_data.clear(), width="stretch")

(
    tab_dashboard,
    tab_analytics,
    tab_creditor_master,
    tab_suppliers,
    tab_lenders,
    tab_bank_loans,
    tab_credit_cards,
    tab_checks,
    tab_reports,
    tab_admin,
) = st.tabs(tab_labels, key="main_tab", default=tab_labels[0], on_change="rerun")

# --------------------------------------------------------------------------
# Tab 1: Dashboard
# --------------------------------------------------------------------------
with tab_dashboard:
    st.header("Overview")

    # "No statement on file yet" (a card with zero statement history, typically not in active use)
    # is routine, not urgent -- a legacy import can leave 40+ of these, which used to fire this
    # banner in red (st.error) every single day. Only "Possibly missing a statement" (a card that
    # HAD statements but seems to have skipped a cycle) is a real, actionable risk worth an error.
    dash_gap_alerts_df = get_statement_gap_alerts()
    dash_missing_recent_df = dash_gap_alerts_df[dash_gap_alerts_df["alert"] == "Possibly missing a statement"] if not dash_gap_alerts_df.empty else dash_gap_alerts_df
    dash_never_had_df = dash_gap_alerts_df[dash_gap_alerts_df["alert"] == "No statement on file yet"] if not dash_gap_alerts_df.empty else dash_gap_alerts_df
    if not dash_missing_recent_df.empty:
        st.error(
            f":material/report: {len(dash_missing_recent_df)} credit card statement(s) may have been overlooked — "
            + "; ".join(f"{r['card_name']} ({r['alert'].lower()})" for _, r in dash_missing_recent_df.iterrows())
            + ". Check the Credit cards tab."
        )
    if not dash_never_had_df.empty:
        st.caption(f":material/info: {len(dash_never_had_df)} card(s) have never had a statement recorded — routine for cards not yet in active use. See Credit cards tab.")

    dash_checkbook_alerts_df = get_checkbook_alerts()
    if not dash_checkbook_alerts_df.empty:
        st.error(
            f":material/report: {len(dash_checkbook_alerts_df)} checkbook(s) have unaccounted checks — "
            + "; ".join(
                f"{r['booklet_code']} ({r['missing_gaps_count']} skipped, {r['duplicate_numbers_count']} duplicate)"
                for _, r in dash_checkbook_alerts_df.iterrows()
            )
            + ". Check the Admin & audit tab (Check booklets)."
        )

    # Supplier concentration alert (2026-09-17, per user request): names the single largest
    # supplier relationship rather than a blended top-5 percentage, since that's the one whose
    # disruption would actually hurt -- 25% is a common rule-of-thumb concentration-risk
    # threshold, not derived from anything this business told me specifically.
    SUPPLIER_DEPENDENCY_ALERT_THRESHOLD_PCT = 25.0
    dash_supplier_dependency = get_top_supplier_dependency()
    if dash_supplier_dependency and dash_supplier_dependency["pct"] >= SUPPLIER_DEPENDENCY_ALERT_THRESHOLD_PCT:
        st.warning(
            f":material/warning: Supplier concentration risk — {dash_supplier_dependency['name']} alone accounts for "
            f"{dash_supplier_dependency['pct']:.0f}% of total outstanding supplier payable "
            f"({php(dash_supplier_dependency['amount'])} of {php(dash_supplier_dependency['total_outstanding'])}). "
            "See Analytics → Supplier concentration (Pareto)."
        )

    ap_outstanding = 0.0
    # Supplier + Lender: not-yet-cleared checks (same "still owed" convention as
    # get_uncleared_checks_report() / v_effective_checks -- a check counts as effectively
    # cleared once its check_date arrives, since this app has no bank reconciliation feature).
    # invoices.status/loan_notes.status were the original source here, but those only get
    # populated by the app's own batch/loan-note workflow -- Phase 8c's legacy migration
    # (Sep 2026) intentionally skipped reconstructing itemized invoices and left every migrated
    # lender at loan_notes.status='Pending Setup', so both went to 0 despite ~P39.6M of real,
    # still-outstanding legacy checks sitting in checks_issued. Querying checks_issued directly
    # counts that money regardless of which path (legacy import or normal workflow) wrote it.
    supplier_lender_uncleared = q(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE status = 'Active' AND check_date > DATE('now', 'localtime')"
    )
    loan_pending = q("SELECT COALESCE(SUM(amount), 0) AS total FROM bank_loan_installments WHERE due_date > DATE('now', 'localtime') AND bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)")
    # unpaid_balance_next-aware (migration 027): a statement carrying a real rolling balance
    # still counts as outstanding past its due date; a legacy statement (unpaid_balance_next
    # always 0) falls through to the original due-date-only rule unchanged.
    cc_unpaid = q(
        f"""SELECT COALESCE(SUM({cc_owed_case('cs')}), 0) AS total
           FROM cc_statements cs WHERE cs.status NOT IN ('Paid', 'Cancelled')"""
    )
    ap_outstanding = float(supplier_lender_uncleared["total"][0]) + float(loan_pending["total"][0]) + float(cc_unpaid["total"][0])

    active_checks = q("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE status = 'Active'")
    cc_alerts = q("SELECT * FROM v_cc_due_alerts")
    critical_alerts = cc_alerts[cc_alerts["urgency_status"] == "CRITICAL (0-3 Days)"] if not cc_alerts.empty else cc_alerts
    # Same 180-day cutoff v_effective_checks (schema.sql) already uses to call a check "Stale" --
    # still Active, but old enough that it was probably lost, never cashed, or just never marked
    # Cleared/Cancelled, rather than genuinely still in flight.
    stale_checks = q(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE status = 'Active' AND check_date < DATE('now', '-180 days')"
    )
    pending_checks_df = get_pending_check_run()
    pending_week_df = (
        pending_checks_df[pd.to_datetime(pending_checks_df["check_date"]).dt.date <= (date.today() + timedelta(days=7))]
        if not pending_checks_df.empty else pending_checks_df
    )

    with st.container(horizontal=True):
        st.metric("Total AP outstanding", php(ap_outstanding), border=True)
        st.metric("Active checks in circulation", f"{int(active_checks['n'][0])} · {php(active_checks['total'][0])}", border=True)
        st.metric(
            "Needs attention", str(len(critical_alerts) + len(dash_missing_recent_df) + len(dash_checkbook_alerts_df)),
            border=True,
            help=f"{len(critical_alerts)} critical credit card alert(s), {len(dash_missing_recent_df)} possibly missed CC statement(s), {len(dash_checkbook_alerts_df)} checkbook issue(s) -- see the banners above, the Credit cards tab, and the Admin & audit tab. ({len(dash_never_had_df)} more card(s) have never had a statement recorded — routine, not counted here.)",
        )

    cash_7d = get_upcoming_cash_obligations(7)
    cash_30d = get_upcoming_cash_obligations(30)
    concentration = get_supplier_concentration(limit=5)
    with st.container(horizontal=True):
        st.metric(
            "Due in next 7 days (all tracks)", php(cash_7d), border=True,
            help="Supplier/Lender checks, Bank Loan installments, and Credit Card statements still owed and due within 7 days — same scope as the Schedule of Payments report.",
        )
        st.metric(
            "Due in next 30 days (all tracks)", php(cash_30d), border=True,
            help="Same scope as above, 30-day window.",
        )
        st.metric(
            "Top 5 suppliers' share of outstanding",
            f"{concentration['pct']:.0f}%" if concentration["total_outstanding"] > 0 else "—",
            border=True,
            help=f"{php(concentration['top_total'])} of {php(concentration['total_outstanding'])} total outstanding supplier payable sits with the 5 largest suppliers by current exposure.",
        )

    with st.container(horizontal=True):
        st.metric(
            "Stale checks", f"{int(stale_checks['n'][0])} · {php(stale_checks['total'][0])}", border=True,
            help="Still marked Active but dated more than 180 days ago -- same cutoff this app already uses elsewhere to call a check effectively stale. May have been lost, never cashed, or just never marked Cleared/Cancelled.",
        )
        st.metric(
            "Checks pending print/signature this week",
            f"{len(pending_week_df)} · {php(pending_week_df['amount'].sum() if not pending_week_df.empty else 0.0)}",
            border=True,
            help="Already issued but not yet printed, dated within the next 7 days. Full list: Checks & checkbook → Checks to print.",
        )

    # TRIAL C (2026-09-14): calendar heatmap — hover a day to see that day's all-tracks total,
    # instead of picking a date (Trial A) or reading it off the issue-check grid (Trial B). A
    # third option to compare, not a replacement — drop this block if it doesn't win.
    with st.container(border=True):
        st.subheader("Payment calendar (all tracks)")
        st.caption("Hover a day for its total. Darker = more payable that day.")
        cal_year = st.session_state.get("cal_year", date.today().year)
        cal_month = st.session_state.get("cal_month", date.today().month)
        cal_month_names = [date(2000, m, 1).strftime("%B") for m in range(1, 13)]
        # Wide enough to cover the postdated-check horizon this business actually schedules
        # against (the payables trend chart above already runs years out), padded a bit past
        # today on both sides rather than tied to whatever data happens to exist right now.
        cal_year_options = list(range(date.today().year - 5, date.today().year + 11))
        if cal_year not in cal_year_options:
            cal_year_options = sorted(set(cal_year_options) | {cal_year})

        cal_col_prev, cal_col_month_pick, cal_col_year_pick, cal_col_next = st.columns([1, 2, 1.4, 1])
        # Both arrow buttons are checked here, before the month/year selectboxes below are
        # instantiated (Streamlit raises a StreamlitAPIException if session_state[key] is set
        # for a widget's key after that widget has already been instantiated in the same script
        # run). `with cal_col_next:` still renders the button in the rightmost column even
        # though its code now runs before the middle columns' widgets are declared -- column
        # placement follows the `st.columns(...)` call, not code order.
        with cal_col_prev:
            if st.button("◀", key="cal_prev_month"):
                cal_month -= 1
                if cal_month < 1:
                    cal_month, cal_year = 12, cal_year - 1
                st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month
                # The month/year picker widgets below keep their own state under their own keys
                # once set -- their `index=` argument is only consulted before that state exists,
                # so the prev/next buttons have to push the new value into those keys directly or
                # the pickers would silently stay on the old month/year after a prev/next click.
                st.session_state["cal_month_pick"] = cal_month_names[cal_month - 1]
                st.session_state["cal_year_pick"] = cal_year
                st.rerun()
        with cal_col_next:
            if st.button("▶", key="cal_next_month"):
                cal_month += 1
                if cal_month > 12:
                    cal_month, cal_year = 1, cal_year + 1
                st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month
                st.session_state["cal_month_pick"] = cal_month_names[cal_month - 1]
                st.session_state["cal_year_pick"] = cal_year
                st.rerun()
        def _cal_month_picked() -> None:
            st.session_state["cal_year_pick"] = prospective_year(cal_month_names.index(st.session_state["cal_month_pick"]) + 1)

        with cal_col_month_pick:
            cal_picked_month_name = st.selectbox(
                "Month", cal_month_names, index=cal_month - 1, key="cal_month_pick", label_visibility="collapsed", on_change=_cal_month_picked,
            )
            cal_month = cal_month_names.index(cal_picked_month_name) + 1
        with cal_col_year_pick:
            cal_year = st.selectbox(
                "Year", cal_year_options, index=cal_year_options.index(cal_year), key="cal_year_pick", label_visibility="collapsed",
            )
        st.session_state["cal_year"], st.session_state["cal_month"] = cal_year, cal_month

        weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        cal_rows = []
        for week_idx, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(cal_year, cal_month)):
            for day_idx, day_num in enumerate(week):
                if day_num == 0:
                    continue
                cal_rows.append({"date": date(cal_year, cal_month, day_num), "week": week_idx, "weekday": weekday_names[day_idx], "day": day_num})
        cal_grid_df = pd.DataFrame(cal_rows)

        cal_daily = get_payment_schedule_daily_totals()
        if not cal_daily.empty:
            cal_daily = cal_daily.copy()
            cal_daily["actual_payment_date"] = pd.to_datetime(cal_daily["actual_payment_date"]).dt.date
        cal_merged = cal_grid_df.merge(cal_daily, left_on="date", right_on="actual_payment_date", how="left") if not cal_daily.empty else cal_grid_df.assign(total_amount=0.0, num_items=0)
        cal_merged["total_amount"] = cal_merged["total_amount"].fillna(0.0)
        cal_merged["num_items"] = cal_merged["num_items"].fillna(0).astype(int)

        # Greyed-out dates (2026-09-14): Philippine holidays (seeded, migration 024) and any
        # custom date the user has marked — both stored in calendar_blocked_dates, rendered as
        # a flat grey layer that overrides the heat color for just those cells.
        cal_blocked = get_blocked_dates_for_month(cal_year, cal_month)
        cal_merged["is_blocked"] = cal_merged["date"].isin(cal_blocked.keys())
        cal_merged["blocked_label"] = cal_merged["date"].apply(lambda d: cal_blocked.get(d, {}).get("label") or cal_blocked.get(d, {}).get("kind", "").title())
        cal_normal_df = cal_merged[~cal_merged["is_blocked"]]
        cal_blocked_df = cal_merged[cal_merged["is_blocked"]]

        cal_tooltip = [
            alt.Tooltip("date:T", title="Date"),
            alt.Tooltip("total_amount:Q", title="Total payable", format=",.2f"),
            alt.Tooltip("num_items:Q", title="Items"),
        ]
        cal_tooltip_blocked = cal_tooltip + [alt.Tooltip("blocked_label:N", title="Marked")]
        # Transposed (2026-09-17) to the conventional calendar layout -- weekday across the top
        # as column headers, week running down as rows -- rather than weekday-as-row/week-as-
        # column, which read more like a generic pivot table than a calendar at a glance.
        cal_weekday_axis = alt.Axis(orient="top", labelAngle=0)
        cal_heat = alt.Chart(cal_normal_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4).encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            color=alt.Color("total_amount:Q", title="Payable", scale=alt.Scale(scheme="blues", type="sqrt"), legend=None),
            tooltip=cal_tooltip,
        )
        cal_grey = alt.Chart(cal_blocked_df).mark_rect(stroke="white", strokeWidth=2, cornerRadius=4, color="#C4C4C4").encode(
            x=alt.X("weekday:N", title=None, sort=weekday_names, axis=cal_weekday_axis),
            y=alt.Y("week:O", title=None, axis=None),
            tooltip=cal_tooltip_blocked,
        )
        cal_dark_threshold = cal_merged["total_amount"].max() * 0.5  # 0 when every day is 0 -> "> 0" is False for all -> black text throughout
        cal_text = alt.Chart(cal_merged).mark_text(baseline="middle", fontSize=12).encode(
            x=alt.X("weekday:N", sort=weekday_names, axis=cal_weekday_axis), y=alt.Y("week:O", axis=None),
            text="day:N",
            color=(
                alt.when(alt.datum.is_blocked).then(alt.value("#404040"))
                .when(alt.datum.total_amount > cal_dark_threshold).then(alt.value("white"))
                .otherwise(alt.value("black"))
            ),  # Altair 6 dropped support for nesting alt.condition() calls -- alt.when()/.then()/.otherwise() is the current chained-condition API.
            tooltip=cal_tooltip,  # the text glyph sits on top of the rect per-pixel, so it needs its
            # own copy of the same tooltip fields -- otherwise hovering exactly on a day number shows
            # this mark's own bare x/y/text encoding (week/weekday/day) instead of the rect's tooltip.
        )
        # Select on the plain integer "day" field, not the temporal "date" field: Vega-Lite
        # returns a temporal selection as milliseconds-since-epoch in UTC, and converting that
        # back with pandas landed one calendar day off the actually-clicked cell (confirmed
        # live: clicking the 15th came back as the 14th) -- a real UTC/local timezone mismatch,
        # not worth fighting when "day" + the already-known cal_year/cal_month reconstructs the
        # exact date with no serialization round-trip at all.
        cal_click = alt.selection_point(name="cal_pick", fields=["day"], on="click", empty=False)
        cal_combined = (cal_heat + cal_grey + cal_text).add_params(cal_click)
        cal_event = st.altair_chart(cal_combined.properties(height=220), width="stretch", on_select="rerun", key="cal_chart")

        cal_picked_date = None
        cal_sel = cal_event.selection.get("cal_pick") if cal_event and cal_event.selection else None
        if cal_sel:
            cal_picked_date = date(cal_year, cal_month, int(cal_sel[0]["day"]))

        if cal_picked_date is not None:
            cal_day_items = get_payment_schedule_all_tracks()
            if not cal_day_items.empty:
                cal_day_items = cal_day_items[pd.to_datetime(cal_day_items["actual_payment_date"]).dt.date == cal_picked_date]
            with st.container(border=True):
                st.markdown(f"**{cal_picked_date.strftime('%B %d, %Y')} — breakdown**")
                if cal_day_items.empty:
                    st.caption("Nothing scheduled for this date.")
                else:
                    st.dataframe(
                        cal_day_items[["track", "payee_name", "reference", "amount", "due_date"]],
                        hide_index=True,
                        column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")},
                    )

                cal_existing_block = cal_blocked.get(cal_picked_date)
                if cal_existing_block:
                    st.info(f"Marked as **{cal_existing_block['label'] or cal_existing_block['kind'].title()}** ({cal_existing_block['kind']}).")
                    if st.button("Unmark this date", icon=":material/clear:", key="cal_unmark_btn"):
                        remove_blocked_date(cal_picked_date)
                        refresh()
                else:
                    cal_mark_col1, cal_mark_col2, cal_mark_col3 = st.columns([2, 1, 1])
                    with cal_mark_col1:
                        cal_mark_label = st.text_input("Label (optional)", key="cal_mark_label", placeholder="e.g. Eid'l Fitr, or a personal note")
                    with cal_mark_col2:
                        cal_mark_kind = st.selectbox("Type", options=["custom", "holiday"], format_func=lambda k: "Custom / no-issue day" if k == "custom" else "Holiday", key="cal_mark_kind")
                    with cal_mark_col3:
                        st.write("")
                        st.write("")
                        if st.button("Mark this date", icon=":material/event_busy:", key="cal_mark_btn"):
                            set_blocked_date(cal_picked_date, cal_mark_label.strip(), cal_mark_kind)
                            refresh()

    with st.container(border=True):
        st.subheader("Credit card due alerts")
        if cc_alerts.empty:
            st.caption("No unpaid credit card statements.")
        else:
            st.dataframe(
                cc_alerts,
                hide_index=True,
                column_config={
                    "total_amount_due": st.column_config.NumberColumn("Amount due", format="₱ %.2f"),
                    "days_until_due": st.column_config.NumberColumn("Days until due"),
                },
            )

    # Collapsed (2026-09-17, per user request): these six sections measure supplier payment
    # behavior over time (aging, DPO, rankings, trend, interest) -- genuinely useful, but not a
    # daily decision-driver, and several read as blank/zero for legacy-migrated data (no
    # itemized due dates survived Phase 8c's consolidated-batch import) until enough new,
    # normal-workflow invoices accumulate post-launch. Grouped here instead of occupying
    # permanent space on the main Overview.
    with st.expander("Supplier & debt analytics"):
        st.subheader("Supplier AP aging")
        aging_df = get_supplier_ap_aging_report()
        if aging_df.empty:
            st.caption("No open supplier invoices.")
        else:
            bucket_totals = pd.DataFrame(
                {
                    "bucket": ["Current", "1-30 days", "31-60 days", "60+ days"],
                    "amount": [
                        aging_df["current_due"].sum(),
                        aging_df["days_1_30"].sum(),
                        aging_df["days_31_60"].sum(),
                        aging_df["days_over_60"].sum(),
                    ],
                }
            )
            st.bar_chart(bucket_totals, x="bucket", y="amount", x_label="Aging bucket", y_label="Amount (PHP)")

        st.divider()
        st.subheader("Supplier payment health")
        st.caption("Lag is measured from a batch's invoice-amount-weighted due date to the check's own date — the closest thing to a per-check due date, since one check pays a batch's net total rather than a single invoice.")
        overdue_threshold = st.number_input(
            "Flag a cancelled/replaced check as \"long overdue\" past this many days", min_value=1, step=5,
            value=int(st.session_state.get("overdue_threshold", 30)), key="overdue_threshold",
        )
        avg_dpo = get_average_dpo_suppliers()
        overdue_cancelled_df = get_overdue_cancelled_checks(int(overdue_threshold))
        dpo_col, overdue_col = st.columns(2)
        dpo_col.metric(
            "Avg Days Payable Outstanding (Suppliers)",
            f"{avg_dpo:.1f} days" if avg_dpo is not None else "—",
            border=True,
            help="Average of (check date − batch's weighted invoice due date) across cleared supplier checks. Positive = paid later than due; negative = paid early.",
        )
        overdue_col.metric(
            f"Checks cancelled while {int(overdue_threshold)}+ days overdue",
            str(len(overdue_cancelled_df)),
            border=True,
            help="No bank-return/NSF tracking exists in this system — this counts cancelled or replaced checks whose own date was already well past the batch's due date, as a proxy for \"check returns due to being long overdue.\"",
        )
        if not overdue_cancelled_df.empty:
            st.dataframe(
                overdue_cancelled_df[["check_number", "creditor_name", "check_date", "amount", "status", "lag_days"]],
                hide_index=True,
                column_config={
                    "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    "lag_days": st.column_config.NumberColumn("Days overdue when voided", format="%.0f"),
                },
            )

        st.divider()
        kpi_col_left, kpi_col_right = st.columns(2)
        with kpi_col_left:
            st.subheader("Top suppliers")
            supplier_metric = st.segmented_control(
                "Rank by", options=["outstanding", "paid"],
                format_func=lambda m: "Outstanding payable" if m == "outstanding" else "Total paid (cleared, all-time)",
                default="outstanding", key="top_supplier_metric",
            )
            top_suppliers_df = get_top_suppliers(supplier_metric or "outstanding", limit=10)
            if top_suppliers_df.empty:
                st.caption("No supplier data yet for this metric.")
            else:
                st.bar_chart(
                    top_suppliers_df, x="supplier_name", y="value", horizontal=True,
                    x_label="Amount (PHP)", y_label="Supplier",
                )
        with kpi_col_right:
            st.subheader("Total payables trend")
            trend_granularity = st.segmented_control(
                "Group by", options=["month", "quarter", "year"],
                format_func=lambda g: g.title(), default="month", key="payables_trend_granularity",
            )
            trend_df = get_payables_trend(trend_granularity or "month")
            if trend_df.empty:
                st.caption("No payables data yet.")
            else:
                # Friendly period labels ("Sep 2026") aren't alphabetically sortable the way
                # "2026-09" was, so the chronological order has to be passed explicitly rather
                # than relying on sort=None -- trend_df is already in period order (built via
                # get_payables_trend()'s own sort-by-key-then-label step), so this list is safe
                # to use as-is.
                period_order = list(trend_df["period"])

                bars = alt.Chart(trend_df).mark_bar(color="#4C78A8").encode(
                    x=alt.X("period:N", title="Period", sort=period_order),
                    y=alt.Y("total_amount:Q", title="Total payables (PHP)"),
                    tooltip=["period", alt.Tooltip("total_amount:Q", title="Total", format=",.2f")],
                )
                trend_line = alt.Chart(trend_df).mark_line(color="#E45756", point=True).encode(
                    x=alt.X("period:N", sort=period_order),
                    y=alt.Y("moving_avg:Q"),
                    tooltip=["period", alt.Tooltip("moving_avg:Q", title="4-period avg", format=",.2f")],
                )
                st.altair_chart((bars + trend_line).properties(height=320), width="stretch")
                # 4 periods only lines up with the business's 120-day longest credit term at
                # Month granularity (4 months ~= 120 days) -- at Quarter/Year it's still a
                # 4-period trailing average, just no longer a ~120-day one (120 days is under a
                # single quarter/year bucket), so the "~120-day" framing is only claimed here.
                sma_caption = "4-period (~120-day) trailing moving average" if trend_granularity == "month" else "4-period trailing moving average"
                st.caption(f"Bars: total payables per period. Orange line: {sma_caption}.")

        st.divider()
        st.subheader("Debt interest tracker")
        st.caption(
            "Interest across all three debt sources — Private Loans (already tracked in full), Bank Loans "
            "(only where an installment's interest portion is filled in), and Credit Cards (only from statements "
            "entered with the interest breakdown). A source with nothing recorded yet just shows ₱0.00, not an error."
        )
        interest_today = date.today()
        interest_month_key = "interest_dashboard_month"
        interest_month = st.selectbox(
            "Month", options=list(range(1, 13)), index=interest_today.month - 1,
            format_func=lambda m: f"{date(2000, m, 1).strftime('%B')} {interest_today.year}", key=interest_month_key,
        )
        interest_summary = get_monthly_interest_summary(interest_today.year, interest_month)

        st.metric(f"Total interest paid ({interest_summary['period']})", php(interest_summary["total_interest_paid"]), help="Sum across all three debt sources.")

        ic1, ic2, ic3 = st.columns(3)
        with ic1:
            st.markdown("**Private loans**")
            pl = interest_summary["private_loans"]
            if pl["count"] == 0:
                st.caption("No interest this month.")
            else:
                for name, amt in pl.items():
                    if name not in ("subtotal", "count"):
                        st.write(f"{name}: **{php(amt)}**")
            st.metric("Subtotal", php(pl["subtotal"]), border=True)
        with ic2:
            st.markdown("**Bank loans**")
            bl_int = interest_summary["bank_loans"]
            if bl_int["count"] == 0:
                st.caption("No interest recorded this month.")
            else:
                for name, amt in bl_int.items():
                    if name not in ("subtotal", "count"):
                        st.write(f"{name}: **{php(amt)}**")
            st.metric("Subtotal", php(bl_int["subtotal"]), border=True)
        with ic3:
            st.markdown("**Credit cards**")
            cc_int = interest_summary["credit_cards"]
            if cc_int["count"] == 0:
                st.caption("No interest recorded this month.")
            else:
                for name, amt in cc_int.items():
                    if name not in ("subtotal", "count"):
                        st.write(f"{name}: **{php(amt)}**")
            st.metric("Subtotal", php(cc_int["subtotal"]), border=True)

        st.divider()
        st.markdown("**Interest trend (last 6 months)**")
        interest_trend_df = get_yearly_interest_trend(interest_today.year).tail(6)
        if interest_trend_df.empty or interest_trend_df["total_interest"].sum() == 0:
            st.caption("Not enough data for a trend yet.")
        else:
            interest_chart_df = interest_trend_df.set_index("month_name")[
                ["private_loans_interest", "bank_loans_interest", "credit_cards_interest", "total_interest"]
            ]
            st.line_chart(interest_chart_df, height=300)
            itc1, itc2, itc3 = st.columns(3)
            itc1.metric("6-month average", php(interest_trend_df["total_interest"].mean()))
            itc2.metric("Peak month", php(interest_trend_df["total_interest"].max()))
            itc3.metric("YTD total", php(interest_trend_df["total_interest"].sum()))

    with st.expander("Recent audit log"):
        audit_df = fix_nullable_int(q("SELECT log_id, user_action, entity_name, entity_id, old_values, new_values, datetime(timestamp, 'localtime') AS timestamp FROM audit_logs ORDER BY log_id DESC LIMIT 15"), "entity_id")
        st.dataframe(audit_df, hide_index=True)

# --------------------------------------------------------------------------
# Tab 1b: Analytics (2026-09-17) -- CPA-facing deep-dive, separate from the Dashboard's
# daily-glance KPIs. Scope note repeated in the UI itself, not just the docstring: this system
# tracks AP only (no revenue/GL/balance sheet), so classic ratios needing those aren't offered
# here -- only what's genuinely computable from checks, loans, and credit cards on file.
# --------------------------------------------------------------------------
with tab_analytics:
    st.header("Analytics")
    st.caption(
        "AP composition, payment behavior, and cost-of-debt analysis. This system tracks the AP side only "
        "(no revenue, general ledger, or balance sheet), so ratios like current ratio or DSO aren't offered here "
        "-- everything below is grounded in checks, loans, and credit cards actually on file."
    )

    analytics_composition_tab, analytics_payment_tab, analytics_debt_tab = st.tabs([
        ":material/stacked_bar_chart: AP Composition & Trend",
        ":material/speed: Payment Behavior",
        ":material/percent: Cost of Debt",
    ])

    _TRACK_COLORS = alt.Scale(domain=["Supplier", "Lender", "Bank Loan", "Credit Card"], scheme="tableau10")

    with analytics_composition_tab:
        an_filter_col1, an_filter_col2 = st.columns(2)
        with an_filter_col1:
            an_granularity = st.segmented_control(
                "Group by", options=["month", "quarter", "year"],
                format_func=lambda g: g.title(), default="month", key="analytics_granularity",
            )
            an_granularity = an_granularity or "month"
        with an_filter_col2:
            # Bounded by default (2026-09-17, per user feedback): a handful of bank loan
            # amortization schedules run to 2031, which otherwise stretches these two charts
            # across 5 years of mostly-empty periods -- real scheduled debt, but noise for
            # reading a trend. "All time" is still one click away for whoever wants the full
            # picture (e.g. to see that same long tail explicitly).
            an_window_today = pd.Timestamp(date.today())
            an_window_end_dates = {
                "Next 1 day": (an_window_today + pd.Timedelta(days=1)).date(),
                "Next 1 week": (an_window_today + pd.Timedelta(days=7)).date(),
                "Next 1 month": (an_window_today + pd.DateOffset(months=1)).date(),
                "Next 2 months": (an_window_today + pd.DateOffset(months=2)).date(),
                "Next 3 months": (an_window_today + pd.DateOffset(months=3)).date(),
                "Next 6 months": (an_window_today + pd.DateOffset(months=6)).date(),
                "Next 12 months": (an_window_today + pd.DateOffset(months=12)).date(),
                "Next 24 months": (an_window_today + pd.DateOffset(months=24)).date(),
            }
            an_window_choice = st.selectbox(
                "Show", options=list(an_window_end_dates.keys()) + ["All time"],
                index=6, key="analytics_window",
            )
        an_end_date = an_window_end_dates.get(an_window_choice)

        an_comp_df = get_ap_composition_by_track(an_granularity, end_date=an_end_date)
        an_growth_df = get_ap_growth_rate(an_granularity, end_date=an_end_date)
        an_concentration = get_supplier_concentration(limit=5)

        kpi1, kpi2, kpi3 = st.columns(3)
        if not an_growth_df.empty:
            kpi1.metric(f"Latest period total ({an_growth_df['period'].iloc[-1]})", php(an_growth_df["total_amount"].iloc[-1]), border=True)
            latest_change = an_growth_df["pct_change"].iloc[-1]
            kpi2.metric(
                f"Change vs prior {an_granularity}",
                f"{latest_change:+.1f}%" if pd.notna(latest_change) else "—",
                border=True,
            )
        else:
            kpi1.metric("Latest period total", "—", border=True)
            kpi2.metric(f"Change vs prior {an_granularity}", "—", border=True)
        kpi3.metric(
            "Top 5 supplier concentration",
            f"{an_concentration['pct']:.0f}%" if an_concentration["total_outstanding"] > 0 else "—",
            border=True,
            help="Share of total outstanding supplier payable held by the 5 largest suppliers by current exposure.",
        )

        st.subheader("AP mix over time, by track")
        if an_comp_df.empty:
            st.caption("No payables data yet.")
        else:
            # an_comp_df's rows are already in chronological order (analytics_service sorts on
            # the raw "2026-09"-style key before converting to this friendly "Sep 2026" label,
            # same pitfall get_payables_trend()'s own period_order already documents elsewhere
            # in this file) -- re-sorting the friendly label here would sort "Dec"/"Feb"/"Jan"
            # alphabetically instead of chronologically, which is exactly the bug this avoids.
            comp_period_order = list(dict.fromkeys(an_comp_df["period"]))
            # Standalone "AP growth rate" line chart replaced (2026-09-17, per user feedback)
            # with an average reference line on this chart plus the same period-total/%-change
            # numbers folded into each bar segment's own tooltip -- one chart instead of two,
            # same information on hover.
            period_totals_df = (
                an_growth_df[["period", "total_amount", "pct_change"]] if not an_growth_df.empty
                else pd.DataFrame(columns=["period", "total_amount", "pct_change"])
            )
            comp_with_context = an_comp_df.merge(period_totals_df, on="period", how="left")
            avg_total = float(period_totals_df["total_amount"].mean()) if not period_totals_df.empty else 0.0

            comp_chart = alt.Chart(comp_with_context).mark_bar().encode(
                x=alt.X("period:N", title="Period", sort=comp_period_order),
                y=alt.Y("amount:Q", title="Total payables (PHP)", stack=True),
                color=alt.Color("track:N", title="Track", scale=_TRACK_COLORS),
                tooltip=[
                    "period", "track",
                    alt.Tooltip("amount:Q", title="This track", format=",.2f"),
                    alt.Tooltip("total_amount:Q", title="Period total (all tracks)", format=",.2f"),
                    alt.Tooltip("pct_change:Q", title="Change vs prior period", format="+.1f"),
                ],
            )
            avg_rule = alt.Chart(pd.DataFrame({"y": [avg_total]})).mark_rule(
                color="#E45756", strokeDash=[6, 4], size=2,
            ).encode(y="y:Q", tooltip=[alt.Tooltip("y:Q", title="Average per period", format=",.2f")])
            st.altair_chart((comp_chart + avg_rule).properties(height=340), width="stretch")
            st.caption(f"Dashed red line: average total payables per {an_granularity} across the window shown ({php(avg_total)}). Hover a bar segment for that period's total and its change vs the prior period.")

        st.subheader("Concentration (Pareto)")
        # One chart with a track filter, not three near-identical ones stacked (2026-09-17, per
        # user feedback) -- "which track" is a filter on the same question ("who/what makes up
        # most of what I owe"), not three different questions.
        an_pareto_track = st.segmented_control(
            "Track", options=["All Tracks", "Supplier", "Lender", "Bank Loan", "Credit Card"],
            default="Supplier", key="analytics_pareto_track",
        )
        an_pareto_track = an_pareto_track or "Supplier"
        if an_pareto_track == "All Tracks":
            an_pareto_df = get_all_tracks_pareto(limit=15)
            an_pareto_noun = "payee/facility, across every track"
        elif an_pareto_track == "Supplier":
            an_pareto_df = get_supplier_pareto(limit=10).rename(columns={"supplier_name": "name"})
            an_pareto_noun = "supplier"
        elif an_pareto_track == "Lender":
            an_pareto_df = get_lender_pareto(limit=10)
            an_pareto_noun = "lender"
        elif an_pareto_track == "Bank Loan":
            an_pareto_df = get_bank_loan_pareto(limit=10)
            an_pareto_noun = "bank loan facility"
        else:
            # Empty for every real business migrated so far -- no cc_statements exist yet (Phase
            # 8a skipped historical CC statement import by design). Kept as a selectable track
            # anyway rather than hidden, same "ready but honestly empty" pattern as the Cost of
            # Debt tab, so it self-populates once real statements get entered.
            an_pareto_df = get_credit_card_pareto(limit=10)
            an_pareto_noun = "credit card"

        if an_pareto_df.empty:
            st.caption(f"No {an_pareto_noun} data yet for this metric.")
        else:
            # Full names truncated/rotated on the x-axis read as noise, not simpler (2026-09-17,
            # per user feedback) -- rank numbers on the axis, full name on hover and in the table
            # underneath, is the same information without the illegible labels.
            an_pareto_df = an_pareto_df.copy()
            an_pareto_df["rank"] = [f"#{i + 1}" for i in range(len(an_pareto_df))]
            rank_order = list(an_pareto_df["rank"])
            # Only get_all_tracks_pareto() returns a "track" column -- fold it into the tooltip
            # when present, so hovering a bar in the All Tracks view also says what kind of
            # obligation it is, not just its name.
            an_pareto_tooltip = ["name"] + (["track"] if "track" in an_pareto_df.columns else []) + [alt.Tooltip("value:Q", title="Outstanding", format=",.2f")]
            an_pareto_line_tooltip = ["name"] + (["track"] if "track" in an_pareto_df.columns else []) + [alt.Tooltip("cumulative_pct:Q", title="Cumulative %", format=".1f")]
            pareto_bars = alt.Chart(an_pareto_df).mark_bar(color="#4C78A8").encode(
                x=alt.X("rank:N", title=None, sort=rank_order),
                y=alt.Y("value:Q", title="Outstanding (PHP)"),
                tooltip=an_pareto_tooltip,
            )
            pareto_line = alt.Chart(an_pareto_df).mark_line(point=True, color="#E45756").encode(
                x=alt.X("rank:N", sort=rank_order),
                y=alt.Y("cumulative_pct:Q", title="Cumulative %", scale=alt.Scale(domain=[0, 100])),
                tooltip=an_pareto_line_tooltip,
            )
            st.altair_chart(
                alt.layer(pareto_bars, pareto_line).resolve_scale(y="independent").properties(height=340),
                width="stretch",
            )
            st.caption(f"Bars (left axis): outstanding per {an_pareto_noun}. Red line (right axis): running cumulative % of total. Hover a bar for its name.")
            an_pareto_table_cols = ["rank", "name"] + (["track"] if "track" in an_pareto_df.columns else []) + ["value", "cumulative_pct"]
            st.dataframe(
                an_pareto_df[an_pareto_table_cols],
                hide_index=True,
                column_config={
                    "rank": "Rank",
                    "name": "Name",
                    "track": "Track",
                    "value": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                    "cumulative_pct": st.column_config.NumberColumn("Cumulative %", format="%.1f%%"),
                },
            )

    with analytics_payment_tab:
        an_status_df = get_check_status_breakdown()
        an_aging_df = get_check_aging_distribution()
        an_avg_dpo = get_average_dpo_suppliers()
        an_cancel_rate = (
            an_status_df[an_status_df["effective_status"].isin(["Cancelled", "Replaced", "Spoiled"])]["n"].sum() / an_status_df["n"].sum() * 100
            if not an_status_df.empty else 0.0
        )

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric(
            "Avg Days Payable Outstanding (Suppliers)", f"{an_avg_dpo:.1f} days" if an_avg_dpo is not None else "—",
            border=True,
            help="Average of (check date − batch's weighted invoice due date) across cleared supplier checks. Only meaningful for batches with itemized invoices/due dates on file.",
        )
        kpi2.metric(
            "Cancelled/replaced/spoiled rate", f"{an_cancel_rate:.1f}%" if not an_status_df.empty else "—", border=True,
            help="Share of all Supplier/Lender checks ever issued that ended up Cancelled, Replaced, or Spoiled.",
        )
        kpi3.metric(
            "Still-outstanding checks", f"{int(an_status_df[an_status_df['effective_status'] == 'Active']['n'].iloc[0])}" if not an_status_df.empty and (an_status_df["effective_status"] == "Active").any() else "0",
            border=True,
        )

        st.subheader("Check status breakdown")
        if an_status_df.empty:
            st.caption("No checks issued yet.")
        else:
            status_donut = alt.Chart(an_status_df).mark_arc(innerRadius=60).encode(
                theta=alt.Theta("n:Q", title="Count"),
                color=alt.Color("effective_status:N", title="Status", scale=alt.Scale(scheme="tableau10")),
                tooltip=["effective_status", "n", alt.Tooltip("total:Q", title="Amount", format=",.2f")],
            )
            st.altair_chart(status_donut.properties(height=300), width="stretch")

        st.subheader("Outstanding checks by days until due")
        if an_aging_df.empty:
            st.caption("No outstanding checks right now.")
        else:
            aging_order = [b[2] for b in [(None, 0, "Overdue (not yet marked cleared)"), (0, 7, "Due in 0-7 days"), (8, 30, "Due in 8-30 days"), (31, 60, "Due in 31-60 days"), (61, 90, "Due in 61-90 days"), (91, None, "Due in 90+ days")]]
            aging_bars = alt.Chart(an_aging_df).mark_bar(color="#4C78A8").encode(
                x=alt.X("bucket:N", title=None, sort=aging_order),
                y=alt.Y("amount:Q", title="Amount (PHP)"),
                tooltip=["bucket", "count", alt.Tooltip("amount:Q", title="Amount", format=",.2f")],
            )
            st.altair_chart(aging_bars.properties(height=300), width="stretch")

        with st.expander("Checks cancelled/replaced while overdue"):
            an_overdue_threshold = st.number_input(
                "Flag a cancelled/replaced check as \"long overdue\" past this many days", min_value=1, step=5,
                value=30, key="analytics_overdue_threshold",
            )
            an_overdue_df = get_overdue_cancelled_checks(int(an_overdue_threshold))
            if an_overdue_df.empty:
                st.caption("None flagged at this threshold.")
            else:
                st.dataframe(
                    an_overdue_df[["check_number", "creditor_name", "check_date", "amount", "status", "lag_days"]],
                    hide_index=True,
                    column_config={
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                        "lag_days": st.column_config.NumberColumn("Days overdue when voided", format="%.0f"),
                    },
                )

    with analytics_debt_tab:
        an_debt_df = get_debt_cost_comparison()
        st.caption(
            "Effective interest rate per facility, where a real rate is actually on file -- private loan terms, a bank "
            "loan's entered interest portions, or a credit card's entered interest breakdown. A facility with nothing "
            "entered is simply left out, not shown as 0%."
        )
        if an_debt_df.empty:
            st.info("No interest-rate data on file yet for any facility. This populates as real loan terms, bank loan interest portions, or credit card interest breakdowns get entered.")
        else:
            kpi1, kpi2 = st.columns(2)
            kpi1.metric("Facilities with a rate on file", str(len(an_debt_df)), border=True)
            costliest = an_debt_df.iloc[0]
            kpi2.metric(
                "Costliest facility (monthly)", f"{costliest['monthly_rate_pct']:.2f}%", border=True,
                help=f"{costliest['name']} ({costliest['source']}, {costliest['reference']}) -- {costliest['annualized_rate_pct']:.1f}% annualized.",
            )

            debt_bars = alt.Chart(an_debt_df).mark_bar().encode(
                x=alt.X("monthly_rate_pct:Q", title="Monthly rate (%)"),
                y=alt.Y("name:N", title=None, sort="-x"),
                color=alt.Color("source:N", title="Source", scale=alt.Scale(scheme="tableau10")),
                tooltip=["name", "source", "reference", alt.Tooltip("monthly_rate_pct:Q", title="Monthly %", format=".2f"), alt.Tooltip("annualized_rate_pct:Q", title="Annualized %", format=".1f")],
            )
            st.altair_chart(debt_bars.properties(height=max(200, 28 * len(an_debt_df))), width="stretch")
            st.dataframe(
                an_debt_df[["source", "name", "reference", "monthly_rate_pct", "annualized_rate_pct"]],
                hide_index=True,
                column_config={
                    "monthly_rate_pct": st.column_config.NumberColumn("Monthly rate", format="%.2f%%"),
                    "annualized_rate_pct": st.column_config.NumberColumn("Annualized rate", format="%.1f%%"),
                },
            )

        st.divider()
        st.subheader("Priority list: facilities missing rate data")
        st.caption(
            "Real money outstanding on these, but no interest rate/principal entered -- ranked by exposure, so entering "
            "real terms starts with whichever one would move the comparison above the most. Edit a facility's terms from "
            "the Lenders tab (loan note) or Bank loans tab."
        )
        an_missing_df = get_missing_debt_terms(limit=10)
        if an_missing_df.empty:
            st.caption("Every facility with outstanding money has rate data on file.")
        else:
            st.dataframe(
                an_missing_df[["source", "name", "reference", "outstanding"]],
                hide_index=True,
                column_config={"outstanding": st.column_config.NumberColumn("Outstanding", format="₱ %.2f")},
            )

# --------------------------------------------------------------------------
# Tab 2: Suppliers (Track 1)
# --------------------------------------------------------------------------
with tab_suppliers:
    st.header("Order Statement")
    st.caption("Supplier records now live in Creditor Master. Open or create a batch below — Invoices, Credit Memo, and Checks all follow whichever batch is open.")

    statements_df = q(
        """SELECT os.statement_id, os.statement_ref, c.creditor_name, os.total_amount,
                  v.credit_memo_total, v.net_total,
                  v.net_total - COALESCE(
                      (SELECT SUM(amount) FROM checks_issued
                       WHERE statement_id = os.statement_id AND status = 'Active' AND check_date <= DATE('now', 'localtime')), 0
                  ) AS outstanding,
                  os.status, os.is_locked, datetime(os.created_at, 'localtime') AS created_at,
                  """ + statement_modified_sql("os") + """ AS last_modified
           FROM order_statements os
           JOIN creditors c ON os.creditor_id = c.creditor_id
           JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
           ORDER BY os.statement_id DESC"""
    )
    selected_sid = st.session_state.get("selected_statement_id")

    # Streamlit forbids writing to st.session_state["os_mode"] after the os_mode widget below
    # has already been instantiated this run (even right before a rerun) -- so a mode switch
    # requested by a button click has to be staged here, before the widget renders, and applied
    # on the rerun that follows.
    if "_os_mode_next" in st.session_state:
        st.session_state["os_mode"] = st.session_state.pop("_os_mode_next")

    os_mode = st.segmented_control(
        "What do you want to do?",
        options=["🔍 Browse Batches", "➕ New Batch"],
        default="🔍 Browse Batches",
        key="os_mode",
    )

    if os_mode == "➕ New Batch":
        st.subheader("Create order statement")
        st.caption("Total starts at ₱0.00 and is never typed in — it recalculates automatically as invoice lines are added to the batch.")
        with st.form("new_statement_form", border=False):
            os_creditor = selectbox_creditor("Supplier", "SUPPLIER", "os_creditor")
            if st.form_submit_button("Create draft", icon=":material/note_add:") and os_creditor:
                if guard_duplicate_submit("create_order_statement", os_creditor):
                    new_sid = create_order_statement(os_creditor)
                    st.session_state["selected_statement_id"] = new_sid
                    st.session_state["_os_mode_next"] = "🔍 Browse Batches"
                    st.success("Draft order statement created — opened below.")
                    refresh()
                else:
                    st.warning("Duplicate submission ignored — identical to the click just before it.")

        with st.expander("Import many batches from one Excel file (one sheet = one new batch)"):
            st.caption(
                "Upload an .xlsx or .xlsm (the macro entry workbook) where each sheet (tab) holds one supplier's invoices in the usual columns "
                "(Doc Ref / Date / Amount / Less %). Name each tab after its supplier. The same supplier can have "
                "several tabs — every tab becomes its own new batch. You review everything before anything is saved."
            )
            st.download_button(
                "Download Excel entry template (with pop-up form)", data=build_invoice_entry_workbook,
                file_name=f"invoice_entry_{date.today():%Y-%m-%d}.xlsm", mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                icon=":material/download:", key="multi_import_template_dl",
                help="A macro-enabled workbook with the supplier list refreshed from the system right now. Open it, enable macros, and use the Add invoice button.",
            )
            mb_file = st.file_uploader("Workbook", type=["xlsx", "xlsm"], key="multi_import_uploader")
            if st.button("Read workbook", icon=":material/upload_file:", key="multi_import_read_btn"):
                if not mb_file:
                    st.warning("Upload a file first.")
                else:
                    mb_sheets, mb_problems = parse_invoice_workbook(mb_file.getvalue())
                    st.session_state["multi_import_sheets"] = [
                        {"sheet": sh.sheet_name, "messages": sh.messages,
                         "rows": [{"doc_ref": r.doc_ref, "invoice_date": r.invoice_date, "amount": r.amount, "less_pct": r.less_pct} for r in sh.rows]}
                        for sh in mb_sheets
                    ]
                    st.session_state["multi_import_problems"] = mb_problems
                    st.session_state.pop("multi_import_result", None)
            for problem in st.session_state.get("multi_import_problems", []):
                st.error(problem)

            mb_supplier_options = creditor_options("SUPPLIER")
            mb_state = st.session_state.get("multi_import_sheets", [])
            mb_plan = []
            if mb_state:
                st.markdown("**Review** — pick the supplier for each tab (tabs set to skip are ignored).")
                for i, sh in enumerate(mb_state):
                    good_rows = [r for r in sh["rows"] if r["doc_ref"].strip() and r["amount"] > 0]
                    guess = match_supplier(sh["sheet"], mb_supplier_options)
                    options = [None] + list(mb_supplier_options)
                    rc1, rc2 = st.columns([2, 3])
                    pick = rc2.selectbox(
                        "Supplier for tab: " + sh["sheet"], options=options,
                        index=options.index(guess) if guess in options else 0,
                        format_func=lambda cid: "— skip this tab —" if cid is None else mb_supplier_options[cid],
                        key=f"multi_import_pick_{i}_{sh['sheet']}",
                    )
                    rc1.markdown(f"**{sh['sheet']}**")
                    rc1.caption(f"{len(good_rows)} invoice(s) · {php(sum(r['amount'] for r in good_rows))}" + (f" · {len(sh['rows']) - len(good_rows)} row(s) will be skipped (missing Doc Ref or amount)" if len(sh["rows"]) != len(good_rows) else ""))
                    for m in sh["messages"][:3]:
                        rc1.caption(f":warning: {m}")
                    if pick is not None and good_rows:
                        mb_plan.append((sh["sheet"], pick, good_rows))
                mb_promote = st.checkbox("Also promote each new batch to Issued", value=True, key="multi_import_promote")
                mb_warnings = [f"{sheet}: {w}" for sheet, cid, rows_ in mb_plan
                               for w in invoice_warnings(cid, [(r["doc_ref"].strip(), r["invoice_date"], r["amount"]) for r in rows_])]
                if st.button("Create batches and import invoices", icon=":material/playlist_add:", key="multi_import_go_btn", disabled=not mb_plan) or dup_resume("multi_import"):
                    if dup_needs_confirm("multi_import", mb_warnings):
                        pass
                    elif guard_duplicate_submit("multi_import", repr([(a, b, len(c)) for a, b, c in mb_plan])):
                        results = []
                        for sheet, cid, rows_ in mb_plan:
                            terms_df = q("SELECT terms FROM creditors WHERE creditor_id = ?", (cid,))
                            terms = terms_df["terms"][0] if not terms_df.empty else ""
                            sid = create_order_statement(cid)
                            added, skipped = 0, []
                            for r in rows_:
                                try:
                                    add_invoice(cid, r["doc_ref"].strip(), r["invoice_date"], resolve_due_date(r["invoice_date"], terms),
                                                r["amount"], [r["less_pct"]] if r["less_pct"] else [], sid)
                                    added += 1
                                except sqlite3.IntegrityError:
                                    skipped.append(r["doc_ref"].strip())
                            ref = q("SELECT statement_ref FROM order_statements WHERE statement_id = ?", (sid,))["statement_ref"][0]
                            if mb_promote and added:
                                try:
                                    ref = promote_draft_statement(sid, "Issued")
                                except ValueError:
                                    pass
                            results.append({"Tab": sheet, "Supplier": mb_supplier_options[cid], "Batch": ref, "Invoices added": added,
                                            "Already on file (skipped)": ", ".join(skipped)})
                        st.session_state["multi_import_result"] = results
                        st.session_state.pop("multi_import_sheets", None)
                        refresh()
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")
            if st.session_state.get("multi_import_result"):
                st.success("Done — new batches created:")
                st.dataframe(pd.DataFrame(st.session_state["multi_import_result"]), hide_index=True)

    else:
        st.subheader("Order statement browser")
        if statements_df.empty:
            st.caption("No order statements yet — switch to \"New Batch\" above to create the first one.")
        else:
            ref_search = st.text_input(
                "Search: statement ref", key="batch_filter_ref_search", placeholder="e.g. DRAFT-20260905...",
            )
            fc1, fc2, fc3 = st.columns(3)
            supplier_filter = fc1.multiselect(
                "Filter: supplier", options=sorted(statements_df["creditor_name"].unique()), key="batch_filter_supplier"
            )
            status_filter_browser = fc2.multiselect(
                "Filter: status", options=sorted(statements_df["status"].unique()), key="batch_filter_status"
            )
            created_dates = pd.to_datetime(statements_df["created_at"]).dt.date
            full_range = (min(created_dates.min(), date.today()), max(created_dates.max(), date.today()))
            date_preset = fc3.segmented_control(
                "Filter: created date",
                options=["Today", "Last 7 days", "Last 30 days", "All time", "Custom"],
                default="All time",
                required=True,
                key="batch_filter_date_preset",
            )
            if date_preset == "Today":
                date_range = (date.today(), date.today())
            elif date_preset == "Last 7 days":
                date_range = (date.today() - timedelta(days=6), date.today())
            elif date_preset == "Last 30 days":
                date_range = (date.today() - timedelta(days=29), date.today())
            elif date_preset == "Custom":
                date_range = fc3.date_input("Custom range", value=full_range, key="batch_filter_date_range")
            else:  # "All time"
                date_range = full_range
            if date_preset != "Custom":
                fc3.caption(f"{date_range[0]:%Y-%m-%d} to {date_range[1]:%Y-%m-%d}")
            mod_col, _mod_spacer = st.columns([1, 2])

            filtered_statements_df = statements_df.copy()
            if ref_search.strip():
                filtered_statements_df = filtered_statements_df[
                    filtered_statements_df["statement_ref"].str.contains(ref_search.strip(), case=False, na=False)
                ]
            if supplier_filter:
                filtered_statements_df = filtered_statements_df[filtered_statements_df["creditor_name"].isin(supplier_filter)]
            if status_filter_browser:
                filtered_statements_df = filtered_statements_df[filtered_statements_df["status"].isin(status_filter_browser)]
            if isinstance(date_range, tuple) and len(date_range) == 2:
                start_d, end_d = date_range
                row_dates = pd.to_datetime(filtered_statements_df["created_at"]).dt.date
                filtered_statements_df = filtered_statements_df[(row_dates >= start_d) & (row_dates <= end_d)]
            filtered_statements_df = date_preset_filter(mod_col, filtered_statements_df, "last_modified", "Filter: last modified", "batch_filter_modified_preset")
            filtered_statements_df = filtered_statements_df.reset_index(drop=True)

            st.caption("Click a row to open that batch — every action below (invoices, credit memo, lock/unlock, issue check, export) defaults to it.")
            browser_event = st.dataframe(
                filtered_statements_df,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="batch_browser_df",
                column_config={
                    "total_amount": st.column_config.NumberColumn("Gross total", format="₱ %.2f"),
                    "credit_memo_total": st.column_config.NumberColumn("Credit memos", format="₱ %.2f"),
                    "net_total": st.column_config.NumberColumn("Net total (checks cut against this)", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding (uncleared)", format="₱ %.2f"),
                    "is_locked": st.column_config.CheckboxColumn("Locked"),
                },
            )
            selected_rows = browser_event.selection.rows if browser_event and browser_event.selection else []
            if selected_rows and selected_rows[0] < len(filtered_statements_df):
                st.session_state["selected_statement_id"] = int(filtered_statements_df.iloc[selected_rows[0]]["statement_id"])
            selected_sid = st.session_state.get("selected_statement_id")

            with st.expander("Promote statement status"):
                st.caption("Informational only — checks can be issued from any status once the batch has a payable balance. Promotion is not required.")
                promotable = statements_df[statements_df["status"] == "Draft"]
                with st.form("promote_statement_form", border=False):
                    if promotable.empty:
                        st.caption("No draft statements to promote.")
                        stmt_choice = None
                    else:
                        stmt_choice = st.selectbox(
                            "Statement",
                            options=promotable["statement_id"],
                            format_func=lambda sid: f"{promotable.set_index('statement_id').loc[sid, 'statement_ref']} ({promotable.set_index('statement_id').loc[sid, 'status']})",
                            index=default_index(promotable["statement_id"], st.session_state.get("selected_statement_id")),
                            key=batch_scoped_key("stmt_choice"),
                        )
                    if st.form_submit_button("Promote to Issued", icon=":material/arrow_upward:") and stmt_choice is not None:
                        if guard_duplicate_submit("promote_statement", int(stmt_choice)):
                            try:
                                new_ref = promote_draft_statement(int(stmt_choice), "Issued")
                                st.success(f"Promoted to Issued as {new_ref}.")
                                refresh()
                            except ValueError as e:
                                st.error(str(e))
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

    batch_is_open = selected_sid is not None and selected_sid in set(statements_df["statement_id"])
    batch_scope_pick = selected_sid if batch_is_open else None

    if not batch_is_open:
        st.info("Open a batch above to work with its invoices, credit memos, and checks.")
    else:
        scoped_row = statements_df.set_index("statement_id").loc[int(batch_scope_pick)]
        scoped_creditor_id = int(q("SELECT creditor_id FROM order_statements WHERE statement_id = ?", (int(batch_scope_pick),))["creditor_id"][0])
        supplier_terms_row = q("SELECT terms FROM creditors WHERE creditor_id = ?", (scoped_creditor_id,))
        supplier_terms = supplier_terms_row["terms"][0] if not supplier_terms_row.empty else ""
        batch_is_locked = int(scoped_row["is_locked"]) == 1

        with st.container(border=True):
            batch_is_cancelled = scoped_row["status"] == "Cancelled"
            hc1, hc2, hc3, hc4 = st.columns([4, 2, 2, 1])
            with hc1:
                st.markdown(f"**{scoped_row['statement_ref']}** — {scoped_row['creditor_name']}")
                lock_badge = "🔒 Locked" if batch_is_locked else "🔓 Unlocked"
                st.caption(f"Net {php(scoped_row['net_total'])} · {scoped_row['status']} · {lock_badge} · Terms: {supplier_terms or 'not set'}")
            with hc2:
                if batch_is_cancelled:
                    st.caption("Cancelled — permanently locked.")
                elif batch_is_locked:
                    if st.button("Unlock batch", icon=":material/lock_open:", key="unlock_btn_open"):
                        confirm_unlock_dialog(int(selected_sid), scoped_row["statement_ref"])
                else:
                    has_checks = not q("SELECT 1 FROM checks_issued WHERE statement_id = ? LIMIT 1", (int(selected_sid),)).empty
                    if has_checks:
                        if st.button("Re-lock batch", icon=":material/lock:", key="relock_btn_open"):
                            ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if ok else st.error)(lock_msg)
                            if ok:
                                refresh()
                    else:
                        st.caption("Locks automatically once a check is issued.")
            with hc3:
                if not batch_is_cancelled:
                    if st.button("Cancel batch", icon=":material/cancel:", key="cancel_batch_btn"):
                        active_checks_row = q(
                            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
                            (int(selected_sid),),
                        )
                        invoice_count_row = q(
                            "SELECT COUNT(*) AS n FROM invoices WHERE statement_id = ? AND status != 'Cancelled'",
                            (int(selected_sid),),
                        )
                        confirm_cancel_dialog(
                            int(selected_sid), scoped_row["statement_ref"],
                            int(active_checks_row["n"][0]), float(active_checks_row["total"][0]),
                            int(invoice_count_row["n"][0]),
                        )
            with hc4:
                if st.button("Clear", key="clear_batch_selection"):
                    st.session_state["selected_statement_id"] = None
                    refresh()

            with st.expander("Export Batch Statement"):
                export_net, export_active_sum, export_remaining = get_batch_balance(int(selected_sid))
                export_is_overpaid = export_remaining < -0.005
                export_is_covered = export_net > 0 and abs(export_remaining) <= 0.005
                if batch_is_locked:
                    st.caption("This batch is locked. Regenerating the report keeps it locked.")
                elif export_is_overpaid:
                    st.error(f"This batch is overpaid by {php(-export_remaining)} — active checks exceed the net total. This can't be locked until you correct or cancel a check.")
                elif export_is_covered:
                    st.caption("This batch is balanced — generating the report will lock it automatically for collection.")
                else:
                    st.caption(f"This batch is not yet balanced (remaining {php(export_remaining)}) — generating now will NOT lock it.")

                export_full_history = st.toggle("Include full check history (incl. cancelled/replaced)", value=True, key="export_history_toggle")
                export_show_payment_application = st.toggle(
                    "Include payment application & running balance", value=False, key="export_payment_application_toggle",
                    help="Adds an 'Applied To' column (which invoice(s) each check paid, where tracked) and a running balance column to the Checks table.",
                )
                export_copy_choice = st.radio(
                    "Copies to print",
                    options=["both", "filing", "supplier"],
                    format_func=lambda c: {"both": "Both (filing + supplier)", "filing": "Filing copy only", "supplier": "Supplier copy only"}[c],
                    horizontal=True,
                    key="export_copy_choice",
                )
                if st.button("Generate Batch Statement PDF", icon=":material/picture_as_pdf:", key="export_generate_btn"):
                    pdf_bytes = generate_batch_statement_pdf(int(selected_sid), export_full_history, export_copy_choice, export_show_payment_application)
                    if pdf_bytes:
                        if export_is_covered and not batch_is_locked:
                            lock_ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if lock_ok else st.error)(
                                "Batch is balanced — locked automatically after generating the report for collection." if lock_ok else lock_msg
                            )
                        ref = scoped_row["statement_ref"]
                        suffix = {"both": "", "filing": "_filing", "supplier": "_supplier"}[export_copy_choice]
                        pdf_preview_and_download(
                            pdf_bytes, f"batch_statement_{ref}{suffix}.pdf".replace(" ", "_"),
                            key="export_download", label="Batch Statement PDF",
                        )
                    else:
                        st.error("Could not generate the Batch Statement PDF.")

                st.divider()
                st.caption("Thermal printer statement — 80mm roll (3.15\" wide), one continuous strip listing the same invoices and checks, no page breaks.")
                if st.button("Generate Thermal Statement (80mm) PDF", icon=":material/receipt_long:", key="export_thermal_generate_btn"):
                    thermal_pdf_bytes = generate_batch_statement_thermal_pdf(int(selected_sid), export_full_history)
                    if thermal_pdf_bytes:
                        if export_is_covered and not batch_is_locked:
                            lock_ok, lock_msg = lock_statement(int(selected_sid))
                            (st.success if lock_ok else st.error)(
                                "Batch is balanced — locked automatically after generating the report for collection." if lock_ok else lock_msg
                            )
                        ref = scoped_row["statement_ref"]
                        pdf_preview_and_download(
                            thermal_pdf_bytes, f"batch_statement_{ref}_thermal.pdf".replace(" ", "_"),
                            key="export_thermal_download", label="Thermal Statement PDF",
                        )
                    else:
                        st.error("Could not generate the thermal statement PDF.")

        tab_invoices_sub, tab_memo_sub, tab_checks_sub = st.tabs(
            ["Invoices", "Credit Memo", "Checks"], key="os_subtab", default="Invoices", on_change="rerun",
        )

        with tab_invoices_sub:
            scoped_invoices_df = q(
                """SELECT invoice_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status
                   FROM invoices
                   WHERE statement_id = ?
                   ORDER BY invoice_id""",
                (int(batch_scope_pick),),
            )
            if batch_is_locked:
                if scoped_invoices_df.empty:
                    st.caption("No invoices linked to this order statement yet.")
                else:
                    st.dataframe(
                        scoped_invoices_df[["invoice_id", "doc_ref", "invoice_date", "due_date", "gross_amount", "amount", "status"]],
                        hide_index=True,
                        column_config={
                            "gross_amount": st.column_config.NumberColumn("Gross amount", format="₱ %.2f"),
                            "amount": st.column_config.NumberColumn("Net amount", format="₱ %.2f"),
                        },
                    )
                st.caption("This batch is cancelled and can't be edited." if batch_is_cancelled else "Batch is locked — unlock it above to add, edit, or delete an invoice.")
            else:
                st.caption(f"Add, edit, or delete rows below, then Save. A new row needs a Doc Ref and a positive Amount; use the row's menu (or select + Delete) to remove one. Due Date is always recomputed as Date Received + this supplier's Terms ({supplier_terms or 'not set'}) when you Save — it's a computed reference field, not manually edited; whatever you type into it is overwritten on Save. The grid itself won't visibly update Due Date while you're still typing, only after Save. Every change is logged automatically (old → new + timestamp).")

                with st.expander("Add invoices from a photo, PDF, or spreadsheet"):
                    bulk_staged_key = batch_scoped_key("bulk_staged")
                    bulk_errors_key = batch_scoped_key("bulk_errors")
                    ocr_raw_text_key = batch_scoped_key("ocr_raw_text")

                    tab_ocr, tab_import = st.tabs(["From images/PDFs (local OCR)", "From spreadsheet (CSV/Excel)"])

                    with tab_ocr:
                        st.caption("Upload photos or PDF scans of this supplier's invoices — one file per invoice. Runs fully offline (Tesseract), no external service or cost. Best-effort read of Doc Ref, Date, and Amount — double-check the results below before adding; nothing is saved until you confirm.")
                        ocr_files = st.file_uploader(
                            "Invoice images or PDFs", type=["png", "jpg", "jpeg", "pdf"],
                            accept_multiple_files=True, key=batch_scoped_key("ocr_uploader"),
                        )
                        if st.button("Extract fields", icon=":material/document_scanner:", key=batch_scoped_key("ocr_extract_btn")):
                            if not ocr_files:
                                st.warning("Upload at least one file first.")
                            else:
                                staged, extract_errors, raw_texts = [], [], {}
                                with st.spinner(f"Extracting {len(ocr_files)} invoice(s)..."):
                                    for f in ocr_files:
                                        fields, message, raw_text = extract_invoice_fields(f.getvalue(), f.name)
                                        if raw_text:
                                            raw_texts[f.name] = raw_text
                                        if fields:
                                            staged.append({
                                                "source_file": f.name,
                                                "doc_ref": fields.doc_ref,
                                                "invoice_date": fields.invoice_date,
                                                "due_date": resolve_due_date(fields.invoice_date, supplier_terms),
                                                "gross_amount": fields.amount,
                                                "less_pct": 0.0,
                                            })
                                            if message:
                                                extract_errors.append(("warning", f"{f.name}: {message}"))
                                        else:
                                            extract_errors.append(("error", f"{f.name}: {message}"))
                                st.session_state[bulk_staged_key] = staged
                                st.session_state[bulk_errors_key] = extract_errors
                                st.session_state[ocr_raw_text_key] = raw_texts

                        ocr_raw_texts = st.session_state.get(ocr_raw_text_key, {})
                        if ocr_raw_texts:
                            with st.expander("Show raw OCR text (for diagnosing a bad read)"):
                                for fname, rtext in ocr_raw_texts.items():
                                    st.caption(fname)
                                    st.text(rtext)

                    with tab_import:
                        st.caption("Upload a .csv or .xlsx with columns like Doc Ref / Date / Amount / Less % (any reasonable header variant works). More reliable than OCR since it's reading real data, not a photo.")
                        st.download_button(
                            "Download CSV template", data=get_template_csv(),
                            file_name=re.sub(r"[^A-Za-z0-9._-]+", "_", f"{scoped_row['creditor_name']}_{scoped_row['statement_ref']}_invoice_template").strip("_") + ".csv",
                            mime="text/csv", icon=":material/download:", key=batch_scoped_key("import_template_btn"),
                        )
                        import_file = st.file_uploader(
                            "Invoice spreadsheet", type=["csv", "xlsx"],
                            accept_multiple_files=False, key=batch_scoped_key("import_uploader"),
                        )
                        if st.button("Import rows", icon=":material/upload_file:", key=batch_scoped_key("import_btn")):
                            if not import_file:
                                st.warning("Upload a file first.")
                            else:
                                imported_rows, import_messages = parse_invoice_spreadsheet(import_file.getvalue(), import_file.name)
                                staged = [{
                                    "source_file": import_file.name,
                                    "doc_ref": r.doc_ref,
                                    "invoice_date": r.invoice_date,
                                    "due_date": resolve_due_date(r.invoice_date, supplier_terms),
                                    "gross_amount": r.amount,
                                    "less_pct": r.less_pct,
                                } for r in imported_rows]
                                st.session_state[bulk_staged_key] = staged
                                st.session_state[bulk_errors_key] = [("warning", m) for m in import_messages]
                                st.session_state[ocr_raw_text_key] = {}

                    for level, e in st.session_state.get(bulk_errors_key, []):
                        (st.warning if level == "warning" else st.error)(e)

                    staged_rows = st.session_state.get(bulk_staged_key, [])
                    if staged_rows:
                        st.caption(f"{len(staged_rows)} invoice(s) staged — review/correct below, then add.")
                        staging_editor_key = batch_scoped_key("bulk_staging_editor")
                        edited_staged_df = st.data_editor(
                            pd.DataFrame(staged_rows),
                            hide_index=True,
                            num_rows="dynamic",
                            disabled=["source_file", "due_date"],
                            key=staging_editor_key,
                            column_config={
                                "source_file": st.column_config.TextColumn("Source"),
                                "doc_ref": st.column_config.TextColumn("Doc Ref"),
                                "invoice_date": st.column_config.DateColumn("Date Received"),
                                "due_date": st.column_config.DateColumn("Due Date (computed, recalculated on save)"),
                                "gross_amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01),
                                "less_pct": st.column_config.NumberColumn("Less %", format="%.2f%%", min_value=0.0, max_value=100.0, step=1.0),
                            },
                        )
                        bulk_promote_after = False
                        if scoped_row["status"] == "Draft":
                            bulk_promote_after = st.checkbox(
                                "Also promote this batch to Issued once the invoices are added", value=True,
                                key=batch_scoped_key("bulk_promote_after"),
                                help="Gives the batch its permanent number (e.g. 2026 - OS 0007) in the same step, so you don't need to open \"Promote statement status\" separately.",
                            )
                        bulk_col_add, bulk_col_discard = st.columns(2)
                        with bulk_col_add:
                            if st.button("Add these invoices to the batch", icon=":material/playlist_add:", key=batch_scoped_key("bulk_commit_btn")) or dup_resume("bulk_add_invoices"):
                                if dup_needs_confirm("bulk_add_invoices", invoice_warnings(scoped_creditor_id, [
                                    (cell_text(r["doc_ref"]).strip(), r["invoice_date"], float(r["gross_amount"] or 0))
                                    for _, r in edited_staged_df.iterrows() if cell_text(r["doc_ref"]).strip() and float(r["gross_amount"] or 0) > 0
                                ])):
                                    pass
                                elif guard_duplicate_submit("bulk_add_invoices", int(batch_scope_pick), edited_staged_df.to_csv(index=False)):
                                    added, commit_errors = 0, []
                                    for _, r in edited_staged_df.iterrows():
                                        doc_ref = cell_text(r["doc_ref"]).strip()
                                        gross = float(r["gross_amount"] or 0)
                                        if not doc_ref or gross <= 0:
                                            commit_errors.append(f"Skipped a row missing Doc Ref or a positive Amount ({r.get('source_file', '?')}).")
                                            continue
                                        resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                                        try:
                                            add_invoice(
                                                scoped_creditor_id, doc_ref, r["invoice_date"], resolved_due,
                                                gross, [float(r["less_pct"])] if r["less_pct"] else [], int(batch_scope_pick),
                                            )
                                            added += 1
                                        except sqlite3.IntegrityError:
                                            commit_errors.append(f"'{doc_ref}' already exists for this supplier.")
                                    if added:
                                        st.success(f"{added} invoice(s) added.")
                                        if bulk_promote_after and not commit_errors:
                                            try:
                                                st.toast(f"{added} invoice(s) added; batch promoted to Issued as {promote_draft_statement(int(batch_scope_pick), 'Issued')}.")
                                            except ValueError as e:
                                                commit_errors.append(f"Invoices were added, but the batch could not be promoted: {e}")
                                    for e in commit_errors:
                                        st.error(e)
                                    st.session_state[bulk_staged_key] = []
                                    st.session_state[bulk_errors_key] = []
                                    if added:
                                        refresh()
                                else:
                                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                        with bulk_col_discard:
                            if st.button("Discard staged rows", icon=":material/delete_sweep:", key=batch_scoped_key("bulk_discard_btn")):
                                st.session_state[bulk_staged_key] = []
                                st.session_state[bulk_errors_key] = []
                                st.rerun()

                original_invoices_df = scoped_invoices_df.copy()
                original_invoices_df["less_pct"] = original_invoices_df["trade_discounts"].apply(parse_less_pct)
                editable_display_df = original_invoices_df[
                    ["invoice_id", "doc_ref", "invoice_date", "due_date", "gross_amount", "less_pct", "amount", "status"]
                ].copy()
                editable_display_df["invoice_date"] = pd.to_datetime(editable_display_df["invoice_date"]).dt.date
                editable_display_df["due_date"] = pd.to_datetime(editable_display_df["due_date"]).dt.date
                invoices_editor_key = batch_scoped_key("edit_invoices_editor")
                edited_invoices_df = st.data_editor(
                    editable_display_df,
                    hide_index=True,
                    num_rows="dynamic",
                    disabled=["invoice_id", "due_date", "amount", "status"],
                    key=invoices_editor_key,
                    column_config={
                        "invoice_id": st.column_config.NumberColumn("ID"),
                        "doc_ref": st.column_config.TextColumn("Doc Ref", default=""),
                        "invoice_date": st.column_config.DateColumn("Date Received", default=date.today()),
                        "due_date": st.column_config.DateColumn(
                            "Due Date (computed, recalculated on save)", default=resolve_due_date(date.today(), supplier_terms)
                        ),
                        "gross_amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01, default=0.0),
                        "less_pct": st.column_config.NumberColumn("Less %", format="%.2f%%", min_value=0.0, max_value=100.0, step=1.0, default=0.0),
                        "amount": st.column_config.NumberColumn("Net Amount (recalculated on save)", format="₱ %.2f"),
                        "status": st.column_config.TextColumn("Status"),
                    },
                )
                if st.button("Save invoice changes", icon=":material/save:", key=batch_scoped_key("save_invoice_edits")) or dup_resume("save_invoice_changes"):
                  if dup_needs_confirm("save_invoice_changes", invoice_warnings(scoped_creditor_id, [
                      (cell_text(r["doc_ref"]).strip(), r["invoice_date"], float(r["gross_amount"] or 0))
                      for _, r in edited_invoices_df.iterrows()
                      if pd.isna(r["invoice_id"]) and cell_text(r["doc_ref"]).strip() and float(r["gross_amount"] or 0) > 0
                  ])):
                    pass
                  elif not guard_duplicate_submit("save_invoice_changes", int(batch_scope_pick), edited_invoices_df.to_csv(index=False)):
                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                  else:
                    original_by_id = original_invoices_df.set_index("invoice_id")
                    seen_ids, added, updated, row_errors = set(), 0, 0, []
                    for _, r in edited_invoices_df.iterrows():
                        if pd.isna(r["invoice_id"]):
                            doc_ref, gross = cell_text(r["doc_ref"]).strip(), float(r["gross_amount"] or 0)
                            if not doc_ref and gross <= 0:
                                continue  # untouched blank template row
                            if not doc_ref or gross <= 0:
                                row_errors.append("New invoice row needs a Doc Ref and a positive Amount.")
                                continue
                            resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                            try:
                                add_invoice(
                                    scoped_creditor_id, doc_ref, r["invoice_date"], resolved_due,
                                    gross, [float(r["less_pct"])] if r["less_pct"] else [], int(batch_scope_pick),
                                )
                                added += 1
                            except sqlite3.IntegrityError:
                                row_errors.append(f"'{doc_ref}' already exists for this supplier.")
                            continue

                        inv_id = int(r["invoice_id"])
                        seen_ids.add(inv_id)
                        orig = original_by_id.loc[inv_id]
                        resolved_due = resolve_due_date(r["invoice_date"], supplier_terms)
                        is_changed = (
                            cell_text(r["doc_ref"]) != cell_text(orig["doc_ref"])
                            or str(r["invoice_date"]) != str(orig["invoice_date"])
                            or str(resolved_due) != str(orig["due_date"])
                            or abs(float(r["gross_amount"]) - float(orig["gross_amount"])) > 0.001
                            or abs(float(r["less_pct"]) - float(orig["less_pct"])) > 0.001
                        )
                        if not is_changed:
                            continue
                        ok, msg = edit_invoice(
                            inv_id, cell_text(r["doc_ref"]), r["invoice_date"], resolved_due,
                            float(r["gross_amount"]), float(r["less_pct"]),
                        )
                        if ok:
                            updated += 1
                        else:
                            row_errors.append(f"Invoice {inv_id}: {msg}")

                    deleted = 0
                    for removed_id in set(original_by_id.index) - seen_ids:
                        ok, msg = delete_invoice(int(removed_id))
                        if ok:
                            deleted += 1
                        else:
                            row_errors.append(msg)

                    summary = [f"{n} {label}" for n, label in [(added, "added"), (updated, "updated"), (deleted, "deleted")] if n]
                    if summary:
                        st.success("Invoices: " + ", ".join(summary) + ".")
                    for e in row_errors:
                        st.error(e)
                    if not summary and not row_errors:
                        st.info("No changes to save.")
                    if summary:
                        refresh()

        with tab_memo_sub:
            st.caption("Applied before checks are issued — reduces the batch's net payable.")
            scoped_memos_df = q(
                """SELECT credit_memo_id, memo_ref, memo_date, amount, reason
                   FROM credit_memos WHERE statement_id = ? ORDER BY credit_memo_id""",
                (int(batch_scope_pick),),
            )
            if batch_is_locked:
                if scoped_memos_df.empty:
                    st.caption("No credit memos applied to this order statement yet.")
                else:
                    st.dataframe(
                        scoped_memos_df,
                        hide_index=True,
                        column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")},
                    )
                st.caption("This batch is cancelled and can't be edited." if batch_is_cancelled else "Batch is locked — unlock it above to add, edit, or delete a credit memo.")
            else:
                st.caption("Add, edit, or delete rows below, then Save. A new row needs a reference and a positive Amount.")
                original_memos_df = scoped_memos_df.copy()
                original_memos_df["memo_date"] = pd.to_datetime(original_memos_df["memo_date"]).dt.date
                edited_memos_df = st.data_editor(
                    original_memos_df,
                    hide_index=True,
                    num_rows="dynamic",
                    disabled=["credit_memo_id"],
                    key=batch_scoped_key("edit_memos_editor"),
                    column_config={
                        "credit_memo_id": st.column_config.NumberColumn("ID"),
                        "memo_ref": st.column_config.TextColumn("Reference", default=""),
                        "memo_date": st.column_config.DateColumn("Memo Date", default=date.today()),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01, default=0.0),
                        "reason": st.column_config.TextColumn("Reason", default=""),
                    },
                )
                if st.button("Save credit memo changes", icon=":material/save:", key=batch_scoped_key("save_memo_edits")) or dup_resume("save_memo_changes"):
                  if dup_needs_confirm("save_memo_changes", [
                      w for _, r in edited_memos_df.iterrows()
                      if pd.isna(r["credit_memo_id"]) and cell_text(r["memo_ref"]).strip() and float(r["amount"] or 0) > 0
                      for w in credit_memo_warnings(scoped_creditor_id, cell_text(r["memo_ref"]), r["memo_date"], float(r["amount"]))
                  ]):
                    pass
                  elif not guard_duplicate_submit("save_memo_changes", int(batch_scope_pick), edited_memos_df.to_csv(index=False)):
                    st.warning("Duplicate submission ignored — identical to the click just before it.")
                  else:
                    original_by_id = original_memos_df.set_index("credit_memo_id")
                    seen_ids, added, updated, row_errors = set(), 0, 0, []
                    for _, r in edited_memos_df.iterrows():
                        if pd.isna(r["credit_memo_id"]):
                            memo_ref, amount = cell_text(r["memo_ref"]).strip(), float(r["amount"] or 0)
                            if not memo_ref and amount <= 0:
                                continue  # untouched blank template row
                            if not memo_ref or amount <= 0:
                                row_errors.append("New credit memo row needs a reference and a positive Amount.")
                                continue
                            ok, msg = add_credit_memo(int(batch_scope_pick), memo_ref, r["memo_date"], amount, cell_text(r["reason"]))
                            if ok:
                                added += 1
                            else:
                                row_errors.append(msg)
                            continue

                        memo_id = int(r["credit_memo_id"])
                        seen_ids.add(memo_id)
                        orig = original_by_id.loc[memo_id]
                        is_changed = (
                            cell_text(r["memo_ref"]) != cell_text(orig["memo_ref"])
                            or str(r["memo_date"]) != str(orig["memo_date"])
                            or abs(float(r["amount"]) - float(orig["amount"])) > 0.001
                            or cell_text(r["reason"]) != cell_text(orig["reason"])
                        )
                        if not is_changed:
                            continue
                        ok, msg = edit_credit_memo(memo_id, cell_text(r["memo_ref"]), r["memo_date"], float(r["amount"]), cell_text(r["reason"]))
                        if ok:
                            updated += 1
                        else:
                            row_errors.append(f"Credit memo {memo_id}: {msg}")

                    deleted = 0
                    for removed_id in set(original_by_id.index) - seen_ids:
                        ok, msg = delete_credit_memo(int(removed_id))
                        if ok:
                            deleted += 1
                        else:
                            row_errors.append(msg)

                    summary = [f"{n} {label}" for n, label in [(added, "added"), (updated, "updated"), (deleted, "deleted")] if n]
                    if summary:
                        st.success("Credit memos: " + ", ".join(summary) + ".")
                    for e in row_errors:
                        st.error(e)
                    if not summary and not row_errors:
                        st.info("No changes to save.")
                    if summary:
                        refresh()

        with tab_checks_sub:
            st.markdown("##### Issue a check")
            _park_flash = st.session_state.pop("_park_flash", None)
            if _park_flash:
                st.success(_park_flash)
            net_total, active_checks_sum, remaining_balance = get_batch_balance(int(batch_scope_pick))
            is_balanced = abs(remaining_balance) <= 0.005
            is_overpaid = remaining_balance < -0.005

            if net_total <= 0:
                st.info("No payable total yet — add invoices (and any credit memos) before issuing checks.")
            elif is_overpaid:
                st.warning(f"Overpaid — active checks ({php(active_checks_sum)}) exceed the net total ({php(net_total)}) by {php(-remaining_balance)}. Correct or replace a check to restore balance.")
            elif is_balanced:
                st.success(f"Balanced — net total {php(net_total)} fully covered by {php(active_checks_sum)} in active checks.")
            else:
                st.info(f"Net total {php(net_total)} · Active checks so far {php(active_checks_sum)} · Remaining balance {php(remaining_balance)}")

            is_payable = not batch_is_locked and scoped_row["status"] != "Cancelled" and remaining_balance >= 0.01
            if not is_payable:
                reasons = []
                if scoped_row["status"] == "Cancelled":
                    reasons.append("batch is cancelled")
                elif batch_is_locked:
                    reasons.append("batch is locked")
                if remaining_balance < 0.01:
                    reasons.append("no remaining balance to pay" if net_total > 0 else "batch has no payable total yet")
                st.caption("Cannot issue a new check here: " + "; ".join(reasons) + ".")
            else:
                st.caption(f"Remaining balance: {php(remaining_balance)} — split it into as many checks as you like; amounts are pre-calculated evenly and fully editable. No check number yet — that's assigned when the check is printed.")

                latest_check_for_supplier = get_latest_check_for_creditor(scoped_creditor_id)
                if latest_check_for_supplier:
                    latest_date, latest_amount = latest_check_for_supplier
                    st.caption(f"Last check issued to this supplier: {latest_date.strftime('%b %d, %Y')} — {php(latest_amount)}.")

                num_checks_key = batch_scoped_key("issue_checks_count")
                grid_key = batch_scoped_key("issue_checks_editor")
                last_signature_key = batch_scoped_key("issue_checks_last_signature")

                open_invoices_df = get_invoices_with_remaining(int(batch_scope_pick))
                # Guardrail (2026-09-27): checks typed into this grid but never parked used to vanish when you opened another
                # order statement. The grid is now remembered per batch for the session and put back when you return.
                park_bases = st.session_state.setdefault("_park_bases", {})
                _sid = int(batch_scope_pick)
                _draft = load_park_draft(_sid)
                _split_key = batch_scoped_key("issue_split_mode")
                _restoring = False
                if _draft and _split_key not in st.session_state:          # widgets were dropped while another batch was open
                    if abs(_draft["balance"] - round(remaining_balance, 2)) <= 0.005 and _draft["invoice_ids"] == tuple(open_invoices_df["invoice_id"]):
                        st.session_state[_split_key] = _draft["mode"]
                        st.session_state[num_checks_key] = _draft["count"]
                        _restoring = True
                    else:
                        clear_park_draft(_sid)
                        park_bases.pop(_sid, None)
                        _draft = None
                        st.info("The checks you had typed for this batch were cleared because its invoices or balance changed in the meantime.")
                split_mode = st.radio(
                    "How to split", ["By number of checks", "One check per invoice"], horizontal=True, key=batch_scoped_key("issue_split_mode"),
                    help="Some suppliers want one check for each invoice. 'One check per invoice' makes a row for every invoice still to be paid, dated on that invoice's due date.",
                )
                per_invoice = split_mode == "One check per invoice" and not open_invoices_df.empty
                if per_invoice:
                    num_checks = len(open_invoices_df)
                    st.caption(f"{num_checks} invoice(s) still to pay — one check each, dated on the invoice's due date and for its remaining amount. Every date and amount stays editable below.")
                else:
                    if split_mode == "One check per invoice":
                        st.caption("No open invoices to make checks for.")
                    num_checks = st.number_input(
                        "Number of checks", min_value=1, max_value=50, step=1,
                        value=int(st.session_state.get(num_checks_key, 1)), key=num_checks_key,
                    )
                    st.caption("Two ways to issue: use the count above to pre-split evenly (whole pesos, editable), or just add/remove rows in the table below and type amounts yourself.")

                # Re-seed the grid whenever the count OR the remaining balance changes (e.g. a check
                # in this batch got cancelled/corrected) — otherwise a stale pre-split from before
                # that change would silently linger in session_state.
                # Default check date = the batch's invoice due date (the earliest one still unpaid if there are several),
                # so a check is dated when it is actually due; every date stays editable in the grid.
                due_val = q_live("SELECT MIN(due_date) AS d FROM invoices WHERE statement_id = ? AND status IN ('Unpaid', 'Partially Paid')", (int(batch_scope_pick),))["d"][0]
                if not has_value(due_val):
                    due_val = q_live("SELECT MIN(due_date) AS d FROM invoices WHERE statement_id = ? AND status != 'Cancelled'", (int(batch_scope_pick),))["d"][0]
                default_check_date = date.fromisoformat(str(due_val)[:10]) if has_value(due_val) else date.today()
                signature = (int(num_checks), round(remaining_balance, 2), default_check_date, per_invoice,
                             tuple(open_invoices_df["invoice_id"]) if per_invoice else ())
                if st.session_state.get(last_signature_key) != signature:
                    st.session_state[last_signature_key] = signature
                    st.session_state.pop(grid_key, None)
                    if not _restoring:
                        park_bases.pop(_sid, None)

                if per_invoice:
                    default_checks_df = pd.DataFrame({
                        "check_date": [date.fromisoformat(str(d)[:10]) for d in open_invoices_df["due_date"]],
                        "amount": [round(float(x), 2) for x in open_invoices_df["remaining"]],
                    })
                else:
                    default_checks_df = pd.DataFrame({
                        "check_date": [default_check_date] * int(num_checks),
                        "amount": split_remaining_evenly(remaining_balance, int(num_checks)),
                    })
                computed_default_df = default_checks_df
                if st.session_state.get(last_signature_key) is not None and _restoring:
                    park_bases[_sid] = pd.DataFrame({"check_date": [date.fromisoformat(d) for d, _ in _draft["rows"]], "amount": [x for _, x in _draft["rows"]]})
                elif grid_key not in st.session_state:
                    park_bases.pop(_sid, None)
                default_checks_df = park_bases.get(_sid, computed_default_df)
                edited_checks_df = st.data_editor(
                    default_checks_df,
                    hide_index=True,
                    num_rows="dynamic",
                    key=grid_key,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date", default=default_check_date),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01, default=0.0),
                    },
                )

                _typed = [(str(r["check_date"])[:10], round(float(r["amount"]), 2)) for _, r in edited_checks_df.iterrows() if pd.notna(r["amount"]) and float(r["amount"]) > 0]
                _computed = [(str(r["check_date"])[:10], round(float(r["amount"]), 2)) for _, r in computed_default_df.iterrows()]
                grid_is_edited = bool(_typed) and _typed != _computed
                if grid_is_edited:
                    _now_draft = {"mode": split_mode, "count": int(num_checks), "balance": round(remaining_balance, 2),
                                  "invoice_ids": tuple(int(i) for i in open_invoices_df["invoice_id"]), "rows": _typed}
                    if not _draft or any(_draft.get(k) != v for k, v in _now_draft.items()):
                        save_park_draft(_sid, split_mode, int(num_checks), remaining_balance, list(open_invoices_df["invoice_id"]), _typed)
                    st.warning(":material/edit_note: These checks are typed in but **not parked yet** — click **Park Metrobank checks** below to save them. "
                               "They are remembered — even if you open another order statement or refresh the page — until you park them.")
                elif _draft:
                    clear_park_draft(_sid)
                grid_total = float(edited_checks_df["amount"].sum())
                grid_diff = remaining_balance - grid_total
                grid_matches = abs(grid_diff) <= 0.005
                if grid_matches:
                    st.success(f"Grid total {php(grid_total)} matches the remaining balance.")
                else:
                    st.warning(f"Grid total {php(grid_total)} vs remaining balance {php(remaining_balance)} — off by {php(grid_diff)}. Adjust amounts to match exactly before issuing.")

                # num_rows="dynamic" always leaves one blank "add a row" affordance in the
                # returned data — drop it rather than reject it as "needs an amount".
                rows = [r for r in edited_checks_df.to_dict("records") if float(r.get("amount") or 0) > 0]

                # Same-day-duplicate warning (2026-09-17, per user request): this business
                # deliberately avoids ever writing two checks to the same supplier on the same
                # day, postdating past the due date instead specifically to prevent it. A
                # warning, not a block -- st.warning() doesn't disable the submit button below,
                # since there can be a genuine reason to do it anyway and the owner asked to keep
                # that override available.
                existing_check_dates = get_existing_check_dates_for_creditor(scoped_creditor_id)
                grid_dates = [r["check_date"] for r in rows]
                same_day_conflicts = sorted({
                    d for d in grid_dates
                    if d in existing_check_dates or grid_dates.count(d) > 1
                })
                if same_day_conflicts:
                    conflict_list = ", ".join(d.strftime("%b %d, %Y") for d in same_day_conflicts)
                    st.warning(
                        f":material/warning: This supplier already has (or would have) more than one check dated {conflict_list} — "
                        "double-check before parking. Still allowed if that's genuinely intended."
                    )

                alloc_ready = False
                alloc_grid_key = batch_scoped_key("issue_checks_alloc_editor")
                edited_alloc_df = pd.DataFrame(columns=["check_row", "check_date", "invoice_id", "doc_ref", "allocated"])

                if grid_matches and rows:
                    st.markdown("##### Allocate to invoices")
                    if open_invoices_df.empty:
                        st.error("No open invoices left to allocate these checks to — every invoice in this batch is already fully allocated or Cancelled. Add more invoices, or reduce the check amounts.")
                    else:
                        st.caption("Every check must be tied to the specific invoice(s) it pays — auto-filled oldest due date first below; adjust the amounts if a check should be split differently.")
                        alloc_signature_key = batch_scoped_key("issue_checks_alloc_signature")
                        alloc_signature = (repr(rows), repr(open_invoices_df["invoice_id"].tolist()), repr(open_invoices_df["remaining"].tolist()))
                        if st.session_state.get(alloc_signature_key) != alloc_signature:
                            st.session_state[alloc_signature_key] = alloc_signature
                            st.session_state.pop(alloc_grid_key, None)

                        preview_df = waterfall_allocate_preview(rows, open_invoices_df)
                        if preview_df.empty:
                            st.error("Couldn't auto-allocate — check the amounts above.")
                        else:
                            edited_alloc_df = st.data_editor(
                                preview_df[["check_row", "check_date", "doc_ref", "allocated"]],
                                hide_index=True,
                                disabled=["check_row", "check_date", "doc_ref"],
                                key=alloc_grid_key,
                                column_config={
                                    "check_row": st.column_config.NumberColumn("Check #"),
                                    "check_date": st.column_config.DateColumn("Check Date"),
                                    "doc_ref": st.column_config.TextColumn("Invoice"),
                                    "allocated": st.column_config.NumberColumn("Allocated", format="₱ %.2f", min_value=0.01, step=0.01),
                                },
                            )
                            edited_alloc_df = edited_alloc_df.copy()
                            edited_alloc_df["invoice_id"] = preview_df["invoice_id"].values

                            alloc_ok = True
                            per_check_totals = edited_alloc_df.groupby("check_row")["allocated"].sum()
                            for idx, row in enumerate(rows, start=1):
                                allocated_for_row = float(per_check_totals.get(idx, 0.0))
                                if abs(allocated_for_row - float(row["amount"])) > 0.01:
                                    alloc_ok = False
                                    st.warning(f"Check #{idx} ({php(row['amount'])}) has {php(allocated_for_row)} allocated — must match exactly.")

                            per_invoice_totals = edited_alloc_df.groupby("invoice_id")["allocated"].sum()
                            for _, inv in open_invoices_df.iterrows():
                                allocated_for_inv = float(per_invoice_totals.get(inv["invoice_id"], 0.0))
                                if allocated_for_inv > float(inv["remaining"]) + 0.01:
                                    alloc_ok = False
                                    st.warning(f"Invoice {inv['doc_ref']} only has {php(inv['remaining'])} remaining — {php(allocated_for_inv)} allocated.")

                            alloc_ready = alloc_ok

                memo_status = get_batch_memo_status(int(batch_scope_pick))
                memo_guard_blocking = memo_status["unapplied_count"] > 0 and not memo_status["confirmed"]
                if memo_guard_blocking:
                    st.warning(
                        f"This batch has {memo_status['unapplied_count']} credit memo(s) totaling {php(memo_status['total_amount'])} "
                        f"reducing the net total to {php(net_total)} — confirm you've reviewed them, or remove one, before issuing checks."
                    )
                    mc1, mc2 = st.columns(2)
                    with mc1:
                        if st.button("✓ Confirm", key=batch_scoped_key("confirm_memos_btn")):
                            confirm_credit_memos(int(batch_scope_pick))
                            refresh()
                    with mc2:
                        remove_toggle_key = batch_scoped_key("show_remove_memo_form")
                        if st.button("Remove memo", key=batch_scoped_key("remove_memo_toggle_btn")):
                            st.session_state[remove_toggle_key] = not st.session_state.get(remove_toggle_key, False)
                    if st.session_state.get(batch_scoped_key("show_remove_memo_form")):
                        batch_memos_df = q("SELECT credit_memo_id, memo_ref, amount FROM credit_memos WHERE statement_id = ?", (int(batch_scope_pick),))
                        if batch_memos_df.empty:
                            st.caption("No credit memos left on this batch.")
                        else:
                            memo_options = {
                                int(r["credit_memo_id"]): f"{r['memo_ref']} — {php(r['amount'])}"
                                for _, r in batch_memos_df.iterrows()
                            }
                            memo_pick = st.selectbox(
                                "Which memo to remove?", options=list(memo_options), format_func=lambda mid: memo_options[mid],
                                key=batch_scoped_key("remove_memo_pick"),
                            )
                            if st.button("Confirm removal", key=batch_scoped_key("remove_memo_confirm_btn")):
                                ok, msg = delete_credit_memo(int(memo_pick))
                                (st.success if ok else st.error)(msg)
                                if ok:
                                    st.session_state.pop(batch_scoped_key("show_remove_memo_form"), None)
                                    refresh()

                if st.button(
                    "Park Metrobank checks", icon=":material/payments:",
                    key=batch_scoped_key("issue_checks_submit"), disabled=not grid_matches or not alloc_ready or memo_guard_blocking,
                ) or dup_resume("issue_checks_grid"):
                    if dup_needs_confirm("issue_checks_grid", check_warnings(scoped_creditor_id, [(r["amount"], r["check_date"]) for r in rows])):
                        pass
                    else:
                        # Confirmation first: shows which invoice each check pays and for how much, then parks the checks, ties them to
                        # their invoices and -- if the batch is still a Draft -- promotes it to Issued (2026-09-26, per user).
                        _park_lines = []
                        for _row_no, _row in enumerate(rows, start=1):
                            _parts = edited_alloc_df[edited_alloc_df["check_row"] == _row_no]
                            _bits = []
                            _full = len(_parts) == 1
                            for _, _p in _parts.iterrows():
                                _inv = open_invoices_df[open_invoices_df["invoice_id"] == _p["invoice_id"]].iloc[0]
                                _left = round(float(_inv["remaining"]) - float(_p["allocated"]), 2)
                                _full = _full and abs(_left) <= 0.005
                                _bits.append(f"invoice **{_inv['doc_ref']}** {php(_p['allocated'])}" + ("" if abs(_left) <= 0.005 else f" (of {php(_inv['remaining'])}, {php(_left)} still unpaid)"))
                            _mark = "✓ matches the invoice" if _full else "⚠ check the split"
                            _park_lines.append(f"- {_row['check_date']} · **{php(_row['amount'])}** → " + " + ".join(_bits) + f" — {_mark}")
                        _is_draft = str(scoped_row["status"]) == "Draft"
                        _park_summary = (
                            f"Park **{len(rows)} check(s)** totaling **{php(sum(float(r['amount']) for r in rows))}** for this batch?" + chr(10) + chr(10)
                            + chr(10).join(_park_lines) + chr(10) + chr(10)
                            + ("This batch is still a **Draft**: parking promotes it to **Issued** and gives it its permanent OS number." if _is_draft
                               else "The batch is already Issued.")
                        )

                        def _do_park(sid=int(batch_scope_pick), cid=scoped_creditor_id, park_rows=list(rows), alloc_df=edited_alloc_df.copy(), draft=_is_draft):
                            ok, msg, check_ids = issue_checks_grid(cid, sid, park_rows)
                            if not ok:
                                return False, msg
                            alloc_by_row: dict[int, list[tuple[int, float]]] = {i + 1: [] for i in range(len(park_rows))}
                            for _, arow in alloc_df.iterrows():
                                alloc_by_row[int(arow["check_row"])].append((int(arow["invoice_id"]), float(arow["allocated"])))
                            problems = []
                            for row_num, check_id in enumerate(check_ids, start=1):
                                a_ok, a_msg = allocate_check_to_invoices(check_id, alloc_by_row.get(row_num, []))
                                if not a_ok:
                                    problems.append(a_msg)
                            sync_invoice_payment_status(sid)
                            promoted = ""
                            if draft:
                                try:
                                    promoted = f" The batch is now Issued as {promote_draft_statement(sid, 'Issued')}."
                                except ValueError as e:
                                    problems.append(f"the batch could not be promoted ({e})")
                            st.session_state.pop(grid_key, None)
                            st.session_state.pop(alloc_grid_key, None)
                            clear_park_draft(sid)
                            st.session_state.get("_park_bases", {}).pop(sid, None)
                            st.session_state["_park_flash"] = msg + promoted + (" Problems: " + "; ".join(problems) if problems else "")
                            return True, msg

                        confirm_create_dialog(_park_summary, batch_scoped_key("issue_checks_confirm"), _do_park)

            st.markdown("##### Checks already issued against this order statement")
            scoped_checks_df = q(
                "SELECT check_id, check_number, check_date, amount, status, datetime(printed_at, 'localtime') AS printed_at FROM checks_issued WHERE statement_id = ? ORDER BY check_id",
                (int(batch_scope_pick),),
            )
            if scoped_checks_df.empty:
                st.caption("No checks issued against this order statement yet.")
            else:
                display_scoped_checks = scoped_checks_df.copy()
                display_scoped_checks["check_number"] = display_scoped_checks["check_number"].fillna("Pending (not yet printed)")
                st.dataframe(
                    display_scoped_checks,
                    hide_index=True,
                    column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")},
                )

            active_checks_df = scoped_checks_df[scoped_checks_df["status"] == "Active"].copy() if not scoped_checks_df.empty else scoped_checks_df
            parked_active_df = active_checks_df[~active_checks_df["check_number"].apply(has_value)] if not active_checks_df.empty else active_checks_df
            numbered_active_df = active_checks_df[active_checks_df["check_number"].apply(has_value)] if not active_checks_df.empty else active_checks_df

            if PRINT_CHECKS_IN_APP:
                render_check_print_section(scoped_checks_df, scoped_row["creditor_name"], key_prefix=batch_scoped_key("os_check_print"))
            render_assign_number_section(scoped_checks_df, key_prefix=batch_scoped_key("os_check_assign"))

            st.markdown("##### Edit parked checks")
            if parked_active_df.empty:
                st.caption("No check without a number in this batch to edit — once a check has its number it is final (use Correct an active check).")
            else:
                st.caption("Date and amount stay freely editable until a check has its check number — no unlock needed, even if it was already exported to Chrysanth (change it there too). Every change is logged automatically (old → new + timestamp).")
                original_parked_df = parked_active_df[["check_id", "check_date", "amount"]].copy()
                original_parked_df["check_date"] = pd.to_datetime(original_parked_df["check_date"]).dt.date
                edited_parked_df = st.data_editor(
                    original_parked_df,
                    hide_index=True,
                    num_rows="fixed",
                    disabled=["check_id"],
                    key=batch_scoped_key("edit_parked_editor"),
                    column_config={
                        "check_id": st.column_config.NumberColumn("ID"),
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                    },
                )
                if st.button("Save parked check edits", icon=":material/save:", key=batch_scoped_key("save_parked_edits")):
                    original_by_id = original_parked_df.set_index("check_id")
                    updated, row_errors = 0, []
                    for _, r in edited_parked_df.iterrows():
                        chk_id = int(r["check_id"])
                        orig = original_by_id.loc[chk_id]
                        if str(r["check_date"]) != str(orig["check_date"]) or abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                            ok, msg = edit_parked_check(chk_id, float(r["amount"]), r["check_date"])
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                    if updated:
                        st.success(f"{updated} parked check(s) updated.")
                    for e in row_errors:
                        st.error(e)
                    if not updated and not row_errors:
                        st.info("No changes to save.")
                    if updated:
                        refresh()

            st.markdown("##### Un-park checks")
            unparkable_df = parked_active_df[~parked_active_df["check_number"].apply(has_value)] if not parked_active_df.empty else parked_active_df
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to un-park checks.")
            elif unparkable_df.empty:
                st.caption("No check to un-park — only checks that have no number yet can be taken back.")
            else:
                st.caption("Took the wrong split, or parked by mistake? Pick parked checks to take back — they are deleted completely (with their invoice links), "
                           "the balance returns and you can park again. A check that already has its number is final and never touched (cancel or replace it instead). "
                           "If one was already exported to Chrysanth, remove it there too.")
                unpark_labels = {int(r["check_id"]): f"{r['check_date']} — {php(r['amount'])}" for _, r in unparkable_df.iterrows()}
                unpark_pick = st.multiselect("Parked checks to take back", options=list(unpark_labels), format_func=lambda cid: unpark_labels[cid],
                                             key=batch_scoped_key("unpark_pick"))
                if st.button("Un-park selected checks", icon=":material/undo:", disabled=not unpark_pick, key=batch_scoped_key("unpark_btn")):
                    def _do_unpark(sid=int(batch_scope_pick), picked=list(unpark_pick)):
                        ok, msg = unpark_checks(sid, picked)
                        if ok:
                            st.session_state["_park_flash"] = msg
                            st.session_state.pop(batch_scoped_key("unpark_pick"), None)
                        return ok, msg
                    confirm_create_dialog(
                        f"Delete **{len(unpark_pick)} parked check(s)** totaling **{php(sum(float(unparkable_df[unparkable_df['check_id'] == c]['amount'].iloc[0]) for c in unpark_pick))}**? "
                        "They are removed completely, their invoice links are released and the balance is available to park again."
                        + (" Some were already exported to Chrysanth — remove them there too." if unparkable_df[unparkable_df["check_id"].isin(unpark_pick) & unparkable_df["printed_at"].apply(has_value)].shape[0] else ""),
                        batch_scoped_key("unpark_confirm"), _do_unpark)

            st.markdown("##### Correct an active check")
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to correct a check.")
            elif numbered_active_df.empty:
                st.caption("No numbered active check in this batch to correct (assign a number first — above — or edit an unprinted check via 'Edit parked checks').")
            else:
                st.caption("Edit Check Number, Check Date, or Amount directly, then Save. Every change is logged automatically (old → new + timestamp). Amount/Date can only be corrected once a check has been printed — otherwise edit it via 'Edit parked checks' above.")
                original_active_df = numbered_active_df[["check_id", "check_number", "check_date", "amount"]].copy()
                original_active_df["check_date"] = pd.to_datetime(original_active_df["check_date"]).dt.date
                edited_active_df = st.data_editor(
                    original_active_df,
                    hide_index=True,
                    num_rows="fixed",
                    disabled=["check_id"],
                    key=batch_scoped_key("correct_checks_editor"),
                    column_config={
                        "check_id": st.column_config.NumberColumn("ID"),
                        "check_number": st.column_config.TextColumn("Check Number"),
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                    },
                )
                if st.button("Save check corrections", icon=":material/save:", key=batch_scoped_key("save_check_corrections")):
                    original_by_id = original_active_df.set_index("check_id")
                    updated, row_errors = 0, []
                    for _, r in edited_active_df.iterrows():
                        chk_id = int(r["check_id"])
                        orig = original_by_id.loc[chk_id]
                        new_number = cell_text(r["check_number"]).strip()
                        if new_number != str(orig["check_number"]).strip():
                            ok, msg = apply_check_correction(chk_id, "Check Number", new_number, "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                        if str(r["check_date"]) != str(orig["check_date"]):
                            ok, msg = apply_check_correction(chk_id, "Check Date", str(r["check_date"]), "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                        if abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                            ok, msg = apply_check_correction(chk_id, "Amount", str(float(r["amount"])), "")
                            if ok:
                                updated += 1
                            else:
                                row_errors.append(f"Check {chk_id}: {msg}")
                    if updated:
                        st.success(f"{updated} correction(s) saved.")
                    for e in row_errors:
                        st.error(e)
                    if not updated and not row_errors:
                        st.info("No changes to save.")
                    if updated:
                        refresh()

                with st.expander("Add a note to a check"):
                    note_pick = st.selectbox(
                        "Check",
                        options=active_checks_df["check_id"],
                        format_func=lambda chid: active_checks_df.set_index("check_id").loc[chid, "check_number"] or "Pending (not yet printed)",
                        key=batch_scoped_key("note_pick"),
                    )
                    note_text = st.text_input("Note", key=batch_scoped_key("note_text"))
                    if st.button("Add note", icon=":material/sticky_note_2:", key=batch_scoped_key("add_note_btn")):
                        if note_text.strip():
                            ok, msg = apply_check_correction(int(note_pick), "Payee Note", note_text.strip(), "")
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()
                        else:
                            st.error("Note can't be blank.")

            st.markdown("##### Void a check")
            if batch_is_locked:
                st.caption("This batch is cancelled." if batch_is_cancelled else "Batch is locked — unlock it above to void a check.")
            elif active_checks_df.empty:
                st.caption("No active check in this batch to void.")
            else:
                st.caption("Void just cancels it (money returns to the available balance); tick 'Reissue' to also cut a replacement (parked, no number yet) in one step.")
                void_pick = st.selectbox(
                    "Check",
                    options=active_checks_df["check_id"],
                    format_func=lambda chid: f"{active_checks_df.set_index('check_id').loc[chid, 'check_number'] or 'Pending'} ({php(active_checks_df.set_index('check_id').loc[chid, 'amount'])})",
                    key=batch_scoped_key("void_pick"),
                )
                void_reissue = st.checkbox("Reissue as a new (parked) check", key=batch_scoped_key("void_reissue_toggle"))
                if void_reissue:
                    void_new_date = st.date_input("New check date", value=date.today(), key=batch_scoped_key("void_new_date"))
                void_reason = st.text_input("Reason (optional)", placeholder="e.g. postdated past new due date", key=batch_scoped_key("void_reason"))
                if st.button("Void & reissue" if void_reissue else "Void check", icon=":material/cancel:", key=batch_scoped_key("void_submit_btn")):
                    if void_reissue:
                        ok, msg, new_check_id = replace_check(int(void_pick), void_new_date, void_reason)
                    else:
                        ok, msg = cancel_check(int(void_pick))
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()

    with st.expander("Vendor SOA matcher"):
        soa_creditor = selectbox_creditor("Supplier", "SUPPLIER", "soa_creditor")
        soa_file = st.file_uploader("Upload vendor statement of account", type=["csv", "xlsx", "xls", "pdf", "png", "jpg", "jpeg", "tiff", "bmp"])
        if soa_creditor is not None and soa_file is not None and st.button("Reconcile SOA", icon=":material/fact_check:"):
            try:
                result = reconcile_vendor_soa(soa_creditor, soa_file.getvalue(), soa_file.name)
            except Exception as e:
                result = {"success": False, "error": f"Could not process this file: {e}"}
            if not result["success"]:
                st.error(result["error"])
            else:
                s = result["summary"]
                st.info(f"Matched {s['matched']} · Missing in AP {s['missing_in_ap']} · Mismatches {s['mismatches']} · Omitted from SOA {s['omitted_from_soa']}")
                m1, m2, m3, m4 = st.tabs(["Matched", "Missing in AP", "Amount mismatches", "Omitted from SOA"])
                with m1:
                    st.dataframe(result["matched"], hide_index=True)
                with m2:
                    st.dataframe(result["missing_in_ap"], hide_index=True)
                with m3:
                    st.dataframe(result["amount_mismatches"], hide_index=True)
                with m4:
                    st.dataframe(result["omitted_from_soa"], hide_index=True)

# --------------------------------------------------------------------------
# Tab: Creditor Master
# --------------------------------------------------------------------------
with tab_creditor_master:
    st.header("Creditor Master")
    st.caption("Master registry of payees tagged supplier, lender, or bank loan. Credit card payees stay in their own track (Credit cards tab).")

    creditor_master_df = q(
        "SELECT creditor_id, creditor_name, creditor_type, tin, contact_person, contact_number, email, terms, is_active, datetime(created_at, 'localtime') AS created_at "
        "FROM creditors WHERE creditor_type IN ('SUPPLIER', 'LENDER', 'BANK_LOAN') ORDER BY creditor_type, creditor_name"
    )
    creditor_master_df["is_active"] = creditor_master_df["is_active"].astype(int) == 1
    filter_col1, filter_col2, filter_col3 = st.columns([2, 2, 1])
    with filter_col1:
        search_master = st.text_input(
            "Search", placeholder="Name, TIN, contact person/number, or email", key="creditor_master_search"
        )
    with filter_col2:
        type_filter_master = st.multiselect(
            "Filter: type", options=["SUPPLIER", "LENDER", "BANK_LOAN"], key="creditor_master_type_filter"
        )
    with filter_col3:
        show_inactive = st.checkbox("Show inactive too", key="creditor_master_show_inactive")
    original_master_df = creditor_master_df[creditor_master_df["creditor_type"].isin(type_filter_master)] if type_filter_master else creditor_master_df
    if not show_inactive:
        original_master_df = original_master_df[original_master_df["is_active"]]
    if search_master.strip():
        needle = search_master.strip().lower()
        search_cols = ["creditor_name", "tin", "contact_person", "contact_number", "email"]
        hit = pd.Series(False, index=original_master_df.index)
        for col in search_cols:
            hit = hit | original_master_df[col].fillna("").astype(str).str.lower().str.contains(needle, regex=False)
        original_master_df = original_master_df[hit]
    original_master_df = original_master_df.reset_index(drop=True)

    st.caption(
        "Add, edit, or deactivate creditors directly in the grid below, then Save. A new row needs a Name and Type. "
        "Uncheck Active to deactivate (reversible — check it again to reactivate); deleting a row outright only "
        "removes it completely if it has no transaction history yet, otherwise it's deactivated instead, same as "
        "unchecking Active. A row's Type can't be changed once it has transaction history, to avoid silently "
        "reclassifying existing batches, loan notes, or bank loans."
    )
    edited_master_df = st.data_editor(
        original_master_df,
        hide_index=True,
        num_rows="dynamic",
        disabled=["creditor_id", "created_at"],
        key="creditor_master_editor",
        column_config={
            "creditor_id": st.column_config.NumberColumn("ID"),
            "creditor_name": st.column_config.TextColumn("Name", default=""),
            "creditor_type": st.column_config.SelectboxColumn("Type", options=["SUPPLIER", "LENDER", "BANK_LOAN"], default="SUPPLIER"),
            "tin": st.column_config.TextColumn("TIN", default=""),
            "contact_person": st.column_config.TextColumn("Contact person", default=""),
            "contact_number": st.column_config.TextColumn("Contact number", default=""),
            "email": st.column_config.TextColumn("Email", default=""),
            "terms": st.column_config.SelectboxColumn("Terms", options=SUPPLIER_TERMS_OPTIONS, default=DEFAULT_SUPPLIER_TERMS),
            "is_active": st.column_config.CheckboxColumn("Active", default=True),
            "created_at": st.column_config.TextColumn("Created"),
        },
    )

    if st.button("Save creditor changes", icon=":material/save:", key="save_creditor_master_edits") or dup_resume("save_creditor_master"):
        if dup_needs_confirm("save_creditor_master", [
            w for _, r in edited_master_df.iterrows()
            if pd.isna(r["creditor_id"]) and cell_text(r["creditor_name"]).strip()
            for w in creditor_warnings(cell_text(r["creditor_name"]), cell_text(r["creditor_type"]))
        ]):
            pass
        elif not guard_duplicate_submit("save_creditor_master", edited_master_df.to_csv(index=False)):
            st.warning("Duplicate submission ignored — identical to the click just before it.")
        else:
            original_by_id = original_master_df.set_index("creditor_id")
            seen_ids = set()
            added = updated = reactivated = deactivated = removed = 0
            row_errors = []

            for _, r in edited_master_df.iterrows():
                if pd.isna(r["creditor_id"]):
                    name = cell_text(r["creditor_name"]).strip()
                    ctype = cell_text(r["creditor_type"]).strip()
                    if not name and not ctype:
                        continue  # untouched blank template row
                    if not name or ctype not in ("SUPPLIER", "LENDER", "BANK_LOAN"):
                        row_errors.append("New creditor row needs a Name and a Type.")
                        continue
                    add_creditor(
                        name, ctype, cell_text(r["tin"]), cell_text(r["contact_person"]),
                        cell_text(r["contact_number"]), cell_text(r["email"]), cell_text(r["terms"]),
                    )
                    added += 1
                    continue

                cid = int(r["creditor_id"])
                seen_ids.add(cid)
                orig = original_by_id.loc[cid]

                new_type = cell_text(r["creditor_type"]).strip() or orig["creditor_type"]
                if new_type != orig["creditor_type"] and creditor_reference_counts(cid):
                    row_errors.append(f"{orig['creditor_name']}: can't change type — it already has transaction history. Kept as {orig['creditor_type']}.")
                    new_type = orig["creditor_type"]

                details_changed = (
                    cell_text(r["creditor_name"]) != cell_text(orig["creditor_name"])
                    or new_type != orig["creditor_type"]
                    or cell_text(r["tin"]) != cell_text(orig["tin"])
                    or cell_text(r["contact_person"]) != cell_text(orig["contact_person"])
                    or cell_text(r["contact_number"]) != cell_text(orig["contact_number"])
                    or cell_text(r["email"]) != cell_text(orig["email"])
                    or cell_text(r["terms"]) != cell_text(orig["terms"])
                )
                if details_changed:
                    update_creditor(
                        cid, cell_text(r["creditor_name"]), new_type, cell_text(r["tin"]),
                        cell_text(r["contact_person"]), cell_text(r["contact_number"]), cell_text(r["email"]),
                        cell_text(r["terms"]),
                    )
                    updated += 1

                if bool(r["is_active"]) != bool(orig["is_active"]):
                    if r["is_active"]:
                        reactivate_creditor(cid)
                        reactivated += 1
                    else:
                        deactivate_creditor(cid)
                        deactivated += 1

            for removed_id in set(original_by_id.index) - seen_ids:
                ok, msg = delete_or_deactivate_creditor(int(removed_id))
                if ok:
                    removed += 1
                else:
                    row_errors.append(msg)

            summary = [
                f"{n} {label}" for n, label in
                [(added, "added"), (updated, "updated"), (reactivated, "reactivated"), (deactivated, "deactivated"), (removed, "removed")]
                if n
            ]
            if summary:
                st.success("Creditors: " + ", ".join(summary) + ".")
            for e in row_errors:
                st.error(e)
            if not summary and not row_errors:
                st.info("No changes to save.")
            if summary:
                refresh()

# --------------------------------------------------------------------------
# Tab 3: Lenders (Track 2)
# --------------------------------------------------------------------------
with tab_lenders:
    st.header("Private lenders")
    st.caption("Add or edit lender records in the Creditor Master tab.")

    if "_ln_mode_next" in st.session_state:
        st.session_state["ln_mode"] = st.session_state.pop("_ln_mode_next")

    loan_notes_df = q(
        """SELECT ln.loan_note_id, c.creditor_name, ln.note_ref, ln.issue_date, ln.principal_amount,
                  ln.monthly_interest_rate, ln.total_interest, ln.total_payable,
                  ln.total_payable - COALESCE(
                      (SELECT SUM(amount) FROM checks_issued
                       WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date <= DATE('now', 'localtime')), 0
                  ) AS outstanding,
                  ln.status, """ + loan_note_modified_sql("ln") + """ AS last_modified
           FROM loan_notes ln JOIN creditors c ON ln.creditor_id = c.creditor_id
           ORDER BY ln.loan_note_id DESC"""
    )

    ln_mode = st.segmented_control(
        "What do you want to do?",
        options=["🔍 Browse Loan Notes", "➕ New Loan Note"],
        default="🔍 Browse Loan Notes",
        key="ln_mode",
    )

    if ln_mode == "➕ New Loan Note":
        st.subheader("Create loan note")
        with st.form("new_loan_note_form", border=False):
            ln_creditor = selectbox_creditor("Lender", "LENDER", "ln_creditor")
            ln_issue_date = st.date_input("Issue date", value=date.today())
            ln_principal = st.number_input("Principal amount", min_value=0.0, step=1000.0)
            ln_rate = st.number_input("Monthly interest rate (%)", min_value=0.0, step=0.5, format="%.2f")
            st.caption("LN No. starts as a draft placeholder — use \"Promote to real LN No.\" below once it's ready to assign the permanent number. Total interest/payable are computed from the repayment schedule, not typed in.")
            if st.form_submit_button("Create loan note", icon=":material/description:"):
                if ln_creditor:
                    err = money_amount_error(ln_principal, "Principal amount")
                    if err:
                        st.error(err)
                    else:
                        def _do_create_loan_note(c=ln_creditor, d=ln_issue_date, p=ln_principal, r=ln_rate):
                            ok, msg, new_lnid = create_loan_note(c, d, p, r)
                            if ok:
                                st.session_state["selected_loan_note_id"] = new_lnid
                                st.session_state["_ln_mode_next"] = "🔍 Browse Loan Notes"
                            return ok, msg
                        confirm_create_dialog(
                            f"Create a loan note for **{selectbox_creditor_label('LENDER', ln_creditor)}** dated "
                            f"{ln_issue_date:%b %d, %Y}: principal **{php(ln_principal)}** at **{ln_rate:g}% per month**?"
                            + dup_note(loan_note_warnings(ln_creditor, ln_issue_date, ln_principal)),
                            "create_loan_note", _do_create_loan_note,
                        )
                else:
                    st.error("Lender is required.")
    else:
        st.subheader("Loan notes")
        if loan_notes_df.empty:
            st.caption("No loan notes yet — switch to \"New Loan Note\" above to create the first one.")
        else:
            ln_status_filter = st.multiselect(
                "Filter: status", options=sorted(loan_notes_df["status"].unique()), key="ln_filter_status"
            )
            filtered_loan_notes_df = loan_notes_df.copy()
            if ln_status_filter:
                filtered_loan_notes_df = filtered_loan_notes_df[filtered_loan_notes_df["status"].isin(ln_status_filter)]
            ln_mod_col, _ln_spacer = st.columns([1, 2])
            filtered_loan_notes_df = date_preset_filter(ln_mod_col, filtered_loan_notes_df, "last_modified", "Filter: last modified", "ln_filter_modified_preset")
            filtered_loan_notes_df = filtered_loan_notes_df.reset_index(drop=True)

            ln_browser_event = st.dataframe(
                filtered_loan_notes_df,
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="ln_browser_df",
                column_config={
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "monthly_interest_rate": st.column_config.NumberColumn("Monthly rate", format="%.2f%%"),
                    "total_interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                    "total_payable": st.column_config.NumberColumn("Total payable", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding (uncleared)", format="₱ %.2f"),
                },
            )
            ln_selected_rows = ln_browser_event.selection.rows if ln_browser_event and ln_browser_event.selection else []
            if ln_selected_rows and ln_selected_rows[0] < len(filtered_loan_notes_df):
                st.session_state["selected_loan_note_id"] = int(filtered_loan_notes_df.iloc[ln_selected_rows[0]]["loan_note_id"])

    selected_lnid = st.session_state.get("selected_loan_note_id") if not loan_notes_df.empty else None

    if selected_lnid is not None and not loan_notes_df.empty and selected_lnid in loan_notes_df["loan_note_id"].values:
        ln_row = loan_notes_df.set_index("loan_note_id").loc[selected_lnid]
        ln_creditor_id = int(q("SELECT creditor_id FROM loan_notes WHERE loan_note_id = ?", (selected_lnid,))["creditor_id"][0])
        ln_is_settled_or_defaulted = ln_row["status"] in ("Settled", "Defaulted", "Cancelled")
        ln_is_cancelled = ln_row["status"] == "Cancelled"

        with st.container(border=True):
            # Remaining/Outstanding only subtracts CLEARED checks (check_date already in the
            # past, same "effective_status" convention v_checks_ledger already uses) — a
            # postdated check hasn't actually left the bank yet, so it shouldn't count as paid
            # down until its date arrives.
            st.markdown(f"**{ln_row['note_ref']}** — {ln_row['creditor_name']}")
            if ln_row["total_payable"] in (None, ""):
                # Pending Setup (Phase 8a placeholder) -- no principal/rate on file yet. q()'s
                # underlying df_query() coerces SQL NULL to '' (fillna('')), not None -- so a
                # NULL total_payable arrives here as an empty string, not None.
                st.caption(f"Pending Setup — principal and rate not yet entered. Fill them in below (\"Edit loan note terms\") to activate this lender. Status: {ln_row['status']}")
            else:
                diff = round(ln_row["total_payable"] - float(q(
                    "SELECT COALESCE(SUM(amount),0) AS s FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_date <= DATE('now', 'localtime')",
                    (selected_lnid,),
                )["s"][0]), 2)
                st.caption(
                    f"Principal {php(ln_row['principal_amount'])} · Rate {ln_row['monthly_interest_rate']:g}%/mo · "
                    f"Total payable {php(ln_row['total_payable'])} · Remaining (uncleared) {php(diff)} · Status: {ln_row['status']}"
                )
            ln_col_promote, ln_col_status, ln_col_export, ln_col_remove = st.columns(4)
            with ln_col_promote:
                if str(ln_row["note_ref"]).startswith("DRAFT-"):
                    if st.button("Promote to real LN No.", icon=":material/arrow_upward:", key="ln_promote_btn"):
                        new_ref = promote_draft_loan_note(selected_lnid)
                        st.success(f"Promoted to {new_ref}.")
                        refresh()
                else:
                    st.caption("LN No. is permanent.")
            with ln_col_status:
                if ln_is_cancelled:
                    st.caption("Cancelled — permanent.")
                elif ln_row["status"] == "Pending Setup":
                    # Not a manual-override target -- "Edit loan note terms" below is how a
                    # placeholder note actually activates (entering real principal/rate flips
                    # it to Active there), and "Pending Setup" isn't one of this override's
                    # own option list anyway.
                    st.caption("Pending Setup — use \"Edit loan note terms\" below to activate.")
                else:
                    with st.expander("Override status"):
                        st.caption("Settled fires automatically once Outstanding (uncleared) hits ₱0 — every check issued must also have actually reached its date, not just been written. Use this only to force Defaulted, or to correct a mistake.")
                        override_status = st.selectbox("Status", ["Active", "Settled", "Defaulted"], index=["Active", "Settled", "Defaulted"].index(ln_row["status"]), key="ln_status_override")
                        if st.button("Save status", icon=":material/update:", key="ln_status_save_btn"):
                            ok, msg = update_loan_note_status(selected_lnid, override_status)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()
            with ln_col_export:
                with st.expander("Export Loan Note PDF"):
                    if st.button("Generate Loan Note PDF", icon=":material/picture_as_pdf:", key="ln_export_btn"):
                        ln_pdf_bytes = generate_loan_note_pdf(selected_lnid)
                        if ln_pdf_bytes:
                            pdf_preview_and_download(ln_pdf_bytes, f"loan_note_{ln_row['note_ref']}.pdf".replace(" ", "_"), key="ln_pdf_download", label="Loan Note PDF")
                        else:
                            st.error("Could not generate the Loan Note PDF.")
            with ln_col_remove:
                if ln_is_cancelled:
                    st.caption("Already cancelled.")
                else:
                    ln_check_count = int(q("SELECT COUNT(*) AS n FROM checks_issued WHERE loan_note_id = ?", (selected_lnid,))["n"][0])
                    if ln_check_count == 0:
                        if st.button("Delete loan note", icon=":material/delete_forever:", key="ln_delete_btn"):
                            confirm_delete_loan_note_dialog(selected_lnid, ln_row["note_ref"])
                    else:
                        if st.button("Cancel loan note", icon=":material/cancel:", key="ln_cancel_btn"):
                            active_ln_check_row = q(
                                "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'",
                                (selected_lnid,),
                            )
                            confirm_cancel_loan_note_dialog(
                                selected_lnid, ln_row["note_ref"],
                                int(active_ln_check_row["n"][0]), float(active_ln_check_row["total"][0]),
                            )

            if not ln_is_settled_or_defaulted:
                with st.expander("Edit loan note terms (Principal / Rate)"):
                    st.caption("Only Principal and Monthly Interest Rate can be changed here — the schedule's balances and this note's Total Interest/Payable recompute automatically. Existing installment rows keep their own already-entered Interest values; only future suggestions pick up a changed rate.")
                    edit_principal = st.number_input("Principal amount", min_value=0.0, step=1000.0, value=float(ln_row["principal_amount"] or 0.0), key="ln_edit_principal")
                    edit_rate = st.number_input("Monthly interest rate (%)", min_value=0.0, step=0.5, format="%.2f", value=float(ln_row["monthly_interest_rate"] or 0.0), key="ln_edit_rate")
                    if st.button("Save changes", icon=":material/save:", key="ln_edit_terms_btn"):
                        ok, msg = edit_loan_note_terms(selected_lnid, edit_principal, edit_rate)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh()

        tab_schedule, tab_ln_checks = st.tabs(
            ["Repayment Schedule", "Checks"], key="ln_subtab", default="Repayment Schedule", on_change="rerun",
        )

        with tab_schedule:
            installments_df = q(
                """SELECT installment_id, installment_no, due_date, principal, interest, outstanding_balance, status
                   FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date, installment_id""",
                (selected_lnid,),
            )
            if not installments_df.empty:
                installments_df["due_date"] = pd.to_datetime(installments_df["due_date"]).dt.date
            if ln_is_settled_or_defaulted:
                st.caption(f"This loan note is {ln_row['status']} — schedule is read-only.")
                st.dataframe(
                    installments_df,
                    hide_index=True,
                    column_config={
                        "principal": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                        "interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                        "outstanding_balance": st.column_config.NumberColumn("Outstanding Balance", format="₱ %.2f"),
                    },
                )
            else:
                st.caption("Add installments as real payment dates are committed to — Interest is a suggested value (rate × outstanding balance × days since the previous row ÷ 30) that you can freely override for lender-specific conventions.")
                current_balance = float(installments_df["outstanding_balance"].iloc[-1]) if not installments_df.empty else float(ln_row["principal_amount"] or 0.0)

                with st.expander("Add an installment", expanded=installments_df.empty):
                    add_due_date = st.date_input("Due date", value=date.today(), key="ln_add_due_date")
                    if st.button("Use remaining balance as principal (lump-sum payoff)", icon=":material/payments:", key="ln_lumpsum_btn"):
                        st.session_state["ln_add_principal"] = current_balance
                    add_principal = st.number_input("Principal", min_value=0.0, step=1000.0, key="ln_add_principal")
                    suggested_interest, balance_before = suggest_installment_interest(selected_lnid, add_due_date)
                    st.caption(f"Suggested interest: {php(suggested_interest)} ({(ln_row['monthly_interest_rate'] or 0):g}% × {php(balance_before)} balance × days elapsed ÷ 30) — editable below.")
                    add_interest = st.number_input("Interest", min_value=0.0, step=100.0, value=suggested_interest, key="ln_add_interest")
                    if st.button("Add installment", icon=":material/playlist_add:", key="ln_add_installment_btn") or dup_resume("add_loan_installment"):
                        if dup_needs_confirm("add_loan_installment", loan_installment_warnings(selected_lnid, [(add_due_date, add_principal, add_interest)])):
                            pass
                        elif guard_duplicate_submit("add_loan_installment", selected_lnid, add_due_date, add_principal, add_interest):
                            ok, msg = add_loan_installment(selected_lnid, add_due_date, add_principal, add_interest)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                del st.session_state["ln_add_principal"]
                                refresh()
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

                ln_import_staged_key = f"ln_import_staged_{selected_lnid}"
                ln_import_errors_key = f"ln_import_errors_{selected_lnid}"
                with st.expander("Import schedule from spreadsheet"):
                    st.caption("Upload a .csv or .xlsx with Date/Principal/Interest columns (any reasonable header variant works, including Outstanding Balance for a sanity check against what this system computes). Rows are staged for review below, added via the same rule-checked path as manually typing them in.")
                    st.download_button(
                        "Download CSV template", data=get_schedule_template_csv(), file_name="loan_schedule_import_template.csv",
                        mime="text/csv", icon=":material/download:", key=f"ln_import_template_btn_{selected_lnid}",
                    )
                    ln_import_file = st.file_uploader("Schedule spreadsheet", type=["csv", "xlsx"], key=f"ln_import_uploader_{selected_lnid}")
                    if st.button("Parse schedule file", icon=":material/upload_file:", key=f"ln_import_parse_btn_{selected_lnid}"):
                        if not ln_import_file:
                            st.warning("Upload a file first.")
                        else:
                            imported_rows, import_messages = parse_loan_schedule_spreadsheet(ln_import_file.getvalue(), ln_import_file.name)
                            staged = [{
                                "due_date": r.due_date, "principal": r.principal, "interest": r.interest,
                                "sheet_balance": r.outstanding_balance if r.outstanding_balance is not None else float("nan"),
                            } for r in imported_rows]
                            st.session_state[ln_import_staged_key] = staged
                            st.session_state[ln_import_errors_key] = import_messages

                    for m in st.session_state.get(ln_import_errors_key, []):
                        st.warning(m)

                    ln_staged_rows = st.session_state.get(ln_import_staged_key, [])
                    if ln_staged_rows:
                        st.caption(f"{len(ln_staged_rows)} row(s) staged — review/correct below, then add. 'Sheet Balance' (from an imported file, if it had one) is shown for comparison only; this system always computes its own.")
                        ln_edited_import_df = st.data_editor(
                            pd.DataFrame(ln_staged_rows),
                            hide_index=True,
                            num_rows="dynamic",
                            disabled=["sheet_balance"],
                            key=f"ln_import_staging_editor_{selected_lnid}",
                            column_config={
                                "due_date": st.column_config.DateColumn("Due Date"),
                                "principal": st.column_config.NumberColumn("Principal", format="₱ %.2f", min_value=0.0, step=0.01),
                                "interest": st.column_config.NumberColumn("Interest", format="₱ %.2f", min_value=0.0, step=0.01),
                                "sheet_balance": st.column_config.NumberColumn("Sheet Balance", format="₱ %.2f"),
                            },
                        )
                        ln_import_col_add, ln_import_col_discard = st.columns(2)
                        with ln_import_col_add:
                            if st.button("Add these installments", icon=":material/playlist_add:", key=f"ln_import_commit_btn_{selected_lnid}") or dup_resume("ln_import_commit"):
                              if dup_needs_confirm("ln_import_commit", loan_installment_warnings(selected_lnid, [
                                  (r["due_date"], float(r["principal"] or 0), float(r["interest"] or 0))
                                  for _, r in ln_edited_import_df.iterrows() if not pd.isna(r["due_date"])
                              ])):
                                pass
                              elif not guard_duplicate_submit("ln_import_commit", selected_lnid, ln_edited_import_df.to_csv(index=False)):
                                st.warning("Duplicate submission ignored — identical to the click just before it.")
                              else:
                                added, import_commit_errors = 0, []
                                for _, r in ln_edited_import_df.sort_values("due_date").iterrows():
                                    if pd.isna(r["due_date"]):
                                        continue
                                    ok, msg = add_loan_installment(selected_lnid, r["due_date"], float(r["principal"] or 0), float(r["interest"] or 0))
                                    if ok:
                                        added += 1
                                    else:
                                        import_commit_errors.append(f"{r['due_date']}: {msg}")
                                if added:
                                    st.success(f"{added} installment(s) added.")
                                for e in import_commit_errors:
                                    st.error(e)
                                st.session_state[ln_import_staged_key] = []
                                st.session_state[ln_import_errors_key] = []
                                if added:
                                    refresh()
                        with ln_import_col_discard:
                            if st.button("Discard parsed rows", icon=":material/delete_sweep:", key=f"ln_import_discard_btn_{selected_lnid}"):
                                st.session_state[ln_import_staged_key] = []
                                st.session_state[ln_import_errors_key] = []
                                st.rerun()

                if not installments_df.empty:
                    edited_installments_df = st.data_editor(
                        installments_df,
                        hide_index=True,
                        num_rows="dynamic",
                        disabled=["installment_id", "installment_no", "outstanding_balance", "status"],
                        key="ln_schedule_editor",
                        column_config={
                            "installment_id": st.column_config.NumberColumn("ID"),
                            "installment_no": st.column_config.NumberColumn("No."),
                            "due_date": st.column_config.DateColumn("Due Date"),
                            "principal": st.column_config.NumberColumn("Principal", format="₱ %.2f", min_value=0.0, step=0.01),
                            "interest": st.column_config.NumberColumn("Interest", format="₱ %.2f", min_value=0.0, step=0.01),
                            "outstanding_balance": st.column_config.NumberColumn("Outstanding Balance", format="₱ %.2f"),
                            "status": st.column_config.TextColumn("Status"),
                        },
                    )
                    if st.button("Save schedule changes", icon=":material/save:", key="ln_save_schedule_btn"):
                        original_by_id = installments_df.set_index("installment_id")
                        seen_ids, updated, deleted, sched_errors = set(), 0, 0, []
                        for _, r in edited_installments_df.iterrows():
                            if pd.isna(r["installment_id"]):
                                continue  # blank template row from the dynamic editor's add affordance
                            iid = int(r["installment_id"])
                            seen_ids.add(iid)
                            orig = original_by_id.loc[iid]
                            if (str(r["due_date"]) != str(orig["due_date"]) or abs(float(r["principal"]) - float(orig["principal"])) > 0.001
                                    or abs(float(r["interest"]) - float(orig["interest"])) > 0.001):
                                ok, msg = edit_loan_installment(iid, r["due_date"], float(r["principal"]), float(r["interest"]))
                                if ok:
                                    updated += 1
                                else:
                                    sched_errors.append(f"Installment {iid}: {msg}")
                        for removed_id in set(original_by_id.index) - seen_ids:
                            ok, msg = delete_loan_installment(int(removed_id))
                            if ok:
                                deleted += 1
                            else:
                                sched_errors.append(msg)
                        summary = [f"{n} {label}" for n, label in [(updated, "updated"), (deleted, "deleted")] if n]
                        if summary:
                            st.success("Schedule: " + ", ".join(summary) + ".")
                        for e in sched_errors:
                            st.error(e)
                        if summary:
                            refresh()

        with tab_ln_checks:
            pending_installments = q(
                "SELECT installment_id, installment_no, due_date, principal, interest FROM loan_installments WHERE loan_note_id = ? AND status = 'Pending' ORDER BY due_date",
                (selected_lnid,),
            )
            if ln_is_settled_or_defaulted:
                st.caption(f"This loan note is {ln_row['status']} — no further checks can be issued.")
            elif pending_installments.empty:
                st.caption("No pending installments to issue a check against — add one in the Repayment Schedule tab.")
            else:
                with st.expander("Issue PDC for an installment", expanded=True):
                    pending_pick = st.selectbox(
                        "Installment",
                        options=pending_installments["installment_id"],
                        format_func=lambda iid: (lambda r: f"#{r['installment_no']} — due {r['due_date']} — {php(r['principal'] + r['interest'])}")(pending_installments.set_index("installment_id").loc[iid]),
                        key="ln_pdc_installment_pick",
                    )
                    pending_row = pending_installments.set_index("installment_id").loc[int(pending_pick)]
                    pdc_amount = float(pending_row["principal"] + pending_row["interest"])
                    pdc_date = pd.to_datetime(pending_row["due_date"]).date()
                    st.caption(f"Check Date and Amount are taken directly from this installment's schedule row — {pdc_date} for {php(pdc_amount)}. To change either, edit the schedule row itself (in the Repayment Schedule tab) before issuing. No check number yet — that's assigned when the check is printed.")
                    if st.button("Issue Metrobank PDC", icon=":material/payments:", key="ln_pdc_issue_btn") or dup_resume("issue_pdc"):
                        if dup_needs_confirm("issue_pdc", check_warnings(ln_creditor_id, [(pdc_amount, pdc_date)])):
                            pass
                        elif guard_duplicate_submit("issue_pdc", int(pending_pick), pdc_amount, pdc_date):
                            ok, msg, check_id = issue_check(ln_creditor_id, pdc_amount, pdc_date, loan_note_id=selected_lnid, installment_id=int(pending_pick))
                            if ok:
                                st.success(msg)
                                refresh()
                            else:
                                st.error(msg)
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

            ln_mismatches = loan_check_mismatches(selected_lnid)
            if ln_mismatches:
                st.warning("Checks not matching the repayment schedule:" + chr(10) + chr(10) + chr(10).join(f"- {m}" for m in ln_mismatches))
            ln_unlinked = unlinked_loan_checks(selected_lnid) if not ln_is_settled_or_defaulted else []
            if ln_unlinked and ln_row["total_payable"] not in (None, ""):
                with st.expander(f"Create schedule from existing checks ({len(ln_unlinked)} checks not tied to the schedule)"):
                    st.caption("Gives each of these checks its own schedule row: due date = check date, principal = check amount, interest = 0 "
                               "(a check can't say how much of it is interest — edit a row later if you know). Nothing is saved if the checks add up to more than the loan's principal.")
                    st.dataframe(pd.DataFrame(ln_unlinked, columns=["check_id", "check_number", "check_date", "amount"]), hide_index=True,
                                 column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")})
                    st.caption(f"Total of these checks: {php(sum(float(c[3]) for c in ln_unlinked))} · Loan principal: {php(ln_row['principal_amount'])}")
                    if st.button("Create schedule rows from these checks", icon=":material/playlist_add_check:", key="ln_sched_from_checks_btn"):
                        def _do_sched(lnid=selected_lnid):
                            ok, msg = create_schedule_from_checks(lnid)
                            if ok:
                                st.toast(msg)
                            return ok, msg
                        confirm_create_dialog(f"Create a schedule row for each of these {len(ln_unlinked)} checks (interest 0)?", "ln_sched_from_checks", _do_sched)
            st.caption("Checks issued against this loan note")
            ln_checks_df = q(
                """SELECT chk.check_id, chk.check_number, chk.check_date, chk.amount, chk.status, datetime(chk.printed_at, 'localtime') AS printed_at,
                          li.installment_no
                   FROM checks_issued chk LEFT JOIN loan_installments li ON li.check_id = chk.check_id
                   WHERE chk.loan_note_id = ? ORDER BY chk.check_id DESC""",
                (selected_lnid,),
            )
            display_ln_checks = ln_checks_df.copy()
            if not display_ln_checks.empty:
                display_ln_checks["check_number"] = display_ln_checks["check_number"].fillna("Pending (not yet printed)")
            st.dataframe(display_ln_checks, hide_index=True, column_config={"amount": st.column_config.NumberColumn("Amount", format="₱ %.2f")})

            if PRINT_CHECKS_IN_APP:
                render_check_print_section(ln_checks_df, ln_row["creditor_name"], key_prefix=f"ln_check_print__{selected_lnid}")
            render_assign_number_section(ln_checks_df, key_prefix=f"ln_check_assign__{selected_lnid}")

            active_ln_checks = ln_checks_df[ln_checks_df["status"] == "Active"]
            parked_ln_checks = active_ln_checks[~active_ln_checks["check_number"].apply(has_value)] if not active_ln_checks.empty else active_ln_checks
            if not parked_ln_checks.empty:
                with st.expander("Edit parked checks (date & amount stay editable until numbered)"):
                    original_ln_parked = parked_ln_checks[["check_id", "check_date", "amount"]].copy()
                    original_ln_parked["check_date"] = pd.to_datetime(original_ln_parked["check_date"]).dt.date
                    edited_ln_parked = st.data_editor(
                        original_ln_parked, hide_index=True, num_rows="fixed", disabled=["check_id"], key="ln_edit_parked_editor",
                        column_config={
                            "check_id": st.column_config.NumberColumn("ID"),
                            "check_date": st.column_config.DateColumn("Check Date"),
                            "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                        },
                    )
                    if (st.button("Save parked check edits", icon=":material/save:", key="ln_save_parked_edits") or dup_resume("ln_save_parked_edits")) and not dup_needs_confirm(
                        "ln_save_parked_edits",
                        loan_check_mismatches(selected_lnid, {int(r["check_id"]): (float(r["amount"]), r["check_date"]) for _, r in edited_ln_parked.iterrows()
                         if str(r["check_date"]) != str(original_ln_parked.set_index("check_id").loc[int(r["check_id"]), "check_date"])
                         or abs(float(r["amount"]) - float(original_ln_parked.set_index("check_id").loc[int(r["check_id"]), "amount"])) > 0.001}),
                    ):
                        original_by_id = original_ln_parked.set_index("check_id")
                        updated, row_errors = 0, []
                        for _, r in edited_ln_parked.iterrows():
                            chk_id = int(r["check_id"])
                            orig = original_by_id.loc[chk_id]
                            if str(r["check_date"]) != str(orig["check_date"]) or abs(float(r["amount"]) - float(orig["amount"])) > 0.001:
                                ok, msg = edit_parked_check(chk_id, float(r["amount"]), r["check_date"])
                                if ok:
                                    updated += 1
                                else:
                                    row_errors.append(f"Check {chk_id}: {msg}")
                        if updated:
                            st.success(f"{updated} parked check(s) updated.")
                        for e in row_errors:
                            st.error(e)
                        if not updated and not row_errors:
                            st.info("No changes to save.")
                        if updated:
                            refresh()

            numbered_ln_checks = active_ln_checks[active_ln_checks["check_number"].apply(has_value)] if not active_ln_checks.empty else active_ln_checks
            if not ln_is_settled_or_defaulted and not numbered_ln_checks.empty:
                with st.expander("Correct a numbered check (number, date or amount)"):
                    st.caption("Edit Check Number, Check Date or Amount directly, then Save. Every change is logged automatically (old → new + timestamp). "
                               "Date and Amount can only be corrected once a check has been printed — an unprinted check is edited under 'Edit parked checks' instead.")
                    original_ln_corr = numbered_ln_checks[["check_id", "check_number", "check_date", "amount"]].copy()
                    original_ln_corr["check_date"] = pd.to_datetime(original_ln_corr["check_date"]).dt.date
                    edited_ln_corr = st.data_editor(
                        original_ln_corr, hide_index=True, num_rows="fixed", disabled=["check_id"], key="ln_correct_checks_editor",
                        column_config={
                            "check_id": st.column_config.NumberColumn("ID"),
                            "check_number": st.column_config.TextColumn("Check Number"),
                            "check_date": st.column_config.DateColumn("Check Date"),
                            "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.01, step=0.01),
                        },
                    )
                    corr_by_id = original_ln_corr.set_index("check_id")

                    def _ln_corr_changes():
                        out = {}
                        for _, r in edited_ln_corr.iterrows():
                            o = corr_by_id.loc[int(r["check_id"])]
                            if str(r["check_date"]) != str(o["check_date"]) or abs(float(r["amount"]) - float(o["amount"])) > 0.001:
                                out[int(r["check_id"])] = (float(r["amount"]), r["check_date"])
                        return out

                    if (st.button("Save check corrections", icon=":material/save:", key="ln_save_check_corrections") or dup_resume("ln_save_check_corrections")) \
                            and not dup_needs_confirm("ln_save_check_corrections", loan_check_mismatches(selected_lnid, _ln_corr_changes())):
                        updated, row_errors = 0, []
                        for _, r in edited_ln_corr.iterrows():
                            chk_id = int(r["check_id"])
                            orig = corr_by_id.loc[chk_id]
                            new_number = cell_text(r["check_number"]).strip()
                            for ctype, changed, value in (
                                ("Check Number", new_number != str(orig["check_number"]).strip(), new_number),
                                ("Check Date", str(r["check_date"]) != str(orig["check_date"]), str(r["check_date"])),
                                ("Amount", abs(float(r["amount"]) - float(orig["amount"])) > 0.001, str(float(r["amount"]))),
                            ):
                                if changed:
                                    ok, msg = apply_check_correction(chk_id, ctype, value, "")
                                    if ok:
                                        updated += 1
                                    else:
                                        row_errors.append(f"Check {chk_id}: {msg}")
                        if updated:
                            st.success(f"{updated} correction(s) saved.")
                        for e in row_errors:
                            st.error(e)
                        if not updated and not row_errors:
                            st.info("No changes to save.")
                        if updated:
                            refresh()

            if not ln_is_settled_or_defaulted and not active_ln_checks.empty:
                with st.expander("Cancel or replace a check"):
                    cr_pick = st.selectbox(
                        "Check",
                        options=active_ln_checks["check_id"],
                        format_func=lambda cid: active_ln_checks.set_index("check_id").loc[cid, "check_number"] or "Pending (not yet printed)",
                        key="ln_cr_pick",
                    )
                    cr_col_cancel, cr_col_replace = st.columns(2)
                    with cr_col_cancel:
                        if st.button("Cancel this check", icon=":material/cancel:", key="ln_check_cancel_btn"):
                            ok, msg = cancel_check(int(cr_pick))
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()
                    with cr_col_replace:
                        new_date = st.date_input("New check date", value=date.today(), key="ln_replace_date")
                        if st.button("Replace this check (new one parked)", icon=":material/sync_alt:", key="ln_replace_btn"):
                            ok, msg, *_ = replace_check(int(cr_pick), new_date, "")
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()

# --------------------------------------------------------------------------
# Tab 4: Bank loans (Track 3)
# --------------------------------------------------------------------------
with tab_bank_loans:
    st.header("Bank loans")
    st.caption("Add or edit lending bank records in the Creditor Master tab. Just Due Date and Amount per installment — no computation, with one optional addition: an interest portion, when you know it, to feed the interest-tracking dashboard. A due date is treated as Paid automatically once it's in the past (same convention as a check's cleared status elsewhere in this app), not a manual flag. Settlement is untracked cash: no check or bank debit is recorded.")

    if "_bl_mode_next" in st.session_state:
        st.session_state["bl_mode"] = st.session_state.pop("_bl_mode_next")

    bl_beneficiaries_df = q("SELECT * FROM loan_beneficiaries ORDER BY name")

    bank_loans_df = q(
        """SELECT bl.bank_loan_id, c.creditor_name, bl.loan_ref, bl.account_number, bl.start_date, bl.maturity_date,
                  bl.principal_amount,
                  CASE WHEN bl.is_cancelled = 1 THEN 0 ELSE COALESCE(
                      (SELECT SUM(amount) FROM bank_loan_installments
                       WHERE bank_loan_id = bl.bank_loan_id AND due_date > DATE('now', 'localtime')), 0
                  ) END AS outstanding,
                  bl.mode_of_payment, CASE WHEN bl.is_cancelled = 1 THEN 'Cancelled' ELSE bl.status END AS status, bl.is_cancelled, lb.name AS beneficiary, lb.beneficiary_type, bl.beneficiary_id,
                  """ + bank_loan_modified_sql("bl") + """ AS last_modified
           FROM bank_loans bl JOIN creditors c ON bl.creditor_id = c.creditor_id
           LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
           ORDER BY bl.bank_loan_id DESC"""
    )

    bl_mode = st.segmented_control(
        "What do you want to do?",
        options=["🔍 Browse Bank Loans", "➕ New Bank Loan"],
        default="🔍 Browse Bank Loans",
        key="bl_mode",
    )

    if bl_mode == "➕ New Bank Loan":
        st.subheader("Create bank loan")
        with st.expander("Loan beneficiary registry"):
            st.caption("Who actually uses/benefits from a loan the company pays for — a person or an affiliate. Reusable across multiple loans.")
            st.dataframe(bl_beneficiaries_df, hide_index=True)
            with st.form("new_loan_beneficiary_form", border=False):
                lb_name = st.text_input("Name")
                lb_type = st.selectbox("Type", ["Person", "Affiliate"])
                lb_contact = st.text_input("Contact number (optional)")
                if st.form_submit_button("Add beneficiary", icon=":material/person_add:"):
                    if lb_name:
                        if guard_duplicate_submit("add_loan_beneficiary", lb_name, lb_type, lb_contact):
                            add_loan_beneficiary(lb_name, lb_type, lb_contact)
                            st.success(f"Added {lb_type.lower()} {lb_name}.")
                            refresh()
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")
                    else:
                        st.error("Name is required.")

        with st.form("new_bank_loan_form", border=False):
            bl_creditor = selectbox_creditor("Bank", "BANK_LOAN", "bl_creditor")
            bl_ref = st.text_input("Loan reference")
            bl_account_number = st.text_input("Bank account number (optional)")
            if bl_beneficiaries_df.empty:
                st.caption("Add a beneficiary above first, or leave unattributed for now.")
                bl_beneficiary = None
            else:
                bl_beneficiary = st.selectbox(
                    "Attributable to (optional)", options=[None] + list(bl_beneficiaries_df["beneficiary_id"]),
                    format_func=lambda bid: "—" if bid is None else f"{bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'name']} ({bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'beneficiary_type']})",
                )
            c1, c2, c3 = st.columns(3)
            bl_start = c1.date_input("Start date", value=date.today())
            bl_maturity = c2.date_input("Maturity date", value=date.today())
            bl_repricing = c3.date_input("Repricing date (optional)", value=None)
            bl_principal = st.number_input("Principal amount (original loan amount)", min_value=0.0, step=10000.0)
            bl_mode_of_payment = st.text_input("Mode of payment (informational only, from the bank's document)", value="ADA")
            st.caption("Mode of payment is descriptive only — it doesn't drive any settlement logic. This system tracks payment as untracked cash by default regardless of what's entered here.")
            if st.form_submit_button("Create bank loan", icon=":material/account_balance:"):
                if bl_creditor and bl_ref:
                    err = money_amount_error(bl_principal, "Principal amount")
                    if not err and bl_maturity <= bl_start:
                        err = "Maturity date must be after the start date."
                    if err:
                        st.error(err)
                    else:
                        def _do_create_bank_loan(args=(bl_creditor, bl_ref, bl_account_number, bl_start, bl_maturity, bl_repricing, bl_principal, bl_mode_of_payment, bl_beneficiary)):
                            try:
                                new_blid = create_bank_loan(*args)
                            except sqlite3.IntegrityError:
                                return False, "This loan reference already exists."
                            except ValueError as e:
                                return False, str(e)
                            st.session_state["selected_bank_loan_id"] = new_blid
                            st.session_state["_bl_mode_next"] = "🔍 Browse Bank Loans"
                            return True, ""
                        confirm_create_dialog(
                            f"Create bank loan **{bl_ref}** with **{selectbox_creditor_label('BANK_LOAN', bl_creditor)}**: "
                            f"principal **{php(bl_principal)}**, {bl_start:%b %d, %Y} to {bl_maturity:%b %d, %Y}?"
                            + dup_note(bank_loan_warnings(bl_creditor, bl_account_number, bl_principal, bl_start)),
                            "create_bank_loan", _do_create_bank_loan,
                        )
                else:
                    st.error("Bank and loan reference are required.")
    else:
        st.subheader("Bank loans")
        if bank_loans_df.empty:
            st.caption("No bank loans yet — switch to \"New Bank Loan\" above to create the first one.")
        else:
            bl_mod_col, _bl_spacer = st.columns([1, 2])
            filtered_bank_loans_df = date_preset_filter(bl_mod_col, bank_loans_df, "last_modified", "Filter: last modified", "bl_filter_modified_preset").reset_index(drop=True)
            bl_browser_event = st.dataframe(
                filtered_bank_loans_df.drop(columns=["beneficiary_id", "is_cancelled"]),
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="bl_browser_df",
                column_config={
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                    "beneficiary": st.column_config.TextColumn("Attributable To"),
                    "beneficiary_type": st.column_config.TextColumn("Type"),
                },
            )
            bl_selected_rows = bl_browser_event.selection.rows if bl_browser_event and bl_browser_event.selection else []
            if bl_selected_rows and bl_selected_rows[0] < len(filtered_bank_loans_df):
                st.session_state["selected_bank_loan_id"] = int(filtered_bank_loans_df.iloc[bl_selected_rows[0]]["bank_loan_id"])

    selected_blid = st.session_state.get("selected_bank_loan_id") if not bank_loans_df.empty else None

    if selected_blid is not None and not bank_loans_df.empty and selected_blid in bank_loans_df["bank_loan_id"].values:
        bl_row = bank_loans_df.set_index("bank_loan_id").loc[selected_blid]
        st.divider()
        with st.container(border=True):
            st.subheader(f"{bl_row['loan_ref']} — {bl_row['creditor_name']}")
            bl_beneficiary_text = f"{bl_row['beneficiary']} ({bl_row['beneficiary_type']})" if pd.notna(bl_row["beneficiary_id"]) and bl_row["beneficiary_id"] != "" else "not set"
            st.caption(f"Status: {bl_row['status']} · Mode of payment (informational): {bl_row['mode_of_payment']} · Outstanding: {php(bl_row['outstanding'])} · Attributable to: {bl_beneficiary_text}")
            if not bl_beneficiaries_df.empty:
                with st.expander("Edit attribution (who this loan is for)"):
                    with st.form("edit_bl_beneficiary_form", border=False):
                        current_bid = None if pd.isna(bl_row["beneficiary_id"]) or bl_row["beneficiary_id"] == "" else int(bl_row["beneficiary_id"])
                        beneficiary_options = [None] + list(bl_beneficiaries_df["beneficiary_id"])
                        new_beneficiary = st.selectbox(
                            "Attributable to", options=beneficiary_options,
                            index=beneficiary_options.index(current_bid) if current_bid in beneficiary_options else 0,
                            format_func=lambda bid: "—" if bid is None else f"{bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'name']} ({bl_beneficiaries_df.set_index('beneficiary_id').loc[bid, 'beneficiary_type']})",
                        )
                        if st.form_submit_button("Save", icon=":material/save:"):
                            edit_bank_loan_beneficiary(selected_blid, new_beneficiary)
                            st.success("Updated.")
                            refresh()
            bl_installment_count = int(q("SELECT COUNT(*) AS n FROM bank_loan_installments WHERE bank_loan_id = ?", (selected_blid,))["n"][0])
            bl_col_export, bl_col_delete = st.columns(2)
            with bl_col_export:
                with st.expander("Export Bank Loan PDF"):
                    if st.button("Generate Bank Loan PDF", icon=":material/picture_as_pdf:", key="bl_export_btn"):
                        bl_pdf_bytes = generate_bank_loan_pdf(selected_blid)
                        if bl_pdf_bytes:
                            pdf_preview_and_download(bl_pdf_bytes, f"bank_loan_{bl_row['loan_ref']}.pdf".replace(" ", "_"), key="bl_pdf_download", label="Bank Loan PDF")
                        else:
                            st.error("Could not generate the Bank Loan PDF.")
            with bl_col_delete:
                if int(bl_row["is_cancelled"]):
                    st.caption("Cancelled — repayment rows are kept on record but not counted as due.")
                elif bl_installment_count > 0:
                    if st.button("Cancel this bank loan", icon=":material/cancel:", key="bl_cancel_btn"):
                        confirm_cancel_bank_loan_dialog(selected_blid, bl_row["loan_ref"], bl_installment_count)
                if not int(bl_row["is_cancelled"]) and bl_installment_count == 0:
                    if st.button("Delete this bank loan (no schedule rows entered yet)", icon=":material/delete:", key="bl_delete_btn"):
                        ok, msg = delete_bank_loan(selected_blid)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            st.session_state["selected_bank_loan_id"] = None
                            refresh()

        st.subheader("Repayment schedule")
        st.caption("Due Date and Amount, straight off the bank's document. Interest is optional — fill it in when you know it, straight off the same document, to feed the interest-tracking dashboard; leave it 0 otherwise. A row is treated as Paid automatically once its due date is in the past; add a row with the blank row at the bottom of the table.")

        bl_schedule_df = q(
            """SELECT installment_id, installment_no, due_date, amount, interest_portion
               FROM bank_loan_installments WHERE bank_loan_id = ? ORDER BY due_date, installment_id""",
            (selected_blid,),
        )
        if not bl_schedule_df.empty:
            bl_schedule_df["due_date"] = pd.to_datetime(bl_schedule_df["due_date"]).dt.date
            bl_schedule_df["status"] = bl_schedule_df["due_date"].apply(lambda d: "Paid" if d <= date.today() else "Pending")

        bl_import_staged_key = f"bl_import_staged_{selected_blid}"
        bl_import_errors_key = f"bl_import_errors_{selected_blid}"
        with st.expander("Import schedule from spreadsheet"):
            st.caption("Upload a .csv or .xlsx with Due Date/Amount columns (any reasonable header variant works). Rows are staged for review below — nothing is saved until you confirm.")
            st.download_button(
                "Download CSV template", data=get_bank_loan_template_csv(), file_name="bank_loan_schedule_import_template.csv",
                mime="text/csv", icon=":material/download:", key=f"bl_import_template_btn_{selected_blid}",
            )
            bl_import_file = st.file_uploader("Schedule spreadsheet", type=["csv", "xlsx"], key=f"bl_import_uploader_{selected_blid}")
            if st.button("Parse schedule file", icon=":material/upload_file:", key=f"bl_import_parse_btn_{selected_blid}"):
                if not bl_import_file:
                    st.warning("Upload a file first.")
                else:
                    bl_imported_rows, bl_import_messages = parse_bank_loan_spreadsheet(bl_import_file.getvalue(), bl_import_file.name)
                    st.session_state[bl_import_staged_key] = [{"due_date": r.due_date, "amount": r.amount} for r in bl_imported_rows]
                    st.session_state[bl_import_errors_key] = bl_import_messages

            for m in st.session_state.get(bl_import_errors_key, []):
                st.warning(m)

            bl_staged_rows = st.session_state.get(bl_import_staged_key, [])
            if bl_staged_rows:
                st.caption(f"{len(bl_staged_rows)} row(s) staged — review/correct below, then add.")
                bl_edited_import_df = st.data_editor(
                    pd.DataFrame(bl_staged_rows),
                    hide_index=True,
                    num_rows="dynamic",
                    key=f"bl_import_staging_editor_{selected_blid}",
                    column_config={
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", step=0.01),
                    },
                )
                bl_import_col_add, bl_import_col_discard = st.columns(2)
                with bl_import_col_add:
                    if st.button("Add these installments", icon=":material/playlist_add:", key=f"bl_import_commit_btn_{selected_blid}") or dup_resume("bl_import_commit"):
                      if dup_needs_confirm("bl_import_commit", bank_schedule_warnings(selected_blid, [
                          (r["due_date"], float(r["amount"] or 0)) for _, r in bl_edited_import_df.iterrows() if not pd.isna(r["due_date"])
                      ])):
                        pass
                      elif not guard_duplicate_submit("bl_import_commit", selected_blid, bl_edited_import_df.to_csv(index=False)):
                        st.warning("Duplicate submission ignored — identical to the click just before it.")
                      else:
                        bl_added, bl_import_commit_errors = 0, []
                        for _, r in bl_edited_import_df.sort_values("due_date").iterrows():
                            if pd.isna(r["due_date"]):
                                continue
                            ok, msg = add_bank_loan_installment(selected_blid, r["due_date"], float(r["amount"] or 0))
                            if ok:
                                bl_added += 1
                            else:
                                bl_import_commit_errors.append(f"{r['due_date']}: {msg}")
                        if bl_added:
                            st.success(f"{bl_added} installment(s) added.")
                        for e in bl_import_commit_errors:
                            st.error(e)
                        st.session_state[bl_import_staged_key] = []
                        st.session_state[bl_import_errors_key] = []
                        if bl_added:
                            refresh()
                with bl_import_col_discard:
                    if st.button("Discard parsed rows", icon=":material/delete_sweep:", key=f"bl_import_discard_btn_{selected_blid}"):
                        st.session_state[bl_import_staged_key] = []
                        st.session_state[bl_import_errors_key] = []
                        st.rerun()

        bl_editor_template = bl_schedule_df if not bl_schedule_df.empty else pd.DataFrame(columns=["installment_id", "installment_no", "due_date", "amount", "interest_portion", "status"])
        bl_edited_schedule_df = st.data_editor(
            bl_editor_template,
            hide_index=True,
            num_rows="dynamic",
            disabled=["installment_id", "installment_no", "status"],
            key="bl_schedule_editor",
            column_config={
                "installment_id": st.column_config.NumberColumn("ID"),
                "installment_no": st.column_config.NumberColumn("No."),
                "due_date": st.column_config.DateColumn("Due Date"),
                "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f", min_value=0.0, step=0.01),
                "interest_portion": st.column_config.NumberColumn("Interest (optional)", format="₱ %.2f", min_value=0.0, step=0.01, default=0.0),
                "status": st.column_config.TextColumn("Status (auto)"),
            },
        )
        if (st.button("Save schedule changes", icon=":material/save:", key="bl_save_schedule_btn") or dup_resume("bl_save_schedule")) and not dup_needs_confirm("bl_save_schedule", bank_schedule_warnings(selected_blid, [
            (r["due_date"], float(r["amount"] or 0)) for _, r in bl_edited_schedule_df.iterrows() if pd.isna(r["installment_id"]) and not pd.isna(r["due_date"])
        ])):
            bl_original_by_id = bl_schedule_df.set_index("installment_id") if not bl_schedule_df.empty else pd.DataFrame()
            bl_seen_ids, bl_added_n, bl_updated_n, bl_deleted_n, bl_sched_errors = set(), 0, 0, 0, []
            for _, r in bl_edited_schedule_df.iterrows():
                if pd.isna(r["due_date"]):
                    continue
                amount = float(r["amount"] or 0)
                interest_portion = float(r.get("interest_portion") or 0)

                if pd.isna(r["installment_id"]):
                    ok, msg = add_bank_loan_installment(selected_blid, r["due_date"], amount, interest_portion)
                    if ok:
                        bl_added_n += 1
                    else:
                        bl_sched_errors.append(msg)
                    continue

                iid = int(r["installment_id"])
                bl_seen_ids.add(iid)
                orig = bl_original_by_id.loc[iid]
                if (str(r["due_date"]) != str(orig["due_date"]) or abs(amount - float(orig["amount"])) > 0.001
                        or abs(interest_portion - float(orig.get("interest_portion") or 0)) > 0.001):
                    ok, msg = edit_bank_loan_installment(iid, r["due_date"], amount, interest_portion)
                    if ok:
                        bl_updated_n += 1
                    else:
                        bl_sched_errors.append(f"Installment {iid}: {msg}")

            if not bl_original_by_id.empty:
                for removed_id in set(bl_original_by_id.index) - bl_seen_ids:
                    ok, msg = delete_bank_loan_installment(int(removed_id))
                    if ok:
                        bl_deleted_n += 1
                    else:
                        bl_sched_errors.append(f"Installment {removed_id}: {msg}")

            if bl_added_n or bl_updated_n or bl_deleted_n:
                st.success(f"Saved — {bl_added_n} added, {bl_updated_n} updated, {bl_deleted_n} deleted.")
            for e in bl_sched_errors:
                st.error(e)
            if bl_added_n or bl_updated_n or bl_deleted_n:
                refresh()

    with st.expander("Bank loan servicing report"):
        st.dataframe(get_bank_loan_servicing_report(), hide_index=True)

# --------------------------------------------------------------------------
# Tab 5: Credit cards (Track 4)
# --------------------------------------------------------------------------
with tab_credit_cards:
    st.header("Credit cards")
    st.caption("Scoped to what matters: due dates, amounts due, whose card it is, making sure every statement gets recorded, and the annual fee waiver reminder.")

    cardholders_df = q("SELECT * FROM cardholders ORDER BY name")
    cardholders_df["is_active"] = pd.to_numeric(cardholders_df["is_active"], errors="coerce").fillna(1).astype(int)
    active_cardholders_df = cardholders_df[cardholders_df["is_active"] == 1]
    cards_df = q(
        """SELECT cc.card_id, cc.creditor_id, c.creditor_name AS issuer, ch.name AS cardholder, cc.card_name, cc.card_number_last4, cc.credit_limit,
                  cc.annual_fee_due_month, cc.statement_day, cc.cardholder_id, cc.is_active
           FROM credit_cards cc JOIN creditors c ON cc.creditor_id = c.creditor_id
           LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
           ORDER BY LOWER(c.creditor_name), LOWER(COALESCE(ch.name, '')), LOWER(cc.card_name), cc.card_number_last4"""
    )
    # q() coerces every NULL to '' app-wide, which mixes '' (str) with real numbers (float) in a
    # nullable numeric column — pyarrow can't serialize that mix for st.dataframe once a real
    # value exists alongside a blank one. Normalize back to a clean numeric dtype for display.
    # credit_limit joined the nullable list in migration 034 (Phase 8a: legacy card import).
    cards_df["annual_fee_due_month"] = pd.to_numeric(cards_df["annual_fee_due_month"], errors="coerce")
    cards_df["statement_day"] = pd.to_numeric(cards_df["statement_day"], errors="coerce")
    cards_df["credit_limit"] = pd.to_numeric(cards_df["credit_limit"], errors="coerce")
    cards_df["is_active"] = pd.to_numeric(cards_df["is_active"], errors="coerce").fillna(1).astype(int)
    active_cards_df = cards_df[cards_df["is_active"] == 1]

    def _card_label(cid: int, df: pd.DataFrame = None) -> str:
        d = (df if df is not None else cards_df).set_index("card_id").loc[cid]
        suffix = "" if d.get("is_active", 1) else " — inactive"
        return f"{d['card_name']} - {d['card_number_last4']} ({d['issuer']}){suffix}"

    def _blank_to_none_int(v):
        # q() coerces every NULL to '' app-wide (engines/db_engine.py df_query) — nullable
        # numeric fields read back through it need this before int() or pd.notna() checks.
        return None if v in (None, "") or (isinstance(v, float) and pd.isna(v)) else int(v)

    # ----------------------------------------------------------------------
    # Needs attention — surfaced first, like the Dashboard, instead of buried
    # between setup forms and data tables further down.
    # ----------------------------------------------------------------------
    gap_alerts_df = get_statement_gap_alerts()
    fee_alerts_df = get_annual_fee_alerts()
    # "No statement on file yet" (a card with zero statement history, typically not in active use)
    # is routine and can be a large pile after a legacy import -- split it from "Possibly missing a
    # statement" (a card that HAD statements but seems to have skipped a cycle), which is the one
    # worth surfacing as a real warning. Otherwise 40+ routine rows bury the rare actionable one.
    if gap_alerts_df.empty:
        never_had_df = missing_recent_df = gap_alerts_df
    else:
        never_had_df = gap_alerts_df[gap_alerts_df["alert"] == "No statement on file yet"]
        missing_recent_df = gap_alerts_df[gap_alerts_df["alert"] == "Possibly missing a statement"]

    if missing_recent_df.empty and fee_alerts_df.empty and never_had_df.empty:
        st.caption("Nothing needs attention — every card has a recent statement on file, and no annual fee months are coming up.")
    else:
        if not missing_recent_df.empty or not fee_alerts_df.empty:
            with st.container(border=True):
                st.markdown("**Needs attention**")
                for _, r in missing_recent_df.iterrows():
                    # Raw truthiness on last_due_date used to silently work by accident when every
                    # card had zero statements (a uniformly-NULL column, read back as an empty
                    # string that's already falsy) -- surfaced as a literal "(last due date on file:
                    # nan)" the moment the column became a real mix of dates and missing values
                    # (2026-09-17, once the first real cc_statements rows existed): a NaN float is
                    # truthy in Python, unlike None or ''. pd.notna() is the correct check either way.
                    st.warning(f"{r['card_name']} ({r['card_number_last4']}) — {r['cardholder_name'] or 'no cardholder set'}: {r['alert']}" + (f" (last due date on file: {r['last_due_date']})" if pd.notna(r['last_due_date']) else ""))
                if not fee_alerts_df.empty:
                    st.caption("Annual fee month coming up — month only, since the exact day is the card's issuance date and isn't on file. Request the waiver before it posts.")
                    st.dataframe(fee_alerts_df[["card_name", "card_number_last4", "cardholder_name", "when"]], hide_index=True)
        if not never_had_df.empty:
            with st.expander(f"{len(never_had_df)} card(s) have never had a statement recorded"):
                st.caption("Routine for a card not in active use yet, or one just brought over from the legacy system. Add its first statement in the Statements tab below once a bill comes in.")
                st.dataframe(
                    never_had_df[["card_name", "card_number_last4", "cardholder_name"]].rename(
                        columns={"card_name": "Card", "card_number_last4": "Last 4", "cardholder_name": "Cardholder"}
                    ),
                    hide_index=True,
                )

    sub_statements, sub_cards, sub_check = st.tabs([
        ":material/receipt_long: Statements", ":material/credit_card: Cards & cardholders", ":material/fact_check: Missing statement check",
    ])

    # ----------------------------------------------------------------------
    with sub_statements:
        st.caption("Whose card, how much, and when it's due. A statement with a real unpaid balance stays Unpaid/Partially Paid even past its due date — only a statement with nothing left owing (or one without rolling-balance tracking at all) auto-shows as Paid once its due date passes.")
        cc_statements_df = q(
            f"""SELECT cs.cc_statement_id, cs.card_id, COALESCE(cs.statement_month, SUBSTR(cs.due_date, 1, 7)) AS statement_month, cc.card_name, cc.card_number_last4, ch.name AS cardholder, cs.due_date, cs.total_amount_due,
                      cs.previous_balance, cs.new_charges, cs.interest_charged, cs.amount_paid, cs.unpaid_balance_next,
                      cs.status AS recorded_status,
                      CASE WHEN cs.paid_confirmed = 0 AND {superseded_sql('cs')} THEN 'Replaced by newer statement'
                           WHEN cs.paid_confirmed = 0 THEN 'Not yet confirmed' ELSE 'Confirmed' END AS payment_check,
                      CASE WHEN cs.status IN ('Paid', 'Cancelled') THEN cs.status
                           WHEN cs.unpaid_balance_next > 0 AND cs.amount_paid > 0.005 THEN 'Partially Paid'
                           WHEN cs.unpaid_balance_next > 0 THEN 'Unpaid'
                           WHEN cs.due_date < DATE('now', 'localtime') THEN 'Paid'
                           ELSE cs.status END AS effective_status,
                      cs.payment_method, {cc_owed_case('cs')} AS still_owed
               FROM cc_statements cs JOIN credit_cards cc ON cs.card_id = cc.card_id
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               ORDER BY cs.due_date ASC"""
        )
        sf1, sf2 = st.columns([1, 3])
        stmt_outstanding_only = sf1.toggle("Outstanding only", value=True, key="stmt_outstanding_only",
                                           help="Show only statements that still count as owed (the same rule as the payable totals). Turn off to see every statement.")
        stmt_search = sf2.text_input("Search statements", placeholder="Type a card, cardholder, bank, last 4 digits, month (2026-09) or status — several words narrow it down",
                                     key="stmt_search", label_visibility="collapsed")

        def filter_statements(df: pd.DataFrame) -> pd.DataFrame:
            """The 'Outstanding only' toggle + the search words (every word must appear somewhere in the row)."""
            out = df[df["still_owed"].astype(float) > 0.005] if stmt_outstanding_only else df
            words = (stmt_search or "").lower().split()
            if words and not out.empty:
                blob = out[["card_name", "card_number_last4", "cardholder", "statement_month", "due_date", "effective_status", "payment_method"]].astype(str).agg(" ".join, axis=1).str.lower()
                out = out[blob.apply(lambda s: all(w in s for w in words))]
            return out

        shown_statements_df = filter_statements(cc_statements_df)
        st.caption(f"Showing {len(shown_statements_df)} of {len(cc_statements_df)} statements.")
        st.dataframe(
            shown_statements_df.drop(columns=["card_id"]),
            hide_index=True,
            column_config={
                "still_owed": st.column_config.NumberColumn("Still owed", format="₱ %.2f"),
                "total_amount_due": st.column_config.NumberColumn("Amount due", format="₱ %.2f"),
                "previous_balance": st.column_config.NumberColumn("Previous balance", format="₱ %.2f"),
                "new_charges": st.column_config.NumberColumn("New charges", format="₱ %.2f"),
                "interest_charged": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                "amount_paid": st.column_config.NumberColumn("Paid", format="₱ %.2f"),
                "unpaid_balance_next": st.column_config.NumberColumn("Unpaid → next", format="₱ %.2f"),
            },
        )

        col_new_stmt = st.container()
        with col_new_stmt:
            with st.container(border=True):
                st.markdown("**Add statement**")
                st.caption("Pick whose card first — one cardholder can have several cards.")
                if active_cardholders_df.empty:
                    st.caption("Add a cardholder first, in Cards & cardholders.")
                    stmt_holder_cards_df = pd.DataFrame(columns=cards_df.columns)
                else:
                    stmt_holder = st.selectbox(
                        "Cardholder", options=active_cardholders_df["cardholder_id"],
                        format_func=lambda hid: active_cardholders_df.set_index("cardholder_id").loc[hid, "name"],
                        key="stmt_holder_pick",
                    )
                    stmt_holder_cards_df = active_cards_df[active_cards_df["cardholder_id"] == stmt_holder]

                if stmt_holder_cards_df.empty:
                    st.caption("No cards assigned to this cardholder yet — assign one via \"Edit a card\" in Cards & cardholders.")
                else:
                    stmt_issuer_names = sorted(stmt_holder_cards_df["issuer"].unique(), key=str.lower)
                    stmt_issuer = st.selectbox("Issuer (bank)", options=stmt_issuer_names, key=f"stmt_issuer_pick_{stmt_holder}")
                    stmt_issuer_cards_df = stmt_holder_cards_df[stmt_holder_cards_df["issuer"] == stmt_issuer].sort_values(
                        ["card_name", "card_number_last4"], key=lambda col: col.astype(str).str.lower()
                    )
                    stmt_card = st.selectbox(
                        "Card", options=stmt_issuer_cards_df["card_id"],
                        format_func=lambda cid: _card_label(cid, stmt_issuer_cards_df),
                        key=f"stmt_card_pick_{stmt_holder}_{stmt_issuer}",
                    )
                    default_year, default_month = get_default_statement_month(int(stmt_card))
                    sm_key = f"{int(stmt_card)}_{default_year}_{default_month}"
                    sm1, sm2 = st.columns([3, 2])
                    stmt_month_num = sm1.selectbox(
                        "Statement month", options=list(range(1, 13)), index=default_month - 1,
                        format_func=lambda m: date(2000, m, 1).strftime("%B"), key=f"stmt_month_{sm_key}",
                        help="Which month this statement is for. Pre-selected to the month after this card's last recorded statement; after December it rolls to January of the next year on its own.",
                    )
                    stmt_year_num = sm2.number_input("Year", min_value=2000, max_value=2100, step=1, value=default_year, key=f"stmt_year_{sm_key}")
                    stmt_due = st.date_input("Due date", value=date.today(), key="stmt_due_date")

                    if st.session_state.pop("_clear_stmt_form", False):
                        for _k in [k for k in st.session_state if str(k).startswith(("stmt_total_direct", "stmt_finance_direct", "stmt_payments_"))]:
                            st.session_state.pop(_k, None)

                    # The statement's total already includes everything unpaid before it, so this is all you type:
                    # what is due, what was paid since the last statement (optional), and any finance charge.
                    stmt_last = q_live(
                        """SELECT cc_statement_id AS id, due_date, total_amount_due AS total, paid_confirmed,
                                  """ + cc_owed_case("cs") + """ AS owed
                           FROM cc_statements cs WHERE card_id = ? AND status != 'Cancelled' ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1""",
                        (int(stmt_card),),
                    )
                    stmt_earlier = stmt_last.iloc[0].to_dict() if not stmt_last.empty and str(stmt_last.iloc[0]["due_date"]) < str(stmt_due) else None
                    stmt_total_direct = st.number_input(
                        "Total amount due (from the statement)", min_value=0.0, step=100.0, key="stmt_total_direct",
                        help="The amount the statement asks you to pay. It already includes any balance carried over, so it is counted once.",
                    )
                    stmt_payments = None
                    if stmt_earlier:
                        stmt_payments = st.number_input(
                            f"Payments received since the earlier statement (optional) — earlier total on file {php(stmt_earlier['total'])}",
                            min_value=0.0, step=100.0, value=None, key=f"stmt_payments_{stmt_earlier['id']}",
                            help="Add up the payment lines on this statement (the ones ending in C). Type 0 if nothing was paid. Leave empty if you don't know — the statement is still recorded and counted once.",
                        )
                    stmt_interest = st.number_input(
                        "Finance charge / interest on this statement (optional)", min_value=0.0, step=10.0, key="stmt_finance_direct",
                        help="The interest / finance charge the statement lists, if any. It is part of the total, and feeds the cost-of-financing figures.",
                    )
                    stmt_total_due = stmt_total_direct
                    stmt_math_ok = True
                    if stmt_earlier and stmt_payments is not None:
                        stmt_carried_calc = max(float(stmt_earlier["total"]) - float(stmt_payments), 0.0)
                        stmt_new_calc = round(stmt_total_direct - stmt_carried_calc - stmt_interest, 2)
                        if stmt_new_calc < -1.0:
                            stmt_math_ok = False
                            st.error(f"These don't add up: earlier statement {php(stmt_earlier['total'])} − payments {php(stmt_payments)} + interest {php(stmt_interest)} is already "
                                     f"{php(stmt_carried_calc + stmt_interest)}, more than the total due {php(stmt_total_direct)}. Check the payments, or the earlier statement's amount on file.")
                            stmt_previous, stmt_new_charges = stmt_carried_calc, 0.0
                        else:
                            if stmt_new_calc < 0:      # a few centavos of rounding on the bank's side
                                stmt_carried_calc, stmt_new_calc = max(stmt_total_direct - stmt_interest, 0.0), 0.0
                            stmt_previous, stmt_new_charges = round(stmt_carried_calc, 2), stmt_new_calc
                            st.caption(f"Earlier {php(stmt_earlier['total'])} − payments {php(stmt_payments)} = {php(stmt_previous)} carried over · "
                                       f"+ new charges {php(stmt_new_charges)} (worked out) + interest {php(stmt_interest)} = **{php(stmt_total_due)}**")
                    else:
                        stmt_previous, stmt_new_charges = 0.0, max(stmt_total_direct - stmt_interest, 0.0)

                    if stmt_total_direct > 0:
                        if stmt_earlier and float(stmt_earlier["owed"]) > 0.005:
                            st.info(f"Payables for this card after saving: **{php(stmt_total_due)}** — it replaces the earlier {php(stmt_earlier['owed'])} that is counted now, so nothing is counted twice.")
                        else:
                            st.info(f"Payables for this card after saving: **{php(stmt_total_due)}**.")

                    if st.button("Add statement", icon=":material/receipt_long:", key="add_cc_statement_btn", disabled=not stmt_math_ok) or dup_resume("add_cc_statement"):
                        stmt_month_text = f"{int(stmt_year_num)}-{int(stmt_month_num):02d}"
                        if dup_needs_confirm("add_cc_statement", cc_statement_warnings(int(stmt_card), stmt_due, stmt_total_due)
                                             + cc_statement_month_warnings(int(stmt_card), stmt_month_text, stmt_total_due)):
                            pass
                        elif guard_duplicate_submit("add_cc_statement", stmt_card, stmt_due, stmt_total_due, stmt_interest, stmt_payments):
                            add_cc_statement(int(stmt_card), stmt_due, stmt_previous, stmt_new_charges, stmt_interest, 0.0,
                                             statement_month=stmt_month_text, paid_confirmed=0)
                            if stmt_earlier and stmt_payments is not None and int(stmt_earlier["paid_confirmed"]) == 0:
                                earlier_paid, earlier_status = confirm_cc_payment_from_next_statement(int(stmt_earlier["id"]), stmt_previous)
                                st.toast(f"Earlier statement confirmed: {php(earlier_paid)} paid ({earlier_status}).")
                            st.session_state["_clear_stmt_form"] = True
                            st.success("Statement recorded.")
                            refresh()
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")

        with st.expander("Edit / delete a statement"):
            st.caption("Only the most recent statement for a card can be changed or removed here — an older one already had its balance carried forward into a later statement, so changing it would leave that later one out of sync.")
            if cc_statements_df.empty:
                st.caption("No statements recorded yet.")
            else:
                latest_stmts_df = (
                    cc_statements_df.sort_values(["due_date", "cc_statement_id"])
                    .groupby("card_id", as_index=False).tail(1)
                )
                latest_stmts_df = filter_statements(latest_stmts_df)
                st.caption("Follows the 'Outstanding only' switch and the search box above the table. You can also type in the box below to jump to a statement.")
            if not cc_statements_df.empty and latest_stmts_df.empty:
                st.caption("No statement matches — turn off 'Outstanding only' or clear the search above the table.")
            elif not cc_statements_df.empty:
                edit_stmt_id = st.selectbox(
                    "Statement", options=latest_stmts_df["cc_statement_id"],
                    format_func=lambda sid: (
                        f"{latest_stmts_df.set_index('cc_statement_id').loc[sid, 'card_name']} - "
                        f"{latest_stmts_df.set_index('cc_statement_id').loc[sid, 'card_number_last4']} — "
                        f"due {latest_stmts_df.set_index('cc_statement_id').loc[sid, 'due_date']} "
                        f"({php(latest_stmts_df.set_index('cc_statement_id').loc[sid, 'total_amount_due'])})"
                    ),
                    key="edit_stmt_pick",
                )
                cur_stmt = latest_stmts_df.set_index("cc_statement_id").loc[edit_stmt_id]
                stored_prev, stored_new, stored_int = float(cur_stmt["previous_balance"]), float(cur_stmt["new_charges"]), float(cur_stmt["interest_charged"])
                stored_total = float(cur_stmt["total_amount_due"])
                # Statements from the 2026-09-17 legacy CC import were inserted as a lump
                # total_amount_due with previous_balance/new_charges/interest_charged left at their
                # 0 default (migration 027's rolling-balance breakdown is "going-forward only, no
                # backfill" -- see add_cc_statement()). Prefilling "New charges" with 0 here would
                # silently recompute total_amount_due as 0 the moment this got saved, even from
                # just fixing the due date. Folding the whole stored total into "New charges" by
                # default keeps the recomputed total matching what was already on file.
                is_legacy_lump_sum = round(stored_prev + stored_new + stored_int, 2) != round(stored_total, 2)
                if is_legacy_lump_sum:
                    st.info(f"This statement was imported as a lump total ({php(stored_total)}) with no previous balance / new charges / interest breakdown on file. That's folded into \"New charges\" below so saving won't change the total unless you edit it.")
                    default_new = round(stored_total - stored_prev - stored_int, 2)
                else:
                    default_new = stored_new
                edit_stmt_due = st.date_input("Due date", value=date.fromisoformat(cur_stmt["due_date"]), key=f"edit_stmt_due_{edit_stmt_id}")
                es1, es2, es3 = st.columns(3)
                with es1:
                    edit_stmt_prev = st.number_input("Previous balance", min_value=0.0, step=100.0, value=stored_prev, key=f"edit_stmt_prev_{edit_stmt_id}")
                with es2:
                    edit_stmt_new = st.number_input("New charges", min_value=0.0, step=100.0, value=default_new, key=f"edit_stmt_new_{edit_stmt_id}")
                with es3:
                    edit_stmt_int = st.number_input("Interest charged", min_value=0.0, step=10.0, value=stored_int, key=f"edit_stmt_int_{edit_stmt_id}")
                edit_stmt_paid = st.number_input("Amount paid", min_value=0.0, step=100.0, value=float(cur_stmt["amount_paid"]), key=f"edit_stmt_paid_{edit_stmt_id}")
                st.metric(
                    "Total amount due (computed)", php(round(edit_stmt_prev + edit_stmt_new + edit_stmt_int, 2)),
                    help="This is what Save will actually record — check it matches the real statement total before saving.",
                )

                ec1, ec2 = st.columns(2)
                with ec1:
                    if st.button("Save statement changes", icon=":material/save:", key=f"save_stmt_{edit_stmt_id}"):
                        ok, msg = edit_cc_statement(int(edit_stmt_id), edit_stmt_due, edit_stmt_prev, edit_stmt_new, edit_stmt_int, edit_stmt_paid)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh()
                with ec2:
                    confirm_delete_stmt = st.checkbox("Yes, delete this statement", key=f"confirm_delete_stmt_{edit_stmt_id}")
                    if st.button("Delete this statement", icon=":material/delete:", key=f"delete_stmt_{edit_stmt_id}", disabled=not confirm_delete_stmt):
                        ok, msg = delete_cc_statement(int(edit_stmt_id))
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh()

    # ----------------------------------------------------------------------
    with sub_cards:
        st.subheader("Cards on file")
        st.dataframe(
            cards_df.drop(columns=["cardholder_id", "creditor_id"]),
            hide_index=True,
            column_config={
                "credit_limit": st.column_config.NumberColumn("Credit limit", format="₱ %.2f"),
                "annual_fee_due_month": st.column_config.NumberColumn("Annual fee month"),
                "statement_day": st.column_config.NumberColumn("Statement day"),
                "is_active": st.column_config.CheckboxColumn("Active"),
            },
        )

        if not cards_df.empty:
            with st.expander("Edit a card (name / issuer / cardholder / credit limit / annual fee month / statement day)"):
                # Pick the cardholder first, then narrow "Card" to just their cards -- faster than
                # scanning every card in the system when you already know whose card you want.
                UNASSIGNED_HOLDER = -1
                edit_card_holder_norm = cards_df["cardholder_id"].apply(_blank_to_none_int)
                holder_filter_ids = sorted(
                    {hid for hid in edit_card_holder_norm if hid is not None},
                    key=lambda hid: str(cardholders_df.set_index("cardholder_id").loc[hid, "name"]).lower(),
                )
                has_unassigned_cards = any(hid is None for hid in edit_card_holder_norm)
                holder_filter_options = [None] + holder_filter_ids + ([UNASSIGNED_HOLDER] if has_unassigned_cards else [])

                def _holder_filter_label(hid):
                    if hid is None:
                        return "All cardholders"
                    if hid == UNASSIGNED_HOLDER:
                        return "(no cardholder)"
                    row = cardholders_df.set_index("cardholder_id").loc[hid]
                    return row["name"] + ("" if row["is_active"] else " — inactive")

                edit_card_holder_filter = st.selectbox(
                    "Cardholder", options=holder_filter_options, format_func=_holder_filter_label, key="edit_card_holder_filter",
                )
                if edit_card_holder_filter is None:
                    cards_for_pick_df = cards_df
                elif edit_card_holder_filter == UNASSIGNED_HOLDER:
                    cards_for_pick_df = cards_df[edit_card_holder_norm.isnull()]
                else:
                    cards_for_pick_df = cards_df[edit_card_holder_norm == edit_card_holder_filter]

                if cards_for_pick_df.empty:
                    st.caption("No cards for this cardholder.")
                else:
                    edit_card_id = st.selectbox(
                        "Card", options=cards_for_pick_df["card_id"],
                        format_func=lambda cid: _card_label(cid, cards_for_pick_df),
                        # Keyed by the cardholder filter + card_id so switching either forces fresh
                        # widgets instead of showing a previous card's stale values after Save
                        # (silent wrong-card overwrite risk).
                        key=f"edit_card_pick_{edit_card_holder_filter}",
                    )
                    current = cards_df.set_index("card_id").loc[edit_card_id]
                    with st.form("edit_card_form", border=False):
                        edit_card_name = st.text_input("Card name", value=str(current["card_name"]), key=f"edit_card_name_{edit_card_id}")
                        issuer_names = sorted(set(creditor_options("CREDIT_CARD").values()) | {str(current["issuer"])}, key=str.lower)
                        edit_issuer = st.selectbox(
                            "Card issuer (bank)", options=issuer_names, index=issuer_names.index(str(current["issuer"])),
                            accept_new_options=True, key=f"edit_card_issuer_{edit_card_id}",
                            help="Pick the bank, or type a new name to add it.",
                        )
                        current_holder = _blank_to_none_int(current["cardholder_id"])
                        # Active cardholders only, like "Add a new card" below -- but always keep the
                        # card's currently assigned cardholder pickable (labeled inactive) even if
                        # they've since been deactivated, so reopening this form doesn't silently drop
                        # the assignment (same pattern as edit_issuer's current-value union above).
                        holder_pick_ids = list(active_cardholders_df["cardholder_id"])
                        if current_holder is not None and current_holder not in holder_pick_ids:
                            holder_pick_ids.append(current_holder)
                        holder_options = [None] + holder_pick_ids
                        edit_holder = st.selectbox(
                            "Cardholder", options=holder_options,
                            index=holder_options.index(current_holder) if current_holder in holder_options else 0,
                            format_func=lambda hid: "—" if hid is None else (
                                cardholders_df.set_index("cardholder_id").loc[hid, "name"]
                                + ("" if cardholders_df.set_index("cardholder_id").loc[hid, "is_active"] else " — inactive")
                            ),
                            key=f"edit_card_holder_{edit_card_id}",
                        )
                        edit_limit = st.number_input(
                            "Credit limit", min_value=0.0, step=10000.0,
                            value=float(current["credit_limit"]) if pd.notna(current["credit_limit"]) else 0.0,
                            help="0 shows here for a card imported with no credit limit on file — enter the real limit to save it.",
                            key=f"edit_card_limit_{edit_card_id}",
                        )
                        current_month = _blank_to_none_int(current["annual_fee_due_month"])
                        month_options = [None] + list(range(1, 13))
                        edit_af_month = st.selectbox(
                            "Annual fee due — month", options=month_options,
                            index=month_options.index(current_month) if current_month in month_options else 0,
                            format_func=lambda m: "—" if m is None else date(2000, m, 1).strftime("%B"),
                            key=f"edit_card_af_month_{edit_card_id}",
                        )
                        edit_statement_day = st.number_input(
                            "Statement day", min_value=0, max_value=31, step=1,
                            value=_blank_to_none_int(current["statement_day"]) or 0, help="0 = not set",
                            key=f"edit_card_stmt_day_{edit_card_id}",
                        )
                        if st.form_submit_button("Save card changes", icon=":material/save:"):
                            ok, msg = edit_credit_card(int(edit_card_id), edit_card_name, get_or_create_card_issuer(edit_issuer), edit_holder, edit_limit, edit_af_month, edit_statement_day or None)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()

                    if int(current["is_active"]):
                        if st.button("Deactivate this card", icon=":material/block:", key=f"deactivate_card_{edit_card_id}"):
                            deactivate_credit_card(int(edit_card_id))
                            st.success("Card deactivated — its statement history stays intact, but it won't appear when adding a new statement.")
                            refresh()
                    else:
                        st.caption("This card is currently inactive.")
                        if st.button("Reactivate this card", icon=":material/refresh:", key=f"reactivate_card_{edit_card_id}"):
                            reactivate_credit_card(int(edit_card_id))
                            st.success("Card reactivated.")
                            refresh()

        with st.expander("Add a new card"):
            with st.form("new_cc_card_form", border=False, clear_on_submit=True):
                st.markdown("**Add card**")
                cc_issuer_names = sorted(set(creditor_options("CREDIT_CARD").values()), key=str.lower)
                cc_issuer = st.selectbox(
                    "Card issuer (bank)", options=cc_issuer_names, index=None, accept_new_options=True, key="cc_issuer_name",
                    placeholder="Pick the bank, or type a new one", help="Type a name that isn't listed to add a new bank.",
                )
                if active_cardholders_df.empty:
                    st.caption("Add a cardholder below first.")
                    cc_holder = None
                else:
                    cc_holder = st.selectbox("Cardholder", options=active_cardholders_df["cardholder_id"], format_func=lambda hid: active_cardholders_df.set_index("cardholder_id").loc[hid, "name"])
                cc_name = st.text_input("Card name")
                cc_last4 = st.text_input("Last 4 digits", max_chars=4)
                cc_limit = st.number_input("Credit limit", min_value=0.0, step=10000.0)
                cc_af_month = st.selectbox(
                    "Annual fee due — month (optional)", options=[None] + list(range(1, 13)),
                    format_func=lambda m: "—" if m is None else date(2000, m, 1).strftime("%B"),
                    help="Month only — the exact day is the card's original issuance date, which isn't tracked here.",
                )
                cc_statement_day = st.number_input(
                    "Statement day (optional)", min_value=0, max_value=31, step=1, value=0,
                    help="The nth day of the month this card's statement generates — a fixed property of the card, so it's not re-entered per statement. 0 = not set.",
                )
                if st.form_submit_button("Add card", icon=":material/credit_card:"):
                    if cc_issuer and cc_name and cc_limit > 0:
                        err = money_amount_error(cc_limit, "Credit limit")
                        if err:
                            st.error(err)
                        else:
                            confirm_create_dialog(
                                f"Add card **{cc_name}** (last 4: {cc_last4 or '—'}) from "
                                f"**{cc_issuer.strip()}** with a credit limit of **{php(cc_limit)}**?"
                                + dup_note(credit_card_warnings(cc_issuer, cc_name, cc_last4)),
                                "add_credit_card",
                                lambda a=(cc_issuer, cc_holder, cc_name, cc_last4, cc_limit, cc_af_month, cc_statement_day or None): add_credit_card(get_or_create_card_issuer(a[0]), *a[1:]),
                            )
                    else:
                        st.error("Issuer, card name, and a positive credit limit are required.")

        with st.expander("Cardholder registry"):
            st.dataframe(
                cardholders_df, hide_index=True,
                column_config={"is_active": st.column_config.CheckboxColumn("Active")},
            )
            with st.form("new_cardholder_form", border=False):
                ch_name = st.text_input("Name")
                ch_dept = st.text_input("Department (optional)")
                ch_contact = st.text_input("Contact number (optional)")
                if st.form_submit_button("Add cardholder", icon=":material/person_add:"):
                    if ch_name:
                        if guard_duplicate_submit("add_cardholder", ch_name, ch_dept, ch_contact):
                            add_cardholder(ch_name, ch_dept, ch_contact)
                            st.success(f"Added cardholder {ch_name}.")
                            refresh()
                        else:
                            st.warning("Duplicate submission ignored — identical to the click just before it.")
                    else:
                        st.error("Name is required.")

            if not cardholders_df.empty:
                st.markdown("**Edit a cardholder**")
                show_inactive_holders = st.checkbox("Include inactive cardholders", key="show_inactive_holders")
                holders_pickable_df = cardholders_df if show_inactive_holders else active_cardholders_df
                if holders_pickable_df.empty:
                    st.caption("No cardholders to show.")
                else:
                    edit_holder_id = st.selectbox(
                        "Cardholder", options=holders_pickable_df["cardholder_id"],
                        format_func=lambda hid: holders_pickable_df.set_index("cardholder_id").loc[hid, "name"]
                        + ("" if holders_pickable_df.set_index("cardholder_id").loc[hid, "is_active"] else " — inactive"),
                        key="edit_holder_pick",
                    )
                    current_holder_row = holders_pickable_df.set_index("cardholder_id").loc[edit_holder_id]
                    with st.form("edit_cardholder_form", border=False):
                        edit_holder_name = st.text_input("Name", value=str(current_holder_row["name"]), key=f"edit_holder_name_{edit_holder_id}")
                        edit_holder_dept = st.text_input("Department (optional)", value=current_holder_row["department"] or "", key=f"edit_holder_dept_{edit_holder_id}")
                        edit_holder_contact = st.text_input("Contact number (optional)", value=current_holder_row["contact_number"] or "", key=f"edit_holder_contact_{edit_holder_id}")
                        if st.form_submit_button("Save cardholder changes", icon=":material/save:"):
                            ok, msg = edit_cardholder(int(edit_holder_id), edit_holder_name, edit_holder_dept, edit_holder_contact)
                            (st.success if ok else st.error)(msg)
                            if ok:
                                refresh()

                    if int(current_holder_row["is_active"]):
                        if st.button("Deactivate this cardholder", icon=":material/block:", key=f"deactivate_holder_{edit_holder_id}"):
                            deactivate_cardholder(int(edit_holder_id))
                            st.success("Cardholder deactivated — their card/statement history stays intact, but they won't appear when adding a new card or statement.")
                            refresh()
                    else:
                        st.caption("This cardholder is currently inactive.")
                        if st.button("Reactivate this cardholder", icon=":material/refresh:", key=f"reactivate_holder_{edit_holder_id}"):
                            reactivate_cardholder(int(edit_holder_id))
                            st.success("Cardholder reactivated.")
                            refresh()

    # ----------------------------------------------------------------------
    with sub_check:
        cov_df = get_statement_coverage()
        cov_today = date.today()
        cov_this = cov_today.strftime("%B %Y")
        cov_prev = (cov_today.replace(day=1) - timedelta(days=1)).strftime("%B %Y")
        st.caption(
            f"Compares {cov_prev} with {cov_this} for every active card, so a card that had a statement last month "
            "but has none recorded yet this month stands out. Only as good as the statements recorded: a card shows "
            "\"No statement ever\" until its first one is entered."
        )
        if cov_df.empty:
            st.caption("No active cards on file.")
        else:
            counts = cov_df["status"].value_counts()
            with st.container(horizontal=True):
                st.metric("Missing this month", int(counts.get("Missing this month", 0)), border=True, help=f"Had a statement in {cov_prev}, none recorded for {cov_this}.")
                st.metric("Missing both months", int(counts.get("Missing both months", 0)), border=True, help=f"Older history, but nothing in {cov_prev} or {cov_this}.")
                st.metric("No statement ever", int(counts.get("No statement ever", 0)), border=True)
                st.metric("Not yet expected", int(counts.get("Not yet expected", 0)), border=True, help="Had one last month, but this card's statement isn't due to arrive yet this month (based on its Statement day, or last month's due day).")
                st.metric("OK", int(counts.get("OK", 0)), border=True, help=f"A statement is recorded for {cov_this}.")
            only_problems = st.toggle("Show only cards that need attention", value=True, key="cov_only_problems")
            cov_show = cov_df[~cov_df["status"].isin(["OK", "Not yet expected"])] if only_problems else cov_df
            report_df = cov_show.rename(columns={
                "status": "Status", "cardholder": "Card holder", "issuer": "Issuer", "card_name": "Card",
                "card_number_last4": "Last 4", "prev_month_due": f"{cov_prev} due date", "prev_month_amount": f"{cov_prev} amount",
                "this_month_due": f"{cov_this} due date", "this_month_amount": f"{cov_this} amount", "last_due_date": "Last statement on file",
                "expected_by": "Expected by",
            }).drop(columns=["card_id", "statement_day"])
            report_df = report_df.sort_values(["Card holder", "Status", "Issuer", "Card"]).reset_index(drop=True)
            st.dataframe(
                report_df, hide_index=True,
                column_config={
                    f"{cov_prev} amount": st.column_config.NumberColumn(format="₱ %.2f"),
                    f"{cov_this} amount": st.column_config.NumberColumn(format="₱ %.2f"),
                },
            )
            if report_df.empty:
                st.success("Every active card has a statement recorded for this month.")
            else:
                with st.expander("Follow-up list by card holder (copy and send)", expanded=True):
                    st.caption("One message per person, listing only the cards still missing a statement — send it so the bill can be paid on time and charges avoided.")
                    for holder, grp in report_df.groupby("Card holder"):
                        first_name = holder.split()[0] if holder and holder != "(no holder)" else "there"
                        card_lines = "\n".join(
                            f"- {r['Issuer']} {r['Card']} (ending {r['Last 4']})"
                            + (f" — last month's statement was due {r[f'{cov_prev} due date']}" if pd.notna(r[f"{cov_prev} due date"]) else "")
                            for _, r in grp.iterrows()
                        )
                        st.markdown(f"**{holder}** — {len(grp)} card(s)")
                        st.code(
                            f"Hi {first_name}, I don't have the {cov_this} statement yet for these cards:\n{card_lines}\n"
                            "Please send me a photo or PDF of the statement (or the amount due and due date) so we can pay on time and avoid charges. Thank you!",
                            language=None,
                        )
                xl = io.BytesIO()
                with pd.ExcelWriter(xl, engine="openpyxl") as writer:
                    report_df.to_excel(writer, index=False, sheet_name="Missing statements")
                    ws = writer.sheets["Missing statements"]
                    for col in ws.columns:
                        ws.column_dimensions[col[0].column_letter].width = min(max(len(str(c.value or "")) for c in col) + 2, 40)
                stamp = cov_today.strftime("%Y-%m-%d")
                d1, d2 = st.columns(2)
                d1.download_button("Download Excel report", data=xl.getvalue(), file_name=f"missing_cc_statements_{stamp}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:", key="cov_dl_xlsx")
                d2.download_button("Download CSV", data=report_df.to_csv(index=False), file_name=f"missing_cc_statements_{stamp}.csv",
                                   mime="text/csv", icon=":material/download:", key="cov_dl_csv")


# --------------------------------------------------------------------------
# Tab 6: Checks & checkbook
# --------------------------------------------------------------------------
with tab_checks:
    st.header("Checks & checkbook")

    with st.container(border=True):
        st.subheader("Checks to print")
        st.caption(
            "Every check already issued (recorded) but not yet printed, across every batch — pick "
            "as many as you like and print them together in one pass. Printing needs no check "
            "number and touches no booklet — the number isn't even drawn on the PDF (it's "
            "pre-printed on the physical blank stock). The Chrysanth file needs a number on every row, so "
            "checks without one get a temporary placeholder number in the file — assign the real numbers below "
            "after printing."
        )
        _unprinted_runs = get_unprinted_batches()
        if not _unprinted_runs.empty:
            st.warning(":material/print: **Exported to Chrysanth but not marked printed yet:** " + "; ".join(
                f"{r['tag']} ({r['check_count']} cheque(s), {php(r['total_amount'])}, exported {r['exported_at']})" for _, r in _unprinted_runs.iterrows())
                + ". Chrysanth reprints everything you tick, so always filter to the one run you mean to print — and use **Mark as printed** in Batch run history below once a run is printed.")
        pending_df = get_pending_check_run()
        if pending_df.empty:
            st.caption("Nothing pending — every issued check has been printed.")
        else:
            st.metric("Pending total", php(pending_df["amount"].sum()), border=True)
            display_pending = pending_df.copy()
            display_pending["check_number"] = display_pending["check_number"].fillna("No number yet")
            display_pending["amount"] = display_pending["amount"].apply(php)
            st.dataframe(
                display_pending[["check_number", "payee_name", "track", "reference", "check_date", "amount"]],
                hide_index=True,
            )

            print_options = {
                int(r["check_id"]): f"{r['payee_name']} — {php(r['amount'])} — {r['check_date']} ({r['track']}"
                + (f" {r['reference']})" if r["reference"] else ")")
                for _, r in pending_df.iterrows()
            }
            print_picked_ids = st.multiselect(
                "Checks to include (leave the rest parked for later)",
                options=list(print_options.keys()),
                default=list(print_options.keys()),
                format_func=lambda cid: print_options[cid],
                key="check_print_pick",
            )
            if PRINT_CHECKS_IN_APP:
                if st.button("Print these checks", icon=":material/print:", disabled=not print_picked_ids, key="check_print_batch_btn"):
                    picked_total = pending_df[pending_df["check_id"].isin(print_picked_ids)]["amount"].sum()
                    run_pdf = generate_check_run_pdf(print_picked_ids, get_check_print_calibration)
                    summary_pdf = generate_check_run_summary_pdf(print_picked_ids)
                    mark_checks_printed(print_picked_ids)
                    st.success(f"Printed {len(print_picked_ids)} check(s) totaling {php(picked_total)}.")
                    if run_pdf:
                        pdf_preview_and_download(run_pdf, "checks_to_print.pdf", key="check_print_pdf_dl", label="Checks PDF")
                    pdf_preview_and_download(summary_pdf, "checks_to_print_summary.pdf", key="check_print_summary_dl", label="Print Summary PDF")
            else:
                # Printing happens outside this system now (Chrysanth's own batch-print feature)
                # -- generating this export IS the "sent for printing" moment, same as the old
                # in-app "Print these checks" button used to be, so it logs a batch (Batch run
                # history, below) and stamps printed_at the same way mark_checks_printed() did.
                # Come back to "Assign check numbers" below once they're physically printed.
                _cs = get_chrysanth_settings()
                with st.expander("Chrysanth settings (account number and crossing)", expanded=not _cs["account_no"]):
                    st.caption("Chrysanth's import only accepts an account that is already set up inside Chrysanth, so type its account number exactly as it appears there (up to 20 characters). "
                               "The crossing is applied to every cheque in the file.")
                    _cs_acct = st.text_input("Account number in Chrysanth", value=_cs["account_no"], max_chars=20, key="chrys_account_no")
                    _cs_cross = st.selectbox("Crossing on every cheque", options=list(CHRYSANTH_CROSSING_OPTIONS), index=list(CHRYSANTH_CROSSING_OPTIONS).index(_cs["crossing"]),
                                             format_func=lambda k: CHRYSANTH_CROSSING_OPTIONS[k], key="chrys_crossing")
                    if st.button("Save Chrysanth settings", icon=":material/save:", key="chrys_save_btn"):
                        ok, msg = save_chrysanth_settings(_cs_acct, _cs_cross)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh()
                _n_unnumbered = int(sum(1 for _, r in pending_df.iterrows() if not has_value(r["check_number"])))
                if _n_unnumbered:
                    st.info(f"{_n_unnumbered} pending check(s) have no check number yet. Chrysanth's file needs one on every row, so those get a temporary placeholder number (starting with 9) "
                            "in the file only — nothing is assigned here. Assign the real numbers under Assign a check number once they are printed.")
                if not _cs["account_no"]:
                    st.info("Save the Chrysanth account number above to enable the export.")
                # Generating the file IS the "sent for printing" moment: it logs a batch (Batch run history, below)
                # and stamps printed_at, as the old in-app print button did.
                if st.button("Generate Chrysanth file (.txt)", icon=":material/download:", disabled=not print_picked_ids or not _cs["account_no"], key="check_export_excel_btn"):
                    if guard_duplicate_submit("create_check_print_batch", tuple(sorted(print_picked_ids))):
                        txt_bytes, txt_problems, txt_ids = generate_pending_check_run_txt(print_picked_ids, log_batch=True)
                        if not txt_ids:
                            st.error("Nothing could be exported:" + chr(10) + chr(10).join(f"- {p}" for p in txt_problems))
                        else:
                            newest_batch = get_check_print_batches().iloc[0]
                            # Keep the file across the rerun that refresh() triggers -- the download button used to vanish with it.
                            st.session_state["_chrysanth_txt"] = {"tag": print_batch_tag(int(newest_batch["print_batch_id"])), "count": len(txt_ids),
                                                                  "bytes": txt_bytes, "problems": txt_problems,
                                                                  "file_name": file_name_for_checks(txt_ids, int(newest_batch["print_batch_id"]))}
                            refresh()
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")
                if st.session_state.get("_chrysanth_txt"):
                    _last = st.session_state["_chrysanth_txt"]
                    st.success(f"{_last['tag']} logged — {_last['count']} check(s). Download the file, then in Chrysanth open Transaction, then File, then Import Cheque Transaction and pick it.")
                    st.info(f"**To print only this run in Chrysanth:** the Remark of every cheque in this run is **{_last['tag']}**. In Cheque Transaction History type **{_last['tag']}** in the **Remark** box "
                            f"at the top and click **Search** — then only this run is loaded, and the tick-box in the header row is safe to use. Do **not** use the filter arrow on the Remark column: it only hides "
                            "rows, and the header tick-box still selects the hidden ones. Chrysanth does not remember what was already printed, so check the total at the bottom "
                            "matches this run before you print, then use Mark as printed in Batch run history.")
                    st.download_button(f"Download {_last['file_name']}", data=_last["bytes"], file_name=_last["file_name"], mime="text/plain",
                                       icon=":material/download:", key="check_export_txt_dl")
                    if _last["problems"]:
                        st.warning("Please note:" + chr(10) + chr(10).join(f"- {p}" for p in _last["problems"]))

    with st.container(border=True):
        st.subheader("Batch run history")
        st.caption("Every export batch logged so far — what went out for printing, and when. Check numbers/booklets shown reflect whatever's been assigned since, not a snapshot frozen at export time.")
        batches_df = get_check_print_batches()
        if batches_df.empty:
            st.caption("No batches exported yet.")
        else:
            display_batches = batches_df.copy()
            display_batches["total_amount"] = display_batches["total_amount"].apply(php)
            display_batches.insert(0, "batch_no", range(len(display_batches), 0, -1))
            display_batches.insert(1, "chrysanth_tag", [print_batch_tag(int(b)) for b in display_batches["print_batch_id"]])
            display_batches["printed"] = display_batches["printed_confirmed_at"].apply(lambda v: f"Printed {str(v)[:16]}" if has_value(v) else "Not printed yet")
            st.dataframe(
                display_batches[["batch_no", "chrysanth_tag", "printed", "exported_at", "check_count", "total_amount"]],
                hide_index=True,
                column_config={
                    "batch_no": "Batch #",
                    "chrysanth_tag": "Chrysanth run number",
                    "printed": "Printed in Chrysanth?",
                    "exported_at": "Exported",
                    "check_count": "Checks",
                    "total_amount": "Total",
                },
            )
            batch_options = {int(r["print_batch_id"]): f"Batch #{n} — {r['exported_at']} — {r['check_count']} check(s)" for n, (_, r) in zip(range(len(batches_df), 0, -1), batches_df.iterrows())}
            batch_pick = st.selectbox("View batch detail", options=list(batch_options.keys()), format_func=lambda bid: batch_options[bid], key="batch_history_pick")
            _picked_row = batches_df[batches_df["print_batch_id"] == int(batch_pick)].iloc[0]
            _picked_printed = has_value(_picked_row["printed_confirmed_at"])
            _pk1, _pk2 = st.columns([1, 2])
            if _picked_printed:
                _pk2.caption(f"{print_batch_tag(int(batch_pick))} was marked printed on {str(_picked_row['printed_confirmed_at'])[:16]}.")
                if _pk1.button("Un-mark as printed", icon=":material/undo:", key=f"unmark_printed_{int(batch_pick)}"):
                    ok, msg = mark_batch_printed(int(batch_pick), False)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()
            else:
                _pk2.caption(f"{print_batch_tag(int(batch_pick))} is exported but not marked printed. Once you have printed it in Chrysanth, mark it here.")
                if _pk1.button("Mark as printed", icon=":material/print:", type="primary", key=f"mark_printed_{int(batch_pick)}"):
                    ok, msg = mark_batch_printed(int(batch_pick), True)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()
            _bx_bytes, _bx_problems = generate_batch_txt(int(batch_pick))
            st.download_button("Download this batch's Chrysanth file (.txt)", data=_bx_bytes, disabled=not get_chrysanth_settings()["account_no"],
                               file_name=file_name_for_batch(int(batch_pick)), mime="text/plain", icon=":material/download:", key=f"batch_txt_dl_{int(batch_pick)}")
            if _bx_problems:
                st.caption("Not in this file: " + " · ".join(_bx_problems))
            detail_df = get_check_print_batch_detail(int(batch_pick))
            if not detail_df.empty:
                display_detail = detail_df.copy()
                display_detail["check_number"] = display_detail["check_number"].fillna("Not yet assigned")
                display_detail["booklet_code"] = display_detail["booklet_code"].fillna("—")
                display_detail["amount"] = display_detail["amount"].apply(php)
                st.dataframe(
                    display_detail[["check_number", "payee_name", "track", "reference", "check_date", "amount", "booklet_code"]],
                    hide_index=True,
                    column_config={
                        "check_number": "Check #", "payee_name": "Payee", "track": "Track",
                        "reference": "OS/Lender Ref", "check_date": "Check Date", "amount": "Amount", "booklet_code": "Booklet",
                    },
                )

    with st.container(border=True):
        st.subheader("Assign check numbers")
        st.caption(
            "Every Active check that doesn't have a number yet, across every batch — regardless of "
            "whether it's been printed. Type each one's number in directly (a backfill — checks "
            "already found/written from an older or exhausted booklet). For the normal one-at-a-"
            "time case, use the \"Assign a check number\" section in Order Statement/Lenders "
            "instead — it offers a dropdown of a booklet's available numbers. Booklets are "
            "registered and managed in Admin & audit."
        )
        unnumbered_df = get_unnumbered_checks()
        if unnumbered_df.empty:
            st.caption("Nothing unnumbered — every active check already has a number.")
        else:
            st.metric("Unnumbered total", php(unnumbered_df["amount"].sum()), border=True)
            assign_options = {
                int(r["check_id"]): f"{r['payee_name']} — {php(r['amount'])} — {r['check_date']} ({r['track']}"
                + (f" {r['reference']})" if r["reference"] else ")")
                for _, r in unnumbered_df.iterrows()
            }
            assign_picked_ids = st.multiselect(
                "Checks to assign in this pass (leave the rest unnumbered for later)",
                options=list(assign_options.keys()),
                default=list(assign_options.keys()),
                format_func=lambda cid: assign_options[cid],
                key="check_assign_pick",
            )

            st.caption("Type each check's number directly (Phase 6 Redux v2 dropped auto-sequencing here — the per-batch \"Assign a check number\" section in Order Statement/Lenders already covers picking a number from a booklet's dropdown one check at a time; this bulk grid is for typing several in at once, e.g. a backfill from an older booklet).")
            backfill_booklets_df = get_booklets(PRIMARY_BANK_ACCOUNT_ID, statuses=("Active", "Exhausted", "Archived"))
            b_ref_options = {None: "(none / not tracked)"}
            for _, r in backfill_booklets_df.iterrows():
                b_ref_options[int(r["booklet_id"])] = f"{r['booklet_code'] or ('Booklet #' + str(int(r['booklet_id'])))} [{r['status']}]"
            ref_booklet_id = st.selectbox(
                "Which booklet do these belong to? (optional, for record-keeping)",
                options=list(b_ref_options.keys()), format_func=lambda bid: b_ref_options[bid], key="check_assign_manual_booklet",
            )
            if not assign_picked_ids:
                st.caption("Pick at least one check above.")
            else:
                manual_df = pd.DataFrame({
                    "check_id": assign_picked_ids,
                    "check": [assign_options[cid] for cid in assign_picked_ids],
                    "check_number": [""] * len(assign_picked_ids),
                })
                edited_manual_df = st.data_editor(
                    manual_df, hide_index=True, num_rows="fixed", disabled=["check_id", "check"],
                    key="check_assign_manual_editor",
                    column_config={
                        "check_id": st.column_config.NumberColumn("ID"),
                        "check": st.column_config.TextColumn("Check"),
                        "check_number": st.column_config.TextColumn("Check Number (as physically written)"),
                    },
                )
                if st.button("Assign numbers", icon=":material/tag:", key="check_assign_manual_btn"):
                    assignments = {int(r["check_id"]): r["check_number"] for _, r in edited_manual_df.iterrows()}
                    if guard_duplicate_submit("bulk_assign_manual", ref_booklet_id, repr(assignments)):
                        ok, msg, assigned_ids = assign_check_numbers_manual(assignments, ref_booklet_id)
                        (st.success if ok else st.error)(msg)
                        if assigned_ids:
                            refresh()
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")

    with st.expander("Log a spoiled check (misprint / jam)"):
        st.caption("Routed to the internal control payee 'Error Checks Only (for control)' — amount and date are always null since no real transaction occurred. A number logged here is treated as unavailable everywhere else from now on, so it won't show up in a booklet's dropdown.")
        spoil_booklets_df = get_booklets(PRIMARY_BANK_ACCOUNT_ID, statuses=("Active",))
        suggested_number = ""
        if not spoil_booklets_df.empty:
            sb_options = {int(r["booklet_id"]): (r["booklet_code"] or f"Booklet #{int(r['booklet_id'])}") for _, r in spoil_booklets_df.iterrows()}
            spoil_booklet_pick = st.selectbox(
                "Booklet the spoiled leaf came from (optional, for a suggested number)",
                options=[None] + list(sb_options.keys()), format_func=lambda bid: "(none)" if bid is None else sb_options[bid], key="spoil_booklet_pick",
            )
            if spoil_booklet_pick is not None:
                brow = spoil_booklets_df.set_index("booklet_id").loc[spoil_booklet_pick]
                suggested_number = str(int(brow["next_available_number"]))
        with st.form("spoiled_check_form", border=False):
            spoiled_number = st.text_input("Check number", value=suggested_number, help="Edit if the spoiled leaf's physical number differs from the suggested next number.")
            spoiled_reason = st.text_input("Reason", placeholder="e.g. printer jam, misaligned print")
            if st.form_submit_button("Log spoiled check", icon=":material/report:"):
                if not spoiled_number:
                    st.error("Check number is required.")
                elif guard_duplicate_submit("log_spoiled_check", spoiled_number, spoiled_reason):
                    ok, msg = log_spoiled_check(spoiled_number, spoiled_reason)
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()
                else:
                    st.warning("Duplicate submission ignored — identical to the click just before it.")

    spoiled_df = q("SELECT * FROM spoiled_checks ORDER BY spoiled_id DESC")
    if not spoiled_df.empty:
        st.markdown("**Spoiled checks on file (Error Checks Only, for control)**")
        st.dataframe(spoiled_df, hide_index=True)

# --------------------------------------------------------------------------
# Tab 7: Reports
# --------------------------------------------------------------------------
with tab_reports:
    st.header("Reports")
    st.caption("Pick a report — its filters, tables, and exports appear below, one report at a time.")

    REPORT_OPTIONS = [
        "Overview — highlights & insights",
        "Possible duplicates — check everything on file",
        "Schedule of payments — all tracks",
        "Parked checks (not yet printed)",
        "Supplier — invoice & check history",
        "Supplier — batch rundown (paid / partial / pending)",
        "Supplier — invoice rundown (paid / partial / pending)",
        "Private lender — loan note & check history",
        "Bank loan — installment history",
        "Credit card — statement history",
        "Master rollup — supplier AP aging",
        "Master rollup — private lender exposure",
        "Master rollup — bank loan servicing",
    ]
    report_choice = st.selectbox("Report", REPORT_OPTIONS, key="report_type_picker")
    st.divider()

    if report_choice == "Overview — highlights & insights":
        st.subheader("Overview — highlights & insights")
        st.caption("What happened in a day, week, month, quarter or year, compared with the period before it — highlights first, then the numbers behind them. Pick the short version for a quick read or the full one for the detail.")
        ov1, ov2, ov3 = st.columns([2, 2, 2])
        ov_kind = ov1.selectbox("Period", PERIOD_KINDS, index=2, key="rpt_ov_kind")
        ov_anchor = ov2.date_input("Any date inside the period", value=date.today(), key="rpt_ov_anchor")
        ov_version = ov3.radio("Version", ["Short", "Full"], horizontal=True, key="rpt_ov_version")
        ov = get_overview(ov_kind, ov_anchor)
        ov_cur, ov_prev = ov["current"], ov["previous"]
        st.markdown(f"#### {ov['label']}  ·  compared with {ov['prev_label']}")

        st.markdown("**Highlights**")
        for line in ov["highlights"]:
            st.markdown(f"- {line}")

        def _ov_delta(now, before):
            return None if not before else f"{(now - before) / before * 100:+.0f}%"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Paid out", php(ov_cur["paid"]), _ov_delta(ov_cur["paid"], ov_prev["paid"]), border=True, help="Checks, loan installments and card payments whose date has arrived.")
        m2.metric("Scheduled, not yet due", php(ov_cur["scheduled"]), _ov_delta(ov_cur["scheduled"], ov_prev["scheduled"]), border=True)
        m3.metric("Interest cost", php(ov_cur["interest_total"]), _ov_delta(ov_cur["interest_total"], ov_prev["interest_total"]), delta_color="inverse", border=True)
        m4.metric("Total outstanding today", php(ov["outstanding_today"]), border=True, help="All four tracks, as on the Dashboard.")

        st.markdown("**Where the money went**")
        ov_tracks = ov_cur["by_track"].copy()
        ov_prev_tracks = ov_prev["by_track"].set_index("track")
        ov_tracks["Total"] = ov_tracks["paid"] + ov_tracks["scheduled"]
        ov_tracks["Previous"] = [float(ov_prev_tracks.loc[t, "paid"] + ov_prev_tracks.loc[t, "scheduled"]) for t in ov_tracks["track"]]
        st.dataframe(
            ov_tracks.rename(columns={"track": "Track", "paid": "Paid", "scheduled": "Scheduled"}), hide_index=True,
            column_config={c: st.column_config.NumberColumn(c, format="₱ %.2f") for c in ("Paid", "Scheduled", "Total", "Previous")},
        )

        if ov["watch"]:
            st.markdown("**Needs attention**")
            st.dataframe(pd.DataFrame(ov["watch"]).rename(columns={"item": "Item", "count": "Count", "detail": "Detail"}), hide_index=True)

        if ov_version == "Full":
            if not ov_cur["payees"].empty:
                st.markdown("**Top payees**")
                st.dataframe(
                    ov_cur["payees"].head(10).rename(columns={"payee": "Payee", "track": "Track", "paid": "Paid", "scheduled": "Scheduled", "total": "Total"}), hide_index=True,
                    column_config={c: st.column_config.NumberColumn(c, format="₱ %.2f") for c in ("Paid", "Scheduled", "Total")},
                )
            st.markdown("**Cost of financing**")
            if ov_cur["interest"].empty:
                st.caption("No interest recorded in this period.")
            else:
                st.dataframe(ov_cur["interest"].head(8).rename(columns={"source": "Source", "name": "Facility", "interest": "Interest"}), hide_index=True,
                             column_config={"Interest": st.column_config.NumberColumn("Interest", format="₱ %.2f")})
            st.markdown("**Payment behavior**")
            ov_b = ov_cur["behavior"]
            if not ov_b["count"]:
                st.caption("Not enough itemised supplier invoices in this period to measure how early or late checks were.")
            else:
                st.write(f"{ov_b['count']} supplier check(s) cleared — on average {abs(ov_b['avg_days']):.0f} day(s) {'after' if ov_b['avg_days'] > 0 else 'before'} the due date, {ov_b['on_time_pct']:.0f}% on time.")
            ov_up = ov["upcoming"]
            st.markdown(f"**Coming up — {ov_up['label']}**")
            if not ov_up["total"]:
                st.caption("Nothing scheduled.")
            else:
                st.write(f"{php(ov_up['total'])} falls due. " + ("Heaviest days: " + "; ".join(f"{d} ({php(a)})" for d, a in ov_up["peak_days"]) if ov_up["peak_days"] else ""))
                uc1, uc2 = st.columns(2)
                uc1.dataframe(pd.DataFrame([{"Track": k, "Amount due": v} for k, v in ov_up["by_track"].items()]), hide_index=True,
                              column_config={"Amount due": st.column_config.NumberColumn("Amount due", format="₱ %.2f")})
                uc2.dataframe(pd.DataFrame([{"Week starting": w, "Amount due": a} for w, a in ov_up["by_week"]]), hide_index=True,
                              column_config={"Amount due": st.column_config.NumberColumn("Amount due", format="₱ %.2f")})

        if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="ov_pdf_btn"):
            pdf_preview_and_download(
                generate_overview_pdf(ov, short=(ov_version == "Short")),
                f"overview_{ov_kind.lower()}_{ov['start']}_{ov_version.lower()}.pdf", key="ov_pdf_download", label="Overview PDF",
            )

    elif report_choice == "Possible duplicates — check everything on file":
        st.subheader("Possible duplicates — check everything on file")
        st.caption(
            "Looks across every supplier and batch, migrated ones included, for the same invoice number written differently "
            "(SI-00123 / 123), several invoices with the same amount on the same date, and batches with the same total. "
            "Not every finding is a mistake — if it is genuine, tick it and mark it as not a duplicate so it stops showing."
        )
        dup_show_dismissed = st.checkbox("Also show the ones I dismissed", key="rpt_dup_show_dismissed")
        dup_df = scan_possible_duplicates(include_dismissed=dup_show_dismissed)
        if dup_df.empty:
            st.success("Nothing suspicious found.")
        else:
            dm1, dm2 = st.columns(2)
            dm1.metric("Findings", len(dup_df), border=True)
            dm2.metric("Amount involved", php(dup_df["amount"].sum()), border=True)
            dup_view = dup_df[["kind", "supplier", "detail", "amount", "where_found", "count", "dismissed", "dup_key"]].copy()
            dup_view.insert(0, "select", False)
            dup_edited = st.data_editor(
                dup_view.drop(columns=["dup_key"]), hide_index=True, key="rpt_dup_editor",
                disabled=["kind", "supplier", "detail", "amount", "where_found", "count", "dismissed"],
                column_config={
                    "select": st.column_config.CheckboxColumn("Select"), "kind": "What matched", "supplier": "Supplier", "detail": "Details",
                    "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"), "where_found": "Batches", "count": "Items", "dismissed": "Dismissed",
                },
            )
            chosen_keys = [dup_view.loc[i, "dup_key"] for i in dup_edited.index[dup_edited["select"]]]
            dc1, dc2 = st.columns(2)
            if dc1.button("Mark selected as not a duplicate", icon=":material/check:", key="rpt_dup_dismiss_btn", disabled=not chosen_keys):
                dismiss_duplicates(chosen_keys)
                refresh()
            if dup_show_dismissed and dc2.button("Restore selected", icon=":material/undo:", key="rpt_dup_restore_btn", disabled=not chosen_keys):
                restore_duplicates(chosen_keys)
                refresh()
            st.download_button("Download CSV", data=dup_df.drop(columns=["dup_key"]).to_csv(index=False), file_name="possible_duplicates.csv", mime="text/csv", key="dl_dup_scan")

    elif report_choice == "Schedule of payments — all tracks":
        st.subheader("Schedule of payments — all tracks")
        st.caption("Everything still owed, across all 4 tracks combined. Grouped by actual payment date — a due date falling on a weekend is paid the next banking day (Monday), so \"Due Date\" (as originally scheduled) and \"Payment Date\" (when it's actually paid/needs funding) can differ. \"Reference\" holds whichever identifier applies to that track (check number for Supplier/Lender, loan ref for Bank Loan, card name for Credit Card).")
        sched_detail = get_payment_schedule_all_tracks()
        if sched_detail.empty:
            st.caption("Nothing scheduled.")
        else:
            f1, f2 = st.columns(2)
            with f1:
                track_filter = st.multiselect("Filter by track", options=sorted(sched_detail["track"].unique()), key="sched_track_filter")
            with f2:
                sched_months = sorted(sched_detail["actual_payment_date"].astype(str).str[:7].unique())
                month_labels = {m: pd.to_datetime(m + "-01").strftime("%B %Y") for m in sched_months}
                month_filter = st.selectbox(
                    "Filter by month (payment date)", options=["All months"] + sched_months,
                    format_func=lambda m: "All months" if m == "All months" else month_labels[m],
                    key="sched_month_filter",
                )

            display_sched = sched_detail
            if track_filter:
                display_sched = display_sched[display_sched["track"].isin(track_filter)]
            if month_filter != "All months":
                display_sched = display_sched[display_sched["actual_payment_date"].astype(str).str.startswith(month_filter)]

            filter_bits = []
            if track_filter:
                filter_bits.append(", ".join(track_filter))
            if month_filter != "All months":
                filter_bits.append(month_labels[month_filter])
            st.caption(f"Exporting {' — '.join(filter_bits)} only." if filter_bits else "The tables, CSVs, and PDF below export exactly what's filtered here.")

            if display_sched.empty:
                st.caption("Nothing scheduled for this filter.")
            else:
                sched_daily = display_sched.groupby("actual_payment_date").agg(num_items=("amount", "count"), total_amount=("amount", "sum")).reset_index().sort_values("actual_payment_date")

                st.markdown("**Daily totals**")
                st.dataframe(
                    sched_daily, hide_index=True,
                    column_config={
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "num_items": st.column_config.NumberColumn("# Items"),
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                    },
                )
                s1, s2 = st.columns(2)
                s1.metric("Total items scheduled", int(sched_daily["num_items"].sum()), border=True)
                s2.metric("Total amount scheduled", php(sched_daily["total_amount"].sum()), border=True)

                st.markdown("**Full breakdown**")
                st.dataframe(
                    display_sched, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

                sched_file_bits = [t.lower().replace(" ", "_") for t in track_filter] + ([month_filter] if month_filter != "All months" else [])
                sched_file_suffix = "_".join(sched_file_bits) if sched_file_bits else "all_tracks"
                st.download_button("Download daily totals CSV", data=sched_daily.to_csv(index=False), file_name=f"payment_schedule_daily_totals_{sched_file_suffix}.csv", mime="text/csv", key="dl_sched_daily")
                st.download_button("Download full breakdown CSV", data=display_sched.to_csv(index=False), file_name=f"payment_schedule_{sched_file_suffix}.csv", mime="text/csv", key="dl_sched_detail")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="sched_pdf_btn"):
                    pdf_preview_and_download(
                        generate_payment_schedule_pdf(track_filter or None, month_filter if month_filter != "All months" else None),
                        f"payment_schedule_{sched_file_suffix}.pdf", key="sched_pdf_download", label="Schedule of Payments PDF",
                    )

    elif report_choice == "Parked checks (not yet printed)":
        st.subheader("Parked checks (not yet printed)")
        st.caption(
            "Every check already recorded but still waiting on a check number — Phase 6 Redux "
            "assigns a number at PRINT time, not issue time. Grouped by check date (the "
            "Checks & checkbook tab's Current check run is the live, same-scope view for actually "
            "printing these)."
        )
        parked_detail = get_parked_checks_report()
        if parked_detail.empty:
            st.caption("Nothing parked — every issued check has been printed.")
        else:
            parked_months = sorted(parked_detail["actual_payment_date"].astype(str).str[:7].unique())
            parked_month_labels = {m: pd.to_datetime(m + "-01").strftime("%B %Y") for m in parked_months}
            parked_month_filter = st.selectbox(
                "Filter by month (check date)", options=["All months"] + parked_months,
                format_func=lambda m: "All months" if m == "All months" else parked_month_labels[m],
                key="parked_month_filter",
            )
            display_parked = parked_detail
            if parked_month_filter != "All months":
                display_parked = display_parked[display_parked["actual_payment_date"].astype(str).str.startswith(parked_month_filter)]

            if display_parked.empty:
                st.caption("Nothing parked for this filter.")
            else:
                parked_daily = display_parked.groupby("actual_payment_date").agg(num_checks=("amount", "count"), total_amount=("amount", "sum")).reset_index().sort_values("actual_payment_date")

                st.markdown("**Daily totals**")
                st.dataframe(
                    parked_daily, hide_index=True,
                    column_config={
                        "actual_payment_date": st.column_config.DateColumn("Check Date"),
                        "num_checks": st.column_config.NumberColumn("# Checks"),
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                    },
                )
                p1, p2 = st.columns(2)
                p1.metric("Total checks parked", int(parked_daily["num_checks"].sum()), border=True)
                p2.metric("Total amount parked", php(parked_daily["total_amount"].sum()), border=True)

                st.markdown("**Full breakdown**")
                st.dataframe(
                    display_parked, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Check Date"),
                        "actual_payment_date": st.column_config.DateColumn("Payment Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

                parked_file_suffix = parked_month_filter if parked_month_filter != "All months" else "all"
                st.download_button("Download daily totals CSV", data=parked_daily.to_csv(index=False), file_name=f"parked_checks_daily_totals_{parked_file_suffix}.csv", mime="text/csv", key="dl_parked_daily")
                st.download_button("Download full breakdown CSV", data=display_parked.to_csv(index=False), file_name=f"parked_checks_{parked_file_suffix}.csv", mime="text/csv", key="dl_parked_detail")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="parked_pdf_btn"):
                    pdf_preview_and_download(
                        generate_parked_checks_pdf(parked_month_filter if parked_month_filter != "All months" else None),
                        f"parked_checks_{parked_file_suffix}.pdf", key="parked_pdf_download", label="Parked Checks PDF",
                    )

    elif report_choice == "Supplier — invoice & check history":
        st.subheader("Supplier — invoice & check history")
        st.caption("Every order statement (batch), invoice, and check for one supplier — pick a supplier, narrow by date/status, and pull a Statement of Account whenever you're asked for this supplier's transaction history.")
        sup_options = creditor_options_all("SUPPLIER")
        if not sup_options:
            st.caption("No suppliers on file yet.")
        else:
            sup_id = st.selectbox("Supplier", options=list(sup_options), format_func=lambda cid: sup_options[cid], key="rpt_supplier_pick")
            sup_statements, sup_invoices, sup_checks = get_supplier_history(sup_id)

            sup_date_from, sup_date_to = history_date_range(
                [
                    sup_statements["created_at"] if not sup_statements.empty else None,
                    sup_invoices["invoice_date"] if not sup_invoices.empty else None,
                    sup_checks["check_date"] if not sup_checks.empty else None,
                ],
                key_prefix=f"rpt_sup_date_{sup_id}",
            )
            sup_statements_f = filter_by_date(sup_statements, "created_at", sup_date_from, sup_date_to)
            sup_invoices_f = filter_by_date(sup_invoices, "invoice_date", sup_date_from, sup_date_to)
            sup_checks_f = filter_by_date(sup_checks, "check_date", sup_date_from, sup_date_to)

            sup_invoices_live = sup_invoices_f[sup_invoices_f["status"] != "Cancelled"] if not sup_invoices_f.empty else sup_invoices_f
            sup_checks_live = sup_checks_f[sup_checks_f["status"] == "Active"] if not sup_checks_f.empty else sup_checks_f
            m1, m2, m3 = st.columns(3)
            m1.metric("Batches (order statements)", len(sup_statements_f), border=True)
            m2.metric("Total invoiced", php(sup_invoices_live["amount"].sum() if not sup_invoices_live.empty else 0), border=True, help="Excludes cancelled invoices. Reflects the date range above, not the status filters below.")
            m3.metric("Total paid (checks)", php(sup_checks_live["amount"].sum() if not sup_checks_live.empty else 0), border=True, help="Active checks only — excludes cancelled/replaced checks. Reflects the date range above, not the status filters below.")

            st.markdown("**Order statements (batches)**")
            sup_statements_shown = filter_by_status(sup_statements_f, "status", key=f"rpt_sup_batch_status_{sup_id}")
            if sup_statements_shown.empty:
                st.caption("No batches for this period/status.")
            else:
                st.dataframe(
                    sup_statements_shown, hide_index=True,
                    column_config={
                        "total_amount": st.column_config.NumberColumn("Total", format="₱ %.2f"),
                        "created_at": st.column_config.DatetimeColumn("Created"),
                    },
                )

            st.markdown("**Invoices**")
            sup_invoices_shown = filter_by_status(sup_invoices_f, "status", key=f"rpt_sup_invoice_status_{sup_id}")
            if sup_invoices_shown.empty:
                st.caption("No invoices for this period/status.")
            else:
                st.dataframe(
                    sup_invoices_shown, hide_index=True,
                    column_config={
                        "invoice_date": st.column_config.DateColumn("Invoice Date"),
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "gross_amount": st.column_config.NumberColumn("Gross", format="₱ %.2f"),
                        "amount": st.column_config.NumberColumn("Net Amount", format="₱ %.2f"),
                    },
                )

            st.markdown("**Checks issued**")
            sup_checks_shown = filter_by_status(sup_checks_f, "status", key=f"rpt_sup_check_status_{sup_id}")
            if sup_checks_shown.empty:
                st.caption("No checks issued for this period/status.")
            else:
                st.dataframe(
                    sup_checks_shown, hide_index=True,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

            sup_slug = sup_options[sup_id].lower().replace(" ", "_")
            c1, c2, c3 = st.columns(3)
            c1.download_button("Download batches CSV", data=sup_statements_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_batches.csv", mime="text/csv", key="dl_sup_batches")
            c2.download_button("Download invoices CSV", data=sup_invoices_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_invoices.csv", mime="text/csv", key="dl_sup_invoices")
            c3.download_button("Download checks CSV", data=sup_checks_shown.to_csv(index=False), file_name=f"supplier_{sup_slug}_checks.csv", mime="text/csv", key="dl_sup_checks")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            sup_group_by_batch = st.checkbox("Break out each batch separately (instead of one combined statement)", key=f"rpt_sup_group_{sup_id}")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="sup_pdf_btn"):
                pdf_preview_and_download(
                    generate_supplier_statement_pdf(
                        sup_options[sup_id], sup_statements_shown, sup_invoices_shown, sup_checks_shown,
                        sup_date_from, sup_date_to, group_by_batch=sup_group_by_batch,
                    ),
                    f"supplier_{sup_slug}_statement.pdf", key="sup_pdf_download", label="Supplier Statement of Account PDF",
                )

    elif report_choice == "Supplier — batch rundown (paid / partial / pending)":
        st.subheader("Supplier — batch rundown")
        st.caption("Every issued batch (order statement) with how much is paid, how much is covered by post-dated checks, and what is still open. Paid = checks whose date has arrived. Pick one supplier or all of them.")
        rd_options = creditor_options_all("SUPPLIER")
        rd_choice = st.selectbox("Supplier", options=[None] + list(rd_options), format_func=lambda cid: "All suppliers" if cid is None else rd_options[cid], key="rpt_rundown_pick")
        rd_all = get_payables_rundown(rd_choice)
        if not rd_all.empty and rd_all["first_check"].notna().any():
            rd_min, rd_max = date.fromisoformat(rd_all["first_check"].min()), date.fromisoformat(rd_all["last_check"].max())
        else:
            rd_min = rd_max = date.today()
        rd_from_col, rd_to_col = st.columns(2)
        rd_from = rd_from_col.date_input("From", value=rd_min, key=f"rpt_rundown_from_{rd_choice}", help="Batches with a check dated on or after this day.")
        rd_to = rd_to_col.date_input("To", value=rd_max, key=f"rpt_rundown_to_{rd_choice}", help="Batches with a check dated on or before this day. Amounts shown are for the whole batch.")
        rd_df = get_payables_rundown(rd_choice, rd_from, rd_to)
        if rd_df.empty:
            st.caption("No issued batches for this selection.")
        else:
            rd_status_order = ["Paid", "Partially Paid", "Pending"]
            rd_cols = st.columns(3)
            for col, status_name in zip(rd_cols, rd_status_order):
                part = rd_df[rd_df["status"] == status_name]
                col.metric(status_name, f"{len(part)} batch(es)", border=True)
                col.caption(f"Net total {php(part['net_total'].sum())} · still owed {php(part['balance'].sum())}")
            rd_picked = st.multiselect("Show", options=rd_status_order, default=rd_status_order, key="rpt_rundown_status")
            rd_view = rd_df[rd_df["status"].isin(rd_picked)].copy()
            rd_view["running_balance"] = rd_view["balance"].groupby(rd_view["supplier"]).cumsum()
            rd_money = {"first_check": st.column_config.DateColumn("First check"), "last_check": st.column_config.DateColumn("Last check")}
            rd_money.update({c: st.column_config.NumberColumn(label, format="₱ %.2f") for c, label in
                        [("net_total", "Net total"), ("paid", "Paid"), ("scheduled", "Post-dated"), ("open", "Not yet covered"), ("balance", "Still owed"), ("running_balance", "Running balance")]})
            if rd_choice is None:
                st.markdown("**Per supplier**")
                rd_sum = rd_view.pivot_table(index="supplier", columns="status", values="net_total", aggfunc="sum", fill_value=0.0)
                for name in rd_status_order:
                    if name not in rd_sum.columns:
                        rd_sum[name] = 0.0
                rd_sum = rd_sum[rd_status_order].reset_index()
                rd_sum["Total"] = rd_sum[rd_status_order].sum(axis=1)
                st.dataframe(rd_sum, hide_index=True, column_config={n: st.column_config.NumberColumn(n, format="₱ %.2f") for n in rd_status_order + ["Total"]})
            st.markdown("**Batches**")
            st.dataframe(
                rd_view[["supplier", "batch", "status", "first_check", "last_check", "net_total", "paid", "scheduled", "open", "balance", "running_balance"]],
                hide_index=True, column_config=rd_money,
            )
            st.download_button("Download rundown CSV", data=rd_view.to_csv(index=False), file_name="batch_rundown.csv", mime="text/csv", key="dl_rundown")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="batch_rundown_pdf_btn"):
                rd_rows = [[r.supplier, r.batch, r.status, str(r.first_check or "—")[:10], str(r.last_check or "—")[:10], _pdf_money(r.net_total), _pdf_money(r.paid), _pdf_money(r.scheduled), _pdf_money(r.balance), _pdf_money(r.running_balance)] for r in rd_view.itertuples()]
                rd_summary = [(n, int((rd_view["status"] == n).sum()), float(rd_view.loc[rd_view["status"] == n, "balance"].sum())) for n in rd_status_order]
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Batch rundown", "All suppliers" if rd_choice is None else rd_options[rd_choice],
                        ["Supplier", "Batch", "Status", "First check", "Last check", "Net total", "Paid", "Post-dated", "Still owed", "Running Balance"], rd_rows,
                        [0.16, 0.13, 0.08, 0.07, 0.07, 0.09, 0.09, 0.09, 0.10, 0.12], [5, 6, 7, 8, 9], rd_summary, f"Checks dated {rd_from} to {rd_to}  ·  amounts in the summary are still owed",
                    ),
                    "batch_rundown.pdf", key="batch_rundown_pdf_download", label="Batch Rundown PDF",
                )

    elif report_choice == "Supplier — invoice rundown (paid / partial / pending)":
        st.subheader("Supplier — invoice rundown")
        st.caption("Every invoice with its status — Paid, Partially Paid or Pending. Pick one supplier or all of them, and narrow by invoice date. For payments per batch, use the batch rundown.")
        ir_options = creditor_options_all("SUPPLIER")
        ir_choice = st.selectbox("Supplier", options=[None] + list(ir_options), format_func=lambda cid: "All suppliers" if cid is None else ir_options[cid], key="rpt_inv_rundown_pick")
        ir_all = get_invoice_rundown(ir_choice)
        if ir_all.empty:
            st.caption("No invoices on file for this selection. (Migrated history was loaded as batches only — see the batch rundown for those.)")
        else:
            ir_min, ir_max = date.fromisoformat(str(ir_all["invoice_date"].min())[:10]), date.fromisoformat(str(ir_all["invoice_date"].max())[:10])
            ir_from_col, ir_to_col = st.columns(2)
            ir_from = ir_from_col.date_input("From", value=ir_min, key=f"rpt_inv_from_{ir_choice}")
            ir_to = ir_to_col.date_input("To", value=ir_max, key=f"rpt_inv_to_{ir_choice}")
            ir_df = get_invoice_rundown(ir_choice, ir_from, ir_to)
            ir_order = ["Paid", "Partially Paid", "Pending"]
            for col, status_name in zip(st.columns(3), ir_order):
                part = ir_df[ir_df["status"] == status_name]
                col.metric(status_name, f"{len(part)} invoice(s)", border=True)
                col.caption(f"Amount {php(part['amount'].sum())}")
            ir_picked = st.multiselect("Show", options=ir_order, default=ir_order, key="rpt_inv_rundown_status")
            ir_view = ir_df[ir_df["status"].isin(ir_picked)].copy()
            # Running balance = what is still owed, adding up row by row within each supplier (Paid rows add nothing).
            ir_view["running_balance"] = ir_view["amount"].where(ir_view["status"] != "Paid", 0.0).groupby(ir_view["supplier"]).cumsum()
            if ir_choice is None and not ir_view.empty:
                st.markdown("**Per supplier**")
                ir_sum = ir_view.pivot_table(index="supplier", columns="status", values="amount", aggfunc="sum", fill_value=0.0)
                for name in ir_order:
                    if name not in ir_sum.columns:
                        ir_sum[name] = 0.0
                ir_sum = ir_sum[ir_order].reset_index()
                ir_sum["Total"] = ir_sum[ir_order].sum(axis=1)
                st.dataframe(ir_sum, hide_index=True, column_config={n: st.column_config.NumberColumn(n, format="₱ %.2f") for n in ir_order + ["Total"]})
            st.markdown("**Invoices**")
            st.dataframe(
                ir_view, hide_index=True,
                column_config={
                    "invoice_date": st.column_config.DateColumn("Invoice Date"),
                    "due_date": st.column_config.DateColumn("Due Date"),
                    "amount": st.column_config.NumberColumn("Net Amount", format="₱ %.2f"),
                    "running_balance": st.column_config.NumberColumn("Running Balance", format="₱ %.2f"),
                },
            )
            st.download_button("Download invoice rundown CSV", data=ir_view.to_csv(index=False), file_name="invoice_rundown.csv", mime="text/csv", key="dl_inv_rundown")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="inv_rundown_pdf_btn"):
                ir_rows = [[r.supplier, r.invoice, r.batch or "—", str(r.invoice_date)[:10], str(r.due_date)[:10], _pdf_money(r.amount), r.status, _pdf_money(r.running_balance)] for r in ir_view.itertuples()]
                ir_summary = [(n, int((ir_view["status"] == n).sum()), float(ir_view.loc[ir_view["status"] == n, "amount"].sum())) for n in ir_order]
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Invoice rundown", "All suppliers" if ir_choice is None else ir_options[ir_choice],
                        ["Supplier", "Invoice", "Batch", "Invoice Date", "Due Date", "Net Amount", "Status", "Running Balance"], ir_rows,
                        [0.20, 0.09, 0.20, 0.09, 0.09, 0.11, 0.09, 0.13], [5, 7], ir_summary, f"Invoice dates {ir_from} to {ir_to}",
                    ),
                    "invoice_rundown.pdf", key="inv_rundown_pdf_download", label="Invoice Rundown PDF",
                )

    elif report_choice == "Private lender — loan note & check history":
        st.subheader("Private lender — loan note & check history")
        st.caption("Every loan note and every check for one private lender — pick a lender, narrow by date/status, and pull a Statement of Account whenever you're asked for this lender's transaction history.")
        lender_options = creditor_options_all("LENDER")
        if not lender_options:
            st.caption("No private lenders on file yet.")
        else:
            lender_id = st.selectbox("Lender", options=list(lender_options), format_func=lambda cid: lender_options[cid], key="rpt_lender_pick")
            ln_notes, ln_checks = get_lender_history(lender_id)

            ln_date_from, ln_date_to = history_date_range(
                [
                    ln_notes["issue_date"] if not ln_notes.empty else None,
                    ln_checks["check_date"] if not ln_checks.empty else None,
                ],
                key_prefix=f"rpt_len_date_{lender_id}",
            )
            ln_notes_f = filter_by_date(ln_notes, "issue_date", ln_date_from, ln_date_to)
            ln_checks_f = filter_by_date(ln_checks, "check_date", ln_date_from, ln_date_to)
            ln_checks_live = ln_checks_f[ln_checks_f["status"] == "Active"] if not ln_checks_f.empty else ln_checks_f

            m1, m2, m3 = st.columns(3)
            m1.metric("Loan notes", len(ln_notes_f), border=True)
            m2.metric("Outstanding (uncleared)", php(ln_notes_f["outstanding_uncleared"].sum() if not ln_notes_f.empty else 0), border=True)
            m3.metric("Total paid (checks)", php(ln_checks_live["amount"].sum() if not ln_checks_live.empty else 0), border=True, help="Active checks only — excludes cancelled/replaced checks. Reflects the date range above, not the status filters below.")

            st.markdown("**Loan notes**")
            ln_notes_shown = filter_by_status(ln_notes_f, "status", key=f"rpt_len_note_status_{lender_id}")
            if ln_notes_shown.empty:
                st.caption("No loan notes for this period/status.")
            else:
                st.dataframe(
                    ln_notes_shown.drop(columns=["loan_note_id"]), hide_index=True,
                    column_config={
                        "issue_date": st.column_config.DateColumn("Issue Date"),
                        "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                        "total_interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                        "total_payable": st.column_config.NumberColumn("Total Payable", format="₱ %.2f"),
                        "outstanding_uncleared": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                    },
                )

            st.markdown("**Checks issued**")
            ln_checks_shown = filter_by_status(ln_checks_f, "status", key=f"rpt_len_check_status_{lender_id}")
            if ln_checks_shown.empty:
                st.caption("No checks issued for this period/status.")
            else:
                st.dataframe(
                    ln_checks_shown, hide_index=True,
                    column_config={
                        "check_date": st.column_config.DateColumn("Check Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )

            lender_slug = lender_options[lender_id].lower().replace(" ", "_")
            c1, c2 = st.columns(2)
            c1.download_button("Download loan notes CSV", data=ln_notes_shown.to_csv(index=False), file_name=f"lender_{lender_slug}_notes.csv", mime="text/csv", key="dl_len_notes")
            c2.download_button("Download checks CSV", data=ln_checks_shown.to_csv(index=False), file_name=f"lender_{lender_slug}_checks.csv", mime="text/csv", key="dl_len_checks")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            ln_group_by_note = st.checkbox("Break out each loan note separately (instead of one combined statement)", key=f"rpt_len_group_{lender_id}")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="len_pdf_btn"):
                pdf_preview_and_download(
                    generate_lender_statement_pdf(
                        lender_options[lender_id], ln_notes_shown, ln_checks_shown,
                        ln_date_from, ln_date_to, group_by_note=ln_group_by_note,
                    ),
                    f"lender_{lender_slug}_statement.pdf", key="len_pdf_download", label="Lender Statement of Account PDF",
                )

    elif report_choice == "Bank loan — installment history":
        st.subheader("Bank loan — installment history")
        st.caption("Every installment for one bank loan, paid or outstanding — pick a loan, narrow by date/status, and pull a Statement of Account whenever you're asked for this loan's history.")
        bl_options = bank_loan_options()
        if not bl_options:
            st.caption("No bank loans on file yet.")
        else:
            bl_id = st.selectbox("Bank loan", options=list(bl_options), format_func=lambda lid: bl_options[lid], key="rpt_bank_loan_pick")
            bl_info, bl_installments = get_bank_loan_history(bl_id)
            bl_row = bl_info.iloc[0]

            i1, i2, i3, i4 = st.columns(4)
            i1.metric("Bank", bl_row["bank_name"], border=True)
            i2.metric("Principal", php(bl_row["principal_amount"]), border=True)
            i3.metric("Status", bl_row["status"], border=True)
            i4.metric("Maturity", bl_row["maturity_date"], border=True)

            bl_date_from, bl_date_to = history_date_range(
                [bl_installments["due_date"] if not bl_installments.empty else None],
                key_prefix=f"rpt_bl_date_{bl_id}",
            )
            bl_installments_f = filter_by_date(bl_installments, "due_date", bl_date_from, bl_date_to)

            st.markdown("**Installments**")
            bl_installments_shown = filter_by_status(bl_installments_f, "status", key=f"rpt_bl_status_{bl_id}")
            if bl_installments_shown.empty:
                st.caption("No installments for this period/status.")
            else:
                st.dataframe(
                    bl_installments_shown, hide_index=True,
                    column_config={
                        "due_date": st.column_config.DateColumn("Due Date"),
                        "amount": st.column_config.NumberColumn("Amount", format="₱ %.2f"),
                    },
                )
                st.download_button("Download installments CSV", data=bl_installments_shown.to_csv(index=False), file_name=f"bank_loan_{bl_row['loan_ref']}_installments.csv", mime="text/csv", key="dl_bl_installments")

            st.divider()
            st.markdown("**PDF — Statement of Account**")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_pdf_btn"):
                pdf_preview_and_download(
                    generate_bank_loan_statement_pdf(bl_row, bl_installments_shown, bl_date_from, bl_date_to),
                    f"bank_loan_{bl_row['loan_ref']}_statement.pdf", key="bl_pdf_download", label="Bank Loan Statement of Account PDF",
                )

    elif report_choice == "Credit card — statement history":
        st.subheader("Credit card — statement history")
        st.caption("Grouped by whose card it is — one cardholder can have several cards. Pick a cardholder, then drill into one of their cards or combine all of them, narrow by date/status, and pull a Statement of Account whenever you're asked for this card's history.")
        cc_holder_options = cardholder_options_with_cards()
        if not cc_holder_options:
            st.caption("No credit cards on file yet.")
        else:
            cc_holder_id = st.selectbox("Cardholder", options=list(cc_holder_options), format_func=lambda hid: cc_holder_options[hid], key="rpt_card_holder_pick")
            cc_holder_slug = cc_holder_options[cc_holder_id].lower().replace(" ", "_")
            cc_hist_options = credit_card_options_for_holder(cc_holder_id)
            cc_picker_options = ["ALL"] + list(cc_hist_options)
            cc_hist_id = st.selectbox(
                "Credit card", options=cc_picker_options,
                format_func=lambda cid: f"All cards ({len(cc_hist_options)})" if cid == "ALL" else cc_hist_options[cid],
                key=f"rpt_card_pick_{cc_holder_id}",
            )

            if cc_hist_id == "ALL":
                cc_cards_info, cc_statements = get_cardholder_all_cards_history(cc_holder_id)

                cc_date_from, cc_date_to = history_date_range(
                    [cc_statements["due_date"] if not cc_statements.empty else None],
                    key_prefix=f"rpt_cc_all_date_{cc_holder_id}",
                )
                cc_statements_f = filter_by_date(cc_statements, "due_date", cc_date_from, cc_date_to)
                cc_unpaid_live = cc_statements_f[~cc_statements_f["status"].isin(["Paid", "Cancelled"])] if not cc_statements_f.empty else cc_statements_f

                m1, m2, m3 = st.columns(3)
                m1.metric("Cards", len(cc_cards_info), border=True)
                m2.metric("Combined credit limit", php(cc_cards_info["credit_limit"].sum() if not cc_cards_info.empty else 0), border=True)
                m3.metric("Total unpaid (all cards)", php(cc_unpaid_live["total_amount_due"].sum() if not cc_unpaid_live.empty else 0), border=True, help="Excludes Paid/Cancelled statements. Reflects the date range above, not the status filter below.")

                st.markdown("**Cards**")
                if cc_cards_info.empty:
                    st.caption("No cards for this cardholder.")
                else:
                    st.dataframe(
                        cc_cards_info.drop(columns=["card_id"]), hide_index=True,
                        column_config={"credit_limit": st.column_config.NumberColumn("Credit Limit", format="₱ %.2f")},
                    )

                st.markdown("**Statements (all cards)**")
                cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_all_status_{cc_holder_id}")
                if cc_statements_shown.empty:
                    st.caption("No statements for this period/status.")
                else:
                    st.dataframe(
                        cc_statements_shown, hide_index=True,
                        column_config={
                            "card_name": "Card",
                            "card_number_last4": "Last 4",
                            "due_date": st.column_config.DateColumn("Due Date"),
                            "total_amount_due": st.column_config.NumberColumn("Amount Due", format="₱ %.2f"),
                            "payment_date": st.column_config.DateColumn("Payment Date"),
                        },
                    )
                    st.download_button("Download statements CSV", data=cc_statements_shown.to_csv(index=False), file_name=f"cardholder_{cc_holder_slug}_all_cards_statements.csv", mime="text/csv", key="dl_cc_all_statements")

                st.divider()
                st.markdown("**PDF — Statement of Account**")
                cc_group_by_card = st.checkbox("Break out each card separately (instead of one combined statement)", key=f"rpt_cc_group_{cc_holder_id}")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_all_pdf_btn"):
                    pdf_preview_and_download(
                        generate_cardholder_statement_pdf(
                            cc_holder_options[cc_holder_id], cc_cards_info, cc_statements_shown,
                            cc_date_from, cc_date_to, group_by_card=cc_group_by_card,
                        ),
                        f"cardholder_{cc_holder_slug}_all_cards_statement.pdf", key="cc_all_pdf_download", label="Cardholder Statement of Account PDF",
                    )
            else:
                cc_info, cc_statements = get_credit_card_history(cc_hist_id)
                cc_row = cc_info.iloc[0]

                i1, i2, i3 = st.columns(3)
                i1.metric("Issuer", cc_row["issuer"], border=True)
                i2.metric("Credit limit", php(cc_row["credit_limit"]), border=True)
                i3.metric("Cardholder", cc_row["cardholder"] or "—", border=True)

                cc_date_from, cc_date_to = history_date_range(
                    [cc_statements["due_date"] if not cc_statements.empty else None],
                    key_prefix=f"rpt_cc_date_{cc_hist_id}",
                )
                cc_statements_f = filter_by_date(cc_statements, "due_date", cc_date_from, cc_date_to)

                st.markdown("**Statements**")
                cc_statements_shown = filter_by_status(cc_statements_f, "status", key=f"rpt_cc_status_{cc_hist_id}")
                if cc_statements_shown.empty:
                    st.caption("No statements for this period/status.")
                else:
                    st.dataframe(
                        cc_statements_shown, hide_index=True,
                        column_config={
                            "due_date": st.column_config.DateColumn("Due Date"),
                            "total_amount_due": st.column_config.NumberColumn("Amount Due", format="₱ %.2f"),
                            "payment_date": st.column_config.DateColumn("Payment Date"),
                        },
                    )
                    st.download_button("Download statements CSV", data=cc_statements_shown.to_csv(index=False), file_name=f"card_{cc_row['card_name']}_{cc_row['card_number_last4']}_statements.csv", mime="text/csv", key="dl_cc_statements")

                st.divider()
                st.markdown("**PDF — Statement of Account**")
                if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="cc_pdf_btn"):
                    pdf_preview_and_download(
                        generate_credit_card_statement_pdf(cc_row, cc_statements_shown, cc_date_from, cc_date_to),
                        f"card_{cc_row['card_name']}_{cc_row['card_number_last4']}_statement.pdf", key="cc_pdf_download", label="Credit Card Statement of Account PDF",
                    )

    elif report_choice == "Master rollup — supplier AP aging":
        st.subheader("Master rollup — supplier AP aging")
        st.caption("Every supplier's open invoices, bucketed by how overdue they are, across the whole company.")
        aging_df = get_supplier_ap_aging_report()
        if aging_df.empty:
            st.caption("No open supplier invoices.")
        else:
            st.dataframe(
                aging_df, hide_index=True,
                column_config={
                    "total_unpaid": st.column_config.NumberColumn("Total Unpaid", format="₱ %.2f"),
                    "current_due": st.column_config.NumberColumn("Current", format="₱ %.2f"),
                    "days_1_30": st.column_config.NumberColumn("1-30 Days", format="₱ %.2f"),
                    "days_31_60": st.column_config.NumberColumn("31-60 Days", format="₱ %.2f"),
                    "days_over_60": st.column_config.NumberColumn("60+ Days", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=aging_df.to_csv(index=False), file_name="supplier_ap_aging.csv", mime="text/csv", key="dl_aging")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="aging_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Supplier AP aging", "All suppliers",
                        ["Supplier", "Open", "Total Unpaid", "Current", "1-30 Days", "31-60 Days", "60+ Days"],
                        [[r.supplier_name, int(r.open_invoices), _pdf_money(r.total_unpaid), _pdf_money(r.current_due), _pdf_money(r.days_1_30), _pdf_money(r.days_31_60), _pdf_money(r.days_over_60)] for r in aging_df.itertuples()],
                        [0.32, 0.07, 0.14, 0.12, 0.12, 0.12, 0.11], [1, 2, 3, 4, 5, 6], [], f"As of {date.today()}",
                    ),
                    "supplier_ap_aging.pdf", key="aging_pdf_download", label="Supplier AP Aging PDF",
                )

    elif report_choice == "Master rollup — private lender exposure":
        st.subheader("Master rollup — private lender exposure")
        st.caption("Every private lender's loan notes and current uncleared exposure, across the whole company.")
        lender_master_df = get_private_lender_master_report()
        if lender_master_df.empty:
            st.caption("No private lender notes on file.")
        else:
            st.dataframe(
                lender_master_df, hide_index=True,
                column_config={
                    "issue_date": st.column_config.DateColumn("Issue Date"),
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "total_interest": st.column_config.NumberColumn("Interest", format="₱ %.2f"),
                    "total_payable": st.column_config.NumberColumn("Total Payable", format="₱ %.2f"),
                    "outstanding_uncleared": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=lender_master_df.to_csv(index=False), file_name="private_lender_exposure.csv", mime="text/csv", key="dl_lender_master")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="lender_master_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Private lender exposure", "All private lenders",
                        ["Lender", "Note", "Issue Date", "Principal", "Interest", "Total Payable", "Outstanding", "Status"],
                        [[r.lender_name, r.note_ref, str(r.issue_date)[:10], _pdf_money(r.principal_amount), _pdf_money(r.total_interest), _pdf_money(r.total_payable), _pdf_money(r.outstanding_uncleared), r.status] for r in lender_master_df.itertuples()],
                        [0.2, 0.13, 0.09, 0.11, 0.1, 0.12, 0.11, 0.14], [3, 4, 5, 6], [], f"As of {date.today()}",
                    ),
                    "private_lender_exposure.pdf", key="lender_master_pdf_download", label="Private Lender Exposure PDF",
                )

    elif report_choice == "Master rollup — bank loan servicing":
        st.subheader("Master rollup — bank loan servicing")
        st.caption("Every bank loan and its current outstanding balance, across the whole company.")
        bl_master_df = get_bank_loan_servicing_report()
        if bl_master_df.empty:
            st.caption("No bank loans on file.")
        else:
            st.dataframe(
                bl_master_df, hide_index=True,
                column_config={
                    "start_date": st.column_config.DateColumn("Start Date"),
                    "maturity_date": st.column_config.DateColumn("Maturity Date"),
                    "principal_amount": st.column_config.NumberColumn("Principal", format="₱ %.2f"),
                    "outstanding": st.column_config.NumberColumn("Outstanding", format="₱ %.2f"),
                },
            )
            st.download_button("Download CSV", data=bl_master_df.to_csv(index=False), file_name="bank_loan_servicing.csv", mime="text/csv", key="dl_bl_master")
            if st.button("Generate PDF", icon=":material/picture_as_pdf:", key="bl_master_pdf_btn"):
                pdf_preview_and_download(
                    generate_rundown_pdf(
                        "Bank loan servicing", "All bank loans",
                        ["Bank", "Loan Ref", "Start", "Maturity", "Principal", "Outstanding", "Status"],
                        [[r.bank_name, r.loan_ref, str(r.start_date or "—")[:10], str(r.maturity_date or "—")[:10], _pdf_money(r.principal_amount) if pd.notna(r.principal_amount) else "—", _pdf_money(r.outstanding), r.status] for r in bl_master_df.itertuples()],
                        [0.16, 0.32, 0.09, 0.09, 0.12, 0.12, 0.10], [4, 5], [], f"As of {date.today()}",
                    ),
                    "bank_loan_servicing.pdf", key="bl_master_pdf_download", label="Bank Loan Servicing PDF",
                )

# --------------------------------------------------------------------------
# Tab 8: Admin & audit
# --------------------------------------------------------------------------
with tab_admin:
    st.header("Admin & audit")

    with st.container(border=True):
        st.subheader("Metrobank Account 1")
        if not bank_df.empty:
            b1, b2 = st.columns(2)
            b1.metric("Bank", bank["bank_name"], border=True)
            b2.metric("Branch code", bank["branch_code"], border=True)

            with st.expander("Check print calibration"):
                st.caption(
                    "No physical check leaf has been measured yet, so these start as a guess. "
                    "Print a test check, measure the drift with a ruler, and enter the correction "
                    "here — mirrors Chrysanth Check Writer's own per-account Alignment tab."
                )
                cal_w, cal_h = st.columns(2)
                new_width_mm = cal_w.number_input("Check width (mm)", min_value=1.0, value=float(bank["check_width_mm"]), step=1.0, key="cal_width_mm")
                new_height_mm = cal_h.number_input("Check height (mm)", min_value=1.0, value=float(bank["check_height_mm"]), step=1.0, key="cal_height_mm")
                cal_x, cal_y = st.columns(2)
                new_offset_x_mm = cal_x.number_input("Shift right (mm)", value=float(bank["check_offset_x_mm"]), step=0.5, key="cal_offset_x_mm")
                new_offset_y_mm = cal_y.number_input("Shift down (mm)", value=float(bank["check_offset_y_mm"]), step=0.5, key="cal_offset_y_mm")
                if st.button("Save calibration", icon=":material/tune:", key="cal_save_btn"):
                    with get_conn() as conn:
                        conn.execute(
                            """UPDATE bank_accounts SET check_width_mm = ?, check_height_mm = ?,
                               check_offset_x_mm = ?, check_offset_y_mm = ? WHERE bank_account_id = ?""",
                            (new_width_mm, new_height_mm, new_offset_x_mm, new_offset_y_mm, PRIMARY_BANK_ACCOUNT_ID),
                        )
                        conn.commit()
                    st.success("Calibration saved.")
                    refresh()

    with st.container(border=True):
        st.subheader("Check booklets")
        st.caption(
            "Multi-booklet management (Phase 6 Redux v2) — register each physical booklet's leaf "
            "range (e.g. Booklet A: 1000-1050). Its \"Assign a check number\" section in Order "
            "Statement/Lenders reads a booklet's still-available numbers from here for a dropdown "
            "you pick from manually — no auto-sequencing."
        )
        booklets_df = q("SELECT * FROM check_booklets ORDER BY booklet_id DESC")
        if booklets_df.empty:
            st.info("No checkbooks registered yet — every check on file so far was issued/migrated without one. \"Assign a check number\" won't have anything to offer, and the gap/duplicate audit below has nothing to check against, until at least one is registered here.")
        st.dataframe(booklets_df, hide_index=True)

        with st.expander("Register new booklet"):
            with st.form("new_booklet_form", border=False):
                bk_code = st.text_input("Booklet code", value="Booklet A")
                bk1, bk2 = st.columns(2)
                bk_start = bk1.number_input("Start check number", min_value=1, step=1, value=1000, help="The full number printed on the first leaf, e.g. 3992554301. It is used exactly as typed.")
                bk_end = bk2.number_input("End check number", min_value=1, step=1, value=1050, help="The full number printed on the last leaf, e.g. 3992554400.")
                bk_received = st.date_input("Date received", value=date.today())
                if st.form_submit_button("Register booklet", icon=":material/menu_book:"):
                    if bk_end < bk_start:
                        st.error("End check number must be greater than or equal to start check number.")
                    elif guard_duplicate_submit("create_booklet", bk_code, bk_start, bk_end, bk_received):
                        ok, msg = create_booklet(PRIMARY_BANK_ACCOUNT_ID, bk_code, int(bk_start), int(bk_end), bk_received)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh()
                    else:
                        st.warning("Duplicate submission ignored — identical to the click just before it.")

        with st.expander("Archive a booklet"):
            archivable_df = booklets_df[booklets_df["status"] != "Archived"] if not booklets_df.empty else booklets_df
            if archivable_df.empty:
                st.caption("No booklet available to archive.")
            else:
                arch_options = {int(r["booklet_id"]): (r["booklet_code"] or f"Booklet #{int(r['booklet_id'])}") + f" [{r['status']}]" for _, r in archivable_df.iterrows()}
                arch_pick = st.selectbox("Booklet", options=list(arch_options.keys()), format_func=lambda bid: arch_options[bid], key="archive_booklet_pick")
                if st.button("Archive this booklet", icon=":material/archive:", key="archive_booklet_btn"):
                    ok, msg = archive_booklet(int(arch_pick))
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()

        with st.expander("Booklet gap audit"):
            st.caption("Every assignment is manual (Phase 6 Redux v2) — a normal one shouldn't leave a gap, since the dropdown only ever offers open leaves. A real gap here usually means a leaf was assigned outside this app (pre-Redux historical data), or a spoiled leaf logged after the fact.")
            if booklets_df.empty:
                st.caption("No booklets registered.")
            else:
                audit_pick = st.selectbox(
                    "Booklet",
                    options=booklets_df["booklet_id"],
                    format_func=lambda bid: booklets_df.set_index("booklet_id").loc[bid, "booklet_code"] or f"Booklet #{bid}",
                    key="audit_pick",
                )
                if st.button("Run gap audit", icon=":material/search:"):
                    result = audit_booklet_gaps(int(audit_pick))
                    a1, a2, a3, a4 = st.columns(4)
                    a1.metric("Total leaves", result["total_leaves"], border=True)
                    a2.metric("Missing gaps", result["missing_gaps_count"], border=True)
                    a3.metric("Duplicates", result["duplicate_numbers_count"], border=True)
                    a4.metric("Booklet", result["booklet_code"], border=True)
                    if result["missing_gaps_count"] > 0:
                        st.error(f"Unaccounted (skipped) check numbers: {', '.join(result['missing_gaps_list'])}")
                    if result["duplicate_numbers_count"] > 0:
                        st.warning(f"Same leaf recorded under conflicting text (e.g. \"1\" vs \"01\"): {', '.join(result['duplicate_numbers_list'])} — see the breakdown below for details.")
                    st.dataframe(result["detailed_breakdown"], hide_index=True)

    with st.container(border=True):
        st.subheader("Database backup & restore")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Backup**")
            if st.button("Generate backup", icon=":material/backup:"):
                filename, backup_bytes = export_database_backup()
                st.session_state["backup_filename"] = filename
                st.session_state["backup_bytes"] = backup_bytes
            if "backup_bytes" in st.session_state:
                st.download_button(
                    "Download backup",
                    data=st.session_state["backup_bytes"],
                    file_name=st.session_state["backup_filename"],
                    mime="application/octet-stream",
                )
        with c2:
            st.markdown("**Restore**")
            st.caption("This overwrites the live database with the uploaded file. The current database is saved as a .bak_before_restore file first, in case this needs undoing.")
            restore_file = st.file_uploader("Upload backup file (.db)", type=["db"])
            confirm_text = st.text_input("Type RESTORE to confirm")
            if st.button("Restore database", icon=":material/restore:", type="secondary"):
                if restore_file is None:
                    st.error("Upload a backup file first.")
                elif confirm_text != "RESTORE":
                    st.error("Type RESTORE in the confirmation box to proceed.")
                else:
                    ok, msg = restore_database_from_bytes(restore_file.getvalue())
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh()

    with st.container(border=True):
        st.subheader("Audit log")
        audit_full = fix_nullable_int(q("SELECT log_id, user_action, entity_name, entity_id, old_values, new_values, datetime(timestamp, 'localtime') AS timestamp FROM audit_logs ORDER BY log_id DESC"), "entity_id")
        action_filter = st.multiselect("Filter by action", options=sorted(audit_full["user_action"].unique()) if not audit_full.empty else [])
        display_audit = audit_full[audit_full["user_action"].isin(action_filter)] if action_filter else audit_full
        st.dataframe(display_audit, hide_index=True)

    with st.container(border=True):
        st.subheader("Changelog")
        st.caption("Everything that's shipped, in plain language, newest first. The same source the \"What's new\" popup reads from.")
        st.markdown(_read_whats_new(), unsafe_allow_html=False)

# Guardrail (2026-09-27): reminder for checks typed but never parked -- in the sidebar, and a browser prompt if the page is
# about to be closed/refreshed while any are pending.
_pending_drafts = list_park_drafts()
if _pending_drafts:
    park_draft_slot.warning(
        ":material/edit_note: **Checks typed but not parked yet**" + chr(10) + chr(10)
        + chr(10).join(f"- {d['label']} — {d['n']} check(s), {php(d['total'])}" for d in _pending_drafts)
        + chr(10) + chr(10) + "Open the batch (Order Statement, Checks) and click **Park Metrobank checks**."
    )
else:
    park_draft_slot.empty()
components.html(
    "<script>try{window.parent.onbeforeunload=" + ("function(e){e.preventDefault();e.returnValue='';return '';}" if _pending_drafts else "null") + ";}catch(e){}</script>",
    height=0,
)
