"""Saved discovery searches and schedule state for external UGC providers."""

from __future__ import annotations

import uuid
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .ugc_discovery_providers import live_provider_ready
from .ugc_discovery_views import TARGET_CHOICES
from .ugc_target_catalog import find_catalog_target
from .ugc_views import _get_workspace

SEARCH_TYPES = {"hashtag": "Hashtag", "location": "Place / location", "account": "Account", "keyword": "Keyword"}
PLATFORMS = {"instagram": "Instagram", "tiktok": "TikTok", "facebook": "Facebook"}
CADENCES = {"manual": "Manual only", "hourly": "Hourly", "daily": "Daily", "weekly": "Weekly"}
CADENCE_DELTAS = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7)}
RUNNING_STALE_AFTER = timedelta(minutes=30)


def _safe_int(value, default=0, minimum=0, maximum=100000):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _posted_target(workspace, values, *, required=False):
    """Resolve catalog selections canonically while retaining a manual bootstrap path."""
    target_key = _text(values.get("target_key"), 500)
    if target_key:
        if "::" not in target_key:
            return {}, "Choose a known TN Game target."
        target_type, target_id = target_key.split("::", 1)
        candidate = find_catalog_target(workspace, target_type, target_id)
        if not candidate:
            return {}, "Choose a known TN Game target."
        return {
            "target_type": candidate["target_type"],
            "target_id": candidate["target_id"],
            "target_label": candidate["target_label"],
            "target_url": candidate.get("target_url") or "",
        }, ""

    target_type = _text(values.get("target_type"), 100)
    target_id = _text(values.get("target_id"), 255)
    if not target_type and not target_id:
        return ({}, "Choose a TN Game target.") if required else ({}, "")
    if target_type not in dict(TARGET_CHOICES):
        return {}, "Choose a valid TN Game target type."
    if not target_id:
        return {}, "Enter the TN Game target ID or slug."
    return {
        "target_type": target_type,
        "target_id": target_id,
        "target_label": _text(values.get("target_label"), 255),
        "target_url": _text(values.get("target_url"), 2000),
    }, ""


