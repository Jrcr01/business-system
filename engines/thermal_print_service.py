"""Printing a thermal statement (Thermal Statement Printing, step 4, 2026-09-27): the one path every print goes through -- audit and the
Bluetooth guardrail. The screen (views/thermal_print.py) only asks the questions; everything that must not go wrong is here.

  print_statement()  1. makes sure the A4 copy behind the QR is on Drive (a new version if the batch changed) -- no slip without a working QR;
                     2. opens the printer (a Bluetooth COM port that does not open is refused at once, before anything is sent);
                     3. sends each copy as its own job (ESC @ ... feed, partial cut) in chunks; a copy counts as SUCCESS only after its whole
                        job, cut included, was sent;
                     4. records every attempted copy (statement_prints + one audit-log entry each: batch, version, copy, print type, printer,
                        SUCCESS / FAILED, Drive file id; the time is the audit row's own). A FAILED copy does not count, so its retry is still
                        the ORIGINAL;
                     5. after a REVISED copy printed, switches the older version's QR link off.
  needs_bluetooth_warning() -- the guardrail check: Bluetooth (serial) printer + a slip longer than the owner's threshold (120 lines).

Test printer (backend "dummy", no paper): its jobs are recorded only in the sandbox database (views/thermal_print.py explains why).
The real USB / Bluetooth / network connections are finished in step 5, when the printer arrives.
"""
from __future__ import annotations

from engines.statement_version_service import (
    ensure_published, link_expiry, print_type_for, record_print, revoke_older_versions,
)
from engines.thermal_statement import build_job, build_slip, estimate_lines, send_job

BACKEND_LABELS = {"dummy": "Test printer (no paper)", "win32raw": "USB (Windows printer driver)", "serial": "Bluetooth (COM port)",
                  "network": "Network (LAN, port 9100)"}


class PrintAborted(Exception):
    """A print that could not go ahead or stopped. .kind: drive / port / midjob / setup. .done: copies that printed before it stopped."""

    def __init__(self, kind: str, message: str, done: list | None = None):
        super().__init__(message)
        self.kind, self.done = kind, done or []


def needs_bluetooth_warning(backend: str, lines: int, cfg: dict) -> bool:
    """Bluetooth guardrail: a long slip over Bluetooth is slow and a dropped connection leaves half a slip -- warn first (owner's threshold,
    default 120 lines; checked per copy, since each copy is its own job)."""
    return backend == "serial" and lines > int(cfg["thermal_bt_line_threshold"])


def open_printer(backend: str, cfg: dict):
    """The printer object for a backend. A connection that cannot be opened raises PrintAborted('port') right away, before anything is
    sent; one whose driver is not installed yet raises PrintAborted('setup') (step 5)."""
    from escpos import printer as p

    if backend == "dummy":
        return p.Dummy()
    if backend == "serial":
        if not cfg["thermal_serial_port"]:
            raise PrintAborted("setup", "No Bluetooth COM port is set (Printer settings).")
        if not p.Serial.is_usable():
            raise PrintAborted("setup", "Bluetooth printing is switched on when the printer arrives (step 5). Use the test printer for now.")
        prn = p.Serial(devfile=cfg["thermal_serial_port"], baudrate=9600, timeout=5)
    elif backend == "network":
        if not cfg["thermal_network_host"]:
            raise PrintAborted("setup", "No printer IP address is set (Printer settings).")
        prn = p.Network(host=cfg["thermal_network_host"], port=int(cfg["thermal_network_port"]), timeout=10)
    elif backend == "win32raw":
        if not p.Win32Raw.is_usable():
            raise PrintAborted("setup", "USB printing through the Xprinter driver is switched on when the printer arrives (step 5). Use the test printer for now.")
        prn = p.Win32Raw(printer_name=cfg["thermal_win32_printer"])
    else:
        raise PrintAborted("setup", f"Unknown printer setting '{backend}'.")
    try:
        prn.open()
    except Exception as e:
        where = {"serial": f"The Bluetooth port {cfg['thermal_serial_port']} did not open", "network": f"The printer at {cfg['thermal_network_host']} did not answer",
                 "win32raw": "The USB printer could not be opened"}.get(backend, "The printer could not be opened")
        raise PrintAborted("port", f"{where} ({e}). Nothing was printed." + (" Connect the USB cable and choose USB instead." if backend == "serial" else ""))
    return prn


def print_statement(statement_id: int, copies: list[str], backend: str, drive, cfg: dict, *, sandbox: bool, record: bool = True,
                    include_full_history: bool = True, opener=open_printer, today=None, slip_no: str | None = None) -> dict:
    """Print the given copies ('SUPPLIER' / 'FILE' / 'FINANCE') of a batch's statement. record=False (the test printer on the live system) prints
    without recording or auditing anything. Returns {'version', 'types', 'done', 'revoked', 'jobs'}; raises PrintAborted."""
    sid = int(statement_id)

    def fail_all(kind, msg, version, types, start=0):
        if record:
            for c in copies[start:]:
                record_print(sid, version, c, types.get(c, "ORIGINAL"), backend, "FAILED", msg)
        raise PrintAborted(kind, msg)

    try:
        v = ensure_published(sid, drive, sandbox=sandbox, today=today)              # also starts the next version if the batch changed
    except Exception as e:                                                           # DriveError / DriveNotConnected / PDF problem
        raise PrintAborted("drive", f"The QR copy could not be made, and a slip without a working QR is never printed. {e}")
    types = {c: print_type_for(sid, c, v)["print_type"] for c in copies}
    until = link_expiry(sid, v)
    try:
        prn = opener(backend, cfg)
    except PrintAborted as e:
        fail_all(e.kind, str(e), v, types)
    done, jobs, lines = [], {}, {}
    try:
        for i, c in enumerate(copies):
            slip = build_slip(sid, c, include_full_history=include_full_history, print_type=types[c], version=v["version_no"],
                              reprint_date=(today.isoformat() if today else None), qr_link=v["drive_link"], link_until=until, slip_no=slip_no)
            lines[c] = estimate_lines(slip)
            job = build_job([slip], int(cfg["thermal_qr_size"]))
            try:
                send_job(job, prn, int(cfg["thermal_chunk_bytes"]))                  # ESC @ ... feed + partial cut, in chunks
            except Exception as e:                                                   # connection dropped mid-job
                msg = (f"Printing stopped partway through the {c} copy ({e}). Tear off and throw away the partial slip, connect the USB cable, "
                       "and print again -- the retry still counts as the original.")
                if record:
                    record_print(sid, v, c, types[c], backend, "FAILED", msg)
                raise PrintAborted("midjob", msg, done=done)
            jobs[c] = job
            if record:
                record_print(sid, v, c, types[c], backend, "SUCCESS")               # only after the whole job, cut included, was sent
            done.append(c)
    finally:
        try:
            prn.close()
        except Exception:
            pass
    revoked, revoke_error = [], None
    if record and any(types[c] == "REVISED" for c in done):
        try:
            revoked = revoke_older_versions(sid, v["version_id"], drive)
        except Exception as e:                                                       # retried on the next print / the daily sweep
            revoke_error = str(e)
    return {"version": v, "types": types, "done": done, "revoked": revoked, "revoke_error": revoke_error, "jobs": jobs, "until": until,
            "lines": lines}
