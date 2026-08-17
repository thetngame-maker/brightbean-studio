"""Provider adapters for scheduled UGC discovery.

The worker talks only to this module. Real providers (for example Apify) can be
added later without changing ingestion, permission, or moderation code.
"""

from __future__ import annotations

import hashlib
import os


class DiscoveryProviderError(RuntimeError):
    pass


def configured_provider_name() -> str:
    """Return the live provider selected for unattended scheduled runs.

    An empty value intentionally means "not connected". We never fall back to
    mock data for unattended production schedules.
    """
    return os.getenv("UGC_DISCOVERY_PROVIDER", "").strip().lower()


def live_provider_ready() -> bool:
    return bool(configured_provider_name())


def _mock_results(saved_search: dict) -> list[dict]:
    """Deterministic test results used only by explicit background test runs."""
    seed = f"{saved_search.get('id')}|{saved_search.get('platform')}|{saved_search.get('query')}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12].upper()
    platform = saved_search.get("platform") or "instagram"
    query = saved_search.get("query") or "discovery search"
    name = saved_search.get("name") or query
    if platform == "instagram":
        urls = [
            f"https://www.instagram.com/p/BG{digest}A/",
            f"https://www.instagram.com/p/BG{digest}B/",
        ]
    else:
        urls = [
            f"https://example.com/{platform}/BG{digest}A",
            f"https://example.com/{platform}/BG{digest}B",
        ]
    return [
        {
            "platform": platform,
            "creator_handle": "tn_worker_test_one",
            "source_url": urls[0],
            "external_id": f"BG{digest}A",
            "title": name,
            "caption": f"Background-worker test result discovered from {query}.",
            "discovery_query": query,
            "like_count": 184,
            "comment_count": 11,
            "view_count": 1250,
        },
        {
            "platform": platform,
            "creator_handle": "tn_worker_test_two",
            "source_url": urls[1],
            "external_id": f"BG{digest}B",
            "title": name,
            "caption": f"Second background-worker test result discovered from {query}.",
            "discovery_query": query,
            "like_count": 96,
            "comment_count": 6,
            "view_count": 780,
        },
    ][: max(1, min(2, int(saved_search.get("result_limit") or 25)))]


def fetch_discovery_results(saved_search: dict, *, provider_name: str | None = None, allow_mock: bool = False) -> tuple[str, list[dict]]:
    """Execute one provider search and return ``(provider_name, normalized rows)``."""
    provider = (provider_name or configured_provider_name()).strip().lower()
    if provider == "mock":
        if not allow_mock:
            raise DiscoveryProviderError("Mock discovery is disabled for unattended scheduled runs.")
        return "mock", _mock_results(saved_search)
    if not provider:
        raise DiscoveryProviderError("No live UGC discovery provider is configured yet.")

    # Deliberately fail closed until a real adapter is connected. The worker and
    # scheduler can be exercised safely with the explicit mock test path.
    raise DiscoveryProviderError(f"Unsupported UGC discovery provider: {provider}")
