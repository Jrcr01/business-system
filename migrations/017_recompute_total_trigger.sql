-- Migration 017: structural safety net for order_statements.total_amount
--
-- CLAUDE.md flagged total_amount as a denormalized column whose correctness depends on every
-- invoice-mutating code path remembering to call recompute_statement_total() -- "Consider a DB
-- trigger as a safety net if this becomes a recurring bug source." Adding it now, before Phase 6
-- adds more surface area that could touch `invoices` without going through that helper.
--
-- Triggers mirror recompute_statement_total()'s exact logic (SUM(amount) over ALL invoices tied
-- to that statement_id, no status filter -- a Cancelled invoice still counts, same as the Python
-- version) so behavior doesn't change, it just becomes structurally impossible to forget. The
-- existing Python calls to recompute_statement_total() are left in place (now redundant, still
-- harmless -- belt-and-suspenders, not a reason to touch working call sites).
--
-- statement_id is never reassigned after an invoice is created (add_invoice/edit_invoice/
-- delete_invoice confirmed by reading app.py) so in practice OLD.statement_id always equals
-- NEW.statement_id on UPDATE, but the trigger recomputes both sides defensively in case that
-- ever changes.

CREATE TRIGGER IF NOT EXISTS trg_invoices_ai_recompute_total
AFTER INSERT ON invoices
WHEN NEW.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = NEW.statement_id)
    WHERE statement_id = NEW.statement_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_au_recompute_total
AFTER UPDATE ON invoices
WHEN NEW.statement_id IS NOT NULL OR OLD.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = NEW.statement_id)
    WHERE NEW.statement_id IS NOT NULL AND statement_id = NEW.statement_id;

    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = OLD.statement_id)
    WHERE OLD.statement_id IS NOT NULL AND OLD.statement_id IS NOT NEW.statement_id AND statement_id = OLD.statement_id;
END;

CREATE TRIGGER IF NOT EXISTS trg_invoices_ad_recompute_total
AFTER DELETE ON invoices
WHEN OLD.statement_id IS NOT NULL
BEGIN
    UPDATE order_statements
    SET total_amount = (SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = OLD.statement_id)
    WHERE statement_id = OLD.statement_id;
END;
