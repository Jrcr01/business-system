"""AppTest-based check for the fine-tune-box-position value refresh: pre-seeds
session_state with a completed annotation (as if the user had already dragged
3 boxes), then drives the fine-tune number_input widgets programmatically and
asserts the confirmed-value text_input actually refreshes to match the new
box position. Deterministic and avoids browser/component automation flakiness
that plain click-drag testing hit for this specific interaction."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from streamlit.testing.v1 import AppTest
from test_extraction import make_invoice_pdf
from invoice_extraction_service import extract_text_from_file, FIELDS

FAILURES = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def main():
    tmpdir = tempfile.mkdtemp(prefix="finetune_test_")
    path = os.path.join(tmpdir, "invoice_0.pdf")
    make_invoice_pdf(path, "INV-2024-301", "2026-05-01", "3,000.00")

    doc = extract_text_from_file(path)
    page = doc["pages"][0]

    def find_word_box(target):
        for w in page["words"]:
            if w["text"] == target:
                return (w["x0"] - 2, w["y0"] - 2, w["x1"] + 2, w["y1"] + 2)
        raise AssertionError(f"word {target!r} not found")

    # Deliberately draw the Total Amount box a bit too wide, so it also
    # catches the "Due:" label to its left -- same real-world case the live
    # browser test hit.
    due_box = find_word_box("Due:")
    amt_box = find_word_box("3,000.00")
    wide_box = (due_box[0], min(due_box[1], amt_box[1]), amt_box[2], max(due_box[3], amt_box[3]))

    boxes = {
        "invoice_number": find_word_box("INV-2024-301"),
        "invoice_date": find_word_box("2026-05-01"),
        "total_amount": wide_box,
    }

    at = AppTest.from_file("extractor_app.py")
    at.session_state["upload_dir"] = tmpdir
    at.session_state["file_paths"] = [path]
    at.session_state["first_page"] = page
    at.session_state["boxes"] = boxes
    at.session_state["confirmed_values"] = {}
    at.session_state["pattern"] = None
    at.session_state["preview_results"] = None
    at.session_state["review_df"] = None
    at.session_state["drag_seq"] = 0
    at.run(timeout=30)
    check("App renders with no exception (initial)", not at.exception, str(at.exception))

    before_value = at.session_state["confirmed_values"].get("total_amount")
    check("Total Amount initially includes 'Due:'", before_value is not None and "Due:" in before_value, repr(before_value))
    print(f"    before fine-tune: confirmed_values['total_amount'] = {before_value!r}")

    # Find the fine-tune number_inputs for total_amount by label, then push
    # Left inward past "Due:" -- exactly the live-browser reproduction case.
    number_inputs = at.number_input
    left_widget = None
    for ni in number_inputs:
        if ni.key == "tune_total_amount_x1":
            left_widget = ni
            break
    check("Found the total_amount Left fine-tune widget", left_widget is not None)
    if left_widget is None:
        print(f"    available number_input keys: {[ni.key for ni in number_inputs]}")
        sys.exit(1)

    new_left = int(amt_box[0]) - 2  # just before the amount itself, past "Due:"
    left_widget.set_value(new_left).run(timeout=30)
    check("App renders with no exception (after fine-tune)", not at.exception, str(at.exception))

    after_value = at.session_state["confirmed_values"].get("total_amount")
    print(f"    after fine-tune:  confirmed_values['total_amount'] = {after_value!r}")
    check("Box actually moved in session_state", at.session_state["boxes"]["total_amount"][0] == new_left)
    check("Confirmed value no longer contains 'Due:'", after_value is not None and "Due:" not in after_value, repr(after_value))
    check("Confirmed value updated to the plain amount", after_value is not None and "3,000.00" in after_value, repr(after_value))
    check("Confirmed value actually changed from before", after_value != before_value, f"before={before_value!r} after={after_value!r}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
