-- Owner, 2026-10-17: "put also a by size", "make this editable until closed or finished": a count keeps the choices it was started with.
ALTER TABLE stock_counts ADD COLUMN scope_json TEXT;
