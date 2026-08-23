"""Secure creator-facing acceptance links for collaboration briefs."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .audit import record_audit_event
from .models import (
    AuditEvent,
    UGCCreator,
    UGCCreatorCollaboration,
    UGCCreatorCollaborationInvite,
    UGCCreatorTask,
)

ACCEPTED = "accepted"
DECLINED = "declined"


class CollaborationInviteError(ValueError):
    pass


def _token_hash(token):
    return salted_hmac("ugc-creator-collaboration-invite", str(token or ""), secret=settings.SECRET_KEY).hexdigest()


def _terms_snapshot(collaboration):
    return {
        "title": collaboration.title,
        "brief": collaboration.brief,
        "deliverables": collaboration.deliverables,
        "offer": collaboration.offer,
        "target_type": collaboration.target_type,
        "target_id": collaboration.target_id,
        "target_label": collaboration.target_label,
        "target_url": collaboration.target_url,
        "requested_rights": list(collaboration.requested_rights or []),
        "content_due_at": collaboration.content_due_at.isoformat() if collaboration.content_due_at else "",
    }


def _terms_digest(snapshot):
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_collaboration_invite(collaboration, *, actor, expires_in_days=14):
    """Snapshot the current brief and create one encrypted bearer link."""
    if collaboration.creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT:
        raise CollaborationInviteError("This creator is marked Do not contact.")
    allowed_statuses = {
        UGCCreatorCollaboration.Status.DRAFT,
        UGCCreatorCollaboration.Status.INVITED,
        UGCCreatorCollaboration.Status.INTERESTED,
    }
    if collaboration.status not in allowed_statuses:
        raise CollaborationInviteError("This collaboration can no longer receive a creator response link.")
    days = max(1, min(30, int(expires_in_days or 14)))
    token = secrets.token_urlsafe(32)
    snapshot = _terms_snapshot(collaboration)
    now = timezone.now()
    with transaction.atomic():
        superseded_count = (
            UGCCreatorCollaborationInvite.objects.select_for_update()
            .filter(collaboration=collaboration, status=UGCCreatorCollaborationInvite.Status.PENDING)
            .update(
                status=UGCCreatorCollaborationInvite.Status.SUPERSEDED,
                responded_at=now,
                updated_at=now,
            )
        )
        invite = UGCCreatorCollaborationInvite.objects.create(
            workspace=collaboration.workspace,
            collaboration=collaboration,
            request_token=token,
            token_hash=_token_hash(token),
            token_hint=token[-6:],
            terms_snapshot=snapshot,
            terms_digest=_terms_digest(snapshot),
            expires_at=now + timedelta(days=days),
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
    return invite, superseded_count


def find_collaboration_invite(token):
    token = str(token or "").strip()
    if not token or len(token) > 200:
        return None
    invite = (
        UGCCreatorCollaborationInvite.objects.select_related(
            "workspace",
            "collaboration",
            "collaboration__creator",
        )
        .prefetch_related("collaboration__creator__identities")
        .filter(token_hash=_token_hash(token))
        .first()
    )
    if invite and not constant_time_compare(invite.token_hash, _token_hash(token)):
        return None
    return invite


def expire_collaboration_invite(invite):
    if invite.status == invite.Status.PENDING and invite.expires_at <= timezone.now():
        invite.status = invite.Status.EXPIRED
        invite.responded_at = timezone.now()
        invite.save(update_fields=["status", "responded_at", "updated_at"])
    return invite


def close_pending_collaboration_invites(collaboration, *, status=None):
    """Invalidate public links after terms change or the workflow closes."""
    status = status or UGCCreatorCollaborationInvite.Status.CANCELLED
    if status not in {
        UGCCreatorCollaborationInvite.Status.CANCELLED,
        UGCCreatorCollaborationInvite.Status.SUPERSEDED,
    }:
        raise ValueError("Unsupported collaboration invite close status.")
    now = timezone.now()
    return UGCCreatorCollaborationInvite.objects.filter(
        collaboration=collaboration,
        status=UGCCreatorCollaborationInvite.Status.PENDING,
    ).update(status=status, responded_at=now, updated_at=now)


def _complete_open_tasks(collaboration, now):
    UGCCreatorTask.objects.filter(
        collaboration=collaboration,
        status=UGCCreatorTask.Status.OPEN,
    ).update(status=UGCCreatorTask.Status.DONE, completed_at=now, completed_by=None, updated_at=now)


def respond_to_collaboration_invite(invite, *, action, response_note=""):
    """Apply a creator decision to the canonical collaboration and task queue."""
    with transaction.atomic():
        invite = (
            UGCCreatorCollaborationInvite.objects.select_for_update()
            .select_related("workspace", "collaboration", "collaboration__creator")
            .get(id=invite.id)
        )
        expire_collaboration_invite(invite)
        if invite.status != invite.Status.PENDING:
            return invite, False
        if action not in {ACCEPTED, DECLINED}:
            raise CollaborationInviteError("Choose accept or decline.")

        collaboration = UGCCreatorCollaboration.objects.select_for_update().select_related("creator").get(
            id=invite.collaboration_id
        )
        allowed_statuses = {
            UGCCreatorCollaboration.Status.DRAFT,
            UGCCreatorCollaboration.Status.INVITED,
            UGCCreatorCollaboration.Status.INTERESTED,
        }
        if collaboration.status not in allowed_statuses:
            invite.status = invite.Status.CANCELLED
            invite.responded_at = timezone.now()
            invite.save(update_fields=["status", "responded_at", "updated_at"])
            return invite, False
        if _terms_digest(_terms_snapshot(collaboration)) != invite.terms_digest:
            invite.status = invite.Status.SUPERSEDED
            invite.responded_at = timezone.now()
            invite.save(update_fields=["status", "responded_at", "updated_at"])
            return invite, False

        now = timezone.now()
        _complete_open_tasks(collaboration, now)
        if action == ACCEPTED:
            invite.status = invite.Status.ACCEPTED
            collaboration.status = UGCCreatorCollaboration.Status.CONFIRMED
            collaboration.invited_at = collaboration.invited_at or now
            due_at = collaboration.content_due_at or now + timedelta(days=7)
            UGCCreatorTask.objects.create(
                workspace=collaboration.workspace,
                creator=collaboration.creator,
                collaboration=collaboration,
                kind=UGCCreatorTask.Kind.COLLABORATION,
                title=f"Check in on deliverables · {collaboration.title}"[:255],
                note="Creator accepted the secure collaboration brief.",
                due_at=due_at,
            )
        else:
            invite.status = invite.Status.DECLINED
            collaboration.status = UGCCreatorCollaboration.Status.DECLINED
        collaboration.creator.last_contacted_at = now
        if collaboration.creator.relationship_stage == UGCCreator.RelationshipStage.PROSPECT:
            collaboration.creator.relationship_stage = UGCCreator.RelationshipStage.CONTACTED
        collaboration.creator.save(update_fields=["last_contacted_at", "relationship_stage", "updated_at"])
        collaboration.completed_at = None
        collaboration.save(update_fields=["status", "invited_at", "completed_at", "updated_at"])
        invite.response_note = str(response_note or "").strip()[:2000]
        invite.responded_at = now
        invite.save(update_fields=["status", "response_note", "responded_at", "updated_at"])

        record_audit_event(
            workspace=invite.workspace,
            actor=None,
            action=f"ugc.creator_collaboration_{action}",
            target=collaboration,
            source=AuditEvent.Source.API,
            metadata={
                "collaboration_id": str(collaboration.id),
                "invite_id": str(invite.id),
                "terms_digest": invite.terms_digest,
                "has_response_note": bool(invite.response_note),
            },
        )
    return invite, True
