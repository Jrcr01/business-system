"""Thermal printer section of a batch (Thermal Statement Printing, 2026-09-27): the 48-column slip preview for the FILE and SUPPLIER
copies, the A4 copy on Google Drive behind the QR (step 2), and a "test printer" that builds the exact ESC/POS job without any hardware
(python-escpos Dummy). Nothing reaches paper yet: versions / reprints (step 3), audit + Bluetooth guardrail (step 4) and the real printer
backends (step 5) come next. The existing thermal PDF above stays as the backup (owner, 2026-09-27)."""
from __future__ import annotations

import os

import streamlit as st

from engines.common import DB_PATH, php
from engines.drive_service import DriveError, DriveNotConnected, GoogleDrive, is_connected
from engines.statement_version_service import current_version, ensure_published, link_expiry
from engines.thermal_statement import COPY_LABELS, build_job, build_slip, estimate_lines, get_printer_config, render_text
from views.shared import guard_duplicate_submit, q

COPY_CHOICES = {"Both": ["FILE", "SUPPLIER"], "File copy": ["FILE"], "Supplier copy": ["SUPPLIER"]}


def _sandbox() -> bool:
    return "TEST" in os.path.basename(DB_PATH).upper()


def render_thermal_printer_section(statement_id: int, include_full_history: bool = True) -> None:
    sid = int(statement_id)
    cfg = get_printer_config()
    st.markdown("**Thermal printer (direct, 80mm)**  :orange-badge[Test mode · no printer connected]")
    st.caption("Preview of the slip exactly as it will print (48 characters wide). The totals are worked out from the lines below, not taken "
               "from the stored batch total. The QR opens the full A4 statement on Google Drive. Nothing goes to paper yet — versions / reprints "
               "and the real printer come in the next steps.")

    version = current_version(sid)
    link = version["drive_link"] if version and version["drive_public"] and version["drive_file_id"] else None
    until = link_expiry(sid, version) if version else None
    if not is_connected():
        st.info(":material/cloud_off: **Google Drive is not connected yet**, so no QR copy can be made. One-time setup: double-click "
                "**drive_connect.bat** in the project folder and follow the steps.")
    elif link:
        st.caption(f":material/cloud_done: QR copy on Drive: statement v{version['version_no']} — [open the A4 copy]({link})"
                   + (f" · online until {until:%b %d, %Y}" if until else ""))
    elif version and version["drive_file_id"]:
        st.caption(f":material/link_off: The online copy of v{version['version_no']} expired"
                   + (f" on {until:%b %d, %Y}" if until else "") + " — printing again re-opens it for 3 months.")
    else:
        st.caption(":material/cloud_upload: Google Drive connected. The A4 copy is uploaded and shared by link when you print.")

    choice = st.segmented_control("Copies", list(COPY_CHOICES), default="Both", required=True, key=f"th_copies_{sid}", label_visibility="collapsed")
    slips = [build_slip(sid, c, include_full_history=include_full_history, qr_link=link, link_until=until if link else None,
                        version=version["version_no"] if version else 1) for c in COPY_CHOICES[choice]]
    slips = [s for s in slips if s is not None]
    if not slips:
        st.error("Could not build the slip for this batch.")
        return

    # Cross-check (not printed): the computed net against the stored net the rest of the app shows.
    stored = q("SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (sid,))
    net = slips[0].totals["net"]
    if not stored.empty and net is not None and abs(float(stored["net_total"][0]) - net) > 0.005:
        st.warning(f"The slip's net total ({php(net)}) differs from the batch total on file ({php(stored['net_total'][0])}). The slip uses its own "
                   "lines — check the batch's invoices and credit memos.")
    if slips[0].totals["missing_amounts"]:
        st.warning(f"{slips[0].totals['missing_amounts']} line(s) have no amount on file, so the affected totals print as —.")

    # one copy per tab: side by side, 48 columns do not fit and the amounts get cut off
    for tab, slip in zip(st.tabs([f"{COPY_LABELS[s.copy]} · about {estimate_lines(s)} lines" for s in slips]), slips):
        with tab:
            st.code(render_text(slip), language=None, width="content")

    sent_key = f"_th_test_job_{sid}"
    if st.button("Send to the test printer (no paper)", icon=":material/print:", key=f"th_test_btn_{sid}",
                 help="Makes sure the A4 copy is on Google Drive and shared by link (the QR needs it), then builds the exact printer job with a "
                      "pretend printer, so everything can be checked before the real printer arrives."):
        if not guard_duplicate_submit("thermal_test_job", sid, choice):
            st.warning("Duplicate click ignored — identical to the click just before it.")
        else:
            try:
                with st.spinner("Uploading the A4 copy to Google Drive..."):
                    v = ensure_published(sid, GoogleDrive(), sandbox=_sandbox())
            except (DriveNotConnected, DriveError) as e:
                st.session_state.pop(sent_key, None)
                st.error(f"Not printed — the QR copy could not be made, and a slip without a working QR is never printed. {e}")
            else:
                v_until = link_expiry(sid, v)
                ready = [build_slip(sid, c, include_full_history=include_full_history, qr_link=v["drive_link"], version=v["version_no"],
                                    link_until=v_until) for c in COPY_CHOICES[choice]]
                st.session_state[sent_key] = {"bytes": build_job(ready, cfg["thermal_qr_size"]), "copies": [s.copy for s in ready],
                                              "ref": ready[0].statement_ref, "link": v["drive_link"], "version": v["version_no"]}
                st.rerun()
    last = st.session_state.get(sent_key)
    if last:
        job = last["bytes"]
        cuts = job.count(b"\x1dV")                            # GS V = cut
        st.success(f"Test job built for {', '.join(COPY_LABELS[c] for c in last['copies'])} (statement v{last['version']}): {len(job):,} bytes, "
                   f"starts with ESC @ (initialize), QR to the A4 copy, {cuts} partial cut(s). Printer setting in use: {cfg['thermal_backend']}.")
        st.caption(f"QR link: {last['link']} — scan it with a phone to check it opens without signing in.")
        st.download_button("Download the printer job (.bin, for testing with the real printer later)", data=job,
                           file_name=f"thermal_{last['ref']}_{'_'.join(last['copies']).lower()}.bin".replace(" ", "_"), mime="application/octet-stream",
                           icon=":material/download:", key=f"th_job_dl_{sid}")
