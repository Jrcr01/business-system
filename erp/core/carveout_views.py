"""Carve-out pages (core step C4, mock-ups approved 2026-09-28): Tags, Tag records (main tag + peso splits), monthly summary. Owner only."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST, require_safe

from core import audit
from core import carveout as rules
from core.models import RecordType, Role, Tag, TagGroup
from core.parties import name_key
from core.roles import role_required
from engines.clock import manila_today

MAX_PARTS = 4


def _months(n: int = 24) -> list[tuple[str, str]]:
    today = manila_today()
    y, m, out = today.year, today.month, []
    for _ in range(n):
        out.append((f"{y}-{m:02d}", f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m - 1]} {y}"))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def _month(request) -> str:
    month = request.GET.get("month") or request.POST.get("month") or ""
    return month if len(month) == 7 and month[4] == "-" and month.replace("-", "").isdigit() else manila_today().strftime("%Y-%m")


def _type(request) -> str:
    rt = request.GET.get("type") or request.POST.get("type") or RecordType.CC_STATEMENT
    return rt if rt in RecordType.values else RecordType.CC_STATEMENT


def _active_tags():
    return Tag.objects.filter(is_active=True).order_by("group", "-is_default", "name")


# ---- tags ------------------------------------------------------------------------------------------------------------------------

@role_required(Role.OWNER)
@require_safe
def tags(request):
    groups = [{"key": g, "label": label, "tags": Tag.objects.filter(group=g).order_by("-is_default", "-is_active", "name")}
              for g, label in TagGroup.choices]
    return render(request, "core/tags.html", {"groups": groups})


@role_required(Role.OWNER)
@require_POST
def tag_add(request):
    group, name = request.POST.get("group"), " ".join((request.POST.get("name") or "").split())
    if group not in TagGroup.values or not name:
        messages.error(request, "Type a name for the sub-tag.")
        return redirect("tags")
    try:
        with transaction.atomic():
            t = Tag.objects.create(group=group, name=name, name_key=name_key(name))
            audit.log("TAG_ADDED", "core_tag", t.pk, new=str(t))
        messages.success(request, f"{t} added.")
    except IntegrityError:
        messages.error(request, f"{name} is already a sub-tag of {TagGroup(group).label}.")
    return redirect("tags")


@role_required(Role.OWNER)
@require_POST
def tag_edit(request, pk):
    t = get_object_or_404(Tag, pk=pk)
    old = str(t)
    if "active" in request.POST:
        make_active = request.POST["active"] == "1"
        if t.is_default and not make_active:
            messages.error(request, f"{t} is where untagged records go, so it can't be deactivated.")
            return redirect("tags")
        with transaction.atomic():
            t.is_active = make_active
            t.save(update_fields=["is_active"])
            audit.log("TAG_REACTIVATED" if make_active else "TAG_DEACTIVATED", "core_tag", t.pk, new=str(t))
        messages.success(request, f"{t} is {'active again' if make_active else 'deactivated: records keep it, it is no longer offered'}.")
        return redirect("tags")
    name = " ".join((request.POST.get("name") or "").split())
    if not name or name == t.name:
        return redirect("tags")
    try:
        with transaction.atomic():
            t.name, t.name_key = name, name_key(name)
            t.save(update_fields=["name", "name_key"])
            audit.log("TAG_RENAMED", "core_tag", t.pk, old=old, new=str(t))
        messages.success(request, f"Renamed to {t}.")
    except IntegrityError:
        messages.error(request, f"{name} is already a sub-tag of {t.get_group_display()}.")
    return redirect("tags")


# ---- tag records -----------------------------------------------------------------------------------------------------------------

@role_required(Role.OWNER)
@require_safe
def tag_records(request):
    rt, month = _type(request), _month(request)
    rows = rules.records(rt, month)
    return render(request, "core/tag_records.html", {
        "rows": rows, "type": rt, "types": RecordType.choices, "month": month, "months": _months(), "tags": _active_tags(),
        "total": sum((r["amount"] for r in rows), Decimal("0")),
    })


@role_required(Role.OWNER)
@require_POST
def tag_record_main(request):
    rt, month = _type(request), _month(request)
    tag = get_object_or_404(Tag, pk=request.POST.get("tag"))
    try:
        rules.set_main_tag(rt, int(request.POST.get("record_id", 0)), tag)
    except ValueError as e:
        messages.error(request, str(e))
    return redirect(f"{reverse('tag_records')}?type={rt}&month={month}")


@role_required(Role.OWNER)
@require_http_methods(["GET", "HEAD", "POST"])
def tag_record_split(request, record_type, record_id):
    if record_type not in RecordType.values:
        return redirect("tag_records")
    rec = rules.record(record_type, record_id)
    if rec is None:
        messages.error(request, "That record is not in the AP system.")
        return redirect("tag_records")
    month = _month(request)
    from core.models import Tagging

    tagging = Tagging.objects.filter(record_type=record_type, record_id=record_id).first()
    current = rules.parts(rec["amount"], tagging)
    form = {"main": current[0][0].pk, "parts": [(t.pk, a) for t, a in current[1:]]}
    if request.method == "POST":
        try:
            main = get_object_or_404(Tag, pk=request.POST.get("main"))
            moved, form_parts = [], []
            for i in range(MAX_PARTS):
                tag_id, amount = request.POST.get(f"tag_{i}"), (request.POST.get(f"amount_{i}") or "").replace(",", "").strip()
                if not tag_id and not amount:
                    continue
                form_parts.append((int(tag_id) if tag_id else None, amount))
                if not tag_id or not amount:
                    raise ValueError("Each part needs both a tag and an amount.")
                moved.append((get_object_or_404(Tag, pk=tag_id), Decimal(amount)))
            form = {"main": main.pk, "parts": form_parts}
            rules.set_split(record_type, record_id, main, moved)
            messages.success(request, f"{rec['label']} saved.")
            return redirect(f"{reverse('tag_records')}?type={record_type}&month={month}")
        except (ValueError, InvalidOperation) as e:
            messages.error(request, str(e) if isinstance(e, ValueError) else "Amounts must be numbers, e.g. 2,000.00.")
    rows = list(form["parts"]) + [(None, "")] * (MAX_PARTS - len(form["parts"]))
    return render(request, "core/tag_split.html", {"rec": rec, "month": month, "tags": _active_tags(), "form": form,
                                                   "rows": rows[:MAX_PARTS], "current": current,
                                                   "type_label": RecordType(record_type).label})


# ---- summary ----------------------------------------------------------------------------------------------------------------------

@role_required(Role.OWNER)
@require_safe
def carveout_summary(request):
    month = _month(request)
    s = rules.summary(month)
    keys = [g for g, _ in TagGroup.choices]
    rows = [{"label": label, "amounts": [row[g] for g in keys], "total": row["total"]} for label, row in s["by_type"].items()]
    total = {"label": "Total", "amounts": [s["totals"][g] for g in keys], "total": s["totals"]["total"]}
    return render(request, "core/carveout_summary.html", {"rows": rows, "total_row": total, "by_tag": s["by_tag"], "flagged": s["flagged"],
                                                          "month": month, "months": _months(), "groups": TagGroup.choices})
