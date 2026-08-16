"""Bulk test importer for the normalized UGC discovery ingestion service."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.media_library.models import MediaAsset
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .ugc_discovery_ingest import MAX_BATCH_ITEMS, ingest_discovered_items
from .ugc_discovery_views import TARGET_CHOICES
from .ugc_views import _get_workspace


SAMPLE_ITEMS = [
    {
        "platform": "instagram",
        "creator_handle": "tn_creator_one",
        "source_url": "https://www.instagram.com/p/TESTFOSTER01/",
        "external_id": "TESTFOSTER01",
        "title": "Foster Falls",
        "caption": "A perfect afternoon at Foster Falls.",
        "discovery_query": "#tennesseewaterfalls",
        "like_count": 142,
        "comment_count": 8,
    },
    {
        "platform": "instagram",
        "creator_handle": "tn_creator_two",
        "source_url": "https://www.instagram.com/p/TESTGREETER02/",
        "external_id": "TESTGREETER02",
        "title": "Greeter Falls",
        "caption": "Waterfall weather in Tennessee.",
        "discovery_query": "Greeter Falls",
        "like_count": 89,
        "comment_count": 5,
    },
]


@login_required
@require_permission("manage_workspace_settings")
def bulk_discovery_form(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    recent_images = list(
        MediaAsset.objects.filter(
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).order_by("-created_at")[:12]
    )
    return render(
        request,
        "ugc/discovered_bulk_form.html",
        {
            "workspace": workspace,
            "target_choices": TARGET_CHOICES,
            "recent_images": recent_images,
            "sample_json": json.dumps(SAMPLE_ITEMS, indent=2),
            "max_batch_items": MAX_BATCH_ITEMS,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_discovery_import(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    raw_json = request.POST.get("items_json", "").strip()
    target_type = request.POST.get("target_type", "").strip()
    target_id = request.POST.get("target_id", "").strip()
    target_label = request.POST.get("target_label", "").strip()
    target_url = request.POST.get("target_url", "").strip()
    media_asset_id = request.POST.get("media_asset_id", "").strip()

    if not target_type or not target_id:
        messages.error(request, "Choose a default TN Game target type and target ID.")
        return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)

    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        messages.error(request, f"The discovery JSON is invalid: {exc.msg} near line {exc.lineno}.")
        return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)

    if not isinstance(items, list):
        messages.error(request, "Discovery JSON must be an array of post objects.")
        return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)
    if not items:
        messages.error(request, "Add at least one discovery result to the JSON array.")
        return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)
    if len(items) > MAX_BATCH_ITEMS:
        messages.error(request, f"Test imports are limited to {MAX_BATCH_ITEMS} items at a time.")
        return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)

    media_asset = None
    if media_asset_id:
        media_asset = MediaAsset.objects.filter(
            id=media_asset_id,
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).first()
        if media_asset is None:
            messages.error(request, "That fallback image does not belong to this workspace.")
            return redirect("ugc:bulk_discovery_form", workspace_id=workspace.id)

    summary = ingest_discovered_items(
        workspace=workspace,
        items=items,
        discovery_source="test_bulk_import",
        default_target_type=target_type,
        default_target_id=target_id,
        default_target_label=target_label,
        default_target_url=target_url,
        media_asset=media_asset,
    )

    for submission in summary["created"]:
        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action="ugc.discovered_bulk_test_import",
            target=submission,
            target_label=str(submission),
            metadata={
                "batch_size": summary["total_received"],
                "created_count": summary["created_count"],
                "duplicate_count": summary["duplicate_count"],
                "invalid_count": summary["invalid_count"],
            },
            request=request,
        )

    messages.success(
        request,
        f"Discovery import complete: {summary['created_count']} created, "
        f"{summary['duplicate_count']} duplicate(s) skipped, "
        f"{summary['invalid_count']} invalid item(s) skipped.",
    )
    if summary["invalid"]:
        reasons = "; ".join(
            f"#{item['index']}: {item['reason']}" for item in summary["invalid"][:5]
        )
        messages.warning(request, f"Skipped items: {reasons}")

    return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")
