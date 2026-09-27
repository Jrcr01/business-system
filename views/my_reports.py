""""My reports" -- build, save and re-run your own reports (2026-09-28, plan phase 4). Shown by the Reports page (switch at the top).
The rules live in engines/report_builder.py (a fixed list of data sets and fields -- no free SQL); this file only draws the criteria,
the result and the buttons. Widget values are kept under mr_* keys; picking a saved report loads its criteria into them."""
from __future__ import annotations

import re
import sqlite3
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from engines import report_builder as rb
from engines.common import php
from views.shared import export_buttons, pdf_preview_and_download, refresh, show_table

NEW = 0
MAX_FILTERS = 6
# criteria widget keys: kept alive across runs even while their widget is hidden (Streamlit drops the value of a widget not drawn)
_CRITERIA_KEY = re.compile(r"^mr_(dataset|date_field|preset|n_days|from|to|nf|columns|g\d(_part)?|aggs|sort|desc|top|chart|name|"
                           r"f\d+_(field|op|text|vals|lo|hi|dlo|dhi))$")


def _persist() -> None:
    for k in [k for k in st.session_state if _CRITERIA_KEY.match(str(k))]:
        st.session_state[k] = st.session_state[k]


def _init_filter(i: int, field: str, ds) -> None:
    ss = st.session_state
    ss[f"mr_f{i}_field"], ss[f"mr_f{i}_op"] = field, rb.OPS[ds.fields[field].kind][0]
    ss[f"mr_f{i}_text"], ss[f"mr_f{i}_vals"] = "", []
    for s_ in ("lo", "hi", "dlo", "dhi"):
        ss[f"mr_f{i}_{s_}"] = None


@st.cache_data(ttl=60, show_spinner=False)
def _data(dataset: str) -> pd.DataFrame:
    return rb.load(dataset)


# ------------------------------------------------------------------ spec <-> widgets
def _clear_criteria() -> None:
    for k in [k for k in st.session_state if str(k).startswith("mr_") and k not in ("mr_pick",)]:
        del st.session_state[k]


def _spec_to_state(spec: dict, name: str = "") -> None:
    _clear_criteria()
    ss = st.session_state
    ss["mr_dataset"] = spec["dataset"]
    ss["mr_date_field"] = spec.get("date_field")
    ss["mr_preset"] = spec.get("preset") or "All dates"
    ss["mr_n_days"] = int(spec.get("n_days") or 30)
    ss["mr_from"] = date.fromisoformat(str(spec["date_from"])[:10]) if spec.get("date_from") else None
    ss["mr_to"] = date.fromisoformat(str(spec["date_to"])[:10]) if spec.get("date_to") else None
    filters = spec.get("filters") or []
    ss["mr_nf"] = len(filters)
    for i, f in enumerate(filters):
        _init_filter(i, f["field"], rb.DATASETS[spec["dataset"]])
        ss[f"mr_f{i}_field"], ss[f"mr_f{i}_op"] = f["field"], f["op"]
        v = f.get("value")
        if f["op"] == "contains":
            ss[f"mr_f{i}_text"] = v or ""
        elif f["op"] in ("is one of", "is not one of"):
            ss[f"mr_f{i}_vals"] = list(v or [])
        else:
            lo, hi = (list(v or []) + [None, None])[:2]
            kind = rb.DATASETS[spec["dataset"]].fields[f["field"]].kind
            if kind in ("date", "datetime"):
                ss[f"mr_f{i}_dlo"] = date.fromisoformat(str(lo)[:10]) if lo else None
                ss[f"mr_f{i}_dhi"] = date.fromisoformat(str(hi)[:10]) if hi else None
            else:
                ss[f"mr_f{i}_lo"], ss[f"mr_f{i}_hi"] = lo, hi
    ss["mr_columns"] = list(spec.get("columns") or rb.DATASETS[spec["dataset"]].default_columns)
    groups = spec.get("group_by") or []
    for i in range(rb.MAX_GROUPS):
        ss[f"mr_g{i}"] = groups[i]["field"] if i < len(groups) else None
        ss[f"mr_g{i}_part"] = (groups[i].get("part") or "month") if i < len(groups) else "month"
    ss["mr_aggs"] = [rb.agg_key(a) for a in spec.get("aggregates") or []]
    ss["mr_sort"] = (spec.get("sort") or {}).get("by")
    ss["mr_desc"] = bool((spec.get("sort") or {}).get("desc"))
    ss["mr_top"] = int(spec.get("top_n") or 0)
    ss["mr_chart"] = spec.get("chart") or "none"
    ss["mr_name"] = name


