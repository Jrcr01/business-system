"""fk_check.py (Phase F1 step 1, 2026-09-28): finds rows pointing at records that don't exist, on a COPY -- the real file is untouched."""
import hashlib
import sqlite3

from tools.fk_check import check


def test_finds_a_broken_link_and_leaves_the_file_untouched(tmp_path):
    p = tmp_path / "t.db"
    with sqlite3.connect(p) as c:
        c.executescript("CREATE TABLE a (id INTEGER PRIMARY KEY); CREATE TABLE b (id INTEGER PRIMARY KEY, a_id INTEGER REFERENCES a(id));"
                        "INSERT INTO a VALUES (1); INSERT INTO b VALUES (1, 1), (2, 99);")
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    assert check(str(p)) == [{"table": "b", "rowid": 2, "parent": "a", "column": "a_id", "value": 99}]
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_the_real_schema_starts_clean(db):
    assert check(db) == []
