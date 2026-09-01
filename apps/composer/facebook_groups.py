"""TN Game Studio Facebook Groups assisted publishing bridge.

Meta no longer exposes an official API for third-party publishing to Facebook
Groups. This module deliberately keeps Facebook Group destinations outside the
automated PlatformPost publisher and adds a manual, multi-group handoff flow to
the composer instead.

The integration is isolated from the large upstream composer template/view files:
ComposerConfig.ready() installs a small render wrapper that injects the CSS/JS
assets only on composer pages.
"""

from __future__ import annotations

import json
from functools import wraps
from urllib.parse import urlparse

from django.conf import settings


_INSTALLED = False


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


def _inject_group_assistant(response, *, post_id: str = ""):
    """Inject the group destination UI assets into a rendered composer response."""
    if getattr(response, "status_code", 500) != 200 or not hasattr(response, "content"):
        return response
    content_type = response.get("Content-Type", "")
    if not content_type.startswith("text/html"):
        return response

    try:
        html = response.content.decode(response.charset or "utf-8")
    except (UnicodeDecodeError, AttributeError):
        return response

    if "facebook_groups.js" in html:
        return response

    static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
    if not static_url.endswith("/"):
        static_url += "/"

    post_key = post_id or "new"
    fragment = (
        f'<link rel="stylesheet" href="{static_url}composer/facebook_groups.css?v=20260901-1">'
        f'<script>window.TN_FACEBOOK_GROUP_POST_KEY={json.dumps(post_key)};</script>'
        f'<script defer src="{static_url}composer/facebook_groups.js?v=20260901-1"></script>'
    )
    if "</body>" in html:
        html = html.replace("</body>", f"{fragment}</body>", 1)
    else:
        html += fragment

    response.content = html.encode(response.charset or "utf-8")
    if response.has_header("Content-Length"):
        del response["Content-Length"]
    return response


def install_facebook_groups() -> None:
    """Install the composer-only Facebook Groups UI bridge once per process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import views

    original_render = views.render

    @wraps(original_render)
    def render_with_facebook_groups(request, template_name, context=None, *args, **kwargs):
        response = original_render(request, template_name, context, *args, **kwargs)
        if template_name != "composer/compose.html":
            return response

        post_id = ""
        if context and context.get("post") is not None:
            post_id = str(context["post"].id)
        return _inject_group_assistant(response, post_id=post_id)

    views.render = render_with_facebook_groups