def _safe_local_path(request, value):
    value = (value or "").strip()
    return bool(
        value.startswith("/")
        and not value.startswith("//")
        and url_has_allowed_host_and_scheme(
            value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
    )


def _effective_result_limit(platform, search_type, result_limit):
    """Keyword discovery is most useful when we keep the full first 100 rows."""
    limit = _safe_int(result_limit, 25, 1, 100)
    if str(platform or "").lower() == "instagram" and str(search_type or "").lower() == "keyword":
        return 100
    return limit


def _instagram_search_url(item):
    if str(item.get("platform") or "").lower() != "instagram":
        return ""
    search_type = str(item.get("search_type") or "").lower()
    query = str(item.get("query") or "").strip()
    if search_type == "location":
        return str(item.get("resolved_location_url") or "").strip()
    if search_type == "hashtag":
        tag = query.lstrip("#").strip()
        return f"https://www.instagram.com/explore/tags/{quote(tag, safe='')}/" if tag else ""
    if search_type == "account":
        handle = query.lstrip("@").strip()
        return f"https://www.instagram.com/{quote(handle, safe='._')}/" if handle else ""
    if search_type == "keyword":
        return f"https://www.instagram.com/explore/search/keyword/?q={quote(query, safe='')}" if query else ""
    return ""


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
        platform = _text(item.get("platform") or "instagram", 30).lower()
        search_type = _text(item.get("search_type") or "hashtag", 30).lower()
        cleaned.append(
            {
                "id": str(item.get("id") or uuid.uuid4()),
                "name": _text(item.get("name"), 100),
                "platform": platform,
                "search_type": search_type,
                "query": _text(item.get("query"), 255),
                "result_limit": _effective_result_limit(platform, search_type, item.get("result_limit")),
                "enabled": bool(item.get("enabled", True)),
                "cadence": cadence,
                "target_type": _text(item.get("target_type"), 100),
                "target_id": _text(item.get("target_id"), 255),
                "target_label": _text(item.get("target_label"), 255),
                "target_url": _text(item.get("target_url"), 2000),
                "resolved_location_id": _text(item.get("resolved_location_id"), 255),
                "resolved_location_name": _text(item.get("resolved_location_name"), 255),
                "resolved_location_url": _text(item.get("resolved_location_url"), 2000),
                "resolved_location_slug": _text(item.get("resolved_location_slug"), 255),
                "resolved_location_city": _text(item.get("resolved_location_city"), 255),
                "resolved_location_address": _text(item.get("resolved_location_address"), 500),
                "resolved_location_lat": item.get("resolved_location_lat"),
                "resolved_location_lng": item.get("resolved_location_lng"),
                "last_run_at": _text(item.get("last_run_at"), 100),
                "last_run_status": _text(item.get("last_run_status"), 30).lower(),
                "last_started_at": _text(item.get("last_started_at"), 100),
                "last_run_error": _text(item.get("last_run_error"), 500),
                "last_provider": _text(item.get("last_provider"), 50),
                "last_created_count": _safe_int(item.get("last_created_count")),
                "last_duplicate_count": _safe_int(item.get("last_duplicate_count")),
                "last_invalid_count": _safe_int(item.get("last_invalid_count")),
                "last_received_count": _safe_int(item.get("last_received_count")),
                "last_scanned_count": _safe_int(item.get("last_scanned_count")),
                "last_fill_target": _safe_int(item.get("last_fill_target")),
                "last_fill_selected_new": _safe_int(item.get("last_fill_selected_new")),
            }
        )
    return cleaned


def _parse_aware(value):
    parsed = parse_datetime(value or "")
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _schedule_state(item, now=None):
    now = now or timezone.now()
    cadence = item.get("cadence", "daily")
    target_ready = bool(item.get("target_type") and item.get("target_id"))
    location_ready = item.get("search_type") != "location" or bool(item.get("resolved_location_url"))
    started = _parse_aware(item.get("last_started_at"))
    running = item.get("last_run_status") == "running" and started and started > now - RUNNING_STALE_AFTER
    state = {
        "cadence_label": CADENCES.get(cadence, "Daily"),
        "due_now": False,
        "next_run_at": None,
        "target_ready": target_ready,
        "location_ready": location_ready,
        "running": bool(running),
    }
    if not item.get("enabled") or cadence == "manual" or running or not location_ready:
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
    return next((i for i in _clean_searches(workspace.discovery_searches) if i["id"] == target), None)


def save_location_match(workspace, search_id, candidate):
    searches = _clean_searches(workspace.discovery_searches)
    target = str(search_id)
    found = False
    for item in searches:
        if item["id"] != target:
            continue
        item["resolved_location_id"] = _text(candidate.get("id"), 255)
        item["resolved_location_name"] = _text(candidate.get("name"), 255)
        item["resolved_location_url"] = _text(candidate.get("url"), 2000)
        item["resolved_location_slug"] = _text(candidate.get("slug"), 255)
        item["resolved_location_city"] = _text(candidate.get("city"), 255)
        item["resolved_location_address"] = _text(candidate.get("address"), 500)
        item["resolved_location_lat"] = candidate.get("lat")
        item["resolved_location_lng"] = candidate.get("lng")
        item["last_run_error"] = ""
        found = True
        break
    if found:
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])
    return found


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
        item["instagram_search_url"] = _instagram_search_url(item)
    searches.sort(
        key=lambda i: (
            0 if i.get("running") else 1,
            0 if i.get("due_now") else 1,
            0 if i.get("enabled") else 1,
            i.get("name", "").lower(),
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
            "enabled_count": sum(1 for i in searches if i["enabled"]),
            "due_count": sum(1 for i in searches if i.get("due_now")),
            "needs_target_count": sum(1 for i in searches if i["enabled"] and not i.get("target_ready")),
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
    result_limit = _effective_result_limit(platform, search_type, request.POST.get("result_limit", "25"))
    target, target_error = _posted_target(workspace, request.POST)
    if platform not in PLATFORMS:
        messages.error(request, "Choose a valid platform.")
    elif search_type not in SEARCH_TYPES:
        messages.error(request, "Choose a valid discovery type.")
    elif cadence not in CADENCES:
        messages.error(request, "Choose a valid discovery cadence.")
    elif target_error:
        messages.error(request, target_error)
    elif not query:
        messages.error(request, "Enter a discovery query.")
    else:
        normalized = query.lower().lstrip("#@").strip()
        duplicate = any(
            i["platform"] == platform
            and i["search_type"] == search_type
            and i["query"].lower().lstrip("#@").strip() == normalized
            for i in searches
        )
        if duplicate:
            messages.warning(request, "That discovery search already exists.")
        else:
            new_search_id = str(uuid.uuid4())
            searches.insert(
                0,
                {
                    "id": new_search_id,
                    "name": name[:100] or query[:100],
                    "platform": platform,
                    "search_type": search_type,
                    "query": query[:255],
                    "result_limit": result_limit,
                    "enabled": True,
                    "cadence": cadence,
                    "target_type": target.get("target_type", ""),
                    "target_id": target.get("target_id", ""),
                    "target_label": target.get("target_label", ""),
                    "target_url": target.get("target_url", ""),
                    "resolved_location_id": "",
                    "resolved_location_name": "",
                    "resolved_location_url": "",
                    "resolved_location_slug": "",
                    "resolved_location_city": "",
                    "resolved_location_address": "",
                    "resolved_location_lat": None,
                    "resolved_location_lng": None,
                    "last_run_at": "",
                    "last_run_status": "",
                    "last_started_at": "",
                    "last_run_error": "",
                    "last_provider": "",
                    "last_created_count": 0,
                    "last_duplicate_count": 0,
                    "last_invalid_count": 0,
                    "last_received_count": 0,
                    "last_scanned_count": 0,
                    "last_fill_target": 0,
                    "last_fill_selected_new": 0,
                },
            )
            workspace.discovery_searches = searches[:100]
            workspace.save(update_fields=["discovery_searches", "updated_at"])
            if target:
                messages.success(request, "Discovery search saved and ready for background runs.")
            else:
                messages.success(request, "Discovery search saved. Choose its default TN Game target next.")
                catalog_url = reverse("ugc:target_catalog", kwargs={"workspace_id": workspace.id})
                return_to = reverse("ugc:discovery_searches", kwargs={"workspace_id": workspace.id})
                return redirect(
                    f"{catalog_url}?{urlencode({'search_id': new_search_id, 'back_to': return_to, 'return_to': return_to})}"
                )
    return redirect("ugc:discovery_searches", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_discovery_search(request, workspace_id, search_id):
    workspace = _get_workspace(request, workspace_id)
    searches = _clean_searches(workspace.discovery_searches)
    action = request.POST.get("action", "toggle").strip().lower()
    found = False
    target_change = None
    if action == "delete":
        searches2 = [i for i in searches if i["id"] != str(search_id)]
        found = len(searches2) != len(searches)
        searches = searches2
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
                target, target_error = _posted_target(workspace, request.POST, required=True)
                if target_error:
                    messages.error(request, target_error)
                    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
                previous_target = {
                    "target_type": item.get("target_type", ""),
                    "target_id": item.get("target_id", ""),
                    "target_label": item.get("target_label", ""),
                    "target_url": item.get("target_url", ""),
                }
                item.update(target)
                target_change = {
                    "label": item.get("name") or item.get("query") or "Discovery search",
                    "from": previous_target,
                    "to": target,
                    "selection_method": "catalog" if request.POST.get("target_key") else "manual",
                }
                found = True
                success = f"Default target changed to {target.get('target_label') or target['target_id']}. This search is ready for background runs."
            else:
                item["enabled"] = not item["enabled"]
                found = True
                success = "Discovery search enabled." if item["enabled"] else "Discovery search paused."
            break
    if found:
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])
        if target_change:
            record_audit_event(
                workspace=workspace,
                actor=request.user,
                action="ugc.discovery_search_target_changed",
                target_type="ugc.discovery_search",
                target_id=str(search_id),
                target_label=target_change["label"],
                metadata={
                    "from": target_change["from"],
                    "to": target_change["to"],
                    "selection_method": target_change["selection_method"],
                },
                request=request,
            )
        messages.success(request, success)
    else:
        messages.error(request, "Discovery search not found.")
    return_to = request.POST.get("return_to", "")
    if _safe_local_path(request, return_to):
        return redirect(return_to)
    return redirect("ugc:discovery_searches", workspace_id=workspace.id)
