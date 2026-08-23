"""Lightweight mobile management for first-party campaign attribution."""

import secrets
from datetime import datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.composer.models import Post
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .campaign_attribution import (
    create_attribution_link,
    generate_conversion_secret,
    is_public_https_destination,
    link_conversion_rate,
    record_registration,
    rotate_conversion_secret,
    tracked_destination_url,
)
from .models import CampaignAttributionConversion, CampaignAttributionLink
from .ugc_target_catalog import find_catalog_target, target_choices
from .ugc_views import _get_workspace

ATTRIBUTION_PAGE_SIZE = 12
REVEAL_SESSION_KEY = "campaign_attribution_secret_reveal"


def _workspace_zone(workspace):
    try:
        return ZoneInfo(workspace.effective_timezone or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def _target(workspace, value):
    value = str(value or "").strip()
    if not value:
        return None
    if "::" not in value:
        return False
    target_type, target_id = value.split("::", 1)
    return find_catalog_target(workspace, target_type, target_id) or False


def _post(workspace, value):
    value = str(value or "").strip()
    if not value:
        return None
    return Post.objects.for_workspace(workspace.id).filter(id=value).first() or False


def _expires_at(workspace, value):
    value = str(value or "").strip()
    if not value:
        return None
    day = parse_date(value)
    if not day:
        return False
    return datetime.combine(day, time.max, tzinfo=_workspace_zone(workspace))


def _link_urls(request, link):
    return {
        "public_url": request.build_absolute_uri(
            reverse("attribution_public:redirect", kwargs={"code": link.code})
        ),
        "conversion_url": request.build_absolute_uri(
            reverse("attribution_public:conversion", kwargs={"code": link.code})
        ),
        "destination_with_tags": tracked_destination_url(link),
    }


def _decorate_link(request, link):
    for key, value in _link_urls(request, link).items():
        setattr(link, key, value)
    link.conversion_rate = link_conversion_rate(link)
    return link


def _reveal(request, link=None):
    data = request.session.pop(REVEAL_SESSION_KEY, None)
    if not isinstance(data, dict) or (link is not None and data.get("link_id") != str(link.id)):
        return None
    return data


def _store_reveal(request, link, secret):
    request.session[REVEAL_SESSION_KEY] = {
        "link_id": str(link.id),
        "link_name": link.name,
        "secret": secret,
        **_link_urls(request, link),
    }


def _post_choices(workspace):
    return Post.objects.for_workspace(workspace.id).only("id", "title", "caption", "updated_at").order_by(
        "-updated_at"
    )[:100]


@login_required
@require_permission("manage_workspace_settings")
def attribution_links(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    view = str(request.GET.get("view") or "active").strip().lower()
    if view not in {"active", "paused", "archived"}:
        view = "active"
    queryset = CampaignAttributionLink.objects.for_workspace(workspace.id).select_related("post")
    if view == "active":
        queryset = queryset.filter(is_active=True, archived_at__isnull=True)
    elif view == "paused":
        queryset = queryset.filter(is_active=False, archived_at__isnull=True)
    else:
        queryset = queryset.filter(archived_at__isnull=False)
    query = str(request.GET.get("q") or "").strip()[:100]
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(destination_url__icontains=query)
            | Q(target_label__icontains=query)
            | Q(utm_campaign__icontains=query)
        )
    page = Paginator(queryset, ATTRIBUTION_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    links = [_decorate_link(request, link) for link in page.object_list]
    all_links = CampaignAttributionLink.objects.for_workspace(workspace.id)
    counts = {
        "active": all_links.filter(is_active=True, archived_at__isnull=True).count(),
        "paused": all_links.filter(is_active=False, archived_at__isnull=True).count(),
        "archived": all_links.filter(archived_at__isnull=False).count(),
    }
    totals = all_links.filter(archived_at__isnull=True).aggregate(
        clicks=Sum("click_count"),
        visitors=Sum("unique_visitor_count"),
        registrations=Sum("registration_count"),
    )
    visitors = int(totals.get("visitors") or 0)
    registrations = int(totals.get("registrations") or 0)
    return render(
        request,
        "ugc/attribution_links.html",
        {
            "workspace": workspace,
            "attribution_links": links,
            "attribution_page": page,
            "attribution_view": view,
            "attribution_query": query,
            "attribution_counts": counts,
            "attribution_totals": {
                "clicks": int(totals.get("clicks") or 0),
                "visitors": visitors,
                "registrations": registrations,
                "conversion_rate": round((registrations / visitors) * 100, 1) if visitors else None,
            },
            "attribution_target_choices": target_choices(workspace, limit=150),
            "attribution_post_choices": _post_choices(workspace),
            "attribution_reveal": _reveal(request),
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_attribution_link_view(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:attribution_links", kwargs={"workspace_id": workspace.id})
    destination_url = str(request.POST.get("destination_url") or "").strip()[:2000]
    if not is_public_https_destination(destination_url):
        messages.error(request, "Use a public HTTPS destination URL.")
        return redirect(fallback)
    target = _target(workspace, request.POST.get("target_key"))
    if target is False:
        messages.error(request, "Choose a destination from the existing TN Game target catalog.")
        return redirect(fallback)
    post = _post(workspace, request.POST.get("post_id"))
    if post is False:
        messages.error(request, "Choose a content idea from this workspace.")
        return redirect(fallback)
    expires_at = _expires_at(workspace, request.POST.get("expires_on"))
    if expires_at is False or (expires_at and expires_at <= timezone.now()):
        messages.error(request, "Choose a future expiration date or leave it blank.")
        return redirect(fallback)
    host = urlsplit(destination_url).hostname or "Campaign"
    default_name = target["target_label"] if target else host
    name = (str(request.POST.get("name") or "").strip() or f"{default_name} Campaign")[:255]
    secret = generate_conversion_secret()
    link = create_attribution_link(
        conversion_secret=secret,
        workspace=workspace,
        name=name,
        destination_url=destination_url,
        post=post,
        target_type=target["target_type"] if target else "",
        target_id=target["target_id"] if target else "",
        target_label=target["target_label"] if target else "",
        target_url=(target.get("target_url") or "") if target else "",
        utm_source=str(request.POST.get("utm_source") or "social").strip()[:100],
        utm_medium=str(request.POST.get("utm_medium") or "organic").strip()[:100],
        utm_campaign=(str(request.POST.get("utm_campaign") or "").strip() or name)[:150],
        expires_at=expires_at,
        created_by=request.user,
        updated_by=request.user,
    )
    _store_reveal(request, link, secret)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="campaign_attribution.link_created",
        target=link,
        metadata={
            "code": link.code,
            "target_type": link.target_type,
            "target_id": link.target_id,
            "post_id": str(link.post_id or ""),
            "destination_host": urlsplit(link.destination_url).hostname or "",
        },
        request=request,
    )
    messages.success(request, "Tracked campaign link created. Copy the conversion key now; it is shown once.")
    return redirect("ugc:attribution_link_detail", workspace_id=workspace.id, link_id=link.id)


@login_required
@require_permission("manage_workspace_settings")
def attribution_link_detail(request, workspace_id, link_id):
    workspace = _get_workspace(request, workspace_id)
    link = get_object_or_404(
        CampaignAttributionLink.objects.for_workspace(workspace.id).select_related("post"),
        id=link_id,
    )
    _decorate_link(request, link)
    conversion_page = Paginator(link.conversions.select_related("recorded_by"), ATTRIBUTION_PAGE_SIZE).get_page(
        request.GET.get("page") or 1
    )
    click_days = list(
        link.click_days.values("day")
        .annotate(clicks=Sum("clicks"), visitors=Count("id"))
        .order_by("-day")[:12]
    )
    return render(
        request,
        "ugc/attribution_link_detail.html",
        {
            "workspace": workspace,
            "link": link,
            "attribution_conversions": conversion_page.object_list,
            "attribution_conversion_page": conversion_page,
            "attribution_click_days": click_days,
            "attribution_target_choices": target_choices(workspace, limit=150),
            "attribution_post_choices": _post_choices(workspace),
            "attribution_reveal": _reveal(request, link),
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_attribution_link(request, workspace_id, link_id):
    workspace = _get_workspace(request, workspace_id)
    link = get_object_or_404(CampaignAttributionLink.objects.for_workspace(workspace.id), id=link_id)
    fallback = reverse("ugc:attribution_link_detail", kwargs={"workspace_id": workspace.id, "link_id": link.id})
    action = str(request.POST.get("action") or "").strip().lower()
    before = {"is_active": link.is_active, "archived": bool(link.archived_at)}
    if action == "pause":
        link.is_active = False
        action_name = "campaign_attribution.link_paused"
        message = "Tracked link paused. Existing attribution history is unchanged."
        fields = ["is_active"]
    elif action == "resume":
        link.is_active = True
        link.archived_at = None
        action_name = "campaign_attribution.link_resumed"
        message = "Tracked link resumed."
        fields = ["is_active", "archived_at"]
    elif action == "archive":
        link.is_active = False
        link.archived_at = timezone.now()
        action_name = "campaign_attribution.link_archived"
        message = "Link archived. Clicks, registrations, and audit history were preserved."
        fields = ["is_active", "archived_at"]
    elif action == "restore":
        link.is_active = False
        link.archived_at = None
        action_name = "campaign_attribution.link_restored"
        message = "Link restored in a paused state."
        fields = ["is_active", "archived_at"]
    elif action == "rotate_key":
        previous_hint = link.conversion_secret_hint
        secret = generate_conversion_secret()
        rotate_conversion_secret(link, secret, actor=request.user)
        _store_reveal(request, link, secret)
        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action="campaign_attribution.conversion_key_rotated",
            target=link,
            metadata={"previous_hint": previous_hint},
            request=request,
        )
        messages.success(request, "Conversion key rotated. The previous key no longer works; copy this one now.")
        return redirect(fallback)
    else:
        messages.error(request, "That attribution action is no longer available.")
        return redirect(fallback)
    link.updated_by = request.user
    link.save(update_fields=[*fields, "updated_by", "updated_at"])
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=action_name,
        target=link,
        metadata={"before": before, "after": {"is_active": link.is_active, "archived": bool(link.archived_at)}},
        request=request,
    )
    messages.success(request, message)
    return redirect(fallback)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def record_attribution_registration(request, workspace_id, link_id):
    workspace = _get_workspace(request, workspace_id)
    link = get_object_or_404(CampaignAttributionLink.objects.for_workspace(workspace.id), id=link_id)
    fallback = reverse("ugc:attribution_link_detail", kwargs={"workspace_id": workspace.id, "link_id": link.id})
    try:
        quantity = int(request.POST.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 0
    if quantity < 1 or quantity > 1_000_000:
        messages.error(request, "Registration count must be between 1 and 1,000,000.")
        return redirect(fallback)
    day = parse_date(str(request.POST.get("occurred_on") or "")) or timezone.localdate()
    if day > timezone.localdate() or day < timezone.localdate() - timedelta(days=1095):
        messages.error(request, "Choose a registration date within the past three years.")
        return redirect(fallback)
    occurred_at = datetime.combine(day, time(hour=12), tzinfo=_workspace_zone(workspace))
    conversion, _created = record_registration(
        link,
        external_id=f"manual-{secrets.token_urlsafe(24)}",
        occurred_at=occurred_at,
        source=CampaignAttributionConversion.Source.MANUAL,
        quantity=quantity,
        note=request.POST.get("note"),
        actor=request.user,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="campaign_attribution.registration_recorded",
        target=conversion,
        metadata={"link_id": str(link.id), "quantity": quantity, "occurred_on": day.isoformat()},
        request=request,
    )
    messages.success(request, f"Recorded {quantity} TN Game registration{'s' if quantity != 1 else ''}.")
    return redirect(fallback)
