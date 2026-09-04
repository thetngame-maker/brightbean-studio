"""Create rights-aware composer drafts from the TN Social Studio extension."""

from __future__ import annotations

import hashlib
import uuid
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.common.audit import record_audit_event
from apps.common.models import ContentPerformanceProfile, UGCSubmission
from apps.common.ugc_permissions import NOT_CONTACTED, set_permission
from apps.common.ugc_provenance import build_provenance, set_provenance
from apps.composer.services import create_post
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount

router = Router(tags=["browser-drafts"])


class BrowserDraftCreate(Schema):
    social_account_id: uuid.UUID
    source_url: str = Field(..., max_length=2000)
    source_platform: str = Field("", max_length=50)
    source_external_id: str = Field("", max_length=255)
    creator_handle: str = Field("", max_length=255)
    creator_name: str = Field("", max_length=255)
    title: str = Field("", max_length=255)
    caption: str = Field("", max_length=10_000)
    media_asset_ids: list[uuid.UUID] = Field(default_factory=list)


class BrowserDraftResponse(Schema):
    post_id: uuid.UUID
    submission_id: uuid.UUID
    edit_path: str
    status: str
    rights_status: str
    duplicate: bool


def _require_create_permission(request: HttpRequest) -> None:
    membership = getattr(request, "workspace_membership", None)
    if membership is None or not membership.effective_permissions.get("create_posts", False):
        raise HttpError(403, "Permission denied: create_posts")


def _resolve_account(request: HttpRequest, account_id: uuid.UUID) -> SocialAccount:
    allowed = {account.id: account for account in request.api_key.social_accounts.all()}
    account = allowed.get(account_id)
    if account is None:
        raise HttpError(403, "SocialAccount is not in this key's allowlist.")
    return account


def _canonical_source_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise HttpError(422, "source_url must be a valid public web address.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HttpError(422, "source_url must be a valid public web address.")
    if parsed.username or parsed.password:
        raise HttpError(422, "source_url must not contain credentials.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))[:2000]


def _platform_for_url(source_url: str, supplied: str) -> str:
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


def _validate_media(workspace, media_ids: list[uuid.UUID]) -> list[MediaAsset]:
    unique_ids = list(dict.fromkeys(media_ids))[:10]
    assets = list(MediaAsset.objects.filter(workspace=workspace, id__in=unique_ids))
    by_id = {asset.id: asset for asset in assets}
    missing = [value for value in unique_ids if value not in by_id]
    if missing:
        raise HttpError(422, "One or more captured media files are unavailable in this workspace.")
    return [by_id[value] for value in unique_ids]


def _response(post, submission, *, duplicate: bool) -> BrowserDraftResponse:
    return BrowserDraftResponse(
        post_id=post.id,
        submission_id=submission.id,
        edit_path=reverse(
            "composer:compose_edit",
            kwargs={"workspace_id": post.workspace_id, "post_id": post.id},
        ),
        status="draft",
        rights_status=submission.rights_passport.status,
        duplicate=duplicate,
    )


@router.post(
    "/",
    response={201: BrowserDraftResponse, 200: BrowserDraftResponse},
    summary="Capture the current social post as a rights-aware Studio draft",
)
def create_browser_draft(request, payload: BrowserDraftCreate):
    enforce_http_rate_limits(request, is_write=True)
    _require_create_permission(request)
    workspace = request.workspace
    account = _resolve_account(request, payload.social_account_id)
    source_url = _canonical_source_url(payload.source_url)
    platform = _platform_for_url(source_url, payload.source_platform)
    creator_handle = payload.creator_handle.strip().lstrip("@")[:255]
    creator_name = payload.creator_name.strip()[:255]
    external_id = payload.source_external_id.strip()[:255]
    target_id = external_id or hashlib.sha256(source_url.encode()).hexdigest()[:32]
    media_assets = _validate_media(workspace, payload.media_asset_ids)
    actor = request.user if getattr(request.user, "is_authenticated", False) else None

    with transaction.atomic():
        submission = (
            UGCSubmission.objects.for_workspace(workspace.id)
            .filter(metadata__provenance__source_url=source_url)
            .order_by("submitted_at")
            .first()
        )
        if submission is not None:
            existing_profile = (
                submission.performance_profiles.select_related("post")
                .filter(post__platform_posts__social_account=account)
                .order_by("post__created_at")
                .first()
            )
            if existing_profile is not None:
                log_audit_entry(
                    request,
                    action="browser_draft.create.duplicate",
                    target_id=existing_profile.post_id,
                    status_code=200,
                )
                return 200, _response(existing_profile.post, submission, duplicate=True)

        if submission is None:
            provenance = build_provenance(
                platform=platform,
                source_url=source_url,
                external_id=external_id,
                creator_handle=creator_handle,
                discovery_source="browser_extension",
            )
            metadata = set_permission(set_provenance({}, provenance), status=NOT_CONTACTED)
            metadata["browser_extension_capture"] = {
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
                title=payload.title.strip()[:255],
                body=payload.caption,
                consent_confirmed=False,
                metadata=metadata,
            )

        notes = [
            "Captured with the TN Social Studio browser extension",
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
            social_account=account,
            caption=payload.caption,
            media_asset_ids=[asset.id for asset in media_assets],
            title=payload.title.strip()[:255] or f"{platform.title()} capture",
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
            notes="Captured from the browser extension; original source retained for rights review.",
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

        record_audit_event(
            workspace=workspace,
            actor=actor,
            action="ugc.browser_draft_created",
            target=submission,
            target_label=str(submission),
            metadata={
                "post_id": str(post.id),
                "social_account_id": str(account.id),
                "platform": platform,
                "source_url": source_url,
                "media_count": len(media_assets),
            },
            request=request,
            source="api",
        )
        log_audit_entry(
            request,
            action="browser_draft.create.201",
            target_id=post.id,
            status_code=201,
        )
        return 201, _response(post, submission, duplicate=False)
