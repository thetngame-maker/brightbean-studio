"""Workspace moderation UI for community-contributed content."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.composer.models import Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.media_library.services import create_asset
from apps.media_library.tasks import process_media_asset
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .audit import record_audit_event
from .models import UGCModerationEvent, UGCReport, UGCSubmission
from .ugc import moderate_submission, resolve_report
from .ugc_provenance import build_provenance, get_provenance, set_provenance


VALID_TABS = {"discovered", "pending", "approved", "reported", "removed"}


def _get_workspace(request, workspace_id):
    workspace = get_object_or_404(Workspace, id=workspace_id)
    if not request.user.is_authenticated:
        raise PermissionDenied("Authentication required.")
    if not WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).exists():
        raise PermissionDenied("You are not a member of this workspace.")
    return workspace


def _discovered_q():
    """Externally discovered content that has not entered the consent flow yet.

    Manual/direct submissions use discovery_source=manual. Future discovery
    providers can use their own stable key (for example ``apify``) and will
    automatically land in the Discovered queue without a schema change.
    """
    return Q(metadata__provenance__discovery_source__isnull=False) & ~Q(
        metadata__provenance__discovery_source="manual"
    )


def _pending_submission_q():
    """Normal pending submissions, excluding externally discovered content."""
    return Q(metadata__provenance__discovery_source__isnull=True) | Q(
        metadata__provenance__discovery_source="manual"
    )


def _queue_counts(workspace):
    base = UGCSubmission.objects.for_workspace(workspace.id)
    pending_base = base.filter(status=UGCSubmission.Status.PENDING)
    return {
        "discovered": pending_base.filter(_discovered_q()).count(),
        "pending": pending_base.filter(_pending_submission_q()).count(),
        "approved": base.filter(status=UGCSubmission.Status.APPROVED).count(),
        "reported": base.filter(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]).distinct().count(),
        "removed": base.filter(status=UGCSubmission.Status.REMOVED).count(),
    }


def _return_to_queue(request, workspace):
    return_to = request.POST.get("return_to") or request.META.get("HTTP_REFERER")
    if return_to:
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


def _render_manual_errors(request, workspace, errors):
    for error in errors:
        messages.error(request, error)
    return redirect("ugc:manual_submission_form", workspace_id=workspace.id)


def _ugc_attribution_line(submission):
    if submission.attribution == UGCSubmission.Attribution.ANONYMOUS:
        return ""
    if submission.attribution == UGCSubmission.Attribution.HANDLE and submission.contributor_handle:
        return f"Community content by @{submission.contributor_handle.lstrip('@')}"
    if submission.contributor_name:
        return f"Community content by {submission.contributor_name}"
    if submission.contributor_handle:
        return f"Community content by @{submission.contributor_handle.lstrip('@')}"
    return "Community content"


def _ugc_draft_caption(submission):
    parts = []
    if submission.body:
        parts.append(submission.body.strip())
    elif submission.title and submission.title != submission.target_label:
        parts.append(submission.title.strip())

    attribution = _ugc_attribution_line(submission)
    if attribution:
        parts.append(attribution)
    if submission.target_label:
        parts.append(f"📍 {submission.target_label}")
    return "\n\n".join(part for part in parts if part)


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

    if tab == "discovered":
        qs = qs.filter(status=UGCSubmission.Status.PENDING).filter(_discovered_q())
    elif tab == "pending":
        qs = qs.filter(status=UGCSubmission.Status.PENDING).filter(_pending_submission_q())
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

    Manual moderators may upload a new image or select one already in the
    workspace media library. New uploads use the same validated media service
    as the REST API, so UGC never gets a second, weaker storage path.
    """

    workspace = _get_workspace(request, workspace_id)
    kind = request.POST.get("kind", "").strip()
    attribution = request.POST.get("attribution", UGCSubmission.Attribution.NAME).strip()
    target_type = request.POST.get("target_type", "").strip()
    target_id = request.POST.get("target_id", "").strip()
    rating_raw = request.POST.get("rating", "").strip()
    media_asset_raw = request.POST.get("media_asset_id", "").strip()
    uploaded_media = request.FILES.get("media_upload")
    consent_confirmed = request.POST.get("consent_confirmed") == "on"
    consent_version = request.POST.get("consent_version", "").strip()
    contributor_handle = request.POST.get("contributor_handle", "").strip().lstrip("@")[:255]
    source_platform = request.POST.get("source_platform", "direct").strip() or "direct"
    source_url = request.POST.get("source_url", "").strip()[:2000]
    source_external_id = request.POST.get("source_external_id", "").strip()[:255]

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
    if media_asset_raw and not uploaded_media:
        media_asset = MediaAsset.objects.filter(
            id=media_asset_raw,
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).first()
        if media_asset is None:
            errors.append("That image does not belong to this workspace.")

    if kind == UGCSubmission.Kind.PHOTO and not uploaded_media and media_asset is None:
        errors.append("Photo submissions require an uploaded or selected image.")

    if errors:
        return _render_manual_errors(request, workspace, errors)

    if uploaded_media:
        try:
            media_asset = create_asset(
                organization=workspace.organization,
                workspace=workspace,
                uploaded_file=uploaded_media,
                uploaded_by=request.user,
                alt_text=request.POST.get("title", "").strip()[:255],
                title=request.POST.get("title", "").strip()[:255],
                tags=["community-content", "ugc"],
            )
        except ValidationError as exc:
            if hasattr(exc, "messages"):
                upload_errors = exc.messages
            else:
                upload_errors = [str(exc)]
            return _render_manual_errors(request, workspace, upload_errors)

        if media_asset.media_type != MediaAsset.MediaType.IMAGE:
            return _render_manual_errors(request, workspace, ["Community photo uploads must be an image."])

        process_media_asset(str(media_asset.id))

    provenance = build_provenance(
        platform=source_platform,
        source_url=source_url,
        external_id=source_external_id,
        creator_handle=contributor_handle,
        discovery_source="manual",
    )

    submission = UGCSubmission.objects.create(
        workspace=workspace,
        kind=kind,
        status=UGCSubmission.Status.PENDING,
        source=UGCSubmission.Source.UI,
        contributor_name=request.POST.get("contributor_name", "").strip()[:255],
        contributor_handle=contributor_handle,
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
        metadata=set_provenance({}, provenance),
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.submitted",
        target=submission,
        target_label=str(submission),
        metadata={
            "source": "manual",
            "source_platform": provenance["platform"],
            "source_url": provenance["source_url"],
            "source_external_id": provenance["external_id"],
            "kind": kind,
            "consent_confirmed": consent_confirmed,
            "media_asset_id": str(media_asset.id) if media_asset else "",
        },
        request=request,
    )
    messages.success(request, "Community submission added to the Pending queue.")
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


