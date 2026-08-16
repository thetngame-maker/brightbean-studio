"""Workspace moderation UI for community-contributed content."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.media_library.models import MediaAsset
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .audit import record_audit_event
from .models import UGCModerationEvent, UGCReport, UGCSubmission
from .ugc import moderate_submission, resolve_report


VALID_TABS = {"pending", "approved", "reported", "removed"}


def _get_workspace(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id)
    if not request.user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if not WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).exists():
        raise PermissionDenied("You are not a member of this workspace.")
    return workspace


def _queue_counts(workspace):
    base = UGCSubmission.objects.for_workspace(workspace.id)
    return {
        "pending": base.filter(status=UGCSubmission.Status.PENDING).count(),
        "approved": base.filter(status=UGCSubmission.Status.APPROVED).count(),
        "reported": base.filter(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]).distinct().count(),
        "removed": base.filter(status=UGCSubmission.Status.REMOVED).count(),
    }


def _return_to_queue(request, workspace):
    return_to = request.POST.get("return_to") or request.META.get("HTTP_REFERER")
    if return_to:
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
def moderation_queue(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    tab = request.GET.get("tab", "pending")
    if tab not in VALID_TABS:
        tab = "pending"

    qs = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .select_related("contributor", "media_asset", "moderated_by")
        .annotate(
            open_report_count=Count(
                "reports",
                filter=Q(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]),
                distinct=True,
            )
        )
    )

    if tab == "pending":
        qs = qs.filter(status=UGCSubmission.Status.PENDING)
    elif tab == "approved":
        qs = qs.filter(status=UGCSubmission.Status.APPROVED)
    elif tab == "reported":
        qs = qs.filter(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]).distinct()
    elif tab == "removed":
        qs = qs.filter(status=UGCSubmission.Status.REMOVED)

    kind = request.GET.get("kind", "").strip()
    if kind in dict(UGCSubmission.Kind.choices):
        qs = qs.filter(kind=kind)
    else:
        kind = ""

    submissions = list(qs[:100])
    reported_ids = [submission.id for submission in submissions if submission.open_report_count]
    reports_by_submission = {}
    if reported_ids:
        reports = (
            UGCReport.objects.for_workspace(workspace.id)
            .filter(
                submission_id__in=reported_ids,
                status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING],
            )
            .select_related("reporter")
            .order_by("-created_at")
        )
        for report in reports:
            reports_by_submission.setdefault(report.submission_id, []).append(report)

    for submission in submissions:
        submission.active_reports = reports_by_submission.get(submission.id, [])

    context = {
        "workspace": workspace,
        "submissions": submissions,
        "active_tab": tab,
        "active_kind": kind,
        "kind_choices": UGCSubmission.Kind.choices,
        "queue_counts": _queue_counts(workspace),
    }
    return render(request, "ugc/moderation_queue.html", context)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_manual_submission_view(request, workspace_id):
    """Create one pending item from the moderation screen.

    This is useful for end-to-end testing and for content a moderator receives
    outside the normal TN Game API flow. It intentionally creates Pending UGC;
    approval still has to go through the normal consent-gated service.
    """

    workspace = _get_workspace(request, workspace_id)
    kind = request.POST.get("kind", "").strip()
    attribution = request.POST.get("attribution", UGCSubmission.Attribution.NAME).strip()
    target_type = request.POST.get("target_type", "").strip()
    target_id = request.POST.get("target_id", "").strip()
    rating_raw = request.POST.get("rating", "").strip()
    media_asset_raw = request.POST.get("media_asset_id", "").strip()
    consent_confirmed = request.POST.get("consent_confirmed") == "on"
    consent_version = request.POST.get("consent_version", "").strip()

    errors = []
    if kind not in dict(UGCSubmission.Kind.choices):
        errors.append("Choose a valid content type.")
    if attribution not in dict(UGCSubmission.Attribution.choices):
        errors.append("Choose a valid attribution option.")
    if not target_type or not target_id:
        errors.append("Target type and target ID are required.")

    rating = None
    if rating_raw:
        try:
            rating = int(rating_raw)
        except ValueError:
            errors.append("Rating must be a number from 1 to 5.")
        else:
            if rating < 1 or rating > 5:
                errors.append("Rating must be between 1 and 5.")
    if kind == UGCSubmission.Kind.REVIEW and rating is None:
        errors.append("Reviews require a 1-5 rating.")
    if consent_confirmed and not consent_version:
        errors.append("Consent version is required when consent is confirmed.")

    media_asset = None
    if media_asset_raw:
        media_asset = MediaAsset.objects.filter(id=media_asset_raw, workspace=workspace).first()
        if media_asset is None:
            errors.append("That media asset does not belong to this workspace.")
    if kind == UGCSubmission.Kind.PHOTO and media_asset is None:
        errors.append("Photo submissions require a Media Asset ID.")

    if errors:
        for error in errors:
            messages.error(request, error)
        return _return_to_queue(request, workspace)

    submission = UGCSubmission.objects.create(
        workspace=workspace,
        kind=kind,
        status=UGCSubmission.Status.PENDING,
        source=UGCSubmission.Source.UI,
        contributor_name=request.POST.get("contributor_name", "").strip()[:255],
        contributor_handle=request.POST.get("contributor_handle", "").strip().lstrip("@")[:255],
        attribution=attribution,
        target_type=target_type[:100],
        target_id=target_id[:255],
        target_label=request.POST.get("target_label", "").strip()[:255],
        target_url=request.POST.get("target_url", "").strip()[:2000],
        media_asset=media_asset,
        title=request.POST.get("title", "").strip()[:255],
        body=request.POST.get("body", "").strip(),
        rating=rating,
        consent_confirmed=consent_confirmed,
        consent_version=consent_version[:50],
        consent_at=timezone.now() if consent_confirmed else None,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.submitted",
        target=submission,
        target_label=str(submission),
        metadata={"source": "manual", "kind": kind, "consent_confirmed": consent_confirmed},
        request=request,
    )
    messages.success(request, "Community submission added to the Pending queue.")
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def moderate_submission_view(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(UGCSubmission, id=submission_id, workspace=workspace)
    action = request.POST.get("action", "").strip()
    note = request.POST.get("note", "").strip()

    allowed = {
        UGCModerationEvent.Action.APPROVE,
        UGCModerationEvent.Action.REJECT,
        UGCModerationEvent.Action.REMOVE,
        UGCModerationEvent.Action.RESTORE,
    }
    if action not in allowed:
        messages.error(request, "That moderation action is not supported.")
    else:
        try:
            moderate_submission(
                submission=submission,
                action=action,
                actor=request.user,
                note=note,
                request=request,
            )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, f"{submission.get_kind_display()} moderation updated.")

    return _return_to_queue(request, workspace)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def resolve_report_view(request, workspace_id, report_id):
    workspace = _get_workspace(request, workspace_id)
    report = get_object_or_404(UGCReport, id=report_id, workspace=workspace)
    status = request.POST.get("status", "").strip()
    note = request.POST.get("note", "").strip()

    try:
        resolve_report(report=report, status=status, actor=request.user, note=note, request=request)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Report updated.")

    return _return_to_queue(request, workspace)
