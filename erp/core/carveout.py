"""Carve-out rules (core step C4): which AP records can be tagged, their tag and split, and the Business / Rental / Family summary.

* AP records are read with plain SQL (AP owns them; never changed from here). Card numbers: only the last 4 digits are ever read.
* A record without a Tagging row is 100% Business · General (owner: "all Business by default").
* Tagged: the main tag gets the record's amount minus the split parts, so the parts always add up to the record's total -- even when
  the total changes later in AP. If the parts ever exceed a (lowered) total, the record is flagged "split needs a look"."""
from datetime import date
from decimal import Decimal

from django.db import connection, transaction

from core import audit
from core.models import RecordType, Tag, TagGroup, Tagging, TagSplit

# record type -> SQL returning (id, label, payee, date 'YYYY-MM-DD', amount) for one month ('YYYY-MM'), cancelled records left out
_SOURCES = {
    RecordType.CC_STATEMENT: """
        SELECT s.cc_statement_id, c.card_name || ' ••' || c.card_number_last4, cr.creditor_name, s.due_date, s.total_amount_due
        FROM cc_statements s JOIN credit_cards c ON c.card_id = s.card_id JOIN creditors cr ON cr.creditor_id = c.creditor_id
        WHERE s.status != 'Cancelled' AND COALESCE(s.statement_month, SUBSTR(s.due_date, 1, 7)) = %s""",
    RecordType.LOAN_NOTE: """
        SELECT n.loan_note_id, n.note_ref, cr.creditor_name, n.issue_date, COALESCE(n.principal_amount, n.total_payable, 0)
        FROM loan_notes n JOIN creditors cr ON cr.creditor_id = n.creditor_id
        WHERE n.status != 'Cancelled' AND SUBSTR(n.issue_date, 1, 7) = %s""",
    RecordType.BANK_LOAN: """
        SELECT b.bank_loan_id, b.loan_ref, cr.creditor_name, b.start_date, COALESCE(b.principal_amount, 0)
        FROM bank_loans b JOIN creditors cr ON cr.creditor_id = b.creditor_id
        WHERE b.is_cancelled = 0 AND SUBSTR(b.start_date, 1, 7) = %s""",
    RecordType.BATCH: """
        SELECT o.statement_id, o.statement_ref, cr.creditor_name, DATE(o.created_at, '+8 hours'), o.total_amount
        FROM order_statements o JOIN creditors cr ON cr.creditor_id = o.creditor_id
        WHERE o.status != 'Cancelled' AND SUBSTR(DATE(o.created_at, '+8 hours'), 1, 7) = %s""",
}
_ONE = {  # record type -> SQL for one record's (label, amount), by id
    RecordType.CC_STATEMENT: "SELECT c.card_name || ' ••' || c.card_number_last4, s.total_amount_due FROM cc_statements s "
                             "JOIN credit_cards c ON c.card_id = s.card_id WHERE s.cc_statement_id = %s",
    RecordType.LOAN_NOTE: "SELECT note_ref, COALESCE(principal_amount, total_payable, 0) FROM loan_notes WHERE loan_note_id = %s",
    RecordType.BANK_LOAN: "SELECT loan_ref, COALESCE(principal_amount, 0) FROM bank_loans WHERE bank_loan_id = %s",
    RecordType.BATCH: "SELECT statement_ref, total_amount FROM order_statements WHERE statement_id = %s",
}


