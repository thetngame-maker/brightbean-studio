"""Lightweight phone moderation queue.

Desktop keeps using the original moderation view/template. Phones render a small,
server-paginated queue with minimal HTML and almost no enhancement JavaScript so
Safari never has to parse or hydrate the full desktop moderation dashboard.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render

from apps.members.decorators import require_permission

from . import ugc_views
from .models import UGCReport, UGCSubmission
from .ugc_views import (
    VALID_TABS,
    _discovered_q,
    _get_workspace,
    _pending_submission_q,
    _queue_counts,
)


MOBILE_PAGE_SIZE = 12


def _is_mobile_request(request):
    ua = (request.META.get("HTTP_USER_AGENT") or "").lower()
    return any(token in ua for token in ("iphone", "ipod", "android", "mobile"))


def _positive_page(raw):
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


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

    total_items = qs.count()
    total_pages = max(1, (total_items + MOBILE_PAGE_SIZE - 1) // MOBILE_PAGE_SIZE)
    page = min(_positive_page(request.GET.get("page")), total_pages)
    start = (page - 1) * MOBILE_PAGE_SIZE
    submissions = list(qs[start : start + MOBILE_PAGE_SIZE])

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
    }
    response = render(request, "ugc/moderation_queue_mobile.html", context)
    response["X-UGC-Mobile-Lite"] = "1"
    response["X-UGC-Mobile-Page"] = str(page)
    return response
