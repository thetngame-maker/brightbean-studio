"""Higher-yield Instagram keyword discovery for UGC searches.

Keyword discovery is intentionally separate from hashtag discovery. Apify's
Instagram Search Scraper exposes a popular-reels search that can return a much
larger, engagement-heavy pool for a topic. We merge that with the existing
Hashtag Scraper keyword mode so Studio has more candidates to dedupe against
before trying to fill the saved search's target-new count.
"""

from __future__ import annotations

import os

from .ugc_discovery_providers import (
    DEFAULT_APIFY_INSTAGRAM_ACTOR,
    DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR,
    DiscoveryProviderError,
    _apify_sync,
    _normalize_rows,
)

MAX_POPULAR_REELS = 64
MAX_KEYWORD_SCAN = 100


def _row_key(row: dict) -> str:
    return str(row.get("external_id") or row.get("source_url") or "").strip()


def _merge_unique(primary: list[dict], secondary: list[dict], limit: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for row in [*primary, *secondary]:
        key = _row_key(row)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(row)
        if len(merged) >= limit:
            break
    return merged


def fetch_apify_keyword_results(saved_search: dict) -> list[dict]:
    """Return a deep, engagement-first candidate pool for one keyword search.

    Popular reels are attempted first because they better match Instagram's
    high-engagement keyword discovery experience. Not every keyword has a
    popular feed, so provider errors from that optional pass are swallowed and
    the Hashtag Scraper keyword mode remains the reliable fallback.
    """
    query = str(saved_search.get("query") or "").strip()
    if not query:
        raise DiscoveryProviderError("Discovery query is empty.")

    scan_limit = max(1, min(MAX_KEYWORD_SCAN, int(saved_search.get("result_limit") or 25)))
    popular_limit = min(MAX_POPULAR_REELS, scan_limit)
    popular_rows: list[dict] = []

    search_actor = (
        os.getenv("APIFY_INSTAGRAM_SEARCH_ACTOR", DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR).strip()
        or DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR
    )
    try:
        popular_payload = _apify_sync(
            search_actor,
            {
                "search": query,
                "searchType": "popular",
                "searchLimit": popular_limit,
            },
            max_items=popular_limit,
            timeout=240,
        )
        popular_rows = _normalize_rows(popular_payload, saved_search, popular_limit)
    except DiscoveryProviderError:
        # Instagram does not expose a popular-reels feed for every keyword.
        # The fallback below should still run instead of failing discovery.
        popular_rows = []

    remaining = max(0, scan_limit - len(popular_rows))
    fallback_rows: list[dict] = []
    if remaining:
        hashtag_actor = (
            os.getenv("APIFY_INSTAGRAM_HASHTAG_ACTOR", DEFAULT_APIFY_INSTAGRAM_ACTOR).strip()
            or DEFAULT_APIFY_INSTAGRAM_ACTOR
        )
        fallback_payload = _apify_sync(
            hashtag_actor,
            {
                "hashtags": [query],
                "keywordSearch": True,
                "resultsType": "posts",
                # Ask for the full scan depth because this actor may repeat
                # some of the same popular results returned above.
                "resultsLimit": scan_limit,
            },
            max_items=scan_limit,
            timeout=240,
        )
        fallback_rows = _normalize_rows(fallback_payload, saved_search, scan_limit)

    return _merge_unique(popular_rows, fallback_rows, scan_limit)
