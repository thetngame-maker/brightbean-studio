"""Higher-yield Instagram keyword discovery for UGC searches.

Keyword discovery is intentionally separate from hashtag discovery. A dedicated
keyword-post actor is used first because it can return a progressively larger
pool of actual keyword-search posts (up to 500). Apify's maintained Search
Scraper popular-reels feed and Hashtag Scraper keyword mode remain secondary
sources for engagement-heavy enrichment and resilience.
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

DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR = "crawlerbros~instagram-keyword-search-scraper"
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


def _keyword_media_url(row: dict) -> str:
    direct = str(row.get("video_url") or row.get("videoUrl") or "").strip()
    if direct:
        return direct
    media_urls = row.get("media_urls") if isinstance(row.get("media_urls"), list) else []
    urls = [str(value or "").strip() for value in media_urls if str(value or "").strip()]
    for value in urls:
        lowered = value.lower().split("?", 1)[0]
        if lowered.endswith((".mp4", ".mov", ".m4v", ".webm")):
            return value
    return urls[0] if urls else ""


def _normalize_keyword_post_row(row: dict, saved_search: dict) -> dict | None:
    if not isinstance(row, dict) or row.get("status") == "No posts found":
        return None

    source_url = str(row.get("post_url") or row.get("postUrl") or row.get("url") or "").strip()
    creator_handle = str(row.get("username") or row.get("ownerUsername") or "").strip().lstrip("@")
    if not source_url or not creator_handle:
        return None

    media_kind = str(row.get("media_type") or row.get("mediaType") or "").strip().lower()
    is_video = any(token in media_kind for token in ("video", "reel", "igtv", "clip"))
    media_url = _keyword_media_url(row)
    thumbnail_url = str(row.get("thumbnail_url") or row.get("thumbnailUrl") or "").strip()

    external_id = str(row.get("id") or row.get("shortcode") or row.get("shortCode") or "").strip()
    if not external_id:
        parts = [part for part in source_url.rstrip("/").split("/") if part]
        if parts:
            external_id = parts[-1]

    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    return {
        "platform": "instagram",
        "creator_handle": creator_handle,
        "creator_name": str(row.get("full_name") or row.get("fullName") or "").strip(),
        "source_url": source_url,
        "external_id": external_id,
        "title": saved_search.get("target_label") or saved_search.get("name") or saved_search.get("query") or "Discovered Instagram post",
        "caption": str(row.get("caption") or "").strip(),
        "discovery_query": str(saved_search.get("query") or "").strip(),
        "media_type": "video" if is_video and media_url else "image",
        "media_url": media_url or thumbnail_url,
        "thumbnail_url": thumbnail_url,
        "instagram_product_type": media_kind,
        "like_count": row.get("like_count") if row.get("like_count") is not None else row.get("likesCount"),
        "comment_count": row.get("comment_count") if row.get("comment_count") is not None else row.get("commentsCount"),
        "view_count": row.get("view_count") if row.get("view_count") is not None else row.get("videoViewCount"),
        "location_id": str(location.get("id") or ""),
        "location_name": str(location.get("name") or ""),
        "location_url": "",
    }


def _normalize_keyword_post_rows(payload: list[dict], saved_search: dict, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for row in payload:
        item = _normalize_keyword_post_row(row, saved_search)
        if item:
            normalized.append(item)
        if len(normalized) >= limit:
            break
    return normalized


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

    # Primary source: actual Instagram keyword-search posts, scalable to 500.
    keyword_rows: list[dict] = []
    keyword_actor = (
        os.getenv("APIFY_INSTAGRAM_KEYWORD_POST_ACTOR", DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR).strip()
        or DEFAULT_APIFY_INSTAGRAM_KEYWORD_POST_ACTOR
    )
    try:
        keyword_payload = _apify_sync(
            keyword_actor,
            {"keywords": [query], "maxPosts": scan_limit},
            max_items=scan_limit,
            timeout=360,
        )
        keyword_rows = _normalize_keyword_post_rows(keyword_payload, saved_search, scan_limit)
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
    except DiscoveryProviderError:
        hashtag_rows = []

    return _merge_unique(keyword_rows, popular_rows, hashtag_rows, limit=scan_limit)
