"""Party rules (core step C3): the name key, AP roles, linking AP's creditors, who may see which party."""
import re

from django.db import transaction
from django.db.models import Q

from core import audit
from core.models import Creditor, Party, PartyAPLink, Role

AP_ROLE = {"SUPPLIER": "Supplier", "LENDER": "Lender", "BANK_LOAN": "Bank loan", "CREDIT_CARD": "Card"}
# role filter chips on the Parties page; Sales sees only the first two
FILTERS = {"customers": "Customers", "affiliates": "Affiliates", "suppliers": "Suppliers", "lenders": "Lenders", "banks": "Banks and cards"}
SALES_FILTERS = ("customers", "affiliates")


def name_key(name: str) -> str:
    """How two names are compared: case, extra spaces and trailing dots don't matter ("S&P Tires " = "s&p tires")."""
    return re.sub(r"\s+", " ", (name or "").strip().lower()).rstrip(".").strip()


def roles(party: Party) -> list[str]:
    out = []
    if party.is_affiliate:
        out.append("Affiliate")
    if party.is_customer:
        out.append("Customer")
    for t in sorted({link.creditor.creditor_type for link in party.ap_links.all()}):
        out.append(AP_ROLE.get(t, t.title()))
    return out


def visible_to(user):
    """Parties this user may see: the owner everyone; Sales customers and affiliates only; nobody else."""
    qs = Party.objects.prefetch_related("ap_links__creditor")
    if user.role == Role.OWNER:
        return qs
    if user.role == Role.SALES:
        return qs.filter(Q(is_customer=True) | Q(is_affiliate=True))
    return qs.none()


def find_same_name(name: str, exclude_pk=None):
    qs = Party.objects.filter(name_key=name_key(name))
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.first()


def sync_ap_links() -> int:
    """Link every AP creditor that has no party yet: to the party with the same name if there is one, else to a new party.
    Returns how many were linked. Safe to run any time (runs when the owner opens Parties, and by `manage.py sync_ap_parties`)."""
    linked = 0
    unlinked = list(Creditor.objects.filter(party_link__isnull=True).order_by("creditor_id"))
    if not unlinked:
        return 0
    with transaction.atomic():
        for cr in unlinked:
            if PartyAPLink.objects.filter(creditor_id=cr.creditor_id).exists():     # someone linked it meanwhile
                continue
            party = find_same_name(cr.creditor_name)
            created = party is None
            if created:
                party = Party.objects.create(name=cr.creditor_name.strip(), name_key=name_key(cr.creditor_name))
            PartyAPLink.objects.create(party=party, creditor=cr)
            audit.log("PARTY_AP_LINKED", "core_party", party.pk,
                      new=f"AP {AP_ROLE.get(cr.creditor_type, cr.creditor_type)} #{cr.creditor_id} {cr.creditor_name}"
                          + (" (new party)" if created else " (same name as an existing party)"))
            linked += 1
    return linked


def unlink_to_own_party(link: PartyAPLink) -> Party:
    """Owner fix for a wrong same-name match: move this AP record to a party of its own."""
    with transaction.atomic():
        old = link.party
        cr = link.creditor
        new = Party.objects.create(name=f"{cr.creditor_name.strip()} (AP #{cr.creditor_id})",
                                   name_key=name_key(f"{cr.creditor_name} (AP #{cr.creditor_id})"))
        link.party = new
        link.save(update_fields=["party"])
        audit.log("PARTY_AP_MOVED", "core_party", new.pk, old=f"was with {old.name} (party #{old.pk})",
                  new=f"AP #{cr.creditor_id} {cr.creditor_name} now its own party")
    return new
