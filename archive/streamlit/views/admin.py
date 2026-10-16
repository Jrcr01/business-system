"""Admin & audit screen (moved out of app.py unchanged, 2026-09-27). app.py calls render() only while this page is open."""
from __future__ import annotations

import streamlit as st

from datetime import date
from engines.backup_service import export_database_backup
from engines.backup_service import restore_database_from_bytes
from engines.check_rules import archive_booklet
from engines.check_rules import create_booklet
from engines.checkbook_service import audit_booklet_gaps
from engines.common import PRIMARY_BANK_ACCOUNT_ID
from engines.common import fix_nullable_int
from engines.common import get_conn
from engines.lock_rules import apply_lock_catchup, lock_catchup_preview

from views.shared import (  # noqa: F401
    MONEY_FORMAT,
    confirm_change_dialog,
    flash,
    _read_whats_new,
    date_col,
    guard_duplicate_submit,
    money_col,
    page_header,
    q,
    refresh,
    section,
    status_badge,
    status_text,
)
from engines.clock import manila_today


def render_new_booklet_form() -> None:
    """Register a new check booklet (leaf range). Shared by Admin and audit and Add data."""
    from engines.common import booklet_defaults
    dflt = booklet_defaults(PRIMARY_BANK_ACCOUNT_ID)                                     # the next booklet follows the last one: its code, the number after its end, the same size
    with st.form("new_booklet_form", border=False):
        bk_code = st.text_input("Booklet code", value=dflt["code"], help='Your own label for the booklet (e.g. the number on its cover), so booklets are easy to tell apart.')
        bk1, bk2 = st.columns(2)
        bk_start = bk1.number_input("Start check number", min_value=1, step=1, value=dflt["start"], help="The full number printed on the first leaf, e.g. 3992554301. It is used exactly as typed.")
        bk_end = bk2.number_input("End check number", min_value=1, step=1, value=dflt["end"], help="The full number printed on the last leaf, e.g. 3992554400.")
        bk_received = st.date_input("Date received", value=manila_today(), help='When the booklet arrived from Metrobank — for your records.')
        if st.form_submit_button("Register booklet", icon=":material/menu_book:", type="primary"):
            if bk_end < bk_start:
                st.error("End check number must be greater than or equal to start check number.")
            elif guard_duplicate_submit("create_booklet", bk_code, bk_start, bk_end, bk_received):
                ok, msg = create_booklet(PRIMARY_BANK_ACCOUNT_ID, bk_code, int(bk_start), int(bk_end), bk_received)
                (st.success if ok else st.error)(msg)
                if ok:
                    refresh(done=msg)
            else:
                st.warning("Duplicate submission ignored — identical to the click just before it.")


