"""Stable media capture for externally discovered UGC.

Discovery providers often return short-lived CDN URLs. We keep the original URL
in provenance, but copy image bytes into Studio's Media Library so moderation
thumbnails and later approved usage do not depend on that remote URL remaining
valid.
"""

from __future__ import annotations

import io
import logging
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from background_task import background
from django.core.files.base import ContentFile

from apps.media_library.models import MediaAsset

from .models import UGCSubmission

logger = logging.getLogger(__name__)

MAX_REMOTE_IMAGE_BYTES = 15 * 1024 * 1024
REMOTE_TIMEOUT_SECONDS = 15
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _remote_media_url(submission: UGCSubmission) -> str:
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    discovery = metadata.get("discovery_import") if isinstance(metadata.get("discovery_import"), dict) else {}
    return str(discovery.get("media_url") or "").strip()


def _capture_status(submission: UGCSubmission) -> str:
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    discovery = metadata.get("discovery_import") if isinstance(metadata.get("discovery_import"), dict) else {}
    return str(discovery.get("media_capture_status") or "").strip()


def _set_capture_status(submission: UGCSubmission, status: str) -> None:
    metadata = dict(submission.metadata or {})
    discovery = dict(metadata.get("discovery_import") or {})
    discovery["media_capture_status"] = str(status or "")[:200]
    metadata["discovery_import"] = discovery
    submission.metadata = metadata
    submission.save(update_fields=["metadata", "updated_at"])


def _safe_filename(submission: UGCSubmission, content_type: str, source_url: str) -> str:
    extension = ALLOWED_IMAGE_TYPES.get(content_type)
    if not extension:
        extension = mimetypes.guess_extension(content_type) or Path(urlparse(source_url).path).suffix or ".jpg"
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    external = str(provenance.get("external_id") or submission.id).strip()
    safe_external = "".join(ch for ch in external if ch.isalnum() or ch in "-_")[:80] or str(submission.id)
    return f"ugc-{safe_external}{extension.lower()}"


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return int(image.width or 0), int(image.height or 0)
    except Exception:
        return 0, 0


def capture_submission_image(submission: UGCSubmission) -> tuple[bool, str]:
    """Download one discovered image and attach a durable MediaAsset."""
    if submission.media_asset_id:
        return True, "already_captured"

    source_url = _remote_media_url(submission)
    parsed = urlparse(source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return False, "missing_or_unsafe_media_url"

    request = Request(
        source_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TNSocialStudio/1.0)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        },
    )
    try:
        with urlopen(request, timeout=REMOTE_TIMEOUT_SECONDS) as response:
            content_type = str(response.headers.get_content_type() or "").lower()
            declared = response.headers.get("Content-Length")
            if content_type not in ALLOWED_IMAGE_TYPES:
                return False, f"unsupported_content_type:{content_type or 'unknown'}"
            if declared:
                try:
                    if int(declared) > MAX_REMOTE_IMAGE_BYTES:
                        return False, "image_too_large"
                except (TypeError, ValueError):
                    pass
            data = response.read(MAX_REMOTE_IMAGE_BYTES + 1)
    except Exception as exc:
        logger.info("Could not fetch discovered UGC media for %s: %s", submission.id, exc)
        return False, f"fetch_failed:{exc.__class__.__name__}"

    if not data:
        return False, "empty_response"
    if len(data) > MAX_REMOTE_IMAGE_BYTES:
        return False, "image_too_large"

    width, height = _image_dimensions(data)
    filename = _safe_filename(submission, content_type, source_url)
    attribution = f"@{submission.contributor_handle}" if submission.contributor_handle else submission.contributor_name

    asset = MediaAsset.objects.create(
        organization=submission.workspace.organization,
        workspace=submission.workspace,
        file=ContentFile(data, name=filename),
        filename=filename,
        media_type=MediaAsset.MediaType.IMAGE,
        mime_type=content_type,
        file_size=len(data),
        width=width,
        height=height,
        alt_text=submission.title or submission.target_label or "Discovered community content",
        title=submission.title or submission.target_label or "Discovered community content",
        source="ugc_discovery",
        source_url=source_url,
        attribution=attribution or "",
        tags=["ugc", "discovered", str(submission.target_id or "")],
    )
    submission.media_asset = asset
    metadata = dict(submission.metadata or {})
    discovery = dict(metadata.get("discovery_import") or {})
    discovery["media_capture_status"] = "captured"
    discovery["media_asset_id"] = str(asset.id)
    metadata["discovery_import"] = discovery
    submission.metadata = metadata
    submission.save(update_fields=["media_asset", "metadata", "updated_at"])
    return True, "captured"


@background(schedule=0)
def capture_discovered_media(submission_id):
    """Background wrapper used after discovery ingestion."""
    try:
        submission = UGCSubmission.objects.select_related("workspace__organization", "media_asset").get(id=submission_id)
    except UGCSubmission.DoesNotExist:
        return

    ok, status = capture_submission_image(submission)
    if not ok:
        _set_capture_status(submission, status)


@background(schedule=0)
def repair_workspace_discovered_media(workspace_id):
    """Backfill thumbnails for old discovered rows without duplicate queue jobs."""
    submissions = list(
        UGCSubmission.objects.for_workspace(workspace_id)
        .filter(media_asset__isnull=True, metadata__provenance__discovery_source__isnull=False)
        .order_by("submitted_at")[:100]
    )
    for submission in submissions:
        if not _remote_media_url(submission):
            continue
        if _capture_status(submission) in {"queued", "captured"}:
            continue
        _set_capture_status(submission, "queued")
        capture_discovered_media(str(submission.id))
