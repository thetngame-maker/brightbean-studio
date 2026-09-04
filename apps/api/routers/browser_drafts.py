"""Create rights-aware composer drafts from the TN Social Studio extension."""

from __future__ import annotations

import uuid

from django.http import HttpRequest
from django.urls import reverse
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.composer.captured_drafts import canonical_source_url, create_captured_draft
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
    try:
        source_url = canonical_source_url(payload.source_url)
    except ValueError as exc:
        raise HttpError(422, str(exc).replace("Source URL", "source_url")) from exc
    media_assets = _validate_media(workspace, payload.media_asset_ids)
    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    result = create_captured_draft(
        workspace=workspace,
        social_account=account,
        source_url=source_url,
        source_platform=payload.source_platform,
        source_external_id=payload.source_external_id,
        creator_handle=payload.creator_handle,
        creator_name=payload.creator_name,
        title=payload.title,
        caption=payload.caption,
        media_assets=media_assets,
        actor=actor,
        request=request,
    )
    status_code = 200 if result.duplicate else 201
    log_audit_entry(
        request,
        action="browser_draft.create.duplicate" if result.duplicate else "browser_draft.create.201",
        target_id=result.post.id,
        status_code=status_code,
    )
    return status_code, _response(result.post, result.submission, duplicate=result.duplicate)
