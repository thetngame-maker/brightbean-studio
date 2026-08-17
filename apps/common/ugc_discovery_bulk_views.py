"""Bulk test importer for the normalized UGC discovery ingestion service."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.media_library.models import MediaAsset
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .ugc_discovery_ingest import MAX_BATCH_ITEMS, ingest_discovered_items
from .ugc_discovery_search_views import get_saved_search, record_search_run
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


def _sample_for_search(saved_search):
    if not saved_search:
        return SAMPLE_ITEMS
    platform = saved_search["platform"]
    query = saved_search["query"]
    slug = saved_search["id"].replace("-", "")[:8].upper()
    base_url = "https://www.instagram.com/p/" if platform == "instagram" else "https://example.com/post/"
    return [
        {
            "platform": platform,
            "creator_handle": "tn_discovery_test_one",
            "source_url": f"{base_url}{slug}A/",
            "external_id": f"{slug}A",
            "title": saved_search["name"] or query,
            "caption": f"Test result discovered from {query}.",
            "discovery_query": query,
            "like_count": 126,
            "comment_count": 7,
        },
        {
            "platform": platform,
            "creator_handle": "tn_discovery_test_two",
            "source_url": f"{base_url}{slug}B/",
            "external_id": f"{slug}B",
            "title": saved_search["name"] or query,
            "caption": f"Second test result discovered from {query}.",
            "discovery_query": query,
            "like_count": 74,
            "comment_count": 4,
        },
    ]


@login_required
@require_permission("manage_workspace_settings")
def bulk_discovery_form(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    saved_search = get_saved_search(workspace, request.GET.get("search_id"))
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
            "sample_json": json.dumps(_sample_for_search(saved_search), indent=2),
            "max_batch_items": min(MAX_BATCH_ITEMS, saved_search["result_limit"] if saved_search else MAX_BATCH_ITEMS),
            "saved_search": saved_search,
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
    search_id = request.POST.get("search_id", "").strip()
    saved_search = get_saved_search(workspace, search_id) if search_id else None
    return_url = (
        f"/workspace/{workspace.id}/community-content/discovered/bulk/?search_id={search_id}"
        if saved_search else f"/workspace/{workspace.id}/community-content/discovered/bulk/"
    )

    if not target_type or not target_id:
        messages.error(request, "Choose a default TN Game target type and target ID.")
        return redirect(return_url)

    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        messages.error(request, f"The discovery JSON is invalid: {exc.msg} near line {exc.lineno}.")
        if saved_search:
            record_search_run(workspace, search_id, status="failed", run_at=timezone.now().isoformat())
        return redirect(return_url)

    if not isinstance(items, list):
        messages.error(request, "Discovery JSON must be an array of post objects.")
        return redirect(return_url)
    if not items:
        messages.error(request, "Add at least one discovery result to the JSON array.")
        return redirect(return_url)

    run_limit = min(MAX_BATCH_ITEMS, saved_search["result_limit"] if saved_search else MAX_BATCH_ITEMS)
    if len(items) > run_limit:
        messages.error(request, f"This discovery run is limited to {run_limit} items.")
        return redirect(return_url)

    media_asset = None
    if media_asset_id:
        media_asset = MediaAsset.objects.filter(
            id=media_asset_id,
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).first()
        if media_asset is None:
            messages.error(request, "That fallback image does not belong to this workspace.")
            return redirect(return_url)

    discovery_source = "saved_search_test" if saved_search else "test_bulk_import"
    summary = ingest_discovered_items(
        workspace=workspace,
        items=items,
        discovery_source=discovery_source,
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
                "saved_search_id": search_id,
                "saved_search_query": saved_search["query"] if saved_search else "",
            },
            request=request,
        )

    if saved_search:
        record_search_run(
            workspace,
            search_id,
            status="success",
            received=summary["total_received"],
            created=summary["created_count"],
            duplicates=summary["duplicate_count"],
            invalid=summary["invalid_count"],
            run_at=timezone.now().isoformat(),
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

    if saved_search:
        return redirect("ugc:discovery_searches", workspace_id=workspace.id)
    return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")
