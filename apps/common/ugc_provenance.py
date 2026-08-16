"""Normalized provenance helpers for community content.

The moderation UI stores flexible integration-specific details in
``UGCSubmission.metadata``.  These helpers keep the shape stable so manual
intake, API imports, webhooks, and future discovery providers (for example an
Instagram discovery worker) can all describe where a submission originally
came from without adding provider-specific database columns.
"""

from __future__ import annotations

from typing import Any


PROVENANCE_KEY = "provenance"

PLATFORM_LABELS = {
    "direct": "Direct submission",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "website": "Website",
    "manual": "Manual entry",
    "api": "API",
    "other": "Other source",
}


def normalize_platform(value: Any) -> str:
    """Return a small stable platform key suitable for storage and filtering."""
    platform = str(value or "").strip().lower().replace(" ", "_")[:50]
    return platform or "direct"


def build_provenance(
    *,
    platform: Any = "direct",
    source_url: Any = "",
    external_id: Any = "",
    creator_handle: Any = "",
    discovery_source: Any = "manual",
    discovery_query: Any = "",
) -> dict[str, str]:
    """Build the canonical provenance object stored under metadata.provenance."""
    return {
        "platform": normalize_platform(platform),
        "source_url": str(source_url or "").strip()[:2000],
        "external_id": str(external_id or "").strip()[:255],
        "creator_handle": str(creator_handle or "").strip().lstrip("@")[:255],
        "discovery_source": str(discovery_source or "manual").strip().lower()[:50],
        "discovery_query": str(discovery_query or "").strip()[:500],
    }


def set_provenance(metadata: Any, provenance: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of metadata with a normalized provenance object attached."""
    result = dict(metadata) if isinstance(metadata, dict) else {}
    result[PROVENANCE_KEY] = build_provenance(
        platform=provenance.get("platform"),
        source_url=provenance.get("source_url"),
        external_id=provenance.get("external_id"),
        creator_handle=provenance.get("creator_handle"),
        discovery_source=provenance.get("discovery_source"),
        discovery_query=provenance.get("discovery_query"),
    )
    return result


def get_provenance(metadata: Any) -> dict[str, str]:
    """Read provenance safely, including older submissions with no metadata."""
    if not isinstance(metadata, dict):
        return build_provenance()
    raw = metadata.get(PROVENANCE_KEY)
    if not isinstance(raw, dict):
        return build_provenance()
    return build_provenance(
        platform=raw.get("platform"),
        source_url=raw.get("source_url"),
        external_id=raw.get("external_id"),
        creator_handle=raw.get("creator_handle"),
        discovery_source=raw.get("discovery_source"),
        discovery_query=raw.get("discovery_query"),
    )


def provenance_label(metadata: Any, *, fallback_source: str = "") -> str:
    """Human-friendly source label for cards, audit views, and draft notes."""
    provenance = get_provenance(metadata)
    platform = provenance["platform"]
    if platform == "direct" and fallback_source:
        fallback = str(fallback_source).strip().lower()
        if fallback in {"api", "import", "webhook"}:
            return PLATFORM_LABELS.get(fallback, fallback.title())
    return PLATFORM_LABELS.get(platform, platform.replace("_", " ").title())
