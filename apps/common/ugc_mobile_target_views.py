"""Lightweight target correction helpers for mobile Approved UGC review."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCSubmission
from .ugc_mobile_quality import _normalise
from .ugc_views import _get_workspace


def target_choices(workspace, *, suggested_label="", current_submission=None, limit=80):
    """Return known workspace UGC targets, with a caption-suggested match first."""
    rows = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .exclude(target_id="")
        .exclude(target_label="")
        .values("target_type", "target_id", "target_label", "target_url")
        .order_by("target_label")
        .distinct()
    )
    suggested_norm = _normalise(suggested_label)
    current_key = None
    if current_submission is not None:
        current_key = (current_submission.target_type, current_submission.target_id)

    seen = set()
    choices = []
    for row in rows:
        key = (row["target_type"], row["target_id"])
        if key in seen:
            continue
        seen.add(key)
        row = dict(row)
        row["is_current"] = key == current_key
        row["is_suggested"] = bool(suggested_norm and _normalise(row["target_label"]) == suggested_norm)
        row["picker_value"] = f'{row["target_type"]}::{row["target_id"]}'
        choices.append(row)
        if len(choices) >= limit:
            break
    choices.sort(key=lambda item: (not item["is_suggested"], item["is_current"], item["target_label"].lower()))
    return choices


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def retarget_submission(request, workspace_id, submission_id):
    """Move one UGC item to a known workspace target without weakening its audit trail."""
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(UGCSubmission, id=submission_id, workspace=workspace)

    target_key = request.POST.get("target_key", "").strip()
    if "::" in target_key:
        target_type, target_id = target_key.split("::", 1)
    else:
        target_type = request.POST.get("target_type", "").strip()
        target_id = request.POST.get("target_id", "").strip()
    target_type = target_type[:100]
    target_id = target_id[:255]
    return_to = request.POST.get("return_to", "").strip()

    candidate = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(target_type=target_type, target_id=target_id)
        .exclude(target_label="")
        .values("target_type", "target_id", "target_label", "target_url")
        .first()
    )
    if not candidate:
        messages.error(request, "Choose a known TN Game target.")
        return redirect(return_to) if return_to.startswith("/") else redirect("ugc:moderation_queue", workspace_id=workspace.id)

    old_target = {
        "target_type": submission.target_type,
        "target_id": submission.target_id,
        "target_label": submission.target_label,
        "target_url": submission.target_url,
    }
    submission.target_type = candidate["target_type"]
    submission.target_id = candidate["target_id"]
    submission.target_label = candidate["target_label"]
    submission.target_url = candidate["target_url"] or ""
    submission.save(update_fields=["target_type", "target_id", "target_label", "target_url", "updated_at"])

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.target_changed",
        target=submission,
        target_label=str(submission),
        metadata={
            "from": old_target,
            "to": {
                "target_type": submission.target_type,
                "target_id": submission.target_id,
                "target_label": submission.target_label,
                "target_url": submission.target_url,
            },
        },
        request=request,
    )
    messages.success(request, f"Target changed to {submission.target_label}.")
    return redirect(return_to) if return_to.startswith("/") else redirect("ugc:moderation_queue", workspace_id=workspace.id)
