"""Dispatch the Community queue to purpose-built lightweight mobile views."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.members.decorators import require_permission

from . import ugc_mobile_queue_views
from .ugc_mobile_quality import decorate_approved_quality
from .ugc_views import _get_workspace, _queue_counts


@login_required
@require_permission("manage_workspace_settings")
def moderation_queue(request, workspace_id):
    """Give Approved its own Ready/Needs-check/Drafted mobile worklist; delegate everything else."""
    tab = (request.GET.get("tab") or "pending").strip().lower()
    if tab != "approved" or not ugc_mobile_queue_views._is_mobile_request(request):
        return ugc_mobile_queue_views.moderation_queue(request, workspace_id)

    workspace = _get_workspace(request, workspace_id)
    decorated, filters, _workflow_counts = ugc_mobile_queue_views._filtered_queue(request, workspace, "approved")
    decorated = [decorate_approved_quality(item) for item in decorated]

    def is_drafted(item):
        return bool((item.metadata or {}).get("studio_post_ids"))

    ready_count = sum(1 for item in decorated if not is_drafted(item) and not item.mobile_needs_quality_check)
    check_count = sum(1 for item in decorated if not is_drafted(item) and item.mobile_needs_quality_check)
    drafted_count = sum(1 for item in decorated if is_drafted(item))
    draft_state = (request.GET.get("draft_state") or "ready").strip().lower()
    if draft_state not in {"ready", "check", "drafted", "all"}:
        draft_state = "ready"

    if draft_state == "ready":
        decorated = [item for item in decorated if not is_drafted(item) and not item.mobile_needs_quality_check]
    elif draft_state == "check":
        decorated = [item for item in decorated if not is_drafted(item) and item.mobile_needs_quality_check]
    elif draft_state == "drafted":
        decorated = [item for item in decorated if is_drafted(item)]

    total_items = len(decorated)
    total_pages = max(1, (total_items + ugc_mobile_queue_views.MOBILE_PAGE_SIZE - 1) // ugc_mobile_queue_views.MOBILE_PAGE_SIZE)
    page = min(ugc_mobile_queue_views._positive_page(request.GET.get("page")), total_pages)
    start = (page - 1) * ugc_mobile_queue_views.MOBILE_PAGE_SIZE
    submissions = decorated[start : start + ugc_mobile_queue_views.MOBILE_PAGE_SIZE]

    context = {
        "workspace": workspace,
        "submissions": submissions,
        "active_tab": "approved",
        "queue_counts": _queue_counts(workspace),
        "approved_ready_count": ready_count,
        "approved_check_count": check_count,
        "approved_drafted_count": drafted_count,
        "approved_draft_state": draft_state,
        "ugc_mobile_page": page,
        "ugc_mobile_total_items": total_items,
        "ugc_mobile_total_pages": total_pages,
        "ugc_mobile_prev_page": page - 1 if page > 1 else None,
        "ugc_mobile_next_page": page + 1 if page < total_pages else None,
        "ugc_mobile_relevance": filters["relevance"],
        "ugc_mobile_media": filters["media"],
        "ugc_mobile_sort": filters["sort"],
        "ugc_mobile_permission": filters["permission"],
        "ugc_mobile_search": filters["search"],
    }
    response = render(request, "ugc/moderation_approved_queue_mobile.html", context)
    response["X-UGC-Mobile-Approved-Queue"] = "1"
    return response
