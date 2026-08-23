"""Automation helpers for creator relationship tasks."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import record_audit_event
from .models import AuditEvent, UGCCreatorCollaboration, UGCCreatorTask, UGCRightsPassport

RIGHTS_RENEWAL_NOTICE_DAYS = 14
COLLABORATION_RIGHTS_FIELDS = {
    "organic_social": "allow_organic_social",
    "website": "allow_website",
    "email": "allow_email",
    "paid_ads": "allow_paid_ads",
    "print": "allow_print",
}


def sync_collaboration_rights_task(passport):
    """Turn a creator rights response into the collaboration's next canonical task."""
    collaborations = UGCCreatorCollaboration.objects.filter(
        submission_id=passport.submission_id,
        status=UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
    ).select_related("creator", "workspace")
    now = timezone.now()
    for collaboration in collaborations:
        requested = collaboration.requested_rights or ["organic_social"]
        rights_ready = passport.is_active and all(
            getattr(passport, COLLABORATION_RIGHTS_FIELDS.get(scope, ""), False) for scope in requested
        )
        if rights_ready:
            title = f"Complete collaboration · {collaboration.title}"[:255]
            note = "Creator usage rights are active for every scope in the accepted brief."
        else:
            title = f"Resolve creator usage rights · {collaboration.title}"[:255]
            note = f"Rights are {passport.get_status_display().lower()} or do not cover every requested scope."
        open_tasks = UGCCreatorTask.objects.filter(
            collaboration=collaboration,
            status=UGCCreatorTask.Status.OPEN,
        )
        existing = open_tasks.filter(title=title).first()
        if existing:
            continue
        open_tasks.update(status=UGCCreatorTask.Status.DONE, completed_at=now, updated_at=now)
        task = UGCCreatorTask.objects.create(
            workspace=collaboration.workspace,
            creator=collaboration.creator,
            collaboration=collaboration,
            submission=passport.submission,
            kind=UGCCreatorTask.Kind.COLLABORATION,
            title=title,
            note=note,
            due_at=now,
        )
        record_audit_event(
            workspace=collaboration.workspace,
            action="ugc.creator_task_auto_created",
            target=collaboration.creator,
            source=AuditEvent.Source.SYSTEM,
            metadata={
                "task_id": str(task.id),
                "kind": task.kind,
                "submission_id": str(passport.submission_id),
                "collaboration_id": str(collaboration.id),
                "rights_ready": rights_ready,
            },
        )
    return None


def sync_rights_renewal_task(passport):
    """Keep one open renewal task aligned with a passport's expiration."""
    creator_id = passport.submission.creator_id
    open_tasks = UGCCreatorTask.objects.filter(
        workspace_id=passport.workspace_id,
        submission_id=passport.submission_id,
        kind=UGCCreatorTask.Kind.RIGHTS_RENEWAL,
        status=UGCCreatorTask.Status.OPEN,
    )
    task = open_tasks.first()
    active_expiration = (
        passport.status == UGCRightsPassport.Status.GRANTED
        and passport.expires_at is not None
        and creator_id is not None
    )
    now = timezone.now()

    if not active_expiration:
        if task is not None:
            task.status = UGCCreatorTask.Status.DISMISSED
            task.completed_at = now
            task.save(update_fields=["status", "completed_at", "updated_at"])
            record_audit_event(
                workspace=passport.workspace,
                action="ugc.creator_task_auto_dismissed",
                target=task.creator,
                source=AuditEvent.Source.SYSTEM,
                metadata={"task_id": str(task.id), "kind": task.kind, "submission_id": str(passport.submission_id)},
            )
        return None

    due_at = max(now, passport.expires_at - timedelta(days=RIGHTS_RENEWAL_NOTICE_DAYS))
    content_label = passport.submission.title or passport.submission.target_label or "community content"
    title = f"Renew rights for {content_label}"[:255]
    note = f"Creator permission expires {timezone.localtime(passport.expires_at).strftime('%b %-d, %Y')}."
    if task is None:
        try:
            with transaction.atomic():
                task = UGCCreatorTask.objects.create(
                    workspace_id=passport.workspace_id,
                    creator_id=creator_id,
                    submission_id=passport.submission_id,
                    kind=UGCCreatorTask.Kind.RIGHTS_RENEWAL,
                    title=title,
                    note=note,
                    due_at=due_at,
                )
        except IntegrityError:
            return open_tasks.first()
        record_audit_event(
            workspace=passport.workspace,
            action="ugc.creator_task_auto_created",
            target=task.creator,
            source=AuditEvent.Source.SYSTEM,
            metadata={
                "task_id": str(task.id),
                "kind": task.kind,
                "submission_id": str(passport.submission_id),
                "due_at": task.due_at.isoformat(),
            },
        )
        return task

    changed = task.due_at != due_at or task.title != title or task.note != note or task.creator_id != creator_id
    if changed:
        before_due_at = task.due_at
        task.creator_id = creator_id
        task.title = title
        task.note = note
        task.due_at = due_at
        task.save(update_fields=["creator", "title", "note", "due_at", "updated_at"])
        record_audit_event(
            workspace=passport.workspace,
            action="ugc.creator_task_auto_updated",
            target=task.creator,
            source=AuditEvent.Source.SYSTEM,
            metadata={
                "task_id": str(task.id),
                "kind": task.kind,
                "before_due_at": before_due_at.isoformat(),
                "after_due_at": task.due_at.isoformat(),
            },
        )
    return task
