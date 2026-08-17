"""Provider adapters for scheduled UGC discovery.

The worker talks only to this module. External services are normalized here so
scheduler, ingestion, permission, and moderation code stay provider-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class DiscoveryProviderError(RuntimeError):
    pass


APIFY_API_BASE = "https://api.apify.com/v2"
DEFAULT_APIFY_INSTAGRAM_ACTOR = "apify~instagram-hashtag-scraper"
DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR = "apify~instagram-scraper"
DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR = "apify~instagram-search-scraper"


def configured_provider_name() -> str:
    return os.getenv("UGC_DISCOVERY_PROVIDER", "").strip().lower()


def provider_health() -> dict:
    provider = configured_provider_name()
    if not provider:
        return {
            "provider": "",
            "ready": False,
            "label": "Live provider not connected",
            "detail": "Set UGC_DISCOVERY_PROVIDER=apify and APIFY_API_TOKEN on Railway.",
        }
    if provider == "apify":
        token_present = bool(os.getenv("APIFY_API_TOKEN", "").strip())
        return {
            "provider": "apify",
            "ready": token_present,
            "label": "Apify connected" if token_present else "Apify needs API token",
            "detail": (
                "Instagram hashtag, keyword, and resolved location discovery can run live."
                if token_present
                else "UGC_DISCOVERY_PROVIDER is set, but APIFY_API_TOKEN is missing."
            ),
        }
    return {
        "provider": provider,
        "ready": False,
        "label": f"Unsupported provider: {provider}",
        "detail": "Choose a supported UGC discovery provider.",
    }


def live_provider_ready() -> bool:
    return bool(provider_health()["ready"])


def _mock_results(saved_search: dict) -> list[dict]:
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


def _first(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _apify_sync(actor_id: str, actor_input: dict, *, max_items: int, timeout: int = 240) -> list[dict]:
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        raise DiscoveryProviderError("APIFY_API_TOKEN is not configured on the worker.")

    params = urlencode({"clean": "1", "format": "json", "maxItems": max_items})
    url = f"{APIFY_API_BASE}/acts/{quote(actor_id, safe='~')}/run-sync-get-dataset-items?{params}"
    request = Request(
        url,
        data=json.dumps(actor_input).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "TN-Social-Studio/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            detail = str((parsed.get("error") or {}).get("message") or parsed.get("message") or "").strip()
        except Exception:
            pass
        suffix = f": {detail}" if detail else ""
        raise DiscoveryProviderError(f"Apify returned HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise DiscoveryProviderError(f"Could not reach Apify: {exc.reason}") from exc
    except TimeoutError as exc:
        raise DiscoveryProviderError("Apify discovery timed out before results were returned.") from exc
    except json.JSONDecodeError as exc:
        raise DiscoveryProviderError("Apify returned an invalid JSON response.") from exc

    if not isinstance(payload, list):
        raise DiscoveryProviderError("Apify returned an unexpected dataset response.")
    return payload


def _location_candidate_from_row(row: dict) -> dict | None:
    if not isinstance(row, dict) or row.get("error"):
        return None
    location_id = str(_first(row.get("location_id"), row.get("locationId"), row.get("id")) or "").strip()
    url = str(_first(row.get("inputUrl"), row.get("url")) or "").strip()
    slug = str(row.get("slug") or "").strip()
    if not url and location_id:
        url = (
            f"https://www.instagram.com/explore/locations/{location_id}/{slug}/"
            if slug
            else f"https://www.instagram.com/explore/locations/{location_id}/"
        )
    name = str(_first(row.get("name"), row.get("shortName")) or "").strip()
    if not name or not url:
        return None
    return {
        "id": location_id,
        "name": name,
        "url": url,
        "slug": slug,
        "category": str(row.get("category") or "").strip(),
        "address": str(_first(row.get("location_address"), row.get("address")) or "").strip(),
        "city": str(_first(row.get("location_city"), row.get("city")) or "").strip(),
        "lat": _first(row.get("lat"), row.get("latitude")),
        "lng": _first(row.get("lng"), row.get("longitude")),
        "media_count": _first(row.get("media_count"), row.get("mediaCount"), row.get("postsCount")),
        "posts": row.get("posts") if isinstance(row.get("posts"), list) else [],
    }


def _search_instagram_places(query: str, limit: int = 8, *, live_search: bool = True) -> list[dict]:
    """Search Instagram places.

    Live search is best for interactive candidate matching. The standard search
    response is richer and can include the location's recent ``posts`` array,
    so background fallback enrichment deliberately uses ``live_search=False``.
    """
    actor_id = os.getenv("APIFY_INSTAGRAM_SEARCH_ACTOR", DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_SEARCH_ACTOR
    rows = _apify_sync(
        actor_id,
        {
            "search": query,
            "searchType": "place",
            "searchLimit": max(1, min(250, int(limit or 8))),
            "liveSearch": bool(live_search),
        },
        max_items=max(1, min(250, int(limit or 8))),
        timeout=180,
    )
    candidates = []
    for row in rows:
        candidate = _location_candidate_from_row(row)
        if candidate:
            candidates.append(candidate)
    return candidates


def resolve_instagram_location_candidates(query: str, limit: int = 8) -> list[dict]:
    """Resolve a human place name into Instagram location candidates."""
    query = str(query or "").strip()
    if not query:
        raise DiscoveryProviderError("Location query is empty.")
    return _search_instagram_places(query, max(1, min(20, int(limit or 8))), live_search=True)


def _normalize_apify_instagram_row(row: dict, saved_search: dict) -> dict | None:
    if not isinstance(row, dict) or row.get("error"):
        return None
    shortcode = str(_first(row.get("shortCode"), row.get("shortcode"), row.get("code")) or "").strip()
    source_url = str(_first(row.get("url"), row.get("postUrl"), row.get("inputUrl")) or "").strip()
    if source_url and "/explore/locations/" in source_url and shortcode:
        # Nested place posts sometimes inherit the place input URL. Prefer the
        # canonical post permalink whenever a shortcode is available.
        source_url = f"https://www.instagram.com/p/{shortcode}/"
    if not source_url and shortcode:
        source_url = f"https://www.instagram.com/p/{shortcode}/"
    creator_handle = str(
        _first(
            row.get("ownerUsername"),
            row.get("username"),
            row.get("owner", {}).get("username") if isinstance(row.get("owner"), dict) else None,
        )
        or ""
    ).strip().lstrip("@")
    if not creator_handle or not source_url:
        return None
    external_id = str(_first(row.get("id"), shortcode) or "").strip()
    title = saved_search.get("target_label") or saved_search.get("name") or saved_search.get("query") or "Discovered Instagram post"
    resolved_location_name = saved_search.get("resolved_location_name") or ""
    row_location = row.get("location") if isinstance(row.get("location"), dict) else {}
    return {
        "platform": "instagram",
        "creator_handle": creator_handle,
        "creator_name": str(_first(row.get("ownerFullName"), row.get("fullName")) or "").strip(),
        "creator_external_id": str(_first(row.get("ownerId"), row.get("owner_id")) or "").strip(),
        "source_url": source_url,
        "external_id": external_id,
        "title": title,
        "caption": str(row.get("caption") or row.get("text") or "").strip(),
        "discovery_query": resolved_location_name or saved_search.get("query") or "",
        "media_url": str(_first(row.get("displayUrl"), row.get("display_url"), row.get("imageUrl"), row.get("image_url"), row.get("videoUrl")) or "").strip(),
        "like_count": _first(row.get("likesCount"), row.get("likeCount"), row.get("likes")),
        "comment_count": _first(row.get("commentsCount"), row.get("commentCount"), row.get("comments")),
        "view_count": _first(row.get("videoPlayCount"), row.get("videoViewCount"), row.get("viewCount"), row.get("igPlayCount"), row.get("views")),
        "location_id": str(_first(row_location.get("id"), saved_search.get("resolved_location_id")) or ""),
        "location_name": str(_first(row_location.get("name"), resolved_location_name) or ""),
        "location_url": str(saved_search.get("resolved_location_url") or ""),
    }


def _normalize_rows(payload: list[dict], saved_search: dict, limit: int) -> list[dict]:
    normalized = []
    for row in payload:
        item = _normalize_apify_instagram_row(row, saved_search)
        if item:
            normalized.append(item)
        if len(normalized) >= limit:
            break
    return normalized


def _match_location_candidate(candidates: list[dict], saved_search: dict) -> dict | None:
    wanted_id = str(saved_search.get("resolved_location_id") or "").strip()
    wanted_url = str(saved_search.get("resolved_location_url") or "").rstrip("/")
    wanted_name = str(saved_search.get("resolved_location_name") or saved_search.get("query") or "").strip().lower()
    for candidate in candidates:
        candidate_url = str(candidate.get("url") or "").rstrip("/")
        if wanted_id and str(candidate.get("id") or "") == wanted_id:
            return candidate
        if wanted_url and candidate_url == wanted_url:
            return candidate
    return next(
        (candidate for candidate in candidates if str(candidate.get("name") or "").strip().lower() == wanted_name),
        None,
    )


def _location_fallback_posts(saved_search: dict, limit: int) -> list[dict]:
    """Use recent posts embedded in the matched standard Instagram place result."""
    query = str(saved_search.get("resolved_location_name") or saved_search.get("query") or "").strip()
    if not query:
        return []

    # Standard (non-live) place search is intentionally used here because Apify
    # documents its richer place dataset with recent ``posts``. Candidate
    # resolution remains live-search based for interactive matching.
    candidates = _search_instagram_places(query, 25, live_search=False)
    matched = _match_location_candidate(candidates, saved_search)
    if matched is None:
        # Some place names are indexed better by the original search phrase.
        original_query = str(saved_search.get("query") or "").strip()
        if original_query and original_query.lower() != query.lower():
            candidates = _search_instagram_places(original_query, 25, live_search=False)
            matched = _match_location_candidate(candidates, saved_search)
    if matched is None:
        return []
    return _normalize_rows(matched.get("posts") or [], saved_search, limit)


def _fetch_apify(saved_search: dict) -> list[dict]:
    if (saved_search.get("platform") or "").lower() != "instagram":
        raise DiscoveryProviderError("The Apify adapter currently supports Instagram discovery only.")

    search_type = (saved_search.get("search_type") or "").lower()
    limit = max(1, min(100, int(saved_search.get("result_limit") or 25)))
    query = str(saved_search.get("query") or "").strip()
    if not query:
        raise DiscoveryProviderError("Discovery query is empty.")

    if search_type == "location":
        location_url = str(saved_search.get("resolved_location_url") or "").strip()
        if not location_url:
            raise DiscoveryProviderError("This location search needs an Instagram location match before it can run.")
        actor_id = os.getenv("APIFY_INSTAGRAM_ACTOR", DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_GENERAL_ACTOR
        payload = _apify_sync(
            actor_id,
            {
                "directUrls": [location_url],
                "resultsType": "posts",
                "resultsLimit": limit,
                "addParentData": True,
            },
            max_items=limit,
        )
        normalized = _normalize_rows(payload, saved_search, limit)
        if not normalized:
            normalized = _location_fallback_posts(saved_search, limit)
        return normalized

    if search_type in {"hashtag", "keyword"}:
        actor_id = os.getenv("APIFY_INSTAGRAM_HASHTAG_ACTOR", DEFAULT_APIFY_INSTAGRAM_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_ACTOR
        actor_input = {
            "hashtags": [query.lstrip("#") if search_type == "hashtag" else query],
            "keywordSearch": search_type == "keyword",
            "resultsType": "posts",
            "resultsLimit": limit,
        }
        payload = _apify_sync(actor_id, actor_input, max_items=limit)
        return _normalize_rows(payload, saved_search, limit)

    raise DiscoveryProviderError(
        "The current Apify adapter supports Instagram hashtag, keyword, and resolved location searches."
    )


def fetch_discovery_results(saved_search: dict, *, provider_name: str | None = None, allow_mock: bool = False) -> tuple[str, list[dict]]:
    provider = (provider_name or configured_provider_name()).strip().lower()
    if provider == "mock":
        if not allow_mock:
            raise DiscoveryProviderError("Mock discovery is disabled for unattended scheduled runs.")
        return "mock", _mock_results(saved_search)
    if provider == "apify":
        return "apify", _fetch_apify(saved_search)
    if not provider:
        raise DiscoveryProviderError("No live UGC discovery provider is configured yet.")
    raise DiscoveryProviderError(f"Unsupported UGC discovery provider: {provider}")
