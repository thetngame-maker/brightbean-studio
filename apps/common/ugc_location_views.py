"""Resolve saved location searches to exact Instagram place URLs."""
from urllib.parse import urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.decorators import require_permission

from .ugc_discovery_providers import DiscoveryProviderError, live_provider_ready, resolve_instagram_location_candidates
from .ugc_discovery_search_views import get_saved_search, save_location_match
from .ugc_views import _get_workspace


def _valid_instagram_location_url(value):
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return parsed.scheme == "https" and parsed.netloc.lower() in {"instagram.com", "www.instagram.com"} and parsed.path.startswith("/explore/locations/")


@login_required
@require_permission("manage_workspace_settings")
@require_http_methods(["GET"])
def location_candidates(request, workspace_id, search_id):
    workspace = _get_workspace(request, workspace_id)
    saved_search = get_saved_search(workspace, search_id)
    if not saved_search:
        messages.error(request, "Discovery search not found.")
        return redirect("ugc:discovery_searches", workspace_id=workspace.id)
    if saved_search.get("platform") != "instagram" or saved_search.get("search_type") != "location":
        messages.error(request, "Location matching is currently available for Instagram location searches.")
        return redirect("ugc:discovery_searches", workspace_id=workspace.id)
    if not live_provider_ready():
        messages.error(request, "Connect the live Apify provider before resolving Instagram locations.")
        return redirect("ugc:discovery_searches", workspace_id=workspace.id)

    candidates = []
    error = ""
    try:
        candidates = resolve_instagram_location_candidates(saved_search.get("query", ""), limit=10)
    except DiscoveryProviderError as exc:
        error = str(exc)

    return render(request, "ugc/location_candidates.html", {
        "workspace": workspace,
        "search": saved_search,
        "candidates": candidates,
        "resolution_error": error,
    })


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def choose_location_candidate(request, workspace_id, search_id):
    workspace = _get_workspace(request, workspace_id)
    saved_search = get_saved_search(workspace, search_id)
    if not saved_search or saved_search.get("search_type") != "location":
        messages.error(request, "Location discovery search not found.")
        return redirect("ugc:discovery_searches", workspace_id=workspace.id)

    url = request.POST.get("url", "").strip()
    if not _valid_instagram_location_url(url):
        messages.error(request, "Choose a valid Instagram /explore/locations/ URL.")
        return redirect("ugc:location_candidates", workspace_id=workspace.id, search_id=search_id)

    candidate = {
        "id": request.POST.get("location_id", "").strip(),
        "name": request.POST.get("name", "").strip() or saved_search.get("query", ""),
        "url": url,
        "slug": request.POST.get("slug", "").strip(),
        "city": request.POST.get("city", "").strip(),
        "address": request.POST.get("address", "").strip(),
        "lat": request.POST.get("lat", "").strip() or None,
        "lng": request.POST.get("lng", "").strip() or None,
    }
    save_location_match(workspace, search_id, candidate)
    messages.success(request, f"Instagram location matched to {candidate['name']}.")
    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
