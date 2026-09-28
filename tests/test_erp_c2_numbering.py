"""core step C2 (2026-09-28): ERP document numbers. Owner: "like AP: 2026 - CR 0001" -- year, 2-letter code, 4 digits, restarting each
Manila year; the same counter table as AP (doc_counters), taken inside the save's locked step, never repeated."""
import threading
from datetime import datetime

import pytest

pytest.importorskip("django", reason="Django is installed only in the ERP environment (.venv)")

from engines.clock import set_fake_now  # noqa: E402


@pytest.fixture
def numbering(erp):
    from core import numbering as n

    saved = dict(n.SERIES)
    n.SERIES.clear()
    n.register("ZT", "Test document")
    yield n
    n.SERIES.clear()
    n.SERIES.update(saved)
    set_fake_now(None)


def test_numbers_look_like_ap_and_follow_each_other(numbering):
    from django.db import transaction

    set_fake_now(datetime(2031, 3, 1, 10, 0))
    with transaction.atomic():
        first = numbering.next_number("ZT")
    with transaction.atomic():
        second = numbering.next_number("ZT")
    assert first == "2031 - ZT 0001" and second == "2031 - ZT 0002"


def test_numbers_start_again_each_manila_year(numbering):
    from django.db import transaction

    set_fake_now(datetime(2032, 12, 31, 23, 30))
    with transaction.atomic():
        assert numbering.next_number("ZT") == "2032 - ZT 0001"
    set_fake_now(datetime(2033, 1, 1, 0, 30))          # 00:30 Manila on New Year = still Dec 31 in UTC
    with transaction.atomic():
        assert numbering.next_number("ZT") == "2033 - ZT 0001"


def test_a_failed_save_leaves_no_gap(numbering):
    from django.db import transaction

    set_fake_now(datetime(2034, 5, 1, 9, 0))
    with transaction.atomic():
        assert numbering.next_number("ZT") == "2034 - ZT 0001"
    with pytest.raises(ZeroDivisionError):
        with transaction.atomic():
            numbering.next_number("ZT")                 # rolled back with its failed save ...
            1 / 0
    with transaction.atomic():
        assert numbering.next_number("ZT") == "2034 - ZT 0002"   # ... so the next save gets the next free number, no gap left open


def test_outside_a_save_or_unknown_codes_are_refused(numbering):
    with pytest.raises(RuntimeError, match="inside the transaction.atomic"):
        numbering.next_number("ZT")
    from django.db import transaction

    with transaction.atomic(), pytest.raises(ValueError, match="Unknown document code"):
        numbering.next_number("QQ")


def test_codes_are_two_capital_letters_and_ap_codes_are_taken(numbering):
    for bad in ("Z", "zt", "ZT1", "12"):
        with pytest.raises(ValueError, match="2 capital letters"):
            numbering.register(bad, "x")
    with pytest.raises(ValueError, match="already used"):
        numbering.register("OS", "Something else")
    with pytest.raises(ValueError, match="already used"):
        numbering.register("ZT", "A different document")
    numbering.register("ZT", "Test document")            # the same registration twice is fine


def test_two_people_saving_at_once_never_get_the_same_number(numbering):
    from django.db import connections, transaction

    set_fake_now(datetime(2035, 7, 1, 9, 0))
    got, errors = [], []
    barrier = threading.Barrier(4)

    def save():
        try:
            barrier.wait()
            for _ in range(5):
                with transaction.atomic():
                    got.append(numbering.next_number("ZT"))
        except Exception as e:                           # pragma: no cover - reported below
            errors.append(e)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=save) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert errors == []
    assert sorted(got) == [f"2035 - ZT {i:04d}" for i in range(1, 21)]
