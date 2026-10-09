"""Bank statement original files are kept 5 years (owner, 2026-10-09: "there should be a retain policy"); every line stays in the database."""
import os
from datetime import date

from engines import bank_statement_service as bs
from tests.conftest import fetchone


def test_files_older_than_5_years_go_and_each_is_audited(db, tmp_path):
    folder = tmp_path / "bank"
    folder.mkdir()
    for name in ("20210101-090000 old.xls", "20211008-090000 day_before.xls", "20211009-090000 exactly_5y.xls", "20260929-112300 new.xls"):
        (folder / name).write_bytes(b"x")
    gone = bs.prune_statement_files(today=date(2026, 10, 9), folder=str(folder))
    assert gone == ["20210101-090000 old.xls", "20211008-090000 day_before.xls"]
    assert sorted(os.listdir(folder)) == ["20211009-090000 exactly_5y.xls", "20260929-112300 new.xls"]
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BANK_FILE_EXPIRED'")[0] == 2


def test_nothing_to_do_without_the_folder(db, tmp_path):
    assert bs.prune_statement_files(folder=str(tmp_path / "none")) == []
