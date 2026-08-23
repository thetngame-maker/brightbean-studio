"""Deterministic creator opportunities from data Studio already owns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from .models import UGCCreator


@dataclass(frozen=True)
class CreatorOpportunity:
    key: str
    label: str
    reason: str
    next_action: str
    score: int
    target_stage: str = ""


OPPORTUNITY_LABELS = {
    "partner": "Partner candidates",
    "trusted": "Ready for trusted",
    "reengage": "Re-engage",
    "rising": "Rising prospects",
}


def _metric(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0
    return 0


def creator_engagement_score(metadata_items):
    """Use captured import metrics only; this never performs provider calls."""
    score = 0
    for metadata in metadata_items:
        metadata = metadata if isinstance(metadata, dict) else {}
        discovery = metadata.get("discovery_import")
        discovery = discovery if isinstance(discovery, dict) else {}
        likes = _metric(discovery.get("like_count"))
        comments = _metric(discovery.get("comment_count"))
        views = _metric(discovery.get("view_count"))
        score += likes + (comments * 10) + (views // 100)
    return int(score)


def classify_creator_opportunity(creator, *, engagement_score=0, now=None):
    """Return the single most useful next relationship action for a creator."""
    now = now or timezone.now()
    content_count = int(getattr(creator, "content_count", 0) or 0)
    granted_count = int(getattr(creator, "granted_count", 0) or 0)
    drafted_count = int(getattr(creator, "drafted_count", 0) or 0)
    grant_rate = round((granted_count / content_count) * 100) if content_count else 0
    stage = creator.relationship_stage

    if stage == UGCCreator.RelationshipStage.TRUSTED and (granted_count >= 3 or drafted_count >= 2):
        return CreatorOpportunity(
            key="partner",
            label="Partner candidate",
            reason=f"{granted_count} granted · {drafted_count} drafted · {grant_rate}% grant rate",
            next_action="Promote to partner",
            score=100 + min(30, granted_count * 4 + drafted_count * 6),
            target_stage=UGCCreator.RelationshipStage.PARTNER,
        )

    if stage == UGCCreator.RelationshipStage.PERMISSIONED and granted_count >= 2 and grant_rate >= 50:
        return CreatorOpportunity(
            key="trusted",
            label="Ready for trusted",
            reason=f"{granted_count} permissions granted · {grant_rate}% grant rate",
            next_action="Promote to trusted",
            score=80 + min(19, granted_count * 4 + drafted_count * 3),
            target_stage=UGCCreator.RelationshipStage.TRUSTED,
        )

    active_stages = {
        UGCCreator.RelationshipStage.PERMISSIONED,
        UGCCreator.RelationshipStage.TRUSTED,
        UGCCreator.RelationshipStage.PARTNER,
    }
    stale_before = now - timedelta(days=30)
    seen_after = now - timedelta(days=120)
    latest_content_at = getattr(creator, "latest_content_at", None) or creator.last_seen_at
    contacted_at = creator.last_contacted_at
    has_newer_content = latest_content_at and (contacted_at is None or latest_content_at > contacted_at)
    if (
        stage in active_stages
        and creator.last_seen_at >= seen_after
        and (contacted_at is None or contacted_at <= stale_before)
        and has_newer_content
    ):
        days = (now - contacted_at).days if contacted_at else None
        reason = (
            f"New content since your last contact · {days} days since outreach"
            if days is not None
            else "Active creator with no relationship outreach recorded"
        )
        return CreatorOpportunity(
            key="reengage",
            label="Re-engage",
            reason=reason,
            next_action="Review creator",
            score=60 + min(19, content_count * 2 + granted_count * 3),
        )

    if stage in {UGCCreator.RelationshipStage.PROSPECT, UGCCreator.RelationshipStage.CONTACTED} and (
        (content_count >= 2 and engagement_score >= 250) or engagement_score >= 1000
    ):
        return CreatorOpportunity(
            key="rising",
            label="Rising prospect",
            reason=f"{content_count} discovered posts · {engagement_score:,} engagement signal",
            next_action="Review prospect",
            score=40 + min(19, content_count * 2 + engagement_score // 500),
        )

    return None
