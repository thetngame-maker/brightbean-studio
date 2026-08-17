"""Higher-yield Instagram keyword discovery for UGC searches.

Keyword discovery is intentionally separate from hashtag discovery. A no-cookie
keyword-post actor is used first because it can return a progressively larger
pool of actual public keyword-search posts. Apify's maintained Search Scraper
popular-reels feed and Hashtag Scraper keyword mode remain secondary sources for
engagement-heavy enrichment and resilience.
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

# This actor accepts public keyword searches without Instagram session cookies
# and exposes limitPerSource for progressively deeper collection.
DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR = "supreme_coder~instagram-post-scraper"
MAX_POPULAR_REELS = 64
MAX_KEYWORD_SCAN = 500


def _row_key(row: dict) -> str:
    return str(row.get("external_id") or row.get("source_url") or "").strip()


def _merge_unique(*groups: list[dict], limit: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for row in group:
            key = _row_key(row)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(row)
            if len(merged) >= limit:
                return merged
    return merged


def fetch_apify_keyword_results(saved_search: dict) -> list[dict]:
    """Return a deep, engagement-aware candidate pool for one keyword search.

    ``result_limit`` is provider scan depth, not Studio's final create count.
    Repeated runs can therefore request 100, then 200, 300, 400, and 500 rows;
    Studio dedupes locally and keeps only unseen posts up to the run target.
    """
    query = str(saved_search.get("query") or "").strip()
    if not query:
        raise DiscoveryProviderError("Discovery query is empty.")

    scan_limit = max(1, min(MAX_KEYWORD_SCAN, int(saved_search.get("result_limit") or 100)))

    # Primary source: public Instagram keyword-search posts. Unlike the previous
    # keyword actor, this source does not require user/session cookies. Its clean
    # output matches the standard Instagram normalizer used elsewhere in Studio.
    keyword_rows: list[dict] = []
    keyword_actor = (
        os.getenv("APIFY_INSTAGRAM_KEYWORD_POST_ACTOR", DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR).strip()
        or DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR
    )
    try:
        keyword_payload = _apify_sync(
            keyword_actor,
            {
                "search": [query],
                "limitPerSource": scan_limit,
                "rawData": False,
            },
            max_items=scan_limit,
            timeout=360,
        )
        keyword_rows = _normalize_rows(keyword_payload, saved_search, scan_limit)
        for row in keyword_rows:
            row["discovery_provider_path"] = "keyword_posts"
    except DiscoveryProviderError:
        keyword_rows = []

    # Secondary source: Apify-maintained popular reels. Keep this because it is
    # especially good at surfacing high-engagement video candidates, but it is
    # not deep enough to be the primary progressive source by itself.
    popular_rows: list[dict] = []
    popular_limit = min(MAX_POPULAR_REELS, scan_limit)
    search_actor = (
        os.getenv("APIFY_INSTAGRAM_SEARCH_ACTOR", DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR).strip()
        or DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR
    )
    try:
        popular_payload = _apify_sync(
            search_actor,
            {"search": query, "searchType": "popular", "searchLimit": popular_limit},
            max_items=popular_limit,
            timeout=240,
        )
        popular_rows = _normalize_rows(popular_payload, saved_search, popular_limit)
        for row in popular_rows:
            row["discovery_provider_path"] = "popular_reels"
    except DiscoveryProviderError:
        popular_rows = []

    merged = _merge_unique(keyword_rows, popular_rows, limit=scan_limit)
    if len(merged) >= scan_limit:
        return merged

    # Final resilience source: the maintained hashtag scraper's keyword mode.
    hashtag_rows: list[dict] = []
    hashtag_actor = (
        os.getenv("APIFY_INSTAGRAM_HASHTAG_ACTOR", DEFAULT_APIFY_INSTAGRAM_ACTOR).strip()
        or DEFAULT_APIFY_INSTAGRAM_ACTOR
    )
    try:
        fallback_payload = _apify_sync(
            hashtag_actor,
            {
                "hashtags": [query],
                "keywordSearch": True,
                "resultsType": "posts",
                "resultsLimit": scan_limit,
            },
            max_items=scan_limit,
            timeout=300,
        )
        hashtag_rows = _normalize_rows(fallback_payload, saved_search, scan_limit)
        for row in hashtag_rows:
            row["discovery_provider_path"] = "keyword_fallback"
    except DiscoveryProviderError:
        hashtag_rows = []

    return _merge_unique(keyword_rows, popular_rows, hashtag_rows, limit=scan_limit)
