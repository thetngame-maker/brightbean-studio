"""Saved discovery searches for future external UGC providers."""

from __future__ import annotations

import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .ugc_views import _get_workspace

SEARCH_TYPES = {
    "hashtag": "Hashtag",
    "location": "Place / location",
    "account": "Account",
    "keyword": "Keyword",
}
PLATFORMS = {
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "facebook": "Facebook",
}


def _clean_searches(value):
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "name": str(item.get("name") or "").strip()[:100],
                "platform": str(item.get("platform") or "instagram").strip().lower(),
                "search_type": str(item.get("search_type") or "hashtag").strip().lower(),
                "query": str(item.get("query") or "").strip()[:255],
                "result_limit": max(1, min(100, int(item.get("result_limit") or 25))),
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return cleaned


@login_required
@require_permission("manage_workspace_settings")
def discovery_searches(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)
    return render(
        request,
        "ugc/discovery_searches.html",
        {
            "workspace": workspace,
            "searches": searches,
            "search_types": SEARCH_TYPES.items(),
            "platforms": PLATFORMS.items(),
            "enabled_count": sum(1 for item in searches if item["enabled"]),
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def save_discovery_search(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)

    platform = request.POST.get("platform", "instagram").strip().lower()
    search_type = request.POST.get("search_type", "hashtag").strip().lower()
    query = request.POST.get("query", "").strip()
    name = request.POST.get("name", "").strip()
    try:
        result_limit = int(request.POST.get("result_limit", "25"))
    except (TypeError, ValueError):
        result_limit = 25
    result_limit = max(1, min(100, result_limit))

    if platform not in PLATFORMS:
        messages.error(request, "Choose a valid platform.")
    elif search_type not in SEARCH_TYPES:
        messages.error(request, "Choose a valid discovery type.")
    elif not query:
        messages.error(request, "Enter a discovery query.")
    else:
        normalized = query.lower().lstrip("#@").strip()
        duplicate = any(
            item["platform"] == platform
            and item["search_type"] == search_type
            and item["query"].lower().lstrip("#@").strip() == normalized
            for item in searches
        )
        if duplicate:
            messages.warning(request, "That discovery search already exists.")
        else:
            searches.insert(
                0,
                {
                    "id": str(uuid.uuid4()),
                    "name": name[:100] or query[:100],
                    "platform": platform,
                    "search_type": search_type,
                    "query": query[:255],
                    "result_limit": result_limit,
                    "enabled": True,
                },
            )
            workspace.discovery_searches = searches[:100]
            workspace.save(update_fields=["discovery_searches", "updated_at"])
            messages.success(request, "Discovery search saved.")

    return redirect("ugc:discovery_searches", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_discovery_search(request, workspace_id, search_id):
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)
    action = request.POST.get("action", "toggle").strip().lower()
    found = False

    if action == "delete":
        new_searches = [item for item in searches if item["id"] != str(search_id)]
        found = len(new_searches) != len(searches)
        searches = new_searches
        success = "Discovery search deleted."
    else:
        for item in searches:
            if item["id"] == str(search_id):
                item["enabled"] = not item["enabled"]
                found = True
                success = "Discovery search enabled." if item["enabled"] else "Discovery search paused."
                break

    if found:
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])
        messages.success(request, success)
    else:
        messages.error(request, "Discovery search not found.")

    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