def _day(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def default_tag() -> Tag:
    return Tag.objects.get(is_default=True)


def record(record_type: str, record_id: int) -> dict | None:
    with connection.cursor() as cur:
        cur.execute(_ONE[record_type], [record_id])
        row = cur.fetchone()
    return {"type": record_type, "id": record_id, "label": row[0], "amount": money(row[1])} if row else None


def parts(amount: Decimal, tagging: Tagging | None) -> list[tuple[Tag, Decimal]]:
    """The record's pesos per tag: the main tag first (amount minus the splits), then each split."""
    if tagging is None:
        return [(default_tag(), amount)]
    splits = [(s.tag, s.amount) for s in tagging.splits.select_related("tag").order_by("tag__group", "tag__name")]
    return [(tagging.main_tag, amount - sum((a for _, a in splits), Decimal("0")))] + splits


def records(record_type: str, month: str) -> list[dict]:
    """The month's records of one type with their tag parts (for the Tag records page)."""
    with connection.cursor() as cur:
        cur.execute(_SOURCES[record_type] + " ORDER BY 4, 2", [month])
        rows = cur.fetchall()
    taggings = {t.record_id: t for t in Tagging.objects.filter(record_type=record_type, record_id__in=[r[0] for r in rows])
                .select_related("main_tag").prefetch_related("splits__tag")}
    out = []
    for rid, label, payee, day, amount in rows:
        t = taggings.get(rid)
        ps = parts(money(amount), t)
        out.append({"id": rid, "label": label, "payee": payee, "date": _day(day), "amount": money(amount), "tagging": t, "parts": ps,
                    "main": ps[0][0], "needs_look": ps[0][1] < 0, "split": len(ps) > 1})
    return out


def summary(month: str) -> dict:
    """Pesos per record type and group for one month, plus per sub-tag. Untagged = Business · General."""
    groups = [g for g, _ in TagGroup.choices]
    by_type, by_tag = {}, {}
    totals = {g: Decimal("0") for g in groups}
    flagged = 0
    for rt, label in RecordType.choices:
        row = {g: Decimal("0") for g in groups}
        for r in records(rt, month):
            flagged += r["needs_look"]
            for tag, amount in r["parts"]:
                row[tag.group] += amount
                by_tag[tag] = by_tag.get(tag, Decimal("0")) + amount
        row["total"] = sum(row[g] for g in groups)
        by_type[label] = row
        for g in groups:
            totals[g] += row[g]
    totals["total"] = sum(totals[g] for g in groups)
    tags = sorted(by_tag.items(), key=lambda kv: (groups.index(kv[0].group), kv[0].name))
    return {"by_type": by_type, "totals": totals, "by_tag": tags, "flagged": flagged}


def set_main_tag(record_type: str, record_id: int, tag: Tag) -> None:
    """Change a record's main tag (its splits stay)."""
    rec = record(record_type, record_id)
    if rec is None:
        raise ValueError("That record is not in the AP system.")
    if not tag.is_active:
        raise ValueError(f"{tag} is deactivated.")
    with transaction.atomic():
        t = Tagging.objects.filter(record_type=record_type, record_id=record_id).first()
        old = str(t.main_tag) if t else str(default_tag()) + " (never tagged)"
        if t is None:
            t = Tagging.objects.create(record_type=record_type, record_id=record_id, main_tag=tag)
        elif t.main_tag_id == tag.pk:
            return
        else:
            t.splits.filter(tag=tag).delete()           # the new main tag can't also be a split part
            t.main_tag = tag
            t.save(update_fields=["main_tag"])
        audit.log("TAG_SET", f"carveout_{record_type}", record_id, old=old, new=f"{rec['label']}: {tag}")


def set_split(record_type: str, record_id: int, main: Tag, moved: list[tuple[Tag, Decimal]]) -> None:
    """Replace a record's tagging with: main tag + these peso parts moved to other tags. The parts must leave something for the main
    tag (a record wholly in another tag = change its main tag instead)."""
    rec = record(record_type, record_id)
    if rec is None:
        raise ValueError("That record is not in the AP system.")
    moved = [(t, money(a)) for t, a in moved if money(a) != 0]
    tags = [t for t, _ in moved]
    if any(a <= 0 for _, a in moved):
        raise ValueError("Each amount moved must be more than zero.")
    if main in tags or len(set(tags)) != len(tags):
        raise ValueError("Each tag can appear only once, and not as both the main tag and a part.")
    if any(not t.is_active for t in [main] + tags):
        raise ValueError("A deactivated tag can't be used.")
    if sum((a for _, a in moved), Decimal("0")) >= rec["amount"]:
        raise ValueError(f"The parts moved must add up to less than the total (PHP {rec['amount']:,.2f}); "
                         "if all of it belongs elsewhere, change the main tag instead.")
    with transaction.atomic():
        t = Tagging.objects.filter(record_type=record_type, record_id=record_id).first()
        old = "; ".join(f"{tag}: {a:,.2f}" for tag, a in parts(rec["amount"], t))
        if t is None:
            t = Tagging.objects.create(record_type=record_type, record_id=record_id, main_tag=main)
        else:
            t.main_tag = main
            t.save(update_fields=["main_tag"])
            t.splits.all().delete()
        for tag, a in moved:
            TagSplit.objects.create(tagging=t, tag=tag, amount=a)
        t.refresh_from_db()
        audit.log("TAG_SPLIT", f"carveout_{record_type}", record_id, old=old,
                  new=f"{rec['label']}: " + "; ".join(f"{tag}: {a:,.2f}" for tag, a in parts(rec["amount"], t)))
