"""Lightweight server-rendered coverage intelligence for canonical TN Game targets."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.urls import reverse

from apps.members.decorators import require_permission

from .ugc_coverage import COVERAGE_LABELS, build_coverage_map
from .ugc_views import _get_workspace

COVERAGE_PAGE_SIZE = 12
VALID_SORTS = {"priority", "coverage", "engaged", "newest", "name"}


@login_required
@require_permission("manage_workspace_settings")
def coverage_map(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    coverage = build_coverage_map(workspace)
    queue = str(request.GET.get("view") or "all").strip().lower()
    if queue not in {"all", *COVERAGE_LABELS}:
        queue = "all"
    query = str(request.GET.get("q") or "").strip()[:120]
    target_type = str(request.GET.get("type") or "").strip().lower()[:100]
    sort = str(request.GET.get("sort") or "priority").strip().lower()
    if sort not in VALID_SORTS:
        sort = "priority"

    all_targets = coverage["targets"]
    target_types = sorted({item["target_type"] for item in all_targets})
    targets = all_targets
    if queue != "all":
        targets = [item for item in targets if item["coverage_state"] == queue]
    if target_type:
        targets = [item for item in targets if item["target_type"] == target_type]
    if query:
        lowered = query.lower()
        targets = [
            item
            for item in targets
            if lowered in item["target_label"].lower()
            or lowered in item["target_id"].lower()
            or any(lowered in alias.lower() for alias in item.get("aliases", []))
        ]

    if sort == "coverage":
        targets.sort(key=lambda item: (item["coverage_score"], item["target_label"].lower()))
    elif sort == "engaged":
        targets.sort(key=lambda item: (-item["engagement_score"], item["target_label"].lower()))
    elif sort == "newest":
        targets.sort(
            key=lambda item: item["latest_content_at"].timestamp() if item["latest_content_at"] else 0,
            reverse=True,
        )
    elif sort == "name":
        targets.sort(key=lambda item: item["target_label"].lower())
    else:
        targets.sort(key=lambda item: (-item["priority_score"], item["target_label"].lower()))

    queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    for target in targets:
        target["discovered_url"] = f"{queue_url}?{urlencode({'tab': 'discovered', 'q': target['target_label']})}"
        target["approved_url"] = (
            f"{queue_url}?{urlencode({'tab': 'approved', 'draft_state': 'all', 'q': target['target_label']})}"
        )

    mapped_targets = [item for item in targets if item.get("map_x") is not None][:120]
    page = Paginator(targets, COVERAGE_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    return render(
        request,
        "ugc/coverage_map.html",
        {
            "workspace": workspace,
            "coverage_targets": page.object_list,
            "coverage_page": page,
            "coverage_counts": coverage["counts"],
            "coverage_mapped_targets": mapped_targets,
            "coverage_mapped_count": coverage["mapped_count"],
            "coverage_queue": queue,
            "coverage_query": query,
            "coverage_type": target_type,
            "coverage_types": target_types,
            "coverage_sort": sort,
            "coverage_labels": COVERAGE_LABELS,
            "coverage_stale_days": coverage["stale_after_days"],
        },
    )
