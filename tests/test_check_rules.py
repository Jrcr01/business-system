

def test_check_numbers_follow_from_the_first_one_typed():
    from engines.check_rules import numbers_in_sequence
    assert numbers_in_sequence("3992554370", 3) == ["3992554370", "3992554371", "3992554372"]
    assert numbers_in_sequence("00099", 3) == ["00099", "00100", "00101"]            # the width of a leading-zero number is kept
    assert numbers_in_sequence("", 2) == ["", ""] and numbers_in_sequence("AB12", 2) == ["", ""] and numbers_in_sequence("5", 0) == []


def test_add_months_keeps_the_day_or_the_months_last_day():
    from datetime import date
    from engines.common import add_months
    assert add_months(date(2026, 10, 3)) == date(2026, 11, 3) and add_months(date(2026, 12, 15)) == date(2027, 1, 15)
    assert add_months(date(2026, 1, 31)) == date(2026, 2, 28) and add_months(date(2024, 1, 31)) == date(2024, 2, 29) and add_months(date(2026, 2, 28), 12) == date(2027, 2, 28)


def test_a_new_booklet_follows_the_last_one(db):
    import sqlite3
    from datetime import date
    from engines.common import booklet_defaults
    assert booklet_defaults(1) == {"code": "Booklet A", "start": 1000, "end": 1050}                  # nothing registered yet
    con = sqlite3.connect(db)
    con.execute("INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received) VALUES (1, 'Booklet 7', 3992554301, 3992554400, 3992554301, '2026-09-01')")
    con.commit()
    con.close()
    assert booklet_defaults(1) == {"code": "Booklet 8", "start": 3992554401, "end": 3992554401 + 99}
