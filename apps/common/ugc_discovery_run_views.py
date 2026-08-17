"""UI endpoints for queueing saved discovery searches on the worker."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .ugc_discovery_search_views import get_saved_search
from .ugc_discovery_tasks import run_saved_discovery_search
from .ugc_views import _get_workspace


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def queue_background_test_run(request, workspace_id, search_id):
    workspace = _get_workspace(request, workspace_id)
    saved_search = get_saved_search(workspace, search_id)
    if not saved_search:
        messages.error(request, "Discovery search not found.")
    elif not saved_search.get("target_type") or not saved_search.get("target_id"):
        messages.error(request, "Set a default TN Game target before running this search in the background.")
    else:
        run_saved_discovery_search(str(workspace.id), str(search_id), True)
        messages.success(
            request,
            "Background discovery test queued. The worker will run it and update these stats shortly.",
        )
    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
