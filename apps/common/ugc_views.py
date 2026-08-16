"""Workspace moderation UI for community-contributed content."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

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

    return redirect(f"{request.POST.get('return_to') or request.META.get('HTTP_REFERER') or ''}" or "ugc:moderation_queue", workspace_id=workspace.id)


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

    return redirect(request.POST.get("return_to") or request.META.get("HTTP_REFERER") or "ugc:moderation_queue", workspace_id=workspace.id)
