"""Thermal printer section of a batch (Thermal Statement Printing, 2026-09-27): the 48-column slip preview for the FILE and SUPPLIER
copies, the A4 copy on Google Drive behind the QR (step 2), statement versions with ORIGINAL / REPRINT / REVISED and "Needs reprint"
(step 3), printing through engines/thermal_print_service.py with the audit entries and the Bluetooth guardrail (step 4), and the printer
settings. The real USB / Bluetooth / network connections are finished in step 5, when the printer arrives; until then the "test printer"
builds the exact ESC/POS job without any hardware (python-escpos Dummy). The existing thermal PDF above stays as the backup (owner).

Viewing is never a print (owner asked, 2026-09-27): the preview, the PDFs and opening the Drive link record nothing. Only a slip sent to a
printer is recorded -- and a TEST-printer job only in the sandbox database, so that reprints / revisions can be tried now without making
the first real print on the live system look like a reprint."""
from __future__ import annotations

import os
import time
from datetime import date

import streamlit as st

from engines.common import DB_PATH, php
from engines.drive_service import DriveError, DriveNotConnected, GoogleDrive, is_connected
from engines.statement_version_service import current_version, link_expiry, needs_reprint, planned_print
from engines.thermal_print_service import BACKEND_LABELS, PrintAborted, needs_bluetooth_warning, print_statement
from engines.thermal_statement import (
    COPY_LABELS, QR_LINES_ESTIMATE, build_slip, estimate_lines, get_printer_config, render_text, save_printer_config,
)
from views.shared import guard_duplicate_submit, q, refresh
from engines.clock import manila_today

COPY_CHOICES = {"All three": ["SUPPLIER", "FILE", "FINANCE"], "Supplier copy": ["SUPPLIER"], "Filing copy": ["FILE"], "Finance copy": ["FINANCE"]}
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
    st.markdown("**Thermal printer (direct, 80mm)**  " + (":orange-badge[Test mode · no printer connected]" if cfg["thermal_backend"] == "dummy"
                                                          else f":blue-badge[{BACKEND_LABELS.get(cfg['thermal_backend'], cfg['thermal_backend'])}]"))
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

    choice = st.segmented_control("Copies", list(COPY_CHOICES), default="All three", required=True, key=f"th_copies_{sid}", label_visibility="collapsed",
                                  help="The statement is printed three times: the supplier's copy, the filing copy (attached to the sales invoice original) and the finance copy. Each ends with the release slip number and the supplier's signature.")
    planned = {c: planned_print(sid, c) for c in COPY_CHOICES[choice]}
    today = manila_today().isoformat()
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

    _printer_settings(cfg)
    backend = cfg["thermal_backend"]
    record = backend != "dummy" or _sandbox()           # test-printer jobs count only in the sandbox (see module doc)
    copies = COPY_CHOICES[choice]
    longest = max(estimate_lines(sl) + (0 if sl.qr_link else QR_LINES_ESTIMATE) for sl in slips)       # the printed slip always has a QR
    ask_key, result_key = f"_th_bt_ask_{sid}", f"_th_result_{sid}"
    if st.button(f"Print {choice.lower()} — {BACKEND_LABELS.get(backend, backend)}", icon=":material/print:", type="primary", key=f"th_print_btn_{sid}",
                 help="Makes sure the A4 copy is on Google Drive (the QR needs it), then prints each copy. Every copy printed is recorded in the "
                      "audit log." + ("" if record else " (Test printer on the live system: nothing is recorded.)")):
        if not guard_duplicate_submit("thermal_print", sid, choice, backend):
            st.warning("Duplicate click ignored — one click prints once.")
        elif needs_bluetooth_warning(backend, longest, cfg):
            st.session_state[ask_key] = True
        else:
            _do_print(sid, copies, backend, cfg, record, include_full_history, result_key)

    if st.session_state.get(ask_key):
        with st.container(border=True):
            st.warning(f":material/bluetooth: **This slip is about {longest} lines** — longer than your Bluetooth limit ({cfg['thermal_bt_line_threshold']} lines). "
                       "Over Bluetooth a long slip is slow, and if the connection drops you get half a slip. USB is safer.")
            a1, a2, a3 = st.columns(3)
            if a1.button("Switch to USB and print", icon=":material/usb:", type="primary", key=f"th_bt_usb_{sid}", width="stretch"):
                st.session_state.pop(ask_key, None)
                _do_print(sid, copies, "win32raw", cfg, True, include_full_history, result_key)
            if a2.button("Print via Bluetooth anyway", icon=":material/bluetooth:", key=f"th_bt_anyway_{sid}", width="stretch"):
                st.session_state.pop(ask_key, None)
                _do_print(sid, copies, "serial", cfg, True, include_full_history, result_key)
            if a3.button("Cancel", icon=":material/close:", key=f"th_bt_cancel_{sid}", width="stretch"):
                st.session_state.pop(ask_key, None)
                st.rerun()

    res = st.session_state.get(result_key)
    if res:
        if res.get("error"):
            (st.warning if res.get("done") else st.error)(("Printed: " + ", ".join(COPY_LABELS[c] for c in res["done"]) + ". " if res.get("done") else "")
                                                         + "Not printed — " + res["error"])
        else:
            st.success(f"Printed {', '.join(COPY_LABELS[c] + ' (' + res['types'][c] + ')' for c in res['done'])} — statement v{res['version_no']} "
                       f"on {BACKEND_LABELS.get(res['backend'], res['backend'])}.")
            st.caption(("Recorded in the audit log, one entry per copy." if res["recorded"] else "Not recorded: test-printer jobs never count as prints on the live system.")
                       + (f" Old QR link(s) switched off: v{', v'.join(str(n) for n in res['revoked'])}." if res.get("revoked") else "")
                       + (f" The old QR link could not be switched off yet ({res['revoke_error']}); it is tried again next time." if res.get("revoke_error") else ""))
            if res.get("jobs") and res["backend"] == "dummy":
                st.download_button("Download the printer job (.bin, for testing with the real printer later)", data=b"".join(res["jobs"].values()),
                                   file_name=f"thermal_{res['ref']}.bin".replace(" ", "_"), mime="application/octet-stream", icon=":material/download:",
                                   key=f"th_job_dl_{sid}")


