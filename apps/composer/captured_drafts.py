"""Create rights-aware drafts captured from external social posts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.utils import timezone

from apps.common.audit import record_audit_event
from apps.common.models import ContentPerformanceProfile, UGCSubmission
from apps.common.ugc_permissions import NOT_CONTACTED, set_permission
from apps.common.ugc_provenance import build_provenance, set_provenance
from apps.composer.models import Post
from apps.composer.services import create_post


@dataclass(frozen=True)
class CapturedDraftResult:
    post: Post
    submission: UGCSubmission
    duplicate: bool


def canonical_source_url(value: str) -> str:
    """Return a stable public HTTP(S) URL or raise ``ValueError``."""
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("Source URL must be a valid public web address.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Source URL must be a valid public web address.")
    if parsed.username or parsed.password:
        raise ValueError("Source URL must not contain credentials.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))[:2000]


def platform_for_url(source_url: str, supplied: str = "") -> str:
    """Infer a supported source platform from a canonical URL."""
    host = (urlsplit(source_url).hostname or "").lower()
    known = (
        ("instagram.com", "instagram"),
        ("facebook.com", "facebook"),
        ("fb.watch", "facebook"),
        ("tiktok.com", "tiktok"),
        ("threads.net", "threads"),
    )
    for suffix, platform in known:
        if host == suffix or host.endswith(f".{suffix}"):
            return platform
    normalized = str(supplied or "").strip().lower().replace(" ", "_")[:50]
    return normalized if normalized in {"website", "other"} else "website"


def find_existing_capture(*, workspace, social_account, source_url: str):
    """Find the existing draft for one canonical source/account pair."""
    submission = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(metadata__provenance__source_url=source_url)
        .order_by("submitted_at")
        .first()
    )
    if submission is None:
        return None
    profile = (
        submission.performance_profiles.select_related("post")
        .filter(post__platform_posts__social_account=social_account)
        .order_by("post__created_at")
        .first()
    )
    if profile is None:
        return None
    return CapturedDraftResult(post=profile.post, submission=submission, duplicate=True)


def create_captured_draft(
    *,
    workspace,
    social_account,
    source_url: str,
    source_platform: str = "",
    source_external_id: str = "",
    creator_handle: str = "",
    creator_name: str = "",
    title: str = "",
    caption: str = "",
    media_assets=(),
    actor=None,
    request=None,
    capture_channel: str = "browser_extension",
    capture_label: str = "TN Social Studio browser extension",
    audit_source: str = "api",
) -> CapturedDraftResult:
    """Create or reuse the rights-aware draft for an external social post."""
    if social_account.workspace_id != workspace.id:
        raise ValueError("The selected social account is not in this workspace.")

    source_url = canonical_source_url(source_url)
    platform = platform_for_url(source_url, source_platform)
    creator_handle = str(creator_handle or "").strip().lstrip("@")[:255]
    creator_name = str(creator_name or "").strip()[:255]
    external_id = str(source_external_id or "").strip()[:255]
    title = str(title or "").strip()[:255]
    caption = str(caption or "")[:10_000]
    media_assets = list(media_assets or ())[:10]
    if any(asset.workspace_id != workspace.id for asset in media_assets):
        raise ValueError("One or more captured media files are unavailable in this workspace.")

    duplicate = find_existing_capture(
        workspace=workspace,
        social_account=social_account,
        source_url=source_url,
    )
    if duplicate is not None:
        return duplicate

    target_id = external_id or hashlib.sha256(source_url.encode()).hexdigest()[:32]
    channel_key = "".join(char if char.isalnum() else "_" for char in capture_channel.lower()).strip("_")
    channel_key = channel_key[:50] or "external"

    with transaction.atomic():
        submission = (
            UGCSubmission.objects.for_workspace(workspace.id)
            .filter(metadata__provenance__source_url=source_url)
            .order_by("submitted_at")
            .first()
        )
        if submission is None:
            provenance = build_provenance(
                platform=platform,
                source_url=source_url,
                external_id=external_id,
                creator_handle=creator_handle,
                discovery_source=channel_key,
            )
            metadata = set_permission(set_provenance({}, provenance), status=NOT_CONTACTED)
            metadata[f"{channel_key}_capture"] = {
                "captured_at": timezone.now().isoformat(),
                "media_count": len(media_assets),
            }
            submission = UGCSubmission.objects.create(
                workspace=workspace,
                kind=UGCSubmission.Kind.PHOTO if media_assets else UGCSubmission.Kind.COMMUNITY_POST,
                status=UGCSubmission.Status.PENDING,
                source=UGCSubmission.Source.IMPORT,
                contributor_name=creator_name,
                contributor_handle=creator_handle,
                contributor_external_id=creator_handle,
                attribution=(
                    UGCSubmission.Attribution.HANDLE
                    if creator_handle
                    else UGCSubmission.Attribution.NAME
                    if creator_name
                    else UGCSubmission.Attribution.ANONYMOUS
                ),
                target_type="community_post",
                target_id=target_id,
                target_label=f"{platform.replace('_', ' ').title()} post",
                target_url=source_url,
                media_asset=media_assets[0] if media_assets else None,
                title=title,
                body=caption,
                consent_confirmed=False,
                metadata=metadata,
            )

        notes = [
            f"Captured with {capture_label}",
            f"Original source URL: {source_url}",
            f"Source platform: {platform}",
            "Creator rights must be reviewed before publishing.",
        ]
        if creator_handle:
            notes.insert(2, f"Original creator: @{creator_handle}")
        elif creator_name:
            notes.insert(2, f"Original creator: {creator_name}")

        post = create_post(
            workspace=workspace,
            social_account=social_account,
            caption=caption,
            media_asset_ids=[asset.id for asset in media_assets],
            title=title or f"{platform.title()} capture",
            internal_notes="\n".join(notes),
            author=actor,
            status="draft",
        )
        ContentPerformanceProfile.objects.create(
            workspace=workspace,
            post=post,
            source_submission=submission,
            creator=submission.creator,
            source_type=ContentPerformanceProfile.SourceType.UGC,
            target_type=submission.target_type,
            target_id=submission.target_id,
            target_label=submission.target_label,
            notes=f"Captured with {capture_label}; original source retained for rights review.",
            created_by=actor,
            updated_by=actor,
        )
        metadata = dict(submission.metadata or {})
        post_ids = [str(value) for value in metadata.get("studio_post_ids") or [] if value]
        post_ids.append(str(post.id))
        metadata["studio_post_ids"] = list(dict.fromkeys(post_ids))[-20:]
        metadata["studio_drafted_at"] = timezone.now().isoformat()
        submission.metadata = metadata
        submission.save(update_fields=["metadata", "updated_at"])

        audit_action = (
            "ugc.browser_draft_created"
            if channel_key == "browser_extension"
            else f"ugc.{channel_key}_draft_created"
        )
        record_audit_event(
            workspace=workspace,
            actor=actor,
            action=audit_action,
            target=submission,
            target_label=str(submission),
            metadata={
                "post_id": str(post.id),
                "social_account_id": str(social_account.id),
                "platform": platform,
                "source_url": source_url,
                "media_count": len(media_assets),
            },
            request=request,
            source=audit_source,
        )
    return CapturedDraftResult(post=post, submission=submission, duplicate=False)
