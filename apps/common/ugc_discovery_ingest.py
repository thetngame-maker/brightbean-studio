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


def _find_duplicate(*, workspace_id, platform: str, external_id: str, source_url: str):
    base = UGCSubmission.objects.for_workspace(workspace_id).select_related("media_asset")
    if external_id:
        return base.filter(
            metadata__provenance__platform=platform,
            metadata__provenance__external_id=external_id,
        ).first()
    if source_url:
        return base.filter(
            metadata__provenance__platform=platform,
            metadata__provenance__source_url=source_url,
        ).first()
    return None


def discovery_item_exists(*, workspace_id, raw: dict[str, Any]) -> bool:
    """Return whether a normalized discovery row already exists in Studio."""
    if not isinstance(raw, dict):
        return False
    platform = normalize_platform(raw.get("platform") or "instagram")
    external_id = _text(raw.get("external_id") or raw.get("shortcode") or raw.get("id"), 255)
    source_url = _text(raw.get("source_url") or raw.get("url"), 2000)
    return bool(
        _find_duplicate(
            workspace_id=workspace_id,
            platform=platform,
            external_id=external_id,
            source_url=source_url,
        )
    )


def select_rows_for_new_target(*, workspace_id, rows: Iterable[dict[str, Any]], target_new: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select enough scanned rows to aim for ``target_new`` unseen items.

    Duplicate rows encountered before the target is reached are intentionally
    retained so ingestion can still enrich engagement metadata and upgrade old
    photo-only records into Reel/video assets.
    """
    target = max(1, min(MAX_BATCH_ITEMS, int(target_new or 1)))
    selected: list[dict[str, Any]] = []
    new_count = 0
    duplicate_count = 0
    scanned_count = 0

    for raw in list(rows)[:MAX_BATCH_ITEMS]:
        scanned_count += 1
        if not isinstance(raw, dict):
            selected.append(raw)
            continue
        duplicate = discovery_item_exists(workspace_id=workspace_id, raw=raw)
        selected.append(raw)
        if duplicate:
            duplicate_count += 1
        else:
            new_count += 1
            if new_count >= target:
                break

    return selected, {
        "scanned_count": scanned_count,
        "selected_new_count": new_count,
        "selected_duplicate_count": duplicate_count,
        "target_new_count": target,
    }


def _discovery_metadata(raw: dict, media_asset=None) -> dict:
    media_type = _text(raw.get("media_type") or "image", 20).lower()
    if media_type not in {"image", "video"}:
        media_type = "image"
    media_url = _text(raw.get("media_url") or raw.get("display_url"), 2000)
    discovery_method = _text(raw.get("discovery_method"), 30).lower()
    if discovery_method not in {"keyword", "hashtag", "location", "account"}:
        discovery_method = ""
    return {
        "media_type": media_type,
        "media_url": media_url,
        "thumbnail_url": _text(raw.get("thumbnail_url"), 2000),
        "instagram_product_type": _text(raw.get("instagram_product_type"), 100),
        "discovery_method": discovery_method,
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
        "creator_identity_provisional": bool(raw.get("creator_identity_provisional")),
        "location_id": _text(raw.get("location_id"), 255),
        "location_name": _text(raw.get("location_name"), 255),
        "location_url": _text(raw.get("location_url"), 2000),
    }


def _upgrade_duplicate_media(submission: UGCSubmission, raw: dict) -> bool:
    """Enrich an existing discovered row and upgrade a still image to Reel video."""
    incoming = _discovery_metadata(raw, media_asset=submission.media_asset)
    metadata = dict(submission.metadata or {})
    discovery = dict(metadata.get("discovery_import") or {})

    for key in (
        "like_count",
        "comment_count",
        "view_count",
        "creator_identity_provisional",
        "location_id",
        "location_name",
        "location_url",
        "instagram_product_type",
        "discovery_method",
    ):
        if incoming.get(key) not in (None, ""):
            discovery[key] = incoming.get(key)

    incoming_type = incoming.get("media_type") or "image"
    incoming_url = incoming.get("media_url") or ""
    incoming_thumb = incoming.get("thumbnail_url") or ""
    needs_capture = False

    if incoming_type == "video" and incoming_url:
        discovery["media_type"] = "video"
        discovery["media_url"] = incoming_url
        discovery["thumbnail_url"] = incoming_thumb
        discovery["media_capture_status"] = "queued"
        submission.kind = UGCSubmission.Kind.COMMUNITY_POST

        if submission.media_asset_id and submission.media_asset and not submission.media_asset.is_video:
            discovery["thumbnail_asset_id"] = str(submission.media_asset_id)
            submission.media_asset = None
            needs_capture = True
        elif not submission.media_asset_id:
            needs_capture = True
        elif submission.media_asset and submission.media_asset.is_video:
            discovery["media_capture_status"] = "captured"
    elif not discovery.get("media_url") and incoming_url:
        discovery["media_type"] = incoming_type
        discovery["media_url"] = incoming_url
        discovery["thumbnail_url"] = incoming_thumb
        if not submission.media_asset_id:
            discovery["media_capture_status"] = "queued"
            needs_capture = True

    metadata["discovery_import"] = discovery
    submission.metadata = metadata
    submission.save(update_fields=["kind", "media_asset", "metadata", "updated_at"])

    if needs_capture:
        capture_discovered_media(str(submission.id))
    return needs_capture


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
    """Create discovered UGC records and return a structured batch summary."""

    created = []
    duplicates = []
    invalid = []
    upgraded_count = 0

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

        duplicate = _find_duplicate(
            workspace_id=workspace.id,
            platform=platform,
            external_id=external_id,
            source_url=source_url,
        )
        if duplicate:
            if _upgrade_duplicate_media(duplicate, raw):
                upgraded_count += 1
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
        discovery = _discovery_metadata(raw, media_asset=media_asset)
        metadata["discovery_import"] = discovery
        media_url = discovery.get("media_url") or ""
        is_video = discovery.get("media_type") == "video"

        with transaction.atomic():
            submission = UGCSubmission.objects.create(
                workspace=workspace,
                kind=UGCSubmission.Kind.COMMUNITY_POST if is_video else UGCSubmission.Kind.PHOTO,
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
        "upgraded_count": upgraded_count,
        "duplicates": duplicates,
        "invalid": invalid,
        "total_received": len(rows),
    }