def _state_to_spec() -> dict:
    ss = st.session_state
    ds_key = ss.get("mr_dataset", "checks")
    ds = rb.DATASETS[ds_key]
    filters = []
    for i in range(int(ss.get("mr_nf", 0))):
        f, op = ss.get(f"mr_f{i}_field"), ss.get(f"mr_f{i}_op")
        if f not in ds.fields or op not in rb.OPS[ds.fields[f].kind]:
            continue
        kind = ds.fields[f].kind
        if op == "contains":
            val = ss.get(f"mr_f{i}_text", "")
        elif op in ("is one of", "is not one of"):
            val = list(ss.get(f"mr_f{i}_vals", []))
        elif kind in ("date", "datetime"):
            lo, hi = ss.get(f"mr_f{i}_dlo"), ss.get(f"mr_f{i}_dhi")
            val = [lo.isoformat() if lo else None, hi.isoformat() if hi else None]
        else:
            val = [ss.get(f"mr_f{i}_lo"), ss.get(f"mr_f{i}_hi")]
        filters.append({"field": f, "op": op, "value": val})
    groups = []
    for i in range(rb.MAX_GROUPS):
        g = ss.get(f"mr_g{i}")
        if not g or g not in ds.fields or (i == 1 and not groups):
            continue
        groups.append({"field": g, **({"part": ss.get(f"mr_g{i}_part", "month")} if ds.fields[g].kind in ("date", "datetime") else {})})
    aggs = []
    for k in ss.get("mr_aggs", []):
        _, fn, fld = k.split(":", 2)
        aggs.append({"fn": fn, "field": None if fld == "*" else fld} if fn == "count" else {"fn": fn, "field": fld})
    preset = ss.get("mr_preset", "All dates")
    return {"dataset": ds_key, "date_field": ss.get("mr_date_field") if ss.get("mr_date_field") in ds.date_fields else None,
            "preset": preset, "n_days": int(ss.get("mr_n_days") or 30),
            "date_from": ss["mr_from"].isoformat() if preset == "Custom range" and ss.get("mr_from") else None,
            "date_to": ss["mr_to"].isoformat() if preset == "Custom range" and ss.get("mr_to") else None,
            "filters": filters, "columns": [c for c in ss.get("mr_columns", ds.default_columns) if c in ds.fields] or list(ds.default_columns),
            "group_by": groups, "aggregates": aggs, "sort": {"by": ss.get("mr_sort"), "desc": bool(ss.get("mr_desc"))},
            "top_n": int(ss.get("mr_top") or 0), "chart": None if ss.get("mr_chart", "none") == "none" else ss.get("mr_chart")}


def _on_pick() -> None:
    pick = st.session_state.get("mr_pick", NEW)
    if pick == NEW:
        _spec_to_state(rb.blank_spec("checks"))
        return
    r = rb.get_saved(pick)
    if r:
        _spec_to_state(r["spec"], r["name"])
        rb.mark_run(pick)


def _on_dataset() -> None:
    ds_key = st.session_state["mr_dataset"]
    name = st.session_state.get("mr_name", "")
    _spec_to_state(rb.blank_spec(ds_key), name)


