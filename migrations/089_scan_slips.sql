-- THE SCAN MOVES THE STOCK, THE INVOICE IS TYPED AFTER CLOSE (owner, 2026-10-11: "the barcode feature will be used only to track movements of inventory
-- in and outs. but the invoice is done manually. we record after the store close"; "we do not need POS. we just need to monitor now"). A slip scanned on
-- the Scan out page may already carry the paper invoice's number: it is offered back when the invoice is typed in the evening.
ALTER TABLE sales ADD COLUMN si_hint TEXT;
