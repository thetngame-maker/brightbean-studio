"""Background worker pipeline for saved UGC discovery searches."""

from __future__ import annotations

import logging
from datetime import timedelta

from background_task import background
from django.db import transaction
from django.utils import timezone

from apps.workspaces.models import Workspace

from .audit import record_audit_event
from .ugc_discovery_ingest import ingest_discovered_items
from .ugc_discovery_providers import DiscoveryProviderError, fetch_discovery_results, live_provider_ready
from .ugc_discovery_search_views import _clean_searches, _schedule_state
from .ugc_location_fallback import deep_location_fallback
from .ugc_remote_media import repair_workspace_discovered_media

logger = logging.getLogger(__name__)

DISCOVERY_SCAN_INTERVAL_SECONDS = 5 * 60
RUNNING_STALE_AFTER = timedelta(minutes=30)


def _find_search(searches, search_id):
    target = str(search_id or "")
    return next((item for item in searches if item.get("id") == target), None)


def _claim_search(workspace_id, search_id, *, force=False):
    """Atomically claim one saved search and return a snapshot to execute."""
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(id=workspace_id)
        searches = _clean_searches(workspace.discovery_searches)
        item = _find_search(searches, search_id)
        if not item:
            return None
        if not item.get("target_type") or not item.get("target_id"):
            return None

        now = timezone.now()
        started_raw = item.get("last_started_at") or ""
        if item.get("last_run_status") == "running" and started_raw:
            from django.utils.dateparse import parse_datetime

            started = parse_datetime(started_raw)
            if started is not None:
                if timezone.is_naive(started):
                    started = timezone.make_aware(started, timezone.get_current_timezone())
                if started > now - RUNNING_STALE_AFTER:
                    return None

        if not force:
            state = _schedule_state(item, now=now)
            if not item.get("enabled") or item.get("cadence") == "manual" or not state.get("due_now"):
                return None

        item["last_run_status"] = "running"
        item["last_started_at"] = now.isoformat()
        item["last_run_error"] = ""
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])
        return dict(item)


def _finish_search(workspace_id, search_id, *, status, provider="", summary=None, error=""):
    summary = summary or {}
    with transaction.atomic():
        workspace = Workspace.objects.select_for_update().get(id=workspace_id)
        searches = _clean_searches(workspace.discovery_searches)
        item = _find_search(searches, search_id)
        if not item:
            return
        item["last_run_status"] = str(status or "")[:30]
        item["last_run_at"] = timezone.now().isoformat()
        item["last_provider"] = str(provider or "")[:50]
        item["last_run_error"] = str(error or "")[:500]
        item["last_received_count"] = int(summary.get("total_received") or 0)
        item["last_created_count"] = int(summary.get("created_count") or 0)
        item["last_duplicate_count"] = int(summary.get("duplicate_count") or 0)
        item["last_invalid_count"] = int(summary.get("invalid_count") or 0)
        workspace.discovery_searches = searches
        workspace.save(update_fields=["discovery_searches", "updated_at"])


def _location_provider_label(provider: str, diagnostics: dict | None) -> str:
    if not diagnostics:
        return provider
    path = str(diagnostics.get("path") or "none")
    details = int(diagnostics.get("details_nested_posts") or 0)
    details_normalized = int(diagnostics.get("details_normalized_posts") or 0)
    search = int(diagnostics.get("search_nested_posts") or 0)
    search_normalized = int(diagnostics.get("search_normalized_posts") or 0)
    return f"{provider} · {path} d{details}>{details_normalized}/s{search}>{search_normalized}"[:50]


@background(schedule=0)
def run_saved_discovery_search(workspace_id, search_id, test_mode=False, force_run=False):
    """Execute one saved search on the shared Railway background worker.

    ``test_mode`` controls whether the mock provider is allowed. ``force_run``
    independently controls whether cadence should be bypassed. Keeping those
    concepts separate lets an explicit user-triggered live Apify run execute
    immediately while unattended scheduled runs still respect Hourly/Daily/
    Weekly due times.
    """
    claimed = _claim_search(workspace_id, search_id, force=bool(test_mode or force_run))
    if not claimed:
        return

    provider = "mock" if test_mode else ""
    diagnostics = None
    try:
        provider, rows = fetch_discovery_results(
            claimed,
            provider_name=provider or None,
            allow_mock=bool(test_mode),
        )

        # Location actors can expose post media in several documented output
        # shapes. Only invoke the deeper inspection when all normal Apify paths
        # returned zero usable rows, so hashtag/keyword performance is unchanged.
        if (
            not rows
            and provider == "apify"
            and str(claimed.get("search_type") or "").lower() == "location"
        ):
            rows, diagnostics = deep_location_fallback(
                claimed,
                int(claimed.get("result_limit") or 25),
            )

        workspace = Workspace.objects.get(id=workspace_id)
        summary = ingest_discovered_items(
            workspace=workspace,
            items=rows[: claimed.get("result_limit", 25)],
            discovery_source=f"{provider}_scheduled" if not test_mode else "mock_background_test",
            default_target_type=claimed["target_type"],
            default_target_id=claimed["target_id"],
            default_target_label=claimed.get("target_label", ""),
            default_target_url=claimed.get("target_url", ""),
        )
        provider_label = _location_provider_label(provider, diagnostics)
        _finish_search(workspace_id, search_id, status="success", provider=provider_label, summary=summary)
        # Also repair older provider imports from before durable media capture
        # existed. The repair task de-duplicates its own queue work.
        repair_workspace_discovered_media(str(workspace.id))
        record_audit_event(
            workspace=workspace,
            action="ugc.discovery_background_run",
            target_type="ugc.discovery_search",
            target_id=str(search_id),
            target_label=claimed.get("name") or claimed.get("query") or "Discovery search",
            source="system",
            metadata={
                "provider": provider,
                "test_mode": bool(test_mode),
                "force_run": bool(force_run),
                "query": claimed.get("query", ""),
                "created_count": summary["created_count"],
                "duplicate_count": summary["duplicate_count"],
                "invalid_count": summary["invalid_count"],
                "location_diagnostics": diagnostics or {},
            },
        )
    except DiscoveryProviderError as exc:
        _finish_search(workspace_id, search_id, status="failed", provider=provider, error=str(exc))
        logger.warning("Discovery search %s could not run: %s", search_id, exc)
    except Exception as exc:
        _finish_search(workspace_id, search_id, status="failed", provider=provider, error=str(exc))
        logger.exception("Background discovery search %s failed", search_id)


@background(schedule=0)
def run_due_discovery_searches():
    """Queue due searches and repair discovered thumbnails on active workspaces."""
    if not live_provider_ready():
        return

    now = timezone.now()
    workspaces = Workspace.objects.filter(is_archived=False).exclude(discovery_searches=[]).only("id", "discovery_searches")
    for workspace in workspaces.iterator():
        repair_workspace_discovered_media(str(workspace.id))
        for item in _clean_searches(workspace.discovery_searches):
            state = _schedule_state(item, now=now)
            if (
                item.get("enabled")
                and item.get("cadence") != "manual"
                and item.get("target_type")
                and item.get("target_id")
                and state.get("due_now")
            ):
                run_saved_discovery_search(str(workspace.id), item["id"], False, False)
