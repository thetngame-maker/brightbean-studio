"""Outreach follow-up tracking for discovered community content."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCSubmission
from .ugc_permissions import REQUESTED, get_permission
from .ugc_provenance import get_provenance
from .ugc_views import _discovered_q, _get_workspace


def _return_to(request, workspace_id):
    value = str(request.POST.get("return_to") or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return f"/workspace/{workspace_id}/community-content/?tab=discovered"


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def log_followup(request, workspace_id, submission_id):
    """Record that a follow-up permission message was sent.

    This does not change the permission state or overwrite the original
    permission-request timestamp. Follow-ups are kept in their own outreach
    metadata so we retain both first-contact and last-contact history.
    """
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(status=UGCSubmission.Status.PENDING)
        .filter(_discovered_q()),
        id=submission_id,
    )

    return_to = _return_to(request, workspace.id)
    permission = get_permission(submission.metadata)
    if permission.get("status") != REQUESTED:
        messages.error(request, "Follow-ups can only be logged after permission has been requested.")
        return redirect(return_to)

    now = timezone.now()
    metadata = dict(submission.metadata or {})
    outreach = metadata.get("outreach") if isinstance(metadata.get("outreach"), dict) else {}
    outreach = dict(outreach)
    outreach["requested_at"] = outreach.get("requested_at") or permission.get("updated_at") or now.isoformat()
    outreach["last_followup_at"] = now.isoformat()
    outreach["followup_count"] = max(0, int(outreach.get("followup_count") or 0)) + 1
    outreach["last_followup_channel"] = str(request.POST.get("channel") or permission.get("channel") or "manual").strip()[:50]
    metadata["outreach"] = outreach

    submission.metadata = metadata
    submission.save(update_fields=["metadata", "updated_at"])

    provenance = get_provenance(metadata)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.permission_followup_sent",
        target=submission,
        target_label=str(submission),
        metadata={
            "followup_count": outreach["followup_count"],
            "channel": outreach["last_followup_channel"],
            "source_platform": provenance.get("platform", ""),
            "discovery_source": provenance.get("discovery_source", ""),
        },
        request=request,
    )

    messages.success(
        request,
        f"Follow-up #{outreach['followup_count']} logged for @{submission.contributor_handle or 'creator'}.",
    )
    return redirect(return_to)
