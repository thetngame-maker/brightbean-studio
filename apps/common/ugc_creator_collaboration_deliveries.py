"""Creator delivery intake backed by canonical UGC, media, rights, and task records."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.utils import timezone

from apps.media_library.models import MediaAsset
from apps.media_library.services import create_asset
from apps.media_library.tasks import process_media_asset
from apps.media_library.validators import validate_file

from .audit import record_audit_event
from .models import (
    AuditEvent,
    UGCCreatorCollaboration,
    UGCCreatorCollaborationDelivery,
    UGCCreatorCollaborationDeliveryAsset,
    UGCCreatorRightsRequest,
    UGCCreatorTask,
    UGCSubmission,
)
from .ugc_creator_rights_requests import (
    create_creator_rights_request,
    expire_creator_rights_request,
    requested_scopes,
)
from .ugc_provenance import build_provenance, set_provenance

MAX_DELIVERY_FILES = 6
MAX_DELIVERY_TOTAL_BYTES = 250 * 1024 * 1024
DELIVERY_METADATA_KEY = "creator_delivery"
VALID_MEDIA_TYPES = {
    MediaAsset.MediaType.IMAGE,
    MediaAsset.MediaType.VIDEO,
    MediaAsset.MediaType.GIF,
}
RIGHTS_SCOPE_FIELDS = {
    "organic_social": "allow_organic_social",
    "website": "allow_website",
    "email": "allow_email",
    "paid_ads": "allow_paid_ads",
    "print": "allow_print",
}


class CreatorDeliveryError(ValueError):
    pass


def latest_delivery_for(collaboration):
    return (
        UGCCreatorCollaborationDelivery.objects.filter(collaboration=collaboration)
        .select_related("submission", "reviewed_by")
        .prefetch_related("attachments__media_asset")
        .order_by("-revision_number")
        .first()
    )


def delivery_history_for(collaboration, *, limit=12):
    return list(
        UGCCreatorCollaborationDelivery.objects.filter(collaboration=collaboration)
        .select_related("submission", "reviewed_by")
        .prefetch_related("attachments__media_asset")
        .order_by("-revision_number")[:limit]
    )


def latest_rights_request_for(submission):
    if submission is None:
        return None
    rights_request = (
        UGCCreatorRightsRequest.objects.filter(submission=submission)
        .select_related("submission", "submission__rights_passport")
        .order_by("-created_at")
        .first()
    )
    if rights_request:
        expire_creator_rights_request(rights_request)
    return rights_request


def _platform_from_url(source_url, fallback="direct"):
    host = (urlsplit(source_url).hostname or "").lower()
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "facebook.com" in host or "fb.watch" in host:
        return "facebook"
    if "youtube.com" in host or "youtu.be" in host:
        return "youtube"
    return fallback or "direct"


def _primary_identity(creator):
    return creator.identities.filter(is_primary=True).first() or creator.identities.first()


def _validate_delivery(source_url, uploaded_files, deliverables_confirmed):
    source_url = str(source_url or "").strip()[:2000]
    files = list(uploaded_files or [])
    if not deliverables_confirmed:
        raise CreatorDeliveryError("Confirm that this submission includes the agreed deliverables.")
    if not source_url and not files:
        raise CreatorDeliveryError("Add a post link or upload at least one photo or video.")
    if source_url:
        try:
            URLValidator(schemes=["http", "https"])(source_url)
        except ValidationError as exc:
            raise CreatorDeliveryError("Enter a valid public content link beginning with http:// or https://.") from exc
    if len(files) > MAX_DELIVERY_FILES:
        raise CreatorDeliveryError(f"Upload no more than {MAX_DELIVERY_FILES} files in one delivery.")
    total_bytes = sum(int(getattr(item, "size", 0) or 0) for item in files)
    if total_bytes > MAX_DELIVERY_TOTAL_BYTES:
        raise CreatorDeliveryError("Uploads are limited to 250MB total. Share a public link for larger videos.")
    for uploaded_file in files:
        media_type, errors = validate_file(uploaded_file)
        if errors:
            raise CreatorDeliveryError(str(errors[0]))
        if media_type not in VALID_MEDIA_TYPES:
            raise CreatorDeliveryError("Creator deliveries may contain photos, GIFs, or videos only.")
    return source_url, files


def _complete_open_tasks(collaboration, *, actor=None):
    now = timezone.now()
    UGCCreatorTask.objects.filter(
        collaboration=collaboration,
        status=UGCCreatorTask.Status.OPEN,
    ).update(
        status=UGCCreatorTask.Status.DONE,
        completed_at=now,
        completed_by=actor if getattr(actor, "is_authenticated", False) else None,
        updated_at=now,
    )


def _create_task(collaboration, *, title, note, due_at, actor=None, submission=None):
    return UGCCreatorTask.objects.create(
        workspace=collaboration.workspace,
        creator=collaboration.creator,
        collaboration=collaboration,
        submission=submission,
        kind=UGCCreatorTask.Kind.COLLABORATION,
        title=title[:255],
        note=note[:5000],
        due_at=due_at,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )


def _create_submission(collaboration, *, source_url, creator_note, first_asset):
    identity = _primary_identity(collaboration.creator)
    handle = identity.handle if identity else ""
    platform = _platform_from_url(source_url, identity.platform if identity else "direct")
    provenance = build_provenance(
        platform=platform,
        source_url=source_url,
        creator_handle=handle,
        discovery_source="manual",
        discovery_query=collaboration.title,
    )
    metadata = set_provenance({}, provenance)
    metadata[DELIVERY_METADATA_KEY] = {
        "collaboration_id": str(collaboration.id),
        "latest_revision": 1,
        "submitted_via": "secure_creator_portal",
    }
    return UGCSubmission.objects.create(
        workspace=collaboration.workspace,
        kind=UGCSubmission.Kind.COMMUNITY_POST,
        status=UGCSubmission.Status.PENDING,
        source=UGCSubmission.Source.API,
        contributor_name=collaboration.creator.display_name,
        contributor_handle=handle,
        attribution=(UGCSubmission.Attribution.HANDLE if handle else UGCSubmission.Attribution.NAME),
        creator=collaboration.creator,
        target_type=collaboration.target_type or "community_post",
        target_id=collaboration.target_id or str(collaboration.id),
        target_label=collaboration.target_label,
        target_url=collaboration.target_url,
        media_asset=first_asset,
        title=collaboration.title,
        body="",
        metadata=metadata,
    )


def submit_creator_delivery(
    invite,
    *,
    source_url="",
    creator_note="",
    uploaded_files=None,
    deliverables_confirmed=False,
):
    """Create an immutable delivery revision from an accepted creator portal."""
    if invite.status != invite.Status.ACCEPTED:
        raise CreatorDeliveryError("Accept the collaboration before delivering content.")
    collaboration = invite.collaboration
    if collaboration.status != UGCCreatorCollaboration.Status.CONFIRMED:
        raise CreatorDeliveryError("This collaboration is not currently accepting a delivery.")
    latest = latest_delivery_for(collaboration)
    if latest and latest.status != UGCCreatorCollaborationDelivery.Status.REVISION_REQUESTED:
        if latest.status == UGCCreatorCollaborationDelivery.Status.SUBMITTED:
            raise CreatorDeliveryError("This delivery is already waiting for the team’s review.")
        raise CreatorDeliveryError("The delivered content has already been accepted.")

    source_url, files = _validate_delivery(source_url, uploaded_files, deliverables_confirmed)
    creator_note = str(creator_note or "").strip()[:2000]
    assets = []
    for uploaded_file in files:
        try:
            asset = create_asset(
                organization=collaboration.workspace.organization,
                workspace=collaboration.workspace,
                uploaded_file=uploaded_file,
                uploaded_by=None,
                alt_text=collaboration.title,
                title=collaboration.title,
                tags=["community-content", "ugc", "creator-delivery", "collaboration"],
            )
        except ValidationError as exc:
            errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
            raise CreatorDeliveryError(str(errors[0])) from exc
        assets.append(asset)

    now = timezone.now()
    with transaction.atomic():
        collaboration = (
            UGCCreatorCollaboration.objects.select_for_update()
            .select_related("workspace__organization", "creator", "submission")
            .get(id=collaboration.id)
        )
        locked_latest = (
            UGCCreatorCollaborationDelivery.objects.select_for_update()
            .filter(collaboration=collaboration)
            .order_by("-revision_number")
            .first()
        )
        if locked_latest and locked_latest.status != UGCCreatorCollaborationDelivery.Status.REVISION_REQUESTED:
            raise CreatorDeliveryError("The delivery state changed. Refresh this page before submitting again.")
        revision_number = (locked_latest.revision_number + 1) if locked_latest else 1
        submission = locked_latest.submission if locked_latest else collaboration.submission
        if submission is None:
            submission = _create_submission(
                collaboration,
                source_url=source_url,
                creator_note=creator_note,
                first_asset=assets[0] if assets else None,
            )
        else:
            metadata = dict(submission.metadata or {})
            delivery_metadata = dict(metadata.get(DELIVERY_METADATA_KEY) or {})
            delivery_metadata.update(
                {
                    "collaboration_id": str(collaboration.id),
                    "latest_revision": revision_number,
                    "submitted_via": "secure_creator_portal",
                }
            )
            metadata[DELIVERY_METADATA_KEY] = delivery_metadata
            if source_url:
                identity = _primary_identity(collaboration.creator)
                metadata = set_provenance(
                    metadata,
                    build_provenance(
                        platform=_platform_from_url(source_url, identity.platform if identity else "direct"),
                        source_url=source_url,
                        creator_handle=identity.handle if identity else submission.contributor_handle,
                        discovery_source="manual",
                        discovery_query=collaboration.title,
                    ),
                )
            submission.metadata = metadata
            if assets:
                submission.media_asset = assets[0]
            submission.save(update_fields=["metadata", "media_asset", "updated_at"])

        delivery = UGCCreatorCollaborationDelivery.objects.create(
            workspace=collaboration.workspace,
            collaboration=collaboration,
            submission=submission,
            revision_number=revision_number,
            source_url=source_url,
            creator_note=creator_note,
            submitted_at=now,
        )
        UGCCreatorCollaborationDeliveryAsset.objects.bulk_create(
            [
                UGCCreatorCollaborationDeliveryAsset(delivery=delivery, media_asset=asset, position=position)
                for position, asset in enumerate(assets)
            ]
        )
        collaboration.submission = submission
        collaboration.save(update_fields=["submission", "updated_at"])
        _complete_open_tasks(collaboration)
        _create_task(
            collaboration,
            title=f"Review creator delivery · {collaboration.title}",
            note=f"Creator submitted delivery revision {revision_number} through the secure portal.",
            due_at=now,
            submission=submission,
        )
        record_audit_event(
            workspace=collaboration.workspace,
            actor=None,
            action="ugc.creator_collaboration_delivery_submitted",
            target=delivery,
            source=AuditEvent.Source.API,
            metadata={
                "collaboration_id": str(collaboration.id),
                "submission_id": str(submission.id),
                "delivery_id": str(delivery.id),
                "revision_number": revision_number,
                "file_count": len(assets),
                "has_source_url": bool(source_url),
                "has_creator_note": bool(creator_note),
            },
        )

    for asset in assets:
        process_media_asset(str(asset.id))
    return latest_delivery_for(collaboration)


def _create_delivery_rights_request(collaboration, delivery, actor):
    scopes = set(collaboration.requested_rights or []) & set(RIGHTS_SCOPE_FIELDS)
    if not scopes:
        return None
    identity = _primary_identity(collaboration.creator)
    credit_text = collaboration.creator.preferred_credit or (
        f"@{identity.handle}" if identity and identity.handle else collaboration.creator.display_name
    )
    kwargs = {field: key in scopes for key, field in RIGHTS_SCOPE_FIELDS.items()}
    rights_request, superseded_count = create_creator_rights_request(
        delivery.submission,
        actor=actor,
        expires_in_days=14,
        credit_required=True,
        credit_text=credit_text,
        **kwargs,
    )
    record_audit_event(
        workspace=collaboration.workspace,
        actor=actor,
        action="ugc.creator_rights_request_created",
        target=rights_request,
        metadata={
            "submission_id": str(delivery.submission_id),
            "collaboration_id": str(collaboration.id),
            "delivery_id": str(delivery.id),
            "requested_scopes": requested_scopes(rights_request),
            "expires_at": rights_request.expires_at.isoformat(),
            "credit_required": rights_request.credit_required,
            "superseded_count": superseded_count,
            "source": "accepted_creator_delivery",
        },
    )
    return rights_request


def review_creator_delivery(delivery, *, action, review_note="", actor):
    """Accept a delivery or request a preserved revision from the creator."""
    action = str(action or "").strip().lower()
    if action not in {"accept", "request_revision"}:
        raise CreatorDeliveryError("Choose accept delivery or request a revision.")
    review_note = str(review_note or "").strip()[:2000]
    if action == "request_revision" and not review_note:
        raise CreatorDeliveryError("Add clear revision feedback before sending it to the creator.")

    with transaction.atomic():
        delivery = (
            UGCCreatorCollaborationDelivery.objects.select_for_update()
            .select_related("collaboration__workspace", "collaboration__creator", "submission")
            .get(id=delivery.id)
        )
        collaboration = UGCCreatorCollaboration.objects.select_for_update().get(id=delivery.collaboration_id)
        latest_id = (
            UGCCreatorCollaborationDelivery.objects.filter(collaboration=collaboration)
            .order_by("-revision_number")
            .values_list("id", flat=True)
            .first()
        )
        if latest_id != delivery.id or delivery.status != UGCCreatorCollaborationDelivery.Status.SUBMITTED:
            raise CreatorDeliveryError("Only the latest submitted delivery can be reviewed.")
        if collaboration.status != UGCCreatorCollaboration.Status.CONFIRMED:
            raise CreatorDeliveryError("This collaboration is no longer awaiting delivery review.")

        now = timezone.now()
        delivery.review_note = review_note
        delivery.reviewed_at = now
        delivery.reviewed_by = actor
        rights_request = None
        _complete_open_tasks(collaboration, actor=actor)
        if action == "request_revision":
            delivery.status = UGCCreatorCollaborationDelivery.Status.REVISION_REQUESTED
            _create_task(
                collaboration,
                title=f"Follow up on requested revisions · {collaboration.title}",
                note=review_note,
                due_at=now + timedelta(days=3),
                actor=actor,
                submission=delivery.submission,
            )
        else:
            delivery.status = UGCCreatorCollaborationDelivery.Status.ACCEPTED
            collaboration.status = UGCCreatorCollaboration.Status.CONTENT_RECEIVED
            collaboration.submission = delivery.submission
            collaboration.completed_at = None
            collaboration.save(update_fields=["status", "submission", "completed_at", "updated_at"])
            rights_request = _create_delivery_rights_request(collaboration, delivery, actor)
            if rights_request:
                _create_task(
                    collaboration,
                    title=f"Follow up on creator usage rights · {collaboration.title}",
                    note="Delivery accepted. A secure Rights Passport request is ready for the creator.",
                    due_at=now + timedelta(days=3),
                    actor=actor,
                    submission=delivery.submission,
                )
            else:
                _create_task(
                    collaboration,
                    title=f"Define usage rights · {collaboration.title}",
                    note="Delivery accepted, but the brief did not contain requested usage scopes.",
                    due_at=now,
                    actor=actor,
                    submission=delivery.submission,
                )
        delivery.save(update_fields=["status", "review_note", "reviewed_at", "reviewed_by", "updated_at"])
        record_audit_event(
            workspace=collaboration.workspace,
            actor=actor,
            action=f"ugc.creator_collaboration_delivery_{'accepted' if action == 'accept' else 'revision_requested'}",
            target=delivery,
            metadata={
                "collaboration_id": str(collaboration.id),
                "submission_id": str(delivery.submission_id),
                "delivery_id": str(delivery.id),
                "revision_number": delivery.revision_number,
                "has_review_note": bool(review_note),
                "rights_request_created": bool(rights_request),
            },
        )
    return delivery, rights_request


def replace_delivery_rights_request(delivery, *, actor):
    """Create a fresh Rights Passport request for an accepted delivery."""
    with transaction.atomic():
        delivery = (
            UGCCreatorCollaborationDelivery.objects.select_for_update()
            .select_related("collaboration__workspace", "collaboration__creator", "submission")
            .get(id=delivery.id)
        )
        collaboration = UGCCreatorCollaboration.objects.select_for_update().get(id=delivery.collaboration_id)
        if delivery.status != UGCCreatorCollaborationDelivery.Status.ACCEPTED:
            raise CreatorDeliveryError("Accept the creator delivery before preparing usage rights.")
        if collaboration.status != UGCCreatorCollaboration.Status.CONTENT_RECEIVED:
            raise CreatorDeliveryError("This collaboration is no longer awaiting a Rights Passport response.")
        latest_request = latest_rights_request_for(delivery.submission)
        if latest_request and latest_request.status == UGCCreatorRightsRequest.Status.GRANTED:
            raise CreatorDeliveryError("Creator usage rights are already active.")
        if latest_request and latest_request.status == UGCCreatorRightsRequest.Status.PENDING:
            raise CreatorDeliveryError("A secure Rights Passport request is already active.")
        if latest_request and latest_request.status == UGCCreatorRightsRequest.Status.DECLINED:
            raise CreatorDeliveryError("The creator declined these usage rights. Start a new agreement before asking again.")
        rights_request = _create_delivery_rights_request(collaboration, delivery, actor)
        if rights_request is None:
            raise CreatorDeliveryError("The accepted brief does not contain usage scopes to request.")
        _complete_open_tasks(collaboration, actor=actor)
        _create_task(
            collaboration,
            title=f"Follow up on creator usage rights · {collaboration.title}",
            note="A replacement secure Rights Passport request is ready for the creator.",
            due_at=timezone.now() + timedelta(days=3),
            actor=actor,
            submission=delivery.submission,
        )
    return rights_request
