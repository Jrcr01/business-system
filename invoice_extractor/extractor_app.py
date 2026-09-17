"""Phase 7 — Invoice Extraction Tool (standalone, not part of the main AP app).

Upload a batch of same-template invoices -> click-drag 3 boxes on the first one
(invoice number, date, amount) -> the learned pattern auto-extracts the rest ->
review/correct in a grid -> export a CSV shaped for the main AP system's
/api/import/invoices endpoint (not called from here — this tool's whole output
is the CSV file).

Click-drag annotation uses the streamlit-image-coordinates component's
click_and_drag=True mode (true mouse-down/mouse-up drag, not a 2-click
approximation) — streamlit-drawable-canvas, the more common canvas library for
this, doesn't work with the Streamlit version installed in this environment
(its component-registration API predates Streamlit 1.62's).
"""
import os
import shutil
import tempfile

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

from invoice_extraction_service import (
    FIELDS,
    extract_text_from_file,
    get_surrounding_text,
    learn_pattern_from_first_invoice,
    process_batch,
    process_batch_per_page,
)

st.set_page_config(page_title="Invoice Extraction Tool", page_icon=":material/receipt_long:", layout="wide")

FIELD_LABELS = {"invoice_number": "Invoice Number", "invoice_date": "Invoice Date", "total_amount": "Total Amount"}
BOX_COLORS = {"invoice_number": (214, 40, 40), "invoice_date": (29, 112, 214), "total_amount": (30, 150, 90)}
MAX_DISPLAY_WIDTH = 950
MAX_FILES = 50
REVIEW_COLUMNS = ["filename", "invoice_number", "invoice_date", "total_amount", "extracted_by", "reviewed_by", "notes"]