# ------------------------------------------------------------------ the criteria form
def _criteria(data: pd.DataFrame) -> None:
    ss = st.session_state
    ds = rb.DATASETS[ss["mr_dataset"]]
    fields = ds.fields
    st.markdown("**1 · Dates**")
    if ss.get("mr_date_field") not in ds.date_fields:
        ss["mr_date_field"] = ds.date_fields[0] if ds.date_fields else None
    if ds.date_fields:
        d1, d2, d3 = st.columns([1.2, 1.4, 1.4])
        d1.selectbox("Date to use", list(ds.date_fields), format_func=lambda f: fields[f].label, key="mr_date_field")
        d2.selectbox("Period", rb.PRESETS, key="mr_preset",
                     help="Relative periods (This month, Last 30 days ...) move forward by themselves each time the report runs.")
        if ss.get("mr_preset") in ("Last N days", "Next N days"):
            d3.number_input("Number of days", min_value=1, max_value=3650, step=1, key="mr_n_days")
        elif ss.get("mr_preset") == "Custom range":
            c1, c2 = d3.columns(2)
            c1.date_input("From", key="mr_from")
            c2.date_input("To", key="mr_to")
    else:
        st.caption("This data has no date to filter on.")

    st.markdown("**2 · Filters**")
    kinds_ok = [k for k in fields if fields[k].kind in rb.OPS]
    for i in range(int(ss.get("mr_nf", 0))):
        c1, c2, c3, c4 = st.columns([1.3, 1.1, 2.4, 0.4], vertical_alignment="bottom")
        f = c1.selectbox("Field", kinds_ok, format_func=lambda k: fields[k].label, key=f"mr_f{i}_field")
        kind = fields[f].kind
        ops = rb.OPS[kind]
        if ss.get(f"mr_f{i}_op") not in ops:
            ss[f"mr_f{i}_op"] = ops[0]
        op = c2.selectbox("Condition", ops, key=f"mr_f{i}_op")
        if op == "contains":
            c3.text_input("Text", key=f"mr_f{i}_text", placeholder="part of a name, number or note")
        elif op in ("is one of", "is not one of"):
            present = sorted({str(v) for v in data[f].dropna().unique()})[:1000] if f in data else []
            chosen = [str(v) for v in ss.get(f"mr_f{i}_vals", [])]
            c3.multiselect("Values", sorted(set(present) | set(chosen)), key=f"mr_f{i}_vals", placeholder="pick one or more")
        elif kind in ("date", "datetime"):
            a, b = c3.columns(2)
            a.date_input("From", key=f"mr_f{i}_dlo")
            b.date_input("To", key=f"mr_f{i}_dhi")
        else:
            a, b = c3.columns(2)
            a.number_input("At least", step=1000.0 if kind == "money" else 1.0, key=f"mr_f{i}_lo")
            b.number_input("At most", step=1000.0 if kind == "money" else 1.0, key=f"mr_f{i}_hi")
        if c4.button(":material/close:", key=f"mr_f{i}_del", help="Remove this filter"):
            _remove_filter(i)
            st.rerun()
    if int(ss.get("mr_nf", 0)) < MAX_FILTERS and st.button("Add a filter", icon=":material/filter_alt:", key="mr_add_filter"):
        n = int(ss.get("mr_nf", 0))
        _init_filter(n, next((k for k in kinds_ok if fields[k].kind == "choice"), kinds_ok[0]), ds)
        ss["mr_nf"] = n + 1
        st.rerun()

    st.markdown("**3 · Columns** (shown when the report is not grouped)")
    st.multiselect("Columns, in order", list(fields), format_func=lambda k: fields[k].label, key="mr_columns",
                   help="Pick them in the order you want them. 'Export all rows' below always has every column.")

    st.markdown("**4 · Group and total** (optional)")
    groupable = [k for k in fields if fields[k].kind in ("text", "choice", "date", "datetime")]
    if ss.get("mr_g0") not in groupable:
        ss["mr_g0"] = None
    if ss.get("mr_g1") not in groupable or ss.get("mr_g1") == ss.get("mr_g0"):
        ss["mr_g1"] = None
    g1, g2, g3, g4 = st.columns(4)
    g1.selectbox("Group by", [None] + groupable, format_func=lambda k: "— no grouping —" if k is None else fields[k].label, key="mr_g0")
    if ss.get("mr_g0") and fields[ss["mr_g0"]].kind in ("date", "datetime"):
        g2.selectbox("by", list(rb.DATE_PARTS), format_func=rb.DATE_PARTS.get, key="mr_g0_part")
    if ss.get("mr_g0"):
        g3.selectbox("Then by", [None] + [k for k in groupable if k != ss["mr_g0"]], format_func=lambda k: "—" if k is None else fields[k].label,
                     key="mr_g1")
        if ss.get("mr_g1") and fields[ss["mr_g1"]].kind in ("date", "datetime"):
            g4.selectbox("by ", list(rb.DATE_PARTS), format_func=rb.DATE_PARTS.get, key="mr_g1_part")
        agg_opts = ["agg:count:*"] + [f"agg:{fn}:{k}" for k in fields if fields[k].kind in ("money", "number") for fn in ("sum", "avg", "min", "max")]
        st.multiselect("Totals", agg_opts, format_func=lambda a: rb.agg_label(ds, {"fn": a.split(":")[1], "field": a.split(":")[2]}),
                       key="mr_aggs", placeholder="Count of rows + Sum of the first amount (default)")

    st.markdown("**5 · Sort, top N and chart**")
    s1, s2, s3, s4 = st.columns([1.6, 0.8, 0.8, 1.4], vertical_alignment="bottom")
    if ss.get("mr_g0"):
        aggs = ss.get("mr_aggs") or ["agg:count:*"] + ([f"agg:sum:{next(k for k in fields if fields[k].kind == 'money')}"]
                                                        if any(v.kind == "money" for v in fields.values()) else [])
        sort_opts = [None] + aggs
        fmt = lambda a: "Group name / date" if a is None else rb.agg_label(ds, {"fn": a.split(":")[1], "field": a.split(":")[2]})  # noqa: E731
    else:
        sort_opts = [None] + list(ss.get("mr_columns") or ds.default_columns)
        fmt = lambda k: "Date (as chosen above)" if k is None else fields[k].label  # noqa: E731
    if ss.get("mr_sort") not in sort_opts:
        ss["mr_sort"] = None
    s1.selectbox("Sort by", sort_opts, format_func=fmt, key="mr_sort")
    s2.toggle("Largest first", key="mr_desc")
    s3.number_input("Top N (0 = all)", min_value=0, max_value=100000, step=5, key="mr_top")
    s4.segmented_control("Chart", ["none", "bar", "line"], format_func={"none": "None", "bar": "Bar", "line": "Line"}.get, key="mr_chart",
                         disabled=not ss.get("mr_g0"), help="Charts show the grouped totals — group the report first.")


