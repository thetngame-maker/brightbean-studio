"""Creator identity and rights-passport synchronization for Community content."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import UGCCreator, UGCCreatorIdentity, UGCRightsPassport, UGCSubmission
from .ugc_permissions import DECLINED, GRANTED, REQUESTED, get_permission
from .ugc_provenance import get_provenance


def normalize_creator_handle(value):
    return str(value or "").strip().lstrip("@").lower()[:255]


def creator_profile_url(platform, handle):
    handle = str(handle or "").strip().lstrip("@")
    if not handle:
        return ""
    templates = {
        "instagram": "https://www.instagram.com/{handle}/",
        "facebook": "https://www.facebook.com/{handle}",
        "tiktok": "https://www.tiktok.com/@{handle}",
        "threads": "https://www.threads.net/@{handle}",
        "youtube": "https://www.youtube.com/@{handle}",
    }
    template = templates.get(str(platform or "").lower())
    return template.format(handle=handle) if template else ""


def _aware_timestamp(value):
    if not value:
        return None
    parsed = value if hasattr(value, "tzinfo") else parse_datetime(str(value))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def ensure_creator_for_submission(submission):
    """Resolve a submission to the workspace's canonical creator record."""
    provenance = get_provenance(submission.metadata)
    platform = str(provenance.get("platform") or "direct").strip().lower()[:30] or "direct"
    handle = str(submission.contributor_handle or provenance.get("creator_handle") or "").strip().lstrip("@")[:255]
    normalized_handle = normalize_creator_handle(handle)
    external_id = str(submission.contributor_external_id or "").strip()[:255]
    if not normalized_handle and not external_id:
        return None

    identity = None
    identities = UGCCreatorIdentity.objects.for_workspace(submission.workspace_id).filter(platform=platform)
    if external_id:
        identity = identities.select_related("creator").filter(external_id=external_id).first()
    if identity is None and normalized_handle:
        identity = identities.select_related("creator").filter(normalized_handle=normalized_handle).first()

    seen_at = submission.submitted_at or timezone.now()
    if identity is None:
        try:
            with transaction.atomic():
                creator = UGCCreator.objects.create(
                    workspace_id=submission.workspace_id,
                    display_name=str(submission.contributor_name or "").strip()[:255],
                    preferred_credit=f"@{handle}" if handle else str(submission.contributor_name or "").strip()[:255],
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                )
                identity = UGCCreatorIdentity.objects.create(
                    workspace_id=submission.workspace_id,
                    creator=creator,
                    platform=platform,
                    external_id=external_id,
                    handle=handle,
                    normalized_handle=normalized_handle,
                    profile_url=creator_profile_url(platform, handle),
                    is_primary=True,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                )
        except IntegrityError:
            identity = identities.select_related("creator").filter(normalized_handle=normalized_handle).first()
            if identity is None and external_id:
                identity = identities.select_related("creator").filter(external_id=external_id).first()
            if identity is None:
                raise
    creator = identity.creator

    identity_updates = []
    handle_available = (
        not normalized_handle
        or not identities.exclude(id=identity.id).filter(normalized_handle=normalized_handle).exists()
    )
    if handle and identity.handle != handle and handle_available:
        identity.handle = handle
        identity_updates.append("handle")
        if identity.normalized_handle != normalized_handle:
            identity.normalized_handle = normalized_handle
            identity_updates.append("normalized_handle")
    if external_id and not identity.external_id:
        identity.external_id = external_id
        identity_updates.append("external_id")
    profile_url = creator_profile_url(platform, identity.handle)
    if profile_url and identity.profile_url != profile_url:
        identity.profile_url = profile_url
        identity_updates.append("profile_url")
    if seen_at > identity.last_seen_at:
        identity.last_seen_at = seen_at
        identity_updates.append("last_seen_at")
    if identity_updates:
        identity.save(update_fields=[*identity_updates, "updated_at"])

    permission = get_permission(submission.metadata)
    permission_updated_at = _aware_timestamp(permission.get("updated_at"))
    creator_updates = []
    display_name = str(submission.contributor_name or "").strip()[:255]
    if display_name and not creator.display_name:
        creator.display_name = display_name
        creator_updates.append("display_name")
    if seen_at < creator.first_seen_at:
        creator.first_seen_at = seen_at
        creator_updates.append("first_seen_at")
    if seen_at > creator.last_seen_at:
        creator.last_seen_at = seen_at
        creator_updates.append("last_seen_at")
    if permission_updated_at and (
        creator.last_contacted_at is None or permission_updated_at > creator.last_contacted_at
    ):
        creator.last_contacted_at = permission_updated_at
        creator_updates.append("last_contacted_at")
    if (
        submission.consent_confirmed
        and submission.consent_at
        and (creator.last_permission_granted_at is None or submission.consent_at > creator.last_permission_granted_at)
    ):
        creator.last_permission_granted_at = submission.consent_at
        creator_updates.append("last_permission_granted_at")

    protected_stages = {
        UGCCreator.RelationshipStage.TRUSTED,
        UGCCreator.RelationshipStage.PARTNER,
        UGCCreator.RelationshipStage.DO_NOT_CONTACT,
    }
    next_stage = creator.relationship_stage
    if creator.relationship_stage not in protected_stages:
        if submission.consent_confirmed or permission["status"] == GRANTED:
            next_stage = UGCCreator.RelationshipStage.PERMISSIONED
        elif permission["status"] in {REQUESTED, DECLINED}:
            next_stage = UGCCreator.RelationshipStage.CONTACTED
    if next_stage != creator.relationship_stage:
        creator.relationship_stage = next_stage
        creator_updates.append("relationship_stage")
    if creator_updates:
        creator.save(update_fields=[*dict.fromkeys(creator_updates), "updated_at"])

    if submission.creator_id != creator.id:
        UGCSubmission.objects.filter(id=submission.id).update(creator=creator)
        submission.creator_id = creator.id
        submission.creator = creator
    return creator


