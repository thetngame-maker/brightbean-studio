"""Automation helpers for creator relationship tasks."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .audit import record_audit_event
from .models import AuditEvent, UGCCreatorTask, UGCRightsPassport

RIGHTS_RENEWAL_NOTICE_DAYS = 14


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
