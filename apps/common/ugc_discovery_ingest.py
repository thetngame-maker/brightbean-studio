"""Provider-agnostic ingestion for externally discovered community content.

Discovery providers should map their result rows into the small normalized item
shape accepted here. Keeping creation and dedupe in one service means the test
importer, scheduled jobs, and future provider adapters all follow the same UGC
permission/moderation workflow.
"""

from __future__ import annotations

from typing import Any, Iterable

from django.db import transaction

from .models import UGCSubmission
from .ugc_permissions import NOT_CONTACTED, set_permission
from .ugc_provenance import build_provenance, normalize_platform, set_provenance
from .ugc_remote_media import capture_discovered_media


MAX_BATCH_ITEMS = 100


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _metric(value: Any):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.replace(",", "").strip()))
        except ValueError:
            return None
    return None


def _duplicate_exists(*, workspace_id, platform: str, external_id: str, source_url: str) -> bool:
    base = UGCSubmission.objects.for_workspace(workspace_id)
    if external_id:
        return base.filter(
            metadata__provenance__platform=platform,
            metadata__provenance__external_id=external_id,
        ).exists()
    if source_url:
        return base.filter(
            metadata__provenance__platform=platform,
            metadata__provenance__source_url=source_url,
        ).exists()
    return False


def ingest_discovered_items(
    *,
    workspace,
    items: Iterable[dict[str, Any]],
    discovery_source: str,
    default_target_type: str,
    default_target_id: str,
    default_target_label: str = "",
    default_target_url: str = "",
    media_asset=None,
) -> dict[str, Any]:
    """Create discovered UGC records and return a structured batch summary.

    Normalized item keys:
      platform, creator_handle, creator_name, creator_external_id,
      source_url, external_id, title, caption, discovery_query,
      media_url, like_count, comment_count, view_count,
      target_type, target_id, target_label, target_url.

    Dedupe uses platform + external_id when an external id exists, otherwise
    platform + source_url. Invalid and duplicate rows are skipped, never fatal
    to the rest of a batch.
    """

    created = []
    duplicates = []
    invalid = []

    rows = list(items)[:MAX_BATCH_ITEMS]
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            invalid.append({"index": index, "reason": "Item must be a JSON object."})
            continue

        platform = normalize_platform(raw.get("platform") or "instagram")
        creator_handle = _text(raw.get("creator_handle") or raw.get("username"), 255).lstrip("@")
        creator_name = _text(raw.get("creator_name") or raw.get("full_name"), 255)
        creator_external_id = _text(raw.get("creator_external_id") or raw.get("owner_id"), 255)
        source_url = _text(raw.get("source_url") or raw.get("url"), 2000)
        external_id = _text(raw.get("external_id") or raw.get("shortcode") or raw.get("id"), 255)
        discovery_query = _text(raw.get("discovery_query"), 500)

        target_type = _text(raw.get("target_type") or default_target_type, 100)
        target_id = _text(raw.get("target_id") or default_target_id, 255)
        target_label = _text(raw.get("target_label") or default_target_label, 255)
        target_url = _text(raw.get("target_url") or default_target_url, 2000)

        if not creator_handle:
            invalid.append({"index": index, "reason": "creator_handle is required."})
            continue
        if not source_url:
            invalid.append({"index": index, "reason": "source_url is required."})
            continue
        if not target_type or not target_id:
            invalid.append({"index": index, "reason": "A target type and target ID are required."})
            continue

        if _duplicate_exists(
            workspace_id=workspace.id,
            platform=platform,
            external_id=external_id,
            source_url=source_url,
        ):
            duplicates.append(
                {
                    "index": index,
                    "platform": platform,
                    "external_id": external_id,
                    "source_url": source_url,
                }
            )
            continue

        provenance = build_provenance(
            platform=platform,
            source_url=source_url,
            external_id=external_id,
            creator_handle=creator_handle,
            discovery_source=discovery_source,
            discovery_query=discovery_query,
        )
        metadata = set_provenance({}, provenance)
        metadata = set_permission(metadata, status=NOT_CONTACTED)
        media_url = _text(raw.get("media_url") or raw.get("display_url"), 2000)
        metadata["discovery_import"] = {
            "media_url": media_url,
            "media_capture_status": "queued" if media_url and media_asset is None else "",
            "like_count": _metric(raw.get("like_count") if raw.get("like_count") is not None else raw.get("likes")),
            "comment_count": _metric(raw.get("comment_count") if raw.get("comment_count") is not None else raw.get("comments")),
            "view_count": _metric(
                raw.get("view_count")
                if raw.get("view_count") is not None
                else raw.get("video_view_count")
                if raw.get("video_view_count") is not None
                else raw.get("views")
            ),
        }

        with transaction.atomic():
            submission = UGCSubmission.objects.create(
                workspace=workspace,
                kind=UGCSubmission.Kind.PHOTO,
                status=UGCSubmission.Status.PENDING,
                source=UGCSubmission.Source.IMPORT,
                contributor_handle=creator_handle,
                contributor_name=creator_name,
                contributor_external_id=creator_external_id,
                attribution=UGCSubmission.Attribution.HANDLE,
                target_type=target_type,
                target_id=target_id,
                target_label=target_label,
                target_url=target_url,
                media_asset=media_asset,
                title=_text(raw.get("title"), 255) or target_label or "Discovered post",
                body=_text(raw.get("caption") or raw.get("body"), 10000),
                consent_confirmed=False,
                metadata=metadata,
            )
        created.append(submission)
        if media_url and media_asset is None:
            capture_discovered_media(str(submission.id))

    return {
        "created": created,
        "created_count": len(created),
        "duplicate_count": len(duplicates),
        "invalid_count": len(invalid),
        "duplicates": duplicates,
        "invalid": invalid,
        "total_received": len(rows),
    }