@login_required
@require_permission("create_posts")
@require_POST
def use_in_post_view(request, workspace_id, submission_id):
    """Turn an approved UGC item into a normal editable Studio draft."""

    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(
        UGCSubmission.objects.select_related("media_asset"),
        id=submission_id,
        workspace=workspace,
    )

    if submission.status != UGCSubmission.Status.APPROVED:
        messages.error(request, "Only approved community content can be used in a post.")
        return _return_to_queue(request, workspace)
    if not submission.consent_confirmed:
        messages.error(request, "Contributor consent is required before community content can be reused.")
        return _return_to_queue(request, workspace)

    source_bits = [
        f"UGC submission: {submission.id}",
        f"Target: {submission.target_type}:{submission.target_id}",
    ]
    if submission.target_label:
        source_bits.append(f"Target name: {submission.target_label}")
    if submission.target_url:
        source_bits.append(f"Target URL: {submission.target_url}")

    provenance = get_provenance(submission.metadata)
    if provenance["platform"] != "direct":
        source_bits.append(f"Original source: {provenance['platform']}")
    if provenance["source_url"]:
        source_bits.append(f"Original source URL: {provenance['source_url']}")
    if provenance["external_id"]:
        source_bits.append(f"Original source ID: {provenance['external_id']}")

    post = Post.objects.create(
        workspace=workspace,
        author=request.user,
        title=submission.title or submission.target_label or "Community content",
        caption=_ugc_draft_caption(submission),
        internal_notes="\n".join(source_bits),
    )

    if submission.media_asset_id:
        PostMedia.objects.create(
            post=post,
            media_asset=submission.media_asset,
            position=0,
            alt_text=getattr(submission.media_asset, "alt_text", "") or submission.title or submission.target_label,
        )

    metadata = dict(submission.metadata or {})
    post_ids = list(metadata.get("studio_post_ids") or [])
    post_ids.append(str(post.id))
    metadata["studio_post_ids"] = post_ids[-20:]
    submission.metadata = metadata
    submission.save(update_fields=["metadata", "updated_at"])

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.used_in_post",
        target=submission,
        target_label=str(submission),
        metadata={"post_id": str(post.id), "media_asset_id": str(submission.media_asset_id or "")},
        request=request,
    )
    messages.success(request, "Draft created from approved community content.")
    return redirect("composer:compose_edit", workspace_id=workspace.id, post_id=post.id)


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
