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


def configured_provider_name() -> str:
    """Return the live provider selected for unattended scheduled runs.

    An empty value intentionally means "not connected". We never fall back to
    mock data for unattended production schedules.
    """
    return os.getenv("UGC_DISCOVERY_PROVIDER", "").strip().lower()


def provider_health() -> dict:
    """Return a secret-safe provider status for UI/runtime gates."""
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
                "Instagram hashtag and keyword discovery can run live."
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


def _first(*values):
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _normalize_apify_instagram_row(row: dict, saved_search: dict) -> dict | None:
    if not isinstance(row, dict) or row.get("error"):
        return None

    shortcode = str(_first(row.get("shortCode"), row.get("shortcode")) or "").strip()
    source_url = str(_first(row.get("url"), row.get("postUrl")) or "").strip()
    if not source_url and shortcode:
        source_url = f"https://www.instagram.com/p/{shortcode}/"

    creator_handle = str(
        _first(row.get("ownerUsername"), row.get("username"), row.get("owner", {}).get("username") if isinstance(row.get("owner"), dict) else None)
        or ""
    ).strip().lstrip("@")
    if not creator_handle or not source_url:
        return None

    external_id = str(_first(row.get("id"), shortcode) or "").strip()
    title = saved_search.get("target_label") or saved_search.get("name") or saved_search.get("query") or "Discovered Instagram post"
    return {
        "platform": "instagram",
        "creator_handle": creator_handle,
        "creator_name": str(_first(row.get("ownerFullName"), row.get("fullName")) or "").strip(),
        "creator_external_id": str(_first(row.get("ownerId"), row.get("owner_id")) or "").strip(),
        "source_url": source_url,
        "external_id": external_id,
        "title": title,
        "caption": str(row.get("caption") or "").strip(),
        "discovery_query": saved_search.get("query") or "",
        "media_url": str(_first(row.get("displayUrl"), row.get("imageUrl"), row.get("videoUrl")) or "").strip(),
        "like_count": _first(row.get("likesCount"), row.get("likes")),
        "comment_count": _first(row.get("commentsCount"), row.get("comments")),
        "view_count": _first(row.get("videoPlayCount"), row.get("videoViewCount"), row.get("igPlayCount"), row.get("views")),
    }


def _fetch_apify(saved_search: dict) -> list[dict]:
    if (saved_search.get("platform") or "").lower() != "instagram":
        raise DiscoveryProviderError("The Apify adapter currently supports Instagram discovery only.")

    search_type = (saved_search.get("search_type") or "").lower()
    if search_type not in {"hashtag", "keyword"}:
        raise DiscoveryProviderError(
            "The current Apify adapter supports Instagram hashtag and keyword searches."
        )

    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        raise DiscoveryProviderError("APIFY_API_TOKEN is not configured on the worker.")

    actor_id = os.getenv("APIFY_INSTAGRAM_HASHTAG_ACTOR", DEFAULT_APIFY_INSTAGRAM_ACTOR).strip() or DEFAULT_APIFY_INSTAGRAM_ACTOR
    limit = max(1, min(100, int(saved_search.get("result_limit") or 25)))
    query = str(saved_search.get("query") or "").strip()
    if not query:
        raise DiscoveryProviderError("Discovery query is empty.")

    actor_input = {
        "hashtags": [query.lstrip("#") if search_type == "hashtag" else query],
        "keywordSearch": search_type == "keyword",
        "resultsType": "posts",
        "resultsLimit": limit,
    }
    params = urlencode({"clean": "1", "format": "json", "maxItems": limit})
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
        with urlopen(request, timeout=240) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            detail = str((parsed.get("error") or {}).get("message") or parsed.get("message") or "").strip()
        except Exception:
            detail = ""
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

    normalized = []
    for row in payload[:limit]:
        item = _normalize_apify_instagram_row(row, saved_search)
        if item:
            normalized.append(item)
    return normalized


def fetch_discovery_results(saved_search: dict, *, provider_name: str | None = None, allow_mock: bool = False) -> tuple[str, list[dict]]:
    """Execute one provider search and return ``(provider_name, normalized rows)``."""
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
