"""Lightweight bulk moderation actions for the mobile Community queue."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCModerationEvent, UGCSubmission
from .ugc import moderate_submission
from .ugc_permissions import GRANTED, set_permission
from .ugc_provenance import get_provenance
from .ugc_views import _get_workspace


MAX_BULK_ITEMS = 50


def _safe_return_to(request, workspace):
    return_to = (request.POST.get("return_to") or "").strip()
    if return_to.startswith("/") and not return_to.startswith("//"):
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


def _selected_submissions(request, workspace):
    raw_ids = request.POST.getlist("submission_ids")[:MAX_BULK_ITEMS]
    submission_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    submissions = {
        str(item.id): item
        for item in UGCSubmission.objects.for_workspace(workspace.id).filter(id__in=submission_ids)
    }
    return submission_ids, submissions


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_remove(request, workspace_id):
    """Move selected Community submissions to Removed using the normal audit path."""
    workspace = _get_workspace(request, workspace_id)
    submission_ids, submissions = _selected_submissions(request, workspace)
    if not submission_ids:
        messages.error(request, "Select at least one Community post to remove.")
        return _safe_return_to(request, workspace)

    removed = 0
    skipped = 0
    for submission_id in submission_ids:
        submission = submissions.get(submission_id)
        if submission is None or submission.status == UGCSubmission.Status.REMOVED:
            skipped += 1
            continue
        try:
            moderate_submission(
                submission=submission,
                action=UGCModerationEvent.Action.REMOVE,
                actor=request.user,
                note="Removed in bulk from mobile Community cleanup.",
                request=request,
            )
        except ValidationError:
            skipped += 1
        else:
            removed += 1

    if removed:
        suffix = f" {skipped} skipped." if skipped else ""
        messages.success(request, f"Removed {removed} Community post{'s' if removed != 1 else ''}.{suffix}")
    else:
        messages.error(request, "No selected Community posts could be removed.")
    return _safe_return_to(request, workspace)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_grant(request, workspace_id):
    """Record creator permission for selected externally discovered submissions."""
    workspace = _get_workspace(request, workspace_id)
    submission_ids, submissions = _selected_submissions(request, workspace)
    if not submission_ids:
        messages.error(request, "Select at least one Community post to grant permission.")
        return _safe_return_to(request, workspace)

    granted = 0
    skipped = 0
    now = timezone.now()
    for submission_id in submission_ids:
        submission = submissions.get(submission_id)
        if submission is None or submission.status != UGCSubmission.Status.PENDING:
            skipped += 1
            continue
        provenance = get_provenance(submission.metadata)
        if provenance.get("discovery_source") in {"", "manual"}:
            skipped += 1
            continue

        metadata = set_permission(
            submission.metadata,
            status=GRANTED,
            channel=provenance.get("platform", "instagram"),
            note="Permission granted in bulk from mobile Community review.",
            updated_at=now.isoformat(),
        )
        submission.metadata = metadata
        submission.consent_confirmed = True
        submission.consent_version = "creator-permission-v1"
        submission.consent_at = now
        submission.save(
            update_fields=[
                "metadata",
                "consent_confirmed",
                "consent_version",
                "consent_at",
                "updated_at",
            ]
        )
        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action="ugc.permission_granted",
            target=submission,
            target_label=str(submission),
            metadata={
                "permission_status": GRANTED,
                "channel": provenance.get("platform", "instagram"),
                "discovery_source": provenance.get("discovery_source", ""),
                "source_platform": provenance.get("platform", ""),
                "bulk": True,
            },
            request=request,
        )
        granted += 1

    if granted:
        suffix = f" {skipped} skipped." if skipped else ""
        messages.success(request, f"Granted permission for {granted} Community post{'s' if granted != 1 else ''}.{suffix}")
    else:
        messages.error(request, "No selected Community posts could be granted permission.")
    return _safe_return_to(request, workspace)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_approve(request, workspace_id):
    """Approve selected pending submissions through the normal consent-enforcing moderation service."""
    workspace = _get_workspace(request, workspace_id)
    submission_ids, submissions = _selected_submissions(request, workspace)
    if not submission_ids:
        messages.error(request, "Select at least one Community post to approve.")
        return _safe_return_to(request, workspace)

    approved = 0
    skipped = 0
    for submission_id in submission_ids:
        submission = submissions.get(submission_id)
        if submission is None or submission.status != UGCSubmission.Status.PENDING:
            skipped += 1
            continue
        try:
            moderate_submission(
                submission=submission,
                action=UGCModerationEvent.Action.APPROVE,
                actor=request.user,
                note="Approved in bulk from mobile Community review.",
                request=request,
            )
        except ValidationError:
            skipped += 1
        else:
            approved += 1

    if approved:
        suffix = f" {skipped} skipped." if skipped else ""
        messages.success(request, f"Approved {approved} Community post{'s' if approved != 1 else ''}.{suffix}")
    else:
        messages.error(request, "No selected Community posts could be approved. Contributor consent is required before approval.")
    return _safe_return_to(request, workspace)