def _remove_filter(i: int) -> None:
    ss = st.session_state
    n = int(ss.get("mr_nf", 0))
    suffixes = ["field", "op", "text", "vals", "lo", "hi", "dlo", "dhi"]
    for j in range(i, n - 1):
        for s in suffixes:
            nxt = f"mr_f{j + 1}_{s}"
            if nxt in ss:
                ss[f"mr_f{j}_{s}"] = ss[nxt]
            else:
                ss.pop(f"mr_f{j}_{s}", None)
    for s in suffixes:
        ss.pop(f"mr_f{n - 1}_{s}", None)
    ss["mr_nf"] = n - 1


# ------------------------------------------------------------------ the result
def show_chart(r: rb.ReportResult, height: int | None = None) -> None:
    if not r.chart or r.chart["data"].empty:
        return
    c = r.chart
    enc = {"x": alt.X(f"{c['x']}:N", sort=c["x_order"], title=None, axis=alt.Axis(labelAngle=-30, labelLimit=140)),
           "y": alt.Y(f"{c['y']}:Q", title=c["y"]),
           "tooltip": [alt.Tooltip(f"{c['x']}:N"), alt.Tooltip(f"{c['y']}:Q", format=",.2f")]}
    if c["color"]:
        enc["color"] = alt.Color(f"{c['color']}:N", title=c["color"])
        enc["tooltip"].insert(1, alt.Tooltip(f"{c['color']}:N"))
        if c["type"] == "bar":
            enc["xOffset"] = f"{c['color']}:N"
    mark = alt.Chart(c["data"]).mark_bar() if c["type"] == "bar" else alt.Chart(c["data"]).mark_line(point=True)
    chart = mark.encode(**enc)
    st.altair_chart(chart.properties(height=height) if height else chart, width="stretch")


def headline_text(r: rb.ReportResult) -> str:
    label, value, kind = r.headline
    return php(value) if kind == "money" else f"{value:,.0f}" if kind == "count" else f"{value:,.2f}"


def _result(r: rb.ReportResult, slug: str) -> None:
    m1, m2 = st.columns(2)
    m1.metric(r.headline[0], headline_text(r), border=True)
    m2.metric("Matching rows", f"{r.matching_rows:,}", border=True)
    st.caption(" · ".join(r.criteria))
    show_chart(r)
    if r.table.empty:
        st.info("Nothing matches these criteria.")
    else:
        show_table(r.table, kinds=r.kinds)
    export_buttons(r.table, slug, key="mr_dl", kinds=r.kinds, what="report")
    p1, p2 = st.columns(2)
    if p1.button("Make the PDF", icon=":material/picture_as_pdf:", key="mr_pdf_btn", width="stretch"):
        st.session_state["_mr_pdf"] = (slug, rb.generate_report_pdf(r))
    with st.expander("Export all rows (plain data)", icon=":material/dataset:"):
        st.caption(f"Every one of the {r.matching_rows:,} matching row(s) with every field, before grouping, sorting or Top N — for extracting data.")
        export_buttons(r.detail_all, f"{slug}_all_rows", key="mr_dl_all", kinds=r.detail_kinds, what="all rows")
    cached = st.session_state.get("_mr_pdf")
    if cached and cached[0] == slug:
        pdf_preview_and_download(cached[1], f"{slug}.pdf", key="mr_pdf_dl", label="Report PDF")


