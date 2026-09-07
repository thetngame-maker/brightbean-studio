"""Durable card previews with background recovery of expired discovery media."""

import logging
from datetime import timedelta
from urllib.parse import urlsplit

from background_task import background
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from apps.media_library.services import generate_video_thumbnail
from apps.members.decorators import require_permission

from .models import UGCSubmission
from .ugc_media_repair import _asset_file_exists
from .ugc_views import _get_workspace

logger = logging.getLogger(__name__)


def _ready(asset):
    if not _asset_file_exists(asset):
        return False
    try:
        return not asset.is_video or bool(asset.thumbnail and asset.thumbnail.storage.exists(asset.thumbnail.name))
    except Exception:
        return False


@background(schedule=0)
def recover_card_preview(submission_id):
    from .ugc_approved_media_repair import repair_one_approved_submission

    submission = UGCSubmission.objects.select_related("media_asset").get(id=submission_id)
    success = False
    try:
        if not _asset_file_exists(submission.media_asset):
            if submission.status != UGCSubmission.Status.APPROVED:
                return
            repair_one_approved_submission(submission)
            submission.refresh_from_db()
        asset = submission.media_asset
        if (
            _asset_file_exists(asset)
            and asset.is_video
            and not (asset.thumbnail and asset.thumbnail.storage.exists(asset.thumbnail.name))
        ):
            thumb = generate_video_thumbnail(asset.file.url)
            if thumb:
                asset.thumbnail.save(f"ugc-{asset.id}.jpg", thumb, save=True)
        success = _ready(asset)
    except Exception:
        logger.exception("Community preview recovery failed for %s", submission_id)
    finally:
        # Merge into fresh metadata to preserve concurrent moderation/usage edits.
        with transaction.atomic():
            current = UGCSubmission.objects.select_for_update().get(id=submission_id)
            metadata = dict(current.metadata or {})
            metadata["card_preview_repair"] = {
                "status": "ready" if success else "unavailable",
                "at": timezone.now().isoformat(),
            }
            current.metadata = metadata
            current.save(update_fields=["metadata", "updated_at"])


@login_required
@require_permission("manage_workspace_settings")
@require_http_methods(["GET", "POST"])
def card_preview(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(
        UGCSubmission.objects.select_related("media_asset"), id=submission_id, workspace=workspace
    )
    asset = submission.media_asset
    if request.GET.get("image") == "1":
        if not _ready(asset):
            raise Http404
        field = asset.thumbnail if asset.is_video else asset.file
        try:
            response = FileResponse(
                field.open("rb"), content_type="image/jpeg" if asset.is_video else (asset.mime_type or "image/jpeg")
            )
        except (OSError, ValueError):
            raise Http404 from None
        response["Cache-Control"] = "private, max-age=300"
        return response
    if _ready(asset):
        return JsonResponse(
            {
                "status": "ready",
                "type": "video" if asset.is_video else "image",
                "url": reverse("composer:media_stream", kwargs={"workspace_id": workspace.id, "asset_id": asset.id}),
            }
        )
    # Some Instagram CDN hosts reject server downloads but allow browser playback.
    # Only use recently refreshed links; stale discovery URLs must be repaired.
    discovery = (submission.metadata or {}).get("discovery_import") or {}
    refreshed = parse_datetime(discovery.get("media_refreshed_at", ""))
    source = discovery.get("media_url") or ""
    thumbnail = discovery.get("thumbnail_url") or source
    if (
        refreshed
        and refreshed > timezone.now() - timedelta(hours=1)
        and urlsplit(source).scheme == "https"
        and urlsplit(thumbnail).scheme == "https"
    ):
        return JsonResponse(
            {"status": "ready", "type": discovery.get("media_type", "image"), "url": source, "thumbnail": thumbnail}
        )
    repair = (submission.metadata or {}).get("card_preview_repair") or {}
    if request.method == "POST":
        with transaction.atomic():
            submission = UGCSubmission.objects.select_for_update().get(id=submission.id)
            metadata = dict(submission.metadata or {})
            repair = metadata.get("card_preview_repair") or {}
            last = parse_datetime(repair.get("at", ""))
            if not last or last < timezone.now() - timedelta(minutes=10):
                repair = {"status": "loading", "at": timezone.now().isoformat()}
                metadata["card_preview_repair"] = repair
                submission.metadata = metadata
                submission.save(update_fields=["metadata", "updated_at"])
                transaction.on_commit(lambda: recover_card_preview(str(submission.id)))
    return JsonResponse({"status": repair.get("status", "missing")}, status=202)
