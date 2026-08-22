"""Dispatch focused mobile Community review by queue.

Discovered keeps the existing outreach-focused review. Pending gets a dedicated
approval-focused review, and Approved gets a deliberate ready-to-use review for
creating composer drafts without changing the working Discovered implementation.
"""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.members.decorators import require_permission

from . import ugc_mobile_queue_views
from .ugc_views import _get_workspace, _queue_counts


def _review_url(workspace, submission, params, return_to):
    url = reverse("ugc:mobile_review", kwargs={"workspace_id": workspace.id, "submission_id": submission.id})
    query = ugc_mobile_queue_views._queue_query(params)
    encoded_return = urlencode({"x": return_to})[2:]
    return f"{url}?{query}&return_to={encoded_return}"


def _focused_context(request, workspace, submission_id, tab):
    decorated, filters, _workflow_counts = ugc_mobile_queue_views._filtered_queue(request, workspace, tab)
    index = next((i for i, item in enumerate(decorated) if item.id == submission_id), None)
    if index is None:
        raise Http404(f"Community item is not in the {tab.title()} review queue.")

    submission = decorated[index]
    params = {"tab": tab, **filters}
    queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    default_return = f"{queue_url}?{ugc_mobile_queue_views._queue_query(params)}"
    return_to = request.GET.get("return_to") or default_return
    if not return_to.startswith("/"):
        return_to = default_return

    prev_item = decorated[index - 1] if index > 0 else None
    next_item = decorated[index + 1] if index + 1 < len(decorated) else None
    action_return_to = _review_url(workspace, next_item, params, return_to) if next_item else return_to

    return {
        "workspace": workspace,
        "submission": submission,
        "active_tab": tab,
        "queue_counts": _queue_counts(workspace),
        "review_index": index + 1,
        "review_total": len(decorated),
        "review_prev": prev_item,
        "review_next": next_item,
        "review_query": ugc_mobile_queue_views._queue_query(params),
        "review_return_to": return_to,
        "review_action_return_to": action_return_to,
    }


@login_required
@require_permission("manage_workspace_settings")
def mobile_review(request, workspace_id, submission_id):
    """Route each queue to the focused mobile workflow that matches its next action."""
    tab = (request.GET.get("tab") or "discovered").strip().lower()
    if tab not in {"pending", "approved"}:
        return ugc_mobile_queue_views.mobile_review(request, workspace_id, submission_id)

    workspace = _get_workspace(request, workspace_id)
    context = _focused_context(request, workspace, submission_id, tab)

    if tab == "approved":
        response = render(request, "ugc/moderation_approved_review_mobile.html", context)
        response["X-UGC-Mobile-Approved-Review"] = "1"
        return response

    response = render(request, "ugc/moderation_pending_review_mobile.html", context)
    response["X-UGC-Mobile-Pending-Review"] = "1"
    return response