# ------------------------------------------------------------------ the page
def render() -> None:
    st.caption("Build your own report: choose the data, the dates, filters, columns and grouping. Save it to run again with one click, "
               "pin it to the Dashboard, or add it to the overall report.")
    try:
        saved = rb.list_saved()
    except sqlite3.OperationalError:
        st.warning("My reports needs the latest database update. Close the app and run update_database.bat, then open it again.")
        return
    ss = st.session_state
    _persist()
    if "_mr_pick_next" in ss:                         # set by Save / Duplicate / the Dashboard before the picker is drawn
        ss["mr_pick"] = ss.pop("_mr_pick_next")
        _on_pick()
    if "mr_dataset" not in ss:
        _on_pick()
    opts = {NEW: "➕ New report", **{r["report_id"]: r["name"] for r in saved}}
    if ss.get("mr_pick", NEW) not in opts:
        ss["mr_pick"] = NEW
        _on_pick()
    top1, top2 = st.columns([3, 2], vertical_alignment="bottom")
    pick = top1.selectbox("Saved report", list(opts), format_func=opts.get, key="mr_pick", on_change=_on_pick)
    current = next((r for r in saved if r["report_id"] == pick), None)
    if current:
        with top2:
            a1, a2, a3 = st.columns(3)
            if a1.button("Duplicate", icon=":material/content_copy:", key="mr_dup", width="stretch"):
                ok, msg, rid = rb.duplicate_report(pick)
                if ok:
                    ss["_mr_pick_next"] = rid
                    refresh(done=msg)
                st.error(msg)
            with a2.popover("Delete", icon=":material/delete:", width="stretch"):
                st.write(f"Delete **{current['name']}**? Its criteria are removed; your data is not touched.")
                if st.button("Yes, delete it", type="primary", key="mr_del_yes"):
                    rb.delete_report(pick)
                    ss["_mr_pick_next"] = NEW
                    refresh(done=f"Deleted “{current['name']}”.")
            last = str(current.get("last_run_at") or "")[:16]
            a3.caption(f"Last run {last}" if last else "Not run yet")
        f1, f2 = st.columns(2)
        pin = f1.toggle("Pin to the Dashboard", value=bool(current["pin_dashboard"]), key=f"mr_pin_{pick}",
                        help="Shows this report's headline figure (and chart) on the Dashboard under My reports.")
        inc = f2.toggle("Include in the overall report", value=bool(current["include_in_overview"]), key=f"mr_inc_{pick}",
                        help="Adds this report to Reports → Overview (screen and PDF), run for the overview's period when it has a date.")
        if pin != bool(current["pin_dashboard"]) or inc != bool(current["include_in_overview"]):
            rb.set_flags(pick, pin_dashboard=pin, include_in_overview=inc)
            refresh(done="Saved.")

    data_opts = list(rb.DATASETS)
    st.selectbox("Report on", data_opts, format_func=lambda k: rb.DATASETS[k].label, key="mr_dataset", on_change=_on_dataset)
    st.caption(rb.DATASETS[ss["mr_dataset"]].description)
    data = _data(ss["mr_dataset"])
    with st.expander("Criteria", icon=":material/tune:", expanded=current is None):
        _criteria(data)
    spec = _state_to_spec()
    errs = rb.validate(spec)
    if errs:
        st.error(" ".join(errs))
        return
    result = rb.run(spec, data=data, title=current["name"] if current else rb.DATASETS[spec["dataset"]].label)
    slug = "".join(ch if ch.isalnum() else "_" for ch in (current["name"] if current else f"my_report_{spec['dataset']}").lower()).strip("_")
    _result(result, slug)

    st.divider()
    n1, n2, n3 = st.columns([3, 1.3, 1.3], vertical_alignment="bottom")
    n1.text_input("Report name", key="mr_name", placeholder="e.g. Checks to ACME this month")
    if current and n2.button("Save changes", icon=":material/save:", type="primary", key="mr_save", width="stretch"):
        ok, msg, rid = rb.save_report(ss.get("mr_name") or current["name"], spec, report_id=pick)
        if ok:
            refresh(done=msg)
        st.error(msg)
    if (n3 if current else n2).button("Save as a new report" if current else "Save report", icon=":material/bookmark_add:",
                                      type="secondary" if current else "primary", key="mr_save_new", width="stretch"):
        ok, msg, rid = rb.save_report(ss.get("mr_name", ""), spec)
        if ok:
            ss["_mr_pick_next"] = rid
            refresh(done=msg)
        st.error(msg)
