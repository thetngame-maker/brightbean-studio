"""Refresh Instagram post context for inbox comments on demand."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.publisher.engine import _resolve_publish_credentials
from providers import get_provider
from providers.instagram_login import API_BASE

from .models import InboxMessage
from .views import _detail_context, _get_workspace


@login_required
@require_permission("use_inbox")
@require_POST
def refresh_instagram_post(request, workspace_id, message_id):
    """Re-fetch the current Instagram media metadata for one inbox comment."""
    workspace = _get_workspace(request, workspace_id)
    message = get_object_or_404(
        InboxMessage.objects.select_related("social_account", "assigned_to"),
        id=message_id,
        workspace=workspace,
    )

    account = message.social_account
    if account.platform != "instagram_login" or message.message_type != InboxMessage.MessageType.COMMENT:
        return HttpResponse("Instagram comment required.", status=400)

    media_id = str((message.extra or {}).get("post_id") or (message.extra or {}).get("stored_post_id") or "")
    if not media_id:
        return HttpResponse("Instagram post ID unavailable.", status=400)

    try:
        provider = get_provider(account.platform, _resolve_publish_credentials(account))
        response = provider._request(
            "GET",
            f"{API_BASE}/{media_id}",
            access_token=account.oauth_access_token,
            params={
                "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp",
            },
        )
        media = response.json()
    except Exception:
        # Keep the existing preview intact if Instagram cannot be reached.
        media = {}

    if media:
        extra = dict(message.extra or {})
        extra["post_id"] = str(media.get("id") or media_id)
        extra["stored_post_id"] = str(media.get("id") or media_id)
        extra["post_permalink_url"] = str(media.get("permalink") or extra.get("post_permalink_url") or "")
        extra["post_caption"] = str(media.get("caption") or "")
        extra["post_media_type"] = str(media.get("media_type") or extra.get("post_media_type") or "")
        extra["post_media_url"] = str(media.get("media_url") or extra.get("post_media_url") or "")
        extra["post_thumbnail_url"] = str(media.get("thumbnail_url") or extra.get("post_thumbnail_url") or "")
        message.extra = extra
        message.save(update_fields=["extra"])

    return render(request, "inbox/partials/_message_panel.html", _detail_context(workspace, message))