def _do_print(sid: int, copies: list, backend: str, cfg: dict, record: bool, include_full_history: bool, result_key: str) -> None:
    """One print run (one button click): a session flag refuses a second run for the same batch while this one is going (a quick double
    click, or a click while Streamlit is still working), then the result is shown after a refresh."""
    run_key = f"_th_running_{sid}"
    started = st.session_state.get(run_key)
    if started and time.monotonic() - started < 120:
        st.warning("A print for this batch is already running — wait for it to finish.")
        return
    st.session_state[run_key] = time.monotonic()
    try:
        try:
            with st.spinner("Preparing the QR copy and printing..."):
                slip_no = None
                if record:                                               # the release slip number printed on every copy; the release is recorded by it later
                    from engines.release_slip_service import create_slip
                    ok_slip, _msg_slip, made = create_slip(sid)
                    slip_no = made["slip_no"] if ok_slip else None
                r = print_statement(sid, copies, backend, GoogleDrive(), cfg, sandbox=_sandbox(), record=record, include_full_history=include_full_history, slip_no=slip_no)
        except (DriveNotConnected, DriveError) as e:                   # GoogleDrive() itself could not start
            raise PrintAborted("drive", f"The QR copy could not be made, and a slip without a working QR is never printed. {e}")
        st.session_state[result_key] = {"done": r["done"], "types": r["types"], "version_no": r["version"]["version_no"], "backend": backend,
                                        "recorded": record, "revoked": r["revoked"], "revoke_error": r["revoke_error"], "jobs": r["jobs"],
                                        "ref": str(sid)}
    except PrintAborted as e:
        st.session_state[result_key] = {"error": str(e), "kind": e.kind, "done": e.done, "backend": backend}
    finally:
        st.session_state.pop(run_key, None)
    st.rerun()


def _printer_settings(cfg: dict) -> None:
    with st.expander("Printer settings", icon=":material/settings:"):
        st.caption("How this laptop reaches the thermal printer. USB and Bluetooth are switched on when the printer arrives (step 5); until then "
                   "use the test printer.")
        keys = list(BACKEND_LABELS)
        backend = st.selectbox("Printer connection", keys, index=keys.index(cfg["thermal_backend"]) if cfg["thermal_backend"] in keys else 0,
                               format_func=BACKEND_LABELS.get, key="th_cfg_backend", help='How this laptop reaches the thermal printer: USB (Windows printer), Bluetooth (COM port), network (IP address), or the test printer.')
        c1, c2 = st.columns(2)
        win = c1.text_input("Windows printer name (USB)", value=cfg["thermal_win32_printer"], placeholder="e.g. XP-T837L", key="th_cfg_win", help='Exactly as shown in Windows Settings → Bluetooth & devices → Printers & scanners.')
        port = c2.text_input("Bluetooth COM port", value=cfg["thermal_serial_port"], placeholder="e.g. COM5", key="th_cfg_port", help='The outgoing COM port Windows gave the paired printer (Bluetooth settings → More Bluetooth settings → COM Ports).')
        host = c1.text_input("Printer IP address (network)", value=cfg["thermal_network_host"], placeholder="e.g. 192.168.1.50", key="th_cfg_host", help="The printer's address on the office network — print the printer's self-test page to see it.")
        thr = c2.number_input("Bluetooth warning above (lines)", min_value=20, max_value=1000, step=10, value=int(cfg["thermal_bt_line_threshold"]),
                              key="th_cfg_thr", help='Printing a long slip over Bluetooth is slow; above this many lines you are asked first (default 120).')
        if st.button("Save printer settings", icon=":material/save:", key="th_cfg_save"):
            save_printer_config({"thermal_backend": backend, "thermal_win32_printer": win.strip(), "thermal_serial_port": port.strip().upper(),
                                 "thermal_network_host": host.strip(), "thermal_bt_line_threshold": int(thr)})
            refresh(done="Printer settings saved.")
