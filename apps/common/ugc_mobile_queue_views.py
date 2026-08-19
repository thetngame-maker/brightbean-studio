"""Lightweight phone moderation queue.

Desktop keeps using the original moderation view/template. Phones render a small,
server-paginated queue with minimal HTML and no enhancement bundle so Safari
never has to hydrate the desktop moderation dashboard.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render

from apps.members.decorators import require_permission

from . import ugc_views
from .models import UGCReport, UGCSubmission
from .ugc_permissions import get_permission
from .ugc_provenance import get_provenance
from .ugc_relevance import score_relevance
from .ugc_views import (
    VALID_TABS,
    _discovered_q,
    _get_workspace,
    _pending_submission_q,
    _queue_counts,
)


MOBILE_PAGE_SIZE = 12
VALID_RELEVANCE = {"relevant", "all", "strong", "possible", "low"}
VALID_MEDIA = {"all", "reels", "photos"}
VALID_SORT = {"newest", "engaged", "liked", "viewed"}
VALID_PERMISSION = {"all", "not_contacted", "requested", "granted", "declined"}


def _is_mobile_request(request):
    ua = (request.META.get("HTTP_USER_AGENT") or "").lower()
    return any(token in ua for token in ("iphone", "ipod", "android", "mobile"))


def _positive_page(raw):
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


def _metric(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0
    return 0


def _decorate_submission(submission):
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    provenance = get_provenance(metadata)
    discovery = metadata.get("discovery_import") if isinstance(metadata.get("discovery_import"), dict) else {}

    relevance = score_relevance(
        {
            "caption": submission.body,
            "location_name": discovery.get("location_name"),
            "source_title": submission.title,
        },
        query=provenance.get("discovery_query", ""),
        target_label=submission.target_label,
    )
    submission.mobile_relevance_status = relevance["relevance_status"]
    submission.mobile_relevance_reason = relevance["relevance_reason"]
    submission.mobile_discovery_method = discovery.get("discovery_method") or ""
    submission.mobile_like_count = discovery.get("like_count")
    submission.mobile_comment_count = discovery.get("comment_count")
    submission.mobile_view_count = discovery.get("view_count")
    submission.mobile_thumbnail_url = discovery.get("thumbnail_url") or ""
    submission.mobile_source_url = provenance.get("source_url") or ""
    submission.mobile_permission_status = get_permission(metadata).get("status") or "not_contacted"

    stored_media_type = str(discovery.get("media_type") or "").strip().lower()
    if submission.media_asset and submission.media_asset.is_video:
        submission.mobile_media_type = "video"
    elif submission.media_asset and submission.media_asset.is_image:
        submission.mobile_media_type = "image"
    elif stored_media_type in {"video", "image"}:
        submission.mobile_media_type = stored_media_type
    else:
        submission.mobile_media_type = ""

    likes = _metric(submission.mobile_like_count)
    comments = _metric(submission.mobile_comment_count)
    views = _metric(submission.mobile_view_count)
    # Comments represent stronger intent than a like, while views are useful but
    # much higher-volume. This mirrors the mobile review goal: surface posts that
    # are both seen and actively engaged with without letting raw views dominate.
    submission.mobile_engagement_score = likes + (comments * 10) + (views / 100)
    return submission


def _matches_relevance(submission, relevance_filter):
    status = getattr(submission, "mobile_relevance_status", "possible")
    if relevance_filter == "all":
        return True
    if relevance_filter == "relevant":
        return status != "low"
    return status == relevance_filter


def _matches_media(submission, media_filter):
    if media_filter == "all":
        return True
    media_type = getattr(submission, "mobile_media_type", "")
    if media_filter == "reels":
        return media_type == "video"
    if media_filter == "photos":
        return media_type == "image"
    return True


def _matches_permission(submission, permission_filter):
    if permission_filter == "all":
        return True
    return getattr(submission, "mobile_permission_status", "not_contacted") == permission_filter


def _sort_mobile(submissions, sort_mode):
    if sort_mode == "liked":
        return sorted(
            submissions,
            key=lambda item: (_metric(getattr(item, "mobile_like_count", 0)), item.submitted_at),
            reverse=True,
        )
    if sort_mode == "viewed":
        return sorted(
            submissions,
            key=lambda item: (_metric(getattr(item, "mobile_view_count", 0)), item.submitted_at),
            reverse=True,
        )
    if sort_mode == "engaged":
        return sorted(
            submissions,
            key=lambda item: (getattr(item, "mobile_engagement_score", 0), item.submitted_at),
            reverse=True,
        )
    return sorted(submissions, key=lambda item: item.submitted_at, reverse=True)


@login_required
@require_permission("manage_workspace_settings")
def moderation_queue(request, workspace_id):
    if not _is_mobile_request(request):
        return ugc_views.moderation_queue(request, workspace_id)

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
        qs = qs.filter(
            reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]
        ).distinct()
    elif tab == "removed":
        qs = qs.filter(status=UGCSubmission.Status.REMOVED)

    kind = request.GET.get("kind", "").strip()
    if kind in dict(UGCSubmission.Kind.choices):
        qs = qs.filter(kind=kind)
    else:
        kind = ""

    search_query = (request.GET.get("q") or "").strip()[:120]
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(contributor_handle__icontains=search_query)
            | Q(contributor_name__icontains=search_query)
            | Q(target_label__icontains=search_query)
        )

    relevance_filter = (request.GET.get("relevance") or "relevant").strip().lower()
    if relevance_filter not in VALID_RELEVANCE:
        relevance_filter = "relevant"

    media_filter = (request.GET.get("media") or "all").strip().lower()
    if media_filter not in VALID_MEDIA:
        media_filter = "all"

    sort_mode = (request.GET.get("sort") or "newest").strip().lower()
    if sort_mode not in VALID_SORT:
        sort_mode = "newest"

    permission_filter = (request.GET.get("permission") or "all").strip().lower()
    if permission_filter not in VALID_PERMISSION:
        permission_filter = "all"

    # Mobile queues are intentionally small (currently hundreds, not millions),
    # so score/filter/sort before slicing. This keeps the iPhone path entirely
    # server-rendered and avoids the intelligence/hydration bundle that caused
    # Safari stalls.
    decorated = [_decorate_submission(submission) for submission in qs[:500]]
    if tab == "discovered":
        decorated = [submission for submission in decorated if _matches_relevance(submission, relevance_filter)]
        decorated = [submission for submission in decorated if _matches_permission(submission, permission_filter)]
    decorated = [submission for submission in decorated if _matches_media(submission, media_filter)]
    decorated = _sort_mobile(decorated, sort_mode)

    total_items = len(decorated)
    total_pages = max(1, (total_items + MOBILE_PAGE_SIZE - 1) // MOBILE_PAGE_SIZE)
    page = min(_positive_page(request.GET.get("page")), total_pages)
    start = (page - 1) * MOBILE_PAGE_SIZE
    submissions = decorated[start : start + MOBILE_PAGE_SIZE]

    context = {
        "workspace": workspace,
        "submissions": submissions,
        "active_tab": tab,
        "active_kind": kind,
        "kind_choices": UGCSubmission.Kind.choices,
        "queue_counts": _queue_counts(workspace),
        "ugc_mobile_page": page,
        "ugc_mobile_total_items": total_items,
        "ugc_mobile_total_pages": total_pages,
        "ugc_mobile_prev_page": page - 1 if page > 1 else None,
        "ugc_mobile_next_page": page + 1 if page < total_pages else None,
        "ugc_mobile_search": search_query,
        "ugc_mobile_relevance": relevance_filter,
        "ugc_mobile_media": media_filter,
        "ugc_mobile_sort": sort_mode,
        "ugc_mobile_permission": permission_filter,
    }
    response = render(request, "ugc/moderation_queue_mobile.html", context)
    response["X-UGC-Mobile-Lite"] = "1"
    response["X-UGC-Mobile-Page"] = str(page)
    return response
