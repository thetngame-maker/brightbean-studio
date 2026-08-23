"""Secure creator-facing requests that update the canonical UGC Rights Passport."""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .audit import record_audit_event
from .models import AuditEvent, UGCCreatorRightsRequest, UGCRightsPassport, UGCSubmission
from .ugc_creator_services import sync_rights_passport_from_submission
from .ugc_creator_task_services import sync_collaboration_rights_task
from .ugc_permissions import DECLINED, GRANTED, get_permission, set_permission

CONSENT_VERSION = "creator-rights-portal-v1"
SCOPE_FIELDS = (
    ("organic_social", "allow_organic_social", "Organic social"),
    ("website", "allow_website", "TN Game website"),
    ("email", "allow_email", "Email/newsletters"),
    ("paid_ads", "allow_paid_ads", "Paid advertising"),
    ("print", "allow_print", "Print materials"),
)


class RightsRequestError(ValueError):
    pass


def generate_rights_request_token():
    return secrets.token_urlsafe(32)


def hash_rights_request_token(token):
    return salted_hmac("ugc-creator-rights-request", str(token or ""), secret=settings.SECRET_KEY).hexdigest()


def token_matches(rights_request, token):
    return constant_time_compare(rights_request.token_hash, hash_rights_request_token(token))


def requested_scopes(rights_request):
    return [key for key, field, _label in SCOPE_FIELDS if getattr(rights_request, field)]


def create_creator_rights_request(
    submission,
    *,
    actor,
    expires_in_days=14,
    allow_organic_social=True,
    allow_website=True,
    allow_email=False,
    allow_paid_ads=False,
    allow_print=False,
    credit_required=True,
    credit_text="",
):
    """Create one encrypted bearer link and supersede older unanswered links."""
    days = max(1, min(90, int(expires_in_days or 14)))
    scopes = {
        "allow_organic_social": bool(allow_organic_social),
        "allow_website": bool(allow_website),
        "allow_email": bool(allow_email),
        "allow_paid_ads": bool(allow_paid_ads),
        "allow_print": bool(allow_print),
    }
    if not any(scopes.values()):
        raise RightsRequestError("Choose at least one requested usage scope.")
    token = generate_rights_request_token()
    now = timezone.now()
    with transaction.atomic():
        superseded = UGCCreatorRightsRequest.objects.select_for_update().filter(
            submission=submission,
            status=UGCCreatorRightsRequest.Status.PENDING,
        )
        superseded_count = superseded.update(
            status=UGCCreatorRightsRequest.Status.SUPERSEDED,
            responded_at=now,
            updated_at=now,
        )
        rights_request = UGCCreatorRightsRequest.objects.create(
            workspace=submission.workspace,
            submission=submission,
            request_token=token,
            token_hash=hash_rights_request_token(token),
            token_hint=token[-6:],
            consent_version=CONSENT_VERSION,
            expires_at=now + timedelta(days=days),
            credit_required=bool(credit_required),
            credit_text=str(credit_text or "").strip()[:500],
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
            **scopes,
        )
    return rights_request, superseded_count


def find_creator_rights_request(token):
    token = str(token or "").strip()
    if not token or len(token) > 200:
        return None
    rights_request = (
        UGCCreatorRightsRequest.objects.select_related(
            "workspace",
            "submission",
            "submission__creator",
            "submission__media_asset",
            "submission__rights_passport",
        )
        .filter(token_hash=hash_rights_request_token(token))
        .first()
    )
    if rights_request and not token_matches(rights_request, token):
        return None
    return rights_request


def expire_creator_rights_request(rights_request):
    if rights_request.status == rights_request.Status.PENDING and rights_request.expires_at <= timezone.now():
        rights_request.status = rights_request.Status.EXPIRED
        rights_request.responded_at = timezone.now()
        rights_request.save(update_fields=["status", "responded_at", "updated_at"])
    return rights_request


