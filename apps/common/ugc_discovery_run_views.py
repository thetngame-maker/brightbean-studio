"""UI endpoints for queueing and monitoring saved discovery searches."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.members.decorators import require_permission

from .ugc_discovery_providers import live_provider_ready, provider_health
from .ugc_discovery_search_views import _clean_searches, get_saved_search, record_search_run
from .ugc_discovery_tasks import run_saved_discovery_search
from .ugc_remote_media import repair_workspace_discovered_media
from .ugc_views import _get_workspace


def _status_signature(searches):
    parts=[]
    for item in searches:
        parts.append(":".join([str(item.get("id") or ""),str(item.get("last_run_status") or ""),str(item.get("last_started_at") or ""),str(item.get("last_run_at") or ""),str(item.get("last_provider") or ""),str(item.get("last_created_count") or 0),str(item.get("last_duplicate_count") or 0),str(item.get("last_invalid_count") or 0)]))
    return "|".join(parts)

@login_required
@require_permission("manage_workspace_settings")
@require_GET
def discovery_run_status(request,workspace_id):
    workspace=_get_workspace(request,workspace_id); searches=_clean_searches(workspace.discovery_searches)
    return JsonResponse({"signature":_status_signature(searches),"workspace_updated_at":workspace.updated_at.isoformat(),"queued_count":sum(1 for i in searches if i.get("last_run_status")=="queued"),"running_count":sum(1 for i in searches if i.get("last_run_status")=="running")})

@login_required
@require_permission("manage_workspace_settings")
@require_POST
def queue_background_test_run(request,workspace_id,search_id):
    workspace=_get_workspace(request,workspace_id); saved_search=get_saved_search(workspace,search_id)
    if not saved_search:
        messages.error(request,"Discovery search not found.")
    elif not saved_search.get("target_type") or not saved_search.get("target_id"):
        messages.error(request,"Set a default TN Game target before running this search in the background.")
    elif saved_search.get("platform")=="instagram" and saved_search.get("search_type")=="location" and not saved_search.get("resolved_location_url"):
        return redirect("ugc:location_candidates",workspace_id=workspace.id,search_id=search_id)
    else:
        live=live_provider_ready(); provider=provider_health()["provider"] if live else "mock"; queued_at=timezone.now().isoformat()
        record_search_run(workspace,search_id,status="queued",started_at=queued_at,provider=provider)
        run_saved_discovery_search(str(workspace.id),str(search_id),not live,True)
        messages.success(request,f"Live {provider.title()} discovery queued. Waiting for the worker to claim it." if live else "Background discovery test queued. Waiting for the worker to claim it.")
    return redirect("ugc:discovery_searches",workspace_id=workspace.id)

@login_required
@require_permission("manage_workspace_settings")
@require_POST
def queue_discovered_media_repair(request,workspace_id):
    workspace=_get_workspace(request,workspace_id); repair_workspace_discovered_media(str(workspace.id))
    messages.success(request,"Missing discovery images queued for capture. Thumbnails will appear as the worker saves them to the Media Library.")
    return redirect(f"/w/{workspace.id}/community-content/?tab=discovered")