def render() -> None:
    bank_df = q("SELECT * FROM bank_accounts WHERE bank_account_id = ?", (PRIMARY_BANK_ACCOUNT_ID,))
    bank = bank_df.iloc[0] if not bank_df.empty else None
    page_header("Admin and audit")

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
                new_width_mm = cal_w.number_input("Check width (mm)", min_value=1.0, value=float(bank["check_width_mm"]), step=1.0, key="cal_width_mm", help='The physical size of a check leaf — measure a real leaf with a ruler. Used to place the printed text.')
                new_height_mm = cal_h.number_input("Check height (mm)", min_value=1.0, value=float(bank["check_height_mm"]), step=1.0, key="cal_height_mm", help='The physical size of a check leaf — measure a real leaf with a ruler. Used to place the printed text.')
                cal_x, cal_y = st.columns(2)
                new_offset_x_mm = cal_x.number_input("Shift right (mm)", value=float(bank["check_offset_x_mm"]), step=0.5, key="cal_offset_x_mm", help='Print a test check. If the text is too far left, increase this (millimetres); a negative number moves it left.')
                new_offset_y_mm = cal_y.number_input("Shift down (mm)", value=float(bank["check_offset_y_mm"]), step=0.5, key="cal_offset_y_mm", help='Print a test check. If the text is too high, increase this (millimetres); a negative number moves it up.')
                if st.button("Save calibration", icon=":material/tune:", key="cal_save_btn"):
                    with get_conn() as conn:
                        conn.execute(
                            """UPDATE bank_accounts SET check_width_mm = ?, check_height_mm = ?,
                               check_offset_x_mm = ?, check_offset_y_mm = ? WHERE bank_account_id = ?""",
                            (new_width_mm, new_height_mm, new_offset_x_mm, new_offset_y_mm, PRIMARY_BANK_ACCOUNT_ID),
                        )
                        conn.commit()
                    refresh(done="Calibration saved.")

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
            render_new_booklet_form()
        with st.expander("Archive a booklet"):
            archivable_df = booklets_df[booklets_df["status"] != "Archived"] if not booklets_df.empty else booklets_df
            if archivable_df.empty:
                st.caption("No booklet available to archive.")
            else:
                arch_options = {int(r["booklet_id"]): (r["booklet_code"] or f"Booklet #{int(r['booklet_id'])}") + f" [{r['status']}]" for _, r in archivable_df.iterrows()}
                arch_pick = st.selectbox("Booklet", options=list(arch_options.keys()), format_func=lambda bid: arch_options[bid], key="archive_booklet_pick")
                if st.button("Archive this booklet", icon=":material/archive:", key="archive_booklet_btn"):
                    def _archive(bid=int(arch_pick)):
                        ok, msg = archive_booklet(bid)
                        if ok:
                            flash(msg)
                        return ok, msg

                    confirm_change_dialog(f"Archive booklet **{arch_options[int(arch_pick)]}**?  \nIt is retired: its numbers are no longer offered when assigning check numbers. "
                                          "Only archive a booklet that is finished or no longer used.", "archive_booklet_confirm", _archive, "Yes, archive it")

        with st.expander("Batch locks — one-time tidy-up"):
            st.caption("Batches now lock by themselves when their checks cover the full amount, and reopen when a check change leaves a balance. "
                       "This lists batches the old manual locking left out of step. Download a backup first (Database backup & restore, below), then apply: nothing else changes, and each change is in the audit log.")
            _prev = lock_catchup_preview()
            _cols = ["statement_ref", "supplier", "net_total", "active_checks", "remaining"]
            _money = {c: st.column_config.NumberColumn(c.replace("_", " ").title(), format="%.2f") for c in ("net_total", "active_checks", "remaining")}
            st.markdown(f"**Fully covered but still open ({len(_prev['to_lock'])})** — will be locked")
            if not _prev["to_lock"].empty:
                st.dataframe(_prev["to_lock"][_cols], hide_index=True, column_config=_money)
            st.markdown(f"**Locked but still owing ({len(_prev['to_unlock'])})** — will be reopened")
            if not _prev["to_unlock"].empty:
                st.dataframe(_prev["to_unlock"][_cols], hide_index=True, column_config=_money)
            if _prev["to_lock"].empty and _prev["to_unlock"].empty:
                st.success("Every batch is in step.")
            else:
                def _apply_catchup():
                    done = apply_lock_catchup()
                    msg = f"Locked {done['locked']} batch(es) and reopened {done['unlocked']}."
                    flash(msg)
                    return True, msg

                if st.button("Apply the tidy-up", icon=":material/lock_reset:", key="lock_catchup_btn"):
                    confirm_change_dialog(f"Lock **{len(_prev['to_lock'])}** fully covered batch(es) and reopen **{len(_prev['to_unlock'])}** that still owe?",
                                          "lock_catchup_confirm", _apply_catchup, "Yes, apply it",
                                          warning="Have you downloaded a backup? This changes the lock on every batch listed above.")

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
                    a1, a2, a3, a5, a4 = st.columns(5)
                    a1.metric("Total leaves", result["total_leaves"], border=True, help='How many checks this booklet holds (last number − first number + 1).')
                    a2.metric("Missing gaps", result["missing_gaps_count"], border=True, help='Leaves between the first and last used numbers with no check and not logged as spoiled — find out what happened to them.')
                    a3.metric("Duplicates", result["duplicate_numbers_count"], border=True, help='Numbers used by more than one check — one of them is probably a typo.')
                    a5.metric("Spoiled (error checks)", result["spoiled_count"], border=True, help='Leaves logged as spoiled (error checks) — they count as used, never reissued.')
                    a4.metric("Booklet", result["booklet_code"], border=True)
                    if result["missing_gaps_count"] > 0:
                        st.error(f"Unaccounted (skipped) check numbers: {', '.join(result['missing_gaps_list'])}")
                    if result["spoiled_count"] > 0:
                        st.info(f"Spoiled leaves logged as error checks (accounted for, not missing): {', '.join(result['spoiled_list'])}")
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
            restore_file = st.file_uploader("Upload backup file (.db)", type=["db"], help="A backup made by this app (.db). Restoring REPLACES all current data with the backup's; the current data is saved as a .bak_before_restore file first.")
            confirm_text = st.text_input("Type RESTORE to confirm", help='Typed on purpose, so a restore can never happen by an accidental click.')
            if st.button("Restore database", icon=":material/restore:", type="secondary"):
                if restore_file is None:
                    st.error("Upload a backup file first.")
                elif confirm_text != "RESTORE":
                    st.error("Type RESTORE in the confirmation box to proceed.")
                else:
                    ok, msg = restore_database_from_bytes(restore_file.getvalue())
                    (st.success if ok else st.error)(msg)
                    if ok:
                        refresh(done=msg)

    with st.container(border=True):
        st.subheader("Audit log")
        audit_full = fix_nullable_int(q("SELECT log_id, datetime(timestamp, '+8 hours') AS timestamp, user_email AS who, user_action, entity_name, entity_id, old_values, new_values FROM audit_logs ORDER BY log_id DESC"), "entity_id")
        action_filter = st.multiselect("Filter by action", options=sorted(audit_full["user_action"].unique()) if not audit_full.empty else [], help='Show only these kinds of recorded changes (e.g. checks issued, cancelled, bank lines resolved).')
        display_audit = audit_full[audit_full["user_action"].isin(action_filter)] if action_filter else audit_full
        st.dataframe(display_audit, hide_index=True)

    with st.container(border=True):
        st.subheader("Changelog")
        st.caption("Everything that's shipped, in plain language, newest first. The same source the \"What's new\" popup reads from.")
        st.markdown(_read_whats_new(), unsafe_allow_html=False)
