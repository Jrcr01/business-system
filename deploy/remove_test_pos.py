"""Removes the 5 test purchase orders JRCR made on the live system while trying the page (owner, 2026-10-11: "remove also PO made in the live" -- all 5,
with the purchase order release). A ONE-TIME step, run by hand after that release and after the backup, never by release.sh:
    on the server:  cd /srv/slicks/app/current && set -a && . /etc/slicks-erp/env && set +a && /srv/slicks/venv/bin/python deploy/remove_test_pos.py --db "$WEB_DB_PATH" [--apply]
Rehearsed 2026-10-11 on a copy of the live data: payables owed went from 257 documents to 256 (less the 13,176.50 test payable), nothing else changed.
Without --apply it only checks and prints what it would do. It refuses, changing nothing, if anything is not exactly as listed on 2026-10-11:
another order exists, an order is not New Century's or not JRCR's, the test payable has a check, a batch or a payment, or the item has other stock entries.
Everything is removed in one locked step, with one audit line naming each order."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))          # the program's folder, for engines/

EXPECTED = {4: ("2026 - PO 0001", "RECEIVED"), 5: ("DRAFT-PO-26-10-01", "DRAFT"), 6: ("DRAFT-PO-26-10-02", "DRAFT"), 7: ("2026 - PO 0002", "ORDERED"),
            8: ("DRAFT-PO-26-10-03", "DRAFT")}
SUPPLIER, MAKER = 10, "v.jrcroxas@gmail.com"
INVOICE, ITEM, MOVE = 283, 990, 1               # PO 0001's payable (SI 56009 / DR 456), the item received, and its one stock entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    from engines.common import write_tx
    with write_tx(a.db) as conn:
        def q(sql, p=()):
            return conn.execute(sql, p).fetchall()

        def tables_with(col):
            return [t for (t,) in q("SELECT name FROM sqlite_master WHERE type = 'table'") if col in [d[1] for d in q(f"PRAGMA table_info({t})")]]

        problems = []
        pos = {r[0]: r for r in q("SELECT po_id, po_no, status, creditor_id, created_by FROM purchase_orders")}
        if set(pos) != set(EXPECTED):
            problems.append(f"the orders on file are {sorted(pos)}, not {sorted(EXPECTED)}")
        for pid, (no, status) in EXPECTED.items():
            r = pos.get(pid)
            if r and (r[1], r[2], r[3], r[4]) != (no, status, SUPPLIER, MAKER):
                problems.append(f"order {pid} is now {tuple(r[1:])}, expected {(no, status, SUPPLIER, MAKER)}")
        ids = ",".join(str(i) for i in EXPECTED)
        lines = [r[0] for r in q(f"SELECT line_id FROM purchase_order_lines WHERE po_id IN ({ids})")]
        lids = ",".join(map(str, lines)) or "0"
        recs = q(f"SELECT receipt_id, po_id, invoice_id FROM po_receipts WHERE po_id IN ({ids})")
        if {r[1] for r in recs} - {4} or {r[2] for r in recs if r[2]} - {INVOICE}:
            problems.append(f"the deliveries are not only PO 0001's with payable {INVOICE}: {recs}")
        inv = q("SELECT doc_ref, dr_no, amount, status, statement_id, creditor_id FROM invoices WHERE invoice_id = ?", (INVOICE,))
        if inv and (inv[0][0], inv[0][3], inv[0][4], inv[0][5]) != ("56009", "Unpaid", None, SUPPLIER):
            problems.append(f"the test payable is not as it was (SI 56009, Unpaid, in no batch): {inv}")
        for t in tables_with("invoice_id"):
            if t in ("invoices", "po_receipts"):
                continue
            n = q(f"SELECT COUNT(*) FROM {t} WHERE invoice_id = ?", (INVOICE,))[0][0]
            if n:
                problems.append(f"{t} has {n} row(s) on the test payable (a check, a payment or a credit memo?)")
        moves = q(f"SELECT move_id, item_id, kind, qty, ref_type, ref_id FROM stock_ledger WHERE item_id = ? OR (ref_type = 'purchase_order_lines' AND ref_id IN ({lids}))", (ITEM,))
        if moves and [tuple(m) for m in moves] != [(MOVE, ITEM, "IN", 1.0, "purchase_order_lines", 64)]:
            problems.append(f"the stock entries are not the one test receipt: {moves}")
        item = q("SELECT on_hand, avg_cost FROM items WHERE id = ?", (ITEM,))
        if moves and item and float(item[0][0] or 0) != 1.0:
            problems.append(f"the item's stock on hand is {item[0][0]}, not the 1 piece of the test receipt")
        for t in tables_with("po_id") + tables_with("po_line_id"):
            if t not in ("purchase_orders", "purchase_order_lines", "po_receipts", "po_receipt_lines"):
                col = "po_id" if "po_id" in [d[1] for d in q(f"PRAGMA table_info({t})")] else "po_line_id"
                n = q(f"SELECT COUNT(*) FROM {t} WHERE {col} IN ({ids if col == 'po_id' else lids})")[0][0]
                if n:
                    problems.append(f"{t} has {n} row(s) on these orders")
        flags = q(f"SELECT flag_id, kind, detail FROM review_flags WHERE (ref_type = 'purchase_orders' AND ref_id IN ({ids})) OR (ref_type = 'invoices' AND ref_id = ?)", (INVOICE,))
        counter = q("SELECT last_no FROM doc_counters WHERE series = 'PO' AND year = 2026")

        print("Orders:", ", ".join(f"{pos[i][1]} ({pos[i][2]})" for i in sorted(pos)))
        print(f"Order lines: {len(lines)}; deliveries: {len(recs)}; test payable: {inv}; stock entries: {moves}; item now: {item}")
        print("Review lines that go with them:", [f[1] for f in flags])
        print("PO counter:", counter)
        if problems:
            print("\nNOT AS EXPECTED, nothing changed:")
            for p in problems:
                print("  -", p)
            return 1
        if not a.apply:
            print("\nAll as expected. Nothing changed (add --apply to remove them).")
            return 0
        rids = ",".join(str(r[0]) for r in recs) or "0"
        conn.execute(f"DELETE FROM po_receipt_lines WHERE receipt_id IN ({rids})")
        conn.execute(f"DELETE FROM po_receipts WHERE po_id IN ({ids})")
        conn.execute("DELETE FROM invoices WHERE invoice_id = ?", (INVOICE,))
        if moves:
            conn.execute("DELETE FROM stock_ledger WHERE move_id = ?", (MOVE,))
            conn.execute("UPDATE items SET on_hand = 0, avg_cost = 0 WHERE id = ?", (ITEM,))        # as it was before the test receipt
        conn.execute(f"DELETE FROM review_flags WHERE flag_id IN ({','.join(str(f[0]) for f in flags) or '0'})")
        conn.execute(f"DELETE FROM purchase_order_lines WHERE po_id IN ({ids})")
        conn.execute(f"DELETE FROM purchase_orders WHERE po_id IN ({ids})")
        conn.execute("UPDATE doc_counters SET last_no = 0 WHERE series = 'PO' AND year = 2026")
        text = ("Test purchase orders removed with the purchase order release, on the owner's instruction (2026-10-11): "
                + "; ".join(f"{pos[i][1]} ({pos[i][2]})" for i in sorted(pos))
                + f". With 2026 - PO 0001: its test payable SI 56009 / DR 456, 13,176.50 (invoice {INVOICE}, unpaid, in no batch, no check) and its stock entry "
                  f"(+1 pc, item {ITEM}; stock on hand and average cost back to 0). {len(flags)} review line(s) about these orders removed. The PO counter starts again at 0001.")
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (?, 'PO_TEST_ORDERS_REMOVED', 'purchase_orders', NULL, ?)",
                     (MAKER, text))
        print("\nREMOVED.", text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
