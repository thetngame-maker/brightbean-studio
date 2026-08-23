"""Bulk permission actions for externally discovered UGC."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCCreator, UGCSubmission
from .ugc_permissions import GRANTED, REQUESTED, VALID_PERMISSION_STATUSES, set_permission
from .ugc_provenance import get_provenance
from .ugc_views import _discovered_q, _get_workspace

MAX_BULK_ITEMS = 100


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_permission_update(request, workspace_id):
    """Apply one permission state to selected discovered items.

    IDs are always re-scoped server-side to this workspace, pending status, and
    the discovered-content predicate. This keeps bulk actions from touching
    direct submissions or content that has already advanced in the workflow.
    """
    workspace = _get_workspace(request, workspace_id)
    status = request.POST.get("permission_status", "").strip().lower()
    if status not in VALID_PERMISSION_STATUSES:
        messages.error(request, "Choose a valid bulk permission action.")
        return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")

    raw_ids = request.POST.getlist("submission_ids")[:MAX_BULK_ITEMS]
    ids = [value for value in raw_ids if value]
    if not ids:
        messages.error(request, "Select at least one discovered item first.")
        return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")

    submissions = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(id__in=ids, status=UGCSubmission.Status.PENDING)
        .filter(_discovered_q())
        .select_related("creator")
    )
    if not submissions:
        messages.error(request, "None of the selected items are still eligible for this action.")
        return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")

    now = timezone.now()
    channel = request.POST.get("channel", "bulk").strip()[:50] or "bulk"
    consent_version = (
        request.POST.get("consent_version", "creator-permission-v1").strip()[:50] or "creator-permission-v1"
    )

    updated = 0
    skipped_do_not_contact = 0
    for submission in submissions:
        if (
            status == REQUESTED
            and submission.creator_id
            and submission.creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT
        ):
            skipped_do_not_contact += 1
            continue
        provenance = get_provenance(submission.metadata)
        submission.metadata = set_permission(
            submission.metadata,
            status=status,
            channel=channel,
            note="Bulk permission update",
            updated_at=now.isoformat(),
        )
        update_fields = ["metadata", "updated_at"]
        if status == GRANTED:
            submission.consent_confirmed = True
            submission.consent_version = consent_version
            submission.consent_at = now
            update_fields.extend(["consent_confirmed", "consent_version", "consent_at"])
        submission.save(update_fields=update_fields)
        updated += 1

        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action=f"ugc.permission_{status}",
            target=submission,
            target_label=str(submission),
            metadata={
                "permission_status": status,
                "channel": channel,
                "bulk": True,
                "discovery_source": provenance.get("discovery_source", ""),
                "source_platform": provenance.get("platform", ""),
            },
            request=request,
        )

    if skipped_do_not_contact:
        messages.warning(
            request,
            f"Skipped {skipped_do_not_contact} item{'s' if skipped_do_not_contact != 1 else ''} from creators marked Do not contact.",
        )
    if status == GRANTED:
        messages.success(
            request,
            f"Permission granted for {updated} item{'s' if updated != 1 else ''}. They moved to Pending.",
        )
    else:
        messages.success(
            request,
            f"Updated permission for {updated} discovered item{'s' if updated != 1 else ''}.",
        )
    return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")
