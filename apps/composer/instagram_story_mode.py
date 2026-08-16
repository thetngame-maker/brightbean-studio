"""TN Social Studio Instagram Story composer bridge.

This module keeps the Story UI/persistence patch small and isolated from the
large upstream composer template/view files.  It is installed from
ComposerConfig.ready().

Responsibilities:
- persist Instagram post_type / also_story fields from the composer;
- load the small Story UI asset on composer pages only.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings


_INSTALLED = False


def install_instagram_story_mode() -> None:
    """Install the Story composer hooks once per Django process."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from apps.social_accounts.models import SocialAccount

    from . import views
    from .models import PlatformPost

    original_sync = views._sync_platform_posts

    @wraps(original_sync)
    def sync_with_instagram_story_fields(request, post, workspace, initial_status=None):
        result = original_sync(request, post, workspace, initial_status=initial_status)

        selected_ids = views._parse_selected_account_ids(request.POST.get("selected_accounts", ""))
        if not selected_ids:
            return result

        accounts = SocialAccount.objects.filter(
            id__in=selected_ids,
            workspace=workspace,
            platform__in=("instagram", "instagram_login"),
        )
        allowed_types = {"image", "reel", "story", "carousel", "video"}

        for account in accounts:
            account_id = str(account.id)
            type_field = f"instagram_post_type_{account_id}"
            story_field = f"instagram_also_story_{account_id}"

            # Old clients/autosaves that do not render the new fields must not
            # erase an existing Story choice.
            if type_field not in request.POST and story_field not in request.POST:
                continue

            try:
                pp = PlatformPost.objects.get(post=post, social_account=account)
            except PlatformPost.DoesNotExist:
                continue

            extra = dict(pp.platform_extra or {})

            if type_field in request.POST:
                post_type = request.POST.get(type_field, "").strip().lower()
                if post_type in allowed_types:
                    extra["post_type"] = post_type
                else:
                    extra.pop("post_type", None)

            if story_field in request.POST:
                also_story = request.POST.get(story_field, "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                # Story-only already is the Story, so a duplicate Story copy is
                # never meaningful.
                if extra.get("post_type") == "story":
                    also_story = False
                extra["also_story"] = also_story

            pp.platform_extra = extra
            pp.save(update_fields=["platform_extra", "updated_at"])

        return result

    views._sync_platform_posts = sync_with_instagram_story_fields

    # ``composer.views`` imports django.shortcuts.render into a module global,
    # so wrapping that reference lets us add small static JS files without
    # copying or forking the 200KB composer template.
    original_render = views.render

    @wraps(original_render)
    def render_with_instagram_story_asset(request, template_name, context=None, *args, **kwargs):
        response = original_render(request, template_name, context, *args, **kwargs)
        if template_name != "composer/compose.html":
            return response
        if response.status_code != 200 or not response.get("Content-Type", "").startswith("text/html"):
            return response

        try:
            html = response.content.decode(response.charset or "utf-8")
        except (UnicodeDecodeError, AttributeError):
            return response

        marker = "instagram_story_mode.js"
        if marker in html:
            return response

        static_url = getattr(settings, "STATIC_URL", "/static/") or "/static/"
        if not static_url.endswith("/"):
            static_url += "/"
        tag = (
            f'<script defer src="{static_url}composer/instagram_story_mode.js?v=20260816-3"></script>'
            f'<script defer src="{static_url}composer/instagram_story_dom_fix.js?v=20260816-1"></script>'
            f'<script defer src="{static_url}composer/instagram_story_preview_layout.js?v=20260816-1"></script>'
        )
        if "</body>" in html:
            html = html.replace("</body>", f"{tag}</body>", 1)
        else:
            html += tag

        response.content = html.encode(response.charset or "utf-8")
        if response.has_header("Content-Length"):
            del response["Content-Length"]
        return response

    views.render = render_with_instagram_story_asset