def cancel_pending_rights_requests_for_submission(submission):
    """Close stale bearer links after a manual final permission decision or removal."""
    permission = get_permission(submission.metadata)
    should_cancel = permission["status"] in {GRANTED, DECLINED} or submission.status in {
        UGCSubmission.Status.REJECTED,
        UGCSubmission.Status.REMOVED,
    }
    if not should_cancel:
        return 0
    now = timezone.now()
    return UGCCreatorRightsRequest.objects.filter(
        submission=submission,
        status=UGCCreatorRightsRequest.Status.PENDING,
    ).update(status=UGCCreatorRightsRequest.Status.CANCELLED, responded_at=now, updated_at=now)


def respond_to_creator_rights_request(rights_request, *, action, selected_scopes=None, credit_text=""):
    """Apply a creator response to permission metadata and the canonical Rights Passport."""
    selected_scopes = {str(value) for value in (selected_scopes or [])}
    with transaction.atomic():
        rights_request = (
            UGCCreatorRightsRequest.objects.select_for_update()
            .select_related("workspace", "submission", "submission__creator")
            .get(id=rights_request.id)
        )
        expire_creator_rights_request(rights_request)
        if rights_request.status != rights_request.Status.PENDING:
            return rights_request, False
        if action not in {GRANTED, DECLINED}:
            raise RightsRequestError("Choose grant or decline.")

        submission = UGCSubmission.objects.select_for_update().get(id=rights_request.submission_id)
        now = timezone.now()
        granted = []
        if action == GRANTED:
            available = set(requested_scopes(rights_request))
            granted = sorted(selected_scopes & available)
            if not granted:
                raise RightsRequestError("Keep at least one usage option selected, or decline the request.")
            submission.consent_confirmed = True
            submission.consent_version = rights_request.consent_version
            submission.consent_at = now
            submission.metadata = set_permission(
                submission.metadata,
                status=GRANTED,
                channel="creator_rights_portal",
                note=f"Creator granted secure rights request {rights_request.id}.",
                updated_at=now.isoformat(),
            )
            submission.save(
                update_fields=["consent_confirmed", "consent_version", "consent_at", "metadata", "updated_at"]
            )
            passport = sync_rights_passport_from_submission(submission)
            passport.status = UGCRightsPassport.Status.GRANTED
            for key, field, _label in SCOPE_FIELDS:
                setattr(passport, field, key in granted)
            passport.allowed_account_ids = []
            passport.credit_required = rights_request.credit_required
            passport.credit_text = (
                str(credit_text or "").strip()[:500]
                or rights_request.credit_text
                or passport.credit_text
            )
            passport.consent_version = rights_request.consent_version
            passport.granted_at = now
            passport.revoked_at = None
            evidence = f"Creator granted secure rights request {rights_request.id} at {now.isoformat()}."
            passport.evidence_note = f"{passport.evidence_note}\n{evidence}".strip()[:5000]
            passport.recorded_by = None
            passport.save()
            rights_request.status = rights_request.Status.GRANTED
        else:
            submission.consent_confirmed = False
            submission.consent_version = ""
            submission.consent_at = None
            submission.metadata = set_permission(
                submission.metadata,
                status=DECLINED,
                channel="creator_rights_portal",
                note=f"Creator declined secure rights request {rights_request.id}.",
                updated_at=now.isoformat(),
            )
            submission.save(
                update_fields=["consent_confirmed", "consent_version", "consent_at", "metadata", "updated_at"]
            )
            passport = sync_rights_passport_from_submission(submission)
            passport.status = UGCRightsPassport.Status.DECLINED
            passport.allow_organic_social = False
            passport.allow_website = False
            passport.allow_email = False
            passport.allow_paid_ads = False
            passport.allow_print = False
            passport.recorded_by = None
            passport.save()
            rights_request.status = rights_request.Status.DECLINED

        rights_request.granted_scopes = granted
        rights_request.responded_at = now
        rights_request.save(update_fields=["status", "granted_scopes", "responded_at", "updated_at"])
        sync_collaboration_rights_task(passport)
        record_audit_event(
            workspace=rights_request.workspace,
            actor=None,
            action=f"ugc.creator_rights_{action}",
            target=rights_request,
            metadata={
                "submission_id": str(submission.id),
                "consent_version": rights_request.consent_version,
                "granted_scopes": granted,
                "credit_required": rights_request.credit_required,
            },
            source=AuditEvent.Source.API,
        )
    return rights_request, True
