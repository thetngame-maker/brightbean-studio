"""Facebook Groups assisted publishing helpers.

Meta no longer exposes an official API for third-party publishing to Facebook
Groups. These helpers deliberately keep group targets separate from automated
PlatformPost publishing and expose an assisted handoff workflow instead.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from django.http import JsonResponse
from django.views.decorators.http import require_POST


def normalize_group_url(value: str) -> str:
    """Return a canonical Facebook group URL or an empty string when invalid."""
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if parsed.scheme not in {"http", "https"} or host not in {
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
    }:
        return ""
    path = parsed.path.rstrip("/")
    if not path.startswith("/groups/") or len(path.split("/")) < 3:
        return ""
    return f"https://www.facebook.com{path}/"


def parse_group_targets(raw: str) -> list[dict[str, str]]:
    """Parse, validate and de-duplicate serialized manual group targets."""
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(items, list):
        return []

    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        url = normalize_group_url(str(item.get("url", "")))
        if not url or url in seen:
            continue
        seen.add(url)
        name = str(item.get("name", "")).strip()[:120] or "Facebook Group"
        targets.append({"name": name, "url": url})
    return targets


def inject_group_assistant(response, *, post_id: str = ""):
    """Inject the opt-in group destination UI into a rendered composer response."""
    if getattr(response, "status_code", 500) != 200 or not hasattr(response, "content"):
        return response
    content_type = response.get("Content-Type", "")
    if "text/html" not in content_type:
        return response

    html = response.content.decode(response.charset or "utf-8")
    marker = "</body>"
    if marker not in html:
        return response

    post_key = post_id or "new"
    fragment = f"""
<link rel=\"stylesheet\" href=\"/static/composer/facebook_groups.css\">
<script>window.TN_FACEBOOK_GROUP_POST_KEY={json.dumps(post_key)};</script>
<script defer src=\"/static/composer/facebook_groups.js\"></script>
"""
    response.content = html.replace(marker, fragment + marker, 1).encode(response.charset or "utf-8")
    if response.has_header("Content-Length"):
        response["Content-Length"] = str(len(response.content))
    return response


@require_POST
def validate_groups(request):
    """Small validation endpoint used by the browser helper."""
    return JsonResponse({"groups": parse_group_targets(request.POST.get("groups", "[]"))})
