"""Lightweight bulk moderation actions for the mobile Community queue."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .models import UGCModerationEvent, UGCSubmission
from .ugc import moderate_submission
from .ugc_views import _get_workspace


MAX_BULK_ITEMS = 50


def _safe_return_to(request, workspace):
    return_to = (request.POST.get("return_to") or "").strip()
    if return_to.startswith("/") and not return_to.startswith("//"):
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def bulk_remove(request, workspace_id):
    """Move selected Community submissions to Removed using the normal audit path."""
    workspace = _get_workspace(request, workspace_id)

    raw_ids = request.POST.getlist("submission_ids")[:MAX_BULK_ITEMS]
    # Preserve the submitted order while removing duplicates/empty values.
    submission_ids = list(dict.fromkeys(value.strip() for value in raw_ids if value.strip()))
    if not submission_ids:
        messages.error(request, "Select at least one Community post to remove.")
        return _safe_return_to(request, workspace)

    submissions = {
        str(item.id): item
        for item in UGCSubmission.objects.for_workspace(workspace.id).filter(id__in=submission_ids)
    }

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
