"""Bulk repair tool for approved Instagram community-content media."""

from __future__ import annotations

import logging

from background_task import background
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from apps.members.decorators import require_permission

from .models import UGCSubmission
from .ugc_discovery_providers import fetch_instagram_post_details
from .ugc_remote_media import capture_submission_gallery
from .ugc_views import _get_workspace

logger = logging.getLogger(__name__)
BATCH_SIZE = 20


def _is_instagram(submission: UGCSubmission) -> bool:
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    source_url = str(provenance.get("source_url") or submission.target_url or "").strip()
    return str(provenance.get("platform") or "").lower() == "instagram" or "instagram.com/" in source_url.lower()


def _source_url(submission: UGCSubmission) -> str:
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    return str(provenance.get("source_url") or submission.target_url or "").strip()


def _asset_exists(submission: UGCSubmission) -> bool:
    asset = submission.media_asset
    if not asset or not asset.file or not asset.file.name:
        return False
    try:
        return bool(asset.file.storage.exists(asset.file.name))
    except Exception:
        logger.exception("Could not verify media storage for approved UGC %s", submission.id)
        return False


def _write_status(submission: UGCSubmission, *, status: str, detail: str = "", asset_count: int = 0) -> None:
    metadata = dict(submission.metadata or {})
    repair = dict(metadata.get("approved_media_repair") or {})
    repair.update(
        {
            "status": status[:100],
            "detail": detail[:500],
            "asset_count": int(asset_count or 0),
        }
    )
    metadata["approved_media_repair"] = repair
    submission.metadata = metadata
    submission.save(update_fields=["metadata", "updated_at"])


def repair_one_approved_submission(submission: UGCSubmission) -> tuple[bool, str, int]:
    if not _is_instagram(submission):
        return False, "not_instagram", 0

    source_url = _source_url(submission)
    if not source_url:
        _write_status(submission, status="failed", detail="missing_source_url")
        return False, "missing_source_url", 0

    _write_status(submission, status="refreshing")
    try:
        refreshed = fetch_instagram_post_details(source_url)
    except Exception as exc:
        detail = f"refresh_failed:{exc}"
        logger.info("Approved media refresh failed for %s: %s", submission.id, exc)
        _write_status(submission, status="failed", detail=detail)
        return False, detail, 0

    refreshed_items = refreshed.get("media_items") if isinstance(refreshed, dict) else None
    if not isinstance(refreshed_items, list) or not refreshed_items:
        _write_status(submission, status="failed", detail="refresh_returned_no_media")
        return False, "refresh_returned_no_media", 0

    metadata = dict(submission.metadata or {})
    discovery = dict(metadata.get("discovery_import") or {})
    discovery["media_items"] = refreshed_items[:20]
    discovery["media_count"] = len(discovery["media_items"])
    discovery["media_url"] = refreshed.get("media_url") or refreshed_items[0].get("media_url") or ""
    discovery["media_type"] = refreshed.get("media_type") or refreshed_items[0].get("media_type") or "image"
    discovery["thumbnail_url"] = refreshed.get("thumbnail_url") or refreshed_items[0].get("thumbnail_url") or ""
    discovery["instagram_product_type"] = refreshed.get("instagram_product_type") or discovery.get("instagram_product_type") or ""
    metadata["discovery_import"] = discovery
    submission.metadata = metadata

    # A database row can survive a Railway deployment even when its local file
    # disappeared. Clear only the broken relation so capture can rebuild it.
    if submission.media_asset_id and not _asset_exists(submission):
        submission.media_asset = None
        discovery.pop("media_asset_id", None)
        discovery["media_capture_status"] = "queued"
        metadata["discovery_import"] = discovery

    submission.save(update_fields=["media_asset", "metadata", "updated_at"])

    try:
        assets = capture_submission_gallery(submission)
    except Exception as exc:
        detail = f"capture_failed:{exc}"
        logger.exception("Approved gallery capture failed for %s", submission.id)
        _write_status(submission, status="failed", detail=detail)
        return False, detail, 0

    if not assets:
        _write_status(submission, status="failed", detail="no_assets_captured")
        return False, "no_assets_captured", 0

    _write_status(submission, status="repaired", detail="ok", asset_count=len(assets))
    return True, "repaired", len(assets)


@background(schedule=0)
def repair_approved_media_batch(workspace_id: str, after_id: str = ""):
    qs = (
        UGCSubmission.objects.for_workspace(workspace_id)
        .select_related("media_asset")
        .filter(status=UGCSubmission.Status.APPROVED)
        .order_by("submitted_at", "id")
    )
    if after_id:
        previous = qs.filter(id=after_id).first()
        if previous:
            qs = qs.filter(submitted_at__gte=previous.submitted_at).exclude(id=previous.id)

    batch = [item for item in qs[: BATCH_SIZE * 3] if _is_instagram(item)][:BATCH_SIZE]
    if not batch:
        return

    for submission in batch:
        repair_one_approved_submission(submission)

    last = batch[-1]
    remaining = qs.filter(submitted_at__gte=last.submitted_at).exclude(id=last.id)
    if any(_is_instagram(item) for item in remaining[: BATCH_SIZE * 3]):
        repair_approved_media_batch(str(workspace_id), str(last.id), schedule=2)


@login_required
@require_permission("manage_workspace_settings")
@require_http_methods(["GET", "POST"])
def approved_media_repair(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    approved = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .select_related("media_asset")
        .filter(status=UGCSubmission.Status.APPROVED)
        .order_by("-submitted_at")
    )
    instagram = [item for item in approved if _is_instagram(item)]
    repaired = sum(1 for item in instagram if (item.metadata or {}).get("approved_media_repair", {}).get("status") == "repaired")
    failed = sum(1 for item in instagram if (item.metadata or {}).get("approved_media_repair", {}).get("status") == "failed")
    missing_primary = sum(1 for item in instagram if not _asset_exists(item))

    if request.method == "POST":
        repair_approved_media_batch(str(workspace.id))
        messages.success(request, f"Approved media repair queued for {len(instagram)} Instagram items. It will run in batches of {BATCH_SIZE}.")
        return redirect("ugc:approved_media_repair", workspace_id=workspace.id)

    return render(
        request,
        "ugc/approved_media_repair.html",
        {
            "workspace": workspace,
            "approved_count": len(approved),
            "instagram_count": len(instagram),
            "repaired_count": repaired,
            "failed_count": failed,
            "missing_primary_count": missing_primary,
            "batch_size": BATCH_SIZE,
        },
    )
