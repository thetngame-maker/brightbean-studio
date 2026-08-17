"""Saved discovery searches for future external UGC providers."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
CADENCES = {
    "manual": "Manual only",
    "hourly": "Hourly",
    "daily": "Daily",
    "weekly": "Weekly",
}
CADENCE_DELTAS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


def _safe_int(value, default=0, minimum=0, maximum=100000):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _clean_searches(value):
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        cadence = str(item.get("cadence") or "daily").strip().lower()
        if cadence not in CADENCES:
            cadence = "daily"
        cleaned.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "name": str(item.get("name") or "").strip()[:100],
                "platform": str(item.get("platform") or "instagram").strip().lower(),
                "search_type": str(item.get("search_type") or "hashtag").strip().lower(),
                "query": str(item.get("query") or "").strip()[:255],
                "result_limit": _safe_int(item.get("result_limit"), default=25, minimum=1, maximum=100),
                "enabled": bool(item.get("enabled", True)),
                "cadence": cadence,
                "last_run_at": str(item.get("last_run_at") or "").strip()[:100],
                "last_run_status": str(item.get("last_run_status") or "").strip().lower()[:30],
                "last_created_count": _safe_int(item.get("last_created_count")),
                "last_duplicate_count": _safe_int(item.get("last_duplicate_count")),
                "last_invalid_count": _safe_int(item.get("last_invalid_count")),
                "last_received_count": _safe_int(item.get("last_received_count")),
            }
        )
    return cleaned


def _schedule_state(item, now=None):
    """Return display/runtime schedule state without persisting derived values."""
    now = now or timezone.now()
    cadence = item.get("cadence", "daily")
    state = {
        "cadence_label": CADENCES.get(cadence, "Daily"),
        "due_now": False,
        "next_run_at": None,
    }
    if not item.get("enabled") or cadence == "manual":
        return state

    last_run = parse_datetime(item.get("last_run_at") or "")
    if last_run is None:
        state["due_now"] = True
        return state
    if timezone.is_naive(last_run):
        last_run = timezone.make_aware(last_run, timezone.get_current_timezone())

    next_run = last_run + CADENCE_DELTAS[cadence]
    state["next_run_at"] = next_run
    state["due_now"] = next_run <= now
    return state


def get_saved_search(workspace, search_id):
    target = str(search_id or "")
    return next((item for item in _clean_searches(workspace.discovery_searches) if item["id"] == target), None)


def record_search_run(workspace, search_id, *, status, received=0, created=0, duplicates=0, invalid=0, run_at=""):
    searches = _clean_searches(workspace.discovery_searches)
    target = str(search_id or "")
    found = False
    for item in searches:
        if item["id"] != target:
            continue
        item["last_run_at"] = str(run_at or "")[:100]
        item["last_run_status"] = str(status or "")[:30]
        item["last_received_count"] = _safe_int(received)
        item["last_created_count"] = _safe_int(created)
        item["last_duplicate_count"] = _safe_int(duplicates)
        item["last_invalid_count"] = _safe_int(invalid)
        found = True
        break
    if found:
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])
    return found


@login_required
@require_permission("manage_workspace_settings")
def discovery_searches(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)
    now = timezone.now()
    for item in searches:
        item.update(_schedule_state(item, now=now))
    # Active scheduled searches that are currently due are the most actionable.
    searches.sort(
        key=lambda item: (
            0 if item.get("due_now") else 1,
            0 if item.get("enabled") else 1,
            item.get("name", "").lower(),
        )
    )
    return render(
        request,
        "ugc/discovery_searches.html",
        {
            "workspace": workspace,
            "searches": searches,
            "search_types": SEARCH_TYPES.items(),
            "platforms": PLATFORMS.items(),
            "cadences": CADENCES.items(),
            "enabled_count": sum(1 for item in searches if item["enabled"]),
            "due_count": sum(1 for item in searches if item.get("due_now")),
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
    cadence = request.POST.get("cadence", "daily").strip().lower()
    result_limit = _safe_int(request.POST.get("result_limit", "25"), default=25, minimum=1, maximum=100)

    if platform not in PLATFORMS:
        messages.error(request, "Choose a valid platform.")
    elif search_type not in SEARCH_TYPES:
        messages.error(request, "Choose a valid discovery type.")
    elif cadence not in CADENCES:
        messages.error(request, "Choose a valid discovery cadence.")
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
                    "cadence": cadence,
                    "last_run_at": "",
                    "last_run_status": "",
                    "last_created_count": 0,
                    "last_duplicate_count": 0,
                    "last_invalid_count": 0,
                    "last_received_count": 0,
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
            if item["id"] != str(search_id):
                continue
            if action == "set_cadence":
                cadence = request.POST.get("cadence", "daily").strip().lower()
                if cadence not in CADENCES:
                    messages.error(request, "Choose a valid discovery cadence.")
                    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
                item["cadence"] = cadence
                found = True
                success = f"Discovery cadence changed to {CADENCES[cadence]}."
            else:
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
