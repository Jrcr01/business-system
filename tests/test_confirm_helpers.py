"""Confirmation pop-ups (owner, 2026-09-30: "confirmation first to avoid accidental clicks ... not consistent ... only the important ones").
A grid Save asks for a second look only for what is hard to take back: rows removed from the table, or a money value changed on an existing row.
Typing in new rows (fast data entry) and editing other columns never asks."""
import pandas as pd

from views.shared import grid_risk_lines, grid_risk_summary


def _orig():
    return pd.DataFrame({"invoice_id": [1, 2, 3], "doc_ref": ["A", "B", "C"], "gross_amount": [100.0, 200.0, 300.0], "note": ["x", "y", "z"]})


def _label(r):
    return f"{r['doc_ref']} · {r['gross_amount']:,.2f}"


def test_nothing_risky_when_only_a_new_row_is_added_or_a_non_money_column_changes():
    edited = _orig().copy()
    edited.loc[0, "note"] = "changed"                                   # not a money column
    edited = pd.concat([edited, pd.DataFrame({"invoice_id": [None], "doc_ref": ["NEW"], "gross_amount": [50.0], "note": [""]})], ignore_index=True)

    removed, changed = grid_risk_lines(_orig(), edited, "invoice_id", _label, ["gross_amount"])

    assert removed == [] and changed == []


def test_a_row_removed_from_the_table_is_listed():
    edited = _orig().iloc[[0, 2]]                                      # invoice 2 was deleted in the grid

    removed, changed = grid_risk_lines(_orig(), edited, "invoice_id", _label, ["gross_amount"])

    assert removed == ["- **B · 200.00**"] and changed == []


def test_a_changed_amount_is_listed_with_old_and_new_values():
    edited = _orig().copy()
    edited.loc[2, "gross_amount"] = 350.0

    removed, changed = grid_risk_lines(_orig(), edited, "invoice_id", _label, ["gross_amount"])

    assert removed == []
    assert changed == ["- **C · 300.00** — gross amount: 300.00 → **350.00**"]


def test_an_empty_original_is_never_risky():
    assert grid_risk_lines(_orig().iloc[0:0], _orig(), "invoice_id", _label, ["gross_amount"]) == ([], [])


def test_the_summary_says_what_will_happen_in_plain_words():
    text = grid_risk_summary("invoice(s)", ["- **B · 200.00**"], ["- **C · 300.00** — gross amount: 300.00 → **350.00**"])
    assert "1 invoice(s) will be deleted" in text and "1 amount(s) will change" in text and text.endswith("Save these changes?")
