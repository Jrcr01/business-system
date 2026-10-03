"""AP Disbursement System — Streamlit UI.

Primary Bank Account: Metrobank Account 1 (Branch Code 399).
"""
import ast
import base64
import calendar
import hashlib
import io
import json
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
from engines.batch_statement_service import generate_batch_statement_pdf, generate_batch_statement_thermal_pdf, bulk_summary_rows, generate_bulk_summary_pdf
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
    generate_metrobank_check_pdf,
)
from engines.cc_service import get_statement_gap_alerts, get_annual_fee_alerts, get_statement_coverage
from engines.checkbook_service import audit_booklet_gaps, get_checkbook_alerts
from engines.last_modified_sql import statement_modified_sql, loan_note_modified_sql, bank_loan_modified_sql
from engines.global_search_service import search_everything
from engines.payment_run_service import get_run_stages, waiting_count
from engines.park_draft_service import save_park_draft, load_park_draft, clear_park_draft, list_park_drafts
from engines.check_run_service import (
    get_pending_check_run,
    get_unnumbered_checks,
    mark_checks_printed,
    generate_check_run_pdf,
    generate_check_run_summary_pdf,
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
    attach_calendar_totals,
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
# Business rules live in engines/*_rules.py and engines/common.py (moved out of this file 2026-09-27); imported back so every
# name still works here and as app.<name>.
from engines.common import (  # noqa: F401
    DB_PATH,
    PRIMARY_BANK_ACCOUNT_ID,
    _as_date,
    _pdf_money,
    cell_text,
    filter_by_date,
    fix_nullable_int,
    get_conn,
    has_value,
    php,
    prospective_year,
    q_live,
    remove_blocked_date,
    set_blocked_date,
)
from engines.creditor_rules import (  # noqa: F401
    CREDITOR_REFERENCE_TABLES,
    add_creditor,
    creditor_reference_counts,
    deactivate_creditor,
    delete_or_deactivate_creditor,
    get_or_create_card_issuer,
    reactivate_creditor,
    update_creditor,
)
from engines.statement_rules import (  # noqa: F401
    DEFAULT_SUPPLIER_TERMS,
    _batch_lock_status,
    add_credit_memo,
    add_invoice,
    add_terms_bank_rule,
    confirm_credit_memos,
    create_order_statement,
    delete_credit_memo,
    delete_invoice,
    edit_credit_memo,
    edit_invoice,
    get_batch_balance,
    get_batch_memo_status,
    get_invoices_with_remaining,
    lock_statement,
    new_draft_ref,
    parse_less_pct,
    parse_terms_days,
    recompute_statement_total,
    resolve_due_date,
    split_remaining_evenly,
    sync_invoice_payment_status,
    unlock_statement,
    waterfall_allocate_preview,
)
from engines.check_rules import (  # noqa: F401
    _booklet_row_dict,
    _check_number_key,
    _check_number_variants,
    _is_check_number_available,
    allocate_check_to_invoices,
    apply_check_correction,
    archive_booklet,
    assign_check_number,
    assign_check_number_from_booklet,
    assign_check_number_manual,
    assign_check_numbers_from_booklet,
    assign_check_numbers_manual,
    build_dual_copy_check_pdf,
    cancel_check,
    cancel_order_statement,
    create_booklet,
    edit_parked_check,
    get_available_booklet_numbers,
    get_booklets,
    get_check_print_calibration,
    get_existing_check_dates_for_creditor,
    get_latest_check_for_creditor,
    issue_check,
    issue_checks_grid,
    log_spoiled_check,
    pick_number_source,
    plan_sequential_numbers,
    print_check_pdf,
    replace_check,
    retarget_invoice_allocations_for_check,
    retract_check_number,
    spoiled_check_problems,
    unlink_invoice_allocations_for_check,
    unpark_checks,
)
from engines.loan_note_rules import (  # noqa: F401
    _LOAN_NOTE_STATUSES,
    cancel_loan_note,
    create_loan_note,
    delete_loan_note,
    promote_draft_loan_note,
    update_loan_note_status,
)
from engines.card_rules import (  # noqa: F401
    _is_latest_cc_statement,
    add_cardholder,
    add_cc_statement,
    add_credit_card,
    confirm_cc_payment_from_next_statement,
    deactivate_cardholder,
    deactivate_credit_card,
    delete_cc_statement,
    edit_cardholder,
    edit_cc_statement,
    edit_credit_card,
    get_default_statement_month,
    get_last_cc_statement_unpaid_balance,
    get_unconfirmed_cc_statement,
    reactivate_cardholder,
    reactivate_credit_card,
)
# Screens live in views/ (one file per main tab) and the shared screen helpers in views/shared.py (moved out of this file
# 2026-09-27); imported back so every name still works here and as app.<name>.
import views.add_data
import views.admin
import views.analytics
import views.release
import views.roles_controls
import views.bank_cash
import views.planner
import views.bank_loans
import views.checks
import views.credit_cards
from views.credit_cards import _card_label  # noqa: F401 -- app._card_label, used by tests
import views.creditor_master
import views.data_health
import views.settings
from engines.settings_registry import label as _hotkey_label, shortcuts as _hotkeys
import views.lenders
import views.order_statement
import views.home
import views.payables_due
import views.payment_run
import views.price_lists
import views.purchase_orders
import views.reports
from views.shared import (  # noqa: F401
    BL_BROWSE,
    BL_NEW,
    LN_BROWSE,
    LN_NEW,
    MONEY_FORMAT,
    OS_BROWSE,
    OS_NEW,
    PAGES,
    PRINT_CHECKS_IN_APP,
    PdfReader,
    PdfWriter,
    STATUS_STYLE,
    SUPPLIER_TERMS_OPTIONS,
    WHATS_NEW_PATH,
    _get_app_start_time,
    _jump,
    _read_whats_new,
    _read_whats_new_latest_section,
    _render_retract_number_section,
    _whats_new_popup_tracker,
    bank_loan_options,
    batch_scoped_key,
    cardholder_options_with_cards,
    confirm_cancel_bank_loan_dialog,
    confirm_cancel_dialog,
    confirm_cancel_loan_note_dialog,
    confirm_create_dialog,
    confirm_delete_loan_note_dialog,
    confirm_unlock_dialog,
    credit_card_options,
    credit_card_options_for_holder,
    creditor_options,
    creditor_options_all,
    date_col,
    date_preset_filter,
    default_index,
    dup_needs_confirm,
    dup_note,
    dup_resume,
    duplicate_warning_dialog,
    filter_by_status,
    flash,
    get_blocked_dates_for_month,
    global_search_dialog,
    go_to,
    guard_duplicate_submit,
    history_date_range,
    mode_label,
    money_col,
    page_header,
    pdf_preview_and_download,
    q,
    refresh,
    render_assign_number_section,
    render_check_print_section,
    section,
    selectbox_creditor,
    selectbox_creditor_label,
    show_flashes,
    status_badge,
    status_text,
)
from engines.clock import manila_today


# --------------------------------------------------------------------------
# Page setup
# --------------------------------------------------------------------------

# Sandbox vs live indicator (2026-09-17): both instances run the exact same code, so with nothing
# else distinguishing them, the only visible difference used to be the port number in the URL --
# an easy mistake to make with two tabs open. Detected from AP_DB_PATH itself rather than a
# separate flag, so it can never drift out of sync with which database is actually behind this
# instance.
IS_SANDBOX = "TEST" in os.path.basename(DB_PATH).upper()
_PAGE_TITLE = ("🧪 SANDBOX — " if IS_SANDBOX else "") + "Slick's Tire Supply — AP Disbursement"

# No logo on screen (2026-09-27, per user: "remove Slick's logo ... everywhere in the app"); printed PDFs keep it.
st.set_page_config(page_title=_PAGE_TITLE, page_icon=":material/request_quote:", layout="wide", initial_sidebar_state="collapsed")

# Sign-in (B8): nothing below runs until someone is signed in; every change this run is recorded under their email, and the menu and sidebar are cut to their role.
from engines.permissions import allowed_pages, filter_menu, may_search
from views.login import gate, render_account_box

_user = gate()
_role = _user["role"]

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

_whats_new_tracker = _whats_new_popup_tracker()
# st.runtime.exists() is False during a bare `import app` (pytest's conftest.py, or any other
# script-mode import) -- st.dialog needs a real, running Streamlit server behind it.
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

show_flashes()      # "Saved ..." toasts queued by refresh(done=...) before the last redraw

# --------------------------------------------------------------------------
# Sidebar menu (2026-09-27, per user: a grouped menu instead of 10 tabs across the top). Each page is one views/<x>.render();
# only the open page runs. The id is the page's web address (localhost:8501/<id>) and what jumps / the search use.
# --------------------------------------------------------------------------
@st.cache_data(ttl="15s")
def _run_waiting() -> int:
    """How many things wait in the payment cycle -- shown beside Payment run in the menu."""
    return waiting_count(get_run_stages(light=True))


_n_waiting = _run_waiting()
PAGES.clear()
PAGES.update({
    "payment-run": st.Page(views.payment_run.render, title="Payment run" + (f" · {_n_waiting}" if _n_waiting else ""), icon=":material/checklist:",
                           url_path="payment-run"),
    "home": st.Page(views.home.render, title="Home", icon=":material/home:", url_path="home", default=True),
    "add-data": st.Page(views.add_data.render, title="Add data", icon=":material/add_box:", url_path="add-data"),
    "order-statements": st.Page(views.order_statement.render, title="Order statements", icon=":material/description:", url_path="order-statements"),
    "checks": st.Page(views.checks.render, title="Checks and checkbook", icon=":material/receipt_long:", url_path="checks"),
    "release": st.Page(views.release.render, title="Release checks", icon=":material/outbox:", url_path="release"),
    "controls": st.Page(views.roles_controls.render, title="Roles and controls", icon=":material/rule:", url_path="controls"),
    "bank-cash": st.Page(views.bank_cash.render, title="Bank and cash", icon=":material/account_balance_wallet:", url_path="bank-cash"),
    "planner": st.Page(views.planner.render, title="Check-date planner", icon=":material/event_available:", url_path="planner"),
    "payables-due": st.Page(views.payables_due.render, title="Payables due", icon=":material/event_upcoming:", url_path="payables-due"),
    "lenders": st.Page(views.lenders.render, title="Lenders", icon=":material/handshake:", url_path="lenders"),
    "bank-loans": st.Page(views.bank_loans.render, title="Bank loans", icon=":material/account_balance:", url_path="bank-loans"),
    "credit-cards": st.Page(views.credit_cards.render, title="Credit cards", icon=":material/credit_card:", url_path="credit-cards"),
    "creditors": st.Page(views.creditor_master.render, title="Creditors", icon=":material/contacts:", url_path="creditors"),
    "reports": st.Page(views.reports.render, title="Reports", icon=":material/summarize:", url_path="reports"),
    "admin": st.Page(views.admin.render, title="Admin and audit", icon=":material/admin_panel_settings:", url_path="admin"),
    "settings": st.Page(views.settings.render, title="Settings", icon=":material/tune:", url_path="settings"),
    "data-health": st.Page(views.data_health.render, title="Data health", icon=":material/health_and_safety:", url_path="data-health"),
    "price-lists": st.Page(views.price_lists.render, title="Price lists", icon=":material/sell:", url_path="price-lists"),
    "purchase-orders": st.Page(views.purchase_orders.render, title="Purchase orders", icon=":material/shopping_cart:", url_path="purchase-orders"),
})
from engines.settings_registry import forecast_shown as _forecast_shown   # the cash forecast and planner are hidden until the owner switches them on (Settings)
if not _forecast_shown():
    PAGES.pop("planner", None)
MENU = {                                                      # in the order the money moves (2026-10-03); the top bar shows these as drop-downs
    "Home": ["home"],
    "Buy": ["price-lists", "purchase-orders", ("Receive goods", ":material/inventory_2:", "purchase-orders", {"po_mode": "Receive"}, "po.receive_goods")],
    "Pay suppliers": [("Match invoices", ":material/receipt_long:", "purchase-orders", {"po_mode": "Match invoice"}, "po.receive"),
                      "order-statements", "payment-run", "checks", "release", "payables-due", "add-data"],
    "Bank": ["bank-cash"] + (["planner"] if "planner" in PAGES else []),
    "Other payables": ["lenders", "bank-loans", "credit-cards"],
    "Records": ["reports"],
    "Set-up": ["creditors", "controls", "settings", "admin", "data-health"],
}
from engines.permissions import can as _can
_PLAIN_MENU = filter_menu({sec: [e for e in ids if not isinstance(e, tuple)] for sec, ids in MENU.items()}, _role)       # a role sees only its own pages
for _hidden in set(PAGES) - allowed_pages(_role, set(PAGES)):
    PAGES.pop(_hidden)
TOPBAR_MENU = {}
for _sec, _ids in MENU.items():                                # the top bar's drop-downs: the pages, plus the shortcuts (Receive goods ...) this role may use
    _keep = [e for e in _ids if (not isinstance(e, tuple) and e in _PLAIN_MENU.get(_sec, [])) or (isinstance(e, tuple) and (e[4] is None or _can(_role, e[4])) and e[2] in PAGES)]
    if _keep:
        TOPBAR_MENU[_sec] = _keep
page = st.navigation({section: [PAGES[pid] for pid in ids] for section, ids in _PLAIN_MENU.items()}, position="hidden")      # the top bar is the menu now; no sidebar

# A jump requested by the search pop-up or a page button (set, then st.rerun()): apply it before any page draws.
_pending_global_jump = st.session_state.pop("_global_jump", None)
if _pending_global_jump:
    _jump(_pending_global_jump["page"], **_pending_global_jump["state"])


_pending_drafts = list_park_drafts() if may_search(_role) else []      # checks typed but never parked: the bell in the top bar, and a browser prompt when the page is closed
_hk = _hotkeys()
_hk_pages, _hk_search, _hk_park, _hk_new = (_hk["pages"]["mod"].replace("+", " + ") + " +", _hotkey_label(_hk["search"]), _hotkey_label(_hk["park"]), _hotkey_label(_hk["new"]))
_SHORTCUTS = (
    "| Keys | What it does |" + chr(10) + "|---|---|" + chr(10)
    + "| **Enter** | In a confirmation pop-up: **Yes** (unless a button is focused) |" + chr(10)
    + "| **Esc** | In a pop-up: go back |" + chr(10)
    + "| **" + _hk_pages + " 1 … 9, 0** | Open a page (1 = Payment run, 2 = Add data, 3 = Order statements, 4 = Checks, 5 = Bank and cash, 6 = Payables due, 7 = Lenders, 8 = Bank loans, 9 = Credit cards, 0 = Reports), **H** = Home |" + chr(10)
    + "| **" + _hk_search + "** | Open **Search everything** (checks, invoices, batches, loans, creditors, cards…) |" + chr(10)
    + "| **" + _hk_park + "** | Press the screen's main **P**ark / Save / Add button (only if there is one) |" + chr(10)
    + "| **" + _hk_new + "** | Open the **N**ew … form (New Batch, New Loan Note, New Bank Loan) |" + chr(10)
    + "| **Tab / Shift+Tab** | Next / previous box or button |" + chr(10)
    + "| **Enter / Space** | Press the focused button |" + chr(10)
    + "| **↑ ↓** | Move through a dropdown (or just type to search it) |"
)
import views.topbar
park_draft_slot = st.empty() if not st.runtime.exists() else views.topbar.render(TOPBAR_MENU, PAGES, _user, _role, IS_SANDBOX, _pending_drafts, {"search": may_search(_role), "calendar": may_search(_role), "shortcuts": _SHORTCUTS})

st.markdown("<style>.st-key-global_search_open_main{position:absolute;width:0;height:0;overflow:hidden;opacity:0;}</style>", unsafe_allow_html=True)
st.markdown("""<style>
[data-testid="stMainBlockContainer"] { padding-top: 1.4rem; }            /* Streamlit leaves ~6rem empty above every page */
[data-testid="stMainBlockContainer"] h2 { color: #0B4F8A; }               /* page titles in Slick's blue, like Home */
[data-testid="stMainBlockContainer"] h3 { color: #1f2937; }
button[kind="primary"] { background-color: #0B4F8A; border-color: #0B4F8A; }
button[kind="primary"]:hover { background-color: #0a2c4d; border-color: #0a2c4d; }
[data-testid="stMetric"] { border-top: 3px solid #F3E03B; }               /* the yellow accent of the shop sign on the figure cards */
</style>""", unsafe_allow_html=True)
if may_search(_role) and st.button("Search everything", key="global_search_open_main"):
    global_search_dialog()

page.run()          # only the open page runs; the others cost nothing


# Guardrail (2026-09-27): reminder for checks typed but never parked -- in the sidebar, and a browser prompt if the page is
# about to be closed/refreshed while any are pending.
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

# Keyboard shortcuts (2026-09-27, per user: less trackpad/mouse). Streamlit has no hotkey setting, so a small script on the page listens for keys and clicks
# the matching control. It replaces its own listener on every run, so it never doubles up. The list is shown in the sidebar (Keyboard shortcuts).
components.html(r"""<script>
(function () {
  var CFG = __HOTKEY_CONFIG__;
  function mods(m) { return { alt: m.indexOf('Alt') >= 0, ctrl: m.indexOf('Ctrl') >= 0, shift: m.indexOf('Shift') >= 0 }; }
  function pressed(e, sc) {
    var m = mods(sc.mod), k = sc.key;
    if (e.altKey !== m.alt || e.ctrlKey !== m.ctrl || e.shiftKey !== m.shift) return false;
    if (/^[A-Za-z]$/.test(k)) return e.code === 'Key' + k.toUpperCase();
    if (/^[0-9]$/.test(k)) return e.code === 'Digit' + k;
    return e.key === k;
  }
  var P = window.parent, D = P.document;
  if (P.__apHotkeys) D.removeEventListener('keydown', P.__apHotkeys, true);
  function visible(el) { return el && el.offsetParent !== null; }
  function isTyping(t) { if (!t) return false; var tag = (t.tagName || '').toLowerCase(); return tag === 'input' || tag === 'textarea' || t.isContentEditable; }
  function buttons(root) { return Array.from(root.querySelectorAll('button')).filter(function (b) { return !b.disabled && visible(b); }); }
  function flash(msg) {
    var old = D.getElementById('ap-hotkey-flash'); if (old) old.remove();
    var d = D.createElement('div'); d.id = 'ap-hotkey-flash'; d.textContent = msg;
    d.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#1f3a5f;color:#fff;padding:10px 18px;border-radius:8px;z-index:99999;font-size:14px;';
    D.body.appendChild(d); setTimeout(function () { d.remove(); }, 2600);
  }
  function pageLink(p) { return Array.from(D.querySelectorAll('.st-key-topbar a')).find(function (a) { var h = (a.getAttribute('href') || '').replace(/\/$/, ''); return h === '/' + p || h.slice(-(p.length + 1)) === '/' + p; }); }
  function allMenuLinks() { return [pageLink('home')]; }                                  // Home
  function menuLinks() { return CFG.order.map(pageLink); }                                // Alt+1 ... Alt+0: a fixed list of pages, whatever the menu order (undefined = not open to this role)
  function searchBox() { return Array.from(D.querySelectorAll('input')).find(function (i) { return (i.placeholder || '').indexOf('Check #') === 0; }); }
  var handler = function (e) {
    var dlg = D.querySelector('[role=dialog]');
    if (dlg) {
      if (e.key === 'Enter' && !e.shiftKey && !e.altKey && !e.ctrlKey) {
        var a = D.activeElement;
        if (a && a.tagName === 'BUTTON') return;                      // a focused button keeps its own behaviour
        var yes = buttons(dlg).find(function (b) { return /^\s*(\S+\s+)?yes/i.test(b.innerText || ''); });
        if (yes) { yes.click(); e.preventDefault(); }
      } else if (e.key === 'Escape') {
        var back = buttons(dlg).find(function (b) { return /go back|no, go back/i.test(b.innerText || ''); });
        if (back) { back.click(); e.preventDefault(); }
      }
      return;
    }
    if (mods(CFG.pages.mod).alt === e.altKey && mods(CFG.pages.mod).ctrl === e.ctrlKey && mods(CFG.pages.mod).shift === e.shiftKey && e.code === 'KeyH') {
      var home = allMenuLinks()[0];
      if (home) { home.click(); e.preventDefault(); }
      return;
    }
    if (mods(CFG.pages.mod).alt === e.altKey && mods(CFG.pages.mod).ctrl === e.ctrlKey && mods(CFG.pages.mod).shift === e.shiftKey && /^Digit[0-9]$/.test(e.code)) {
      var n = e.code === 'Digit0' ? 9 : parseInt(e.code.slice(5), 10) - 1;
      var links = menuLinks();
      if (links[n]) { links[n].click(); e.preventDefault(); }
      return;
    }
    if (pressed(e, CFG.search) && (CFG.search.mod !== 'None' || !isTyping(e.target))) {
      var opener = D.querySelector('.st-key-global_search_open_main button');
      if (opener) {
        opener.click();
        var tries = 0, timer = setInterval(function () {                 // put the cursor in the pop-up's search box as soon as it exists
          var inp = D.querySelector('[role=dialog] input');
          if (inp) { inp.focus(); clearInterval(timer); } else if (++tries > 30) { clearInterval(timer); }
        }, 100);
      } else { var box = searchBox(); if (box) box.focus(); }
      e.preventDefault();
      return;
    }
    if (pressed(e, CFG.park)) {
      var area = D.querySelector('[data-testid="stMain"]') || D;
      var re = /Park Metrobank checks|Save invoice changes|Save statement changes|Add statement|Assign check number|Save changes|Save parked check edits|Save check corrections|Add installment|Add invoice|Create schedule rows/i;
      var cands = buttons(area).filter(function (b) { return re.test(b.innerText || ''); });
      if (cands.length === 1) { cands[0].click(); }
      else if (cands.length === 0) { flash('No Park / Save button on this screen.'); }
      else { flash(cands.length + ' Park / Save buttons here - click the one you want.'); }
      e.preventDefault();
      return;
    }
    if (pressed(e, CFG.new)) {
      var area2 = D.querySelector('[data-testid="stMain"]') || D;
      var nb = buttons(area2).find(function (b) { return /New (Batch|Loan Note|Bank Loan)/i.test(b.innerText || ''); });
      if (nb) { nb.click(); } else { flash('No "New ..." form on this screen.'); }
      e.preventDefault();
    }
  };
  P.__apHotkeys = handler;
  D.addEventListener('keydown', handler, true);
  // Search as you type: Streamlit only reads a text box on Enter, so once you pause typing in the Search everything box a synthetic Enter is sent.
  if (P.__apLiveSearch) D.removeEventListener('input', P.__apLiveSearch, true);
  P.__apLiveSearch = function (e) {
    var t = e.target;
    if (!t || t.tagName !== 'INPUT' || !t.closest('[role=dialog]') || (t.placeholder || '').indexOf('e.g. 3992554313') !== 0) return;
    if (P.__apSearchTimer) P.clearTimeout(P.__apSearchTimer);
    P.__apSearchTimer = P.setTimeout(function () {
      t.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }, 350);
  };
  D.addEventListener('input', P.__apLiveSearch, true);
  // hovering a menu item shows its hotkey (Streamlit redraws the menu now and then, so this is re-applied every second)
  function labelTabs() {
    menuLinks().slice(0, 10).forEach(function (t, i) {
      if (!t) return;
      var tip = CFG.pages.mod + '+' + ((i + 1) % 10);
      if (t.title !== tip) t.title = tip;
    });
  }
  if (P.__apHotkeyTimer) P.clearInterval(P.__apHotkeyTimer);
  P.__apHotkeyTimer = P.setInterval(labelTabs, 1000);
  labelTabs();
})();
</script>""".replace("__HOTKEY_CONFIG__", json.dumps({**_hotkeys(), "order": views.topbar.HOTKEY_PAGES})), height=0)