def _init_state() -> None:
    defaults = {
        "upload_dir": None,
        "file_paths": [],
        "first_page": None,
        "boxes": {},
        "confirmed_values": {},
        "pattern": None,
        "preview_results": None,
        "review_df": None,
        "drag_seq": 0,
        "extraction_mode": "one_per_file",
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def _reset_all() -> None:
    upload_dir = st.session_state.get("upload_dir")
    if upload_dir and os.path.isdir(upload_dir):
        shutil.rmtree(upload_dir, ignore_errors=True)
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    st.rerun()


def render_for_display(image: Image.Image, max_width: int = MAX_DISPLAY_WIDTH) -> tuple[Image.Image, float]:
    """Downscales only for on-screen display; returns (display_image, scale)
    where native_px = display_px * scale. Every box, and every word position
    from invoice_extraction_service.py, lives in native pixel space — display
    size is purely cosmetic and never stored."""
    if image.width <= max_width:
        return image, 1.0
    scale = image.width / max_width
    return image.resize((max_width, int(image.height / scale)), Image.LANCZOS), scale


def draw_boxes(display_image: Image.Image, boxes: dict, scale: float) -> Image.Image:
    """boxes are in native px; scale converts native -> this display image's
    own pixel space (the inverse of render_for_display's scale)."""
    overlay = display_image.copy()
    draw = ImageDraw.Draw(overlay)
    for field, (x1, y1, x2, y2) in boxes.items():
        dx1, dy1, dx2, dy2 = x1 / scale, y1 / scale, x2 / scale, y2 / scale
        color = BOX_COLORS.get(field, (255, 165, 0))
        draw.rectangle([dx1, dy1, dx2, dy2], outline=color, width=3)
        draw.text((dx1 + 3, max(0, dy1 - 16)), FIELD_LABELS.get(field, field), fill=color)
    return overlay


_init_state()

st.title("Invoice Extraction Tool")
st.caption(
    "Phase 7 — standalone from the main AP system. Upload a batch of invoices that all share one "
    "layout, annotate the first one, and the rest auto-extract from the pattern it learns. Export "
    "the reviewed CSV and import it into the main app separately."
)

# --------------------------------------------------------------------------
# 1. Upload
# --------------------------------------------------------------------------
st.header("1. Upload invoices")

if not st.session_state["file_paths"]:
    uploaded = st.file_uploader(
        f"Upload up to {MAX_FILES} invoice PDFs or images — the first one becomes the annotation template",
        type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True, key="uploader",
    )
    if uploaded:
        if len(uploaded) > MAX_FILES:
            st.error(f"Please upload fewer than {MAX_FILES} files (got {len(uploaded)}).")
        else:
            upload_dir = tempfile.mkdtemp(prefix="invoice_extractor_")
            paths = []
            for f in uploaded:
                p = os.path.join(upload_dir, f.name)
                with open(p, "wb") as out:
                    out.write(f.getbuffer())
                paths.append(p)
            st.session_state["upload_dir"] = upload_dir
            st.session_state["file_paths"] = paths
            st.rerun()
else:
    st.success(f"{len(st.session_state['file_paths'])} file(s) loaded.")
    with st.expander("Uploaded files"):
        st.write([os.path.basename(p) for p in st.session_state["file_paths"]])

    if st.session_state["pattern"] is None:
        st.radio(
            "How should this batch be split into invoices?",
            options=["one_per_file", "one_per_page"],
            format_func=lambda m: (
                "One invoice per file (default — each upload is a separate invoice)"
                if m == "one_per_file"
                else "One invoice per page (a PDF here holds several invoices back-to-back, e.g. a combined scan)"
            ),
            key="extraction_mode",
            help=(
                "Matches DataSnipper's per-document vs. per-page extraction toggle. Either way, you still "
                "annotate only the very first page of the very first file — this just controls whether later "
                "pages of a multi-page PDF are treated as more invoices to auto-extract, or ignored."
            ),
        )

    if st.button("Start over with a new batch", icon=":material/restart_alt:"):
        _reset_all()

# --------------------------------------------------------------------------
# 2. Annotate the first invoice
# --------------------------------------------------------------------------
if st.session_state["file_paths"] and st.session_state["pattern"] is None:
    st.header("2. Annotate the first invoice")

    if st.session_state["first_page"] is None:
        with st.spinner("Reading first invoice..."):
            doc = extract_text_from_file(st.session_state["file_paths"][0])
        if not doc["pages"]:
            st.error(doc["warning"] or "Could not read the first invoice — try a different file as the first upload.")
            st.stop()
        if doc["warning"]:
            st.warning(doc["warning"])
        st.session_state["first_page"] = doc["pages"][0]

    page = st.session_state["first_page"]
    display_img, scale = render_for_display(page["image"])
    boxes = st.session_state["boxes"]
    remaining = [f for f in FIELDS if f not in boxes]

    if remaining:
        current_field = remaining[0]
        st.info(f"Click and drag a box around the **{FIELD_LABELS[current_field]}** on the invoice below.")
        overlay = draw_boxes(display_img, boxes, scale)
        coords = streamlit_image_coordinates(overlay, key=f"drag_{st.session_state['drag_seq']}", click_and_drag=True)
        if coords and all(k in coords for k in ("x1", "y1", "x2", "y2")):
            nx1, ny1, nx2, ny2 = coords["x1"] * scale, coords["y1"] * scale, coords["x2"] * scale, coords["y2"] * scale
            box = (min(nx1, nx2), min(ny1, ny2), max(nx1, nx2), max(ny1, ny2))
            if box[2] - box[0] >= 3 and box[3] - box[1] >= 3:  # ignore an accidental click with no real drag
                st.session_state["boxes"][current_field] = box
                st.session_state["drag_seq"] += 1
                st.rerun()
    else:
        st.image(draw_boxes(display_img, boxes, scale), caption="All 3 fields boxed", width="stretch")
        st.subheader("Confirm the value found in each box")
        preview_pattern = learn_pattern_from_first_invoice(boxes, page)
        cols = st.columns(3)
        for col, field in zip(cols, FIELDS):
            box = boxes[field]
            hint = get_surrounding_text(page, *box, radius=60)
            with col:
                st.markdown(f"**{FIELD_LABELS[field]}**")
                if hint:
                    st.caption(f"Nearby text: _{hint}_")
                else:
                    st.caption("_No nearby text detected._")
                default_val = st.session_state["confirmed_values"].get(field, preview_pattern[field]["sample_value"])
                if not default_val:
                    st.warning("Couldn't read anything in this box — type the correct value in below.", icon=":material/warning:")
                st.session_state["confirmed_values"][field] = st.text_input("Value", value=default_val, key=f"confirm_{field}")

                with st.expander("Fine-tune box position"):
                    st.caption("If the drag felt laggy or landed slightly off, nudge the numbers below — the box and hint above update instantly.")
                    bx1, by1, bx2, by2 = (int(v) for v in box)
                    nc1, nc2 = st.columns(2)
                    new_x1 = nc1.number_input("Left", 0, page["width"], bx1, key=f"tune_{field}_x1")
                    new_y1 = nc1.number_input("Top", 0, page["height"], by1, key=f"tune_{field}_y1")
                    new_x2 = nc2.number_input("Right", 0, page["width"], bx2, key=f"tune_{field}_x2")
                    new_y2 = nc2.number_input("Bottom", 0, page["height"], by2, key=f"tune_{field}_y2")
                    tuned = (min(new_x1, new_x2), min(new_y1, new_y2), max(new_x1, new_x2), max(new_y1, new_y2))
                    if tuned != box:
                        st.session_state["boxes"][field] = tuned
                        st.session_state["confirmed_values"].pop(field, None)
                        st.session_state.pop(f"confirm_{field}", None)  # the text_input widget keeps its own value under this key once created; value= alone won't override it on rerun
                        st.rerun()

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Redraw boxes", icon=":material/undo:"):
                st.session_state["boxes"] = {}
                st.session_state["confirmed_values"] = {}
                st.session_state["drag_seq"] += 1
                st.rerun()
        with col_b:
            if st.button("Extract pattern", icon=":material/psychology:", type="primary"):
                st.session_state["pattern"] = preview_pattern
                st.session_state["preview_results"] = None  # force a fresh preview run below
                st.rerun()

# --------------------------------------------------------------------------
# 3. Preview extraction — like DataSnipper's live "Preview of extractions"
# panel: run the learned pattern across the WHOLE batch right away and show
# the results table before committing to anything, so a bad pattern (wrong
# box, wrong field) is obvious immediately rather than after export.
# --------------------------------------------------------------------------
if st.session_state["pattern"] is not None and st.session_state["review_df"] is None:
    st.header("3. Preview extraction")
    total_files = len(st.session_state["file_paths"])

    if st.session_state["preview_results"] is None:
        progress = st.progress(0.0, text="Starting...")

        def _on_progress(done: int, total: int, name: str) -> None:
            progress.progress(done / total if total else 1.0, text=f"{done}/{total} — {name}")

        batch_fn = process_batch_per_page if st.session_state["extraction_mode"] == "one_per_page" else process_batch
        st.session_state["preview_results"] = batch_fn(
            st.session_state["file_paths"], st.session_state["pattern"],
            first_invoice_values=st.session_state["confirmed_values"], on_progress=_on_progress,
        )
        st.rerun()

    preview_df = pd.DataFrame(st.session_state["preview_results"], columns=REVIEW_COLUMNS)
    blank_mask = preview_df[["invoice_number", "invoice_date", "total_amount"]].apply(lambda c: c.astype(str).str.strip() == "").any(axis=1)
    mode_note = (
        "each page of every file counts as its own invoice"
        if st.session_state["extraction_mode"] == "one_per_page"
        else "each file counts as one invoice (its first page only)"
    )
    st.caption(
        f"Pattern learned from {os.path.basename(st.session_state['file_paths'][0])}, applied across {total_files} "
        f"uploaded file(s) — {mode_note}. The first invoice uses your confirmed values, the rest use the learned "
        "pattern. Nothing is final yet."
    )
    m1, m2 = st.columns(2)
    m1.metric("Total invoices", len(preview_df))
    m2.metric("Rows with a blank field", int(blank_mask.sum()))
    st.dataframe(preview_df, hide_index=True, width="stretch")

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Redo annotation", icon=":material/undo:"):
            st.session_state["boxes"] = {}
            st.session_state["confirmed_values"] = {}
            st.session_state["pattern"] = None
            st.session_state["preview_results"] = None
            st.session_state["drag_seq"] += 1
            st.rerun()
    with col_b:
        if st.button("Looks good — continue to review & export", icon=":material/check_circle:", type="primary"):
            st.session_state["review_df"] = preview_df.copy()
            st.rerun()

# --------------------------------------------------------------------------
# 4. Review & export
# --------------------------------------------------------------------------
if st.session_state["review_df"] is not None:
    st.header("4. Review & export")
    df = st.session_state["review_df"]
    blank_mask = df[["invoice_number", "invoice_date", "total_amount"]].apply(lambda c: c.astype(str).str.strip() == "").any(axis=1)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total invoices", len(df))
    m2.metric("Auto-extracted", int((df["extracted_by"] == "auto").sum()))
    m3.metric("Rows with a blank field", int(blank_mask.sum()))
    if blank_mask.any():
        st.warning(f"{int(blank_mask.sum())} row(s) have at least one blank field — fill these in below before exporting.")

    reviewer_name = st.text_input("Your name (recorded as reviewed_by)", key="reviewer_name")

    edited_df = st.data_editor(
        df, hide_index=True, num_rows="fixed", key="review_editor", disabled=["filename"],
        column_config={
            "filename": st.column_config.TextColumn("File"),
            "invoice_number": st.column_config.TextColumn("Invoice Number"),
            "invoice_date": st.column_config.TextColumn("Invoice Date (YYYY-MM-DD)"),
            "total_amount": st.column_config.TextColumn("Total Amount"),
            "extracted_by": st.column_config.TextColumn("Extracted By"),
            "reviewed_by": st.column_config.TextColumn("Reviewed By"),
            "notes": st.column_config.TextColumn("Notes"),
        },
    )
    st.session_state["review_df"] = edited_df

    if st.button("Mark every row as reviewed by me", icon=":material/fact_check:", disabled=not reviewer_name):
        st.session_state["review_df"]["reviewed_by"] = reviewer_name
        st.rerun()

    st.divider()
    csv_bytes = edited_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export CSV", data=csv_bytes, file_name="invoice_extraction_export.csv", mime="text/csv",
        icon=":material/download:", type="primary",
    )
    st.caption("Import this file into the main AP system via its /api/import/invoices endpoint.")
