"""Saved discovery searches and schedule state for external UGC providers."""

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

from .ugc_discovery_providers import live_provider_ready
from .ugc_discovery_views import TARGET_CHOICES
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
RUNNING_STALE_AFTER = timedelta(minutes=30)


def _safe_int(value, default=0, minimum=0, maximum=100000):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _clean_searches(value):
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        cadence = _text(item.get("cadence") or "daily", 20).lower()
        if cadence not in CADENCES:
            cadence = "daily"
        cleaned.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "name": _text(item.get("name"), 100),
                "platform": _text(item.get("platform") or "instagram", 30).lower(),
                "search_type": _text(item.get("search_type") or "hashtag", 30).lower(),
                "query": _text(item.get("query"), 255),
                "result_limit": _safe_int(item.get("result_limit"), default=25, minimum=1, maximum=100),
                "enabled": bool(item.get("enabled", True)),
                "cadence": cadence,
                "target_type": _text(item.get("target_type"), 100),
                "target_id": _text(item.get("target_id"), 255),
                "target_label": _text(item.get("target_label"), 255),
                "target_url": _text(item.get("target_url"), 2000),
                "last_run_at": _text(item.get("last_run_at"), 100),
                "last_run_status": _text(item.get("last_run_status"), 30).lower(),
                "last_started_at": _text(item.get("last_started_at"), 100),
                "last_run_error": _text(item.get("last_run_error"), 500),
                "last_provider": _text(item.get("last_provider"), 50),
                "last_created_count": _safe_int(item.get("last_created_count")),
                "last_duplicate_count": _safe_int(item.get("last_duplicate_count")),
                "last_invalid_count": _safe_int(item.get("last_invalid_count")),
                "last_received_count": _safe_int(item.get("last_received_count")),
            }
        )
    return cleaned


def _parse_aware(value):
    parsed = parse_datetime(value or "")
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _schedule_state(item, now=None):
    """Return derived runtime/display state without persisting it."""
    now = now or timezone.now()
    cadence = item.get("cadence", "daily")
    target_ready = bool(item.get("target_type") and item.get("target_id"))
    started = _parse_aware(item.get("last_started_at"))
    running = item.get("last_run_status") == "running" and started and started > now - RUNNING_STALE_AFTER
    state = {
        "cadence_label": CADENCES.get(cadence, "Daily"),
        "due_now": False,
        "next_run_at": None,
        "target_ready": target_ready,
        "running": bool(running),
    }
    if not item.get("enabled") or cadence == "manual" or running:
        return state

    last_run = _parse_aware(item.get("last_run_at"))
    if last_run is None:
        state["due_now"] = True
        return state

    next_run = last_run + CADENCE_DELTAS[cadence]
    state["next_run_at"] = next_run
    state["due_now"] = next_run <= now
    return state


def get_saved_search(workspace, search_id):
    target = str(search_id or "")
    return next((item for item in _clean_searches(workspace.discovery_searches) if item["id"] == target), None)


def record_search_run(
    workspace,
    search_id,
    *,
    status,
    received=0,
    created=0,
    duplicates=0,
    invalid=0,
    run_at="",
    started_at="",
    provider="",
    error="",
):
    searches = _clean_searches(workspace.discovery_searches)
    target = str(search_id or "")
    found = False
    for item in searches:
        if item["id"] != target:
            continue
        if run_at:
            item["last_run_at"] = _text(run_at, 100)
        if started_at:
            item["last_started_at"] = _text(started_at, 100)
        item["last_run_status"] = _text(status, 30).lower()
        item["last_provider"] = _text(provider, 50)
        item["last_run_error"] = _text(error, 500)
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
    searches.sort(
        key=lambda item: (
            0 if item.get("running") else 1,
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
            "target_choices": TARGET_CHOICES,
            "enabled_count": sum(1 for item in searches if item["enabled"]),
            "due_count": sum(1 for item in searches if item.get("due_now")),
            "needs_target_count": sum(1 for item in searches if item["enabled"] and not item.get("target_ready")),
            "live_provider_ready": live_provider_ready(),
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
    target_type = _text(request.POST.get("target_type"), 100)
    target_id = _text(request.POST.get("target_id"), 255)
    target_label = _text(request.POST.get("target_label"), 255)
    target_url = _text(request.POST.get("target_url"), 2000)

    if platform not in PLATFORMS:
        messages.error(request, "Choose a valid platform.")
    elif search_type not in SEARCH_TYPES:
        messages.error(request, "Choose a valid discovery type.")
    elif cadence not in CADENCES:
        messages.error(request, "Choose a valid discovery cadence.")
    elif target_type and target_type not in dict(TARGET_CHOICES):
        messages.error(request, "Choose a valid TN Game target type.")
    elif bool(target_type) != bool(target_id):
        messages.error(request, "Target type and target ID must be set together.")
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
                    "target_type": target_type,
                    "target_id": target_id,
                    "target_label": target_label,
                    "target_url": target_url,
                    "last_run_at": "",
                    "last_run_status": "",
                    "last_started_at": "",
                    "last_run_error": "",
                    "last_provider": "",
                    "last_created_count": 0,
                    "last_duplicate_count": 0,
                    "last_invalid_count": 0,
                    "last_received_count": 0,
                },
            )
            workspace.discovery_searches = searches[:100]
            workspace.save(update_fields=["discovery_searches", "updated_at"])
            if target_type and target_id:
                messages.success(request, "Discovery search saved and ready for background runs.")
            else:
                messages.success(request, "Discovery search saved. Add a default TN Game target before background runs.")

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
            elif action == "set_target":
                target_type = _text(request.POST.get("target_type"), 100)
                target_id = _text(request.POST.get("target_id"), 255)
                if target_type not in dict(TARGET_CHOICES) or not target_id:
                    messages.error(request, "Choose a target type and enter its target ID.")
                    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
                item["target_type"] = target_type
                item["target_id"] = target_id
                item["target_label"] = _text(request.POST.get("target_label"), 255)
                item["target_url"] = _text(request.POST.get("target_url"), 2000)
                found = True
                success = "Default TN Game target saved. This search is ready for background runs."
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
