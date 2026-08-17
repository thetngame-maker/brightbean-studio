"""UI endpoints for queueing and monitoring saved discovery searches."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

from apps.members.decorators import require_permission

from .ugc_discovery_providers import live_provider_ready, provider_health
from .ugc_discovery_search_views import _clean_searches, get_saved_search
from .ugc_discovery_tasks import run_saved_discovery_search
from .ugc_views import _get_workspace


def _status_signature(searches):
    """Compact non-secret fingerprint for client-side run-status polling."""
    parts = []
    for item in searches:
        parts.append(
            ":".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("last_run_status") or ""),
                    str(item.get("last_started_at") or ""),
                    str(item.get("last_run_at") or ""),
                    str(item.get("last_provider") or ""),
                    str(item.get("last_created_count") or 0),
                    str(item.get("last_duplicate_count") or 0),
                    str(item.get("last_invalid_count") or 0),
                ]
            )
        )
    return "|".join(parts)


@login_required
@require_permission("manage_workspace_settings")
@require_GET
def discovery_run_status(request, workspace_id):
    """Return a lightweight fingerprint so the Discovery page can self-refresh.

    The endpoint intentionally returns no provider credentials, queries, target
    details, or UGC payloads. Its only job is to tell the page when worker-owned
    run state has changed since the previous poll.
    """
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)
    return JsonResponse(
        {
            "signature": _status_signature(searches),
            "running_count": sum(1 for item in searches if item.get("last_run_status") == "running"),
        }
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def queue_background_test_run(request, workspace_id, search_id):
    """Queue a worker run, using live discovery when configured.

    Before a live provider is connected this remains the safe deterministic mock
    path used to prove the queue/worker/ingestion pipeline. As soon as provider
    credentials are present, the same button executes the real provider.
    """
    workspace = _get_workspace(request, workspace_id)
    saved_search = get_saved_search(workspace, search_id)
    if not saved_search:
        messages.error(request, "Discovery search not found.")
    elif not saved_search.get("target_type") or not saved_search.get("target_id"):
        messages.error(request, "Set a default TN Game target before running this search in the background.")
    else:
        live = live_provider_ready()
        run_saved_discovery_search(str(workspace.id), str(search_id), not live)
        if live:
            health = provider_health()
            messages.success(
                request,
                f"Live {health['provider'].title()} discovery queued. The worker will update these stats when it finishes.",
            )
        else:
            messages.success(
                request,
                "Background discovery test queued. The worker will run it and update these stats shortly.",
            )
    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
