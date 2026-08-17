"""Deep fallback extraction for Instagram location discovery.

This module is intentionally separate from the main provider adapter. It only
runs after a resolved Instagram location returned zero normalized posts through
the normal Apify paths, giving us a safe place to handle alternate provider
shapes without destabilizing hashtag/keyword discovery.
"""

from __future__ import annotations

import os

from .ugc_discovery_providers import (
    DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR,
    DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR,
    _apify_sync,
    _first,
    _normalize_rows,
)

POST_CONTAINER_KEYS = {
    "posts",
    "latestPosts",
    "recentPosts",
    "topPosts",
    "latest_posts",
    "recent_posts",
    "top_posts",
}
POST_WRAPPER_KEYS = ("node", "post", "media", "item")
LIST_WRAPPER_KEYS = ("edges", "items", "nodes", "data", "results")


def _looks_like_post(value: dict) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(
        value.get("shortCode")
        or value.get("shortcode")
        or value.get("code")
        or value.get("postUrl")
        or (value.get("url") and "/p/" in str(value.get("url")))
        or value.get("ownerUsername")
        or value.get("ownerId")
        or isinstance(value.get("owner"), dict)
    )


def _unwrap_post_values(value, *, depth=0):
    """Yield actual post dictionaries from list/GraphQL wrapper shapes."""
    if depth > 5:
        return
    if isinstance(value, list):
        for item in value:
            yield from _unwrap_post_values(item, depth=depth + 1)
        return
    if not isinstance(value, dict):
        return

    # Common GraphQL/provider wrapper: {"node": {actual post...}}.
    for key in POST_WRAPPER_KEYS:
        child = value.get(key)
        if isinstance(child, dict):
            yield from _unwrap_post_values(child, depth=depth + 1)
            return

    # Containers frequently wrap a list as {"edges": [...]}, {"items": [...]},
    # etc. Recurse into those before treating the wrapper itself as a post.
    for key in LIST_WRAPPER_KEYS:
        child = value.get(key)
        if isinstance(child, (list, dict)):
            yield from _unwrap_post_values(child, depth=depth + 1)
            return

    if _looks_like_post(value):
        yield value


def _walk_post_containers(value, *, depth=0):
    """Yield actual post dictionaries from known nested post collections."""
    if depth > 7:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if key in POST_CONTAINER_KEYS and isinstance(child, (list, dict)):
                yield from _unwrap_post_values(child)
            elif isinstance(child, (dict, list)):
                yield from _walk_post_containers(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from _walk_post_containers(child, depth=depth + 1)


def _matches_location_row(row: dict, saved_search: dict) -> bool:
    wanted_id = str(saved_search.get("resolved_location_id") or "").strip()
    wanted_url = str(saved_search.get("resolved_location_url") or "").rstrip("/")
    wanted_name = str(saved_search.get("resolved_location_name") or saved_search.get("query") or "").strip().lower()

    row_id = str(_first(row.get("location_id"), row.get("locationId"), row.get("id")) or "").strip()
    row_url = str(_first(row.get("inputUrl"), row.get("url")) or "").rstrip("/")
    row_name = str(_first(row.get("name"), row.get("shortName")) or "").strip().lower()

    if wanted_id and row_id == wanted_id:
        return True
    if wanted_url and row_url == wanted_url:
        return True
    return bool(wanted_name and row_name == wanted_name)


def deep_location_fallback(saved_search: dict, limit: int) -> tuple[list[dict], dict]:
    """Try alternate Apify location response shapes and return diagnostics."""
    limit = max(1, min(100, int(limit or 25)))
    location_url = str(saved_search.get("resolved_location_url") or "").strip()
    query = str(saved_search.get("resolved_location_name") or saved_search.get("query") or "").strip()
    diagnostics = {
        "details_rows": 0,
        "details_nested_posts": 0,
        "details_normalized_posts": 0,
        "search_rows": 0,
        "search_nested_posts": 0,
        "search_normalized_posts": 0,
        "normalized_posts": 0,
        "path": "none",
    }

    # 1) Location details can use a different output schema from posts mode.
    if location_url:
        actor_id = os.getenv("APIFY_INSTAGRAM_ACTOR", DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR
        details = _apify_sync(
            actor_id,
            {"directUrls": [location_url], "resultsType": "details", "resultsLimit": limit, "addParentData": True},
            max_items=max(5, min(limit, 25)),
        )
        diagnostics["details_rows"] = len(details)
        nested = list(_walk_post_containers(details))
        diagnostics["details_nested_posts"] = len(nested)
        normalized = _normalize_rows(nested, saved_search, limit)
        diagnostics["details_normalized_posts"] = len(normalized)
        if normalized:
            diagnostics["normalized_posts"] = len(normalized)
            diagnostics["path"] = "location_details"
            return normalized, diagnostics

    # 2) Inspect the raw standard place-search result instead of only its
    # normalized top-level `posts` field. Apify occasionally nests richer media
    # data differently as its actor output evolves.
    if query:
        actor_id = os.getenv("APIFY_INSTAGRAM_SEARCH_ACTOR", DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR
        search_rows = _apify_sync(
            actor_id,
            {"search": query, "searchType": "place", "searchLimit": 50, "liveSearch": False},
            max_items=50,
            timeout=180,
        )
        diagnostics["search_rows"] = len(search_rows)
        matched = next((row for row in search_rows if isinstance(row, dict) and _matches_location_row(row, saved_search)), None)
        nested = list(_walk_post_containers(matched or {}))
        diagnostics["search_nested_posts"] = len(nested)
        normalized = _normalize_rows(nested, saved_search, limit)
        diagnostics["search_normalized_posts"] = len(normalized)
        if normalized:
            diagnostics["normalized_posts"] = len(normalized)
            diagnostics["path"] = "place_search_nested"
            return normalized, diagnostics

    return [], diagnostics
