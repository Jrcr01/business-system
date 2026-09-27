"""Thermal Statement Printing, step 4: every copy printed goes through print_statement() -- audit entries, SUCCESS only after the whole
job, a FAILED copy does not count (the retry is still the ORIGINAL), the Bluetooth guardrail, and a port that does not open."""
import sqlite3
from datetime import date

import pytest

import app
from conftest import make_creditor, make_statement
from engines.statement_rules import lock_statement, unlock_statement
from engines.statement_version_service import print_type_for
from engines.thermal_print_service import PrintAborted, needs_bluetooth_warning, open_printer, print_statement
from engines.thermal_statement import PRINTER_DEFAULTS, get_printer_config
from test_statement_versions_drive import FakeDrive


class FakePrinter:
    """Collects what is sent; can fail when opened or after `fail_after` jobs (a Bluetooth connection dropping mid-job)."""

    def __init__(self, fail_after=None):
        self.jobs, self.fail_after, self.closed = [], fail_after, False

    def _raw(self, data):
        if data.startswith(b"\x1b@"):                                  # a new copy (job) starts
            if self.fail_after is not None and len(self.jobs) >= self.fail_after:
                raise OSError("connection lost")
            self.jobs.append(b"")
        self.jobs[-1] += data

    def close(self):
        self.closed = True


def _batch(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 1000.0, [], sid)
    return creditor_id, sid


def _cfg(**over):
    cfg = get_printer_config()
    cfg.update(over)
    return cfg


def _audit(db, action):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute("SELECT new_values FROM audit_logs WHERE user_action = ? ORDER BY log_id", (action,))]


def _prints(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT copy_type, print_type, backend, status FROM statement_prints ORDER BY print_id").fetchall()


def test_one_click_prints_each_copy_once_with_one_audit_entry_each(db):
    _, sid = _batch(db)
    prn = FakePrinter()
    r = print_statement(sid, ["FILE", "SUPPLIER"], "network", FakeDrive(), _cfg(), sandbox=True, opener=lambda b, c: prn)
    assert r["done"] == ["FILE", "SUPPLIER"] and r["types"] == {"FILE": "ORIGINAL", "SUPPLIER": "ORIGINAL"}
    assert len(prn.jobs) == 2 and all(j.startswith(b"\x1b@") and b"\x1dV" in j for j in prn.jobs) and prn.closed   # init ... cut, per copy
    assert _prints(db) == [("FILE", "ORIGINAL", "network", "SUCCESS"), ("SUPPLIER", "ORIGINAL", "network", "SUCCESS")]
    entries = _audit(db, "STATEMENT_PRINTED")
    assert len(entries) == 2 and "v1 FILE COPY, ORIGINAL, printer network, SUCCESS, Drive file file1" in entries[0]


def test_the_test_printer_on_the_live_system_records_nothing(db):
    _, sid = _batch(db)
    r = print_statement(sid, ["FILE"], "dummy", FakeDrive(), _cfg(), sandbox=False, record=False)
    assert r["done"] == ["FILE"] and r["jobs"]["FILE"].startswith(b"\x1b@")
    assert _prints(db) == [] and _audit(db, "STATEMENT_PRINTED") == []


def test_a_port_that_does_not_open_prints_nothing_and_the_retry_is_still_the_original(db):
    _, sid = _batch(db)

    def closed_port(backend, cfg):
        raise PrintAborted("port", "The Bluetooth port COM5 did not open (timeout). Nothing was printed. Connect the USB cable and choose USB instead.")

    with pytest.raises(PrintAborted) as e:
        print_statement(sid, ["FILE", "SUPPLIER"], "serial", FakeDrive(), _cfg(), sandbox=True, opener=closed_port)
    assert e.value.kind == "port" and "USB" in str(e.value)
    assert [p[3] for p in _prints(db)] == ["FAILED", "FAILED"] and len(_audit(db, "STATEMENT_PRINT_FAILED")) == 2
    r = print_statement(sid, ["FILE", "SUPPLIER"], "network", FakeDrive(), _cfg(), sandbox=True, opener=lambda b, c: FakePrinter())
    assert r["types"] == {"FILE": "ORIGINAL", "SUPPLIER": "ORIGINAL"}                         # failed attempts never count


def test_a_connection_lost_mid_job_says_to_discard_the_partial_slip_and_retry(db):
    _, sid = _batch(db)
    with pytest.raises(PrintAborted) as e:
        print_statement(sid, ["FILE", "SUPPLIER"], "serial", FakeDrive(), _cfg(), sandbox=True, opener=lambda b, c: FakePrinter(fail_after=1))
    assert e.value.kind == "midjob" and e.value.done == ["FILE"] and "throw away the partial slip" in str(e.value)
    assert _prints(db) == [("FILE", "ORIGINAL", "serial", "SUCCESS"), ("SUPPLIER", "ORIGINAL", "serial", "FAILED")]
    assert print_type_for(sid, "FILE")["print_type"] == "REPRINT" and print_type_for(sid, "SUPPLIER")["print_type"] == "ORIGINAL"


def test_a_revised_print_switches_the_old_qr_off(db):
    creditor_id, sid = _batch(db)
    drive = FakeDrive()
    first = print_statement(sid, ["FILE", "SUPPLIER"], "network", drive, _cfg(), sandbox=True, opener=lambda b, c: FakePrinter())
    unlock_statement(sid)
    app.add_invoice(creditor_id, "INV-2", date(2026, 9, 2), date(2026, 10, 2), 50.0, [], sid)
    lock_statement(sid)
    r = print_statement(sid, ["FILE", "SUPPLIER"], "network", drive, _cfg(), sandbox=True, opener=lambda b, c: FakePrinter())
    assert r["types"] == {"FILE": "REVISED", "SUPPLIER": "REVISED"} and r["revoked"] == [1]
    assert first["version"]["drive_file_id"] not in drive.public


def test_no_qr_copy_means_no_print_at_all(db):
    _, sid = _batch(db)
    prn = FakePrinter()
    with pytest.raises(PrintAborted) as e:
        print_statement(sid, ["FILE"], "network", FakeDrive(fail_upload=True), _cfg(), sandbox=True, opener=lambda b, c: prn)
    assert e.value.kind == "drive" and prn.jobs == [] and _prints(db) == []


def test_bluetooth_guardrail_and_printer_setup_messages(db):
    cfg = _cfg()
    assert int(PRINTER_DEFAULTS["thermal_bt_line_threshold"]) == 120
    assert needs_bluetooth_warning("serial", 121, cfg) and not needs_bluetooth_warning("serial", 120, cfg)
    assert not needs_bluetooth_warning("win32raw", 500, cfg) and not needs_bluetooth_warning("network", 500, cfg)
    with pytest.raises(PrintAborted) as e:
        open_printer("serial", _cfg(thermal_serial_port=""))
    assert e.value.kind == "setup" and "COM port" in str(e.value)
    with pytest.raises(PrintAborted) as e:
        open_printer("network", _cfg(thermal_network_host="127.0.0.1", thermal_network_port=1))     # nothing listens on port 1
    assert e.value.kind == "port" and "did not answer" in str(e.value)
    assert open_printer("dummy", cfg).__class__.__name__ == "Dummy"
