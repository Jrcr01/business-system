"""Parties pages (core step C3, mock-ups approved 2026-09-28). Owner: everyone; Sales: customers and affiliates only, can add customers
and edit contact details; only the owner sets credit, terms, affiliate and active. Every change goes to the audit log."""
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core import audit
from core import parties as rules
from core.models import Party, Role
from core.party_forms import PartyCreditForm, PartyDetailsForm
from core.roles import role_required


def _party_or_404(request, pk):
    return get_object_or_404(rules.visible_to(request.user), pk=pk)


def _form_errors_to_messages(request, form):
    for errs in form.errors.values():
        for e in errs:
            messages.error(request, e)


@role_required(Role.OWNER, Role.SALES)
@require_safe
def parties(request):
    if request.user.is_owner:
        rules.sync_ap_links()                          # new AP creditors appear by themselves
    allowed = rules.FILTERS if request.user.is_owner else {k: rules.FILTERS[k] for k in rules.SALES_FILTERS}
    show = request.GET.get("show", "")
    search = (request.GET.get("q") or "").strip()
    qs = rules.visible_to(request.user)
    if show == "customers":
        qs = qs.filter(is_customer=True)
    elif show == "affiliates":
        qs = qs.filter(is_affiliate=True)
    elif show in ("suppliers", "lenders", "banks") and request.user.is_owner:
        types = {"suppliers": ["SUPPLIER"], "lenders": ["LENDER"], "banks": ["BANK_LOAN", "CREDIT_CARD"]}[show]
        qs = qs.filter(ap_links__creditor__creditor_type__in=types)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(contact_number__icontains=search))
    inactive = request.GET.get("inactive") == "1"
    if not inactive:
        qs = qs.filter(is_active=True)
    rows = [{"p": p, "roles": rules.roles(p)} for p in qs.distinct().order_by("-is_walk_in", "name")]
    return render(request, "core/parties.html", {"rows": rows, "filters": allowed, "show": show, "q": search, "inactive": inactive})


@role_required(Role.OWNER, Role.SALES)
@require_http_methods(["GET", "HEAD", "POST"])
def party_add(request):
    form = PartyDetailsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            p = Party.objects.create(name=d["name"], name_key=rules.name_key(d["name"]), is_customer=True,
                                     contact_number=d["contact_number"], address=d["address"])
            audit.log("PARTY_ADDED", "core_party", p.pk, new=f"customer {p.name}")
        messages.success(request, f"{p.name} is added as a customer.")
        return redirect("party_detail", pk=p.pk)
    return render(request, "core/party_add.html", {"form": form})


@role_required(Role.OWNER, Role.SALES)
@require_safe
def party_detail(request, pk):
    p = _party_or_404(request, pk)
    return render(request, "core/party_detail.html", {
        "p": p, "roles": rules.roles(p),
        "links": [{"c": link.creditor, "role": rules.AP_ROLE.get(link.creditor.creditor_type, link.creditor.creditor_type)}
                  for link in p.ap_links.select_related("creditor").order_by("creditor_id")] if request.user.is_owner else [],
    })


@role_required(Role.OWNER, Role.SALES)
@require_POST
def party_details(request, pk):
    p = _party_or_404(request, pk)
    if p.is_walk_in:
        messages.error(request, "The Walk-in customer can't be changed.")
        return redirect("party_detail", pk=pk)
    form = PartyDetailsForm(request.POST, party=p)
    if not form.is_valid():
        _form_errors_to_messages(request, form)
        return redirect("party_detail", pk=pk)
    before = {"name": p.name, "contact_number": p.contact_number, "address": p.address}
    changed = {k: v for k, v in form.cleaned_data.items() if before[k] != v}
    if changed:
        with transaction.atomic():
            for k, v in changed.items():
                setattr(p, k, v)
            p.name_key = rules.name_key(p.name)
            p.save()
            audit.log("PARTY_EDITED", "core_party", p.pk, old="; ".join(f"{k}: {before[k]}" for k in changed),
                      new="; ".join(f"{k}: {v}" for k, v in changed.items()))
        messages.success(request, "Details saved.")
    return redirect("party_detail", pk=pk)


@role_required(Role.OWNER)
@require_POST
def party_credit(request, pk):
    p = _party_or_404(request, pk)
    if p.is_walk_in:
        messages.error(request, "The Walk-in customer is cash only.")
        return redirect("party_detail", pk=pk)
    form = PartyCreditForm(request.POST)
    if not form.is_valid():
        _form_errors_to_messages(request, form)
        return redirect("party_detail", pk=pk)
    d = form.cleaned_data
    new = {"credit_allowed": d["credit_allowed"], "terms_days": d["terms_days"] if d["credit_allowed"] else p.terms_days,
           "is_affiliate": d["is_affiliate"]}
    if d["credit_allowed"] and not p.is_customer:
        new["is_customer"] = True                      # credit is for customers
    before = {k: getattr(p, k) for k in new}
    changed = {k: v for k, v in new.items() if before[k] != v}
    if changed:
        with transaction.atomic():
            for k, v in changed.items():
                setattr(p, k, v)
            p.save()
            action = "PARTY_CREDIT_CHANGED" if {"credit_allowed", "terms_days"} & changed.keys() else "PARTY_ROLE_CHANGED"
            audit.log(action, "core_party", p.pk, old="; ".join(f"{k}: {before[k]}" for k in changed),
                      new="; ".join(f"{k}: {v}" for k, v in changed.items()))
        messages.success(request, "Credit and roles saved.")
    return redirect("party_detail", pk=pk)


@role_required(Role.OWNER)
@require_POST
def party_active(request, pk):
    p = _party_or_404(request, pk)
    make_active = request.POST.get("active") == "1"
    if p.is_walk_in:
        messages.error(request, "The Walk-in customer is always active.")
    elif make_active != p.is_active:
        with transaction.atomic():
            p.is_active = make_active
            p.save(update_fields=["is_active"])
            audit.log("PARTY_REACTIVATED" if make_active else "PARTY_DEACTIVATED", "core_party", p.pk, new=p.name)
        messages.success(request, f"{p.name} is {'active again' if make_active else 'deactivated'}.")
    return redirect("party_detail", pk=pk)


@role_required(Role.OWNER)
@require_POST
def party_unlink(request, pk, creditor_id):
    """Fix a wrong same-name match: move one AP record to a party of its own."""
    p = _party_or_404(request, pk)
    link = get_object_or_404(p.ap_links, creditor_id=creditor_id)
    if p.ap_links.count() == 1 and not (p.is_customer or p.is_affiliate):
        messages.error(request, "This is the party's only record, so there's nothing to separate.")
        return redirect("party_detail", pk=pk)
    new = rules.unlink_to_own_party(link)
    messages.success(request, f"AP #{creditor_id} is now its own party: {new.name}. Rename it if you like.")
    return redirect("party_detail", pk=new.pk)
