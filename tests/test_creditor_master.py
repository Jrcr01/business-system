"""Creditor Master's grid-backing functions (2026-09-14 consolidation): add,
full-field update including the type-change safeguard, and the
delete/deactivate/reactivate trio."""
import sqlite3

import app
from conftest import fetchone


def test_add_creditor_inserts_active_by_default(db):
    creditor_id = app.add_creditor("Acme Corp", "SUPPLIER", "123-TIN", "Juan", "0912", "a@a.com", "30")

    row = fetchone(db, "SELECT creditor_name, creditor_type, terms, is_active FROM creditors WHERE creditor_id = ?", (creditor_id,))
    assert row == ("Acme Corp", "SUPPLIER", "30", 1)


def test_update_creditor_changes_all_fields_including_type_when_no_history(db):
    creditor_id = app.add_creditor("Acme Corp", "SUPPLIER", "", "", "", "", "")

    app.update_creditor(creditor_id, "Acme Corp Renamed", "LENDER", "T1", "Juan", "0912", "a@a.com", "60")

    row = fetchone(db, "SELECT creditor_name, creditor_type, tin, terms FROM creditors WHERE creditor_id = ?", (creditor_id,))
    assert row == ("Acme Corp Renamed", "LENDER", "T1", "60")


def test_creditor_reference_counts_empty_for_unused_creditor(db):
    creditor_id = app.add_creditor("Unused Co", "SUPPLIER", "", "", "", "", "")
    assert app.creditor_reference_counts(creditor_id) == {}


def test_creditor_reference_counts_detects_order_statement(db):
    creditor_id = app.add_creditor("Used Co", "SUPPLIER", "", "", "", "", "")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'REF-1', 0, 'Draft')",
        (creditor_id,),
    )
    conn.commit()
    conn.close()

    refs = app.creditor_reference_counts(creditor_id)

    assert refs == {"order_statements": 1}


def test_delete_or_deactivate_hard_deletes_when_no_history(db):
    creditor_id = app.add_creditor("Orphan Co", "SUPPLIER", "", "", "", "", "")

    ok, msg = app.delete_or_deactivate_creditor(creditor_id)

    assert ok
    assert "deleted outright" in msg
    assert fetchone(db, "SELECT * FROM creditors WHERE creditor_id = ?", (creditor_id,)) is None


def test_delete_or_deactivate_soft_deletes_when_history_exists(db):
    creditor_id = app.add_creditor("History Co", "SUPPLIER", "", "", "", "", "")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'REF-2', 0, 'Draft')",
        (creditor_id,),
    )
    conn.commit()
    conn.close()

    ok, msg = app.delete_or_deactivate_creditor(creditor_id)

    assert ok
    assert "marked inactive" in msg
    row = fetchone(db, "SELECT is_active FROM creditors WHERE creditor_id = ?", (creditor_id,))
    assert row == (0,)  # row still exists, just deactivated


def test_reactivate_creditor_sets_is_active_1(db):
    creditor_id = app.add_creditor("Reactivate Co", "SUPPLIER", "", "", "", "", "")
    app.deactivate_creditor(creditor_id)
    assert fetchone(db, "SELECT is_active FROM creditors WHERE creditor_id = ?", (creditor_id,)) == (0,)

    app.reactivate_creditor(creditor_id)

    assert fetchone(db, "SELECT is_active FROM creditors WHERE creditor_id = ?", (creditor_id,)) == (1,)


def test_deactivate_creditor_never_hard_deletes_even_without_history(db):
    """The important distinction from delete_or_deactivate_creditor(): a plain
    checkbox-uncheck in the grid must always be reversible, never a hard delete,
    regardless of whether the creditor has any transaction history."""
    creditor_id = app.add_creditor("No History Co", "SUPPLIER", "", "", "", "", "")

    app.deactivate_creditor(creditor_id)

    row = fetchone(db, "SELECT is_active FROM creditors WHERE creditor_id = ?", (creditor_id,))
    assert row == (0,)  # still exists (would be None if hard-deleted)
