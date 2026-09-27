"""Thermal printer section of a batch (Thermal Statement Printing, build step 1, 2026-09-27): the 48-column slip preview for the FILE and
SUPPLIER copies, and a "test printer" that builds the exact ESC/POS job without any hardware (python-escpos Dummy). Nothing reaches
paper yet: the QR / A4 copy on Drive (step 2), versions and reprints (step 3), audit + Bluetooth guardrail (step 4) and the real printer
backends (step 5) come next. The existing thermal PDF above stays as the backup (owner, 2026-09-27)."""
from __future__ import annotations

import streamlit as st

from engines.common import php
from engines.thermal_statement import COPY_LABELS, build_job, build_slip, estimate_lines, get_printer_config, render_text
from views.shared import guard_duplicate_submit, q

COPY_CHOICES = {"Both": ["FILE", "SUPPLIER"], "File copy": ["FILE"], "Supplier copy": ["SUPPLIER"]}


def render_thermal_printer_section(statement_id: int, include_full_history: bool = True) -> None:
    sid = int(statement_id)
    cfg = get_printer_config()
    st.markdown("**Thermal printer (direct, 80mm)**  :orange-badge[Test mode · no printer connected]")
    st.caption("Preview of the slip exactly as it will print (48 characters wide). The totals are worked out from the lines below, not taken "
               "from the stored batch total. Nothing goes to paper yet — the QR link to the A4 copy, versions / reprints and the real printer "
               "come in the next steps.")
    choice = st.segmented_control("Copies", list(COPY_CHOICES), default="Both", required=True, key=f"th_copies_{sid}", label_visibility="collapsed")
    slips = [build_slip(sid, c, include_full_history=include_full_history) for c in COPY_CHOICES[choice]]
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
                 help="Builds the exact printer job (ESC @, Font A, lines, QR, feed, partial cut) with a pretend printer, so the layout can be checked "
                      "before the real printer arrives."):
        if guard_duplicate_submit("thermal_test_job", sid, choice):
            job = build_job(slips, cfg["thermal_qr_size"])
            st.session_state[sent_key] = {"bytes": job, "copies": [s.copy for s in slips], "ref": slips[0].statement_ref}
        else:
            st.warning("Duplicate click ignored — identical to the click just before it.")
    last = st.session_state.get(sent_key)
    if last:
        job = last["bytes"]
        cuts = job.count(b"\x1dV")                            # GS V = cut
        st.success(f"Test job built for {', '.join(COPY_LABELS[c] for c in last['copies'])}: {len(job):,} bytes, starts with ESC @ (initialize), "
                   f"{cuts} partial cut(s). Printer setting in use: {cfg['thermal_backend']}.")
        st.download_button("Download the printer job (.bin, for testing with the real printer later)", data=job,
                           file_name=f"thermal_{last['ref']}_{'_'.join(last['copies']).lower()}.bin".replace(" ", "_"), mime="application/octet-stream",
                           icon=":material/download:", key=f"th_job_dl_{sid}")