def sync_rights_passport_from_submission(submission):
    """Create or advance a rights passport from the legacy permission fields."""
    permission = get_permission(submission.metadata)
    if submission.consent_confirmed or permission["status"] == GRANTED:
        derived_status = UGCRightsPassport.Status.GRANTED
    elif permission["status"] == REQUESTED:
        derived_status = UGCRightsPassport.Status.REQUESTED
    elif permission["status"] == DECLINED:
        derived_status = UGCRightsPassport.Status.DECLINED
    else:
        derived_status = UGCRightsPassport.Status.NOT_REQUESTED

    provenance = get_provenance(submission.metadata)
    handle = str(submission.contributor_handle or provenance.get("creator_handle") or "").strip().lstrip("@")
    credit_text = ""
    creator = getattr(submission, "creator", None)
    if creator:
        credit_text = creator.preferred_credit
    credit_text = credit_text or (f"@{handle}" if handle else submission.contributor_name)
    passport, created = UGCRightsPassport.objects.get_or_create(
        submission=submission,
        defaults={
            "workspace_id": submission.workspace_id,
            "status": derived_status,
            "allow_organic_social": submission.consent_confirmed,
            "allow_website": submission.consent_confirmed,
            "credit_required": True,
            "credit_text": str(credit_text or "").strip()[:500],
            "evidence_url": str(provenance.get("source_url") or "").strip()[:2000],
            "consent_version": submission.consent_version,
            "granted_at": submission.consent_at if submission.consent_confirmed else None,
        },
    )
    if created:
        return passport

    updates = []
    prior_status = passport.status
    if (
        passport.status not in {UGCRightsPassport.Status.REVOKED, UGCRightsPassport.Status.EXPIRED}
        and passport.status != derived_status
    ):
        passport.status = derived_status
        updates.append("status")
    if (
        derived_status == UGCRightsPassport.Status.GRANTED
        and prior_status != UGCRightsPassport.Status.GRANTED
        and submission.consent_confirmed
    ):
        passport.allow_organic_social = True
        passport.allow_website = True
        updates.extend(["allow_organic_social", "allow_website"])
    if submission.consent_version and passport.consent_version != submission.consent_version:
        passport.consent_version = submission.consent_version
        updates.append("consent_version")
    if submission.consent_at and passport.granted_at != submission.consent_at:
        passport.granted_at = submission.consent_at
        updates.append("granted_at")
    if not passport.credit_text and credit_text:
        passport.credit_text = str(credit_text)[:500]
        updates.append("credit_text")
    evidence_url = str(provenance.get("source_url") or "").strip()[:2000]
    if evidence_url and not passport.evidence_url:
        passport.evidence_url = evidence_url
        updates.append("evidence_url")
    if updates:
        passport.save(update_fields=[*updates, "updated_at"])
    return passport


def synchronize_submission_relationship(submission):
    ensure_creator_for_submission(submission)
    return sync_rights_passport_from_submission(submission)


def rights_can_use(submission, scope="organic_social"):
    """Return whether the current passport authorizes one usage scope."""
    try:
        passport = submission.rights_passport
    except UGCRightsPassport.DoesNotExist:
        passport = sync_rights_passport_from_submission(submission)
    if (
        passport.status == UGCRightsPassport.Status.GRANTED
        and passport.expires_at
        and passport.expires_at <= timezone.now()
    ):
        return False, "Creator permission has expired. Update the rights passport before reusing this content."
    if passport.status != UGCRightsPassport.Status.GRANTED:
        return (
            False,
            f"Rights are {passport.get_status_display().lower()}. Grant permission before reusing this content.",
        )
    field = f"allow_{scope}"
    if not getattr(passport, field, False):
        return False, f"The rights passport does not allow {scope.replace('_', ' ')} use."
    return True, ""
