"""Checks & checkbook screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from engines.check_rules import assign_check_numbers_manual
from engines.check_rules import get_booklets
from engines.check_rules import get_check_print_calibration
from engines.check_rules import link_replacement
from engines.check_rules import log_spoiled_check
from engines.check_rules import spoiled_check_problems
from engines.check_run_service import CHRYSANTH_CROSSING_OPTIONS
from engines.check_run_service import file_name_for_batch
from engines.check_run_service import file_name_for_checks
from engines.check_run_service import generate_batch_txt
from engines.check_run_service import generate_check_run_pdf
from engines.check_run_service import generate_check_run_summary_pdf
from engines.check_run_service import generate_pending_check_run_txt
from engines.check_run_service import get_check_print_batch_detail
from engines.check_run_service import get_check_print_batches
from engines.check_run_service import get_chrysanth_settings
from engines.check_run_service import get_pending_check_run
from engines.check_run_service import get_unnumbered_checks
from engines.check_run_service import get_unprinted_batches
from engines.check_run_service import mark_batch_printed
from engines.check_run_service import mark_checks_printed
from engines.check_run_service import print_batch_tag
from engines.check_run_service import save_chrysanth_settings
from engines.common import PRIMARY_BANK_ACCOUNT_ID
from engines.pdc_release_service import for_release, mark_released, preview_release_before, recent_releases, release_before, undo_release
from engines.common import has_value
from engines.common import php
from engines.voided_checks_service import voided_checks
from views.guarantee import render_guarantee_list

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    PRINT_CHECKS_IN_APP,
    date_col,
    dup_needs_confirm,
    confirm_change_dialog,
    confirm_create_dialog,
    dup_resume,
    export_buttons,
    flash,
    guard_duplicate_submit,
    show_table,
    money_col,
    page_header,
    pdf_preview_and_download,
    q,
    refresh,
    section,
    status_badge,
    status_text,
)


def render_log_spoiled_form() -> None:
    """Log a spoiled check (misprint / jam), with the same number checks as everywhere else. Shared by Checks and checkbook and Add data."""
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
        spoiled_reason = st.text_input("Reason", placeholder="e.g. printer jam, misaligned print", help='Why the leaf was spoiled (misprint, wrong amount, torn …) — kept in the spoiled check log.')
        _spoil_submitted = st.form_submit_button("Log spoiled check", icon=":material/report:", type="primary")
    # handled outside the form: the "not in any booklet" confirmation pop-up has its own buttons, which a form cannot hold
    if _spoil_submitted or dup_resume("log_spoiled_check"):
        _sp_number, _sp_blocking, _sp_warnings = spoiled_check_problems(spoiled_number)
        if _sp_blocking:
            st.error(_sp_blocking[0])
        elif dup_needs_confirm("log_spoiled_check", _sp_warnings):
            pass
        elif guard_duplicate_submit("log_spoiled_check", spoiled_number, spoiled_reason):
            ok, msg = log_spoiled_check(spoiled_number, spoiled_reason)
            (st.success if ok else st.error)(msg)
            if ok:
                refresh(done=msg)
        else:
            st.warning("Duplicate submission ignored — identical to the click just before it.")


def render_for_release() -> None:
    """Printed post-dated checks the supplier has not collected yet, and the ones released lately (with Undo). Engine: engines/pdc_release_service.py."""
    from datetime import timedelta

    from engines.clock import manila_today

    with st.container(border=True):
        st.subheader("For release")
        st.caption("Printed checks still in the office. When a supplier collects theirs, tick them here and say who took them: from then the supplier can deposit on the check date. "
                   "Checks written by hand are handed over when recorded, so they are not listed.")
        waiting = for_release()
        if waiting.empty:
            st.caption("Nothing is waiting: every printed check has been collected.")
        else:
            st.metric("Printed, not collected", f"{len(waiting)} · {php(waiting['amount'].sum())}", f"oldest {int(waiting['days_waiting'].max())} day(s) in the office",
                      delta_color="off", delta_arrow="off", border=True)
            grid = waiting.assign(collect=False)[["collect", "supplier", "check_number", "check_date", "amount", "days_waiting"]]
            edited = st.data_editor(
                grid, hide_index=True, width="stretch", key="release_grid", disabled=["supplier", "check_number", "check_date", "amount", "days_waiting"],
                column_config={"collect": st.column_config.CheckboxColumn("Collected"), "supplier": "Supplier", "check_number": "Check no.",
                               "check_date": st.column_config.DateColumn("Check date"), "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT),
                               "days_waiting": st.column_config.NumberColumn("Days in office", format="%d")})
            ticked = [int(waiting.loc[i, "check_id"]) for i in edited.index[edited["collect"]]]
            c1, c2, c3 = st.columns([2, 1.4, 1.4], vertical_alignment="bottom")
            who = c1.text_input("Collected by", key="release_who", placeholder="e.g. the supplier's collector")
            when = c2.date_input("Collected on", value=manila_today(), key="release_date")
            if c3.button(f"Mark {len(ticked)} released" if ticked else "Mark released", icon=":material/outbox:", type="primary", key="release_go", disabled=not ticked, width="stretch"):
                ok, msg = mark_released(ticked, when, who)
                (refresh(done=msg) if ok else st.error(msg))
        with st.expander("Checks already with their suppliers (one-time catch-up)", icon=":material/inventory:"):
            st.caption("Checks printed before release tracking began are already in the suppliers' hands. Pick the last print date to treat as collected: every waiting check printed on or "
                       "before it is marked released on its print date. Do this once, then use the list above for new checks.")
            cut = st.date_input("Printed on or before", value=manila_today() - timedelta(days=14), key="release_cut")
            pv = preview_release_before(cut)
            st.write(f"This would mark **{pv['checks']}** check(s), {php(pv['amount'])}, as already collected.")
            if st.button("Mark them as collected", icon=":material/done_all:", key="release_cut_go", disabled=not pv["checks"]):
                def _cut():
                    ok, msg = release_before(cut)
                    if ok:
                        flash(msg)
                    return ok, msg

                confirm_change_dialog(f"Mark **{pv['checks']}** check(s) ({php(pv['amount'])}) printed on or before {cut:%b %d, %Y} as already collected by their suppliers?",
                                      "release_cut_confirm", _cut, "Yes, mark them", warning="Use this only for checks the suppliers really hold. Each can still be taken back one by one.")
        recent = recent_releases(30)
        if not recent.empty:
            with st.expander(f"Released in the last 30 days ({len(recent)})", icon=":material/history:"):
                st.dataframe(recent.assign(state=["Cleared by the bank" if c else "With the supplier" for c in recent["cleared"]])[
                    ["released_date", "supplier", "check_number", "check_date", "amount", "released_to", "state"]], hide_index=True, width="stretch",
                    column_config={"released_date": st.column_config.DateColumn("Released"), "supplier": "Supplier", "check_number": "Check no.",
                                   "check_date": st.column_config.DateColumn("Check date"), "amount": st.column_config.NumberColumn("Amount", format=MONEY_FORMAT),
                                   "released_to": "Collected by", "state": "Now"})
                undoable = recent[recent["cleared"] == 0]
                if not undoable.empty:
                    labels = {int(r.check_id): f"{r.supplier} · {r.check_number or 'no number'} · {php(r.amount)} · released {r.released_date}" for r in undoable.itertuples()}
                    pick = st.selectbox("Marked by mistake?", list(labels), format_func=labels.get, key="release_undo_pick", index=None, placeholder="Pick a check to take back")
                    if pick is not None and st.button("Undo this release", icon=":material/undo:", key="release_undo"):
                        ok, msg = undo_release(int(pick))
                        (refresh(done=msg) if ok else st.error(msg))


def render() -> None:
    page_header("Checks and checkbook")

    render_for_release()

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
                help='Checks going into this Chrysanth run. Untick any you want to keep parked for a later run.',
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
                    _cs_acct = st.text_input("Account number in Chrysanth", value=_cs["account_no"], max_chars=20, key="chrys_account_no", help='Exactly as the account is set up inside Chrysanth (up to 20 characters) — Chrysanth rejects the file otherwise.')
                    _cs_cross = st.selectbox("Crossing on every cheque", options=list(CHRYSANTH_CROSSING_OPTIONS), index=list(CHRYSANTH_CROSSING_OPTIONS).index(_cs["crossing"]),
                                             format_func=lambda k: CHRYSANTH_CROSSING_OPTIONS[k], key="chrys_crossing", help='The crossing printed on every cheque in the exported file.')
                    if st.button("Save Chrysanth settings", icon=":material/save:", key="chrys_save_btn"):
                        ok, msg = save_chrysanth_settings(_cs_acct, _cs_cross)
                        (st.success if ok else st.error)(msg)
                        if ok:
                            refresh(done=msg)
                _n_unnumbered = int(sum(1 for _, r in pending_df.iterrows() if not has_value(r["check_number"])))
                if _n_unnumbered:
                    st.info(f"{_n_unnumbered} pending check(s) have no check number yet. Chrysanth's file needs one on every row, so those get a temporary placeholder number (starting with 9) "
                            "in the file only — nothing is assigned here. Assign the real numbers under Assign a check number once they are printed.")
                if not _cs["account_no"]:
                    st.info("Save the Chrysanth account number above to enable the export.")
                # Generating the file IS the "sent for printing" moment: it logs a batch (Batch run history, below)
                # and stamps printed_at, as the old in-app print button did.
                if st.button("Generate Chrysanth file (.txt)", icon=":material/download:", type="primary", disabled=not print_picked_ids or not _cs["account_no"], key="check_export_excel_btn"):
                    def _make_chrysanth_file(picked=list(print_picked_ids)):
                        txt_bytes, txt_problems, txt_ids = generate_pending_check_run_txt(picked, log_batch=True)
                        if not txt_ids:
                            return False, "Nothing could be exported:" + chr(10) + chr(10).join(f"- {p}" for p in txt_problems)
                        newest_batch = get_check_print_batches().iloc[0]
                        # Keep the file across the rerun the pop-up triggers -- the download button used to vanish with it.
                        st.session_state["_chrysanth_txt"] = {"tag": print_batch_tag(int(newest_batch["print_batch_id"])), "count": len(txt_ids),
                                                              "bytes": txt_bytes, "problems": txt_problems,
                                                              "file_name": file_name_for_checks(txt_ids, int(newest_batch["print_batch_id"]))}
                        return True, ""

                    _picked_total = float(pending_df[pending_df["check_id"].isin(print_picked_ids)]["amount"].sum())
                    confirm_change_dialog(f"Make the Chrysanth file for **{len(print_picked_ids)} check(s)** totaling **{php(_picked_total)}**?  \n"
                                          "This logs a print run and marks these checks as sent for printing — they leave the \"Checks to print\" list.",
                                          "chrysanth_confirm", _make_chrysanth_file, "Yes, make the file")
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
                    def _unmark(bp=int(batch_pick)):
                        ok, msg = mark_batch_printed(bp, False)
                        if ok:
                            flash(msg)
                        return ok, msg

                    confirm_change_dialog(f"Un-mark **{print_batch_tag(int(batch_pick))}** as printed?  \nIt goes back to \"exported, not yet printed\".",
                                          f"unmark_printed_confirm_{int(batch_pick)}", _unmark, "Yes, un-mark it")
            else:
                _pk2.caption(f"{print_batch_tag(int(batch_pick))} is exported but not marked printed. Once you have printed it in Chrysanth, mark it here.")
                if _pk1.button("Mark as printed", icon=":material/print:", type="primary", key=f"mark_printed_{int(batch_pick)}"):
                    def _mark(bp=int(batch_pick)):
                        ok, msg = mark_batch_printed(bp, True)
                        if ok:
                            flash(msg)
                        return ok, msg

                    confirm_change_dialog(f"Mark **{print_batch_tag(int(batch_pick))}** as printed ({int(_picked_row['check_count'])} check(s))?  \nOnly do this once the run is really printed in Chrysanth.",
                                          f"mark_printed_confirm_{int(batch_pick)}", _mark, "Yes, it is printed")
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
            "registered and managed in Admin and audit."
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
                help='Checks to number now; the rest stay unnumbered for later.',
            )

            st.caption("Type each check's number directly (Phase 6 Redux v2 dropped auto-sequencing here — the per-batch \"Assign a check number\" section in Order Statement/Lenders already covers picking a number from a booklet's dropdown one check at a time; this bulk grid is for typing several in at once, e.g. a backfill from an older booklet).")
            backfill_booklets_df = get_booklets(PRIMARY_BANK_ACCOUNT_ID, statuses=("Active", "Exhausted", "Archived"))
            b_ref_options = {None: "(none / not tracked)"}
            for _, r in backfill_booklets_df.iterrows():
                b_ref_options[int(r["booklet_id"])] = f"{r['booklet_code'] or ('Booklet #' + str(int(r['booklet_id'])))} [{r['status']}]"
            ref_booklet_id = st.selectbox(
                "Which booklet do these belong to? (optional, for record-keeping)",
                options=list(b_ref_options.keys()), format_func=lambda bid: b_ref_options[bid], key="check_assign_manual_booklet",
                help='Optional: which booklet the leaves came from, so its missing / duplicate counts stay accurate.',
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

                    def _assign_numbers(a=assignments, booklet=ref_booklet_id):
                        ok, msg, assigned_ids = assign_check_numbers_manual(a, booklet)
                        if ok:
                            flash(msg)
                        return ok, msg

                    confirm_change_dialog(f"Give **{len(assignments)} check(s)** these numbers?\n\n" + "\n".join(
                        f"- {assign_options[cid]} → **{str(no).strip() or '(blank)'}**" for cid, no in assignments.items()),
                        "bulk_assign_confirm", _assign_numbers, "Yes, assign the numbers",
                        warning="A check number is final once assigned — changing it later needs a logged correction.")

    with st.expander("Log a spoiled check (misprint / jam)"):
        render_log_spoiled_form()
    render_voided_checks_register()
    render_guarantee_list()


VOIDED_LABELS = {"what": "What", "check_number": "Check #", "check_date": "Check date", "amount": "Amount", "payee": "Payee",
                 "reference": "Batch / loan note", "happened_at": "When", "reason": "Reason", "replaced_by": "Replaced by check #",
                 "bank_paid_anyway": "Bank paid it anyway?", "handwritten": "Handwritten"}
VOIDED_KINDS = {"check_date": "date", "amount": "money", "happened_at": "datetime"}


def render_voided_checks_register() -> None:
    """Every voided, replaced and spoiled check in one list (owner, 2026-09-30), with the date, the reason, the check that replaced it and a
    warning when the bank paid a check we had voided."""
    df_all = voided_checks()
    with st.container(border=True):
        st.subheader("Voided, replaced and spoiled checks")
        st.caption("Every check we voided or replaced, and every spoiled leaf: when, why, and which check replaced it. "
                   "\"Bank paid it anyway\" means the bank statement shows a payment under a number we had voided — check it with the bank.")
        if df_all.empty:
            st.caption("None yet.")
            return
        f1, f2 = st.columns([2, 3])
        what = f1.multiselect("Show", ["Voided", "Replaced", "Spoiled"], default=["Voided", "Replaced", "Spoiled"], key="voided_what")
        df = df_all[df_all["what"].isin(what)] if what else df_all.iloc[0:0]
        text = f2.text_input("Search (check #, payee, reason, batch)", key="voided_search").strip().lower()
        if text:
            hay = df[["check_number", "payee", "reason", "reference"]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
            df = df[hay.str.contains(text, regex=False)]
        alert = df[df["bank_paid_anyway"] != ""]
        if not alert.empty:
            st.error(f"{len(alert)} voided / spoiled check(s) appear on the bank statement: "
                     + ", ".join(str(n) for n in alert["check_number"]) + ". Check them with the bank.", icon=":material/warning:")
        m1, m2, m3 = st.columns(3)
        m1.metric("Voided", int((df["what"] == "Voided").sum()), border=True)
        m2.metric("Replaced", int((df["what"] == "Replaced").sum()), border=True)
        m3.metric("Spoiled", int((df["what"] == "Spoiled").sum()), border=True)
        show_table(df, VOIDED_LABELS, hide=["check_id"], kinds=VOIDED_KINDS)
        export_buttons(df.drop(columns=["check_id"]), "voided_replaced_spoiled_checks", key="dl_voided", labels=VOIDED_LABELS,
                       what="voided checks", kinds=VOIDED_KINDS)
        voided_only = df_all[(df_all["what"] == "Voided") & df_all["check_id"].notna()]
        if not voided_only.empty:
            with st.expander("Link a voided check to the check that replaced it", icon=":material/link:"):
                st.caption("Use this when the replacement was written by hand and recorded separately: the voided check is then shown as replaced by it.")
                old_opts = {int(r.check_id): f"{r.check_number or 'no number'} · {php(r.amount)} · {r.payee}" for r in voided_only.itertuples()}
                old_id = st.selectbox("Voided check", list(old_opts), format_func=old_opts.get, key="vc_link_old")
                payee_id = int(q("SELECT creditor_id FROM checks_issued WHERE check_id = ?", (old_id,))["creditor_id"][0])
                active = q("SELECT check_id, check_number, check_date, amount FROM checks_issued WHERE creditor_id = ? AND status = 'Active' ORDER BY check_id DESC LIMIT 150", (payee_id,))
                if active.empty:
                    st.info("This payee has no active check to link to. Record the replacement first (Add data → Handwritten check).")
                else:
                    new_opts = {int(r.check_id): f"{r.check_number or 'no number yet'} · {r.check_date} · {php(r.amount)}" for r in active.itertuples()}
                    new_id = st.selectbox("Replaced by", list(new_opts), format_func=new_opts.get, key="vc_link_new")
                    if st.button("Link them", icon=":material/link:", key="vc_link_btn"):
                        def _do(o=int(old_id), n=int(new_id)):
                            ok, msg = link_replacement(o, n, "linked by hand")
                            if ok:
                                st.toast(msg)
                            return ok, msg

                        confirm_create_dialog(f"Show **{old_opts[int(old_id)]}** as replaced by **{new_opts[int(new_id)]}**?", "vc_link", _do)
