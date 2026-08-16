"""Authenticated intake API for TN Game/community-contributed content.

The binary upload remains owned by /api/v1/media/.  This endpoint accepts an
optional MediaAsset UUID plus contributor/target/consent metadata and creates a
pending moderation record.  Keeping the two steps separate reuses the existing
media validation, storage quotas, processing, and idempotent upload pipeline.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.common.audit import record_audit_event
from apps.common.models import UGCSubmission
from apps.media_library.models import MediaAsset

router = Router(tags=["community-content"])


class CommunityContentCreate(Schema):
    kind: str = "photo"
    contributor_external_id: str = ""
    contributor_name: str = ""
    contributor_handle: str = ""
    attribution: str = "name"
    target_type: str
    target_id: str
    target_label: str = ""
    target_url: str = ""
    title: str = ""
    body: str = ""
    rating: int | None = None
    media_asset_id: uuid.UUID | None = None
    consent_confirmed: bool = False
    consent_version: str = ""
    metadata: dict[str, Any] = {}


class CommunityContentResponse(Schema):
    id: uuid.UUID
    status: str
    kind: str
    target_type: str
    target_id: str
    target_label: str
    contributor_name: str
    contributor_handle: str
    media_asset_id: uuid.UUID | None = None
    consent_confirmed: bool
    submitted_at: str

    @classmethod
    def from_submission(cls, submission: UGCSubmission):
        return cls(
            id=submission.id,
            status=submission.status,
            kind=submission.kind,
            target_type=submission.target_type,
            target_id=submission.target_id,
            target_label=submission.target_label,
            contributor_name=submission.contributor_name,
            contributor_handle=submission.contributor_handle,
            media_asset_id=submission.media_asset_id,
            consent_confirmed=submission.consent_confirmed,
            submitted_at=submission.submitted_at.isoformat(),
        )


def _require_intake_permission(request: HttpRequest) -> None:
    """Require a key capable of introducing community media into this workspace.

    ``upload_media`` already exists in the workspace permission catalogue and is
    the permission required by the preceding binary-upload step.  Reusing it
    avoids inventing an API-only permission that workspace roles cannot grant.
    """

    membership = getattr(request, "workspace_membership", None)
    if membership is None or not membership.effective_permissions.get("upload_media", False):
        raise HttpError(403, "Permission denied: upload_media")


def _validate_payload(payload: CommunityContentCreate) -> None:
    if payload.kind not in dict(UGCSubmission.Kind.choices):
        raise HttpError(422, "Unsupported community content kind.")
    if payload.attribution not in dict(UGCSubmission.Attribution.choices):
        raise HttpError(422, "Unsupported attribution preference.")
    if not payload.target_type.strip() or not payload.target_id.strip():
        raise HttpError(422, "target_type and target_id are required.")
    if payload.rating is not None and not 1 <= payload.rating <= 5:
        raise HttpError(422, "rating must be between 1 and 5.")
    if payload.kind == UGCSubmission.Kind.REVIEW and payload.rating is None:
        raise HttpError(422, "Reviews require a 1-5 rating.")
    if payload.kind == UGCSubmission.Kind.PHOTO and payload.media_asset_id is None:
        raise HttpError(422, "Photo submissions require media_asset_id. Upload the photo to /api/v1/media/ first.")
    if payload.consent_confirmed and not payload.consent_version.strip():
        raise HttpError(422, "consent_version is required when consent_confirmed is true.")


@router.post(
    "/",
    response={201: CommunityContentResponse},
    summary="Create a pending community-content submission",
)
def create_community_content(request, payload: CommunityContentCreate):
    enforce_http_rate_limits(request, is_write=True)
    _require_intake_permission(request)
    _validate_payload(payload)

    workspace = request.workspace  # set by ApiKeyAuth
    media_asset = None
    if payload.media_asset_id is not None:
        # UGC may reference only an asset belonging to this workspace (not a
        # different tenant). Shared org media is intentionally excluded from
        # external contributor intake because contributor uploads should have
        # one unambiguous owning workspace.
        media_asset = get_object_or_404(
            MediaAsset.objects.filter(workspace_id=workspace.id),
            id=payload.media_asset_id,
        )

    actor = request.user if getattr(request.user, "is_authenticated", False) else None
    submission = UGCSubmission.objects.create(
        workspace=workspace,
        kind=payload.kind,
        status=UGCSubmission.Status.PENDING,
        source=UGCSubmission.Source.API,
        contributor_external_id=payload.contributor_external_id.strip()[:255],
        contributor_name=payload.contributor_name.strip()[:255],
        contributor_handle=payload.contributor_handle.strip().lstrip("@")[:255],
        attribution=payload.attribution,
        target_type=payload.target_type.strip()[:100],
        target_id=payload.target_id.strip()[:255],
        target_label=payload.target_label.strip()[:255],
        target_url=payload.target_url.strip()[:2000],
        media_asset=media_asset,
        title=payload.title.strip()[:255],
        body=payload.body,
        rating=payload.rating,
        consent_confirmed=payload.consent_confirmed,
        consent_version=payload.consent_version.strip()[:50],
        consent_at=timezone.now() if payload.consent_confirmed else None,
        metadata=payload.metadata or {},
    )

    record_audit_event(
        workspace=workspace,
        actor=actor,
        action="ugc.submitted",
        target=submission,
        target_label=str(submission),
        metadata={
            "source": "api",
            "kind": submission.kind,
            "target_type": submission.target_type,
            "target_id": submission.target_id,
            "consent_confirmed": submission.consent_confirmed,
        },
        request=request,
        source="api",
    )
    log_audit_entry(
        request,
        action="community_content.create.201",
        target_id=submission.id,
        status_code=201,
    )
    return 201, CommunityContentResponse.from_submission(submission)
