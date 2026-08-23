"""Lightweight target correction helpers for mobile Approved UGC review."""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCSubmission
from .ugc_mobile_quality import _normalise, approved_quality, decorate_approved_quality
from .ugc_views import _get_workspace


def _learned_target_key(workspace, suggested_label):
    """Return a recently learned target for a caption alias, if one exists.

    Corrections live on the submission metadata so this remains migration-free.
    We intentionally keep the lookup bounded and treat the result as a suggestion
    only; nothing is silently retargeted.
    """
    alias_norm = _normalise(suggested_label)
    if not alias_norm:
        return None
    recent = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .only("metadata")
        .order_by("-updated_at")[:250]
    )
    for submission in recent:
        correction = (submission.metadata or {}).get("target_correction") or {}
        if correction.get("alias_norm") != alias_norm:
            continue
        target_type = (correction.get("to_target_type") or "").strip()
        target_id = (correction.get("to_target_id") or "").strip()
        if target_type and target_id:
            return target_type, target_id
    return None


def target_choices(workspace, *, suggested_label="", current_submission=None, limit=80):
    """Return known workspace UGC targets, with learned/caption suggestions first."""
    rows = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .exclude(target_id="")
        .exclude(target_label="")
        .values("target_type", "target_id", "target_label", "target_url")
        .order_by("target_label")
        .distinct()
    )
    suggested_norm = _normalise(suggested_label)
    learned_key = _learned_target_key(workspace, suggested_label)
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
        exact_match = bool(suggested_norm and _normalise(row["target_label"]) == suggested_norm)
        learned_match = bool(learned_key and key == learned_key)
        row["is_current"] = key == current_key
        row["is_suggested"] = exact_match or learned_match
        row["suggestion_source"] = "learned" if learned_match and not exact_match else ("caption" if exact_match else "")
        row["picker_value"] = f'{row["target_type"]}::{row["target_id"]}'
        choices.append(row)
        if len(choices) >= limit:
            break
    choices.sort(key=lambda item: (not item["is_suggested"], item["is_current"], item["target_label"].lower()))
    return choices


def _safe_return(request, workspace):
    return_to = request.POST.get("return_to", "").strip()
    if return_to.startswith("/"):
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


def _next_quality_check_url(workspace, *, return_to=""):
    """Return the next undrafted approved item that still needs a check."""
    candidates = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(status=UGCSubmission.Status.APPROVED)
        .select_related("media_asset")
        .order_by("-submitted_at")[:250]
    )
    for candidate in candidates:
        if (candidate.metadata or {}).get("studio_post_ids"):
            continue
        decorate_approved_quality(candidate)
        if not candidate.mobile_needs_quality_check:
            continue
        review_url = reverse(
            "ugc:mobile_review",
            kwargs={"workspace_id": workspace.id, "submission_id": candidate.id},
        )
        query = {"tab": "approved", "draft_state": "check"}
        if return_to.startswith("/"):
            query["return_to"] = return_to
        return f"{review_url}?{urlencode(query)}"
    return ""


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def retarget_submission(request, workspace_id, submission_id):
    """Move one UGC item to a known workspace target without weakening its audit trail."""
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(UGCSubmission, id=submission_id, workspace=workspace)
    return_to = request.POST.get("return_to", "").strip()
    was_check_queue = "draft_state=check" in return_to

    target_key = request.POST.get("target_key", "").strip()
    if "::" in target_key:
        target_type, target_id = target_key.split("::", 1)
    else:
        target_type = request.POST.get("target_type", "").strip()
        target_id = request.POST.get("target_id", "").strip()
    target_type = target_type[:100]
    target_id = target_id[:255]

    candidate = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(target_type=target_type, target_id=target_id)
        .exclude(target_label="")
        .values("target_type", "target_id", "target_label", "target_url")
        .first()
    )
    if not candidate:
        messages.error(request, "Choose a known TN Game target.")
        return _safe_return(request, workspace)

    quality_before = approved_quality(submission)
    learned_alias = (quality_before.get("suggested_target_label") or "").strip()

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

    metadata = dict(submission.metadata or {})
    metadata.pop("approved_quality_override", None)
    if learned_alias and _normalise(learned_alias) != _normalise(candidate["target_label"]):
        metadata["target_correction"] = {
            "alias": learned_alias,
            "alias_norm": _normalise(learned_alias),
            "from_target_type": old_target["target_type"],
            "from_target_id": old_target["target_id"],
            "from_target_label": old_target["target_label"],
            "to_target_type": candidate["target_type"],
            "to_target_id": candidate["target_id"],
            "to_target_label": candidate["target_label"],
            "corrected_at": timezone.now().isoformat(),
            "corrected_by": str(request.user.id),
        }
    submission.metadata = metadata
    submission.save(
        update_fields=["target_type", "target_id", "target_label", "target_url", "metadata", "updated_at"]
    )

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
            "learned_alias": learned_alias,
        },
        request=request,
    )
    messages.success(request, f"Target changed to {submission.target_label}.")

    if was_check_queue:
        next_url = _next_quality_check_url(workspace, return_to=return_to)
        if next_url:
            return redirect(next_url)
    return _safe_return(request, workspace)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def mark_quality_checked(request, workspace_id, submission_id):
    """Explicitly clear the current conservative quality warning after human review."""
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(UGCSubmission, id=submission_id, workspace=workspace)
    quality = approved_quality(submission)

    if not quality.get("needs_check"):
        messages.info(request, "This item no longer needs a quality check.")
        return _safe_return(request, workspace)

    metadata = dict(submission.metadata or {})
    metadata["approved_quality_override"] = {
        "fingerprint": quality.get("fingerprint", ""),
        "kind": quality.get("kind", ""),
        "reason": quality.get("reason", ""),
        "reviewed_at": timezone.now().isoformat(),
        "reviewed_by": str(request.user.id),
    }
    submission.metadata = metadata
    submission.save(update_fields=["metadata", "updated_at"])

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.quality_check_cleared",
        target=submission,
        target_label=str(submission),
        metadata={
            "kind": quality.get("kind", ""),
            "reason": quality.get("reason", ""),
            "fingerprint": quality.get("fingerprint", ""),
        },
        request=request,
    )
    messages.success(request, "Quality check cleared. The item is ready to use.")
    return _safe_return(request, workspace)
