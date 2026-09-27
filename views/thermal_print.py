"""Thermal printer section of a batch (Thermal Statement Printing, 2026-09-27): the 48-column slip preview for the FILE and SUPPLIER
copies, the A4 copy on Google Drive behind the QR (step 2), statement versions with ORIGINAL / REPRINT / REVISED and "Needs reprint"
(step 3), and a "test printer" that builds the exact ESC/POS job without any hardware (python-escpos Dummy). Audit + Bluetooth guardrail
(step 4) and the real printer backends (step 5) come next. The existing thermal PDF above stays as the backup (owner, 2026-09-27).

Viewing is never a print (owner asked, 2026-09-27): the preview, the PDFs and opening the Drive link record nothing. Only a slip sent to a
printer is recorded -- and a TEST-printer job only in the sandbox database, so that reprints / revisions can be tried now without making
the first real print on the live system look like a reprint."""
from __future__ import annotations

import os
from datetime import date

import streamlit as st

from engines.common import DB_PATH, php
from engines.drive_service import DriveError, DriveNotConnected, GoogleDrive, is_connected
from engines.statement_version_service import (
    current_version, ensure_published, link_expiry, needs_reprint, planned_print, print_type_for, record_print, revoke_older_versions,
)
from engines.thermal_statement import COPY_LABELS, build_job, build_slip, estimate_lines, get_printer_config, render_text
from views.shared import guard_duplicate_submit, q

COPY_CHOICES = {"Both": ["FILE", "SUPPLIER"], "File copy": ["FILE"], "Supplier copy": ["SUPPLIER"]}
TYPE_LABELS = {"ORIGINAL": "original", "REPRINT": "REPRINT", "REVISED": "REVISED"}


def _sandbox() -> bool:
    return "TEST" in os.path.basename(DB_PATH).upper()


def _type_text(p: dict) -> str:
    if p["print_type"] == "REVISED":
        return f"REVISED v{p['version_no']} (supersedes v{p['supersedes']})"
    if p["print_type"] == "REPRINT":
        return f"REPRINT of v{p['version_no']}"
    return f"original v{p['version_no']}"


def render_thermal_printer_section(statement_id: int, include_full_history: bool = True) -> None:
    sid = int(statement_id)
    cfg = get_printer_config()
    st.markdown("**Thermal printer (direct, 80mm)**  :orange-badge[Test mode · no printer connected]")
    st.caption("Preview of the slip exactly as it will print (48 characters wide). The totals are worked out from the lines below, not taken "
               "from the stored batch total. The QR opens the full A4 statement on Google Drive. Viewing this preview or the PDFs is never "
               "recorded as a print — only a slip sent to a printer is.")

    if needs_reprint(sid):
        st.warning(":material/print: **Needs reprint** — this batch was changed after its statement was printed. Print it again: the new "
                   "slip says REVISED, and the old QR link is switched off once the revised one is printed.")

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
    planned = {c: planned_print(sid, c) for c in COPY_CHOICES[choice]}
    today = date.today().isoformat()
    slips = []
    for c in COPY_CHOICES[choice]:
        p = planned[c]
        same_version = version is not None and p["version_no"] == version["version_no"]   # a new version gets its own link when printed
        slips.append(build_slip(sid, c, include_full_history=include_full_history, print_type=p["print_type"], version=p["version_no"],
                                reprint_date=today, qr_link=link if same_version else None,
                                link_until=until if (link and same_version) else None))
    slips = [s for s in slips if s is not None]
    if not slips:
        st.error("Could not build the slip for this batch.")
        return
    st.caption("Printing now: " + "; ".join(f"{COPY_LABELS[c]} as {_type_text(planned[c])}" for c in COPY_CHOICES[choice]) + ".")

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
            drive = None
            try:
                with st.spinner("Uploading the A4 copy to Google Drive..."):
                    drive = GoogleDrive()
                    v = ensure_published(sid, drive, sandbox=_sandbox())           # also starts the next version if the batch changed
            except (DriveNotConnected, DriveError) as e:
                st.session_state.pop(sent_key, None)
                st.error(f"Not printed — the QR copy could not be made, and a slip without a working QR is never printed. {e}")
            else:
                v_until = link_expiry(sid, v)
                types = {c: print_type_for(sid, c, v) for c in COPY_CHOICES[choice]}
                ready = [build_slip(sid, c, include_full_history=include_full_history, qr_link=v["drive_link"], version=v["version_no"],
                                    print_type=types[c]["print_type"], reprint_date=today, link_until=v_until) for c in COPY_CHOICES[choice]]
                job = build_job(ready, cfg["thermal_qr_size"])
                recorded, revoked = False, []
                if _sandbox():                                                  # test prints count only in the sandbox (see module doc)
                    for c in COPY_CHOICES[choice]:
                        record_print(sid, v, c, types[c]["print_type"], "dummy", "SUCCESS")
                    recorded = True
                    if any(t["print_type"] == "REVISED" for t in types.values()):
                        try:
                            revoked = revoke_older_versions(sid, v["version_id"], drive)
                        except DriveError as e:
                            st.warning(f"The old QR link could not be switched off yet ({e}); it is tried again on the next print.")
                st.session_state[sent_key] = {"bytes": job, "copies": [s.copy for s in ready], "ref": ready[0].statement_ref,
                                              "link": v["drive_link"], "version": v["version_no"], "recorded": recorded, "revoked": revoked,
                                              "types": {c: _type_text(t) for c, t in types.items()}}
                st.rerun()
    last = st.session_state.get(sent_key)
    if last:
        job = last["bytes"]
        cuts = job.count(b"\x1dV")                            # GS V = cut
        st.success(f"Test job built — " + "; ".join(f"{COPY_LABELS[c]} as {t}" for c, t in last["types"].items())
                   + f": {len(job):,} bytes, starts with ESC @ (initialize), QR to the A4 copy, {cuts} partial cut(s). "
                   f"Printer setting in use: {cfg['thermal_backend']}.")
        st.caption(("Recorded as printed (test copy only), so the next print of the same version is a REPRINT." if last["recorded"]
                    else "Not recorded: test-printer jobs never count as prints on the live system.")
                   + (f" Old QR link(s) switched off: v{', v'.join(str(n) for n in last['revoked'])}." if last["revoked"] else "")
                   + f" QR link: {last['link']}")
        st.download_button("Download the printer job (.bin, for testing with the real printer later)", data=job,
                           file_name=f"thermal_{last['ref']}_{'_'.join(last['copies']).lower()}.bin".replace(" ", "_"), mime="application/octet-stream",
                           icon=":material/download:", key=f"th_job_dl_{sid}")
