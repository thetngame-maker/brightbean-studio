"""Reusable services for community-content moderation and reporting."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .audit import record_audit_event
from .models import UGCModerationEvent, UGCReport, UGCSubmission


_MODERATION_TARGETS = {
    UGCModerationEvent.Action.APPROVE: UGCSubmission.Status.APPROVED,
    UGCModerationEvent.Action.REJECT: UGCSubmission.Status.REJECTED,
    UGCModerationEvent.Action.REMOVE: UGCSubmission.Status.REMOVED,
    UGCModerationEvent.Action.RESTORE: UGCSubmission.Status.APPROVED,
}


@transaction.atomic
def moderate_submission(*, submission, action, actor=None, note="", request=None):
    """Apply a moderation decision and append both moderation + audit history.

    Approval and restore are blocked until contributor consent is explicitly
    recorded. This makes consent a server-side publication invariant rather
    than merely a checkbox the UI is expected to remember.
    """

    if action == UGCModerationEvent.Action.NOTE:
        UGCModerationEvent.objects.create(
            submission=submission,
            actor=actor,
            action=action,
            from_status=submission.status,
            to_status=submission.status,
            note=note,
        )
        record_audit_event(
            workspace=submission.workspace,
            actor=actor,
            action="ugc.moderation_note_added",
            target=submission,
            target_label=str(submission),
            metadata={"note": note[:500]},
            request=request,
        )
        return submission

    try:
        new_status = _MODERATION_TARGETS[action]
    except KeyError as exc:
        raise ValidationError("Unsupported moderation action.") from exc

    if new_status == UGCSubmission.Status.APPROVED and not submission.consent_confirmed:
        raise ValidationError("This submission cannot be approved until contributor consent is recorded.")

    old_status = submission.status
    now = timezone.now()
    submission.status = new_status
    submission.moderated_by = actor
    submission.moderated_at = now
    submission.moderation_note = note

    update_fields = ["status", "moderated_by", "moderated_at", "moderation_note", "updated_at"]
    if new_status == UGCSubmission.Status.APPROVED:
        if not submission.published_at:
            submission.published_at = now
            update_fields.append("published_at")
    elif new_status == UGCSubmission.Status.REMOVED:
        # Preserve original published_at as useful history; status controls
        # whether content is currently displayable.
        pass

    submission.save(update_fields=update_fields)

    UGCModerationEvent.objects.create(
        submission=submission,
        actor=actor,
        action=action,
        from_status=old_status,
        to_status=new_status,
        note=note,
    )
    record_audit_event(
        workspace=submission.workspace,
        actor=actor,
        action=f"ugc.{action}",
        target=submission,
        target_label=str(submission),
        metadata={"from_status": old_status, "to_status": new_status, "note": note[:500]},
        request=request,
    )
    return submission


@transaction.atomic
def report_submission(
    *,
    submission,
    reason,
    reporter=None,
    reporter_external_id="",
    reporter_name="",
    details="",
    request=None,
):
    """Create a report and record it in the shared audit trail."""

    report = UGCReport.objects.create(
        workspace=submission.workspace,
        submission=submission,
        reporter=reporter,
        reporter_external_id=reporter_external_id,
        reporter_name=reporter_name,
        reason=reason,
        details=details,
    )
    record_audit_event(
        workspace=submission.workspace,
        actor=reporter,
        action="ugc.reported",
        target=submission,
        target_label=str(submission),
        metadata={"report_id": str(report.id), "reason": reason},
        request=request,
    )
    return report


@transaction.atomic
def resolve_report(*, report, status, actor=None, note="", request=None):
    """Resolve or dismiss a moderation report."""

    allowed = {UGCReport.Status.RESOLVED, UGCReport.Status.DISMISSED}
    if status not in allowed:
        raise ValidationError("Reports can only be resolved or dismissed by this action.")

    report.status = status
    report.handled_by = actor
    report.handled_at = timezone.now()
    report.resolution_note = note
    report.save(update_fields=["status", "handled_by", "handled_at", "resolution_note", "updated_at"])

    record_audit_event(
        workspace=report.workspace,
        actor=actor,
        action=f"ugc.report_{status}",
        target=report.submission,
        target_label=str(report.submission),
        metadata={"report_id": str(report.id), "note": note[:500]},
        request=request,
    )
    return report
