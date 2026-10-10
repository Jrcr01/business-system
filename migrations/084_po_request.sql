-- ASK THE SUPPLIER FIRST (owner, 2026-10-11: "there should be a draft first to be sent to supplier then once we received feedback from them that items are
-- available then thats the only time it will become PO"). A request is a DRAFT order with requested_at set: it has its own number (RQ-0001), and when the
-- supplier confirms, the available quantities become the purchase order while what was asked stays in qty_requested.
ALTER TABLE purchase_orders ADD COLUMN request_no TEXT;            -- 'RQ-0001' (doc_counters series 'RQ'), given when the draft is sent to the supplier
ALTER TABLE purchase_orders ADD COLUMN requested_at DATETIME;      -- when it was sent; NULL = a plain draft (or an order made before this step existed)
ALTER TABLE purchase_orders ADD COLUMN requested_by TEXT;
ALTER TABLE purchase_order_lines ADD COLUMN qty_requested REAL;    -- what was asked of the supplier, kept when the confirmed quantity is less
